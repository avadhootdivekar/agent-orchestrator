"""Awaiting-input requests: the ONE validator and ONE writer shared by `ao answer` and the engine
(docs-md/awaiting-input-hld.md §5.1).

Storage: ``<run_dir>/inputs/<request_id>.json`` holds the full request including its answer and is
the single source of truth; ``RunState.input_requests`` mirrors it. Only this module writes those
files (modelled on ``feedback.py``): a process lock + ``flock`` sidecar, tmp + ``os.replace``,
symlink refusal, size caps, and a corrupt file is never overwritten. A task can never answer its
own request -- answers only enter through `answer_request`/`assume_defaults`, called by the CLI
(or, later, the HTTP layer), never from task output paths.
"""

from __future__ import annotations

import json
import os
import re
import stat
import threading
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .errors import OrchestratorError
from .fsutil import FileLock, atomic_write_bytes
from .models import (
    INPUT_MAX_FILE_BYTES,
    INPUT_REQUEST_ID_RE,
    InputAnswer,
    InputField,
    InputRequest,
    InputRequestSpec,
    InputSource,
)

INPUTS_DIR = "inputs"
INPUT_LOCK_FILE = ".inputs.lock"
INPUT_LOCK_TIMEOUT_SECONDS = 10.0
INPUT_REQUEST_FILENAME = "input-request.json"  # written by a task into its output dir
INPUT_MAX_REQUESTS_PER_TASK = 5  # runaway guard: a task re-asking forever fails the task
INPUT_ANSWER_FILENAME = "answer.json"  # engine-written answer for a spec-level `input:` task
BOOL_TRUE = ("true", "yes", "y", "1", "on")
BOOL_FALSE = ("false", "no", "n", "0", "off")
# Strip the producer-controlled keys: the engine alone generates ids/state/answers.
_ENGINE_OWNED_KEYS = ("request_id", "state", "created_at", "answered_at", "answer", "task_id")
_TASK_ID_SAFE_RE = re.compile(r"[^a-z0-9._-]")


class InputError(OrchestratorError):
    """Unknown request, invalid value, request not open, conflicting re-answer, bad file."""


class InputUnknownError(InputError):
    """No such request file."""


class InputNotOpenError(InputError):
    """The request is already answered/assumed/cancelled (CLI exit code 3)."""


_thread_lock = threading.Lock()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def inputs_dir(run_dir: str | Path) -> Path:
    return Path(run_dir) / INPUTS_DIR


def request_path(run_dir: str | Path, request_id: str) -> Path:
    if INPUT_REQUEST_ID_RE.fullmatch(request_id) is None:
        raise InputError(f"invalid request id {request_id!r}")
    return inputs_dir(run_dir) / f"{request_id}.json"


def make_request_id(task_id: str, n: int) -> str:
    """Engine-generated id ``q-<task>-<n>``; the task id is sanitised, never trusted raw."""
    safe = _TASK_ID_SAFE_RE.sub("-", task_id.lower()).strip(".-_") or "task"
    return f"q-{safe}-{n}"[:64]


def _refuse_symlink(path: Path) -> None:
    try:
        is_link = stat.S_ISLNK(os.lstat(path).st_mode)
    except FileNotFoundError:
        return
    except OSError as e:
        raise InputError(f"cannot stat {path.name}: {e.strerror or type(e).__name__}") from e
    if is_link:
        raise InputError(f"refusing symlinked {path.name}")


def _read_request_file(path: Path) -> InputRequest:
    _refuse_symlink(path)
    try:
        if path.stat().st_size > INPUT_MAX_FILE_BYTES:
            raise InputError(f"{path.name} exceeds {INPUT_MAX_FILE_BYTES} bytes")
        return InputRequest.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        raise
    except (OSError, ValueError, ValidationError) as e:
        raise InputError(f"corrupt input request {path.name} (not modified): {e}") from e


def _write_request(path: Path, req: InputRequest) -> None:
    _refuse_symlink(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, req.model_dump_json(indent=2).encode("utf-8"), mode=0o644)


class _Locked:
    """Process-wide thread lock + cross-process flock on a sidecar in the inputs dir."""

    def __init__(self, run_dir: str | Path) -> None:
        d = inputs_dir(run_dir)
        d.mkdir(parents=True, exist_ok=True)
        self._flock = FileLock(d / INPUT_LOCK_FILE, timeout=INPUT_LOCK_TIMEOUT_SECONDS)

    def __enter__(self) -> None:
        _thread_lock.acquire()
        try:
            self._flock.__enter__()
        except BaseException:
            _thread_lock.release()
            raise

    def __exit__(self, *exc: Any) -> None:
        try:
            self._flock.__exit__(*exc)
        finally:
            _thread_lock.release()


def load_request(run_dir: str | Path, request_id: str) -> InputRequest:
    try:
        return _read_request_file(request_path(run_dir, request_id))
    except FileNotFoundError:
        raise InputUnknownError(f"unknown input request {request_id!r}") from None


def list_requests(
    run_dir: str | Path, *, state: str | None = None, task_id: str | None = None
) -> list[InputRequest]:
    """All requests of the run (sorted by id), optionally filtered by state/task."""
    d = inputs_dir(run_dir)
    if not d.is_dir():
        return []
    out: list[InputRequest] = []
    for p in sorted(d.glob("*.json")):
        try:
            req = _read_request_file(p)
        except FileNotFoundError:  # raced a removal
            continue
        if (state is None or req.state == state) and (task_id is None or req.task_id == task_id):
            out.append(req)
    return out


def parse_request_spec(raw: bytes | str) -> InputRequestSpec:
    """Validate a task-written ``input-request.json`` body (untrusted) into a spec.

    Engine-owned keys a producer might include (ids/state/answer) are dropped, never trusted."""
    data = raw if isinstance(raw, bytes) else raw.encode("utf-8")
    if len(data) > INPUT_MAX_FILE_BYTES:
        raise InputError(f"input request exceeds {INPUT_MAX_FILE_BYTES} bytes")
    try:
        doc = json.loads(data)
        if not isinstance(doc, dict):
            raise ValueError("top level must be a JSON object")
        for k in _ENGINE_OWNED_KEYS:
            doc.pop(k, None)
        doc.pop("schema_version", None)
        return InputRequestSpec.model_validate(doc)
    except (ValueError, ValidationError) as e:
        raise InputError(f"invalid input-request: {e}") from e


def read_task_request_file(path: str | Path) -> InputRequestSpec | None:
    """Read a task's ``input-request.json``; ``None`` if absent, InputError if invalid."""
    p = Path(path)
    _refuse_symlink(p)
    try:
        if p.stat().st_size > INPUT_MAX_FILE_BYTES:
            raise InputError(f"input-request.json exceeds {INPUT_MAX_FILE_BYTES} bytes")
        raw = p.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as e:
        raise InputError(f"cannot read input-request.json: {e.strerror}") from e
    return parse_request_spec(raw)


def next_request_id(run_dir: str | Path, task_id: str) -> str:
    n = 1
    while request_path(run_dir, make_request_id(task_id, n)).exists():
        n += 1
    return make_request_id(task_id, n)


def open_request(
    run_dir: str | Path,
    task_id: str,
    spec: InputRequestSpec,
    *,
    now: Callable[[], datetime] = _utc_now,
) -> InputRequest:
    """Create the request file for *task_id* (new id every call). Returns the open request."""
    with _Locked(run_dir):
        req = InputRequest(
            **spec.model_dump(),
            request_id=next_request_id(run_dir, task_id),
            task_id=task_id,
            state="open",
            created_at=now().isoformat(),
        )
        _write_request(request_path(run_dir, req.request_id), req)
    return req


def _coerce(field: InputField, raw: object) -> str | bool:
    """Validate one value against its field; CLI strings are coerced for bool fields."""
    name = field.name
    if field.type == "bool":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str) and raw.strip().lower() in BOOL_TRUE:
            return True
        if isinstance(raw, str) and raw.strip().lower() in BOOL_FALSE:
            return False
        raise InputError(f"field {name!r}: expected a boolean, got {raw!r}")
    if not isinstance(raw, str):
        raise InputError(f"field {name!r}: expected text, got {type(raw).__name__}")
    if field.type == "choice":
        if raw not in field.choices:
            raise InputError(f"field {name!r}: {raw!r} is not one of {field.choices}")
        return raw
    if len(raw) > field.max_chars:
        raise InputError(f"field {name!r}: exceeds {field.max_chars} characters")
    if field.required and not raw.strip():
        raise InputError(f"field {name!r} is required")
    return raw


def validate_values(spec: InputRequestSpec, values: dict[str, object]) -> dict[str, str | bool]:
    """Check *values* against the request's fields. Unknown names and missing required fields
    (without a default) are errors; a missing field with a default takes the default."""
    known = {f.name: f for f in spec.fields}
    unknown = sorted(set(values) - set(known))
    if unknown:
        raise InputError(f"unknown field(s): {', '.join(unknown)}")
    out: dict[str, str | bool] = {}
    for f in spec.fields:
        if f.name in values:
            out[f.name] = _coerce(f, values[f.name])
        elif f.default is not None:
            out[f.name] = f.default
        elif f.required:
            raise InputError(f"field {f.name!r} is required")
    return out


def answer_request(
    run_dir: str | Path,
    request_id: str,
    values: dict[str, object],
    *,
    source: InputSource = "cli",
    by: str = "",
    now: Callable[[], datetime] = _utc_now,
) -> InputRequest:
    """Record a human answer. Idempotent only when the values are identical; a different re-answer
    or an answer to a cancelled request raises (`InputNotOpenError`)."""
    with _Locked(run_dir):
        path = request_path(run_dir, request_id)
        req = load_request(run_dir, request_id)
        clean = validate_values(req, values)
        if req.state != "open":
            if req.state == "answered" and req.answer is not None and req.answer.values == clean:
                return req
            raise InputNotOpenError(f"request {request_id!r} is {req.state}, not open")
        req.state = "answered"
        req.answered_at = now().isoformat()
        req.answer = InputAnswer(values=clean, assumed=False, source=source, by=by)
        _write_request(path, req)
    return req


def assume_defaults(
    run_dir: str | Path,
    request_id: str,
    *,
    now: Callable[[], datetime] = _utc_now,
) -> InputRequest:
    """Non-blocking path: answer every field with its default (``assumed=True``). A required
    field with no default raises `InputError` (the engine fails the task rather than hang)."""
    with _Locked(run_dir):
        path = request_path(run_dir, request_id)
        req = load_request(run_dir, request_id)
        if req.state != "open":
            raise InputNotOpenError(f"request {request_id!r} is {req.state}, not open")
        values: dict[str, str | bool] = {}
        for f in req.fields:
            if f.default is not None:
                values[f.name] = f.default
            elif f.required:
                raise InputError(f"no default for required field {f.name!r} in non-blocking mode")
        req.state = "assumed"
        req.answered_at = now().isoformat()
        req.answer = InputAnswer(values=values, assumed=True, source="default", by="default")
        _write_request(path, req)
    return req


def cancel_request(run_dir: str | Path, request_id: str) -> InputRequest | None:
    """Mark an open request cancelled (task gone / run cancelled). Missing file => None."""
    with _Locked(run_dir):
        try:
            req = load_request(run_dir, request_id)
        except InputError:
            return None
        if req.state == "open":
            req.state = "cancelled"
            _write_request(request_path(run_dir, request_id), req)
    return req


def render_summary(requests: Iterable[InputRequest]) -> list[str]:
    """Plain-text lines describing requests for the CLI (task text is data; no markup)."""
    lines: list[str] = []
    for r in requests:
        lines.append(f"[{r.request_id}] task={r.task_id} state={r.state}")
        lines.append(f"  {r.prompt}")
        for f in r.fields:
            extra = f" choices={f.choices}" if f.type == "choice" else ""
            dflt = f" default={f.default!r}" if f.default is not None else ""
            req = "" if f.required else " (optional)"
            lines.append(f"  - {f.name} [{f.type}]{extra}{dflt}{req} {f.label}".rstrip())
    return lines


def restore_request(run_dir: str | Path, req: InputRequest) -> None:
    """Re-create a missing request file from the RunState mirror (never overwrites a file)."""
    with _Locked(run_dir):
        path = request_path(run_dir, req.request_id)
        if not path.exists():
            _write_request(path, req)
