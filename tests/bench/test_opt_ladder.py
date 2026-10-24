"""`opt-ladder` family + `objective` grader (T-Os5Lad): ladder maths, feasibility checker,
deterministic generation with strict ladders, and grading end to end in the process sandbox."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.bench.errors import GraderError
from agent_orchestrator.bench.gen.base import Difficulty
from agent_orchestrator.bench.gen.materialize import generate_instance, write_instance
from agent_orchestrator.bench.gen.opt_ladder import SIZES, reference, strict_ladder
from agent_orchestrator.bench.objective import (
    OBJECTIVE_GRADER_TYPE,
    ObjectiveGrader,
    validate_cases,
)
from agent_orchestrator.bench.spec import GraderConfig

SMALL = Difficulty(entities=2, traps=1, concurrency=0, scale=0)  # 3 problems x 1 case, smallest


@dataclass
class Ctx:
    repo_dir: str
    grading_dir: str | None


def _cfg(**extra: Any) -> GraderConfig:
    extra.setdefault("sandbox", {"type": "process"})
    return GraderConfig(type=OBJECTIVE_GRADER_TYPE, **extra)


# -- ladder maths -----------------------------------------------------------------------------


def test_ladder_points_interpolates_and_extrapolates() -> None:
    pts = reference().ladder_points
    ladder = [100.0, 50.0, 40.0, 30.0, 25.0]
    assert pts(150.0, ladder) == 0.0
    assert pts(100.0, ladder) == 0.0
    assert pts(75.0, ladder) == pytest.approx(0.5)
    assert pts(50.0, ladder) == pytest.approx(1.0)
    assert pts(35.0, ladder) == pytest.approx(2.5)
    assert pts(25.0, ladder) == pytest.approx(4.0)
    # Beyond the strongest rung the last gap (5) keeps extrapolating: no upper limit.
    assert pts(20.0, ladder) == pytest.approx(5.0)
    assert pts(10.0, ladder) == pytest.approx(7.0)


def test_strict_ladder() -> None:
    assert strict_ladder([10.0, 9.0, 8.0])
    assert not strict_ladder([10.0, 10.0, 8.0])
    assert not strict_ladder([10.0, 9.0, 9.5])


# -- feasibility checker ----------------------------------------------------------------------


def test_tour_evaluate() -> None:
    ref = reference()
    case = {"points": [[0, 0], [3, 0], [3, 4], [0, 4]]}
    assert ref.evaluate("tour", case, [0, 1, 2, 3]) == pytest.approx(14.0)
    assert ref.evaluate("tour", case, [0, 2, 1, 3]) == pytest.approx(5 + 4 + 5 + 4)
    for bad in ([0, 1, 2], [0, 1, 1, 3], [0, 1, 2, 4], [0, 1, 2, True], "0123", None):
        with pytest.raises(ref.OpError):
            ref.evaluate("tour", case, bad)


def test_facility_evaluate_enforces_capacity() -> None:
    ref = reference()
    case = {"sites": [[0, 0, 10, 5], [10, 0, 20, 100]], "customers": [[0, 0, 3], [0, 0, 3]]}
    with pytest.raises(ref.OpError, match="capacity"):
        ref.evaluate("facility", case, [0, 0])  # demand 6 > capacity 5
    # site 0 (open 10, distance 0) + site 1 (open 20, 3 x distance 10)
    assert ref.evaluate("facility", case, [0, 1]) == pytest.approx(10 + 20 + 30)
    with pytest.raises(ref.OpError):
        ref.evaluate("facility", case, [0, 2])


def test_partition_evaluate_enforces_balance() -> None:
    ref = reference()
    case = {"n": 4, "k": 2, "edges": [[0, 1, 5], [1, 2, 1], [2, 3, 5]]}
    assert ref.evaluate("partition", case, [0, 0, 1, 1]) == 1
    assert ref.evaluate("partition", case, [0, 1, 0, 1]) == 11
    with pytest.raises(ref.OpError, match="differ by at most 1"):
        ref.evaluate("partition", case, [0, 0, 0, 1])
    with pytest.raises(ref.OpError):
        ref.evaluate("partition", case, [0, 0, 1, 2])
    with pytest.raises(ref.OpError):
        ref.evaluate("nope", case, [0, 0, 1, 1])


# -- generation -------------------------------------------------------------------------------


def test_generation_is_deterministic_with_strict_ladders() -> None:
    a = generate_instance("opt-ladder", 7, SMALL, "python")
    b = generate_instance("opt-ladder", 7, SMALL, "python")
    assert a.probes == b.probes and a.spec_md == b.spec_md
    cases = validate_cases(a.probes)
    assert [c["problem"] for c in cases] == ["tour", "facility", "partition"]
    for c in cases:
        assert strict_ladder(c["ladder"]) and len(c["ladder"]) == len(reference().RUNGS)
        # The rung answers are feasible and reproduce the stored values.
        answers = reference().PROBLEMS[c["problem"]][0](c["case"])
        assert reference().evaluate(c["problem"], c["case"], answers[-1]) == c["ladder"][-1]
    assert len(cases[0]["case"]["points"]) == SIZES["tour"][0]
    assert len(cases[1]["case"]["customers"]) == SIZES["facility"][0]
    # Hidden cases never reach the agent-visible spec.
    assert json.dumps(cases[0]["case"]["points"][:3])[1:-1] not in a.spec_md
    assert "capacity" in a.spec_md and "10 seconds" in a.spec_md
    assert generate_instance("opt-ladder", 8, SMALL, "python").probes != a.probes


def test_suite_uses_objective_grader_and_optimize_instruction() -> None:
    from agent_orchestrator.bench.gen.cli import build_suite

    suite, instructions = build_suite(
        suite_id="t",
        families=["opt-ladder"],
        count=1,
        languages=["python"],
        base_seed=3,
        difficulty=SMALL,
        tier="medium",
        parity="../../parity/default",
        command="test",
    )
    task = suite["tasks"][0]
    assert task["grader"]["type"] == "objective"
    assert task["instruction"].endswith("instruction-optimize-jsonl.md")
    assert "scored by" in next(iter(instructions.values()))


def test_validate_cases_rejects_malformed() -> None:
    good = {"id": "a", "problem": "tour", "case": {}, "sense": "min", "ladder": [3.0, 2.0]}
    validate_cases({"kind": "objective", "cases": [good]})
    for bad in (
        {"kind": "blackbox", "cases": [good]},
        {"kind": "objective", "cases": []},
        {"kind": "objective", "cases": [{**good, "ladder": [2.0, 2.0]}]},
        {"kind": "objective", "cases": [{**good, "sense": "max"}]},
        {"kind": "objective", "cases": [{**good, "case": []}]},
    ):
        with pytest.raises(GraderError):
            validate_cases(bad)


# -- grading end to end (process sandbox) -----------------------------------------------------

STUB_APP = """\
import json, sys, time
sys.path.insert(0, "ref")
import opt_server as ref

# Rungs 0-2 do not depend on the metaheuristic budgets: make rungs 3-4 instant.
ref.TOUR_ILS_ROUNDS = ref.FACILITY_SA_ITERS = ref.PARTITION_SA_ITERS = (0, 0)
RUNG = int(sys.argv[1])
SLOW = sys.argv[2:]  # problems to stall on
for raw in sys.stdin:
    req = json.loads(raw)
    if req["op"] == "ping":
        out = {"ok": True}
    elif req["problem"] in SLOW:
        time.sleep(5)
        out = {"ok": True, "answer": []}
    elif RUNG < 0:
        out = {"ok": True, "answer": [0]}
    else:
        out = {"ok": True, "answer": ref.PROBLEMS[req["problem"]][0](req["case"])[RUNG]}
    print(json.dumps(out), flush=True)
"""


def _staged(tmp_path: Path) -> tuple[Path, Path]:
    instance = generate_instance("opt-ladder", 11, SMALL, "python")
    repo, grading = tmp_path / "repo", tmp_path / "grading"
    write_instance(instance, repo, grading)
    return repo, grading


def _stub(repo: Path, grading: Path, *args: str) -> None:
    (repo / "ref").mkdir()
    shutil.copy(grading / "reference" / "server.py", repo / "ref" / "opt_server.py")
    (repo / "stub.py").write_text(STUB_APP)
    (repo / "run.sh").write_text(f"#!/bin/sh\nexec python3 stub.py {' '.join(args)}\n")
    (repo / "run.sh").chmod(0o755)


def test_reference_scores_top_rung(tmp_path: Path) -> None:
    repo, grading = _staged(tmp_path)
    shutil.rmtree(repo)
    shutil.copytree(grading / "reference", repo)
    result = ObjectiveGrader().grade(_cfg(), Ctx(str(repo), str(grading)))
    assert result.solved and result.score == 1.0
    assert result.detail["points"] == pytest.approx(4.0)
    assert set(result.detail["points_by_problem"]) == {"tour", "facility", "partition"}


def test_greedy_artefact_scores_rung_one(tmp_path: Path) -> None:
    repo, grading = _staged(tmp_path)
    _stub(repo, grading, "1")
    result = ObjectiveGrader().grade(_cfg(), Ctx(str(repo), str(grading)))
    assert result.detail["points"] == pytest.approx(1.0)
    assert result.score == pytest.approx(0.25) and not result.solved
    # par is a knob: the same points normalise differently.
    result = ObjectiveGrader().grade(_cfg(par=2), Ctx(str(repo), str(grading)))
    assert result.score == pytest.approx(0.5)


def test_infeasible_and_late_answers_score_zero_and_grading_continues(tmp_path: Path) -> None:
    repo, grading = _staged(tmp_path)
    _stub(repo, grading, "-1")
    result = ObjectiveGrader().grade(_cfg(), Ctx(str(repo), str(grading)))
    assert result.detail["points"] == 0.0
    assert all(c["reason"].startswith("infeasible") for c in result.detail["cases"])

    repo2, grading2 = tmp_path / "r2", tmp_path / "g2"
    write_instance(generate_instance("opt-ladder", 11, SMALL, "python"), repo2, grading2)
    _stub(repo2, grading2, "2", "tour")
    result = ObjectiveGrader().grade(_cfg(case_seconds=0.5), Ctx(str(repo2), str(grading2)))
    by_id = {c["id"].split("-")[0]: c for c in result.detail["cases"]}
    assert by_id["tour"]["points"] == 0.0 and "no answer within" in by_id["tour"]["reason"]
    # The artefact was restarted after the timeout and still answered the later cases.
    assert by_id["facility"]["points"] == pytest.approx(2.0)
    assert by_id["partition"]["points"] == pytest.approx(2.0)


def test_missing_run_sh_scores_zero(tmp_path: Path) -> None:
    repo, grading = _staged(tmp_path)
    result = ObjectiveGrader().grade(_cfg(), Ctx(str(repo / "nowhere"), str(grading)))
    assert result.score == 0.0 and "run.sh" in result.detail["artefact_error"]


def test_needs_grading_dir(tmp_path: Path) -> None:
    with pytest.raises(GraderError):
        ObjectiveGrader().grade(_cfg(), Ctx(str(tmp_path), None))


# -- through the runner, then `ao-bench regrade` (CLI) ----------------------------------------


def test_run_then_regrade_with_a_new_par(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from agent_orchestrator.bench.cli import app
    from agent_orchestrator.bench.gen.cli import build_suite
    from agent_orchestrator.bench.runner import run_suite
    from agent_orchestrator.bench.workspace import bench_workspace_root

    suite, instructions = build_suite(
        suite_id="opt-mini", families=["opt-ladder"], count=1, languages=["python"],
        base_seed=5, difficulty=SMALL, tier="small", parity="unused", command="test",
    )  # fmt: skip
    for rel, text in instructions.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    suite["tasks"][0]["grader"]["sandbox"] = {"type": "process"}
    del suite["parity"]  # no parity bundle: its toolchains would be downloaded
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(json.dumps(suite))
    subject = tmp_path / "subject.json"
    subject.write_text(json.dumps({"version": "1.0", "id": "fake-copy-reference", "type": "fake",
                                   "scripted_effect": "copy-reference"}))  # fmt: skip
    record = run_suite(suite_path, subject, out_dir=tmp_path / "res")
    run_root = bench_workspace_root() / record.bench_run_id
    try:
        assert record.tasks[0].score == 1.0
        assert record.tasks[0].grader_detail["points"] == pytest.approx(4.0)
        # The instrument changes (par 4 -> 8); the same saved artefact is graded again.
        suite["tasks"][0]["grader"]["par"] = 8
        suite_path.write_text(json.dumps(suite))
        run_dir = tmp_path / "res" / record.bench_run_id
        result = CliRunner().invoke(app, ["regrade", "--run", str(run_dir), "--suite",
                                          str(suite_path)])  # fmt: skip
        assert result.exit_code == 0, result.output
        regraded = json.loads((tmp_path / "res" / f"{record.bench_run_id}-regrade" / "run.json")
                              .read_text())["tasks"][0]  # fmt: skip
        assert regraded["score"] == pytest.approx(0.5)
        assert regraded["grader_detail"]["regraded"] is True
        assert regraded["cost_usd"] == record.tasks[0].cost_usd
    finally:
        shutil.rmtree(run_root, ignore_errors=True)
