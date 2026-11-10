"""T-Sn0Spk (c): per-tick cost on a synthetic 1k-task RunState. THROWAWAY.
uv run python output/E-Sn7tL2-sentinel-long-running-overseer/spike/spike_c_tickcost.py
"""

from __future__ import annotations

import json
import statistics
import tempfile
import time
from pathlib import Path

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.dag import build_dag
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import TaskRunState, TaskSpec, WorkflowSpec
from agent_orchestrator.runstate import RunStateStore

N, WIDTH = 1000, 40  # 25 layers x 40 wide; each task depends on 2 tasks of previous layer
ITERS = 1000


def timeit(fn, iters=ITERS):
    xs = []
    for _ in range(iters):
        t = time.perf_counter()
        fn()
        xs.append((time.perf_counter() - t) * 1000)
    xs.sort()
    return {
        "p50_ms": round(statistics.median(xs), 3),
        "p95_ms": round(xs[int(0.95 * len(xs)) - 1], 3),
        "max_ms": round(xs[-1], 3),
        "iters": iters,
    }


with tempfile.TemporaryDirectory(prefix="ao-spike-c-") as d:
    ws = Path(d)
    (ws / "specs" / "instructions").mkdir(parents=True)
    (ws / "specs" / "instructions" / "i.md").write_text("x")
    tasks = []
    for i in range(N):
        layer, col = divmod(i, WIDTH)
        deps = (
            []
            if layer == 0
            else [
                f"t{(layer - 1) * WIDTH + col:04d}",
                f"t{(layer - 1) * WIDTH + (col + 1) % WIDTH:04d}",
            ]
        )
        tasks.append(
            TaskSpec(
                id=f"t{i:04d}",
                agent="ag",
                instruction="specs/instructions/i.md",
                depends_on=deps,
                outputs=[f"out/t{i:04d}.txt"],
            )
        )
    wf = WorkflowSpec(version="1.0", id="spike-c", repo_set="rs", tasks=tasks)
    store = LocalFsArtifactStore(str(ws))
    rs = RunStateStore(str(ws), store)
    orch = Orchestrator(FakeExecutor(), store, rs, max_parallel=6)
    state = rs.new_run(wf)
    # halfway through the DAG: layers 0..11 succeeded, 6 of layer 12 running, rest pending
    done = set()
    for i, t in enumerate(tasks):
        st = state.tasks.setdefault(t.id, TaskRunState())
        if i < 12 * WIDTH:
            st.status = "succeeded"
            done.add(t.id)
        elif i < 12 * WIDTH + 6:
            st.status = "running"
    running = {t.id for t in tasks if state.tasks[t.id].status == "running"}

    out: dict = {"n_tasks": N, "width": WIDTH, "layers": N // WIDTH, "iters": ITERS}
    graph = build_dag(wf)
    order = graph.topological_order()
    preds = orch._predecessors(graph)
    out["build_dag"] = timeit(lambda: build_dag(wf), 200)
    out["topological_order"] = timeit(graph.topological_order, 200)
    out["_predecessors"] = timeit(lambda: orch._predecessors(graph), 200)
    out["_recompute_order"] = timeit(lambda: orch._recompute_order(graph, done), 200)
    out["_ready_ids"] = timeit(lambda: orch._ready_ids(order, preds, state, done, running))
    out["_pending_ids"] = timeit(lambda: orch._pending_ids(state, order))
    out["tick_core(_ready_ids+_pending_ids)"] = timeit(
        lambda: (
            orch._ready_ids(order, preds, state, done, running),
            orch._pending_ids(state, order),
        )
    )
    out["full_reshape(build_dag+topo+preds)"] = timeit(
        lambda: (lambda g: (g.topological_order(), orch._predecessors(g)))(build_dag(wf)), 200
    )
    out["RunState.save"] = timeit(lambda: rs.save(state), 200)
    out["RunState.load"] = timeit(lambda: rs.load(state.run_id), 200)
    out["state_json_bytes"] = (rs._path(state.run_id)).stat().st_size
    out["model_dump_json_only"] = timeit(lambda: state.model_dump_json(indent=2), 200)
    print(json.dumps(out, indent=1))
