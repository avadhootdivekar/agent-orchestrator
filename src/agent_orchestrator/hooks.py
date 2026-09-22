"""Task lifecycle hook execution mechanics (E-AMSSHX,
`docs-md/task-lifecycle-hooks-hld.md` §5).

`run_hook` is the sole entry point: given a `HookSpec` (the workflow-root-declared argv
command a task references by name via `HookRef`, see `models.py`) and this dispatch's
context, spawn the hook as a bounded subprocess and degrade every anticipated failure mode --
timeout, missing binary, malformed result file -- into a `HookOutcome` rather than raising.
`engine.py`'s `Orchestrator._run_with_retries` has exactly two thin call sites into this
module (a pre-hook gate before the attempt loop, and `_finalize_with_post_hook` after it);
kept as its own module (not engine.py, already ~4000 lines) since this is a self-contained,
single-responsibility chunk of logic -- an early-gate architect suggestion.

`run_hook`'s `kind` parameter has a THIRD value as of E-1cecSx B3.2/B3.3 (ADR-0015 decision 2):
`"settlement_hook"`, called from `outcomes.py`'s POST-RUN grading pass (`ao report outcomes
--grade`), never from `engine.py`. This module's own execution logic needed no other change --
`kind` is treated as an opaque string used solely for the returned `HookOutcome.kind` field, so
widening the Literal (here and on `HookOutcome.kind` in `models.py`) is the entire diff.

This is the FIFTH independent bounded-subprocess-with-timeout implementation in this codebase
(`isolation/git.py`, `isolation/integrator.py` x2, `isolation/resolvers.py`,
`bench/graders.py::_run_command`) -- a DRY trade-off recorded, not hidden (HLD §5):
extracting one shared primitive would touch all of those files, out of this epic's narrow
change-scope boundary. `_run_hook_inner`'s shape deliberately mirrors
`bench/graders.py::_run_command`'s "never raises, degrade to a result object" idiom.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Literal

from .artifacts import ArtifactStore, read_control
from .errors import ControlFileError
from .models import HookOutcome, HookSpec, HookStatus

logger = logging.getLogger(__name__)

# Mirrors isolation/integrator.py's VERIFY_CAPTURE_CAP_BYTES precedent (same order of
# magnitude) -- an unbounded capture_output=True on a runaway hook is a real disk/memory-
# exhaustion surface, not hypothetical, given the timeout alone can still be many seconds of
# output.
HOOK_CAPTURE_CAP_BYTES: int = 1_048_576  # 1 MiB per stream

# Bounded tail length for the stderr text folded into TaskResult.error when a hook's own
# on_failure policy fails/downgrades a task (engine.py call sites) -- mirrors
# executors/claude_cli.py's own stderr-tail-into-TaskResult.error convention (that module's
# `err_tail = (...)[-500:]`); named here rather than repeating a bare literal at the call site.
HOOK_ERROR_STDERR_TAIL_CHARS: int = 500

_STDOUT_FILE = "stdout.txt"
_STDERR_FILE = "stderr.txt"
_CONTEXT_FILE = "context.json"
_RESULT_FILE = "result.json"
_CONTEXT_VERSION = 1


def _cap(text: str) -> str:
    return text[:HOOK_CAPTURE_CAP_BYTES]


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _write_capture(capture_dir: str, stdout: str | None, stderr: str | None) -> None:
    """Write bounded stdout/stderr to *capture_dir* (HLD §5). Best-effort: a write failure
    here must not itself raise out of `run_hook`'s never-raises contract."""
    try:
        Path(capture_dir, _STDOUT_FILE).write_text(_cap(stdout or ""), encoding="utf-8")
        Path(capture_dir, _STDERR_FILE).write_text(_cap(stderr or ""), encoding="utf-8")
    except OSError:
        logger.warning("hooks: failed to write stdout/stderr capture under %s", capture_dir)


def read_stderr_tail(capture_dir: str, max_chars: int = HOOK_ERROR_STDERR_TAIL_CHARS) -> str:
    """Best-effort bounded read of ``<capture_dir>/stderr.txt``'s tail.

    For folding a hook's own failure detail into `TaskResult.error` at the engine call sites
    (never the optional JSON result file -- D3 still holds, this is purely for a human/self-
    heal-readable message, mirroring `executors/claude_cli.py`'s own stderr-tail convention).
    Returns "" if the file doesn't exist or can't be read -- never raises, matching
    `run_hook`'s own contract.
    """
    try:
        text = Path(capture_dir, _STDERR_FILE).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-max_chars:] if max_chars > 0 else ""


def _populate_result_file(
    outcome: HookOutcome, artifact_store: ArtifactStore, result_path: str
) -> HookOutcome:
    """Result-file read sequencing (HLD §5, review finding -- must be structurally distinct
    from one blanket try/except): `exists()` decides "no file" (normal) vs "present" FIRST;
    only a present-but-malformed file raises `ControlFileError` and sets `HookOutcome.error`.
    A numeric `score` key is popped out into the typed field; everything else (including a
    non-numeric `score`) stays in `detail` verbatim. Never overrides `outcome.status` (D3).
    """
    if not artifact_store.exists(result_path):
        return outcome  # detail={}, error=None, score=None -- a hook choosing not to write
        # one is normal, not an error.
    try:
        data = read_control(artifact_store, result_path)
    except ControlFileError as exc:
        # Present but malformed (oversized/invalid JSON/non-object) -- THE genuinely-
        # malformed case FR-6 wants surfaced, distinct from "no file" above.
        return outcome.model_copy(update={"error": str(exc)})
    detail = dict(data)
    score: float | None = None
    raw_score = detail.get("score")
    if isinstance(raw_score, int | float) and not isinstance(raw_score, bool):
        score = float(raw_score)
        del detail["score"]
    return outcome.model_copy(update={"detail": detail, "score": score})


def _run_hook_inner(
    hook: HookSpec,
    *,
    kind: Literal["pre_hook", "post_hook", "settlement_hook"],
    hook_name: str,
    run_id: str,
    task_id: str,
    cycle: int,
    capture_dir: str,
    context_fields: dict,
    env_overlay: dict[str, str],
    cwd: str,
    artifact_store: ArtifactStore,
    start: float,
) -> HookOutcome:
    Path(capture_dir).mkdir(parents=True, exist_ok=True)
    context_path = os.path.join(capture_dir, _CONTEXT_FILE)
    # Absolute path that does NOT yet exist -- the hook script may (optionally) write its
    # JSON verdict detail here; existence is what run_hook checks AFTER the subprocess ends.
    result_path = os.path.join(capture_dir, _RESULT_FILE)
    Path(context_path).write_text(
        json.dumps({"version": _CONTEXT_VERSION, **context_fields}, indent=2),
        encoding="utf-8",
    )

    env = {
        **os.environ,
        **env_overlay,
        "AO_RUN_ID": run_id,
        "AO_TASK_ID": task_id,
        "AO_HOOK_KIND": kind,
        "AO_HOOK_NAME": hook_name,
        "AO_DISPATCH_CYCLE": str(cycle),
        "AO_HOOK_CONTEXT_PATH": context_path,
        "AO_HOOK_RESULT_PATH": result_path,
    }

    try:
        proc = subprocess.run(  # noqa: S603 -- shell=False, argv from HookSpec.command, bounded
            hook.command,
            shell=False,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            timeout=hook.timeout_seconds,
            capture_output=True,
            text=True,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else None
        stderr = exc.stderr if isinstance(exc.stderr, str) else None
        _write_capture(capture_dir, stdout, stderr)
        return HookOutcome(
            kind=kind,
            hook_name=hook_name,
            status="timed_out",
            exit_code=None,
            duration_ms=_elapsed_ms(start),
            error=f"hook {hook_name!r} timed out after {hook.timeout_seconds}s",
        )
    except OSError as exc:
        # Missing/non-executable command, bad cwd, etc. -- never a silent pass.
        return HookOutcome(
            kind=kind,
            hook_name=hook_name,
            status="error",
            exit_code=None,
            duration_ms=_elapsed_ms(start),
            error=f"hook {hook_name!r} failed to start: {exc}",
        )

    _write_capture(capture_dir, proc.stdout, proc.stderr)
    status: HookStatus = "passed" if proc.returncode == 0 else "failed"
    outcome = HookOutcome(
        kind=kind,
        hook_name=hook_name,
        status=status,
        exit_code=proc.returncode,
        duration_ms=_elapsed_ms(start),
    )
    # Only attempted when the subprocess actually produced a real exit code (D3: exit code
    # is the SOLE status source) -- a timed_out/error HookOutcome above returns early and
    # never reaches this, so `outcome.error` there stays the subprocess-level message set
    # above rather than being clobbered by "no result file" (error=None).
    return _populate_result_file(outcome, artifact_store, result_path)


def run_hook(
    hook: HookSpec,
    *,
    kind: Literal["pre_hook", "post_hook", "settlement_hook"],
    hook_name: str,
    run_id: str,
    task_id: str,
    cycle: int,
    capture_dir: str,
    context_fields: dict,
    env_overlay: dict[str, str],
    cwd: str,
    artifact_store: ArtifactStore,
) -> HookOutcome:
    """Run one lifecycle hook and return its `HookOutcome`. Never raises.

    A timeout, missing binary, or malformed `result.json` all degrade to a `HookOutcome`
    with `status`/`error` set -- never an uncaught exception reaching the worker thread's
    `ThreadPoolExecutor` future (HLD §5). The outer try/except below is a last-resort guard
    beyond the specific, expected failure modes `_run_hook_inner` already handles, so a truly
    unanticipated bug (e.g. a non-JSON-serializable `context_fields` value) still degrades to
    a `HookOutcome` rather than propagating.
    """
    start = time.monotonic()
    try:
        return _run_hook_inner(
            hook,
            kind=kind,
            hook_name=hook_name,
            run_id=run_id,
            task_id=task_id,
            cycle=cycle,
            capture_dir=capture_dir,
            context_fields=context_fields,
            env_overlay=env_overlay,
            cwd=cwd,
            artifact_store=artifact_store,
            start=start,
        )
    except Exception as exc:  # noqa: BLE001 -- never-raises contract, see docstring above
        logger.exception("hooks.run_hook: unexpected error running hook %r", hook_name)
        return HookOutcome(
            kind=kind,
            hook_name=hook_name,
            status="error",
            duration_ms=_elapsed_ms(start),
            error=f"hook {hook_name!r} raised an unexpected error: {exc}",
        )
