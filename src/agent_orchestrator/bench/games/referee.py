"""Referee and match runner for `game-arena` (design §3, §4).

A :class:`Player` is anything that can pick a move: a reference bot in-process
(:class:`BotPlayer`) or the subject's process (the grader's adapter). The referee enforces
legality: an illegal, malformed, late or missing move ends the game as a forfeit for that player.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Protocol

from .base import DRAW, LOSS, WIN, Game, GameError, Move, State
from .bots import Bot

OPENING_TRIES = 20
SEED_MASK = 0xFFFFFFFF


class Forfeit(Exception):
    """Raised by a player that cannot produce a legal move in time."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Player(Protocol):
    def start(self, game: Game, you: int, ms_per_move: int, seed: int) -> None: ...

    def choose(self, game: Game, state: State, legal: list[Move], history: list[Move]) -> Move: ...

    def finish(self) -> None: ...


class BotPlayer:
    """A reference bot as a player; never forfeits (its move is checked all the same)."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    def start(self, game: Game, you: int, ms_per_move: int, seed: int) -> None:
        self._bot.new_game(game, you, seed)

    def choose(self, game: Game, state: State, legal: list[Move], history: list[Move]) -> Move:
        return self._bot.move(game, state, legal)

    def finish(self) -> None:
        return None


@dataclass
class GameRecord:
    """One finished game. ``result`` is for player 0; ``forfeit`` is the side that forfeited."""

    result: float
    plies: int
    moves: list[Move] = field(default_factory=list)
    forfeit: int | None = None
    reason: str = ""


def make_opening(game: Game, seed: int, plies: int) -> list[Move]:
    """``plies`` random legal moves (fewer if the game would end); never a finished game."""
    for attempt in range(OPENING_TRIES):
        rng = random.Random((seed * 1_000_003 + attempt) & SEED_MASK)
        s = game.initial()
        moves: list[Move] = []
        for _ in range(plies):
            legal = game.legal(s)
            if not legal:
                break
            m = rng.choice(legal)
            moves.append(m)
            s = game.apply(s, m)
        if not game.terminal(s):
            return moves
    raise GameError(f"no non-terminal {plies}-ply opening found for {game.key} (seed {seed})")


def replay(game: Game, moves: list[Move]) -> State:
    s = game.initial()
    for m in moves:
        s = game.apply(s, m)
    return s


def play_game(
    game: Game,
    players: tuple[Player, Player],
    opening: list[Move],
    ms_per_move: int,
    seed: int,
) -> GameRecord:
    """Play from the end of ``opening``; ``players[i]`` plays side ``i``."""
    for i, p in enumerate(players):
        p.start(game, i, ms_per_move, seed + i)
    s = replay(game, opening)
    history = list(opening)
    cap = game.max_plies() + 2
    try:
        while not game.terminal(s):
            if len(history) > cap:  # a rules bug, not a player's fault: call it a draw
                return GameRecord(DRAW, len(history), history, None, "ply cap")
            side = game.to_move(s)
            legal = game.legal(s)
            try:
                move = players[side].choose(game, s, legal, history)
            except Forfeit as exc:
                return _forfeit(side, history, exc.reason)
            if move not in legal:
                return _forfeit(side, history, f"illegal move {move!r}")
            s = game.apply(s, move)
            history.append(move)
        return GameRecord(game.result(s), len(history), history)
    finally:
        for p in players:
            p.finish()


def _forfeit(side: int, history: list[Move], reason: str) -> GameRecord:
    return GameRecord(LOSS if side == 0 else WIN, len(history), history, side, reason)


def play_pair(
    game: Game,
    a: Player,
    b: Player,
    opening: list[Move],
    ms_per_move: int,
    seed: int,
) -> tuple[GameRecord, GameRecord]:
    """The same opening twice, sides swapped: ``a`` as player 0, then ``a`` as player 1."""
    return (
        play_game(game, (a, b), opening, ms_per_move, seed),
        play_game(game, (b, a), opening, ms_per_move, seed),
    )


def score_for_a(first: GameRecord, second: GameRecord) -> tuple[float, float]:
    """Score of ``a`` (1 / 0.5 / 0 per game) over the two games of a pair, and the games played."""
    return first.result + (1.0 - second.result), 2.0
