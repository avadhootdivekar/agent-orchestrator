"""The local username + password provider: the login side (HLD 11.15.3-11.15.7; L3).

Everything but the second factor (``totp_service.py``). FastAPI-free. Every credential check goes
through :class:`~.guard.AttemptGuard`; every store/lockout/audit call made from a coroutine goes
through ``seams.run_sync`` (the event loop never blocks on a file lock). ``snapshot()`` and
``revalidate()`` stay synchronous on purpose: they are a stat on a cache hit (HLD 11.15.7).

Revocation model (D10): a session carries the immutable ``user_id`` and the ``credential_epoch``
read from the **same snapshot** as the verified hash. Every web-initiated credential write re-checks
both inside the locked ``mutate`` (``store.with_identity``), so a password change or "log out
everywhere" that raced a login makes the login's session stale instead of silently valid.
"""

from __future__ import annotations

import logging
import os
import shlex
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar, TypeVar

from .audit import AuditEvent, AuditLog, AuditOutcome
from .constants import (
    LOCAL_PROVIDER_ID,
    MAX_USERNAME_CHARS,
    SOURCE_WEB,
)
from .errors import (
    AuthError,
    AuthNotReadyError,
    ErrorCode,
    StoreCorruptError,
    StoreLockTimeoutError,
    StoreUnavailableError,
)
from .guard import AttemptGuard, AttemptResult, AttemptSubject, lockout_call, ok_if, subject_for
from .lockouts import USERNAME_RE, LockoutKey, LockoutStore
from .model import AuditEventName, SessionState, TotpPolicy, TotpRequirement
from .passwords import PasswordHasher, PasswordPolicy
from .paths import check_private_paths, check_state_dir, xdg_default_store_dir
from .policy import totp_requirement
from .provider import AuthProvider, ClientInfo, Revalidation, UserView, VerifiedIdentity
from .seams import Clock, run_sync
from .sessions import SessionRecord
from .settings import AuthSettings
from .store import (
    StaleIdentityError,
    UserRecord,
    UserStore,
    UserStoreFile,
    bump_epoch,
    cas_mark_login,
    cas_rehash_password,
    set_password_hash,
    with_identity,
)

_log = logging.getLogger(__name__)
_T = TypeVar("_T")

_BOOTSTRAP_USERNAME_PLACEHOLDER = "<username>"


def normalize_username(raw: str) -> str:
    """NFKC -> strip -> lower: the one canonical form of a login name (HLD 11.15.4)."""
    return unicodedata.normalize("NFKC", raw).strip().lower()


class LocalPasswordProvider(AuthProvider):
    provider_id: ClassVar[str] = LOCAL_PROVIDER_ID

    def __init__(
        self,
        store: UserStore,
        lockouts: LockoutStore,
        guard: AttemptGuard,
        hasher: PasswordHasher,
        settings: AuthSettings,
        audit: AuditLog,
        clock: Clock,
        *,
        realm: str | None = None,
    ) -> None:
        self._store = store
        self._lockouts = lockouts
        self._guard = guard
        self._hasher = hasher
        self._settings = settings
        self._audit = audit
        self._clock = clock
        self._realm = realm  # audit label only; the guard carries its own for failure events
        self._path_warnings: list[str] = []
        self._unreadable_logged = False

    @property
    def guard(self) -> AttemptGuard:
        """The shared attempt guard (the second-factor service must use the same one)."""
        return self._guard

    # -- readiness -----------------------------------------------------------------------------

    def check_ready(self) -> None:
        """Raise ``AuthNotReadyError`` / ``UnsafePermissionsError`` unless logins can be served.

        Order matches HLD 11.15.3. Non-fatal parent-directory notices (security L6) are kept for
        :meth:`startup_warnings`. Ends by creating the lockout name key (security L1), so the
        first unknown-user attempt still makes exactly one lockout write (S6).
        """
        paths = self._store.paths
        if not paths.store_dir.exists() or not os.path.lexists(paths.users_file):
            raise AuthNotReadyError(self._bootstrap_message())
        notices = check_private_paths(paths.store_dir, paths.users_file)
        notices += check_state_dir(paths.state_dir)  # created 0700 when missing
        self._path_warnings = list(dict.fromkeys(notices))
        try:
            snap = self._store.snapshot()
        except StoreCorruptError as exc:
            raise AuthNotReadyError(f"user store {paths.users_file} is unusable: {exc}") from exc
        except StoreUnavailableError as exc:
            raise AuthNotReadyError(
                f"user store {paths.users_file} is unreadable: {exc.cause_for_log or exc}"
            ) from exc
        if not snap.users:
            raise AuthNotReadyError(self._bootstrap_message())
        try:
            self._lockouts.check_readable()
        except StoreCorruptError as exc:
            raise AuthNotReadyError(
                f"lockout state {paths.lockouts_file} is unusable: {exc}; "
                "run `ao auth unlock <user>` to rewrite it"
            ) from exc
        self._lockouts.ensure_name_key()

    def _bootstrap_message(self) -> str:
        paths = self._store.paths
        source = self._settings.sources.get("enabled", "default")
        auth_dir = ""
        if _differs(paths.store_dir, xdg_default_store_dir()):
            auth_dir = f" --auth-dir {shlex.quote(str(paths.store_dir))}"
        return (
            f"Dashboard authentication is enabled (ui.auth.enabled from {source}) but the user "
            f"store\n{paths.users_file} has no accounts. Create the first account on this "
            "machine, as this OS user:\n\n"
            f"    ao auth add-user {_BOOTSTRAP_USERNAME_PLACEHOLDER}{auth_dir}\n\n"
            "then start again. To run without authentication instead, pass --no-auth "
            "(or set AO_UI_AUTH=0)."
        )

    def startup_warnings(self) -> list[str]:
        """Operator warnings (HLD 11.15.3): policy/enrollment mismatches and parent-dir notices."""
        warnings: list[str] = []
        try:
            users = list(self._store.snapshot().users.values())
        except (StoreCorruptError, StoreUnavailableError):
            users = []  # check_ready already refuses an unusable store; nothing to count here
        policy = self._settings.totp
        enrolled = sum(1 for rec in users if rec.totp is not None)
        if policy is TotpPolicy.OFF and enrolled:
            warnings.append(
                "totp=off only disables new enrollment; "
                f"{enrolled} enrolled user(s) will still be asked for a code"
            )
        # An enrolled user is challenged under every policy (S9), so only a user who is NOT
        # enrolled and whose requirement cannot be met is locked out.
        blocked = sum(
            1
            for rec in users
            if rec.totp is None
            and totp_requirement(policy, rec.totp_required) is TotpRequirement.BLOCKED
        )
        if blocked:
            warnings.append(
                f"totp=off: {blocked} user(s) require two-factor authentication but cannot "
                "enroll while the policy is off; they cannot log in "
                "(run ao auth disable-2fa <user> or raise the policy)"
            )
        warnings.extend(self._path_warnings)
        return warnings

    # -- login ---------------------------------------------------------------------------------

    async def authenticate(
        self, username_raw: str, password: str, client: ClientInfo
    ) -> VerifiedIdentity:
        """HLD 11.15.4. One snapshot, one scrypt (a dummy for an unknown or malformed name), one
        lockout write per failure; ``next_state`` decided by the sticky-enrollment/policy rules."""
        uname = normalize_username(username_raw)
        snap = self._snapshot()  # ONE snapshot for the whole attempt (D10)
        rec = snap.users.get(uname) if USERNAME_RE.fullmatch(uname) else None
        if rec is not None:
            subject = AttemptSubject(uname, rec.user_id, LockoutKey(user_id=rec.user_id))
        else:
            # v2.1 (L1): a keyed digest; every malformed name shares one bucket. The gate key is
            # capped so a huge junk name cannot pin memory in the gate table.
            digest = await lockout_call(self._lockouts.name_digest, uname)
            subject = AttemptSubject(uname[:MAX_USERNAME_CHARS], None, LockoutKey(phantom=digest))
        verified_hash = rec.password_hash if rec is not None else self._hasher.dummy_hash

        async def verify_password() -> AttemptResult:
            # NO short-circuit: the scrypt verify ALWAYS runs, so timing does not reveal the name.
            matched = await self._hasher.verify(password, verified_hash)
            return ok_if(matched and rec is not None)

        result = await self._guard.attempt(
            subject,
            client,
            verify=verify_password,
            failure_event=AuditEventName.LOGIN_FAILURE,
            reset_on_success=False,
        )
        if not result.ok or rec is None:
            raise AuthError(ErrorCode.INVALID_CREDENTIALS)
        if self._hasher.needs_rehash(verified_hash):
            await self._rehash(uname, rec, verified_hash, password)
        next_state = self._next_state(rec)
        if next_state is SessionState.FULL:
            await lockout_call(self._lockouts.reset, rec.user_id)
            now = self._clock.now_utc()
            await self._mutate(
                lambda f: cas_mark_login(
                    f, uname, user_id=rec.user_id, epoch=rec.credential_epoch, now=now
                )
            )
        else:
            # The lockout is NOT reset until the second factor succeeds.
            await run_sync(
                self._audit.record,
                AuditEvent(
                    AuditEventName.LOGIN_SECOND_FACTOR_PENDING,
                    AuditOutcome.INFO,
                    username=uname,
                    user_id=rec.user_id,
                    realm=self._realm,
                    client_addr=client.key,
                    details={"second_factor": next_state.api_state},
                ),
            )
        return VerifiedIdentity(
            rec.user_id, uname, tuple(rec.roles), rec.credential_epoch, snap.store_id, next_state
        )

    def _next_state(self, rec: UserRecord) -> SessionState:
        """Sticky enrollment first (S9: every policy), then what the policy demands of the user."""
        if rec.totp is not None:
            return SessionState.PARTIAL_SECOND_FACTOR
        requirement = totp_requirement(self._settings.totp, rec.totp_required)
        if requirement is TotpRequirement.ENROLL_ALLOWED:
            return SessionState.PARTIAL_ENROLL
        if requirement is TotpRequirement.BLOCKED:  # policy off cannot downgrade a required user
            raise AuthError(ErrorCode.TOTP_REQUIRED)
        return SessionState.FULL

    async def _rehash(self, uname: str, rec: UserRecord, old_hash: str, password: str) -> None:
        """Opportunistic upgrade to current scrypt parameters (CAS: identity + old hash)."""
        new_hash = await self._hasher.hash(password)
        now = self._clock.now_utc()
        # Not applied -> a concurrent credential change won; harmless (the session goes stale).
        await self._mutate(
            lambda f: cas_rehash_password(
                f,
                uname,
                user_id=rec.user_id,
                epoch=rec.credential_epoch,
                old_hash=old_hash,
                new_hash=new_hash,
                now=now,
            )
        )

    # -- re-authentication and credential changes ----------------------------------------------

    async def verify_current_password(
        self, session: SessionRecord, password: str, client: ClientInfo
    ) -> None:
        """Re-authenticate a live session (FR-18): guarded, one scrypt, ``REAUTH_FAILURE`` audit."""
        self._snapshot()
        rec = self._current_record(session)
        stored_hash = rec.password_hash

        async def verify_password() -> AttemptResult:
            return ok_if(await self._hasher.verify(password, stored_hash))

        result = await self._guard.attempt(
            subject_for(session),
            client,
            verify=verify_password,
            failure_event=AuditEventName.REAUTH_FAILURE,
            reset_on_success=False,
        )
        if not result.ok:
            raise AuthError(ErrorCode.INVALID_CREDENTIALS)

    async def change_password(
        self, session: SessionRecord, current: str, new: str, client: ClientInfo
    ) -> int:
        """Return the new ``credential_epoch``. The caller rotates the session (E8)."""
        await self.verify_current_password(session, current, client)
        PasswordPolicy(self._settings.min_password_length).check(new, username=session.username)
        new_hash = await self._hasher.hash(new)
        now = self._clock.now_utc()
        epoch = await self._identity_write(
            session, lambda f: set_password_hash(f, session.username, new_hash, now=now)
        )
        await run_sync(
            self._audit.record,
            AuditEvent(
                AuditEventName.PASSWORD_CHANGED,
                AuditOutcome.SUCCESS,
                username=session.username,
                user_id=session.user_id,
                realm=self._realm,
                client_addr=client.key,
                details={"source": SOURCE_WEB},
            ),
        )
        return epoch

    async def logout_everywhere(self, session: SessionRecord) -> int:
        """Bump the epoch under the identity guard: every other session of the user goes stale.
        The route audits ``auth.logout_all``."""
        now = self._clock.now_utc()
        return await self._identity_write(
            session, lambda f: bump_epoch(f, session.username, now=now)
        )

    async def _identity_write(
        self, session: SessionRecord, mutation: Callable[[UserStoreFile], _T]
    ) -> _T:
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

    def _current_record(self, session: SessionRecord) -> UserRecord:
        rec = self._store.user_by_id(session.user_id)
        if rec is None or rec.credential_epoch != session.credential_epoch:
            raise AuthError(ErrorCode.NOT_AUTHENTICATED)
        return rec

    # -- AuthProvider ---------------------------------------------------------------------------

    def revalidate(self, user_id: str, credential_epoch: int) -> Revalidation:
        """Tri-state, synchronous, cheap on a cache hit (a cache miss re-parses once per change)."""
        try:
            rec = self._store.user_by_id(user_id)
        except (StoreCorruptError, StoreUnavailableError, StoreLockTimeoutError, OSError) as exc:
            if not self._unreadable_logged:  # once per outage, not once per request
                self._unreadable_logged = True
                _log.error("user store unreadable; answering 503: %s", exc)
            return Revalidation.UNAVAILABLE
        self._unreadable_logged = False
        if rec is not None and rec.credential_epoch == credential_epoch:
            return Revalidation.VALID
        return Revalidation.REVOKED

    def user_view(self, username: str) -> UserView | None:
        rec = self._snapshot().users.get(username)
        if rec is None:
            return None
        remaining = (
            sum(1 for code in rec.recovery_codes if code.used_at is None)
            if rec.totp is not None
            else None
        )
        return UserView(
            rec.user_id,
            rec.username,
            tuple(rec.roles),
            rec.totp is not None,
            remaining,
            rec.totp_required,
        )

    # -- plumbing -------------------------------------------------------------------------------

    def _snapshot(self) -> UserStoreFile:
        """The store snapshot; an unreadable or corrupt store is a 503 (``snapshot_or_503``)."""
        try:
            return self._store.snapshot()
        except StoreCorruptError as exc:
            raise StoreUnavailableError(cause_for_log=f"user store: {exc}") from exc

    async def _mutate(self, fn: Callable[[UserStoreFile], _T]) -> _T:
        try:
            return await run_sync(self._store.mutate, fn)
        except (StoreLockTimeoutError, StoreCorruptError) as exc:
            raise StoreUnavailableError(cause_for_log=f"user store: {exc}") from exc


def _differs(a: Path, b: Path) -> bool:
    try:
        return a.resolve() != b.resolve()
    except (OSError, RuntimeError):
        return a != b
