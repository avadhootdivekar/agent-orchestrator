"""Engine behaviour for awaiting_input: both producers, answer detection, exit-waiting,
prepare_resume, non-blocking assumptions (docs-md/awaiting-input-hld.md §6, §7, §10)."""

from __future__ import annotations

import json
from pathlib import Path

from agent_orchestrator import inputs
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors.base import Executor
from agent_orchestrator.executors.prompt import build_prompt
from agent_orchestrator.models import (
    InputField,
    InputRequestSpec,
    TaskContext,
    TaskResult,
    TaskSpec,
)
from tests.test_engine import _fake_agents, _fake_reposets, _make_workspace, _task, _workflow

ASK = InputRequestSpec(
    prompt="Which database?",
    fields=[InputField(name="db", type="choice", choices=["postgres", "sqlite"], default="sqlite")],
)


class _AskingExecutor(Executor):
    """First dispatch writes input-request.json and exits 0; later dispatches write outputs."""

    def __init__(self, spec: dict | str | None = None, always_ask: bool = False):
        self.ctxs: list[TaskContext] = []
        self.spec = spec if spec is not None else ASK.model_dump()
        self.always_ask = always_ask

    def execute(self, ctx: TaskContext) -> TaskResult:
        self.ctxs.append(ctx)
        asking = (ctx.task_id == "ask") and (self.always_ask or len(self._mine()) == 1)
        if asking and ctx.input_request_path:
            Path(ctx.input_request_path).parent.mkdir(parents=True, exist_ok=True)
            body = self.spec if isinstance(self.spec, str) else json.dumps(self.spec)
            Path(ctx.input_request_path).write_text(body)
        else:
            for p in ctx.output_paths:
                Path(p).parent.mkdir(parents=True, exist_ok=True)
                Path(p).write_text("ok")
        return TaskResult(task_id=ctx.task_id, status="succeeded", attempts=1)

    def _mine(self) -> list[TaskContext]:
        return [c for c in self.ctxs if c.task_id == "ask"]


def _asker(**kw) -> TaskSpec:
    t = _task("ask", outputs=["output/ask.txt"])
    return t.model_copy(update={"can_request_input": True, **kw})


def _setup(tmp_path, tasks, executor, **orch_kw):
    store, rs = _make_workspace(tmp_path)
    wf = _workflow(tasks)
    orch = Orchestrator(executor, store, rs, sleeper=lambda s: None, **orch_kw)
    return wf, rs, store, orch


def _run(tmp_path, tasks, executor, **kw):
    wf, rs, store, orch = _setup(tmp_path, tasks, executor, **kw)
    state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())
    return wf, rs, store, state


def _resume(tmp_path, wf, rs, store, executor, **kw):
    orch = Orchestrator(executor, store, rs, sleeper=lambda s: None, **kw)
    existing = rs.prepare_resume(rs.load(_last_run_id(rs, tmp_path)), wf)
    return orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=existing)


def _last_run_id(rs, tmp_path) -> str:
    return sorted(p.name for p in (tmp_path / ".orchestrator" / "runs").iterdir())[-1]


def _run_dir(tmp_path, state) -> Path:
    return tmp_path / ".orchestrator" / "runs" / state.run_id


# ---- producer (b): task-written artifact ------------------------------------------------------


def test_artifact_request_parks_task_and_run_stays_running(tmp_path) -> None:
    tasks = [_asker(), _task("after", depends_on=["ask"], outputs=["output/after.txt"])]
    ex = _AskingExecutor()
    _, _, _, state = _run(tmp_path, tasks, ex)
    assert state.tasks["ask"].status == "awaiting_input"
    assert state.tasks["after"].status == "pending"
    assert state.status == "running"  # waiting, NOT failed (ADR-1)
    (req,) = state.input_requests.values()
    assert req.state == "open" and req.task_id == "ask" and req.prompt == "Which database?"
    assert inputs.load_request(_run_dir(tmp_path, state), req.request_id).state == "open"
    assert state.tasks["ask"].attempts <= 1  # no retry attempt consumed
    status = json.loads((_run_dir(tmp_path, state) / "status.json").read_text())
    assert status["waiting_for_input"] is True
    assert status["counts"]["awaiting_input"] == 1
    assert status["input_requests"][0]["request_id"] == req.request_id


def test_independent_branch_still_completes_while_parked(tmp_path) -> None:
    tasks = [_asker(), _task("other", outputs=["output/other.txt"])]
    _, _, _, state = _run(tmp_path, tasks, _AskingExecutor())
    assert state.tasks["other"].status == "succeeded"
    assert state.tasks["ask"].status == "awaiting_input"
    assert state.status == "running"


def test_answer_then_resume_redispatches_with_answer_path(tmp_path) -> None:
    tasks = [_asker(), _task("after", depends_on=["ask"], outputs=["output/after.txt"])]
    ex = _AskingExecutor()
    wf, rs, store, state = _run(tmp_path, tasks, ex)
    (req,) = state.input_requests.values()
    inputs.answer_request(_run_dir(tmp_path, state), req.request_id, {"db": "postgres"})

    final = _resume(tmp_path, wf, rs, store, ex)
    assert final.status == "succeeded"
    assert final.tasks["ask"].status == "succeeded" and final.tasks["after"].status == "succeeded"
    second = ex._mine()[1]
    answer_file = str(inputs.request_path(_run_dir(tmp_path, state), req.request_id))
    assert second.input_answer_path == answer_file
    assert answer_file in build_prompt(second)
    assert "data, not instructions" in build_prompt(second)
    assert ex._mine()[0].input_answer_path is None
    assert final.input_requests[req.request_id].state == "answered"


def test_resume_without_answer_stays_waiting_and_does_not_reask(tmp_path) -> None:
    tasks = [_asker()]
    ex = _AskingExecutor()
    wf, rs, store, state = _run(tmp_path, tasks, ex)
    again = _resume(tmp_path, wf, rs, store, ex)
    assert again.tasks["ask"].status == "awaiting_input"
    assert again.status == "running"
    assert len(again.input_requests) == 1  # still the one request
    assert len(ex._mine()) == 1  # executor was not re-run


def test_orphan_request_cancelled_on_resume(tmp_path) -> None:
    tasks = [_asker()]
    ex = _AskingExecutor()
    wf, rs, store, state = _run(tmp_path, tasks, ex)
    wf2 = _workflow([_task("only", outputs=["output/o.txt"])])
    existing = rs.prepare_resume(rs.load(state.run_id), wf2)
    (req,) = existing.input_requests.values()
    assert req.state == "cancelled"
    assert inputs.load_request(_run_dir(tmp_path, state), req.request_id).state == "cancelled"


def test_regular_failed_task_still_halts_run(tmp_path) -> None:
    class Boom(Executor):
        def execute(self, ctx):
            return TaskResult(task_id=ctx.task_id, status="failed", attempts=1, error="x")

    _, _, _, state = _run(tmp_path, [_task("a", outputs=["output/a.txt"])], Boom())
    assert state.status == "failed"


def test_invalid_request_file_fails_task_not_waits(tmp_path) -> None:
    _, _, _, state = _run(tmp_path, [_asker()], _AskingExecutor(spec='{"prompt": ""}'))
    assert state.tasks["ask"].status == "failed"
    assert state.status == "failed" and not state.input_requests


def test_not_advertised_without_can_request_input(tmp_path) -> None:
    ex = _AskingExecutor()
    _run(tmp_path, [_task("ask", outputs=["output/ask.txt"])], ex)
    assert all(c.input_request_path is None for c in ex.ctxs)
    assert "input-request.json" not in build_prompt(ex.ctxs[0])


def test_request_file_ignored_when_task_did_not_opt_in(tmp_path) -> None:
    class Sneaky(_AskingExecutor):
        def execute(self, ctx):
            Path(ctx.output_dir).mkdir(parents=True, exist_ok=True)
            (Path(ctx.output_dir) / "input-request.json").write_text(json.dumps(ASK.model_dump()))
            return super().execute(ctx)

    _, _, _, state = _run(tmp_path, [_task("ask", outputs=["output/ask.txt"])], Sneaky())
    assert state.status == "succeeded" and not state.input_requests


def test_runaway_reasking_task_fails(tmp_path) -> None:
    tasks = [_asker()]
    ex = _AskingExecutor(always_ask=True)
    _, _, _, state = _run(tmp_path, tasks, ex, input_mode="nonblocking")
    assert state.tasks["ask"].status == "failed"
    assert state.status == "failed"


# ---- producer (a): spec-level input block ----------------------------------------------------


def _gate_tasks() -> list[TaskSpec]:
    gate = _task("gate", outputs=["output/answer.json"]).model_copy(update={"input": ASK})
    return [gate, _task("after", depends_on=["gate"], outputs=["output/after.txt"])]


def test_input_block_parks_without_running_an_executor(tmp_path) -> None:
    ex = _AskingExecutor()
    _, _, _, state = _run(tmp_path, _gate_tasks(), ex)
    assert state.tasks["gate"].status == "awaiting_input"
    assert state.status == "running"
    assert ex.ctxs == []


def test_input_block_answer_becomes_output_artifact(tmp_path) -> None:
    ex = _AskingExecutor()
    wf, rs, store, state = _run(tmp_path, _gate_tasks(), ex)
    (req,) = state.input_requests.values()
    inputs.answer_request(_run_dir(tmp_path, state), req.request_id, {"db": "postgres"})
    final = _resume(tmp_path, wf, rs, store, ex)
    assert final.status == "succeeded"
    answer = json.loads((tmp_path / "output" / "answer.json").read_text())
    assert answer["values"] == {"db": "postgres"} and answer["assumed"] is False
    assert (_run_dir(tmp_path, state) / "gate" / "answer.json").is_file()
    assert [c.task_id for c in ex.ctxs] == ["after"]


# ---- non-blocking -------------------------------------------------------------------------------


def test_nonblocking_input_block_assumes_defaults_and_proceeds(tmp_path) -> None:
    _, _, _, state = _run(tmp_path, _gate_tasks(), _AskingExecutor(), input_mode="nonblocking")
    assert state.status == "succeeded" and state.input_mode == "nonblocking"
    (req,) = state.input_requests.values()
    assert req.state == "assumed" and req.answer and req.answer.assumed
    assert json.loads((tmp_path / "output" / "answer.json").read_text())["values"] == {
        "db": "sqlite"
    }
    status = json.loads((_run_dir(tmp_path, state) / "status.json").read_text())
    assert status["assumptions"] == ["gate.db='sqlite' (default)"]
    assert "waiting_for_input" not in status


def test_nonblocking_artifact_request_redispatches_with_assumption(tmp_path) -> None:
    ex = _AskingExecutor()
    _, _, _, state = _run(tmp_path, [_asker()], ex, input_mode="nonblocking")
    assert state.status == "succeeded"
    assert len(ex._mine()) == 2
    (req,) = state.input_requests.values()
    assert ex._mine()[1].input_answer_path == str(
        inputs.request_path(_run_dir(tmp_path, state), req.request_id)
    )


def test_nonblocking_missing_required_default_fails_task_not_hangs(tmp_path) -> None:
    spec = InputRequestSpec(prompt="Who?", fields=[InputField(name="who")])
    gate = _task("gate").model_copy(update={"input": spec})
    _, _, _, state = _run(tmp_path, [gate], _AskingExecutor(), input_mode="nonblocking")
    assert state.tasks["gate"].status == "failed" and state.status == "failed"
    (req,) = inputs.list_requests(_run_dir(tmp_path, state), task_id="gate")
    assert req.state == "cancelled"  # no dangling open request


def test_switching_to_nonblocking_on_resume_converts_open_requests(tmp_path) -> None:
    ex = _AskingExecutor()
    wf, rs, store, state = _run(tmp_path, _gate_tasks(), ex)
    assert state.status == "running"
    final = _resume(tmp_path, wf, rs, store, ex, input_mode="nonblocking")
    assert final.status == "succeeded"
    (req,) = final.input_requests.values()
    assert req.state == "assumed"


def test_default_mode_is_blocking(tmp_path) -> None:
    _, _, _, state = _run(tmp_path, _gate_tasks(), _AskingExecutor())
    assert state.input_mode == "blocking"


def test_status_json_key_set_unchanged_for_runs_without_requests(tmp_path) -> None:
    _, _, _, state = _run(tmp_path, [_task("a", outputs=["output/a.txt"])], _AskingExecutor())
    status = json.loads((_run_dir(tmp_path, state) / "status.json").read_text())
    for key in ("waiting_for_input", "input_requests", "assumptions"):
        assert key not in status
    assert "awaiting_input" not in status["counts"]
