"""User-store test helpers (owner: T-8NQP8J; HLD section 20.2).

``make_store`` builds a real :class:`UserStore` in a private temp directory, so tests exercise the
same flock + atomic-write path as production with a fixed clock and seeded entropy. The module
also holds the **module-level** ``spawn`` worker targets the multiprocessing tests need (a spawn
child can only import top-level functions). Test-only: never imported by ``src/``.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_orchestrator.auth.paths import StorePaths
from agent_orchestrator.auth.recovery import generate_recovery_codes, new_recovery_records
from agent_orchestrator.auth.seams import Clock, Entropy
from agent_orchestrator.auth.store import (
    UserStore,
    add_user,
    consume_totp,
    enroll_totp,
)
from agent_orchestrator.auth.totp import b32encode_secret, hotp, totp_step

from .core import FakeClock, SeededEntropy

STORE_NOW = datetime(2026, 1, 1, tzinfo=UTC)  # FakeClock's default start
STORE_LOCK_TIMEOUT_FOR_TESTS = 5.0

# Well-formed against the users.json schema (the store itself never parses a password hash).
TEST_PASSWORD_HASH = (
    "$scrypt$v=1$ln=10,r=8,p=1$"
    + base64.b64encode(bytes(16)).decode().rstrip("=")
    + "$"
    + base64.b64encode(bytes(range(32))).decode().rstrip("=")
)
OTHER_PASSWORD_HASH = TEST_PASSWORD_HASH.replace("ln=10", "ln=11")

# RFC 6238 appendix B SHA-1 seed: ASCII "12345678901234567890".
RFC_KEY = b"12345678901234567890"
RFC_SECRET_B32 = b32encode_secret(RFC_KEY)
RFC_UNIX_T59 = 59  # step 1; HOTP(RFC_KEY, 1) = 287082 (RFC 4226 appendix D)


def make_store_paths(tmp_path: Path) -> StorePaths:
    """``<tmp>/store`` (0700) and ``<tmp>/state`` (0700)."""
    store_dir, state_dir = tmp_path / "store", tmp_path / "state"
    for d in (store_dir, state_dir):
        d.mkdir(mode=0o700, exist_ok=True)
        os.chmod(d, 0o700)  # exact regardless of the umask
    return StorePaths.at(store_dir, state_dir)


def make_store(
    tmp_path: Path,
    users: Sequence[str] = (),
    *,
    clock: Clock | None = None,
    entropy: Entropy | None = None,
    lock_timeout: float = STORE_LOCK_TIMEOUT_FOR_TESTS,
    create: bool = True,
) -> UserStore:
    """A store under ``tmp_path``, initialized (``create=True``) and seeded with ``users``
    (each with ``TEST_PASSWORD_HASH``, epoch 1, not enrolled)."""
    clock = clock or FakeClock()
    entropy = entropy or SeededEntropy(1)
    paths = make_store_paths(tmp_path)
    store = UserStore(paths, clock=clock, entropy=entropy, lock_timeout=lock_timeout)
    if create:

        def seed(f: Any) -> None:
            for name in users:
                add_user(f, name, TEST_PASSWORD_HASH, now=clock.now_utc(), entropy=entropy)

        store.mutate(seed, create=True)
    return store


def totp_code(unix_seconds: float, key: bytes = RFC_KEY) -> str:
    """The valid 6-digit code at ``unix_seconds`` (computed with the production primitives)."""
    return hotp(key, totp_step(unix_seconds))


def enroll(
    store: UserStore,
    username: str,
    *,
    now_unix: float = RFC_UNIX_T59,
    secret_b32: str = RFC_SECRET_B32,
    entropy: Entropy | None = None,
) -> list[str]:
    """Enroll ``username`` with the RFC seed (``last_used_step`` = the step at ``now_unix``).

    Returns the ten plaintext recovery codes. The user's epoch is bumped by one.
    """
    entropy = entropy or SeededEntropy(7)
    rec = store.snapshot().users[username]
    codes = generate_recovery_codes(entropy)
    store.mutate(
        lambda f: enroll_totp(
            f,
            username,
            user_id=rec.user_id,
            epoch=rec.credential_epoch,
            secret_b32=secret_b32,
            step=totp_step(now_unix),
            records=new_recovery_records(codes, entropy),
            now=STORE_NOW,
            completes_login=False,
        )
    )
    return codes


# ---------------------------------------------------------------------------
# spawn worker targets (module level so a spawned child can import them)
# ---------------------------------------------------------------------------


def _worker_store(store_dir: str, state_dir: str, seed: int) -> UserStore:
    return UserStore(
        StorePaths.at(Path(store_dir), Path(state_dir)),
        clock=FakeClock(),
        entropy=SeededEntropy(seed),
        lock_timeout=STORE_LOCK_TIMEOUT_FOR_TESTS,
    )


def add_user_worker(store_dir: str, state_dir: str, username: str, seed: int) -> None:
    """Add one user through ``mutate`` (the AC-13 concurrent-writer test)."""
    store = _worker_store(store_dir, state_dir, seed)
    entropy = SeededEntropy(seed)
    store.mutate(
        lambda f: add_user(f, username, TEST_PASSWORD_HASH, now=STORE_NOW, entropy=entropy)
    )


def consume_totp_worker(
    store_dir: str,
    state_dir: str,
    username: str,
    user_id: str,
    epoch: int,
    code: str,
    now_unix: float,
    start: Any,
    results: Any,
) -> None:
    """Wait for ``start``, consume one TOTP code, report the outcome kind (the AC-7 race)."""
    store = _worker_store(store_dir, state_dir, 0)
    start.wait(30.0)
    outcome = store.mutate(
        lambda f: consume_totp(f, username, code, user_id=user_id, epoch=epoch, now_unix=now_unix)
    )
    results.put(str(outcome.kind))
