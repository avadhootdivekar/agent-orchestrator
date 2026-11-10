"""T-Sn6Brr engine tests: soft-barrier emitters, inbox ``inject`` / ``edit_pending``, resume."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator import engine as engine_mod
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import (
    SentinelSpec,
    TaskContext,
    TaskResult,
    TaskSpec,
    WorkflowSpec,
)
from agent_orchestrator.sentinel_settings import resolve_sentinel_settings
from tests.test_sentinel_tick import WAIT_S, _run_dir, _setup, _wait_for, _write_entry

INSTR = "specs/examples/instructions/design.md"


class Exec(FakeExecutor):
    """Records dispatch order/counts and peak concurrency; per-task hooks run in the worker."""

    def __init__(self, hooks: dict[str, Any] | None = None, emit: dict | None = None) -> None:
        super().__init__(emit_payloads=emit or {})
        self.hooks = hooks or {}
        self.lock = threading.Lock()
        self.order: list[str] = []
        self.running = 0
        self.max_running = 0

    def execute(self, ctx: TaskContext) -> TaskResult:
        with self.lock:
            self.order.append(ctx.task_id)
            self.running += 1
            self.max_running = max(self.max_running, self.running)
        try:
            hook = self.hooks.get(ctx.task_id)
            if hook is not None:
                hook(self, ctx)
            return super().execute(ctx)
        finally:
            with self.lock:
                self.running -= 1


@pytest.fixture(autouse=True)
def _fast_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    real = engine_mod.wait

    def fast(fs: Any, timeout: float | None = None, return_when: Any = None) -> Any:
        return real(fs, timeout=0.02 if timeout is not None else None, return_when=return_when)

    monkeypatch.setattr(engine_mod, "wait", fast)


def T(tid: str, **kw: Any) -> TaskSpec:
    return TaskSpec(id=tid, agent="ag", instruction=INSTR, **kw)


def WF(tasks: list[TaskSpec]) -> WorkflowSpec:
    return WorkflowSpec(version="1.0", id="wf", repo_set="rs", tasks=tasks)


def manifest(*children: dict) -> dict:
    return {"tasks": [{"agent": "ag", "instruction": INSTR, **c} for c in children]}


def sentinel(**kw: Any):
    return resolve_sentinel_settings(workflow=SentinelSpec(enabled=True, tick_seconds=1, **kw))


# ---- soft barrier -------------------------------------------------------------------------
def test_is_barrier_modes(tmp_path: Path) -> None:
    store, rs, _, _ = _setup(tmp_path)
    orch = Orchestrator(Exec(), store, rs)
    wf = WF(
        [
            T("h", emit_tasks=True, task_manifest_path="m.json"),
            T("s", emit_tasks=True, task_manifest_path="m.json", barrier="soft"),
            T("p"),
        ]
    )
    assert orch._is_barrier(wf.task("h"), wf) is True
    assert orch._is_barrier(wf.task("s"), wf) is False
    assert orch._is_barrier(wf.task("p"), wf) is False


def test_soft_emitter_overlaps_three_unrelated_tasks(tmp_path: Path) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    release = threading.Event()
    seen: dict[str, int] = {}

    def block(ex: Exec, ctx: TaskContext) -> None:
        assert release.wait(WAIT_S)

    def emitter(ex: Exec, ctx: TaskContext) -> None:
        assert _wait_for(lambda: ex.running >= 4)  # 3 blockers + this emitter
        seen["running_at_emit"] = ex.running
        release.set()

    ex = Exec(
        {"u1": block, "u2": block, "u3": block, "em": emitter},
        emit={"em": manifest({"id": "kid", "depends_on": ["em"]})},
    )
    wf = WF(
        [
            T("u1"),
            T("u2"),
            T("u3"),
            T("em", emit_tasks=True, task_manifest_path="out/m.json", barrier="soft"),
        ]
    )
    orch = Orchestrator(ex, store, rs, max_parallel=4)
    state = orch.run(wf, reposets, agents)
    assert state.status == "succeeded", state.tasks
    assert seen["running_at_emit"] == 4  # emitter ran WHILE the 3 unrelated tasks were in flight
    assert sorted(ex.order) == ["em", "kid", "u1", "u2", "u3"]  # no double dispatch
    assert state.tasks["kid"].status == "succeeded" and state.tasks["kid"].origin == "injected"


def test_soft_manifest_edge_into_running_task_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    release = threading.Event()
    real = engine_mod.graph_check.check_additive_manifest

    def spy(*a: Any, **k: Any) -> None:
        try:
            real(*a, **k)
        finally:
            release.set()  # `slow` is still running while the emitter's manifest is checked

    monkeypatch.setattr(engine_mod.graph_check, "check_additive_manifest", spy)

    def block(ex: Exec, ctx: TaskContext) -> None:
        assert release.wait(WAIT_S)

    ex = Exec(
        {
            "slow": block,
            "em": lambda ex, ctx: _wait_for(lambda: ex.running >= 2),
        },
        emit={"em": manifest({"id": "late", "outputs": ["x/in"]})},
    )
    # `slow` consumes x/in: a late producer would become a backward edge into a running task.
    wf = WF(
        [
            T("slow", inputs=["x/in"]),
            T("em", emit_tasks=True, task_manifest_path="out/m.json", barrier="soft"),
        ]
    )
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "in").write_text("seed")
    state = Orchestrator(ex, store, rs, max_parallel=2).run(wf, reposets, agents)
    assert state.tasks["em"].status == "failed" and state.status == "failed"
    assert "late" not in state.tasks


# ---- inbox inject / edit_pending ----------------------------------------------------------
def _processed(run_dir: Path, name: str) -> Path:
    return run_dir / "control" / "inbox-processed" / f"{name}.json"


def _rejected_rule(run_dir: Path, name: str) -> str:
    return json.loads((run_dir / "control" / "inbox-rejected" / f"{name}.reason.json").read_text())[
        "rule_id"
    ]


def test_edit_pending_priority_changes_dispatch_order_and_bad_edit_quarantined(
    tmp_path: Path,
) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    wf = WF([T("a"), T("b"), T("c")])
    state0 = rs.new_run(wf)
    run_dir = _run_dir(rs, state0)

    def first(ex: Exec, ctx: TaskContext) -> None:
        _write_entry(run_dir, "ctl-prio-000001", "edit_pending", {"priority": {"c": 5}})
        _write_entry(run_dir, "ctl-bad-0000001", "edit_pending", {"cancel": ["a"]})
        assert _wait_for(lambda: _processed(run_dir, "ctl-prio-000001").exists())
        assert _wait_for(
            lambda: (run_dir / "control" / "inbox-rejected" / "ctl-bad-0000001.json").exists()
        )

    ex = Exec({"a": first})
    orch = Orchestrator(ex, store, rs, max_parallel=1, sentinel=sentinel())
    state = orch.run(wf, reposets, agents, run_state=state0)
    assert state.status == "succeeded"
    assert ex.order == ["a", "c", "b"]  # c was reprioritised ahead of b
    assert _rejected_rule(run_dir, "ctl-bad-0000001") == "GE-NOT-PENDING"
    assert [e.op for e in state.graph_edits] == ["priority"]
    assert state.graph_edits[0].source == "cli:tester"


def test_edit_pending_add_edge_reorders_and_persists(tmp_path: Path) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    wf = WF([T("a"), T("b"), T("c")])
    state0 = rs.new_run(wf)
    run_dir = _run_dir(rs, state0)

    def first(ex: Exec, ctx: TaskContext) -> None:
        # b must now wait for c (c would otherwise run after b in D6 order)
        _write_entry(
            run_dir, "ctl-edge-000001", "edit_pending", {"add_edges": [{"from": "c", "to": "b"}]}
        )
        assert _wait_for(lambda: _processed(run_dir, "ctl-edge-000001").exists())

    ex = Exec({"a": first})
    orch = Orchestrator(ex, store, rs, max_parallel=1, sentinel=sentinel())
    state = orch.run(wf, reposets, agents, run_state=state0)
    assert state.status == "succeeded"
    assert ex.order == ["a", "c", "b"]
    saved = rs.load(state.run_id)
    assert saved.graph_edits[0].op == "set_edges" and saved.graph_edits[0].depends_on == ["c"]


def test_inject_inbox_runs_sentinel_origin_task(tmp_path: Path) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    wf = WF([T("a")])
    state0 = rs.new_run(wf)
    run_dir = _run_dir(rs, state0)

    def first(ex: Exec, ctx: TaskContext) -> None:
        _write_entry(
            run_dir,
            "ctl-inj-0000001",
            "inject",
            {"tasks": [{"id": "extra", "agent": "ag", "instruction": INSTR, "depends_on": ["a"]}]},
        )
        assert _wait_for(lambda: _processed(run_dir, "ctl-inj-0000001").exists())

    ex = Exec({"a": first})
    orch = Orchestrator(ex, store, rs, max_parallel=1, sentinel=sentinel())
    state = orch.run(wf, reposets, agents, run_state=state0)
    assert state.status == "succeeded" and ex.order == ["a", "extra"]
    assert state.tasks["extra"].origin == "sentinel"
    assert state.spawned_by["extra"].parent_task_id == "ctl:cli:tester"


def test_llm_actor_blocked_when_graph_edits_disallowed(tmp_path: Path) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    wf = WF([T("a"), T("b")])
    state0 = rs.new_run(wf)
    run_dir = _run_dir(rs, state0)

    def first(ex: Exec, ctx: TaskContext) -> None:
        _write_entry(
            run_dir,
            "sentinel-g01-prop1",
            "edit_pending",
            {"priority": {"b": 1}},
            actor="sentinel-g01",
        )
        assert _wait_for(
            lambda: (run_dir / "control" / "inbox-rejected" / "sentinel-g01-prop1.json").exists()
        )

    from agent_orchestrator.models import SentinelLlmSpec

    st = resolve_sentinel_settings(
        workflow=SentinelSpec(
            enabled=True, tick_seconds=1, llm=SentinelLlmSpec(allow_graph_edits=False)
        )
    )
    ex = Exec({"a": first})
    state = Orchestrator(ex, store, rs, max_parallel=1, sentinel=st).run(
        wf, reposets, agents, run_state=state0
    )
    assert state.status == "succeeded"
    assert _rejected_rule(run_dir, "sentinel-g01-prop1") == "GE-DISABLED"


def test_resume_does_not_revert_edits(tmp_path: Path) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    wf = WF([T("a"), T("b"), T("c"), T("d")])
    state0 = rs.new_run(wf)
    run_dir = _run_dir(rs, state0)

    def first(ex: Exec, ctx: TaskContext) -> None:
        _write_entry(
            run_dir,
            "ctl-multi-00001",
            "edit_pending",
            {
                "cancel": ["d"],
                "skip": ["b"],
                "add_edges": [{"from": "c", "to": "b"}],
                "priority": {"c": 2},
            },
        )
        assert _wait_for(lambda: _processed(run_dir, "ctl-multi-00001").exists())
        raise RuntimeError("crash a")  # leave the run failed/resumable

    ex = Exec({"a": first})
    with pytest.raises(RuntimeError, match="crash a"):
        Orchestrator(ex, store, rs, max_parallel=1, sentinel=sentinel()).run(
            wf, reposets, agents, run_state=state0
        )
    loaded = rs.load(state0.run_id)
    assert {e.op for e in loaded.graph_edits} == {"set_edges", "remove_task", "skip", "priority"}

    wf2 = WF([T("a"), T("b"), T("c"), T("d")])  # fresh static spec: edits must be replayed
    loaded = rs.prepare_resume(loaded, wf2)
    assert [t.id for t in wf2.tasks] == ["a", "b", "c"]
    assert wf2.task("b").depends_on == ["c"]
    assert loaded.tasks["b"].status == "skipped" and "d" not in loaded.tasks
    ex2 = Exec()
    final = Orchestrator(ex2, store, rs, max_parallel=1, sentinel=sentinel()).run(
        wf2, reposets, agents, run_state=loaded
    )
    assert final.status == "succeeded"
    assert "d" not in ex2.order and "b" not in ex2.order and "c" in ex2.order
    assert len(final.graph_edits) == len(loaded.graph_edits)  # nothing duplicated


def test_consumed_edit_replayed_after_crash_is_not_applied_twice(tmp_path: Path) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    wf = WF([T("a"), T("b")])
    state0 = rs.new_run(wf)
    run_dir = _run_dir(rs, state0)

    def first(ex: Exec, ctx: TaskContext) -> None:
        _write_entry(run_dir, "ctl-prio-replay1", "edit_pending", {"priority": {"b": 9}})
        assert _wait_for(lambda: _processed(run_dir, "ctl-prio-replay1").exists())
        # crash window: persisted (consumed id + edit) but the file is still in the inbox
        (_processed(run_dir, "ctl-prio-replay1")).replace(
            run_dir / "control" / "inbox" / "ctl-prio-replay1.json"
        )
        threading.Event().wait(0.3)

    ex = Exec({"a": first})
    state = Orchestrator(ex, store, rs, max_parallel=1, sentinel=sentinel()).run(
        wf, reposets, agents, run_state=state0
    )
    assert state.status == "succeeded"
    assert len(state.graph_edits) == 1
    assert (
        state.sentinel is not None and state.sentinel.inbox_consumed.count("ctl-prio-replay1") == 1
    )
