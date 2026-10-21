"""CliRunner e2e for awaiting_input: `ao run`, `ao status`, `ao answer`, `--input-mode`."""

from __future__ import annotations

import json
import re
from pathlib import Path

from typer.testing import CliRunner

from agent_orchestrator.cli import app

runner = CliRunner()

INPUT_BLOCK = {
    "prompt": "Which database?",
    "fields": [
        {"name": "db", "type": "choice", "choices": ["postgres", "sqlite"], "default": "sqlite"},
        {"name": "note", "type": "text", "required": False},
    ],
}


def _write(tmp_path: Path, input_block: dict | None = None) -> tuple[Path, Path, Path, dict]:
    instr = tmp_path / "specs" / "examples" / "instructions"
    instr.mkdir(parents=True)
    (instr / "design.md").write_text("do it")
    wf = tmp_path / "wf.json"
    wf.write_text(
        json.dumps(
            {
                "version": "1.0",
                "id": "inwf",
                "repo_set": "rs",
                "tasks": [
                    {
                        "id": "gate",
                        "agent": "ag",
                        "instruction": "specs/examples/instructions/design.md",
                        "outputs": ["output/answer.json"],
                        "input": input_block or INPUT_BLOCK,
                    },
                    {
                        "id": "after",
                        "agent": "ag",
                        "instruction": "specs/examples/instructions/design.md",
                        "depends_on": ["gate"],
                        "outputs": ["output/after.txt"],
                    },
                ],
            }
        )
    )
    rs = tmp_path / "rs.json"
    rs.write_text(
        json.dumps(
            {
                "version": "1.0",
                "repo_sets": {
                    "rs": {"workspace_root": str(tmp_path), "repos": [{"id": "core", "path": "."}]}
                },
            }
        )
    )
    ag = tmp_path / "ag.json"
    ag.write_text(json.dumps({"version": "1.0", "agents": {"ag": {"executor": "fake"}}}))
    env = {"AO_WORKSPACE_ROOT": str(tmp_path)}
    return wf, rs, ag, env


def _common(wf: Path, rs: Path, ag: Path) -> list[str]:
    return ["--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)]


def _run_id(output: str) -> str:
    m = re.search(r"^Run:\s+(\S+)", output, re.M)
    assert m, output
    return m.group(1)


def _start(tmp_path: Path, extra: list[str] | None = None, block: dict | None = None):
    wf, rs, ag, env = _write(tmp_path, block)
    res = runner.invoke(app, ["run", *_common(wf, rs, ag), *(extra or [])], env=env)
    return wf, rs, ag, env, res


def test_run_waits_exit_zero_and_status_shows_awaiting_input(tmp_path) -> None:
    wf, rs, ag, env, res = _start(tmp_path)
    assert res.exit_code == 0, res.output  # waiting is not a failure
    assert "awaiting_input" in res.output
    assert "Waiting for input: 1 request(s)" in res.output
    assert "Which database?" in res.output
    rid = _run_id(res.output)

    st = runner.invoke(app, ["status", "--run-id", rid], env=env)
    assert st.exit_code == 0, st.output
    assert "awaiting_input" in st.output and "Waiting for input" in st.output
    assert "failed" not in st.output.split("Task")[0]  # run header is not 'failed'
    assert "Status:       running" in st.output


def test_answer_list_set_and_resume_completes_run(tmp_path) -> None:
    wf, rs, ag, env, res = _start(tmp_path)
    rid = _run_id(res.output)

    lst = runner.invoke(app, ["answer", "--run-id", rid, "--list"], env=env)
    assert lst.exit_code == 0 and "q-gate-1" in lst.output and "choices=['postgres'" in lst.output

    ans = runner.invoke(
        app,
        ["answer", "--run-id", rid, "--set", "db=postgres", "--resume", *_common(wf, rs, ag)],
        env=env,
    )
    assert ans.exit_code == 0, ans.output
    assert "Answered q-gate-1" in ans.output and "succeeded" in ans.output
    answer = json.loads((tmp_path / "output" / "answer.json").read_text())
    assert answer["values"] == {"db": "postgres"} and answer["assumed"] is False
    assert (tmp_path / "output" / "after.txt").exists()

    st = runner.invoke(app, ["status", "--run-id", rid], env=env)
    assert "Waiting for input" not in st.output


def test_answer_without_resume_prints_hint_and_run_still_waiting(tmp_path) -> None:
    wf, rs, ag, env, res = _start(tmp_path)
    rid = _run_id(res.output)
    ans = runner.invoke(app, ["answer", "--run-id", rid, "--set", "db=sqlite"], env=env)
    assert ans.exit_code == 0 and f"ao resume --run-id {rid}" in ans.output
    res2 = runner.invoke(app, ["resume", "--run-id", rid, *_common(wf, rs, ag)], env=env)
    assert res2.exit_code == 0 and "succeeded" in res2.output.lower()


def test_answer_json_input_and_exit_codes(tmp_path) -> None:
    wf, rs, ag, env, res = _start(tmp_path)
    rid = _run_id(res.output)
    # usage / validation errors => 2, run unchanged
    for args in (
        ["--set", "db=mysql"],
        ["--set", "bogus=1"],
        ["--set", "novalue"],
        [],  # no values, no TTY
        ["--request-id", "q-nope-9", "--set", "db=sqlite"],
    ):
        r = runner.invoke(app, ["answer", "--run-id", rid, *args], env=env)
        assert r.exit_code == 2, (args, r.output)
    bad_run = runner.invoke(app, ["answer", "--run-id", "nope", "--list"], env=env)
    assert bad_run.exit_code == 2

    body = tmp_path / "a.json"
    body.write_text(json.dumps({"db": "postgres", "note": "x"}))
    ok = runner.invoke(app, ["answer", "--run-id", rid, "--json", str(body)], env=env)
    assert ok.exit_code == 0, ok.output
    # already answered => 3 (different values), identical re-answer is idempotent
    again = runner.invoke(
        app, ["answer", "--run-id", rid, "--request-id", "q-gate-1", "--set", "db=sqlite"], env=env
    )
    assert again.exit_code == 3
    same = runner.invoke(
        app, ["answer", "--run-id", rid, "--request-id", "q-gate-1", "--json", str(body)], env=env
    )
    assert same.exit_code == 0


def test_nonblocking_run_proceeds_with_recorded_assumption(tmp_path) -> None:
    _, _, _, env, res = _start(tmp_path, ["--input-mode", "nonblocking"])
    assert res.exit_code == 0, res.output
    assert "succeeded" in res.output.lower()
    assert "ASSUMED: gate.db='sqlite' (default)" in res.output
    rid = _run_id(res.output)
    answer = json.loads((tmp_path / "output" / "answer.json").read_text())
    assert answer["assumed"] is True
    st = runner.invoke(app, ["status", "--run-id", rid], env=env)
    assert "ASSUMED: gate.db='sqlite' (default)" in st.output
    lst = runner.invoke(app, ["answer", "--run-id", rid, "--list"], env=env)
    assert "No open input requests" in lst.output


def test_nonblocking_required_field_without_default_fails_run(tmp_path) -> None:
    block = {"prompt": "Who?", "fields": [{"name": "who"}]}
    _, _, _, _, res = _start(tmp_path, ["--input-mode", "nonblocking"], block)
    assert res.exit_code == 1
    assert "failed" in res.output


def test_invalid_input_mode_rejected(tmp_path) -> None:
    _, _, _, _, res = _start(tmp_path, ["--input-mode", "sometimes"])
    assert res.exit_code == 2 and "--input-mode" in res.output


def test_resume_input_mode_nonblocking_converts_open_requests(tmp_path) -> None:
    wf, rs, ag, env, res = _start(tmp_path)
    rid = _run_id(res.output)
    r = runner.invoke(
        app,
        ["resume", "--run-id", rid, "--input-mode", "nonblocking", *_common(wf, rs, ag)],
        env=env,
    )
    assert r.exit_code == 0, r.output
    assert "succeeded" in r.output.lower() and "ASSUMED" in r.output


def test_ao_new_run_accepts_input_mode_flag(tmp_path) -> None:
    res = runner.invoke(
        app, ["new", "x", "y", "--input-mode", "bogus"], env={"AO_WORKSPACE_ROOT": str(tmp_path)}
    )
    assert res.exit_code == 2
