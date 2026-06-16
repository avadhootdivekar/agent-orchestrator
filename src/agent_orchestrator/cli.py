"""Typer-based CLI for the agent orchestrator.

Commands:
  ao validate   — Validate workflow, reposets, and agents specs.
  ao run        — Run a workflow from scratch.
  ao resume     — Resume a previously interrupted run.
  ao status     — Show current status of a run.
"""

from __future__ import annotations

import os

import typer

app = typer.Typer(name="ao", help="Agent Orchestrator CLI", add_completion=False)


def _load_all(
    workflow_path: str,
    reposets_path: str | None,
    agents_path: str | None,
):
    """Load and cross-validate workflow, reposets, and agents."""
    from .config import load_agents, load_reposets
    from .spec import cross_validate, load_workflow

    rp = reposets_path or os.environ.get("AO_REPOSETS")
    ap = agents_path or os.environ.get("AO_AGENTS")

    if not rp:
        typer.echo("--reposets or AO_REPOSETS required", err=True)
        raise typer.Exit(1)
    if not ap:
        typer.echo("--agents or AO_AGENTS required", err=True)
        raise typer.Exit(1)

    workflow = load_workflow(workflow_path)
    reposets = load_reposets(rp)
    agents = load_agents(ap)
    cross_validate(workflow, reposets, agents)
    return workflow, reposets, agents


def _print_state(state) -> None:
    typer.echo(f"\nRun:    {state.run_id}")
    typer.echo(f"Status: {state.status}")
    typer.echo(f"\n{'Task':<30} {'Status':<15} {'Attempts'}")
    typer.echo("-" * 55)
    for tid, ts in state.tasks.items():
        typer.echo(f"{tid:<30} {ts.status:<15} {ts.attempts}")


@app.command()
def validate(
    workflow: str = typer.Option(..., help="Path to workflow JSON/YAML"),
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
    workflow: str = typer.Option(..., help="Path to workflow JSON/YAML"),
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
    workflow: str = typer.Option(..., help="Path to workflow JSON/YAML"),
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
    workflow: str = typer.Option(..., help="Path to workflow JSON/YAML"),
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


if __name__ == "__main__":
    app()
