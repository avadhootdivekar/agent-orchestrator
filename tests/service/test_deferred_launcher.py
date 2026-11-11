"""B-1 regression: the daemon's ``_DeferredLauncher`` shim must forward ``launch_capped_resume``."""

from __future__ import annotations

from typing import Any

import pytest

from agent_orchestrator.service.cli import _DeferredLauncher


class _Target:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, float]] = []

    def launch_capped_resume(self, rec: Any, new_cap_usd: float) -> str:
        self.calls.append((rec, new_cap_usd))
        return "launched"


def test_forwards_launch_capped_resume_to_target() -> None:
    shim = _DeferredLauncher()
    shim.target = _Target()
    assert hasattr(shim, "launch_capped_resume")
    assert shim.launch_capped_resume("rec", 2.5) == "launched"
    assert shim.target.calls == [("rec", 2.5)]


def test_launch_capped_resume_before_wiring_raises() -> None:
    with pytest.raises(RuntimeError, match="before the engine finished wiring"):
        _DeferredLauncher().launch_capped_resume("rec", 1.0)
