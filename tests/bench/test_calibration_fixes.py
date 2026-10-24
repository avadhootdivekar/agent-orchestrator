"""T-Cb8Rk2 harness fixes found during calibration (fake tier):

1. a jsonl probe may send ANY JSON value as the request line (the job-queue
   `trap-invalid-input` probe sends `[1, 2]` / `"enqueue"`); the grader used to reject it as a
   probe-definition error at traps=3, turning every such task into an infra error;
2. when the subject finished and the grader raised, the run record keeps the subject's real
   cost/tokens instead of $0.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.bench.blackbox import HttpTransport, _validate_op_expect
from agent_orchestrator.bench.errors import GraderError
from agent_orchestrator.bench.graders import Grader, GradeResult
from agent_orchestrator.bench.registries import GRADER_REGISTRY, SUBJECT_REGISTRY
from agent_orchestrator.bench.runner import run_suite
from agent_orchestrator.bench.sandbox import Handle
from agent_orchestrator.bench.spec import BenchTask
from agent_orchestrator.bench.subjects import Subject, SubjectResult
from agent_orchestrator.bench.workspace import RunContext


@pytest.mark.parametrize("value", [[1, 2], "enqueue", 5, None, {"op": "ping"}])
def test_jsonl_op_accepts_any_json_value(value: Any) -> None:
    _validate_op_expect({"op": {"json": value}, "expect": {}}, "jsonl", "probe")


def test_jsonl_op_still_requires_the_json_key() -> None:
    with pytest.raises(GraderError, match="needs a `json` value"):
        _validate_op_expect({"op": {}, "expect": {}}, "jsonl", "probe")


def test_grader_error_keeps_the_subjects_spend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suite_factory: Callable[..., Path]
) -> None:
    class _Paid(Subject):
        def run(self, task: BenchTask, ctx: RunContext) -> SubjectResult:
            return SubjectResult(
                status="succeeded",
                wall_clock_seconds=1.0,
                cost_usd=0.42,
                input_tokens=10,
                output_tokens=20,
                turns=3,
                capture_dir=ctx.capture_dir,
            )

    class _Broken(Grader):
        def _grade(self, cfg: Any, ctx: Any) -> GradeResult:
            raise GraderError("probes definition error")

    monkeypatch.setitem(SUBJECT_REGISTRY, "fake", _Paid)
    monkeypatch.setitem(GRADER_REGISTRY, "fake", _Broken)
    suite = suite_factory(
        suite_id="fix-cost",
        tasks=[
            {
                "id": "t1",
                "category": "bugfix",
                "instruction": "tasks/t1/instruction.md",
                "fixture": "tasks/t1/fixture",
                "grader": {"type": "fake"},
            }
        ],
    )
    subject = tmp_path / "s.json"
    subject.write_text(json.dumps({"version": "1.0", "id": "paid", "type": "fake"}))
    record = run_suite(suite, subject, out_dir=tmp_path / "res")
    (task,) = record.tasks
    assert task.subject_status == "error" and "probes definition error" in (task.raw_error or "")
    assert task.cost_usd == pytest.approx(0.42)
    assert task.output_tokens == 20 and task.turns == 3
    assert record.aggregate.total_cost_usd == pytest.approx(0.42)


# ---------------------------------------------------------------------------
# 3. HTTP transport: direct container address + abortive close (no TIME_WAIT pile-up)
# ---------------------------------------------------------------------------


class _OkHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802 -- http.server API
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = b'{"ok": true}'
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        return


@pytest.fixture()
def local_server() -> Any:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OkHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def test_http_transport_prefers_the_direct_address(local_server: int) -> None:
    # the published port is bogus: only the direct address can answer
    handle = Handle(kind="http", port=1, direct_host="127.0.0.1", direct_port=local_server)
    out = HttpTransport(handle).send({"method": "POST", "path": "/x", "json": {"a": 1}}, 5.0)
    assert out.status == 201 and out.body == {"ok": True}


def test_http_transport_many_short_connections_do_not_fail(local_server: int) -> None:
    transport = HttpTransport(Handle(kind="http", port=local_server))
    for _ in range(2000):
        assert transport.send({"method": "POST", "path": "/x", "json": {}}, 5.0).status == 201
