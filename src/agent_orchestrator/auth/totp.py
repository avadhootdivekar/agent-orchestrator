"""RFC 4226 / RFC 6238 TOTP primitives (HLD section 11.7; L1, stdlib only).

Pure functions: no store, no clock, no I/O. ``now_unix`` is always passed in, so callers inject
the clock. Codes are strings throughout (leading zeros are significant). Replay protection is
**not** here: it is enforced inside ``store.consume_totp`` under the store lock.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from urllib.parse import quote, urlencode

from .constants import (
    TOTP_ALGORITHM,
    TOTP_DIGITS,
    TOTP_PERIOD_SECONDS,
    TOTP_SECRET_BYTES,
    TOTP_WINDOW_STEPS,
)
from .seams import SYSTEM_ENTROPY, Entropy

_HOTP_COUNTER_BYTES = 8
_DYNAMIC_TRUNCATION_MASK = 0x0F
_SIGN_BIT_MASK = 0x7FFFFFFF
_TRUNCATED_BYTES = 4
_BASE32_BLOCK = 8
_CODE_SEPARATORS = str.maketrans("", "", " -")
_OTPAUTH_SCHEME = "otpauth://totp/"


def new_totp_secret(entropy: Entropy = SYSTEM_ENTROPY) -> bytes:
    """A fresh ``TOTP_SECRET_BYTES`` shared secret."""
    return entropy.token_bytes(TOTP_SECRET_BYTES)


def b32encode_secret(secret: bytes) -> str:
    """RFC 4648 base32 without padding (the form authenticator apps display)."""
    return base64.b32encode(secret).decode("ascii").rstrip("=")


def b32decode_secret(text: str) -> bytes:
    """Inverse of :func:`b32encode_secret`; tolerant of spaces, case and missing padding.

    Raises:
        ValueError: if ``text`` is not valid base32 (``binascii.Error`` is a ``ValueError``).
    """
    cleaned = text.replace(" ", "").upper()
    if not cleaned.isascii():
        raise ValueError("secret is not valid base32")
    cleaned += "=" * (-len(cleaned) % _BASE32_BLOCK)
    return base64.b32decode(cleaned)


def hotp(key: bytes, counter: int, *, digits: int = TOTP_DIGITS) -> str:
    """RFC 4226 section 5.3 (HMAC-SHA-1, dynamic truncation), zero-padded to ``digits``."""
    mac = hmac.new(key, counter.to_bytes(_HOTP_COUNTER_BYTES, "big"), hashlib.sha1).digest()
    offset = mac[-1] & _DYNAMIC_TRUNCATION_MASK
    value = int.from_bytes(mac[offset : offset + _TRUNCATED_BYTES], "big") & _SIGN_BIT_MASK
    return str(value % 10**digits).zfill(digits)


def totp_step(unix_seconds: float, *, period: int = TOTP_PERIOD_SECONDS) -> int:
    """The RFC 6238 time step (``T0 = 0``): ``floor(unix_seconds / period)``."""
    return int(unix_seconds // period)


def normalize_totp_code(raw: str) -> str | None:
    """Drop spaces and hyphens; accept exactly ``TOTP_DIGITS`` ASCII digits, else ``None``.

    ``str.isdecimal`` is deliberately not used: it admits non-ASCII digits (Arabic-Indic, ...).
    """
    code = raw.translate(_CODE_SEPARATORS)
    if len(code) != TOTP_DIGITS or not all("0" <= ch <= "9" for ch in code):
        return None
    return code


def match_totp_step(
    key: bytes, code: str, now_unix: float, *, window: int = TOTP_WINDOW_STEPS
) -> int | None:
    """The step whose code equals ``code`` within ``+-window`` of ``now_unix``, else ``None``.

    Every candidate is computed and compared (no early exit), so timing does not reveal which
    step matched. If several match (only possible for a degenerate key) the latest wins.
    Negative candidates (near the epoch) are skipped.
    """
    current = totp_step(now_unix)
    supplied = code.encode("utf-8")
    matched: list[int] = []
    for candidate in range(current - window, current + window + 1):
        if candidate < 0:
            continue
        if hmac.compare_digest(hotp(key, candidate).encode("ascii"), supplied):
            matched.append(candidate)
    return max(matched) if matched else None


def otpauth_uri(secret_b32: str, *, issuer: str, account: str) -> str:
    """The ``otpauth://`` provisioning URI; pure ASCII (non-ASCII is percent-encoded UTF-8)."""
    label = quote(issuer, safe="") + ":" + quote(account, safe="")
    query = urlencode(
        [
            ("secret", secret_b32),
            ("issuer", issuer),
            ("algorithm", TOTP_ALGORITHM),
            ("digits", str(TOTP_DIGITS)),
            ("period", str(TOTP_PERIOD_SECONDS)),
        ],
        quote_via=quote,
    )
    return f"{_OTPAUTH_SCHEME}{label}?{query}"
