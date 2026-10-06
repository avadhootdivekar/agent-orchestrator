"""``guard.py``: the single throttle -> gate -> lockout -> verify -> record -> audit sequence.

Fakes for the lockout store and the audit log (T-XchniS AC 3, HLD 11.11); real throttle, gates
and ``LockoutPolicy`` over a fixed clock.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from agent_orchestrator.auth.audit import AuditEvent, AuditOutcome
from agent_orchestrator.auth.errors import (
    AuthError,
    BusyError,
    ErrorCode,
    StoreCorruptError,
    StoreLockTimeoutError,
    StoreUnavailableError,
    TooManyAttemptsError,
)
from agent_orchestrator.auth.guard import (
    AttemptGuard,
    AttemptResult,
    AttemptSubject,
    from_recovery_outcome,
    from_totp_outcome,
    ok_if,
    subject_for,
)
from agent_orchestrator.auth.lockouts import LockoutKey, LockoutPolicy, LockoutState
from agent_orchestrator.auth.model import AuditEventName
from agent_orchestrator.auth.provider import ClientInfo
from agent_orchestrator.auth.store import OutcomeKind, RecoveryOutcome, TotpOutcome
from agent_orchestrator.auth.throttle import AddressThrottle, UsernameGates

from .helpers.core import FakeClock, run_async
from .helpers.provider import CLIENT
from .helpers.sessions import make_manager

THRESHOLD = 3
POLICY = LockoutPolicy(threshold=THRESHOLD, base_seconds=30, max_seconds=900)
USER_ID = "u" * 32
PHANTOM = "ab" * 32
KNOWN = AttemptSubject("alice", USER_ID, LockoutKey(user_id=USER_ID))
UNKNOWN = AttemptSubject("ghost", None, LockoutKey(phantom=PHANTOM))


class FakeLockouts:
    """Records every call; applies the real policy so thresholds and locks behave."""

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self.states: dict[LockoutKey, LockoutState] = {}
        self.calls: list[tuple[str, Any]] = []
        self.fail_with: Exception | None = None

    def state(self, key: LockoutKey) -> LockoutState:
        self.calls.append(("state", key))
        if self.fail_with is not None:
            raise self.fail_with
        return self.states.get(key, LockoutState())

    def record_failure(self, key: LockoutKey, policy: LockoutPolicy) -> LockoutState:
        self.calls.append(("record_failure", key))
        if self.fail_with is not None:
            raise self.fail_with
        new = policy.register_failure(self.states.get(key, LockoutState()), self._clock.now_utc())
        self.states[key] = new
        return new

    def reset(self, user_id: str) -> None:
        self.calls.append(("reset", user_id))
        self.states.pop(LockoutKey(user_id=user_id), None)

    def count(self, name: str) -> int:
        return sum(1 for call, _ in self.calls if call == name)


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)

    def named(self, name: AuditEventName) -> list[AuditEvent]:
        return [e for e in self.events if e.event == name]


class Harness:
    def __init__(self, *, address_threshold: int = 20) -> None:
        self.clock = FakeClock()
        self.lockouts = FakeLockouts(self.clock)
        self.audit = FakeAudit()
        self.throttle = AddressThrottle(address_threshold, clock=self.clock)
        self.guard = AttemptGuard(
            address_throttle=self.throttle,
            gates=UsernameGates(),
            lockouts=self.lockouts,  # type: ignore[arg-type]
            lockout_policy=POLICY,
            audit=self.audit,  # type: ignore[arg-type]
            realm="hub",
            clock=self.clock,
        )
        self.verify_calls = 0

    def verifier(self, *, ok: bool, reason: str | None = None) -> Callable[[], Awaitable[Any]]:
        async def verify() -> AttemptResult:
            self.verify_calls += 1
            return AttemptResult(ok, reason)

        return verify

    def attempt(
        self,
        subject: AttemptSubject = KNOWN,
        *,
        ok: bool = False,
        reset: bool = False,
        client: ClientInfo = CLIENT,
        verify: Callable[[], Awaitable[Any]] | None = None,
        event: AuditEventName = AuditEventName.LOGIN_FAILURE,
    ) -> AttemptResult:
        return run_async(
            self.guard.attempt(
                subject,
                client,
                verify=verify or self.verifier(ok=ok, reason=None if ok else "invalid_credentials"),
                failure_event=event,
                reset_on_success=reset,
            )
        )


# --- refusals happen before any verification ------------------------------------------------


def test_active_address_throttle_refuses_before_verify_and_writes_nothing() -> None:
    h = Harness(address_threshold=2)
    for _ in range(2):
        h.throttle.record_failure(CLIENT.key)
    with pytest.raises(TooManyAttemptsError) as caught:
        h.attempt(ok=True)
    assert caught.value.retry_after_seconds >= 1
    assert h.verify_calls == 0
    assert h.lockouts.calls == []  # not even a state read
    assert h.audit.events == []


def test_active_account_lockout_refuses_before_verify_and_writes_nothing() -> None:
    h = Harness()
    for _ in range(THRESHOLD):
        h.attempt()
    verify_before, writes_before = h.verify_calls, h.lockouts.count("record_failure")
    with pytest.raises(TooManyAttemptsError) as caught:
        h.attempt(ok=True)
    assert caught.value.status == 429
    assert h.verify_calls == verify_before
    assert h.lockouts.count("record_failure") == writes_before


def test_lockout_refusal_is_uniform_for_a_phantom() -> None:
    h = Harness()
    for _ in range(THRESHOLD):
        h.attempt(UNKNOWN)
    with pytest.raises(TooManyAttemptsError):
        h.attempt(UNKNOWN, ok=True)


def test_a_lock_expires_with_the_wall_clock() -> None:
    h = Harness()
    for _ in range(THRESHOLD):
        h.attempt()
    h.clock.advance(31)
    assert h.attempt(ok=True).ok


# --- BusyError --------------------------------------------------------------------------------


def test_busy_error_counts_one_address_failure_no_lockout_write_and_reraises() -> None:
    h = Harness(address_threshold=2)

    async def busy() -> AttemptResult:
        raise BusyError()

    with pytest.raises(BusyError):
        h.attempt(verify=busy)
    assert h.lockouts.count("record_failure") == 0
    assert h.audit.events == []
    # One address failure recorded: a second one reaches the threshold of 2.
    assert h.throttle.retry_after(CLIENT.key) is None
    with pytest.raises(BusyError):
        h.attempt(verify=busy)
    assert h.throttle.retry_after(CLIENT.key) is not None


def test_stale_identity_inside_verify_counts_no_failure() -> None:
    h = Harness()

    async def stale() -> AttemptResult:
        return from_totp_outcome(TotpOutcome(OutcomeKind.STALE))

    with pytest.raises(AuthError) as caught:
        h.attempt(verify=stale)
    assert caught.value.code is ErrorCode.NOT_AUTHENTICATED
    assert h.lockouts.count("record_failure") == 0
    assert h.throttle.retry_after(CLIENT.key) is None
    assert h.audit.events == []


def test_an_unexpected_verify_error_propagates_without_recording() -> None:
    h = Harness()

    async def boom() -> AttemptResult:
        raise RuntimeError("scrypt exploded")

    with pytest.raises(RuntimeError):
        h.attempt(verify=boom)
    assert h.lockouts.count("record_failure") == 0


# --- one write per failure, audited ---------------------------------------------------------


def test_known_failure_makes_exactly_one_write_one_address_failure_one_event() -> None:
    h = Harness(address_threshold=1)
    result = h.attempt(KNOWN)
    assert not result.ok and result.reason == "invalid_credentials"
    assert h.lockouts.count("record_failure") == 1
    assert h.throttle.retry_after(CLIENT.key) is not None  # one address failure at threshold 1
    (event,) = h.audit.events
    assert event.event == AuditEventName.LOGIN_FAILURE and event.outcome is AuditOutcome.FAILURE
    assert (event.username, event.username_hash, event.user_id) == ("alice", None, USER_ID)
    assert (event.realm, event.client_addr) == ("hub", CLIENT.key)
    assert event.details == {"reason": "invalid_credentials"}


def test_phantom_failure_is_identified_by_a_keyed_digest_prefix_never_an_unkeyed_hash() -> None:
    h = Harness()
    h.attempt(UNKNOWN)
    assert h.lockouts.count("record_failure") == 1  # the same single write as a known user
    (event,) = h.audit.events
    assert event.username is None and event.user_id is None
    assert event.username_hash == PHANTOM[:16]
    assert event.username_hash != hashlib.sha256(b"ghost").hexdigest()[:16]


def test_the_requested_failure_event_name_is_used() -> None:
    h = Harness()
    h.attempt(event=AuditEventName.SECOND_FACTOR_FAILURE)
    assert [e.event for e in h.audit.events] == [AuditEventName.SECOND_FACTOR_FAILURE]


def test_lockout_event_is_emitted_exactly_once_on_the_threshold_failure() -> None:
    h = Harness()
    for index in range(1, THRESHOLD + 1):
        h.attempt()
        expected = 1 if index >= THRESHOLD else 0
        assert len(h.audit.named(AuditEventName.LOCKOUT)) == expected
    h.clock.advance(1000)  # the lock expires; the next failure is the (threshold+1)-th
    h.attempt()
    (lockout,) = h.audit.named(AuditEventName.LOCKOUT)
    assert lockout.outcome is AuditOutcome.INFO and lockout.user_id == USER_ID
    assert lockout.details["lockout_failures"] == THRESHOLD
    assert lockout.details["retry_after_seconds"] == 30


def test_a_phantom_threshold_emits_the_lockout_event_with_the_hash_not_a_name() -> None:
    h = Harness()
    for _ in range(THRESHOLD):
        h.attempt(UNKNOWN)
    (lockout,) = h.audit.named(AuditEventName.LOCKOUT)
    assert lockout.username is None and lockout.username_hash == PHANTOM[:16]


# --- success ----------------------------------------------------------------------------------


def test_success_writes_nothing_unless_reset_is_requested() -> None:
    h = Harness()
    assert h.attempt(ok=True, reset=False).ok
    assert h.lockouts.count("record_failure") == 0 and h.lockouts.count("reset") == 0
    assert h.audit.events == []


def test_reset_on_success_resets_a_known_user_once() -> None:
    h = Harness()
    h.attempt()
    assert h.attempt(ok=True, reset=True).ok
    assert h.lockouts.calls.count(("reset", USER_ID)) == 1
    assert h.lockouts.states == {}


def test_reset_on_success_skips_a_phantom() -> None:
    h = Harness()
    assert h.attempt(UNKNOWN, ok=True, reset=True).ok
    assert h.lockouts.count("reset") == 0


def test_a_failed_attempt_is_not_reset_even_when_requested() -> None:
    h = Harness()
    h.attempt(reset=True)
    assert h.lockouts.count("reset") == 0


def test_result_fields_pass_through() -> None:
    h = Harness()

    async def with_remaining() -> AttemptResult:
        return from_recovery_outcome(RecoveryOutcome(OutcomeKind.OK, 7))

    assert h.attempt(verify=with_remaining).recovery_codes_remaining == 7


# --- serialization and store errors ---------------------------------------------------------


def test_attempts_on_one_account_serialize_through_the_username_gate() -> None:
    h = Harness()
    running = peak = 0

    async def slow() -> AttemptResult:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        running -= 1
        return AttemptResult(True)

    async def both() -> None:
        await asyncio.gather(
            *(
                h.guard.attempt(
                    KNOWN,
                    CLIENT,
                    verify=slow,
                    failure_event=AuditEventName.LOGIN_FAILURE,
                    reset_on_success=False,
                )
                for _ in range(3)
            )
        )

    run_async(both())
    assert peak == 1


@pytest.mark.parametrize("error", [StoreLockTimeoutError("locked"), StoreCorruptError("bad json")])
def test_lockout_store_trouble_is_a_503_with_the_cause_for_the_log(error: Exception) -> None:
    h = Harness()
    h.lockouts.fail_with = error
    with pytest.raises(StoreUnavailableError) as caught:
        h.attempt(ok=True)
    assert caught.value.status == 503
    assert "lockout state" in (caught.value.cause_for_log or "")
    assert h.verify_calls == 0  # fail closed: never verify without being able to count


def test_a_failed_attempt_whose_write_fails_is_a_503_not_a_silent_pass() -> None:
    h = Harness()

    async def verify_then_break() -> AttemptResult:
        h.lockouts.fail_with = StoreLockTimeoutError("locked")
        return AttemptResult(False, "invalid_credentials")

    with pytest.raises(StoreUnavailableError):
        h.attempt(verify=verify_then_break)


# --- helpers ------------------------------------------------------------------------------------


def test_ok_if_maps_a_flag_to_a_result() -> None:
    assert ok_if(True) == AttemptResult(True)
    assert ok_if(False) == AttemptResult(False, "invalid_credentials")
    assert ok_if(False, reason="invalid") == AttemptResult(False, "invalid")


def test_from_totp_outcome_maps_every_kind() -> None:
    assert from_totp_outcome(TotpOutcome(OutcomeKind.OK, 5)) == AttemptResult(True)
    assert from_totp_outcome(TotpOutcome(OutcomeKind.INVALID)) == AttemptResult(False, "invalid")
    assert from_totp_outcome(TotpOutcome(OutcomeKind.REPLAYED)) == AttemptResult(False, "replayed")
    with pytest.raises(AuthError) as caught:
        from_totp_outcome(TotpOutcome(OutcomeKind.STALE))
    assert caught.value.code is ErrorCode.NOT_AUTHENTICATED


def test_from_recovery_outcome_maps_every_kind() -> None:
    ok = from_recovery_outcome(RecoveryOutcome(OutcomeKind.OK, 9))
    assert ok == AttemptResult(True, recovery_codes_remaining=9)
    assert from_recovery_outcome(RecoveryOutcome(OutcomeKind.INVALID)) == AttemptResult(
        False, "invalid"
    )
    with pytest.raises(AuthError):
        from_recovery_outcome(RecoveryOutcome(OutcomeKind.STALE))


def test_subject_for_a_session_is_a_known_account() -> None:
    manager, _, _ = make_manager()
    from .helpers.sessions import make_identity

    record = manager.issue(
        make_identity(user_id=USER_ID, username="alice"),
        make_identity().next_state,
        client_key=CLIENT.key,
        auth_method="password",
    ).record
    assert subject_for(record) == KNOWN
