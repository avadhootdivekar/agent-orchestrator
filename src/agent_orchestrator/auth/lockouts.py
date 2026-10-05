"""``lockouts.json``: account lockouts, phantom lockouts and keyed name digests (HLD 11.11, L2).

Mutable security state that lives in the **state** directory, never in ``users.json`` (D5):

* :class:`LockoutPolicy` is the pure maths (threshold, exponential back-off, 24 h reset).
* :class:`LockoutStore` persists it with the same stat-cached snapshot, sidecar ``flock`` and
  atomic replace as :class:`~agent_orchestrator.auth.store.UserStore`. Two realms (two stores on
  one state directory) therefore share counts.
* Unknown usernames get a **phantom** entry keyed by :meth:`LockoutStore.name_digest`, an
  HMAC-SHA256 under a per-store random key (``name_key_hex``; v2.1, security L1). Every name that
  fails ``USERNAME_PATTERN`` collapses into one bucket, so attacker-chosen strings cannot grow the
  table, and a digest is not brute-forceable offline without the key.
* A corrupt or unreadable file **fails closed**: ``StoreUnavailableError`` (503), never "no
  lockouts". ``reset(..., repair_corrupt=True)`` (``ao auth unlock``) is the explicit way out.

Losing ``lockouts.json`` rotates the name key (old phantoms and audit hashes stop correlating);
that is accepted (HLD 11.11).
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import math
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from agent_orchestrator import fsutil

from .constants import (
    INVALID_USERNAME_BUCKET,
    LOCKOUT_NAME_KEY_BYTES,
    LOCKOUT_RESET_AFTER_SECONDS,
    PHANTOM_LOCKOUT_MAX_ENTRIES,
    STORE_LOCK_TIMEOUT_SECONDS,
    STORE_SCHEMA_VERSION,
    USERNAME_PATTERN,
)
from .errors import StoreCorruptError, StoreLockTimeoutError, StoreUnavailableError
from .paths import StorePaths
from .seams import SYSTEM_CLOCK, SYSTEM_ENTROPY, Clock, Entropy

# ``_field_paths`` is the one routine that renders a pydantic error without echoing the input.
from .store import _field_paths, canonical_json, format_timestamp, parse_timestamp

_log = logging.getLogger(__name__)
_T = TypeVar("_T")

USERNAME_RE = re.compile(USERNAME_PATTERN)
_NAME_KEY_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
# 2 ** n is capped here so a huge failure count cannot build a huge integer before min().
_MAX_BACKOFF_EXPONENT = 62

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

# extra="allow": unknown fields written by a NEWER ao survive a rewrite (D5).
# hide_input_in_errors: a ValidationError never echoes the name key.
_MODEL_CONFIG = ConfigDict(extra="allow", hide_input_in_errors=True)


class LockoutState(BaseModel):
    model_config = _MODEL_CONFIG
    failures: int = Field(default=0, ge=0)
    last_failure_at: str | None = None
    locked_until: str | None = None


class LockoutFile(BaseModel):
    model_config = _MODEL_CONFIG
    schema_version: int = STORE_SCHEMA_VERSION
    name_key_hex: str | None = Field(default=None, repr=False)  # 32 random bytes, hex; per store
    accounts: dict[str, LockoutState] = {}  # key = user_id
    phantoms: dict[str, LockoutState] = {}  # key = name_digest(unknown username), 64 hex

    @field_validator("name_key_hex")
    @classmethod
    def _key_is_hex(cls, value: str | None) -> str | None:
        if value is not None and not _NAME_KEY_HEX_RE.fullmatch(value):
            raise ValueError("name_key_hex must be 64 lowercase hex characters")
        return value


@dataclass(frozen=True)
class LockoutKey:
    """Exactly one of the two: a known account's ``user_id`` or an unknown name's digest."""

    user_id: str | None = None
    phantom: str | None = None

    def __post_init__(self) -> None:
        if (self.user_id is None) == (self.phantom is None):
            raise ValueError("LockoutKey needs exactly one of user_id and phantom")


# ---------------------------------------------------------------------------
# Policy (pure)
# ---------------------------------------------------------------------------


def _round_up_to_second(moment: datetime) -> datetime:
    """Persisted timestamps have 1 s resolution; round up so a lock is never shortened."""
    if moment.microsecond:
        return moment.replace(microsecond=0) + timedelta(seconds=1)
    return moment


@dataclass(frozen=True)
class LockoutPolicy:
    """Exponential back-off: failure ``threshold`` locks for ``base`` s, doubling up to ``max``."""

    threshold: int
    base_seconds: int
    max_seconds: int
    reset_after_seconds: int = LOCKOUT_RESET_AFTER_SECONDS

    def effective_failures(self, state: LockoutState, now: datetime) -> int:
        """The failure count, or 0 when the last failure is older than ``reset_after_seconds``.

        An unparseable ``last_failure_at`` keeps the count (fail closed).
        """
        if state.failures <= 0:
            return 0
        last = parse_timestamp(state.last_failure_at) if state.last_failure_at else None
        if last is not None and (now - last).total_seconds() > self.reset_after_seconds:
            return 0
        return state.failures

    def retry_after(self, state: LockoutState, now: datetime) -> int | None:
        """Whole seconds left on an active lock (rounded up), else ``None``."""
        until = parse_timestamp(state.locked_until) if state.locked_until else None
        if until is None:
            return None
        remaining = (until - now).total_seconds()
        return math.ceil(remaining) if remaining > 0 else None

    def register_failure(self, state: LockoutState, now: datetime) -> LockoutState:
        """The state after one more failed attempt (a copy; unknown fields are kept)."""
        failures = self.effective_failures(state, now) + 1
        new = state.model_copy(deep=True)
        new.failures = failures
        new.last_failure_at = format_timestamp(now)
        if failures >= self.threshold:
            exponent = min(failures - self.threshold, _MAX_BACKOFF_EXPONENT)
            delay = min(self.max_seconds, self.base_seconds * 2**exponent)
            new.locked_until = format_timestamp(_round_up_to_second(now + timedelta(seconds=delay)))
        else:
            new.locked_until = None
        return new

    def register_success(self) -> LockoutState:
        return LockoutState()


# ---------------------------------------------------------------------------
# Loading: one parse + validation routine shared by snapshot() and the locked mutation
# ---------------------------------------------------------------------------


def load_lockout_file(path: Path) -> LockoutFile:
    """Read, parse and validate ``lockouts.json``.

    Raises ``StoreUnavailableError`` (cannot read), ``StoreCorruptError`` (not JSON, wrong shape,
    unsupported schema) or ``FileNotFoundError`` (callers treat a missing file as empty).
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
    version = data.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise StoreCorruptError(f"{path}: schema_version is missing or invalid")
    if version > STORE_SCHEMA_VERSION:
        raise StoreCorruptError(
            f"{path}: schema_version is newer than this ao supports; upgrade ao"
        )
    try:
        return LockoutFile.model_validate(data)
    except ValidationError as exc:
        raise StoreCorruptError(f"{path}: invalid content: {_field_paths(exc)}") from exc


def _oldest_phantom(phantoms: dict[str, LockoutState], keep: str) -> str | None:
    """The phantom (other than ``keep``) with the oldest ``last_failure_at``; none sorts first.

    Timestamps share one fixed-width format, so string order is time order.
    """
    candidates = [(s.last_failure_at or "", key) for key, s in phantoms.items() if key != keep]
    return min(candidates)[1] if candidates else None


def enter_state_lock(
    stack: contextlib.ExitStack, lock_path: Path, state_dir: Path, timeout: float
) -> None:
    """Take a sidecar ``flock`` in the state directory, creating that directory (0700) first
    when it is missing. Shared with ``audit.py``. Raises ``fsutil.LockTimeoutError`` / ``OSError``.
    """
    try:
        stack.enter_context(fsutil.FileLock(lock_path, timeout=timeout))
    except FileNotFoundError:
        fsutil.ensure_private_dir(state_dir, create=True, fix=False)
        stack.enter_context(fsutil.FileLock(lock_path, timeout=timeout))


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

_StatKey = tuple[int, int, int]


class LockoutStore:
    """Cross-process lockout counters plus the keyed name digests (``lockouts.json``).

    Raises ``StoreUnavailableError`` for an unreadable or corrupt file (fail closed) and
    ``StoreLockTimeoutError`` when the sidecar lock is held too long.
    """

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
        self._cache: LockoutFile | None = None

    # -- reads -------------------------------------------------------------------------------

    def state(self, key: LockoutKey) -> LockoutState:
        """A copy of the entry for ``key``; ``LockoutState()`` when there is none."""
        snapshot = self._read(fail_closed=True)
        table = snapshot.accounts if key.user_id is not None else snapshot.phantoms
        entry = table.get(key.user_id if key.user_id is not None else str(key.phantom))
        return entry.model_copy(deep=True) if entry is not None else LockoutState()

    def check_readable(self) -> None:
        """Parse ``lockouts.json`` if present. Raises ``StoreCorruptError`` when it is corrupt
        and ``StoreUnavailableError`` when it cannot be read (``check_ready`` calls this)."""
        self._read(fail_closed=False)

    def name_digest(self, normalized_username: str) -> str:
        """``HMAC-SHA256(name_key, subject).hexdigest()`` (64 hex).

        ``subject`` is the name when it matches ``USERNAME_PATTERN``, else
        ``INVALID_USERNAME_BUCKET``: every malformed name shares one digest. A missing key (the
        file was deleted at runtime) is created here once and logged once.
        """
        key_hex = self._read(fail_closed=True).name_key_hex
        if key_hex is None:
            self.ensure_name_key()
            _log.warning("lockout name key was missing; created a new one (old digests rotate)")
            key_hex = self._read(fail_closed=True).name_key_hex
            if key_hex is None:  # pragma: no cover - ensure_name_key just wrote it
                raise StoreUnavailableError(cause_for_log="lockout name key could not be created")
        subject = (
            normalized_username
            if USERNAME_RE.fullmatch(normalized_username)
            else INVALID_USERNAME_BUCKET
        )
        return hmac.new(bytes.fromhex(key_hex), subject.encode("utf-8"), hashlib.sha256).hexdigest()

    # -- writes ------------------------------------------------------------------------------

    def ensure_name_key(self) -> None:
        """Create ``name_key_hex`` if absent (one write); no write when it already exists."""
        if self._read(fail_closed=True).name_key_hex is not None:
            return

        def create(f: LockoutFile) -> None:
            if f.name_key_hex is None:
                f.name_key_hex = self._new_name_key()

        self._mutate(create)

    def record_failure(self, key: LockoutKey, policy: LockoutPolicy) -> LockoutState:
        """One locked read-modify-write; returns the new state. Phantoms are capped at
        ``PHANTOM_LOCKOUT_MAX_ENTRIES`` (oldest ``last_failure_at`` evicted); accounts never."""
        now = self._clock.now_utc()

        def apply(f: LockoutFile) -> LockoutState:
            if key.user_id is not None:
                table, name = f.accounts, key.user_id
            else:
                table, name = f.phantoms, str(key.phantom)
            new = policy.register_failure(table.get(name, LockoutState()), now)
            table[name] = new
            while len(f.phantoms) > PHANTOM_LOCKOUT_MAX_ENTRIES:
                oldest = _oldest_phantom(f.phantoms, keep=name)
                if oldest is None:  # pragma: no cover - cap >= 1 keeps at least the new entry
                    break
                del f.phantoms[oldest]
            return new

        return self._mutate(apply)

    def reset(self, user_id: str, *, repair_corrupt: bool = False) -> None:
        """Clear one account's entry; writes nothing when it is already clear.

        ``repair_corrupt=True`` (``ao auth unlock``) rewrites a corrupt file as an empty valid one
        (with a fresh name key) and logs one WARNING; without it a corrupt file raises.
        """
        self._mutate(lambda f: f.accounts.pop(user_id, None), repair_corrupt=repair_corrupt)

    def forget(self, user_id: str) -> None:
        """Remove a deleted account's entry (``remove-user``)."""
        self._mutate(lambda f: f.accounts.pop(user_id, None))

    # -- internals ---------------------------------------------------------------------------

    def _new_name_key(self) -> str:
        return self._entropy.token_bytes(LOCKOUT_NAME_KEY_BYTES).hex()

    def _read(self, *, fail_closed: bool) -> LockoutFile:
        """The cached snapshot (SHARED: read-only). A missing file is an empty ``LockoutFile``.

        ``fail_closed`` maps a corrupt file to ``StoreUnavailableError`` (the serving path).
        """
        try:
            return self._snapshot()
        except StoreCorruptError as exc:
            if not fail_closed:
                raise
            raise StoreUnavailableError(cause_for_log=str(exc)) from exc

    def _snapshot(self) -> LockoutFile:
        path = self._paths.lockouts_file
        try:
            st = os.stat(path)
        except FileNotFoundError:
            return self._set_cache(None, LockoutFile())
        except OSError as exc:
            raise StoreUnavailableError(cause_for_log=f"cannot stat {path}: {exc}") from exc
        key = (st.st_ino, st.st_mtime_ns, st.st_size)
        with self._cache_guard:
            if key == self._cache_key and self._cache is not None:
                return self._cache
        try:
            model = load_lockout_file(path)
        except FileNotFoundError:  # removed between stat and read
            return self._set_cache(None, LockoutFile())
        return self._set_cache(key, model)

    def _set_cache(self, key: _StatKey | None, model: LockoutFile) -> LockoutFile:
        with self._cache_guard:
            self._cache_key, self._cache = key, model
        return model

    def _enter_lock(self, stack: contextlib.ExitStack) -> None:
        try:
            enter_state_lock(
                stack, self._paths.lockouts_lock, self._paths.state_dir, self._lock_timeout
            )
        except fsutil.LockTimeoutError as exc:
            raise StoreLockTimeoutError(str(exc)) from exc

    def _mutate(self, fn: Callable[[LockoutFile], _T], *, repair_corrupt: bool = False) -> _T:
        """Apply ``fn`` to a fresh copy under the lock and persist the result if it changed."""
        path = self._paths.lockouts_file
        try:
            with contextlib.ExitStack() as stack:
                self._enter_lock(stack)
                fsutil.remove_stale_temp_files(path)
                repaired = False
                try:
                    current = load_lockout_file(path)
                except FileNotFoundError:
                    current = LockoutFile()
                except StoreCorruptError as exc:
                    if not repair_corrupt:
                        raise StoreUnavailableError(cause_for_log=str(exc)) from exc
                    _log.warning("lockouts.json was corrupt and has been reset to an empty file")
                    current, repaired = LockoutFile(name_key_hex=self._new_name_key()), True
                before = current.model_dump(mode="json")
                result = fn(current)
                after = current.model_dump(mode="json")
                if not repaired and after == before:
                    return result
                try:
                    validated = LockoutFile.model_validate(after)
                except ValidationError as exc:
                    raise StoreCorruptError(
                        f"refusing to write invalid lockouts.json: {_field_paths(exc)}"
                    ) from exc
                fsutil.atomic_write_bytes(path, canonical_json(validated.model_dump(mode="json")))
                self._refresh_after_write(validated)
                return result
        except OSError as exc:
            raise StoreUnavailableError(cause_for_log=f"{path}: {exc}") from exc

    def _refresh_after_write(self, validated: LockoutFile) -> None:
        try:
            st = os.stat(self._paths.lockouts_file)
        except OSError:
            self._set_cache(None, validated)
            return
        self._set_cache((st.st_ino, st.st_mtime_ns, st.st_size), validated)
