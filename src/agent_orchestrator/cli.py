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

import typer

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
) -> None:
    """Run a workflow from scratch."""
    from .artifacts import LocalFsArtifactStore
    from .engine import Orchestrator
    from .errors import CycleError, OrchestratorError
    from .executors import DispatchExecutor
    from .runstate import RunStateStore

    try:
        wf, reposet_map, agent_map = _load_all(workflow, reposets, agents)
    except (OrchestratorError, SystemExit):
        return

    workspace = os.environ.get("AO_WORKSPACE_ROOT") or reposet_map[wf.repo_set].workspace_root
    store = LocalFsArtifactStore(workspace)
    rs_store = RunStateStore(workspace, store)
    executor = DispatchExecutor()
    orch = Orchestrator(executor, store, rs_store)

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
) -> None:
    """Resume a previously interrupted run."""
    from .artifacts import LocalFsArtifactStore
    from .engine import Orchestrator
    from .errors import OrchestratorError
    from .executors import DispatchExecutor
    from .runstate import RunStateStore

    try:
        wf, reposet_map, agent_map = _load_all(workflow, reposets, agents)
    except (OrchestratorError, SystemExit):
        return

    workspace = os.environ.get("AO_WORKSPACE_ROOT") or reposet_map[wf.repo_set].workspace_root
    store = LocalFsArtifactStore(workspace)
    rs_store = RunStateStore(workspace, store)

    try:
        existing = rs_store.load(run_id)
        existing = rs_store.prepare_resume(existing, wf)
    except FileNotFoundError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    executor = DispatchExecutor()
    orch = Orchestrator(executor, store, rs_store)

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
    workflow: str | None = typer.Option(None, help="Path to workflow JSON/YAML"),
    reposets: str | None = typer.Option(None, help="Path to reposets config JSON/YAML"),
    agents: str | None = typer.Option(None, help="Path to agents config JSON/YAML"),
) -> None:
    """Show current status of a run."""
    from .artifacts import LocalFsArtifactStore
    from .errors import OrchestratorError
    from .runstate import RunStateStore

    try:
        wf, reposet_map, agent_map = _load_all(workflow, reposets, agents)
    except (OrchestratorError, SystemExit):
        return

    workspace = os.environ.get("AO_WORKSPACE_ROOT") or reposet_map[wf.repo_set].workspace_root
    store = LocalFsArtifactStore(workspace)
    rs_store = RunStateStore(workspace, store)

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


if __name__ == "__main__":
    app()
