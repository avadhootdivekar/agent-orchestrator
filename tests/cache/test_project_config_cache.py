"""T-28J9oR AC-6: `CacheConfig` and the `ao init` template (U-C1..U-C4)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agent_orchestrator.cache import constants as c
from agent_orchestrator.errors import ConfigError
from agent_orchestrator.project_config import (
    _INIT_TEMPLATE,
    CacheConfig,
    ProjectConfig,
    load_project_config,
)


class TestDefaults:
    def test_defaults_equal_constants(self) -> None:
        cfg = CacheConfig()
        assert cfg.enabled is None
        assert cfg.mode == c.MODE_ON
        assert cfg.max_bytes == c.DEFAULT_CACHE_MAX_BYTES
        assert cfg.max_entry_bytes is None
        assert cfg.effective_max_entry_bytes == c.DEFAULT_CACHE_MAX_ENTRY_BYTES
        assert cfg.ttl_days == c.DEFAULT_CACHE_TTL_DAYS
        assert cfg.include_repo_heads is c.DEFAULT_CACHE_INCLUDE_REPO_HEADS
        assert cfg.max_input_bytes == c.DEFAULT_CACHE_MAX_INPUT_BYTES
        assert cfg.max_input_files == c.DEFAULT_CACHE_MAX_INPUT_FILES

    def test_project_config_default_block(self) -> None:
        assert ProjectConfig().cache == CacheConfig()

    def test_literal_mode_default_matches_constant(self) -> None:
        # project_config uses the literal "on" (mypy cannot narrow the str constant).
        assert CacheConfig().mode == c.MODE_ON == "on"


class TestRejected:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"max_bytes": 0},
            {"max_bytes": 2**51},
            {"ttl_days": 0},
            {"ttl_days": 40000},
            {"mode": "refresh"},
            {"mode": "sometimes"},
            {"enabled": "yes"},
            {"max_entry_bytes": 0},
            {"max_input_bytes": 0},
            {"max_input_files": 0},
            {"max_input_files": 10**7 + 1},
            {"include_repo_heads": "yes"},
        ],
    )
    def test_invalid(self, kwargs: dict) -> None:
        with pytest.raises(ValidationError):
            CacheConfig(**kwargs)

    def test_entry_above_total_rejected_with_message(self) -> None:
        with pytest.raises(ValidationError) as exc:
            CacheConfig(max_bytes=1000, max_entry_bytes=2000)
        assert "cache.max_entry_bytes (2000) must be <= cache.max_bytes (1000)" in str(exc.value)


class TestAccepted:
    def test_small_max_bytes_alone_clamps_entry_cap(self) -> None:
        cfg = CacheConfig(max_bytes=1_000_000)
        assert cfg.effective_max_entry_bytes == 1_000_000

    def test_explicit_entry_cap_wins(self) -> None:
        assert CacheConfig(max_entry_bytes=5).effective_max_entry_bytes == 5

    def test_ttl_none_means_never_expire(self) -> None:
        assert CacheConfig(ttl_days=None).ttl_days is None

    def test_shadow_mode(self) -> None:
        assert CacheConfig(enabled=True, mode="shadow").mode == "shadow"


class TestInitTemplate:
    def _uncomment_cache_block(self) -> str:
        out: list[str] = []
        in_block = False
        for line in _INIT_TEMPLATE.splitlines():
            if line.startswith("# cache:"):
                in_block = True
                out.append(line[2:])
            elif in_block and line.startswith("#   "):
                out.append(line[2:])
            else:
                in_block = False
        return "\n".join(out) + "\n"

    def test_template_cache_block_parses_into_valid_config(self, tmp_path: Path) -> None:
        text = self._uncomment_cache_block()
        assert text.startswith("cache:")
        assert yaml.safe_load(text)["cache"]["max_bytes"] == c.DEFAULT_CACHE_MAX_BYTES
        path = tmp_path / "config.yaml"
        path.write_text(text, encoding="utf-8")
        cfg = load_project_config(path)
        assert cfg.cache.enabled is False
        assert cfg.cache.mode == "on"
        assert cfg.cache.ttl_days == c.DEFAULT_CACHE_TTL_DAYS
        assert cfg.cache.effective_max_entry_bytes == c.DEFAULT_CACHE_MAX_ENTRY_BYTES

    def test_template_lines_fit_100_columns(self) -> None:
        assert all(len(ln) <= 100 for ln in _INIT_TEMPLATE.splitlines() if "cache" in ln.lower())

    def test_whole_template_still_loads(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text(_INIT_TEMPLATE, encoding="utf-8")
        assert load_project_config(path).cache == CacheConfig()


class TestConfigFile:
    def test_refresh_mode_in_file_is_config_error(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("cache:\n  enabled: true\n  mode: refresh\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_project_config(path)

    def test_ttl_zero_in_file_is_config_error(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("cache:\n  ttl_days: 0\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_project_config(path)
