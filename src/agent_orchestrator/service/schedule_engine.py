"""The scheduler decision engine (T-Ev3Qm5; HLD scheduler-triggers-hld.md §6.1-§6.5, §6.9, §7.2).

A signal-free ``ScheduleEngine`` that, given a clock, decides which schedules should fire and
why. It owns *policy* -- overlap, catch-up, jitter, concurrency, quarantine -- and none of the
*mechanism*: it never spawns a process (``ScheduledLauncher``, ``T-Lp4Wt6``), never registers a
signal handler (``T-Sv5Hb3``), and touches workspace files only through the schedule store.

Structural rule: :meth:`ScheduleEngine.evaluate` is **pure with respect to wall-clock time**.
All time enters through ``now`` (the injected ``clock`` is read only in :meth:`tick` and the
launch path), so a fixed clock makes every scenario replayable. It is *not* pure in the I/O
sense: deriving "active" reads run state and process liveness (HLD §6.4).

Sharing state stores (IMPORTANT for the launcher / service wiring): :class:`ScheduleStateStore`
caches ``state.json`` in memory and is single-writer. The launcher must therefore be built with
the engine's own :attr:`ScheduleEngine.state_store`, :attr:`fire_store` and :attr:`event_log`
(and its per-workspace :meth:`ScheduleEngine.process_supervisor`), never private instances, or
the two caches clobber each other.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import IO, Any, Final, Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from ..artifacts import ArtifactPathError, LocalFsArtifactStore
from ..models import RunState, TrippedBreaker, compute_run_usage_totals
from ..scheduler import CronScheduler, IntervalScheduler, Scheduler
from ..schedules.errors import ScheduleFileError
from ..schedules.governance import LimitsSpec
from ..schedules.models import ResolvedBinding
from ..schedules.store import ScheduleStore
from ..schedules.windows import WeekStart
from ..ui.processes import ProcessSupervisor, _pid_alive
from ..ui.runs import RunNotFoundError, RunRepository
from .capped_resume import (
    REQUEST_KIND_RESUME_CAPPED,
    ResumeCappedRequest,
    ResumeOutcome,
    drain_resume_capped,
    is_stranded_resume,
    parse_request_age_ok,
    recover_stranded_resume,
    sweep_pause_expiry,
)
from .fire_store import (
    SCHEDULES_SUBDIR,
    CostConfidence,
    FireAlreadyRecordedError,
    FireIntentRecord,
    FireIntentStore,
    ScheduleEventLog,
    ScheduleState,
    ScheduleStateStore,
    fire_key,
)
from .governance_gate import (
    GOVERNANCE_CAPPED_STATES,
    GOVERNANCE_LOCK_NAME,
    REASON_COST_CAP_REACHED,
    REASON_GOVERNANCE_ERROR,
    REASON_QUOTA_EXHAUSTED,
    FireRecordLedger,
    GateResult,
    UsageLedger,
    gate,
    skeleton,
)
from .registry import ServiceRegistry
from .statefile import atomic_write_bytes
from .usage_ledger import run_confidence

logger = logging.getLogger(__name__)

# -- constants (HLD §7.2 / §6) -----------------------------------------------------------------
DEFAULT_EVAL_INTERVAL_SECONDS = 15.0
SCHEDULE_TICK_BUDGET_SECONDS = 0.5
MAX_CONSECUTIVE_EVAL_ERRORS = 5
STATUS_COMPLETED: Final = "completed"
STATUS_QUARANTINED: Final = "quarantined"
STATUS_DISABLED: Final = "disabled"
STATUS_ACTIVE: Final = "active"
MAX_CATCHUP_STEPS = 10_000
REQUEST_TTL_SECONDS = 300
DEFAULT_MAX_CONCURRENT_GLOBAL = 3
ENGINE_LOCK_FILENAME = "engine.lock"
REQUESTS_SUBDIR = "requests"
REQUEST_KIND_RUN_NOW = "run_now"
REQUEST_KIND_RESTART_COMPLETED: Final = "restart_completed"
COST_CAP_BREAKER_ID = "builtin.cost_cap"
# L-n1: a failing retire-hook finalisation is retried with capped exponential backoff, then given
# up on (one ERROR event); the start sweep picks the record up again on the next restart.
FINALIZE_RETRY_BASE_SECONDS = 2.0
FINALIZE_RETRY_MAX_BACKOFF_SECONDS = 300.0
FINALIZE_RETRY_MAX_ATTEMPTS = 6
EVENT_FINALIZE_FAILED: Final = "schedule.governance.finalize_failed"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_SUCCEEDED = "succeeded"
RUN_STATUS_RUNNING = "running"
FIRE_KEY_JITTER_HEX_DIGITS = 8  # leading hex digits of the fire key that seed the jitter
PURE_KINDS = ("cron", "interval")  # served by Scheduler.next_fire; event kinds use TriggerSource

# Collapsed backlogs are reported once, not as one burned record per instant (a 10 000-instant
# backlog would otherwise cost 10 000 fsyncs inside a 0.5 s budget).
REASON_BACKLOG_COLLAPSED = "backlog_collapsed"
REASON_QUEUE_FULL = "queue_full"
REASON_OVERLAP = "overlap"
REASON_MISSED = "missed"
REASON_QUEUE_LOST = "queue_lost_on_restart"


class FireReason(StrEnum):
    """Every ``FireDecision.reason`` (``ao schedule list`` renders these verbatim)."""

    DUE = "due"
    QUEUE_DRAIN = "queue_drain"
    NOT_DUE = "not_due"
    JITTER_PENDING = "jitter_pending"
    NEVER = "never"
    DISABLED = "disabled"
    COMPLETED = "completed"
    QUARANTINED = "quarantined"
    UNTIL_SATISFIED = "until_satisfied"
    ALREADY_FIRED = "already_fired"
    MISSED = "missed"
    OVERLAP_SKIP = "overlap_skip"
    OVERLAP_QUEUED = "overlap_queued"
    CONCURRENCY = "concurrency"
    FILE_INVALID = "file_invalid"
    # Governance (T-PpE5lf, HLD §8.1): burn the key like overlap, unlike concurrency.
    QUOTA_EXHAUSTED = REASON_QUOTA_EXHAUSTED
    COST_CAP_REACHED = REASON_COST_CAP_REACHED
    GOVERNANCE_ERROR = REASON_GOVERNANCE_ERROR


Clock = Callable[[], datetime]
Monotonic = Callable[[], float]


def _utcnow() -> datetime:
    return datetime.now(UTC)


# -- errors ------------------------------------------------------------------------------------


class EngineLockHeldError(RuntimeError):
    """Another engine already holds ``<state_dir>/schedules/engine.lock`` (F9)."""


# -- contracts ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class FireDecision:
    """The outcome of evaluating one binding at one instant (HLD §7.2)."""

    fire: bool
    reason: str
    workspace_root: str
    schedule_id: str
    fire_key: str | None = None
    scheduled_for: datetime | None = None
    effective_due: datetime | None = None
    run_id: str | None = None
    binding: ResolvedBinding | None = None
    # Governance (T-PpE5lf): the limits the gate evaluated and its verdict (None when ungoverned).
    effective_limits: LimitsSpec | None = None
    gate: GateResult | None = None


@dataclass(frozen=True)
class Candidate:
    """An event-kind trigger's proposed fire instant (``TriggerSource.observe``)."""

    scheduled_for: datetime
    payload: Any = None
    # Content-derived token mixed into the fire key so different states with the same
    # ``scheduled_for`` second do not collide (file_watch, T-Fw8Gp4 M2). Empty = legacy key.
    discriminator: str = ""
    # The source observes a *change now*, not a slot that was due earlier: ``scheduled_for`` is
    # data (an mtime), so lateness-based ``missed``/``catch_up`` handling must not apply (M1).
    skip_catch_up: bool = False


@dataclass(frozen=True)
class RunNowRequest:
    """A drained ``requests/*.json`` run-now request (HLD §6.9)."""

    workspace_root: str
    schedule_id: str
    requested_at: datetime
    force: bool = False
    requested_by: str = "cli"


class ScheduledLauncher(Protocol):
    """Mechanism half of a fire (implemented by ``T-Lp4Wt6``). Must not raise for a failed
    launch -- it records ``launch_failed`` and returns the record."""

    def launch(self, decision: FireDecision) -> FireIntentRecord: ...


@runtime_checkable
class TriggerSource(Protocol):
    """Event-kind (``file_watch`` / ``webhook``) trigger (HLD §7.1, F6).

    ``observe`` never consumes a fireable change (no queue pop); it may persist a baseline when
    nothing *wanted* changed (file_watch seeds its digest, T-Fw8Gp4 H1). ``commit`` is called by
    the engine **only once the fire key is burned or launched** -- never on a concurrency skip,
    which deliberately does not burn the key, so a concurrency skip cannot lose an event.
    """

    def observe(
        self, binding: ResolvedBinding, state: ScheduleState, now: datetime
    ) -> Candidate | None: ...

    def commit(self, binding: ResolvedBinding, candidate: Candidate) -> None: ...


TriggerSourceFactory = Callable[[str, ResolvedBinding], TriggerSource]


def _default_process_supervisor(root: str) -> ProcessSupervisor:
    # discovery timeout 0: a blocking run-id wait would stall the shared tick (T-Lp4Wt6 AC4).
    return ProcessSupervisor(root, run_id_discovery_timeout=0.0)


# -- engine lock (F9) --------------------------------------------------------------------------


def _lock_path(state_dir: Path) -> Path:
    return Path(state_dir) / SCHEDULES_SUBDIR / ENGINE_LOCK_FILENAME


def probe_engine_lock(state_dir: Path) -> bool:
    """True when a live engine holds ``engine.lock`` (non-blocking try-lock-and-release).

    The CLI uses this for local run-now / daemon detection (the hub is 401 under auth and live
    even for ``--no-schedules``). Never raises on a missing state dir -- that means "no engine".
    """
    path = _lock_path(state_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "a+")
    except OSError:
        return False
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return False
    finally:
        handle.close()


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


# -- engine ------------------------------------------------------------------------------------


class ScheduleEngine:
    """Decides which schedules fire (see module docstring). Single-threaded, signal-free."""

    def __init__(
        self,
        registry_path: Path,
        *,
        state_dir: Path | None = None,
        clock: Clock = _utcnow,
        monotonic: Monotonic = time.monotonic,
        launcher: ScheduledLauncher | None = None,
        schedule_store_factory: Callable[[str], ScheduleStore] = ScheduleStore,
        process_supervisor_factory: Callable[
            [str], ProcessSupervisor
        ] = _default_process_supervisor,
        run_repository_factory: Callable[[str], RunRepository] = RunRepository,
        registry_factory: Callable[[Path], ServiceRegistry] = ServiceRegistry,
        eval_interval_seconds: float = DEFAULT_EVAL_INTERVAL_SECONDS,
        max_concurrent_global: int = DEFAULT_MAX_CONCURRENT_GLOBAL,
        usage_ledger_factory: Callable[[FireIntentStore], UsageLedger] = FireRecordLedger,
        governance_lock_path: Path | None = None,
        fire_store: FireIntentStore | None = None,
        state_store: ScheduleStateStore | None = None,
        event_log: ScheduleEventLog | None = None,
    ) -> None:
        if state_dir is None:
            from .paths import default_state_dir

            state_dir = default_state_dir()
        self._registry_path = Path(registry_path)
        self._state_dir = Path(state_dir)
        self._clock = clock
        self._monotonic = monotonic
        self._launcher = launcher
        self._store_factory = schedule_store_factory
        self._supervisor_factory = process_supervisor_factory
        self._repo_factory = run_repository_factory
        self._registry_factory = registry_factory
        self._eval_interval = eval_interval_seconds
        self._max_global = max_concurrent_global

        # R-3: stores are injectable; the defaults keep the previous behaviour.
        self._fires = fire_store or FireIntentStore(self._state_dir, clock=clock)
        self._state = state_store or ScheduleStateStore(self._state_dir)
        self._events = event_log or ScheduleEventLog(self._state_dir, clock=clock)
        # W1: last ``until_unreadable`` error per (workspace, schedule), so a persistently bad gate
        # emits once per distinct error rather than every evaluation.
        self._until_errors: dict[tuple[str, str], str] = {}
        self._ledger: UsageLedger = usage_ledger_factory(self._fires)
        self._governance_lock = (
            Path(governance_lock_path)
            if governance_lock_path is not None
            else self._state_dir / SCHEDULES_SUBDIR / GOVERNANCE_LOCK_NAME
        )

        # F2: ONE long-lived ProcessSupervisor (and repo) per workspace, built lazily, never
        # per call -- only the instance that spawned a child can reap it.
        self._supervisors: dict[str, ProcessSupervisor] = {}
        self._repos: dict[str, RunRepository] = {}
        self._schedulers: dict[str, Scheduler] = {
            "cron": CronScheduler(clock),
            "interval": IntervalScheduler(),
        }
        self._source_factories: dict[str, TriggerSourceFactory] = {}
        # Production file_watch source (T-Fw8Gp4); lazy import: watch.py imports this module.
        # Callers may override with register_trigger_source("file_watch", ...).
        from .watch import file_watch_factory

        self._source_factories["file_watch"] = file_watch_factory(
            self._state_dir,
            lambda name, payload: self._events.emit(name, **payload),
            monotonic=self._monotonic,
        )
        self._sources: dict[tuple[str, str], tuple[ResolvedBinding, TriggerSource]] = {}

        self._lock_handle: IO[str] | None = None
        self._last_eval_mono: float | None = None
        self._resume_from: tuple[str, str] | None = None  # budget cursor: first unreached binding
        self._last_tick_at: datetime | None = None
        self._last_tick_duration_ms: int | None = None

        # (workspace, schedule) -> (queued key, scheduled_for, effective_due). The state store
        # persists only the key; the instant is needed to launch the drain. Lost on restart,
        # in which case the queued fire is dropped loudly (see _drain_queued).
        self._queued_meta: dict[tuple[str, str], tuple[str, datetime, datetime | None]] = {}
        # Per-tick memo of derived live runs, plus fires accepted earlier in the same tick (they
        # are not launched until evaluation ends but must count against the caps).
        self._in_tick = False
        self._live_memo: dict[str, FireIntentRecord] | None = None
        self._finalize_retry: set[str] = set()  # M2: keys whose retire hook failed
        self._finalize_attempts: dict[str, int] = {}  # L-n1: failures so far, per key
        self._finalize_next_at: dict[str, float] = {}  # L-n1: monotonic instant of the next retry
        self._pending_by_root: dict[str, int] = {}
        # (root, schedule_id) of fires accepted earlier in this tick: they count against overlap
        # (M1) as well as the caps, so run-now + the natural instant cannot both launch.
        self._pending_schedules: set[tuple[str, str]] = set()
        # Last backlog-collapse already reported per binding, so a pending instant is not
        # re-reported every tick (L1).
        self._collapse_reported: dict[tuple[str, str], tuple[datetime, int]] = {}
        self._pending_commits: dict[str, tuple[TriggerSource, ResolvedBinding, Candidate]] = {}

    # -- collaborators exposed for the launcher / wiring --------------------------------------

    @property
    def state_store(self) -> ScheduleStateStore:
        return self._state

    @property
    def clock(self) -> Clock:
        """The wall clock this engine (and its default stores) reads."""
        return self._clock

    @property
    def monotonic(self) -> Monotonic:
        """The monotonic clock behind the tick rate limit and retry back-off."""
        return self._monotonic

    @property
    def fire_store(self) -> FireIntentStore:
        return self._fires

    @property
    def event_log(self) -> ScheduleEventLog:
        return self._events

    @property
    def usage_ledger(self) -> UsageLedger:
        return self._ledger

    @property
    def governance_lock_path(self) -> Path:
        return self._governance_lock

    def process_supervisor(self, root: str) -> ProcessSupervisor:
        """The cached per-workspace supervisor (factory called once per workspace)."""
        key = str(Path(root).resolve())
        sup = self._supervisors.get(key)
        if sup is None:
            sup = self._supervisors[key] = self._supervisor_factory(key)
        return sup

    def _repo(self, root: str) -> RunRepository:
        key = str(Path(root).resolve())
        repo = self._repos.get(key)
        if repo is None:
            repo = self._repos[key] = self._repo_factory(key)
        return repo

    def register_trigger_source(self, kind: str, factory: TriggerSourceFactory) -> None:
        """Register the event-kind source factory (``T-Fw8Gp4`` / ``T-Wh9Kv1``). One instance is
        built per ``(workspace, schedule)`` and rebuilt when the binding changes."""
        if kind in PURE_KINDS:
            raise ValueError(f"{kind!r} is a pure Scheduler kind, not a TriggerSource kind")
        self._source_factories[kind] = factory

    # -- lifecycle -----------------------------------------------------------------------------

    def start(self) -> None:
        """Take the single-evaluator lock and reconcile orphaned fires. Launches nothing."""
        self._acquire_lock()
        in_flight = {r.fire_key for r in self._fires.list(status="intended")}
        orphans = self._fires.reconcile_orphans(
            _pid_alive, lambda root, run_id: self._repo(root).exists(run_id)
        )
        for rec in orphans:
            self._events.emit(
                "schedule.fire_orphaned",
                workspace=rec.workspace_root,
                schedule_id=rec.schedule_id,
                fire_key=rec.fire_key,
                scheduled_for=rec.scheduled_for,
                run_id=rec.run_id,
            )
        self._adopt_reconciled(in_flight)
        self._sweep_unfinalized()

    def shutdown(self) -> None:
        """Release the engine lock. Idempotent. Never touches a child process."""
        handle, self._lock_handle = self._lock_handle, None
        if handle is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    def _acquire_lock(self) -> None:
        if self._lock_handle is not None:
            return
        path = _lock_path(self._state_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.seek(0)
            holder = handle.read().strip() or "unknown"
            handle.close()
            raise EngineLockHeldError(
                f"another schedule engine is already running (pid {holder}); refusing to start "
                f"a second evaluator against {path}"
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._lock_handle = handle

    # -- the tick (HLD §6.2) -------------------------------------------------------------------

    def tick(self, now: datetime | None = None) -> list[FireDecision]:
        """One service-loop step. Self-rate-limited to ``eval_interval_seconds`` (monotonic);
        returns the decisions that fired. Never raises."""
        self._reconcile_supervisors()  # every call: prompt reaping, independent of the rate limit
        self._retry_finalizations()  # M2: re-drive failed retire hooks, also unthrottled
        mono = self._monotonic()
        if self._last_eval_mono is not None and mono - self._last_eval_mono < self._eval_interval:
            return []
        self._last_eval_mono = mono
        now = now if now is not None else self._clock()
        deadline = mono + SCHEDULE_TICK_BUDGET_SECONDS
        self._in_tick, self._live_memo, self._pending_by_root = True, None, {}
        self._pending_schedules = set()
        decisions: list[FireDecision] = []
        try:
            decisions = self._evaluate_all(now, deadline)
        except Exception as exc:  # noqa: BLE001 - nothing may escape into the service loop
            logger.exception("schedule tick failed")
            self._events.emit("schedule.eval_failed", reason="tick_failed", detail=str(exc))
        finally:
            self._in_tick, self._live_memo = False, None
        for d in decisions:
            self._launch(d)
        self._last_tick_at = now
        self._last_tick_duration_ms = int((self._monotonic() - mono) * 1000)
        return decisions

    def _reconcile_supervisors(self) -> None:
        for root, sup in list(self._supervisors.items()):
            try:
                sup.reconcile()
            except Exception as exc:  # noqa: BLE001
                logger.warning("reconcile of %s failed: %s", root, exc)

    def _evaluate_all(self, now: datetime, deadline: float) -> list[FireDecision]:
        decisions: list[FireDecision] = []
        try:
            entries = [e for e in self._registry_factory(self._registry_path).load().workspaces]
        except Exception as exc:  # noqa: BLE001 - corrupt registry: skip this tick, keep ticking
            self._events.emit("schedule.eval_failed", reason="registry_unreadable", detail=str(exc))
            return decisions
        enabled = {str(Path(e.root).resolve()): e for e in entries if _opted_in(e)}

        # T-nI4DBf: capped-run resume requests + pause-expiry sweep, ahead of the evaluation loop
        # so the sweep never waits behind (and is bounded by) the tick budget.
        self.drain_resume_requests(now)
        self.drain_restart_requests(now)
        self._sweep_pause_expiry(now)

        for req in self.drain_run_now_requests(now):
            entry = enabled.get(str(Path(req.workspace_root).resolve()))
            binding = self._find_binding(entry, req.schedule_id) if entry is not None else None
            if binding is None:
                self._events.emit(
                    "schedule.eval_failed",
                    workspace=req.workspace_root,
                    schedule_id=req.schedule_id,
                    reason="run_now_unknown_schedule",
                )
                continue
            self._collect(decisions, entry, binding, now, run_now=req)

        pairs: list[tuple[Any, ResolvedBinding]] = []
        for root, entry in enabled.items():
            try:
                bindings = self._store_factory(root).load().bindings
            except ScheduleFileError as exc:
                self._events.emit(
                    "schedule.file_invalid",
                    workspace=root,
                    reason=FireReason.FILE_INVALID.value,
                    detail=str(exc),
                )
                continue
            pairs.extend((entry, b) for b in bindings)
        pairs = self._rotate_from_cursor(pairs)

        self._resume_from = None
        for entry, binding in pairs:
            if self._monotonic() > deadline:
                self._resume_from = (str(Path(entry.root).resolve()), binding.id)
                self._events.emit(
                    "schedule.tick_budget_exceeded",
                    level=logging.WARNING,
                    workspace=entry.root,
                    schedule_id=binding.id,
                    detail=f"stopped at {binding.id}",
                )
                break
            st = self._state.get(entry.root, binding.id)
            if st is not None and st.status in (STATUS_COMPLETED, STATUS_QUARANTINED):
                continue  # terminal: evaluate() would only re-report it
            if not binding.enabled:
                continue
            self._collect(decisions, entry, binding, now)
        return decisions

    def _rotate_from_cursor(
        self, pairs: list[tuple[Any, ResolvedBinding]]
    ) -> list[tuple[Any, ResolvedBinding]]:
        """Start at the binding a budget-truncated tick stopped at, so a slow early schedule
        cannot starve the later ones forever."""
        if self._resume_from is None:
            return pairs
        for i, (entry, b) in enumerate(pairs):
            if (str(Path(entry.root).resolve()), b.id) == self._resume_from:
                return pairs[i:] + pairs[:i]
        return pairs

    def _collect(
        self,
        out: list[FireDecision],
        entry: Any,
        binding: ResolvedBinding,
        now: datetime,
        run_now: RunNowRequest | None = None,
    ) -> None:
        """Evaluate one binding with full NFR-2 isolation; append it to *out* if it fires."""
        root = entry.root
        try:
            d = self.evaluate(entry, binding, now, run_now=run_now)
        except Exception as exc:  # noqa: BLE001 - isolate: one bad binding never kills the loop
            self._quarantine_on_repeat(root, binding.id, exc)
            return
        try:  # L4: bookkeeping failure (e.g. disk full) must not discard the other decisions
            st = self._state.get(root, binding.id)
            if st is not None and st.consecutive_eval_errors:
                self._state.clear_eval_errors(root, binding.id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("clearing eval errors of %s/%s failed: %s", root, binding.id, exc)
        if run_now is not None and d.reason == FireReason.CONCURRENCY.value:
            self._defer_run_now(run_now)  # M3: a full machine is transient, keep the request
        if d.fire:
            out.append(d)
            key = str(Path(root).resolve())
            self._pending_by_root[key] = self._pending_by_root.get(key, 0) + 1
            self._pending_schedules.add((key, binding.id))

    def _defer_run_now(self, req: RunNowRequest) -> None:
        """Write a concurrency-blocked run-now request back so a later tick retries it (same
        ``requested_at`` => same fire key, still bounded by ``REQUEST_TTL_SECONDS``)."""
        directory = self._state_dir / SCHEDULES_SUBDIR / REQUESTS_SUBDIR
        body = {
            "kind": REQUEST_KIND_RUN_NOW,
            "workspace_root": req.workspace_root,
            "schedule_id": req.schedule_id,
            "requested_at": req.requested_at.isoformat(),
            "force": req.force,
            "requested_by": req.requested_by,
        }
        try:
            directory.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(directory / f"{uuid.uuid4().hex}.json", json.dumps(body).encode())
        except OSError as exc:
            self._events.emit(
                "schedule.request_dropped",
                workspace=req.workspace_root,
                schedule_id=req.schedule_id,
                reason="concurrency",
                detail=str(exc),
            )
            return
        self._events.emit(
            "schedule.run_now_deferred",
            workspace=req.workspace_root,
            schedule_id=req.schedule_id,
            reason=FireReason.CONCURRENCY.value,
        )

    def _quarantine_on_repeat(self, root: str, schedule_id: str, exc: Exception) -> None:
        logger.warning("evaluation of %s/%s failed: %s", root, schedule_id, exc)
        count = self._state.bump_eval_error(root, schedule_id)
        self._events.emit(
            "schedule.eval_failed", workspace=root, schedule_id=schedule_id, detail=str(exc)
        )
        if count >= MAX_CONSECUTIVE_EVAL_ERRORS:
            self._state.set_status(
                root, schedule_id, STATUS_QUARANTINED, f"{count} consecutive evaluation errors"
            )
            self._events.emit(
                "schedule.quarantined", workspace=root, schedule_id=schedule_id, detail=str(exc)
            )

    def _launch(self, d: FireDecision) -> None:
        """Hand one firing decision to the launcher; a launcher failure never escapes."""
        assert d.fire_key is not None and d.binding is not None
        if self._launcher is None:
            logger.info(
                "no launcher configured; %s/%s decision not launched",
                d.workspace_root,
                d.schedule_id,
            )
            return
        try:
            self._launcher.launch(d)
        except Exception as exc:  # noqa: BLE001 - the launcher contract is "never raise"
            logger.exception("launcher raised for %s/%s", d.workspace_root, d.schedule_id)
            self._state.record_failure(d.workspace_root, d.schedule_id, str(exc))
            self._events.emit(
                "schedule.fire_failed",
                workspace=d.workspace_root,
                schedule_id=d.schedule_id,
                fire_key=d.fire_key,
                detail=str(exc),
            )
        # A record means the key is burned (launched OR failed): only then is it safe to drop the
        # queued fire and to let an event source consume its input.
        if not self._fires.exists(d.fire_key, d.workspace_root):
            return
        if d.reason == FireReason.QUEUE_DRAIN:
            self._state.clear_queued(d.workspace_root, d.schedule_id)
            self._queued_meta.pop((str(Path(d.workspace_root).resolve()), d.schedule_id), None)
        self._commit(d.fire_key)

    def fire(self, decision: FireDecision) -> FireIntentRecord | None:
        """Launch one already-evaluated firing decision and return its fire record.

        Public seam for in-process callers (``ao schedule run-now --local``) that evaluate a
        single binding themselves and must not reach into ``_launcher``. Returns ``None`` when no
        launcher is configured. Unlike :meth:`_launch` a launcher exception is NOT swallowed: the
        caller reports it (the daemon path keeps the never-raise contract via :meth:`_launch`).
        """
        if self._launcher is None:
            return None
        return self._launcher.launch(decision)

    # -- run-now (HLD §6.9) ---------------------------------------------------------------------

    def drain_run_now_requests(self, now: datetime | None = None) -> list[RunNowRequest]:
        """Read and DELETE each ``requests/*.json`` run-now file; expired/invalid ones are
        discarded with ``schedule.request_expired``. Webhook deliveries (``kind: webhook``)
        share the directory and are left for ``T-Wh9Kv1``."""
        now = now if now is not None else self._clock()
        directory = self._state_dir / SCHEDULES_SUBDIR / REQUESTS_SUBDIR
        out: list[RunNowRequest] = []
        if not directory.is_dir():
            return out
        for path in sorted(directory.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = None
            if (
                isinstance(raw, dict)
                and raw.get("kind", REQUEST_KIND_RUN_NOW) != REQUEST_KIND_RUN_NOW
            ):
                continue  # not ours
            path.unlink(missing_ok=True)  # delete first: at-most-once even if processing crashes
            requested_at = _parse_dt(raw.get("requested_at")) if isinstance(raw, dict) else None
            if (
                not isinstance(raw, dict)
                or requested_at is None
                or not isinstance(raw.get("workspace_root"), str)
                or not isinstance(raw.get("schedule_id"), str)
            ):
                self._events.emit("schedule.request_expired", reason="invalid", detail=path.name)
                continue
            if now - requested_at > timedelta(seconds=REQUEST_TTL_SECONDS):
                self._events.emit(
                    "schedule.request_expired",
                    workspace=raw["workspace_root"],
                    schedule_id=raw["schedule_id"],
                    reason="expired",
                    detail=f"requested_at={requested_at.isoformat()}",
                )
                continue
            out.append(
                RunNowRequest(
                    workspace_root=raw["workspace_root"],
                    schedule_id=raw["schedule_id"],
                    requested_at=requested_at,
                    force=bool(raw.get("force", False)),
                    requested_by=str(raw.get("requested_by", "cli")),
                )
            )
        return out

    # -- completed-schedule restart (T-Un0Lm6 M1) -----------------------------------------------

    def drain_restart_requests(self, now: datetime | None = None) -> list[str]:
        """Read and DELETE each ``kind: restart_completed`` request and apply it here, in the
        engine (the single ``state.json`` writer): reset ``runs_count``, clear ``queued_*`` and
        set the status back to ``active``. Expired/invalid files are discarded with
        ``schedule.request_expired``; a request for a schedule that is no longer ``completed`` is
        a no-op. Returns the restarted schedule ids. Never raises."""
        now = now if now is not None else self._clock()
        directory = self._state_dir / SCHEDULES_SUBDIR / REQUESTS_SUBDIR
        out: list[str] = []
        if not directory.is_dir():
            return out
        for path in sorted(directory.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # run-now's drain owns unreadable files
            if not isinstance(raw, dict) or raw.get("kind") != REQUEST_KIND_RESTART_COMPLETED:
                continue
            path.unlink(missing_ok=True)  # delete first: at-most-once even if processing crashes
            requested_at = _parse_dt(raw.get("requested_at"))
            root, sid = raw.get("workspace_root"), raw.get("schedule_id")
            if requested_at is None or not isinstance(root, str) or not isinstance(sid, str):
                self._events.emit("schedule.request_expired", reason="invalid", detail=path.name)
                continue
            if now - requested_at > timedelta(seconds=REQUEST_TTL_SECONDS):
                self._events.emit(
                    "schedule.request_expired",
                    workspace=root,
                    schedule_id=sid,
                    reason="expired",
                    detail=f"requested_at={requested_at.isoformat()}",
                )
                continue
            if apply_restart(self._state, root, sid):
                self._until_errors.pop((str(Path(root).resolve()), sid), None)
                out.append(sid)
        return out

    # -- capped-run resume (T-nI4DBf, HLD §9.5) -------------------------------------------------

    def drain_resume_requests(self, now: datetime | None = None) -> list[ResumeOutcome]:
        """Read and DELETE each ``kind: resume_capped`` request and perform it -- this method is
        the single launch authority for resumes. Expired/invalid files are discarded with
        ``schedule.request_expired``. Also the in-process path for ``ao schedule resume`` when no
        daemon is live. Never raises."""
        now = now if now is not None else self._clock()
        directory = self._state_dir / SCHEDULES_SUBDIR / REQUESTS_SUBDIR
        out: list[ResumeOutcome] = []
        if not directory.is_dir():
            return out
        for path in sorted(directory.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # run-now's drain owns unreadable files
            if not isinstance(raw, dict) or raw.get("kind") != REQUEST_KIND_RESUME_CAPPED:
                continue
            path.unlink(missing_ok=True)  # delete first: at-most-once even if processing crashes
            req = ResumeCappedRequest.from_json(raw)
            if req is None:
                self._events.emit("schedule.request_expired", reason="invalid", detail=path.name)
                continue
            if not parse_request_age_ok(req, now):
                self._events.emit(
                    "schedule.request_expired",
                    workspace=req.workspace_root,
                    schedule_id=req.schedule_id,
                    reason="expired",
                    detail=f"requested_at={req.requested_at.isoformat()}",
                )
                continue
            out.append(self._resume_one(req, now))
        return out

    def _resume_one(self, req: ResumeCappedRequest, now: datetime) -> ResumeOutcome:
        root = str(Path(req.workspace_root).resolve())
        binding: ResolvedBinding | None = None
        try:
            binding = next(
                (b for b in self._store_factory(root).load().bindings if b.id == req.schedule_id),
                None,
            )
        except ScheduleFileError as exc:
            self._events.emit(
                "schedule.file_invalid", workspace=root, reason="file_invalid", detail=str(exc)
            )
        launcher = self._launcher if hasattr(self._launcher, "launch_capped_resume") else None
        return drain_resume_capped(
            req,
            binding=binding,
            fires=self._fires,
            ledger=self._ledger,
            launcher=launcher,  # type: ignore[arg-type]
            lock_path=self._governance_lock,
            events=self._events,
            now=now,
            other_run_active=lambda rec: self._other_run_active(rec),
            mark_active=lambda rec: self._state.mark_active(
                rec.workspace_root, rec.schedule_id, rec.fire_key
            ),
        )

    def _other_run_active(self, rec: FireIntentRecord) -> bool:
        """Is a run of the same schedule other than *rec* live (overlap refusal for a resume)?"""
        st = self._state.get(rec.workspace_root, rec.schedule_id)
        if st is None:
            return False
        live = self._live_runs()
        return any(k in live for k in st.active_fire_keys if k != rec.fire_key)

    def _sweep_pause_expiry(self, now: datetime) -> None:
        try:
            sweep_pause_expiry(self._fires, self._events, now)
        except Exception:  # noqa: BLE001 - bookkeeping must never break the tick
            logger.exception("pause-expiry sweep failed")

    def _find_binding(self, entry: Any, schedule_id: str) -> ResolvedBinding | None:
        try:
            for b in self._store_factory(entry.root).load().bindings:
                if b.id == schedule_id:
                    return b
        except ScheduleFileError as exc:
            self._events.emit(
                "schedule.file_invalid",
                workspace=entry.root,
                reason="file_invalid",
                detail=str(exc),
            )
        return None

    # -- liveness / "active" derivation (HLD §6.4) -----------------------------------------------

    def _is_live(self, rec: FireIntentRecord) -> bool:
        """A fire is live iff its run is ``running`` AND its process is alive on the OWNING cached
        supervisor (F2: a fresh instance would call a zombie alive). A run whose ``state.json``
        does not exist yet is "starting": live while the process is."""
        if rec.run_id is None:
            return False
        try:
            running: bool | None = (
                self._repo(rec.workspace_root).load_state(rec.run_id).status == RUN_STATUS_RUNNING
            )
        except RunNotFoundError:
            running = None
        if running is False:
            return False
        return self.process_supervisor(rec.workspace_root).is_running(rec.run_id)

    def _live_runs(self) -> dict[str, FireIntentRecord]:
        """fire_key -> record for every live scheduled run; retires finished keys as a side
        effect (without this a crashed run holds its overlap/concurrency slot forever)."""
        if self._live_memo is not None:
            return self._live_memo
        live: dict[str, FireIntentRecord] = {}
        dead: list[str] = []
        for s in self._state.all().values():
            for key in s.active_fire_keys:
                if key in live:
                    continue
                rec = self._fires.get(key)
                if rec is not None and self._is_live(rec):
                    live[key] = rec
                else:
                    dead.append(key)
        if dead:
            self._state.retire_finished(dead)
            for key in dead:
                self._finalize_governance(key)
        if self._in_tick:
            self._live_memo = live
        return live

    def _overlap_active(self, st: ScheduleState, root: str, sid: str) -> bool:
        live = self._live_runs()
        return (root, sid) in self._pending_schedules or any(k in live for k in st.active_fire_keys)

    def _concurrency_blocked(self, root: str, binding: ResolvedBinding) -> bool:
        live = self._live_runs()
        resolved = str(Path(root).resolve())
        in_root = sum(1 for r in live.values() if str(Path(r.workspace_root).resolve()) == resolved)
        in_root += self._pending_by_root.get(resolved, 0)
        total = len(live) + sum(self._pending_by_root.values())
        return in_root >= binding.max_concurrent or total >= self._max_global  # type: ignore[operator]

    # -- evaluate (HLD §6.3, corrected by F7/F8/F11/F13) -------------------------------------------

    def evaluate(
        self,
        entry: Any,
        binding: ResolvedBinding,
        now: datetime,
        *,
        run_now: RunNowRequest | None = None,
    ) -> FireDecision:
        """Decide whether *binding* fires at *now*. Pure w.r.t. wall-clock time.

        Check order is normative and two orderings are load-bearing -- do not "tidy" them:
        ``already_fired`` comes AFTER catch-up (so a missed instant burns its key exactly once)
        and ``concurrency`` comes LAST (so a fire blocked only by a full machine is retried).
        """
        root = str(Path(entry.root).resolve())
        sid = binding.id

        def no_fire(reason: FireReason, **kw: Any) -> FireDecision:
            return FireDecision(False, reason.value, root, sid, **kw)

        st = self._state.get(root, sid) or ScheduleState()
        if st.status == STATUS_COMPLETED:
            return no_fire(FireReason.COMPLETED)
        if st.status == STATUS_QUARANTINED:
            return no_fire(FireReason.QUARANTINED)
        if st.status == STATUS_DISABLED or not binding.enabled:
            return no_fire(FireReason.DISABLED)

        # ``until`` (HLD §6.8, T-Un0Lm6): status -> until -> queued drain -> next_fire. A satisfied
        # schedule is marked ``completed`` (binding untouched) and neither drains a queued fire
        # nor computes a next one.
        if binding.until is not None:
            satisfied_by = until_condition(
                root, binding, st, self._events, unreadable_seen=self._until_errors
            )
            if satisfied_by is not None:
                self._state.set_status(root, sid, STATUS_COMPLETED, reason=f"until:{satisfied_by}")
                self._events.emit(
                    "schedule.until_satisfied",
                    workspace=root,
                    schedule_id=sid,
                    runs_count=st.runs_count,
                    condition=satisfied_by,
                )
                return no_fire(FireReason.UNTIL_SATISFIED)

        if (
            run_now is None
            and st.queued_fire_key is not None
            and not self._overlap_active(st, root, sid)
        ):
            drained = self._drain_queued(root, binding, st)
            if drained is not None:
                return drained

        candidate: Candidate | None = None
        source: TriggerSource | None = None
        if run_now is not None:
            scheduled_for = run_now.requested_at
        elif binding.kind in PURE_KINDS:
            scheduled = self._next_scheduled_for(root, binding, st, now)
            if isinstance(scheduled, FireReason):
                return no_fire(scheduled)
            scheduled_for = scheduled
        else:
            source = self._source_for(root, binding)
            if source is None:
                return no_fire(FireReason.NEVER)
            candidate = source.observe(binding, st, now)
            if candidate is None:
                return no_fire(FireReason.NOT_DUE)
            scheduled_for = candidate.scheduled_for
            if scheduled_for > now:
                return no_fire(FireReason.NOT_DUE)

        key = fire_key(root, sid, scheduled_for, candidate.discriminator if candidate else "")
        effective_due = scheduled_for
        catch_up_lateness: timedelta | None = None
        if run_now is None:
            # Deterministic jitter from the fire key (itself derived from scheduled_for, not the
            # jittered time) so a restart mid-jitter re-derives the same offset.
            effective_due = scheduled_for + timedelta(seconds=_jitter_offset(key, binding))
            if now < effective_due:
                return no_fire(FireReason.JITTER_PENDING)

            # F7: lateness from effective_due -- jitter must not count as lateness.
            lateness = now - effective_due
            grace = timedelta(seconds=binding.grace_seconds)  # type: ignore[arg-type]
            if (candidate is None or not candidate.skip_catch_up) and lateness > grace:
                window = timedelta(seconds=binding.catch_up_window_seconds)  # type: ignore[arg-type]
                if binding.catch_up == "skip" or lateness > window:
                    if not self._burn(key, REASON_MISSED, root, sid, scheduled_for, effective_due):
                        return self._already_fired(
                            root, sid, scheduled_for, source, binding, candidate
                        )
                    self._state.advance_anchor(root, sid, scheduled_for)
                    self._events.emit(
                        "schedule.missed",
                        workspace=root,
                        schedule_id=sid,
                        fire_key=key,
                        scheduled_for=scheduled_for,
                        lateness_seconds=lateness.total_seconds(),
                    )
                    self._commit_now(source, binding, candidate)
                    return no_fire(FireReason.MISSED, fire_key=key, scheduled_for=scheduled_for)
                catch_up_lateness = lateness

        # at-most-once (§6.6). The queue drain bypasses this for exactly ``queued_fire_key``.
        if self._fires.exists(key, root) and key != st.queued_fire_key:
            return self._already_fired(
                root, sid, scheduled_for, source, binding, candidate, advance=run_now is None
            )
        if catch_up_lateness is not None:  # L3: only for an instant that can actually fire
            self._events.emit(
                "schedule.catch_up",
                workspace=root,
                schedule_id=sid,
                fire_key=key,
                lateness_seconds=catch_up_lateness.total_seconds(),
            )

        advance = run_now is None  # a run-now never disturbs the natural schedule's anchor
        if self._overlap_active(st, root, sid) and not (run_now is not None and run_now.force):
            return self._apply_overlap(
                root, binding, st, key, scheduled_for, effective_due, source, candidate, advance
            )

        # Concurrency is checked LAST and does NOT burn the key -- unlike overlap/missed. This
        # asymmetry is deliberate: a full machine is a transient shortage, so the same instant
        # must still fire once a slot frees inside the grace window; an overlap skip or a missed
        # instant is a final decision about that instant.
        # Governance (HLD §8.1): AFTER overlap so an overlap skip keeps its true cause, BEFORE
        # concurrency because a governance skip burns the key (deterministic "skipped, here is
        # why") while a concurrency skip does not.
        blocked, verdict = self._govern(
            root, binding, key, scheduled_for, effective_due, source, candidate, advance
        )
        if blocked is not None:
            return blocked

        if self._concurrency_blocked(root, binding):
            return self._concurrency_skip(root, sid, key, scheduled_for, effective_due)

        if source is not None and candidate is not None:
            self._pending_commits[key] = (source, binding, candidate)
        return self._fire(root, binding, key, scheduled_for, effective_due, verdict)

    def _next_scheduled_for(
        self, root: str, binding: ResolvedBinding, st: ScheduleState, now: datetime
    ) -> datetime | FireReason:
        """cron/interval: the single most-recent due instant (backlog collapse, ADR-0014 D4), or
        the no-fire reason. Never backfills; bounded by ``MAX_CATCHUP_STEPS``."""
        sched = self._schedulers[binding.kind]
        trigger = binding.to_trigger()
        anchor = st.last_scheduled_for
        if anchor is None:
            # First sighting: pin the anchor so a restart does not push every future instant out
            # of reach by recomputing "next after now" forever.
            self._state.advance_anchor(root, binding.id, now)
            anchor = now
        nxt = sched.next_fire(trigger, anchor)
        if nxt is None:
            if st.next_fire_at is not None:
                self._state.set_next_fire(root, binding.id, None)
            return FireReason.NEVER
        scheduled_for, skipped = nxt, 0
        while skipped < MAX_CATCHUP_STEPS:
            following = sched.next_fire(trigger, scheduled_for)
            if following is None or following > now:
                break
            # M2: with jitter, only collapse past an instant that is already STALE (its jitter
            # window opened and ``grace`` elapsed) and onto one whose own window is open. Never
            # skip an instant that is still waiting out jitter or can still fire in grace -- with
            # jitter >= period that silently lost most fires.
            if binding.jitter_seconds and (
                self._effective_due(root, binding, scheduled_for)
                + timedelta(seconds=binding.grace_seconds or 0)
                >= now
                or self._effective_due(root, binding, following) > now
            ):
                break
            scheduled_for, skipped = following, skipped + 1
        upcoming = scheduled_for if scheduled_for > now else sched.next_fire(trigger, now)
        if upcoming != st.next_fire_at:
            self._state.set_next_fire(root, binding.id, upcoming)
        logger.debug("schedule.evaluated %s/%s next_fire_at=%s", root, binding.id, upcoming)
        memo_key = (root, binding.id)
        if not skipped:
            self._collapse_reported.pop(memo_key, None)
        elif self._collapse_reported.get(memo_key) != (scheduled_for, skipped):
            # Reported once: burning one record per skipped instant would cost one fsync each.
            # L1: and once per distinct collapse, not again every tick while the instant waits
            # out jitter or a concurrency slot.
            self._collapse_reported[memo_key] = (scheduled_for, skipped)
            self._events.emit(
                "schedule.missed",
                workspace=root,
                schedule_id=binding.id,
                scheduled_for=scheduled_for,
                reason=REASON_BACKLOG_COLLAPSED,
                skipped_instants=skipped,
            )
        if scheduled_for > now:
            return FireReason.NOT_DUE
        return scheduled_for

    @staticmethod
    def _effective_due(root: str, binding: ResolvedBinding, scheduled_for: datetime) -> datetime:
        key = fire_key(root, binding.id, scheduled_for)
        return scheduled_for + timedelta(seconds=_jitter_offset(key, binding))

    def _apply_overlap(
        self,
        root: str,
        binding: ResolvedBinding,
        st: ScheduleState,
        key: str,
        scheduled_for: datetime,
        effective_due: datetime,
        source: TriggerSource | None,
        candidate: Candidate | None,
        advance: bool,
    ) -> FireDecision:
        sid = binding.id
        active = list(st.active_fire_keys)
        if binding.overlap == "skip":
            self._burn(key, REASON_OVERLAP, root, sid, scheduled_for, effective_due)
            self._after_burn(root, sid, scheduled_for, advance, source, binding, candidate)
            self._events.emit(
                "schedule.skipped_overlap",
                workspace=root,
                schedule_id=sid,
                fire_key=key,
                reason=REASON_OVERLAP,
                active=active,
            )
            return FireDecision(
                False, FireReason.OVERLAP_SKIP.value, root, sid, key, scheduled_for, effective_due
            )
        if binding.overlap == "queue":
            if st.queued_fire_key is None:
                # F8: the queued key gets NO fire record until its drain launches it
                # (intended -> launched); a record now would make the drain collide with it.
                self._state.set_queued(
                    root, sid, key, scheduled_for, candidate.discriminator if candidate else ""
                )
                self._queued_meta[(root, sid)] = (key, scheduled_for, effective_due)
            else:
                self._burn(key, REASON_QUEUE_FULL, root, sid, scheduled_for, effective_due)
                self._events.emit(
                    "schedule.skipped_overlap",
                    workspace=root,
                    schedule_id=sid,
                    fire_key=key,
                    reason=REASON_QUEUE_FULL,
                    active=active,
                )
            self._after_burn(root, sid, scheduled_for, advance, source, binding, candidate)
            return FireDecision(
                False, FireReason.OVERLAP_QUEUED.value, root, sid, key, scheduled_for, effective_due
            )
        # ``allow`` (``replace`` is a load-time error in the model and is not implemented here):
        # governance, then the concurrency caps (same order as the non-overlap path).
        blocked, verdict = self._govern(
            root, binding, key, scheduled_for, effective_due, source, candidate, advance
        )
        if blocked is not None:
            return blocked
        if self._concurrency_blocked(root, binding):
            return self._concurrency_skip(root, sid, key, scheduled_for, effective_due)
        if source is not None and candidate is not None:
            self._pending_commits[key] = (source, binding, candidate)
        return self._fire(root, binding, key, scheduled_for, effective_due, verdict)

    def _concurrency_skip(
        self,
        root: str,
        sid: str,
        key: str,
        scheduled_for: datetime,
        effective_due: datetime | None,
    ) -> FireDecision:
        """The one place a concurrency skip is built (main, ``allow`` and queue-drain paths).
        It deliberately writes NO fire record: a full machine is transient (AC10/AC18)."""
        self._events.emit(
            "schedule.skipped_concurrency", workspace=root, schedule_id=sid, fire_key=key
        )
        return FireDecision(
            False, FireReason.CONCURRENCY.value, root, sid, key, scheduled_for, effective_due
        )

    def _drain_queued(
        self, root: str, binding: ResolvedBinding, st: ScheduleState
    ) -> FireDecision | None:
        """The active run ended: launch the buffered fire first. Subject to the concurrency caps
        (a drain must not exceed them) and, like any concurrency skip, leaves the queue intact."""
        sid = binding.id
        key = st.queued_fire_key
        assert key is not None
        meta = self._queued_meta.get((root, sid))
        if (meta is None or meta[0] != key) and st.queued_scheduled_for is not None:
            # Restarted since queueing: rehydrate from the persisted original scheduled_for. The
            # key must re-derive from it; effective_due is deterministic in (key, binding).
            persisted = st.queued_scheduled_for
            if fire_key(root, sid, persisted, st.queued_discriminator) == key:
                due = persisted + timedelta(seconds=_jitter_offset(key, binding))
                meta = (key, persisted, due)
                self._queued_meta[(root, sid)] = meta
        if meta is None or meta[0] != key:
            # Legacy state (key without instant) or an inconsistent pair: the instant needed to
            # launch is unknown. Drop it loudly rather than guess (at-most-once beats duplicate).
            self._state.clear_queued(root, sid)
            self._events.emit(
                "schedule.missed",
                workspace=root,
                schedule_id=sid,
                fire_key=key,
                reason=REASON_QUEUE_LOST,
            )
            return None
        _, scheduled_for, effective_due = meta
        # Second governance call site (HLD §8.1, M2): the drain returns before the main gate step.
        # Gated against the ORIGINAL scheduled_for; a rejection clears and burns the queued key.
        blocked, verdict = self._govern(
            root, binding, key, scheduled_for, effective_due, None, None, False, from_queue=True
        )
        if blocked is not None:
            return blocked
        if self._concurrency_blocked(root, binding):
            return self._concurrency_skip(root, sid, key, scheduled_for, effective_due)
        return self._fire(
            root, binding, key, scheduled_for, effective_due, verdict, reason=FireReason.QUEUE_DRAIN
        )

    # -- governance (T-PpE5lf) -----------------------------------------------------------------

    @staticmethod
    def _fire(
        root: str,
        binding: ResolvedBinding,
        key: str,
        scheduled_for: datetime,
        effective_due: datetime | None,
        verdict: GateResult | None,
        reason: FireReason = FireReason.DUE,
    ) -> FireDecision:
        return FireDecision(
            True,
            reason.value,
            root,
            binding.id,
            key,
            scheduled_for,
            effective_due,
            None,
            binding,
            effective_limits=binding.limits,
            gate=verdict,
        )

    @staticmethod
    def _gate_zone(binding: ResolvedBinding) -> tuple[ZoneInfo, WeekStart]:
        return ZoneInfo(binding.timezone or "UTC"), binding.week_start or "monday"

    def _govern(
        self,
        root: str,
        binding: ResolvedBinding,
        key: str,
        scheduled_for: datetime,
        effective_due: datetime | None,
        source: TriggerSource | None,
        candidate: Candidate | None,
        advance: bool,
        *,
        from_queue: bool = False,
    ) -> tuple[FireDecision | None, GateResult | None]:
        """Run the gate. ``(None, verdict)`` admits; ``(decision, verdict)`` is a no-fire that has
        already burned the key. A gate that raises fails CLOSED: burn ``governance_error``, emit
        at ERROR, then re-raise so ``_collect``'s eval-error/quarantine path still applies."""
        limits = binding.limits
        if limits is None:
            return None, None
        sid = binding.id
        tz, week_start = self._gate_zone(binding)
        try:
            g = gate(
                binding,
                limits,
                scheduled_for,
                ledger=self._ledger,
                workspace_root=root,
                tz=tz,
                week_start=week_start,
            )
        except Exception as exc:  # noqa: BLE001 - fail closed on any gate failure
            self._burn_governance(
                root, binding, limits, key, scheduled_for, effective_due,
                REASON_GOVERNANCE_ERROR, {"error": str(exc)}, from_queue,
            )  # fmt: skip
            self._events.emit(
                "schedule.governance.error",
                workspace=root,
                schedule_id=sid,
                fire_key=key,
                reason=REASON_GOVERNANCE_ERROR,
                detail=str(exc),
            )
            raise
        if g.admit:
            return None, g
        reason = g.reason or REASON_GOVERNANCE_ERROR
        self._burn_governance(
            root, binding, limits, key, scheduled_for, effective_due, reason, g.detail, from_queue
        )
        if not from_queue:
            self._after_burn(root, sid, scheduled_for, advance, source, binding, candidate)
        self._events.emit(
            "schedule.governance.skipped",
            workspace=root,
            schedule_id=sid,
            fire_key=key,
            scheduled_for=scheduled_for,
            reason=reason,
            **{k: v for k, v in g.detail.items() if k != "reason"},
        )
        return (
            FireDecision(
                False,
                reason,
                root,
                sid,
                key,
                scheduled_for,
                effective_due,
                effective_limits=limits,
                gate=g,
            ),
            g,
        )

    def _burn_governance(
        self,
        root: str,
        binding: ResolvedBinding,
        limits: LimitsSpec,
        key: str,
        scheduled_for: datetime,
        effective_due: datetime | None,
        reason: str,
        detail: dict[str, Any],
        from_queue: bool,
    ) -> None:
        tz, week_start = self._gate_zone(binding)
        try:
            self._fires.write_suppressed(
                key,
                reason,
                workspace_root=root,
                schedule_id=binding.id,
                scheduled_for=scheduled_for,
                effective_due=effective_due,
                governance=skeleton(limits, str(tz), week_start, detail),
            )
        except FireAlreadyRecordedError:
            pass
        if from_queue:  # the queued fire is final: drop it so it cannot be drained again
            self._state.clear_queued(root, binding.id)
            self._queued_meta.pop((root, binding.id), None)

    def _finalize_governance(self, key: str) -> None:
        """Retire hook (HLD §6.4, M3): a finished governed run gets its cap state. Never raises.

        Idempotent (a ``final``/capped record is a no-op) and re-driven (M2): a failure parks the
        key in ``_finalize_retry`` for later ticks (capped exponential backoff, L-n1), and
        :meth:`start` sweeps records a crash left unfinalised. After
        ``FINALIZE_RETRY_MAX_ATTEMPTS`` failures it gives up with ONE ERROR event; the record
        keeps holding its reservation (fail-safe) until the next restart's sweep."""
        try:
            self._finalize_governance_once(key)
        except Exception:  # noqa: BLE001 - bookkeeping must never break the tick
            attempts = self._finalize_attempts.get(key, 0) + 1
            self._finalize_attempts[key] = attempts
            if attempts >= FINALIZE_RETRY_MAX_ATTEMPTS:
                logger.exception("governance finalisation of %s failed; giving up", key)
                self._give_up_finalizing(key, attempts)
                return
            delay = min(
                FINALIZE_RETRY_BASE_SECONDS * 2 ** (attempts - 1),
                FINALIZE_RETRY_MAX_BACKOFF_SECONDS,
            )
            self._finalize_next_at[key] = self._monotonic() + delay
            logger.exception(
                "governance finalisation of %s failed (attempt %d); retry in %.0fs",
                key, attempts, delay,
            )  # fmt: skip
            self._finalize_retry.add(key)
        else:
            self._clear_finalize_retry(key)

    def _clear_finalize_retry(self, key: str) -> None:
        self._finalize_retry.discard(key)
        self._finalize_attempts.pop(key, None)
        self._finalize_next_at.pop(key, None)

    def _give_up_finalizing(self, key: str, attempts: int) -> None:
        self._clear_finalize_retry(key)
        rec = self._fires.get(key)
        self._events.emit(
            EVENT_FINALIZE_FAILED,
            workspace=rec.workspace_root if rec is not None else None,
            schedule_id=rec.schedule_id if rec is not None else None,
            fire_key=key,
            run_id=rec.run_id if rec is not None else None,
            attempts=attempts,
        )

    def _retry_finalizations(self) -> None:
        if not self._finalize_retry:
            return  # no clock read on the common path (the tick budget counts monotonic calls)
        now = self._monotonic()
        for key in sorted(self._finalize_retry):
            if now >= self._finalize_next_at.get(key, 0.0):
                self._finalize_governance(key)

    def _adopt_reconciled(self, in_flight: set[str]) -> None:
        """N-M1: an ``intended`` record that reconcile moved to ``launched`` (crash after Popen)
        is a live child nobody tracks. Register it in the schedule's ``active_fire_keys`` (with
        the counters the lost post-launch bookkeeping would have bumped) so overlap/concurrency
        see it and the normal retire hook finalises it / pauses it on a cap trip."""
        for key in sorted(in_flight):
            rec = self._fires.get(key)
            if rec is None or rec.status != "launched" or rec.run_id is None:
                continue
            try:
                self._state.record_fire(
                    rec.workspace_root,
                    rec.schedule_id,
                    scheduled_for=rec.scheduled_for,
                    run_id=rec.run_id,
                    fire_at=rec.created_at,
                    fire_key=rec.fire_key,
                )
            except Exception:  # noqa: BLE001 - recovery must never stop the engine starting
                logger.exception("could not re-register recovered fire %s", rec.fire_key)

    def _recover_resumed(self, rec: FireIntentRecord) -> None:
        """Start-time repair of a ``resumed`` record nothing is tracking (w25-01). Live child ->
        adopt it (never respawn); never spawned -> roll back to ``paused`` for the operator to
        re-request; otherwise the child ran and ended -> the normal retire hook finalises it.
        Start-only on purpose: a tick-time rollback could race an in-flight in-process drain
        (the dashboard's resume) between its ``resumed`` write and its spawn."""
        active = {k for s in self._state.all().values() for k in s.active_fire_keys}
        if rec.fire_key in active or rec.run_id is None:
            return  # tracked: _live_runs retires and finalises it when it ends
        try:
            if self._is_live(rec):
                self._state.adopt_active(rec.workspace_root, rec.schedule_id, rec.fire_key)
                return
            try:
                updated: datetime | None = _parse_dt(
                    getattr(
                        self._repo(rec.workspace_root).load_state(rec.run_id), "updated_at", None
                    )
                )
            except RunNotFoundError:
                updated = None
            if is_stranded_resume(rec, updated):
                recover_stranded_resume(
                    rec, fires=self._fires, events=self._events, lock_path=self._governance_lock
                )
            else:
                self._finalize_governance(rec.fire_key)
        except Exception:  # noqa: BLE001 - recovery must never stop the engine starting
            logger.exception("could not recover resumed record %s", rec.fire_key)

    def _sweep_unfinalized(self) -> None:
        """M2 crash recovery at start: governed ``launched`` records that never got their retire
        hook (the process died between ``retire_finished`` and finalisation) and whose run is no
        longer live. A record that is still live (L-n4: pid reuse / lost tracking) is
        re-registered as active instead of finalised, so the normal retire path owns it."""
        active = {k for s in self._state.all().values() for k in s.active_fire_keys}
        for rec in self._fires.list(status="launched"):
            g = rec.governance
            if g is not None and not g.final and g.cap_state == "resumed":
                self._recover_resumed(rec)  # w25-01: crash around a capped resume's spawn
                continue
            if g is None or g.final or g.cap_state != "none" or rec.run_id is None:
                continue
            if rec.fire_key in active:
                continue  # still tracked: _live_runs retires it when it ends
            try:
                live = self._is_live(rec)
            except Exception:  # noqa: BLE001
                logger.exception("sweep could not read run %s", rec.run_id)
                continue
            if live:
                self._state.adopt_active(rec.workspace_root, rec.schedule_id, rec.fire_key)
            else:
                self._finalize_governance(rec.fire_key)

    def _finalize_governance_once(self, key: str) -> None:
        rec = self._fires.get(key)
        g = rec.governance if rec is not None else None
        if rec is None or g is None or rec.run_id is None or g.final:
            return
        if g.cap_state in GOVERNANCE_CAPPED_STATES:
            return
        try:
            state = self._repo(rec.workspace_root).load_state(rec.run_id)
        except RunNotFoundError:
            return  # never started: orphan reconciliation owns it
        status = getattr(state, "status", None)
        engine_cost = _observed_cost(state)  # the cap's own metric (G-7)
        observed = max(g.cost_observed_usd, engine_cost)
        confidence = "actual" if observed > 0 else g.cost_confidence
        killed = status not in (RUN_STATUS_SUCCEEDED, RUN_STATUS_FAILED)
        # M1 (w01-07) / T-lTW88y re-review: a PARTLY blind run (a killed or blind attempt next to
        # tasks that reported) has spend the observed figure cannot see. Persist the same
        # confidence and charge the ledger's ``run_confidence`` gives it, so the window does not
        # under-charge once ``state.json`` is gone.
        conf = _run_confidence(state)
        if conf in _CONSERVATIVE_CONFIDENCES:
            # G-5/G-12: the label depends neither on a cost reservation (quota-only bindings have
            # none) nor on the run being killed; the reservation branch below may still relabel.
            confidence = conf
        if g.reserved_usd is not None and (
            observed <= 0 or killed or conf in _CONSERVATIVE_CONFIDENCES
        ):
            # H1 / HLD §10.4, §16: a blind executor (no cost reported) or an externally killed run
            # is charged what it could have spent, never $0 -- otherwise period caps never bind.
            observed = max(observed, g.reserved_usd)
            confidence = "partial" if conf == "partial" and not killed else "unknown"
        resumed_at = max(
            (t for t in (_parse_dt(r.get("at")) for r in g.resumes) if t is not None),
            default=None,
        )
        trips = [
            t
            for t in getattr(state, "tripped_breakers", [])
            if isinstance(t, TrippedBreaker)
            and t.id == COST_CAP_BREAKER_ID
            # L3: an unparseable trip time fails closed (counts as fresh); a trip AT the resume
            # instant is stale (the resume re-armed the breaker after it).
            and (
                resumed_at is None
                or ((_parse_dt(t.at) or datetime.max.replace(tzinfo=UTC)) > resumed_at)
            )
        ]
        upd: dict[str, Any] = {
            "cost_observed_usd": observed,
            "cost_confidence": confidence,
            "engine_cost_usd": engine_cost,
        }
        capped = status == RUN_STATUS_FAILED and bool(trips)
        expires: datetime | None = None
        now = self._clock()
        if capped and g.on_cap == "abort":
            upd.update(cap_state="aborted", final=True)
        elif capped:
            expires = now + timedelta(seconds=g.pause_ttl_seconds or 0)
            upd.update(cap_state="paused", paused_at=now, pause_expires_at=expires)
        else:
            upd["final"] = True  # succeeded / non-cap failure: no more governed spend
        # Persist BEFORE emitting: a failed write is retried without a duplicate event.
        self._fires.update(key, governance=g.model_copy(update=upd))
        if capped:
            cap = g.run_cost_cap_usd
            self._events.emit(
                "schedule.governance.run_cost_capped",
                workspace=rec.workspace_root,
                schedule_id=rec.schedule_id,
                fire_key=key,
                run_id=rec.run_id,
                cost_usd=observed,
                cap_usd=cap,
                engine_cost_usd=engine_cost,
                overshoot_usd=max(0.0, engine_cost - cap) if cap is not None else 0.0,
                on_cap=g.on_cap,
                pause_expires_at=expires,
            )

    # -- small helpers -----------------------------------------------------------------------------

    def _burn(
        self,
        key: str,
        reason: str,
        root: str,
        sid: str,
        scheduled_for: datetime,
        effective_due: datetime | None,
    ) -> bool:
        """Write a ``suppressed`` record (burning the key). False if one already existed."""
        try:
            self._fires.write_suppressed(
                key,
                reason,
                workspace_root=root,
                schedule_id=sid,
                scheduled_for=scheduled_for,
                effective_due=effective_due,
            )
        except FireAlreadyRecordedError:
            return False
        return True

    def _after_burn(
        self,
        root: str,
        sid: str,
        scheduled_for: datetime,
        advance: bool,
        source: TriggerSource | None,
        binding: ResolvedBinding,
        candidate: Candidate | None,
    ) -> None:
        if advance:
            self._state.advance_anchor(root, sid, scheduled_for)  # F13: advance on EVERY burn
        self._commit_now(source, binding, candidate)

    def _already_fired(
        self,
        root: str,
        sid: str,
        scheduled_for: datetime,
        source: TriggerSource | None,
        binding: ResolvedBinding,
        candidate: Candidate | None,
        advance: bool = True,
    ) -> FireDecision:
        self._after_burn(root, sid, scheduled_for, advance, source, binding, candidate)
        return FireDecision(
            False,
            FireReason.ALREADY_FIRED.value,
            root,
            sid,
            fire_key(root, sid, scheduled_for, candidate.discriminator if candidate else ""),
            scheduled_for,
        )

    def _source_for(self, root: str, binding: ResolvedBinding) -> TriggerSource | None:
        factory = self._source_factories.get(binding.kind)
        if factory is None:
            return None
        cached = self._sources.get((root, binding.id))
        if cached is not None and cached[0] == binding:
            return cached[1]
        source = factory(root, binding)
        self._sources[(root, binding.id)] = (binding, source)
        return source

    @staticmethod
    def _commit_now(
        source: TriggerSource | None, binding: ResolvedBinding, candidate: Candidate | None
    ) -> None:
        if source is not None and candidate is not None:
            source.commit(binding, candidate)

    def _commit(self, key: str) -> None:
        pending = self._pending_commits.pop(key, None)
        if pending is not None:
            source, binding, candidate = pending
            source.commit(binding, candidate)

    # -- status (HLD §12) ----------------------------------------------------------------------

    def status_snapshot(self) -> dict[str, Any]:
        """HLD §12's ``schedules`` payload. A pure read of the state store, the registry and the
        schedule files plus in-memory tick timing; performs no evaluation and never raises."""
        try:
            return self._snapshot()
        except Exception as exc:  # noqa: BLE001
            logger.warning("status_snapshot failed: %s", exc)
            return {**self._empty_snapshot(), "error": str(exc)}

    def _empty_snapshot(self) -> dict[str, Any]:
        """The HLD §12 shape with zero values (L7): error payloads keep every key."""
        return {
            "enabled": True,
            "eval_interval_seconds": self._eval_interval,
            "totals": {"active": 0, "disabled": 0, "completed": 0, "quarantined": 0},
            "next_fire_at": None,
            "live_scheduled_runs": 0,
            "max_concurrent_global": self._max_global,
            "recent_failures": 0,
            "last_tick_at": _iso(self._last_tick_at),
            "last_tick_duration_ms": self._last_tick_duration_ms,
            "webhook": {"enabled": False, "host": None, "port": None},
            "by_workspace": [],
        }

    def _snapshot(self) -> dict[str, Any]:
        totals = {"active": 0, "disabled": 0, "completed": 0, "quarantined": 0}
        by_workspace: list[dict[str, Any]] = []
        live_runs = failures = 0
        next_fire: datetime | None = None
        try:
            entries = [e for e in self._registry_factory(self._registry_path).load().workspaces]
        except Exception:  # noqa: BLE001
            entries = []
        for entry in entries:
            if not _opted_in(entry):
                continue
            try:
                bindings = self._store_factory(entry.root).load().bindings
            except Exception:  # noqa: BLE001 - e.g. file_invalid: show the workspace, no rows
                bindings = []
            rows: list[dict[str, Any]] = []
            for b in bindings:
                st = self._state.get(entry.root, b.id) or ScheduleState()
                status = "disabled" if not b.enabled and st.status == "active" else st.status
                totals[status] += 1
                live_runs += len(st.active_fire_keys)
                failures += st.consecutive_failures
                if status == "active" and st.next_fire_at is not None:
                    next_fire = (
                        st.next_fire_at if next_fire is None else min(next_fire, st.next_fire_at)
                    )
                rows.append(
                    {
                        "id": b.id,
                        "kind": b.kind,
                        "enabled": b.enabled,
                        "status": status,
                        "next_fire_at": _iso(st.next_fire_at),
                        "last_fire_at": _iso(st.last_fire_at),
                        "last_run_id": st.last_run_id,
                        "last_status": st.last_status,
                        "consecutive_failures": st.consecutive_failures,
                    }
                )
            by_workspace.append({"root": entry.root, "schedules": rows})
        return {
            "enabled": True,
            "eval_interval_seconds": self._eval_interval,
            "totals": totals,
            "next_fire_at": _iso(next_fire),
            "live_scheduled_runs": live_runs,
            "max_concurrent_global": self._max_global,
            "recent_failures": failures,
            "last_tick_at": _iso(self._last_tick_at),
            "last_tick_duration_ms": self._last_tick_duration_ms,
            "webhook": {"enabled": False, "host": None, "port": None},
            "by_workspace": by_workspace,
        }


# Run confidences the ledger charges conservatively (``max(observed, reserved)``) while open.
_CONSERVATIVE_CONFIDENCES: Final = frozenset({"partial", "unknown"})


def _run_confidence(state: Any) -> CostConfidence | None:
    """``usage_ledger.run_confidence`` for a real :class:`RunState`; ``None`` for any other shape
    (a probe that could not load a full state), which keeps the plain observed-cost rule."""
    return run_confidence(state) if isinstance(state, RunState) else None


def _observed_cost(state: Any) -> float:
    tasks = getattr(state, "tasks", None)
    if not isinstance(tasks, dict):
        return 0.0
    return float(compute_run_usage_totals(state).cost_usd)


def _opted_in(entry: Any) -> bool:
    """F5: schedules are opt-in per workspace (``WorkspaceEntry.schedules``, default False;
    the field lands with ``T-Sv5Hb3`` -- until then no registry entry opts in)."""
    return bool(getattr(entry, "schedules", False))


UNTIL_MAX_RUNS: Final = "max_runs"
UNTIL_ARTIFACT_EXISTS: Final = "artifact_exists"
UNTIL_GATE: Final = "gate"
DEFAULT_UNTIL_MAX_RUNS: Final = 100  # CLI bound for an ``until`` schedule given none (HLD §6.8)


class EventSink(Protocol):
    """The slice of ``ScheduleEventLog`` the ``until`` helpers need (also easy to fake)."""

    def emit(self, event: str, level: int | None = None, **fields: Any) -> None: ...


def _gate_matches(actual: object, expected: bool | int | str) -> bool:
    """``==`` restricted to bool/int/str: a float, object, list or null never matches (and never
    raises), and a bool never equals a non-bool (``True == 1`` must not end a loop)."""
    if isinstance(actual, bool) != isinstance(expected, bool):
        return False
    if not isinstance(actual, bool | int | str):
        return False
    return bool(actual == expected)


def until_condition(
    workspace_root: str | Path,
    binding: ResolvedBinding,
    state: ScheduleState,
    events: EventSink | None = None,
    unreadable_seen: dict[tuple[str, str], str] | None = None,
) -> str | None:
    """Which ``until`` condition (if any) is satisfied now; ``None`` = keep going.

    Order is normative (HLD §6.8): ``max_runs`` FIRST so termination is guaranteed whatever the
    other conditions do, then ``artifact_exists``, then the gate-file triple. ``max_runs``
    counts *launched runs* (``runs_count``, bumped when a launch succeeds, whatever the run's
    outcome): a schedule whose runs all fail still stops at the cap. Burned/skipped instants and
    failed launches do not count.

    Bad reads NEVER terminate -- deliberately the opposite of the engine's intra-run
    ``LoopSpec``, where an unreadable gate *fails the run* (the run is broken). Here an
    unreadable gate means only that the next fire proceeds: a transient write or a crashed run
    must not silently end a multi-day schedule. Such reads emit ``schedule.until_unreadable``,
    de-duplicated when *unreadable_seen* is given: only a change of error (per schedule) emits,
    and a clean read forgets the last error.
    All paths resolve through ``LocalFsArtifactStore`` (workspace-root guard, also at read time).
    """
    until = binding.until
    if until is None:
        return None
    if until.max_runs is not None and state.runs_count >= until.max_runs:
        return UNTIL_MAX_RUNS
    store = LocalFsArtifactStore(str(workspace_root))
    seen_key = (str(Path(workspace_root).resolve()), binding.id)
    if until.artifact_exists is not None and store.exists(until.artifact_exists):
        return UNTIL_ARTIFACT_EXISTS
    if until.gate_file is None:
        return None
    try:
        path = Path(store.resolve(until.gate_file))
        if not path.is_file():
            if unreadable_seen is not None:
                unreadable_seen.pop(seen_key, None)
            return None  # absent gate: not done yet (not an error)
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ArtifactPathError) as exc:  # ValueError: JSON + UnicodeDecode
        repeated = unreadable_seen is not None and unreadable_seen.get(seen_key) == str(exc)
        if unreadable_seen is not None:
            unreadable_seen[seen_key] = str(exc)
        if events is not None and not repeated:
            events.emit(
                "schedule.until_unreadable",
                logging.WARNING,
                workspace=str(workspace_root),
                schedule_id=binding.id,
                error=str(exc),
            )
        return None
    if unreadable_seen is not None:
        unreadable_seen.pop(seen_key, None)
    if isinstance(data, dict) and _gate_matches(data.get(until.gate_field), until.gate_equals):
        return UNTIL_GATE
    return None


def apply_restart(store: ScheduleStateStore, root: str | Path, schedule_id: str) -> bool:
    """Restart a ``completed`` schedule in *store*: ``runs_count`` -> 0, ``queued_*`` cleared (a
    stale queue from before completion must not drain after the restart), status ``active``.
    Returns False (no change) when the schedule is not ``completed``. The caller must be the
    state's single writer: the engine, or a process holding ``engine.lock``."""
    st = store.get(root, schedule_id)
    if st is None or st.status != STATUS_COMPLETED:
        return False
    st.runs_count = 0
    store.clear_queued(root, schedule_id)
    store.set_status(root, schedule_id, STATUS_ACTIVE)
    return True


def restart_completed_schedule(
    state_dir: Path, root: str | Path, schedule_id: str, now: datetime
) -> bool:
    """Restart a ``completed`` schedule from outside the engine. Returns True when applied
    directly (no engine running: we took ``engine.lock`` and wrote state ourselves) and False
    when a live engine holds the lock and the restart was queued as a ``restart_completed``
    request, applied at the start of the engine's next tick (single-writer rule, F9)."""
    path = _lock_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            body = {
                "kind": REQUEST_KIND_RESTART_COMPLETED,
                "workspace_root": str(Path(root).resolve()),
                "schedule_id": schedule_id,
                "requested_at": now.astimezone(UTC).isoformat(),
            }
            req_dir = path.parent / REQUESTS_SUBDIR
            req_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(req_dir / f"{uuid.uuid4().hex}.json", json.dumps(body).encode())
            return False
        try:
            apply_restart(ScheduleStateStore(state_dir), root, schedule_id)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return True


def until_satisfied(
    workspace_root: str | Path,
    binding: ResolvedBinding,
    state: ScheduleState,
    events: EventSink | None = None,
) -> bool:
    """HLD §6.8 ``until_satisfied``; see :func:`until_condition` for the semantics."""
    return until_condition(workspace_root, binding, state, events) is not None


def _jitter_offset(key: str, binding: ResolvedBinding) -> int:
    jitter = binding.jitter_seconds or 0
    return int(key[:FIRE_KEY_JITTER_HEX_DIGITS], 16) % (jitter + 1)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


__all__ = [
    "DEFAULT_EVAL_INTERVAL_SECONDS",
    "DEFAULT_MAX_CONCURRENT_GLOBAL",
    "MAX_CATCHUP_STEPS",
    "MAX_CONSECUTIVE_EVAL_ERRORS",
    "REQUEST_TTL_SECONDS",
    "SCHEDULE_TICK_BUDGET_SECONDS",
    "Candidate",
    "EngineLockHeldError",
    "FireDecision",
    "FireReason",
    "RunNowRequest",
    "ScheduleEngine",
    "ScheduledLauncher",
    "TriggerSource",
    "probe_engine_lock",
    "restart_completed_schedule",
    "apply_restart",
]
