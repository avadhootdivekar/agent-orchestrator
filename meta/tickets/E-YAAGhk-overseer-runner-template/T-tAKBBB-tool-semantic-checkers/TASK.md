# TASK: T-tAKBBB-tool-semantic-checkers

## Metadata
- Task ID: `T-tAKBBB-tool-semantic-checkers`
- Epic ID: `E-YAAGhk-overseer-runner-template`
- Owner: developer
- Created: 2026-09-26
- Last Updated: 2026-09-26
- Status: Draft
- Estimate: 3 days (24 h)

## Requirements Mapping
- Requirement IDs: FR-3, FR-6 (enforcement), FR-8, FR-9, FR-11 (hold event), FR-12 (deferral)
- Design: `docs-md/overseer-runner-hld.md` §8.2 (allowed decisions), §8.3 (enforcement, R13c, integrity model), §8.4 M3

## Description
Add the semantic rules to the `ckpt-check` rule engine:

- **OV-R11** stage restrictions. In converge, units may only target work items that already exist
  in the ledger. In stabilize, the kind must be stabilize, verify, or document.
- **OV-R12** verdict checks:
  - the verdict is schema-valid
  - `decision ∈ digest.allowed_decisions`
  - every digest signal id has exactly one response
  - `accept` on a high signal needs a rationale ≥ 40 chars
  - every ask appears in `alignment`, and every criterion appears in `criteria`
  - `deferred` needs a `deferred_reason` and evidence ≥ 40 chars
  - `human_descoped` needs a prior `hold_answered` event
  - `hold` needs `hold_questions`
- **OV-R13** attempt cap: a unit on an item at the cap needs a non-empty `approach_change`, and
  above cap+1 it is forbidden.
- **OV-R13c** rename dodge: Jaccard ≥ `GOAL_SIMILARITY_REJECT` against a capped item's latest goal,
  or `prior_attempts`/`touches` overlapping a capped item's.
- **OV-R14** decision ↔ manifest consistency:
  - `continue`, `redirect`, and `stabilize` need a next ckpt with ≥1 unit
  - `hold` needs a next ckpt with 0 units and a valid `control/hold-request.json`
  - `closeout` needs the tail
  - an early closeout (stage explore or converge) needs, for every non-deferred ask, a verify-kind
    unit with verdict pass after that ask's last implement/fix/stabilize/document unit
- **OV-R16** `must_close` ⇒ `closeout`.

On a successful non-dry run, append the chained ledger events `checkpoint` (decision,
`verdict_sha256`, `manifest_sha256`) and, if the decision is `hold`, `hold_requested`.

- Inputs: verdict, digest, manifest, briefs, ledger, `control/hold-request.json`
- Outputs: violations, and ledger events on success

## Acceptance Criteria
1. Each of R11, R12 (every sub-clause), R13, R13c, R14 (every decision branch, including early
   closeout with and without verify evidence), and R16 has a passing and a failing fixture. The
   failing case exits 2 with the exact `OV-` rule id.
2. R13c:
   - a new key with goal "Fix tokenizer bug in parser" against a capped item goal "fix the parser
     tokenizer bug" is rejected
   - an unrelated goal passes
   - a shared `touches` glob with the capped item is rejected
   - the rejection message names the capped work item and the three allowed alternatives
3. Ledger events: after a successful non-dry `ckpt-check`, exactly one `checkpoint` event is
   appended, plus `hold_requested` iff the decision is `hold`. The chain verifies. `--dry-run`
   appends nothing. A re-run after success doesn't duplicate the events (idempotent by
   `checkpoint` id).
4. Combined structural + semantic run: a fixture with one R8 violation and one R12 violation reports
   both (rules never short-circuit each other).
5. ≥ 90% coverage for the semantic section. `ruff`/`mypy` are clean. A `reviewer`-agent review is
   recorded in STATUS.md.

## Risks
- The Jaccard heuristic can false-positive. Mitigation: `approach_change` on the capped key is always
  the legal route; the threshold is a named constant tuned from T-23yMMB data.

## Dependencies
- T-ABDjSj (ledger, digest), T-C6uQJW (signal ids/format), T-HPJcc6 (the rule engine).

## Pseudocode / Algorithm
```text
jaccard(a, b) = |T(a) ∩ T(b)| / |T(a) ∪ T(b)|, T = set of lowercase alphanumeric tokens with len >= 3,
minus a fixed STOPWORDS set (named constant).
early_closeout_ok(ask): last_change = max wave/order of units on ask with kind in {implement,fix,stabilize,document};
                        exists unit on ask with kind == verify, verdict == pass, order > last_change
```

## Schemas / Interface Notes
- Verdict and ledger event schemas: §13.4.

## Handoff Boundary
- Upstream: T-HPJcc6, T-C6uQJW.
- Downstream: T-WruPiv, T-vmI0jI, T-3FlD46.

## Artifacts
- Tests: `tests/test_overseer_tool_checker_semantic.py`
