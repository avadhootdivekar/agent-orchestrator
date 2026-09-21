"""Tests for E-Wk9Tz3 T-Lr6Ka3: the engine-level T2 (LLM resolver) / T3 (rerun) / T4
(fail) conflict ladder -- `engine.py`'s `_run_and_integrate` mode-branch dispatch
overrides (`_prepare_resolver_dispatch`/`_prepare_rerun_dispatch`) and the production
wiring of `isolation.escalation.escalate`/`isolation.resolvers.resolve_mechanically` as
`Orchestrator`'s default hooks.

Two testing techniques, both real git (`tests/isolation/conftest.py` is NOT reused here --
this file needs a FIXED repo path per `tests/test_engine_isolation.py`'s own "small local
copy" precedent, which its own module docstring authorizes):

1. `TestRealConflictLadder` -- a genuine two-task PARALLEL isolated dispatch (real
   `WorktreeManager` + real `Integrator`, no test double for either) where both tasks'
   worktrees are created from the SAME pre-run head (both dispatched in one wave), so
   whichever lands SECOND hits a real rebase conflict. Assertions are written to be
   race-order-agnostic (neither test assumes which of the two tasks wins the landing
   race) -- only that the loser goes through a REAL T2 resolve.
2. Every other test uses a `_ScriptedIntegrator` (mirrors `tests/test_engine_isolation.py`
   and `tests/test_engine_isolation_accounting.py`'s own established, deterministic
   pattern for exercising `_settle_completed_task`'s conflict switch) -- crucially, this
   does NOT bypass this ticket's own dispatch-context code: `_run_and_integrate`'s mode
   branch reads `state.task_integration[tid].mode` (set by the switch from whatever
   `IntegrationResult.status` it was handed, scripted or real) and calls
   `_prepare_resolver_dispatch`/`_prepare_rerun_dispatch` regardless of which Integrator
   produced that status -- so these tests still exercise real worktree resets, real
   conflict-manifest/instruction-copy file writes, and real agent/env substitution,
   while only the FINAL squash/rebase/verify/CAS mechanics are scripted.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.budget import DefaultBudgetManager, cycle_key
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.estimator import HeuristicTokenEstimator
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.isolation import escalation
from agent_orchestrator.isolation.git import GitRepo
from agent_orchestrator.isolation.integrator import IntegrationResult, MaterializeResult
from agent_orchestrator.models import (
    AgentSpec,
    BudgetSpec,
    CircuitBreakerSpec,
    EstimatorConfig,
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

# ---------------------------------------------------------------------------
# Small local git/workspace fixture helpers -- the SAME "small local copy" pattern
# tests/test_engine_isolation.py's own module docstring documents and authorizes.
# ---------------------------------------------------------------------------


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
    return {
        "ag": AgentSpec(executor="fake"),
        "merge-resolver": AgentSpec(executor="fake"),
    }


def _reposet(workspace: str) -> dict:
    return {
        "rs": RepoSet(
            workspace_root=workspace, repos=[RepoRef(id="core", path="repo", role="primary")]
        )
    }


def _task(tid: str, outputs: list[str] | None = None, isolation: str = "inherit") -> TaskSpec:
    return TaskSpec(
        id=tid,
        agent="ag",
        instruction="specs/instructions/design.md",
        outputs=outputs or [],
        isolation=isolation,  # type: ignore[arg-type]
    )


def _workflow(
    tasks: list[TaskSpec],
    circuit_breakers: list[CircuitBreakerSpec] | None = None,
    budget: BudgetSpec | None = None,
    integration: IntegrationSpec | None = None,
) -> WorkflowSpec:
    return WorkflowSpec(
        version="1.0",
        id="wf",
        repo_set="rs",
        defaults=WorkflowDefaults(isolation="worktree"),  # type: ignore[arg-type]
        tasks=tasks,
        circuit_breakers=circuit_breakers or [],
        integration=integration
        or IntegrationSpec(resolver_agent="merge-resolver", sync_checkout="never"),
        budget=budget,
    )


def _instructions(tmp_path: Path) -> None:
    (tmp_path / "specs" / "instructions").mkdir(parents=True, exist_ok=True)
    (tmp_path / "specs" / "instructions" / "design.md").write_text("# Do it\n")


def _orch(tmp_path: Path, executor, max_parallel: int = 1, **kwargs) -> Orchestrator:
    store, rs_store = _workspace(tmp_path)
    return Orchestrator(executor, store, rs_store, max_parallel=max_parallel, **kwargs)


def _run(orch: Orchestrator, wf: WorkflowSpec, tmp_path: Path, **kwargs):
    return orch.run(wf, _reposet(str(tmp_path)), _agents(), **kwargs)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _ScriptedIntegrator:
    """Local copy of `tests/test_engine_isolation.py`'s own `_ScriptedIntegrator` (module-
    private there, this file must not import from another test module).

    ``materialize_results``: a scripted queue for `materialize_conflict` (review C-1
    rework), separate from ``results`` (the `integrate`/`resume_integration` queue) since
    `_prepare_resolver_dispatch` calls `materialize_conflict` FIRST, before either of
    those. Defaults to always reporting a live conflict (``["f.txt"]``) so every existing
    scripted resolve-mode test keeps dispatching the resolver exactly as before unless a
    test explicitly configures otherwise (e.g. to prove the "clean, skip T2" path).
    """

    def __init__(
        self,
        results: list[IntegrationResult],
        *,
        materialize_results: list[MaterializeResult] | None = None,
    ) -> None:
        self._results = list(results)
        self._materialize_results = (
            list(materialize_results) if materialize_results is not None else None
        )
        self.calls = 0
        self.materialize_calls = 0
        self.materialize_base_commits: list[dict[str, str] | None] = []

    def integrate(self, task_iso, run_integration, task_integration, attempt, *, agent_id):  # noqa: ANN001, ARG002
        self.calls += 1
        if self._results:
            return self._results.pop(0)
        return IntegrationResult(status="failed", reason="scripted_exhausted")

    def resume_integration(self, task_iso, run_integration, task_integration, attempt):  # noqa: ANN001, ARG002
        return self.integrate(task_iso, run_integration, task_integration, attempt, agent_id="x")

    def materialize_conflict(  # noqa: ANN001, ARG002
        self, task_iso, run_integration, attempt, *, base_commits=None
    ):
        self.materialize_calls += 1
        # Review re-review M-1: the engine must hand the task's DURABLE historical bases
        # (`TaskIntegrationState.base_commits`) to every `materialize_conflict` call, so
        # the "no squash recorded under this attempt" fallback can parent its fresh squash
        # on the real base rather than on a `repo.base` that `ensure()` already refreshed.
        # Recorded here so a test can assert the engine actually threads it through.
        self.materialize_base_commits.append(base_commits)
        if self._materialize_results:
            return self._materialize_results.pop(0)
        return MaterializeResult(status="conflict", conflicted_paths=["f.txt"])


class _ScriptedIntegratorWithSquash(_ScriptedIntegrator):
    """`_ScriptedIntegrator` variant that fills in a "conflict_rerun" result's ``squash``
    dict dynamically from ``task_iso.repos`` at call time -- the real per-repo key is a
    content hash of the repo's common_dir, only known once the worktree exists, so it
    can't be hardcoded into a static `IntegrationResult` the way `reason`/`status` can."""

    def __init__(self, results: list[IntegrationResult], *, squash_sha: str) -> None:
        super().__init__(results)
        self._squash_sha = squash_sha

    def integrate(self, task_iso, run_integration, task_integration, attempt, *, agent_id):  # noqa: ANN001, ARG002
        self.calls += 1
        if self._results:
            result = self._results.pop(0)
            if result.status == "conflict_rerun" and not result.squash:
                result = dataclasses.replace(
                    result, squash={r.key: self._squash_sha for r in task_iso.repos}
                )
            return result
        return IntegrationResult(status="failed", reason="scripted_exhausted")


def _union_resolve_markers(text: str) -> str:
    """Generic conflict resolution: keep BOTH sides' content (ours then theirs), stripping
    git's own `<<<<<<</=======/>>>>>>>` markers -- a real, content-driven resolution
    (mirrors `merge-resolve.md`'s own Rule 2, "preserve both intents"), never a pre-known
    answer. Used by `_LadderExecutor`'s ``discover_and_resolve`` mode (review C-1: "resolve
    by reading and rewriting the file, not a pre-scripted answer")."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("<<<<<<<"):
            i += 1
            while i < len(lines) and not lines[i].startswith("======="):
                out.append(lines[i])
                i += 1
            i += 1  # skip the ======= marker
            while i < len(lines) and not lines[i].startswith(">>>>>>>"):
                out.append(lines[i])
                i += 1
            i += 1  # skip the >>>>>>> marker
        else:
            out.append(line)
            i += 1
    return "".join(out)


class _LadderExecutor(FakeExecutor):
    """`FakeExecutor` variant used throughout this file:

    - ``context_history``: EVERY dispatched `TaskContext` per task_id (not just
      last-invocation-wins) -- needed to distinguish a task's own dispatch from its later
      resolver-mode redispatch, since both share the same ``task_id``.
    - ``resolved_writes``: like `FakeExecutor`'s own ``repo_writes``, but applied ONLY
      when the dispatch is detected as a resolver-mode one (``ctx.instruction_path`` ends
      in the resolver instruction filename) -- `repo_writes` alone can't distinguish a
      task's own dispatch from its resolver redispatch (both share ``task_id``). Used by
      SCRIPTED-integrator tests, where there is no real git conflict to discover.
    - ``discover_and_resolve``: review C-1 -- a resolver dispatch under this flag reads the
      conflict manifest (its own input path), asserts `rebase_in_progress` is true in the
      worktree (a genuine `GitRepo` check, not a stub) and that the manifest's own
      ``rebase_in_progress``/``conflicted_paths`` are non-trivial, reads EACH conflicted
      path off disk, asserts it actually contains git's own conflict markers, and resolves
      it by reading and rewriting (`_union_resolve_markers`) -- never a pre-scripted
      answer. Used by `TestRealConflictLadder` (the real `Integrator`, real conflicts).
    """

    def __init__(
        self,
        *args,
        resolved_writes: dict[str, dict[str, dict[str, str]]] | None = None,
        discover_and_resolve: bool = False,
        fail_resolver_attempts: int = 0,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.context_history: dict[str, list[TaskContext]] = {}
        self.calls: dict[str, int] = {}
        self._resolved_writes = resolved_writes or {}
        self._discover_and_resolve = discover_and_resolve
        # M-1 re-review: the first `fail_resolver_attempts` resolver dispatches per task
        # still DISCOVER (read + assert live markers) but do NOT apply a fix -- simulating
        # AC-7's "resolver succeeds without resolving anything" so a test can drive a
        # SECOND (or later) genuine T2 attempt on the SAME still-unresolved conflict.
        self._fail_resolver_attempts = fail_resolver_attempts
        self.resolver_dispatch_count: dict[str, int] = {}
        self.discovered_conflicts: dict[str, list[str]] = {}

    def execute(self, ctx: TaskContext) -> TaskResult:
        self.context_history.setdefault(ctx.task_id, []).append(ctx)
        self.calls[ctx.task_id] = self.calls.get(ctx.task_id, 0) + 1
        is_resolver = ctx.instruction_path.endswith(escalation.RESOLVER_INSTRUCTION_FILENAME)
        # DISCOVER (read + assert live ground truth, then compute the resolution) BEFORE
        # `super().execute()` -- `FakeExecutor`'s own `repo_writes` (keyed only by
        # task_id, unaware of resolver-mode) would otherwise overwrite the genuine
        # conflict-marked file with the ORIGINAL non-conflicted content first, destroying
        # the very markers this mode exists to prove were read.
        resolved: dict[str, str] = {}
        if is_resolver and self._discover_and_resolve:
            discovered = self._discover_conflict(ctx)
            n = self.resolver_dispatch_count.get(ctx.task_id, 0)
            self.resolver_dispatch_count[ctx.task_id] = n + 1
            if n >= self._fail_resolver_attempts:
                resolved = discovered
        result = super().execute(ctx)
        # APPLY (write what was discovered) AFTER `super().execute()` so it -- like
        # `resolved_writes` below -- always wins over `repo_writes`' own reapplication.
        if resolved:
            repo_root = next(iter(ctx.repo_paths.values()))
            for rel, content in resolved.items():
                Path(repo_root, rel).write_text(content, encoding="utf-8")
            self.discovered_conflicts[ctx.task_id] = list(resolved)
        if result.status == "succeeded" and is_resolver and ctx.task_id in self._resolved_writes:
            for repo_id, files in self._resolved_writes[ctx.task_id].items():
                repo_root = ctx.repo_paths.get(repo_id)
                if repo_root is None:
                    continue
                for rel_path, content in files.items():
                    Path(repo_root, rel_path).write_text(content, encoding="utf-8")
        return result

    def _discover_conflict(self, ctx: TaskContext) -> dict[str, str]:
        """Review C-1: prove the resolver dispatch carries LIVE ground truth (a real
        conflict manifest, a genuinely mid-rebase worktree, real conflict markers on
        disk), then compute the resolution purely from what was read -- no pre-known
        answer. Returns ``{relative_path: resolved_content}``, applied by the caller
        AFTER `super().execute()` (see `execute`'s own comment for why)."""
        manifest_paths = [p for p in ctx.input_paths if p.endswith(".json")]
        assert manifest_paths, "resolver dispatched with no conflict manifest input"
        manifest = json.loads(Path(manifest_paths[0]).read_text(encoding="utf-8"))
        assert manifest["rebase_in_progress"] is True, "manifest claims no live conflict"
        conflicted_paths = manifest["conflicted_paths"]
        assert conflicted_paths, "manifest lists no conflicted paths"

        repo_root = next(iter(ctx.repo_paths.values()))
        git = GitRepo(repo_root)
        assert git.rebase_in_progress(repo_root), (
            "resolver dispatched but worktree is not mid-rebase"
        )

        resolved: dict[str, str] = {}
        for rel in conflicted_paths:
            text = Path(repo_root, rel).read_text(encoding="utf-8")
            assert "<<<<<<<" in text, f"{rel} has no conflict markers to discover"
            resolved[rel] = _union_resolve_markers(text)
        return resolved


# ---------------------------------------------------------------------------------------
# 1. Real end-to-end: two parallel isolated tasks, a genuine rebase conflict, a real T2
#    resolver dispatch that writes a real fix, `resume_integration` lands it.
# ---------------------------------------------------------------------------------------


class TestRealConflictLadder:
    def test_t1_conflict_t2_resolver_resolves_and_lands(self, tmp_path: Path) -> None:
        """Review C-1: the resolver GENUINELY discovers the conflict (`_LadderExecutor`'s
        ``discover_and_resolve`` mode asserts live `rebase_in_progress` + real markers on
        disk, then resolves by reading and rewriting -- never a pre-scripted answer)."""
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"]), _task("b", outputs=["out/b.txt"])])
        executor = _LadderExecutor(
            repo_writes={
                "a": {"core": {"f.txt": "line1-A\nline2\nline3\n"}},
                "b": {"core": {"f.txt": "line1-B\nline2\nline3\n"}},
            },
            discover_and_resolve=True,
        )
        orch = _orch(tmp_path, executor, max_parallel=2)
        state = _run(orch, wf, tmp_path)

        assert state.status == "succeeded"
        assert state.task_integration["a"].status == "integrated"
        assert state.task_integration["b"].status == "integrated"

        # Race-order-agnostic: exactly one of the two went through a real T2 resolve.
        resolver_counts = {tid: state.task_integration[tid].resolver_attempts for tid in ("a", "b")}
        assert sorted(resolver_counts.values()) == [0, 1]
        loser = next(tid for tid, n in resolver_counts.items() if n == 1)
        winner = "b" if loser == "a" else "a"

        # AC-4: the loser's SECOND dispatch substituted agent + instruction.
        history = executor.context_history[loser]
        assert len(history) == 2
        t1_ctx, t2_ctx = history
        assert t2_ctx.instruction_path.endswith("merge-resolve.md")
        assert t1_ctx.instruction_path != t2_ctx.instruction_path

        # S-2: forced disallowed_tools + resolver_env on the T2 dispatch ONLY.
        assert t1_ctx.agent.disallowed_tools == []
        assert "WebFetch" in t2_ctx.agent.disallowed_tools
        assert "WebSearch" in t2_ctx.agent.disallowed_tools
        assert "GIT_TERMINAL_PROMPT" not in t1_ctx.env
        assert t2_ctx.env.get("GIT_TERMINAL_PROMPT") == "0"
        assert t2_ctx.env.get("GIT_ASKPASS") == "/bin/false"

        # AC-4: conflict manifest appended to input_paths; a real file with the shape
        # AC-2 requires.
        manifest_inputs = [p for p in t2_ctx.input_paths if p.endswith(".json")]
        assert len(manifest_inputs) == 1
        assert Path(manifest_inputs[0]).is_file()

        # AC-3: merge-resolve.md was copied into the run dir, matching the packaged asset.
        packaged_asset = (
            Path(escalation.__file__).resolve().parents[1]
            / "templates"
            / "builtin"
            / "instructions"
            / escalation.RESOLVER_INSTRUCTION_FILENAME
        )
        assert Path(t2_ctx.instruction_path).read_text(
            encoding="utf-8"
        ) == packaged_asset.read_text(encoding="utf-8")

        # The winner never touched the resolver path at all (non-resolver dispatch
        # carries neither forced tools nor resolver env).
        winner_history = executor.context_history[winner]
        assert len(winner_history) == 1
        assert winner_history[0].agent.disallowed_tools == []
        assert "GIT_TERMINAL_PROMPT" not in winner_history[0].env

        # The resolver genuinely discovered the conflict (asserted live, inside the
        # executor itself, before this point -- these are a second, independent check).
        assert executor.discovered_conflicts.get(loser) == ["f.txt"]

        # Final content: the loser's DISCOVERED-AND-REWRITTEN resolution is what actually
        # landed (AC-6) -- a union of both sides' line-1 content, no markers left, proving
        # the write came from reading the real conflict rather than a scripted answer.
        shown = _git(["show", f"{state.integration.branch}:f.txt"], tmp_path / "repo")
        assert "<<<<<<<" not in shown and "=======" not in shown and ">>>>>>>" not in shown
        assert "line1-A" in shown
        assert "line1-B" in shown
        assert "line2" in shown and "line3" in shown


# ---------------------------------------------------------------------------------------
# 2. Scripted-integrator based: exercises `_prepare_resolver_dispatch`/
#    `_prepare_rerun_dispatch` (real worktree/git) with the FINAL squash/rebase/verify/CAS
#    outcome scripted, matching the established repo-wide pattern for this switch.
# ---------------------------------------------------------------------------------------


class TestResolverDispatchContext:
    def test_resolve_mode_writes_manifest_and_substitutes_agent(self, tmp_path: Path) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        executor = _LadderExecutor()
        scripted = _ScriptedIntegrator(
            [
                IntegrationResult(status="conflict_resolver", conflicted_paths=["f.txt"]),
                IntegrationResult(status="integrated"),
            ]
        )
        orch = _orch(tmp_path, executor, integrator=scripted)
        state = _run(orch, wf, tmp_path)

        assert state.status == "succeeded"
        history = executor.context_history["a"]
        assert len(history) == 2
        t1_ctx, t2_ctx = history
        assert t1_ctx.agent.executor == "fake"
        assert t2_ctx.instruction_path.endswith("merge-resolve.md")
        assert any(p.endswith("conflict-1.json") for p in t2_ctx.input_paths)
        # Task id/timeout/output dir untouched (AC-4).
        assert t2_ctx.task_id == "a"
        assert t2_ctx.timeout_seconds == t1_ctx.timeout_seconds
        # Review re-review M-1: the dispatch prep hands `materialize_conflict` the task's
        # durable per-repo base commits (recorded when the worktree was created), never
        # nothing -- without them its fallback path can misreport a live conflict as clean.
        assert scripted.materialize_calls == 1
        recorded = scripted.materialize_base_commits[0]
        assert recorded == state.task_integration["a"].base_commits
        assert recorded and all(len(sha) == 40 for sha in recorded.values())

    def test_head_moved_clean_reapply_skips_t2_and_lands(self, tmp_path: Path) -> None:
        """Review C-1 item 4: `materialize_conflict` reports "clean" (the head moved and
        the durable squash now applies without a conflict) -- the resolver is NEVER
        dispatched at all, and the task lands straight through `resume_integration`."""
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        executor = _LadderExecutor()
        scripted = _ScriptedIntegrator(
            [
                IntegrationResult(status="conflict_resolver", conflicted_paths=["f.txt"]),
                IntegrationResult(status="integrated"),
            ],
            materialize_results=[MaterializeResult(status="clean")],
        )
        orch = _orch(tmp_path, executor, integrator=scripted)
        state = _run(orch, wf, tmp_path)

        assert state.status == "succeeded"
        assert state.task_integration["a"].status == "integrated"
        assert scripted.materialize_calls == 1
        # The resolver was never dispatched -- only T1's own (normal-agent) dispatch.
        history = executor.context_history["a"]
        assert len(history) == 1
        assert history[0].instruction_path.endswith("design.md")
        # resume_integration still ran (scripted.calls counts BOTH integrate/resume calls).
        assert scripted.calls == 2

    def test_defensive_fallback_when_resolver_agent_unset(self, tmp_path: Path) -> None:
        """A directly-injected escalation_hook test-double can set mode == "resolve"
        without `escalate()`'s own preconditions holding (no `resolver_agent` configured)
        -- the dispatch must not crash, and falls back to the task's own agent."""
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow(
            [_task("a", outputs=["out/a.txt"])],
            integration=IntegrationSpec(sync_checkout="never"),  # no resolver_agent
        )
        executor = _LadderExecutor()
        scripted = _ScriptedIntegrator(
            [
                IntegrationResult(status="conflict_resolver", conflicted_paths=["f.txt"]),
                IntegrationResult(status="integrated"),
            ]
        )
        orch = _orch(tmp_path, executor, integrator=scripted)
        state = _run(orch, wf, tmp_path)

        assert state.status == "succeeded"
        history = executor.context_history["a"]
        assert len(history) == 2
        # No substitution happened -- same instruction path both times.
        assert history[0].instruction_path == history[1].instruction_path


class TestRerunDispatchContext:
    def test_rerun_resets_worktree_and_exports_previous_patch(self, tmp_path: Path) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        repo = _git_repo(tmp_path)
        # A commit representing the squash the T1 dispatch would have produced (the diff
        # T3's `export_previous_patch` needs a real base..squash pair to compute) -- built
        # directly on the shared repo, then main is reset back so the run itself starts
        # clean. `IntegrationResult.squash` is keyed by the WORKTREE GROUP key (a content
        # hash of the repo's common_dir, computed only once the worktree exists) rather
        # than the reposet's own "core" id, so `_ScriptedIntegratorWithSquash` fills it in
        # dynamically from `task_iso.repos` at call time instead of a static dict here.
        base_sha = _git(["rev-parse", "HEAD"], repo).strip()
        (repo / "f.txt").write_text("squashed-change\nline2\nline3\n")
        _git(["add", "-A"], repo)
        _git(
            ["-c", "user.name=t", "-c", "user.email=t@x.invalid", "commit", "-q", "-m", "sq"],
            repo,
        )
        squash_sha = _git(["rev-parse", "HEAD"], repo).strip()
        _git(["reset", "-q", "--hard", base_sha], repo)

        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        executor = _LadderExecutor(
            repo_writes={"a": {"core": {"f.txt": "changed\nline2\nline3\n"}}}
        )
        scripted = _ScriptedIntegratorWithSquash(
            [
                IntegrationResult(status="conflict_rerun", reason="verify_failed"),
                IntegrationResult(status="integrated"),
            ],
            squash_sha=squash_sha,
        )
        orch = _orch(tmp_path, executor, integrator=scripted)
        state = _run(orch, wf, tmp_path)

        assert state.status == "succeeded"
        history = executor.context_history["a"]
        assert len(history) == 2
        t1_ctx, t2_ctx = history
        # T3 does NOT substitute agent/instruction (unlike T2) -- only appends the patch.
        assert t2_ctx.instruction_path == t1_ctx.instruction_path
        assert t2_ctx.agent.disallowed_tools == []
        patch_inputs = [p for p in t2_ctx.input_paths if p.endswith(".patch")]
        assert len(patch_inputs) == 1
        patch_path = Path(patch_inputs[0])
        assert patch_path.is_file()
        assert "squashed-change" in patch_path.read_text(encoding="utf-8")

        ti = state.task_integration["a"]
        assert ti.reruns == 1
        # AC-8: the worktree base recorded in state was updated (repo-key, not "core" --
        # see the comment above).
        assert all(v for v in ti.base_commits.values())


class TestTerminationNoInfiniteLoop:
    def test_resolver_succeeds_but_resolves_nothing_terminates_at_t3(self, tmp_path: Path) -> None:
        """AC-7: a resolver "succeeds" (executor exit ok) without resolving anything --
        `rebase_continue` still reports conflicts -- escalate() is called again with the
        counter already incremented -- terminates (here: at T3, then lands) rather than
        looping forever."""
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        executor = _LadderExecutor()  # no resolved_writes -- resolver writes nothing
        scripted = _ScriptedIntegrator(
            [
                IntegrationResult(status="conflict_resolver", conflicted_paths=["f.txt"]),
                IntegrationResult(status="conflict_rerun", conflicted_paths=["f.txt"]),
                IntegrationResult(status="integrated"),
            ]
        )
        orch = _orch(tmp_path, executor, integrator=scripted)
        state = _run(orch, wf, tmp_path)

        assert state.status == "succeeded"
        assert scripted.calls == 3
        assert executor.calls["a"] == 3


class TestT4Failure:
    def test_fails_with_reason_naming_paths_worktree_branch_and_retains_both(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        executor = _LadderExecutor()
        scripted = _ScriptedIntegrator(
            [IntegrationResult(status="failed", reason="conflict_unresolved: paths=[f.txt]")]
        )
        orch = _orch(tmp_path, executor, integrator=scripted)
        state = _run(orch, wf, tmp_path)

        assert state.status == "failed"
        ti = state.task_integration["a"]
        assert ti.status == "failed"
        assert ti.last_error is not None and "f.txt" in ti.last_error
        # Retained regardless of keep_worktrees (default "on_failure" would retain
        # anyway, but this proves the branch too, via the shared repo's refs).
        assert ti.branches
        branch = next(iter(ti.branches.values()))
        refs = _git(["for-each-ref", f"refs/heads/{branch}"], tmp_path / "repo")
        assert branch in refs
        worktree_path = next(iter(ti.repos.values()))
        assert Path(worktree_path).is_dir()


class TestFullLadderUnderDefaultRetryPolicy:
    def test_t1_t2_t3_completes_under_default_retry_policy(self, tmp_path: Path) -> None:
        """R-9: T2/T3 redispatches are new `_run_with_retries` calls with their OWN fresh
        retry budget -- never gated by `RetryPolicy.max_attempts` (default 1). A task
        that gets requeued THREE times under the default policy must not be marked
        failed by retry exhaustion at any point.
        """
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        # DEFAULT RetryPolicy(max_attempts=1) applies -- neither the task nor the
        # workflow overrides it.
        assert wf.tasks[0].retries is None
        assert wf.defaults.retries is not None and wf.defaults.retries.max_attempts == 1
        executor = _LadderExecutor()
        scripted = _ScriptedIntegrator(
            [
                IntegrationResult(status="conflict_resolver", conflicted_paths=["f.txt"]),
                IntegrationResult(status="conflict_rerun", conflicted_paths=["f.txt"]),
                IntegrationResult(status="integrated"),
            ]
        )
        orch = _orch(tmp_path, executor, integrator=scripted)
        state = _run(orch, wf, tmp_path)

        assert state.status == "succeeded"
        assert state.tasks["a"].status == "succeeded"
        assert state.task_integration["a"].status == "integrated"


class TestNonResolverDispatchCarriesNoContainment:
    def test_a_task_that_never_conflicts_carries_neither(self, tmp_path: Path) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        executor = _LadderExecutor()
        scripted = _ScriptedIntegrator([IntegrationResult(status="integrated")])
        orch = _orch(tmp_path, executor, integrator=scripted)
        state = _run(orch, wf, tmp_path)

        assert state.status == "succeeded"
        ctx = executor.context_history["a"][0]
        assert ctx.agent.disallowed_tools == []
        assert "GIT_TERMINAL_PROMPT" not in ctx.env
        assert "GIT_ASKPASS" not in ctx.env


# ---------------------------------------------------------------------------------------
# Cost accounting (D9/AC-5, amendment 14) -- BudgetCounters, not only cumulative_*.
# ---------------------------------------------------------------------------------------


class TestCostBreakerAcrossTheLadder:
    def test_task_cost_usd_breaker_trips_on_the_second_conflict_cycle(self, tmp_path: Path) -> None:
        """A task that conflicts TWICE (T1 -> T2 -> lands) accumulates attempt-1's cost
        plus the resolver attempt's cost. A breaker threshold set JUST above attempt-1's
        own cost alone must trip once the resolver cycle's cost is added -- and the trip
        must be visible on `BudgetCounters` (not only `TaskRunState.cumulative_cost_usd`),
        per amendment 14 -- a missing/wrong accounting fix cannot hide behind a passing
        cumulative-only assertion.
        """
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow(
            [_task("a", outputs=["out/a.txt"])],
            circuit_breakers=[
                CircuitBreakerSpec(
                    id="cost-cap", condition="task_cost_usd", action="fail", threshold=1.5
                )
            ],
        )
        executor = _CostLadderExecutor(
            costs={"a": [1.0, 1.0]},
        )
        scripted = _ScriptedIntegrator(
            [
                IntegrationResult(status="conflict_resolver", conflicted_paths=["f.txt"]),
                IntegrationResult(status="integrated"),
            ]
        )
        cfg = EstimatorConfig(chars_per_token=4, pessimism_buffer=1.0, output_allowance_tokens=0)
        budget_spec = BudgetSpec(total_tokens=1_000_000, estimator=cfg)
        wf.budget = budget_spec
        store, rs_store = _workspace(tmp_path)
        mgr = DefaultBudgetManager(budget_spec, lambda: _FIXED_DT)
        estimator = HeuristicTokenEstimator(store)
        orch = Orchestrator(
            executor,
            store,
            rs_store,
            integrator=scripted,
            budget_manager=mgr,
            estimator=estimator,
        )
        state = orch.run(wf, _reposet(str(tmp_path)), _agents())

        assert state.status == "failed"
        assert "cost-cap" in [tb.condition for tb in state.tripped_breakers] or any(
            tb.id == "cost-cap" for tb in state.tripped_breakers
        )
        assert state.tasks["a"].cumulative_cost_usd == pytest.approx(2.0)
        # BudgetCounters-level assertion (amendment 14): the SECOND cycle's charge/
        # reconcile actually reached the shared ledger, keyed by its own dispatch_cycle
        # (T-Ac6Vd9's cycle_key), not silently dropped.
        assert cycle_key("a", 1) in state.budget_counters.reconciled_cycles


class _CostLadderExecutor(_LadderExecutor):
    """`_LadderExecutor` variant that reports a FIXED cost per call (task_id -> list of
    per-call USD costs, repeating the last entry once exhausted) -- needed for the cost-
    breaker test above, independent of `FakeExecutor`'s own `token_outputs` (which does
    not carry `cost_usd`).
    """

    def __init__(self, *args, costs: dict[str, list[float]], **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._costs = {k: list(v) for k, v in costs.items()}

    def execute(self, ctx: TaskContext) -> TaskResult:
        n = self.calls.get(ctx.task_id, 0)  # pre-increment count, from the base class
        result = super().execute(ctx)
        queue = self._costs.get(ctx.task_id)
        if queue and result.status == "succeeded":
            cost = queue[min(n, len(queue) - 1)]
            result.cost_usd = cost
            result.actuals_available = True
            result.input_tokens = result.input_tokens or 0
            result.output_tokens = result.output_tokens or 0
        return result


# ---------------------------------------------------------------------------------------
# Resume mid-escalation
# ---------------------------------------------------------------------------------------


class TestResumeMidEscalation:
    def test_resume_after_conflict_resolver_settle_completes_via_t2(self, tmp_path: Path) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])

        executor1 = _LadderExecutor()
        scripted1 = _ScriptedIntegrator(
            [IntegrationResult(status="conflict_resolver", conflicted_paths=["f.txt"])]
        )
        store, rs_store = _workspace(tmp_path)

        def _cancel_after_cycle_one() -> bool:
            return executor1.calls.get("a", 0) >= 1

        orch1 = Orchestrator(
            executor1, store, rs_store, integrator=scripted1, cancel_fn=_cancel_after_cycle_one
        )
        state = orch1.run(wf, _reposet(str(tmp_path)), _agents())

        assert state.status == "cancelled"
        ti = state.task_integration["a"]
        assert ti.status == "conflict_resolver"
        assert ti.mode == "resolve"
        assert ti.resolver_attempts == 1

        rs_store.prepare_resume(state, wf)
        assert state.tasks["a"].status == "pending"
        # Integration bookkeeping (unlike TaskRunState) survives resume untouched --
        # HLD's documented reason it lives on RunState, not TaskRunState.
        assert state.task_integration["a"].mode == "resolve"
        rs_store.save(state)

        executor2 = _LadderExecutor()
        scripted2 = _ScriptedIntegrator([IntegrationResult(status="integrated")])
        orch2 = Orchestrator(executor2, store, rs_store, integrator=scripted2)
        resumed = orch2.run(wf, _reposet(str(tmp_path)), _agents(), run_state=state)

        assert resumed.status == "succeeded"
        assert resumed.task_integration["a"].status == "integrated"
        # The resumed dispatch went straight to the resolver instruction (mode carried
        # forward across the resume).
        ctx = executor2.context_history["a"][0]
        assert ctx.instruction_path.endswith("merge-resolve.md")

    def test_real_git_resume_mid_t2_rematerializes_and_completes(self, tmp_path: Path) -> None:
        """Review C-1 item 4: a REAL Integrator (no scripting) -- cancelled right after the
        loser's T1 conflict settles (before T2 ever dispatches), then resumed with a FRESH
        `Orchestrator`/`Integrator` (no in-memory state carried over -- everything
        `materialize_conflict` needs comes from durable git refs). The resolver still
        genuinely discovers the conflict and the task lands.
        """
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"]), _task("b", outputs=["out/b.txt"])])
        executor1 = _LadderExecutor(
            repo_writes={
                "a": {"core": {"f.txt": "line1-A\nline2\nline3\n"}},
                "b": {"core": {"f.txt": "line1-B\nline2\nline3\n"}},
            },
        )
        store, rs_store = _workspace(tmp_path)

        def _cancel_after_first_wave() -> bool:
            return sum(executor1.calls.values()) >= 2

        orch1 = Orchestrator(
            executor1, store, rs_store, max_parallel=2, cancel_fn=_cancel_after_first_wave
        )
        state = orch1.run(wf, _reposet(str(tmp_path)), _agents())

        assert state.status == "cancelled"
        resolver_counts = {tid: state.task_integration[tid].resolver_attempts for tid in ("a", "b")}
        assert sorted(resolver_counts.values()) == [0, 1]
        loser = next(tid for tid, n in resolver_counts.items() if n == 1)
        assert state.task_integration[loser].mode == "resolve"
        # The resolver was never dispatched for the loser (cancelled before T2).
        assert len(executor1.context_history[loser]) == 1

        rs_store.prepare_resume(state, wf)
        assert state.tasks[loser].status == "pending"
        rs_store.save(state)

        executor2 = _LadderExecutor(
            repo_writes={
                "a": {"core": {"f.txt": "line1-A\nline2\nline3\n"}},
                "b": {"core": {"f.txt": "line1-B\nline2\nline3\n"}},
            },
            discover_and_resolve=True,
        )
        orch2 = Orchestrator(executor2, store, rs_store, max_parallel=2)
        resumed = orch2.run(wf, _reposet(str(tmp_path)), _agents(), run_state=state)

        assert resumed.status == "succeeded"
        assert resumed.task_integration[loser].status == "integrated"
        assert executor2.discovered_conflicts.get(loser) == ["f.txt"]
        loser_history = executor2.context_history[loser]
        assert len(loser_history) == 1  # only the T2 resolver dispatch happened post-resume
        assert loser_history[0].instruction_path.endswith("merge-resolve.md")


# ---------------------------------------------------------------------------------------
# Cancellation between tiers
# ---------------------------------------------------------------------------------------


class TestCancelBetweenTiers:
    def test_cancel_after_t2_requeue_halts_before_t3_dispatch(self, tmp_path: Path) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        executor = _LadderExecutor()
        scripted = _ScriptedIntegrator(
            [
                IntegrationResult(status="conflict_resolver", conflicted_paths=["f.txt"]),
                IntegrationResult(status="integrated"),
            ]
        )

        def _cancel_after_one_dispatch() -> bool:
            return executor.calls.get("a", 0) >= 1

        orch = _orch(tmp_path, executor, integrator=scripted, cancel_fn=_cancel_after_one_dispatch)
        state = _run(orch, wf, tmp_path)

        assert state.status == "cancelled"
        assert executor.calls.get("a", 0) == 1  # T2 never dispatched
        assert state.task_integration["a"].status == "conflict_resolver"
        assert scripted.calls == 1


# ---------------------------------------------------------------------------------------
# Golden event stream for a non-isolated run -- unchanged (NFR-2).
# ---------------------------------------------------------------------------------------


class TestNonIsolatedRunUnchanged:
    def test_no_integration_events_no_task_integration_entries(self, tmp_path: Path) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            defaults=WorkflowDefaults(isolation="none"),  # type: ignore[arg-type]
            tasks=[_task("a", outputs=["out/a.txt"], isolation="none")],
        )
        store, rs_store = _workspace(tmp_path)
        executor = _LadderExecutor()
        orch = Orchestrator(executor, store, rs_store)
        state = orch.run(wf, {"rs": RepoSet(workspace_root=str(tmp_path), repos=[])}, _agents())

        assert state.status == "succeeded"
        assert state.task_integration == {}
        assert state.integration.active is False
