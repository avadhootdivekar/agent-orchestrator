# TASK: T-Cx4Jf1-cli-config-prune-observability

## Metadata
- Task ID: `T-Cx4Jf1-cli-config-prune-observability`
- Epic ID: `E-Wk9Tz3-task-isolation`
- Owner: unassigned (developer)
- Created: 2026-09-06
- Last Updated: 2026-09-06
- Status: Draft
- Estimate: 2 days

## Requirements Mapping
- Requirement IDs: FR-14, FR-15, FR-12 (config surface) · Design: HLD §11 M9

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
