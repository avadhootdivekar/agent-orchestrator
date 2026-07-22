"""Subject adapters -- run one bench task with one subject-under-test (design doc §4.2).

`Subject.run(task, ctx) -> SubjectResult` is the seam the runner (T-Run5Tz) dispatches
through via `SUBJECT_REGISTRY[subject_spec.type]`. Three concrete subjects register
themselves into that registry at import time (module bottom):

  ClaudeCliSubject  -- bare `claude -p ...`, the baseline. Reuses core's
                       `executors.claude_cli.parse_usage_and_429` (which itself calls
                       `extract_result_event`) for cost/token extraction from the
                       captured stream, and `parse_transcript_events` for a best-effort
                       assistant-turn count -- the SAME parsing this repo's own
                       `ClaudeCliExecutor` uses, so bench numbers and `ao run` numbers
                       are computed identically (C4, ADR-0008).
  AoWorkflowSubject -- shells `uv run ao run ...` (an `ao` DAG is the system under
                       test) and reuses core's `models.compute_run_usage_totals` over
                       a `runstate.RunStateStore`-loaded `state.json` for cost/tokens.
  FakeSubject       -- deterministic, network-free, no subprocess -- the ONLY subject
                       CI ever runs for real.

Both real subjects are pure subprocess adapters: paths cross into the child process as
CLI args/env, never file *content* (NFR-1 hygiene, mirrored from the core executor).

Import direction (SI-1): this module imports read-only from core
(`executors.claude_cli`, `artifacts`, `models`, `runstate`) but core never imports
`bench/`.

Open contract note (flagged for T-Run5Tz, the runner, to honor -- see
`workspace.RunContext.subject_base_dir` docstring): `SubjectSpec.workflow` /
`.reposets` / `.agents` are documented by `subject.schema.json` as paths "relative to
this subject.json", but `bench/spec.py`'s `load_subject` (T-Sc4Hm2, not owned by this
task) does not resolve them against the subject.json's directory -- only `BenchTask`
paths get that treatment in `load_suite`. `AoWorkflowSubject.run` resolves them against
`ctx.subject_base_dir` when the runner sets it (falling back to an already-absolute
path, or the current working directory otherwise); an absolute path in the subject.json
itself always works with no runner change needed.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel

from ..artifacts import LocalFsArtifactStore
from ..executors.claude_cli import (
    STDERR_FILE,
    TRANSCRIPT_FILE,
    parse_transcript_events,
    parse_usage_and_429,
)
from ..models import compute_run_usage_totals
from ..runstate import RunStateStore
from .errors import SubjectError
from .registries import register_subject
from .spec import BenchTask, SubjectSpec
from .workspace import REPO_ROOT, RunContext

# --- claude_cli subject defaults (design doc §5.2 / §4.2) ---------------------------
# `bypassPermissions` so the agent may run tests, not just edit files (R1, learnings
# §21) -- committed claude_cli subject configs are expected to set this explicitly, but
# a spec that omits it still gets a working default rather than a Claude Code CLI
# prompt-for-permission hang under headless `-p`.
DEFAULT_CLAUDE_PERMISSION_MODE = "bypassPermissions"
DEFAULT_CLAUDE_PROMPT_TEMPLATE = "Solve the task described in {instruction}. Repo: {repo}."

# --- fake subject scripted effects (design doc §4.2) ---------------------------------
# `copy-solution` overlay-copies this marker directory (shipped INSIDE a test fixture,
# so it rides along with the normal fixture copytree) onto the workspace repo, then
# removes the marker so it never pollutes what the grader sees.
_SOLUTION_MARKER_DIR = ".bench-solution"
# Extra scripted_effect values (beyond copy-solution/noop) that report a subject-level
# outcome directly, with no repo mutation -- lets the runner/grader/reporting paths be
# exercised deterministically for the failed/timed_out/error branches without a real
# subprocess. Uses the EXISTING `scripted_effect` schema field (subject.schema.json is
# not owned by this task and is not touched -- SI-1).
_STATUS_SCRIPTED_EFFECTS: dict[str, Literal["failed", "timed_out", "error"]] = {
    "fail": "failed",
    "timeout": "timed_out",
    "error": "error",
}


class SubjectResult(BaseModel):
    """Outcome of one `Subject.run()` call (design doc §6, plus observability fields).

    `argv` / `resolved_model` / `resolved_permission_mode` are additive beyond the
    design doc §6 field list: the exact invocation that produced this result, recorded
    per this task's determinism/observability requirement so a `config_fingerprint`
    (T-Run5Tz/T-Rpt3Wq) can be derived from what actually ran, not just the spec.
    """

    status: Literal["succeeded", "failed", "timed_out", "error"]
    wall_clock_seconds: float
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    attempts: int | None = None
    turns: int | None = None
    capture_dir: str
    raw_error: str | None = None
    argv: list[str] = []
    resolved_model: str | None = None
    resolved_permission_mode: str | None = None


class Subject(ABC):
    """One system-under-test adapter (design doc §4.2, §6)."""

    def __init__(self, spec: SubjectSpec) -> None:
        self.spec = spec

    @abstractmethod
    def run(self, task: BenchTask, ctx: RunContext) -> SubjectResult:
        """Run *task* inside the workspace described by *ctx*; return its outcome.

        Never raises for an ordinary run-time failure (non-zero exit, timeout, missing
        `claude`/`uv` binary, quota exhaustion) -- those all map to a `SubjectResult`
        with an appropriate `status`. May raise `SubjectError` for a condition the
        runner itself should treat as this task erroring out (e.g. the ao_workflow
        subject's exactly-one-run-dir invariant, A4/R2) -- the runner (T-Run5Tz)
        catches `BenchError` around this call per the design doc §4.5 pseudocode.
        """
        raise NotImplementedError


def _status_from(returncode: int, timed_out: bool) -> Literal["succeeded", "failed", "timed_out"]:
    """Map a subprocess outcome to a subject status (design doc §4.2 pseudocode)."""
    if timed_out:
        return "timed_out"
    return "succeeded" if returncode == 0 else "failed"


def _run_with_timeout(
    argv: list[str],
    *,
    cwd: str | Path,
    stdout_path: Path,
    stderr_path: Path,
    timeout_seconds: int,
    env: dict[str, str] | None = None,
) -> tuple[int, bool]:
    """Spawn *argv*, OS-level-capturing stdout/stderr to files; enforce *timeout_seconds*.

    Shared by `ClaudeCliSubject` and `AoWorkflowSubject` (both real subjects need
    identical spawn/capture/timeout/kill semantics; extracted here per CLAUDE.md's
    "no duplicate logic" rule).

    On timeout, the whole process GROUP is killed (`start_new_session=True` gives the
    child its own group; `os.killpg(..., SIGKILL)` kills it and everything it spawned)
    rather than just the immediate child -- a hung `claude`/`ao` subprocess may itself
    have spawned tool-use children. Because the capture files are opened before the
    child starts writing (OS-level redirect), a killed/timed-out run still leaves a
    partial transcript on disk (mirrors `ClaudeCliExecutor`'s own capture strategy).
    """
    timed_out = False
    with (
        open(stdout_path, "w", encoding="utf-8") as out_f,
        open(stderr_path, "w", encoding="utf-8") as err_f,
    ):
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=out_f,
            stderr=err_f,
            env=env,
            start_new_session=True,
        )
        try:
            proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass  # already exited between the timeout firing and the killpg call
            proc.wait()
    # Always set by this point -- both wait() calls above run to completion.
    return cast(int, proc.returncode), timed_out


class ClaudeCliSubject(Subject):
    """Bare `claude -p` baseline subject (design doc §4.2)."""

    def run(self, task: BenchTask, ctx: RunContext) -> SubjectResult:
        # `task` itself is unused here -- the prompt only ever references paths
        # already resolved onto `ctx` (NFR-1); kept in the signature to match the
        # `Subject.run(task, ctx)` protocol every subject implements.
        if shutil.which("claude") is None:
            return SubjectResult(
                status="error",
                wall_clock_seconds=0.0,
                capture_dir=ctx.capture_dir,
                raw_error="`claude` CLI not found on PATH",
            )

        prompt_template = self.spec.prompt_template or DEFAULT_CLAUDE_PROMPT_TEMPLATE
        prompt = prompt_template.format(instruction=ctx.instruction_path, repo=ctx.repo_dir)
        permission_mode = self.spec.permission_mode or DEFAULT_CLAUDE_PERMISSION_MODE
        # ctx.max_turns is a bench-run-level override (e.g. `ao-bench run --max-turns`);
        # spec.max_turns is this subject's own committed default. CLI/run-level wins,
        # mirroring core cli.py's own "CLI > spec > unset" precedence for --max-turns.
        max_turns = ctx.max_turns if ctx.max_turns is not None else self.spec.max_turns

        argv = ["claude", "-p", prompt]
        if self.spec.model:
            argv += ["--model", self.spec.model]
        argv += [
            "--permission-mode",
            permission_mode,
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        argv += list(self.spec.extra_args)
        if max_turns:
            argv += ["--max-turns", str(max_turns)]

        capture_dir = Path(ctx.capture_dir)
        capture_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = capture_dir / TRANSCRIPT_FILE
        stderr_path = capture_dir / STDERR_FILE

        t0 = time.monotonic()
        try:
            returncode, timed_out = _run_with_timeout(
                argv,
                cwd=ctx.repo_dir,
                stdout_path=transcript_path,
                stderr_path=stderr_path,
                timeout_seconds=ctx.timeout_seconds,
            )
        except OSError as exc:
            return SubjectResult(
                status="error",
                wall_clock_seconds=time.monotonic() - t0,
                capture_dir=ctx.capture_dir,
                raw_error=f"Failed to spawn `claude`: {exc}",
                argv=argv,
                resolved_model=self.spec.model,
                resolved_permission_mode=permission_mode,
            )
        wall = time.monotonic() - t0

        transcript_text = transcript_path.read_text(encoding="utf-8", errors="replace")
        stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")

        usage = parse_usage_and_429(
            stdout=transcript_text,
            stderr=stderr_text,
            returncode=returncode,
            now_epoch=time.time(),
        )
        turns = sum(
            1 for e in parse_transcript_events(transcript_text) if e.get("type") == "assistant"
        )

        if usage["claude_quota_exhausted"]:
            # Distinct from a provider 429 or an ordinary failure (AC5): never counted
            # as a solve, surfaced as its own error reason.
            return SubjectResult(
                status="error",
                wall_clock_seconds=wall,
                cost_usd=usage["cost_usd"],
                attempts=1,
                turns=turns,
                capture_dir=ctx.capture_dir,
                raw_error=(
                    "claude usage-quota exhausted (see capture/transcript.jsonl + stderr.txt)"
                ),
                argv=argv,
                resolved_model=self.spec.model,
                resolved_permission_mode=permission_mode,
            )

        status = _status_from(returncode, timed_out)
        raw_error: str | None = None
        if status == "failed":
            raw_error = (stderr_text or transcript_text or "")[-500:] or (
                f"claude exited {returncode}"
            )
        elif status == "timed_out":
            raw_error = f"claude timed out after {ctx.timeout_seconds}s"

        return SubjectResult(
            status=status,
            wall_clock_seconds=wall,
            cost_usd=usage["cost_usd"],
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cache_creation_input_tokens=usage["cache_creation_input_tokens"],
            cache_read_input_tokens=usage["cache_read_input_tokens"],
            attempts=1,
            turns=turns,
            capture_dir=ctx.capture_dir,
            raw_error=raw_error,
            argv=argv,
            resolved_model=self.spec.model,
            resolved_permission_mode=permission_mode,
        )


def _resolve_template_path(value: str, base_dir: Path) -> Path:
    """Resolve a subject-spec template path (workflow/reposets/agents) against *base_dir*."""
    p = Path(value)
    return p if p.is_absolute() else (base_dir / p).resolve()


def _render_reposet(template_path: Path, *, workspace: str, repo_dir: str) -> dict:
    """Render a reposet TEMPLATE for one task run (design doc §4.2 pseudocode).

    For every named repo_set in the template: point `workspace_root` at this task's
    workspace, and the PRIMARY repo (`repos[0]`) at this task's mutable fixture copy.
    Any additional `repos[1:]` (support repos) are left as authored in the template --
    the MVP `ao-epic` workflow template is expected to declare exactly one repo per
    repo_set (Q1, T-Fx6Dp0), but this does not hard-require it.
    """
    data = json.loads(template_path.read_text())
    for repo_set in data.get("repo_sets", {}).values():
        repo_set["workspace_root"] = workspace
        repos = repo_set.get("repos") or []
        if repos:
            repos[0]["path"] = repo_dir
    return data


def _latest_run_dir(runs_root: Path) -> Path:
    """Return the single run dir under *runs_root*; raise SubjectError otherwise.

    ASSUMPTION A4 / Risk R2: a fresh workspace per (subject, task) should produce
    exactly one `run_id` under `<ws>/.orchestrator/runs/`. Zero or more than one means
    cost attribution can't be trusted -- raised, not guessed at, so the runner records
    this task as `status="error"` (design doc §4.5) rather than silently picking one.
    """
    if not runs_root.is_dir():
        raise SubjectError(f"ao_workflow subject: no run directory produced under {runs_root}")
    candidates = sorted(p for p in runs_root.iterdir() if p.is_dir())
    if len(candidates) != 1:
        raise SubjectError(
            f"ao_workflow subject: expected exactly one run dir under {runs_root}, found "
            f"{len(candidates)}: {[c.name for c in candidates]} (A4/R2 -- a fresh workspace "
            "per (subject, task) should yield a single run_id)"
        )
    return candidates[0]


class AoWorkflowSubject(Subject):
    """An `ao` DAG is the system under test (design doc §4.2)."""

    def run(self, task: BenchTask, ctx: RunContext) -> SubjectResult:
        # `task`'s instruction/fixture are already materialized into ctx.repo_dir by
        # workspace.py; `task` itself is unused beyond that (see Subject.run protocol).
        missing = [f for f in ("workflow", "reposets", "agents") if getattr(self.spec, f) is None]
        if missing:
            return SubjectResult(
                status="error",
                wall_clock_seconds=0.0,
                capture_dir=ctx.capture_dir,
                raw_error=(
                    f"ao_workflow subject {self.spec.id!r} missing required field(s): {missing}"
                ),
            )
        if shutil.which("uv") is None:
            return SubjectResult(
                status="error",
                wall_clock_seconds=0.0,
                capture_dir=ctx.capture_dir,
                raw_error="`uv` not found on PATH (needed for `uv run ao run`, ASSUMPTION A2)",
            )

        base_dir = Path(ctx.subject_base_dir) if ctx.subject_base_dir else Path.cwd()
        # mypy: the `missing` check above guarantees these three are non-None strings.
        workflow_path = _resolve_template_path(cast(str, self.spec.workflow), base_dir)
        reposets_template_path = _resolve_template_path(cast(str, self.spec.reposets), base_dir)
        agents_path = _resolve_template_path(cast(str, self.spec.agents), base_dir)
        for label, resolved in (
            ("workflow", workflow_path),
            ("reposets", reposets_template_path),
            ("agents", agents_path),
        ):
            if not resolved.is_file():
                return SubjectResult(
                    status="error",
                    wall_clock_seconds=0.0,
                    capture_dir=ctx.capture_dir,
                    raw_error=(
                        f"ao_workflow subject {self.spec.id!r}: {label} template not found: "
                        f"{resolved}"
                    ),
                )

        try:
            rendered = _render_reposet(
                reposets_template_path, workspace=ctx.workspace, repo_dir=ctx.repo_dir
            )
        except (json.JSONDecodeError, OSError) as exc:
            return SubjectResult(
                status="error",
                wall_clock_seconds=0.0,
                capture_dir=ctx.capture_dir,
                raw_error=f"failed to render reposet template {reposets_template_path}: {exc}",
            )
        rendered_path = Path(ctx.workspace) / "reposet.rendered.json"
        rendered_path.write_text(json.dumps(rendered, indent=2))

        argv = [
            "uv",
            "run",
            "ao",
            "run",
            "--workflow",
            str(workflow_path),
            "--reposets",
            str(rendered_path),
            "--agents",
            str(agents_path),
        ]
        # Deviation from design doc §4.2 pseudocode's `AO_BUDGET_TOTAL` env var: core
        # `cli.py`'s `run`/`resume` commands read budget ONLY as the `--budget-total`
        # CLI flag (no env var is wired for it -- see `_resolve_run_settings`), so it
        # is passed as argv here instead. ctx.budget_total (a bench-run-level override)
        # wins over spec.budget_total (this subject's own default), same CLI>spec
        # precedence as core's own `_build_effective_budget`.
        budget_total = ctx.budget_total if ctx.budget_total is not None else self.spec.budget_total
        if budget_total is not None:
            argv += ["--budget-total", str(budget_total)]

        env = dict(os.environ)
        env["AO_WORKSPACE_ROOT"] = ctx.workspace
        if self.spec.model:
            env["AO_MODEL"] = self.spec.model
        max_turns = ctx.max_turns if ctx.max_turns is not None else self.spec.max_turns
        if max_turns:
            env["AO_MAX_TURNS"] = str(max_turns)
        if self.spec.max_parallel:
            env["AO_MAX_PARALLEL"] = str(self.spec.max_parallel)

        capture_dir = Path(ctx.capture_dir)
        capture_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = capture_dir / "ao.stdout.txt"
        stderr_path = capture_dir / "ao.stderr.txt"

        t0 = time.monotonic()
        try:
            returncode, timed_out = _run_with_timeout(
                argv,
                # ASSUMPTION A2: invoke `uv run ao` from the repo root (the editable
                # install), never a stale global `ao` snapshot (learnings).
                cwd=REPO_ROOT,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                timeout_seconds=ctx.timeout_seconds,
                env=env,
            )
        except OSError as exc:
            return SubjectResult(
                status="error",
                wall_clock_seconds=time.monotonic() - t0,
                capture_dir=ctx.capture_dir,
                raw_error=f"Failed to spawn `uv run ao run`: {exc}",
                argv=argv,
                resolved_model=self.spec.model,
            )
        wall = time.monotonic() - t0

        status = _status_from(returncode, timed_out)

        # Exactly-one-run-dir is asserted regardless of `status`: a non-zero/timed-out
        # `ao run` can still have produced a partial run worth grading (design doc §4.2
        # edge cases: "subject non-zero exit -> status=failed, task still graded").
        run_dir = _latest_run_dir(Path(ctx.workspace) / ".orchestrator" / "runs")

        cost_usd: float | None = None
        input_tokens: int | None = None
        output_tokens: int | None = None
        cache_creation_input_tokens: int | None = None
        cache_read_input_tokens: int | None = None
        attempts: int | None = None
        try:
            store = LocalFsArtifactStore(ctx.workspace)
            state = RunStateStore(ctx.workspace, store).load(run_dir.name)
            totals = compute_run_usage_totals(state)
            cost_usd = totals.cost_usd
            input_tokens = totals.input_tokens
            output_tokens = totals.output_tokens
            cache_creation_input_tokens = totals.cache_creation_input_tokens
            cache_read_input_tokens = totals.cache_read_input_tokens
            attempts = sum(ts.attempts for ts in state.tasks.values())
        except FileNotFoundError:
            # design doc §4.2 edge case: missing state.json -> cost=None, still grade.
            pass

        raw_error: str | None = None
        if status == "failed":
            stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
            raw_error = stderr_text[-500:] or f"`uv run ao run` exited {returncode}"
        elif status == "timed_out":
            raw_error = f"`uv run ao run` timed out after {ctx.timeout_seconds}s"

        return SubjectResult(
            status=status,
            wall_clock_seconds=wall,
            cost_usd=cost_usd,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
            attempts=attempts,
            turns=None,  # ao_workflow's unit of work is tasks, not claude "turns"
            capture_dir=ctx.capture_dir,
            raw_error=raw_error,
            argv=argv,
            resolved_model=self.spec.model,
        )


def _apply_scripted_effect(effect: str | None, repo_dir: Path) -> None:
    """Apply a `FakeSubject` `scripted_effect` to *repo_dir* (design doc §4.2).

    Raises `SubjectError` for an unrecognized effect string -- a spec typo should fail
    loudly, not silently no-op.
    """
    if effect in (None, "noop"):
        return
    if effect == "copy-solution":
        solution_dir = repo_dir / _SOLUTION_MARKER_DIR
        if not solution_dir.is_dir():
            return
        for src in solution_dir.rglob("*"):
            if src.is_file():
                dest = repo_dir / src.relative_to(solution_dir)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
        shutil.rmtree(solution_dir)
        return
    raise SubjectError(f"FakeSubject: unknown scripted_effect {effect!r}")


class FakeSubject(Subject):
    """Deterministic, network-free subject -- the ONLY subject CI uses (design doc §4.2).

    `scripted_effect` (an existing `SubjectSpec` field -- no schema change, SI-1)
    drives behaviour:
      - `None` / `"noop"`   -- workspace left untouched (a real grader then fails).
      - `"copy-solution"`   -- overlay-copies `<repo_dir>/.bench-solution/**` onto
                               `<repo_dir>/` then removes the marker dir, so a real
                               grader can pass. The marker dir ships INSIDE the task
                               fixture and is carried into the workspace by the normal
                               fixture copytree (`workspace.materialize_workspace`) --
                               `FakeSubject` never reads outside `ctx.repo_dir`.
      - `"fail"`/`"timeout"`/`"error"` -- returns that `SubjectResult.status` directly,
                               with no repo mutation, so the runner/grader/reporting
                               paths can be exercised deterministically for every
                               subject-status branch without a real subprocess.

    No sleeping: `wall_clock_seconds` is the real (near-zero) time this in-process call
    took, never a scripted delay.
    """

    def run(self, task: BenchTask, ctx: RunContext) -> SubjectResult:
        # `task` carries nothing FakeSubject needs beyond what's already in
        # ctx.repo_dir (see Subject.run protocol).
        effect = self.spec.scripted_effect
        t0 = time.monotonic()

        if effect in _STATUS_SCRIPTED_EFFECTS:
            status = _STATUS_SCRIPTED_EFFECTS[effect]
            return SubjectResult(
                status=status,
                wall_clock_seconds=time.monotonic() - t0,
                cost_usd=self.spec.fake_cost,
                input_tokens=(self.spec.fake_tokens or {}).get("in"),
                output_tokens=(self.spec.fake_tokens or {}).get("out"),
                attempts=1,
                turns=None,
                capture_dir=ctx.capture_dir,
                raw_error=f"FakeSubject scripted_effect={effect!r}" if status == "error" else None,
                argv=[],
                resolved_model=self.spec.model,
            )

        _apply_scripted_effect(effect, Path(ctx.repo_dir))
        return SubjectResult(
            status="succeeded",
            wall_clock_seconds=time.monotonic() - t0,
            cost_usd=self.spec.fake_cost,
            input_tokens=(self.spec.fake_tokens or {}).get("in"),
            output_tokens=(self.spec.fake_tokens or {}).get("out"),
            attempts=1,
            turns=None,
            capture_dir=ctx.capture_dir,
            argv=[],
            resolved_model=self.spec.model,
        )


register_subject("claude_cli", ClaudeCliSubject)
register_subject("ao_workflow", AoWorkflowSubject)
register_subject("fake", FakeSubject)
