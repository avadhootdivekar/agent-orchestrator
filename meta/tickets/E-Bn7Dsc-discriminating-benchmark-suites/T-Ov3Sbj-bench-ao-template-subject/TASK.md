# TASK: T-Ov3Sbj-bench-ao-template-subject

## Metadata
- Task ID: `T-Ov3Sbj-bench-ao-template-subject`
- Epic ID: `E-Bn7Dsc-discriminating-benchmark-suites`
- Owner: Claude (developer) for avadhoot
- Created: 2026-10-24
- Last Updated: 2026-10-24
- Status: Done
- Estimate: < 1 day

## Description
Owner ask (2026-10-24): benchmark an `ao` **overseer** subject on Sonnet only next to bare haiku/sonnet/opus.
The bench's `ao_workflow` subject could only run a fixed `workflow.json` (`ao run --workflow`), while
`overseer-runner` is a builtin *template* that `ao new` instantiates (workflow + tools + instructions + hooks)
into a workspace and whose first stage branches the repo off `main`.

## Acceptance Criteria
1. `ao_workflow` subjects accept `template`, `template_params`, `prompt_file`, `init_git` (schema + `SubjectSpec`);
   template mode runs `uv run ao new <template> bench --workspace <ws> --param repo_set=<first repo_set> --param ...
   --prompt-file <prompt> --run --reposets <rendered> --agents <agents>`; cost/tokens still come from the single
   run dir under `<ws>/.orchestrator/runs/`.
2. `init_git`: repo/ becomes a one-commit git repo (`.claude/` excluded) with a local bare `origin` at
   `<ws>/_origin.git` tracking `main` (outside the graded repo).
3. `benchmarks/subjects/ao-overseer-sonnet.json`: every agent on Sonnet (`AO_MODEL`, no per-agent/per-unit model
   override, no leverage-haiku), thin role prompts, prompt.md under the 600-char wrapper budget, per-task budget
   `run_budget_usd=3`, `wave_size=3`, `max_waves=2`, `final_push=false`.
4. Fake-tier tests; a real smoke shows the full overseer path (branch-off → intake → wave → checkpoint →
   final-verify → closeout) and Sonnet in every init event.

## Notes / declared asymmetry
- The overseer subject's repo is a git repo; the bare subjects' repos are not. The overseer's design needs it
  (branch-off stage); it is declared in the campaign report.
- `budget_total` does not apply in template mode (`ao new` has no such flag); the template's own
  `run_budget_usd` breaker bounds each task.
