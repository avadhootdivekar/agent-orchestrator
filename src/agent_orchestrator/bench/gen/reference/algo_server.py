"""Reference implementation of the `algo-puzzle` family (grader-owned, never agent-visible).

Several algorithmic problems per instance (``limits.problems``, each with a seeded *variant* in
``limits.variants``), answered over JSON lines: ``{"op": "solve", "problem": <name>, "case": ...}``
-> ``{"ok": true, "answer": <int>}``. A case is either explicit or ``{"gen": {...}}``: inputs drawn
from a fixed linear congruential stream, so huge inputs fit in one short request. Every solver here
is the efficient one (large hidden cases need it); the generator cross-checks each against a
brute-force solver (``gen/algo_puzzle.py``) on many small random cases before emitting an instance.

This file is copied verbatim into ``<grading_dir>/reference/`` by the generated provider.
"""

from __future__ import annotations

import bisect
import heapq
import json
import os
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

LCG_A, LCG_C, LCG_M = 1664525, 1013904223, 2**32
MAX_N = 300_000

Case = dict[str, Any]


class OpError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def invalid(message: str) -> OpError:
    return OpError("invalid_request", message)


def num(case: Case, key: str, lo: int, hi: int) -> int:
    v = case.get(key)
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise invalid(f"{key} must be an integer from {lo} to {hi}")
    return v


def ints(v: Any, width: int | None = None) -> list[Any]:
    """A list of ints (width None) or of int rows of `width`."""
    if not isinstance(v, list):
        raise invalid("expected an array")
    for x in v:
        row = x if width is not None else [x]
        if not isinstance(row, list) or (width is not None and len(row) != width):
            raise invalid(f"expected rows of {width} integers")
        if any(not isinstance(y, int) or isinstance(y, bool) for y in row):
            raise invalid("expected integers")
    return v


class Stream:
    """x_{k+1} = (A * x_k + C) mod 2^32 from x_0 = seed; draw(lo, hi) = lo + x mod span."""

    def __init__(self, seed: int) -> None:
        self.x = seed

    def draw(self, lo: int, hi: int) -> int:
        self.x = (LCG_A * self.x + LCG_C) % LCG_M
        return lo + self.x % (hi - lo + 1)


# -- problems: each is (expand gen -> explicit case, solve explicit case) ------------------------


def gen_values(g: Case, signed: bool) -> list[int]:
    s, n, m = Stream(num(g, "seed", 0, LCG_M - 1)), num(g, "n", 1, MAX_N), num(g, "max", 0, 10**9)
    return [s.draw(-m if signed else 0, m) for _ in range(n)]


def lis_gen(g: Case) -> Case:
    return {"values": gen_values(g, signed=False)}


def lis(case: Case, variant: str) -> int:
    pick = bisect.bisect_left if variant == "strict" else bisect.bisect_right
    tails: list[int] = []
    for x in ints(case.get("values")):
        i = pick(tails, x)
        if i == len(tails):
            tails.append(x)
        else:
            tails[i] = x
    return len(tails)


def intervals_gen(g: Case) -> Case:
    s, n = Stream(num(g, "seed", 0, LCG_M - 1)), num(g, "n", 1, MAX_N)
    span, max_len = num(g, "span", 0, 10**9), num(g, "max_len", 1, 10**9)
    max_value = num(g, "max_value", 1, 10**6)
    jobs = []
    for _ in range(n):
        start = s.draw(0, span)
        length = s.draw(1, max_len)
        jobs.append([start, start + length, s.draw(1, max_value)])
    return {"jobs": jobs}


def intervals(case: Case, variant: str) -> int:
    jobs = ints(case.get("jobs"), 3)
    if any(e <= st or v < 0 for st, e, v in jobs):
        raise invalid("each job needs start < end and value >= 0")
    jobs = sorted(jobs, key=lambda j: j[1])
    ends = [j[1] for j in jobs]
    pick = bisect.bisect_right if variant == "touching_ok" else bisect.bisect_left
    best = [0] * (len(jobs) + 1)
    for i, (start, _end, value) in enumerate(jobs):
        k = pick(ends, start, 0, i)  # jobs compatible with this one all end before index k
        best[i + 1] = max(best[i], best[k] + value)
    return best[-1]


def range_gen(g: Case) -> Case:
    lo, hi = num(g, "lo", -(10**15), 10**15), num(g, "hi", -(10**15), 10**15)
    return {"values": gen_values(g, signed=True), "lo": lo, "hi": hi}


def _bounds(case: Case) -> tuple[list[int], int, int]:
    values = ints(case.get("values"))
    return values, num(case, "lo", -(10**15), 10**15), num(case, "hi", -(10**15), 10**15)


def pair_count(case: Case, variant: str) -> int:
    values, lo, hi = _bounds(case)
    a = sorted(values)
    if lo > hi:
        return 0
    total = 0
    for j, x in enumerate(a):  # pairs (i, j) with i < j in sorted order
        if variant == "sum":
            total += bisect.bisect_right(a, hi - x, 0, j) - bisect.bisect_left(a, lo - x, 0, j)
        else:  # abs_difference: x - a[i] in [lo, hi] (a[i] <= x)
            lo_d, hi_d = max(lo, 0), hi
            if lo_d <= hi_d:
                total += bisect.bisect_right(a, x - lo_d, 0, j) - bisect.bisect_left(
                    a, x - hi_d, 0, j
                )  # noqa: E501
    return total


def subarray_sums(case: Case, variant: str) -> int:
    values, lo, hi = _bounds(case)
    if lo > hi:
        return 0
    prefix = [0]
    for x in values:
        prefix.append(prefix[-1] + x)
    keys = sorted(set(prefix))
    tree = [0] * (len(keys) + 1)

    def add(i: int) -> None:
        i += 1
        while i <= len(keys):
            tree[i] += 1
            i += i & -i

    def count_le(i: int) -> int:  # how many inserted keys have index < i
        c = 0
        while i > 0:
            c += tree[i]
            i -= i & -i
        return c

    total = 0
    for p in prefix:  # earlier prefix q with lo <= p - q <= hi, i.e. p - hi <= q <= p - lo
        total += count_le(bisect.bisect_right(keys, p - lo)) - count_le(
            bisect.bisect_left(keys, p - hi)
        )
        add(bisect.bisect_left(keys, p))
    return total


def path_gen(g: Case) -> Case:
    s, n = Stream(num(g, "seed", 0, LCG_M - 1)), num(g, "n", 1, MAX_N)
    m, max_w = num(g, "m", 0, MAX_N), num(g, "max_w", 1, 10**6)
    edges = [[s.draw(0, n - 1), s.draw(0, n - 1), s.draw(1, max_w)] for _ in range(m)]
    return {"n": n, "k": num(g, "k", 0, 5), "edges": edges}


def free_edges_path(case: Case, variant: str) -> int:
    n, k = num(case, "n", 1, MAX_N), num(case, "k", 0, 5)
    adj: list[list[tuple[int, int]]] = [[] for _ in range(n)]
    for u, v, w in ints(case.get("edges"), 3):
        if not (0 <= u < n and 0 <= v < n and w >= 0):
            raise invalid("edge endpoints must be nodes and weights >= 0")
        adj[u].append((v, w))
        if variant == "undirected":
            adj[v].append((u, w))
    inf = 1 << 62
    dist = [[inf] * (k + 1) for _ in range(n)]
    dist[0][0] = 0
    heap = [(0, 0, 0)]
    while heap:
        d, u, used = heapq.heappop(heap)
        if d > dist[u][used]:
            continue
        if u == n - 1:
            return d
        for v, w in adj[u]:
            if d + w < dist[v][used]:
                dist[v][used] = d + w
                heapq.heappush(heap, (d + w, v, used))
            if used < k and d < dist[v][used + 1]:
                dist[v][used + 1] = d
                heapq.heappush(heap, (d, v, used + 1))
    return -1


def connect_gen(g: Case) -> Case:
    s, n = Stream(num(g, "seed", 0, LCG_M - 1)), num(g, "n", 1, MAX_N)
    m, q, max_t = num(g, "m", 0, MAX_N), num(g, "q", 0, MAX_N), num(g, "max_t", 0, 10**9)
    links = [[s.draw(0, n - 1), s.draw(0, n - 1), s.draw(0, max_t)] for _ in range(m)]
    queries = [[s.draw(0, n - 1), s.draw(0, n - 1), s.draw(0, max_t)] for _ in range(q)]
    return {"n": n, "links": links, "queries": queries}


def connected_at(case: Case, variant: str) -> int:
    n = num(case, "n", 1, MAX_N)
    links = sorted(ints(case.get("links"), 3), key=lambda e: e[2])
    queries = ints(case.get("queries"), 3)
    if any(not (0 <= r[0] < n and 0 <= r[1] < n) for r in [*links, *queries]):
        raise invalid("endpoints must be nodes")
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    total, li = 0, 0
    for u, v, t in sorted(queries, key=lambda r: r[2]):
        while li < len(links) and (
            links[li][2] <= t if variant == "inclusive" else links[li][2] < t
        ):
            a, b = find(links[li][0]), find(links[li][1])
            if a != b:
                parent[a] = b
            li += 1
        total += find(u) == find(v)
    return total


PROBLEMS: dict[str, tuple[Callable[[Case], Case], Callable[[Case, str], int]]] = {
    "lis": (lis_gen, lis),
    "intervals": (intervals_gen, intervals),
    "pair_count": (range_gen, pair_count),
    "subarray_sums": (range_gen, subarray_sums),
    "free_edges_path": (path_gen, free_edges_path),
    "connected_at": (connect_gen, connected_at),
}


def expand(problem: str, case: Case) -> Case:
    """The explicit case for `case` (expanding a `gen` case with the problem's generator)."""
    if "gen" not in case:
        return case
    if not isinstance(case["gen"], dict):
        raise invalid("gen must be an object")
    return PROBLEMS[problem][0](case["gen"])


class Engine:
    def __init__(self, limits: dict[str, Any]) -> None:
        self.variants: dict[str, str] = limits["variants"]

    def solve(self, problem: str, case: Case) -> int:
        return PROBLEMS[problem][1](expand(problem, case), self.variants[problem])

    def handle(self, req: Any) -> dict[str, Any]:
        try:
            if not isinstance(req, dict) or not isinstance(req.get("op"), str):
                raise invalid("request must be an object with a string op")
            if req["op"] == "ping":
                return {"ok": True}
            if req["op"] != "solve":
                raise OpError("unknown_op", f"unknown op {req['op']!r}")
            if req.get("problem") not in self.variants:
                raise OpError("unknown_problem", "problem is not one of this engine's problems")
            case = req.get("case")
            if not isinstance(case, dict):
                raise invalid("case must be an object")
            return {"ok": True, "answer": self.solve(req["problem"], case)}
        except OpError as err:
            return {"ok": False, "error": err.code, "message": err.message}


def instance_path() -> str:
    return os.environ.get("INSTANCE_JSON") or str(Path(__file__).with_name("instance.json"))


def lines(stream: Any) -> Iterator[str]:
    for raw in stream:
        line = raw.strip()
        if line:
            yield line


def main() -> None:
    with open(instance_path(), encoding="utf-8") as fh:
        inst = json.load(fh)
    engine = Engine(inst["limits"])
    out = sys.stdout
    for line in lines(sys.stdin):
        try:
            req = json.loads(line)
        except ValueError:
            resp: dict[str, Any] = {"ok": False, "error": "invalid_request", "message": "bad JSON"}
        else:
            resp = engine.handle(req)
        out.write(json.dumps(resp) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
