# TASK: T-b2n6rk-retry-timeout-safety

## Metadata
- Task ID: `T-b2n6rk-retry-timeout-safety`
- Epic ID: `E-m2k9pa-orchestrator-mvp-a`
- Owner: `TODO`
- Created: `2026-06-16`
- Last Updated: `2026-06-16`
- Status: `Draft`
- Estimate: `< 1 day`

## Requirements Mapping
- Requirement IDs: FR-6, NFR-4

## Description
Implement retry-with-backoff and timeout/cancellation around executor calls, with an injectable sleeper so
tests are deterministic. (LLD §8 `_run_with_retries`.)

## Acceptance Criteria
1. `_run_with_retries` attempts `1..max_attempts`; success short-circuits; failures sleep `backoff_seconds` (injectable sleeper) between attempts.
2. Effective retry/timeout = task override else workflow default.
3. Timeout from executor (`timed_out`) is treated as a failed attempt and is retryable.
4. Bounded + cancellable: a cancel signal stops further attempts and marks `cancelled`.
5. Tests (no real sleep): succeeds-on-2nd-attempt; exhausts attempts→failed; timeout path; cancel path.

## Risks
- Real sleeps slow tests — must inject sleeper/clock.

## Dependencies
- Upstream: T-p6m4qz. Downstream: T-h7k3qm.

## Pseudocode / Algorithm
```text
for attempt in 1..max:
  r = executor.execute(ctx)
  if r.status == succeeded: return r
  if cancelled: return cancelled
  if attempt < max: sleeper(backoff_seconds)
return last failed
```

## Schemas / Interface Notes
- Interface: `_run_with_retries(task, defaults, ...)`; injectable `sleeper`.
- Artifacts: N/A (operates on results).

## Handoff Boundary
- Upstream: executor results. Downstream: engine uses this wrapper.

## Artifacts
- Docs/comments: this folder.
