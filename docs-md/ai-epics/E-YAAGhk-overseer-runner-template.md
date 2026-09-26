# Epic: E-YAAGhk-overseer-runner-template

## Metadata
- Epic ID: `E-YAAGhk-overseer-runner-template`
- Title: `overseer-runner` builtin template — recursive wave decomposition, periodic overseer
  checkpoints (alignment/loops/progress), budget-staged graceful degradation
- Owner: architect (design, Done) → dev-epic (decomposition + delivery, this document)
- Created: 2026-09-26
- Last Updated: 2026-09-26
- Status: **In Progress** — 1 of 15 tasks done (`T-pYt478`), 14 remain, 1 explicitly deferred
  (`T-zLHc7Q`, below MVP cut line)
- Ticket mirror: `meta/tickets/E-YAAGhk-overseer-runner-template/` (`EPIC.md`, `STATUS.md`, 15 task
  dirs) — kept in sync with this document at every update, per `meta/tickets/README.md`.

## Authoritative design (not re-derived here)
- HLD+LLD: [`docs-md/overseer-runner-hld.md`](../overseer-runner-hld.md) (25 sections, Rev 2)
- ADR: [`docs-md/adr/ADR-0016-overseer-runner-cadence-and-budget-governance.md`](../adr/ADR-0016-overseer-runner-cadence-and-budget-governance.md)
  (D1–D9)
- G5 bug repro: [`output/E-YAAGhk-overseer-runner-template/repro_emit_lost_on_breaker_trip.py`](../../output/E-YAAGhk-overseer-runner-template/repro_emit_lost_on_breaker_trip.py)
- 15 tickets: `meta/tickets/E-YAAGhk-overseer-runner-template/T-*/TASK.md`

This document is the dev-epic **execution log**: sequencing, delegation, evidence, and progress
against the design above. It does not restate or re-litigate design content already fixed by the
architect (explicit instruction from the epic owner) — see each ticket's own `TASK.md` for exact
acceptance criteria before implementing it.

## Goal / acceptance criteria (restated from EPIC.md, unchanged)
Ship `ao new overseer-runner`: one open-ended `prompt.md` (possibly several distinct asks) is
decomposed at run time into bounded **waves** of emitted sub-DAGs, each wave closed by an
**overseer checkpoint** that judges alignment/loops/progress and emits the next wave, a
stabilization wave, a human hold, or the close-out tail. At 80/90/95% of a fixed `run_budget_usd`
(projected, latched) the overseer moves explore→converge→stabilize→closeout so the deliverable
ends usable, not half-finished. A hard `run_cost_usd` breaker at 100% is the backstop.

## Requirements traceability
Full table with verification methods: `docs-md/overseer-runner-hld.md` §1.2; summarized with
task ownership in `meta/tickets/E-YAAGhk-overseer-runner-template/EPIC.md`. Every MVP requirement
(FR-1..14, FR-16..19) maps to at least one task below; the acceptance-criteria matrix
(HLD §19) is the authoritative FR→task→test mapping and is not duplicated here to avoid drift.

- **MVP** (must ship, this epic): FR-1..14, FR-16, FR-17 (real-LLM smoke, needs spend
  authorization — see Risks), FR-18, FR-19. NFR-1..9.
- **MVP-Should (in this epic, below the cut line)**: FR-15 (nested depth-2 sub-DAGs, `T-zLHc7Q`).
  Per the epic owner's explicit instruction, this is implemented **only if its own ticket says
  it's in scope**. `T-zLHc7Q`'s own `TASK.md`/`STATUS.md` state "Draft (MVP-Should, below the
  sprint cut line)" — i.e. its own ticket does NOT claim in-scope status. **Decision: deferred,
  not implemented, does not block epic completion** (the feature defaults to disabled via
  `max_expanders_per_wave: 0`).
- **Non-MVP (deferred, with reasoning in HLD §1.2)**: NFR-X1..X11.

## Early gate (reviewer + architect pass on the end-to-end flow)
**Already satisfied at design time — not re-run.** Per the epic owner's explicit instruction not
to re-derive or re-litigate the design, dev-epic does not repeat this pass. Evidence it happened:
HLD §23.3 "Phase-4 consultation record" — `manager`, `developer`, `reviewer`, `tester`,
`dev-security`, and `dev-critic` all reviewed Rev 1 of the design (sprint/task breakdown, engine
feasibility of the G5 reorder, the checker rule set, the e2e harness approach, tool/hook security,
and breaker-latch/lock-in risk) against the exact orchestration flow (intake → wave emission →
checkpoint → budget stage machine → close-out tail). Every finding is either incorporated into
Rev 2 or explicitly and reasonedly declined (`STATUS.md` "Consultation summary" table;
HLD §23.3 full record). This is the epic's early gate outcome.

## Decomposition — dependency-ordered execution sequence
Sequencing is by dependency, not calendar sprints (EPIC.md's "Sprint 1/2" framing is the human/
capacity-planning view; this is the same graph read as an execution order for an agent-driven
delivery). One task = one owner (developer/tester/dev-security/architect per its `TASK.md`) plus a
`reviewer`-agent pass recorded in that task's `STATUS.md`, per every task's own acceptance
criteria.

| # | Task | Owner | Depends on | Status |
|---|---|---|---|---|
| 0 | `T-pYt478` emit-settle-atomicity (G5, engine) | developer | — | **Done, landed on epic branch** |
| 1a | `T-ABDjSj` tool M1 (config/state/ledger/budget/hold/unit-gate) | developer | — | Not started |
| 1b | `T-eGXqXH` template scaffold | developer | — | Not started |
| 2 | `T-C6uQJW` tool M2 (loop/progress detectors) | developer | T-ABDjSj | Not started |
| 3 | `T-ltBLUY` contract.md.tmpl + README | developer | T-eGXqXH | Not started |
| 4 | `T-5ZzAZp` agent instructions | developer | T-ltBLUY | Not started |
| 5 | `T-HPJcc6` tool M3a structural checkers | developer | T-ABDjSj, T-ltBLUY | Not started |
| 6 | `T-tAKBBB` tool M3b semantic checkers | developer | T-ABDjSj, T-C6uQJW, T-HPJcc6 | Not started |
| 7 | `T-WruPiv` e2e harness + core scenarios (a)-(d) | tester | T-eGXqXH, T-ltBLUY, T-5ZzAZp, T-ABDjSj, T-C6uQJW, T-HPJcc6, T-tAKBBB | Not started |
| 8 | `T-3FlD46` security review + hardening | dev-security | T-ABDjSj, T-C6uQJW, T-HPJcc6, T-tAKBBB (can run parallel with #7/#9 once checkers merge) | Not started |
| 9 | `T-vmI0jI` e2e failure scenarios (e)-(h) | tester | T-WruPiv, T-ABDjSj, T-tAKBBB | Not started (**no longer blocked on G5** — fix is live on this branch, see below) |
| 10 | `T-23yMMB` live smoke run, real `claude_cli`, ≤$25 | tester | T-WruPiv + all impl tasks | Not started (**needs explicit user spend authorization**) |
| 11 | `T-gbccdr` docs refresh | architect | everything above except T-zLHc7Q | Not started |
| — | `T-zLHc7Q` nested expander sub-DAG (FR-15) | developer | — | **Deferred** (below cut line, not blocking) |

Rows 1a/1b have no cross-dependency and can be delegated concurrently. Row 8 can start as soon as
its deps merge, in parallel with rows 7/9 (per the architect's own sequencing note — security
should not be left to the very end).

### G5 / `T-pYt478` sequencing note (superseded — resolved)
`T-pYt478` was first implemented in isolation on `fix/emit-settle-atomicity` (cut from `main`@
`8c13320`), independently re-verified, and reviewed (approve with nits, addressed). **Update: the
user decided against a standalone PR to `main`.** Commit `3692eac` was cherry-picked onto
`ad/overseer-runner-workflow` as commit `2387503`; the full suite was re-run clean on the epic
branch post-cherry-pick (3983 passed/8 skipped/0 failed, identical); the epic branch (fix
included) was **pushed to `origin/ad/overseer-runner-workflow`** — push only, no PR opened, and
dev-epic will not open one. `fix/emit-settle-atomicity` is now an orphaned local branch (unpushed,
no PR) — the fix ships as part of this epic's own eventual PR to `main` instead, and that PR
description should call out commit `2387503` as a distinct, independently-justified engine
correctness fix (also protects `routed-runner`) rather than attribute it only to this template.

**Consequence: `T-vmI0jI` is no longer blocked.** Its only real remaining dependency is
`T-WruPiv` (the e2e harness).

## Evidence log

### T-pYt478 (Done)
- Bug independently reproduced by dev-epic on unmodified `main`@`8c13320` before any code was
  written: `run1: failed {'emit': 'succeeded'} injected= []` → `run2: succeeded {'emit':
  'succeeded'} injected= []` — exact match to the architect's documented repro.
- Fix implemented on `fix/emit-settle-atomicity` (off `main`@`8c13320`) by a `developer` subagent
  with an explicit change-scope boundary (only `engine.py::_settle_completed_task`'s ordering +
  named/new test files; nothing under `templates/`, `meta/`, `docs-md/`).
- Independently re-verified by dev-epic (not trusting the subagent's self-report): read the full
  `git diff`, read the full new test file, and re-ran every command myself:
  - `pytest tests/test_emit_settle_atomicity.py -v` → **6 passed**.
  - Full `pytest -q` → **3983 passed, 8 skipped, 0 failed** (169.78s) — zero regressions.
  - `ruff check .` → all checks passed. `ruff format --check .` → 306 files already formatted.
  - `mypy src` (exact CI command) → 4 errors, all in `_version.py`; confirmed **pre-existing** via
    `git stash` + re-run against unmodified `main`@`8c13320` (identical 4 errors).
  - `git status --short` → only `engine.py` (modified) + the new test file — no pre-existing test
    touched, so no NFR-2 regression-gate allowlist entry was needed.
- Reviewed by a `reviewer` subagent: **approve with nits**, no blocking issues. It independently
  reproduced the bug itself (stashed the fix, re-ran the new tests against pre-fix code — 3 of 6
  correctly failed, proving the suite isn't tautological). One substantive finding: two test
  docstrings (`test_plain_resume_after_trip_dispatches_injected`,
  `test_monitor_extend_at_emitter_boundary_lets_injected_tasks_run`) overstated what they prove,
  since the `injected_task_count` breaker type structurally couldn't lose the manifest pre-fix
  (only delayed detection by one boundary) — the genuine "lost forever" case is a count-independent
  condition like `stop_file`, already covered by a different test in the same file. dev-epic
  corrected both docstrings in place to state accurately what they verify (latch semantics /
  consult fall-through), re-ran (still 6 passed, ruff clean), and committed.
- Committed locally: `3692eac` on `fix/emit-settle-atomicity`.
- **Landed (update):** user decided against a standalone PR to `main`. Cherry-picked as commit
  `2387503` onto `ad/overseer-runner-workflow`, full suite re-verified clean post-cherry-pick
  (3983 passed/8 skipped/0 failed, identical), branch pushed to
  `origin/ad/overseer-runner-workflow` (push only, no PR). `fix/emit-settle-atomicity` is now
  orphaned (unpushed, no PR, superseded by the cherry-pick) — the fix ships as part of this epic's
  eventual PR to `main`, called out separately in that PR's description.
- Full detail: `meta/tickets/E-YAAGhk-overseer-runner-template/T-pYt478-emit-settle-atomicity/STATUS.md`.

### Remaining 14 tasks
Not started. Full `TASK.md` acceptance criteria read for all 15 tickets during decomposition
(this document's sequencing table above reflects that read) — no task will be implemented from a
guess at scope.

## Risks & blockers
- **Resolved:** `T-pYt478` is no longer a blocker for anything — it's live on
  `ad/overseer-runner-workflow` (commit `2387503`, pushed to origin). No task in the remaining 14
  depends on a separate merge to `main` any more.
- `T-23yMMB` (live smoke run) spends real money against a real LLM (`claude_cli`), capped at $25
  per the architect's design. Per the epic owner's explicit instruction, **dev-epic will not run
  this without first flagging it back for explicit spend authorization** — this is a real-money
  action outside pure code changes, not something to just run because a ticket's acceptance
  criteria call for it.
- `T-zLHc7Q` (nested expanders) is deferred per its own ticket's below-cut-line status; documented
  above, does not block closure.
- Design-level risks (LLM overseer ignoring stages, holds rendering as `failed`, tamper-evident
  vs. tamper-proof governance, G5 blast radius) are catalogued in HLD §23 and were not
  re-litigated; `T-pYt478`'s specific blast-radius risk is closed (evidence above).
- Repo-wide constraint carried forward: dev-epic does not push any branch or open/create any PR
  for the rest of this epic — everything stays local except the epic branch itself, which the user
  already pushed (push only, no PR) as recorded above.

## Next actions
1. Proceed into the remaining 14 tasks (go-ahead confirmed) — parallel track `T-ABDjSj` +
   `T-eGXqXH` first (no cross-deps), then down the sequencing table.
2. Before `T-23yMMB`: explicitly ask for spend authorization, don't just run it.
3. At the next natural milestone (or the spend-authorization point, whichever comes first): report
   back with evidence.

## Pre-close checklist (tracked against `.claude/agents/dev-epic.md`'s mandatory list — epic is
**not** closed; this is a running scorecard, updated every iteration)
- [x] Epic context doc created under `docs-md/ai-epics/`, mirrored into `meta/tickets/<EpicID>/`
      (this document + `EPIC.md`/`STATUS.md` updated in the same pass).
- [x] Requirements categorized MVP/Non-MVP/Stretch with traceability (inherited from the design's
      own HLD §1.2 table + EPIC.md; every MVP requirement maps to a task in the sequencing table
      above).
- [x] Early gate run: satisfied at design time (HLD §23.3), recorded above, not re-run.
- [x] Every delegated agent given an explicit change-scope boundary — `T-pYt478`'s `developer` and
      `reviewer` subagent prompts both stated exact allowed/forbidden files.
- [x] Quantifiable checkpoints tracked this iteration: `T-pYt478` evidence above (test counts,
      pass/fail, coverage of named regression-risk files).
- [ ] Late gate (end-to-end path via `tester` with real evidence) — **not yet**, epic is 1/15
      tasks in.
- [x] Ticket status synced consistently across `TASK.md`/`STATUS.md` and the epic
      `EPIC.md`/`STATUS.md` rollup, with `By/Role/Date` attribution, for everything done so far.
- [ ] Final handoff (done vs. not-done vs. next steps vs. artifact pointers) — **N/A yet**, epic in
      progress; see "Next actions" above for the current-iteration equivalent.
