"""Tests for `agent_orchestrator.isolation.runlock` (E-Wk9Tz3 T-Wl2Bq7, HLD §12.3).

Uses this package's own autouse `_isolated_git_env` fixture (`conftest.py`) so
`AO_STATE_DIR` is always redirected under `tmp_path` -- no test here ever touches the real
`~/.local/state/ao`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_orchestrator.errors import WorkspaceLockHeldError
from agent_orchestrator.isolation.runlock import WorkspaceRunLock, _lock_path_for

# ---------------------------------------------------------------------------------------
# Acquire / release
# ---------------------------------------------------------------------------------------


class TestAcquireRelease:
    def test_acquire_grants_when_uncontended(self, tmp_path: Path) -> None:
        ws = str(tmp_path / "workspace")
        lock = WorkspaceRunLock(ws, "run-a")
        claim = lock.acquire()
        assert claim.granted is True
        assert claim.reclaimed is False
        assert claim.holder_run_id is None
        lock.release()

    def test_lock_file_written_at_the_documented_path(self, tmp_path: Path) -> None:
        ws = str(tmp_path / "workspace")
        lock = WorkspaceRunLock(ws, "run-a")
        lock.acquire()
        expected = _lock_path_for(ws)
        assert lock.lock_path == expected
        assert expected.parent.name == "runlocks"
        assert expected.name.endswith(".lock")
        lock.release()

    def test_lock_file_content_is_deterministic_json(self, tmp_path: Path) -> None:
        ws = str(tmp_path / "workspace")
        fixed_at = "2026-09-07T00:00:00+00:00"
        lock = WorkspaceRunLock(
            ws,
            "run-a",
            clock=lambda: __import__("datetime").datetime.fromisoformat(fixed_at),
            boot_id_reader=lambda: "boot-fixed",
        )
        lock.acquire()
        raw = lock.lock_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
        assert payload == {
            "run_id": "run-a",
            "pid": os.getpid(),
            "boot_id": "boot-fixed",
            "at": fixed_at,
        }
        # Deterministic serialization: sorted keys, so byte content (not just parsed
        # equality) is reproducible given the same inputs.
        assert raw == json.dumps(payload, sort_keys=True)
        lock.release()

    def test_release_is_never_raising_even_when_never_acquired(self, tmp_path: Path) -> None:
        lock = WorkspaceRunLock(str(tmp_path / "workspace"), "run-a")
        lock.release()  # no-op, must not raise
        lock.release()  # idempotent

    def test_release_is_never_raising_after_a_normal_acquire(self, tmp_path: Path) -> None:
        ws = str(tmp_path / "workspace")
        lock = WorkspaceRunLock(ws, "run-a")
        lock.acquire()
        lock.release()
        lock.release()  # second release on an already-released instance: still a no-op

    def test_clean_release_unlinks_the_lock_file(self, tmp_path: Path) -> None:
        ws = str(tmp_path / "workspace")
        lock = WorkspaceRunLock(ws, "run-a")
        lock.acquire()
        assert lock.lock_path.exists()
        lock.release()
        assert not lock.lock_path.exists()

    def test_acquire_is_not_reentrant(self, tmp_path: Path) -> None:
        ws = str(tmp_path / "workspace")
        lock = WorkspaceRunLock(ws, "run-a")
        lock.acquire()
        with pytest.raises(RuntimeError):
            lock.acquire()
        lock.release()

    def test_two_workspaces_never_contend(self, tmp_path: Path) -> None:
        ws_a = str(tmp_path / "workspace-a")
        ws_b = str(tmp_path / "workspace-b")
        lock_a = WorkspaceRunLock(ws_a, "run-a")
        lock_b = WorkspaceRunLock(ws_b, "run-b")
        assert lock_a.acquire().granted is True
        assert lock_b.acquire().granted is True
        lock_a.release()
        lock_b.release()


# ---------------------------------------------------------------------------------------
# Second holder (same process, and a real second process)
# ---------------------------------------------------------------------------------------


class TestSecondHolder:
    def test_same_process_second_instance_is_denied_and_sees_is_held_by_other(
        self, tmp_path: Path
    ) -> None:
        ws = str(tmp_path / "workspace")
        holder = WorkspaceRunLock(ws, "run-a")
        assert holder.acquire().granted is True

        contender = WorkspaceRunLock(ws, "run-b")
        assert contender.is_held_by_other() is True
        claim = contender.acquire()
        assert claim.granted is False
        assert claim.holder_run_id == "run-a"
        assert claim.holder_pid == os.getpid()

        holder.release()
        assert contender.is_held_by_other() is False

    def test_holder_never_sees_itself_as_held_by_other(self, tmp_path: Path) -> None:
        ws = str(tmp_path / "workspace")
        lock = WorkspaceRunLock(ws, "run-a")
        lock.acquire()
        assert lock.is_held_by_other() is False
        lock.release()

    def test_real_second_process_is_denied_naming_the_holder(self, tmp_path: Path) -> None:
        ws = str(tmp_path / "workspace")
        script = Path(__file__).parent / "_runlock_hold_helper.py"
        proc = subprocess.Popen(
            [sys.executable, str(script), ws, "holder-run"],
            stdout=subprocess.PIPE,
            text=True,
            env=dict(os.environ),
        )
        try:
            line = proc.stdout.readline() if proc.stdout else ""
            assert line.strip() == "ACQUIRED"

            contender = WorkspaceRunLock(ws, "contender-run")
            claim = contender.acquire()
            assert claim.granted is False
            assert claim.holder_run_id == "holder-run"
            assert claim.holder_pid == proc.pid
        finally:
            proc.kill()
            proc.wait(timeout=5)

    def test_real_second_process_death_lets_the_next_run_reclaim(self, tmp_path: Path) -> None:
        ws = str(tmp_path / "workspace")
        script = Path(__file__).parent / "_runlock_hold_helper.py"
        proc = subprocess.Popen(
            [sys.executable, str(script), ws, "holder-run"],
            stdout=subprocess.PIPE,
            text=True,
            env=dict(os.environ),
        )
        line = proc.stdout.readline() if proc.stdout else ""
        assert line.strip() == "ACQUIRED"
        held_pid = proc.pid

        proc.kill()
        proc.wait(timeout=5)  # fully reaped -- flock is now guaranteed released by the OS

        successor = WorkspaceRunLock(ws, "successor-run")
        claim = successor.acquire()
        assert claim.granted is True
        assert claim.reclaimed is True
        assert claim.holder_run_id == "holder-run"
        assert claim.holder_pid == held_pid
        assert claim.stale_reason == "dead_pid"
        successor.release()


# ---------------------------------------------------------------------------------------
# Stale-lock reclamation (content-based, uncontended flock)
# ---------------------------------------------------------------------------------------


class TestStaleReclamation:
    def test_dead_pid_is_reclaimed(self, tmp_path: Path) -> None:
        ws = str(tmp_path / "workspace")
        # A real, definitely-dead pid: spawn a trivial subprocess and wait for it to exit.
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait(timeout=5)

        lock_path = _lock_path_for(ws)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(
            json.dumps({"run_id": "stale-run", "pid": dead.pid, "boot_id": "boot-X", "at": "x"})
        )

        lock = WorkspaceRunLock(ws, "new-run", boot_id_reader=lambda: "boot-X")
        claim = lock.acquire()
        assert claim.granted is True
        assert claim.reclaimed is True
        assert claim.stale_reason == "dead_pid"
        assert claim.holder_run_id == "stale-run"
        lock.release()

    def test_different_boot_id_is_reclaimed_even_with_a_live_pid(self, tmp_path: Path) -> None:
        ws = str(tmp_path / "workspace")
        lock_path = _lock_path_for(ws)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        # Our OWN pid is trivially "alive" -- proves boot-id mismatch alone overrides a
        # live-looking pid.
        lock_path.write_text(
            json.dumps(
                {"run_id": "stale-run", "pid": os.getpid(), "boot_id": "boot-OLD", "at": "x"}
            )
        )

        lock = WorkspaceRunLock(ws, "new-run", boot_id_reader=lambda: "boot-NEW")
        claim = lock.acquire()
        assert claim.granted is True
        assert claim.reclaimed is True
        assert claim.stale_reason == "boot_id_mismatch"
        lock.release()

    def test_dead_pid_checked_before_boot_id_when_both_could_apply(self, tmp_path: Path) -> None:
        ws = str(tmp_path / "workspace")
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait(timeout=5)
        lock_path = _lock_path_for(ws)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(
            json.dumps({"run_id": "stale-run", "pid": dead.pid, "boot_id": "boot-OLD", "at": "x"})
        )
        lock = WorkspaceRunLock(ws, "new-run", boot_id_reader=lambda: "boot-NEW")
        claim = lock.acquire()
        assert claim.stale_reason == "dead_pid"
        lock.release()

    def test_garbled_lock_file_content_is_tolerated_as_a_fresh_acquire(
        self, tmp_path: Path
    ) -> None:
        ws = str(tmp_path / "workspace")
        lock_path = _lock_path_for(ws)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_bytes(b"not json at all {{{")

        lock = WorkspaceRunLock(ws, "new-run")
        claim = lock.acquire()
        assert claim.granted is True
        lock.release()


# ---------------------------------------------------------------------------------------
# Context manager (typed error naming the holder)
# ---------------------------------------------------------------------------------------


class TestContextManager:
    def test_enter_grants_and_exit_releases(self, tmp_path: Path) -> None:
        ws = str(tmp_path / "workspace")
        with WorkspaceRunLock(ws, "run-a") as lock:
            assert lock.is_held_by_other() is False
        # released -- a fresh acquire elsewhere now succeeds.
        other = WorkspaceRunLock(ws, "run-b")
        assert other.acquire().granted is True
        other.release()

    def test_enter_raises_typed_error_naming_the_holder_when_denied(self, tmp_path: Path) -> None:
        ws = str(tmp_path / "workspace")
        holder = WorkspaceRunLock(ws, "run-a")
        holder.acquire()
        try:
            with pytest.raises(WorkspaceLockHeldError) as excinfo:
                with WorkspaceRunLock(ws, "run-b"):
                    pass
            assert excinfo.value.holder_run_id == "run-a"
            assert excinfo.value.holder_pid == os.getpid()
        finally:
            holder.release()

    def test_exit_releases_even_when_the_with_body_raises(self, tmp_path: Path) -> None:
        ws = str(tmp_path / "workspace")

        class _Boom(Exception):
            pass

        with pytest.raises(_Boom):
            with WorkspaceRunLock(ws, "run-a"):
                raise _Boom

        other = WorkspaceRunLock(ws, "run-b")
        assert other.acquire().granted is True
        other.release()


# ---------------------------------------------------------------------------------------
# Injectable clock / boot-id / pid-liveness
# ---------------------------------------------------------------------------------------


class TestInjectables:
    def test_pid_alive_injection_overrides_the_real_probe(self, tmp_path: Path) -> None:
        ws = str(tmp_path / "workspace")
        lock_path = _lock_path_for(ws)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        # A pid that IS genuinely alive (our own) -- but the injected oracle claims dead.
        lock_path.write_text(
            json.dumps({"run_id": "stale-run", "pid": os.getpid(), "boot_id": "same", "at": "x"})
        )
        lock = WorkspaceRunLock(
            ws,
            "new-run",
            pid_alive=lambda _pid: False,
            boot_id_reader=lambda: "same",
        )
        claim = lock.acquire()
        assert claim.granted is True
        assert claim.stale_reason == "dead_pid"
        lock.release()

    def test_clock_is_never_called_directly_from_time_module(self, tmp_path: Path) -> None:
        """CLAUDE.md determinism: a fixed injected clock produces a fixed timestamp,
        never real wall-clock time."""
        ws = str(tmp_path / "workspace")
        calls = {"n": 0}

        def _clock() -> object:
            calls["n"] += 1
            import datetime as _dt

            return _dt.datetime(2020, 1, 1, tzinfo=_dt.UTC)

        lock = WorkspaceRunLock(ws, "run-a", clock=_clock)  # type: ignore[arg-type]
        lock.acquire()
        assert calls["n"] == 1
        payload = json.loads(lock.lock_path.read_text())
        assert payload["at"] == "2020-01-01T00:00:00+00:00"
        lock.release()
