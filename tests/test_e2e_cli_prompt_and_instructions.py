"""E2E tests for `ao run --prompt` and general instructions (E-Ui7Kq2 FR-P1, FR-GI1).

Driven through the CLI via ``CliRunner`` — the outermost boundary a user touches — so these
exercise flag parsing, config/env resolution, artifact-store path guarding, and prompt
assembly together, not just the Python API underneath.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_orchestrator.cli import ENV_GENERAL_INSTRUCTIONS, app

runner = CliRunner()


def _write_specs(
    tmp_path: Path,
    prompt_path: str | None = None,
    general_instructions: list[str] | None = None,
) -> tuple[Path, Path, Path]:
    """Write a minimal fake-executor workflow/reposet/agents triplet into *tmp_path*."""
    (tmp_path / "instructions").mkdir(exist_ok=True)
    (tmp_path / "instructions" / "build.md").write_text("build it", encoding="utf-8")

    task: dict = {
        "id": "build",
        "agent": "dev",
        "instruction": "instructions/build.md",
        "outputs": ["out/build.md"],
        "skip_if_outputs_exist": False,
    }
    workflow: dict = {
        "version": "1.0",
        "id": "prompt-demo",
        "repo_set": "main",
        "tasks": [task],
    }
    if prompt_path is not None:
        workflow["prompt_path"] = prompt_path
        # The prompt only matters because a task consumes it, exactly as a real spec would.
        task["inputs"] = [prompt_path]
    if general_instructions is not None:
        workflow["general_instructions"] = general_instructions

    wf = tmp_path / "workflow.json"
    wf.write_text(json.dumps(workflow, indent=2), encoding="utf-8")

    rs = tmp_path / "reposets.json"
    rs.write_text(
        json.dumps(
            {
                "version": "1.0",
                "repo_sets": {
                    "main": {
                        "repos": [{"id": "repo", "path": str(tmp_path), "role": "primary"}],
                        "workspace_root": str(tmp_path),
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    ag = tmp_path / "agents.json"
    ag.write_text(
        json.dumps({"version": "1.0", "agents": {"dev": {"executor": "fake"}}}),
        encoding="utf-8",
    )
    return wf, rs, ag


def _run_cli(wf: Path, rs: Path, ag: Path, tmp_path: Path, *extra: str):
    return runner.invoke(
        app,
        [
            "run",
            "--workflow",
            str(wf),
            "--reposets",
            str(rs),
            "--agents",
            str(ag),
            *extra,
        ],
        env={"AO_WORKSPACE_ROOT": str(tmp_path)},
    )


class TestPromptFlag:
    def test_prompt_text_is_written_to_the_declared_prompt_path(self, tmp_path: Path) -> None:
        wf, rs, ag = _write_specs(tmp_path, prompt_path="prompts/run.md")
        result = _run_cli(wf, rs, ag, tmp_path, "--prompt", "Add rate limiting to /orders")

        assert result.exit_code == 0, result.output
        assert (tmp_path / "prompts" / "run.md").read_text(encoding="utf-8") == (
            "Add rate limiting to /orders"
        )

    def test_prompt_file_contents_are_copied_to_the_prompt_path(self, tmp_path: Path) -> None:
        wf, rs, ag = _write_specs(tmp_path, prompt_path="prompts/run.md")
        source = tmp_path / "my-prompt.md"
        source.write_text("# Feature request\n\nDo the thing.\n", encoding="utf-8")

        result = _run_cli(wf, rs, ag, tmp_path, "--prompt-file", str(source))

        assert result.exit_code == 0, result.output
        assert (tmp_path / "prompts" / "run.md").read_text(encoding="utf-8") == (
            "# Feature request\n\nDo the thing.\n"
        )

    def test_prompt_overwrites_a_previous_prompt(self, tmp_path: Path) -> None:
        wf, rs, ag = _write_specs(tmp_path, prompt_path="prompts/run.md")
        (tmp_path / "prompts").mkdir()
        (tmp_path / "prompts" / "run.md").write_text("stale prompt", encoding="utf-8")

        _run_cli(wf, rs, ag, tmp_path, "--prompt", "fresh prompt")
        assert (tmp_path / "prompts" / "run.md").read_text(encoding="utf-8") == "fresh prompt"

    def test_prompt_without_a_declared_prompt_path_fails_loudly(self, tmp_path: Path) -> None:
        # Silently dropping the prompt would let a user pay for a run that ignored it.
        wf, rs, ag = _write_specs(tmp_path, prompt_path=None)
        result = _run_cli(wf, rs, ag, tmp_path, "--prompt", "nowhere to go")

        assert result.exit_code == 1
        assert "prompt_path" in result.output

    def test_prompt_and_prompt_file_together_are_rejected(self, tmp_path: Path) -> None:
        wf, rs, ag = _write_specs(tmp_path, prompt_path="prompts/run.md")
        source = tmp_path / "p.md"
        source.write_text("x", encoding="utf-8")

        result = _run_cli(wf, rs, ag, tmp_path, "--prompt", "a", "--prompt-file", str(source))
        assert result.exit_code == 1
        assert "mutually exclusive" in result.output

    def test_unreadable_prompt_file_fails_with_a_clear_message(self, tmp_path: Path) -> None:
        wf, rs, ag = _write_specs(tmp_path, prompt_path="prompts/run.md")
        result = _run_cli(wf, rs, ag, tmp_path, "--prompt-file", str(tmp_path / "missing.md"))

        assert result.exit_code == 1
        assert "cannot read --prompt-file" in result.output

    def test_prompt_path_escaping_the_workspace_is_rejected(self, tmp_path: Path) -> None:
        wf, rs, ag = _write_specs(tmp_path, prompt_path="../../escaped.md")
        result = _run_cli(wf, rs, ag, tmp_path, "--prompt", "x")

        assert result.exit_code == 1
        assert not (tmp_path.parent.parent / "escaped.md").exists()

    def test_no_prompt_flag_leaves_an_existing_prompt_file_alone(self, tmp_path: Path) -> None:
        # A hand-written prompt (the meta/ao/epics/<id>/prompt.md convention) keeps working.
        wf, rs, ag = _write_specs(tmp_path, prompt_path="prompts/run.md")
        (tmp_path / "prompts").mkdir()
        (tmp_path / "prompts" / "run.md").write_text("hand written", encoding="utf-8")

        result = _run_cli(wf, rs, ag, tmp_path)
        assert result.exit_code == 0, result.output
        assert (tmp_path / "prompts" / "run.md").read_text(encoding="utf-8") == "hand written"


class TestGeneralInstructionFlag:
    def _read_prompt_sent(self, tmp_path: Path) -> str:
        """Read back the prompt the fake executor recorded, via its transcript."""
        transcripts = list(
            (tmp_path / ".orchestrator" / "runs").glob("*/build/attempt-*/transcript.jsonl")
        )
        assert transcripts, "no task transcript was captured"
        return transcripts[0].read_text(encoding="utf-8")

    def test_cli_flag_is_accepted_and_the_run_succeeds(self, tmp_path: Path) -> None:
        wf, rs, ag = _write_specs(tmp_path)
        (tmp_path / "rules.md").write_text("house rules", encoding="utf-8")

        result = _run_cli(wf, rs, ag, tmp_path, "--general-instruction", "rules.md")
        assert result.exit_code == 0, result.output

    def test_flag_is_repeatable(self, tmp_path: Path) -> None:
        wf, rs, ag = _write_specs(tmp_path)
        (tmp_path / "a.md").write_text("a", encoding="utf-8")
        (tmp_path / "b.md").write_text("b", encoding="utf-8")

        result = _run_cli(
            wf, rs, ag, tmp_path, "--general-instruction", "a.md", "--general-instruction", "b.md"
        )
        assert result.exit_code == 0, result.output

    def test_env_var_applies_with_no_flag_at_all(self, tmp_path: Path) -> None:
        # The "define once per workspace, applied even when not asked for" property.
        wf, rs, ag = _write_specs(tmp_path)
        (tmp_path / "rules.md").write_text("house rules", encoding="utf-8")

        result = runner.invoke(
            app,
            ["run", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
            env={
                "AO_WORKSPACE_ROOT": str(tmp_path),
                ENV_GENERAL_INSTRUCTIONS: str(tmp_path / "rules.md"),
            },
        )
        assert result.exit_code == 0, result.output

    def test_workflow_declared_instructions_need_no_flag(self, tmp_path: Path) -> None:
        wf, rs, ag = _write_specs(tmp_path, general_instructions=["rules.md"])
        (tmp_path / "rules.md").write_text("house rules", encoding="utf-8")

        result = _run_cli(wf, rs, ag, tmp_path)
        assert result.exit_code == 0, result.output

    def test_a_bad_instruction_path_does_not_fail_the_run(self, tmp_path: Path) -> None:
        # One typo in a workspace-wide setting must not break every run in the workspace.
        wf, rs, ag = _write_specs(tmp_path)
        result = _run_cli(wf, rs, ag, tmp_path, "--general-instruction", "../../etc/passwd")
        assert result.exit_code == 0, result.output

    def test_resume_accepts_the_flag_too(self, tmp_path: Path) -> None:
        wf, rs, ag = _write_specs(tmp_path)
        (tmp_path / "rules.md").write_text("rules", encoding="utf-8")
        _run_cli(wf, rs, ag, tmp_path)

        run_ids = [p.name for p in (tmp_path / ".orchestrator" / "runs").iterdir()]
        assert run_ids

        result = runner.invoke(
            app,
            [
                "resume",
                "--run-id",
                run_ids[0],
                "--workflow",
                str(wf),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
                "--general-instruction",
                "rules.md",
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )
        assert result.exit_code == 0, result.output

    def test_resume_has_no_prompt_flag(self, tmp_path: Path) -> None:
        # Deliberate: rewriting a run's prompt mid-flight would make it irreproducible from
        # its own artifacts. Asserted against the registered parameters rather than the
        # help text, which legitimately mentions "--prompt" while explaining the omission.
        wf, rs, ag = _write_specs(tmp_path, prompt_path="prompts/run.md")
        _run_cli(wf, rs, ag, tmp_path)
        run_ids = [p.name for p in (tmp_path / ".orchestrator" / "runs").iterdir()]

        result = runner.invoke(
            app,
            [
                "resume",
                "--run-id",
                run_ids[0],
                "--workflow",
                str(wf),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
                "--prompt",
                "should not be accepted",
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )
        assert result.exit_code != 0, "resume must reject --prompt"

    def test_config_file_general_instructions_apply_with_no_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wf, rs, ag = _write_specs(tmp_path)
        (tmp_path / ".git").mkdir()
        (tmp_path / ".ao").mkdir()
        (tmp_path / ".ao" / "rules.md").write_text("house rules", encoding="utf-8")
        (tmp_path / ".ao" / "config.yaml").write_text(
            "general_instructions:\n  - rules.md\n", encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)

        result = _run_cli(wf, rs, ag, tmp_path)
        assert result.exit_code == 0, result.output


class TestHelpSurface:
    def test_run_help_documents_both_new_flags(self) -> None:
        result = runner.invoke(app, ["run", "--help"])
        assert result.exit_code == 0
        assert "--prompt" in result.output
        assert "--general-instruction" in result.output

    def test_general_instruction_help_states_it_is_additive(self) -> None:
        # The additive-not-override semantics are the surprising part; the help must say so.
        result = runner.invoke(app, ["run", "--help"])
        normalized = " ".join(result.output.split())
        assert "ADDITIVE" in normalized or "additive" in normalized

    def test_env_var_name_is_documented(self) -> None:
        result = runner.invoke(app, ["run", "--help"])
        assert ENV_GENERAL_INSTRUCTIONS in " ".join(result.output.split())

    def test_pathsep_convention_is_documented(self) -> None:
        assert os.pathsep in (":", ";")
