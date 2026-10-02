"""Dashboard API for the recorded run prompt (E-Us9Kd4 FR-13)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="dashboard API needs the optional [ui] extra")
from fastapi.testclient import TestClient  # noqa: E402

from agent_orchestrator.models import MAX_PROMPT_BYTES, RunPrompt  # noqa: E402
from agent_orchestrator.project_config import ProjectConfig  # noqa: E402
from agent_orchestrator.ui.app import API_PREFIX, create_app  # noqa: E402
from agent_orchestrator.ui.runs import PROMPT_PREVIEW_CHARS, prompt_preview  # noqa: E402
from agent_orchestrator.ui.service import DashboardService  # noqa: E402

from .conftest import StubSupervisor, make_run_state, write_run  # noqa: E402

HOSTILE = "<script>alert(1)</script><img src=x onerror=alert(2)> **bold** `code`"


@pytest.fixture()
def client(workspace: Path, stub_supervisor: StubSupervisor) -> Iterator[TestClient]:
    service = DashboardService(
        str(workspace),
        supervisor=stub_supervisor,  # type: ignore[arg-type]
        project_config=ProjectConfig(),
    )
    with TestClient(create_app(service)) as c:
        yield c


def _prompt(text: str, path: str = "prompts/run.md", **kw) -> RunPrompt:
    base = dict(
        text=text, chars=len(text), source="cli-prompt", path=path,
        sha256=hashlib.sha256(text.encode()).hexdigest(), captured_at="2026-10-02T12:00:00+00:00",
    )  # fmt: skip
    base.update(kw)
    return RunPrompt(**base)  # type: ignore[arg-type]


def _write(workspace: Path, prompt: RunPrompt | None, run_id: str = "r1") -> None:
    state = make_run_state(run_id=run_id)
    state.prompt = prompt
    write_run(workspace, state)


def _detail(client: TestClient, run_id: str = "r1") -> dict:
    return client.get(f"{API_PREFIX}/runs/{run_id}").json()


class TestPreviewHelper:
    def test_collapses_whitespace_and_ellipsizes(self) -> None:
        out = prompt_preview("line one\n\n  line   two " + "z" * 200)
        assert out is not None and "\n" not in out
        assert len(out) == PROMPT_PREVIEW_CHARS and out.endswith("…")

    def test_short_unchanged_and_blank_is_none(self) -> None:
        assert prompt_preview("  hi \n there ") == "hi there"
        assert prompt_preview("  \n\t") is None


class TestApi:
    def test_old_state_without_prompt(self, client: TestClient, workspace: Path) -> None:
        _write(workspace, None)
        d = _detail(client)
        assert d["prompt"] is None and d["prompt_changed_since_start"] is None
        row = client.get(f"{API_PREFIX}/runs").json()[0]
        assert row["prompt_preview"] is None

    def test_detail_has_full_prompt_list_only_preview(
        self, client: TestClient, workspace: Path
    ) -> None:
        text = "Add rate limiting\n" + "detail " * 100
        _write(workspace, _prompt(text))
        d = _detail(client)
        assert d["prompt"]["text"] == text and d["prompt"]["source"] == "cli-prompt"
        row = client.get(f"{API_PREFIX}/runs").json()[0]
        assert row["prompt_preview"].startswith("Add rate limiting detail")
        assert "prompt" not in row and text not in str(row)
        assert d["summary"]["prompt_preview"] == row["prompt_preview"]

    def test_truncated_prompt_is_passed_through(self, client: TestClient, workspace: Path) -> None:
        kept = "a" * MAX_PROMPT_BYTES
        _write(workspace, _prompt(kept, truncated=True, chars=MAX_PROMPT_BYTES * 3))
        p = _detail(client)["prompt"]
        assert p["truncated"] is True and p["chars"] == MAX_PROMPT_BYTES * 3
        assert len(p["text"]) == MAX_PROMPT_BYTES

    def test_hostile_prompt_returned_as_data(self, client: TestClient, workspace: Path) -> None:
        _write(workspace, _prompt(HOSTILE))
        resp = client.get(f"{API_PREFIX}/runs/r1")
        assert resp.headers["content-type"].startswith("application/json")
        assert resp.json()["prompt"]["text"] == HOSTILE
        row = client.get(f"{API_PREFIX}/runs").json()[0]
        assert row["prompt_preview"].startswith("<script>")  # data; escaped client-side

    def test_changed_since_start(self, client: TestClient, workspace: Path) -> None:
        f = workspace / "prompts" / "run.md"
        f.parent.mkdir()
        f.write_text("original", encoding="utf-8")
        _write(workspace, _prompt("original"))
        assert _detail(client)["prompt_changed_since_start"] is False
        f.write_text("edited", encoding="utf-8")
        assert _detail(client)["prompt_changed_since_start"] is True
        f.unlink()
        assert _detail(client)["prompt_changed_since_start"] is None

    def test_path_escape_is_not_read(self, client: TestClient, workspace: Path) -> None:
        _write(workspace, _prompt("x", path="../../etc/passwd"))
        assert _detail(client)["prompt_changed_since_start"] is None
