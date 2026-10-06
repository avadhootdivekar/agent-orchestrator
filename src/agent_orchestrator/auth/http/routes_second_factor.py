"""The second-factor routes E3-E7 (HLD 2.4, 11.18, 14.2-14.4; L4).

Owner: T-KQ6ZrY (T-rpKCjP created the registered stub). ``add_totp_routes`` registers, for the
local-password provider, ``POST /api/auth/totp/{verify, enroll/begin, enroll/confirm, disable,
recovery-codes}`` over the runtime's ``LocalTotpService`` (``auth/totp_service.py``);
it returns at once when ``runtime.totp`` is ``None`` (a provider without a local second factor).

This module imports its helpers from ``routes.py``; ``routes.py`` imports it lazily inside
``install_auth_routes`` (never at its top), so the import cycle cannot bite.

Rules this module follows (the same as ``routes.py``):

* **Flat, ``async def`` routes** (``app.add_api_route``), so ``AuthMiddleware`` classifies them.
* **R1a:** ``from __future__ import annotations`` is on, so every name a handler annotation uses
  (``Request``) is imported at module scope -- never only under ``TYPE_CHECKING``.
* **Who counts what** (S22): the service counts a failed code, token or re-auth password against
  the account lockout (via the shared ``AttemptGuard``); THIS module counts the per-session
  budgets -- the E3 attempts of a partial session and the E5 confirm attempts of a pending secret.
* **Rotation** (D25): every success that changes the session's identity or privilege ends in a
  fresh cookie AND a fresh proof; the old pair dies with the replaced record.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import Response

from ..audit import AuditEvent, AuditOutcome
from ..constants import (
    AUTH_ENROLL_BEGIN_PATH,
    AUTH_ENROLL_CONFIRM_PATH,
    AUTH_RECOVERY_CODES_PATH,
    AUTH_TOTP_DISABLE_PATH,
    AUTH_TOTP_VERIFY_PATH,
    LOCAL_PROVIDER_ID,
    MAX_LOGIN_PASSWORD_CHARS,
)
from ..errors import AuthError, ErrorCode
from ..model import AuditEventName, AuthMethod, SecondFactor, SessionState
from ..provider import ClientInfo, VerifiedIdentity
from ..runtime import AuthRuntime
from ..seams import run_sync
from ..sessions import SessionRecord
from .responses import clear_cookie_header
from .routes import (
    FIELD_CURRENT_PASSWORD,
    _append_header,
    _json,
    _require_full_session,
    _scope_session,
    _session_response,
    _text,
    auth_error_handler,
    client_info,
    read_json_object,
    register_route_builder,
    require_str,
    rotate,
    user_payload,
)

# --- Named constants (no magic literals) ---
FIELD_CODE = "code"
FIELD_RECOVERY_CODE = "recovery_code"
FIELD_ENROLLMENT_TOKEN = "enrollment_token"
# A TOTP code, a recovery code or an enrollment token with generous room for spaces and dashes.
# The service normalizes and judges the value; this bound only caps what is read and hashed.
MAX_CODE_FIELD_CHARS = 64
ATTEMPTS_REMAINING_KEY = "attempts_remaining"
AUTH_METHOD_PASSWORD: AuthMethod = "password"
AUTH_METHOD_PASSWORD_TOTP: AuthMethod = "password+totp"
SECOND_FACTOR_NONE = "none"
AUDIT_DETAIL_RECOVERY_REMAINING = "recovery_codes_remaining"


def add_totp_routes(app: FastAPI, runtime: AuthRuntime) -> None:
    """Register E3-E7 for a runtime with a TOTP service; a no-op without one."""
    service = runtime.totp
    if service is None or _already_installed(app):
        return

    async def auth_totp_verify(request: Request) -> Response:
        """E3: the second step of a login (TOTP or recovery code)."""
        session = _proven_session(request)
        if session.state is SessionState.FULL:
            raise AuthError(ErrorCode.ALREADY_AUTHENTICATED)
        if session.state is not SessionState.PARTIAL_SECOND_FACTOR:  # unreachable: middleware
            raise AuthError(session.state.denial_code)
        data = await read_json_object(request, allowed=frozenset({FIELD_CODE, FIELD_RECOVERY_CODE}))
        if (FIELD_CODE in data) == (FIELD_RECOVERY_CODE in data):
            raise AuthError(
                ErrorCode.INVALID_REQUEST,
                f"Provide exactly one of {FIELD_CODE} and {FIELD_RECOVERY_CODE}.",
            )
        code = require_str(data, FIELD_CODE, max_len=MAX_CODE_FIELD_CHARS, optional=True)
        recovery = require_str(
            data, FIELD_RECOVERY_CODE, max_len=MAX_CODE_FIELD_CHARS, optional=True
        )
        info = client_info(request.scope, runtime)
        try:
            result = await service.verify_second_factor(
                session, code=code, recovery_code=recovery, client=info
            )
        except AuthError as exc:
            if exc.code is ErrorCode.INVALID_CODE:
                remaining = runtime.sessions.record_second_factor_failure(session)
                exc.extra[ATTEMPTS_REMAINING_KEY] = remaining
                if remaining == 0:  # the partial session is gone: tell the browser
                    return await _error_clearing_cookie(request, runtime, exc, info)
            elif exc.code is ErrorCode.NOT_AUTHENTICATED:
                # The identity went stale (or the user vanished): the partial session is dead.
                runtime.sessions.destroy(session)
                return await _error_clearing_cookie(request, runtime, exc, info)
            raise
        issued = runtime.sessions.issue(
            _identity_of(session, session.credential_epoch),
            SessionState.FULL,
            client_key=info.key,
            auth_method=AUTH_METHOD_PASSWORD_TOTP,
            second_factor=result.method.value,
            replacing=session,  # a fresh absolute deadline: the login completes here
        )
        await _audit(runtime, AuditEventName.LOGIN_SUCCESS, issued.record, info)
        used_recovery = result.method is SecondFactor.RECOVERY_CODE
        if used_recovery:
            await _audit(
                runtime,
                AuditEventName.RECOVERY_CODE_USED,
                issued.record,
                info,
                details={AUDIT_DETAIL_RECOVERY_REMAINING: result.recovery_codes_remaining},
            )
        body = {
            "state": SessionState.FULL.api_state,
            "user": user_payload(runtime, issued.record),
            "used_recovery_code": used_recovery,
        }
        return _session_response(runtime, issued, body, info)

    async def auth_totp_enroll_begin(request: Request) -> Response:
        """E4: start an enrollment; the pending secret lives only in the session."""
        session = _proven_session(request)
        if session.state is SessionState.FULL:  # voluntary: re-authenticate with the password
            data = await read_json_object(
                request,
                allowed=frozenset({FIELD_CURRENT_PASSWORD}),
                required=frozenset({FIELD_CURRENT_PASSWORD}),
            )
            password: str | None = _text(
                require_str(data, FIELD_CURRENT_PASSWORD, max_len=MAX_LOGIN_PASSWORD_CHARS)
            )
            token: str | None = None
        elif session.state is SessionState.PARTIAL_ENROLL:  # forced: the CLI-issued token
            data = await read_json_object(request, allowed=frozenset({FIELD_ENROLLMENT_TOKEN}))
            # Optional on purpose: a missing token is a COUNTED failure inside the service.
            token = require_str(
                data, FIELD_ENROLLMENT_TOKEN, max_len=MAX_CODE_FIELD_CHARS, optional=True
            )
            password = None
        else:  # a second-factor-pending session: unreachable (the middleware decides)
            raise AuthError(session.state.denial_code)
        info = client_info(request.scope, runtime)
        challenge = await service.begin_enrollment(
            session, info, current_password=password, enrollment_token=token
        )
        runtime.sessions.set_pending_secret(session, challenge.secret)
        return _json(
            {
                "secret": challenge.secret_b32,
                "otpauth_uri": challenge.otpauth_uri,
                "issuer": challenge.issuer,
                "account": challenge.account,
                "algorithm": challenge.algorithm,
                "digits": challenge.digits,
                "period": challenge.period,
            }
        )

    async def auth_totp_enroll_confirm(request: Request) -> Response:
        """E5: confirm the pending secret with a code; on success the session is rotated."""
        data = await read_json_object(
            request, allowed=frozenset({FIELD_CODE}), required=frozenset({FIELD_CODE})
        )
        code = _text(require_str(data, FIELD_CODE, max_len=MAX_CODE_FIELD_CHARS))
        session = _proven_session(request)
        if session.state is SessionState.PARTIAL_SECOND_FACTOR:  # unreachable: middleware
            raise AuthError(session.state.denial_code)
        info = client_info(request.scope, runtime)
        # Transport first (a remote plain-HTTP client learns nothing else), then the 409.
        service.require_secure_transport(info)
        if session.pending_totp_secret is None:
            raise AuthError(ErrorCode.NO_PENDING_ENROLLMENT)
        completes_login = session.state is SessionState.PARTIAL_ENROLL
        try:
            result = await service.confirm_enrollment(session, code, info)
        except AuthError as exc:
            if exc.code is ErrorCode.INVALID_CODE:
                # The wrong code is checked against the pending secret, not the store, so the
                # session (not the guard) counts it; the secret is discarded at the limit.
                exc.extra[ATTEMPTS_REMAINING_KEY] = runtime.sessions.record_confirm_failure(session)
            raise
        rotated = rotate(
            runtime,
            session,
            epoch=result.credential_epoch,
            auth_method=AUTH_METHOD_PASSWORD_TOTP,
            second_factor=SecondFactor.TOTP.value,
            keep_absolute=not completes_login,  # a voluntary enrollment keeps its deadline
        )
        if completes_login:
            await _audit(runtime, AuditEventName.LOGIN_SUCCESS, rotated.record, info)
        body = {
            "state": SessionState.FULL.api_state,
            "user": user_payload(runtime, rotated.record),
            "recovery_codes": result.recovery_codes,
        }
        return _session_response(runtime, rotated, body, info)

    async def auth_totp_disable(request: Request) -> Response:
        """E6: turn TOTP off (only where it is optional); needs the password and a code."""
        current, code = await _reauth_fields(request)
        session = _require_full_session(request)
        info = client_info(request.scope, runtime)
        epoch = await service.disable_totp(session, current, code, info)
        rotated = rotate(
            runtime,
            session,
            epoch=epoch,
            auth_method=AUTH_METHOD_PASSWORD,
            second_factor=SECOND_FACTOR_NONE,
            keep_absolute=True,
        )
        body = {
            "state": SessionState.FULL.api_state,
            "user": user_payload(runtime, rotated.record),
        }
        return _session_response(runtime, rotated, body, info)

    async def auth_recovery_regenerate(request: Request) -> Response:
        """E7: replace all recovery codes; needs the password and a code."""
        current, code = await _reauth_fields(request)
        session = _require_full_session(request)
        info = client_info(request.scope, runtime)
        epoch, codes = await service.regenerate_recovery_codes(session, current, code, info)
        rotated = rotate(
            runtime,
            session,
            epoch=epoch,
            auth_method=session.auth_method,  # the login method is unchanged
            second_factor=session.second_factor,
            keep_absolute=True,
        )
        body = {"recovery_codes": codes, "user": user_payload(runtime, rotated.record)}
        return _session_response(runtime, rotated, body, info)

    app.add_api_route(AUTH_TOTP_VERIFY_PATH, auth_totp_verify, methods=["POST"])
    app.add_api_route(AUTH_ENROLL_BEGIN_PATH, auth_totp_enroll_begin, methods=["POST"])
    app.add_api_route(AUTH_ENROLL_CONFIRM_PATH, auth_totp_enroll_confirm, methods=["POST"])
    app.add_api_route(AUTH_TOTP_DISABLE_PATH, auth_totp_disable, methods=["POST"])
    app.add_api_route(AUTH_RECOVERY_CODES_PATH, auth_recovery_regenerate, methods=["POST"])


# ---------------------------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------------------------


def _already_installed(app: FastAPI) -> bool:
    """Idempotence: a second call (a re-imported module registers a second builder) adds nothing."""
    return any(getattr(route, "path", None) == AUTH_TOTP_VERIFY_PATH for route in app.router.routes)


def _proven_session(request: Request) -> SessionRecord:
    """The proof-verified session of a non-PUBLIC route. The middleware guarantees it; re-check."""
    session, proof_ok = _scope_session(request)
    if session is None or not proof_ok:
        raise AuthError(ErrorCode.NOT_AUTHENTICATED)
    return session


async def _reauth_fields(request: Request) -> tuple[str, str]:
    """``(current_password, code)`` of E6/E7: both required, a TOTP or a recovery code."""
    data = await read_json_object(
        request,
        allowed=frozenset({FIELD_CURRENT_PASSWORD, FIELD_CODE}),
        required=frozenset({FIELD_CURRENT_PASSWORD, FIELD_CODE}),
    )
    current = _text(require_str(data, FIELD_CURRENT_PASSWORD, max_len=MAX_LOGIN_PASSWORD_CHARS))
    code = _text(require_str(data, FIELD_CODE, max_len=MAX_CODE_FIELD_CHARS))
    return current, code


def _identity_of(session: SessionRecord, epoch: int) -> VerifiedIdentity:
    """A FULL-state identity for ``session``'s account at ``epoch`` (login completion, E3)."""
    return VerifiedIdentity(
        session.user_id,
        session.username,
        session.roles,
        epoch,
        session.store_id,
        next_state=SessionState.FULL,
        provider=session.provider,
    )


async def _error_clearing_cookie(
    request: Request, runtime: AuthRuntime, exc: AuthError, info: ClientInfo
) -> Response:
    """The normal error response plus a clear-cookie header: the session is gone server-side."""
    response = await auth_error_handler(request, exc)
    _append_header(response, clear_cookie_header(runtime.realm, secure=info.secure))
    return response


async def _audit(
    runtime: AuthRuntime,
    event: AuditEventName,
    session: SessionRecord,
    info: ClientInfo,
    *,
    details: Mapping[str, Any] | None = None,
) -> None:
    await run_sync(
        runtime.audit.record,
        AuditEvent(
            event,
            AuditOutcome.SUCCESS,
            username=session.username,
            user_id=session.user_id,
            realm=runtime.realm.id,
            client_addr=info.key,
            session_id=session.session_id,
            auth_method=session.auth_method,
            details=dict(details or {}),
        ),
    )


# Registered at import (HLD 11.18); ``install_auth_routes`` runs it after the password builder.
register_route_builder(LOCAL_PROVIDER_ID, add_totp_routes)

__all__ = ["add_totp_routes"]
