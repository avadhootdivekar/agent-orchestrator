# TASK: T-Sbj9Ka-subject-adapters

## Metadata
- Task ID: `T-Sbj9Ka-subject-adapters`
- Epic ID: `E-9Qk4Zt-agent-benchmark-harness`
- Owner: developer agent
- Created: 2026-07-22 · Last Updated: 2026-07-22
- Status: Done · Estimate: 3.0 days (≤3)

## Requirements Mapping
- FR-2, NFR-1 (core untouched), NFR-2 (repo-local workspaces).

## Description
Implement the `Subject` ABC + `SUBJECT_REGISTRY` and the three MVP subjects (design §4.2, §6). Each `run(task, ctx)` executes one task in a materialized workspace and returns a `SubjectResult` with status + cost/tokens. **Reuse core helpers** for cost/usage (C4/NFR-1): `parse_usage_and_429`/`extract_result_event` (claude subject) and `compute_run_usage_totals` + runstate load (ao subject) — imported read-only; nothing in core imports `bench/`.

Subjects:
- `ClaudeCliSubject` — `claude -p <prompt> --model <m> --permission-mode <pm> --output-format stream-json --verbose [--max-turns N]`, `cwd=repo_dir`, `stdin=DEVNULL`, OS-level capture to `capture/transcript.jsonl`. Cost/tokens via `parse_usage_and_429`. Default `permission_mode=bypassPermissions` (agent may run tests; learnings §21).
- `AoWorkflowSubject` — render per-task reposet (workspace_root + repo path), write instruction to `repo/INSTRUCTION.md`, `uv run ao run --workflow … --reposets <rendered> --agents <spec.agents>` with `AO_MODEL/AO_MAX_TURNS/AO_BUDGET_TOTAL/AO_MAX_PARALLEL/AO_WORKSPACE_ROOT` env, `cwd=REPO_ROOT`, `stdin=DEVNULL`. Cost/tokens via `compute_run_usage_totals(load_state(latest_run_dir))`.
- `FakeSubject` — deterministic, network-free: applies `scripted_effect` to `repo_dir` (e.g. `copy-solution` → real fix so a real grader passes; `noop` → grader fails) and returns scripted cost/tokens. This is the ONLY subject CI uses.

Also: workspace materialization helper (copy fixture → `ws/repo`, path-guarded under `playground/.tmp/bench/`).

## Acceptance Criteria
1. `FakeSubject` with `scripted_effect=copy-solution` on a tiny fixture → `SubjectResult(status="succeeded")` and the solution file present in `ws/repo`; with `noop` → fixture unchanged. Deterministic, no network. (unit)
2. Given `claude`/`ao` absent from PATH, When a real subject runs, Then `status="error"` with a clear message (not an unhandled exception).
3. `[real_llm]` Given `claude` available, When `ClaudeCliSubject(haiku)` runs one tiny task, Then `SubjectResult.cost_usd` and `input/output_tokens` are populated from the reused core parser and `capture/transcript.jsonl` exists.
4. `[real_llm]` `AoWorkflowSubject(haiku)` produces exactly one run dir under `ws/.orchestrator/runs`; `cost_usd`/tokens come from `compute_run_usage_totals` over its `state.json`.
5. Timeout → `status="timed_out"`, partial capture retained; `claude_quota_exhausted` signal → `status="error"` with a quota reason, not counted as a solve.
6. Workspace materialization refuses a fixture/target path that escapes `playground/.tmp/bench/` (`..`/symlink) with `SubjectError`. Committed fixture is never mutated (only the copy).
7. `grep` shows `bench/` imports from core but no core module imports `bench/`; `mypy`/`ruff` clean on `bench/`.

## Risks
- ao-subject cost attribution needs a single run_id (A4/R2) → `latest_run_dir` asserts exactly one run dir, else `SubjectError`.
- `--permission-mode` mismatch (R1) → default `bypassPermissions`; document per subject config.

## Dependencies
- T-Sc4Hm2 (models, registries, errors).

## Pseudocode / Algorithm
```text
PROTOCOL Subject: run(task, ctx) -> SubjectResult   # design §4.2 pseudocode
materialize(task, ws): assert_under(playground/.tmp/bench, ws); rmtree(ws) if exists; copytree(task.fixture, ws/repo)
status_from(rc, timed_out): timed_out→"timed_out"; rc==0→"succeeded"; else→"failed"
```

## Schemas / Interface Notes
- Interface: `Subject.run(task,ctx)->SubjectResult`; `SUBJECT_REGISTRY[type]`. Models: `SubjectResult`, `RunContext` (design §6).
- Reused core (import-only): `executors.claude_cli.parse_usage_and_429`, `.extract_result_event`; `models.compute_run_usage_totals`; `runstate` loader.
- Triggers/events: `bench.task.*` logs emitted by the runner (T-Run5Tz), not here.
- Artifacts: writes only under `playground/.tmp/bench/**` (gitignored).

## Handoff Boundary
- Upstream: T-Sc4Hm2 models/registries.
- Downstream: T-Run5Tz drives `Subject.run`; T-Fx6Dp0 authors the `ao-epic` workflow template + subject configs (Q1).

## Artifacts
- Docs/comments: this folder. Large outputs: none (workspaces are gitignored).
