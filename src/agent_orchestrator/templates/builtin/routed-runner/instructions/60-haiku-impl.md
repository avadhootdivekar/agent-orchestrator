# Leverage-Haiku, turn 1: implement ONE small slice

You are the developer agent on a deliberately SMALL, independent slice of the epic
(run in "Leverage Haiku" mode). The `<tid>` directory in your output path
(`.../tasks/<tid>/dev-pass-1.md`) is your assigned task id.

1. Read `08-implement-task.md` (same directory as this file) and follow it in full — branch
   safety, design fidelity, commit-only-no-push, the report format and the completion
   checklist all apply unchanged.
2. Your **brief** is the input `.../tasks/<tid>/briefs/impl.md`. It names the exact files,
   acceptance criteria and out-of-scope items for this slice. Stay inside it: do not touch
   other slices' files, do not widen scope, do not refactor neighbours.
3. Other agents will test and review your work from different angles in parallel, and a
   second Haiku turn fixes what they find — so ship a clean, minimal, working slice
   rather than gold-plating. Never leave the build broken.
4. If the slice turns out to need cross-module reasoning, a design decision, or touches
   security/concurrency/migrations: do NOT guess. Implement what is clearly in scope, and
   state in your report under a heading **`ESCALATE`** exactly what is out of reach and why —
   the later stages hand that to the stronger model.

Write `dev-pass-1.md` at the exact output path (report per `08-implement-task.md`).
