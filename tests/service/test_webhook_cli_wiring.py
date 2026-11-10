"""T-Wh9Kv1 review fixes: ``ao service run --webhook`` applies the M2 bounds and the L6 warning."""

from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

from agent_orchestrator.cli import app
from agent_orchestrator.service import webhook as wh

from .test_service_schedule_integration import run_env  # noqa: F401  (fixture)

runner = CliRunner()


def test_webhook_listener_config_carries_connection_bounds(run_env: SimpleNamespace) -> None:  # noqa: F811
    result = runner.invoke(app, ["service", "run", "--webhook"])
    assert result.exit_code == 0, result.output
    _app, kwargs = run_env.uvicorn.configs[1]
    assert kwargs["limit_concurrency"] == wh.WEBHOOK_LIMIT_CONCURRENCY
    assert kwargs["timeout_keep_alive"] == wh.WEBHOOK_TIMEOUT_KEEP_ALIVE_SECONDS
    assert "limit_concurrency" not in run_env.uvicorn.configs[0][1]  # the hub is untouched


def test_non_loopback_warning_text_is_the_documented_one(run_env: SimpleNamespace) -> None:  # noqa: F811
    result = runner.invoke(app, ["service", "run", "--webhook", "--webhook-host", "0.0.0.0"])
    assert result.exit_code == 0, result.output
    assert "independently of dashboard auth" in result.output
    assert wh.WEBHOOK_ALLOWED_HOSTS_ENV in result.output and "reverse proxy" in result.output
