"""Timing/profiling and cache-effectiveness reporting (E-1cecSx B2/B4,
`docs-md/cost-caching-optimization-hld.md` §2, §4).

Three families of pure function, all read-only over already-persisted data:

- **Run-level timing** (B2.1, this module): `top_n_slowest_tasks` reuses the exact per-task
  `ended_at - started_at` derivation `models.py::compute_run_active_seconds` already performs
  (summed, run-wide) -- here, unsummed and ranked. No new capture needed.
- **Within-task activity-type breakdown** (B2.2, this module): `task_activity_breakdown` reads
  one task's captured `transcript.jsonl` (via the EXISTING `claude_cli.py::
  parse_transcript_events`, reused unchanged -- this module never re-implements JSONL parsing)
  and buckets elapsed wall time between events into an activity category derived from
  `tool_use` block names (design doc §2.2's table). See that function's own docstring for the
  explicit approximation caveats -- turn-level granularity, elapsed-time-to-next-event heuristic
  -- this is deliberately NOT sold as exact per-call profiling anywhere (docstring, CLI help
  text, or comments).
- **Cache effectiveness** (B4, this module): `cache_effectiveness` derives a hit-rate figure
  from the token-accounting fields E-9h3m7k already captures
  (`TaskRunState.cumulative_cache_read_input_tokens`/`.cumulative_cache_creation_input_tokens`).

All three function families surface via the on-demand `ao report-timing`/dashboard expandable
detail view only -- never a new default column on the main task table (locked-in constraint,
design doc §2.3/§4).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from .executors.claude_cli import parse_transcript_events
from .models import RunState, RunUsageTotals, TaskRunState


class TaskDuration(BaseModel):
    """One task's wall-clock duration for one dispatch cycle's settle (design doc §2.1)."""

    task_id: str
    seconds: float


def top_n_slowest_tasks(state: RunState, n: int) -> list[TaskDuration]:
    """The *n* slowest SETTLED tasks in *state*, by `ended_at - started_at`, descending.

    Same per-task duration derivation `models.py::compute_run_active_seconds` already uses
    (summed, run-wide) -- unsummed here, one row per task, ranked. A task contributes only
    once BOTH timestamps are present (pending/running/not_taken tasks are excluded, same
    guard as `compute_run_active_seconds`). Ties break by task id (ascending) for
    deterministic output. `n <= 0` returns an empty list rather than raising.
    """
    if n <= 0:
        return []
    durations: list[TaskDuration] = []
    for task_id, ts in state.tasks.items():
        if ts.started_at is None or ts.ended_at is None:
            continue
        started = datetime.fromisoformat(ts.started_at)
        ended = datetime.fromisoformat(ts.ended_at)
        durations.append(TaskDuration(task_id=task_id, seconds=(ended - started).total_seconds()))
    durations.sort(key=lambda d: (-d.seconds, d.task_id))
    return durations[:n]


class CacheEffectiveness(BaseModel):
    """Prompt-cache hit-rate figure for one task (or a run-wide total)."""

    cache_read_tokens: int
    cache_creation_tokens: int
    uncached_input_tokens: int
    # None when there is no input at all to compute a rate over (the zero-denominator case) --
    # distinct from a genuine 0.0 hit rate (input existed, none of it was cached), so a
    # dashboard/report can render "n/a" rather than a misleading "0%".
    hit_rate: float | None = None


def _effectiveness(cache_read: int, cache_creation: int, uncached_input: int) -> CacheEffectiveness:
    denominator = cache_read + cache_creation + uncached_input
    hit_rate = (cache_read / denominator) if denominator > 0 else None
    return CacheEffectiveness(
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
        uncached_input_tokens=uncached_input,
        hit_rate=hit_rate,
    )


def cache_effectiveness(ts: TaskRunState) -> CacheEffectiveness:
    """Cache hit-rate for one task, from its already-persisted cumulative usage (E-9h3m7k)."""
    return _effectiveness(
        cache_read=ts.cumulative_cache_read_input_tokens,
        cache_creation=ts.cumulative_cache_creation_input_tokens,
        uncached_input=ts.cumulative_input_tokens,
    )


def run_cache_effectiveness(totals: RunUsageTotals) -> CacheEffectiveness:
    """Run-wide cache hit-rate, from `models.py::compute_run_usage_totals`'s output."""
    return _effectiveness(
        cache_read=totals.cache_read_input_tokens,
        cache_creation=totals.cache_creation_input_tokens,
        uncached_input=totals.input_tokens,
    )


# ---------------------------------------------------------------------------
# Within-task activity-type breakdown (B2.2, design doc §2.2).
# ---------------------------------------------------------------------------

ActivityCategory = Literal[
    "file-edit",
    "build-or-test",
    "shell-other",
    "search-or-read",
    "other-tool",
    "thinking-or-text",
]

# Tool names bucketed into each category -- copied VERBATIM from design doc §2.2's table
# (early-gate architect finding, required: named module-level constants, never inline/magic
# literals). These sets are the only inputs `_categorize_tool` consults.
_FILE_EDIT_TOOL_NAMES: frozenset[str] = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
_SEARCH_OR_READ_TOOL_NAMES: frozenset[str] = frozenset({"Read", "Grep", "Glob"})
_BASH_TOOL_NAME = "Bash"

# Case-insensitive substring keyword list distinguishing `build-or-test` from `shell-other`
# for a `Bash` tool_use, matched against the block's `input.command` text -- copied verbatim
# from design doc §2.2. This is an explicit HEURISTIC over free-form shell text, not a shell
# parser (e.g. a command that merely mentions "make" in a path or comment still matches) --
# the design doc flags this as the epic's only heuristic-based MVP item; not oversold as exact.
_BUILD_OR_TEST_KEYWORDS: tuple[str, ...] = (
    "pytest",
    "npm run build",
    "make",
    "go build",
    "go test",
    "cargo build",
    "cargo test",
    "mvn",
    "ruff",
    "mypy",
)

# Tie-break order when one assistant turn carries MULTIPLE `tool_use` blocks that map to
# different categories (e.g. an `Edit` alongside a `Bash` call in one turn). Design doc §2.2
# directs that such a turn's whole elapsed time is "attributed to the whole set, not split
# further" but does not prescribe which category wins when the set spans more than one --
# this mirrors the design doc table's own row order (file-edit first) as the most
# information-dense category to surface for a mixed turn. Documented here as an explicit,
# disclosed implementation choice, not left as an unstated assumption.
_CATEGORY_PRIORITY: tuple[ActivityCategory, ...] = (
    "file-edit",
    "build-or-test",
    "shell-other",
    "search-or-read",
    "other-tool",
)


def _categorize_tool(name: str, tool_input: dict) -> ActivityCategory:
    """Bucket one `tool_use` block by tool name (design doc §2.2's table)."""
    if name in _FILE_EDIT_TOOL_NAMES:
        return "file-edit"
    if name == _BASH_TOOL_NAME:
        command = str(tool_input.get("command", "")).lower()
        if any(keyword in command for keyword in _BUILD_OR_TEST_KEYWORDS):
            return "build-or-test"
        return "shell-other"
    if name in _SEARCH_OR_READ_TOOL_NAMES:
        return "search-or-read"
    return "other-tool"


def _tool_use_blocks(event: dict) -> list[dict]:
    """`tool_use` content blocks of one `assistant` event, or `[]` for any other event/shape
    (a `user`/`system`/`result` event, or a malformed `assistant` event -- best-effort, never
    raises)."""
    if event.get("type") != "assistant":
        return []
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return []
    return [
        block for block in content if isinstance(block, dict) and block.get("type") == "tool_use"
    ]


def _categorize_event(event: dict) -> ActivityCategory:
    """Bucket one transcript event for the elapsed-time walk below.

    `thinking-or-text` when *event* carries no `tool_use` block at all -- an assistant
    text/thinking-only turn, a `user` tool-result turn, or a `system`/`result` event (design
    doc §2.2: "time between events with NO tool_use block at all"). Otherwise, the tool-derived
    category of its `tool_use` block(s), via `_categorize_tool`, tie-broken by
    `_CATEGORY_PRIORITY` when the turn carries more than one block mapping to different
    categories.
    """
    blocks = _tool_use_blocks(event)
    if not blocks:
        return "thinking-or-text"
    categories = {
        _categorize_tool(str(block.get("name", "")), block.get("input") or {}) for block in blocks
    }
    if len(categories) == 1:
        return categories.pop()
    for category in _CATEGORY_PRIORITY:
        if category in categories:
            return category
    return "other-tool"  # defensive only -- unreachable: _CATEGORY_PRIORITY covers every
    # category _categorize_tool can return, so the loop above always matches first.


def _parse_event_timestamp(value: object) -> datetime | None:
    """Parse a transcript event's top-level `timestamp` field to a timezone-aware `datetime`.

    Confirmed shape (design doc §2.2, direct inspection of real captured transcripts): ISO
    8601 with millisecond precision and a trailing `Z`, e.g. `"2026-07-22T11:05:48.049Z"`.
    Returns `None` for a missing/non-string/unparseable value -- events without a usable
    timestamp (`system`/`rate_limit_event` events in a real capture carry none) are silently
    excluded from `task_activity_breakdown`'s walk, never raise.

    Deliberately NOT reused from `claude_cli.py::_parse_iso_to_epoch` (the same trailing-`Z`
    handling, a few lines) -- that helper is module-private to `claude_cli.py` and returns an
    epoch float rather than a `datetime`; duplicating this small, one-off ISO parse avoids
    coupling this module to another module's private symbol for an unrelated purpose.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class ActivityCategorySeconds(BaseModel):
    """One activity category's total elapsed seconds within one task's transcript."""

    category: ActivityCategory
    seconds: float


class ActivityBreakdown(BaseModel):
    """Within-task activity-type breakdown for one task's `transcript.jsonl` (design doc
    §2.2, B2.2) -- a compact, whole-transcript SUM per category (not per-event rows), sorted
    descending by seconds (ties broken by category name, ascending, for deterministic output).
    See `task_activity_breakdown`'s own docstring for the approximation this represents.
    """

    total_seconds: float
    by_category: list[ActivityCategorySeconds]


def task_activity_breakdown(transcript_path: str) -> ActivityBreakdown:
    """Within-task activity-type breakdown for one task's captured `transcript.jsonl` (B2.2).

    Reads the file at *transcript_path* and hands its text to the EXISTING
    `claude_cli.py::parse_transcript_events` (reused unchanged -- this function's own job is
    only the file read plus the bucketing below, never a second JSONL parser; `claude_cli.py`
    is otherwise untouched by this change). Raises the same `OSError`/`FileNotFoundError`
    `open()` raises for a missing/unreadable path -- callers are expected to catch it, the same
    convention `RunStateStore.load` already establishes for this codebase's other
    "missing input" cases (see `cli.py::report_timing`'s `--task` handling).

    For each event with a parseable top-level `timestamp`, the wall-clock gap to the NEXT
    timestamped event is bucketed via `_categorize_event` into an `ActivityCategory` and
    summed. Events with no parseable `timestamp` (e.g. `system`/`rate_limit_event` events in a
    real capture) are excluded entirely -- never used as either interval endpoint. The final
    timestamped event has no "next" event and so contributes no elapsed time. An empty or
    all-unparseable transcript returns an all-zero (`total_seconds=0.0`, `by_category=[]`)
    breakdown, never raises.

    **APPROXIMATION, not exact per-call profiling** (design doc §2.2 -- deliberately not
    oversold anywhere, docstring/CLI help text/comments alike):
    - **Turn-level granularity.** A single `assistant` turn can carry more than one parallel
      `tool_use` block; the whole turn's elapsed time is attributed to ONE category (see
      `_categorize_event`/`_CATEGORY_PRIORITY`), never split proportionally across categories.
    - **Elapsed-time-to-next-event heuristic folds ordinary model latency into an adjacent
      bucket.** The gap measured for one event's category is the wall-clock distance to the
      NEXT event, which necessarily includes normal model "thinking"/response-generation and
      CLI/API round-trip latency, not just time genuinely spent executing that tool. Precise
      enough to answer "is this task's time mostly in edits, mostly in test runs, or mostly in
      search/exploration" -- the operator question B2.2 exists to answer -- not exact
      per-call profiling.
    """
    with open(transcript_path, encoding="utf-8") as f:
        text = f.read()
    events = parse_transcript_events(text)

    timestamped: list[tuple[datetime, dict]] = []
    for event in events:
        ts = _parse_event_timestamp(event.get("timestamp"))
        if ts is not None:
            timestamped.append((ts, event))

    totals: dict[ActivityCategory, float] = {}
    for (current_ts, current_event), (next_ts, _next_event) in zip(timestamped, timestamped[1:]):
        elapsed = (next_ts - current_ts).total_seconds()
        if elapsed < 0:
            # Defensive only -- a real capture stream is emitted (and thus timestamped) in
            # order; never raise on an out-of-order/malformed pair, just skip contributing it.
            continue
        category = _categorize_event(current_event)
        totals[category] = totals.get(category, 0.0) + elapsed

    by_category = [ActivityCategorySeconds(category=c, seconds=s) for c, s in totals.items()]
    by_category.sort(key=lambda row: (-row.seconds, row.category))
    return ActivityBreakdown(total_seconds=sum(totals.values()), by_category=by_category)
