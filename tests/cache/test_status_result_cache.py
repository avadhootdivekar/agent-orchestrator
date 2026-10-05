"""T-eyn5UG AC-7, AC-8 (U-RS1, U-RS2) and the status half of AC-6: `RunStateStore.write_status`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.models import RunState
from agent_orchestrator.runstate import RunStateStore
from tests.cache._report_states import (
    RESULT_CACHE_RUN_SCHEMA,
    RESULT_CACHE_TASK_SCHEMA,
    hit_rec,
    mixed_state,
    run_state,
    settled,
)

# The pre-epic key sets, as literals: an addition here is a status.json contract change.
PRE_EPIC_TOP_LEVEL = [
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
]
PRE_EPIC_PER_TASK = [
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
]


def _status(workspace: Path, state: RunState) -> dict[str, Any]:
    workspace.mkdir(parents=True, exist_ok=True)
    store = RunStateStore(str(workspace), LocalFsArtifactStore(str(workspace)))
    store.save(state)  # save() derives status.json next to state.json (ADR-002)
    path = workspace / ".orchestrator" / "runs" / state.run_id / "status.json"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


class TestEmptyMapIsByteIdentical:
    """U-RS1 (NFR-1)."""

    def test_exact_pre_epic_top_level_and_per_task_key_lists(self, tmp_path: Path) -> None:
        data = _status(tmp_path, run_state({"a": settled(), "b": settled(status="pending")}))
        assert list(data) == PRE_EPIC_TOP_LEVEL
        assert [list(t) for t in data["tasks"]] == [PRE_EPIC_PER_TASK, PRE_EPIC_PER_TASK]

    def test_only_stale_records_also_add_nothing(self, tmp_path: Path) -> None:
        stale = hit_rec(ended_at="1999-01-01T00:00:00+00:00")
        data = _status(tmp_path, run_state({"a": settled()}, {"a": stale}))
        assert list(data) == PRE_EPIC_TOP_LEVEL
        assert list(data["tasks"][0]) == PRE_EPIC_PER_TASK


class TestWithCurrentHit:
    """U-RS2 and the status half of U-RP8."""

    def test_adds_exactly_the_two_result_cache_keys(self, tmp_path: Path) -> None:
        state = run_state({"a": settled(), "b": settled()}, {"a": hit_rec()})
        data = _status(tmp_path, state)
        assert list(data) == [*PRE_EPIC_TOP_LEVEL, "result_cache"]
        a, b = data["tasks"]
        assert list(a) == [*PRE_EPIC_PER_TASK, "result_cache"]
        assert list(b) == PRE_EPIC_PER_TASK
        assert a["result_cache"]["hit"] is True and a["result_cache"]["saved_tokens"] == 15400
        jsonschema.validate(a["result_cache"], RESULT_CACHE_TASK_SCHEMA)
        jsonschema.validate(data["result_cache"], RESULT_CACHE_RUN_SCHEMA)
        assert data["result_cache"]["hits"] == 1

    def test_nothing_else_changes(self, tmp_path: Path) -> None:
        tasks = {"a": settled(), "b": settled()}
        base = _status(tmp_path / "plain", run_state(dict(tasks)))
        cached = _status(tmp_path / "cached", run_state(dict(tasks), {"a": hit_rec()}))
        for key in PRE_EPIC_TOP_LEVEL:
            if key not in ("tasks", "updated_at"):  # updated_at is the store wall clock
                assert cached[key] == base[key], key
        for old, new in zip(base["tasks"], cached["tasks"], strict=True):
            assert {k: v for k, v in new.items() if k != "result_cache"} == old

    def test_stale_filtering_matches_the_helpers(self, tmp_path: Path) -> None:
        data = _status(tmp_path, mixed_state())
        with_rc = [t["id"] for t in data["tasks"] if "result_cache" in t]
        assert with_rc == ["hit", "miss"]
        assert data["result_cache"]["hits"] == 1 and data["result_cache"]["misses"] == 1
