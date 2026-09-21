# ADR-0003 — Global settings & precedence policy (model, effort, limits)

- Status: **Accepted** (user approved recommendations 2026-07-09; resolved decisions below)
- Date: 2026-07-09
- Deciders: Avadhoot Divekar (user), Claude (architect role)
- Related: `project_config.py` (three-layer resolution, shipped) · memory `project_config_three_layers` · [`token-budgeting-hld.md`](../token-budgeting-hld.md) · Epic [`E-st5p3q-settings-precedence-policy`](../../meta/tickets/E-st5p3q-settings-precedence-policy/EPIC.md)

## Context

Runtime settings (model, effort, max_attempts, max_turns, quota waits, timeouts, budget) can plausibly live at many levels. Shipped today:

1. **CLI flag > env var (`AO_*`) > project config (`.ao/config.yaml`, discovered by cwd walk-up) > built-in default** — implemented in `_resolve_run_settings()` (cli.py).
2. **Per-agent** `model`/`effort`/`max_turns` in `agents.json` (`AgentSpec`).
3. Workflow file: only `defaults.retries`/`defaults.timeout_seconds` + per-task overrides + `budget`. **No** workflow-level model/effort yet. No per-*task* model/effort either.

Note: "current repo / working directory" as a level **already is** the project-config layer — `.ao/config.yaml` is found by walking up from cwd to the git root. It is not a fourth mechanism.

**Live defect that motivates this ADR**: a global `--model`/`AO_MODEL`/config `model` is applied by overwriting `model` on *every* `AgentSpec` (cli.py `spec.model = eff_model`). Deliberately-pinned per-agent models (e.g. a downstream runner's `architect-opus`, `reviewer-opus`) are silently downgraded by `ao run --model haiku`. The same holds for `effort`. This is precedence inversion: the most *specific* declaration loses to the most *generic* one.

## Decision

**Option 1 — one specificity chain + first-class observability**, rather than either unlimited layering or forced explicitness:

### 1.1 Single precedence rule: *more specific wins; within a level, invocation beats file*

```
task-level (workflow)            ← most specific, wins
  > agent-level (agents.json)
    > workflow-level defaults (workflow.json)
      > invocation level:  CLI flag > env var > .ao/config.yaml
        > built-in default
```

- Add `model` / `effort` / `max_turns` to workflow `defaults` (author intent for the whole workflow) and optionally to `TaskSpec` (pin one expensive step).
- Invocation-level settings become **fill-in defaults, not overrides**: they apply only where no agent/workflow/task-level value is declared. This fixes the live defect.
- A **forced override stays available but must say so**: `--force-model X` (or `--model-override`) keeps today's clobber-everything behavior for the legitimate "rerun the whole thing cheap on haiku" case — explicit flag name = no silent surprise.

### 1.2 Confusion is solved by observability, not by removing layers

- **`ao config explain [--workflow …]`**: prints every effective setting with its source, `git config --show-origin`-style — per agent and per task (`model=claude-opus-4-8 ← agents.json:architect-opus`, `max_attempts=2 ← env AO_MAX_ATTEMPTS`).
- **Freeze at run start**: the fully-resolved settings (value + source for each) are logged as a `run.settings` event and persisted into `RunState`. `ao resume` reuses the frozen values instead of re-resolving — a run is reproducible even if env/config changed since.
- **`ao validate --strict`** warns when a workflow depends on ambient (invocation-level) values for model/effort — the middle ground that gives the "explicit workflows" benefit as an opt-in lint rather than mandatory boilerplate.

## Alternatives considered

- **Option 2 — always-explicit workflows** (every workflow declares model/effort or an explicit `inherit` marker; user-proposed). Pros: self-contained, reproducible-by-reading. Cons: `inherit: env` re-encodes the same precedence chain with extra typing (the confusion moves, it doesn't die); workflow files become environment-coupled and less portable across repos/plans; templates/scaffolds (epic-runner generates workflow.json) must grow knobs anyway. Captured instead as the `ao validate --strict` lint in 1.2.
- **Option 3 — flatten to two layers** (workflow file + CLI only; drop env/config for runtime knobs). Simplest mental model but breaks CI ergonomics (env is how CI configures), regresses shipped `.ao/config.yaml` behavior, and makes shared-machine defaults impossible.
- **Option 4 — status quo** (invocation clobbers agents). Rejected: the downstream-runner opus-agents downgrade is a real silent-wrong-result case.

## Known pitfalls this policy must handle (from repo history)

- Empty-string env values are falsy sentinels (`AO_MAX_ATTEMPTS=""` silently skipped injection — memory/learning). Resolution must distinguish *unset* from *empty* and reject empty explicitly.
- Resume semantics: without freezing (1.2), a resumed run can silently continue under different settings than it started with.
- Per-agent vs global conflicts (the live defect above).
- New `RunState` field for frozen settings needs a default for old `state.json` (memory `persisted-model-fields-need-defaults`).

## Resolved decisions (user accepted 2026-07-09)

1. **Workflow-file `defaults.model` beats a plain CLI `--model`** (author intent > invocation default); `--force-model` / `--force-effort` are the explicit, loud override for the "rerun everything cheap" case. Decided once — not to be revisited.
2. **Per-*task* `model`/`effort` is in scope** (needed by [`granular-task-decomposition-hld.md`](../granular-task-decomposition-hld.md) §5 E3 for cheap step sessions); may land as a later phase within the settings epic if sizing demands, but it is a tracked requirement, not a maybe.
   - **Shipped** (2026-08-28): `TaskSpec.model`/`effort`/`max_turns` land as task-level overrides, resolved once at dispatch by `models.resolve_effective_agent` (task > agent, fill-in per-field, never a clobber) — see epic `E-Tk7Qp2` / `meta/tickets/E-Tk7Qp2-per-task-effort/`. A new `"xhigh"` effort tier (→120 `--max-turns`) ships alongside it. The routed-runner template's `task-breakdown` stage uses it to size fan-out tasks. `--force-model`/`--force-effort` and the invocation-level fill-in-not-override fix (1.1) remain NOT done — the live global-clobber defect (cli.py `spec.model = eff_model`) is untouched by this slice; task-level values win regardless since the clobber loop never touches `TaskSpec` fields.
3. **The chain governs: model, effort, max_turns, max_attempts, timeout_seconds.** Quota and budget settings stay run-scoped on the invocation chain only (they guard the wallet, not a task).
