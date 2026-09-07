# TASK: T-Dr5Yq6-docs-refresh

## Metadata
- Task ID: `T-Dr5Yq6-docs-refresh`
- Epic ID: `E-Wk9Tz3-task-isolation`
- Owner: unassigned (architect or developer)
- Created: 2026-09-06
- Last Updated: 2026-09-07
- Status: Draft
- Estimate: 1 day

## Requirements Mapping
- Requirement IDs: post-implementation reconciliation for all FR/NFR · Design: HLD (whole document)
- Review findings folded in: **R-13** (LFS non-goal), **S-8** (reserved `ao/` namespace), plus
  reconciliation of HLD §24 "Review dispositions" against what actually shipped. Estimate unchanged at
  1 day.

## Description
**Post-implementation reconciliation.** Bring `docs-md/` into line with what was actually built,
including every deviation from this design, and close the epic's paperwork. Mark complete **only**
after confirming each claim against the merged code — read the source, do not trust the tickets.

Files you own:
- `docs-md/task-isolation-hld.md` (edit — add an "As-built deviations" section, mirroring
  `parallel-execution-hld.md` §14; correct any statement the implementation contradicted)
- `docs-md/adr/ADR-0013-per-task-git-isolation-and-rebase-integration.md` (edit — status
  `Proposed` → `Accepted / shipped` + an implementation-notes addendum; **do not rewrite the
  decisions**)
- `docs-md/ai-epics/E-Wk9Tz3-task-isolation.md` (edit — final evidence log + traceability table)
- `docs-md/hld-agent-orchestrator.md` (edit — one line in the feature-design-docs list pointing at
  this HLD/ADR, matching how `parallel-execution-hld.md` is referenced)
- `docs-md/parallel-execution-hld.md` (edit — **one** cross-reference note in §12 saying the
  write-conflict follow-on shipped as ADR-0013; no other change)
- `meta/tickets/E-Wk9Tz3-task-isolation/EPIC.md` + `STATUS.md` and every task's `TASK.md` + `STATUS.md`
  (status sync)

Do NOT touch: `meta/ROADMAP.md`, `CLAUDE.md`, `meta/learnings*.md`, other ADRs, `src/`, or
`../ao-runner-finplan`. **Report** the ROADMAP edits that are needed (§1 table row, §3.4, §4) to the
epic owner rather than making them — that file is owned elsewhere.

## Acceptance Criteria
1. Every factual claim in `task-isolation-hld.md` is re-verified against the merged source, with
   file:line references refreshed. Anything the implementation changed is corrected **in place** and
   also listed in a new "As-built deviations" section that says *what* changed and *why*, in the style
   of `parallel-execution-hld.md` §14.
2. ADR-0013's status header becomes `Accepted / shipped (<date>)`, with an "Implementation notes"
   section recording: which decisions held exactly, which were refined, and any decision the build
   proved wrong. The decision bodies themselves are **not** rewritten (an ADR records what was decided
   and why, not what we wish we had decided).
3. `docs-md/ai-epics/E-Wk9Tz3-task-isolation.md` carries the final evidence log (baseline → final test
   counts at each task boundary, ruff/mypy deltas, the security-pass outcome) and a **requirement
   traceability table** mapping every FR/NFR to the landed module and its dedicated tests.
4. Operator documentation exists and is accurate: how to opt in, where worktrees live, how to set
   `verify_command` and `resolvers`, what each `integration.*` event means, how to recover from a T4
   failure (retained worktree + branch + `ao resume`), how `ao prune --worktrees-only` reaps orphans,
   and the cold-rebuild / shared-build-cache guidance (NFR-6). Verified by **actually following** the
   recovery procedure once against a real temp repo.
5. Every ticket in the epic (12 tasks + the epic) has `Status` consistent across `TASK.md`,
   `STATUS.md` and the epic's checkbox list, each with `By/Role/Date/Comment` attribution — no item
   marked done in one file and open in another.
6. Open questions OQ-1..OQ-5 and HLD §20's user decisions are each resolved, re-scoped, or explicitly
   carried forward with a named owner. Deferred non-MVP items (§23) are restated as a clean follow-on
   list for the epic owner to consider for the roadmap.
7. A short, explicit list of **needed edits this ticket did not make** (`meta/ROADMAP.md` §1 table row,
   §2 "Recently delivered", §3.4 and §4 gap removal) is handed to the epic owner in `STATUS.md`.
8. `uv run pytest -q` still green (docs-only change, but run it to prove nothing was disturbed);
   markdown links resolve (a link-check pass over the edited files).

### Amendments from the 2026-09-07 review gates

9. **Reconcile HLD §24 "Review dispositions" against reality.** Every finding marked *Fixed* must be
   re-verified against the merged code, not against the ticket that claimed it. Anything that shipped
   differently is re-dispositioned in place (Fixed -> Accepted-with-rationale / Deferred, with the
   reason), so §24 remains a truthful record rather than an aspiration. Any finding that shipped
   **unfixed** without a recorded rationale is a blocker for closing the epic.
10. **R-13 / S-8 documentation duties.** The operator docs state (a) Git LFS is not handled, with the
    revisit trigger (`isolation.env` + `GIT_LFS_SKIP_SMUDGE`, and the NFR-6 disk guidance), and
    (b) `refs/heads/ao/**` and `refs/ao/**` are reserved for the engine.
11. **Verify the recovery procedure by executing it.** The T4 recovery path (retained worktree +
    branch -> hand-resolve -> `ao resume`) is walked end to end once against a real temp repo, and the
    docs are corrected wherever it does not work as written. Same for `ao prune --worktrees-only`
    reaping an orphan.
12. **Report, do not make, the `meta/ROADMAP.md` edits.** §1's capability-table row, §2 "Recently
    delivered", and the §3.4 / §4 gap removal all need updating — hand the exact proposed text to the
    epic owner in `STATUS.md`. Also report whether ADR-0014 / `scheduler-triggers-hld.md`'s
    "per-workspace cap of 1 until the isolation epic states a multi-run policy" note can now be
    relaxed, given ADR-0013 D8 states that policy (recommendation: **no** — `workspace_lock: require`
    makes a cap violation degrade safely, but the cap is still the better default).

## Risks
- Docs drifting from code is the failure this ticket exists to prevent — so "read the ticket and
  believe it" is the anti-pattern. Every claim is checked against source.
- Editing `parallel-execution-hld.md` risks scope creep into a shipped document. Keep it to the single
  cross-reference note.
- Two sources of truth: the HLD and the ai-epics page. The HLD is the design of record; the ai-epics
  page is the narrative/evidence log. Do not duplicate content between them.

## Dependencies
- Upstream: `T-Ee3Mn8-e2e-and-review` (its evidence is this ticket's input).
- Downstream: none — this closes the epic.

## Pseudocode / Algorithm
```text
1. git diff <epic base>..HEAD --stat  -> the authoritative list of what actually changed
2. For each HLD section, open the named source file and confirm or correct.
3. Record deviations; refresh line references; update ADR status + implementation notes.
4. Fill the traceability table from the real test names.
5. Walk the T4 recovery procedure end to end against a temp repo; fix the docs where it does not work.
6. Sync every ticket's status; hand the ROADMAP edits to the epic owner.
```

## Schemas / Interface Notes
- Interface / API: none.
- Spec / data schema: none.
- Triggers / events: none.
- Artifacts: docs only.

## Handoff Boundary
- Upstream: the merged epic + `T-Ee3Mn8`'s evidence.
- Downstream: the epic owner applies the reported `meta/ROADMAP.md` edits.

## Artifacts
- Docs/comments: `meta/tickets/E-Wk9Tz3-task-isolation/T-Dr5Yq6-docs-refresh/`
- Large outputs: none

---
- By: architect · Role: architect · Date: 2026-09-07 · Comment: Phase-2 amendment. Added the duty to
  re-verify HLD §24's dispositions against merged code (so the review record cannot rot into a claim),
  the R-13/S-8 documentation items, an execute-it-don't-describe-it check on the T4 recovery procedure,
  and an explicit hand-off of the ROADMAP + ADR-0014 cross-epic note to the epic owner. Estimate
  unchanged at 1 day.
