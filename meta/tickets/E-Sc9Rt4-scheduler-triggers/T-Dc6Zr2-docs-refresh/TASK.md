# TASK: T-Dc6Zr2-docs-refresh

## Metadata
- Task ID: `T-Dc6Zr2-docs-refresh`
- Epic ID: `E-Sc9Rt4-scheduler-triggers`
- Owner: architect agent (or dev-epic at close)
- Created: 2026-09-06
- Last Updated: 2026-09-06
- Status: Draft
- Estimate: 2 days

## Requirements Mapping
- Requirement IDs: all (post-implementation reconciliation)

## Description
**Post-implementation reconciliation.** Reconcile every document this epic touched with what was
actually built — including, explicitly, the places where the implementation *deviated* from the
design. A design doc that quietly describes something other than the shipped code is worse than
no design doc, because the next epic will build against the fiction.

This task closes only after each claim in the refreshed docs has been checked against the merged
code, not against an earlier task's report of it.

Files you own (create/edit freely):
- `docs-md/scheduler-triggers-hld.md` (status header → shipped; every deviation folded in)
- `docs-md/adr/ADR-0014-service-owned-scheduler-and-triggers.md` (status header; a
  "Late-gate corrections (date)" section if `T-Se4Bk5` changed any documented decision)
- `docs-md/ai-epics/E-Sc9Rt4-scheduler-triggers.md` (evidence log, traceability table, close-out)
- `docs-md/lld-agent-orchestrator.md` (§7 Scheduler — currently says event triggers are
  "non-MVP" and lists no scheduler CLI; both are now false)
- `meta/ROADMAP.md` (§1 status row, §2-series "just landed", §3.2, §4 if a limitation is carried)
- `README.md` (a short `ao schedule` section if the CLI surface warrants it)
- `meta/tickets/E-Sc9Rt4-scheduler-triggers/**` (final status sync)
- `docs-md/guide-*.md` (a new operator guide only if `T-Se4Bk5` or field use shows one is needed)

Do NOT touch: `docs-md/task-isolation-hld.md`, `docs-md/adr/ADR-0013-*.md` (concurrent epic —
reference by name only); other epics' ADRs; `meta/learnings*.md` and `CLAUDE.md` unless the user
asks.

## Acceptance Criteria
1. **HLD reconciliation.** Walk `docs-md/scheduler-triggers-hld.md` section by section against
   the merged code. Every interface signature, constant name, constant *value*, file path, state
   shape, event name, HTTP status and CLI flag in the document matches what shipped. Where it
   does not, the document changes — not the code — and the change is recorded in AC3's deviation
   list. Update the status line to `Accepted / shipped (<date>)` with what verified it.
2. **Constants and paths sweep.** Mechanically extract every named constant and file path
   mentioned in the HLD and ADR (`DEFAULT_EVAL_INTERVAL_SECONDS`, `SCHEDULE_TICK_BUDGET_SECONDS`,
   `MAX_CONSECUTIVE_EVAL_ERRORS`, `MIN_INTERVAL_SECONDS`, `REPLAY_WINDOW_SECONDS`,
   `NONCE_CACHE_SIZE`, `MAX_WEBHOOK_BODY_BYTES`, `EVENT_LOG_MAX_BYTES`, `REQUEST_TTL_SECONDS`,
   `DEFAULT_UNTIL_MAX_RUNS`, `WATCH_SCAN_BUDGET_SECONDS`, the state-dir layout, the five HTTP
   routes) and confirm each against `src/`. Report the sweep, not just its conclusion.
3. **Deviation list.** A dedicated subsection — "Deviations from the original design" — naming
   every place the implementation diverged, why, and whether the divergence was better. If there
   were none, say so explicitly; a silent absence reads as "not checked".
4. **ADR-0014 reconciliation.** Status header updated. Any decision that `T-Se4Bk5`'s gate or
   implementation reality overturned gets a dated "Late-gate corrections" section in the same
   numbered, `**bold restatement.** explanation` style ADR-0012 uses — the decision headings
   themselves are **not** rewritten (that is this repo's established convention: corrections are
   recorded, not retconned).
5. **`lld-agent-orchestrator.md` §7** rewritten: the `Scheduler` ABC now has five
   implementations, event triggers are no longer "non-MVP", and §12's CLI surface list gains
   `ao schedule`. Its `Trigger` model snippet gains `interval_seconds` and `"interval"`.
6. **ROADMAP sync** (the close ritual): §1's status row for "Cron / event triggers" moves off
   "Spec'd, not scheduled"; a "just landed" subsection is added in the §2-series style; §3.2 is
   struck or reduced to what genuinely remains; §3.1 is left **untouched and still highest
   priority** — the webhook HMAC is one endpoint, not the auth epic, and the docs must not imply
   otherwise; §4 gains any limitation this epic carried (the DST fall-back double fire, the
   at-most-once missed run on a crash mid-fire, the in-memory nonce/rate reset on restart, the
   microsecond `launch_id` resolution).
7. **ai-epics page** completed: the evidence log carries dated entries with hard numbers (suite
   counts and deltas at each task boundary, ruff/mypy status, coverage), a
   `### Final MVP requirement traceability` table `| Requirement | Landed in | Test coverage |`
   with per-file test counts, and a close-out with the deferred items and their reasons.
8. **Ticket sync** (tickets README rules 8 and 9): every task's `TASK.md`/`STATUS.md` state,
   `EPIC.md`'s task checkboxes, and the epic `STATUS.md` rollup agree — including that the
   wording and any counts (findings, tests) match across all of them.
9. **Operator-facing correctness.** The docs must let someone who has never read this epic answer,
   from the docs alone: how do I make a workflow run nightly; what happens if my laptop was off;
   what happens if the previous run is still going; how do I stop it; how do I point a git push
   at it; and why did my schedule not fire. Each has a findable answer.
10. **Every claim is verified against code, not against a report.** State in `STATUS.md` which
    claims were spot-checked and how. This task is not Done on "the docs were edited"; it is Done
    on "the docs were confirmed true".

## Risks
- Docs-refresh tasks get cut when a sprint slips. Sprint 3 is deliberately sized at 4 of 15 days
  so this one cannot be squeezed — if it is being cut anyway, that is a signal to escalate, not
  to trim ACs.
- The most likely divergence to go unnoticed is a constant whose *value* changed during
  implementation (a timeout, a cap, a window). AC2's mechanical sweep exists precisely because a
  section-by-section read misses those.
- ROADMAP §3.1 must not be softened. It is genuinely still the top priority, and one
  HMAC-protected endpoint could easily read as "auth is partly done".
- Editing `meta/ROADMAP.md` is out of bounds for every other task in this epic and in bounds only
  here, at close.

## Dependencies
- `T-Se4Bk5` must have closed (its findings and any late-gate corrections are inputs). Every
  implementation task Done.

## Pseudocode / Algorithm
```text
FOR doc IN [scheduler-triggers-hld.md, ADR-0014, lld-agent-orchestrator.md §7/§12,
            ai-epics page, ROADMAP §1/§2/§3.2/§4, README]:
    FOR claim IN extract_claims(doc):            # signatures, constants, paths, routes, flags
        actual = read_from_source(claim)
        IF actual != claim:
            IF the code is right:  fix the doc;  append to DEVIATIONS
            ELSE:                  file a defect against the owning task -- do NOT edit src/ here
    record(doc, claims_checked, claims_corrected)

ASSERT deviations_section_exists(scheduler-triggers-hld.md)     # even if it says "none"
ASSERT roadmap_section_3_1_unchanged()                          # auth is NOT partly done
ASSERT ticket_states_agree(EPIC.md, STATUS.md, every T-*/{TASK,STATUS}.md)
```

## Schemas / Interface Notes
- Interface / API: documents them; defines none.
- Spec / data schema: confirms `specs/schedules.schema.json` and the `$defs.trigger` delta are
  documented exactly as shipped.
- Triggers / events: confirms the documented event vocabulary matches the emitted one — a
  mechanical diff of HLD §12's table against `grep -rn 'emit("' src/`.
- Artifacts: `docs-md/**`, `meta/ROADMAP.md`, `README.md`, this epic's ticket folder.

## Handoff Boundary
- Upstream: `T-Se4Bk5` (gate findings), every implementation task's `STATUS.md` evidence.
- Downstream: none — this task closes the epic. Anything it could not close is carried into
  `meta/ROADMAP.md` §4 with its consequence, per that file's own §5 instructions.

## Artifacts
- Docs/comments: `meta/tickets/E-Sc9Rt4-scheduler-triggers/T-Dc6Zr2-docs-refresh/`
- Large outputs: N/A
