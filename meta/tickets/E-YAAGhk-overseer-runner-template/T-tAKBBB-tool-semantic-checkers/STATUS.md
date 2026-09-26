# STATUS

- ID: `T-tAKBBB-tool-semantic-checkers`
- Updated At: 2026-09-26
- State: Done — reviewed (approve with nits), all 3 warnings fixed
- Owner: developer

## This update
- Added the semantic half of M3's `ckpt-check` rule engine (OV-R11, R12, R13, R13c, R14, R16) by
  appending new check functions to the two EXISTING extension-point lists `T-HPJcc6` left
  (`_MANIFEST_STRUCTURAL_CHECKS`, `_WAVE_ENTRY_STRUCTURAL_CHECKS`) — no new list, no restructuring
  of the shared rule engine. `ManifestCheckContext` extended with `verdict`/`digest`/`ledger_lines`
  fields, threaded in from `ckpt_check`'s existing context construction.
- R13c (rename-dodge): Jaccard similarity (`_goal_tokens`/`_jaccard_similarity`,
  `GOAL_SIMILARITY_REJECT = 0.6`, `STOPWORDS` — all named `Final` constants) against a capped work
  item's latest brief goal, plus a separate `prior_attempts`/`touches` exact-overlap path.
- AC3: on a successful, non-dry-run `ckpt-check`, appends a chained `checkpoint` ledger event
  (decision, verdict/manifest sha256) plus `hold_requested` iff the decision is `hold`, idempotent
  by checkpoint id (mirrors M1's `_lock_charter` "check before append" pattern).
- New `tests/test_overseer_tool_checker_semantic.py`, 52 tests (53 after the review-fix regression
  test below). All prior M1/M2/M3a tests pass unmodified (verified: 279 → 331 → 332, only additive).

By: dev-epic · Role: manager · Date: 2026-09-26 · Comment: Independently re-verified before
requesting review, and found a real gap through direct code tracing (not something I asked the
developer to check): the developer's own design choice — every R12/R14/R16 check gates on
`ctx.verdict is not None` and returns `[]` when absent, to avoid breaking `T-HPJcc6`'s pre-existing
structural fixtures (none of which ever write a `verdict.json`) — left `ckpt_check` reusing
`_read_optional_json` (M2's tolerant "missing/malformed means no data, never a Violation" reader,
correct for HISTORICAL data, wrong for THIS checkpoint's own current verdict) for its own verdict
read. I traced `engine.py`'s actual hook/output-check ordering
(`_finalize_with_post_hook`, engine.py ~L3978, runs post_hook on the EXECUTOR's own exit status,
BEFORE `_settle_completed_task`'s `missing_outputs` EXISTENCE-ONLY check, ~L1738) and confirmed: a
genuinely MISSING verdict.json is already safely caught by the engine's own check (redundant, not a
gap) — but a verdict.json that EXISTS yet is malformed/unparseable slips past that existence-only
check entirely, and would have silently passed `ckpt_check` with zero violations. Fixed with a
narrow, additional check in `ckpt_check` (verdict path exists but `_read_optional_json` returns
`None` → an `R12` violation) that does NOT touch the plain-missing case (redundant with the engine,
and would have broken every `T-HPJcc6` structural fixture). Added
`test_r12_malformed_verdict_json_fails_not_silently_skipped`. Re-verified: 331 passed (was 330),
coverage 98%, ruff/mypy/pyright clean.

By: reviewer · Role: reviewer · Date: 2026-09-26 · Comment: **Verdict: approve with nits, no
blocking issues.** Independently reproduced every claim: full targeted suite (331 passed),
structural suite alone (100 passed, zero regressions from the semantic diff), coverage (1857
stmts/32 missed/98%), ruff/mypy/pyright (all clean, including a real `pyright` run dev-epic didn't
have installed at review time). Wrote and ran a standalone script constructing a manifest that
trips BOTH R1 (unparseable) and R12 (malformed verdict) simultaneously — confirmed both rule ids
appear in the reported violations, proving the "never short-circuit" property (AC4) holds for the
new malformed-verdict check too. Independently re-tokenized both R13c Jaccard AC2 fixtures by hand
— confirmed similarity 1.0 (reject) and 0.0 (pass, no false positive), matching the shipped tests.
Traced `engine.py` independently and confirmed dev-epic's ordering claim and fix scoping are
correct. Read all 52 new tests end-to-end — every failing case asserts a specific rule id.
**Three Warnings, all fixed by dev-epic:**
1. **R14 hold validation didn't check the hold-request's own `checkpoint` field matches the
   checkpoint currently deciding hold** — a stale request left over from an earlier checkpoint's
   hold would still validate for a later, unrelated checkpoint. Fixed: `_valid_hold_request` now
   takes `checkpoint_id` and requires `data.get("checkpoint") == checkpoint_id`; the one call site
   (which already had `ctx.emitter_id` in scope) updated. Added
   `test_r14_hold_request_from_a_different_checkpoint_is_invalid_fails`.
2. **R12's "verdict is schema-valid" sub-clause omitted `checkpoint`/`stage` fields.** Noted as
   arguably out of this ticket's literal scope (TASK.md's own R12 bullet list never calls these
   out) — **not fixed here**, recorded as a follow-up candidate for whoever next touches R12.
3. **DRY: `_charter_ask_ids` duplication** — `check_manifest`'s own inline charter-ask-id
   computation was functionally identical to the module-level `_charter_ask_ids` helper (which
   didn't exist yet when that inline code was first written). Fixed: `check_manifest` now calls the
   shared helper directly.
Re-verified after all fixes: 332 passed (was 331), coverage still 98% (1854 stmts/31 missed), full
suite **4439 passed, 8 skipped, 0 failed**, ruff/mypy/pyright clean.

## Evidence
- `.venv/bin/pytest -q tests/test_overseer_tool_checker_semantic.py` → `53 passed` (52 original + 1
  post-review regression test).
- `.venv/bin/pytest -q tests/test_overseer_tool_budget.py tests/test_overseer_tool_ledger.py tests/test_overseer_tool_gates.py tests/test_overseer_tool_detectors.py tests/test_overseer_tool_checker_structural.py tests/test_overseer_tool_checker_semantic.py`
  → `332 passed` (279 prior + 53 new).
- Coverage: `1854 stmts, 31 miss, 98%` (combined M1/M2/M3a/M3b).
- Full repo suite: `.venv/bin/pytest -q` → `4439 passed, 8 skipped, 0 failed` (zero regressions vs.
  the 4386 baseline after the branch_policy amendment + routed-runner cherry-pick).
- `.venv/bin/ruff check` + `.venv/bin/ruff format --check` clean. `.venv/bin/mypy` clean.
- Real `pyright` run (both dev-epic and the reviewer, independently) on both touched files: 0 real
  errors (only the known/ignorable pytest-import-unresolved noise).
- Code: `src/agent_orchestrator/templates/builtin/overseer-runner/tools/overseer_tool.py` (M3
  semantic diff + the malformed-verdict fix + the 3 review fixes).
- Tests: `tests/test_overseer_tool_checker_semantic.py`.

## Risks / Blockers
- None outstanding. All 3 reviewer warnings addressed (2 fixed, 1 explicitly deferred with
  rationale — see above).
- Forward note (from the reviewer's Testing notes): nothing in this suite drives `ckpt_check`
  through the ACTUAL engine post_hook dispatch path end-to-end (a real task whose declared output
  `verdict.json` is genuinely absent) to empirically confirm the "engine's missing_outputs check
  already covers this" claim, beyond the code trace. This is `T-WruPiv`/`T-vmI0jI`'s territory (the
  e2e harness tasks), not this ticket's — noted here so it isn't lost.

## Next actions
1. None outstanding — done, reviewed (approve with nits, all actionable findings fixed).
2. Unblocks `T-WruPiv` (e2e harness — all S1 tasks plus all three checker tasks are now done).
