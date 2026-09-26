"""Static + render-level sanity checks for the built-in `overseer-runner` workflow template
(T-eGXqXH).

Scope note (read before extending this file): at this point in the epic, two sibling files
this template's *own* spec references don't exist yet --
`overseer-contract.md.tmpl` (T-ltBLUY, the next task) and `instructions/*.md` (T-5ZzAZp). That
means the public `agent_orchestrator.templates.instantiate()` / `ao new overseer-runner` path
cannot be exercised end-to-end yet: `instantiate()` unconditionally fails when it reaches the
`assets` loop and finds `instructions/` missing (see `_materialize_asset`), regardless of
whether the rest of the instance would have rendered correctly. A true end-to-end `ao new`/
`ao run` proof is `T-WruPiv`'s job, once the rest of the template lands.

So, per this task's own acceptance criteria (AC1-AC8), the checks below render each `.tmpl`
file directly with the SAME private rendering primitive `instantiate()` itself uses
(`agent_orchestrator.templates._render`), and validate the result against the real
`WorkflowSpec` schema (`spec.load_workflow` + `spec.cross_validate` + `dag.build_dag` +
`spec.validate_run_control` -- the same pipeline `cli._load_all`/`ao validate` runs) and
against the real `overseer_tool.py` config loader (`load_config`, imported from its source path
exactly as the HLD's own test-strategy table specifies) -- without needing a scaffolded
instance directory at all. This mirrors `test_builtin_routed_runner_assets.py`'s static-check
style, but (unlike that file, whose module docstring cites a since-resolved historical
concurrent-development constraint) deliberately DOES use `agent_orchestrator.templates`'
private rendering helpers directly, since that module now exists and is the most faithful way
to prove these `.tmpl` files render the way production `instantiate()` will once the two
sibling files land.
"""

from __future__ import annotations

import importlib.util
import json
import py_compile
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from agent_orchestrator.dag import build_dag
from agent_orchestrator.models import AgentSpec, RepoRef, RepoSet
from agent_orchestrator.spec import cross_validate, load_workflow, validate_run_control
from agent_orchestrator.templates import _VAR_RE, TemplateError, _render, load_template

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = (
    _REPO_ROOT / "src" / "agent_orchestrator" / "templates" / "builtin" / "overseer-runner"
)
MANIFEST_PATH = TEMPLATE_DIR / "template.yaml"
TOOL_PATH = TEMPLATE_DIR / "tools" / "overseer_tool.py"

# Every variable `{{ ... }}` may legally reference anywhere in this template's rendered
# files (mirrors test_builtin_routed_runner_assets.py's ALLOWED_VARIABLES pattern).
ALLOWED_VARIABLES = frozenset(
    {
        "id",
        "workspace_root",
        "instance_dir",
        "params.repo_set",
        "params.run_budget_usd",
        "params.task_budget_usd",
        "params.converge_pct",
        "params.stabilize_pct",
        "params.closeout_pct",
        "params.wave_size",
        "params.max_waves",
        "params.wave_max_minutes",
        "params.max_attempts_per_item",
        "params.max_expanders_per_wave",
        "params.max_injected_tasks",
        "params.final_push",
        "params.overseer_effort",
        "params.overseer_model",
        "params.python_bin",
    }
)

EXPECTED_PARAM_NAMES = frozenset(
    {
        "repo_set",
        "run_budget_usd",
        "task_budget_usd",
        "converge_pct",
        "stabilize_pct",
        "closeout_pct",
        "wave_size",
        "max_waves",
        "wave_max_minutes",
        "max_attempts_per_item",
        "max_expanders_per_wave",
        "max_injected_tasks",
        "final_push",
        "overseer_effort",
        "overseer_model",
        "python_bin",
    }
)

EXPECTED_TASK_IDS = frozenset({"git-branch-off", "intake"})

EXPECTED_HOOK_IDS = frozenset(
    {
        "ov-intake-prep",
        "ov-intake-check",
        "ov-ckpt-prep",
        "ov-ckpt-check",
        "ov-expander-check",
        "ov-unit-gate",
    }
)

# HLD §13.2: 120s for prep/check hooks, 300s for ov-ckpt-prep (more I/O), 60s for
# ov-unit-gate (must stay fast).
EXPECTED_HOOK_TIMEOUTS = {
    "ov-intake-prep": 120,
    "ov-intake-check": 120,
    "ov-ckpt-prep": 300,
    "ov-ckpt-check": 120,
    "ov-expander-check": 120,
    "ov-unit-gate": 60,
}

# Config schema's REQUIRED keys (`overseer_tool.load_config`'s `_require_number` calls --
# a missing one raises CFG-0). The remaining Config fields (final_push, overseer_effort,
# overseer_model, python_bin, runs_root, kind_map) have tool-side defaults but are still
# rendered explicitly by this template (HLD §13.4).
CONFIG_REQUIRED_KEYS = frozenset(
    {
        "converge_pct",
        "stabilize_pct",
        "closeout_pct",
        "wave_size",
        "max_waves",
        "wave_max_minutes",
        "max_attempts_per_item",
        "max_expanders_per_wave",
        "max_injected_tasks",
        "run_budget_usd",
        "task_budget_usd",
        "stall_waves",
        "stabilize_wave_size",
        "max_stabilize_passes",
        "sub_wave_size",
        "default_unit_cost_usd",
        "default_ckpt_cost_usd",
        "contract_version",
    }
)

# HLD §13.4: written per-kind (not grouped), even though several kinds share one
# {agent, instruction} pair -- these two instruction basenames are the only ones involved.
_WORK_UNIT_INSTRUCTION = "workflows/overseer-runner/instructions/10-work-unit.md"
_STABILIZE_UNIT_INSTRUCTION = "workflows/overseer-runner/instructions/11-stabilize-unit.md"
_EXPANDER_INSTRUCTION = "workflows/overseer-runner/instructions/30-expander.md"

EXPECTED_KIND_MAP = {
    "research": {"agent": "architect", "instruction": _WORK_UNIT_INSTRUCTION},
    "design": {"agent": "architect", "instruction": _WORK_UNIT_INSTRUCTION},
    "implement": {"agent": "developer", "instruction": _WORK_UNIT_INSTRUCTION},
    "fix": {"agent": "developer", "instruction": _WORK_UNIT_INSTRUCTION},
    "document": {"agent": "developer", "instruction": _WORK_UNIT_INSTRUCTION},
    "stabilize": {"agent": "developer", "instruction": _STABILIZE_UNIT_INSTRUCTION},
    "test": {"agent": "tester", "instruction": _WORK_UNIT_INSTRUCTION},
    "verify": {"agent": "tester", "instruction": _WORK_UNIT_INSTRUCTION},
    "review": {"agent": "reviewer", "instruction": _WORK_UNIT_INSTRUCTION},
    "expand": {"agent": "architect", "instruction": _EXPANDER_INSTRUCTION},
}

# Dummy substitution values -- every numeric/boolean param is a valid bare-JSON-literal
# string, matching this template's own defaults (§13.1), so DUMMY_VALUES renders exactly
# like a default `ao new overseer-runner <id> --param repo_set=...` would.
DUMMY_ID = "o-ab12cd-sample-slug"
DUMMY_INSTANCE_DIR = f"workflows/overseer-runner/runs/{DUMMY_ID}"
DUMMY_WORKSPACE_ROOT = "/tmp/dummy-workspace"

DUMMY_VALUES: dict[str, str] = {
    "id": DUMMY_ID,
    "instance_dir": DUMMY_INSTANCE_DIR,
    "workspace_root": DUMMY_WORKSPACE_ROOT,
    "params.repo_set": "default-set",
    "params.run_budget_usd": "2000",
    "params.task_budget_usd": "75",
    "params.converge_pct": "80",
    "params.stabilize_pct": "90",
    "params.closeout_pct": "95",
    "params.wave_size": "6",
    "params.max_waves": "12",
    "params.wave_max_minutes": "90",
    "params.max_attempts_per_item": "3",
    "params.max_expanders_per_wave": "0",
    "params.max_injected_tasks": "160",
    "params.final_push": "true",
    "params.overseer_effort": "high",
    "params.overseer_model": "",
    "params.python_bin": "python3",
}


def _load_manifest() -> dict[str, Any]:
    with MANIFEST_PATH.open() as f:
        return yaml.safe_load(f)


def _render_file(rel_path: str, variables: dict[str, str]) -> str:
    """Render one of this template's `.tmpl` sources with the REAL `_render` primitive
    `instantiate()` itself calls -- `escape_json=True` iff the rendered target would be a
    `.json` file, exactly mirroring `instantiate()`'s own `target_path.suffix.lower() ==
    ".json"` check."""
    raw = (TEMPLATE_DIR / rel_path).read_text(encoding="utf-8")
    target_is_json = rel_path.endswith(".json.tmpl") or rel_path.endswith(".json")
    return _render(raw, variables, source_desc=rel_path, escape_json=target_is_json)


def _render_workflow(variables: dict[str, str] | None = None) -> dict[str, Any]:
    values = dict(DUMMY_VALUES if variables is None else variables)
    return json.loads(_render_file("workflow.json.tmpl", values))


def _render_config(variables: dict[str, str] | None = None) -> dict[str, Any]:
    values = dict(DUMMY_VALUES if variables is None else variables)
    return json.loads(_render_file("overseer-config.json.tmpl", values))


def _all_template_content_files() -> list[Path]:
    """Every file a `{{ }}` token could legally appear in (mirrors routed-runner's own
    `_all_template_files`). `tools/overseer_tool.py` is deliberately included -- it must
    contain ZERO tokens (T-ABDjSj's render-safety guarantee), so an offending token there
    would show up here too rather than needing a separate assertion."""
    return [
        TEMPLATE_DIR / "workflow.json.tmpl",
        TEMPLATE_DIR / "overseer-config.json.tmpl",
        TEMPLATE_DIR / "prompt.md.tmpl",
        TOOL_PATH,
    ]


def _load_tool_module() -> ModuleType:
    """Import overseer_tool.py from its source path -- exactly the pattern
    `test_overseer_tool_budget.py`/`test_overseer_tool_ledger.py`/`test_overseer_tool_gates.py`
    already use (HLD §18's own convention for this tool)."""
    spec = importlib.util.spec_from_file_location("overseer_tool_under_test_assets", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Dataclasses (with `from __future__ import annotations` in the tool) resolve their
    # string annotations via `sys.modules[cls.__module__]` -- the module must be registered
    # BEFORE `exec_module` runs, or `dataclass()` itself raises at class-definition time.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 1. template.yaml parses and matches HLD §13.1 exactly (AC1)
# ---------------------------------------------------------------------------


def test_template_yaml_parses_as_yaml() -> None:
    manifest = _load_manifest()
    assert isinstance(manifest, dict)


def test_template_yaml_top_level_fields() -> None:
    manifest = _load_manifest()
    assert manifest["version"] == "1.0"
    assert manifest["name"] == "overseer-runner"
    assert isinstance(manifest["description"], str) and manifest["description"].strip()
    assert manifest["id_pattern"] == "o-{rand6}-{slug}"
    assert manifest["instance_dir"] == "workflows/overseer-runner/runs/{id}"


def test_template_yaml_params_shape_matches_hld_13_1() -> None:
    manifest = _load_manifest()
    params = manifest["params"]
    assert isinstance(params, dict)
    assert set(params) == EXPECTED_PARAM_NAMES
    assert len(params) == 16

    allowed_param_keys = {"description", "required", "enum", "default"}
    for name, spec in params.items():
        assert isinstance(spec, dict), name
        assert set(spec) <= allowed_param_keys, name

    repo_set = params["repo_set"]
    assert repo_set.get("required") is True
    assert "default" not in repo_set

    expected_defaults = {
        "run_budget_usd": "2000",
        "task_budget_usd": "75",
        "converge_pct": "80",
        "stabilize_pct": "90",
        "closeout_pct": "95",
        "wave_size": "6",
        "max_waves": "12",
        "wave_max_minutes": "90",
        "max_attempts_per_item": "3",
        "max_expanders_per_wave": "0",
        "max_injected_tasks": "160",
        "final_push": "true",
        "overseer_effort": "high",
        "overseer_model": "",
        "python_bin": "python3",
    }
    for name, default in expected_defaults.items():
        assert params[name].get("default") == default, name
        assert not params[name].get("required", False), name

    assert params["final_push"]["enum"] == ["true", "false"]
    assert params["overseer_effort"]["enum"] == ["medium", "high", "xhigh"]


def test_template_yaml_dirs() -> None:
    manifest = _load_manifest()
    assert manifest["dirs"] == [
        "outputs",
        "outputs/overseer",
        "outputs/manifests",
        "outputs/progress",
        "outputs/waves",
        "outputs/checkpoints",
        "outputs/final",
        "control",
        "needs-input",
        "tools",
    ]


def test_template_yaml_files_shape() -> None:
    manifest = _load_manifest()
    files = manifest["files"]
    assert isinstance(files, list) and len(files) == 4

    by_target = {f["target"]: f for f in files}
    assert set(by_target) == {
        "workflow.json",
        "prompt.md",
        "overseer-config.json",
        "tools/overseer_tool.py",
    }
    assert by_target["workflow.json"]["source"] == "workflow.json.tmpl"
    assert by_target["prompt.md"]["source"] == "prompt.md.tmpl"
    assert by_target["prompt.md"].get("keep_existing") is True
    assert by_target["overseer-config.json"]["source"] == "overseer-config.json.tmpl"
    assert by_target["tools/overseer_tool.py"]["source"] == "tools/overseer_tool.py"

    # No overseer-contract.md.tmpl entry yet -- that's T-ltBLUY (explicit scope boundary).
    assert "overseer-contract.md" not in by_target


def test_no_overseer_contract_tmpl_or_instructions_dir_created_yet() -> None:
    """Guards this task's own scope boundary: `overseer-contract.md.tmpl` (T-ltBLUY) and
    `instructions/*.md` (T-5ZzAZp) must NOT be created by this task."""
    assert not (TEMPLATE_DIR / "overseer-contract.md.tmpl").exists()
    assert not (TEMPLATE_DIR / "instructions").exists()


def test_template_yaml_assets_shape() -> None:
    manifest = _load_manifest()
    assets = manifest["assets"]
    assert isinstance(assets, list) and len(assets) == 1
    asset = assets[0]
    assert asset["source"] == "instructions/"
    assert asset["target"] == "workflows/overseer-runner/instructions/"
    assert asset.get("keep_existing") is True


def test_template_yaml_required_agents() -> None:
    manifest = _load_manifest()
    assert set(manifest["required_agents"]) == {
        "architect",
        "developer",
        "git-operator",
        "manager",
        "reviewer",
        "tester",
    }
    assert len(manifest["required_agents"]) == len(set(manifest["required_agents"]))


def test_every_files_entry_source_exists_on_disk() -> None:
    """The `assets[].source` (`instructions/`) deliberately does NOT exist yet (T-5ZzAZp) --
    see the module docstring -- so only `files[].source` is checked for existence here,
    unlike routed-runner's combined check."""
    manifest = _load_manifest()
    for entry in manifest["files"]:
        source = entry.get("source")
        assert source is not None
        assert (TEMPLATE_DIR / source).is_file(), source


# ---------------------------------------------------------------------------
# 2. load_template() succeeds (AC1)
# ---------------------------------------------------------------------------


def test_load_template_succeeds() -> None:
    with tempfile.TemporaryDirectory() as ws:
        info = load_template("overseer-runner", ws, None)
    assert info.name == "overseer-runner"
    assert info.source == "builtin"
    assert {p.name for p in info.params} == EXPECTED_PARAM_NAMES
    repo_set_param = next(p for p in info.params if p.name == "repo_set")
    assert repo_set_param.required is True
    final_push_param = next(p for p in info.params if p.name == "final_push")
    assert final_push_param.enum == ["true", "false"]
    overseer_effort_param = next(p for p in info.params if p.name == "overseer_effort")
    assert overseer_effort_param.enum == ["medium", "high", "xhigh"]
    assert set(info.required_agents) == {
        "architect",
        "developer",
        "git-operator",
        "manager",
        "reviewer",
        "tester",
    }


# ---------------------------------------------------------------------------
# 3. Every `{{ }}` token is within the allowed variable set
# ---------------------------------------------------------------------------


def test_all_template_tokens_within_allowed_variable_set() -> None:
    offenders: list[str] = []
    for path in _all_template_content_files():
        text = path.read_text(encoding="utf-8")
        for match in _VAR_RE.finditer(text):
            name = match.group(1)
            if name not in ALLOWED_VARIABLES:
                offenders.append(f"{path.relative_to(TEMPLATE_DIR)}: {{{{ {name} }}}}")
    assert not offenders, "unknown template variable(s) found:\n" + "\n".join(offenders)


def test_tool_source_has_no_template_tokens() -> None:
    """AC7 / developer #5: overseer_tool.py must never contain a `{{ }}` placeholder --
    one would break rendering (or silently corrupt the tool) since `_render` runs over
    every `files[]` entry's content, not just `.tmpl`-suffixed ones."""
    text = TOOL_PATH.read_text(encoding="utf-8")
    assert _VAR_RE.search(text) is None


# ---------------------------------------------------------------------------
# 4. Tool render safety (AC7)
# ---------------------------------------------------------------------------


def test_tool_renders_byte_identical(tmp_path: Path) -> None:
    source_text = TOOL_PATH.read_text(encoding="utf-8")
    rendered = _render_file("tools/overseer_tool.py", DUMMY_VALUES)
    assert rendered == source_text


def test_tool_renders_byte_identical_and_compiles(tmp_path: Path) -> None:
    rendered = _render_file("tools/overseer_tool.py", DUMMY_VALUES)
    rendered_path = tmp_path / "overseer_tool.py"
    rendered_path.write_text(rendered, encoding="utf-8")
    py_compile.compile(str(rendered_path), doraise=True)


# ---------------------------------------------------------------------------
# 5. workflow.json.tmpl: parses, and passes the real WorkflowSpec/ao-validate pipeline (AC2)
# ---------------------------------------------------------------------------


def _cross_validate_minimal(workflow_dict: dict[str, Any]) -> None:
    """Run the same pipeline `cli._load_all`/`ao validate` runs (load_workflow's caller
    already parsed the dict into a WorkflowSpec; this re-parses via a temp file to also
    exercise `load_workflow`'s own JSON-Schema + pydantic construction path end to end),
    using an in-memory reposet/agent map -- avoids depending on `instructions/`/
    `overseer-contract.md` (not yet created) the way a full `ao new --validate-only`
    would."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(workflow_dict, f)
        workflow_path = f.name
    try:
        wf = load_workflow(workflow_path)
    finally:
        Path(workflow_path).unlink()

    reposet_map = {
        "default-set": RepoSet(
            repos=[RepoRef(id="target", path=".", role="primary")],
            workspace_root=DUMMY_WORKSPACE_ROOT,
        )
    }
    agent_map = {
        name: AgentSpec(executor="fake")
        for name in ("architect", "developer", "git-operator", "manager", "reviewer", "tester")
    }
    cross_validate(wf, reposet_map, agent_map)
    graph = build_dag(wf)
    graph.topological_order()
    validate_run_control(wf, graph)


def test_workflow_json_tmpl_renders_valid_json_and_passes_load_workflow_and_ao_validate() -> None:
    workflow = _render_workflow()

    assert workflow["id"] == DUMMY_ID
    assert workflow["repo_set"] == "default-set"
    assert workflow["prompt_path"] == f"{DUMMY_INSTANCE_DIR}/prompt.md"
    assert workflow["triggers"] == [{"type": "manual"}]

    task_ids = {t["id"] for t in workflow["tasks"]}
    assert task_ids == EXPECTED_TASK_IDS

    # AC2: passes load_workflow + the same cross-validation `ao validate` runs, with no errors.
    _cross_validate_minimal(workflow)


def test_workflow_json_tmpl_hooks_shape() -> None:
    """AC4: exactly 6 hooks, absolute script path, on_failure fail_task, per-hook timeouts."""
    workflow = _render_workflow()
    hooks = workflow["hooks"]
    assert set(hooks) == EXPECTED_HOOK_IDS

    expected_script = f"{DUMMY_WORKSPACE_ROOT}/{DUMMY_INSTANCE_DIR}/tools/overseer_tool.py"
    for hook_id, hook in hooks.items():
        command = hook["command"]
        assert command[0] == "python3"
        assert command[1] == expected_script
        assert Path(command[1]).is_absolute()
        assert command[1].endswith("/tools/overseer_tool.py")
        assert hook["on_failure"] == "fail_task"
        assert hook["timeout_seconds"] == EXPECTED_HOOK_TIMEOUTS[hook_id]

    subcommand_by_hook = {
        "ov-intake-prep": "intake-prep",
        "ov-intake-check": "intake-check",
        "ov-ckpt-prep": "ckpt-prep",
        "ov-ckpt-check": "ckpt-check",
        "ov-expander-check": "expander-check",
        "ov-unit-gate": "unit-gate",
    }
    for hook_id, subcommand in subcommand_by_hook.items():
        assert hooks[hook_id]["command"][2] == subcommand
        assert hooks[hook_id]["command"][3:] == [
            "--workspace-root",
            DUMMY_WORKSPACE_ROOT,
            "--instance-dir",
            DUMMY_INSTANCE_DIR,
        ]


def test_workflow_json_tmpl_breakers_shape() -> None:
    """AC3: exact breaker id/condition/action/mode/threshold set."""
    workflow = _render_workflow()
    breakers_by_id = {b["id"]: b for b in workflow["circuit_breakers"]}
    assert len(breakers_by_id) == 11

    stop_file_breakers = {
        bid: b for bid, b in breakers_by_id.items() if b["condition"] == "stop_file"
    }
    assert len(stop_file_breakers) == 5
    pause_breakers = {bid for bid, b in stop_file_breakers.items() if b["action"] == "pause"}
    halt_breakers = {bid for bid, b in stop_file_breakers.items() if b["action"] == "stop"}
    assert pause_breakers == {"human-input-gate", "human-input-gate-2", "human-input-gate-3"}
    assert halt_breakers == {"operator-kill", "operator-kill-2"}

    run_deadline = breakers_by_id["run-deadline"]
    assert run_deadline["condition"] == "run_wall_clock_seconds"
    assert run_deadline["threshold"] == 432000
    assert run_deadline["action"] == "stop"
    assert run_deadline["mode"] == "recommend"

    run_active_cap = breakers_by_id["run-active-cap"]
    assert run_active_cap["condition"] == "run_active_seconds"
    assert run_active_cap["threshold"] == 432000
    assert run_active_cap["mode"] == "recommend"

    systemic = breakers_by_id["systemic-breakage"]
    assert systemic["condition"] == "consecutive_failures"
    assert systemic["threshold"] == 3
    assert systemic["action"] == "fail"
    assert systemic["mode"] == "recommend"

    runaway = breakers_by_id["runaway-fanout"]
    assert runaway["condition"] == "injected_task_count"
    assert runaway["threshold"] == 160
    assert runaway["action"] == "fail"
    assert runaway.get("mode", "hard") == "hard"  # hard = default mode, omitted

    task_budget = breakers_by_id["task-budget-cap"]
    assert task_budget["condition"] == "task_cost_usd"
    assert task_budget["threshold"] == 75
    assert task_budget["action"] == "fail"
    assert task_budget["mode"] == "recommend"

    run_budget = breakers_by_id["run-budget-backstop"]
    assert run_budget["condition"] == "run_cost_usd"
    assert run_budget["threshold"] == 2000
    assert run_budget["action"] == "stop"
    assert run_budget.get("mode", "hard") == "hard"  # hard = default mode, omitted


def test_workflow_json_tmpl_tasks_shape() -> None:
    """AC5: intake's emit_tasks/task_manifest_path/pre_hook/post_hook/skip_if_outputs_exist."""
    workflow = _render_workflow()
    tasks_by_id = {t["id"]: t for t in workflow["tasks"]}

    git_branch_off = tasks_by_id["git-branch-off"]
    assert git_branch_off["agent"] == "git-operator"
    assert git_branch_off["depends_on"] == []
    assert git_branch_off["skip_if_outputs_exist"] is False
    assert git_branch_off["isolation"] == "none"

    intake = tasks_by_id["intake"]
    assert intake["agent"] == "architect"
    assert intake["depends_on"] == ["git-branch-off"]
    assert intake["emit_tasks"] is True
    assert intake["task_manifest_path"].endswith("outputs/manifests/intake.json")
    assert intake["pre_hook"] == {"use": "ov-intake-prep"}
    assert intake["post_hook"] == {"use": "ov-intake-check", "on_failure": "fail_task"}
    assert intake["skip_if_outputs_exist"] is False
    assert f"{DUMMY_INSTANCE_DIR}/overseer-contract.md" in intake["inputs"]
    assert f"{DUMMY_INSTANCE_DIR}/overseer-config.json" in intake["inputs"]


# ---------------------------------------------------------------------------
# 6. Param overrides (AC6)
# ---------------------------------------------------------------------------


def test_param_override_renders_correct_threshold_and_config() -> None:
    overrides = dict(DUMMY_VALUES)
    overrides["params.run_budget_usd"] = "500"
    overrides["params.wave_size"] = "3"

    workflow = _render_workflow(overrides)
    breakers_by_id = {b["id"]: b for b in workflow["circuit_breakers"]}
    assert breakers_by_id["run-budget-backstop"]["threshold"] == 500

    config = _render_config(overrides)
    assert config["wave_size"] == 3
    assert config["run_budget_usd"] == 500


def test_non_numeric_run_budget_usd_fails_rendering_validation() -> None:
    overrides = dict(DUMMY_VALUES)
    overrides["params.run_budget_usd"] = "abc"

    rendered_workflow_text = _render_file("workflow.json.tmpl", overrides)
    with pytest.raises(json.JSONDecodeError):
        json.loads(rendered_workflow_text)

    rendered_config_text = _render_file("overseer-config.json.tmpl", overrides)
    with pytest.raises(json.JSONDecodeError):
        json.loads(rendered_config_text)


# ---------------------------------------------------------------------------
# 7. overseer-config.json.tmpl (AC2 config schema)
# ---------------------------------------------------------------------------


def test_overseer_config_json_tmpl_has_every_required_key() -> None:
    config = _render_config()
    assert config["schema"] == "ao.overseer.config/v1"
    missing = CONFIG_REQUIRED_KEYS - set(config)
    assert not missing, f"missing required config keys: {missing}"


def test_overseer_config_json_tmpl_final_push_renders_as_json_boolean() -> None:
    """Correctness pitfall: `Config.final_push` is computed as `bool(data.get(...))` in the
    tool, so a JSON *string* `"false"` would be truthy in Python and silently keep the tail
    push enabled. This asserts the rendered value is a real JSON boolean, not a string."""
    config = _render_config()
    assert config["final_push"] is True
    assert isinstance(config["final_push"], bool)

    overrides = dict(DUMMY_VALUES)
    overrides["params.final_push"] = "false"
    config_false = _render_config(overrides)
    assert config_false["final_push"] is False
    assert isinstance(config_false["final_push"], bool)


def test_overseer_config_json_tmpl_kind_map_matches_hld_13_4() -> None:
    config = _render_config()
    assert config["kind_map"] == EXPECTED_KIND_MAP


def test_overseer_config_json_tmpl_scalar_defaults() -> None:
    config = _render_config()
    assert config["overseer_effort"] == "high"
    assert config["overseer_model"] == ""
    assert config["python_bin"] == "python3"
    assert config["stall_waves"] == 2
    assert config["stabilize_wave_size"] == 4
    assert config["max_stabilize_passes"] == 2
    assert config["sub_wave_size"] == 4
    assert config["default_unit_cost_usd"] == 8
    assert config["default_ckpt_cost_usd"] == 5
    assert config["runs_root"] == ".orchestrator/runs"
    assert config["contract_version"] == 1


def test_overseer_config_json_tmpl_satisfies_the_real_tool_loader(tmp_path: Path) -> None:
    """Integration check: the rendered config must actually pass `overseer_tool.load_config`
    (CFG-0..3), not just look right by inspection."""
    module = _load_tool_module()
    inst_dir = tmp_path / "inst"
    inst_dir.mkdir()
    (inst_dir / "overseer-config.json").write_text(
        _render_file("overseer-config.json.tmpl", DUMMY_VALUES), encoding="utf-8"
    )
    cfg = module.load_config(inst_dir)
    assert cfg.wave_size == 6
    assert cfg.max_waves == 12
    assert cfg.run_budget_usd == 2000.0
    assert cfg.final_push is True
    assert cfg.kind_map == EXPECTED_KIND_MAP


# ---------------------------------------------------------------------------
# 8. Packaging: the wheel ships this template's files (AC8)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not on PATH; needed to build a wheel")
def test_wheel_contains_overseer_runner_template_files(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    result = subprocess.run(
        ["uv", "build", "--wheel", "-o", str(dist_dir)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr

    wheels = list(dist_dir.glob("*.whl"))
    assert len(wheels) == 1, wheels

    with zipfile.ZipFile(wheels[0]) as z:
        names = set(z.namelist())

    expected = {
        "agent_orchestrator/templates/builtin/overseer-runner/template.yaml",
        "agent_orchestrator/templates/builtin/overseer-runner/workflow.json.tmpl",
        "agent_orchestrator/templates/builtin/overseer-runner/overseer-config.json.tmpl",
        "agent_orchestrator/templates/builtin/overseer-runner/prompt.md.tmpl",
        "agent_orchestrator/templates/builtin/overseer-runner/tools/overseer_tool.py",
    }
    missing = expected - names
    assert not missing, f"missing from built wheel: {missing}"


# ---------------------------------------------------------------------------
# 9. prompt.md.tmpl: renders, sections present (HLD §17)
# ---------------------------------------------------------------------------


def test_prompt_md_tmpl_renders_and_has_required_sections() -> None:
    rendered = _render_file("prompt.md.tmpl", DUMMY_VALUES)
    assert DUMMY_ID in rendered
    for heading in (
        "## Asks",
        "## What usable means to me",
        "## Constraints",
        "## Budget notes",
        "## Out of scope",
    ):
        assert heading in rendered


# ---------------------------------------------------------------------------
# 10. instantiate() with an unknown/undeclared param still fails fast (defense in depth,
#     exercises the shared param-resolution path without requiring the missing assets)
# ---------------------------------------------------------------------------


def test_load_template_then_reject_unknown_param() -> None:
    from agent_orchestrator.templates import instantiate

    with tempfile.TemporaryDirectory() as ws:
        info = load_template("overseer-runner", ws, None)
        with pytest.raises(TemplateError, match="unknown template param"):
            instantiate(
                info,
                ws,
                slug_or_id="demo",
                params={"repo_set": "default-set", "not_a_real_param": "x"},
            )
