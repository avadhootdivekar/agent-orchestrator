# STATUS

- ID: `T-UJElTR-e2e-verification`
- Updated At: 2026-09-21
- State: `Done`
- Owner: `dev-epic` agent

## This update — final late-gate results
1. **Full test suite** (`pytest -q`, deselecting only the confirmed pre-existing/out-of-scope
   gate failure): **3968 passed, 8 skipped, 1 deselected, 0 failed** (166.86s). No regressions
   from any part of this epic.
2. **`ruff check .`**: clean (0 findings across the whole repo).
   **`ruff format --check .`**: found and fixed one file (`tests/test_outcomes.py`) needing
   reformat; re-verified clean after.
3. **`mypy src`** (this project's actual CI invocation — `mypy .` fails on a pre-existing,
   unrelated duplicate-module issue under `benchmarks/`, not in scope): clean except 4
   pre-existing errors in `_version.py`, confirmed via `git diff` to be untouched by this
   session or any part of this epic.
4. **NFR-2 pre-epic-test-integrity gate**: found and fixed one legitimate new divergence
   (`tests/ui/test_runs.py`, B4's additive contract tests) — registered per the gate's own
   documented exception process. The gate's one remaining failure
   (`tests/test_e2e_builtin_routed_runner.py`) is confirmed pre-existing — last touched in
   `b0cb467` ("Ad/task isolation (#11)"), a DIFFERENT epic's PR that landed on this branch
   before this session started; zero diff from Epic B. Disclosed, not silently excluded.
5. **Real end-to-end demonstration** (not just pytest): `scripts/helper/epics/E-1cecSx/
   run_demo.sh` — a real `ao run` → `ao report-timing` → `ao report-outcomes` →
   `ao report-outcomes --grade grade` sequence against a scratch workspace, using the real
   CLI (`ao`, the installed console script), not a test harness. Captured output saved to
   `output/E-1cecSx-cost-caching-optimization/demo-run-output.txt`. Evidence in that file
   shows, concretely:
   - A dispatched task's `post_hook` (Epic A's existing, dispatch-scoped mechanism) firing and
     passing during `ao run` itself.
   - The `skip_if_outputs_exist`-skipped task genuinely taking the skip path (pre-seeded
     output untouched).
   - `ao report-timing` printing a real per-task duration + cache-hit-rate table.
   - `ao report-outcomes` printing real retry/dispatch-cycle/self-heal counts.
   - `ao report-outcomes --grade grade` (ADR-0015 decision 2) grading BOTH tasks in one pass —
     the written `settlement_grades.json` shows `"settle_reason": "dispatched"` for one task and
     `"settle_reason": "skipped"` for the other, both `"kind": "settlement_hook"`,
     `"status": "passed"`, `"score": 1.0` — the epic's core B3 claim, demonstrated with real
     output a user could actually look at, not asserted only in a test.
6. **`tests/test_e2e_cli_cost_caching.py`** (the automated equivalent of the above, run
   verbosely): 1 passed. Output saved to
   `output/E-1cecSx-cost-caching-optimization/e2e-test-output.txt`.

## Evidence (files)
- `output/E-1cecSx-cost-caching-optimization/demo-run-output.txt` — real CLI demonstration.
- `output/E-1cecSx-cost-caching-optimization/e2e-test-output.txt` — automated e2e test output.
- `scripts/helper/epics/E-1cecSx/run_demo.sh` — the demo script itself, re-runnable.

## What is explicitly NOT verified (disclosed, not hidden)
- B1's fix effectiveness against a LIVE Anthropic cache: not verified empirically (would
  require real, additional paid `claude` CLI dispatches purely for validation — deliberately
  not run this session beyond one incidental $0.19 connectivity check, per the design doc
  §1.4's own disclosed evidence boundary). B1's argv-injection correctness (the part ao
  actually controls and can verify without live spend) IS verified, by unit test.
- Frontend UI correctness beyond `npm run build`/`npm run test`/`npm run typecheck` (no visual/
  screenshot verification was performed — the delegated agent's report states this was not
  attempted; not claimed here either).

## Risks / Blockers
- None.

## Next actions
- None — epic ready for final completion handoff.
