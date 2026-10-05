"""Typed error vocabulary for dashboard/hub authentication (HLD section 11.2, L0).

Two families:

* :class:`AuthError` -- HTTP-mappable. Carries a stable :class:`ErrorCode`, a user-facing
  ``detail`` (never a secret, never a caller-supplied value), optional ``extra`` envelope keys
  and response ``headers``. ``cause_for_log`` is for the operator log only: it is never part of
  ``str(err)``, ``detail`` or the response body (FR-20).
* :class:`AuthConfigError` -- a startup/CLI configuration problem; the caller maps it to
  ``EXIT_CONFIG`` and ``str()`` is the operator-facing message.

Store domain errors (``StoreMissingError`` and friends) live in ``auth/store.py`` (L2).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from agent_orchestrator.errors import ConfigError, OrchestratorError

from .constants import STORE_RETRY_AFTER_SECONDS

RETRY_AFTER_HEADER = "Retry-After"
RETRY_AFTER_SECONDS_KEY = "retry_after_seconds"
BUSY_RETRY_AFTER_SECONDS = 1


class ErrorCode(StrEnum):
    """Every ``code`` of the HTTP error envelope (HLD section 2.2)."""

    INVALID_REQUEST = "invalid_request"
    PASSWORD_POLICY = "password_policy"
    NOT_AUTHENTICATED = "not_authenticated"
    SECOND_FACTOR_REQUIRED = "second_factor_required"
    ENROLLMENT_REQUIRED = "enrollment_required"
    INVALID_CREDENTIALS = "invalid_credentials"
    INVALID_CODE = "invalid_code"
    ORIGIN_REQUIRED = "origin_required"
    ORIGIN_MISMATCH = "origin_mismatch"
    CROSS_SITE_REQUEST = "cross_site_request"
    INSECURE_TRANSPORT = "insecure_transport"
    TOTP_DISABLED_BY_POLICY = "totp_disabled_by_policy"
    TOTP_REQUIRED = "totp_required"
    ALREADY_AUTHENTICATED = "already_authenticated"
    TOTP_ALREADY_ENROLLED = "totp_already_enrolled"
    TOTP_NOT_ENROLLED = "totp_not_enrolled"
    NO_PENDING_ENROLLMENT = "no_pending_enrollment"
    BODY_TOO_LARGE = "body_too_large"
    TOO_MANY_ATTEMPTS = "too_many_attempts"
    BUSY = "busy"
    STORE_UNAVAILABLE = "store_unavailable"
    FORBIDDEN = "forbidden"  # RESERVED for future RBAC (403); never emitted in the MVP


STATUS_BY_CODE: Mapping[ErrorCode, int] = MappingProxyType(
    {
        ErrorCode.INVALID_REQUEST: 400,
        ErrorCode.PASSWORD_POLICY: 400,
        ErrorCode.NOT_AUTHENTICATED: 401,
        ErrorCode.SECOND_FACTOR_REQUIRED: 401,
        ErrorCode.ENROLLMENT_REQUIRED: 401,
        ErrorCode.INVALID_CREDENTIALS: 401,
        ErrorCode.INVALID_CODE: 401,
        ErrorCode.ORIGIN_REQUIRED: 403,
        ErrorCode.ORIGIN_MISMATCH: 403,
        ErrorCode.CROSS_SITE_REQUEST: 403,
        ErrorCode.INSECURE_TRANSPORT: 403,
        ErrorCode.TOTP_DISABLED_BY_POLICY: 403,
        ErrorCode.TOTP_REQUIRED: 403,
        ErrorCode.FORBIDDEN: 403,
        ErrorCode.ALREADY_AUTHENTICATED: 409,
        ErrorCode.TOTP_ALREADY_ENROLLED: 409,
        ErrorCode.TOTP_NOT_ENROLLED: 409,
        ErrorCode.NO_PENDING_ENROLLMENT: 409,
        ErrorCode.BODY_TOO_LARGE: 413,
        ErrorCode.TOO_MANY_ATTEMPTS: 429,
        ErrorCode.BUSY: 503,
        ErrorCode.STORE_UNAVAILABLE: 503,
    }
)

# One user-facing message per code. Static text only: no placeholders, no caller values.
DEFAULT_DETAIL: Mapping[ErrorCode, str] = MappingProxyType(
    {
        ErrorCode.INVALID_REQUEST: "The request is malformed.",
        ErrorCode.PASSWORD_POLICY: "The new password does not meet the password policy.",
        ErrorCode.NOT_AUTHENTICATED: "Authentication is required.",
        ErrorCode.SECOND_FACTOR_REQUIRED: "A second factor is required to finish signing in.",
        ErrorCode.ENROLLMENT_REQUIRED: "Two-factor enrollment is required to finish signing in.",
        ErrorCode.INVALID_CREDENTIALS: "Invalid username or password.",
        ErrorCode.INVALID_CODE: "Invalid or already used code.",
        ErrorCode.ORIGIN_REQUIRED: "The Origin header is required for this request.",
        ErrorCode.ORIGIN_MISMATCH: "The request origin does not match this server.",
        ErrorCode.CROSS_SITE_REQUEST: "Cross-site requests are not allowed.",
        ErrorCode.INSECURE_TRANSPORT: "This operation requires a secure (HTTPS) connection.",
        ErrorCode.TOTP_DISABLED_BY_POLICY: "Two-factor authentication is disabled by policy.",
        ErrorCode.TOTP_REQUIRED: "Two-factor authentication is required and cannot be disabled.",
        ErrorCode.ALREADY_AUTHENTICATED: "Already signed in.",
        ErrorCode.TOTP_ALREADY_ENROLLED: "Two-factor authentication is already enrolled.",
        ErrorCode.TOTP_NOT_ENROLLED: "Two-factor authentication is not enrolled.",
        ErrorCode.NO_PENDING_ENROLLMENT: "No two-factor enrollment is in progress.",
        ErrorCode.BODY_TOO_LARGE: "The request body is too large.",
        ErrorCode.TOO_MANY_ATTEMPTS: "Too many attempts. Try again later.",
        ErrorCode.BUSY: "The server is busy. Try again shortly.",
        ErrorCode.STORE_UNAVAILABLE: "The account store is temporarily unavailable.",
        ErrorCode.FORBIDDEN: "You do not have permission to do this.",
    }
)


class AuthError(OrchestratorError):
    """HTTP-mappable auth failure. ``detail`` never contains secrets or caller-supplied values."""

    def __init__(
        self,
        code: ErrorCode,
        detail: str | None = None,
        *,
        extra: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
        cause_for_log: str | None = None,
    ) -> None:
        self.code = code
        self.detail = detail if detail is not None else DEFAULT_DETAIL[code]
        self.extra: dict[str, object] = dict(extra) if extra else {}
        self.headers: dict[str, str] = dict(headers) if headers else {}
        # Logged once by the boundary for 5xx; never sent and never in str(self).
        self.cause_for_log = cause_for_log
        super().__init__(self.detail)

    @property
    def status(self) -> int:
        return STATUS_BY_CODE[self.code]


class TooManyAttemptsError(AuthError):
    """The address throttle or the account lockout is active (429)."""

    def __init__(self, retry_after_seconds: int, *, cause_for_log: str | None = None) -> None:
        super().__init__(
            ErrorCode.TOO_MANY_ATTEMPTS,
            extra={RETRY_AFTER_SECONDS_KEY: retry_after_seconds},
            headers={RETRY_AFTER_HEADER: str(retry_after_seconds)},
            cause_for_log=cause_for_log,
        )
        self.retry_after_seconds = retry_after_seconds


class BusyError(AuthError):
    """The hash queue is full (503, ``Retry-After: 1``)."""

    def __init__(self, *, cause_for_log: str | None = None) -> None:
        super().__init__(
            ErrorCode.BUSY,
            extra={RETRY_AFTER_SECONDS_KEY: BUSY_RETRY_AFTER_SECONDS},
            headers={RETRY_AFTER_HEADER: str(BUSY_RETRY_AFTER_SECONDS)},
            cause_for_log=cause_for_log,
        )


class StoreUnavailableError(AuthError):
    """The store or state file is locked too long, unreadable or corrupt (503)."""

    def __init__(self, *, cause_for_log: str | None = None) -> None:
        super().__init__(
            ErrorCode.STORE_UNAVAILABLE,
            headers={RETRY_AFTER_HEADER: str(STORE_RETRY_AFTER_SECONDS)},
            cause_for_log=cause_for_log,
        )


class AuthConfigError(ConfigError):
    """Startup/CLI configuration problem -> ``EXIT_CONFIG``; ``str()`` is the operator message."""


class AuthNotReadyError(AuthConfigError):
    """Zero users, or a missing, unsafe or corrupt store."""


class UnsafePermissionsError(AuthConfigError):
    """The store or state directory/file has unsafe ownership or permissions."""


class StoreCorruptError(OrchestratorError):
    """A store or state file is unreadable or fails validation."""


class StoreLockTimeoutError(OrchestratorError):
    """A store or state lock could not be acquired within its timeout."""
