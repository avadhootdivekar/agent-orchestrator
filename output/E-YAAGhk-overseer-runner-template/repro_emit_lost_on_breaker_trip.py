"""Scratch repro (architect verification only, not committed): a breaker tripping at an
emit_tasks task's own settle halts BEFORE injection; resume then never re-injects."""
from pathlib import Path

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import (AgentSpec, CircuitBreakerSpec, RepoRef, RepoSet,
                                       TaskSpec, WorkflowSpec)
from agent_orchestrator.runstate import RunStateStore


def test_emit_lost_on_breaker_trip(tmp_path: Path) -> None:
    store = LocalFsArtifactStore(str(tmp_path))
    rs = RunStateStore(str(tmp_path), store)
    agents = {"ag": AgentSpec(executor="fake")}
    reposets = {"rs": RepoSet(workspace_root=str(tmp_path),
                              repos=[RepoRef(id="core", path=".", role="primary")])}
    emitter = TaskSpec(id="emit", agent="ag", instruction="i.md", outputs=["out/emit.md"],
                       emit_tasks=True, task_manifest_path="out/manifest.json")
    (tmp_path / "i.md").write_text("x")
    flag = tmp_path / "control" / "halt.flag"
    wf = WorkflowSpec(version="1.0", id="wf", repo_set="rs", tasks=[emitter],
                      circuit_breakers=[CircuitBreakerSpec(id="kill", condition="stop_file",
                                                           path="control/halt.flag",
                                                           action="stop")])
    manifest = {"tasks": [{"id": "child", "agent": "ag", "instruction": "i.md",
                           "depends_on": ["emit"], "outputs": ["out/child.md"]}]}
    flag.parent.mkdir(parents=True)
    flag.write_text("")  # simulate: flag present when the emitter settles
    ex = FakeExecutor(emit_payloads={"emit": manifest})
    state = Orchestrator(ex, store, rs).run(wf, reposets, agents)
    print("run1:", state.status, {k: v.status for k, v in state.tasks.items()},
          "injected=", [t.id for t in state.injected_tasks])
    flag.unlink()
    loaded = rs.load(state.run_id)
    wf2 = WorkflowSpec(version="1.0", id="wf", repo_set="rs", tasks=[emitter],
                       circuit_breakers=wf.circuit_breakers)
    loaded = rs.prepare_resume(loaded, wf2)
    state2 = Orchestrator(FakeExecutor(emit_payloads={"emit": manifest}), store, rs).run(
        wf2, reposets, agents, run_state=loaded)
    print("run2:", state2.status, {k: v.status for k, v in state2.tasks.items()},
          "injected=", [t.id for t in state2.injected_tasks])
