"""The second factor of the local provider: TOTP verify, enrollment, disable, recovery codes
(HLD 11.15.1, 11.15.5, 11.15.6; L3).

FastAPI-free. Every credential check (a code, a recovery code, an enrollment token, the re-auth
password) goes through :class:`~.guard.AttemptGuard`, so each is throttled, locked out and audited
like a login. Every store, lockout and audit call made from a coroutine goes through
``seams.run_sync`` (the event loop never blocks on a file lock).

Who counts what (HLD 11.15.2, S22):

* the **guard** counts a failed second-factor code, a failed re-auth password and a failed (or
  missing) enrollment token -- exactly one ``record_failure`` per failed attempt;
* the **route** counts the wrong codes of an enrollment *confirm* on the session (they are checked
  against the pending secret, not the store), so this service never touches the session;
* a stale identity (the user's credential changed under the session) is not a guess: it raises
  ``NOT_AUTHENTICATED`` from inside the guard's ``verify`` and is therefore not counted.

Web-initiated credential writes go through ``store.with_identity`` (D10); the CLI uses the bare
mutations.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from .audit import AuditEvent, AuditLog, AuditOutcome
from .constants import (
    SOURCE_WEB,
    TOTP_ALGORITHM,
    TOTP_DIGITS,
    TOTP_PERIOD_SECONDS,
)
from .errors import (
    AuthError,
    ErrorCode,
    StoreCorruptError,
    StoreLockTimeoutError,
    StoreUnavailableError,
)
from .guard import (
    REASON_INVALID,
    AttemptGuard,
    AttemptResult,
    from_recovery_outcome,
    from_totp_outcome,
    lockout_call,
    ok_if,
    subject_for,
)
from .local_provider import LocalPasswordProvider
from .lockouts import LockoutStore
from .model import AuditEventName, SecondFactor, SessionState, TotpPolicy, TotpRequirement
from .policy import totp_requirement
from .provider import ClientInfo
from .recovery import generate_recovery_codes, new_recovery_records, normalize_recovery_code
from .seams import SYSTEM_ENTROPY, Clock, Entropy, run_sync
from .sessions import SessionRecord
from .settings import AuthSettings
from .store import (
    AlreadyEnrolledError,
    NotEnrolledError,
    StaleIdentityError,
    UserRecord,
    UserStore,
    UserStoreFile,
    cas_mark_login,
    consume_enrollment_token,
    consume_recovery,
    consume_totp,
    enroll_totp,
    remove_totp,
    replace_recovery_codes,
    with_identity,
)
from .totp import (
    b32encode_secret,
    match_totp_step,
    new_totp_secret,
    normalize_totp_code,
    otpauth_uri,
)

_T = TypeVar("_T")


@dataclass(frozen=True)
class SecondFactorResult:
    method: SecondFactor
    recovery_codes_remaining: int | None  # only for a recovery-code login


@dataclass(frozen=True)
class EnrollmentChallenge:
    """What ``begin`` hands the route: the route stores ``secret`` on the session as pending."""

    secret: bytes = field(repr=False)
    secret_b32: str = field(repr=False)
    otpauth_uri: str = field(repr=False)  # embeds the secret
    issuer: str
    account: str
    algorithm: str = TOTP_ALGORITHM
    digits: int = TOTP_DIGITS
    period: int = TOTP_PERIOD_SECONDS


@dataclass(frozen=True)
class EnrollmentResult:
    credential_epoch: int
    recovery_codes: list[str] = field(repr=False)


def classify_code(code: str) -> tuple[SecondFactor, str] | None:
    """The one place that tells a TOTP code from a recovery code in a single ``code`` field.

    Spaces (and hyphens) stripped: exactly six ASCII digits -> TOTP; else a string that
    normalizes to a 16-character Crockford recovery code -> recovery code; else ``None`` (a
    counted, invalid attempt that never reaches the store).
    """
    totp = normalize_totp_code(code)
    if totp is not None:
        return SecondFactor.TOTP, totp
    recovery = normalize_recovery_code(code)
    if recovery is not None:
        return SecondFactor.RECOVERY_CODE, recovery
    return None


def _invalid_code() -> AuthError:
    return AuthError(ErrorCode.INVALID_CODE, extra={"reason": REASON_INVALID})


class LocalTotpService:
    def __init__(
        self,
        store: UserStore,
        lockouts: LockoutStore,
        guard: AttemptGuard,
        provider: LocalPasswordProvider,
        settings: AuthSettings,
        audit: AuditLog,
        clock: Clock,
        entropy: Entropy = SYSTEM_ENTROPY,
        *,
        realm: str | None = None,
    ) -> None:
        self._store = store
        self._lockouts = lockouts
        self._guard = guard
        self._provider = provider
        self._settings = settings
        self._audit = audit
        self._clock = clock
        self._entropy = entropy
        self._realm = realm  # audit label only; the guard carries its own for failure events

    # -- second factor at login ------------------------------------------------------------------

    async def verify_second_factor(
        self,
        session: SessionRecord,
        *,
        code: str | None = None,
        recovery_code: str | None = None,
        client: ClientInfo,
    ) -> SecondFactorResult:
        """HLD 11.15.5. ``code`` selects TOTP; otherwise ``recovery_code`` is checked.

        Works under every policy: an enrolled user is always challenged (S9). On success the
        account lockout is reset and ``last_login_at`` stamped; the route issues the full session.
        """
        if code is not None:
            method, normalized = SecondFactor.TOTP, normalize_totp_code(code)
        else:
            method = SecondFactor.RECOVERY_CODE
            normalized = None if recovery_code is None else normalize_recovery_code(recovery_code)
        result = await self._check_code(
            session,
            client,
            method,
            normalized,
            failure_event=AuditEventName.SECOND_FACTOR_FAILURE,
            reset_on_success=True,
            not_enrolled=ErrorCode.NOT_AUTHENTICATED,  # an enrolled session cannot lose TOTP
        )
        if not result.ok:
            raise AuthError(ErrorCode.INVALID_CODE, extra={"reason": result.reason})
        now = self._clock.now_utc()
        await self._mutate(
            lambda f: cas_mark_login(
                f,
                session.username,
                user_id=session.user_id,
                epoch=session.credential_epoch,
                now=now,
            )
        )
        return SecondFactorResult(method, result.recovery_codes_remaining)

    # -- enrollment ------------------------------------------------------------------------------

    async def begin_enrollment(
        self,
        session: SessionRecord,
        client: ClientInfo,
        *,
        current_password: str | None = None,
        enrollment_token: str | None = None,
    ) -> EnrollmentChallenge:
        """HLD 11.15.6. A FULL session re-authenticates with its password; a forced-enrollment
        (PARTIAL_ENROLL) session presents the CLI-issued token, which this call CONSUMES."""
        self.require_secure_transport(client)
        self._require_policy_on()
        rec = self._current_record(session)
        if rec.totp is not None:
            raise AuthError(ErrorCode.TOTP_ALREADY_ENROLLED)
        if session.state is SessionState.FULL:
            if current_password is None:
                raise AuthError(ErrorCode.INVALID_REQUEST)
            await self._provider.verify_current_password(session, current_password, client)
        elif session.state is SessionState.PARTIAL_ENROLL:
            await self._consume_enrollment_token(session, client, enrollment_token)
        else:
            raise AuthError(session.state.denial_code)
        secret = new_totp_secret(self._entropy)
        secret_b32 = b32encode_secret(secret)
        issuer = self._settings.totp_issuer
        return EnrollmentChallenge(
            secret,
            secret_b32,
            otpauth_uri(secret_b32, issuer=issuer, account=session.username),
            issuer,
            session.username,
        )

    async def _consume_enrollment_token(
        self, session: SessionRecord, client: ClientInfo, token: str | None
    ) -> None:
        normalized = None if token is None else normalize_recovery_code(token)

        async def verify() -> AttemptResult:
            if normalized is None:  # missing or malformed: a counted failure, no store write
                return ok_if(False, reason=REASON_INVALID)
            now = self._clock.now_utc()
            consumed = await self._mutate(
                lambda f: consume_enrollment_token(
                    f, session.username, normalized, user_id=session.user_id, now=now
                )
            )
            return ok_if(consumed, reason=REASON_INVALID)

        result = await self._guard.attempt(
            subject_for(session),
            client,
            verify=verify,
            failure_event=AuditEventName.ENROLLMENT_TOKEN_FAILURE,
            reset_on_success=False,
        )
        if not result.ok:
            raise _invalid_code()

    async def confirm_enrollment(
        self, session: SessionRecord, code: str, client: ClientInfo
    ) -> EnrollmentResult:
        """Check ``code`` against the session's pending secret and persist the enrollment.

        The pending-attempt counter is the route's; a wrong code here only raises
        ``INVALID_CODE`` and writes nothing.
        """
        self.require_secure_transport(client)
        self._require_policy_on()
        secret = session.pending_totp_secret
        if secret is None:  # confirm without a begin
            raise AuthError(ErrorCode.INVALID_REQUEST)
        normalized = normalize_totp_code(code)
        now = self._clock.now_utc()
        step = None if normalized is None else match_totp_step(secret, normalized, now.timestamp())
        if step is None:
            raise _invalid_code()
        codes = generate_recovery_codes(self._entropy)
        records = new_recovery_records(codes, self._entropy)
        completes_login = session.state is SessionState.PARTIAL_ENROLL
        try:
            epoch = await self._identity_write(
                session,
                lambda f: enroll_totp(
                    f,
                    session.username,
                    user_id=session.user_id,
                    epoch=session.credential_epoch,
                    secret_b32=b32encode_secret(secret),
                    step=step,
                    records=records,
                    now=now,
                    completes_login=completes_login,
                ),
            )
        except AlreadyEnrolledError as exc:
            raise AuthError(ErrorCode.TOTP_ALREADY_ENROLLED) from exc
        if completes_login:
            await lockout_call(self._lockouts.reset, session.user_id)
        await self._audit_web(AuditEventName.TOTP_ENROLLED, session, client)
        return EnrollmentResult(epoch, codes)

    # -- disable / regenerate --------------------------------------------------------------------

    async def disable_totp(
        self, session: SessionRecord, current_password: str, code: str, client: ClientInfo
    ) -> int:
        """Self-service disable: only where TOTP is optional for this user (checked FIRST, before
        any password work). Returns the new ``credential_epoch``; the route rotates the session."""
        rec = self._current_record(session)
        if totp_requirement(self._settings.totp, rec.totp_required) is not TotpRequirement.NONE:
            raise AuthError(ErrorCode.TOTP_REQUIRED)
        if rec.totp is None:
            raise AuthError(ErrorCode.TOTP_NOT_ENROLLED)
        await self._reauthenticate(session, current_password, code, client)
        now = self._clock.now_utc()
        try:
            epoch = await self._identity_write(
                session, lambda f: remove_totp(f, session.username, now=now, set_required=None)
            )
        except NotEnrolledError as exc:  # a concurrent disable won the race
            raise AuthError(ErrorCode.TOTP_NOT_ENROLLED) from exc
        await self._audit_web(AuditEventName.TOTP_DISABLED, session, client)
        return epoch

    async def regenerate_recovery_codes(
        self, session: SessionRecord, current_password: str, code: str, client: ClientInfo
    ) -> tuple[int, list[str]]:
        """Replace all recovery codes: ``(new credential_epoch, the ten new codes)``."""
        self.require_secure_transport(client)
        rec = self._current_record(session)
        if rec.totp is None:
            raise AuthError(ErrorCode.TOTP_NOT_ENROLLED)
        await self._reauthenticate(session, current_password, code, client)
        codes = generate_recovery_codes(self._entropy)
        records = new_recovery_records(codes, self._entropy)
        now = self._clock.now_utc()
        try:
            epoch = await self._identity_write(
                session, lambda f: replace_recovery_codes(f, session.username, records, now=now)
            )
        except NotEnrolledError as exc:
            raise AuthError(ErrorCode.TOTP_NOT_ENROLLED) from exc
        await self._audit_web(AuditEventName.RECOVERY_CODES_REGENERATED, session, client)
        return epoch, codes

    async def _reauthenticate(
        self, session: SessionRecord, current_password: str, code: str, client: ClientInfo
    ) -> None:
        """Password, then a TOTP or recovery code: both guarded, neither resets the lockout."""
        await self._provider.verify_current_password(session, current_password, client)
        classified = classify_code(code)
        method, normalized = classified if classified is not None else (SecondFactor.TOTP, None)
        result = await self._check_code(
            session,
            client,
            method,
            normalized,
            failure_event=AuditEventName.SECOND_FACTOR_FAILURE,
            reset_on_success=False,
            not_enrolled=ErrorCode.TOTP_NOT_ENROLLED,
        )
        if not result.ok:
            raise AuthError(ErrorCode.INVALID_CODE, extra={"reason": result.reason})

    # -- shared plumbing -------------------------------------------------------------------------

    async def _check_code(
        self,
        session: SessionRecord,
        client: ClientInfo,
        method: SecondFactor,
        normalized: str | None,
        *,
        failure_event: AuditEventName,
        reset_on_success: bool,
        not_enrolled: ErrorCode,
    ) -> AttemptResult:
        """One guarded code check: ONE ``store.mutate`` (consume), or none for a malformed code.

        STALE raises ``NOT_AUTHENTICATED`` inside ``verify`` (``from_*_outcome``), so the guard
        does not count it.
        """

        async def verify() -> AttemptResult:
            if normalized is None:  # malformed: a counted failure that never reaches the store
                return ok_if(False, reason=REASON_INVALID)
            now = self._clock.now_utc()
            try:
                if method is SecondFactor.TOTP:
                    totp_outcome = await self._mutate(
                        lambda f: consume_totp(
                            f,
                            session.username,
                            normalized,
                            user_id=session.user_id,
                            epoch=session.credential_epoch,
                            now_unix=now.timestamp(),
                        )
                    )
                    return from_totp_outcome(totp_outcome)
                recovery_outcome = await self._mutate(
                    lambda f: consume_recovery(
                        f,
                        session.username,
                        normalized,
                        user_id=session.user_id,
                        epoch=session.credential_epoch,
                        now=now,
                    )
                )
                return from_recovery_outcome(recovery_outcome)
            except NotEnrolledError as exc:
                raise AuthError(not_enrolled) from exc

        return await self._guard.attempt(
            subject_for(session),
            client,
            verify=verify,
            failure_event=failure_event,
            reset_on_success=reset_on_success,
        )

    def require_secure_transport(self, client: ClientInfo) -> None:
        """D7/S23: a secret or recovery codes must not cross plain HTTP from a non-loopback peer.

        Public so the E5 route can apply it BEFORE its own "no pending enrollment" check.

        ``is_loopback`` already folds in the Host and forwarding-header checks (v2.1, M2).
        """
        if not client.secure and not client.is_loopback:
            raise AuthError(ErrorCode.INSECURE_TRANSPORT)

    def _require_policy_on(self) -> None:
        if self._settings.totp is TotpPolicy.OFF:
            raise AuthError(ErrorCode.TOTP_DISABLED_BY_POLICY)

    def _current_record(self, session: SessionRecord) -> UserRecord:
        try:
            rec = self._store.user_by_id(session.user_id)
        except StoreCorruptError as exc:
            raise StoreUnavailableError(cause_for_log=f"user store: {exc}") from exc
        if rec is None or rec.credential_epoch != session.credential_epoch:
            raise AuthError(ErrorCode.NOT_AUTHENTICATED)
        return rec

    async def _mutate(self, fn: Callable[[UserStoreFile], _T]) -> _T:
        try:
            return await run_sync(self._store.mutate, fn)
        except (StoreLockTimeoutError, StoreCorruptError) as exc:
            raise StoreUnavailableError(cause_for_log=f"user store: {exc}") from exc

    async def _identity_write(
        self, session: SessionRecord, mutation: Callable[[UserStoreFile], _T]
    ) -> _T:
        """A web-initiated credential write: re-checks ``user_id`` + epoch inside the lock (D10)."""
        guarded = with_identity(
            mutation,
            username=session.username,
            user_id=session.user_id,
            epoch=session.credential_epoch,
        )
        try:
            return await self._mutate(guarded)
        except StaleIdentityError as exc:
            raise AuthError(ErrorCode.NOT_AUTHENTICATED) from exc

    async def _audit_web(
        self, name: AuditEventName, session: SessionRecord, client: ClientInfo
    ) -> None:
        await run_sync(
            self._audit.record,
            AuditEvent(
                name,
                AuditOutcome.SUCCESS,
                username=session.username,
                user_id=session.user_id,
                realm=self._realm,
                client_addr=client.key,
                details={"source": SOURCE_WEB},
            ),
        )
