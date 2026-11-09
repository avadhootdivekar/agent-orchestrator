# TASK: T-Sn4Rec-liveness-kill-recovery-ladder

## Metadata
- Task ID: `T-Sn4Rec-liveness-kill-recovery-ladder`
- Epic ID: `E-Sn7tL2-sentinel-long-running-overseer`
- Owner: `unassigned`
- Created: `2026-11-09`
- Last Updated: `2026-11-09`
- Status: `Done`
- Estimate: `< 3 days`
- Phase: P1
- Agent: developer (+ tester)

## Requirements Mapping
- Requirement IDs: FR-4, FR-5, FR-6, FR-21; D2

## Description
1. Executor API: `TaskContext.cancel_event` + `activity_path`; `ClaudeCliExecutor` waits in short slices, kills the
process group on cancel, returns outcome `killed{reason, source}`; other executors: cancel best-effort, documented.
2. Liveness: tick samples size/mtime of `activity_path` → `state.tasks[tid].last_activity_at`, `running_seconds`.
3. New task status `killed` (decision §10.2) through runstate, reporting, UI types (display only here).
4. Mechanical ladder (EPIC FR-6 / §4.3): idle / stuck-factor / cost triggers; retry → escalate → fail; per-task
   `recovery{kills,last_reason,escalation_step}`; kills don't consume `max_attempts`; barrier tasks alarm-only by default;
   systemic alarm (N kills across tasks in window → stop killing, pause LLM, raise directive/alarm).
5. Inbox kind `kill` enabled (manual/ctl/dashboard; LLM source gated later in T-Sn9Pow).

## Acceptance Criteria
1. Real-subprocess test: a sleeping child tree is killed within 2 s of cancel; no orphan processes.
2. Stalled fake executor → killed after `idle_kill_minutes` (fixed clock), retried with retry-context note, escalated, then failed.
3. Stuck-factor needs ≥3 same-kind samples and floor minutes; tests for both.
4. Systemic alarm stops further kills; logged + ledgered (overseer workflows).
5. `ao ctl kill` e2e: manual kill → ladder; `--no-retry` → failed.
6. Resume mid-ladder preserves `recovery` state; no double kill.

## Risks
- False positives on silent long thinking — conservative defaults from spike data; kills never burn attempts.

## Dependencies
- T-Sn3Tck
- T-Sn0Spk

## Pseudocode / Algorithm
```text
tick: for tid in running: probe(activity_path); trig = idle|stuck|cost
  if trig and not protected(tid): request cancel(tid, reason)
settle(outcome=killed): r = ts.recovery; if r.kills < max: requeue with escalate[r.step]; else fail(normal path)
```

## Schemas / Interface Notes
- `TaskRunState.recovery`, `last_activity_at`, status `killed`
- executor outcome `killed`
- run-log events `sentinel.kill`, `sentinel.recovery.*`

## Handoff Boundary
- Upstream: T-Sn3Tck
- Downstream: T-Sn5Ui1 (Kill button, liveness column), T-Sn9Pow (LLM kill)

## Artifacts
- Docs/comments: `meta/tickets/E-Sn7tL2-sentinel-long-running-overseer/T-Sn4Rec-liveness-kill-recovery-ladder/`
- Large outputs: `N/A`

## Standard gates (every task)
- `pytest -q`, `ruff check . && ruff format --check .`, `mypy .` green; UI changes also `npm test` + `npm run build` in `ui/`.
- New behaviour off / absent settings ⇒ byte-identical behaviour (golden tests stay green).
- Work only in `./agent-orchestrator` on the epic branch; test against temp workspaces; never `install.sh` / restart `ao.service`.
- Update this TASK.md + STATUS.md and the epic STATUS.md rollup together.
