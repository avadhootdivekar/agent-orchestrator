"""Tests for the `--isolation` CLI flag / `AO_ISOLATION` env var / `.ao/config.yaml
isolation:` block precedence chain, the fill-in-only semantics, and the `ao status`
integration summary line (E-Wk9Tz3 HLD §11 M9, T-Cx4Jf1 Part A).

Precedence unit tests mirror `tests/test_cli.py::TestResolveRunSettingsMaxParallel` /
`TestResolveMonitoringSettings`'s own style (direct calls to the private cli.py resolver,
isolated from this repo's own `.ao/config.yaml` via `monkeypatch.chdir`). The fill-in
semantics and the real worktree lifecycle are proven end-to-end via `CliRunner`, per
CLAUDE.md's outermost-boundary e2e rule -- `tests/test_e2e_cli_isolation.py`'s own
`_git`/`_git_repo`/`_patch_dispatch_executor`/`_env` helpers are reused directly (this
repo's own precedent for cross-test-file helper reuse: `tests/test_hotspots.py` imports
from `tests/isolation/conftest.py`, `tests/test_wave_concurrency_semantics.py` imports
from `tests/test_wave_scheduler.py`).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from agent_orchestrator.cli import app
from agent_orchestrator.project_config import IsolationConfig, ProjectConfig
from tests.test_e2e_cli_isolation import _env, _git, _git_repo, _patch_dispatch_executor

runner = CliRunner()


# ---------------------------------------------------------------------------------------
# _resolve_isolation_settings precedence (AC-1): CLI > AO_ISOLATION > isolation.mode > unset
# ---------------------------------------------------------------------------------------


class TestResolveIsolationSettings:
    def _call(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolation: str | None):
        from agent_orchestrator.cli import _resolve_isolation_settings

        monkeypatch.chdir(tmp_path)  # isolate from this repo's own .ao/config.yaml
        return _resolve_isolation_settings(isolation)

    def test_none_everywhere_defaults_to_unset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """C-1 (2026-09-07 review): "auto" was dropped -- "no override" is now `None`, not
        a third string sentinel (no `models.py`/HLD constant for it was ever found)."""
        monkeypatch.delenv("AO_ISOLATION", raising=False)
        mode, strict, env = self._call(tmp_path, monkeypatch, None)
        assert (mode, strict, env) == (None, False, {})

    def test_config_only_resolves_to_config_value(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AO_ISOLATION", raising=False)
        (tmp_path / ".ao").mkdir()
        (tmp_path / ".ao" / "config.yaml").write_text("isolation:\n  mode: worktree\n")
        mode, _strict, _env = self._call(tmp_path, monkeypatch, None)
        assert mode == "worktree"

    def test_env_overrides_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / ".ao").mkdir()
        (tmp_path / ".ao" / "config.yaml").write_text("isolation:\n  mode: worktree\n")
        monkeypatch.setenv("AO_ISOLATION", "none")
        mode, _strict, _env = self._call(tmp_path, monkeypatch, None)
        assert mode == "none"

    def test_cli_overrides_env_and_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".ao").mkdir()
        (tmp_path / ".ao" / "config.yaml").write_text("isolation:\n  mode: worktree\n")
        monkeypatch.setenv("AO_ISOLATION", "none")
        mode, _strict, _env = self._call(tmp_path, monkeypatch, "worktree")
        assert mode == "worktree"

    def test_empty_env_treated_as_unset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AO_ISOLATION", "")
        mode, _strict, _env = self._call(tmp_path, monkeypatch, None)
        assert mode is None

    def test_invalid_cli_value_exits_1_with_message(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        monkeypatch.delenv("AO_ISOLATION", raising=False)
        with pytest.raises(typer.Exit):
            self._call(tmp_path, monkeypatch, "bogus")
        assert "must be one of" in capsys.readouterr().err

    def test_invalid_env_value_exits_1_with_message(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        monkeypatch.setenv("AO_ISOLATION", "bogus")
        with pytest.raises(typer.Exit):
            self._call(tmp_path, monkeypatch, None)
        assert "must be one of" in capsys.readouterr().err

    def test_invalid_config_file_mode_exits_1_with_message(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """C-2 (2026-09-07 review): a malformed `isolation.mode` in `.ao/config.yaml` must
        exit 1 with an actionable message, not silently fall back to "no override" --
        `_load_project_config_or_none`'s previous broad `except Exception` discarded the
        `ConfigError` `load_project_config` correctly raised, before this function's own
        `ISOLATION_MODE_CHOICES` check ever saw the value."""
        monkeypatch.delenv("AO_ISOLATION", raising=False)
        (tmp_path / ".ao").mkdir()
        (tmp_path / ".ao" / "config.yaml").write_text("isolation:\n  mode: bogus\n")
        with pytest.raises(typer.Exit):
            self._call(tmp_path, monkeypatch, None)
        err = capsys.readouterr().err
        assert "ERROR" in err
        assert "bogus" in err or "isolation.mode" in err

    def test_strict_and_env_are_config_file_only_no_cli_or_env_surface(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """HLD §11 M9's own interface table: `isolation.env`/`isolation.strict` come only
        from `.ao/config.yaml` -- an env var that merely LOOKS related must not affect
        either (no such CLI/env surface exists, unlike `mode`)."""
        (tmp_path / ".ao").mkdir()
        (tmp_path / ".ao" / "config.yaml").write_text(
            "isolation:\n  strict: true\n  env:\n    core:\n      CARGO_TARGET_DIR: /tmp/target\n"
        )
        monkeypatch.setenv("AO_ISOLATION_STRICT", "false")
        _mode, strict, env = self._call(tmp_path, monkeypatch, None)
        assert strict is True
        assert env == {"core": {"CARGO_TARGET_DIR": "/tmp/target"}}

    def test_returned_env_is_a_copy_not_the_live_config_object(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".ao").mkdir()
        (tmp_path / ".ao" / "config.yaml").write_text("isolation:\n  env:\n    core:\n      X: 1\n")
        _mode, _strict, env = self._call(tmp_path, monkeypatch, None)
        env["core"]["X"] = "mutated"
        _mode2, _strict2, env2 = self._call(tmp_path, monkeypatch, None)
        assert env2["core"]["X"] == "1"


# ---------------------------------------------------------------------------------------
# _apply_isolation_state_dir_env: config-file AO_STATE_DIR fill-in, real env always wins.
# ---------------------------------------------------------------------------------------


class TestApplyIsolationStateDirEnv:
    def test_sets_when_unset_and_config_declares_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from agent_orchestrator.cli import _apply_isolation_state_dir_env

        monkeypatch.delenv("AO_STATE_DIR", raising=False)
        cfg = ProjectConfig(isolation=IsolationConfig(state_dir="/tmp/custom-state"))
        _apply_isolation_state_dir_env(cfg)
        assert os.environ["AO_STATE_DIR"] == "/tmp/custom-state"

    def test_real_env_wins_over_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from agent_orchestrator.cli import _apply_isolation_state_dir_env

        monkeypatch.setenv("AO_STATE_DIR", "/real/state")
        cfg = ProjectConfig(isolation=IsolationConfig(state_dir="/tmp/custom-state"))
        _apply_isolation_state_dir_env(cfg)
        assert os.environ["AO_STATE_DIR"] == "/real/state"

    def test_no_op_when_cfg_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from agent_orchestrator.cli import _apply_isolation_state_dir_env

        monkeypatch.delenv("AO_STATE_DIR", raising=False)
        _apply_isolation_state_dir_env(None)
        assert "AO_STATE_DIR" not in os.environ

    def test_no_op_when_state_dir_absent_from_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from agent_orchestrator.cli import _apply_isolation_state_dir_env

        monkeypatch.delenv("AO_STATE_DIR", raising=False)
        _apply_isolation_state_dir_env(ProjectConfig())
        assert "AO_STATE_DIR" not in os.environ


# ---------------------------------------------------------------------------------------
# Fill-in semantics: a run-level override becomes WorkflowSpec.defaults.isolation ONLY --
# it never touches a task's own explicit `isolation` declaration (ADR-0006, scoped
# deliberately narrower than the HLD's literal "global kill switch"/"force every task"
# text for this Part A slice -- see this ticket's STATUS.md for the recorded decision).
# ---------------------------------------------------------------------------------------


class TestIsolationFillInIsAPureDefaultsOverride:
    def test_defaults_mutation_fills_in_inherit_only(self) -> None:
        from agent_orchestrator.models import (
            TaskSpec,
            WorkflowDefaults,
            WorkflowSpec,
            resolve_task_isolation,
        )

        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            defaults=WorkflowDefaults(isolation="none"),
            tasks=[
                TaskSpec(id="inherits", agent="ag", instruction="i.md"),
                TaskSpec(
                    id="explicit_worktree", agent="ag", instruction="i.md", isolation="worktree"
                ),
                TaskSpec(id="explicit_none", agent="ag", instruction="i.md", isolation="none"),
            ],
        )

        # Exactly what cli.py's run()/resume() do for a resolved isolation mode that isn't
        # None (i.e. some layer supplied an override).
        wf.defaults.isolation = "worktree"

        assert resolve_task_isolation(wf.task("inherits"), wf) == "worktree"  # filled in
        assert resolve_task_isolation(wf.task("explicit_worktree"), wf) == "worktree"
        assert resolve_task_isolation(wf.task("explicit_none"), wf) == "none"  # never clobbered


# ---------------------------------------------------------------------------------------
# End-to-end: the --isolation flag alone (no defaults.isolation in the spec) drives a
# real worktree lifecycle through the CLI, and ao status surfaces the result.
# ---------------------------------------------------------------------------------------


def _write_specs_no_isolation_declared(tmp_path: Path) -> None:
    (tmp_path / "specs" / "instructions").mkdir(parents=True, exist_ok=True)
    (tmp_path / "specs" / "instructions" / "task_a.md").write_text("# task_a\n")

    workflow = {
        "version": "1.0",
        "id": "iso-flag-e2e-wf",
        "repo_set": "rs",
        # No `defaults.isolation` at all -- proves --isolation alone (never a spec-level
        # declaration) drives worktree isolation end-to-end.
        "integration": {"sync_checkout": "never", "ladder": ["auto", "mechanical"]},
        "tasks": [
            {
                "id": "task_a",
                "agent": "ag",
                "instruction": "specs/instructions/task_a.md",
                "outputs": ["output/a.txt"],
            },
        ],
    }
    (tmp_path / "workflow.json").write_text(json.dumps(workflow))

    reposets = {
        "version": "1.0",
        "repo_sets": {
            "rs": {
                "workspace_root": str(tmp_path),
                "repos": [{"id": "core", "path": "repo", "role": "primary"}],
            }
        },
    }
    (tmp_path / "reposets.json").write_text(json.dumps(reposets))

    agents = {"version": "1.0", "agents": {"ag": {"executor": "fake"}}}
    (tmp_path / "agents.json").write_text(json.dumps(agents))


class TestIsolationFlagDrivesRealWorktree:
    def test_isolation_worktree_flag_lands_cleans_up_and_shows_in_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _git_repo(tmp_path)
        _write_specs_no_isolation_declared(tmp_path)
        _patch_dispatch_executor(monkeypatch, {"task_a": {"core": {"a_code.txt": "a code\n"}}})

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(tmp_path / "workflow.json"),
                "--reposets",
                str(tmp_path / "reposets.json"),
                "--agents",
                str(tmp_path / "agents.json"),
                "--isolation",
                "worktree",
            ],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, f"CLI failed:\n{result.output}"
        assert "succeeded" in result.output.lower()
        assert (tmp_path / "output" / "a.txt").exists()
        # ao run's own final _print_state already shows the integration summary.
        assert "Integration:" in result.output

        state = json.loads(
            next((tmp_path / ".orchestrator" / "runs").iterdir()).joinpath("state.json").read_text()
        )
        branch = state["integration"]["branch"]
        assert branch is not None
        shown = _git(["show", f"{branch}:a_code.txt"], repo)
        assert shown == "a code\n"

        # Checked-out branch (main) is untouched (sync_checkout: never).
        assert not (repo / "a_code.txt").exists()

        # Worktree cleaned per policy (default keep_worktrees=on_failure, task succeeded):
        # only the main worktree remains registered.
        listing = _git(["worktree", "list", "--porcelain"], repo)
        worktree_lines = [line for line in listing.splitlines() if line.startswith("worktree ")]
        assert worktree_lines == [f"worktree {repo}"]

        # `ao status --run-id` surfaces the same one-line integration summary (AC-8).
        status_result = runner.invoke(
            app,
            ["status", "--run-id", state["run_id"], "--workspace", str(tmp_path)],
        )
        assert status_result.exit_code == 0, status_result.output
        assert "Integration:" in status_result.output
        assert branch in status_result.output

    def test_isolation_none_flag_overrides_declared_worktree_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TASK.md AC-2's scenario for a task left at isolation="inherit": a workflow
        declaring `defaults.isolation: worktree` takes the shared-checkout path under
        `--isolation none` -- no worktree, no integration ref."""
        repo = _git_repo(tmp_path)
        (tmp_path / "specs" / "instructions").mkdir(parents=True, exist_ok=True)
        (tmp_path / "specs" / "instructions" / "task_a.md").write_text("# task_a\n")
        workflow = {
            "version": "1.0",
            "id": "iso-none-flag-wf",
            "repo_set": "rs",
            "defaults": {"isolation": "worktree"},
            "integration": {"ladder": ["auto", "mechanical"]},
            "tasks": [
                {
                    "id": "task_a",
                    "agent": "ag",
                    "instruction": "specs/instructions/task_a.md",
                    "outputs": ["output/a.txt"],
                }
            ],
        }
        (tmp_path / "workflow.json").write_text(json.dumps(workflow))
        reposets = {
            "version": "1.0",
            "repo_sets": {
                "rs": {
                    "workspace_root": str(tmp_path),
                    "repos": [{"id": "core", "path": "repo", "role": "primary"}],
                }
            },
        }
        (tmp_path / "reposets.json").write_text(json.dumps(reposets))
        (tmp_path / "agents.json").write_text(
            json.dumps({"version": "1.0", "agents": {"ag": {"executor": "fake"}}})
        )

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(tmp_path / "workflow.json"),
                "--reposets",
                str(tmp_path / "reposets.json"),
                "--agents",
                str(tmp_path / "agents.json"),
                "--isolation",
                "none",
            ],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, f"CLI failed:\n{result.output}"
        assert "Integration:" not in result.output  # degrades cleanly (AC-9 contract)

        state = json.loads(
            next((tmp_path / ".orchestrator" / "runs").iterdir()).joinpath("state.json").read_text()
        )
        assert state["integration"]["active"] is False
        assert state["integration"]["branch"] is None

        listing = _git(["worktree", "list", "--porcelain"], repo)
        worktree_lines = [line for line in listing.splitlines() if line.startswith("worktree ")]
        assert worktree_lines == [f"worktree {repo}"]  # no worktree was ever created


class TestNoIsolationKillSwitch:
    """C-1 coordinator decision (2026-09-07): `--no-isolation`/`AO_NO_ISOLATION` is a TRUE
    kill switch -- unlike `--isolation none`'s fill-in-only semantics, it overrides even a
    task's own explicit `isolation: "worktree"` declaration."""

    def _write_specs_with_explicit_worktree_tasks(self, tmp_path: Path) -> None:
        (tmp_path / "specs" / "instructions").mkdir(parents=True, exist_ok=True)
        for name in ("task_a", "task_b"):
            (tmp_path / "specs" / "instructions" / f"{name}.md").write_text(f"# {name}\n")
        workflow = {
            "version": "1.0",
            "id": "iso-kill-switch-wf",
            "repo_set": "rs",
            "defaults": {"isolation": "worktree"},
            "integration": {"ladder": ["auto", "mechanical"]},
            "tasks": [
                {
                    "id": "task_a",
                    "agent": "ag",
                    "instruction": "specs/instructions/task_a.md",
                    "isolation": "worktree",  # explicit -- the fill-in mechanism can't touch this
                    "outputs": ["output/a.txt"],
                },
                {
                    "id": "task_b",
                    "agent": "ag",
                    "instruction": "specs/instructions/task_b.md",
                    # isolation left at "inherit" -> defaults.isolation="worktree"
                    "outputs": ["output/b.txt"],
                },
            ],
        }
        (tmp_path / "workflow.json").write_text(json.dumps(workflow))
        reposets = {
            "version": "1.0",
            "repo_sets": {
                "rs": {
                    "workspace_root": str(tmp_path),
                    "repos": [{"id": "core", "path": "repo", "role": "primary"}],
                }
            },
        }
        (tmp_path / "reposets.json").write_text(json.dumps(reposets))
        (tmp_path / "agents.json").write_text(
            json.dumps({"version": "1.0", "agents": {"ag": {"executor": "fake"}}})
        )

    def test_cli_flag_forces_every_task_to_none_and_warns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _git_repo(tmp_path)
        self._write_specs_with_explicit_worktree_tasks(tmp_path)
        _patch_dispatch_executor(monkeypatch, {})

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(tmp_path / "workflow.json"),
                "--reposets",
                str(tmp_path / "reposets.json"),
                "--agents",
                str(tmp_path / "agents.json"),
                "--no-isolation",
            ],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, f"CLI failed:\n{result.output}"
        assert "WARNING: --no-isolation forced isolation='none'" in result.output
        assert "task_a" in result.output and "task_b" in result.output

        state = json.loads(
            next((tmp_path / ".orchestrator" / "runs").iterdir()).joinpath("state.json").read_text()
        )
        assert state["integration"]["active"] is False  # neither task ever isolated
        listing = _git(["worktree", "list", "--porcelain"], repo)
        worktree_lines = [line for line in listing.splitlines() if line.startswith("worktree ")]
        assert worktree_lines == [f"worktree {repo}"]

    def test_env_var_layer_has_the_same_effect(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _git_repo(tmp_path)
        self._write_specs_with_explicit_worktree_tasks(tmp_path)
        _patch_dispatch_executor(monkeypatch, {})

        env = _env(tmp_path)
        env["AO_NO_ISOLATION"] = "1"
        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(tmp_path / "workflow.json"),
                "--reposets",
                str(tmp_path / "reposets.json"),
                "--agents",
                str(tmp_path / "agents.json"),
            ],
            env=env,
        )
        assert result.exit_code == 0, f"CLI failed:\n{result.output}"

        state = json.loads(
            next((tmp_path / ".orchestrator" / "runs").iterdir()).joinpath("state.json").read_text()
        )
        assert state["integration"]["active"] is False
        listing = _git(["worktree", "list", "--porcelain"], repo)
        worktree_lines = [line for line in listing.splitlines() if line.startswith("worktree ")]
        assert worktree_lines == [f"worktree {repo}"]

    def test_fill_in_alone_never_flips_an_explicit_task(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Negative control: WITHOUT --no-isolation, --isolation none is fill-in-only and
        does NOT touch task_a's explicit isolation="worktree" -- it still isolates."""
        repo = _git_repo(tmp_path)
        self._write_specs_with_explicit_worktree_tasks(tmp_path)
        _patch_dispatch_executor(monkeypatch, {"task_a": {"core": {"a_code.txt": "a code\n"}}})

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(tmp_path / "workflow.json"),
                "--reposets",
                str(tmp_path / "reposets.json"),
                "--agents",
                str(tmp_path / "agents.json"),
                "--isolation",
                "none",
            ],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, f"CLI failed:\n{result.output}"

        state = json.loads(
            next((tmp_path / ".orchestrator" / "runs").iterdir()).joinpath("state.json").read_text()
        )
        # task_a's explicit isolation="worktree" survives the fill-in-only --isolation none.
        assert state["task_integration"]["task_a"]["isolation"] == "worktree"
        _ = repo  # repo fixture only needed to make the real git worktree machinery work


class TestIsolationCliValidationExitsCleanly:
    """Invalid --isolation reaches the standard `_load_all`-then-resolve flow and exits 1
    with a clear message, on both `run` and `resume` (AC-1)."""

    def _write_trivial_specs(self, tmp_path: Path) -> None:
        (tmp_path / "specs" / "instructions").mkdir(parents=True, exist_ok=True)
        (tmp_path / "specs" / "instructions" / "task_a.md").write_text("# task_a\n")
        workflow = {
            "version": "1.0",
            "id": "iso-invalid-wf",
            "repo_set": "rs",
            "tasks": [
                {
                    "id": "task_a",
                    "agent": "ag",
                    "instruction": "specs/instructions/task_a.md",
                    "outputs": ["output/a.txt"],
                }
            ],
        }
        (tmp_path / "workflow.json").write_text(json.dumps(workflow))
        reposets = {
            "version": "1.0",
            "repo_sets": {
                "rs": {"workspace_root": str(tmp_path), "repos": [{"id": "core", "path": "."}]}
            },
        }
        (tmp_path / "reposets.json").write_text(json.dumps(reposets))
        (tmp_path / "agents.json").write_text(
            json.dumps({"version": "1.0", "agents": {"ag": {"executor": "fake"}}})
        )

    def test_run_invalid_isolation_value_exits_1(self, tmp_path: Path) -> None:
        self._write_trivial_specs(tmp_path)
        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(tmp_path / "workflow.json"),
                "--reposets",
                str(tmp_path / "reposets.json"),
                "--agents",
                str(tmp_path / "agents.json"),
                "--isolation",
                "bogus",
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )
        assert result.exit_code == 1
        assert "must be one of" in result.output

    def test_resume_invalid_isolation_value_exits_1(self, tmp_path: Path) -> None:
        self._write_trivial_specs(tmp_path)
        result = runner.invoke(
            app,
            [
                "resume",
                "--run-id",
                "does-not-matter",
                "--workflow",
                str(tmp_path / "workflow.json"),
                "--reposets",
                str(tmp_path / "reposets.json"),
                "--agents",
                str(tmp_path / "agents.json"),
                "--isolation",
                "bogus",
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )
        assert result.exit_code == 1
        assert "must be one of" in result.output

    def test_run_invalid_isolation_config_mode_exits_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """C-2 (2026-09-07 review), outermost-boundary version of
        `TestResolveIsolationSettings::test_invalid_config_file_mode_exits_1_with_message`:
        a malformed `.ao/config.yaml isolation.mode` reaches `ao run` itself and exits 1
        with the actionable message, not a silent fall-through."""
        monkeypatch.chdir(tmp_path)  # so .ao/config.yaml below is discovered
        self._write_trivial_specs(tmp_path)
        (tmp_path / ".ao").mkdir()
        (tmp_path / ".ao" / "config.yaml").write_text("isolation:\n  mode: bogus\n")

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(tmp_path / "workflow.json"),
                "--reposets",
                str(tmp_path / "reposets.json"),
                "--agents",
                str(tmp_path / "agents.json"),
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )
        assert result.exit_code == 1
        assert "ERROR" in result.output

    def test_run_isolation_worktree_and_no_isolation_mutually_exclusive(
        self, tmp_path: Path
    ) -> None:
        self._write_trivial_specs(tmp_path)
        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(tmp_path / "workflow.json"),
                "--reposets",
                str(tmp_path / "reposets.json"),
                "--agents",
                str(tmp_path / "agents.json"),
                "--isolation",
                "worktree",
                "--no-isolation",
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )
        assert result.exit_code == 1
        assert "mutually exclusive" in result.output

    def test_resume_isolation_worktree_and_no_isolation_mutually_exclusive(
        self, tmp_path: Path
    ) -> None:
        self._write_trivial_specs(tmp_path)
        result = runner.invoke(
            app,
            [
                "resume",
                "--run-id",
                "does-not-matter",
                "--workflow",
                str(tmp_path / "workflow.json"),
                "--reposets",
                str(tmp_path / "reposets.json"),
                "--agents",
                str(tmp_path / "agents.json"),
                "--isolation",
                "worktree",
                "--no-isolation",
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )
        assert result.exit_code == 1
        assert "mutually exclusive" in result.output


class TestAoStatusIntegrationSummaryDegradesCleanly:
    def test_non_isolated_run_status_has_no_integration_line(self, tmp_path: Path) -> None:
        (tmp_path / "specs" / "instructions").mkdir(parents=True, exist_ok=True)
        (tmp_path / "specs" / "instructions" / "task_a.md").write_text("# task_a\n")
        workflow = {
            "version": "1.0",
            "id": "iso-none-wf",
            "repo_set": "rs",
            "tasks": [
                {
                    "id": "task_a",
                    "agent": "ag",
                    "instruction": "specs/instructions/task_a.md",
                    "outputs": ["output/a.txt"],
                }
            ],
        }
        (tmp_path / "workflow.json").write_text(json.dumps(workflow))
        reposets = {
            "version": "1.0",
            "repo_sets": {
                "rs": {"workspace_root": str(tmp_path), "repos": [{"id": "core", "path": "."}]}
            },
        }
        (tmp_path / "reposets.json").write_text(json.dumps(reposets))
        (tmp_path / "agents.json").write_text(
            json.dumps({"version": "1.0", "agents": {"ag": {"executor": "fake"}}})
        )

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(tmp_path / "workflow.json"),
                "--reposets",
                str(tmp_path / "reposets.json"),
                "--agents",
                str(tmp_path / "agents.json"),
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )
        assert result.exit_code == 0, result.output
        assert "Integration:" not in result.output

        run_id = next((tmp_path / ".orchestrator" / "runs").iterdir()).name
        status_result = runner.invoke(
            app, ["status", "--run-id", run_id, "--workspace", str(tmp_path)]
        )
        assert status_result.exit_code == 0
        assert "Integration:" not in status_result.output


class TestAoResumeHonoursFileLevelIsolationDefault:
    def test_resume_with_no_cli_flag_picks_up_config_isolation_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`ao resume` re-loads the project config on each invocation (like every other
        setting in `_resolve_run_settings`'s own chain) -- a `.ao/config.yaml` written
        AFTER the initial (non-isolated) crash is honoured on resume with no `--isolation`
        flag at all, isolating only the not-yet-dispatched task."""
        from agent_orchestrator.artifacts import LocalFsArtifactStore
        from agent_orchestrator.engine import Orchestrator
        from agent_orchestrator.executors.fake import FakeExecutor
        from agent_orchestrator.runstate import RunStateStore
        from agent_orchestrator.spec import load_workflow

        # `find_project_config` walks up from the process cwd, not from --workspace/
        # AO_WORKSPACE_ROOT -- chdir into tmp_path so the .ao/config.yaml written below is
        # actually discovered by `ao resume`.
        monkeypatch.chdir(tmp_path)

        repo = _git_repo(tmp_path)
        (tmp_path / "specs" / "instructions").mkdir(parents=True, exist_ok=True)
        for name in ("task_a", "task_b"):
            (tmp_path / "specs" / "instructions" / f"{name}.md").write_text(f"# {name}\n")
        workflow = {
            "version": "1.0",
            "id": "iso-resume-wf",
            "repo_set": "rs",
            "integration": {"sync_checkout": "never", "ladder": ["auto", "mechanical"]},
            "tasks": [
                {
                    "id": "task_a",
                    "agent": "ag",
                    "instruction": "specs/instructions/task_a.md",
                    "outputs": ["output/a.txt"],
                },
                {
                    "id": "task_b",
                    "agent": "ag",
                    "instruction": "specs/instructions/task_b.md",
                    "depends_on": ["task_a"],
                    "outputs": ["output/b.txt"],
                },
            ],
        }
        (tmp_path / "workflow.json").write_text(json.dumps(workflow))
        reposets = {
            "version": "1.0",
            "repo_sets": {
                "rs": {
                    "workspace_root": str(tmp_path),
                    "repos": [{"id": "core", "path": "repo", "role": "primary"}],
                }
            },
        }
        (tmp_path / "reposets.json").write_text(json.dumps(reposets))
        (tmp_path / "agents.json").write_text(
            json.dumps({"version": "1.0", "agents": {"ag": {"executor": "fake"}}})
        )

        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setenv("AO_STATE_DIR", str(tmp_path / "ao-state"))

        wf = load_workflow(str(tmp_path / "workflow.json"))
        import agent_orchestrator.config as config_mod

        reposet_map = config_mod.load_reposets(str(tmp_path / "reposets.json"))
        agent_map = config_mod.load_agents(str(tmp_path / "agents.json"))
        store = LocalFsArtifactStore(str(tmp_path))
        rs_store = RunStateStore(str(tmp_path), store)

        # First "process": task_a succeeds non-isolated (no override anywhere yet);
        # task_b fails outright, simulating the crash.
        orch1 = Orchestrator(FakeExecutor(behaviors={"task_b": "fail"}), store, rs_store)
        state1 = orch1.run(wf, reposet_map, agent_map)
        assert state1.status == "failed"
        run_id = state1.run_id
        assert state1.task_integration.get("task_a", None) is None or (
            state1.task_integration["task_a"].isolation == "none"
        )

        # Now a workspace-level config appears, declaring isolation.mode: worktree --
        # no --isolation flag is ever passed to resume.
        (tmp_path / ".ao").mkdir()
        (tmp_path / ".ao" / "config.yaml").write_text("isolation:\n  mode: worktree\n")

        _patch_dispatch_executor(
            monkeypatch,
            {
                "task_a": {"core": {"a_code.txt": "a code\n"}},
                "task_b": {"core": {"b_code.txt": "b code\n"}},
            },
        )
        result = runner.invoke(
            app,
            [
                "resume",
                "--run-id",
                run_id,
                "--workflow",
                str(tmp_path / "workflow.json"),
                "--reposets",
                str(tmp_path / "reposets.json"),
                "--agents",
                str(tmp_path / "agents.json"),
            ],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, f"CLI resume failed:\n{result.output}"
        assert "succeeded" in result.output.lower()

        final_state = json.loads(
            (tmp_path / ".orchestrator" / "runs" / run_id / "state.json").read_text()
        )
        # task_b, dispatched only on resume, picked up the config file's isolation.mode.
        assert final_state["task_integration"]["task_b"]["isolation"] == "worktree"
        assert final_state["task_integration"]["task_b"]["status"] == "integrated"
        branch = final_state["integration"]["branch"]
        assert branch is not None
        shown = _git(["show", f"{branch}:b_code.txt"], repo)
        assert shown == "b code\n"
