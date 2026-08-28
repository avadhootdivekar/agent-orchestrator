"""E2E tests for the dashboard (E-Ui7Kq2).

The outermost boundary a user actually touches:

* ``ao ui`` invoked through ``CliRunner`` — flag surface, guard rails, error paths;
* a **real uvicorn server** in a subprocess, driven over **real HTTP**, that launches a
  **real ``ao run``** child and observes the run appear, complete, and be deletable.

That second class is what proves the whole stack composes — CLI -> server -> service ->
supervisor -> engine -> run artifacts -> API -> client — which no lower-level test can.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

pytest.importorskip("fastapi", reason="dashboard needs the optional [ui] extra")
pytest.importorskip("uvicorn", reason="dashboard needs the optional [ui] extra")
httpx = pytest.importorskip("httpx", reason="dashboard e2e needs httpx")

from agent_orchestrator.cli import app  # noqa: E402

runner = CliRunner()

SERVER_BOOT_TIMEOUT = 30.0
RUN_COMPLETION_TIMEOUT = 60.0
POLL_INTERVAL = 0.1


def _free_port() -> int:
    """Reserve an ephemeral port, then release it for the server to bind."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for(predicate, timeout: float, interval: float = POLL_INTERVAL) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass  # server not up yet / transient read of a half-written file
        time.sleep(interval)
    return False


def _write_workspace(root: Path) -> None:
    """Populate *root* with a runnable fake-executor workflow plus browsable files."""
    (root / "instructions").mkdir(parents=True, exist_ok=True)
    (root / "instructions" / "build.md").write_text("build it", encoding="utf-8")
    (root / ".hidden-file").write_text("dotfile", encoding="utf-8")
    (root / "binary.dat").write_bytes(b"\x00\x01\x02\x03")

    (root / "workflow.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "id": "e2e-demo",
                "name": "E2E demo workflow",
                "repo_set": "main",
                "prompt_path": "prompts/run.md",
                "tasks": [
                    {
                        "id": "build",
                        "agent": "dev",
                        "instruction": "instructions/build.md",
                        "inputs": ["prompts/run.md"],
                        "outputs": ["out/build.md"],
                        "skip_if_outputs_exist": False,
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (root / "reposets.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "repo_sets": {
                    "main": {
                        "repos": [{"id": "repo", "path": str(root), "role": "primary"}],
                        "workspace_root": str(root),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "agents.json").write_text(
        json.dumps({"version": "1.0", "agents": {"dev": {"executor": "fake"}}}),
        encoding="utf-8",
    )
    (root / ".ao").mkdir(exist_ok=True)
    (root / ".ao" / "config.yaml").write_text(
        f"workflow: {root / 'workflow.json'}\n"
        f"reposets: {root / 'reposets.json'}\n"
        f"agents: {root / 'agents.json'}\n"
        "general_instructions:\n  - house-rules.md\n",
        encoding="utf-8",
    )
    (root / ".ao" / "house-rules.md").write_text("Always write tests.", encoding="utf-8")


class TestUiCommandSurface:
    """`ao ui` flag surface and guard rails, via CliRunner."""

    def test_help_lists_the_command(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "ui" in result.output

    def test_ui_help_documents_the_flags(self) -> None:
        result = runner.invoke(app, ["ui", "--help"])
        assert result.exit_code == 0
        for flag in ("--host", "--port", "--workspace", "--open", "--reload"):
            assert flag in result.output

    def test_help_warns_that_the_default_bind_is_loopback(self) -> None:
        # The dashboard is unauthenticated and can spend money; the default must be
        # loopback and the help must say why.
        result = runner.invoke(app, ["ui", "--help"])
        normalized = " ".join(result.output.split())
        assert "loopback" in normalized

    def test_nonexistent_workspace_is_rejected(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["ui", "--workspace", str(tmp_path / "nope")])
        assert result.exit_code == 1
        assert "not a directory" in result.output


@pytest.fixture()
def live_server(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    """A real `ao ui` server on a real port, serving a real workspace."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_workspace(workspace)

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "agent_orchestrator.cli",
            "ui",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workspace",
            str(workspace),
        ],
        cwd=str(workspace),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )

    def _healthy() -> bool:
        return httpx.get(f"{base_url}/api/health", timeout=2.0).status_code == 200

    if not _wait_for(_healthy, SERVER_BOOT_TIMEOUT):
        proc.kill()
        output = proc.stdout.read().decode("utf-8", errors="replace") if proc.stdout else ""
        pytest.fail(f"dashboard server did not start within {SERVER_BOOT_TIMEOUT}s:\n{output}")

    try:
        yield base_url, workspace
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), 15)
            proc.wait(timeout=10)
        except (ProcessLookupError, subprocess.TimeoutExpired, OSError):
            proc.kill()
        if proc.stdout:
            proc.stdout.close()


class TestLiveServer:
    """Full stack over real HTTP against a real server process."""

    def test_health_endpoint_responds(self, live_server) -> None:
        base_url, _ = live_server
        body = httpx.get(f"{base_url}/api/health", timeout=5.0).json()
        assert body["status"] == "ok"

    def test_workspace_endpoint_reports_the_served_root(self, live_server) -> None:
        base_url, workspace = live_server
        body = httpx.get(f"{base_url}/api/workspace", timeout=5.0).json()
        assert body["workspace_root"] == str(workspace.resolve())

    def test_browses_files_including_hidden_and_binary(self, live_server) -> None:
        base_url, _ = live_server
        body = httpx.get(f"{base_url}/api/files", timeout=5.0).json()
        names = {e["name"] for e in body["entries"]}
        assert ".hidden-file" in names
        assert "binary.dat" in names

        content = httpx.get(
            f"{base_url}/api/files/content", params={"path": "binary.dat"}, timeout=5.0
        ).json()
        assert content["is_binary"] is True and content["text"] is None

    def test_traversal_is_refused_over_http(self, live_server) -> None:
        base_url, _ = live_server
        response = httpx.get(f"{base_url}/api/files", params={"path": "../../../etc"}, timeout=5.0)
        assert response.status_code == 403

    def test_discovers_the_workflow(self, live_server) -> None:
        base_url, _ = live_server
        body = httpx.get(f"{base_url}/api/workflows", timeout=5.0).json()
        assert any(w["id"] == "e2e-demo" and w["prompt_path"] == "prompts/run.md" for w in body)

    def test_reports_configured_general_instructions(self, live_server) -> None:
        base_url, _ = live_server
        body = httpx.get(f"{base_url}/api/general-instructions", timeout=5.0).json()
        assert len(body) == 1
        assert body[0]["exists"] is True

    def test_serves_the_dashboard_page(self, live_server) -> None:
        base_url, _ = live_server
        response = httpx.get(base_url, timeout=5.0)
        # 503 means the frontend was not built in this checkout — still a defined contract.
        assert response.status_code in (200, 503)

    def test_full_run_lifecycle_from_prompt_to_deletion(self, live_server) -> None:
        """POST a prompt -> engine runs -> stats update -> run is deletable."""
        base_url, workspace = live_server
        workflow_path = str(workspace / "workflow.json")

        launch = httpx.post(
            f"{base_url}/api/runs",
            json={
                "workflow_path": workflow_path,
                "prompt": "Build the thing, carefully.",
                "options": {},
            },
            timeout=30.0,
        )
        assert launch.status_code == 201, launch.text

        # The prompt the user typed reached the workflow's declared prompt_path.
        prompt_file = workspace / "prompts" / "run.md"
        assert _wait_for(prompt_file.is_file, 20.0), "prompt file was never written"
        assert prompt_file.read_text(encoding="utf-8") == "Build the thing, carefully."

        def _run_finished() -> bool:
            rows = httpx.get(f"{base_url}/api/runs", timeout=5.0).json()
            return bool(rows) and rows[0]["status"] in ("succeeded", "failed")

        assert _wait_for(_run_finished, RUN_COMPLETION_TIMEOUT), (
            "run never reached a terminal state"
        )

        rows = httpx.get(f"{base_url}/api/runs", timeout=5.0).json()
        run = rows[0]
        assert run["status"] == "succeeded", run
        assert run["workflow_id"] == "e2e-demo"
        assert run["task_count"] == 1

        # Per-run detail carries the task breakdown.
        detail = httpx.get(f"{base_url}/api/runs/{run['run_id']}", timeout=5.0).json()
        assert [t["id"] for t in detail["tasks"]] == ["build"]
        assert detail["tasks"][0]["status"] == "succeeded"

        # The CLI log for a dashboard-launched run is retrievable.
        log = httpx.get(f"{base_url}/api/runs/{run['run_id']}/log", timeout=5.0).json()
        assert "Run:" in log["text"]

        # Aggregate stats reflect the completed run.
        stats = httpx.get(f"{base_url}/api/runs/stats", timeout=5.0).json()
        assert stats["total_runs"] == 1
        assert stats["total_tasks"] == 1
        assert stats["runs_by_status"].get("succeeded") == 1

        # And it can be deleted once finished.
        assert httpx.delete(f"{base_url}/api/runs/{run['run_id']}", timeout=10.0).status_code == 200
        assert httpx.get(f"{base_url}/api/runs", timeout=5.0).json() == []
        assert not (workspace / ".orchestrator" / "runs" / run["run_id"]).exists()

    def test_prompt_for_a_workflow_without_prompt_path_is_rejected(self, live_server) -> None:
        base_url, workspace = live_server
        no_prompt = workspace / "no-prompt.json"
        no_prompt.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "id": "no-prompt",
                    "repo_set": "main",
                    "tasks": [{"id": "t", "agent": "dev", "instruction": "instructions/build.md"}],
                }
            ),
            encoding="utf-8",
        )

        response = httpx.post(
            f"{base_url}/api/runs",
            json={"workflow_path": str(no_prompt), "prompt": "nowhere to go"},
            timeout=10.0,
        )
        assert response.status_code == 400
        assert "prompt_path" in response.json()["detail"]

    def test_unknown_endpoint_returns_json_404_not_the_spa(self, live_server) -> None:
        base_url, _ = live_server
        response = httpx.get(f"{base_url}/api/nope", timeout=5.0)
        assert response.status_code == 404
