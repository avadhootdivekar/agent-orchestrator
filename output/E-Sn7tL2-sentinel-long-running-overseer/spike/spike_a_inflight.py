"""T-Sn0Spk (a): in-flight DAG rebuild safety. THROWAWAY spike -- not production code.

Run (from the product clone, temp workspace is created under tempfile):
    uv run python output/E-Sn7tL2-sentinel-long-running-overseer/spike/spike_a_inflight.py

Emulates the proposed "soft barrier" tick: a subclass overrides `_ready_ids` (called on the MAIN
thread once per scheduler iteration) and, once >=2 tasks are still in flight after the first completion (3 were dispatched), applies graph edits and
swaps the engine's run()-local `graph`/`order`/`preds` IN PLACE (run() cannot be reassigned from
a hook; the real fix would be a `_apply_pending_edits()` call site in run()).
"""

from __future__ import annotations

import copy
import json
import tempfile
import threading
import time
from pathlib import Path

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.dag import CycleError, build_dag
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors.base import Executor
from agent_orchestrator.models import (
    AgentSpec,
    RepoRef,
    RepoSet,
    TaskResult,
    TaskSpec,
    WorkflowSpec,
)
from agent_orchestrator.runstate import RunStateStore

SLOW = 1.2  # seconds a "slow" task occupies a slot


class SlowExecutor(Executor):
    """Records every dispatch (start/end wall times) and sleeps; thread-safe."""

    def __init__(self) -> None:
        self.calls: dict[str, list[tuple[float, float]]] = {}
        self._lock = threading.Lock()

    def execute(self, ctx) -> TaskResult:  # type: ignore[override]
        t0 = time.monotonic()
        time.sleep(
            SLOW * {"1": 0.3, "2": 1.0, "3": 1.6}.get(ctx.task_id[-1], 1.0)
            if ctx.task_id.startswith("slow")
            else 0.05
        )
        for p in ctx.output_paths:
            Path(p).parent.mkdir(parents=True, exist_ok=True)
            Path(p).write_text("x")
        t1 = time.monotonic()
        with self._lock:
            self.calls.setdefault(ctx.task_id, []).append((t0, t1))
        return TaskResult(task_id=ctx.task_id, status="succeeded", attempts=1, exit_code=0)


def mk(
    ws: Path,
    tid: str,
    deps: list[str] | None = None,
    outs: list[str] | None = None,
    ins: list[str] | None = None,
    skip: bool = True,
) -> TaskSpec:
    ip = ws / "specs" / "instructions" / f"{tid}.md"
    ip.parent.mkdir(parents=True, exist_ok=True)
    ip.write_text("x")
    return TaskSpec(
        id=tid,
        agent="ag",
        instruction=f"specs/instructions/{tid}.md",
        skip_if_outputs_exist=skip,
        depends_on=deps or [],
        outputs=outs if outs is not None else [f"out/{tid}.txt"],
        inputs=ins or [],
    )


def apply_edits(wf: WorkflowSpec, state, edits: dict, graph, order: list[str], preds: dict) -> dict:
    """Validate on a deep copy, then commit in place. Returns a report of what was rejected."""
    rep: dict = {"rejected": [], "applied": []}
    cand = copy.deepcopy(wf)
    by_id = {t.id: t for t in cand.tasks}
    for t in edits.get("add_tasks", []):
        cand.tasks.append(copy.deepcopy(t))
    by_id = {t.id: t for t in cand.tasks}
    for tid, deps in edits.get("set_deps", {}).items():
        if state.tasks[tid].status != "pending":
            rep["rejected"].append((tid, f"status={state.tasks[tid].status}"))
            continue
        by_id[tid].depends_on = deps
    for tid in edits.get("remove", []):
        if state.tasks[tid].status != "pending":
            rep["rejected"].append((tid, f"status={state.tasks[tid].status}"))
            continue
        cand.tasks = [t for t in cand.tasks if t.id != tid]
    try:
        g2 = build_dag(cand)
        o2 = g2.topological_order()
    except CycleError as exc:
        rep["rejected"].append(("*", f"CycleError: {exc}"))
        return rep
    # commit: workflow in place (same list object), then graph/order/preds in place
    wf.tasks[:] = cand.tasks
    graph.__init__(g2.adjacency(), wf.tasks, output_to_task=g2.output_to_task())  # type: ignore[misc]
    order[:] = o2
    preds.clear()
    p2: dict[str, set[str]] = {n: set() for n in g2.adjacency()}
    for n, succ in g2.adjacency().items():
        for s in succ:
            p2[s].add(n)
    preds.update(p2)
    for t in edits.get("add_tasks", []):
        from agent_orchestrator.models import TaskRunState

        state.tasks[t.id] = TaskRunState()
        state.injected_tasks.append(t)
    for tid in edits.get("remove", []):
        if tid not in {t.id for t in wf.tasks}:  # actually removed
            state.tasks.pop(tid, None)
    rep["applied"].append(sorted(edits))
    return rep


class HookedOrch(Orchestrator):
    def __init__(self, *a, edit_plan=None, trigger_inflight=2, **k):
        super().__init__(*a, **k)
        self._plan = edit_plan
        self._trigger = trigger_inflight
        self.report: dict | None = None
        self.hook_order_before: list[str] | None = None
        self.tick_state_snapshot: dict | None = None
        self._wf = None
        self._state = None

    def run(self, workflow, reposets, agents, run_state=None):
        self._wf = workflow
        return super().run(workflow, reposets, agents, run_state)

    def _ready_ids(self, order, preds, state, done, in_flight_ids):
        if self.report is None and len(in_flight_ids) >= self._trigger:
            self._state = state
            self.hook_order_before = list(order)
            self.tick_state_snapshot = {t: s.status for t, s in state.tasks.items()}
            g = self._graph_ref
            self.report = apply_edits(self._wf, state, self._plan(state), g, order, preds)
        return super()._ready_ids(order, preds, state, done, in_flight_ids)

    def _prepare_and_maybe_dispatch(self, tid, workflow, graph, *a, **k):
        self._graph_ref = graph  # capture run()'s live Graph object for in-place swap
        return super()._prepare_and_maybe_dispatch(tid, workflow, graph, *a, **k)


def scenario(name: str, build_tasks, plan, max_parallel=6):
    with tempfile.TemporaryDirectory(prefix="ao-spike-a-") as d:
        ws = Path(d)
        store = LocalFsArtifactStore(str(ws))
        rs = RunStateStore(str(ws), store)
        ex = SlowExecutor()
        tasks = build_tasks(ws)
        wf = WorkflowSpec(version="1.0", id=f"spike-{name}", repo_set="rs", tasks=tasks)
        reposets = {
            "rs": RepoSet(
                workspace_root=str(ws), repos=[RepoRef(id="core", path=".", role="primary")]
            )
        }
        agents = {"ag": AgentSpec(executor="fake")}
        orch = HookedOrch(
            ex, store, rs, max_parallel=max_parallel, edit_plan=lambda st: plan(ws, st)
        )
        # `_graph_ref` is only set after the first dispatch; hook triggers only at >=3 in flight, so it is set
        t0 = time.monotonic()
        state = orch.run(wf, reposets, agents)
        wall = time.monotonic() - t0
        out = {
            "scenario": name,
            "status": state.status,
            "wall_s": round(wall, 2),
            "hook_report": orch.report,
            "dispatch_counts": {t: len(v) for t, v in ex.calls.items()},
            "double_dispatch": [t for t, v in ex.calls.items() if len(v) > 1],
            "task_status": {t: s.status for t, s in state.tasks.items()},
            "start_order": [
                t
                for t, _ in sorted(((t, v[0][0]) for t, v in ex.calls.items()), key=lambda x: x[1])
            ],
        }
        # ordering checks: for each task, every dependency in the FINAL workflow finished before it started
        viol = []
        for t in wf.tasks:
            for dep in t.depends_on:
                if (
                    dep in ex.calls
                    and t.id in ex.calls
                    and ex.calls[t.id][0][0] < ex.calls[dep][0][1] - 1e-3
                ):
                    viol.append(f"{t.id} started before dep {dep} finished")
        # FULL-graph check (declared + INFERRED edges): an edge a->b where b started before a finished
        g_final = build_dag(wf)
        for a, succ in g_final.adjacency().items():
            for b in succ:
                if a in ex.calls and b in ex.calls and ex.calls[b][0][0] < ex.calls[a][0][1] - 1e-3:
                    viol.append(f"GRAPH EDGE {a}->{b}: {b} started before {a} finished")
        out["order_violations"] = viol
        out["producer_of_out_p1"] = g_final.producer_of("out/p1.txt")
        # resume equality: reload state, rebuild from ORIGINAL static spec + prepare_resume
        loaded = rs.load(state.run_id)
        static = WorkflowSpec(
            version="1.0", id=f"spike-{name}", repo_set="rs", tasks=build_tasks(ws)
        )
        rs.prepare_resume(loaded, static)
        live_edges = sorted((a, b) for a, s in build_dag(wf).adjacency().items() for b in s)
        resumed_edges = sorted((a, b) for a, s in build_dag(static).adjacency().items() for b in s)
        out["resume_graph_equal"] = live_edges == resumed_edges
        if live_edges != resumed_edges:
            out["resume_diff"] = {
                "only_live": [e for e in live_edges if e not in resumed_edges],
                "only_resumed": [e for e in resumed_edges if e not in live_edges],
            }
        # determinism: topo order recomputed twice equal
        out["order_deterministic"] = (
            build_dag(wf).topological_order() == build_dag(wf).topological_order()
        )
        return out, ex


def base(ws):
    # 3 slow running roots + pending chain p1 -> p2 -> p3, plus pending q (dep on slow1)
    return [
        mk(ws, "slow1"),
        mk(ws, "slow2"),
        mk(ws, "slow3"),
        mk(ws, "p1", ["slow1"]),
        mk(ws, "p2", ["p1"]),
        mk(ws, "p3", ["p2"]),
        mk(ws, "q", ["slow2"]),
    ]


RESULTS = []


# S1: additive tasks (2 new: n1 dep slow3, n2 dep n1) + add edge q -> p3 (between pending tasks)
def s1(ws, st):
    return {
        "add_tasks": [mk(ws, "n1", ["slow3"]), mk(ws, "n2", ["n1"])],
        "set_deps": {"p3": ["p2", "q"]},
    }


RESULTS.append(scenario("additive-and-edge-add", base, s1)[0])


# S2: remove edge between pending tasks (p2 no longer waits for p1) + remove pending task q
def s2(ws, st):
    return {"set_deps": {"p2": []}, "remove": ["q"]}


RESULTS.append(scenario("edge-remove-and-task-remove", base, s2)[0])


# S3: cycle-introducing edit must be rejected, graph untouched
def s3(ws, st):
    return {"set_deps": {"p1": ["p3"]}}


RESULTS.append(scenario("cycle-rejected", base, s3)[0])


# S4: edits targeting NON-pending (running) tasks must be rejected by the validator
def s4(ws, st):
    return {"set_deps": {"slow2": ["p3"]}, "remove": ["slow3"]}


RESULTS.append(scenario("edit-running-rejected", base, s4)[0])


# S5: injected task whose OUTPUT matches the INPUT of a running/pending task -> hidden inferred edge
def base_io(ws):
    (ws / "out").mkdir(exist_ok=True)
    (ws / "out" / "late.txt").write_text("pre-existing")
    return [
        mk(ws, "slow1"),
        mk(ws, "slow2"),
        mk(ws, "slow3", [], ins=["out/late.txt"]),  # RUNNING consumer of out/late.txt
    ]


def s5(ws, st):
    return {"add_tasks": [mk(ws, "late", ["slow2"], outs=["out/late.txt"], skip=False)]}


RESULTS.append(scenario("inferred-edge-from-injected-output", base_io, s5)[0])


# S6: duplicate-output injection (two producers of one path)
def s6(ws, st):
    return {"add_tasks": [mk(ws, "dup", ["slow1"], outs=["out/p1.txt"])]}


RESULTS.append(scenario("duplicate-output-producer", base, s6)[0])


# S7: thread-safety stress -- inject 300 tasks while 6 workers are running and reading workflow
def base_wide(ws):
    return [mk(ws, f"slow{i}") for i in range(1, 7)]


def s7(ws, st):
    return {"add_tasks": [mk(ws, f"bulk{i:03d}", ["slow1"]) for i in range(300)]}


r7 = scenario("bulk-inject-300-while-6-running", base_wide, s7)[0]
r7["dispatch_counts"] = {
    "n_tasks": len(r7["dispatch_counts"]),
    "max_per_task": max(r7["dispatch_counts"].values()),
}
r7["task_status"] = {
    "n": len(r7["task_status"]),
    "all_succeeded": all(v == "succeeded" for v in r7["task_status"].values()),
}
r7["start_order"] = r7["start_order"][:8] + ["..."]
RESULTS.append(r7)

print(json.dumps(RESULTS, indent=1, default=str))
