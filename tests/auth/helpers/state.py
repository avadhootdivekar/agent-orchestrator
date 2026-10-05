"""Lockout/audit test helpers (owner: T-CsT5gk; HLD section 20.2).

Builders for :class:`LockoutStore` / :class:`AuditLog` over a private temp state directory, plus the
**module-level** ``spawn`` worker targets (a spawn child can only import top-level functions).
Test-only: never imported by ``src/``.
"""

from __future__ import annotations

from pathlib import Path

from agent_orchestrator.auth.audit import AuditEvent, AuditLog, AuditOutcome
from agent_orchestrator.auth.constants import (
    AUDIT_BACKUP_COUNT,
    AUDIT_FAILURE_EVENTS_PER_MINUTE,
    AUDIT_LOCK_TIMEOUT_SECONDS,
    AUDIT_MAX_BYTES,
    STORE_LOCK_TIMEOUT_SECONDS,
)
from agent_orchestrator.auth.lockouts import LockoutKey, LockoutPolicy, LockoutStore
from agent_orchestrator.auth.model import AuditEventName
from agent_orchestrator.auth.paths import StorePaths
from agent_orchestrator.auth.seams import Clock, Entropy

from .core import FakeClock, SeededEntropy
from .store import make_store_paths

# A policy that never locks within the spawn tests, so only the counter is observed.
NEVER_LOCK_POLICY = LockoutPolicy(threshold=10_000, base_seconds=1, max_seconds=1)
USER_ID = "0123456789abcdef0123456789abcdef"
OTHER_USER_ID = "fedcba9876543210fedcba9876543210"


def make_lockout_store(
    tmp_path: Path,
    *,
    clock: Clock | None = None,
    entropy: Entropy | None = None,
    lock_timeout: float = STORE_LOCK_TIMEOUT_SECONDS,
) -> LockoutStore:
    """A lockout store over ``<tmp>/state`` (0700); the file itself is not created."""
    return LockoutStore(
        make_store_paths(tmp_path),
        clock=clock or FakeClock(),
        entropy=entropy or SeededEntropy(5),
        lock_timeout=lock_timeout,
    )


def make_audit_log(
    tmp_path: Path,
    *,
    clock: Clock | None = None,
    strict: bool = True,
    max_bytes: int = AUDIT_MAX_BYTES,
    backups: int = AUDIT_BACKUP_COUNT,
    failures_per_minute: int = AUDIT_FAILURE_EVENTS_PER_MINUTE,
    lock_timeout: float = AUDIT_LOCK_TIMEOUT_SECONDS,
) -> AuditLog:
    """An audit log over ``<tmp>/state`` (strict by default so a bad test event fails loudly)."""
    return AuditLog(
        make_store_paths(tmp_path),
        clock=clock or FakeClock(),
        strict=strict,
        max_bytes=max_bytes,
        backups=backups,
        failures_per_minute=failures_per_minute,
        lock_timeout=lock_timeout,
    )


def record_failure_worker(
    store_dir: str, state_dir: str, user_id: str, count: int, seed: int
) -> None:
    """Record ``count`` failures for ``user_id`` (the lost-update test)."""
    store = LockoutStore(
        StorePaths.at(Path(store_dir), Path(state_dir)),
        clock=FakeClock(),
        entropy=SeededEntropy(seed),
    )
    for _ in range(count):
        store.record_failure(LockoutKey(user_id=user_id), NEVER_LOCK_POLICY)


def audit_worker(state_dir: str, worker: int, count: int) -> None:
    """Write ``count`` info events tagged with ``worker`` (the concurrent-writer test)."""
    log = AuditLog.for_state_dir(Path(state_dir), clock=FakeClock(), strict=True)
    for i in range(count):
        log.record(
            AuditEvent(
                AuditEventName.LOGOUT,
                AuditOutcome.INFO,
                realm="cli",
                details={"reason": f"w{worker}-{i}"},
            )
        )
