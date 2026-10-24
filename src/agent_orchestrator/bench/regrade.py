"""`ao-bench regrade` (T-Os5Lad): grade a finished run's saved workspaces again with the current
grader and generator, without running any subject.

Instrument tuning (design `docs-md/benchmark-open-scale-design.md` §5) changes grader-side knobs:
rung strength, par, the time limit. Rerunning the agents to see the effect would cost money and add
agent variance. The artefacts are already on disk (the run's `repo/`), so the same artefacts are
graded again against freshly materialised grader-owned assets (``source`` -> provider ->
``grading_dir``). Subject-side fields (cost, tokens, wall time, status) are kept; only ``solved``,
``score``, ``grader_detail`` and ``grader_raw_tail`` change, and the aggregate is recomputed.

The result is a new run dir (``<bench_run_id>-regrade`` by default), never an in-place rewrite, so
the original grading stays on record. Tasks whose workspace is gone, or that were not attempted
(infra errors), keep their original record and are listed as skipped.
"""

from __future__ import annotations

import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from .metrics import TaskMetric, aggregate
from .registries import GRADER_REGISTRY, WORKSPACE_PROVIDER_REGISTRY
from .results import is_attempted, load_run
from .runner import RUN_JSON_FILENAME, BenchRunRecord, BenchTaskRecord, _persist_record
from .spec import BenchTask, load_suite

REGRADE_SUFFIX = "-regrade"


def _repo_of(rec: BenchTaskRecord) -> Path | None:
    """The artefact dir: `workspace` points at the task workspace or its `capture/` dir."""
    ws = Path(rec.workspace)
    for cand in (ws / "repo", ws.parent / "repo"):
        if cand.is_dir():
            return cand
    return None


@dataclass
class _Ctx:
    repo_dir: str
    grading_dir: str | None


def _regrade_task(task: BenchTask, rec: BenchTaskRecord, suite_dir: Path) -> BenchTaskRecord:
    repo = _repo_of(rec)
    assert repo is not None  # filtered by the caller
    if task.source is None:
        raise ValueError(f"task {task.id}: no source block to materialise grader assets from")
    provider = WORKSPACE_PROVIDER_REGISTRY[task.source.type]()
    with tempfile.TemporaryDirectory(prefix="ao-bench-regrade-") as tmp:
        grading = Path(tmp) / "grading"
        provider.prepare(task, Path(tmp) / "scratch", suite_base_dir=suite_dir, grading_dir=grading)
        gr = GRADER_REGISTRY[task.grader.type]().grade(task.grader, _Ctx(str(repo), str(grading)))
    return rec.model_copy(
        update={
            "solved": gr.solved,
            "score": gr.score,
            "grader_detail": {**gr.detail, "regraded": True},
            "grader_raw_tail": gr.raw_tail,
        }
    )


def regrade_run(
    run_dir: str | Path,
    suite_path: str | Path,
    out_dir: str | Path | None = None,
    max_parallel: int = 3,
) -> tuple[Path, list[str]]:
    """-> (new run dir, ids of tasks kept unchanged)."""
    run = load_run(run_dir)
    suite = load_suite(Path(suite_path))
    suite_dir = Path(suite_path).resolve().parent
    tasks = {t.id: t for t in suite.tasks}
    todo = [
        r for r in run.tasks if r.task_id in tasks and is_attempted(r) and _repo_of(r) is not None
    ]
    skipped = [r.task_id for r in run.tasks if r not in todo]
    with ThreadPoolExecutor(max_workers=max(1, max_parallel)) as pool:
        done = dict(
            zip(
                [r.task_id for r in todo],
                pool.map(lambda r: _regrade_task(tasks[r.task_id], r, suite_dir), todo),
                strict=True,
            )
        )
    new_tasks = [done.get(r.task_id, r) for r in run.tasks]
    new_id = run.bench_run_id + REGRADE_SUFFIX
    record: BenchRunRecord = run.model_copy(
        update={
            "bench_run_id": new_id,
            "tasks": new_tasks,
            "aggregate": aggregate(cast("list[TaskMetric]", new_tasks)),
        }
    )
    out = Path(out_dir) if out_dir else Path(run_dir).resolve().parent / new_id
    _persist_record(out / RUN_JSON_FILENAME, record)
    return out, skipped


__all__ = ["REGRADE_SUFFIX", "regrade_run"]
