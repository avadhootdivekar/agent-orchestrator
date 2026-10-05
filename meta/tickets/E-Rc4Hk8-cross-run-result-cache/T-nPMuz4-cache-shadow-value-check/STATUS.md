# STATUS

- ID: `T-nPMuz4-cache-shadow-value-check`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `tester` (+ `manager` sign-off)

## This update
- Implemented (commit `edc3c18`): `docs-md/result-cache-g0-protocol.md` (procedure, report template,
  decision-rule table marked as a recommendation, OQ-6 note), the smoke evidence and a guard test.
  G0 itself is **not run** (post-merge follow-up, owner parent/operator; does not block epic closure).

## Evidence
- Protocol: `docs-md/result-cache-g0-protocol.md`. Smoke: `output/E-Rc4Hk8-cross-run-result-cache/g0-protocol-smoke.md`
  (real `ao run` x2 under `AO_CACHE=shadow` with a fake-executor workflow, outputs deleted between runs;
  run ids `g0-smoke-20261005T121237Z` / `g0-smoke-20261005T121239Z`). Second run `result_cache`:
  `lookups=2, would_hits=2, misses=0, ineligible=1`; window rate 0.5; stats entries 0 -> 2.
  The protocol's bash blocks were executed verbatim, including the `run.log` miss-component script
  (real `cache.miss` events, one miss diffed: `{'instruction': 1}`).
- Stable `ao` unchanged: `ao 0.1.0 (a10ebc5.dirty) built 2026-10-03T16:50:49Z` before and after
  (`install.sh` not run).
- Guard test `tests/cache/test_g0_protocol_doc.py` (10 tests). `pytest -q -p no:cacheprovider tests/cache
  tests/test_spawn_provenance.py tests/test_nfr2_regression_gate.py`: 1301 passed. `ruff check src tests`
  and `ruff format --check` clean on touched files (`src/agent_orchestrator/_build_info.py` is a
  pre-existing generated, gitignored file); `mypy src tests/cache`: only the 4 pre-existing errors in
  `src/agent_orchestrator/_version.py`. No `src/` change, so no full suite.
- By: developer · Role: developer · Date: 2026-10-05

## Risks / Blockers
- None. Dependencies T-o95l1M and T-eyn5UG are Done.
- OQ-6: the parent confirms the G0 thresholds and owns the post-merge execution.

## Next actions
1. **manager:** review the evidence and sign off (AC-6); then State -> Done and sync EPIC/rollup.
2. **parent/operator (post-merge):** execute G0 per the protocol; confirm the OQ-6 thresholds.
3. T-bdQZW4 links the protocol (does not edit it).

## Comments
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Status initialized (Draft, Rev 2).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD §23.5). State stays `Draft`
  (surfaces, after T-o95l1M); this file, `TASK.md`, `HANDOFF.md` (when present) and the epic `STATUS.md` rollup
  agree.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Implemented (commit `edc3c18`); State -> In Review, awaiting the manager sign-off (AC-6). G0 itself is not run (post-merge, parent/operator). Evidence: `output/E-Rc4Hk8-cross-run-result-cache/g0-protocol-smoke.md`.
- By: manager · Role: agent · Date: 2026-10-05 · Comment: AC-6 sign-off. Reviewed the protocol doc (post-merge/operator-owned opening, no claim G0 ran), the smoke evidence (run 2 lookups=2, would_hits=2) and the guard test. G0 execution remains a post-merge follow-up. State -> Done.
