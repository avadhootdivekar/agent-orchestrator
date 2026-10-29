"""Game-agnostic reference bots for the `game-arena` ladder (design §2).

Every bot is deterministic given its seed: it never reads the clock (strength is a depth or a
playout budget, not a time budget), and ties are broken by a seeded shuffle of the root moves, so
two runs of a rating round robin or of a grade give the same games.
"""

from __future__ import annotations

import math
import random
from typing import Protocol

from .base import DRAW, WIN, BotSpec, Game, Move, State

# Bump when a bot's behaviour changes: part of the ladder version, so stale ratings are refused.
BOTS_VERSION = 1
TERMINAL_SCORE = 1_000_000.0
INF = float("inf")
UCT_C = 1.4
SEED_MASK = 0xFFFFFFFF


class Bot(Protocol):
    def new_game(self, game: Game, you: int, seed: int) -> None: ...

    def move(self, game: Game, state: State, legal: list[Move]) -> Move: ...


def terminal_value(game: Game, s: State, root: int) -> float:
    """Exact outcome of a terminal state for ``root``, on the evaluation scale."""
    r = game.result(s)
    if r == DRAW:
        return 0.0
    return TERMINAL_SCORE if (r == WIN) == (root == 0) else -TERMINAL_SCORE


class RandomBot:
    def new_game(self, game: Game, you: int, seed: int) -> None:
        self._rng = random.Random(seed & SEED_MASK)

    def move(self, game: Game, state: State, legal: list[Move]) -> Move:
        return self._rng.choice(legal)


class AlphaBetaBot:
    """Fixed-depth alpha-beta. ``greedy`` (depth 1, material) and the ``ab`` rungs (heuristic)."""

    def __init__(self, depth: int, evaluator: str) -> None:
        self.depth = depth
        self.evaluator = evaluator

    def new_game(self, game: Game, you: int, seed: int) -> None:
        self._rng = random.Random((seed ^ 0xAB) & SEED_MASK)
        self._you = you

    def _eval(self, game: Game, s: State) -> float:
        if self.evaluator == "material":
            return game.material(s, self._you)
        return game.heuristic(s, self._you)

    def _search(self, game: Game, s: State, depth: int, alpha: float, beta: float) -> float:
        if game.terminal(s):
            return terminal_value(game, s, self._you)
        if depth == 0:
            return self._eval(game, s)
        if game.to_move(s) == self._you:
            best = -INF
            for m in game.legal(s):
                best = max(best, self._search(game, game.apply(s, m), depth - 1, alpha, beta))
                alpha = max(alpha, best)
                if alpha >= beta:
                    break
            return best
        best = INF
        for m in game.legal(s):
            best = min(best, self._search(game, game.apply(s, m), depth - 1, alpha, beta))
            beta = min(beta, best)
            if alpha >= beta:
                break
        return best

    def move(self, game: Game, state: State, legal: list[Move]) -> Move:
        order = list(legal)
        self._rng.shuffle(order)  # the tie-break: the first of the best moves in this order
        best_move, best = order[0], -INF
        for m in order:
            v = self._search(game, game.apply(state, m), self.depth - 1, best, INF)
            if v > best:
                best_move, best = m, v
        return best_move


class _Node:
    __slots__ = ("children", "move", "parent", "player", "untried", "visits", "wins")

    def __init__(self, parent: _Node | None, move: Move | None, player: int, untried: list) -> None:
        self.parent = parent
        self.move = move
        self.player = player  # who made `move` (the side to move at the parent)
        self.untried = untried
        self.children: list[_Node] = []
        self.visits = 0
        self.wins = 0.0


class MctsBot:
    """UCT with uniformly random playouts; ``playouts`` per move (a budget, not a time)."""

    def __init__(self, playouts: int) -> None:
        self.playouts = playouts

    def new_game(self, game: Game, you: int, seed: int) -> None:
        self._rng = random.Random((seed ^ 0x3C) & SEED_MASK)
        self._you = you

    def _playout(self, game: Game, s: State) -> float:
        rng = self._rng
        while not game.terminal(s):
            s = game.apply(s, rng.choice(game.legal(s)))
        return game.result(s)

    def move(self, game: Game, state: State, legal: list[Move]) -> Move:
        order = list(legal)
        self._rng.shuffle(order)
        root = _Node(None, None, 1 - game.to_move(state), list(order))  # `order` is kept for ties
        for _ in range(self.playouts):
            node, s = root, state
            while not node.untried and node.children:  # select
                log_n = math.log(node.visits)
                node = max(
                    node.children,
                    key=lambda c: c.wins / c.visits + UCT_C * math.sqrt(log_n / c.visits),
                )
                s = game.apply(s, node.move)  # type: ignore[arg-type]
            if node.untried and not game.terminal(s):  # expand
                m = node.untried.pop()
                mover = game.to_move(s)
                s = game.apply(s, m)
                child_untried = [] if game.terminal(s) else list(game.legal(s))
                child = _Node(node, m, mover, child_untried)
                node.children.append(child)
                node = child
            r0 = self._playout(game, s)  # result for player 0
            while node is not None:  # backpropagate: wins for the side that moved into the node
                node.visits += 1
                node.wins += r0 if node.player == 0 else 1.0 - r0
                node = node.parent  # type: ignore[assignment]
        best = max(root.children, key=lambda c: (c.visits, -order.index(c.move)))  # type: ignore[arg-type]
        return best.move  # type: ignore[return-value]


def make_bot(spec: BotSpec) -> Bot:
    kind = spec[0]
    if kind == "random":
        return RandomBot()
    if kind == "greedy":
        return AlphaBetaBot(1, "material")
    if kind == "ab":
        return AlphaBetaBot(int(spec[1]), "heuristic")
    if kind == "mcts":
        return MctsBot(int(spec[1]))
    raise ValueError(f"unknown reference bot {spec!r}")


def spec_label(spec: BotSpec) -> str:
    return spec[0] if len(spec) == 1 else f"{spec[0]}{spec[1]}"
