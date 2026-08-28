"""Static sanity checks for the built-in `routed-runner` workflow template (T-Tb3rtr).

These tests deliberately do NOT import `agent_orchestrator.templates` (the templates
module owned by the concurrent T-Tc0r3a task may not exist yet) -- they only inspect
the on-disk template tree under
`src/agent_orchestrator/templates/builtin/routed-runner/` against the manifest
contract in `docs-md/workflow-templates-hld.md` §2.2.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = _REPO_ROOT / "src" / "agent_orchestrator" / "templates" / "builtin" / "routed-runner"
MANIFEST_PATH = TEMPLATE_DIR / "template.yaml"

# The only variables `{{ ... }}` tokens may reference anywhere in this template's
# rendered files (HLD §2.2: "Rendering: `{{ var }}` substitution ... over these
# variables only").
ALLOWED_VARIABLES = frozenset(
    {
        "id",
        "slug",
        "instance_dir",
        "workspace_root",
        "params.type",
        "params.repo_set",
        "params.task_budget_usd",
        "params.run_budget_usd",
    }
)

_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")

# The exact task-id set the finplan generator's embedded Python (new-epic-run.sh)
# produces for its static `tasks` list (head + bug + epic + task + doc + testing +
# per-route push sinks). Dynamically-injected epic-fanout tasks are NOT part of this
# set -- they don't exist until `task-breakdown` runs.
EXPECTED_TASK_IDS = frozenset(
    {
        "git-branch-off",
        "classify",
        "bug-triage",
        "bug-fix",
        "bug-test",
        "bug-review",
        "refine-requirements",
        "market-survey",
        "design-draft",
        "design-review",
        "design-final",
        "task-breakdown",
        "full-test",
        "task-plan",
        "task-impl",
        "task-test",
        "task-review",
        "task-fix",
        "task-retest",
        "doc-plan",
        "doc-write",
        "doc-review",
        "test-gap-analysis",
        "test-write",
        "test-run",
        "bug-push",
        "epic-push",
        "task-push",
        "doc-push",
        "testing-push",
    }
)

# Dummy substitution values for rendering workflow.json.tmpl into parseable JSON.
# task_budget_usd/run_budget_usd are bare (unquoted) numeric positions in the
# template, so their dummy values must themselves be valid JSON number literals.
DUMMY_VALUES = {
    "id": "e-ab12cd-sample-slug",
    "slug": "sample-slug",
    "instance_dir": "workflows/routed-runner/runs/e-ab12cd-sample-slug",
    "workspace_root": "/tmp/dummy-workspace",
    "params.type": "epic",
    "params.repo_set": "default-set",
    "params.task_budget_usd": "75",
    "params.run_budget_usd": "1500",
}


def _render(text: str, values: dict[str, str]) -> str:
    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise AssertionError(f"no dummy value provided for template token {{{{ {name} }}}}")
        return values[name]

    return _TOKEN_RE.sub(_sub, text)


def _all_template_files() -> list[Path]:
    """Every file this template renders: the four `files:`/inline template sources
    referenced from template.yaml, plus template.yaml itself (for its inline `content`
    entry), plus every asset instruction file -- i.e. everything a `{{ }}` token could
    legally appear in.
    """
    files = [
        MANIFEST_PATH,
        TEMPLATE_DIR / "workflow.json.tmpl",
        TEMPLATE_DIR / "prompt.md.tmpl",
        TEMPLATE_DIR / "breakdown-contract.md.tmpl",
    ]
    files.extend(sorted((TEMPLATE_DIR / "instructions").glob("*.md")))
    return files


def _load_manifest() -> dict[str, Any]:
    with MANIFEST_PATH.open() as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# 1. template.yaml parses and matches the HLD §2.2 field shapes
# ---------------------------------------------------------------------------


def test_template_yaml_parses_as_yaml() -> None:
    manifest = _load_manifest()
    assert isinstance(manifest, dict)


def test_template_yaml_top_level_fields() -> None:
    manifest = _load_manifest()
    assert manifest["version"] == "1.0"
    assert manifest["name"] == "routed-runner"
    assert isinstance(manifest["description"], str) and manifest["description"].strip()
    assert manifest["id_pattern"] == "e-{rand6}-{slug}"
    assert manifest["instance_dir"] == "workflows/routed-runner/runs/{id}"


def test_template_yaml_params_shape() -> None:
    manifest = _load_manifest()
    params = manifest["params"]
    assert isinstance(params, dict)
    assert set(params) == {"type", "repo_set", "task_budget_usd", "run_budget_usd"}

    allowed_param_keys = {"description", "required", "enum", "default"}
    for name, spec in params.items():
        assert isinstance(spec, dict), name
        assert set(spec) <= allowed_param_keys, name
        assert isinstance(spec.get("description", ""), str)

    type_param = params["type"]
    assert type_param["required"] is False
    assert set(type_param["enum"]) == {"bug", "epic", "task", "documentation", "testing"}

    repo_set_param = params["repo_set"]
    assert repo_set_param["required"] is True
    assert "default" not in repo_set_param  # no sensible cross-workspace default (HLD §2.8)

    task_budget = params["task_budget_usd"]
    assert task_budget["required"] is False
    assert task_budget["default"] == "75"

    run_budget = params["run_budget_usd"]
    assert run_budget["required"] is False
    assert run_budget["default"] == "1500"


def test_template_yaml_dirs_mirror_bash_mkdir_list() -> None:
    manifest = _load_manifest()
    assert manifest["dirs"] == [
        "outputs",
        "outputs/bug",
        "outputs/epic",
        "outputs/task",
        "outputs/doc",
        "outputs/testing",
        "outputs/tasks",
        "control",
        "needs-input",
    ]


def test_template_yaml_files_shape() -> None:
    manifest = _load_manifest()
    files = manifest["files"]
    assert isinstance(files, list) and len(files) == 4

    by_target = {f["target"]: f for f in files}
    assert set(by_target) == {
        "workflow.json",
        "prompt.md",
        "breakdown-contract.md",
        "outputs/forced-type.txt",
    }

    for entry in files:
        has_source = "source" in entry
        has_content = "content" in entry
        assert has_source != has_content, entry  # mutually exclusive, per HLD §2.2

    assert by_target["workflow.json"]["source"] == "workflow.json.tmpl"
    assert by_target["prompt.md"]["source"] == "prompt.md.tmpl"
    assert by_target["prompt.md"].get("keep_existing") is True
    assert by_target["breakdown-contract.md"]["source"] == "breakdown-contract.md.tmpl"

    forced_type = by_target["outputs/forced-type.txt"]
    assert forced_type["content"] == "{{ params.type }}\n"
    assert forced_type.get("when") == "type"


def test_template_yaml_assets_shape() -> None:
    manifest = _load_manifest()
    assets = manifest["assets"]
    assert isinstance(assets, list) and len(assets) == 1
    asset = assets[0]
    assert asset["source"] == "instructions/"
    assert asset["target"] == "workflows/routed-runner/instructions/"
    assert asset.get("keep_existing") is True


def test_template_yaml_required_agents_exactly_match_dag() -> None:
    manifest = _load_manifest()
    assert set(manifest["required_agents"]) == {
        "architect",
        "architect-opus",
        "developer",
        "full-tester",
        "git-operator",
        "manager",
        "market-surveyor",
        "reviewer",
        "reviewer-opus",
        "tester",
    }
    # no duplicates
    assert len(manifest["required_agents"]) == len(set(manifest["required_agents"]))


# ---------------------------------------------------------------------------
# 2. every file referenced by the manifest exists
# ---------------------------------------------------------------------------


def test_every_manifest_referenced_file_exists() -> None:
    manifest = _load_manifest()

    for entry in manifest["files"]:
        source = entry.get("source")
        if source is not None:
            assert (TEMPLATE_DIR / source).is_file(), source

    for asset in manifest["assets"]:
        source_dir = TEMPLATE_DIR / asset["source"]
        assert source_dir.is_dir(), asset["source"]
        assert any(source_dir.glob("*.md")), "instructions/ asset dir has no content"


def test_instructions_dir_has_all_31_stage_files() -> None:
    instruction_files = sorted((TEMPLATE_DIR / "instructions").glob("*.md"))
    assert len(instruction_files) == 31


# ---------------------------------------------------------------------------
# 3. every `{{ ... }}` token is within the allowed variable set
# ---------------------------------------------------------------------------


def test_all_template_tokens_within_allowed_variable_set() -> None:
    offenders: list[str] = []
    for path in _all_template_files():
        text = path.read_text()
        for match in _TOKEN_RE.finditer(text):
            name = match.group(1)
            if name not in ALLOWED_VARIABLES:
                offenders.append(f"{path.relative_to(TEMPLATE_DIR)}: {{{{ {name} }}}}")
    assert not offenders, "unknown template variable(s) found:\n" + "\n".join(offenders)


def test_instructions_are_path_generic_with_no_template_tokens() -> None:
    """Instructions are workspace assets, not per-instance renders (HLD §2.2) -- none
    of them should contain a `{{ }}` token at all.
    """
    for path in sorted((TEMPLATE_DIR / "instructions").glob("*.md")):
        assert not _TOKEN_RE.search(path.read_text()), path.name


# ---------------------------------------------------------------------------
# 4. workflow.json.tmpl becomes valid JSON after dummy substitution
# ---------------------------------------------------------------------------


def test_workflow_json_tmpl_valid_after_dummy_substitution() -> None:
    raw = (TEMPLATE_DIR / "workflow.json.tmpl").read_text()
    rendered = _render(raw, DUMMY_VALUES)
    workflow = json.loads(rendered)  # raises if not valid JSON

    assert workflow["prompt_path"] == f"{DUMMY_VALUES['instance_dir']}/prompt.md"
    assert "branches" in workflow and len(workflow["branches"]) == 1
    assert workflow["repo_set"] == DUMMY_VALUES["params.repo_set"]
    assert workflow["id"] == DUMMY_VALUES["id"]

    task_ids = {t["id"] for t in workflow["tasks"]}
    assert task_ids == EXPECTED_TASK_IDS

    # prompt.md is a declared input of both head tasks (HLD §2.3).
    prompt_path = f"{DUMMY_VALUES['instance_dir']}/prompt.md"
    tasks_by_id = {t["id"]: t for t in workflow["tasks"]}
    assert prompt_path in tasks_by_id["git-branch-off"]["inputs"]
    assert prompt_path in tasks_by_id["classify"]["inputs"]

    # budget breaker thresholds substituted as bare numbers, not strings.
    breakers_by_id = {b["id"]: b for b in workflow["circuit_breakers"]}
    assert breakers_by_id["task-budget-cap"]["threshold"] == 75
    assert breakers_by_id["run-budget-cap"]["threshold"] == 1500


def test_workflow_json_tmpl_router_routes_match_task_route_entries() -> None:
    raw = (TEMPLATE_DIR / "workflow.json.tmpl").read_text()
    workflow = json.loads(_render(raw, DUMMY_VALUES))
    router = workflow["branches"][0]
    assert router["router_task_id"] == "classify"
    assert router["default_route"] is None
    assert router["routes"] == {
        "bug": {"entry": ["bug-triage"]},
        "epic": {"entry": ["refine-requirements"]},
        "task": {"entry": ["task-plan"]},
        "documentation": {"entry": ["doc-plan"]},
        "testing": {"entry": ["test-gap-analysis"]},
    }


def test_breakdown_contract_tmpl_renders_without_unknown_tokens() -> None:
    raw = (TEMPLATE_DIR / "breakdown-contract.md.tmpl").read_text()
    rendered = _render(raw, DUMMY_VALUES)
    assert DUMMY_VALUES["instance_dir"] in rendered
    assert "{{" not in rendered


def test_prompt_md_tmpl_renders_without_unknown_tokens() -> None:
    raw = (TEMPLATE_DIR / "prompt.md.tmpl").read_text()
    rendered = _render(raw, DUMMY_VALUES)
    assert rendered.startswith(f"# {DUMMY_VALUES['id']}")
    assert "{{" not in rendered
    # FinPlan-specific section replaced by a generic one (HLD §2.8 delta).
    assert "## Project context" in rendered
    assert "FinPlan" not in rendered


# ---------------------------------------------------------------------------
# 5. no finplan/fin-plan/fin_plan reference anywhere in the template tree
# ---------------------------------------------------------------------------


def test_no_finplan_references_in_instruction_and_template_files() -> None:
    """Per HLD §2.8 / T-Tb3rtr scope: every instruction file (workspace asset) and
    every rendered template file must be FinPlan-free. README.md is deliberately
    exempt -- it documents this template's provenance (generalized FROM the finplan
    epic-runner) and legitimately names it for that purpose.
    """
    pattern = re.compile("finplan|fin-plan|fin_plan", re.IGNORECASE)
    scanned = [
        MANIFEST_PATH,
        TEMPLATE_DIR / "workflow.json.tmpl",
        TEMPLATE_DIR / "prompt.md.tmpl",
        TEMPLATE_DIR / "breakdown-contract.md.tmpl",
        *sorted((TEMPLATE_DIR / "instructions").glob("*.md")),
    ]
    offenders = [str(p.relative_to(TEMPLATE_DIR)) for p in scanned if pattern.search(p.read_text())]
    assert not offenders, f"FinPlan-specific reference(s) found in: {offenders}"
