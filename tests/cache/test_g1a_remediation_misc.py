"""G1a remediation, small items: S-11 (key builder never leaks a raw OSError) and S-10 (drift
tripwire for the bounds that `models.py` and `cache/constants.py` deliberately both spell out)."""

from __future__ import annotations

import errno
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator import models
from agent_orchestrator.cache import constants as c
from agent_orchestrator.cache.constants import REASON_INPUT_UNREADABLE
from agent_orchestrator.cache.types import UncacheableError
from tests.cache.keys_fixture import key_for, write_files


@pytest.fixture(autouse=True)
def _cache_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AO_CACHE", raising=False)


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    write_files(root)
    return root


def _lstat_failing_in_keys(monkeypatch: pytest.MonkeyPatch, target: str, exc: OSError) -> None:
    """Make `os.lstat(target)` fail, but only when called from `cache/keys.py` (the guard's own
    path resolution must keep working)."""
    real = os.lstat

    def lstat(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        if os.fspath(path) == target and sys._getframe(1).f_code.co_filename.endswith("keys.py"):
            raise exc
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", lstat)


def test_a_prior_output_removed_between_the_checks_is_the_absent_prior(
    ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S-11: a concurrent unlink between `lexists` and `lstat` must not escape as a raw error."""
    target = str(ws.resolve() / "out" / "summary.md")
    (ws / "out").mkdir(exist_ok=True)
    (ws / "out" / "summary.md").write_text("prior")
    _lstat_failing_in_keys(monkeypatch, target, FileNotFoundError(errno.ENOENT, "gone", target))
    absent = key_for(ws).key
    monkeypatch.undo()
    (ws / "out" / "summary.md").unlink()
    assert key_for(ws).key == absent  # exactly the key of a genuinely absent prior


def test_an_unreadable_prior_output_is_uncacheable_not_a_raw_oserror(
    ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = str(ws.resolve() / "out" / "summary.md")
    (ws / "out").mkdir(exist_ok=True)
    (ws / "out" / "summary.md").write_text("prior")
    _lstat_failing_in_keys(monkeypatch, target, PermissionError(errno.EACCES, "denied", target))
    with pytest.raises(UncacheableError) as err:
        key_for(ws)
    assert err.value.reason == REASON_INPUT_UNREADABLE and err.value.detail == "out/summary.md"


@pytest.mark.parametrize(
    ("models_name", "cache_name"),
    [
        ("RESULT_CACHE_MAX_TEXT", "MAX_TEXT_CHARS"),
        ("RESULT_CACHE_MAX_REASON", "MAX_REASON_CHARS"),
        ("RESULT_CACHE_MAX_USD", "MAX_ENTRY_COST_USD"),
        ("RESULT_CACHE_MAX_TOKENS", "MAX_ENTRY_TOKENS"),
        ("RESULT_CACHE_MAX_SECONDS", "MAX_ENTRY_SECONDS"),
    ],
)
def test_the_state_record_bounds_equal_the_cache_entry_bounds(
    models_name: str, cache_name: str
) -> None:
    """S-10: `models.py` cannot import the cache package (layering), so each bound is spelled
    twice. This pins them equal."""
    assert getattr(models, models_name) == getattr(c, cache_name)


def test_the_key_pattern_in_models_matches_the_cache_hex_pattern() -> None:
    assert models.RESULT_CACHE_KEY_PATTERN == f"^{c.SHA256_HEX_RE.pattern}$"
