"""Supervision of ``ao run`` / ``ao resume`` subprocesses launched from the dashboard.

Design
------
The dashboard does **not** run the engine in-process. A run is a long, expensive,
cancellable thing; hosting it inside the web server would mean a dashboard restart kills
in-flight work and a crashing run takes the UI down with it. Instead each run is a child
process invoking the same CLI an operator would type, which keeps exactly one execution
path through the engine (CLAUDE.md: "e2e tests should cover the full path... from as outer
a boundary as possible").

Children are started with ``start_new_session=True`` so each gets its own process group.
Cancel then signals the **group**, which reaches the `claude` subprocesses the engine
spawns — signalling only the direct child would leave those orphaned and still burning
tokens.

Launch records are persisted under ``<workspace>/.orchestrator/ui/launches/`` so cancel
still works after the dashboard itself restarts: the PID outlives the web server that
created it, and an in-memory-only registry would strand every running job.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path

from .runs import ORCHESTRATOR_DIR, RUNS_DIR, RunRepository

UI_DIR = "ui"
LAUNCHES_DIR = "launches"
LOGS_DIR = "logs"

# How long `launch_run` waits for the engine to create its run directory so the launch can
# be reported with a real run id. The engine writes state.json on its first save, which
# happens before the first task executes; a couple of seconds is generous. Exceeding it is
# NOT an error -- the record simply carries run_id=None and is reconciled later by
# `reconcile`, because a slow-starting run must never look like a failed launch.
RUN_ID_DISCOVERY_TIMEOUT_SECONDS = 10.0
RUN_ID_DISCOVERY_POLL_SECONDS = 0.05

# Launch lifecycle as the dashboard reports it (E-iafh2F launch-status; HLD §3). Derived on
# read from the persisted record + process liveness -- never persisted, so an old record
# written before this existed still classifies correctly.
STATUS_STARTING = "starting"  # alive, no run dir yet, still inside the discovery window
STATUS_STARTED = "started"  # a run directory is attributed to this launch
STATUS_FAILED_TO_START = "failed_to_start"  # process gone and no run dir ever appeared
STATUS_RUNNING_UNCONFIRMED = "running_unconfirmed"  # alive, no run dir, past the window

# A launch id is minted by `_new_launch_id` and is also a filename component, so anything
# arriving from a URL must match this exactly before it touches the filesystem.
LAUNCH_ID_PATTERN = re.compile(r"^launch-\d{8}T\d{12}Z$")

# Bounds for the log tail returned with a launch (UI shows ~40 lines in a <pre>).
LOG_TAIL_MAX_BYTES = 16_384
LOG_TAIL_MAX_LINES = 40

# Control characters other than \n and \t are stripped from the tail: the log is whatever a
# child (or a hostile spec/agent it ran) printed, and it is rendered in an operator's UI.
_UNSAFE_LOG_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

# Grace period between SIGTERM and SIGKILL on cancel. The engine handles SIGTERM by
# unwinding normally; SIGKILL is the backstop for a wedged child.
CANCEL_GRACE_SECONDS = 10.0


class LaunchError(Exception):
    """Raised when a run could not be launched or cancelled."""


@dataclass
class LaunchRecord:
    """One dashboard-initiated ``ao`` invocation."""

    launch_id: str
    kind: str
    """``"run"`` or ``"resume"``."""
    pid: int
    argv: list[str]
    started_at: str
    log_path: str
    run_id: str | None = None
    workflow_path: str | None = None
    prompt_chars: int = 0
    finished_at: str | None = None
    exit_code: int | None = None
    cancelled: bool = False
    known_run_ids: list[str] = field(default_factory=list)
    """Run ids that already existed when this launch started — used to attribute the new
    run directory to this launch and not to a concurrent one."""

    def to_dict(self) -> dict:
        return asdict(self)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def derive_launch_status(
    record: LaunchRecord, alive: bool, now: datetime, discovery_window: float
) -> str:
    """Pure classification of one launch (see the ``STATUS_*`` constants).

    A record with a ``run_id`` is ``started`` whatever happens to the run afterwards --
    from then on the *run's* status is the source of truth, not the launch's. Without a
    run id the process decides: gone means the engine died before creating a run
    directory (``failed_to_start``); alive splits on age so a slow-but-healthy start
    reads as "starting" first and "unconfirmed" only once the discovery window has passed.
    """
    if record.run_id:
        return STATUS_STARTED
    if not alive:
        return STATUS_FAILED_TO_START
    try:
        age = (now - datetime.fromisoformat(record.started_at)).total_seconds()
    except ValueError:
        return STATUS_RUNNING_UNCONFIRMED  # unreadable timestamp: do not claim "starting"
    return STATUS_STARTING if age < discovery_window else STATUS_RUNNING_UNCONFIRMED


def tail_log_text(
    path: Path, max_bytes: int = LOG_TAIL_MAX_BYTES, max_lines: int = LOG_TAIL_MAX_LINES
) -> tuple[str, bool]:
    """Last *max_lines* lines (and at most *max_bytes* bytes) of *path*, as safe text.

    Returns ``(text, truncated)``. Missing/unreadable file -> ``("", False)``.
    """
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            cut = size > max_bytes
            if cut:
                fh.seek(size - max_bytes)
            raw = fh.read(max_bytes)
    except OSError:
        return "", False
    text = _UNSAFE_LOG_CHARS.sub("", raw.decode("utf-8", errors="replace"))
    lines = text.splitlines()
    if cut and lines:
        lines = lines[1:]  # first line is almost certainly cut mid-way
    truncated = cut or len(lines) > max_lines
    return "\n".join(lines[-max_lines:]), truncated


def _pid_alive(pid: int) -> bool:
    """Return True if *pid* exists and we may signal it.

    ``os.kill(pid, 0)`` performs the permission/existence check without delivering a
    signal. This cannot distinguish our child from an unrelated process that reused the
    PID; callers pair it with the persisted launch record for that reason.

    NOT sufficient on its own for a process this supervisor spawned: an exited child stays
    a zombie — and therefore still signalable — until its parent reaps it. That is what
    :meth:`ProcessSupervisor._alive` handles; use that for tracked launches and this only
    for PIDs inherited from a previous dashboard process (already reparented to init, so
    reaped by it).
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class ProcessSupervisor:
    """Launches, tracks, and cancels dashboard-initiated runs for one workspace."""

    def __init__(
        self,
        workspace_root: str,
        ao_command: list[str] | None = None,
        env: dict[str, str] | None = None,
        run_id_discovery_timeout: float = RUN_ID_DISCOVERY_TIMEOUT_SECONDS,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        """
        Args:
            workspace_root: Workspace whose ``.orchestrator/`` tree holds runs and launches.
            ao_command: argv prefix used to invoke the CLI. Defaults to
                ``[sys.executable, "-m", "agent_orchestrator.cli"]`` rather than a bare
                ``ao`` so the dashboard always drives the *same* installation it is running
                from — a globally installed ``ao`` on PATH is frequently a stale snapshot
                of a different version (that mismatch is a known, previously-hit failure
                mode in this project). Injectable so tests can substitute a stub.
            env: Extra environment variables for children, merged over ``os.environ``.
            run_id_discovery_timeout: Seconds to wait for a launched run's directory to
                appear. Injectable so tests need not pay the production timeout.
            clock: Time source for launch ids/timestamps/status ages. Injectable so
                status classification is deterministic in tests.
        """
        self._clock = clock
        self._root = Path(workspace_root).resolve()
        self._ao_command = (
            list(ao_command)
            if ao_command
            else [
                sys.executable,
                "-m",
                "agent_orchestrator.cli",
            ]
        )
        self._env_overrides = dict(env or {})
        self._repo = RunRepository(str(self._root))
        self._discovery_timeout = run_id_discovery_timeout
        # pid -> Popen for children THIS supervisor instance spawned. Kept so they can be
        # reaped: without a wait()/poll() an exited child remains a zombie and every
        # liveness check would report it as still running, forever.
        self._children: dict[int, subprocess.Popen] = {}

    def _alive(self, record: LaunchRecord) -> bool:
        """Liveness for *record*, reaping the child if it has exited.

        For a launch this instance owns, ``Popen.poll()`` is authoritative and also reaps,
        so the process leaves the table instead of lingering as an un-reapable zombie.
        For a launch inherited from a previous dashboard process (persisted record, no
        Popen), fall back to the signal probe — init has already reaped it, so the probe
        is accurate there.
        """
        proc = self._children.get(record.pid)
        if proc is None:
            return _pid_alive(record.pid)

        exit_code = proc.poll()
        if exit_code is None:
            return True

        self._children.pop(record.pid, None)
        record.exit_code = exit_code
        return False

    # -- paths -----------------------------------------------------------------

    @property
    def _ui_dir(self) -> Path:
        return self._root / ORCHESTRATOR_DIR / UI_DIR

    @property
    def launches_dir(self) -> Path:
        return self._ui_dir / LAUNCHES_DIR

    @property
    def logs_dir(self) -> Path:
        return self._ui_dir / LOGS_DIR

    # -- persistence -----------------------------------------------------------

    def _record_path(self, launch_id: str) -> Path:
        return self.launches_dir / f"{launch_id}.json"

    def _save(self, record: LaunchRecord) -> None:
        self.launches_dir.mkdir(parents=True, exist_ok=True)
        path = self._record_path(record.launch_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")
        os.replace(tmp, path)  # atomic, same write-then-rename as RunStateStore

    def load_records(self) -> list[LaunchRecord]:
        """Every persisted launch record, newest-first. Corrupt records are skipped."""
        if not self.launches_dir.is_dir():
            return []
        records: list[LaunchRecord] = []
        for path in self.launches_dir.glob("*.json"):
            record = self._read_record(path)
            if record is not None:
                records.append(record)
        records.sort(key=lambda r: r.started_at, reverse=True)
        return records

    @staticmethod
    def _read_record(path: Path) -> LaunchRecord | None:
        """Parse one record file; ``None`` for anything unreadable or not a record.

        Unknown keys are dropped (a record written by a newer version must not vanish
        after a downgrade), but a missing required key or a wrong container type is not
        guessed at.
        """
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return None
            known = {f.name for f in fields(LaunchRecord)}
            return LaunchRecord(**{k: v for k, v in data.items() if k in known})
        except (OSError, ValueError, TypeError):
            return None

    def get_launch(self, launch_id: str) -> LaunchRecord | None:
        """Load one record by id, refreshed against process liveness.

        *launch_id* may come straight from a URL, so it must match
        :data:`LAUNCH_ID_PATTERN` (no separators, no ``..``) before it is joined onto a
        path; anything else is "not found". The stored ``launch_id`` field must agree with
        the filename, so a tampered/copied record cannot alias another launch's log.
        """
        if not LAUNCH_ID_PATTERN.match(launch_id):
            return None
        record = self._read_record(self._record_path(launch_id))
        if record is None or record.launch_id != launch_id:
            return None
        self._refresh(record)
        return record

    def describe(self, record: LaunchRecord, *, include_log: bool = False) -> dict:
        """The persisted fields plus derived ``status`` (and optionally the log tail).

        Additive over :meth:`LaunchRecord.to_dict`. The log path is *derived from the
        validated launch id*, never taken from the record's own ``log_path`` (which a
        tampered record could point anywhere).
        """
        alive = record.finished_at is None and self._alive(record)
        data = record.to_dict()
        data["status"] = derive_launch_status(record, alive, self._clock(), self._discovery_timeout)
        if include_log:
            text, truncated = self._safe_log_tail(record.launch_id)
            data["log_tail"] = text
            data["log_truncated"] = truncated
        return data

    def _safe_log_tail(self, launch_id: str) -> tuple[str, bool]:
        if not LAUNCH_ID_PATTERN.match(launch_id):
            return "", False
        path = self.logs_dir / f"{launch_id}.log"
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            return "", False
        # A symlinked log could point outside the workspace's logs dir.
        if resolved.parent != self.logs_dir.resolve() or not resolved.is_file():
            return "", False
        return tail_log_text(resolved)

    def record_for_run(self, run_id: str) -> LaunchRecord | None:
        """Return the launch record that owns *run_id*, if the dashboard started it."""
        for record in self.load_records():
            if record.run_id == run_id:
                return record
        return None

    # -- launching -------------------------------------------------------------

    def _spawn(self, argv: list[str], kind: str, launch_id: str) -> LaunchRecord:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.logs_dir / f"{launch_id}.log"

        env = {**os.environ, **self._env_overrides}
        known = set(self._repo.list_run_ids())

        # stdout and stderr both land in one log so the UI can show the operator exactly
        # what the CLI printed, interleaved in real order.
        log_handle = open(log_path, "wb")
        try:
            proc = subprocess.Popen(  # noqa: S603 - argv is built here, never shell-parsed
                argv,
                cwd=str(self._root),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
                start_new_session=True,  # own process group -> cancel reaches the whole tree
            )
        except OSError as exc:
            log_handle.close()
            raise LaunchError(f"failed to start {argv[0]}: {exc}") from exc
        finally:
            # The child holds its own dup of the fd; this process does not need it.
            log_handle.close()

        self._children[proc.pid] = proc

        record = LaunchRecord(
            launch_id=launch_id,
            kind=kind,
            pid=proc.pid,
            argv=list(argv),
            started_at=self._clock().isoformat(),
            log_path=str(log_path),
            known_run_ids=sorted(known),
        )
        self._save(record)
        return record

    def launch_run(
        self,
        workflow_path: str | None = None,
        prompt: str | None = None,
        reposets: str | None = None,
        agents: str | None = None,
        options: dict[str, object] | None = None,
        general_instructions: list[str] | None = None,
        workspace: str | None = None,
    ) -> LaunchRecord:
        """Start ``ao run`` for *workflow_path*, optionally writing *prompt* first.

        The prompt is passed via ``--prompt-file`` rather than ``--prompt`` so no run
        content ever transits an argv (visible in ``ps`` to every user on the box, and
        subject to ARG_MAX for a long prompt).

        Returns:
            The persisted :class:`LaunchRecord`, with ``run_id`` filled in when the engine
            created its run directory quickly enough to observe.
        """
        launch_id = self._new_launch_id()
        argv = [*self._ao_command, "run"]

        if workflow_path:
            argv += ["--workflow", workflow_path]
        if reposets:
            argv += ["--reposets", reposets]
        if agents:
            argv += ["--agents", agents]
        if workspace:
            argv += ["--workspace", workspace]

        prompt_chars = 0
        if prompt is not None and prompt.strip():
            prompt_file = self._write_prompt_file(launch_id, prompt)
            argv += ["--prompt-file", str(prompt_file)]
            prompt_chars = len(prompt)

        for path in general_instructions or []:
            argv += ["--general-instruction", path]

        argv += _render_options(options or {})

        record = self._spawn(argv, kind="run", launch_id=launch_id)
        record.workflow_path = workflow_path
        record.prompt_chars = prompt_chars
        record.run_id = self._discover_run_id(record)
        if record.run_id is None and record.exit_code is not None:
            # The child already exited without ever creating a run directory: record that
            # now, so the very first response can say "failed to start" instead of leaving
            # it to a later reconcile.
            record.finished_at = self._clock().isoformat()
        self._save(record)
        return record

    def launch_resume(
        self,
        run_id: str,
        workflow_path: str | None = None,
        reposets: str | None = None,
        agents: str | None = None,
        options: dict[str, object] | None = None,
        general_instructions: list[str] | None = None,
        workspace: str | None = None,
    ) -> LaunchRecord:
        """Start ``ao resume --run-id <run_id>``."""
        launch_id = self._new_launch_id()
        argv = [*self._ao_command, "resume", "--run-id", run_id]

        if workflow_path:
            argv += ["--workflow", workflow_path]
        if reposets:
            argv += ["--reposets", reposets]
        if agents:
            argv += ["--agents", agents]
        if workspace:
            argv += ["--workspace", workspace]
        for path in general_instructions or []:
            argv += ["--general-instruction", path]
        argv += _render_options(options or {})

        record = self._spawn(argv, kind="resume", launch_id=launch_id)
        record.workflow_path = workflow_path
        record.run_id = run_id  # known up front, unlike a fresh run
        self._save(record)
        return record

    def _new_launch_id(self) -> str:
        return f"launch-{self._clock().strftime('%Y%m%dT%H%M%S%fZ')}"

    def _write_prompt_file(self, launch_id: str, prompt: str) -> Path:
        """Persist the UI-typed prompt so it can be passed by path, and kept for audit."""
        prompts_dir = self._ui_dir / "prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)
        path = prompts_dir / f"{launch_id}.md"
        path.write_text(prompt, encoding="utf-8")
        return path

    def _discover_run_id(self, record: LaunchRecord) -> str | None:
        """Wait briefly for the run directory this launch created.

        The engine derives ``run_id`` itself (``<workflow_id>-<UTC timestamp>``) and only
        the child knows it, so the dashboard learns it by diffing the runs directory
        against the pre-launch snapshot captured in ``record.known_run_ids``.

        Under simultaneous launches two new directories can appear inside one poll window;
        ids already claimed by another persisted record are excluded so each launch adopts
        at most one run. Returning ``None`` is a normal outcome for a slow start, not a
        failure — `reconcile` picks it up on the next poll.
        """
        known = set(record.known_run_ids)
        claimed = {r.run_id for r in self.load_records() if r.run_id}
        deadline = time.monotonic() + self._discovery_timeout

        def fresh_run() -> str | None:
            fresh = [rid for rid in self._repo.list_run_ids() if rid not in known | claimed]
            # list_run_ids is newest-first by mtime; the oldest unclaimed id is the one
            # created earliest after this launch, i.e. most likely ours.
            return fresh[-1] if fresh else None

        while True:
            found = fresh_run()
            if found:
                return found
            if not self._alive(record):
                # Child already exited (bad flags, validation error, ...): stop waiting at
                # once -- there is nothing left to discover. One last scan closes the race
                # where it created its run directory and exited between the two checks.
                return fresh_run()
            if time.monotonic() >= deadline:
                return None
            time.sleep(RUN_ID_DISCOVERY_POLL_SECONDS)

    # -- lifecycle -------------------------------------------------------------

    def reconcile(self) -> list[LaunchRecord]:
        """Refresh liveness/run-id for every persisted record and return them.

        Called on read paths so a dashboard that restarted still reports accurate state:
        records whose PID is gone are marked finished, and a record that had not yet
        observed its run directory gets a second chance to attach one.
        """
        records = self.load_records()
        for record in records:
            self._refresh(record)
        return records

    def _refresh(self, record: LaunchRecord) -> None:
        """Bring one record up to date (run id, liveness) and persist any change."""
        changed = False

        if record.run_id is None and record.finished_at is None:
            discovered = self._discover_run_id_fast(record)
            if discovered:
                record.run_id = discovered
                changed = True

        if record.finished_at is None and not self._alive(record):
            record.finished_at = self._clock().isoformat()
            changed = True

        if changed:
            self._save(record)

    def _discover_run_id_fast(self, record: LaunchRecord) -> str | None:
        """Non-blocking variant of :meth:`_discover_run_id` for the reconcile path."""
        known = set(record.known_run_ids)
        claimed = {
            r.run_id for r in self.load_records() if r.run_id and r.launch_id != record.launch_id
        }
        fresh = [rid for rid in self._repo.list_run_ids() if rid not in known | claimed]
        return fresh[-1] if fresh else None

    def is_running(self, run_id: str) -> bool:
        """True if a dashboard-launched process for *run_id* is still alive."""
        record = self.record_for_run(run_id)
        return bool(record and record.finished_at is None and self._alive(record))

    def cancel(self, run_id: str) -> LaunchRecord:
        """Cancel the dashboard-launched process for *run_id* (FR-R2).

        Signals the child's whole process group — SIGTERM, then SIGKILL after
        :data:`CANCEL_GRACE_SECONDS` — so the `claude` processes the engine spawned die
        with it rather than continuing to spend tokens against a run nobody is watching.

        The caller is responsible for marking the run's persisted state ``cancelled``; this
        method owns the process, not the run state (see
        :meth:`DashboardService.cancel_run`).

        Raises:
            LaunchError: If the dashboard did not launch this run, or it already finished.
        """
        record = self.record_for_run(run_id)
        if record is None:
            raise LaunchError(
                f"run {run_id} was not launched from this dashboard, so there is no process "
                "to cancel. Stop it where it was started, or configure a `stop_file` "
                "circuit breaker on the workflow for out-of-band cancellation."
            )
        if not self._alive(record):
            record.finished_at = record.finished_at or self._clock().isoformat()
            self._save(record)
            raise LaunchError(f"run {run_id} is no longer running (pid {record.pid} is gone)")

        self._signal_group(record.pid, signal.SIGTERM)

        deadline = time.monotonic() + CANCEL_GRACE_SECONDS
        while time.monotonic() < deadline and self._alive(record):
            time.sleep(RUN_ID_DISCOVERY_POLL_SECONDS)

        if self._alive(record):
            self._signal_group(record.pid, signal.SIGKILL)
            # Reap the killed child so it does not linger as a zombie, and so a later
            # liveness probe cannot be fooled by an unreaped entry.
            proc = self._children.pop(record.pid, None)
            if proc is not None:
                try:
                    proc.wait(timeout=CANCEL_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    pass

        record.cancelled = True
        record.finished_at = self._clock().isoformat()
        self._save(record)
        return record

    @staticmethod
    def _signal_group(pid: int, sig: int) -> None:
        """Signal the process group led by *pid*, falling back to the process itself."""
        try:
            os.killpg(os.getpgid(pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, sig)
            except OSError:
                pass  # already gone; cancel is idempotent by intent

    def read_log(self, launch_id: str, max_bytes: int = 200_000) -> str:
        """Return the tail of a launch's combined stdout/stderr log."""
        path = self.logs_dir / f"{launch_id}.log"
        if not path.is_file():
            return ""
        size = path.stat().st_size
        with open(path, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
            return fh.read().decode("utf-8", errors="replace")


# Options the dashboard may forward to `ao run`/`ao resume`, mapped to their CLI flags.
# An explicit allow-list, NOT free-form passthrough: the UI is unauthenticated by design in
# this release, so a request body must never be able to inject arbitrary argv into a
# subprocess. Anything absent here is silently ignored.
ALLOWED_OPTIONS: dict[str, str] = {
    "model": "--model",
    "effort": "--effort",
    "max_attempts": "--max-attempts",
    "max_turns": "--max-turns",
    "max_parallel": "--max-parallel",
    "budget_total": "--budget-total",
    "quota_max_wait": "--quota-max-wait",
    "quota_poll_interval": "--quota-poll-interval",
}

# Boolean options rendered as bare flags rather than `--flag value`.
ALLOWED_BOOL_OPTIONS: dict[str, tuple[str, str]] = {
    "self_heal": ("--self-heal", "--no-self-heal"),
}


def _render_options(options: dict[str, object]) -> list[str]:
    """Render an options mapping into argv fragments, dropping anything not allow-listed."""
    argv: list[str] = []
    for key, value in options.items():
        if value is None or value == "":
            continue
        if key in ALLOWED_BOOL_OPTIONS:
            on_flag, off_flag = ALLOWED_BOOL_OPTIONS[key]
            argv.append(on_flag if value else off_flag)
        elif key in ALLOWED_OPTIONS:
            argv += [ALLOWED_OPTIONS[key], str(value)]
    return argv


__all__ = [
    "ALLOWED_BOOL_OPTIONS",
    "ALLOWED_OPTIONS",
    "CANCEL_GRACE_SECONDS",
    "LAUNCH_ID_PATTERN",
    "STATUS_FAILED_TO_START",
    "STATUS_RUNNING_UNCONFIRMED",
    "STATUS_STARTED",
    "STATUS_STARTING",
    "derive_launch_status",
    "tail_log_text",
    "LaunchError",
    "LaunchRecord",
    "ProcessSupervisor",
    "RUNS_DIR",
]
