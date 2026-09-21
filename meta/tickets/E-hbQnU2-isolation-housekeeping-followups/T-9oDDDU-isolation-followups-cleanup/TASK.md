# TASK: T-9oDDDU-isolation-followups-cleanup

## Metadata
- Task ID: `T-9oDDDU-isolation-followups-cleanup`
- Epic ID: `E-hbQnU2-isolation-housekeeping-followups`
- Owner: unassigned
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: Draft (not started)
- Estimate: < 3 days (four independent sub-items, each well under a day)

## Requirements Mapping
- Requirement IDs: FR-1, FR-2, FR-3, FR-4, NFR-1 (see `EPIC.md`)

## Description
Four small, previously-unticketed cleanups in the per-task git isolation subsystem
(`src/agent_orchestrator/isolation/`), each independently landable:

1. **FR-1 — worktree-list parsing robustness.** `parse_worktree_list` (isolation module —
   locate via `grep -rn "worktree list --porcelain" src/agent_orchestrator/isolation/`) parses
   `git worktree list --porcelain` output line-based. Accepted at the time (`T-Gt4Pw8` C-9)
   because engine-constructed worktree paths never contain a newline, but that invariant is
   not enforced anywhere. Either switch to `-z` (NUL-delimited) parsing, or add an explicit
   guard/test that pins the no-newline invariant so a future change can't silently break this.
2. **FR-2 — de-duplicate `_pid_alive`/`_read_os_boot_id`.** Triplicated across
   `src/agent_orchestrator/ui/processes.py`, `src/agent_orchestrator/service/supervisor.py`,
   `src/agent_orchestrator/isolation/runlock.py`. Extract to one leaf module and update all
   three call sites.
3. **FR-3 — T2 resolver dispatch-window race.** Investigate whether a third task landing
   during the merge-resolver's own dispatch window (`resume_integration`) can race with the
   resolver's in-flight work. Either fix the race if real, or write up why it can't happen
   (e.g. an existing lock already closes it) and record that in
   `docs-md/task-isolation-hld.md`.
4. **FR-4 — `GitRepo.diff_patch()`.** `export_previous_patch` currently reaches
   `GitRepo._run(["diff", ...])` directly (no public raw-diff method exists). Add
   `GitRepo.diff_patch(...)` as a real public method with the same behavior, and switch
   `export_previous_patch` to call it.

## Acceptance Criteria
1. FR-1: a test proves the chosen fix (either `-z`-parsing round-trips a worktree path
   containing a newline correctly, or an explicit assertion/test documents and enforces the
   no-newline invariant with a clear failure message if violated).
2. FR-2: `_pid_alive`/`_read_os_boot_id` exist in exactly one module; the three call sites
   import from it; existing tests for all three call sites still pass unmodified (behavior
   preserved).
3. FR-3: either (a) a regression test demonstrates the race is fixed, or (b) a documented,
   evidence-backed explanation is added to `docs-md/task-isolation-hld.md` explaining why no
   race exists (cite the specific lock/mechanism that closes the window).
4. FR-4: `GitRepo.diff_patch()` exists, is covered by a unit test, and
   `export_previous_patch` no longer calls `GitRepo._run` directly for this purpose.
5. Full existing suite (`.venv/bin/python -m pytest -q -m "not real_llm and not swebench"`)
   passes with no regressions; `ruff check`/`ruff format --check`/`mypy src` clean on touched
   files.

## Risks
- FR-3 is the only item that might turn out to be a real bug rather than a documentation gap;
  budget investigation time before committing to "fix" vs. "document as safe."
- Touches `isolation/`, `ui/`, and `service/` — shared modules; keep each fix's diff minimal
  and reviewed independently rather than landing all four in one large change.

## Dependencies
- None — can be picked up independently of any other open epic.

## Pseudocode / Algorithm
```text
N/A — this task is a set of four small, independent code fixes/investigations, not a single
algorithm. See Description above for each.
```

## Schemas / Interface Notes
- Interface / API (Python protocol/ABC or CLI/HTTP): `GitRepo.diff_patch()` (new public
  method); no CLI/schema surface changes expected for any of the four items.
- Spec / data schema (JSON/YAML): N/A — no `specs/*.schema.json` changes expected.
- Triggers / events (cron/event): N/A
- Artifacts (inputs/outputs by path): N/A — internal code cleanup, no new artifacts.

## Handoff Boundary
- Upstream: `docs-md/task-isolation-hld.md` ("Deferred with rationale, discovered during
  implementation" section) and `meta/tickets/E-Wk9Tz3-task-isolation/T-Lr6Ka3-llm-resolver-and-rerun/STATUS.md`
  are the source record for all four items — read them before starting.
- Downstream: none — this is a leaf cleanup task.

## Artifacts
- Docs/comments: `meta/tickets/E-hbQnU2-isolation-housekeeping-followups/T-9oDDDU-isolation-followups-cleanup/`
- Large outputs: none expected.
