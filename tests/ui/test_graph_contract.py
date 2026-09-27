"""AC-7 contract test (T-AsQ77e, HLD §14.2, frozen at design time).

`ui/src/test/fixtures/run-graph.json` is T-adVpTj's checked-in, design-time fixture:
the frontend's tests build against it starting day 1, before this endpoint existed.
This test is the OTHER half of that contract -- it asserts the fixture's shape equals
the backend `RunGraph` dataclass family's field set and the documented closed enums,
in BOTH directions: a fixture key the dataclasses don't have fails here, and so does a
dataclass field the fixture doesn't exercise. It does not redefine §14.2 -- only
verifies conformance against it (TASK.md's explicit framing).
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_orchestrator.dag import EDGE_KIND_EXPLICIT, EDGE_KIND_INFERRED, EDGE_KIND_LOOP
from agent_orchestrator.ui.graph import (
    GRAPH_SCHEMA_VERSION,
    GRAPH_SOURCE_SNAPSHOT,
    GRAPH_SOURCE_UNAVAILABLE,
    SPAWN_DATA_NONE,
    SPAWN_DATA_NOT_RECORDED,
    SPAWN_DATA_RECORDED,
    GraphDependencyEdge,
    GraphLoop,
    GraphNode,
    GraphRouter,
    GraphSpawnEdge,
    RunGraph,
)

# tests/ui/test_graph_contract.py -> tests/ui -> tests -> <repo root> -> ui/src/test/fixtures/
_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "ui" / "src" / "test" / "fixtures" / "run-graph.json"
)

_CLOSED_SOURCE = frozenset({GRAPH_SOURCE_SNAPSHOT, GRAPH_SOURCE_UNAVAILABLE})
_CLOSED_SPAWN_DATA = frozenset({SPAWN_DATA_RECORDED, SPAWN_DATA_NOT_RECORDED, SPAWN_DATA_NONE})
_CLOSED_EDGE_KIND = frozenset({EDGE_KIND_EXPLICIT, EDGE_KIND_LOOP, EDGE_KIND_INFERRED})


def _load_fixture() -> dict:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def test_fixture_file_exists_at_the_expected_path() -> None:
    assert _FIXTURE_PATH.is_file(), (
        f"T-adVpTj's frontend fixture is expected at {_FIXTURE_PATH}; if it moved, this "
        "contract test's path needs updating too (an architect note per HLD §14.2's "
        "'Contract freeze' clause, not a silent path fix)."
    )


class TestTopLevelContract:
    def test_fixture_top_level_keys_equal_the_rungraph_dataclass_fields(self) -> None:
        fixture = _load_fixture()
        assert set(fixture.keys()) == set(RunGraph.__dataclass_fields__.keys())

    def test_fixture_schema_version_matches_the_backend_constant(self) -> None:
        assert _load_fixture()["schema_version"] == GRAPH_SCHEMA_VERSION

    def test_fixture_source_is_in_the_closed_enum(self) -> None:
        assert _load_fixture()["source"] in _CLOSED_SOURCE

    def test_fixture_spawn_data_is_in_the_closed_enum(self) -> None:
        assert _load_fixture()["spawn_data"] in _CLOSED_SPAWN_DATA


class TestNodeContract:
    def test_every_node_key_set_equals_the_graphnode_dataclass_fields(self) -> None:
        fixture = _load_fixture()
        expected = set(GraphNode.__dataclass_fields__.keys())
        assert fixture["nodes"], "fixture must exercise at least one node"
        for node in fixture["nodes"]:
            assert set(node.keys()) == expected

    def test_node_origin_is_a_non_empty_string_open_set(self) -> None:
        # `origin` is documented as an OPEN set (known values: static/injected/loop) --
        # the fixture deliberately includes an unknown value ("manual") to prove
        # clients must render it generically rather than reject it. The contract here
        # only requires it be a non-empty string, never a closed-enum membership check.
        fixture = _load_fixture()
        origins = {node["origin"] for node in fixture["nodes"]}
        assert all(isinstance(o, str) and o for o in origins)
        assert "manual" in origins, "fixture should keep exercising an unknown origin value"


class TestEdgeContract:
    def test_every_dependency_edge_key_set_equals_the_dataclass_fields(self) -> None:
        fixture = _load_fixture()
        expected = set(GraphDependencyEdge.__dataclass_fields__.keys())
        assert fixture["dependency_edges"]
        for edge in fixture["dependency_edges"]:
            assert set(edge.keys()) == expected

    def test_every_dependency_edge_kind_is_in_the_closed_enum(self) -> None:
        fixture = _load_fixture()
        for edge in fixture["dependency_edges"]:
            assert edge["kind"] in _CLOSED_EDGE_KIND

    def test_every_spawn_edge_key_set_equals_the_dataclass_fields(self) -> None:
        fixture = _load_fixture()
        expected = set(GraphSpawnEdge.__dataclass_fields__.keys())
        assert fixture["spawn_edges"]
        for edge in fixture["spawn_edges"]:
            assert set(edge.keys()) == expected


class TestLoopAndRouterContract:
    def test_every_loop_key_set_equals_the_dataclass_fields(self) -> None:
        fixture = _load_fixture()
        expected = set(GraphLoop.__dataclass_fields__.keys())
        assert fixture["loops"]
        for loop in fixture["loops"]:
            assert set(loop.keys()) == expected

    def test_every_router_key_set_equals_the_dataclass_fields(self) -> None:
        fixture = _load_fixture()
        expected = set(GraphRouter.__dataclass_fields__.keys())
        assert fixture["routers"]
        for router in fixture["routers"]:
            assert set(router.keys()) == expected


class TestBuilderNeverProducesAnUndocumentedShape:
    """The other direction of "fails in both directions" (TASK.md AC-7): a freshly
    built `RunGraph` (from a from-scratch `RunState`, not a re-parse of the fixture's
    own JSON) must serialize to exactly the same top-level key set the fixture has --
    so a future field added to the dataclass but never mirrored into the fixture (or
    vice versa) is caught regardless of which side changed first.
    """

    def test_a_freshly_built_rungraph_has_the_fixtures_top_level_keys(self) -> None:
        from dataclasses import asdict

        from agent_orchestrator.models import RunState
        from agent_orchestrator.ui.graph import build_run_graph

        state = RunState(
            run_id="contract-run",
            workflow_id="contract",
            repo_set="contract-repos",
            started_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        built = asdict(build_run_graph(state, None))
        assert set(built.keys()) == set(_load_fixture().keys())
