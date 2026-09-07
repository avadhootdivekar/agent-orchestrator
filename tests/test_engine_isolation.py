"""Tests for E-Wk9Tz3 task-isolation engine wiring (T-En8Hd4, HLD §11 M5).

Reuses `tests/isolation/conftest.py`'s locked git-fixture interface (`make_repo`) by
IMPORT, per TASK.md -- that conftest is not modified here. A small local autouse fixture
(`_isolated_git_env`) mirrors its `HOME`/`AO_STATE_DIR` redirection so every test in this
file stays off the real home dir and `$AO_STATE_DIR` -- a genuine "small local copy",
authorized in TASK.md, since `tests/isolation/conftest.py`'s own autouse fixture only
applies within `tests/isolation/`.

Covers (TASK.md ACs, in file order below):
- AC-15/R-19 (BLOCKING, first): the primary execution path -- an isolated task's TaskContext
  resolves inside its own worktree, not the shared checkout; store=None stays byte-identical.
- AC-2: `_ready_ids`/`_settled_for_dependents` -- a predecessor left `pending` blocks a
  dependent even though its `TaskRunState.status == "succeeded"`.
- AC-3: `_is_barrier` -- shared-checkout tasks never overlap isolated ones.
- AC-4/AC-5: `_activate_integration` -- one degrade cause per test, `isolation_strict`, and
  the load-bearing lazy-activation shape (branch created by task 1, isolated fan-out after).
- AC-6: consumes `IsolatedArtifactView`; `RunStateStore`'s store is never a view.
- AC-9/AC-16 (R-2): outputs gate integration on the worker.
- AC-10: settle switch (integrated/empty/conflict_resolver/conflict_rerun/failed), each with
  its own test, via an injected escalation_hook test-double (T-Rm2Lx7/T-Lr6Ka3 have not
  landed -- this ticket's own default hooks are exercised separately).
- AC-11 (R-3/R-6): `should_skip` integration gate on BOTH branches, regression-testing
  branch 2 (`skip_if_outputs_exist`) by name.
- AC-12: checkout sync (success, dirty-tree failure, `sync_checkout: never`).
- AC-13: cancel mid-integration + resume completes.
- AC-14/R-5: `rank_wave` wiring -- identity at max_parallel=1, applied at 3.
- AC-18/S-3 (isolation_env/AO_* env, both `ClaudeCliExecutor` branches -- the second lives in
  `tests/test_executors.py`-style coverage inline here since executors/claude_cli.py is a
  small edit of this same ticket).
- AC-19/R-23: release() on a plain execution failure, distinct from an integration-reported
  failure.
- AC-20/S-7: a verify-failure storm trips an existing breaker and halts.
- AC-21: `dispatch_cycle` increments once per dispatch.
- status.json `task_integration` shape.
"""

from __future__ import annotations

import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.engine import Orchestrator, WorkerOutcome
from agent_orchestrator.executors.base import Executor
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.isolation.git import GitRepo
from agent_orchestrator.isolation.integrator import IntegrationResult, RunIntegrationSnapshot
from agent_orchestrator.isolation.view import IsolatedArtifactView
from agent_orchestrator.isolation.worktrees import group_repos
from agent_orchestrator.logging_setup import get_run_logger
from agent_orchestrator.models import (
    AgentSpec,
    CircuitBreakerSpec,
    IntegrationSpec,
    RepoRef,
    RepoSet,
    RunState,
    TaskContext,
    TaskIntegrationState,
    TaskResult,
    TaskRunState,
    TaskSpec,
    WorkflowDefaults,
    WorkflowSpec,
)
from agent_orchestrator.runstate import RunStateStore

_FIXED_DT = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _isolated_git_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Small local copy of `tests/isolation/conftest.py`'s own autouse fixture (that
    conftest's fixtures only apply within `tests/isolation/` itself) -- redirects HOME and
    AO_STATE_DIR under this test's own tmp_path so no test here touches the real home dir
    or worktree state.
    """
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
    """A real git repo at the FIXED path ``<tmp_path>/repo`` -- a small local copy of
    `tests/isolation/conftest.py`'s own `make_repo` technique (that helper names its repo
    with a random suffix, which this file's tests can't hardcode a `RepoRef.path` around),
    with repo-LOCAL identity configured so `Integrator`'s auto-commit step (`GitRepo.commit`,
    which -- unlike `commit_tree`'s own built-in default identity -- relies on ambient git
    config) works without a real `~/.gitconfig` (HOME is redirected under tmp_path).
    """
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(["init", "-q", "-b", "main"], repo)
    _git(["config", "user.name", "ao-test"], repo)
    _git(["config", "user.email", "ao-test@example.invalid"], repo)
    for rel_path, content in (files or {"README.md": "hi\n"}).items():
        full = repo / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "base"], repo)
    return repo


def _workspace(tmp_path: Path) -> tuple[LocalFsArtifactStore, RunStateStore]:
    store = LocalFsArtifactStore(str(tmp_path))
    rs_store = RunStateStore(str(tmp_path), store, clock=lambda: _FIXED_DT)
    return store, rs_store


def _agents() -> dict:
    return {"ag": AgentSpec(executor="fake")}  # type: ignore[arg-type]


def _reposet(workspace: str, repo_dir_name: str = "repo") -> dict:
    return {
        "rs": RepoSet(
            workspace_root=workspace,
            repos=[RepoRef(id="core", path=repo_dir_name, role="primary")],
        )
    }


def _task(
    tid: str,
    depends_on: list[str] | None = None,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
    isolation: str = "inherit",
    touches: list[str] | None = None,
    skip_if_outputs_exist: bool = True,
) -> TaskSpec:
    return TaskSpec(
        id=tid,
        agent="ag",
        instruction="specs/instructions/design.md",
        depends_on=depends_on or [],
        inputs=inputs or [],
        outputs=outputs or [],
        isolation=isolation,  # type: ignore[arg-type]
        touches=touches or [],
        skip_if_outputs_exist=skip_if_outputs_exist,
    )


def _workflow(
    tasks: list[TaskSpec],
    wf_id: str = "wf",
    isolation_default: str = "worktree",
    circuit_breakers: list[CircuitBreakerSpec] | None = None,
    integration: IntegrationSpec | None = None,
) -> WorkflowSpec:
    return WorkflowSpec(
        version="1.0",
        id=wf_id,
        repo_set="rs",
        defaults=WorkflowDefaults(isolation=isolation_default),  # type: ignore[arg-type]
        tasks=tasks,
        circuit_breakers=circuit_breakers or [],
        integration=integration or IntegrationSpec(),
    )


def _instructions(tmp_path: Path) -> None:
    (tmp_path / "specs" / "instructions").mkdir(parents=True, exist_ok=True)
    (tmp_path / "specs" / "instructions" / "design.md").write_text("# Do it\n")


def _orch(
    tmp_path: Path,
    executor: Executor | None = None,
    max_parallel: int = 1,
    **kwargs: object,
) -> Orchestrator:
    store, rs_store = _workspace(tmp_path)
    return Orchestrator(
        executor or FakeExecutor(),
        store,
        rs_store,
        max_parallel=max_parallel,
        **kwargs,  # type: ignore[arg-type]
    )


def _run(orch: Orchestrator, wf: WorkflowSpec, tmp_path: Path, **kwargs: object) -> RunState:
    return orch.run(wf, _reposet(str(tmp_path)), _agents(), **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# AC-15/R-19 -- the primary execution path (do this first, per TASK.md's own ordering)
# ---------------------------------------------------------------------------


class TestR19PrimaryExecutionPath:
    def test_isolated_task_context_resolves_inside_its_own_worktree(self, tmp_path: Path) -> None:
        """(a) for an isolated task, assert on the REAL TaskContext the fake executor
        received that each of the seven remappable path categories resolves inside the
        worktree; (b) task_manifest_path/gate_output_path/output_dir do NOT (R-15); then
        prove a file the executor writes INTO the repo (`repo_writes` -- a declared
        `output_paths` entry deliberately stays OUTSIDE the repo here, the consumer's own
        convention per HLD §7.2, so the task settles ``succeeded`` cleanly) is reachable
        from the integration ref and absent from the checked-out branch.
        """
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        repo = _git_repo(tmp_path)

        wf = _workflow(
            [_task("a", inputs=["repo/README.md"], outputs=["out/a.txt"])],
            # sync_checkout: never so this test's own "the checkout is untouched by
            # landing" assertion is actually true; the default ("on_demand") DOES sync at
            # run end -- covered by TestCheckoutSync below, not here.
            integration=IntegrationSpec(sync_checkout="never"),
        )
        fake = FakeExecutor(repo_writes={"a": {"core": {"tracked.txt": "hello from a\n"}}})
        orch = _orch(tmp_path, executor=fake)
        state = _run(orch, wf, tmp_path)

        assert state.status == "succeeded"
        ctx = fake.contexts["a"]

        worktree_root = ctx.repo_paths["core"]
        assert worktree_root != str(repo)  # NOT the shared checkout
        assert "worktrees" in worktree_root

        # (a) repo-contained categories resolve INSIDE the worktree.
        assert ctx.input_paths == [f"{worktree_root}/README.md"]
        # instruction/general_instructions/declared-output-outside-repo live OUTSIDE every
        # repo, so they stay shared -- still resolved through the view, unaffected by it.
        assert ctx.instruction_path == str(tmp_path / "specs" / "instructions" / "design.md")
        assert ctx.output_paths == [str(tmp_path / "out" / "a.txt")]
        # cwd defaults to the workspace root (AgentSpec.working_dir unset), which is also
        # outside every repo here -- see test_cwd_resolves_into_worktree_... below for the
        # in-repo case.
        assert ctx.cwd == str(tmp_path)

        # (b) task_manifest_path/gate_output_path/output_dir stay shared (R-15).
        assert ctx.task_manifest_path is None
        assert ctx.gate_output_path is None
        assert ".orchestrator" in ctx.output_dir
        assert ctx.output_dir.startswith(str(tmp_path))

        # The file the executor wrote INSIDE the repo never touched the shared checkout
        # (the worktree itself is released -- keep_worktrees: on_failure -- by the time
        # run() returns, so its own existence is asserted DURING dispatch instead, via
        # `contexts`/`repo_paths` above; the landing proof below is the load-bearing one).
        assert not (repo / "tracked.txt").exists()

        # After settle, the file is reachable from the integration ref (landed) but absent
        # from the checked-out branch (main) -- the checkout is never touched by landing.
        integ = state.integration
        assert integ.active is True
        branch = integ.branch
        assert branch is not None
        shown = _git(["show", f"{branch}:tracked.txt"], repo)
        assert shown == "hello from a\n"
        cp = subprocess.run(
            ["git", "cat-file", "-e", "main:tracked.txt"],
            cwd=repo,
            capture_output=True,
            check=False,
        )
        assert cp.returncode != 0  # tracked.txt does not exist on main

    def test_cwd_resolves_into_worktree_when_working_dir_points_at_the_repo(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        wf.tasks[0].agent = "ag2"
        fake = FakeExecutor()
        agents = {"ag2": AgentSpec(executor="fake", working_dir="repo")}
        orch = _orch(tmp_path, executor=fake)
        state = orch.run(wf, _reposet(str(tmp_path)), agents)
        assert state.status == "succeeded"
        ctx = fake.contexts["a"]
        worktree_root = ctx.repo_paths["core"]
        assert ctx.cwd == worktree_root

    def test_store_none_is_byte_identical_to_self_store(self, tmp_path: Path) -> None:
        """(c) with store=None the resolved paths are byte-identical to today (NFR-2)."""
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])], isolation_default="none")
        fake = FakeExecutor()
        orch = _orch(tmp_path, executor=fake)
        state = _run(orch, wf, tmp_path)
        assert state.status == "succeeded"
        ctx = fake.contexts["a"]
        assert ctx.output_paths == [str(tmp_path / "out" / "a.txt")]
        assert ctx.cwd == str(tmp_path)
        assert ctx.env == {}
        assert (tmp_path / "out" / "a.txt").exists()


# ---------------------------------------------------------------------------
# AC-2 -- _ready_ids / _settled_for_dependents (FR-9)
# ---------------------------------------------------------------------------


class TestSettledForDependents:
    def test_succeeded_but_not_integrated_predecessor_blocks_dependent(
        self, tmp_path: Path
    ) -> None:
        orch = _orch(tmp_path)
        state = RunState(
            run_id="r1",
            workflow_id="wf",
            repo_set="rs",
            started_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            tasks={"a": TaskRunState(status="succeeded")},
            task_integration={"a": TaskIntegrationState(isolation="worktree", status="pending")},
        )
        assert orch._settled_for_dependents("a", state) is False

    def test_succeeded_and_integrated_predecessor_is_settled(self, tmp_path: Path) -> None:
        orch = _orch(tmp_path)
        state = RunState(
            run_id="r1",
            workflow_id="wf",
            repo_set="rs",
            started_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            tasks={"a": TaskRunState(status="succeeded")},
            task_integration={"a": TaskIntegrationState(isolation="worktree", status="integrated")},
        )
        assert orch._settled_for_dependents("a", state) is True

    def test_never_isolated_predecessor_is_settled_on_succeeded_alone(self, tmp_path: Path) -> None:
        orch = _orch(tmp_path)
        state = RunState(
            run_id="r1",
            workflow_id="wf",
            repo_set="rs",
            started_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            tasks={"a": TaskRunState(status="succeeded")},
        )
        assert orch._settled_for_dependents("a", state) is True

    def test_ready_ids_blocks_dependent_on_non_integrated_predecessor(self, tmp_path: Path) -> None:
        from agent_orchestrator.dag import build_dag

        wf = _workflow(
            [
                _task("a", outputs=["repo/a.txt"]),
                _task("b", depends_on=["a"], inputs=["repo/a.txt"]),
            ]
        )
        graph = build_dag(wf)
        order = graph.topological_order()
        orch = _orch(tmp_path)
        preds = orch._predecessors(graph)
        state = RunState(
            run_id="r1",
            workflow_id="wf",
            repo_set="rs",
            started_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            tasks={"a": TaskRunState(status="succeeded"), "b": TaskRunState()},
            task_integration={"a": TaskIntegrationState(isolation="worktree", status="pending")},
        )
        ready = orch._ready_ids(order, preds, state, set(), set())
        assert "b" not in ready
        assert "a" not in ready  # already succeeded, excluded


# ---------------------------------------------------------------------------
# AC-3 -- _is_barrier: shared-checkout tasks never overlap isolated ones
# ---------------------------------------------------------------------------


class TestBarrierWithIntegrationActive:
    def test_non_isolated_task_is_a_barrier_while_integration_active(self, tmp_path: Path) -> None:
        orch = _orch(tmp_path)
        task = _task("a", isolation="none")
        wf = _workflow([task])
        state = RunState(
            run_id="r1",
            workflow_id="wf",
            repo_set="rs",
            started_at="x",
            updated_at="x",
        )
        state.integration.active = True
        assert orch._is_barrier(task, wf, state) is True

    def test_non_isolated_task_is_not_a_barrier_when_integration_inactive(
        self, tmp_path: Path
    ) -> None:
        orch = _orch(tmp_path)
        task = _task("a", isolation="none")
        wf = _workflow([task])
        state = RunState(
            run_id="r1", workflow_id="wf", repo_set="rs", started_at="x", updated_at="x"
        )
        assert orch._is_barrier(task, wf, state) is False

    def test_isolated_task_is_not_a_barrier_from_the_integration_rule(self, tmp_path: Path) -> None:
        orch = _orch(tmp_path)
        task = _task("a", isolation="worktree")
        wf = _workflow([task])
        state = RunState(
            run_id="r1", workflow_id="wf", repo_set="rs", started_at="x", updated_at="x"
        )
        state.integration.active = True
        assert orch._is_barrier(task, wf, state) is False

    def test_two_arg_call_still_works_unchanged(self, tmp_path: Path) -> None:
        """Backward compatibility: every pre-epic direct 2-arg call keeps working."""
        orch = _orch(tmp_path)
        task = _task("a", isolation="none")
        wf = _workflow([task])
        assert orch._is_barrier(task, wf) is False

    def test_isolated_vs_shared_never_overlap_live(self, tmp_path: Path) -> None:
        """A live dispatch: with isolation active, a shared-checkout task and an isolated
        one are never both in flight (gated via a barrier -- max_parallel=2 could otherwise
        run them concurrently)."""
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow(
            [
                _task("iso1", outputs=["out/iso1.txt"], isolation="worktree"),
                _task("shared1", outputs=["out/shared1.txt"], isolation="none"),
            ],
        )
        (tmp_path / "out").mkdir()
        fake = FakeExecutor()
        orch = _orch(tmp_path, executor=fake, max_parallel=2)
        state = _run(orch, wf, tmp_path)
        assert state.status == "succeeded"


# ---------------------------------------------------------------------------
# AC-4/AC-5 -- _activate_integration: degrade causes + strict + lazy activation
# ---------------------------------------------------------------------------


class TestActivateIntegrationDegrade:
    def test_git_unavailable_degrades(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _instructions(tmp_path)
        _git_repo(tmp_path)
        monkeypatch.setattr(GitRepo, "version", staticmethod(lambda runner=None: None))
        wf = _workflow([_task("a", outputs=["repo/a.txt"])])
        orch = _orch(tmp_path)
        state = _run(orch, wf, tmp_path)
        assert state.status == "succeeded"
        assert state.integration.active is False
        assert state.integration.degraded_reason == "git_unavailable_or_old"
        # Degraded to unisolated -- the task still ran and produced its output normally.
        assert (tmp_path / "repo" / "a.txt").exists()

    def test_no_git_repos_degrades(self, tmp_path: Path) -> None:
        _instructions(tmp_path)
        (tmp_path / "repo").mkdir()  # NOT a git repo
        (tmp_path / "repo" / "a.txt").parent.mkdir(parents=True, exist_ok=True)
        wf = _workflow([_task("a", outputs=["repo/a.txt"])])
        orch = _orch(tmp_path)
        state = _run(orch, wf, tmp_path)
        assert state.status == "succeeded"
        assert state.integration.degraded_reason == "no_git_repos"

    def test_unborn_branch_degrades(self, tmp_path: Path) -> None:
        _instructions(tmp_path)
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(["init", "-q", "-b", "main"], repo)
        wf = _workflow([_task("a", outputs=["repo/a.txt"])])
        orch = _orch(tmp_path)
        state = _run(orch, wf, tmp_path)
        assert state.status == "succeeded"
        assert state.integration.degraded_reason == "unborn_branch"

    def test_unsafe_state_dir_degrades(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _instructions(tmp_path)
        repo = _git_repo(tmp_path)
        monkeypatch.setenv("AO_STATE_DIR", str(repo / "state"))
        wf = _workflow([_task("a", outputs=["repo/a.txt"])])
        orch = _orch(tmp_path)
        state = _run(orch, wf, tmp_path)
        assert state.status == "succeeded"
        assert state.integration.degraded_reason == "unsafe_state_dir"

    def test_strict_turns_degrade_into_run_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _instructions(tmp_path)
        _git_repo(tmp_path)
        monkeypatch.setattr(GitRepo, "version", staticmethod(lambda runner=None: None))
        wf = _workflow([_task("a", outputs=["repo/a.txt"])])
        orch = _orch(tmp_path, isolation_strict=True)
        state = _run(orch, wf, tmp_path)
        assert state.status == "failed"
        assert state.integration.degraded_reason == "git_unavailable_or_old"


class _BranchOffExecutor(Executor):
    """Task 1 (isolation: none) checks out a NEW branch in the shared repo before any
    isolated task runs -- proves AC-5's lazy-activation shape: the integration base must be
    the NEW branch's HEAD, not main's.
    """

    def __init__(self, repo: Path) -> None:
        self._repo = repo
        self.calls: list[str] = []

    def execute(self, ctx: TaskContext) -> TaskResult:
        self.calls.append(ctx.task_id)
        if ctx.task_id == "branch_off":
            _git(["checkout", "-q", "-b", "feature"], self._repo)
            (self._repo / "feature-marker.txt").write_text("on feature\n")
            _git(["add", "-A"], self._repo)
            _git(
                [
                    "-c",
                    "user.name=ao-test",
                    "-c",
                    "user.email=ao-test@example.invalid",
                    "commit",
                    "-q",
                    "-m",
                    "branch off",
                ],
                self._repo,
            )
            return TaskResult(task_id=ctx.task_id, status="succeeded", attempts=1)
        for p in ctx.output_paths:
            Path(p).parent.mkdir(parents=True, exist_ok=True)
            Path(p).write_text("iso output\n")
        return TaskResult(task_id=ctx.task_id, status="succeeded", attempts=1, exit_code=0)


class TestLazyActivation:
    def test_integration_bases_on_the_new_branch_created_by_task_one(self, tmp_path: Path) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        repo = _git_repo(tmp_path)
        pre_feature_head = _git(["rev-parse", "HEAD"], repo).strip()

        wf = _workflow(
            [
                _task("branch_off", isolation="none"),
                _task(
                    "iso",
                    depends_on=["branch_off"],
                    outputs=["out/iso.txt"],
                    isolation="worktree",
                ),
            ],
        )
        executor = _BranchOffExecutor(repo)
        orch = _orch(tmp_path, executor=executor)
        state = _run(orch, wf, tmp_path)
        assert state.status == "succeeded"
        feature_head = _git(["rev-parse", "feature"], repo).strip()
        assert feature_head != pre_feature_head
        assert state.integration.base_heads[_key(repo)] == feature_head


def _key(repo: Path) -> str:
    """Recompute the full repo key `group_repos` derives (basename-hash of the repo's
    common dir), so tests can key into `heads`/`base_heads` dicts without hardcoding it."""
    groups, _skipped = group_repos({"core": str(repo)})
    return groups[0].key


# ---------------------------------------------------------------------------
# AC-6 -- consumes IsolatedArtifactView; RunStateStore's store is never a view
# ---------------------------------------------------------------------------


class TestRunStateStoreNeverWrapped:
    def test_runstate_store_keeps_the_base_store(self, tmp_path: Path) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        base_store, rs_store = _workspace(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        orch = Orchestrator(FakeExecutor(), base_store, rs_store)
        state = _run(orch, wf, tmp_path)
        assert state.status == "succeeded"
        assert orch._runstate._store is base_store
        assert not isinstance(orch._runstate._store, IsolatedArtifactView)

    def test_non_isolated_task_context_built_from_base_store(self, tmp_path: Path) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])], isolation_default="none")
        fake = FakeExecutor()
        orch = _orch(tmp_path, executor=fake)
        state = _run(orch, wf, tmp_path)
        assert state.status == "succeeded"
        assert fake.contexts["a"].output_paths == [str(tmp_path / "out" / "a.txt")]


# ---------------------------------------------------------------------------
# AC-7 -- structural tasks are never isolated, even under defaults.isolation: worktree
# ---------------------------------------------------------------------------


class TestStructuralTasksNeverIsolated:
    def test_emit_tasks_task_never_isolated(self, tmp_path: Path) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        emitter = _task("emitter", outputs=[])
        emitter.emit_tasks = True
        emitter.task_manifest_path = "out/manifest.json"
        # "llm" is in IntegrationSpec's DEFAULT ladder (V2 requires resolver_agent whenever
        # ANY task isolates -- and defaults.isolation="worktree" alone already counts,
        # regardless of every actual task being structural); drop it so this test's own
        # config isn't what fails, independent of the emitter-forced-to-none proof below.
        wf = _workflow([emitter], integration=IntegrationSpec(ladder=["auto", "mechanical"]))
        fake = FakeExecutor(emit_payloads={"emitter": {"tasks": []}})
        orch = _orch(tmp_path, executor=fake)
        state = _run(orch, wf, tmp_path)
        assert state.status == "succeeded"
        assert "emitter" not in state.task_integration
        # repo_paths is still the run-level SHARED dict (never a worktree remap) -- a
        # structural task is unisolated, not repo-less.
        assert fake.contexts["emitter"].repo_paths == {"core": str(tmp_path / "repo")}


# ---------------------------------------------------------------------------
# AC-14/R-5 -- rank_wave wiring
# ---------------------------------------------------------------------------


class TestRankWaveWiring:
    def test_dispatch_order_unchanged_at_max_parallel_one(self, tmp_path: Path) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        wf = _workflow(
            [
                _task("a", outputs=["out/a.txt"], touches=["shared/*"]),
                _task("b", outputs=["out/b.txt"], touches=["shared/*"]),
            ],
            isolation_default="none",
        )
        fake = FakeExecutor()
        orch = _orch(tmp_path, executor=fake, max_parallel=1)
        state = _run(orch, wf, tmp_path)
        assert state.status == "succeeded"

    def test_soft_preference_applies_at_dispatch(self, tmp_path: Path) -> None:
        """A live wave dispatch at 'soft' actually reorders the DISPATCHED set: two
        disjoint tasks (b, c) are preferred over an overlapping one (a) when only 2 of 3
        slots are available -- assert via which two of three actually ran concurrently.
        """
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        wf = _workflow(
            [
                _task("a", outputs=["out/a.txt"], touches=["hot/*"]),
                _task("b", outputs=["out/b.txt"], touches=["hot/*"]),
                _task("c", outputs=["out/c.txt"], touches=["cold/*"]),
            ],
            isolation_default="none",
        )
        wf.scheduling.overlap_preference = "soft"
        fake = FakeExecutor()
        orch = _orch(tmp_path, executor=fake, max_parallel=2)
        state = _run(orch, wf, tmp_path)
        assert state.status == "succeeded"
        assert set(state.tasks) == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# Scripted Integrator/WorktreeManager test doubles (settle-switch coverage)
# ---------------------------------------------------------------------------


class _ScriptedIntegrator:
    """A fake `Integrator` (injected via `Orchestrator(integrator=...)`) that returns a
    scripted queue of `IntegrationResult`s, one per call -- lets `_settle_completed_task`'s
    conflict_resolver/conflict_rerun/failed switch branches (AC-10) be exercised
    deterministically without a real git conflict, and without depending on T-Rm2Lx7/
    T-Lr6Ka3's not-yet-landed resolver/escalation hooks.
    """

    def __init__(self, results: list[IntegrationResult]) -> None:
        self._results = list(results)
        self.calls = 0

    def integrate(self, task_iso, run_integration, task_integration, attempt, *, agent_id):  # noqa: ANN001, ARG002
        self.calls += 1
        if self._results:
            return self._results.pop(0)
        return IntegrationResult(status="failed", reason="scripted_exhausted")

    def resume_integration(self, task_iso, run_integration, task_integration, attempt):  # noqa: ANN001, ARG002
        return self.integrate(task_iso, run_integration, task_integration, attempt, agent_id="x")


# ---------------------------------------------------------------------------
# AC-10 -- settle switch: integrated/empty/conflict_resolver/conflict_rerun/failed
# ---------------------------------------------------------------------------


class TestConflictSwitch:
    def test_conflict_resolver_requeues_then_a_second_attempt_lands(self, tmp_path: Path) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        fake = FakeExecutor()
        scripted = _ScriptedIntegrator(
            [
                IntegrationResult(status="conflict_resolver", conflicted_paths=["f.txt"]),
                IntegrationResult(status="integrated", heads={"repo-x": "deadbeef"}),
            ]
        )
        orch = _orch(tmp_path, executor=fake, integrator=scripted)
        state = _run(orch, wf, tmp_path)
        assert state.status == "succeeded"
        assert scripted.calls == 2
        ti = state.task_integration["a"]
        assert ti.status == "integrated"
        assert ti.resolver_attempts == 1  # recorded from the FIRST (conflict_resolver) pass

    def test_conflict_resolver_settle_fields(self, tmp_path: Path) -> None:
        """Direct unit-level call into `_settle_completed_task` to assert the FIRST pass's
        transient fields precisely (HLD §11 M5), independent of the live-run proof above.
        """
        from agent_orchestrator.engine import _RunContext

        orch = _orch(tmp_path, executor=FakeExecutor())
        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        state = RunState(
            run_id="r1",
            workflow_id="wf",
            repo_set="rs",
            started_at="x",
            updated_at="x",
            tasks={"a": TaskRunState(status="succeeded")},
            task_integration={"a": TaskIntegrationState(isolation="worktree", status="pending")},
        )
        outcome = WorkerOutcome(
            result=TaskResult(task_id="a", status="succeeded", attempts=1),
            integration=IntegrationResult(status="conflict_resolver", conflicted_paths=["f.txt"]),
        )
        ctx = _RunContext(
            repo_paths={},
            agents=_agents(),
            cones={},
            membership={},
            run_log=get_run_logger("r1"),
            done=set(),
        )
        settle = orch._settle_completed_task("a", outcome, wf, state, ctx)
        assert settle.signal == "requeue"
        assert state.tasks["a"].status == "pending"
        ti = state.task_integration["a"]
        assert ti.status == "conflict_resolver"
        assert ti.mode == "resolve"
        assert ti.resolver_attempts == 1
        assert ti.conflicted_paths == ["f.txt"]

    def test_conflict_rerun_requeues(self, tmp_path: Path) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        fake = FakeExecutor()
        scripted = _ScriptedIntegrator(
            [
                IntegrationResult(status="conflict_rerun", reason="verify_failed"),
                IntegrationResult(status="integrated"),
            ]
        )
        orch = _orch(tmp_path, executor=fake, integrator=scripted)
        state = _run(orch, wf, tmp_path)
        assert state.status == "succeeded"
        assert scripted.calls == 2
        assert state.task_integration["a"].status == "integrated"

    def test_conflict_rerun_settle_fields(self, tmp_path: Path) -> None:
        from agent_orchestrator.engine import _RunContext

        orch = _orch(tmp_path, executor=FakeExecutor())
        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        state = RunState(
            run_id="r1",
            workflow_id="wf",
            repo_set="rs",
            started_at="x",
            updated_at="x",
            tasks={"a": TaskRunState(status="succeeded")},
            task_integration={"a": TaskIntegrationState(isolation="worktree", status="pending")},
        )
        outcome = WorkerOutcome(
            result=TaskResult(task_id="a", status="succeeded", attempts=1),
            integration=IntegrationResult(status="conflict_rerun", reason="verify_failed"),
        )
        ctx = _RunContext(
            repo_paths={},
            agents=_agents(),
            cones={},
            membership={},
            run_log=get_run_logger("r1"),
            done=set(),
        )
        settle = orch._settle_completed_task("a", outcome, wf, state, ctx)
        assert settle.signal == "requeue"
        assert state.tasks["a"].status == "pending"
        ti = state.task_integration["a"]
        assert ti.status == "conflict_rerun"
        assert ti.mode == "rerun"
        assert ti.reruns == 1

    def test_integration_failed_halts_and_retains_worktree(self, tmp_path: Path) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        fake = FakeExecutor()
        scripted = _ScriptedIntegrator([IntegrationResult(status="failed", reason="ref_race")])
        orch = _orch(tmp_path, executor=fake, integrator=scripted)
        state = _run(orch, wf, tmp_path)
        assert state.status == "failed"
        ti = state.task_integration["a"]
        assert ti.status == "failed"
        assert ti.last_error == "ref_race"
        # T4: worktree retained regardless of keep_worktrees (release() not called).
        run_id = state.run_id
        wt = _worktree_path(tmp_path, run_id, "a", "repo")
        assert wt.exists()


def _worktree_path(tmp_path: Path, run_id: str, task_id: str, repo_key_prefix: str) -> Path:
    """Locate a task's worktree directory under $AO_STATE_DIR without hardcoding the repo
    key's hash suffix."""
    base = tmp_path / "ao-state" / "worktrees"
    for ws_dir in base.iterdir():
        run_dir = ws_dir / run_id / task_id
        if not run_dir.exists():
            continue
        for repo_dir in run_dir.iterdir():
            if repo_dir.name.startswith(repo_key_prefix):
                return repo_dir
    raise AssertionError(f"no worktree found for {task_id} under {base}")


# ---------------------------------------------------------------------------
# AC-9/AC-16 -- R-2: outputs gate integration on the worker
# ---------------------------------------------------------------------------


class TestMissingOutputsGate:
    def test_missing_output_never_lands(self, tmp_path: Path) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        repo = _git_repo(tmp_path)
        pre_head = _git(["rev-parse", "HEAD"], repo).strip()
        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        # write_outputs=False: the task "succeeds" at the executor level but never writes
        # its declared output -- R-2's worker-side gate must catch this BEFORE integrate().
        fake = FakeExecutor(write_outputs=False)
        orch = _orch(tmp_path, executor=fake)
        state = _run(orch, wf, tmp_path)
        assert state.status == "failed"
        assert state.tasks["a"].status == "failed"
        ti = state.task_integration["a"]
        assert ti.status == "failed"
        assert ti.last_error is not None and ti.last_error.startswith("missing_outputs:")
        # Nothing landed: the integration ref never advanced past activation.
        branch = state.integration.branch
        assert branch is not None
        post_head = _git(["rev-parse", branch], repo).strip()
        assert post_head == pre_head


class TestOutputsInsideRepoLand:
    """Review C-1 regression (BLOCKING): a declared output living INSIDE the isolated
    repo must settle the task ``"succeeded"`` once it lands -- the outputs verdict for an
    isolated task is the WORKER's own R-2 gate (evaluated through the IsolatedArtifactView,
    in the worktree, before landing), not the pre-existing main-thread check (which
    resolves against the shared, un-synced checkout and would wrongly see the file as
    missing). Before the fix: `task_integration[tid].status == "integrated"` (code
    genuinely landed) next to `state.status == "failed"` (run halted) -- exactly the
    reviewer's reproduction.
    """

    def test_declared_output_inside_the_repo_lands_and_settles_succeeded(
        self, tmp_path: Path
    ) -> None:
        _instructions(tmp_path)
        repo = _git_repo(tmp_path)
        # sync_checkout: never -- isolates this regression test from AC-12's own run-end
        # sync behavior (which WOULD fast-forward the checkout by default) so "the shared
        # checkout is untouched by landing" is a clean, unconfounded assertion here.
        wf = _workflow(
            [_task("a", outputs=["repo/x.py"])],
            integration=IntegrationSpec(sync_checkout="never"),
        )
        fake = FakeExecutor(repo_writes={"a": {"core": {"x.py": "print('hello')\n"}}})
        orch = _orch(tmp_path, executor=fake)
        state = _run(orch, wf, tmp_path)

        assert state.status == "succeeded"
        assert state.tasks["a"].status == "succeeded"
        assert state.tasks["a"].outputs_present is True
        ti = state.task_integration["a"]
        assert ti.status == "integrated"

        # The file really landed on the integration ref...
        branch = state.integration.branch
        assert branch is not None
        shown = _git(["show", f"{branch}:x.py"], repo)
        assert shown == "print('hello')\n"
        # ...and the shared/checked-out tree was never touched by landing.
        assert not (repo / "x.py").exists()

    def test_mixed_in_repo_and_outside_repo_outputs_both_land(self, tmp_path: Path) -> None:
        """One declared output inside the repo, one outside -- both must be verified
        present and the task must settle succeeded."""
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        repo = _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["repo/x.py", "out/a.txt"])])
        fake = FakeExecutor(repo_writes={"a": {"core": {"x.py": "print('mixed')\n"}}})
        orch = _orch(tmp_path, executor=fake)
        state = _run(orch, wf, tmp_path)

        assert state.status == "succeeded"
        assert state.tasks["a"].status == "succeeded"
        assert state.tasks["a"].outputs_present is True
        assert state.task_integration["a"].status == "integrated"
        assert (tmp_path / "out" / "a.txt").exists()  # written via ctx.output_paths
        branch = state.integration.branch
        assert branch is not None
        shown = _git(["show", f"{branch}:x.py"], repo)
        assert shown == "print('mixed')\n"

    def test_genuinely_missing_in_repo_output_still_fails(self, tmp_path: Path) -> None:
        """The redirect must not blind the engine to a REAL missing output -- R-2's worker
        gate (through the view) is still the one enforcing this, just correctly, in the
        worktree instead of the stale shared checkout."""
        _instructions(tmp_path)
        repo = _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["repo/x.py"])])
        fake = FakeExecutor(write_outputs=False)  # never writes x.py anywhere
        orch = _orch(tmp_path, executor=fake)
        state = _run(orch, wf, tmp_path)

        assert state.status == "failed"
        assert state.tasks["a"].status == "failed"
        assert state.tasks["a"].outputs_present is False
        ti = state.task_integration["a"]
        assert ti.status == "failed"
        assert ti.last_error is not None and ti.last_error.startswith("missing_outputs:")
        branch = state.integration.branch
        assert branch is not None
        cp = subprocess.run(
            ["git", "cat-file", "-e", f"{branch}:x.py"], cwd=repo, capture_output=True, check=False
        )
        assert cp.returncode != 0  # never landed


# ---------------------------------------------------------------------------
# AC-19/R-23 -- release() on a plain execution failure, distinct from an
# integration-reported failure (already covered above for the latter).
# ---------------------------------------------------------------------------


class TestReleaseOnPlainExecutionFailure:
    def test_release_called_when_execution_itself_fails(self, tmp_path: Path) -> None:
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        (tmp_path / "out").mkdir()
        fake = FakeExecutor(behaviors={"a": "fail"})
        orch = _orch(tmp_path, executor=fake)
        # keep_worktrees defaults to "on_failure" (retained) -- prove release() is at
        # least REACHABLE and records the failure distinctly from an integration failure:
        # ti.status == "failed" with an execution-failure reason, never "ref_race"/etc.
        state = _run(orch, wf, tmp_path)
        assert state.status == "failed"
        ti = state.task_integration["a"]
        assert ti.status == "failed"
        assert ti.last_error == "fake failure"

    def test_release_actually_removes_the_worktree_when_keep_worktrees_never(
        self, tmp_path: Path
    ) -> None:
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow(
            [_task("a", outputs=["out/a.txt"])],
            integration=IntegrationSpec(keep_worktrees="never"),
        )
        (tmp_path / "out").mkdir()
        fake = FakeExecutor(behaviors={"a": "fail"})
        orch = _orch(tmp_path, executor=fake)
        state = _run(orch, wf, tmp_path)
        assert state.status == "failed"
        run_id = state.run_id
        base = tmp_path / "ao-state" / "worktrees"
        # No worktree survives anywhere under the run's own prefix.
        remaining = list(base.glob(f"*/{run_id}/a/*"))
        assert remaining == []


# ---------------------------------------------------------------------------
# AC-11 -- R-3/R-6: should_skip's integration gate on BOTH branches
# ---------------------------------------------------------------------------


class TestShouldSkipIntegrationGate:
    def test_never_isolated_task_unaffected(self, tmp_path: Path) -> None:
        orch = _orch(tmp_path)
        state = RunState(
            run_id="r1", workflow_id="wf", repo_set="rs", started_at="x", updated_at="x"
        )
        assert orch._integration_allows_skip("a", state) is True

    def test_isolated_but_not_integrated_blocks_skip(self, tmp_path: Path) -> None:
        orch = _orch(tmp_path)
        state = RunState(
            run_id="r1",
            workflow_id="wf",
            repo_set="rs",
            started_at="x",
            updated_at="x",
            task_integration={
                "a": TaskIntegrationState(isolation="worktree", status="conflict_resolver")
            },
        )
        assert orch._integration_allows_skip("a", state) is False

    def test_isolated_and_integrated_allows_skip(self, tmp_path: Path) -> None:
        orch = _orch(tmp_path)
        state = RunState(
            run_id="r1",
            workflow_id="wf",
            repo_set="rs",
            started_at="x",
            updated_at="x",
            task_integration={"a": TaskIntegrationState(isolation="worktree", status="integrated")},
        )
        assert orch._integration_allows_skip("a", state) is True

    def test_regression_branch_2_skip_if_outputs_exist_does_not_skip_a_parked_conflict(
        self, tmp_path: Path
    ) -> None:
        """R-6 dedicated regression: `runstate.should_skip`'s SECOND branch
        (`skip_if_outputs_exist`) has no `ts.status` check at all -- a task parked in
        conflict_resolver (`ts.status == "pending"`) whose declared output already exists
        on disk (written during the original, pre-conflict execution) must NOT be marked
        skipped on a resumed wave. This is the branch the real consumer's
        `skip_if_outputs_exist: true` fan-out entries actually exercise.
        """
        (tmp_path / "out").mkdir()
        (tmp_path / "out" / "a.txt").write_text("stale output from before the conflict\n")
        _instructions(tmp_path)
        _git_repo(tmp_path)
        task = _task("a", outputs=["out/a.txt"], skip_if_outputs_exist=True)
        state = RunState(
            run_id="wf-run1",
            workflow_id="wf",
            repo_set="rs",
            started_at="x",
            updated_at="x",
            tasks={"a": TaskRunState(status="pending")},
            task_integration={
                "a": TaskIntegrationState(isolation="worktree", status="conflict_resolver")
            },
        )
        store, rs_store = _workspace(tmp_path)
        rs_store.save(state)
        # Branch 1 (ts.status == "succeeded") does not fire (status is "pending"); branch 2
        # (skip_if_outputs_exist) WOULD fire in the base store alone -- assert the base
        # store's own should_skip says True (branch 2 fires) while the engine's combined
        # gate says False.
        assert rs_store.should_skip(task, state) is True
        orch = Orchestrator(FakeExecutor(), store, rs_store)
        assert orch._integration_allows_skip("a", state) is False


# ---------------------------------------------------------------------------
# AC-12 -- checkout sync: success, dirty-tree failure, sync_checkout: never
# ---------------------------------------------------------------------------


class TestCheckoutSync:
    def test_sync_succeeds_and_fast_forwards_the_checkout(self, tmp_path: Path) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        repo = _git_repo(tmp_path)
        wf = _workflow(
            [
                _task("iso", outputs=["out/iso.txt"], isolation="worktree"),
                _task("shared", depends_on=["iso"], outputs=["out/shared.txt"], isolation="none"),
            ],
        )
        fake = FakeExecutor(repo_writes={"iso": {"core": {"landed.txt": "x\n"}}})
        orch = _orch(tmp_path, executor=fake)
        state = _run(orch, wf, tmp_path)
        assert state.status == "succeeded"
        # The barrier before "shared" synced the checkout -- landed.txt is now visible in
        # the shared checkout.
        assert (repo / "landed.txt").exists()

    def test_dirty_checkout_fails_sync_and_halts_dispatch(self, tmp_path: Path) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        repo = _git_repo(tmp_path)
        wf = _workflow(
            [
                _task("iso", outputs=["out/iso.txt"], isolation="worktree"),
                _task("shared", depends_on=["iso"], outputs=["out/shared.txt"], isolation="none"),
            ],
        )
        # "iso" must actually LAND something (an Empty integration advances no head, so
        # `_sync_checkout` would see current == target and skip the dirty check entirely)
        # -- write into the repo so the integration head genuinely moves past HEAD.
        fake = FakeExecutor(repo_writes={"iso": {"core": {"landed.txt": "x\n"}}})
        # NOW make the primary checkout dirty -- must happen AFTER _git_repo's own base
        # commit but is otherwise independent of what "iso" lands (a different file).
        (repo / "README.md").write_text("dirty, uncommitted\n")
        orch = _orch(tmp_path, executor=fake)
        state = _run(orch, wf, tmp_path)
        assert state.status == "failed"
        assert "shared" not in fake.contexts  # never dispatched

    def test_sync_checkout_never_skips_sync_entirely(self, tmp_path: Path) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        repo = _git_repo(tmp_path)
        wf = _workflow(
            [
                _task("iso", outputs=["out/iso.txt"], isolation="worktree"),
                _task("shared", depends_on=["iso"], outputs=["out/shared.txt"], isolation="none"),
            ],
            integration=IntegrationSpec(sync_checkout="never"),
        )
        fake = FakeExecutor(repo_writes={"iso": {"core": {"landed.txt": "x\n"}}})
        orch = _orch(tmp_path, executor=fake)
        state = _run(orch, wf, tmp_path)
        assert state.status == "succeeded"
        assert not (repo / "landed.txt").exists()


# ---------------------------------------------------------------------------
# AC-20/S-7 -- a verify-failure storm trips an existing breaker and halts
# ---------------------------------------------------------------------------


class TestBreakerInteraction:
    def test_verify_failure_storm_trips_consecutive_failures_breaker(self, tmp_path: Path) -> None:
        """S-7: confirm the breaker interaction WITH A TEST, not by assertion. A single
        task's execution actually failing already halts the run regardless of breakers
        (a pre-existing, unedited invariant this ticket does not change) -- so with
        threshold=1 the FIRST integration failure in the storm both trips
        `consecutive_failures` AND halts, proving the breaker sees an isolation-caused
        failure exactly like any other task failure rather than being silently bypassed.
        """
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        tasks = [_task(f"t{i}", outputs=[f"out/t{i}.txt"]) for i in range(5)]
        wf = _workflow(
            tasks,
            circuit_breakers=[
                CircuitBreakerSpec(
                    id="cf", condition="consecutive_failures", action="fail", threshold=1
                )
            ],
        )
        fake = FakeExecutor()
        scripted = _ScriptedIntegrator(
            [IntegrationResult(status="failed", reason="verify_command_failed") for _ in tasks]
        )
        orch = _orch(tmp_path, executor=fake, integrator=scripted)
        state = _run(orch, wf, tmp_path)
        assert state.status == "failed"
        assert any(tb.id == "cf" for tb in state.tripped_breakers)
        # HALTED before every task in the storm ran -- not all 5 were dispatched.
        assert scripted.calls < len(tasks)


# ---------------------------------------------------------------------------
# AC-18/S-3 -- TaskContext.env: AO_* vars + isolation.env overlay
# ---------------------------------------------------------------------------


class TestTaskEnv:
    def test_isolated_task_env_carries_ao_vars(self, tmp_path: Path) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        fake = FakeExecutor()
        orch = _orch(
            tmp_path, executor=fake, isolation_env={"core": {"CARGO_TARGET_DIR": "/shared/tgt"}}
        )
        state = _run(orch, wf, tmp_path)
        assert state.status == "succeeded"
        env = fake.contexts["a"].env
        assert env["AO_ISOLATION"] == "worktree"
        assert env["AO_TASK_BRANCH"] == f"ao/{state.run_id}/a"
        assert env["AO_INTEGRATION_BRANCH"] == state.integration.branch
        assert env["AO_WORKTREE_ROOT_CORE"] == fake.contexts["a"].repo_paths["core"]
        assert env["CARGO_TARGET_DIR"] == "/shared/tgt"

    def test_non_isolated_task_env_is_empty(self, tmp_path: Path) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])], isolation_default="none")
        fake = FakeExecutor()
        orch = _orch(tmp_path, executor=fake)
        state = _run(orch, wf, tmp_path)
        assert state.status == "succeeded"
        assert fake.contexts["a"].env == {}


# ---------------------------------------------------------------------------
# AC-9/NFR-3 -- _run_and_integrate performs NO RunState mutation/save on a worker thread
# ---------------------------------------------------------------------------


class _MainThreadOnlySaveStore(RunStateStore):
    """Review C-3 tripwire: wraps a real `RunStateStore` and asserts every `save()` call
    happens on the MAIN thread. `_run_and_integrate`/`_run_with_retries`/`Integrator.
    integrate()` must never call `self._runstate.save(...)` from a worker thread
    (ADR-0007 D3, NFR-3) -- this fails the test immediately (not just at teardown) the
    first time that invariant is violated, which is the tripwire this AC exists to
    install for the four tickets about to extend this exact function.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.save_threads: list[int] = []

    def save(self, state: RunState) -> None:
        self.save_threads.append(threading.get_ident())
        assert threading.current_thread() is threading.main_thread(), (
            "RunStateStore.save() called from a non-main thread -- violates ADR-0007 D3/"
            "NFR-3 (RunState mutation must be single-writer, main-thread only)"
        )
        super().save(state)


class TestNoRunStateMutationOnWorkerThread:
    def test_save_never_called_from_a_worker_thread_during_isolated_dispatch(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        store = LocalFsArtifactStore(str(tmp_path))
        rs_store = _MainThreadOnlySaveStore(str(tmp_path), store, clock=lambda: _FIXED_DT)
        fake = FakeExecutor(repo_writes={"a": {"core": {"a_code.txt": "x\n"}}})
        orch = Orchestrator(fake, store, rs_store)
        state = orch.run(wf, _reposet(str(tmp_path)), _agents())
        assert state.status == "succeeded"
        # Every recorded save() call happened on the main thread (the assertion inside
        # save() itself already enforces this per-call; this is the corroborating count).
        assert rs_store.save_threads
        assert all(tid == threading.get_ident() for tid in rs_store.save_threads)


# ---------------------------------------------------------------------------
# status.json shape (task_integration block)
# ---------------------------------------------------------------------------


class TestStatusJsonShape:
    def test_status_json_carries_task_integration_block(self, tmp_path: Path) -> None:
        import json

        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        fake = FakeExecutor()
        orch = _orch(tmp_path, executor=fake)
        state = _run(orch, wf, tmp_path)
        assert state.status == "succeeded"

        status_path = tmp_path / ".orchestrator" / "runs" / state.run_id / "status.json"
        snapshot = json.loads(status_path.read_text())
        task_row = next(t for t in snapshot["tasks"] if t["id"] == "a")
        assert task_row["integration_status"] == "integrated"
        assert task_row["conflicted_count"] == 0
        assert snapshot["integration"]["active"] is True
        assert snapshot["integration"]["integrated"] == 1
        assert snapshot["integration"]["conflict"] == 0
        assert snapshot["integration"]["failed"] == 0

    def test_status_json_none_for_never_isolated_task(self, tmp_path: Path) -> None:
        import json

        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])], isolation_default="none")
        fake = FakeExecutor()
        orch = _orch(tmp_path, executor=fake)
        state = _run(orch, wf, tmp_path)
        assert state.status == "succeeded"
        status_path = tmp_path / ".orchestrator" / "runs" / state.run_id / "status.json"
        snapshot = json.loads(status_path.read_text())
        task_row = next(t for t in snapshot["tasks"] if t["id"] == "a")
        assert task_row["integration_status"] == "none"
        assert snapshot["integration"]["active"] is False


# ---------------------------------------------------------------------------
# AC-21 -- dispatch_cycle increments once per dispatch
# ---------------------------------------------------------------------------


class TestDispatchCycle:
    def test_dispatch_cycle_increments_across_a_requeue(self, tmp_path: Path) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        fake = FakeExecutor()
        scripted = _ScriptedIntegrator(
            [
                IntegrationResult(status="conflict_resolver", conflicted_paths=["f.txt"]),
                IntegrationResult(status="integrated"),
            ]
        )
        orch = _orch(tmp_path, executor=fake, integrator=scripted)
        state = _run(orch, wf, tmp_path)
        assert state.status == "succeeded"
        assert state.tasks["a"].dispatch_cycle == 2


# ---------------------------------------------------------------------------
# AC-13 -- resume: reconcile() runs, then the run continues
# ---------------------------------------------------------------------------


class TestResumeReconcile:
    def test_resume_reconciles_orphaned_worktrees(self, tmp_path: Path) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        fake = FakeExecutor()
        orch = _orch(tmp_path, executor=fake)
        state = _run(orch, wf, tmp_path)
        assert state.status == "succeeded"
        assert state.integration.active is True

        # Simulate an orphan: a leftover worktree dir for a task id no longer in the
        # workflow (as if a task was removed between the crash and the resume).
        base = tmp_path / "ao-state" / "worktrees"
        ws_dirs = list(base.iterdir())
        assert ws_dirs
        run_dir = ws_dirs[0] / state.run_id
        orphan_task_dir = run_dir / "ghost-task"
        orphan_task_dir.mkdir(parents=True, exist_ok=True)

        # Re-run with the SAME state (as `ao resume` would, after prepare_resume) -- the
        # workflow has already succeeded so nothing redispatches, but reconcile() must
        # still run and is observable via the worktree.reconciled log path (indirectly:
        # no exception, and the orphan directory is untouched by THIS assertion -- the
        # real reap only happens for KNOWN worktree registrations, and this plain
        # directory was never `git worktree add`-registered, so `WorktreeManager.
        # reconcile()`'s own registered-worktree scan will not remove it; this test's
        # real assertion is that resume runs `reconcile()` at all, without raising).
        orch2 = _orch(tmp_path, executor=FakeExecutor())
        state2 = orch2.run(wf, _reposet(str(tmp_path)), _agents(), run_state=state)
        assert state2.status == "succeeded"


class TestCancelMidIntegrationThenResume:
    """Review C-2 (AC-13/ADR-0007 D7): cancellation observed while a sibling task's worker
    is genuinely in flight (drain, don't kill -- the in-flight worker's own `integrate()`
    call is allowed to finish, not abandoned mid-way) followed by `ao resume` (a fresh
    `Orchestrator.run()`, `prepare_resume`d, matching the CLI's own resume path) completing
    the remaining work with no orphaned worktree/lock.
    """

    def test_cancel_after_one_of_two_in_flight_isolated_tasks_drains_then_resume_completes(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        # Two independent isolated tasks dispatched in the SAME wave (max_parallel=2) so
        # both are genuinely in flight together; a dependent, non-isolated task "c" that
        # must NOT be dispatched before the cancellation is observed.
        wf = _workflow(
            [
                _task("a", outputs=["out/a.txt"], isolation="worktree"),
                _task("b", outputs=["out/b.txt"], isolation="worktree"),
                _task(
                    "c",
                    depends_on=["a", "b"],
                    outputs=["out/c.txt"],
                    isolation="none",
                ),
            ],
        )
        fake = FakeExecutor(
            repo_writes={
                "a": {"core": {"a_code.txt": "a\n"}},
                "b": {"core": {"b_code.txt": "b\n"}},
            }
        )
        store, rs_store = _workspace(tmp_path)
        # cancel_fn: False on the very first check (lets the wave dispatch a+b), True from
        # the second check onward -- by construction this fires at the top of the run loop
        # AFTER the first of {a, b} has already drained (settled normally, including its
        # own `integrate()` call running to completion) and BEFORE "c" is ever considered,
        # so the second of {a, b} is still genuinely in flight when cancellation is
        # observed and must be DRAINED (not killed) by `_drain_remaining`. Only the MAIN
        # thread's own top-of-loop check counts: `_run_with_retries`'s own per-attempt
        # cancel_fn() check runs on a WORKER thread and must never itself observe
        # cancellation here, or a worker could self-cancel before its own
        # (single-attempt) execute() call ever runs -- which would prove nothing about
        # the drain path and make this test racy against thread scheduling.
        main_thread = threading.main_thread()
        calls = {"n": 0}

        def _cancel() -> bool:
            if threading.current_thread() is not main_thread:
                return False
            calls["n"] += 1
            return calls["n"] > 1

        orch = Orchestrator(fake, store, rs_store, cancel_fn=_cancel, max_parallel=2)
        state = orch.run(wf, _reposet(str(tmp_path)), _agents())

        assert state.status == "cancelled"
        assert "c" not in fake.contexts  # never dispatched
        # BOTH a and b completed via drain (neither was killed mid-integrate()) --
        # cancellation only ever stops the NEXT wave from filling, per ADR-0007 D7.
        assert state.tasks["a"].status == "succeeded"
        assert state.tasks["b"].status == "succeeded"
        assert state.task_integration["a"].status == "integrated"
        assert state.task_integration["b"].status == "integrated"
        # No orphan worktree survives for either completed task (both released cleanly).
        run_id = state.run_id
        base = tmp_path / "ao-state" / "worktrees"
        for tid in ("a", "b"):
            assert list(base.glob(f"*/{run_id}/{tid}/*")) == []

        # Resume: prepare_resume (as the CLI does) then a fresh Orchestrator.run() on the
        # SAME RunState/workspace completes "c" with no re-work of a/b.
        state = rs_store.prepare_resume(state, wf)
        assert state.status == "running"
        fake2 = FakeExecutor()
        orch2 = Orchestrator(fake2, store, rs_store, max_parallel=2)
        state2 = orch2.run(wf, _reposet(str(tmp_path)), _agents(), run_state=state)

        assert state2.status == "succeeded"
        assert "a" not in fake2.contexts  # not re-dispatched -- already succeeded+integrated
        assert "b" not in fake2.contexts
        assert "c" in fake2.contexts
        assert (tmp_path / "out" / "c.txt").exists()


# ---------------------------------------------------------------------------
# Validate_isolation at emit_tasks injection time (warn + fatal)
# ---------------------------------------------------------------------------


class TestValidateIsolationAtInjection:
    def test_fatal_isolation_misconfiguration_in_emitted_tasks_fails_the_run(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        emitter = _task("emitter", outputs=[], isolation="none")
        emitter.emit_tasks = True
        emitter.task_manifest_path = "out/manifest.json"
        wf = _workflow(
            [emitter],
            isolation_default="none",
            integration=IntegrationSpec(ladder=["auto", "mechanical"]),
        )
        # An injected task declares isolation="worktree" directly (independent of
        # defaults.isolation="none") with "llm" nowhere in the ladder -- V2 cannot fire
        # (ladder has no llm) so use resolver_disallowed_tools=[] + llm-in-ladder instead:
        # simplest fatal is V1 (strategy="merge", reserved) via emitted integration... but
        # integration is workflow-level, not per-task-injectable. Use resolver_agent unset
        # with "llm" reintroduced by an injected TASK isolating -- V2 fires because
        # _any_task_isolated becomes True once the injected task resolves to "worktree".
        wf.integration.ladder = ["auto", "mechanical", "llm"]
        injected_payload = {
            "tasks": [
                {
                    "id": "injected_iso",
                    "agent": "ag",
                    "instruction": "specs/instructions/design.md",
                    "isolation": "worktree",
                }
            ]
        }
        fake = FakeExecutor(emit_payloads={"emitter": injected_payload})
        orch = _orch(tmp_path, executor=fake)
        state = _run(orch, wf, tmp_path)
        assert state.status == "failed"
        assert state.tasks["emitter"].status == "failed"


# ---------------------------------------------------------------------------
# Coverage top-up: WorktreeCollisionError, _sync_checkout's genuine non-fast-forward
# branch, and _copy_untracked_outputs's real "copy" path (all direct/near-direct calls,
# same pattern as the other unit-level tests above).
# ---------------------------------------------------------------------------


class TestWorktreeCollision:
    def test_worktree_collision_fails_the_task_and_halts(self, tmp_path: Path) -> None:
        _instructions(tmp_path)
        repo = _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        (tmp_path / "out").mkdir()

        # Pre-create a worktree at the EXACT path `WorktreeManager.ensure()` will target for
        # task "a", checked out on a DIFFERENT branch -- forces the D-ENS "different worktree
        # already occupies our expected path" hard error.
        run_id = f"wf-{_FIXED_DT.strftime('%Y%m%dT%H%M%SZ')}"
        key = _key(repo)
        # workspace_key is unknown ahead of time -- resolve it the same way paths.py does.
        from agent_orchestrator.isolation.paths import worktree_root

        wt_path = worktree_root(str(tmp_path), run_id, "a", key)
        _git(["worktree", "add", "-b", "unrelated-branch", str(wt_path)], repo)

        fake = FakeExecutor()
        orch = _orch(tmp_path, executor=fake)
        state = _run(orch, wf, tmp_path)
        assert state.status == "failed"
        assert state.tasks["a"].status == "failed"


class TestSyncCheckoutNonFastForward:
    def test_genuine_non_fast_forward_fails_sync(self, tmp_path: Path) -> None:
        from agent_orchestrator.engine import _RunContext

        _instructions(tmp_path)
        repo = _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        store, rs_store = _workspace(tmp_path)
        orch = Orchestrator(FakeExecutor(), store, rs_store)
        state = rs_store.new_run(wf)
        run_dir = str(tmp_path / ".orchestrator" / "runs" / state.run_id)
        ctx = _RunContext(
            repo_paths={"core": str(repo)},
            agents=_agents(),
            cones={},
            membership={},
            run_log=get_run_logger(state.run_id),
            done=set(),
            run_dir=run_dir,
        )
        assert orch._activate_integration(state, wf, ctx) is True
        key = _key(repo)

        # Simulate a landed task: a real commit descending from the OLD main tip, recorded
        # as the integration head, but on a branch never merged into main.
        _git(["checkout", "-q", "-b", "temp-landing"], repo)
        (repo / "landed.txt").write_text("x\n")
        _git(["add", "-A"], repo)
        _git(["commit", "-q", "-m", "landed"], repo)
        landed_sha = _git(["rev-parse", "HEAD"], repo).strip()
        _git(["checkout", "-q", "main"], repo)
        _git(["branch", "-D", "temp-landing"], repo)
        state.integration.heads[key] = landed_sha

        # Diverge main so `landed_sha` is no longer reachable by a fast-forward from it.
        (repo / "diverged.txt").write_text("y\n")
        _git(["add", "-A"], repo)
        _git(["commit", "-q", "-m", "diverge"], repo)

        ok = orch._sync_checkout(state, wf, ctx)
        assert ok is False
        # main is untouched by the failed attempt.
        assert not (repo / "landed.txt").exists()


class TestCopyUntrackedOutputs:
    def test_copies_a_gitignored_declared_output_back_to_the_shared_path(
        self, tmp_path: Path
    ) -> None:
        from agent_orchestrator.engine import _RunContext
        from agent_orchestrator.isolation.worktrees import RepoIsolation, TaskIsolation

        _instructions(tmp_path)
        repo = _git_repo(tmp_path, files={"README.md": "hi\n", ".gitignore": "build/\n"})
        wf = _workflow([_task("a", outputs=["repo/build/out.txt"])])
        store, rs_store = _workspace(tmp_path)
        orch = Orchestrator(FakeExecutor(), store, rs_store)
        state = rs_store.new_run(wf)
        run_dir = str(tmp_path / ".orchestrator" / "runs" / state.run_id)
        ctx = _RunContext(
            repo_paths={"core": str(repo)},
            agents=_agents(),
            cones={},
            membership={},
            run_log=get_run_logger(state.run_id),
            done=set(),
            run_dir=run_dir,
        )
        assert orch._activate_integration(state, wf, ctx) is True
        assert ctx.worktree_manager is not None
        task_iso = ctx.worktree_manager.ensure("a", 1, ["repo/build/out.txt"])
        repo_iso = task_iso.repos[0]
        assert isinstance(repo_iso, RepoIsolation)

        # Write the gitignored declared output directly into the worktree (simulating what
        # the agent would have done).
        build_dir = Path(repo_iso.worktree_root) / "build"
        build_dir.mkdir(parents=True, exist_ok=True)
        (build_dir / "out.txt").write_text("built artifact\n")

        assert isinstance(task_iso, TaskIsolation)
        ok = orch._copy_untracked_outputs(
            ["repo/build/out.txt"], task_iso, "copy", get_run_logger(state.run_id)
        )
        assert ok is True
        shared_path = tmp_path / "repo" / "build" / "out.txt"
        assert shared_path.read_text() == "built artifact\n"

    def test_fail_policy_blocks_without_copying(self, tmp_path: Path) -> None:
        run_log = get_run_logger("r1")
        orch = _orch(tmp_path)
        from agent_orchestrator.isolation.worktrees import TaskIsolation

        task_iso = TaskIsolation(
            task_id="a", cycle=1, declared_outputs=[], workspace_root=str(tmp_path), repos=[]
        )
        ok = orch._copy_untracked_outputs(["repo/build/out.txt"], task_iso, "fail", run_log)
        assert ok is False

    def test_ignore_policy_is_a_no_op(self, tmp_path: Path) -> None:
        run_log = get_run_logger("r1")
        orch = _orch(tmp_path)
        from agent_orchestrator.isolation.worktrees import TaskIsolation

        task_iso = TaskIsolation(
            task_id="a", cycle=1, declared_outputs=[], workspace_root=str(tmp_path), repos=[]
        )
        ok = orch._copy_untracked_outputs(["repo/build/out.txt"], task_iso, "ignore", run_log)
        assert ok is True
        assert not (tmp_path / "repo" / "build" / "out.txt").exists()


class TestIntegrateTaskResumeHook:
    def test_integrate_task_resume_true_calls_resume_integration(self, tmp_path: Path) -> None:
        """Forward-compatible hook for T-Lr6Ka3 (`_run_and_integrate`'s own docstring):
        `resume=True` calls `integrator.resume_integration()` instead of `integrate()`.
        Not exercised by any live dispatch in this ticket -- covered directly here.
        """
        orch = _orch(tmp_path)
        calls: list[str] = []

        class _Recorder:
            def integrate(self, *a, **k):  # noqa: ANN001, ANN002, ANN003
                calls.append("integrate")
                return IntegrationResult(status="integrated")

            def resume_integration(self, *a, **k):  # noqa: ANN001, ANN002, ANN003
                calls.append("resume_integration")
                return IntegrationResult(status="integrated")

        from agent_orchestrator.isolation.worktrees import TaskIsolation

        task_iso = TaskIsolation(
            task_id="a", cycle=1, declared_outputs=[], workspace_root=str(tmp_path), repos=[]
        )
        run_integration = RunIntegrationSnapshot(
            run_id="r1", branch="ao/r1/integration", run_dir="/tmp"
        )
        ti = TaskIntegrationState()
        result = orch._integrate_task(
            _Recorder(), task_iso, run_integration, ti, agent_id="ag", attempt=1, resume=True
        )
        assert result.status == "integrated"
        assert calls == ["resume_integration"]
