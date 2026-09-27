# STATUS

- ID: `T-WruPiv-e2e-harness-core-scenarios`
- Updated At: 2026-09-26
- State: Done
- Owner: tester → dev-epic

## This update
- Ticket created by the architect design pass (Rev 2, after the Phase-4 consultations). Sprint: S2 (harness from day 1).

By: architect · Role: architect · Date: 2026-09-26 · Comment: Created from `docs-md/overseer-runner-hld.md` Rev 2. The ACs are pass/fail and agent-executable, and the task is sized ≤3 days. Not started.

## Evidence
- None yet (not started).

By: developer · Role: developer · Date: 2026-09-26 · Comment: Fixed a reviewer-flagged
CRITICAL gap plus two WARNINGs, scoped to `tests/overseer_runner_harness.py`,
`tests/test_e2e_builtin_overseer_runner.py`, `tests/test_e2e_builtin_overseer_runner_simple.py`
only (no `overseer_tool.py` or other ticket files touched).
  - CRITICAL: `ScriptedOverseerExecutor` was clobbering the real, tool-computed `digest.json`
    (both via its own script writes and via the inherited `FakeExecutor` stub pass) with a
    test-authored flat shape lacking the nested `budget.stage` the checker/next-checkpoint
    actually read. This silently disabled OV-R11 (stage-gated rules) AND, discovered during
    the fix, OV-R14's early-closeout evidence check, for every scenario. Fixed by snapshotting
    digest.json before calling `super().execute()` and restoring it after, plus excluding it by
    filename from both scripted-write passes. Redesigned scenario (b)'s costs against the REAL
    `derive_budget` (reverse-engineered via a standalone script, then verified against actual
    on-disk digests) to drive a genuine explore(15%) -> converge(35%) -> stabilize(45%) ->
    closeout(60%) sequence with `run_budget_usd=100`/`wave_size=1`. Also found and fixed a
    second latent harness bug: scripted `cost_usd` was never actually recorded into
    `state.spent` because `TaskResult.actuals_available` was never set True, so budget staging
    was silently a no-op in every scenario until fixed.
  - Fixing the digest-clobber surfaced OV-R14's early-closeout evidence requirement as
    genuinely live for scenario (a)-minimal, (c), and (d) too (they decide `closeout` at
    `stage=explore` with no prior verify-kind ledger unit) -- each got a verify unit added to
    satisfy it for real.
  - WARNING 2: scenario (d) now asserts ledger `unit` line count is unchanged across the
    `ao resume` call (idempotency) and that both `hold_requested`/`hold_answered` ledger events
    exist.
  - WARNING 3: the harness's side-channel write pass (absolute-path `output_map` keys outside
    `ctx.output_paths`) is now allowlisted to exactly `hold-request.json` / anything under a
    `needs-input/` dir; anything else is refused so a mis-declared output surfaces as a missing
    file, not silent success.
  - Verification: all 5 tests (`test_scenario_a_minimal_two_waves`,
    `test_scenario_a_two_waves_early_verified_closeout`, `test_scenario_b_stage_escalation`,
    `test_scenario_c_checker_rejection_self_corrects`,
    `test_scenario_d_human_in_the_loop_hold_resume`) pass on 3 consecutive local runs.
    Scenario (b)'s real, on-disk digest sequence: ck-01 explore (pct_used=15.0), ck-02 converge
    (35.0), ck-03 stabilize (45.0), ck-04 closeout (60.0) -- SUT-derived, not fixture values.
    Full repo suite: `4449 passed, 8 skipped` (unchanged from the stated baseline, zero
    regressions; `pytest-randomly` is not installed, so no `-p no:randomly` run was needed).
    `ruff check`/`ruff format --check` clean on the 3 touched files. `mypy` clean except
    pre-existing, project-wide `import-untyped` (missing `py.typed` marker) notices that appear
    identically on every other test file importing `agent_orchestrator.*` (confirmed via
    `tests/test_cli.py`) -- not introduced by this change. `pyright --pythonpath
    .venv/bin/python` clean (0 errors/warnings).

## Risks / Blockers
- See TASK.md "Risks". No blockers at creation.
- The `cost_usd` -> `state.spent` wiring gap (fixed above) means any FUTURE scenario relying on
  scripted costs must go through `ScriptedOverseerExecutor`, not a bespoke executor that skips
  its `actuals_available=True` assignment.

## Next actions
1. Build `tests/overseer_runner_harness.py` (scripted executor plus the entry builder). DONE.
2. Scenarios (a)–(d) once the checkers land. DONE -- all 5 tests pass; see Evidence above.
3. Reviewer sign-off on this fix pass. DONE — see below.

By: dev-epic · Role: manager · Date: 2026-09-26 · Comment: Independently re-verified this fix pass
with primary evidence rather than accepting the developer's report at face value (per this epic's
standing "verify before trust" discipline, reinforced after an earlier task in this same epic
self-reported "production-ready" when it was not). Reproduced, myself: all 5 tests pass 3/3
consecutive runs (`.venv/bin/pytest tests/test_e2e_builtin_overseer_runner_simple.py
tests/test_e2e_builtin_overseer_runner.py -v`, run 3x); scenario (b)'s real on-disk digest.json
sequence via `pytest --basetemp=<dir>` — confirmed `ck-01 explore (pct_used=15.0) → ck-02 converge
(35.0) → ck-03 stabilize (45.0) → ck-04 closeout (60.0)`, nested under `budget.*` exactly matching
`_digest_stage()`'s real read path, not the developer's claim alone; confirmed `TaskResult
.actuals_available` (`models.py:868`) is a real field the engine gates cost-recording on
(`engine.py:1613,3015,4055-4066`), validating the second latent-bug claim; full repo suite
`.venv/bin/pytest -q` → **4449 passed, 8 skipped, 0 failed** (identical to the pre-fix baseline, no
regressions); `ruff check`/`ruff format --check`/`mypy`/`pyright --pythonpath .venv/bin/python` all
independently re-run clean on the 3 touched files. Judgment call: did NOT request a second
reviewer-agent round on top of the one already recorded above — the original reviewer pass already
covered the harness mechanism in full and approved 4/4 of its own flagged judgment calls, its 3
remaining findings (1 critical, 2 warnings) are now fixed exactly as specified, and dev-epic's own
re-verification here used primary evidence (raw digest.json files, engine source) rather than
trusting either agent's self-report — judged equivalent-or-stronger than a second rubber-stamp pass
for this specific fix-the-exact-findings scope. AC6 ("a reviewer-agent review is recorded") is
satisfied by the review already on record above. Ticket marked Done.
