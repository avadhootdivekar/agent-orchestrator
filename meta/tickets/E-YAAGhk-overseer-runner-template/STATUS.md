# STATUS

- ID: `E-YAAGhk-overseer-runner-template`
- Updated At: 2026-09-26
- State: In Progress (design complete; implementation started)
- Owner: architect → dev-epic

## This update
- **Non-epic commit note, for a future PR description or bisect**: commit `746a503`
  ("routed-runner: policy-aware git-branch-off (branch_policy param)", cherry-picked from
  `fix/branch-off-policy`@`cd3077c` at the user's explicit request to consolidate this session's
  branches) is **logically independent of this epic**. It's a `routed-runner` enhancement
  (analogous `branch_policy` support for that template's own `git-branch-off`, done by a separate
  isolated-worktree agent), bundled onto `ad/overseer-runner-workflow` for branch consolidation
  only — it touches only `templates/builtin/routed-runner/` + its own two test files, has zero
  overlap with any `overseer-runner` file, and is not part of this epic's requirements/ACs. Full
  suite re-verified after the cherry-pick: 4386 passed/8 skipped/0 failed (exactly +1 over the
  4385 baseline, matching that commit's own single new e2e test — no interaction/regression
  between the two changes). `ruff check`/`ruff format --check` clean on the cherry-picked files.
  **Not pushed** — per the epic owner's explicit standing instruction not to push any branch
  without the user's own direct confirmation; the user (or their own process) may push
  `ad/overseer-runner-workflow` themselves when ready.
- `T-5ZzAZp` (agent instructions) and `T-HPJcc6` (tool M3a structural checkers) are both **Done**,
  built CONCURRENTLY (verified disjoint files: `instructions/*.md` + its own test file vs.
  `overseer_tool.py`'s new M3 checker section + its own test file — no overlap).
  - `T-5ZzAZp`: all 8 MVP instruction files (`01-git-branch-off.md`, `00-intake.md`,
    `10-work-unit.md`, `11-stabilize-unit.md`, `20-checkpoint.md`, `40-final-verify.md`,
    `41-closeout.md`, `90-final-push.md`) ship under
    `src/agent_orchestrator/templates/builtin/overseer-runner/instructions/`, each opening with the
    `instructions-version: 1` header and the "contract wins" clause, pointing at
    `overseer-contract.md` for shapes rather than restating them. Pause/halt flag names in
    `90-final-push.md` cross-checked against `workflow.json.tmpl`'s real `circuit_breakers` and
    match. 65 new tests. Reviewed (approve with nits): `00-intake.md` mis-cited the contract for
    the charter schema (the contract explicitly disclaims it) — fixed, now cites HLD §13.4, plus
    added the missing `acceptance[].id` shape sentence (`A<n>.<m>`) to both `00-intake.md` and
    `20-checkpoint.md` so `criteria[]` has a stable id to key against.
  - `T-HPJcc6`: `intake-check`/`ckpt-check` (structural half, OV-R1-R10/R15) implemented with a new
    collect-all-violations rule engine (`RuleViolation`/`CheckViolations`), extensible for
    `T-tAKBBB`'s semantic rules via two additive check-fn lists. New rule id `CHR-1` for charter
    validity. 100 new tests, 99% coverage. Reviewed (**approve**, 3 non-blocking nits, one trivial
    comment fix applied).
  - Combined full suite after both + all fixes: **4374 passed, 8 skipped, 0 failed**.
  - Also independently confirmed (via a real `pyright` run, not just `mypy`) that a
    coordinator-flagged mid-development finding in `test_overseer_tool_checker_structural.py` was
    resolved by the time the task finished; separately fixed one real Pyright finding in the
    already-merged `T-ABDjSj`'s `test_overseer_tool_budget.py` (a type-narrowing issue on a test
    helper's `.update()` call).
  - **New requirement (branch_policy amendment, tracked here, not yet implemented)**: an optional
    `branch_policy` param plus a deterministic git-fact-gathering pass
    (`git fetch`+`merge-base --is-ancestor`, dirty-tree check, upstream-tracking check, `main`
    check) in `01-git-branch-off.md`, documented in `README.md`. Confirmed as an amendment to this
    now-Done `T-5ZzAZp` ticket rather than a new one, to be implemented as its own follow-up
    dev→verify→review cycle before `T-tAKBBB` starts. `routed-runner`'s equivalent change is being
    handled separately, off `main`, with no file overlap with this epic's branch.
  See `T-5ZzAZp-agent-instructions/STATUS.md` and `T-HPJcc6-tool-structural-checkers/STATUS.md`
  for full evidence and disclosed judgment calls.
- `T-ltBLUY` (checkpoint contract + README) is **Done, reviewed**: `overseer-contract.md.tmpl`
  (paths, id patterns, the Unit/Next-checkpoint/Tail/Expander shapes, the kind→agent/instruction
  table kept byte-identical to the config's `kind_map`, the four agent-authored artifact schemas,
  the budget stage/decision table, the 10 signal types, the `overseer_model` rule, the
  currently-enforced rule ids (grepped from the shipped tool, not retyped from the design doc)
  plus the forthcoming `OV-R1`..`OV-R16`/`OV-R13c` checker rules, and the self-check section) and
  `README.md` (all 16 required sections) now ship, plus the one `files:` entry T-eGXqXH's own
  review deliberately deferred to this task. 19 net new tests added (28 pre-existing → 47 total
  in the asset suite); full repo suite **4209 passed, 8 skipped, 0 failed**. One disclosed
  judgment call: two of T-eGXqXH's own temporal scope-boundary guard tests (whose own docstring
  named this task as their trigger) were updated to match the new, required reality rather than
  left contradicting a mandatory ticket requirement. Reviewed (approve with nits): the README
  didn't disclose the template isn't runnable end to end yet (M3 checker subcommands don't exist)
  — fixed with a "Current epic status" note in Preflight; a test-count discrepancy in this
  ticket's own STATUS.md ("27 new" vs. the actual 19 net new) — corrected. See
  `T-ltBLUY-contract-and-readme/STATUS.md` for the full rationale and evidence.
- `T-ABDjSj` (tool M1: config/state/ledger/budget/hold/charter-lock/unit-gate/request-closeout) is
  **Done**: `src/agent_orchestrator/templates/builtin/overseer-runner/tools/overseer_tool.py` (new
  file/dir, stdlib only) plus 124 new tests across 3 files, 99% coverage on the tool's own source,
  full repo suite 4107 passed/8 skipped/0 failed (no regressions), ruff/mypy clean. Full evidence
  and judgment-call log in `T-ABDjSj-tool-state-ledger-budget/STATUS.md`.
- `T-eGXqXH` (template scaffold) and `T-C6uQJW` (M2 detectors) are **Done**, built CONCURRENTLY
  (verified disjoint file ownership: scaffold `.tmpl`/`template.yaml` files vs. `overseer_tool.py`'s
  M2 section — no race). Both reviewed (approve with nits): T-eGXqXH's two warnings are tracked
  (the missing `overseer-contract.md.tmpl` files-entry handed to `T-ltBLUY`; the engine-wide "no
  rollback on failed `ao new`" gap recorded as a known limitation, out of scope for this epic).
  T-C6uQJW's one real finding (W1: the path-history trim was prioritizing declared paths over
  undeclared/evasive git-derived ones, inverting the anti-evasion guarantee this feature exists
  for) was fixed by dev-epic and regression-tested; its other finding (W2: signals re-fire
  indefinitely with no expiry) is handed off to `T-tAKBBB` to consider. dev-epic also found and
  fixed a real cross-task issue: T-C6uQJW's new git-subprocess calls tripped a repo-wide security
  guard (`tests/isolation/test_security_guards.py`); added a justified allowlist entry. Combined
  full suite: **4190 passed, 8 skipped, 0 failed**.
- `T-tAKBBB` (tool M3b semantic checkers) is **Done**: OV-R11-R14/R16/R13c added to the same rule
  engine `T-HPJcc6` built, plus AC3's ledger events. dev-epic found and fixed a real gap via direct
  `engine.py` tracing (a malformed-but-present `verdict.json` would have silently passed
  `ckpt_check` with zero violations — a genuinely missing one is already caught by the engine's own
  existence-only check, so the fix is narrowly scoped to the malformed case only, touching no
  existing fixture). Reviewed (approve with nits): 2 of 3 warnings fixed (a stale
  hold-request-from-a-different-checkpoint gap; a DRY duplication), 1 explicitly deferred
  (R12 omitting `checkpoint`/`stage` fields — out of this ticket's literal scope). Full suite
  **4439 passed, 8 skipped, 0 failed**. All three checker tasks (`T-HPJcc6`, `T-tAKBBB`) plus every
  S1 task are now Done — `T-WruPiv` (e2e harness) is unblocked.
- `T-3FlD46` (dev-security review of tool/hooks/checkers) is **Done**: every HLD §23.3 row 5
  design-level finding traced by hand in the shipped code (not docstring-only) — `_check_entry_r6_brief`'s
  exact `kind_map` pin, `hold_gate`'s INT-2/INT-4 fail-closed handling, the `append_chained`/
  `verify_ledger_chain` hash-chain (tamper-evident, not tamper-proof — NFR-X11 accepted residual),
  `effective_budget`'s override-to-`state.json`-`breaker_overrides` coupling, `classify_path_entry`/
  `_confine_repo_relative`'s symlink/`..`/absolute/unknown-`repo_id` confinement, and
  `templates/__init__.py`'s `escape_json=True` JSON-render argv safety — all verified-implemented,
  all with test citations. One new HIGH-equivalent gap found and fixed: `read_ledger_lines` had no
  size cap (every other agent-touchable JSON read in the tool is bounded; the ledger was not),
  fixed with a `LEDGER_MAX_BYTES` (32 MiB) stat-before-read guard raising `Violation("INT-3")`,
  fail-closed and consistent with existing malformed-ledger semantics. 3 new regression tests for
  the fix plus 1 previously-missing real-1.5-MiB-breadcrumb test (the existing test only
  monkeypatched the size constant down, never exercised the real threshold) — 5 new tests total in
  `tests/test_overseer_tool_security.py`. Two LOW/MEDIUM follow-ups filed, not fixed here (both
  instances of the already-accepted NFR-X11 residual, not new privilege boundaries): N2
  (`overseer-config.json`'s `kind_map` isn't integrity-locked the way `charter.json` is — a
  workspace-write-capable task could rewrite the R6 pin source itself), N3 (`_git_changed_paths`
  passes a workspace-derived revision string to git with no `--` separator or hex-SHA format
  check — cheap hygiene, not a new privilege escalation since any task able to write
  `path-history.json` already has equal-or-greater Bash execution capability). `grep` evidence: no
  `shell=True`/`eval(`/`exec(`/`pickle`/`os.system` anywhere in the tool. `pip-audit` not
  applicable (stdlib only). dev-epic independently re-ran and confirmed (not just accepted the
  subagent's self-report): full checker+security suite **337 passed** (332 pre-existing + 5 new,
  zero regressions), `ruff check`/`ruff format --check`/`mypy`/`pyright` all clean on both touched
  files. No separate reviewer-agent pass was delegated for this task (judgment call: small,
  single-fix scope; the dev-security review was itself the review; dev-epic independently verified
  the diff/tests/lint/types directly; the ticket's ACs don't mandate a separate reviewer pass the
  way other tickets did). Full findings table, `grep` evidence, and follow-up tickets in
  `T-3FlD46-security-review-hardening/STATUS.md`.
- `T-WruPiv` (e2e harness + core scenarios a-d) is **Done**: the first task exercising the full
  `overseer-runner` template end to end via `CliRunner` on the real CLI, with the real
  `overseer_tool.py` checkers/hooks running as subprocesses. All 4 scenarios: (a) two waves then
  early verified closeout, (b) budget-stage escalation, (c) a checker rejection that self-corrects
  via `ao resume`, (d) human-in-the-loop hold/resume with idempotency assertions. Went through two
  rounds: harness bugs (instance_dir prefixes, real `prompt_sha256`, kind-driven instruction
  defaults, a side-channel write path for `control/hold-request.json`, an `OV-R8`→`OV-R10`
  rule-id correction, a missing script entry, a real run+resume control-flow fix) resolved across
  two subagent fix-passes plus dev-epic's own direct fixes; then a reviewer pass found ONE
  CRITICAL gap beyond that scope — `ScriptedOverseerExecutor` was clobbering the real,
  tool-computed `digest.json` with a flat test-authored shape lacking the nested `budget.stage`
  the checker actually reads, silently disabling `OV-R11`'s stage-gated enforcement (and, found
  during the fix, `OV-R14`'s early-closeout evidence check) in every scenario — plus 2 warnings
  (scenario (d)'s missing idempotency assertions; an unscoped side-channel write allowlist). A
  follow-up developer pass fixed all three and found a second latent bug in the same area:
  scripted `cost_usd` was never folded into `state.spent` because `TaskResult.actuals_available`
  was never set, so budget staging was a silent no-op throughout the harness's whole life until
  now. Scenario (b) now drives a real, SUT-derived stage sequence, independently reproduced by
  dev-epic from raw on-disk `digest.json` files (not trusted from the report): explore
  (pct_used=15.0) → converge (35.0) → stabilize (45.0) → closeout (60.0), `run_budget_usd=100`.
  dev-epic independently re-verified every claim with primary evidence (real digest files, engine
  source for `actuals_available`'s gating semantics) rather than accepting either subagent's
  self-report: all 5 tests pass 3/3 consecutive runs; full suite **4449 passed, 8 skipped, 0
  failed** (no regressions); ruff/ruff format/mypy/pyright all clean. Judgment call: did not
  request a second reviewer round after the fix — the original review already covered the harness
  mechanism in full (approved 4/4 of its own flagged judgment calls) and its 3 remaining findings
  are now fixed exactly as specified, verified with primary evidence stronger than a typical
  re-review rubber-stamp; documented in `T-WruPiv-e2e-harness-core-scenarios/STATUS.md`.
- Rollup: MVP tasks 10/13 done (`T-pYt478`, `T-ABDjSj`, `T-eGXqXH`, `T-C6uQJW`, `T-ltBLUY`,
  `T-5ZzAZp`, `T-HPJcc6`, `T-tAKBBB`, `T-3FlD46`, `T-WruPiv`) · MVP-Should 0/1 (deferred, see below)
  · design 1/1.
- The architecture package is complete (Rev 2): `docs-md/overseer-runner-hld.md` (sections 1–25),
  ADR-0016 (D1–D9), and 15 task tickets.
- Phase-4 consultations were done with all six roles. The record is in the design doc §23.3. This
  is treated as the epic's **early gate** (reviewer + architect pass on the end-to-end orchestration
  flow) — not re-run by dev-epic, per explicit instruction not to re-litigate the design.
- `T-pYt478` (G5 engine fix) is **Done and landed**: implemented on `fix/emit-settle-atomicity`
  (commit `3692eac`), independently re-verified by dev-epic (not just the implementer's
  self-report), and reviewed (approve with nits, addressed). **User decided to fold it directly
  into this epic branch instead of a standalone PR to `main`**: cherry-picked onto
  `ad/overseer-runner-workflow` as commit `2387503`, full suite re-run clean (3983 passed/8
  skipped/0 failed), and the epic branch pushed to `origin/ad/overseer-runner-workflow`. Full
  evidence in `T-pYt478-emit-settle-atomicity/STATUS.md`.
- dev-epic execution log / decomposition: `docs-md/ai-epics/overseer-runner-template.md`.
- (Rollup superseded by the current one at the top of this update — see above.)

By: developer · Role: developer · Date: 2026-09-26 · Comment: T-ABDjSj (tool M1) done and verified
end-to-end (tests run, coverage measured, full suite re-run, lint/types checked) — see this epic's
`T-ABDjSj-tool-state-ledger-budget/STATUS.md` for the complete evidence log and judgment calls
made where the design doc was silent (rule-id prefix formatting, `prep-result.json` placement,
`request-closeout`'s run-id resolution, path-history scope, the new `BC-2` rule id).

By: architect · Role: architect · Date: 2026-09-26 · Comment: The design stays within existing engine
primitives (recursive `emit_tasks` waves, pre/post hooks, breakers, `state.json`), with **one**
separately scoped engine correctness fix. That fix is G5 (T-pYt478): emissions are lost when a
breaker trips or the process crashes at an emitter's settle. It was reproduced empirically, it is
unavoidable at template level, and it also fixes a latent `routed-runner` exposure. It should merge
to `main` as its own PR first.

The consultations changed the design materially:
- the tool reads `state.json` rather than `status.json`
- `instruction` is pinned per kind
- a $0 per-unit budget gate was added, because breakers latch
- hash-chained ledger events make the hold and override tamper-evident
- FR-16 and FR-17 were promoted to MVP
- the checker and e2e were split
- the team was re-planned to 4 developers × 2 sprints

A final self-review added FR-19 (`request-closeout`) after finding that a plain resume following a
budget trip cannot reach close-out on its own.

## Consultation summary (details in design doc §23.3)
| Role | Top finding | Resolution |
|---|---|---|
| manager | FR-16/FR-17 are needed for MVP; the checker and e2e were under-estimated | Promoted; split into T-HPJcc6/T-tAKBBB and T-WruPiv/T-vmI0jI |
| developer | BLOCKER: `status.json` has no per-task timestamps | The tool reads `state.json` (field-contract test) |
| reviewer | Attempt-cap rename dodge; unjustified `deferred` | OV-R13c; `deferred_reason` + evidence under OV-R12 |
| tester | Missing cancel, parallel, and idempotency e2e; coverage of a path-loaded file | Scenarios (g)/(h) and the (d) ledger assertion; `--cov=<tools path>` |
| dev-security | CRITICAL: unit `instruction` not pinned | OV-R6 exact `kind_map` match |
| dev-critic | Breaker latch means plain resume has no wall after G5 | FR-18 unit gate (+ FR-19) |

## Evidence
- Design: `docs-md/overseer-runner-hld.md`; ADR: `docs-md/adr/ADR-0016-overseer-runner-cadence-and-budget-governance.md`
- G5 repro (reproduced independently by dev-epic on unmodified `main`@`8c13320` before any code
  was written): `output/E-YAAGhk-overseer-runner-template/repro_emit_lost_on_breaker_trip.py`
- `T-pYt478` full evidence (commands + exact output): `T-pYt478-emit-settle-atomicity/STATUS.md`.
  Headline numbers: 6/6 new tests, full suite 3983 passed/8 skipped/0 failed, ruff clean, mypy
  clean net of 4 pre-existing unrelated errors.
- dev-epic execution/decomposition log: `docs-md/ai-epics/overseer-runner-template.md`.

## Risks / Blockers
- No blockers on `T-pYt478` (resolved; see its STATUS.md).
- **Resolved**: `T-pYt478`'s fix is live on `ad/overseer-runner-workflow` (commit `2387503`) and
  pushed to origin. `T-vmI0jI` (e2e failure scenario (e), the one task that genuinely needs the fix
  live) is therefore **no longer blocked** — no PR-merge dependency remains for the rest of this
  epic. Note for whoever opens the eventual epic PR to `main`: call out `2387503` as a distinct,
  independently-justified engine correctness fix (also fixes a latent `routed-runner` exposure) in
  the PR description, separate from the template feature itself.
- Top risks (unchanged from design): an LLM overseer ignoring stages (mitigated mechanically);
  holds rendering as `failed` (NFR-X6); governance being tamper-evident only (NFR-X11). The full
  list is in design doc §23.

## Next actions
1. All of S1 + all 3 checker tasks + `T-3FlD46` + `T-WruPiv` are Done — 10/13 MVP tasks. Next:
   `T-vmI0jI` (e2e failure scenarios (e)-(h), deps: T-WruPiv only, now unblocked — the harness it
   reuses is solid, including the now-real budget/stage machinery), `T-23yMMB` (needs explicit user
   spend authorization, <=$25, before running), `T-gbccdr` (last — carries the AC6/rollback
   engine-gap note from T-eGXqXH's review, the R12 checkpoint/stage deferral note from T-tAKBBB's
   review, the N2/N3 follow-up notes from T-3FlD46's review, and a note that any FUTURE scenario
   reusing `ScriptedOverseerExecutor` must set `actuals_available=True` alongside `cost_usd` or
   budget staging silently no-ops, for the deviations section). `T-zLHc7Q` stays deferred
   (MVP-Should, below cut line per its own ticket status) unless told otherwise.
2. When ready to open a PR for this epic, flag commit `2387503` (G5 fix) separately in the
   description per the note above.
