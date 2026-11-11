"""T-Cl6Jn9 AC3/AC15a follow-up: ``ao schedule next`` calls ``TriggerSource.observe`` for
event-kind schedules (read-only), via ``CliRunner`` with the fixed clock ``T0``."""

from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from agent_orchestrator.service.supervisor import _slug_for_root
from agent_orchestrator.service.webhook import WebhookDelivery, WebhookRequestQueue

from .test_cli_e2e import QUOTA, T0, Env
from .test_cli_e2e import env as _env_fixture

runner = CliRunner()
env = _env_fixture


def _add_watch(env: Env) -> Path:
    inbox = env.ws / "inbox"
    inbox.mkdir()
    env.run(
        "add", "hook", "--webhook", "--workflow", "w.json", *QUOTA, "--secret-env", "AO_X_SECRET"
    )
    data = env.data()
    data["schedules"].append(
        {
            "id": "watcher",
            "kind": "file_watch",
            "workflow": "w.json",
            "watch": {"paths": ["inbox/*.md"], "debounce_seconds": 0, "poll_seconds": 1},
            "limits": data["schedules"][0]["limits"],
        }
    )
    env.sched_file.write_text(json.dumps(data))
    return inbox


def _next_json(env: Env) -> list[dict[str, Any]]:
    return json.loads(env.run("next", "-n", "10", "--json").stdout)


def test_next_no_pending_event_lists_unpredicted_note(env: Env) -> None:
    _add_watch(env)
    res = env.run("next")
    assert "no pending event" in res.output and "hook (webhook)" in res.output
    assert _next_json(env) == []


def test_next_shows_pending_webhook_without_consuming(env: Env) -> None:
    _add_watch(env)
    queue = WebhookRequestQueue(env.state)
    path = queue.enqueue(
        WebhookDelivery(
            workspace_slug=_slug_for_root(str(env.ws)),
            schedule_id="hook",
            received_at=T0 - timedelta(seconds=5),
            delivery_id="d-1",
        )
    )
    out = _next_json(env)
    assert [(r["id"], r["kind"], r["pending_event"]) for r in out] == [("hook", "webhook", True)]
    assert out[0]["at"].startswith("2026-03-04T05:06:02")
    assert path.is_file()  # observe-only: the request is still queued
    assert "pending event" in env.run("next").output


def test_next_shows_pending_file_watch_and_writes_nothing(env: Env) -> None:
    inbox = _add_watch(env)
    f = inbox / "a.md"
    f.write_text("x")
    stamp = (T0 - timedelta(minutes=5)).timestamp()
    os.utime(f, (stamp, stamp))
    before = sorted(p for p in env.state.rglob("*") if p.is_file()) if env.state.exists() else []
    out = _next_json(env)
    assert [(r["id"], r["pending_event"]) for r in out] == [("watcher", True)]
    assert out[0]["at"].startswith("2026-03-04T05:01:07")
    after = sorted(p for p in env.state.rglob("*") if p.is_file()) if env.state.exists() else []
    assert after == before  # no digest baseline persisted by a preview
