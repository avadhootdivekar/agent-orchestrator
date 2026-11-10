"""T-Sn0Spk (a, addendum): does dropping a `depends_on` entry drop the edge when `inputs` still names the producer's output?
(models the FR-10 early-checkpoint edit: ck depends_on trimmed, but ck.inputs lists every unit breadcrumb)
    uv run python output/E-Sn7tL2-sentinel-long-running-overseer/spike/spike_a2_inferred_edge.py"""

import json

from agent_orchestrator.dag import build_dag
from agent_orchestrator.models import TaskSpec, WorkflowSpec


def T(i, deps=(), ins=(), outs=()):
    return TaskSpec(
        id=i,
        agent="ag",
        instruction="i.md",
        depends_on=list(deps),
        inputs=list(ins),
        outputs=list(outs),
    )


mk = lambda ck: WorkflowSpec(
    version="1.0",
    id="w",
    repo_set="rs",
    tasks=[T("u1", outs=["progress/u1.json"]), T("u2", outs=["progress/u2.json"]), ck],
)
before = mk(T("ck", ["u1", "u2"], ["progress/u1.json", "progress/u2.json"]))
trim_deps_only = mk(T("ck", ["u1"], ["progress/u1.json", "progress/u2.json"]))
trim_both = mk(T("ck", ["u1"], ["progress/u1.json"]))
edges = lambda w: sorted((a, b) for a, s in build_dag(w).adjacency().items() for b in s)
print(
    json.dumps(
        {
            "before": edges(before),
            "trim_depends_on_only": edges(trim_deps_only),
            "trim_depends_on_and_inputs": edges(trim_both),
        }
    )
)
