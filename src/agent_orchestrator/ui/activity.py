"""Read-only live activity for a run's tasks (E-iafh2F Phase 1, ADR-0018).

A running task's tokens/cost are mirrored into ``state.json`` only when it settles, and
its turn count is recorded nowhere except the per-attempt ``transcript.jsonl`` (claude
``stream-json``) and, once finished, ``result.json``. This module derives live numbers from
those files at read time -- **no engine or state change**.

Split like ``graph.py``/``runs.py``:

* pure: ``TranscriptScan`` / ``fold_line`` / ``describe_action`` / ``parse_result`` /
  ``build_task_activity`` -- no I/O, the clock is an argument;
* I/O: ``TranscriptTailer`` (bounded, incremental, cached reader), ``locate_attempt_dirs``
  (path-guarded directory resolution) and ``read_run_activity`` (the one function that ties
  them to a loaded ``RunState``).

Safety (ADR-0011 / NFR-1): task ids come from ``state.json`` and are untrusted. Every path is
resolved and must stay inside the run directory and be a regular file (a FIFO planted as
``transcript.jsonl`` must not block the server). Every failure degrades to "no data", never an
error. Reads are bounded (``TAIL_CAP_BYTES`` per poll, ``MAX_LINE_BYTES`` per line).

Importable without the dashboard's optional web-framework extra.
"""

from __future__ import annotations

import json
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..models import RunState, TaskRunState
from .graph import display_text

logger = logging.getLogger(__name__)

# -- layout the engine writes (engine._run_with_retries, executors.claude_cli) -------------
TRANSCRIPT_FILE = "transcript.jsonl"
RESULT_FILE = "result.json"
CYCLE_DIR_PREFIX = "cycle-"
ATTEMPT_DIR_PREFIX = "attempt-"

# -- bounds ----------------------------------------------------------------------------------
# Max bytes consumed from one transcript per call. A first sight (or a burst) larger than this
# skips ahead to the tail and flags the scan ``approximate`` (counts become lower bounds).
TAIL_CAP_BYTES = 2 * 1024 * 1024
# A single event line longer than this is skipped unparsed (a huge tool_result).
MAX_LINE_BYTES = 512 * 1024
MAX_RESULT_BYTES = 1024 * 1024
MAX_CACHE_ENTRIES = 512
# Work bound per request: attempt dirs inspected per task (highest N kept), tasks per run.
MAX_ATTEMPT_DIRS = 64
MAX_ACTIVITY_TASKS = 2000
LAST_ACTION_MAX_CHARS = 120

# Idle (no transcript write) for this long on a RUNNING task => ``stuck`` hint.
STUCK_AFTER_SECONDS = 300.0

# `TaskActivity.source`.
SOURCE_TRANSCRIPT = "transcript"
SOURCE_RESULT = "result"
SOURCE_NONE = "none"

ACTIVITY_SCHEMA_VERSION = 1
STATUS_RUNNING = "running"

# Not-yet-started / never-dispatched statuses: nothing on disk to read.
_NO_CAPTURE_STATUSES = frozenset({"pending", "not_taken", "skipped"})

# Last-action wording for events that carry no tool call.
ACTION_REPLYING = "replying"
ACTION_THINKING = "thinking"
ACTION_STARTING = "starting"

# Tool-input keys worth showing, in priority order (first present wins).
_TOOL_ARG_KEYS = ("command", "file_path", "path", "pattern", "url", "description", "prompt")


# ---------------------------------------------------------------------------
# Pure layer
# ---------------------------------------------------------------------------


@dataclass
class TranscriptScan:
    """Running fold of a stream-json transcript (mutable accumulator, cached per file).

    Empirically (real transcripts, 60 attempts) the stream's own ``usage.output_tokens`` is a
    first-chunk value (~1% of the final), so live OUTPUT tokens are an *estimate*: per message
    ``max(reported, chars // OUTPUT_CHARS_PER_TOKEN)`` plus the ``thinking_tokens`` events'
    deltas. That lands at ~60-85% of the final figure -- a lower bound, labelled as such.
    Input tokens (non-cache) are reported correctly per message.
    """

    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    last_action: str | None = None
    bad_lines: int = 0
    approximate: bool = False
    # message id -> [input, reported_output, chars]; blocks of one API message arrive as
    # separate events sharing the id (and repeating usage), so totals are kept as deltas.
    _msgs: dict[str, list[int]] = field(default_factory=dict, repr=False)
    _prev_type: str | None = field(default=None, repr=False)

    @property
    def total_output(self) -> int:
        return self.output_tokens + self.thinking_tokens


# Bound on the per-scan message-id table (a pathological transcript must not grow it forever).
MAX_TRACKED_MESSAGES = 20000
# Heuristic for text/tool-input -> tokens when the stream has no real output count yet.
OUTPUT_CHARS_PER_TOKEN = 4


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _tool_arg(tool_input: object) -> str:
    if not isinstance(tool_input, dict):
        return ""
    for key in _TOOL_ARG_KEYS:
        raw = tool_input.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip().splitlines()[0]
    return ""


def describe_action(event: dict) -> str | None:
    """One-line description of what *event* shows the agent doing, or ``None`` to keep the
    previous description. The text is agent-authored: it is sanitized by the caller."""
    kind = event.get("type")
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if kind == "assistant" and isinstance(content, list):
        for block in reversed(content):
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_use":
                name = block.get("name")
                label = name if isinstance(name, str) and name else "tool"
                arg = _tool_arg(block.get("input"))
                return f"{label}: {arg}" if arg else label
            if btype == "thinking":
                return ACTION_THINKING
            if btype == "text":
                return ACTION_REPLYING
    # A `user` event is a tool_result coming back: it does not replace "what the agent last
    # did", which is the more useful thing to show (so it returns None, like a result event).
    if kind == "system":
        return ACTION_STARTING
    return None


def fold_line(scan: TranscriptScan, line: str) -> None:
    """Fold one transcript line into *scan*. Garbled/non-event lines only bump ``bad_lines``."""
    line = line.strip()
    if not line:
        return
    try:
        event = json.loads(line)
    except (ValueError, RecursionError):
        scan.bad_lines += 1
        return
    if not isinstance(event, dict):
        scan.bad_lines += 1
        return

    action = describe_action(event)
    if action is not None:
        cleaned, _ = display_text(action)
        scan.last_action = cleaned[:LAST_ACTION_MAX_CHARS]

    etype = event.get("type")
    if etype == "system" and event.get("subtype") == "thinking_tokens":
        scan.thinking_tokens += _as_int(event.get("estimated_tokens_delta"))
        return
    prev, scan._prev_type = scan._prev_type, etype if isinstance(etype, str) else None
    if etype != "assistant":
        return
    message = event.get("message")
    if not isinstance(message, dict):
        return
    sidechain = bool(event.get("parent_tool_use_id"))  # a subagent's message: tokens, not turns
    chars = _content_chars(message.get("content"))
    usage = message.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    in_tok, out_tok = _as_int(usage.get("input_tokens")), _as_int(usage.get("output_tokens"))
    msg_id = message.get("id")
    if isinstance(msg_id, str) and msg_id:
        if len(scan._msgs) >= MAX_TRACKED_MESSAGES:
            scan._msgs.clear()
            scan.approximate = True
        known = scan._msgs.get(msg_id)
        if known is None:
            known = scan._msgs[msg_id] = [0, 0, 0]
            if not sidechain:
                scan.turns += 1
        old_in, old_out = known[0], max(known[1], known[2] // OUTPUT_CHARS_PER_TOKEN)
        known[0], known[1], known[2] = in_tok, max(known[1], out_tok), known[2] + chars
        scan.input_tokens += known[0] - old_in
        new_out = max(known[1], known[2] // OUTPUT_CHARS_PER_TOKEN)
        scan.output_tokens += new_out - old_out
        return
    # No message id (older/other emitters): cannot de-duplicate, so consecutive assistant
    # events are treated as ONE turn and each event's own usage/chars are added as they come.
    if not sidechain and prev != "assistant":
        scan.turns += 1
    scan.input_tokens += in_tok
    scan.output_tokens += max(out_tok, chars // OUTPUT_CHARS_PER_TOKEN)


def _content_chars(content: object) -> int:
    """Characters of model-authored text / tool input in a message's content blocks."""
    if not isinstance(content, list):
        return 0
    total = 0
    for block in content:
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if isinstance(text, str):
            total += len(text)
        tool_input = block.get("input")
        if isinstance(tool_input, dict):
            total += len(json.dumps(tool_input, ensure_ascii=False))
    return total


@dataclass(frozen=True)
class ResultInfo:
    """What a finished attempt's ``result.json`` says (the final ``result`` event)."""

    turns: int | None
    input_tokens: int
    output_tokens: int
    cost_usd: float | None


def parse_result(text: str) -> ResultInfo | None:
    """Parse ``result.json``; ``None`` when it is not a JSON object."""
    try:
        obj = json.loads(text)
    except (ValueError, RecursionError):
        return None
    if not isinstance(obj, dict):
        return None
    usage = obj.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    turns = obj.get("num_turns")
    cost = obj.get("total_cost_usd")
    return ResultInfo(
        turns=_as_int(turns) if isinstance(turns, int) and not isinstance(turns, bool) else None,
        input_tokens=_as_int(usage.get("input_tokens")),
        output_tokens=_as_int(usage.get("output_tokens")),
        cost_usd=float(cost)
        if isinstance(cost, (int, float)) and not isinstance(cost, bool)
        else None,
    )


@dataclass(frozen=True)
class TaskActivity:
    """Live (or settled) activity of one task -- the JSON payload row."""

    task_id: str
    status: str
    source: str
    attempt: int | None
    cycle: int
    turns: int | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    last_action: str | None
    idle_seconds: float | None
    elapsed_seconds: float | None
    stuck: bool
    approximate: bool
    # True when output tokens are the live estimate (a lower bound), not the engine's exact figure.
    tokens_estimated: bool = False


@dataclass(frozen=True)
class RunActivity:
    schema_version: int
    run_id: str
    generated_at: str
    tasks: dict[str, TaskActivity]


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def elapsed_seconds(ts: TaskRunState, now: datetime) -> float | None:
    """Seconds since the task first started (to ``ended_at`` once it has ended)."""
    started = _parse_iso(ts.started_at)
    if started is None:
        return None
    ended = _parse_iso(ts.ended_at) if ts.status != STATUS_RUNNING else None
    return max(0.0, ((ended or now) - started).total_seconds())


def build_task_activity(
    task_id: str,
    ts: TaskRunState,
    *,
    now: datetime,
    scan: TranscriptScan | None,
    transcript_mtime: float | None,
    attempt: int | None,
    prior: list[ResultInfo],
    final: ResultInfo | None,
) -> TaskActivity:
    """Combine the on-disk observations into one row (pure).

    *prior* are the already-finished attempts of the current cycle (their ``result.json``);
    they add to a running task's turns/tokens/cost so the numbers match the cumulative
    semantics the settled ``TaskStat`` uses. *final* is the latest attempt's ``result.json``
    for a settled task. Cost is only ever a floor from finished attempts: live events carry
    no price (ADR-0018 D1).
    """
    running = ts.status == STATUS_RUNNING
    elapsed = elapsed_seconds(ts, now)
    prior_turns = sum(r.turns or 0 for r in prior)
    prior_in = sum(r.input_tokens for r in prior)
    prior_out = sum(r.output_tokens for r in prior)
    costs = [r.cost_usd for r in prior if r.cost_usd is not None]
    prior_cost = sum(costs) if costs else None

    if running and scan is not None:
        idle = None
        if transcript_mtime is not None:
            idle = max(0.0, now.timestamp() - transcript_mtime)
        return TaskActivity(
            task_id=task_id,
            status=ts.status,
            source=SOURCE_TRANSCRIPT,
            attempt=attempt,
            cycle=ts.dispatch_cycle,
            turns=prior_turns + scan.turns,
            input_tokens=prior_in + scan.input_tokens,
            output_tokens=prior_out + scan.total_output,
            cost_usd=prior_cost,
            last_action=scan.last_action,
            idle_seconds=idle,
            elapsed_seconds=elapsed,
            stuck=idle is not None and idle >= STUCK_AFTER_SECONDS,
            approximate=scan.approximate,
            tokens_estimated=True,
        )

    if not running and final is None and scan is not None:
        # Settled attempt that never wrote result.json (crash / quota kill): fall back to the
        # transcript so turns are still shown (source stays "transcript").
        return TaskActivity(
            task_id=task_id,
            status=ts.status,
            source=SOURCE_TRANSCRIPT,
            attempt=attempt,
            cycle=ts.dispatch_cycle,
            turns=prior_turns + scan.turns,
            input_tokens=prior_in + scan.input_tokens,
            output_tokens=prior_out + scan.total_output,
            cost_usd=None,
            last_action=None,
            idle_seconds=None,
            elapsed_seconds=elapsed,
            stuck=False,
            approximate=scan.approximate,
            tokens_estimated=True,
        )

    if not running and (final is not None or prior):
        results = [*prior, *([final] if final is not None else [])]
        turns = sum(r.turns or 0 for r in results)
        return TaskActivity(
            task_id=task_id,
            status=ts.status,
            source=SOURCE_RESULT,
            attempt=attempt,
            cycle=ts.dispatch_cycle,
            turns=turns,
            input_tokens=sum(r.input_tokens for r in results),
            output_tokens=sum(r.output_tokens for r in results),
            cost_usd=None,  # settled cost is TaskStat.cost_usd (the engine's own number)
            last_action=None,
            idle_seconds=None,
            elapsed_seconds=elapsed,
            stuck=False,
            approximate=False,
        )

    # Running with no transcript yet, or no capture at all.
    return TaskActivity(
        task_id=task_id,
        status=ts.status,
        source=SOURCE_NONE,
        attempt=attempt,
        cycle=ts.dispatch_cycle,
        turns=prior_turns if prior else None,
        input_tokens=prior_in if prior else None,
        output_tokens=prior_out if prior else None,
        cost_usd=prior_cost,
        last_action=None,
        idle_seconds=None,
        elapsed_seconds=elapsed,
        stuck=False,
        approximate=False,
    )


# ---------------------------------------------------------------------------
# I/O layer
# ---------------------------------------------------------------------------


@dataclass
class _Entry:
    offset: int
    size: int
    mtime_ns: int
    scan: TranscriptScan
    ino: int = 0


class TranscriptTailer:
    """Bounded, incremental, cached reader of ``transcript.jsonl`` files.

    Re-reading only the bytes appended since the previous poll keeps the per-poll cost
    proportional to new output, and ``TAIL_CAP_BYTES`` bounds the worst case (ADR-0018 D1).
    Thread-safe: the dashboard's sync endpoints run on a thread pool.
    """

    def __init__(self, *, tail_cap: int = TAIL_CAP_BYTES, max_entries: int = MAX_CACHE_ENTRIES):
        self._tail_cap = tail_cap
        self._max_entries = max_entries
        self._cache: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()

    def scan(self, path: Path) -> tuple[TranscriptScan, float] | None:
        """Return ``(scan, mtime_epoch)`` for *path*, or ``None`` if unreadable/not a file."""
        try:
            st = path.stat()
        except OSError:
            return None
        key = str(path)
        with self._lock:
            entry = self._cache.get(key)
            if entry is not None and (entry.size, entry.mtime_ns) == (st.st_size, st.st_mtime_ns):
                self._cache.move_to_end(key)
                return entry.scan, st.st_mtime
            if entry is None or st.st_size < entry.offset or entry.ino != st.st_ino:
                entry = _Entry(0, 0, 0, TranscriptScan(), st.st_ino)  # new, truncated or replaced
            try:
                self._advance(path, entry, st.st_size)
            except OSError:
                return None
            entry.size, entry.mtime_ns = st.st_size, st.st_mtime_ns
            self._cache[key] = entry
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_entries:
                self._cache.popitem(last=False)
            return entry.scan, st.st_mtime

    def _advance(self, path: Path, entry: _Entry, size: int) -> None:
        """Consume complete lines in ``[entry.offset, size)``; leave a partial tail unread."""
        start = entry.offset
        skip_partial_first = False
        if size - start > self._tail_cap:
            start = size - self._tail_cap
            entry.scan.approximate = True
            skip_partial_first = True
        with path.open("rb") as handle:
            handle.seek(start)
            data = handle.read(size - start)
        if skip_partial_first:
            nl = data.find(b"\n")
            data = b"" if nl < 0 else data[nl + 1 :]
            start = size - len(data)
        last_nl = data.rfind(b"\n")
        if last_nl < 0:
            # No complete line yet. An over-long unterminated line is dropped (bounded memory);
            # otherwise wait for the writer to finish it.
            if len(data) > MAX_LINE_BYTES:
                entry.scan.bad_lines += 1
                entry.scan.approximate = True
                entry.offset = start + len(data)
            else:
                entry.offset = start
            return
        for raw in data[: last_nl + 1].split(b"\n"):
            if len(raw) > MAX_LINE_BYTES:
                entry.scan.bad_lines += 1
                entry.scan.approximate = True
                continue
            fold_line(entry.scan, raw.decode("utf-8", errors="replace"))
        entry.offset = start + last_nl + 1


_RESULT_CACHE_MAX = 1024


class ResultReader:
    """Cached, bounded ``result.json`` reader (keyed by path, validated by size+mtime)."""

    def __init__(self) -> None:
        self._cache: OrderedDict[str, tuple[int, int, ResultInfo | None]] = OrderedDict()
        self._lock = threading.Lock()

    def read(self, path: Path) -> ResultInfo | None:
        try:
            st = path.stat()
        except OSError:
            return None
        if st.st_size > MAX_RESULT_BYTES:
            return None
        key = str(path)
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None and hit[:2] == (st.st_size, st.st_mtime_ns):
                return hit[2]
        try:
            info = parse_result(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return None
        with self._lock:
            self._cache[key] = (st.st_size, st.st_mtime_ns, info)
            while len(self._cache) > _RESULT_CACHE_MAX:
                self._cache.popitem(last=False)
        return info


def _safe_segment(name: str) -> bool:
    """A task id is a single path segment: no separators, no dot-dirs, no NUL."""
    return (
        bool(name)
        and name not in (".", "..")
        and "/" not in name
        and "\\" not in name
        and "\0" not in name
    )


def _inside(base: Path, candidate: Path) -> bool:
    """True iff *candidate* resolves to a location inside *base* (symlink escapes fail)."""
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError):
        return False
    return resolved == base or base in resolved.parents


def _regular_file(base: Path, path: Path) -> Path | None:
    """*path* if it is a regular file that stays inside *base*; else ``None``."""
    if not _inside(base, path):
        return None
    try:
        resolved = path.resolve()  # open the resolved target, not a re-followable symlink
        return resolved if resolved.is_file() else None
    except (OSError, RuntimeError):
        return None


def locate_attempt_dirs(run_dir: Path, task_id: str, cycle: int) -> list[tuple[int, Path]]:
    """``[(attempt_number, dir)]`` ascending, for the task's *current* dispatch cycle.

    Layout (engine ``_run_with_retries``): cycle <= 1 -> ``<run>/<task>/attempt-<n>``;
    cycle >= 2 -> ``<run>/<task>/cycle-<c>/attempt-<n>``. Returns ``[]`` for an unsafe id or
    when nothing is on disk. Every returned directory resolves inside *run_dir*.
    """
    if not _safe_segment(task_id):
        return []
    base = run_dir.resolve()
    task_dir = run_dir / task_id
    if cycle >= 2:
        task_dir = task_dir / f"{CYCLE_DIR_PREFIX}{cycle}"
    if not _inside(base, task_dir):
        return []
    try:
        children = list(task_dir.iterdir())
    except OSError:
        return []
    found: list[tuple[int, Path]] = []
    for child in children:
        name = child.name
        if not name.startswith(ATTEMPT_DIR_PREFIX):
            continue
        digits = name[len(ATTEMPT_DIR_PREFIX) :]
        if not (digits.isascii() and digits.isdigit()):
            continue
        if _inside(base, child) and child.is_dir():
            found.append((int(digits), child))
    found.sort()
    return found[-MAX_ATTEMPT_DIRS:]


def read_run_activity(
    state: RunState,
    run_dir: Path,
    *,
    tailer: TranscriptTailer,
    results: ResultReader,
    now: datetime,
) -> RunActivity:
    """Activity for every task of *state* that has been dispatched (the one I/O entry point)."""
    base = run_dir.resolve()
    rows: dict[str, TaskActivity] = {}
    ordered = sorted(state.tasks.items(), key=lambda kv: kv[1].status != STATUS_RUNNING)
    for task_id, ts in ordered[:MAX_ACTIVITY_TASKS]:
        if ts.status in _NO_CAPTURE_STATUSES and ts.attempts == 0:
            continue
        attempts = locate_attempt_dirs(run_dir, task_id, ts.dispatch_cycle)
        latest_n = attempts[-1][0] if attempts else None
        prior: list[ResultInfo] = []
        for _, adir in attempts[:-1]:
            rpath = _regular_file(base, adir / RESULT_FILE)
            info = results.read(rpath) if rpath else None
            if info is not None:
                prior.append(info)

        scan = mtime = None
        final = None
        if attempts:
            last_dir = attempts[-1][1]
            if ts.status == STATUS_RUNNING:
                tpath = _regular_file(base, last_dir / TRANSCRIPT_FILE)
                got = tailer.scan(tpath) if tpath else None
                if got is not None:
                    scan, mtime = got
            else:
                rpath = _regular_file(base, last_dir / RESULT_FILE)
                final = results.read(rpath) if rpath else None
                if final is None:
                    tpath = _regular_file(base, last_dir / TRANSCRIPT_FILE)
                    got = tailer.scan(tpath) if tpath else None
                    if got is not None:
                        scan, mtime = got
        rows[task_id] = build_task_activity(
            task_id,
            ts,
            now=now,
            scan=scan,
            transcript_mtime=mtime,
            attempt=latest_n,
            prior=prior,
            final=final,
        )
    return RunActivity(
        schema_version=ACTIVITY_SCHEMA_VERSION,
        run_id=state.run_id,
        generated_at=now.isoformat(),
        tasks=rows,
    )


def utc_now() -> datetime:
    """Default clock; tests pass their own ``now``."""
    return datetime.now(UTC)


__all__ = [
    "RunActivity",
    "TaskActivity",
    "TranscriptScan",
    "TranscriptTailer",
    "ResultReader",
    "build_task_activity",
    "describe_action",
    "fold_line",
    "locate_attempt_dirs",
    "parse_result",
    "read_run_activity",
    "utc_now",
]
