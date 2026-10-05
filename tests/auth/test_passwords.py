"""scrypt hashing, parse bounds, policy and the bounded hasher (HLD section 11.6)."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import threading
import time

import pytest

from agent_orchestrator.auth import passwords
from agent_orchestrator.auth.constants import (
    HASH_CONCURRENCY,
    MAX_PASSWORD_BYTES,
    SCRYPT_DKLEN,
    SCRYPT_SALT_BYTES,
)
from agent_orchestrator.auth.errors import AuthError, BusyError, ErrorCode
from agent_orchestrator.auth.passwords import (
    CURRENT_PARAMS,
    BoundedScryptHasher,
    MalformedHashError,
    PasswordHasher,
    PasswordPolicy,
    ScryptParams,
    format_hash,
    hash_password,
    needs_rehash,
    normalize_password,
    parse_hash,
    verify_password,
)
from agent_orchestrator.auth.seams import Entropy
from tests.auth.helpers.core import SeededEntropy, run_async
from tests.auth.helpers.crypto import TEST_PARAMS, FastFakeHasher

PASSWORD = "correct horse battery"
HASH_RE = re.compile(
    r"^\$scrypt\$v=1\$ln=\d{1,2},r=\d{1,2},p=\d{1,2}\$[A-Za-z0-9+/]+\$[A-Za-z0-9+/]+$"
)
B64_SALT = "A" * 22  # 16 bytes, unpadded
B64_DK = "A" * 43  # 32 bytes, unpadded


def hash_str(ln: int, r: int, p: int, version: str = "1") -> str:
    return f"$scrypt$v={version}$ln={ln},r={r},p={p}${B64_SALT}${B64_DK}"


@pytest.fixture(autouse=True)
def _reset_malformed_log(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(passwords, "_malformed_logged", False)


# --- format / round trip (AC-1, AC-4) ---------------------------------------------------------


def test_round_trip_and_format() -> None:
    encoded = hash_password(PASSWORD, params=TEST_PARAMS, entropy=SeededEntropy(1))
    assert HASH_RE.match(encoded)
    parsed = parse_hash(encoded)
    assert parsed.params == TEST_PARAMS
    assert len(parsed.salt) == SCRYPT_SALT_BYTES == 16
    assert len(parsed.dk) == SCRYPT_DKLEN == 32
    assert format_hash(parsed.params, parsed.salt, parsed.dk) == encoded
    assert "=" not in "".join(encoded.split("$")[-2:])  # base64 padding stripped


def test_hash_is_deterministic_per_entropy_and_salted() -> None:
    a = hash_password(PASSWORD, params=TEST_PARAMS, entropy=SeededEntropy(1))
    assert a == hash_password(PASSWORD, params=TEST_PARAMS, entropy=SeededEntropy(1))
    assert a != hash_password(PASSWORD, params=TEST_PARAMS, entropy=SeededEntropy(2))


def test_current_params_values() -> None:
    assert (CURRENT_PARAMS.log2_n, CURRENT_PARAMS.r, CURRENT_PARAMS.p) == (15, 8, 3)
    assert CURRENT_PARAMS.n == 32768
    assert CURRENT_PARAMS.dklen == 32


def test_real_params_hash_and_verify() -> None:
    """The only test at production cost: proves ``maxmem`` is passed (default 32 MiB fails)."""
    encoded = hash_password(PASSWORD, entropy=SeededEntropy(3))
    assert encoded.startswith("$scrypt$v=1$ln=15,r=8,p=3$")
    assert verify_password(PASSWORD, encoded)
    assert not verify_password(PASSWORD + "x", encoded)
    assert not needs_rehash(encoded)


def test_maxmem_covers_required_memory() -> None:
    assert CURRENT_PARAMS.maxmem() >= 128 * CURRENT_PARAMS.n * CURRENT_PARAMS.r
    big = ScryptParams(17, 8, 1)
    assert big.maxmem() == 2 * 128 * big.n * big.r
    assert TEST_PARAMS.maxmem() >= 64 * 1024 * 1024


def test_hash_rejects_oversized_password() -> None:
    with pytest.raises(ValueError):
        hash_password("a" * (MAX_PASSWORD_BYTES + 1), params=TEST_PARAMS)


# --- parse_hash bounds (AC-2) -----------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        hash_str(9, 8, 1),
        hash_str(18, 8, 1),
        hash_str(15, 0, 1),
        hash_str(15, 17, 1),
        hash_str(15, 8, 0),
        hash_str(15, 8, 17),
        hash_str(17, 9, 1),  # 128 * 2^17 * 9 > 128 MiB
        hash_str(15, 8, 1, version="2"),
        f"$scrypt$v=1$ln=15,r=8${B64_SALT}${B64_DK}",  # missing field
        f"$scrypt$v=1$ln=15,r=8,p=3${B64_SALT}",  # missing dk
        f"$scrypt$v=1$ln=15,r=8,p=3$A${B64_DK}",  # 1 char: impossible base64 length
        f"$scrypt$v=1$ln=15,r=8,p=3$!!!!${B64_DK}",  # invalid base64 alphabet
        "$scrypt$v=1$ln=999999999999,r=8,p=1$" + B64_SALT + "$" + B64_DK,
        "$scrypt$v=1$ln=١٥,r=8,p=1$" + B64_SALT + "$" + B64_DK,  # non-ASCII digits
        "  " + hash_str(10, 8, 1),  # surrounding junk
        hash_str(10, 8, 1) + "\n",
        "",
        "plaintext",
    ],
)
def test_parse_bounds_rejects(bad: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> bytes:
        raise AssertionError("scrypt must never run while parsing")

    monkeypatch.setattr(hashlib, "scrypt", boom)
    with pytest.raises(MalformedHashError):
        parse_hash(bad)


def test_parse_bounds_accepts_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> bytes:
        raise AssertionError("scrypt must never run while parsing")

    monkeypatch.setattr(hashlib, "scrypt", boom)
    assert parse_hash(hash_str(17, 8, 1)).params == ScryptParams(17, 8, 1)  # exactly 128 MiB
    assert parse_hash(hash_str(10, 1, 1)).params.log2_n == 10
    assert parse_hash(hash_str(10, 16, 16)).params.p == 16


def test_parse_reports_dklen_from_the_hash() -> None:
    parsed = parse_hash(f"$scrypt$v=1$ln=10,r=8,p=1${B64_SALT}$AAAAAAAAAAA")
    assert parsed.params.dklen == len(parsed.dk) == 8


# --- verify_password (AC-3) -------------------------------------------------------------------


def test_verify_right_and_wrong() -> None:
    encoded = hash_password(PASSWORD, params=TEST_PARAMS, entropy=SeededEntropy(1))
    assert verify_password(PASSWORD, encoded) is True
    assert verify_password("wrong password", encoded) is False
    assert verify_password("", encoded) is False
    assert verify_password(PASSWORD.upper(), encoded) is False  # no case change


def test_verify_malformed_logs_exactly_once(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger=passwords.__name__):
        assert verify_password(PASSWORD, "garbage") is False
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert "garbage" not in errors[0].getMessage()
        caplog.clear()
        assert verify_password(PASSWORD, hash_str(99, 8, 1)) is False
        assert not [r for r in caplog.records if r.levelno == logging.ERROR]


@pytest.mark.parametrize(
    ("composed", "other"),
    [("café au lait!", "café au lait!"), ("ＡＢＣ pass", "ABC pass")],
)
def test_verify_is_nfkc_equivalent(composed: str, other: str) -> None:
    encoded = hash_password(composed, params=TEST_PARAMS, entropy=SeededEntropy(1))
    assert verify_password(other, encoded)
    assert normalize_password(composed) == normalize_password(other)


def test_normalize_keeps_whitespace_and_case() -> None:
    assert normalize_password("  Mixed Case  ") == "  Mixed Case  "


def test_oversized_password_runs_one_scrypt_and_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    long_pw = "a" * (MAX_PASSWORD_BYTES + 100)
    # A hash of the *capped prefix*: even that must not let the oversized input verify.
    salt = bytes(16)
    prefix_dk = hashlib.scrypt(
        long_pw.encode()[:MAX_PASSWORD_BYTES],
        salt=salt,
        n=TEST_PARAMS.n,
        r=TEST_PARAMS.r,
        p=TEST_PARAMS.p,
        maxmem=TEST_PARAMS.maxmem(),
        dklen=32,
    )
    encoded = format_hash(TEST_PARAMS, salt, prefix_dk)
    calls = 0
    real = hashlib.scrypt

    def counting(*a: object, **k: object) -> bytes:
        nonlocal calls
        calls += 1
        return real(*a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(passwords.hashlib, "scrypt", counting)
    assert verify_password(long_pw, encoded) is False
    assert calls == 1


def test_malformed_hash_runs_no_scrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> bytes:
        raise AssertionError("no scrypt for a malformed hash")

    monkeypatch.setattr(passwords.hashlib, "scrypt", boom)
    assert verify_password(PASSWORD, "nope") is False


# --- needs_rehash (AC-5) ----------------------------------------------------------------------


def test_needs_rehash_each_dimension() -> None:
    encoded = hash_password(PASSWORD, params=TEST_PARAMS, entropy=SeededEntropy(1))
    assert not needs_rehash(encoded, params=TEST_PARAMS)
    assert needs_rehash(encoded, params=ScryptParams(11, 8, 1))
    assert needs_rehash(encoded, params=ScryptParams(10, 9, 1))
    assert needs_rehash(encoded, params=ScryptParams(10, 8, 2))
    assert needs_rehash(encoded, params=ScryptParams(10, 8, 1, dklen=64))
    assert needs_rehash(encoded)  # defaults to CURRENT_PARAMS (ln=15)


def test_needs_rehash_malformed_is_true() -> None:
    assert needs_rehash("garbage")


# --- PasswordPolicy (AC-6) --------------------------------------------------------------------


def test_policy_violations() -> None:
    policy = PasswordPolicy(min_length=12, max_length=20)
    assert policy.violations("a" * 12, username="bob") == []
    assert policy.violations("short", username=None) == ["too_short"]
    assert policy.violations("a" * 21, username=None) == ["too_long"]
    assert policy.violations("abcdefghijk\x00l", username=None) == ["control_characters"]
    assert policy.violations("abcdefghijk\x7fl", username=None) == ["control_characters"]
    assert policy.violations("abcdefghijk\tl", username=None) == ["control_characters"]
    assert policy.violations("ALICE-Example", username="alice-example") == ["equals_username"]
    # Several at once, in a stable order.
    assert PasswordPolicy(min_length=12).violations("Bob\n", username="bob\n") == [
        "too_short",
        "control_characters",
        "equals_username",
    ]


def test_policy_length_is_measured_on_nfkc_form() -> None:
    policy = PasswordPolicy(min_length=4, max_length=4)
    assert policy.violations("éééé", username=None) == []  # 4 after NFKC
    assert policy.violations("ﬁﬁ", username=None) == []  # ligature fi -> "fi" x2 = 4
    assert policy.violations("①②③", username=None) == ["too_short"]


def test_policy_check_raises_without_leaking_password() -> None:
    sentinel = "S3NTINEL-pw\x01"
    policy = PasswordPolicy(min_length=40)
    with pytest.raises(AuthError) as info:
        policy.check(sentinel, username="u")
    err = info.value
    assert err.code is ErrorCode.PASSWORD_POLICY
    assert err.status == 400
    assert err.extra["violations"] == ["too_short", "control_characters"]
    assert sentinel not in err.detail and sentinel not in str(err) and "S3NTINEL" not in repr(err)
    assert "S3NTINEL" not in repr(err.extra)
    PasswordPolicy(min_length=3).check("fine password", username="u")  # no raise


# --- BoundedScryptHasher (AC-7) ---------------------------------------------------------------


def test_hasher_dummy_hash_computed_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[ScryptParams] = []
    real = passwords.hash_password

    def spy(raw: str, *, params: ScryptParams, entropy: Entropy) -> str:
        calls.append(params)
        return real(raw, params=params, entropy=entropy)

    monkeypatch.setattr(passwords, "hash_password", spy)
    hasher = BoundedScryptHasher(params=TEST_PARAMS, entropy=SeededEntropy(1))
    try:
        assert calls == [TEST_PARAMS]
        assert parse_hash(hasher.dummy_hash).params == TEST_PARAMS
        run_async(hasher.verify("x", hasher.dummy_hash))
        assert calls == [TEST_PARAMS]  # verifying never re-hashes the dummy
    finally:
        hasher.close()


def test_hasher_hash_verify_and_rehash() -> None:
    hasher = BoundedScryptHasher(params=TEST_PARAMS, entropy=SeededEntropy(1))
    try:
        encoded = run_async(hasher.hash(PASSWORD))
        assert parse_hash(encoded).params == TEST_PARAMS
        assert run_async(hasher.verify(PASSWORD, encoded)) is True
        assert run_async(hasher.verify("nope", encoded)) is False
        assert hasher.needs_rehash(encoded) is False
        other = hash_password(PASSWORD, params=ScryptParams(11, 8, 1))
        assert hasher.needs_rehash(other) is True
    finally:
        hasher.close()


def test_hasher_busy_when_queue_full(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = threading.Event()

    def slow(raw: str, encoded: str) -> bool:
        gate.wait(5)
        return True

    monkeypatch.setattr(passwords, "verify_password", slow)
    hasher = BoundedScryptHasher(params=TEST_PARAMS, queue_max=1, entropy=SeededEntropy(1))

    async def scenario() -> tuple[bool, str]:
        first = asyncio.create_task(hasher.verify("a", "b"))
        await asyncio.sleep(0)  # let it count itself as pending
        try:
            with pytest.raises(BusyError) as info:
                await hasher.verify("c", "d")
            with pytest.raises(BusyError):
                await hasher.hash("e")  # hash shares the same bound
            detail = info.value.headers["Retry-After"]
        finally:
            gate.set()
        return await first, detail

    try:
        result, retry_after = run_async(scenario())
    finally:
        gate.set()
        hasher.close()
    assert result is True
    assert retry_after == "1"
    assert hasher._pending == 0  # released on completion


def test_hasher_pending_released_after_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(raw: str, encoded: str) -> bool:
        raise RuntimeError("boom")

    monkeypatch.setattr(passwords, "verify_password", failing)
    hasher = BoundedScryptHasher(params=TEST_PARAMS, queue_max=1, entropy=SeededEntropy(1))
    try:
        for _ in range(3):  # would raise BusyError on the 2nd call if pending leaked
            with pytest.raises(RuntimeError):
                run_async(hasher.verify("a", "b"))
        assert hasher._pending == 0
    finally:
        hasher.close()


def test_hasher_in_flight_never_exceeds_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    lock = threading.Lock()
    state = {"now": 0, "max": 0}

    def slow(raw: str, encoded: str) -> bool:
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.02)  # real wall time: the point is overlapping threads, not the clock
        with lock:
            state["now"] -= 1
        return True

    monkeypatch.setattr(passwords, "verify_password", slow)
    hasher = BoundedScryptHasher(params=TEST_PARAMS, queue_max=50, entropy=SeededEntropy(1))

    async def many() -> list[bool]:
        return list(await asyncio.gather(*(hasher.verify("a", "b") for _ in range(10))))

    try:
        results = run_async(many())
    finally:
        hasher.close()
    assert results == [True] * 10
    assert state["max"] == HASH_CONCURRENCY == 2


def test_fast_fake_hasher_satisfies_protocol() -> None:
    fake: PasswordHasher = FastFakeHasher()
    assert parse_hash(fake.dummy_hash).params == CURRENT_PARAMS
    encoded = run_async(fake.hash("pw"))
    assert encoded == "fake$pw"
    assert run_async(fake.verify("pw", encoded)) is True
    assert run_async(fake.verify("nope", encoded)) is False
    assert fake.needs_rehash(encoded) is False
    assert isinstance(fake, FastFakeHasher) and fake.verify_calls == 2


def test_fast_fake_hasher_knobs() -> None:
    assert FastFakeHasher(rehash_needed=True).needs_rehash("x") is True
    busy = FastFakeHasher(raise_busy=True)
    with pytest.raises(BusyError):
        run_async(busy.verify("a", "b"))
    assert busy.verify_calls == 1
