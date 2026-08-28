"""Unit + integration tests for general instructions (E-Ui7Kq2 FR-GI1).

The contract under test: instructions declared **once per workspace** reach **every task of
every run**, even when `ao run` is invoked with no general-instruction flag at all. That
"even when not asked for" property is what makes the merge additive rather than a
precedence chain, and it is what most of these tests pin down.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.cli import ENV_GENERAL_INSTRUCTIONS, resolve_general_instructions
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.estimator import HeuristicTokenEstimator
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.executors.prompt import build_prompt
from agent_orchestrator.models import (
    AgentSpec,
    EstimatorConfig,
    RepoRef,
    RepoSet,
    TaskContext,
    TaskSpec,
    WorkflowSpec,
)
from agent_orchestrator.project_config import load_project_config
from agent_orchestrator.runstate import RunStateStore

# ---------------------------------------------------------------------------
# Layer merging (cli.resolve_general_instructions)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_GENERAL_INSTRUCTIONS, raising=False)


@pytest.fixture()
def isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A cwd with no discoverable project config, so layer tests start from empty."""
    workdir = tmp_path / "isolated"
    workdir.mkdir()
    (workdir / ".git").mkdir()  # halts find_project_config's walk-up
    monkeypatch.chdir(workdir)
    return workdir


class TestResolveLayers:
    def test_no_layers_yields_nothing(self, isolated_cwd: Path) -> None:
        assert resolve_general_instructions(None, None) == []

    def test_cli_flags_are_included(self, isolated_cwd: Path) -> None:
        assert resolve_general_instructions(["a.md", "b.md"], None) == ["a.md", "b.md"]

    def test_workflow_declarations_are_included(self, isolated_cwd: Path) -> None:
        assert resolve_general_instructions(None, ["wf.md"]) == ["wf.md"]

    def test_env_var_is_pathsep_separated(
        self, isolated_cwd: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_GENERAL_INSTRUCTIONS, os.pathsep.join(["x.md", "y.md"]))
        assert resolve_general_instructions(None, None) == ["x.md", "y.md"]

    def test_env_var_ignores_blank_segments(
        self, isolated_cwd: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_GENERAL_INSTRUCTIONS, f"a.md{os.pathsep}{os.pathsep}b.md")
        assert resolve_general_instructions(None, None) == ["a.md", "b.md"]

    def test_layers_are_additive_not_a_precedence_chain(
        self, isolated_cwd: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # THE core property. A --general-instruction on the command line must not DROP the
        # workspace's env/config rules — that is the opposite of "applied regardless".
        monkeypatch.setenv(ENV_GENERAL_INSTRUCTIONS, "env.md")
        merged = resolve_general_instructions(["cli.md"], ["wf.md"])
        assert set(merged) == {"env.md", "cli.md", "wf.md"}

    def test_merge_order_is_broadest_scope_first(
        self, isolated_cwd: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_GENERAL_INSTRUCTIONS, "env.md")
        assert resolve_general_instructions(["cli.md"], ["wf.md"]) == [
            "env.md",
            "cli.md",
            "wf.md",
        ]

    def test_duplicates_across_layers_appear_once(
        self, isolated_cwd: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_GENERAL_INSTRUCTIONS, "same.md")
        assert resolve_general_instructions(["same.md"], ["same.md"]) == ["same.md"]

    def test_config_file_layer_is_included_without_any_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The "define once in the workspace" path: no CLI flag, no env var.
        project = tmp_path / "project"
        (project / ".ao").mkdir(parents=True)
        (project / ".git").mkdir()
        (project / ".ao" / "config.yaml").write_text(
            "general_instructions:\n  - rules.md\n", encoding="utf-8"
        )
        monkeypatch.chdir(project)

        # Anchored to the CONFIG FILE's directory (.ao/), matching how workflow/reposets/
        # agents paths in the same file already resolve.
        merged = resolve_general_instructions(None, None)
        assert merged == [str((project / ".ao" / "rules.md").resolve())]


class TestProjectConfigLoading:
    def test_relative_paths_anchor_to_the_config_file(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "proj" / ".ao"
        config_dir.mkdir(parents=True)
        config = config_dir / "config.yaml"
        config.write_text("general_instructions:\n  - ../rules.md\n", encoding="utf-8")

        loaded = load_project_config(config)
        assert loaded.general_instructions == [str((tmp_path / "proj" / "rules.md").resolve())]

    def test_absolute_paths_are_left_alone(self, tmp_path: Path) -> None:
        config = tmp_path / "config.yaml"
        config.write_text("general_instructions:\n  - /abs/rules.md\n", encoding="utf-8")
        assert load_project_config(config).general_instructions == ["/abs/rules.md"]

    def test_absent_key_defaults_to_empty(self, tmp_path: Path) -> None:
        config = tmp_path / "config.yaml"
        config.write_text("model: claude-opus-4-8\n", encoding="utf-8")
        assert load_project_config(config).general_instructions == []


# ---------------------------------------------------------------------------
# Prompt assembly (executors.prompt.build_prompt)
# ---------------------------------------------------------------------------


def _ctx(prompt_template: str, general: list[str]) -> TaskContext:
    return TaskContext(
        run_id="run-1",
        task_id="t1",
        agent=AgentSpec(executor="fake", prompt_template=prompt_template),
        instruction_path="/ws/instr.md",
        general_instruction_paths=general,
        input_paths=["/ws/in.md"],
        output_paths=["/ws/out.md"],
        repo_paths={"main": "/ws/repo"},
        timeout_seconds=60,
    )


class TestPromptAssembly:
    DEFAULT_TEMPLATE = AgentSpec(executor="fake").prompt_template

    def test_no_general_instructions_leaves_the_prompt_untouched(self) -> None:
        # Byte-identical to pre-feature behaviour for every workspace that configures none.
        with_none = build_prompt(_ctx(self.DEFAULT_TEMPLATE, []))
        assert "general instructions" not in with_none

    def test_appends_a_clause_when_the_template_has_no_placeholder(self) -> None:
        # Every agents.json written before this feature — and the AgentSpec default — has
        # no placeholder. A placeholder-only design would silently drop the workspace's
        # rules for exactly those configs.
        prompt = build_prompt(_ctx(self.DEFAULT_TEMPLATE, ["/ws/rules.md"]))
        assert "/ws/rules.md" in prompt
        assert "general instructions" in prompt

    def test_honours_an_explicit_placeholder_without_appending(self) -> None:
        template = "House rules: {general_instructions}. Task: {instruction}."
        prompt = build_prompt(_ctx(template, ["/ws/rules.md"]))
        assert prompt == "House rules: /ws/rules.md. Task: /ws/instr.md."
        assert prompt.count("/ws/rules.md") == 1, "no duplicate clause when positioned"

    def test_multiple_instructions_are_all_named(self) -> None:
        prompt = build_prompt(_ctx(self.DEFAULT_TEMPLATE, ["/ws/a.md", "/ws/b.md"]))
        assert "/ws/a.md" in prompt and "/ws/b.md" in prompt

    def test_other_placeholders_still_render(self) -> None:
        prompt = build_prompt(_ctx(self.DEFAULT_TEMPLATE, ["/ws/rules.md"]))
        assert "/ws/instr.md" in prompt
        assert "/ws/in.md" in prompt
        assert "/ws/out.md" in prompt

    def test_prompt_contains_paths_only_never_file_contents(self, tmp_path: Path) -> None:
        # NFR-1: the executor boundary passes paths, never payloads.
        rules = tmp_path / "rules.md"
        rules.write_text("SECRET RULE CONTENT", encoding="utf-8")
        prompt = build_prompt(_ctx(self.DEFAULT_TEMPLATE, [str(rules)]))
        assert str(rules) in prompt
        assert "SECRET RULE CONTENT" not in prompt


# ---------------------------------------------------------------------------
# Engine integration
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine_fixture(tmp_path: Path):
    """A minimal two-task workflow wired to a FakeExecutor that records prompts."""
    (tmp_path / "instr.md").write_text("do the thing", encoding="utf-8")
    (tmp_path / "rules.md").write_text("house rules", encoding="utf-8")
    (tmp_path / "extra.md").write_text("more rules", encoding="utf-8")

    workflow = WorkflowSpec(
        version="1.0",
        id="gi-demo",
        repo_set="main",
        tasks=[
            TaskSpec(id="first", agent="dev", instruction="instr.md", outputs=["out-1.md"]),
            TaskSpec(
                id="second",
                agent="dev",
                instruction="instr.md",
                outputs=["out-2.md"],
                depends_on=["first"],
            ),
        ],
    )
    reposets = {
        "main": RepoSet(
            repos=[RepoRef(id="repo", path=str(tmp_path))], workspace_root=str(tmp_path)
        )
    }
    agents = {"dev": AgentSpec(executor="fake")}
    return tmp_path, workflow, reposets, agents


def _run(tmp_path: Path, workflow, reposets, agents, general: list[str] | None):
    executor = FakeExecutor()
    store = LocalFsArtifactStore(str(tmp_path))
    orch = Orchestrator(
        executor,
        store,
        RunStateStore(str(tmp_path), store),
        general_instructions=general,
    )
    state = orch.run(workflow, reposets, agents)
    return executor, state


class TestEngineIntegration:
    def test_general_instructions_reach_every_task(self, engine_fixture) -> None:
        tmp_path, workflow, reposets, agents = engine_fixture
        executor, state = _run(tmp_path, workflow, reposets, agents, [str(tmp_path / "rules.md")])

        assert state.status == "succeeded"
        assert set(executor.prompts) == {"first", "second"}
        for task_id, prompt in executor.prompts.items():
            assert str(tmp_path / "rules.md") in prompt, f"{task_id} missed the general rules"

    def test_workflow_declared_instructions_apply_without_any_injection(
        self, engine_fixture
    ) -> None:
        # A direct library user of Orchestrator (no CLI) must still get the workflow layer.
        tmp_path, workflow, reposets, agents = engine_fixture
        workflow.general_instructions = ["rules.md"]

        executor, _ = _run(tmp_path, workflow, reposets, agents, None)
        for prompt in executor.prompts.values():
            assert str(tmp_path / "rules.md") in prompt

    def test_injected_and_workflow_layers_are_merged_and_deduped(self, engine_fixture) -> None:
        tmp_path, workflow, reposets, agents = engine_fixture
        workflow.general_instructions = ["rules.md"]

        # The CLI passes an already-merged list that includes the workflow layer; the
        # engine's own merge must not produce a duplicate from that overlap.
        executor, _ = _run(
            tmp_path, workflow, reposets, agents, ["rules.md", str(tmp_path / "extra.md")]
        )
        prompt = executor.prompts["first"]
        assert prompt.count(str(tmp_path / "rules.md")) == 1
        assert str(tmp_path / "extra.md") in prompt

    def test_paths_are_resolved_through_the_artifact_store(self, engine_fixture) -> None:
        tmp_path, workflow, reposets, agents = engine_fixture
        executor, _ = _run(tmp_path, workflow, reposets, agents, ["rules.md"])
        # Relative in, absolute out — same treatment as task.instruction.
        assert str(tmp_path / "rules.md") in executor.prompts["first"]

    def test_traversal_path_is_dropped_not_fatal(self, engine_fixture) -> None:
        # One bad entry in a workspace-wide config must not fail every task of every
        # workflow; the engine logs and carries on, and `ao validate` reports it up front.
        tmp_path, workflow, reposets, agents = engine_fixture
        executor, state = _run(
            tmp_path, workflow, reposets, agents, ["../../etc/passwd", "rules.md"]
        )

        assert state.status == "succeeded"
        prompt = executor.prompts["first"]
        assert "passwd" not in prompt
        assert str(tmp_path / "rules.md") in prompt

    def test_no_general_instructions_is_byte_identical(self, engine_fixture) -> None:
        tmp_path, workflow, reposets, agents = engine_fixture
        executor, state = _run(tmp_path, workflow, reposets, agents, None)
        assert state.status == "succeeded"
        assert "general instructions" not in executor.prompts["first"]


class TestEstimatorAccounting:
    def test_general_instructions_count_toward_the_token_estimate(self, tmp_path: Path) -> None:
        # They are real input tokens on every task. Omitting them would under-estimate
        # every task in a workspace that configures them, letting the budget gate admit
        # work it cannot afford.
        (tmp_path / "instr.md").write_text("x" * 100, encoding="utf-8")
        (tmp_path / "rules.md").write_text("y" * 4000, encoding="utf-8")

        store = LocalFsArtifactStore(str(tmp_path))
        estimator = HeuristicTokenEstimator(store)
        cfg = EstimatorConfig()

        base = TaskContext(
            run_id="r",
            task_id="t",
            agent=AgentSpec(executor="fake"),
            instruction_path=str(tmp_path / "instr.md"),
            input_paths=[],
            output_paths=[],
            repo_paths={},
            timeout_seconds=60,
        )
        with_rules = base.model_copy(
            update={"general_instruction_paths": [str(tmp_path / "rules.md")]}
        )

        assert estimator.estimate(with_rules, cfg) > estimator.estimate(base, cfg)
