# Instruction: scope-review

This file is a **payload referenced by path** from `workflow.json`. The orchestrator never reads it;
only the assigned agent (in its own context window) does.

Task: inspect the repo and decide which modules need a deep review. The number of modules (N) is
not known ahead of time — that's the point of this task. Then write a task manifest to
`output/scope-manifest.json` with:

- One `subreview-<module>` task per module that needs review (agent: `reviewer`,
  `depends_on: ["scope-review"]`, `inputs: [<that module's path>]`,
  `outputs: ["output/reviews/<module>.md"]`).
- Exactly one **fixed-id** `aggregate-review` task (agent: `reviewer`,
  `depends_on: [<every subreview-<module> id above>]`,
  `inputs: [<every subreview output path above>]`, `outputs: ["output/review-summary.md"]`).
  This id and output path are a fixed contract the rest of the workflow depends on — they must
  not change between runs, even though the number of `subreview-*` tasks feeding into it does.

Always include the `aggregate-review` task in the manifest, even if zero modules need a deep
review — in that case give it `depends_on: ["scope-review"]`, no `inputs`, and have it write a
"no modules required deep review" note to `output/review-summary.md`. The downstream `publish`
task always reads from that fixed path, so it must exist on every run regardless of N.
