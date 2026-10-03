# Usage analytics — `ao report-usage` (2026-10-02)

Answers "is this model/effort configuration earning its cost?" from real runs, without an A/B harness. Complements `ao-bench` (controlled, offline — see `benchmarking-framework-hld.md`); this is observational.

## What is recorded (per dispatch, on `TaskRunState`)
- `agent`, `model`, `effort` — the EFFECTIVE values handed to the executor (task override > agent > global fill-in, via `resolve_effective_agent`). `model: null` = the CLI default ran. Before this, a per-task override (e.g. the Haiku tier) could not be attributed after the fact.
- `upstream_producers` — ids of tasks whose declared outputs this task consumes.
- `review_verdict_path` — set for a task declaring a `review.md` output: the sibling `review-verdict.json`.
- Recorded in `engine._prepare_and_maybe_dispatch` next to `dispatch_cycle`; computed by `usage.dispatch_provenance` (pure). Backward compatible: all default to "unknown".
- Known approximation: a T2 conflict-resolver redispatch records the originating task's agent, not the resolver's.

## Review verdict sidecar
Reviewers (instructions `10`, `23`, `33`, `42`) also write `review-verdict.json` next to `review.md`:
`{"verdict": "PASS"|"FAIL", "findings": {"critical": n, "major": n, "minor": n}, "must_fix": n}`.
It is deliberately NOT a declared output: it is metadata, never a gate, so a reviewer forgetting it cannot fail a run. It is read at report time through `artifacts.read_control` (bounded); a missing/malformed file means "no verdict" (the report shows `verdicts found: x/y` so coverage is visible).

## The report
`ao report-usage [--workspace W] [--run-id R ...] [--json]` scans `.orchestrator/runs/*/state.json` and groups settled, dispatched tasks by (agent, model, effort): task count, success, retry rate (>1 attempt or dispatch cycle), mean cost, tokens, and — attributed to the PRODUCER of the reviewed work, not the reviewer — reviewed count, FAIL rate, and critical/major/minor findings. A group is flagged "high rework" only with ≥5 reviewed tasks and ≥50% FAIL (`usage.MIN_SAMPLE_FOR_FLAG`/`REWORK_FLAG_RATE`).

## Limits (read before acting on it)
- Observational: models get different tasks; compare similar task mixes. Small groups are shown, never flagged.
- Review FAIL rate measures what the reviewer catches; a weak reviewer under-reports.
- Runs recorded before this change appear under `(default)`.
- Not yet covered: repeated-trial pass rates / paired comparison in `ao-bench`, and an ao-vs-bare-Claude comparison on live runs.

## Update (E-Us9Kd4): usefulness signals
This report now also joins (a) generic verdicts for any workflow (`TaskSpec.verdict_path`, declared `*verdict.json` outputs; overseer checkpoint/final-verify "outcome vs charter"), (b) your own ratings (`ao rate`, dashboard) with reviewer-disagreement candidates, and (c) git diff survival (`--with-survival`, `ao report-survival`). Design, schemas, attribution algorithm and limits: [`usage-signals-hld.md`](usage-signals-hld.md). The dashboard "Usage" tab renders the same rollup (`GET /api/usage`).

## Update 2 (E-Us9Kd4 follow-ups)
- `record_git_heads` (default on; `--record-git-heads/--no-record-git-heads`, `AO_RECORD_GIT_HEADS`, `.ao/config.yaml`) controls the per-dispatch/settle `git rev-parse HEAD` used for survival attribution. Off => `ao report-survival` falls back to isolation ranges / time-window and says heads were intentionally not recorded; report-usage is unaffected.
- `RunState.prompt` records the run prompt (bounded 64 KiB, sha256 of the full text, source) and the dashboard shows it (run-detail Prompt panel, list preview). This also makes a future re-run-of-similar-prompt signal derivable (same workflow + prompt sha within a window).
