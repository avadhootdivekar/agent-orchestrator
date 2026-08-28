"""Tests for `service.boot_resume`: scan (AC1), decide/record bookkeeping (AC2/AC3), and
characterization tests pinning the three `ProcessSupervisor.reconcile()` behaviors this
module relies on but does not itself define (AC23)."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_orchestrator.service.boot_resume import (
    BootResumeGuard,
    Decision,
    ResumeCandidate,
    scan_resumable_runs,
)
from agent_orchestrator.ui.processes import LaunchRecord, ProcessSupervisor

from ..ui.conftest import make_run_state, write_run


def _dead_pid() -> int:
    """A PID guaranteed to be fully exited and reaped by THIS process (not a zombie) --
    `os.kill(pid, 0)` reports it accurately as gone. Simulates a launch whose owning process
    (the dashboard, or a previous supervisor boot) has since exited entirely, which is what
    every real boot-resume candidate looks like."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _persist_record(
    workspace: Path,
    *,
    launch_id: str,
    pid: int,
    run_id: str | None,
    finished_at: str | None = None,
    exit_code: int | None = None,
) -> LaunchRecord:
    """Persist a `LaunchRecord` directly (bypassing `launch_run`/`launch_resume`) so no
    `ProcessSupervisor` instance ever tracks a live `Popen` for *pid* -- exactly the shape a
    real cross-process/cross-boot record has, and the shape AC23's characterization tests
    need to isolate "was this ever this instance's own child" from "is the pid alive"."""
    record = LaunchRecord(
        launch_id=launch_id,
        kind="resume",
        pid=pid,
        argv=["ao", "resume", "--run-id", run_id or "unknown"],
        started_at="2026-08-28T00:00:00+00:00",
        log_path=str(workspace / "unused.log"),
        run_id=run_id,
        finished_at=finished_at,
        exit_code=exit_code,
    )
    # Reuses ProcessSupervisor's own (private) save so the on-disk shape is byte-identical
    # to what production writes, rather than re-deriving the JSON layout by hand.
    ProcessSupervisor(str(workspace))._save(record)  # noqa: SLF001
    return record


class TestScanResumableRuns:
    def test_alive_pid_is_never_a_candidate(self, tmp_path: Path) -> None:
        _persist_record(tmp_path, launch_id="l1", pid=os.getpid(), run_id="run-1")
        write_run(tmp_path, make_run_state(run_id="run-1", status="running"))

        assert scan_resumable_runs(str(tmp_path)) == []

    def test_dead_pid_with_succeeded_status_is_never_a_candidate(self, tmp_path: Path) -> None:
        _persist_record(tmp_path, launch_id="l1", pid=_dead_pid(), run_id="run-1")
        write_run(tmp_path, make_run_state(run_id="run-1", status="succeeded"))

        assert scan_resumable_runs(str(tmp_path)) == []

    def test_dead_pid_with_running_status_is_a_candidate(self, tmp_path: Path) -> None:
        _persist_record(tmp_path, launch_id="l1", pid=_dead_pid(), run_id="run-1")
        write_run(tmp_path, make_run_state(run_id="run-1", status="running"))

        candidates = scan_resumable_runs(str(tmp_path))
        assert len(candidates) == 1
        assert candidates[0].run_id == "run-1"
        assert candidates[0].workspace_root == str(tmp_path.resolve())

    def test_record_with_no_run_id_is_never_a_candidate(self, tmp_path: Path) -> None:
        _persist_record(tmp_path, launch_id="l1", pid=_dead_pid(), run_id=None)
        assert scan_resumable_runs(str(tmp_path)) == []

    def test_run_state_missing_is_never_a_candidate(self, tmp_path: Path) -> None:
        # A dead pid pointing at a run_id with no state.json on disk at all (pruned/never
        # written) must not raise -- defensive, not expected in practice.
        _persist_record(tmp_path, launch_id="l1", pid=_dead_pid(), run_id="never-written")
        assert scan_resumable_runs(str(tmp_path)) == []


class TestReconcileCharacterization:
    """Pins the three `ProcessSupervisor.reconcile()` behaviors `scan_resumable_runs` relies
    on but does not itself define (AC23) -- `ui/processes.py` is out of scope for this epic
    and could change these for dashboard-only reasons; these tests exist so that would fail
    THIS suite loudly instead of silently breaking boot-resume."""

    def test_reconcile_returns_every_record_not_just_live_ones(self, tmp_path: Path) -> None:
        _persist_record(tmp_path, launch_id="alive", pid=os.getpid(), run_id="run-alive")
        _persist_record(tmp_path, launch_id="dead", pid=_dead_pid(), run_id="run-dead")

        records = ProcessSupervisor(str(tmp_path)).reconcile()
        assert {r.launch_id for r in records} == {"alive", "dead"}

    def test_reconcile_sets_finished_at_on_a_dead_pid_record(self, tmp_path: Path) -> None:
        _persist_record(tmp_path, launch_id="dead", pid=_dead_pid(), run_id="run-1")

        records = ProcessSupervisor(str(tmp_path)).reconcile()
        assert records[0].finished_at is not None

        # And it was actually persisted, not just mutated in memory.
        reloaded = ProcessSupervisor(str(tmp_path)).load_records()
        assert reloaded[0].finished_at is not None

    def test_reconcile_leaves_exit_code_none_for_a_record_never_popened_by_this_instance(
        self, tmp_path: Path
    ) -> None:
        # This record's pid was never tracked by ANY ProcessSupervisor's Popen table (it was
        # spawned and reaped directly by the test) -- exactly `scan_resumable_runs`'s own
        # usage pattern (`supervisor_factory(root)` always constructs a fresh instance).
        _persist_record(tmp_path, launch_id="dead", pid=_dead_pid(), run_id="run-1")

        records = ProcessSupervisor(str(tmp_path)).reconcile()
        assert records[0].finished_at is not None
        assert records[0].exit_code is None


class _FakeClock:
    """Manually-advanced clock -- boot-resume cooldown/quarantine tests must never wait on
    real time (CLAUDE.md determinism rule)."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


@pytest.fixture()
def fake_clock() -> _FakeClock:
    return _FakeClock(datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC))


class TestBootResumeGuard:
    def test_first_attempt_is_approved(self, tmp_path: Path, fake_clock: _FakeClock) -> None:
        guard = BootResumeGuard(tmp_path, clock=fake_clock)
        candidate = ResumeCandidate(workspace_root=str(tmp_path), run_id="run-1")
        assert guard.decide(candidate, "boot-A") == Decision.APPROVE

    def test_dedups_within_one_boot(self, tmp_path: Path, fake_clock: _FakeClock) -> None:
        guard = BootResumeGuard(tmp_path, clock=fake_clock)
        candidate = ResumeCandidate(workspace_root=str(tmp_path), run_id="run-1")

        assert guard.decide(candidate, "boot-A") == Decision.APPROVE
        guard.record_attempt(candidate, "boot-A")

        # Same boot, later "tick" -- must not re-attempt even though cooldown has not
        # elapsed (dedup is checked FIRST, ahead of cooldown, per the HLD §6.4 rule order).
        assert guard.decide(candidate, "boot-A") == Decision.SKIP_ALREADY_THIS_BOOT

    def test_enforces_cooldown_across_two_different_boots(
        self, tmp_path: Path, fake_clock: _FakeClock
    ) -> None:
        guard = BootResumeGuard(tmp_path, clock=fake_clock, cooldown_seconds=60.0)
        candidate = ResumeCandidate(workspace_root=str(tmp_path), run_id="run-1")

        guard.record_attempt(candidate, "boot-A")

        # A DIFFERENT boot id (e.g. a rapid supervisor restart) within the cooldown window
        # must still be blocked -- this is precisely what stops a poisoned run from
        # loop-resuming across rapid restart cycles (per-boot dedup alone cannot catch it).
        fake_clock.advance(30.0)
        assert guard.decide(candidate, "boot-B") == Decision.SKIP_COOLDOWN

        # Once the cooldown window has fully elapsed, a new boot may try again.
        fake_clock.advance(31.0)  # total 61s since the attempt
        assert guard.decide(candidate, "boot-B") == Decision.APPROVE

    def test_quarantines_after_max_attempts(self, tmp_path: Path, fake_clock: _FakeClock) -> None:
        guard = BootResumeGuard(tmp_path, clock=fake_clock, max_attempts=3, cooldown_seconds=0.0)
        candidate = ResumeCandidate(workspace_root=str(tmp_path), run_id="run-1")

        for i in range(3):
            assert guard.decide(candidate, f"boot-{i}") == Decision.APPROVE
            guard.record_attempt(candidate, f"boot-{i}")

        # A 4th distinct boot, well past any cooldown, is still refused -- quarantined.
        assert guard.decide(candidate, "boot-3") == Decision.SKIP_QUARANTINED

    def test_quarantine_takes_priority_over_a_since_expired_cooldown(
        self, tmp_path: Path, fake_clock: _FakeClock
    ) -> None:
        # Rule order (HLD §6.4): dedup -> quarantine -> cooldown -> approve. Once quarantined,
        # it must stay quarantined even though enough time has passed that cooldown alone
        # would no longer be the blocking reason.
        guard = BootResumeGuard(tmp_path, clock=fake_clock, max_attempts=1, cooldown_seconds=1.0)
        candidate = ResumeCandidate(workspace_root=str(tmp_path), run_id="run-1")
        guard.record_attempt(candidate, "boot-A")

        fake_clock.advance(1000.0)  # cooldown long expired
        assert guard.decide(candidate, "boot-B") == Decision.SKIP_QUARANTINED

    def test_record_attempt_persists_immediately_and_survives_a_fresh_instance(
        self, tmp_path: Path, fake_clock: _FakeClock
    ) -> None:
        candidate = ResumeCandidate(workspace_root=str(tmp_path), run_id="run-1")
        BootResumeGuard(tmp_path, clock=fake_clock).record_attempt(candidate, "boot-A")

        # A fresh instance (simulating a crash-and-restart between two resume attempts)
        # must see the persisted attempt count, not start over.
        reloaded = BootResumeGuard(tmp_path, clock=fake_clock)
        assert reloaded.decide(candidate, "boot-A") == Decision.SKIP_ALREADY_THIS_BOOT

    def test_key_is_scoped_by_both_workspace_root_and_run_id(
        self, tmp_path: Path, fake_clock: _FakeClock
    ) -> None:
        guard = BootResumeGuard(tmp_path, clock=fake_clock)
        ws_a = tmp_path / "a"
        ws_b = tmp_path / "b"
        ws_a.mkdir()
        ws_b.mkdir()
        candidate_a = ResumeCandidate(workspace_root=str(ws_a), run_id="run-1")
        candidate_b = ResumeCandidate(workspace_root=str(ws_b), run_id="run-1")

        guard.record_attempt(candidate_a, "boot-A")

        # Same run_id, different workspace -- must be an independent bookkeeping entry.
        assert guard.decide(candidate_b, "boot-A") == Decision.APPROVE
