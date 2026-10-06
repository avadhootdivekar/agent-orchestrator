"""Recovery codes and enrollment-token format (HLD section 11.8)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import pytest

from agent_orchestrator.auth import recovery
from agent_orchestrator.auth.constants import (
    RECOVERY_CODE_CHARS,
    RECOVERY_CODE_COUNT,
    RECOVERY_SALT_BYTES,
)
from agent_orchestrator.auth.recovery import (
    CROCKFORD_ALPHABET,
    find_unused_match,
    generate_recovery_codes,
    hash_recovery_code,
    new_recovery_records,
    normalize_recovery_code,
)
from tests.auth.helpers.core import SeededEntropy

CODE_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{4}(-[0-9A-HJKMNP-TV-Z]{4}){3}$")


@dataclass
class Rec:
    """Stands in for ``store.RecoveryCodeHash`` (the store is a higher layer)."""

    salt_hex: str
    hash_hex: str
    used_at: str | None = None


def make_records(codes: list[str], seed: int = 99) -> list[Rec]:
    pairs = new_recovery_records(codes, SeededEntropy(seed))
    return [Rec(salt, digest) for salt, digest in pairs]


def test_alphabet_is_crockford() -> None:
    assert len(CROCKFORD_ALPHABET) == 32
    assert not set("ILOU") & set(CROCKFORD_ALPHABET)


def test_generate_ten_distinct_well_formed_codes() -> None:
    codes = generate_recovery_codes(SeededEntropy(1))
    assert len(codes) == RECOVERY_CODE_COUNT == 10
    assert len(set(codes)) == 10
    assert all(CODE_RE.match(c) for c in codes)


def test_generate_is_deterministic_per_seed_and_covers_alphabet() -> None:
    assert generate_recovery_codes(SeededEntropy(5)) == generate_recovery_codes(SeededEntropy(5))
    assert generate_recovery_codes(SeededEntropy(5)) != generate_recovery_codes(SeededEntropy(6))
    chars = "".join(generate_recovery_codes(SeededEntropy(3), count=200)).replace("-", "")
    assert set(chars) == set(CROCKFORD_ALPHABET)


def test_generate_encoding_is_msb_first_five_bits() -> None:
    class Fixed:
        def token_bytes(self, n: int) -> bytes:
            return bytes(n)

    assert generate_recovery_codes(Fixed(), count=1) == ["0000-0000-0000-0000"]

    class AllOnes:
        def token_bytes(self, n: int) -> bytes:
            return b"\xff" * n

    assert generate_recovery_codes(AllOnes(), count=1) == ["ZZZZ-ZZZZ-ZZZZ-ZZZZ"]

    class Counting:
        def token_bytes(self, n: int) -> bytes:
            return (0x0000_0000_0000_0000_0001).to_bytes(n, "big")

    assert generate_recovery_codes(Counting(), count=1) == ["0000-0000-0000-0001"]


def test_generate_single_enrollment_token() -> None:
    (token,) = generate_recovery_codes(SeededEntropy(2), count=1)
    assert CODE_RE.match(token)
    assert generate_recovery_codes(SeededEntropy(2), count=0) == []


def test_normalize_maps_case_separators_and_aliases() -> None:
    assert normalize_recovery_code("abcd-efgh-jkmn-pqrs") == "ABCDEFGHJKMNPQRS"
    assert normalize_recovery_code(" ABCD EFGH-JKMN pqrs ") == "ABCDEFGHJKMNPQRS"
    assert normalize_recovery_code("IiLl-Oo00-1111-0000") == "1111000011110000"
    assert normalize_recovery_code("ABCDEFGHJKMNPQRS") == "ABCDEFGHJKMNPQRS"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "ABCD-EFGH-JKMN",  # too short
        "ABCD-EFGH-JKMN-PQRS-TVWX",  # too long
        "ABCD-EFGH-JKMN-PQRU",  # U is not in the alphabet
        "ABCD-EFGH-JKMN-PQR!",
        "١BCD-EFGH-JKMN-PQRS",  # non-ASCII digit
        "éBCD-EFGH-JKMN-PQRS",
    ],
)
def test_normalize_rejects_invalid(bad: str) -> None:
    assert normalize_recovery_code(bad) is None


def test_generated_codes_normalize_to_sixteen_chars() -> None:
    for code in generate_recovery_codes(SeededEntropy(8)):
        normalized = normalize_recovery_code(code)
        assert normalized is not None and len(normalized) == RECOVERY_CODE_CHARS
        assert normalize_recovery_code(code.lower()) == normalized


def test_hash_recovery_code_matches_spec() -> None:
    salt = bytes(range(16))
    assert (
        hash_recovery_code("ABCDEFGHJKMNPQRS", salt)
        == hashlib.sha256(salt + b"ABCDEFGHJKMNPQRS").hexdigest()
    )
    assert hash_recovery_code("ABCDEFGHJKMNPQRS", salt) != hash_recovery_code(
        "ABCDEFGHJKMNPQRT", salt
    )
    assert hash_recovery_code("ABCDEFGHJKMNPQRS", salt) != hash_recovery_code(
        "ABCDEFGHJKMNPQRS", bytes(16)
    )


def test_new_recovery_records_unique_salts_and_hashes_normalized_form() -> None:
    codes = generate_recovery_codes(SeededEntropy(4))
    records = new_recovery_records(codes, SeededEntropy(10))
    assert len(records) == len(codes)
    assert len({salt for salt, _ in records}) == len(codes)
    for code, (salt_hex, hash_hex) in zip(codes, records, strict=True):
        assert len(bytes.fromhex(salt_hex)) == RECOVERY_SALT_BYTES
        normalized = normalize_recovery_code(code)
        assert normalized is not None
        assert hash_hex == hash_recovery_code(normalized, bytes.fromhex(salt_hex))
        assert re.fullmatch(r"[0-9a-f]{64}", hash_hex)


def test_new_recovery_records_rejects_invalid_code() -> None:
    with pytest.raises(ValueError):
        new_recovery_records(["not-a-code"], SeededEntropy(1))
    assert new_recovery_records([], SeededEntropy(1)) == []


def test_find_unused_match_finds_each_position() -> None:
    codes = generate_recovery_codes(SeededEntropy(11))
    records = make_records(codes)
    for i, code in enumerate(codes):
        normalized = normalize_recovery_code(code)
        assert normalized is not None
        assert find_unused_match(normalized, records) == i


def test_find_unused_match_no_match_and_empty() -> None:
    records = make_records(generate_recovery_codes(SeededEntropy(12)))
    assert find_unused_match("0" * RECOVERY_CODE_CHARS, records) is None
    assert find_unused_match("0" * RECOVERY_CODE_CHARS, []) is None


def test_find_unused_match_never_matches_used_and_picks_first_unused() -> None:
    (code,) = generate_recovery_codes(SeededEntropy(13), count=1)
    normalized = normalize_recovery_code(code)
    assert normalized is not None
    salt_a, salt_b = bytes(16), bytes([1]) * 16
    digest_a = hash_recovery_code(normalized, salt_a)
    digest_b = hash_recovery_code(normalized, salt_b)
    used = Rec(salt_a.hex(), digest_a, used_at="2026-01-01T00:00:00Z")
    unused = Rec(salt_b.hex(), digest_b)
    assert find_unused_match(normalized, [used]) is None
    assert find_unused_match(normalized, [used, unused]) == 1
    twin = Rec(salt_b.hex(), digest_b)  # a duplicate: the first unused index wins
    assert find_unused_match(normalized, [used, unused, twin]) == 1


def test_find_unused_match_hashes_every_record_even_when_first_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codes = generate_recovery_codes(SeededEntropy(14))
    records = make_records(codes)
    normalized = normalize_recovery_code(codes[0])
    assert normalized is not None
    calls = 0
    real = recovery.hash_recovery_code

    def counting(n: str, salt: bytes) -> str:
        nonlocal calls
        calls += 1
        return real(n, salt)

    monkeypatch.setattr(recovery, "hash_recovery_code", counting)
    assert find_unused_match(normalized, records) == 0
    assert calls == len(records)
    calls = 0
    assert find_unused_match("0" * RECOVERY_CODE_CHARS, records) is None
    assert calls == len(records)
