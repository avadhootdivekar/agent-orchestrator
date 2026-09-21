"""E-Wk9Tz3 T-Ee3Mn8 matrix item A: the T1(unresolved)->T2->T3->T4 conflict ladder,
end-to-end, filling the two gaps `tests/test_engine_conflict_escalation.py` (T-Lr6Ka3,
studied first per this ticket's own instruction) does NOT already cover with a REAL
two-task conflict:

- `TestMaxResolverAttemptsCapEndToEnd`: ``max_resolver_attempts: 0`` -- a real conflict
  must never reach a resolver dispatch at all (proven by the ABSENCE of any
  `merge-resolve.md`-instructed dispatch in the loser's history), landing via T3 instead.
- `TestLadderWithoutMechanicalOrLlm`: ``ladder: ["auto", "rerun"]`` -- a real conflict
  skips BOTH T1 (mechanical) and T2 (llm) entirely, landing via T3 directly.

Everything else item A asks for (the resolver dispatch's forced `disallowed_tools`
union + `resolver_env` keys, `GitRepo.rebase_in_progress` + real on-disk conflict
markers at dispatch time, resolve-by-read-and-rewrite-and-stage, a non-resolver
dispatch in the same run carrying neither, T2-fails-falls-to-T3, T3-fails-names-
task-and-paths at T4, the full ladder under the DEFAULT `RetryPolicy`, and the
`task_cost_usd` breaker tripping on the second conflict cycle asserted on
`BudgetCounters`) is already exercised by `TestRealConflictLadder`,
`TestTerminationNoInfiniteLoop`, `TestT4Failure`, `TestFullLadderUnderDefaultRetryPolicy`
and `TestCostBreakerAcrossTheLadder` in that file -- this file does not duplicate them,
only re-runs them as part of the full suite gate and adds the two gaps above.

Same "small local copy" fixture-helper pattern as `tests/test_engine_conflict_escalation.py`
(that file's own module docstring: never cross-import fixture helpers between test
modules) -- adapted here, not imported.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.isolation import escalation
from agent_orchestrator.models import (
    AgentSpec,
    IntegrationSpec,
    RepoRef,
    RepoSet,
    TaskContext,
    TaskResult,
    TaskSpec,
    WorkflowDefaults,
    WorkflowSpec,
)
from agent_orchestrator.runstate import RunStateStore

_FIXED_DT = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _isolated_git_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AO_STATE_DIR", str(tmp_path / "ao-state"))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    monkeypatch.delenv("GIT_CONFIG_SYSTEM", raising=False)


def _git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout


def _git_repo(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(["init", "-q", "-b", "main"], repo)
    _git(["config", "user.name", "ao-test"], repo)
    _git(["config", "user.email", "ao-test@example.invalid"], repo)
    for rel_path, content in (files or {"f.txt": "line1\nline2\nline3\n"}).items():
        (repo / rel_path).write_text(content)
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "base"], repo)
    return repo


def _workspace(tmp_path: Path) -> tuple[LocalFsArtifactStore, RunStateStore]:
    store = LocalFsArtifactStore(str(tmp_path))
    rs_store = RunStateStore(str(tmp_path), store, clock=lambda: _FIXED_DT)
    return store, rs_store


def _agents() -> dict:
    return {"ag": AgentSpec(executor="fake"), "merge-resolver": AgentSpec(executor="fake")}


def _reposet(workspace: str) -> dict:
    return {
        "rs": RepoSet(
            workspace_root=workspace, repos=[RepoRef(id="core", path="repo", role="primary")]
        )
    }


def _task(tid: str, outputs: list[str] | None = None) -> TaskSpec:
    return TaskSpec(
        id=tid, agent="ag", instruction="specs/instructions/design.md", outputs=outputs or []
    )


def _workflow(tasks: list[TaskSpec], integration: IntegrationSpec) -> WorkflowSpec:
    return WorkflowSpec(
        version="1.0",
        id="wf",
        repo_set="rs",
        defaults=WorkflowDefaults(isolation="worktree"),  # type: ignore[arg-type]
        tasks=tasks,
        integration=integration,
    )


def _instructions(tmp_path: Path) -> None:
    (tmp_path / "specs" / "instructions").mkdir(parents=True, exist_ok=True)
    (tmp_path / "specs" / "instructions" / "design.md").write_text("# Do it\n")


def _orch(tmp_path: Path, executor, max_parallel: int = 2, **kwargs) -> Orchestrator:
    store, rs_store = _workspace(tmp_path)
    return Orchestrator(executor, store, rs_store, max_parallel=max_parallel, **kwargs)


def _run(orch: Orchestrator, wf: WorkflowSpec, tmp_path: Path):
    return orch.run(wf, _reposet(str(tmp_path)), _agents())


class _HistoryExecutor(FakeExecutor):
    """`FakeExecutor` recording every dispatched `TaskContext` per task_id, keyed by call
    order -- lets a test tell a task's OWN dispatch apart from a later requeue redispatch
    that reuses the same task_id (a resolver redispatch, or a T3 rerun redispatch)."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.context_history: dict[str, list[TaskContext]] = {}

    def execute(self, ctx: TaskContext) -> TaskResult:
        self.context_history.setdefault(ctx.task_id, []).append(ctx)
        return super().execute(ctx)


def _resolver_dispatches(history: list[TaskContext]) -> list[TaskContext]:
    return [
        c for c in history if c.instruction_path.endswith(escalation.RESOLVER_INSTRUCTION_FILENAME)
    ]


class TestMaxResolverAttemptsCapEndToEnd:
    def test_cap_zero_never_dispatches_a_resolver_for_a_real_conflict(self, tmp_path: Path) -> None:
        """``max_resolver_attempts: 0`` -- `escalate()`'s own cap check
        (``task_integration.resolver_attempts < spec.max_resolver_attempts``, i.e.
        ``0 < 0``) must be False on the VERY FIRST real conflict, so the loser's conflict
        goes straight to T3 (rerun still in the ladder) -- never a resolver dispatch, even
        though ``resolver_agent`` IS configured and "llm" IS in the ladder.
        """
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow(
            [_task("a", outputs=["out/a.txt"]), _task("b", outputs=["out/b.txt"])],
            integration=IntegrationSpec(
                resolver_agent="merge-resolver", sync_checkout="never", max_resolver_attempts=0
            ),
        )
        executor = _HistoryExecutor(
            repo_writes={
                "a": {"core": {"f.txt": "line1-A\nline2\nline3\n"}},
                "b": {"core": {"f.txt": "line1-B\nline2\nline3\n"}},
            }
        )
        orch = _orch(tmp_path, executor, max_parallel=2)
        state = _run(orch, wf, tmp_path)

        assert state.status == "succeeded"
        loser = next(tid for tid in ("a", "b") if state.task_integration[tid].reruns == 1)
        winner = "b" if loser == "a" else "a"
        assert state.task_integration[loser].resolver_attempts == 0
        assert state.task_integration[winner].reruns == 0

        loser_history = executor.context_history[loser]
        assert len(loser_history) == 2, "expected exactly one requeue redispatch (T3), not T2"
        assert _resolver_dispatches(loser_history) == [], (
            "a resolver dispatch happened despite max_resolver_attempts: 0"
        )
        # T3's redispatch is the ORIGINAL instruction, unlike a T2 substitution.
        assert loser_history[1].instruction_path == loser_history[0].instruction_path
        assert loser_history[1].agent.disallowed_tools == []


class TestLadderWithoutMechanicalOrLlm:
    def test_ladder_auto_rerun_only_skips_t1_and_t2_for_a_real_conflict(
        self, tmp_path: Path
    ) -> None:
        """``ladder: ["auto", "rerun"]`` (no "mechanical", no "llm") -- T1's
        `resolve_mechanically` hook is only invoked when `TIER_MECHANICAL in spec.ladder`
        (`isolation/integrator.py`'s `_rebase_onto_and_resolve`, unedited by this ticket);
        `escalate()`'s T2 branch requires `TIER_LLM in spec.ladder`. With neither present,
        a real conflict must go straight from the initial rebase to T3 -- landing (rerun
        still in the ladder) without ever touching mechanical resolution OR a resolver
        agent, even though `resolver_agent` IS configured.
        """
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow(
            [_task("a", outputs=["out/a.txt"]), _task("b", outputs=["out/b.txt"])],
            integration=IntegrationSpec(
                resolver_agent="merge-resolver", sync_checkout="never", ladder=["auto", "rerun"]
            ),
        )
        executor = _HistoryExecutor(
            repo_writes={
                "a": {"core": {"f.txt": "line1-A\nline2\nline3\n"}},
                "b": {"core": {"f.txt": "line1-B\nline2\nline3\n"}},
            }
        )
        orch = _orch(tmp_path, executor, max_parallel=2)
        state = _run(orch, wf, tmp_path)

        assert state.status == "succeeded"
        loser = next(tid for tid in ("a", "b") if state.task_integration[tid].reruns == 1)
        assert state.task_integration[loser].resolver_attempts == 0
        assert state.task_integration[loser].tier_reached in (None, "rerun", "auto")

        loser_history = executor.context_history[loser]
        assert len(loser_history) == 2
        assert _resolver_dispatches(loser_history) == []
        assert loser_history[1].instruction_path == loser_history[0].instruction_path
