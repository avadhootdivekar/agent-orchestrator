"""ScheduleStore: round-trip, missing/corrupt files, and locked read-modify-write."""

from __future__ import annotations

import multiprocessing
from collections.abc import Callable
from pathlib import Path

import pytest

from agent_orchestrator.schedules import (
    ScheduleBinding,
    ScheduleFile,
    ScheduleFileError,
    ScheduleStore,
)


def _binding(sid: str) -> ScheduleBinding:
    return ScheduleBinding(id=sid, kind="cron", schedule="0 * * * *", workflow="w.json")


def _add(sid: str) -> Callable[[ScheduleFile], ScheduleFile]:
    def fn(f: ScheduleFile) -> ScheduleFile:
        return ScheduleFile(
            version=f.version, defaults=f.defaults, schedules=[*f.schedules, _binding(sid)]
        )

    return fn


def _worker(root: str, sid: str) -> None:
    ScheduleStore(root).mutate(_add(sid))


def test_path(tmp_path: Path) -> None:
    assert ScheduleStore(tmp_path).path == tmp_path / ".ao" / "schedules.yaml"


def test_missing_file_is_empty(tmp_path: Path) -> None:
    assert ScheduleStore(tmp_path).load() == ScheduleFile()


def test_save_load_roundtrip_and_no_tmp_left(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path)
    f = ScheduleFile(schedules=[_binding("a")])
    store.save(f)
    assert store.load() == f
    assert [p.name for p in store.path.parent.iterdir()] == ["schedules.yaml"]


def test_corrupt_yaml_named_error(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path)
    store.path.parent.mkdir()
    store.path.write_text("schedules: [unclosed")
    with pytest.raises(ScheduleFileError, match="schedules.yaml"):
        store.load()


def test_non_mapping_and_invalid_schema_named_error(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path)
    store.path.parent.mkdir()
    store.path.write_text("- a\n- b\n")
    with pytest.raises(ScheduleFileError, match="mapping"):
        store.load()
    store.path.write_text("schedules:\n- {id: x, kind: cron, schedule: 'bad bad', workflow: w}\n")
    with pytest.raises(ScheduleFileError) as ei:
        store.load()
    assert "schedules.yaml" in str(ei.value) and "'x'" in str(ei.value)


def test_empty_file_is_empty_schedule_file(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path)
    store.path.parent.mkdir()
    store.path.write_text("")
    assert store.load() == ScheduleFile()


def test_mutate_rereads_under_lock(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path)
    store.mutate(_add("one"))
    stale = store.load()
    store.mutate(_add("two"))
    out = store.mutate(_add("three"))
    assert [b.id for b in out.schedules] == ["one", "two", "three"]
    assert [b.id for b in stale.schedules] == ["one"]
    assert store.path.with_name("schedules.yaml.lock").exists()


def test_mutate_failure_leaves_file_untouched(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path)
    store.mutate(_add("one"))

    def boom(f: ScheduleFile) -> ScheduleFile:
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError):
        store.mutate(boom)
    assert [b.id for b in store.load().schedules] == ["one"]
    # lock released: another mutate still works
    store.mutate(_add("two"))


def test_concurrent_mutate_loses_nothing(tmp_path: Path) -> None:
    ids = [f"s{i}" for i in range(8)]
    ctx = multiprocessing.get_context("fork")
    procs = [ctx.Process(target=_worker, args=(str(tmp_path), i)) for i in ids]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
        assert p.exitcode == 0
    assert sorted(b.id for b in ScheduleStore(tmp_path).load().schedules) == sorted(ids)


def test_save_rejects_invalid_model_and_keeps_file(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path)
    row = {"id": "a", "kind": "cron", "schedule": "0 2 * * *", "workflow": "w"}
    store.save(ScheduleFile.from_data({"schedules": [row]}))
    before = store.path.read_text()

    def add_dup(cur: ScheduleFile) -> ScheduleFile:
        cur.schedules.append(cur.schedules[0].model_copy())
        return cur

    with pytest.raises(ScheduleFileError, match="duplicate schedule id 'a'"):
        store.mutate(add_dup)
    assert store.path.read_text() == before
    assert len(store.load().schedules) == 1  # still loadable -> remove/disable can repair
