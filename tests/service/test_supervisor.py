"""Integration-flavored tests for `service.supervisor` -- fake children via real `sh -c`
subprocesses (no real network/uvicorn, no `[ui]` extra needed), fake/injectable clocks so
timing-sensitive assertions (backoff growth, stagger) never wait on real time."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_orchestrator.service.boot_resume import BootResumeGuard, Decision, ResumeCandidate
from agent_orchestrator.service.registry import ServiceRegistry, ServiceRegistryFile, WorkspaceEntry
from agent_orchestrator.service.supervisor import (
    BACKOFF_BASE_SECONDS,
    FAST_FAIL_GRACE_SECONDS,
    FAST_FAIL_MAX_ATTEMPTS,
    SPAWN_STAGGER_SECONDS,
    SUPERVISOR_SNAPSHOT_FILENAME,
    ManagedChildSnapshot,
    RestartBackoff,
    Supervisor,
    SupervisorLockHeldError,
    SupervisorSnapshot,
    acquire_singleton_lock,
    release_singleton_lock,
)

# -- shared fixtures/helpers -----------------------------------------------------------------


@pytest.fixture()
def state_dir(tmp_path: Path) -> Path:
    d = tmp_path / "state"
    d.mkdir()
    return d


@pytest.fixture()
def registry_path(tmp_path: Path) -> Path:
    return tmp_path / "service.yaml"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_until(predicate, timeout: float = 5.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _sleep_child_spawner(script: str = "sleep 100"):
    """A fake `child_spawner` running a real `sh -c` script instead of `ao ui`/uvicorn --
    ignores *root*/*port* entirely, ao AC6 intends for tests."""

    def _spawn(root: str, port: int, host: str, log_path: Path) -> subprocess.Popen:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "wb") as log_handle:
            return subprocess.Popen(  # noqa: S602, S603 - fixed test-only script, no shell injection
                ["sh", "-c", script],
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )

    return _spawn


class _FakeMonotonic:
    """Manually-advanced monotonic clock -- backoff/stagger assertions must never depend on
    real elapsed time."""

    def __init__(self, start: float = 0.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _no_sleep(_seconds: float) -> None:
    return None


def _decoy_child_process(root: str, port: int) -> subprocess.Popen:
    """A fake "previous incarnation's `ao ui` child" for orphan-reclamation tests.

    `_pid_plausibly_matches` (AC18) checks `/proc/<pid>/cmdline` for *root* and *port*
    substrings -- extra positional args after a `sh -c SCRIPT` become the script's `$0`/`$1`/
    ... and are NOT used by the script, but they DO appear verbatim in `/proc/<pid>/cmdline`
    (the real argv passed to `execve`), which is exactly what's being matched against.
    """
    return subprocess.Popen(
        ["sh", "-c", "sleep 100", "ao-ui-decoy", "--workspace", root, "--port", str(port)],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
    )


def _one_workspace_registry(
    registry_path: Path, ws: Path, *, port: int | None, autoresume: bool = False
) -> ServiceRegistryFile:
    """Build a single-workspace registry AND persist it to *registry_path* -- mirrors the
    real calling contract: `Supervisor.__init__`'s `registry` argument is assumed to already
    be reflected on disk at `registry_path` (a real caller does `ServiceRegistry().load()`
    immediately before constructing `Supervisor`), since `start()`'s port-persistence step
    (AC17) re-reads the CURRENT on-disk file under lock and merges onto it -- a registry
    that was never written to `registry_path` would look like it has zero workspaces to
    merge the resolved ports onto."""
    data = ServiceRegistryFile(
        workspaces=[WorkspaceEntry(root=str(ws.resolve()), port=port, autoresume=autoresume)]
    )
    ServiceRegistry(registry_path).save(data)
    return data


# -- RestartBackoff (AC5) ----------------------------------------------------------------------


class TestRestartBackoff:
    def test_next_delay_grows_exponentially_up_to_the_cap(self) -> None:
        backoff = RestartBackoff(base=1.0, factor=2.0, cap=60.0, stability_window=30.0)
        assert backoff.next_delay(0) == 1.0
        assert backoff.next_delay(1) == 2.0
        assert backoff.next_delay(2) == 4.0
        assert backoff.next_delay(3) == 8.0
        assert backoff.next_delay(10) == 60.0  # capped

    def test_is_stable_against_a_fake_monotonic_clock(self) -> None:
        backoff = RestartBackoff(stability_window=30.0)
        clock = _FakeMonotonic(start=100.0)
        stable_since = 100.0

        clock.advance(29.9)
        assert backoff.is_stable(clock() - stable_since) is False

        clock.advance(0.2)  # total 30.1s elapsed
        assert backoff.is_stable(clock() - stable_since) is True


# -- Singleton lock (AC15) ----------------------------------------------------------------------

_LOCK_HOLDER_SCRIPT = (
    "from pathlib import Path\n"
    "import sys, time\n"
    "from agent_orchestrator.service.supervisor import acquire_singleton_lock\n"
    # The handle MUST be bound to a name -- an unreferenced return value is immediately
    # garbage-collected by CPython's refcounting, which closes the fd and silently releases
    # the flock right after acquiring it.
    "_handle = acquire_singleton_lock(Path(sys.argv[1]))\n"
    "time.sleep(100)\n"
)


class TestSingletonLock:
    def test_acquire_then_release_allows_a_fresh_acquire(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "supervisor.lock"
        handle = acquire_singleton_lock(lock_path)
        release_singleton_lock(handle)

        handle2 = acquire_singleton_lock(lock_path)
        release_singleton_lock(handle2)

    def test_contention_raises_naming_the_holders_pid(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "supervisor.lock"
        holder = subprocess.Popen(
            [sys.executable, "-c", _LOCK_HOLDER_SCRIPT, str(lock_path)], stdin=subprocess.DEVNULL
        )
        try:
            assert _wait_until(lambda: lock_path.exists() and lock_path.read_text().strip() != "")
            with pytest.raises(SupervisorLockHeldError) as exc_info:
                acquire_singleton_lock(lock_path)
            assert str(holder.pid) in str(exc_info.value)
        finally:
            holder.kill()
            holder.wait()

    def test_a_killed_holder_releases_the_lock_for_a_fresh_acquire(self, tmp_path: Path) -> None:
        """AC15's mandated test: actually kill a subprocess holding the lock and confirm a
        fresh acquire succeeds -- not just an assertion about flock's documented semantics."""
        lock_path = tmp_path / "supervisor.lock"
        holder = subprocess.Popen(
            [sys.executable, "-c", _LOCK_HOLDER_SCRIPT, str(lock_path)], stdin=subprocess.DEVNULL
        )
        try:
            assert _wait_until(lambda: lock_path.exists() and lock_path.read_text().strip() != "")
            with pytest.raises(SupervisorLockHeldError):
                acquire_singleton_lock(lock_path)

            holder.kill()  # SIGKILL -- ungraceful, no chance to run any cleanup code
            holder.wait(timeout=5.0)

            handle = acquire_singleton_lock(lock_path)  # must now succeed
            release_singleton_lock(handle)
        finally:
            if holder.poll() is None:
                holder.kill()
                holder.wait()

    def test_supervisor_start_fails_fast_on_lock_contention(
        self, tmp_path: Path, state_dir: Path, registry_path: Path
    ) -> None:
        lock_path = state_dir / "supervisor.lock"
        holder = subprocess.Popen(
            [sys.executable, "-c", _LOCK_HOLDER_SCRIPT, str(lock_path)], stdin=subprocess.DEVNULL
        )
        try:
            assert _wait_until(lambda: lock_path.exists() and lock_path.read_text().strip() != "")
            supervisor = Supervisor(
                registry=ServiceRegistryFile(workspaces=[]),
                state_dir=state_dir,
                hub_port=8770,
                registry_path=registry_path,
                child_spawner=_sleep_child_spawner(),
            )
            with pytest.raises(SupervisorLockHeldError):
                supervisor.start()
        finally:
            holder.kill()
            holder.wait()


# -- monitor loop / restart backoff (AC8/AC13) --------------------------------------------------


class TestTickAndBackoff:
    def test_tick_detects_a_killed_child_and_restarts_it_after_backoff(
        self, tmp_path: Path, state_dir: Path, registry_path: Path
    ) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        clock = _FakeMonotonic(start=0.0)
        supervisor = Supervisor(
            registry=_one_workspace_registry(registry_path, ws, port=_free_port()),
            state_dir=state_dir,
            hub_port=8770,
            registry_path=registry_path,
            child_spawner=_sleep_child_spawner(),
            monotonic=clock,
            sleeper=_no_sleep,
        )
        supervisor.start()
        try:
            child = next(iter(supervisor._children.values()))
            original_pid = child.popen.pid

            os.kill(original_pid, signal.SIGKILL)
            assert _wait_until(lambda: child.popen.poll() is not None)

            supervisor.tick()
            assert child.popen is None
            assert child.next_retry_at == pytest.approx(BACKOFF_BASE_SECONDS)
            assert child.restart_count == 1

            clock.advance(BACKOFF_BASE_SECONDS)
            supervisor.tick()
            assert child.popen is not None
            assert child.popen.pid != original_pid
            assert child.popen.poll() is None
        finally:
            supervisor.shutdown(grace_seconds=2.0)

    def test_repeated_crashes_grow_the_backoff_delay(
        self, tmp_path: Path, state_dir: Path, registry_path: Path
    ) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        clock = _FakeMonotonic(start=0.0)
        supervisor = Supervisor(
            registry=_one_workspace_registry(registry_path, ws, port=_free_port()),
            state_dir=state_dir,
            hub_port=8770,
            registry_path=registry_path,
            child_spawner=_sleep_child_spawner(),
            monotonic=clock,
            sleeper=_no_sleep,
        )
        supervisor.start()
        try:
            child = next(iter(supervisor._children.values()))
            observed_delays: list[float] = []

            for _ in range(3):
                pid = child.popen.pid
                os.kill(pid, signal.SIGKILL)
                assert _wait_until(lambda: child.popen.poll() is not None)

                before = clock.value
                supervisor.tick()  # detects the crash, schedules next_retry_at
                observed_delays.append(child.next_retry_at - before)

                clock.advance(child.next_retry_at - before)
                supervisor.tick()  # respawns
                assert child.popen is not None
                assert child.popen.pid != pid

                # Stay "up" past the fast-fail grace window (but well under the 30s
                # stability window) so this test isolates pure backoff growth from the
                # EADDRINUSE backstop (a separate, dedicated test below).
                clock.advance(FAST_FAIL_GRACE_SECONDS + 1.0)

            assert observed_delays == [1.0, 2.0, 4.0]
        finally:
            supervisor.shutdown(grace_seconds=2.0)

    def test_stability_window_resets_backoff_bookkeeping(
        self, tmp_path: Path, state_dir: Path, registry_path: Path
    ) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        clock = _FakeMonotonic(start=0.0)
        backoff = RestartBackoff(stability_window=30.0)
        supervisor = Supervisor(
            registry=_one_workspace_registry(registry_path, ws, port=_free_port()),
            state_dir=state_dir,
            hub_port=8770,
            registry_path=registry_path,
            child_spawner=_sleep_child_spawner(),
            monotonic=clock,
            sleeper=_no_sleep,
        )
        supervisor.start()
        try:
            child = next(iter(supervisor._children.values()))
            os.kill(child.popen.pid, signal.SIGKILL)
            assert _wait_until(lambda: child.popen.poll() is not None)
            supervisor.tick()
            clock.advance(child.next_retry_at - clock.value)
            supervisor.tick()  # restart_count now 1
            assert child.restart_count == 1

            # Stay up past the stability window without crashing again.
            clock.advance(backoff.stability_window + 1.0)
            supervisor.tick()
            assert child.restart_count == 0
            assert child.fast_fail_count == 0
        finally:
            supervisor.shutdown(grace_seconds=2.0)


class TestEaddrinuseBackstop:
    def test_reassigns_port_after_n_consecutive_fast_failures(
        self, tmp_path: Path, state_dir: Path, registry_path: Path
    ) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        port = _free_port()
        clock = _FakeMonotonic(start=0.0)
        supervisor = Supervisor(
            registry=_one_workspace_registry(registry_path, ws, port=port),
            state_dir=state_dir,
            hub_port=8770,
            registry_path=registry_path,
            child_spawner=_sleep_child_spawner("exit 1"),  # exits immediately, every time
            monotonic=clock,
            sleeper=_no_sleep,
        )
        supervisor.start()
        try:
            child = next(iter(supervisor._children.values()))
            assert child.port == port

            for i in range(FAST_FAIL_MAX_ATTEMPTS):
                assert _wait_until(
                    lambda: child.popen is not None and child.popen.poll() is not None
                )
                supervisor.tick()  # detects the fast exit
                if i < FAST_FAIL_MAX_ATTEMPTS - 1:
                    assert child.reassignment_reason is None
                    clock.advance(child.next_retry_at - clock.value)
                    supervisor.tick()  # respawns on the SAME port, will fail fast again
                else:
                    assert child.reassignment_reason is not None
                    assert child.port != port
                    assert child.fast_fail_count == 0  # reset after reassignment
                    assert child.restart_count == 0

            # And the reassigned port is what the next spawn attempt actually uses.
            clock.advance(child.next_retry_at - clock.value)
            supervisor.tick()
            assert child.popen is not None
        finally:
            supervisor.shutdown(grace_seconds=2.0)


# -- shutdown (AC9) -----------------------------------------------------------------------------


class TestShutdown:
    def test_shutdown_terminates_children_within_grace_and_reaps_them(
        self, tmp_path: Path, state_dir: Path, registry_path: Path
    ) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        supervisor = Supervisor(
            registry=_one_workspace_registry(registry_path, ws, port=_free_port()),
            state_dir=state_dir,
            hub_port=8770,
            registry_path=registry_path,
            child_spawner=_sleep_child_spawner("sleep 100"),
        )
        supervisor.start()
        child = next(iter(supervisor._children.values()))
        pid = child.popen.pid
        assert child.popen.poll() is None  # alive before shutdown

        supervisor.shutdown(grace_seconds=5.0)

        assert child.popen is None
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)  # fully reaped, not a lingering zombie

    def test_shutdown_sigkills_a_child_that_ignores_sigterm(
        self, tmp_path: Path, state_dir: Path, registry_path: Path
    ) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        supervisor = Supervisor(
            registry=_one_workspace_registry(registry_path, ws, port=_free_port()),
            state_dir=state_dir,
            hub_port=8770,
            registry_path=registry_path,
            child_spawner=_sleep_child_spawner("trap : TERM; sleep 100"),
        )
        supervisor.start()
        child = next(iter(supervisor._children.values()))
        pid = child.popen.pid

        started = time.monotonic()
        supervisor.shutdown(grace_seconds=1.0)  # short so the test stays fast
        elapsed = time.monotonic() - started

        assert child.popen is None
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        assert elapsed >= 1.0, "must have waited out the grace period before SIGKILL"

    def test_shutdown_never_touches_a_process_outside_its_own_child_set(
        self, tmp_path: Path, state_dir: Path, registry_path: Path
    ) -> None:
        """The crux claim of the whole epic (NFR-1): a detached run survives a supervisor
        shutdown. Simulated by a decoy spawned the same way `ui/processes.py` spawns a run
        (`start_new_session=True`), entirely outside the Supervisor's own child set."""
        decoy = subprocess.Popen(
            ["sh", "-c", "sleep 100"], start_new_session=True, stdin=subprocess.DEVNULL
        )
        try:
            ws = tmp_path / "ws"
            ws.mkdir()
            supervisor = Supervisor(
                registry=_one_workspace_registry(registry_path, ws, port=_free_port()),
                state_dir=state_dir,
                hub_port=8770,
                registry_path=registry_path,
                child_spawner=_sleep_child_spawner("sleep 100"),
            )
            supervisor.start()
            supervisor.shutdown(grace_seconds=2.0)

            assert decoy.poll() is None, "a process outside the child set must not be touched"
        finally:
            decoy.kill()
            decoy.wait()

    def test_shutdown_clears_supervisor_json(
        self, tmp_path: Path, state_dir: Path, registry_path: Path
    ) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        supervisor = Supervisor(
            registry=_one_workspace_registry(registry_path, ws, port=_free_port()),
            state_dir=state_dir,
            hub_port=8770,
            registry_path=registry_path,
            child_spawner=_sleep_child_spawner(),
        )
        supervisor.start()
        assert (state_dir / SUPERVISOR_SNAPSHOT_FILENAME).is_file()

        supervisor.shutdown(grace_seconds=2.0)
        assert not (state_dir / SUPERVISOR_SNAPSHOT_FILENAME).exists()


# -- orphan reclamation (AC18) --------------------------------------------------------------------


class TestOrphanReclamation:
    def test_reclaims_a_still_alive_previous_child_matching_root_and_port(
        self, tmp_path: Path, state_dir: Path, registry_path: Path
    ) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        port = _free_port()
        decoy = _decoy_child_process(str(ws.resolve()), port)
        try:
            previous = SupervisorSnapshot(
                pid=999_999,
                boot_id="prev-boot",
                os_boot_id=None,
                started_at="2026-08-28T00:00:00+00:00",
                hub_port=8770,
                children=[ManagedChildSnapshot(root=str(ws.resolve()), port=port, pid=decoy.pid)],
            )
            (state_dir / SUPERVISOR_SNAPSHOT_FILENAME).write_text(previous.model_dump_json())

            supervisor = Supervisor(
                registry=_one_workspace_registry(registry_path, ws, port=port),
                state_dir=state_dir,
                hub_port=8770,
                registry_path=registry_path,
                child_spawner=_sleep_child_spawner("sleep 100"),
                boot_id="this-boot",
            )
            supervisor.start()

            assert _wait_until(lambda: decoy.poll() is not None), (
                "orphan must be SIGTERM'd before the replacement spawns"
            )

            new_child = next(iter(supervisor._children.values()))
            assert new_child.popen is not None
            assert new_child.popen.pid != decoy.pid

            supervisor.shutdown(grace_seconds=2.0)
        finally:
            if decoy.poll() is None:
                decoy.kill()
            decoy.wait()

    def test_does_not_touch_a_previous_child_whose_port_no_longer_matches(
        self, tmp_path: Path, state_dir: Path, registry_path: Path
    ) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        pinned_port = _free_port()
        stale_port = _free_port()
        decoy = subprocess.Popen(
            ["sh", "-c", "sleep 100"], start_new_session=True, stdin=subprocess.DEVNULL
        )
        try:
            previous = SupervisorSnapshot(
                pid=999_999,
                boot_id="prev-boot",
                os_boot_id=None,
                started_at="2026-08-28T00:00:00+00:00",
                hub_port=8770,
                children=[
                    ManagedChildSnapshot(root=str(ws.resolve()), port=stale_port, pid=decoy.pid)
                ],
            )
            (state_dir / SUPERVISOR_SNAPSHOT_FILENAME).write_text(previous.model_dump_json())

            supervisor = Supervisor(
                registry=_one_workspace_registry(registry_path, ws, port=pinned_port),
                state_dir=state_dir,
                hub_port=8770,
                registry_path=registry_path,
                child_spawner=_sleep_child_spawner("sleep 100"),
                boot_id="this-boot",
            )
            supervisor.start()

            time.sleep(0.2)  # give any (incorrect) reclamation attempt a moment to happen
            assert decoy.poll() is None, "a non-matching (root, port) must never be touched"

            supervisor.shutdown(grace_seconds=2.0)
        finally:
            if decoy.poll() is None:
                decoy.kill()
            decoy.wait()

    def test_same_boot_id_is_never_treated_as_a_previous_incarnation(
        self, tmp_path: Path, state_dir: Path, registry_path: Path
    ) -> None:
        # Defensive: a snapshot whose boot_id equals THIS boot's own id must never trigger
        # reclamation (it should be impossible in practice -- boot_id is fresh per instance
        # -- but guards against a caller re-using a boot_id by mistake).
        ws = tmp_path / "ws"
        ws.mkdir()
        port = _free_port()
        decoy = subprocess.Popen(
            ["sh", "-c", "sleep 100"], start_new_session=True, stdin=subprocess.DEVNULL
        )
        try:
            previous = SupervisorSnapshot(
                pid=999_999,
                boot_id="same-boot",
                os_boot_id=None,
                started_at="2026-08-28T00:00:00+00:00",
                hub_port=8770,
                children=[ManagedChildSnapshot(root=str(ws.resolve()), port=port, pid=decoy.pid)],
            )
            (state_dir / SUPERVISOR_SNAPSHOT_FILENAME).write_text(previous.model_dump_json())

            supervisor = Supervisor(
                registry=_one_workspace_registry(registry_path, ws, port=port),
                state_dir=state_dir,
                hub_port=8770,
                registry_path=registry_path,
                child_spawner=_sleep_child_spawner("sleep 100"),
                boot_id="same-boot",
            )
            supervisor.start()

            time.sleep(0.2)
            assert decoy.poll() is None

            supervisor.shutdown(grace_seconds=2.0)
        finally:
            if decoy.poll() is None:
                decoy.kill()
            decoy.wait()


# -- boot-resume idempotency re-check (AC16) -----------------------------------------------------


class _RecheckScenario:
    """Drives a fake `ProcessSupervisor`/`RunRepository` pair whose SCAN-time view (always a
    dead-pid, `status="running"` candidate) differs from their ACT-time (immediately-before-
    spawn) view -- simulating a human's dashboard Resume click, or the engine reaching a
    terminal status, landing in the same window as boot-resume's approval."""

    def __init__(self, run_id: str, *, recheck_is_running: bool, recheck_status: str) -> None:
        self.run_id = run_id
        self.recheck_is_running = recheck_is_running
        self.recheck_status = recheck_status
        self.load_state_calls = 0
        self.is_running_calls = 0
        self.launch_resume_called = False


class _FakeReconcileRecord:
    def __init__(self, run_id: str, finished_at: str) -> None:
        self.run_id = run_id
        self.finished_at = finished_at


class _FakeProcSupForRecheck:
    def __init__(self, root: str, scenario: _RecheckScenario) -> None:
        self._scenario = scenario

    def reconcile(self) -> list[_FakeReconcileRecord]:
        return [
            _FakeReconcileRecord(
                run_id=self._scenario.run_id, finished_at="2026-01-01T00:00:00+00:00"
            )
        ]

    def is_running(self, run_id: str) -> bool:
        self._scenario.is_running_calls += 1
        return self._scenario.recheck_is_running

    def launch_resume(self, run_id: str) -> None:
        self._scenario.launch_resume_called = True


class _FakeRepoForRecheck:
    def __init__(self, root: str, scenario: _RecheckScenario) -> None:
        self._scenario = scenario

    def load_state(self, run_id: str) -> SimpleNamespace:
        self._scenario.load_state_calls += 1
        # The FIRST call is scan's own candidacy check -- must be "running" to become a
        # candidate at all. Any later call is the Act-step recheck.
        status = (
            "running" if self._scenario.load_state_calls == 1 else self._scenario.recheck_status
        )
        return SimpleNamespace(status=status)


class TestBootResumeIdempotencyRecheck:
    def _start(
        self,
        tmp_path: Path,
        state_dir: Path,
        registry_path: Path,
        scenario: _RecheckScenario,
    ) -> Supervisor:
        ws = tmp_path / "ws"
        ws.mkdir()
        supervisor = Supervisor(
            registry=_one_workspace_registry(registry_path, ws, port=_free_port(), autoresume=True),
            state_dir=state_dir,
            hub_port=8770,
            registry_path=registry_path,
            child_spawner=_sleep_child_spawner(),
            boot_id="test-boot",
            process_supervisor_factory=lambda root: _FakeProcSupForRecheck(root, scenario),
            run_repository_factory=lambda root: _FakeRepoForRecheck(root, scenario),
        )
        supervisor.start()
        return supervisor

    def test_skips_without_burning_an_attempt_when_already_running_at_act_time(
        self, tmp_path: Path, state_dir: Path, registry_path: Path
    ) -> None:
        scenario = _RecheckScenario(
            run_id="run-1", recheck_is_running=True, recheck_status="running"
        )
        supervisor = self._start(tmp_path, state_dir, registry_path, scenario)
        try:
            assert scenario.is_running_calls == 1
            assert scenario.launch_resume_called is False
            decisions = supervisor.status_snapshot()["boot_resume_decisions"]
            assert decisions[0]["decision"] == "skip_recheck_already_running"

            # The attempt budget must NOT have been spent.
            guard = BootResumeGuard(state_dir)
            candidate = ResumeCandidate(
                workspace_root=str((tmp_path / "ws").resolve()), run_id="run-1"
            )
            assert guard.decide(candidate, "some-later-boot") == Decision.APPROVE
        finally:
            supervisor.shutdown(grace_seconds=2.0)

    def test_skips_without_burning_an_attempt_when_terminal_at_act_time(
        self, tmp_path: Path, state_dir: Path, registry_path: Path
    ) -> None:
        scenario = _RecheckScenario(
            run_id="run-1", recheck_is_running=False, recheck_status="succeeded"
        )
        supervisor = self._start(tmp_path, state_dir, registry_path, scenario)
        try:
            assert scenario.launch_resume_called is False
            decisions = supervisor.status_snapshot()["boot_resume_decisions"]
            assert decisions[0]["decision"] == "skip_recheck_terminal"

            guard = BootResumeGuard(state_dir)
            candidate = ResumeCandidate(
                workspace_root=str((tmp_path / "ws").resolve()), run_id="run-1"
            )
            assert guard.decide(candidate, "some-later-boot") == Decision.APPROVE
        finally:
            supervisor.shutdown(grace_seconds=2.0)

    def test_approves_and_launches_when_still_genuinely_a_candidate_at_act_time(
        self, tmp_path: Path, state_dir: Path, registry_path: Path
    ) -> None:
        scenario = _RecheckScenario(
            run_id="run-1", recheck_is_running=False, recheck_status="running"
        )
        supervisor = self._start(tmp_path, state_dir, registry_path, scenario)
        try:
            assert scenario.launch_resume_called is True
            decisions = supervisor.status_snapshot()["boot_resume_decisions"]
            assert decisions[0]["decision"] == "approve"

            guard = BootResumeGuard(state_dir)
            candidate = ResumeCandidate(
                workspace_root=str((tmp_path / "ws").resolve()), run_id="run-1"
            )
            assert guard.decide(candidate, "test-boot") == Decision.SKIP_ALREADY_THIS_BOOT
        finally:
            supervisor.shutdown(grace_seconds=2.0)


# -- port persistence via the locked mutate() (AC17) ---------------------------------------------


class TestPortPersistence:
    def test_p3_picked_port_is_persisted_via_locked_mutate(
        self, tmp_path: Path, state_dir: Path, registry_path: Path
    ) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        supervisor = Supervisor(
            registry=_one_workspace_registry(registry_path, ws, port=None),
            state_dir=state_dir,
            hub_port=8770,
            registry_path=registry_path,
            child_spawner=_sleep_child_spawner(),
        )
        supervisor.start()
        try:
            child = next(iter(supervisor._children.values()))
            persisted = ServiceRegistry(registry_path).load()
            assert persisted.workspaces[0].port == child.port
        finally:
            supervisor.shutdown(grace_seconds=2.0)

    def test_does_not_clobber_a_concurrently_added_workspace(
        self, tmp_path: Path, state_dir: Path, registry_path: Path
    ) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        concurrent_ws = tmp_path / "concurrent"
        concurrent_ws.mkdir()

        # The on-disk registry already has an extra workspace this Supervisor's OWN
        # `registry` snapshot never saw -- simulates `ao service add` racing the boot.
        ServiceRegistry(registry_path).save(
            ServiceRegistryFile(
                workspaces=[
                    WorkspaceEntry(root=str(ws.resolve()), port=None, autoresume=False),
                    WorkspaceEntry(root=str(concurrent_ws.resolve()), port=None, autoresume=False),
                ]
            )
        )
        # Deliberately NOT persisted via _one_workspace_registry -- this snapshot pre-dates
        # the concurrent add already saved to registry_path above, and must stay stale.
        stale_registry = ServiceRegistryFile(
            workspaces=[WorkspaceEntry(root=str(ws.resolve()), port=None, autoresume=False)]
        )
        supervisor = Supervisor(
            registry=stale_registry,
            state_dir=state_dir,
            hub_port=8770,
            registry_path=registry_path,
            child_spawner=_sleep_child_spawner(),
        )
        supervisor.start()
        try:
            persisted = ServiceRegistry(registry_path).load()
            roots = {w.root for w in persisted.workspaces}
            assert str(concurrent_ws.resolve()) in roots
        finally:
            supervisor.shutdown(grace_seconds=2.0)


# -- status_snapshot / persisted files (AC10) ------------------------------------------------------


class TestStatusSnapshot:
    def test_shape_and_fields(self, tmp_path: Path, state_dir: Path, registry_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        port = _free_port()
        clock = _FakeMonotonic(start=0.0)
        supervisor = Supervisor(
            registry=_one_workspace_registry(registry_path, ws, port=port),
            state_dir=state_dir,
            hub_port=8770,
            registry_path=registry_path,
            child_spawner=_sleep_child_spawner(),
            monotonic=clock,
            sleeper=_no_sleep,
        )
        supervisor.start()
        try:
            clock.advance(5.0)
            snapshot = supervisor.status_snapshot()

            assert snapshot["hub_port"] == 8770
            assert snapshot["supervisor_pid"] == os.getpid()
            assert snapshot["uptime_seconds"] == pytest.approx(5.0)
            assert snapshot["conflicts"] == []
            assert isinstance(snapshot["boot_resume_decisions"], list)

            [entry] = snapshot["workspaces"]
            assert entry["root"] == str(ws.resolve())
            assert entry["port"] == port
            assert entry["state"] == "running"
            assert entry["pid"] is not None
            assert entry["restart_count"] == 0
            assert entry["last_error"] is None
            assert entry["log_path"] is not None
            assert entry["reassignment_reason"] is None
        finally:
            supervisor.shutdown(grace_seconds=2.0)

    def test_writes_supervisor_json_and_port_resolution_json(
        self, tmp_path: Path, state_dir: Path, registry_path: Path
    ) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        supervisor = Supervisor(
            registry=_one_workspace_registry(registry_path, ws, port=None),
            state_dir=state_dir,
            hub_port=8770,
            registry_path=registry_path,
            child_spawner=_sleep_child_spawner(),
        )
        supervisor.start()
        try:
            assert (state_dir / "supervisor.json").is_file()
            assert (state_dir / "port_resolution.json").is_file()
        finally:
            supervisor.shutdown(grace_seconds=2.0)


# -- inter-spawn stagger (AC22) -------------------------------------------------------------------


class TestSpawnStagger:
    def test_sleeper_invoked_once_per_spawned_child(
        self, tmp_path: Path, state_dir: Path, registry_path: Path
    ) -> None:
        ws1 = tmp_path / "ws1"
        ws1.mkdir()
        ws2 = tmp_path / "ws2"
        ws2.mkdir()
        registry = ServiceRegistryFile(
            workspaces=[
                WorkspaceEntry(root=str(ws1.resolve()), port=_free_port(), autoresume=False),
                WorkspaceEntry(root=str(ws2.resolve()), port=_free_port(), autoresume=False),
            ]
        )
        ServiceRegistry(registry_path).save(registry)
        sleep_calls: list[float] = []
        supervisor = Supervisor(
            registry=registry,
            state_dir=state_dir,
            hub_port=8770,
            registry_path=registry_path,
            child_spawner=_sleep_child_spawner(),
            sleeper=sleep_calls.append,
        )
        supervisor.start()
        try:
            assert len(sleep_calls) == 2
            assert all(c == pytest.approx(SPAWN_STAGGER_SECONDS) for c in sleep_calls)
        finally:
            supervisor.shutdown(grace_seconds=2.0)


# -- basic start() behavior ------------------------------------------------------------------


class TestStartBasic:
    def test_empty_registry_spawns_nothing_and_does_not_error(
        self, tmp_path: Path, state_dir: Path, registry_path: Path
    ) -> None:
        supervisor = Supervisor(
            registry=ServiceRegistryFile(workspaces=[]),
            state_dir=state_dir,
            hub_port=8770,
            registry_path=registry_path,
            child_spawner=_sleep_child_spawner(),
        )
        supervisor.start()
        assert supervisor._children == {}
        supervisor.shutdown(grace_seconds=2.0)

    def test_one_child_per_workspace(
        self, tmp_path: Path, state_dir: Path, registry_path: Path
    ) -> None:
        ws1 = tmp_path / "ws1"
        ws1.mkdir()
        ws2 = tmp_path / "ws2"
        ws2.mkdir()
        registry = ServiceRegistryFile(
            workspaces=[
                WorkspaceEntry(root=str(ws1.resolve()), port=_free_port(), autoresume=False),
                WorkspaceEntry(root=str(ws2.resolve()), port=_free_port(), autoresume=False),
            ]
        )
        ServiceRegistry(registry_path).save(registry)
        supervisor = Supervisor(
            registry=registry,
            state_dir=state_dir,
            hub_port=8770,
            registry_path=registry_path,
            child_spawner=_sleep_child_spawner(),
        )
        supervisor.start()
        try:
            assert set(supervisor._children) == {str(ws1.resolve()), str(ws2.resolve())}
            for child in supervisor._children.values():
                assert child.popen is not None
                assert child.popen.poll() is None
        finally:
            supervisor.shutdown(grace_seconds=2.0)


class TestPerWorkspaceHost:
    """The registry `host` pin (P2) reaches the spawner and the status snapshot."""

    def test_registry_host_reaches_spawner_and_snapshot(
        self, tmp_path: Path, state_dir: Path, registry_path: Path
    ) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        registry = ServiceRegistryFile(
            workspaces=[WorkspaceEntry(root=str(ws), port=_free_port(), host="0.0.0.0")]
        )
        ServiceRegistry(path=registry_path).save(registry)

        spawned_hosts: list[str] = []
        real_spawner = _sleep_child_spawner()

        def _recording_spawner(root: str, port: int, host: str, log_path: Path) -> subprocess.Popen:
            spawned_hosts.append(host)
            return real_spawner(root, port, host, log_path)

        supervisor = Supervisor(
            registry=registry,
            state_dir=state_dir,
            hub_port=8770,
            registry_path=registry_path,
            child_spawner=_recording_spawner,
            sleeper=_no_sleep,
        )
        supervisor.start()
        try:
            assert spawned_hosts == ["0.0.0.0"]
            snapshot = supervisor.status_snapshot()
            assert snapshot["workspaces"][0]["host"] == "0.0.0.0"
        finally:
            supervisor.shutdown(grace_seconds=2.0)

    def test_default_host_is_loopback(
        self, tmp_path: Path, state_dir: Path, registry_path: Path
    ) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        spawned_hosts: list[str] = []
        real_spawner = _sleep_child_spawner()

        def _recording_spawner(root: str, port: int, host: str, log_path: Path) -> subprocess.Popen:
            spawned_hosts.append(host)
            return real_spawner(root, port, host, log_path)

        supervisor = Supervisor(
            registry=_one_workspace_registry(registry_path, ws, port=_free_port()),
            state_dir=state_dir,
            hub_port=8770,
            registry_path=registry_path,
            child_spawner=_recording_spawner,
            sleeper=_no_sleep,
        )
        supervisor.start()
        try:
            assert spawned_hosts == ["127.0.0.1"]
        finally:
            supervisor.shutdown(grace_seconds=2.0)
