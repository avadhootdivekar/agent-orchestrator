"""Reference implementation of the `algo-hard` family (grader-owned, never agent-visible).

Same protocol and generated-case stream as ``algo_server.py``; the problems are stated as stories
(SPEC.md never names the technique) and need a non-obvious efficient method: slope trick, a suffix
automaton, combinatorics over obstacles, order statistics over sliding windows, and counting
longest increasing subsequences with a Fenwick tree. The generator cross-checks every solver here
against brute force (``gen/algo_puzzle.py``) on many small random cases before emitting an instance.

This file is copied verbatim into ``<grading_dir>/reference/`` by the generated provider.
"""

from __future__ import annotations

import bisect
import heapq
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

LCG_A, LCG_C, LCG_M = 1664525, 1013904223, 2**32
MAX_N = 300_000
MOD = 1_000_000_007

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


def gen_values(g: Case) -> list[int]:
    s, n, m = Stream(num(g, "seed", 0, LCG_M - 1)), num(g, "n", 1, MAX_N), num(g, "max", 0, 10**9)
    return [s.draw(0, m) for _ in range(n)]


# -- smoothing: min sum |a_i - b_i| over non-decreasing (or strictly increasing) b -------------


def smooth_gen(g: Case) -> Case:
    return {"heights": gen_values(g)}


def smooth(case: Case, variant: str) -> int:
    a = ints(case.get("heights"))
    if variant == "strict":  # b strictly increasing <=> b_i - i non-decreasing
        a = [x - i for i, x in enumerate(a)]
    heap: list[int] = []  # max-heap (negated) of the slope-trick breakpoints
    cost = 0
    for x in a:
        heapq.heappush(heap, -x)
        if -heap[0] > x:
            cost += -heap[0] - x
            heapq.heapreplace(heap, -x)
    return cost


# -- distinct substrings (optionally: occurring at least twice) ---------------------------------


def text_gen(g: Case) -> Case:
    s, n = Stream(num(g, "seed", 0, LCG_M - 1)), num(g, "n", 1, MAX_N)
    k = num(g, "letters", 1, 26)
    return {"text": "".join(chr(97 + s.draw(0, k - 1)) for _ in range(n))}


def substrings(case: Case, variant: str) -> int:
    text = case.get("text")
    if not isinstance(text, str) or not text:
        raise invalid("text must be a non-empty string")
    link, length, nxt, cnt = [-1], [0], [dict[str, int]()], [0]
    last = 0
    for ch in text:
        cur = len(length)
        length.append(length[last] + 1)
        link.append(-1)
        nxt.append({})
        cnt.append(1)
        p = last
        while p != -1 and ch not in nxt[p]:
            nxt[p][ch] = cur
            p = link[p]
        if p == -1:
            link[cur] = 0
        else:
            q = nxt[p][ch]
            if length[p] + 1 == length[q]:
                link[cur] = q
            else:
                clone = len(length)
                length.append(length[p] + 1)
                link.append(link[q])
                nxt.append(dict(nxt[q]))
                cnt.append(0)
                while p != -1 and nxt[p].get(ch) == q:
                    nxt[p][ch] = clone
                    p = link[p]
                link[q] = link[cur] = clone
        last = cur
    if variant == "all":
        return sum(length[v] - length[link[v]] for v in range(1, len(length)))
    for v in sorted(range(1, len(length)), key=lambda v: -length[v]):  # occurrence counts
        cnt[link[v]] += cnt[v]
    return sum(length[v] - length[link[v]] for v in range(1, len(length)) if cnt[v] >= 2)


# -- monotone lattice paths around blocked cells, mod 1e9+7 -------------------------------------


def grid_gen(g: Case) -> Case:
    s = Stream(num(g, "seed", 0, LCG_M - 1))
    rows, cols = num(g, "rows", 1, 10**5), num(g, "cols", 1, 10**5)
    k = num(g, "blocked", 0, 3000)
    blocked = [[s.draw(0, rows - 1), s.draw(0, cols - 1)] for _ in range(k)]
    return {"rows": rows, "cols": cols, "blocked": blocked}


_FACT: list[int] = [1]
_INV: list[int] = [1]


def _binom(n: int, r: int) -> int:
    if r < 0 or r > n:
        return 0
    while len(_FACT) <= n:
        _FACT.append(_FACT[-1] * len(_FACT) % MOD)
    if len(_INV) < len(_FACT):
        inv = [1] * len(_FACT)
        inv[-1] = pow(_FACT[-1], MOD - 2, MOD)
        for i in range(len(_FACT) - 1, 0, -1):
            inv[i - 1] = inv[i] * i % MOD
        _INV[:] = inv
    return _FACT[n] * _INV[r] % MOD * _INV[n - r] % MOD


def grid_paths(case: Case, variant: str) -> int:
    rows, cols = num(case, "rows", 1, 10**5), num(case, "cols", 1, 10**5)
    cells = {(r, c) for r, c in ints(case.get("blocked"), 2)}
    if any(not (0 <= r < rows and 0 <= c < cols) for r, c in cells):
        raise invalid("blocked cells must be inside the grid")
    if (0, 0) in cells or (rows - 1, cols - 1) in cells:
        return 0
    pts = sorted(cells) + [(rows - 1, cols - 1)]
    ways: list[int] = []
    for i, (r, c) in enumerate(pts):
        w = _binom(r + c, r)
        for j in range(i):
            pr, pc = pts[j]
            if pr <= r and pc <= c:
                w -= ways[j] * _binom(r - pr + c - pc, r - pr)
        ways.append(w % MOD)
    return ways[-1]


# -- sum of the (lower or upper) medians of every window of length w ----------------------------


def window_gen(g: Case) -> Case:
    return {"values": gen_values(g), "w": num(g, "w", 1, MAX_N)}


def window_medians(case: Case, variant: str) -> int:
    a = ints(case.get("values"))
    w = num(case, "w", 1, MAX_N)
    if w > len(a):
        return 0
    keys = sorted(set(a))
    rank = {v: i for i, v in enumerate(keys)}
    size = len(keys)
    tree = [0] * (size + 1)
    log = 1 << size.bit_length()

    def add(i: int, d: int) -> None:
        i += 1
        while i <= size:
            tree[i] += d
            i += i & -i

    def kth(k: int) -> int:  # 1-based k-th smallest present value's rank
        pos, step = 0, log
        while step:
            nxt = pos + step
            if nxt <= size and tree[nxt] < k:
                pos = nxt
                k -= tree[nxt]
            step >>= 1
        return pos

    k = (w + 1) // 2 if variant == "lower" else w // 2 + 1
    total = 0
    for i, x in enumerate(a):
        add(rank[x], 1)
        if i >= w:
            add(rank[a[i - w]], -1)
        if i >= w - 1:
            total += keys[kth(k)]
    return total


# -- number of longest increasing subsequences, mod 1e9+7 ---------------------------------------


def lis_count(case: Case, variant: str) -> int:
    a = ints(case.get("values"))
    keys = sorted(set(a))
    size = len(keys)
    best = [0] * (size + 1)  # Fenwick over value ranks of (length, count), max-combined
    ways = [0] * (size + 1)

    def query(i: int) -> tuple[int, int]:  # best (length, count) over ranks < i
        bl, bc = 0, 1
        while i > 0:
            if best[i] > bl:
                bl, bc = best[i], ways[i]
            elif best[i] == bl and bl:
                bc = (bc + ways[i]) % MOD
            i -= i & -i
        return bl, bc

    def update(i: int, length: int, count: int) -> None:
        i += 1
        while i <= size:
            if length > best[i]:
                best[i], ways[i] = length, count
            elif length == best[i]:
                ways[i] = (ways[i] + count) % MOD
            i += i & -i

    top, total = 0, 0
    for x in a:
        r = bisect.bisect_left(keys, x) if variant == "strict" else bisect.bisect_right(keys, x)
        length, count = query(r)
        length += 1
        update(bisect.bisect_left(keys, x), length, count)
        if length > top:
            top, total = length, count
        elif length == top:
            total = (total + count) % MOD
    return total


PROBLEMS: dict[str, tuple[Callable[[Case], Case], Callable[[Case, str], int]]] = {
    "smoothing": (smooth_gen, smooth),
    "substrings": (text_gen, substrings),
    "grid_paths": (grid_gen, grid_paths),
    "window_medians": (window_gen, window_medians),
    "lis_count": (lambda g: {"values": gen_values(g)}, lis_count),
}


def expand(problem: str, case: Case) -> Case:
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


def main() -> None:
    with open(instance_path(), encoding="utf-8") as fh:
        inst = json.load(fh)
    engine = Engine(inst["limits"])
    out = sys.stdout
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
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
