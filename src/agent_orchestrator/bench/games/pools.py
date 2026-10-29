"""Dev and held-out pools of game configurations (design §7), indexed by the `entities` level.

Each pool maps level 0..3 to the game configurations a seed may draw from (smaller boards at low
levels). Only configurations that passed the balance check (design §9) are listed; each needs a
frozen-ratings file (``ao-bench arena-rate``). Free-placement Connect-K is supported by the
plugin but not shipped: it is mostly draws at shallow depth.
"""

from __future__ import annotations

import random
from typing import Any

from .base import Game, game_from_wire

Wire = dict[str, Any]


def _connectk(w: int, h: int, k: int) -> Wire:
    return {"id": "connectk", "width": w, "height": h, "k": k, "gravity": 1}


OTHELLO_6: Wire = {"id": "othello", "size": 6}
OTHELLO_8: Wire = {"id": "othello", "size": 8}

DEV_POOL: tuple[tuple[Wire, ...], ...] = (
    (_connectk(6, 6, 4), OTHELLO_6),
    (_connectk(7, 6, 4), _connectk(6, 7, 4), OTHELLO_6),
    (_connectk(9, 7, 5), OTHELLO_8),
    (_connectk(8, 8, 5), OTHELLO_8),
)
HELDOUT_POOL: tuple[tuple[Wire, ...], ...] = (
    ({"id": "breakthrough", "size": 7},),
    ({"id": "breakthrough", "size": 7},),
    ({"id": "breakthrough", "size": 8},),
    ({"id": "breakthrough", "size": 8},),
)
POOLS = {"dev": DEV_POOL, "heldout": HELDOUT_POOL}


def pick_game(pool: tuple[tuple[Wire, ...], ...], level: int, rng: random.Random) -> Game:
    options = pool[level]
    return game_from_wire(options[rng.randrange(len(options))])


def pool_games(pool: tuple[tuple[Wire, ...], ...]) -> list[Game]:
    """Every distinct configuration of the pool, in first-seen order."""
    seen: dict[str, Game] = {}
    for level in pool:
        for wire in level:
            g = game_from_wire(wire)
            seen.setdefault(g.key, g)
    return list(seen.values())
