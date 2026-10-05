"""`ao cache` Typer sub-app (E-Rc4Hk8, HLD 8.9, 14.2): ls, stats, show, rm, prune, clear, verify.

This module is loaded by `agent_orchestrator.cli` at import time, so it must stay cheap: its
module-level imports are `typer` and `cache.constants` only (an AST test pins that), and every
command body imports `cache.cli_ops` (the store, `json`, pydantic models, ...) lazily. The
commands themselves live in `cli_ops`; each returns its exit code, raised here as `typer.Exit`.

Deferred (non-MVP, intentionally absent): `rm --run/--task`, `verify --repair`, `refresh`.
"""

from __future__ import annotations

import typer

from . import constants

cache_app = typer.Typer(name="cache", no_args_is_help=True)

_WORKSPACE_HELP = (
    "Workspace root (default: AO_WORKSPACE_ROOT, then the project config's workspace)."
)
_JSON_HELP = "Print exactly one JSON document on stdout (also on a non-zero exit)."
_PREFIX_HELP = "Entry key, or a unique prefix of it (4 to 64 lowercase hex characters)."

# One shared Option declaration per flag (Typer reads them; it does not mutate them).
_WORKSPACE = typer.Option(None, "--workspace", "-w", help=_WORKSPACE_HELP)
_AS_JSON = typer.Option(False, "--json", help=_JSON_HELP)


def _now():
    """The wall clock for every command (a timezone-aware `datetime`): a module-level seam so
    tests can patch it. Unannotated on purpose: `datetime` may not be imported at module level."""
    from datetime import UTC, datetime

    return datetime.now(UTC)


@cache_app.callback()
def _cache_group() -> None:
    """Manage the RESULT cache (reuse of identical, previously successful task outputs across
    runs) -- unrelated to Claude prompt caching.
    """


@cache_app.command("ls")
def ls_command(
    workspace: str | None = _WORKSPACE,
    as_json: bool = _AS_JSON,
    limit: int = typer.Option(
        constants.DEFAULT_LS_LIMIT, "--limit", help="Show at most this many entries."
    ),
    sort: str = typer.Option(
        constants.SORT_LRU,
        "--sort",
        help="Order: lru (most recently used first), created (newest first) or size (largest).",
    ),
) -> None:
    """List cache entries."""
    from .cli_ops import run_ls

    raise typer.Exit(run_ls(workspace, as_json, limit=limit, sort=sort, now=_now()))


@cache_app.command("stats")
def stats_command(workspace: str | None = _WORKSPACE, as_json: bool = _AS_JSON) -> None:
    """Show store size, limits, age range, expired entries and anomalies."""
    from .cli_ops import run_stats

    raise typer.Exit(run_stats(workspace, as_json, now=_now()))


@cache_app.command("show")
def show_command(
    key: str = typer.Argument(..., metavar="KEY_OR_PREFIX", help=_PREFIX_HELP),
    workspace: str | None = _WORKSPACE,
    as_json: bool = _AS_JSON,
) -> None:
    """Show one entry: provenance, outputs, blob presence and key components."""
    from .cli_ops import run_show

    raise typer.Exit(run_show(workspace, as_json, prefix=key, now=_now()))


@cache_app.command("rm")
def rm_command(
    key: str = typer.Argument(..., metavar="KEY_OR_PREFIX", help=_PREFIX_HELP),
    workspace: str | None = _WORKSPACE,
    as_json: bool = _AS_JSON,
) -> None:
    """Remove exactly one entry (its blobs are reclaimed by `ao cache prune`)."""
    from .cli_ops import run_rm

    raise typer.Exit(run_rm(workspace, as_json, prefix=key))


@cache_app.command("prune")
def prune_command(
    workspace: str | None = _WORKSPACE,
    as_json: bool = _AS_JSON,
    max_bytes: int | None = typer.Option(
        None, "--max-bytes", help="Size budget in bytes (overrides cache.max_bytes)."
    ),
    older_than: int | None = typer.Option(
        None,
        "--older-than",
        help="Expire entries older than DAYS (overrides cache.ttl_days; 0 expires every entry).",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report what would go; delete nothing."),
) -> None:
    """Remove invalid, expired and least-recently-used entries, then unreferenced blobs, stale
    temp files and stale restore leftovers in output directories."""
    from .cli_ops import run_prune

    code = run_prune(
        workspace, as_json, max_bytes=max_bytes, older_than=older_than, dry_run=dry_run, now=_now()
    )
    raise typer.Exit(code)


@cache_app.command("clear")
def clear_command(
    workspace: str | None = _WORKSPACE,
    as_json: bool = _AS_JSON,
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Do not ask: required unless stdin is a terminal (and never with --json).",
    ),
) -> None:
    """Delete every entry and blob."""
    from .cli_ops import run_clear

    raise typer.Exit(run_clear(workspace, as_json, yes=yes))


@cache_app.command("verify")
def verify_command(workspace: str | None = _WORKSPACE, as_json: bool = _AS_JSON) -> None:
    """Check entries and blobs for corruption (read-only: deletes nothing). Exit 1 on corruption."""
    from .cli_ops import run_verify

    raise typer.Exit(run_verify(workspace, as_json))
