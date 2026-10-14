"""Queued operator notes (A6): store, prompt delivery, result-cache key, engine integration.

A note is free-form guidance for a RUNNING run. It reaches only tasks dispatched after it was
submitted, by PATH (NFR-1); nothing can reach a task that is already running.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.executors.prompt import build_prompt
from agent_orchestrator.feedback import (
    MAX_OPERATOR_NOTE_CHARS,
    MAX_OPERATOR_NOTES,
    OPERATOR_NOTES_FILE,
    OPERATOR_NOTES_INDEX_FILE,
    FeedbackCapError,
    FeedbackError,
    OperatorNoteCapError,
    OperatorNoteError,
    add_operator_note,
    load_operator_notes,
    operator_notes_path,
)
from agent_orchestrator.models import (
    AgentSpec,
    RepoRef,
    RepoSet,
    TaskContext,
    TaskSpec,
    WorkflowSpec,
)
from agent_orchestrator.runstate import RunStateStore
from tests.cache.keys_fixture import key_for, write_files

RUN = "wf-20260101T000000Z"


def _clock() -> datetime:
    return datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


@pytest.fixture
def ws(tmp_path: Path) -> str:
    (tmp_path / ".orchestrator" / "runs" / RUN).mkdir(parents=True)
    return str(tmp_path)


def _add(ws: str, text: str = "prefer small diffs", **kw):  # type: ignore[no-untyped-def]
    return add_operator_note(ws, RUN, text=text, now=_clock, **kw)


class TestStore:
    def test_append_writes_index_and_markdown(self, ws: str) -> None:
        n1 = _add(ws, "  first  ")
        n2 = _add(ws, "second", source="dashboard")
        assert (n1.id, n1.text, n1.source, n1.ts) == ("n1", "first", "cli", _clock().isoformat())
        assert (n2.id, n2.source) == ("n2", "dashboard")
        assert [n.text for n in load_operator_notes(ws, RUN).notes] == ["first", "second"]
        md = operator_notes_path(ws, RUN).read_text(encoding="utf-8")
        assert md.index("first") < md.index("second") and "## Note n2" in md

    def test_missing_index_is_empty(self, ws: str) -> None:
        assert load_operator_notes(ws, RUN).notes == []

    @pytest.mark.parametrize("text", ["", "   \n\t", "a\x00b"])
    def test_rejects_empty_or_nul(self, ws: str, text: str) -> None:
        with pytest.raises(OperatorNoteError):
            _add(ws, text)
        assert not (Path(ws) / ".orchestrator/runs" / RUN / OPERATOR_NOTES_FILE).exists()

    def test_oversize_is_rejected_not_trimmed(self, ws: str) -> None:
        _add(ws, "x" * MAX_OPERATOR_NOTE_CHARS)  # exactly at the cap is fine
        with pytest.raises(OperatorNoteError, match="exceeds"):
            _add(ws, "x" * (MAX_OPERATOR_NOTE_CHARS + 1))
        assert len(load_operator_notes(ws, RUN).notes) == 1

    def test_bad_source_rejected(self, ws: str) -> None:
        with pytest.raises(OperatorNoteError, match="source"):
            _add(ws, source="attacker")

    def test_cap_rejects_the_next_note(self, ws: str) -> None:
        for i in range(MAX_OPERATOR_NOTES):
            _add(ws, f"n{i}")
        with pytest.raises(OperatorNoteCapError) as ei:
            _add(ws, "one too many")
        assert isinstance(ei.value, FeedbackCapError)  # the dashboard maps this to 409
        assert len(load_operator_notes(ws, RUN).notes) == MAX_OPERATOR_NOTES

    @pytest.mark.parametrize("run_id", ["..", ".", "a/b", "", "x" * 200, "../etc"])
    def test_bad_run_id_rejected(self, ws: str, run_id: str) -> None:
        with pytest.raises(FeedbackError):
            add_operator_note(ws, run_id, text="hi")

    def test_unknown_run_is_not_found(self, ws: str) -> None:
        with pytest.raises(FeedbackError, match="run not found"):
            add_operator_note(ws, "no-such-run", text="hi")

    def test_symlinked_index_refused(self, ws: str, tmp_path: Path) -> None:
        run_dir = Path(ws) / ".orchestrator/runs" / RUN
        target = tmp_path / "elsewhere.json"
        target.write_text("{}")
        (run_dir / OPERATOR_NOTES_INDEX_FILE).symlink_to(target)
        with pytest.raises(FeedbackError, match="symlink"):
            _add(ws)
        assert target.read_text() == "{}"

    def test_symlinked_markdown_refused(self, ws: str, tmp_path: Path) -> None:
        run_dir = Path(ws) / ".orchestrator/runs" / RUN
        target = tmp_path / "victim.md"
        target.write_text("keep")
        (run_dir / OPERATOR_NOTES_FILE).symlink_to(target)
        with pytest.raises(FeedbackError, match="symlink"):
            _add(ws)
        assert target.read_text() == "keep"

    def test_corrupt_index_is_never_overwritten(self, ws: str) -> None:
        idx = Path(ws) / ".orchestrator/runs" / RUN / OPERATOR_NOTES_INDEX_FILE
        idx.write_text("{not json")
        with pytest.raises(OperatorNoteError, match="corrupt"):
            _add(ws)
        assert idx.read_text() == "{not json"

    def test_concurrent_appends_keep_every_note(self, ws: str) -> None:
        threads = [threading.Thread(target=_add, args=(ws, f"note {i}")) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        doc = load_operator_notes(ws, RUN)
        assert sorted(n.id for n in doc.notes) == sorted(f"n{i}" for i in range(1, 21))
        assert len({n.text for n in doc.notes}) == 20

    def test_text_is_stored_verbatim_never_interpreted(self, ws: str) -> None:
        evil = "<script>alert(1)</script> {instruction} {0} ${HOME} $(rm -rf /)"
        _add(ws, evil)
        raw = json.loads(
            (Path(ws) / ".orchestrator/runs" / RUN / OPERATOR_NOTES_INDEX_FILE).read_text()
        )
        assert raw["notes"][0]["text"] == evil


def _ctx(notes: str | None) -> TaskContext:
    return TaskContext(
        run_id="run-1",
        task_id="t1",
        agent=AgentSpec(executor="fake"),
        instruction_path="/ws/instr.md",
        operator_notes_path=notes,
        input_paths=["/ws/in.md"],
        output_paths=["/ws/out.md"],
        repo_paths={"main": "/ws/repo"},
        timeout_seconds=60,
    )


class TestPrompt:
    def test_no_notes_prompt_is_unchanged(self) -> None:
        assert build_prompt(_ctx(None)) == build_prompt(_ctx(None).model_copy(update={}))
        assert "operator" not in build_prompt(_ctx(None)).lower()

    def test_notes_clause_appended_once_with_path_only(self, tmp_path: Path) -> None:
        notes = tmp_path / "operator-notes.md"
        notes.write_text("SECRET NOTE CONTENT")
        prompt = build_prompt(_ctx(str(notes)))
        assert prompt.startswith(build_prompt(_ctx(None)))
        assert prompt.count(str(notes)) == 1
        assert "SECRET NOTE CONTENT" not in prompt  # NFR-1: a path, never the content
        assert "does not conflict" in prompt  # advisory, not an override


class TestCacheKey:
    def test_no_notes_key_is_unchanged(self, tmp_path: Path) -> None:
        write_files(tmp_path)
        assert key_for(tmp_path).key == key_for(tmp_path, operator_notes_path=None).key
        assert "operator_notes" not in key_for(tmp_path).components

    def test_notes_change_the_key_and_content_matters(self, tmp_path: Path) -> None:
        write_files(tmp_path)
        notes = tmp_path / ".orchestrator" / "runs" / RUN / OPERATOR_NOTES_FILE
        notes.parent.mkdir(parents=True)
        base = key_for(tmp_path).key
        notes.write_text("be terse")
        k1 = key_for(tmp_path, operator_notes_path=str(notes))
        assert k1.key != base and "operator_notes" in k1.components
        notes.write_text("be terse, and add tests")  # a later note => a different key
        k2 = key_for(tmp_path, operator_notes_path=str(notes))
        assert k2.key != k1.key
        notes.write_text("be terse")  # same content => same key (path/run id is normalized away)
        assert key_for(tmp_path, operator_notes_path=str(notes)).key == k1.key

    def test_notes_outside_workspace_make_the_task_uncacheable(self, tmp_path: Path) -> None:
        from agent_orchestrator.cache.types import UncacheableError

        write_files(tmp_path)
        outside = tmp_path.parent / "outside-notes.md"
        outside.write_text("x")
        with pytest.raises(UncacheableError):
            key_for(tmp_path, operator_notes_path=str(outside))


class _NoteOnFirst(FakeExecutor):
    """Submits a note while task 'first' is running (as the dashboard would)."""

    def __init__(self, ws: str, text: str = "use the staging db") -> None:
        super().__init__()
        self._ws, self._text = ws, text
        self.notes_file_seen: dict[str, bool] = {}

    def execute(self, ctx: TaskContext):  # type: ignore[no-untyped-def]
        if ctx.task_id == "first":
            add_operator_note(self._ws, ctx.run_id, text=self._text, source="dashboard")
        self.notes_file_seen[ctx.task_id] = ctx.operator_notes_path is not None
        return super().execute(ctx)


@pytest.fixture()
def wf(tmp_path: Path):
    (tmp_path / "instr.md").write_text("do the thing", encoding="utf-8")
    workflow = WorkflowSpec(
        version="1.0",
        id="notes-demo",
        repo_set="main",
        tasks=[
            TaskSpec(id="first", agent="dev", instruction="instr.md", outputs=["o1.md"]),
            TaskSpec(
                id="second",
                agent="dev",
                instruction="instr.md",
                outputs=["o2.md"],
                depends_on=["first"],
            ),
        ],
    )
    reposets = {
        "main": RepoSet(
            repos=[RepoRef(id="repo", path=str(tmp_path))], workspace_root=str(tmp_path)
        )
    }
    return tmp_path, workflow, reposets, {"dev": AgentSpec(executor="fake")}


def _run(executor: FakeExecutor, tmp_path: Path, workflow, reposets, agents):  # type: ignore[no-untyped-def]
    store = LocalFsArtifactStore(str(tmp_path))
    return Orchestrator(executor, store, RunStateStore(str(tmp_path), store)).run(
        workflow, reposets, agents
    )


class TestEngine:
    def test_note_reaches_later_tasks_only(self, wf) -> None:  # type: ignore[no-untyped-def]
        tmp_path, workflow, reposets, agents = wf
        ex = _NoteOnFirst(str(tmp_path))
        state = _run(ex, tmp_path, workflow, reposets, agents)
        assert state.status == "succeeded"
        notes = str(tmp_path / ".orchestrator" / "runs" / state.run_id / OPERATOR_NOTES_FILE)
        # 'first' was already dispatched when the note arrived: its prompt cannot carry it.
        assert notes not in ex.prompts["first"]
        assert ex.prompts["second"].endswith(
            f" The operator left guidance for this run in {notes}; read it before starting and "
            "follow it where it does not conflict with your instructions."
        )
        assert "use the staging db" not in ex.prompts["second"]  # path only, never content
        assert os.path.isfile(notes)

    def test_run_without_notes_has_no_clause(self, wf) -> None:  # type: ignore[no-untyped-def]
        tmp_path, workflow, reposets, agents = wf
        ex = FakeExecutor()
        _run(ex, tmp_path, workflow, reposets, agents)
        assert all("operator" not in p.lower() for p in ex.prompts.values())
