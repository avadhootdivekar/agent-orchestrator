"""``audit.py``: line format, validation, rotation, fail-open, concurrency and flood coalescing
(T-CsT5gk AC 8-13). Fixed clock; the concurrency test uses real ``spawn`` processes."""

from __future__ import annotations

import json
import logging
import multiprocessing
import os
import stat
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from agent_orchestrator import fsutil
from agent_orchestrator.auth import audit as audit_mod
from agent_orchestrator.auth import constants
from agent_orchestrator.auth.audit import (
    AUTH_DETAIL_KEYS,
    INVALID_EVENT_NAME,
    AuditEvent,
    AuditLog,
    AuditOutcome,
    NullAuditLog,
    validate_event,
)
from agent_orchestrator.auth.model import AuditEventName

from .helpers.core import FakeClock
from .helpers.state import audit_worker, make_audit_log, make_lockout_store
from .helpers.store import make_store_paths

SPAWN = multiprocessing.get_context("spawn")
JOIN_TIMEOUT = 60.0
SCHEMA = Draft202012Validator(
    json.loads((Path(__file__).parent / "schemas" / "auth-audit-line-v1.json").read_text())
)
USER_ID = "6c1e0b5f2a7d4e9c8b3a1f0e9d8c7b6a"
SESSION_ID = "9b2f4c0e5d8a4f7e9b2f4c0e5d8a4f7e"


def lines(log: AuditLog) -> list[dict[str, Any]]:
    path = log._paths.audit_file
    if not path.exists():
        return []
    out = [json.loads(line) for line in path.read_text().splitlines()]
    for entry in out:
        assert [e.message for e in SCHEMA.iter_errors(entry)] == []
    return out


def login_failure(**kw: Any) -> AuditEvent:
    return AuditEvent(AuditEventName.LOGIN_FAILURE, AuditOutcome.FAILURE, **kw)


def logout() -> AuditEvent:
    return AuditEvent(AuditEventName.LOGOUT, AuditOutcome.INFO, realm="cli")


# ---------------------------------------------------------------------------
# Lines (AC 8)
# ---------------------------------------------------------------------------


class TestLines:
    def test_a_full_event_is_one_schema_valid_line(self, tmp_path: Path) -> None:
        log = make_audit_log(tmp_path)
        log.record(
            AuditEvent(
                AuditEventName.LOGIN_SUCCESS,
                AuditOutcome.SUCCESS,
                username="alice",
                user_id=USER_ID,
                realm="ui:3f2a9c0d1e7b",
                client_addr="127.0.0.1",
                session_id=SESSION_ID,
                auth_method="password+totp",
                details={"second_factor": "totp", "rehashed": False},
            )
        )
        raw = log._paths.audit_file.read_text()
        assert raw.endswith("\n") and raw.count("\n") == 1
        (entry,) = lines(log)
        assert entry["v"] == constants.AUDIT_SCHEMA_VERSION == 1
        assert entry["pid"] == os.getpid()
        assert entry["event"] == "auth.login.success" and entry["outcome"] == "success"
        assert entry["username"] == "alice" and entry["user_id"] == USER_ID
        assert entry["username_hash"] is None
        assert entry["details"] == {"second_factor": "totp", "rehashed": False}

    def test_the_file_is_in_the_state_dir_at_0600(self, tmp_path: Path) -> None:
        log = make_audit_log(tmp_path)
        log.record(logout())
        paths = make_store_paths(tmp_path)
        assert log._paths.audit_file == paths.state_dir / "audit.jsonl"
        assert not (paths.store_dir / "audit.jsonl").exists()
        assert stat.S_IMODE(os.stat(paths.audit_file).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(paths.audit_lock).st_mode) == 0o600

    def test_timestamp_keys_and_ascii(self, tmp_path: Path) -> None:
        clock = FakeClock()
        clock.advance(0.1237)  # sub-second wall time
        log = make_audit_log(tmp_path, clock=clock)
        log.record(AuditEvent(AuditEventName.LOGIN_FAILURE, AuditOutcome.FAILURE, username="rémi☃"))
        text = log._paths.audit_file.read_text()
        assert text.isascii() and "\\u00e9" in text and "\\u2603" in text
        entry = json.loads(text)
        assert entry["ts"] == "2026-01-01T00:00:00.123Z"
        assert list(entry) == sorted(entry)
        assert " " not in text.split('"username"')[0]  # compact separators

    def test_user_id_is_null_when_absent(self, tmp_path: Path) -> None:
        log = make_audit_log(tmp_path)
        log.record(logout())
        assert lines(log)[0]["user_id"] is None

    def test_unknown_name_event_with_a_keyed_digest_prefix(self, tmp_path: Path) -> None:
        store = make_lockout_store(tmp_path)
        digest = store.name_digest("ghost")
        log = make_audit_log(tmp_path)
        log.record(
            login_failure(username_hash=digest[: constants.AUDIT_USERNAME_HASH_CHARS], realm="hub")
        )
        (entry,) = lines(log)
        assert entry["username_hash"] == digest[:16] and entry["username"] is None
        assert not hasattr(audit_mod, "username_hash")  # v2.1: AuditLog never hashes names

    def test_for_state_dir_creates_the_directory_at_0700(self, tmp_path: Path) -> None:
        state_dir = tmp_path / "fresh" / "state"
        log = AuditLog.for_state_dir(state_dir, clock=FakeClock(), strict=True)
        log.record(logout())
        assert stat.S_IMODE(os.stat(state_dir).st_mode) == 0o700
        assert (state_dir / "audit.jsonl").is_file()
        assert lines(log)[0]["event"] == "auth.logout"

    def test_appends_to_an_existing_file(self, tmp_path: Path) -> None:
        log = make_audit_log(tmp_path)
        log.record(logout())
        again = AuditLog(log._paths, clock=FakeClock(), strict=True)
        again.record(logout())
        assert len(lines(log)) == 2

    def test_null_audit_log_writes_nothing(self, tmp_path: Path) -> None:
        null = NullAuditLog()
        null.record(logout())
        null.flush_suppressed()
        assert list(tmp_path.iterdir()) == []

    def test_null_audit_log_runs_the_base_initializer(self) -> None:
        null = NullAuditLog()
        assert null._max_bytes > 0 and null._count == 0  # base attributes exist


# ---------------------------------------------------------------------------
# Validation (AC 9)
# ---------------------------------------------------------------------------


class TestValidation:
    @pytest.mark.parametrize(
        "event",
        [
            AuditEvent("login", AuditOutcome.INFO),
            AuditEvent("Auth.Login", AuditOutcome.INFO),
            AuditEvent("auth.nonexistent.event", AuditOutcome.INFO),
            AuditEvent("auth.logout", AuditOutcome.INFO, details={"not_allowed": 1}),
            AuditEvent("approval.requested", AuditOutcome.INFO, details={"nested": {"a": 1}}),  # type: ignore[dict-item]
            AuditEvent("approval.requested", AuditOutcome.INFO, details={"note": "x" * 201}),
            AuditEvent("approval.requested", AuditOutcome.INFO, details={"ratio": 0.5}),  # type: ignore[dict-item]
            AuditEvent("auth.logout", AuditOutcome.INFO, username="a", username_hash="0" * 16),
        ],
    )
    def test_strict_raises_for_every_kind_of_malformed_event(
        self, tmp_path: Path, event: AuditEvent
    ) -> None:
        log = make_audit_log(tmp_path, strict=True)
        with pytest.raises(ValueError, match="invalid audit event"):
            log.record(event)
        assert lines(log) == []

    def test_a_long_string_is_rejected_in_auth_events_too(self, tmp_path: Path) -> None:
        log = make_audit_log(tmp_path)
        with pytest.raises(ValueError):
            log.record(login_failure(details={"reason": "x" * 201}))
        log.record(login_failure(details={"reason": "x" * 200}))  # the limit itself is fine

    def test_other_namespaces_need_no_registration(self, tmp_path: Path) -> None:
        log = make_audit_log(tmp_path)
        log.record(
            AuditEvent(
                "approval.requested", AuditOutcome.INFO, details={"approval_id": "ap-1", "n": 3}
            )
        )
        assert lines(log)[0]["details"] == {"approval_id": "ap-1", "n": 3}

    def test_every_detail_key_the_hld_lists_is_allowed(self) -> None:
        for key in AUTH_DETAIL_KEYS:
            assert (
                validate_event(AuditEvent("auth.logout", AuditOutcome.INFO, details={key: 1})) == []
            )

    def test_error_messages_never_echo_values(self, tmp_path: Path) -> None:
        log = make_audit_log(tmp_path)
        secret = "s3cr3t-value-" + "x" * 200
        with pytest.raises(ValueError) as info:
            log.record(login_failure(details={"reason": secret}))
        assert "s3cr3t" not in str(info.value)

    def test_non_strict_writes_a_reduced_event_and_logs_one_error_per_name(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        log = make_audit_log(tmp_path, strict=False)
        bad = login_failure(
            username="alice", user_id=USER_ID, realm="hub", details={"bogus_key": "SECRET"}
        )
        with caplog.at_level(logging.DEBUG):
            log.record(bad)
            log.record(bad)  # same name: no second ERROR
            log.record(AuditEvent("auth.logout", AuditOutcome.INFO, details={"bogus_key": 1}))
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 2  # one per event name
        assert "SECRET" not in caplog.text
        first, second, third = lines(log)
        for entry in (first, second):
            assert entry["event"] == "auth.login.failure" and entry["outcome"] == "failure"
            assert (entry["username"], entry["user_id"], entry["realm"]) == (
                "alice",
                USER_ID,
                "hub",
            )
            assert entry["details"] == {}
        assert third["event"] == "auth.logout" and third["details"] == {}

    def test_non_strict_replaces_a_malformed_event_name(self, tmp_path: Path) -> None:
        log = make_audit_log(tmp_path, strict=False)
        log.record(AuditEvent("not namespaced\nforged line", AuditOutcome.INFO))
        (entry,) = lines(log)
        assert entry["event"] == INVALID_EVENT_NAME

    def test_non_strict_drops_the_hash_when_both_identities_are_set(self, tmp_path: Path) -> None:
        log = make_audit_log(tmp_path, strict=False)
        log.record(login_failure(username="alice", username_hash="a" * 16))
        (entry,) = lines(log)
        assert entry["username"] == "alice" and entry["username_hash"] is None


# ---------------------------------------------------------------------------
# Rotation (AC 10, cut-line #1)
# ---------------------------------------------------------------------------


class TestRotation:
    def test_files_shift_up_to_the_backup_count_and_every_line_parses(self, tmp_path: Path) -> None:
        log = make_audit_log(tmp_path, max_bytes=200)
        for i in range(10):
            log.record(AuditEvent("approval.requested", AuditOutcome.INFO, details={"n": i}))
        state_dir = log._paths.state_dir
        names = sorted(p.name for p in state_dir.glob("audit.jsonl*"))
        expected = ["audit.jsonl"] + [
            f"audit.jsonl.{i}" for i in range(1, constants.AUDIT_BACKUP_COUNT + 1)
        ]
        assert names == sorted(expected)
        seen = []
        for name in names:
            for line in (state_dir / name).read_text().splitlines():
                assert [e.message for e in SCHEMA.iter_errors(json.loads(line))] == []
                seen.append(json.loads(line)["details"]["n"])
            assert stat.S_IMODE(os.stat(state_dir / name).st_mode) == 0o600
        # Each line exceeds max_bytes, so every write rotates: the oldest four events fell off.
        assert sorted(seen) == [4, 5, 6, 7, 8, 9]
        newest = json.loads((state_dir / "audit.jsonl").read_text())["details"]["n"]
        assert newest == 9

    def test_no_rotation_below_the_limit(self, tmp_path: Path) -> None:
        log = make_audit_log(tmp_path)
        for _ in range(20):
            log.record(logout())
        assert [p.name for p in log._paths.state_dir.glob("audit.jsonl*")] == ["audit.jsonl"]

    def test_zero_backups_just_discards_the_full_file(self, tmp_path: Path) -> None:
        log = make_audit_log(tmp_path, max_bytes=200, backups=0)
        for _ in range(4):
            log.record(logout())
        assert [p.name for p in log._paths.state_dir.glob("audit.jsonl*")] == ["audit.jsonl"]
        assert len(lines(log)) == 1

    def test_one_oversized_line_in_an_empty_file_is_still_written(self, tmp_path: Path) -> None:
        log = make_audit_log(tmp_path, max_bytes=10)
        log.record(logout())
        assert len(lines(log)) == 1
        assert not (log._paths.state_dir / "audit.jsonl.1").exists()


# ---------------------------------------------------------------------------
# Fail-open (AC 11)
# ---------------------------------------------------------------------------


class TestFailOpen:
    def test_permission_error_is_one_error_naming_the_event_only(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = make_audit_log(tmp_path)

        def deny(*args: Any, **kwargs: Any) -> int:
            raise PermissionError("denied")

        monkeypatch.setattr(os, "open", deny)
        with caplog.at_level(logging.DEBUG):
            log.record(login_failure(username="alice", details={"reason": "PAYLOAD-SENTINEL"}))
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        message = errors[0].getMessage()
        assert "auth.login.failure" in message and "PermissionError" in message
        assert "PAYLOAD-SENTINEL" not in caplog.text and "alice" not in caplog.text

    def test_lock_timeout_fails_open(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        log = make_audit_log(tmp_path, lock_timeout=0.05)
        with fsutil.FileLock(log._paths.audit_lock, timeout=1.0):
            with caplog.at_level(logging.DEBUG):
                log.record(logout())
        assert [r.levelno for r in caplog.records] == [logging.ERROR]
        assert lines(log) == []

    def test_a_symlinked_audit_file_is_not_followed(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        log = make_audit_log(tmp_path)
        target = tmp_path / "victim.txt"
        target.write_text("untouched")
        log._paths.audit_file.symlink_to(target)
        with caplog.at_level(logging.DEBUG):
            log.record(logout())
        assert target.read_text() == "untouched"
        assert [r.levelno for r in caplog.records] == [logging.ERROR]

    def test_the_log_recovers_after_a_failure(self, tmp_path: Path) -> None:
        log = make_audit_log(tmp_path, lock_timeout=0.05)
        with fsutil.FileLock(log._paths.audit_lock, timeout=1.0):
            log.record(logout())  # lost
        log.record(logout())
        assert len(lines(log)) == 1


# ---------------------------------------------------------------------------
# Concurrency (AC 12)
# ---------------------------------------------------------------------------


def test_four_spawn_processes_write_one_hundred_valid_lines(tmp_path: Path) -> None:
    paths = make_store_paths(tmp_path)
    procs = [
        SPAWN.Process(target=audit_worker, args=(str(paths.state_dir), i, 25)) for i in range(4)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=JOIN_TIMEOUT)
    assert [p.exitcode for p in procs] == [0] * 4
    entries = [json.loads(line) for line in paths.audit_file.read_text().splitlines()]
    assert len(entries) == 100
    for entry in entries:
        assert [e.message for e in SCHEMA.iter_errors(entry)] == []
    assert len({e["details"]["reason"] for e in entries}) == 100  # nothing lost or duplicated


# ---------------------------------------------------------------------------
# Flood coalescing (AC 13, cut-line #1)
# ---------------------------------------------------------------------------


class TestCoalescing:
    def test_failures_above_the_cap_are_counted_and_summarised_next_minute(
        self, tmp_path: Path
    ) -> None:
        clock = FakeClock()
        log = make_audit_log(tmp_path, clock=clock)
        for _ in range(constants.AUDIT_FAILURE_EVENTS_PER_MINUTE + 1):
            log.record(login_failure(realm="hub"))
        assert len(lines(log)) == constants.AUDIT_FAILURE_EVENTS_PER_MINUTE == 60

        clock.advance(60)
        log.record(logout())

        entries = lines(log)
        assert len(entries) == 62
        burst, after = entries[-2], entries[-1]
        assert burst["event"] == "auth.failure.burst" and burst["outcome"] == "info"
        assert burst["details"] == {"suppressed": 1}
        assert after["event"] == "auth.logout"

    def test_a_failure_in_the_next_minute_also_flushes_first_and_counts_fresh(
        self, tmp_path: Path
    ) -> None:
        clock = FakeClock()
        log = make_audit_log(tmp_path, clock=clock, failures_per_minute=2)
        for _ in range(5):
            log.record(login_failure())
        clock.advance(60)
        log.record(login_failure())
        events = [e["event"] for e in lines(log)]
        assert events == ["auth.login.failure"] * 2 + ["auth.failure.burst", "auth.login.failure"]
        assert lines(log)[2]["details"] == {"suppressed": 3}

    def test_lockout_events_are_never_suppressed(self, tmp_path: Path) -> None:
        log = make_audit_log(tmp_path, failures_per_minute=2)
        for _ in range(5):
            log.record(login_failure())
        for _ in range(3):
            log.record(AuditEvent(AuditEventName.LOCKOUT, AuditOutcome.INFO, realm="hub"))
        assert [e["event"] for e in lines(log)].count("auth.lockout") == 3

    def test_non_failure_events_are_never_suppressed(self, tmp_path: Path) -> None:
        log = make_audit_log(tmp_path, failures_per_minute=1)
        for _ in range(4):
            log.record(logout())
        assert len(lines(log)) == 4

    def test_any_failure_outcome_counts_even_outside_the_auth_namespace(
        self, tmp_path: Path
    ) -> None:
        log = make_audit_log(tmp_path, failures_per_minute=1)
        for _ in range(3):
            log.record(AuditEvent("approval.denied", AuditOutcome.FAILURE))
        assert len(lines(log)) == 1

    def test_flush_suppressed_writes_a_pending_burst_now(self, tmp_path: Path) -> None:
        log = make_audit_log(tmp_path, failures_per_minute=1)
        for _ in range(4):
            log.record(login_failure())
        log.flush_suppressed()
        last = lines(log)[-1]
        assert last["event"] == "auth.failure.burst" and last["details"] == {"suppressed": 3}
        before = len(lines(log))
        log.flush_suppressed()  # nothing pending now
        assert len(lines(log)) == before

    def test_the_cap_applies_per_wall_clock_minute(self, tmp_path: Path) -> None:
        clock = FakeClock()
        log = make_audit_log(tmp_path, clock=clock, failures_per_minute=2)
        for _ in range(3):
            log.record(login_failure())
            clock.advance(40)  # 40 s apart: minutes 0, 0, 1 -> never over the cap of 2 per minute
        assert [e["event"] for e in lines(log)] == ["auth.login.failure"] * 3
