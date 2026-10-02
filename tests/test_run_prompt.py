"""Run-prompt capture into RunState (E-Us9Kd4 FR-13): helper, engine, and CLI e2e."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from agent_orchestrator.cli import app
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import (
    MAX_PROMPT_BYTES,
    PROMPT_SOURCE_CLI_PROMPT,
    PROMPT_SOURCE_WORKFLOW_FILE,
    RunPrompt,
    RunState,
)
from agent_orchestrator.run_prompt import capture_run_prompt, file_sha256, prompt_source
from tests.test_e2e_cli_prompt_and_instructions import _run_cli, _write_specs
from tests.test_engine import _fake_agents, _fake_reposets, _make_workspace, _task, _workflow

runner = CliRunner()
FIXED = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)


def _clock() -> datetime:
    return FIXED


def _capture(path: Path, source=PROMPT_SOURCE_WORKFLOW_FILE) -> RunPrompt:
    got = capture_run_prompt(path, "p/prompt.md", source, _clock)
    assert got is not None
    return got


class TestCaptureHelper:
    def test_small_prompt_roundtrip(self, tmp_path: Path) -> None:
        f = tmp_path / "p.md"
        f.write_text("héllo\nworld", encoding="utf-8")
        got = _capture(f)
        assert got.text == "héllo\nworld"
        assert got.truncated is False
        assert got.chars == len("héllo\nworld")
        assert got.sha256 == hashlib.sha256(f.read_bytes()).hexdigest()
        assert got.captured_at == FIXED.isoformat()
        assert got.path == "p/prompt.md"

    def test_truncation_bounds_text_but_hashes_and_counts_full(self, tmp_path: Path) -> None:
        f = tmp_path / "big.md"
        text = "x" * (MAX_PROMPT_BYTES * 2 + 123)
        f.write_text(text, encoding="utf-8")
        got = _capture(f)
        assert got.truncated is True
        assert len(got.text.encode()) <= MAX_PROMPT_BYTES
        assert got.chars == len(text)
        assert got.sha256 == hashlib.sha256(text.encode()).hexdigest()

    def test_truncation_never_splits_a_multibyte_char(self, tmp_path: Path) -> None:
        f = tmp_path / "mb.md"
        text = "é" * (MAX_PROMPT_BYTES)  # 2 bytes each -> 128 KiB
        f.write_text(text, encoding="utf-8")
        got = _capture(f)
        assert got.truncated and "�" not in got.text
        assert len(got.text.encode()) <= MAX_PROMPT_BYTES
        assert got.chars == len(text)

    def test_exactly_at_limit_is_not_truncated(self, tmp_path: Path) -> None:
        f = tmp_path / "edge.md"
        f.write_text("a" * MAX_PROMPT_BYTES, encoding="utf-8")
        got = _capture(f)
        assert got.truncated is False and len(got.text) == MAX_PROMPT_BYTES

    def test_missing_or_directory_returns_none(self, tmp_path: Path) -> None:
        assert (
            capture_run_prompt(tmp_path / "nope", "x", PROMPT_SOURCE_WORKFLOW_FILE, _clock) is None
        )
        assert capture_run_prompt(tmp_path, "x", PROMPT_SOURCE_WORKFLOW_FILE, _clock) is None
        assert file_sha256(tmp_path / "nope") is None

    def test_source_mapping(self) -> None:
        assert prompt_source("a", None) == "cli-prompt"
        assert prompt_source(None, "f") == "cli-prompt-file"
        assert prompt_source(None, None) == "workflow-file"


class TestEngineStoresInjectedPrompt:
    def _run(self, tmp_path: Path, prompt: RunPrompt | None) -> RunState:
        store, rs = _make_workspace(tmp_path)
        orch = Orchestrator(FakeExecutor(), store, rs, run_prompt=prompt)
        wf = _workflow([_task("a", outputs=["output/a.txt"])])
        return orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

    def test_prompt_stored_on_new_run_and_persisted(self, tmp_path: Path) -> None:
        rp = RunPrompt(
            text="t", chars=1, source=PROMPT_SOURCE_CLI_PROMPT, path="p.md",
            sha256="ab", captured_at=FIXED.isoformat(),
        )  # fmt: skip
        state = self._run(tmp_path, rp)
        assert state.prompt == rp
        on_disk = json.loads(
            next((tmp_path / ".orchestrator/runs").iterdir()).joinpath("state.json").read_text()
        )
        assert on_disk["prompt"]["text"] == "t"

    def test_no_prompt_means_none(self, tmp_path: Path) -> None:
        assert self._run(tmp_path, None).prompt is None

    def test_old_state_json_without_prompt_loads(self) -> None:
        raw = {
            "run_id": "r",
            "workflow_id": "w",
            "repo_set": "s",
            "started_at": "x",
            "updated_at": "y",
        }
        assert RunState.model_validate(raw).prompt is None

    def test_resume_never_overwrites_recorded_prompt(self, tmp_path: Path) -> None:
        old = RunPrompt(
            text="orig", chars=4, source="cli-prompt", path="p.md", sha256="1", captured_at="t"
        )
        first = self._run(tmp_path, old)
        store, rs = _make_workspace(tmp_path)
        other = old.model_copy(update={"text": "NEW"})
        orch = Orchestrator(FakeExecutor(), store, rs, run_prompt=other)
        wf = _workflow([_task("a", outputs=["output/a.txt"])])
        resumed = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=first)
        assert resumed.prompt is not None and resumed.prompt.text == "orig"


def _state_of(tmp_path: Path) -> dict:
    run_dir = next((tmp_path / ".orchestrator" / "runs").iterdir())
    return json.loads((run_dir / "state.json").read_text())


class TestCliE2E:
    def test_run_prompt_flag_recorded(self, tmp_path: Path) -> None:
        wf, rs, ag = _write_specs(tmp_path, prompt_path="prompts/run.md")
        res = _run_cli(wf, rs, ag, tmp_path, "--prompt", "Add rate limiting")
        assert res.exit_code == 0, res.output
        p = _state_of(tmp_path)["prompt"]
        assert p["text"] == "Add rate limiting" and p["source"] == "cli-prompt"
        assert p["path"] == "prompts/run.md" and p["truncated"] is False
        assert p["sha256"] == hashlib.sha256(b"Add rate limiting").hexdigest()

    def test_run_prompt_file_flag_source(self, tmp_path: Path) -> None:
        wf, rs, ag = _write_specs(tmp_path, prompt_path="prompts/run.md")
        src = tmp_path / "mine.md"
        src.write_text("# Do it\n", encoding="utf-8")
        res = _run_cli(wf, rs, ag, tmp_path, "--prompt-file", str(src))
        assert res.exit_code == 0, res.output
        assert _state_of(tmp_path)["prompt"]["source"] == "cli-prompt-file"

    def test_hand_written_file_is_workflow_file(self, tmp_path: Path) -> None:
        wf, rs, ag = _write_specs(tmp_path, prompt_path="prompts/run.md")
        (tmp_path / "prompts").mkdir()
        (tmp_path / "prompts" / "run.md").write_text("by hand", encoding="utf-8")
        res = _run_cli(wf, rs, ag, tmp_path)
        assert res.exit_code == 0, res.output
        p = _state_of(tmp_path)["prompt"]
        assert p["source"] == "workflow-file" and p["text"] == "by hand"

    def test_no_prompt_path_means_none(self, tmp_path: Path) -> None:
        wf, rs, ag = _write_specs(tmp_path)
        res = _run_cli(wf, rs, ag, tmp_path)
        assert res.exit_code == 0, res.output
        assert _state_of(tmp_path)["prompt"] is None

    def test_declared_but_missing_file_means_none(self, tmp_path: Path) -> None:
        wf, rs, ag = _write_specs(tmp_path, prompt_path="prompts/run.md")
        res = _run_cli(wf, rs, ag, tmp_path)
        assert res.exit_code != 0  # the task's declared input is missing
        assert _state_of(tmp_path)["prompt"] is None

    def test_resume_keeps_recorded_prompt_even_if_file_edited(self, tmp_path: Path) -> None:
        wf, rs, ag = _write_specs(tmp_path, prompt_path="prompts/run.md")
        assert _run_cli(wf, rs, ag, tmp_path, "--prompt", "original").exit_code == 0
        run_id = next((tmp_path / ".orchestrator" / "runs").iterdir()).name
        (tmp_path / "prompts" / "run.md").write_text("edited later", encoding="utf-8")
        res = runner.invoke(
            app,
            ["resume", "--run-id", run_id, "--workflow", str(wf), "--reposets", str(rs),
             "--agents", str(ag)],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )  # fmt: skip
        assert res.exit_code == 0, res.output
        p = _state_of(tmp_path)["prompt"]
        assert p["text"] == "original"
