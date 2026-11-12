"""benchmarks/harbor/compare.py: multi-job arms, the owner's time rule, paired statistics."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPARE = REPO_ROOT / "benchmarks" / "harbor" / "compare.py"
T0 = datetime(2026, 11, 12, tzinfo=UTC)


def _load_compare():
    spec = importlib.util.spec_from_file_location("harbor_compare", COMPARE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["harbor_compare"] = module
    spec.loader.exec_module(module)
    return module


compare = _load_compare()


def _trial(jobs: Path, job: str, n: int, task: str, reward: float | None, minutes: float,
           cost: float = 1.0, exception: str | None = None) -> None:  # fmt: skip
    trial = jobs / job / f"{task}__{n}"
    trial.mkdir(parents=True)
    end = T0 + timedelta(minutes=minutes)
    doc = {
        "task_name": f"org/{task}",
        "agent_info": {"name": "x", "model_info": {"name": "m"}},
        "agent_result": {"cost_usd": cost},
        "verifier_result": None if reward is None else {"rewards": {"reward": reward}},
        "exception_info": {"exception_type": exception} if exception else None,
        "agent_execution": {"started_at": T0.isoformat(), "finished_at": end.isoformat()},
    }
    (trial / "result.json").write_text(json.dumps(doc))


def test_time_limit_is_an_hour_for_short_tasks_and_2x_for_long_ones() -> None:
    assert compare.time_limit(10, 60, 2) == 70
    assert compare.time_limit(100, 60, 2) == 200


def test_time_rule_turns_slow_solves_into_failures(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    # short task: plain 10 min -> ao limit 70 min
    _trial(jobs, "cc-short", 1, "short", 1, 10)
    _trial(jobs, "cc-short", 2, "short", 0, 10)
    _trial(jobs, "ao-short", 1, "short", 1, 65)  # within the hour
    _trial(jobs, "ao-short", 2, "short", 1, 80)  # time-fail
    # long task: plain 100 min -> ao limit 200 min
    _trial(jobs, "cc-long", 1, "long", 1, 100)
    _trial(jobs, "ao-long", 1, "long", 1, 190)
    _trial(jobs, "ao-long", 2, "long", 1, 210)  # time-fail
    _trial(jobs, "ao-long", 3, "long", None, 5, exception="DockerError")  # infra
    a = compare.load_arm("plain", str(jobs / "cc-*"))
    b = compare.load_arm("ao", str(jobs / "ao-*"))
    report = compare.compare(a, b, {"short": "grading"}, 60, 2)

    plain, ao = report["arms"]
    assert (ao["attempts"], ao["solved_raw"], ao["time_fails"], ao["solved"]) == (4, 4, 2, 2)
    assert ao["infra_excluded"] == 1
    assert (plain["solved"], plain["time_fails"]) == (2, 0)
    rows = {r["task"]: r for r in report["per_task"]}
    assert rows["short"]["ao"]["solved"] == 1 and rows["short"]["set"] == "grading"
    # per-task rates: short plain 0.5 vs ao 0.5 (tie); long plain 1.0 vs ao 0.5 (plain better)
    assert report["paired"]["all"]["plain_better"] == 1
    assert report["paired"]["all"]["ties"] == 1
    assert report["paired"]["grading"]["tasks"] == 1


def test_sign_test_and_wilson() -> None:
    assert compare.sign_test_p(0, 0) is None
    assert compare.sign_test_p(8, 1) == pytest.approx(0.0391, abs=1e-4)
    assert compare.sign_test_p(3, 3) == 1.0
    assert compare.wilson(0, 0) is None
    low, high = compare.wilson(5, 10)
    assert low < 0.5 < high


def test_cli_markdown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    jobs = tmp_path / "jobs"
    _trial(jobs, "cc-t", 1, "t", 0, 5)
    _trial(jobs, "ao-t", 1, "t", 1, 30)
    sets = tmp_path / "sets.json"
    sets.write_text(json.dumps({"grading_set": ["t"], "regression_set": []}))
    out_json = tmp_path / "report.json"
    rc = compare.main([
        "--arm", f"plain={jobs}/cc-*", "--arm", f"ao={jobs}/ao-*",
        "--sets", str(sets), "--markdown", "--json", str(out_json),
    ])  # fmt: skip
    assert rc == 0
    text = capsys.readouterr().out
    assert "| t | grading | 0/1 (0) |" in text
    assert json.loads(out_json.read_text())["paired"]["all"]["ao_better"] == 1
