# TASK: T-Cx4Jf1-cli-config-prune-observability

## Metadata
- Task ID: `T-Cx4Jf1-cli-config-prune-observability`
- Epic ID: `E-Wk9Tz3-task-isolation`
- Owner: developer-agent
- Created: 2026-09-06
- Last Updated: 2026-09-07
- Status: Split into Part A (In Review) / Part B (Pending, after T-Lr6Ka3) -- see "Coordinator split" below
- Estimate: 2.5 days

## Coordinator split (2026-09-07)

The coordinator split this task into two delivery slices, tracked separately so Part A can
land before `T-Lr6Ka3` (the conflict-resolver ladder) exists:

- **Part A -- CLI flags + config chain + `ao prune` worktree GC + status surface.** No
  `engine.py` edits. Covers AC-1 through AC-6, AC-8 (CLI/status half only -- the
  `status.json` schema itself is `T-Sc7Rm2`'s, already shipped), AC-13 (S-8 namespace
  reservation) and AC-14 (R-6 scoped prune). **Status: In Review** (this update).
- **Part B -- structured event emission inside `engine.py`.** Covers AC-7 (the event
  contract test), AC-11's `tier_counts` INCREMENT logic if not already done by
  `T-En8Hd4`/`T-Ib5Qy9` (this ticket's own display-only surfacing of the already-shipped
  field is Part A, done), and AC-12 (S-7's `worktree.retention_high` warning, which is an
  `engine.py`-only emission). Assigned after `T-Lr6Ka3` lands, per the epic's fixed
  `engine.py` edit order. **Status: Pending.**
- **Deferred, not formally re-assigned to either slice** -- AC-9 (dashboard "Integration"
  column + run-header line, `ui/runs.py`). The coordinator's Part A brief enumerated
  exactly four surfaces (CLI flags, config chain, `ao prune` GC, status surface) and did
  not include the dashboard; it is not technically blocked on anything Part A/B touches,
  but is out of scope for this delivery. Flagged here rather than silently dropped.

## Requirements Mapping
- Requirement IDs: FR-14, FR-15, FR-12 (config surface) · Design: HLD §11 M9
- Review findings folded in: **S-5** (surface free-tier/rerere resolution volume), **S-7**
  (retention warning), **S-8** (`ao/` namespace reservation in the config template), **R-6** (`ao prune`
  uses the scoped prune). Re-estimated 2 -> 2.5 days.

## Description
The operator-facing surface: the `--isolation` override chain, the `.ao/config.yaml` `isolation:`
block, worktree garbage collection in `ao prune`, the complete event contract, `status.json` fields,
and one dashboard column.

Files you own:
- `src/agent_orchestrator/cli.py` (edit — `--isolation` on `run`/`resume`, `_resolve_run_settings`
  extension, `ao prune` worktree GC; **not** the `hotspots` command, which is `T-Ov9Bt5`'s)
- `src/agent_orchestrator/project_config.py` (edit — `IsolationConfig` + `isolation` field +
  `_INIT_TEMPLATE` lines)
- `src/agent_orchestrator/engine.py` (edit — event-emission audit only: fill in any
  `integration.*`/`worktree.*` line the earlier tickets left out; **no logic changes**)
- `src/agent_orchestrator/ui/runs.py` or the dashboard task-table component (edit — one column +
  one run-header line)
- `tests/test_isolation_cli_precedence.py`, `tests/test_prune_worktrees.py`,
  `tests/test_isolation_events.py` (new); `tests/ui/` extension for the column

Do NOT touch: any `isolation/` module's logic, `models.py`, `spec.py`, `specs/*.schema.json`.

## Acceptance Criteria
1. `--isolation {none,worktree,auto}` exists on **both** `run` and `resume`, resolved by
   `_resolve_run_settings` as `CLI > AO_ISOLATION > .ao/config.yaml isolation.mode > "auto"`, exactly
   mirroring the `--max-parallel` pattern. `auto` means "honour the spec". Test matrix mirroring
   `tests/test_e2e_cli_max_parallel.py`: CLI beats env, env beats config, config beats default;
   an empty env var falls through; an invalid value exits 1 with a clear message.
2. `--isolation none` is a **global kill switch**: with a workflow declaring
   `defaults.isolation: worktree`, the run takes the shared-checkout path and creates no worktree and
   no `ao/` ref. e2e test asserts both.
3. `--isolation worktree` forces every non-structural task to `worktree` even when the spec says
   `none` (structural tasks stay `none` per D6). e2e test.
4. `ProjectConfig.isolation: IsolationConfig` with `mode`, `strict`, `state_dir`, `env`
   (`dict[str, dict[str, str]]`, keyed by repo id). Confirm by **reading** that `ProjectConfig` has no
   explicit `extra="forbid"` (pydantic's default `extra="ignore"` is what makes this additive both
   directions); if an override exists, stop and flag it rather than changing validation behaviour.
   `_INIT_TEMPLATE` documents every key, commented out.
5. `ao prune` also, for each run directory it deletes: `WorktreeManager.gc_run(run_id)` (remove
   worktrees, `git worktree prune`, delete `refs/heads/ao/<run>/*` and `refs/ao/runs/<run>/*`).
   `--no-worktrees` opts out; `--dry-run` prints what would be removed and touches nothing.
   Tests assert the refs and worktrees are actually gone (and, for `--dry-run`, still present).
6. `ao prune --worktrees-only` reaps worktrees and `ao/` refs whose run directory no longer exists,
   without deleting any run directory. This is the leak class already visible in the consumer repo
   (a stray `.worktrees/full-test-*` plus 14 orphan `worktree-agent-*` branches). Test with a
   hand-created orphan.
7. **Event contract test.** A single integration test drives one clean integration, one T1 resolution
   and one failure, then asserts `run.log` contains exactly the expected `worktree.*` /
   `integration.*` event set from HLD §11 M9 — each with `run_id`, `task_id`, `repo` and, where
   applicable, `tier`, `resolver`, `conflicted` (a **count**, never contents), `from`/`to`,
   `duration_ms`. Also asserts **every terminal path emits exactly one terminal event** (no silent
   exits).
8. `status.json` carries the top-level `integration` block and the per-task `integration_status` /
   `tier_reached` / `conflicted_count` (schema shipped by `T-Sc7Rm2`); a test asserts the exact key
   set, and `ao status --run-id` prints a one-line integration summary.
9. Dashboard: the run task table gains an "Integration" column and the run header shows the
   integration branch + head when present, degrading cleanly (blank) for runs without isolation. One
   UI test.
10. `uv run pytest -q` fully green with recorded counts; `ruff` clean; `uv run mypy src` zero new
    errors; the NFR-2 gate still passes.

### Amendments from the 2026-09-07 review gates

11. **S-5 — make free-tier resolutions visible.** A `rerere` replay lands at the same zero-review tier
    as a clean auto-merge, and with the **default** structural verify it cannot be distinguished from
    one. Because `$GIT_DIR/rr-cache` is shared across runs, a wrong-but-syntactically-valid replay
    recurs silently. Surface `RunIntegrationState.tier_counts` (the field ships with `T-Sc7Rm2`) in
    `status.json` and in the dashboard run header, and show each task's existing `tier_reached` in the
    Integration column. Display-only — **no new field, no new mechanism**. Test: `tier_counts`
    increments for a rerere-resolved integration and appears in `status.json`.
12. **S-7 — bound retained-worktree growth *during* a run.** `keep_worktrees: "on_failure"` retains
    every failed task's worktree by design, so a systemic failure in a ~100-task run retains ~100 of
    them before anyone runs cleanup. Emit `worktree.retention_high` **once** per run at a named
    threshold constant, naming `ao prune --worktrees-only`. A hard cap is deliberately **not**
    implemented — deleting the evidence an operator needs to diagnose a systemic failure is worse than
    the disk cost. (`T-En8Hd4` AC-20 separately confirms a verify-failure storm trips an existing
    breaker.)
13. **S-8 — document the reserved namespace.** The `_INIT_TEMPLATE` comment block states:
    *"`refs/heads/ao/**` and `refs/ao/**` are reserved for the engine — do not create branches there."*
    `ensure()` already treats an unexpected pre-existing `ao/<run>/<task>` branch as a hard error; this
    makes the reservation explicit rather than an implicit consequence of that error path.
14. **R-6 — `ao prune` uses the scoped prune.** The GC path calls `WorktreeManager.gc_run(run_id)`,
    which uses `prune_worktrees_scoped` — never a blanket `git worktree prune`. Test: a user-created
    worktree on the same repo, made unreachable, survives both `ao prune` and
    `ao prune --worktrees-only`.

## Risks
- `cli.py` is shared with `T-Ov9Bt5`. Agree the split before starting: that ticket adds only the
  `hotspots` command; this one adds the option chain and the `prune` changes.
- `engine.py` is shared with `T-En8Hd4`/`T-Lr6Ka3`. This ticket makes **logging-only** edits; if a
  logic change looks necessary, hand it back to the owning ticket.
- Deleting refs is destructive. Mitigation: only ever inside the fixed `ao/` namespace, only for run
  ids the prune is already deleting (or, for `--worktrees-only`, whose run directory is already gone),
  and `--dry-run` is tested to touch nothing.

## Dependencies
- Upstream: `T-Wk3Nv6` (`gc_run`), `T-Ib5Qy9` + `T-En8Hd4` (the events to audit).
- Downstream: `T-Ee3Mn8`, `T-Dr5Yq6`.

## Pseudocode / Algorithm
```text
HLD §11 M9 — precedence chain, config block, prune extension, and the full event list.
```

## Schemas / Interface Notes
- Interface / API: `ao run/resume --isolation`, `ao prune [--worktrees/--no-worktrees]
  [--worktrees-only]`; `AO_ISOLATION`; `.ao/config.yaml` `isolation: {mode, strict, state_dir, env}`.
- Spec / data schema: `status.json` additions (see `T-Sc7Rm2`).
- Triggers / events: the full `worktree.*` / `integration.*` / `scheduling.*` list in HLD §11 M9.
- Artifacts: none new.

## Handoff Boundary
- Upstream: merged `worktrees.py`, `integrator.py`, `engine.py`.
- Downstream: `T-Dr5Yq6` documents this surface.

## Artifacts
- Docs/comments: `meta/tickets/E-Wk9Tz3-task-isolation/T-Cx4Jf1-cli-config-prune-observability/`
- Large outputs: none

---
- By: architect · Role: architect · Date: 2026-09-07 · Comment: Phase-2 amendment. S-5 surfaced via
  the already-shipping `tier_counts`/`tier_reached` fields (display-only, no new mechanism); S-7 as a
  one-shot `worktree.retention_high` warning with the deliberate decision **not** to hard-cap retention;
  S-8 as an explicit namespace reservation in the config template; R-6 carried into `ao prune` with a
  foreign-worktree survival test. Re-estimated 2 -> 2.5 days.

---
- By: developer-agent · Role: developer · Date: 2026-09-07 · Comment: Part A delivered (see
  "Coordinator split" above and `STATUS.md` for evidence/gates). Two deliberate scope resolutions,
  neither guessed at:
  1. **AC-2/AC-3's literal "global kill switch"/"forces every non-structural task" text is NOT what
     shipped.** The coordinator's own Part A brief was explicit and took precedence over this file's
     older text: `--isolation`/`AO_ISOLATION`/`isolation.mode` is a **fill-in** for
     `WorkflowSpec.defaults.isolation` only -- it changes the value tasks left at `isolation="inherit"`
     resolve to, and NEVER overrides a task's own explicit `isolation: "worktree"`/`"none"` declaration
     (ADR-0006, `resolve_task_isolation`'s existing "inherit" semantics, unchanged). AC-2's own test
     scenario (a workflow's `defaults.isolation: worktree`, no task declaring its own isolation) is
     satisfied exactly by this mechanism and is tested; the stronger "override an explicit per-task
     declaration too" behavior is not implemented in Part A. Documented as a scope decision, not a bug.
  2. **`isolation.strict`/`isolation.env` have no CLI/env surface** -- config-file only. HLD §11 M9's own
     interface table is explicit ("isolation.env comes only from `.ao/config.yaml` ... never from a
     workflow spec or a manifest") and no ticket anywhere defines an `--isolation-strict` flag or an
     `AO_ISOLATION_ENV`/`AO_ISOLATION_STRICT` env var; only `mode` gets the full CLI > env > config >
     default chain. The task-assignment message's own "Read first" paraphrase suggested a broader
     surface; resolved in favor of the authoritative HLD/TASK.md text rather than guessed at.

---
- By: developer-agent · Role: developer · Date: 2026-09-07 · Comment: **Review response.** The Part A
  review (`REVIEW-partA.md`) confirmed item 1 above was a real, must-fix gap (C-1) precisely because
  a genuine kill switch was missing. The coordinator resolved it authoritatively (not left to this
  agent): the fill-in-only `--isolation`/`AO_ISOLATION`/`isolation.mode` chain from item 1 stands
  unchanged, and a new `--no-isolation`/`AO_NO_ISOLATION` flag (no config-file layer; mutually
  exclusive with `--isolation worktree`) is the true, loud kill switch AC-2/AC-3 originally called
  for, applied on top of the fill-in so it always wins including over an explicit per-task
  declaration. `"auto"` was dropped from the CLI/config surface entirely per the same review (C-1):
  confirmed, again, no `models.py`/HLD constant for an isolation-mode `"auto"` exists; "no override"
  is `None`, not a string. C-2 (config-file `ConfigError` silently swallowed) was also fixed, at the
  shared `_load_project_config_or_none` helper level per the coordinator's instruction, since it was
  a systemic gap affecting every settings resolver in `cli.py`, not just isolation. Full disposition
  of every finding (C-1 through C-5) in `STATUS.md`. Gates re-verified: targeted suite 175/0
  (+templates spot-check 100/0), full suite **3459 passed / 7 skipped / 0 failed**. No commit made
  per instruction.
