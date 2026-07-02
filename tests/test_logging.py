"""Tests for T-pd2vu2 — structured JSON logging (logging_setup.py)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from agent_orchestrator.logging_setup import (
    JSONFormatter,
    attach_run_handler,
    detach_run_handler,
    get_run_logger,
)

# ---------------------------------------------------------------------------
# JSONFormatter unit tests
# ---------------------------------------------------------------------------


def _make_record(
    msg: str = "hello",
    level: int = logging.INFO,
    extra: dict | None = None,
    exc_info: tuple | None = None,
) -> logging.LogRecord:
    """Build a minimal LogRecord."""
    record = logging.LogRecord(
        name="agent_orchestrator.test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=exc_info,
    )
    for k, v in (extra or {}).items():
        setattr(record, k, v)
    return record


class TestJSONFormatterFields:
    """AC-1: JSONFormatter emits valid JSON with required fields."""

    def test_required_fields_always_present(self) -> None:
        fmt = JSONFormatter()
        record = _make_record("test message")
        line = fmt.format(record)
        obj = json.loads(line)

        assert "ts" in obj, "ts field missing"
        assert "level" in obj, "level field missing"
        assert "logger" in obj, "logger field missing"
        assert "msg" in obj, "msg field missing"
        assert obj["msg"] == "test message"
        assert obj["level"] == "INFO"

    def test_ts_is_iso8601_utc(self) -> None:
        from datetime import datetime

        fmt = JSONFormatter()
        record = _make_record("ts test")
        obj = json.loads(fmt.format(record))
        # Should parse without error and end with +00:00 or Z
        parsed = datetime.fromisoformat(obj["ts"].replace("Z", "+00:00"))
        assert parsed.utcoffset() is not None

    def test_run_id_injected_via_extra(self) -> None:
        fmt = JSONFormatter()
        record = _make_record("run", extra={"run_id": "my-run-1"})
        obj = json.loads(fmt.format(record))
        assert obj["run_id"] == "my-run-1"

    def test_task_id_injected_via_extra(self) -> None:
        fmt = JSONFormatter()
        record = _make_record("task", extra={"run_id": "r1", "task_id": "task-a"})
        obj = json.loads(fmt.format(record))
        assert obj["task_id"] == "task-a"

    def test_event_field_injected_via_extra(self) -> None:
        fmt = JSONFormatter()
        record = _make_record("event test", extra={"event": "task.start"})
        obj = json.loads(fmt.format(record))
        assert obj["event"] == "task.start"

    def test_promoted_optional_fields(self) -> None:
        fmt = JSONFormatter()
        record = _make_record(
            "promoted",
            extra={
                "run_id": "r",
                "task_id": "t",
                "event": "task.end",
                "attempt": 2,
                "status": "succeeded",
                "exit_code": 0,
            },
        )
        obj = json.loads(fmt.format(record))
        assert obj["attempt"] == 2
        assert obj["status"] == "succeeded"
        assert obj["exit_code"] == 0

    def test_exception_info_serialised_to_exc_field(self) -> None:
        fmt = JSONFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = _make_record("exc test", exc_info=exc_info)
        obj = json.loads(fmt.format(record))
        assert "exc" in obj
        assert "ValueError" in obj["exc"]
        assert "boom" in obj["exc"]

    def test_warning_level(self) -> None:
        fmt = JSONFormatter()
        record = _make_record("warn", level=logging.WARNING)
        obj = json.loads(fmt.format(record))
        assert obj["level"] == "WARNING"

    def test_output_is_single_line(self) -> None:
        """Each record must be one JSON object on one line — no embedded newlines."""
        fmt = JSONFormatter()
        record = _make_record("single line check")
        line = fmt.format(record)
        assert "\n" not in line


# ---------------------------------------------------------------------------
# attach / detach lifecycle tests
# ---------------------------------------------------------------------------


class TestHandlerLifecycle:
    """AC-3, AC-4: handlers attached/detached correctly; no leakage between runs."""

    def test_attach_creates_handlers(self, tmp_path: Path) -> None:
        run_id = "lifecycle-test-1"
        log_path = str(tmp_path / ".orchestrator" / "runs" / run_id / "run.log")
        pkg_logger = logging.getLogger("agent_orchestrator")
        initial_count = len(pkg_logger.handlers)

        try:
            handler = attach_run_handler(run_id, log_path)
            assert handler is not None
            # Two handlers added (file + console)
            assert len(pkg_logger.handlers) == initial_count + 2
        finally:
            detach_run_handler(run_id)

    def test_detach_removes_handlers(self, tmp_path: Path) -> None:
        run_id = "lifecycle-test-2"
        log_path = str(tmp_path / ".orchestrator" / "runs" / run_id / "run.log")
        pkg_logger = logging.getLogger("agent_orchestrator")
        initial_count = len(pkg_logger.handlers)

        attach_run_handler(run_id, log_path)
        detach_run_handler(run_id)

        assert len(pkg_logger.handlers) == initial_count

    def test_detach_idempotent_when_not_attached(self) -> None:
        """detach_run_handler must not raise if run_id was never attached."""
        detach_run_handler("nonexistent-run-id-xyz")  # must not raise

    def test_attach_idempotent_same_run_id(self, tmp_path: Path) -> None:
        """Calling attach twice for the same run_id is a no-op (returns same handler)."""
        run_id = "idempotent-attach"
        log_path = str(tmp_path / ".orchestrator" / "runs" / run_id / "run.log")
        pkg_logger = logging.getLogger("agent_orchestrator")
        initial_count = len(pkg_logger.handlers)

        try:
            h1 = attach_run_handler(run_id, log_path)
            h2 = attach_run_handler(run_id, log_path)
            assert h1 is h2
            # Still only two additional handlers (not four)
            assert len(pkg_logger.handlers) == initial_count + 2
        finally:
            detach_run_handler(run_id)

    def test_handler_detached_even_on_exception(self, tmp_path: Path) -> None:
        """Simulates the finally pattern used in Orchestrator.run()."""
        run_id = "exception-run"
        log_path = str(tmp_path / ".orchestrator" / "runs" / run_id / "run.log")
        pkg_logger = logging.getLogger("agent_orchestrator")
        initial_count = len(pkg_logger.handlers)

        try:
            attach_run_handler(run_id, log_path)
            raise RuntimeError("simulated run failure")
        except RuntimeError:
            pass
        finally:
            detach_run_handler(run_id)

        # After finally, handler count is back to baseline
        assert len(pkg_logger.handlers) == initial_count

    def test_no_cross_contamination_between_runs(self, tmp_path: Path) -> None:
        """Records from run-A must not appear in run-B's log file (AC-3)."""
        run_a_log = tmp_path / ".orchestrator" / "runs" / "run-A" / "run.log"
        run_b_log = tmp_path / ".orchestrator" / "runs" / "run-B" / "run.log"

        try:
            attach_run_handler("run-A", str(run_a_log))
            log_a = get_run_logger("run-A")
            log_a.info("message only for A", extra={"event": "run-A-event"})
        finally:
            detach_run_handler("run-A")

        try:
            attach_run_handler("run-B", str(run_b_log))
            log_b = get_run_logger("run-B")
            log_b.info("message only for B", extra={"event": "run-B-event"})
        finally:
            detach_run_handler("run-B")

        a_text = run_a_log.read_text()
        b_text = run_b_log.read_text()

        assert "run-A-event" in a_text
        assert "run-B-event" not in a_text
        assert "run-B-event" in b_text
        assert "run-A-event" not in b_text


# ---------------------------------------------------------------------------
# get_run_logger tests
# ---------------------------------------------------------------------------


class TestGetRunLogger:
    def test_injects_run_id(self, tmp_path: Path) -> None:
        run_id = "logger-test-run"
        log_path = str(tmp_path / "run.log")
        try:
            attach_run_handler(run_id, log_path)
            adapter = get_run_logger(run_id)
            adapter.info("adapter test", extra={"event": "adapter.test"})
        finally:
            detach_run_handler(run_id)

        lines = [json.loads(ln) for ln in Path(log_path).read_text().splitlines() if ln.strip()]
        assert any(r.get("run_id") == run_id for r in lines)

    def test_injects_task_id(self, tmp_path: Path) -> None:
        run_id = "logger-task-test"
        log_path = str(tmp_path / "run.log")
        try:
            attach_run_handler(run_id, log_path)
            adapter = get_run_logger(run_id, task_id="my-task")
            adapter.info("task adapter test", extra={"event": "task.test"})
        finally:
            detach_run_handler(run_id)

        lines = [json.loads(ln) for ln in Path(log_path).read_text().splitlines() if ln.strip()]
        assert any(r.get("task_id") == "my-task" for r in lines)

    def test_no_task_id_when_omitted(self, tmp_path: Path) -> None:
        run_id = "logger-notask"
        log_path = str(tmp_path / "run.log")
        try:
            attach_run_handler(run_id, log_path)
            adapter = get_run_logger(run_id)
            adapter.info("no task id", extra={"event": "no.task"})
        finally:
            detach_run_handler(run_id)

        lines = [json.loads(ln) for ln in Path(log_path).read_text().splitlines() if ln.strip()]
        # run_id should be present, task_id should NOT
        assert all(r.get("run_id") == run_id for r in lines)
        assert all("task_id" not in r for r in lines)
