#!/usr/bin/env python3
"""Deterministic grading hook script (E-1cecSx B3.2,
`docs-md/cost-caching-optimization-hld.md` §3.2).

Lives alongside Epic A's own example hooks (`specs/examples/hooks/check_disk_space.py`,
`specs/examples/hooks/grade.py` -- a deliberately trivial "declared outputs exist" demo
script) so a demo `WorkflowSpec` can reference either by a relative path, same convention.
This script is the more capable sibling `grade.py` intentionally stayed simple for: it
generalizes `agent_orchestrator.bench.graders`' `CommandGrader`/`PytestGrader` shape into a
standalone script usable as either Epic A's `post_hook` (dispatch-scoped) or Epic B's
`settlement_hook` (fires regardless of skip/resume) -- the SAME script works for both,
following Epic A HLD §7's 5-step recipe:

    1. Read `AO_HOOK_CONTEXT_PATH` (task id, output paths, agent status/exit_code).
    2. Call an existing/adapted `Grader.grade(...)` -- reused here, not reimplemented (CLAUDE.md
       DRY rule): this script imports `bench.graders.CommandGrader`/`PytestGrader` directly
       rather than re-shelling-out with its own copy of that logic.
    3. Write `{"score": ..., "detail": {...}}` to `AO_HOOK_RESULT_PATH`.
    4. `sys.exit(0 if result.solved else 1)`.
    5. Referenced by name from whichever task(s) should be graded: `"post_hook": {"use":
       "grade", "on_failure": "ignore"}` (dispatch-scoped, observe-only) or `"settlement_hook":
       {"use": "grade"}` (fires for skipped/resumed tasks too -- always observe-only, see the
       design doc §3.3; `on_failure` is not consulted for `settlement_hook`).

**Deterministic/scripted only -- NEVER an LLM call** (Epic A HLD §7's explicit non-billable
boundary for this hook mechanism: an LLM-based grader's cost/tokens would be invisible to
`reconcile()`/the budget breakers, and would bypass `AgentSpec` resolution and quota/429
handling entirely). `AO_GRADE_COMMAND` below must be a deterministic check -- a test suite, a
build, a scripted assertion -- never something that itself calls out to Claude/another LLM.

Configuration is via environment variables (so ONE script instance, referenced by name in
`WorkflowSpec.hooks`, works for every task that wires it -- `HookSpec.command` is a fixed
argv list per the workflow-root registry design, D4; env vars are how a fixed argv adapts
per invocation without per-task script copies):

    AO_GRADE_COMMAND          Required. The verify command to run, e.g. "pytest -q --tb=no".
    AO_GRADE_MODE             "command" (default) -- exit-code-only, mirrors
                               `bench.graders.CommandGrader`. "pytest" -- also parses a
                               trailing pytest summary line into a fractional `score`,
                               mirrors `bench.graders.PytestGrader`.
    AO_GRADE_CWD              Optional. Directory the command runs in, relative to this
                               process's own cwd (already the task's resolved working
                               directory, per `hooks.py::run_hook`'s `cwd=` argument).
                               Defaults to "." (the hook's own cwd).
    AO_GRADE_TIMEOUT_SECONDS  Optional int, default 120 (matches
                               `bench.graders._DEFAULT_GRADER_TIMEOUT_SECONDS`).
    AO_GRADE_PASS_THRESHOLD   Optional float in [0, 1]. In "pytest" mode, overrides `solved`
                               from the parsed pass-rate score (mirrors `GraderConfig.
                               pass_threshold`). Ignored in "command" mode.

Reads `AO_HOOK_CONTEXT_PATH` (informational -- folded into `detail.context_summary` so a
human reading `result.json` sees which task/cycle produced this grade without re-opening
`context.json` separately) and writes `AO_HOOK_RESULT_PATH` per the hook contract
(`hooks.py::run_hook`'s `_populate_result_file`: a numeric top-level `score` key is promoted
to the typed `HookOutcome.score` field; everything else stays in `HookOutcome.detail`).

Exit codes: `0` if the grader's `GradeResult.solved` is True, `1` otherwise. A configuration
error (missing `AO_GRADE_COMMAND`) also exits `1` (never `0` -- an unconfigured grader hook
must never silently report "passed"), with `detail.reason` explaining why.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

# The script is invoked via subprocess with `agent_orchestrator` importable (installed in the
# same environment that runs the engine) -- reusing the bench module's grader implementations
# rather than re-shelling-out with a second copy of the same bounded-subprocess logic (CLAUDE.md
# DRY rule; hooks.py's own module docstring already records this codebase's accepted trade-off
# for NOT extracting a shared primitive across all 5-6 instances -- this script deliberately
# does NOT become a 7th independent one by importing the existing implementation instead).
from agent_orchestrator.bench.graders import CommandGrader, PytestGrader
from agent_orchestrator.bench.spec import GraderConfig

_DEFAULT_MODE = "command"
_DEFAULT_TIMEOUT_SECONDS = 120


@dataclass
class _ScriptGraderContext:
    """Satisfies `bench.graders.GraderContext`'s structural Protocol (`repo_dir: str`)."""

    repo_dir: str


def _read_context() -> dict:
    context_path = os.environ.get("AO_HOOK_CONTEXT_PATH")
    if not context_path:
        return {}
    try:
        return json.loads(Path(context_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Informational only (folded into detail.context_summary) -- never fatal to grading.
        return {}


def _write_result(result_path: str, score: float, detail: dict) -> None:
    payload = {"score": score, "detail": detail}
    Path(result_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    result_path = os.environ.get("AO_HOOK_RESULT_PATH")
    context = _read_context()
    context_summary = {
        "task_id": context.get("task_id"),
        "hook_kind": context.get("hook_kind"),
        "dispatch_cycle": context.get("dispatch_cycle"),
        "status": context.get("status"),  # present for post_hook; absent for settlement_hook
    }

    command = os.environ.get("AO_GRADE_COMMAND")
    if not command:
        detail = {"reason": "AO_GRADE_COMMAND not set", "context_summary": context_summary}
        if result_path:
            _write_result(result_path, 0.0, detail)
        sys.stderr.write("grade_task.py: AO_GRADE_COMMAND is required\n")
        return 1

    mode = os.environ.get("AO_GRADE_MODE", _DEFAULT_MODE)
    cwd = os.environ.get("AO_GRADE_CWD", ".")
    timeout_raw = os.environ.get("AO_GRADE_TIMEOUT_SECONDS")
    timeout_seconds = int(timeout_raw) if timeout_raw else _DEFAULT_TIMEOUT_SECONDS
    threshold_raw = os.environ.get("AO_GRADE_PASS_THRESHOLD")
    pass_threshold = float(threshold_raw) if threshold_raw else None

    cfg = GraderConfig(
        type=mode,
        command=command,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        pass_threshold=pass_threshold,
    )
    # `repo_dir` is this process's own cwd (already the task's resolved working directory --
    # `hooks.py::run_hook` spawns this script with `cwd=` set to it); `GraderConfig.cwd` above
    # is then resolved RELATIVE to that, exactly as `bench.graders._resolve_cwd` already does
    # for a real bench run.
    ctx = _ScriptGraderContext(repo_dir=os.getcwd())

    grader = PytestGrader() if mode == "pytest" else CommandGrader()
    grade = grader.grade(cfg, ctx)

    detail = dict(grade.detail)
    detail["solved"] = grade.solved
    detail["context_summary"] = context_summary
    if grade.raw_tail:
        detail["raw_tail"] = grade.raw_tail

    if result_path:
        _write_result(result_path, grade.score, detail)

    return 0 if grade.solved else 1


if __name__ == "__main__":
    sys.exit(main())
