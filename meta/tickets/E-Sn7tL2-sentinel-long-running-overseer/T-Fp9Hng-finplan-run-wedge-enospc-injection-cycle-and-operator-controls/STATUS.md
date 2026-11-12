# STATUS

- ID: `T-Fp9Hng-finplan-run-wedge-enospc-injection-cycle-and-operator-controls`
- Updated At: `2026-11-11`
- State: `Draft` (analysis/ticket only)
- Owner: `TODO`

## This update
- By: claude-sonnet-5-5 (finplan workspace session) · Role: other · Date: 2026-11-11 · Comment: Filed from the second incident in run `o-k6shov-ux-complete-500-20261110T113138Z`: E1 engine wedge after ENOSPC (54 min, looked like lock contention), E2 idle-kill blind to child work (w14-04 killed at 10:24:35), E3 self-dependency manifest crashed the engine silently + checker gaps, E4 no operator directive/stabilize lever (prompt locked, request-closeout only when failed and closeout-only), E5 heavy-resource policy is LLM prose only. Operator workarounds are in TASK.md. No engine code was read beyond the pointers listed; line numbers are approximate.

## Risks / Blockers
- Engine fixes need an owner and a human `install.sh`; see TASK.md Risks.
- 2026-11-12: E6 throughput section added (measured numbers, 5 proposals, repo split).
- 2026-11-12: E5/E6 narrowed per owner decision (test-scope/lanes/per-lane dirs -> fin_plan ticket T-t8Ls3q; resources -> T-Rs4Qv8; no template change).
- 2026-11-12: Reviewed against clone HEAD + run data. E5 lock origin corrected (Overseer-invented from the FinPlan prompt); done-check rule 7 named as the sweep driver; E2 ask 3 moved to run prompt/agent specs; E6 (b) resume-on-completion made optional. FinPlan-side fixes landed in fin_plan 30b3fa85.
