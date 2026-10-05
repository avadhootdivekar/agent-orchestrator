"""``users.json``: the only module that reads or writes the credential store (HLD 11.9, L2).

* Reads are cheap and cached by a ``(inode, mtime_ns, size)`` stat key.
* Writes go through :meth:`UserStore.mutate`: sidecar ``flock`` -> stale-temp cleanup -> a fresh
  parse -> a pure mutation function -> validation -> atomic replace -> cache refresh. A mutation
  that changed nothing (a failed CAS, a rejected code, a stale identity) writes nothing.
* Identity is ``user_id`` (immutable, random) plus ``credential_epoch``. Every web-initiated
  write re-checks both inside the lock (:func:`with_identity`, or the ``user_id``/``epoch``
  arguments of the ``cas_*``, ``consume_*`` and ``enroll_totp`` mutations).
* Lockout state is **not** here (``lockouts.py``).

Forward compatibility (D5): unknown fields written by a newer ``ao`` are preserved verbatim
(``extra="allow"``); a reader refuses only ``schema_version > 1`` or ``required_features`` it does
not know. Validation errors carry field paths only, never values (``hide_input_in_errors``).
"""

from __future__ import annotations

import contextlib
import hmac
import json
import os
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_orchestrator import fsutil
from agent_orchestrator.errors import OrchestratorError

from .constants import (
    ENROLLMENT_TOKEN_TTL_SECONDS,
    KNOWN_STORE_FEATURES,
    MAX_USERS,
    RECOVERY_SALT_BYTES,
    STORE_LOCK_TIMEOUT_SECONDS,
    STORE_SCHEMA_VERSION,
    USER_ID_BYTES,
    USERS_FILENAME,
)
from .errors import StoreCorruptError, StoreLockTimeoutError, StoreUnavailableError
from .paths import StorePaths
from .recovery import (
    find_unused_match,
    generate_recovery_codes,
    hash_recovery_code,
    normalize_recovery_code,
)
from .seams import SYSTEM_CLOCK, SYSTEM_ENTROPY, Clock, Entropy
from .totp import b32decode_secret, match_totp_step

_T = TypeVar("_T")

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
STORE_ID_BYTES = 16
_UPGRADE_HINT = "upgrade ao"

# ---------------------------------------------------------------------------
# Domain errors (the CLI maps them to exit 1; the services map them to HTTP codes)
# ---------------------------------------------------------------------------


class StoreMissingError(OrchestratorError):
    """The store (or its directory) does not exist and the caller did not ask to create it."""


class UserExistsError(OrchestratorError):
    """``add_user`` for a username that is already present."""


class UserNotFoundError(OrchestratorError):
    """The named user is not in the store."""


class TooManyUsersError(OrchestratorError):
    """The store already holds ``MAX_USERS`` users."""


class AlreadyEnrolledError(OrchestratorError):
    """The user already has TOTP enrolled (the HLD's ``TotpAlreadyEnrolledError``)."""


class NotEnrolledError(OrchestratorError):
    """The operation needs an enrolled TOTP and the user has none."""


class StaleIdentityError(OrchestratorError):
    """The session's ``user_id`` / ``credential_epoch`` no longer match the stored record."""


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

# extra="allow": unknown fields written by a NEWER ao are preserved verbatim (D5).
# hide_input_in_errors: a ValidationError never echoes a hash, seed or code (dev-security #11).
_STORE_MODEL_CONFIG = ConfigDict(extra="allow", hide_input_in_errors=True)


class TotpEnrollment(BaseModel):
    model_config = _STORE_MODEL_CONFIG
    secret_b32: str = Field(repr=False)
    algorithm: Literal["SHA1"] = "SHA1"
    digits: Literal[6] = 6
    period: Literal[30] = 30
    enrolled_at: str
    last_used_step: int


class RecoveryCodeHash(BaseModel):
    model_config = _STORE_MODEL_CONFIG
    salt_hex: str = Field(repr=False)
    hash_hex: str = Field(repr=False)
    used_at: str | None = None


class EnrollmentToken(BaseModel):
    """CLI-issued, single-use (D7): a salted hash, never the plaintext."""

    model_config = _STORE_MODEL_CONFIG
    salt_hex: str = Field(repr=False)
    hash_hex: str = Field(repr=False)
    expires_at: str


class UserRecord(BaseModel):
    model_config = _STORE_MODEL_CONFIG
    user_id: str  # 32 hex, random at add_user, NEVER changes (D10)
    username: str
    password_hash: str = Field(repr=False)
    created_at: str
    updated_at: str
    password_changed_at: str
    credential_epoch: int = 1
    roles: list[str] = []
    totp: TotpEnrollment | None = None
    recovery_codes: list[RecoveryCodeHash] = []
    totp_required: bool = False  # per-user "must have TOTP" (--require-totp, reset-2fa)
    enrollment_token: EnrollmentToken | None = None
    last_login_at: str | None = None


class UserStoreFile(BaseModel):
    model_config = _STORE_MODEL_CONFIG
    schema_version: int = STORE_SCHEMA_VERSION
    store_id: str
    created_at: str
    required_features: list[str] = []  # refused when it names a feature not in KNOWN_STORE_FEATURES
    users: dict[str, UserRecord] = {}


class OutcomeKind(StrEnum):
    """Result kind of ``consume_totp`` (all four) and ``consume_recovery`` (no ``REPLAYED``)."""

    OK = "ok"
    INVALID = "invalid"
    REPLAYED = "replayed"
    STALE = "stale"


@dataclass(frozen=True)
class TotpOutcome:
    kind: OutcomeKind
    step: int | None = None  # the accepted step when OK


@dataclass(frozen=True)
class RecoveryOutcome:
    kind: OutcomeKind
    remaining: int | None = None  # unused codes left when OK


# ---------------------------------------------------------------------------
# Time and serialization helpers
# ---------------------------------------------------------------------------


def format_timestamp(moment: datetime) -> str:
    """``YYYY-MM-DDTHH:MM:SSZ`` in UTC. Naive datetimes are a caller bug and are refused."""
    if moment.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return moment.astimezone(UTC).strftime(TIMESTAMP_FORMAT)


def parse_timestamp(text: str) -> datetime | None:
    """Inverse of :func:`format_timestamp`; ``None`` for anything malformed."""
    try:
        return datetime.strptime(text, TIMESTAMP_FORMAT).replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def canonical_json(obj: object) -> bytes:
    """Stable on-disk form: sorted keys, 2-space indent, ASCII-only, trailing newline."""
    return (json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode("ascii")


def _empty_store() -> UserStoreFile:
    return UserStoreFile(store_id="", created_at="")


# ---------------------------------------------------------------------------
# Loading: ONE parse + validation routine shared by snapshot(), mutate() and the probe
# ---------------------------------------------------------------------------


def _field_paths(exc: ValidationError) -> str:
    """Field paths and error types only: never the input value (it may be a hash or a seed)."""
    parts = [".".join(str(p) for p in err["loc"]) + f" ({err['type']})" for err in exc.errors()]
    return "; ".join(parts[:10])


def _check_compatibility(path: Path, data: dict[str, object]) -> None:
    version = data.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise StoreCorruptError(f"{path}: schema_version is missing or invalid")
    if version > STORE_SCHEMA_VERSION:
        raise StoreCorruptError(
            f"{path}: schema_version is newer than this ao supports "
            f"(<= {STORE_SCHEMA_VERSION}); {_UPGRADE_HINT}"
        )
    features = data.get("required_features", [])
    if not isinstance(features, list) or not all(isinstance(f, str) for f in features):
        raise StoreCorruptError(f"{path}: required_features must be a list of strings")
    if set(features) - KNOWN_STORE_FEATURES:
        raise StoreCorruptError(
            f"{path}: requires features this ao does not support; {_UPGRADE_HINT}"
        )


def load_store_file(path: Path) -> UserStoreFile:
    """Read, parse and validate ``users.json``.

    Raises ``StoreUnavailableError`` (cannot read), ``StoreCorruptError`` (not JSON, wrong shape,
    unsupported schema/feature) or ``FileNotFoundError`` (callers handle the missing case).
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise StoreUnavailableError(cause_for_log=f"cannot read {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as exc:  # includes UnicodeDecodeError
        raise StoreCorruptError(f"{path}: not valid JSON") from exc
    if not isinstance(data, dict):
        raise StoreCorruptError(f"{path}: top level must be a JSON object")
    _check_compatibility(path, data)
    try:
        return UserStoreFile.model_validate(data)
    except ValidationError as exc:
        raise StoreCorruptError(f"{path}: invalid content: {_field_paths(exc)}") from exc


def count_store_users(store_dir: Path) -> int | None:
    """Tri-state probe: 0 when the directory or file is missing; ``None`` (UNKNOWN, fail closed)
    when the file is unreadable, corrupt or from an unsupported schema; else the user count."""
    users_file = store_dir / USERS_FILENAME
    try:
        os.stat(users_file)
    except (FileNotFoundError, NotADirectoryError):
        return 0
    except OSError:
        return None
    try:
        return len(load_store_file(users_file).users)
    except FileNotFoundError:
        return 0
    except (StoreCorruptError, StoreUnavailableError):
        return None


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

_StatKey = tuple[int, int, int]


class UserStore:
    def __init__(
        self,
        paths: StorePaths,
        *,
        clock: Clock = SYSTEM_CLOCK,
        entropy: Entropy = SYSTEM_ENTROPY,
        lock_timeout: float = STORE_LOCK_TIMEOUT_SECONDS,
    ) -> None:
        self._paths = paths
        self._clock = clock
        self._entropy = entropy
        self._lock_timeout = lock_timeout
        self._cache_guard = threading.Lock()
        self._cache_key: _StatKey | None = None
        self._cache: UserStoreFile | None = None
        self._by_user_id: dict[str, UserRecord] = {}

    @property
    def paths(self) -> StorePaths:
        return self._paths

    def exists(self) -> bool:
        try:
            os.stat(self._paths.users_file)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise StoreUnavailableError(cause_for_log=f"cannot stat users file: {exc}") from exc
        return True

    def snapshot(self) -> UserStoreFile:
        """The current store content. The result is SHARED: treat it as read-only.

        An empty store (``store_id == ""``) when the file is missing. Unchanged files are not
        re-read. Raises ``StoreCorruptError`` / ``StoreUnavailableError``.
        """
        try:
            st = os.stat(self._paths.users_file)
        except FileNotFoundError:
            return self._cache_empty()
        except OSError as exc:
            raise StoreUnavailableError(cause_for_log=f"cannot stat users file: {exc}") from exc
        key = (st.st_ino, st.st_mtime_ns, st.st_size)
        with self._cache_guard:
            if key == self._cache_key and self._cache is not None:
                return self._cache
        try:
            model = load_store_file(self._paths.users_file)
        except FileNotFoundError:  # removed between stat and read
            return self._cache_empty()
        self._set_cache(key, model)
        return model

    def user_by_id(self, user_id: str) -> UserRecord | None:
        self.snapshot()  # refreshes the index when the file changed
        with self._cache_guard:
            return self._by_user_id.get(user_id)

    def count_users(self) -> int | None:
        """User count, or ``None`` when the store exists but is unreadable/corrupt (UNKNOWN)."""
        try:
            return len(self.snapshot().users)
        except (StoreCorruptError, StoreUnavailableError):
            return None

    def mutate(self, fn: Callable[[UserStoreFile], _T], *, create: bool = False) -> _T:
        """Apply ``fn`` to a fresh copy of the store under the lock and persist the result.

        ``fn`` mutates its argument in place or raises (nothing is written then). When ``fn``
        left the content unchanged an existing file is not rewritten. ``create=True`` initializes
        a missing store (the directory must already exist: creating and fixing it is the CLI's
        job). Raises ``StoreMissingError``, ``StoreLockTimeoutError``, ``StoreCorruptError`` and
        ``StoreUnavailableError`` besides whatever ``fn`` raises.
        """
        users_file = self._paths.users_file
        try:
            with contextlib.ExitStack() as stack:
                try:
                    stack.enter_context(
                        fsutil.FileLock(self._paths.users_lock, timeout=self._lock_timeout)
                    )
                except fsutil.LockTimeoutError as exc:
                    raise StoreLockTimeoutError(str(exc)) from exc
                except FileNotFoundError as exc:
                    raise StoreMissingError(
                        f"store directory {self._paths.store_dir} does not exist"
                    ) from exc
                fsutil.remove_stale_temp_files(users_file)
                try:
                    current = load_store_file(users_file)
                    existed = True
                except FileNotFoundError:
                    if not create:
                        raise StoreMissingError(f"{users_file} does not exist") from None
                    current = UserStoreFile(
                        store_id=self._entropy.token_bytes(STORE_ID_BYTES).hex(),
                        created_at=format_timestamp(self._clock.now_utc()),
                    )
                    existed = False
                before = current.model_dump(mode="json")
                result = fn(current)
                after = current.model_dump(mode="json")
                if existed and after == before:
                    return result
                try:
                    validated = UserStoreFile.model_validate(after)
                except ValidationError as exc:
                    raise StoreCorruptError(
                        f"refusing to write invalid users.json: {_field_paths(exc)}"
                    ) from exc
                fsutil.atomic_write_bytes(
                    users_file, canonical_json(validated.model_dump(mode="json"))
                )
                self._refresh_after_write(validated)
                return result
        except OSError as exc:
            raise StoreUnavailableError(cause_for_log=f"{users_file}: {exc}") from exc

    # -- cache -------------------------------------------------------------------------------

    def _set_cache(self, key: _StatKey | None, model: UserStoreFile) -> None:
        index = {rec.user_id: rec for rec in model.users.values()}
        with self._cache_guard:
            self._cache_key, self._cache, self._by_user_id = key, model, index

    def _cache_empty(self) -> UserStoreFile:
        empty = _empty_store()
        self._set_cache(None, empty)
        return empty

    def _refresh_after_write(self, validated: UserStoreFile) -> None:
        try:
            st = os.stat(self._paths.users_file)
        except OSError:
            self._set_cache(None, validated)
            return
        self._set_cache((st.st_ino, st.st_mtime_ns, st.st_size), validated)


# ---------------------------------------------------------------------------
# Pure mutation functions: (f, ...) -> result. They mutate `f` in place or raise.
# ---------------------------------------------------------------------------

RecoveryInput = Sequence[tuple[str, str] | RecoveryCodeHash]


def _record(f: UserStoreFile, username: str) -> UserRecord:
    rec = f.users.get(username)
    if rec is None:
        raise UserNotFoundError(f"no such user: {username}")
    return rec


def _identity_matches(rec: UserRecord | None, user_id: str, epoch: int) -> bool:
    return rec is not None and rec.user_id == user_id and rec.credential_epoch == epoch


def _to_recovery_hashes(records: RecoveryInput) -> list[RecoveryCodeHash]:
    out: list[RecoveryCodeHash] = []
    for item in records:
        if isinstance(item, RecoveryCodeHash):
            out.append(item.model_copy(deep=True))
        else:
            salt_hex, hash_hex = item
            out.append(RecoveryCodeHash(salt_hex=salt_hex, hash_hex=hash_hex))
    return out


def _bump(rec: UserRecord, now: datetime) -> int:
    rec.credential_epoch += 1
    rec.updated_at = format_timestamp(now)
    return rec.credential_epoch


def add_user(
    f: UserStoreFile,
    username: str,
    password_hash: str,
    *,
    now: datetime,
    entropy: Entropy,
    totp_required: bool = False,
) -> UserRecord:
    if username in f.users:
        raise UserExistsError(f"user already exists: {username}")
    if len(f.users) >= MAX_USERS:
        raise TooManyUsersError(f"the store already holds the maximum of {MAX_USERS} users")
    stamp = format_timestamp(now)
    rec = UserRecord(
        user_id=entropy.token_bytes(USER_ID_BYTES).hex(),
        username=username,
        password_hash=password_hash,
        created_at=stamp,
        updated_at=stamp,
        password_changed_at=stamp,
        credential_epoch=1,
        totp_required=totp_required,
    )
    f.users[username] = rec
    return rec


def remove_user(f: UserStoreFile, username: str) -> str:
    """Delete the user and return the removed ``user_id`` (the caller clears its lockout)."""
    user_id = _record(f, username).user_id
    del f.users[username]
    return user_id


def set_password_hash(f: UserStoreFile, username: str, new_hash: str, *, now: datetime) -> int:
    rec = _record(f, username)
    rec.password_hash = new_hash
    rec.password_changed_at = format_timestamp(now)
    return _bump(rec, now)


def cas_rehash_password(
    f: UserStoreFile,
    username: str,
    *,
    user_id: str,
    epoch: int,
    old_hash: str,
    new_hash: str,
    now: datetime,
) -> bool:
    """Opportunistic rehash: applies only while identity AND the verified hash are unchanged.

    No epoch bump: the credential did not change, only its encoding.
    """
    rec = f.users.get(username)
    if rec is None or not _identity_matches(rec, user_id, epoch):
        return False
    if not hmac.compare_digest(rec.password_hash.encode(), old_hash.encode()):
        return False
    rec.password_hash = new_hash
    rec.updated_at = format_timestamp(now)
    return True


def cas_mark_login(
    f: UserStoreFile, username: str, *, user_id: str, epoch: int, now: datetime
) -> bool:
    rec = f.users.get(username)
    if rec is None or not _identity_matches(rec, user_id, epoch):
        return False
    rec.last_login_at = format_timestamp(now)
    return True


def consume_totp(
    f: UserStoreFile,
    username: str,
    code: str | None,
    *,
    user_id: str,
    epoch: int,
    now_unix: float,
) -> TotpOutcome:
    """Check a (normalized) TOTP code and, on success, record its step (replay protection)."""
    rec = f.users.get(username)
    if rec is None or not _identity_matches(rec, user_id, epoch):
        return TotpOutcome(OutcomeKind.STALE)
    if rec.totp is None:
        raise NotEnrolledError(f"{username} has no TOTP enrolled")
    if code is None:
        return TotpOutcome(OutcomeKind.INVALID)
    try:
        key = b32decode_secret(rec.totp.secret_b32)
    except ValueError as exc:
        raise StoreCorruptError("a stored TOTP secret is not valid base32") from exc
    step = match_totp_step(key, code, now_unix)
    if step is None:
        return TotpOutcome(OutcomeKind.INVALID)
    if step <= rec.totp.last_used_step:
        return TotpOutcome(OutcomeKind.REPLAYED)
    rec.totp.last_used_step = step
    return TotpOutcome(OutcomeKind.OK, step)


def consume_recovery(
    f: UserStoreFile,
    username: str,
    normalized: str | None,
    *,
    user_id: str,
    epoch: int,
    now: datetime,
) -> RecoveryOutcome:
    rec = f.users.get(username)
    if rec is None or not _identity_matches(rec, user_id, epoch):
        return RecoveryOutcome(OutcomeKind.STALE)
    if rec.totp is None:
        raise NotEnrolledError(f"{username} has no TOTP enrolled")
    if normalized is None:
        return RecoveryOutcome(OutcomeKind.INVALID)
    index = find_unused_match(normalized, rec.recovery_codes)
    if index is None:
        return RecoveryOutcome(OutcomeKind.INVALID)
    rec.recovery_codes[index].used_at = format_timestamp(now)
    remaining = sum(1 for code in rec.recovery_codes if code.used_at is None)
    return RecoveryOutcome(OutcomeKind.OK, remaining)


def issue_enrollment_token(
    f: UserStoreFile, username: str, *, entropy: Entropy, now: datetime
) -> str:
    """Issue a single-use enrollment token (recovery-code format); returns the plaintext ONCE.

    Only a salted hash is stored. Replaces any previous token.
    """
    rec = _record(f, username)
    if rec.totp is not None:
        raise AlreadyEnrolledError(f"{username} already has TOTP enrolled")
    token = generate_recovery_codes(entropy, count=1)[0]
    normalized = normalize_recovery_code(token)
    if normalized is None:  # unreachable: generate_recovery_codes emits valid codes
        raise StoreCorruptError("generated enrollment token failed normalization")
    salt = entropy.token_bytes(RECOVERY_SALT_BYTES)
    rec.enrollment_token = EnrollmentToken(
        salt_hex=salt.hex(),
        hash_hex=hash_recovery_code(normalized, salt),
        expires_at=format_timestamp(now + timedelta(seconds=ENROLLMENT_TOKEN_TTL_SECONDS)),
    )
    rec.updated_at = format_timestamp(now)
    return token


def consume_enrollment_token(
    f: UserStoreFile, username: str, normalized: str | None, *, user_id: str, now: datetime
) -> bool:
    """True (and the token is cleared) for a valid, unexpired, matching token; else False.

    An expired (or unparseable-expiry) token is cleared. A wrong guess leaves the token alone.
    """
    rec = f.users.get(username)
    if rec is None or rec.user_id != user_id or rec.enrollment_token is None or normalized is None:
        return False
    token = rec.enrollment_token
    expires = parse_timestamp(token.expires_at)
    if expires is None or expires <= now:
        rec.enrollment_token = None
        return False
    try:
        salt = bytes.fromhex(token.salt_hex)
    except ValueError:
        return False
    ok = hmac.compare_digest(
        hash_recovery_code(normalized, salt).encode("ascii"), token.hash_hex.encode("ascii")
    )
    if ok:
        rec.enrollment_token = None
    return ok


def enroll_totp(
    f: UserStoreFile,
    username: str,
    *,
    user_id: str,
    epoch: int,
    secret_b32: str,
    step: int,
    records: RecoveryInput,
    now: datetime,
    completes_login: bool,
) -> int:
    """Enroll TOTP (identity-checked, not already enrolled); returns the new epoch."""
    rec = f.users.get(username)
    if rec is None or not _identity_matches(rec, user_id, epoch):
        raise StaleIdentityError(f"identity of {username} changed")
    if rec.totp is not None:
        raise AlreadyEnrolledError(f"{username} already has TOTP enrolled")
    rec.totp = TotpEnrollment(
        secret_b32=secret_b32, enrolled_at=format_timestamp(now), last_used_step=step
    )
    rec.recovery_codes = _to_recovery_hashes(records)
    rec.enrollment_token = None
    if completes_login:
        rec.last_login_at = format_timestamp(now)
    return _bump(rec, now)


def remove_totp(
    f: UserStoreFile, username: str, *, now: datetime, set_required: bool | None
) -> int:
    """Remove TOTP and recovery codes; ``set_required`` True/False sets the per-user flag, None
    keeps it. Raises ``NotEnrolledError`` only for a non-enrolled user with ``set_required=None``
    (web self-disable), so ``disable-2fa`` can clear the flag and ``reset-2fa`` is idempotent.
    The epoch is bumped only when something changed. Returns the (new) epoch.
    """
    rec = _record(f, username)
    enrolled = rec.totp is not None
    if not enrolled and set_required is None:
        raise NotEnrolledError(f"{username} has no TOTP enrolled")
    changed = False
    if enrolled:
        rec.totp = None
        rec.recovery_codes = []
        changed = True
    if set_required is False and rec.enrollment_token is not None:
        rec.enrollment_token = None
        changed = True
    if set_required is not None and rec.totp_required != set_required:
        rec.totp_required = set_required
        changed = True
    return _bump(rec, now) if changed else rec.credential_epoch


def replace_recovery_codes(
    f: UserStoreFile, username: str, records: RecoveryInput, *, now: datetime
) -> int:
    rec = _record(f, username)
    if rec.totp is None:
        raise NotEnrolledError(f"{username} has no TOTP enrolled")
    rec.recovery_codes = _to_recovery_hashes(records)
    return _bump(rec, now)


def bump_epoch(f: UserStoreFile, username: str, *, now: datetime) -> int:
    return _bump(_record(f, username), now)


def with_identity(
    mutation: Callable[[UserStoreFile], _T], *, username: str, user_id: str, epoch: int
) -> Callable[[UserStoreFile], _T]:
    """Wrap a mutation so it runs only while ``user_id`` and ``credential_epoch`` still match
    (D10). The check happens inside the locked ``mutate``; a mismatch raises
    ``StaleIdentityError`` before ``mutation`` runs, so nothing is written."""

    def guarded(f: UserStoreFile) -> _T:
        if not _identity_matches(f.users.get(username), user_id, epoch):
            raise StaleIdentityError(f"identity of {username} changed")
        return mutation(f)

    return guarded
