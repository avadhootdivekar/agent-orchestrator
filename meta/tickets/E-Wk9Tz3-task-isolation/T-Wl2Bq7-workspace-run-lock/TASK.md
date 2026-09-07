# TASK: T-Wl2Bq7-workspace-run-lock

## Metadata
- Task ID: `T-Wl2Bq7-workspace-run-lock`
- Epic ID: `E-Wk9Tz3-task-isolation`
- Owner: unassigned (developer)
- Created: 2026-09-07
- Last Updated: 2026-09-07
- Status: Draft
- Estimate: 1.5 days

## Requirements Mapping
- Requirement IDs: FR-13, NFR-4 · Design: HLD §12.3, ADR-0013 **D8**
- Created by the 2026-09-07 design gate to carry **R-4** (Blocking) and **R-12** (Major) out of
  `T-En8Hd4`, which would otherwise have exceeded the epic's 3-day cap.

## Description
State and enforce the multi-run policy that `meta/ROADMAP.md` §3.4, ADR-0014 and
`scheduler-triggers-hld.md` all explicitly defer to this epic — *"keep the scheduler's per-workspace cap
at 1 for isolated workflows until the isolation epic states a multi-run policy."* The first version of
this design never stated one.

**What is already safe, and what is not.** The per-repo `IntegrationLock` plus the `update-ref` CAS
already serialize two runs' `integrate()` calls correctly, and each run has its own integration ref —
**landing is safe under concurrency by construction**. The unsafe part is D5's checkout sync:
`git merge --ff-only <integration_branch>` is a **working-tree mutation of one shared physical
checkout**, with no lock, no CAS and no awareness that another run has a different integration branch.
Two runs' barrier-time syncs can interleave partial checkouts, or fast-forward the tree to the *other*
run's head under a task about to read it.

Files you own:
- `src/agent_orchestrator/isolation/runlock.py` (new — `WorkspaceRunLock`)
- `src/agent_orchestrator/engine.py` (edit — **narrow**: the `_activate_integration` lock claim, the
  `_sync_checkout` diagnostics, and the release in `run()`'s `finally`. Read the **merged** file from
  `T-En8Hd4` and `T-Ac6Vd9` first.)
- `tests/isolation/test_runlock.py`, `tests/test_engine_multirun.py` (new)

Do NOT touch: `models.py` (`integration.workspace_lock` and `RunIntegrationState.workspace_lock_held`
ship with `T-Sc7Rm2` and are **frozen**), `isolation/integrator.py`, `isolation/worktrees.py`,
`cli.py`, `spec.py`.

## Acceptance Criteria
1. `WorkspaceRunLock(workspace_root)` with `try_acquire(run_id, policy) -> Claim` and `release()`.
   Backing file `$AO_STATE_DIR/runlocks/<workspace_key>.lock`, `flock`-held for the life of the run,
   payload `{run_id, pid, boot_id, at}`. Reuse `isolation/paths.py::workspace_key` and the shared XDG
   helper from `T-Wk3Nv6` — do not hand-roll a third path resolution.
2. **Stale-lock reclamation.** A lock whose recorded pid is dead, or whose `boot_id` differs from the
   current boot, is reclaimed with a logged `integration.runlock_reclaimed`. Follow the pattern the
   `service/` supervisor singleton lock already uses rather than inventing a second one; read it first
   and say in your handoff whether it was reusable or why it was not.
3. **Policy behaviour, driven by the frozen `integration.workspace_lock` field:**
   - `"require"` (default) — when the lock is held by a **live** run, this run does **not** activate
     isolation: it degrades to `isolation: none` with
     `integration.degraded reason="workspace_locked:<holder_run_id>"` and runs exactly as it does
     today. Under `isolation.strict: true` this becomes a hard run failure instead.
   - `"skip_sync"` — activate isolation and integrate normally (landing is already safe), but **never**
     fast-forward the shared checkout. `ao validate` emits a warning when a workflow sets this while
     it also contains any non-isolated task, because those tasks will run against a checkout that does
     not contain this run's work.
   - `"off"` — no lock, no protection; prints a startup warning naming the risk.
4. The claim happens in `_activate_integration` **before any ref is created**, and the lock is released
   in `run()`'s `finally` (alongside the existing `detach_run_handler`), so an exception or a cancel
   never leaks it.
5. **Test — the actual race.** Two runs in one workspace, driven with a **real second process** (not
   two threads): the second gets `workspace_locked`, never fast-forwards the checkout, and completes
   with `isolation: none`. A separate test kills the holder process and asserts the next run reclaims
   the lock.
6. Test: `"skip_sync"` isolates and lands on its own integration ref while the shared checkout's HEAD
   is provably unchanged; `"off"` acquires nothing and warns.
7. **R-12 — better diagnostics for the dirty-checkout failure.** `_sync_checkout` computes the
   collision set **before** attempting the merge (`diff --name-only <checkout_head>..<integration_head>`
   intersected with the dirty set) and, on failure, emits `integration.sync_failed` **naming the
   colliding paths** plus the exact command an operator would run — not a bare git error. When the
   collision set is **empty** and the merge still fails, report that as a distinct anomaly (the tree
   moved under us), because the two have completely different causes.
8. **R-12 — run-start pre-flight.** When isolation activates, compute the primary checkout's dirty set
   once and log `worktree.checkout_dirty` with the count, so the risk is visible **before** the first
   barrier rather than at it. (The consumer's checkout currently carries ~4106 entries, so this is a
   realistic first-adoption failure, not a corner case.)
9. Test: a checkout dirtied on a path the run will integrate produces a `sync_failed` naming exactly
   that path; a checkout dirtied only on unrelated paths syncs successfully.
10. **Explicitly not implemented:** stash-and-restore of non-overlapping dirty files. It mutates the
    operator's uncommitted work, which is the class of action this design refuses to take unprompted.
    Record the decision in your handoff; `T-Ee3Mn8` measures the real collision rate, and that number
    is what would justify revisiting it.
11. **Cross-epic note.** `E-Sc9Rt4` needs no change: `"require"` is exactly the behaviour ADR-0014
    assumed when it capped per-workspace concurrency at 1. This ticket turns that assumption into
    enforcement so a cap violation, or a manual second `ao run`, degrades cleanly instead of racing.
    Confirm by reading ADR-0014 that nothing there contradicts D8, and say so in your handoff.
12. `uv run pytest -q` fully green with recorded counts; `ruff` clean; `uv run mypy src` zero new
    errors; the NFR-2 gate still passes (a single-run, non-isolated invocation claims no lock and is
    byte-identical).

## Risks
- A run lock that leaks makes every subsequent run in that workspace silently non-isolated — the
  failure is quiet and looks like "isolation just stopped working". Mitigation: pid + boot-id
  reclamation (AC-2), the `finally` release (AC-4), and `integration.degraded` always naming the
  holder run id so the cause is visible in one log line.
- Why not lock only the fast-forward? Because that makes the *sync* atomic but leaves the window
  between it and the non-isolated task that reads the tree — the task would still see another run's
  head appear mid-execution. Owning the workspace for the life of the run is the only granularity at
  which D5's guarantee is actually true. Do not "optimize" this to a narrower lock.
- Degrade-rather-than-fail is deliberate: a second run failing outright would make isolation a foot-gun
  for anyone who runs two workflows in one workspace, which is legal today.

## Dependencies
- Upstream: `T-Sc7Rm2` (frozen fields), `T-Wk3Nv6` (`workspace_key`, the shared XDG helper),
  `T-En8Hd4` (`_activate_integration` and `_sync_checkout` exist and are merged).
- Downstream: `T-Ee3Mn8` (§17.5 rows R-4 and R-12), `T-Dr5Yq6` (documents the policy and reports the
  ADR-0014 cross-epic note).

## Pseudocode / Algorithm
```text
HLD §12.3 in full, and ADR-0013 D8 for the rationale.

_activate_integration(state, workflow):
    claim = WorkspaceRunLock(workspace_root).try_acquire(run_id, policy=spec.workspace_lock)
    IF claim is DENIED:
        IF policy == "require":
            state.integration.degraded_reason = "workspace_locked:" + claim.holder_run_id
            RETURN False                       # -> isolation:none (or fail, under strict)
        IF policy == "skip_sync":
            state.integration.sync_disabled = True
    state.integration.workspace_lock_held = (claim is GRANTED)
    ... existing git probe + ref creation ...

_sync_checkout(state):
    IF state.integration.sync_disabled: LOG integration.sync_skipped; RETURN True
    incoming  = git.diff_names(checkout, checkout_head, integration_head)
    dirty     = {e.path for e in git.status_porcelain(checkout) if e.is_modified_tracked}
    collide   = incoming & dirty
    ok = git.merge_ff_only(checkout, integration_branch)
    IF ok: LOG integration.sync_ok; RETURN True
    LOG integration.sync_failed {colliding: sorted(collide), anomaly: (collide == empty), hint: "..."}
    RETURN False
```

## Schemas / Interface Notes
- Interface / API (locked here): `WorkspaceRunLock(workspace_root)`, `.try_acquire(run_id, policy)
  -> Claim{granted, holder_run_id}`, `.release()`.
- Spec / data schema: consumes the frozen `integration.workspace_lock` and
  `RunIntegrationState.workspace_lock_held` from `T-Sc7Rm2`; adds none.
- Triggers / events: `integration.runlock_acquired`, `integration.runlock_reclaimed`,
  `integration.runlock_denied`, `integration.sync_ok|sync_skipped|sync_failed`,
  `worktree.checkout_dirty`.
- Artifacts: `$AO_STATE_DIR/runlocks/<workspace_key>.lock`.

## Handoff Boundary
- Upstream: merged `engine.py`; `T-Wk3Nv6`'s path helpers.
- Downstream: publish the lock's file location and the exact degrade-reason string, since `T-Cx4Jf1`
  surfaces it and `T-Dr5Yq6` documents it.

## Artifacts
- Docs/comments: `meta/tickets/E-Wk9Tz3-task-isolation/T-Wl2Bq7-workspace-run-lock/`
- Large outputs: none

---
- By: architect · Role: architect · Date: 2026-09-07 · Comment: Created by the Phase-2 review pass to
  carry R-4 (Blocking) and R-12 (Major) out of `T-En8Hd4`. R-4 was the one Blocking finding with no
  design answer at all: two sibling documents defer the multi-run policy here and this design was
  silent. The chosen answer (per-workspace run lock, default-degrade) is the smallest one that makes
  D5's guarantee true, and it needs no change in `E-Sc9Rt4` — `require` is exactly what ADR-0014
  already assumed. Stash-and-restore for R-12 is explicitly declined, not merely unimplemented.
