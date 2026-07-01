"""Pytest configuration for the playground test suite.

Gate: any test marked @pytest.mark.real_llm is skipped unless the env var
AO_E2E_REAL_LLM=1 is set, preventing token burn in default CI (NFR-3, ADR-003).
"""

from __future__ import annotations

import os

import pytest


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
