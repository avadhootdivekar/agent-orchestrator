"""U-K2..U-K12: determinism, sensitivity, insensitivity, normalization and refusals of the key."""

from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator import models
from agent_orchestrator.cache.constants import (
    AGENT_KEY_FIELDS,
    AGENT_NON_KEY_FIELDS,
    COMPONENT_DIGEST_CHARS,
    KEY_PLACEHOLDER_ID,
    KEY_PLACEHOLDER_TIMEOUT,
    PRIOR_ABSENT,
    REASON_CONTROL_OUTPUT,
    REASON_DUPLICATE_OUTPUT,
    REASON_INPUT_MISSING,
    REASON_INPUT_NOT_REGULAR,
    REASON_INPUT_TOO_LARGE,
    REASON_KEY_ENCODING,
    REASON_OUTPUT_NOT_REGULAR,
    REASON_PATH_IN_CACHE_DIR,
    REASON_PATH_REJECTED,
    REASON_PROMPT_RENDER_ERROR,
    REASON_SENSITIVE_OUTPUT,
)
from agent_orchestrator.cache.keys import build_cache_key, canonical_json, summary_from_doc
from agent_orchestrator.cache.types import UncacheableError
from agent_orchestrator.cache.types import canonical_json as types_canonical_json
from agent_orchestrator.executors.prompt import build_prompt
from agent_orchestrator.models import AgentSpec, RetryPolicy, TaskContext
from tests.cache.keys_fixture import (
    GV1_HEAD,
    default_agent,
    default_task,
    key_for,
    make_deps,
    make_request,
    write_files,
)

needs_posix = pytest.mark.skipif(sys.platform == "win32", reason="symlinks are POSIX-only here")
HEAD2 = "fedcba9876543210fedcba9876543210fedcba98"


@pytest.fixture(autouse=True)
def _cache_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AO_CACHE", raising=False)


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    write_files(root)
    return root


def refusal(ws: Path, **overrides: Any) -> UncacheableError:
    with pytest.raises(UncacheableError) as info:
        key_for(ws, **overrides)
    return info.value


# ------------------------------------------------------------------------------------ U-K2
def test_one_hundred_builds_give_one_key(ws: Path) -> None:
    assert len({key_for(ws).key for _ in range(100)}) == 1


def test_the_same_tree_under_another_absolute_path_gives_the_same_key(tmp_path: Path) -> None:
    keys = []
    for name in ("one", "a/deeper/two"):
        root = tmp_path / name
        root.mkdir(parents=True)
        write_files(root)
        keys.append(key_for(root))
    assert keys[0].key == keys[1].key
    assert keys[0].components == keys[1].components


def test_a_moved_workspace_keeps_its_keys(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    write_files(src)
    before = key_for(src).key
    dst = tmp_path / "moved"
    shutil.move(str(src), str(dst))
    assert key_for(dst).key == before


def test_the_key_is_a_sha256_and_components_are_twelve_hex_chars(ws: Path) -> None:
    got = key_for(ws)
    assert len(got.key) == 64 and int(got.key, 16) >= 0
    assert set(got.components) == {
        "key_schema",
        "agent",
        "argv",
        "executor_fingerprint",
        "prompt",
        "instruction",
        "general_instructions",
        "inputs",
        "dynamic_inputs",
        "outputs",
        "repo_heads",
    }
    assert all(len(v) == COMPONENT_DIGEST_CHARS for v in got.components.values())


# ------------------------------------------------------------------------------------ U-K3
def _write(rel: str, text: str) -> Callable[[Path], dict[str, Any]]:
    def mutate(ws: Path) -> dict[str, Any]:
        path = ws / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return {}

    return mutate


def _agent(**kw: Any) -> Callable[[Path], dict[str, Any]]:
    return lambda ws: {"agent": default_agent(**kw)}


def _task(**kw: Any) -> Callable[[Path], dict[str, Any]]:
    return lambda ws: {"task": default_task(**kw)}


def _override(**kw: Any) -> Callable[[Path], dict[str, Any]]:
    return lambda ws: dict(kw)


def _two_inputs_swapped(ws: Path) -> dict[str, Any]:
    (ws / "docs" / "b.md").write_text("beta\n")
    return {"task": default_task(inputs=["docs/notes.md", "docs/b.md"])}


def _prior(ws: Path) -> dict[str, Any]:
    (ws / "out").mkdir()
    (ws / "out" / "summary.md").write_text("an earlier result\n")
    return {}


def _working_dir(ws: Path) -> dict[str, Any]:
    (ws / "sub").mkdir()
    return {"agent": default_agent(working_dir="sub")}


SENSITIVE_CASES: dict[str, Callable[[Path], dict[str, Any]]] = {
    "instruction content": _write("specs/instr/summarize.md", "Different.\n"),
    "general instruction content": _write(".ao/house-rules.md", "Be verbose.\n"),
    "input content": _write("docs/notes.md", "beta\n"),
    "output set": _task(outputs=["out/summary.md", "out/extra.md"]),
    "output path": _task(outputs=["out/other.md"]),
    "output prior": _prior,
    "repo head": _override(repo_heads={"core": HEAD2}),
    "repo set": _override(repo_paths={"core": "WS", "extra": "WS"}),
    "model": _agent(model="opus"),
    "effort": _agent(effort="high"),
    "max_turns": _agent(max_turns=7),
    "prompt_template": _agent(prompt_template="Do {instruction} with {inputs} into {outputs}."),
    "command_template": _agent(command_template=["claude", "-p", "{prompt}", "--verbose"]),
    "extra_args": _agent(extra_args=["--permission-mode", "plan"]),
    "disallowed_tools": _agent(disallowed_tools=["WebSearch"]),
    "forced_disallowed_tools": _agent(forced_disallowed_tools=["Bash"]),
    "exclude_dynamic_system_prompt_sections": _agent(exclude_dynamic_system_prompt_sections=True),
    "context_window": _agent(context_window="shared"),
    "working_dir": _working_dir,
    "cli version": lambda ws: {"deps_version": "2.1.300 (Claude Code)"},
    "allowlisted env var": _override(environ={"ANTHROPIC_MODEL": "claude-opus-x"}),
    "CLAUDE.md": _write("CLAUDE.md", "project rules\n"),
    ".claude/agents/x.md": _write(".claude/agents/x.md", "an agent\n"),
    ".mcp.json": _write(".mcp.json", "{}\n"),
    "input order": _two_inputs_swapped,
    "executor": _agent(executor="fake"),
}


@pytest.mark.parametrize("case", sorted(SENSITIVE_CASES))
def test_sensitivity(ws: Path, case: str) -> None:
    base = key_for(ws)
    overrides = SENSITIVE_CASES[case](ws)
    if "repo_paths" in overrides:
        overrides["repo_paths"] = {k: str(ws.resolve()) for k in overrides["repo_paths"]}
    version = overrides.pop("deps_version", None)
    deps = make_deps(version) if version else None
    changed = key_for(ws, deps, **overrides)
    assert changed.key != base.key


def test_sensitivity_to_input_order_itself(ws: Path) -> None:
    (ws / "docs" / "b.md").write_text("beta\n")
    ab = key_for(ws, task=default_task(inputs=["docs/notes.md", "docs/b.md"]))
    ba = key_for(ws, task=default_task(inputs=["docs/b.md", "docs/notes.md"]))
    assert ab.key != ba.key
    assert ab.components["inputs"] != ba.components["inputs"]


def test_sensitivity_to_dynamic_input_content_and_order(ws: Path) -> None:
    (ws / "docs" / "dyn.md").write_text("one\n")
    (ws / "docs" / "dyn2.md").write_text("two\n")
    first = key_for(ws, dynamic_input_paths=("docs/dyn.md",))
    assert first.key != key_for(ws).key
    (ws / "docs" / "dyn.md").write_text("changed\n")
    assert key_for(ws, dynamic_input_paths=("docs/dyn.md",)).key != first.key
    both = key_for(ws, dynamic_input_paths=("docs/dyn.md", "docs/dyn2.md"))
    swapped = key_for(ws, dynamic_input_paths=("docs/dyn2.md", "docs/dyn.md"))
    assert both.key != swapped.key


def test_sensitivity_to_argv_construction(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = key_for(ws)
    monkeypatch.setitem(models.EFFORT_MAX_TURNS, "medium", 31)
    after = key_for(ws)
    assert before.key != after.key
    assert before.components["argv"] != after.components["argv"]
    assert before.components["prompt"] == after.components["prompt"]


def test_an_env_var_outside_the_allowlist_is_ignored(ws: Path) -> None:
    assert key_for(ws, environ={"SOME_OTHER_VAR": "x", "PATH": "/bin"}).key == key_for(ws).key


def test_repo_heads_are_ignored_when_not_included(ws: Path) -> None:
    on = key_for(ws)
    off = key_for(ws, include_repo_heads=False)
    off2 = key_for(ws, include_repo_heads=False, repo_heads={"core": HEAD2})
    assert off.key == off2.key != on.key
    assert off.summary.repo_heads is None
    assert off.components["repo_heads"] != on.components["repo_heads"]


def test_an_absent_prior_differs_from_a_present_prior_in_the_outputs_component(ws: Path) -> None:
    absent = key_for(ws)
    _prior(ws)
    present = key_for(ws)
    assert absent.components["outputs"] != present.components["outputs"]
    assert absent.components["prompt"] == present.components["prompt"]


# ------------------------------------------------------------------------------------ U-K4
def test_the_key_ignores_ids_timeouts_retries_and_graph_wiring(ws: Path) -> None:
    base = key_for(ws)
    other_task = default_task(
        id="some-other-id",
        timeout_seconds=1234,
        retries=RetryPolicy(max_attempts=5, backoff_seconds=3.0),
        depends_on=["a", "b"],
        touches=["src/x.py"],
        skip_if_outputs_exist=False,
        join="any",
    )
    assert key_for(ws, task=other_task).key == base.key


def test_the_key_ignores_forbidden_task_models(ws: Path) -> None:
    changed = key_for(ws, agent=default_agent(forbidden_task_models=["haiku"]))
    assert changed.key == key_for(ws).key


def test_an_api_key_never_enters_the_key(ws: Path) -> None:
    secret = "sk-ant-SECRET-VALUE-123"
    with_secret = key_for(ws, environ={"ANTHROPIC_API_KEY": secret, "ANTHROPIC_AUTH_TOKEN": secret})
    assert with_secret.key == key_for(ws).key
    dumped = with_secret.summary.model_dump_json() + repr(with_secret)
    assert secret not in dumped


def test_agent_field_sets_cover_the_ignored_fields() -> None:
    # The fields U-K4 relies on being unkeyed really are classified as non-key.
    assert "forbidden_task_models" in AGENT_NON_KEY_FIELDS


# ------------------------------------------------------------------------------------ U-K5
@needs_posix
def test_n1_paths_in_the_key_are_resolved_workspace_relative_posix(ws: Path) -> None:
    (ws / "docs" / "alias.md").symlink_to("notes.md")
    base = key_for(ws)
    for spelling in (
        "docs/alias.md",
        "./docs/../docs/notes.md",
        str(ws.resolve() / "docs/notes.md"),
    ):
        got = key_for(ws, task=default_task(inputs=[spelling]))
        assert got.key == base.key
        assert got.summary.inputs == ["docs/notes.md"]


def test_n2_outputs_are_sorted_in_the_key_but_keep_declared_order_in_the_prompt(ws: Path) -> None:
    ab = key_for(ws, task=default_task(outputs=["out/a.md", "out/b.md"]))
    ba = key_for(ws, task=default_task(outputs=["out/b.md", "out/a.md"]))
    assert ab.components["outputs"] == ba.components["outputs"]  # structured field is sorted
    assert ab.components["prompt"] != ba.components["prompt"]  # prompt keeps declared order
    assert ab.key != ba.key
    assert ab.output_paths == ba.output_paths == ("out/a.md", "out/b.md")
    assert ab.summary.outputs == ["out/a.md", "out/b.md"]


def test_n2_general_instructions_and_dynamic_inputs_keep_order(ws: Path) -> None:
    (ws / ".ao" / "second.md").write_text("two\n")
    first = str(ws.resolve() / ".ao" / "house-rules.md")
    second = str(ws.resolve() / ".ao" / "second.md")
    ab = key_for(ws, general_instruction_paths=(first, second))
    ba = key_for(ws, general_instruction_paths=(second, first))
    assert ab.key != ba.key
    assert ab.summary.general_instructions == [".ao/house-rules.md", ".ao/second.md"]


def test_n3_the_absolute_workspace_root_never_appears(ws: Path) -> None:
    got = key_for(ws)
    blob = got.summary.model_dump_json() + repr(dict(got.components)) + got.key
    assert str(ws.resolve()) not in blob
    assert all(not p.startswith("/") for p in got.output_paths)
    assert got.summary.digests and all(not p.startswith("/") for p in got.summary.digests)


def test_n4_run_and_task_ids_and_timeouts_are_excluded(ws: Path) -> None:
    got = key_for(ws, task=default_task(id="unique-task-id-xyz", timeout_seconds=99))
    assert "unique-task-id-xyz" not in got.summary.model_dump_json()
    assert got.key == key_for(ws).key


def test_n6_directory_walks_skip_dot_git_the_state_dir_and_own_outputs(ws: Path) -> None:
    (ws / "tree").mkdir()
    (ws / "tree" / "a.txt").write_text("a")
    task = default_task(inputs=["tree", "."], outputs=["tree/out.md"])
    base = key_for(ws, task=task)
    (ws / "tree" / ".git").mkdir()
    (ws / "tree" / ".git" / "HEAD").write_text("ref")
    (ws / ".git").mkdir()
    (ws / ".git" / "index").write_text("x")
    (ws / ".orchestrator" / "cache").mkdir(parents=True)
    (ws / ".orchestrator" / "state.json").write_text("{}")
    (ws / "tree" / "out.md").write_text("an output produced by the run")  # own declared output
    assert key_for(ws, task=task).components["inputs"] == base.components["inputs"]
    (ws / "tree" / "a.txt").write_text("changed")
    assert key_for(ws, task=task).components["inputs"] != base.components["inputs"]


def test_n7_dynamic_inputs_resolve_against_the_workspace_root(ws: Path) -> None:
    (ws / "docs" / "dyn.md").write_text("dyn\n")
    got = key_for(ws, dynamic_input_paths=("docs/dyn.md",))
    assert got.summary.dynamic_inputs == ["docs/dyn.md"]
    err = refusal(ws, dynamic_input_paths=("docs/not-there.md",))
    assert err.reason == REASON_INPUT_MISSING


def test_n8_context_files_from_every_directory_down_to_the_cwd_are_keyed(ws: Path) -> None:
    (ws / "a" / "b").mkdir(parents=True)
    agent = default_agent(working_dir="a/b")
    base = key_for(ws, agent=agent)
    for rel in ("a/CLAUDE.md", "a/b/.mcp.json"):
        (ws / rel).write_text("ctx\n")
        changed = key_for(ws, agent=agent)
        assert changed.components["executor_fingerprint"] != base.components["executor_fingerprint"]
        (ws / rel).unlink()
    (ws / "a" / "sibling").mkdir()
    (ws / "a" / "sibling" / "CLAUDE.md").write_text("not on the path\n")
    assert key_for(ws, agent=agent).key == base.key  # off the root-to-cwd path: not keyed


# ------------------------------------------------------------------------------------ U-K6
def test_preseed_makes_the_settle_time_recompute_equal_the_lookup_key(ws: Path) -> None:
    task = default_task(inputs=["docs/notes.md", "out/summary.md"], outputs=["out/summary.md"])
    (ws / "out").mkdir()
    (ws / "out" / "summary.md").write_text("prior content\n")
    deps = make_deps()
    lookup = build_cache_key(make_request(ws, task=task), deps)
    # The agent overwrites the output (it is also an input).
    (ws / "out" / "summary.md").write_text("what the agent wrote\n")
    naive = build_cache_key(make_request(ws, task=task), deps)
    settled = build_cache_key(make_request(ws, task=task), deps, preseed=lookup.preseed)
    assert naive.key != lookup.key
    assert settled.key == lookup.key
    (abs_out,) = lookup.preseed
    prior = lookup.preseed[abs_out]
    assert prior is not None and prior.sha256 in {e for e in lookup.summary.digests.values()}


def test_preseed_none_means_absent(ws: Path) -> None:
    deps = make_deps()
    lookup = build_cache_key(make_request(ws), deps)
    (abs_out,) = lookup.preseed
    assert lookup.preseed[abs_out] is None
    (ws / "out").mkdir()
    (ws / "out" / "summary.md").write_text("now present")
    assert build_cache_key(make_request(ws), deps, preseed=lookup.preseed).key == lookup.key


def test_an_input_that_is_an_absent_output_is_input_missing(ws: Path) -> None:
    err = refusal(ws, task=default_task(inputs=["out/summary.md"]))
    assert err.reason == REASON_INPUT_MISSING and err.detail == "out/summary.md"


# ------------------------------------------------------------------------------------ U-K7
def test_the_normalized_prompt_is_invariant_to_run_and_task_ids(ws: Path) -> None:
    def ctx(run_id: str, task_id: str, timeout: int) -> TaskContext:
        return TaskContext(
            run_id=run_id,
            task_id=task_id,
            agent=default_agent(),
            instruction_path="specs/instr/summarize.md",
            input_paths=["docs/notes.md"],
            output_paths=["out/summary.md"],
            repo_paths={"core": "."},
            timeout_seconds=timeout,
        )

    placeholder = ctx(KEY_PLACEHOLDER_ID, KEY_PLACEHOLDER_ID, KEY_PLACEHOLDER_TIMEOUT)
    assert build_prompt(placeholder) == build_prompt(ctx("run-20261005", "task-77", 600))


# ------------------------------------------------------------------------------------ U-K8
def test_agent_field_classification_is_complete_and_disjoint() -> None:
    fields = set(AgentSpec.model_fields)
    classified = set(AGENT_KEY_FIELDS) | set(AGENT_NON_KEY_FIELDS)
    unclassified = sorted(fields - classified)
    stale = sorted(classified - fields)
    assert classified == fields and not (AGENT_KEY_FIELDS & AGENT_NON_KEY_FIELDS), (
        f"AgentSpec changed. Unclassified fields: {unclassified}; stale names: {stale}. "
        "Classify each new field in cache/constants.py (AGENT_KEY_FIELDS if it can change what "
        "the agent does, AGENT_NON_KEY_FIELDS otherwise) and decide whether KEY_SCHEMA_VERSION "
        "must be bumped (HLD 8.2.7 Versioning)."
    )


# ------------------------------------------------------------------------------------ U-K9
def test_a_traversal_path_is_rejected(ws: Path) -> None:
    for task in (
        default_task(inputs=["../outside.md"]),
        default_task(instruction="../../etc/passwd"),
        default_task(outputs=["../escape.md"]),
    ):
        assert refusal(ws, task=task).reason == REASON_PATH_REJECTED


def test_an_absolute_path_outside_the_workspace_is_rejected(ws: Path) -> None:
    assert refusal(ws, task=default_task(inputs=["/etc/hostname"])).reason == REASON_PATH_REJECTED


def test_a_nul_byte_is_rejected(ws: Path) -> None:
    assert refusal(ws, task=default_task(inputs=["docs/no\0tes.md"])).reason == REASON_PATH_REJECTED


@needs_posix
def test_a_symlink_loop_is_rejected(ws: Path) -> None:
    (ws / "loop-a").symlink_to("loop-b")
    (ws / "loop-b").symlink_to("loop-a")
    assert refusal(ws, task=default_task(inputs=["loop-a"])).reason == REASON_PATH_REJECTED


@needs_posix
def test_a_claude_md_symlink_out_of_the_workspace_is_rejected(ws: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("secrets")
    (ws / "CLAUDE.md").symlink_to(outside)
    assert refusal(ws).reason == REASON_PATH_REJECTED


def test_a_path_inside_the_cache_directory_is_refused(ws: Path) -> None:
    (ws / ".orchestrator" / "cache").mkdir(parents=True)
    (ws / ".orchestrator" / "cache" / "x.json").write_text("{}")
    for task in (
        default_task(inputs=[".orchestrator/cache/x.json"]),
        default_task(inputs=[".orchestrator/cache"]),
        default_task(instruction=".orchestrator/cache/x.json"),
    ):
        assert refusal(ws, task=task).reason == REASON_PATH_IN_CACHE_DIR


def test_an_absolute_general_instruction_or_repo_path_is_guarded(ws: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("x")
    assert refusal(ws, general_instruction_paths=(str(outside),)).reason == REASON_PATH_REJECTED
    assert refusal(ws, repo_paths={"core": str(tmp_path)}).reason == REASON_PATH_REJECTED
    cache_dir = str(ws.resolve() / ".orchestrator" / "cache")
    assert refusal(ws, repo_paths={"core": cache_dir}).reason == REASON_PATH_IN_CACHE_DIR


def test_duplicate_outputs_are_refused(ws: Path) -> None:
    for outputs in (["out/a.md", "out/a.md"], ["out/a.md", "out/../out/a.md"]):
        assert refusal(ws, task=default_task(outputs=outputs)).reason == REASON_DUPLICATE_OUTPUT


# ----------------------------------------------------------------------------------- U-K10
@pytest.mark.parametrize(
    "output",
    [
        ".git/hooks/pre-commit",
        ".github/workflows/ci.yml",
        ".claude/settings.json",
        ".ao/config.yaml",
        ".orchestrator/state.json",
        "CLAUDE.md",
        "sub/AGENTS.md",
        ".mcp.json",
        ".envrc",
        ".gitlab-ci.yml",
        "Jenkinsfile",
        ".githooks/pre-commit",
        ".circleci/config.yml",
        ".vscode/tasks.json",
        ".cursor/rules/x.mdc",
        ".pre-commit-config.yaml",
        ".gitmodules",
        ".gitattributes",
    ],
)
def test_sensitive_outputs_are_refused(ws: Path, output: str) -> None:
    err = refusal(ws, task=default_task(outputs=[output]))
    assert err.reason == REASON_SENSITIVE_OUTPUT and err.detail == output


@needs_posix
def test_an_output_symlinked_into_git_hooks_is_sensitive(ws: Path) -> None:
    (ws / ".git" / "hooks").mkdir(parents=True)
    (ws / "out").mkdir()
    (ws / "out" / "summary.md").symlink_to(ws / ".git" / "hooks" / "post-commit")
    err = refusal(ws)
    assert err.reason == REASON_SENSITIVE_OUTPUT and ".git/hooks/post-commit" in err.detail


def test_a_non_sensitive_lookalike_is_allowed(ws: Path) -> None:
    assert key_for(ws, task=default_task(outputs=["docs/claude-guide.md", "notes/claude-notes.md"]))


def test_a_case_variant_of_a_sensitive_path_is_refused(ws: Path) -> None:
    """SEC-06: the match is case-insensitive (a case-insensitive filesystem aliases the names)."""
    err = refusal(ws, task=default_task(outputs=["docs/claude.md"]))
    assert err.reason == REASON_SENSITIVE_OUTPUT and err.detail == "docs/claude.md"


def test_an_engine_read_control_file_cannot_be_an_output(ws: Path) -> None:
    control = frozenset({str(ws.resolve() / "out" / "summary.md")})
    err = refusal(ws, control_paths_abs=control)
    assert err.reason == REASON_CONTROL_OUTPUT and err.detail == "out/summary.md"


def test_an_output_that_exists_but_is_not_a_regular_file_is_refused(ws: Path) -> None:
    (ws / "out" / "summary.md").mkdir(parents=True)
    err = refusal(ws)
    assert err.reason == REASON_OUTPUT_NOT_REGULAR and err.detail == "out/summary.md"


@needs_posix
def test_an_output_that_is_a_dangling_symlink_is_treated_as_absent_at_its_target(ws: Path) -> None:
    (ws / "out").mkdir()
    (ws / "out" / "summary.md").symlink_to(ws / "out" / "real.md")
    got = key_for(ws)
    assert got.output_paths == ("out/real.md",)  # N-1: resolved path
    assert next(iter(got.preseed.values())) is None


# ----------------------------------------------------------------------------------- U-K11
def test_an_unknown_template_placeholder_is_a_render_error(ws: Path) -> None:
    for template in ("Do {nope}.", "Do {0}.", "Do {instruction"):
        err = refusal(ws, agent=default_agent(prompt_template=template))
        assert err.reason == REASON_PROMPT_RENDER_ERROR
    assert refusal(ws, agent=default_agent(prompt_template="{nope}")).detail == "KeyError"


def test_a_value_json_cannot_encode_is_key_encoding(ws: Path) -> None:
    bad = default_agent().model_copy(update={"max_turns": float("nan")})
    assert refusal(ws, agent=bad).reason == REASON_KEY_ENCODING


def test_a_summary_beyond_the_entry_bounds_is_key_encoding(ws: Path) -> None:
    many = ["docs/notes.md"] * 4097  # one hash (memoized), but over MAX_LIST_ITEMS in the summary
    err = refusal(ws, task=default_task(inputs=many))
    assert err.reason == REASON_KEY_ENCODING and err.detail == "summary_out_of_bounds"


def test_canonical_json_is_the_single_serializer() -> None:
    assert canonical_json is types_canonical_json
    assert canonical_json({"b": 1, "a": [1, 2]}) == '{"a":[1,2],"b":1}'
    with pytest.raises(ValueError, match="Out of range float"):
        canonical_json({"x": float("inf")})


# ----------------------------------------------------------------------------------- U-K12
def test_secrets_argv_prompt_and_templates_stay_out_of_the_summary(ws: Path) -> None:
    secret = "sk-ant-api03-SECRET-IN-EXTRA-ARGS"
    agent = default_agent(
        extra_args=["--api-key", secret],
        prompt_template="PRIVATE TEMPLATE {instruction} {inputs} {outputs} {repos}",
        command_template=["claude", "-p", "{prompt}", "--private-flag"],
    )
    got = key_for(ws, agent=agent)
    dumped = got.summary.model_dump_json()
    for forbidden in (secret, "--api-key", "PRIVATE TEMPLATE", "--private-flag", "--output-format"):
        assert forbidden not in dumped
    assert "Follow the instructions" not in dumped and "PRIVATE" not in repr(got.components)
    assert got.summary.model == "sonnet" and got.summary.max_turns is None


def test_summary_carries_only_non_sensitive_fields(ws: Path) -> None:
    got = key_for(ws)
    assert set(got.summary.model_dump()) == {
        "key_schema",
        "executor",
        "model",
        "effort",
        "max_turns",
        "instruction",
        "inputs",
        "dynamic_inputs",
        "general_instructions",
        "outputs",
        "digests",
        "repo_heads",
        "components",
    }
    assert got.summary.instruction == "specs/instr/summarize.md"
    assert got.summary.general_instructions == [".ao/house-rules.md"]
    assert got.summary.repo_heads == {"core": GV1_HEAD}
    assert dict(got.summary.components) == dict(got.components)


def test_summary_from_doc_is_a_pure_projection() -> None:
    doc = {
        "key_schema": 1,
        "agent": {"executor": "fake", "model": None, "effort": None, "max_turns": None},
        "instruction": {"path": "i.md", "kind": "file", "sha256": "a" * 64, "size": 1},
        "general_instructions": [],
        "inputs": [{"path": "d", "kind": "dir", "sha256": "b" * 64, "size": 2}],
        "dynamic_inputs": [],
        "outputs": [{"path": "o.md", "prior": PRIOR_ABSENT}],
        "repo_heads": None,
    }
    summary = summary_from_doc(doc, {"prompt": "123456789abc"})
    assert summary.digests == {"i.md": "a" * 64, "d": "b" * 64}
    assert summary.outputs == ["o.md"] and summary.repo_heads is None


# ------------------------------------------------------------------------------- misc / bounds
def test_a_directory_input_is_keyed_by_its_content(ws: Path) -> None:
    (ws / "tree").mkdir()
    (ws / "tree" / "a.txt").write_text("a")
    task = default_task(inputs=["tree"])
    before = key_for(ws, task=task)
    (ws / "tree" / "b.txt").write_text("b")
    assert key_for(ws, task=task).key != before.key


@needs_posix
def test_a_symlink_inside_an_input_directory_makes_the_task_uncacheable(ws: Path) -> None:
    (ws / "tree").mkdir()
    (ws / "tree" / "link").symlink_to(ws / "docs" / "notes.md")
    err = refusal(ws, task=default_task(inputs=["tree"]))
    assert err.reason == REASON_INPUT_NOT_REGULAR


def test_the_hash_budget_is_enforced_across_the_whole_key(ws: Path) -> None:
    assert refusal(ws, max_input_bytes=10).reason == REASON_INPUT_TOO_LARGE
    assert refusal(ws, max_input_files=2).reason == REASON_INPUT_TOO_LARGE
    assert key_for(ws, max_input_bytes=10**6, max_input_files=100)


def test_a_missing_instruction_or_input_is_input_missing(ws: Path) -> None:
    assert refusal(ws, task=default_task(instruction="specs/nope.md")).reason == (
        REASON_INPUT_MISSING
    )
    assert refusal(ws, task=default_task(inputs=["docs/nope.md"])).reason == REASON_INPUT_MISSING


def test_the_fake_executor_has_no_argv_or_fingerprint(ws: Path) -> None:
    deps_calls = make_deps()
    got = key_for(ws, deps_calls, agent=default_agent(executor="fake"))
    assert got.cli_version is None
    assert got.components["argv"] == got.components["executor_fingerprint"]  # both `null`
    assert deps_calls.cli_version_of.call_count == 0  # type: ignore[attr-defined]


def test_cache_key_exposes_the_output_maps(ws: Path) -> None:
    got = key_for(ws)
    assert got.output_abs == {"out/summary.md": str(ws.resolve() / "out" / "summary.md")}
    assert got.cli_version == "2.1.278 (Claude Code)"
