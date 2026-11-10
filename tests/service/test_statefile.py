"""service.statefile: atomic JSON model read/write and corrupt-file-starts-clean."""

from __future__ import annotations

import logging
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
