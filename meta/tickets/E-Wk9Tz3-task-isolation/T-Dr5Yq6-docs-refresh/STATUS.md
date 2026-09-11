# STATUS

- ID: `T-Dr5Yq6-docs-refresh`
- Updated At: 2026-09-11
- State: Done (pass 2 of 2 complete)
- Owner: architect (agent) / manager (pass 2)

## Pass 2 (2026-09-11, executed by the epic manager at close-out)

Both items deferred at the end of pass 1 are now done:

1. **HLD §11 M9's event contract table + status.json/dashboard text.** Reconciled against
   `T-Cx4Jf1` Part B's shipped event emission (independently re-verified, not merely re-read from that
   ticket's own claims). The `NOT YET RECONCILED` banner is replaced with a corrected field contract:
   only `engine.py`/`isolation/integrator.py` lines carry `run_id`/`task_id` (the three module-level
   -logger emitters carry neither); `repo` is genuinely absent from the per-task/per-run lines, not a
   omission; `conflicted` is a count on the engine's lines but a `paths` list on the integrator's own
   `integration.conflict`/`denylisted_path`; the two head shas are `head_from`/`head_to`, never
   `from`/`to` (a reserved word); `duration_ms` exists only on the engine's four per-task terminal
   lines. §24's R-12 and S-5 rows are re-dispositioned from "outstanding"/"open" to **Fixed**, each
   with the concrete evidence (the real collision measurement; the tier_counts/dashboard tests).
2. **`meta/learnings.md` / `meta/learning-compact.md` placeholders.** Replaced with four real,
   individually-marked entries distilled from this epic's actual execution — the self-referential gate
   needing its own exception-list maintenance, the alias-defeatable AST guard, the retry-ladder
   mode-reset defect, and the mega-commit evidence-drift risk. Nothing invented beyond what actually
   happened in this epic.

Also closed during the same pass, beyond pass 2's original two deferred items, because they surfaced
directly from re-verifying pass 1's own "Defects found" list against the merged code a second time:

3. **Defect 1 (T4-resume discards hand-resolution) — fixed**, not merely documented. `engine.py`'s T4
   branch now resets `ti.mode = "normal"`; regression test added; HLD §12.2 and the "Known-defective as
   shipped" table (now "closed by pass 2") both updated. This was pass 1's most important finding and
   pass 2 is what actually closed it.
4. **Defect 2 (stale `workspace_lock` docstring) — fixed** in both `models.py` and
   `specs/workflow.schema.json`, plus the same stale-comment pattern found and fixed on the adjacent
   `RunIntegrationState.workspace_lock_held` field (not originally named).
5. **Epic-level status sync** (handed to the epic owner at the end of pass 1) — performed: see the
   epic `EPIC.md`/`STATUS.md` rollup and every task's `TASK.md`/`STATUS.md`, all now consistently
   `Done`.
6. **The `meta/ROADMAP.md` edits pass 1 already made** were re-checked against the final as-built state
   (no further change needed — isolation is still opt-in/off-by-default and every §3.4/§4 wording pass
   1 wrote still holds).

Gates: `pytest -q` 3831 passed / 8 skipped / 0 failed; `ruff check .` / `ruff format --check .` clean;
`mypy src` 4 pre-existing `_version.py` errors, unchanged. No markdown link added or removed in this
pass; the two touched HLD sections (§11 M9, §24/§25) were spot-checked for dangling anchors.
— By: manager · Role: manager · Date: 2026-09-11 · Comment: Pass 2 complete; epic ready to close.

## This update

- Ticket created by the architect as part of the `E-Wk9Tz3-task-isolation` design package.
- **2026-09-07 — Phase-2 review amendment applied.** Folded in: the duty to re-verify HLD §24's
  dispositions against merged code, the R-13/S-8 documentation items, an execute-it-don't-describe-it
  check on the T4 recovery procedure, and an explicit hand-off of the `meta/ROADMAP.md` + ADR-0014
  cross-epic note to the epic owner. Estimate unchanged at 1 day.
- **2026-09-07 — Reconciliation pass 1 executed.** See below.

- By: architect · Role: architect · Date: 2026-09-07 · Comment: Reconciled `docs-md/` against the
  merged implementation. Every factual claim about a flag, default, env var, ref name, file path or
  symbol was checked against `--help` output or merged source rather than against the ticket that
  claimed it; two operator procedures were executed against real temporary git repositories rather than
  described. Ten as-built deviations recorded in a new HLD §25, each corrected in place at the section
  it affects. Six §24 dispositions changed. Scope was widened mid-task by the coordinator to include
  `meta/ROADMAP.md`, `meta/learnings*.md` and one instruction file
  (`src/agent_orchestrator/templates/instructions/conflict-friendly-coding.md`), and narrowed to
  exclude the epic-level `STATUS.md` and every other ticket's files.

## What was reconciled

**`docs-md/task-isolation-hld.md`** — status header now says *Shipped*, with an explicit warning that
pre-§25 `file.py:NNN` references are historical (the 2026-09-06 design snapshot) rather than current.
Corrections applied in place, each cross-referenced to its §25 entry:

| Section | Correction |
|---|---|
| §7.4 | The post-integration output check is **skipped** for isolated tasks; R-2's stated rationale ("the main-thread check will reach the same verdict") was false for an in-repo output and shipped as a blocking defect. The `should_skip` gate lives at the engine call site, not inside `should_skip`. |
| §6.4, §8.4, §8.5, §11 M7 | T2 **re-materializes** the conflict (`Integrator.materialize_conflict`) rather than inheriting a mid-rebase worktree — which `WorktreeManager.ensure()`'s unconditional rebase-abort made impossible. Sequence diagram and state machine updated, including the "conflict no longer reproduces → skip T2, spend nothing" branch. |
| §10.4 | V1-V12 are **conditionally gated** (applying them literally would have failed every existing workflow); `cross_validate` returns warnings instead of logging them; V8 narrowed; **V13** added (documented as a security-remediation addition, not part of the original design). |
| §11 M9 | `.ao/config.yaml` block corrected to `mode: null`; `ao prune` surface corrected and its two limitations named. Event list and `status.json`/dashboard text **deliberately left unreconciled** and marked as such — see "Deferred". |
| §12.2 | Rewritten. Names the retained worktree/branch, the `conflict-<n>.json` / `previous-<n>.patch` / `merge-resolve.md` / `verify.*` paths and the durable squash ref, then documents the two recoveries that actually work — because the documented one does not. |
| §12.3, §16 | The barrier checkout sync is `GitRepo.fast_forward_checkout` (`read-tree -u -m` + CAS `update-ref HEAD`, with its own ancestry check), not `git merge --ff-only`. |
| §14 | As-built security posture; M-1 (`filter`/`merge` drivers not neutralized) as an accepted documented limitation, with what is and is not covered; the new hard guard against a worktree root overlapping the workspace root; an honest marker on the S-2 row. |
| §16.0 (new) | Operator quick reference — see below. |
| §22 | OQ-1..OQ-5 each resolved with their as-built answer; §20's decisions 1 and 3 carried forward as implemented-by-default rather than ratified. |
| §24 | Re-verification note + six changed dispositions. |
| §25 (new) | Ten as-built deviations, a "known-defective as shipped" table, and the deferred-with-rationale items discovered during implementation. |
| §16 | Fixed a dangling reference to a "D13" that has never existed in §5 or ADR-0013. |

**Operator surface, documented where an operator looks** (new HLD §16.0, plus the `ao init` template
and `conflict-friendly-coding.md`): the two-flag model and its full precedence chain, the
`.ao/config.yaml` `isolation:` block with the config-file-only rationale for `strict`/`env`, `ao prune`
including `--worktrees-only`, `ao hotspots`, the reserved `refs/heads/ao/**` + `refs/ao/**` namespace
and the three ref shapes, where worktrees live, the worktree-root/workspace-root overlap prohibition,
the Git-LFS non-goal with its revisit trigger, and the T4 recovery procedure.

**`ADR-0013`** — status `Proposed` → **`Accepted / shipped (2026-09-07)`**. Decision bodies untouched.
Added an *Implementation notes* section: which decisions held exactly (D1, D2, D3, D4, D6, D8), which
were refined in mechanism (D5, D7, D9 — including that D9's "zero new plumbing" claim needed two bug
fixes to become true), and the one whose supporting mechanism was proved impossible and replaced (T2
re-materialization). Corrected "Six decisions" → eight. Added an explicit note that the Consequences'
security-control (2) describes an intent, not a current behaviour.

**`docs-md/ai-epics/E-Wk9Tz3-task-isolation.md`** — final evidence log (implementation, live-run gate,
as-built security audit, this pass), a **requirement traceability table** mapping every FR-1..FR-16 and
NFR-1..NFR-6 to its landed module and dedicated tests, an "Open at closing time" list, and rewritten
next actions. Test files still uncommitted in `T-Ee3Mn8`'s working tree are marked *(in flight)* so the
table names coverage without claiming results.

**`meta/ROADMAP.md`** — §1 gains a `Per-task git isolation + rebase integration` row (**New, opt-in**)
and the parallel-execution row is corrected; §2 "Recently delivered" gains E-Wk9Tz3; a new §2b describes
what landed and names the first-ship limitations; E-Wk9Tz3 is removed from "Designed, awaiting
implementation" while **E-Sc9Rt4 / ADR-0014 stays exactly where it is**; §3.4 marks isolation delivered
and reframes the remaining work; §4's parallel-write-conflict gap is **rewritten rather than deleted**,
because isolation is opt-in and off by default, so the gap is now avoidable rather than gone.

**`meta/learnings.md` / `meta/learning-compact.md`** — a single clearly-marked **placeholder** in each.
No entries invented; the epic's process learnings are the epic owner's to supply.

**Cross-references** — one line in `hld-agent-orchestrator.md`'s feature-doc list; one note in
`parallel-execution-hld.md` §12 recording that its accepted write-conflict gap shipped as ADR-0013.

## Verification performed

Not prose — executed:

- **`ao prune --worktrees-only`** against a real temp git repo with an orphaned run: `--dry-run` named
  both the worktree path and `refs/heads/ao/run-orphan/t1`; the live run removed the directory, the
  worktree registration and the branch, reporting `1 orphaned run(s) reaped`.
- **The isolation flag chain**: `--isolation auto` → `ERROR: --isolation must be one of ('none',
  'worktree'), got 'auto'`; `--isolation worktree --no-isolation` → `ERROR: ... mutually exclusive`;
  `AO_ISOLATION=worktree` + `--no-isolation` → **not** an error, kill switch wins with
  `WARNING: --no-isolation forced isolation='none' for 1 task(s) that declared otherwise: t1`. That
  asymmetry is now documented, because the one-line summary does not convey it.
- **`isolation.strict: true` on a non-git workspace**: logs `worktree.non_git_repo` then
  `integration.degraded reason="no_git_repos" strict=true` and ends the run `failed` without
  dispatching. With `strict: false` the same workspace degrades and proceeds.
- **`ao init`** writes the `isolation:` block and the reserved-namespace sentence.
- **The T4 → resume path**, driven through `RunStateStore.prepare_resume` for all three
  `TaskIntegrationState.mode` values. This is where the documentation was wrong — see Defects.
- **Schema/model parity**: `specs/workflow.schema.json`'s `integration` properties compared field by
  field against `IntegrationSpec`; every default in HLD §10.1/§10.2 confirmed.

`./.venv/bin/pytest -q` → **3689 passed, 16 failed, 7 skipped**. The 16 failures are **not** from this
change (which touches no executable code beyond one markdown instruction file): they are the in-flight
security remediation landing in `src/` while this pass ran — the new M-3 `ConfigError` guard rejects a
worktree root inside the workspace root, and the existing fixtures still place `$AO_STATE_DIR` there
(`tests/test_engine_isolation.py::TestWorktreeCollision`, the `tests/test_e2e_cli_prune_worktrees.py`
suite). Those fixtures belong to other agents' tickets. **No green-suite claim is made here.**

## Defects found (code, not documentation)

Raised rather than smoothed over. None is fixable by editing docs.

1. **`ao resume` after a T4 failure discards an operator's hand-resolution.** `prepare_resume`
   preserves `task_integration[tid]` verbatim and `mode` is only ever reset to `"normal"` on a
   successful land, so a task that exhausted the ladder resumes with `mode == "rerun"` — and
   `_prepare_rerun_dispatch` `git reset --hard`s every one of that task's worktrees to the current
   integration head before redispatching. HLD §12.2, the §8.4 T4 row and the epic's own acceptance
   criterion 3 all promise the opposite. **Suggested fix:** set `ti.mode = "normal"` alongside
   `ti.status = "failed"` on the T4 branch of `_settle_completed_task`. Verified by execution; §12.2 now
   documents the two recoveries that do work.
2. **Stale text in user-visible code.** `models.py::IntegrationSpec.workspace_lock` and the same
   field's `description` in `specs/workflow.schema.json` still read *"Semantics are fixed by T-En8Hd4;
   the field is reserved here so the schema is not reopened … this ticket implements no behaviour for
   it."* `T-Wl2Bq7` implemented it. The schema description ships to every user who reads the spec.
3. **`ao prune` leaks the `ao/` refs of a fully successful run** (already found by the live-run gate,
   assigned to `T-Cx4Jf1` Part B; restated here because it is the normal path, not an edge case).
4. **Observability is half-shipped** — `tier_counts` never incremented; `tier_reached` /
   `conflicted_count` overwritten by a clean final attempt; `worktree.retention_high` and the dashboard
   column absent (`T-Cx4Jf1` Part B).

Two further defects are already owned by the security remediation and are only *recorded* here, not
re-raised: the S-2 resolver containment being ineffective as shipped, and (now fixed while this pass
ran) `sanitize_ref_component`'s non-injectivity below its length bound, closed by V13.

## Deferred to pass 2 (agreed, not forgotten)

1. **HLD §11 M9's event contract table** and all `status.json` / dashboard observability text. The
   as-built event set was **deliberately not guessed at** while `T-Cx4Jf1` Part B is in flight; the
   block carries a visible `NOT YET RECONCILED` marker telling readers to trust `run.log` instead. Known
   already to differ from the designed list in both directions.
2. **`meta/learnings.md` / `meta/learning-compact.md`** — placeholders only, pending the epic owner's
   process learnings.

Both are blocked on inputs, not on effort. Ready to run pass 2 as soon as the as-built event set lands.

## Evidence

- [`docs-md/task-isolation-hld.md`](../../../../docs-md/task-isolation-hld.md) §25 — the deviation log.
- [`ADR-0013`](../../../../docs-md/adr/ADR-0013-per-task-git-isolation-and-rebase-integration.md) —
  *Implementation notes*.
- [`docs-md/ai-epics/E-Wk9Tz3-task-isolation.md`](../../../../docs-md/ai-epics/E-Wk9Tz3-task-isolation.md)
  — evidence log + traceability table.
- [`REVIEW-security-asbuilt-2026-09-07.md`](../REVIEW-security-asbuilt-2026-09-07.md) — the audit whose
  M-1 / M-3 / verdict items this pass documented.

## Risks / Blockers

- **Not blocked.** Pass 2 waits on the observability slice and on the epic owner's learnings.
- The four defects above are handed to the epic owner; only defect 1 contradicts a documented operator
  procedure, and it is the one worth fixing before anyone follows §12.2 in anger.

## Handed to the epic owner (not done here)

1. **Epic-level status sync.** The epic `STATUS.md` is owned by the coordinator and was not touched.
   For the record, this pass observed: every `EPIC.md` task checkbox is still `[ ]` while 11 tasks read
   Done; the epic State reads `Draft`; `T-Ee3Mn8`'s `TASK.md` says `Draft` while its own `STATUS.md`
   says `In Review`; and the epic rollup's test-count evidence (*27 passed, 2 xfail*) disagrees with
   `T-Ee3Mn8`'s own (*40 passing + 3 xfail*). The epic has **14** tasks, not the 12 this ticket's AC-5
   assumed.
2. **ADR-0014 / `scheduler-triggers-hld.md`'s per-workspace cap note** — asked whether it can be relaxed
   now that ADR-0013 D8 states the multi-run policy. **Recommendation: no.** `workspace_lock: "require"`
   makes a cap violation *degrade safely* (the second run silently loses isolation) but does not make it
   desirable. The cap stays the better default and the lock is the backstop; `meta/ROADMAP.md` §3.4 now
   says exactly that. Neither ADR-0014 nor `scheduler-triggers-hld.md` was edited.

## Next actions

1. Epic owner: apply the epic-level status sync above and route defect 1 (T4 resume) to a developer.
2. On the observability slice landing, send the as-built event set — pass 2 reconciles HLD §11 M9's
   event contract table and the `status.json`/dashboard text, and removes the `NOT YET RECONCILED`
   marker.
3. Send the epic's process learnings to replace the two placeholders.
