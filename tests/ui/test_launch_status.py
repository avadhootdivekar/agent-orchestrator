"""Launch status, bounded log tail, and pre-spawn validation (T-launch-status).

Covers the failure mode where `ao run` dies within seconds (e.g. an unknown repo_set) and
the dashboard used to bounce the operator to the run list with no trace of why:

* pure status derivation with a fixed clock,
* the real :class:`ProcessSupervisor` against three fake engines (exits 1 immediately,
  stays alive without creating a run dir, creates a run dir),
* hostile/garbled launch records and log-tail bounds,
* the service preflight (valid repo-set names in the error, template enum),
* the HTTP failure path through the real FastAPI app.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_orchestrator.project_config import ProjectConfig
from agent_orchestrator.ui.processes import (
    LAUNCH_ID_PATTERN,
    LOG_TAIL_MAX_BYTES,
    LOG_TAIL_MAX_LINES,
    STATUS_FAILED_TO_START,
    STATUS_RUNNING_UNCONFIRMED,
    STATUS_STARTED,
    STATUS_STARTING,
    LaunchRecord,
    ProcessSupervisor,
    derive_launch_status,
    tail_log_text,
)
from agent_orchestrator.ui.service import DashboardError, DashboardService

from .conftest import StubSupervisor, write_template, write_workflow

fastapi = pytest.importorskip("fastapi", reason="dashboard API needs the optional [ui] extra")
from fastapi.testclient import TestClient  # noqa: E402

from agent_orchestrator.ui.app import API_PREFIX, create_app  # noqa: E402

T0 = datetime(2026, 10, 3, 12, 0, 0, tzinfo=UTC)
WINDOW = 10.0

FAIL_FAST = [
    sys.executable,
    "-c",
    "import sys; print('ERROR: Unknown repo_set: ai-models', file=sys.stderr); sys.exit(1)",
]
STAY_ALIVE = [sys.executable, "-c", "import time; time.sleep(60)"]
MAKE_RUN_DIR = [
    sys.executable,
    "-c",
    "import os, time; os.makedirs('.orchestrator/runs/demo-20260101T000000Z'); time.sleep(0.3)",
]


def _record(**kw: object) -> LaunchRecord:
    base: dict = {
        "launch_id": "launch-20261003T120000000000Z",
        "kind": "run",
        "pid": 1,
        "argv": ["ao"],
        "started_at": T0.isoformat(),
        "log_path": "x",
    }
    base.update(kw)
    return LaunchRecord(**base)


class TestDeriveStatus:
    def test_run_id_means_started_even_if_process_gone(self) -> None:
        r = _record(run_id="r1", exit_code=1)
        assert derive_launch_status(r, False, T0, WINDOW) == STATUS_STARTED

    def test_gone_without_run_is_failed_to_start(self) -> None:
        assert derive_launch_status(_record(), False, T0, WINDOW) == STATUS_FAILED_TO_START

    def test_alive_young_is_starting_old_is_unconfirmed(self) -> None:
        young = T0 + timedelta(seconds=WINDOW - 0.1)
        old = T0 + timedelta(seconds=WINDOW)
        assert derive_launch_status(_record(), True, young, WINDOW) == STATUS_STARTING
        assert derive_launch_status(_record(), True, old, WINDOW) == STATUS_RUNNING_UNCONFIRMED

    def test_bad_timestamp_never_claims_starting(self) -> None:
        r = _record(started_at="garbage")
        assert derive_launch_status(r, True, T0, WINDOW) == STATUS_RUNNING_UNCONFIRMED


class TestTailLog:
    def test_missing_file(self, tmp_path: Path) -> None:
        assert tail_log_text(tmp_path / "nope.log") == ("", False)

    def test_keeps_last_n_lines_and_flags_truncation(self, tmp_path: Path) -> None:
        f = tmp_path / "a.log"
        f.write_text("\n".join(f"line {i}" for i in range(100)), encoding="utf-8")
        text, truncated = tail_log_text(f)
        lines = text.splitlines()
        assert len(lines) == LOG_TAIL_MAX_LINES
        assert lines[-1] == "line 99"
        assert truncated is True

    def test_short_log_is_not_truncated(self, tmp_path: Path) -> None:
        f = tmp_path / "a.log"
        f.write_text("ERROR: boom\n", encoding="utf-8")
        assert tail_log_text(f) == ("ERROR: boom", False)

    def test_byte_cap_drops_partial_first_line(self, tmp_path: Path) -> None:
        f = tmp_path / "a.log"
        f.write_text(("x" * 100 + "\n") * 1000, encoding="utf-8")
        text, truncated = tail_log_text(f, max_bytes=1000, max_lines=500)
        assert truncated is True
        assert len(text.encode()) <= 1000
        assert all(line == "x" * 100 for line in text.splitlines())

    def test_single_huge_line_is_bounded(self, tmp_path: Path) -> None:
        f = tmp_path / "a.log"
        f.write_text("y" * (LOG_TAIL_MAX_BYTES * 5), encoding="utf-8")
        text, truncated = tail_log_text(f)
        assert len(text) <= LOG_TAIL_MAX_BYTES
        assert truncated is True

    def test_strips_terminal_control_characters(self, tmp_path: Path) -> None:
        f = tmp_path / "a.log"
        f.write_bytes(b"ok\x1b[31mred\x07\x00\n\xff\xfebad utf8\ttab\n")
        text, _ = tail_log_text(f)
        assert "\x1b" not in text and "\x07" not in text and "\x00" not in text
        assert "ok[31mred" in text and "\t" in text


@pytest.fixture()
def clock() -> list[datetime]:
    """Mutable fixed clock: tests advance it by assigning ``clock[0]``."""
    return [T0]


def _sup(workspace: Path, command: list[str], clock: list[datetime], timeout: float = 1.0):
    return ProcessSupervisor(
        str(workspace), ao_command=command, run_id_discovery_timeout=timeout, clock=lambda: clock[0]
    )


def _wait_exit(sup: ProcessSupervisor, launch_id: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = sup.get_launch(launch_id)
        if record is not None and record.finished_at:
            return
        time.sleep(0.02)
    raise AssertionError("child never exited")


class TestSupervisorFailFast:
    def test_immediate_exit_returns_early_with_failure_record(
        self, workspace: Path, clock: list[datetime]
    ) -> None:
        # A 30 s discovery window must NOT be waited out when the child is already dead.
        sup = _sup(workspace, FAIL_FAST, clock, timeout=30.0)
        started = time.monotonic()
        record = sup.launch_run(workflow_path="wf.json")
        assert time.monotonic() - started < 10.0

        assert record.run_id is None
        assert record.exit_code == 1
        assert record.finished_at == T0.isoformat()
        view = sup.describe(record, include_log=True)
        assert view["status"] == STATUS_FAILED_TO_START
        assert "Unknown repo_set: ai-models" in view["log_tail"]
        assert view["log_truncated"] is False

    def test_persisted_record_survives_new_supervisor(
        self, workspace: Path, clock: list[datetime]
    ) -> None:
        record = _sup(workspace, FAIL_FAST, clock).launch_run()
        fresh = _sup(workspace, FAIL_FAST, clock)
        loaded = fresh.get_launch(record.launch_id)
        assert loaded is not None and loaded.exit_code == 1
        assert fresh.describe(loaded)["status"] == STATUS_FAILED_TO_START


class TestSupervisorAlive:
    def test_alive_without_run_dir_is_starting_then_unconfirmed(
        self, workspace: Path, clock: list[datetime]
    ) -> None:
        sup = _sup(workspace, STAY_ALIVE, clock, timeout=0.2)
        record = sup.launch_run()
        try:
            assert record.run_id is None and record.finished_at is None
            assert sup.describe(record)["status"] == STATUS_STARTING  # fixed clock: age 0
            clock[0] = T0 + timedelta(seconds=1)  # past the 0.2 s window
            assert sup.describe(record)["status"] == STATUS_RUNNING_UNCONFIRMED
        finally:
            os.killpg(os.getpgid(record.pid), 9)

    def test_run_dir_appearing_later_flips_to_started(
        self, workspace: Path, clock: list[datetime]
    ) -> None:
        sup = _sup(workspace, STAY_ALIVE, clock, timeout=0.1)
        record = sup.launch_run()
        try:
            assert sup.describe(record)["status"] != STATUS_STARTED
            (workspace / ".orchestrator" / "runs" / "late-1").mkdir(parents=True)
            again = sup.get_launch(record.launch_id)
            assert again is not None and again.run_id == "late-1"
            assert sup.describe(again)["status"] == STATUS_STARTED
        finally:
            os.killpg(os.getpgid(record.pid), 9)

    def test_run_dir_created_is_started(self, workspace: Path, clock: list[datetime]) -> None:
        sup = _sup(workspace, MAKE_RUN_DIR, clock, timeout=5.0)
        record = sup.launch_run()
        assert record.run_id == "demo-20260101T000000Z"
        view = sup.describe(record, include_log=True)
        assert view["status"] == STATUS_STARTED
        assert view["log_tail"] == ""


class TestHostileRecords:
    def _write(self, sup: ProcessSupervisor, name: str, text: str) -> None:
        sup.launches_dir.mkdir(parents=True, exist_ok=True)
        (sup.launches_dir / name).write_text(text, encoding="utf-8")

    def test_garbled_and_wrong_shaped_records_are_skipped(
        self, workspace: Path, clock: list[datetime]
    ) -> None:
        sup = _sup(workspace, FAIL_FAST, clock)
        good = sup.launch_run()
        self._write(sup, "bad1.json", "{not json")
        self._write(sup, "bad2.json", "[1, 2]")
        self._write(sup, "bad3.json", json.dumps({"launch_id": "x"}))  # required keys missing
        assert [r.launch_id for r in sup.load_records()] == [good.launch_id]

    def test_unknown_keys_from_newer_version_are_tolerated(
        self, workspace: Path, clock: list[datetime]
    ) -> None:
        sup = _sup(workspace, FAIL_FAST, clock)
        record = sup.launch_run()
        path = sup.launches_dir / f"{record.launch_id}.json"
        data = json.loads(path.read_text())
        data["future_field"] = 1
        path.write_text(json.dumps(data))
        assert sup.get_launch(record.launch_id) is not None

    @pytest.mark.parametrize(
        "launch_id",
        [
            "../etc/passwd",
            "launch-../../x",
            "launch-1",
            "",
            "launch-20261003T120000000000Z/..",
            "x",
        ],
    )
    def test_malformed_ids_are_not_found(
        self, workspace: Path, clock: list[datetime], launch_id: str
    ) -> None:
        assert _sup(workspace, FAIL_FAST, clock).get_launch(launch_id) is None

    def test_record_whose_id_disagrees_with_filename_is_rejected(
        self, workspace: Path, clock: list[datetime]
    ) -> None:
        sup = _sup(workspace, FAIL_FAST, clock)
        record = sup.launch_run()
        other = "launch-20200101T000000000000Z"
        data = json.loads((sup.launches_dir / f"{record.launch_id}.json").read_text())
        self._write(sup, f"{other}.json", json.dumps(data))  # claims record.launch_id
        assert sup.get_launch(other) is None

    def test_tampered_log_path_is_ignored(self, workspace: Path, clock: list[datetime]) -> None:
        secret = workspace / "secret.txt"
        secret.write_text("TOP SECRET")
        sup = _sup(workspace, FAIL_FAST, clock)
        record = sup.launch_run()
        path = sup.launches_dir / f"{record.launch_id}.json"
        data = json.loads(path.read_text())
        data["log_path"] = str(secret)
        path.write_text(json.dumps(data))
        loaded = sup.get_launch(record.launch_id)
        assert loaded is not None
        assert "TOP SECRET" not in sup.describe(loaded, include_log=True)["log_tail"]

    def test_symlinked_log_pointing_outside_is_not_read(
        self, workspace: Path, clock: list[datetime]
    ) -> None:
        secret = workspace / "secret.txt"
        secret.write_text("TOP SECRET")
        sup = _sup(workspace, FAIL_FAST, clock)
        record = sup.launch_run()
        log = sup.logs_dir / f"{record.launch_id}.log"
        log.unlink()
        log.symlink_to(secret)
        assert sup.describe(record, include_log=True)["log_tail"] == ""

    def test_launch_id_pattern_matches_minted_ids(
        self, workspace: Path, clock: list[datetime]
    ) -> None:
        assert LAUNCH_ID_PATTERN.match(_sup(workspace, FAIL_FAST, clock).launch_run().launch_id)


# ---------------------------------------------------------------------------
# Service: preflight + template enum + launch listing
# ---------------------------------------------------------------------------


def _write_reposets(path: Path, names: list[str]) -> Path:
    spec = {
        "version": "1.0",
        "repo_sets": {
            n: {"workspace_root": ".", "repos": [{"id": "r", "path": ".", "role": "primary"}]}
            for n in names
        },
    }
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


def _write_agents(path: Path, names: list[str]) -> Path:
    spec = {"version": "1.0", "agents": {n: {"executor": "fake"} for n in names}}
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


def _workflow(path: Path, repo_set: str) -> Path:
    write_workflow(path)
    data = json.loads(path.read_text())
    data["repo_set"] = repo_set
    path.write_text(json.dumps(data))
    return path


@pytest.fixture()
def configured(workspace: Path) -> ProjectConfig:
    reposets = _write_reposets(workspace / "reposets.json", ["fin-plan"])
    agents = _write_agents(workspace / "agents.json", ["dev"])
    return ProjectConfig(reposets=str(reposets), agents=str(agents))


@pytest.fixture(autouse=True)
def _no_ambient_ao_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AO_REPOSETS", raising=False)
    monkeypatch.delenv("AO_AGENTS", raising=False)


class TestPreflight:
    def test_unknown_repo_set_lists_valid_names_and_does_not_spawn(
        self, workspace: Path, configured: ProjectConfig
    ) -> None:
        stub = StubSupervisor(workspace)
        svc = DashboardService(str(workspace), supervisor=stub, project_config=configured)  # type: ignore[arg-type]
        wf = _workflow(workspace / "wf.json", "ai-models")
        with pytest.raises(DashboardError) as exc:
            svc.start_run(workflow_path=str(wf))
        assert "Unknown repo_set ai-models; available: fin-plan" in str(exc.value)
        assert stub.launch_calls == []

    def test_valid_spec_launches(self, workspace: Path, configured: ProjectConfig) -> None:
        stub = StubSupervisor(workspace)
        svc = DashboardService(str(workspace), supervisor=stub, project_config=configured)  # type: ignore[arg-type]
        wf = _workflow(workspace / "wf.json", "fin-plan")
        assert svc.start_run(workflow_path=str(wf))["status"] == "started"
        assert len(stub.launch_calls) == 1

    def test_unknown_agent_is_rejected_via_engine_cross_validation(
        self, workspace: Path, configured: ProjectConfig
    ) -> None:
        stub = StubSupervisor(workspace)
        svc = DashboardService(str(workspace), supervisor=stub, project_config=configured)  # type: ignore[arg-type]
        _write_agents(workspace / "agents.json", ["someone-else"])
        wf = _workflow(workspace / "wf.json", "fin-plan")
        with pytest.raises(DashboardError, match="unknown agent"):
            svc.start_run(workflow_path=str(wf))
        assert stub.launch_calls == []

    def test_missing_reposets_file_fails_fast(self, workspace: Path) -> None:
        stub = StubSupervisor(workspace)
        cfg = ProjectConfig(reposets=str(workspace / "gone.json"))
        svc = DashboardService(str(workspace), supervisor=stub, project_config=cfg)  # type: ignore[arg-type]
        wf = _workflow(workspace / "wf.json", "fin-plan")
        with pytest.raises(DashboardError, match="File not found"):
            svc.start_run(workflow_path=str(wf))

    def test_no_reposets_configured_defers_to_engine(self, workspace: Path) -> None:
        stub = StubSupervisor(workspace)
        svc = DashboardService(
            str(workspace), supervisor=stub, project_config=ProjectConfig(workflow="x")
        )  # type: ignore[arg-type]
        wf = _workflow(workspace / "wf.json", "whatever")
        svc.start_run(workflow_path=str(wf))
        assert len(stub.launch_calls) == 1

    def test_env_reposets_is_honored(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AO_REPOSETS", str(_write_reposets(workspace / "e.json", ["envset"])))
        stub = StubSupervisor(workspace)
        svc = DashboardService(
            str(workspace), supervisor=stub, project_config=ProjectConfig(workflow="x")
        )  # type: ignore[arg-type]
        wf = _workflow(workspace / "wf.json", "nope")
        with pytest.raises(DashboardError, match="available: envset"):
            svc.start_run(workflow_path=str(wf))

    def test_unloadable_workflow_is_a_precise_error(
        self, workspace: Path, configured: ProjectConfig
    ) -> None:
        stub = StubSupervisor(workspace)
        svc = DashboardService(str(workspace), supervisor=stub, project_config=configured)  # type: ignore[arg-type]
        bad = workspace / "wf.json"
        bad.write_text("{}")
        with pytest.raises(DashboardError, match="cannot launch"):
            svc.start_run(workflow_path=str(bad))


@pytest.fixture(autouse=True)
def _isolate_builtin_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_orchestrator.templates as templates_mod

    monkeypatch.setattr(templates_mod, "_BUILTIN_ROOT", tmp_path / "fake-builtin-root")


class TestTemplateRepoSet:
    def _svc(
        self, workspace: Path, cfg: ProjectConfig, tmpl: Path
    ) -> tuple[DashboardService, StubSupervisor]:
        stub = StubSupervisor(workspace)
        cfg = cfg.model_copy(update={"templates": [str(tmpl)]})
        return DashboardService(str(workspace), supervisor=stub, project_config=cfg), stub  # type: ignore[arg-type]

    def _tmpl(self, workspace: Path) -> Path:
        return write_template(
            workspace / "t",
            extra_params={
                "repo_set": {"description": "rs", "required": True, "default": "ai-models"}
            },
        )

    def test_repo_set_param_becomes_enum_of_valid_names(
        self, workspace: Path, configured: ProjectConfig
    ) -> None:
        svc, _ = self._svc(workspace, configured, self._tmpl(workspace))
        param = next(p for p in svc.list_templates()[0]["params"] if p["name"] == "repo_set")
        assert param["enum"] == ["fin-plan"]
        assert param["default"] is None  # declared default is not a valid name

    def test_valid_default_is_kept(self, workspace: Path, configured: ProjectConfig) -> None:
        _write_reposets(workspace / "reposets.json", ["ai-models", "fin-plan"])
        svc, _ = self._svc(workspace, configured, self._tmpl(workspace))
        param = next(p for p in svc.list_templates()[0]["params"] if p["name"] == "repo_set")
        assert param["enum"] == ["ai-models", "fin-plan"] and param["default"] == "ai-models"

    def test_unreadable_reposets_leaves_free_text(self, workspace: Path) -> None:
        cfg = ProjectConfig(reposets=str(workspace / "missing.json"))
        svc, _ = self._svc(workspace, cfg, self._tmpl(workspace))
        param = next(p for p in svc.list_templates()[0]["params"] if p["name"] == "repo_set")
        assert param["enum"] is None

    def test_bad_repo_set_rejected_before_scaffolding(
        self, workspace: Path, configured: ProjectConfig
    ) -> None:
        svc, stub = self._svc(workspace, configured, self._tmpl(workspace))
        with pytest.raises(DashboardError, match="Unknown repo_set ai-models; available: fin-plan"):
            svc.create_instance(
                "mini", slug_or_id="x1", params={"repo_set": "ai-models"}, start=True
            )
        assert not (workspace / "runs").exists()
        assert stub.launch_calls == []


class TestListLaunches:
    def _svc(
        self, workspace: Path, clock: list[datetime]
    ) -> tuple[DashboardService, ProcessSupervisor]:
        sup = _sup(workspace, FAIL_FAST, clock)
        return DashboardService(str(workspace), supervisor=sup, clock=lambda: clock[0]), sup

    def test_status_and_age_filters(self, workspace: Path, clock: list[datetime]) -> None:
        svc, sup = self._svc(workspace, clock)
        clock[0] = T0 - timedelta(hours=30)
        old = sup.launch_run()
        clock[0] = T0 - timedelta(hours=1)
        recent = sup.launch_run()
        clock[0] = T0
        rows = svc.list_launches(status=STATUS_FAILED_TO_START, since_hours=24)
        assert [r["launch_id"] for r in rows] == [recent.launch_id]
        assert {r["launch_id"] for r in svc.list_launches()} == {old.launch_id, recent.launch_id}
        assert svc.list_launches(status=STATUS_STARTED) == []

    def test_cancelled_launches_are_not_failures(
        self, workspace: Path, clock: list[datetime]
    ) -> None:
        svc, sup = self._svc(workspace, clock)
        record = sup.launch_run()
        path = sup.launches_dir / f"{record.launch_id}.json"
        data = json.loads(path.read_text())
        data["cancelled"] = True
        path.write_text(json.dumps(data))
        assert svc.list_launches(status=STATUS_FAILED_TO_START) == []

    def test_get_unknown_launch_is_not_found(self, workspace: Path, clock: list[datetime]) -> None:
        svc, _ = self._svc(workspace, clock)
        with pytest.raises(DashboardError, match="launch not found"):
            svc.get_launch("launch-20261003T120000000000Z")


# ---------------------------------------------------------------------------
# HTTP-level failure path (real supervisor, real FastAPI app)
# ---------------------------------------------------------------------------


@pytest.fixture()
def http(workspace: Path, configured: ProjectConfig, clock: list[datetime]):
    sup = _sup(workspace, FAIL_FAST, clock, timeout=30.0)
    svc = DashboardService(
        str(workspace), supervisor=sup, project_config=configured, clock=lambda: clock[0]
    )
    with TestClient(create_app(svc)) as client:
        yield client


class TestHttpFailurePath:
    def test_failed_launch_end_to_end(self, workspace: Path, http: TestClient) -> None:
        wf = _workflow(workspace / "wf.json", "fin-plan")
        resp = http.post(f"{API_PREFIX}/runs", json={"workflow_path": str(wf)})
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == STATUS_FAILED_TO_START
        assert body["exit_code"] == 1 and body["run_id"] is None
        assert body["workflow_path"] == str(wf)
        assert "Unknown repo_set: ai-models" in body["log_tail"]

        one = http.get(f"{API_PREFIX}/launches/{body['launch_id']}").json()
        assert one["status"] == STATUS_FAILED_TO_START
        assert "Unknown repo_set: ai-models" in one["log_tail"]

        failed = http.get(
            f"{API_PREFIX}/launches", params={"status": "failed_to_start", "since_hours": 24}
        ).json()
        assert [r["launch_id"] for r in failed] == [body["launch_id"]]
        assert "log_tail" not in failed[0]  # list rows stay small
        # The run list stays empty -- this is exactly why the failure strip exists.
        assert http.get(f"{API_PREFIX}/runs").json() == []

    def test_bad_repo_set_is_a_400_with_valid_names(
        self, workspace: Path, http: TestClient
    ) -> None:
        wf = _workflow(workspace / "wf.json", "ai-models")
        resp = http.post(f"{API_PREFIX}/runs", json={"workflow_path": str(wf)})
        assert resp.status_code == 400
        assert resp.json()["detail"].startswith("Unknown repo_set ai-models; available: fin-plan")
        assert http.get(f"{API_PREFIX}/launches").json() == []  # nothing was spawned

    def test_template_bad_repo_set_is_a_400(
        self, workspace: Path, configured: ProjectConfig, clock: list[datetime]
    ) -> None:
        tmpl = write_template(
            workspace / "t", extra_params={"repo_set": {"description": "rs", "required": True}}
        )
        cfg = configured.model_copy(update={"templates": [str(tmpl)]})
        svc = DashboardService(
            str(workspace), supervisor=_sup(workspace, FAIL_FAST, clock), project_config=cfg
        )
        with TestClient(create_app(svc)) as client:
            resp = client.post(
                f"{API_PREFIX}/templates/mini/instances",
                json={"slug_or_id": "x1", "params": {"repo_set": "ai-models"}, "start": True},
            )
            assert resp.status_code == 400
            assert "available: fin-plan" in resp.json()["detail"]
            listed = client.get(f"{API_PREFIX}/templates").json()
            enum = next(p for p in listed[0]["params"] if p["name"] == "repo_set")["enum"]
            assert enum == ["fin-plan"]

    @pytest.mark.parametrize(
        "launch_id", ["..%2F..%2Fetc%2Fpasswd", "launch-1", "nope", "launch-20261003T120000000000Z"]
    )
    def test_unknown_or_hostile_launch_ids_404(self, http: TestClient, launch_id: str) -> None:
        assert http.get(f"{API_PREFIX}/launches/{launch_id}").status_code == 404

    def test_bad_query_params_are_rejected(self, http: TestClient) -> None:
        assert http.get(f"{API_PREFIX}/launches", params={"since_hours": -1}).status_code == 422
