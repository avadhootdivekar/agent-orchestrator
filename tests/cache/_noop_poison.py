"""Poisoned-import finder for the NFR-1 no-op proof (I-1; HLD 8.7.5).

Deliberately free of any ``agent_orchestrator`` import so a subprocess can install it BEFORE the
package is first imported. Python 3.12+ consults ``find_spec`` only: a finder that defines just
the legacy ``find_module`` is silently ignored (that is what made the first draft of I-1
vacuous), so ``find_spec`` is the one hook implemented here.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from importlib.abc import MetaPathFinder
from importlib.machinery import ModuleSpec
from types import ModuleType

CACHE_PACKAGE = "agent_orchestrator.cache"
# The only cache-package modules a cache-off ENGINE process may load (HLD 8.7.5).
ALLOWED_CACHE_MODULES = frozenset({CACHE_PACKAGE, f"{CACHE_PACKAGE}.constants"})


def is_forbidden_cache_module(fullname: str) -> bool:
    """True for ``agent_orchestrator.cache.<anything>`` other than the allowed modules."""
    return fullname.startswith(f"{CACHE_PACKAGE}.") and fullname not in ALLOWED_CACHE_MODULES


class PoisonedFinder(MetaPathFinder):
    """Raise ``ImportError`` for every forbidden ``agent_orchestrator.cache.*`` module."""

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: ModuleType | None = None,
    ) -> ModuleSpec | None:
        if is_forbidden_cache_module(fullname):
            raise ImportError(f"poisoned (NFR-1 no-op proof): {fullname} must not be imported")
        return None


def install() -> PoisonedFinder:
    """Put a fresh finder first on ``sys.meta_path`` and return it."""
    finder = PoisonedFinder()
    sys.meta_path.insert(0, finder)
    return finder


def loaded_cache_modules() -> set[str]:
    """Every ``agent_orchestrator.cache*`` module currently in ``sys.modules``."""
    return {
        name
        for name in sys.modules
        if name == CACHE_PACKAGE or name.startswith(f"{CACHE_PACKAGE}.")
    }
