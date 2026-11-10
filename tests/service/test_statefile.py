"""service.statefile: atomic JSON model read/write and corrupt-file-starts-clean."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from pydantic import BaseModel

from agent_orchestrator.service.statefile import read_json_model, write_json_model_atomic


class Sample(BaseModel):
    n: int = 0
    tags: list[str] = []


def test_missing_file_gives_default(tmp_path: Path) -> None:
    assert read_json_model(tmp_path / "x.json", Sample, default_factory=lambda: Sample(n=7)).n == 7


def test_roundtrip_creates_parents_and_leaves_no_tmp(tmp_path: Path) -> None:
    p = tmp_path / "a" / "b" / "s.json"
    write_json_model_atomic(p, Sample(n=3, tags=["x"]))
    assert read_json_model(p, Sample, default_factory=Sample) == Sample(n=3, tags=["x"])
    assert [f.name for f in p.parent.iterdir()] == ["s.json"]


@pytest.mark.parametrize("content", ["{not json", '{"n": "str"}', ""])
def test_corrupt_starts_clean_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, content: str
) -> None:
    p = tmp_path / "s.json"
    p.write_text(content)
    with caplog.at_level(logging.WARNING):
        got = read_json_model(p, Sample, default_factory=lambda: Sample(n=-1))
    assert got.n == -1
    assert "corrupt" in caplog.text


def test_unreadable_path_starts_clean(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    d = tmp_path / "dir.json"
    d.mkdir()  # reading a directory raises OSError
    with caplog.at_level(logging.WARNING):
        assert read_json_model(d, Sample, default_factory=lambda: Sample(n=9)).n == 9
    assert "unreadable" in caplog.text


def test_failed_write_cleans_tmp_and_keeps_old(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "s.json"
    write_json_model_atomic(p, Sample(n=1))

    def boom(*a: object, **k: object) -> None:
        raise OSError("disk")

    monkeypatch.setattr("agent_orchestrator.service.statefile.os.replace", boom)
    with pytest.raises(OSError):
        write_json_model_atomic(p, Sample(n=2))
    assert read_json_model(p, Sample, default_factory=Sample).n == 1
    assert [f.name for f in tmp_path.iterdir()] == ["s.json"]


def test_default_mode_does_not_fsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr("agent_orchestrator.service.statefile.os.fsync", calls.append)
    write_json_model_atomic(tmp_path / "s.json", Sample(n=1))
    assert calls == []


def test_durable_fsyncs_file_and_parent_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import stat

    kinds: list[str] = []
    real_fsync = os.fsync

    def spy(fd: int) -> None:
        kinds.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        real_fsync(fd)

    monkeypatch.setattr("agent_orchestrator.service.statefile.os.fsync", spy)
    p = tmp_path / "s.json"
    write_json_model_atomic(p, Sample(n=5), durable=True)
    assert kinds == ["file", "dir"]  # file before rename, directory after
    assert read_json_model(p, Sample, default_factory=Sample).n == 5
    assert [f.name for f in tmp_path.iterdir()] == ["s.json"]


def test_durable_failure_cleans_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(fd: int) -> None:
        raise OSError("fsync")

    monkeypatch.setattr("agent_orchestrator.service.statefile.os.fsync", boom)
    with pytest.raises(OSError):
        write_json_model_atomic(tmp_path / "s.json", Sample(n=1), durable=True)
    assert list(tmp_path.iterdir()) == []


def test_durable_dir_fsync_failure_is_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """N3: an EIO from the directory fsync must not be swallowed silently."""
    import stat

    real_fsync = os.fsync

    def flaky(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(5, "EIO")
        real_fsync(fd)

    monkeypatch.setattr("agent_orchestrator.service.statefile.os.fsync", flaky)
    p = tmp_path / "s.json"
    with caplog.at_level(logging.WARNING, logger="agent_orchestrator.service.statefile"):
        write_json_model_atomic(p, Sample(n=7), durable=True)
    warnings = [r for r in caplog.records if "not be durable" in r.getMessage()]
    assert len(warnings) == 1 and str(tmp_path) in warnings[0].getMessage()
    assert read_json_model(p, Sample, default_factory=Sample).n == 7  # file itself is in place


def test_tmp_names_are_unique_per_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    names: list[str] = []
    real_replace = os.replace

    def spy(src: object, dst: object) -> None:
        names.append(Path(str(src)).name)
        real_replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr("agent_orchestrator.service.statefile.os.replace", spy)
    p = tmp_path / "s.json"
    for i in range(5):
        write_json_model_atomic(p, Sample(n=i))
    assert len(set(names)) == 5  # not pid-based: same process, distinct tmp names


def test_concurrent_writers_do_not_collide(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    p = tmp_path / "s.json"

    def work(i: int) -> None:
        write_json_model_atomic(p, Sample(n=i), durable=bool(i % 2))

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(work, range(64)))  # any collision would raise (FileNotFoundError etc.)
    assert 0 <= read_json_model(p, Sample, default_factory=Sample).n < 64
    assert [f.name for f in tmp_path.iterdir()] == ["s.json"]


def test_existing_file_mode_preserved(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    write_json_model_atomic(p, Sample(n=1))
    p.chmod(0o640)
    write_json_model_atomic(p, Sample(n=2))
    assert p.stat().st_mode & 0o777 == 0o640
