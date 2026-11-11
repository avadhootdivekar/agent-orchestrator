"""T-Hb3Adp: the Harbor seam -- `ao-bench exec-subject` (run an ao_workflow subject on an
existing repo dir), `harbor-prompt` (the plain arm's byte-identical prompt template) and
`harbor-parity` (compare what each arm's Claude Code sessions loaded). Fake tier: the `ao`
subprocess is mocked; everything goes through the `ao-bench` CLI (CliRunner)."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_orchestrator.bench import subjects
from agent_orchestrator.bench.cli import app
from agent_orchestrator.bench.harbor_exec import (
    EFFECTIVE_PROMPT_NAME,
    INSTRUCTION_HEADING,
    JINJA_INSTRUCTION,
    compose_prompt,
)
from tests.bench.test_subjects import _write_fake_run_state, _write_reposet_template

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[2]
COMMITTED_HARBOR_SUBJECT = REPO_ROOT / "benchmarks" / "subjects" / "ao-overseer-harbor.json"
WRAPPER = "# Bench task\n\n## Asks\n- Do the task below.\n"


def _subject(base: Path, **extra: object) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    _write_reposet_template(base / "reposet.json", repo_set_id="bench-set")
    (base / "agents.json").write_text("{}")
    (base / "prompt.md").write_text(WRAPPER)
    data: dict[str, object] = {
        "version": "1.0",
        "id": "ao-overseer-harbor",
        "type": "ao_workflow",
        "template": "overseer-runner",
        "template_params": {"final_push": "false"},
        "prompt_file": "prompt.md",
        "reposets": "reposet.json",
        "agents": "agents.json",
        "model": "claude-sonnet-5-5",
    }
    data.update(extra)
    path = base / "subject.json"
    path.write_text(json.dumps({k: v for k, v in data.items() if v is not None}))
    return path


def _capture_ao(
    monkeypatch: pytest.MonkeyPatch, ws: Path, *, returncode: int = 0, run: bool = True
) -> dict[str, object]:
    captured: dict[str, object] = {}

    def fake_run(argv, *, cwd, stdout_path, stderr_path, timeout_seconds, env=None):  # noqa: ANN001
        captured.update(argv=argv, cwd=cwd, env=env)
        stdout_path.write_text("")
        stderr_path.write_text("boom" if returncode else "")
        if run:
            _write_fake_run_state(ws, "o-abc123-bench", cost=2.5, in_tokens=1000, out_tokens=50)
        return returncode, False

    monkeypatch.setattr(subjects, "_run_with_timeout", fake_run)
    return captured


def _exec_args(subject: Path, repo: Path, ws: Path, out: Path, instr: Path) -> list[str]:
    return ["exec-subject", "--subject", str(subject), "--repo-dir", str(repo),
            "--instruction-file", str(instr), "--workspace", str(ws), "--out", str(out),
            "--ao-command", "/opt/bin/ao"]  # fmt: skip


@pytest.fixture()
def layout(tmp_path: Path) -> dict[str, Path]:
    repo = tmp_path / "app"
    repo.mkdir()
    instr = tmp_path / "instruction.md"
    instr.write_text("  Fix the bug in foo.py.\n\n")
    return {"subject": _subject(tmp_path / "subj"), "repo": repo, "ws": tmp_path / "ws",
            "out": tmp_path / "logs" / "ao-usage.json", "instr": instr}  # fmt: skip


def test_compose_prompt_appends_instruction_verbatim_without_trailing_newline() -> None:
    text = compose_prompt(WRAPPER + "\n\n", "\n  do X\n  then Y  \n")
    assert text == f"{WRAPPER.rstrip()}\n\n{INSTRUCTION_HEADING}\n\ndo X\n  then Y"


def test_exec_subject_runs_installed_ao_in_workspace_and_writes_usage(
    monkeypatch: pytest.MonkeyPatch, layout: dict[str, Path]
) -> None:
    captured = _capture_ao(monkeypatch, layout["ws"])
    result = runner.invoke(
        app,
        [
            *_exec_args(**layout),
            "--model",
            "claude-opus-5-5",
            "--param",
            "allowed_models=claude-opus-5-5",
            "--param",
            "final_push=true",
        ],  # fmt: skip
    )
    assert result.exit_code == 0, result.output
    argv = captured["argv"]
    assert isinstance(argv, list)
    # launched as an installed tool from the scratch workspace, not `uv run ao` from a checkout
    assert argv[:4] == ["/opt/bin/ao", "new", "overseer-runner", subjects.TEMPLATE_INSTANCE_SLUG]
    assert captured["cwd"] == layout["ws"].resolve()
    params = [argv[i + 1] for i, a in enumerate(argv) if a == "--param"]
    assert "allowed_models=claude-opus-5-5" in params
    assert "final_push=true" in params and "final_push=false" not in params  # --param wins
    effective = layout["ws"] / EFFECTIVE_PROMPT_NAME
    assert argv[argv.index("--prompt-file") + 1] == str(effective.resolve())
    assert effective.read_text() == compose_prompt(WRAPPER, "Fix the bug in foo.py.")
    env = captured["env"]
    assert isinstance(env, dict) and env["AO_MODEL"] == "claude-opus-5-5"  # --model wins
    usage = json.loads(layout["out"].read_text())
    assert usage["status"] == "succeeded" and usage["cost_usd"] == pytest.approx(2.5)
    assert usage["subject_id"] == "ao-overseer-harbor" and usage["model"] == "claude-opus-5-5"
    assert not any(layout["repo"].iterdir())  # nothing written into the repo under test


def test_exec_subject_failed_run_still_exits_zero_for_grading(
    monkeypatch: pytest.MonkeyPatch, layout: dict[str, Path]
) -> None:
    _capture_ao(monkeypatch, layout["ws"], returncode=1)
    result = runner.invoke(app, _exec_args(**layout))
    assert result.exit_code == 0, result.output  # the harness grades the repo regardless
    assert json.loads(layout["out"].read_text())["status"] == "failed"


def test_exec_subject_without_run_dir_is_a_subject_error(
    monkeypatch: pytest.MonkeyPatch, layout: dict[str, Path]
) -> None:
    _capture_ao(monkeypatch, layout["ws"], run=False)
    result = runner.invoke(app, _exec_args(**layout))
    assert result.exit_code == 2
    usage = json.loads(layout["out"].read_text())
    assert usage["status"] == "error" and "SubjectError" in usage["raw_error"]


def test_exec_subject_rejects_workspace_inside_repo(layout: dict[str, Path]) -> None:
    layout["ws"] = layout["repo"] / "ws"
    result = runner.invoke(app, _exec_args(**layout))
    assert result.exit_code == 1 and "outside" in result.output


def test_exec_subject_rejects_non_template_subject(tmp_path: Path, layout: dict[str, Path]) -> None:
    layout["subject"] = _subject(tmp_path / "wf", template=None, workflow="wf.json")
    result = runner.invoke(app, _exec_args(**layout))
    assert result.exit_code == 1 and "template mode" in result.output


def test_harbor_prompt_template_matches_exec_subject_prompt(tmp_path: Path) -> None:
    subject = _subject(tmp_path / "subj")
    out = tmp_path / "prompts" / "p.j2"
    result = runner.invoke(app, ["harbor-prompt", "--subject", str(subject), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_text() == compose_prompt(WRAPPER, JINJA_INSTRUCTION)
    # Rendering is Harbor's (Jinja, drops the final newline); jinja2 ships with Harbor, not
    # ao, so the byte-identity check against Harbor's renderer lives in the README smoke.
    assert out.read_text().endswith(JINJA_INSTRUCTION)


def test_harbor_prompt_rejects_jinja_in_wrapper(tmp_path: Path) -> None:
    subject = _subject(tmp_path / "subj")
    (tmp_path / "subj" / "prompt.md").write_text("Use {{ x }} here\n")
    result = runner.invoke(
        app, ["harbor-prompt", "--subject", str(subject), "--out", str(tmp_path / "p.j2")]
    )
    assert result.exit_code == 1 and "Jinja" in result.output


def test_committed_harbor_subject_composes() -> None:
    result = runner.invoke(
        app, ["harbor-prompt", "--subject", str(COMMITTED_HARBOR_SUBJECT), "--out", "/dev/null"]
    )
    assert result.exit_code == 0, result.output


# --- harbor-parity ------------------------------------------------------------------------


def _init(tools: list[str], skills: list[str] | None = None, **extra: object) -> str:
    event: dict[str, object] = {
        "type": "system", "subtype": "init", "tools": tools, "skills": skills or [],
        "agents": [], "slash_commands": [], "mcp_servers": [], "plugins": [],
        "model": "claude-sonnet-5-5", "permissionMode": "bypassPermissions",
        "output_style": "default", "claude_code_version": "2.1.321",
    }  # fmt: skip
    event.update(extra)
    return json.dumps(event) + "\n"


def _cc_job(root: Path, *inits: str) -> Path:
    agent = root / "task__1" / "agent"
    agent.mkdir(parents=True)
    (agent / "claude-code.txt").write_text("".join(inits) + '{"type":"result"}\n')
    return root


def _ao_job(root: Path, *transcripts: str, summary: str | None = None) -> Path:
    agent = root / "task__1" / "agent"
    agent.mkdir(parents=True)
    members = [
        (f".orchestrator/runs/r/t{i}/attempt-1/transcript.jsonl", text)
        for i, text in enumerate(transcripts)
    ]
    if summary is not None:  # ao's run summarizer session
        members.append((".orchestrator/runs/r/summary/capture/transcript.jsonl", summary))
    with tarfile.open(agent / "ao-orchestrator.tgz", "w:gz") as tar:
        for name, text in members:
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return root


def test_harbor_parity_matching_arms_exit_zero(tmp_path: Path) -> None:
    cc = _cc_job(tmp_path / "cc", _init(["Bash", "Edit"]))
    ao = _ao_job(tmp_path / "ao", _init(["Bash", "Edit"]), _init(["Bash", "Edit"]))
    result = runner.invoke(app, ["harbor-parity", str(cc), str(ao)])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["profiles"]["ao"]["sessions"] == 2 and report["mismatches"] == {}


def test_harbor_parity_ignores_ao_run_summarizer_session(tmp_path: Path) -> None:
    # The summarizer is a fixed Haiku engine session (no Bash, no repo): not a task session.
    cc = _cc_job(tmp_path / "cc", _init(["Bash", "Edit"]))
    summary = _init(["Read", "Write", "Glob", "Grep"], model="claude-haiku-5-5")
    ao = _ao_job(tmp_path / "ao", _init(["Bash", "Edit"]), summary=summary)
    result = runner.invoke(app, ["harbor-parity", str(cc), str(ao)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["profiles"]["ao"]["sessions"] == 1


def test_harbor_parity_flags_extra_skill_or_tool_in_one_arm(tmp_path: Path) -> None:
    cc = _cc_job(tmp_path / "cc", _init(["Bash", "Edit"]))
    ao = _ao_job(tmp_path / "ao", _init(["Bash", "Edit", "Task"], skills=["dev-tips"]))
    result = runner.invoke(app, ["harbor-parity", str(cc), str(ao)])
    assert result.exit_code == 3
    mismatches = json.loads(result.output)["mismatches"]
    assert set(mismatches) == {"skills", "tools"}


def test_harbor_parity_flags_auto_memory_and_missing_sessions(tmp_path: Path) -> None:
    cc = _cc_job(tmp_path / "cc", _init(["Bash"], memory_paths=["/m/MEMORY.md"]))
    empty = tmp_path / "empty"
    (empty / "task__1" / "agent").mkdir(parents=True)
    result = runner.invoke(app, ["harbor-parity", str(cc), str(empty)])
    assert result.exit_code == 3
    report = json.loads(result.output)
    assert report["no_sessions"] == ["empty"] and report["profiles"]["cc"]["auto_memory"]


def test_exec_subject_rejects_malformed_param(layout: dict[str, Path]) -> None:
    result = runner.invoke(app, [*_exec_args(**layout), "--param", "no-equals"])
    assert result.exit_code == 1 and "KEY=VALUE" in result.output
