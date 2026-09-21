"""Tests for T-Sc7Rm2 — isolation/integration/scheduling schema+model additions
(E-Wk9Tz3, HLD §10.1/§10.2/§10.3, §11 M2).

Covers: named constants (AC-1), model defaults (AC-2/AC-3), S-2/S-3/S-5/S-6 security
fields (AC-4..7), R-21/R-1b requeue fields (AC-8/AC-9), R-4/R-5 reserved+derived resolvers
(AC-10/AC-11/AC-12), schema/model agreement + round-trip (AC-13, AC-16), the AC-15
command/argv containment structural test, NFR-5 backward compat (AC-16), prepare_resume
preservation (AC-17), write_status's exact key set (AC-18), emit_tasks manifest survival
(AC-19), and the _clone_body pin test (AC-20).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_orchestrator.artifacts import LocalFsArtifactStore, read_task_manifest
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import (
    DEFAULT_COMMIT_DENYLIST,
    DEFAULT_HOTSPOTS_PATH,
    DEFAULT_INTEGRATION_LOCK_TIMEOUT_SECONDS,
    DEFAULT_REGENERATE_TIMEOUT_SECONDS,
    DEFAULT_RESOLVER_DISALLOWED_TOOLS,
    DEFAULT_VERIFY_TIMEOUT_SECONDS,
    RESOLVER_FORCED_DISALLOWED_TOOLS,
    AgentSpec,
    BudgetCounters,
    IntegrationSpec,
    LoopSpec,
    RegenerateRule,
    RouterSpec,
    RouteSpec,
    RunIntegrationState,
    RunState,
    SchedulingSpec,
    TaskContext,
    TaskIntegrationState,
    TaskRunState,
    TaskSpec,
    WorkflowDefaults,
    WorkflowSpec,
    resolve_overlap_preference,
    resolve_task_isolation,
)
from agent_orchestrator.runstate import RunStateStore
from agent_orchestrator.spec import load_workflow

REPO_ROOT = Path(__file__).parent.parent
FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# AC-1: named constants, no magic literals
# ---------------------------------------------------------------------------


class TestNamedConstants:
    def test_verify_timeout_default(self) -> None:
        assert DEFAULT_VERIFY_TIMEOUT_SECONDS == 1800

    def test_integration_lock_timeout_default(self) -> None:
        assert DEFAULT_INTEGRATION_LOCK_TIMEOUT_SECONDS == 1800

    def test_regenerate_timeout_default(self) -> None:
        assert DEFAULT_REGENERATE_TIMEOUT_SECONDS == 120

    def test_hotspots_path_default(self) -> None:
        assert DEFAULT_HOTSPOTS_PATH == ".ao/hotspots.json"

    def test_resolver_disallowed_tools_default_is_non_empty(self) -> None:
        # An empty default would silently reopen the hole V11 exists to close.
        assert DEFAULT_RESOLVER_DISALLOWED_TOOLS
        assert set(DEFAULT_RESOLVER_DISALLOWED_TOOLS) == {"WebFetch", "WebSearch"}

    def test_resolver_forced_floor_denies_shell_subagent_and_egress(self) -> None:
        """As-built security review C-3: unlike `DEFAULT_RESOLVER_DISALLOWED_TOOLS` (an
        operator-editable default), this floor is unioned in unconditionally by
        `isolation.escalation.resolver_agent_spec` -- a linked worktree shares the main
        repository's `.git`, so a shell there is a host-compromise primitive."""
        assert set(RESOLVER_FORCED_DISALLOWED_TOOLS) >= {
            "Bash",
            "Task",
            "WebFetch",
            "WebSearch",
        }

    def test_commit_denylist_default_covers_common_secret_shapes(self) -> None:
        for expected in (".env", "*.pem", "*.key", "id_rsa*", "*credentials*.json"):
            assert expected in DEFAULT_COMMIT_DENYLIST


# ---------------------------------------------------------------------------
# AC-2/AC-3: model defaults
# ---------------------------------------------------------------------------


class TestModelDefaults:
    def test_task_spec_isolation_and_touches_defaults(self) -> None:
        t = TaskSpec(id="t", agent="a", instruction="i.md")
        assert t.isolation == "inherit"
        assert t.touches == []

    def test_workflow_defaults_isolation_defaults_none(self) -> None:
        assert WorkflowDefaults().isolation == "none"

    def test_integration_spec_defaults_match_hld(self) -> None:
        integ = IntegrationSpec()
        assert integ.ladder == ["auto", "mechanical", "llm", "rerun"]
        assert integ.auto_commit is True
        assert integ.on_denylisted_path == "fail"
        assert integ.resolver_deny_push is True
        assert integ.workspace_lock == "require"
        assert integ.strategy == "rebase"

    def test_scheduling_spec_defaults(self) -> None:
        sched = SchedulingSpec()
        assert sched.overlap_preference is None
        assert sched.hotspots_path == DEFAULT_HOTSPOTS_PATH

    def test_workflow_spec_integration_and_scheduling_default_constructed(self) -> None:
        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=[TaskSpec(id="t", agent="a", instruction="i.md")],
        )
        assert wf.integration == IntegrationSpec()
        assert wf.scheduling == SchedulingSpec()

    def test_task_context_env_defaults_empty(self) -> None:
        ctx = TaskContext(
            run_id="r",
            task_id="t",
            agent=AgentSpec(executor="fake"),
            instruction_path="i.md",
            input_paths=[],
            output_paths=[],
            repo_paths={},
            timeout_seconds=60,
        )
        assert ctx.env == {}


# ---------------------------------------------------------------------------
# C-5: pydantic Field(ge=...) constraints matching specs/workflow.schema.json's
# `minimum` for every field the review flagged (verify_timeout_seconds,
# lock_timeout_seconds, max_resolver_attempts, max_reruns_per_task,
# RegenerateRule.timeout_seconds) -- pydantic is the ONLY gate for an installed wheel
# (schema not packaged), so this parity matters as much as the V1-V12 rules.
# ---------------------------------------------------------------------------


class TestNumericFieldConstraintsMatchSchemaMinimums:
    @pytest.mark.parametrize(
        "field,bad_value",
        [
            ("verify_timeout_seconds", 0),
            ("lock_timeout_seconds", 0),
            ("max_resolver_attempts", -1),
            ("max_reruns_per_task", -1),
        ],
    )
    def test_integration_spec_rejects_below_schema_minimum(
        self, field: str, bad_value: int
    ) -> None:
        with pytest.raises(ValidationError):
            IntegrationSpec(**{field: bad_value})

    @pytest.mark.parametrize(
        "field,good_value",
        [
            ("verify_timeout_seconds", 1),
            ("lock_timeout_seconds", 1),
            ("max_resolver_attempts", 0),
            ("max_reruns_per_task", 0),
        ],
    )
    def test_integration_spec_accepts_schema_minimum_value(
        self, field: str, good_value: int
    ) -> None:
        integ = IntegrationSpec(**{field: good_value})
        assert getattr(integ, field) == good_value

    def test_regenerate_rule_rejects_below_schema_minimum(self) -> None:
        with pytest.raises(ValidationError):
            RegenerateRule(glob="*.lock", command=["make"], timeout_seconds=0)

    def test_regenerate_rule_accepts_schema_minimum_value(self) -> None:
        rule = RegenerateRule(glob="*.lock", command=["make"], timeout_seconds=1)
        assert rule.timeout_seconds == 1


# ---------------------------------------------------------------------------
# S-2/S-3/S-5/S-6 security fields (AC-4..7)
# ---------------------------------------------------------------------------


class TestSecurityFields:
    def test_s2_resolver_disallowed_tools_and_deny_push_defaults(self) -> None:
        integ = IntegrationSpec()
        assert integ.resolver_disallowed_tools == DEFAULT_RESOLVER_DISALLOWED_TOOLS
        assert integ.resolver_deny_push is True

    def test_agent_spec_forced_disallowed_tools_defaults_to_empty(self) -> None:
        """C-1: engine-populated only -- an ordinary agent carries no forced set, so the
        non-isolated dispatch path stays byte-identical (NFR-2)."""
        assert AgentSpec(executor="fake").forced_disallowed_tools == []

    def test_s3_auto_commit_denylist_and_action_defaults(self) -> None:
        integ = IntegrationSpec()
        assert integ.auto_commit is True
        assert integ.commit_denylist == DEFAULT_COMMIT_DENYLIST
        assert integ.on_denylisted_path == "fail"

    def test_s6_regenerate_rule_timeout_default(self) -> None:
        rule = RegenerateRule(glob="*.lock", command=["make", "lock"])
        assert rule.timeout_seconds == DEFAULT_REGENERATE_TIMEOUT_SECONDS

    def test_s5_run_integration_state_tier_counts_defaults_empty(self) -> None:
        assert RunIntegrationState().tier_counts == {}


# ---------------------------------------------------------------------------
# R-21/R-1b requeue fields (AC-8/AC-9)
# ---------------------------------------------------------------------------


class TestRequeueFields:
    def test_task_run_state_dispatch_cycle_defaults_zero(self) -> None:
        assert TaskRunState().dispatch_cycle == 0

    def test_budget_counters_reconciled_cycles_defaults_empty_and_tasks_untouched(self) -> None:
        bc = BudgetCounters()
        assert bc.reconciled_cycles == []
        assert bc.reconciled_tasks == []  # pre-existing field, left in place (backward compat)


# ---------------------------------------------------------------------------
# R-4 reserved workspace_lock field (AC-10)
# ---------------------------------------------------------------------------


class TestReservedWorkspaceLock:
    def test_integration_spec_workspace_lock_default_require(self) -> None:
        assert IntegrationSpec().workspace_lock == "require"

    def test_run_integration_state_workspace_lock_held_default_false(self) -> None:
        assert RunIntegrationState().workspace_lock_held is False


# ---------------------------------------------------------------------------
# R-5: resolve_overlap_preference — single derived-default site (AC-11)
# ---------------------------------------------------------------------------


def _wf_for_overlap(
    isolations: list[str], defaults_isolation: str, explicit: str | None
) -> WorkflowSpec:
    tasks = [
        TaskSpec(id=f"t{i}", agent="a", instruction="i.md", isolation=iso)
        for i, iso in enumerate(isolations)
    ]
    return WorkflowSpec(
        version="1.0",
        id="wf",
        repo_set="rs",
        defaults=WorkflowDefaults(isolation=defaults_isolation),
        tasks=tasks,
        scheduling=SchedulingSpec(overlap_preference=explicit),
    )


class TestResolveOverlapPreference:
    @pytest.mark.parametrize(
        "isolations,defaults_isolation,explicit,expected",
        [
            # NFR-2: a workflow with NO isolation anywhere resolves to "off".
            (["none"], "none", None, "off"),
            (["none", "none"], "none", None, "off"),
            # Any task resolving to worktree (directly or inherited) -> derived "soft".
            (["worktree"], "none", None, "soft"),
            (["inherit"], "worktree", None, "soft"),
            (["none", "worktree"], "none", None, "soft"),
            # An explicit spec value always wins over the derived default, both ways.
            (["worktree"], "none", "off", "off"),
            (["none"], "none", "soft", "soft"),
        ],
    )
    def test_table(
        self,
        isolations: list[str],
        defaults_isolation: str,
        explicit: str | None,
        expected: str,
    ) -> None:
        wf = _wf_for_overlap(isolations, defaults_isolation, explicit)
        assert resolve_overlap_preference(wf) == expected


# ---------------------------------------------------------------------------
# resolve_task_isolation — HLD §11 M2 (AC-12)
# ---------------------------------------------------------------------------


def _structural_workflow(role: str, task_isolation: str, defaults_isolation: str) -> WorkflowSpec:
    defaults = WorkflowDefaults(isolation=defaults_isolation)
    if role == "emit_tasks":
        t = TaskSpec(
            id="t",
            agent="a",
            instruction="i.md",
            isolation=task_isolation,
            emit_tasks=True,
            task_manifest_path="m.json",
        )
        return WorkflowSpec(version="1.0", id="wf", repo_set="rs", defaults=defaults, tasks=[t])
    if role == "router":
        t = TaskSpec(id="t", agent="a", instruction="i.md", isolation=task_isolation)
        other = TaskSpec(id="other", agent="a", instruction="i.md")
        return WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            defaults=defaults,
            tasks=[t, other],
            branches=[
                RouterSpec(
                    id="r",
                    router_task_id="t",
                    verdict_path="v.json",
                    routes={"a": RouteSpec(entry=["other"])},
                )
            ],
        )
    if role == "loop_gate":
        t = TaskSpec(id="t", agent="a", instruction="i.md", isolation=task_isolation)
        return WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            defaults=defaults,
            tasks=[t],
            loops=[LoopSpec(id="lp", body=["t"], gate_task_id="t", gate_output_path="g.json")],
        )
    raise ValueError(role)


_STRUCTURAL_ROLES = ["emit_tasks", "router", "loop_gate"]
_ISOLATION_DECLARATIONS = [
    ("none", "none"),
    ("worktree", "none"),
    ("inherit", "none"),
    ("inherit", "worktree"),
]


class TestResolveTaskIsolationStructural:
    """3 structural roles x 4 isolation-declaration shapes = 12 combinations; a
    structural task ALWAYS resolves to "none" regardless of what it (or the workflow
    default) declares."""

    @pytest.mark.parametrize("role", _STRUCTURAL_ROLES)
    @pytest.mark.parametrize("task_iso,defaults_iso", _ISOLATION_DECLARATIONS)
    def test_always_resolves_none(self, role: str, task_iso: str, defaults_iso: str) -> None:
        wf = _structural_workflow(role, task_iso, defaults_iso)
        assert resolve_task_isolation(wf.task("t"), wf) == "none"

    def test_warns_exactly_once_when_worktree_explicitly_requested(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        wf = _structural_workflow("emit_tasks", "worktree", "none")
        with caplog.at_level(logging.WARNING, logger="agent_orchestrator.models"):
            resolve_task_isolation(wf.task("t"), wf)
        matches = [r for r in caplog.records if "forced to isolation='none'" in r.getMessage()]
        assert len(matches) == 1
        assert "t" in matches[0].getMessage()

    def test_no_warning_when_structural_task_did_not_request_worktree(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        wf = _structural_workflow("router", "none", "none")
        with caplog.at_level(logging.WARNING, logger="agent_orchestrator.models"):
            resolve_task_isolation(wf.task("t"), wf)
        assert not any("forced to isolation" in r.getMessage() for r in caplog.records)


class TestResolveTaskIsolationLoopGateClone:
    """C-1 regression: a loop's gate task is re-dispatched every iteration via
    `engine.py::_clone_body`, which suffixes the clone's id ``__iter<N>`` for N>=2. An
    earlier version of `_is_structural_task`'s loop-gate branch did a BARE equality check
    against `gate_task_id` with no suffix-stripping, so `resolve_task_isolation` correctly
    forced iteration 1's gate task to "none" but silently isolated iteration 2+'s clone
    instead -- a direct violation of the "loop-gate tasks are serial barriers" invariant
    (ADR-0007 D4) for any workflow using `defaults.isolation="worktree"` with a loop.
    """

    def _loop_workflow(self, defaults_isolation: str) -> WorkflowSpec:
        gate = TaskSpec(id="dev", agent="a", instruction="i.md")
        return WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            defaults=WorkflowDefaults(isolation=defaults_isolation),
            tasks=[gate],
            loops=[LoopSpec(id="lp", body=["dev"], gate_task_id="dev", gate_output_path="g.json")],
        )

    def test_iteration_1_gate_task_resolves_none(self) -> None:
        wf = self._loop_workflow("worktree")
        assert resolve_task_isolation(wf.task("dev"), wf) == "none"

    def test_iteration_2_gate_clone_still_resolves_none(self) -> None:
        """The regression: a bare TaskSpec carrying the __iter2-suffixed clone id, with
        the workflow's defaults.isolation="worktree" (so an unrecognized structural task
        would incorrectly inherit "worktree"), must still resolve to "none"."""
        wf = self._loop_workflow("worktree")
        clone = TaskSpec(id="dev__iter2", agent="a", instruction="i.md", isolation="worktree")
        assert resolve_task_isolation(clone, wf) == "none"

    def test_iteration_3_gate_clone_still_resolves_none(self) -> None:
        wf = self._loop_workflow("worktree")
        clone = TaskSpec(id="dev__iter3", agent="a", instruction="i.md", isolation="inherit")
        assert resolve_task_isolation(clone, wf) == "none"

    def test_clone_body_boundary_gate_clone_resolves_none(self, tmp_path: Path) -> None:
        """Integration-style: go through the REAL `_clone_body` (not a hand-built
        TaskSpec) to prove the actual clone produced by the engine also resolves "none",
        not just a manually-suffixed stand-in."""
        from agent_orchestrator.engine import Orchestrator

        gate = TaskSpec(id="dev", agent="a", instruction="i.md", isolation="worktree")
        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            defaults=WorkflowDefaults(isolation="worktree"),
            tasks=[gate],
            loops=[LoopSpec(id="lp", body=["dev"], gate_task_id="dev", gate_output_path="g.json")],
        )
        store = LocalFsArtifactStore(str(tmp_path))
        rs_store = RunStateStore(str(tmp_path), store)
        orch = Orchestrator(FakeExecutor(), store, rs_store)

        clones = orch._clone_body(wf.loops[0], 2, wf)
        clone = clones[0]
        assert clone.id == "dev__iter2"
        assert resolve_task_isolation(clone, wf) == "none"


class TestResolveTaskIsolationNonStructural:
    @pytest.mark.parametrize(
        "task_iso,defaults_iso,expected",
        [
            ("none", "none", "none"),
            ("none", "worktree", "none"),
            ("worktree", "none", "worktree"),
            ("worktree", "worktree", "worktree"),
            ("inherit", "none", "none"),
            ("inherit", "worktree", "worktree"),
        ],
    )
    def test_non_structural_resolution(
        self, task_iso: str, defaults_iso: str, expected: str
    ) -> None:
        t = TaskSpec(id="t", agent="a", instruction="i.md", isolation=task_iso)
        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            defaults=WorkflowDefaults(isolation=defaults_iso),
            tasks=[t],
        )
        assert resolve_task_isolation(t, wf) == expected


# ---------------------------------------------------------------------------
# AC-13: schema accepts every new field; rejects unknown ones.
# ---------------------------------------------------------------------------


class TestSchemaAcceptsNewFields:
    def _full_workflow_dict(self) -> dict:
        return {
            "version": "1.0",
            "id": "iso-wf",
            "repo_set": "rs",
            "defaults": {"isolation": "worktree"},
            "tasks": [
                {
                    "id": "t1",
                    "agent": "ag",
                    "instruction": "i.md",
                    "isolation": "worktree",
                    "touches": ["src/**/*.py"],
                }
            ],
            "integration": {
                "strategy": "rebase",
                "branch": "ao/run/integration",
                "verify_command": ["make", "verify"],
                "verify_timeout_seconds": 600,
                "ladder": ["auto", "mechanical", "llm", "rerun"],
                "resolvers": {
                    "rerere": True,
                    "union": ["package-lock.json"],
                    "regenerate": [
                        {
                            "glob": "*.lock",
                            "command": ["make", "lock"],
                            "take": "theirs",
                            "timeout_seconds": 60,
                        }
                    ],
                },
                "auto_commit": True,
                "commit_denylist": [".env"],
                "on_denylisted_path": "fail",
                "resolver_agent": "resolver",
                "resolver_instruction": "merge-resolve.md",
                "resolver_disallowed_tools": ["WebFetch", "WebSearch"],
                "resolver_deny_push": True,
                "workspace_lock": "require",
                "max_resolver_attempts": 1,
                "max_reruns_per_task": 1,
                "untracked_outputs": "copy",
                "keep_worktrees": "on_failure",
                "sync_checkout": "on_demand",
                "commit_message_template": "ao: {task_id}",
                "lock_timeout_seconds": 900,
            },
            "scheduling": {"overlap_preference": "soft", "hotspots_path": ".ao/hotspots.json"},
        }

    def test_spec_using_every_new_field_loads(self, tmp_path: Path) -> None:
        p = tmp_path / "wf.json"
        p.write_text(json.dumps(self._full_workflow_dict()))
        wf = load_workflow(p)
        assert wf.integration.resolver_agent == "resolver"
        assert wf.scheduling.overlap_preference == "soft"
        assert wf.tasks[0].isolation == "worktree"
        assert wf.tasks[0].touches == ["src/**/*.py"]

    def test_unknown_integration_field_rejected(self, tmp_path: Path) -> None:
        data = self._full_workflow_dict()
        data["integration"]["bogus_field"] = True
        p = tmp_path / "wf.json"
        p.write_text(json.dumps(data))
        with pytest.raises(Exception):  # SpecValidationError (jsonschema ValidationError)
            load_workflow(p)

    def test_unknown_scheduling_field_rejected(self, tmp_path: Path) -> None:
        data = self._full_workflow_dict()
        data["scheduling"]["bogus_field"] = True
        p = tmp_path / "wf.json"
        p.write_text(json.dumps(data))
        with pytest.raises(Exception):
            load_workflow(p)

    def test_unknown_task_field_still_rejected(self, tmp_path: Path) -> None:
        data = self._full_workflow_dict()
        data["tasks"][0]["bogus_field"] = True
        p = tmp_path / "wf.json"
        p.write_text(json.dumps(data))
        with pytest.raises(Exception):
            load_workflow(p)


# ---------------------------------------------------------------------------
# AC-16(c): a workflow JSON with no new keys produces the documented defaults, and
# every specs/examples/* workflow round-trips unchanged through pydantic.
# ---------------------------------------------------------------------------


def _discover_example_workflows() -> list[Path]:
    return sorted((REPO_ROOT / "specs" / "examples").glob("workflow*.json"))


class TestSpecsExamplesRoundTripUnchanged:
    @pytest.mark.parametrize("path", _discover_example_workflows(), ids=lambda p: p.name)
    def test_loads_with_documented_defaults(self, path: Path) -> None:
        wf = load_workflow(path)
        assert wf.defaults.isolation == "none"
        assert wf.integration == IntegrationSpec()
        assert wf.scheduling == SchedulingSpec()
        for task in wf.tasks:
            assert task.isolation == "inherit"
            assert task.touches == []

    @pytest.mark.parametrize("path", _discover_example_workflows(), ids=lambda p: p.name)
    def test_round_trips_through_json_unchanged(self, path: Path) -> None:
        wf = load_workflow(path)
        reloaded = WorkflowSpec(**json.loads(wf.model_dump_json()))
        assert reloaded == wf


# ---------------------------------------------------------------------------
# AC-15: command/argv containment — structural test.
# ---------------------------------------------------------------------------


class TestCommandArgvContainment:
    def test_no_command_or_resolver_field_lives_on_task_spec(self) -> None:
        """verify_command, resolvers.regenerate[].command, commit_denylist and every
        resolver_* field must exist ONLY on WorkflowSpec.integration, never on TaskSpec
        -- so an agent-authored emit_tasks manifest (parsed into TaskSpec alone by
        artifacts.read_task_manifest) can contribute a mode enum + advisory globs but
        nothing that executes. Computed from IntegrationSpec's own fields (not
        hardcoded) so a FUTURE field addition is automatically covered."""
        integration_execution_fields = {
            name
            for name in IntegrationSpec.model_fields
            if name.startswith("resolver_")
            or name in {"verify_command", "commit_denylist", "resolvers"}
        }
        # Sanity: the set isn't accidentally empty (which would make this test vacuous).
        assert integration_execution_fields == {
            "verify_command",
            "commit_denylist",
            "resolvers",
            "resolver_agent",
            "resolver_instruction",
            "resolver_disallowed_tools",
            "resolver_deny_push",
        }
        assert integration_execution_fields.isdisjoint(TaskSpec.model_fields.keys())


# ---------------------------------------------------------------------------
# AC-19: emit_tasks manifest entry survives TaskSpec(**t) construction unchanged.
# ---------------------------------------------------------------------------


class TestManifestSurvivesIsolationFields:
    def test_read_task_manifest_preserves_isolation_and_touches(self, tmp_path: Path) -> None:
        manifest = {
            "tasks": [
                {
                    "id": "t1",
                    "agent": "ag",
                    "instruction": "i.md",
                    "isolation": "worktree",
                    "touches": ["src/**"],
                }
            ]
        }
        (tmp_path / "manifest.json").write_text(json.dumps(manifest))
        store = LocalFsArtifactStore(str(tmp_path))
        tasks = read_task_manifest(store, "manifest.json")
        assert tasks[0].isolation == "worktree"
        assert tasks[0].touches == ["src/**"]


# ---------------------------------------------------------------------------
# AC-20: _clone_body pin test -- no engine.py edit needed, model_copy already carries
# isolation/touches forward.
# ---------------------------------------------------------------------------


class TestCloneBodyPinsIsolationFields:
    def test_clone_body_carries_isolation_and_touches_forward(self, tmp_path: Path) -> None:
        from agent_orchestrator.engine import Orchestrator

        task = TaskSpec(
            id="dev",
            agent="ag",
            instruction="i.md",
            isolation="worktree",
            touches=["src/**"],
        )
        wf = WorkflowSpec(version="1.0", id="wf", repo_set="rs", tasks=[task])
        loop = LoopSpec(id="lp", body=["dev"], gate_task_id="dev", gate_output_path="g.json")

        store = LocalFsArtifactStore(str(tmp_path))
        rs_store = RunStateStore(str(tmp_path), store)
        orch = Orchestrator(FakeExecutor(), store, rs_store)

        clones = orch._clone_body(loop, 2, wf)
        assert clones[0].isolation == "worktree"
        assert clones[0].touches == ["src/**"]


# ---------------------------------------------------------------------------
# AC-16(a)/(b): NFR-5 backward-compat load of a pre-epic state.json, and a new
# RunState round-trips identically.
# ---------------------------------------------------------------------------


class TestRunStateBackwardCompat:
    def test_old_state_json_fixture_loads_with_isolation_field_defaults(self) -> None:
        fixture_path = FIXTURES_DIR / "state_pre_routing_breakers.json"
        raw = fixture_path.read_text()
        assert "integration" not in raw
        assert "task_integration" not in raw
        assert "dispatch_cycle" not in raw
        assert "reconciled_cycles" not in raw

        state = RunState.model_validate_json(raw)

        assert state.integration == RunIntegrationState()
        assert state.integration.active is False
        assert state.task_integration == {}
        assert state.budget_counters.reconciled_cycles == []
        for ts in state.tasks.values():
            assert ts.dispatch_cycle == 0

    def test_old_state_json_fixture_round_trips_after_load(self) -> None:
        fixture_path = FIXTURES_DIR / "state_pre_routing_breakers.json"
        state = RunState.model_validate_json(fixture_path.read_text())
        reloaded = RunState.model_validate_json(state.model_dump_json())
        assert reloaded == state

    def test_new_run_state_round_trips_identically(self) -> None:
        state = RunState(
            run_id="r",
            workflow_id="wf",
            repo_set="rs",
            started_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            tasks={"t1": TaskRunState(dispatch_cycle=2)},
            task_integration={"t1": TaskIntegrationState(status="integrated", mode="resolve")},
            integration=RunIntegrationState(active=True, branch="ao/r/integration"),
        )
        reloaded = RunState.model_validate_json(state.model_dump_json())
        assert reloaded == state


# ---------------------------------------------------------------------------
# AC-17: prepare_resume preserves state.integration/task_integration verbatim, and
# normalizes any "integrating" status to "pending" while keeping mode + dispatch_cycle.
# ---------------------------------------------------------------------------


class TestPrepareResumeIntegration:
    def test_preserves_integration_state_and_normalizes_integrating_status(
        self, tmp_path: Path
    ) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        rs_store = RunStateStore(str(tmp_path), store)
        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=[TaskSpec(id="t1", agent="a", instruction="i.md")],
        )
        state = rs_store.new_run(wf)
        state.tasks["t1"] = TaskRunState(status="running", dispatch_cycle=3)
        state.task_integration["t1"] = TaskIntegrationState(
            status="integrating", mode="resolve", tier_reached="llm"
        )
        state.integration = RunIntegrationState(
            active=True, branch="ao/x/integration", tier_counts={"auto": 2}
        )

        resumed = rs_store.prepare_resume(state, wf)

        # TaskRunState reset to pending, but dispatch_cycle preserved (R-21).
        assert resumed.tasks["t1"].status == "pending"
        assert resumed.tasks["t1"].dispatch_cycle == 3

        # task_integration normalized from "integrating" -> "pending"; mode preserved.
        assert resumed.task_integration["t1"].status == "pending"
        assert resumed.task_integration["t1"].mode == "resolve"
        assert resumed.task_integration["t1"].tier_reached == "llm"

        # RunIntegrationState preserved verbatim.
        assert resumed.integration.active is True
        assert resumed.integration.branch == "ao/x/integration"
        assert resumed.integration.tier_counts == {"auto": 2}

    def test_non_integrating_task_integration_status_untouched(self, tmp_path: Path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        rs_store = RunStateStore(str(tmp_path), store)
        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=[TaskSpec(id="t1", agent="a", instruction="i.md")],
        )
        state = rs_store.new_run(wf)
        state.tasks["t1"] = TaskRunState(status="succeeded")
        state.task_integration["t1"] = TaskIntegrationState(status="integrated")

        resumed = rs_store.prepare_resume(state, wf)
        assert resumed.task_integration["t1"].status == "integrated"


# ---------------------------------------------------------------------------
# AC-18: write_status's exact top-level `integration` block + per-task keys.
# ---------------------------------------------------------------------------


class TestWriteStatusIntegrationBlock:
    def test_exact_key_sets(self, tmp_path: Path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        rs_store = RunStateStore(str(tmp_path), store)
        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=[TaskSpec(id="t1", agent="a", instruction="i.md")],
        )
        state = rs_store.new_run(wf)
        state.task_integration["t1"] = TaskIntegrationState(
            status="integrated", tier_reached="auto", conflicted_paths=["a.py", "b.py"]
        )
        state.integration = RunIntegrationState(
            active=True, branch="ao/x/integration", tier_counts={"auto": 1}
        )
        rs_store.save(state)

        status_path = tmp_path / ".orchestrator" / "runs" / state.run_id / "status.json"
        snap = json.loads(status_path.read_text())

        assert set(snap.keys()) == {
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
        assert set(snap["integration"].keys()) == {
            "active",
            "branch",
            "heads",
            "tier_counts",
            "integrated",
            "conflict",
            "failed",
        }
        assert snap["integration"]["active"] is True
        assert snap["integration"]["branch"] == "ao/x/integration"
        assert snap["integration"]["tier_counts"] == {"auto": 1}
        assert snap["integration"]["integrated"] == 1
        assert snap["integration"]["conflict"] == 0
        assert snap["integration"]["failed"] == 0

        task_entry = snap["tasks"][0]
        assert set(task_entry.keys()) == {
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
        assert task_entry["integration_status"] == "integrated"
        assert task_entry["tier_reached"] == "auto"
        assert task_entry["conflicted_count"] == 2
        assert task_entry["dispatch_cycle"] == 0

    def test_conflict_statuses_grouped_and_task_without_integration_defaults(
        self, tmp_path: Path
    ) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        rs_store = RunStateStore(str(tmp_path), store)
        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=[
                TaskSpec(id="t1", agent="a", instruction="i.md"),
                TaskSpec(id="t2", agent="a", instruction="i.md"),
            ],
        )
        state = rs_store.new_run(wf)
        state.task_integration["t1"] = TaskIntegrationState(status="conflict_resolver")
        state.task_integration["t2"] = TaskIntegrationState(status="failed")
        rs_store.save(state)

        status_path = tmp_path / ".orchestrator" / "runs" / state.run_id / "status.json"
        snap = json.loads(status_path.read_text())

        assert snap["integration"]["conflict"] == 1
        assert snap["integration"]["failed"] == 1
        t2_entry = next(t for t in snap["tasks"] if t["id"] == "t2")
        assert t2_entry["integration_status"] == "failed"

    def test_task_with_no_integration_entry_defaults_sensibly(self, tmp_path: Path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        rs_store = RunStateStore(str(tmp_path), store)
        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=[TaskSpec(id="t1", agent="a", instruction="i.md")],
        )
        state = rs_store.new_run(wf)
        # No task_integration entry at all for t1 -- must default sensibly.
        rs_store.save(state)

        status_path = tmp_path / ".orchestrator" / "runs" / state.run_id / "status.json"
        snap = json.loads(status_path.read_text())

        t1_entry = next(t for t in snap["tasks"] if t["id"] == "t1")
        assert t1_entry["integration_status"] == "none"
        assert t1_entry["tier_reached"] is None
        assert t1_entry["conflicted_count"] == 0
