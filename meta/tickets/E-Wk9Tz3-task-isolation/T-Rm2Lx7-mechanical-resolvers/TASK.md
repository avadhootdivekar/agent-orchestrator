# TASK: T-Rm2Lx7-mechanical-resolvers

## Metadata
- Task ID: `T-Rm2Lx7-mechanical-resolvers`
- Epic ID: `E-Wk9Tz3-task-isolation`
- Owner: unassigned (developer)
- Created: 2026-09-06
- Last Updated: 2026-09-06
- Status: Draft
- Estimate: 2.5 days

## Requirements Mapping
- Requirement IDs: FR-7 (tier T1), NFR-1 · Design: HLD §8.4, §11 M6

## Description
Tier 1 of the ladder: repair as many conflicts as possible for **free** — `rerere` replay, union merge
for registry-style files, and regenerate-then-rebuild for lockfiles — with no LLM and no file contents
in orchestrator memory.

Files you own:
- `src/agent_orchestrator/isolation/resolvers.py` (new)
- `tests/isolation/test_resolvers.py` (new)
- `tests/isolation/conftest.py` (extend — additional conflict fixture kinds only; do not change the
  existing builder signatures from `T-Gt4Pw8`)

Do NOT touch: `integrator.py` (you fill its injected `resolver_hook`; wire-up is a one-line change the
integrator owner already provided), `engine.py`, `models.py`, `cli.py`.

## Acceptance Criteria
1. `plan_resolution(conflicted, cfg, already_resolved) -> ResolutionPlan` is **pure** (no git, no
   filesystem) and follows the precedence in HLD §11 M6: already-resolved (rerere) → regenerate
   (first matching rule wins, rules evaluated in declared order) → union → unresolved. Table-driven
   test covering every branch, plus determinism (input order shuffled, output identical because paths
   are sorted).
2. `apply_plan` union resolver operates over **index stages**: `git show :1:/:2:/:3:` into temp files,
   `git merge-file --union -p ours base theirs` into the worktree file, then `git add`. A fixture where
   both sides append a line to `mod.rs` resolves to a file containing **both** lines, in a
   deterministic order, with no conflict markers.
3. Union **declines** when it cannot be meaningful: add/add with no base, add/delete, delete/modify,
   and binary files are all reported as unresolved rather than silently mangled. One test per case.
4. Regenerate: `take: ours|theirs` selects a stage via `git checkout --ours/--theirs`, then runs the
   rule's argv with a timeout, the run env, and cwd = worktree; a non-zero exit marks the path
   unresolved; on success `git add -A` (a regeneration may legitimately touch sibling files). Tests
   with a fake `regen.sh` that (a) succeeds, (b) fails, (c) hangs past the timeout.
5. `rerere` replay: with `-c rerere.enabled=true -c rerere.autoupdate=true` on every git call
   (`T-Gt4Pw8`'s `GitRepo`), a conflict resolved once and then reproduced on a second branch is
   resolved **automatically**, and `plan_resolution` sees it via `already_resolved`. An end-to-end
   integration test proves the replay. A second test asserts `resolvers.rerere: false` disables it and
   that the repo's `git config` is never written.
6. `apply_plan` returns the still-unresolved list by **re-querying** `conflicted_paths` after applying,
   not by trusting its own bookkeeping (a regenerate command can leave the file conflicted).
7. **NFR-1**: a test asserts `resolvers.py` contains no `open(`/`read_text(` on any repository file —
   all content movement is via git subprocesses and temp files. (Enforce with an AST/grep-based test,
   not a comment.)
8. Recommended default union globs (`**/mod.rs`, `**/__init__.py`, `**/index.ts`, `CHANGELOG.md`,
   `**/*.gitignore`) are shipped as **documentation only** — `ResolverConfig.union` stays `[]` by
   default. A test asserts the default is empty (a silent union merge of the wrong file is worse than
   a conflict).
9. `uv run pytest -q tests/isolation/` green; full suite green with recorded counts; `ruff` clean;
   `uv run mypy src` zero new errors.

## Risks
- A wrong union merge is silent. Mitigation: verify (`T-Ib5Qy9`) runs after resolution and catches
  leftover markers; union is opt-in per glob; the instruction pack (`T-Tp7Zs2`) makes registry files
  append-only so union is actually correct for them.
- `rerere` persists across runs via `$GIT_DIR/rr-cache`, so a bad resolution can be replayed forever.
  Mitigation: documented in the HLD (OQ-4) with `resolvers.rerere: false` as the escape hatch; note it
  in your handoff.
- `git merge-file --union` semantics on files with different line endings. Mitigation: fixtures set
  `core.autocrlf=false`; note the limitation.

## Dependencies
- Upstream: `T-Gt4Pw8` (git API + fixtures), `T-Ib5Qy9` (the `resolver_hook` contract).
- Downstream: `T-Lr6Ka3` (escalates when T1 leaves anything unresolved).

## Pseudocode / Algorithm
```text
HLD §11 M6 — plan_resolution and apply_plan, verbatim.
```

## Schemas / Interface Notes
- Interface / API (locked): `ResolutionStep`, `ResolutionPlan`,
  `plan_resolution(conflicted, cfg, already_resolved) -> ResolutionPlan`,
  `apply_plan(plan, worktree, git, env) -> list[str]`.
- Spec / data schema: consumes `ResolverConfig` / `RegenerateRule` from `T-Sc7Rm2`.
- Triggers / events: `integration.resolved` with `tier=mechanical` and `resolver=<rerere|union|regenerate>`.
- Artifacts: none persisted beyond the worktree.

## Handoff Boundary
- Upstream: merged `git.py` + the integrator's hook signature.
- Downstream: `T-Lr6Ka3` receives the unresolved list.

## Artifacts
- Docs/comments: `meta/tickets/E-Wk9Tz3-task-isolation/T-Rm2Lx7-mechanical-resolvers/`
- Large outputs: none
