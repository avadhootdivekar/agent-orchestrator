"""Recovery codes and CLI enrollment tokens (HLD section 11.8; L1, stdlib only).

A code is 80 random bits rendered as 16 Crockford base32 characters, grouped
``XXXX-XXXX-XXXX-XXXX``. CLI-issued enrollment tokens reuse exactly this format and these
functions (``generate_recovery_codes(entropy, count=1)``). The store converts
:func:`new_recovery_records` output into its own pydantic records, so this module stays
pydantic-free; :func:`find_unused_match` takes any records that expose ``salt_hex`` /
``hash_hex`` / ``used_at``.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Sequence
from typing import Protocol

from .constants import (
    RECOVERY_CODE_BYTES,
    RECOVERY_CODE_CHARS,
    RECOVERY_CODE_COUNT,
    RECOVERY_CODE_GROUP,
    RECOVERY_SALT_BYTES,
)
from .seams import SYSTEM_ENTROPY, Entropy

CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CROCKFORD_ALIASES = {"I": "1", "L": "1", "O": "0"}

_BITS_PER_CHAR = 5
_CHAR_MASK = (1 << _BITS_PER_CHAR) - 1
_GROUP_SEPARATOR = "-"
_NORMALIZE_TABLE: dict[int, int | str | None] = {
    ord(" "): None,
    ord("-"): None,
    **{ord(alias): target for alias, target in CROCKFORD_ALIASES.items()},
}
_ALPHABET_SET = frozenset(CROCKFORD_ALPHABET)


class RecoveryRecord(Protocol):
    """The read-only shape :func:`find_unused_match` needs (``store.RecoveryCodeHash``)."""

    @property
    def salt_hex(self) -> str: ...

    @property
    def hash_hex(self) -> str: ...

    @property
    def used_at(self) -> str | None: ...


def _encode_code(raw: bytes) -> str:
    """80 bits -> 16 Crockford characters (5 bits each, most significant first), grouped."""
    value = int.from_bytes(raw, "big")
    text = "".join(
        CROCKFORD_ALPHABET[(value >> (_BITS_PER_CHAR * i)) & _CHAR_MASK]
        for i in reversed(range(RECOVERY_CODE_CHARS))
    )
    return _GROUP_SEPARATOR.join(
        text[i : i + RECOVERY_CODE_GROUP]
        for i in range(0, RECOVERY_CODE_CHARS, RECOVERY_CODE_GROUP)
    )


def generate_recovery_codes(
    entropy: Entropy = SYSTEM_ENTROPY, *, count: int = RECOVERY_CODE_COUNT
) -> list[str]:
    """``count`` display-form codes (``XXXX-XXXX-XXXX-XXXX``), each from fresh entropy."""
    return [_encode_code(entropy.token_bytes(RECOVERY_CODE_BYTES)) for _ in range(count)]


def normalize_recovery_code(raw: str) -> str | None:
    """Upper-case, drop spaces/hyphens, map I/L -> 1 and O -> 0.

    Returns the 16-character canonical form, or ``None`` for a wrong length or any character
    outside the Crockford alphabet (including non-ASCII).
    """
    code = raw.upper().translate(_NORMALIZE_TABLE)
    if len(code) != RECOVERY_CODE_CHARS or not _ALPHABET_SET.issuperset(code):
        return None
    return code


def hash_recovery_code(normalized: str, salt: bytes) -> str:
    """``sha256(salt || normalized_ascii)`` as lowercase hex (HLD section 12.2)."""
    return hashlib.sha256(salt + normalized.encode("ascii")).hexdigest()


def new_recovery_records(codes: Sequence[str], entropy: Entropy) -> list[tuple[str, str]]:
    """``(salt_hex, hash_hex)`` per code, each with its own ``RECOVERY_SALT_BYTES`` salt.

    ``codes`` are display-form codes as returned by :func:`generate_recovery_codes`.

    Raises:
        ValueError: if a code is not a valid recovery code.
    """
    records: list[tuple[str, str]] = []
    for code in codes:
        normalized = normalize_recovery_code(code)
        if normalized is None:
            raise ValueError("not a valid recovery code")
        salt = entropy.token_bytes(RECOVERY_SALT_BYTES)
        records.append((salt.hex(), hash_recovery_code(normalized, salt)))
    return records


def find_unused_match(normalized: str, records: Sequence[RecoveryRecord]) -> int | None:
    """Index of the first *unused* record matching ``normalized``, else ``None``.

    A hash is computed and compared for **every** record (each has its own salt), with no early
    exit, so timing does not reveal the position of a match or how many codes remain.
    """
    found: int | None = None
    for index, record in enumerate(records):
        digest = hash_recovery_code(normalized, bytes.fromhex(record.salt_hex))
        equal = hmac.compare_digest(digest.encode("ascii"), record.hash_hex.encode("ascii"))
        if equal and record.used_at is None and found is None:
            found = index
    return found
