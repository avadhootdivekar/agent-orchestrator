"""Append-only JSONL audit log in the **state** directory (HLD 11.12, 12.4; L2).

One compact line per event (sorted keys, ASCII, millisecond ``Z`` timestamp), written under the
``audit.lock`` flock and fsynced. Rotation at ``max_bytes`` shifts ``audit.jsonl -> .1 -> ...``.

* **Fail-open.** A full disk or a permission problem logs one ERROR naming the event (never its
  payload) and returns: an audit failure must not lock users out.
* **Validation.** ``strict=True`` (tests) raises ``ValueError`` for a malformed event;
  production writes a *reduced* event (same name, outcome and identity, empty ``details``) and
  logs one ERROR per event name.
* **Flood coalescing.** Above ``failures_per_minute`` failure events per wall-clock minute per
  process, further failure events are counted, not written; the next ``record()`` in a later
  minute (or ``flush_suppressed()``) first writes one ``auth.failure.burst``. ``auth.lockout`` is
  never suppressed.
* Unknown usernames are identified by ``username_hash``, which the **caller** computes from
  ``LockoutStore.name_digest`` (keyed HMAC, v2.1): this module never hashes a name.
* ``audit_log_for(request)`` lives in ``runtime.py`` (it needs ``runtime_of``, an L3 module).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from .constants import (
    AUDIT_BACKUP_COUNT,
    AUDIT_FAILURE_EVENTS_PER_MINUTE,
    AUDIT_LOCK_TIMEOUT_SECONDS,
    AUDIT_MAX_BYTES,
    AUDIT_MAX_DETAIL_CHARS,
    AUDIT_SCHEMA_VERSION,
    STORE_FILE_MODE,
)
from .lockouts import enter_state_lock
from .model import AuditEventName
from .paths import StorePaths
from .seams import SYSTEM_CLOCK, Clock

_log = logging.getLogger(__name__)

AUTH_EVENT_PREFIX = "auth."
AUDIT_EVENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
# Written instead of an event name that is not even well-formed, so every line stays schema-valid.
INVALID_EVENT_NAME = "audit.invalid_event"
_TS_FORMAT = "%Y-%m-%dT%H:%M:%S"
_MINUTE_SECONDS = 60
_KNOWN_AUTH_EVENTS = frozenset(str(name) for name in AuditEventName)

# Detail keys an ``auth.*`` event may carry. Other namespaces are not restricted (R-7c).
AUTH_DETAIL_KEYS: frozenset[str] = frozenset(
    {
        "reason",
        "second_factor",
        "recovery_codes_remaining",
        "policy",
        "retry_after_seconds",
        "lockout_failures",
        "source",
        "target_username",
        "everywhere",
        "rehashed",
        "suppressed",
        # CLI-originated events only (auth/cli.py): the effective OS uid / login name of the
        # operator running ``ao auth``. Web events have no OS identity and never carry these.
        "os_uid",
        "os_user",
    }
)

# The ``*.failure`` events: the ones flood coalescing applies to (besides any FAILURE outcome).
FAILURE_CLASS_EVENTS: frozenset[str] = frozenset(
    str(name)
    for name in (
        AuditEventName.LOGIN_FAILURE,
        AuditEventName.SECOND_FACTOR_FAILURE,
        AuditEventName.REAUTH_FAILURE,
        AuditEventName.ENROLLMENT_TOKEN_FAILURE,
    )
)


class AuditOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    INFO = "info"


DetailValue = str | int | bool | None


@dataclass(frozen=True)
class AuditEvent:
    event: str  # an ``AuditEventName`` for auth.*; other namespaces match AUDIT_EVENT_NAME_RE
    outcome: AuditOutcome
    username: str | None = None  # known accounts only
    # Unknown names only: ``LockoutStore.name_digest(normalized)[:AUDIT_USERNAME_HASH_CHARS]``.
    username_hash: str | None = None
    user_id: str | None = None  # the stable account key (known accounts only)
    realm: str | None = None  # "hub" | "ui:<workspace_id>" | "cli"
    client_addr: str | None = None  # canonical client key
    session_id: str | None = None  # non-secret
    auth_method: str | None = None
    details: Mapping[str, DetailValue] = field(default_factory=dict)


def _is_failure_event(event: AuditEvent) -> bool:
    """Counted by flood coalescing; ``auth.lockout`` is never one (the account signal survives)."""
    if str(event.event) == AuditEventName.LOCKOUT:
        return False
    return event.outcome is AuditOutcome.FAILURE or str(event.event) in FAILURE_CLASS_EVENTS


def validate_event(event: AuditEvent) -> list[str]:
    """Problems with ``event``, as short messages that never contain a payload value."""
    problems: list[str] = []
    name = str(event.event)
    if not AUDIT_EVENT_NAME_RE.fullmatch(name):
        problems.append("event name is not namespaced (expected <namespace>.<name>)")
    elif name.startswith(AUTH_EVENT_PREFIX):
        if name not in _KNOWN_AUTH_EVENTS:
            problems.append("auth.* event name is not in AuditEventName")
        unknown = sorted(set(event.details) - AUTH_DETAIL_KEYS)
        if unknown:
            problems.append(f"auth.* detail keys not allowed: {unknown}")
    for key, value in event.details.items():
        if isinstance(value, str):
            if len(value) > AUDIT_MAX_DETAIL_CHARS:
                problems.append(f"detail {key!r} is longer than {AUDIT_MAX_DETAIL_CHARS} chars")
        elif value is not None and not isinstance(value, bool | int):
            problems.append(f"detail {key!r} must be a string, int, bool or None")
    if event.username is not None and event.username_hash is not None:
        problems.append("username and username_hash are never both set")
    return problems


def _iso_millis(moment: datetime) -> str:
    utc = moment.astimezone(UTC)
    return f"{utc.strftime(_TS_FORMAT)}.{utc.microsecond // 1000:03d}Z"


def _minute_of(moment: datetime) -> int:
    return int(moment.timestamp()) // _MINUTE_SECONDS


class AuditLog:
    """Writes audit events to ``<state_dir>/audit.jsonl``. ``record`` is sync and thread-safe;
    async callers use ``await run_sync(audit.record, event)``."""

    def __init__(
        self,
        paths: StorePaths,
        *,
        clock: Clock = SYSTEM_CLOCK,
        strict: bool = False,
        max_bytes: int = AUDIT_MAX_BYTES,
        backups: int = AUDIT_BACKUP_COUNT,
        failures_per_minute: int = AUDIT_FAILURE_EVENTS_PER_MINUTE,
        lock_timeout: float = AUDIT_LOCK_TIMEOUT_SECONDS,
    ) -> None:
        self._paths = paths
        self._clock = clock
        self._strict = strict
        self._max_bytes = max_bytes
        self._backups = backups
        self._failures_per_minute = failures_per_minute
        self._lock_timeout = lock_timeout
        self._guard = threading.Lock()  # serializes this process's counters and writes
        self._minute: int | None = None
        self._count = 0
        self._suppressed = 0
        self._reported: set[str] = set()  # event names whose validation ERROR was already logged

    @classmethod
    def for_state_dir(
        cls, state_dir: Path, *, clock: Clock = SYSTEM_CLOCK, strict: bool = False
    ) -> AuditLog:
        """An audit log in ``state_dir`` alone (the approvals epic uses this when auth is off).
        Only the audit paths of ``StorePaths`` are used, so the store directory is irrelevant."""
        return cls(StorePaths.at(state_dir, state_dir), clock=clock, strict=strict)

    # -- public ------------------------------------------------------------------------------

    def record(self, event: AuditEvent) -> None:
        """Validate, coalesce floods, then append one line. Never raises in production mode
        (``strict=False``); ``strict=True`` raises ``ValueError`` for a malformed event."""
        problems = validate_event(event)
        if problems:
            if self._strict:
                raise ValueError(f"invalid audit event {str(event.event)!r}: {'; '.join(problems)}")
            event = self._reduce(event, problems)
        with self._guard:
            minute = _minute_of(self._clock.now_utc())
            if minute != self._minute:
                self._flush_burst_locked()
                self._minute, self._count = minute, 0
            if _is_failure_event(event):
                self._count += 1
                if self._count > self._failures_per_minute:
                    self._suppressed += 1
                    return
            self._write_line_locked(event)

    def flush_suppressed(self) -> None:
        """Write a pending ``auth.failure.burst`` now (the server calls this at shutdown)."""
        with self._guard:
            self._flush_burst_locked()

    # -- internals ---------------------------------------------------------------------------

    def _reduce(self, event: AuditEvent, problems: list[str]) -> AuditEvent:
        """The event minus its details, with a well-formed name. Logs one ERROR per event name."""
        name = str(event.event)
        safe_name = name if AUDIT_EVENT_NAME_RE.fullmatch(name) else INVALID_EVENT_NAME
        with self._guard:
            first = safe_name not in self._reported
            self._reported.add(safe_name)
        if first:
            _log.error("audit: invalid event %s written without details: %s", safe_name, problems)
        reduced = replace(event, event=safe_name, details={})
        if reduced.username is not None and reduced.username_hash is not None:
            reduced = replace(reduced, username_hash=None)
        return reduced

    def _flush_burst_locked(self) -> None:
        if self._suppressed <= 0:
            return
        burst = AuditEvent(
            AuditEventName.FAILURE_BURST,
            AuditOutcome.INFO,
            details={"suppressed": self._suppressed},
        )
        self._suppressed = 0
        self._write_line_locked(burst)

    def _write_line_locked(self, event: AuditEvent) -> None:
        name = str(event.event)
        line = self._format(event)
        try:
            with contextlib.ExitStack() as stack:
                enter_state_lock(
                    stack, self._paths.audit_lock, self._paths.state_dir, self._lock_timeout
                )
                self._rotate_if_needed(len(line))
                self._append(line)
        except OSError as exc:  # includes fsutil.LockTimeoutError / UnsafePathError
            # Fail open, and say which event was lost, never what it contained.
            _log.error("audit: failed to record %s (%s)", name, type(exc).__name__)

    def _format(self, event: AuditEvent) -> bytes:
        payload = {
            "v": AUDIT_SCHEMA_VERSION,
            "ts": _iso_millis(self._clock.now_utc()),
            "pid": os.getpid(),
            "event": str(event.event),
            "outcome": str(event.outcome),
            "username": event.username,
            "username_hash": event.username_hash,
            "user_id": event.user_id,
            "realm": event.realm,
            "client_addr": event.client_addr,
            "session_id": event.session_id,
            "auth_method": event.auth_method,
            "details": dict(event.details),
        }
        text = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return (text + "\n").encode("ascii")

    def _append(self, line: bytes) -> None:
        fd = os.open(
            self._paths.audit_file,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
            STORE_FILE_MODE,
        )
        try:
            view = memoryview(line)
            while view:
                view = view[os.write(fd, view) :]
            os.fsync(fd)
        finally:
            os.close(fd)

    def _rotate_if_needed(self, incoming: int) -> None:
        """Shift ``audit.jsonl -> .1 -> ... -> .<backups>`` (oldest dropped), under the lock."""
        path = self._paths.audit_file
        try:
            size = os.stat(path).st_size
        except FileNotFoundError:
            return
        if size == 0 or size + incoming <= self._max_bytes:
            return
        if self._backups <= 0:
            os.unlink(path)
            return
        for index in range(self._backups - 1, 0, -1):
            with contextlib.suppress(FileNotFoundError):
                os.replace(f"{path}.{index}", f"{path}.{index + 1}")
        os.replace(path, f"{path}.1")


class NullAuditLog(AuditLog):
    """Discards every event (tests, explicit opt-out)."""

    def __init__(self) -> None:
        # Nothing is ever written; the base initializer runs with inert paths so every attribute
        # (counters, guard) exists should a base method ever be called.
        super().__init__(StorePaths.at(Path(os.devnull), Path(os.devnull)))

    def record(self, event: AuditEvent) -> None:
        return None

    def flush_suppressed(self) -> None:
        return None
