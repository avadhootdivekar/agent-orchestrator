"""The event loop never runs a file-lock-taking store call (T-XchniS AC 13; HLD 11.15.7, AC-40).

``ThreadRecorder`` wraps the blocking store/lockout/audit methods and records which thread ran
each call. A scenario runs under ``run_async``; the loop thread is the thread the coroutine runs
on, so any recorded call on it is an event-loop-blocking call. T-yfrfxv appends its second-factor
scenarios to this harness.
"""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.auth.audit import AuditLog
from agent_orchestrator.auth.errors import AuthError
from agent_orchestrator.auth.lockouts import LockoutStore
from agent_orchestrator.auth.model import SessionState, TotpPolicy
from agent_orchestrator.auth.provider import ClientInfo
from agent_orchestrator.auth.store import UserStore

from .helpers.core import run_async
from .helpers.provider import CLIENT, PASSWORD, ProviderEnv, RehashingHasher, make_env
from .helpers.store import enroll, totp_code
from .helpers.totp_service import make_totp_env

LOOPBACK_HTTP = ClientInfo("127.0.0.1", True, False)
NEW_PASSWORD = "an entirely different passphrase"

# Every blocking, lock-taking or file-writing method the login side is allowed to call, and only
# from a worker thread. ``snapshot``/``user_by_id`` are deliberately absent: cheap cache reads.
WATCHED: tuple[tuple[type, str], ...] = (
    (UserStore, "mutate"),
    (LockoutStore, "state"),
    (LockoutStore, "record_failure"),
    (LockoutStore, "reset"),
    (LockoutStore, "forget"),
    (LockoutStore, "name_digest"),
    (LockoutStore, "ensure_name_key"),
    (AuditLog, "record"),
)


class ThreadRecorder:
    """Records ``(owner.method, thread)`` for every watched call, delegating to the original."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[str, threading.Thread]] = []
        for owner, name in WATCHED:
            self._wrap(monkeypatch, owner, name)

    def _wrap(self, monkeypatch: pytest.MonkeyPatch, owner: type, name: str) -> None:
        original = getattr(owner, name)
        label = f"{owner.__name__}.{name}"

        def wrapper(obj: Any, *args: Any, **kwargs: Any) -> Any:
            self.calls.append((label, threading.current_thread()))
            return original(obj, *args, **kwargs)

        monkeypatch.setattr(owner, name, wrapper)

    def labels(self) -> set[str]:
        return {label for label, _ in self.calls}

    def on_thread(self, thread: threading.Thread) -> list[str]:
        return [label for label, seen in self.calls if seen is thread]


def run_on_loop(scenario: Callable[[], Awaitable[None]]) -> threading.Thread:
    """Run ``scenario`` under ``run_async``; return the event-loop thread."""
    loop_thread: list[threading.Thread] = []

    async def wrapped() -> None:
        loop_thread.append(threading.current_thread())
        await scenario()

    run_async(wrapped())
    return loop_thread[0]


async def attempt(env: ProviderEnv, name: str, password: str) -> None:
    try:
        await env.provider.authenticate(name, password, CLIENT)
    except AuthError:
        pass  # a refusal is a result here; only the threads matter


def test_login_store_work_never_runs_on_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make_env(tmp_path, hasher=RehashingHasher(), threshold=10)
    recorder = ThreadRecorder(monkeypatch)

    async def scenario() -> None:
        await attempt(env, "alice", "wrong")  # failed: state + record_failure + audit.record
        await attempt(env, "ghost", "wrong")  # phantom: name_digest + the same single write
        await attempt(env, "alice", PASSWORD)  # success: rehash, reset, cas_mark_login

    loop_thread = run_on_loop(scenario)
    assert recorder.on_thread(loop_thread) == []
    assert {
        "LockoutStore.state",
        "LockoutStore.record_failure",
        "LockoutStore.reset",
        "LockoutStore.name_digest",
        "UserStore.mutate",
        "AuditLog.record",
    } <= recorder.labels()


def test_the_partial_login_audit_event_runs_off_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make_env(tmp_path, totp=TotpPolicy.OPTIONAL)
    enroll(env.store, "alice")
    recorder = ThreadRecorder(monkeypatch)

    async def scenario() -> None:
        identity = await env.provider.authenticate("alice", PASSWORD, CLIENT)
        assert identity.next_state is SessionState.PARTIAL_SECOND_FACTOR

    loop_thread = run_on_loop(scenario)
    assert recorder.on_thread(loop_thread) == []
    assert "AuditLog.record" in recorder.labels()


def test_reauth_password_change_and_logout_everywhere_run_off_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make_env(tmp_path, threshold=10)
    session = env.session_for("alice")
    recorder = ThreadRecorder(monkeypatch)

    async def scenario() -> None:
        with pytest.raises(AuthError):
            await env.provider.verify_current_password(session, "wrong", CLIENT)  # failure path
        await env.provider.change_password(session, PASSWORD, NEW_PASSWORD, CLIENT)
        fresh = env.session_for("alice")
        await env.provider.logout_everywhere(fresh)

    loop_thread = run_on_loop(scenario)
    assert recorder.on_thread(loop_thread) == []
    assert {"UserStore.mutate", "LockoutStore.record_failure", "AuditLog.record"} <= (
        recorder.labels()
    )


def test_the_harness_does_flag_a_blocking_call_on_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Self-check: a store call made directly from the coroutine IS recorded on the loop thread."""
    env = make_env(tmp_path)
    recorder = ThreadRecorder(monkeypatch)

    async def scenario() -> None:
        env.store.mutate(lambda f: None)  # a (wrong) direct call from the loop

    loop_thread = run_on_loop(scenario)
    assert recorder.on_thread(loop_thread) == ["UserStore.mutate"]


# -- second factor (T-yfrfxv; AC-40 second-factor part) --------------------------------------------


def test_second_factor_verify_runs_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = make_totp_env(tmp_path, threshold=10)
    codes = t.enrolled()
    session = t.session(state=SessionState.PARTIAL_SECOND_FACTOR)
    recorder = ThreadRecorder(monkeypatch)

    async def scenario() -> None:
        for kwargs in ({"code": "000000"}, {"code": t.code()}, {"recovery_code": codes[0]}):
            try:
                await t.svc.verify_second_factor(session, client=CLIENT, **kwargs)
            except AuthError:
                pass  # the wrong code: the failure path (record_failure + audit) is watched too

    loop_thread = run_on_loop(scenario)
    assert recorder.on_thread(loop_thread) == []
    assert {
        "UserStore.mutate",
        "LockoutStore.state",
        "LockoutStore.record_failure",
        "LockoutStore.reset",
        "AuditLog.record",
    } <= recorder.labels()


def test_enrollment_begin_with_a_token_and_confirm_run_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = make_totp_env(tmp_path, threshold=10)
    token = t.issue_token()
    session = t.session(state=SessionState.PARTIAL_ENROLL)
    recorder = ThreadRecorder(monkeypatch)

    async def scenario() -> None:
        with pytest.raises(AuthError):  # a missing token: the counted-failure path
            await t.svc.begin_enrollment(session, LOOPBACK_HTTP)
        challenge = await t.svc.begin_enrollment(session, LOOPBACK_HTTP, enrollment_token=token)
        session.pending_totp_secret = challenge.secret
        code = totp_code(t.now_unix(), challenge.secret)
        await t.svc.confirm_enrollment(session, code, LOOPBACK_HTTP)

    loop_thread = run_on_loop(scenario)
    assert recorder.on_thread(loop_thread) == []
    assert {
        "UserStore.mutate",
        "LockoutStore.record_failure",
        "LockoutStore.reset",  # a forced-enrollment confirm clears the lockout
        "AuditLog.record",
    } <= recorder.labels()


def test_disable_and_regenerate_run_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = make_totp_env(tmp_path, threshold=10)
    codes = t.enrolled()
    recorder = ThreadRecorder(monkeypatch)

    async def scenario() -> None:
        await t.svc.regenerate_recovery_codes(t.session(), PASSWORD, codes[0], LOOPBACK_HTTP)
        await t.svc.disable_totp(t.session(), PASSWORD, t.code(), CLIENT)

    loop_thread = run_on_loop(scenario)
    assert recorder.on_thread(loop_thread) == []
    assert {"UserStore.mutate", "AuditLog.record"} <= recorder.labels()
