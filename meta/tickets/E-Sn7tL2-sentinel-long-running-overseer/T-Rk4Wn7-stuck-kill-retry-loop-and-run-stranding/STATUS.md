# STATUS

- ID: `T-Rk4Wn7-stuck-kill-retry-loop-and-run-stranding`
- Updated At: `2026-11-11`
- State: `Draft`
- Owner: `TODO`

## This update
- By: claude-sonnet-5-5 (finplan workspace session) · Role: other · Date: 2026-11-10 · Comment: Filed from a live overseer run. D1 (retry keeps original `started_at` → 3 kills in 32 s) is confirmed from `state.json`/engine log; the retry code path itself was not read. D2 confirmed by threshold arithmetic. D3 (stranded ready tasks) is UNCONFIRMED. D4 confirmed from the g01 transcript. Operator workaround applied in the live run (stuck thresholds widened, `max_recovery_kills_per_task 1`, `systemic_kill_alarm 5`, `llm.agent` set to a permission-skipping agent) and the run resumed.

- By: developer · Role: developer · Date: 2026-11-11 · Comment: D1-D4 + CTL implemented and committed; D2 committed; A3 Opus review fixes H1/H2/M1/M2/M3/L1/L2/L3/L5/L6 done (mutation-checked H1, M2); L4 (T4 sibling isolation test) and the owner's `fail_fast` opt-out decision remain open. See TASK.md.

## Evidence
- See TASK.md "Evidence (where to look)".

## Risks / Blockers
- None for filing. Live engine is a non-editable `uv tool` snapshot; fixes need the human install step.

## Next actions
1. Reproduce D1 and D3 in a temp workspace.
2. Fix D1 first (smallest, highest impact), then D4, D2, D3.
