"""Browser-based dashboard for the agent orchestrator (E-Ui7Kq2).

Layered so the web framework stays at the edge:

``files``      filesystem browsing, root-scoped and traversal-guarded
``runs``       run discovery, per-run detail, aggregate stats, deletion
``processes``  supervision of ``ao run`` / ``ao resume`` subprocesses
``service``    :class:`~agent_orchestrator.ui.service.DashboardService` — the whole
               dashboard API as plain Python, with **no FastAPI import anywhere**
``app``        thin FastAPI adapter that maps HTTP routes onto ``DashboardService``

Only ``app`` requires the optional ``[ui]`` extra; everything below it is importable (and
unit-testable) against the core dependency set alone.
"""

from __future__ import annotations

__all__ = ["DashboardService"]


def __getattr__(name: str) -> object:
    # Lazy re-export: importing agent_orchestrator.ui must not drag in the service layer
    # (and its transitive imports) for callers that only want a submodule.
    if name == "DashboardService":
        from .service import DashboardService

        return DashboardService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
