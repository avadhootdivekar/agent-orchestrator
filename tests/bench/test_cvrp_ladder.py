"""`cvrp-ladder` family (T-Ga4Rna): feasibility checker, deterministic strict ladder, spec hygiene,
and grading with the `objective` grader in the process sandbox (reference as artefact)."""

from __future__ import annotations

import json
import random
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent_orchestrator.bench.errors import GraderError
from agent_orchestrator.bench.gen import cvrp_ladder as cv
from agent_orchestrator.bench.gen.base import FAMILY_REGISTRY, Difficulty
from agent_orchestrator.bench.gen.materialize import generate_instance, write_instance
from agent_orchestrator.bench.gen.opt_ladder import strict_ladder
from agent_orchestrator.bench.gen.traps import traps_for_family
from agent_orchestrator.bench.objective import OBJECTIVE_GRADER_TYPE, ObjectiveGrader
from agent_orchestrator.bench.spec import GraderConfig

SMALL = Difficulty(entities=0, traps=1, concurrency=0, scale=0)  # 1 case, 100 customers


@dataclass
class Ctx:
    repo_dir: str
    grading_dir: str | None


@pytest.fixture(autouse=True)
def fast_top_rung(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cv.reference(), "STRONG_ITERS_PER_CUSTOMER", 20)


def _case(n: int = 40, seed: int = 1, traps: int = 0) -> dict:
    return cv.gen_case(random.Random(seed), n, traps)


def test_evaluate_accepts_feasible_and_rejects_the_rest() -> None:
    ref = cv.reference()
    case = {"depot": [0, 0], "capacity": 10, "customers": [[3, 4, 5], [6, 8, 5], [0, 1, 6]]}
    assert ref.evaluate("cvrp", case, [[0, 1], [2]]) == pytest.approx(20.0 + 2.0)
    for bad in (
        [[0, 1, 2]],
        [[0], [1]],
        [[0, 1], [1, 2]],
        [[0, 1], [2], [5]],
        "x",
        [[0.5], [1], [2]],
    ):
        with pytest.raises(ref.OpError):
            ref.evaluate("cvrp", case, bad)


def test_ladder_is_feasible_strict_and_deterministic() -> None:
    ref = cv.reference()
    case = _case(60, traps=1)
    values = ref.ladder_values("cvrp", case)
    assert len(values) == len(ref.RUNGS) and strict_ladder(values)
    assert ref.ladder_values("cvrp", case) == values
    for routes in ref.ladder(case):
        assert ref.evaluate("cvrp", case, ref.to_answer(routes)) > 0


def test_ladder_points_have_no_upper_limit() -> None:
    pts = cv.reference().ladder_points
    lad = [100.0, 50.0, 40.0, 30.0, 25.0]
    assert pts(25.0, lad) == pytest.approx(4.0) and pts(10.0, lad) > 6.0


def test_registered_with_traps() -> None:
    assert "cvrp-ladder" in FAMILY_REGISTRY
    assert {"vehicle_capacity", "cvrp_degenerate"} <= set(traps_for_family("cvrp-ladder"))


def test_instance_hides_cases_and_states_the_problem(tmp_path: Path) -> None:
    inst = generate_instance("cvrp-ladder", 7, SMALL, "python")
    assert inst.probes["kind"] == "objective" and len(inst.probes["cases"]) == 1
    case = inst.probes["cases"][0]
    assert strict_ladder(case["ladder"]) and len(case["case"]["customers"]) == 100
    spec = inst.spec_md
    assert "capacity" in spec.lower() and "100 customers" in spec
    assert json.dumps(case["case"]["customers"][0]) not in spec
    assert "ladder" in spec.lower() and "reference" in spec.lower()  # scoring is described
    assert "probe" not in spec.lower() and "hidden" in spec.lower()
    again = generate_instance("cvrp-ladder", 7, SMALL, "python")
    assert again.probes == inst.probes  # deterministic


def test_reference_scores_the_top_rung_and_zero_for_junk(tmp_path: Path) -> None:
    inst = generate_instance("cvrp-ladder", 7, SMALL, "python")
    repo, grading = tmp_path / "repo", tmp_path / "grading"
    write_instance(inst, repo, grading)
    shutil.copytree(grading / "reference", repo, dirs_exist_ok=True)
    cfg = GraderConfig(type=OBJECTIVE_GRADER_TYPE, sandbox={"type": "process"}, case_seconds=60)
    res = ObjectiveGrader().grade(cfg, Ctx(str(repo), str(grading)))
    # The artefact runs the full-budget top rung, the ladder here a short one: at least par.
    assert res.detail["points"] >= 4.0 and res.score == 1.0 and res.solved

    (repo / "server.py").write_text(
        "import json,sys\nfor l in sys.stdin:\n"
        " print(json.dumps({'ok':True,'answer':[[0]]}),flush=True)\n"
    )
    bad = ObjectiveGrader().grade(cfg, Ctx(str(repo), str(grading)))
    assert bad.score == 0.0 and "customer" in bad.detail["cases"][0]["reason"]


def test_missing_grading_dir_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(GraderError):
        ObjectiveGrader().grade(GraderConfig(type=OBJECTIVE_GRADER_TYPE), Ctx(str(tmp_path), None))
