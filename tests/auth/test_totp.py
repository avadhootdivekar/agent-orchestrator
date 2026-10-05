"""TOTP primitives: RFC 4226 / RFC 6238 vectors, window matching, URI (HLD section 11.7)."""

from __future__ import annotations

import pytest

from agent_orchestrator.auth import totp
from agent_orchestrator.auth.constants import TOTP_SECRET_BYTES
from agent_orchestrator.auth.totp import (
    b32decode_secret,
    b32encode_secret,
    hotp,
    match_totp_step,
    new_totp_secret,
    normalize_totp_code,
    otpauth_uri,
    totp_step,
)
from tests.auth.helpers.core import SeededEntropy

RFC_KEY = b"12345678901234567890"
HOTP_VECTORS = [
    "755224",
    "287082",
    "359152",
    "969429",
    "338314",
    "254676",
    "287922",
    "162583",
    "399871",
    "520489",
]
TOTP_VECTORS = {
    59: "94287082",
    1111111109: "07081804",
    1111111111: "14050471",
    1234567890: "89005924",
    2000000000: "69279037",
    20000000000: "65353130",
}
NOW = 1_700_000_010  # mid-step; step = 56666667


@pytest.mark.parametrize(("counter", "expected"), list(enumerate(HOTP_VECTORS)))
def test_hotp_rfc4226_vectors(counter: int, expected: str) -> None:
    assert hotp(RFC_KEY, counter) == expected


@pytest.mark.parametrize(("unix", "expected"), list(TOTP_VECTORS.items()))
def test_totp_rfc6238_sha1_vectors(unix: int, expected: str) -> None:
    assert hotp(RFC_KEY, totp_step(unix), digits=8) == expected
    # 6-digit codes are the last six digits; leading zeros survive (07081804 -> 081804).
    assert hotp(RFC_KEY, totp_step(unix)) == expected[-6:]


def test_leading_zero_code_stays_a_string() -> None:
    code = hotp(RFC_KEY, totp_step(1111111109))
    assert code == "081804"
    assert match_totp_step(RFC_KEY, code, 1111111109) == totp_step(1111111109)


def test_totp_step_floors_and_boundary() -> None:
    assert totp_step(0) == 0
    assert totp_step(29.999) == 0
    assert totp_step(30) == 1  # exactly on a boundary: the new step
    assert totp_step(59) == 1
    assert totp_step(120, period=60) == 2


def test_match_accepts_window_and_returns_step() -> None:
    s = totp_step(NOW)
    for delta in (-1, 0, 1):
        assert match_totp_step(RFC_KEY, hotp(RFC_KEY, s + delta), NOW) == s + delta


def test_match_rejects_outside_window() -> None:
    s = totp_step(NOW)
    for delta in (-2, 2):
        assert match_totp_step(RFC_KEY, hotp(RFC_KEY, s + delta), NOW) is None
    assert match_totp_step(RFC_KEY, hotp(RFC_KEY, s + 2), NOW, window=2) == s + 2


def test_match_rejects_garbage_codes() -> None:
    for bad in ("", "12345", "1234567", "abcdef", "١٢٣٤٥٦"):
        assert match_totp_step(RFC_KEY, bad, NOW) is None


def test_match_computes_every_candidate_even_when_first_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = totp_step(NOW)
    first = hotp(RFC_KEY, s - 1)
    calls: list[int] = []
    real = totp.hotp

    def counting(key: bytes, counter: int, **kw: int) -> str:
        calls.append(counter)
        return real(key, counter, **kw)

    monkeypatch.setattr(totp, "hotp", counting)
    assert match_totp_step(RFC_KEY, first, NOW) == s - 1
    assert calls == [s - 1, s, s + 1]  # 2*window+1, in order, no early exit
    calls.clear()
    assert match_totp_step(RFC_KEY, first, NOW, window=3) == s - 1
    assert len(calls) == 7


def test_match_skips_negative_candidates_near_epoch(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    real = totp.hotp

    def counting(key: bytes, counter: int, **kw: int) -> str:
        calls.append(counter)
        return real(key, counter, **kw)

    monkeypatch.setattr(totp, "hotp", counting)
    assert match_totp_step(RFC_KEY, hotp(RFC_KEY, 0), 5) == 0
    assert calls == [0, 1]  # step -1 skipped, never hashed with a negative counter


def test_normalize_totp_code() -> None:
    assert normalize_totp_code("123456") == "123456"
    assert normalize_totp_code("123 456") == "123456"
    assert normalize_totp_code("123-456") == "123456"
    assert normalize_totp_code(" 08-18 04 ") == "081804"
    assert normalize_totp_code("12345") is None
    assert normalize_totp_code("1234567") is None
    assert normalize_totp_code("١٢٣٤٥٦") is None  # Arabic-Indic
    assert normalize_totp_code("12345a") is None
    assert normalize_totp_code("") is None


def test_new_secret_uses_entropy_and_length() -> None:
    a = new_totp_secret(SeededEntropy(1))
    assert len(a) == TOTP_SECRET_BYTES
    assert a == new_totp_secret(SeededEntropy(1))
    assert a != new_totp_secret(SeededEntropy(2))
    assert len(new_totp_secret()) == TOTP_SECRET_BYTES  # default CSPRNG


def test_b32_round_trip_and_tolerance() -> None:
    secret = new_totp_secret(SeededEntropy(7))
    text = b32encode_secret(secret)
    assert "=" not in text and text == text.upper()
    assert len(text) == 32  # 20 bytes -> 32 chars, no padding needed
    assert b32decode_secret(text) == secret
    spaced = " ".join(text[i : i + 4] for i in range(0, len(text), 4)).lower()
    assert b32decode_secret(spaced) == secret
    # Lengths that need padding round-trip too.
    assert b32decode_secret(b32encode_secret(b"abc")) == b"abc"


@pytest.mark.parametrize("bad", ["not base32!", "1", "éééééééé"])
def test_b32_decode_rejects_garbage(bad: str) -> None:
    with pytest.raises(ValueError):
        b32decode_secret(bad)


def test_otpauth_uri_exact() -> None:
    uri = otpauth_uri("JBSWY3DPEHPK3PXP", issuer="ao@devbox", account="alice")
    assert uri == (
        "otpauth://totp/ao%40devbox:alice?secret=JBSWY3DPEHPK3PXP&issuer=ao%40devbox"
        "&algorithm=SHA1&digits=6&period=30"
    )
    assert uri.isascii()


def test_otpauth_uri_escapes_and_stays_ascii() -> None:
    uri = otpauth_uri("JBSWY3DPEHPK3PXP", issuer="café & co", account="a b:c/ü")
    assert uri.isascii()
    assert "caf%C3%A9%20%26%20co:a%20b%3Ac%2F%C3%BC?" in uri
    assert "issuer=caf%C3%A9%20%26%20co&" in uri
