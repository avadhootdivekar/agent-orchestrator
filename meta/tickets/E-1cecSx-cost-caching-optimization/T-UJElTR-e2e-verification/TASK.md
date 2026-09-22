# TASK: T-UJElTR-e2e-verification

## Metadata
- Task ID: `T-UJElTR-e2e-verification`
- Epic ID: `E-1cecSx-cost-caching-optimization`
- Owner: `dev-epic` agent, via delegated `tester` agent for the run itself
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: `Done`
- Estimate: `< 1 day`

## Requirements Mapping
- Requirement IDs: all (late-gate verification of FR-B1-1 through FR-B4-1)

## Description
Late-gate end-to-end verification, per CLAUDE.md's mandatory checklist and the dev-epic
protocol's "run the end-to-end path via `tester`" requirement. Not unit tests — a real workflow
spec run through the engine's own CLI entrypoint (outer-boundary e2e, `CliRunner`-equivalent
where available), producing artifacts a user could actually look at.

## Acceptance Criteria
1. Full existing test suite (`pytest -q`) run in full (not just new tests) — report exact
   pass/fail/skip counts, no regressions vs. the pre-epic baseline.
2. `ruff check .` and `mypy .` (or the project's configured subset) run — report actual output.
3. New example workflow (`specs/examples/cost-caching-demo.json` or similar, from `T-Ar8HJF`/
   `T-J1b0FN`) run end-to-end via the CLI (`ao run` or equivalent), exercising:
   - A `settlement_hook`-graded, freshly-dispatched task.
   - A `settlement_hook`-graded, `skip_if_outputs_exist`-skipped task (pre-seed its outputs
     before the run so the skip path is genuinely taken).
   - `--exclude-dynamic-system-prompt-sections` argv injection on an isolated-worktree task
     (verify via the FAKE/deterministic executor's recorded argv, since a live Claude CLI cache
     read/write assertion is out of reach for CI per the design doc §1.3/§6 — say so explicitly,
     do not claim a live cache hit was observed unless it genuinely was).
4. Post-run: `ao report timing <run_id>` and `ao report outcomes <run_id>` produce non-empty,
   sensible output — capture actual command output as evidence.
5. Dashboard `run_detail` REST response (or equivalent backend call) for the demo run includes
   the new cache-effectiveness field(s) — capture actual response JSON as evidence.
6. Evidence saved under `output/E-1cecSx-cost-caching-optimization/` and linked from this
   ticket's STATUS.md and the epic's STATUS.md.
7. Epic + all task tickets' STATUS.md/TASK.md updated to `Done` (or explicit `Blocked` with
   assumption/question) once this verification passes.

## Risks
- A live Anthropic API cache-hit assertion is explicitly out of reach without spending real
  account budget for pure R&D (documented in the design doc, not hidden). This task verifies
  the FIX is correctly WIRED (argv injection), not that the live API cache actually hits — that
  distinction must be stated plainly in the final report, not glossed over.

## Dependencies
- Depends on `T-lue4Rz`, `T-J1b0FN`, `T-Ar8HJF`, `T-h1KdlK` all landing first.

## Pseudocode / Algorithm
N/A — this is a verification task, not new production code.

## Schemas / Interface Notes
- N/A.

## Handoff Boundary
- Upstream: all four implementation tasks.
- Downstream: epic completion handoff.

## Artifacts
- Docs/comments: `meta/tickets/E-1cecSx-cost-caching-optimization/T-UJElTR-e2e-verification/`
- Large outputs: `output/E-1cecSx-cost-caching-optimization/`
