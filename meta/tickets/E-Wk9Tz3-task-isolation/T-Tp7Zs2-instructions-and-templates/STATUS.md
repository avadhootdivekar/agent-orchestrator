# STATUS

- ID: `T-Tp7Zs2-instructions-and-templates`
- Updated At: 2026-09-07
- State: In Review
- Owner: developer-agent

## This update
- **2026-09-07 — REVIEW.md addressed (APPROVE WITH CHANGES).** Per-finding disposition:
  - **C-1 (MUST FIX) — Fixed.** All three parts: (1) the five route-terminal push tasks
    (`bug-push`/`epic-push`/`task-push`/`doc-push`/`testing-push`) pinned
    `"isolation": "none"` in `workflow.json.tmpl`, mirroring `git-branch-off`. (2) The nine
    reviewer-named files made worktree-aware/reworded: `21-bug-fix.md`, `22-bug-test.md`,
    `41-doc-write.md`, `51-test-write.md` gained the "commit only, do not push" wording +
    worktree-tolerant branch check (same pattern as the original six R-22 files);
    `23-bug-review.md` reworded off "pushed commits" + worktree-tolerant branch check;
    `40-doc-plan.md`, `42-doc-review.md`, `50-test-gap.md`, `52-test-run.md` (read-only,
    no push directive) got worktree-tolerant branch checks. `90-final-push.md` (the
    push-terminal file) instead gained a "This task always runs unisolated" note
    explaining why the pin must stay (it makes the real `git push`; must run against
    the shared, synced checkout). (3) README.md's "How to opt in" step 2 rewritten to
    name the isolation-aware task ids precisely instead of recommending a blanket
    `defaults.isolation: "worktree"`, and a new regression test
    (`test_isolation_pin_invariant_matches_real_push_directives`) scans every rendered
    task's instruction file for a REAL `git push` command (distinguishing it from a
    "never `git push`" prohibition) and asserts `isolation: "none"` on every match —
    enforcing the invariant, not just documenting it.
    **Self-discovered addition beyond the review's own list:** `35-task-retest.md`
    (task-route retest stage) had the identical bug (push directive, non-worktree-aware
    branch check) but was named by neither the original R-22 six-file list nor this
    review's C-1 list — fixed in the same pass since the new generic invariant test
    would otherwise have caught it anyway. **Deliberately left out of scope:**
    `20-bug-triage.md` — the review's own C-1 fix-list explicitly excludes it (its
    caveat sentence names exactly 9 files, `bug-triage` is not among them), and it has
    no push directive (read-only investigation, never commits) — isolating it under a
    blanket `defaults.isolation: "worktree"` is a real but self-contained STOP failure
    (no data loss, no stray branch), out of this fix's scope; the narrowed README
    wording calls this out explicitly as a named exception rather than silently
    omitting it.
  - **C-2 (Minor) — Fixed.** README.md's "How to opt in" step 2 now distinguishes
    code-forced (`classify`/`task-breakdown`, via `_is_structural_task`/V4) from
    explicitly-pinned-by-this-template (`git-branch-off`, via its own YAML pin only).
  - **C-3 (Minor) — Fixed.** `template.yaml`'s `required_agents` reordered to the same
    alphabetical sequence README.md already used (cosmetic; both `set`-based tests
    unaffected).
- Ticket created by the architect as part of the `E-Wk9Tz3-task-isolation` design package. Not
  started; no code written.
- **2026-09-07 — Phase-2 review amendment applied.** Folded in: R-22 (six shipped instruction files tell agents to `git push`, contradicting the isolation model — plus two review files referencing "pushed commits"; a repo-wide assertion guards against a missed file), S-2 (the shipped `merge-resolver` agent declares its own `disallowed_tools` so the template never triggers V10), S-3/S-5 documentation duties. **Re-estimated 1.5 -> 2 days.**
- Per-finding dispositions: HLD §24 "Review dispositions".

- **2026-09-07 — implemented, In Review.** All base AC-1..AC-9 plus the 2026-09-07 amendments
  (AC-10..13 / R-22 / S-2 / S-3/S-5) delivered:
  - `templates/instructions/conflict-friendly-coding.md` (new): the 7 numbered HLD §11 M10(a)
    rules + the S-3 auto-commit/secret note, 35 lines (well under the ~80-line risk budget).
  - `breakdown-contract.md.tmpl`: Hard Rule 5 widened to add `touches`/`isolation`; new
    "`touches` & `isolation` per task" section carries the exact guidance sentence the ticket
    specifies.
  - `instructions/07-task-breakdown.md`: `.ao/hotspots.json` declared as an OPTIONAL input (see
    "Deviation" below on why it is prose-only, never a `workflow.json` `inputs:` entry); the
    "Minimize collision" bullet is REWRITTEN (not appended) to name `touches`, reference hotspots,
    and drop the old push-toward-`depends_on` wording.
  - `instructions/08-implement-task.md`: gained the "If you are in an isolated worktree" section
    (AC-5) and a worktree-aware branch check (`ao/<run_id>/<task_id>` is valid, not a STOP
    condition).
  - **R-22 (AC-10):** all six push directives replaced ("commit only — the engine integrates your
    work; do not push") in `08-implement-task.md`, `11-fix-task.md`, `31-task-impl.md`,
    `34-task-fix.md`, `09-write-tests.md`, `32-task-test.md`. Beyond the ticket's literal AC-5
    scope (08 only), the SAME worktree-aware branch-check wording was applied to `11/31/32/34`
    too (`09`/`33-task-review.md`'s read-only check likewise) — these files have the identical
    "STOP if not the epic branch" bug as `08`, and leaving it unfixed while fixing the push
    directive in the same file would ship a known-inconsistent instruction set. Flagged here per
    CLAUDE.md ("say so, don't silently implement a suboptimal design"), not silently expanded.
  - **R-22 (AC-11):** `10-review-task.md`/`33-task-review.md` reworded to "inspect the commits on
    your current branch" (no more "pushed commits").
  - **AC-6:** `workflow.json.tmpl` gained `defaults.isolation: "none"` and `git-branch-off` pinned
    `"isolation": "none"`. **Deviation from AC-6's literal "commented example integration block"
    wording**, traced and documented, not guessed: `templates/__init__.py`'s
    `_validate_rendered_workflow` runs `json.loads` + full `WorkflowSpec`/JSON-Schema validation on
    EVERY render (no templating conditionals exist to gate it out), so a literal `//`-style
    comment breaks parsing on every `ao new`; and `spec.py`'s V5 rule
    (`validate_isolation`) emits a WARNING for ANY non-default `integration` block when no task
    resolves to `isolation: worktree` — which every default render of this template is, by
    design (opt-in stays opt-in). Embedding a live block would therefore either break `ao new`
    outright or put a NEW warning on the default render, violating the "no new warnings" gate.
    The worked example (matching AC-6's `verify_command`/`resolver_agent`/`resolvers.union`
    trio) now lives in `README.md`'s new "Parallel isolation" section instead, and
    `tests/test_conflict_instructions.py` proves the documented recipe validates clean end to
    end once a user applies it (see below). `template.yaml` gained `merge-resolver` in
    `required_agents`.
  - **AC-12 (S-2):** since `template.yaml`'s `required_agents` carries agent NAMES only (no
    `AgentSpec`/`disallowed_tools` — that lives in the workspace's own `agents:` config, outside
    this template), "the shipped merge-resolver agent declares `disallowed_tools`" is proven at
    the test level: `tests/test_conflict_instructions.py` applies the README recipe to an actual
    `ao new` render, then runs `ao validate` twice — once with a `merge-resolver` agent entry
    carrying `disallowed_tools: ["WebFetch", "WebSearch"]` (clean, no V10 warning) and once with
    `[]` (a real V10 warning fires, proving the first result is non-vacuous).
  - **AC-13 (S-3/S-5):** `conflict-friendly-coding.md` states the auto-commit/secret point;
    `README.md` states the `verify_command`-unset/`rerere`-unreviewed point and the NFR-6
    cold-rebuild + shared-build-cache recipe (documented as OS/build-tool-level guidance —
    `IntegrationSpec` has no `env` field yet in this codebase; that lands with `T-En8Hd4`).
  - Risk note (per-run contract) documented in `README.md`'s closing section.

- **Deviation, stated plainly:** `.ao/hotspots.json` is declared as an input in
  `07-task-breakdown.md`'s PROSE only, never added to `workflow.json.tmpl`'s `task-breakdown`
  task's actual `inputs:` array — the engine gates a task's dispatch on its declared inputs
  existing, so a hard `inputs:` entry would make the WHOLE template fail for any workspace that
  has not run `ao hotspots` first, contradicting "optional" and the "no behaviour change" gate.
  This matches the shared T-Ov9Bt5 contract (`.ao/hotspots.json`, `{"version","generated_at",
  "window_days","repos":{...}}`) as an advisory path only.

## Evidence
- Design: [`docs-md/task-isolation-hld.md`](../../../../docs-md/task-isolation-hld.md) (see the
  module section named in `TASK.md`) and
  [`ADR-0013`](../../../../docs-md/adr/ADR-0013-per-task-git-isolation-and-rebase-integration.md).
- Files touched (original pass): `templates/instructions/conflict-friendly-coding.md` (new, 35
  lines), `templates/builtin/routed-runner/{breakdown-contract.md.tmpl, template.yaml,
  workflow.json.tmpl, README.md, instructions/{07,08,09,10,11,31,32,33,34}-*.md}`,
  `tests/test_conflict_instructions.py` (new), `tests/test_builtin_routed_runner_assets.py`
  (extended).
- Files touched (REVIEW.md fix pass): `workflow.json.tmpl` (5 push-task pins),
  `instructions/{21,22,23,35,40,41,42,50,51,52,90}-*.md` (11 files: worktree-aware rewrite,
  worktree-tolerant branch checks, review-wording reword, or the "always unisolated" note),
  `template.yaml` (C-3 reorder), `README.md` (C-1/C-2 rewrite of "How to opt in" step 2),
  `tests/test_builtin_routed_runner_assets.py` (extended further: +6 net tests — the C-1
  invariant test, the 5-push-task pin test, the final-push documentation test, plus fixes to
  two pre-existing whitespace-normalization bugs the new files exposed
  — `test_no_push_directives_in_isolated_per_task_instructions`/
  `test_push_directive_files_say_the_engine_integrates` were checking raw, un-normalized text
  and false-failed on markdown line-wraps inside `35-task-retest.md`).
- Targeted suite (post-REVIEW.md fix): `pytest tests/test_conflict_instructions.py
  tests/test_builtin_routed_runner_assets.py tests/test_e2e_builtin_routed_runner.py
  tests/test_e2e_cli_templates.py -q` -> **76 passed, 0 failed**.
- Full suite: `pytest -q` -> **3265 passed / 7 skipped / 0 failed**, run TWICE (stable, no
  transient failures from the two concurrently in-progress developers' uncommitted files;
  confirmed via `git status` that none of this ticket's edits touch `T-Wk3Nv6`'s or `T-Ov9Bt5`'s
  files, and none of theirs touch this ticket's — a new untracked `tests/isolation/test_locks.py`
  from `T-Wk3Nv6` fails `ruff check`/`format` on its own, reported below, not fixed here).
- Lint/types (this ticket's files): `ruff check`/`ruff format --check` on
  `tests/test_conflict_instructions.py tests/test_builtin_routed_runner_assets.py` -> clean.
  Repo-wide `ruff check .` -> 4 errors, all in `tests/isolation/test_locks.py` (untracked,
  `T-Wk3Nv6`'s in-progress file, not touched by this ticket); repo-wide `ruff format --check .`
  -> 1 file would reformat, same file. `uv run mypy src` -> exactly the 4 pre-existing
  `_version.py` errors, unchanged.

## Risks / Blockers
- See `TASK.md` > Risks. Blocked only by the dependencies listed in `TASK.md` > Dependencies.
- Not blocked. Two deviations from the ticket's literal text are called out above (both traced to
  real code behavior, not guessed) — flagging for architect/reviewer sign-off rather than treating
  as silently resolved:
  1. AC-6's "commented example integration block" lives in `README.md`, not as live JSON in
     `workflow.json.tmpl` (would break `ao new` or trip V5 on every default render).
  2. `.ao/hotspots.json` is a prose-only declared input in `07-task-breakdown.md`, never a
     `workflow.json` `inputs:` entry (would hard-gate `task-breakdown` on a file most workspaces
     will not have yet).
- Consumer note (per TASK.md Risks): `../ao-runner-finplan` maintains its own copy of the
  breakdown contract and was **not** touched — its adoption is a separate ticket in that repo.

## Next actions
1. Architect/reviewer sign-off on the two documented deviations above.
2. `T-Ee3Mn8` (asset assertions) and `T-Dr5Yq6` (docs) are unblocked by this ticket's fields.

---
- By: reviewer-agent · Role: reviewer · Date: 2026-09-07 · Comment: Full review posted —
  `REVIEW.md`. Verdict **APPROVE WITH CHANGES** (one must-fix, C-1). Verified independently (not
  just re-running the developer's own suite): `ruff`/`mypy` clean at the expected baseline; targeted
  suite 73 passed; two fresh `CliRunner` scripts confirm `required_agents` carrying `merge-resolver`
  does NOT break `ao new`/`ao validate` for a workspace whose `agents.json` lacks it (purely
  informational field, never enforced — read `templates/__init__.py` to confirm). Both documented
  deviations **accepted**: (a) the README-only integration-block example is correctly forced by the
  templating engine having no per-file conditionals plus `_validate_rendered_workflow` always running
  full validation — re-derived the reasoning independently and it holds; (b) `.ao/hotspots.json` as a
  prose-only input is correct because `engine.py`'s missing-inputs gate has no generic "optional
  input" concept, confirmed by reading the gate directly.
  **C-1 (Major, must-fix)**: README's "Parallel isolation" opt-in step 2 documents
  `defaults.isolation: "worktree"` as safe for "every non-structural task," but
  `90-final-push.md` (backing all five route-terminal push tasks) and several untouched mid-route
  files (`bug-fix`/`bug-test`/`bug-review`/`doc-*`/`test-*`) are not pinned to `isolation: "none"` in
  `workflow.json.tmpl` and are not worktree-aware — they still say "commit AND push" and their branch
  check rejects an `ao/<run_id>/<task_id>` worktree branch. Following the README's own recipe
  verbatim reintroduces R-22's exact failure mode outside the epic/task-route pipeline this ticket
  fixed. Default render is unaffected (Major, not Blocking). Fix: pin the five push-task ids to
  `isolation: "none"` in `workflow.json.tmpl` (mirrors the existing `git-branch-off` pin) and narrow
  the README's step-2 recommendation; add a regression test. Two minor nits (C-2, C-3) also logged,
  optional polish. Full evidence and file:line citations in `REVIEW.md`.
