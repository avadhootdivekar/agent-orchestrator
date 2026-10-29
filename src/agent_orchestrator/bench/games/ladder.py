"""The frozen ladder of a game: version stamp and stored ratings (design §6).

``ratings/<game key>.json`` holds the offline round-robin ratings of the reference bots. The
**ladder version** hashes everything that decides what a rung is (game rules version and
parameters, bot list, bot code version); the grader refuses ratings whose stored version differs
from the live one, so a changed bot or rule cannot be graded against stale numbers.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .base import Game, GameError
from .bots import BOTS_VERSION, spec_label

RATINGS_DIR = Path(__file__).resolve().parent / "ratings"
VERSION_HEX = 12


def ladder_version(game: Game) -> str:
    body = {
        "game": game.wire(),
        "rules_version": game.rules_version,
        "ladder": [list(spec) for spec in game.ladder()],
        "bots_version": BOTS_VERSION,
    }
    text = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()[:VERSION_HEX]


@dataclass(frozen=True)
class FrozenLadder:
    game_key: str
    version: str
    labels: list[str]
    ratings: list[float]
    games_per_pair: int

    def par_rating(self, game: Game) -> float:
        return self.ratings[game.par_rung()]


def ratings_path(game: Game, root: Path | None = None) -> Path:
    return (root or RATINGS_DIR) / f"{game.key}.json"


def load_ladder(game: Game, root: Path | None = None) -> FrozenLadder:
    path = ratings_path(game, root)
    if not path.is_file():
        raise GameError(
            f"no frozen ratings for {game.key}: run `ao-bench arena-rate --game '{game.key}'`"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        version, ratings = data["version"], [float(r) for r in data["ratings"]]
        pairs = int(data["games_per_pair"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise GameError(f"unreadable ratings file {path}: {exc}") from exc
    live = ladder_version(game)
    if version != live:
        raise GameError(
            f"stale ratings for {game.key}: file has ladder version {version}, the live ladder is "
            f"{live}; re-rate with `ao-bench arena-rate --game '{game.key}'`"
        )
    labels = [spec_label(spec) for spec in game.ladder()]
    if len(ratings) != len(labels):
        raise GameError(f"ratings file {path} has {len(ratings)} rungs, ladder has {len(labels)}")
    return FrozenLadder(game.key, version, labels, ratings, pairs)


def write_ladder(
    game: Game, ratings: list[float], games_per_pair: int, root: Path | None = None
) -> Path:
    path = ratings_path(game, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "game": game.wire(),
        "version": ladder_version(game),
        "labels": [spec_label(spec) for spec in game.ladder()],
        "ratings": [round(r, 1) for r in ratings],
        "games_per_pair": games_per_pair,
    }
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return path
