"""Typer-based CLI for the agent orchestrator.

Commands:
  ao validate   — Validate workflow, reposets, and agents specs.
  ao run        — Run a workflow from scratch.
  ao resume     — Resume a previously interrupted run.
  ao status     — Show current status of a run.
  ao init       — Scaffold a per-project .ao/config.yaml.
  ao prune      — Remove stale run artifacts from a workspace.
  ao ui         — Serve the browser dashboard (needs the optional `ui` extra).
  ao templates  — List discovered workflow templates (E-Tpl3x9).
  ao new        — Scaffold (and optionally validate/run) a workflow instance from a template.

Options:
  ao --version / -V         — Show version (+ commit/build info for non-release builds).
  ao --verbose / -v         — Debug-level logging.
  ao --quiet / -q           — Warnings/errors only (mutually exclusive with --verbose).
  ao --install-completion   — Install shell completion for the current shell.
  ao --show-completion      — Print the completion script for the current shell.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from .service.cli import app as service_app

if TYPE_CHECKING:
    from .artifacts import ArtifactStore
    from .executors.base import Executor
    from .models import BudgetSpec
    from .monitoring import Monitor
    from .project_config import MonitoringConfig, ProjectConfig

app = typer.Typer(name="ao", help="Agent Orchestrator CLI", add_completion=True)
app.add_typer(service_app, name="service")

_PACKAGE_LOGGER = "agent_orchestrator"


def _version_callback(value: bool) -> None:
    if value:
        from ._version import get_version_string

        typer.echo(get_version_string())
        raise typer.Exit(0)


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show the ao version and exit.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable debug-level logging (default is info-level).",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Suppress info-level logging; show only warnings/errors.",
    ),
) -> None:
    """Agent Orchestrator CLI."""
    if verbose and quiet:
        typer.echo("ERROR: --verbose and --quiet are mutually exclusive", err=True)
        raise typer.Exit(1)
    # Set the level explicitly only when asked; otherwise leave it untouched so
    # logging_setup.attach_run_handler's own NOTSET-guarded INFO default applies.
    if verbose:
        logging.getLogger(_PACKAGE_LOGGER).setLevel(logging.DEBUG)
    elif quiet:
        logging.getLogger(_PACKAGE_LOGGER).setLevel(logging.WARNING)


def _resolve_config_defaults(
    workflow: str | None,
    reposets: str | None,
    agents: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Merge CLI args, env vars, and project-config file with correct precedence.

    Precedence (highest to lowest):
      CLI flag > environment variable > project config file

    Returns:
        Tuple of (workflow, reposets, agents) with the highest-priority value
        for each.  Values may still be ``None`` if nothing supplies them.
    """
    from .project_config import (
        apply_project_config_env,
        find_project_config,
        load_project_config,
    )

    # 1. Try to find and load a project config.
    cfg = None
    config_path = find_project_config()
    if config_path is not None:
        try:
            cfg = load_project_config(config_path)
            apply_project_config_env(cfg)
        except Exception as exc:  # ConfigError or unexpected parse failure
            typer.echo(f"WARNING: could not load project config {config_path}: {exc}", err=True)
            cfg = None

    # 2. Merge: CLI > env > project config.
    resolved_workflow = workflow or os.environ.get("AO_WORKFLOW") or (cfg.workflow if cfg else None)
    resolved_reposets = reposets or os.environ.get("AO_REPOSETS") or (cfg.reposets if cfg else None)
    resolved_agents = agents or os.environ.get("AO_AGENTS") or (cfg.agents if cfg else None)

    return resolved_workflow, resolved_reposets, resolved_agents


def _load_all(
    workflow: str | None,
    reposets: str | None,
    agents: str | None,
) -> tuple:
    """Resolve defaults, then load and cross-validate workflow, reposets, and agents."""
    from .config import load_agents, load_reposets
    from .dag import build_dag
    from .spec import cross_validate, load_workflow, validate_run_control

    workflow_path, rp, ap = _resolve_config_defaults(workflow, reposets, agents)

    if not workflow_path:
        typer.echo(
            "ERROR: --workflow is required"
            " (or set AO_WORKFLOW, or add 'workflow' to .ao/config.yaml)",
            err=True,
        )
        raise typer.Exit(1)
    if not rp:
        typer.echo(
            "ERROR: --reposets or AO_REPOSETS required (or add 'reposets' to .ao/config.yaml)",
            err=True,
        )
        raise typer.Exit(1)
    if not ap:
        typer.echo(
            "ERROR: --agents or AO_AGENTS required (or add 'agents' to .ao/config.yaml)",
            err=True,
        )
        raise typer.Exit(1)

    wf = load_workflow(workflow_path)
    reposet_map = load_reposets(rp)
    agent_map = load_agents(ap)
    # cross_validate's return is the isolation/integration/scheduling rules' non-fatal
    # warnings (V4/V5/V7/V10, E-Wk9Tz3 C-3) -- printed the same way validate_run_control's
    # own warnings are printed just below, so both share one visible convention.
    for warning in cross_validate(wf, reposet_map, agent_map):
        typer.echo(f"WARNING: {warning}", err=True)

    # Routing + circuit-breaker static validation (LLD §4.4, epic E-rc7k2v).
    # Must run AFTER build_dag: needs the runtime graph (declared + inferred
    # edges) for reachability/cone computation. topological_order() surfaces
    # CycleError here too (previously only caught inside `ao run`/`resume`).
    graph = build_dag(wf)
    graph.topological_order()
    for warning in validate_run_control(wf, graph):
        typer.echo(f"WARNING: {warning}", err=True)

    return wf, reposet_map, agent_map


def _print_state(state) -> None:
    from .models import compute_run_usage_totals

    typer.echo(f"\nRun:    {state.run_id}")
    typer.echo(f"Status: {state.status}")
    typer.echo(
        f"\n{'Task':<30} {'Status':<15} {'Route':<20} {'Attempts':<9} "
        f"{'Tokens (in/out)':<20} {'Cost($)'}"
    )
    typer.echo("-" * 110)
    for tid, ts in state.tasks.items():
        route = ts.route or ""
        tokens = f"{ts.cumulative_input_tokens}/{ts.cumulative_output_tokens}"
        typer.echo(
            f"{tid:<30} {ts.status:<15} {route:<20} {ts.attempts:<9} "
            f"{tokens:<20} {ts.cumulative_cost_usd:.4f}"
        )
    # Routing + circuit-breaker observability trailer (LLD §10.2, FR-CB4).
    for tb in getattr(state, "tripped_breakers", []):
        typer.echo(f"Tripped breakers: {tb.id} ({tb.condition}, action={tb.action})")
    # Run-wide actual usage totals (E-9h3m7k FR-3) — real values, not estimates.
    totals = compute_run_usage_totals(state)
    typer.echo(
        f"\nTotal tokens: in={totals.input_tokens} out={totals.output_tokens} "
        f"cache_creation={totals.cache_creation_input_tokens} "
        f"cache_read={totals.cache_read_input_tokens}"
    )
    typer.echo(f"Total cost:   ${totals.cost_usd:.4f}")


def _print_status_snapshot(snap: dict) -> None:
    """Print a status.json snapshot in the same table format as _print_state."""
    typer.echo(f"\nRun:          {snap.get('run_id', '')}")
    typer.echo(f"Status:       {snap.get('status', '')}")
    current = snap.get("current_task")
    if current:
        typer.echo(f"Current task: {current}")
    typer.echo(
        f"\n{'Task':<30} {'Status':<15} {'Route':<20} {'Attempts':<9} "
        f"{'Tokens (in/out)':<20} {'Cost($)'}"
    )
    typer.echo("-" * 110)
    for task_entry in snap.get("tasks", []):
        route = task_entry.get("route") or ""
        tokens = f"{task_entry.get('input_tokens', 0)}/{task_entry.get('output_tokens', 0)}"
        typer.echo(
            f"{task_entry.get('id', ''):<30} "
            f"{task_entry.get('status', ''):<15} "
            f"{route:<20} "
            f"{task_entry.get('attempts', 0):<9} "
            f"{tokens:<20} "
            f"{task_entry.get('cost_usd', 0.0):.4f}"
        )
    # Routing + circuit-breaker observability trailer (LLD §10.2, FR-CB4).
    for tb in snap.get("tripped_breakers", []):
        typer.echo(f"Tripped breakers: {tb['id']} ({tb['condition']}, action={tb['action']})")
    # Run-wide actual usage totals (E-9h3m7k FR-3) — real values, not estimates.
    totals = snap.get("usage_totals") or {}
    typer.echo(
        f"\nTotal tokens: in={totals.get('input_tokens', 0)} out={totals.get('output_tokens', 0)} "
        f"cache_creation={totals.get('cache_creation_input_tokens', 0)} "
        f"cache_read={totals.get('cache_read_input_tokens', 0)}"
    )
    typer.echo(f"Total cost:   ${totals.get('cost_usd', 0.0):.4f}")


def _load_project_config_or_none() -> ProjectConfig | None:
    """Discover + load the nearest project config, swallowing any error to `None`.

    Shared by `_resolve_run_settings` and `_resolve_monitoring_settings` — previously each
    had its own verbatim copy of this find-then-load-then-swallow block (late-gate reviewer
    finding; CLAUDE.md's DRY rule: "no duplicate logic — extract to a function... if logic
    appears twice"). Each resolver still calls this once per `ao run`/`ao resume` invocation
    (two calls total per command, same as before) — this extraction removes the duplicated
    *logic*, not the duplicated *call*; a single-parse-per-command optimization would need a
    shared cache, which isn't worth the added state for a small, local config file read.
    """
    from .project_config import find_project_config, load_project_config

    config_path = find_project_config()
    if config_path is None:
        return None
    try:
        return load_project_config(config_path)
    except Exception:
        return None


# Env var carrying the workspace-scoped general-instruction list, os.pathsep-separated
# (":" on POSIX, ";" on Windows) — the same convention PATH/PYTHONPATH use, so shells and
# CI systems can compose it without inventing a project-specific delimiter.
ENV_GENERAL_INSTRUCTIONS = "AO_GENERAL_INSTRUCTIONS"


def resolve_general_instructions(
    cli_paths: list[str] | None,
    workflow_paths: list[str] | None = None,
) -> list[str]:
    """Build the effective general-instruction path list for a run (E-Ui7Kq2 FR-GI1).

    General instructions are **additive across every layer**, not an override chain — this
    is the one setting in AO that deliberately does NOT follow the CLI > env > config
    precedence of `_resolve_run_settings`. The user-facing contract is "define them once in
    the workspace and every task gets them regardless"; a precedence chain would mean a
    single `--general-instruction` on the command line silently *dropped* the workspace's
    house rules, which is the opposite of that contract.

    Merge order (stable, first occurrence wins on duplicates):
        project config -> env var -> CLI flags -> workflow spec

    Ordering is "broadest scope first" so workspace-wide rules are presented to the agent
    before workflow-specific ones. De-duplication is by resolved path string, so the same
    file named twice across layers is passed to the agent only once.

    Args:
        cli_paths: Values of repeated ``--general-instruction`` flags (may be None/empty).
        workflow_paths: ``WorkflowSpec.general_instructions`` (may be None/empty).

    Returns:
        De-duplicated, order-preserving list of instruction paths. Empty list when no layer
        declares any — which keeps every pre-epic run byte-identical.
    """
    cfg = _load_project_config_or_none()

    env_raw = os.environ.get(ENV_GENERAL_INSTRUCTIONS, "")
    env_paths = [p for p in env_raw.split(os.pathsep) if p.strip()]

    merged: list[str] = []
    seen: set[str] = set()
    for layer in (
        list(cfg.general_instructions) if cfg else [],
        env_paths,
        list(cli_paths or []),
        list(workflow_paths or []),
    ):
        for path in layer:
            candidate = path.strip()
            if candidate and candidate not in seen:
                seen.add(candidate)
                merged.append(candidate)
    return merged


def _apply_prompt(
    wf,  # WorkflowSpec — untyped to keep this module's lazy-import style
    store: ArtifactStore,
    prompt: str | None,
    prompt_file: str | None,
) -> None:
    """Write ``--prompt`` / ``--prompt-file`` content into the workflow's prompt artifact.

    This is the per-RUN input path (E-Ui7Kq2 FR-P1) and is deliberately separate from
    general instructions (per-WORKSPACE): the prompt says *what this run should do*, the
    general instructions say *how every task in this workspace should behave*.

    The destination is ``WorkflowSpec.prompt_path``, resolved through the artifact store so
    it is path-guarded to the workspace root exactly like every other artifact. A workflow
    consumes the prompt by listing that same path in a task's ``inputs``.

    No-ops when neither flag is given, so a workflow with a hand-written prompt file (the
    ``meta/ao/epics/<id>/prompt.md`` convention) keeps working untouched.

    Raises:
        typer.Exit: If both flags are given, if the workflow declares no ``prompt_path``,
            or if ``--prompt-file`` points at an unreadable file.
    """
    if prompt is None and prompt_file is None:
        return

    if prompt is not None and prompt_file is not None:
        typer.echo("ERROR: --prompt and --prompt-file are mutually exclusive", err=True)
        raise typer.Exit(1)

    if not wf.prompt_path:
        typer.echo(
            f"ERROR: workflow '{wf.id}' declares no `prompt_path`, so there is nowhere to "
            'write the prompt. Add `"prompt_path": "<path>"` to the workflow spec and '
            "list that same path in the inputs of the task that should consume it.",
            err=True,
        )
        raise typer.Exit(1)

    if prompt_file is not None:
        try:
            text = Path(prompt_file).read_text(encoding="utf-8")
        except OSError as exc:
            typer.echo(f"ERROR: cannot read --prompt-file {prompt_file}: {exc}", err=True)
            raise typer.Exit(1)
    else:
        text = prompt or ""

    from .errors import ArtifactPathError

    try:
        dest = Path(store.resolve(wf.prompt_path))
    except ArtifactPathError as exc:
        typer.echo(f"ERROR: invalid workflow prompt_path: {exc}", err=True)
        raise typer.Exit(1)

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    typer.echo(f"Wrote prompt ({len(text)} chars) to {dest}")


def _resolve_run_settings(
    max_attempts: int | None,
    max_turns: int | None,
    model: str | None,
    effort: str | None,
    quota_max_wait: int | None,
    quota_poll_interval: int | None,
    max_parallel: int | None,
) -> tuple[int | None, int | None, str | None, str | None, int, int, int]:
    """Merge CLI flags, env vars, and project config for runtime execution settings.

    Precedence (highest to lowest): CLI flag > env var > project config > built-in default.
    Returns (max_attempts, max_turns, model, effort, quota_max_wait_seconds, quota_poll_seconds,
    max_parallel).
    """
    from .models import (
        DEFAULT_MAX_PARALLEL,
        DEFAULT_QUOTA_MAX_WAIT_SECONDS,
        DEFAULT_QUOTA_POLL_SECONDS,
    )

    def _int_env(name: str) -> int | None:
        v = os.environ.get(name)
        try:
            return int(v) if v else None
        except ValueError:
            return None

    cfg = _load_project_config_or_none()

    resolved_max_attempts = (
        max_attempts or _int_env("AO_MAX_ATTEMPTS") or (cfg.max_attempts if cfg else None)
    )
    resolved_max_turns = max_turns or _int_env("AO_MAX_TURNS") or (cfg.max_turns if cfg else None)
    resolved_model = model or os.environ.get("AO_MODEL") or (cfg.model if cfg else None)
    resolved_effort = effort or os.environ.get("AO_EFFORT") or (cfg.effort if cfg else None)
    resolved_quota_max_wait = int(
        quota_max_wait
        or _int_env("AO_QUOTA_MAX_WAIT_SECONDS")
        or (cfg.quota_max_wait_seconds if cfg else None)
        or DEFAULT_QUOTA_MAX_WAIT_SECONDS
    )
    resolved_quota_poll = int(
        quota_poll_interval
        or _int_env("AO_QUOTA_POLL_SECONDS")
        or (cfg.quota_poll_seconds if cfg else None)
        or DEFAULT_QUOTA_POLL_SECONDS
    )
    # NOTE: like every other `or`-chain above, a falsy 0 here is indistinguishable from
    # "unset" and falls through to DEFAULT_MAX_PARALLEL (=1) -- consistent with how
    # --quota-max-wait/--max-attempts 0 already behave in this function. A negative value is
    # truthy in Python so it survives the chain unchanged and is what the guard below rejects.
    resolved_max_parallel = int(
        max_parallel
        or _int_env("AO_MAX_PARALLEL")
        or (cfg.max_parallel if cfg else None)
        or DEFAULT_MAX_PARALLEL
    )
    if resolved_max_parallel < 1:
        typer.echo("ERROR: --max-parallel must be >= 1", err=True)
        raise typer.Exit(1)
    return (
        resolved_max_attempts,
        resolved_max_turns,
        resolved_model,
        resolved_effort,
        resolved_quota_max_wait,
        resolved_quota_poll,
        resolved_max_parallel,
    )


def _resolve_monitoring_settings(self_heal: bool | None) -> MonitoringConfig:
    """Merge CLI flag, env var, and project config for monitoring settings (E-XyfjuZ).

    `self_heal` uses the SAME tri-state CLI > env var > project config > built-in default
    precedence as every other runtime setting in `_resolve_run_settings` (revised from an
    earlier monotonic-enable-only draft per early-gate reviewer feedback: an operator must
    be able to force self-heal OFF via CLI/env even when the project config enables it,
    matching every other setting's override symmetry). Every other monitoring knob
    (`monitor` selection, bounds, `transient_patterns`) is config-file-ONLY — no CLI/env —
    per the epic brief's "config file is the primary surface; don't explode flag count."

    Returns the fully-resolved `MonitoringConfig` (never `None` — an absent project config
    resolves to `MonitoringConfig()`'s all-defaults, byte-identical to pre-epic behavior).
    """
    from .project_config import MonitoringConfig

    cfg = _load_project_config_or_none()
    base = cfg.monitoring if cfg else MonitoringConfig()

    def _bool_env(name: str) -> bool | None:
        v = os.environ.get(name)
        if not v:
            return None
        return v.strip().lower() in ("1", "true", "yes", "on")

    resolved_self_heal = self_heal
    if resolved_self_heal is None:
        resolved_self_heal = _bool_env("AO_SELF_HEAL")
    if resolved_self_heal is None:
        resolved_self_heal = base.self_heal

    return base.model_copy(update={"self_heal": resolved_self_heal})


def _build_monitor(
    cfg: MonitoringConfig, agent_map: dict, store: ArtifactStore, executor: Executor
) -> Monitor:
    """Construct the configured `Monitor` implementation (E-XyfjuZ).

    `cfg.monitor == "rules"` (default) -> `RuleBasedMonitor` seeded from `cfg`'s
    `heal_wait_seconds`/`transient_patterns`. Any other value -> looked up in *agent_map*
    (`agents.json`) and wrapped in `AgentMonitor`; a clean `typer.Exit(1)` if the name isn't
    a known agent (fails fast, before any task dispatch, mirroring `_load_all`'s existing
    validation style).
    """
    from .monitoring import AgentMonitor, RuleBasedMonitor

    if cfg.monitor == "rules":
        return RuleBasedMonitor(
            wait_seconds=cfg.heal_wait_seconds,
            extra_transient_patterns=cfg.transient_patterns,
        )
    if cfg.monitor not in agent_map:
        typer.echo(
            f"ERROR: monitoring.monitor {cfg.monitor!r} not found in agents "
            f"(known agents: {sorted(agent_map)})",
            err=True,
        )
        raise typer.Exit(1)
    return AgentMonitor(agent_map[cfg.monitor], executor, store, name=cfg.monitor)


def _build_effective_budget(
    wf_budget: BudgetSpec | None,
    budget_total: int | None,
    rate_tokens: int | None,
    rate_window: str | None,
    on_exhaustion_override: str | None,
    pessimism_buffer: float | None,
) -> BudgetSpec | None:
    """Build effective BudgetSpec by merging CLI flags over the workflow budget block.

    Precedence: CLI > spec > unset (no limit).
    Returns None if no budget is configured anywhere (no-op path).
    Raises typer.Exit(1) on invalid combinations.
    """
    from .errors import SpecValidationError
    from .models import BudgetSpec, EstimatorConfig, RateLimit
    from .spec import budget_cross_validate

    # Validate on_exhaustion value if provided
    if on_exhaustion_override is not None and on_exhaustion_override not in ("stop", "wait"):
        typer.echo(
            f"ERROR: --on-exhaustion must be 'stop' or 'wait', got '{on_exhaustion_override}'",
            err=True,
        )
        raise typer.Exit(1)

    # Start with spec as base (or empty defaults)
    base_total = wf_budget.total_tokens if wf_budget else None
    base_rate = wf_budget.rate if wf_budget else None
    base_on_exhaustion = wf_budget.on_exhaustion if wf_budget else "stop"
    base_estimator = wf_budget.estimator if wf_budget else EstimatorConfig()

    # CLI overrides (per-field, not whole-object replacement)
    effective_total = budget_total if budget_total is not None else base_total

    # Rate: CLI tokens + window override the spec rate if either is provided.
    # Precedence is per-field: if only one of the two is given on the CLI,
    # fall back to the spec rate for the missing piece.
    if rate_tokens is not None or rate_window is not None:
        resolved_rate_tokens = (
            rate_tokens
            if rate_tokens is not None
            else (base_rate.tokens if base_rate is not None else None)
        )
        resolved_rate_window = (
            rate_window
            if rate_window is not None
            else (base_rate.window if base_rate is not None else None)
        )
        if resolved_rate_tokens is None or resolved_rate_window is None:
            typer.echo(
                "ERROR: --rate-tokens and --rate-window must both be provided"
                " when specifying a rate limit via CLI",
                err=True,
            )
            raise typer.Exit(1)
        # Validate that the window value is a known literal before constructing RateLimit.
        # budget_cross_validate will catch this too, but a clearer message here is better.
        valid_windows = ("minute", "ten_minutes", "hour")
        if resolved_rate_window not in valid_windows:
            typer.echo(
                f"ERROR: --rate-window must be one of {valid_windows},"
                f" got '{resolved_rate_window}'",
                err=True,
            )
            raise typer.Exit(1)
        from typing import Literal, cast

        effective_rate: RateLimit | None = RateLimit(
            tokens=resolved_rate_tokens,
            window=cast(Literal["minute", "ten_minutes", "hour"], resolved_rate_window),
        )
    else:
        effective_rate = base_rate

    # Cast on_exhaustion to the expected Literal type after validation above.
    from typing import Literal, cast

    effective_on_exhaustion = cast(
        "Literal['stop', 'wait']",
        on_exhaustion_override if on_exhaustion_override is not None else base_on_exhaustion,
    )

    if pessimism_buffer is not None:
        effective_estimator = base_estimator.model_copy(
            update={"pessimism_buffer": pessimism_buffer}
        )
    else:
        effective_estimator = base_estimator

    # No-op path: no budget anywhere
    if effective_total is None and effective_rate is None:
        return None

    eff = BudgetSpec(
        total_tokens=effective_total,
        rate=effective_rate,
        on_exhaustion=effective_on_exhaustion,
        estimator=effective_estimator,
    )

    try:
        budget_cross_validate(eff)
    except SpecValidationError as e:
        typer.echo(f"ERROR: budget config invalid: {e}", err=True)
        raise typer.Exit(1)

    return eff


@app.command()
def validate(
    workflow: str | None = typer.Option(None, help="Path to workflow JSON/YAML"),
    reposets: str | None = typer.Option(None, help="Path to reposets config JSON/YAML"),
    agents: str | None = typer.Option(None, help="Path to agents config JSON/YAML"),
) -> None:
    """Validate workflow, reposets, and agents specs."""
    from .errors import OrchestratorError

    try:
        _load_all(workflow, reposets, agents)
        typer.echo("OK: all specs valid")
    except OrchestratorError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def run(
    workflow: str | None = typer.Option(None, help="Path to workflow JSON/YAML"),
    reposets: str | None = typer.Option(None, help="Path to reposets config JSON/YAML"),
    agents: str | None = typer.Option(None, help="Path to agents config JSON/YAML"),
    budget_total: int | None = typer.Option(
        None, "--budget-total", help="Total token budget for the run"
    ),
    rate_tokens: int | None = typer.Option(
        None, "--rate-tokens", help="Max tokens per rate window"
    ),
    rate_window: str | None = typer.Option(
        None, "--rate-window", help="Rate window: minute, ten_minutes, or hour"
    ),
    on_exhaustion: str | None = typer.Option(
        None, "--on-exhaustion", help="Action on budget exhaustion: stop (default) or wait"
    ),
    pessimism_buffer: float | None = typer.Option(
        None, "--pessimism-buffer", help="Estimator pessimism multiplier (default 1.3)"
    ),
    max_attempts: int | None = typer.Option(
        None,
        "--max-attempts",
        help=(
            "Max attempts per task (overrides workflow defaults.retries.max_attempts)."
            " Env: AO_MAX_ATTEMPTS"
        ),
    ),
    max_turns: int | None = typer.Option(
        None,
        "--max-turns",
        help=(
            "Max turns per claude agent invocation (overrides effort-derived value)."
            " Env: AO_MAX_TURNS"
        ),
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help="Claude model for all agents (e.g. claude-sonnet-4-6). Env: AO_MODEL",
    ),
    effort: str | None = typer.Option(
        None,
        "--effort",
        help="Effort level for all agents: low, medium, or high. Env: AO_EFFORT",
    ),
    quota_max_wait: int | None = typer.Option(
        None,
        "--quota-max-wait",
        help=(
            "Max seconds to wait during a Claude quota-exhaustion episode before failing"
            " (default 21600 = 6 h). Env: AO_QUOTA_MAX_WAIT_SECONDS"
        ),
    ),
    quota_poll_interval: int | None = typer.Option(
        None,
        "--quota-poll-interval",
        help=(
            "Seconds to sleep between quota-exhaustion re-run attempts"
            " (default 900 = 15 min). Env: AO_QUOTA_POLL_SECONDS"
        ),
    ),
    max_parallel: int | None = typer.Option(
        None,
        "--max-parallel",
        help=(
            "Max independent ready tasks to run at once (default 1 = serial)."
            " Env: AO_MAX_PARALLEL. Config: max_parallel."
        ),
    ),
    self_heal: bool | None = typer.Option(
        None,
        "--self-heal/--no-self-heal",
        help=(
            "Enable/disable task-failure self-healing (Consult Point B, E-XyfjuZ);"
            " overrides env/config in either direction. Env: AO_SELF_HEAL (1/0)."
            " Default: off. Unrelated to recommend-mode breaker consult (Consult Point A),"
            " which activates purely from a workflow's own circuit_breakers[].mode."
        ),
    ),
    general_instruction: list[str] = typer.Option(
        [],
        "--general-instruction",
        help=(
            "PATH to an instruction file applied to EVERY task (repeatable). ADDITIVE, not an"
            " override: merged with .ao/config.yaml general_instructions,"
            f" {ENV_GENERAL_INSTRUCTIONS} (os.pathsep-separated), and the workflow's own"
            " general_instructions."
        ),
    ),
    prompt: str | None = typer.Option(
        None,
        "--prompt",
        help=(
            "Prompt text for this run; written to the workflow's declared `prompt_path`"
            " before the run starts. Requires the workflow to declare prompt_path."
        ),
    ),
    prompt_file: str | None = typer.Option(
        None,
        "--prompt-file",
        help=(
            "Read the run prompt from this file instead of --prompt; its contents are copied"
            " into the workflow's declared `prompt_path`. Mutually exclusive with --prompt."
        ),
    ),
) -> None:
    """Run a workflow from scratch."""
    from datetime import UTC
    from datetime import datetime as _dt

    from .artifacts import LocalFsArtifactStore
    from .budget import DefaultBudgetManager
    from .engine import Orchestrator
    from .errors import CycleError, OrchestratorError
    from .estimator import HeuristicTokenEstimator
    from .executors import DispatchExecutor
    from .runstate import RunStateStore

    try:
        wf, reposet_map, agent_map = _load_all(workflow, reposets, agents)
    except OrchestratorError as e:
        # Includes SpecValidationError/CycleError from the new build_dag +
        # validate_run_control pass in _load_all (LLD §4.4) — must exit non-zero,
        # not silently return (was previously unreachable here since _load_all
        # never built the DAG; cycles used to surface only from orch.run() below).
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)
    except SystemExit:
        return

    (
        eff_max_attempts,
        eff_max_turns,
        eff_model,
        eff_effort,
        eff_quota_max_wait,
        eff_quota_poll,
        eff_max_parallel,
    ) = _resolve_run_settings(
        max_attempts,
        max_turns,
        model,
        effort,
        quota_max_wait,
        quota_poll_interval,
        max_parallel,
    )

    if eff_max_attempts is not None:
        wf.defaults.retries.max_attempts = eff_max_attempts
    if eff_max_turns is not None:
        for spec in agent_map.values():
            spec.max_turns = eff_max_turns
    if eff_model is not None:
        for spec in agent_map.values():
            spec.model = eff_model
    if eff_effort is not None:
        _valid_efforts = ("low", "medium", "high")
        if eff_effort not in _valid_efforts:
            typer.echo(
                f"ERROR: --effort must be one of {_valid_efforts}, got '{eff_effort}'",
                err=True,
            )
            raise typer.Exit(1)
        from typing import Literal, cast

        for spec in agent_map.values():
            spec.effort = cast("Literal['low', 'medium', 'high']", eff_effort)

    workspace = os.environ.get("AO_WORKSPACE_ROOT") or reposet_map[wf.repo_set].workspace_root
    store = LocalFsArtifactStore(workspace)
    rs_store = RunStateStore(workspace, store)
    executor = DispatchExecutor()

    # Materialize the run prompt BEFORE the run starts, so the task that lists prompt_path
    # in its inputs sees the new text (and its missing-inputs gate passes).
    _apply_prompt(wf, store, prompt, prompt_file)

    effective_general_instructions = resolve_general_instructions(
        general_instruction, wf.general_instructions
    )

    # Build effective budget (CLI > spec > unset)
    effective_budget = _build_effective_budget(
        wf.budget, budget_total, rate_tokens, rate_window, on_exhaustion, pessimism_buffer
    )

    # Construct BudgetManager + estimator if budget is configured
    budget_manager = None
    estimator = None
    clock = None
    if effective_budget is not None:

        def _wall_clock() -> _dt:
            return _dt.now(UTC)

        clock = _wall_clock
        budget_manager = DefaultBudgetManager(effective_budget, clock)
        estimator = HeuristicTokenEstimator(store)

    # Agent-based monitoring & self-healing (E-XyfjuZ) — config-primary, minimal CLI surface.
    monitoring_cfg = _resolve_monitoring_settings(self_heal)
    monitor = _build_monitor(monitoring_cfg, agent_map, store, executor)

    orch = Orchestrator(
        executor,
        store,
        rs_store,
        budget_manager=budget_manager,
        estimator=estimator,
        clock=clock,
        quota_max_wait_seconds=eff_quota_max_wait,
        quota_poll_seconds=eff_quota_poll,
        monitor=monitor,
        max_extensions_per_breaker=monitoring_cfg.max_extensions_per_breaker,
        max_monitor_calls_per_run=monitoring_cfg.max_monitor_calls_per_run,
        self_heal_enabled=monitoring_cfg.self_heal,
        max_heal_retries_per_task=monitoring_cfg.max_heal_retries_per_task,
        max_parallel=eff_max_parallel,
        general_instructions=effective_general_instructions,
    )

    try:
        state = orch.run(wf, reposet_map, agent_map)
    except (OrchestratorError, CycleError) as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    _print_state(state)
    raise typer.Exit(0 if state.status == "succeeded" else 1)


@app.command()
def resume(
    run_id: str = typer.Option(..., help="Run ID to resume"),
    workflow: str | None = typer.Option(None, help="Path to workflow JSON/YAML"),
    reposets: str | None = typer.Option(None, help="Path to reposets config JSON/YAML"),
    agents: str | None = typer.Option(None, help="Path to agents config JSON/YAML"),
    budget_total: int | None = typer.Option(
        None, "--budget-total", help="Total token budget override"
    ),
    rate_tokens: int | None = typer.Option(
        None, "--rate-tokens", help="Max tokens per rate window override"
    ),
    rate_window: str | None = typer.Option(
        None, "--rate-window", help="Rate window override: minute, ten_minutes, or hour"
    ),
    on_exhaustion: str | None = typer.Option(
        None, "--on-exhaustion", help="Action on budget exhaustion: stop or wait"
    ),
    pessimism_buffer: float | None = typer.Option(
        None, "--pessimism-buffer", help="Estimator pessimism multiplier override"
    ),
    max_attempts: int | None = typer.Option(
        None,
        "--max-attempts",
        help=(
            "Max attempts per task override (overrides workflow defaults.retries.max_attempts)."
            " Env: AO_MAX_ATTEMPTS"
        ),
    ),
    max_turns: int | None = typer.Option(
        None,
        "--max-turns",
        help=(
            "Max turns per claude agent invocation (overrides effort-derived value)."
            " Env: AO_MAX_TURNS"
        ),
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help="Claude model for all agents (e.g. claude-sonnet-4-6). Env: AO_MODEL",
    ),
    effort: str | None = typer.Option(
        None,
        "--effort",
        help="Effort level for all agents: low, medium, or high. Env: AO_EFFORT",
    ),
    quota_max_wait: int | None = typer.Option(
        None,
        "--quota-max-wait",
        help="Max seconds to wait during a Claude quota-exhaustion episode before failing "
        "(default 21600 = 6 h). Env: AO_QUOTA_MAX_WAIT_SECONDS",
    ),
    quota_poll_interval: int | None = typer.Option(
        None,
        "--quota-poll-interval",
        help="Seconds to sleep between quota-exhaustion re-run attempts "
        "(default 900 = 15 min). Env: AO_QUOTA_POLL_SECONDS",
    ),
    max_parallel: int | None = typer.Option(
        None,
        "--max-parallel",
        help=(
            "Max independent ready tasks to run at once (default 1 = serial)."
            " Env: AO_MAX_PARALLEL. Config: max_parallel."
        ),
    ),
    extend_breaker: str | None = typer.Option(
        None,
        "--extend-breaker",
        help="Circuit breaker id (from workflow.circuit_breakers) to extend and un-latch on "
        "this resume. Requires exactly one of --extend-by-seconds/--extend-by-same. "
        "(E-3JTmVu FR-2d)",
    ),
    extend_by_seconds: float | None = typer.Option(
        None,
        "--extend-by-seconds",
        help="Amount (in the breaker condition's native unit -- seconds for time-based "
        "conditions, USD for cost conditions, a count otherwise) to add to --extend-breaker's "
        "current effective threshold. Mutually exclusive with --extend-by-same.",
    ),
    extend_by_same: bool = typer.Option(
        False,
        "--extend-by-same",
        help="Extend --extend-breaker's current effective threshold by the ORIGINAL "
        "threshold set at startup (spec.threshold) again. Mutually exclusive with "
        "--extend-by-seconds.",
    ),
    self_heal: bool | None = typer.Option(
        None,
        "--self-heal/--no-self-heal",
        help=(
            "Enable/disable task-failure self-healing (Consult Point B, E-XyfjuZ);"
            " overrides env/config in either direction. Env: AO_SELF_HEAL (1/0)."
            " Default: off."
        ),
    ),
    general_instruction: list[str] = typer.Option(
        [],
        "--general-instruction",
        help=(
            "PATH to an instruction file applied to EVERY task (repeatable). ADDITIVE, not an"
            " override: merged with .ao/config.yaml general_instructions,"
            f" {ENV_GENERAL_INSTRUCTIONS} (os.pathsep-separated), and the workflow's own"
            " general_instructions."
        ),
    ),
) -> None:
    """Resume a previously interrupted run.

    Note there is deliberately no ``--prompt`` here: the run prompt is a per-run INPUT
    artifact that the original ``ao run`` already materialized, and tasks that consumed it
    have their outputs on disk. Rewriting it mid-run would make the run irreproducible from
    its own artifacts (the resumability invariant). Start a new run to change the prompt.
    General instructions, by contrast, are resolved fresh on every invocation, so a workspace
    can add house rules and have a resumed run's remaining tasks pick them up.
    """
    from datetime import UTC
    from datetime import datetime as _dt

    from .artifacts import LocalFsArtifactStore
    from .breakers import apply_breaker_extension
    from .budget import DefaultBudgetManager
    from .engine import Orchestrator
    from .errors import OrchestratorError, SpecValidationError
    from .estimator import HeuristicTokenEstimator
    from .executors import DispatchExecutor
    from .logging_setup import attach_run_handler, get_run_logger
    from .runstate import RunStateStore

    try:
        wf, reposet_map, agent_map = _load_all(workflow, reposets, agents)
    except OrchestratorError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)
    except SystemExit:
        return

    workspace = os.environ.get("AO_WORKSPACE_ROOT") or reposet_map[wf.repo_set].workspace_root
    store = LocalFsArtifactStore(workspace)
    rs_store = RunStateStore(workspace, store)

    effective_general_instructions = resolve_general_instructions(
        general_instruction, wf.general_instructions
    )

    (
        eff_max_attempts,
        eff_max_turns,
        eff_model,
        eff_effort,
        eff_quota_max_wait,
        eff_quota_poll,
        eff_max_parallel,
    ) = _resolve_run_settings(
        max_attempts,
        max_turns,
        model,
        effort,
        quota_max_wait,
        quota_poll_interval,
        max_parallel,
    )

    if eff_max_attempts is not None:
        wf.defaults.retries.max_attempts = eff_max_attempts
    if eff_max_turns is not None:
        for spec in agent_map.values():
            spec.max_turns = eff_max_turns
    if eff_model is not None:
        for spec in agent_map.values():
            spec.model = eff_model
    if eff_effort is not None:
        _valid_efforts = ("low", "medium", "high")
        if eff_effort not in _valid_efforts:
            typer.echo(
                f"ERROR: --effort must be one of {_valid_efforts}, got '{eff_effort}'",
                err=True,
            )
            raise typer.Exit(1)
        from typing import Literal, cast

        for spec in agent_map.values():
            spec.effort = cast("Literal['low', 'medium', 'high']", eff_effort)

    try:
        existing = rs_store.load(run_id)
        existing = rs_store.prepare_resume(existing, wf)
    except FileNotFoundError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    # ---- Breaker extension (E-3JTmVu FR-2d) ----
    # Applied to the already-loaded/prepared `existing` RunState, BEFORE orch.run() -- the
    # mutation is persisted via orch.run()'s own save path (Orchestrator.run's first action
    # is self._runstate.save(state), engine.py ~:135) since it's the same object instance.
    if extend_breaker is not None:
        breaker_spec = next((b for b in wf.circuit_breakers if b.id == extend_breaker), None)
        if breaker_spec is None:
            typer.echo(
                f"ERROR: no circuit breaker with id {extend_breaker!r} in "
                f"workflow.circuit_breakers",
                err=True,
            )
            raise typer.Exit(1)

        run_dir = os.path.join(workspace, ".orchestrator", "runs", run_id)
        log_path = os.path.join(run_dir, "run.log")
        attach_run_handler(run_id, log_path)  # idempotent; orch.run() attaches the same pair
        extend_log = get_run_logger(run_id)

        def _extend_clock() -> _dt:
            return _dt.now(UTC)

        old_effective = existing.breaker_overrides.get(breaker_spec.id, breaker_spec.threshold)
        try:
            new_threshold = apply_breaker_extension(
                existing,
                breaker_spec,
                extend_by_seconds=extend_by_seconds,
                extend_by_same=extend_by_same,
                clock=_extend_clock,
                run_log=extend_log,
            )
        except (SpecValidationError, ValueError) as e:
            typer.echo(f"ERROR: {e}", err=True)
            raise typer.Exit(1)
        # Persist immediately (don't rely solely on orch.run()'s later save): later CLI-only
        # validation below (e.g. --on-exhaustion) can still typer.Exit(1) before orch.run() is
        # ever reached, which would otherwise silently discard an already-logged extension.
        rs_store.save(existing)
        typer.echo(f"Extended breaker {breaker_spec.id!r}: {old_effective} -> {new_threshold}")
    elif extend_by_seconds is not None or extend_by_same:
        typer.echo("ERROR: --extend-by-seconds/--extend-by-same require --extend-breaker", err=True)
        raise typer.Exit(1)

    # Build effective budget (CLI > spec > unset), using the original workflow budget
    effective_budget = _build_effective_budget(
        wf.budget, budget_total, rate_tokens, rate_window, on_exhaustion, pessimism_buffer
    )

    budget_manager = None
    estimator = None
    clock = None
    if effective_budget is not None:

        def _wall_clock() -> _dt:
            return _dt.now(UTC)

        clock = _wall_clock
        budget_manager = DefaultBudgetManager(effective_budget, clock)
        estimator = HeuristicTokenEstimator(store)

    executor = DispatchExecutor()

    # Agent-based monitoring & self-healing (E-XyfjuZ) — config-primary, minimal CLI surface.
    monitoring_cfg = _resolve_monitoring_settings(self_heal)
    monitor = _build_monitor(monitoring_cfg, agent_map, store, executor)

    orch = Orchestrator(
        executor,
        store,
        rs_store,
        budget_manager=budget_manager,
        estimator=estimator,
        clock=clock,
        quota_max_wait_seconds=eff_quota_max_wait,
        quota_poll_seconds=eff_quota_poll,
        monitor=monitor,
        max_extensions_per_breaker=monitoring_cfg.max_extensions_per_breaker,
        max_monitor_calls_per_run=monitoring_cfg.max_monitor_calls_per_run,
        self_heal_enabled=monitoring_cfg.self_heal,
        max_heal_retries_per_task=monitoring_cfg.max_heal_retries_per_task,
        max_parallel=eff_max_parallel,
        general_instructions=effective_general_instructions,
    )

    try:
        state = orch.run(wf, reposet_map, agent_map, run_state=existing)
    except OrchestratorError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    _print_state(state)
    raise typer.Exit(0 if state.status == "succeeded" else 1)


@app.command()
def status(
    run_id: str = typer.Option(..., help="Run ID to inspect"),
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        help="Workspace root (or set AO_WORKSPACE_ROOT). "
        "When provided the --workflow/--reposets/--agents triplet is not required.",
    ),
    workflow: str | None = typer.Option(None, help="Path to workflow JSON/YAML"),
    reposets: str | None = typer.Option(None, help="Path to reposets config JSON/YAML"),
    agents: str | None = typer.Option(None, help="Path to agents config JSON/YAML"),
) -> None:
    """Show current status of a run.

    Reads status.json if present (fast path); falls back to state.json.
    Accepts --workspace <root> (or AO_WORKSPACE_ROOT) so the full
    --workflow/--reposets/--agents triplet is not required just to inspect a run.
    """
    import json
    from pathlib import Path

    # Resolve workspace: --workspace > AO_WORKSPACE_ROOT > derive from spec triplet.
    ws_root: str | None = workspace or os.environ.get("AO_WORKSPACE_ROOT")

    if ws_root is None:
        # Fall back to deriving from the spec triplet (backward-compatible path).
        from .artifacts import LocalFsArtifactStore
        from .errors import OrchestratorError
        from .runstate import RunStateStore

        try:
            wf, reposet_map, _ = _load_all(workflow, reposets, agents)
        except OrchestratorError as e:
            typer.echo(f"ERROR: {e}", err=True)
            raise typer.Exit(1)
        except SystemExit:
            return

        ws_root = reposet_map[wf.repo_set].workspace_root

    # Try status.json first (FR-6), fall back to state.json via RunStateStore.
    status_json_path = Path(ws_root) / ".orchestrator" / "runs" / run_id / "status.json"
    if status_json_path.exists():
        try:
            snap = json.loads(status_json_path.read_text())
            _print_status_snapshot(snap)
            return
        except (json.JSONDecodeError, KeyError):
            # Corrupt status.json — fall through to state.json
            pass

    # Fallback: load from state.json via RunStateStore
    from .artifacts import LocalFsArtifactStore
    from .runstate import RunStateStore

    store = LocalFsArtifactStore(ws_root)
    rs_store = RunStateStore(ws_root, store)

    try:
        loaded_state = rs_store.load(run_id)
    except FileNotFoundError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    _print_state(loaded_state)


@app.command(name="init")
def init_cmd(
    directory: str | None = typer.Option(
        None,
        "--dir",
        "-d",
        help="Directory in which to create .ao/config.yaml (default: current directory)",
    ),
) -> None:
    """Scaffold a per-project .ao/config.yaml in the current directory.

    Creates .ao/config.yaml with commented example fields so you can get
    started quickly.  Does not overwrite an existing file.
    """
    from .errors import ConfigError
    from .project_config import scaffold_init

    target = Path(directory) if directory else None
    try:
        config_path = scaffold_init(target)
    except ConfigError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Created {config_path}")
    typer.echo("")
    typer.echo("Next steps:")
    typer.echo(
        "  1. Edit .ao/config.yaml — uncomment and fill in workflow, reposets, agents paths."
    )
    typer.echo("  2. Run `ao validate` to check your config.")
    typer.echo("  3. Run `ao run` to execute your workflow.")


# Loopback default for `ao ui --host`. The dashboard has NO authentication in this release
# and can both read the filesystem and spend money by launching runs, so it must not be
# reachable off-box unless an operator deliberately overrides this.
UI_DEFAULT_HOST = "127.0.0.1"
UI_DEFAULT_PORT = 8765


@app.command(name="ui")
def ui_cmd(
    host: str = typer.Option(
        UI_DEFAULT_HOST,
        "--host",
        help=(
            "Interface to bind. Defaults to loopback because the dashboard is unauthenticated"
            " and can launch runs; only change this on a trusted network."
        ),
    ),
    port: int = typer.Option(UI_DEFAULT_PORT, "--port", "-p", help="Port to listen on."),
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace root to serve (default: current directory). Env: AO_WORKSPACE_ROOT",
    ),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev)."),
    open_browser: bool = typer.Option(
        False, "--open", help="Open the dashboard in the default browser once it is serving."
    ),
) -> None:
    """Serve the browser-based dashboard.

    Requires the optional `ui` extra:  uv sync --extra ui  (or pip install 'agent-orchestrator[ui]')
    """
    try:
        import uvicorn
    except ImportError:
        typer.echo(
            "ERROR: the dashboard needs the optional 'ui' extra.\n"
            "  uv sync --extra ui       (this repo)\n"
            "  pip install 'agent-orchestrator[ui]'",
            err=True,
        )
        raise typer.Exit(1)

    ws = workspace or os.environ.get("AO_WORKSPACE_ROOT") or os.getcwd()
    ws = str(Path(ws).resolve())
    if not Path(ws).is_dir():
        typer.echo(f"ERROR: workspace root is not a directory: {ws}", err=True)
        raise typer.Exit(1)

    from .ui.app import create_app
    from .ui.security import DEFAULT_ALLOWED_HOSTS, resolve_allowed_hosts
    from .ui.service import DashboardService

    url = f"http://{host}:{port}"
    typer.echo(f"Agent Orchestrator dashboard — workspace: {ws}")
    typer.echo(f"Serving on {url}  (Ctrl-C to stop)")
    # Single source of truth for the loopback host list: ui/security.py's DEFAULT_ALLOWED_HOSTS
    # (imported lazily, same as the rest of this function — the [ui] extra must stay optional).
    if host not in DEFAULT_ALLOWED_HOSTS:
        typer.echo(
            f"WARNING: binding {host} exposes an UNAUTHENTICATED dashboard that can browse "
            "files and start runs. Only do this on a trusted network.",
            err=True,
        )

    if open_browser:
        import threading
        import webbrowser

        # Fire after a short delay so the server is accepting connections by the time the
        # browser requests the page.
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    # The Host allowlist always includes the loopback defaults plus whatever host this
    # process actually bound to (see ui/security.py) — so a deliberate off-loopback bind
    # (warned about above) still works, while any *other* Host header (DNS rebinding) is
    # rejected with 421.
    if reload:
        # uvicorn's reloader re-imports the app in a fresh process, so it needs an import
        # string plus the workspace/bound-host handed over via env rather than a live object.
        os.environ["AO_UI_WORKSPACE"] = ws
        os.environ["AO_UI_BOUND_HOST"] = host
        uvicorn.run(
            "agent_orchestrator.ui.app:create_app_from_env",
            host=host,
            port=port,
            reload=True,
            factory=True,
        )
    else:
        app_instance = create_app(
            DashboardService(ws), allowed_hosts=resolve_allowed_hosts(bound_host=host)
        )
        uvicorn.run(app_instance, host=host, port=port)


@app.command()
def prune(
    workspace: str = typer.Option(..., "--workspace", "-w", help="Workspace root to prune"),
    older_than: int = typer.Option(
        7, "--older-than", help="Delete runs older than this many days (0 = all)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print what would be deleted without deleting"
    ),
) -> None:
    """Remove stale run artifacts from a workspace.

    Deletes run directories under <workspace>/.orchestrator/runs/ that are
    older than --older-than days. Use --dry-run to preview. Analogous to
    `docker system prune` -- run periodically to reclaim disk space.
    """
    import shutil
    import time

    runs_dir = Path(workspace) / ".orchestrator" / "runs"
    if not runs_dir.exists():
        typer.echo(f"Nothing to prune: {runs_dir}")
        raise typer.Exit(0)

    now = time.time()
    cutoff = now - older_than * 86400  # 0 days => cutoff == now => all dirs qualify

    candidates = [p for p in runs_dir.iterdir() if p.is_dir()]
    to_delete = [p for p in candidates if older_than == 0 or os.path.getmtime(p) < cutoff]

    for p in to_delete:
        if dry_run:
            typer.echo(f"Would delete: {p}")
        else:
            shutil.rmtree(p)
            typer.echo(f"Deleted: {p}")

    action = "would be deleted" if dry_run else "deleted"
    typer.echo(f"{len(to_delete)} run(s) {action}")


def _resolve_templates_workspace_root(workspace: str | None) -> str:
    """Workspace-root resolution shared by `ao templates`/`ao new` (E-Tpl3x9).

    Mirrors `ui_cmd`'s own `--workspace`/`AO_WORKSPACE_ROOT`/cwd resolution rather than
    `_load_all`'s reposet-derived one: template discovery/scaffolding operates directly on
    a workspace directory, with no workflow/reposets/agents triplet loaded yet (that only
    happens afterwards, for `--validate-only`/`--run`, via the normal `_load_all` path).
    """
    ws = workspace or os.environ.get("AO_WORKSPACE_ROOT") or os.getcwd()
    return str(Path(ws).resolve())


def _parse_param_options(raw: list[str]) -> dict[str, str]:
    """Parse repeated `--param key=value` options into a dict. Raises ValueError on a
    malformed entry (no `=`, or an empty key) so the caller can report a clean CLI error."""
    parsed: dict[str, str] = {}
    for item in raw:
        if "=" not in item:
            raise ValueError(f"--param must be key=value, got {item!r}")
        key, _, value = item.partition("=")
        key = key.strip()
        if not key:
            raise ValueError(f"--param has an empty key: {item!r}")
        parsed[key] = value
    return parsed


@app.command(name="templates")
def templates_cmd(
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace root to discover templates in (default: current directory). "
        "Env: AO_WORKSPACE_ROOT",
    ),
) -> None:
    """List discovered workflow templates (built-in + workspace-registered, E-Tpl3x9)."""
    from .templates import TemplateError, discover_templates

    ws_root = _resolve_templates_workspace_root(workspace)
    cfg = _load_project_config_or_none()

    try:
        infos = discover_templates(ws_root, cfg)
    except TemplateError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    if not infos:
        typer.echo("No templates discovered.")
        typer.echo(
            "Register one via .ao/config.yaml's `templates:` list, or point `ao new` at a "
            "template directory directly."
        )
        return

    for info in infos:
        typer.echo(f"{info.name}  [{info.source}]")
        if info.description:
            typer.echo(f"  {info.description}")
        if info.required_agents:
            typer.echo(f"  required agents: {', '.join(info.required_agents)}")
        if info.params:
            typer.echo("  params:")
            for p in info.params:
                req = "required" if p.required else "optional"
                extra = f", enum={p.enum}" if p.enum else ""
                default = f", default={p.default!r}" if p.default is not None else ""
                desc = f" — {p.description}" if p.description else ""
                typer.echo(f"    --param {p.name}=<value>  ({req}{extra}{default}){desc}")
        else:
            typer.echo("  params: (none)")
        typer.echo("")


def _invoke_run_in_process(workflow_path: str, reposets: str | None, agents: str | None) -> int:
    """Invoke the existing `run` command in-process for `ao new --run`.

    Deliberately does NOT subprocess to an `ao` binary (the global snapshot can be stale and
    silently ignore features — the exact "routing support" staleness trap new-epic-run.sh's
    `require_routing_support` guards against) and does NOT duplicate `run`'s ~150-line body.
    `typer.main.get_command(app).main(args=[...], standalone_mode=False)` was verified
    directly (manual run against this repo's own example workflow with the fake executor)
    to both suppress `run`'s internal `raise typer.Exit(...)` from propagating as a real
    `SystemExit` AND return its intended exit code, so the caller gets a clean int back.
    """
    import typer.main

    args = ["run", "--workflow", workflow_path]
    if reposets:
        args += ["--reposets", reposets]
    if agents:
        args += ["--agents", agents]
    command = typer.main.get_command(app)
    return int(command.main(args=args, standalone_mode=False))


@app.command(name="new")
def new_cmd(
    template: str = typer.Argument(
        ..., help="Template name (from `ao templates`) or a directory path containing template.yaml"
    ),
    slug_or_id: str = typer.Argument(
        ...,
        help="A bare slug (a random instance id is generated) or a full id already matching "
        "the template's id_pattern",
    ),
    param: list[str] = typer.Option(
        [], "--param", help="Template param as key=value (repeatable)."
    ),
    prompt_file: str | None = typer.Option(
        None,
        "--prompt-file",
        help="Write this file's content into the instance's prompt.md. Conflict-checked "
        "against an existing, differently-edited prompt.md (never silently overwritten).",
    ),
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace root to scaffold into (default: current directory). Must match the "
        "target repo_set's workspace_root for --validate-only/--run to resolve correctly. "
        "Env: AO_WORKSPACE_ROOT",
    ),
    validate_only: bool = typer.Option(
        False,
        "--validate-only",
        help="Scaffold, then `ao validate` the rendered workflow, and stop.",
    ),
    run: bool = typer.Option(
        False,
        "--run",
        help="Scaffold, validate, then run the rendered workflow in-process "
        "(equivalent to `ao run --workflow <rendered>`).",
    ),
    reposets: str | None = typer.Option(
        None, help="Path to reposets config JSON/YAML (used only by --validate-only/--run)"
    ),
    agents: str | None = typer.Option(
        None, help="Path to agents config JSON/YAML (used only by --validate-only/--run)"
    ),
) -> None:
    """Scaffold (and optionally validate/run) a workflow instance from a template (E-Tpl3x9)."""
    from .errors import OrchestratorError
    from .templates import TemplateError, instantiate, load_template

    if validate_only and run:
        typer.echo("ERROR: --validate-only and --run are mutually exclusive", err=True)
        raise typer.Exit(1)

    ws_root = _resolve_templates_workspace_root(workspace)
    cfg = _load_project_config_or_none()

    try:
        parsed_params = _parse_param_options(param)
    except ValueError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    prompt_text: str | None = None
    if prompt_file is not None:
        try:
            prompt_text = Path(prompt_file).read_text(encoding="utf-8")
        except OSError as e:
            typer.echo(f"ERROR: cannot read --prompt-file {prompt_file}: {e}", err=True)
            raise typer.Exit(1)

    try:
        tmpl = load_template(template, ws_root, cfg)
        result = instantiate(
            tmpl, ws_root, slug_or_id=slug_or_id, params=parsed_params, prompt_text=prompt_text
        )
    except TemplateError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Instance ready: {result.instance_dir}")
    typer.echo(f"  workflow -> {result.workflow_path}")
    if result.prompt_path:
        typer.echo(f"  prompt   -> {result.prompt_path}")
    if result.created:
        typer.echo(f"  created ({len(result.created)}): " + ", ".join(result.created))
    if result.skipped:
        typer.echo(
            f"  skipped ({len(result.skipped)}, already present): " + ", ".join(result.skipped)
        )

    if not validate_only and not run:
        typer.echo("")
        typer.echo("Next steps:")
        step = 1
        if result.prompt_path:
            typer.echo(f"  {step}. Edit your prompt:   $EDITOR {result.prompt_path}")
            step += 1
        typer.echo(f"  {step}. Validate:           ao validate --workflow {result.workflow_path}")
        step += 1
        typer.echo(f"  {step}. Run:                ao run      --workflow {result.workflow_path}")
        return

    try:
        _load_all(result.workflow_path, reposets, agents)
        typer.echo("OK: rendered workflow is valid")
    except OrchestratorError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)
    except SystemExit:
        return

    if not run:
        return

    typer.echo("Running (in-process, equivalent to `ao run --workflow <rendered>`)...")
    exit_code = _invoke_run_in_process(result.workflow_path, reposets, agents)
    raise typer.Exit(exit_code)


if __name__ == "__main__":
    app()
