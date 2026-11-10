"""Offline rating of a game's reference bots (design §6): round robin + balance check.

Every pair of rungs plays ``games_per_pair`` games (half the openings, each twice with the sides
swapped), in a process pool. Reference bots are deterministic from the seeds, so a rating run is
reproducible. The same Elo estimator and prior as the grader's are used.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

from .base import Game, game_from_wire
from .bots import make_bot
from .rating import fit_round_robin
from .referee import BotPlayer, make_opening, play_game, play_pair, score_for_a

OPENING_PLIES = 4  # random plies before the bots take over; stored with the ratings
SEED_STRIDE = 100_003
BALANCE_RUNG = 3  # the mirror match for the balance check uses this rung (clamped to the ladder)
BALANCE_GAMES = 200


@dataclass
class RoundRobin:
    ratings: list[float]
    pairs: dict[tuple[int, int], tuple[float, float]]
    first_player_score: float
    seconds: float
    games_per_pair: int
    opening_plies: int = OPENING_PLIES
    notes: list[str] = field(default_factory=list)


def _pair_job(args: tuple) -> tuple[int, int, float, float]:
    wire, i, j, k, seed, plies = args
    g = game_from_wire(wire)
    specs = g.ladder()
    opening_seed = seed * SEED_STRIDE + k
    opening = make_opening(g, opening_seed, plies)
    a, b = BotPlayer(make_bot(specs[i])), BotPlayer(make_bot(specs[j]))
    first, second = play_pair(g, a, b, opening, 0, opening_seed ^ (i * 131 + j))
    pts, n = score_for_a(first, second)
    return i, j, pts, n


def _mirror_job(args: tuple) -> float:
    wire, k, seed, plies = args
    g = game_from_wire(wire)
    spec = g.ladder()[min(BALANCE_RUNG, len(g.ladder()) - 1)]
    opening_seed = seed * SEED_STRIDE + k
    opening = make_opening(g, opening_seed, plies)
    players = (BotPlayer(make_bot(spec)), BotPlayer(make_bot(spec)))
    return play_game(g, players, opening, 0, opening_seed).result


def run_round_robin(
    game: Game,
    games_per_pair: int,
    seed: int,
    workers: int,
    plies: int = OPENING_PLIES,
    progress: Callable[[str], None] | None = None,
) -> RoundRobin:
    if games_per_pair < 2 or games_per_pair % 2:
        raise ValueError("games_per_pair must be an even number >= 2")
    wire = game.wire()
    n = len(game.ladder())
    jobs = [
        (wire, i, j, k, seed, plies)
        for i in range(n)
        for j in range(i + 1, n)
        for k in range(games_per_pair // 2)
    ]
    t0 = time.perf_counter()
    totals: dict[tuple[int, int], list[float]] = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for done, (i, j, pts, games) in enumerate(pool.map(_pair_job, jobs, chunksize=1), 1):
            cell = totals.setdefault((i, j), [0.0, 0.0])
            cell[0] += pts
            cell[1] += games
            if progress and done % max(1, len(jobs) // 10) == 0:
                progress(f"  {done}/{len(jobs)} pair-games ({time.perf_counter() - t0:.0f}s)")
        mirror = list(pool.map(_mirror_job, [(wire, k, seed, plies) for k in range(BALANCE_GAMES)]))
    pairs = {key: (v[0], v[1]) for key, v in totals.items()}
    return RoundRobin(
        ratings=fit_round_robin(n, pairs),
        pairs=pairs,
        first_player_score=sum(mirror) / len(mirror),
        seconds=time.perf_counter() - t0,
        games_per_pair=games_per_pair,
        opening_plies=plies,
    )
