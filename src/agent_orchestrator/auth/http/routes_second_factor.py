"""The second-factor routes E3-E7 (HLD 11.18; L4).

STUB created by T-rpKCjP so the builder registry has its registered seam; T-KQ6ZrY owns this file
from here on and fills in the handlers. ``add_totp_routes`` returns at once without a TOTP
service (``runtime.totp is None``, and in this stub always), so no route is added.

This module imports its helpers from ``routes.py``; ``routes.py`` imports it lazily inside
``install_auth_routes`` (never at its top), so the import cycle cannot bite.
"""

from __future__ import annotations

from fastapi import FastAPI

from ..constants import LOCAL_PROVIDER_ID
from ..runtime import AuthRuntime
from .routes import register_route_builder


def add_totp_routes(app: FastAPI, runtime: AuthRuntime) -> None:
    """Register E3-E7 for a runtime with a TOTP service; a no-op without one."""
    if runtime.totp is None:
        return
    # T-KQ6ZrY: the E3-E7 handlers land here. Until then no second-factor route exists.


register_route_builder(LOCAL_PROVIDER_ID, add_totp_routes)
