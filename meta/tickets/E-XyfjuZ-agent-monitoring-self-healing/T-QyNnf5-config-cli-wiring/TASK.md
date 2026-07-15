# TASK: T-QyNnf5-config-cli-wiring

## Metadata
- Task ID: `T-QyNnf5-config-cli-wiring`
- Epic ID: `E-XyfjuZ-agent-monitoring-self-healing`
- Owner: dev-epic agent (self-implemented)
- Created: 2026-07-14
- Last Updated: 2026-07-14
- Status: Draft
- Estimate: 0.5 day

## Requirements Mapping
- Requirement IDs: FR-5, FR-8

## Description
Add `MonitoringConfig` (new pydantic model in `project_config.py`) with fields: `self_heal: bool
= False`, `monitor: str = "rules"`, `max_extensions_per_breaker: int = 1`,
`max_heal_retries_per_task: int = 1`, `max_monitor_calls_per_run: int = 10`,
`heal_wait_seconds: float = 30.0`, `transient_patterns: list[str] = []` (additive to
`monitoring.DEFAULT_TRANSIENT_PATTERNS`, never replacing). Add `ProjectConfig.monitoring:
MonitoringConfig = MonitoringConfig()`.

Add `_resolve_monitoring_settings(self_heal_flag: bool) -> MonitoringConfig` to `cli.py` (mirrors
`_resolve_run_settings`'s discovery/precedence pattern per Design Decision D7 — monotonic-enable
for `self_heal` only: CLI flag OR env `AO_SELF_HEAL` OR config all independently can turn it on;
`monitor`/bounds/patterns are config-file-only, no CLI/env, per "don't explode flag count"). Add
`_build_monitor(monitor_name, agent_map, store, executor) -> Monitor` (returns `RuleBasedMonitor`
for `"rules"`; looks up `agent_map[monitor_name]` and constructs `AgentMonitor` otherwise; clean
`typer.Exit(1)` error if the name isn't found in `agent_map`).

Wire both into `ao run` and `ao resume` (one new CLI option: `--self-heal`, boolean, default
False; help text documents the env var). No new subcommand (FR-8).

## Acceptance Criteria
1. `.ao/config.yaml` with a `monitoring:` block loads via `ProjectConfig` without error; an
   absent `monitoring:` block defaults to `MonitoringConfig()` (all-defaults, byte-identical to
   pre-epic).
2. `ao run --self-heal` / `AO_SELF_HEAL=1` / config `monitoring.self_heal: true` each
   independently enable self-heal (verified by 3 separate CliRunner invocations).
3. `ao run`/`ao resume` with `monitoring.monitor: <unknown-agent-name>` in config exits 1 with a
   clear error naming the missing agent (before any task dispatch).
4. `ao run`/`ao resume` with `monitoring.monitor: rules` (or omitted) constructs a
   `RuleBasedMonitor` seeded with `heal_wait_seconds`/`transient_patterns` from config.
5. No new `ao` subcommand exists; `ao --help` output is unchanged apart from the new `--self-heal`
   option under `run`/`resume`.
6. `ao init`'s scaffolded `.ao/config.yaml` template gains a commented `monitoring:` section
   documenting the available keys (mirrors the existing commented style for budget/quota knobs).

## Risks
- Low: purely additive config + CLI option; the main risk is drift between `_resolve_run_settings`
  style and the new `_resolve_monitoring_settings` — mitigated by keeping them structurally
  parallel and explicitly documenting the D7 simplification in code comments.

## Dependencies
- `T-h2XLxe-monitor-abstraction` (needs `RuleBasedMonitor`/`AgentMonitor`/
  `DEFAULT_TRANSIENT_PATTERNS`).
- `T-TdildW-breaker-consult-engine` + `T-yjtdAq-self-heal-engine` (needs the final
  `Orchestrator.__init__` kwarg names to wire into `run`/`resume`).

## Pseudocode / Algorithm
```text
class MonitoringConfig(BaseModel):
    self_heal: bool = False
    monitor: str = "rules"
    max_extensions_per_breaker: int = 1
    max_heal_retries_per_task: int = 1
    max_monitor_calls_per_run: int = 10
    heal_wait_seconds: float = 30.0
    transient_patterns: list[str] = []

def _resolve_monitoring_settings(self_heal_flag: bool) -> MonitoringConfig:
    cfg = load project config (if any)
    base = cfg.monitoring if cfg else MonitoringConfig()
    env_self_heal = os.environ.get("AO_SELF_HEAL", "").strip().lower() in ("1", "true", "yes")
    resolved = self_heal_flag or env_self_heal or base.self_heal
    return base.model_copy(update={"self_heal": resolved})

def _build_monitor(name, agent_map, store, executor) -> Monitor:
    if name == "rules": return RuleBasedMonitor(...)
    if name not in agent_map: typer.echo(...); raise typer.Exit(1)
    return AgentMonitor(agent_map[name], executor, store, name=name)
```

## Schemas / Interface Notes
- Interface / API: `_resolve_monitoring_settings`, `_build_monitor` (cli.py, private).
- Spec / data schema (JSON/YAML): `.ao/config.yaml` `monitoring:` block (documented in
  `project_config.py` docstrings + the `ao init` template).
- Triggers / events (cron/event): N/A.
- Artifacts (inputs/outputs by path): N/A.

## Handoff Boundary
- Upstream: `T-h2XLxe`, `T-TdildW`, `T-yjtdAq`.
- Downstream: `T-Wx8vUq` covers CliRunner e2e tests for all acceptance criteria above.

## Artifacts
- Docs/comments: `meta/tickets/E-XyfjuZ-agent-monitoring-self-healing/T-QyNnf5-config-cli-wiring/`
- Large outputs: none.
