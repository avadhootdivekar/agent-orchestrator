"""In-memory session table and the session lifecycle (HLD sections 11.10, 12.3; L3).

* One timeline: every deadline is on ``Clock.monotonic()`` (``CLOCK_BOOTTIME`` in production, so
  a suspended machine still idles out). The wall clock only labels ``auth_time``/``created_at``
  and the ``times()`` projections; it never decides expiry.
* Two bearer secrets per session (D25): the cookie *token* and the header *proof*. Only their
  sha256 digests are stored; neither raw value is in any ``repr``.
* ``SessionStore.get()``/``records()`` return copies, so a caller mutating one changes nothing
  until it goes through a :class:`SessionManager` method, and **every mutation ends in
  ``store.put()``** (reviewer finding R-5). A mutation of a record that has meanwhile been
  destroyed is dropped rather than resurrecting the session.
* ``SessionRecord.roles`` is a tuple (``replace()`` is a shallow copy, so a list would alias);
  :meth:`SessionManager.principal_for` is the only ``Principal`` builder and the only
  tuple -> list converter.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from .constants import (
    MAX_ENROLL_CONFIRM_ATTEMPTS,
    MAX_PARTIAL_SESSIONS,
    MAX_SECOND_FACTOR_ATTEMPTS,
    MAX_SESSIONS_PER_USER,
    MAX_SESSIONS_TOTAL,
    PARTIAL_SESSION_TTL_SECONDS,
    SESSION_PROOF_B64_CHARS,
    SESSION_PROOF_BYTES,
    SESSION_TOKEN_B64_CHARS,
    SESSION_TOKEN_BYTES,
)
from .model import AuthMethod, SecondFactor, SessionState
from .principal import Principal
from .provider import VerifiedIdentity
from .seams import Clock, Entropy

SESSION_ID_BYTES = 16  # 32 hex chars, the audit schema's ``session_id`` shape
SECOND_FACTOR_NONE = "none"

# amr values (RFC 8176; "rcv" = recovery code is a local extension)
AMR_PASSWORD = "pwd"
AMR_OTP = "otp"
AMR_RECOVERY = "rcv"
AMR_MFA = "mfa"

_B64URL_RE = re.compile(r"[A-Za-z0-9_-]+")


@dataclass
class SessionRecord:
    session_id: str  # non-secret, safe to log and audit
    token_hash: bytes = field(repr=False)  # sha256(cookie token)
    proof_hash: bytes = field(repr=False)  # sha256(session proof)
    realm: str
    store_id: str
    username: str
    user_id: str
    provider: str
    roles: tuple[str, ...]  # a tuple on purpose (see module docstring)
    state: SessionState
    auth_method: AuthMethod | None  # set only when FULL
    second_factor: str  # "none" | SecondFactor values
    auth_time: datetime | None  # set on a fresh FULL issue; kept on keep_absolute_deadline
    amr: tuple[str, ...]
    credential_epoch: int
    created_mono: float
    last_activity_mono: float
    absolute_deadline_mono: float
    created_at: datetime  # wall clock, display/audit only
    client_key: str
    second_factor_failures: int = 0
    pending_totp_secret: bytes | None = field(default=None, repr=False)
    pending_confirm_failures: int = 0


class SessionStore(ABC):
    """The future handoff/SSO seam. ``get`` and ``records`` return copies."""

    @abstractmethod
    def get(self, token_hash: bytes) -> SessionRecord | None: ...

    @abstractmethod
    def put(self, record: SessionRecord) -> None:
        """Insert or replace by ``token_hash``. EVERY mutation ends here."""

    @abstractmethod
    def delete(self, token_hash: bytes) -> None: ...

    @abstractmethod
    def records(self) -> list[SessionRecord]:
        """A snapshot copy, in insertion order."""


class InMemorySessionStore(SessionStore):
    """A dict under a lock; stores and returns copies so nobody aliases a live record."""

    def __init__(self) -> None:
        self._records: dict[bytes, SessionRecord] = {}
        self._lock = threading.Lock()

    def get(self, token_hash: bytes) -> SessionRecord | None:
        with self._lock:
            record = self._records.get(token_hash)
            return replace(record) if record is not None else None

    def put(self, record: SessionRecord) -> None:
        with self._lock:
            self._records[record.token_hash] = replace(record)

    def delete(self, token_hash: bytes) -> None:
        with self._lock:
            self._records.pop(token_hash, None)

    def records(self) -> list[SessionRecord]:
        with self._lock:
            return [replace(r) for r in self._records.values()]


@dataclass(frozen=True)
class IssuedSession:
    token: str = field(repr=False)  # the cookie value
    proof: str = field(repr=False)  # the X-AO-Session-Proof value
    record: SessionRecord


@dataclass(frozen=True)
class SessionTimes:
    """Wall-clock projections of the monotonic deadlines (aware UTC)."""

    idle_timeout_seconds: int
    idle_expires_at: datetime
    absolute_expires_at: datetime


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode_secret(value: str | None, *, chars: int, size: int) -> bytes | None:
    """Strictly decode an unpadded base64url secret, or ``None`` for anything malformed.

    Rejects wrong length, characters outside the alphabet and non-canonical encodings (set
    trailing bits), so exactly one string maps to each secret.
    """
    if value is None or len(value) != chars or _B64URL_RE.fullmatch(value) is None:
        return None
    # The alphabet check above leaves nothing for the decoder to reject (43 chars + 1 pad).
    raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if len(raw) != size or _b64url_encode(raw) != value:
        return None
    return raw


def _sha256(raw: bytes) -> bytes:
    return hashlib.sha256(raw).digest()


def amr_for(auth_method: AuthMethod, second_factor: str) -> tuple[str, ...]:
    """The RFC 8176 ``amr`` for a FULL session; ``ValueError`` for an inconsistent pair."""
    if second_factor == SECOND_FACTOR_NONE and auth_method == "password":
        return (AMR_PASSWORD,)
    if auth_method == "password+totp":
        if second_factor == SecondFactor.TOTP:
            return (AMR_PASSWORD, AMR_OTP, AMR_MFA)
        if second_factor == SecondFactor.RECOVERY_CODE:
            return (AMR_PASSWORD, AMR_RECOVERY, AMR_MFA)
    raise ValueError(f"inconsistent auth_method {auth_method!r} / second_factor {second_factor!r}")


def _is_expired(record: SessionRecord, now: float, idle_seconds: int) -> bool:
    if now >= record.absolute_deadline_mono:
        return True
    # Partial sessions never slide: only their absolute (TTL) deadline applies.
    return record.state is SessionState.FULL and now >= record.last_activity_mono + idle_seconds


class SessionManager:
    def __init__(
        self,
        store: SessionStore,
        *,
        realm: str,
        idle_seconds: int,
        absolute_seconds: int,
        clock: Clock,
        entropy: Entropy,
    ) -> None:
        self._store = store
        self._realm = realm
        self._idle_seconds = idle_seconds
        self._absolute_seconds = absolute_seconds
        self._clock = clock
        self._entropy = entropy

    # --- issue / lookup / proof ---

    def issue(
        self,
        identity: VerifiedIdentity,
        state: SessionState,
        *,
        client_key: str,
        auth_method: AuthMethod | None,
        second_factor: str = SECOND_FACTOR_NONE,
        replacing: SessionRecord | None = None,
        keep_absolute_deadline: bool = False,
    ) -> IssuedSession:
        """Create a session, retiring ``replacing`` (rotation) so its token and proof die."""
        full = state is SessionState.FULL
        if full and auth_method is None:
            raise ValueError("a FULL session requires an auth_method")
        amr = amr_for(auth_method, second_factor) if full and auth_method is not None else ()
        now = self._clock.monotonic()
        now_wall = self._clock.now_utc()
        keep = (
            keep_absolute_deadline
            and replacing is not None
            and replacing.state is SessionState.FULL
            and full
        )
        if replacing is not None:
            self._store.delete(replacing.token_hash)
        token = self._entropy.token_bytes(SESSION_TOKEN_BYTES)
        proof = self._entropy.token_bytes(SESSION_PROOF_BYTES)
        session_id = self._entropy.token_bytes(SESSION_ID_BYTES).hex()
        if keep and replacing is not None:
            absolute = replacing.absolute_deadline_mono
            auth_time = replacing.auth_time
        else:
            absolute = now + (self._absolute_seconds if full else PARTIAL_SESSION_TTL_SECONDS)
            auth_time = now_wall if full else None
        record = SessionRecord(
            session_id=session_id,
            token_hash=_sha256(token),
            proof_hash=_sha256(proof),
            realm=self._realm,
            store_id=identity.store_id,
            username=identity.username,
            user_id=identity.user_id,
            provider=identity.provider,
            roles=tuple(identity.roles),
            state=state,
            auth_method=auth_method if full else None,
            second_factor=str(second_factor),  # a SecondFactor member becomes its plain value
            auth_time=auth_time,
            amr=amr,
            credential_epoch=identity.credential_epoch,
            created_mono=now,
            last_activity_mono=now,
            absolute_deadline_mono=absolute,
            created_at=now_wall,
            client_key=client_key,
        )
        self._enforce_bounds(record, now)
        self._store.put(record)
        return IssuedSession(
            token=_b64url_encode(token), proof=_b64url_encode(proof), record=record
        )

    def lookup(self, cookie_value: str | None) -> SessionRecord | None:
        """The live session for a cookie value, or ``None``. Deletes an expired one."""
        raw = _decode_secret(cookie_value, chars=SESSION_TOKEN_B64_CHARS, size=SESSION_TOKEN_BYTES)
        if raw is None:
            return None
        record = self._store.get(_sha256(raw))
        if record is None:
            return None
        if record.realm != self._realm:
            # A record issued by another realm sharing this store (the SSO/handoff seam, S3):
            # never ours to honour, and not ours to delete or expire either.
            return None
        if _is_expired(record, self._clock.monotonic(), self._idle_seconds):
            self._store.delete(record.token_hash)
            return None
        return record

    def proof_matches(self, record: SessionRecord, proof_header: str | None) -> bool:
        raw = _decode_secret(proof_header, chars=SESSION_PROOF_B64_CHARS, size=SESSION_PROOF_BYTES)
        if raw is None:
            return False
        return hmac.compare_digest(_sha256(raw), record.proof_hash)

    # --- mutations (each ends in exactly one put) ---

    def touch(self, record: SessionRecord) -> None:
        """Slide the idle timer. FULL sessions only; a partial session never slides."""
        if record.state is not SessionState.FULL:
            return
        record.last_activity_mono = self._clock.monotonic()
        self._save(record)

    def record_second_factor_failure(self, record: SessionRecord) -> int:
        """Count a failed second factor; return the attempts left. Destroys the session at 0."""
        record.second_factor_failures += 1
        remaining = max(MAX_SECOND_FACTOR_ATTEMPTS - record.second_factor_failures, 0)
        if remaining == 0:
            self.destroy(record)
        else:
            self._save(record)
        return remaining

    def set_pending_secret(self, record: SessionRecord, secret: bytes) -> None:
        record.pending_totp_secret = secret
        record.pending_confirm_failures = 0
        self._save(record)

    def record_confirm_failure(self, record: SessionRecord) -> int:
        """Count a failed enrollment confirmation; clear the pending secret at the limit."""
        record.pending_confirm_failures += 1
        remaining = max(MAX_ENROLL_CONFIRM_ATTEMPTS - record.pending_confirm_failures, 0)
        if remaining == 0:
            record.pending_totp_secret = None
        self._save(record)
        return remaining

    def clear_pending(self, record: SessionRecord) -> None:
        record.pending_totp_secret = None
        record.pending_confirm_failures = 0
        self._save(record)

    def destroy(self, record: SessionRecord) -> None:
        self._store.delete(record.token_hash)

    def destroy_user_sessions(self, user_id: str, *, except_session_id: str | None = None) -> int:
        """Destroy every session of ``user_id`` (this realm) but ``except_session_id``.

        Keyed by ``user_id``, never by username, so a removed-then-re-added account of the same
        name is untouched. Returns the number destroyed.
        """
        count = 0
        for record in self._store.records():
            if record.user_id == user_id and record.session_id != except_session_id:
                self._store.delete(record.token_hash)
                count += 1
        return count

    # --- projections ---

    def principal_for(self, record: SessionRecord) -> Principal:
        """The ONLY ``Principal`` builder and the ONLY tuple -> list converter.

        ``roles`` is a fresh list on every call (security M5): mutating it affects neither the
        next request's principal nor the stored record.
        """
        if record.state is not SessionState.FULL:
            raise ValueError("a Principal exists only for a FULL session")
        if record.auth_method is None or record.auth_time is None:
            raise ValueError("a FULL session record must carry auth_method and auth_time")
        return Principal(
            record.username,
            record.auth_method,
            list(record.roles),
            user_id=record.user_id,
            realm=record.realm,
            session_id=record.session_id,
            amr=record.amr,
            auth_time=record.auth_time,
            provider=record.provider,
        )

    def times(self, record: SessionRecord) -> SessionTimes:
        mono = self._clock.monotonic()
        wall = self._clock.now_utc()
        absolute_at = wall + timedelta(seconds=record.absolute_deadline_mono - mono)
        if record.state is not SessionState.FULL:
            return SessionTimes(PARTIAL_SESSION_TTL_SECONDS, absolute_at, absolute_at)
        idle_at = wall + timedelta(seconds=record.last_activity_mono + self._idle_seconds - mono)
        return SessionTimes(self._idle_seconds, min(idle_at, absolute_at), absolute_at)

    # --- internals ---

    def _save(self, record: SessionRecord) -> None:
        """``put`` unless the session was destroyed meanwhile (never resurrect a revoked one)."""
        if self._store.get(record.token_hash) is not None:
            self._store.put(record)

    def _evict(self, victim: SessionRecord, live: list[SessionRecord]) -> None:
        self._store.delete(victim.token_hash)
        live.remove(victim)

    @staticmethod
    def _oldest(candidates: list[SessionRecord]) -> SessionRecord:
        # Least recently active first; creation time breaks ties (min keeps insertion order).
        return min(candidates, key=lambda r: (r.last_activity_mono, r.created_mono))

    def _enforce_bounds(self, new: SessionRecord, now: float) -> None:
        """Garbage-collect expired sessions, then evict oldest-first to stay within the caps."""
        live: list[SessionRecord] = []
        for record in self._store.records():
            if _is_expired(record, now, self._idle_seconds):
                self._store.delete(record.token_hash)
            else:
                live.append(record)
        same_user = [r for r in live if r.user_id == new.user_id]
        while len(same_user) >= MAX_SESSIONS_PER_USER:
            victim = self._oldest(same_user)
            same_user.remove(victim)
            self._evict(victim, live)
        if new.state is not SessionState.FULL:
            partial = [r for r in live if r.state is not SessionState.FULL]
            while len(partial) >= MAX_PARTIAL_SESSIONS:
                victim = self._oldest(partial)
                partial.remove(victim)
                self._evict(victim, live)
        while live and len(live) >= MAX_SESSIONS_TOTAL:
            self._evict(self._oldest(live), live)
