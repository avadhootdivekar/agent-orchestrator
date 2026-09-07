"""FakeExecutor — deterministic test executor that never spawns subprocesses."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from ..models import AgentSpec, TaskContext, TaskResult
from .base import Executor
from .prompt import build_prompt

Behavior = Literal["succeed", "fail", "timeout"]


def _write_capture_stubs(output_dir: str, task_id: str) -> None:
    """Create output_dir and write the capture artifacts (FR-4/FR-5 contract).

    Mirrors ClaudeCliExecutor's layout so integration/e2e tests can assert the
    same files regardless of executor: a multi-event ``transcript.jsonl`` (so
    "all turns captured" assertions have >1 event to check), the human-readable
    ``stdout.txt``, an empty ``stderr.txt``, and the terminal ``result.json``.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    result_event = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": f"fake output for {task_id}",
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }
    transcript_events = [
        {"type": "system", "subtype": "init", "task_id": task_id},
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": f"fake stdout for {task_id}"}]},
        },
        result_event,
    ]
    Path(os.path.join(output_dir, "transcript.jsonl")).write_text(
        "\n".join(json.dumps(e) for e in transcript_events) + "\n", encoding="utf-8"
    )
    Path(os.path.join(output_dir, "stdout.txt")).write_text(
        f"fake stdout for {task_id}\n", encoding="utf-8"
    )
    Path(os.path.join(output_dir, "stderr.txt")).write_text("", encoding="utf-8")
    Path(os.path.join(output_dir, "result.json")).write_text(
        json.dumps(result_event, indent=2), encoding="utf-8"
    )


class FakeExecutor(Executor):
    """Controllable executor for unit/integration tests.

    Parameters
    ----------
    behaviors:
        Mapping of task_id -> Behavior. Tasks not listed default to "succeed".
    write_outputs:
        When True (default) and behavior is "succeed", creates stub output files
        so artifact existence checks pass.
    manifest_payloads:
        Mapping of task_id -> list of artifact paths to write into the task's
        output_manifest_path (if declared). Only used when behavior is "succeed"
        and write_outputs is True.
    emit_payloads:
        Mapping of task_id -> dict to write as a task manifest
        (``{"tasks": [...]}`` JSON) at the task's ``task_manifest_path`` on
        success.  Drives Area-2 emit_tasks tests (T-5isej3).
    gate_payloads:
        Mapping of task_id -> list[bool]. On the K-th invocation of a gate task
        (1-indexed), writes ``{"continue": <bool>}`` to the task's
        ``gate_output_path`` for that iteration, letting a test script
        "continue, continue, stop".  The path is the LoopSpec.gate_output_path
        suffixed per iteration — the caller must pass in a dict that maps the
        suffixed task id to the gate bool for *that* invocation.  For simplicity,
        the mapping uses the base gate_task_id and a positional list of bools;
        the executor writes the correct path based on invocation count.
    token_outputs:
        Mapping of task_id -> token field dict.  On success, if the task_id is
        present, populates the token fields (input_tokens, output_tokens,
        cache_creation_input_tokens, cache_read_input_tokens) in the returned
        TaskResult and sets actuals_available=True.  (T-1m9744)
    rate_limit_tasks:
        Mapping of task_id -> retry_after_epoch (float | None).  On the *first*
        call for that task_id the executor returns a failed TaskResult with
        provider_rate_limited=True and the configured retry_after_epoch.
        Subsequent calls succeed normally (the engine handles re-runs).
        (T-1m9744)
    quota_exhausted_tasks:
        Mapping of task_id -> exhaust_count (int).  For the first *exhaust_count*
        calls the executor returns a failed TaskResult with claude_quota_exhausted=True.
        Subsequent calls succeed normally (the engine handles quota-wait + re-run).
    repo_writes:
        E-Wk9Tz3 (T-En8Hd4): mapping of task_id -> {repo_id: {relative_path: content}}.
        On success, for each entry writes *content* to ``ctx.repo_paths[repo_id]/
        relative_path`` (parents created as needed) -- lets a test simulate an agent that
        actually modifies a tracked file inside a repo (isolated or not), which
        ``output_paths``-only writes cannot: those may live outside every repo entirely.
        For an isolated task ``ctx.repo_paths[repo_id]`` already resolves into that task's
        worktree (the engine remaps it before dispatch -- R-19), so this needs no isolation
        awareness of its own. Additive and backward compatible: no entry, no effect.
    """

    def __init__(
        self,
        behaviors: dict[str, Behavior] | None = None,
        write_outputs: bool = True,
        manifest_payloads: dict[str, list[str]] | None = None,
        emit_payloads: dict[str, dict] | None = None,
        gate_payloads: dict[str, list[bool]] | None = None,
        token_outputs: dict[str, dict] | None = None,
        rate_limit_tasks: dict[str, float | None] | None = None,
        quota_exhausted_tasks: dict[str, int] | None = None,
        repo_writes: dict[str, dict[str, dict[str, str]]] | None = None,
    ) -> None:
        self._behaviors: dict[str, Behavior] = behaviors or {}
        self._write_outputs = write_outputs
        self._manifest_payloads: dict[str, list[str]] = manifest_payloads or {}
        self._emit_payloads: dict[str, dict] = emit_payloads or {}
        self._gate_payloads: dict[str, list[bool]] = gate_payloads or {}
        self._token_outputs: dict[str, dict] = token_outputs or {}
        self._rate_limit_tasks: dict[str, float | None] = rate_limit_tasks or {}
        self._quota_exhausted_tasks: dict[str, int] = quota_exhausted_tasks or {}
        self._repo_writes: dict[str, dict[str, dict[str, str]]] = repo_writes or {}
        # invocation counters for gate tasks: base_id -> count (0-indexed)
        self._gate_invocations: dict[str, int] = {}
        # task_ids that have already been 429'd once; subsequent calls succeed
        self._rate_limited_once: set[str] = set()
        # remaining quota-exhaustion counts per task_id
        self._quota_exhausted_remaining: dict[str, int] = dict(self._quota_exhausted_tasks)
        # task_id -> prompt this executor WOULD have sent, rendered through the same
        # executors.prompt.build_prompt the real ClaudeCliExecutor uses. Lets tests assert
        # on prompt assembly (e.g. that general instructions reached every task) without
        # spawning a subprocess. Recorded per invocation; the last one per task wins.
        self.prompts: dict[str, str] = {}
        # task_id -> the ctx.agent this executor received, i.e. AFTER the engine's
        # per-task model/effort/max_turns resolution (ADR-0003 decision 2,
        # `models.resolve_effective_agent`). Lets tests assert the resolved settings
        # reached dispatch without spawning a subprocess or a ClaudeCliExecutor-specific
        # argv capture. Recorded per invocation; the last one per task wins.
        self.resolved_agents: dict[str, AgentSpec] = {}
        # E-Wk9Tz3 (T-En8Hd4, R-19 proof): the FULL TaskContext this executor received,
        # per task_id -- the real object the engine built, not a helper's return value.
        # Lets a test assert every path category (instruction_path/input_paths/
        # output_paths/output_manifest_path/repo_paths/cwd) actually resolved inside an
        # isolated task's worktree, and that ctx.env carries the AO_* isolation vars.
        # Recorded per invocation; the last one per task wins.
        self.contexts: dict[str, TaskContext] = {}

    def execute(self, ctx: TaskContext) -> TaskResult:
        # Render through the SHARED builder so prompt-assembly assertions made against the
        # fake executor stay honest about what the real one would produce.
        self.prompts[ctx.task_id] = build_prompt(ctx)
        self.resolved_agents[ctx.task_id] = ctx.agent
        self.contexts[ctx.task_id] = ctx

        # --- Quota exhaustion simulation: fail N times, then succeed ---
        if self._quota_exhausted_remaining.get(ctx.task_id, 0) > 0:
            self._quota_exhausted_remaining[ctx.task_id] -= 1
            return TaskResult(
                task_id=ctx.task_id,
                status="failed",
                attempts=1,
                claude_quota_exhausted=True,
                error="fake quota exhaustion",
            )

        # --- 429 simulation: fail on first call, succeed on retry ---
        if ctx.task_id in self._rate_limit_tasks and ctx.task_id not in self._rate_limited_once:
            self._rate_limited_once.add(ctx.task_id)
            return TaskResult(
                task_id=ctx.task_id,
                status="failed",
                attempts=1,
                provider_rate_limited=True,
                provider_retry_after_epoch=self._rate_limit_tasks[ctx.task_id],
                error="fake 429",
            )

        behavior = self._behaviors.get(ctx.task_id, "succeed")

        # Always write capture stubs when output_dir is set (FR-4 contract).
        # This mirrors ClaudeCliExecutor behaviour so integration tests exercise
        # the same path regardless of which executor is used.
        if ctx.output_dir:
            _write_capture_stubs(ctx.output_dir, ctx.task_id)

        if behavior == "timeout":
            return TaskResult(
                task_id=ctx.task_id,
                status="timed_out",
                attempts=1,
                error="fake timeout",
                output_artifact_path=ctx.output_dir or None,
            )

        if behavior == "fail":
            return TaskResult(
                task_id=ctx.task_id,
                status="failed",
                attempts=1,
                exit_code=1,
                error="fake failure",
                output_artifact_path=ctx.output_dir or None,
            )

        # succeed
        if self._write_outputs:
            for path in ctx.output_paths:
                parent = os.path.dirname(path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(path, "w") as f:
                    f.write(f"fake output for {ctx.task_id}\n")

            # Write output manifest if declared and a payload is configured
            if ctx.output_manifest_path and ctx.task_id in self._manifest_payloads:
                artifacts = self._manifest_payloads[ctx.task_id]
                parent = os.path.dirname(ctx.output_manifest_path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(ctx.output_manifest_path, "w") as f:
                    json.dump({"artifacts": artifacts}, f)

            # Write task manifest (emit_payloads) for emit_tasks tasks (Area 2).
            # The engine passes the resolved task_manifest_path via ctx.task_manifest_path.
            if ctx.task_id in self._emit_payloads and ctx.task_manifest_path:
                payload = self._emit_payloads[ctx.task_id]
                parent = os.path.dirname(ctx.task_manifest_path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(ctx.task_manifest_path, "w") as f:
                    json.dump(payload, f)

            # E-Wk9Tz3 (T-En8Hd4): write into a repo path (isolated -> already remapped
            # into the task's worktree by the engine; not isolated -> the shared checkout).
            # {repo_id: {relative_path: content}} -- simulates an agent that modifies a
            # TRACKED file, which output_paths-only writes cannot (those commonly live
            # outside every repo entirely).
            if ctx.task_id in self._repo_writes:
                for repo_id, files in self._repo_writes[ctx.task_id].items():
                    repo_root = ctx.repo_paths.get(repo_id)
                    if repo_root is None:
                        continue
                    for rel_path, content in files.items():
                        full = os.path.join(repo_root, rel_path)
                        parent = os.path.dirname(full)
                        if parent:
                            os.makedirs(parent, exist_ok=True)
                        with open(full, "w") as f:
                            f.write(content)

        # Write gate verdict file (gate_payloads) on the K-th invocation of a gate task.
        # The base task id (without __iterN suffix) is used as the key in gate_payloads.
        # The engine passes the iteration-suffixed, resolved gate path via ctx.gate_output_path.
        base_id = ctx.task_id.split("__iter")[0]
        if base_id in self._gate_payloads and ctx.gate_output_path:
            invoc = self._gate_invocations.get(base_id, 0)
            verdicts = self._gate_payloads[base_id]
            if invoc < len(verdicts):
                parent = os.path.dirname(ctx.gate_output_path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(ctx.gate_output_path, "w") as f:
                    json.dump({"continue": verdicts[invoc]}, f)
            self._gate_invocations[base_id] = invoc + 1

        # Build success result; populate token fields if configured (T-1m9744).
        token_info = self._token_outputs.get(ctx.task_id)
        if token_info is not None:
            return TaskResult(
                task_id=ctx.task_id,
                status="succeeded",
                attempts=1,
                exit_code=0,
                output_artifact_path=ctx.output_dir or None,
                actuals_available=True,
                input_tokens=token_info.get("input_tokens"),
                output_tokens=token_info.get("output_tokens"),
                cache_creation_input_tokens=token_info.get("cache_creation_input_tokens"),
                cache_read_input_tokens=token_info.get("cache_read_input_tokens"),
            )

        return TaskResult(
            task_id=ctx.task_id,
            status="succeeded",
            attempts=1,
            exit_code=0,
            output_artifact_path=ctx.output_dir or None,
        )
