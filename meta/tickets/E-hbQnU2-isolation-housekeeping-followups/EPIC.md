# EPIC: E-hbQnU2-isolation-housekeeping-followups

## Metadata
- Epic ID: `E-hbQnU2-isolation-housekeeping-followups`
- Title: Task-isolation subsystem — untracked fast-follow cleanups discovered during Epic C's reliability audit
- Owner: dev-epic (spun off during Epic C / C0 audit, not implemented here)
- Created: 2026-09-21
- Last Updated: 2026-09-21 (reconciled — see note below)
- Status: Draft (backlog)

> **Reconciliation note (2026-09-21):** this epic folder was found on disk, untracked by git, at
> the start of a fresh Epic C dev-epic run. Its own text referenced a parent epic/task
> (`E-lBessP-workflow-authoring-skill` / `T-V66ejq-reliability-audit`) that does not exist on
> disk or in git history — evidently a prior Epic C attempt got far enough to run its own C0
> audit and spin off this ticket, but was interrupted before committing its own epic scaffolding.
> Content was verified as real and accurate (citations checked below) and is adopted as-is;
> only the "spun off from" back-reference is corrected to point at the actual epic/task that
> landed this run: `meta/tickets/E-DOiDqE-workflow-authoring-skill/T-trl41B-reliability-audit/`.

## Summary
- Goal: Track four small, already-identified-but-never-ticketed follow-up items in the
  per-task git isolation subsystem (`E-Wk9Tz3`, `docs-md/task-isolation-hld.md`), so they do
  not stay permanently invisible to planning. None of these are regressions in shipped
  behavior — `docs-md/task-isolation-hld.md` §21/§24/"Deferred with rationale" explicitly
  records each as accepted-for-now with a rationale, but three of the four say **"no ticket
  filed"** / **"no ticket named"** in the HLD's own text, and the fourth (`GitRepo.diff_patch()`)
  was only "filed as a follow-up suggestion" inline in prose (`T-Lr6Ka3/STATUS.md`), never as
  an actual ticket. This epic is that ticket.
- Found while: Epic C (`E-DOiDqE-workflow-authoring-skill`) C0 reliability/failure-mode audit,
  surveying `meta/learnings.md`, `meta/ROADMAP.md` §4, and past `REVIEW*.md`/`STATUS.md` notes
  for DAG-authoring failure patterns. These four items are unrelated to that epic's
  skill-authoring deliverable (they are engine/isolation code debt, not spec-authoring
  guidance), so per Epic C's scope rules they are spun off here rather than folded into C's
  work.
- Scope In (MVP, if picked up): the four items below, each independently small and mechanical.
- Scope Out: nothing else in the isolation subsystem — this is a narrow cleanup list, not a
  re-audit of `E-Wk9Tz3`.

## Requirements
- FR-1: `git worktree list --porcelain` is parsed line-based, not NUL-delimited (`-z`)
  (`T-Gt4Pw8` C-9, `docs-md/task-isolation-hld.md` "Deferred with rationale"). Accepted because
  engine-constructed worktree paths never contain a newline today — but nothing enforces that
  invariant, so a future path-construction change could silently reintroduce a parsing bug.
  Fix: switch `parse_worktree_list` (wherever `git worktree list --porcelain` is invoked) to
  `-z` parsing, or add an explicit assertion/test pinning the no-newline invariant.
- FR-2: `_pid_alive` / `_read_os_boot_id` are triplicated across `ui/processes.py`,
  `service/supervisor.py`, and `isolation/runlock.py` (`T-Wl2Bq7` W-2 reviewer recommendation).
  Fix: extract to one leaf module (e.g. `src/agent_orchestrator/procutil.py`) and have all
  three call sites import from it.
- FR-3: A three-way race during the T2 merge-resolver's own dispatch window (a third task
  landing while the resolver works) is out of `T-Lr6Ka3`'s scope and was noted as a follow-up
  for whoever next touches `resume_integration`, but no ticket was ever created to track it.
  Fix (or at minimum: investigate and document): determine whether `resume_integration`'s
  locking already closes this window or whether a real race exists, and either fix it or
  record the accepted risk with evidence in the HLD.
- FR-4: `export_previous_patch` reaches into `GitRepo._run(["diff", ...])` directly because
  there is no public raw-diff method on `GitRepo`; add `GitRepo.diff_patch()` as a proper
  public method and have `export_previous_patch` use it instead of reaching past the
  abstraction.
- NFR-1: None of these fixes may change externally-observed isolation behavior (worktree
  layout, integration semantics, CLI output) — they are internal cleanups/robustness fixes.
  Each should ship with a regression test proving behavior is unchanged (or, for FR-3, with
  evidence resolving whether a race exists at all).

## Task List
- [ ] `T-9oDDDU-isolation-followups-cleanup` — Investigate and fix/document FR-1..FR-4, each as
  an independently landable change with its own test evidence.

## Risks and Dependencies
- All four items touch `src/agent_orchestrator/isolation/` and/or `src/agent_orchestrator/ui/`
  and `src/agent_orchestrator/service/` — shared modules per CLAUDE.md's change-scope policy,
  so keep each fix as narrow as possible and avoid touching isolation's core rebase/CAS-landing
  logic beyond what FR-1..FR-4 require.
- Low priority / low risk: none of these are correctness regressions in shipped behavior today
  (see `docs-md/task-isolation-hld.md` "Deferred with rationale, discovered during
  implementation" for the original acceptance rationale of each).

## Links
- Source of these findings: `docs-md/task-isolation-hld.md`, "Deferred with rationale,
  discovered during implementation" section (search for "no ticket filed" / "no ticket named").
- Related epic: `meta/tickets/E-Wk9Tz3-task-isolation/` (EPIC.md, STATUS.md, and
  `T-Gt4Pw8-git-porcelain/`, `T-Wl2Bq7-workspace-run-lock/`, `T-Lr6Ka3-llm-resolver-and-rerun/`
  for the original context of each item).
- Spun off from: `meta/tickets/E-DOiDqE-workflow-authoring-skill/T-trl41B-reliability-audit/`
  (Epic C, C0).
- Output artifacts (if any): none yet — not implemented in this pass.
