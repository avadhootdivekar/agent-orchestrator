"""Tests for the dashboard's workflow-template endpoints (T-Tu1api-ui-endpoints, HLD §2.6).

Two layers, matching the pattern in `test_service.py` / `test_api_integration.py`:

* `TestListTemplatesService` / `TestCreateInstanceService` exercise `DashboardService`
  directly (framework-free, fast) — discovery precedence, scaffold-only vs. start=True,
  slug derivation, and the 400/404/409 error paths.
* `TestTemplatesApi` drives the real FastAPI app through `TestClient`, asserting the exact
  JSON field names `ui/src/types.ts` (`TemplateInfo`, `CreateInstanceResponse`) expects, and
  that the service-level errors map to the HTTP status codes HLD §2.6 specifies.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import agent_orchestrator.templates as templates_mod
from agent_orchestrator.project_config import ProjectConfig
from agent_orchestrator.ui.service import DashboardError, DashboardService

from .conftest import StubSupervisor, write_template

fastapi = pytest.importorskip("fastapi", reason="dashboard API needs the optional [ui] extra")
from fastapi.testclient import TestClient  # noqa: E402

from agent_orchestrator.ui.app import API_PREFIX, create_app  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_builtin_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point template discovery's built-in root at an empty tmp dir by default, so these
    tests never depend on (or are polluted by) the real shipped built-in templates — same
    isolation `tests/test_templates.py::TestDiscovery` uses for the core module's own tests.
    """
    monkeypatch.setattr(templates_mod, "_BUILTIN_ROOT", tmp_path / "fake-builtin-root")


@pytest.fixture()
def mini_template_dir(workspace: Path) -> Path:
    """A minimal template registered under a `cfg-templates/` dir next to the workspace."""
    return write_template(workspace / "cfg-templates", dirname="mini-template", name="mini")


@pytest.fixture()
def template_config(mini_template_dir: Path) -> ProjectConfig:
    return ProjectConfig(templates=[str(mini_template_dir)])


@pytest.fixture()
def service_with_template(
    workspace: Path, stub_supervisor: StubSupervisor, template_config: ProjectConfig
) -> DashboardService:
    return DashboardService(
        str(workspace),
        supervisor=stub_supervisor,  # type: ignore[arg-type]
        project_config=template_config,
    )


@pytest.fixture()
def template_client(
    workspace: Path, stub_supervisor: StubSupervisor, template_config: ProjectConfig
) -> Iterator[TestClient]:
    service = DashboardService(
        str(workspace),
        supervisor=stub_supervisor,  # type: ignore[arg-type]
        project_config=template_config,
    )
    with TestClient(create_app(service)) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Service: list_templates
# ---------------------------------------------------------------------------


class TestListTemplatesService:
    def test_workspace_registered_template_is_visible(
        self, service_with_template: DashboardService
    ) -> None:
        infos = service_with_template.list_templates()
        assert [i["name"] for i in infos] == ["mini"]
        assert infos[0]["source"] == "workspace"
        assert infos[0]["params"][0]["name"] == "greeting"

    def test_builtin_and_workspace_registered_are_both_visible(
        self,
        workspace: Path,
        stub_supervisor: StubSupervisor,
        template_config: ProjectConfig,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Point the module's built-in root at a temp dir with its own fixture template so
        # this test never depends on (or is polluted by) the real shipped built-ins.
        fake_builtin_root = tmp_path / "fake-builtin-root"
        write_template(fake_builtin_root, dirname="builtin-tmpl", name="builtin-tmpl")
        monkeypatch.setattr(templates_mod, "_BUILTIN_ROOT", fake_builtin_root)

        service = DashboardService(
            str(workspace),
            supervisor=stub_supervisor,  # type: ignore[arg-type]
            project_config=template_config,
        )
        names = {i["name"] for i in service.list_templates()}
        assert names == {"mini", "builtin-tmpl"}

    def test_empty_workspace_lists_nothing(
        self, workspace: Path, stub_supervisor: StubSupervisor
    ) -> None:
        service = DashboardService(
            str(workspace), supervisor=stub_supervisor, project_config=ProjectConfig()
        )  # type: ignore[arg-type]
        assert service.list_templates() == []

    def test_misconfigured_templates_entry_raises_dashboard_error(
        self, workspace: Path, stub_supervisor: StubSupervisor
    ) -> None:
        cfg = ProjectConfig(templates=[str(workspace / "does-not-exist")])
        service = DashboardService(str(workspace), supervisor=stub_supervisor, project_config=cfg)  # type: ignore[arg-type]
        with pytest.raises(DashboardError):
            service.list_templates()


# ---------------------------------------------------------------------------
# Service: create_instance
# ---------------------------------------------------------------------------


class TestCreateInstanceService:
    def test_scaffold_only_creates_files_and_matches_response_shape(
        self,
        service_with_template: DashboardService,
        workspace: Path,
        stub_supervisor: StubSupervisor,
    ) -> None:
        response = service_with_template.create_instance(
            "mini", slug_or_id="my-feature", params={}, prompt="Do the thing", start=False
        )

        assert set(response) == {"instance_dir", "workflow_path", "workflow", "launch"}
        assert response["launch"] is None
        assert stub_supervisor.launch_calls == []

        assert Path(response["workflow_path"]).is_file()
        assert (Path(response["instance_dir"]) / "prompt.md").read_text(
            encoding="utf-8"
        ) == "Do the thing"

        workflow = response["workflow"]
        assert workflow["id"].endswith("-my-feature")
        assert workflow["task_count"] == 1
        assert workflow["prompt_path"]

    def test_create_with_start_launches_and_does_not_double_write_prompt(
        self,
        service_with_template: DashboardService,
        workspace: Path,
        stub_supervisor: StubSupervisor,
    ) -> None:
        response = service_with_template.create_instance(
            "mini",
            slug_or_id="run-me",
            params={},
            prompt="hello there",
            start=True,
            options={"model": "claude-opus-4-8"},
        )

        assert response["launch"] is not None
        assert response["launch"]["run_id"] == "launched-run"
        assert len(stub_supervisor.launch_calls) == 1

        call = stub_supervisor.launch_calls[0]
        assert call["workflow_path"] == response["workflow_path"]
        # The prompt was already written into prompt.md by instantiate(); passing it again
        # to launch_run would write it a second time via a separate launch-scoped file.
        assert call.get("prompt") is None
        assert call["options"] == {"model": "claude-opus-4-8"}

        prompt_path = Path(response["instance_dir"]) / "prompt.md"
        assert prompt_path.read_text(encoding="utf-8") == "hello there"

    def test_scaffold_only_without_start_does_not_touch_the_supervisor(
        self, service_with_template: DashboardService, stub_supervisor: StubSupervisor
    ) -> None:
        service_with_template.create_instance(
            "mini", slug_or_id="quiet", params={}, prompt="x", start=False
        )
        assert stub_supervisor.launch_calls == []

    def test_slug_is_derived_from_the_first_prompt_line(
        self, service_with_template: DashboardService
    ) -> None:
        response = service_with_template.create_instance(
            "mini",
            params={},
            prompt="Hello World!\nSecond line describing the task in more detail.",
            start=False,
        )
        assert Path(response["instance_dir"]).name.endswith("-hello-world")

    def test_unknown_template_raises_a_distinct_not_found_error(
        self, service_with_template: DashboardService
    ) -> None:
        with pytest.raises(DashboardError, match="template not found"):
            service_with_template.create_instance("does-not-exist", slug_or_id="x", params={})

    def test_ad_hoc_filesystem_path_as_name_is_rejected(
        self, service_with_template: DashboardService, mini_template_dir: Path
    ) -> None:
        """M1 regression: `create_instance` must resolve `name` ONLY against the
        discovered/vetted list -- `templates.load_template()`'s ad-hoc-filesystem-path
        branch (a CLI-only affordance) must not be reachable through the dashboard, even
        when the path happens to point at an otherwise-registered template's own
        directory."""
        with pytest.raises(DashboardError, match="template not found"):
            service_with_template.create_instance(str(mini_template_dir), slug_or_id="x", params={})

    def test_missing_slug_and_prompt_raises(self, service_with_template: DashboardService) -> None:
        with pytest.raises(DashboardError, match="slug"):
            service_with_template.create_instance("mini", params={})

    def test_blank_prompt_does_not_derive_a_slug(
        self, service_with_template: DashboardService
    ) -> None:
        with pytest.raises(DashboardError, match="slug"):
            service_with_template.create_instance("mini", params={}, prompt="   \n  !!! \n")

    def test_unknown_param_raises_a_generic_error_not_404_or_409(
        self, service_with_template: DashboardService
    ) -> None:
        with pytest.raises(DashboardError) as exc_info:
            service_with_template.create_instance("mini", slug_or_id="x", params={"nope": "y"})
        message = str(exc_info.value)
        assert "template not found" not in message
        assert "prompt conflict" not in message

    def test_missing_required_param_raises_a_generic_error(
        self, workspace: Path, stub_supervisor: StubSupervisor
    ) -> None:
        tdir = write_template(
            workspace / "cfg-templates",
            dirname="req-template",
            name="req",
            extra_params={"repo_set": {"description": "Target repo set", "required": True}},
        )
        service = DashboardService(
            str(workspace),
            supervisor=stub_supervisor,  # type: ignore[arg-type]
            project_config=ProjectConfig(templates=[str(tdir)]),
        )
        with pytest.raises(DashboardError) as exc_info:
            service.create_instance("req", slug_or_id="x", params={})
        message = str(exc_info.value)
        assert "template not found" not in message
        assert "prompt conflict" not in message

    def test_conflicting_prompt_on_an_existing_instance_raises_prompt_conflict(
        self, service_with_template: DashboardService
    ) -> None:
        first = service_with_template.create_instance(
            "mini", slug_or_id="dup", params={}, prompt="first version", start=False
        )
        full_id = Path(first["instance_dir"]).name

        with pytest.raises(DashboardError, match="prompt conflict"):
            service_with_template.create_instance(
                "mini",
                slug_or_id=full_id,
                params={},
                prompt="second, different version",
                start=False,
            )

    def test_identical_re_instantiate_is_idempotent_not_a_conflict(
        self, service_with_template: DashboardService
    ) -> None:
        first = service_with_template.create_instance(
            "mini", slug_or_id="same", params={}, prompt="same text", start=False
        )
        full_id = Path(first["instance_dir"]).name

        second = service_with_template.create_instance(
            "mini", slug_or_id=full_id, params={}, prompt="same text", start=False
        )
        assert second["instance_dir"] == first["instance_dir"]


# ---------------------------------------------------------------------------
# FastAPI: GET /api/templates, POST /api/templates/{name}/instances
# ---------------------------------------------------------------------------


class TestTemplatesApi:
    def test_get_templates_lists_the_registered_template_with_expected_fields(
        self, template_client: TestClient
    ) -> None:
        body = template_client.get(f"{API_PREFIX}/templates").json()
        assert [t["name"] for t in body] == ["mini"]

        entry = body[0]
        assert set(entry) == {
            "name",
            "description",
            "path",
            "source",
            "params",
            "required_agents",
            "prompt_skeleton",
        }
        assert entry["source"] == "workspace"

        param = entry["params"][0]
        assert set(param) == {"name", "description", "required", "enum", "default"}

    def test_post_creates_instance_with_the_expected_response_shape(
        self, template_client: TestClient, stub_supervisor: StubSupervisor
    ) -> None:
        response = template_client.post(
            f"{API_PREFIX}/templates/mini/instances",
            json={"params": {}, "prompt": "Build the thing", "start": True, "options": {}},
        )
        assert response.status_code == 201

        body = response.json()
        assert set(body) == {"instance_dir", "workflow_path", "workflow", "launch"}
        assert set(body["workflow"]) == {
            "id",
            "name",
            "path",
            "task_count",
            "prompt_path",
            "general_instructions",
            "error",
        }
        assert body["launch"]["run_id"] == "launched-run"
        assert len(stub_supervisor.launch_calls) == 1

    def test_post_without_start_returns_a_null_launch(self, template_client: TestClient) -> None:
        response = template_client.post(
            f"{API_PREFIX}/templates/mini/instances",
            json={"slug_or_id": "scaffold-only", "params": {}, "start": False, "options": {}},
        )
        assert response.status_code == 201
        assert response.json()["launch"] is None

    def test_post_unknown_template_returns_404(self, template_client: TestClient) -> None:
        response = template_client.post(
            f"{API_PREFIX}/templates/does-not-exist/instances",
            json={"params": {}, "start": False, "options": {}},
        )
        assert response.status_code == 404

    def test_post_cwd_relative_path_to_unregistered_template_returns_404(
        self,
        template_client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """M1 regression -- reproduces the review's 'sneaky' scenario: only ``mini`` is
        registered (`GET /api/templates` -> ``["mini"]``), but a second, completely
        unregistered template directory exists elsewhere. Before the fix, `create_instance`
        fell through to `templates.load_template()`'s ad-hoc-path branch, so a bare
        (slash-free) `name` that happened to resolve to a valid template dir relative to
        the *server process's* CWD would be silently accepted and instantiated (201) even
        though the dashboard never listed it. It must now be a 404."""
        sneaky_parent = tmp_path / "sneaky-parent"
        write_template(sneaky_parent, dirname="sneaky", name="sneaky")
        monkeypatch.chdir(sneaky_parent)

        response = template_client.post(
            f"{API_PREFIX}/templates/sneaky/instances",
            json={"slug_or_id": "x", "params": {}, "start": False, "options": {}},
        )
        assert response.status_code == 404

    def test_post_missing_slug_and_prompt_returns_400(self, template_client: TestClient) -> None:
        response = template_client.post(
            f"{API_PREFIX}/templates/mini/instances",
            json={"params": {}, "start": False, "options": {}},
        )
        assert response.status_code == 400

    def test_post_unknown_param_returns_400(self, template_client: TestClient) -> None:
        response = template_client.post(
            f"{API_PREFIX}/templates/mini/instances",
            json={
                "slug_or_id": "x",
                "params": {"nope": "y"},
                "start": False,
                "options": {},
            },
        )
        assert response.status_code == 400

    def test_post_conflicting_prompt_returns_409(self, template_client: TestClient) -> None:
        first = template_client.post(
            f"{API_PREFIX}/templates/mini/instances",
            json={
                "slug_or_id": "dup2",
                "params": {},
                "prompt": "v1",
                "start": False,
                "options": {},
            },
        )
        full_id = Path(first.json()["instance_dir"]).name

        response = template_client.post(
            f"{API_PREFIX}/templates/mini/instances",
            json={
                "slug_or_id": full_id,
                "params": {},
                "prompt": "v2, different",
                "start": False,
                "options": {},
            },
        )
        assert response.status_code == 409

    def test_malformed_body_returns_422(self, template_client: TestClient) -> None:
        response = template_client.post(
            f"{API_PREFIX}/templates/mini/instances", json={"params": "not-a-dict"}
        )
        assert response.status_code == 422
