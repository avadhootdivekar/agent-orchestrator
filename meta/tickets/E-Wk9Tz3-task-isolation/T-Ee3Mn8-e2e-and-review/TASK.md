# TASK: T-Ee3Mn8-e2e-and-review

## Metadata
- Task ID: `T-Ee3Mn8-e2e-and-review`
- Epic ID: `E-Wk9Tz3-task-isolation`
- Owner: unassigned (tester + reviewer + dev-security)
- Created: 2026-09-06
- Last Updated: 2026-09-07
- Status: Draft
- Estimate: 3 days

## Requirements Mapping
- Requirement IDs: all FR/NFR (cross-cutting verification), especially NFR-1, NFR-2, NFR-3, R4
- Design: HLD §17 (test strategy), §17.4 (acceptance matrix)
- Review findings folded in: **S-4** (the missing per-task path-guard test), **R-12** (measure the
  dirty-checkout collision rate), **R-20** (NFR-3 assertion), plus the full finding-driven test matrix
  in HLD §17.5. Estimate unchanged at 3 days — these replace, rather than add to, the generic
  "security pass" bullet already scoped here.

## Description
The epic's verification gate. End-to-end tests through the **outermost boundary** (`CliRunner`, per
CLAUDE.md), the NFR-2 regression gate, a security pass over the artifact path-guard widening, and a
review pass over the whole epic diff. Ships **no production code** except fixes the review demands,
and then only with the owning ticket's agreement.

Files you own:
- `tests/test_e2e_isolation.py` (new — the CLI-level suite)
- `tests/isolation/test_conflict_fixtures.py` (new — fixture-quality tests)
- `tests/test_nfr2_regression_gate.py` (new — the explicit gate)
- This ticket's `STATUS.md` (the evidence log for the epic's late gate)

Do NOT create production code. Any fix goes back to the owning ticket, or is applied here **only**
with an explicit note naming the owning ticket and the reason.

## Acceptance Criteria
1. **NFR-2 gate (blocking).** A documented, reproducible check that the pre-epic engine suite passes
   **unedited** at defaults, with exact before/after pass counts recorded in `STATUS.md`. Any edit to
   a pre-existing engine test is a **failure of this gate**, not a test fix.
2. Deterministic conflict fixtures (extending `tests/isolation/conftest.py`) cover: `clean`,
   `mechanical_union`, `mechanical_lock`, `rerere_repeat`, `true_conflict`, `add_add`,
   `delete_modify`, `binary`, `semantic` (both rebase cleanly but the verify command fails only after
   both land). Each fixture has a test asserting it actually produces the intended git outcome — a
   fixture that silently stops conflicting would make the whole ladder suite vacuous.
3. e2e-1 **happy path**: `ao run --max-parallel 3` on a workflow with `defaults.isolation: worktree`
   over a real temp git repo with `fake` executors that write files. Asserts: three worktrees existed
   concurrently (via a gated executor, not timing); all three landed on the integration branch; the
   final tree contains all three changes; exit 0; no worktree survives; `git worktree list` shows only
   the main checkout.
4. e2e-2 **T1 mechanical**: a scripted collision on a registry file lands via the union resolver;
   `run.log` contains `integration.resolved tier=mechanical resolver=union`; the landed file contains
   both entries.
5. e2e-3 **T2 LLM resolver**: a true conflict plus a fake resolver agent → `integration.resolver_
   dispatched` then `integration.merged tier=llm`; the task's `cumulative_cost_usd` includes the
   resolver attempt and `compute_run_usage_totals` reflects it.
6. e2e-4 **verify failure → T3**: the `semantic` fixture with a `verify_command` → `integration.rerun_
   dispatched`, then success on the fresh base.
7. e2e-5 **T4 → operator → resume**: caps set to 0 → exit code 1, task `failed`, worktree **and**
   branch retained; the test then resolves by hand in the retained worktree and `ao resume` completes
   the run with exit 0.
8. e2e-6 **kill switch**: `--isolation none` on the same spec reproduces the shared-checkout path
   exactly (no `ao/` refs, no worktrees).
9. e2e-7 **precedence matrix** for `--isolation` / `AO_ISOLATION` / `.ao/config.yaml`.
10. e2e-8 **degradation**: a reposet pointing at a plain (non-git) directory runs to completion with
    `integration.degraded`, and the same spec with `isolation.strict: true` fails with a clear message.
11. **Security pass (dev-security)** over exactly these surfaces, with findings recorded in
    `STATUS.md`: (a) the path-guard widening **as built** — it lives in
    `isolation/view.py::IsolatedArtifactView`, not in `LocalFsArtifactStore` (which gained no
    `extra_roots` parameter and whose `resolve()` guard is unchanged). Confirm: the view's roots can
    only be produced by `WorktreeManager` via a single task's `TaskIsolation`; abspath+symlink
    resolution still runs before containment; traversal and symlink escapes still raise;
    `RunStateStore`'s store is the base store and is never a view; and — the structural guard —
    **`resolve_unchecked` has no caller outside `isolation/view.py`** (assert by AST/grep over `src/`,
    so a future edit cannot quietly reuse the unguarded primitive); (b) ref-name
    sanitization against a hostile injected task id; (c) `verify_command` / `regenerate[].command`
    execution (argv-only, timeout, cwd, captured+capped output); (d) `isolation.env` provenance
    (config only, never spec/manifest); (e) auto-commit not sweeping secrets (`.gitignore` respected,
    run state outside the worktree, engine never pushes).
12. **NFR-1 audit**: an automated test asserting no module under `isolation/` opens a repository file
    for reading (AST/grep-based over `open(`, `read_text(`, `read_bytes(`), and that
    `conflict-<n>.json` contains only ids/paths/refs/booleans.
13. **NFR-3 audit**: a test that fails if `RunState` is mutated or `save` is called from any thread
    other than the main thread during a `max_parallel=3` isolated run (wrap the runstate store and
    record `threading.current_thread()` on every call).
14. **Reviewer pass** over the full epic diff against ADR-0013's decisions, CLAUDE.md's code-quality
    rules (no magic literals, no duplicate logic, error handling, testability) and the HLD's acceptance
    matrix (§17.4). Every row of that matrix is marked verified or explicitly deferred with a reason.
15. Coverage on new modules >= 80%, reported per module in `STATUS.md`. Full suite green; `ruff` clean;
    `uv run mypy src` zero new errors versus the recorded baseline.

### Amendments from the 2026-09-07 review gates

16. **HLD §17.5 is a checklist, not a suggestion.** Every row of the finding-driven test table in
    §17.5 is either present and passing, or explicitly listed in `STATUS.md` as deferred **with the
    owning ticket named**. A row silently absent is a failure of this gate.
17. **S-4 — verify the property end to end, at the engine boundary.** `T-Wk3Nv6` already unit-tests
    `IsolatedArtifactView` directly (sibling-task, cross-run, traversal, symlink-escape); **do not
    re-implement those.** What no ticket covers is the same property through a **real dispatch**: in a
    `max_parallel=2` isolated run, a task A whose spec declares an absolute input, an absolute output,
    and an `AgentSpec.working_dir` under **task B's** worktree must fail with a structured
    `ArtifactPathError` at dispatch — and task B's worktree must be provably unmodified afterwards.
    Three explicit cases (input / output / cwd). Also assert the sibling case for a worktree belonging
    to a **different run** in the same workspace.
18. **R-20 / NFR-3 — assert the invariant, don't assume it.** A test that fails if `RunState` is
    mutated or `save()` is called from any thread other than the main thread during a
    `max_parallel=3` isolated run (wrap the runstate store and record
    `threading.current_thread()` on every call), **plus** a static assertion that
    `isolation/integrator.py` neither imports `RunState` nor calls `.save(`.
19. **R-12 — measure, don't assume.** Against a snapshot of the consumer's real dirty-file set (~4106
    `status --porcelain` entries), compute how often those paths would collide with a realistic run's
    integrated changes, and record the observed collision rate in `STATUS.md` as a named
    first-adoption risk. This is a **measurement**, not a pass/fail gate: the design deliberately
    declined to stash the operator's uncommitted work, and this number is what would justify
    revisiting that.
20. **Security pass re-scoped.** The dev-security gate has already run on the design; this pass
    verifies the **implementation** of its findings: S-1 (planted hooks never fire, fixture proven
    non-vacuous), S-2 (force-injected tool policy present on the dispatched context even when the agent
    declares none; `resolver_env` present), S-3 (untracked `.env` aborts naming the path; tracked file
    not screened), S-4 (AC-17), S-6 (regenerate killed at its own timeout), plus the original
    surfaces (ref-name sanitization against a hostile injected task id, argv-only command execution,
    `isolation.env` provenance, auto-commit not sweeping ignored files).

## Risks
- Flaky concurrency tests. Mitigation: use a **gated executor** with latches (the ADR-0007 T-TNleFt
  pattern) rather than sleeps or timing assertions, and fixed clocks/dates everywhere.
- Slow suite (real git + real subprocesses). Mitigation: keep fixtures tiny, reuse one temp origin per
  module, and mark the heaviest e2e cases so they can be selected in CI.
- Reviewing a diff written by several agents invites "fix it while I'm here". Mitigation: findings go
  back to the owning ticket by default; anything fixed here is named and justified.

## Dependencies
- Upstream: every other implementation task.
- Downstream: `T-Dr5Yq6-docs-refresh`.

## Pseudocode / Algorithm
```text
HLD §17.1-§17.4 is the test plan; §17.4 is the row-by-row completion checklist.
```

## Schemas / Interface Notes
- Interface / API: none produced.
- Spec / data schema: none produced.
- Triggers / events: asserts the full event contract from HLD §11 M9.
- Artifacts: test evidence recorded in this ticket's `STATUS.md`.

## Handoff Boundary
- Upstream: all merged implementation.
- Downstream: `T-Dr5Yq6` reconciles the docs against whatever this pass proves the code actually does.

## Artifacts
- Docs/comments: `meta/tickets/E-Wk9Tz3-task-isolation/T-Ee3Mn8-e2e-and-review/`
- Large outputs: none (keep coverage HTML out of the repo; report numbers only)

---
- By: architect · Role: architect · Date: 2026-09-07 · Comment: Phase-2 amendment. Made HLD §17.5's
  finding-driven test table a hard checklist for this gate; replaced the too-weak S-4 acceptance
  criterion with the task-A-cannot-reach-task-B test (three cases); added the NFR-3 thread-identity
  assertion for R-20; and turned R-12 into an explicit measurement with a recorded number rather than a
  pass/fail. The security pass is re-scoped from "review the design" (already done) to "verify the
  implementation of S-1..S-6". Estimate unchanged at 3 days.
