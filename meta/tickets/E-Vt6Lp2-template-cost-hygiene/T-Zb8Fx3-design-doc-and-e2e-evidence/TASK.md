# TASK: T-Zb8Fx3-design-doc-and-e2e-evidence

## Metadata
- Task ID: `T-Zb8Fx3-design-doc-and-e2e-evidence`
- Epic ID: `E-Vt6Lp2-template-cost-hygiene`
- Owner: dev-epic
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: Draft
- Estimate: < 1 day

## Requirements Mapping
- Requirement IDs: NFR-D1-3, and the epic's late-gate verification obligation (dev-epic contract
  step 8) for D1+D2 together.

## Description
Write `docs-md/template-cost-hygiene-hld.md`: the D1 mechanism choice (asset-based seed file vs.
documentation-only vs. `ao validate` warning), justified against the real
`templates/__init__.py` rendering mechanics; the chosen `--autocompact` default, justified
quantitatively from the real 232-turn/~280K-token/42.5M-cache-read-token numbers (an estimate,
disclosed as such, not a measured optimum); the early-gate architect/reviewer outcome; and the
late-gate real end-to-end evidence: actually run `ao new routed-runner` into a fresh `tmp_path`
scratch workspace (same pattern `tests/test_e2e_builtin_routed_runner.py` already uses) and show
`agents.recommended.json` rendering + `keep_existing` holding on a second call, PLUS the full
existing test suite run with pass/fail counts (no regressions) and ruff/mypy output on touched
files.

## Acceptance Criteria
1. `docs-md/template-cost-hygiene-hld.md` exists, covers: scope, D1 alternatives considered (a)
   asset-seed (chosen) vs. (b) doc+validator-warning (partially deferred — doc landed, warning
   deferred to Non-MVP) with justification tied to `spec.py`'s V-rule module cohesion; the
   `--autocompact` default with the quantitative growth-curve reasoning; early-gate review
   outcome; late-gate e2e evidence (commands run + actual output, not paraphrased).
2. Real `ao new` invocation into a `tmp_path`-style scratch workspace performed in this session
   (not merely described) — command + output captured in the doc or referenced test file.
3. Full `pytest -q -m "not real_llm and not swebench"` run via
   `.venv/bin/python -m pytest` (never `uv run pytest` — known hang on this machine) — exact
   pass/fail/skip counts recorded.
4. `ruff check .`, `ruff format --check .`, `mypy .` (or scoped to touched files if repo-wide is
   out of this epic's change boundary) output recorded.
5. Epic + all 3 task tickets' `STATUS.md`/`TASK.md`/`EPIC.md` synced to final state (Done, with
   `By/Role/Date` attribution on any comment).

## Risks
- None beyond what's already disclosed in the epic-level Risks section (estimate-based default).

## Dependencies
- Depends on `T-Hn4Rq8` and `T-Kd2Wp5` being substantively complete (this task's evidence
  exercises both).

## Pseudocode / Algorithm
```text
N/A — documentation + verification task.
```

## Schemas / Interface Notes
- Interface / API: N/A.
- Spec / data schema: N/A.
- Triggers / events: N/A.
- Artifacts (inputs/outputs by path): `docs-md/template-cost-hygiene-hld.md` (new).

## Handoff Boundary
- Upstream: `T-Hn4Rq8-agents-recommended-asset`, `T-Kd2Wp5-skill-cost-hygiene-section`.
- Downstream: none — this is the epic's closing task.

## Artifacts
- Docs/comments: `meta/tickets/E-Vt6Lp2-template-cost-hygiene/T-Zb8Fx3-design-doc-and-e2e-evidence/`
- Large outputs: `docs-md/template-cost-hygiene-hld.md`.
