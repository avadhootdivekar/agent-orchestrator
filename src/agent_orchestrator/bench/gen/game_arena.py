"""`game-arena` families (T-Ga4Rna; design `docs-md/benchmark-game-arena-design.md`).

The subject writes a bot for a game described in `SPEC.md`; the `arena` grader plays it against a
ladder of reference bots with frozen Elo ratings and scores the maximum-likelihood rating
(unbounded). Two families share this code and differ only in the pool of games:

- ``game-arena``: the dev pool (Othello, Connect-K), for calibration and for tuning `ao`;
- ``game-arena-heldout``: the held-out pool (Breakthrough), for final comparisons only.

Difficulty axes: ``entities`` -> the game configuration (pool level, small boards first);
``concurrency`` -> games per rung (4, 8, 12, 20); ``scale`` -> milliseconds per move (1000, 500,
250, 100); ``traps`` is unused (the random opening is the anti-memorisation device).
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import Any, ClassVar

from ..games import GameError
from ..games.base import Game
from ..games.bots import spec_label
from ..games.ladder import ladder_version, load_ladder
from ..games.pools import DEV_POOL, HELDOUT_POOL, Wire, pick_game
from .base import Difficulty, Family, Instance, register_family
from .refs import reference_files
from .skeleton import allow_list_text, resolve_allow, run_contract_text, skeleton_files

ARENA_KIND = "arena"
REFERENCE_PROGRAM = "arena_server.py"
GAMES_BY_LEVEL = (4, 8, 12, 20)
MS_BY_LEVEL = (1000, 500, 250, 100)
STOP_SCORE = 0.2
BUDGET_SECONDS = 3600
JsonDict = dict[str, Any]


def render_spec(game: Game, limits: JsonDict, language: str, allow: Sequence[str]) -> str:
    labels = ", ".join(f"`{spec_label(s)}`" for s in game.ladder())
    parts = [
        "# Game-playing bot",
        "",
        "Build a program that plays the game in section 4 over stdin/stdout. It is rated by "
        "playing reference bots of increasing strength; **a stronger bot earns a higher "
        "rating**, with no upper limit.",
        "",
        "## 1. Run contract",
        "",
        run_contract_text("jsonl", language),
        "",
        "## 2. Allowed dependencies",
        "",
        allow_list_text(language, allow),
        "",
        "## 3. Protocol",
        "",
        "- Every request is one line holding a JSON object with a string field `op`; every "
        "response is one line holding a JSON object with a boolean field `ok`. Blank lines are "
        "skipped.",
        '- `{"op": "ping"}` → `{"ok": true}`.',
        '- `{"op": "new", "game": {...}, "you": 0|1, "ms_per_move": N}` → `{"ok": true}`. Starts a '
        "new game; `game` names the game and its parameters (as in section 4) and `you` is the "
        "side your bot plays. Games are played one after another by the same process.",
        '- `{"op": "move", "state": {...}, "legal": [...], "history": [...]}` → '
        '`{"ok": true, "move": <one of legal>}`. Your turn: `state` is the position (section 4), '
        "`legal` lists every legal move, and `history` lists every move played so far in this "
        "game (including the opening), oldest first. Reply with exactly one of the listed moves.",
        f"- **Time limit:** each `move` must be answered within **{limits['ms_per_move']} "
        "milliseconds** of the request (measured by the grader, with a little transport slack). "
        "A late, missing, malformed or illegal move, or a crash, loses that game; the process is "
        "restarted for the next one. A bot may use the whole limit.",
        "",
        "## 4. The game",
        "",
        game.rules_md(),
        "",
        f"The `game` object of `new` for this task is `{_json(game.wire())}`.",
        "",
        "## 5. Scoring",
        "",
        "Your bot plays a ladder of reference bots, weakest first: a random mover, a greedy "
        "one-move bot, then alpha-beta searchers that look further ahead at each step (the "
        f"stronger ones think much harder). There are {len(game.ladder())} rungs "
        f"({labels}).",
        "",
        f"- Against each rung your bot plays **{limits['games_per_rung']} games**, half as each "
        f"side, from random openings of {limits['opening_plies']} moves (both sides play each "
        "opening once).",
        "- Your result is an **Elo rating**: 400 points per factor of 10 in odds, the random bot "
        "is 0. It is the rating that best explains your results against the rungs; a draw counts "
        "half. Playing stops climbing once you win at most a fifth of the games against a rung.",
        "- The rating has no upper limit; the score is the rating relative to the strongest rung.",
        "",
    ]
    return "\n".join(parts).rstrip() + "\n"


def _json(value: Any) -> str:
    import json

    return json.dumps(value, separators=(", ", ": "))


class _ArenaBase(Family):
    contract = "jsonl"
    grader_type = ARENA_KIND
    instruction_kind = "arena"
    in_default_set = False
    pool: ClassVar[tuple[tuple[Wire, ...], ...]]

    def generate(
        self,
        seed: int,
        difficulty: Difficulty,
        language: str,
        allow: Sequence[str] | None = None,
    ) -> Instance:
        allow_list = resolve_allow(language, allow)
        rng = random.Random(seed)
        game = pick_game(self.pool, difficulty.entities, rng)
        try:
            frozen = load_ladder(game)
        except GameError as exc:
            raise ValueError(f"{self.name}: {exc}") from exc
        ms = MS_BY_LEVEL[difficulty.scale]
        games = GAMES_BY_LEVEL[difficulty.concurrency]
        par = frozen.par_rating(game)
        solved = frozen.ratings[game.solved_rung]
        limits: JsonDict = {
            "game": game.wire(),
            "ms_per_move": ms,
            "games_per_rung": games,
            "opening_plies": frozen.opening_plies,
            "par": par,
            "reference_rung": game.par_rung(),
        }
        probes = {
            "version": "1.0",
            "kind": ARENA_KIND,
            "contract": "jsonl",
            "game": game.wire(),
            "ladder_version": ladder_version(game),
            "rungs": [
                {"label": label, "rating": rating}
                for label, rating in zip(frozen.labels, frozen.ratings, strict=True)
            ],
            "par_rating": par,
            "solved_rating": solved,
            "games_per_rung": games,
            "ms_per_move": ms,
            "opening_plies": frozen.opening_plies,
            "opening_seed": seed,
            "stop_score": STOP_SCORE,
            "budget_seconds": BUDGET_SECONDS,
        }
        return Instance(
            family=self.name,
            seed=seed,
            difficulty=difficulty,
            language=language,
            contract="jsonl",
            entities=[],
            traps=["random_opening", "move_time_limit"],
            limits=limits,
            allow=allow_list,
            spec_md=render_spec(game, limits, language, allow_list),
            skeleton=skeleton_files("jsonl", language, allow_list, title="game-playing bot"),
            probes=probes,
            reference=reference_files(REFERENCE_PROGRAM),
        )


@register_family
class GameArenaFamily(_ArenaBase):
    name = "game-arena"
    pool = DEV_POOL


@register_family
class GameArenaHeldoutFamily(_ArenaBase):
    name = "game-arena-heldout"
    pool = HELDOUT_POOL


__all__ = ["GameArenaFamily", "GameArenaHeldoutFamily", "render_spec"]
