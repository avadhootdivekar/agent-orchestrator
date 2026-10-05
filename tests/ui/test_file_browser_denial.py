"""T-Hd4wQ2: the file browser's generic ``denied_paths`` mechanism (HLD 11.4, NFR-1, S12).

The workspace deliberately contains the credential store, its state directory and a
``service.env`` (``$HOME`` points at the workspace, the ``--workspace ~`` case). Everything is
exercised through the real HTTP app (auth off: the denial is the one deliberate auth-off change)
and, as ``ao ui`` wires it with auth on, through a service given explicit ``denied_paths``.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent_orchestrator.auth.paths import default_denied_paths  # noqa: E402
from agent_orchestrator.ui import files as files_module  # noqa: E402
from agent_orchestrator.ui.app import API_PREFIX, create_app  # noqa: E402
from agent_orchestrator.ui.files import (  # noqa: E402
    FileBrowser,
    PathNotAllowedError,
    Root,
)
from agent_orchestrator.ui.service import DashboardService  # noqa: E402

SECRET_TEXT = "SUPER-SECRET-CONTENT"


@pytest.fixture()
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Workspace holding the store (``.ao-store``), its state dir and ``.config/ao/service.env``."""
    root = (tmp_path / "ws").resolve()
    store = root / ".ao-store"
    (store / "state").mkdir(parents=True)
    (store / "users.json").write_text(SECRET_TEXT)
    (store / "state" / "lockouts.json").write_text(SECRET_TEXT)
    (store / "state" / "audit.jsonl").write_text(SECRET_TEXT)
    (store / "page.html").write_text(f"<html><body>{SECRET_TEXT}</body></html>")
    (root / ".config" / "ao").mkdir(parents=True)
    (root / ".config" / "ao" / "service.env").write_text(f"ANTHROPIC_API_KEY={SECRET_TEXT}")
    (root / "README.md").write_text("# ok\n")
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("print('ok')\n")
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setenv("AO_AUTH_DIR", str(store))
    monkeypatch.delenv("AO_AUTH_STATE_DIR", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    return root


@pytest.fixture(params=["auth-off-defaults", "auth-on-explicit"])
def client(ws: Path, request: pytest.FixtureRequest) -> Iterator[TestClient]:
    if request.param == "auth-off-defaults":
        service = DashboardService(str(ws))  # no denied_paths: the defaults protect it
    else:
        denied = [str(p) for p in default_denied_paths()]  # what `ao ui` passes with auth on
        service = DashboardService(str(ws), denied_paths=denied)
    with TestClient(create_app(service)) as test_client:
        yield test_client


def _get(client: TestClient, endpoint: str, path: str) -> tuple[int, str]:
    response = client.get(f"{API_PREFIX}/files{endpoint}", params={"path": path})
    return response.status_code, response.text


class TestDenial:
    @pytest.mark.parametrize("path", [".ao-store", ".ao-store/state", ".config/ao/service.env"])
    def test_listing_a_denied_path_is_403(self, client: TestClient, path: str) -> None:
        status, body = _get(client, "", path)
        assert status == 403
        assert "auth" not in body.lower().replace(".ao-store", "")

    @pytest.mark.parametrize(
        "path",
        [
            ".ao-store/users.json",
            ".ao-store/state/lockouts.json",
            ".ao-store/state/audit.jsonl",
            ".config/ao/service.env",
            "src/../.ao-store/users.json",
            ".ao-store/../.ao-store/users.json",
        ],
    )
    def test_reading_a_denied_file_is_403_and_never_leaks(
        self, client: TestClient, path: str
    ) -> None:
        status, body = _get(client, "/content", path)
        assert status == 403
        assert SECRET_TEXT not in body
        assert "auth" not in body.lower().replace(".ao-store", "")

    def test_absolute_path_is_403(self, client: TestClient, ws: Path) -> None:
        status, body = _get(client, "/content", str(ws / ".ao-store" / "users.json"))
        assert status == 403
        assert SECRET_TEXT not in body

    def test_symlink_into_the_store_is_403(self, client: TestClient, ws: Path) -> None:
        (ws / "link").symlink_to(ws / ".ao-store")
        (ws / "envlink").symlink_to(ws / ".config" / "ao" / "service.env")
        assert _get(client, "/content", "link/users.json")[0] == 403
        assert _get(client, "", "link")[0] == 403
        status, body = _get(client, "/content", "envlink")
        assert status == 403
        assert SECRET_TEXT not in body

    def test_html_preview_of_a_markup_file_in_the_store_is_403(self, client: TestClient) -> None:
        status, body = _get(client, "/html", ".ao-store/page.html")
        assert status == 403
        assert SECRET_TEXT not in body

    def test_listing_the_parent_omits_the_denied_entries(
        self, client: TestClient, ws: Path
    ) -> None:
        (ws / "link").symlink_to(ws / ".ao-store")
        response = client.get(f"{API_PREFIX}/files", params={"path": ""})
        names = {e["name"] for e in response.json()["entries"]}
        assert {"README.md", "src"} <= names
        assert ".ao-store" not in names
        assert "link" not in names
        config = client.get(f"{API_PREFIX}/files", params={"path": ".config/ao"})
        assert config.status_code == 200
        assert "service.env" not in {e["name"] for e in config.json()["entries"]}

    def test_neighbouring_paths_stay_browsable(self, client: TestClient, ws: Path) -> None:
        (ws / ".ao-store-notes").mkdir()
        (ws / ".ao-store-notes" / "n.txt").write_text("fine")
        assert _get(client, "/content", ".ao-store-notes/n.txt")[0] == 200
        assert _get(client, "/content", "src/main.py")[0] == 200

    def test_a_denied_path_that_does_not_exist_yet_is_still_denied(
        self, client: TestClient
    ) -> None:
        assert _get(client, "/content", ".ao-store/state/never-created.json")[0] == 403


class TestDefaults:
    def test_defaults_cover_hermetic_dir_state_xdg_and_service_env(
        self, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(ws / "xdgc"))
        monkeypatch.setenv("XDG_STATE_HOME", str(ws / "xdgs"))
        (ws / "xdgc" / "ao" / "auth").mkdir(parents=True)
        (ws / "xdgs" / "ao" / "auth").mkdir(parents=True)
        (ws / "xdgc" / "ao" / "service.env").write_text(SECRET_TEXT)
        service = DashboardService(str(ws))
        for rel in (".ao-store", ".ao-store/state", "xdgc/ao/auth", "xdgs/ao/auth"):
            with pytest.raises(PathNotAllowedError):
                service.list_dir(path=rel)
        for rel in ("xdgc/ao/service.env", ".config/ao/service.env"):
            with pytest.raises(PathNotAllowedError):
                service.read_file(path=rel)

    def test_explicit_empty_list_disables_the_default_denial(self, ws: Path) -> None:
        service = DashboardService(str(ws), denied_paths=[])
        assert service.read_file(path=".ao-store/users.json")["text"] == SECRET_TEXT


class TestOneDenyHelper:
    def test_resolve_calls_is_denied_exactly_once_per_request(
        self, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        browser = FileBrowser(
            roots=[Root(name="workspace", path=str(ws))],
            denied_paths=[str(ws / ".ao-store")],
        )
        calls: list[Path] = []
        original = FileBrowser._is_denied

        def spy(self: FileBrowser, resolved: Path) -> bool:
            calls.append(resolved)
            return original(self, resolved)

        monkeypatch.setattr(FileBrowser, "_is_denied", spy)
        browser.resolve("workspace", "src/main.py")
        assert len(calls) == 1
        with pytest.raises(PathNotAllowedError, match="path is not browsable"):
            browser.resolve("workspace", ".ao-store/users.json")
        assert len(calls) == 2

    def test_no_other_deny_check_exists_in_files_module(self) -> None:
        source = inspect.getsource(files_module)
        assert source.count("is_within(") == 1  # only inside `_is_denied`
        assert source.count("self._is_denied(") == 1  # only inside `resolve`
        assert source.count("entry_is_denied(") == 1  # the one listing call

    def test_a_path_escaping_the_root_keeps_its_existing_error(self, ws: Path) -> None:
        browser = FileBrowser(roots=[Root(name="workspace", path=str(ws))])
        with pytest.raises(PathNotAllowedError, match="escapes root"):
            browser.resolve("workspace", "../outside")

    def test_no_denied_paths_means_unchanged_behaviour(self, ws: Path) -> None:
        browser = FileBrowser(roots=[Root(name="workspace", path=str(ws))])
        assert ".ao-store" in {e.name for e in browser.list_dir("workspace", "")}
        assert browser.read_file("workspace", ".ao-store/users.json").text == SECRET_TEXT
