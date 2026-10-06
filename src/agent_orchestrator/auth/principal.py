"""The authenticated-identity contract the rest of ``ao`` consumes (HLD sections 2.6, 11.14; L0).

``Principal`` is the frozen contract of the approval-gates epic (owner decision, v2.1; OQ-8):

* ``username``, ``auth_method`` and ``roles`` are the brief's positional minimum, so
  ``Principal(username, auth_method, roles)`` keeps working;
* ``roles`` is a ``list[str]``: a fresh ``[]`` per principal and excluded from ``hash()`` (so
  ``hash(p)`` works on a frozen dataclass holding a list; ``==`` still compares it);
* every additive field is keyword-only, because a defaulted ``roles`` followed by non-default
  positional fields raises ``TypeError`` at class creation.

``SessionManager.principal_for`` is the only builder and the only tuple -> list converter:
session records keep tuple roles, so a shallow-copied record can never alias a list.

RBAC forward note: roles are frozen into the session at issue. When RBAC lands, any role change
must bump ``credential_epoch`` (or ``revalidate`` must re-read roles), otherwise live sessions
keep stale roles until they expire.

The request helpers read ``request.state`` through the ``SCOPE_*`` constant names and never
import the web framework, so this module stays L0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .constants import SCOPE_AUTH_ENABLED_KEY, SCOPE_PRINCIPAL_KEY
from .errors import AuthError, ErrorCode
from .model import AuthMethod


@dataclass(frozen=True, slots=True)
class Principal:
    username: str  # normalized account name: a display/audit label, NOT a stable key
    auth_method: AuthMethod  # "password+totp" also covers a recovery-code login (see ``amr``)
    roles: list[str] = field(default_factory=list, hash=False)  # always [] in the MVP
    # --- additive, keyword-only (v2.1) ---
    user_id: str = field(kw_only=True)  # immutable random id: the STABLE key to persist
    realm: str = field(kw_only=True)  # "hub" | "ui:<workspace_id>"
    session_id: str = field(kw_only=True)  # non-secret id for audit correlation
    # amr: ("pwd",) | ("pwd","otp","mfa") | ("pwd","rcv","mfa")
    amr: tuple[str, ...] = field(kw_only=True)
    auth_time: datetime = field(kw_only=True)  # UTC instant the session lineage became FULL
    provider: str = field(kw_only=True)  # "local-password" in the MVP


def current_principal(request: Any) -> Principal | None:
    """The request's principal, or ``None`` (anonymous, auth off, or no middleware)."""
    result: Principal | None = getattr(getattr(request, "state", None), SCOPE_PRINCIPAL_KEY, None)
    return result


def auth_enabled(request: Any) -> bool:
    """Whether auth is on for this request. Never infer "off" from ``principal is None``."""
    return bool(getattr(getattr(request, "state", None), SCOPE_AUTH_ENABLED_KEY, False))


def require_principal(request: Any) -> Principal | None:
    """Auth on: the principal, or ``AuthError(NOT_AUTHENTICATED)``. Auth off: ``None``.

    Use on any route that must never run anonymously, even if it was marked PUBLIC by mistake
    (defence in depth).
    """
    if not auth_enabled(request):
        return None
    principal = current_principal(request)
    if principal is None:
        raise AuthError(ErrorCode.NOT_AUTHENTICATED)
    return principal
