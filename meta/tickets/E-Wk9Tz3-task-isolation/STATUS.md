# STATUS

- ID: `E-Wk9Tz3-task-isolation`
- Updated At: 2026-09-07
- State: Draft
- Owner: architect (agent)

## This update
- Architecture package complete and design-only. Written: `docs-md/task-isolation-hld.md`
  (requirements, landscape survey, HLD, LLD for 11 modules with pseudocode/interfaces/edge cases,
  schema deltas, mermaid block/state/sequence diagrams for the happy path and every conflict tier,
  resume/crash/cancel paths, test plan, acceptance matrix, readiness gate, risks, open questions),
  `docs-md/adr/ADR-0013-per-task-git-isolation-and-rebase-integration.md` (7 decisions + alternatives
  + consequences, referencing ADR-0007), `docs-md/ai-epics/E-Wk9Tz3-task-isolation.md`, this epic
  ticket, and 12 task tickets.
- **No implementation, no commits.** `src/`, `meta/ROADMAP.md`, `CLAUDE.md`, `meta/learnings*.md`,
  existing ADRs and `../ao-runner-finplan` are untouched.
- Design was validated against the real code (`engine.py` @ 2204 lines, `models.py`, `artifacts.py`,
  `runstate.py`, `cli.py`, `specs/*.schema.json`, `templates/builtin/routed-runner/`) and against the
  real consumer's specs, agents, breakdown contract, git history and parallel-development guidelines.

- **2026-09-07 — two review gates completed and fully incorporated (Phases 1 and 2).**
  `REVIEW-design-2026-09-07.md` (reviewer: APPROVE WITH CHANGES — 6 Blocking, 11 Major, 7 Minor) and
  `REVIEW-security-design-2026-09-07.md` (dev-security: conditional pass — 2 Blocking, 3 Major,
  3 Minor, 3 Info). **All 8 Blocking and all 14 Major findings are dispositioned**; per-finding
  outcomes with the location of each fix are in HLD §24 "Review dispositions". No ADR-0013 decision
  (D1-D7) was overturned.
  - **Phase 1** (before development started): `T-Gt4Pw8` and `T-Sc7Rm2` amended for S-1, R-6, R-23 and
    every schema/model field the later fixes need, so the schema lands once. Those two tickets are now
    **frozen** — development is under way against them.
  - **Phase 2** (this update): every remaining finding across the HLD, ADR-0013 and the other 10
    tickets; two new tasks split out of `T-En8Hd4` to keep the 3-day cap; ADR-0007 misquote corrected;
    ADR-0013 **D8** added (the multi-run policy `meta/ROADMAP.md` §3.4 and ADR-0014 both defer here).
  - **Plan changed: 12 tasks / 28 days / 2 sprints -> 14 tasks / 33 days / 3 sprints.** New:
    `T-Ac6Vd9-requeue-accounting` (2 d, R-1 + R-21), `T-Wl2Bq7-workspace-run-lock` (1.5 d, R-4 + R-12).
    Re-estimated: `T-Wk3Nv6` 2.5->3, `T-Cx4Jf1` 2->2.5, `T-Tp7Zs2` 1.5->2. Sprint 3 is deliberately
    under-committed (6.5 d) as the remediation budget for the late gate.
  - The single most consequential finding was **R-19**: `_run_with_retries` computes six of the seven
    remappable path categories internally from the shared store, so as originally pseudocoded an
    "isolated" task would still have read and written the shared checkout while ao created worktrees
    nothing used. It is now `T-En8Hd4`'s first acceptance criterion.

- **2026-09-07 — `T-Gt4Pw8-git-porcelain` rollup: In Review.** All 24 ACs implemented
  (`src/agent_orchestrator/isolation/git.py`, `isolation/__init__.py`, `errors.py` additions, a new
  shared `src/agent_orchestrator/xdg.py`); 88 new tests, full suite 2211 passed / 7 skipped / 0 failed,
  `ruff`/`mypy` clean, `isolation/git.py` at 96% coverage (TOTAL 94%, no regression). One authorized
  interface amendment (architect Phase-2 routing): `GitRepo.__init__` gained `hooks_dir: Path | None =
  None`, and `EMPTY_HOOKS_DIR` resolves via the new `xdg.resolve_state_dir` instead of the not-yet-
  landed `isolation/paths.py::state_dir()`. Full detail in `T-Gt4Pw8-git-porcelain/STATUS.md`
  ("Interface confirmation for downstream tasks"). Awaiting review; no commit made per instruction.
  — By: developer-agent · Role: developer · Date: 2026-09-07

- **2026-09-07 — `T-Sc7Rm2-isolation-schema-models` rollup: In Review.** All 21 ACs implemented
  (`models.py`, `specs/workflow.schema.json`, `spec.py`, `runstate.py` — no `engine.py`/`budget.py`/
  `artifacts.py`/`cli.py`/`executors/`/`templates/`/`isolation/` edits, per this task's boundary);
  115 new tests, full suite 2008 -> 2211 passed / 7 skipped (unchanged) / 0 failed (includes
  `T-Gt4Pw8`'s concurrently-landed tests), `ruff`/`format --check` clean, `mypy src` unchanged at 4
  pre-existing `_version.py` errors, coverage `models.py` 99% / `runstate.py` 99% / `spec.py` 94% /
  `budget.py` 100% (TOTAL 94%, baseline). One design correction recorded against the HLD's literal
  V1-V12 table: V1/V2/V3/V7/V8/V10/V11 are gated on "at least one task resolves to
  isolation='worktree'" (the ladder default already includes `"llm"`, so evaluating them
  unconditionally would fatal on `resolver_agent` for every non-isolated workflow — caught by the
  pre-existing suite before the fix). `dispatch_cycle` confirmed to survive `prepare_resume`
  (downstream `T-En8Hd4` dependency). Full detail in
  `T-Sc7Rm2-isolation-schema-models/STATUS.md` and `TASK.md`. Awaiting review; no commit made per
  instruction.
  — By: developer-agent · Role: developer · Date: 2026-09-07

- **2026-09-07 — `T-Ov9Bt5-overlap-scheduling-hotspots` rollup: In Review.** AC-1..AC-14 implemented:
  pure `rank_wave`/`overlap_score`/`glob_intersection` (`scheduling/overlap.py`, new package) and
  `compute_hotspots`/`parse_churn`/`load_hotspots`/`observed_conflicts`/`merge_hotspots`
  (`isolation/hotspots.py`, new); `ao hotspots` CLI command (`cli.py`, isolated to that one addition);
  one new underscore-prefixed `GitRepo._log_name_only` in `T-Gt4Pw8`'s (landed) `isolation/git.py` —
  kept private so `tests/isolation/test_git.py`'s structural public-method sweep (a `T-Wk3Nv6`-owned
  test file per the concurrency boundary) needed no edit and stays green (103/103, unchanged). 661
  new tests (`test_overlap_ranking.py`, `test_hotspots.py`, `test_e2e_cli_hotspots.py`); 100%
  coverage on all three new modules. `ruff`/`format --check` clean repo-wide; `mypy src` unchanged at
  4 pre-existing `_version.py` errors. Targeted suite (+`test_wave_scheduler.py`/`test_cli.py`) 733
  passed / 0 failed. Full suite **3232 passed / 7 skipped / 0 failed**, one clean run (no transient
  failures to re-run) — the delta above this ticket's own 661 tests and the 2250 baseline is
  `T-Wk3Nv6`/`T-Tp7Zs2`'s concurrently in-progress, uncommitted work in the same checkout, confirmed
  via `git status` to touch none of this ticket's files. Per R-5/R-18 (HLD §24), the `engine.py`
  wave-fill call site is explicitly **not** added here — `T-En8Hd4`'s job — and confirmed not yet
  landed; the exact signature and insertion point (`engine.py`'s wave/barrier loop, right after
  `ready = self._ready_ids(...)`, before `for tid in ready:`) are published in this task's own
  `STATUS.md` "Hook points" note for `T-En8Hd4` to consume. Awaiting review; no commit made per
  instruction.
  — By: developer-agent · Role: developer · Date: 2026-09-07

- **2026-09-07 — `T-Ov9Bt5-overlap-scheduling-hotspots` review response: In Review (unchanged).**
  `REVIEW.md` verdict APPROVE WITH CHANGES; all three Major findings fixed: C-1 (`GitRepo.
  log_name_only` made public, one authorized additive entry in `tests/isolation/test_git.py`, 103/103
  still green), C-2 (`.ao/hotspots.json` now written atomically, mirroring `runstate.py`'s
  tmp+`os.replace` idiom; new failure-mid-write test), C-3 (`-z` + NUL-split parsing fixes a silent
  non-ASCII-filename drop, real-git-captured fixture regenerated, two new tests). W-1 deferred with
  reason (its fix needs a second `test_git.py` edit not authorized this round). S-1/S-2 applied
  (structural AST guard for AC-14; lazy CLI import restored). S-3/S-4 no action, per the review's own
  conclusion. Gates re-verified: `ruff`/`mypy` clean/unchanged; targeted suite (incl.
  `tests/isolation/test_git.py`) 768 passed / 0 failed; full suite **3248 passed / 7 skipped / 0
  failed**, one clean run. Full disposition in this task's own `STATUS.md`. Still awaiting re-review;
  no commit made per instruction.
  — By: developer-agent · Role: developer · Date: 2026-09-07

- **2026-09-07 — `T-Tp7Zs2-instructions-and-templates` rollup: In Review.** Base AC-1..AC-9 plus
  the R-22/S-2/S-3/S-5 Phase-2 amendments (AC-10..13) all delivered: new packaged
  `conflict-friendly-coding.md` (7 numbered rules, usable via the existing `general_instructions`
  mechanism); `breakdown-contract.md.tmpl`'s field allowlist widened with `touches`/`isolation` +
  the exact guidance sentence; `07-task-breakdown.md`'s "Minimize collision" bullet rewritten
  (not appended) around `touches`/hotspots; all six per-task instructions' `git push` directives
  replaced with "commit only — the engine integrates your work"; the two review instructions
  reworded off "pushed commits"; `template.yaml` gained `merge-resolver` in `required_agents`;
  `workflow.json.tmpl` gained `defaults.isolation: "none"` and a pinned `git-branch-off`. Two
  deviations from the ticket's literal text, both traced to real code paths and flagged for
  sign-off rather than guessed: (1) AC-6's "commented example integration block" lives in
  `README.md` instead of live JSON in `workflow.json.tmpl` — `spec.py`'s V5 rule warns on ANY
  non-default `integration` block while no task is isolated, which every default render of this
  template is by design, so embedding one live would put a NEW warning on the default render; (2)
  `.ao/hotspots.json` is a prose-only declared input in `07-task-breakdown.md`, never a
  `workflow.json` `inputs:` entry, since the engine gates a task's dispatch on declared inputs
  existing and most workspaces will not have run `ao hotspots` yet. `T-Ov9Bt5`'s
  `.ao/hotspots.json` shape/`merge-resolver` `disallowed_tools`/V10 contract is consumed exactly
  as published (own tests never invented a different shape). 12 net new tests
  (`tests/test_conflict_instructions.py` + extensions to
  `tests/test_builtin_routed_runner_assets.py`); targeted suite 98 passed / 0 failed; full suite
  **3244 passed / 7 skipped / 0 failed**, run twice, stable — confirmed via `git status` that this
  ticket's edits and `T-Wk3Nv6`'s/`T-Ov9Bt5`'s concurrently in-progress uncommitted files touch
  disjoint file sets. `ruff`/`format --check` clean repo-wide; `mypy src` unchanged at 4
  pre-existing `_version.py` errors. Awaiting review; no commit made per instruction.
  — By: developer-agent · Role: developer · Date: 2026-09-07

- **2026-09-07 — `T-Wk3Nv6-worktree-lifecycle` rollup: In Review.** All 20 ACs implemented:
  `isolation/paths.py` (new, pure, import-safe without git — `sanitize_ref_component`,
  `task_branch`, `integration_branch`, `squash_ref`, `workspace_key`, `state_dir`, `worktree_root`,
  `worktree_root_prefix_for`, `effective_path`, `RESERVED_SHARED_PREFIXES`,
  `RESERVED_BRANCH_COMPONENTS`); `isolation/worktrees.py` (new — `group_repos`/`IsolatedRepo`/
  `RepoMember`, `TaskIsolation`/`RepoIsolation`, `WorktreeManager.ensure/release/reconcile/gc_run`);
  `isolation/view.py` (new — `IsolatedArtifactView`, S-4: built from exactly one `TaskIsolation`,
  never a manager's registry). `service/paths.py`: only `default_state_dir` migrated onto the
  already-landed `xdg.resolve_state_dir` (R-11) — `default_registry_path` deliberately left alone
  (genuinely different shape: `$XDG_CONFIG_HOME`/file vs `$XDG_STATE_HOME`/dir), the unedited
  `tests/service/test_paths.py` suite is the behaviour-preservation gate and passes unedited.
  `artifacts.py`: added `LocalFsArtifactStore.resolve_unchecked`/`.root` (read-only) plus two shared
  private stat helpers so `IsolatedArtifactView` doesn't duplicate the resolve-then-stat pattern; no
  `extra_roots` param added (per the ticket's explicit instruction); `resolve()`'s existing guard
  unchanged, `tests/test_artifacts.py` passes unedited. 298 new tests across 4 new files
  (`tests/isolation/test_paths.py` 250 incl. a 220-string corpus pinned against real
  `git check-ref-format`, `test_worktrees.py` 28 over real temp git repos, `test_view.py` 16 incl. the
  S-4 task-A/task-B and symlink-escape/cross-run tests, `test_service_paths_migration.py` 4). Targeted
  suite (`tests/isolation tests/service tests/test_artifacts.py tests/test_xdg.py`) 632 passed /
  0 failed. Full suite run twice per the brief's transient-concurrent-edit guidance: first run 4
  failed, all in `tests/test_builtin_routed_runner_assets.py` (a different, concurrently in-flight
  task's file — `T-Wk3Nv6` never touches it); second run **3244 passed / 7 skipped / 0 failed**,
  stable. `ruff check .`/`ruff format --check .` clean repo-wide; `mypy src` unchanged at 4
  pre-existing `_version.py` errors. Coverage: `isolation/paths.py` 100%, `isolation/view.py` 100%,
  `isolation/worktrees.py` 97%, `service/paths.py` 100%; repo TOTAL 95% (baseline 94%, no
  regression). Five deviations logged (none blocking) in `T-Wk3Nv6-worktree-lifecycle/TASK.md`'s
  developer-agent comment, most notably `TaskIsolation` gaining a `workspace_root: str` field beyond
  AC-19's minimum so `effective_path`'s reserved-shared-prefix rule is computable from its locked
  2-arg signature. Awaiting review; no commit made per instruction.
  — By: developer-agent · Role: developer · Date: 2026-09-07

- **2026-09-07 — `T-Ib5Qy9-integrator-core` rollup: In Review.** The epic's hardest ticket: all 18
  ACs (incl. the R-20/R-7/R-8/S-3 amendments) implemented in two new modules,
  `isolation/locks.py` (`IntegrationLock`: process-wide `threading.Lock` + cross-process `flock`,
  bounded `.acquire(timeout) -> bool`, never raises/hangs) and `isolation/integrator.py`
  (`Integrator.integrate`/`.resume_integration`: S-3 denylist screen → auto-commit → R-8 Empty check →
  per-repo lock (sorted key order) → deterministic squash → rebase → injected `resolver_hook` (T1) →
  injected `escalation_hook` (T2/T3/T4) → verify once (default: `git grep -l`/`git diff --check`,
  paths only, NFR-1) → CAS land with R-7-scoped single retry + `is_ancestor` already-landed
  short-circuit + `integration.partial` on a true partial land). Two documented, non-blocking
  signature additions beyond the HLD's schematic pseudocode (`agent_id` and
  `task_integration: TaskIntegrationState` as explicit `integrate()` arguments — full rationale in
  `T-Ib5Qy9`'s own `STATUS.md`); no interface-change request filed against `T-Wk3Nv6` (its published
  `TaskIsolation`/`WorktreeManager`/`group_repos` surface was consumed as-is). 53 new tests
  (`tests/isolation/test_locks.py` 12 incl. a real second `multiprocessing` process for the
  cross-process lock cases; `tests/isolation/test_integrator.py` 41 over real temp git repos incl.
  every non-clean `make_conflict_repo` kind, CAS win/loss-retry/exceed-bound-partial-landing/
  already-landed, verify pass/fail via both the grep and diff-check branches, S-3 denylist
  fail/warn/allow, R-20 no-`RunState` proof, and `resume_integration`'s own lock-timeout/verify-
  failure/CAS-race/multi-repo paths). `tests/isolation` 467 passed / 0 failed; full suite run twice
  per the brief: first run (no coverage) **3323 passed / 7 skipped / 0 failed**; second run (with
  `--cov`) had one transient, unrelated failure (`test_wave_scheduler.py`, a timing-sensitive test in
  a file this ticket never touches) that reproduced 0/2 times run in isolation. `ruff`/`ruff format
  --check` clean repo-wide; `mypy src` unchanged at 4 pre-existing `_version.py` errors. Coverage:
  `isolation/integrator.py` 95%, `isolation/locks.py` 100%; repo TOTAL 95% (baseline 95%, no
  regression). Full detail, published hook-point signatures for `T-En8Hd4`/`T-Rm2Lx7`/`T-Lr6Ka3`, and
  the design-decision rationale are in `T-Ib5Qy9-integrator-core/STATUS.md`. Awaiting review; no
  commit made per instruction.
  — By: developer-agent · Role: developer · Date: 2026-09-07

## Evidence
- `docs-md/task-isolation-hld.md` — 1874 lines.
- `docs-md/adr/ADR-0013-per-task-git-isolation-and-rebase-integration.md` — 269 lines.
- 14 task folders under `meta/tickets/E-Wk9Tz3-task-isolation/`, each with `TASK.md` + `STATUS.md`
  (12 original + `T-Ac6Vd9-requeue-accounting` and `T-Wl2Bq7-workspace-run-lock`, both created by the
  Phase-2 review pass).
- Two review reports in this folder, unedited by the epic:
  `REVIEW-design-2026-09-07.md`, `REVIEW-security-design-2026-09-07.md`.
- Field evidence incorporated: consumer hotspot data (`apis/accounts.rs` — 23 lifetime / 18
  six-month commits), the collision-avoidance `depends_on` counts (14/15 and 15/20), the 105 GB
  `target/` vs ~18-21 GB free disk constraint, the 4106-entry dirty checkout, and the already-leaked
  `worktree-agent-*` branches + a stray `.worktrees/full-test-*` — each of which changed a design
  decision (D4 ref-based landing, D7 build-cache carve-out, FR-14 GC, D5 `should_skip` rule).

## Risks / Blockers
- Not blocked; both review gates confirmed the first two tasks could start immediately, and they have.
- Carried from the gates: R-12 (the first barrier's fast-forward on a ~4106-entry dirty checkout — the
  most likely first-adoption failure; diagnostics added, stash-and-restore declined, `T-Ee3Mn8`
  measures it) and R13 (five tasks now edit `engine.py` — mitigated by a fixed edit order and a
  read-the-merged-file rule on every dependent ticket).
- Not blocked. Five user decisions are recorded with recommended defaults (EPIC.md "Decisions needed
  from the user"); items 1 and 2 should be confirmed before `T-Ib5Qy9-integrator-core` merges.
- Sprint 2 is planned at ~top-of-band capacity; `T-Tp7Zs2` then `T-Ov9Bt5` are the named descope
  candidates.

## Next actions
1. Development is under way on `T-Gt4Pw8` and `T-Sc7Rm2` (frozen at Phase 1). `T-Wk3Nv6` and
   `T-Ib5Qy9` can start as soon as those merge.
2. User confirms (or overrides) the five recorded decisions — especially the integration target and
   the default verify behaviour.
3. The early gate is **done** (both reviews above) — the remaining verification is `T-Ee3Mn8`'s late
   gate, which now verifies the *implementation* of S-1..S-6 rather than re-reviewing the design.

---
- By: architect · Role: architect · Date: 2026-09-06 · Comment: Design package delivered; epic ready
  for implementation planning. Execution-readiness gate in HLD §19 answers PASS on all four
  questions.
