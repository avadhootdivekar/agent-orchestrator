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
        TEMPLATE_DIR / "agents.recommended.json.tmpl",
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
    assert isinstance(assets, list) and len(assets) == 2
    by_target = {a["target"]: a for a in assets}
    assert set(by_target) == {
        "workflows/routed-runner/instructions/",
        "agents.recommended.json",
    }

    instructions_asset = by_target["workflows/routed-runner/instructions/"]
    assert instructions_asset["source"] == "instructions/"
    assert instructions_asset.get("keep_existing") is True

    agents_seed_asset = by_target["agents.recommended.json"]
    assert agents_seed_asset["source"] == "agents.recommended.json.tmpl"
    assert agents_seed_asset.get("keep_existing") is True


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
        # merge-resolver: T-Tp7Zs2/S-2 -- used only when a task opts into
        # `isolation: worktree` and `integration.ladder` reaches its "llm" tier
        # (README.md "Parallel isolation"), but declared here so the workspace's
        # `agents:` config resolves it up front rather than at first isolated use.
        "merge-resolver",
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
        # Asset sources may be a directory (instructions/) or a single file
        # (agents.recommended.json.tmpl) -- `_materialize_asset` in templates/__init__.py
        # handles both; mirror that here rather than assuming every asset is a directory.
        source_path = TEMPLATE_DIR / asset["source"]
        assert source_path.is_dir() or source_path.is_file(), asset["source"]
        if source_path.is_dir():
            assert any(source_path.glob("*.md")), f"{asset['source']} asset dir has no content"


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


# ---------------------------------------------------------------------------
# 6. E-Wk9Tz3 T-Tp7Zs2: isolation wiring -- workflow.json.tmpl stays inert by default
# ---------------------------------------------------------------------------

INSTRUCTIONS_DIR = TEMPLATE_DIR / "instructions"

# Every per-task instruction that used to tell its agent to `git push` (R-22 + the
# REVIEW.md C-1 follow-up): under isolation these run on an ao-owned, never-pushed
# branch. The first six are the epic-fan-out/single-task-route files R-22 originally
# named; the rest are the bug/doc/testing-route (+ single-task-route retest) files
# C-1 identified as reachable via the README's `defaults.isolation: "worktree"`
# opt-in, plus `35-task-retest.md` (found during the C-1 fix pass -- same bug, never
# named by either R-22 or C-1).
PUSH_DIRECTIVE_FILES = (
    "08-implement-task.md",
    "11-fix-task.md",
    "31-task-impl.md",
    "34-task-fix.md",
    "09-write-tests.md",
    "32-task-test.md",
    "21-bug-fix.md",
    "22-bug-test.md",
    "41-doc-write.md",
    "51-test-write.md",
    "35-task-retest.md",
)

# The review instructions that used to tell the reviewer to inspect "pushed" commits.
# `23-bug-review.md` (C-1 follow-up) had the identical pattern; `42-doc-review.md`
# never referenced pushed commits at all (it reads the written files directly), so it
# is not in this list.
REVIEW_WORDING_FILES = ("10-review-task.md", "33-task-review.md", "23-bug-review.md")


def test_workflow_json_tmpl_defaults_isolation_none() -> None:
    raw = (TEMPLATE_DIR / "workflow.json.tmpl").read_text()
    workflow = json.loads(_render(raw, DUMMY_VALUES))
    assert workflow["defaults"]["isolation"] == "none"


def test_workflow_json_tmpl_git_branch_off_pinned_isolation_none() -> None:
    raw = (TEMPLATE_DIR / "workflow.json.tmpl").read_text()
    workflow = json.loads(_render(raw, DUMMY_VALUES))
    by_id = {t["id"]: t for t in workflow["tasks"]}
    assert by_id["git-branch-off"]["isolation"] == "none"


# The five route-terminal push tasks, all backed by 90-final-push.md (REVIEW.md C-1).
PUSH_TERMINAL_TASK_IDS = ("bug-push", "epic-push", "task-push", "doc-push", "testing-push")


def test_workflow_json_tmpl_push_terminal_tasks_pinned_isolation_none() -> None:
    """REVIEW.md C-1: following the README's `defaults.isolation: "worktree"` opt-in
    must not silently isolate a task that makes a real `git push` -- that would either
    STOP-fail the route's actual deliverable (an `ao/<run_id>/<task_id>` branch is not
    "the epic branch") or push a disposable, disconnected branch instead of the
    route's real work."""
    raw = (TEMPLATE_DIR / "workflow.json.tmpl").read_text()
    workflow = json.loads(_render(raw, DUMMY_VALUES))
    by_id = {t["id"]: t for t in workflow["tasks"]}
    for tid in PUSH_TERMINAL_TASK_IDS:
        assert by_id[tid]["isolation"] == "none", tid


_GIT_PUSH_COMMAND_RE = re.compile(r"git\s*(-C\s+\S+\s+)?push\b", re.IGNORECASE)


def _instruction_has_real_push_directive(path: Path) -> bool:
    """True when *path* contains an actual `git ... push` command that is NOT sitting
    in a negated ("never `git push`") context -- distinguishes a real directive to
    push from a prohibition that merely names the command. Requires "git" adjacent to
    "push" so idiomatic, non-command uses of the word ("anything the reviewer should
    push on") never false-positive."""
    normalized = " ".join(path.read_text().split())
    for m in _GIT_PUSH_COMMAND_RE.finditer(normalized):
        window = normalized[max(0, m.start() - 25) : m.end()]
        if not _PUSH_NEGATION_RE.search(window):
            return True
    return False


def test_isolation_pin_invariant_matches_real_push_directives() -> None:
    """REVIEW.md C-1 regression: enforce the invariant, not just document it -- EVERY
    task in the rendered workflow whose instruction file contains a real `git push`
    directive must be pinned `isolation: "none"`. This is what actually prevents R-22
    from reappearing for any task (present or future) that this template wires up to
    an instruction file which pushes, regardless of whether anyone remembers to name
    it explicitly in a hand-maintained list."""
    raw = (TEMPLATE_DIR / "workflow.json.tmpl").read_text()
    workflow = json.loads(_render(raw, DUMMY_VALUES))
    offenders = []
    for task in workflow["tasks"]:
        instr_path = INSTRUCTIONS_DIR / Path(task["instruction"]).name
        if instr_path.is_file() and _instruction_has_real_push_directive(instr_path):
            if task.get("isolation") != "none":
                offenders.append(task["id"])
    assert not offenders, (
        f"task(s) with a real git-push directive but no isolation:none pin: {offenders}"
    )


def test_final_push_instruction_documents_why_it_always_runs_unisolated() -> None:
    text = (INSTRUCTIONS_DIR / "90-final-push.md").read_text()
    assert "always runs unisolated" in text.lower()
    assert "isolation" in text.lower() and "none" in text


def test_workflow_json_tmpl_ships_no_live_integration_block() -> None:
    """A live, non-default `integration`/`scheduling` block would trip spec.py's V5
    ("integration configured but no task resolves to isolation=worktree") on every
    single default render -- exactly the "no new warnings" regression this template
    must not ship. The example lives in README.md's "Parallel isolation" section
    instead; see test_conflict_instructions.py for a test that the documented recipe,
    once applied by a user, actually validates clean."""
    raw = (TEMPLATE_DIR / "workflow.json.tmpl").read_text()
    workflow = json.loads(_render(raw, DUMMY_VALUES))
    assert "integration" not in workflow
    assert "scheduling" not in workflow


_PUSH_NEGATION_RE = re.compile(
    r"(not push|never `?git push|nothing is pushed|no push)", re.IGNORECASE
)


def test_no_push_directives_in_isolated_per_task_instructions() -> None:
    """Every mention of "push" in these six files must sit in a NEGATIVE context
    (do not push / never git push) -- markdown line-wraps mid-sentence, so this
    normalizes whitespace first rather than checking line-by-line."""
    offenders: list[str] = []
    for name in PUSH_DIRECTIVE_FILES:
        normalized = " ".join((INSTRUCTIONS_DIR / name).read_text().split())
        for m in re.finditer(r"\bpush(ed)?\b", normalized, re.IGNORECASE):
            window = normalized[max(0, m.start() - 25) : m.end()]
            if not _PUSH_NEGATION_RE.search(window):
                offenders.append(f"{name}: ...{window}...")
    assert not offenders, "git-push directive(s) found:\n" + "\n".join(offenders)


def test_push_directive_files_say_the_engine_integrates() -> None:
    for name in PUSH_DIRECTIVE_FILES:
        normalized = " ".join((INSTRUCTIONS_DIR / name).read_text().split())
        assert "the engine integrates your work" in normalized, name


def test_review_instructions_no_longer_reference_pushed_commits() -> None:
    for name in REVIEW_WORDING_FILES:
        normalized = " ".join((INSTRUCTIONS_DIR / name).read_text().split()).lower()
        assert "pushed commits" not in normalized, name
        assert "actual pushed" not in normalized, name
        assert "nothing is pushed" in normalized, name
        assert "current branch" in normalized, name


def test_task_breakdown_declares_hotspots_as_optional_input() -> None:
    text = (INSTRUCTIONS_DIR / "07-task-breakdown.md").read_text()
    assert ".ao/hotspots.json" in text
    assert "OPTIONAL" in text
    assert "ao hotspots" in text


def test_task_breakdown_minimize_collision_bullet_rewritten() -> None:
    text = (INSTRUCTIONS_DIR / "07-task-breakdown.md").read_text()
    assert "touches" in text
    assert "avoid concentrating" in text
    # The old bullet pushed the agent to serialize depends_on purely to dodge a file
    # collision -- that phrasing must be gone, not merely appended to (AC-4).
    assert "make one depend on the other" not in text
    assert "do not add a cross-task" in text.lower()


def test_breakdown_contract_widens_field_allowlist_with_touches_and_isolation() -> None:
    """Hard Rule 5's field allowlist wraps across several lines -- extract the whole
    numbered paragraph (up to the next "N. " item), not just its first line."""
    lines = (TEMPLATE_DIR / "breakdown-contract.md.tmpl").read_text().splitlines()
    start = next(
        i for i, line in enumerate(lines) if line.strip().startswith("5. Do not add fields beyond")
    )
    end = next(i for i in range(start + 1, len(lines)) if re.match(r"^\d+\. ", lines[i].strip()))
    hard_rule_5 = " ".join(lines[start:end])
    assert "touches" in hard_rule_5
    assert "isolation" in hard_rule_5


def test_breakdown_contract_touches_guidance_sentence_present() -> None:
    text = (TEMPLATE_DIR / "breakdown-contract.md.tmpl").read_text()
    normalized = " ".join(text.split())
    assert (
        "`touches` is a best-effort glob list; being incomplete is fine and never blocks "
        "scheduling." in normalized
    )
    assert (
        "prefer NOT to add a cross-task `depends_on` purely to avoid file collisions" in normalized
    )


def test_template_yaml_required_agents_includes_merge_resolver() -> None:
    manifest = _load_manifest()
    assert "merge-resolver" in manifest["required_agents"]


def test_readme_documents_parallel_isolation_section() -> None:
    text = (TEMPLATE_DIR / "README.md").read_text()
    assert "## Parallel isolation" in text
    assert 'isolation: "worktree"' in text or "isolation: worktree" in text
    assert "verify_command" in text
    assert "AO_STATE_DIR" in text or "AO_WORKTREE_ROOT" in text
    # S-5: unset verify_command makes a rerere replay functionally unreviewed.
    assert "unreviewed" in text.lower()
    # NFR-6: cold-rebuild caveat + shared build-cache recipe, not ignored.
    assert "cold" in text.lower() and "rebuild" in text.lower()
    assert "CARGO_TARGET_DIR" in text or "sccache" in text or "ccache" in text


# ---------------------------------------------------------------------------
# E-Wk9Tz3 T-Lr6Ka3 (additive): the packaged T2 resolver instruction this template's
# README already documents the `merge-resolver` agent recipe around (`resolver_agent`,
# S-2's `disallowed_tools` requirement) now actually ships in the package. Lives OUTSIDE
# `TEMPLATE_DIR` (`templates/builtin/instructions/`, a sibling of `routed-runner/`, not a
# routed-runner-specific asset) -- checked here rather than left undone, per T-Lr6Ka3's
# own TASK.md instruction to add these cases in this file.
# ---------------------------------------------------------------------------

MERGE_RESOLVE_PATH = (
    _REPO_ROOT
    / "src"
    / "agent_orchestrator"
    / "templates"
    / "builtin"
    / "instructions"
    / "merge-resolve.md"
)


def test_merge_resolve_instruction_ships_in_the_package() -> None:
    assert MERGE_RESOLVE_PATH.is_file()


def test_merge_resolve_instruction_matches_the_readmes_recipe() -> None:
    """The README's `merge-resolver` recipe (`resolver_agent`, `disallowed_tools`) only
    makes sense once this file exists -- a minimal cross-check that the two agree on the
    basics (never running `rebase --continue`/pushing itself)."""
    text = MERGE_RESOLVE_PATH.read_text(encoding="utf-8").lower()
    assert "rebase --continue" in text
    assert "push" in text
    readme = (TEMPLATE_DIR / "README.md").read_text(encoding="utf-8")
    assert "merge-resolver" in readme


# ---------------------------------------------------------------------------
# 7. `agents.recommended.json.tmpl` content (D1, E-Vt6Lp2-template-cost-hygiene): the
# recommended --autocompact seed a workspace merges into its own agents.json.
# ---------------------------------------------------------------------------

AGENTS_RECOMMENDED_PATH = TEMPLATE_DIR / "agents.recommended.json.tmpl"

# The 9 of 11 required_agents roles judged to do long, multi-turn agentic work (early-gate
# architect+reviewer reviewed choice -- see EPIC.md "Early-gate review -- outcome"), split (Rev
# 2, docs-md/template-cost-hygiene-hld.md sec 3.4) by what the role's task actually does:
# architecture/design synthesis (wider context, higher threshold) vs. narrower dev-cycle work
# (threshold fires on most substantive tasks regardless of exact value -- set more assertively).
ARCHITECTURE_ROLES = frozenset({"architect", "architect-opus", "reviewer-opus"})
DEV_CYCLE_ROLES = frozenset(
    {"developer", "full-tester", "manager", "market-surveyor", "reviewer", "tester"}
)
LONG_MULTI_TURN_ROLES = ARCHITECTURE_ROLES | DEV_CYCLE_ROLES

# Deliberately excluded: short-lived/mechanical (git-operator) or security-sensitive over
# unreviewed content where compaction fidelity risk outweighs the benefit (merge-resolver).
SHORT_LIVED_EXCLUDED_ROLES = frozenset({"git-operator", "merge-resolver"})


def _load_agents_recommended() -> dict[str, Any]:
    return json.loads(AGENTS_RECOMMENDED_PATH.read_text(encoding="utf-8"))


def test_agents_recommended_is_valid_json_with_agents_json_shaped_top_level() -> None:
    data = _load_agents_recommended()
    # Mirrors specs/examples/agents.json's top-level shape (version + agents), plus disclosed
    # underscore-prefixed metadata keys -- deliberately NOT wholesale-loadable as a real
    # agents.json (additionalProperties: false would reject the metadata keys), which is the
    # point: a workspace must consciously merge fields, not `cp` this file over its own.
    assert data["version"] == "1.0"
    assert isinstance(data["agents"], dict)
    assert data["_note"], "must document that this is a merge reference, not a live config"
    assert "keep_existing" in data["_note"] or "once" in data["_note"].lower()


def test_agents_recommended_covers_exactly_the_long_multi_turn_roles() -> None:
    data = _load_agents_recommended()
    assert set(data["agents"]) == LONG_MULTI_TURN_ROLES
    assert set(data["agents"]).isdisjoint(SHORT_LIVED_EXCLUDED_ROLES)


def test_agents_recommended_uses_extra_args_not_command_template() -> None:
    """Early-gate reviewer finding: `command_template` fully overrides the base argv, so a
    recommended `command_template` array would clobber a workspace's own tuning on merge.
    `extra_args` is the additive field the engine always appends after `command_template`
    (executors/claude_cli.py) -- this is what makes the recommendation safely mergeable."""
    data = _load_agents_recommended()
    for role, spec in data["agents"].items():
        assert "command_template" not in spec, role
        assert spec.get("executor") == "claude_cli", role
        assert "--autocompact" in spec.get("extra_args", []), role


def test_agents_recommended_autocompact_threshold_matches_documented_default() -> None:
    """The design doc (docs-md/template-cost-hygiene-hld.md sec 3.4) is the single source of
    the chosen --autocompact values; this test pins the seed file to stay in sync with it."""
    data = _load_agents_recommended()
    assert data["_autocompact_defaults"] == {
        "architecture_roles": "500000",
        "dev_cycle_roles": "180000",
    }
    for role, spec in data["agents"].items():
        extra_args = spec["extra_args"]
        idx = extra_args.index("--autocompact")
        expected = "500000" if role in ARCHITECTURE_ROLES else "180000"
        assert extra_args[idx + 1] == expected, role


def test_agents_recommended_every_entry_valid_against_agents_schema_shape() -> None:
    """Each individual agent entry (not the whole file) must be a valid partial AgentSpec --
    only fields specs/agents.schema.json's per-agent additionalProperties:false allows."""
    schema_path = _REPO_ROOT / "specs" / "agents.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    allowed_fields = set(schema["$defs"]["agent"]["properties"])
    data = _load_agents_recommended()
    for role, spec in data["agents"].items():
        unknown = set(spec) - allowed_fields
        assert not unknown, f"{role}: unknown AgentSpec field(s) {unknown}"
        assert "executor" in spec, role  # the schema's one required field


def test_readme_documents_agents_recommended_section() -> None:
    text = (TEMPLATE_DIR / "README.md").read_text()
    assert "agents.recommended.json" in text
    assert "--autocompact" in text
    assert "extra_args" in text
    assert "merge reference" in text.lower() or "not a live config" in text.lower()
    # The 9-vs-2 role split rationale must be documented, not just implemented.
    assert "git-operator" in text
    assert "merge-resolver" in text
