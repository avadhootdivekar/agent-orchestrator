# EPIC: E-aYG5vn-dashboard-run-limits-and-effort-naming

## Metadata
- Epic ID: `E-aYG5vn-dashboard-run-limits-and-effort-naming`
- Title: Dashboard shows effective run/task limits; thinking-effort vs turn-budget naming made consistent across CLI, spec and UI
- Owner: avadhoot
- Created: 2026-11-09
- Last Updated: 2026-11-09
- Status: Draft (requirements + design seed; no tasks created yet)
- Related / co-delivery: `E-Sn7tL2-sentinel-long-running-overseer` — **implement in the same run**; it touches the same
  components (run-settings plumbing in `cli.py`, `state.json` settings snapshot, `ui/processes.py` allow-list, NewRun /
  TemplateLaunch / RunDetail). See §5 for the shared-foundation ordering.

## 1. Summary
Owner request (2026-11-09): (1) for a running or completed run, the dashboard does not show the limits that govern it:
total/run budget, task budget, total timeout, task timeout and similar; (2) recent CLI additions such as Claude
**thinking effort** (`low|medium|high|xhigh|max`) are missing in the dashboard, and the naming is confusing: what we
call `effort` is really a **turn budget** (maps to `--max-turns`), while the model's reasoning depth is
`thinking_effort`. Make naming and fields consistent everywhere.

## 2. Findings (verified at HEAD of `ad/enhancements-20oct`, 2026-11-09)
1. **Limits are not persisted or exposed.** Run detail (`ui/runs.py:343` `detail`) returns tripped breakers only; there
   is no "effective settings" snapshot in `state.json`. Invocation settings (`max_parallel`, `max_attempts`,
   `max_turns`, quota wait, token budget) are resolved in `cli.py:779 _resolve_run_settings` and passed to the engine
   but never recorded, so a completed run cannot show them. Workflow-level limits live in `workflow.json`
   (`defaults.timeout_seconds` default 1800, per-task `timeout_seconds`, `budget`, breaker thresholds such as
   `task_cost_usd`, `run_cost_usd`, `run_wall_clock_seconds`, `run_active_seconds`, `injected_task_count`). Template
   budgets (`overseer-runner` `run_budget_usd`) live in `overseer-config.json`, not in the engine.
2. **Naming collision.** `AgentSpec.effort: EffortLevel` (`models.py:256`, `low|medium|high|xhigh`) → `--max-turns`
   via `EFFORT_MAX_TURNS` {15, 30, 60, 120}; `thinking_effort` (`models.py:259`, `:445`, `low…max`) → Claude's own
   `--effort`. CLI `ao run --effort` help still says "low, medium, or high" (`cli.py:1229`; omits `xhigh`). Claude's
   `--effort` means thinking, so `ao --effort` reads as the opposite of what it does.
3. **Dashboard gaps.** `NewRun.tsx:179-215`: fields Model, "Effort" (turn tier, mislabelled), Max parallel, Max
   attempts, Token budget. Missing: **Thinking effort** (although `ui/processes.py:654` already allow-lists
   `thinking_effort`), **Max turns** (allow-listed, in the numeric list at `:60`, but no input), quota wait/poll,
   self-heal. `TemplateLaunch.tsx:54` posts `options: {}`, so template launches carry no run options at all.
4. **Per-task thinking effort not recorded.** `TaskRunState` records the effective `agent/model/effort` (E-iafh2F,
   `ui/runs.py:110-127`) but not `thinking_effort` or effective `max_turns` / `timeout_seconds`, so NowRunning /
   RunDetail / Usage can't show them. `Usage.tsx` groups by `(agent, model, effort)`, so the thinking dimension is lost.

## 3. Requirements

| ID | Requirement | Verification |
|----|-------------|--------------|
| FR-1 | **Effective-settings snapshot.** At run start and on every resume, the engine records `state.effective_settings` with, for each setting, `{value, source}` (`cli` / `env` / `config` / `workflow` / `default` / `live-override`): max_parallel, max_attempts, max_turns, turn_budget, model, thinking_effort, quota wait/poll, self_heal, token budget, default task timeout, run-level breaker thresholds, and the sentinel block (E-Sn7tL2). Resume appends a dated entry so changes across resumes are visible. | engine + resume tests |
| FR-2 | **Per-task effective limits.** `TaskRunState` additionally records `thinking_effort`, `max_turns`, `timeout_seconds`, `task_cost_cap_usd` (from the breaker, if any) and `max_attempts` at dispatch. | engine tests; old-state back-compat (fields optional) |
| FR-3 | **Run limits panel.** RunDetail gains a "Limits & settings" section: each limit with its source and, where measurable, **consumption vs cap**: run cost vs run budget USD (engine breaker and/or overseer `run_budget_usd` with current overseer stage), tokens vs token budget, wall clock vs `run_wall_clock_seconds`, active seconds vs `run_active_seconds`, injected tasks vs cap, max_parallel (+ live override and ceiling from E-Sn7tL2). Works for running and completed runs; absent values show "—" with a tooltip "not recorded (pre-upgrade run)". | UI tests; API tests |
| FR-4 | **Task limits.** Task detail panel and NowRunning rows show thinking effort, turn budget / max turns (with turns used, from live activity), timeout (elapsed vs timeout), task cost vs task cap, attempts used vs max. | UI tests |
| FR-5 | **Naming.** User-facing name **Thinking effort** for `thinking_effort`; **Turn budget** for today's `effort` (values `low|medium|high|xhigh` with their turn counts shown, e.g. "high (60 turns)"); **Max turns** for the explicit override. Applied to dashboard labels, CLI help, docs, `ao status`/`ao usage` output headers. | UI snapshot tests; CLI help test |
| FR-6 | **Back-compatible renames in CLI/spec/env.** Add `--turn-budget` / `AO_TURN_BUDGET` / spec key `turn_budget` as the canonical names; keep `--effort` / `AO_EFFORT` / `effort` as accepted aliases that emit a deprecation warning (CLI, `ao validate`). Error if both are given with different values. Keep `--thinking-effort` unchanged. Stored state keeps reading old `effort` keys. | CLI + validate + spec tests |
| FR-7 | **Launch forms complete.** NewRun and resume forms: Thinking effort (select `low…max`), Turn budget (select), Max turns, Max parallel, Max attempts, Token budget, quota wait/poll, self-heal, plus the sentinel section from E-Sn7tL2, all rendered from one options schema (`GET /api/schema/run-options`, generated from the same definitions the CLI resolver uses) instead of hand-written fields; the server allow-list is derived from that schema (no argv injection, unknown keys still rejected). `TemplateLaunch` sends options. | UI + API tests; argv-injection negative test |
| FR-8 | **Usage view.** Group by `(agent, model, turn budget, thinking effort)`; old rows show "—" for thinking effort. | UI + usage tests |

NFR-1 back-compat: old `state.json` files render (missing fields → "—"). NFR-2: no change to engine scheduling
behaviour. NFR-3: control/launch endpoints keep ADR-0021 auth + allow-list posture.

## 4. Design notes
- One **run-options registry** (Python): name, CLI flag(s) incl. deprecated aliases, env var, config key, type/enum,
  help, UI group. `cli.py` resolvers, `ui/processes.py` allow-list, `/api/schema/run-options`, and
  `effective_settings` source-tracking all read it. This is the same mechanism E-Sn7tL2 needs for `SentinelSpec` and
  live overrides, so it is built once (shared foundation, §5).
- Overseer budget (`run_budget_usd`, stage) is read from `overseer-config.json` + latest `digest.json` by the run-detail
  service when the workflow is overseer-runner; shown as "template budget" with its stage.

## 5. Tasks (proposed) and co-delivery with E-Sn7tL2
- [ ] `T-zP28k9-run-options-registry-and-settings-snapshot` — FR-1, FR-2, FR-6, registry + schema endpoint. **Shared
  foundation:** E-Sn7tL2's `T-Sn2Set` builds on it (or both are done as one task); do this first.
- [ ] `T-xNFBww-dashboard-limits-panels` — FR-3, FR-4, FR-8. Shares RunDetail layout with E-Sn7tL2's Sentinel panel
  (`T-Sn5Ui1`): design the run page sections together.
- [ ] `T-f0d7gm-effort-naming-and-launch-forms` — FR-5, FR-7 (schema-driven forms, TemplateLaunch options fix, help
  text, docs). Shares the schema-driven form with `T-Sn5Ui1`.

Order inside the combined implementation run: `T-zP28k9` → E-Sn7tL2 `T-Sn2Set` → (`T-f0d7gm` ∥ `T-xNFBww`) alongside
E-Sn7tL2 P1 UI (`T-Sn5Ui1`). These three tasks do not depend on the E-Sn7tL2 spike and can start immediately.

## 6. Open points (owner)
1. Canonical names: **`turn_budget` / "Turn budget"** (recommended) vs `max_turns_tier` / "Turn limit".
2. Deprecation horizon for `--effort` / spec `effort`: **warn now, keep accepting indefinitely** (recommended; cheap,
   avoids breaking sibling workspaces' workflows and overlays).

## 7. Acceptance
Full `pytest`, `ruff`, `mypy`, UI tests green; CliRunner e2e for alias/deprecation paths; a completed run and a running
run both show limits and per-task effective settings in the dashboard; docs/CLI reference updated.
