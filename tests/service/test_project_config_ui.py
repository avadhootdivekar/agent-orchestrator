"""Tests for `ProjectConfig.ui` (AC4/AC5, E-GIytcL FR-7): additive `UIConfig` field used by
the multi-workspace service's P1 port resolution."""

from __future__ import annotations

from pathlib import Path

from agent_orchestrator.project_config import ProjectConfig, UIConfig, load_project_config


def _write_config(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class TestUIConfigDefaults:
    def test_default_project_config_has_default_ui_config(self) -> None:
        cfg = ProjectConfig()
        assert cfg.ui == UIConfig()
        assert cfg.ui.port is None

    def test_ui_config_round_trips_port(self) -> None:
        cfg = ProjectConfig(ui=UIConfig(port=8899))
        assert cfg.ui.port == 8899


class TestLoadProjectConfigUI:
    def test_config_without_ui_key_yields_defaults(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".ao" / "config.yaml"
        _write_config(config_path, "workflow: wf.json\n")

        cfg = load_project_config(config_path)

        assert cfg.ui == UIConfig()
        assert cfg.ui.port is None

    def test_config_with_ui_port_is_loaded(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".ao" / "config.yaml"
        _write_config(config_path, "ui:\n  port: 8899\n")

        cfg = load_project_config(config_path)

        assert cfg.ui.port == 8899

    def test_old_config_unaware_of_ui_key_is_forward_compatible(self, tmp_path: Path) -> None:
        """A config predating this field only has known top-level keys -- confirms
        `extra="ignore"` (pydantic v2 default, unchanged) round-trips cleanly either way."""
        config_path = tmp_path / ".ao" / "config.yaml"
        _write_config(config_path, "workflow: wf.json\nreposets: rs.json\nagents: ag.json\n")

        cfg = load_project_config(config_path)

        # `load_project_config` resolves relative paths against the config file's directory.
        assert cfg.workflow == str(config_path.parent / "wf.json")
        assert cfg.ui.port is None

    def test_new_key_ignored_by_hypothetical_older_schema_is_simulated_via_extra_field(
        self, tmp_path: Path
    ) -> None:
        """A config carrying an unfamiliar top-level key (simulating a NEWER config being
        read by conceptually older code that doesn't know about it) does not raise, thanks
        to `extra="ignore"`."""
        config_path = tmp_path / ".ao" / "config.yaml"
        _write_config(config_path, "workflow: wf.json\nsome_future_unknown_key: {nested: true}\n")

        cfg = load_project_config(config_path)

        assert cfg.workflow == str(config_path.parent / "wf.json")
        assert cfg.ui.port is None
