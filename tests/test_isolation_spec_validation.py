"""Tests for T-Sc7Rm2 — cross-validation rules V1-V12 (E-Wk9Tz3, HLD §10.4).

Each rule gets a happy-path test and at least one failure test. V1/V2/V3/V7/V8/V10/V11
only evaluate when isolation is "active" for the workflow -- `spec._any_task_isolated`:
`defaults.isolation == "worktree"` OR at least one static task resolves to isolation=
"worktree" (see `spec.validate_isolation`'s docstring for why the unconditional
alternative is wrong: IntegrationSpec's own ladder default already includes "llm", so
evaluating those rules unconditionally would fatal on resolver_agent for every
non-isolated workflow, breaking NFR-2/NFR-5). Tests that exercise those rules therefore
isolate at least one task (or set `defaults.isolation="worktree"`) and, unless the test is
specifically about the ladder/resolver relationship, set a ladder without "llm" to avoid
tripping V2/V3/V10/V11 as a side effect.

C-3: `cross_validate` RETURNS its non-fatal warnings (V4/V5/V7/V10) rather than logging
them -- `ao validate` attaches no log handler for the package logger, so a bare
`logger.warning` call there was invisible. Tests assert against the returned list, not
`caplog`.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_orchestrator.cli import app
from agent_orchestrator.errors import SpecValidationError
from agent_orchestrator.models import AgentSpec, RepoRef, RepoSet, TaskSpec, WorkflowSpec
from agent_orchestrator.spec import cross_validate

runner = CliRunner()

# A ladder that never requires a resolver_agent -- used by tests whose subject is NOT the
# ladder/resolver_agent relationship itself, so they don't trip V2/V3/V10/V11 as a
# side effect of the (llm-including) IntegrationSpec.ladder default.
_NO_LLM_LADDER = ["auto", "mechanical", "rerun"]


def _agents(**overrides: AgentSpec) -> dict:
    base: dict = {"ag": AgentSpec(executor="fake")}
    base.update(overrides)
    return base


def _reposets(workspace: str) -> dict:
    return {
        "rs": RepoSet(
            workspace_root=workspace,
            repos=[RepoRef(id="core", path=".", role="primary")],
        )
    }


def _isolated_task(**overrides: object) -> TaskSpec:
    base: dict = {"id": "t1", "agent": "ag", "instruction": "i.md", "isolation": "worktree"}
    base.update(overrides)
    return TaskSpec(**base)


def _plain_task(**overrides: object) -> TaskSpec:
    base: dict = {"id": "t1", "agent": "ag", "instruction": "i.md"}
    base.update(overrides)
    return TaskSpec(**base)


def _wf(tasks: list[TaskSpec], **overrides: object) -> WorkflowSpec:
    base: dict = {"version": "1.0", "id": "wf", "repo_set": "rs", "tasks": tasks}
    base.update(overrides)
    return WorkflowSpec(**base)


def _resolver_agent(*disallowed: str) -> AgentSpec:
    return AgentSpec(executor="fake", disallowed_tools=list(disallowed))


# ---------------------------------------------------------------------------
# V1 — integration.strategy == "merge" is reserved -- fatal.
# ---------------------------------------------------------------------------


class TestV1MergeStrategyReserved:
    def test_merge_strategy_fatal(self, tmp_path: Path) -> None:
        wf = _wf(
            [_isolated_task()],
            integration={"strategy": "merge", "ladder": _NO_LLM_LADDER},
        )
        with pytest.raises(SpecValidationError, match="strategy='merge'"):
            cross_validate(wf, _reposets(str(tmp_path)), _agents())

    def test_rebase_strategy_ok(self, tmp_path: Path) -> None:
        wf = _wf(
            [_isolated_task()],
            integration={"strategy": "rebase", "ladder": _NO_LLM_LADDER},
        )
        cross_validate(wf, _reposets(str(tmp_path)), _agents())  # no raise


# ---------------------------------------------------------------------------
# V2 — "llm" in ladder requires resolver_agent -- fatal.
# ---------------------------------------------------------------------------


class TestV2LlmRequiresResolverAgent:
    def test_llm_in_default_ladder_without_resolver_agent_fatal(self, tmp_path: Path) -> None:
        wf = _wf([_isolated_task()])  # default integration: ladder includes "llm"
        with pytest.raises(SpecValidationError, match="resolver_agent"):
            cross_validate(wf, _reposets(str(tmp_path)), _agents())

    def test_defaults_isolation_worktree_with_only_structural_static_tasks_still_gates(
        self, tmp_path: Path
    ) -> None:
        """C-2 gap 1 regression: the ONLY static task is emit_tasks (forced to
        isolation='none' by resolve_task_isolation), but `defaults.isolation='worktree'`
        means every emit_tasks-injected child will isolate at runtime. V1-V3/V7/V8/V10/V11
        must still run (proven here by V2 firing) -- an earlier version of the
        `_any_task_isolated` gate checked only `workflow.tasks`' resolved isolation and
        missed this shape entirely, silently letting an invalid `integration` config
        (default ladder includes "llm", no resolver_agent) pass `ao validate`."""
        task = _plain_task(emit_tasks=True, task_manifest_path="m.json")
        wf = _wf([task], defaults={"isolation": "worktree"})
        with pytest.raises(SpecValidationError, match="resolver_agent"):
            cross_validate(wf, _reposets(str(tmp_path)), _agents())

    def test_defaults_isolation_none_with_only_structural_static_tasks_does_not_gate(
        self, tmp_path: Path
    ) -> None:
        """Control for the above: defaults.isolation='none' (the byte-identical default)
        with the same all-structural static graph must NOT gate -- nothing ever isolates,
        so the invalid default-ladder-without-resolver_agent config is correctly inert."""
        task = _plain_task(emit_tasks=True, task_manifest_path="m.json")
        wf = _wf([task])  # defaults.isolation defaults to "none"
        cross_validate(wf, _reposets(str(tmp_path)), _agents())  # no raise

    def test_llm_with_resolver_agent_set_ok(self, tmp_path: Path) -> None:
        wf = _wf([_isolated_task()], integration={"resolver_agent": "resolver"})
        cross_validate(
            wf,
            _reposets(str(tmp_path)),
            _agents(resolver=_resolver_agent("WebFetch", "WebSearch")),
        )


# ---------------------------------------------------------------------------
# V3 — resolver_agent, when set, must be a known agent -- fatal.
# ---------------------------------------------------------------------------


class TestV3UnknownResolverAgent:
    def test_unknown_resolver_agent_fatal(self, tmp_path: Path) -> None:
        wf = _wf(
            [_isolated_task()],
            integration={"resolver_agent": "nope", "ladder": _NO_LLM_LADDER},
        )
        with pytest.raises(SpecValidationError, match="not a known agent"):
            cross_validate(wf, _reposets(str(tmp_path)), _agents())

    def test_known_resolver_agent_ok(self, tmp_path: Path) -> None:
        wf = _wf(
            [_isolated_task()],
            integration={"resolver_agent": "resolver", "ladder": _NO_LLM_LADDER},
        )
        cross_validate(
            wf,
            _reposets(str(tmp_path)),
            _agents(resolver=_resolver_agent("WebFetch", "WebSearch")),
        )


# ---------------------------------------------------------------------------
# V4 — structural task (emit_tasks/router/loop-gate) declaring worktree -- warning.
# ---------------------------------------------------------------------------


class TestV4StructuralTaskWorktreeWarns:
    def test_emit_tasks_task_with_worktree_isolation_warns(self, tmp_path: Path) -> None:
        task = _plain_task(isolation="worktree", emit_tasks=True, task_manifest_path="m.json")
        wf = _wf([task])
        warnings = cross_validate(wf, _reposets(str(tmp_path)), _agents())
        assert any("emit_tasks/router/loop-gate" in m for m in warnings)

    def test_non_structural_task_with_worktree_isolation_no_v4_warning(
        self, tmp_path: Path
    ) -> None:
        wf = _wf([_isolated_task()], integration={"ladder": _NO_LLM_LADDER})
        warnings = cross_validate(wf, _reposets(str(tmp_path)), _agents())
        assert not any("emit_tasks/router/loop-gate" in m for m in warnings)


# ---------------------------------------------------------------------------
# V5 — integration.* configured but nothing isolated -- warning.
# ---------------------------------------------------------------------------


class TestV5IntegrationConfiguredButUnused:
    # Distinct substring from V7's own "<field>=... has no effect" wording, which would
    # otherwise false-positive-match these assertions (max_resolver_attempts defaults to 1).
    _V5_PHRASE = "integration config has no effect"

    def test_configured_with_no_isolated_task_warns(self, tmp_path: Path) -> None:
        wf = _wf([_plain_task()], integration={"auto_commit": False})
        warnings = cross_validate(wf, _reposets(str(tmp_path)), _agents())
        assert any(self._V5_PHRASE in m for m in warnings)

    def test_configured_with_isolated_task_no_warning(self, tmp_path: Path) -> None:
        wf = _wf(
            [_isolated_task()],
            integration={
                "auto_commit": False,
                "ladder": _NO_LLM_LADDER,
                "max_resolver_attempts": 0,  # avoid an unrelated V7 warning
            },
        )
        warnings = cross_validate(wf, _reposets(str(tmp_path)), _agents())
        assert not any(self._V5_PHRASE in m for m in warnings)

    def test_default_integration_with_no_isolated_task_no_warning(self, tmp_path: Path) -> None:
        """Untouched integration block (== IntegrationSpec()) must never warn -- this is
        the "byte-identical for an unrelated existing spec" case."""
        wf = _wf([_plain_task()])
        warnings = cross_validate(wf, _reposets(str(tmp_path)), _agents())
        assert not any(self._V5_PHRASE in m for m in warnings)

    def test_configured_with_defaults_isolation_worktree_and_only_structural_static_tasks(
        self, tmp_path: Path
    ) -> None:
        """C-2 gap 1: defaults.isolation='worktree' with an ALL-STRUCTURAL static graph
        (a single emit_tasks task, forced to isolation='none') must still count as
        "isolation active" -- every emit_tasks-injected child defaults to isolation=
        "inherit" -> "worktree" at runtime. V5 must NOT warn here (integration genuinely
        has an effect), and V1-V3/V7/V8/V10/V11 must actually run (proven by the sibling
        TestV2 case in this module using the identical shape)."""
        task = _plain_task(emit_tasks=True, task_manifest_path="m.json")
        wf = _wf(
            [task],
            defaults={"isolation": "worktree"},
            integration={"auto_commit": False, "ladder": _NO_LLM_LADDER},
        )
        warnings = cross_validate(wf, _reposets(str(tmp_path)), _agents())
        assert not any(self._V5_PHRASE in m for m in warnings)


# ---------------------------------------------------------------------------
# V6 — touches entries must be workspace-relative -- fatal.
# ---------------------------------------------------------------------------


class TestV6TouchesUnsafeGlob:
    @pytest.mark.parametrize("bad_glob", ["/abs/path", "../escape", "src/../../x"])
    def test_unsafe_touches_glob_fatal(self, tmp_path: Path, bad_glob: str) -> None:
        wf = _wf([_plain_task(touches=[bad_glob])])
        with pytest.raises(SpecValidationError, match="touches"):
            cross_validate(wf, _reposets(str(tmp_path)), _agents())

    def test_safe_touches_glob_ok(self, tmp_path: Path) -> None:
        wf = _wf([_plain_task(touches=["src/**/*.py"])])
        cross_validate(wf, _reposets(str(tmp_path)), _agents())

    def test_empty_touches_entry_is_not_flagged_unsafe(self, tmp_path: Path) -> None:
        wf = _wf([_plain_task(touches=[""])])
        cross_validate(wf, _reposets(str(tmp_path)), _agents())  # no raise

    def test_leading_slash_flagged_regardless_of_host_platform(self, tmp_path: Path) -> None:
        """C-6: the absolute-path check uses `posixpath.isabs` (platform-independent),
        not `os.path.isabs` (host-platform-dependent) -- a leading-'/' glob must be
        flagged the same way whether `ao validate` runs on Linux, macOS, or Windows."""
        wf = _wf([_plain_task(touches=["/etc/passwd"])])
        with pytest.raises(SpecValidationError, match="touches"):
            cross_validate(wf, _reposets(str(tmp_path)), _agents())


# ---------------------------------------------------------------------------
# V7 — max_resolver_attempts > 0 while "llm" absent from ladder -- warning.
# ---------------------------------------------------------------------------


class TestV7MaxResolverAttemptsWithoutLlm:
    def test_warns_when_llm_absent(self, tmp_path: Path) -> None:
        wf = _wf(
            [_isolated_task()],
            integration={"ladder": _NO_LLM_LADDER, "max_resolver_attempts": 1},
        )
        warnings = cross_validate(wf, _reposets(str(tmp_path)), _agents())
        assert any("max_resolver_attempts" in m for m in warnings)

    def test_no_warning_when_max_resolver_attempts_zero(self, tmp_path: Path) -> None:
        wf = _wf(
            [_isolated_task()],
            integration={"ladder": _NO_LLM_LADDER, "max_resolver_attempts": 0},
        )
        warnings = cross_validate(wf, _reposets(str(tmp_path)), _agents())
        assert not any("max_resolver_attempts" in m for m in warnings)


# ---------------------------------------------------------------------------
# V8 — a RepoRef path inside a pre-existing, on-disk git worktree of another
# reposet member -- fatal. Only probed when isolation is active.
# ---------------------------------------------------------------------------


def _git(*args: str, cwd: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_repo_with_commit(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git("init", cwd=str(path))
    _git("config", "user.email", "t@example.com", cwd=str(path))
    _git("config", "user.name", "tester", cwd=str(path))
    (path / "f.txt").write_text("x")
    _git("add", "-A", cwd=str(path))
    _git("commit", "-m", "init", cwd=str(path))


class TestV8ForeignWorktreeCollision:
    def test_repo_and_its_own_linked_worktree_collide_fatal(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo_with_commit(repo)
        wt = tmp_path / "repo-wt"
        _git("worktree", "add", "-b", "wtbranch", str(wt), cwd=str(repo))

        reposets = {
            "rs": RepoSet(
                workspace_root=str(tmp_path),
                repos=[
                    RepoRef(id="core", path=str(repo), role="primary"),
                    RepoRef(id="linked", path=str(wt), role="support"),
                ],
            )
        }
        wf = _wf([_isolated_task()], integration={"ladder": _NO_LLM_LADDER})
        with pytest.raises(SpecValidationError, match="pre-existing, on-disk git worktree"):
            cross_validate(wf, reposets, _agents())

    def test_independent_repos_do_not_collide(self, tmp_path: Path) -> None:
        repo_a = tmp_path / "a"
        repo_b = tmp_path / "b"
        _init_repo_with_commit(repo_a)
        _init_repo_with_commit(repo_b)
        reposets = {
            "rs": RepoSet(
                workspace_root=str(tmp_path),
                repos=[RepoRef(id="a", path=str(repo_a)), RepoRef(id="b", path=str(repo_b))],
            )
        }
        wf = _wf([_isolated_task()], integration={"ladder": _NO_LLM_LADDER})
        cross_validate(wf, reposets, _agents())  # no raise

    def test_subdirectory_of_same_repo_does_not_collide(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo_with_commit(repo)
        sub = repo / "docs"
        sub.mkdir()
        reposets = {
            "rs": RepoSet(
                workspace_root=str(tmp_path),
                repos=[RepoRef(id="core", path=str(repo)), RepoRef(id="docs", path=str(sub))],
            )
        }
        wf = _wf([_isolated_task()], integration={"ladder": _NO_LLM_LADDER})
        cross_validate(wf, reposets, _agents())  # no raise -- ordinary same-repo nesting

    def test_not_probed_when_no_task_isolated(self, tmp_path: Path) -> None:
        """A foreign-worktree collision on disk is irrelevant when isolation never
        engages -- V8 must not fire (and must not even probe the filesystem)."""
        repo = tmp_path / "repo"
        _init_repo_with_commit(repo)
        wt = tmp_path / "repo-wt"
        _git("worktree", "add", "-b", "wtbranch", str(wt), cwd=str(repo))
        reposets = {
            "rs": RepoSet(
                workspace_root=str(tmp_path),
                repos=[
                    RepoRef(id="core", path=str(repo), role="primary"),
                    RepoRef(id="linked", path=str(wt), role="support"),
                ],
            )
        }
        wf = _wf([_plain_task()])  # isolation resolves to "none"
        cross_validate(wf, reposets, _agents())  # no raise

    def test_non_git_reporef_is_skipped_gracefully(self, tmp_path: Path) -> None:
        """A RepoRef that isn't a git repo at all (`git rev-parse` exits non-zero) must be
        skipped, not raise -- V8 can only ever fire a false positive, never block a
        legitimate non-git workspace member."""
        repo = tmp_path / "repo"
        _init_repo_with_commit(repo)
        not_a_repo = tmp_path / "plain-dir"
        not_a_repo.mkdir()
        reposets = {
            "rs": RepoSet(
                workspace_root=str(tmp_path),
                repos=[
                    RepoRef(id="core", path=str(repo)),
                    RepoRef(id="plain", path=str(not_a_repo)),
                ],
            )
        }
        wf = _wf([_isolated_task()], integration={"ladder": _NO_LLM_LADDER})
        cross_validate(wf, reposets, _agents())  # no raise

    def test_reporef_path_does_not_exist_is_skipped_gracefully(self, tmp_path: Path) -> None:
        """A RepoRef path that doesn't exist on disk yet must be skipped (the git probe's
        subprocess raises FileNotFoundError resolving `cwd`), not raise or crash
        `ao validate`."""
        repo = tmp_path / "repo"
        _init_repo_with_commit(repo)
        missing = tmp_path / "does-not-exist-yet"
        reposets = {
            "rs": RepoSet(
                workspace_root=str(tmp_path),
                repos=[
                    RepoRef(id="core", path=str(repo)),
                    RepoRef(id="missing", path=str(missing)),
                ],
            )
        }
        wf = _wf([_isolated_task()], integration={"ladder": _NO_LLM_LADDER})
        cross_validate(wf, reposets, _agents())  # no raise


# ---------------------------------------------------------------------------
# V9 — a task id sanitizing to the reserved component "integration" -- fatal.
# ---------------------------------------------------------------------------


class TestV9ReservedTaskId:
    @pytest.mark.parametrize("bad_id", ["integration", "Integration", "INTEGRATION"])
    def test_reserved_task_id_fatal(self, tmp_path: Path, bad_id: str) -> None:
        wf = _wf([TaskSpec(id=bad_id, agent="ag", instruction="i.md")])
        with pytest.raises(SpecValidationError, match="reserved component"):
            cross_validate(wf, _reposets(str(tmp_path)), _agents())

    def test_non_reserved_task_id_ok(self, tmp_path: Path) -> None:
        wf = _wf([TaskSpec(id="integrate-step", agent="ag", instruction="i.md")])
        cross_validate(wf, _reposets(str(tmp_path)), _agents())  # no raise


# ---------------------------------------------------------------------------
# V10 — resolver_agent's own disallowed_tools missing the force-injected set -- warning.
# ---------------------------------------------------------------------------


class TestV10ResolverAgentMissingDisallowedToolsWarns:
    def test_agent_missing_required_disallowed_tools_warns(self, tmp_path: Path) -> None:
        wf = _wf([_isolated_task()], integration={"resolver_agent": "resolver"})
        warnings = cross_validate(
            wf,
            _reposets(str(tmp_path)),
            _agents(resolver=AgentSpec(executor="fake", disallowed_tools=[])),
        )
        assert any("disallowed_tools" in m for m in warnings)

    def test_agent_covering_disallowed_tools_no_warning(self, tmp_path: Path) -> None:
        wf = _wf([_isolated_task()], integration={"resolver_agent": "resolver"})
        warnings = cross_validate(
            wf,
            _reposets(str(tmp_path)),
            _agents(
                resolver=AgentSpec(
                    executor="fake", disallowed_tools=["WebFetch", "WebSearch", "Bash"]
                )
            ),
        )
        assert not any("disallowed_tools" in m for m in warnings)


# ---------------------------------------------------------------------------
# V11 — resolver_disallowed_tools == [] while "llm" in ladder -- fatal.
# ---------------------------------------------------------------------------


class TestV11EmptyResolverDisallowedToolsFatal:
    def test_empty_resolver_disallowed_tools_with_llm_fatal(self, tmp_path: Path) -> None:
        wf = _wf(
            [_isolated_task()],
            integration={"resolver_agent": "resolver", "resolver_disallowed_tools": []},
        )
        with pytest.raises(SpecValidationError, match="resolver_disallowed_tools"):
            cross_validate(wf, _reposets(str(tmp_path)), _agents(resolver=_resolver_agent()))

    def test_non_empty_resolver_disallowed_tools_ok(self, tmp_path: Path) -> None:
        wf = _wf(
            [_isolated_task()],
            integration={"resolver_agent": "resolver", "resolver_disallowed_tools": ["WebFetch"]},
        )
        cross_validate(
            wf,
            _reposets(str(tmp_path)),
            _agents(resolver=AgentSpec(executor="fake", disallowed_tools=["WebFetch"])),
        )


# ---------------------------------------------------------------------------
# V12 — commit_denylist / resolvers.union globs must be workspace-relative -- fatal.
# ---------------------------------------------------------------------------


class TestV12CommitDenylistAndUnionUnsafeGlobs:
    def test_unsafe_commit_denylist_glob_fatal(self, tmp_path: Path) -> None:
        wf = _wf([_plain_task()], integration={"commit_denylist": ["../escape"]})
        with pytest.raises(SpecValidationError, match="commit_denylist"):
            cross_validate(wf, _reposets(str(tmp_path)), _agents())

    def test_unsafe_resolvers_union_glob_fatal(self, tmp_path: Path) -> None:
        wf = _wf([_plain_task()], integration={"resolvers": {"union": ["/abs"]}})
        with pytest.raises(SpecValidationError, match="resolvers.union"):
            cross_validate(wf, _reposets(str(tmp_path)), _agents())

    def test_safe_globs_ok(self, tmp_path: Path) -> None:
        wf = _wf(
            [_plain_task()],
            integration={
                "commit_denylist": [".env"],
                "resolvers": {"union": ["package-lock.json"]},
            },
        )
        cross_validate(wf, _reposets(str(tmp_path)), _agents())  # no raise


# ---------------------------------------------------------------------------
# e2e: `ao validate` via CliRunner (outermost boundary per CLAUDE.md).
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data))
    return path


def _write_reposets(tmp_path: Path) -> Path:
    return _write_json(
        tmp_path / "reposets.json",
        {
            "version": "1.0",
            "repo_sets": {
                "rs": {
                    "workspace_root": str(tmp_path),
                    "repos": [{"id": "core", "path": ".", "role": "primary"}],
                }
            },
        },
    )


def _write_agents(tmp_path: Path) -> Path:
    return _write_json(
        tmp_path / "agents.json", {"version": "1.0", "agents": {"ag": {"executor": "fake"}}}
    )


class TestValidateCliBoundary:
    def test_ao_validate_accepts_a_valid_isolation_spec(self, tmp_path: Path) -> None:
        instr_dir = tmp_path / "specs" / "instructions"
        instr_dir.mkdir(parents=True)
        (instr_dir / "t1.md").write_text("do the thing")

        wf = _write_json(
            tmp_path / "workflow.json",
            {
                "version": "1.0",
                "id": "iso-wf",
                "repo_set": "rs",
                "defaults": {"isolation": "worktree"},
                "tasks": [
                    {
                        "id": "t1",
                        "agent": "ag",
                        "instruction": "specs/instructions/t1.md",
                        "touches": ["src/**"],
                    }
                ],
                "integration": {"ladder": _NO_LLM_LADDER},
            },
        )
        rs = _write_reposets(tmp_path)
        ag = _write_agents(tmp_path)

        result = runner.invoke(
            app,
            ["validate", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )

        assert result.exit_code == 0, result.output
        assert "OK" in result.output

    def test_ao_validate_rejects_a_fatal_isolation_rule_violation(self, tmp_path: Path) -> None:
        instr_dir = tmp_path / "specs" / "instructions"
        instr_dir.mkdir(parents=True)
        (instr_dir / "t1.md").write_text("do the thing")

        wf = _write_json(
            tmp_path / "workflow.json",
            {
                "version": "1.0",
                "id": "iso-wf",
                "repo_set": "rs",
                "tasks": [
                    {
                        "id": "t1",
                        "agent": "ag",
                        "instruction": "specs/instructions/t1.md",
                        "isolation": "worktree",
                    }
                ],
                # Default ladder includes "llm" with no resolver_agent set -> V2 fatal.
            },
        )
        rs = _write_reposets(tmp_path)
        ag = _write_agents(tmp_path)

        result = runner.invoke(
            app,
            ["validate", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )

        assert result.exit_code == 1
        assert "ERROR" in result.output
        assert "resolver_agent" in result.output

    def test_ao_validate_prints_a_v5_warning_with_the_established_label(
        self, tmp_path: Path
    ) -> None:
        """C-3: `ao validate` has no log handler attached for the package logger, so a
        bare `logger.warning` call there was invisible. `cross_validate`'s returned
        warnings must reach the operator via the SAME `WARNING: <text>` convention
        `validate_run_control`'s own warnings already use."""
        instr_dir = tmp_path / "specs" / "instructions"
        instr_dir.mkdir(parents=True)
        (instr_dir / "t1.md").write_text("do the thing")

        wf = _write_json(
            tmp_path / "workflow.json",
            {
                "version": "1.0",
                "id": "iso-wf",
                "repo_set": "rs",
                "tasks": [{"id": "t1", "agent": "ag", "instruction": "specs/instructions/t1.md"}],
                # No task isolates; integration is configured anyway -> V5 warning.
                "integration": {"auto_commit": False},
            },
        )
        rs = _write_reposets(tmp_path)
        ag = _write_agents(tmp_path)

        result = runner.invoke(
            app,
            ["validate", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )

        assert result.exit_code == 0, result.output
        assert "WARNING: workflow.integration is configured" in result.output
        assert "integration config has no effect" in result.output
