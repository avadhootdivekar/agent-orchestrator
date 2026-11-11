# STATUS

- ID: `T-Rk4Wn7-stuck-kill-retry-loop-and-run-stranding`
- Updated At: `2026-11-11`
- State: `Done` (open owner question: `defaults.fail_fast` opt-out; not a blocker)
- Owner: `TODO`

## This update
- By: claude-sonnet-5-5 (finplan workspace session) · Role: other · Date: 2026-11-10 · Comment: Filed from a live overseer run. D1 (retry keeps original `started_at` → 3 kills in 32 s) is confirmed from `state.json`/engine log; the retry code path itself was not read. D2 confirmed by threshold arithmetic. D3 (stranded ready tasks) is UNCONFIRMED. D4 confirmed from the g01 transcript. Operator workaround applied in the live run (stuck thresholds widened, `max_recovery_kills_per_task 1`, `systemic_kill_alarm 5`, `llm.agent` set to a permission-skipping agent) and the run resumed.

- By: developer · Role: developer · Date: 2026-11-11 · Comment: D1-D4 + CTL implemented and committed; D2 committed; A3 Opus review fixes H1/H2/M1/M2/M3/L1/L2/L3/L5/L6 done (mutation-checked H1, M2); L4 (T4 sibling isolation test) and the owner's `fail_fast` opt-out decision remain open. See TASK.md.

- By: tester · Role: tester · Date: 2026-11-11 · Comment: w21-07 independent A3 verification on HEAD 19b17db: see outputs/waves/w21/w21-07-a3-final-verify.md of the overseer run; header State/Status corrected from stale `Draft` to `Done`.

- By: developer · Role: developer · Date: 2026-11-11 · Comment: A3 closed except one owner decision. L4 (T4 conflict + independent-sibling isolation e2e, `tests/test_e2e_isolation_sibling_failure.py`) done, mutation-checked against D3. State: D1 done, D2 done, D3 done (continue-on-failure default), D4 done, CTL-SCHEMA investigated + L5 shared constants (auto-pause/sidecar-in-pack idea left open). **OPEN OWNER DECISION: add `defaults.fail_fast` opt-out for D3 (needs engine.py/spec change) or keep continue-on-failure unconditional.** Recommendation: keep. See TASK.md "A3 close-out".

## Evidence
- See TASK.md "Evidence (where to look)".

## Risks / Blockers
- None. Open owner question (not a blocker): `defaults.fail_fast` opt-out for D3 (recommendation: keep continue-on-failure). Live engine is a non-editable `uv tool` snapshot; fixes need the human install step.

## Next actions
1. Owner: decide `defaults.fail_fast` (TASK.md "A3 close-out").
2. Human: run `install.sh` to promote to the live engine; smoke-test one real LLM-lane generation (D4 flag set unverified live).
3. Optional: stop counting CTL-SCHEMA note rejections toward auto-pause (followups 3/4).
