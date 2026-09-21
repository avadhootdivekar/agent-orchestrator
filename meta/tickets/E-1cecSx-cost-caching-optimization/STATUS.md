# STATUS

- ID: `E-1cecSx-cost-caching-optimization`
- Updated At: 2026-09-21
- State: `In Progress`
- Owner: `dev-epic` agent

## This update
- Epic scoped from the 4-workstream request (B1 prompt-caching audit, B2 timing, B3 outcome
  metrics, B4 dashboard). Design doc written (`docs-md/cost-caching-optimization-hld.md`)
  covering the B1 audit with primary-source evidence, B2 feasibility confirmation, B3 MVP scope
  decision (including a correction to Epic A HLD §7's single-call-site claim for the
  post-settlement trigger — verified against current `engine.py`, not assumed), and B4 approach.
  Ticket workspace created (this epic + 5 tasks). Requesting early-gate `reviewer`+`architect`
  pass next, before implementation starts.

## Evidence
- B1 finding sourced from `code.claude.com/docs/en/prompt-caching` ("Cache scope" section) and
  `code.claude.com/docs/en/agent-sdk/modifying-system-prompts` ("Improve prompt caching across
  users and machines"), fetched live 2026-09-21; quoted verbatim in the design doc §1.3.
- B1 code audit: `executors/prompt.py`, `executors/claude_cli.py`, `models.py` NFR-1 comment,
  `isolation/worktrees.py`, `isolation/escalation.py` read in full or targeted; zero ao-authored
  leaks found (design doc §1.2).
- B2.2 feasibility confirmed by direct inspection of real captured transcripts under
  `playground/.tmp/bench/2026-07-22-*/.../transcript.jsonl` — `assistant`/`user` events carry
  ISO-8601-millisecond `timestamp` fields.
- B3.3 correction confirmed by reading `engine.py::_prepare_and_maybe_dispatch` (line 946),
  `_settle_completed_task` (line 1380), `_run_and_integrate` (line 3420) in full.

## Risks / Blockers
- None blocking. B1's fix cannot be verified against a live Anthropic cache in this environment
  without spending real API budget for pure R&D — documented as an evidence boundary in the
  design doc §1.3, not a blocker to shipping the fix (the fix's correctness rests on citing
  Anthropic's own documented flag semantics, not on ao re-deriving them empirically).

## Next actions
1. Run early-gate `reviewer` (+`architect`) pass on the design doc's proposed end-to-end shape.
2. Incorporate findings, then delegate implementation: B1 (self), B2+B4 (developer, parallel),
   B3 (developer, parallel), with explicit change-scope boundaries per the design doc §7.
3. Late-gate `tester` pass: full existing suite + new tests + e2e demonstration workflow.
