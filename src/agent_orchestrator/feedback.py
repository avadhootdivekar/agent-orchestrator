"""Local user feedback on runs/tasks (E-Us9Kd4 Part 3, `docs-md/usage-signals-hld.md`).

Storage: ``<ws>/.orchestrator/runs/<run_id>/feedback.json``. This module is the ONE validator
and ONE writer: `ao rate` and the dashboard POST both call `add_feedback`.

Write safety: the read-modify-write runs under a process-wide thread lock plus a cross-process
``flock`` on a companion lock file, and the file itself is replaced atomically (tmp +
``os.replace``) so a crash never leaves a torn file. A corrupt existing file is NEVER
overwritten -- `FeedbackError` is raised so the operator can inspect it.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import secrets
import stat
import threading
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .errors import OrchestratorError
from .models import RunState

FEEDBACK_FILE = "feedback.json"
FEEDBACK_LOCK_FILE = "feedback.lock"
SCHEMA_VERSION = 1
MAX_NOTE_CHARS = 2000
MAX_ENTRIES = 500
MAX_RUN_ID_CHARS = 128
FEEDBACK_FILE_MODE = 0o644  # as the previous default-umask file; owner-writable only
RUN_ID_RE = re.compile(rf"[A-Za-z0-9._-]{{1,{MAX_RUN_ID_CHARS}}}")  # used with fullmatch

Rating = Literal["good", "ok", "bad"]
Reason = Literal["wrong", "incomplete", "unnecessary", "too-costly", "needed-hand-fixing"]
Scope = Literal["run", "task"]
Source = Literal["cli", "dashboard"]
RATINGS: tuple[str, ...] = get_args(Rating)
REASONS: tuple[str, ...] = get_args(Reason)
SCOPES: tuple[str, ...] = get_args(Scope)
SOURCES: tuple[str, ...] = get_args(Source)

# Queued operator notes (A6): free-form guidance an operator submits to a RUNNING run. The index
# is the source of truth; the markdown is a derived, human/agent-readable view of it. The engine
# hands only the markdown's PATH to tasks dispatched later (NFR-1) -- never its content.
OPERATOR_NOTES_FILE = "operator-notes.md"
OPERATOR_NOTES_INDEX_FILE = "operator-notes.json"
MAX_OPERATOR_NOTE_CHARS = 2000
MAX_OPERATOR_NOTES = 100
OPERATOR_NOTE_ID_PREFIX = "n"

_ORCHESTRATOR_DIR = ".orchestrator"
_RUNS_DIR = "runs"
_STATE_FILE = "state.json"
_DOT_RUN_IDS = {".", ".."}


class FeedbackError(OrchestratorError):
    """Invalid feedback input, unknown run/task, cap reached, or an unreadable store."""


class FeedbackCapError(FeedbackError):
    """The run already holds MAX_ENTRIES entries; adding more is rejected (never trimmed)."""


class OperatorNoteError(FeedbackError):
    """Invalid operator note, unknown run, cap reached, or an unreadable notes store."""


class OperatorNoteCapError(OperatorNoteError, FeedbackCapError):
    """The run already holds MAX_OPERATOR_NOTES notes; adding more is rejected (never trimmed)."""


class FeedbackEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ts: str
    scope: Scope
    task_id: str | None = None
    rating: Rating
    reasons: list[Reason] = Field(default_factory=list)
    note: str | None = None
    source: Source

    @field_validator("reasons", mode="after")
    @classmethod
    def _dedupe_reasons(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))  # order-preserving dedupe

    @field_validator("note", mode="after")
    @classmethod
    def _trim_note(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if len(v) > MAX_NOTE_CHARS:
            raise ValueError(f"note exceeds {MAX_NOTE_CHARS} characters ({len(v)})")
        return v or None

    @model_validator(mode="after")
    def _check_scope(self) -> FeedbackEntry:
        if self.scope == "task" and not self.task_id:
            raise ValueError("task_id is required when scope is 'task'")
        if self.scope == "run" and self.task_id is not None:
            raise ValueError("task_id must be absent when scope is 'run'")
        return self


class FeedbackFile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: int = SCHEMA_VERSION
    entries: list[FeedbackEntry] = Field(default_factory=list)


def validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or run_id in _DOT_RUN_IDS or RUN_ID_RE.fullmatch(run_id) is None:
        raise FeedbackError(f"invalid run id {run_id!r}: must match {RUN_ID_RE.pattern}")
    return run_id


def _run_dir(ws_root: str | Path, run_id: str) -> Path:
    validate_run_id(run_id)
    runs = Path(ws_root) / _ORCHESTRATOR_DIR / _RUNS_DIR
    d = runs / run_id
    # Containment check on the RESOLVED path (a symlinked run dir must not escape the runs root).
    if d.resolve().parent != runs.resolve() or not d.is_dir():
        raise FeedbackError(f"run not found: {run_id!r}")
    return d


def feedback_path(ws_root: str | Path, run_id: str) -> Path:
    """Path of the run's feedback file; the id is validated and the run dir must exist."""
    return _run_dir(ws_root, run_id) / FEEDBACK_FILE


def _refuse_symlink(path: Path) -> None:
    """Refuse a symlinked feedback file (it could redirect reads/writes outside the run dir).
    The message names the file, never an absolute path."""
    try:
        is_link = stat.S_ISLNK(os.lstat(path).st_mode)
    except FileNotFoundError:
        return
    except OSError as e:
        raise FeedbackError(f"cannot stat {path.name}: {e.strerror or type(e).__name__}") from e
    if is_link:
        raise FeedbackError(f"refusing symlinked {path.name}")


def _read_file(path: Path) -> FeedbackFile:
    _refuse_symlink(path)
    if not path.exists():
        return FeedbackFile()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {data.get('schema_version')!r}")
        return FeedbackFile.model_validate(data)
    except (OSError, ValueError, ValidationError) as e:
        raise FeedbackError(
            f"corrupt feedback file {path} (not modified; fix or remove it): {e}"
        ) from e


def load_feedback(ws_root: str | Path, run_id: str) -> FeedbackFile:
    """Missing file => empty; corrupt file => FeedbackError."""
    return _read_file(feedback_path(ws_root, run_id))


_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


@contextmanager
def _locked(run_dir: Path) -> Iterator[None]:
    key = str(run_dir.resolve())
    with _thread_locks_guard:
        tlock = _thread_locks.setdefault(key, threading.Lock())
    with tlock:
        fd = os.open(run_dir / FEEDBACK_LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)  # closing releases the flock


def _atomic_write(path: Path, doc: BaseModel) -> None:
    _atomic_write_text(path, doc.model_dump_json(indent=2))


def _atomic_write_text(path: Path, text: str) -> None:
    _refuse_symlink(path)
    # Unique tmp name in the same directory (=> os.replace is atomic), created exclusively and
    # without following symlinks so a pre-planted link can never redirect the write.
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(tmp, flags, FEEDBACK_FILE_MODE)
    except OSError as e:
        raise FeedbackError(f"cannot create temp file for {path.name}: {e.strerror}") from e
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _known_task_ids(run_dir: Path) -> set[str]:
    p = run_dir / _STATE_FILE
    try:
        return set(RunState.model_validate_json(p.read_text(encoding="utf-8")).tasks)
    except (OSError, ValueError) as e:
        raise FeedbackError(f"cannot read run state to validate task id: {e}") from e


def _utc_now() -> datetime:
    return datetime.now(UTC)


def add_feedback(
    ws_root: str | Path,
    run_id: str,
    *,
    scope: str,
    rating: str,
    reasons: list[str] | None = None,
    note: str | None = None,
    task_id: str | None = None,
    source: str = "cli",
    now: Callable[[], datetime] = _utc_now,
) -> FeedbackEntry:
    """Validate and append one entry (history is kept). Raises FeedbackError on any problem."""
    run_dir = _run_dir(ws_root, run_id)
    try:
        entry = FeedbackEntry(
            ts=now().isoformat(),
            scope=scope,  # type: ignore[arg-type]  # validated by pydantic
            task_id=task_id,
            rating=rating,  # type: ignore[arg-type]
            reasons=reasons or [],  # type: ignore[arg-type]
            note=note,
            source=source,  # type: ignore[arg-type]
        )
    except ValidationError as e:
        msgs = "; ".join(
            f"{'.'.join(str(x) for x in err['loc']) or 'entry'}: {err['msg']}" for err in e.errors()
        )
        raise FeedbackError(f"invalid feedback: {msgs}") from e
    if entry.task_id is not None and entry.task_id not in _known_task_ids(run_dir):
        raise FeedbackError(f"unknown task {entry.task_id!r} in run {run_id!r}")

    path = run_dir / FEEDBACK_FILE
    with _locked(run_dir):
        doc = _read_file(path)  # corrupt => raises, file untouched
        if len(doc.entries) >= MAX_ENTRIES:
            raise FeedbackCapError(f"feedback entry cap reached ({MAX_ENTRIES}) for run {run_id!r}")
        doc.entries.append(entry)
        doc.schema_version = SCHEMA_VERSION
        _atomic_write(path, doc)
    return entry


def effective_ratings(
    entries: list[FeedbackEntry],
) -> dict[tuple[str, str | None], FeedbackEntry]:
    """Latest entry per (scope, task_id); later list position wins."""
    out: dict[tuple[str, str | None], FeedbackEntry] = {}
    for e in entries:
        out[(e.scope, e.task_id)] = e
    return out


def effective_for_task(
    entries: list[FeedbackEntry], task_id: str, explicit_only: bool = False
) -> FeedbackEntry | None:
    """A task's effective rating: its own task entry, else the run entry, else None.

    `explicit_only=True` returns ONLY the task-scope entry (no run-level fallback); use it
    for reviewer-error candidates, where a blanket run rating must not count."""
    eff = effective_ratings(entries)
    explicit = eff.get(("task", task_id))
    if explicit_only:
        return explicit
    return explicit or eff.get(("run", None))


@dataclass(frozen=True)
class FeedbackSummary:
    """Rollup of effective per-task ratings (the join in the usage report builds on this)."""

    good: int = 0
    ok: int = 0
    bad: int = 0
    unnecessary: int = 0
    rated_tasks: int = 0
    total_tasks: int = 0


def summarize(entries: list[FeedbackEntry], task_ids: list[str]) -> FeedbackSummary:
    counts: Counter[str] = Counter()
    unnecessary = rated = 0
    for tid in task_ids:
        e = effective_for_task(entries, tid)
        if e is None:
            continue
        rated += 1
        counts[e.rating] += 1
        if "unnecessary" in e.reasons:
            unnecessary += 1
    return FeedbackSummary(
        good=counts["good"],
        ok=counts["ok"],
        bad=counts["bad"],
        unnecessary=unnecessary,
        rated_tasks=rated,
        total_tasks=len(task_ids),
    )


# -- queued operator notes (A6) ----------------------------------------------------------------


class OperatorNote(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    ts: str
    source: Source
    text: str


class OperatorNotesFile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: int = SCHEMA_VERSION
    notes: list[OperatorNote] = Field(default_factory=list)


def operator_notes_path(ws_root: str | Path, run_id: str) -> Path:
    """Path of the run's markdown notes file (the one handed to tasks by path)."""
    return _run_dir(ws_root, run_id) / OPERATOR_NOTES_FILE


def _read_notes(path: Path) -> OperatorNotesFile:
    _refuse_symlink(path)
    if not path.exists():
        return OperatorNotesFile()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {data.get('schema_version')!r}")
        return OperatorNotesFile.model_validate(data)
    except (OSError, ValueError, ValidationError) as e:
        raise OperatorNoteError(
            f"corrupt operator notes index {path} (not modified; fix or remove it): {e}"
        ) from e


def load_operator_notes(ws_root: str | Path, run_id: str) -> OperatorNotesFile:
    """Missing index => empty; corrupt index => OperatorNoteError."""
    return _read_notes(_run_dir(ws_root, run_id) / OPERATOR_NOTES_INDEX_FILE)


def render_operator_notes_markdown(doc: OperatorNotesFile) -> str:
    """The agent-facing view. Each note is a level-2 section; text is verbatim (never parsed)."""
    parts = ["# Operator notes\n"]
    for n in doc.notes:
        parts.append(f"\n## Note {n.id} ({n.ts}, {n.source})\n\n{n.text}\n")
    return "".join(parts)


def add_operator_note(
    ws_root: str | Path,
    run_id: str,
    *,
    text: str,
    source: str = "cli",
    now: Callable[[], datetime] = _utc_now,
) -> OperatorNote:
    """Validate and append one note (append-only). Raises OperatorNoteError on any problem.

    The text is stripped; empty, NUL-containing or over-long text is rejected (never trimmed), as
    is the (MAX_OPERATOR_NOTES+1)th note. Index and markdown are both replaced atomically under
    the run's lock; the index goes first so a crash can only leave the (derived) markdown stale.
    """
    run_dir = _run_dir(ws_root, run_id)
    if not isinstance(text, str):
        raise OperatorNoteError("invalid note: text must be a string")
    text = text.strip()
    if not text:
        raise OperatorNoteError("invalid note: text is empty")
    if len(text) > MAX_OPERATOR_NOTE_CHARS:
        raise OperatorNoteError(
            f"invalid note: text exceeds {MAX_OPERATOR_NOTE_CHARS} characters ({len(text)})"
        )
    if "\x00" in text:
        raise OperatorNoteError("invalid note: text contains a NUL character")
    if source not in SOURCES:
        raise OperatorNoteError(f"invalid note: source must be one of {SOURCES}")

    index_path = run_dir / OPERATOR_NOTES_INDEX_FILE
    md_path = run_dir / OPERATOR_NOTES_FILE
    with _locked(run_dir):
        doc = _read_notes(index_path)  # corrupt => raises, file untouched
        if len(doc.notes) >= MAX_OPERATOR_NOTES:
            raise OperatorNoteCapError(
                f"operator note cap reached ({MAX_OPERATOR_NOTES}) for run {run_id!r}"
            )
        note = OperatorNote(
            id=f"{OPERATOR_NOTE_ID_PREFIX}{len(doc.notes) + 1}",
            ts=now().isoformat(),
            source=source,  # type: ignore[arg-type]  # checked above
            text=text,
        )
        doc.notes.append(note)
        doc.schema_version = SCHEMA_VERSION
        _atomic_write(index_path, doc)
        _atomic_write_text(md_path, render_operator_notes_markdown(doc))
    return note
