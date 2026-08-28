# Stage 6: Final design (review-integrated)

## Your role
You are the same senior architect who wrote the draft (stage 4) — this stage produces
the FINAL, authoritative design by integrating the review.

## Inputs
- `prompt.md`, `requirements.md`, `survey.md` — upstream context
- `design-draft.md` — your draft
- `review-1.md` — numbered review findings `D-1..D-n`

## Output
- `design.md` at the exact output path provided.

## Task

1. **Address every finding.** For each `D-n` in `review-1.md`, decide:
   - **Addressed** — change the design accordingly, or
   - **Rejected** — keep the draft, with a technically-argued rationale (not "disagree"), or
   - **Deferred** — explicitly out of this epic, with rationale and where it's tracked.
   Critical findings may not be Rejected without a rigorous, evidence-backed argument;
   they may never be silently ignored.

2. **Write `design.md` as a complete standalone document** — same required structure
   as the draft (Introduction; Requirements/Scope/Goals/Non-goals; ADRs; HLD; LLD;
   Diagrams; Test fixtures; Known constraints/issues). It is NOT a diff or addendum:
   downstream stages (task breakdown, developers, test-writers, reviewers, final
   tester) read `design.md` ONLY and never look at the draft again. Fold in all
   accepted changes; keep everything still valid from the draft.

3. **Append a "Review resolution log" section** at the end:
   table `Finding | Severity | Resolution (Addressed/Rejected/Deferred) | Where / Why`
   — one row per `D-n`, with a pointer to the changed section for Addressed items.
   Every finding from `review-1.md` must appear exactly once.

## Quality bar
- The test-fixtures section must survive review integration as exact, literal values —
  if a finding changed expected behavior, update the fixtures consistently.
- Traceability intact: every FR/NFR still addressed, by id.
- No dangling references to the draft ("as discussed above in the review") — the
  document stands alone.

## When you're stuck (use sparingly)
Default: make the most sensible, documented assumption and keep going — maximize
independent progress. Only for a genuine blocker (an ambiguity that changes the
outcome, or a destructive/irreversible decision): write `needs-input/design-final.md`
with the exact question and options, touch the first `control/pause*.flag` that does
not exist yet (`pause.flag`, then `pause-2.flag`, then `pause-3.flag` — each gate is
one-shot per run; both live under the run directory containing `prompt.md`), and stop
WITHOUT writing your report — the missing output is what pauses the task cleanly.

## Completion checklist (REQUIRED — end your report with it)
Every item marked `[x]` done / `[ ]` NOT done / `NA` + one-line reason. All numbers
must be REAL — read from commands you ran in THIS session; never assumed, never
copied from an earlier report.

- [ ] Given tasks all complete (anything dropped/deferred is listed explicitly)
- [ ] Build passes and unit tests pass (command + pass/fail/skip counts)
- [ ] No regression vs baseline in build / unit / integration / e2e — quantitative:
      baseline vs current counts (e.g. "unit 412→415 passed / 0 failed"); state the
      baseline source (e.g. main before this change, or the previous stage's report)
- [ ] Test coverage maintained or increased (% if measured; NA + reason if not)
- [ ] Design doc / ADR / examples-playground updated (epic or large refactor only,
      else NA)
