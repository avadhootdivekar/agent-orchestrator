# TASK: T-n477z9-sorting-example

## Metadata
- Task ID: `T-n477z9-sorting-example`
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
Replicate the proven Phase-1 template to a second example, `playground/sorting/` (problem:
"implement `sort_ascending(nums: list[int]) -> list[int]`"). Reuse the exact workflow shape,
fixture contracts, and the parametrized deterministic + fixture tiers so the new example is
picked up automatically by `discover_examples()`. Only the problem statement, instruction
wording, and fixture values change — the structure is identical to `sum-of-array`.

This task proves the template is truly reusable (a key epic goal) with near-zero new test code.

## Inputs / Outputs
- Inputs: `playground/sum-of-array/*` as the template; parametrized tiers from `T-ee8hzo`/`T-r21p4y`.
- Outputs: `playground/sorting/{PROBLEM.md,workflow.json,reposet.json,agents.fake.json,agents.claude.json,instructions/*,fixtures/*}`.

## Acceptance Criteria
1. `ao validate` on `playground/sorting/` specs → exit 0 (fake + claude agents).
2. The example is auto-discovered by the fixture + deterministic + perf parametrizations
   (no per-example test file needed); all parametrized cases pass for `sorting`.
3. All six areas assert green for `sorting` via the shared deterministic tier (paths differ,
   structure identical).
4. IDs match `^[a-z0-9][a-z0-9-_]*$`; agents entries use only the 4 allowed keys; injected +
   loop paths unique (fixture-tier acyclicity holds).
5. A gated real-LLM smoke test covers `sorting` (reusing the T-g7rjh0 harness) — skipped by default.
6. No `src/` change (NFR-1); `ruff`/`mypy` clean; Phase-1 assets untouched (FR-9).

## Risks
- Divergence from the template creates per-example special-casing → keep structure identical;
  only values differ. If a structural change is genuinely needed, update the template + all
  examples together, not one-off.

## Dependencies
- `T-r21p4y` (parametrized deterministic tier), `T-ee8hzo` (fixture tier), `T-1vuzyi` (template).

## Pseudocode / Algorithm
```text
cp -r playground/sum-of-array playground/sorting
# edit PROBLEM.md, instruction wording, ids stay structurally identical (sort-ascending vs sum)
# fixtures/tasks-manifest.json: same chain shape, paths under output/tasks/t1/
# discover_examples() now yields ["sorting","sum-of-array"] -> all parametrized tests cover both
```

## Schemas / Interface Notes
- Interface / API: N/A (assets only; tests are inherited parametrizations).
- Spec / data schema: same as `sum-of-array`.
- Triggers / events: manual.
- Artifacts: `playground/sorting/*`.

## Handoff Boundary
- Upstream: `T-r21p4y`, `T-ee8hzo`, `T-1vuzyi`.
- Downstream: `T-5bh03j` (docs list the example), `T-92o31p` (perf auto-covers it).

## Artifacts
- Docs/comments: this task folder.
- Large outputs: none.
