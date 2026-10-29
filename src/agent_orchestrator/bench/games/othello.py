"""Othello / Reversi on an ``n x n`` board (``n`` even, 4..8), as a bitboard game.

Cell ``i = row * n + col`` is bit ``i``. Player 0 plays black and moves first. The generic
:class:`~agent_orchestrator.bench.games.base.Game` interface handles passes inside ``apply``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .base import (
    DRAW,
    LOSS,
    WIN,
    BotSpec,
    Game,
    GameError,
    JsonDict,
    Move,
    State,
    check_int,
    register_game,
)

MIN_SIZE = 4
MAX_SIZE = 8
EVEN = 2
# State: (black bits, white bits, to_move, plies); to_move == -1 means the game is over.
OVER = -1
# Evaluation weights (design: simple, hand-written once; the ladder's strength is search depth).
CORNER_W = 25
X_SQUARE_W = -8  # diagonal neighbours of a corner
C_SQUARE_W = -4  # edge neighbours of a corner
EDGE_W = 3
INNER_W = 1
MOBILITY_W = 2
DIRECTIONS = ("E", "W", "S", "N", "SE", "SW", "NE", "NW")


class _Geometry:
    """Masks and shifts for one board size."""

    def __init__(self, n: int) -> None:
        self.n = n
        self.full = (1 << (n * n)) - 1
        col0 = sum(1 << (r * n) for r in range(n))
        last = sum(1 << (r * n + n - 1) for r in range(n))
        self.not_col0 = self.full & ~col0
        self.not_last = self.full & ~last
        corners = {0, n - 1, n * (n - 1), n * n - 1}
        self.corner_mask = sum(1 << i for i in corners)
        x_sq = {n + 1, 2 * n - 2, n * (n - 2) + 1, n * (n - 1) - 2}
        c_sq = {1, n, n - 2, 2 * n - 1, n * (n - 1) + 1, n * (n - 2), n * n - 2, n * (n - 1) - 1}
        self.x_mask = sum(1 << i for i in x_sq)
        self.c_mask = sum(1 << i for i in c_sq)
        edge = {i for i in range(n * n) if i % n in (0, n - 1) or i // n in (0, n - 1)}
        self.edge_mask = sum(1 << i for i in edge) & ~(self.corner_mask | self.c_mask)
        self.inner_mask = self.full & ~(
            self.corner_mask | self.x_mask | self.c_mask | self.edge_mask
        )

    def shift(self, b: int, d: str) -> int:
        n = self.n
        if d == "E":
            return (b << 1) & self.not_col0
        if d == "W":
            return (b >> 1) & self.not_last
        if d == "S":
            return (b << n) & self.full
        if d == "N":
            return b >> n
        if d == "SE":
            return (b << (n + 1)) & self.not_col0 & self.full
        if d == "SW":
            return (b << (n - 1)) & self.not_last & self.full
        if d == "NE":
            return (b >> (n - 1)) & self.not_col0
        return (b >> (n + 1)) & self.not_last  # NW


def _moves_mask(g: _Geometry, me: int, opp: int) -> int:
    empty = g.full & ~(me | opp)
    out = 0
    for d in DIRECTIONS:
        t = g.shift(me, d) & opp
        for _ in range(g.n - 3):
            t |= g.shift(t, d) & opp
        out |= g.shift(t, d) & empty
    return out


def _flips(g: _Geometry, me: int, opp: int, move_bit: int) -> int:
    flipped = 0
    for d in DIRECTIONS:
        run = 0
        x = g.shift(move_bit, d)
        while x & opp:
            run |= x
            x = g.shift(x, d)
        if x & me:
            flipped |= run
    return flipped


def _bits(mask: int) -> list[int]:
    out = []
    while mask:
        low = mask & -mask
        out.append(low.bit_length() - 1)
        mask ^= low
    return out


@register_game
class Othello(Game):
    id = "othello"
    rules_version = 1

    def __init__(self, size: int = 8) -> None:
        if size < MIN_SIZE or size > MAX_SIZE or size % EVEN:
            raise GameError(f"othello size must be an even number in {MIN_SIZE}..{MAX_SIZE}")
        self.size = size
        self._g = _Geometry(size)

    def params(self) -> JsonDict:
        return {"size": self.size}

    @classmethod
    def from_params(cls, params: Mapping[str, Any]) -> Othello:
        size = check_int(params, "size", MIN_SIZE, MAX_SIZE)
        if set(params) != {"size"}:
            raise GameError(f"othello takes only 'size', got {sorted(params)}")
        return cls(size)

    # -- rules ------------------------------------------------------------------------------
    def initial(self) -> State:
        n, h = self.size, self.size // 2
        white = (1 << ((h - 1) * n + h - 1)) | (1 << (h * n + h))
        black = (1 << ((h - 1) * n + h)) | (1 << (h * n + h - 1))
        return (black, white, 0, 0)

    def to_move(self, s: State) -> int:
        return s[2]

    def plies(self, s: State) -> int:
        return s[3]

    def max_plies(self) -> int:
        return self.size * self.size  # every ply places a disc

    def _sides(self, s: State) -> tuple[int, int]:
        return (s[0], s[1]) if s[2] == 0 else (s[1], s[0])

    def legal(self, s: State) -> list[Move]:
        if s[2] == OVER:
            return []
        me, opp = self._sides(s)
        return list(_bits(_moves_mask(self._g, me, opp)))

    def apply(self, s: State, m: Move) -> State:
        g = self._g
        p = s[2]
        me, opp = self._sides(s)
        bit = 1 << int(m)  # type: ignore[arg-type]
        fl = _flips(g, me, opp, bit)
        me |= bit | fl
        opp &= ~fl
        black, white = (me, opp) if p == 0 else (opp, me)
        # Next player: the opponent unless it has no move; then the mover again; else over.
        if _moves_mask(g, opp, me):
            nxt = 1 - p
        elif _moves_mask(g, me, opp):
            nxt = p
        else:
            nxt = OVER
        return (black, white, nxt, s[3] + 1)

    def result(self, s: State) -> float:
        b, w = s[0].bit_count(), s[1].bit_count()
        return WIN if b > w else LOSS if b < w else DRAW

    # -- evaluation ---------------------------------------------------------------------------
    def material(self, s: State, p: int) -> float:
        mine, theirs = (s[0], s[1]) if p == 0 else (s[1], s[0])
        return float(mine.bit_count() - theirs.bit_count())

    def heuristic(self, s: State, p: int) -> float:
        g = self._g
        mine, theirs = (s[0], s[1]) if p == 0 else (s[1], s[0])
        if s[2] == OVER:  # exact outcome dominates every positional term
            return 1000.0 * (mine.bit_count() - theirs.bit_count())
        score = 0
        for mask, w in (
            (g.corner_mask, CORNER_W),
            (g.x_mask, X_SQUARE_W),
            (g.c_mask, C_SQUARE_W),
            (g.edge_mask, EDGE_W),
            (g.inner_mask, INNER_W),
        ):
            score += w * ((mine & mask).bit_count() - (theirs & mask).bit_count())
        mob = _moves_mask(g, mine, theirs).bit_count() - _moves_mask(g, theirs, mine).bit_count()
        return float(score + MOBILITY_W * mob)

    # -- wire format ----------------------------------------------------------------------------
    def encode_state(self, s: State) -> JsonDict:
        n = self.size
        rows = []
        for r in range(n):
            row = []
            for c in range(n):
                bit = 1 << (r * n + c)
                row.append("B" if s[0] & bit else "W" if s[1] & bit else ".")
            rows.append("".join(row))
        return {"board": rows, "to_move": s[2], "plies": s[3]}

    def rules_md(self) -> str:
        n = self.size
        return (
            f"**Othello (Reversi)** on a {n}x{n} board. Player 0 plays black (`B`) and moves "
            "first; "
            "player 1 plays white (`W`).\n\n"
            f"- Squares are numbered `row * {n} + col`, with row 0 at the top and column 0 at the "
            "left. A move is one such integer.\n"
            f"- Start position: the four centre squares are occupied, white on `({n // 2 - 1}, "
            f"{n // 2 - 1})` and `({n // 2}, {n // 2})`, black on `({n // 2 - 1}, {n // 2})` and "
            f"`({n // 2}, {n // 2 - 1})`.\n"
            "- A move places a disc of your colour on an empty square such that, in at least one "
            "of "
            "the eight directions, an unbroken line of opponent discs starting next to the new "
            "disc "
            "ends in a disc of your colour. Every such line is flipped to your colour. A move that "
            "flips nothing is illegal.\n"
            "- A player who has no legal move is skipped; the other player moves again. You are "
            "never asked to move when you have no legal move.\n"
            "- The game ends when neither player has a legal move. The player with more discs "
            "wins; "
            "equal counts are a draw.\n\n"
            'State on the wire: `{"board": [<row string>, ...], "to_move": 0|1, "plies": N}` '
            "with `B` black, `W` white and `.` empty, row 0 first."
        )

    # -- ladder -----------------------------------------------------------------------------
    def ladder(self) -> Sequence[BotSpec]:
        return (
            ("random",), ("greedy",), ("ab", 1), ("ab", 2), ("ab", 3), ("ab", 4), ("ab", 5),
            ("ab", 6),
        )  # fmt: skip
