# REVIEW: T-Rm2Lx7-mechanical-resolvers

- Reviewer: reviewer-agent
- Date: 2026-09-07
- Scope reviewed (uncommitted): `src/agent_orchestrator/isolation/resolvers.py` (new),
  `tests/isolation/test_resolvers.py` (new), `TASK.md`/`STATUS.md` for this task and the epic
  rollup (`meta/tickets/E-Wk9Tz3-task-isolation/STATUS.md`). Cross-read (not re-reviewed as
  deliverables of this task, since neither is owned by it and neither is being touched by anyone
  else): `src/agent_orchestrator/isolation/integrator.py` (committed, HEAD `fd75dce`),
  `src/agent_orchestrator/models.py` (committed), `tests/isolation/test_integrator.py::
  TestMechanicalResolution` (committed), `docs-md/task-isolation-hld.md` §6.4/§8.4/§8.5/§8.6/§11
  M1/M6/M7, §24. Read `T-Lr6Ka3-llm-resolver-and-rerun/TASK.md` for the downstream contract this
  task must leave intact. Per the brief, did **not** touch or evaluate `T-Wl2Bq7`'s uncommitted work
  (`engine.py`, `isolation/runlock.py`, `isolation/git.py`'s new `fast_forward_checkout`,
  `errors.py`, the associated test files) beyond reading `git.py`'s diff to confirm its additive
  shape for the coordination note in C-1.
- Verified independently (not trusted from STATUS.md): `uv run ruff check` / `ruff format --check`
  on `resolvers.py` + `test_resolvers.py` — clean, both already formatted. `uv run mypy src` —
  exactly 4 pre-existing `_version.py` errors, no new ones, no `engine.py` transient error observed.
  `uv run pytest tests/isolation/test_resolvers.py tests/isolation/test_integrator.py -q -p
  no:cacheprovider --durations=10` — **78 passed**, matching STATUS.md; slowest test 1.09s
  (`test_hang_is_killed_at_its_own_rule_timeout_not_the_lock_timeout`, as expected for a
  timeout-bound test). `grep -rn "subprocess" src/agent_orchestrator/isolation/resolvers.py` — 3
  raw `subprocess.run(["git", ...])` call sites confirmed (see C-1). Coverage on `resolvers.py`
  via `pytest --cov`: **94%** (179 stmts, 10 miss: 267-271, 273, 307, 428-439, 526), matching
  STATUS.md's number but not its full characterization (see C-7).

## Verdict: **APPROVE WITH CHANGES**

Must-fix before this ticket (or the epic) is considered done: **C-1** (S-1 porcelain bypass —
`COORDINATE` with `T-Wl2Bq7`'s concurrent `git.py` edit), **C-2** (rerere escape hatch is wired
nowhere in production — lives in committed `integrator.py`, not this task's files, but nobody else
is editing that file so this task's fix loop can repair it), **C-3** (ladder's `"mechanical"` entry
has no effect anywhere in the code — same disposition as C-2). C-4/C-5 are should-fix,
non-blocking. C-6/C-7/C-8 are nits.

The `plan_resolution`/`apply_plan`/`resolve_mechanically` implementation itself — the part this
task actually owns and was asked to build — is careful, correct against its own ACs, and honestly
self-documented. Both bugs the developer found and fixed (premature index resolution on a failed
regenerate; unscoped `add -A` staging a sibling's still-conflicted path) are real, well-understood,
and each has a real regression test that would have caught it. The two disclosed gaps (C-1, C-2)
are exactly what the developer said they were: genuine interface gaps in modules this task does not
own, correctly *not* worked around by reaching into someone else's file, and clearly flagged rather
than silently shipped. That is the right call under the ticket's "Do NOT touch" boundary — it does
not, however, make the gaps themselves optional.

---

## Summary

Solid, well-tested T1 mechanical resolver implementation matching HLD §11 M6's precedence and
edge-case table almost exactly, with real `git init` fixtures throughout (no mocked git) and two
correctly-diagnosed, correctly-fixed deviations from the HLD's own (slightly wrong) pseudocode. The
main design-health concern is not in this module's own logic but at its two seams: it reaches around
`GitRepo`'s porcelain with local `subprocess` calls (C-1), and the module correctly assumes
`Integrator` threads `spec.resolvers.rerere` into the `GitRepo` it hands to `resolve_mechanically`
— an assumption that is false in production (C-2), which also means the ladder's advertised ability
to disable a tier by removing it from `integration.ladder` does not hold for `"mechanical"` at all
(C-3). None of these are structural problems with this task's own code; they are real gaps at its
boundary that block the epic's own stated guarantees (S-1's single choke point; S-5/OQ-4's rerere
escape hatch) from actually being true end-to-end.

## Critical (Blocking)

### C-1 — S-1 bypass: raw `subprocess` git calls instead of `GitRepo` porcelain
**Location:** `src/agent_orchestrator/isolation/resolvers.py:232-274` (`_merge_file_union`) and
`:341-357` (`_git_checkout`), called from `UnionResolver.apply` (`:305`) and
`RegenerateResolver.apply` (`:404, :426, :438, :441`).

**Evidence:**
```
$ grep -rn "subprocess" src/agent_orchestrator/isolation/resolvers.py
258:            cp = subprocess.run(  # noqa: S603 -- fixed argv, no shell, hardened timeout
349:    subprocess.run(  # noqa: S603 -- fixed argv, no shell
407:            cp = subprocess.run(  # noqa: S603 -- fixed argv from spec, no shell
```
Line 349's argv is literally `["git", "checkout", *args]`. HLD §11 M1 states plainly: *"One typed,
timeout-bounded, exception-safe surface for every git call. **Nothing else in the codebase shells
out to git.** It is also the single choke point where the engine's own git invocations are made
hook-free, editor-free, signature-free and network-free (S-1)."* §24's own Blocking-table
disposition for S-1 is *"`SAFETY_ARGS` on every invocation at the single choke point... the gate
test is proven non-vacuous."* Every git invocation in `resolvers.py` except the ones routed through
the injected `GitRepo` (`show_stage`, `add_paths`, `conflicted_paths`) skips `SAFETY_ARGS`
(`core.hooksPath=EMPTY_HOOKS_DIR`, `commit.gpgsign=false`, `core.editor=true`, `gc.auto=0`), the
`FORBIDDEN_SUBCOMMANDS` guard, and `GIT_TERMINAL_PROMPT=0`/`GIT_ASKPASS=""` (the caller's `env` is
passed through verbatim, and `Integrator._base_env` builds it from **full** `os.environ`, not
`GitRepo._run`'s hardened env — so `_git_checkout`'s `git checkout --ours/--theirs/--merge` runs
with none of `_run`'s env hardening in production, only the caller's own environment).

**Why it matters:** this is precisely the layering breach S-1 exists to prevent, and it is not
hypothetical — the epic's own security-design review gate closed S-1 as Blocking specifically on
the promise that *nothing else* shells out to git. Practically the immediate injection risk is low
(fixed argv; `step.path` comes from `git diff --name-only --diff-filter=U`, not attacker input;
`rule.take` is a closed pydantic `Literal`), but `git checkout --ours/--theirs/--merge -- <path>`
inside a repo whose worktree shares the main checkout's hooks directory is exactly the class of call
the porcelain's hook suppression exists for, and the module's own docstring (`:43-50`) already
identifies this as a known gap rather than an oversight.

**Fix:** add a minimal additive set to `GitRepo` (HLD §11 M1 is the right home — it already owns
`show_stage`/`add_paths`/rebase ops):
```python
def checkout_stage(self, cwd: str, side: Literal["ours", "theirs"], path: str) -> None:
    """git checkout --ours|--theirs -- <path> (worktree-file only, index untouched)."""
    self._run(["checkout", f"--{side}", "--", path], cwd=cwd, check=False)

def checkout_merge(self, cwd: str, path: str) -> None:
    """git checkout --merge -- <path> (restores original conflict markers)."""
    self._run(["checkout", "--merge", "--", path], cwd=cwd, check=False)

def merge_file_union(self, ours: bytes, base: bytes, theirs: bytes) -> bytes | None:
    """git merge-file --union -p over three blobs via temp files; None on any failure."""
    ...  # body is resolvers.py's existing _merge_file_union, moved verbatim
```
then delete `_git_checkout`/`_merge_file_union` from `resolvers.py` and call the new methods
through the `git: GitRepo` parameter already threaded everywhere in this module. `checkout_stage`/
`checkout_merge` naturally pick up `SAFETY_ARGS` + the hardened env from `_run`; note the timeout
changes from the module's current 30s ceiling to `GitRepo`'s 300s default unless a `timeout=` is
threaded through — worth a one-line call-out in the PR, not a blocker.

**`COORDINATE`:** `T-Wl2Bq7` is concurrently adding `GitRepo.fast_forward_checkout` (append-only,
after `reset_hard`, before `diff_names`, confirmed via `git diff -- isolation/git.py`). The three
new methods above should be appended in the same append-only style, under distinct names, so both
land without a textual merge conflict regardless of landing order.

## Major

### C-2 — `resolvers.rerere: false`'s documented escape hatch is not wired in production
**Location:** `src/agent_orchestrator/isolation/integrator.py:340-352` (`Integrator._git_for`) —
committed, HEAD `fd75dce`, not touched by this task or `T-Wl2Bq7`.

**Evidence:**
```python
def _git_for(self, repo: RepoIsolation) -> GitRepo:
    git = self._git_repos.get(repo.key)
    if git is None:
        git = GitRepo(repo.toplevel, runner=self._runner, hooks_dir=self._hooks_dir)
        self._git_repos[repo.key] = git
    return git
```
`GitRepo.__init__` defaults `rerere: bool = True` (`git.py:494`). `_git_for` never passes `rerere=`,
so every `GitRepo` the integrator constructs has rerere **on**, regardless of
`IntegrationSpec.resolvers.rerere` (`models.py:425`, default `True` but author-settable to `False`).
HLD OQ-4 (`docs-md/task-isolation-hld.md:2549-2551`) documents this exact field as the answer to
*"a bad [rerere] resolution can persist... `resolvers.rerere: false` is the escape hatch"* — this is
not incidental config, it is the epic's own stated mitigation for the rerere risk both `TASK.md`
Risks and HLD §24's S-5 disposition point at.

`resolvers.py`'s own crediting logic is correct in isolation — `resolve_mechanically` only labels a
pre-resolved path `tier=mechanical resolver=rerere` when `config.rerere` is true (`:528`), and
`test_rerere_false_disables_replay_and_never_writes_git_config` proves that *when the caller hands
it a `GitRepo(rerere=False)`*. But no test anywhere exercises the real production wiring
(`Integrator._git_for` constructing the `GitRepo` it then passes to `resolve_mechanically`) with
`resolvers.rerere: false` — `test_integrator.py`'s only rerere-through-`Integrator` test
(`TestMechanicalResolution::test_rerere_only_resolution_lands_without_ever_calling_the_resolver_hook`)
uses the default `IntegrationSpec()` (`resolvers.rerere` defaults `True`). This task's own tests
construct `GitRepo` directly (`_git_repo(repo, rerere=rerere)`, `test_resolvers.py:57-58`),
bypassing `_git_for` entirely, so they cannot and do not catch this.

**Why it matters:** an operator who sets `resolvers.rerere: false` to stop a bad cached resolution
(`$GIT_DIR/rr-cache` is repo-persistent, per the task's own Risks section) gets no effect at all —
git's rerere still replays automatically **inside `git rebase` itself**, before `resolve_mechanically`
is ever called (the `outcome.paths == []` fast path at `integrator.py:987-994`, which never invokes
`resolver_hook`). The only thing `resolvers.rerere: false` currently controls is whether a path
already resolved *some other way* gets *credited* as tier `rerere` in `resolvers.py`'s own bookkeeping
— it does not, and cannot, stop git's own replay, because the flag never reaches the `GitRepo`
instance whose per-invocation `-c rerere.*` args actually drive that behavior.

**Fix:**
```python
git = GitRepo(
    repo.toplevel,
    runner=self._runner,
    hooks_dir=self._hooks_dir,
    rerere=self._spec.resolvers.rerere,
)
```
**Test to add** (in `tests/isolation/test_integrator.py`, mirroring
`test_rerere_only_resolution_lands_without_ever_calling_the_resolver_hook`'s teach/replay fixture
but through a real `Integrator(IntegrationSpec(resolvers=ResolverConfig(rerere=False)), ...)`):
teach a resolution on a throwaway branch, then run the *same* conflict through
`integrator.integrate(...)` with `rerere=False` in the spec, and assert the task lands in
`conflict_resolver`/`conflict_rerun` (escalated) with the path still genuinely conflicted, **not**
`STATUS_INTEGRATED` — proving the escape hatch actually prevents the replay end-to-end, not just
the crediting.

### C-3 — `IntegrationSpec.ladder`'s `"mechanical"` entry is never checked
**Location:** `src/agent_orchestrator/isolation/integrator.py:970-1008`
(`Integrator._rebase_onto_and_resolve`) — committed, not touched by this task.

**Evidence:** the method calls `self._resolver_hook(...)` unconditionally on any conflict
(`:987-990`) with no check of `"mechanical" in self._spec.ladder` anywhere in the file (confirmed
by grep — the only `ladder`-adjacent references in `integrator.py` are docstring prose). HLD §8.4's
opening line is explicit: *"`integration.ladder` is an ordered list, default `["auto",
"mechanical", "llm", "rerun"]`; **removing an entry disables that tier**."* Today, a workflow
author who writes `ladder: ["auto", "llm", "rerun"]` — e.g. to force every conflict through an
audited LLM resolver rather than an unreviewed union/regenerate/rerere pass — gets no such effect:
T1 (rerere replay, union, regenerate) still runs unconditionally.

**Why it matters:** this is a real spec/DAG-correctness gap against the epic's own documented
contract (challenge item 3 in this review's brief asked exactly this question). It compounds C-2 —
even a spec author who removes `"mechanical"` from the ladder *and* sets `resolvers.rerere: false`
still gets git's own automatic rerere replay, with no way in the current spec surface to prevent it.

**Fix (not this task's file, but nobody else is editing it):** gate the `self._resolver_hook(...)`
call at `integrator.py:987-990` on `TIER_MECHANICAL in self._spec.ladder`; when absent, treat every
path in `outcome.paths` as unresolved without calling the hook (mirroring how `escalate()`'s
pseudocode already gates T2/T3 on ladder membership). Needs a test asserting a conflict that a
union rule would otherwise resolve is instead left for escalation when `ladder` omits
`"mechanical"`. Flag for whoever owns `integrator.py`'s wiring next (`T-Ib5Qy9`'s owner, or
folded into `T-Lr6Ka3` since `escalate()` is the other half of ladder gating and is still Draft).

## Minor / Should-fix (non-blocking)

### C-4 — Two failure branches decline silently where their sibling branches log
**Location:** `resolvers.py:272-273` (`_merge_file_union`, `cp.returncode != 0`) and `:440-442`
(`RegenerateResolver.apply`, `cp.returncode != 0`).

Both branches return `None`/`False` with no `logger` call at all, immediately after (or before, in
the regenerate case) sibling branches for `TimeoutExpired`/`OSError` that *do* call
`logger.warning(EVENT_..., extra={...})`. A regenerate command that runs, exits non-zero (arguably
the single most common real failure mode — "the script rejected this input"), and gets silently
reduced to `unresolved` gives an operator debugging "why didn't `uv.lock` regenerate" nothing to go
on beyond the eventual T2/T3 escalation — no exit code, no `stderr` tail, even though `cp.stderr` is
already captured (`capture_output=True`) and sitting right there. This breaches "errors/logging:
never swallow" for what is, functionally, the primary error path of the whole `RegenerateResolver`.

**Fix:** add a `logger.warning` in both branches, e.g. for regenerate:
```python
logger.warning(EVENT_REGENERATE_ERROR, extra={
    "event": EVENT_REGENERATE_ERROR, "path": step.path, "resolver": RESOLVER_REGENERATE,
    "returncode": cp.returncode, "stderr_tail": cp.stderr[-4096:],
})
```
(4 KiB matches `GitError`'s own `stderr_tail` convention in `git.py`/`errors.py` — reuse that
constant rather than a new literal if one already exists). Mirror for `_merge_file_union`'s
`EVENT_MERGE_FILE_ERROR`.

### C-5 — Sibling-file scoping via mtime: a full worktree walk plus a narrow race
**Location:** `resolvers.py:320-338` (`_snapshot_mtimes`), used by `RegenerateResolver.apply`
(`:405, :445-449`).

`_snapshot_mtimes` does `root.rglob("*")` over the **entire worktree** twice per regenerate call
(before/after), which is O(all files in the repo) Python-side filesystem work for what git can
answer directly and more cheaply. It also has a narrow but real correctness edge: if the regenerate
command rewrites a sibling file with new content within the same `st_mtime_ns` tick as the
pre-checkout snapshot (coarse-clock filesystems, some CI/container overlay filesystems, or two
writes fast enough that the OS clock hasn't advanced), that sibling is invisible to the diff and
never staged — it sits with new, resolved-looking bytes on disk but the conflict markers still
recorded in the index, neither committed nor reported as unresolved by `apply_plan`'s re-query
(only `step.path` itself is unconditionally staged, `:448`, so this can't happen to the primary
target — only to an incidentally-touched sibling).

This is exactly the question this review's brief flagged: `GitRepo.status_porcelain(cwd)` (already
a locked M1 method, `git.py:879-884`) reports git's own worktree-vs-index diff, is unaffected by
timestamp granularity, and avoids the manual tree walk entirely. Suggest diffing
`{e.path for e in git.status_porcelain(worktree, untracked=False)}` before/after instead of
`_snapshot_mtimes`, still unioned with `step.path`. NFR-1-safe either way (paths/status codes only,
never content).

## Nits

### C-6 — Registry pluggability claim is narrower than the docstring states
`resolvers.py:165-167` says extending the ladder is "register + spec enum, no
`apply_plan`/`plan_resolution` core-code change." True for `apply_plan`'s dispatch (proven by
`TestRegistryExtension`). Not fully true for `plan_resolution`: its precedence branches
(already-resolved → regenerate → union → unresolved) are hardcoded, and `ResolutionStep.resolver`
is a closed `Literal["rerere", "union", "regenerate"]` (`:107`) — a genuinely new,
independently-triggered T1 technique (not just a new implementation behind an existing
regenerate/union trigger) still needs both a `plan_resolution` branch and a widened `Literal`. Not
a functional problem today (T1 has exactly three fixed techniques per the locked HLD), but the
docstring should say "extending what `union`/`regenerate` *do*" rather than implying arbitrary new
precedence categories are zero-core-change, so a future contributor doesn't over-trust the claim.

### C-7 — STATUS.md's coverage characterization is slightly off
STATUS.md says the 10 uncovered statements are "all defensive subprocess-exception branches."
Confirmed by `pytest --cov`: 267-271, 273, 307, 428-439 fit that description, but line 526
(`if not conflicted_paths: return []` in `resolve_mechanically`) is a trivial empty-input guard, not
a subprocess-exception path. Cosmetic — a one-line `assert resolve_mechanically([], ...) == []`
test would close it and make the coverage narrative accurate.

### C-8 — STATUS.md misattributes the reason `git.py` wasn't touched
STATUS.md's "Interface change requests" section says the local subprocess helpers exist "per the
ticket's explicit instruction not to edit `git.py`." `TASK.md`'s "Do NOT touch" list names only
`integrator.py`, `engine.py`, `models.py`, `cli.py` — `git.py` is not on it (only referenced as an
upstream dependency: *"Upstream: `T-Gt4Pw8` (git API + fixtures)"*). Not touching `git.py` was
still the right call given `T-Wl2Bq7`'s concurrent edits to the same file, but the STATUS.md
citation is inaccurate and worth a one-line correction so a future reader doesn't go looking for a
"do not touch git.py" instruction that isn't there.

## Dimensions walked with nothing further to flag
- **SOLID/KISS:** `MechanicalResolver` ABC + registry is the right amount of abstraction for exactly
  three techniques with a documented extension seam; no over-engineering.
- **DRY:** no meaningful duplication found beyond C-4's two near-identical silent-decline branches
  (noted there, not a separate finding).
- **Magic literals:** none — every threshold/status string is a named constant, timeouts are either
  spec-configurable (`rule.timeout_seconds`) or a named module constant
  (`MERGE_FILE_TIMEOUT_SECONDS`).
- **Determinism:** `plan_resolution` is proven pure and order-independent
  (`test_deterministic_regardless_of_input_order`); union ordering/dedup is delegated to git's own
  `merge-file --union`, tested for stability across two independent re-derivations.
- **Resume/idempotency:** `apply_plan`'s re-query (never trusting its own bookkeeping) plus the
  `checkout --merge` restore-on-failure fix mean a path is always left either fully resolved+staged
  or fully original — no partially-applied state for T2/T3 to inherit. This is a genuine strength.
- **NFR-1:** enforced by an AST-grep test (`TestNoDirectFileReads`), not a comment — correct choice
  per the task's own instruction.

## Testing notes
- **Mock/inject:** `git: GitRepo` and `env` are already the injection seams used throughout; no
  changes needed there. The recommended C-1 fix keeps the same seam.
- **Integration-test:** C-2's and C-3's fixes both need a real-`Integrator` end-to-end test (not a
  `resolvers.py`-local one) since the bug is specifically in how `Integrator` constructs/gates
  around `GitRepo`/`resolver_hook` — a `resolvers.py`-only test cannot see it, as demonstrated by
  this task's existing (correct, but insufficient) rerere tests.
- **Coverage gaps:** the 10 uncovered lines in `resolvers.py` are acceptable as-is except line 526
  (C-7, trivial to close). Not force-covering the `TimeoutExpired`/`OSError`/`merged is None`
  defensive branches via mocked subprocess, per the task's own "real fixtures only" instruction, is
  a reasonable tradeoff — those are genuinely narrow belt-and-suspenders paths already guarded by
  earlier deterministic checks.

## Pre-submit checklist
- [x] Review scope confirmed: exactly the files named in the brief (`resolvers.py`, its test file,
  this task's ticket docs); `T-Wl2Bq7`'s uncommitted work was read only where needed for the C-1
  coordination note, not reviewed.
- [x] Three-level alignment checked: project goals (deterministic/idempotent/resumable — strong;
  pluggable — strong at the apply layer, overclaimed at the plan layer, C-6; safe-by-default — C-1
  gap), epic/task ACs (all 10 ACs including S-5/S-6 amendments verified against real tests; ticket
  read), code-level intent (docstrings/tests match what the code does, including the two honestly
  disclosed gaps).
- [x] All review dimensions walked (SOLID/KISS, DRY, magic literals, pluggability, spec/DAG
  correctness, determinism/resume safety, errors/logging, testability, concurrency/rollout) — see
  findings above plus the "nothing further to flag" list.
- [x] Every finding cites file:line, evidence (grep output, code excerpt, or a cited HLD/TASK.md
  line), why it matters, and a concrete next step.
- [x] Findings bucketed by actual merge-blocking severity: C-1 is a confirmed architectural
  boundary breach with a real (if narrow) exploit-surface argument → Blocking. C-2/C-3 are real,
  confirmed, production-breaking gaps in a documented safety control, but live entirely outside this
  task's owned files → Major, flagged for repair, not scored against this task's own ACs. C-4/C-5 →
  should-fix. C-6/C-7/C-8 → nits.
- [x] Testing notes included above.
- [x] No source/test edits made; no commits made.
