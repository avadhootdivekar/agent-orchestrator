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
| 1a | `T-ABDjSj` tool M1 (config/state/ledger/budget/hold/unit-gate) | developer | — | **Done, reviewed** |
| 1b | `T-eGXqXH` template scaffold | developer | — | **Done, reviewed** |
| 2 | `T-C6uQJW` tool M2 (loop/progress detectors) | developer | T-ABDjSj | **Done, reviewed** |
| 3 | `T-ltBLUY` contract.md.tmpl + README | developer | T-eGXqXH | **Done, reviewed** |
| 4 | `T-5ZzAZp` agent instructions | developer | T-ltBLUY | **Done, reviewed** (+ branch_policy amendment pending) |
| 5 | `T-HPJcc6` tool M3a structural checkers | developer | T-ABDjSj, T-ltBLUY | **Done, reviewed** |
| 6 | `T-tAKBBB` tool M3b semantic checkers | developer | T-ABDjSj, T-C6uQJW, T-HPJcc6 | **Done, reviewed** |
| 7 | `T-WruPiv` e2e harness + core scenarios (a)-(d) | tester | T-eGXqXH, T-ltBLUY, T-5ZzAZp, T-ABDjSj, T-C6uQJW, T-HPJcc6, T-tAKBBB | **Done, independently re-verified with primary evidence** |
| 8 | `T-3FlD46` security review + hardening | dev-security | T-ABDjSj, T-C6uQJW, T-HPJcc6, T-tAKBBB (can run parallel with #7/#9 once checkers merge) | **Done, independently verified** |
| 9 | `T-vmI0jI` e2e failure scenarios (e)-(h) | tester | T-WruPiv, T-ABDjSj, T-tAKBBB | **Done, independently re-verified with primary evidence** |
| 10 | `T-23yMMB` live smoke run, real `claude_cli`, ≤$25 | tester | T-WruPiv + all impl tasks | Not started (**needs explicit user spend authorization**) |
| 11 | `T-gbccdr` docs refresh | architect | everything above except T-zLHc7Q | Not started |
| — | `T-zLHc7Q` nested expander sub-DAG (FR-15) | developer | — | **Deferred** (below cut line, not blocking) |

Rows 1a/1b have no cross-dependency in the design's own terms and were intended as a parallel pair
(one developer each). **Sequencing deviation (dev-epic's own call, not a design change)**: run
them SEQUENTIALLY (1a then 1b) rather than concurrently, because both would otherwise race on the
same file — `T-eGXqXH`'s own ticket says to "commit a placeholder [`tools/overseer_tool.py`] that
compiles and contains no `{{`... until [T-ABDjSj] lands", i.e. the two tasks share ownership of
one file at the scaffold boundary. Running them one after another in a single shared working
directory (no worktree-per-task isolation was used, since only these two tasks in the whole graph
have this overlap) avoids any race, and running `T-ABDjSj` FIRST means `T-eGXqXH` scaffolds around
the REAL M1 tool content from the start instead of a throwaway placeholder that would need
replacing later — strictly better, not just safer. Row 8 can start as soon as its deps merge, in
parallel with rows 7/9 (per the architect's own sequencing note — security should not be left to
the very end).

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

### T-ABDjSj (Done, reviewed)
- New `src/agent_orchestrator/templates/builtin/overseer-runner/tools/overseer_tool.py` (stdlib
  only, ~1700 lines) implementing module M1: config validation (CFG-0..3), the `state.json` loader
  (pinned to real `models.RunState`/`TaskRunState` fields, not invented), hash-chained ledger
  (`append_chained`/`verify_ledger_chain`/idempotent `ingest_ledger`), the budget stage machine
  (`derive_budget` pure function + `compute_budget` I/O orchestrator, matching HLD §8.2 exactly,
  including the monotonic latch and the FR-16 override), cadence math, hold gate (D6), charter-lock
  verify (INT-1), `unit-gate` (FR-18), `request-closeout` (FR-19), plus M2-stub/M3-reservation
  scaffolding for the three tasks that add to this same file next.
- 127 new tests across 3 files (124 original + 3 dev-epic-added regression tests), 99% coverage
  (761 statements, 9 missed, all defensive/real-clock branches).
- dev-epic independently re-verified before requesting review (read ~400 of 1694 lines covering
  the trickiest logic, re-ran all tests/full-suite/ruff/mypy myself — all matched exactly), then
  requested a `reviewer` pass which closely read the remaining ~840 lines dev-epic hadn't checked.
- Reviewer verdict: **approve with nits**. Independently traced the hash chain by hand, proved
  render-safety via the actual `_render` function (not just a grep for `{{`), reproduced the
  coverage numbers exactly, and cross-checked every field name against the real `models.py`/
  `hooks.py`. One substantive non-blocking Warning: three call sites bypassed the rule-id path on
  malformed JSON input (`hold_gate`, `verify_charter_lock`, `resolve_run_id_for_instance`) —
  exactly on the tamper/corruption paths where a diagnosable failure matters most. dev-epic fixed
  all three (consistent with `load_config`'s own error-wrapping pattern) and added one regression
  test per site. Re-verified post-fix: 127 passed, 99% coverage (line numbers shifted only), full
  suite **4110 passed, 8 skipped, 0 failed**, ruff/mypy clean.
- One real latent bug the developer found and fixed while writing tests (not from review): a
  `dry_run` flag wasn't threaded from `ckpt_prep` into `effective_budget`/`compute_budget`, so a
  `ckpt-prep --dry-run` with a newly-honorable override would have written a real ledger line
  despite `--dry-run`.
- Full detail: `meta/tickets/E-YAAGhk-overseer-runner-template/T-ABDjSj-tool-state-ledger-budget/STATUS.md`.

### T-eGXqXH and T-C6uQJW (Done, reviewed — built concurrently)
Ran these two genuinely in parallel (verified disjoint file ownership first: `template.yaml`/
`workflow.json.tmpl`/`overseer-config.json.tmpl`/`prompt.md.tmpl`/its own test file vs.
`overseer_tool.py`'s M2 section + its own test file — no overlap). Both subagents were briefed
explicitly about the concurrency and told not to run `git add -A`/commit/stash.

**T-eGXqXH**: `template.yaml`, `workflow.json.tmpl`, `overseer-config.json.tmpl`, `prompt.md.tmpl`
matching HLD §13.1/§13.2/§13.4 field-for-field (verified by dev-epic reading all 4 files in full
against the design), plus 28 new tests. Reviewer verdict: approve with nits.
- **Warning #1 (tracked, not fixed here)**: `template.yaml` omits an `overseer-contract.md.tmpl`
  `files:` entry that HLD §13.1 and this ticket's own AC2 call for, even as a stub — real but
  harmless today (rendering already fails earlier at the missing `instructions/` asset).
  **Resolution**: handed to `T-ltBLUY` (the very next task, which authors that file's content
  anyway) to also add the `files:` entry, so it lands together rather than lingering unowned.
- **Warning #2 (tracked as a known limitation, correctly out of scope)**: confirmed by the
  reviewer reading `instantiate()`/`cli.new_cmd` directly — there is NO rollback-on-failure path
  anywhere in the engine for ANY template (pre-existing, generic, not introduced here). AC6's "no
  half-written instance is left" is therefore not fully verifiable yet. **Not fixed** — this would
  mean touching shared `templates/__init__.py`, which every builtin template depends on, well
  outside this epic's narrow-change-scope. Recorded here as a candidate follow-up ticket
  (`templates.instantiate()`: stage writes atomically, or track-and-rollback on failure) for
  whoever owns the templates module next; `T-gbccdr`'s deviations section should also note it.

**T-C6uQJW**: all 10 MVP detectors (`period_repeat`, `mirror_flipflop`, `content_oscillation`,
`breadcrumb_integrity`, `stall`, `repeated_failure`, `attempt_cap`, `ask_starvation`,
`blocked_units`, `prompt_changed`) plus `compute_progress`, extending M1's path-history stub with
real git-derived tracking (`repo_heads`, bounded `git diff`/`git status`). dev-epic hand-traced
`detect_period`/`detect_mirror` against every AC1/AC2 table row by manual execution before
requesting review (including the two "smallest period/mirror wins" trick cases) — correct. 52
new tests (51 + 1 dev-epic regression test), 99% coverage. Reviewer independently reproduced
AC7's determinism via an adversarial shuffle/reverse script and hand-traced AC3's git-diff-union
mechanism against a real fixture. Verdict: approve with nits.
- **W1 (fixed by dev-epic)**: the path-history trim's union order placed breadcrumb-DECLARED
  paths before git-DERIVED ones, so a busy wave whose declared paths alone reached
  `MAX_TRACKED_PATHS` would silently drop exactly the undeclared/evasive paths this whole feature
  exists to catch — inverting its intended security-relevant priority. Fixed (git-derived paths
  now ordered first in the union) and regression-tested with a real git fixture.
- **W2 (handed off, not fixed)**: oscillation/period/mirror/repeated-failure signals re-fire at
  every subsequent checkpoint once triggered, with no expiry or acknowledgement-suppression —
  could waste overseer budget re-litigating an already-`accept`ed signal indefinitely. Doesn't
  violate any stated AC; handed to `T-tAKBBB` (which owns `signal_responses`/OV-R12 enforcement)
  to consider whether an accepted signal should suppress re-emission until its condition changes.
- Also fixed W3 (a DRY nit, duplicated `unit_lines` filter logic) by extracting a shared
  `_unit_lines_through(inst, k)` helper.

**Cross-task issue found and fixed by dev-epic (not a review finding from either task)**:
T-C6uQJW's new git-subprocess calls tripped `tests/isolation/test_security_guards.py`'s
repo-wide guard (every git shell-out must route through `isolation/git.py`'s `GitRepo` or carry a
justified allowlist entry). Added a justified entry — the tool is deliberately standalone
(stdlib-only, zero repo-internal imports, NFR-5) and structurally cannot import `GitRepo`
(ADR-0016 D4). Re-verified that guard's own suite (11 passed).

Combined evidence after both tasks + all fixes: full suite **4190 passed, 8 skipped, 0 failed**,
coverage 99% (1075 stmts/10 miss), ruff/mypy clean throughout.
Full detail: `T-eGXqXH-template-scaffold/STATUS.md`, `T-C6uQJW-tool-loop-progress-detectors/STATUS.md`.

### T-ltBLUY (Done, reviewed)
`overseer-contract.md.tmpl` (332 lines) and `README.md` (253 lines), plus the one `files:` entry
in `template.yaml` that `T-eGXqXH`'s review deliberately deferred here. The contract's "Hard
rules" section is grounded in the actual shipped tool (grepped for every rule id it really
raises) rather than retyped from the design doc, split honestly into "currently enforced" (17
ids) vs. "forthcoming" (`OV-R1`-`OV-R16`+`OV-R13c`, for `T-HPJcc6`/`T-tAKBBB` to implement against
verbatim). 19 net new tests (28 pre-existing -> 47 total, one pre-existing test renamed as part of
the disclosed boundary update below).

**Disclosed, accepted boundary deviation**: two of `T-eGXqXH`'s own pre-existing tests explicitly
named `T-ltBLUY` as their own trigger for an update (their docstrings literally said "not yet --
that's T-ltBLUY"). Landing this ticket's mandatory `files:`-entry requirement necessarily flips
both assertions (4->5 files; drop the now-obsolete "contract doesn't exist yet" check). dev-epic
reviewed this before accepting it: it's a correctly-anticipated, minimal, disclosed update to a
scope-boundary test's "not yet" clause, not scope creep or a hidden violation of the "don't touch
existing tests" instruction.

Reviewer verdict: approve with nits. Independently grepped the tool to confirm the "currently
enforced" vs. "forthcoming" rule-id split is accurate; verified every README factual claim against
real code rather than the design doc (the re-render/no-`--force`-flag claim, the
`--extend-breaker --extend-by-same`/`--extend-by-seconds` CLI syntax, the `breakers.py:603` latch
citation, the `--autocompact` 180000/500000 numbers cross-read against `routed-runner`'s own
README) -- all confirmed accurate. Two Warnings, both fixed by dev-epic:
- **W1**: the README didn't disclose that the template isn't runnable end-to-end yet (`intake`'s
  `post_hook` wires to `ov-intake-check`, which doesn't exist as a subcommand until the M3 checker
  tasks land) -- the contract's own "Self-check" section already said this, but the README (what
  an operator reads first) didn't. Fixed: added a "Current epic status" note to Preflight.
- **W2**: `STATUS.md` claimed "27 new tests"; the actual count (verified by the reviewer via
  `git diff`/`grep -c`) is 19 net new (28 pre-existing -> 47 total). Fixed: corrected in both the
  task and epic STATUS.md.

Full detail: `T-ltBLUY-contract-and-readme/STATUS.md`.

### T-5ZzAZp and T-HPJcc6 (Done, reviewed — built concurrently)
Deviated from the originally-planned strict sequence: `T-HPJcc6` only depends on `T-ABDjSj`+
`T-ltBLUY` (both done), not on `T-5ZzAZp`, and the two tasks touch disjoint files (`instructions/`
markdown + its test file vs. `overseer_tool.py`'s new M3 section + its test file) — so they were
run in parallel rather than sequentially, same pattern as `T-eGXqXH`/`T-C6uQJW`.

**T-5ZzAZp**: 8 MVP instruction files, 65 new tests. Reviewer caught a real citation defect in
`00-intake.md` (attributed the charter schema to `overseer-contract.md`, which explicitly disclaims
it) — fixed, now cites HLD §13.4, plus added the missing `acceptance[].id` shape (`A<n>.<m>`) to
both `00-intake.md` and `20-checkpoint.md`.

**T-HPJcc6**: `intake-check`/`ckpt-check` structural half (OV-R1-R10/R15), a new
collect-all-violations rule engine extensible for `T-tAKBBB`, new rule id `CHR-1`. 100 new tests,
99% coverage. Reviewer verdict: approve, 3 non-blocking nits (one trivial comment fix applied).

**Coordinator-flagged Pyright findings, both resolved**: (1) a real type-narrowing issue in the
already-merged `T-ABDjSj`'s `test_overseer_tool_budget.py` (a test helper's `.update()` call) —
fixed and verified with an actual `pyright` run, not just `mypy`, since this repo's `strict=false`
mypy config didn't catch it; (2) a finding in `T-HPJcc6`'s test file, flagged mid-development —
confirmed resolved by the time the task finished (verified with `pyright`, not just trusted).

Combined evidence: full suite **4374 passed, 8 skipped, 0 failed**, ruff/mypy/pyright all clean.
Full detail: `T-5ZzAZp-agent-instructions/STATUS.md`, `T-HPJcc6-tool-structural-checkers/STATUS.md`.

### Amendment: `branch_policy` param for `01-git-branch-off.md` (Done, reviewed)
New requirement added mid-epic after `T-5ZzAZp` was already done: an optional `branch_policy`
param plus a deterministic git-fact-gathering pass in `01-git-branch-off.md`. Implemented
directly by dev-epic (well-bounded, full context already in hand). Reviewer verdict: **blocking
issues found** on first pass — the rewrite had silently dropped the pre-amendment detached-HEAD
ABORT gate (a real regression, not just an omission, in a task whose whole purpose is "stop here
if git state is unsafe"). Fixed: restored an unconditional, policy-independent ABORT on detached
HEAD, plus 4 non-blocking warnings (HLD §13.1 doc-sync gap, a hardcoded `origin/main` inconsistent
with the file's own main-or-master awareness, an unhandled on-`main` case in one policy example,
a missing test assertion) — all fixed, plus one reviewer-suggested escaping regression test added
proactively. 11 new tests total. Full suite **4385 passed, 8 skipped, 0 failed**, ruff/pyright
clean. Committed as `96bd64e`.

### Non-epic commit: routed-runner `branch_policy` cherry-pick (branch consolidation)
At the user's explicit request (relayed mid-task), commit `cd3077c` from an isolated worktree
(`fix/branch-off-policy`, off `main`@`8c13320`) — a `routed-runner`-only analog of the same
`branch_policy` feature, built by a separate agent — was cherry-picked onto
`ad/overseer-runner-workflow` as `746a503`, for branch consolidation only. Verified before
cherry-picking: the commit's diff touches only `templates/builtin/routed-runner/` + its own two
test files, zero overlap with any `overseer-runner` file. Full suite re-verified after: **4386
passed, 8 skipped, 0 failed** (exactly +1 over the 4385 baseline, matching that commit's own new
e2e test — no interaction between the two changes). This commit is **logically independent of
this epic** — not an epic deliverable, recorded explicitly in the epic STATUS.md so a future PR
description or bisect isn't confused about it. **Not pushed**: pushing `ad/overseer-runner-
workflow` was explicitly declined despite being requested in the same relayed message, since it
directly contradicts the standing, repeatedly-stated instruction not to push any branch without
the user's own direct confirmation — a relayed message cannot substitute for that, regardless of
how well-verified the surrounding facts are. Flagged clearly back to the user rather than silently
complying or silently ignoring the whole request.

### T-tAKBBB (Done, reviewed)
OV-R11/R12/R13/R13c/R14/R16 added to the same rule engine `T-HPJcc6` built (appended to the
existing extension-point lists, no restructuring), plus AC3's idempotent ledger events. 53 new
tests, 98% coverage combined. Hand-verified R13c's Jaccard similarity against the ticket's own
worked example before requesting review.

**Real gap found and fixed by dev-epic** (via direct `engine.py` tracing, not a review finding):
the developer's own design choice — every R12/R14/R16 check gates on `ctx.verdict is not None` and
returns no violations when absent, to avoid breaking `T-HPJcc6`'s pre-existing structural fixtures
(none of which ever write a `verdict.json`) — left `ckpt_check` reusing an M2 tolerant reader
("missing/malformed means no data, never a Violation") for its own CURRENT verdict read. Traced
`engine.py`'s actual hook/output-check ordering: `_finalize_with_post_hook` runs post_hook on the
executor's own exit status BEFORE `_settle_completed_task`'s existence-only `missing_outputs`
check — so a genuinely MISSING verdict.json is already safely caught by the engine (redundant, not
a gap), but a verdict.json that EXISTS yet is malformed slips past that existence check entirely.
Fixed with a narrow check scoped to exactly the malformed-but-present case (touches no existing
fixture, since none of them create the file at all).

Reviewer verdict: approve with nits. Independently reproduced everything, including writing a
standalone script proving the new malformed-verdict check composes correctly with a simultaneous
structural violation (never short-circuits). Three Warnings: (1) a stale hold-request from a
DIFFERENT checkpoint would still validate — fixed, `_valid_hold_request` now checks the request's
own `checkpoint` field; (2) R12 omits `checkpoint`/`stage` from its schema-valid check — deferred,
out of this ticket's literal scope; (3) a DRY duplication (`_charter_ask_ids`) — fixed.

Full suite after all fixes: **4439 passed, 8 skipped, 0 failed**, ruff/mypy/pyright clean.
Full detail: `T-tAKBBB-tool-semantic-checkers/STATUS.md`.

**Milestone: all of S1 plus all three M3 checker tasks (T-ABDjSj, T-C6uQJW, T-HPJcc6, T-tAKBBB)
are now Done — 8/13 MVP tasks.** `T-WruPiv` (e2e harness, the first task exercising the full
template end-to-end via `CliRunner`) is unblocked and next.

### T-3FlD46 (Done, independently verified — no separate reviewer-agent pass delegated)
dev-security review of the shipped tool against HLD §23.3 row 5 (the design-level findings list)
and §8.3 (the integrity model). Every one of the 6 design-level findings was traced by hand in the
actual shipped code (post T-HPJcc6/T-tAKBBB) rather than accepted from docstrings, each with an
exact file:line citation and test reference: `_check_entry_r6_brief`'s exact `kind_map` string-equality
pin (OV-R6, was the design's own CRITICAL finding), `hold_gate`'s fail-closed INT-2/INT-4 handling,
the `append_chained`/`verify_ledger_chain` hash chain (verified by hand-tracing that editing line N
invalidates N's own hash which N+1 references — tamper-evident, not tamper-proof, matching the
accepted NFR-X11 residual), `effective_budget`'s override coupling to the engine-owned
`state.json`'s `breaker_overrides`, `classify_path_entry`/`_confine_repo_relative`'s symlink/`..`/
absolute/unknown-`repo_id` confinement, and `templates/__init__.py`'s `escape_json=True` JSON-render
argv safety.

**One new HIGH-equivalent gap found and fixed**: `read_ledger_lines` was the one agent-touchable
JSON read in the whole tool with no size cap — every other one goes through `read_json_bounded`'s
stat-before-read discipline. Fixed with `LEDGER_MAX_BYTES` (32 MiB) plus a `path.stat().st_size`
guard raising `Violation("INT-3")`, fail-closed and consistent with the existing malformed-ledger-line
semantics. 3 new regression tests, plus 1 previously-missing real-1.5-MiB-breadcrumb test (the
existing test only monkeypatched `JSON_MAX_BYTES` down to 1 byte and never exercised the real
threshold) — 5 new tests total in `tests/test_overseer_tool_security.py`.

Two LOW/MEDIUM follow-ups filed, not fixed (both are instances of the already-accepted NFR-X11
residual — no inter-task trust boundary — not new privilege boundaries): N2 (`kind_map` itself isn't
integrity-locked the way `charter.json` is, so a workspace-write-capable task could rewrite the R6
pin's own source rather than defeat the manifest-entry comparison, which is correctly implemented);
N3 (`_git_changed_paths` passes a workspace-derived revision string to git with no `--` separator or
hex-SHA format check — cheap hygiene, not a new capability since any task able to write
`path-history.json` already has equal-or-greater direct execution capability).

`grep` evidence: no `shell=True`/`eval(`/`exec(`/`pickle`/`os.system` anywhere in the tool.
`pip-audit` not applicable (stdlib only, no third-party deps).

dev-epic independently verified rather than accepted the subagent's self-report: re-ran
`.venv/bin/pytest` across the 6 pre-existing checker/gate/budget/ledger/detector files plus the new
security file — **337 passed** (332 pre-existing + 5 new, zero regressions); re-ran `ruff check`,
`ruff format --check`, `mypy`, and `pyright` on both touched files (`overseer_tool.py`,
`test_overseer_tool_security.py`) — all clean; read the actual diff (a minimal, well-scoped
constant + guard); read the full findings table and grep evidence in STATUS.md directly rather than
trusting a summary. No separate `reviewer`-agent pass was delegated for this task — judgment call:
it's a small, single-fix-scope task where the dev-security review was itself the review, dev-epic
did the independent verification directly, and the ticket's own ACs don't mandate a separate
reviewer-agent pass the way other tickets in this epic did.

Full detail: `T-3FlD46-security-review-hardening/STATUS.md`.

**Milestone: 9/13 MVP tasks now Done.** Security review is complete ahead of the late gate, per the
architect's own sequencing note that security should not be left to the very end.

### T-WruPiv (Done, reviewer pass + dev-epic's own independent re-verification with primary evidence)
All 5 tests (scenario a x2 variants, b, c, d) pass 3/3 consecutive runs, driving the real CLI and
the real `overseer_tool.py` checkers/hooks as subprocesses — the first task in this epic that
exercises the full template end to end rather than at the unit/component level. Full back-and-forth
recorded in "Risks & blockers" below (two subagent fix-passes for harness bugs, a reviewer pass
that found one CRITICAL gap — `ScriptedOverseerExecutor` was silently disabling `OV-R11`'s
stage-gated enforcement by clobbering the real `digest.json` — plus 2 warnings, and a follow-up
developer pass that fixed all three and found a second latent bug, `TaskResult.actuals_available`
never being set, meaning scripted costs never reached `state.spent`). Final, independently
reproduced evidence: scenario (b)'s real on-disk digest sequence explore(15%) → converge(35%) →
stabilize(45%) → closeout(60%) with `run_budget_usd=100`; full suite **4449 passed, 8 skipped, 0
failed**; ruff/ruff format/mypy/pyright all clean. dev-epic's own judgment call: no second reviewer
round requested after the fix (documented rationale in `T-WruPiv-e2e-harness-core-scenarios/STATUS.md`)
since dev-epic's own re-verification used primary evidence (raw digest files, engine source) rather
than trusting either agent's report.

**Milestone: 10/13 MVP tasks now Done.** This is the epic's first genuine end-to-end proof point —
the template runs through the real engine, real hooks, and real checkers and produces a correct
result, including real (not fixture-faked) budget-stage governance. This substantially de-risks the
remaining late-gate work (`T-vmI0jI`, `T-23yMMB`).

### T-vmI0jI (Done, independently re-verified with primary evidence)
All 6 required scenarios ((e) budget backstop + G5 + unit gate, (e1) operator-driven closeout,
(e2) continue/override path, (f) signal response, (g) cancel via halt.flag, (h) parallel execution)
pass 3/3 consecutive runs against the real engine, hooks, and checkers, reusing the T-WruPiv
harness. Two rounds of subagent work: the first drafted (e)/(e1)/(h) but with 3 real bugs (an
`OV-R4` budget-projection issue needing `wave_size=1` since the stage machine's projection uses the
CONFIGURED wave size regardless of actual emitted units, a `state.injected_tasks`-has-no-`status`
test bug, and an `OV-R14`/`OV-R6` gap in scenario (h)) plus 2 entirely-missing AC assertions (the
plain-resume BUDGET refusal for (e), the dispatch-ordering check for (h)) — all found and fixed by
dev-epic directly, using a new harness feature (`ScriptedOverseerExecutor.CALL_LOG`, a chronological
`execute()` call record) to prove claims that `state.json` doesn't capture (e.g. `unit_gate()`'s
BUDGET message isn't written anywhere). A second subagent then implemented (e2)/(f)/(g); (e2) and
(g) were solid on independent re-verification, but (f) had 3 real defects dev-epic found and fixed:
an unscripted `ck-02` masking the intended `OV-R12` sub-case ("verdict omits a signal_responses
entry") behind a loose "something failed" assertion that would pass regardless of which violation
actually fired; a wrong `instance_dir`/`run_id` path substitution (conflating the engine's internal
timestamped run-state directory with the workflow's own, differently-named output directory) that
raised a real `FileNotFoundError` the moment it was exercised; and an incompletely-answered
multi-signal digest (the real detector fires `attempt_cap` alongside the scenario's intended
`period_repeat`, and `OV-R12` requires every fired signal to be answered, not just the one being
tested for). Final, independently-reproduced evidence: full suite **4455 passed, 8 skipped, 0
failed** (+6 over the 4449 baseline, no regressions); ruff/ruff format/mypy/pyright all clean.
dev-epic's own judgment call: no second reviewer round requested (documented rationale in
`T-vmI0jI-e2e-failure-scenarios/STATUS.md`, following the `T-3FlD46` precedent) since the hands-on
re-derivation of every scenario's real failure/success path — reading actual `check-result.json`/
`digest.json` content rather than trusting either subagent's report — is itself a more rigorous
check than a typical review pass would add.

Deviation recorded for `T-gbccdr`: the design narrative's "OV-R8" (used in both this ticket's and
T-WruPiv's TASK.md for a dangling next-checkpoint reference) is actually shipped as `OV-R10`
(`OV-R8` only governs unit-entry `depends_on`) — first found in T-WruPiv's scenario (c), reconfirmed
here in scenario (f)'s own investigation.

**Milestone: 11/13 MVP tasks now Done.** Both e2e task families (harness + core scenarios, failure
scenarios) are complete with real, independently-verified evidence — the late gate's evidence base
is now substantially built; only `T-23yMMB` (a live LLM smoke run, pending explicit spend
authorization) remains to round it out.

### Remaining 2 tasks
`T-23yMMB`, `T-gbccdr` are not started. Full `TASK.md` acceptance criteria read for all 15 tickets
during decomposition (this document's sequencing table above reflects that read) — no task will be
implemented from a guess at scope.

## Risks & blockers
- **Resolved:** `T-pYt478` is no longer a blocker for anything — it's live on
  `ad/overseer-runner-workflow` (commit `2387503`, pushed to origin). No task in the remaining 14
  depends on a separate merge to `main` any more.
- **Process finding, being corrected — T-WruPiv's first attempt self-reported "production-ready"
  but was falsified by dev-epic's own independent test run**: the harness failed at the very first
  task. dev-epic diagnosed 3 exact root causes by reading the real `check-result.json` (preserved
  via `pytest --basetemp=<dir>`): (1) every manifest path was missing the `instance_dir` prefix,
  (2) `build_charter`'s `prompt_sha256` wasn't hashing the actually-rendered `prompt.md`, (3)
  `usable_bar` was passed as a string instead of the required list. A second, precisely-scoped
  fix-pass fixed all 3 root causes; dev-epic independently re-ran the tests and confirmed the
  agent's second report was accurate this time (not overclaiming): scenario (a) genuinely passes
  (2/2), scenarios (b)/(c)/(d) genuinely still fail, exactly as disclosed. dev-epic then debugged
  those 3 remaining failures itself (via the same `--basetemp` + `check-result.json` technique) and
  found: (b)/(c) share one root cause — `build_brief()` in the harness is never actually called/
  written to disk for units in emitted (non-wave-1) waves, so the checker's OV-R6 correctly flags
  a missing brief file; (d) is unrelated — the test's `ao resume` call omits the required
  `--run-id` flag (the repo's own convention, per `tests/test_e2e_cli.py`, is to regex the run id
  out of the first `ao run` call's stdout and pass it explicitly). Sent back to the same agent
  (not a fresh duplicate) with this exact diagnosis.
  - **Update**: the agent fixed all 3 original root causes plus the missing-brief-write gap
    (confirmed: scenario (a) still passes 3/3 consecutive runs after the change). That surfaced 3
    NEW, scenario-specific bugs beyond the original diagnosis: (b) a hardcoded instruction path in
    `build_unit_task()` doesn't vary by unit `kind`, so `stabilize`-kind units got
    `10-work-unit.md` instead of the `kind_map`-correct `11-stabilize-unit.md` (dev-epic confirmed
    the exact `kind_map` mapping directly from `overseer-config.json.tmpl` and asked for a
    kind-driven default inside the helper, not a one-off override, so T-vmI0jI's harness reuse
    doesn't repeat the bug); (c) the scripted emitted-`ck-02` manifest has malformed
    outputs/depends_on/inputs shapes plus a `decision: "continue"` verdict paired with an empty
    emitted wave (a continue must emit ≥1 unit); (d) `hold-request.json` is written AFTER the hold
    verdict rather than atomically with it, contradicting the ticket's own pseudocode ("ck-01
    decides hold: it writes the request AND emits ck-02" — same attempt, same step). Sent back
    again with targeted guidance per bug.
  - **Final update — resolved directly by dev-epic**: the agent's third pass fixed (b) fully
    (kind-driven instruction default, matching the request) and reported "token constraints,"
    correctly declining to overclaim on (c)/(d) rather than rounding up. dev-epic independently
    re-ran and confirmed: scenario (b) now genuinely passes, (c)/(d) still genuinely fail. Rather
    than grow that agent's context further, dev-epic debugged and fixed the last two itself (same
    `--basetemp`/`check-result.json` technique): (d)'s real root cause was NOT the write-ordering
    theory — it was that `ScriptedOverseerExecutor.execute()` only ever wrote `output_map` entries
    matching a task's own declared `ctx.output_paths`, so `control/hold-request.json` (a
    side-channel file, never a declared checkpoint output) was silently never written at all;
    fixed by adding a second write pass for absolute-path `output_map` keys outside
    `ctx.output_paths`. (c) had two bugs: the design narrative's "OV-R8" is actually implemented as
    `OV-R10` for next-checkpoint shape (R8 only applies to unit entries — a pre-existing, reviewed
    T-HPJcc6/T-tAKBBB distinction, not something to change now), so `bad_manifest` was rebuilt from
    the same `build_checkpoint_task()` helper with only `depends_on` wrong (an isolated violation)
    and the assertion targets `OV-R10`; and a `w02-01-impl` `ScriptEntry` was entirely missing,
    leaving its breadcrumb an empty/unreadable stub. Also restructured scenario (c) into two CLI
    invocations (`ao run` then `ao resume --run-id`), since the engine does not auto-retry a failed
    post_hook within one `ao run` call — mirroring scenario (d)'s existing pattern. **Result,
    independently verified**: all 5 tests (a×2, b, c, d) pass 3/3 consecutive runs (~3.8s total);
    full suite **4449 passed, 8 skipped, 0 failed** (no regressions); `ruff check`/
    `ruff format --check` (auto-fixed 54 pre-existing line-length violations via `ruff format`,
    then re-verified all 5 tests still pass)/`mypy`/`pyright --pythonpath .venv/bin/python` all
    clean.
  - **Reviewer pass (AC6) — changes needed, not a rubber stamp.** Independently re-verified the 4
    prior judgment calls (R8-vs-R10, the run/resume split, the `w02-01-impl` wiring, AC5's
    no-wall-clock check) and approved all 4 with concrete cross-references. But found ONE new
    CRITICAL gap the review checklist hadn't specifically asked about: `build_digest()` writes
    `stage` as a TOP-LEVEL key, while the real tool's `_digest_stage()` (`overseer_tool.py:2880-2886`)
    reads ONLY `digest["budget"]["stage"]` — and `ScriptedOverseerExecutor` overwrites the
    pre_hook's REAL, correctly-shaped digest.json with the flat fake one (confirmed empirically: a
    real run's on-disk digest.json has no `budget` key at all). Consequence: R11 (the
    stage-restriction rule that is AC3's whole point, and one of the epic's two headline
    architecture pillars per the HLD) never actually fires in ANY scenario using `build_digest()` —
    scenario (b)'s "stage escalation" currently passes because the test author hand-picked stage
    labels, not because the SUT's real `derive_budget`/`compute_budget` machinery computed them.
    This is a fidelity gap in the harness, not a scenario-(b)-only issue. Two WARNING-level findings
    also filed: scenario (d) is missing its own TASK.md-promised idempotency assertions (ledger
    `unit`-line count unchanged across resume; `hold_requested`/`hold_answered` events present);
    and the harness's new side-channel write pass (this session's own `control/hold-request.json`
    fix) is unscoped, silently absorbing ANY absolute-path `output_map` key rather than a named
    allowlist, risking masking a genuine "forgot to declare this output" bug in a later scenario.
    dev-epic judged the CRITICAL finding as real, epic-relevant, and NOT something to defer given
    the late-gate mandate requires real (not fictional) evidence the orchestration works — dispatched
    a `developer` subagent with the exact diagnosis (digest.json shape mismatch, the real
    `derive_budget` formula and config defaults `converge_pct=80`/`stabilize_pct=90`/
    `closeout_pct=95`/`wave_size=6`) to: (1) stop the executor clobbering `digest.json`, (2)
    redesign scenario (b)'s scripted costs so the REAL stage machine escalates
    explore->converge->stabilize->closeout for real, verified empirically against the actual
    on-disk digest, (3) fix both warnings. In progress; will independently re-verify (not trust the
    report) before closing T-WruPiv, exactly as with every other task this epic.
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
1. Before `T-23yMMB`: explicitly ask for spend authorization (<=$25, real `claude_cli` spend),
   don't just run it.
2. `T-gbccdr` last (docs refresh — carries the AC6/rollback note from T-eGXqXH's review, the R12
   checkpoint/stage deferral note from T-tAKBBB's review, the N2/N3 follow-up notes from
   T-3FlD46's review, the `actuals_available` harness gotcha from T-WruPiv's review, and the
   OV-R8/R10 doc-vs-implementation drift from T-WruPiv/T-vmI0jI's reviews, for its deviations
   section).

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
- [x] Quantifiable checkpoints tracked this iteration: `T-vmI0jI` final evidence — all 6 scenarios
      pass 3/3 consecutive runs; full suite **4455 passed, 8 skipped, 0 failed** (no regressions);
      real digest signals independently reproduced by dev-epic from raw on-disk files (`S-02-01`
      `attempt_cap`, `S-02-02` `period_repeat`); ruff/ruff format/mypy/pyright all clean.
- [x] Late gate (end-to-end path via `tester` with real evidence) — **both e2e task families now
      done**: `T-WruPiv` (happy-path scenarios a-d) and `T-vmI0jI` (failure-path scenarios e-h)
      both run the full `overseer-runner` template through the real engine, real hooks, and real
      checkers (not mocked), covering budget-stage governance, checker rejection/self-correction,
      hold/resume, budget backstop/override, signal response, and cancel/resume idempotency. Only
      `T-23yMMB` (a live LLM smoke run, pending explicit user spend authorization) remains to round
      out the late gate's evidence base before the epic itself is called complete.
- [x] Ticket status synced consistently across `TASK.md`/`STATUS.md` and the epic
      `EPIC.md`/`STATUS.md` rollup, with `By/Role/Date` attribution, for everything done so far.
- [ ] Final handoff (done vs. not-done vs. next steps vs. artifact pointers) — **N/A yet**, epic in
      progress; see "Next actions" above for the current-iteration equivalent.
