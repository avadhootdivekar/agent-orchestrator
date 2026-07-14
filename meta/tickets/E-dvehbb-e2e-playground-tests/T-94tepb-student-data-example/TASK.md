# TASK: T-94tepb-student-data-example

## Metadata
- Task ID: `T-94tepb-student-data-example`
- Epic ID: `E-dvehbb-e2e-playground-tests`
- Owner: developer
- Created: 2026-07-01
- Last Updated: 2026-07-01
- Status: Draft
- Estimate: `< 3 days`
- MVP: no (Phase 2)

## Requirements Mapping
- FR-1, FR-2, FR-3, FR-4, FR-9, NFR-2, NFR-5.

## Description
Replicate the template to a slightly richer third example, `playground/student-data/`
(problem: "given a list of students `{name, age, standard, scores}`, implement query
methods — `by_age_range`, `by_score_range`"). This example intentionally has a marginally
larger surface than sum/sort to exercise the fan-out with a slightly bigger — but still
low-burn and deterministic — chain. It reuses the same workflow shape and parametrized tiers.

If a second injected chain is desired (N=2), first resolve OQ-2 (aggregator-injected-task
pattern) — otherwise keep N=1 like the other examples to preserve the static `integrate`/loop
wiring. Default: N=1, richer instructions only.

## Inputs / Outputs
- Inputs: template `playground/sum-of-array/*`; parametrized tiers.
- Outputs: `playground/student-data/{PROBLEM.md,workflow.json,reposet.json,agents.fake.json,agents.claude.json,instructions/*,fixtures/*}`.

## Acceptance Criteria
1. `ao validate` on `playground/student-data/` specs → exit 0 (fake + claude agents).
2. Auto-discovered by the fixture + deterministic + perf parametrizations; all cases pass.
3. All six areas assert green via the shared deterministic tier.
4. IDs match `^[a-z0-9][a-z0-9-_]*$`; agents entries use only the 4 allowed keys; injected +
   loop paths unique.
5. If N>1 chains are used, an injected aggregator task is added per OQ-2 and the fixture-tier
   acyclicity test still passes; otherwise N=1 keeps the static wiring.
6. Gated real-LLM smoke test covers `student-data` (reuses T-g7rjh0 harness), skipped by default.
7. Problem stays low-burn; no `src/` change (NFR-1); Phase-1 assets untouched.

## Risks
- Temptation to grow the example large (more chains, more agents) → keep trivial + low-burn;
  richer instructions, not a bigger DAG, unless OQ-2 is resolved.
- Multi-chain (N>1) needs the aggregator pattern; do NOT hand-wire a static `final-review`
  to unknown chain ids.

## Dependencies
- `T-r21p4y`, `T-ee8hzo`, `T-1vuzyi`. (OQ-2 if N>1.)

## Pseudocode / Algorithm
```text
cp -r playground/sum-of-array playground/student-data
# richer PROBLEM.md + instructions (by_age_range / by_score_range)
# keep the single-chain manifest fixture (N=1) unless OQ-2 resolved
# discover_examples() -> ["sorting","student-data","sum-of-array"]
```

## Schemas / Interface Notes
- Interface / API: N/A (assets only).
- Spec / data schema: same shape as template.
- Triggers / events: manual.
- Artifacts: `playground/student-data/*`.

## Handoff Boundary
- Upstream: `T-r21p4y`, `T-ee8hzo`, `T-1vuzyi`.
- Downstream: `T-5bh03j` (docs), `T-92o31p` (perf auto-covers).

## Artifacts
- Docs/comments: this task folder.
- Large outputs: none.
