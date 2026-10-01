"""Tests for T-AZzgT8-spawn-provenance (E-k3AMEr, ADR-0017 D1, HLD §8.1).

Covers TASK.md acceptance criteria 3-12 (AC-1/AC-2 -- the model shape and the
keyword-only, required ``parent_task_id`` -- are verified directly against the model /
via a manual mypy check recorded in STATUS.md, not here).

Layout mirrors the existing `test_dynamic_injection.py` / `test_loop_construct.py`
conventions: unit-level tests call `Orchestrator._inject` directly where that gives the
most precise assertion (AC-8); everything else runs the full engine via the shared
`make_workflow`/`make_orchestrator` fixtures (or a local clock-injecting variant, when a
test needs a deterministic ``injected_at``).
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.errors import InjectionError
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import (
    SPAWN_ORIGIN_INJECTED,
    SPAWN_ORIGIN_LOOP,
    AgentSpec,
    RepoRef,
    RepoSet,
    RunState,
    TaskRunState,
    TaskSpec,
    WorkflowSpec,
)
from agent_orchestrator.runstate import RunStateStore

_FIXED_DT = datetime(2026, 1, 1, tzinfo=UTC)


def _assert_spawn_invariant(state: RunState) -> None:
    """AC-6: every injected task id has a matching, origin-consistent spawned_by record.

    Holds for a fresh run and (per AC-6) for a failed-then-resumed run -- callers apply
    this to both.
    """
    for spec in state.injected_tasks:
        assert spec.id in state.spawned_by, f"no spawned_by record for injected id {spec.id!r}"
        assert state.spawned_by[spec.id].origin == state.tasks[spec.id].origin, (
            f"spawned_by/{spec.id}.origin != tasks/{spec.id}.origin"
        )


def _make_orch(
    executor: FakeExecutor,
    store: LocalFsArtifactStore,
    rs_store: RunStateStore,
    workspace: Path,
    clock=None,
    max_parallel: int = 1,
) -> tuple[Orchestrator, dict, dict]:
    """Local orchestrator factory that (unlike the shared `make_orchestrator` fixture)
    accepts an explicit `clock`, needed for AC-3's deterministic `injected_at` assertion.
    """
    orch = Orchestrator(executor, store, rs_store, clock=clock, max_parallel=max_parallel)
    reposets = {
        "rs": RepoSet(
            workspace_root=str(workspace),
            repos=[RepoRef(id="core", path=".", role="primary")],
        )
    }
    agents: dict = {"ag": AgentSpec(executor="fake")}
    return orch, reposets, agents


# ---------------------------------------------------------------------------
# AC-3: emit -- a flat batch of injected tasks records parent/origin/timestamp
# ---------------------------------------------------------------------------


class TestEmitProvenance:
    def test_emit_batch_records_spawned_by_for_every_child(
        self,
        make_workflow,
        store: LocalFsArtifactStore,
        rs_store: RunStateStore,
        workspace: Path,
        fixed_clock,
    ) -> None:
        manifest_path = "output/manifest.json"
        wf = make_workflow(
            [
                {
                    "id": "a",
                    "emit_tasks": True,
                    "task_manifest_path": manifest_path,
                    "skip_if_outputs_exist": False,
                }
            ],
            wf_id="ac3-emit-wf",
        )
        emitted = [
            {"id": tid, "agent": "ag", "instruction": "specs/instructions/a.md"}
            for tid in ("d", "e", "f")
        ]
        executor = FakeExecutor(emit_payloads={"a": {"tasks": emitted}})
        orch, reposets, agents = _make_orch(executor, store, rs_store, workspace, clock=fixed_clock)
        state = orch.run(wf, reposets, agents)

        assert state.status == "succeeded"
        a_cycle = state.tasks["a"].dispatch_cycle
        fixed_iso = fixed_clock().isoformat()
        for tid in ("d", "e", "f"):
            rec = state.spawned_by[tid]
            assert rec.parent_task_id == "a"
            assert rec.parent_dispatch_cycle == a_cycle
            assert rec.origin == SPAWN_ORIGIN_INJECTED
            assert rec.injected_at == fixed_iso
            assert rec.loop_id is None
            assert rec.iteration is None

        _assert_spawn_invariant(state)

    def test_emitter_emits_zero_tasks_no_records(
        self,
        make_workflow,
        make_orchestrator,
    ) -> None:
        """Edge case (HLD §8.1 table): an emitter with an empty manifest adds no records."""
        manifest_path = "output/empty-manifest.json"
        wf = make_workflow(
            [{"id": "a", "emit_tasks": True, "task_manifest_path": manifest_path}],
            wf_id="ac3-empty-emit-wf",
        )
        executor = FakeExecutor(emit_payloads={"a": {"tasks": []}})
        orch, reposets, agents = make_orchestrator(executor)
        state = orch.run(wf, reposets, agents)

        assert state.status == "succeeded"
        assert state.spawned_by == {}
        assert state.injected_tasks == []


# ---------------------------------------------------------------------------
# AC-4: nested emit -- an injected task that itself emits records its own parent chain
# ---------------------------------------------------------------------------


class TestNestedEmitProvenance:
    def test_nested_emit_parent_chain(
        self,
        make_workflow,
        make_orchestrator,
        workspace: Path,
    ) -> None:
        """a (static, emit_tasks) emits b (emit_tasks); b emits c."""
        manifest_a = "output/manifest-a.json"
        manifest_b = "output/manifest-b.json"

        wf = make_workflow(
            [
                {
                    "id": "a",
                    "emit_tasks": True,
                    "task_manifest_path": manifest_a,
                    "skip_if_outputs_exist": False,
                }
            ],
            wf_id="ac4-nested-emit-wf",
        )
        b_spec = {
            "id": "b",
            "agent": "ag",
            "instruction": "specs/instructions/a.md",
            "emit_tasks": True,
            "task_manifest_path": manifest_b,
            "skip_if_outputs_exist": False,
            "depends_on": ["a"],
        }
        c_spec = {
            "id": "c",
            "agent": "ag",
            "instruction": "specs/instructions/a.md",
            "depends_on": ["b"],
        }
        executor = FakeExecutor(emit_payloads={"a": {"tasks": [b_spec]}, "b": {"tasks": [c_spec]}})
        orch, reposets, agents = make_orchestrator(executor)
        state = orch.run(wf, reposets, agents)

        assert state.status == "succeeded"
        assert state.spawned_by["b"].parent_task_id == "a"
        assert state.spawned_by["b"].origin == SPAWN_ORIGIN_INJECTED
        assert state.spawned_by["c"].parent_task_id == "b"
        assert state.spawned_by["c"].origin == SPAWN_ORIGIN_INJECTED
        assert "a" not in state.spawned_by  # static task -- never injected, no record

        _assert_spawn_invariant(state)


# ---------------------------------------------------------------------------
# AC-5: loop -- clones record the CURRENT iteration's gate task as parent
# ---------------------------------------------------------------------------


class TestLoopProvenance:
    def test_loop_clones_record_gate_as_parent_per_iteration(
        self,
        make_workflow,
        make_orchestrator,
    ) -> None:
        """Loop L, body [dev, gate], 3 iterations (gate: True, True, False)."""
        gate_path = "output/gate-verdict.json"
        wf = make_workflow(
            [
                {"id": "dev", "outputs": ["output/dev.md"]},
                {"id": "gate", "inputs": ["output/dev.md"], "depends_on": ["dev"]},
            ],
            wf_id="ac5-loop-wf",
            loops=[
                {
                    "id": "L",
                    "body": ["dev", "gate"],
                    "gate_task_id": "gate",
                    "gate_output_path": gate_path,
                    "max_iterations": 5,
                }
            ],
        )
        executor = FakeExecutor(gate_payloads={"gate": [True, True, False]})
        orch, reposets, agents = make_orchestrator(executor)
        state = orch.run(wf, reposets, agents)

        assert state.status == "succeeded"
        assert "dev__iter3" in state.tasks  # sanity: 3 iterations materialized

        for tid in ("dev__iter2", "gate__iter2"):
            rec = state.spawned_by[tid]
            assert rec.parent_task_id == "gate"
            assert rec.origin == SPAWN_ORIGIN_LOOP
            assert rec.loop_id == "L"
            assert rec.iteration == 2

        for tid in ("dev__iter3", "gate__iter3"):
            rec = state.spawned_by[tid]
            assert rec.parent_task_id == "gate__iter2"
            assert rec.origin == SPAWN_ORIGIN_LOOP
            assert rec.loop_id == "L"
            assert rec.iteration == 3

        # Iteration-1 body tasks are the authored body, never injected -- no record.
        assert "dev" not in state.spawned_by
        assert "gate" not in state.spawned_by

        _assert_spawn_invariant(state)


# ---------------------------------------------------------------------------
# AC-7: resume preserves spawned_by AND TaskRunState.origin, across all three reset sites
# ---------------------------------------------------------------------------


class TestResumePreservesProvenance:
    def test_resume_after_injected_task_failure_preserves_spawned_by_and_origin(
        self,
        make_workflow,
        make_orchestrator,
        workspace: Path,
        rs_store: RunStateStore,
    ) -> None:
        """runstate.py::prepare_resume's wholesale TaskRunState replace (~line 278)."""
        manifest_path = "output/manifest.json"
        wf = make_workflow(
            [
                {
                    "id": "a",
                    "emit_tasks": True,
                    "task_manifest_path": manifest_path,
                    "outputs": ["output/a.txt"],
                }
            ],
            wf_id="ac7-resume-wf",
        )
        emitted = {
            "id": "e",
            "agent": "ag",
            "instruction": "specs/instructions/a.md",
            "outputs": ["output/e.txt"],
            "depends_on": ["a"],
        }
        executor1 = FakeExecutor(emit_payloads={"a": {"tasks": [emitted]}}, behaviors={"e": "fail"})
        orch1, reposets, agents = make_orchestrator(executor1)
        state1 = orch1.run(wf, reposets, agents)

        assert state1.status == "failed"
        assert state1.tasks["e"].status == "failed"
        assert state1.tasks["e"].origin == SPAWN_ORIGIN_INJECTED
        spawned_by_before = dict(state1.spawned_by)
        run_id = state1.run_id

        loaded = rs_store.load(run_id)
        prepared = rs_store.prepare_resume(loaded, wf)
        # The wholesale replace happened (status went failed -> pending); origin survived it
        # (previously silently reset to the TaskRunState default "static").
        assert prepared.tasks["e"].status == "pending"
        assert prepared.tasks["e"].origin == SPAWN_ORIGIN_INJECTED
        assert prepared.spawned_by == spawned_by_before

        executor2 = FakeExecutor()
        orch2, _, _ = make_orchestrator(executor2)
        state2 = orch2.run(wf, reposets, agents, run_state=prepared)

        assert state2.status == "succeeded"
        assert state2.tasks["e"].status == "succeeded"
        # spawned_by is RunState-level and resume never re-injects an already-injected id
        # (HLD §8.1 "Idempotency and versioning") -- byte-identical to before resume.
        assert state2.spawned_by == spawned_by_before

        _assert_spawn_invariant(state2)

    def test_missing_inputs_reset_preserves_origin(
        self,
        make_workflow,
        make_orchestrator,
        workspace: Path,
    ) -> None:
        """engine.py's missing-inputs reset (~line 1112): an injected task whose declared
        input never materializes fails there, and must keep origin='injected' rather than
        the TaskRunState default 'static'.
        """
        manifest_path = "output/manifest.json"
        wf = make_workflow(
            [{"id": "a", "emit_tasks": True, "task_manifest_path": manifest_path}],
            wf_id="ac7-missing-inputs-wf",
        )
        emitted = {
            "id": "orphan",
            "agent": "ag",
            "instruction": "specs/instructions/a.md",
            # Declares an input no task ever produces -> fails the missing-inputs check.
            "inputs": ["output/never-written.txt"],
        }
        executor = FakeExecutor(emit_payloads={"a": {"tasks": [emitted]}})
        orch, reposets, agents = make_orchestrator(executor)
        state = orch.run(wf, reposets, agents)

        assert state.status == "failed"
        assert state.tasks["orphan"].status == "failed"
        assert state.tasks["orphan"].origin == SPAWN_ORIGIN_INJECTED
        assert state.spawned_by["orphan"].origin == SPAWN_ORIGIN_INJECTED


# ---------------------------------------------------------------------------
# AC-7 (continued): the worktree-collision reset site (engine.py ~1051)
#
# Small local copy of test_engine_isolation.py's real-git-repo helpers (that file's own
# fixtures are scoped to its own module) -- same technique, applied to an INJECTED task
# so the collision reset carries origin="injected" forward.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_git_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AO_STATE_DIR", str(tmp_path / "ao-state"))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    monkeypatch.delenv("GIT_CONFIG_SYSTEM", raising=False)


def _git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(["init", "-q", "-b", "main"], repo)
    _git(["config", "user.name", "ao-test"], repo)
    _git(["config", "user.email", "ao-test@example.invalid"], repo)
    (repo / "README.md").write_text("hi\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "base"], repo)
    return repo


class TestWorktreeCollisionResetPreservesOrigin:
    def test_injected_task_worktree_collision_preserves_origin(self, tmp_path: Path) -> None:
        from agent_orchestrator.isolation.paths import worktree_root
        from agent_orchestrator.isolation.worktrees import group_repos
        from agent_orchestrator.models import IntegrationSpec, WorkflowDefaults

        (tmp_path / "specs" / "instructions").mkdir(parents=True, exist_ok=True)
        (tmp_path / "specs" / "instructions" / "design.md").write_text("# Do it\n")
        (tmp_path / "out").mkdir()
        repo = _git_repo(tmp_path)

        emitter = TaskSpec(
            id="emitter",
            agent="ag",
            instruction="specs/instructions/design.md",
            emit_tasks=True,
            task_manifest_path="out/manifest.json",
            outputs=[],
        )
        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            defaults=WorkflowDefaults(isolation="none"),
            tasks=[emitter],
            # "llm" is in IntegrationSpec's own DEFAULT_LADDER but needs resolver_agent set
            # (V2) -- irrelevant to this test, so drop it (mirrors
            # test_engine_isolation.py::TestValidateIsolationAtInjection's own workaround).
            integration=IntegrationSpec(ladder=["auto", "mechanical"]),
        )

        injected_payload = {
            "tasks": [
                {
                    "id": "injected_iso",
                    "agent": "ag",
                    "instruction": "specs/instructions/design.md",
                    "isolation": "worktree",
                }
            ]
        }
        fake = FakeExecutor(emit_payloads={"emitter": injected_payload})

        store = LocalFsArtifactStore(str(tmp_path))
        rs_store = RunStateStore(str(tmp_path), store, clock=lambda: _FIXED_DT)
        orch = Orchestrator(fake, store, rs_store)

        # Pre-create a worktree at the EXACT path WorktreeManager.ensure() will target for
        # the INJECTED task "injected_iso", on a different branch -- forces the "different
        # worktree already occupies our expected path" hard error (same technique as
        # test_engine_isolation.py::TestWorktreeCollision).
        run_id = f"wf-{_FIXED_DT.strftime('%Y%m%dT%H%M%SZ')}"
        groups, _skipped = group_repos({"core": str(repo)})
        key = groups[0].key
        wt_path = worktree_root(str(tmp_path), run_id, "injected_iso", key)
        _git(["worktree", "add", "-b", "unrelated-branch", str(wt_path)], repo)

        reposets = {
            "rs": RepoSet(
                workspace_root=str(tmp_path),
                repos=[RepoRef(id="core", path="repo", role="primary")],
            )
        }
        agents: dict = {"ag": AgentSpec(executor="fake")}
        state = orch.run(wf, reposets, agents)

        assert state.status == "failed"
        assert state.tasks["injected_iso"].status == "failed"
        # origin carried forward across the ~line 1051 wholesale replace -- previously reset
        # to the TaskRunState default "static".
        assert state.tasks["injected_iso"].origin == SPAWN_ORIGIN_INJECTED
        assert state.spawned_by["injected_iso"].origin == SPAWN_ORIGIN_INJECTED


# ---------------------------------------------------------------------------
# AC-8: duplicate id in an emit batch -- InjectionError, no spawned_by for the collider
# ---------------------------------------------------------------------------


class TestDuplicateIdProvenance:
    def test_duplicate_id_in_batch_raises_and_leaves_no_record_for_the_collider(
        self, tmp_path: Path
    ) -> None:
        """Batch [x, y, x_dup] where x_dup.id == x.id: x and y are recorded (today's
        partial-injection behavior, AC-8), then InjectionError fires on x_dup before its
        own spawned_by write -- the existing "x" record (from the first x) is untouched.
        """
        store = LocalFsArtifactStore(str(tmp_path))
        rs_store = RunStateStore(str(tmp_path), store, clock=lambda: _FIXED_DT)
        orch = Orchestrator(FakeExecutor(), store, rs_store)

        emitter = TaskSpec(id="emitter", agent="ag", instruction="i.md")
        workflow = WorkflowSpec(version="1.0", id="wf", repo_set="rs", tasks=[emitter])
        state = RunState(
            run_id="r1",
            workflow_id="wf",
            repo_set="rs",
            started_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            tasks={"emitter": TaskRunState(status="succeeded", dispatch_cycle=1)},
        )

        x = TaskSpec(id="x", agent="ag", instruction="i.md")
        y = TaskSpec(id="y", agent="ag", instruction="i.md")
        x_dup = TaskSpec(id="x", agent="ag", instruction="i.md")  # same id as x

        with pytest.raises(InjectionError):
            orch._inject(
                [x, y, x_dup],
                workflow,
                state,
                origin=SPAWN_ORIGIN_INJECTED,
                parent_task_id="emitter",
            )

        # x and y were injected before the collision was hit.
        assert {t.id for t in state.injected_tasks} == {"x", "y"}
        assert set(state.spawned_by.keys()) == {"x", "y"}
        assert state.spawned_by["x"].parent_task_id == "emitter"
        assert state.spawned_by["y"].parent_task_id == "emitter"
        # workflow.tasks holds exactly one "x" (from the first occurrence) -- x_dup never
        # got appended.
        assert [t.id for t in workflow.tasks].count("x") == 1


# ---------------------------------------------------------------------------
# AC-9: max_parallel=4 produces the same spawned_by as serial (settle is main-thread only)
# ---------------------------------------------------------------------------


class TestMaxParallelProvenance:
    def test_max_parallel_4_produces_same_spawned_by_as_serial(
        self,
        make_workflow,
        store: LocalFsArtifactStore,
        rs_store: RunStateStore,
        workspace: Path,
        fixed_clock,
    ) -> None:
        manifest_path = "output/manifest.json"
        emitted = [
            {"id": tid, "agent": "ag", "instruction": "specs/instructions/a.md"}
            for tid in ("d", "e", "f")
        ]

        def _run(max_parallel: int, wf_id: str) -> RunState:
            wf = make_workflow(
                [
                    {
                        "id": "a",
                        "emit_tasks": True,
                        "task_manifest_path": manifest_path,
                        "skip_if_outputs_exist": False,
                    }
                ],
                wf_id=wf_id,
            )
            executor = FakeExecutor(emit_payloads={"a": {"tasks": emitted}})
            orch, reposets, agents = _make_orch(
                executor, store, rs_store, workspace, clock=fixed_clock, max_parallel=max_parallel
            )
            return orch.run(wf, reposets, agents)

        state_serial = _run(1, "ac9-serial-wf")
        state_parallel = _run(4, "ac9-parallel-wf")

        assert state_serial.status == "succeeded"
        assert state_parallel.status == "succeeded"
        assert state_serial.spawned_by == state_parallel.spawned_by


# ---------------------------------------------------------------------------
# AC-10: backward/forward compatibility of state.json
# ---------------------------------------------------------------------------


class _PreEpicRunStateShape(BaseModel):
    """Stand-in for RunState's shape before `spawned_by` existed -- proves an OLDER reader
    tolerates the new field via pydantic v2's default `extra="ignore"` (no `model_config`
    override anywhere in models.py, per AC-10's own note)."""

    run_id: str
    workflow_id: str
    repo_set: str
    started_at: str
    updated_at: str


class TestStateJsonCompatibility:
    def test_pre_epic_state_json_loads_with_empty_spawned_by(self) -> None:
        pre_epic_json = json.dumps(
            {
                "run_id": "wf-20260101T000000Z",
                "workflow_id": "wf",
                "repo_set": "rs",
                "started_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        )
        state = RunState.model_validate_json(pre_epic_json)
        assert state.spawned_by == {}

    def test_new_state_json_loads_into_pre_epic_shape_without_error(
        self,
        make_workflow,
        make_orchestrator,
    ) -> None:
        wf = make_workflow(
            [{"id": "a", "emit_tasks": True, "task_manifest_path": "output/m.json"}],
            wf_id="ac10-fwd-compat-wf",
        )
        executor = FakeExecutor(
            emit_payloads={
                "a": {
                    "tasks": [{"id": "d", "agent": "ag", "instruction": "specs/instructions/a.md"}]
                }
            }
        )
        orch, reposets, agents = make_orchestrator(executor)
        state = orch.run(wf, reposets, agents)
        assert state.spawned_by  # non-empty: proves this round-trip actually exercises it

        raw = state.model_dump_json()
        loaded = _PreEpicRunStateShape.model_validate_json(raw)  # must not raise
        assert loaded.run_id == state.run_id

    def test_spawn_record_with_unknown_origin_value_loads(self) -> None:
        raw = json.dumps(
            {
                "run_id": "r",
                "workflow_id": "wf",
                "repo_set": "rs",
                "started_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "spawned_by": {
                    "x": {
                        "parent_task_id": "p",
                        "parent_dispatch_cycle": 1,
                        "origin": "future-kind",
                        "injected_at": "2026-01-01T00:00:00+00:00",
                    }
                },
            }
        )
        state = RunState.model_validate_json(raw)
        assert state.spawned_by["x"].origin == "future-kind"
        assert state.spawned_by["x"].loop_id is None
        assert state.spawned_by["x"].iteration is None


# ---------------------------------------------------------------------------
# AC-11: no behavior change -- carry-forward writes are not behavioral readers
# ---------------------------------------------------------------------------


class TestNoBehaviorChange:
    def test_origin_has_no_behavioral_reader_outside_ui_runs(self) -> None:
        """A-6 re-verified (AC-11): no code branches control flow on
        TaskRunState.origin's VALUE outside ui/runs.py. The carry-forward assignments this
        ticket adds (`origin=ts.origin`, `origin=ts_pre.origin`,
        `origin=prev.origin if prev else "static"`) are plain copies, never comparisons.
        """
        root = Path(__file__).parent.parent / "src" / "agent_orchestrator"
        behavioral_pattern = re.compile(r"\.origin\s*(==|!=|\bin\b)")
        offenders = []
        for f in root.rglob("*.py"):
            if f == root / "ui" / "runs.py":
                continue
            for lineno, line in enumerate(f.read_text().splitlines(), start=1):
                if behavioral_pattern.search(line):
                    offenders.append(f"{f}:{lineno}: {line.strip()}")
        assert not offenders, (
            f"Found behavioral .origin comparisons outside ui/runs.py: {offenders}"
        )


# ---------------------------------------------------------------------------
# AC-12: status.json shape unchanged (snapshot-compare)
# ---------------------------------------------------------------------------


class TestStatusJsonUnchanged:
    def test_status_json_shape_unchanged_for_emit_run(
        self,
        make_workflow,
        make_orchestrator,
        workspace: Path,
        read_status,
    ) -> None:
        wf = make_workflow(
            [
                {
                    "id": "a",
                    "emit_tasks": True,
                    "task_manifest_path": "output/m.json",
                    "skip_if_outputs_exist": False,
                }
            ],
            wf_id="ac12-status-shape-wf",
        )
        executor = FakeExecutor(
            emit_payloads={
                "a": {
                    "tasks": [{"id": "d", "agent": "ag", "instruction": "specs/instructions/a.md"}]
                }
            }
        )
        orch, reposets, agents = make_orchestrator(executor)
        state = orch.run(wf, reposets, agents)
        assert state.status == "succeeded"

        run_dir = workspace / ".orchestrator" / "runs" / state.run_id
        snapshot = read_status(run_dir)

        expected_top_keys = {
            "run_id",
            "workflow_id",
            "status",
            "updated_at",
            "current_task",
            "counts",
            "tasks",
            "route_decisions",
            "tripped_breakers",
            "usage_totals",
            "integration",
        }
        assert set(snapshot.keys()) == expected_top_keys
        assert "spawned_by" not in snapshot  # RunState-only field, not part of the derived
        # status.json projection -- write_status() was not touched by this ticket.

        expected_task_keys = {
            "id",
            "status",
            "attempts",
            "output_artifact_path",
            "origin",
            "route",
            "not_taken_reason",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "integration_status",
            "tier_reached",
            "conflicted_count",
            "dispatch_cycle",
        }
        for task_entry in snapshot["tasks"]:
            assert set(task_entry.keys()) == expected_task_keys
