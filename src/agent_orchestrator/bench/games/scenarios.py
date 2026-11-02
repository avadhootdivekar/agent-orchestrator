"""Fixed-scenario positions for `game-scenarios` (design `benchmark-game-arena-design.md` §11).

A scenario is a position reached by a random play-out from the initial position, together with a
value for every legal move from an exact fixed-depth alpha-beta search (the *reference depth*,
``Game.scenario_depth``). The subject answers each position once with a move; a move scores by its
rank among the legal moves (a percentile, so it does not depend on the evaluation scale):

    quality(m) = #{legal moves worse than m} / #{legal moves worse than the best}

so the best moves (ties included) score 1 and the worst move 0. The values are the hidden
assets; the subject sees only the position, its legal moves and the history.

Position sets are seeded, so they can be regenerated for held-out comparisons; computing the
values is the slow part, so a set is cached on disk (``scenario_cache_dir``), keyed by everything
that changes it.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import Game, GameError, Move, State, game_from_wire
from .bots import BOTS_VERSION, INF, AlphaBetaBot
from .referee import replay

SCENARIOS_VERSION = 1
MIN_LEGAL = 3  # a position with fewer legal moves is too easy to be informative
MIN_PLIES = 4
MAX_PLY_FRACTION = 0.7
MAX_PLY_CAP = 40
ATTEMPTS_PER_SCENARIO = 40
WORKERS_ENV = "AO_BENCH_SCENARIO_WORKERS"
CACHE_ENV = "AO_BENCH_CACHE"
EPS = 1e-9


@dataclass(frozen=True)
class Scenario:
    history: list[Any]  # encoded moves from the initial position
    legal: list[Any]  # encoded legal moves, in ``Game.legal`` order
    values: list[float]  # reference value of each legal move for the side to move
    plies: int

    def wire(self) -> dict[str, Any]:
        return {
            "history": self.history,
            "legal": self.legal,
            "values": self.values,
            "plies": self.plies,
        }

    @classmethod
    def from_wire(cls, raw: dict[str, Any]) -> Scenario:
        return cls(
            list(raw["history"]),
            list(raw["legal"]),
            [float(v) for v in raw["values"]],
            int(raw["plies"]),
        )


def move_quality(values: list[float], chosen: int) -> float:
    """Percentile score of the legal move at index *chosen* (1 = a best move)."""
    best = max(values)
    worse_than_best = sum(1 for v in values if v < best - EPS)
    if worse_than_best == 0:
        return 1.0
    worse_than_chosen = sum(1 for v in values if v < values[chosen] - EPS)
    return worse_than_chosen / worse_than_best


def is_best(values: list[float], chosen: int) -> bool:
    return values[chosen] >= max(values) - EPS


def random_quality(scenarios: list[Scenario]) -> float:
    """Expected quality of a uniformly random mover (exact, from the stored values)."""
    if not scenarios:
        return 0.0
    total = 0.0
    for sc in scenarios:
        total += sum(move_quality(sc.values, i) for i in range(len(sc.values))) / len(sc.values)
    return total / len(scenarios)


def root_values(game: Game, state: State, depth: int) -> list[float]:
    """Exact depth-``depth`` alpha-beta value of every legal move, for the side to move."""
    bot = AlphaBetaBot(depth, "heuristic")
    bot.new_game(game, game.to_move(state), 0)
    return [
        bot._search(game, game.apply(state, m), depth - 1, -INF, INF)  # noqa: SLF001
        for m in game.legal(state)
    ]


def _values_task(args: tuple[dict[str, Any], list[Any], int]) -> list[float]:
    wire, history, depth = args
    game = game_from_wire(wire)
    state = replay(game, [game.decode_move(m) for m in history])
    return root_values(game, state, depth)


def _draw_positions(game: Game, seed: int, count: int) -> list[list[Move]]:
    """*count* distinct non-terminal positions (as move histories) with enough legal moves."""
    top = min(int(game.max_plies() * MAX_PLY_FRACTION), MAX_PLY_CAP)
    if top < MIN_PLIES:
        raise GameError(f"{game.key}: too short for scenarios")
    out: list[list[Move]] = []
    seen: set[str] = set()
    for attempt in range(count * ATTEMPTS_PER_SCENARIO):
        if len(out) == count:
            break
        rng = random.Random((seed * 1_000_003 + attempt) & 0xFFFFFFFF)
        target = rng.randint(MIN_PLIES, top)
        s: State = game.initial()
        history: list[Move] = []
        for _ in range(target):
            legal = game.legal(s)
            if not legal:
                break
            m = rng.choice(legal)
            history.append(m)
            s = game.apply(s, m)
        if game.terminal(s) or len(game.legal(s)) < MIN_LEGAL:
            continue
        key = json.dumps([game.encode_move(m) for m in history])
        if key not in seen:
            seen.add(key)
            out.append(history)
    if len(out) < count:
        raise GameError(f"{game.key}: only {len(out)} of {count} scenarios found (seed {seed})")
    return out


def scenario_cache_dir() -> Path:
    base = os.environ.get(CACHE_ENV) or str(Path.home() / ".cache" / "ao-bench")
    return Path(base) / "scenarios"


def _cache_path(game: Game, seed: int, count: int, depth: int) -> Path:
    body = json.dumps(
        [game.wire(), game.rules_version, seed, count, depth, BOTS_VERSION, SCENARIOS_VERSION],
        sort_keys=True,
    )
    digest = hashlib.sha256(body.encode()).hexdigest()[:16]
    return scenario_cache_dir() / f"{game.key}-{digest}.json"


def make_scenarios(
    game: Game, seed: int, count: int, depth: int | None = None, *, workers: int | None = None
) -> list[Scenario]:
    """The scenario set for (game, seed, count, depth); computed once, then read from the cache."""
    depth = depth or game.scenario_depth
    path = _cache_path(game, seed, count, depth)
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        return [Scenario.from_wire(r) for r in cached]
    except (OSError, ValueError, KeyError):
        pass
    histories = _draw_positions(game, seed * 7919 + 13, count * 2)  # spare, for flat positions
    encoded = [[game.encode_move(m) for m in h] for h in histories]
    n_workers = workers or int(os.environ.get(WORKERS_ENV) or min(os.cpu_count() or 1, 16))
    jobs = [(game.wire(), e, depth) for e in encoded]
    if n_workers > 1:
        with ProcessPoolExecutor(max_workers=n_workers) as pool:
            all_values = list(pool.map(_values_task, jobs, chunksize=1))
    else:
        all_values = [_values_task(j) for j in jobs]
    out: list[Scenario] = []
    for hist, enc, values in zip(histories, encoded, all_values, strict=True):
        if max(values) - min(values) <= EPS:  # every move equal: nothing to tell apart
            continue
        state = replay(game, hist)
        legal = [game.encode_move(m) for m in game.legal(state)]
        out.append(Scenario(enc, legal, values, game.plies(state)))
        if len(out) == count:
            break
    if len(out) < count:
        raise GameError(f"{game.key}: only {len(out)} informative scenarios of {count}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps([s.wire() for s in out]), encoding="utf-8")
    tmp.replace(path)
    return out


__all__ = [
    "Scenario",
    "is_best",
    "make_scenarios",
    "move_quality",
    "random_quality",
    "root_values",
    "scenario_cache_dir",
]
