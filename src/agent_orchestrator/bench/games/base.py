"""Games plugin interface for `game-arena` (T-Ga4Rna, `docs-md/benchmark-game-arena-design.md` §2).

A game is a :class:`Game` subclass registered with :func:`register_game`. States are immutable
and hashable; moves are ints or tuples of ints (``list`` of ints on the wire). Passes are handled
inside :meth:`Game.apply`, so ``legal`` is never empty for a non-terminal state and no bot is
ever asked to pass. Everything else (referee, reference bots, rating, grader, family) is generic
and knows nothing about a particular game.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar, Union

State = Any  # immutable, hashable, game specific
Move = Union[int, tuple[int, ...]]  # noqa: UP007 -- a runtime alias, kept explicit
JsonDict = dict[str, Any]

# Result values (always from player 0's point of view).
WIN = 1.0
DRAW = 0.5
LOSS = 0.0
# A "ply cap" no real game reaches; it only stops a runaway game from a buggy bot or rule.
PLY_CAP_FACTOR = 4

BotSpec = tuple  # ("random",) | ("greedy",) | ("ab", depth) | ("mcts", playouts)


class GameError(ValueError):
    """A malformed move, unknown game or invalid game parameters."""


class Game(ABC):
    """One game class with fixed parameters (e.g. Othello 8x8, Connect-K 7x6 k=4 gravity)."""

    id: ClassVar[str]
    # Bumped when the rules or the evaluation change; part of the ladder version (design §6).
    rules_version: ClassVar[int] = 1

    # -- identity --------------------------------------------------------------------------
    @abstractmethod
    def params(self) -> JsonDict:
        """JSON-able parameters (without ``id``) that reconstruct this game."""

    @classmethod
    @abstractmethod
    def from_params(cls, params: Mapping[str, Any]) -> Game:
        """Build from :meth:`params` (validated: raise :class:`GameError`)."""

    @property
    def key(self) -> str:
        """Stable id of the game class and its parameters, e.g. ``othello-size8``."""
        parts = "-".join(f"{k}{v}" for k, v in sorted(self.params().items()))
        return f"{self.id}-{parts}" if parts else self.id

    def wire(self) -> JsonDict:
        """The ``game`` object of the ``new`` request."""
        return {"id": self.id, **self.params()}

    # -- rules ------------------------------------------------------------------------------
    @abstractmethod
    def initial(self) -> State: ...

    @abstractmethod
    def to_move(self, s: State) -> int:
        """0 or 1 for a non-terminal state; -1 for a terminal one."""

    @abstractmethod
    def legal(self, s: State) -> list[Move]:
        """Legal moves in a deterministic order; non-empty unless the state is terminal."""

    @abstractmethod
    def apply(self, s: State, m: Move) -> State:
        """The state after ``m`` (``m`` must be legal); a player without moves is skipped."""

    def terminal(self, s: State) -> bool:
        return self.to_move(s) < 0

    @abstractmethod
    def result(self, s: State) -> float:
        """Result for player 0 of a terminal state: ``WIN``, ``DRAW`` or ``LOSS``."""

    @abstractmethod
    def plies(self, s: State) -> int:
        """Moves played so far."""

    @abstractmethod
    def max_plies(self) -> int:
        """An upper bound on the length of any game (the referee's runaway guard)."""

    # -- evaluation (reference bots only) -------------------------------------------------
    def material(self, s: State, p: int) -> float:
        """Cheap material count for player ``p`` (greedy bot); default: none."""
        return 0.0

    @abstractmethod
    def heuristic(self, s: State, p: int) -> float:
        """Static evaluation for player ``p`` (alpha-beta bots); large = good, symmetric."""

    # -- wire format ------------------------------------------------------------------------
    @abstractmethod
    def encode_state(self, s: State) -> JsonDict: ...

    def encode_move(self, m: Move) -> Any:
        return list(m) if isinstance(m, tuple) else m

    def decode_move(self, x: Any) -> Move:
        """JSON -> move; strict (no bool / float), the caller checks legality."""
        if type(x) is int:
            return x
        if isinstance(x, list) and x and all(type(i) is int for i in x):
            return tuple(x)
        raise GameError(f"malformed move {x!r}")

    @abstractmethod
    def rules_md(self) -> str:
        """Exact rules and the wire format of the state and of a move, for ``SPEC.md``."""

    # -- ladder -----------------------------------------------------------------------------
    @abstractmethod
    def ladder(self) -> Sequence[BotSpec]:
        """Reference bots, weakest first; rung 0 is the random bot."""

    # Rung indices used by the grader: `par` maps to score 1.0; `solved` needs at least this rung.
    solved_rung: ClassVar[int] = 2

    def par_rung(self) -> int:
        return len(self.ladder()) - 1

    def balance_bounds(self) -> tuple[float, float]:
        """Accepted first-player score of mirror matches of a mid-ladder bot (design §9)."""
        return (0.35, 0.65)


GAME_REGISTRY: dict[str, type[Game]] = {}


def register_game(cls: type[Game]) -> type[Game]:
    """Class decorator: register *cls* under ``cls.id`` (a duplicate is a programming error)."""
    if cls.id in GAME_REGISTRY:
        raise ValueError(f"game already registered: {cls.id!r}")
    GAME_REGISTRY[cls.id] = cls
    return cls


def game_from_wire(spec: Mapping[str, Any]) -> Game:
    """``{"id": ..., <params>}`` -> game; the inverse of :meth:`Game.wire`."""
    gid = spec.get("id")
    cls = GAME_REGISTRY.get(gid) if isinstance(gid, str) else None
    if cls is None:
        raise GameError(f"unknown game {gid!r}; known: {sorted(GAME_REGISTRY)}")
    return cls.from_params({k: v for k, v in spec.items() if k != "id"})


def check_int(params: Mapping[str, Any], name: str, lo: int, hi: int) -> int:
    v = params.get(name)
    if type(v) is not int or not lo <= v <= hi:
        raise GameError(f"game parameter {name} must be an integer in {lo}..{hi}, got {v!r}")
    return v
