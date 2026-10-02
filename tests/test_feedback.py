"""Unit tests for feedback.py (E-Us9Kd4 T-Fb3Ef4)."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_orchestrator import feedback as fb
from agent_orchestrator.feedback import (
    MAX_ENTRIES,
    MAX_NOTE_CHARS,
    FeedbackCapError,
    FeedbackError,
    add_feedback,
    effective_for_task,
    effective_ratings,
    feedback_path,
    load_feedback,
    summarize,
    validate_run_id,
)
from agent_orchestrator.models import RunState, TaskRunState

RUN = "wf-20260101T000000Z"


def _clock() -> datetime:
    return datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


@pytest.fixture
def ws(tmp_path: Path) -> str:
    run_dir = tmp_path / ".orchestrator" / "runs" / RUN
    run_dir.mkdir(parents=True)
    st = RunState(
        run_id=RUN,
        workflow_id="wf",
        repo_set="r",
        started_at="t",
        updated_at="t",
        tasks={"a": TaskRunState(), "b": TaskRunState()},
    )
    (run_dir / "state.json").write_text(st.model_dump_json())
    return str(tmp_path)


def _add(ws: str, **kw):  # type: ignore[no-untyped-def]
    args = {"scope": "run", "rating": "good", "now": _clock}
    args.update(kw)
    return add_feedback(ws, RUN, **args)


def test_add_and_load_roundtrip(ws: str) -> None:
    e = _add(ws, note="  nice  ")
    assert e.ts == "2026-01-02T03:04:05+00:00" and e.note == "nice" and e.source == "cli"
    doc = load_feedback(ws, RUN)
    assert doc.schema_version == 1 and doc.entries == [e]


def test_missing_file_is_empty(ws: str) -> None:
    assert load_feedback(ws, RUN).entries == []


def test_reasons_deduped_and_validated(ws: str) -> None:
    e = _add(ws, rating="bad", reasons=["wrong", "wrong", "incomplete"])
    assert e.reasons == ["wrong", "incomplete"]
    with pytest.raises(FeedbackError, match="reasons"):
        _add(ws, reasons=["nope"])


@pytest.mark.parametrize(
    "kw",
    [
        {"rating": "great"},
        {"scope": "galaxy"},
        {"source": "email"},
        {"scope": "task"},  # task id missing
        {"scope": "run", "task_id": "a"},  # task id on run scope
    ],
)
def test_invalid_inputs_rejected(ws: str, kw: dict) -> None:
    with pytest.raises(FeedbackError):
        _add(ws, **kw)
    assert not feedback_path(ws, RUN).exists()


def test_oversize_note_rejected_and_exact_limit_ok(ws: str) -> None:
    _add(ws, note="x" * MAX_NOTE_CHARS)
    with pytest.raises(FeedbackError, match="note exceeds"):
        _add(ws, note="x" * (MAX_NOTE_CHARS + 1))


def test_unknown_task_rejected(ws: str) -> None:
    assert _add(ws, scope="task", task_id="a").task_id == "a"
    with pytest.raises(FeedbackError, match="unknown task"):
        _add(ws, scope="task", task_id="zzz")


@pytest.mark.parametrize("bad", ["../x", "a/b", "", ".", "..", "a b", "x" * 129, "..\\x"])
def test_bad_run_ids_rejected(ws: str, bad: str) -> None:
    with pytest.raises(FeedbackError):
        validate_run_id(bad)
    with pytest.raises(FeedbackError):
        add_feedback(ws, bad, scope="run", rating="ok")


def test_unknown_run_rejected(ws: str) -> None:
    with pytest.raises(FeedbackError, match="run not found"):
        add_feedback(ws, "nope", scope="run", rating="ok")


def test_symlinked_run_dir_escape_rejected(ws: str, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (Path(ws) / ".orchestrator" / "runs" / "evil").symlink_to(outside)
    with pytest.raises(FeedbackError):
        feedback_path(ws, "evil")


def test_entry_cap(ws: str) -> None:
    p = feedback_path(ws, RUN)
    entry = {"ts": "t", "scope": "run", "rating": "ok", "source": "cli"}
    p.write_text(json.dumps({"schema_version": 1, "entries": [entry] * MAX_ENTRIES}))
    with pytest.raises(FeedbackCapError, match="cap"):
        _add(ws)
    assert len(load_feedback(ws, RUN).entries) == MAX_ENTRIES


def test_corrupt_file_refused_and_untouched(ws: str) -> None:
    p = feedback_path(ws, RUN)
    p.write_text("{not json")
    for fn in (lambda: load_feedback(ws, RUN), lambda: _add(ws)):
        with pytest.raises(FeedbackError, match="corrupt"):
            fn()
    assert p.read_text() == "{not json"


def test_unknown_schema_version_refused(ws: str) -> None:
    feedback_path(ws, RUN).write_text('{"schema_version": 99, "entries": []}')
    with pytest.raises(FeedbackError, match="schema_version"):
        _add(ws)


def test_crash_between_tmp_write_and_replace_keeps_old_file(
    ws: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add(ws, note="first")
    before = feedback_path(ws, RUN).read_text()

    def boom(*a: object, **k: object) -> None:
        raise OSError("simulated crash")

    monkeypatch.setattr(fb.os, "replace", boom)
    with pytest.raises(OSError):
        _add(ws, note="second")
    monkeypatch.undo()
    assert feedback_path(ws, RUN).read_text() == before
    assert [e.note for e in load_feedback(ws, RUN).entries] == ["first"]
    _add(ws, note="third")  # stale .tmp does not wedge later writes
    assert len(load_feedback(ws, RUN).entries) == 2


def test_concurrent_threads_keep_every_entry(ws: str) -> None:
    n = 25
    errs: list[BaseException] = []

    def work(i: int) -> None:
        try:
            _add(ws, note=f"n{i}")
        except BaseException as e:  # pragma: no cover
            errs.append(e)

    ts = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs
    assert sorted(e.note or "" for e in load_feedback(ws, RUN).entries) == sorted(
        f"n{i}" for i in range(n)
    )


def test_concurrent_processes_keep_every_entry(ws: str) -> None:
    code = (
        "import sys;from agent_orchestrator.feedback import add_feedback;"
        "add_feedback(sys.argv[1], sys.argv[2], scope='run', rating='ok', note=sys.argv[3])"
    )
    procs = [subprocess.Popen([sys.executable, "-c", code, ws, RUN, f"p{i}"]) for i in range(6)]
    assert all(p.wait(timeout=60) == 0 for p in procs)
    assert len(load_feedback(ws, RUN).entries) == 6


def test_latest_wins_and_run_vs_task_effective(ws: str) -> None:
    _add(ws, rating="bad")
    _add(ws, rating="good")  # run-level latest
    _add(ws, scope="task", task_id="a", rating="bad", reasons=["unnecessary"])
    _add(ws, scope="task", task_id="a", rating="ok")
    entries = load_feedback(ws, RUN).entries
    eff = effective_ratings(entries)
    assert eff[("run", None)].rating == "good"
    assert eff[("task", "a")].rating == "ok"
    assert effective_for_task(entries, "a").rating == "ok"  # type: ignore[union-attr]
    assert effective_for_task(entries, "b").rating == "good"  # type: ignore[union-attr]
    assert effective_for_task(entries, "b", explicit_only=True) is None
    assert effective_for_task(entries, "a", explicit_only=True).rating == "ok"  # type: ignore[union-attr]
    assert effective_for_task([], "a") is None


def test_summarize(ws: str) -> None:
    _add(ws, rating="good")
    _add(ws, scope="task", task_id="a", rating="bad", reasons=["unnecessary"])
    s = summarize(load_feedback(ws, RUN).entries, ["a", "b"])
    assert (s.good, s.bad, s.ok, s.unnecessary, s.rated_tasks, s.total_tasks) == (1, 1, 0, 1, 2, 2)
    assert summarize([], ["a"]).rated_tasks == 0
