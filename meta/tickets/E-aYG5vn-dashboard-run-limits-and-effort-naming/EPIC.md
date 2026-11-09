# EPIC: E-aYG5vn-dashboard-run-limits-and-effort-naming

## Metadata
- Epic ID: `E-aYG5vn-dashboard-run-limits-and-effort-naming`
- Title: Effective run/task limits visible everywhere; "effort" = thinking effort, "turn budget" = max-turn tier; first-class autocompact
- Owner: avadhoot
- Created: 2026-11-09
- Last Updated: 2026-11-09 (Rev 2 — owner decisions recorded, autocompact added, tasks created)
- Status: Ready for implementation (start: T-zP28k9). 6 tasks (5 Draft, 1 Blocked by owner gate).
- **Co-delivery:** `E-Sn7tL2-sentinel-long-running-overseer`. Implement in the **same run**: same components
  (run-settings plumbing in `cli.py`, `state.json` settings snapshot, `ui/processes.py` allow-list, NewRun /
  TemplateLaunch / RunDetail). Combined order: `../E-Sn7tL2-sentinel-long-running-overseer/HANDOFF.md`.

## 0. Owner requirements and decisions (2026-11-09)
1. For a **running or completed** run, the dashboard (and CLI) must show the limits that govern it: run/total budget,
   task budget, total timeout, task timeout, etc.
2. Recently added settings (Claude thinking effort `low|medium|high|xhigh|max`) are missing in the dashboard.
3. **Naming (approved):** *effort* means **thinking effort**; what is called `effort` today becomes **turn budget**
   (the max-turn tier); `max_turns` stays the explicit integer override.
4. **Autocompact (approved):** must be a first-class setting in **both CLI and dashboard** (today it only exists as
   Claude's `--autocompact` hand-placed in `agents.json` `extra_args`).
5. **Provider-specific settings** (autocompact is Claude-specific today): first-class but **provider-neutral**: defined
   once in the ao settings model, translated by executors that support it, warned (never fatal) for those that don't.
   Same pattern already used for `thinking_effort` (`models.py:258`: "provider-neutral; claude_cli maps it").

## 1. Findings (verified at HEAD of `ad/enhancements-20oct`, 2026-11-09)
1. **Limits not persisted or exposed.** Run detail (`ui/runs.py:343`) returns tripped breakers only; no
   effective-settings snapshot in `state.json`. Invocation settings (`max_parallel`, `max_attempts`, `max_turns`, quota,
   token budget) are resolved in `cli.py:779` but never recorded. Workflow limits live in `workflow.json`
   (`defaults.timeout_seconds` 1800, per-task `timeout_seconds`, `budget`, breaker thresholds `task_cost_usd`,
   `run_cost_usd`, `run_wall_clock_seconds`, `run_active_seconds`, `injected_task_count`); overseer `run_budget_usd`
   lives in `overseer-config.json`.
2. **Naming collision.** `AgentSpec.effort` (`models.py:256`, `low|medium|high|xhigh`) → `--max-turns` via
   `EFFORT_MAX_TURNS` {15,30,60,120}; `thinking_effort` (`models.py:259`, `:445`) → Claude `--effort`. `ao run --effort`
   help says "low, medium, or high" (`cli.py:1229`, omits xhigh).
3. **Dashboard gaps.** `NewRun.tsx:179-215` has Model, "Effort" (turn tier, mislabelled), Max parallel, Max attempts,
   Token budget. Missing: Thinking effort (already allow-listed at `ui/processes.py:654`), Max turns (allow-listed, no
   input), quota wait/poll, self-heal, autocompact. `TemplateLaunch.tsx:54` posts `options: {}`.
4. **Per-task effective settings partly recorded.** `TaskRunState` has agent/model/effort (E-iafh2F) but not
   thinking effort, max turns, autocompact, timeout, task cost cap; `Usage.tsx` groups by `(agent, model, effort)`.
5. **Autocompact** is not an ao setting anywhere in `src/`: only README guidance and `agents.*.json.tmpl`
   `extra_args: ["--autocompact", "<value>"]`, plus an `ao validate` warning when missing
   (`docs-md/template-cost-hygiene-hld.md`). Only one real executor exists (`executors/claude_cli.py`).

## 2. Requirements

| ID | Requirement | Task |
|----|-------------|------|
| FR-1 | **Run-options registry + effective-settings snapshot** `state.effective_settings{key:{value,source}}` (+ `history[]` per resume; source ∈ cli/env/config/workflow/default/live-override). | T-zP28k9 |
| FR-2 | **Per-task effective limits** at dispatch: thinking_effort, turn_budget, max_turns, autocompact, timeout_seconds, task_cost_cap_usd, max_attempts. | T-zP28k9 |
| FR-3 | **Run "Limits & settings" panel**: each setting + source; consumption vs cap (run cost vs USD caps incl. overseer budget + stage, tokens, wall clock, active seconds, injected tasks, max_parallel/live/ceiling, default timeout, quota). Running and completed runs; pre-upgrade runs show "—". | T-xNFBww |
| FR-4 | **Task limits** in task panel + NowRunning: thinking effort, turn budget / max turns with turns used, autocompact, timeout elapsed vs limit, cost vs cap, attempts used vs max. | T-xNFBww |
| FR-5 | **Naming** in UI, CLI help, docs, `ao status/usage` headers: "Thinking effort", "Turn budget" (e.g. "high (60 turns)"), "Max turns", "Autocompact". | T-f0d7gm (CLI/docs), T-pMpo4O (UI) |
| FR-6 | **Back-compatible rename**: canonical `turn_budget` / `--turn-budget` / `AO_TURN_BUDGET`; legacy `effort` / `--effort` / `AO_EFFORT` accepted as turn budget **with a deprecation warning** this release; conflict ⇒ error; `ao migrate effort-keys [--apply]` rewrites workspace files. | T-f0d7gm |
| FR-7 | **Schema-driven launch forms** (New Run, Resume, Template Launch) from `GET /api/schema/run-options`; server allow-list derived from the same registry; TemplateLaunch sends options. | T-pMpo4O |
| FR-8 | **Usage view** grouped by `(agent, model, turn budget, thinking effort)`. | T-xNFBww |
| FR-9 | **First-class, provider-neutral `autocompact`** (`auto` or 100k–1M tokens) on agent/task/run level; CLI `--autocompact`, env, config, spec, dashboard; executors declare `supported_settings`; `claude_cli` maps to `--autocompact` (existing `extra_args` wins, warning); unsupported executor ⇒ validate + run-log warning; registry `providers` metadata shown in CLI help and dashboard ("applies to: claude-cli"). | T-f0d7gm |
| FR-10 | **Final flip (gated):** `effort` / `--effort` / `AO_EFFORT` become aliases of **thinking effort**, after all workspaces are migrated and one deprecation release has shipped; un-migrated detection notice. | T-9ymKja |

NFR-1 old `state.json` files render. NFR-2 no scheduling change. NFR-3 launch/control endpoints keep ADR-0021 auth +
allow-list posture (no free-form argv). NFR-4 every setting in the registry appears in **CLI and dashboard** — a
registry test fails if an option lacks either surface (unless explicitly marked CLI-only with a reason).

## 3. Design
- **Run-options registry** (`src/agent_orchestrator/run_options.py`): one entry per option with type/enum/range, CLI
  flag(s) + deprecated aliases, env var, config key, help, UI group/label, `providers` (None = all executors),
  `resumable`. Consumers: `cli.py` resolvers (run, resume, new --run), `ui/processes.py` allow-list,
  `/api/schema/run-options`, `state.effective_settings`, `ao ctl set` validation (E-Sn7tL2). Built once, here.
- **Provider-specific settings policy (FR-9, applies to future ones too):** a setting becomes first-class when it is
  operationally important across runs (cost/quality) *and* can be expressed neutrally. It lives in the neutral model;
  each executor declares `supported_settings` and maps it; unsupported ⇒ warning, never failure; raw provider flags
  remain possible via `extra_args`, which always win. Settings that cannot be expressed neutrally stay in
  `extra_args` only.
- Overseer budget/stage read from `overseer-config.json` + latest digest via a small per-template adapter.

## 4. Tasks (folders created 2026-11-09)

| # | Task | Reqs | Depends on | Cross-epic link |
|---|------|------|------------|-----------------|
| 1 | `T-zP28k9-run-options-registry-and-settings-snapshot` | FR-1, FR-2, NFR-4 | — | **blocks E-Sn7tL2 T-Sn2Set** |
| 2 | `T-f0d7gm-turn-budget-rename-and-autocompact` | FR-5 (CLI/docs), FR-6, FR-9 | T-zP28k9 | — |
| 3 | `T-pMpo4O-schema-driven-launch-forms` | FR-5 (UI), FR-7 | T-f0d7gm (+ T-Sn2Set for Sentinel group) | form reused by **T-Sn5Ui1** |
| 4 | `T-xNFBww-dashboard-limits-panels` | FR-3, FR-4, FR-8 | T-zP28k9, T-f0d7gm | run-page layout co-designed with **T-Sn5Ui1** |
| 5 | `T-mQs7CQ-dashboard-epic-e2e-and-docs` | §5 | T-pMpo4O, T-xNFBww | fixtures reused by **T-SnXAcc** |
| 6 | `T-9ymKja-effort-means-thinking-flip` | FR-10 | T-f0d7gm + owner gate | — (Blocked) |

## 5. Final checklist (definition of done)
- [ ] `pytest`, `ruff`, `mypy`, UI `npm test` + `npm run build` green; registry regression test proves every pre-existing option resolves identically.
- [ ] `state.effective_settings` + per-task effective fields written on run and resume; old states render.
- [ ] Dashboard: Limits & settings panel for a running and a completed run; task panel + NowRunning show thinking effort, turn budget/max turns (+ used), autocompact, timeout, cost vs cap, attempts.
- [ ] New Run, Resume and Template Launch forms are schema-driven and include Thinking effort, Turn budget, Max turns, Autocompact, quota, self-heal (+ Sentinel group from E-Sn7tL2).
- [ ] CLI: `--turn-budget`, `--thinking-effort`, `--autocompact` on run/resume/new --run; `--effort` deprecation warning; conflict error; `ao migrate effort-keys` dry-run/apply.
- [ ] Autocompact: provider-neutral field, executor capability, claude mapping, extra_args precedence, unsupported-executor warning.
- [ ] NFR-4 test: every registry option has CLI + dashboard surfaces.
- [ ] Docs: CLI reference, dashboard guide, template READMEs (autocompact now a field), cost-hygiene HLD note, migration note.
- [ ] Tickets + STATUS rollups consistent; E-Sn7tL2 cross-links intact.
- [ ] T-9ymKja remains Blocked until owner gate; recorded in STATUS.md.
