"""Grader adapters (design doc `docs-md/benchmarking-framework-hld.md` §4.3, §6).

Produce a `GradeResult` over a task's mutated workspace (`ctx.repo_dir`). Every concrete
grader below is **bounded and never raises for an anticipated failure mode** (grading
command missing/timing out, a golden file absent) -- those degrade to a graceful
`GradeResult(solved=False, ...)` with the failure recorded in `detail`/`raw_tail`,
mirroring §4.3's edge-case table. Only a genuinely *unexpected* internal error (a bug,
not a modeled failure surface) propagates, wrapped as `GraderError` by the `Grader.grade`
boundary below -- the future runner (T-Run5Tz) catches `BenchError` around the call and
itself falls back to a `GradeResult`, so this module only needs to add context, never
recover twice.

`ctx` is typed as `GraderContext`, a *local* structural (`Protocol`) subset of the real
`RunContext` (design §6) rather than an import of `agent_orchestrator.bench.subjects` --
that module is a concurrently-written sibling task (T-Sbj9Ka); importing it here would
create an unnecessary and possibly-cyclic dependency on WIP code. Any object exposing a
`repo_dir: str` attribute (including the real `RunContext` once it lands) satisfies it.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from .errors import GraderError
from .registries import register_grader
from .spec import Assertion, GraderConfig

# Default pytest invocation (design §4.3 pseudocode) -- overridable per-task via
# `GraderConfig.command`. Named, not inlined, so tests/config can reference/override it.
_DEFAULT_PYTEST_COMMAND = "uv run pytest -q --tb=no"

# Grading commands are bounded by default even when a task/suite omits
# `timeout_seconds` -- an unbounded grading subprocess could hang a whole bench run.
_DEFAULT_GRADER_TIMEOUT_SECONDS = 120

# `GradeResult.raw_tail` / `detail` captures are capped (design §4.3 pseudocode:
# `tail(out, 2000)`) so a runaway/verbose command can't bloat `run.json`.
_RAW_TAIL_MAX_CHARS = 2000

# Conventional POSIX sentinel returncodes used when the process itself never produced
# one (spawn failed / timed out) -- mirrors `timeout(1)` (124) and shell "command not
# found" (127) so `detail.returncode` stays a familiar, greppable integer instead of an
# ad hoc negative magic number.
_TIMEOUT_RETURNCODE = 124
_COMMAND_NOT_FOUND_RETURNCODE = 127


@runtime_checkable
class GraderContext(Protocol):
    """Structural subset of `RunContext` (design §6) that graders need: the absolute
    path to the mutated repo copy a subject just ran against. See module docstring for
    why this is a local Protocol instead of importing `bench.subjects.RunContext`.
    """

    repo_dir: str


class GradeResult(BaseModel):
    """Verdict produced by a `Grader` (design §6): `solved` is the pass/fail source of
    truth used by aggregation; `score` is a finer-grained [0,1] signal (e.g. pytest
    pass-rate) that MAY diverge from `solved` (a `pass_threshold` can make a partial
    score count as solved, or vice versa is never true -- score never determines solved
    on its own except via that explicit opt-in).
    """

    solved: bool
    score: float = Field(ge=0.0, le=1.0)
    detail: dict[str, Any] = Field(default_factory=dict)
    raw_tail: str = ""


@dataclass
class _CommandRun:
    """Internal result of `_run_command` -- never raises; failure modes are flags."""

    returncode: int
    output: str
    timed_out: bool = False
    missing: bool = False


def _resolve_cwd(repo_dir: str, cfg_cwd: str | None) -> Path:
    return Path(repo_dir) / (cfg_cwd or ".")


def _tail(text: str, limit: int = _RAW_TAIL_MAX_CHARS) -> str:
    return text[-limit:] if len(text) > limit else text


def _run_command(command: str, cwd: Path, timeout_seconds: int) -> _CommandRun:
    """Spawn `command` (shlex-split, `shell=False`) in `cwd`, bounded by
    `timeout_seconds`. Never raises: a malformed command string, a missing binary/bad
    cwd, or a timeout all degrade to a `_CommandRun` with a flag set and a conventional
    returncode rather than propagating `OSError`/`subprocess.TimeoutExpired` -- callers
    (concrete graders) decide how each flag affects `solved`/`score` (design §4.3 edge
    cases: "grader command missing/timeout -> solved=False ... no exception raised").
    """
    try:
        argv = shlex.split(command)
    except ValueError as exc:  # e.g. unbalanced quotes
        return _CommandRun(
            returncode=_COMMAND_NOT_FOUND_RETURNCODE,
            output=f"malformed grader command {command!r}: {exc}",
            missing=True,
        )
    if not argv:
        return _CommandRun(
            returncode=_COMMAND_NOT_FOUND_RETURNCODE, output="empty grader command", missing=True
        )
    try:
        proc = subprocess.run(  # noqa: S603 -- shell=False, argv from shlex.split, bounded timeout
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        return _CommandRun(returncode=proc.returncode, output=output)
    except (FileNotFoundError, NotADirectoryError) as exc:
        # Missing binary OR a bad `cfg.cwd` (both surface as OSError subclasses here).
        return _CommandRun(returncode=_COMMAND_NOT_FOUND_RETURNCODE, output=str(exc), missing=True)
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stdout or "") + (exc.stderr or "") if isinstance(exc.stdout, str) else ""
        return _CommandRun(returncode=_TIMEOUT_RETURNCODE, output=partial, timed_out=True)


# Enrichment-only pytest summary parser (ASSUMPTION A3) -- matches counts like
# "1 failed, 3 passed in 0.05s" or "2 errors in 0.01s". Deliberately permissive: an
# unrecognized/garbled summary format degrades to zero matches (passed=failed=total=0)
# rather than raising, since the exit code -- not this regex -- is the pass source of
# truth (design §4.3).
_PYTEST_SUMMARY_COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|error|errors)\b")


def _parse_pytest_summary(output: str) -> tuple[int, int, int]:
    """Best-effort parse of pytest's trailing summary line into (passed, failed,
    total). `error`/`errors` counts (collection/fixture errors) are folded into
    `failed` for scoring purposes -- an errored test is not a pass either way.
    """
    passed = 0
    failed = 0
    for count_str, label in _PYTEST_SUMMARY_COUNT_RE.findall(output):
        count = int(count_str)
        if label == "passed":
            passed += count
        else:
            failed += count
    return passed, failed, passed + failed


class Grader(ABC):
    """Base class for all grader adapters (design §4.3, §6: `Grader.grade(cfg, ctx) ->
    GradeResult`, errors: `GraderError`).
    """

    def grade(self, cfg: GraderConfig, ctx: GraderContext) -> GradeResult:
        """Public entrypoint. Wraps `_grade` so a genuinely unexpected internal failure
        (not one of the anticipated surfaces each concrete grader already handles
        gracefully) is wrapped as `GraderError` with context instead of an opaque
        traceback, and never silently swallowed (CLAUDE.md error-boundary rule).
        """
        try:
            return self._grade(cfg, ctx)
        except GraderError:
            raise
        except Exception as exc:
            raise GraderError(f"{type(self).__name__} failed to grade: {exc}") from exc

    @abstractmethod
    def _grade(self, cfg: GraderConfig, ctx: GraderContext) -> GradeResult:
        raise NotImplementedError


class PytestGrader(Grader):
    """Test-pass-rate grader -- the MVP default (design §4.3). Exit code is the sole
    source of truth for `solved`; the summary line only enriches `score` (ASSUMPTION
    A3). An optional `cfg.pass_threshold` overrides `solved` from the parsed score.
    """

    def _grade(self, cfg: GraderConfig, ctx: GraderContext) -> GradeResult:
        command = cfg.command or _DEFAULT_PYTEST_COMMAND
        cwd = _resolve_cwd(ctx.repo_dir, cfg.cwd)
        timeout = cfg.timeout_seconds or _DEFAULT_GRADER_TIMEOUT_SECONDS
        run = _run_command(command, cwd, timeout)

        detail: dict[str, Any] = {"returncode": run.returncode}
        if run.timed_out:
            detail["timed_out"] = True
        if run.missing:
            detail["missing_command"] = True
        if run.timed_out or run.missing:
            # A command that never finished/ran can't be trusted for a partial score
            # either -- collapse straight to the "never raises" failure shape.
            return GradeResult(solved=False, score=0.0, detail=detail, raw_tail=_tail(run.output))

        passed, failed, total = _parse_pytest_summary(run.output)
        solved = run.returncode == 0 and total > 0 and failed == 0
        score = (passed / total) if total > 0 else (1.0 if run.returncode == 0 else 0.0)
        if cfg.pass_threshold is not None:
            solved = score >= cfg.pass_threshold
        detail.update({"passed": passed, "failed": failed, "total": total})
        return GradeResult(solved=solved, score=score, detail=detail, raw_tail=_tail(run.output))


class CommandGrader(Grader):
    """Generic exit-code grader (design §4.3): `solved = (rc == 0)`. This is the one
    hook non-dev domains use -- any command that exits 0 on success qualifies.
    """

    def _grade(self, cfg: GraderConfig, ctx: GraderContext) -> GradeResult:
        if not cfg.command:
            return GradeResult(
                solved=False, score=0.0, detail={"reason": "no command configured"}, raw_tail=""
            )
        cwd = _resolve_cwd(ctx.repo_dir, cfg.cwd)
        timeout = cfg.timeout_seconds or _DEFAULT_GRADER_TIMEOUT_SECONDS
        run = _run_command(cfg.command, cwd, timeout)

        detail: dict[str, Any] = {"returncode": run.returncode}
        if run.timed_out:
            detail["timed_out"] = True
        if run.missing:
            detail["missing_command"] = True
        solved = run.returncode == 0 and not run.timed_out and not run.missing
        score = 1.0 if solved else 0.0
        return GradeResult(solved=solved, score=score, detail=detail, raw_tail=_tail(run.output))


def _resolve_golden_path(assertion: Assertion) -> Path | None:
    """Resolve `assertion.golden` for an `equals_file` check.

    KNOWN CONTRACT GAP (flagged for arbitration -- not fixed here, it lives outside the
    files this task owns): `Assertion.golden` (`bench/spec.py`) is documented as
    "relative to the suite.json", and `load_suite` validates the referenced file exists
    at LOAD time using that suite-relative base -- but it does not rewrite `golden` to
    an absolute path before returning the `BenchSuite`, and neither `GraderConfig` nor
    `RunContext` (design §6) carries the suite's base directory through to grade time.
    So a `FileAssertionGrader` running later (invoked by the future runner, T-Run5Tz)
    cannot itself reconstruct the same base path `load_suite` used.

    Until that's threaded through (recommended fix: `load_suite` resolves `golden` to
    an absolute path in place), this resolves an already-absolute `golden` directly,
    and a relative one against the current working directory (the convention for a
    caller that `cd`s to/near the suite before invoking the runner) -- and NEVER
    raises: an unresolvable golden degrades the assertion to `passed=False` (see
    `_check_assertion`), not an exception.
    """
    if not assertion.golden:
        return None
    p = Path(assertion.golden)
    return p if p.is_absolute() else Path.cwd() / p


def _check_assertion(assertion: Assertion, repo_dir: Path) -> dict[str, Any]:
    target = repo_dir / assertion.path
    if assertion.type == "exists":
        return {"type": "exists", "path": assertion.path, "passed": target.exists()}

    if assertion.type == "contains":
        passed = False
        if target.is_file():
            try:
                passed = (assertion.substring or "") in target.read_text(errors="replace")
            except OSError:
                passed = False
        return {"type": "contains", "path": assertion.path, "passed": passed}

    # assertion.type == "equals_file" (the only remaining Literal per spec.py)
    golden_path = _resolve_golden_path(assertion)
    passed = False
    if golden_path is not None and golden_path.is_file() and target.is_file():
        try:
            passed = target.read_bytes() == golden_path.read_bytes()
        except OSError:
            passed = False
    return {"type": "equals_file", "path": assertion.path, "passed": passed}


class FileAssertionGrader(Grader):
    """Files exist / contain a substring / match a golden file (design §4.3).
    `score` = fraction of assertions passing; `solved` = all of them passing.
    """

    def _grade(self, cfg: GraderConfig, ctx: GraderContext) -> GradeResult:
        if not cfg.assertions:
            return GradeResult(
                solved=False,
                score=0.0,
                detail={"per_assertion": [], "reason": "no assertions configured"},
                raw_tail="",
            )
        repo_dir = Path(ctx.repo_dir)
        checks = [_check_assertion(a, repo_dir) for a in cfg.assertions]
        passed_count = sum(1 for c in checks if c["passed"])
        score = passed_count / len(checks)
        solved = passed_count == len(checks)
        detail = {"per_assertion": checks}
        return GradeResult(solved=solved, score=score, detail=detail, raw_tail="")


class FakeGrader(Grader):
    """Deterministic, network-free grader for tests only (design §3.1). `GraderConfig`
    has no dedicated fake-only fields (unlike `SubjectSpec`'s `scripted_effect`/
    `fake_cost`), so this re-purposes two existing fields as its script:

    - `cfg.command == "false"` -> not solved (score 0.0), mirroring `CommandGrader`'s
      exit-code convention so a suite author can swap `type: fake` for `type: command`
      without learning a new vocabulary. Anything else (including unset) -> solved.
    - `cfg.pass_threshold`, if set, OVERRIDES the resulting score directly (it is not
      treated as a threshold check here) so a test can script a partial/fractional
      score without spawning a real subprocess.
    """

    def _grade(self, cfg: GraderConfig, ctx: GraderContext) -> GradeResult:
        solved = cfg.command != "false"
        score = cfg.pass_threshold if cfg.pass_threshold is not None else (1.0 if solved else 0.0)
        return GradeResult(
            solved=solved,
            score=score,
            detail={"scripted": True, "command": cfg.command},
            raw_tail="",
        )


# Register concrete graders into GRADER_REGISTRY at import time (design §6) so
# `bench/spec.py`'s KNOWN_GRADER_TYPES set and this registry now agree on every type.
register_grader("pytest", PytestGrader)
register_grader("command", CommandGrader)
register_grader("file_assertion", FileAssertionGrader)
register_grader("fake", FakeGrader)
