"""Dashboard and hub authentication: local accounts plus optional TOTP (E-Da5Tn9).

Layered (HLD section 11.1) so the web framework stays at the edge:

L0  ``constants`` ``errors`` ``seams`` ``model`` ``principal`` -- vocabulary, no framework
L1  pure crypto and policy (``passwords``, ``totp``, ``recovery``, ``policy``, ``paths``)
L2  persistence and configuration (``store``, ``lockouts``, ``audit``, ``scrub``, ``settings``)
L3  services (``sessions``, ``throttle``, ``guard``, ``provider``, ``runtime``, ``launch``)
L4  edges (``http/*`` and ``cli``)

Only ``auth/http/middleware.py`` and the route modules import fastapi/starlette. Importing
this package imports nothing heavy (no pydantic, yaml, fastapi, starlette or uvicorn): the
re-export skeleton below is lazy (PEP 562), like ``ui/__init__.py``. Later tasks append their
public names to ``_LAZY_EXPORTS``.
"""

from __future__ import annotations

import importlib

# public name -> submodule (relative to this package) that defines it.
_LAZY_EXPORTS: dict[str, str] = {}

__all__ = sorted(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    # Lazy re-export: importing agent_orchestrator.auth must not drag in any submodule.
    module = _LAZY_EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(f".{module}", __name__), name)
