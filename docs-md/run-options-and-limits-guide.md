# Run options, limits and the dashboard (guide)

Covers epic E-aYG5vn: one registry of run options, the dashboard forms and "Limits & settings"
panel built from it, the `effort` → `turn_budget` rename, and provider-neutral `autocompact`.
Behaviour described here is what ships today; the gated final flip (T-9ymKja) is called out
explicitly as **not yet done**.

## Vocabulary

| Term | Meaning | CLI | Env | Config / spec key |
|---|---|---|---|---|
| **Thinking effort** | model reasoning depth: `low`…`max`. Unset = agent default. claude-cli only. | `--thinking-effort` | `AO_THINKING_EFFORT` | `thinking_effort` |
| **Turn budget** | max-turn *tier*: low=15, medium=30, high=60, xhigh=120 turns | `--turn-budget` | `AO_TURN_BUDGET` | `turn_budget` |
| **Max turns** | explicit integer; overrides the turn budget | `--max-turns` | `AO_MAX_TURNS` | `max_turns` |
| **Autocompact** | `auto` or a context-token threshold 100000–1000000. claude-cli only | `--autocompact` | `AO_AUTOCOMPACT` | `autocompact` |

Precedence at run level is always CLI > env > `.ao/config.yaml` > default. For agents/tasks the
spec key wins in the order task > agent > run level.

## Launching from the dashboard

* **New run**, **From template** and the **Resume** panel render the same schema-driven form
  (`GET /api/schema/run-options`; the allow-list behind `POST /api/runs`,
  `POST /api/templates/{name}/instances` and `POST /api/runs/{id}/resume` is derived from the same
  registry). A body key that is not a registry option is dropped, never forwarded to argv.
* Options the executor does not support are labelled "applies to: claude-cli" and warned about in
  the run log, never fatal.
* Every registry option is also a CLI flag on `ao run` (checked by a test); the dashboard adds no
  setting the CLI lacks.

## Reading limits on a run

* **Limits & settings** (run page): each effective setting + its source, and each limit with
  consumption vs cap. `settings_recorded` is `false` for runs started before the snapshot existed
  (the panel shows "—"; nothing is invented).
* **Resume history**: `state.json` keeps `effective_settings` (latest) and
  `effective_settings_history[]` (one entry per `start`/`resume`, each with its full settings), so a
  resume with changed options shows what the earlier invocation used.
* **Task panel / Now running**: per task the limits in force at its latest dispatch
  (`effective_limits`): thinking effort, turn budget, max turns, autocompact, timeout, attempts.
  `max_turns` is the cap actually applied (explicit value, else derived from the tier).

## Migration: `effort` → `turn_budget`

Until now `effort` (key, `--effort`, `AO_EFFORT`) meant the max-turn tier. That tier is now called
**turn budget**, because *effort* is going to mean **thinking effort**.

**This release (current behaviour)**

* `turn_budget` / `--turn-budget` / `AO_TURN_BUDGET` are canonical.
* `effort` / `--effort` / `AO_EFFORT` still work and still mean the turn budget, with a
  deprecation warning. Setting both to different values is an error; the same value is accepted.
* `ao status` / `ao usage` / the dashboard Usage view group by turn budget and thinking effort.

**What you should do**

```bash
ao migrate effort-keys --workspace .            # dry run: prints the diff, changes nothing
ao migrate effort-keys --workspace . --apply    # rewrites effort -> turn_budget (idempotent)
```

It renames the exact key `effort` (never `thinking_effort` / `overseer_effort`; commented YAML lines
are skipped) as a textual edit that preserves formatting, in `.ao/config.yaml`, `.ao/overlays/**`,
`specs/**` and `workflows/**` (`.json`/`.yaml`/`.yml`/`.tmpl`, including scaffolded run instances);
it skips `.orchestrator/`, `outputs/` and `.git`. A mapping that already holds *both* `effort` and
`turn_budget` is reported as a CONFLICT, left alone, and the command exits 1. A second `--apply`
finds nothing to do. Change any `AO_EFFORT` env vars / CI variables and `--effort` flags to the
`turn_budget` equivalents by hand — the command does not see those.

**Not done yet (T-9ymKja, blocked by an owner gate)** — after every workspace is migrated and one
deprecation release has shipped, `effort` / `--effort` / `AO_EFFORT` will become aliases of
**thinking effort**. Do not rely on the old meaning past that release.

Template note: the built-in `routed-runner` / `overseer-runner` still emit the legacy `effort` key
on injected tasks (and `overseer_effort` is a turn-budget tier); they are covered by the same alias
until the flip.

## Tests

`tests/ui/test_e2e_run_options_dashboard.py` drives a real `ProcessSupervisor` + real `ao run` /
`ao resume` children (fake executor, temp workspace) through the API for New Run, Template Launch
and Resume, and `CliRunner` for the flags, deprecation, `ao migrate effort-keys` and the CLI/
schema parity check. `tests/test_run_options.py` and `tests/test_turn_budget_autocompact.py` cover
the registry and the alias matrix.
