"""Tests for `GET /api/runs/{run_id}/graph` and its additive `RunDetail` fields
(T-AsQ77e, HLD §8.4/§14.2, ADR-0017 D4).

Covers TASK.md's AC-1 through AC-6: the endpoint is a thin, faithful wrapper around
`ui.graph.build_run_graph` (AC-1); 404s reuse the existing `run_dir` guard, never a new
one (AC-2); every non-"unknown run" failure mode degrades to 200 with `warnings[]`
and never a 5xx (AC-3); `RunDetail.graph_version` and `RunGraph.graph_version` are
equal by construction because both come from the SAME `compute_graph_version(state)`
call on the SAME state (AC-4, reviewer MUST-FIX); the additive fields never disturb a
pre-existing key's value (AC-5); and no instruction/hook-argv/integration-command text
ever reaches the wire (AC-6). AC-7 (fixture-vs-dataclass contract) lives in its own
`test_graph_contract.py`; AC-8 (coverage gate) is reported in STATUS, not asserted here.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi", reason="dashboard API needs the optional [ui] extra")
from fastapi.testclient import TestClient  # noqa: E402

from agent_orchestrator.artifacts import LocalFsArtifactStore  # noqa: E402
from agent_orchestrator.models import (  # noqa: E402
    HookRef,
    HookSpec,
    IntegrationSpec,
    RunState,
    TaskRunState,
    TaskSpec,
    WorkflowSpec,
)
from agent_orchestrator.runstate import RunStateStore, load_workflow_snapshot_at  # noqa: E402
from agent_orchestrator.ui.app import API_PREFIX, create_app  # noqa: E402
from agent_orchestrator.ui.graph import (  # noqa: E402
    GRAPH_SOURCE_SNAPSHOT,
    GRAPH_SOURCE_UNAVAILABLE,
    build_run_graph,
)
from agent_orchestrator.ui.runs import RunNotFoundError, RunRepository  # noqa: E402
from agent_orchestrator.ui.service import DashboardService  # noqa: E402

from .conftest import StubSupervisor, make_run_state, write_run  # noqa: E402

# AC-6 sentinels: distinctive strings that cannot collide with anything the builder
# itself would ever emit (ids, statuses, warning text).
_SECRET_HOOK_ARGV = "zzsentinel-secret-hook-argv --flag"
_SECRET_INSTRUCTION = "instructions/zzsentinel-secret-instruction.md"
_SECRET_VERIFY_CMD = "zzsentinel-secret-verify-cmd"

_RUN_ID = "demo-20260724T100000Z"


def _workflow() -> WorkflowSpec:
    """A minimal, schema-valid two-task static spec: ``b`` explicitly depends on ``a``."""
    return WorkflowSpec(
        version="1.0",
        id="demo",
        repo_set="demo-repos",
        tasks=[
            TaskSpec(id="a", agent="dev", instruction="instructions/a.md"),
            TaskSpec(id="b", agent="dev", instruction="instructions/b.md", depends_on=["a"]),
        ],
    )


def _sensitive_workflow() -> WorkflowSpec:
    """Same shape as `_workflow`, plus a hook, an integration verify command, and a
    distinctive instruction path -- everything AC-6 says must never reach `/graph`."""
    return WorkflowSpec(
        version="1.0",
        id="demo",
        repo_set="demo-repos",
        hooks={"gate": HookSpec(type="command", command=[_SECRET_HOOK_ARGV])},
        integration=IntegrationSpec(verify_command=[_SECRET_VERIFY_CMD]),
        tasks=[
            TaskSpec(
                id="a",
                agent="dev",
                instruction=_SECRET_INSTRUCTION,
                post_hook=HookRef(use="gate"),
            ),
            TaskSpec(id="b", agent="dev", instruction="instructions/b.md", depends_on=["a"]),
        ],
    )


def _tasks_ab() -> dict[str, TaskRunState]:
    return {
        "a": TaskRunState(
            status="succeeded",
            attempts=1,
            started_at="2026-07-24T10:00:00+00:00",
            ended_at="2026-07-24T10:01:00+00:00",
        ),
        "b": TaskRunState(
            status="succeeded",
            attempts=1,
            started_at="2026-07-24T10:01:00+00:00",
            ended_at="2026-07-24T10:02:00+00:00",
        ),
    }


def write_snapshot_backed_run(workspace: Path, state: RunState, workflow: WorkflowSpec) -> Path:
    """Persist *state* through a real `SpecSession` + workflow-snapshot write, in the
    same order the engine uses (`record_spec_session` BEFORE the first `save()`) --
    see T-l7t6TT's `Orchestrator.run` insertion point. Returns the run directory.
    """
    pinned = datetime.fromisoformat(state.updated_at)
    store = RunStateStore(
        str(workspace), LocalFsArtifactStore(str(workspace)), clock=lambda: pinned
    )
    store.record_spec_session(state, workflow)
    store.save(state)
    return workspace / ".orchestrator" / "runs" / state.run_id


def _events(caplog: pytest.LogCaptureFixture, name: str) -> list[logging.LogRecord]:
    """Filter caplog records by the structured `event` extra (mirrors the pattern
    `tests/test_workflow_snapshot.py` already uses for `run.*` events)."""
    return [r for r in caplog.records if getattr(r, "event", None) == name]


@pytest.fixture()
def repo(workspace: Path) -> RunRepository:
    return RunRepository(str(workspace))


@pytest.fixture()
def client(workspace: Path, stub_supervisor: StubSupervisor) -> Iterator[TestClient]:
    service = DashboardService(str(workspace), supervisor=stub_supervisor)  # type: ignore[arg-type]
    with TestClient(create_app(service)) as test_client:
        yield test_client


class TestSnapshotBackedHappyPath:
    """AC-1: a snapshot-backed run's `/graph` is a thin, faithful wrapper."""

    def test_repo_load_graph_equals_build_run_graph_on_the_same_inputs(
        self, workspace: Path, repo: RunRepository
    ) -> None:
        state = make_run_state(run_id=_RUN_ID, tasks=_tasks_ab())
        write_snapshot_backed_run(workspace, state, _workflow())

        graph = repo.load_graph(_RUN_ID)
        assert graph.source == GRAPH_SOURCE_SNAPSHOT
        assert graph.schema_version == 1

        # Rebuild directly from freshly reloaded state + snapshot, exactly as
        # `load_graph` does internally, and compare the WHOLE dataclass (structural
        # equality, not a field-by-field eyeball).
        reloaded = repo.load_state(_RUN_ID)
        sha = reloaded.spec_sessions[-1].spec_sha256
        snapshot = load_workflow_snapshot_at(repo.run_dir(_RUN_ID), sha)
        expected = build_run_graph(reloaded, snapshot)

        assert graph == expected
        assert {n.id for n in graph.nodes} == {n.id for n in expected.nodes}
        assert graph.dependency_edges == expected.dependency_edges
        assert graph.spawn_edges == expected.spawn_edges

    def test_http_graph_response_equals_build_run_graph_on_the_same_inputs(
        self, workspace: Path, client: TestClient
    ) -> None:
        state = make_run_state(run_id=_RUN_ID, tasks=_tasks_ab())
        write_snapshot_backed_run(workspace, state, _workflow())

        resp = client.get(f"{API_PREFIX}/runs/{_RUN_ID}/graph")
        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == GRAPH_SOURCE_SNAPSHOT
        assert body["schema_version"] == 1

        repo = RunRepository(str(workspace))
        reloaded = repo.load_state(_RUN_ID)
        sha = reloaded.spec_sessions[-1].spec_sha256
        snapshot = load_workflow_snapshot_at(repo.run_dir(_RUN_ID), sha)
        expected = asdict(build_run_graph(reloaded, snapshot))

        assert body == expected


class TestNotFound:
    """AC-2: 404s, reusing the existing `run_dir` guard rather than a new one."""

    def test_unknown_run_id_gives_404_with_the_exact_detail_message(
        self, client: TestClient
    ) -> None:
        resp = client.get(f"{API_PREFIX}/runs/no-such-run/graph")
        assert resp.status_code == 404
        assert resp.json() == {"detail": "run not found: no-such-run"}

    def test_unreadable_state_json_gives_404(self, workspace: Path, client: TestClient) -> None:
        bad = workspace / ".orchestrator" / "runs" / "corrupt-run"
        bad.mkdir(parents=True)
        (bad / "state.json").write_text("{not json", encoding="utf-8")

        resp = client.get(f"{API_PREFIX}/runs/corrupt-run/graph")
        assert resp.status_code == 404
        assert resp.json() == {"detail": "run not found: corrupt-run"}

    @pytest.mark.parametrize("evil_id", ["..%2F..%2Fetc", "../x", "..", "../runs"])
    def test_traversal_ids_are_rejected_by_the_existing_run_dir_guard(
        self, workspace: Path, repo: RunRepository, evil_id: str
    ) -> None:
        # Same guard `RunRepository.run_dir` already enforces for `detail`/`delete`
        # (see `tests/ui/test_runs.py::TestDelete::
        # test_run_id_cannot_escape_the_runs_directory`) -- proven directly against
        # the repository rather than through an HTTP client, which normalizes `..`
        # segments out of the URL before the request is even sent.
        with pytest.raises(RunNotFoundError):
            repo.load_graph(evil_id)

    @pytest.mark.parametrize("evil_id", ["..%2F..%2Fetc", "../x"])
    def test_traversal_ids_via_the_service_layer_map_to_dashboard_error(
        self, workspace: Path, stub_supervisor: StubSupervisor, evil_id: str
    ) -> None:
        from agent_orchestrator.ui.service import DashboardError

        service = DashboardService(str(workspace), supervisor=stub_supervisor)  # type: ignore[arg-type]
        with pytest.raises(DashboardError):
            service.run_graph(evil_id)


class TestDegraded:
    """AC-3: every case short of "run not found" is 200 with `source="unavailable"`
    and non-empty `warnings`, never a 5xx; `ui.graph.degraded` fires for every one of
    them except the pre-epic case (which predates snapshots entirely -- nothing to be
    "missing or invalid").
    """

    def test_pre_epic_run_has_no_spec_sessions(
        self, workspace: Path, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        write_run(workspace, make_run_state(run_id=_RUN_ID, tasks=_tasks_ab()))

        with caplog.at_level(logging.WARNING, logger="agent_orchestrator"):
            resp = client.get(f"{API_PREFIX}/runs/{_RUN_ID}/graph")

        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == GRAPH_SOURCE_UNAVAILABLE
        assert body["warnings"]
        assert _events(caplog, "ui.graph.degraded") == []

    def test_snapshot_file_deleted(
        self, workspace: Path, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        state = make_run_state(run_id=_RUN_ID, tasks=_tasks_ab())
        run_dir = write_snapshot_backed_run(workspace, state, _workflow())
        snapshot_files = list(run_dir.glob("workflow.snapshot.*.json"))
        assert len(snapshot_files) == 1
        snapshot_files[0].unlink()

        with caplog.at_level(logging.WARNING, logger="agent_orchestrator"):
            resp = client.get(f"{API_PREFIX}/runs/{_RUN_ID}/graph")

        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == GRAPH_SOURCE_UNAVAILABLE
        assert body["warnings"]
        assert len(_events(caplog, "ui.graph.degraded")) == 1

    def test_snapshot_over_the_size_cap(
        self,
        workspace: Path,
        client: TestClient,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        state = make_run_state(run_id=_RUN_ID, tasks=_tasks_ab())
        write_snapshot_backed_run(workspace, state, _workflow())
        monkeypatch.setattr("agent_orchestrator.runstate.WORKFLOW_SNAPSHOT_MAX_BYTES", 4)

        with caplog.at_level(logging.WARNING, logger="agent_orchestrator"):
            resp = client.get(f"{API_PREFIX}/runs/{_RUN_ID}/graph")

        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == GRAPH_SOURCE_UNAVAILABLE
        assert body["warnings"]
        assert len(_events(caplog, "ui.graph.degraded")) == 1

    def test_snapshot_with_mismatched_sha(
        self, workspace: Path, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        state = make_run_state(run_id=_RUN_ID, tasks=_tasks_ab())
        run_dir = write_snapshot_backed_run(workspace, state, _workflow())
        snapshot_files = list(run_dir.glob("workflow.snapshot.*.json"))
        assert len(snapshot_files) == 1
        path = snapshot_files[0]
        body_json = json.loads(path.read_text(encoding="utf-8"))
        body_json["spec_sha256"] = "0" * 64  # well-formed hex, but wrong -- name/body mismatch
        path.write_text(json.dumps(body_json), encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger="agent_orchestrator"):
            resp = client.get(f"{API_PREFIX}/runs/{_RUN_ID}/graph")

        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == GRAPH_SOURCE_UNAVAILABLE
        assert body["warnings"]
        assert len(_events(caplog, "ui.graph.degraded")) == 1

    def test_invalid_snapshot_json(
        self, workspace: Path, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        state = make_run_state(run_id=_RUN_ID, tasks=_tasks_ab())
        run_dir = write_snapshot_backed_run(workspace, state, _workflow())
        snapshot_files = list(run_dir.glob("workflow.snapshot.*.json"))
        assert len(snapshot_files) == 1
        snapshot_files[0].write_text("{not valid json", encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger="agent_orchestrator"):
            resp = client.get(f"{API_PREFIX}/runs/{_RUN_ID}/graph")

        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == GRAPH_SOURCE_UNAVAILABLE
        assert body["warnings"]
        assert len(_events(caplog, "ui.graph.degraded")) == 1


class TestVersionEquality:
    """AC-4 (reviewer MUST-FIX): `GET /runs/{id}`'s `graph_version` equals
    `GET /runs/{id}/graph`'s, for every run kind in AC-1 and AC-3, because both are
    `compute_graph_version(state)` on the SAME loaded state -- never two independent
    derivations."""

    def test_snapshot_backed_run(self, workspace: Path, client: TestClient) -> None:
        state = make_run_state(run_id=_RUN_ID, tasks=_tasks_ab())
        write_snapshot_backed_run(workspace, state, _workflow())
        self._assert_versions_match(client, _RUN_ID)

    def test_pre_epic_run(self, workspace: Path, client: TestClient) -> None:
        write_run(workspace, make_run_state(run_id=_RUN_ID, tasks=_tasks_ab()))
        self._assert_versions_match(client, _RUN_ID)

    def test_snapshot_file_deleted(self, workspace: Path, client: TestClient) -> None:
        state = make_run_state(run_id=_RUN_ID, tasks=_tasks_ab())
        run_dir = write_snapshot_backed_run(workspace, state, _workflow())
        for f in run_dir.glob("workflow.snapshot.*.json"):
            f.unlink()
        self._assert_versions_match(client, _RUN_ID)

    def test_sha_mismatched_snapshot(self, workspace: Path, client: TestClient) -> None:
        state = make_run_state(run_id=_RUN_ID, tasks=_tasks_ab())
        run_dir = write_snapshot_backed_run(workspace, state, _workflow())
        path = next(run_dir.glob("workflow.snapshot.*.json"))
        body_json = json.loads(path.read_text(encoding="utf-8"))
        body_json["spec_sha256"] = "1" * 64
        path.write_text(json.dumps(body_json), encoding="utf-8")
        self._assert_versions_match(client, _RUN_ID)

    def test_invalid_snapshot_json(self, workspace: Path, client: TestClient) -> None:
        state = make_run_state(run_id=_RUN_ID, tasks=_tasks_ab())
        run_dir = write_snapshot_backed_run(workspace, state, _workflow())
        next(run_dir.glob("workflow.snapshot.*.json")).write_text("nope", encoding="utf-8")
        self._assert_versions_match(client, _RUN_ID)

    @staticmethod
    def _assert_versions_match(client: TestClient, run_id: str) -> None:
        detail = client.get(f"{API_PREFIX}/runs/{run_id}").json()
        graph = client.get(f"{API_PREFIX}/runs/{run_id}/graph").json()
        assert detail["graph_version"]  # never empty/None on the new backend
        assert detail["graph_version"] == graph["graph_version"]


class TestAdditiveOnly:
    """AC-5: every pre-existing `RunDetail`/`TaskStat` key keeps its exact value; only
    `graph_version`, `tasks[].dispatch_cycle`, and `tasks[].not_taken_reason` are new.
    """

    # The full field set BEFORE this task (T-M4qboy's STATUS.md / this task's own
    # TASK.md both predate any of these three fields) -- hardcoded here as the "before"
    # snapshot so a diff against it is exact, not an eyeball.
    _PRE_EXISTING_RUN_DETAIL_KEYS = frozenset(
        {
            "summary",
            "tasks",
            "tripped_breakers",
            "route_decisions",
            "monitor_decisions",
            "run_dir",
            "integration",
        }
    )
    _PRE_EXISTING_TASK_STAT_KEYS = frozenset(
        {
            "id",
            "status",
            "attempts",
            "started_at",
            "ended_at",
            "duration_seconds",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "origin",
            "route",
            "output_artifact_path",
            "outputs",
            "integration_status",
            "tier_reached",
            "conflicted_count",
            "cache_read_tokens",
            "cache_creation_tokens",
            "cache_hit_rate",
        }
    )

    def test_only_the_three_documented_keys_are_new(
        self, workspace: Path, repo: RunRepository
    ) -> None:
        write_run(workspace, make_run_state())  # conftest's own default fixture
        payload = asdict(repo.detail("demo-20260724T100000Z"))

        # graph_version is this epic's; prompt/prompt_changed_since_start are E-Us9Kd4 FR-13's.
        assert set(payload.keys()) - self._PRE_EXISTING_RUN_DETAIL_KEYS == {
            "graph_version",
            "prompt",
            "prompt_changed_since_start",
        }
        assert self._PRE_EXISTING_RUN_DETAIL_KEYS <= set(payload.keys())

        build_task = next(t for t in payload["tasks"] if t["id"] == "build")
        new_task_keys = set(build_task.keys()) - self._PRE_EXISTING_TASK_STAT_KEYS
        # agent/model/effort are E-iafh2F (live activity) additions.
        assert new_task_keys == {"dispatch_cycle", "not_taken_reason", "agent", "model", "effort"}
        assert self._PRE_EXISTING_TASK_STAT_KEYS <= set(build_task.keys())

    def test_every_pre_existing_value_is_byte_identical_to_before(
        self, workspace: Path, repo: RunRepository
    ) -> None:
        # Same fixture `tests/ui/test_runs.py::TestDetail` already pins these exact
        # numbers against -- if this task's change had touched any of them, one of
        # these assertions (not just a key-presence check) would fail.
        write_run(workspace, make_run_state())
        payload = asdict(repo.detail("demo-20260724T100000Z"))

        assert payload["run_dir"] == str(repo.run_dir("demo-20260724T100000Z"))
        assert payload["tripped_breakers"] == []
        assert payload["route_decisions"] == {}
        assert payload["monitor_decisions"] == []
        assert payload["integration"] is None
        assert payload["summary"]["run_id"] == "demo-20260724T100000Z"
        assert payload["summary"]["cost_usd"] == pytest.approx(0.75)
        assert payload["summary"]["input_tokens"] == 1500
        assert payload["summary"]["output_tokens"] == 375

        build_task = next(t for t in payload["tasks"] if t["id"] == "build")
        assert build_task["status"] == "succeeded"
        assert build_task["attempts"] == 1
        assert build_task["duration_seconds"] == pytest.approx(120)
        assert build_task["input_tokens"] == 1000
        assert build_task["output_tokens"] == 250
        assert build_task["cost_usd"] == pytest.approx(0.5)
        assert build_task["origin"] == "static"
        assert build_task["route"] is None
        assert build_task["output_artifact_path"] is None
        assert build_task["outputs"] == []
        assert build_task["integration_status"] is None
        assert build_task["tier_reached"] is None
        assert build_task["conflicted_count"] == 0
        assert build_task["cache_read_tokens"] == 0
        assert build_task["cache_creation_tokens"] == 0
        # 1000 input tokens with zero cache tokens -> hit rate 0.0 (a real rate, distinct
        # from the zero-denominator `None` case `reporting.cache_effectiveness` documents).
        assert build_task["cache_hit_rate"] == pytest.approx(0.0)

        # The new fields default sanely for a fixture that never touched them.
        assert build_task["dispatch_cycle"] == 0
        assert build_task["not_taken_reason"] is None
        assert payload["graph_version"]  # always computed on the new backend


class TestNoLeakage:
    """AC-6 (security-relevant): hook argv, task instructions, and integration
    commands must never appear anywhere in the serialized `/graph` response."""

    def test_sensitive_strings_never_reach_the_graph_response(
        self, workspace: Path, repo: RunRepository
    ) -> None:
        state = make_run_state(run_id=_RUN_ID, tasks=_tasks_ab())
        write_snapshot_backed_run(workspace, state, _sensitive_workflow())

        graph = repo.load_graph(_RUN_ID)
        serialized = json.dumps(asdict(graph))

        for sentinel in (_SECRET_HOOK_ARGV, _SECRET_INSTRUCTION, _SECRET_VERIFY_CMD):
            assert sentinel not in serialized

    def test_sensitive_strings_never_reach_the_http_response(
        self, workspace: Path, client: TestClient
    ) -> None:
        state = make_run_state(run_id=_RUN_ID, tasks=_tasks_ab())
        write_snapshot_backed_run(workspace, state, _sensitive_workflow())

        resp = client.get(f"{API_PREFIX}/runs/{_RUN_ID}/graph")
        assert resp.status_code == 200
        serialized = resp.text

        for sentinel in (_SECRET_HOOK_ARGV, _SECRET_INSTRUCTION, _SECRET_VERIFY_CMD):
            assert sentinel not in serialized
