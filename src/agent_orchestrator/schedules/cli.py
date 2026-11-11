"""``ao schedule`` -- the operator surface of the scheduler (T-Cl6Jn9; HLD §9 / §6.9).

Read commands (``list``, ``next``, ``history``, ``validate``), write commands (``add``,
``remove``, ``enable``, ``disable``, ``run-now``) and the foreground ``daemon``.

Design rules (see the ticket's Risks section):

* **Liveness = the engine lock.** "Is an engine running" is decided by probing
  ``<state_dir>/schedules/engine.lock`` (:func:`probe_engine_lock`), never by hub reachability
  (a hub under auth answers 401 and a ``--no-schedules`` daemon has a hub but no engine).
* **One launch path.** ``run-now --local`` builds the very same :class:`ScheduleEngine` +
  :class:`ScheduledLauncher` pair the daemon uses and evaluates the request through
  ``ScheduleEngine.evaluate(run_now=...)``, so overlap/concurrency/governance cannot diverge.
  This module spawns no process itself.
* **One cron implementation.** ``next`` calls the engine's own ``CronScheduler`` /
  ``IntervalScheduler``; it never calls an event ``TriggerSource.commit``.
* Every write to ``.ao/schedules.yaml`` goes through :meth:`ScheduleStore.mutate`.

Time enters through :func:`_now` (tests monkeypatch it); the real registry file and state dir are
only touched through ``AO_SERVICE_CONFIG`` / ``AO_SERVICE_STATE_DIR`` / ``--state-dir`` so tests
never reach a real ``$HOME``.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import signal
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import IO, Annotated, Any, Final
from zoneinfo import ZoneInfo

import typer

from ..run_options import get_option
from ..scheduler import CronScheduler, IntervalScheduler, Scheduler
from ..service.fire_store import (
    SCHEDULES_SUBDIR,
    ScheduleEventLog,
    ScheduleState,
    ScheduleStateStore,
)
from ..service.governance_gate import gate
from ..service.paths import default_registry_path, default_state_dir
from ..service.registry import (
    ServiceRegistry,
    ServiceRegistryFile,
    ServiceScheduleConfig,
    WorkspaceEntry,
)
from ..service.schedule_engine import (
    DEFAULT_UNTIL_MAX_RUNS,
    ENGINE_LOCK_FILENAME,
    MAX_CATCHUP_STEPS,
    PURE_KINDS,
    REQUEST_KIND_RUN_NOW,
    REQUESTS_SUBDIR,
    EngineLockHeldError,
    FireDecision,
    RunNowRequest,
    ScheduleEngine,
    probe_engine_lock,
)
from ..service.statefile import atomic_write_bytes
from .errors import ScheduleFileError
from .governance import LIMITS_REQUIRED, MAX_PROMPT_BYTES, LimitsSpec
from .models import SCHEDULE_RUN_ARG_NAMES, ResolvedBinding, ScheduleFile
from .store import ScheduleStore
from .usage_view import (
    build_usage_doc,
    compact_limits,
    governance_fields,
    paused_records,
    readonly_fire_store,
    reason_with_period,
    render_usage,
)
from .windows import PERIODS, Period, windows_for

app = typer.Typer(name="schedule", help="Declare, inspect and fire workspace schedules.")

# -- constants ---------------------------------------------------------------------------------
DEFAULT_NEXT_COUNT = 10
DEFAULT_HISTORY_COUNT = 50
DAEMON_POLL_SECONDS = 2.0  # upper bound on the foreground loop's wait (shutdown latency)
EXIT_USAGE = 2
LIMITS_HINT = (
    "pass --quota PERIOD=N, --cost-per-run USD and/or --cost-cap PERIOD=USD, or set defaults.limits"
)
REQUESTED_BY_CLI = "cli"
EVENT_ENABLED = "schedule.enabled"
EVENT_DISABLED = "schedule.disabled"
REASON_RUN_NOW = "run_now"
NO_VALUE = "-"
DEFAULT_ID_PREFIX = "wf"
WORKSPACE_MARKER_DIR = ".ao"
PROMPTS_RELDIR = Path(WORKSPACE_MARKER_DIR) / "schedule-prompts"
EXIT_RESUME_REFUSED = 3
STATUS_COMPLETED: Final = "completed"
STATUS_QUARANTINED: Final = "quarantined"
STATUS_DISABLED: Final = "disabled"
STATUS_ACTIVE: Final = "active"
ENGINE_FIX_HINT = "start one with `ao service start` or `ao schedule daemon`"
NO_ENGINE_WARNING = (
    "WARNING: no live schedule engine found -- nothing will fire. "
    "Start one with `ao service start` (or `ao service run`) or `ao schedule daemon`."
)

StateDirOpt = Annotated[
    str | None,
    typer.Option(
        "--state-dir",
        help="Scheduler state directory (default: $AO_SERVICE_STATE_DIR or the XDG state dir).",
    ),
]
WorkspaceOpt = Annotated[
    str | None,
    typer.Option("--workspace", help="Workspace root (default: the workspace containing the cwd)."),
]
JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable output.")]

# Test seams: the clock and (for run-now --local / daemon) the per-workspace process supervisor.
_SUPERVISOR_FACTORY: Callable[[str], Any] | None = None


def _now() -> datetime:
    return datetime.now(UTC)


# -- shared helpers ----------------------------------------------------------------------------


def _fail(message: str, code: int = 1) -> typer.Exit:
    typer.echo(f"ERROR: {message}", err=True)
    return typer.Exit(code)


def _state_dir(raw: str | None) -> Path:
    return Path(raw).expanduser() if raw else default_state_dir()


def _find_workspace(start: Path) -> Path:
    """Nearest ancestor of *start* holding a ``.ao/`` directory, else *start* itself."""
    for candidate in (start, *start.parents):
        if (candidate / WORKSPACE_MARKER_DIR).is_dir():
            return candidate
    return start


def _workspace_root(raw: str | None) -> Path:
    return Path(raw).expanduser().resolve() if raw else _find_workspace(Path.cwd().resolve())


def _load_registry() -> ServiceRegistryFile:
    try:
        return ServiceRegistry(default_registry_path()).load()
    except Exception as exc:  # noqa: BLE001 - a corrupt registry must not block read commands
        typer.echo(f"WARNING: cannot read the service registry ({exc})", err=True)
        return ServiceRegistryFile()


def _selected_roots(workspace: str | None, all_workspaces: bool) -> list[Path]:
    if all_workspaces:
        return [Path(w.root) for w in _load_registry().workspaces]
    return [_workspace_root(workspace)]


def _load_file(root: Path) -> ScheduleFile:
    try:
        return ScheduleStore(root).load()
    except ScheduleFileError as exc:
        raise _fail(str(exc)) from exc


def _fmt(value: datetime | None) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if value is not None else NO_VALUE


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [
        max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)
    ]
    lines = ["  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)).rstrip()]
    lines += ["  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)).rstrip() for r in rows]
    return "\n".join(lines)


def _emit_json(payload: object) -> None:
    typer.echo(json.dumps(payload, indent=2, default=str))


def _registry_entry(registry: ServiceRegistryFile, root: Path) -> WorkspaceEntry | None:
    return next((w for w in registry.workspaces if Path(w.root).resolve() == root), None)


def _inert_reason(registry: ServiceRegistryFile, root: Path) -> str | None:
    """Why a workspace's schedules can never fire (F5 opt-in), or ``None`` when they can."""
    entry = _registry_entry(registry, root)
    if entry is None:
        return (
            f"{root} is not registered with the service, so its schedules are inert; "
            f"register it with `ao service add {root} --schedules`"
        )
    if not entry.schedules:
        return (
            f"schedules are disabled for {root} (opt-in, default off), so they are inert; "
            f"enable them with `ao service set {root} --schedules`"
        )
    return None


@contextmanager
def _hold_engine_lock(state_dir: Path) -> Iterator[bool]:
    """Try to take ``engine.lock`` non-blocking for the duration of the block. Yields whether it
    was acquired; releases on exit. Same file/``flock`` the engine uses (F9)."""
    path = state_dir / SCHEDULES_SUBDIR / ENGINE_LOCK_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    handle: IO[str] = open(path, "a+")  # noqa: SIM115 - released in the finally below
    held = False
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            held = True
        except OSError:
            held = False
        yield held
    finally:
        if held:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


class _SingleWorkspaceRegistry(ServiceRegistry):
    """In-memory one-entry registry (``--workspace``): never reads or writes the real
    ``service.yaml``; the workspace is opted in because the operator asked for it explicitly."""

    def __init__(self, root: str) -> None:
        super().__init__(Path("<in-memory>"))
        self._file = ServiceRegistryFile(workspaces=[WorkspaceEntry(root=root, schedules=True)])

    def load(self) -> ServiceRegistryFile:
        return self._file


def _engine_policy() -> ServiceScheduleConfig:
    """The machine-level scheduler policy (``schedules:`` in the service registry). Unlike the
    read commands, an engine-building command fails CLOSED when the registry is unreadable."""
    try:
        return ServiceRegistry(default_registry_path()).load().schedules
    except Exception as exc:  # noqa: BLE001 - any load failure must refuse, not default open
        raise _fail(f"cannot read the service registry to check the kill switch ({exc})") from exc


def _require_schedules_enabled(policy: ServiceScheduleConfig) -> None:
    if not policy.enabled:
        raise _fail(
            "schedules are disabled machine-wide (registry `schedules.enabled: false`); "
            "nothing will be fired"
        )


def _build_engine(
    state_dir: Path, workspace: Path | None, *, interval: float | None = None
) -> ScheduleEngine:
    """Engine + launcher + trigger sources wired like ``service.cli.build_schedule_engine``
    (shared stores, shared per-workspace supervisor cache, the registry's kill switch, global
    cap and eval interval) but not started. Raises when the kill switch is set."""
    from ..service.schedule_launch import ScheduledLauncher
    from ..service.webhook import WebhookSource

    policy = _engine_policy()
    _require_schedules_enabled(policy)

    class _Deferred:
        target: Any = None

        def launch(self, decision: FireDecision) -> Any:
            return self.target.launch(decision)

        def launch_capped_resume(self, rec: Any, new_cap_usd: float) -> Any:
            # B-1 (w21-03): without this the engine's resume drain sees no launcher capability.
            return self.target.launch_capped_resume(rec, new_cap_usd)

    def clock() -> datetime:
        return _now()

    kwargs: dict[str, Any] = {
        "eval_interval_seconds": interval if interval is not None else policy.eval_interval_seconds,
        "max_concurrent_global": policy.max_concurrent_global,
    }
    if workspace is not None:
        root = str(workspace)
        kwargs["registry_factory"] = lambda _path: _SingleWorkspaceRegistry(root)
    if _SUPERVISOR_FACTORY is not None:
        kwargs["process_supervisor_factory"] = _SUPERVISOR_FACTORY
    deferred = _Deferred()
    engine = ScheduleEngine(
        default_registry_path(), state_dir=state_dir, clock=clock, launcher=deferred, **kwargs
    )
    engine.register_trigger_source(
        "webhook", lambda root, binding: WebhookSource(state_dir, root, binding)
    )
    deferred.target = ScheduledLauncher(
        state_dir,
        process_supervisor_factory=engine.process_supervisor,
        clock=clock,
        fire_store=engine.fire_store,
        state_store=engine.state_store,
        event_log=engine.event_log,
        usage_ledger=engine.usage_ledger,
        governance_lock_path=engine.governance_lock_path,
    )
    return engine


def _status_of(binding: ResolvedBinding, st: ScheduleState) -> str:
    return STATUS_DISABLED if not binding.enabled and st.status == STATUS_ACTIVE else st.status


# -- list --------------------------------------------------------------------------------------


def _governance_summary(
    root: Path, b: ResolvedBinding, fires: Any, ledger: Any, now: datetime
) -> dict[str, Any]:
    """``limits`` / ``today`` / ``paused`` of one binding for ``list`` (read-only)."""
    limits = b.limits
    today: dict[str, Any] | None = None
    if limits is not None and limits.quotas:
        quota = min(limits.quotas, key=lambda q: PERIODS.index(q.period))
        tz = ZoneInfo(b.timezone or "UTC")
        wins = windows_for(now, [quota.period], tz, b.week_start or "monday")
        used = ledger.usage(str(root), b.id, wins)[quota.period].runs
        today = {"period": quota.period, "used": used, "max": quota.max_runs}
    return {
        "limits": compact_limits(limits),
        "today": today,
        "paused": len(paused_records(fires, str(root), b.id)),
    }


@app.command(name="list")
def list_cmd(
    workspace: WorkspaceOpt = None,
    all_workspaces: Annotated[
        bool, typer.Option("--all", help="Every registered workspace.")
    ] = False,
    as_json: JsonOpt = False,
    state_dir: StateDirOpt = None,
) -> None:
    """List schedules with their status, next/last fire and last run."""
    sdir = _state_dir(state_dir)
    registry = _load_registry()
    state = ScheduleStateStore(sdir)
    live = probe_engine_lock(sdir)
    rows: list[list[str]] = []
    payload: list[dict[str, Any]] = []
    warnings: list[str] = []
    any_schedule = False
    now = _now()
    fires, ledger = _readonly_ledger(sdir)
    for root in _selected_roots(workspace, all_workspaces):
        bindings = _load_file(root).bindings
        if not bindings:
            continue
        any_schedule = True
        inert = _inert_reason(registry, root)
        if inert:
            warnings.append(f"WARNING: {inert}")
        for b in bindings:
            st = state.get(root, b.id) or ScheduleState()
            gov = _governance_summary(root, b, fires, ledger, now)
            today = gov["today"]
            row = {
                "workspace": str(root),
                "id": b.id,
                "kind": b.kind,
                "enabled": b.enabled,
                "status": _status_of(b, st),
                "next_fire": _iso(st.next_fire_at),
                "last_fire": _iso(st.last_fire_at),
                "last_run": st.last_run_id,
                "runs_count": st.runs_count,
                "governance": gov,
            }
            payload.append(row)
            rows.append(
                [
                    b.id,
                    b.kind,
                    "yes" if b.enabled else "no",
                    str(row["status"]),
                    _fmt(st.next_fire_at),
                    _fmt(st.last_fire_at),
                    st.last_run_id or NO_VALUE,
                    str(st.runs_count),
                    str(gov["limits"]),
                    f"{today['used']}/{today['max']}" if today else NO_VALUE,
                    str(gov["paused"]),
                ]
            )
    if any_schedule and not live:
        warnings.insert(0, NO_ENGINE_WARNING)
    for line in warnings:
        typer.echo(line, err=True)
    if as_json:
        _emit_json({"engine_live": live, "schedules": payload})
        return
    if not rows:
        typer.echo("No schedules.")
        return
    typer.echo(
        _table(
            [
                "ID",
                "KIND",
                "ENABLED",
                "STATUS",
                "NEXT FIRE",
                "LAST FIRE",
                "LAST RUN",
                "RUNS",
                "LIMITS",
                "TODAY",
                "PAUSED",
            ],
            rows,  # fmt: skip
        )
    )


# -- next --------------------------------------------------------------------------------------


def _upcoming(
    sched: Scheduler, binding: ResolvedBinding, st: ScheduleState, now: datetime, count: int
) -> list[datetime]:
    """The next *count* instants, collapsing a backlog the way the engine does (one due fire,
    not one per missed instant). Pure: reads *st*, writes nothing."""
    trigger = binding.to_trigger()
    anchor = st.last_scheduled_for or now
    nxt = sched.next_fire(trigger, anchor)
    steps = 0
    while nxt is not None and steps < MAX_CATCHUP_STEPS:
        following = sched.next_fire(trigger, nxt)
        if following is None or following > now:
            break
        nxt, steps = following, steps + 1
    out: list[datetime] = []
    while nxt is not None and len(out) < count:
        out.append(nxt)
        nxt = sched.next_fire(trigger, nxt)
    return out


def _would_skip(
    b: ResolvedBinding, at: datetime, root: str, ledger: Any
) -> tuple[str, dict[str, Any]] | None:
    """``(text, detail)`` when the pre-fire gate would suppress the fire at *at*, else ``None``."""
    if b.limits is None:
        return None
    result = gate(
        b,
        b.limits,
        at,
        ledger=ledger,
        workspace_root=root,
        tz=ZoneInfo(b.timezone or "UTC"),
        week_start=b.week_start or "monday",
    )
    if result.admit:
        return None
    resets = result.detail.get("resets_at")
    text = f"would skip: {result.reason}" + (f" (resets {resets})" if resets else "")
    return text, {"reason": result.reason, **result.detail}


def _readonly_digest_store(state_dir: Path) -> Any:
    """A ``WatchDigestStore`` whose ``save`` is a no-op: a preview must not seed the baseline."""
    from ..service.watch import Digest, WatchDigestStore

    class _ReadOnly(WatchDigestStore):
        def save(self, workspace_slug: str, schedule_id: str, digest: Digest) -> None:
            return None

    return _ReadOnly(state_dir)


def _observe_event_kind(
    root: Path, b: ResolvedBinding, st: ScheduleState, now: datetime, sdir: Path
) -> datetime | None:
    """The instant of the pending event ``TriggerSource.observe`` reports for *b*, else ``None``.

    Read-only: ``observe`` never consumes (no ``commit`` here) and the file-watch digest store is
    wrapped so its baseline seeding is dropped. A source error reads as "nothing pending"."""
    from ..service.watch import FileWatchSource
    from ..service.webhook import WebhookSource

    try:
        source: Any
        if b.kind == "webhook":
            source = WebhookSource(sdir, str(root), b)
        elif b.kind == "file_watch" and b.watch is not None:
            source = FileWatchSource(str(root), b.watch, _readonly_digest_store(sdir))
        else:
            return None
        candidate = source.observe(b, st, now)
    except Exception:  # noqa: BLE001 - a preview must never fail on a broken source
        return None
    return candidate.scheduled_for if candidate is not None else None


def _pending_note(fire: tuple[datetime, str, str], pending: set[tuple[datetime, str, str]]) -> str:
    return "pending event" if fire in pending else ""


@app.command(name="next")
def next_cmd(
    count: Annotated[
        int, typer.Option("-n", min=1, help="How many upcoming fires.")
    ] = DEFAULT_NEXT_COUNT,
    workspace: WorkspaceOpt = None,
    all_workspaces: Annotated[
        bool, typer.Option("--all", help="Every registered workspace.")
    ] = False,
    as_json: JsonOpt = False,
    state_dir: StateDirOpt = None,
) -> None:
    """Upcoming fires, soonest first. Event kinds (file_watch/webhook) have no predicted time; one
    shows up only when its source already observes a pending event (marked ``pending event``)."""
    now = _now()
    state = ScheduleStateStore(_state_dir(state_dir))
    scheds: dict[str, Scheduler] = {
        "cron": CronScheduler(lambda: now),
        "interval": IntervalScheduler(),
    }
    fires: list[tuple[datetime, str, str]] = []
    resolved: dict[tuple[str, str], ResolvedBinding] = {}
    skipped_event_kinds: list[str] = []
    pending_events: set[tuple[datetime, str, str]] = set()
    sdir = _state_dir(state_dir)
    for root in _selected_roots(workspace, all_workspaces):
        for b in _load_file(root).bindings:
            if not b.enabled:
                continue
            if b.kind not in PURE_KINDS:
                # F6: event kinds are observe-only and never committed here, so the preview
                # shows a pending event but can never consume it.
                st = state.get(root, b.id) or ScheduleState()
                if st.status in (STATUS_COMPLETED, STATUS_QUARANTINED):
                    continue
                seen = _observe_event_kind(root, b, st, now, sdir)
                if seen is None:
                    skipped_event_kinds.append(f"{b.id} ({b.kind})")
                else:
                    resolved[(str(root), b.id)] = b
                    fires.append((seen, str(root), b.id))
                    pending_events.add((seen, str(root), b.id))
                continue
            st = state.get(root, b.id) or ScheduleState()
            if st.status in (STATUS_COMPLETED, STATUS_QUARANTINED):
                continue
            resolved[(str(root), b.id)] = b
            fires += [(t, str(root), b.id) for t in _upcoming(scheds[b.kind], b, st, now, count)]
    fires.sort()
    fires = fires[:count]
    # Pure what-if: the same gate() the engine runs, against the current ledger (no reservations
    # are made and nothing is written, so a later fire's answer assumes the earlier ones are free).
    _, ledger = _readonly_ledger(_state_dir(state_dir))
    notes = [_would_skip(resolved[(w, i)], t, w, ledger) for t, w, i in fires]
    if as_json:
        _emit_json(
            [
                {
                    "at": _iso(t),
                    "workspace": w,
                    "id": i,
                    **(
                        {"pending_event": True, "kind": resolved[(w, i)].kind}
                        if f in pending_events
                        else {}
                    ),
                    **({"would_skip": n[1]} if n else {}),
                }
                for f, n in zip(fires, notes, strict=True)
                for t, w, i in [f]
            ]
        )
        return
    if skipped_event_kinds:
        typer.echo(
            "NOTE: no predicted time for event-driven schedules (no pending event): "
            + ", ".join(skipped_event_kinds),
            err=True,
        )
    if not fires:
        typer.echo("No upcoming fires.")
        return
    if any(notes) or pending_events:
        rows = [
            [
                _fmt(t),
                i,
                w,
                "; ".join(x for x in (_pending_note(f, pending_events), n[0] if n else "") if x),
            ]
            for f, n in zip(fires, notes, strict=True)
            for t, w, i in [f]
        ]
        typer.echo(_table(["NEXT FIRE", "ID", "WORKSPACE", "NOTE"], rows))
        return
    typer.echo(_table(["NEXT FIRE", "ID", "WORKSPACE"], [[_fmt(t), i, w] for t, w, i in fires]))


# -- history -----------------------------------------------------------------------------------


def _with_governance(event: dict[str, Any]) -> dict[str, Any]:
    gov = governance_fields(event)
    return event if gov is None else {**event, "governance": gov}


@app.command(name="history")
def history_cmd(
    schedule_id: Annotated[str | None, typer.Argument(metavar="[ID]")] = None,
    count: Annotated[
        int, typer.Option("-n", min=1, help="How many events.")
    ] = DEFAULT_HISTORY_COUNT,
    workspace: WorkspaceOpt = None,
    all_workspaces: Annotated[
        bool, typer.Option("--all", help="Every workspace in the event log.")
    ] = False,
    as_json: JsonOpt = False,
    state_dir: StateDirOpt = None,
) -> None:
    """Event log, newest first -- including suppressed fires and their reason."""
    log = ScheduleEventLog(_state_dir(state_dir))
    root = None if all_workspaces else _workspace_root(workspace)
    events = log.read(limit=count, schedule_id=schedule_id, workspace_root=root)
    if as_json:
        _emit_json([_with_governance(e) for e in events])
        return
    if not events:
        typer.echo("No events.")
        return
    rows = [
        [
            str(e.get("ts") or NO_VALUE),
            str(e.get("event") or NO_VALUE),
            str(e.get("schedule_id") or NO_VALUE),
            reason_with_period(e),
            str(e.get("run_id") or e.get("detail") or NO_VALUE),
        ]
        for e in events
    ]
    typer.echo(_table(["TIME", "EVENT", "SCHEDULE", "REASON", "RUN / DETAIL"], rows))


# -- validate ----------------------------------------------------------------------------------


def unbound_trigger_warnings(
    root: Path, workflow_path: str | None, triggers: list[Any]
) -> list[str]:
    """ADR-0014 D2: a workflow spec declaring a non-manual trigger that no binding in *root*'s
    ``.ao/schedules.yaml`` references. A warning, never an error."""
    declared = [t.type for t in triggers if t.type != "manual"]
    if not declared:
        return []
    bound = False
    if workflow_path:
        target = Path(workflow_path).resolve()
        try:
            bindings = ScheduleStore(root).load().schedules
        except ScheduleFileError:
            bindings = []
        bound = any(b.workflow and (root / b.workflow).resolve() == target for b in bindings)
    if bound:
        return []
    name = workflow_path or "workflow"
    return [
        f"{name} declares {', '.join(sorted(set(declared)))} trigger(s) but no schedule binds "
        f"it; it will never fire on its own. Add one with `ao schedule add --from-workflow`."
    ]


def governance_warnings(root: Path, bindings: list[ResolvedBinding]) -> list[str]:
    """L4/L7: limit combinations that are valid but almost certainly not what the owner meant."""
    out: list[str] = []
    for b in bindings:
        cost = b.limits.cost if b.limits is not None else None
        per_run = cost.per_run_usd if cost is not None else None
        if per_run is None:
            if b.overlap == "allow":
                out.append(
                    f"schedule {b.id!r}: overlap: allow without limits.cost.per_run_usd -- with "
                    "period-only caps one run may take the whole remaining period budget as its "
                    "cap, and allow serializes in practice; set per_run_usd"
                )
            continue
        budget = _sentinel_llm_budget(root, b)
        if budget is not None and budget >= per_run:
            out.append(
                f"schedule {b.id!r}: sentinel.llm.budget_usd ({budget:g}) >= per_run_usd "
                f"({per_run:g}) -- generation spend counts toward the run cap, so the LLM lane "
                "can use up the whole cap"
            )
    return out


def _sentinel_llm_budget(root: Path, b: ResolvedBinding) -> float | None:
    """The binding workflow spec's ``sentinel.llm.budget_usd`` (``None`` if absent/unreadable)."""
    if b.workflow is None:
        return None
    from ..errors import OrchestratorError
    from ..spec import load_workflow

    try:
        wf = load_workflow(str(root / b.workflow))
    except (OrchestratorError, OSError):
        return None
    llm = wf.sentinel.llm if wf.sentinel is not None else None
    return llm.budget_usd if llm is not None else None


def validate_workspace_schedules(workflow_path: str | None, workflow: Any) -> None:
    """The ``ao validate`` hook: validate the workflow workspace's ``.ao/schedules.yaml``
    (errors exit 1) and warn about an unbound non-manual trigger of *workflow*."""
    # W2: the workflow's own workspace, not the cwd's (cwd only when no path is given)
    root = (
        _find_workspace(Path(workflow_path).resolve().parent)
        if workflow_path
        else _workspace_root(None)
    )
    try:
        ScheduleStore(root).load()
    except ScheduleFileError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1) from exc
    for line in unbound_trigger_warnings(root, workflow_path, workflow.triggers):
        typer.echo(f"WARNING: {line}", err=True)


@app.command(name="validate")
def validate_cmd(
    workspace: WorkspaceOpt = None,
    workflow: Annotated[
        list[str] | None,
        typer.Option("--workflow", help="Also warn when this spec has an unbound trigger."),
    ] = None,
) -> None:
    """Validate ``.ao/schedules.yaml`` (exit 0/1); warn about unbound workflow triggers."""
    root = _workspace_root(workspace)
    try:
        file = ScheduleStore(root).load()
    except ScheduleFileError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1) from exc
    if workflow:
        from ..errors import OrchestratorError
        from ..spec import load_workflow

        for path in workflow:
            try:
                wf = load_workflow(path)
            except (OrchestratorError, OSError) as exc:
                raise _fail(f"{path}: {exc}") from exc
            for line in unbound_trigger_warnings(root, path, wf.triggers):
                typer.echo(f"WARNING: {line}", err=True)
    for line in governance_warnings(root, file.bindings):
        typer.echo(f"WARNING: {line}", err=True)
    typer.echo(f"OK: {len(file.schedules)} schedule(s) valid in {ScheduleStore(root).path}")


# -- add ---------------------------------------------------------------------------------------


def _coerce_run_arg(name: str, raw: str) -> object:
    """CLI strings -> the strict types ``RunArgs`` demands (registry-declared option type)."""
    opt = get_option(name)
    try:
        if opt.type == "int":
            return int(raw)
        if opt.type == "float":
            return float(raw)
        if opt.type == "bool":
            lowered = raw.strip().lower()
            if lowered not in ("true", "false"):
                raise ValueError("expected true or false")
            return lowered == "true"
    except ValueError as exc:
        raise _fail(f"--run-arg {name}={raw!r}: {exc}", EXIT_USAGE) from exc
    return raw


def _parse_kv(raw: list[str], flag: str) -> dict[str, str]:
    from ..cli import _parse_param_options  # lazy: ao.cli imports this module

    try:
        return _parse_param_options(raw)
    except ValueError as exc:
        raise _fail(str(exc).replace("--param", flag), EXIT_USAGE) from exc


def _run_args(raw: list[str]) -> dict[str, object]:
    parsed = _parse_kv(raw, "--run-arg")
    unknown = sorted(set(parsed) - set(SCHEDULE_RUN_ARG_NAMES))
    if unknown:
        raise _fail(
            f"unknown --run-arg key(s) {unknown}; allowed: {', '.join(SCHEDULE_RUN_ARG_NAMES)}",
            EXIT_USAGE,
        )
    return {k: _coerce_run_arg(k, v) for k, v in parsed.items()}


def _period_value(raw: str, flag: str, *, money: bool) -> tuple[Period, float]:
    """``PERIOD=N`` (``PERIOD=USD`` when *money*) -> (period, number); exit 2 names the token."""
    period, sep, num = raw.partition("=")
    period = period.strip()
    shape = "PERIOD=USD (e.g. week=20)" if money else "PERIOD=MAX_RUNS (e.g. day=3)"
    if not sep or period not in PERIODS:
        raise _fail(
            f"{flag} must be {shape} with PERIOD in {'/'.join(PERIODS)}, got {raw!r}", EXIT_USAGE
        )
    try:
        value = float(num) if money else int(num)
    except ValueError:
        raise _fail(f"{flag} must be {shape}, got {raw!r}", EXIT_USAGE) from None
    if not value > 0 or value == float("inf"):
        raise _fail(f"{flag} value must be a positive number, got {raw!r}", EXIT_USAGE)
    return period, value  # type: ignore[return-value]


def _limits(
    quotas: list[str],
    per_run_usd: float | None,
    *,
    cost_caps: list[str] | None = None,
    on_cap: str | None = None,
    pause_ttl: int | None = None,
) -> dict[str, Any] | None:
    """The HLD §6.1 ``limits`` block from the limit flags (``None`` when no flag was given)."""
    limits: dict[str, Any] = {}
    if quotas:
        limits["quotas"] = [
            {"period": p, "max_runs": int(n)}
            for p, n in (_period_value(q, "--quota", money=False) for q in quotas)
        ]
    caps = [_period_value(c, "--cost-cap", money=True) for c in cost_caps or []]
    if per_run_usd is not None or caps:
        cost: dict[str, Any] = {}
        if per_run_usd is not None:
            cost["per_run_usd"] = per_run_usd
        if on_cap is not None:
            cost["on_cap"] = on_cap
        if pause_ttl is not None:
            cost["pause_ttl_seconds"] = pause_ttl
        if caps:
            cost["periods"] = [{"period": p, "max_usd": v} for p, v in caps]
        limits["cost"] = cost
    elif on_cap is not None or pause_ttl is not None:
        raise _fail("--on-cap/--pause-ttl need --cost-per-run or --cost-cap", EXIT_USAGE)
    return limits or None


def _check_on_cap(on_cap: str | None) -> None:
    if on_cap is not None and on_cap not in ("pause", "abort"):
        raise _fail(f"--on-cap must be pause or abort, got {on_cap!r}", EXIT_USAGE)


def _check_week_start(week_start: str | None) -> None:
    if week_start is not None and week_start not in ("monday", "sunday"):
        raise _fail(f"--week-start must be monday or sunday, got {week_start!r}", EXIT_USAGE)


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_prompt(root: Path, schedule_id: str, text: str, force: bool) -> str:
    """Write ``.ao/schedule-prompts/<id>.md``; refuse to replace a different file without
    ``--force``. Returns the workspace-relative path."""
    rel = PROMPTS_RELDIR / f"{schedule_id}.md"
    path = root / rel
    data = text.encode("utf-8")
    if len(data) > MAX_PROMPT_BYTES:
        raise _fail(f"--prompt is {len(data)} bytes; the limit is {MAX_PROMPT_BYTES}", EXIT_USAGE)
    if path.exists() and not force and path.read_bytes() != data:
        raise _fail(f"{rel} already exists with different content (use --force)", EXIT_USAGE)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, data)
    return str(rel)


def _until_equals(raw: str) -> bool | int | str:
    """``--until-equals`` text -> gate value (``true``/``false`` -> bool, digits -> int)."""
    lowered = raw.strip().lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    stripped = raw.strip()
    if stripped.lstrip("-").isdigit():
        return int(stripped)
    return raw


def _until(
    artifact: str | None,
    gate: str | None,
    field: str | None,
    equals: str | None,
    max_runs: int | None,
) -> dict[str, Any] | None:
    """Build the ``until`` block. An ``until`` is always bounded: with an artifact/gate condition
    but no ``--max-runs`` the CLI supplies ``DEFAULT_UNTIL_MAX_RUNS`` and says so (HLD §6.8)."""
    if (field is not None or equals is not None) and gate is None:
        raise _fail("--until-field/--until-equals need --until-gate", EXIT_USAGE)
    out: dict[str, Any] = {}
    if artifact is not None:
        out["artifact_exists"] = artifact
    if gate is not None:
        out["gate_file"] = gate
        if field is not None:
            out["gate_field"] = field
        if equals is not None:
            out["gate_equals"] = _until_equals(equals)
    if max_runs is not None:
        out["max_runs"] = max_runs
    elif out:
        out["max_runs"] = DEFAULT_UNTIL_MAX_RUNS
        typer.echo(
            f"No --max-runs given: bounding the until loop at {DEFAULT_UNTIL_MAX_RUNS} runs."
        )
    return out or None


def _slug(text: str) -> str:
    out = "".join(c if c.isalnum() or c in "-_" else "-" for c in text.lower()).strip("-_")
    return out or DEFAULT_ID_PREFIX


def _from_workflow(
    path: str, schedule_id: str | None, root: Path
) -> list[tuple[str, dict[str, Any]]]:
    """One ``(id, trigger-fields)`` per cron/interval trigger the spec declares (AC8)."""
    from ..errors import OrchestratorError
    from ..spec import load_workflow

    try:
        wf = load_workflow(path)
    except (OrchestratorError, OSError) as exc:
        raise _fail(f"{path}: {exc}") from exc
    base = schedule_id or _slug(wf.id)
    timed = [t for t in wf.triggers if t.type in PURE_KINDS]
    out: list[tuple[str, dict[str, Any]]] = []
    for index, trig in enumerate(timed, start=1):
        sid = base if schedule_id and len(timed) == 1 else f"{base}-{index}"
        if trig.type == "cron":
            fields: dict[str, Any] = {"kind": "cron", "schedule": trig.schedule}
            if trig.timezone != "UTC":
                fields["timezone"] = trig.timezone
        else:
            fields = {"kind": "interval", "interval_seconds": trig.interval_seconds}
        out.append((sid, fields))
    return out


def _workspace_relative(root: Path, workflow: str) -> str:
    resolved = Path(workflow).expanduser()
    resolved = resolved if resolved.is_absolute() else Path.cwd() / resolved
    try:
        return str(resolved.resolve().relative_to(root))
    except ValueError:
        return workflow  # let ScheduleBinding validation name the offending path


@app.command(name="add")
def add_cmd(
    schedule_id: Annotated[str | None, typer.Argument(metavar="[ID]")] = None,
    cron: Annotated[str | None, typer.Option("--cron", help="5-field cron expression.")] = None,
    interval: Annotated[
        int | None, typer.Option("--interval", help="Fixed period in seconds (>= 60).")
    ] = None,
    workflow: Annotated[
        str | None, typer.Option("--workflow", help="Workflow spec path (workspace-relative).")
    ] = None,
    template: Annotated[
        str | None, typer.Option("--template", help="Workflow template name.")
    ] = None,
    param: Annotated[
        list[str] | None, typer.Option("--param", help="Template param k=v (repeatable).")
    ] = None,
    instance_id: Annotated[
        str | None, typer.Option("--instance-id", help="Instance id pattern ({date}, {rand6}).")
    ] = None,
    tz: Annotated[str | None, typer.Option("--tz", help="IANA timezone for cron.")] = None,
    prompt_file: Annotated[str | None, typer.Option("--prompt-file")] = None,
    overlap: Annotated[str | None, typer.Option("--overlap", help="skip | queue | allow")] = None,
    catch_up: Annotated[str | None, typer.Option("--catch-up", help="once | skip")] = None,
    catch_up_window: Annotated[
        int | None, typer.Option("--catch-up-window", help="Seconds.")
    ] = None,
    jitter: Annotated[int | None, typer.Option("--jitter", help="Max jitter seconds.")] = None,
    max_concurrent: Annotated[int | None, typer.Option("--max-concurrent")] = None,
    run_arg: Annotated[
        list[str] | None, typer.Option("--run-arg", help="Run option k=v (repeatable).")
    ] = None,
    quota: Annotated[
        list[str] | None,
        typer.Option("--quota", help="Governance quota PERIOD=MAX_RUNS (repeatable)."),
    ] = None,
    per_run_usd: Annotated[
        float | None,
        typer.Option("--cost-per-run", "--per-run-usd", help="Per-run cost cap (USD)."),
    ] = None,
    cost_cap: Annotated[
        list[str] | None,
        typer.Option("--cost-cap", help="Period cost cap PERIOD=USD (repeatable)."),
    ] = None,
    on_cap: Annotated[
        str | None,
        typer.Option("--on-cap", help="pause (default) | abort when a run hits its cap."),
    ] = None,
    pause_ttl: Annotated[
        int | None, typer.Option("--pause-ttl", min=0, help="Seconds a paused run stays resumable.")
    ] = None,
    week_start: Annotated[str | None, typer.Option("--week-start", help="monday | sunday")] = None,
    prompt: Annotated[
        str | None,
        typer.Option("--prompt", help="Fixed prompt text -> .ao/schedule-prompts/ID.md."),
    ] = None,
    pin_prompt: Annotated[
        bool, typer.Option("--pin-prompt", help="Store the prompt file's current sha256.")
    ] = False,
    until_artifact: Annotated[
        str | None, typer.Option("--until-artifact", help="Stop once this path exists.")
    ] = None,
    until_gate: Annotated[
        str | None, typer.Option("--until-gate", help="Stop when this JSON gate file says so.")
    ] = None,
    until_field: Annotated[
        str | None, typer.Option("--until-field", help="Gate JSON field (default: done).")
    ] = None,
    until_equals: Annotated[
        str | None, typer.Option("--until-equals", help="Gate value (default: true).")
    ] = None,
    max_runs: Annotated[
        int | None, typer.Option("--max-runs", help="Stop after N fires (until-loop bound).")
    ] = None,
    webhook: Annotated[
        bool, typer.Option("--webhook", help="Fire on an authenticated webhook delivery.")
    ] = False,
    secret_env: Annotated[
        str | None, typer.Option("--secret-env", help="Webhook HMAC key: env var NAME.")
    ] = None,
    secret_file: Annotated[
        str | None,
        typer.Option("--secret-file", help="Webhook HMAC key: workspace-relative file (0600)."),
    ] = None,
    rate_per_minute: Annotated[
        int | None, typer.Option("--rate-per-minute", min=1, help="Webhook accept rate.")
    ] = None,
    webhook_scheme: Annotated[
        str | None,
        typer.Option("--webhook-scheme", help="ao (X-AO-Signature, default) | github"),
    ] = None,
    watch: Annotated[
        list[str] | None,
        typer.Option("--watch", help="Fire on file changes: workspace-relative glob (repeatable)."),
    ] = None,
    watch_on: Annotated[
        list[str] | None,
        typer.Option("--watch-on", help="created | modified (repeatable; default both)."),
    ] = None,
    debounce: Annotated[
        int | None, typer.Option("--debounce", min=0, help="Watch quiet period, seconds.")
    ] = None,
    poll_seconds: Annotated[
        int | None, typer.Option("--poll-seconds", min=1, help="Watch scan period, seconds.")
    ] = None,
    max_files: Annotated[
        int | None, typer.Option("--max-files", min=1, help="Watch scan bound (files).")
    ] = None,
    disabled: Annotated[bool, typer.Option("--disabled", help="Create it disabled.")] = False,
    from_workflow: Annotated[
        str | None,
        typer.Option(
            "--from-workflow", help="Derive bindings from a spec's cron/interval triggers."
        ),
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="Replace an existing id.")] = False,
    workspace: WorkspaceOpt = None,
) -> None:
    """Add a schedule binding to ``.ao/schedules.yaml``."""
    root = _workspace_root(workspace)
    common: dict[str, Any] = {}
    if tz:
        common["timezone"] = tz
    for key, value in (
        ("prompt_file", prompt_file),
        ("overlap", overlap),
        ("catch_up", catch_up),
        ("catch_up_window_seconds", catch_up_window),
        ("jitter_seconds", jitter),
        ("max_concurrent", max_concurrent),
        ("instance_id", instance_id),
    ):
        if value is not None:
            common[key] = value
    if template:
        common["template"] = template
        if param:
            common["params"] = _parse_kv(param, "--param")
    if disabled:
        common["enabled"] = False
    run_args = _run_args(run_arg or [])
    if run_args:
        common["run_args"] = run_args
    _check_on_cap(on_cap)
    _check_week_start(week_start)
    limits = _limits(
        quota or [], per_run_usd, cost_caps=cost_cap, on_cap=on_cap, pause_ttl=pause_ttl
    )
    if limits:
        common["limits"] = limits
    if week_start is not None:
        common["week_start"] = week_start
    if prompt is not None and prompt_file is not None:
        raise _fail("--prompt and --prompt-file are mutually exclusive", EXIT_USAGE)
    if pin_prompt and prompt is None and prompt_file is None:
        raise _fail("--pin-prompt needs --prompt or --prompt-file", EXIT_USAGE)
    if prompt is not None and from_workflow:
        raise _fail("--prompt needs an explicit ID (not --from-workflow)", EXIT_USAGE)
    # D-2 up front, so a refusal writes nothing (not even a prompt file).
    if not limits and _load_file(root).defaults.limits is None:
        raise _fail(
            f"{LIMITS_REQUIRED.format(id=schedule_id or from_workflow)}; {LIMITS_HINT}", EXIT_USAGE
        )

    until = _until(until_artifact, until_gate, until_field, until_equals, max_runs)
    if until:
        common["until"] = until

    new: list[tuple[str, dict[str, Any]]]
    if from_workflow:
        if cron is not None or interval is not None:
            raise _fail("--from-workflow derives the trigger; drop --cron/--interval", EXIT_USAGE)
        if workflow or template:
            raise _fail("--from-workflow already names the workflow", EXIT_USAGE)
        derived = _from_workflow(from_workflow, schedule_id, root)
        if not derived:
            typer.echo(f"{from_workflow} declares no cron/interval trigger; nothing to add.")
            return
        common["workflow"] = _workspace_relative(root, from_workflow)
        new = [(sid, {**fields, **common}) for sid, fields in derived]
    else:
        if schedule_id is None:
            raise _fail("ID is required (unless --from-workflow)", EXIT_USAGE)
        webhook_opts = (secret_env, secret_file, rate_per_minute, webhook_scheme)
        watch_opts = (watch_on, debounce, poll_seconds, max_files)
        if watch and webhook:
            raise _fail("give one trigger: --watch or --webhook", EXIT_USAGE)
        if watch:
            if cron is not None or interval is not None:
                raise _fail("--watch is the trigger; drop --cron/--interval", EXIT_USAGE)
        elif any(o is not None for o in watch_opts):
            raise _fail("--watch-on/--debounce/--poll-seconds/--max-files need --watch", EXIT_USAGE)
        if webhook:
            if cron is not None or interval is not None:
                raise _fail("--webhook is the trigger; drop --cron/--interval", EXIT_USAGE)
        elif any(o is not None for o in webhook_opts):
            raise _fail(
                "--secret-env/--secret-file/--rate-per-minute/--webhook-scheme need --webhook",
                EXIT_USAGE,
            )
        elif not watch and (cron is None) == (interval is None):
            raise _fail(
                "give exactly one of --cron / --interval (or --webhook / --watch)", EXIT_USAGE
            )
        if (workflow is None) == (template is None):
            raise _fail("give exactly one of --workflow / --template", EXIT_USAGE)
        fields: dict[str, Any]
        if webhook:
            if (secret_env is None) == (secret_file is None):
                raise _fail(
                    "--webhook needs exactly one of --secret-env / --secret-file", EXIT_USAGE
                )
            spec = {
                k: v
                for k, v in (
                    ("secret_env", secret_env),
                    ("secret_file", secret_file),
                    ("rate_per_minute", rate_per_minute),
                    ("scheme", webhook_scheme),
                )
                if v is not None
            }
            fields = {"kind": "webhook", "webhook": spec}
        elif watch:
            bad = [e for e in watch_on or [] if e not in ("created", "modified")]
            if bad:
                raise _fail(f"--watch-on: {bad[0]!r} is not created|modified", EXIT_USAGE)
            wspec: dict[str, Any] = {"paths": list(watch)}
            for wkey, wval in (
                ("on", list(dict.fromkeys(watch_on)) if watch_on else None),
                ("debounce_seconds", debounce),
                ("poll_seconds", poll_seconds),
                ("max_files", max_files),
            ):
                if wval is not None:
                    wspec[wkey] = wval
            fields = {"kind": "file_watch", "watch": wspec}
        elif cron is not None:
            fields = {"kind": "cron", "schedule": cron}
        else:
            fields = {"kind": "interval", "interval_seconds": interval}
        if workflow:
            common["workflow"] = workflow
        new = [(schedule_id, {**fields, **common})]

    if prompt is not None:
        assert schedule_id is not None  # guaranteed by the non-from-workflow branch
        rel = _write_prompt(root, schedule_id, prompt, force)
        new = [(sid, {**f, "prompt_file": rel}) for sid, f in new]
    if pin_prompt:
        pinned = root / str(new[0][1]["prompt_file"])
        if not pinned.is_file():
            raise _fail(f"--pin-prompt: {pinned} is not a file", EXIT_USAGE)
        new = [(sid, {**f, "prompt_sha256": _sha256_of(pinned)}) for sid, f in new]

    store = ScheduleStore(root)

    def apply(current: ScheduleFile) -> ScheduleFile:
        data = current.to_data()
        rows = data.setdefault("schedules", [])
        for sid, fields in new:
            existing = [i for i, r in enumerate(rows) if r.get("id") == sid]
            if existing and not force:
                raise ScheduleFileError(
                    f"schedule {sid!r} already exists (use --force to replace)",
                    schedule_id=sid,
                    field="id",
                )
            for i in reversed(existing):
                del rows[i]
            rows.append({"id": sid, **fields})
        return ScheduleFile.from_data(data, source=str(store.path))

    try:
        store.mutate(apply)
    except ScheduleFileError as exc:
        if exc.field == "limits":
            raise _fail(f"{exc} ({LIMITS_HINT})", EXIT_USAGE) from exc
        raise _fail(str(exc)) from exc
    for sid, _fields in new:
        typer.echo(f"Added schedule {sid!r} to {store.path}")


# -- set-limits / pin-prompt ------------------------------------------------------------------


def _mutate_binding(root: Path, schedule_id: str, edit: Callable[[dict[str, Any]], None]) -> None:
    """Apply *edit* to one binding's raw row via the locked ``mutate()``; the whole file is
    re-validated, so an edit that breaks D-2 or a limit rule is refused and nothing is written."""
    store = ScheduleStore(root)

    def apply(current: ScheduleFile) -> ScheduleFile:
        data = current.to_data()
        for row in data.get("schedules", []):
            if row.get("id") == schedule_id:
                edit(row)
        return ScheduleFile.from_data(data, source=str(store.path))

    try:
        store.mutate(apply)
    except ScheduleFileError as exc:
        raise _fail(str(exc), EXIT_USAGE if exc.field in ("limits", None) else 1) from exc


def _merge_by_period(base: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = {e["period"]: e for e in base}
    merged.update({e["period"]: e for e in new})
    return list(merged.values())


@app.command(name="set-limits")
def set_limits_cmd(
    schedule_id: Annotated[str, typer.Argument(metavar="ID")],
    quota: Annotated[
        list[str] | None, typer.Option("--quota", help="Set quota PERIOD=MAX_RUNS (repeatable).")
    ] = None,
    per_run_usd: Annotated[
        float | None, typer.Option("--cost-per-run", help="Set the per-run cost cap (USD).")
    ] = None,
    cost_cap: Annotated[
        list[str] | None, typer.Option("--cost-cap", help="Set period cap PERIOD=USD (repeatable).")
    ] = None,
    on_cap: Annotated[str | None, typer.Option("--on-cap", help="pause | abort")] = None,
    pause_ttl: Annotated[int | None, typer.Option("--pause-ttl", min=0)] = None,
    week_start: Annotated[str | None, typer.Option("--week-start", help="monday | sunday")] = None,
    clear_quotas: Annotated[
        bool, typer.Option("--clear-quotas", help="Drop every quota first.")
    ] = False,
    clear_cost: Annotated[
        bool, typer.Option("--clear-cost", help="Drop the cost block first.")
    ] = False,
    workspace: WorkspaceOpt = None,
) -> None:
    """Rewrite only a schedule's ``limits`` (flags merge by period; ``--clear-*`` drop first)."""
    root = _workspace_root(workspace)
    _check_on_cap(on_cap)
    _check_week_start(week_start)
    file = _load_file(root)
    binding = next((b for b in file.bindings if b.id == schedule_id), None)
    if binding is None:
        raise _fail(f"no such schedule {schedule_id!r} in {ScheduleStore(root).path}")
    given = _limits(
        quota or [], per_run_usd, cost_caps=cost_cap, on_cap=on_cap, pause_ttl=pause_ttl
    )
    if given is None and not (clear_quotas or clear_cost or week_start):
        raise _fail("nothing to change: give a limit flag, --clear-quotas/--clear-cost", EXIT_USAGE)
    base = binding.limits.model_dump(mode="json", exclude_none=True) if binding.limits else {}
    if clear_quotas:
        base.pop("quotas", None)
    if clear_cost:
        base.pop("cost", None)
    if given is not None:
        if "quotas" in given:
            base["quotas"] = _merge_by_period(base.get("quotas", []), given["quotas"])
        if "cost" in given:
            cost = {
                **base.get("cost", {}),
                **{k: v for k, v in given["cost"].items() if k != "periods"},
            }
            periods = _merge_by_period(
                base.get("cost", {}).get("periods", []), given["cost"].get("periods", [])
            )
            if periods:
                cost["periods"] = periods
            base["cost"] = cost
    if not LimitsSpec.model_validate(base).declares_any_limit():
        raise _fail(f"{LIMITS_REQUIRED.format(id=schedule_id)}; {LIMITS_HINT}", EXIT_USAGE)

    def edit(row: dict[str, Any]) -> None:
        row["limits"] = base
        if week_start is not None:
            row["week_start"] = week_start

    _mutate_binding(root, schedule_id, edit)
    typer.echo(
        f"Updated limits of {schedule_id!r}: {compact_limits(LimitsSpec.model_validate(base))}"
    )


@app.command(name="pin-prompt")
def pin_prompt_cmd(
    schedule_id: Annotated[str, typer.Argument(metavar="ID")], workspace: WorkspaceOpt = None
) -> None:
    """Record the prompt file's current sha256 as the pin (a later edit then skips fires)."""
    root = _workspace_root(workspace)
    binding = next((b for b in _load_file(root).bindings if b.id == schedule_id), None)
    if binding is None:
        raise _fail(f"no such schedule {schedule_id!r} in {ScheduleStore(root).path}")
    if binding.prompt_file is None:
        raise _fail(f"schedule {schedule_id!r} has no prompt_file; nothing to pin", EXIT_USAGE)
    path = root / binding.prompt_file
    if not path.is_file():
        raise _fail(f"prompt file {binding.prompt_file} does not exist", EXIT_USAGE)
    sha = _sha256_of(path)
    _mutate_binding(root, schedule_id, lambda row: row.__setitem__("prompt_sha256", sha))
    typer.echo(f"Pinned {schedule_id!r} prompt {binding.prompt_file} sha256:{sha[:12]}")


# -- usage / resume ----------------------------------------------------------------------------


def _readonly_ledger(sdir: Path) -> tuple[Any, Any]:
    """(fires, ledger) that never write back: for views and the ``next`` projection."""
    from ..service.usage_ledger import UsageLedger

    fires = readonly_fire_store(sdir)
    return fires, UsageLedger(fires, clock=_now)


def _parse_at(raw: str | None) -> datetime:
    if raw is None:
        return _now()
    try:
        at = datetime.fromisoformat(raw)
    except ValueError:
        raise _fail(f"--at must be an ISO 8601 instant, got {raw!r}", EXIT_USAGE) from None
    return at if at.tzinfo is not None else at.replace(tzinfo=UTC)  # naive == UTC


@app.command(name="usage")
def usage_cmd(
    schedule_id: Annotated[str | None, typer.Argument(metavar="[ID]")] = None,
    at: Annotated[
        str | None, typer.Option("--at", help="Evaluate windows at this ISO 8601 instant.")
    ] = None,
    workspace: WorkspaceOpt = None,
    as_json: JsonOpt = False,
    state_dir: StateDirOpt = None,
) -> None:
    """Window usage, spend, paused runs and recent governance events (read-only)."""
    root, sdir = _workspace_root(workspace), _state_dir(state_dir)
    instant = _parse_at(at)
    bindings = _load_file(root).bindings
    if schedule_id is not None:
        bindings = [b for b in bindings if b.id == schedule_id]
        if not bindings:
            raise _fail(f"no such schedule {schedule_id!r} in {ScheduleStore(root).path}")
    fires, ledger = _readonly_ledger(sdir)
    doc = build_usage_doc(
        workspace=str(root),
        at=instant,
        generated_at=_now(),
        bindings=bindings,
        ledger=ledger,
        fires=fires,
        events=ScheduleEventLog(sdir, clock=_now),
    )
    if as_json:
        _emit_json(doc)
    elif doc["schedules"]:
        typer.echo(render_usage(doc))
    else:
        typer.echo("No schedules.")


def _refusal_detail(exc: Any, rec_expires: datetime | None) -> str:
    """The reason + the numbers an operator needs (expiry instant / remaining headroom)."""
    detail = str(exc)
    if exc.reason == "expired" and rec_expires is not None:
        detail += f" (expired at {rec_expires.isoformat()})"
    if exc.reason == "no_headroom" and exc.remaining_usd is not None:
        detail += f" (remaining ${exc.remaining_usd:.2f} in {exc.window})"
    return detail


@app.command(name="resume")
def resume_cmd(
    schedule_id: Annotated[str, typer.Argument(metavar="ID")],
    run: Annotated[str, typer.Option("--run", help="The cap-paused run id.")],
    add_usd: Annotated[
        float | None, typer.Option("--add-usd", help="Top-up (default: the per-run cap).")
    ] = None,
    local: Annotated[bool, typer.Option("--local", help="Drain in this process.")] = False,
    queue: Annotated[bool, typer.Option("--queue", help="Hand the request to a daemon.")] = False,
    workspace: WorkspaceOpt = None,
    state_dir: StateDirOpt = None,
    requested_by: Annotated[str, typer.Option("--requested-by")] = REQUESTED_BY_CLI,
) -> None:
    """Resume a run paused by its cost cap (exit 3 when refused: expired/aborted/no headroom)."""
    if local and queue:
        raise _fail("--local and --queue are mutually exclusive", EXIT_USAGE)
    from ..service.capped_resume import ResumeRefused, request_capped_resume
    from ..service.fire_store import FireIntentStore
    from ..service.usage_ledger import UsageLedger

    root, sdir = _workspace_root(workspace), _state_dir(state_dir)
    binding = next((b for b in _load_file(root).bindings if b.id == schedule_id), None)
    fires = FireIntentStore(sdir)
    ledger = UsageLedger(fires, clock=_now)

    def request(drain: Callable[[datetime], list[Any]] | None) -> Any:
        return request_capped_resume(
            str(root),
            schedule_id,
            run,
            add_usd,
            requested_by,
            state_dir=sdir,
            binding=binding,
            fires=fires,
            ledger=ledger,
            clock=_now,
            drain=drain,
        )

    try:
        if queue or (not local and probe_engine_lock(sdir)):
            req = request(None)
            typer.echo("queued; the daemon resumes it within one evaluation interval")
        else:
            engine = None
            try:
                engine = _build_engine(sdir, root)
                engine.start()
            except EngineLockHeldError:
                engine = None
            if engine is None:
                req = request(None)
                typer.echo("a daemon is evaluating; queued instead")
            else:
                try:
                    req = request(engine.drain_resume_requests)
                finally:
                    engine.shutdown()
    except ResumeRefused as exc:
        rec = fires.find_by_run_id(str(root), run)
        expires = rec.governance.pause_expires_at if rec and rec.governance else None
        typer.echo(
            f"ERROR: resume refused ({exc.reason}): {_refusal_detail(exc, expires)}", err=True
        )
        raise typer.Exit(EXIT_RESUME_REFUSED) from exc
    typer.echo(f"Resume of {run} accepted: +${req.add_usd:.2f}, new cap ${req.new_cap_usd:.2f}")


# -- remove / enable / disable ----------------------------------------------------------------


def _require_binding(root: Path, schedule_id: str) -> None:
    if not any(b.id == schedule_id for b in _load_file(root).schedules):
        raise _fail(f"no such schedule {schedule_id!r} in {ScheduleStore(root).path}")


def _set_enabled(root: Path, schedule_id: str, enabled: bool) -> None:
    def apply(current: ScheduleFile) -> ScheduleFile:
        data = current.to_data()
        for row in data.get("schedules", []):
            if row.get("id") == schedule_id:
                row["enabled"] = enabled
        return ScheduleFile.from_data(data, source=str(ScheduleStore(root).path))

    try:
        ScheduleStore(root).mutate(apply)
    except ScheduleFileError as exc:
        raise _fail(str(exc)) from exc


@app.command(name="remove")
def remove_cmd(
    schedule_id: Annotated[str, typer.Argument(metavar="ID")], workspace: WorkspaceOpt = None
) -> None:
    """Remove a schedule binding."""
    root = _workspace_root(workspace)
    _require_binding(root, schedule_id)

    def apply(current: ScheduleFile) -> ScheduleFile:
        data = current.to_data()
        data["schedules"] = [r for r in data.get("schedules", []) if r.get("id") != schedule_id]
        return ScheduleFile.from_data(data, source=str(ScheduleStore(root).path))

    try:
        ScheduleStore(root).mutate(apply)
    except ScheduleFileError as exc:
        raise _fail(str(exc)) from exc
    typer.echo(f"Removed schedule {schedule_id!r}")


@app.command(name="enable")
def enable_cmd(
    schedule_id: Annotated[str, typer.Argument(metavar="ID")],
    workspace: WorkspaceOpt = None,
    state_dir: StateDirOpt = None,
    requested_by: Annotated[str, typer.Option("--requested-by")] = REQUESTED_BY_CLI,
) -> None:
    """Enable a schedule. A ``completed`` one is restarted: ``runs_count`` and status reset."""
    root, sdir = _workspace_root(workspace), _state_dir(state_dir)
    _require_binding(root, schedule_id)
    state = ScheduleStateStore(sdir)
    st = state.get(root, schedule_id)
    restart = st is not None and st.status == STATUS_COMPLETED
    if restart:
        # state.json is single-writer (the engine caches it): only touch it while holding the
        # engine lock, otherwise the daemon's next save would silently undo the reset.
        with _hold_engine_lock(sdir) as held:
            if not held:
                raise _fail(
                    "a daemon is evaluating; cannot reset a completed schedule's state safely. "
                    "Stop the daemon and run `enable` again."
                )
            fresh = ScheduleStateStore(sdir)
            current = fresh.get(root, schedule_id)
            if current is not None:
                current.runs_count = 0
            fresh.set_status(root, schedule_id, STATUS_ACTIVE)
    _set_enabled(root, schedule_id, True)
    ScheduleEventLog(sdir, clock=_now).emit(
        EVENT_ENABLED,
        workspace=str(root),
        schedule_id=schedule_id,
        detail=f"requested_by={requested_by}" + (" restarted" if restart else ""),
    )
    typer.echo(
        f"Enabled schedule {schedule_id!r}" + (" (restarted: runs_count reset)" if restart else "")
    )


@app.command(name="disable")
def disable_cmd(
    schedule_id: Annotated[str, typer.Argument(metavar="ID")],
    workspace: WorkspaceOpt = None,
    state_dir: StateDirOpt = None,
    requested_by: Annotated[str, typer.Option("--requested-by")] = REQUESTED_BY_CLI,
) -> None:
    """Disable a schedule (kept in the file, never fires)."""
    root, sdir = _workspace_root(workspace), _state_dir(state_dir)
    _require_binding(root, schedule_id)
    _set_enabled(root, schedule_id, False)
    ScheduleEventLog(sdir, clock=_now).emit(
        EVENT_DISABLED,
        workspace=str(root),
        schedule_id=schedule_id,
        requested_by=requested_by,
        detail=f"requested_by={requested_by}",
    )
    typer.echo(f"Disabled schedule {schedule_id!r}")


# -- run-now -----------------------------------------------------------------------------------


def _queue_request(
    sdir: Path, root: Path, schedule_id: str, force: bool, requested_by: str
) -> Path:
    directory = sdir / SCHEDULES_SUBDIR / REQUESTS_SUBDIR
    path = directory / f"{uuid.uuid4().hex}.json"
    body = {
        "kind": REQUEST_KIND_RUN_NOW,
        "workspace_root": str(root),
        "schedule_id": schedule_id,
        "requested_at": _now().isoformat(),
        "force": force,
        "requested_by": requested_by,
    }
    atomic_write_bytes(path, json.dumps(body).encode("utf-8"))
    return path


def _run_local(
    sdir: Path, root: Path, binding: ResolvedBinding, force: bool, requested_by: str
) -> None:
    """Fire in-process through the engine's own evaluate + launcher, holding ``engine.lock``.
    Caller has already established the lock is free (EngineLockHeldError is handled there)."""
    engine = _build_engine(sdir, root)
    engine.start()  # takes engine.lock; raises EngineLockHeldError when an engine is evaluating
    try:
        entry = WorkspaceEntry(root=str(root), schedules=True)
        request = RunNowRequest(
            workspace_root=str(root),
            schedule_id=binding.id,
            requested_at=_now(),
            force=force,
            requested_by=requested_by,
        )
        decision = engine.evaluate(entry, binding, request.requested_at, run_now=request)
        if not decision.fire:
            typer.echo(f"not fired: {decision.reason}")
            return
        record = engine.fire(decision)
        if record is None:  # pragma: no cover - _build_engine always wires a launcher
            raise _fail("no launcher configured")
        if record.status != "launched":
            raise _fail(
                f"fire {record.fire_key} {record.status}: {record.reason or 'no reason recorded'}"
            )
        typer.echo(f"fired: run_id={record.run_id} fire_key={record.fire_key}")
    finally:
        engine.shutdown()


@app.command(name="run-now")
def run_now_cmd(
    schedule_id: Annotated[str, typer.Argument(metavar="ID")],
    force: Annotated[
        bool, typer.Option("--force", help="Bypass overlap only (never caps/until).")
    ] = False,
    local: Annotated[bool, typer.Option("--local", help="Fire in this process.")] = False,
    queue: Annotated[bool, typer.Option("--queue", help="Hand the request to a daemon.")] = False,
    workspace: WorkspaceOpt = None,
    state_dir: StateDirOpt = None,
    requested_by: Annotated[str, typer.Option("--requested-by")] = REQUESTED_BY_CLI,
) -> None:
    """Fire a schedule now: queued for a live daemon, else launched in-process."""
    if local and queue:
        raise _fail("--local and --queue are mutually exclusive", EXIT_USAGE)
    root, sdir = _workspace_root(workspace), _state_dir(state_dir)
    bindings = {b.id: b for b in _load_file(root).bindings}
    binding = bindings.get(schedule_id)
    if binding is None:
        raise _fail(f"no such schedule {schedule_id!r} in {ScheduleStore(root).path}")

    def queued(note: str | None = None) -> None:
        if note:
            typer.echo(note)
        _queue_request(sdir, root, schedule_id, force, requested_by)
        typer.echo("queued; will fire within one evaluation interval")
        inert = _inert_reason(_load_registry(), root)
        if inert:
            typer.echo(f"WARNING: the daemon will ignore this request: {inert}", err=True)
        if not _load_registry().schedules.enabled:
            typer.echo(
                "WARNING: schedules are disabled machine-wide (`schedules.enabled: false`); "
                "no engine will fire this request",
                err=True,
            )

    if queue or (not local and probe_engine_lock(sdir)):
        queued()
        return
    if not local:
        # H2: without --local the in-process path must do what a daemon would (ignore a
        # workspace that is unregistered / not opted in); an explicit --local is the opt-in.
        inert = _inert_reason(_load_registry(), root)
        if inert:
            raise _fail(f"{inert}; pass --local to fire it in this process anyway")
    try:
        _run_local(sdir, root, binding, force, requested_by)
    except EngineLockHeldError:
        queued("a daemon is evaluating; queued instead")


# -- daemon ------------------------------------------------------------------------------------


def _decision_line(d: FireDecision) -> str:
    return f"fire {d.workspace_root}/{d.schedule_id} reason={d.reason} key={d.fire_key}"


@app.command(name="daemon")
def daemon_cmd(
    workspace: WorkspaceOpt = None,
    once: Annotated[bool, typer.Option("--once", help="One tick, then exit.")] = False,
    interval: Annotated[
        float | None, typer.Option("--interval", min=0.1, help="Evaluation interval seconds.")
    ] = None,
    state_dir: StateDirOpt = None,
) -> None:
    """Run the schedule engine in the foreground (no systemd needed). Takes ``engine.lock``."""
    sdir = _state_dir(state_dir)
    scope = _workspace_root(workspace) if workspace else None
    engine = _build_engine(sdir, scope, interval=interval)
    try:
        engine.start()
    except EngineLockHeldError as exc:
        raise _fail(f"{exc}") from exc
    previous: dict[int, Any] = {}
    try:
        if once:
            decisions = engine.tick(_now())
            for d in decisions:
                typer.echo(_decision_line(d), err=True)
            typer.echo(str(len(decisions)))
            return
        stop = threading.Event()

        def _stop(signum: int, frame: FrameType | None) -> None:
            stop.set()

        previous = {sig: signal.signal(sig, _stop) for sig in (signal.SIGINT, signal.SIGTERM)}
        typer.echo(f"Schedule engine running (state: {sdir}); Ctrl-C to stop.", err=True)
        poll = min(interval, DAEMON_POLL_SECONDS) if interval else DAEMON_POLL_SECONDS
        while not stop.is_set():
            # `.wait()` returns the moment the signal handler sets the event (PEP 475 would
            # retry a plain sleep), bounding shutdown latency.
            if not stop.wait(poll):
                for d in engine.tick():
                    typer.echo(_decision_line(d), err=True)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        engine.shutdown()


__all__ = ["app", "unbound_trigger_warnings", "validate_workspace_schedules"]
