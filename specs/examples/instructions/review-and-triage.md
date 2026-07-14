# Instruction: review-and-triage

This file is a **payload referenced by path** from `workflow.json`. The orchestrator never reads it;
only the assigned agent (in its own context window) does.

Task: review the code, write findings to `output/review.md`, then decide which findings are
auto-fixable. Write a task manifest to `output/fix-manifest.json` with:

- Per auto-fixable finding, a pair of tasks sharing a `<finding-id>` suffix:
  - `fix-<finding-id>` (agent: `developer`, `depends_on: ["review"]`, `inputs: ["output/review.md"]`,
    `outputs: ["output/fixes/<finding-id>.md"]`) — implements the fix.
  - `verify-<finding-id>` (agent: `tester`, `depends_on: ["fix-<finding-id>"]`,
    `inputs: ["output/fixes/<finding-id>.md"]`, `outputs: ["output/verified/<finding-id>.md"]`) —
    builds/tests the fix.
- Exactly one **fixed-id** `verify-summary` task (agent: `tester`,
  `depends_on: [<every verify-<finding-id> id above>]`,
  `inputs: [<every verify output path above>]`, `outputs: ["output/verify-summary.md"]`).
  This id and output path are a fixed contract `final-report` depends on — they must not change
  between runs, even though the number of findings does.

Always include the `verify-summary` task, even if zero findings are auto-fixable — in that case
give it `depends_on: ["review"]`, no `inputs`, and have it write a "no auto-fixable findings" note
to `output/verify-summary.md`. `final-report` always reads from that fixed path, so it must exist
on every run regardless of finding count.
