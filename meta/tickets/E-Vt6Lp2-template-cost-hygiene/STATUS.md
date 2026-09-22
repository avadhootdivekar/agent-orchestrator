# STATUS

- ID: `E-Vt6Lp2-template-cost-hygiene`
- Updated At: 2026-09-21
- State: Done
- Owner: dev-epic

## This update
- By: dev-epic · Role: manager · Date: 2026-09-21
- Comment: Epic complete. All 3 tasks Done with evidence. Early-gate `architect`+`reviewer` pass
  run and incorporated (real correctness fix adopted: `extra_args` not `command_template`; two
  points of reviewer disagreement resolved with recorded reasoning — see EPIC.md "Early-gate
  review — outcome"). D1 (asset mechanism), D2 (skill section), D3 (design doc + late-gate real
  `ao new` e2e evidence + full-suite regression) all shipped. Also found and closed a
  pre-existing, undeclared NFR-2 gate gap (predates this epic, from `b0cb467`) surfaced only by
  running the FULL suite rather than just the 4 target template suites.

## Evidence
- 5 commits on `ad/cost-perf-hooks-skills`: `00cd383`, `9b09e76`, `f08816f`, `ae5f5e9`, `244ca49`.
- Full suite: 3977 passed, 1 skipped, 7 deselected, 0 failed (final run).
- Design doc: `docs-md/template-cost-hygiene-hld.md`.

## Risks / Blockers
- None outstanding. Disclosed, not blocking: the `--autocompact=200000` default is an informed
  estimate from aggregate (not per-turn) real data (design doc §3); compaction-fidelity impact on
  long-task success rate is explicitly out of this epic's scope to validate.

## Next actions
1. None — epic complete. A human may want to review the `--autocompact` default and the NFR-2
   gate exception entry (both disclosed, reasoned, not silent).

## Follow-up update (2026-09-22): `--autocompact` revised from uniform `200000` to a role split

Per the next-action note above — reviewed with the user. A broader real-data sample (peak
context across 5 tasks spanning the full duration distribution, not just the original single
232-turn/280K-token anchor) found ordinary 25-60 min dev-cycle tasks routinely reaching
290K-424K peak context, overturning the "rare long tail" framing the original `200000` was
anchored on. Revised to a role split, user-confirmed:

- `architect`, `architect-opus`, `reviewer-opus` (architecture/design synthesis work —
  `reviewer-opus` is dispatched only at `design-review`) → `500000`.
- `developer`, `full-tester`, `manager`, `market-surveyor`, `reviewer` (code review, not design
  review), `tester` (narrower dev-cycle work) → `180000`.

Updated: `agents.recommended.json.tmpl`, this template's `README.md`, the `workflow-authoring`
skill's "Cost & context hygiene" section, `docs-md/template-cost-hygiene-hld.md` (new §3.4,
original §3.1-§3.3 kept intact and marked as the superseded single-anchor rationale rather than
rewritten), and `tests/test_builtin_routed_runner_assets.py`'s threshold-pinning test (now
per-role, was uniform). Full suite re-run clean after the change. Same disclosed risk as before,
now for both thresholds: compaction-fidelity impact on task success rate is still unvalidated —
watch via `ao report-outcomes --grade` before treating either number as final.
