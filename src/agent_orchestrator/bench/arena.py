"""`arena` grader (T-Ga4Rna, design `docs-md/benchmark-game-arena-design.md` §3-§5).

Grades a game-playing bot by playing it against a ladder of reference bots with frozen Elo
ratings, and scores the maximum-likelihood rating. The reference bots run in this process (they
are deterministic and never read the clock); the subject's artefact runs in the sandbox as a
jsonl process, one process reused across games.

``grading_dir`` (written by the `game-arena` families)::

    instance.json   language, limits
    probes.json     {"kind": "arena", "game": <wire>, "ladder_version", "rungs": [{"label",
                     "rating"}], "par_rating", "solved_rating", "games_per_rung", "ms_per_move",
                     "opening_plies", "opening_seed", "stop_score", "budget_seconds"}
    reference/      run.sh + server.py: a reference bot as an artefact (self-check)

Rungs are played weakest first; after a rung the subject scored ``stop_score`` or less on, higher
rungs are not played. An illegal, malformed, late or missing move, or a crash, forfeits that game
(the artefact is restarted for the next one).

Result: ``score = min(1, max(0, rating) / par_rating)``, ``solved = rating >= solved_rating``;
the raw Elo rating (unbounded) and its 95% interval are in ``detail`` (``points`` = the rating).
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from .blackbox import (
    DEFAULT_STARTUP_TIMEOUT,
    INSTANCE_FILE,
    RAW_TAIL_MAX_CHARS,
    Session,
)
from .errors import GraderError
from .games import GameError, game_from_wire
from .games.base import Game, Move, State
from .games.bots import make_bot
from .games.ladder import ladder_version
from .games.rating import rate_against
from .games.referee import BotPlayer, Forfeit, make_opening, play_pair, score_for_a
from .graders import Grader, GraderContext, GradeResult
from .objective import request_timeout
from .registries import register_grader
from .sandbox import ArtefactFailure, ArtefactTimeout, SandboxConfig, create_sandbox
from .spec import GraderConfig

ARENA_GRADER_TYPE = "arena"
PROBES_FILE = "probes.json"
NEW_GAME_SECONDS = 5.0  # the subject's budget to acknowledge `new` (start of a game)
SEED_STRIDE = 100_003
DEFAULT_BUDGET_SECONDS = 3600.0
DEFAULT_STOP_SCORE = 0.2
MAX_START_FAILURES = 2
MAX_REASONS = 5
REQUIRED_PROBE_KEYS = (
    "game",
    "ladder_version",
    "rungs",
    "par_rating",
    "solved_rating",
    "games_per_rung",
    "ms_per_move",
    "opening_plies",
    "opening_seed",
)


def _load_json(path: Path, what: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GraderError(f"arena grader: cannot read {what} {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise GraderError(f"arena grader: {what} {path} is not a JSON object")
    return data


def validate_probes(probes: dict[str, Any]) -> None:
    if probes.get("kind") != ARENA_GRADER_TYPE:
        raise GraderError("arena grader: probes.json kind must be 'arena'")
    missing = [k for k in REQUIRED_PROBE_KEYS if k not in probes]
    if missing:
        raise GraderError(f"arena grader: probes.json lacks {missing}")
    rungs = probes["rungs"]
    ok = isinstance(rungs, list) and len(rungs) >= 2
    ok = ok and all(isinstance(r, dict) and {"label", "rating"} <= set(r) for r in rungs)
    games = probes["games_per_rung"]
    if not ok or type(games) is not int or games < 2 or games % 2:
        raise GraderError("arena grader: bad rungs or games_per_rung (an even number >= 2)")
    if not probes["par_rating"] > 0:
        raise GraderError("arena grader: par_rating must be positive")


class ArtefactPlayer:
    """The subject's process as a :class:`~.games.referee.Player`."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._error: str | None = None
        self.start_failed = False  # the process could not be started for the last game
        self.move_ms: list[float] = []
        self._ms = 0

    def _fail(self, reason: str, *, restart: bool = True) -> None:
        self._error = reason[:300]
        if restart:
            self._session.close()  # a late answer would misalign the next request

    def start(self, game: Game, you: int, ms_per_move: int, seed: int) -> None:
        self._error = None
        self.start_failed = False
        try:
            transport = self._session.ensure(fresh=False)
        except ArtefactFailure as exc:
            self.start_failed = True
            self._fail(f"artefact did not start: {exc}", restart=False)
            return
        req = {"op": "new", "game": game.wire(), "you": you, "ms_per_move": ms_per_move}
        try:
            out = transport.send({"json": req}, request_timeout(NEW_GAME_SECONDS))
        except ArtefactTimeout:
            self._fail(f"no answer to 'new' within {NEW_GAME_SECONDS:g}s")
            return
        except ArtefactFailure as exc:
            self._fail(f"artefact failed: {exc}")
            return
        if not isinstance(out.body, dict) or out.body.get("ok") is not True:
            self._fail(f"'new' was not answered ok: {out.text[:120]!r}", restart=False)
        self._ms = ms_per_move

    def choose(self, game: Game, state: State, legal: list[Move], history: list[Move]) -> Move:
        if self._error:
            raise Forfeit(self._error)
        req = {
            "op": "move",
            "state": game.encode_state(state),
            "legal": [game.encode_move(m) for m in legal],
            "history": [game.encode_move(m) for m in history],
        }
        transport = self._session.transport
        if transport is None:
            raise Forfeit("artefact is not running")
        t0 = time.perf_counter()
        try:
            out = transport.send({"json": req}, request_timeout(self._ms / 1000.0))
        except ArtefactTimeout:
            self._fail(f"no move within {self._ms} ms")
            raise Forfeit(self._error or "timeout") from None
        except ArtefactFailure as exc:
            self._fail(f"artefact failed: {exc}")
            raise Forfeit(self._error or "failed") from None
        self.move_ms.append((time.perf_counter() - t0) * 1000.0)
        body = out.body
        if not isinstance(body, dict) or body.get("ok") is not True or "move" not in body:
            raise Forfeit(f"not an ok move: {out.text[:120]!r}")
        try:
            return game.decode_move(body["move"])
        except GameError as exc:
            raise Forfeit(str(exc)) from None

    def finish(self) -> None:
        return None


class ArenaGrader(Grader):
    """cfg (`GraderConfig` + `model_extra`): `sandbox` (SandboxConfig keys), `artefact_env`
    (extra environment for the artefact, e.g. to run a reference bot), `games_per_rung`,
    `ms_per_move`, `par_rating`, `solved_rating`, `stop_score`, `budget_seconds` (cfg beats
    probes.json)."""

    def _grade(self, cfg: GraderConfig, ctx: GraderContext) -> GradeResult:
        extra = dict(cfg.model_extra or {})
        gd = getattr(ctx, "grading_dir", None)
        if not gd:
            raise GraderError("arena grader needs a grading_dir")
        grading_dir = Path(gd)
        probes = _load_json(grading_dir / PROBES_FILE, "probes")
        validate_probes(probes)
        instance_path = grading_dir / INSTANCE_FILE
        instance = _load_json(instance_path, "instance") if instance_path.is_file() else {}
        try:
            game = game_from_wire(probes["game"])
        except GameError as exc:
            raise GraderError(f"arena grader: {exc}") from exc
        live = ladder_version(game)
        if probes["ladder_version"] != live:
            raise GraderError(
                f"arena grader: probes were built for ladder {probes['ladder_version']}, the "
                f"live ladder of {game.key} is {live}; regenerate the task"
            )
        specs = list(game.ladder())
        rungs = probes["rungs"]
        if len(rungs) != len(specs):
            raise GraderError("arena grader: probes rungs do not match the game's ladder")

        def knob(name: str, default: Any = None) -> Any:  # cfg beats probes.json
            return extra.get(name, probes.get(name, default))

        stop_score = float(knob("stop_score", DEFAULT_STOP_SCORE))
        budget = float(knob("budget_seconds", DEFAULT_BUDGET_SECONDS))
        games_per_rung = int(knob("games_per_rung"))
        ms_per_move = int(knob("ms_per_move"))
        par_rating = float(knob("par_rating"))
        solved_rating = float(knob("solved_rating"))
        plies = int(probes["opening_plies"])
        opening_seed = int(probes["opening_seed"])

        sandbox_raw = dict(extra.get("sandbox") or {})
        language = instance.get("language") or extra.get("language") or sandbox_raw.get("language")
        if not language:
            raise GraderError("arena grader: no language (instance.json / cfg.language)")
        sandbox_raw.setdefault("startup_timeout_seconds", DEFAULT_STARTUP_TIMEOUT)
        sandbox = create_sandbox(
            SandboxConfig.parse({**sandbox_raw, "language": language}), language
        )

        artefact_dir = Path(ctx.repo_dir)
        artefact_env = {str(k): str(v) for k, v in (extra.get("artefact_env") or {}).items()}
        work_root = Path(tempfile.mkdtemp(prefix="arena-", dir=grading_dir))
        session = Session(
            sandbox, "jsonl", artefact_dir, work_root, env_for=lambda _d: artefact_env
        )
        player = ArtefactPlayer(session)
        artefact_error: str | None = None
        if not (artefact_dir / "run.sh").is_file():
            artefact_error = "run.sh not found in the artefact directory"

        table: list[dict[str, Any]] = []
        played: list[tuple[float, float, float]] = []  # (rung rating, points, games)
        start_failures = 0
        stopped_reason: str | None = artefact_error
        t0 = time.perf_counter()
        try:
            for idx, (rung, spec) in enumerate(zip(rungs, specs, strict=True)):
                row: dict[str, Any] = {"rung": rung["label"], "rating": rung["rating"]}
                if stopped_reason is None and time.perf_counter() - t0 > budget:
                    stopped_reason = f"grading budget of {budget:g}s used up"
                if stopped_reason is not None:
                    table.append({**row, "played": False, "note": stopped_reason})
                    continue
                points = games = forfeits = 0.0
                reasons: list[str] = []
                for k in range(games_per_rung // 2):
                    opening = make_opening(game, opening_seed * SEED_STRIDE + k, plies)
                    ref = BotPlayer(make_bot(spec))
                    first, second = play_pair(
                        game, player, ref, opening, ms_per_move, opening_seed * 3 + idx
                    )
                    pts, n = score_for_a(first, second)
                    points += pts
                    games += n
                    # In `first` the subject is player 0, in `second` player 1.
                    for rec, side in ((first, 0), (second, 1)):
                        if rec.forfeit == side:
                            forfeits += 1
                            if len(reasons) < MAX_REASONS:
                                reasons.append(rec.reason)
                    start_failures = start_failures + 1 if player.start_failed else 0
                    if start_failures >= MAX_START_FAILURES:
                        artefact_error = reasons[-1] if reasons else "artefact did not start"
                        stopped_reason = artefact_error
                        break
                score = points / games if games else 0.0
                table.append(
                    {
                        **row,
                        "played": True,
                        "games": games,
                        "points": points,
                        "score": round(score, 3),
                        "forfeits": int(forfeits),
                        "reasons": reasons,
                    }  # fmt: skip
                )
                played.append((float(rung["rating"]), points, games))
                if stopped_reason is None and score <= stop_score:
                    stopped_reason = f"stopped after rung {rung['label']} (score {score:.2f})"
        finally:
            session.close()
            shutil.rmtree(work_root, ignore_errors=True)

        rating = rate_against(played)
        lo, hi = rating.ci95
        value = rating.rating if played else 0.0
        score = round(min(1.0, max(0.0, value) / par_rating), 6)
        move_ms = player.move_ms
        detail: dict[str, Any] = {
            "grader": ARENA_GRADER_TYPE,
            "language": language,
            "sandbox": sandbox.kind,
            "game": game.key,
            "ladder_version": live,
            "points": round(value, 1),
            "rating": round(value, 1),
            "ci95": [round(lo, 1), round(hi, 1)] if played else None,
            "par_rating": par_rating,
            "solved_rating": solved_rating,
            "games_per_rung": games_per_rung,
            "ms_per_move": ms_per_move,
            "rungs": table,
            "moves": len(move_ms),
            "max_move_ms": round(max(move_ms), 1) if move_ms else None,
            "mean_move_ms": round(sum(move_ms) / len(move_ms), 1) if move_ms else None,
        }
        if artefact_error:
            detail["artefact_error"] = artefact_error
        tail = [
            f"{r['rung']}: " + (f"{r['points']:g}/{r['games']:g}" if r["played"] else "not played")
            for r in table
        ]
        tail.append(f"rating {value:.0f}" + (f" (95% {lo:.0f}..{hi:.0f})" if played else ""))
        return GradeResult(
            solved=bool(played) and value >= solved_rating,
            score=score,
            detail=detail,
            raw_tail="\n".join(tail)[-RAW_TAIL_MAX_CHARS:],
        )


register_grader(ARENA_GRADER_TYPE, ArenaGrader)

__all__ = ["ARENA_GRADER_TYPE", "ArenaGrader", "ArtefactPlayer", "validate_probes"]
