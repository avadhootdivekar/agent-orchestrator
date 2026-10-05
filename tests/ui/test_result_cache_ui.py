"""Dashboard surface of the cross-run result cache (T-bLpoze, HLD 8.10, ADR-0019 D9 / D35).

* D-1a: `tasks[].result_cache` / top-level `result_cache` in the run-detail payload.
* D-1b: the file browser never serves the cache store (M-10).
* U-LZ2: building a run detail for a state without records never loads `cache.report`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi", reason="dashboard API needs the optional [ui] extra")
from fastapi.testclient import TestClient  # noqa: E402

import agent_orchestrator  # noqa: E402
from agent_orchestrator.cache import report  # noqa: E402
from agent_orchestrator.project_config import ProjectConfig  # noqa: E402
from agent_orchestrator.ui.app import API_PREFIX, create_app  # noqa: E402
from agent_orchestrator.ui.files import FileBrowser, PathNotAllowedError, Root  # noqa: E402
from agent_orchestrator.ui.runs import RunRepository  # noqa: E402
from agent_orchestrator.ui.service import DashboardService  # noqa: E402
from tests.cache._report_states import (  # noqa: E402
    TASK_VIEW_KEYS,
    hit_rec,
    mixed_state,
    rec,
    run_state,
    settled,
    would_hit_rec,
)

from .conftest import StubSupervisor, make_run_state, write_run  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = Path(agent_orchestrator.__file__).resolve().parents[1]
REPORT_MODULE = "agent_orchestrator.cache.report"
SUBPROCESS_TIMEOUT_SECONDS = 120
HOSTILE_SOURCE = "<img src=x onerror=alert(1)>"


@pytest.fixture()
def client(workspace: Path, stub_supervisor: StubSupervisor) -> Iterator[TestClient]:
    service = DashboardService(
        str(workspace),
        supervisor=stub_supervisor,  # type: ignore[arg-type]
        project_config=ProjectConfig(),
    )
    with TestClient(create_app(service)) as test_client:
        yield test_client


def _detail(client: TestClient, run_id: str) -> dict:
    response = client.get(f"{API_PREFIX}/runs/{run_id}")
    assert response.status_code == 200
    body: dict = response.json()
    return body


class TestRunDetailPayload:
    """D-1a."""

    def test_task_and_run_blocks_equal_the_report_helpers(
        self, workspace: Path, client: TestClient
    ) -> None:
        state = mixed_state("rc-run")
        write_run(workspace, state)
        body = _detail(client, "rc-run")
        by_id = {t["id"]: t for t in body["tasks"]}
        current = report.current_records(state)
        assert set(current) == {"hit", "miss"}  # the two stale records are not current
        for tid in current:
            assert by_id[tid]["result_cache"] == report.task_view(current[tid])
            assert set(by_id[tid]["result_cache"]) == TASK_VIEW_KEYS  # exactly the D35 fields
        assert by_id["stale_cycle"]["result_cache"] is None
        assert by_id["stale_ended"]["result_cache"] is None
        assert body["result_cache"] == report.run_block(state)
        assert body["result_cache"]["hits"] == 1

    def test_hit_view_carries_the_savings(self, workspace: Path, client: TestClient) -> None:
        write_run(workspace, mixed_state("rc-run"))
        hit = next(t for t in _detail(client, "rc-run")["tasks"] if t["id"] == "hit")
        assert hit["result_cache"]["hit"] is True
        assert hit["result_cache"]["saved_cost_usd"] == pytest.approx(0.4123)
        assert hit["result_cache"]["source_run_id"] == "doc-pipeline-20261005T101450Z"

    def test_shadow_run_reports_would_hits_not_hits(
        self, workspace: Path, client: TestClient
    ) -> None:
        state = run_state({"a": settled()}, {"a": would_hit_rec()}, "shadow-run")
        write_run(workspace, state)
        body = _detail(client, "shadow-run")
        assert body["result_cache"]["hits"] == 0
        assert body["result_cache"]["would_hits"] == 1
        assert body["tasks"][0]["result_cache"]["hit"] is False

    def test_all_records_stale_gives_null_run_block(
        self, workspace: Path, client: TestClient
    ) -> None:
        state = run_state({"a": settled(cycle=2)}, {"a": hit_rec()}, "stale-run")
        write_run(workspace, state)
        body = _detail(client, "stale-run")
        assert body["result_cache"] is None
        assert body["tasks"][0]["result_cache"] is None

    def test_run_without_records_serializes_as_before_plus_two_null_keys(
        self, workspace: Path, client: TestClient
    ) -> None:
        state = make_run_state(run_id="plain-run")
        assert state.result_cache == {}
        write_run(workspace, state)
        body = _detail(client, "plain-run")
        assert body["result_cache"] is None
        assert all(t["result_cache"] is None for t in body["tasks"])
        # The only new keys are the two `result_cache` ones (the service adds `launch`/`is_live`).
        detail = RunRepository(str(workspace)).detail("plain-run")
        assert {k for k in vars(detail)} <= set(body)
        assert set(body["tasks"][0]) - {"result_cache"} == {
            f for f in vars(detail.tasks[0]) if f != "result_cache"
        }

    def test_hostile_provenance_passes_through_as_data(
        self, workspace: Path, client: TestClient
    ) -> None:
        state = run_state(
            {"a": settled()}, {"a": hit_rec(source_run_id=HOSTILE_SOURCE)}, "hostile-run"
        )
        write_run(workspace, state)
        body = _detail(client, "hostile-run")
        assert body["tasks"][0]["result_cache"]["source_run_id"] == HOSTILE_SOURCE

    def test_miss_with_ineligible_record_is_a_non_hit_view(
        self, workspace: Path, client: TestClient
    ) -> None:
        state = run_state({"a": settled()}, {"a": rec("ineligible")}, "inel-run")
        write_run(workspace, state)
        body = _detail(client, "inel-run")
        assert body["tasks"][0]["result_cache"]["hit"] is False
        assert body["result_cache"]["ineligible"] == 1


def _seed_cache(workspace: Path) -> Path:
    cache = workspace / ".orchestrator" / "cache"
    blob_dir = cache / "blobs" / "ab"
    blob_dir.mkdir(parents=True)
    (blob_dir / ("c" * 64)).write_text("cached secret payload", encoding="utf-8")
    (cache / "entries" / "v1").mkdir(parents=True)
    (cache / "entries" / "v1" / "e.json").write_text("{}", encoding="utf-8")
    return cache


@pytest.fixture()
def browser(workspace: Path) -> FileBrowser:
    return FileBrowser([Root(name="workspace", path=str(workspace))])


class TestFileBrowserDeniesCache:
    """D-1b."""

    @pytest.mark.parametrize(
        "rel",
        [
            ".orchestrator/cache",
            ".orchestrator/cache/",
            ".orchestrator/cache/entries",
            ".orchestrator/cache/entries/v1/e.json",
            f".orchestrator/cache/blobs/ab/{'c' * 64}",
            ".orchestrator/runs/../cache",  # `..` resolving into the cache
            "src/../.orchestrator/cache/blobs",
        ],
    )
    def test_cache_paths_are_refused(self, workspace: Path, browser: FileBrowser, rel: str) -> None:
        _seed_cache(workspace)
        (workspace / "src").mkdir()
        (workspace / ".orchestrator" / "runs").mkdir(parents=True, exist_ok=True)
        with pytest.raises(PathNotAllowedError):
            browser.resolve(None, rel)
        with pytest.raises(PathNotAllowedError):
            browser.list_dir(None, rel)
        with pytest.raises(PathNotAllowedError):
            browser.read_file(None, rel)

    def test_absolute_path_into_the_cache_is_refused(
        self, workspace: Path, browser: FileBrowser
    ) -> None:
        cache = _seed_cache(workspace)
        with pytest.raises(PathNotAllowedError):
            browser.read_file(None, str(cache / "entries" / "v1" / "e.json"))

    def test_symlink_resolving_into_the_cache_is_refused(
        self, workspace: Path, browser: FileBrowser
    ) -> None:
        cache = _seed_cache(workspace)
        blob = cache / "blobs" / "ab" / ("c" * 64)
        (workspace / "innocent.txt").symlink_to(blob)
        (workspace / "innocent_dir").symlink_to(cache, target_is_directory=True)
        with pytest.raises(PathNotAllowedError):
            browser.read_file(None, "innocent.txt")
        with pytest.raises(PathNotAllowedError):
            browser.list_dir(None, "innocent_dir")

    def test_symlinked_cache_root_is_still_refused_at_its_real_location(
        self, workspace: Path, browser: FileBrowser
    ) -> None:
        # `.orchestrator/cache` itself is a symlink to another place inside the root.
        real = workspace / "elsewhere"
        (real / "blobs").mkdir(parents=True)
        (real / "blobs" / "x").write_text("x", encoding="utf-8")
        (workspace / ".orchestrator").mkdir()
        (workspace / ".orchestrator" / "cache").symlink_to(real, target_is_directory=True)
        with pytest.raises(PathNotAllowedError):
            browser.read_file(None, ".orchestrator/cache/blobs/x")
        with pytest.raises(PathNotAllowedError):
            browser.read_file(None, "elsewhere/blobs/x")

    def test_runs_and_sibling_paths_behave_as_before(
        self, workspace: Path, browser: FileBrowser
    ) -> None:
        _seed_cache(workspace)
        runs = workspace / ".orchestrator" / "runs" / "r1"
        runs.mkdir(parents=True)
        (runs / "status.json").write_text('{"ok": true}', encoding="utf-8")
        (workspace / ".orchestrator" / "cache-notes.txt").write_text("n", encoding="utf-8")
        assert browser.read_file(None, ".orchestrator/runs/r1/status.json").text == '{"ok": true}'
        assert browser.read_file(None, ".orchestrator/cache-notes.txt").text == "n"
        names = {e.name for e in browser.list_dir(None, ".orchestrator")}
        assert {"runs", "cache", "cache-notes.txt"} <= names  # the folder is listed, not opened

    def test_http_endpoints_return_403_for_the_cache(
        self, workspace: Path, client: TestClient
    ) -> None:
        _seed_cache(workspace)
        blob = f".orchestrator/cache/blobs/ab/{'c' * 64}"
        for endpoint, path in (
            ("files", ".orchestrator/cache"),
            ("files/content", blob),
            ("files/html", blob),
            ("files/content", ".orchestrator/runs/../cache/entries/v1/e.json"),
        ):
            response = client.get(f"{API_PREFIX}/{endpoint}", params={"path": path})
            assert response.status_code == 403, (endpoint, path)
            assert "cached secret payload" not in response.text


_LAZY_SCRIPT = textwrap.dedent(
    """
    import json, sys, tempfile
    from tests.cache._report_states import hit_rec, run_state, settled
    from agent_orchestrator.artifacts import LocalFsArtifactStore
    from agent_orchestrator.runstate import RunStateStore
    from agent_orchestrator.ui.runs import RunRepository

    with_records = sys.argv[1] == "records"
    state = run_state({"a": settled(), "b": settled()}, {"a": hit_rec()} if with_records else {})
    with tempfile.TemporaryDirectory() as ws:
        RunStateStore(ws, LocalFsArtifactStore(ws)).save(state)
        detail = RunRepository(ws).detail(state.run_id)
    print("REPORT=" + json.dumps({
        "loaded": sorted(m for m in sys.modules if m.startswith("agent_orchestrator.cache")),
        "run_block": detail.result_cache,
        "task_blocks": [t.result_cache for t in detail.tasks],
    }))
    """
)


def _run_lazy(mode: str) -> dict[str, object]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AO_")}
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SRC_DIR), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
    )
    proc = subprocess.run(
        [sys.executable, "-c", _LAZY_SCRIPT, mode],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("REPORT="))
    out: dict[str, object] = json.loads(line[len("REPORT=") :])
    return out


class TestLazyImport:
    """U-LZ2 (dashboard part); the second case is the positive control."""

    def test_empty_map_never_loads_cache_report(self) -> None:
        out = _run_lazy("empty")
        assert REPORT_MODULE not in out["loaded"]  # type: ignore[operator]
        assert out["run_block"] is None
        assert out["task_blocks"] == [None, None]

    def test_non_empty_map_loads_it(self) -> None:
        out = _run_lazy("records")
        assert REPORT_MODULE in out["loaded"]  # type: ignore[operator]
        assert out["run_block"] is not None
        assert out["task_blocks"][0]["hit"] is True  # type: ignore[index]
        assert out["task_blocks"][1] is None  # type: ignore[index]
