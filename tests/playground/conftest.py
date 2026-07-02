"""Pytest configuration for the playground test suite.

Gate: any test marked @pytest.mark.real_llm is skipped unless the env var
AO_E2E_REAL_LLM=1 is set, preventing token burn in default CI (NFR-3, ADR-003).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

# Repo root — tests/playground/conftest.py -> parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]
# Repo-local, gitignored base for real-LLM workspaces. Real agents run in a
# spawned `claude` subprocess whose sandbox allow-list is the repo working
# directory; pytest's tmp_path lives under the system /tmp, which is OUTSIDE
# that allow-list, so instruction reads / output writes there are denied. A
# workspace under the repo stays inside the allow-list without granting the
# subprocess any unrestricted access. See ClaudeCliExecutor.
_REAL_LLM_TMP_BASE = _REPO_ROOT / "playground" / ".tmp"


@pytest.fixture
def real_llm_workspace() -> Iterator[Path]:
    """Per-test workspace under ``playground/.tmp/`` (repo-local, gitignored).

    Use this instead of ``tmp_path`` for any test that spawns the real
    ``claude`` CLI: the subprocess can only touch paths inside its sandbox
    allow-list (the repo working directory), and ``tmp_path`` (system /tmp)
    falls outside it.

    Workspaces are deliberately preserved after the test so that produced
    artifacts are available for post-run audit and debugging. Use ``ao prune``
    to clean up stale workspaces when disk space becomes a concern.
    """
    _REAL_LLM_TMP_BASE.mkdir(parents=True, exist_ok=True)
    workspace = _REAL_LLM_TMP_BASE / f"ws-{uuid.uuid4().hex[:12]}"
    workspace.mkdir()
    yield workspace


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip real_llm-marked items unless AO_E2E_REAL_LLM=1 is set in the environment."""
    if os.environ.get("AO_E2E_REAL_LLM") == "1":
        return  # gate open — let all tests run on their own merits

    skip_marker = pytest.mark.skip(
        reason=(
            "real_llm tier disabled; set AO_E2E_REAL_LLM=1 to enable "
            "(e.g. AO_E2E_REAL_LLM=1 uv run pytest -m real_llm)"
        )
    )
    for item in items:
        if "real_llm" in item.keywords:
            item.add_marker(skip_marker)
