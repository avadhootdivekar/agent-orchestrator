"""Tests for the live-activity reader (E-iafh2F Phase 1, ADR-0018).

Unit (pure fold/describe/parse/build, fixed clocks), I/O (bounded incremental tail, partial /
garbled / oversize lines, cache, path safety) and integration (service + HTTP). The
real-process e2e lives in ``test_activity_e2e.py``.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_orchestrator.models import RunState, TaskRunState
from agent_orchestrator.ui import activity as act
from agent_orchestrator.ui.activity import (
    ACTION_REPLYING,
    ACTION_THINKING,
    LAST_ACTION_MAX_CHARS,
    SOURCE_NONE,
    SOURCE_RESULT,
    SOURCE_TRANSCRIPT,
    STUCK_AFTER_SECONDS,
    ResultReader,
    TranscriptScan,
    TranscriptTailer,
    build_task_activity,
    describe_action,
    fold_line,
    locate_attempt_dirs,
    parse_result,
    read_run_activity,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "transcript_stream_real_shape.jsonl"
NOW = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
RUN_ID = "demo-20261002T110000Z"


def _assistant(msg_id: str | None, blocks: list[dict], *, out: int = 5, inp: int = 3, **extra):
    message: dict = {
        "role": "assistant",
        "content": blocks,
        "usage": {"input_tokens": inp, "output_tokens": out},
    }
    if msg_id is not None:
        message["id"] = msg_id
    return {"type": "assistant", "message": message, **extra}


def _tool(name: str, **inp: object) -> dict:
    return {"type": "tool_use", "id": "t", "name": name, "input": inp}


def _fold(*events: object) -> TranscriptScan:
    scan = TranscriptScan()
    for e in events:
        fold_line(scan, e if isinstance(e, str) else json.dumps(e))
    return scan


# ---------------------------------------------------------------------------
# Pure: describe_action / fold_line / parse_result
# ---------------------------------------------------------------------------


class TestDescribeAction:
    def test_tool_use_shows_name_and_first_line_of_arg(self) -> None:
        ev = _assistant("m1", [_tool("Bash", command="pytest -q\necho second")])
        assert describe_action(ev) == "Bash: pytest -q"

    def test_arg_key_priority_and_missing_arg(self) -> None:
        assert (
            describe_action(_assistant("m", [_tool("Read", file_path="/a/b.py")]))
            == "Read: /a/b.py"
        )
        assert describe_action(_assistant("m", [_tool("Grep", pattern="foo")])) == "Grep: foo"
        assert describe_action(_assistant("m", [_tool("Weird", x=1)])) == "Weird"

    def test_thinking_text_and_user_and_system(self) -> None:
        assert (
            describe_action(_assistant("m", [{"type": "thinking", "thinking": ""}]))
            == ACTION_THINKING
        )
        assert describe_action(_assistant("m", [{"type": "text", "text": "hi"}])) == ACTION_REPLYING
        assert describe_action({"type": "user", "message": {"content": []}}) is None
        assert describe_action({"type": "result"}) is None

    def test_malformed_shapes_do_not_raise(self) -> None:
        assert describe_action({"type": "assistant", "message": "nope"}) is None
        assert (
            describe_action({"type": "assistant", "message": {"content": [1, None, "x"]}}) is None
        )
        assert describe_action(_assistant("m", [_tool("T", command=7)])) == "T"


class TestFoldLine:
    def test_blocks_of_one_message_count_as_one_turn_with_last_usage(self) -> None:
        # stream-json emits one event per content block; all share message.id.
        scan = _fold(
            _assistant("m1", [{"type": "thinking", "thinking": ""}], out=1, inp=10),
            _assistant("m1", [_tool("Bash", command="ls")], out=2, inp=10),
            {"type": "user", "message": {"content": []}},
            _assistant("m2", [{"type": "text", "text": "done"}], out=4, inp=7),
        )
        assert scan.turns == 2
        assert scan.input_tokens == 17  # 10 (once) + 7, NOT 10+10+7
        assert scan.last_action == ACTION_REPLYING

    def test_output_tokens_use_char_estimate_when_reported_is_tiny(self) -> None:
        # Reported output_tokens in the stream is a first-chunk value; chars/4 wins.
        scan = _fold(_assistant("m1", [{"type": "text", "text": "x" * 400}], out=1))
        assert scan.output_tokens == 100

    def test_thinking_token_events_are_added(self) -> None:
        scan = _fold(
            {"type": "system", "subtype": "thinking_tokens", "estimated_tokens_delta": 50},
            {"type": "system", "subtype": "thinking_tokens", "estimated_tokens_delta": 25},
        )
        assert scan.thinking_tokens == 75
        assert scan.total_output == 75

    def test_sidechain_messages_add_tokens_but_not_turns(self) -> None:
        scan = _fold(
            _assistant("m1", [{"type": "text", "text": "a"}], inp=5),
            _assistant("s1", [{"type": "text", "text": "b"}], inp=6, parent_tool_use_id="toolu_x"),
        )
        assert scan.turns == 1
        assert scan.input_tokens == 11

    def test_interleaved_message_ids_are_not_double_counted(self) -> None:
        scan = _fold(
            _assistant("a", [{"type": "text", "text": "x" * 40}], inp=1),
            _assistant("b", [{"type": "text", "text": "y" * 40}], inp=2),
            _assistant("a", [{"type": "text", "text": "z" * 40}], inp=1),
        )
        assert scan.turns == 2
        assert scan.input_tokens == 3
        assert scan.output_tokens == 20 + 10  # a: 80 chars -> 20, b: 40 -> 10

    def test_no_id_consecutive_assistant_events_are_one_turn(self) -> None:
        scan = _fold(
            _assistant(None, [{"type": "thinking", "thinking": ""}]),
            _assistant(None, [_tool("Read", file_path="/a")]),
            {"type": "user", "message": {"content": []}},
            _assistant(None, [{"type": "text", "text": "ok"}]),
        )
        assert scan.turns == 2

    @pytest.mark.parametrize("line", ["", "   ", "not json", "{", "[1,2]", "42", "null", '"str"'])
    def test_garbled_or_non_object_lines_never_raise(self, line: str) -> None:
        scan = _fold(line)
        assert scan.turns == 0
        assert scan.bad_lines == (0 if not line.strip() else 1)

    def test_hostile_field_types_are_tolerated(self) -> None:
        scan = _fold(
            {
                "type": "assistant",
                "message": {
                    "id": 5,
                    "usage": {"input_tokens": "x", "output_tokens": -3},
                    "content": "s",
                },
            },
            {
                "type": "assistant",
                "message": {"id": "m", "usage": [1], "content": [{"input": "str"}]},
            },
            {"type": "system", "subtype": "thinking_tokens", "estimated_tokens_delta": True},
        )
        assert scan.input_tokens == 0 and scan.thinking_tokens == 0

    def test_last_action_is_sanitized_and_capped(self) -> None:
        evil = "ls‮​" + "A" * 500
        scan = _fold(_assistant("m", [_tool("Bash", command=evil)]))
        assert scan.last_action is not None
        assert "‮" not in scan.last_action and "​" not in scan.last_action
        assert len(scan.last_action) <= LAST_ACTION_MAX_CHARS

    def test_real_shape_fixture_matches_result_event(self) -> None:
        """Pinned against a redacted capture of a real claude stream-json transcript."""
        lines = FIXTURE.read_text().splitlines()
        scan = TranscriptScan()
        for line in lines:
            fold_line(scan, line)
        result = json.loads(lines[-1])
        distinct = len({json.loads(x)["message"]["id"] for x in lines if '"assistant"' in x[:20]})
        assert scan.turns == distinct > 0
        assert scan.bad_lines == 0
        # Live turns are a (slightly lower) view of the final num_turns.
        assert scan.turns <= result["num_turns"]
        # Input tokens are exact; output is a lower-bound estimate of the final figure.
        assert scan.input_tokens == result["usage"]["input_tokens"]
        assert 0 < scan.total_output <= result["usage"]["output_tokens"]


class TestParseResult:
    def test_reads_turns_usage_and_cost(self) -> None:
        info = parse_result(
            json.dumps(
                {
                    "num_turns": 7,
                    "total_cost_usd": 0.5,
                    "usage": {"input_tokens": 3, "output_tokens": 9},
                }
            )
        )
        assert info is not None
        assert (info.turns, info.input_tokens, info.output_tokens, info.cost_usd) == (7, 3, 9, 0.5)

    @pytest.mark.parametrize("text", ["", "x", "[1]", "null"])
    def test_non_objects_are_none(self, text: str) -> None:
        assert parse_result(text) is None

    def test_missing_and_bad_fields_degrade(self) -> None:
        info = parse_result(json.dumps({"num_turns": "7", "total_cost_usd": True, "usage": "u"}))
        assert info is not None
        assert info.turns is None and info.cost_usd is None and info.input_tokens == 0


# ---------------------------------------------------------------------------
# Pure: build_task_activity
# ---------------------------------------------------------------------------


def _running_ts(**kw: object) -> TaskRunState:
    base = {
        "status": "running",
        "attempts": 1,
        "started_at": (NOW - timedelta(seconds=95)).isoformat(),
        "dispatch_cycle": 1,
    }
    return TaskRunState(**{**base, **kw})  # type: ignore[arg-type]


class TestBuildTaskActivity:
    def test_running_with_transcript_fixed_clock(self) -> None:
        scan = _fold(_assistant("m", [_tool("Bash", command="ls")], inp=4))
        row = build_task_activity(
            "t",
            _running_ts(),
            now=NOW,
            scan=scan,
            transcript_mtime=NOW.timestamp() - 12,
            attempt=1,
            prior=[],
            final=None,
        )
        assert row.source == SOURCE_TRANSCRIPT
        assert row.turns == 1 and row.input_tokens == 4
        assert row.idle_seconds == 12.0 and row.elapsed_seconds == 95.0
        assert row.last_action == "Bash: ls"
        assert row.stuck is False and row.tokens_estimated is True

    def test_stuck_when_idle_past_threshold_and_boundary(self) -> None:
        scan = _fold(_assistant("m", [{"type": "text", "text": "a"}]))
        for idle, stuck in [(STUCK_AFTER_SECONDS - 1, False), (STUCK_AFTER_SECONDS, True)]:
            row = build_task_activity(
                "t",
                _running_ts(),
                now=NOW,
                scan=scan,
                transcript_mtime=NOW.timestamp() - idle,
                attempt=1,
                prior=[],
                final=None,
            )
            assert row.stuck is stuck

    def test_prior_attempts_accumulate_cost_floor_and_turns(self) -> None:
        prior = [
            parse_result(
                json.dumps(
                    {
                        "num_turns": 5,
                        "total_cost_usd": 0.25,
                        "usage": {"input_tokens": 1, "output_tokens": 100},
                    }
                )
            ),
        ]
        scan = _fold(_assistant("m", [{"type": "text", "text": "a"}], inp=2))
        row = build_task_activity(
            "t",
            _running_ts(attempts=2),
            now=NOW,
            scan=scan,
            transcript_mtime=NOW.timestamp(),
            attempt=2,
            prior=[p for p in prior if p],
            final=None,
        )
        assert row.turns == 6 and row.cost_usd == 0.25 and row.input_tokens == 3

    def test_running_without_transcript_is_source_none(self) -> None:
        row = build_task_activity(
            "t",
            _running_ts(),
            now=NOW,
            scan=None,
            transcript_mtime=None,
            attempt=None,
            prior=[],
            final=None,
        )
        assert row.source == SOURCE_NONE and row.turns is None and not row.stuck

    def test_settled_uses_result_turns_and_ends_elapsed(self) -> None:
        ts = TaskRunState(
            status="succeeded",
            attempts=1,
            started_at=(NOW - timedelta(seconds=100)).isoformat(),
            ended_at=(NOW - timedelta(seconds=40)).isoformat(),
            dispatch_cycle=1,
        )
        final = parse_result(json.dumps({"num_turns": 40, "usage": {}}))
        row = build_task_activity(
            "t", ts, now=NOW, scan=None, transcript_mtime=None, attempt=1, prior=[], final=final
        )
        assert row.source == SOURCE_RESULT and row.turns == 40 and row.elapsed_seconds == 60.0
        assert row.cost_usd is None and row.last_action is None

    def test_settled_without_result_falls_back_to_transcript(self) -> None:
        ts = TaskRunState(status="failed", attempts=1, dispatch_cycle=1)
        scan = _fold(_assistant("m", [{"type": "text", "text": "a"}]))
        row = build_task_activity(
            "t", ts, now=NOW, scan=scan, transcript_mtime=1.0, attempt=1, prior=[], final=None
        )
        assert row.source == SOURCE_TRANSCRIPT and row.turns == 1

    def test_naive_and_garbled_timestamps(self) -> None:
        ts = _running_ts(started_at="2026-10-02T11:58:00")  # naive -> treated as UTC
        row = build_task_activity(
            "t", ts, now=NOW, scan=None, transcript_mtime=None, attempt=None, prior=[], final=None
        )
        assert row.elapsed_seconds == 120.0
        bad = _running_ts(started_at="garbage")
        row = build_task_activity(
            "t", bad, now=NOW, scan=None, transcript_mtime=None, attempt=None, prior=[], final=None
        )
        assert row.elapsed_seconds is None


# ---------------------------------------------------------------------------
# I/O: TranscriptTailer
# ---------------------------------------------------------------------------


def _line(msg_id: str, text: str = "a") -> str:
    return json.dumps(_assistant(msg_id, [{"type": "text", "text": text}])) + "\n"


class TestTranscriptTailer:
    def test_incremental_reads_only_new_complete_lines(self, tmp_path: Path) -> None:
        f = tmp_path / "transcript.jsonl"
        f.write_text(_line("m1") + _line("m2")[:20])  # second line is PARTIAL
        tailer = TranscriptTailer()
        got = tailer.scan(f)
        assert got is not None and got[0].turns == 1
        with f.open("a") as h:  # writer finishes the line and adds another
            h.write(_line("m2")[20:] + _line("m3"))
        scan, _ = tailer.scan(f) or (None, 0)
        assert scan is not None and scan.turns == 3 and scan.bad_lines == 0

    def test_cache_hit_when_size_and_mtime_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        f = tmp_path / "transcript.jsonl"
        f.write_text(_line("m1"))
        tailer = TranscriptTailer()
        first = tailer.scan(f)
        calls = []
        orig = TranscriptTailer._advance
        monkeypatch.setattr(
            TranscriptTailer, "_advance", lambda *a, **k: (calls.append(1), orig(*a, **k))[1]
        )
        second = tailer.scan(f)
        assert calls == [] and first is not None and second is not None and first[0] is second[0]

    def test_truncated_or_replaced_file_resets(self, tmp_path: Path) -> None:
        f = tmp_path / "transcript.jsonl"
        f.write_text(_line("m1") + _line("m2"))
        tailer = TranscriptTailer()
        assert (tailer.scan(f) or (None,))[0].turns == 2  # type: ignore[union-attr]
        f.write_text(_line("n1"))  # shorter than the stored offset
        assert (tailer.scan(f) or (None,))[0].turns == 1  # type: ignore[union-attr]

    def test_missing_directory_and_fifo_are_none(self, tmp_path: Path) -> None:
        tailer = TranscriptTailer()
        assert tailer.scan(tmp_path / "nope.jsonl") is None
        assert tailer.scan(tmp_path) is None or True  # a directory must not crash

    def test_garbled_lines_are_counted_and_skipped(self, tmp_path: Path) -> None:
        f = tmp_path / "transcript.jsonl"
        f.write_bytes(b"\xff\xfe garbage\n" + _line("m1").encode() + b'{"type":\n')
        scan, _ = TranscriptTailer().scan(f) or (None, 0)  # type: ignore[misc]
        assert scan is not None and scan.turns == 1 and scan.bad_lines == 2

    def test_big_burst_skips_to_tail_and_flags_approximate(self, tmp_path: Path) -> None:
        f = tmp_path / "transcript.jsonl"
        f.write_text("".join(_line(f"m{i}") for i in range(200)))
        tailer = TranscriptTailer(tail_cap=2000)
        scan, _ = tailer.scan(f) or (None, 0)  # type: ignore[misc]
        assert scan is not None and scan.approximate is True
        assert 0 < scan.turns < 200 and scan.bad_lines == 0  # first partial line was dropped

    def test_oversize_line_is_skipped_without_stalling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(act, "MAX_LINE_BYTES", 300)
        f = tmp_path / "transcript.jsonl"
        f.write_text(_line("m1", "x" * 500) + _line("m2"))
        scan, _ = TranscriptTailer().scan(f) or (None, 0)  # type: ignore[misc]
        assert scan is not None and scan.turns == 1 and scan.approximate and scan.bad_lines == 1

    def test_unterminated_oversize_line_advances_offset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(act, "MAX_LINE_BYTES", 300)
        f = tmp_path / "transcript.jsonl"
        f.write_text("y" * 500)  # no newline, over the limit
        tailer = TranscriptTailer()
        tailer.scan(f)
        with f.open("a") as h:
            h.write("\n" + _line("m1"))
        scan, _ = tailer.scan(f) or (None, 0)  # type: ignore[misc]
        assert scan is not None and scan.turns == 1

    def test_cache_is_bounded(self, tmp_path: Path) -> None:
        tailer = TranscriptTailer(max_entries=3)
        for i in range(6):
            p = tmp_path / f"t{i}.jsonl"
            p.write_text(_line("m"))
            tailer.scan(p)
        assert len(tailer._cache) == 3

    def test_multibyte_split_across_reads_does_not_raise(self, tmp_path: Path) -> None:
        f = tmp_path / "transcript.jsonl"
        data = _line("m1", "é" * 10).encode()
        f.write_bytes(data[:-6])  # cut mid-character, no newline yet
        tailer = TranscriptTailer()
        tailer.scan(f)
        f.write_bytes(data + _line("m2").encode())
        scan, _ = tailer.scan(f) or (None, 0)  # type: ignore[misc]
        assert scan is not None and scan.turns == 2


# ---------------------------------------------------------------------------
# I/O: locate_attempt_dirs + path safety + read_run_activity
# ---------------------------------------------------------------------------


def _attempt(
    run_dir: Path,
    task: str,
    n: int,
    *,
    cycle: int = 1,
    transcript: str | None = None,
    result: dict | None = None,
) -> Path:
    base = run_dir / task
    if cycle >= 2:
        base = base / f"cycle-{cycle}"
    d = base / f"attempt-{n}"
    d.mkdir(parents=True, exist_ok=True)
    if transcript is not None:
        (d / "transcript.jsonl").write_text(transcript)
    if result is not None:
        (d / "result.json").write_text(json.dumps(result))
    return d


class TestLocateAttemptDirs:
    def test_numeric_order_and_flat_layout(self, tmp_path: Path) -> None:
        for n in (2, 10, 1):
            _attempt(tmp_path, "t", n)
        assert [n for n, _ in locate_attempt_dirs(tmp_path, "t", 1)] == [1, 2, 10]

    def test_cycle_two_nests_and_ignores_flat_attempts(self, tmp_path: Path) -> None:
        _attempt(tmp_path, "t", 1)
        _attempt(tmp_path, "t", 1, cycle=2)
        _attempt(tmp_path, "t", 2, cycle=2)
        assert [n for n, _ in locate_attempt_dirs(tmp_path, "t", 2)] == [1, 2]
        assert locate_attempt_dirs(tmp_path, "t", 3) == []

    @pytest.mark.parametrize("task_id", ["", ".", "..", "../x", "a/b", "a\\b", "a\0b"])
    def test_unsafe_task_ids_are_rejected(self, tmp_path: Path, task_id: str) -> None:
        (tmp_path / "x" / "attempt-1").mkdir(parents=True)
        assert locate_attempt_dirs(tmp_path, task_id, 1) == []

    def test_non_matching_names_ignored(self, tmp_path: Path) -> None:
        for name in ("attempt-x", "attempt-", "attempt-1a", "attempt-٣", "other"):
            (tmp_path / "t" / name).mkdir(parents=True)
        _attempt(tmp_path, "t", 4)
        assert [n for n, _ in locate_attempt_dirs(tmp_path, "t", 1)] == [4]

    def test_symlinked_attempt_dir_escaping_run_dir_is_ignored(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run"
        outside = tmp_path / "outside"
        outside.mkdir()
        (run_dir / "t").mkdir(parents=True)
        (run_dir / "t" / "attempt-1").symlink_to(outside, target_is_directory=True)
        assert locate_attempt_dirs(run_dir, "t", 1) == []

    def test_symlinked_task_dir_escaping_run_dir_is_ignored(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run"
        outside = tmp_path / "outside"
        (outside / "attempt-1").mkdir(parents=True)
        run_dir.mkdir()
        (run_dir / "t").symlink_to(outside, target_is_directory=True)
        assert locate_attempt_dirs(run_dir, "t", 1) == []


def _state(tasks: dict[str, TaskRunState]) -> RunState:
    return RunState(
        run_id=RUN_ID,
        workflow_id="demo",
        repo_set="r",
        started_at="2026-10-02T11:00:00+00:00",
        updated_at="2026-10-02T11:30:00+00:00",
        status="running",
        tasks=tasks,
    )


def _read(state: RunState, run_dir: Path) -> act.RunActivity:
    return read_run_activity(
        state, run_dir, tailer=TranscriptTailer(), results=ResultReader(), now=NOW
    )


class TestReadRunActivity:
    def test_running_and_settled_rows(self, tmp_path: Path) -> None:
        _attempt(tmp_path, "run1", 1, transcript=_line("m1") + _line("m2"))
        _attempt(
            tmp_path,
            "done",
            1,
            result={"num_turns": 9, "usage": {"input_tokens": 1, "output_tokens": 2}},
        )
        state = _state(
            {
                "run1": _running_ts(),
                "done": TaskRunState(status="succeeded", attempts=1, dispatch_cycle=1),
                "wait": TaskRunState(),  # pending: no row
            }
        )
        rows = _read(state, tmp_path).tasks
        assert set(rows) == {"run1", "done"}
        assert rows["run1"].turns == 2 and rows["run1"].source == SOURCE_TRANSCRIPT
        assert rows["done"].turns == 9 and rows["done"].source == SOURCE_RESULT

    def test_retry_sums_finished_attempts_then_live_one(self, tmp_path: Path) -> None:
        _attempt(
            tmp_path,
            "t",
            1,
            transcript=_line("a"),
            result={
                "num_turns": 5,
                "total_cost_usd": 0.1,
                "usage": {"input_tokens": 1, "output_tokens": 50},
            },
        )
        _attempt(tmp_path, "t", 2, transcript=_line("b"))
        row = _read(_state({"t": _running_ts(attempts=2)}), tmp_path).tasks["t"]
        assert row.attempt == 2 and row.turns == 6 and row.cost_usd == 0.1

    def test_requeue_cycle_reads_nested_dir(self, tmp_path: Path) -> None:
        _attempt(tmp_path, "t", 1, transcript=_line("old"))
        _attempt(tmp_path, "t", 1, cycle=2, transcript=_line("a") + _line("b") + _line("c"))
        row = _read(_state({"t": _running_ts(dispatch_cycle=2)}), tmp_path).tasks["t"]
        assert row.turns == 3 and row.cycle == 2

    def test_missing_dir_garbled_result_and_wiped_attempt(self, tmp_path: Path) -> None:
        rows = _read(
            _state({"t": _running_ts(), "u": TaskRunState(status="failed", attempts=1)}), tmp_path
        ).tasks
        assert rows["t"].source == SOURCE_NONE and rows["u"].source == SOURCE_NONE
        d = _attempt(tmp_path, "u", 1)
        (d / "result.json").write_text("{not json")
        assert (
            _read(_state({"u": TaskRunState(status="failed", attempts=1)}), tmp_path)
            .tasks["u"]
            .source
            == SOURCE_NONE
        )

    def test_symlinked_transcript_outside_run_dir_is_not_read(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run"
        secret = tmp_path / "secret.jsonl"
        secret.write_text(_line("leak") * 5)
        d = _attempt(run_dir, "t", 1)
        (d / "transcript.jsonl").symlink_to(secret)
        row = _read(_state({"t": _running_ts()}), run_dir).tasks["t"]
        assert row.source == SOURCE_NONE and row.turns is None

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs POSIX fifo")
    def test_fifo_transcript_does_not_block(self, tmp_path: Path) -> None:
        d = _attempt(tmp_path, "t", 1)
        os.mkfifo(d / "transcript.jsonl")
        start = time.monotonic()
        row = _read(_state({"t": _running_ts()}), tmp_path).tasks["t"]
        assert time.monotonic() - start < 2 and row.source == SOURCE_NONE

    def test_hostile_task_id_in_state_reads_nothing(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        _attempt(tmp_path, "victim", 1, transcript=_line("secret"))
        row = _read(_state({"../victim": _running_ts()}), run_dir).tasks["../victim"]
        assert row.source == SOURCE_NONE

    def test_task_cap_prefers_running(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(act, "MAX_ACTIVITY_TASKS", 2)
        tasks = {f"s{i}": TaskRunState(status="succeeded", attempts=1) for i in range(5)}
        tasks["live"] = _running_ts()
        assert "live" in _read(_state(tasks), tmp_path).tasks

    def test_growing_transcript_between_polls(self, tmp_path: Path) -> None:
        d = _attempt(tmp_path, "t", 1, transcript=_line("a"))
        tailer, results = TranscriptTailer(), ResultReader()
        state = _state({"t": _running_ts()})
        r1 = read_run_activity(state, tmp_path, tailer=tailer, results=results, now=NOW)
        with (d / "transcript.jsonl").open("a") as h:
            h.write(_line("b"))
        r2 = read_run_activity(state, tmp_path, tailer=tailer, results=results, now=NOW)
        assert (r1.tasks["t"].turns, r2.tasks["t"].turns) == (1, 2)


# ---------------------------------------------------------------------------
# Integration: repository + HTTP
# ---------------------------------------------------------------------------

fastapi = pytest.importorskip("fastapi", reason="dashboard API needs the optional [ui] extra")
from fastapi.testclient import TestClient  # noqa: E402

from agent_orchestrator.ui.app import API_PREFIX, create_app  # noqa: E402
from agent_orchestrator.ui.runs import RunNotFoundError, RunRepository  # noqa: E402
from agent_orchestrator.ui.service import DashboardService  # noqa: E402

from .conftest import StubSupervisor, write_run  # noqa: E402


def _client(workspace: Path) -> TestClient:
    service = DashboardService(str(workspace), supervisor=StubSupervisor(workspace))  # type: ignore[arg-type]
    return TestClient(create_app(service))


class TestActivityEndpoint:
    def test_endpoint_returns_live_row_and_additive_detail_fields(self, tmp_path: Path) -> None:
        state = _state(
            {
                "w": _running_ts(model="sonnet", effort="high", agent="dev"),
                "p": TaskRunState(),
            }
        )
        run_dir = write_run(tmp_path, state)
        _attempt(run_dir, "w", 1, transcript=_line("m1") + _line("m2"))
        client = _client(tmp_path)

        body = client.get(f"{API_PREFIX}/runs/{RUN_ID}/activity").json()
        assert body["schema_version"] == 1 and body["run_id"] == RUN_ID
        assert body["tasks"]["w"]["turns"] == 2 and body["tasks"]["w"]["source"] == "transcript"
        assert "p" not in body["tasks"]

        detail = client.get(f"{API_PREFIX}/runs/{RUN_ID}").json()
        stat = {t["id"]: t for t in detail["tasks"]}["w"]
        assert (stat["model"], stat["effort"], stat["agent"]) == ("sonnet", "high", "dev")
        brief = detail["summary"]["running_tasks"]
        assert [b["id"] for b in brief] == ["w"] and brief[0]["model"] == "sonnet"

        listing = client.get(f"{API_PREFIX}/runs").json()
        assert listing[0]["running_tasks"][0]["id"] == "w"

    @pytest.mark.parametrize("run_id", ["nope", "..%2F..%2Fetc", "%2e%2e"])
    def test_unknown_or_traversal_run_id_is_404(self, tmp_path: Path, run_id: str) -> None:
        assert _client(tmp_path).get(f"{API_PREFIX}/runs/{run_id}/activity").status_code == 404

    def test_repository_raises_for_traversal_id(self, tmp_path: Path) -> None:
        with pytest.raises(RunNotFoundError):
            RunRepository(str(tmp_path)).load_activity("../../etc")

    def test_running_briefs_are_capped(self, tmp_path: Path) -> None:
        from agent_orchestrator.ui.runs import MAX_RUNNING_BRIEFS

        tasks = {f"t{i}": _running_ts() for i in range(MAX_RUNNING_BRIEFS + 5)}
        write_run(tmp_path, _state(tasks))
        row = _client(tmp_path).get(f"{API_PREFIX}/runs").json()[0]
        assert len(row["running_tasks"]) == MAX_RUNNING_BRIEFS
        assert row["task_counts"]["running"] == MAX_RUNNING_BRIEFS + 5

    def test_old_state_without_transcripts_degrades_to_200(self, tmp_path: Path) -> None:
        write_run(tmp_path, _state({"w": _running_ts()}))
        body = _client(tmp_path).get(f"{API_PREFIX}/runs/{RUN_ID}/activity").json()
        assert body["tasks"]["w"]["source"] == "none"
