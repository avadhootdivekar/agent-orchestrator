"""U-K1: the golden vector GV-1 (Rev 2) of HLD 8.2.7, reproduced EXACTLY.

A failure here means the key changed for an existing workflow. If the cause is a legitimate
upstream change (`build_prompt` wording, argv construction), update GV-1 in the HLD with the
reason and decide whether `KEY_SCHEMA_VERSION` must be bumped; never just edit the constants.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.cache.keys_fixture import (
    GV1_COMPONENTS,
    GV1_HEAD,
    GV1_KEY,
    key_for,
    write_files,
)


@pytest.fixture(autouse=True)
def _cache_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AO_CACHE", raising=False)


def test_gv1_key_and_every_component_digest(tmp_path: Path) -> None:
    write_files(tmp_path)
    got = key_for(tmp_path)
    assert got.key == GV1_KEY
    assert dict(got.components) == GV1_COMPONENTS


def test_gv1_summary_and_provenance(tmp_path: Path) -> None:
    write_files(tmp_path)
    got = key_for(tmp_path)
    assert got.output_paths == ("out/summary.md",)
    assert got.cli_version == "2.1.278 (Claude Code)"
    assert got.summary.repo_heads == {"core": GV1_HEAD}
    assert got.summary.digests["docs/notes.md"] == (
        "b6a98d9ce9a2d9149288fa3df42d377c3e42737afdcdaf714e33c0a100b51060"
    )
