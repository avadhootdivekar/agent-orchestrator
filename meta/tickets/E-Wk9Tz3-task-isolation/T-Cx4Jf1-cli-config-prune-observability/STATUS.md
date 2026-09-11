# STATUS

- ID: `T-Cx4Jf1-cli-config-prune-observability`
- Updated At: 2026-09-07
- State: Part A -- In Review · Part B -- In Review (all ACs delivered, none deferred)
- Owner: developer-agent

## This update

- **2026-09-07 — Part B delivered: event emission, `tier_counts`, retention warning,
  dashboard column.** Covers AC-7, AC-9, AC-10, AC-11/S-5, AC-12/S-7. Files touched:
  `src/agent_orchestrator/engine.py` (emission + the two state-accounting fixes the
  coordinator explicitly assigned), `src/agent_orchestrator/ui/runs.py`,
  `ui/src/components/RunDetail.tsx`, `ui/src/types.ts`, the rebuilt
  `src/agent_orchestrator/ui/static/` bundle, and two new test files. No `isolation/`
  module, `models.py`, `spec.py`, `specs/*.schema.json` or `cli.py` edit.

### Per-AC disposition (Part B)

- **AC-7 — event contract test. DONE.** `tests/test_isolation_events.py::TestEventContract`
  drives ONE run covering all three outcomes (a clean `auto` land, a T1 `mechanical` union
  resolution, and a T4 `integration.failed`) and asserts: the HLD §11 M9 event set is
  present; `integration.activated`/`integration.summary` appear exactly once; every
  run-scoped `integration.*`/`worktree.*` line carries `run_id`; the engine's per-task lines
  carry `task_id`, `repo`, `tier`, `conflicted` (an `int`, asserted as such), `attempt` and
  `duration_ms`; `integration.merged` carries `head_from`/`head_to`; `run.log` contains no
  conflict-marker text anywhere (NFR-1); and **every terminal path emits exactly one
  terminal event**, with the LAST terminal event per task matching that task's final
  `TaskIntegrationState.status`.
  Emission gaps filled in `engine.py` (logging only — no control flow changed):
  1. The `outcome.integration is None` settle branch (execution failure with retries
     exhausted, and R-2's worker-side missing-outputs short-circuit) was **genuinely
     silent**: `ti.status` went `failed`, the worktree was released, and not one
     `integration.*` line was written. Now emits `integration.failed` with the same
     `reason` it records in `ti.last_error`. Pinned by
     `test_missing_declared_output_still_emits_one_terminal_event`.
  2. `integration.merged` was emitted only in the `else` of `if not copy_ok:` — a task whose
     merge landed but whose untracked-outputs copy-back failed exited with no terminal
     event. It is now emitted unconditionally (the merge DID happen; the copy failure has
     its own `integration.untracked_outputs_blocked` line and its own `ts.status` effect).
  3. Full HLD §11 M9 field set added to all four engine terminal lines via one shared
     `_integration_event_fields` helper (no four-way duplication), plus `head_from`/`head_to`
     on `integration.merged` (captured BEFORE `state.integration.heads.update`).
  4. `duration_ms`: measured off the **injected clock** (`self._clock`, never `time.time()`)
     in a new `_integrate_task_timed` wrapper. Deliberately a WRAPPER, not a signature
     change to `_integrate_task` — that adapter's exact signature is pinned by
     `T-En8Hd4`'s own `tests/test_engine_isolation.py`, which is not this ticket's file.
  5. `scheduling.overlap_preferred` was in HLD §11 M9's list but emitted **nowhere**. Added
     in the wave loop, gated on `resolve_overlap_preference(workflow) == "soft"` (so a
     workflow with no isolation emits nothing — NFR-2), scored with the same pure
     `scheduling.overlap.overlap_score` `rank_wave` itself uses (no second copy of the
     scoring rule). Test: `test_overlap_preference_wave_line_only_at_soft`.
- **AC-9 — dashboard. DONE** (de-deferred into Part B by the coordinator).
  `ui/runs.py`: `TaskStat` gains `integration_status`/`tier_reached`/`conflicted_count`
  (read from `RunState.task_integration`, `None`/`0` when the task has no entry) and
  `RunDetail` gains an `integration: RunIntegration | None` block (branch, heads,
  `tier_counts`, `degraded_reason`) that is `None` unless `state.integration.active` — one
  "is there anything to show" test for the frontend instead of two. `RunDetail.tsx` gains
  an "Integration" column (`IntegrationCell`: status chip + tier tag + conflict count,
  a plain `—` for a never-isolated task) and an `IntegrationHeader` line under the tiles.
  Frontend bundle rebuilt (`make ui-build`); `npm run typecheck` and `npm run test`
  (79 tests) both clean. Tests: `tests/ui/test_runs_integration_surface.py` (6) —
  dataclass layer AND the real `/api/runs/{id}` JSON payload, both for an isolated run and
  for a pre-epic run that must degrade to `null`/`0`.
- **AC-10 — gates. DONE.** Numbers below.
- **AC-11 / S-5 — `tier_counts` increment. DONE (the field was NEVER written).** Confirmed
  three independent ways: a grep for any write site across `src/` + `tests/` returns none
  (only reads in `runstate.py`/`cli.py`); the coordinator's own live `ao run` produced
  `tier_counts: {}` after two integrations one of which conflicted and was rerun; and the
  one pre-existing test showing a non-empty value hand-sets it on the state object before
  saving, which proves serialization and nothing else. Implemented in `engine.py`'s settle:
  - `_integration_tier_outcomes(integ)` returns the tiers ONE attempt consumed:
    `integ.tier_reached` when set, **plus `TIER_RERUN` when the outcome is
    `conflict_rerun`**. That second half is load-bearing — `escalation.escalate` leaves
    `tier_reached` unset for a T3 decision and the rerun's own later attempt lands at
    `auto`, so no `IntegrationResult` anywhere ever reports `tier_reached == "rerun"` and a
    naive "count `tier_reached`" would make the ladder's most expensive tier permanently
    invisible. A T2 (`conflict_resolver`) escalation is deliberately NOT credited `llm` at
    dispatch: the resolver's own `resume_integration` settle reports `tier_reached: "llm"`,
    so crediting both would double-count one resolution.
  - `tier_counts` is also now carried on the `integration.summary` line.
  - `_TIER_RANK` is derived from `models.DEFAULT_LADDER` (no second declaration of tier
    order) and used by `_max_tier`.
  - **Per-task accumulation (coordinator decision, 2026-09-07).** `ti.tier_reached` is now
    `_max_tier(ti.tier_reached, *attempt_tiers)` and `ti.conflicted_paths` is only replaced
    by a NON-EMPTY list, instead of both being overwritten by every settle. Rationale: a
    task that conflicted, escalated to T3 and then landed cleanly on a fresh base reported
    `tier_reached: "auto"`, `conflicted_count: 0` — `status.json` forgot it had ever been
    anything but free, and only `run.log` remembered. Safe because the only consumers of
    `ti.conflicted_paths` are (a) `escalation.escalate`'s T4 reason, which already read a
    one-attempt-stale value by construction, and (b) the T2 resolver manifest, which
    `_prepare_resolver_dispatch` already overrides with `materialize_conflict`'s LIVE
    re-derived value (T-Lr6Ka3 review C-1).
  - Tests (both drive a REAL integration and read the value back out of `status.json`, per
    the coordinator's instruction — never a hand-built state object):
    `test_rerere_replay_increments_tier_counts_in_status_json` primes `$GIT_DIR/rr-cache`
    with a real taught-then-replayed resolution, asserts the replay genuinely happened
    (`integration.conflict` with `paths == []` — git had already staged every hunk) and
    that `status.json` reports `{"auto": 1, "mechanical": 1}`; and
    `test_conflict_then_rerun_keeps_the_highest_tier_and_the_conflict_count` drives a real
    conflict→T3 rerun→clean land and asserts the surviving `tier_reached == "rerun"`,
    `conflicted_count == 1`, and `tier_counts == {"auto": 2, "mechanical": 1, "rerun": 1}`.
- **AC-12 / S-7 — `worktree.retention_high`. Verified as shipped, one cleanup, now
  covered.** The implementation already existed (`_WORKTREE_RETENTION_WARN_THRESHOLD = 5`
  at module level, the `_RunContext.retention_warned` latch, `_warn_if_retention_high`
  called from both failure settles) — it landed with `T-En8Hd4`, not with this ticket. What
  did not exist was any test: a grep for "retention" across the whole test tree returned
  zero hits. Verified against both halves the AC names and one literal extracted:
  the remedy string is now the named `_WORKTREE_RETENTION_REMEDY` constant rather than an
  inline literal at the emission site, so the CLI surface and the warning cannot drift.
  Tests: `test_retention_high_fires_exactly_once_per_run` drives `THRESHOLD + 1` uniformly
  failing isolated tasks in ONE wave (a task failure halts the run, and `_drain_remaining`
  then settles every in-flight sibling, so the threshold is crossed strictly more than
  once) and asserts **exactly one** warning carrying `threshold`/`retained`/`remedy`; and
  `test_no_retention_warning_when_worktrees_are_never_kept` proves the
  `keep_worktrees: "never"` guard. No hard cap was added — deliberately, per HLD §24.

### Divergences: HLD §11 M9 (designed) vs the shipped event set

Recorded here for `T-Dr5Yq6`'s deferred reconciliation pass rather than "fixed" in code —
the HLD's own §11 M9 block now carries a `NOT YET RECONCILED` banner saying exactly this.
None of the below were changed by this ticket; every one of them lives in an `isolation/`
module this ticket does not own.

1. **`from`/`to` -> `head_from`/`head_to`.** §11 M9 says "`from`/`to` shas"; `integrator.py`
   emits `head_from`/`head_to` (and the engine's new per-task line matches it). `from` is a
   Python keyword and cannot be a `**kwargs` name, so this reads as a deliberate rename, not
   an omission. **The HLD text should change, not the code.**
2. **`conflicted` as a count.** §11 M9 says "`conflicted` (count, not contents)". The
   integrator's `integration.conflict`/`integration.denylisted_path` carry `paths` — a list
   of PATHS (never file content, so NFR-1 holds). The engine's per-task lines now carry the
   `conflicted` COUNT, and `integration.failed` deliberately carries BOTH (AC-10 of
   `T-Lr6Ka3` requires the paths so an operator can find the worktree without grepping).
   Reconciling the integrator's own field name is `T-Ib5Qy9`/`T-Rm2Lx7` territory.
3. **"Every `integration.*` line carries `run_id`, `task_id`" is not true for two emitters.**
   `isolation/resolvers.py`'s per-path `integration.resolved` and `isolation/worktrees.py`'s
   / `isolation/git.py`'s `worktree.*` lines go through those modules' own module-level
   loggers, not the run-scoped `LoggerAdapter` the engine hands `Integrator` — so they carry
   no `run_id`, and `resolvers.py`'s carries no `task_id` either. Fixing it needs a logger
   injection point on `WorktreeManager.__init__`/`resolve_mechanically` (neither has one),
   i.e. an `isolation/` edit. Low impact in practice: `run.log` is a per-run file. Pinned
   and commented in the event-contract test (`MODULE_LOGGER_SOURCES`) so the gap stays
   visible instead of being papered over.
4. **`repo` is absent on the genuinely non-per-repo lines** — `integration.started`,
   `integration.empty`, `integration.verify_started`/`verify_passed`/`verify_failed`,
   `integration.summary`. Reads as correct-by-construction (they are per-TASK or per-RUN),
   not as an omission; §11 M9's "every line carries `repo`" is the text that is wrong.
5. **`duration_ms` existed nowhere before this ticket.** Now on the engine's four per-task
   terminal lines. The integrator's per-repo lines still carry none — adding it there is an
   `isolation/` edit.
6. **The shipped set is a superset in places.** Not in §11 M9's list but emitted today:
   `integration.empty`, `integration.denylisted_path`, `integration.untracked_outputs_blocked`,
   `integration.rerun_patch_skipped`, `integration.workspace_lock_off`,
   `integration.runlock_acquired`/`runlock_denied`/`runlock_reclaimed`,
   `integration.resolver_merge_file_error`, `integration.resolver_regenerate_timeout`,
   `integration.resolver_regenerate_error`, `worktree.non_git_repo`,
   `worktree.branch_reattached`, `worktree.remove_skipped`, `worktree.reconciled`,
   `worktree.prune_scoped`, `worktree.retention_high`.
7. **`integration.partial` and `integration.degraded` are both live** — checked explicitly
   because both looked like candidates for "declared but never emitted"; they are not.

## Earlier updates

- **2026-09-07 — Review response: fix pass complete, both must-fix findings closed.**
  `REVIEW-partA.md` verdict APPROVE WITH CHANGES (must-fix C-1, C-2; should-fix C-3/C-4/C-5).
  Per-finding disposition:
  - **C-1 (must-fix) — AC-2/AC-3 deviation.** Resolved by explicit coordinator decision (not a
    developer-side reinterpretation anymore): `--isolation {none,worktree}` (+ env/config layers)
    STAYS a fill-in default (ADR-0006, unchanged); a new **`--no-isolation`**/`AO_NO_ISOLATION`
    flag on `run`/`resume` is a true, loud kill switch — forces EVERY task's `isolation` to
    `"none"` regardless of task/defaults/config, logs one WARNING naming every overridden task id,
    deliberately has NO config-file layer (an emergency override, not a setting), and is mutually
    exclusive with an explicit `--isolation worktree` on the same invocation (usage error, exit 1).
    Applied AFTER the fill-in so it always wins, including over an explicit per-task
    `isolation: "worktree"` declaration. `"auto"` is DROPPED from `ISOLATION_MODE_CHOICES`
    entirely — confirmed (again) that neither `models.py` nor the HLD define an isolation-mode
    `"auto"` constant (the reviewer's own finding); "no override" is now represented by the
    resolved mode being `None`, not a third string value. `IsolationConfig.mode` is now
    `WorkflowIsolation | None = None` (was `Literal["none","worktree","auto"] = "auto"`).
    HLD §11 M9 amendment is the architect's own action item (per the coordinator), not this
    ticket's. New tests: `TestNoIsolationKillSwitch` (CLI flag forces every task + warns
    naming both overridden ids; `AO_NO_ISOLATION=1` env layer has the same effect; a negative
    control proving fill-in-ALONE still never flips an explicit task) plus two mutual-exclusivity
    CliRunner tests (`run`/`resume`).
  - **C-2 (must-fix) — invalid config-file `isolation.mode` silently fell back instead of exiting
    1.** Root cause confirmed exactly as the reviewer found it: `_load_project_config_or_none`'s
    `except Exception: return None` discarded `load_project_config`'s own correctly-raised
    `ConfigError` before `_resolve_isolation_settings`'s own `ISOLATION_MODE_CHOICES` check ever
    ran. Fixed at the shared-helper level (per the coordinator's explicit instruction, since this
    is a systemic gap affecting every settings resolver, not just isolation): `_load_project_config_or_none`
    now returns `None` ONLY for "no config file found" and lets a present-but-invalid file's
    `ConfigError` propagate; a new `_load_project_config_or_exit()` wrapper converts that
    `ConfigError` into the standard `typer.Exit(1)` + actionable message, and is now used at
    **every** call site in `cli.py` (`resolve_general_instructions`, `_resolve_run_settings`,
    `_resolve_monitoring_settings`, `_resolve_isolation_settings`, `templates_cmd`, `new_cmd`) —
    so a malformed config now errors loudly everywhere it's read, not just for isolation. Verified:
    the pre-existing `--max-parallel` config-layer test suite (`tests/test_e2e_cli_max_parallel.py`)
    still passes unedited (it never exercises a malformed file). New tests: a unit-level
    `test_invalid_config_file_mode_exits_1_with_message` and a CliRunner e2e
    `test_run_invalid_isolation_config_mode_exits_1`.
  - **C-3 (should-fix) — duplicated isolation-mode string literals.** Fixed: `cli.py` now imports
    `models.ISOLATION_NONE`/`ISOLATION_WORKTREE` at module level (confirmed via
    `sys.modules` that `models` is ALREADY loaded transitively through `.service.cli` before this
    import — zero new eager-import cost, unlike `.engine`, which stays lazy) and derives
    `ISOLATION_MODE_CHOICES` from them; `project_config.IsolationConfig.mode` is now typed
    `models.WorkflowIsolation | None` instead of a third independent `Literal[...]` spelling.
  - **C-4 (should-fix) — silent skip in `_discover_run_worktree_repos`.** Fixed: both degrade
    branches (`GitRepo.probe(repo_dir) is None` and `GitRepo.probe(toplevel) is None`) now
    `typer.echo(f"WARNING: ...", err=True)` naming the run id and the unreadable path, matching
    the established convention elsewhere in this neighborhood (`_gc_run_worktrees`'s/
    `_preview_run_worktrees`'s own `GitError` warnings). STATUS.md's prior claim that this already
    logged was wrong (verified by re-reading the actual code, per the reviewer's own finding) —
    corrected here, not repeated.
  - **C-5 (should-fix) — a manually-removed worktree directory (dangling admin entry).** Verified
    empirically (real git, this review): `git worktree list --porcelain` still lists a worktree
    whose directory was `rm -rf`'d directly (marked `prunable`), and `git worktree remove --force`
    on it succeeds, cleaning the dangling entry. Since `WorktreeManager.gc_run` is driven by git's
    OWN worktree registry (`git worktree list`), not a filesystem re-scan, it ALREADY reaps a
    dangling entry correctly — the only requirement is that `_discover_run_worktree_repos` can
    bootstrap the repo at all, i.e. at least one OTHER worktree directory for the same run/repo
    still exists on disk. New test
    `TestPruneReapsDanglingAdminEntry::test_manually_removed_worktree_dir_still_reaped_via_sibling_discovery`
    proves this directly: task-a's worktree survives, task-b's is manually `rm -rf`'d, `ao prune
    --worktrees-only` reaps BOTH the dangling entry and its branch ref. The residual TRUE
    structural limit (every worktree directory for a run/repo gone, nothing left to bootstrap
    discovery from at all) is real and unfixable without a persisted repo manifest `ao prune`
    doesn't have — now documented in `ao prune`'s own `--help` text (a new "LIMITATION:" paragraph)
    in addition to this file's Risks section below.
  - Suggestions (C-6/C-7/C-8/C-9) — not required by the coordinator's fix list; not actioned this
    round, no change in disposition from the original review.
  - Gates re-verified after the fix pass (numbers below, superseding the earlier round).

- Ticket created by the architect as part of the `E-Wk9Tz3-task-isolation` design package. Not
  started; no code written.
- **2026-09-07 — Phase-2 review amendment applied.** Folded in: S-5 (surface `tier_counts`/`tier_reached` — display-only, no new mechanism), S-7 (one-shot `worktree.retention_high`; a hard cap deliberately rejected), S-8 (`ao/` namespace reservation in the config template), R-6 (`ao prune` uses the scoped prune, with a foreign-worktree survival test). **Re-estimated 2 -> 2.5 days.**
- Per-finding dispositions: HLD §24 "Review dispositions".

- **2026-09-07 — Coordinator split applied; Part A delivered.** The coordinator split this
  task in two so Part A could land ahead of `T-Lr6Ka3`. Full mapping in `TASK.md`'s
  "Coordinator split" section; summary:
  - **Part A (this update): CLI flags + config chain + `ao prune` worktree GC + status
    surface. No `engine.py` edits — confirmed via `git status`, only `cli.py` and
    `project_config.py` touched under `src/`.**
  - **Part B (not started): structured `worktree.*`/`integration.*` event emission inside
    `engine.py`** — the event-contract test (AC-7) and S-7's `worktree.retention_high`
    warning (AC-12) both require `engine.py` logic and are explicitly out of scope here.
    Assigned after `T-Lr6Ka3` lands, per the epic's fixed `engine.py` edit order.
  - **Deferred, unassigned:** the dashboard "Integration" column / run-header line (AC-9,
    `ui/runs.py`) — not in the coordinator's enumerated Part A surface list, not picked up
    by Part B either. Flagged for a follow-up ticket rather than silently dropped.

### What Part A implements

1. **`--isolation {none,worktree,auto}` on `run` and `resume`** (`cli.py`), resolved by a
   new `_resolve_isolation_settings` exactly mirroring `_resolve_run_settings`'s own
   `--max-parallel` chain: `--isolation` > `AO_ISOLATION` > `.ao/config.yaml
   isolation.mode` > `"auto"` (no override). An empty env var falls through; an invalid
   value at any layer exits 1 with a clear `ISOLATION_MODE_CHOICES`-naming message.
   `ao validate` is unchanged (no `--isolation` flag added there, per the brief).
2. **Fill-in semantics, not a global kill switch** (a deliberate, documented narrowing of
   TASK.md's original AC-2/AC-3 text — see `TASK.md`'s developer-agent comment for the full
   rationale). A resolved mode `!= "auto"` is written onto `WorkflowSpec.defaults.isolation`
   directly (`wf.defaults.isolation = eff_isolation_mode`), which is exactly the value
   `models._declared_isolation`/`resolve_task_isolation` already fall back to for any task
   left at `isolation="inherit"` — so this needed no `models.py`/`engine.py` change at all.
   A task with its own explicit `isolation: "worktree"`/`"none"` is never touched.
3. **`isolation.strict`/`isolation.env` wired straight to the already-existing
   `Orchestrator(isolation_strict=, isolation_env=)` constructor params** (added by
   `T-En8Hd4`, previously unwired — this ticket is the "future M9 ticket" that ticket's own
   STATUS.md named). Config-file only, no CLI/env surface, per HLD §11 M9's own interface
   table.
4. **`.ao/config.yaml` `isolation.state_dir`** fills `AO_STATE_DIR` in (new
   `_apply_isolation_state_dir_env`), ONLY when the real env var isn't already set —
   mirrors `apply_project_config_env`'s existing "explicit env always wins" rule. Applied
   before any `isolation.paths` call, so `ao run`/`resume`/`prune` all honour it for the
   same workspace.
5. **`project_config.IsolationConfig`** (`mode`, `strict`, `state_dir`, `env: dict[str,
   dict[str, str]]`) added to `ProjectConfig.isolation`. Confirmed by reading:
   `ProjectConfig` carries no `extra="forbid"` override anywhere in `project_config.py`
   (pydantic's default `extra="ignore"` applies), so this is additive both directions, per
   AC-4's instruction. `_INIT_TEMPLATE` documents every key (commented) plus the S-8
   `refs/heads/ao/**`/`refs/ao/**` reservation sentence verbatim.
6. **`ao prune` worktree GC** (AC-5): for every run directory `ao prune` deletes, also
   `WorktreeManager.gc_run(run_id)` — new `--worktrees/--no-worktrees` (default on) and
   `--dry-run` extends to worktrees (a read-only preview via new `_preview_run_worktrees`,
   which never calls a mutating method). Since `ao prune` takes only `--workspace` (no
   reposets/workflow triplet), the real git repos a run touches are DISCOVERED by probing
   the physical worktree directories left on disk under
   `isolation.paths.worktree_root_prefix_for(workspace, run_id)` (new
   `_discover_run_worktree_repos`) — recovered from each worktree's `--git-common-dir`
   (never the worktree directory itself, which this same GC pass may remove mid-operation)
   so the constructed `GitRepo`'s invocation cwd stays stable for the whole call.
7. **`ao prune --worktrees-only`** (AC-6): a standalone sweep (new `_prune_worktrees_only`)
   that reaps worktrees/`ao/`-namespaced refs whose run directory no longer exists,
   touching zero run directories. Tested against a hand-created orphan.
8. **R-6/AC-14**: every removal path is `WorktreeManager.gc_run` -> `GitRepo.
   prune_worktrees_scoped`/scoped `worktree_remove`/`list_refs` calls only — no raw git
   subprocess anywhere in `cli.py`'s new code. A user-created (foreign) worktree on the
   same repo survives both `ao prune` and `ao prune --worktrees-only` — dedicated test.
9. **`ao status` integration summary** (AC-8, display half): new `_echo_integration_summary`
   shared by `_print_state` (live `RunState`, used by `run`/`resume`'s own final print and
   `status`'s state.json fallback) and `_print_status_snapshot` (the `status.json` fast
   path) — one-line branch/head(s)/integrated/conflict/failed/tier_counts summary,
   no-op (byte-identical output) when `integration.active` is `False`. The `status.json`
   schema itself (`integration` block, per-task `integration_status`/`tier_reached`/
   `conflicted_count`) already shipped with `T-Sc7Rm2`; this ticket only adds the CLI
   display over already-shipped fields — no new `status.json` keys.
10. **S-5 (partial — CLI half only)**: `tier_counts` is now visible in `ao status`
    output (`tiers: auto=2, mechanical=1` etc.). The dashboard half of S-5 is out of scope
    (see "Deferred" above).

### Deviations recorded (both explained, neither guessed at — see `TASK.md`'s
developer-agent comment for full text)
- AC-2/AC-3's literal "global kill switch"/"forces every task" wording is narrowed to a
  pure `defaults.isolation` fill-in, per the coordinator's explicit Part A brief
  ("never overwrites a task's explicit isolation... consistent with `resolve_task_isolation`").
- `isolation.strict`/`isolation.env` are config-file-only (no `--isolation-strict` CLI flag,
  no `AO_ISOLATION_ENV`/`AO_ISOLATION_STRICT` env var) — resolved against the authoritative
  HLD §11 M9 interface table and `T-En8Hd4`'s own recorded interface-gap note, over a looser
  paraphrase in the task-assignment message.

## Evidence

### Part B (2026-09-07)
- Design: HLD §11 M9 (its event list now carries a `NOT YET RECONCILED` banner naming
  `T-Dr5Yq6`), §11 M9's S-5 and S-7 paragraphs, `ADR-0013` D5/D6.
- Edited source: `src/agent_orchestrator/engine.py` (emission + `tier_counts` increment +
  per-task tier accumulation; `_max_tier`, `_integration_tier_outcomes`,
  `_integration_event_fields`, `_integrate_task_timed`, `_log_overlap_preference`,
  `_WORKTREE_RETENTION_REMEDY`), `src/agent_orchestrator/ui/runs.py` (`TaskStat` +3 fields,
  new `RunIntegration`, `RunDetail.integration`).
- Edited frontend: `ui/src/types.ts`, `ui/src/components/RunDetail.tsx`
  (`IntegrationCell`, `IntegrationHeader`, one new table column); bundle rebuilt into
  `src/agent_orchestrator/ui/static/assets/index-M62F4OvQ.js`.
- New tests: `tests/test_isolation_events.py` (7: the AC-7 contract test, the
  previously-silent missing-outputs terminal path, `scheduling.overlap_preferred`, the
  rerere `tier_counts` increment read back out of `status.json`, conflict→rerun tier/count
  survival, and both retention-warning cases);
  `tests/ui/test_runs_integration_surface.py` (6: dataclass + `/api/runs/{id}` payload,
  isolated and degrades-to-null).
- NOT edited, deliberately: any `isolation/` module, `models.py`, `spec.py`,
  `specs/*.schema.json`, `cli.py`, `runstate.py`, or another ticket's test file.

### Part A
- Design: [`docs-md/task-isolation-hld.md`](../../../../docs-md/task-isolation-hld.md) §11 M9,
  §14 and
  [`ADR-0013`](../../../../docs-md/adr/ADR-0013-per-task-git-isolation-and-rebase-integration.md)
  D6; ADR-0003 §3 (precedence), ADR-0006 (per-agent/per-task config over run-level flags).
- Edited source: `src/agent_orchestrator/cli.py`, `src/agent_orchestrator/project_config.py`.
- New tests: `tests/test_cli_isolation_flags.py` (20 tests: precedence chain, fill-in
  semantics, `AO_STATE_DIR` fill-in, two real-git worktree e2e via `CliRunner`, invalid-value
  exit-1 on both `run`/`resume`, `ao status` degrade-cleanly, `ao resume` picking up a
  file-level isolation default written after the initial crash);
  `tests/test_e2e_cli_prune_worktrees.py` (7 tests: orphan reaped + foreign survives +
  report text, dry-run touches nothing, live run directory untouched, default-prune GC +
  `--no-worktrees` opt-out + dry-run, R-6 foreign-worktree-survives-both-variants).
- Additive cases: `tests/test_project_config.py` (+9: `IsolationConfig` schema round-trip/
  validation/nested-env-coercion/rejection, `load_project_config` parsing +
  bad-mode rejection, scaffold-template content). `tests/test_config.py` — N/A, that file
  covers `config.py` (`load_agents`/`load_reposets`), an unrelated module; no applicable
  additive case.

## Gates — Part B (exact numbers, 2026-09-07)
- `./.venv/bin/ruff check .` / `ruff format --check .`: clean, repo-wide (289 files).
- `./.venv/bin/mypy src`: unchanged at exactly **4 pre-existing `_version.py` errors**,
  73 source files checked — no new errors.
- Full suite `./.venv/bin/pytest -q -p no:cacheprovider`: **3702 passed / 7 skipped /
  0 failed** in 138.7s, one clean run. Baseline on HEAD at Part B start was 3678/7/0; this
  ticket adds **13** tests (`tests/test_isolation_events.py` 7 +
  `tests/ui/test_runs_integration_surface.py` 6). The remaining +11 are the sibling
  `T-Ee3Mn8` reviewer's concurrent additions under `tests/isolation/` (confirmed via
  `git status` file ownership — zero overlap with this ticket's files).
- Isolation-focused regression sweep (`test_engine_isolation.py test_e2e_isolation.py
  tests/isolation test_nfr2_regression_gate.py test_isolation_models.py
  test_e2e_cli_isolation.py test_cli_isolation_flags.py`): **843 passed / 0 failed** —
  the NFR-2 golden-compare gate included.
- Frontend: `npm run typecheck` clean; `npm run test` **79 passed (8 files)**;
  `npm run build` regenerated `src/agent_orchestrator/ui/static/assets/index-M62F4OvQ.js`
  (the CSS bundle hash is unchanged).
- Live end-to-end verification against the coordinator's own reproduction (real `ao run`,
  `claude_cli` executor, two conflicting parallel isolated tasks, ladder
  `["auto","mechanical","rerun"]`): `status.json` now reports
  `tier_counts: {"auto": 2, "mechanical": 1, "rerun": 1}` (was `{}`) and the reran task
  reports `tier_reached: "rerun"`, `conflicted_count: 1` (was `"auto"`, `0`).

**Caveat on re-running these numbers.** The full-suite figure above was measured on a clean
tree. Shortly afterwards a concurrent security-hardening pass began landing across
`cli.py`, `spec.py`, `models.py`, `errors.py`, `executors/claude_cli.py` and five
`isolation/` modules, which introduced a new guard rejecting an `$AO_STATE_DIR` that lies
INSIDE the workspace root. Every pre-existing isolation e2e fixture points `AO_STATE_DIR`
at `<workspace>/ao-state`, so `tests/test_e2e_isolation.py` (24-30 failures) and
`tests/test_engine_isolation.py` (27 failures) are currently red on that guard alone —
files this ticket never touched, failing with `ConfigError: worktree root ... lies inside
the workspace root`, i.e. entirely attributable to that in-flight pass and its not-yet-
updated helpers. This ticket's own fixtures already point `AO_STATE_DIR` at a SIBLING of
the workspace, so `tests/test_isolation_events.py` (7/7) and
`tests/ui/test_runs_integration_surface.py` (6/6) stay green through it, as do
`tests/test_isolation_models.py` and the rest of `tests/ui/`. The full-suite number needs
one re-run once that pass settles; nothing in it is expected to change.

## Gates (Part A — exact numbers, post-review fix pass, 2026-09-07)
- `uv run ruff check .` / `uv run ruff format --check .`: clean, repo-wide.
- `uv run mypy src`: unchanged at exactly 4 pre-existing `_version.py` errors.
- Targeted suite (`tests/test_cli.py tests/test_cli_isolation_flags.py tests/test_e2e_cli.py
  tests/test_e2e_cli_prune_worktrees.py tests/test_config.py tests/test_project_config.py
  tests/test_e2e_cli_hotspots.py tests/test_e2e_cli_isolation.py`): **175 passed / 0 failed**
  (up from 165: +9 net new/changed isolation-mode tests in `test_cli_isolation_flags.py` and
  `test_project_config.py`, +1 new dangling-admin-entry test in
  `test_e2e_cli_prune_worktrees.py`). Also spot-checked `tests/test_templates.py`
  `tests/test_e2e_cli_templates.py` `tests/ui/test_templates_api.py` (the other
  `_load_project_config_or_exit` call sites, C-2's shared-helper fix): **100 passed / 0 failed**.
- Full suite (`uv run pytest -q -p no:cacheprovider`): **3459 passed / 7 skipped / 0 failed**
  in 129.0s, one clean run — the two `T-Ac6Vd9`-attributable playground-capture failures from
  the pre-fix-pass round are gone (that sibling ticket's concurrent checkout state moved on in
  the interim; unrelated to this fix pass, confirmed via `git status` unchanged file
  ownership).

## Risks / Blockers
- See `TASK.md` > Risks. Not blocked; Part A's dependencies (`T-Wk3Nv6`, `T-Ib5Qy9`,
  `T-En8Hd4`) are all merged-in-checkout (`In Review`) and were read from source, not
  re-derived from the design doc, per this ticket's own "Read first" instruction.
- Part B is blocked on `T-Lr6Ka3` landing, per the coordinator's own split.
- `_discover_run_worktree_repos`'s ".git"-suffix-stripping heuristic (recovering a repo's
  persistent toplevel from `--git-common-dir` when only a linked worktree survives on disk)
  assumes the standard non-bare, non-`--separate-git-dir` repo layout this codebase always
  creates; a genuinely unusual layout degrades to "repo skipped, logged" rather than a
  crash (`GitRepo.probe(toplevel) is None` guard), never a false GC.
- `_worktree_run_ids`'s directory-name-is-the-run-id assumption holds for every run_id this
  codebase generates (`sanitize_ref_component`'s charset already matches
  `<workflow_id>-<UTC timestamp>`); documented as a scoped assumption, not asserted as a
  general guarantee.
- **C-5 residual limit (2026-09-07 review, confirmed real, not fixed — see disposition
  above).** If EVERY worktree directory for a given run's repo is gone from disk (not just
  one, with a sibling surviving to bootstrap discovery), that repo becomes permanently
  undiscoverable to `ao prune` in any variant, and its `ao/`-namespaced branch/refs are
  never reaped — reported as "0 orphaned run(s)" with no error. Fixing this would need a
  persisted repo manifest `ao prune` doesn't have (it takes only `--workspace`); documented
  in `ao prune --help`'s own text as well as here.

## Next actions
1. Reviewer: verify Part A against the deviations recorded above (both are argued from the
   coordinator's own brief / the authoritative HLD text, not guessed).
2. Reviewer: verify Part B — in particular the two accounting semantics the coordinator
   decided (credit `rerun` at the T3 escalation settle, since no `IntegrationResult` ever
   reports that tier; accumulate `tier_reached`/`conflicted_count` instead of overwriting).
3. `T-Dr5Yq6`: fold the "Divergences: HLD §11 M9 vs the shipped event set" list above into
   the HLD's own §11 M9 reconciliation pass (the block already carries a `NOT YET
   RECONCILED` banner naming that ticket).
4. Follow-up, NOT this ticket: give `WorktreeManager`/`resolve_mechanically` a logger
   injection point so their `worktree.*`/`integration.resolved` lines carry `run_id`
   (divergence 3). Needs an `isolation/` edit, which this ticket is forbidden.

---
- By: developer-agent · Role: developer · Date: 2026-09-07 · Comment: Part A implemented and
  gated (see above); no commit made per instruction. Full pre-handoff checklist in the PR/
  session summary.

---
- By: developer-agent · Role: developer · Date: 2026-09-07 · Comment: Review response complete
  — both must-fix findings (C-1, C-2) and all three should-fix findings (C-3, C-4, C-5) closed;
  full per-finding disposition in "This update" above. C-1 implemented exactly per the
  coordinator's explicit decision (`--no-isolation` kill switch, `"auto"` dropped). C-2 fixed at
  the shared-helper level, all 6 call sites in `cli.py` updated, pre-existing `--max-parallel`
  config tests confirmed still green. Gates: `ruff`/`format --check` clean; `mypy src` unchanged
  at 4 pre-existing errors; targeted suite 175/0 (+templates spot-check 100/0); full suite
  **3459 passed / 7 skipped / 0 failed**, one clean run. No commit made per instruction.

---
- By: reviewer-agent · Role: reviewer · Date: 2026-09-07 · Comment: Part A code review complete
  — full findings in
  [`REVIEW-partA.md`](REVIEW-partA.md). **Verdict: APPROVE WITH CHANGES** (must-fix: C-1, C-2).
  Verified live: `ruff check`/`format --check` clean on all 5 scope files; `mypy src` unchanged
  at 4 pre-existing `_version.py` errors; targeted suite (the 8 files STATUS.md names, incl.
  `test_e2e_cli_isolation.py`) **165 passed / 0 failed**, matching this file's own count exactly;
  `ao init` → uncommented `isolation:` block round-trips cleanly through `ProjectConfig`; the two
  playground-capture failures are confirmed via `git diff --stat` to be caused solely by
  `T-Ac6Vd9`'s concurrent, uncommitted `engine.py`/`budget.py` changes, zero overlap with this
  ticket's files.
  - **C-1 (Major)**: AC-2/AC-3's fill-in-only implementation (vs. the HLD's literal "global kill
    switch"/"forces every task" text) is well-argued from ADR-0006 and thoroughly tested, but is
    currently recorded only as the developer's own comment — no architect/coordinator sign-off,
    and `docs-md/task-isolation-hld.md` §11 M9 still contradicts the shipped behavior. Needs either
    a formal sign-off + HLD text correction, or an explicitly-named `--force-*`-style override per
    ADR-0006's own carve-out.
  - **C-2 (Major)**: Empirically verified that an invalid `isolation.mode` value in
    `.ao/config.yaml` does **not** exit 1 — `_load_project_config_or_none`'s broad
    `except Exception: return None` (pre-existing, shared with `_resolve_run_settings`/
    `_resolve_monitoring_settings`) swallows the `ConfigError` before `_resolve_isolation_settings`
    ever sees it, silently falling back to `"auto"`. This contradicts both AC-1's own text ("an
    invalid value at any layer exits 1") and this file's own claim of the same. No test covers
    this layer. The identical gap pre-exists for `--max-parallel`'s config layer too (not
    introduced fresh here), which is why this is Major rather than Blocking — but it needs either
    a fix or an explicit, correctly-scoped documentation of the limitation plus a pinning test.
  - Five further Warnings/Suggestions (C-3 through C-9: duplicated isolation-mode string literals
    vs. `models.py`'s existing named constants; a silent no-log skip in
    `_discover_run_worktree_repos` that also contradicts this file's own "repo skipped, logged"
    Risks claim; an undocumented structural limit on GC-ing a manually-removed worktree directory;
    plus three Suggestions) — see `REVIEW-partA.md` for full detail, locations, and concrete fixes.
  - Explicitly re-confirmed both Part A/Part B splits and the AC-9 dashboard deferral are correctly
    scoped and flagged, not silently dropped.
  - No source/test edits made; no commit made, per instruction.

---
- By: developer-agent · Role: developer · Date: 2026-09-07 · Comment: **Part B complete.**
  AC-7 / AC-9 / AC-10 / AC-11 (S-5) / AC-12 (S-7) all delivered — no AC on this ticket is
  now deferred or unassigned. Gates: `ruff check .` + `ruff format --check .` clean;
  `mypy src` unchanged at 4 pre-existing `_version.py` errors; full suite **3702 passed /
  7 skipped / 0 failed**; frontend typecheck + 79 vitest tests clean; bundle rebuilt.
  Two coordinator-assigned behaviour changes (the `tier_counts` increment and
  highest-tier-across-attempts accumulation) are implemented and verified against the
  coordinator's own live reproduction, not just against tests. Seven designed-vs-shipped
  event divergences recorded above for `T-Dr5Yq6` rather than "fixed" against stale prose;
  none of them is in a file this ticket owns. No commit made and no epic-level `STATUS.md`
  edit, per instruction.
