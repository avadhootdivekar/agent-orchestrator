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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import IO, Any, Protocol, runtime_checkable

from ..scheduler import CronScheduler, IntervalScheduler, Scheduler
from ..schedules.errors import ScheduleFileError
from ..schedules.models import ResolvedBinding
from ..schedules.store import ScheduleStore
from ..ui.processes import ProcessSupervisor, _pid_alive
from ..ui.runs import RunNotFoundError, RunRepository
from .fire_store import (
    SCHEDULES_SUBDIR,
    FireAlreadyRecordedError,
    FireIntentRecord,
    FireIntentStore,
    ScheduleEventLog,
    ScheduleState,
    ScheduleStateStore,
    fire_key,
)
from .registry import ServiceRegistry

logger = logging.getLogger(__name__)

# -- constants (HLD §7.2 / §6) -----------------------------------------------------------------
DEFAULT_EVAL_INTERVAL_SECONDS = 15.0
SCHEDULE_TICK_BUDGET_SECONDS = 0.5
MAX_CONSECUTIVE_EVAL_ERRORS = 5
MAX_CATCHUP_STEPS = 10_000
REQUEST_TTL_SECONDS = 300
DEFAULT_MAX_CONCURRENT_GLOBAL = 3
ENGINE_LOCK_FILENAME = "engine.lock"
REQUESTS_SUBDIR = "requests"
REQUEST_KIND_RUN_NOW = "run_now"
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


@dataclass(frozen=True)
class Candidate:
    """An event-kind trigger's proposed fire instant (``TriggerSource.observe``)."""

    scheduled_for: datetime
    payload: Any = None


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

    ``observe`` is pure and read-only (no digest save, no queue pop). ``commit`` is called by
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

        self._fires = FireIntentStore(self._state_dir, clock=clock)
        self._state = ScheduleStateStore(self._state_dir)
        self._events = ScheduleEventLog(self._state_dir, clock=clock)

        # F2: ONE long-lived ProcessSupervisor (and repo) per workspace, built lazily, never
        # per call -- only the instance that spawned a child can reap it.
        self._supervisors: dict[str, ProcessSupervisor] = {}
        self._repos: dict[str, RunRepository] = {}
        self._schedulers: dict[str, Scheduler] = {
            "cron": CronScheduler(clock),
            "interval": IntervalScheduler(),
        }
        self._source_factories: dict[str, TriggerSourceFactory] = {}
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
        self._pending_by_root: dict[str, int] = {}
        self._pending_commits: dict[str, tuple[TriggerSource, ResolvedBinding, Candidate]] = {}

    # -- collaborators exposed for the launcher / wiring --------------------------------------

    @property
    def state_store(self) -> ScheduleStateStore:
        return self._state

    @property
    def fire_store(self) -> FireIntentStore:
        return self._fires

    @property
    def event_log(self) -> ScheduleEventLog:
        return self._events

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
        mono = self._monotonic()
        if self._last_eval_mono is not None and mono - self._last_eval_mono < self._eval_interval:
            return []
        self._last_eval_mono = mono
        now = now if now is not None else self._clock()
        deadline = mono + SCHEDULE_TICK_BUDGET_SECONDS
        self._in_tick, self._live_memo, self._pending_by_root = True, None, {}
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
            if st is not None and st.status in ("completed", "quarantined"):
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
        st = self._state.get(root, binding.id)
        if st is not None and st.consecutive_eval_errors:
            self._state.clear_eval_errors(root, binding.id)
        if d.fire:
            out.append(d)
            key = str(Path(root).resolve())
            self._pending_by_root[key] = self._pending_by_root.get(key, 0) + 1

    def _quarantine_on_repeat(self, root: str, schedule_id: str, exc: Exception) -> None:
        logger.warning("evaluation of %s/%s failed: %s", root, schedule_id, exc)
        count = self._state.bump_eval_error(root, schedule_id)
        self._events.emit(
            "schedule.eval_failed", workspace=root, schedule_id=schedule_id, detail=str(exc)
        )
        if count >= MAX_CONSECUTIVE_EVAL_ERRORS:
            self._state.set_status(
                root, schedule_id, "quarantined", f"{count} consecutive evaluation errors"
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
        if self._in_tick:
            self._live_memo = live
        return live

    def _overlap_active(self, st: ScheduleState) -> bool:
        live = self._live_runs()
        return any(k in live for k in st.active_fire_keys)

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
        if st.status == "completed":
            return no_fire(FireReason.COMPLETED)
        if st.status == "quarantined":
            return no_fire(FireReason.QUARANTINED)
        if st.status == "disabled" or not binding.enabled:
            return no_fire(FireReason.DISABLED)

        # T-Un0Lm6 inserts the ``until`` check HERE (status -> until -> queued drain): on
        # satisfied it sets status "completed", emits schedule.until_satisfied and returns
        # FireReason.UNTIL_SATISFIED.

        if run_now is None and st.queued_fire_key is not None and not self._overlap_active(st):
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

        key = fire_key(root, sid, scheduled_for)
        effective_due = scheduled_for
        if run_now is None:
            # Deterministic jitter from the fire key (itself derived from scheduled_for, not the
            # jittered time) so a restart mid-jitter re-derives the same offset.
            effective_due = scheduled_for + timedelta(seconds=_jitter_offset(key, binding))
            if now < effective_due:
                return no_fire(FireReason.JITTER_PENDING)

            # F7: lateness from effective_due -- jitter must not count as lateness.
            lateness = now - effective_due
            if lateness > timedelta(seconds=binding.grace_seconds):  # type: ignore[arg-type]
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
                self._events.emit(
                    "schedule.catch_up",
                    workspace=root,
                    schedule_id=sid,
                    fire_key=key,
                    lateness_seconds=lateness.total_seconds(),
                )

        # at-most-once (§6.6). The queue drain bypasses this for exactly ``queued_fire_key``.
        if self._fires.exists(key, root) and key != st.queued_fire_key:
            return self._already_fired(
                root, sid, scheduled_for, source, binding, candidate, advance=run_now is None
            )

        advance = run_now is None  # a run-now never disturbs the natural schedule's anchor
        if self._overlap_active(st) and not (run_now is not None and run_now.force):
            return self._apply_overlap(
                root, binding, st, key, scheduled_for, effective_due, source, candidate, advance
            )

        # Concurrency is checked LAST and does NOT burn the key -- unlike overlap/missed. This
        # asymmetry is deliberate: a full machine is a transient shortage, so the same instant
        # must still fire once a slot frees inside the grace window; an overlap skip or a missed
        # instant is a final decision about that instant.
        if self._concurrency_blocked(root, binding):
            self._events.emit(
                "schedule.skipped_concurrency", workspace=root, schedule_id=sid, fire_key=key
            )
            return no_fire(FireReason.CONCURRENCY, fire_key=key, scheduled_for=scheduled_for)

        if source is not None and candidate is not None:
            self._pending_commits[key] = (source, binding, candidate)
        return FireDecision(
            True, FireReason.DUE.value, root, sid, key, scheduled_for, effective_due, None, binding
        )

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
            scheduled_for, skipped = following, skipped + 1
        upcoming = scheduled_for if scheduled_for > now else sched.next_fire(trigger, now)
        if upcoming != st.next_fire_at:
            self._state.set_next_fire(root, binding.id, upcoming)
        logger.debug("schedule.evaluated %s/%s next_fire_at=%s", root, binding.id, upcoming)
        if skipped:
            # Reported once: burning one record per skipped instant would cost one fsync each.
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
                self._state.set_queued(root, sid, key, scheduled_for)
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
        # fall through to the concurrency caps.
        if self._concurrency_blocked(root, binding):
            self._events.emit(
                "schedule.skipped_concurrency", workspace=root, schedule_id=sid, fire_key=key
            )
            return FireDecision(
                False, FireReason.CONCURRENCY.value, root, sid, key, scheduled_for, effective_due
            )
        if source is not None and candidate is not None:
            self._pending_commits[key] = (source, binding, candidate)
        return FireDecision(
            True, FireReason.DUE.value, root, sid, key, scheduled_for, effective_due, None, binding
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
            if fire_key(root, sid, persisted) == key:
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
        if self._concurrency_blocked(root, binding):
            self._events.emit(
                "schedule.skipped_concurrency", workspace=root, schedule_id=sid, fire_key=key
            )
            return FireDecision(
                False, FireReason.CONCURRENCY.value, root, sid, key, scheduled_for, effective_due
            )
        return FireDecision(
            True,
            FireReason.QUEUE_DRAIN.value,
            root,
            sid,
            key,
            scheduled_for,
            effective_due,
            None,
            binding,
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
            fire_key(root, sid, scheduled_for),
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
            return {"enabled": True, "error": str(exc)}

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


def _opted_in(entry: Any) -> bool:
    """F5: schedules are opt-in per workspace (``WorkspaceEntry.schedules``, default False;
    the field lands with ``T-Sv5Hb3`` -- until then no registry entry opts in)."""
    return bool(getattr(entry, "schedules", False))


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
]
