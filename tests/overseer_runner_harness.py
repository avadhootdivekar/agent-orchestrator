"""Harness for end-to-end overseer-runner template testing.

Provides ScriptedOverseerExecutor (a test-only FakeExecutor subclass that scripted
execution) and contract-entry builders for charter, briefs, breadcrumbs, digests,
verdicts, and manifests. Designed to be reused across multiple test files
(T-WruPiv, T-vmI0jI, etc.).

All JSON shapes are verified against overseer-contract.md.tmpl and the instruction
files to ensure exact compliance — the real hooks will reject malformed data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Final

from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import TaskContext, TaskResult


@dataclass
class ScriptEntry:
    """One scripted execution step: what files to write for a given task_id/attempt.

    - `output_map`: dict mapping declared output path names to content to write.
      Use None as a key to match all outputs (optional mapping for output_paths not in the dict).
    - `manifest`: manifest to write at task_manifest_path (for emit_tasks tasks).
    - `cost_usd`: cost to report in the result.
    """

    output_map: dict[str, Any] = field(default_factory=dict)  # output name or pattern -> content
    manifest: dict[str, Any] | None = None
    cost_usd: float = 0.0


#: Filename the executor NEVER writes, even if a script's `output_map` names it. `digest.json`
#: is a tool-owned artifact: the real `ov-ckpt-prep` pre_hook computes and writes it (budget/
#: stage/cadence/signals) BEFORE this executor runs (engine order: pre_hook -> executor ->
#: post_hook), and the manager agent only ever READS it (see
#: `.../instructions/20-checkpoint.md`) -- it is listed as a formal task `output` purely for the
#: engine's dependency tracking, not because the agent authors it. Clobbering it with scripted
#: content here would silently defeat the checker's stage-gated rules (OV-R11) and the stage
#: latch's own persistence (`read_prev_stage` reads `budget.stage` back off THIS file for the
#: next checkpoint), because the checker/next-checkpoint always re-reads digest.json from disk,
#: never the in-memory value the real pre_hook computed.
_TOOL_OWNED_DIGEST_FILENAME: Final[str] = "digest.json"

#: Allowlist for the side-channel write pass below: only these two known-legitimate control
#: artifacts (scenario (d)'s hold flow) may be written at an absolute-path key that isn't one of
#: the task's declared `outputs`. Anything else outside `ctx.output_paths` is refused so a
#: typo'd/mis-declared path from a future scenario surfaces as a missing-file test failure
#: instead of being silently absorbed.
_SIDE_CHANNEL_ALLOWED_FILENAMES: Final[frozenset[str]] = frozenset({"hold-request.json"})
_SIDE_CHANNEL_ALLOWED_PARENT_DIRS: Final[frozenset[str]] = frozenset({"needs-input"})


def _is_side_channel_allowed(path: Path) -> bool:
    """True iff *path* is one of the two known legitimate side-channel control artifacts."""
    return path.name in _SIDE_CHANNEL_ALLOWED_FILENAMES or path.parent.name in (
        _SIDE_CHANNEL_ALLOWED_PARENT_DIRS
    )


class ScriptedOverseerExecutor(FakeExecutor):
    """TEST-ONLY executor that intercepts FakeExecutor and injects domain-specific outputs.

    Usage:
    1. Define SCRIPT class attribute mapping task_ids to ScriptEntry objects.
    2. In output_map, use relative paths like "charter.json" that will be matched against
       ctx.output_paths (which include full workspace-relative paths).
    3. Install via monkeypatch.setattr before CliRunner.invoke.
    4. The executor overwrites stubs with real content from the script.

    The real FakeExecutor still creates stubs; this layer replaces them with real content,
    EXCEPT `digest.json` (see `_TOOL_OWNED_DIGEST_FILENAME`), which is left for the real
    `ov-ckpt-prep` pre_hook's own write to survive untouched.
    """

    SCRIPT: ClassVar[dict[str, ScriptEntry]] = {}

    def __init__(self) -> None:
        super().__init__()
        self._attempt_counter: dict[str, int] = {}

    def execute(self, ctx: TaskContext) -> TaskResult:
        """Intercept and augment the fake executor's output with scripted data."""
        # Snapshot any tool-owned digest.json BEFORE calling the parent FakeExecutor: its own
        # stub-writing pass unconditionally overwrites EVERY declared `ctx.output_paths` entry
        # (including digest.json) with generic "fake output for <task_id>" text -- clobbering
        # the REAL digest the `ov-ckpt-prep` pre_hook already wrote to disk before this executor
        # ran (engine order: pre_hook -> executor -> post_hook). FakeExecutor is production test
        # infra out of this task's scope, so we restore the real bytes below instead of changing
        # it.
        digest_snapshots: dict[str, bytes] = {}
        for output_path in ctx.output_paths:
            output_path_obj = Path(output_path)
            if output_path_obj.name == _TOOL_OWNED_DIGEST_FILENAME and output_path_obj.is_file():
                digest_snapshots[output_path] = output_path_obj.read_bytes()

        # Call the parent FakeExecutor to handle stubs and tracking.
        result = super().execute(ctx)

        # Restore the real pre_hook-written digest.json over the parent's stub clobber.
        for output_path, digest_content in digest_snapshots.items():
            Path(output_path).write_bytes(digest_content)

        # Bail if the task failed or we're not scripted for it.
        if result.status != "succeeded":
            return result

        # Track and retrieve this task's attempt number.
        self._attempt_counter[ctx.task_id] = self._attempt_counter.get(ctx.task_id, 0) + 1

        # Look up the script entry for this task.
        script_entry = self.SCRIPT.get(ctx.task_id)
        if script_entry is None:
            return result  # No script for this task, use fake defaults.

        written: set[str] = set()

        # Overwrite declared output files with scripted content -- EXCEPT digest.json, which is
        # tool-owned (see `_TOOL_OWNED_DIGEST_FILENAME`): the real pre_hook already wrote it
        # before this executor ran, and it must survive untouched.
        for output_path in ctx.output_paths:
            if Path(output_path).name == _TOOL_OWNED_DIGEST_FILENAME:
                continue

            # Try to find a matching script entry.
            # First, try exact filename match.
            filename = Path(output_path).name
            content = script_entry.output_map.get(filename)

            # If not found, try a suffix match (e.g., "charter.json" matches path ending with it)
            if content is None:
                for key, value in script_entry.output_map.items():
                    if output_path.endswith(key):
                        content = value
                        break

            # Write the content if found
            if content is not None:
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                if isinstance(content, dict):
                    Path(output_path).write_text(json.dumps(content))
                else:
                    Path(output_path).write_text(content)
                written.add(str(output_path))

        # Also write any output_map entries keyed by an absolute path that is NOT one of this
        # task's declared `outputs` -- side-channel control artifacts (e.g.
        # `control/hold-request.json`, `needs-input/<checkpoint>.md`) that a checkpoint writes
        # conditionally on its own verdict decision, and which the manifest deliberately never
        # declares as a formal output (only digest/verdict/report are declared, per
        # `build_checkpoint_task`). Without this, a hold-deciding checkpoint's scripted
        # hold-request.json/needs-input file is silently dropped because the loop above only
        # ever visits `ctx.output_paths`. Narrowly allowlisted (see
        # `_is_side_channel_allowed`) so a genuine forgot-to-declare-this-output bug still
        # surfaces as a missing-file test failure instead of being silently absorbed, and
        # `digest.json` is excluded here too since it is the same write-matching mechanism.
        for key, value in script_entry.output_map.items():
            key_path = Path(key)
            if not key_path.is_absolute() or key in written:
                continue
            if key_path.name == _TOOL_OWNED_DIGEST_FILENAME:
                continue
            if not _is_side_channel_allowed(key_path):
                continue
            key_path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(value, dict):
                key_path.write_text(json.dumps(value))
            else:
                key_path.write_text(value)

        # Write the task manifest if one is scripted (for emit_tasks tasks).
        if script_entry.manifest and ctx.task_manifest_path:
            Path(ctx.task_manifest_path).parent.mkdir(parents=True, exist_ok=True)
            Path(ctx.task_manifest_path).write_text(json.dumps(script_entry.manifest))

        # Update cost_usd in the result. `actuals_available` must also be set: the engine only
        # folds `result.cost_usd` into `TaskRunState.cumulative_cost_usd` (engine.py's
        # `_record_task_result`) when `actuals_available` is True (mirroring the real
        # ClaudeCliExecutor, which sets it whenever the CLI reported a `total_cost_usd`).
        # FakeExecutor's own default success path leaves it False, so without this a scripted
        # `cost_usd` is pure decoration: `state.spent` (and therefore every budget/stage
        # computation in `derive_budget`) stays $0 for the whole run regardless of what
        # scenarios script here.
        result.cost_usd = script_entry.cost_usd
        result.actuals_available = True

        return result


# ============================================================================================
# ===== Contract-entry builders (generate exactly-valid JSON shapes) =======================
# ============================================================================================


def build_charter(
    asks: list[dict[str, Any]],
    global_constraints: str = "",
    assumptions: str = "",
    out_of_scope: str = "",
    open_questions: str = "",
    prompt_path: Path | str | None = None,
) -> dict[str, Any]:
    """Build a contract-valid charter.json.

    Each ask in `asks` must have: ask_id, statement, deliverable_type, acceptance
    (list of texts), usable_bar, priority.

    If prompt_path is provided (e.g., instance_dir / "prompt.md"), reads it and
    computes the SHA256 hash. Otherwise uses a zero-filled default.
    """
    import hashlib

    if prompt_path:
        prompt_path = Path(prompt_path)
        prompt_sha256 = hashlib.sha256(prompt_path.read_bytes()).hexdigest()
    else:
        prompt_sha256 = "0" * 64

    return {
        "schema": "ao.overseer.charter/v1",
        "asks": asks,
        "global_constraints": global_constraints,
        "assumptions": assumptions,
        "out_of_scope": out_of_scope,
        "open_questions": open_questions,
        "prompt_sha256": prompt_sha256,
    }


def build_ask(
    ask_id: str,
    statement: str,
    deliverable_type: str,
    acceptance: list[str],
    usable_bar: str | list[str],
    priority: int,
) -> dict[str, Any]:
    """Build one ask entry for the charter.

    usable_bar can be either a str (normalized to a 1-element list) or list[str].
    """
    # Normalize usable_bar to a list
    if isinstance(usable_bar, str):
        usable_bar_list = [usable_bar]
    else:
        usable_bar_list = list(usable_bar)

    return {
        "ask_id": ask_id,
        "statement": statement,
        "deliverable_type": deliverable_type,
        "acceptance": [
            {"id": f"{ask_id}.{i + 1}", "text": text} for i, text in enumerate(acceptance)
        ],
        "usable_bar": usable_bar_list,
        "priority": priority,
    }


def build_brief(
    unit_id: str,
    wave: int,
    ask_ids: list[str],
    work_item: str,
    kind: str,
    goal: str,
    acceptance: list[str],
    prior_attempts: list[str] | None = None,
    approach_change: str | None = None,
) -> dict[str, Any]:
    """Build a contract-valid brief.json for a unit."""
    return {
        "schema": "ao.overseer.brief/v1",
        "unit_id": unit_id,
        "wave": wave,
        "ask_ids": ask_ids,
        "work_item": work_item,
        "kind": kind,
        "goal": goal,
        "acceptance": acceptance,
        "approach_change": approach_change,
        "prior_attempts": prior_attempts or [],
        "context_paths": [],
        "touches": [],
    }


def build_breadcrumb(
    unit_id: str,
    outcome: str = "done",
    verdict: str = "pass",
    summary: str = "done",
    acceptance_met: list[int] | None = None,
    changed_paths: list[str] | None = None,
    followups: list[str] | None = None,
) -> dict[str, Any]:
    """Build a contract-valid breadcrumb.json for a unit."""
    return {
        "schema": "ao.overseer.breadcrumb/v1",
        "unit_id": unit_id,
        "outcome": outcome,
        "verdict": verdict,
        "summary": summary,
        "changed_paths": changed_paths or [],
        "acceptance_met": acceptance_met or [],
        "followups": followups or [],
        "needs_input": False,
    }


def build_digest(
    checkpoint: str,
    stage: str,
    allowed_wave_size: int,
    must_close: bool = False,
    signals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a digest.json (tool-generated; for testing we mock it)."""
    return {
        "schema": "ao.overseer.digest/v1",
        "checkpoint": checkpoint,
        "stage": stage,
        "allowed_wave_size": allowed_wave_size,
        "cadence": {"allowed_wave_size": allowed_wave_size},
        "must_close": must_close,
        "allowed_decisions": ["continue", "redirect", "hold", "stabilize", "closeout"],
        "signals": signals or [],
    }


def build_verdict(
    checkpoint: str,
    stage: str,
    decision: str,
    rationale: str = "continued work",
    next_wave_goal: str = "continue implementation",
    alignment: list[dict[str, Any]] | None = None,
    criteria: list[dict[str, Any]] | None = None,
    signal_responses: list[dict[str, Any]] | None = None,
    hold_questions: list[str] | None = None,
) -> dict[str, Any]:
    """Build a verdict.json for a checkpoint."""
    return {
        "schema": "ao.overseer.verdict/v1",
        "checkpoint": checkpoint,
        "stage": stage,
        "decision": decision,
        "rationale": rationale,
        "next_wave_goal": next_wave_goal,
        "alignment": alignment or [],
        "criteria": criteria or [],
        "signal_responses": signal_responses or [],
        "hold_questions": hold_questions or [],
    }


def build_unit_task(
    unit_id: str,
    wave: int,
    agent: str,
    depends_on: list[str],
    brief_path: str,
    output_report: str,
    output_breadcrumb: str,
    instance_dir: str | None = None,
    kind: str = "implement",
    instruction: str | None = None,
) -> dict[str, Any]:
    """Build a unit entry for a task manifest.

    If instance_dir is provided, all paths (inputs, outputs) are prefixed with it.
    Otherwise, paths are used as-is (for backward compatibility with tests that
    pass pre-formatted paths).

    kind parameter derives the default instruction (implement->10-work-unit.md,
    stabilize->11-stabilize-unit.md, expand->30-expander.md, others->10-work-unit.md).
    instruction parameter overrides the derived default when explicitly provided.
    """
    # Derive instruction from kind if not explicitly provided
    if instruction is None:
        kind_map = {
            "implement": "workflows/overseer-runner/instructions/10-work-unit.md",
            "verify": "workflows/overseer-runner/instructions/10-work-unit.md",
            "research": "workflows/overseer-runner/instructions/10-work-unit.md",
            "design": "workflows/overseer-runner/instructions/10-work-unit.md",
            "stabilize": "workflows/overseer-runner/instructions/11-stabilize-unit.md",
            "expand": "workflows/overseer-runner/instructions/30-expander.md",
        }
        instruction = kind_map.get(kind, "workflows/overseer-runner/instructions/10-work-unit.md")

    # Determine how to handle paths based on instance_dir and whether paths are pre-formatted
    if instance_dir:
        # Ensure instance_dir doesn't end with /
        instance_dir = instance_dir.rstrip("/")
        # Prefix inputs that don't already have the instance_dir prefix
        inputs = [
            f"{instance_dir}/overseer-contract.md",
            f"{instance_dir}/outputs/charter.json",
            brief_path if brief_path.startswith(instance_dir) else f"{instance_dir}/{brief_path}",
        ]
        # Prefix outputs
        report_path = (
            output_report
            if output_report.startswith(instance_dir)
            else f"{instance_dir}/{output_report}"
        )
        breadcrumb_path = (
            output_breadcrumb
            if output_breadcrumb.startswith(instance_dir)
            else f"{instance_dir}/{output_breadcrumb}"
        )
        outputs = [report_path, breadcrumb_path]
    elif brief_path.startswith("workflows/"):
        # Paths are already prefixed (detected by workflows/ prefix)
        # Extract the instance dir prefix for the overseer files
        overseer_prefix = brief_path.split("/outputs/")[0]
        inputs = [
            f"{overseer_prefix}/overseer-contract.md",
            f"{overseer_prefix}/outputs/charter.json",
            brief_path,
        ]
        outputs = [output_report, output_breadcrumb]
    else:
        # Use bare paths (old behavior for backward compatibility)
        inputs = [
            "overseer-contract.md",
            "outputs/charter.json",
            brief_path,
        ]
        outputs = [output_report, output_breadcrumb]

    return {
        "id": unit_id,
        "agent": agent,
        "instruction": instruction,
        "depends_on": depends_on,
        "inputs": inputs,
        "outputs": outputs,
        "pre_hook": {"use": "ov-unit-gate"},
        "timeout_seconds": 7200,
        "skip_if_outputs_exist": True,
        "effort": "medium",
    }


def build_checkpoint_task(
    checkpoint_id: str,
    wave: int,
    depends_on: list[str],
    input_breadcrumbs: list[str],
    instance_dir: str,
) -> dict[str, Any]:
    """Build a checkpoint entry for a task manifest."""
    return {
        "id": checkpoint_id,
        "agent": "manager",
        "instruction": "workflows/overseer-runner/instructions/20-checkpoint.md",
        "depends_on": depends_on,
        "inputs": [
            f"{instance_dir}/overseer-contract.md",
            f"{instance_dir}/overseer-config.json",
            f"{instance_dir}/outputs/charter.json",
        ]
        + input_breadcrumbs,
        "outputs": [
            f"{instance_dir}/outputs/checkpoints/{checkpoint_id}/digest.json",
            f"{instance_dir}/outputs/checkpoints/{checkpoint_id}/verdict.json",
            f"{instance_dir}/outputs/checkpoints/{checkpoint_id}/report.md",
        ],
        "emit_tasks": True,
        "task_manifest_path": f"{instance_dir}/outputs/manifests/{checkpoint_id}.json",
        "pre_hook": {"use": "ov-ckpt-prep"},
        "post_hook": {"use": "ov-ckpt-check", "on_failure": "fail_task"},
        "timeout_seconds": 3600,
        "skip_if_outputs_exist": False,
        "effort": "high",
    }


def build_tail_tasks(
    final_checkpoint_id: str,
    instance_dir: str,
    final_push: bool = True,
) -> list[dict[str, Any]]:
    """Build the tail tasks (final-verify, closeout, and optional final-push)."""
    tasks = [
        {
            "id": "final-verify",
            "agent": "tester",
            "instruction": "workflows/overseer-runner/instructions/40-final-verify.md",
            "depends_on": [final_checkpoint_id],
            "inputs": [
                f"{instance_dir}/outputs/charter.json",
                f"{instance_dir}/outputs/checkpoints/{final_checkpoint_id}/verdict.json",
            ],
            "outputs": [f"{instance_dir}/outputs/final/verify.md"],
            "timeout_seconds": 7200,
            "skip_if_outputs_exist": False,
        },
        {
            "id": "closeout",
            "agent": "manager",
            "instruction": "workflows/overseer-runner/instructions/41-closeout.md",
            "depends_on": ["final-verify"],
            "inputs": [
                f"{instance_dir}/outputs/charter.json",
                f"{instance_dir}/outputs/final/verify.md",
            ],
            "outputs": [f"{instance_dir}/outputs/final/closeout.md"],
            "timeout_seconds": 3600,
            "skip_if_outputs_exist": False,
        },
    ]
    if final_push:
        tasks.append(
            {
                "id": "final-push",
                "agent": "git-operator",
                "instruction": "workflows/overseer-runner/instructions/90-final-push.md",
                "depends_on": ["closeout"],
                "inputs": [f"{instance_dir}/outputs/final/closeout.md"],
                "outputs": [f"{instance_dir}/outputs/final/push-report.md"],
                "timeout_seconds": 2400,
                "skip_if_outputs_exist": False,
                "isolation": "none",
            }
        )
    return tasks


def build_hold_request(
    checkpoint_id: str,
    questions: list[str],
    needs_input_path: str = "",
) -> dict[str, Any]:
    """Build a hold-request.json for when a checkpoint decides to hold."""
    return {
        "schema": "ao.overseer.hold/v1",
        "checkpoint": checkpoint_id,
        "created_at": "2026-01-01T00:00:00Z",
        "questions": questions,
        "needs_input_path": needs_input_path,
    }
