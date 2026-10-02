"""CLI-boundary e2e for `ao rate` (E-Us9Kd4 T-Fb3Ef4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_orchestrator.cli import app
from agent_orchestrator.models import RunState, TaskRunState

RUN = "wf-20260101T000000Z"
runner = CliRunner()


@pytest.fixture
def ws(tmp_path: Path) -> str:
    d = tmp_path / ".orchestrator" / "runs" / RUN
    d.mkdir(parents=True)
    st = RunState(
        run_id=RUN,
        workflow_id="wf",
        repo_set="r",
        started_at="t",
        updated_at="t",
        tasks={"build": TaskRunState()},
    )
    (d / "state.json").write_text(st.model_dump_json())
    return str(tmp_path)


def _rate(ws: str, *args: str):  # type: ignore[no-untyped-def]
    return runner.invoke(app, ["rate", *args, "--workspace", ws])


def test_rate_run_and_show(ws: str) -> None:
    r = _rate(ws, RUN, "bad", "--reason", "wrong", "--reason", "too-costly", "--note", "meh")
    assert r.exit_code == 0, r.output
    assert "Recorded 'bad' for run" in r.output
    assert _rate(ws, RUN, "good", "--task", "build").exit_code == 0
    shown = _rate(ws, RUN, "--show")
    assert shown.exit_code == 0
    assert "wrong,too-costly" in shown.output and "build" in shown.output and "meh" in shown.output


def test_show_json(ws: str) -> None:
    _rate(ws, RUN, "bad")
    _rate(ws, RUN, "ok")
    _rate(ws, RUN, "good", "--task", "build")
    out = json.loads(_rate(ws, RUN, "--show", "--json").output)
    assert len(out["entries"]) == 3
    assert {(e["scope"], e["rating"]) for e in out["effective"]} == {
        ("run", "ok"),
        ("task", "good"),
    }


def test_show_empty(ws: str) -> None:
    r = _rate(ws, RUN, "--show")
    assert r.exit_code == 0 and "no feedback" in r.output


@pytest.mark.parametrize(
    "args",
    [
        [RUN, "great"],
        [RUN, "good", "--task", "nope"],
        ["no-such-run", "good"],
        ["../x", "good"],
        [RUN, "good", "--note", "x" * 2001],
        [RUN, "good", "--reason", "bogus"],
        [RUN],  # no rating, no --show
    ],
)
def test_validation_errors_exit_1(ws: str, args: list[str]) -> None:
    r = _rate(ws, *args)
    assert r.exit_code == 1
    assert "ERROR" in r.output
    assert not (Path(ws) / ".orchestrator/runs" / RUN / "feedback.json").exists()
