"""Awaiting-input models and the shared inputs.py validator/writer (awaiting-input-hld.md)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_orchestrator import inputs
from agent_orchestrator.models import (
    INPUT_MAX_CHOICES,
    INPUT_MAX_FIELDS,
    InputField,
    InputRequestSpec,
    RunState,
    TaskRunState,
    assumption_lines,
    run_waiting_for_input,
)

SPEC = InputRequestSpec(
    prompt="Which database?",
    fields=[
        InputField(name="db", type="choice", choices=["postgres", "sqlite"], default="sqlite"),
        InputField(name="note", type="text", required=False),
        InputField(name="ok", type="bool", default=True),
    ],
)


def _open(tmp_path: Path, spec: InputRequestSpec = SPEC, task: str = "t1"):
    return inputs.open_request(tmp_path, task, spec)


# ---- models ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "Bad-Name"},
        {"name": "x", "type": "choice"},  # choice without choices
        {"name": "x", "type": "choice", "choices": ["a"], "default": "b"},  # default not a choice
        {"name": "x", "type": "text", "choices": ["a"]},  # choices on a text field
        {"name": "x", "type": "bool", "default": "yes"},  # wrong default type
        {"name": "x", "type": "choice", "choices": ["a", "a"]},  # duplicate choices
        {"name": "x", "type": "choice", "choices": [str(i) for i in range(INPUT_MAX_CHOICES + 1)]},
        {"name": "x", "max_chars": 0},
    ],
)
def test_field_validation_rejects(kwargs) -> None:
    with pytest.raises(ValidationError):
        InputField(**kwargs)


def test_spec_validation_limits() -> None:
    with pytest.raises(ValidationError):
        InputRequestSpec(prompt="  ")
    with pytest.raises(ValidationError):
        InputRequestSpec(prompt="p" * 2001)
    with pytest.raises(ValidationError):
        InputRequestSpec(prompt="p", fields=[InputField(name="a"), InputField(name="a")])
    with pytest.raises(ValidationError):
        InputRequestSpec(
            prompt="p", fields=[InputField(name=f"f{i}") for i in range(INPUT_MAX_FIELDS + 1)]
        )


def test_old_state_json_loads_without_new_fields() -> None:
    raw = RunState(run_id="r", workflow_id="w", started_at="x", updated_at="x").model_dump(
        exclude={"input_mode", "input_requests"}
    )
    state = RunState.model_validate(raw)
    assert state.input_mode == "blocking" and state.input_requests == {}


def test_task_status_accepts_awaiting_input() -> None:
    assert TaskRunState(status="awaiting_input").status == "awaiting_input"


# ---- open / answer --------------------------------------------------------------------------


def test_open_request_engine_generates_id_and_state(tmp_path) -> None:
    req = _open(tmp_path)
    assert req.request_id == "q-t1-1" and req.state == "open" and req.answer is None
    assert _open(tmp_path).request_id == "q-t1-2"  # a re-ask gets a new id
    assert inputs.request_path(tmp_path, "q-t1-1").is_file()


def test_unsafe_task_id_never_reaches_a_path(tmp_path) -> None:
    req = _open(tmp_path, task="../../etc/Passwd x")
    assert "/" not in req.request_id and ".." not in req.request_id.replace("-", "")
    assert inputs.request_path(tmp_path, req.request_id).parent == inputs.inputs_dir(tmp_path)
    with pytest.raises(inputs.InputError):
        inputs.request_path(tmp_path, "../evil")


def test_answer_valid_coerces_bool_and_applies_defaults(tmp_path) -> None:
    req = _open(tmp_path)
    done = inputs.answer_request(tmp_path, req.request_id, {"db": "postgres", "ok": "no"})
    assert done.state == "answered" and done.answer is not None
    assert done.answer.values == {"db": "postgres", "ok": False}
    assert done.answer.assumed is False and done.answer.source == "cli"
    assert inputs.load_request(tmp_path, req.request_id).state == "answered"


@pytest.mark.parametrize(
    "values",
    [
        {"db": "mysql"},
        {"nope": "x"},
        {"ok": "maybe"},
        {"note": 5},
        {"db": "sqlite", "note": "x" * 5000},
    ],
)
def test_answer_invalid_values_rejected_and_request_stays_open(tmp_path, values) -> None:
    req = _open(tmp_path)
    with pytest.raises(inputs.InputError):
        inputs.answer_request(tmp_path, req.request_id, values)
    assert inputs.load_request(tmp_path, req.request_id).state == "open"


def test_required_field_without_default_must_be_supplied(tmp_path) -> None:
    spec = InputRequestSpec(prompt="p", fields=[InputField(name="who")])
    req = _open(tmp_path, spec)
    with pytest.raises(inputs.InputError, match="required"):
        inputs.answer_request(tmp_path, req.request_id, {})
    with pytest.raises(inputs.InputError, match="required"):
        inputs.answer_request(tmp_path, req.request_id, {"who": "  "})


def test_reanswer_idempotent_only_when_identical(tmp_path) -> None:
    req = _open(tmp_path)
    inputs.answer_request(tmp_path, req.request_id, {"db": "postgres"})
    again = inputs.answer_request(tmp_path, req.request_id, {"db": "postgres"})
    assert again.state == "answered"
    with pytest.raises(inputs.InputNotOpenError):
        inputs.answer_request(tmp_path, req.request_id, {"db": "sqlite"})


def test_unknown_request(tmp_path) -> None:
    with pytest.raises(inputs.InputUnknownError):
        inputs.answer_request(tmp_path, "q-nope-1", {})


def test_cancelled_request_cannot_be_answered(tmp_path) -> None:
    req = _open(tmp_path)
    inputs.cancel_request(tmp_path, req.request_id)
    with pytest.raises(inputs.InputNotOpenError):
        inputs.answer_request(tmp_path, req.request_id, {"db": "sqlite"})


def test_assume_defaults_records_assumption(tmp_path) -> None:
    req = _open(tmp_path)
    done = inputs.assume_defaults(tmp_path, req.request_id)
    assert done.state == "assumed" and done.answer is not None
    assert done.answer.assumed and done.answer.source == "default"
    assert done.answer.values == {"db": "sqlite", "ok": True}  # optional 'note' w/o default omitted
    state = RunState(run_id="r", workflow_id="w", started_at="x", updated_at="x")
    state.input_requests[done.request_id] = done
    assert "t1.db='sqlite' (default)" in assumption_lines(state)


def test_assume_defaults_missing_required_default_raises(tmp_path) -> None:
    req = _open(tmp_path, InputRequestSpec(prompt="p", fields=[InputField(name="who")]))
    with pytest.raises(inputs.InputError, match="no default for required field 'who'"):
        inputs.assume_defaults(tmp_path, req.request_id)
    assert inputs.load_request(tmp_path, req.request_id).state == "open"


def test_list_requests_filters(tmp_path) -> None:
    a = _open(tmp_path, task="a")
    _open(tmp_path, task="b")
    inputs.answer_request(tmp_path, a.request_id, {"db": "sqlite"})
    assert [r.task_id for r in inputs.list_requests(tmp_path, state="open")] == ["b"]
    assert len(inputs.list_requests(tmp_path)) == 2
    assert inputs.list_requests(tmp_path / "missing") == []


# ---- file safety ----------------------------------------------------------------------------


def test_corrupt_file_not_overwritten(tmp_path) -> None:
    req = _open(tmp_path)
    p = inputs.request_path(tmp_path, req.request_id)
    p.write_text("{not json")
    with pytest.raises(inputs.InputError, match="corrupt"):
        inputs.answer_request(tmp_path, req.request_id, {"db": "sqlite"})
    assert p.read_text() == "{not json"


def test_symlinked_request_file_refused(tmp_path) -> None:
    req = _open(tmp_path)
    p = inputs.request_path(tmp_path, req.request_id)
    target = tmp_path / "elsewhere.json"
    target.write_text(p.read_text())
    p.unlink()
    os.symlink(target, p)
    with pytest.raises(inputs.InputError, match="symlink"):
        inputs.answer_request(tmp_path, req.request_id, {"db": "sqlite"})


def test_restore_request_rebuilds_missing_file_only(tmp_path) -> None:
    req = _open(tmp_path)
    p = inputs.request_path(tmp_path, req.request_id)
    p.unlink()
    inputs.restore_request(tmp_path, req)
    assert inputs.load_request(tmp_path, req.request_id).state == "open"
    inputs.answer_request(tmp_path, req.request_id, {"db": "sqlite"})
    inputs.restore_request(tmp_path, req)  # existing (answered) file is never overwritten
    assert inputs.load_request(tmp_path, req.request_id).state == "answered"


# ---- task-written request file (producer b) -------------------------------------------------


def test_parse_request_spec_drops_engine_owned_keys() -> None:
    body = json.dumps(
        {
            "prompt": "Q?",
            "fields": [{"name": "a", "type": "bool"}],
            "request_id": "q-x-1",
            "state": "answered",
            "answer": {"values": {"a": True}},
            "task_id": "evil",
        }
    )
    spec = inputs.parse_request_spec(body)
    assert spec.prompt == "Q?" and spec.fields[0].name == "a"


@pytest.mark.parametrize(
    "body", ["[]", "{", '{"prompt": ""}', '{"prompt": "p", "fields": [{"name": "A"}]}']
)
def test_parse_request_spec_rejects_invalid(body) -> None:
    with pytest.raises(inputs.InputError, match="invalid input-request"):
        inputs.parse_request_spec(body)


def test_parse_request_spec_size_cap() -> None:
    with pytest.raises(inputs.InputError, match="exceeds"):
        inputs.parse_request_spec(b" " * (64 * 1024 + 1))


def test_read_task_request_file_absent_none(tmp_path) -> None:
    assert inputs.read_task_request_file(tmp_path / "nope.json") is None


def test_run_waiting_for_input_derivation() -> None:
    s = RunState(run_id="r", workflow_id="w", started_at="x", updated_at="x")
    s.tasks = {"a": TaskRunState(status="awaiting_input"), "b": TaskRunState(status="pending")}
    assert run_waiting_for_input(s)
    s.tasks["c"] = TaskRunState(status="running")
    assert not run_waiting_for_input(s)
    s.tasks.pop("c")
    s.status = "failed"
    assert not run_waiting_for_input(s)
