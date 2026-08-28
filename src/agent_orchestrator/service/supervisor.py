"""The supervisor daemon: spawns one ``ao ui`` child per registered workspace, monitors and
restarts crashed children with backoff, handles graceful shutdown, and runs the boot-resume
scan/decide/act sequence exactly once at startup (HLD §5.2/§6.1/§6.3/§6.5, ADR-0012 D1/D2/D4
+ early-gate corrections).

``Supervisor`` is a plain, signal-free Python class (AC11): ``start()``/``tick()``/
``shutdown()``/``status_snapshot()`` are each independently callable so tests can single-step
the monitor loop deterministically instead of racing real timers, and so a later task
(``T-Hb3x7q``, the ``ao service run`` CLI command + hub server) can own signal handling and
call into this class like any other API.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from pydantic import BaseModel

from ..ui.processes import ProcessSupervisor
from ..ui.runs import RunNotFoundError, RunRepository
from .boot_resume import BootResumeGuard, Decision, ResumeCandidate, scan_resumable_runs
from .ports import PortResolution, persist_resolution, pick_free_port, resolve_ports
from .registry import ServiceRegistry, ServiceRegistryFile

logger = logging.getLogger(__name__)

# -- filenames under <state_dir> (HLD §5.2) -------------------------------------------------
SUPERVISOR_LOCK_FILENAME = "supervisor.lock"
SUPERVISOR_SNAPSHOT_FILENAME = "supervisor.json"
PORT_RESOLUTION_FILENAME = "port_resolution.json"
LOGS_SUBDIR = "logs"

# Loopback host every spawned `ao ui` child binds -- mirrors `cli.py::UI_DEFAULT_HOST`; the
# service module intentionally does not import `cli.py` (out of bounds for this task), so
# this is a deliberate, small, documented duplication of the same literal.
DEFAULT_CHILD_HOST = "127.0.0.1"

# Restart backoff (AC5 / HLD §6.3): base 1s, x2, capped at 60s; resets once a restarted
# child has stayed up continuously past the 30s stability window.
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_FACTOR = 2.0
BACKOFF_CAP_SECONDS = 60.0
STABILITY_WINDOW_SECONDS = 30.0

# EADDRINUSE backstop (AC19 / HLD §6.3): distinct from RestartBackoff's own knobs above --
# N consecutive fast (within-grace-window) exits reassigns the port instead of backing off
# against a doomed one forever.
FAST_FAIL_GRACE_SECONDS = 3.0
FAST_FAIL_MAX_ATTEMPTS = 3

# Inter-spawn stagger (AC22): a lightweight, bounded thundering-herd mitigation, not a
# general concurrency limiter.
SPAWN_STAGGER_SECONDS = 0.3

# Shutdown (AC9 / HLD §6.5).
DEFAULT_SHUTDOWN_GRACE_SECONDS = 10.0
SHUTDOWN_POLL_SECONDS = 0.05

# Orphan reclamation (AC18 / HLD §6.1 step 3).
ORPHAN_RECLAIM_GRACE_SECONDS = 3.0
ORPHAN_RECLAIM_POLL_SECONDS = 0.05


class SupervisorLockHeldError(Exception):
    """Raised when ``<state_dir>/supervisor.lock`` is already held by another process
    (AC15 / ADR-0012 early-gate correction #3: nothing downstream is safe to run twice
    concurrently against the same registry/state files)."""


def acquire_singleton_lock(lock_path: Path) -> IO[str]:
    """Acquire the exclusive, non-blocking singleton lock at *lock_path*.

    Returns the open file handle -- the caller MUST keep it open (and therefore the flock
    held) for the life of the process; only :func:`release_singleton_lock` (called from
    :meth:`Supervisor.shutdown`) closes it explicitly. An OS-level crash/SIGKILL releases the
    flock automatically when the kernel tears down the process's fd table, which is exactly
    the "singleton for as long as the process is alive, no explicit cleanup required"
    property AC15 needs -- verified in tests by actually killing a subprocess holding the
    lock and confirming a fresh acquire succeeds, not just an assertion about flock's
    documented semantics.

    Raises:
        SupervisorLockHeldError: if another process already holds the lock. Best-effort
            includes that process's pid (written into the lock file's contents by the
            holder) in the error message when readable.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.seek(0)
        holder = handle.read().strip() or "unknown"
        handle.close()
        raise SupervisorLockHeldError(
            f"another `ao service run` appears to be active (pid {holder}); refusing to "
            f"start a second supervisor against {lock_path}"
        ) from exc
    # Record our own pid so a contending process's error message can name us.
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def release_singleton_lock(handle: IO[str]) -> None:
    """Release a lock acquired by :func:`acquire_singleton_lock`. Only called from a clean
    :meth:`Supervisor.shutdown` -- see AC15: never released anywhere else."""
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _read_os_boot_id() -> str | None:
    """Best-effort OS boot id (AC21): Linux only, ``None`` everywhere else or on any read
    error -- advisory only, never raises, never gates a decision by itself."""
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _pid_alive(pid: int) -> bool:
    """Existence probe for a PID this process did NOT itself ``Popen`` (mirrors
    ``ui/processes.py::_pid_alive``; duplicated locally since that module is read-only and
    private-by-convention).

    Correct here (unlike a ``Popen`` this process spawned) because every PID this is used on
    -- a previous boot's orphaned child -- is not a child of *this* process, so it cannot
    become an un-reapable zombie from this process's perspective: if it already exited, its
    real parent (init, after ``start_new_session`` re-parenting) has already reaped it and
    the kill probe reports it accurately as gone.
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


def _slug_for_root(root: str) -> str:
    """Filesystem-safe, collision-resistant slug for a workspace root's log filename
    (AC20)."""
    sanitized = re.sub(r"[^A-Za-z0-9]+", "_", root).strip("_") or "root"
    digest = hashlib.sha256(root.encode("utf-8")).hexdigest()[:8]
    return f"{sanitized}-{digest}"


# -- ManagedChild / RestartBackoff (AC4/AC5) -------------------------------------------------


@dataclass
class ManagedChild:
    """Bookkeeping for one supervised ``ao ui`` child process."""

    root: str
    port: int
    popen: subprocess.Popen | None = None
    restart_count: int = 0
    next_retry_at: float | None = None
    """Monotonic timestamp at/after which `tick()` should attempt a respawn. `None` while
    `popen` is set (nothing to retry) or immediately after a clean shutdown."""
    last_error: str | None = None
    stable_since: float | None = None
    """Monotonic timestamp of the current life's last (re)spawn, or `None` while not
    running. Doubles as both the stability-window anchor (backoff reset) and the
    startup-grace anchor (EADDRINUSE fast-fail detection) -- both are "how long has this
    specific life been running" questions answered from the same timestamp."""
    fast_fail_count: int = 0
    log_path: str | None = None
    reassignment_reason: str | None = None
    """Set only by the EADDRINUSE backstop (AC19) -- surfaced as its own status field,
    deliberately not folded into `last_error`."""


@dataclass(frozen=True)
class RestartBackoff:
    """Pure exponential-backoff helper (AC5). No I/O, no clock of its own -- callers supply
    elapsed/attempt numbers so this is testable in isolation against a fake monotonic clock
    without any `Supervisor` machinery."""

    base: float = BACKOFF_BASE_SECONDS
    factor: float = BACKOFF_FACTOR
    cap: float = BACKOFF_CAP_SECONDS
    stability_window: float = STABILITY_WINDOW_SECONDS

    def next_delay(self, restart_count: int) -> float:
        """Delay before the next restart, given *restart_count* consecutive non-stable
        restarts already attempted (0 for the first restart after a fresh/stable child)."""
        return min(self.base * (self.factor**restart_count), self.cap)

    def is_stable(self, elapsed_seconds: float) -> bool:
        """True once a child has been continuously up for at least the stability window."""
        return elapsed_seconds >= self.stability_window


# -- on-disk snapshots (HLD §5.2) ------------------------------------------------------------


class ManagedChildSnapshot(BaseModel):
    root: str
    port: int
    pid: int


class SupervisorSnapshot(BaseModel):
    """``<state_dir>/supervisor.json`` -- read both by ``ao service status``'s fallback path
    and by the *next* boot's orphan-reclamation step (AC18)."""

    pid: int
    boot_id: str
    os_boot_id: str | None = None
    started_at: str
    hub_port: int
    children: list[ManagedChildSnapshot] = []


ChildSpawner = Callable[[str, int, Path], "subprocess.Popen"]


def default_child_spawner(ao_executable: list[str]) -> ChildSpawner:
    """Build the production `child_spawner`: real ``<ao_executable> ui --workspace <root>
    --host 127.0.0.1 --port <port>``, stdout/stderr redirected to *log_path* (AC20, same
    open-handle-then-close-in-parent pattern as ``ui/processes.py``'s own ``_spawn``),
    ``start_new_session=True`` so each child gets its own session/process group -- this is
    what lets `Supervisor.shutdown()` target only the child's own pid (ADR-0012 D2) without
    a SIGTERM to *this* process's controlling terminal also reaching them.
    """

    def _spawn(root: str, port: int, log_path: Path) -> subprocess.Popen:
        argv = [
            *ao_executable,
            "ui",
            "--workspace",
            root,
            "--host",
            DEFAULT_CHILD_HOST,
            "--port",
            str(port),
        ]
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = open(log_path, "wb")
        try:
            return subprocess.Popen(  # noqa: S603 - argv is built here, never shell-parsed
                argv,
                cwd=root,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        finally:
            # The child holds its own dup of the fd; this process does not need it.
            log_handle.close()

    return _spawn


class Supervisor:
    """Owns port resolution/persistence, orphan reclamation, boot-resume, child process
    monitoring/restart, and graceful shutdown for one supervisor daemon instance.

    Deliberately signal-free (AC11): the caller (`ao service run`'s CLI body, a later task)
    installs `SIGTERM`/`SIGINT` handlers and calls `.shutdown()` from them.
    """

    def __init__(
        self,
        registry: ServiceRegistryFile,
        *,
        ao_executable: list[str] | None = None,
        state_dir: Path,
        hub_port: int,
        registry_path: Path | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        child_spawner: ChildSpawner | None = None,
        process_supervisor_factory: Callable[[str], ProcessSupervisor] = ProcessSupervisor,
        run_repository_factory: Callable[[str], RunRepository] = RunRepository,
        boot_id: str | None = None,
        spawn_stagger_seconds: float = SPAWN_STAGGER_SECONDS,
    ) -> None:
        """
        Args:
            registry: The workspace list to serve, already loaded by the caller (e.g. via
                `ServiceRegistry().load()`). Port resolution/persistence re-reads the
                current on-disk registry under lock (AC17) rather than trusting this
                snapshot verbatim for the write, but this snapshot governs which
                workspaces get a child spawned *this* boot.
            ao_executable: argv prefix used to spawn a child `ao ui`. Defaults to
                `[sys.executable, "-m", "agent_orchestrator.cli"]`, never a bare `ao`, for
                the same "stale global install" reason `ui/processes.py` documents.
            state_dir: Runtime state directory (lock/snapshot/boot-resume/logs).
            hub_port: Included verbatim in `status_snapshot()`; this class does not itself
                start a hub server (that is `T-Hb3x7q`'s CLI body).
            registry_path: Optional explicit registry file path (else `ServiceRegistry`'s
                own `default_registry_path()`/env-override resolution).
            clock/monotonic/sleeper: Injectable time sources for deterministic tests.
            child_spawner: Injectable `(root, port, log_path) -> Popen`. Tests substitute a
                fake (e.g. `sh -c "trap : TERM; sleep 100"`) instead of a real `ao ui`/
                uvicorn process.
            process_supervisor_factory/run_repository_factory: Threaded through to
                boot-resume's scan AND the immediately-before-spawn idempotency re-check
                (AC16), so both can be swapped for fixtures in tests.
            boot_id: Injectable for deterministic tests; defaults to a fresh `uuid4` hex
                per instance -- each `Supervisor` represents exactly one boot.
        """
        # Normalize every root the same way `registry.py::save()` does, unconditionally --
        # `resolve_ports()` below is called once against `self._registry` and its resulting
        # `PortResolution.ports` keys must stay valid dict lookups against `self._registry.
        # workspaces` for the rest of `start()`, including after `self._registry` is
        # replaced by `mutate()`'s (also-normalized) return value. Without this, a caller
        # that hands in a non-normalized root (anything other than a fresh `ServiceRegistry.
        # load()`) could silently never get a child spawned for that workspace.
        self._registry = ServiceRegistryFile(
            workspaces=[
                entry.model_copy(update={"root": str(Path(entry.root).resolve())})
                for entry in registry.workspaces
            ]
        )
        self._ao_executable = (
            list(ao_executable)
            if ao_executable
            else [sys.executable, "-m", "agent_orchestrator.cli"]
        )
        self._state_dir = state_dir
        self._hub_port = hub_port
        self._registry_store = ServiceRegistry(registry_path)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._child_spawner: ChildSpawner = child_spawner or default_child_spawner(
            self._ao_executable
        )
        self._process_supervisor_factory = process_supervisor_factory
        self._run_repository_factory = run_repository_factory
        self._boot_id = boot_id or uuid.uuid4().hex
        self._spawn_stagger_seconds = spawn_stagger_seconds

        self._children: dict[str, ManagedChild] = {}
        self._backoff = RestartBackoff()
        self._boot_resume_guard = BootResumeGuard(state_dir, clock=self._clock)
        self._boot_resume_decisions: list[dict[str, str]] = []
        self._last_resolution: PortResolution | None = None
        self._lock_handle: IO[str] | None = None
        self._started_monotonic: float | None = None
        self._started_at_iso: str | None = None
        self._os_boot_id: str | None = None

    # -- boot -----------------------------------------------------------------------------

    def start(self) -> None:
        """Startup sequence (HLD §6.1): lock -> resolve+persist ports -> reclaim orphans ->
        boot-resume -> spawn children."""
        lock_path = self._state_dir / SUPERVISOR_LOCK_FILENAME
        self._lock_handle = acquire_singleton_lock(lock_path)

        self._started_monotonic = self._monotonic()
        self._started_at_iso = self._clock().isoformat()
        self._os_boot_id = _read_os_boot_id()

        # Read the PREVIOUS boot's snapshot before writing anything of our own -- orphan
        # reclamation (AC18) needs the prior incarnation's {root, port, pid}s, and writing
        # our own supervisor.json first would destroy that evidence.
        previous_snapshot = self._read_previous_supervisor_snapshot()

        # Resolve ports exactly once (P3 picks are real random binds -- resolving twice
        # would hand children a different port than whatever gets persisted). AC17: persist
        # via the locked `mutate()`, re-reading the CURRENT on-disk registry under the lock
        # and merging this already-computed resolution onto it -- `persist_resolution` only
        # touches entries this resolution has a P3 pick for, by root, so a concurrent
        # `ao service add`/`remove` is never clobbered (it simply isn't touched by this
        # merge, and is picked up on a later boot).
        resolution = resolve_ports(self._registry)
        updated_registry = self._registry_store.mutate(
            lambda current: persist_resolution(current, resolution)
        )
        self._registry = updated_registry
        self._last_resolution = resolution
        self._write_port_resolution(resolution)

        self._reclaim_orphans(previous_snapshot, resolution)

        # Safe to start writing our own supervisor.json now -- pid/boot_id land on disk even
        # if a later step in start() raises, so a next boot's orphan-reclaim still sees us.
        self._write_supervisor_snapshot()

        self._run_boot_resume()

        for entry in self._registry.workspaces:
            port = resolution.ports.get(entry.root)
            if port is None:
                continue  # defensive; resolve_ports always assigns a port per workspace
            child = ManagedChild(root=entry.root, port=port)
            self._children[entry.root] = child
            self._respawn(child, self._monotonic())
            self._sleeper(self._spawn_stagger_seconds)

    # -- boot-resume act step (AC16) -------------------------------------------------------

    def _run_boot_resume(self) -> None:
        for entry in self._registry.workspaces:
            if not entry.autoresume:
                continue
            candidates = scan_resumable_runs(
                entry.root,
                supervisor_factory=self._process_supervisor_factory,
                repo_factory=self._run_repository_factory,
            )
            for candidate in candidates:
                self._decide_and_act(candidate)

    def _record_decision(self, candidate: ResumeCandidate, decision: str) -> None:
        self._boot_resume_decisions.append(
            {
                "workspace_root": candidate.workspace_root,
                "run_id": candidate.run_id,
                "decision": decision,
            }
        )

    def _decide_and_act(self, candidate: ResumeCandidate) -> None:
        decision = self._boot_resume_guard.decide(candidate, self._boot_id)
        if decision != Decision.APPROVE:
            self._record_decision(candidate, decision.value)
            return

        # AC16 (blocking): re-check immediately before spawning -- not cached from the scan
        # -- so a human clicking Resume from the dashboard in the same window never races a
        # second engine onto the same run state. A skip here must NOT call record_attempt:
        # an attempt not made must not consume the attempts/cooldown budget.
        proc_sup = self._process_supervisor_factory(candidate.workspace_root)
        repo = self._run_repository_factory(candidate.workspace_root)

        if proc_sup.is_running(candidate.run_id):
            self._record_decision(candidate, "skip_recheck_already_running")
            return
        try:
            state = repo.load_state(candidate.run_id)
        except RunNotFoundError:
            self._record_decision(candidate, "skip_recheck_state_missing")
            return
        if state.status != "running":
            self._record_decision(candidate, "skip_recheck_terminal")
            return

        proc_sup.launch_resume(candidate.run_id)
        self._boot_resume_guard.record_attempt(
            candidate, self._boot_id, os_boot_id=self._os_boot_id
        )
        self._record_decision(candidate, Decision.APPROVE.value)

    # -- orphan reclamation (AC18) ----------------------------------------------------------

    def _reclaim_orphans(
        self, previous: SupervisorSnapshot | None, resolution: PortResolution
    ) -> None:
        if previous is None or previous.boot_id == self._boot_id:
            return
        for prev_child in previous.children:
            if resolution.ports.get(prev_child.root) != prev_child.port:
                continue  # this boot resolved a different port -- not the same assignment
            if not self._pid_plausibly_matches(prev_child.pid, prev_child.root, prev_child.port):
                continue
            self._terminate_and_wait(prev_child.pid)

    def _pid_plausibly_matches(self, pid: int, root: str, port: int) -> bool:
        if not _pid_alive(pid):
            return False
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            return True  # /proc unavailable -- degrade to pid-liveness-only (documented)
        cmdline = raw.decode("utf-8", errors="replace")
        return root in cmdline and str(port) in cmdline

    def _terminate_and_wait(self, pid: int) -> None:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return  # already gone
        deadline = self._monotonic() + ORPHAN_RECLAIM_GRACE_SECONDS
        while self._monotonic() < deadline and _pid_alive(pid):
            self._sleeper(ORPHAN_RECLAIM_POLL_SECONDS)
        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    # -- monitor loop (AC8) ------------------------------------------------------------------

    def tick(self) -> None:
        """One monitor-loop pass: `poll()` each child (never a bare PID-alive check, same
        zombie-reaping reasoning as `ui/processes.py`), restart exited children respecting
        backoff, and reset backoff bookkeeping for children that have proven stable."""
        now = self._monotonic()
        for child in self._children.values():
            if child.popen is not None:
                exit_code = child.popen.poll()
                if exit_code is None:
                    self._maybe_reset_stability(child, now)
                    continue
                self._on_child_exit(child, exit_code, now)
            elif child.next_retry_at is not None and now >= child.next_retry_at:
                self._respawn(child, now)

    def _maybe_reset_stability(self, child: ManagedChild, now: float) -> None:
        if child.stable_since is None:
            return
        if self._backoff.is_stable(now - child.stable_since):
            child.restart_count = 0
            child.fast_fail_count = 0

    def _on_child_exit(self, child: ManagedChild, exit_code: int, now: float) -> None:
        ran_for = (now - child.stable_since) if child.stable_since is not None else None
        child.popen = None
        child.last_error = f"child exited with code {exit_code}" + (
            f" after {ran_for:.1f}s" if ran_for is not None else ""
        )
        child.stable_since = None
        self._write_supervisor_snapshot()
        self._register_failure(child, now, ran_for)

    def _register_failure(self, child: ManagedChild, now: float, ran_for: float | None) -> None:
        """Shared backoff/fast-fail bookkeeping for both a child that exited and a
        `child_spawner` call that raised outright (`ran_for=0.0`, the fastest possible
        failure)."""
        if ran_for is not None and self._backoff.is_stable(ran_for):
            child.restart_count = 0
            child.fast_fail_count = 0

        fast = ran_for is not None and ran_for < FAST_FAIL_GRACE_SECONDS
        child.fast_fail_count = child.fast_fail_count + 1 if fast else 0

        if child.fast_fail_count >= FAST_FAIL_MAX_ATTEMPTS:
            self._reassign_port(child, now)
            return

        delay = self._backoff.next_delay(child.restart_count)
        child.restart_count += 1
        child.next_retry_at = now + delay

    def _reassign_port(self, child: ManagedChild, now: float) -> None:
        """EADDRINUSE backstop (AC19): re-resolve via a fresh P3 pick for this boot only --
        the registry's P1/P2 pin is left untouched, so the next boot tries the pin again."""
        exclude = {c.port for c in self._children.values()}
        old_port = child.port
        new_port = pick_free_port(exclude=exclude)
        child.port = new_port
        child.fast_fail_count = 0
        child.restart_count = 0
        child.reassignment_reason = (
            f"EADDRINUSE backstop: port {old_port} failed to start {FAST_FAIL_MAX_ATTEMPTS} "
            f"times within {FAST_FAIL_GRACE_SECONDS}s each; reassigned to {new_port} for this "
            "boot only (registry pin left untouched)"
        )
        child.next_retry_at = now  # retry promptly on the next tick()

    def _respawn(self, child: ManagedChild, now: float) -> None:
        log_path = self._log_path_for(child.root)
        try:
            popen = self._child_spawner(child.root, child.port, log_path)
        except OSError as exc:
            child.last_error = f"spawn failed: {exc}"
            child.popen = None
            child.stable_since = None
            self._register_failure(child, now, ran_for=0.0)
            return

        child.popen = popen
        child.stable_since = now
        child.next_retry_at = None
        child.last_error = None
        child.log_path = str(log_path)
        self._write_supervisor_snapshot()

    def _log_path_for(self, root: str) -> Path:
        return self._state_dir / LOGS_SUBDIR / f"{_slug_for_root(root)}.log"

    # -- shutdown (AC9) -----------------------------------------------------------------------

    def shutdown(self, grace_seconds: float = DEFAULT_SHUTDOWN_GRACE_SECONDS) -> None:
        """Graceful stop (HLD §6.5, ADR-0012 D2): SIGTERM each child's own PID -- never a
        process group, never anything the child itself spawned -- poll up to *grace_seconds*,
        SIGKILL stragglers, reap. Must NOT touch any process this `Supervisor` did not itself
        spawn as a direct child."""
        for child in self._children.values():
            if child.popen is not None:
                try:
                    child.popen.terminate()  # SIGTERM to that pid only (Popen semantics)
                except ProcessLookupError:
                    pass

        deadline = self._monotonic() + grace_seconds
        while self._monotonic() < deadline:
            if all(c.popen is None or c.popen.poll() is not None for c in self._children.values()):
                break
            self._sleeper(SHUTDOWN_POLL_SECONDS)

        for child in self._children.values():
            if child.popen is not None and child.popen.poll() is None:
                try:
                    child.popen.kill()  # SIGKILL straggler, still that pid only
                except ProcessLookupError:
                    pass

        for child in self._children.values():
            if child.popen is not None:
                try:
                    child.popen.wait(timeout=grace_seconds)
                except subprocess.TimeoutExpired:
                    pass
                child.popen = None

        if self._lock_handle is not None:
            release_singleton_lock(self._lock_handle)
            self._lock_handle = None

        self._clear_supervisor_snapshot()

    # -- status / persistence (AC10) -----------------------------------------------------------

    def status_snapshot(self) -> dict:
        """The exact payload shape `service/hub.py` (next task) serves verbatim from
        `GET /api/service/status`."""
        now = self._monotonic()
        uptime = (
            max(0.0, now - self._started_monotonic) if self._started_monotonic is not None else 0.0
        )
        workspaces = [
            {
                "root": root,
                "port": child.port,
                "pid": child.popen.pid if child.popen is not None else None,
                "state": self._child_state(child),
                "restart_count": child.restart_count,
                "last_error": child.last_error,
                "log_path": child.log_path,
                "reassignment_reason": child.reassignment_reason,
            }
            for root, child in self._children.items()
        ]
        conflicts: list[dict] = (
            [c.model_dump(mode="json") for c in self._last_resolution.conflicts]
            if self._last_resolution is not None
            else []
        )
        return {
            "workspaces": workspaces,
            "conflicts": conflicts,
            "boot_resume_decisions": list(self._boot_resume_decisions),
            "supervisor_pid": os.getpid(),
            "hub_port": self._hub_port,
            "uptime_seconds": uptime,
        }

    @staticmethod
    def _child_state(child: ManagedChild) -> str:
        if child.popen is not None:
            return "running"
        if child.next_retry_at is not None:
            return "restarting"
        return "stopped"

    def _write_port_resolution(self, resolution: PortResolution) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        path = self._state_dir / PORT_RESOLUTION_FILENAME
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(resolution.model_dump(mode="json"), indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def _write_supervisor_snapshot(self) -> None:
        snapshot = SupervisorSnapshot(
            pid=os.getpid(),
            boot_id=self._boot_id,
            os_boot_id=self._os_boot_id,
            started_at=self._started_at_iso or self._clock().isoformat(),
            hub_port=self._hub_port,
            children=[
                ManagedChildSnapshot(root=root, port=child.port, pid=child.popen.pid)
                for root, child in self._children.items()
                if child.popen is not None
            ],
        )
        self._state_dir.mkdir(parents=True, exist_ok=True)
        path = self._state_dir / SUPERVISOR_SNAPSHOT_FILENAME
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(snapshot.model_dump(mode="json"), indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def _read_previous_supervisor_snapshot(self) -> SupervisorSnapshot | None:
        path = self._state_dir / SUPERVISOR_SNAPSHOT_FILENAME
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return SupervisorSnapshot.model_validate(data)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "previous boot's supervisor snapshot at %s is corrupt/unreadable (%s) -- "
                "skipping orphan reclamation this boot (best-effort, never blocks startup)",
                path,
                exc,
            )
            return None  # corrupt/unreadable -- best-effort, never blocks boot

    def _clear_supervisor_snapshot(self) -> None:
        """A clean `shutdown()` clears `supervisor.json` -- its ABSENCE at the next boot is
        exactly what tells orphan-reclamation "nothing to reclaim, the prior exit was clean"
        (HLD §5.2/§6.1 step 3)."""
        path = self._state_dir / SUPERVISOR_SNAPSHOT_FILENAME
        try:
            path.unlink()
        except FileNotFoundError:
            pass


__all__ = [
    "BACKOFF_BASE_SECONDS",
    "BACKOFF_CAP_SECONDS",
    "BACKOFF_FACTOR",
    "DEFAULT_CHILD_HOST",
    "DEFAULT_SHUTDOWN_GRACE_SECONDS",
    "FAST_FAIL_GRACE_SECONDS",
    "FAST_FAIL_MAX_ATTEMPTS",
    "LOGS_SUBDIR",
    "PORT_RESOLUTION_FILENAME",
    "SPAWN_STAGGER_SECONDS",
    "STABILITY_WINDOW_SECONDS",
    "SUPERVISOR_LOCK_FILENAME",
    "SUPERVISOR_SNAPSHOT_FILENAME",
    "ChildSpawner",
    "ManagedChild",
    "ManagedChildSnapshot",
    "RestartBackoff",
    "Supervisor",
    "SupervisorLockHeldError",
    "SupervisorSnapshot",
    "acquire_singleton_lock",
    "default_child_spawner",
    "release_singleton_lock",
]
