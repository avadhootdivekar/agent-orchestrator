"""U3 of the no-repo-set (workspace-only) mode (ADR-0022, no-repo-set-mode-hld.md §8).

Engine + executor layer: `Engine.run` computes `repo_paths={}` when the workflow has no
`repo_set` (S13); isolation degrades once with `no_git_repos` (S14); survival records nothing
(S15); the result cache round-trips with empty repo state (S16); the prompt drops the `Repos:`
clause (S18); and NFR-1-style path safety holds — artifacts only ever land under the workspace.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.errors import ArtifactPathError
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.executors.prompt import build_prompt
from agent_orchestrator.models import (
    AgentSpec,
    RunState,
    TaskContext,
    TaskSpec,
    WorkflowDefaults,
    WorkflowSpec,
)
from agent_orchestrator.runstate import RunStateStore
from agent_orchestrator.survival import record_git_start
from tests.cache.test_engine_result_cache import (
    INSTRUCTION,
    CostlyFakeExecutor,
    World,
)

AGENTS = {"ag": AgentSpec(executor="fake")}


def _wf(tasks: list[TaskSpec], **kw: object) -> WorkflowSpec:
    return WorkflowSpec(version="1.0", id="norepo", tasks=tasks, **kw)  # type: ignore[arg-type]


def _task(tid: str, **kw: object) -> TaskSpec:
    return TaskSpec(id=tid, agent="ag", instruction=INSTRUCTION, **kw)  # type: ignore[arg-type]


def _chain() -> WorkflowSpec:
    return _wf(
        [
            _task("a", outputs=["out/a.txt"]),
            _task("b", depends_on=["a"], inputs=["out/a.txt"], outputs=["out/b.txt"]),
        ]
    )


def _orch(ws: Path, executor: FakeExecutor, **kw: object) -> Orchestrator:
    store = LocalFsArtifactStore(str(ws))
    return Orchestrator(executor, store, RunStateStore(str(ws), store), **kw)  # type: ignore[arg-type]


def _files(root: Path) -> set[Path]:
    return {p for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / INSTRUCTION).parent.mkdir(parents=True)
    (root / INSTRUCTION).write_text("# stub\n")
    return root


# --- S13: engine runs to completion with repo_paths == {} ---------------------------------
def test_engine_runs_no_repo_workflow_to_completion_with_empty_repo_paths(ws: Path) -> None:
    spy = FakeExecutor()
    state = _orch(ws, spy).run(_chain(), {}, AGENTS)

    assert state.status == "succeeded"
    assert state.repo_set is None
    assert {t: s.status for t, s in state.tasks.items()} == {"a": "succeeded", "b": "succeeded"}
    assert [spy.contexts[t].repo_paths for t in ("a", "b")] == [{}, {}]
    assert (ws / "out" / "a.txt").exists() and (ws / "out" / "b.txt").exists()


def test_repo_set_mode_still_resolves_repo_paths(ws: Path) -> None:
    # Byte-identical regression for the repo_set branch.
    from agent_orchestrator.models import RepoRef, RepoSet

    reposets = {
        "rs": RepoSet(workspace_root=str(ws), repos=[RepoRef(id="core", path=".", role="primary")])
    }
    spy = FakeExecutor()
    state = _orch(ws, spy).run(
        _wf([_task("a", outputs=["out/a.txt"])], repo_set="rs"), reposets, AGENTS
    )
    assert state.status == "succeeded"
    assert spy.contexts["a"].repo_paths == {"core": str(ws.resolve())}


# --- S14: isolation degrades once, run still succeeds -------------------------------------
def test_isolation_degrades_with_no_git_repos_and_logs_once(
    ws: Path, caplog: pytest.LogCaptureFixture
) -> None:
    wf = _wf([_task("a", outputs=["out/a.txt"])], defaults=WorkflowDefaults(isolation="worktree"))
    with caplog.at_level(logging.INFO, logger="agent_orchestrator"):
        state = _orch(ws, FakeExecutor()).run(wf, {}, AGENTS)

    assert state.status == "succeeded"
    assert state.integration.active is False
    assert state.integration.degraded_reason == "no_git_repos"
    degraded = [r for r in caplog.records if getattr(r, "event", None) == "integration.degraded"]
    assert len(degraded) == 1
    assert (ws / "out" / "a.txt").exists()


# --- S15: survival records nothing --------------------------------------------------------
def test_survival_records_no_git_repos(ws: Path) -> None:
    state = _orch(ws, FakeExecutor()).run(_wf([_task("a", outputs=["out/a.txt"])]), {}, AGENTS)
    assert state.git_repos == {} and state.git_start_heads == {}
    fresh = RunState.model_validate(state.model_dump())
    record_git_start(fresh, {})  # direct: empty in, empty out, never raises
    assert fresh.git_repos == {} and fresh.git_start_heads == {}


# --- S16: result cache hit/miss round-trips with empty repo state -------------------------
def test_result_cache_round_trips_without_repos(tmp_path: Path, monkeypatch) -> None:
    from agent_orchestrator.cache import constants as c

    monkeypatch.delenv(c.ENV_CACHE, raising=False)
    world = World(tmp_path.resolve())
    wf = _wf([_task("a", cache=True, skip_if_outputs_exist=False, outputs=["out/a.txt"])])
    agents = World.agents()

    first = CostlyFakeExecutor()
    s1 = world.orchestrator(first).run(wf, {}, agents)
    assert first.executed == ["a"] and s1.status == "succeeded"
    assert (s1.result_cache["a"].outcome, s1.result_cache["a"].stored) == ("miss", True)
    body = world.out("out/a.txt").read_bytes()
    world.delete_outputs("out/a.txt")

    second = CostlyFakeExecutor()
    s2 = world.orchestrator(second).run(wf, {}, agents)
    assert second.executed == []
    assert s2.result_cache["a"].outcome == "hit"
    assert world.out("out/a.txt").read_bytes() == body


# --- S18: prompt drops the phantom `Repos:` clause ----------------------------------------
def _ctx(agent: AgentSpec, repo_paths: dict[str, str]) -> TaskContext:
    return TaskContext(
        task_id="t",
        run_id="r",
        agent=agent,
        instruction_path="i.md",
        input_paths=["in.txt"],
        output_paths=["out.txt"],
        repo_paths=repo_paths,
        timeout_seconds=60,
        cwd=".",
    )


def test_prompt_omits_repos_clause_when_empty() -> None:
    prompt = build_prompt(_ctx(AgentSpec(executor="fake"), {}))
    assert "Repos" not in prompt and "repos" not in prompt
    assert prompt.endswith("Write outputs to: out.txt.")


def test_prompt_with_repos_is_unchanged() -> None:
    prompt = build_prompt(_ctx(AgentSpec(executor="fake"), {"core": "/w/core"}))
    assert prompt.endswith("Write outputs to: out.txt. Repos: core=/w/core.")


def test_custom_template_without_repos_clause_is_untouched() -> None:
    agent = AgentSpec(executor="fake", prompt_template="Do {instruction} -> {outputs} [{repos}]")
    assert build_prompt(_ctx(agent, {})) == "Do i.md -> out.txt []"


# --- A2.3 / NFR-1: path safety, artifacts only under the workspace ------------------------
@pytest.mark.parametrize("bad", ["../escape.txt", "out/../../escape.txt"])
def test_relative_escape_is_rejected(ws: Path, bad: str) -> None:
    store = LocalFsArtifactStore(str(ws))
    with pytest.raises(ArtifactPathError):
        store.resolve(bad)


def test_absolute_outside_root_is_rejected(ws: Path, tmp_path: Path) -> None:
    with pytest.raises(ArtifactPathError):
        LocalFsArtifactStore(str(ws)).resolve(str(tmp_path / "elsewhere.txt"))


def test_symlink_out_of_workspace_is_rejected(ws: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, ws / "link")
    with pytest.raises(ArtifactPathError):
        LocalFsArtifactStore(str(ws)).resolve("link/x.txt")


def test_escaping_output_fails_the_task_and_writes_nothing_outside(
    ws: Path, tmp_path: Path
) -> None:
    before = _files(tmp_path)
    wf = _wf([_task("a", outputs=["../escape.txt"])])
    try:
        state = _orch(ws, FakeExecutor()).run(wf, {}, AGENTS)
        assert state.status != "succeeded"
    except ArtifactPathError:
        pass  # rejected up front is equally acceptable
    assert not (tmp_path / "escape.txt").exists()
    assert {p for p in _files(tmp_path) - before if ws not in p.parents} == set()


def test_full_run_writes_only_under_workspace(ws: Path, tmp_path: Path) -> None:
    before = _files(tmp_path)
    state = _orch(ws, FakeExecutor()).run(_chain(), {}, AGENTS)
    assert state.status == "succeeded"
    created = _files(tmp_path) - before
    assert created, "run should have produced artifacts"
    assert all(ws in p.parents for p in created), sorted(str(p) for p in created)
