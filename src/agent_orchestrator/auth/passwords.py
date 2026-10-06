"""scrypt password hashing, the password policy and the bounded hasher (HLD section 11.6; L1).

Stdlib only: no framework, no pydantic, no file I/O. Hash strings follow HLD section 12.2:
``$scrypt$v=1$ln=15,r=8,p=3$<salt>$<dk>`` (standard base64, ``=`` stripped; ``v=1`` means NFKC +
UTF-8). :func:`parse_hash` enforces hard bounds so a hostile hash string in a tampered store can
never request gigabytes of scrypt memory (dev-security #12).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import logging
import re
import threading
import unicodedata
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol, TypeVar

from .constants import (
    HASH_CONCURRENCY,
    HASH_FORMAT_VERSION,
    HASH_QUEUE_MAX,
    MAX_PASSWORD_BYTES,
    MAX_PASSWORD_LENGTH,
    SCRYPT_DKLEN,
    SCRYPT_LOG2_N,
    SCRYPT_MAX_HASH_MEMORY_BYTES,
    SCRYPT_MAX_LOG2_N,
    SCRYPT_MAX_P,
    SCRYPT_MAX_R,
    SCRYPT_MAXMEM_BYTES,
    SCRYPT_P,
    SCRYPT_R,
    SCRYPT_SALT_BYTES,
)
from .errors import AuthError, BusyError, ErrorCode
from .seams import SYSTEM_ENTROPY, Entropy

_log = logging.getLogger(__name__)
_T = TypeVar("_T")

SCRYPT_MIN_LOG2_N = 10
_SCRYPT_BLOCK_BYTES = 128  # scrypt works on 128 * r byte blocks; memory is 128 * N * r
_MAXMEM_HEADROOM_FACTOR = 2
_HASH_PREFIX = "$scrypt$v="
_DUMMY_SECRET_BYTES = 32
_B64_PAD_BLOCK = 4
_ASCII_DEL = 0x7F
_ASCII_SPACE = 0x20

# Policy violation codes (the ``violations`` list of the ``password_policy`` error).
VIOLATION_TOO_SHORT = "too_short"
VIOLATION_TOO_LONG = "too_long"
VIOLATION_CONTROL_CHARACTERS = "control_characters"
VIOLATION_EQUALS_USERNAME = "equals_username"
VIOLATIONS_KEY = "violations"

# ``\d`` is limited to ASCII (re.ASCII) and to two digits: a hostile "ln=999999999999" is a
# format error before ``int()`` ever sees it. Bounds are checked after the match.
_HASH_RE = re.compile(
    rf"\$scrypt\$v={HASH_FORMAT_VERSION}\$ln=(\d{{1,2}}),r=(\d{{1,2}}),p=(\d{{1,2}})"
    r"\$([A-Za-z0-9+/]+)\$([A-Za-z0-9+/]+)",
    re.ASCII,
)


class MalformedHashError(ValueError):
    """A stored password hash is not a well-formed, in-bounds ``$scrypt$v=1$...`` string.

    Module-local and never user-facing: :func:`verify_password` turns it into ``False``.
    """


@dataclass(frozen=True)
class ScryptParams:
    log2_n: int
    r: int
    p: int
    dklen: int = SCRYPT_DKLEN

    @property
    def n(self) -> int:
        return 1 << self.log2_n

    def maxmem(self) -> int:
        """The ``maxmem`` to pass to ``hashlib.scrypt`` (its default 32 MiB rejects ln=15, r=8)."""
        needed = _MAXMEM_HEADROOM_FACTOR * _SCRYPT_BLOCK_BYTES * self.n * self.r
        return max(SCRYPT_MAXMEM_BYTES, needed)


CURRENT_PARAMS = ScryptParams(SCRYPT_LOG2_N, SCRYPT_R, SCRYPT_P)


@dataclass(frozen=True)
class ParsedHash:
    params: ScryptParams
    salt: bytes
    dk: bytes


def normalize_password(raw: str) -> str:
    """NFKC only: no strip, no case change (``v=1`` hashes depend on exactly this)."""
    return unicodedata.normalize("NFKC", raw)


@dataclass(frozen=True)
class PasswordPolicy:
    min_length: int
    max_length: int = MAX_PASSWORD_LENGTH

    def violations(self, raw: str, *, username: str | None = None) -> list[str]:
        """Every violated rule, computed on the NFKC form. Never contains the password."""
        normalized = normalize_password(raw)
        found: list[str] = []
        if len(normalized) < self.min_length:
            found.append(VIOLATION_TOO_SHORT)
        if len(normalized) > self.max_length:
            found.append(VIOLATION_TOO_LONG)
        if any(ord(ch) < _ASCII_SPACE or ord(ch) == _ASCII_DEL for ch in normalized):
            found.append(VIOLATION_CONTROL_CHARACTERS)
        if username is not None and normalized.casefold() == username.casefold():
            found.append(VIOLATION_EQUALS_USERNAME)
        return found

    def check(self, raw: str, *, username: str | None = None) -> None:
        """Raise ``AuthError(PASSWORD_POLICY)`` listing the violated rules, if any."""
        found = self.violations(raw, username=username)
        if found:
            raise AuthError(ErrorCode.PASSWORD_POLICY, extra={VIOLATIONS_KEY: found})


def _b64_encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii").rstrip("=")


def _b64_decode(text: str) -> bytes:
    padded = text + "=" * (-len(text) % _B64_PAD_BLOCK)
    try:
        return base64.b64decode(padded, validate=True)
    except binascii.Error as exc:
        raise MalformedHashError("invalid base64") from exc


def format_hash(params: ScryptParams, salt: bytes, dk: bytes) -> str:
    return (
        f"{_HASH_PREFIX}{HASH_FORMAT_VERSION}$ln={params.log2_n},r={params.r},p={params.p}"
        f"${_b64_encode(salt)}${_b64_encode(dk)}"
    )


def parse_hash(encoded: str) -> ParsedHash:
    """Strictly parse and bound-check a hash string. Never calls scrypt.

    Raises:
        MalformedHashError: wrong format or version, parameters out of bounds, more than
            ``SCRYPT_MAX_HASH_MEMORY_BYTES`` of scrypt memory, or invalid base64.
    """
    match = _HASH_RE.fullmatch(encoded)
    if match is None:
        raise MalformedHashError("format")
    log2_n, r, p = (int(match.group(i)) for i in (1, 2, 3))
    if not (
        SCRYPT_MIN_LOG2_N <= log2_n <= SCRYPT_MAX_LOG2_N
        and 1 <= r <= SCRYPT_MAX_R
        and 1 <= p <= SCRYPT_MAX_P
    ):
        raise MalformedHashError("parameters out of bounds")
    if _SCRYPT_BLOCK_BYTES * (1 << log2_n) * r > SCRYPT_MAX_HASH_MEMORY_BYTES:
        raise MalformedHashError("memory bound")
    salt = _b64_decode(match.group(4))
    dk = _b64_decode(match.group(5))
    return ParsedHash(ScryptParams(log2_n, r, p, dklen=len(dk)), salt, dk)


def _password_bytes(raw: str) -> tuple[bytes, bool]:
    """NFKC + UTF-8, capped at ``MAX_PASSWORD_BYTES``. The flag says the input was over the cap."""
    pw = normalize_password(raw).encode("utf-8")
    if len(pw) > MAX_PASSWORD_BYTES:
        return pw[:MAX_PASSWORD_BYTES], True
    return pw, False


def _scrypt(pw: bytes, salt: bytes, params: ScryptParams) -> bytes:
    return hashlib.scrypt(
        pw,
        salt=salt,
        n=params.n,
        r=params.r,
        p=params.p,
        maxmem=params.maxmem(),
        dklen=params.dklen,
    )


def hash_password(
    raw: str, *, params: ScryptParams = CURRENT_PARAMS, entropy: Entropy = SYSTEM_ENTROPY
) -> str:
    """Hash ``raw`` with a fresh ``SCRYPT_SALT_BYTES`` salt.

    Raises:
        ValueError: if the UTF-8 form exceeds ``MAX_PASSWORD_BYTES`` (the policy forbids this, so
            reaching it is a caller bug; such a hash could never verify).
    """
    pw, truncated = _password_bytes(raw)
    if truncated:
        raise ValueError("password exceeds the maximum encoded length")
    salt = entropy.token_bytes(SCRYPT_SALT_BYTES)
    return format_hash(params, salt, _scrypt(pw, salt, params))


# Log the "bad hash in the store" condition once per process: a corrupt record would otherwise
# log on every login attempt. Verify runs in executor threads, hence the lock.
_malformed_lock = threading.Lock()
_malformed_logged = False


def _log_malformed_once() -> None:
    global _malformed_logged
    with _malformed_lock:
        if _malformed_logged:
            return
        _malformed_logged = True
    _log.error("malformed password hash in store")


def verify_password(raw: str, encoded: str) -> bool:
    """Constant-time check of ``raw`` against ``encoded``.

    A malformed ``encoded`` returns False (one ERROR log line per process, never the hash). An
    over-long ``raw`` still runs exactly one scrypt on its capped prefix, then returns False.
    """
    try:
        parsed = parse_hash(encoded)
    except MalformedHashError:
        _log_malformed_once()
        return False
    pw, truncated = _password_bytes(raw)
    dk = _scrypt(pw, parsed.salt, parsed.params)
    return hmac.compare_digest(dk, parsed.dk) and not truncated


def needs_rehash(encoded: str, *, params: ScryptParams = CURRENT_PARAMS) -> bool:
    """True when any of ``ln``, ``r``, ``p`` or ``dklen`` differs from ``params``.

    An unparseable hash also reports True (it is certainly not at the current parameters); it
    can never verify, so a rehash is only ever reached after a successful verify.
    """
    try:
        parsed = parse_hash(encoded)
    except MalformedHashError:
        return True
    return parsed.params != params


class PasswordHasher(Protocol):
    """What the local provider depends on (fakeable; the test helpers ship a fast fake)."""

    dummy_hash: str

    async def verify(self, raw: str, encoded: str) -> bool: ...

    async def hash(self, raw: str) -> str: ...

    def needs_rehash(self, encoded: str) -> bool: ...


class BoundedScryptHasher:
    """Production :class:`PasswordHasher`: scrypt on a small thread pool with a bounded queue.

    At most ``concurrency`` scrypts run at once and at most ``queue_max`` are pending (running
    plus waiting); one more raises :class:`BusyError`. Counting that 503 as an address failure is
    the guard's job. The pending counter is confined to the event loop thread, so it needs no lock.
    """

    def __init__(
        self,
        *,
        params: ScryptParams = CURRENT_PARAMS,
        concurrency: int = HASH_CONCURRENCY,
        queue_max: int = HASH_QUEUE_MAX,
        entropy: Entropy = SYSTEM_ENTROPY,
    ) -> None:
        self._params = params
        self._entropy = entropy
        self._queue_max = queue_max
        self._pending = 0
        self._executor = ThreadPoolExecutor(
            max_workers=concurrency, thread_name_prefix="ao-auth-hash"
        )
        # Computed once, at startup: a fixed-cost hash that unknown usernames are verified
        # against so a login for a missing user costs the same as for a real one.
        self.dummy_hash: str = hash_password(
            entropy.token_bytes(_DUMMY_SECRET_BYTES).hex(), params=params, entropy=entropy
        )

    async def verify(self, raw: str, encoded: str) -> bool:
        return await self._run(verify_password, raw, encoded)

    async def hash(self, raw: str) -> str:
        return await self._run(self._hash_sync, raw)

    def needs_rehash(self, encoded: str) -> bool:
        return needs_rehash(encoded, params=self._params)

    def close(self) -> None:
        """Release the worker threads (waits for in-flight scrypts)."""
        self._executor.shutdown(wait=True)

    def _hash_sync(self, raw: str) -> str:
        return hash_password(raw, params=self._params, entropy=self._entropy)

    async def _run(self, fn: Callable[..., _T], *args: str) -> _T:
        if self._pending >= self._queue_max:
            raise BusyError()
        self._pending += 1
        try:
            return await asyncio.get_running_loop().run_in_executor(self._executor, fn, *args)
        finally:
            self._pending -= 1
