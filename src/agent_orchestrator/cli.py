"""Typer-based CLI for the agent orchestrator.

Commands:
  ao validate   — Validate workflow, reposets, and agents specs.
  ao run        — Run a workflow from scratch.
  ao resume     — Resume a previously interrupted run.
  ao status     — Show current status of a run.
  ao init       — Scaffold a per-project .ao/config.yaml.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from .models import BudgetSpec

app = typer.Typer(name="ao", help="Agent Orchestrator CLI", add_completion=False)


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
    from .spec import cross_validate, load_workflow

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
    cross_validate(wf, reposet_map, agent_map)
    return wf, reposet_map, agent_map


def _print_state(state) -> None:
    typer.echo(f"\nRun:    {state.run_id}")
    typer.echo(f"Status: {state.status}")
    typer.echo(f"\n{'Task':<30} {'Status':<15} {'Attempts'}")
    typer.echo("-" * 55)
    for tid, ts in state.tasks.items():
        typer.echo(f"{tid:<30} {ts.status:<15} {ts.attempts}")


def _print_status_snapshot(snap: dict) -> None:
    """Print a status.json snapshot in the same table format as _print_state."""
    typer.echo(f"\nRun:          {snap.get('run_id', '')}")
    typer.echo(f"Status:       {snap.get('status', '')}")
    current = snap.get("current_task")
    if current:
        typer.echo(f"Current task: {current}")
    typer.echo(f"\n{'Task':<30} {'Status':<15} {'Attempts'}")
    typer.echo("-" * 55)
    for task_entry in snap.get("tasks", []):
        typer.echo(
            f"{task_entry.get('id', ''):<30} "
            f"{task_entry.get('status', ''):<15} "
            f"{task_entry.get('attempts', 0)}"
        )


def _resolve_run_settings(
    max_attempts: int | None,
    max_turns: int | None,
    model: str | None,
    effort: str | None,
    quota_max_wait: int | None,
    quota_poll_interval: int | None,
) -> tuple[int | None, int | None, str | None, str | None, int, int]:
    """Merge CLI flags, env vars, and project config for runtime execution settings.

    Precedence (highest to lowest): CLI flag > env var > project config > built-in default.
    Returns (max_attempts, max_turns, model, effort, quota_max_wait_seconds, quota_poll_seconds).
    """
    from .models import DEFAULT_QUOTA_MAX_WAIT_SECONDS, DEFAULT_QUOTA_POLL_SECONDS
    from .project_config import find_project_config, load_project_config

    def _int_env(name: str) -> int | None:
        v = os.environ.get(name)
        try:
            return int(v) if v else None
        except ValueError:
            return None

    cfg = None
    config_path = find_project_config()
    if config_path is not None:
        try:
            cfg = load_project_config(config_path)
        except Exception:
            cfg = None

    resolved_max_attempts = (
        max_attempts or _int_env("AO_MAX_ATTEMPTS") or (cfg.max_attempts if cfg else None)
    )
    resolved_max_turns = (
        max_turns or _int_env("AO_MAX_TURNS") or (cfg.max_turns if cfg else None)
    )
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
    return (
        resolved_max_attempts,
        resolved_max_turns,
        resolved_model,
        resolved_effort,
        resolved_quota_max_wait,
        resolved_quota_poll,
    )


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
    except (OrchestratorError, SystemExit):
        return

    (
        eff_max_attempts,
        eff_max_turns,
        eff_model,
        eff_effort,
        eff_quota_max_wait,
        eff_quota_poll,
    ) = _resolve_run_settings(
        max_attempts, max_turns, model, effort, quota_max_wait, quota_poll_interval
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

    orch = Orchestrator(
        executor,
        store,
        rs_store,
        budget_manager=budget_manager,
        estimator=estimator,
        clock=clock,
        quota_max_wait_seconds=eff_quota_max_wait,
        quota_poll_seconds=eff_quota_poll,
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
) -> None:
    """Resume a previously interrupted run."""
    from datetime import UTC
    from datetime import datetime as _dt

    from .artifacts import LocalFsArtifactStore
    from .budget import DefaultBudgetManager
    from .engine import Orchestrator
    from .errors import OrchestratorError
    from .estimator import HeuristicTokenEstimator
    from .executors import DispatchExecutor
    from .runstate import RunStateStore

    try:
        wf, reposet_map, agent_map = _load_all(workflow, reposets, agents)
    except (OrchestratorError, SystemExit):
        return

    workspace = os.environ.get("AO_WORKSPACE_ROOT") or reposet_map[wf.repo_set].workspace_root
    store = LocalFsArtifactStore(workspace)
    rs_store = RunStateStore(workspace, store)

    (
        eff_max_attempts,
        eff_max_turns,
        eff_model,
        eff_effort,
        eff_quota_max_wait,
        eff_quota_poll,
    ) = _resolve_run_settings(
        max_attempts, max_turns, model, effort, quota_max_wait, quota_poll_interval
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
    orch = Orchestrator(
        executor,
        store,
        rs_store,
        budget_manager=budget_manager,
        estimator=estimator,
        clock=clock,
        quota_max_wait_seconds=eff_quota_max_wait,
        quota_poll_seconds=eff_quota_poll,
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
        except (OrchestratorError, SystemExit):
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


if __name__ == "__main__":
    app()
