"""Tests for per-project config discovery, schema validation, and `ao init`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_orchestrator.cli import app
from agent_orchestrator.errors import ConfigError
from agent_orchestrator.project_config import (
    IsolationConfig,
    ProjectConfig,
    find_project_config,
    load_project_config,
    scaffold_init,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(path: Path, content: str) -> None:
    """Write config content to a path, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _minimal_config(**kwargs) -> str:
    import yaml

    return (
        yaml.dump(kwargs) if kwargs else "workflow: wf.json\nreposets: rs.json\nagents: ag.json\n"
    )


# ---------------------------------------------------------------------------
# ProjectConfig schema tests
# ---------------------------------------------------------------------------


class TestProjectConfigSchema:
    def test_all_fields_optional(self) -> None:
        cfg = ProjectConfig()
        assert cfg.workflow is None
        assert cfg.reposets is None
        assert cfg.agents is None
        assert cfg.workspace_root is None
        assert cfg.env == {}

    def test_valid_full_config(self) -> None:
        cfg = ProjectConfig(
            workflow="wf.json",
            reposets="rs.json",
            agents="ag.json",
            workspace_root="/tmp/ws",
            env={"FOO": "bar"},
        )
        assert cfg.workflow == "wf.json"
        assert cfg.env == {"FOO": "bar"}

    def test_env_coerces_to_str_mapping(self) -> None:
        cfg = ProjectConfig(env={"A": 1, "B": 2})  # type: ignore[arg-type]
        assert cfg.env == {"A": "1", "B": "2"}

    def test_env_none_becomes_empty_dict(self) -> None:
        cfg = ProjectConfig(env=None)  # type: ignore[arg-type]
        assert cfg.env == {}

    def test_env_non_mapping_raises(self) -> None:
        with pytest.raises(Exception):
            ProjectConfig(env="not-a-dict")  # type: ignore[arg-type]

    def test_max_parallel_round_trips(self) -> None:
        """T-JXiI9j AC-4: ProjectConfig(max_parallel=3) round-trips."""
        cfg = ProjectConfig(max_parallel=3)
        assert cfg.max_parallel == 3

    def test_max_parallel_absent_defaults_to_none(self) -> None:
        """T-JXiI9j AC-4: a config with no max_parallel key loads with max_parallel is None
        (byte-identical to pre-task behavior for every existing config file)."""
        cfg = ProjectConfig()
        assert cfg.max_parallel is None

    def test_isolation_absent_is_all_defaults(self) -> None:
        """E-Wk9Tz3 AC-4: an absent `isolation:` block (every pre-epic config) loads with
        IsolationConfig()'s all-defaults -- byte-identical to pre-epic behavior."""
        cfg = ProjectConfig()
        assert cfg.isolation == IsolationConfig()
        assert cfg.isolation.mode is None
        assert cfg.isolation.strict is False
        assert cfg.isolation.state_dir is None
        assert cfg.isolation.env == {}


# ---------------------------------------------------------------------------
# IsolationConfig schema tests (E-Wk9Tz3 HLD §11 M9, AC-4)
# ---------------------------------------------------------------------------


class TestIsolationConfigSchema:
    def test_round_trips_every_field(self) -> None:
        cfg = IsolationConfig(
            mode="worktree",
            strict=True,
            state_dir="/tmp/ao-state",
            env={"core": {"CARGO_TARGET_DIR": "/abs/shared/target"}},
        )
        assert cfg.mode == "worktree"
        assert cfg.strict is True
        assert cfg.state_dir == "/tmp/ao-state"
        assert cfg.env == {"core": {"CARGO_TARGET_DIR": "/abs/shared/target"}}

    def test_mode_rejects_unknown_literal(self) -> None:
        with pytest.raises(Exception):
            IsolationConfig(mode="bogus")  # type: ignore[arg-type]

    def test_mode_rejects_auto_dropped_2026_09_07(self) -> None:
        """C-1 (2026-09-07 review): "auto" was removed from the valid values -- no
        `models.py`/HLD constant for an isolation-mode "auto" was ever found; "no override"
        is now represented by `mode=None`, not a third string value."""
        with pytest.raises(Exception):
            IsolationConfig(mode="auto")  # type: ignore[arg-type]

    def test_mode_none_is_the_default_and_a_valid_explicit_value(self) -> None:
        assert IsolationConfig().mode is None
        assert IsolationConfig(mode=None).mode is None

    def test_env_none_becomes_empty_dict(self) -> None:
        cfg = IsolationConfig(env=None)  # type: ignore[arg-type]
        assert cfg.env == {}

    def test_env_coerces_nested_values_to_str(self) -> None:
        cfg = IsolationConfig(env={"core": {"MAX": 4}})  # type: ignore[arg-type]
        assert cfg.env == {"core": {"MAX": "4"}}

    def test_env_non_mapping_raises(self) -> None:
        with pytest.raises(Exception):
            IsolationConfig(env="not-a-dict")  # type: ignore[arg-type]

    def test_env_non_nested_mapping_raises(self) -> None:
        """Each repo id's value must itself be a mapping (VAR -> value), not a bare
        scalar -- `isolation.env: {core: not-a-dict}` is a config error, not silently
        coerced."""
        with pytest.raises(Exception):
            IsolationConfig(env={"core": "not-a-dict"})  # type: ignore[arg-type]

    def test_load_project_config_parses_full_isolation_block(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".ao" / "config.yaml"
        _write_config(
            config_path,
            "isolation:\n"
            "  mode: worktree\n"
            "  strict: true\n"
            "  state_dir: /tmp/custom-state\n"
            "  env:\n"
            "    core:\n"
            "      CARGO_TARGET_DIR: /abs/shared/target\n",
        )
        cfg = load_project_config(config_path)
        assert cfg.isolation.mode == "worktree"
        assert cfg.isolation.strict is True
        assert cfg.isolation.state_dir == "/tmp/custom-state"
        assert cfg.isolation.env == {"core": {"CARGO_TARGET_DIR": "/abs/shared/target"}}

    def test_load_project_config_rejects_bad_isolation_mode(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".ao" / "config.yaml"
        _write_config(config_path, "isolation:\n  mode: bogus\n")
        with pytest.raises(ConfigError):
            load_project_config(config_path)


# ---------------------------------------------------------------------------
# load_project_config tests
# ---------------------------------------------------------------------------


class TestLoadProjectConfig:
    def test_loads_yaml_with_paths(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / ".ao" / "config.yaml"
        _write_config(
            cfg_path,
            "workflow: workflow.json\nreposets: rs.json\nagents: ag.json\n",
        )
        cfg = load_project_config(cfg_path)
        # Relative paths should be resolved to absolute, anchored at config dir.
        assert cfg.workflow == str((tmp_path / ".ao" / "workflow.json").resolve())
        assert cfg.reposets == str((tmp_path / ".ao" / "rs.json").resolve())
        assert cfg.agents == str((tmp_path / ".ao" / "ag.json").resolve())

    def test_absolute_path_not_re_resolved(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "ao.yaml"
        cfg_path.write_text("workflow: /absolute/wf.json\n")
        cfg = load_project_config(cfg_path)
        assert cfg.workflow == "/absolute/wf.json"

    def test_missing_file_raises_config_error(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="not found"):
            load_project_config(tmp_path / "nonexistent.yaml")

    def test_invalid_yaml_raises_config_error(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "ao.yaml"
        cfg_path.write_text(": invalid: yaml: [\n")
        with pytest.raises(ConfigError):
            load_project_config(cfg_path)

    def test_non_mapping_yaml_raises_config_error(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "ao.yaml"
        cfg_path.write_text("- item1\n- item2\n")
        with pytest.raises(ConfigError, match="mapping"):
            load_project_config(cfg_path)

    def test_env_loaded_from_config(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "ao.yaml"
        cfg_path.write_text("env:\n  MY_KEY: my_val\n")
        cfg = load_project_config(cfg_path)
        assert cfg.env == {"MY_KEY": "my_val"}


# ---------------------------------------------------------------------------
# find_project_config — walk-up logic
# ---------------------------------------------------------------------------


class TestFindProjectConfig:
    def test_finds_ao_config_yaml_in_cwd(self, tmp_path: Path) -> None:
        cfg = tmp_path / ".ao" / "config.yaml"
        _write_config(cfg, "workflow: wf.json\n")
        found = find_project_config(tmp_path)
        assert found == cfg.resolve()

    def test_finds_ao_yaml_in_cwd(self, tmp_path: Path) -> None:
        cfg = tmp_path / "ao.yaml"
        cfg.write_text("workflow: wf.json\n")
        found = find_project_config(tmp_path)
        assert found == cfg.resolve()

    def test_prefers_ao_config_yaml_over_ao_yaml(self, tmp_path: Path) -> None:
        """`.ao/config.yaml` takes precedence over `ao.yaml` in the same dir."""
        cfg1 = tmp_path / ".ao" / "config.yaml"
        _write_config(cfg1, "workflow: wf.json\n")
        cfg2 = tmp_path / "ao.yaml"
        cfg2.write_text("workflow: other.json\n")
        found = find_project_config(tmp_path)
        assert found == cfg1.resolve()

    def test_walks_up_to_parent(self, tmp_path: Path) -> None:
        parent_cfg = tmp_path / ".ao" / "config.yaml"
        _write_config(parent_cfg, "workflow: wf.json\n")
        subdir = tmp_path / "subdir" / "nested"
        subdir.mkdir(parents=True)
        found = find_project_config(subdir)
        assert found == parent_cfg.resolve()

    def test_stops_at_git_boundary(self, tmp_path: Path) -> None:
        """Walk-up should stop when it hits a .git directory."""
        # Place config in grandparent, git root in parent — should NOT find the config.
        grandparent_cfg = tmp_path / ".ao" / "config.yaml"
        _write_config(grandparent_cfg, "workflow: wf.json\n")

        git_root = tmp_path / "project"
        (git_root / ".git").mkdir(parents=True)  # fake git root
        subdir = git_root / "src"
        subdir.mkdir()

        found = find_project_config(subdir)
        assert found is None

    def test_returns_none_when_no_config_found(self, tmp_path: Path) -> None:
        subdir = tmp_path / "a" / "b" / "c"
        subdir.mkdir(parents=True)
        # Ensure we don't walk out of tmp_path by placing a fake git root there.
        (tmp_path / ".git").mkdir()
        found = find_project_config(subdir)
        assert found is None

    def test_finds_config_in_git_root_itself(self, tmp_path: Path) -> None:
        """Config file in the git root directory is found (stop-at-git checks after config)."""
        (tmp_path / ".git").mkdir()
        cfg = tmp_path / ".ao" / "config.yaml"
        _write_config(cfg, "workflow: wf.json\n")
        found = find_project_config(tmp_path)
        assert found == cfg.resolve()


# ---------------------------------------------------------------------------
# scaffold_init / ao init command
# ---------------------------------------------------------------------------


class TestScaffoldInit:
    def test_creates_ao_config_yaml(self, tmp_path: Path) -> None:
        result = scaffold_init(tmp_path)
        assert result == (tmp_path / ".ao" / "config.yaml").resolve()
        assert result.exists()

    def test_file_contains_expected_comments(self, tmp_path: Path) -> None:
        scaffold_init(tmp_path)
        content = (tmp_path / ".ao" / "config.yaml").read_text()
        assert "workflow" in content
        assert "reposets" in content
        assert "agents" in content
        assert "ao init" in content or "Generated" in content

    def test_file_contains_max_parallel_comment(self, tmp_path: Path) -> None:
        """T-JXiI9j AC-6: the scaffolded template documents max_parallel + AO_MAX_PARALLEL."""
        scaffold_init(tmp_path)
        content = (tmp_path / ".ao" / "config.yaml").read_text()
        assert "max_parallel" in content
        assert "AO_MAX_PARALLEL" in content

    def test_file_documents_isolation_block_and_reserved_namespace(self, tmp_path: Path) -> None:
        """E-Wk9Tz3 AC-4/S-8: the scaffolded template documents every `isolation:` key and
        states the `ao/` ref-namespace reservation explicitly."""
        scaffold_init(tmp_path)
        content = (tmp_path / ".ao" / "config.yaml").read_text()
        assert "isolation:" in content
        assert "mode" in content and "AO_ISOLATION" in content
        assert "strict" in content
        assert "state_dir" in content
        assert "env" in content
        assert "refs/heads/ao/" in content and "reserved" in content.lower()
        assert "--no-isolation" in content

    def test_raises_if_file_already_exists(self, tmp_path: Path) -> None:
        scaffold_init(tmp_path)  # create once
        with pytest.raises(ConfigError, match="already exists"):
            scaffold_init(tmp_path)  # second call should fail

    def test_creates_parent_dir_if_missing(self, tmp_path: Path) -> None:
        target = tmp_path / "new_project"
        # target does not exist yet
        result = scaffold_init(target)
        assert result.exists()

    def test_default_is_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = scaffold_init()
        assert result.parent.parent == tmp_path


class TestInitCommand:
    def test_init_creates_config(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", "--dir", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "Created" in result.output
        assert (tmp_path / ".ao" / "config.yaml").exists()

    def test_init_prints_next_steps(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", "--dir", str(tmp_path)])
        assert "Next steps" in result.output

    def test_init_fails_if_config_exists(self, tmp_path: Path) -> None:
        cfg = tmp_path / ".ao" / "config.yaml"
        _write_config(cfg, "workflow: wf.json\n")
        result = runner.invoke(app, ["init", "--dir", str(tmp_path)])
        assert result.exit_code == 1
        assert "already exists" in result.output


# ---------------------------------------------------------------------------
# CLI integration: config-file defaults vs flag override
# ---------------------------------------------------------------------------


def _write_spec_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Write minimal valid workflow, reposets, and agents files in tmp_path."""
    wf = tmp_path / "workflow.json"
    rs = tmp_path / "reposets.json"
    ag = tmp_path / "agents.json"

    wf.write_text(
        json.dumps(
            {
                "version": "1.0",
                "id": "test-wf",
                "repo_set": "default-set",
                "tasks": [
                    {
                        "id": "t1",
                        "agent": "ag1",
                        "instruction": "instr.md",
                        "outputs": ["output/result.txt"],
                    }
                ],
            }
        )
    )
    rs.write_text(
        json.dumps(
            {
                "version": "1.0",
                "repo_sets": {
                    "default-set": {
                        "workspace_root": str(tmp_path),
                        "repos": [{"id": "core", "path": ".", "role": "primary"}],
                    }
                },
            }
        )
    )
    ag.write_text(json.dumps({"version": "1.0", "agents": {"ag1": {"executor": "fake"}}}))
    return wf, rs, ag


class TestCliConfigDiscovery:
    def test_validate_uses_project_config_when_no_flags(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wf, rs, ag = _write_spec_files(tmp_path)

        # Write project config pointing at these files.
        import yaml

        cfg_path = tmp_path / ".ao" / "config.yaml"
        _write_config(
            cfg_path,
            yaml.dump(
                {
                    "workflow": str(wf),
                    "reposets": str(rs),
                    "agents": str(ag),
                }
            ),
        )

        # Change working directory to tmp_path so discovery picks it up.
        monkeypatch.chdir(tmp_path)
        # Clear env vars that could supply paths.
        monkeypatch.delenv("AO_WORKFLOW", raising=False)
        monkeypatch.delenv("AO_REPOSETS", raising=False)
        monkeypatch.delenv("AO_AGENTS", raising=False)

        result = runner.invoke(app, ["validate"])
        assert result.exit_code == 0, result.output
        assert "OK" in result.output

    def test_cli_flag_overrides_project_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Passing --workflow explicitly should override what's in the project config."""
        wf, rs, ag = _write_spec_files(tmp_path)

        # Project config points at non-existent files (to confirm they'd fail if used).
        import yaml

        cfg_path = tmp_path / ".ao" / "config.yaml"
        _write_config(
            cfg_path,
            yaml.dump(
                {
                    "workflow": "/nonexistent/wf.json",
                    "reposets": "/nonexistent/rs.json",
                    "agents": "/nonexistent/ag.json",
                }
            ),
        )

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("AO_WORKFLOW", raising=False)
        monkeypatch.delenv("AO_REPOSETS", raising=False)
        monkeypatch.delenv("AO_AGENTS", raising=False)

        # Explicit flags should win over project config.
        result = runner.invoke(
            app,
            [
                "validate",
                "--workflow",
                str(wf),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "OK" in result.output

    def test_missing_required_args_shows_helpful_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("AO_WORKFLOW", raising=False)
        monkeypatch.delenv("AO_REPOSETS", raising=False)
        monkeypatch.delenv("AO_AGENTS", raising=False)

        result = runner.invoke(app, ["validate"])
        assert result.exit_code == 1
        # Should mention how to fix it.
        assert "workflow" in result.output.lower() or "workflow" in (result.stderr or "").lower()
