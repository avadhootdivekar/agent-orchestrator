# STATUS

- ID: `T-Fp9Hng-finplan-run-wedge-enospc-injection-cycle-and-operator-controls`
- Updated At: `2026-11-11`
- State: `Draft` (analysis/ticket only)
- Owner: `TODO`

## This update
- By: claude-sonnet-5-5 (finplan workspace session) · Role: other · Date: 2026-11-11 · Comment: Filed from the second incident in run `o-k6shov-ux-complete-500-20261110T113138Z`: E1 engine wedge after ENOSPC (54 min, looked like lock contention), E2 idle-kill blind to child work (w14-04 killed at 10:24:35), E3 self-dependency manifest crashed the engine silently + checker gaps, E4 no operator directive/stabilize lever (prompt locked, request-closeout only when failed and closeout-only), E5 heavy-resource policy is LLM prose only. Operator workarounds are in TASK.md. No engine code was read beyond the pointers listed; line numbers are approximate.

## Risks / Blockers
- Engine fixes need an owner and a human `install.sh`; see TASK.md Risks.
