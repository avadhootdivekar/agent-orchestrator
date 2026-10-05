"""One vocabulary for auth states, policies and audit events (HLD sections 11.2, 12.4; L0).

Every other auth module imports these names from here; none defines its own. Audit event names
are only ever spelled through :class:`AuditEventName` (no string literals elsewhere).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from .errors import ErrorCode

AuthMethod = Literal["password", "password+totp"]


class TotpPolicy(StrEnum):
    """Deployment-wide TOTP policy (``ui.auth.totp`` / ``AO_UI_AUTH_TOTP``)."""

    OFF = "off"
    OPTIONAL = "optional"
    REQUIRED = "required"


class TotpRequirement(StrEnum):
    """What TOTP demands of one user at login (policy x per-user flag x enrollment)."""

    NONE = "none"
    ENROLL_ALLOWED = "enroll_allowed"
    BLOCKED = "blocked"


class SecondFactor(StrEnum):
    TOTP = "totp"
    RECOVERY_CODE = "recovery_code"


class SessionState(StrEnum):
    PARTIAL_SECOND_FACTOR = "partial_second_factor"
    PARTIAL_ENROLL = "partial_enroll"
    FULL = "full"

    @property
    def api_state(self) -> str:
        """The ``state`` string the HTTP API reports for this session."""
        return _API_STATE[self]

    @property
    def denial_code(self) -> ErrorCode:
        """The error code for a request a *partial* session may not make.

        ``FULL`` is never denied by state, so asking for its denial code is a programming error.
        """
        return denial_code(self)


_API_STATE: dict[SessionState, str] = {
    SessionState.PARTIAL_SECOND_FACTOR: "second_factor_required",
    SessionState.PARTIAL_ENROLL: "enrollment_required",
    SessionState.FULL: "authenticated",
}


def denial_code(state: SessionState | None) -> ErrorCode:
    """Denial code for a request outside the session's allowed set; ``None`` = no session."""
    if state is None:
        return ErrorCode.NOT_AUTHENTICATED
    if state is SessionState.PARTIAL_SECOND_FACTOR:
        return ErrorCode.SECOND_FACTOR_REQUIRED
    if state is SessionState.PARTIAL_ENROLL:
        return ErrorCode.ENROLLMENT_REQUIRED
    raise ValueError("a FULL session is never denied by session state")


class AuditEventName(StrEnum):
    """Every audit event this version emits (HLD section 12.4): 24 in v2.1."""

    LOGIN_SUCCESS = "auth.login.success"
    LOGIN_FAILURE = "auth.login.failure"
    LOGIN_SECOND_FACTOR_PENDING = "auth.login.second_factor_pending"
    SECOND_FACTOR_FAILURE = "auth.second_factor.failure"
    REAUTH_FAILURE = "auth.reauth.failure"
    ENROLLMENT_TOKEN_FAILURE = "auth.enrollment_token.failure"
    LOCKOUT = "auth.lockout"
    FAILURE_BURST = "auth.failure.burst"
    LOGOUT = "auth.logout"
    LOGOUT_ALL = "auth.logout_all"
    RECOVERY_CODE_USED = "auth.recovery_code.used"
    RECOVERY_CODES_REGENERATED = "auth.recovery_codes.regenerated"
    PASSWORD_CHANGED = "auth.password.changed"
    TOTP_ENROLLED = "auth.totp.enrolled"
    TOTP_DISABLED = "auth.totp.disabled"
    TOTP_RESET = "auth.totp.reset"
    ENROLLMENT_TOKEN_ISSUED = "auth.enrollment_token.issued"
    USER_ADDED = "auth.user.added"
    USER_REMOVED = "auth.user.removed"
    USER_UNLOCKED = "auth.user.unlocked"
    SESSIONS_REVOKED = "auth.sessions.revoked"
    STARTUP_REFUSED = "auth.startup.refused"
    STARTUP_DISABLED_BY_CONFIG = "auth.startup.disabled_by_config"  # v2.1 (security M3)
    STARTUP_TOTP_DOWNGRADED_BY_CONFIG = "auth.startup.totp_downgraded_by_config"  # v2.1
