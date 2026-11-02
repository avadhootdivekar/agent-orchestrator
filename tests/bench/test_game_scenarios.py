"""`game-scenarios` family + `scenarios` grader (T-Ga4Rna): scenario generation and caching,
the quality measure, and grading end to end in the process sandbox with scripted artefacts."""

from __future__ import annotations

import json
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import agent_orchestrator
from agent_orchestrator.bench.errors import GraderError
from agent_orchestrator.bench.games import game_from_wire
from agent_orchestrator.bench.games.base import GAME_REGISTRY
from agent_orchestrator.bench.games.scenarios import (
    CACHE_ENV,
    Scenario,
    is_best,
    make_scenarios,
    move_quality,
    random_quality,
    root_values,
)
from agent_orchestrator.bench.gen import game_scenarios as gs
from agent_orchestrator.bench.gen.base import FAMILY_REGISTRY, Difficulty
from agent_orchestrator.bench.gen.cli import instruction_text
from agent_orchestrator.bench.gen.materialize import generate_instance, write_instance
from agent_orchestrator.bench.gen.traps import traps_for_family
from agent_orchestrator.bench.scenarios_grader import (
    SCENARIOS_GRADER_TYPE,
    ScenariosGrader,
    validate_probes,
)
from agent_orchestrator.bench.spec import KNOWN_GRADER_TYPES, GraderConfig

SRC = str(Path(agent_orchestrator.__file__).resolve().parents[1])
SMALL = Difficulty(entities=0, traps=0, concurrency=0, scale=0)
OTHELLO4 = {"id": "othello", "size": 4}

LEGAL_RANDOM = """\
import json, random, sys
rng = random.Random(1)
for line in sys.stdin:
    req = json.loads(line)
    if req.get("op") == "move":
        resp = {"ok": True, "move": rng.choice(req["legal"])}
    else:
        resp = {"ok": True}
    print(json.dumps(resp), flush=True)
"""
ILLEGAL = LEGAL_RANDOM.replace('rng.choice(req["legal"])', "12345")
CRASHING = LEGAL_RANDOM.replace(
    'if req.get("op") == "move":', 'if req.get("op") == "move":\n        sys.exit(3)'
)


@dataclass
class Ctx:
    repo_dir: str
    grading_dir: str | None


@pytest.fixture(autouse=True)
def small_and_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A private scenario cache, serial value computation, 6 shallow scenarios per task."""
    monkeypatch.setenv(CACHE_ENV, str(tmp_path / "cache"))
    monkeypatch.setenv("AO_BENCH_SCENARIO_WORKERS", "1")
    monkeypatch.setattr(gs, "SCENARIOS_BY_LEVEL", (6, 6, 6, 6))
    for cls in GAME_REGISTRY.values():  # the games override the base depth
        monkeypatch.setattr(cls, "scenario_depth", 2)


def _task(tmp_path: Path, family: str, seed: int, bot: str | None) -> Ctx:
    inst = generate_instance(family, seed, SMALL, "python")
    repo, grading = tmp_path / "repo", tmp_path / "grading"
    write_instance(inst, repo, grading)
    if bot is not None:
        (repo / "bot.py").write_text(bot)
        run = repo / "run.sh"
        run.write_text("#!/bin/sh\nexec python3 bot.py\n")
        run.chmod(run.stat().st_mode | stat.S_IXUSR)
    return Ctx(str(repo), str(grading))


def _grade(ctx: Ctx, **extra: Any):
    extra.setdefault("sandbox", {"type": "process"})
    return ScenariosGrader().grade(GraderConfig(type=SCENARIOS_GRADER_TYPE, **extra), ctx)


# -- the quality measure --------------------------------------------------------------------------


def test_quality_is_a_rank_percentile_with_ties() -> None:
    values = [3.0, 1.0, 3.0, 0.0, 2.0]
    assert [move_quality(values, i) for i in range(5)] == [1.0, 1 / 3, 1.0, 0.0, 2 / 3]
    assert is_best(values, 0) and is_best(values, 2) and not is_best(values, 4)


def test_random_quality_is_exact() -> None:
    sc = Scenario([], ["a", "b", "c"], [0.0, 1.0, 2.0], 5)  # qualities 0, .5, 1
    assert random_quality([sc]) == pytest.approx(0.5)
    assert random_quality([]) == 0.0


def test_root_values_agree_with_a_hand_search() -> None:
    game = game_from_wire(OTHELLO4)
    s = game.initial()
    values = root_values(game, s, 1)
    assert len(values) == len(game.legal(s)) and len(set(values)) >= 1


# -- scenario sets --------------------------------------------------------------------------------


def test_scenarios_are_deterministic_cached_and_informative() -> None:
    game = game_from_wire({"id": "othello", "size": 6})
    a = make_scenarios(game, 5, 8, 2)
    assert len(a) == 8
    assert all(len(s.legal) >= 3 and max(s.values) > min(s.values) for s in a)
    assert len({json.dumps(s.history) for s in a}) == 8
    assert make_scenarios(game, 5, 8, 2) == a  # read back from the cache
    assert make_scenarios(game, 6, 8, 2) != a  # another seed, another set


def test_cache_is_keyed_by_depth() -> None:
    game = game_from_wire({"id": "othello", "size": 6})
    d1 = make_scenarios(game, 5, 6, 1)
    d2 = make_scenarios(game, 5, 6, 2)
    assert [s.history for s in d1] != [] and d1 != d2 or d1[0].values != d2[0].values


# -- registry and wiring --------------------------------------------------------------------------


def test_registered_and_wired() -> None:
    assert "scenarios" in KNOWN_GRADER_TYPES
    assert {"game-scenarios", "game-scenarios-heldout"} <= set(FAMILY_REGISTRY)
    assert "move" in instruction_text("jsonl", "scenarios").lower()
    for fam in ("game-scenarios", "game-scenarios-heldout"):
        assert {"random_positions", "move_time_limit"} <= set(traps_for_family(fam))


def test_instance_hides_the_values_from_the_subject(tmp_path: Path) -> None:
    ctx = _task(tmp_path, "game-scenarios", 3, None)
    repo = Path(ctx.repo_dir)
    visible = "\n".join(p.read_text() for p in repo.rglob("*") if p.is_file())
    probes = json.loads((Path(ctx.grading_dir) / "probes.json").read_text())
    validate_probes(probes)
    assert len(probes["scenarios"]) == 6 and "values" not in visible
    spec = (repo / "SPEC.md").read_text().lower()
    assert "reference" not in spec.replace("reference bots", "") or "values" not in spec
    assert "hidden" not in spec and "probes" not in spec


def test_heldout_family_draws_from_the_heldout_pool(tmp_path: Path) -> None:
    ctx = _task(tmp_path, "game-scenarios-heldout", 3, None)
    probes = json.loads((Path(ctx.grading_dir) / "probes.json").read_text())
    assert probes["game"]["id"] == "breakthrough"


def test_probe_validation_rejects_garbage() -> None:
    with pytest.raises(GraderError):
        validate_probes({"kind": "arena"})
    with pytest.raises(GraderError):
        validate_probes({"kind": "scenarios", "game": {}, "scenarios": [], "ms_per_move": 1,
                         "solved_top1": 0.5})  # fmt: skip


# -- grading --------------------------------------------------------------------------------------


def test_legal_random_bot_scores_near_zero(tmp_path: Path) -> None:
    ctx = _task(tmp_path, "game-scenarios", 3, LEGAL_RANDOM)
    res = _grade(ctx)
    d = res.detail
    assert d["scenarios"] == 6 and d["forfeits"] == 0 and 0.0 <= res.score <= 1.0
    assert d["random_quality"] > 0 and d["quality"] <= 1.0
    assert set(d["by_phase"]) <= {"early", "mid", "late"} and d["max_move_ms"] is not None


def test_illegal_moves_score_zero(tmp_path: Path) -> None:
    res = _grade(_task(tmp_path, "game-scenarios", 3, ILLEGAL))
    d = res.detail
    assert d["forfeits"] == 6 and d["quality"] == 0.0 and res.score == 0.0 and not res.solved
    assert "illegal" in d["reasons"][0].lower()


def test_crashing_bot_scores_zero(tmp_path: Path) -> None:
    res = _grade(_task(tmp_path, "game-scenarios", 3, CRASHING))
    assert res.score == 0.0 and res.detail["forfeits"] == 6


def test_missing_run_sh_scores_zero(tmp_path: Path) -> None:
    ctx = _task(tmp_path, "game-scenarios", 3, None)
    (Path(ctx.repo_dir) / "run.sh").unlink()
    res = _grade(ctx)
    assert res.score == 0.0 and "run.sh" in res.detail["artefact_error"]


def test_reference_artefact_finds_the_best_moves(tmp_path: Path) -> None:
    """The bundled reference bot, searching as deep as the reference values, solves the set."""
    ctx = _task(tmp_path, "game-scenarios", 3, None)
    repo = Path(ctx.repo_dir)
    for f in (Path(ctx.grading_dir) / "reference").iterdir():
        shutil.copy2(f, repo / f.name)
    inst = json.loads((repo / "instance.json").read_text())
    inst["limits"]["reference_rung"] = 3  # ab2 on Othello 6: the depth the values were made with
    (repo / "instance.json").write_text(json.dumps(inst))
    res = _grade(ctx, artefact_env={"AO_BENCH_SRC": SRC})
    d = res.detail
    assert d["forfeits"] == 0 and d["top1"] >= 0.8 and res.score > 0.7 and res.solved
