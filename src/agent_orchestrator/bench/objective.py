"""`objective` grader (T-Os5Lad, design `docs-md/benchmark-open-scale-design.md` §3.1, §6).

Grades open-scale optimisation tasks: every feasible answer is accepted and scored by **quality**
against a ladder of reference solutions, instead of pass/fail probes.

``grading_dir`` (written by a generated family whose ``grader_type`` is ``objective``)::

    instance.json   language, limits
    probes.json     {"kind": "objective", "case_seconds", "par", "solved_points", "rungs",
                     "cases": [{"id", "problem", "sense", "case", "ladder": [v_0..v_K]}]}
    reference/      run.sh + server.py exposing evaluate(problem, case, answer) and
                    ladder_points(y, ladder)

The artefact runs in the same sandbox as the blackbox grader (jsonl contract). Each case is one
``solve`` request bounded by ``case_seconds``; the grader-owned ``evaluate`` (imported in-process
from ``reference/server.py``, never from the artefact) checks feasibility and computes the
objective, and ``ladder_points`` places it on the ladder (unbounded above the top rung). A late,
crashed, malformed or infeasible answer earns 0 for that case; the artefact is restarted and grading
continues. The reference is not run at grade time: rung values were computed deterministically at
generation time.

Result: ``score = min(1, points / par)``, ``solved = points >= solved_points``, raw ``points`` (the
open-scale number) in ``detail``.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import ModuleType
from typing import Any

from .blackbox import (
    DEFAULT_STARTUP_TIMEOUT,
    INSTANCE_FILE,
    RAW_TAIL_MAX_CHARS,
    REFERENCE_DIRNAME,
    Session,
)
from .errors import GraderError
from .graders import Grader, GraderContext, GradeResult
from .registries import register_grader
from .sandbox import ArtefactFailure, ArtefactTimeout, SandboxConfig, create_sandbox
from .spec import GraderConfig

OBJECTIVE_GRADER_TYPE = "objective"
PROBES_FILE = "probes.json"
REFERENCE_PROGRAM = "server.py"
DEFAULT_CASE_SECONDS = 10.0
# Transport slack on top of the stated limit: JSON encode/decode of a large answer + pipe latency.
REQUEST_GRACE_SECONDS = 1.0
DEFAULT_SOLVED_POINTS = 2.0
# Give up on an artefact that cannot even start this many times in a row (remaining cases score 0).
MAX_START_FAILURES = 2


def _load_json(path: Path, what: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GraderError(f"objective grader: cannot read {what} {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise GraderError(f"objective grader: {what} {path} is not a JSON object")
    return data


def load_checker(reference_dir: Path) -> ModuleType:
    """Import the grader-owned reference program for its `evaluate` / `ladder_points`."""
    path = reference_dir / REFERENCE_PROGRAM
    if not path.is_file():
        raise GraderError(f"objective grader: reference program missing: {path}")
    name = f"ao_bench_objective_{abs(hash(str(path.resolve())))}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise GraderError(f"objective grader: cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    for attr in ("evaluate", "ladder_points"):
        if not callable(getattr(module, attr, None)):
            raise GraderError(f"objective grader: {path} has no {attr}()")
    return module


def validate_cases(probes: dict[str, Any]) -> list[dict[str, Any]]:
    if probes.get("kind") != OBJECTIVE_GRADER_TYPE:
        raise GraderError("objective grader: probes.json kind must be 'objective'")
    cases = probes.get("cases")
    if not isinstance(cases, list) or not cases:
        raise GraderError("objective grader: probes.json has no cases")
    for c in cases:
        ladder = c.get("ladder") if isinstance(c, dict) else None
        if (
            not isinstance(c, dict)
            or not isinstance(c.get("id"), str)
            or not isinstance(c.get("problem"), str)
            or not isinstance(c.get("case"), dict)
            or c.get("sense", "min") != "min"
            or not isinstance(ladder, list)
            or len(ladder) < 2
            or any(not isinstance(v, int | float) for v in ladder)
            or any(a <= b for a, b in zip(ladder, ladder[1:]))
        ):
            raise GraderError(
                f"objective grader: malformed case {c.get('id') if isinstance(c, dict) else c!r} "
                "(needs id, problem, case, sense 'min' and a strictly decreasing ladder)"
            )
    return cases


class ObjectiveGrader(Grader):
    """cfg (`GraderConfig` + `model_extra`): `sandbox` (SandboxConfig keys), `par` and
    `solved_points` (cfg beats probes.json), `case_seconds` (cfg beats probes.json)."""

    def _grade(self, cfg: GraderConfig, ctx: GraderContext) -> GradeResult:
        extra = dict(cfg.model_extra or {})
        gd = getattr(ctx, "grading_dir", None)
        if not gd:
            raise GraderError("objective grader needs a grading_dir")
        grading_dir = Path(gd)
        probes = _load_json(grading_dir / PROBES_FILE, "probes")
        cases = validate_cases(probes)
        instance_path = grading_dir / INSTANCE_FILE
        instance = _load_json(instance_path, "instance") if instance_path.is_file() else {}
        checker = load_checker(grading_dir / REFERENCE_DIRNAME)

        sandbox_raw = dict(extra.get("sandbox") or {})
        language = instance.get("language") or extra.get("language") or sandbox_raw.get("language")
        if not language:
            raise GraderError("objective grader: no language (instance.json / cfg.language)")
        case_seconds = float(
            extra.get("case_seconds") or probes.get("case_seconds") or DEFAULT_CASE_SECONDS
        )
        par = float(extra.get("par") or probes.get("par") or len(cases[0]["ladder"]) - 1)
        solved_points = float(
            extra.get("solved_points") or probes.get("solved_points") or DEFAULT_SOLVED_POINTS
        )
        sandbox_raw.setdefault("startup_timeout_seconds", DEFAULT_STARTUP_TIMEOUT)
        sandbox = create_sandbox(
            SandboxConfig.parse({**sandbox_raw, "language": language}), language
        )

        artefact_dir = Path(ctx.repo_dir)
        results: list[dict[str, Any]] = []
        artefact_error: str | None = None
        if not (artefact_dir / "run.sh").is_file():
            artefact_error = "run.sh not found in the artefact directory"
        work_root = Path(tempfile.mkdtemp(prefix="objective-", dir=grading_dir))
        session = Session(sandbox, "jsonl", artefact_dir, work_root)
        start_failures = 0
        give_up = artefact_error  # set: every remaining case scores 0 with this reason
        try:
            for c in cases:
                if give_up:
                    results.append(self._zero(c, give_up))
                    continue
                results.append(self._run_case(c, session, checker, case_seconds))
                if results[-1].get("start_failed"):
                    start_failures += 1
                    artefact_error = results[-1]["reason"]
                    if start_failures >= MAX_START_FAILURES:
                        give_up = artefact_error
                else:
                    start_failures = 0
        finally:
            session.close()
            shutil.rmtree(work_root, ignore_errors=True)

        points = sum(r["points"] for r in results) / len(results)
        per_problem: dict[str, list[float]] = {}
        for r in results:
            per_problem.setdefault(r["problem"], []).append(r["points"])
        score = round(min(1.0, max(0.0, points / par)), 6) if par > 0 else 0.0
        detail: dict[str, Any] = {
            "grader": OBJECTIVE_GRADER_TYPE,
            "language": language,
            "sandbox": sandbox.kind,
            "points": round(points, 6),
            "par": par,
            "solved_points": solved_points,
            "case_seconds": case_seconds,
            "rungs": probes.get("rungs"),
            "points_by_problem": {p: round(sum(v) / len(v), 6) for p, v in per_problem.items()},
            "cases": results,
        }
        if artefact_error:
            detail["artefact_error"] = artefact_error
        tail = [
            f"{r['id']}: {r['points']:.3f} pts" + (f" ({r['reason']})" if r.get("reason") else "")
            for r in results
        ]
        return GradeResult(
            solved=points >= solved_points,
            score=score,
            detail=detail,
            raw_tail="\n".join(tail)[-RAW_TAIL_MAX_CHARS:],
        )

    @staticmethod
    def _zero(c: dict[str, Any], reason: str, **more: Any) -> dict[str, Any]:
        return {
            "id": c["id"],
            "problem": c["problem"],
            "points": 0.0,
            "objective": None,
            "ladder": c["ladder"],
            "reason": reason,
            **more,
        }

    def _run_case(
        self, c: dict[str, Any], session: Session, checker: ModuleType, case_seconds: float
    ) -> dict[str, Any]:
        req = {"op": "solve", "problem": c["problem"], "case": c["case"]}
        try:
            transport = session.ensure(fresh=False)
        except ArtefactFailure as exc:
            return self._zero(c, f"artefact did not start: {exc}"[:300], start_failed=True)
        t0 = time.perf_counter()
        try:
            outcome = transport.send({"json": req}, case_seconds + REQUEST_GRACE_SECONDS)
        except ArtefactTimeout:
            session.close()  # a late answer would misalign the next request: restart
            return self._zero(c, f"no answer within {case_seconds:g}s")
        except ArtefactFailure as exc:
            session.close()
            return self._zero(c, f"artefact failed: {exc}"[:300])
        seconds = round(time.perf_counter() - t0, 3)
        body = outcome.body
        if not isinstance(body, dict) or body.get("ok") is not True or "answer" not in body:
            return self._zero(c, f"not an ok answer: {outcome.text[:120]!r}", seconds=seconds)
        try:
            y = float(checker.evaluate(c["problem"], c["case"], body["answer"]))
        except Exception as exc:  # noqa: BLE001 -- the checker's OpError or a malformed answer
            return self._zero(
                c, f"infeasible: {getattr(exc, 'message', exc)}"[:300], seconds=seconds
            )
        points = float(checker.ladder_points(y, c["ladder"]))
        return {
            "id": c["id"],
            "problem": c["problem"],
            "points": round(points, 6),
            "objective": y,
            "ladder": c["ladder"],
            "seconds": seconds,
        }


register_grader(OBJECTIVE_GRADER_TYPE, ObjectiveGrader)

__all__ = ["OBJECTIVE_GRADER_TYPE", "ObjectiveGrader", "load_checker", "validate_cases"]
