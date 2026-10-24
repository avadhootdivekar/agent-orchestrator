"""T-Pf4Mix `algo-puzzle` family: reference solvers against hand-computed answers and brute force,
the LCG stream, and generator validity/determinism."""

from __future__ import annotations

import random

from agent_orchestrator.bench.gen import FAMILY_REGISTRY
from agent_orchestrator.bench.gen.algo_puzzle import BRUTE, VARIANTS, cross_check, new_engine
from agent_orchestrator.bench.gen.base import Difficulty
from agent_orchestrator.bench.gen.validate import validate_probes

ALL = {p: v[0] for p, v in VARIANTS.items()}


def solve(problem: str, case: dict, variant: str | None = None) -> object:
    engine = new_engine({"variants": {**ALL, **({problem: variant} if variant else {})}})
    return engine.handle({"op": "solve", "problem": problem, "case": case}).get("answer")


def test_hand_computed_variants() -> None:
    assert solve("lis", {"values": [1, 2, 2, 3]}, "strict") == 3
    assert solve("lis", {"values": [1, 2, 2, 3]}, "non_decreasing") == 4
    jobs = {"jobs": [[0, 2, 5], [2, 4, 5], [1, 3, 7]]}
    assert solve("intervals", jobs, "touching_ok") == 10
    assert solve("intervals", jobs, "touching_conflicts") == 7
    vals = {"values": [1, -1, 3], "lo": 0, "hi": 2}
    assert solve("pair_count", vals, "sum") == 2  # 1-1=0, -1+3=2
    assert solve("pair_count", vals, "abs_difference") == 2  # |1-(-1)|=2, |1-3|=2
    assert solve("subarray_sums", vals) == 3  # [1], [1, -1], [-1, 3]
    edges = {"n": 3, "k": 0, "edges": [[1, 0, 1], [1, 2, 1]]}
    assert solve("free_edges_path", edges, "directed") == -1
    assert solve("free_edges_path", edges, "undirected") == 2
    assert solve("free_edges_path", {**edges, "k": 1}, "undirected") == 1
    conn = {"n": 2, "links": [[0, 1, 5]], "queries": [[0, 1, 5], [1, 1, 0]]}
    assert solve("connected_at", conn, "inclusive") == 2
    assert solve("connected_at", conn, "exclusive") == 1


def test_lcg_stream_and_gen_case() -> None:
    # x1 = 1013904223 (seed 0); x2 = 1664525 * x1 + 1013904223 mod 2^32 = 1196435762
    assert solve("lis", {"gen": {"seed": 0, "n": 2, "max": 10**9}}, "strict") == 2
    from agent_orchestrator.bench.gen.algo_puzzle import _engine_module

    s = _engine_module().Stream(0)
    assert [s.draw(0, 2**32 - 1), s.draw(0, 2**32 - 1)] == [1013904223, 1196435762]


def test_errors() -> None:
    e = new_engine({"variants": {"lis": "strict"}})
    resp = e.handle({"op": "solve", "problem": "intervals", "case": {}})
    assert resp["error"] == "unknown_problem"
    assert e.handle({"op": "nope"})["error"] == "unknown_op"
    assert e.handle({"op": "solve", "problem": "lis", "case": []})["error"] == "invalid_request"


def test_reference_matches_brute_force() -> None:
    cross_check(random.Random(3))
    assert set(BRUTE) == set(VARIANTS)


def test_generator_valid_deterministic() -> None:
    fam = FAMILY_REGISTRY["algo-puzzle"]()
    d = Difficulty(entities=2, traps=1, concurrency=0, scale=1)
    a = fam.generate(5, d, "python")
    assert a == fam.generate(5, d, "python")
    assert validate_probes(a.probes) == [] and len(a.limits["problems"]) == 4
    assert a.traps and all(p in a.spec_md for p in a.limits["problems"])
