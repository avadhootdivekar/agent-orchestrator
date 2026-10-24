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


def test_algo_hard_hand_computed_and_brute_force() -> None:
    from agent_orchestrator.bench.gen.algo_hard import HARD

    def hard(problem: str, case: dict, variant: str) -> object:
        variants = {p: v[0] for p, v in HARD.variants.items()} | {problem: variant}
        resp = new_engine({"variants": variants}, HARD).handle(
            {"op": "solve", "problem": problem, "case": case}
        )
        return resp.get("answer")

    assert hard("smoothing", {"heights": [3, 1, 2]}, "non_decreasing") == 2  # -> 1,1,2 or 2,2,2
    assert hard("smoothing", {"heights": [1, 1, 1]}, "strict") == 2  # -> 0,1,2
    assert hard("substrings", {"text": "aaa"}, "all") == 3
    assert hard("substrings", {"text": "aaa"}, "repeated") == 2  # "a", "aa"
    assert hard("grid_paths", {"rows": 2, "cols": 3, "blocked": [[0, 1]]}, "count") == 1
    assert hard("window_medians", {"values": [1, 5, 2, 8], "w": 2}, "lower") == 1 + 2 + 2
    assert hard("window_medians", {"values": [1, 5, 2, 8], "w": 2}, "upper") == 5 + 5 + 8
    assert hard("lis_count", {"values": [1, 3, 2, 4]}, "strict") == 2
    assert hard("lis_count", {"values": [2, 2]}, "non_decreasing") == 1
    cross_check(random.Random(4), HARD)
    fam = FAMILY_REGISTRY["algo-hard"]()
    inst = fam.generate(9, Difficulty(entities=1, traps=1, concurrency=0, scale=1), "python")
    assert validate_probes(inst.probes) == [] and len(inst.limits["problems"]) == 3
