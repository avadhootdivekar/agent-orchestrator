"""T-eyn5UG AC-11 (U-LZ1), NFR-1: the read sides import `cache.report` lazily.

Each case runs in a FRESH interpreter (pytest's own session has long since imported everything),
calls `write_status`, `aggregate_usage` and `_settle_reason`, and reports whether
`agent_orchestrator.cache.report` ended up in `sys.modules`. The two halves are a pair: the
positive control proves the check can fail.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import agent_orchestrator

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = Path(agent_orchestrator.__file__).resolve().parents[1]
REPORT_MODULE = "agent_orchestrator.cache.report"
TIMEOUT_SECONDS = 120

_SCRIPT = textwrap.dedent(
    """
    import json, sys, tempfile
    from tests.cache._report_states import hit_rec, run_state, settled
    from agent_orchestrator.artifacts import LocalFsArtifactStore
    from agent_orchestrator.outcomes import _settle_reason
    from agent_orchestrator.runstate import RunStateStore
    from agent_orchestrator.usage import aggregate_usage

    with_records = sys.argv[1] == "records"
    state = run_state({"a": settled(), "b": settled()}, {"a": hit_rec()} if with_records else {})
    with tempfile.TemporaryDirectory() as ws:
        RunStateStore(ws, LocalFsArtifactStore(ws)).write_status(state)
        aggregate_usage([state], LocalFsArtifactStore(ws))
        reasons = [_settle_reason(state.tasks[t], state=state, tid=t) for t in ("a", "b")]
    print("REPORT=" + json.dumps({
        "loaded": sorted(m for m in sys.modules if m.startswith("agent_orchestrator.cache")),
        "reasons": reasons,
    }))
    """
)


def _run(mode: str) -> dict[str, object]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AO_")}
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SRC_DIR), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
    )
    proc = subprocess.run(
        [sys.executable, "-c", _SCRIPT, mode],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("REPORT="))
    report: dict[str, object] = json.loads(line[len("REPORT=") :])
    return report


@pytest.fixture(scope="module")
def empty_run() -> dict[str, object]:
    return _run("empty")


@pytest.fixture(scope="module")
def run_with_records() -> dict[str, object]:
    return _run("records")


class TestLazyReadSides:
    def test_empty_map_never_loads_cache_report(self, empty_run: dict[str, object]) -> None:
        assert REPORT_MODULE not in empty_run["loaded"]  # type: ignore[operator]
        assert empty_run["reasons"] == ["dispatched", "dispatched"]

    def test_empty_map_loads_no_cache_module_at_all_from_these_paths(
        self, empty_run: dict[str, object]
    ) -> None:
        # models.py imports nothing from cache/; the read sides must not add a cache import.
        assert empty_run["loaded"] == []

    def test_non_empty_map_loads_it(self, run_with_records: dict[str, object]) -> None:
        assert REPORT_MODULE in run_with_records["loaded"]  # type: ignore[operator]
        assert run_with_records["reasons"] == ["cached", "dispatched"]
