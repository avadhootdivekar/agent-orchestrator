"""Per-app route policy tables and the pure policy predicates (HLD section 11.14; L1).

One explicit table per app (a renamed route silently falls back to AUTHENTICATED, the safe
direction). Path strings come from ``constants.py``. Denial codes live in
``model.denial_code``; this module never repeats them.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from .constants import (
    AUTH_ENROLL_BEGIN_PATH,
    AUTH_ENROLL_CONFIRM_PATH,
    AUTH_LOGIN_PATH,
    AUTH_LOGOUT_PATH,
    AUTH_STATUS_PATH,
    AUTH_TOTP_VERIFY_PATH,
    HEALTH_PATH,
    HUB_ASSET_ROUTE_PATH,
    HUB_LOGIN_PATH,
    SPA_ASSETS_MOUNT_NAME,
    SPA_FALLBACK_PATH,
)
from .model import SessionState, TotpPolicy, TotpRequirement


class RoutePolicy(StrEnum):
    PUBLIC = "public"
    PARTIAL_SECOND_FACTOR = "partial_second_factor"
    ENROLLMENT = "enrollment"
    AUTHENTICATED = "authenticated"  # the default for anything absent from a table


# (HTTP method or "MOUNT", route.path template or mount name)
RouteKey = tuple[str, str]

MOUNT_METHOD = "MOUNT"

# Every other /api/auth/* route (password, keepalive, totp/disable, totp/recovery-codes) is
# AUTHENTICATED by omission.
AUTH_ROUTE_POLICIES: Mapping[RouteKey, RoutePolicy] = MappingProxyType(
    {
        ("GET", AUTH_STATUS_PATH): RoutePolicy.PUBLIC,
        ("POST", AUTH_LOGIN_PATH): RoutePolicy.PUBLIC,
        ("POST", AUTH_LOGOUT_PATH): RoutePolicy.PUBLIC,
        ("POST", AUTH_TOTP_VERIFY_PATH): RoutePolicy.PARTIAL_SECOND_FACTOR,
        ("POST", AUTH_ENROLL_BEGIN_PATH): RoutePolicy.ENROLLMENT,
        ("POST", AUTH_ENROLL_CONFIRM_PATH): RoutePolicy.ENROLLMENT,
    }
)

DASHBOARD_ROUTE_POLICIES: Mapping[RouteKey, RoutePolicy] = MappingProxyType(
    {
        **AUTH_ROUTE_POLICIES,
        ("GET", HEALTH_PATH): RoutePolicy.PUBLIC,
        ("GET", "/"): RoutePolicy.PUBLIC,  # spa_root or missing_frontend
        ("GET", SPA_FALLBACK_PATH): RoutePolicy.PUBLIC,  # never for /api/* (guarded elsewhere)
        (MOUNT_METHOD, SPA_ASSETS_MOUNT_NAME): RoutePolicy.PUBLIC,
    }
)

# The hub's "/" and "/api/service/status" are AUTHENTICATED by omission.
HUB_ROUTE_POLICIES: Mapping[RouteKey, RoutePolicy] = MappingProxyType(
    {
        **AUTH_ROUTE_POLICIES,
        ("GET", HUB_LOGIN_PATH): RoutePolicy.PUBLIC,
        ("GET", HUB_ASSET_ROUTE_PATH): RoutePolicy.PUBLIC,
    }
)

# v2.1 (security M1): the ONE explicit cookie-only-navigation flag, per app. A route here may
# yield a principal from the cookie alone (a navigation cannot send the proof header). It must
# be a read-only HTML GET outside /api and AUTHENTICATED in the app's table. Kept separate from
# the tables so ``policy_allows`` is unchanged and the flag can never make a route public.
DASHBOARD_COOKIE_ONLY_NAVIGATION: frozenset[RouteKey] = frozenset()  # the SPA shell is PUBLIC
HUB_COOKIE_ONLY_NAVIGATION: frozenset[RouteKey] = frozenset({("GET", "/")})  # the hub index

_ALLOWED_STATES: Mapping[RoutePolicy, frozenset[SessionState]] = MappingProxyType(
    {
        RoutePolicy.PUBLIC: frozenset(SessionState),
        RoutePolicy.PARTIAL_SECOND_FACTOR: frozenset(
            {SessionState.PARTIAL_SECOND_FACTOR, SessionState.FULL}
        ),
        RoutePolicy.ENROLLMENT: frozenset({SessionState.PARTIAL_ENROLL, SessionState.FULL}),
        RoutePolicy.AUTHENTICATED: frozenset({SessionState.FULL}),
    }
)


def proof_required(policy: RoutePolicy, *, cookie_only_navigation: bool) -> bool:
    """Whether the session proof header must accompany the cookie (D25, v2.1).

    True for every non-PUBLIC policy, API or not, unless the route is the flagged cookie-only
    navigation; False for PUBLIC.
    """
    return policy is not RoutePolicy.PUBLIC and not cookie_only_navigation


def policy_allows(policy: RoutePolicy, state: SessionState | None) -> bool:
    """Whether a session in ``state`` (``None`` = none) may make a request under ``policy``."""
    if state is None:
        return policy is RoutePolicy.PUBLIC
    return state in _ALLOWED_STATES[policy]


def totp_requirement(policy: TotpPolicy, user_totp_required: bool) -> TotpRequirement:
    """The ONE place that decides "TOTP is required for this user".

    Required = deployment policy REQUIRED or the user's own flag. A requirement under policy
    OFF cannot be met (``BLOCKED``); otherwise the user may enroll (``ENROLL_ALLOWED``).
    """
    required = policy is TotpPolicy.REQUIRED or user_totp_required
    if not required:
        return TotpRequirement.NONE
    return TotpRequirement.BLOCKED if policy is TotpPolicy.OFF else TotpRequirement.ENROLL_ALLOWED
