"""Games plugin for `game-arena` (T-Ga4Rna). Importing this package registers every game."""

from __future__ import annotations

from . import (
    breakthrough as _breakthrough,  # noqa: F401 -- registers "breakthrough"
)
from . import (
    connectk as _connectk,  # noqa: F401 -- registers "connectk"
)
from . import (
    othello as _othello,  # noqa: F401 -- registers "othello"
)
from .base import GAME_REGISTRY, Game, GameError, game_from_wire, register_game

__all__ = ["GAME_REGISTRY", "Game", "GameError", "game_from_wire", "register_game"]
