"""Breakthrough on an ``n x n`` board (n 5..8), as a bitboard game.

Player 0 starts on rows 0 and 1 and moves toward row ``n - 1``; player 1 starts on the last two
rows and moves toward row 0. A piece steps one square forward, or diagonally forward; only the
diagonal step may capture. The first player to reach the far row wins; a player who has no piece
or no legal move loses. There are no draws.

State: ``(bits0, bits1, to_move, plies, winner)``; a move is ``(from_cell, to_cell)``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .base import (
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

MIN_SIZE, MAX_SIZE = 5, 8
OVER = -1
NO_WINNER = -1
WIN_SCORE = 1_000_000.0
PIECE_W = 100.0
ADVANCE_W = 3.0
NEAR_GOAL_W = 40.0  # a piece two steps from the far row is a threat
SAFE_W = 2.0  # a piece that has a friendly piece diagonally behind it


def _cells(mask: int) -> list[int]:
    out = []
    while mask:
        low = mask & -mask
        out.append(low.bit_length() - 1)
        mask ^= low
    return out


@register_game
class Breakthrough(Game):
    id = "breakthrough"
    rules_version = 1

    def __init__(self, size: int = 8) -> None:
        if not MIN_SIZE <= size <= MAX_SIZE:
            raise GameError(f"breakthrough size must be in {MIN_SIZE}..{MAX_SIZE}")
        self.size = size
        n = size
        self._rows = [sum(1 << (r * n + c) for c in range(n)) for r in range(n)]

    def params(self) -> JsonDict:
        return {"size": self.size}

    @classmethod
    def from_params(cls, params: Mapping[str, Any]) -> Breakthrough:
        if set(params) != {"size"}:
            raise GameError(f"breakthrough takes only 'size', got {sorted(params)}")
        return cls(check_int(params, "size", MIN_SIZE, MAX_SIZE))

    # -- rules ------------------------------------------------------------------------------
    def initial(self) -> State:
        n = self.size
        return (
            self._rows[0] | self._rows[1],
            self._rows[n - 2] | self._rows[n - 1],
            0,
            0,
            NO_WINNER,
        )

    def to_move(self, s: State) -> int:
        return s[2]

    def plies(self, s: State) -> int:
        return s[3]

    def max_plies(self) -> int:
        return 4 * self.size * self.size  # generous: each piece makes at most n forward steps

    def _moves(self, mine: int, theirs: int, p: int) -> list[tuple[int, int]]:
        n = self.size
        step = n if p == 0 else -n
        out = []
        for f in _cells(mine):
            col = f % n
            to = f + step
            if not 0 <= to < n * n:
                continue
            for dc in (0, -1, 1):
                c = col + dc
                if not 0 <= c < n:
                    continue
                t = to + dc
                bit = 1 << t
                if mine & bit:
                    continue
                if dc == 0 and theirs & bit:
                    continue  # the straight step never captures
                out.append((f, t))
        return out

    def legal(self, s: State) -> list[Move]:
        if s[2] == OVER:
            return []
        p = s[2]
        mine, theirs = (s[0], s[1]) if p == 0 else (s[1], s[0])
        return list(self._moves(mine, theirs, p))

    def apply(self, s: State, m: Move) -> State:
        n = self.size
        p = s[2]
        f, t = m  # type: ignore[misc]
        mine, theirs = (s[0], s[1]) if p == 0 else (s[1], s[0])
        mine = (mine & ~(1 << f)) | (1 << t)
        theirs &= ~(1 << t)
        b0, b1 = (mine, theirs) if p == 0 else (theirs, mine)
        plies = s[3] + 1
        reached = (t // n == n - 1) if p == 0 else (t // n == 0)
        if reached or not theirs or not self._moves(theirs, mine, 1 - p):
            return (b0, b1, OVER, plies, p)
        return (b0, b1, 1 - p, plies, NO_WINNER)

    def result(self, s: State) -> float:
        return WIN if s[4] == 0 else LOSS

    # -- evaluation ---------------------------------------------------------------------------
    def material(self, s: State, p: int) -> float:
        mine, theirs = (s[0], s[1]) if p == 0 else (s[1], s[0])
        return float(mine.bit_count() - theirs.bit_count())

    def _side_score(self, bits: int, q: int) -> float:
        n = self.size
        total = 0.0
        for cell in _cells(bits):
            r = cell // n
            ahead = r if q == 0 else n - 1 - r  # rows advanced
            total += PIECE_W + ADVANCE_W * ahead
            if ahead >= n - 3:
                total += NEAR_GOAL_W
            c = cell % n
            back = cell - n if q == 0 else cell + n
            for dc in (-1, 1):
                if 0 <= c + dc < n and 0 <= back + dc < n * n and bits & (1 << (back + dc)):
                    total += SAFE_W
        return total

    def heuristic(self, s: State, p: int) -> float:
        if s[2] == OVER:
            return WIN_SCORE if s[4] == p else -WIN_SCORE
        mine, theirs = (s[0], s[1]) if p == 0 else (s[1], s[0])
        return self._side_score(mine, p) - self._side_score(theirs, 1 - p)

    # -- wire format ----------------------------------------------------------------------------
    def encode_state(self, s: State) -> JsonDict:
        n = self.size
        rows = []
        for r in range(n):
            row = []
            for c in range(n):
                bit = 1 << (r * n + c)
                row.append("0" if s[0] & bit else "1" if s[1] & bit else ".")
            rows.append("".join(row))
        return {"board": rows, "to_move": s[2], "plies": s[3]}

    def rules_md(self) -> str:
        n = self.size
        return (
            f"**Breakthrough** on a {n}x{n} board. Player 0 (`0`) starts with a piece on every "
            "square of "
            f"rows 0 and 1 and moves toward row {n - 1}; player 1 (`1`) starts on rows {n - 2} and "
            f"{n - 1} and moves toward row 0. Player 0 moves first.\n\n"
            f"- Squares are numbered `row * {n} + col`, row 0 at the top. A move is a pair "
            "`[from, to]` of square numbers.\n"
            "- A piece moves one row forward (toward the far row): straight ahead onto an empty "
            "square, or diagonally forward onto an empty square or onto an opponent piece, which "
            "is "
            "captured (removed). A straight move never captures, and a piece cannot move onto a "
            "square holding a piece of its own side.\n"
            "- A player wins by moving a piece onto the far row, or by capturing the opponent's "
            "last "
            "piece. A player who has no legal move on their turn loses. There are no draws.\n\n"
            'State on the wire: `{"board": [<row string>, ...], "to_move": 0|1, "plies": N}` '
            "with `0`, `1` for the pieces of player 0 and 1, and `.` for empty, row 0 first."
        )

    # -- ladder -----------------------------------------------------------------------------
    def ladder(self) -> Sequence[BotSpec]:
        return (("random",), ("greedy",), ("ab", 1), ("ab", 2), ("ab", 3), ("ab", 4))
