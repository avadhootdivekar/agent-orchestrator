"""Deterministic (no LLM, no network) tests for the committed `ao-epic-plus` bench
subject: a 4-agent plan -> implement -> review -> fix `ao_workflow` DAG (epic
`E-Bt4Xk9-complex-benchmark-tiers`, task `T-Ep8Lq6-ao-epic-plus-subject`, FR-8).

Mirrors `tests/bench/test_dev_core_suite.py`'s own `ao-epic` coverage
(`test_ao_epic_subjects_pin_model_via_subject_not_per_agent` /
`test_ao_epic_workflow_assets_are_valid`) for the harder 4-agent variant: subject
configs load via `load_subject`, `workflow.json` cross-validates via the same core
`agent_orchestrator.spec`/`agent_orchestrator.config` loaders `ao validate` itself uses,
the DAG shape is a linear plan->implement->review->fix chain with artifact-wired
inputs/outputs, and `agents.json` pins NO per-agent `model` anywhere (the uniform-model
sidestep for the model-override-clobber defect, epic risk R6).

Everything here loads committed spec files or invokes the `ao-bench validate` Typer app
directly (`typer.testing.CliRunner`, the outer-boundary e2e pattern `tests/bench/
test_cli_validate.py` already uses for `ao-epic`/`fake` subjects) -- never `claude`,
never a real `uv run` subprocess -- so this file runs unconditionally in the default
(`not real_llm`) test tier, same as the rest of `tests/bench/`.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agent_orchestrator.bench.cli import app
from agent_orchestrator.bench.spec import load_subject

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SUBJECTS_DIR = _REPO_ROOT / "benchmarks" / "subjects"
_AO_EPIC_PLUS_DIR = _SUBJECTS_DIR / "ao-epic-plus"

_EXPECTED_TASK_CHAIN = ["plan", "implement", "review", "fix"]
_EXPECTED_AGENT_BY_TASK = {
    "plan": "planner",
    "implement": "developer",
    "review": "reviewer",
    "fix": "fixer",
}

runner = CliRunner()


# ---------------------------------------------------------------------------
# AC1: `ao-bench validate --subject ...` -> OK, type ao_workflow (CLI e2e boundary)
# ---------------------------------------------------------------------------


def test_validate_cli_accepts_ao_epic_plus_sonnet_subject() -> None:
    result = runner.invoke(
        app, ["validate", "--subject", str(_SUBJECTS_DIR / "ao-epic-plus-sonnet.json")]
    )
    assert result.exit_code == 0, result.output
    assert "type='ao_workflow'" in result.output
    assert "OK" in result.output


def test_validate_cli_accepts_ao_epic_plus_haiku_subject() -> None:
    result = runner.invoke(
        app, ["validate", "--subject", str(_SUBJECTS_DIR / "ao-epic-plus-haiku.json")]
    )
    assert result.exit_code == 0, result.output
    assert "type='ao_workflow'" in result.output
    assert "OK" in result.output


# ---------------------------------------------------------------------------
# Subject spec loading (AC1) + uniform-model wiring (AC3)
# ---------------------------------------------------------------------------


def test_ao_epic_plus_subjects_load_via_load_subject() -> None:
    sonnet = load_subject(_SUBJECTS_DIR / "ao-epic-plus-sonnet.json")
    haiku = load_subject(_SUBJECTS_DIR / "ao-epic-plus-haiku.json")

    assert sonnet.id == "ao-epic-plus-sonnet"
    assert haiku.id == "ao-epic-plus-haiku"
    assert sonnet.type == haiku.type == "ao_workflow"
    assert sonnet.model == "claude-sonnet-5"
    assert haiku.model == "claude-haiku-4-5-20251001"
    assert sonnet.max_turns == 30

    # Both variants point at the SAME committed workflow/agents/reposet template --
    # they differ only by model (uniform-model, AC3).
    assert sonnet.workflow == haiku.workflow == "ao-epic-plus/workflow.json"
    assert sonnet.reposets == haiku.reposets == "ao-epic-plus/reposet.json"
    assert sonnet.agents == haiku.agents == "ao-epic-plus/agents.json"


def test_ao_epic_plus_agents_pin_no_per_agent_model() -> None:
    """AC3 (grep-asserted): no agent in agents.json sets its own `model` -- the
    subject's model flows only via `AO_MODEL` (bench/subjects.py's `AoWorkflowSubject`),
    sidestepping the known model-override-clobber defect (epic risk R6)."""
    agents_data = json.loads((_AO_EPIC_PLUS_DIR / "agents.json").read_text())
    assert set(agents_data["agents"]) == {"planner", "developer", "reviewer", "fixer"}
    for agent_name, agent_spec in agents_data["agents"].items():
        assert "model" not in agent_spec, (
            f"agents.json agent {agent_name!r} must not pin its own model "
            "(uniform-model clobber safety, epic R6)"
        )


# ---------------------------------------------------------------------------
# AC2: 4-task DAG shape, instruction path, artifact wiring
# ---------------------------------------------------------------------------


def test_ao_epic_plus_workflow_assets_cross_validate() -> None:
    """The ao-epic-plus subject's own workflow/reposet/agents template cross-validates
    via core `ao validate` semantics (spec.cross_validate) -- exercised directly here
    (network-free) rather than shelling to `uv run ao validate`."""
    from agent_orchestrator.config import load_agents, load_reposets
    from agent_orchestrator.spec import cross_validate, load_workflow

    wf = load_workflow(_AO_EPIC_PLUS_DIR / "workflow.json")
    reposet_map = load_reposets(_AO_EPIC_PLUS_DIR / "reposet.json")
    agent_map = load_agents(_AO_EPIC_PLUS_DIR / "agents.json")
    cross_validate(wf, reposet_map, agent_map)  # raises SpecValidationError on failure

    assert wf.id == wf.id.lower()
    assert [t.id for t in wf.tasks] == _EXPECTED_TASK_CHAIN
    # Every task reads the bench task's OWN materialized instruction (resolved against
    # the ephemeral per-run workspace, not this committed directory -- see
    # instructions/plan.md's module docstring for why).
    assert all(t.instruction == "repo/INSTRUCTION.md" for t in wf.tasks)
    assert all(agent_map[t.agent].working_dir == "repo" for t in wf.tasks)
    # bypassPermissions (AC2) lives in each agent's command_template, mirroring ao-epic.
    for agent in agent_map.values():
        assert "--permission-mode" in agent.command_template
        assert "bypassPermissions" in agent.command_template


def test_ao_epic_plus_workflow_task_agent_mapping_and_dependency_chain() -> None:
    """AC2: plan (no deps) -> implement (deps plan) -> review (deps implement) ->
    fix (deps review), each mapped to its own agent role."""
    from agent_orchestrator.spec import load_workflow

    wf = load_workflow(_AO_EPIC_PLUS_DIR / "workflow.json")
    by_id = {t.id: t for t in wf.tasks}

    for task_id, expected_agent in _EXPECTED_AGENT_BY_TASK.items():
        assert by_id[task_id].agent == expected_agent

    assert by_id["plan"].depends_on == []
    assert by_id["implement"].depends_on == ["plan"]
    assert by_id["review"].depends_on == ["implement"]
    assert by_id["fix"].depends_on == ["review"]


def test_ao_epic_plus_workflow_artifacts_wire_the_verify_repair_chain() -> None:
    """AC2/epic risk callout: `review` must consume `implement`'s output and `fix` must
    consume `review`'s findings -- i.e. the DAG is a real verify/repair loop, not four
    independent turns. Every artifact path is under `output/*.md` (per-run workspace)."""
    from agent_orchestrator.spec import load_workflow

    wf = load_workflow(_AO_EPIC_PLUS_DIR / "workflow.json")
    by_id = {t.id: t for t in wf.tasks}

    assert by_id["plan"].inputs == []
    assert by_id["plan"].outputs == ["output/plan.md"]
    assert by_id["implement"].inputs == by_id["plan"].outputs
    assert by_id["implement"].outputs == ["output/impl.md"]
    assert by_id["review"].inputs == by_id["implement"].outputs
    assert by_id["review"].outputs == ["output/review.md"]
    assert by_id["fix"].inputs == by_id["review"].outputs
    assert by_id["fix"].outputs == ["output/fix.md"]

    for t in wf.tasks:
        for path in (*t.inputs, *t.outputs):
            assert path.startswith("output/") and path.endswith(".md")


def test_ao_epic_plus_workflow_has_no_cycles_and_unique_task_ids() -> None:
    """DAG correctness (CLAUDE.md orchestration rule): reject cycles, stable/unique ids."""
    from agent_orchestrator.spec import load_workflow

    wf = load_workflow(_AO_EPIC_PLUS_DIR / "workflow.json")
    ids = [t.id for t in wf.tasks]
    assert len(ids) == len(set(ids)) == 4

    # Manual topological check: every depends_on target must appear strictly earlier
    # in the declared task order (a stronger, deterministic guarantee than merely
    # "no cycles" -- matches this file's own authored linear-chain shape).
    seen: set[str] = set()
    for t in wf.tasks:
        for dep in t.depends_on:
            assert dep in seen, f"task {t.id!r} depends_on {dep!r} which is not yet defined"
        seen.add(t.id)


# ---------------------------------------------------------------------------
# AC4: agents.json prompt_template vs instructions/*.md kept in sync
# ---------------------------------------------------------------------------


def test_ao_epic_plus_instructions_mirror_and_document_agents_json_authority() -> None:
    """AC4: every instructions/*.md file exists for each of the 4 roles and explicitly
    documents that `agents.json`'s `prompt_template` (not this file) is authoritative --
    the same convention `ao-epic/instructions/{implement,verify}.md` already established."""
    for task_id, role in _EXPECTED_AGENT_BY_TASK.items():
        md_path = _AO_EPIC_PLUS_DIR / "instructions" / f"{task_id}.md"
        assert md_path.is_file(), f"missing instructions/{task_id}.md for role {role!r}"
        text = md_path.read_text()
        assert "authoritative" in text.lower()
        assert "agents.json" in text
        assert role in text


# ---------------------------------------------------------------------------
# AC6: reviewer/fixer run the repo's own visible tests, never touch grading scripts
# ---------------------------------------------------------------------------


def test_reviewer_and_fixer_prompts_require_running_tests_and_forbid_grading_edits() -> None:
    agents_data = json.loads((_AO_EPIC_PLUS_DIR / "agents.json").read_text())
    for role in ("reviewer", "fixer"):
        prompt = agents_data["agents"][role]["prompt_template"]
        assert "pytest" in prompt, f"{role} prompt must instruct running the repo's own tests"
        assert "check.py" in prompt and "check_tests.py" in prompt, (
            f"{role} prompt must name the grading/check scripts it must not touch"
        )
        assert "do not modify" in prompt.lower() or "do not" in prompt.lower()


def test_code_touching_agents_forbid_modifying_instruction_or_grading_scripts() -> None:
    """Same guardrail as `ao-epic` (epic FR-8 AC6): every agent that can touch repo code
    (developer/reviewer/fixer) must not modify the instruction file or any grading/check
    script. `planner` is exempt -- by design it makes no code changes at all (it only
    writes the plan artifact), so the guardrail is meaningless for that role."""
    agents_data = json.loads((_AO_EPIC_PLUS_DIR / "agents.json").read_text())
    for role in ("developer", "reviewer", "fixer"):
        prompt = agents_data["agents"][role]["prompt_template"]
        assert "{instruction}" in prompt
        assert "check.py" in prompt
        assert "check_tests.py" in prompt
