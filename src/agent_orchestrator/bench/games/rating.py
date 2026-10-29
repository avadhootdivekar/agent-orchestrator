"""Elo rating by maximum likelihood (design §5).

Logistic model, 400 points per factor 10. Draws count one half. A **prior** of one virtual game
at score 0.5 against every opponent that was played keeps a perfect score from rating infinity.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

ELO_SCALE = 400.0
LN10 = math.log(10.0)
PRIOR_GAMES = 1.0
PRIOR_SCORE = 0.5
RATING_MIN = -2000.0
RATING_MAX = 4000.0
Z95 = 1.96
NEWTON_ITERS = 100
TOL = 1e-6


def expected(r: float, opp: float) -> float:
    """Expected score of a player rated ``r`` against one rated ``opp``."""
    return 1.0 / (1.0 + 10.0 ** ((opp - r) / ELO_SCALE))


@dataclass(frozen=True)
class Rating:
    rating: float
    se: float

    @property
    def ci95(self) -> tuple[float, float]:
        return (self.rating - Z95 * self.se, self.rating + Z95 * self.se)


def rate_against(results: Sequence[tuple[float, float, float]]) -> Rating:
    """MLE rating given ``(opponent rating, score, games)`` for each played opponent.

    ``score`` is the total points won (a win counts 1, a draw 0.5). With no games the rating is 0
    with a very large error.
    """
    data = [(o, s + PRIOR_SCORE * PRIOR_GAMES, n + PRIOR_GAMES) for o, s, n in results if n > 0]
    if not data:
        return Rating(0.0, float("inf"))
    r = sum(o for o, _, _ in data) / len(data)
    k = LN10 / ELO_SCALE
    for _ in range(NEWTON_ITERS):
        grad = hess = 0.0
        for o, s, n in data:
            p = expected(r, o)
            grad += k * (s - n * p)
            hess += k * k * n * p * (1.0 - p)
        if hess <= 0.0:
            break
        step = grad / hess
        r = min(RATING_MAX, max(RATING_MIN, r + step))
        if abs(step) < TOL:
            break
    fisher = sum(k * k * n * expected(r, o) * (1.0 - expected(r, o)) for o, _, n in data)
    return Rating(r, 1.0 / math.sqrt(fisher) if fisher > 0 else float("inf"))


def fit_round_robin(n_bots: int, pairs: dict[tuple[int, int], tuple[float, float]]) -> list[float]:
    """Ratings of ``n_bots`` bots from ``{(i, j): (points of i, games)}`` (i < j), bot 0 at 0.

    Each played pair gets the same virtual-game prior as :func:`rate_against`. Diagonal-Newton
    sweeps until the largest change is below tolerance.
    """
    played = {
        (i, j): (s + PRIOR_SCORE * PRIOR_GAMES, n + PRIOR_GAMES) for (i, j), (s, n) in pairs.items()
    }
    ratings = [0.0] * n_bots
    k = LN10 / ELO_SCALE
    for _ in range(NEWTON_ITERS * 10):
        biggest = 0.0
        for i in range(1, n_bots):
            grad = hess = 0.0
            for (a, b), (s, n) in played.items():
                if i not in (a, b):
                    continue
                opp = b if i == a else a
                points = s if i == a else n - s
                p = expected(ratings[i], ratings[opp])
                grad += k * (points - n * p)
                hess += k * k * n * p * (1.0 - p)
            if hess <= 0.0:
                continue
            step = grad / hess
            new = min(RATING_MAX, max(RATING_MIN, ratings[i] + step))
            biggest = max(biggest, abs(new - ratings[i]))
            ratings[i] = new
        if biggest < TOL:
            break
    return ratings
