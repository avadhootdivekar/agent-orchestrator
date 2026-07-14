# TASK: T-p6m4qz-executor-claude-native

## Metadata
- Task ID: `T-p6m4qz-executor-claude-native`
- Epic ID: `E-m2k9pa-orchestrator-mvp-a`
- Owner: `TODO`
- Created: `2026-06-16`
- Last Updated: `2026-06-16`
- Status: `Draft`
- Estimate: `2 days`

## Requirements Mapping
- Requirement IDs: FR-1, FR-8, NFR-1, NFR-2

## Description
Implement the pluggable executor layer: `Executor` ABC, `ClaudeCliExecutor` (subprocess to `claude`/CLI,
paths-only), and `FakeExecutor` (deterministic, for tests). This is the **NFR-1 context-hygiene boundary**.
(LLD §4.)

## Acceptance Criteria
1. `Executor.execute(ctx: TaskContext) -> TaskResult`; `TaskContext` carries **paths/ids only** (no content field).
2. `ClaudeCliExecutor` renders prompt from `prompt_template` with `{instruction}{inputs}{outputs}{repos}` (paths only), builds argv from `command_template`, runs via `subprocess.run(timeout=...)`.
3. Non-zero exit → `failed`; `TimeoutExpired` → `timed_out`. Only exit code + truncated error tail are kept (no full stdout in state).
4. `FakeExecutor(behaviors, write_outputs)` is deterministic and can `touch` declared outputs.
5. Tests: a `RecordingExecutor` asserts no field of the received `TaskContext` equals any artifact file's contents (hygiene proof); template renders only paths.

## Risks
- `claude` CLI shape may change — keep it in templates/config, not hardcoded.

## Dependencies
- Upstream: T-r4t8wd (AgentSpec). Downstream: T-h7k3qm, T-b2n6rk.

## Pseudocode / Algorithm
```text
render prompt = template.format(instruction=path, inputs=paths, outputs=paths, repos=paths)
argv = [substitute {prompt} in command_template] + extra_args
res = subprocess.run(argv, timeout=ctx.timeout_seconds, capture_output=True)
map exit/timeout -> TaskResult(status, attempts, exit_code, error=tail)
```

## Schemas / Interface Notes
- Interface: `Executor` ABC; `TaskContext`/`TaskResult` (LLD §1).
- Triggers/events: N/A.
- Artifacts: receives input/output paths; never reads their contents.

## Handoff Boundary
- Upstream: AgentSpec + TaskContext. Downstream: engine calls execute().

## Artifacts
- Docs/comments: this folder.
