"""Overseer budget / stage reader for the Run page "Limits & settings" section (T-xNFBww).

The overseer template keeps its own budget outside the engine: ``overseer-config.json`` holds
``run_budget_usd`` / ``max_injected_tasks`` and each checkpoint's ``digest.json`` reports spend and
the latched ``stage``. That layout is specific to the ``overseer-runner`` template, so it lives
behind a tiny adapter registry **keyed by template name** -- a different template with its own
budget model adds an entry here and the rest of the dashboard is untouched.

Everything read here is untrusted file content: all access is bounded, tolerant (a missing,
oversized or malformed file yields fewer rows, never an error) and confined to
``<workspace>/workflows/<template>/runs/<workflow_id>/``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from .limits import RunLimit

# `workflow_id` comes from state.json, so it is validated as a single safe path segment.
_SAFE_SEGMENT: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_MAX_JSON_BYTES: Final = 256 * 1024
_CHECKPOINT_DIR: Final = re.compile(r"^ck-(\d+)$")
CONFIG_FILE: Final = "overseer-config.json"
DIGEST_FILE: Final = "digest.json"
CHECKPOINTS_REL: Final = Path("outputs") / "checkpoints"
OVERSEER_SOURCE: Final = "overseer"
OVERSEER_TEMPLATE: Final = "overseer-runner"


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file() or path.stat().st_size > _MAX_JSON_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _latest_digest(run_dir: Path) -> dict[str, Any] | None:
    root = run_dir / CHECKPOINTS_REL
    try:
        numbered = sorted(
            (int(m.group(1)), p)
            for p in root.iterdir()
            if p.is_dir() and (m := _CHECKPOINT_DIR.match(p.name))
        )
    except OSError:
        return None
    for _, ck in reversed(numbered):  # newest checkpoint that actually has a readable digest
        digest = _read_json_object(ck / DIGEST_FILE)
        if digest is not None:
            return digest
    return None


def overseer_limits(workspace: Path, workflow_id: str, *, injected_count: int) -> list[RunLimit]:
    """Overseer rows for the run named *workflow_id* (``[]`` when it is not an overseer run)."""
    if not _SAFE_SEGMENT.match(workflow_id):
        return []
    run_dir = workspace / "workflows" / OVERSEER_TEMPLATE / "runs" / workflow_id
    config = _read_json_object(run_dir / CONFIG_FILE)
    if config is None:
        return []
    digest = _latest_digest(run_dir)
    budget = digest.get("budget") if digest else None
    budget = budget if isinstance(budget, dict) else {}

    cap = _number(config.get("run_budget_usd"))
    rows: list[RunLimit] = []
    if cap is not None:
        stage = budget.get("stage")
        rows.append(
            RunLimit(
                key="overseer.run_budget_usd",
                label="Overseer run budget",
                source=OVERSEER_SOURCE,
                used=_number(budget.get("spent_usd")),
                cap=cap,
                unit="usd",
                note=f"stage: {stage}" if isinstance(stage, str) else None,
            )
        )
    stage = budget.get("stage")
    if isinstance(stage, str) and stage:
        rows.append(
            RunLimit(
                key="overseer.stage",
                label="Overseer budget stage",
                value=stage,
                source=OVERSEER_SOURCE,
            )
        )
    inj_cap = _number(config.get("max_injected_tasks"))
    if inj_cap is not None:
        rows.append(
            RunLimit(
                key="overseer.max_injected_tasks",
                label="Overseer max injected tasks",
                source=OVERSEER_SOURCE,
                used=injected_count,
                cap=inj_cap,
                unit="tasks",
            )
        )
    return rows


# template name -> reader. Add an entry to teach the dashboard another template's budget model.
TEMPLATE_ADAPTERS: Final[dict[str, Callable[..., list[RunLimit]]]] = {
    OVERSEER_TEMPLATE: overseer_limits,
}


def template_limits(workspace: Path, workflow_id: str, *, injected_count: int) -> list[RunLimit]:
    """Rows from every registered template adapter that recognises this run."""
    rows: list[RunLimit] = []
    for adapter in TEMPLATE_ADAPTERS.values():
        rows.extend(adapter(workspace, workflow_id, injected_count=injected_count))
    return rows
