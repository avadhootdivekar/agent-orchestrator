"""Security review regression tests for `overseer_tool.py` (T-3FlD46).

Ticket: `meta/tickets/E-YAAGhk-overseer-runner-template/T-3FlD46-security-review-hardening/`.
This file adds ONLY the test cases the review found genuinely missing from the existing
`tests/test_overseer_tool_*.py` suite:

- a REAL (not monkeypatched-constant) oversized breadcrumb, at the byte size the ticket names
  (1.5 MiB), asserting the bounded-read rule id (BC-1) -- the existing
  `test_read_breadcrumb_oversize_is_bc1` in `test_overseer_tool_ledger.py` only shrinks
  `JSON_MAX_BYTES` to 1 byte and never exercises the real 1 MiB threshold end to end.
- `read_ledger_lines`'s new file-size cap (`LEDGER_MAX_BYTES`), added by this same review as a
  fix: nothing previously bounded the TOTAL size of `outputs/ledger.jsonl` before reading it
  line-by-line, even though every other agent-authored JSON read in this tool goes through
  `read_json_bounded`'s stat-before-read discipline (NFR-6). `outputs/ledger.jsonl` lives in the
  same fully agent-writable workspace as everything else (NFR-X11: no inter-task trust
  boundary), so a rogue/buggy unit could append an oversized ledger directly and force this
  tool to read an unbounded amount of data into memory on every subsequent `ckpt-prep`/
  `hold_gate`/`effective_budget` call, all of which start from `read_ledger_lines`.

Every other case in the ticket's "Additional test cases to add if missing" list (symlink
escape, `repo_id` spoof, `..`/absolute paths, ledger chain rewrite (INT-3), deleting
`hold-request.json` after a hold decision (INT-2), a forged request with no hold decision
(INT-4), a forged override without a matching extension (refused), and an `instruction`
redirection attempt (OV-R6)) already has real coverage elsewhere -- see STATUS.md's findings
table for the exact existing test names. This file does not duplicate them.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TOOL_PATH = (
    _REPO_ROOT
    / "src"
    / "agent_orchestrator"
    / "templates"
    / "builtin"
    / "overseer-runner"
    / "tools"
    / "overseer_tool.py"
)


def _load_overseer_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("overseer_tool_under_test_security", _TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ov = _load_overseer_tool()

_NOW = "2026-01-01T00:00:00+00:00"


def write_breadcrumb(inst: Path, unit_id: str, **fields: object) -> None:
    path = ov.breadcrumb_path(inst, unit_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema": "ao.overseer.breadcrumb/v1",
        "unit_id": unit_id,
        "outcome": "done",
        "verdict": "pass",
        "summary": "did the thing",
        "changed_paths": [],
        "needs_input": False,
    }
    data.update(fields)
    path.write_text(json.dumps(data))


# =============================================================================================
# ===== Real (non-monkeypatched) oversize breadcrumb: BC-1, bounded read (NFR-6) ==============
# =============================================================================================


def test_breadcrumb_over_1mib_real_bytes_is_rejected_bc1(tmp_path: Path) -> None:
    """Ticket AC2: "a 1.5 MiB breadcrumb (rejected, bounded read)" -- using the REAL
    `JSON_MAX_BYTES` threshold (1 MiB) and a genuinely oversized file on disk, not a
    monkeypatched constant. Padding is added to an otherwise-valid breadcrumb so the file is
    unambiguously a breadcrumb-shaped payload that is simply too big, not garbage.
    """
    inst = tmp_path / "instance"
    write_breadcrumb(inst, "w01-01-a", padding="x" * int(1.5 * 1024 * 1024))
    path = ov.breadcrumb_path(inst, "w01-01-a")
    assert path.stat().st_size > ov.JSON_MAX_BYTES

    with pytest.raises(ov.Violation) as excinfo:
        ov.read_breadcrumb(inst, "w01-01-a")
    assert excinfo.value.rule_id == "BC-1"


def test_breadcrumb_just_under_cap_is_accepted(tmp_path: Path) -> None:
    """Sanity/boundary check paired with the oversize test above: a breadcrumb comfortably
    under the real 1 MiB cap must still read normally (the fix/verification must not have
    tightened the cap itself).
    """
    inst = tmp_path / "instance"
    write_breadcrumb(inst, "w01-01-a", summary="s" * 1000)
    path = ov.breadcrumb_path(inst, "w01-01-a")
    assert path.stat().st_size < ov.JSON_MAX_BYTES

    data = ov.read_breadcrumb(inst, "w01-01-a")
    assert data is not None
    assert data["unit_id"] == "w01-01-a"


# =============================================================================================
# ===== `read_ledger_lines` file-size cap (LEDGER_MAX_BYTES, INT-3) ===========================
# =============================================================================================


def test_read_ledger_lines_oversize_file_is_int3(tmp_path: Path) -> None:
    """An `outputs/ledger.jsonl` larger than `LEDGER_MAX_BYTES` must fail closed (INT-3) via a
    stat-before-read size check, the same discipline `read_json_bounded` applies to every other
    agent-touchable JSON file this tool reads (NFR-6). Before this review's fix, this file had
    no size cap at all: a single line of this size (no trailing newline) would previously have
    been read entirely into memory as one Python string before `json.loads` ever ran.
    """
    inst = tmp_path / "instance"
    path = ov.ledger_path(inst)
    path.parent.mkdir(parents=True, exist_ok=True)
    # One line, no newline: exercises the worst case (memory would be consumed materializing
    # this single "line" before any per-line JSON parsing/rejection could occur).
    path.write_text("x" * (ov.LEDGER_MAX_BYTES + 1))

    with pytest.raises(ov.Violation) as excinfo:
        ov.read_ledger_lines(inst)
    assert excinfo.value.rule_id == "INT-3"


def test_read_ledger_lines_many_small_lines_over_cap_is_int3(tmp_path: Path) -> None:
    """The same cap also closes the "many small lines" variant of the DoS (as opposed to one
    huge line): a ledger built from thousands of small, individually-valid-looking JSON lines
    that together exceed `LEDGER_MAX_BYTES` must still fail closed before any line is parsed.
    """
    inst = tmp_path / "instance"
    path = ov.ledger_path(inst)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"type": "event", "event": "checkpoint", "decision": "continue"}) + "\n"
    repeat_count = (ov.LEDGER_MAX_BYTES // len(line)) + 10
    with path.open("w", encoding="utf-8") as fh:
        for _ in range(repeat_count):
            fh.write(line)
    assert path.stat().st_size > ov.LEDGER_MAX_BYTES

    with pytest.raises(ov.Violation) as excinfo:
        ov.read_ledger_lines(inst)
    assert excinfo.value.rule_id == "INT-3"


def test_read_ledger_lines_under_cap_still_parses_normally(tmp_path: Path) -> None:
    """Regression guard for the fix above: an ordinary, well-under-cap ledger must be
    completely unaffected by the new size check.
    """
    inst = tmp_path / "instance"
    now = ov.parse_now(_NOW)
    for i in range(5):
        ov.append_chained(
            inst,
            {"type": "event", "event": "checkpoint", "decision": "continue", "seq_hint": i},
            now,
        )

    lines = ov.read_ledger_lines(inst)
    assert len(lines) == 5
    ov.verify_ledger_chain(inst)  # must not raise
