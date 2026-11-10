"""`game-scenarios` families (T-Ga4Rna; design `docs-md/benchmark-game-arena-design.md` §11).

Same bot task as `game-arena` (same protocol, games and pools), graded differently: the bot is
asked for one move in each of N fixed positions and every answer is scored against a hidden
reference search. No match noise and no ladder to outgrow, so it is the tuning signal; the
`game-arena` rating stays the final strength check. Two families, differing only in the pool:

- ``game-scenarios``: the dev pool (Othello, Connect-K);
- ``game-scenarios-heldout``: the held-out pool (Breakthrough), final comparisons only.

Difficulty axes: ``entities`` -> the game configuration; ``concurrency`` -> number of scenarios
(50, 100, 200, 400); ``scale`` -> milliseconds per move (1000, 500, 250, 100); ``traps`` unused.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import Any, ClassVar

from ..games import GameError
from ..games.base import Game
from ..games.bots import spec_label
from ..games.pools import DEV_POOL, HELDOUT_POOL, Wire, pick_game
from ..games.scenarios import make_scenarios, random_quality
from .base import Difficulty, Family, Instance, register_family
from .refs import reference_files
from .skeleton import allow_list_text, resolve_allow, run_contract_text, skeleton_files

SCENARIOS_KIND = "scenarios"
REFERENCE_PROGRAM = "arena_server.py"
SCENARIOS_BY_LEVEL = (50, 100, 200, 400)
MS_BY_LEVEL = (1000, 500, 250, 100)
SOLVED_TOP1 = 0.5  # share of best moves a bot must find to count as solved
BUDGET_SECONDS = 1800
JsonDict = dict[str, Any]


def _json(value: Any) -> str:
    import json

    return json.dumps(value, separators=(", ", ": "))


def render_spec(game: Game, limits: JsonDict, language: str, allow: Sequence[str]) -> str:
    parts = [
        "# Game-playing bot: choose the best move",
        "",
        "Build a program that, given a position of the game in section 4, chooses a good move "
        "over stdin/stdout. It is scored on a large fixed set of positions: **the better the "
        "move you choose, the higher the score**, with no upper limit on how deep you may search "
        "within the time limit.",
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
        '- `{"op": "new", "game": {...}, "you": 0|1, "ms_per_move": N}` → `{"ok": true}`. '
        "Announces the next position; `game` names the game and its parameters (as in section "
        "4) and `you` is the side to move. The positions are independent and arrive one after "
        "another to the same process.",
        '- `{"op": "move", "state": {...}, "legal": [...], "history": [...]}` → '
        '`{"ok": true, "move": <one of legal>}`. `state` is the position (section 4), `legal` '
        "lists every legal move and `history` lists the moves that led to it from the initial "
        "position, oldest first. Reply with exactly one of the listed moves.",
        f"- **Time limit:** each `move` must be answered within **{limits['ms_per_move']} "
        "milliseconds** of the request (measured by the grader, with a little transport slack). "
        "A late, missing, malformed or illegal move, or a crash, scores 0 for that position; "
        "the process is restarted for the next one. A bot may use the whole limit.",
        "",
        "## 4. The game",
        "",
        game.rules_md(),
        "",
        f"The `game` object of `new` for this task is `{_json(game.wire())}`.",
        "",
        "## 5. Scoring",
        "",
        f"You are asked for a move in **{limits['scenarios']} positions**, reached by random "
        "play from the start (early, middle and late game). Every legal move of every position "
        "has been valued by an exact deep search; your move scores by its **rank among the "
        "legal moves**: a best move scores 1, the worst legal move 0, in between the share of "
        "the moves that are worse than a best move that yours beats.",
        "",
        "- Your result is the mean over all positions, rescaled so that a uniformly random mover "
        "scores 0; the share of positions where you found a best move is reported as well.",
        "- Searching deeper within the time limit is what raises the score; the search the "
        "positions were valued with is strong, so there is no easy way to reach the maximum.",
        "",
    ]
    return "\n".join(parts).rstrip() + "\n"


class _ScenariosBase(Family):
    contract = "jsonl"
    grader_type = SCENARIOS_KIND
    instruction_kind = "scenarios"
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
        count = SCENARIOS_BY_LEVEL[difficulty.concurrency]
        ms = MS_BY_LEVEL[difficulty.scale]
        try:
            scenarios = make_scenarios(game, seed, count)
        except GameError as exc:
            raise ValueError(f"{self.name}: {exc}") from exc
        limits: JsonDict = {
            "game": game.wire(),
            "ms_per_move": ms,
            "scenarios": count,
            "scenario_depth": game.scenario_depth,
            "reference_rung": game.reference_rung(),
            "reference_labels": [spec_label(s) for s in game.ladder()],
        }
        probes = {
            "version": "1.0",
            "kind": SCENARIOS_KIND,
            "contract": "jsonl",
            "game": game.wire(),
            "scenarios": [s.wire() for s in scenarios],
            "random_quality": round(random_quality(scenarios), 6),
            "ms_per_move": ms,
            "solved_top1": SOLVED_TOP1,
            "budget_seconds": BUDGET_SECONDS,
        }
        return Instance(
            family=self.name,
            seed=seed,
            difficulty=difficulty,
            language=language,
            contract="jsonl",
            entities=[],
            traps=["random_positions", "move_time_limit"],
            limits=limits,
            allow=allow_list,
            spec_md=render_spec(game, limits, language, allow_list),
            skeleton=skeleton_files("jsonl", language, allow_list, title="game-playing bot"),
            probes=probes,
            reference=reference_files(REFERENCE_PROGRAM),
        )


@register_family
class GameScenariosFamily(_ScenariosBase):
    name = "game-scenarios"
    pool = DEV_POOL


@register_family
class GameScenariosHeldoutFamily(_ScenariosBase):
    name = "game-scenarios-heldout"
    pool = HELDOUT_POOL


__all__ = ["GameScenariosFamily", "GameScenariosHeldoutFamily", "render_spec"]
