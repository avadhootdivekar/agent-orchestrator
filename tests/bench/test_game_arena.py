"""`game-arena` family + `arena` grader (T-Ga4Rna): generation, spec hygiene, and grading end to
end in the process sandbox with scripted artefacts (no LLM, fixed seeds)."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import agent_orchestrator
from agent_orchestrator.bench.arena import ARENA_GRADER_TYPE, ArenaGrader, validate_probes
from agent_orchestrator.bench.errors import GraderError
from agent_orchestrator.bench.games import game_from_wire
from agent_orchestrator.bench.games import ladder as ladder_mod
from agent_orchestrator.bench.games.ladder import ladder_version, load_ladder, write_ladder
from agent_orchestrator.bench.games.pools import DEV_POOL, HELDOUT_POOL, pool_games
from agent_orchestrator.bench.games.roundrobin import RoundRobin
from agent_orchestrator.bench.gen.base import FAMILY_REGISTRY, Difficulty
from agent_orchestrator.bench.gen.cli import build_suite, instruction_text
from agent_orchestrator.bench.gen.materialize import generate_instance, write_instance
from agent_orchestrator.bench.spec import KNOWN_GRADER_TYPES, GraderConfig

SRC = str(Path(agent_orchestrator.__file__).resolve().parents[1])
SMALL = Difficulty(entities=0, traps=0, concurrency=0, scale=0)  # 4 games per rung, 1000 ms
FAST = Difficulty(entities=0, traps=0, concurrency=0, scale=3)  # 100 ms per move

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
SLOW = "import time\n" + LEGAL_RANDOM.replace(
    'if req.get("op") == "move":', 'if req.get("op") == "move":\n        time.sleep(1.5)'
)
CRASHING = LEGAL_RANDOM.replace(
    'if req.get("op") == "move":', 'if req.get("op") == "move":\n        sys.exit(3)'
)


@dataclass
class Ctx:
    repo_dir: str
    grading_dir: str | None


@pytest.fixture
def ratings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Synthetic frozen ratings (0, 100, 200, ...) for every pooled game."""
    root = tmp_path / "ratings"
    monkeypatch.setattr(ladder_mod, "RATINGS_DIR", root)
    for g in pool_games(DEV_POOL) + pool_games(HELDOUT_POOL):
        n = len(g.ladder())
        rr = RoundRobin([100.0 * i for i in range(n)], {}, 0.5, 0.0, 40)
        write_ladder(g, rr, root)
    return root


def _task(tmp_path: Path, family: str, seed: int, diff: Difficulty, bot: str | None) -> Ctx:
    inst = generate_instance(family, seed, diff, "python")
    repo, grading = tmp_path / "repo", tmp_path / "grading"
    write_instance(inst, repo, grading)
    if bot is not None:  # replace the skeleton with a scripted artefact
        (repo / "bot.py").write_text(bot)
        run = repo / "run.sh"
        run.write_text("#!/bin/sh\nexec python3 bot.py\n")
        run.chmod(run.stat().st_mode | stat.S_IXUSR)
    return Ctx(str(repo), str(grading))


def _grade(ctx: Ctx, **extra: Any):
    extra.setdefault("sandbox", {"type": "process"})
    return ArenaGrader().grade(GraderConfig(type=ARENA_GRADER_TYPE, **extra), ctx)


# -- registry and wiring --------------------------------------------------------------------------


def test_registered_everywhere() -> None:
    assert "arena" in KNOWN_GRADER_TYPES
    assert {"game-arena", "game-arena-heldout"} <= set(FAMILY_REGISTRY)
    assert FAMILY_REGISTRY["game-arena"].grader_type == ARENA_GRADER_TYPE
    assert "bot" in instruction_text("jsonl", "arena").lower()


def test_pools_are_disjoint_and_valid() -> None:
    dev = {g.key for g in pool_games(DEV_POOL)}
    held = {g.key for g in pool_games(HELDOUT_POOL)}
    assert dev and held and not dev & held
    assert all(len(level) >= 1 for level in DEV_POOL + HELDOUT_POOL) and len(DEV_POOL) == 4
    assert {g.id for g in pool_games(HELDOUT_POOL)} == {"breakthrough"}


# -- shipped ratings stay in step with the code ---------------------------------------


@pytest.mark.parametrize(
    "game", pool_games(DEV_POOL) + pool_games(HELDOUT_POOL), ids=lambda g: g.key
)
def test_shipped_ratings_are_fresh_and_sane(game) -> None:
    frozen = load_ladder(game)  # raises when missing or stale: re-run `ao-bench arena-rate`
    assert frozen.ratings[0] == 0.0
    assert frozen.ratings[-1] > 300.0 and max(frozen.ratings) == pytest.approx(
        frozen.ratings[-1], abs=150
    )
    lo, hi = game.balance_bounds()
    assert lo <= frozen.first_player_score <= hi, "unbalanced game: pick other parameters"


# -- generation --------------------------------------------------------------------------


def test_generation_is_deterministic_and_hides_the_ladder(ratings: Path) -> None:
    a = generate_instance("game-arena", 7, SMALL, "python")
    b = generate_instance("game-arena", 7, SMALL, "python")
    assert a.model_dump() == b.model_dump()
    validate_probes(a.probes)
    spec = a.spec_md
    assert "State on the wire" in spec and "ms_per_move" in spec
    for hidden in ("ladder_version", "par_rating", "solved_rating", "probes.json", "opening_seed"):
        assert hidden not in spec  # grader-owned names never reach the subject
    assert "alpha-beta" in spec and "Elo" in spec


def test_pools_decide_the_game(ratings: Path) -> None:
    def games(family: str, seeds: range) -> set[str]:
        return {
            generate_instance(family, s, Difficulty(entities=1), "python").probes["game"]["id"]
            for s in seeds
        }

    dev = games("game-arena", range(12))
    assert dev <= {"othello", "connectk"} and "connectk" in dev
    assert games("game-arena-heldout", range(4)) == {"breakthrough"}


def test_difficulty_axes_set_the_knobs(ratings: Path) -> None:
    easy = generate_instance("game-arena", 3, Difficulty(concurrency=0, scale=0), "python")
    hard = generate_instance("game-arena", 3, Difficulty(concurrency=3, scale=3), "python")
    assert (easy.probes["games_per_rung"], easy.probes["ms_per_move"]) == (4, 1000)
    assert (hard.probes["games_per_rung"], hard.probes["ms_per_move"]) == (20, 100)


def test_missing_ratings_is_a_clear_generation_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ladder_mod, "RATINGS_DIR", tmp_path / "none")
    with pytest.raises(ValueError, match="arena-rate"):
        generate_instance("game-arena", 1, SMALL, "python")


def test_suite_builder_accepts_the_family(ratings: Path) -> None:
    suite, instructions = build_suite(
        suite_id="x", families=["game-arena"], count=2, languages=["python"], base_seed=5,
        difficulty=SMALL, tier="large", parity="p", command="c",
    )  # fmt: skip
    assert {t["grader"]["type"] for t in suite["tasks"]} == {"arena"}
    assert any("arena" in path for path in instructions)


# -- grading end to end ---------------------------------------------------------------------------


def test_legal_random_bot_is_rated_near_the_bottom_and_stops_early(
    tmp_path: Path, ratings: Path
) -> None:
    ctx = _task(tmp_path, "game-arena", 3, SMALL, LEGAL_RANDOM)
    res = _grade(ctx)
    d = res.detail
    assert d["game"] and d["ladder_version"] == ladder_version(game_from_wire(json.loads(
        (Path(ctx.grading_dir) / "probes.json").read_text())["game"]))  # fmt: skip
    played = [r for r in d["rungs"] if r["played"]]
    assert played[0]["rung"] == "random" and played[0]["games"] == 4
    assert any(not r["played"] for r in d["rungs"]), "the climb stops once rungs are lost"
    assert d["rating"] < 150 and res.score < 0.5 and not res.solved
    assert all(r["forfeits"] == 0 for r in played)
    assert d["moves"] > 0 and d["ci95"][0] < d["rating"] < d["ci95"][1]


def test_illegal_moves_forfeit_every_game(tmp_path: Path, ratings: Path) -> None:
    res = _grade(_task(tmp_path, "game-arena", 3, SMALL, ILLEGAL))
    first = res.detail["rungs"][0]
    assert first["forfeits"] == 4 and first["points"] == 0 and "illegal" in first["reasons"][0]
    assert res.score == 0.0 and res.detail["rating"] < 0


def test_crash_and_timeout_forfeit_and_the_bot_is_restarted(tmp_path: Path, ratings: Path) -> None:
    res = _grade(_task(tmp_path, "game-arena", 3, FAST, CRASHING))
    first = res.detail["rungs"][0]
    assert first["forfeits"] == first["games"] == 4 and res.score == 0.0
    slow = _grade(_task(tmp_path / "s", "game-arena", 3, FAST, SLOW))
    assert slow.detail["rungs"][0]["forfeits"] == 4
    assert "no move within 100 ms" in slow.detail["rungs"][0]["reasons"][0]


def test_missing_run_sh_scores_zero(tmp_path: Path, ratings: Path) -> None:
    ctx = _task(tmp_path, "game-arena", 3, SMALL, None)
    (Path(ctx.repo_dir) / "run.sh").unlink()
    res = _grade(ctx)
    assert res.score == 0.0 and not res.solved and "run.sh" in res.detail["artefact_error"]
    assert all(not r["played"] for r in res.detail["rungs"])


def test_stale_probes_are_refused(tmp_path: Path, ratings: Path) -> None:
    ctx = _task(tmp_path, "game-arena", 3, SMALL, LEGAL_RANDOM)
    path = Path(ctx.grading_dir) / "probes.json"
    probes = json.loads(path.read_text())
    probes["ladder_version"] = "0" * 12
    path.write_text(json.dumps(probes))
    with pytest.raises(GraderError, match="ladder"):
        _grade(ctx)


def test_budget_stops_the_climb(tmp_path: Path, ratings: Path) -> None:
    res = _grade(_task(tmp_path, "game-arena", 3, SMALL, LEGAL_RANDOM), budget_seconds=-1)
    assert all(not r["played"] for r in res.detail["rungs"]) and res.score == 0.0


# -- reference bot as an artefact -----------------------------------------------------------------


def test_reference_server_speaks_the_protocol(tmp_path: Path, ratings: Path) -> None:
    ctx = _task(tmp_path, "game-arena", 3, SMALL, None)
    ref = Path(ctx.grading_dir) / "reference"
    game = game_from_wire(json.loads((Path(ctx.grading_dir) / "probes.json").read_text())["game"])
    s = game.initial()
    legal = [game.encode_move(m) for m in game.legal(s)]
    lines = [
        {"op": "ping"},
        {"op": "new", "game": game.wire(), "you": 0, "ms_per_move": 1000},
        {"op": "move", "state": game.encode_state(s), "legal": legal, "history": []},
        {"op": "move", "state": {}, "legal": [], "history": ["x"]},
    ]
    proc = subprocess.run(
        [sys.executable, str(ref / "server.py")],
        input="\n".join(json.dumps(x) for x in lines) + "\n",
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "AO_BENCH_SRC": SRC},
    )  # fmt: skip
    out = [json.loads(x) for x in proc.stdout.splitlines()]
    assert out[0] == {"ok": True} and out[1] == {"ok": True}
    assert out[2]["ok"] and out[2]["move"] in legal
    assert out[3]["ok"] is False  # a protocol server answers errors, never crashes


def test_reference_artefact_beats_the_weak_rungs(tmp_path: Path, ratings: Path) -> None:
    ctx = _task(tmp_path, "game-arena", 3, SMALL, None)
    repo = Path(ctx.repo_dir)
    for f in (Path(ctx.grading_dir) / "reference").iterdir():  # the reference bot as the subject
        shutil.copy2(f, repo / f.name)
    inst = json.loads((repo / "instance.json").read_text())
    inst["limits"]["reference_rung"] = 2  # a quick one-ply searcher keeps the test fast
    (repo / "instance.json").write_text(json.dumps(inst))
    res = _grade(ctx, artefact_env={"AO_BENCH_SRC": SRC})
    played = [r for r in res.detail["rungs"] if r["played"]]
    assert played[0]["points"] == played[0]["games"]  # beats random
    assert res.detail["rating"] > 200 and sum(r["forfeits"] for r in played) == 0
