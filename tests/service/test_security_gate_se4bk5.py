"""T-Se4Bk5 security-gate regressions on the untrusted ``.ao/schedules.yaml`` path surface.

Each test loads a binding through the real :class:`ScheduleFile` validator (the same path a
hostile workspace file takes) and drives it into the consumer that touches the filesystem.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.schedules.errors import ScheduleFileError
from agent_orchestrator.schedules.models import ResolvedBinding, ScheduleFile
from agent_orchestrator.service import governance_gate as gg

_LIMITS: dict[str, Any] = {"cost": {"per_run_usd": 1.0}}


def _binding(**fields: Any) -> ResolvedBinding:
    raw: dict[str, Any] = {
        "id": "s1",
        "kind": "cron",
        "schedule": "0 * * * *",
        "workflow": "w.json",
        "limits": _LIMITS,
    }
    raw.update(fields)
    return ScheduleFile.from_data({"version": "1.0", "schedules": [raw]}, source="t").bindings[0]


# Escaping shapes are refused at load (named ScheduleFileError) for every path-bearing field.
_ESCAPES = ["..", "../x", "a/../../x", "/etc/passwd", "~/x", "~root/x", "C:/x", "a\\..\\..\\x"]


@pytest.mark.parametrize("value", _ESCAPES)
@pytest.mark.parametrize("field", ["workflow", "prompt_file"])
def test_escaping_path_shapes_are_refused_at_load(field: str, value: str) -> None:
    with pytest.raises(ScheduleFileError, match="workspace-relative"):
        _binding(**{field: value})


@pytest.mark.parametrize("value", [*_ESCAPES, "./x", "a/b", "%2e%2e", "a\x00b"])
def test_template_must_be_a_bare_name(value: str) -> None:
    with pytest.raises(ScheduleFileError):
        _binding(workflow=None, template=value)


def test_nul_byte_prompt_file_is_a_named_refusal_not_a_crash(tmp_path: Path) -> None:
    # A YAML ``"\0"`` escape passes the load-time shape check; the gate must refuse it by name
    # (prompt_missing / invalid_path) instead of letting ``ValueError`` escape (T-Se4Bk5 S-3).
    b = _binding(prompt_file="p\x00.md")
    g = gg.read_prompt(b, str(tmp_path))
    assert isinstance(g, gg.GateResult) and not g.admit
    assert (g.reason, g.detail["detail"]) == (gg.REASON_PROMPT_MISSING, gg.DETAIL_INVALID_PATH)


def test_url_encoded_dotdot_is_a_literal_name_inside_the_root(tmp_path: Path) -> None:
    # No layer URL-decodes: ``%2e%2e/p.md`` is a literal directory name under the root.
    (tmp_path / "%2e%2e").mkdir()
    (tmp_path / "%2e%2e" / "p.md").write_text("hi", encoding="utf-8")
    snap = gg.read_prompt(_binding(prompt_file="%2e%2e/p.md"), str(tmp_path))
    assert isinstance(snap, gg.PromptSnapshot) and snap.text == "hi"


def test_overlong_prompt_file_is_a_named_refusal(tmp_path: Path) -> None:
    g = gg.read_prompt(_binding(prompt_file="x" * 5000), str(tmp_path))
    assert isinstance(g, gg.GateResult) and g.reason == gg.REASON_PROMPT_MISSING
