"""T-Cb8Rk2: the comparison's cost/efficiency table, per-probe-group scores and rule-based
observations (fake tier: two real fake-subject runs whose run.json is then edited to carry
blackbox-style scores, costs and score breakdowns)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.bench.results import build_comparison, write_comparison
from agent_orchestrator.bench.runner import RUN_JSON_FILENAME, resolve_result_dir, run_suite


def _breakdown(core: float, trap: float, perf: float) -> dict[str, Any]:
    return {
        g: {"weight": w, "passed": int(f * 10), "total": 10, "fraction": f}
        for g, w, f in (("core", 0.5, core), ("trap", 0.35, trap), ("perf", 0.15, perf))
    }


def _run(suite: Path, tmp_path: Path, sid: str, out: Path) -> Path:
    subject = tmp_path / f"{sid}.json"
    subject.write_text(json.dumps({"version": "1.0", "id": sid, "type": "fake"}))
    return resolve_result_dir(run_suite(suite, subject, out_dir=out).bench_run_id, out_dir=out)


def _rewrite(run_dir: Path, rows: dict[str, tuple[float, float, bool, dict[str, Any]]]) -> None:
    data = json.loads((run_dir / RUN_JSON_FILENAME).read_text())
    for t in data["tasks"]:
        score, cost, solved, breakdown = rows[t["task_id"]]
        t.update(score=score, cost_usd=cost, solved=solved, output_tokens=1000)
        t["grader_detail"] = {"score_breakdown": breakdown}
    (run_dir / RUN_JSON_FILENAME).write_text(json.dumps(data))


@pytest.fixture()
def two_runs(tmp_path: Path, suite_factory: Callable[..., Path]) -> list[Path]:
    tasks = [
        {
            "id": tid,
            "category": "feature",
            "instruction": f"tasks/{tid}/instruction.md",
            "fixture": f"tasks/{tid}/fixture",
            "grader": {"type": "pytest", "command": "true", "cwd": "."},
        }
        for tid in ("t1", "t2")
    ]
    suite = suite_factory(suite_id=f"eff-{uuid.uuid4().hex[:6]}", tasks=tasks)
    out = tmp_path / "res"
    strong, weak = _run(suite, tmp_path, "strong", out), _run(suite, tmp_path, "weak", out)
    _rewrite(strong, {"t1": (1.0, 2.0, True, _breakdown(1, 1, 1)),
                      "t2": (0.9, 2.0, True, _breakdown(1, 0.8, 0.7))})  # fmt: skip
    _rewrite(weak, {"t1": (0.6, 0.5, False, _breakdown(1, 0.2, 0.9)),
                    "t2": (0.5, 0.5, False, _breakdown(0.8, 0.1, 0.9))})  # fmt: skip
    return [strong, weak]


def test_efficiency_rows(two_runs: list[Path]) -> None:
    cmp = build_comparison(two_runs)
    strong, weak = cmp.efficiency["strong"], cmp.efficiency["weak"]
    assert strong.spend_usd == pytest.approx(4.0) and strong.solved_runs == 2
    assert strong.cost_per_task_mean == pytest.approx(2.0)
    assert strong.cost_per_solved == pytest.approx(2.0)
    assert strong.cost_per_score_point == pytest.approx(4.0 / 1.9)
    assert weak.cost_per_solved is None  # nothing solved: no $/solved
    assert weak.cost_per_score_point == pytest.approx(1.0 / 1.1)
    assert weak.output_tokens_per_task_mean == pytest.approx(1000)


def test_group_scores_and_observations(two_runs: list[Path], tmp_path: Path) -> None:
    cmp = build_comparison(two_runs)
    assert cmp.group_scores["strong"]["trap"] == pytest.approx(0.9)
    assert cmp.group_scores["weak"]["trap"] == pytest.approx(0.15)
    text = " ".join(cmp.observations)
    assert "Highest mean score: strong" in text
    assert "Cheapest per score point: weak" in text
    assert "Probe group 'trap' separates subjects by 0.75" in text
    # perf spread (0.85 vs 0.9) is below the call-out threshold
    assert "'perf'" not in text
    _, md = write_comparison(cmp, tmp_path / "cmp")
    body = md.read_text()
    assert "## Cost & efficiency" in body and "### Observations (rule-based)" in body
    assert "| strong | 4.0000 | 2 | 2 | 0.950 |" in body
