"""The single throttle -> gate -> lockout -> verify -> record -> audit sequence (HLD 11.11; L3).

Every credential check in the local provider and the TOTP service goes through
:meth:`AttemptGuard.attempt`, so a password, a code, a recovery code and an enrollment token are
throttled, locked out and audited identically. FastAPI-free.

Contract (HLD 11.11, 11.15.2):

* the address throttle is consulted **before any hashing**; a throttled or locked-out attempt never
  calls ``verify`` and writes nothing;
* ``verify`` runs inside the per-username gate, so concurrent attempts on one account serialize;
* a ``BusyError`` from ``verify`` (the hash queue is full) counts as an **address** failure only --
  no lockout entry, so a flood cannot lock a victim out -- and is re-raised (D6);
* every other failure makes **exactly one** ``LockoutStore.record_failure`` write, for a known
  user and a phantom alike (S6), plus one address failure and one audit event;
* the guard emits ``*.failure`` and ``auth.lockout``; the caller emits everything else.

The ``ok_if`` / ``from_*`` helpers adapt store outcomes to :class:`AttemptResult`, so no flow
invents its own mapping.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from .audit import AuditEvent, AuditLog, AuditOutcome
from .constants import AUDIT_USERNAME_HASH_CHARS
from .errors import (
    AuthError,
    BusyError,
    ErrorCode,
    StoreCorruptError,
    StoreLockTimeoutError,
    StoreUnavailableError,
    TooManyAttemptsError,
)
from .lockouts import LockoutKey, LockoutPolicy, LockoutState, LockoutStore
from .model import AuditEventName
from .provider import ClientInfo
from .seams import Clock, run_sync
from .sessions import SessionRecord
from .store import OutcomeKind, RecoveryOutcome, TotpOutcome
from .throttle import AddressThrottle, UsernameGates

_T = TypeVar("_T")

# AttemptResult.reason vocabulary (no secrets; never a submitted value).
REASON_INVALID_CREDENTIALS = "invalid_credentials"
REASON_INVALID = "invalid"
REASON_REPLAYED = "replayed"


@dataclass(frozen=True)
class AttemptSubject:
    username: str  # normalized input (gate key)
    user_id: str | None  # None -> unknown name (a phantom)
    lockout_key: LockoutKey


@dataclass(frozen=True)
class AttemptResult:
    ok: bool
    reason: str | None = None  # "invalid_credentials" | "invalid" | "replayed"
    recovery_codes_remaining: int | None = None


def subject_for(session: SessionRecord) -> AttemptSubject:
    """The guard subject of a live session: always a known account."""
    return AttemptSubject(session.username, session.user_id, LockoutKey(user_id=session.user_id))


def ok_if(flag: bool, *, reason: str = REASON_INVALID_CREDENTIALS) -> AttemptResult:
    """A password verify (default reason) or an enrollment token (``reason="invalid"``)."""
    return AttemptResult(True) if flag else AttemptResult(False, reason)


def from_totp_outcome(outcome: TotpOutcome) -> AttemptResult:
    """OK / INVALID / REPLAYED. STALE raises ``NOT_AUTHENTICATED``: the credential changed under
    the session, nothing was guessed, so the guard (which sees an exception) counts no failure."""
    if outcome.kind is OutcomeKind.OK:
        return AttemptResult(True)
    if outcome.kind is OutcomeKind.REPLAYED:
        return AttemptResult(False, REASON_REPLAYED)
    if outcome.kind is OutcomeKind.INVALID:
        return AttemptResult(False, REASON_INVALID)
    raise AuthError(ErrorCode.NOT_AUTHENTICATED)


def from_recovery_outcome(outcome: RecoveryOutcome) -> AttemptResult:
    """OK (with the remaining count) / INVALID. STALE raises as in :func:`from_totp_outcome`."""
    if outcome.kind is OutcomeKind.OK:
        return AttemptResult(True, recovery_codes_remaining=outcome.remaining)
    if outcome.kind is OutcomeKind.INVALID:
        return AttemptResult(False, REASON_INVALID)
    raise AuthError(ErrorCode.NOT_AUTHENTICATED)


async def lockout_call(fn: Callable[..., _T], *args: object) -> _T:
    """Run a ``LockoutStore`` call off the loop; a lock timeout or corrupt file is a 503.

    The cause rides on the error (``cause_for_log``) for the HTTP boundary to log once. Shared by
    the guard and the provider, so no flow maps these errors differently.
    """
    try:
        return await run_sync(fn, *args)
    except (StoreLockTimeoutError, StoreCorruptError) as exc:
        raise StoreUnavailableError(cause_for_log=f"lockout state: {exc}") from exc


class AttemptGuard:
    def __init__(
        self,
        *,
        address_throttle: AddressThrottle,
        gates: UsernameGates,
        lockouts: LockoutStore,
        lockout_policy: LockoutPolicy,
        audit: AuditLog,
        realm: str,
        clock: Clock,
    ) -> None:
        self._throttle = address_throttle
        self._gates = gates
        self._lockouts = lockouts
        self._policy = lockout_policy
        self._audit = audit
        self._realm = realm
        self._clock = clock

    async def attempt(
        self,
        subject: AttemptSubject,
        client: ClientInfo,
        *,
        verify: Callable[[], Awaitable[AttemptResult]],
        failure_event: AuditEventName,
        reset_on_success: bool,
    ) -> AttemptResult:
        retry = self._throttle.retry_after(client.key)  # before ANY scrypt
        if retry:
            raise TooManyAttemptsError(retry)
        async with self._gates.hold(subject.username):
            state = await lockout_call(self._lockouts.state, subject.lockout_key)
            retry = self._policy.retry_after(state, self._clock.now_utc())
            if retry:
                raise TooManyAttemptsError(retry)  # uniform; no verify
            try:
                result = await verify()
            except BusyError:
                self._throttle.record_failure(client.key)  # D6: floods cannot starve
                raise
            if not result.ok:
                await self._record_failure(subject, client, result, failure_event)
                return result
            if reset_on_success and subject.user_id is not None:
                await lockout_call(self._lockouts.reset, subject.user_id)
            return result

    async def _record_failure(
        self,
        subject: AttemptSubject,
        client: ClientInfo,
        result: AttemptResult,
        failure_event: AuditEventName,
    ) -> None:
        # Exactly ONE lockout write per failed attempt, known user or phantom (S6).
        new = await lockout_call(self._lockouts.record_failure, subject.lockout_key, self._policy)
        self._throttle.record_failure(client.key)
        await run_sync(
            self._audit.record,
            self._event(failure_event, AuditOutcome.FAILURE, subject, client, reason=result.reason),
        )
        if new.failures == self._policy.threshold:
            await run_sync(
                self._audit.record,
                self._event(
                    AuditEventName.LOCKOUT,
                    AuditOutcome.INFO,
                    subject,
                    client,
                    lockout_failures=new.failures,
                    retry_after_seconds=self._retry_after(new),
                ),
            )

    def _event(
        self,
        name: AuditEventName,
        outcome: AuditOutcome,
        subject: AttemptSubject,
        client: ClientInfo,
        **details: str | int | None,
    ) -> AuditEvent:
        """A failure-class event: a known account by name, an unknown one by a keyed-digest prefix.

        v2.1 (security L1): the unknown name's ``username_hash`` is a PREFIX of the keyed
        ``LockoutStore.name_digest`` that is also the phantom's lockout key, never an unkeyed
        hash of the name.
        """
        known = subject.user_id is not None
        phantom = subject.lockout_key.phantom
        return AuditEvent(
            name,
            outcome,
            username=subject.username if known else None,
            username_hash=None if known or phantom is None else phantom[:AUDIT_USERNAME_HASH_CHARS],
            user_id=subject.user_id,
            realm=self._realm,
            client_addr=client.key,
            details=details,
        )

    def _retry_after(self, state: LockoutState) -> int | None:
        return self._policy.retry_after(state, self._clock.now_utc())
