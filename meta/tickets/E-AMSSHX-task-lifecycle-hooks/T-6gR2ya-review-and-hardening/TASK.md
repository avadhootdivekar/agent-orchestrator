# TASK: T-6gR2ya-review-and-hardening

## Metadata
- Task ID: `T-6gR2ya-review-and-hardening`
- Epic ID: `E-AMSSHX-task-lifecycle-hooks`
- Owner: reviewer (delegated)
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: Draft
- Estimate: < 1 day

## Requirements Mapping
- Requirement IDs: cross-cutting (all FR-1..FR-7)

## Description
Critical review pass on the full implementation diff (models.py, engine.py, spec.py,
workflow.schema.json, specs/examples, tests) against this repo's SOLID/KISS/DRY,
pluggable-architecture, no-magic-literals, error-handling/logging, testability, and
retry/resume/concurrency-safety standards (per the `reviewer` agent's own charter), and
specifically against the HLD's change-scope boundary table (HLD §10) — confirm no out-of-scope
file was touched and no existing behavior changed for a hookless workflow.

## Acceptance Criteria
1. Confirms byte-identical behavior for every pre-existing workflow spec (no `pre_hook`/
   `post_hook` declared anywhere) — spot-checks at least one pre-existing test file's assertions
   still hold verbatim.
2. Confirms `_run_with_retries`'s early-return wiring exactly matches HLD §6 (cancelled/
   quota-exhausted bypass post-hook; succeeded + exhausted-attempts wrap it).
3. Confirms `_run_hook` never raises and every subprocess/file-IO failure mode degrades to a
   `HookOutcome`, not an exception reaching the worker thread's `ThreadPoolExecutor` future.
4. Confirms NFR-1 (paths-only) is honored — no hook-dispatch code path reads task
   instruction/output file *content*, only paths + the bounded control-file JSON.
5. Flags any magic literals, duplicated logic (2+ occurrences), or missing docstrings on new
   public/semi-public surfaces, per CLAUDE.md code-quality rules.
6. Findings recorded in this ticket's STATUS.md with clear pass/fail per item; any fix required
   is delegated back to `developer` and re-verified (full suite re-run), not silently skipped.

## Risks
- None beyond normal review scope creep — bounded explicitly to the change-scope table.

## Dependencies
- T-jI3P4p (suite green before review is meaningful).

## Pseudocode / Algorithm
```text
N/A.
```

## Schemas / Interface Notes
- N/A — review task.

## Handoff Boundary
- Upstream: full implementation diff (all prior tasks).
- Downstream: epic completion handoff cites this review's outcome.

## Artifacts
- Docs/comments: `meta/tickets/E-AMSSHX-task-lifecycle-hooks/T-6gR2ya-review-and-hardening/`
- Large outputs: N/A
