"""`scenarios` grader (T-Ga4Rna, design `docs-md/benchmark-game-arena-design.md` §11).

Asks the subject's game bot for one move in each of N fixed positions and scores every answer by
its rank among the legal moves against a hidden reference search (`games/scenarios.py`). Cheap
(N requests, no games), deterministic and low-variance: the tuning signal next to the `arena`
ladder, which stays the final strength rating.

``grading_dir``::

    instance.json   language, limits
    probes.json     {"kind": "scenarios", "game": <wire>, "scenarios": [{"history", "legal",
                     "values", "plies"}], "ms_per_move", "solved_top1", "budget_seconds"}
    reference/      run.sh + server.py: a reference bot as an artefact (self-check)

An illegal, malformed, late or missing move, or a crash, scores 0 for that position (the process
is restarted). ``score`` = ``(quality - q_random) / (1 - q_random)`` clamped to [0, 1], where
``quality`` is the mean move quality and ``q_random`` that of a uniformly random mover (exact, from
the stored values); ``solved`` = the share of best moves (``top1``) is at least ``solved_top1``.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from .arena import PROBES_FILE, ArtefactPlayer, _load_json
from .blackbox import DEFAULT_STARTUP_TIMEOUT, INSTANCE_FILE, RAW_TAIL_MAX_CHARS, Session
from .errors import GraderError
from .games import GameError, game_from_wire
from .games.referee import Forfeit, replay
from .games.scenarios import Scenario, is_best, move_quality, random_quality
from .graders import Grader, GraderContext, GradeResult
from .registries import register_grader
from .sandbox import SandboxConfig, create_sandbox
from .spec import GraderConfig

SCENARIOS_GRADER_TYPE = "scenarios"
DEFAULT_BUDGET_SECONDS = 1800.0
MAX_START_FAILURES = 3
MAX_REASONS = 5
PHASES = (("early", 0.0, 1 / 3), ("mid", 1 / 3, 2 / 3), ("late", 2 / 3, 1.01))
REQUIRED_PROBE_KEYS = ("game", "scenarios", "ms_per_move", "solved_top1")


def validate_probes(probes: dict[str, Any]) -> None:
    if probes.get("kind") != SCENARIOS_GRADER_TYPE:
        raise GraderError("scenarios grader: probes.json kind must be 'scenarios'")
    missing = [k for k in REQUIRED_PROBE_KEYS if k not in probes]
    if missing:
        raise GraderError(f"scenarios grader: probes.json lacks {missing}")
    scs = probes["scenarios"]
    if not isinstance(scs, list) or not scs:
        raise GraderError("scenarios grader: no scenarios")
    for raw in scs:
        if not isinstance(raw, dict) or {"history", "legal", "values"} - set(raw):
            raise GraderError("scenarios grader: malformed scenario")
        if len(raw["legal"]) != len(raw["values"]) or len(raw["legal"]) < 2:
            raise GraderError("scenarios grader: legal/values mismatch")


class ScenariosGrader(Grader):
    """cfg (`GraderConfig` + `model_extra`): `sandbox`, `artefact_env`, `ms_per_move`,
    `solved_top1`, `budget_seconds` (cfg beats probes.json)."""

    def _grade(self, cfg: GraderConfig, ctx: GraderContext) -> GradeResult:
        extra = dict(cfg.model_extra or {})
        gd = getattr(ctx, "grading_dir", None)
        if not gd:
            raise GraderError("scenarios grader needs a grading_dir")
        grading_dir = Path(gd)
        probes = _load_json(grading_dir / PROBES_FILE, "probes")
        validate_probes(probes)
        instance_path = grading_dir / INSTANCE_FILE
        instance = _load_json(instance_path, "instance") if instance_path.is_file() else {}
        try:
            game = game_from_wire(probes["game"])
        except GameError as exc:
            raise GraderError(f"scenarios grader: {exc}") from exc
        scenarios = [Scenario.from_wire(r) for r in probes["scenarios"]]

        def knob(name: str, default: Any = None) -> Any:  # cfg beats probes.json
            return extra.get(name, probes.get(name, default))

        ms_per_move = int(knob("ms_per_move"))
        solved_top1 = float(knob("solved_top1"))
        budget = float(knob("budget_seconds", DEFAULT_BUDGET_SECONDS))

        sandbox_raw = dict(extra.get("sandbox") or {})
        language = instance.get("language") or extra.get("language") or sandbox_raw.get("language")
        if not language:
            raise GraderError("scenarios grader: no language (instance.json / cfg.language)")
        sandbox_raw.setdefault("startup_timeout_seconds", DEFAULT_STARTUP_TIMEOUT)
        sandbox = create_sandbox(
            SandboxConfig.parse({**sandbox_raw, "language": language}), language
        )
        artefact_dir = Path(ctx.repo_dir)
        artefact_env = {str(k): str(v) for k, v in (extra.get("artefact_env") or {}).items()}
        work_root = Path(tempfile.mkdtemp(prefix="scen-", dir=grading_dir))
        session = Session(
            sandbox, "jsonl", artefact_dir, work_root, env_for=lambda _d: artefact_env
        )
        player = ArtefactPlayer(session)
        artefact_error: str | None = None
        if not (artefact_dir / "run.sh").is_file():
            artefact_error = "run.sh not found in the artefact directory"

        qualities: list[float] = []
        bests: list[bool] = []
        plies: list[int] = []
        forfeits = 0
        reasons: list[str] = []
        start_failures = 0
        stopped: str | None = artefact_error
        t0 = time.perf_counter()
        try:
            for sc in scenarios:
                if stopped is None and time.perf_counter() - t0 > budget:
                    stopped = f"grading budget of {budget:g}s used up"
                q, best = 0.0, False
                if stopped is None:
                    history = [game.decode_move(m) for m in sc.history]
                    state = replay(game, history)
                    legal = game.legal(state)
                    player.start(game, game.to_move(state), ms_per_move, 0)
                    start_failures = start_failures + 1 if player.start_failed else 0
                    try:
                        move = player.choose(game, state, legal, history)
                        idx = legal.index(move)
                        q, best = move_quality(sc.values, idx), is_best(sc.values, idx)
                    except Forfeit as exc:
                        forfeits += 1
                        if len(reasons) < MAX_REASONS:
                            reasons.append(exc.reason)
                    except ValueError:  # `legal.index`: a well-formed but illegal move
                        forfeits += 1
                        if len(reasons) < MAX_REASONS:
                            reasons.append("illegal move")
                    if start_failures >= MAX_START_FAILURES:
                        stopped = reasons[-1] if reasons else "artefact did not start"
                        artefact_error = stopped
                qualities.append(q)
                bests.append(best)
                plies.append(sc.plies)
        finally:
            session.close()
            shutil.rmtree(work_root, ignore_errors=True)

        n = len(scenarios)
        quality = sum(qualities) / n
        top1 = sum(bests) / n
        base = random_quality(scenarios)
        score = round(min(1.0, max(0.0, (quality - base) / (1.0 - base))), 6)
        top = max(plies) + 1
        by_phase: dict[str, dict[str, float]] = {}
        for name, lo, hi in PHASES:
            members = [i for i, p in enumerate(plies) if lo <= p / top < hi]
            if members:
                by_phase[name] = {
                    "n": len(members),
                    "quality": round(sum(qualities[i] for i in members) / len(members), 4),
                    "top1": round(sum(bests[i] for i in members) / len(members), 4),
                }
        move_ms = player.move_ms
        detail: dict[str, Any] = {
            "grader": SCENARIOS_GRADER_TYPE,
            "language": language,
            "sandbox": sandbox.kind,
            "game": game.key,
            "scenarios": n,
            "ms_per_move": ms_per_move,
            "quality": round(quality, 4),
            "random_quality": round(base, 4),
            "top1": round(top1, 4),
            "solved_top1": solved_top1,
            "points": round(100.0 * score, 2),
            "forfeits": forfeits,
            "reasons": reasons,
            "by_phase": by_phase,
            "max_move_ms": round(max(move_ms), 1) if move_ms else None,
            "mean_move_ms": round(sum(move_ms) / len(move_ms), 1) if move_ms else None,
        }
        if stopped:
            detail["stopped"] = stopped
        if artefact_error:
            detail["artefact_error"] = artefact_error
        tail = [
            f"scenarios {n}: top1 {top1:.3f} quality {quality:.3f} (random {base:.3f})",
            f"forfeits {forfeits}",
        ]
        return GradeResult(
            solved=top1 >= solved_top1,
            score=score,
            detail=detail,
            raw_tail="\n".join(tail)[-RAW_TAIL_MAX_CHARS:],
        )


register_grader(SCENARIOS_GRADER_TYPE, ScenariosGrader)

__all__ = ["SCENARIOS_GRADER_TYPE", "ScenariosGrader", "validate_probes"]
