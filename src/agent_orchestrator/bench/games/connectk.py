"""Connect-K on a ``width x height`` board: ``k`` in a row (any of 4 directions) wins.

``gravity=1``: a move is a column (the disc falls to the lowest empty cell), as in Connect Four;
``gravity=0``: a move is any empty cell (an m,n,k-game). The parameters are drawn per task, so a
memorised engine for the standard game does not apply.

State: ``(cells, to_move, plies, winner)`` with ``cells`` a tuple (row-major, row 0 at the top,
0 empty / 1 player 0 / 2 player 1) and ``winner`` -1 until a line is made.
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

MIN_DIM, MAX_DIM = 3, 10
MIN_K, MAX_K = 3, 6
OVER = -1
NO_WINNER = -1
WIN_SCORE = 1_000_000.0
LINE_BASE = 4.0  # a window holding c of my discs (and none of theirs) is worth LINE_BASE ** c
CENTRE_W = 2.0
DIRS = ((0, 1), (1, 0), (1, 1), (1, -1))


@register_game
class ConnectK(Game):
    id = "connectk"
    rules_version = 1

    def __init__(self, width: int = 7, height: int = 6, k: int = 4, gravity: int = 1) -> None:
        if not (MIN_DIM <= width <= MAX_DIM and MIN_DIM <= height <= MAX_DIM):
            raise GameError(f"connectk width/height must be in {MIN_DIM}..{MAX_DIM}")
        if not MIN_K <= k <= min(MAX_K, max(width, height)):
            raise GameError(f"connectk k must be in {MIN_K}..{min(MAX_K, max(width, height))}")
        if gravity not in (0, 1):
            raise GameError("connectk gravity must be 0 or 1")
        self.width, self.height, self.k, self.gravity = width, height, k, gravity
        self._windows = self._make_windows()
        cx, cy = (width - 1) / 2, (height - 1) / 2
        # Centre-out move order: good for alpha-beta, deterministic.
        self._order = sorted(
            range(width * height),
            key=lambda i: (abs(i % width - cx) + abs(i // width - cy), i),
        )
        self._col_order = sorted(range(width), key=lambda c: (abs(c - cx), c))
        self._centre = [CENTRE_W - (abs(i % width - cx) + abs(i // width - cy)) * 0.3
                        for i in range(width * height)]  # fmt: skip

    def params(self) -> JsonDict:
        return {"width": self.width, "height": self.height, "k": self.k, "gravity": self.gravity}

    @classmethod
    def from_params(cls, params: Mapping[str, Any]) -> ConnectK:
        if set(params) != {"width", "height", "k", "gravity"}:
            raise GameError(f"connectk takes width, height, k, gravity; got {sorted(params)}")
        return cls(
            check_int(params, "width", MIN_DIM, MAX_DIM),
            check_int(params, "height", MIN_DIM, MAX_DIM),
            check_int(params, "k", MIN_K, MAX_K),
            check_int(params, "gravity", 0, 1),
        )

    def _make_windows(self) -> list[tuple[int, ...]]:
        w, h, k = self.width, self.height, self.k
        out = []
        for r in range(h):
            for c in range(w):
                for dr, dc in DIRS:
                    er, ec = r + dr * (k - 1), c + dc * (k - 1)
                    if 0 <= er < h and 0 <= ec < w:
                        out.append(tuple((r + dr * i) * w + c + dc * i for i in range(k)))
        return out

    # -- rules ------------------------------------------------------------------------------
    def initial(self) -> State:
        return ((0,) * (self.width * self.height), 0, 0, NO_WINNER)

    def to_move(self, s: State) -> int:
        return s[1]

    def plies(self, s: State) -> int:
        return s[2]

    def max_plies(self) -> int:
        return self.width * self.height

    def legal(self, s: State) -> list[Move]:
        if s[1] == OVER:
            return []
        cells = s[0]
        if self.gravity:
            return [c for c in self._col_order if cells[c] == 0]
        return [i for i in self._order if cells[i] == 0]

    def _cell_of(self, cells: tuple[int, ...], m: int) -> int:
        if not self.gravity:
            return m
        w = self.width
        r = self.height - 1
        while cells[r * w + m]:
            r -= 1
        return r * w + m

    def _makes_line(self, cells: list[int], i: int, who: int) -> bool:
        w, h, k = self.width, self.height, self.k
        r, c = divmod(i, w)
        for dr, dc in DIRS:
            n = 1
            for sign in (1, -1):
                rr, cc = r + sign * dr, c + sign * dc
                while 0 <= rr < h and 0 <= cc < w and cells[rr * w + cc] == who:
                    n += 1
                    rr, cc = rr + sign * dr, cc + sign * dc
            if n >= k:
                return True
        return False

    def apply(self, s: State, m: Move) -> State:
        cells = list(s[0])
        p = s[1]
        i = self._cell_of(s[0], int(m))  # type: ignore[arg-type]
        cells[i] = p + 1
        plies = s[2] + 1
        if self._makes_line(cells, i, p + 1):
            return (tuple(cells), OVER, plies, p)
        if plies == self.width * self.height:
            return (tuple(cells), OVER, plies, NO_WINNER)
        return (tuple(cells), 1 - p, plies, NO_WINNER)

    def result(self, s: State) -> float:
        return DRAW if s[3] == NO_WINNER else WIN if s[3] == 0 else LOSS

    # -- evaluation ---------------------------------------------------------------------------
    def heuristic(self, s: State, p: int) -> float:
        if s[1] == OVER:
            return 0.0 if s[3] == NO_WINNER else WIN_SCORE if s[3] == p else -WIN_SCORE
        cells = s[0]
        me, you = p + 1, 2 - p
        total = 0.0
        for win in self._windows:
            mine = theirs = 0
            for i in win:
                v = cells[i]
                if v == me:
                    mine += 1
                elif v:
                    theirs += 1
            if mine and not theirs:
                total += LINE_BASE**mine
            elif theirs and not mine:
                total -= LINE_BASE**theirs
        for i, v in enumerate(cells):
            if v == me:
                total += self._centre[i]
            elif v == you:
                total -= self._centre[i]
        return total

    # -- wire format ----------------------------------------------------------------------------
    def encode_state(self, s: State) -> JsonDict:
        w = self.width
        sym = ".XO"
        rows = ["".join(sym[v] for v in s[0][r * w : (r + 1) * w]) for r in range(self.height)]
        return {"board": rows, "to_move": s[1], "plies": s[2]}

    def rules_md(self) -> str:
        w, h, k = self.width, self.height, self.k
        if self.gravity:
            move = (
                f"A move is a column number `0..{w - 1}` (column 0 at the left). The disc falls to "
                "the lowest empty cell of that column; a full column is illegal."
            )
        else:
            move = (
                f"A move is a cell number `row * {w} + col` (row 0 at the top, column 0 at the "
                "left) of any empty cell."
            )
        return (
            f"**Connect-{k}** on a board {w} columns wide and {h} rows high. Player 0 plays `X` "
            "and "
            "moves first; player 1 plays `O`.\n\n"
            f"- {move}\n"
            f"- A player who makes a line of at least {k} of their own discs in a row "
            "(horizontally, "
            "vertically or diagonally) with the disc just placed wins immediately.\n"
            "- If the board is full and nobody has won, the game is a draw.\n\n"
            'State on the wire: `{"board": [<row string>, ...], "to_move": 0|1, "plies": N}` '
            "with `X`, `O` and `.` (empty), row 0 (the top) first."
        )

    # -- ladder -----------------------------------------------------------------------------
    def ladder(self) -> Sequence[BotSpec]:
        return (("random",), ("greedy",), ("ab", 1), ("ab", 2), ("ab", 4), ("ab", 6), ("ab", 8))
