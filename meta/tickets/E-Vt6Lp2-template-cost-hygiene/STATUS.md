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
