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
import json
import signal
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import IO, Annotated, Any

import typer

from ..run_options import get_option
from ..scheduler import CronScheduler, IntervalScheduler, Scheduler
from ..service.fire_store import (
    SCHEDULES_SUBDIR,
    ScheduleEventLog,
    ScheduleState,
    ScheduleStateStore,
)
from ..service.paths import default_registry_path, default_state_dir
from ..service.registry import ServiceRegistry, ServiceRegistryFile, WorkspaceEntry
from ..service.schedule_engine import (
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
from .models import SCHEDULE_RUN_ARG_NAMES, ResolvedBinding, ScheduleFile
from .store import ScheduleStore

app = typer.Typer(name="schedule", help="Declare, inspect and fire workspace schedules.")

# -- constants ---------------------------------------------------------------------------------
DEFAULT_NEXT_COUNT = 10
DEFAULT_HISTORY_COUNT = 50
DAEMON_POLL_SECONDS = 2.0  # upper bound on the foreground loop's wait (shutdown latency)
EXIT_USAGE = 2
REQUESTED_BY_CLI = "cli"
EVENT_ENABLED = "schedule.enabled"
EVENT_DISABLED = "schedule.disabled"
REASON_RUN_NOW = "run_now"
NO_VALUE = "-"
DEFAULT_ID_PREFIX = "wf"
WORKSPACE_MARKER_DIR = ".ao"
STATUS_COMPLETED = "completed"
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


def _build_engine(
    state_dir: Path, workspace: Path | None, *, interval: float | None = None
) -> ScheduleEngine:
    """Engine + launcher wired exactly like ``service.cli.build_schedule_engine`` (shared stores,
    shared per-workspace supervisor cache) but not started."""
    from ..service.schedule_launch import ScheduledLauncher

    class _Deferred:
        target: Any = None

        def launch(self, decision: FireDecision) -> Any:
            return self.target.launch(decision)

    def clock() -> datetime:
        return _now()

    kwargs: dict[str, Any] = {}
    if workspace is not None:
        root = str(workspace)
        kwargs["registry_factory"] = lambda _path: _SingleWorkspaceRegistry(root)
    if interval is not None:
        kwargs["eval_interval_seconds"] = interval
    if _SUPERVISOR_FACTORY is not None:
        kwargs["process_supervisor_factory"] = _SUPERVISOR_FACTORY
    deferred = _Deferred()
    engine = ScheduleEngine(
        default_registry_path(), state_dir=state_dir, clock=clock, launcher=deferred, **kwargs
    )
    deferred.target = ScheduledLauncher(
        state_dir,
        process_supervisor_factory=engine.process_supervisor,
        clock=clock,
        fire_store=engine.fire_store,
        state_store=engine.state_store,
        event_log=engine.event_log,
    )
    return engine


def _status_of(binding: ResolvedBinding, st: ScheduleState) -> str:
    return "disabled" if not binding.enabled and st.status == "active" else st.status


# -- list --------------------------------------------------------------------------------------


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
            row = {
                "workspace": str(root),
                "id": b.id,
                "kind": b.kind,
                "enabled": b.enabled,
                "status": _status_of(b, st),
                "next_fire": _iso(st.next_fire_at),
                "last_fire": _iso(st.last_fire_at),
                "last_run": st.last_run_id,
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
        _table(["ID", "KIND", "ENABLED", "STATUS", "NEXT FIRE", "LAST FIRE", "LAST RUN"], rows)
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


@app.command(name="next")
def next_cmd(
    count: Annotated[int, typer.Option("-n", help="How many upcoming fires.")] = DEFAULT_NEXT_COUNT,
    workspace: WorkspaceOpt = None,
    all_workspaces: Annotated[
        bool, typer.Option("--all", help="Every registered workspace.")
    ] = False,
    as_json: JsonOpt = False,
    state_dir: StateDirOpt = None,
) -> None:
    """Upcoming fires, soonest first (cron/interval only; event kinds have no predicted time)."""
    now = _now()
    state = ScheduleStateStore(_state_dir(state_dir))
    scheds: dict[str, Scheduler] = {
        "cron": CronScheduler(lambda: now),
        "interval": IntervalScheduler(),
    }
    fires: list[tuple[datetime, str, str]] = []
    skipped_event_kinds: list[str] = []
    for root in _selected_roots(workspace, all_workspaces):
        for b in _load_file(root).bindings:
            if not b.enabled:
                continue
            if b.kind not in PURE_KINDS:
                # F6: event kinds are observe-only and never committed here; with no source
                # constructed nothing can be consumed, so they are simply listed as unpredicted.
                skipped_event_kinds.append(f"{b.id} ({b.kind})")
                continue
            st = state.get(root, b.id) or ScheduleState()
            if st.status in ("completed", "quarantined"):
                continue
            fires += [(t, str(root), b.id) for t in _upcoming(scheds[b.kind], b, st, now, count)]
    fires.sort()
    fires = fires[:count]
    if as_json:
        _emit_json([{"at": _iso(t), "workspace": w, "id": i} for t, w, i in fires])
        return
    if skipped_event_kinds:
        typer.echo(
            "NOTE: no predicted time for event-driven schedules: " + ", ".join(skipped_event_kinds),
            err=True,
        )
    if not fires:
        typer.echo("No upcoming fires.")
        return
    typer.echo(_table(["NEXT FIRE", "ID", "WORKSPACE"], [[_fmt(t), i, w] for t, w, i in fires]))


# -- history -----------------------------------------------------------------------------------


@app.command(name="history")
def history_cmd(
    schedule_id: Annotated[str | None, typer.Argument(metavar="[ID]")] = None,
    count: Annotated[int, typer.Option("-n", help="How many events.")] = DEFAULT_HISTORY_COUNT,
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
        _emit_json(events)
        return
    if not events:
        typer.echo("No events.")
        return
    rows = [
        [
            str(e.get("ts") or NO_VALUE),
            str(e.get("event") or NO_VALUE),
            str(e.get("schedule_id") or NO_VALUE),
            str(e.get("reason") or NO_VALUE),
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


def validate_workspace_schedules(workflow_path: str | None, workflow: Any) -> None:
    """The ``ao validate`` hook: validate the cwd workspace's ``.ao/schedules.yaml`` (errors exit
    1) and warn about an unbound non-manual trigger of *workflow*."""
    root = _workspace_root(None)
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


def _limits(quotas: list[str], per_run_usd: float | None) -> dict[str, Any] | None:
    limits: dict[str, Any] = {}
    if quotas:
        entries = []
        for item in quotas:
            period, sep, runs = item.partition("=")
            if not sep or not runs.strip().isdigit():
                raise _fail(
                    f"--quota must be PERIOD=MAX_RUNS (e.g. day=4), got {item!r}", EXIT_USAGE
                )
            entries.append({"period": period.strip(), "max_runs": int(runs)})
        limits["quotas"] = entries
    if per_run_usd is not None:
        limits["cost"] = {"per_run_usd": per_run_usd}
    return limits or None


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
        float | None, typer.Option("--per-run-usd", help="Per-run cost cap.")
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
    limits = _limits(quota or [], per_run_usd)
    if limits:
        common["limits"] = limits

    new: list[tuple[str, dict[str, Any]]]
    if from_workflow:
        if cron or interval:
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
        if (cron is None) == (interval is None):
            raise _fail("give exactly one of --cron / --interval", EXIT_USAGE)
        if (workflow is None) == (template is None):
            raise _fail("give exactly one of --workflow / --template", EXIT_USAGE)
        fields = {"kind": "cron", "schedule": cron} if cron else {
            "kind": "interval", "interval_seconds": interval,
        }  # fmt: skip
        if workflow:
            common["workflow"] = workflow
        new = [(schedule_id, {**fields, **common})]

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
        hint = (
            " (pass --quota PERIOD=N and/or --per-run-usd, or set defaults.limits)"
            if exc.field == "limits"
            else ""
        )
        raise _fail(f"{exc}{hint}") from exc
    for sid, _fields in new:
        typer.echo(f"Added schedule {sid!r} to {store.path}")


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
                    "Stop it (or retry between ticks) and run `enable` again."
                )
            fresh = ScheduleStateStore(sdir)
            current = fresh.get(root, schedule_id)
            if current is not None:
                current.runs_count = 0
            fresh.set_status(root, schedule_id, "active")
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
        record = engine._launcher.launch(decision) if engine._launcher else None  # noqa: SLF001
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

    if queue or (not local and probe_engine_lock(sdir)):
        queued()
        return
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
        float | None, typer.Option("--interval", help="Evaluation interval seconds.")
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

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
        typer.echo(f"Schedule engine running (state: {sdir}); Ctrl-C to stop.", err=True)
        poll = min(interval, DAEMON_POLL_SECONDS) if interval else DAEMON_POLL_SECONDS
        while not stop.is_set():
            # `.wait()` returns the moment the signal handler sets the event (PEP 475 would
            # retry a plain sleep), bounding shutdown latency.
            if not stop.wait(poll):
                for d in engine.tick():
                    typer.echo(_decision_line(d), err=True)
    finally:
        engine.shutdown()


__all__ = ["app", "unbound_trigger_warnings", "validate_workspace_schedules"]
