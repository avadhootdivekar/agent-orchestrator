"""Tests for E-Wk9Tz3 T-Wl2Bq7 (workspace run lock + R-12 checkout-sync diagnostics, HLD
§12.3, ADR-0013 D8).

Reuses `tests/test_engine_isolation.py`'s own test scaffolding (`_git`, `_git_repo`,
`_workspace`, `_agents`, `_reposet`, `_task`, `_workflow`, `_instructions`, `_orch`, `_run`,
and its autouse `_isolated_git_env` fixture) by import -- this repo's own established
cross-file test-helper-reuse convention (`tests/test_e2e_cli_prune_worktrees.py`,
`tests/test_cli_isolation_flags.py`, `tests/test_wave_concurrency_semantics.py` all do the
same). No new fixture-building duplicated here beyond what this file's own scenarios need.

Covers (top-level task assignment's "Tests" section, in file order below):
- `workspace_lock: "require"` (default): a live holder degrades this run to
  `isolation: none` with the holder named in ONE warning; `isolation.strict` turns that
  into a run failure.
- `"skip_sync"`: isolates and lands regardless of the lock's own claim outcome, but the
  shared checkout's HEAD is provably never touched.
- `"off"`: acquires nothing (no lock file at all) and warns once; sync itself still
  proceeds normally (only the PROTECTION is disabled, not the sync).
- `_sync_checkout`'s R-12 diagnostics: a clean fast-forward at a barrier; a genuine
  dirty-path collision (named, no data loss); a diverged (non-fast-forward) checkout;
  never invoked when nothing is isolated; only `merge`/`rebase`-free (ref/index-safe)
  commands ever run against the SHARED checkout.
- Resume takeover reclaims a stale lock.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from pathlib import Path

import pytest

from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors.base import Executor
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.isolation import git as git_module
from agent_orchestrator.isolation.git import _SubprocessRunner
from agent_orchestrator.isolation.runlock import WorkspaceRunLock, _lock_path_for
from agent_orchestrator.models import IntegrationSpec, TaskContext, TaskResult
from tests.test_engine_isolation import (
    _agents,
    _git,
    _git_repo,
    _instructions,
    _isolated_git_env,  # noqa: F401 -- pytest autouse fixture, registered by import
    _orch,
    _reposet,
    _run,
    _task,
    _workflow,
    _workspace,
)

_COMMIT_ARGS = ["-c", "user.name=ao-test", "-c", "user.email=ao-test@example.invalid"]


def _events(caplog: pytest.LogCaptureFixture, name: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "event", None) == name]


# ---------------------------------------------------------------------------------------
# `workspace_lock` policy behaviour
# ---------------------------------------------------------------------------------------


class TestWorkspaceLockRequirePolicy:
    def test_second_run_degrades_to_none_with_one_warning_naming_the_holder(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])

        holder = WorkspaceRunLock(str(tmp_path), "holder-run")
        assert holder.acquire().granted is True
        try:
            orch = _orch(tmp_path)
            with caplog.at_level(logging.WARNING, logger="agent_orchestrator"):
                state = _run(orch, wf, tmp_path)
        finally:
            holder.release()

        # Default (non-strict): degrades cleanly, run still succeeds.
        assert state.status == "succeeded"
        assert state.integration.active is False
        assert state.integration.workspace_lock_held is False
        assert state.integration.degraded_reason == "workspace_locked:holder-run"

        degraded = _events(caplog, "integration.degraded")
        assert len(degraded) == 1  # exactly ONE warning, per AC
        assert degraded[0].levelno == logging.WARNING
        assert degraded[0].holder_run_id == "holder-run"
        assert degraded[0].holder_pid == os.getpid()

    def test_strict_turns_the_denial_into_a_run_failure(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])

        holder = WorkspaceRunLock(str(tmp_path), "holder-run")
        assert holder.acquire().granted is True
        try:
            orch = _orch(tmp_path, isolation_strict=True)
            with caplog.at_level(logging.ERROR, logger="agent_orchestrator"):
                state = _run(orch, wf, tmp_path)
        finally:
            holder.release()

        assert state.status == "failed"
        assert state.integration.degraded_reason == "workspace_locked:holder-run"
        errors = _events(caplog, "integration.degraded")
        assert len(errors) == 1
        assert errors[0].levelno == logging.ERROR

    def test_lock_is_released_after_a_successful_run(self, tmp_path: Path) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        fake = FakeExecutor(repo_writes={"a": {"core": {"landed.txt": "x\n"}}})
        orch = _orch(tmp_path, executor=fake)
        state = _run(orch, wf, tmp_path)
        assert state.status == "succeeded"
        assert state.integration.workspace_lock_held is True
        # Released -- a fresh acquire elsewhere now succeeds uncontended.
        probe = WorkspaceRunLock(str(tmp_path), "probe-run")
        assert probe.acquire().granted is True
        probe.release()

    def test_lock_is_released_even_when_the_run_fails(self, tmp_path: Path) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])

        class _BoomExecutor(Executor):
            def execute(self, ctx: TaskContext) -> TaskResult:
                raise RuntimeError("boom")

        orch = _orch(tmp_path, executor=_BoomExecutor())
        with pytest.raises(RuntimeError):
            _run(orch, wf, tmp_path)

        probe = WorkspaceRunLock(str(tmp_path), "probe-run")
        assert probe.acquire().granted is True
        probe.release()


class TestWorkspaceLockSkipSyncPolicy:
    def test_isolates_and_lands_leaving_the_shared_checkout_head_unchanged(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        repo = _git_repo(tmp_path)
        pre_head = _git(["rev-parse", "HEAD"], repo).strip()
        wf = _workflow(
            [
                _task("a", outputs=["out/a.txt"], isolation="worktree"),
                _task("b", depends_on=["a"], outputs=["out/b.txt"], isolation="none"),
            ],
            integration=IntegrationSpec(workspace_lock="skip_sync"),
        )
        fake = FakeExecutor(repo_writes={"a": {"core": {"landed.txt": "x\n"}}})
        orch = _orch(tmp_path, executor=fake)
        with caplog.at_level(logging.INFO, logger="agent_orchestrator"):
            state = _run(orch, wf, tmp_path)

        assert state.status == "succeeded"
        assert state.task_integration["a"].status == "integrated"
        # "b" (non-isolated) still ran -- skip_sync never blocks dispatch, only the sync.
        assert state.tasks["b"].status == "succeeded"
        # The shared checkout's HEAD is PROVABLY unchanged: never fast-forwarded.
        assert _git(["rev-parse", "HEAD"], repo).strip() == pre_head
        assert not (repo / "landed.txt").exists()

        skipped = _events(caplog, "integration.sync_skipped")
        assert any(r.reason == "workspace_lock=skip_sync" for r in skipped)

    def test_proceeds_even_when_the_lock_claim_is_denied(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow(
            [_task("a", outputs=["out/a.txt"], isolation="worktree")],
            integration=IntegrationSpec(workspace_lock="skip_sync"),
        )
        fake = FakeExecutor(repo_writes={"a": {"core": {"landed.txt": "x\n"}}})

        holder = WorkspaceRunLock(str(tmp_path), "holder-run")
        assert holder.acquire().granted is True
        try:
            orch = _orch(tmp_path, executor=fake)
            with caplog.at_level(logging.WARNING, logger="agent_orchestrator"):
                state = _run(orch, wf, tmp_path)
        finally:
            holder.release()

        assert state.status == "succeeded"
        assert state.integration.active is True  # isolation activates regardless
        assert state.integration.workspace_lock_held is False
        assert state.task_integration["a"].status == "integrated"
        denied = _events(caplog, "integration.runlock_denied")
        assert len(denied) == 1
        assert denied[0].holder_run_id == "holder-run"
        assert denied[0].policy == "skip_sync"


class TestWorkspaceLockOffPolicy:
    def test_acquires_nothing_and_warns_once(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        repo = _git_repo(tmp_path)
        wf = _workflow(
            [
                _task("a", outputs=["out/a.txt"], isolation="worktree"),
                _task("b", depends_on=["a"], outputs=["out/b.txt"], isolation="none"),
            ],
            integration=IntegrationSpec(workspace_lock="off"),
        )
        fake = FakeExecutor(repo_writes={"a": {"core": {"landed.txt": "x\n"}}})
        orch = _orch(tmp_path, executor=fake)
        with caplog.at_level(logging.WARNING, logger="agent_orchestrator"):
            state = _run(orch, wf, tmp_path)

        assert state.status == "succeeded"
        assert state.integration.workspace_lock_held is False
        # "off" acquires NOTHING -- the runlocks directory never gets a lock file at all.
        lock_path = _lock_path_for(str(tmp_path))
        assert not lock_path.exists()

        off_warnings = _events(caplog, "integration.workspace_lock_off")
        assert len(off_warnings) == 1

        # Sync itself still proceeds normally under "off" -- only the PROTECTION is
        # disabled, not the sync.
        assert (repo / "landed.txt").exists()


# ---------------------------------------------------------------------------------------
# `_sync_checkout` R-12 diagnostics
# ---------------------------------------------------------------------------------------


class TestSyncCheckoutFastForward:
    def test_clean_checkout_fast_forwards_at_a_barrier(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        repo = _git_repo(tmp_path)
        wf = _workflow(
            [
                _task("a", outputs=["out/a.txt"], isolation="worktree"),
                _task("b", depends_on=["a"], outputs=["out/b.txt"], isolation="none"),
            ],
        )
        fake = FakeExecutor(repo_writes={"a": {"core": {"landed.txt": "x\n"}}})
        orch = _orch(tmp_path, executor=fake)
        with caplog.at_level(logging.INFO, logger="agent_orchestrator"):
            state = _run(orch, wf, tmp_path)

        assert state.status == "succeeded"
        target_head = next(iter(state.integration.heads.values()))
        assert _git(["rev-parse", "HEAD"], repo).strip() == target_head
        # Index and worktree are both clean after the fast-forward.
        assert _git(["status", "--porcelain"], repo).strip() == ""
        assert (repo / "landed.txt").read_text() == "x\n"
        ok_events = _events(caplog, "integration.sync_ok")
        assert len(ok_events) == 1

    def test_colliding_dirty_path_refuses_naming_exactly_that_path_no_data_loss(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        repo = _git_repo(tmp_path)  # base has README.md
        wf = _workflow(
            [
                _task("a", outputs=["out/a.txt"], isolation="worktree"),
                _task("b", depends_on=["a"], outputs=["out/b.txt"], isolation="none"),
            ],
        )
        # "a" rewrites the SAME tracked path the shared checkout is about to be dirtied on.
        fake = FakeExecutor(repo_writes={"a": {"core": {"README.md": "changed by a\n"}}})
        (repo / "README.md").write_text("local uncommitted edit\n")
        orch = _orch(tmp_path, executor=fake)
        with caplog.at_level(logging.WARNING, logger="agent_orchestrator"):
            state = _run(orch, wf, tmp_path)

        assert state.status == "failed"
        assert "b" not in fake.contexts  # halted before the barrier task ever dispatched
        # No data loss: the local edit is untouched.
        assert (repo / "README.md").read_text() == "local uncommitted edit\n"

        # AC-12's run-end best-effort finalize re-attempts the sync once more after the
        # halt (pre-existing behaviour, unchanged by this ticket) -- both attempts see
        # the identical, still-unresolved collision.
        failed_events = _events(caplog, "integration.sync_failed")
        assert failed_events
        for event in failed_events:
            assert event.reason == "dirty_checkout"
            assert event.colliding_paths == ["README.md"]
            assert "README.md" in event.hint

    def test_unrelated_dirty_path_still_syncs_successfully(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        repo = _git_repo(tmp_path)
        wf = _workflow(
            [
                _task("a", outputs=["out/a.txt"], isolation="worktree"),
                _task("b", depends_on=["a"], outputs=["out/b.txt"], isolation="none"),
            ],
        )
        fake = FakeExecutor(repo_writes={"a": {"core": {"landed.txt": "x\n"}}})
        (repo / "README.md").write_text("dirty but unrelated\n")
        orch = _orch(tmp_path, executor=fake)
        with caplog.at_level(logging.INFO, logger="agent_orchestrator"):
            state = _run(orch, wf, tmp_path)

        assert state.status == "succeeded"
        assert (repo / "landed.txt").exists()
        assert (repo / "README.md").read_text() == "dirty but unrelated\n"
        assert len(_events(caplog, "integration.sync_ok")) == 1
        assert len(_events(caplog, "integration.sync_failed")) == 0

    def test_rename_collision_on_the_old_path_is_named_not_misclassified(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Review C-2: git's DEFAULT rename detection collapses `diff --name-only` for a
        pure rename down to only the NEW path -- so a local, uncommitted edit at the OLD
        (pre-rename) path is a completely ordinary collision that the naive collision-set
        computation would silently miss, misclassifying the refusal as a generic
        `sync_anomaly` instead of a named `dirty_checkout`. `_sync_checkout` must use
        `diff_names_no_renames` (rename detection forced OFF) so both paths are seen."""
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        content = "line\n" * 50  # large/similar enough for git to actually detect a rename
        repo = _git_repo(tmp_path, files={"README.md": content})

        class _RenamingExecutor(Executor):
            def execute(self, ctx: TaskContext) -> TaskResult:
                if ctx.task_id == "a":
                    worktree = Path(ctx.repo_paths["core"])
                    (worktree / "README.md").unlink()
                    (worktree / "README2.md").write_text(content)
                    for p in ctx.output_paths:
                        Path(p).parent.mkdir(parents=True, exist_ok=True)
                        Path(p).write_text("a output\n")
                    return TaskResult(task_id=ctx.task_id, status="succeeded", attempts=1)
                for p in ctx.output_paths:
                    Path(p).parent.mkdir(parents=True, exist_ok=True)
                    Path(p).write_text("b output\n")
                return TaskResult(task_id=ctx.task_id, status="succeeded", attempts=1)

        wf = _workflow(
            [
                _task("a", outputs=["out/a.txt"], isolation="worktree"),
                _task("b", depends_on=["a"], outputs=["out/b.txt"], isolation="none"),
            ],
        )
        local_edit = content + "local uncommitted edit\n"
        (repo / "README.md").write_text(local_edit)  # dirty at the OLD (pre-rename) path
        orch = _orch(tmp_path, executor=_RenamingExecutor())
        with caplog.at_level(logging.WARNING, logger="agent_orchestrator"):
            state = _run(orch, wf, tmp_path)

        assert state.status == "failed"
        # No data loss -- the local edit at the old path is untouched.
        assert (repo / "README.md").read_text() == local_edit

        failed_events = _events(caplog, "integration.sync_failed")
        assert failed_events
        for event in failed_events:
            assert event.reason == "dirty_checkout"
            assert "README.md" in event.colliding_paths

    def test_diverged_checkout_refuses_non_fast_forward_with_diagnostics(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The shared checkout advances INDEPENDENTLY (an out-of-band commit, simulating
        an operator committing directly to it) while "a" is isolated -- by the time the
        barrier task "b" would sync, the checkout is no longer an ancestor of the
        integration head at all, so this must refuse as `not_fast_forward`, distinct from
        `dirty_checkout`."""
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        repo = _git_repo(tmp_path)

        class _DivergingExecutor(Executor):
            def __init__(self, repo: Path) -> None:
                self._repo = repo
                self.calls: list[str] = []

            def execute(self, ctx: TaskContext) -> TaskResult:
                self.calls.append(ctx.task_id)
                if ctx.task_id == "a":
                    (Path(ctx.repo_paths["core"]) / "landed.txt").write_text("x\n")
                    for p in ctx.output_paths:  # satisfy the declared out/a.txt (R-2 gate)
                        Path(p).parent.mkdir(parents=True, exist_ok=True)
                        Path(p).write_text("a output\n")
                    (self._repo / "diverged.txt").write_text("diverged\n")
                    _git(["add", "-A"], self._repo)
                    _git([*_COMMIT_ARGS, "commit", "-q", "-m", "out-of-band"], self._repo)
                    return TaskResult(task_id=ctx.task_id, status="succeeded", attempts=1)
                for p in ctx.output_paths:
                    Path(p).parent.mkdir(parents=True, exist_ok=True)
                    Path(p).write_text("b output\n")
                return TaskResult(task_id=ctx.task_id, status="succeeded", attempts=1)

        wf = _workflow(
            [
                _task("a", outputs=["out/a.txt"], isolation="worktree"),
                _task("b", depends_on=["a"], outputs=["out/b.txt"], isolation="none"),
            ],
        )
        executor = _DivergingExecutor(repo)
        orch = _orch(tmp_path, executor=executor)
        with caplog.at_level(logging.WARNING, logger="agent_orchestrator"):
            state = _run(orch, wf, tmp_path)

        assert state.status == "failed"
        assert "b" not in executor.calls  # halted before the barrier task ever dispatched
        # The out-of-band commit is untouched -- no data loss, no forced overwrite.
        assert (repo / "diverged.txt").read_text() == "diverged\n"

        # (AC-12's run-end best-effort finalize re-attempts once more after the halt --
        # same pre-existing behaviour as the dirty-collision case above.)
        failed_events = _events(caplog, "integration.sync_failed")
        assert failed_events
        for event in failed_events:
            assert event.reason == "not_fast_forward"

    def test_sync_never_invoked_when_nothing_is_isolated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow(
            [_task("a", outputs=["out/a.txt"], isolation="none")],
            isolation_default="none",
        )
        calls: list[str] = []
        original = Orchestrator._sync_checkout

        def _spy(self: Orchestrator, state: object, workflow: object, ctx: object) -> bool:
            calls.append("called")
            return original(self, state, workflow, ctx)  # type: ignore[arg-type]

        monkeypatch.setattr(Orchestrator, "_sync_checkout", _spy)
        orch = _orch(tmp_path)
        state = _run(orch, wf, tmp_path)
        assert state.status == "succeeded"
        assert calls == []

    def test_sync_called_once_per_barrier_and_once_at_run_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow(
            [
                _task("a", outputs=["out/a.txt"], isolation="worktree"),
                _task("b", depends_on=["a"], outputs=["out/b.txt"], isolation="none"),
                _task("c", depends_on=["b"], outputs=["out/c.txt"], isolation="none"),
            ],
        )
        fake = FakeExecutor(repo_writes={"a": {"core": {"landed.txt": "x\n"}}})
        calls: list[str] = []
        original = Orchestrator._sync_checkout

        def _spy(self: Orchestrator, state: object, workflow: object, ctx: object) -> bool:
            calls.append("called")
            return original(self, state, workflow, ctx)  # type: ignore[arg-type]

        monkeypatch.setattr(Orchestrator, "_sync_checkout", _spy)
        orch = _orch(tmp_path, executor=fake)
        state = _run(orch, wf, tmp_path)
        assert state.status == "succeeded"
        # Once before "b" (barrier), once before "c" (barrier -- integration still
        # active), once more at run-end finalize.
        assert len(calls) == 3


class TestOnlyRefIndexSafeCommandsAgainstTheSharedCheckout:
    def test_never_invokes_merge_or_rebase_against_the_shared_checkout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R-4/R-12's own explicit contract, proven at the FULL engine level: every git
        argv issued with cwd == the SHARED checkout's toplevel (never a task worktree,
        which legitimately undergoes rebase-based integration) is `merge`/`rebase`-free.
        """
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        repo = _git_repo(tmp_path)
        wf = _workflow(
            [
                _task("a", outputs=["out/a.txt"], isolation="worktree"),
                _task("b", depends_on=["a"], outputs=["out/b.txt"], isolation="none"),
            ],
        )
        fake = FakeExecutor(repo_writes={"a": {"core": {"landed.txt": "x\n"}}})

        recorded: list[list[str]] = []
        real_runner = _SubprocessRunner()

        def _recording_runner(argv, **kwargs):  # type: ignore[no-untyped-def]
            if kwargs.get("cwd") and os.path.normpath(kwargs["cwd"]) == os.path.normpath(str(repo)):
                recorded.append(argv)
            return real_runner(argv, **kwargs)

        monkeypatch.setattr(git_module, "_DEFAULT_RUNNER", _recording_runner)
        orch = _orch(tmp_path, executor=fake)
        state = _run(orch, wf, tmp_path)
        assert state.status == "succeeded"
        assert recorded  # sanity: the shared checkout WAS touched (the sync itself)
        for argv in recorded:
            assert "merge" not in argv
            assert "rebase" not in argv


# ---------------------------------------------------------------------------------------
# Resume takeover
# ---------------------------------------------------------------------------------------


class TestResumeTakeover:
    def test_resume_reclaims_a_stale_lock(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow(
            [
                _task("a", outputs=["out/a.txt"], isolation="worktree"),
                _task("b", depends_on=["a"], outputs=["out/b.txt"], isolation="none"),
            ],
        )
        fake = FakeExecutor(repo_writes={"a": {"core": {"landed.txt": "x\n"}}})
        store, rs_store = _workspace(tmp_path)
        orch = Orchestrator(fake, store, rs_store, max_parallel=1)
        state = orch.run(wf, _reposet(str(tmp_path)), _agents())
        assert state.status == "succeeded"
        assert state.integration.active is True
        assert state.integration.workspace_lock_held is True

        # Simulate a crash: the run's own lock file is gone (released cleanly above), so
        # fabricate a leaked one -- a dead pid, matching what a genuinely crashed
        # process's lock would look like once the OS auto-releases its flock.
        dead = subprocess.Popen(["python3", "-c", "pass"])
        dead.wait(timeout=5)
        lock_path = _lock_path_for(str(tmp_path))
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(
            f'{{"run_id": "{state.run_id}", "pid": {dead.pid}, "boot_id": "stale-boot", "at": "x"}}'
        )

        state = rs_store.prepare_resume(state, wf)
        fake2 = FakeExecutor()
        orch2 = Orchestrator(fake2, store, rs_store, max_parallel=1)
        with caplog.at_level(logging.WARNING, logger="agent_orchestrator"):
            state2 = orch2.run(wf, _reposet(str(tmp_path)), _agents(), run_state=state)

        assert state2.status == "succeeded"
        reclaimed = _events(caplog, "integration.runlock_reclaimed")
        assert any(getattr(r, "resume", False) is True for r in reclaimed)
        # Released again cleanly at the end of the resumed run.
        assert not lock_path.exists()

    def test_resume_denied_by_a_genuinely_live_holder_degrades_to_unsynced(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """W-4: the resume-takeover DENIAL branch (some other live run now holds the
        workspace) -- distinct from the reclaim-a-stale-lock path above. Must not fail the
        resume; must clear `workspace_lock_held` so `_sync_checkout` stays a no-op for the
        rest of this run rather than risk an unprotected checkout mutation."""
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow(
            [
                _task("a", outputs=["out/a.txt"], isolation="worktree"),
                _task("b", depends_on=["a"], outputs=["out/b.txt"], isolation="none"),
            ],
        )
        fake = FakeExecutor(repo_writes={"a": {"core": {"landed.txt": "x\n"}}})
        store, rs_store = _workspace(tmp_path)
        # Stop after "a" settles (succeeded+integrated) but before "b" ever dispatches --
        # mirrors `tests.test_engine_isolation.TestCancelMidIntegrationThenResume`'s own
        # technique (a fake cancel_fn, not a real crash) so there is genuine pending work
        # left for the resumed run. Only the MAIN thread's own top-of-loop check counts --
        # `_run_with_retries`'s per-attempt cancel_fn() check runs on a WORKER thread and
        # must never itself observe cancellation, or "a" could self-cancel mid-execution
        # instead of settling first (same rationale as that sibling test).
        main_thread = threading.main_thread()
        calls = {"n": 0}

        def _cancel() -> bool:
            if threading.current_thread() is not main_thread:
                return False
            calls["n"] += 1
            return calls["n"] > 1

        orch = Orchestrator(fake, store, rs_store, max_parallel=1, cancel_fn=_cancel)
        state = orch.run(wf, _reposet(str(tmp_path)), _agents())
        assert state.status == "cancelled"
        assert state.tasks["a"].status == "succeeded"
        assert "b" not in fake.contexts
        assert state.integration.workspace_lock_held is True

        # A genuinely live SECOND run now holds the workspace lock -- resume must never
        # override a real flock (Investigation B's own invariant: flock is the sole
        # grant/deny authority).
        contender = WorkspaceRunLock(str(tmp_path), "contender-run")
        assert contender.acquire().granted is True
        try:
            state = rs_store.prepare_resume(state, wf)
            fake2 = FakeExecutor()
            orch2 = Orchestrator(fake2, store, rs_store, max_parallel=1)
            with caplog.at_level(logging.INFO, logger="agent_orchestrator"):
                state2 = orch2.run(wf, _reposet(str(tmp_path)), _agents(), run_state=state)
        finally:
            contender.release()

        # Denied reclaim does not fail the resume -- it degrades to "never sync again".
        assert state2.status == "succeeded"
        assert state2.integration.workspace_lock_held is False
        assert "b" in fake2.contexts  # the remaining task still ran (dispatch unaffected)

        denied = _events(caplog, "integration.runlock_denied")
        assert any(
            getattr(r, "resume", False) is True and r.holder_run_id == "contender-run"
            for r in denied
        )
        skipped = _events(caplog, "integration.sync_skipped")
        assert any(getattr(r, "reason", None) == "workspace_lock_not_held" for r in skipped)
