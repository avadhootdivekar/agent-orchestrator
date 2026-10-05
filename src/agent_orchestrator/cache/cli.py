"""`ao cache` Typer sub-app (E-Rc4Hk8, HLD 8.9).

Skeleton only (T-28J9oR): the group callback carries the group help so `ao cache --help`
renders. T-6tRKml adds the `ls|stats|show|rm|prune|clear|verify` commands. Lazy imports only:
this module is loaded by `agent_orchestrator.cli` at import time, so it must stay cheap.
"""

from __future__ import annotations

import typer

cache_app = typer.Typer(name="cache", no_args_is_help=True)


@cache_app.callback()
def _cache_group() -> None:
    """Manage the RESULT cache (reuse of identical, previously successful task outputs across
    runs) -- unrelated to Claude prompt caching.
    """
