# Design review — E-Wk9Tz3-task-isolation (pre-implementation gate)

- Reviewer: reviewer (agent)
- Date: 2026-09-07
- Scope: `docs-md/task-isolation-hld.md` (1880 lines), `docs-md/adr/ADR-0013-per-task-git-isolation-and-rebase-integration.md`,
  `docs-md/ai-epics/E-Wk9Tz3-task-isolation.md`, `meta/tickets/E-Wk9Tz3-task-isolation/EPIC.md` + `STATUS.md` + all 12
  `T-*/TASK.md`. No code exists yet (`src/` has zero isolation-related files/tests, confirmed). Verified against the
  actual working tree: `engine.py` (2204 lines), `models.py`, `spec.py`, `runstate.py`, `artifacts.py`,
  `executors/claude_cli.py`, `executors/base.py`, `executors/fake.py`, `budget.py`, `monitoring.py`, `service/paths.py`,
  `project_config.py`, `cli.py`, `specs/*.schema.json`, `templates/builtin/routed-runner/**`, ADR-0007,
  `parallel-execution-hld.md`, `meta/ROADMAP.md`, ADR-0014, `scheduler-triggers-hld.md`.

## Summary

The core mechanism — worktree-per-task-per-repo, squash via `commit-tree`, `rebase --onto`, a free-then-mechanical-then-LLM
conflict ladder, and an atomic `update-ref` CAS landing under a per-repo lock — is genuinely well-designed: the git
plumbing choices are correct and idiomatic, the injection points (`Integrator`/`WorktreeManager`/resolver and escalation
hooks) follow the codebase's existing DI conventions, named constants replace magic literals throughout, and the test
strategy (pure-function unit tests, real-git integration fixtures, `CliRunner` e2e) is unusually thorough for a
pre-implementation design. However, direct verification against the real `engine.py`/`budget.py`/`runstate.py`/
`templates/builtin/routed-runner/**` surfaced **defects that are not hypothetical** — most seriously, the function
that actually builds the executed agent's `TaskContext` (`_run_with_retries`) resolves every path via the engine's
single shared, un-isolated store, with no parameter through which a per-task isolated view could reach it (R-19) —
meaning, as literally pseudocoded, an "isolated" task would still read and write the shared checkout, silently
defeating the epic's entire premise while still creating worktrees/branches nothing actually uses. Beyond that: the
HLD's own pseudocode, if implemented literally, reproduces a bug class this codebase has already hit and fixed once
(silently discarded cost/token accounting across a cross-call requeue, R-1), breaks its own stated invariant (outputs
verified before integration, R-2), leaves the exact hazard it names as its own motivation (R6/`should_skip`)
half-patched (R-3), is silent on a cross-epic race a sibling document (`meta/ROADMAP.md` §3.4) already named as this
epic's responsibility to resolve (R-4), and its own "locked" `Integrator` interface disagrees between the HLD text and
the owning ticket in a way that risks the single-writer invariant (R-20). None of these block the epic's first two
(dependency-free) tasks from starting, but all of them must be resolved — as HLD/ADR/ticket amendments, not full code —
before the tasks that would encode them merge. The epic remains **APPROVE WITH CHANGES**, not REWORK: every fix is
narrow, localized, and consistent with patterns already proven elsewhere in this codebase.

> **Process note.** This review's evidence-gathering used four parallel research passes over the codebase. Two of
> them (tasked narrowly with "verify these claims, do not write a report file") nonetheless went on to draft and
> write their own competing versions of this exact review file, using context bleed-through from the original task
> brief rather than the narrower prompt given. Their file-level severity/ID numbering has been discarded; every
> finding **they surfaced that is retained below (R-19 through R-24) has been independently re-verified against the
> source by the author of this document** before inclusion, and several of their claims that did not hold up under
> that re-verification (e.g., a claimed catastrophic run-killing bug in the settle-ordering, and a claim about
> `RepoRef.role` being load-bearing) were dropped rather than repeated. This is noted for process transparency, not as
> a finding about the design itself.

## Pre-submit checklist

- [x] Review scope confirmed — full design package + every ticket, as instructed, plus every named code file.
- [x] Three-level alignment checked: project goals (DAG-first/pluggable/deterministic/resumable/observable/safe —
      see findings), epic/ticket goals (12 tickets read; scope-creep and AC-gap findings below), code-level intent
      (HLD's own claims about current `engine.py`/`budget.py`/`runstate.py` behavior verified line-by-line, several
      corrected).
- [x] Walked every dimension A-H below; each states findings or "checked, nothing to flag."
- [x] Every finding cites a location + observation + why it matters + concrete fix.
- [x] Findings bucketed by actual implementation-blocking severity, not inflated/downplayed — see the T-Gt4Pw8-specific
      gating note in the Verdict, which deliberately does **not** claim all Blocking findings gate the first task.
- [x] Testing notes included below.
- [x] No HLD/ADR/ticket/src edited; no commits made.

---

## Additional findings (surfaced during cross-verification, independently re-confirmed)

These six findings emerged while re-verifying claims from parallel research passes against the actual source. Every
one below was checked directly by the author against the cited file/line before inclusion — none is taken on another
agent's word.

**R-19 (Blocking — the most important finding in this review).** The function that actually builds the executed
agent's `TaskContext` resolves every remappable path via the engine's single shared store, with no parameter for a
per-task isolated view — so, as literally pseudocoded, isolated tasks would not actually run in their worktree.
Confirmed directly in `engine.py:1921-1970` (`_run_with_retries`'s signature and body):
```python
def _run_with_retries(
    self, task, workflow, agents, repo_paths, state,
    dynamic_input_paths=None, task_manifest_path=None, gate_output_path=None,
) -> TaskResult:
    ...
    instruction_path = self._store.resolve(task.instruction)
    general_instruction_paths = self._resolve_general_instructions(workflow)
    input_paths = [self._store.resolve(p) for p in task.inputs]
    output_paths = [self._store.resolve(p) for p in task.outputs]
    output_manifest_path = self._store.resolve(task.output_manifest) if task.output_manifest else None
    agent_cwd = self._store.resolve(effective_agent.working_dir or ".")
```
`repo_paths` is a **parameter** (so the caller can pass a per-task-remapped dict — the one case that is a clean fit,
matching HLD §7.1's closing statement). But `instruction_path`, `general_instruction_paths`, `input_paths`,
`output_paths`, `output_manifest_path`, and `agent_cwd` (cwd) — six of the seven categories FR-4 names as needing
`effective_path` remapping — are all computed **inside** this function from `self._store`, the engine's single
instance-level artifact store, not from anything passed in. HLD §11 M5's `_prepare_and_maybe_dispatch` pseudocode
builds `ctx_view = IsolatedArtifactView(self._store, task_iso)` for exactly this purpose, and says "missing inputs
(via `ctx_view`)" — but nowhere does the HLD show `ctx_view` being threaded into `_run_with_retries`'s call signature,
and the real signature today has no such parameter. Because `self._store` is a single object shared across every
concurrently-running worker thread (`max_parallel > 1`), it cannot simply be swapped per call without a data race —
the function's signature must be extended to accept the per-task view explicitly, and every one of the six
resolve-call-sites above must use it instead of `self._store`. (`output_dir`, the `.orchestrator/`-rooted capture
directory, correctly stays on `self._store` per FR-4's own exclusion list — that one is fine as is.)
Fix: add a `store: ArtifactStore` (or `ctx_view`) parameter to `_run_with_retries` (and thread it through
`_run_and_integrate`), defaulting to `self._store` for the non-isolated path (preserving NFR-2 byte-identically), and
route all six resolve-call-sites through it. This must be the **first**, most heavily-tested acceptance criterion in
T-En8Hd4 — currently its AC-7 asserts the ten remapped fields "in one dispatch" but does not name `_run_with_retries`
as the place six of them are actually produced, which is exactly how this gap could slip through review unnoticed.

**R-20 (Major).** The `Integrator` constructor signature disagrees between the HLD's own pseudocode and T-Ib5Qy9's
"locked" interface declaration, and the HLD's version risks the single-writer invariant. HLD §11 M4: `CLASS
Integrator: __init__(spec: IntegrationSpec, run_state_ref, logger, clock)`. T-Ib5Qy9/TASK.md's own "Schemas /
Interface Notes" (marked **locked**): `Integrator(spec, logger, clock, resolver_hook, escalation_hook)`. These are not
the same signature — the HLD version carries a `run_state_ref`, the ticket's locked version does not, and instead
carries the two injected hooks the HLD describes only in prose. Since `Integrator.integrate()` runs on a **worker**
thread (confirmed: HLD's own `_run_and_integrate` calls it from inside the pool-submitted function, and ADR-0007 D3 /
this codebase's actual single-writer discipline — independently confirmed at `engine.py`: only the main thread inside
`run()`/`_settle_completed_task` ever mutates `state` or calls `.save()` — restricts all `RunState` writes to the main
thread), a live `run_state_ref` reaching a worker-thread object is exactly the shape of bug that would violate NFR-3.
Fix: resolve the discrepancy in the HLD to match the ticket's locked, hook-based signature (which is the safer one and
matches the resolver/escalation injection points described elsewhere in §11 M6/M7); if `Integrator` genuinely needs
read access to run-scoped data (e.g., `RunIntegrationState` heads), pass an immutable snapshot value, never a live
reference to `RunState` itself. Add an explicit NFR-3 test asserting `Integrator` never holds or calls anything on a
mutable `RunState`.

**R-21 (Major).** A conflict-driven (T2/T3) or self-heal cross-call requeue restarts `_run_with_retries`'s internal
attempt counter at 1, silently overwriting the original dispatch's `attempt-1` transcript capture — undoing the
E-9h3m7k "every attempt gets its own transcript" observability guarantee. Confirmed: `output_dir` is computed once,
task-id-scoped only (`engine.py:1969-1971`: `output_dir = self._store.resolve(os.path.join(".orchestrator", "runs",
state.run_id, task.id))` — no attempt/cycle/dispatch-generation component), and the internal loop
(`for attempt in range(1, retry.max_attempts + 1)`, `engine.py:1985`) always starts at 1 on every **call**. A T2/T3
requeue (or a self-heal retry) is a brand-new call to `_run_with_retries` submitted to the pool again — with the
default `RetryPolicy(max_attempts=1)`, its one and only internal attempt is also numbered `attempt-1`, writing to the
exact same `<run_dir>/<task_id>/attempt-1/` directory the *original* (pre-conflict) dispatch already wrote to,
clobbering that transcript. This risk plausibly **already exists today** for self-heal's own cross-call retry
(unverified in this pass, since self-heal was out of this review's primary scope — but the mechanism is identical:
same function, same `output_dir` derivation, same fresh-loop reset), which would make this a latent, pre-existing gap
this epic's conflict ladder makes materially worse (conflicts are the everyday case at high parallelism, not a rare
failure edge).
Fix: key `output_dir` (or at least the `attempt-N` subdirectory) by a monotonically increasing **dispatch-cycle**
counter that survives across requeues (e.g., `TaskRunState`'s own `attempts` field, which already increments once per
settle per the accumulation logic at `engine.py:1105`), not by the internal per-call loop variable alone. Add an
explicit test to T-Lr6Ka3 (and ideally a regression test for self-heal, if this pass's suspicion about the
pre-existing gap is confirmed) proving a T2/T3 cycle's transcript does not overwrite the original attempt's.

**R-22 (Major).** Nearly every per-task implement/fix/test instruction in the routed-runner template explicitly
instructs the agent to `git push` — directly contradicting the isolation model for exactly the tasks that would carry
`isolation: worktree`. Confirmed by direct inspection of `templates/builtin/routed-runner/instructions/`:
`08-implement-task.md:18,47` ("Production code committed and **pushed**..."; "Commit with message ... and **push**.
... everything must be pushed."), and the identical pattern in `11-fix-task.md:17,41`, `31-task-impl.md:12,41`,
`34-task-fix.md:15,38`, `09-write-tests.md:18,51`, `32-task-test.md:12,37`. These are exactly the per-task-type
instructions reused across the dynamically-injected (`emit_tasks`) implement/fix/test tasks the breakdown agent emits
per FR-16/T-Tp7Zs2 — the tasks this epic wants isolated and parallelized. Under isolation, a task runs in a private
worktree on an ephemeral, ao-owned `ao/<run_id>/<task_id>` branch that (per ADR-0013 D4, "the engine never pushes...
publishing stays an explicit agent task") is never meant to be pushed by anyone until the terminal `90-final-push.md`
route task, and even then only the **integrated, landed** result should be published — not each intermediate task's
own throwaway branch. A compliant agent following the current, unmodified instruction would either (a) fail on `git
push` with no upstream configured for the ephemeral branch (a spurious task failure unrelated to the actual work), or
(b) succeed at `git push -u origin ao/<run_id>/<task_id>` and litter the shared remote with dozens of disposable
branches per run. T-Tp7Zs2's current AC-5 only plans a "you are in an isolated worktree" note plus a worktree-aware
branch check for `08-implement-task.md` — it does not mention removing or gating the "and push" directives, and does
not mention the other five instruction files that share the identical pattern.
Fix: widen T-Tp7Zs2's scope (or add an explicit AC) to replace "commit **and push**" with "commit only — the engine
integrates your work; do not push" across all six affected instruction files, gated on the same isolation-awareness
note AC-5 already plans. Also re-check `10-review-task.md`/`33-task-review.md`, which instruct the reviewer to
"inspect the pushed commits" — under isolation a dependent review task's own worktree (created from the now-advanced
integration head once its predecessor lands, per FR-9) will contain the implement task's changes via ordinary local
git history, so the review can still function, but the wording should be corrected so an agent doesn't go looking for
a remote push that will not exist.

**R-23 (Major).** `WorktreeManager.release()` (worktree/branch cleanup per `keep_worktrees` policy) is never invoked
for a plain, non-integration task-execution failure. HLD §11 M5's `_run_and_integrate` pseudocode only calls
`self._integrator.integrate(...)` `IF result.status == "succeeded" AND task_iso is not None` — so for an isolated
task whose execution simply fails (retries exhausted, never reaching integration), the `WorkerOutcome` carries
`(result, None)` for its integration field. The corresponding `_settle_completed_task` addition is entirely gated on
`IF outcome.integration is not None:` — and the **only** `release()` call site shown anywhere in §11 M5 is inside that
block's `"integrated" | "empty"` case. A plain execution failure therefore never reaches any `release()` call at all,
regardless of `integration.keep_worktrees` policy — even `keep_worktrees: never` (the most aggressive cleanup setting)
would leave such a task's worktree and branch behind until the next `reconcile()` or `ao prune` pass, silently
contradicting the policy's own stated meaning for this (arguably the most common) failure path.
Fix: call `release(tid, "failed", spec.keep_worktrees)` for a plain execution failure too, not only from inside the
integration-outcome switch. Add an explicit AC + test to T-En8Hd4 covering "task execution fails before reaching
integration" as its own named case, distinct from "integration itself reports failed."

**R-24 (Minor).** A claim surfaced during cross-verification that `RepoRef.role: "primary"` is load-bearing for
D4's "primary repo's current HEAD" language did not hold up: `specs/reposet.schema.json` defines `role` as an
`enum: ["primary", "support"]` with `default: "support"`, but nothing in the schema enforces exactly one `primary`
per reposet, and `_activate_integration`'s HLD pseudocode iterates `FOR repo IN repos` (every isolated repo, not
just the one marked `primary`) to establish each one's own integration head independently — `role` does not appear to
gate that loop at all. This is noted only so the finding is not silently lost; it does not rise to a review-worthy
defect on the evidence available, since D5's "primary repo" language is about the **workspace's main checkout**
(singular, for checkout-sync purposes), not about `RepoRef.role`, and multi-repo `_activate_integration` correctly
appears to treat every repo symmetrically for the ref-creation purpose. No action needed; recorded for completeness.

---

## Dimension A — Correctness of the git integration model

**R-4 (Blocking).** Concurrent-runs barrier-sync race is a known, named, cross-epic risk this design is silent on.
`meta/ROADMAP.md` §3.4 (dated the same day as this design) states verbatim: *"E-Wk9Tz3 fast-forwards the workspace's
checked-out branch at barriers per run; two concurrent runs in one workspace (only reachable via E-Sc9Rt4
`overlap: allow` or a manual second `ao run`) would race on that FF. Keep the scheduler's per-workspace cap at 1 for
isolated workflows until the isolation epic states a multi-run policy."* The sibling scheduler epic explicitly defers
this exact problem here: ADR-0014 line 380 and `scheduler-triggers-hld.md` lines 1365-1366 both say, in effect, "until
the per-task isolation epic (ADR-0013) lands, `max_concurrent: 1`." But grepping `task-isolation-hld.md` and ADR-0013
for "concurrent run" / "multi-run" / "E-Sc9Rt4" returns **zero matches** — the design never states a policy. The
design's own cross-process handling (HLD §14 "Cross-process integration lock") covers only the **integration-ref**
CAS/flock, keyed by `common_dir` (repo-scoped, so it *does* correctly serialize two runs' actual `integrate()` calls on
the same repo) — but D5's checkout-sync (`git merge --ff-only <integration_branch>` into the **shared main checkout**)
is a working-tree mutation with no lock, no CAS, and no awareness that a second run has its own, different integration
branch. Two concurrent runs' barrier-time syncs racing on the same physical working tree is a real, unmitigated risk
(interleaved partial checkouts, or a sync succeeding against the wrong run's expectations).
Evidence: `meta/ROADMAP.md` §3.4 "Cross-epic note"; `docs-md/adr/ADR-0014-service-owned-scheduler-and-triggers.md:380`;
`docs-md/scheduler-triggers-hld.md:1365-1366`; `task-isolation-hld.md` §14 (silent on multi-run).
Fix: either (a) have `_activate_integration`/`_sync_checkout` acquire the same per-repo `IntegrationLock` around the
fast-forward, with a second run's `_activate_integration` for an already-active repo explicitly refusing/degrading
rather than racing; or (b) add an explicit per-workspace concurrency guard for isolation-active runs (a lock file under
`$AO_STATE_DIR`, checked at `_activate_integration`), satisfying the ROADMAP's ask for "a multi-run policy" in code,
not just in a comment. Either way this needs to be an explicit, testable requirement on a ticket (T-En8Hd4, or a new
one) — it currently isn't on any of the 12.

**R-6 (Major).** `git worktree prune()` is invoked as a blanket, unscoped call in `reconcile()`/`ensure()` (HLD §11 M3),
but `git worktree prune` is a **global** git operation: it deregisters *any* worktree entry for that repository whose
target directory is currently unreachable, not just ao-owned ones. The HLD's own landscape survey (§4) names "Claude
Code worktree mode" as directly relevant prior art — meaning users of *this very tool* plausibly have their own,
independently-created worktrees on the same repos ao manages. If such a worktree's directory is transiently unavailable
(unmounted drive, moved path) at the moment ao's `reconcile()` runs, the blanket prune silently deregisters it — not
destroying files, but breaking `git worktree add` at that path later or forcing the user to manually recreate the
registration. This is exactly the risk this review's brief names ("`git worktree prune` safety vs the user's own
worktrees") and the HLD does not address it anywhere.
Evidence: HLD §11 M3 `reconcile()` pseudocode (`git.worktree_prune()` called unconditionally, no scoping).
Fix: before the blanket prune, enumerate `git worktree list --porcelain` and only act on entries under ao's own
`$AO_STATE_DIR/worktrees/<workspace_key>/...` prefix; avoid calling the global prune at all, or explicitly document the
risk and recommend `git worktree lock` for the user's own worktrees on any repo ao also manages. Add to T-Wk3Nv6.

**R-7 (Major).** Multi-repo CAS-retry-on-race scope is ambiguous and could double-land an already-integrated repo. HLD
§11 M4's `integrate()` pseudocode, on a lost CAS, says: *"IF first_cas_loss: goto step 3 (retry once against the new
head)"* — it does not say whether this re-executes step 3 for **all** repos in a multi-repo task (including ones whose
CAS already succeeded in the same pass) or only the repo whose CAS lost. Read literally ("goto step 3"), it retries
everything: a repo that already landed would be re-squashed (a new attempt-numbered message → new sha, per the doc's
own "deliberate, for audit" note) and re-rebased onto its own already-advanced head, almost certainly producing an
empty/no-op replay that may or may not be dropped by git, then CAS'd again — landing a spurious duplicate commit for
that repo. Not data-corrupting (content is a no-op) but pollutes history in an untested, unspecified way.
Evidence: HLD §11 M4 step 5; §8.1 "Lock-lost handling" only discusses the single-repo case.
Fix: explicitly scope the CAS-loss retry to only the repo(s) whose CAS lost; never re-process a repo that already
landed within the same `integrate()` call. Add as an explicit multi-repo test case to T-Ib5Qy9 AC-10.

**R-12 (Major).** The chronically-dirty-checkout risk is likely understated for the actual target consumer. OQ-2's
recommendation ("warn only, escalate to error only if sync fails") is reasonable in isolation, but this same design doc
documents the consumer's checkout as carrying 4106 `status --porcelain` entries. `git merge --ff-only` fails when
incoming commits touch paths that also have uncommitted local changes; in a monorepo fan-out it is plausible that at
least some of an isolated run's integrated paths overlap those 4106 dirty entries. That means the **first** non-isolated
barrier task (or the run-end sync) in a realistic consumer run has a non-trivial chance of `sync_failed` → HALT —
undermining the exact scenario (the consumer) this epic exists to unblock. This is *named* (OQ-2) but its practical
likelihood is not estimated or mitigated beyond "warn and halt."
Evidence: HLD §1 field evidence (4106 entries); ADR-0013 D4 rationale; HLD OQ-2.
Fix: not necessarily a code fix pre-implementation, but T-Ee3Mn8 or the docs-refresh task should measure, against a
snapshot of the consumer's actual dirty-file set, how often those paths would collide with a real run's integrated
changes, and consider whether `_sync_checkout` should stash-and-restore only the *non-overlapping* dirty files before
the ff-only merge rather than failing outright. Name this explicitly as a first-adoption risk to measure.

**R-13 (Minor).** Git LFS is never mentioned anywhere in the 1880-line HLD, despite the design otherwise being
thorough about repo-shape edge cases (submodules, bare repos, detached HEAD via rebase, disk cost). `git worktree add`
on an LFS-tracked repo checks out LFS blobs per-worktree by default — a smaller but analogous disk-duplication cost to
the build-cache discussion in D7/NFR-6, plus LFS file-locking semantics across concurrent branches. Fine to declare
out of scope, but silence (vs. an explicit one-line "not handled, revisit if a consumer uses LFS") is a gap for a
document this thorough about everything else.

**R-14 (Minor).** Cross-validation rule V8 ("A `RepoRef` path that lies inside another reposet member's *worktree* →
fatal") is ambiguous: ao's own worktrees don't exist until dispatch time, so a **static** validation rule can only mean
"a pre-existing git worktree found on disk via git probing at validate time," not "an ao-created worktree." The
surrounding prose in §7.1's edge-cases paragraph gets this right ("rejected at validation... reposets must point at
real checkouts") but §10.4's one-line table entry reads as if it's about ao's own future worktrees. Clarify the wording
so an implementer doesn't try to check against not-yet-existent ao worktree paths.

**Checked, nothing further to flag:** detached HEAD during rebase (handled via `rebase_in_progress`/abort, standard git
behavior, adequately covered as an edge case); submodules (explicitly out of scope, warned); untracked/ignored files
produced by a task (`untracked_outputs: copy|fail|ignore`, §7.4 — adequate, with the caveat in R-8 below about how the
Integrator learns which paths are declared outputs); `.orchestrator/`'s location relative to worktrees (never inside
one, by construction of D2 — confirmed structurally sound); lock ordering for deadlock avoidance (sorted repo-key order
+ `ExitStack`, correctly unwinds on a failed acquire mid-sequence — verified by tracing the pseudocode by hand).

---

## Dimension B — Artifact/outputs semantics

**R-2 (Blocking).** The missing-outputs check runs **after** integration lands, contradicting the design's own stated
invariant. HLD §7.4's table claims the post-execution missing-output check "verifies the agent actually produced what
it declared, **before anything is integrated**." But the HLD's own `_run_and_integrate` worker pseudocode (§11 M5)
calls `self._integrator.integrate(...)` **unconditionally** once `result.status == "succeeded"`, with no outputs-check
gating it. I confirmed directly in the real code that the actual missing-outputs check lives in
`_settle_completed_task` (`engine.py:1122-1138`) — **main thread only**, which by construction runs strictly after the
worker (including, per the new design, its own `integrate()` call) has already completed:
```python
if result.status == "succeeded":
    missing_outputs = [o for o in task.outputs if not self._store.exists(o)]
    if missing_outputs:
        ...
        ts.status = "failed"
```
So for an isolated task, the git CAS-land happens **before** anyone checks the agent actually produced its declared
outputs. A task that "succeeds" at the executor level but is missing a declared output would land its (possibly
broken/incomplete) work on the shared integration ref — visible to dependents per FR-9's "integrated" gate — and only
afterward get marked `failed`, with no landed-then-undo path (T4's "retain worktree, don't land" semantics never
trigger, because landing already happened through a different code path).
Fix: move (or duplicate) the missing-outputs check onto the worker thread, through the isolated view/`effective_path`,
**before** calling `Integrator.integrate()` in `_run_and_integrate` — gate integration on outputs-present, not only on
executor status. Add an explicit AC + test to T-En8Hd4.

**R-3 (Blocking).** `should_skip`'s `skip_if_outputs_exist` branch has no integration-status gate at all — patching
only the `ts.status == "succeeded"` branch leaves the exact hazard the HLD names as its own motivation (R6/D10) wide
open. Confirmed directly at `runstate.py:156-172`, `should_skip` has two independent branches:
```python
if ts and ts.status == "succeeded":
    if not task.outputs or all(self._store.exists(o) for o in task.outputs):
        return True
if task.skip_if_outputs_exist and task.outputs:      # <-- no ts.status check at all
    if all(self._store.exists(o) for o in task.outputs):
        return True
```
This is called on **every wave** (`engine.py:527`), not only at `ao resume`. Per the design's own settle pseudocode, a
task stuck in `conflict_resolver`/`conflict_rerun` has `ts.status = "pending"` (never `"succeeded"`) while its
declared output — written during the original execution, before the conflict was even discovered — already exists on
disk. Since the consumer sets `skip_if_outputs_exist: true` on **every** fan-out entry (per the HLD's own field
evidence, §1), the second branch fires regardless of `ts.status`, forcibly marks the task `"skipped"`
(`engine.py:534`), and adds it to `ctx.done` — and because `_settled_for_dependents`'s proposed rule treats
`status == "skipped"` as automatically settled, a dependent would then proceed as if this predecessor's code had
landed, when it in fact never did. HLD's D10 text ("`should_skip` ... additionally requires
`integration_status == "integrated"` for isolated tasks") is stated abstractly, without naming which of the two
current branches it must apply to — and the branch the consumer actually exercises is the *second* one.
Fix: thread the `task_integration[tid].status == "integrated"` gate through **both** branches of `should_skip`, not
only the `ts.status == "succeeded"` one. Make this the explicit subject of T-En8Hd4's AC-11 "Dedicated regression test
for R6" — name the second branch specifically, since it is the one the consumer's own field-observed usage pattern
actually exercises.

**R-8 (Major).** The `Integrator`'s stated interface never receives the task's declared output paths, yet the design's
own mechanisms need them. HLD §11 M4 states `Integrator.__init__(spec, run_state_ref, logger, clock)` and
`integrate(task_iso, run_integration, attempt)` — no `TaskSpec.outputs` anywhere. But (a) `GitRepo.
ls_files_untracked_ignored(cwd, paths)` (§11 M1) is explicitly "for untracked-output copy-back" and takes `paths`,
which has to come from somewhere; (b) §11 M4's own step-1 pseudocode is "IF all repos' HEAD == base **AND no untracked
declared outputs**: RETURN Empty" — requiring knowledge of declared outputs to decide Empty-ness — while §8.2's
description of the *same* check ("IF tip == base AND worktree_clean: RETURN Empty") omits the declared-outputs clause
entirely. The document disagrees with itself about what the Empty check requires, and no module's interface is shown
carrying the data either version needs.
Fix: add `declared_outputs: list[str]` (or similar) to `Integrator.integrate()`'s parameters (or onto `TaskIsolation`,
populated by `T-Wk3Nv6`'s `ensure()`); reconcile §7.4/§8.2/§11 M4 to agree on the Empty-check condition; assign an
explicit AC to T-Ib5Qy9 or T-Wk3Nv6 naming who supplies it.

**Checked, nothing further to flag:** dependents wait for *integrated*, not merely *succeeded* (FR-9, `_settled_for_
dependents` — sound in principle, correctly special-cases `not_taken`/`skipped` per the engine's existing settled-set);
`emit_tasks`/`LoopSpec` clone survival of `isolation`/`touches` — **verified structurally correct, better than the HLD
assumes**: `_clone_body` (`engine.py:2153`) uses `base.model_copy(deep=True, update={...})` with only a handful of
fields explicitly overridden, so pydantic's `model_copy` carries forward every other field (including `isolation`/
`touches`, once they exist) automatically — no code change is actually needed here, contrary to the tickets' framing
of this as an open risk to verify; routing/`not_taken` cones — already correctly treated as settled today
(`engine.py:1469`), a clean base for `_settled_for_dependents` to extend; `general_instructions` — resolved through one
well-contained function (`engine.py:1381`/`1405`), an easy `effective_path` wrap.

---

## Dimension C — Retry ladder as attempts of the same task

**R-1 (Blocking).** D9's "zero new plumbing" cost-accounting claim does not hold as designed, and the reason is a bug
class this codebase has already found and fixed once. Two independent, compounding defects:

1. *`cumulative_cost_usd`/`cumulative_*_tokens` are not accumulated before a conflict-driven requeue.* HLD §11 M5's
   `_settle_completed_task` additions for `CASE "conflict_resolver"` / `CASE "conflict_rerun"` go straight from
   `ti.*` bookkeeping to `RETURN SettleResult("requeue")`, with no accumulate-into-`ts.cumulative_*` step. I confirmed
   in the real code that the **existing** self-heal requeue path had to add exactly this step, with a comment marking
   it a previously-shipped, reviewer-caught defect (`engine.py:1063-1071`): *"Reviewer-flagged Critical fix: this
   failed cycle's REAL actuals... would otherwise be silently discarded by the `continue` below... This is the same
   bug class E-9h3m7k fixed for retries WITHIN one `_run_with_retries` call; self-heal's cross-call redispatch needed
   the identical treatment."* `_run_with_retries`'s own `cum_*` accumulators (`engine.py:1978-1985`) reset to zero on
   **every call**, so a T2/T3 requeue (a brand-new call submitted to the pool) starts counting from zero — exactly
   the cross-call pattern self-heal already needed the fix for. As written, the ladder's own pseudocode reproduces the
   bug self-heal just fixed.
2. *The token-budget ledger has a separate, deeper idempotency bug that compounds this.* `DefaultBudgetManager.
   reconcile()` (`budget.py:146-159`) is a one-shot-per-`task_id` guard (`reconciled_tasks` list) — once a `task_id`
   is reconciled (which happens unconditionally at the **first** settle of *any* completed dispatch, before the
   conflict/verify outcome is even known), any later `reconcile()` call for a T2/T3 redispatch of the *same* task_id
   is silently a no-op. `reverse_estimate()` (`budget.py:161-171`, called only in the HLD's `conflict_resolver` branch,
   **not** `conflict_rerun`) pops `charged_estimate[task_id]` but never clears `reconciled_tasks`, so it doesn't
   restore reconcile-ability either — and by the time the conflict is even discovered, `reconcile()` has typically
   already run once for the original "succeeded" result, so `reverse_estimate` in the conflict_resolver branch is
   likely a no-op too (its own guard: `if task_id not in counters.charged_estimate: return`). Net effect: a
   conflicting task's real token/cost spend on its T2/T3 attempt(s) is invisible to `total_tokens`/rate-window gating
   and undercounted in `cumulative_cost_usd` — directly contradicting D9's central promise and risk R2's "bounded for
   free" mitigation, precisely for the pathological-repeat-conflict scenario the design says is safety-bounded.

Evidence: `engine.py:1063-1071` (self-heal's fix + comment), `engine.py:1978-2058` (`_run_with_retries`'s per-call
reset), `budget.py:146-171` (`reconcile`/`reverse_estimate`), HLD §11 M5 (`conflict_resolver`/`conflict_rerun` cases).
Fix: (a) accumulate `outcome.result`'s actuals into `ts.cumulative_*` **before** returning `SettleResult("requeue")`
for both `conflict_resolver` and `conflict_rerun`, mirroring the self-heal pattern exactly; (b) fix `budget.py` so
`reconcile`/`charge_estimate` can be safely re-run for a redispatched `task_id` — e.g. key `reconciled_tasks`/
`charged_estimate` by `(task_id, attempt_index)`, or have escalation explicitly evict `task_id` from `reconciled_tasks`
whenever a new `charge_estimate` is issued for it. Add both as explicit ACs to T-En8Hd4 (settle bookkeeping) and
T-Lr6Ka3 (D9 cost accounting) — neither ticket's current AC list catches this today.

**R-9 (Major).** `RetryPolicy.max_attempts` interaction with T2/T3 is unaddressed in the design text, though direct
code reading suggests it is *probably* safe by an existing precedent that no ticket cites or tests. `RetryPolicy.
max_attempts` defaults to **1** (`models.py:64`), and `_run_with_retries`'s internal `for attempt in range(1, retry.
max_attempts + 1)` loop (`engine.py:1985`) resets fresh on every call — so a T2/T3 requeue (a new pool submission) gets
its own fresh budget, mirroring this codebase's existing, explicitly-documented precedent for self-heal: *"heal
retries never consume `RetryPolicy.max_attempts` accounting (a completely separate counter)"* (`engine.py:199-204`).
This means the default `max_attempts=1` likely does **not** starve T2/T3 — but no HLD text or ticket states this
precedent must be followed, or requires a test proving it. An implementer who instead gates redispatch by comparing
`ti.attempts` (or similar) against `retry.max_attempts` directly would silently break the ladder for every workflow
using the default retry policy.
Fix: cite the self-heal precedent explicitly in HLD §13, and add an AC + test to T-Lr6Ka3 proving a task with the
*default* `RetryPolicy` (max_attempts=1) still completes a full T1→T2→T3 escalation without being marked failed by
retry exhaustion.

**Checked, nothing further to flag:** cancellation/timeouts mid-rebase (drain-don't-kill, ADR-0007 D7, correctly
reused — the worker finishes its bounded git sequence, CAS is the correctness backstop); quota-exhaustion interaction
— the design's `ti.mode` field is read fresh at each dispatch from persistent `RunState`, so a quota-wait requeue would
correctly re-enter with the right substituted agent/instruction on the next attempt; this is architecturally sound by
construction, though (like R-9) not explicitly tested by any ticket — worth a one-line AC addition, not elevated to
its own finding since the mechanism is the same one R-9 already covers.

---

## Dimension D — Scheduler ranking

**R-5 (Blocking).** `rank_wave`/hotspots wiring into the engine's dispatch loop has **no assigned, testable acceptance
criterion in any of the 12 tickets** — FR-10's core mechanism risks shipping as dead code. T-Ov9Bt5's own TASK.md
explicitly disclaims engine.py: *"Do NOT touch: `engine.py` (the one-line wave-fill call site is `T-En8Hd4`'s; if it
is not there yet, ship `rank_wave` standalone and flag it)."* But T-En8Hd4's TASK.md acceptance criteria (14 items,
read in full) never mention `rank_wave`, `scheduling/overlap.py`, `overlap_preference`, or hotspots — its ACs cover
`_settled_for_dependents`, `_is_barrier`, `_activate_integration`, `IsolatedArtifactView`, `TaskContext.env`,
`_run_and_integrate`, `_settle_completed_task`, `should_skip`, checkout sync, and cancel/halt, but not the wave-fill
ranking call site. The HLD's own §11 M8 subtask list even lists "(6) engine wiring + `hotspots_path` load-with-fallback"
as one of *T-Ov9Bt5's own* subtasks — directly contradicting T-Ov9Bt5's explicit ticket text. As written, nobody's
acceptance criteria requires the one-line `ranked = rank_wave(...)` call to actually exist in shipped `engine.py`, nor
a test proving the wave loop applies it when `overlap_preference == "soft"`. I independently confirmed the insertion
point is clean and non-disruptive (right after the `ready = self._ready_ids(...)` line in `run()`'s wave-fill loop) —
so this is a coordination gap in the tickets, not a design infeasibility.
Fix: add an explicit AC to T-En8Hd4 requiring the wave-fill call site to invoke `rank_wave` (with hotspots loaded via
`load_hotspots`) when `overlap_preference == "soft"`, with a dedicated test asserting soft-preference ordering is
actually applied in a live wave dispatch. Fix HLD §11 M8's subtask list to match the ticket boundary (both should name
T-En8Hd4 as sole owner of the call site — this is the same fix as R-18 below).

**Checked, nothing further to flag:** `rank_wave` purity/determinism — the pseudocode is pure (no git/filesystem/clock),
the `n==1` no-op and never-withholds-a-slot properties are both provable from the algorithm as written and both have
dedicated property tests planned (T-Ov9Bt5 AC-2/3); tie-breaking is deterministic (`ready.index(t)` throughout);
hotspot input format and matching cost — bounded by `top_k=40` (a constant), so the stated "independent of repo size"
complexity claim holds even though hotspot-weight lookup is a second (glob-vs-path) matching pass distinct from
`glob_intersection` (glob-vs-glob) — this is a real second algorithm the pseudocode doesn't fully spell out, but it's
bounded and cheap, not worth a separate finding.

---

## Dimension E — Multi-repo reposets

Covered substantively under Dimension A (R-6 `worktree prune` scope, R-7 CAS-retry scope) and Dimension B (R-8
Integrator's missing declared-outputs input). Additionally checked and sound: `group_repos`'s grouping by
`git rev-parse --git-common-dir` correctly handles the real consumer's nested-`RepoRef` shape (`core=./fin_plan`,
`docs=./fin_plan/docs-md`) as one `IsolatedRepo`; `specs/reposet.schema.json` (read in full) places no constraint that
would reject or collide on this shape today — confirms the HLD's assumption is accurate; lock acquisition in globally
sorted repo-key order, released via `ExitStack`, is a standard, correct deadlock-avoidance discipline, and unwinds
correctly (releases already-acquired locks) when a later acquire in the same call times out, verified by tracing the
pseudocode's `WITH ExitStack()` scoping by hand. A `RepoRef` that is not a git repo mixed with ones that are is handled
(`group_repos`'s "not a git repo → RECORD non_git(repo_id); CONTINUE" — such repos simply stay unisolated / shared,
which is consistent with FR-12's per-repo degrade framing, though the HLD doesn't spell out that degrade is per-repo
rather than per-run here — worth a one-line clarification, not a separate finding).

---

## Dimension F — Schema/config

**R-10 (Major).** ADR-0013's foundational "accepted gap" quotation from ADR-0007 does not exist in ADR-0007. ADR-0013's
Context section presents, as a markdown blockquote (i.e., as a verbatim citation): *"With `max_parallel > 1`,
co-scheduled tasks are not checked for overlapping outputs — keeping them disjoint is the spec author's job."*
attributed to ADR-0007. I grepped ADR-0007's complete text for "overlap", "conflict", "collide", "collision",
"disjoint", and "spec author" — **zero matches**. Its actual "Consequences" section discusses only executor
thread-safety and log-line interleaving, nothing about write-conflicts or file-collision responsibility. The identical
unsourced phrase also appears (paraphrased as an indirect claim) in `task-isolation-hld.md:36`. I also checked
`parallel-execution-hld.md` (cited alongside ADR-0007 as the source) — no match there either. The underlying *concern*
is legitimate regardless (max_parallel>1 genuinely ships with no write-conflict detection, confirmed by the actual
`repo_paths` sharing at `engine.py:304-305`) — this is a citation-integrity problem, not a substance problem, but on a
project whose own conventions (CLAUDE.md, `meta/tickets/README.md`) place heavy weight on traceable, attributable
records, a fabricated verbatim quotation attributed to a specific prior ADR should be corrected before the
docs-refresh task inherits it uncorrected.
Fix: correct ADR-0013's Context section to present the gap as the epic author's own characterization of ADR-0007's
D1-D7 (accurate in substance), not as a direct quote; either drop the blockquote formatting or cite the actual correct
source (`meta/ROADMAP.md` §4's "already failed in the field" framing is the closest real match).

**R-11 (Major, DRY).** `isolation/paths.py::state_dir()` would be a third, near-duplicate implementation of the same
env→XDG→default resolution pattern. Confirmed directly: `service/paths.py`'s `default_state_dir()` uses
`AO_SERVICE_STATE_DIR > $XDG_STATE_HOME/ao/service > ~/.local/state/ao/service` — a **different** env var name and a
different final subdirectory than the HLD's proposed `AO_STATE_DIR > $XDG_STATE_HOME/ao > ~/.local/state/ao`.
`project_config.py` documents its own separate implementation of the identical pattern-shape already. The HLD's claim
that its new function "mirrors `service/paths.py`" is true only in *shape*, not by *reuse* — a third hand-copied
implementation violates CLAUDE.md's no-duplicate-logic rule.
Fix: factor a shared helper (e.g. `_resolve_xdg_path(override_env, xdg_env, subdir, default_base)`) in a common
module, reused by `service/paths.py`, `project_config.py`, and the new `isolation/paths.py::state_dir()`. Add to
T-Wk3Nv6's scope.

**R-14, R-15 (Minor)** — see Dimensions A and below.

**R-16 (Minor, documentation clarity).** HLD §7.3's own example code block shows a **mutating**-constructor shape
(`LocalFsArtifactStore.__init__(self, workspace_root, extra_roots=())`) as the primary illustration of the path-guard
widening, with the safer non-mutating `IsolatedArtifactView` wrapper relegated to a "preferred implementation shape"
footnote. I confirmed directly that `RunStateStore` and the task artifact store are **the same `LocalFsArtifactStore`
instance** today (`cli.py`'s construction pattern: `store = LocalFsArtifactStore(workspace); rs_store =
RunStateStore(workspace, store)`) — so the mutating-constructor shape, if followed literally, would silently widen
`RunStateStore`'s roots too, directly violating §7.3's own rule 3 ("the store used for run state keeps the un-widened
root"). T-En8Hd4's AC-6 correctly commits to the wrapper shape, so this is probably harmless in practice, but the HLD
body should make the wrapper **normative**, not merely "preferred," and should not present a literally-followable
example that violates its own safety rule.

**R-15 (Minor, naming).** `output_manifest_path` (existing — an agent-written manifest, subject to `effective_path`
remap) and `task_manifest_path` (existing — engine-read for `emit_tasks`, explicitly **excluded** from remap) are easy
to confuse in HLD §7.2's remap-exclusion list, which names both in close proximity. Not a bug, but worth an explicit
code comment at the actual `effective_path` call sites so an implementer doesn't invert the two.

**Checked, nothing further to flag:** pydantic `extra=` config — confirmed via direct grep that neither `models.py`
nor `project_config.py` overrides pydantic's default, and `project_config.py:158` explicitly documents relying on
"pydantic's default `extra=\"ignore\"`" — the HLD's backward/forward-compat assumption for `RunState`/`WorkflowSpec`
is sound; `cross_validate`'s existing structure (`spec.py`: a top-level function delegating to
`budget_cross_validate`, `_check_reserved_id`, `_effective_deps`, `_simulate_route_selection`,
`_check_any_join_satisfiability`, `validate_run_control`) is already modular per-concern, so bolting on V1-V9 as new
sub-checks is a clean, additive extension consistent with the existing pattern; `errors.py` is confirmed to be a
single-hierarchy error home (`OrchestratorError` base, 10+ subclasses) — `GitError(OrchestratorError)` fits the
existing convention exactly as T-Gt4Pw8 plans; the routed-runner breakdown contract's current field allowlist
(confirmed verbatim at `breakdown-contract.md.tmpl:122-124`: `id, agent, instruction, depends_on, inputs, outputs,
timeout_seconds, skip_if_outputs_exist, effort, model, max_turns`) matches the HLD's claim exactly, and the
"Minimize collision" bullet needing rewrite (confirmed verbatim at `07-task-breakdown.md:33-35`: *"where two tasks
must touch the same file, make one depend on the other"*) is exactly the anti-pattern the epic wants to eliminate —
T-Tp7Zs2's characterization of both is accurate.

---

## Dimension G — Ticket quality

**R-5, R-18** (rank_wave/hotspots wiring orphan and its HLD/ticket contradiction) already covered above under D/F.

File-ownership discipline is otherwise good: I independently re-verified (via `grep` over every `TASK.md`'s "Files you
own" section, not just prose claims) that `engine.py` is touched by exactly the three tasks the epic doc claims
(T-En8Hd4 main, T-Lr6Ka3 resolver/rerun dispatch only, T-Cx4Jf1 event-emission audit only), `cli.py` is cleanly split
between T-Cx4Jf1 and T-Ov9Bt5, `runstate.py` is cleanly split between T-Sc7Rm2 (state/prepare_resume/write_status) and
T-En8Hd4 (`should_skip` only), and `artifacts.py`/`models.py` each have exactly one owner. Task sizing (all ≤3 days),
dependency ordering (T-Gt4Pw8/T-Sc7Rm2 parallel-first, everything else correctly gated on real interface
dependencies), and ticket-status sync (all 12 `TASK.md`/`STATUS.md` pairs consistently "Draft", epic rollup
consistent) all check out cleanly.

**R-18 (Minor).** HLD §11 M8's subtask list item "(6) engine wiring + `hotspots_path` load-with-fallback" contradicts
T-Ov9Bt5's own ticket text ("Do NOT touch: `engine.py`"). Same underlying gap as R-5 — fix both documents to agree
that T-En8Hd4 alone owns the engine.py call site.

**Missing-task check:** no gap found beyond R-5's coordination issue. Migration of existing runs is correctly N/A
(nothing to migrate pre-implementation); `ao prune` worktree GC is explicitly in T-Cx4Jf1's scope; dashboard visibility
is explicitly in T-Cx4Jf1's scope (AC-9); docs are explicitly T-Dr5Yq6's scope. No contradiction with CLAUDE.md's
no-magic-literals/pluggable-interfaces/no-duplicate-logic rules found beyond R-11 (DRY, state_dir) above — the design
is otherwise unusually disciplined about named constants (`GIT_DEFAULT_TIMEOUT_SECONDS`, `GIT_MIN_VERSION`,
`DEFAULT_VERIFY_TIMEOUT_SECONDS`, `CONFLICT_WEIGHT = 5`, etc., all named module constants per the tickets' own ACs)
and about pluggability (`Integrator`/`WorktreeManager` injectable into `Orchestrator`, resolver/escalation hooks
injected into `Integrator` rather than hard-wired — matching the existing `budget_manager`/`monitor` injection
precedent).

---

## Dimension H — Test strategy

The planned strategy (pure-function unit tests for `effective_path`/`sanitize_ref_component`/`rank_wave`/
`plan_resolution`/`escalate`/churn-parsing; real-`git init` integration fixtures for every conflict class incl.
`add_add`/`delete_modify`/`binary`/`semantic`; `CliRunner` e2e at `max_parallel: 3` with a `fake` executor; explicit
crash-mid-rebase/resume tests; a dedicated NFR-2 regression gate) is well above the bar for this stage and correctly
distinguishes unit/integration/e2e levels per CLAUDE.md's own testing guidance. Gaps specific to the findings above,
none of which are in the current plan:

- A test proving the accumulate-before-requeue fix for `conflict_resolver`/`conflict_rerun` (R-1) — mirror the
  self-heal test pattern already in this codebase.
- A test proving missing-outputs is checked **before** `integrate()` lands for an isolated task, not just that it's
  eventually detected (R-2).
- A `should_skip` test that specifically targets the `skip_if_outputs_exist` branch (not just the `ts.status ==
  "succeeded"` branch) with a task stuck in `conflict_resolver`/`conflict_rerun` (R-3, folded into T-En8Hd4 AC-11
  which currently only names "R6" generically — name the branch explicitly).
- A test for two concurrent runs' checkout-sync race (R-4) — cannot be written meaningfully until the multi-run policy
  is decided, but should be added to the backlog now rather than discovered during `T-Ee3Mn8`.
- A multi-repo CAS-retry test asserting an already-landed repo is **not** reprocessed when a sibling repo's CAS loses
  (R-7).
- A test that a hand-created "foreign" (non-ao) worktree survives `reconcile()`/`ensure()` untouched (R-6).
- A test with the **default** `RetryPolicy(max_attempts=1)` driving a task through T1→T2→T3 to prove it isn't starved
  by retry-exhaustion (R-9).

**What to mock:** `Integrator`, `WorktreeManager`, and the resolver/escalation hooks are already designed as
injectable — use fakes/stubs for `T-En8Hd4`'s engine-level tests exactly as `budget_manager`/`monitor` are faked today.
**What to integration-test (real git, no mocks):** the full squash→rebase→verify→CAS sequence, worktree lifecycle,
and — critically — the cross-process lock (a real second OS process, not just a second thread, exactly as T-Ib5Qy9
AC-1(b) already plans).

---

## Verdict

**APPROVE WITH CHANGES.**

The mechanism design (git plumbing, locking, CAS landing, ladder structure, injection points) is sound and does not
need rework. The Blocking findings are real but narrow and fit the existing codebase's own established patterns for
the fix (this codebase has already solved the identical cross-call cost-accounting problem for self-heal, and the
identical max_attempts-independence problem for self-heal — the isolation ladder just needs to reuse those patterns
explicitly rather than reinvent them incompletely). R-19 is the exception worth naming plainly: it is not a subtle
edge case, it is a gap in the **primary execution path** — the HLD's pseudocode does not yet show how an isolated
task's agent actually gets pointed at its worktree instead of the shared checkout. It must be resolved (as a
signature/threading fix documented in the HLD and reflected in T-En8Hd4's AC-7) before T-En8Hd4 is considered
correctly specified, though it still does not block T-Gt4Pw8/T-Sc7Rm2 from starting today.

**Specific answer to "what must resolve before `T-Gt4Pw8` starts":** nothing. `T-Gt4Pw8` (`isolation/git.py`) is a
self-contained, dependency-free git-plumbing wrapper; none of the findings above touch it, and it can start
immediately, in parallel with `T-Sc7Rm2`, exactly as planned.

**What must resolve before the affected tasks merge** (via HLD/ADR/ticket amendments — not necessarily full code —
so the acceptance criteria are correct when those tasks are actually implemented):
- **R-19** (isolated execution doesn't actually route through the worktree), **R-2** (missing-outputs sequencing),
  **R-3** (`should_skip` both branches), **R-4** (concurrent-run barrier-sync race), and **R-23** (`release()` never
  called on a plain execution failure) — before **T-En8Hd4** merges (Sprint 1). R-4 possibly also requires a small
  addition to **T-Sc7Rm2**'s state model if a lock/policy field is needed.
- **R-1** (cost/token accounting gap), **R-9** (max_attempts precedent), and **R-21** (attempt-counter/transcript
  clobbering on requeue) — before **T-Lr6Ka3** merges (Sprint 2), and R-1/R-21 likely also touch **T-En8Hd4**'s
  settle-time bookkeeping.
- **R-20** (`Integrator` constructor signature disagreement, NFR-3 risk) — before **T-Ib5Qy9** merges (Sprint 1);
  this is a five-minute fix (pick one signature) but must happen before the ticket's own tests are written against
  whichever signature is wrong.
- **R-5** (`rank_wave` wiring orphan) — before **T-En8Hd4** and **T-Ov9Bt5** are both considered done (an AC must be
  added to one of them now, before Sprint 2 planning finalizes).
- **R-22** (per-task `git push` instructions contradict the isolation model) — before **T-Tp7Zs2** merges (Sprint 2);
  widen its scope now rather than discover it during `T-Ee3Mn8`'s e2e pass.

Major findings **R-6, R-7, R-8, R-10, R-11, R-12** should be folded into the relevant tickets' acceptance criteria
before those tickets' respective owning tasks (T-Wk3Nv6, T-Ib5Qy9, T-Dr5Yq6) merge, but do not block Sprint 1 from
starting. Minor findings **R-13 through R-18, R-24** are documentation-quality items for T-Dr5Yq6 to pick up.

---

## Findings index

| ID | Severity | Dimension | One-line |
|---|---|---|---|
| R-1 | Blocking | C | T2/T3 requeue silently discards cost/token accounting (two compounding bugs: engine settle + budget.py reconcile idempotency) |
| R-2 | Blocking | B | Missing-outputs check runs after integration lands, contradicting HLD §7.4 |
| R-3 | Blocking | B | `should_skip`'s `skip_if_outputs_exist` branch has no integration-status gate — R6 half-fixed |
| R-4 | Blocking | A | Concurrent-runs barrier-sync race, named by ROADMAP §3.4, unaddressed by this design |
| R-5 | Blocking | D/G | `rank_wave`/hotspots engine wiring has no owning acceptance criterion anywhere |
| R-6 | Major | A | `git worktree prune()` is unscoped — can deregister the user's own unrelated worktrees |
| R-7 | Major | A/E | Multi-repo CAS-retry scope ambiguous — could double-land an already-integrated repo |
| R-8 | Major | B | `Integrator`'s interface never receives declared outputs, though its own logic needs them |
| R-9 | Major | C | `RetryPolicy.max_attempts` interaction with T2/T3 unaddressed in text (likely safe, untested) |
| R-10 | Major | F | ADR-0013's founding quote from ADR-0007 does not exist in ADR-0007 |
| R-11 | Major | F | `state_dir()` would be a third near-duplicate of an existing env/XDG resolution pattern |
| R-12 | Major | A | Dirty-checkout barrier-sync failure risk likely underestimated for the real consumer |
| R-13 | Minor | A | Git LFS never mentioned |
| R-14 | Minor | A/F | V8's "worktree" wording ambiguous (pre-existing vs ao-created) |
| R-15 | Minor | F | `output_manifest_path` vs `task_manifest_path` naming trap |
| R-16 | Minor | F | HLD §7.3's mutating-constructor example contradicts its own normative rule |
| R-17 | Minor | A | HLD §1's "self-heal git stash" framing could be misread as this repo's `monitoring.py` |
| R-18 | Minor | D/G | HLD §11 M8 subtask list contradicts T-Ov9Bt5's explicit ticket boundary |
| R-19 | **Blocking** | B/A | `_run_with_retries` resolves paths via the shared store, not any isolated view — isolation doesn't route execution to the worktree as pseudocoded |
| R-20 | Major | C | `Integrator`'s constructor signature disagrees between the HLD and the ticket's locked interface — NFR-3 risk |
| R-21 | Major | C | Requeue (T2/T3/self-heal) restarts the attempt counter, clobbering the original attempt's transcript capture |
| R-22 | Major | F/G | Per-task routed-runner instructions tell agents to `git push`, contradicting the isolation model |
| R-23 | Major | A/B | `release()` is never called for a plain (non-integration) task-execution failure |
| R-24 | Minor | E | `RepoRef.role` load-bearing claim did not hold up on inspection — recorded, no action needed |

**Total: 6 Blocking (R-1, R-2, R-3, R-4, R-5, R-19), 11 Major, 7 Minor — 24 findings.**

**Must resolve before implementation of `T-Gt4Pw8` starts: none.** (See Verdict for the per-task gating that does
apply to later tasks — most urgently R-19 and R-20, which affect the two hardest tasks in the epic, T-En8Hd4 and
T-Ib5Qy9, and are cheapest to fix now, in the design, before either ticket's tests are written against an
incompletely- or incorrectly-specified interface.)
