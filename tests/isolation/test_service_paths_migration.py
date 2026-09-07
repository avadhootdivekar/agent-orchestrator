"""Equivalence proof for `service/paths.py::default_state_dir`'s migration onto
`xdg.resolve_state_dir` (E-Wk9Tz3 T-Wk3Nv6, R-11).

`tests/service/test_paths.py` is the primary gate (it must pass UNEDITED -- proving the
migrated function still returns the exact literal paths the old hand-rolled precedence did).
This file is the belt-and-suspenders companion: it asserts `default_state_dir()` and
`xdg.resolve_state_dir(...)` agree, called directly, for every precedence branch -- so a
future edit to either side that silently diverges them is caught here even if neither
individual assertion in `tests/service/test_paths.py` happens to change.

`default_registry_path` is NOT part of this migration (see `service/paths.py`'s module
docstring and the T-Wk3Nv6 ticket's own Risks section): it resolves via `$XDG_CONFIG_HOME`,
a genuinely different shape from `resolve_state_dir`'s `$XDG_STATE_HOME` precedence, so
forcing it onto the same helper would be a false DRY. Nothing here touches it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_orchestrator.service.paths import (
    AO_SERVICE_STATE_DIR_ENV,
    default_state_dir,
)
from agent_orchestrator.xdg import resolve_state_dir


@pytest.fixture(autouse=True)
def _no_service_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(AO_SERVICE_STATE_DIR_ENV, raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


class TestDefaultStateDirEquivalence:
    def test_override_env_branch(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(AO_SERVICE_STATE_DIR_ENV, str(tmp_path / "custom"))
        assert default_state_dir() == resolve_state_dir(
            AO_SERVICE_STATE_DIR_ENV, "ao/service", "ao/service"
        )

    def test_xdg_state_home_branch(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
        assert default_state_dir() == resolve_state_dir(
            AO_SERVICE_STATE_DIR_ENV, "ao/service", "ao/service"
        )

    def test_home_fallback_branch(self, tmp_path: Path) -> None:
        assert default_state_dir() == resolve_state_dir(
            AO_SERVICE_STATE_DIR_ENV, "ao/service", "ao/service"
        )

    def test_home_fallback_matches_documented_literal_path(self, tmp_path: Path) -> None:
        # Belt-and-suspenders against the *documented* shape too, not just self-consistency
        # against `resolve_state_dir` (which could itself drift and still "agree" above).
        assert default_state_dir() == tmp_path / "home" / ".local" / "state" / "ao" / "service"
