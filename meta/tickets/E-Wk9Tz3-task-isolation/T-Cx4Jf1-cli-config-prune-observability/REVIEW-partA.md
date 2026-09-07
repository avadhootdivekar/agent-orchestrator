# REVIEW — Part A (`T-Cx4Jf1-cli-config-prune-observability`)

- Reviewer: reviewer-agent
- Date: 2026-09-07
- Scope: `src/agent_orchestrator/cli.py`, `src/agent_orchestrator/project_config.py`,
  `tests/test_cli_isolation_flags.py`, `tests/test_e2e_cli_prune_worktrees.py`, the additive
  cases in `tests/test_project_config.py`, and this ticket's `TASK.md`/`STATUS.md`
  (uncommitted, branch `ad/task-isolation`). Part B (`engine.py` event emission) is
  deliberately out of scope, per the coordinator split — not evaluated here.
- Excluded, confirmed by `git diff --stat`: `engine.py`, `budget.py`, `runstate.py`,
  `executors/*`, `tests/test_engine*`, `tests/test_budget*`, `tests/test_engine_isolation_accounting.py`
  are `T-Ac6Vd9`'s concurrent uncommitted work — zero overlap with this ticket's file set.
  `isolation/resolvers.py`, `tests/isolation/*` (`T-Rm2Lx7`) show no uncommitted changes at
  all in this checkout. Both correctly out of scope.

## Verdict: **APPROVE WITH CHANGES** (must-fix: C-1, C-2)

## Summary

This is a well-scoped, well-tested, well-documented delivery. The precedence chain
(`--isolation` > `AO_ISOLATION` > `.ao/config.yaml isolation.mode` > `"auto"`) is implemented
and table-tested exactly like `--max-parallel`'s existing chain; `ao prune`'s worktree GC and
`--worktrees-only` reconciliation route exclusively through `WorktreeManager.gc_run` /
`GitRepo.prune_worktrees_scoped` (zero raw `subprocess`/git calls), with a real foreign-worktree
survival test proving R-6. `IsolationConfig` is additive per pydantic's default `extra="ignore"`,
confirmed by reading (no override exists). `ao init`'s output is valid YAML that round-trips
through `ProjectConfig` (verified live in this review). `ruff check`/`format --check` are clean,
`mypy src` is unchanged at the 4 pre-existing `_version.py` errors, and the targeted suite is
165/165 exactly matching STATUS.md's claimed count. The two playground-capture failures are
correctly attributed to `T-Ac6Vd9`'s concurrent, uncommitted `engine.py`/`budget.py` work — none
of the files behind those failures are touched by this diff.

The one substantive design tension is real and the developer surfaced it transparently rather
than hiding it: AC-2/AC-3's literal HLD-M9 "global kill switch" / "forces every task" text is
**not** what shipped — what shipped is a pure `defaults.isolation` fill-in that never overrides a
task's own explicit `isolation` declaration, justified by ADR-0006. That justification is sound,
but it is currently recorded only as a developer's own comment in `TASK.md`/`STATUS.md`, not as a
sign-off from the architect/coordinator who wrote the original AC text, and the HLD §11 M9 section
itself still contradicts it. Separately, empirical testing during this review found that an
invalid `isolation.mode` value written directly into `.ao/config.yaml` does **not** exit 1 (it
silently falls back to `"auto"`), which contradicts STATUS.md's own claim that "an invalid value
at any layer exits 1" — a real, untested gap, not just an inaccurate claim.

## Blocking

None. No swallowed error causes silent data loss, no non-determinism was found on the run path,
and no crash path was found (every `GitError`/probe failure is caught and either warned or
degrades safely). The two findings below are Major, not Blocking, because both are bounded,
non-corrupting gaps with a clear remediation path — but they should be closed before merge, not
carried forward silently.

## Major (must-fix before merge)

### C-1 — AC-2/AC-3 deviation lacks an authoritative sign-off; HLD text still contradicts shipped behavior

- **Location**: `TASK.md` AC-2/AC-3 vs. `cli.py:970-981` (`run`) / `cli.py:1246-1249` (`resume`);
  `docs-md/task-isolation-hld.md` §11 M9 ("`'none' = force every task to isolation:none (a global
  kill switch for a bad run)`", "`'worktree' = force every non-structural task to
  isolation:worktree`").
- **Observation**: What shipped is `if eff_isolation_mode != "auto": wf.defaults.isolation =
  eff_isolation_mode` — a pure fill-in for tasks left at `isolation="inherit"`, proven by
  `tests/test_cli_isolation_flags.py::TestIsolationFillInIsAPureDefaultsOverride::
  test_defaults_mutation_fills_in_inherit_only`, whose own assertion
  (`resolve_task_isolation(wf.task("explicit_none"), wf) == "none"  # never clobbered`)
  **disproves** AC-3's literal text ("forces every non-structural task to worktree even when the
  spec says none"). AC-2's own test scenario (no per-task isolation declared) happens to be
  satisfied by the fill-in mechanism, but AC-3 is materially unmet, by design, with no e2e test of
  the literal AC-3 scenario (because the shipped code deliberately can't produce that behavior).
- **Why it matters**: ADR-0006 is a genuinely strong argument for the fill-in-only design ("never a
  silent global clobber... unless explicitly-named loud override like `--force-*`"), and the
  developer's reasoning is sound and thoroughly documented (`TASK.md`'s developer-agent comment,
  `STATUS.md`'s "Deviations recorded" section, and inline test comments). But this is a
  ticket-owning architect's/coordinator's AC being unilaterally reinterpreted by the implementing
  agent, with the disposition recorded only in the developer's own comment — not in an architect
  sign-off, not in an HLD amendment. `docs-md/task-isolation-hld.md` §11 M9 still tells the next
  reader "`--isolation none` is a global kill switch" and "`--isolation worktree` forces every
  non-structural task", which is now simply false about the shipped system. A future operator who
  needs a genuine kill switch (e.g., a spec with per-task `isolation: worktree` declarations
  causing a systemic failure) has no lever at all — `--isolation none` will silently do nothing for
  those tasks, which is a worse operator experience than either "true kill switch" or "clearly
  documented as fill-in-only from the start."
- **Concrete fix**: Before merge, get one of: (a) explicit architect/coordinator sign-off recorded
  as a `TASK.md`/epic `STATUS.md` comment (not just the developer's own), plus a correction to HLD
  §11 M9's "global kill switch"/"forces every task" prose so it no longer contradicts the shipped
  system; or (b) if the literal "true kill switch" behavior is operationally required, implement it
  behind an explicitly-named loud override per ADR-0006's own carve-out (e.g. `--isolation
  {none,worktree}` keeps today's fill-in semantics, and a separate, explicitly-named
  `--force-isolation` — or documented follow-up ticket — provides the override). Either way, the
  HLD text must stop contradicting the code.

### C-2 — AC-1's "invalid value at any layer exits 1" claim is false for the config-file layer

- **Location**: `cli.py:557-593` (`_resolve_isolation_settings`) → `_load_project_config_or_none`
  (`cli.py:316-334`, pre-existing, shared with `_resolve_run_settings`/
  `_resolve_monitoring_settings`).
- **Observation**: Verified empirically in this review:
  ```
  $ echo -e 'isolation:\n  mode: bogus\n' > .ao/config.yaml
  >>> _resolve_isolation_settings(None)
  ('auto', False, {})     # no exception, no message, no exit code — silent fallback to "auto"
  ```
  `load_project_config` itself correctly raises `ConfigError` for this input (proven by
  `tests/test_project_config.py::TestIsolationConfigSchema::
  test_load_project_config_rejects_bad_isolation_mode`), but `_load_project_config_or_none`'s
  bare `except Exception: return None` — called by `_resolve_isolation_settings` — discards that
  error entirely before the CLI-layer `ISOLATION_MODE_CHOICES` validation ever runs, so the
  malformed value is never even seen by `_resolve_isolation_settings`'s own `if resolved_mode not
  in ISOLATION_MODE_CHOICES` check.
- **Why it matters**: This directly contradicts AC-1's own text ("an invalid value at any layer
  exits 1 with a clear message") and `STATUS.md`'s own claim ("An empty env var falls through; an
  invalid value at any layer exits 1 with a clear `ISOLATION_MODE_CHOICES`-naming message") — a
  self-report that overstates what the code (and its own test suite) actually cover. There is no
  test anywhere in `tests/test_cli_isolation_flags.py` for a config-file-layer invalid value routed
  through `_resolve_isolation_settings`/`ao run`/`ao resume` (only `test_invalid_cli_value_exits_1`
  and `test_invalid_env_value_exits_1` exist). Operationally: a typo'd `isolation.mode: workree` in
  `.ao/config.yaml` silently no-ops rather than erroring — worse than "loud and clear" for exactly
  the class of mistake this AC exists to catch. (Confirmed via
  `tests/test_e2e_cli_max_parallel.py` that the identical gap already exists, un-flagged, for
  `max_parallel`'s config-file layer — this is an inherited, systemic gap in
  `_load_project_config_or_none`, not something this ticket introduced from scratch, which is why
  this is Major rather than Blocking.)
- **Concrete fix**: Either (a) fix `_load_project_config_or_none` (or add a narrower variant) to
  let a `ConfigError` specifically from a *present-but-invalid* `isolation:`/relevant block
  propagate as a clear `typer.Exit(1)` message rather than swallowing to `None` — this also fixes
  the identical pre-existing gap for `max_parallel`/monitoring settings, or (b) at minimum, correct
  `STATUS.md`'s "any layer" claim to accurately scope it to CLI+env only, add a regression test
  that pins the current (silent-fallback) behavior so it's an intentional, documented limitation
  rather than an untested gap, and file a follow-up ticket for the shared helper fix.

## Warnings (should fix)

### C-3 — Isolation-mode string literals duplicated instead of reusing `models.py`'s named constants

- **Location**: `cli.py:361-362` (`ISOLATION_MODE_AUTO = "auto"`, `ISOLATION_MODE_CHOICES =
  ("none", "worktree", ISOLATION_MODE_AUTO)`), `project_config.py:118`
  (`mode: Literal["none", "worktree", "auto"] = "auto"`), vs. `models.py:89-90`
  (`ISOLATION_NONE: Literal["none"] = "none"`, `ISOLATION_WORKTREE: Literal["worktree"] =
  "worktree"`).
- **Observation**: `models.py` already exports named constants for exactly these two string
  values, importable without editing `models.py` (the ticket's own "do not touch models.py" rule
  is about logic changes, not read-only imports). `cli.py` and `project_config.py` each re-spell
  `"none"`/`"worktree"` independently instead of importing them.
- **Why it matters**: CLAUDE.md: "Spec/schema constants are named, not magic literals." Three
  independent spellings of the same two domain values (`models.py`, `cli.py`, `project_config.py`)
  can silently drift if a future isolation mode is ever added/renamed in one place and not the
  others — exactly the DRY risk the review brief called out by name.
- **Fix**: At minimum, share one source between `cli.py` and `project_config.py` (e.g. define
  `ISOLATION_MODE_CHOICES` once and derive both the CLI tuple and the pydantic `Literal` from it).
  Where practical, import `models.ISOLATION_NONE`/`ISOLATION_WORKTREE` for the two values they
  share with `models.py`'s existing `WorkflowIsolation` set, rather than retyping the strings.

### C-4 — `_discover_run_worktree_repos` silently skips an unreadable derived toplevel, no warning

- **Location**: `cli.py`, `_discover_run_worktree_repos`, the `if GitRepo.probe(toplevel) is
  None: continue` branch.
- **Observation**: When the derived main-repo toplevel can't be probed (repo moved, an unusual
  `--separate-git-dir` layout, permissions), this silently `continue`s with **no** log/echo —
  unlike every other degrade path in this same function's neighborhood (`group_repos`'s
  `worktree.non_git_repo` warning, `_gc_run_worktrees`'s own `typer.echo(f"WARNING: ...")` on
  `GitError`, `_preview_run_worktrees`'s own warning on a per-repo `GitError`).
- **Why it matters**: The review brief specifically asked about "the repo moved" / "a foreign
  worktree" correctness; a moved/unreadable repo is exactly the case where an operator running `ao
  prune` most needs to know GC was incomplete, and currently gets zero signal — `ao prune` reports
  success with no indication a repo's worktrees/refs were left behind. Note this also contradicts
  `STATUS.md`'s own Risks entry, which describes this exact branch as degrading to "repo skipped,
  **logged**" — verified against the actual code (`cli.py:1614-1617`) that no log/echo call exists
  at that `continue` at all. Another self-report vs. code mismatch, same pattern as C-2.
- **Fix**: Add a one-line `typer.echo(f"WARNING: skipping worktree GC for unreadable repo at
  {repo_dir}: ...", err=True)` (or an equivalent `logger.warning`) at that `continue`, matching the
  established convention elsewhere in this module.

### C-5 — Undocumented structural gap: a manually-removed worktree directory becomes permanently unreapable

- **Location**: `cli.py`, `_discover_run_worktree_repos` / `_worktree_run_ids` design as a whole.
- **Observation**: Repo discovery for `ao prune` is entirely directory-existence-driven (there is
  no persisted "which repos did this run touch" manifest available to `ao prune`, since it takes
  only `--workspace`, not a reposets file — confirmed this is a genuine interface constraint, not
  an oversight). If every physical worktree directory for a run is removed from disk by something
  other than `WorktreeManager` (a manual `rm -rf`, a filesystem restore, etc.) before `ao
  prune`/`ao prune --worktrees-only` runs, that run's real git repo(s) can never be rediscovered,
  so `ao/`-namespaced branches, squash refs, and git's own `.git/worktrees/<id>/` admin entries in
  the main repo are permanently unreapable by any `ao prune` variant — with no error, just "0
  orphaned run(s) reaped."
- **Why it matters**: This is exactly one of the scenarios the review brief asked to be checked
  ("a worktree dir was deleted manually (dangling admin entry)"). It is a real, if low-probability,
  limitation, and unlike the two assumptions already recorded in `STATUS.md`'s Risks section (the
  `.git`-suffix heuristic, the directory-name-is-run-id assumption), this specific failure mode
  isn't written down anywhere.
- **Fix**: No code change required for Part A (fixing it would need a persisted repo-manifest,
  which is a real design change, not a small one) — but add this as a third documented, scoped
  assumption in `STATUS.md`'s Risks section, parallel to the other two, so it isn't rediscovered
  the hard way in production.

## Suggestions (nice to have)

- **C-6** — No test proves `_apply_isolation_state_dir_env`'s config-filled (not
  operator-set) `AO_STATE_DIR` actually reaches a spawned agent subprocess's environment (via
  `claude_cli.py:483`'s `{**os.environ, **ctx.env}`). The propagation mechanism is standard
  Python subprocess inheritance and is very likely fine, but it's untested end-to-end for the
  config-fill-in case specifically (only the operator-set-env case is exercised, indirectly, by
  existing tests).
- **C-7** — `isolation/paths.py::workspace_key`'s lexical-only path handling (pre-existing,
  out of this ticket's edit scope) means `ao prune --workspace <path-A>` and the `ao run
  --workspace <path-B>` that created the state will silently miss each other's worktree root if
  A and B are different (e.g. symlinked) lexical forms of the same directory — `ao prune` reports
  "0 orphaned run(s)" with no hint that it may be looking in the wrong place. Worth a one-line
  docs callout on `ao prune --help` or the operator docs, since this is the first command where an
  operator supplies `--workspace` on a separate invocation from the one that created the state.
- **C-8** — `_resolve_isolation_settings` re-parses `.ao/config.yaml` via
  `_load_project_config_or_none()` a third time per `ao run`/`ao resume` invocation (on top of
  `_resolve_run_settings`'s and `_resolve_monitoring_settings`'s own calls). Pre-blessed as an
  acceptable tradeoff by the pre-existing docstring; noted only as a nit, not asking for a change.
- **C-9** — `_load_all`'s bare `-> tuple:` return annotation (pre-existing, untouched by this
  diff) makes `wf`/`reposet_map`/`agent_map` all `Any`-typed to mypy, so
  `wf.defaults.isolation = eff_isolation_mode`'s runtime-correct `str`-into-`Literal["none",
  "worktree"]` assignment isn't actually type-checked. STATUS.md's "0 new mypy errors" claim is
  literally true but mypy isn't actually exercising this line — worth knowing, not worth blocking
  on (fixing `_load_all`'s return type is out of this ticket's scope and would touch a much wider
  surface).

## Verified positives (checked, not just trusted)

- `ProjectConfig` has no `extra="forbid"` anywhere — additive both directions, per AC-4 (read,
  confirmed via `grep`).
- `ao init` → uncommented `isolation:` block → `ProjectConfig.model_validate` round-trips cleanly
  (executed live in this review).
- Zero raw `subprocess`/git calls anywhere in `cli.py`'s new code — every mutation and every
  read-only preview routes through `GitRepo`/`WorktreeManager` (`grep` confirmed).
- `--isolation` fill-in-vs-override semantics are proven correct at both the unit level
  (`resolve_task_isolation`) and the resume path (`TestAoResumeHonoursFileLevelIsolationDefault`:
  an already-completed task's isolation is immutable across resume; only a not-yet-dispatched task
  picks up a config value written after the crash) — solid determinism/resume-safety evidence.
- `ao prune`'s foreign-worktree survival (R-6/AC-14) is proven for both `ao prune` and `ao prune
  --worktrees-only` in one dedicated test.
- `ruff check`/`format --check` clean on all 5 scope files; `mypy src` unchanged at exactly 4
  pre-existing `_version.py` errors; targeted suite `165 passed / 0 failed` (verified by rerunning
  it in this review, exactly matching STATUS.md's count once `tests/test_e2e_cli_isolation.py`
  — omitted from the review brief's own command list but present in STATUS.md's — is included).
- The two failing playground-capture tests are confirmed, via `git diff --stat`, to be caused
  solely by `T-Ac6Vd9`'s concurrent uncommitted changes to `engine.py`/`budget.py` (attempt-capture
  directory re-keying) — zero file overlap with this ticket's own diff.

## Part A omissions (explicit, as recorded by the developer)

- **AC-9 (dashboard "Integration" column + run-header line, `ui/runs.py`)** — correctly flagged by
  the developer as unassigned to either Part A or Part B (the coordinator's Part A brief enumerated
  four surfaces, not five). Needs a follow-up ticket, not a Part A fix.
- **AC-7 (event contract test)**, **AC-12 (`worktree.retention_high` warning)** — correctly
  deferred to Part B per the coordinator split (`engine.py`-only work, blocked on `T-Lr6Ka3`). Not
  flagged as a gap here, per this review's own instructions.

## Testing notes

- **What to mock**: none needed beyond what's already used — `GitRepo.probe`/real temp git repos
  are the right boundary; the existing `RecordingFakeRunner`-style injection (used elsewhere in the
  `isolation/` test suite) would be the seam if a future test needs to simulate a `GitError` from
  `_gc_run_worktrees` without a real git failure.
- **Integration-test**: the real-git `CliRunner` e2e tests already cover the important
  cross-boundary paths (CLI → `_resolve_isolation_settings` → `WorkflowSpec.defaults.isolation` →
  `resolve_task_isolation` → real worktree creation/cleanup). No gaps found there beyond C-2/C-6
  above.
- **Coverage gaps**: config-file-layer invalid-value path (C-2); the "unreadable/moved repo"
  branch of `_discover_run_worktree_repos` (C-4, would need a fixture that creates a worktree then
  corrupts/moves its main repo before running `ao prune`); `AO_STATE_DIR` propagation into a
  spawned agent's subprocess env specifically via the config-fill-in path (C-6, not the
  operator-set-env path, which is exercised).

---
- By: reviewer-agent · Role: reviewer · Date: 2026-09-07 · Comment: Part A code review complete.
  Verdict APPROVE WITH CHANGES (must-fix C-1, C-2). Full findings in this file. Verification run
  live in this review: `ruff check`/`format --check` clean; `mypy src` 4 pre-existing
  `_version.py` errors only; targeted suite (8 files incl. `test_e2e_cli_isolation.py`) 165/165;
  `ao init` → uncommented `isolation:` block round-trips through `ProjectConfig`; `T-Ac6Vd9`
  attribution for the two playground-capture failures confirmed via `git diff --stat`.
