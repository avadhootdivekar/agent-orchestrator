# STATUS

- ID: `E-1cecSx-cost-caching-optimization`
- Updated At: 2026-09-21
- State: `In Progress`
- Owner: `dev-epic` agent

## This update
- Early-gate `reviewer` + `architect` review completed (2026-09-21). Reviewer: approve with
  changes. Architect: approve with changes overall; **needs rework** on the original in-engine
  `settlement_hook` design specifically (B3.3). Both reviews traced claims against the actual
  current code (not taken on faith) and the reviewer independently re-fetched Anthropic's live
  docs.
- Design doc revised to Rev 2 incorporating every finding (`docs-md/cost-caching-optimization-
  hld.md` §8 has the full record). Core outcome: B3.3's in-engine mechanism was REPLACED with a
  post-run grading pass (`ADR-0015` decision 2), eliminating `engine.py` from this epic's
  change-scope entirely — a stronger guarantee than the original "one helper + two call sites"
  plan.
- B1 (prompt-caching audit + fix), B3 (outcome/accuracy metrics, all three sub-items), and the
  backend halves of B2 and B4 are now **implemented, tested, and committed**
  (commit `57b6469`). B2.2 (within-task activity breakdown) and B4's dashboard frontend are
  delegated to `developer` agents, running now.

## Evidence
- Full existing suite: 3938 passed, 8 skipped, 1 pre-existing unrelated deselect, 0 failed
  (`pytest -q`, ~164s) — no regressions from B1/B3/B2.1/B4-backend.
- 60 new tests added across `tests/test_executor.py`, `tests/test_outcomes.py`,
  `tests/test_reporting.py`, `tests/test_e2e_cli_cost_caching.py`, `tests/ui/test_runs.py`.
- One real outer-CLI-boundary e2e test (`tests/test_e2e_cli_cost_caching.py`,
  `typer.testing.CliRunner`) proves `ao run` → `ao report-timing` → `ao report-outcomes
  [--grade]` end to end, including the load-bearing B3.3 proof: one `--grade` invocation
  grades both a freshly-dispatched task and a `skip_if_outputs_exist`-skipped task with zero
  per-task settlement wiring.
- `ruff check` + `mypy src` clean on every touched/new file (one pre-existing, unrelated
  `_version.py` mypy issue confirmed via `git diff` to be untouched by this session).
- Disclosed: one real $0.19 API charge from an unplanned `claude` CLI connectivity check during
  B1 research (`T-lue4Rz`'s STATUS.md has the detail); no further paid experiments run.

## Risks / Blockers
- None blocking. Two delegated tasks (B2.2, B4-frontend) in progress; `T-UJElTR` (late gate)
  blocked on them landing.

## Next actions
1. Await B2.2 and B4-frontend delegated developer results.
2. Run `T-UJElTR`: full-suite re-verification after those land, plus the late-gate e2e pass via
   `tester` per the dev-epic protocol.
3. Final ticket sync (all to `Done`) and epic completion handoff.
