# Leverage-Haiku: aggregate all task pipelines

You are the manager agent closing the fan-out of a "Leverage Haiku" run. Follow
`12-aggregate.md` (same directory) — verification-and-reporting only, never soften a
failure — with these differences in what to read per task directory `.../tasks/<tid>/`:

- **Ladder tasks** (have `dev-pass-1.md`, `test-<angle>.md`, `review-<lens>.md`,
  `dev-pass-2.md`, `test-pass-2.md`, and `test-pass-3.md`): the final state is
  `test-pass-3.md`. If it is the "SKIPPED" stub, the final state is `test-pass-2.md`
  (verdict `CLEAN`). Unresolved MUST-FIX = those still open after `dev-pass-3.md`
  (or `dev-pass-2.md` when escalation was skipped).
- **Critical tasks** (standard 5-entry pipeline, final state `test-pass-2.md`): treat exactly as `12-aggregate.md` describes.

Additionally record, per task, **how far it climbed**: `Haiku-closed` (verify CLEAN),
`escalated` (Sonnet ran), or `critical` (never on Haiku), and a tally of the three across
the epic — this is the run's measure of how well Leverage Haiku worked. Write
`all-tasks-complete.md` at the exact output path as `12-aggregate.md` specifies.
