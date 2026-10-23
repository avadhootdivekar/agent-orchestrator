"""`ao-bench import-prs` (HLD §4.2 Tier 2a) -- STUB, owner: Tier-2 track."""

from __future__ import annotations

import typer


def import_prs_command(
    repo: str = typer.Option(
        ..., "--repo", help="GitHub repo (owner/name) to mine merged PRs from."
    ),
    since: str = typer.Option(..., "--since", help="ISO date; only PRs merged on/after it."),
    out: str = typer.Option(..., "--out", help="Suite dir to write instances.json into."),
) -> None:
    """Mine recent merged PRs with test changes into a pr_mined suite."""
    typer.echo("ERROR: ao-bench import-prs is not implemented yet (E-Bn7Dsc)", err=True)
    raise typer.Exit(1)
