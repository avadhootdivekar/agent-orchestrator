"""T-28J9oR AC-7..AC-10: mode matrix and author policy (U-S1..U-S4)."""

from __future__ import annotations

import itertools

import pytest

from agent_orchestrator.cache import constants
from agent_orchestrator.cache.settings import (
    ResultCacheSettings,
    opted_in_count,
    resolve_result_cache_settings,
    task_cache_policy,
)
from agent_orchestrator.models import TaskSpec, WorkflowDefaults, WorkflowSpec
from agent_orchestrator.project_config import CacheConfig


def _resolve(
    cli: bool | None = None,
    env: dict[str, str] | None = None,
    cfg: CacheConfig | None = None,
) -> tuple[ResultCacheSettings, list[str]]:
    return resolve_result_cache_settings(cli, env or {}, cfg)


class TestModeMatrix:
    """U-S1."""

    @pytest.mark.parametrize(
        ("cli", "mode"), [(True, "on"), (False, "off")], ids=["cli-on", "cli-off"]
    )
    def test_cli_flag_wins_over_everything(self, cli: bool, mode: str) -> None:
        cfg = CacheConfig(enabled=not cli)
        s, w = _resolve(cli, {"AO_CACHE": "shadow"}, cfg)
        assert (s.mode, s.source, w) == (mode, "cli", [])

    @pytest.mark.parametrize(
        ("raw", "mode"),
        [
            ("1", "on"),
            ("true", "on"),
            ("yes", "on"),
            (" ON ", "on"),
            ("0", "off"),
            ("false", "off"),
            ("no", "off"),
            ("off", "off"),
            ("shadow", "shadow"),
            (" Shadow ", "shadow"),
        ],
    )
    def test_env_values(self, raw: str, mode: str) -> None:
        s, w = _resolve(None, {"AO_CACHE": raw}, CacheConfig(enabled=True))
        assert (s.mode, s.source, w) == (mode, "env", [])

    @pytest.mark.parametrize("raw", ["refresh", "maybe", "2", "onn"])
    def test_unknown_env_is_off_with_exactly_one_warning(self, raw: str) -> None:
        # Fail closed even though the config would enable it.
        s, w = _resolve(None, {"AO_CACHE": raw}, CacheConfig(enabled=True))
        assert (s.mode, s.source) == ("off", "env")
        assert len(w) == 1
        assert f"AO_CACHE={raw!r} not recognised" in w[0]
        assert "result cache OFF" in w[0]

    @pytest.mark.parametrize("env", [{}, {"AO_CACHE": ""}, {"AO_CACHE": "   "}])
    def test_unset_or_blank_env_falls_through(self, env: dict[str, str]) -> None:
        s, w = _resolve(None, env, None)
        assert (s.mode, s.source, w) == ("off", "default", [])
        s2, _ = _resolve(None, env, CacheConfig(enabled=True))
        assert (s2.mode, s2.source) == ("on", "config")

    def test_cli_wins_over_shadow_env(self) -> None:
        s, _ = _resolve(True, {"AO_CACHE": "shadow"})
        assert s.mode == "on"

    @pytest.mark.parametrize("mode", ["on", "shadow"])
    def test_config_enabled_uses_config_mode(self, mode: str) -> None:
        s, w = _resolve(cfg=CacheConfig(enabled=True, mode=mode))  # type: ignore[arg-type]
        assert (s.mode, s.source, w) == (mode, "config", [])

    def test_config_disabled_is_off(self) -> None:
        s, _ = _resolve(cfg=CacheConfig(enabled=False))
        assert (s.mode, s.source) == ("off", "config")

    def test_config_mode_without_enabled_is_off(self) -> None:
        s, _ = _resolve(cfg=CacheConfig(mode="shadow"))
        assert (s.mode, s.source) == ("off", "default")

    def test_no_inputs_is_off_default(self) -> None:
        s, w = _resolve()
        assert (s.mode, s.source, w) == ("off", "default", [])

    def test_limits_come_from_config_or_defaults(self) -> None:
        s, _ = _resolve()
        assert s.max_bytes == constants.DEFAULT_CACHE_MAX_BYTES
        assert s.max_entry_bytes == constants.DEFAULT_CACHE_MAX_ENTRY_BYTES
        assert s.ttl_days == constants.DEFAULT_CACHE_TTL_DAYS
        assert s.include_repo_heads is constants.DEFAULT_CACHE_INCLUDE_REPO_HEADS
        assert s.max_input_bytes == constants.DEFAULT_CACHE_MAX_INPUT_BYTES
        assert s.max_input_files == constants.DEFAULT_CACHE_MAX_INPUT_FILES
        small, _ = _resolve(cfg=CacheConfig(max_bytes=1_000_000, ttl_days=None))
        assert small.max_entry_bytes == 1_000_000
        assert small.ttl_days is None

    def test_settings_are_frozen(self) -> None:
        s, _ = _resolve()
        with pytest.raises(AttributeError):
            s.mode = "on"  # type: ignore[misc]


def _wf(tasks: list[TaskSpec], default: bool | None = None) -> WorkflowSpec:
    return WorkflowSpec(
        version="1.0",
        id="wf",
        repo_set="rs",
        defaults=WorkflowDefaults(cache=default),
        tasks=tasks,
    )


def _t(tid: str = "t", cache: bool | None = None) -> TaskSpec:
    return TaskSpec(id=tid, agent="a", instruction="i", cache=cache)


class TestAuthorPolicy:
    """U-S2."""

    @pytest.mark.parametrize(
        ("task", "default"), list(itertools.product([False, True, None], [False, True, None]))
    )
    def test_task_beats_defaults_beats_flip_point(
        self, task: bool | None, default: bool | None
    ) -> None:
        if task is not None:
            expected = task
        elif default is not None:
            expected = default
        else:
            expected = constants.DEFAULT_TASK_CACHE_POLICY
        wf = _wf([_t(cache=task)], default)
        assert task_cache_policy(wf.tasks[0], wf, injected=False) is expected

    def test_unset_unset_is_false(self) -> None:
        wf = _wf([_t()])
        assert task_cache_policy(wf.tasks[0], wf, injected=False) is False
        assert task_cache_policy(wf.tasks[0], wf, injected=True) is False

    @pytest.mark.parametrize("default", [False, None])
    def test_injected_true_is_ignored(self, default: bool | None) -> None:
        wf = _wf([_t(cache=True)], default)
        assert task_cache_policy(wf.tasks[0], wf, injected=True) is False

    def test_injected_true_falls_back_to_defaults_true(self) -> None:
        wf = _wf([_t(cache=True)], True)
        assert task_cache_policy(wf.tasks[0], wf, injected=True) is True

    def test_injected_false_narrows_defaults_true(self) -> None:
        wf = _wf([_t(cache=False)], True)
        assert task_cache_policy(wf.tasks[0], wf, injected=True) is False


class TestOptedInCount:
    """U-S3."""

    def test_counts_static_tasks(self) -> None:
        wf = _wf([_t("a", True), _t("b"), _t("c", False), _t("d", True)])
        assert opted_in_count(wf) == (2, 4)

    def test_defaults_true_counts_unset_tasks(self) -> None:
        wf = _wf([_t("a"), _t("b", False)], True)
        assert opted_in_count(wf) == (1, 2)

    def test_none_opted_in(self) -> None:
        assert opted_in_count(_wf([_t("a"), _t("b")])) == (0, 2)


class TestFlipPointPin:
    """U-S4 (ADR-0019 D1)."""

    def test_flip_point_is_false(self) -> None:
        assert constants.DEFAULT_TASK_CACHE_POLICY is False, (
            "DEFAULT_TASK_CACHE_POLICY changed: flipping the author-policy default needs an "
            "ADR-0019 D1 addendum (double opt-in -> operator-only opt-in); see HLD 8.1.3"
        )

    def test_unset_levels_return_the_constant(self) -> None:
        wf = _wf([_t()])
        assert (
            task_cache_policy(wf.tasks[0], wf, injected=False)
            is constants.DEFAULT_TASK_CACHE_POLICY
        )

    def test_monkeypatching_the_constant_flips_the_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wf = _wf([_t()])
        monkeypatch.setattr(constants, "DEFAULT_TASK_CACHE_POLICY", True)
        assert task_cache_policy(wf.tasks[0], wf, injected=False) is True
        assert opted_in_count(wf) == (1, 1)
        # An explicit author value still wins over the flipped default.
        off = _wf([_t(cache=False)])
        assert task_cache_policy(off.tasks[0], off, injected=False) is False
