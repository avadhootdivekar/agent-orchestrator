# EPIC: E-st5p3q-settings-precedence-policy

## Metadata
- Epic ID: `E-st5p3q-settings-precedence-policy`
- Title: Settings & precedence policy — specificity chain, clobber-defect fix, force overrides, `ao config explain`, frozen RunState settings
- Owner: architect
- Created: 2026-07-09
- Last Updated: 2026-07-09 (epic drafted from accepted design session)
- Status: Draft (Proposed / backlog)

## Summary
- Goal: Implement ADR-0003 — one **specificity precedence chain** (more specific wins; within a level, invocation beats file), fixing the live precedence-inversion defect where a global CLI/env `--model`/`--effort` silently overwrites deliberately-pinned per-agent settings. Make resolution observable (`ao config explain`), reproducible (freeze resolved settings into `RunState` at run start), and lint-able (`ao validate --strict`).
- Motivating live defect: in `src/agent_orchestrator/cli.py` (`_resolve_run_settings`), a global `--model`/`AO_MODEL`/config `model` is applied by looping every `AgentSpec` and overwriting it — `for spec in agent_map.values(): spec.model = eff_model` (run path lines ~412-414; resume path ~568-570), and the same for `effort` (~415-425 / ~571-581). Deliberately-pinned per-agent models (a downstream runner's `architect-opus`, `reviewer-opus`) are silently downgraded by `ao run --model haiku`. This is precedence inversion: the most specific declaration loses to the most generic one.
- Scope In: single resolver implementing the chain; fill-in (not override) invocation semantics; `--force-model`/`--force-effort`; workflow-`defaults` and per-task `model`/`effort`; empty-string-env rejection; freeze-into-RunState + resume reuse; `ao config explain`; `ao validate --strict`.
- Scope Out: quota and budget settings staying on the invocation chain only (they guard the wallet, not a task — deliberately excluded from the specificity chain); an `inherit:` marker DSL in workflow files (ADR-0003 Option 2, rejected); flattening to two layers (Option 3, rejected).

## Traceability (design source — decisions locked 2026-07-09)
- ADR: [`docs-md/adr/ADR-0003-settings-precedence-policy.md`](../../../docs-md/adr/ADR-0003-settings-precedence-policy.md) — Accepted. §1.1 precedence rule, §1.2 observability/freeze/strict-lint, "Known pitfalls", "Resolved decisions" 1–3.
- Related HLD (consumer of per-task model/effort): [`docs-md/granular-task-decomposition-hld.md`](../../../docs-md/granular-task-decomposition-hld.md) §5 E3.
- Shipped baseline: `project_config.py` three-layer resolution (memory `project_config_three_layers`); `cli.py` `_resolve_run_settings`.

## The precedence chain (ADR-0003 §1.1 — locked)
```
task-level (workflow)                 ← most specific, wins
  > agent-level (agents.json)
    > workflow-level defaults (workflow.json)
      > invocation level:  CLI flag > env var > .ao/config.yaml
        > built-in default
```
Governs **model, effort, max_turns, max_attempts, timeout_seconds** (resolved decision 3). Invocation-level values are **fill-in defaults, not overrides** — they apply only where no task/agent/workflow value is declared.

## MVP vs Non-MVP split
- **MVP (this epic)** — the resolver + clobber-defect fix + force flags + workflow-defaults model/effort/max_turns + empty-string-env rejection + freeze-into-RunState + resume reuse + `ao config explain` + `ao validate --strict`, with tests and docs.
- **In scope, may be a later phase within this epic** — per-task `model`/`effort` (ADR-0003 resolved decision 2: a tracked requirement, not a maybe; needed by the granular-decomposition epic's cheap step sessions). Sequenced after the resolver lands; interim workaround is extra agent entries (`developer-step`) in `agents.json`.
- **Non-MVP / explicitly excluded** — `inherit:`-marker workflows (ADR Option 2), two-layer flattening (Option 3), pulling quota/budget onto the specificity chain (resolved decision 3 keeps them run-scoped).

## Requirements (traceable to ADR-0003)
### Functional
- FR-S1 Single precedence chain resolver: task > agent > workflow-defaults > invocation(CLI>env>`.ao/config.yaml`) > default, for the five governed knobs. [§1.1, resolved 3]
- FR-S2 Fill-in, not override: invocation-level model/effort apply only where no more-specific value is declared — fixes the live clobber defect in `cli.py`. [§1.1, Known pitfalls, resolved 1]
- FR-S3 Loud forced override: `--force-model X` / `--force-effort X` retain today's clobber-everything behavior for the legitimate "rerun the whole thing cheap" case; explicit flag name = no silent surprise. [§1.1, resolved 1]
- FR-S4 Workflow `defaults.model`/`effort`/`max_turns`: author intent for the whole workflow; schema + models + resolver wiring. Workflow `defaults.model` beats a plain CLI `--model`. [§1.1, resolved 1]
- FR-S5 Per-task `model`/`effort` on `TaskSpec`: top of the chain; pins one expensive/cheap step (enables `E-gd8m4x` step sessions). [resolved 2; granular HLD §5 E3]
- FR-S6 `ao config explain [--workflow …]`: prints every effective setting with its source, `git config --show-origin`-style, per agent and per task (`model=claude-opus-4-8 ← agents.json:architect-opus`). [§1.2]
- FR-S7 Freeze at run start: fully-resolved settings (value + source per agent/task) logged as a `run.settings` event and persisted into `RunState`; `ao resume` reuses the frozen values instead of re-resolving. New `RunState` field needs a default so old `state.json` still loads. [§1.2, Known pitfalls]
- FR-S8 Empty-string-env rejection: distinguish *unset* from *empty*; reject `AO_*=""` explicitly rather than silently skipping (fixes the `AO_MAX_ATTEMPTS=""` falsy-sentinel bug). [Known pitfalls]
- FR-S9 `ao validate --strict`: warn when a workflow depends on ambient (invocation-level) model/effort — an opt-in lint, not mandatory boilerplate. [§1.2]
- FR-S10 Chain scope boundary: the chain governs model/effort/max_turns/max_attempts/timeout_seconds only; quota and budget stay run-scoped on the invocation chain. [resolved 3]
### Non-functional
- NFR-S1 Backward-compat: the new frozen-settings `RunState` field has a default (memory `persisted-model-fields-need-defaults`); old `state.json` loads unchanged.
- NFR-S2 Deterministic resume: a resumed run is reproducible even if env/config changed since — driven by the frozen settings, not re-resolution.
- NFR-S3 No magic literals: default model/effort/turns and level names are named constants; sources are enumerated, not stringly-typed ad hoc.

## Candidate task list (high-level — NOT tickets yet; each ≤ 3 days)
- Precedence resolver: one `resolve_setting`/`SettingsResolver` returning value + source for each governed knob across the full chain; unit-tested precedence matrix. Replaces the per-`AgentSpec` clobber. (FR-S1, FR-S10)
- Fix the live clobber defect: invocation-level model/effort become fill-in only at the `cli.py` run + resume paths (~412-425, ~568-581); declared agent/workflow/task values are preserved. Regression test reproduces the downstream-runner opus-downgrade and asserts it no longer happens. (FR-S2)
- `--force-model` / `--force-effort` flags: explicit clobber-everything override under loud names; documented as the only way to override a declared value. (FR-S3)
- Workflow `defaults.model`/`effort`/`max_turns`: `workflow.schema.json` + models + resolver level; `defaults.model` beats plain `--model`. (FR-S4)
- Per-task `model`/`effort`: `TaskSpec` + schema + resolver top-of-chain. (FR-S5)
- Empty-string-env rejection: unset-vs-empty distinction for all `AO_*` runtime knobs; structured error on empty. (FR-S8)
- Freeze resolved settings into `RunState`: new `resolved_settings` field (value + source per agent/task) with backward-compat default; `run.settings` event; `ao resume` reuses frozen values. (FR-S7, NFR-S1, NFR-S2)
- `ao config explain [--workflow …]`: git-config-style effective-settings + source dump, per agent and per task. (FR-S6)
- `ao validate --strict`: warn when a workflow relies on ambient invocation-level model/effort. (FR-S9)
- Tests + docs refresh: resolver precedence matrix, clobber-defect regression, force-flag behavior, empty-env rejection, freeze/resume reproducibility; reconcile ADR-0003 → shipped, update the `project_config_three_layers` memory to the new chain.

## Dependencies between epics
- **Provides to `E-gd8m4x` (granular decomposition)**: per-task `model`/`effort` (FR-S5 / granular HLD E3) so step sessions run cheaper/smaller models than the planner. If FR-S5 lands late, `E-gd8m4x` falls back to extra `developer-step` agent entries in `agents.json`.
- **Coordinates with `E-rc7k2v` (run-control)**: both introduce a new `RunState` field requiring a backward-compat default — share the `persisted-model-fields-need-defaults` migration pattern; otherwise independent (can proceed in parallel).

## Risks and Dependencies
- R1: Fill-in semantics change observable behavior for anyone relying on `--model` clobbering agents; mitigate with `--force-model` + `ao config explain` + release note; the clobber was the defect, so this is intended.
- R2: Freezing settings into `RunState` must not break resume of pre-existing runs — default-valued field + resume path reads frozen-if-present-else-resolve (with a one-time warning).
- R3: Empty-string rejection could break CI that sets `AO_*=""` to mean "unset"; document the migration (unset the var instead of emptying it).
- R4: Source attribution in `ao config explain` must stay accurate as levels are added — enumerate sources centrally in the resolver, not per call site.

## Links
- ADR: `docs-md/adr/ADR-0003-settings-precedence-policy.md`
- Related HLD (consumer): `docs-md/granular-task-decomposition-hld.md` §5 E3
- LLD / sprint plan / task tickets: **separate phase** (not in this epic doc)
- Large outputs (if any): `output/E-st5p3q-settings-precedence-policy/`
