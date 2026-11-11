"""T-lTW88y final security gate: regression tests for fixes landed by the gate itself.

F-1/F-2 (w21-02 audit fixes, tests added by the w22-03 re-review): ``capped_resume.max_add`` is
bounded by ``MAX_USD`` with no cost cap at all; drop-request free text is bounded.

G-1: the fixed-prompt read must not follow a symlink swapped in between the root check and the
open (TOCTOU). ``read_prompt`` resolves and root-checks the path first, so a legitimate in-root
symlink is still accepted (its target is what gets opened); only the raw open refuses symlinks.
"""

from __future__ import annotations

import errno
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_orchestrator.schedules.governance import MAX_USD
from agent_orchestrator.service import capped_resume as cr
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


# -- w21-02 audit fixes F-1 / F-2 had no regression test (re-review w22-03) ------------------


def test_f1_max_add_without_any_cost_cap_is_bounded_by_max_usd() -> None:
    no_limits = SimpleNamespace(limits=None)
    assert cr.max_add(no_limits, float("inf")) == MAX_USD  # type: ignore[arg-type]
    assert cr.max_add(no_limits, 3.0) == 3.0  # type: ignore[arg-type]
    assert cr.max_add(no_limits, -1.0) == 0.0  # type: ignore[arg-type]


def _request(**over: object) -> dict:
    body = cr.ResumeCappedRequest(
        "/ws", "s", "r1", 1.0, 3.0, "cli", datetime(2026, 1, 1, tzinfo=UTC), "req_1-A"
    ).to_json()
    body.update(over)
    return body


def test_f2_well_formed_request_parses() -> None:
    req = cr.ResumeCappedRequest.from_json(_request())
    assert req is not None and req.request_id == "req_1-A"


@pytest.mark.parametrize(
    "over",
    [
        {"request_id": "../../etc"},
        {"request_id": "a" * 65},
        {"request_id": ""},
        {"request_id": "x\ny"},
        {"requested_by": "b" * (cr.MAX_REQUEST_TEXT + 1)},
    ],
)
def test_f2_unbounded_or_unsafe_request_text_is_rejected(over: dict) -> None:
    assert cr.ResumeCappedRequest.from_json(_request(**over)) is None
