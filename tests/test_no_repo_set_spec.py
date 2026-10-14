"""U1 of the no-repo-set (workspace-only) mode (ADR-0022, no-repo-set-mode-hld.md §8).

Covers the model + spec layer only: `repo_set` optional on `WorkflowSpec`/`RunState`,
`cross_validate` with `reposets={}`, the V13 warning, the JSON schema, and the regression that a
workflow that *does* name a repo_set still fails with the unchanged `Unknown repo_set` error.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_orchestrator.errors import SpecValidationError
from agent_orchestrator.models import AgentSpec, RepoRef, RepoSet, RunState, WorkflowSpec
from agent_orchestrator.spec import cross_validate, load_workflow

_NO_LLM_LADDER = ["auto", "mechanical", "rerun"]
_AGENTS = {"ag": AgentSpec(executor="fake")}
_TASK = {"id": "t1", "agent": "ag", "instruction": "i.md"}


def _wf(**extra: object) -> WorkflowSpec:
    data: dict = {"version": "1", "id": "wf", "tasks": [_TASK]}
    data.update(extra)
    return WorkflowSpec(**data)


@pytest.mark.parametrize("kwargs", [{}, {"repo_set": None}, {"repo_set": ""}, {"repo_set": "  "}])
def test_workflow_spec_accepts_missing_null_or_blank_repo_set(kwargs: dict) -> None:
    assert _wf(**kwargs).repo_set is None


def test_workflow_spec_keeps_non_empty_repo_set() -> None:
    assert _wf(repo_set="rs").repo_set == "rs"


def test_cross_validate_no_repo_set_with_empty_reposets_passes() -> None:
    assert cross_validate(_wf(), {}, _AGENTS) == []


def test_cross_validate_no_repo_set_ignores_a_supplied_reposets_file() -> None:
    reposets = {
        "rs": RepoSet(workspace_root=".", repos=[RepoRef(id="c", path=".", role="primary")])
    }
    assert cross_validate(_wf(), reposets, _AGENTS) == []


def test_repo_set_with_no_reposets_still_raises_unknown_repo_set() -> None:
    # Regression: existing behaviour with a repo_set is unchanged (message verbatim).
    with pytest.raises(SpecValidationError, match=r"Unknown repo_set: 'rs'") as exc:
        cross_validate(_wf(repo_set="rs"), {}, _AGENTS)
    assert exc.value.path == "repo_set"


def test_cross_validate_still_checks_agents_without_repo_set() -> None:
    wf = _wf(tasks=[{**_TASK, "agent": "missing"}])
    with pytest.raises(SpecValidationError, match="unknown agent"):
        cross_validate(wf, {}, _AGENTS)


def test_v13_warns_for_default_worktree_isolation_without_repo_set() -> None:
    wf = _wf(defaults={"isolation": "worktree"}, integration={"ladder": _NO_LLM_LADDER})
    warnings = cross_validate(wf, {}, _AGENTS)
    assert [w for w in warnings if w.startswith("V13")]


def test_v13_warns_for_per_task_isolation_without_repo_set() -> None:
    wf = _wf(tasks=[{**_TASK, "isolation": "worktree"}], integration={"ladder": _NO_LLM_LADDER})
    assert any(w.startswith("V13") for w in cross_validate(wf, {}, _AGENTS))


def test_v13_not_emitted_without_isolation() -> None:
    assert not any("V13" in w for w in cross_validate(_wf(), {}, _AGENTS))


def test_v13_not_emitted_when_repo_set_present(tmp_path: Path) -> None:
    reposets = {
        "rs": RepoSet(
            workspace_root=str(tmp_path), repos=[RepoRef(id="c", path=".", role="primary")]
        )
    }
    wf = _wf(
        repo_set="rs",
        defaults={"isolation": "worktree"},
        integration={"ladder": _NO_LLM_LADDER},
    )
    assert not any("V13" in w for w in cross_validate(wf, reposets, _AGENTS))


def test_v3_still_fatal_without_repo_set() -> None:
    wf = _wf(
        defaults={"isolation": "worktree"},
        integration={"ladder": ["auto", "llm"], "resolver_agent": "nope"},
    )
    with pytest.raises(SpecValidationError, match="resolver_agent"):
        cross_validate(wf, {}, _AGENTS)


def test_load_workflow_schema_accepts_omitted_null_and_blank_repo_set(tmp_path: Path) -> None:
    base = {"version": "1.0", "id": "wf", "tasks": [_TASK]}
    for i, extra in enumerate([{}, {"repo_set": None}, {"repo_set": ""}]):
        p = tmp_path / f"wf{i}.json"
        p.write_text(json.dumps({**base, **extra}))
        assert load_workflow(p).repo_set is None


def test_load_workflow_schema_still_rejects_non_string_repo_set(tmp_path: Path) -> None:
    p = tmp_path / "wf.json"
    p.write_text(json.dumps({"version": "1.0", "id": "wf", "repo_set": 5, "tasks": [_TASK]}))
    with pytest.raises(SpecValidationError):
        load_workflow(p)


def test_run_state_repo_set_optional_and_legacy_state_loads() -> None:
    base = {
        "run_id": "r",
        "workflow_id": "wf",
        "started_at": "2026-10-06T00:00:00Z",
        "updated_at": "2026-10-06T00:00:00Z",
    }
    assert RunState(**base).repo_set is None
    assert RunState.model_validate_json(json.dumps({**base, "repo_set": "rs"})).repo_set == "rs"
