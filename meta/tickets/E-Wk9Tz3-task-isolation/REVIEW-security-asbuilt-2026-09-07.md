# Security review (as-built) — E-Wk9Tz3-task-isolation

- Reviewer: dev-security (agent)
- Date: 2026-09-07
- Branch: `ad/task-isolation`
- Baseline: [`REVIEW-security-design-2026-09-07.md`](REVIEW-security-design-2026-09-07.md) (design-time,
  findings S-1..S-11). This review checks whether the shipped code honours those controls and looks for
  what the implementation newly introduced.
- Scope: `src/agent_orchestrator/isolation/**`, `engine.py` isolation paths, `executors/claude_cli.py`
  env/tool overlay, `templates/builtin/instructions/merge-resolve.md`, `ao prune`.
- Method: code read **plus** executable attack scripts. Every finding tagged **[PROVEN]** was reproduced
  against real `git` / the real modules; findings tagged **[READ]** are code-read only. No file under
  `src/` or `tests/` was modified. No commits made.

---

## Verdict

**Do not merge as-is.** The architecture is sound and most design-time controls landed correctly (S-1,
S-4, S-6, S-9, NFR-1, the CAS/lock discipline, and the ref/path sanitizer are all genuinely present and
were verified working). But the **single control the design called blocking — S-2, structural containment
of the T2 LLM resolver — is not effective in the shipped code.** Both of its halves fail: the forced
`disallowed_tools` union is silently discarded by the executor, and `resolver_deny_push` does not deny
push. On top of that the resolver retains `Bash` and therefore full write access to the shared `.git`
directory, which yields a complete prompt-injection → persistent-host-compromise chain.

There is also one **non-security-but-critical data-loss defect** (C-4) that destroys live task work on
resume, which a reviewer should treat as merge-blocking regardless of this review's remit.

**Must fix before merge:** C-1, C-2, C-3, C-4, H-1, H-2, H-3.
**Should fix before merge or ship behind an explicit off-by-default flag:** H-4, M-1, M-3.

---

## Design-time controls: confirmed / weakened / missing

| Control | Status | Evidence |
|---|---|---|
| **S-1** engine git calls fire no repo-local hook | **CONFIRMED [PROVEN]** | Planted 6 hooks in a fixture repo. A plain `git commit` fired 8 hook invocations; `GitRepo.worktree_add` + `add_all` + `commit` fired **zero**. `-c core.hooksPath=<empty 0700 dir>` composed in `_run`; `resolve_empty_hooks_dir` refuses a non-empty dir. |
| **S-1** (sibling gap) `.gitattributes` filters / merge drivers | **MISSING [PROVEN]** | Same fixture: engine `worktree_add` ran a `filter.evil.smudge` command; `add_all` ran `clean` 4×. See **M-1**. |
| **S-2(a)** forced `disallowed_tools` union on the T2 resolver | **DEFEATED [PROVEN]** | Union lands on the `AgentSpec` and the engine correctly dispatches `dispatch.agents` — but `_ensure_disallowed_tools` drops it. See **C-1**. |
| **S-2(b)** `resolver_deny_push` neutralizes the push path | **DEFEATED [PROVEN]** | Only https remotes are blocked. See **C-2**. |
| **S-2(c)** resolver tool policy documented | Present | `merge-resolve.md` is well written and explicitly frames conflict content as untrusted — but asserts a containment guarantee that does not exist (**C-2**). |
| **S-3** commit denylist backstop | **PARTIAL [PROVEN]** | Implemented well (untracked-only, default `fail`, path+basename match) but bypassed by untracked directories, and absent entirely on the resume/resolver path. See **H-3**. |
| **S-4** `IsolatedArtifactView` is per-task, not registry-wide | **CONFIRMED [PROVEN]** | `view.py` builds `_roots` from `task_isolation.repos` only; both construction sites (`engine.py:965`, `engine.py:2674`) pass the settling task's own `TaskIsolation`. Traversal, `/etc/passwd`, cross-task absolute paths and in-worktree symlink escapes all rejected. Two caveats: **H-2** (the guard is applied to the wrong path on the copy-back) and **M-4**. |
| **S-5** rerere tier visibility | Present [READ] | `RunIntegrationState.tier_counts`, `tier_reached`. Note the cache is now demonstrably *attacker-writable* — see **C-3**. |
| **S-6** `regenerate[].timeout_seconds` | **CONFIRMED [READ]** | `RegenerateRule.timeout_seconds` (default 120s), passed explicitly at `resolvers.py:363`. argv list, no shell, `stdin=DEVNULL`. |
| **S-7** retained-worktree disk cap | Not implemented [READ] | No run-level cap or `worktree.retention_high` warning. Tracked, non-blocking. |
| **S-8** reserved `ao/` namespace, hard error on collision | **PARTIAL [READ]** | `ensure()` raises `WorktreeCollisionError` correctly, but V9 checks the **raw** id while the collision is on the sanitized one; the only backstop is an `assert`. See **L-4**. |
| **S-9** 0700 worktree dirs | **CONFIRMED, non-uniform [READ]** | `_mkdir_0700` chmods every level it creates (`worktrees.py:198-214`), plus the git-created leaf. But `runlock.py:270,330` creates `$AO_STATE_DIR/runlocks/` with no mode. |
| **S-10** verify capture size cap | **CONFIRMED [READ]** | `VERIFY_CAPTURE_CAP_BYTES = 1 MiB` per stream. |
| **S-11** `git worktree prune` scope | **PARTIAL [READ]** | Scoped in the normal path, but `git.py:750-753` falls back to a repo-**global** `git worktree prune` when no unlocked foreign prunable entry exists, contradicting `worktrees.py`'s own "never a global prune" claim. See **L-5**. |
| **NFR-1** conflicted file *contents* never logged | **CONFIRMED [READ]** | No log call in `integrator.py`/`engine.py`/`worktrees.py` carries file bodies; `conflict-<n>.json` is ids/paths/refs/bools only. Two documented content-bearing **artifacts** (not logs): `verify.stdout/stderr` (capped) and `previous-<n>.patch` (uncapped, **L-6**). |
| No `eval`/`exec`/`shell=True` anywhere in the subsystem | **CONFIRMED [READ]** | Every user-supplied command is an argv list. |
| Ref/path sanitizer blocks traversal + ref injection | **CONFIRMED [PROVEN]** | 17 hostile ids (`../../../../etc`, `$(id)`, `` `id` ``, `x\nrefs/heads/main`, `--upload-pack=x`, `\x00evil`, 200-char) — none escaped the worktree prefix or produced an option-like/injected ref. Hash-shortening above 80 chars works. But it is **not injective** — see **M-2**. |
| CAS retry bounded | **CONFIRMED [READ]** | `CAS_RETRY_BOUND = 1`, unconditional increment, hard return on exhaustion. Cannot spin. |
| Lock release on exception paths | **CONFIRMED [READ]** | `ExitStack` + `stack.callback(lock.release)`, handlers outside the `with`. |

---

## CRITICAL

### C-1 — The S-2(a) forced `disallowed_tools` union never reaches the resolver's CLI when the agent sets any tool-policy flag of its own **[PROVEN]**

- **Location**: `src/agent_orchestrator/executors/claude_cli.py:136-152` (`_ensure_disallowed_tools`) and
  `:132-134` (`_has_tool_policy`); call site `:459`. Interacts with
  `src/agent_orchestrator/isolation/escalation.py:161-167` (`resolver_agent_spec`).
- **Attack**: The workflow names a `merge-resolver` agent whose `AgentSpec` carries
  `extra_args: ["--allowedTools", "Read,Edit,Bash,WebFetch"]` (or *any* `--disallowedTools`/`--tools`, in
  either spelling or the `=` form, in `extra_args` **or** `command_template`). `resolver_agent_spec`
  dutifully unions `spec.resolver_disallowed_tools` into `AgentSpec.disallowed_tools`, the engine
  correctly dispatches `dispatch.agents` — and then the executor sees a tool-policy flag already in argv
  and **skips injection entirely**. The union is computed, stored, and thrown away.
- **Evidence**: `scratchpad/exp1_union_bypass.py`. Benign agent → argv ends
  `['--disallowedTools', 'WebFetch', 'WebSearch']`. With `extra_args=["--allowedTools", "...,WebFetch"]`
  → argv is `['claude', '-p', '--allowedTools', 'Read,Edit,Bash,WebFetch,WebSearch']`, i.e. **WebFetch
  explicitly re-enabled**, `disallowed_tools` on the model still reading `['WebFetch','WebSearch']`.
  Same result for `--disallowedTools Glob` in `extra_args` and for `--allowed-tools=WebFetch` in
  `command_template`.
- **Why this is exactly the hole S-2 existed to close**: the design's own words are "the union wins
  regardless of what the agent declares, so a misconfigured (or malicious) resolver `AgentSpec` can never
  reopen the hole this exists to close." It can, via a field one level over.
- **Fix**: `_ensure_disallowed_tools`'s "explicit flag wins" rule is correct for ordinary agents and must
  not change. Add a **separate, non-overridable** path for forced tools: give `AgentSpec` a
  `forced_disallowed_tools` (or thread a `forced: tuple[str,...]` through `ExecutionContext`) that is
  appended to argv **unconditionally**, after any agent-supplied policy flag, and have
  `resolver_agent_spec` populate that field instead of `disallowed_tools`. `--disallowedTools` is
  additive on top of `--allowedTools` in the CLI, so a trailing forced list is honoured. Add a test that
  asserts the forced set survives each of the five `_TOOL_POLICY_FLAGS` spellings.

### C-2 — `resolver_deny_push` does not deny push: only https remotes are blocked, and any shell-out undoes it in one token **[PROVEN]**

- **Location**: `src/agent_orchestrator/isolation/escalation.py:169-186` (`resolver_env`);
  `templates/builtin/instructions/merge-resolve.md` rule 6.
- **Attack**: `resolver_env` sets `GIT_CONFIG_COUNT=2` with `credential.helper=""` and
  `http.proxy=127.0.0.1:1`, plus `GIT_ASKPASS=/bin/false`. A hijacked resolver with `Bash` pushes anyway.
- **Evidence**: `scratchpad/exp2_push.sh` and `exp3_ssh.sh`, run under the **exact** env `resolver_env`
  produces:

  | Transport / evasion | Result |
  |---|---|
  | `git push` to a **local-path** remote (`/path/to/remote.git`) | **SUCCEEDED** |
  | `git push` to a **`file://`** remote | **SUCCEEDED** |
  | `git push` to an **`ssh://`** remote (the common real-world case) | **SUCCEEDED** |
  | `env -u GIT_CONFIG_COUNT git push` (https) | **SUCCEEDED** |
  | `git -c http.proxy= push` (https; command-line `-c` beats `GIT_CONFIG_*`) | **SUCCEEDED** |
  | `git push` to an **https** remote, no evasion | blocked (proxy 127.0.0.1:1) |

  So the control covers exactly one transport and is undone by a single `env -u` / `-c`.
- **Compounding**: `merge-resolve.md` rule 6 tells the model *"You do not have push access from this
  worktree for the duration of this dispatch."* That is false. Prompt text asserting a guarantee the
  implementation does not provide is worse than silence — it discourages the reader from adding the real
  control.
- **Fix**: env vars cannot contain a child process that can call `env`. Real options, in order of
  strength: (a) **remove the remote for the duration** — `git remote remove origin` in the resolver's
  worktree is not possible (remotes are per-repo, not per-worktree), so instead run the resolver with
  `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM` pointed at a scratch file **and** accept that this is still
  evadable; (b) **deny `Bash` to the resolver** (see C-3) — this is the only structural fix, and it
  removes the push vector along with everything else; (c) if `Bash` must stay, drop the false claim from
  `merge-resolve.md` and document `resolver_deny_push` honestly as "best-effort, https only".
  Recommend (b). Also add `GIT_SSH_COMMAND=/bin/false` and `core.sshCommand` to `resolver_env` as
  defence in depth, and stop hardcoding `GIT_CONFIG_COUNT=2` (it silently clobbers an operator's own
  exported `GIT_CONFIG_*` pairs).

### C-3 — The T2 resolver keeps `Bash` and therefore has full write access to the shared `.git`: it can rewrite the integration ref, poison `rr-cache` across runs, and plant a hook in the operator's main checkout **[PROVEN]**

- **Location**: `src/agent_orchestrator/models.py:69`
  (`DEFAULT_RESOLVER_DISALLOWED_TOOLS = ["WebFetch", "WebSearch"]`);
  `escalation.py:161-167`; `merge-resolve.md` rule 4 (which *requires* the model to run `git add`).
- **Attack chain**: a crafted line inside a conflict hunk (a comment, a string literal, a lockfile entry —
  content neither task author reviewed) prompt-injects the resolver. The resolver runs `Bash`. A linked
  worktree shares the main repo's object database, refs, config and hook directory via `--git-common-dir`.
- **Evidence**: `scratchpad/exp8_refs.sh`, executed from inside a linked worktree under the full
  `resolver_env`:

  | Action | Result |
  |---|---|
  | `git update-ref refs/heads/ao/run1/integration <sha>` | **SUCCEEDED** — the run's integration branch rewritten directly. The `Integrator`'s CAS protects against a concurrent *engine* writer; it does nothing against a worktree-resident agent. |
  | write `.git/rr-cache/<hash>/preimage` | **SUCCEEDED** — and `rerere` is on by default (`GitRepo(rerere=True)`, `IntegrationSpec.rerere=True`, `RERERE_ARGS` sets `rerere.autoupdate=true`), so a poisoned resolution is **auto-replayed and auto-staged in every future run** at the free T1 tier. This is design finding S-5's mechanism, now shown to be attacker-writable rather than merely fallible. |
  | write `.git/hooks/pre-commit` in the **main checkout** | **SUCCEEDED** — persists after the run ends, outside every worktree, and fires on the **operator's own** future interactive commits. S-1 protects the engine's git calls; it does not protect the human. |
  | `git config --local alias.x '!id'` | **SUCCEEDED** — a shell alias in the user's repo config, executed on their next `git x`. |
  | `git branch -f main <sha>` | blocked — only because `main` was checked out. Any **non-checked-out** user branch is force-movable. |

  Every one of the brief's threat-1 questions ("can it reach the integration ref, escape the worktree,
  persist anything outside it") answers **yes**.
- **Fix**: add `Bash` (and `Task`, to stop subagent spawn from laundering around the tool policy) to
  `DEFAULT_RESOLVER_DISALLOWED_TOOLS`, and have the **engine** stage the resolved paths instead of the
  model. `GitRepo.add_paths` already exists and `conflicted_paths` is already known to the caller, so
  `merge-resolve.md` rule 4 becomes "edit the files and stop; the engine stages them" — a strictly
  simpler contract. This is the one change that closes C-2, C-3 and the exfiltration half of C-1 at once,
  and it costs the resolver nothing it actually needs. It depends on C-1 being fixed first, or the
  denial is again droppable.

### C-4 — `reconcile()` compares *sanitized* worktree/ref components against *raw* task ids, and force-deletes the live worktree and branch of any task whose id is not sanitize-identity **[PROVEN]**

- **Location**: `src/agent_orchestrator/isolation/worktrees.py:479` (`task_id = rel.split(os.sep)[0]`) and
  `:509` (`task_component = ref_name[len(ref_prefix):]`), both compared against `known_task_ids`.
  Callers pass **raw** ids: `engine.py:911` (run end) and `engine.py:2585` (**resume**).
- **Attack / misconfig path**: any task id containing a character outside `[A-Za-z0-9._-]` — `:`, `/`, a
  space, most punctuation. `TaskSpec.id` is a bare `str` with no pydantic pattern; the
  `^[a-z0-9][a-z0-9-_]*$` rule lives only in `specs/workflow.schema.json`, which is not packaged into the
  wheel and silently no-ops when absent; `read_task_manifest` validates no id at all, so an
  agent-emitted task manifest can supply one freely.
- **Evidence**: `scratchpad/exp10_reconcile.py`, against real `git` and the real `WorktreeManager`.
  Two live worktrees created for declared tasks `build:web` and `plain-task`, each holding uncommitted
  work. `reconcile({"build:web", "plain-task"})` — i.e. **both ids are known and current**:

  ```
  removed_worktrees: [.../run1/build-web/repo-x]
  deleted_refs     : ['refs/heads/ao/run1/build-web']
    'build:web' : worktree still on disk = False   branch still exists = False
    'plain-task': worktree still on disk = True    branch still exists = True
  ```

  `worktree_remove(force=True)` discards the uncommitted work; `delete_ref` removes the only remaining
  pointer to it. On **resume** this happens *before the task runs*, so a resumed run silently loses the
  completed-but-unlanded work of every such task.
- **Fix**: compare like with like — build
  `known_components = {paths.sanitize_ref_component(t) for t in known_task_ids}` once and test against
  that, at both `:479` and `:509`. Because the sanitizer is non-injective (M-2), the safe form is a
  map from sanitized component back to the raw ids that produced it. Add a regression test with an id
  that requires sanitizing.

---

## HIGH

### H-1 — On resume, `state.integration.branch` is trusted verbatim from `state.json`, which an isolated task can write; the CAS then lands task squashes on any ref, including `refs/heads/main` **[PROVEN, partial]**

- **Location**: `engine.py:1030-1031` (activation runs only `if not state.integration.active`, so a
  resume — where `active` is already `True` in `state.json` — **skips** the safe re-derivation at
  `engine.py:2599` `branch = isolation_paths.integration_branch(state.run_id)`), `engine.py:1058-1062`,
  then `integrator.py:673` / `:1096` `ref = f"refs/heads/{run_integration.branch}"` →
  `git.update_ref_cas(ref, ...)`. Model: `models.py:763` `branch: str | None = None`, no constraint.
- **Attack**: `paths.RESERVED_SHARED_PREFIXES` deliberately keeps `.orchestrator/` **shared and
  writable** from inside an isolated task (correct for run bookkeeping). A task's agent writes
  `<ws>/.orchestrator/runs/<run>/state.json` with `integration.branch: "main"`. The operator resumes.
  Every subsequent task squash CAS-lands onto `refs/heads/main` — the CAS succeeds, because `expected_old`
  is read from that same ref at `integrator.py:1044`.
- **Evidence**: `RunIntegrationState.model_validate({'active':True,'branch':'main'})` is accepted;
  the resulting CAS target is `refs/heads/main` (`'../../../evil'` yields `refs/heads/../../../evil`).
  Verified by execution. The activation-skip on resume and the ref f-strings are read-verified.
- **Fix**: re-derive rather than trust — call `isolation_paths.integration_branch(state.run_id)` on the
  resume path too and assert it equals the persisted value (fail the run on mismatch), **or** validate at
  the CAS sites that the ref starts with `refs/heads/{AO_REF_NAMESPACE}/`. The latter is one line and
  covers both entry points. Separately, consider whether `state.json` should be integrity-checked at all
  given tasks can write it.

### H-2 — Untracked-output copy-back dereferences symlinks, exfiltrating arbitrary host files into the shared workspace **[PROVEN]**

- **Location**: `engine.py:2644-2684` (`_copy_untracked_outputs`) — `src = view.resolve(output)`,
  `dst = base.resolve(output)`, `shutil.copy2(src, dst)`. Reached when `integration.untracked_outputs`
  is `"copy"` (**the default**, `models.py:476`).
- **Attack**: an isolated task declares a gitignored output (`build/report.txt`) and, instead of writing
  it, makes it a symlink to `~/.ssh/id_rsa` / `~/.aws/credentials` / a sibling repo's `.env`.
- **Why the S-4 guard misses it**: `view.resolve("repo/build/report.txt")` resolves the **workspace**
  path (where there is no symlink), then `paths.effective_path` remaps it into the worktree **lexically**
  — `paths.py:150-153` is explicit that this module does no filesystem I/O. So the returned `src` is
  never realpath'd, and `shutil.copy2` follows symlinks by default. The containment check is applied to
  the wrong path.
- **Evidence**: `scratchpad/exp11_symlink_out.py`, real git + real `WorktreeManager`/`IsolatedArtifactView`:
  `git ls-files --others --ignored --exclude-standard` **does** list the symlink, `view.resolve` **allows**
  it, and the copy lands the secret's **content** at `<workspace>/repo/build/report.txt`
  (`islink(dst) = False`, body = the private key). From there it is an ordinary artifact readable by
  every downstream task and sweepable by a later `git add -A`.
- **Fix**: in `_copy_untracked_outputs`, reject any `src` where `os.path.realpath(src)` is not inside the
  task's own worktree root (`os.path.lexists` + `islink` check, or `Path(src).resolve()` re-containment),
  and pass `follow_symlinks=False` to `copy2` so a legitimate symlink output is copied as a link rather
  than dereferenced. Raise/skip with an event (`integration.untracked_output_symlink`) rather than
  silently copying.

### H-3 — The S-3 commit denylist is bypassed by any secret inside a newly untracked directory, and is absent entirely on the resume/resolver path **[PROVEN]**

- **Location**: `integrator.py:910-912` (the only screen call site in the codebase) reading
  `git.status_porcelain(wt, untracked=True)` → `git.py:880-885`, which runs
  `git status --porcelain -z` with **no `-uall`**.
- **Attack**: git's default `--untracked-files=normal` collapses a wholly-untracked directory to one
  entry. `config/.env` is reported as `config/`, which matches no denylist glob (its basename is `""`),
  so the screen passes and `git add -A` commits the secret onto the task branch → integration branch →
  the fast-forwarded main checkout → a later sanctioned `git push` by an ordinary task.
- **Evidence**: `scratchpad/exp7_denylist.py`, real git:

  ```
  what the screen SEES: ['.env', 'certs/', 'config/', 'deploy/']
  denylist HITS       : ['.env']
  actually COMMITTED  : ['.env', 'certs/server.pem', 'config/.env', 'deploy/id_rsa']
  >>> swept in UNDETECTED: ['certs/server.pem', 'config/.env', 'deploy/id_rsa']
  ```

  The root-level `.env` is caught; the three nested ones are not. `_matches_denylist`'s own docstring
  claims "`commit_denylist: ['.env']` also catches `config/.env`" — true of the matcher in isolation,
  false of the system, because the caller never gives it the filename.
- **Second bypass, same finding**: `integrator.py:526` and `:613` (the `resume_integration` /
  post-resolver restage paths) call `git.add_all(wt)` with **no denylist screen at all and no
  `auto_commit` gate**. These are precisely the paths that sweep whatever the **T2 resolver agent** just
  wrote into the worktree — the least-trusted writer in the system.
- **Third, minor**: `fnmatchcase` is case-sensitive, so `.ENV` / `My.PEM` pass.
- **Fix**: (a) add an `untracked_mode` parameter to `status_porcelain` and pass
  `--untracked-files=all` from the screen (or screen via the existing
  `ls_files_untracked_ignored`-style `git ls-files -o --exclude-standard`, which lists files
  individually); (b) factor the screen into a helper and call it before **every** `add_all`, including
  `:526` and `:613`; (c) lowercase both sides, or use `fnmatch.fnmatch`, for the basename comparison.

### H-4 — `ao prune` has no active-run guard and force-removes retained failed-task worktrees by default **[READ]**

- **Location**: `cli.py:1887-1973` (`prune`), `worktrees.py:544-600` (`gc_run`).
- **Risk**: `--worktrees` defaults **on**, so `ao prune` now `shutil.rmtree`s the run dir *and*
  `worktree_remove(force=True)`s every worktree under that run's prefix *and* deletes its
  `ao/<run>/*` branch and squash refs. `--force` on `git worktree remove` discards uncommitted and
  untracked content. `keep_worktrees: on_failure` retains failed tasks' worktrees **precisely so the
  operator can recover that work**; a habitual `ao prune` (the command's own docstring invites
  `docker system prune`-style periodic use) destroys it with no confirmation and no unmerged check.
  `--older-than 0` makes the cutoff `now`, so *every* run dir qualifies.
  Separately, `prune` never consults `WorkspaceRunLock`, so a prune concurrent with a live run deletes
  that run's state dir and force-removes its live worktrees mid-task.
- **Fix**: (a) check `WorkspaceRunLock.is_held_by_other` (or the presence of a live runlock payload) and
  refuse to prune a run whose lock is held, unless `--force`; (b) before deleting a task branch, check
  `git.is_ancestor(branch_tip, integration_head)` and skip-with-warning when the branch holds unlanded
  commits, unless `--force`; (c) make `--worktrees` opt-in, or at minimum require confirmation when any
  candidate worktree is dirty.

---

## MEDIUM

### M-1 — S-1 neutralizes hooks but not `.gitattributes`-driven `filter`/`merge` drivers; engine git calls remain a per-task execution amplifier **[PROVEN]**

- **Location**: `git.py:84-92` (`SAFETY_ARGS` — only `commit.gpgsign`, `core.editor`, `gc.auto`) and
  `:542-543` (the hooks `-c`). No `core.attributesFile` override, no `filter.*`/`merge.*` neutralization.
- **Attack**: this is the *same* threat model S-1 was written for — a repo the operator bootstrapped
  once. `git-lfs`, `git-crypt`, `nbstripout` and friends all install `filter.<name>.clean/.smudge`
  **commands into git config** and a `filter=` attribute into a **tracked** `.gitattributes`. The engine
  then executes those commands automatically, per task, unattended, concurrently across worktrees —
  exactly the amplifier S-1 exists to prevent.
- **Evidence**: `scratchpad/exp4b.py` — a bootstrapped fixture repo with both local hooks and a local
  `filter.evil` config:
  ```
  after ENGINE worktree_add:   hooks fired = []  | filters ran = ['SMUDGE']
  after ENGINE add_all+commit: hooks fired = []  | filters ran = ['SMUDGE','CLEAN','CLEAN','CLEAN','CLEAN']
  ```
  Hook suppression works perfectly; filters run freely. `git rebase --onto` additionally honours a
  `.gitattributes` `merge=<driver>` attribute, executing `merge.<driver>.driver` from config.
- **Caveat (stated honestly)**: the *command* always comes from git config, never from tracked content
  alone, so a bare `git clone` cannot inject one. The exposure is the bootstrapped-repo case — which is
  the case S-1 itself cites.
- **Fix**: add to `SAFETY_ARGS` (or to the composed per-invocation flags):
  `-c core.attributesFile=/dev/null` and, for the sweep/checkout calls specifically, disable filtering
  where git supports it. A cheaper, well-targeted alternative: leave the filters alone (they may be
  load-bearing for the repo's correctness) but **document** in `conflict-friendly-coding.md` that the
  engine runs the repo's configured filters/merge drivers once per task, so a repo with a hostile or
  expensive filter must not be run under isolation. Recommend documenting + a `NO_ATTRIBUTES_ARGS`
  constant behind an `integration.neutralize_attributes` opt-in.

### M-2 — `sanitize_ref_component` is not injective below 80 chars: two distinct task ids silently share one worktree and one branch **[PROVEN]**

- **Location**: `paths.py:79-107`. The `_TRUNCATION_HASH_LEN` disambiguator fires only above the 80-char
  bound; below it, character substitution collides freely.
- **Evidence**: `scratchpad/exp10_reconcile.py` — `ensure("svc/api", ...)` and `ensure("svc-api", ...)`
  return the **identical** `worktree_root` and the **identical** `branch` (`ao/run1/svc-api`), and the
  second call takes the "reuse" branch at `worktrees.py:313-314`. Two supposedly isolated tasks then run
  in one worktree on one branch. `_inject`'s duplicate check (`engine.py:3846-3851`) is exact-string, so
  both ids are accepted. Also observed: `../../../../etc` and `etc` both → `etc`; `$(id)` and `` `id` ``
  both → `id`.
- **Reachability**: agent-emitted task manifests, which the module's own comments already name as the
  threat model.
- **Fix**: always append the short hash of the **original** value (not only above the length bound), or
  add a validation rule rejecting a workflow/manifest whose task ids do not sanitize injectively.
  The first is a two-line change and also fixes C-4's map cleanly.

### M-3 — `AO_WORKTREE_ROOT` pointed inside the workspace root silently defeats S-4 **[PROVEN]**

- **Location**: `paths.py:171-181` (`worktree_root_prefix_for`) — `os.environ.get("AO_WORKTREE_ROOT")` is
  used with **no validation whatsoever**.
- **Attack/misconfig**: an operator sets `AO_WORKTREE_ROOT=<workspace>/.worktrees` (a natural thing to do
  — keeps everything in one tree, survives a `$HOME` on a small volume). Now every task's worktree lies
  *under the shared workspace root*, so `IsolatedArtifactView.resolve` takes its **first** branch
  (`full.startswith(base_root)`) and passes the path to `effective_path`, which does not recognise task
  B's worktree as any of task A's repo toplevels and returns it **unchanged**.
- **Evidence**: `scratchpad/exp5_view.py`. With worktrees outside the workspace (the default), task A's
  view rejects an absolute path into task B's worktree. With `AO_WORKTREE_ROOT` inside the workspace,
  the same path is **ALLOWED**, both absolutely and as the relative `.worktrees/B/repo/secret.txt`.
- **Fix**: reject at resolution time — if the resolved worktree prefix is inside the workspace root,
  raise with a clear message (or add the prefix to `RESERVED_SHARED_PREFIXES`-style exclusion). One
  `startswith` check in `worktree_root_prefix_for`, plus a note in the config docs.

### M-4 — `IsolatedArtifactView` mixes realpath'd and normpath'd roots; a symlinked `$AO_STATE_DIR` fails every isolated artifact resolve **[PROVEN]**

- **Location**: `view.py:53-55` builds `self._roots` with `os.path.normpath`, while
  `artifacts.py:69-81` (`resolve_unchecked`) returns a fully `Path.resolve()`d path.
- **Evidence**: `scratchpad/exp5_view.py` layout 3 — a worktree root reached through a symlinked state
  dir causes the task's **own** file to be **rejected** with `ArtifactPathError`.
- **Severity**: fail-closed, so not a bypass — but it is an availability break in a security control, and
  it is common in practice (`/var` → `/private/var`, a `~/.local/state` symlinked to another volume, any
  `TMPDIR` under a symlink). `paths.effective_path` has the same mismatch and there fails **open**
  (silently declining to remap, so an "isolated" task reads the shared checkout).
- **Fix**: resolve consistently — `str(Path(r.worktree_root).resolve())` for `_roots`, and realpath
  `repo.toplevel` when `RepoIsolation` is constructed in `worktrees.py` so `effective_path`'s lexical
  comparison operates on already-canonical values.

### M-5 — `verify_command` and `regenerate[].command` inherit the full parent environment and the parent's stdin **[READ]**

- **Location**: `integrator.py:395-401` (`_base_env` = `{**os.environ, AO_RUN_ID, AO_TASK_ID,
  AO_ATTEMPT}`), `:306-313` (default exec runner), `resolvers.py:359-367`.
- **Risk**: every secret in the orchestrator's environment (`ANTHROPIC_API_KEY`, `GITHUB_TOKEN`,
  `SSH_AUTH_SOCK`) reaches these workflow-author-supplied commands, whose stdout/stderr are then written
  into the run dir (`verify.stdout`, 1 MiB cap). This matches design finding S-10's "known limitation"
  framing and the rest of the system's posture, so it is not new — but note two deltas: the verify
  command does **not** get `git.py`'s `_FORCED_ENV` hardening, and unlike `resolvers.py:366` it does
  **not** pass `stdin=subprocess.DEVNULL`, so a verify command can block forever on the operator's
  terminal while holding every repo lock.
- **Fix**: add `stdin=subprocess.DEVNULL` to `_default_exec_runner` (one line, removes the lock-holding
  hang). Consider an `integration.verify_env` allowlist as a follow-up.

---

## LOW / hygiene

- **L-1 — `_run`'s forbidden-verb guard has three bypasses [PROVEN]** (`git.py:245-247, 286-298`).
  `scratchpad/exp6_guard.py`: `["--namespace","n","push"]` and `["--work-tree","/x","push"]` both **pass**
  (these are global options *with a value*, missing from `_GLOBAL_OPTIONS_WITH_VALUE`, so the value token
  is mistaken for the subcommand); `["--config-env=alias.p=EVIL","p"]` passes (`_forbidden_alias_key`
  inspects only `-c`); and a caller-supplied `-c core.hooksPath=/tmp/evil` is appended **after** the S-1
  one, where git's last-wins rule re-enables hooks. No current call site passes untrusted argv, so this
  is defence-in-depth only — but the module docstring advertises the guard as structural.
  *Fix*: add `--work-tree`, `--namespace`, `--exec-path`, `--super-prefix` to
  `_GLOBAL_OPTIONS_WITH_VALUE`; extend the alias check to `--config-env`; and reject any caller-supplied
  `-c core.hooksPath=`.
- **L-2 — runlock hygiene [READ]** (`runlock.py:270, 330; locks.py:124`). `$AO_STATE_DIR/runlocks/` is
  created with no mode (S-9 is otherwise uniform); neither `os.open` uses `O_NOFOLLOW`, so on a shared
  host with a writable state dir a pre-planted symlink is followed into an `ftruncate`+`write`; and
  `release()` unlinks while another process may hold an fd on the inode, permitting two simultaneous
  "exclusive" holders. The **stale-lock takeover logic itself is correct** — `flock` is the sole
  authority and forged pid/boot_id content only changes a log line.
- **L-3 — `IntegrationLock` release leak [READ]** (`locks.py:142-150`): if `os.close` raises,
  `self._thread_held` is never cleared and the process-wide `threading.Lock` for that `common_dir` leaks
  permanently.
- **L-4 — V9 validates the raw id, and the only backstop is an `assert` [READ]** (`spec.py:462` vs
  `paths.py:124`). `-integration`, `.integration`, `integration.` all sanitize to `integration` and pass
  V9; the `assert` then fires — except under `python -O`/`PYTHONOPTIMIZE`, where it vanishes — and the
  resulting `AssertionError` is not caught at `engine.py:952` (which handles only
  `WorktreeCollisionError`), crashing the whole run rather than failing the task. *Fix*: check the
  sanitized value in V9 and replace the `assert` with a raised `WorktreeCollisionError`.
- **L-5 — global `git worktree prune` fallback [READ]** (`git.py:749-770`): when no *unlocked* foreign
  prunable entry exists, ao issues a repo-global `git worktree prune`, contradicting `worktrees.py`'s
  line-8 "never a global prune (R-6)" claim. Defensible (git skips locked worktrees) but the docstring
  should not claim otherwise.
- **L-6 — unbounded artifact/manifest reads [READ]**: `escalation.export_previous_patch`
  (`escalation.py:278-294`) captures a full `git diff` into memory and writes it uncapped to
  `previous-<n>.patch` — contrast the 1 MiB `_cap` on verify output. `hotspots.load_hotspots`
  (`hotspots.py:128`) and `artifacts.read_task_manifest` have no size cap, unlike `read_control`'s
  `MAX_CONTROL_FILE_BYTES`.
- **L-7 — `diff_check` docstring is inaccurate [READ]** (`git.py:1016-1033`): the returned lines contain
  the offending **source line verbatim** (`git diff --check` echoes it), so the docstring's "no file
  content is read to produce them" is wrong. The value is used only for truthiness at
  `integrator.py:1370` and is never logged or persisted, so NFR-1 holds in practice — fix the docstring.
- **L-8 — S-2 skipped on the fallback path [READ]** (`engine.py:3387-3389`): when
  `spec.resolver_agent` is unset or unknown, `_prepare_resolver_dispatch` returns the *original* task,
  agents and env while reporting `materialized=True`, so the task's own agent is dispatched into a
  conflicted worktree with no forced tool policy and no `resolver_env`. `escalate()` never produces this
  state, so it is defensive-only — but it is a fail-**open** default; prefer failing the task.
- **L-9 — submodule support is advisory-only [READ]**: `IsolatedRepo.submodule` is set at
  `worktrees.py:142` and read nowhere; a parent repo containing submodules is warned about and then
  processed anyway (gitlinks swept by `add -A`). `git submodule` is correctly in
  `FORBIDDEN_SUBCOMMANDS`, and `.gitmodules` is never read — so there is no execution vector here, only
  a correctness gap.
- **L-10 — `_isolation_env` seam [READ]** (`engine.py:2258`): operator config can inject arbitrary
  key/value pairs into the agent environment with no key validation. Not reachable from workflow YAML
  today (M9 unbuilt), but validate keys against `[A-Za-z_][A-Za-z0-9_]*` before that seam goes live.

---

## CI & process

Nothing in this epic changes the CI posture, but the findings above argue for these gates:

1. **`bandit`** (or `ruff`'s `S` rules, already partially in use given the `# noqa: S603` markers) as a
   required PR check — it would not have caught these, but it makes the existing `noqa` suppressions
   auditable rather than invisible.
2. **`gitleaks`** on the default branch and on PRs. Given H-3 (the engine auto-commits untracked content
   it did not screen correctly) and C-3 (a resolver can write into `.git`), a repo-side secret scan is
   the only backstop that does not depend on the denylist being right.
3. **`pip-audit`** on `uv.lock` — `CLAUDE.md` already asks for it; the isolation subsystem adds no new
   third-party dependency (pure stdlib + `git` subprocess), so this stays cheap.
4. **A dedicated security regression suite** under `tests/isolation/test_security_guards.py` (already
   exists; a reviewer is editing it) asserting, at minimum: the forced tool set survives every
   `_TOOL_POLICY_FLAGS` spelling (C-1); a planted hook never fires (S-1, already covered); a symlinked
   untracked output is refused (H-2); a nested `config/.env` trips the denylist (H-3); `reconcile` keeps
   a live worktree whose id needs sanitizing (C-4).
5. **Branch protection**: these findings are exactly the class that a `--no-verify`-style bypass would
   ship. Require the suite above on the default branch.

---

## Follow-ups

| # | Finding | Owning ticket (suggested) | Gate |
|---|---|---|---|
| 1 | C-1 forced tool union | `T-Lr6Ka3-llm-resolver-and-rerun` + executor | **merge-blocking** |
| 2 | C-2 push denial | `T-Lr6Ka3` + `T-Tp7Zs2-instructions-and-templates` | **merge-blocking** |
| 3 | C-3 resolver `Bash` / `.git` write | `T-Lr6Ka3` + `T-Ib5Qy9-integrator-core` (engine-side staging) | **merge-blocking** |
| 4 | C-4 reconcile id mismatch | `T-Wk3Nv6-worktree-lifecycle` | **merge-blocking** (data loss) |
| 5 | H-1 resume branch trust | `T-En8Hd4-engine-isolation-wiring` | **merge-blocking** |
| 6 | H-2 symlink copy-back | `T-En8Hd4` | **merge-blocking** |
| 7 | H-3 denylist `-uall` + resume path | `T-Ib5Qy9-integrator-core` | **merge-blocking** |
| 8 | H-4 `ao prune` blast radius | `T-Cx4Jf1-cli-config-prune-observability` | before release |
| 9 | M-1 `.gitattributes` filters | `T-Gt4Pw8-git-porcelain` + `T-Tp7Zs2` (docs) | before release |
| 10 | M-2 sanitizer injectivity | `T-Wk3Nv6` / `T-Sc7Rm2-isolation-schema-models` | before release |
| 11 | M-3 `AO_WORKTREE_ROOT` validation | `T-Wk3Nv6` | before release |
| 12 | M-4/M-5, L-1..L-10 | respective owners | track |

Verify-in-staging: items 1-3 need a live T2 dispatch against a deliberately prompt-injected conflict
fixture (assert the resolver's argv, then assert `.git/hooks/` and `.git/rr-cache/` are unchanged after
the run). Item 4 needs a resume test with a `:`-containing task id. Items 6-7 are unit-testable.

---

## Pre-submit checklist

- [x] Confirmed engagement criteria met — explicit, scoped as-built security audit of a large new
  subsystem (worktree isolation, artifact path-guard widening, a new automatic LLM dispatch point),
  requested as this epic's pre-merge security pass.
- [x] Untrusted spec/payload execution surface checked — no `eval`/`exec`/`shell=True` anywhere in the
  subsystem; `verify_command`/`regenerate[].command` are argv lists from the workflow-root spec only, not
  populatable from an agent-emitted task manifest; ref/path sanitization verified against 17 hostile ids
  with no traversal, option-injection or ref-injection escape. Findings: **C-3** (the resolver agent, not
  a spec field, is the real execution surface), **H-1** (a task-writable `state.json` field steers a CAS
  ref), **L-1** (guard bypasses), **M-1** (config-declared git filters).
- [x] Sandboxing/isolation checked — per-task worktree confinement verified working (**S-4 confirmed**);
  resource bounds present (git timeouts, `verify_timeout_seconds`, `lock_timeout_seconds`,
  `regenerate[].timeout_seconds`, `CAS_RETRY_BOUND=1` — no unbounded loop); no recursion or fan-out
  beyond `max_parallel`. Findings: **C-3** (the sandbox is a worktree, not a container — it shares
  `.git`), **M-3** (`AO_WORKTREE_ROOT` can collapse the boundary), **M-2** (two tasks in one worktree).
- [x] Artifact/path safety checked — traversal, absolute-path and in-worktree symlink escapes all
  correctly rejected by `IsolatedArtifactView` **[PROVEN]**. Findings: **H-2** (the copy-back applies the
  guard to the wrong path and dereferences symlinks), **M-4** (mixed realpath/normpath),
  **M-3**. Writes over engine/config files: **H-1** (`state.json`), **C-3** (`.git/config`,
  `.git/hooks`).
- [x] Secrets handling checked — no plaintext secrets in the repo or specs; the isolation env overlay is
  ids/branches/paths only, nothing secret-ish added by ao; NFR-1 verified (no conflicted file *contents*
  in any log record; the two content-bearing artifacts are documented and one is capped). Findings:
  **H-3** (denylist bypass sweeps secrets into commits), **H-2** (symlink exfiltration), **M-5**
  (full-env inheritance into workflow-supplied commands), **L-6** (uncapped patch artifact).
- [x] Trigger authn/authz checked — **N/A, unchanged**: this epic adds no cron/webhook/API trigger and no
  control endpoint. It extends only the operator-invoked `ao run`/`resume`/`prune`/`hotspots` CLI, which
  carries the same trust model as before. (The service daemon's triggers are E-GIytcL's surface, not
  this branch's.) The one CLI-authorization-adjacent finding is **H-4** (`ao prune`'s widened, unguarded
  blast radius).
- [x] Input validation checked — safe loaders throughout (`json.loads`, pydantic `model_validate_json`;
  no YAML deserialization and no archive extraction in this subsystem, so no zip-slip surface); no new
  SSRF surface from the engine itself (the only network-capable actor is the resolver agent — **C-1**,
  **C-3**). Findings: **H-1** (`RunIntegrationState.branch` unconstrained), **L-4** (V9 validates the
  wrong string), **L-6** (missing size caps on two reads), **C-4/M-2** (`TaskSpec.id` has no pattern and
  the packaged JSON Schema silently no-ops).
- [x] Rate limiting / abuse / DoS bounds checked — CAS retry bounded, lock acquisition bounded and
  non-raising, every subprocess timeout-bounded, existing cost/failure breakers bound T2/T3 spend.
  Findings: **M-5** (verify inherits stdin and can hang while holding every repo lock), **L-6**
  (unbounded reads), and design **S-7** (no cumulative retained-worktree cap) remains unimplemented.
- [x] Supply chain checked — **no new third-party dependency, base image or CI action** is introduced by
  this branch; the subsystem is stdlib + `git` subprocess only. `uv.lock` discipline unchanged. Recommend
  `pip-audit` stays a required check (see CI section) but there is no new exposure to report.
- [x] CI/CD merge-gate posture checked — no workflow file changed on this branch. Existing gates
  (`pytest`, `ruff`, `mypy`, coverage ≥80% per `T-Ee3Mn8` AC15) are unaffected. Gaps and concrete
  additions listed under **CI & process** (gitleaks, bandit/ruff-S visibility, the five named security
  regression tests).
- [x] Every finding has a location + a plausible attack/misconfig path — 11 of 22 findings were
  reproduced with executable scripts and are tagged **[PROVEN]** with their output quoted; the rest are
  tagged **[READ]** and none is asserted as exploitable without naming the reachable path. Severities are
  not inflated: **M-1** explicitly states the config precondition that limits it; **L-1** and **L-8**
  are explicitly labelled defence-in-depth with no reachable call site today.

---

## Experiment index

All scripts under
`/tmp/claude-1000/-usr-avadhoot-mounted-agent-orchestrator/45c17c8f-f587-4e19-8d9f-1d663f9a0488/scratchpad/`
(throwaway; nothing under `src/` or `tests/` was touched).

| Script | Proves |
|---|---|
| `exp1_union_bypass.py` | C-1 — the forced union is dropped for 3 of 4 agent shapes |
| `exp2_push.sh` | C-2 — local-path / `file://` push succeeds; `env -u` and `-c` evasions |
| `exp3_ssh.sh` | C-2 — `ssh://` push succeeds under the full `resolver_env` |
| `exp4_hooks.py`, `exp4b.py` | S-1 confirmed (0 hooks fired); M-1 (smudge/clean filters do fire) |
| `exp5_view.py` | S-4 confirmed; M-3 (`AO_WORKTREE_ROOT`); M-4 (symlinked state dir) |
| `exp6_guard.py` | L-1 — 3 forbidden-verb / hooksPath guard bypasses |
| `exp7_denylist.py` | H-3 — 3 of 4 planted secrets committed unscreened |
| `exp8_refs.sh` | C-3 — integration-ref rewrite, rr-cache poisoning, hook + alias plant |
| `exp9_ids.py` | Sanitizer confirmed clean against 17 hostile ids |
| `exp10_reconcile.py` | C-4 — live worktree + branch destroyed; M-2 — id collision |
| `exp11_symlink_out.py` | H-2 — private key content copied into the shared workspace |

---
- By: dev-security · Role: reviewer · Date: 2026-09-07 · Comment: As-built security review per
  `T-Ee3Mn8` AC11, against the 2026-09-07 design review as baseline. Read-only: no `src/` or `tests/`
  file was modified, no commit made. Files owned by the concurrently-working developer and reviewer
  agents (`engine.py`, `ui/runs.py`, `tests/test_isolation_events.py`, `tests/ui/`,
  `tests/test_e2e_isolation.py`, `tests/isolation/test_ladder_e2e.py`,
  `tests/isolation/test_security_guards.py`, `tests/isolation/test_conflict_fixtures.py`,
  `tests/test_nfr2_regression_gate.py`, `tests/isolation/conftest.py`) were read but never written.
