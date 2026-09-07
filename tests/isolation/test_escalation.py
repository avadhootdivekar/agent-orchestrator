"""Tests for E-Wk9Tz3 T-Lr6Ka3: `isolation/escalation.py` (T2 LLM resolver / T3 rerun /
T4 fail decision logic, S-2 structural containment, and the artifact writers/dispatch-
context builders `engine.py`'s `_run_and_integrate` mode branch calls).

AC-1's table-driven decision test lives in `TestEscalateDecisionTable`. AC-2's "no value
in conflict-<n>.json is read from a repository file's contents" lives in
`TestWriteConflictManifest`. S-2's union/env containment tests (TASK.md amendment 12) live
in `TestResolverAgentSpec`/`TestResolverEnv`/`TestBuildResolverDispatch`. The packaged
`merge-resolve.md` asset test lives in `TestMergeResolveAsset` (mirrors
`tests/test_builtin_routed_runner_assets.py`'s static packaging-test style).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent_orchestrator import templates as templates_pkg
from agent_orchestrator.isolation import escalation
from agent_orchestrator.isolation.git import GitRepo
from agent_orchestrator.isolation.integrator import (
    STATUS_CONFLICT_RERUN,
    STATUS_CONFLICT_RESOLVER,
    STATUS_FAILED,
)
from agent_orchestrator.isolation.worktrees import RepoIsolation, TaskIsolation
from agent_orchestrator.models import AgentSpec, IntegrationSpec, TaskIntegrationState, TaskSpec

from .conftest import make_repo

# ---------------------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------------------


def _ti(
    *,
    resolver_attempts: int = 0,
    reruns: int = 0,
    conflicted_paths: list[str] | None = None,
    repos: dict[str, str] | None = None,
    branches: dict[str, str] | None = None,
    base_commits: dict[str, str] | None = None,
    squash_commits: dict[str, str] | None = None,
) -> TaskIntegrationState:
    return TaskIntegrationState(
        isolation="worktree",
        status="integrating",
        resolver_attempts=resolver_attempts,
        reruns=reruns,
        conflicted_paths=conflicted_paths or [],
        repos=repos or {},
        branches=branches or {},
        base_commits=base_commits or {},
        squash_commits=squash_commits or {},
    )


def _spec(
    *,
    ladder: list[str] | None = None,
    resolver_agent: str | None = "merge-resolver",
    max_resolver_attempts: int = 1,
    max_reruns_per_task: int = 1,
    resolver_disallowed_tools: list[str] | None = None,
    resolver_deny_push: bool = True,
) -> IntegrationSpec:
    kwargs: dict[str, object] = {
        "ladder": ladder if ladder is not None else ["auto", "mechanical", "llm", "rerun"],
        "resolver_agent": resolver_agent,
        "max_resolver_attempts": max_resolver_attempts,
        "max_reruns_per_task": max_reruns_per_task,
        "resolver_deny_push": resolver_deny_push,
    }
    if resolver_disallowed_tools is not None:
        kwargs["resolver_disallowed_tools"] = resolver_disallowed_tools
    return IntegrationSpec(**kwargs)  # type: ignore[arg-type]


def _task_iso(task_id: str = "a", repo_keys: list[str] | None = None) -> TaskIsolation:
    repos = [
        RepoIsolation(
            key=k,
            toplevel=f"/repo/{k}",
            common_dir=f"/repo/{k}/.git",
            worktree_root=f"/wt/{task_id}/{k}",
            branch=f"ao/run1/{task_id}",
            base="base-sha",
        )
        for k in (repo_keys or ["core"])
    ]
    return TaskIsolation(
        task_id=task_id, cycle=1, declared_outputs=[], workspace_root="/workspace", repos=repos
    )


# ---------------------------------------------------------------------------------------
# AC-1: escalate() decision table
# ---------------------------------------------------------------------------------------


class TestEscalateDecisionTable:
    @pytest.mark.parametrize(
        (
            "ladder",
            "resolver_agent",
            "resolver_attempts",
            "max_resolver_attempts",
            "reruns",
            "max_reruns_per_task",
            "cause",
            "expected_status",
        ),
        [
            # -- T2 eligible: llm in ladder, resolver_agent set, under cap, conflict cause.
            (["llm", "rerun"], "merge-resolver", 0, 1, 0, 1, "conflict", STATUS_CONFLICT_RESOLVER),
            (["llm", "rerun"], "merge-resolver", 1, 2, 0, 1, "conflict", STATUS_CONFLICT_RESOLVER),
            # -- T2 never entered for cause == "verify" (AC-9), even if otherwise eligible.
            (["llm", "rerun"], "merge-resolver", 0, 1, 0, 1, "verify", STATUS_CONFLICT_RERUN),
            # -- "llm" absent from ladder -> falls through to T3.
            (["rerun"], "merge-resolver", 0, 1, 0, 1, "conflict", STATUS_CONFLICT_RERUN),
            # -- resolver_agent unset -> T2 skipped even with "llm" in ladder and under cap.
            (["llm", "rerun"], None, 0, 1, 0, 1, "conflict", STATUS_CONFLICT_RERUN),
            # -- resolver_attempts already at cap -> T2 skipped, falls to T3.
            (["llm", "rerun"], "merge-resolver", 1, 1, 0, 1, "conflict", STATUS_CONFLICT_RERUN),
            (["llm", "rerun"], "merge-resolver", 5, 1, 0, 1, "conflict", STATUS_CONFLICT_RERUN),
            # -- max_resolver_attempts: 0 -> straight to T3 (never T2), per HLD edge case.
            (["llm", "rerun"], "merge-resolver", 0, 0, 0, 1, "conflict", STATUS_CONFLICT_RERUN),
            # -- T3 eligible: rerun in ladder, under cap (from either cause).
            (["rerun"], None, 0, 1, 0, 1, "conflict", STATUS_CONFLICT_RERUN),
            (["rerun"], None, 0, 1, 0, 1, "verify", STATUS_CONFLICT_RERUN),
            (["rerun"], None, 0, 1, 1, 2, "conflict", STATUS_CONFLICT_RERUN),
            # -- "rerun" absent from ladder -> straight to T4.
            (["llm"], "merge-resolver", 1, 1, 0, 1, "conflict", STATUS_FAILED),
            (["auto", "mechanical"], "merge-resolver", 0, 1, 0, 1, "conflict", STATUS_FAILED),
            # -- reruns already at cap -> T4.
            (["rerun"], None, 0, 1, 1, 1, "conflict", STATUS_FAILED),
            (["llm", "rerun"], "merge-resolver", 1, 1, 3, 1, "conflict", STATUS_FAILED),
            # -- both caps 0 -> straight to T4 ("eject", pure merge-queue semantics).
            (["llm", "rerun"], "merge-resolver", 0, 0, 0, 0, "conflict", STATUS_FAILED),
            (["llm", "rerun"], "merge-resolver", 0, 0, 0, 0, "verify", STATUS_FAILED),
            # -- empty ladder -> T4 regardless of caps/cause.
            ([], "merge-resolver", 0, 5, 0, 5, "conflict", STATUS_FAILED),
            ([], "merge-resolver", 0, 5, 0, 5, "verify", STATUS_FAILED),
            # -- verify cause, rerun cap exhausted -> T4 (never T2, per AC-9).
            (["llm", "rerun"], "merge-resolver", 0, 1, 1, 1, "verify", STATUS_FAILED),
        ],
    )
    def test_decision_table(
        self,
        ladder: list[str],
        resolver_agent: str | None,
        resolver_attempts: int,
        max_resolver_attempts: int,
        reruns: int,
        max_reruns_per_task: int,
        cause: str,
        expected_status: str,
    ) -> None:
        spec = _spec(
            ladder=ladder,
            resolver_agent=resolver_agent,
            max_resolver_attempts=max_resolver_attempts,
            max_reruns_per_task=max_reruns_per_task,
        )
        ti = _ti(resolver_attempts=resolver_attempts, reruns=reruns, conflicted_paths=["f.txt"])
        result = escalation.escalate(ti, spec, cause)  # type: ignore[arg-type]
        assert result.status == expected_status

    def test_conflicted_paths_carried_through_every_branch(self) -> None:
        ti = _ti(resolver_attempts=0, reruns=0, conflicted_paths=["a.py", "b.py"])
        for spec in (
            _spec(ladder=["llm"], max_resolver_attempts=1),
            _spec(ladder=["rerun"], max_reruns_per_task=1),
            _spec(ladder=[]),
        ):
            result = escalation.escalate(ti, spec, "conflict")
            assert result.conflicted_paths == ["a.py", "b.py"]

    def test_tier_reached_left_none_for_integrator_to_fill_in(self) -> None:
        """`Integrator._merge_escalation` fills `tier_reached` from its own already-known
        `overall_tier` -- `escalate()` must not guess it (see the function's own
        docstring), so every branch returns the `IntegrationResult` default."""
        ti = _ti()
        assert escalation.escalate(ti, _spec(ladder=["llm"]), "conflict").tier_reached is None
        assert escalation.escalate(ti, _spec(ladder=["rerun"]), "conflict").tier_reached is None
        assert escalation.escalate(ti, _spec(ladder=[]), "conflict").tier_reached is None


class TestT4Reason:
    def test_names_paths_worktrees_and_branches(self) -> None:
        ti = _ti(
            resolver_attempts=1,
            reruns=1,
            conflicted_paths=["src/x.py"],
            repos={"core": "/wt/a/core"},
            branches={"core": "ao/run1/a"},
        )
        result = escalation.escalate(ti, _spec(ladder=[]), "conflict")
        assert result.status == STATUS_FAILED
        assert result.reason is not None
        assert "src/x.py" in result.reason
        assert "/wt/a/core" in result.reason
        assert "ao/run1/a" in result.reason
        assert "conflict_unresolved" in result.reason

    def test_verify_cause_reason_distinguishable(self) -> None:
        ti = _ti(conflicted_paths=[])
        result = escalation.escalate(ti, _spec(ladder=[]), "verify")
        assert result.reason is not None
        assert "verify_unresolved" in result.reason


# ---------------------------------------------------------------------------------------
# S-2 (BLOCKING, TASK.md amendment 12) -- structural containment
# ---------------------------------------------------------------------------------------


class TestResolverAgentSpec:
    def test_union_when_agent_declares_no_disallowed_tools(self) -> None:
        agent = AgentSpec(executor="fake")
        spec = _spec(resolver_disallowed_tools=["WebFetch", "WebSearch"])
        forced = escalation.resolver_agent_spec(agent, spec)
        assert set(forced.disallowed_tools) == {"WebFetch", "WebSearch"}

    def test_union_preserves_the_agents_own_list(self) -> None:
        agent = AgentSpec(executor="fake", disallowed_tools=["KillShell"])
        spec = _spec(resolver_disallowed_tools=["WebFetch", "WebSearch"])
        forced = escalation.resolver_agent_spec(agent, spec)
        assert set(forced.disallowed_tools) == {"KillShell", "WebFetch", "WebSearch"}

    def test_agent_cannot_shrink_the_forced_set(self) -> None:
        """Even if an agent's own spec somehow declared an empty override intent, the
        union always wins -- there is no code path where the forced set is narrowed."""
        agent = AgentSpec(executor="fake", disallowed_tools=[])
        spec = _spec(resolver_disallowed_tools=["WebFetch", "WebSearch"])
        forced = escalation.resolver_agent_spec(agent, spec)
        assert "WebFetch" in forced.disallowed_tools
        assert "WebSearch" in forced.disallowed_tools

    def test_does_not_mutate_the_original_agent_spec(self) -> None:
        agent = AgentSpec(executor="fake")
        escalation.resolver_agent_spec(agent, _spec())
        assert agent.disallowed_tools == []


class TestResolverEnv:
    def test_default_deny_push_env_shape(self) -> None:
        env = escalation.resolver_env(_spec(resolver_deny_push=True))
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GIT_ASKPASS"] == "/bin/false"
        assert env["GIT_CONFIG_COUNT"] == "2"
        assert env["GIT_CONFIG_KEY_0"] == "credential.helper"
        assert env["GIT_CONFIG_VALUE_0"] == ""
        assert env["GIT_CONFIG_KEY_1"] == "http.proxy"
        assert env["GIT_CONFIG_VALUE_1"] == "127.0.0.1:1"
        assert len(env) == 7

    def test_opt_out_returns_empty(self) -> None:
        assert escalation.resolver_env(_spec(resolver_deny_push=False)) == {}


class TestBuildResolverDispatch:
    def _task(self) -> TaskSpec:
        return TaskSpec(
            id="a",
            agent="original-agent",
            instruction="specs/instructions/design.md",
            inputs=["specs/in.txt"],
            outputs=["out/a.txt"],
        )

    def test_substitutes_agent_and_instruction(self) -> None:
        agents = {
            "original-agent": AgentSpec(executor="fake"),
            "merge-resolver": AgentSpec(executor="fake"),
        }
        spec = _spec(resolver_agent="merge-resolver")
        dispatch = escalation.build_resolver_dispatch(
            self._task(),
            agents,
            None,
            spec,
            manifest_relpath=".orchestrator/runs/r1/a/integration/conflict-1.json",
            instruction_relpath=".orchestrator/runs/r1/a/integration/merge-resolve.md",
        )
        assert dispatch.task.agent == "merge-resolver"
        assert dispatch.task.instruction == ".orchestrator/runs/r1/a/integration/merge-resolve.md"
        assert dispatch.task.id == "a"  # AC-4: task id untouched

    def test_conflict_manifest_appended_to_inputs(self) -> None:
        agents = {
            "original-agent": AgentSpec(executor="fake"),
            "merge-resolver": AgentSpec(executor="fake"),
        }
        dispatch = escalation.build_resolver_dispatch(
            self._task(),
            agents,
            None,
            _spec(resolver_agent="merge-resolver"),
            manifest_relpath=".orchestrator/runs/r1/a/integration/conflict-1.json",
            instruction_relpath=".orchestrator/runs/r1/a/integration/merge-resolve.md",
        )
        assert dispatch.task.inputs == [
            "specs/in.txt",
            ".orchestrator/runs/r1/a/integration/conflict-1.json",
        ]

    def test_effective_disallowed_tools_forced_even_when_agent_declares_none(self) -> None:
        agents = {
            "original-agent": AgentSpec(executor="fake"),
            "merge-resolver": AgentSpec(executor="fake"),  # declares NO disallowed_tools
        }
        spec = _spec(resolver_agent="merge-resolver")
        dispatch = escalation.build_resolver_dispatch(
            self._task(),
            agents,
            None,
            spec,
            manifest_relpath="x.json",
            instruction_relpath="y.md",
        )
        effective = dispatch.agents[dispatch.task.agent]
        assert "WebFetch" in effective.disallowed_tools
        assert "WebSearch" in effective.disallowed_tools
        # The ORIGINAL agents dict entry is untouched (a shallow copy was made).
        assert agents["merge-resolver"].disallowed_tools == []

    def test_env_union_with_existing_isolation_overlay(self) -> None:
        agents = {"original-agent": AgentSpec(executor="fake"), "mr": AgentSpec(executor="fake")}
        spec = _spec(resolver_agent="mr", resolver_deny_push=True)
        dispatch = escalation.build_resolver_dispatch(
            self._task(),
            agents,
            {"AO_ISOLATION": "worktree", "AO_TASK_BRANCH": "ao/r1/a"},
            spec,
            manifest_relpath="x.json",
            instruction_relpath="y.md",
        )
        assert dispatch.env["AO_ISOLATION"] == "worktree"
        assert dispatch.env["AO_TASK_BRANCH"] == "ao/r1/a"
        assert dispatch.env["GIT_TERMINAL_PROMPT"] == "0"
        assert dispatch.env["GIT_ASKPASS"] == "/bin/false"

    def test_non_resolver_dispatch_carries_neither_forced_tools_nor_env(self) -> None:
        """A PLAIN task (never touched by `build_resolver_dispatch`) carries none of S-2's
        forced containment -- proven here at the unit level; the engine-level equivalent
        (a real, non-resolver dispatch's actual `TaskContext`) is in
        `tests/test_engine_conflict_escalation.py`."""
        agent = AgentSpec(executor="fake")
        assert agent.disallowed_tools == []
        env_overlay = {"AO_ISOLATION": "worktree"}
        assert "GIT_TERMINAL_PROMPT" not in env_overlay


class TestBuildRerunTask:
    def test_appends_patch_path_only(self) -> None:
        task = TaskSpec(
            id="a",
            agent="original-agent",
            instruction="specs/instructions/design.md",
            inputs=["specs/in.txt"],
            outputs=["out/a.txt"],
        )
        rerun_task = escalation.build_rerun_task(
            task, ".orchestrator/runs/r1/a/integration/previous-1.patch"
        )
        assert rerun_task.agent == "original-agent"
        assert rerun_task.instruction == "specs/instructions/design.md"
        assert rerun_task.inputs == [
            "specs/in.txt",
            ".orchestrator/runs/r1/a/integration/previous-1.patch",
        ]
        assert rerun_task.outputs == ["out/a.txt"]


# ---------------------------------------------------------------------------------------
# AC-3 -- workspace-relative artifact paths
# ---------------------------------------------------------------------------------------


class TestArtifactRelpaths:
    def test_conflict_manifest_relpath_shape(self) -> None:
        assert escalation.conflict_manifest_relpath("r1", "a", 2) == (
            ".orchestrator/runs/r1/a/integration/conflict-2.json"
        )

    def test_previous_patch_relpath_shape(self) -> None:
        assert escalation.previous_patch_relpath("r1", "a", 3) == (
            ".orchestrator/runs/r1/a/integration/previous-3.patch"
        )

    def test_resolver_instruction_relpath_shape(self) -> None:
        assert escalation.resolver_instruction_relpath("r1", "a") == (
            ".orchestrator/runs/r1/a/integration/merge-resolve.md"
        )


# ---------------------------------------------------------------------------------------
# AC-2 -- conflict manifest writer
# ---------------------------------------------------------------------------------------


class TestWriteConflictManifest:
    def test_shape_and_version(self, tmp_path: Path) -> None:
        dest = tmp_path / "conflict-1.json"
        ti = _ti(
            resolver_attempts=1,
            conflicted_paths=["src/x.py"],
            repos={"core": "/wt/a/core"},
            branches={"core": "ao/run1/a"},
            base_commits={"core": "base123"},
            squash_commits={"core": "squash456"},
        )
        task_iso = _task_iso()
        escalation.write_conflict_manifest(
            str(dest),
            run_id="r1",
            task_id="a",
            attempt=1,
            task_integration=ti,
            task_iso=task_iso,
            rebase_in_progress=True,
        )
        payload = json.loads(dest.read_text(encoding="utf-8"))
        assert payload["version"] == "1.0"
        assert payload["run_id"] == "r1"
        assert payload["task_id"] == "a"
        assert payload["attempt"] == 1
        assert payload["conflicted_paths"] == ["src/x.py"]
        assert payload["rebase_in_progress"] is True
        assert payload["repos"][0]["repo_key"] == "core"
        assert payload["repos"][0]["worktree"] == "/wt/a/core"
        assert payload["repos"][0]["branch"] == "ao/run1/a"
        assert payload["repos"][0]["base"] == "base123"
        assert payload["repos"][0]["squash"] == "squash456"
        assert isinstance(payload["instructions"], str)

    def test_rebase_in_progress_is_never_hardcoded_the_caller_derives_it(
        self, tmp_path: Path
    ) -> None:
        """Review C-1: `rebase_in_progress` is a REQUIRED kwarg, not an internal default --
        the caller must derive it from the live git state (`materialize_conflict`'s own
        result), never assume it."""
        dest = tmp_path / "conflict-1.json"
        escalation.write_conflict_manifest(
            str(dest),
            run_id="r1",
            task_id="a",
            attempt=1,
            task_integration=_ti(),
            task_iso=_task_iso(),
            rebase_in_progress=False,
        )
        payload = json.loads(dest.read_text(encoding="utf-8"))
        assert payload["rebase_in_progress"] is False

    def test_only_ids_paths_refs_and_booleans(self, tmp_path: Path) -> None:
        """AC-2: no value in the manifest is read from a repository FILE's content --
        every value traces back to `TaskIntegrationState`/`TaskIsolation`/ints/strs the
        engine already had, never anything derived from opening a tracked file."""
        dest = tmp_path / "conflict-1.json"
        ti = _ti(conflicted_paths=["a.py"], repos={"core": "/wt/a/core"})
        escalation.write_conflict_manifest(
            str(dest),
            run_id="r1",
            task_id="a",
            attempt=1,
            task_integration=ti,
            task_iso=_task_iso(),
            rebase_in_progress=True,
        )
        payload = json.loads(dest.read_text(encoding="utf-8"))

        def _all_leaf_values(obj: object) -> list[object]:
            if isinstance(obj, dict):
                out: list[object] = []
                for v in obj.values():
                    out.extend(_all_leaf_values(v))
                return out
            if isinstance(obj, list):
                out = []
                for v in obj:
                    out.extend(_all_leaf_values(v))
                return out
            return [obj]

        for value in _all_leaf_values(payload):
            assert isinstance(value, (str, int, bool)) or value is None

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        dest = tmp_path / "nested" / "dirs" / "conflict-1.json"
        escalation.write_conflict_manifest(
            str(dest),
            run_id="r1",
            task_id="a",
            attempt=1,
            task_integration=_ti(),
            task_iso=_task_iso(),
            rebase_in_progress=True,
        )
        assert dest.is_file()


# ---------------------------------------------------------------------------------------
# AC-8 -- previous-patch export (real git, via tests/isolation/conftest.py's make_repo)
# ---------------------------------------------------------------------------------------


class TestExportPreviousPatch:
    def test_patch_exists_and_applies_cleanly_to_the_old_base(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path, files={"f.txt": "a\nb\nc\n"})
        base_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        (repo / "f.txt").write_text("a\nb\nc\nnew-line\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@example.invalid",
                "commit",
                "-q",
                "-m",
                "wk",
            ],
            cwd=repo,
            check=True,
        )
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()

        git = GitRepo(str(repo))
        dest = tmp_path / "previous-1.patch"
        escalation.export_previous_patch(
            str(dest), git, cwd=str(repo), base=base_sha, head=head_sha
        )
        assert dest.is_file()
        patch_text = dest.read_text(encoding="utf-8")
        assert "new-line" in patch_text
        assert "f.txt" in patch_text

        # Applies cleanly against a fresh checkout of the OLD base.
        apply_repo = tmp_path / "apply-target"
        subprocess.run(["git", "clone", "-q", str(repo), str(apply_repo)], check=True)
        subprocess.run(["git", "-C", str(apply_repo), "checkout", "-q", base_sha], check=True)
        result = subprocess.run(
            ["git", "-C", str(apply_repo), "apply", "--check", str(dest)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_never_mutates_head_or_working_tree(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        base_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        git = GitRepo(str(repo))
        escalation.export_previous_patch(
            str(tmp_path / "p.patch"), git, cwd=str(repo), base=base_sha, head=base_sha
        )
        head_after = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        assert head_after == base_sha
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout
        assert status == ""


# ---------------------------------------------------------------------------------------
# merge-resolve.md packaging + untrusted-content framing
# ---------------------------------------------------------------------------------------

_TEMPLATES_ROOT = Path(templates_pkg.__file__).resolve().parent
MERGE_RESOLVE_PATH = _TEMPLATES_ROOT / "builtin" / "instructions" / "merge-resolve.md"


class TestMergeResolveAsset:
    def test_ships_in_the_package(self) -> None:
        assert MERGE_RESOLVE_PATH.is_file()

    def test_instructs_reading_the_manifest_and_resolving_listed_paths_only(self) -> None:
        text = MERGE_RESOLVE_PATH.read_text(encoding="utf-8")
        lowered = text.lower()
        assert "conflict-<n>.json" in text or "conflict manifest" in lowered
        assert "conflicted_paths" in text or "listed" in lowered

    def test_preserves_both_intents_and_forbids_deleting_a_siblings_change(self) -> None:
        text = MERGE_RESOLVE_PATH.read_text(encoding="utf-8").lower()
        assert "both" in text
        assert "never delete" in text or "do not delete" in text

    def test_git_add_each_resolved_path(self) -> None:
        assert "`git add`" in MERGE_RESOLVE_PATH.read_text(encoding="utf-8")

    def test_forbids_continue_commit_push_and_branch_switch(self) -> None:
        text = MERGE_RESOLVE_PATH.read_text(encoding="utf-8").lower()
        assert "rebase --continue" in text
        assert "commit" in text
        assert "push" in text
        assert "switch" in text or "branches" in text

    def test_marks_conflict_content_as_untrusted(self) -> None:
        text = MERGE_RESOLVE_PATH.read_text(encoding="utf-8").lower()
        assert "untrusted" in text

    def test_manifest_instructions_field_also_marks_content_untrusted(self) -> None:
        assert "untrusted" in escalation._RESOLVER_MANIFEST_INSTRUCTIONS.lower()
