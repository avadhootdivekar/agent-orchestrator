# STATUS

- ID: `T-HPJcc6-tool-structural-checkers`
- Updated At: 2026-09-26
- State: Done — reviewed (approve, non-blocking nits)
- Owner: developer

## This update
- Ticket created by the architect design pass (Rev 2, after the Phase-4 consultations). Sprint: S2.

By: architect · Role: architect · Date: 2026-09-26 · Comment: Created from `docs-md/overseer-runner-hld.md` Rev 2. The ACs are pass/fail and agent-executable, and the task is sized ≤3 days. Not started.

By: developer · Role: developer · Date: 2026-09-26 · Comment: Implemented `intake-check`/`ckpt-check`
in `tools/overseer_tool.py` (M3, structural half only: OV-R1-R10, OV-R15, plus intake-only charter
checks). New rule engine: `RuleViolation`/`CheckViolations` collect every violation across every
rule before one combined exit-2 report (never raise-on-first, unlike M1's `Violation`); two
additive check-fn lists (`_MANIFEST_STRUCTURAL_CHECKS`, `_WAVE_ENTRY_STRUCTURAL_CHECKS`) are the
explicit extension point for T-tAKBBB's semantic rules (R11-R14/R16/R13c). Added a new rule id
`CHR-1` (charter validity/prompt-hash, not in the HLD's R1-R16 list -- documented in the tool's own
docstring, judgment call mirroring how M1 added `BC-2`). New tests:
`tests/test_overseer_tool_checker_structural.py` (100 tests, all AC1/AC2 rule+scenario coverage,
AC3 charter/idempotency/dry-run, AC4 contract-shape drift guard via the rendered
`overseer-contract.md.tmpl`, AC5 TaskSpec+hook-wiring property tests). Coverage: 98.3% on the M3
code added (99% combined with M1/M2). `ruff check`/`ruff format --check` clean; `mypy` clean except
two pre-existing `import-untyped` notices also present on the sibling `test_builtin_overseer_runner_
assets.py` file when checked the same way (package has no `py.typed` marker; out of this task's
scope). All 179 prior M1/M2 tests pass unchanged (zero regressions); combined suite is 279 passed.
Did not touch M1/M2 functions, `overseer-contract.md.tmpl`, `README.md`, `template.yaml`,
`workflow.json.tmpl`, `overseer-config.json.tmpl`, or any concurrently-edited `instructions/*.md`
file (T-5ZzAZp, in flight on the same branch). Reviewer-agent review still to be arranged separately.

By: dev-epic · Role: manager · Date: 2026-09-26 · Comment: Independently re-verified before
requesting review: ran a real `pyright` pass on both the tool file and the test file (0 real
errors on either, beyond the expected/ignorable pytest-import noise — this resolved a
mid-development finding the epic owner had flagged), re-ran all 279 tests + coverage myself, and
hand-read/verified the security-critical OV-R6 exact-match check and the OV-R8 depends_on check
before requesting review.

By: reviewer · Role: reviewer · Date: 2026-09-26 · Comment: **Verdict: Approve**, with 3
non-blocking nits. Read the entire M3 diff (lines 2148-3312) plus the full 1679-line test file
against `overseer-contract.md.tmpl`'s exact wording, and independently re-ran everything (100
passed standalone, 391 passed on the wider selection, ruff/mypy clean). Verified R1-R10 and R15
each against the contract's precise text with line citations; confirmed R4's intake-time cap
reuses `derive_budget` with no hand-rolled duplicate formula; confirmed the `CHR-1` rule id is a
genuinely distinct validation point from `INT-1` (pre-lock vs. post-lock), not overlapping
semantics, and doesn't collide with any existing id; confirmed `--dry-run` writes zero files on
both subcommands by code inspection; confirmed the rule-engine extension point is genuinely
additive (a `T-tAKBBB` semantic-rule function of the matching signature appends with zero other
change) and that violations from multiple rules are collected together, not short-circuited —
proven by an existing test that trips two different rules in one manifest and asserts both appear
in stderr, not just the first. Found no additional dead code beyond what the developer already
removed in their own draft. Spot-checked ~25 of the 100 tests (well beyond the requested 5-8) —
every one asserts the exact rule id, none are rubber-stamps.
Three non-blocking nits: (1) two dry-run combinations aren't directly tested, though both are
provably safe by construction (independent, already-separately-tested boolean gates) — a coverage
nicety, not a risk; (2) `_intake_allowed_wave_size`'s `k=1` argument is inert (only feeds a
discarded `must_close`/`allowed_decisions`) but wasn't commented as such; (3) `CHR-1` covers six
distinct charter defects under one id, a reasonable KISS call for this ticket's scope but worth
noting for whoever builds `T-23yMMB`'s rejection-rate tooling if finer-grained ids become useful.
dev-epic fixed nit (2) with a one-line clarifying comment (trivial, no behavior change);
re-verified: 100 passed, ruff/mypy clean. Nits (1) and (3) are recorded here, not actioned —
genuinely optional per the reviewer's own framing.

## Evidence
- `tests/test_overseer_tool_checker_structural.py` -- 100/100 passed (both pre- and post- the
  nit-2 comment fix).
- Combined with M1/M2 (`test_overseer_tool_budget.py`/`_ledger.py`/`_gates.py`/`_detectors.py`):
  279/279 passed, 0 regressions. Combined with T-5ZzAZp's own instructions/assets tests (both
  landing together): 391/391 passed.
- Coverage: `--cov=src/agent_orchestrator/templates/builtin/overseer-runner/tools` → 99% (1554
  stmts, 21 missed, all pre-existing M1/M2 gaps or truly unreachable defensive branches).
- `ruff check` / `ruff format --check` clean on both touched files. `mypy` clean.
- Real `pyright` run on both files: 0 errors (dev-epic's own independent check, resolving a
  mid-development finding flagged by the epic owner).

## Risks / Blockers
- None outstanding. All reviewer nits are either fixed (nit 2) or explicitly recorded as
  non-blocking (nits 1, 3) — see above.
- Judgment calls, all reviewed and accepted: (1) the new `CHR-1` rule id for charter validity
  (schema/prompt-hash/ask-shape), since neither the HLD nor the ticket names one; (2) R4's
  intake-time cap reuses `derive_budget` with `spent=0`/`prev_stage="explore"` (ticket's own
  suggested approach) rather than a hand-derived formula; (3) `inputs` fields are checked as
  "contains the required paths" (superset) while `outputs` fields are checked for exact equality,
  applied uniformly across unit/next-checkpoint/tail shapes, to avoid over-strict rejection of
  legitimate extra context paths (ticket's own "Risks" section flags over-strictness as a concern).

## Next actions
1. None outstanding — done, reviewed (approve, nits handled).
2. T-tAKBBB adds the semantic rules (R11-R14, R16, R13c) to the same rule-engine lists.
