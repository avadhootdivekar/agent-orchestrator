"""Dashboard panel v2 data (E-Sn7tL2 T-Sn9Pow): generation timeline + spend, proposals, directives.

A read-only projection of ``RunState`` + the run directory (``sentinel/``, ``control/``). Kept out
of ``control.build_status`` so the snapshot-tested ``ao.ctl.status/v1`` schema is untouched.
Everything is bounded (newest ``LIST_CAP`` rows) and tolerant of missing/garbled files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from .models import RunState, is_sentinel_generation_task, sentinel_generation_spend_usd
from .sentinel_powers import read_directives

DETAIL_SCHEMA: Final = "ao.sentinel.panel/v2"
LIST_CAP: Final = 100
ACK_FILE: Final = "directive-acks.jsonl"


def _jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _generations(state: RunState, run_dir: Path) -> list[dict[str, Any]]:
    sdir = run_dir / "sentinel"
    ended = {
        r["data"].get("generation"): r["data"]
        for r in _jsonl(sdir / "ledger.jsonl")
        if r.get("event") == "generation_ended" and isinstance(r.get("data"), dict)
    }
    rows: list[dict[str, Any]] = []
    for tid, ts in sorted(state.tasks.items()):
        if not is_sentinel_generation_task(tid):
            continue
        gen = int(tid.rsplit("g", 1)[-1])
        info = ended.get(gen, {})
        trig = [
            t.get("kind")
            for t in _jsonl(sdir / f"g{gen:02d}" / "triggers.jsonl")
            if isinstance(t.get("kind"), str)
        ]
        rows.append(
            {
                "generation": gen,
                "task_id": tid,
                "status": ts.status,
                "started_at": ts.started_at,
                "ended_at": ts.ended_at,
                "cost_usd": round(ts.cumulative_cost_usd, 6),
                "triggers": trig,
                "graceful": info.get("graceful"),
                "ended_reason": info.get("forced") or info.get("ended_reason"),
            }
        )
    return rows[-LIST_CAP:]


def _proposals(run_dir: Path) -> dict[str, list[dict[str, Any]]]:
    ctl = run_dir / "control"
    applied: list[dict[str, Any]] = []
    for path in sorted((ctl / "inbox-processed").glob("*.json")):
        d = _json(path)
        if d and str(d.get("actor", "")).startswith("sentinel-g"):
            applied.append(
                {
                    "id": d.get("id"),
                    "kind": d.get("kind"),
                    "actor": d.get("actor"),
                    "at": d.get("created_at"),
                }
            )
    quarantined: list[dict[str, Any]] = []
    for path in sorted((ctl / "inbox-rejected").glob("*.json")):
        if path.name.endswith(".reason.json"):
            continue
        d = _json(path)
        if not d or not str(d.get("actor", "")).startswith("sentinel-g"):
            continue
        why = _json(path.with_name(f"{path.stem}.reason.json")) or {}
        quarantined.append(
            {
                "id": d.get("id"),
                "kind": d.get("kind"),
                "actor": d.get("actor"),
                "at": d.get("created_at"),
                "rule_id": why.get("rule_id"),
                "detail": why.get("detail"),
            }
        )
    return {"applied": applied[-LIST_CAP:], "quarantined": quarantined[-LIST_CAP:]}


def _directives(run_dir: Path) -> list[dict[str, Any]]:
    ctl = run_dir / "control"
    acks = {a.get("directive_id"): a for a in _jsonl(ctl / ACK_FILE)}
    out = []
    for d in read_directives(ctl)[-LIST_CAP:]:
        ack = acks.get(d["id"])
        out.append(
            {
                "id": d["id"],
                "at": d.get("at"),
                "actor": d.get("actor"),
                "severity": d.get("severity"),
                "text": d.get("text"),
                "rationale": d.get("rationale"),
                "ack": None
                if ack is None
                else {
                    "response": ack.get("response"),
                    "rationale": ack.get("rationale"),
                    "checkpoint": ack.get("checkpoint"),
                    "at": ack.get("at"),
                },
            }
        )
    return out


def build_panel(state: RunState, run_dir: Path) -> dict[str, Any]:
    """The panel-v2 document. A run without sentinel footprint yields empty lists."""
    gens = _generations(state, run_dir)
    return {
        "schema": DETAIL_SCHEMA,
        "run_id": state.run_id,
        "generations": gens,
        "spend_usd": round(sentinel_generation_spend_usd(state), 6),
        "proposals": _proposals(run_dir),
        "directives": _directives(run_dir),
    }
