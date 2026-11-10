"""T-Wh9Kv1 AC15 / AC15b: ``ao schedule add --webhook`` and a signed delivery fired by the real
``ao schedule daemon --once`` into a real run directory, all through ``CliRunner`` (fixed clock)."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from agent_orchestrator.cli import app
from agent_orchestrator.service.fire_store import FireIntentStore
from agent_orchestrator.service.registry import default_registry_path
from agent_orchestrator.service.supervisor import _slug_for_root
from agent_orchestrator.service.webhook import (
    WebhookRequestQueue,
    build_webhook_app,
    compute_signature,
    make_binding_lookup,
)

from .test_cli_e2e import QUOTA, T0, Env, opt_in
from .test_cli_e2e import env as _env_fixture

runner = CliRunner()
env = _env_fixture  # re-export the workspace fixture under its own name
SECRET_ENV = "AO_TEST_E2E_HOOK_SECRET"
SECRET = "e2e-secret-value-0123456789"
BODY = b'{"ref": "refs/heads/main"}'


def add_hook(env: Env, *extra: str, ok: bool = True) -> Any:
    return env.run("add", "on-push", "--webhook", "--workflow", "w.json", *QUOTA, *extra, ok=ok)


def test_add_webhook_flags_write_a_valid_binding(env: Env) -> None:
    add_hook(
        env,
        "--secret-env",
        SECRET_ENV,
        "--rate-per-minute",
        "5",
        "--webhook-scheme",
        "github",
    )
    row = env.data()["schedules"][0]
    assert row["kind"] == "webhook" and "interval_seconds" not in row and "schedule" not in row
    assert row["webhook"] == {"secret_env": SECRET_ENV, "rate_per_minute": 5, "scheme": "github"}
    assert "OK: 1" in env.run("validate").output
    assert "webhook" in env.run("list").output
    add_hook(env, "--secret-file", ".ao/hook.secret", "--force")
    assert env.data()["schedules"][0]["webhook"] == {"secret_file": ".ao/hook.secret"}


@pytest.mark.parametrize(
    "extra,needle",
    [
        ([], "exactly one of --secret-env"),
        (["--secret-env", SECRET_ENV, "--secret-file", "f"], "exactly one of --secret-env"),
        (["--secret-env", SECRET_ENV, "--cron", "0 * * * *"], "drop --cron"),
        (["--secret-env", SECRET_ENV, "--webhook-scheme", "both"], "scheme"),
        (["--secret-env", "1BAD"], "secret_env"),
        (["--secret-env", SECRET_ENV, "--rate-per-minute", "0"], "rate-per-minute"),
    ],
)
def test_add_webhook_rejects_bad_combinations(env: Env, extra: list[str], needle: str) -> None:
    res = add_hook(env, *extra, ok=False)
    assert needle in res.output
    assert not env.sched_file.exists() or env.data()["schedules"] == []


def test_webhook_options_without_webhook_flag_are_refused(env: Env) -> None:
    res = env.run(
        "add", "x", "--interval", "60", "--workflow", "w.json", *QUOTA,
        "--secret-env", SECRET_ENV, ok=False,
    )  # fmt: skip
    assert "need --webhook" in res.output


def test_signed_delivery_is_fired_by_daemon_once_into_a_run_dir(
    env: Env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC15: add (CLI) -> signed POST (real listener app, real registry lookup) -> ``daemon
    --once`` -> exactly one launch and a run directory; a second tick fires nothing."""
    monkeypatch.setenv(SECRET_ENV, SECRET)
    opt_in(env)
    add_hook(env, "--secret-env", SECRET_ENV)
    queue = WebhookRequestQueue(env.state)
    listener = build_webhook_app(
        make_binding_lookup(default_registry_path()),
        queue,
        clock=lambda: T0,
        emit=lambda event, **fields: None,
    )
    client = TestClient(listener, base_url="http://localhost")
    ts = str(int(T0.timestamp()))
    headers = {
        "Content-Type": "application/json",
        "X-AO-Timestamp": ts,
        "X-AO-Signature": compute_signature(SECRET.encode(), BODY, ts),
        "X-AO-Delivery": "e2e-1",
    }
    slug = _slug_for_root(str(env.ws))
    assert client.post(f"/hooks/{slug}/on-push", content=BODY, headers=headers).status_code == 202
    (pending,) = list((env.state / "schedules" / "requests").glob("*.json"))
    assert json.loads(pending.read_text())["delivery_id"] == "e2e-1"

    first = runner.invoke(app, ["schedule", "daemon", "--once", "--workspace", str(env.ws)])
    assert first.exit_code == 0, first.output
    assert first.stdout.strip() == "1" and "on-push" in first.stderr
    (record,) = FireIntentStore(env.state).list()
    assert record.run_id and (env.ws / "runs" / record.run_id).is_dir()  # a real run directory
    assert not pending.exists()  # consumed

    second = runner.invoke(app, ["schedule", "daemon", "--once", "--workspace", str(env.ws)])
    assert second.exit_code == 0 and second.stdout.strip() == "0"
    assert len(FireIntentStore(env.state).list()) == 1
    # a replay of the same delivery is refused (nonce) and never reaches the engine
    assert client.post(f"/hooks/{slug}/on-push", content=BODY, headers=headers).status_code == 409


def test_wrong_signature_never_reaches_the_daemon(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SECRET_ENV, SECRET)
    opt_in(env)
    add_hook(env, "--secret-env", SECRET_ENV)
    listener = build_webhook_app(
        make_binding_lookup(default_registry_path()),
        WebhookRequestQueue(env.state),
        clock=lambda: T0 + timedelta(seconds=0),
        emit=lambda event, **fields: None,
    )
    client = TestClient(listener, base_url="http://localhost")
    ts = str(int(T0.timestamp()))
    bad = {
        "Content-Type": "application/json",
        "X-AO-Timestamp": ts,
        "X-AO-Signature": compute_signature(b"wrong-secret-wrong-secret", BODY, ts),
    }
    slug = _slug_for_root(str(env.ws))
    assert client.post(f"/hooks/{slug}/on-push", content=BODY, headers=bad).status_code == 401
    res = runner.invoke(app, ["schedule", "daemon", "--once", "--workspace", str(env.ws)])
    assert res.exit_code == 0 and res.stdout.strip() == "0"
    assert FireIntentStore(env.state).list() == []
