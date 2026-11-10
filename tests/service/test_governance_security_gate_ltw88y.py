"""T-lTW88y final security gate: regression tests for fixes landed by the gate itself.

G-1: the fixed-prompt read must not follow a symlink swapped in between the root check and the
open (TOCTOU). ``read_prompt`` resolves and root-checks the path first, so a legitimate in-root
symlink is still accepted (its target is what gets opened); only the raw open refuses symlinks.
"""

from __future__ import annotations

import errno
import hashlib
from pathlib import Path

import pytest

from agent_orchestrator.service import governance_gate as gg

from .test_schedule_engine import binding


def test_g1_raw_prompt_open_refuses_a_symlink_final_component(tmp_path: Path) -> None:
    outside = tmp_path / "secret.md"
    outside.write_text("do not leak")
    link = tmp_path / "ws" / "p.md"
    link.parent.mkdir()
    link.symlink_to(outside)
    with pytest.raises(OSError) as info:
        gg._read_regular_file(link, "p.md")
    assert info.value.errno == errno.ELOOP


def test_g1_symlink_swapped_in_after_the_root_check_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    prompt = ws / "p.md"
    prompt.write_text("benign")
    secret = tmp_path / "secret.md"
    secret.write_text("do not leak")
    real_read = gg._read_regular_file

    def swap_then_read(path: Path, rel: str) -> bytes | gg.GateResult:
        # The race: after resolve()/is_relative_to() passed, the file becomes a symlink out.
        path.unlink()
        path.symlink_to(secret)
        return real_read(path, rel)

    monkeypatch.setattr(gg, "_read_regular_file", swap_then_read)
    g = gg.read_prompt(binding("g", prompt_file="p.md"), str(ws))
    assert isinstance(g, gg.GateResult) and not g.admit
    assert g.reason == gg.REASON_PROMPT_MISSING
    assert str(g.detail["detail"]).startswith("unreadable")
    assert "do not leak" not in repr(g)


def test_g1_in_root_symlink_still_reads_its_target(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / "prompts").mkdir(parents=True)
    data = b"nightly triage"
    (ws / "prompts" / "real.md").write_bytes(data)
    (ws / "p.md").symlink_to(ws / "prompts" / "real.md")
    snap = gg.read_prompt(binding("g", prompt_file="p.md"), str(ws))
    assert isinstance(snap, gg.PromptSnapshot)
    assert snap.text == "nightly triage"
    assert snap.sha256 == hashlib.sha256(data).hexdigest()
