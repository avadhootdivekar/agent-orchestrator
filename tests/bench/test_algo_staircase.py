"""`algo-staircase` family + the `objective` grader's "rungs" mode (T-Os5Lad)."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.bench.errors import GraderError
from agent_orchestrator.bench.gen.algo_staircase import RUNGS, rung_sizes
from agent_orchestrator.bench.gen.base import Difficulty
from agent_orchestrator.bench.gen.materialize import generate_instance, write_instance
from agent_orchestrator.bench.objective import (
    OBJECTIVE_GRADER_TYPE,
    ObjectiveGrader,
    staircase_points,
    validate_cases,
)
from agent_orchestrator.bench.spec import GraderConfig

ONE = Difficulty(entities=0, traps=1, concurrency=0, scale=0)  # 1 problem, 1 seed/rung, 10 s


@dataclass
class Ctx:
    repo_dir: str
    grading_dir: str | None


def _cfg(**extra: Any) -> GraderConfig:
    extra.setdefault("sandbox", {"type": "process"})
    return GraderConfig(type=OBJECTIVE_GRADER_TYPE, **extra)


def test_staircase_points_counts_consecutive_cleared_rungs() -> None:
    r = [
        {"problem": "a", "rung": 0, "points": 1.0},
        {"problem": "a", "rung": 1, "points": 1.0},
        {"problem": "a", "rung": 1, "points": 0.0},  # second seed of rung 1 fails
        {"problem": "a", "rung": 2, "points": 1.0},  # does not count: rung 1 not cleared
        {"problem": "b", "rung": 0, "points": 1.0},
        {"problem": "b", "rung": 1, "points": 1.0},
        {"problem": "c", "rung": 0, "points": 0.0},
    ]
    assert staircase_points(r) == {"a": 1.0, "b": 2.0, "c": 0.0}


def test_validate_rungs_mode() -> None:
    ok = {"id": "x", "problem": "p", "case": {}, "expect": 3, "rung": 0}
    validate_cases({"kind": "objective", "aggregate": "rungs", "cases": [ok]})
    ladder = {"id": "y", "problem": "p", "case": {}, "ladder": [2.0, 1.0]}
    for bad in (
        {"kind": "objective", "aggregate": "rungs", "cases": [{**ok, "rung": -1}]},
        {"kind": "objective", "aggregate": "rungs", "cases": [ladder]},
        {"kind": "objective", "aggregate": "median", "cases": [ok]},
    ):
        with pytest.raises(GraderError):
            validate_cases(bad)


def test_generation_deterministic_and_ordered() -> None:
    a = generate_instance("algo-staircase", 3, ONE, "python")
    assert a.probes == generate_instance("algo-staircase", 3, ONE, "python").probes
    cases = a.probes["cases"]
    assert [c["rung"] for c in cases] == list(range(RUNGS))
    assert [c["case"]["gen"].get("n", 0) for c in cases if c["problem"] != "grid_paths"] in (
        [],
        rung_sizes(),
    )
    assert all(isinstance(c["expect"], int) for c in cases)
    assert "within **10 seconds**" in a.spec_md and "256,000" in a.spec_md
    assert str(cases[-1]["expect"]) not in a.spec_md
    tight = generate_instance("algo-staircase", 3, Difficulty(entities=0, scale=3), "python")
    assert "within **1 second**" in tight.spec_md


STUB = """\
import json, os, sys
sys.path.insert(0, "ref")
import algo_hard_server as ref

engine = ref.Engine(json.load(open("inst.json"))["limits"])
WRONG_FROM = int(sys.argv[1])
for raw in sys.stdin:
    req = json.loads(raw)
    resp = engine.handle(req)
    gen = req.get("case", {}).get("gen", {}) if isinstance(req.get("case"), dict) else {}
    if resp.get("ok") and "answer" in resp and gen.get("n", 0) >= WRONG_FROM:
        resp["answer"] += 1
    print(json.dumps(resp), flush=True)
"""


def _staged(tmp_path: Path) -> tuple[Path, Path]:
    inst = generate_instance("algo-staircase", 9, ONE, "python")
    repo, grading = tmp_path / "repo", tmp_path / "grading"
    write_instance(inst, repo, grading)
    return repo, grading


def test_reference_clears_every_rung(tmp_path: Path) -> None:
    repo, grading = _staged(tmp_path)
    shutil.rmtree(repo)
    shutil.copytree(grading / "reference", repo)
    result = ObjectiveGrader().grade(_cfg(), Ctx(str(repo), str(grading)))
    assert result.detail["aggregate"] == "rungs"
    assert result.detail["points"] == pytest.approx(RUNGS) and result.score == 1.0


def test_wrong_from_a_size_stops_the_staircase(tmp_path: Path) -> None:
    repo, grading = _staged(tmp_path)
    (repo / "ref").mkdir()
    shutil.copy(grading / "reference" / "server.py", repo / "ref" / "algo_hard_server.py")
    shutil.copy(grading / "instance.json", repo / "inst.json")
    (repo / "stub.py").write_text(STUB)
    problem = json.loads((grading / "probes.json").read_text())["cases"][0]["problem"]
    wrong_from = 16_000 if problem != "grid_paths" else 10**9  # grid cases carry no `n`
    (repo / "run.sh").write_text(f"#!/bin/sh\nexec python3 stub.py {wrong_from}\n")
    (repo / "run.sh").chmod(0o755)
    result = ObjectiveGrader().grade(_cfg(), Ctx(str(repo), str(grading)))
    expected = 2.0 if problem != "grid_paths" else float(RUNGS)
    assert result.detail["points"] == pytest.approx(expected)
    assert result.score == pytest.approx(expected / RUNGS)
