"""service.fire_store: at-most-once fire intents, schedule state, structured event log.

Fixed clocks and ``tmp_path`` state dirs only; no real processes (probes are injected).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from agent_orchestrator.service import fire_store as fs
from agent_orchestrator.service.fire_store import (
    EVENT_LEVELS,
    FireAlreadyRecordedError,
    FireIntentRecord,
    FireIntentStore,
    ScheduleEventLog,
    ScheduleStateStore,
    fire_key,
    state_key,
)

T0 = datetime(2026, 9, 6, 0, 0, tzinfo=UTC)
ROOT = "/ws/alpha"
ALL_STATUSES = ["intended", "launched", "suppressed", "orphaned", "launch_failed"]


def _rec(key: str = "k1", status: str = "intended", **kw: object) -> FireIntentRecord:
    data: dict[str, object] = {
        "fire_key": key,
        "workspace_root": ROOT,
        "schedule_id": "nightly",
        "scheduled_for": T0,
        "created_at": T0,
        "status": status,
    }
    data.update(kw)
    return FireIntentRecord.model_validate(data)


@pytest.fixture
def store(tmp_path: Path) -> FireIntentStore:
    return FireIntentStore(tmp_path, clock=lambda: T0)


# -- fire_key (AC1, AC14) ---------------------------------------------------------------------


def test_fire_key_utc_and_other_tz_same_instant_same_key() -> None:
    ny = T0.astimezone(ZoneInfo("America/New_York"))
    assert ny.isoformat() != T0.isoformat()
    assert fire_key(ROOT, "s", ny) == fire_key(ROOT, "s", T0)


def test_fire_key_naive_raises() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        fire_key(ROOT, "s", datetime(2026, 9, 6))


def test_fire_key_normalises_root(tmp_path: Path) -> None:
    (tmp_path / "w").mkdir()
    messy = f"{tmp_path}/w/../w/"
    assert fire_key(messy, "s", T0) == fire_key(tmp_path / "w", "s", T0)


def test_fire_key_stable_across_processes() -> None:
    code = (
        "from datetime import datetime, UTC;"
        "from agent_orchestrator.service.fire_store import fire_key;"
        f"print(fire_key({ROOT!r}, 's', datetime(2026,9,6,tzinfo=UTC)))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert out == fire_key(ROOT, "s", T0)


def test_fire_key_varies_with_inputs() -> None:
    base = fire_key(ROOT, "s", T0)
    assert fire_key(ROOT, "t", T0) != base
    assert fire_key(ROOT, "s", T0 + timedelta(seconds=1)) != base


# -- store basics ------------------------------------------------------------------------------


def test_record_path_uses_supervisor_slug(store: FireIntentStore, tmp_path: Path) -> None:
    from agent_orchestrator.service.supervisor import _slug_for_root

    store.write(_rec())
    resolved = str(Path(ROOT).resolve())
    assert (tmp_path / "schedules/fires" / _slug_for_root(resolved) / "k1.json").is_file()


def test_write_get_update_roundtrip(store: FireIntentStore) -> None:
    store.write(_rec())
    updated = store.update("k1", status="launched", pid=7, run_id="r1")
    got = store.get("k1")
    assert got == updated
    assert (got.status, got.pid, got.run_id) == ("launched", 7, "r1")  # type: ignore[union-attr]


def test_update_unknown_and_immutable_fields(store: FireIntentStore) -> None:
    with pytest.raises(KeyError):
        store.update("nope", status="launched")
    store.write(_rec())
    with pytest.raises(ValueError, match="immutable"):
        store.update("k1", schedule_id="other")
    with pytest.raises(ValueError):  # invalid status revalidated
        store.update("k1", status="bogus")


def test_write_refuses_existing_key_so_status_cannot_regress(store: FireIntentStore) -> None:
    store.write(_rec())
    store.update("k1", status="launched")
    with pytest.raises(FireAlreadyRecordedError):
        store.write(_rec())
    assert store.get("k1").status == "launched"  # type: ignore[union-attr]


def test_write_suppressed(store: FireIntentStore) -> None:
    r = store.write_suppressed(
        "ks", "overlap", workspace_root=ROOT, schedule_id="nightly", scheduled_for=T0
    )
    assert (r.status, r.reason, r.created_at) == ("suppressed", "overlap", T0)
    assert store.exists("ks")


def test_list_filters_order_and_limit(store: FireIntentStore) -> None:
    for i in range(3):
        store.write(_rec(f"k{i}", created_at=T0 + timedelta(minutes=i)))
    store.write(_rec("other", workspace_root="/ws/beta", status="launched"))
    assert [r.fire_key for r in store.list(workspace_root=ROOT)] == ["k2", "k1", "k0"]
    assert [r.fire_key for r in store.list(workspace_root=ROOT, limit=2)] == ["k2", "k1"]
    assert [r.fire_key for r in store.list(status="launched")] == ["other"]
    assert len(store.list()) == 4


def test_empty_store(store: FireIntentStore) -> None:
    assert store.list() == [] and store.get("x") is None and not store.exists("x")


# -- at-most-once (AC5, AC12) ------------------------------------------------------------------


@pytest.mark.parametrize("status", ALL_STATUSES)
def test_exists_true_for_every_status(store: FireIntentStore, status: str) -> None:
    store.write(_rec())
    assert store.exists("k1")
    store.update("k1", status=status)
    assert store.exists("k1")
    assert store.exists("k1", ROOT)


def test_corrupt_record_still_suppresses(store: FireIntentStore, tmp_path: Path) -> None:
    store.write(_rec())
    path = next((tmp_path / "schedules/fires").glob("*/k1.json"))
    path.write_text("{garbage")
    assert store.exists("k1")
    assert store.get("k1") is None  # unreadable but never raises


def test_crash_simulation_intended_dead_pid_is_orphaned_zero_launches(
    store: FireIntentStore,
) -> None:
    store.write(_rec(pid=4242, run_id="r-dead"))
    launches: list[str] = []  # there is no launcher; probes must be the only collaborators

    def pid_alive(pid: int) -> bool:
        launches.append(f"pid:{pid}")
        return False

    def run_exists(root: str, run_id: str) -> bool:
        launches.append(f"run:{run_id}")
        return False

    orphaned = store.reconcile_orphans(pid_alive, run_exists)
    assert [r.fire_key for r in orphaned] == ["k1"]
    assert orphaned[0].status == "orphaned"
    assert store.get("k1").status == "orphaned"  # type: ignore[union-attr]
    assert store.exists("k1")  # orphan still burns the key; never deleted
    assert launches == ["pid:4242", "run:r-dead"]  # probes only


def test_reconcile_promotes_live_pid_or_existing_run(store: FireIntentStore) -> None:
    store.write(_rec("a", pid=1))
    store.write(_rec("b", pid=None, run_id="r-b"))
    store.write(_rec("c"))  # no pid, no run_id -> orphaned
    store.write(_rec("d", status="launched"))  # untouched
    orphaned = store.reconcile_orphans(lambda pid: pid == 1, lambda root, rid: rid == "r-b")
    assert [r.fire_key for r in orphaned] == ["c"]
    assert {k: store.get(k).status for k in "abcd"} == {  # type: ignore[union-attr]
        "a": "launched",
        "b": "launched",
        "c": "orphaned",
        "d": "launched",
    }
    assert store.reconcile_orphans(lambda p: False, lambda r, i: False) == []  # idempotent


def test_clock_rewind_does_not_double_fire(store: FireIntentStore) -> None:
    """A backwards clock re-derives an already-recorded fire_key; the store still suppresses it."""
    key = fire_key(ROOT, "nightly", T0)
    store.write(_rec(key))
    store.update(key, status="launched", run_id="r1")
    rewound = T0.astimezone(ZoneInfo("Asia/Kolkata"))  # same instant, different tz, re-derived
    again = fire_key(ROOT, "nightly", rewound)
    assert again == key and store.exists(again)
    with pytest.raises(FireAlreadyRecordedError):
        store.write(_rec(again))
    # and a restart (fresh store object) agrees
    assert FireIntentStore(store._root.parent.parent).exists(again)


# -- durability (AC4, AC13) --------------------------------------------------------------------


def test_write_calls_fsync(store: FireIntentStore, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    real = os.fsync
    monkeypatch.setattr(fs.os, "fsync", lambda fd: (calls.append(fd), real(fd))[1])
    store.write(_rec())
    assert len(calls) == 1
    store.update("k1", status="launched")
    assert len(calls) == 2  # every persist, not just the first intent


def test_failed_write_leaves_no_tmp_and_no_record(
    store: FireIntentStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def boom(fd: int) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(fs.os, "fsync", boom)
    with pytest.raises(OSError):
        store.write(_rec())
    monkeypatch.undo()
    assert not store.exists("k1")
    assert list((tmp_path / "schedules/fires").rglob("*.tmp")) == []


def test_truncated_tmp_never_returned(store: FireIntentStore, tmp_path: Path) -> None:
    store.write(_rec())
    d = next((tmp_path / "schedules/fires").iterdir())
    (d / "k2.json.tmp").write_text('{"fire_key": "k2", "status": "inten')
    assert store.get("k2") is None and not store.exists("k2")
    assert [r.fire_key for r in store.list()] == ["k1"]


def test_corrupt_json_record_skipped_by_list(
    store: FireIntentStore, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store.write(_rec())
    d = next((tmp_path / "schedules/fires").iterdir())
    (d / "bad.json").write_text("nope")
    with caplog.at_level(logging.WARNING):
        assert [r.fire_key for r in store.list()] == ["k1"]
    assert "unreadable" in caplog.text


# -- prune -------------------------------------------------------------------------------------


def test_prune_by_age_and_count_never_drops_intended(tmp_path: Path) -> None:
    now = T0 + timedelta(days=100)
    st = FireIntentStore(tmp_path, clock=lambda: now)
    st.write(_rec("old", status="launched", created_at=T0))
    st.write(_rec("old-intended", status="intended", created_at=T0))
    for i in range(3):
        st.write(_rec(f"new{i}", status="launched", created_at=now - timedelta(hours=i)))
    assert st.prune(max_age_days=90, max_records=100) == 1
    assert not st.exists("old") and st.exists("old-intended")
    assert st.prune(max_age_days=90, max_records=2) == 1  # oldest non-intended beyond cap
    assert {r.fire_key for r in st.list()} == {"old-intended", "new0", "new1"}


# -- schedule state (AC7, AC8, AC15) -----------------------------------------------------------


def test_state_record_fire_and_persistence(tmp_path: Path) -> None:
    st = ScheduleStateStore(tmp_path)
    assert st.get(ROOT, "s") is None
    st.record_failure(ROOT, "s", "boom")
    st.record_fire(ROOT, "s", scheduled_for=T0, run_id="r1", fire_at=T0, fire_key="k1")
    st.record_fire(ROOT, "s", scheduled_for=T0, run_id="r2", fire_at=T0, fire_key="k1")
    reloaded = ScheduleStateStore(tmp_path).get(ROOT, "s")  # persisted immediately
    assert reloaded is not None
    assert (reloaded.runs_count, reloaded.last_run_id, reloaded.last_fire_at) == (2, "r2", T0)
    assert reloaded.consecutive_failures == 0
    assert reloaded.active_fire_keys == ["k1"]


def test_state_eval_errors_failures_status_queue_anchor(tmp_path: Path) -> None:
    st = ScheduleStateStore(tmp_path)
    assert [st.bump_eval_error(ROOT, "s") for _ in range(5)] == [1, 2, 3, 4, 5]
    st.clear_eval_errors(ROOT, "s")
    st.record_failure(ROOT, "s", "x")
    st.record_failure(ROOT, "s", "y")
    st.set_status(ROOT, "s", "quarantined", "too many eval errors")
    st.set_queued(ROOT, "s", "kq")
    st.set_next_fire(ROOT, "s", T0)
    st.advance_anchor(ROOT, "s", T0 + timedelta(days=1))
    s = ScheduleStateStore(tmp_path).get(ROOT, "s")
    assert s is not None
    assert (s.consecutive_eval_errors, s.consecutive_failures) == (0, 2)
    assert (s.status, s.disabled_reason) == ("quarantined", "too many eval errors")
    assert (s.queued_fire_key, s.next_fire_at) == ("kq", T0)
    assert s.last_scheduled_for == T0 + timedelta(days=1)
    st.set_status(ROOT, "s", "active")
    st.clear_queued(ROOT, "s")
    s2 = st.get(ROOT, "s")
    assert s2 is not None and (s2.status, s2.disabled_reason, s2.queued_fire_key) == (
        "active",
        None,
        None,
    )


def test_state_retire_finished_and_all(tmp_path: Path) -> None:
    st = ScheduleStateStore(tmp_path)
    st.record_fire(ROOT, "a", scheduled_for=T0, run_id="r", fire_at=T0, fire_key="k1")
    st.record_fire("/ws/beta", "b", scheduled_for=T0, run_id="r", fire_at=T0, fire_key="k2")
    st.retire_finished(["k1", "unknown"])
    assert st.get(ROOT, "a").active_fire_keys == []  # type: ignore[union-attr]
    assert st.get("/ws/beta", "b").active_fire_keys == ["k2"]  # type: ignore[union-attr]
    keys = list(st.all())
    assert len(keys) == 2 and all("|" in k for k in keys)


def test_state_corrupt_file_starts_clean_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    p = tmp_path / "schedules" / "state.json"
    p.parent.mkdir(parents=True)
    p.write_text("{corrupt")
    with caplog.at_level(logging.WARNING):
        st = ScheduleStateStore(tmp_path)
    assert st.all() == {} and "corrupt" in caplog.text
    st.bump_eval_error(ROOT, "s")  # next write repairs it
    assert ScheduleStateStore(tmp_path).get(ROOT, "s") is not None


# -- event log (AC9-AC11, AC16) ----------------------------------------------------------------


@pytest.mark.parametrize("event,level", sorted(EVENT_LEVELS.items()))
def test_every_hld_event_fields_and_logger_level(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, event: str, level: int
) -> None:
    log = ScheduleEventLog(tmp_path, clock=lambda: T0)
    with caplog.at_level(logging.DEBUG, logger=fs.logger.name):
        log.emit(
            event, workspace=ROOT, schedule_id="s", fire_key="k", run_id="r", lateness_seconds=3
        )
    line = json.loads((tmp_path / "schedules/events.jsonl").read_text().splitlines()[-1])
    assert line["event"] == event and line["ts"] == T0.isoformat()
    assert (line["workspace"], line["schedule_id"], line["fire_key"], line["run_id"]) == (
        ROOT,
        "s",
        "k",
        "r",
    )
    assert line["lateness_seconds"] == 3
    for common in ("scheduled_for", "reason", "detail"):
        assert common in line and line[common] is None
    rec = [r for r in caplog.records if r.name == fs.logger.name and event in r.getMessage()]
    assert [r.levelno for r in rec] == [level]


def test_hld_event_table_is_complete() -> None:
    expected = {
        "schedule.evaluated", "schedule.fired", "schedule.skipped_overlap",
        "schedule.skipped_concurrency", "schedule.missed", "schedule.catch_up",
        "schedule.fire_failed", "schedule.fire_orphaned", "schedule.until_satisfied",
        "schedule.eval_failed", "schedule.quarantined", "schedule.file_invalid",
        "schedule.enabled", "schedule.disabled", "schedule.request_expired",
        "schedule.watch_budget_exceeded", "webhook.received", "webhook.rejected",
        # governance HLD §13.1 (T-PpE5lf)
        "schedule.governance.skipped", "schedule.governance.reserved",
        "schedule.governance.run_cost_capped", "schedule.governance.launch_unsafe",
        "schedule.governance.error", "schedule.governance.finalize_failed",
    }  # fmt: skip
    assert set(EVENT_LEVELS) == expected


def test_emit_explicit_level_overrides_and_datetimes_serialise(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    log = ScheduleEventLog(tmp_path, clock=lambda: T0)
    with caplog.at_level(logging.DEBUG, logger=fs.logger.name):
        log.emit("schedule.fired", logging.ERROR, scheduled_for=T0)
    assert caplog.records[-1].levelno == logging.ERROR
    assert log.read(limit=1)[0]["scheduled_for"] == T0.isoformat()


def test_read_newest_first_filters_and_limit(tmp_path: Path) -> None:
    log = ScheduleEventLog(tmp_path, clock=lambda: T0)
    log.emit("schedule.fired", workspace=ROOT, schedule_id="a")
    log.emit("schedule.fired", workspace="/ws/beta", schedule_id="b")
    log.emit("schedule.missed", workspace=ROOT, schedule_id="a")
    assert [e["event"] for e in log.read()] == [
        "schedule.missed",
        "schedule.fired",
        "schedule.fired",
    ]
    assert len(log.read(limit=2)) == 2
    assert len(log.read(schedule_id="a")) == 2
    assert [e["schedule_id"] for e in log.read(workspace_root=ROOT)] == ["a", "a"]
    assert ScheduleEventLog(tmp_path / "empty").read() == []


def test_rotation_spans_both_files(tmp_path: Path) -> None:
    log = ScheduleEventLog(tmp_path, clock=lambda: T0, max_bytes=400)
    for i in range(12):
        log.emit("schedule.fired", workspace=ROOT, schedule_id="s", detail=f"n{i}")
    base = tmp_path / "schedules"
    assert (base / "events.jsonl.1").is_file()
    assert (base / "events.jsonl").stat().st_size < 400 + 300
    details = [e["detail"] for e in log.read()]
    assert details[0] == "n11"
    assert details == sorted(details, key=lambda d: -int(d[1:]))  # strictly newest-first
    assert "n11" in details and len(details) > 3  # spans the active file and the rollover
    assert len(list(base.glob("events.jsonl*"))) == 2  # a single .1, never .2


def test_malformed_lines_skipped_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    log = ScheduleEventLog(tmp_path, clock=lambda: T0)
    log.emit("schedule.fired", schedule_id="s")
    with open(tmp_path / "schedules/events.jsonl", "a") as fh:
        fh.write("{not json\n[1,2]\n\n")
    log.emit("schedule.missed", schedule_id="s")
    with caplog.at_level(logging.WARNING):
        events = log.read()
    assert [e["event"] for e in events] == ["schedule.missed", "schedule.fired"]
    assert "malformed" in caplog.text


def test_emit_never_raises_on_unwritable_dir(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    blocker = tmp_path / "blocked"
    blocker.write_text("a file where the state dir should be")
    log = ScheduleEventLog(blocker, clock=lambda: T0)
    with caplog.at_level(logging.INFO, logger=fs.logger.name):
        log.emit("schedule.fired", schedule_id="s")  # must return normally
    assert "schedule.fired" in caplog.text and "unwritable" in caplog.text
    assert log.read() == []


def test_emit_unserialisable_field_does_not_raise(tmp_path: Path) -> None:
    class Bad:
        def __str__(self) -> str:
            raise RuntimeError("no")

    ScheduleEventLog(tmp_path, clock=lambda: T0).emit("schedule.fired", detail=Bad())


def test_queued_scheduled_for_round_trips_and_clears(tmp_path: Path) -> None:
    st = ScheduleStateStore(tmp_path)
    st.set_queued(ROOT, "s", "kq", T0)
    s = ScheduleStateStore(tmp_path).get(ROOT, "s")
    assert s is not None and (s.queued_fire_key, s.queued_scheduled_for) == ("kq", T0)
    st.clear_queued(ROOT, "s")
    s2 = ScheduleStateStore(tmp_path).get(ROOT, "s")
    assert s2 is not None and (s2.queued_fire_key, s2.queued_scheduled_for) == (None, None)


def test_legacy_state_file_without_queued_scheduled_for_loads(tmp_path: Path) -> None:
    path = tmp_path / "schedules" / "state.json"
    path.parent.mkdir(parents=True)
    key = state_key(ROOT, "s")
    path.write_text(json.dumps({"schedules": {key: {"status": "active", "queued_fire_key": "kq"}}}))
    s = ScheduleStateStore(tmp_path).get(ROOT, "s")
    assert s is not None and (s.queued_fire_key, s.queued_scheduled_for) == ("kq", None)
