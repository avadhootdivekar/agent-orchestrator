"""Reference implementation of the `cvrp-ladder` family (grader-owned, never agent-visible).

Capacitated vehicle routing (CVRP): serve every customer from one depot with vehicles of capacity
``Q``; minimise the total Euclidean route length. NP-hard, and no known optimum at the sizes used
(100-800 customers), so the score is how far up a *ladder* of deterministic reference solvers an
answer reaches (design `docs-md/benchmark-cvrp-ladder-design.md`):

- rung 0 `trivial`: one route per customer;
- rung 1 `nearest`: nearest-neighbour routes (fill a vehicle with the closest feasible customer);
- rung 2 `savings`: Clarke-Wright parallel savings;
- rung 3 `local_search`: savings, then relocate / swap / 2-opt / 2-opt* to a local optimum;
- rung 4 `strong`: ruin-and-recreate simulated annealing (SISR, Christiaens & Vanden Berghe 2020)
  started from rung 3, ``STRONG_ITERS_PER_CUSTOMER`` iterations per customer.

Every solver is deterministic (fixed-seed RNG, iteration budgets, never wall-clock time). The
generator stores the rung values with each hidden case; the reference server answers with the
rung-4 solution. ``evaluate`` is the grader's feasibility checker and objective.

Case: ``{"depot": [x, y], "capacity": Q, "customers": [[x, y, demand], ...]}``. Answer: a list of
routes, each a list of customer indices ``0..n-1`` in visiting order. Internally customers are
numbered ``1..n`` and the depot is node 0.

This file is copied verbatim into ``<grading_dir>/reference/`` by the generated provider.
"""

from __future__ import annotations

import json
import math
import random
import sys
from typing import Any

Case = dict[str, Any]
Routes = list[list[int]]

RUNGS = ("trivial", "nearest", "savings", "local_search", "strong")
TOP_RUNG = len(RUNGS) - 1
SOLVER_SEED = 20261102
EPS = 1e-9
NEIGHBOURS = 10  # granular neighbourhood of the local search
LS_MAX_PASSES = 60
STRONG_ITERS_PER_CUSTOMER = 500
SISR_AVG_REMOVED = 10
SISR_MAX_STRING = 10
SISR_BLINK = 0.01
SISR_T0, SISR_TF = 100.0, 1.0
TOP_UNIT_FRACTION = 0.01  # one point per this fraction of the top value above the top rung


class OpError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def invalid(message: str) -> OpError:
    return OpError("invalid_request", message)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


# =============================================================================================
# Problem data and objective
# =============================================================================================


class Problem:
    """Distance matrix over nodes 0 (depot) and 1..n (customers)."""

    def __init__(self, case: Case) -> None:
        depot, cust = case["depot"], case["customers"]
        self.n = len(cust)
        self.cap = int(case["capacity"])
        pts = [depot] + [[c[0], c[1]] for c in cust]
        self.dem = [0] + [int(c[2]) for c in cust]
        self.d = [[math.hypot(p[0] - q[0], p[1] - q[1]) for q in pts] for p in pts]
        self.neigh = [self._sorted_neighbours(i) for i in range(self.n + 1)]

    def _sorted_neighbours(self, i: int) -> list[int]:
        row = self.d[i]
        return sorted((j for j in range(1, self.n + 1) if j != i), key=lambda j: (row[j], j))

    def route_cost(self, r: list[int]) -> float:
        if not r:
            return 0.0
        d = self.d
        total = d[0][r[0]] + d[r[-1]][0]
        for a, b in zip(r, r[1:]):
            total += d[a][b]
        return total

    def cost(self, routes: Routes) -> float:
        return sum(self.route_cost(r) for r in routes)

    def load(self, r: list[int]) -> int:
        return sum(self.dem[c] for c in r)


def cvrp_evaluate(case: Case, answer: Any) -> float:
    n, cap = len(case["customers"]), int(case["capacity"])
    if not isinstance(answer, list) or any(not isinstance(r, list) for r in answer):
        raise invalid("answer must be an array of routes (arrays of customer indices)")
    seen = [False] * n
    for r in answer:
        load = 0
        for c in r:
            if not _is_int(c) or not 0 <= c < n:
                raise invalid(f"customer indices must be integers from 0 to {n - 1}")
            if seen[c]:
                raise invalid(f"customer {c} is visited more than once")
            seen[c] = True
            load += int(case["customers"][c][2])
        if load > cap:
            raise invalid(f"a route carries {load} > capacity {cap}")
    if not all(seen):
        raise invalid("every customer must be visited exactly once")
    pb = Problem(case)
    return pb.cost([[c + 1 for c in r] for r in answer])


# =============================================================================================
# Rungs 0-2: constructive
# =============================================================================================


def trivial(pb: Problem) -> Routes:
    return [[c] for c in range(1, pb.n + 1)]


def nearest(pb: Problem) -> Routes:
    left = set(range(1, pb.n + 1))
    routes: Routes = []
    while left:
        r: list[int] = []
        load, cur = 0, 0
        while True:
            cand = [c for c in left if load + pb.dem[c] <= pb.cap]
            if not cand:
                break
            nxt = min(cand, key=lambda c: (pb.d[cur][c], c))
            r.append(nxt)
            left.remove(nxt)
            load += pb.dem[nxt]
            cur = nxt
        routes.append(r)
    return routes


def savings(pb: Problem) -> Routes:
    d, n = pb.d, pb.n
    routes: dict[int, list[int]] = {c: [c] for c in range(1, n + 1)}
    loads = {c: pb.dem[c] for c in range(1, n + 1)}
    rid = {c: c for c in range(1, n + 1)}
    pairs = sorted(
        ((d[0][i] + d[0][j] - d[i][j], i, j) for i in range(1, n + 1) for j in range(i + 1, n + 1)),
        key=lambda t: (-t[0], t[1], t[2]),
    )
    for s, i, j in pairs:
        if s <= 0:
            break
        ri, rj = rid[i], rid[j]
        if ri == rj or loads[ri] + loads[rj] > pb.cap:
            continue
        a, b = routes[ri], routes[rj]
        if a[-1] == i and b[0] == j:
            merged = a + b
        elif a[0] == i and b[-1] == j:
            merged = b + a
        elif a[0] == i and b[0] == j:
            merged = a[::-1] + b
        elif a[-1] == i and b[-1] == j:
            merged = a + b[::-1]
        else:
            continue
        routes[ri] = merged
        loads[ri] += loads[rj]
        del routes[rj], loads[rj]
        for c in b:
            rid[c] = ri
    return [routes[k] for k in sorted(routes)]


# =============================================================================================
# Rung 3: local search (first improvement; relocate, swap, 2-opt, 2-opt*)
# =============================================================================================


def _candidates(pb: Problem, routes: Routes, loc: dict[int, tuple[int, int]], c: int, d: int):
    """Yield ``(ri, new_ri_route, rj, new_rj_route)`` for every move of the pair (c, d)."""
    (ri, pi), (rj, pj) = loc[c], loc[d]
    a, b = routes[ri], routes[rj]
    if ri == rj:
        without = a[:pi] + a[pi + 1 :]
        q = without.index(d)
        yield ri, without[:q] + [c] + without[q:], None, None  # relocate before d
        yield ri, without[: q + 1] + [c] + without[q + 1 :], None, None  # relocate after d
        swapped = list(a)
        swapped[pi], swapped[pj] = d, c
        yield ri, swapped, None, None
        lo, hi = (pi, pj) if pi < pj else (pj, pi)
        yield ri, a[:lo] + a[lo : hi + 1][::-1] + a[hi + 1 :], None, None  # 2-opt
        return
    dc, dd = pb.dem[c], pb.dem[d]
    la, lb = pb.load(a), pb.load(b)
    if lb + dc <= pb.cap:
        a2 = a[:pi] + a[pi + 1 :]
        yield ri, a2, rj, b[:pj] + [c] + b[pj:]  # c before d
        yield ri, a2, rj, b[: pj + 1] + [c] + b[pj + 1 :]  # c after d
    if la - dc + dd <= pb.cap and lb - dd + dc <= pb.cap:
        yield ri, a[:pi] + [d] + a[pi + 1 :], rj, b[:pj] + [c] + b[pj + 1 :]  # swap
    t1, t2 = a[pi + 1 :], b[pj + 1 :]
    if (
        pb.load(a[: pi + 1]) + pb.load(t2) <= pb.cap
        and pb.load(b[: pj + 1]) + pb.load(t1) <= pb.cap
    ):
        yield ri, a[: pi + 1] + t2, rj, b[: pj + 1] + t1  # 2-opt*: exchange the tails


def local_search(pb: Problem, routes: Routes) -> Routes:
    routes = [list(r) for r in routes if r]
    for _ in range(LS_MAX_PASSES):
        improved = False
        loc = {c: (i, p) for i, r in enumerate(routes) for p, c in enumerate(r)}
        for c in range(1, pb.n + 1):
            moved = False
            for d in pb.neigh[c][:NEIGHBOURS]:
                ri, rj = loc[c][0], loc[d][0]
                old = pb.route_cost(routes[ri]) + (pb.route_cost(routes[rj]) if ri != rj else 0.0)
                for ri2, na, rj2, nb in _candidates(pb, routes, loc, c, d):
                    new = pb.route_cost(na) + (pb.route_cost(nb) if nb is not None else 0.0)
                    if new < old - EPS:
                        routes[ri2] = na
                        if nb is not None:
                            routes[rj2] = nb
                        moved = True
                        break
                if moved:
                    break
            if moved:
                improved = True
                routes = [r for r in routes if r]
                loc = {c2: (i, p) for i, r in enumerate(routes) for p, c2 in enumerate(r)}
        if not improved:
            break
    return routes


# =============================================================================================
# Rung 4: SISR (slack induction by string removals), simulated annealing acceptance
# =============================================================================================


def _ruin(pb: Problem, routes: Routes, rng: random.Random) -> tuple[Routes, list[int]]:
    n = pb.n
    where = {c: i for i, r in enumerate(routes) for c in r}
    avg_card = n / len(routes)
    ls_max = min(SISR_MAX_STRING, avg_card)
    ks_max = 4.0 * SISR_AVG_REMOVED / (1.0 + ls_max) - 1.0
    ks = int(rng.uniform(1.0, ks_max + 1.0))
    seed = rng.randint(1, n)
    new = [list(r) for r in routes]
    removed: list[int] = []
    ruined: set[int] = set()
    for c in [seed] + pb.neigh[seed]:
        if len(ruined) >= ks:
            break
        ri = where[c]
        if c in removed or ri in ruined:
            continue
        r = new[ri]
        lt_max = min(len(r), ls_max)
        lt = int(rng.uniform(1.0, lt_max + 1.0))
        pos = r.index(c)
        if lt < len(r) and rng.random() < 0.5:  # split string: keep m customers inside the string
            m = rng.randint(1, len(r) - lt)
            span = lt + m
            start = max(0, min(pos - rng.randint(0, span - 1), len(r) - span))
            seg = r[start : start + span]
            keep0 = rng.randint(0, lt)
            drop = seg[:keep0] + seg[keep0 + m :]
            removed += drop
            new[ri] = r[:start] + seg[keep0 : keep0 + m] + r[start + span :]
        else:  # plain string containing c
            start = max(0, min(pos - rng.randint(0, lt - 1), len(r) - lt))
            removed += r[start : start + lt]
            new[ri] = r[:start] + r[start + lt :]
        ruined.add(ri)
    return [r for r in new if r], removed


def _recreate(pb: Problem, routes: Routes, removed: list[int], rng: random.Random) -> Routes:
    order = rng.choices(("random", "demand", "far", "close"), weights=(4, 4, 2, 1))[0]
    if order == "random":
        rng.shuffle(removed)
    elif order == "demand":
        removed.sort(key=lambda c: (-pb.dem[c], c))
    elif order == "far":
        removed.sort(key=lambda c: (-pb.d[0][c], c))
    else:
        removed.sort(key=lambda c: (pb.d[0][c], c))
    d, cap = pb.d, pb.cap
    loads = [pb.load(r) for r in routes]
    for c in removed:
        best, best_at = math.inf, None
        for i, r in enumerate(routes):
            if loads[i] + pb.dem[c] > cap:
                continue
            prev = 0
            for p in range(len(r) + 1):
                nxt = r[p] if p < len(r) else 0
                if rng.random() >= SISR_BLINK:
                    delta = d[prev][c] + d[c][nxt] - d[prev][nxt]
                    if delta < best:
                        best, best_at = delta, (i, p)
                prev = nxt
        if best_at is None:
            routes.append([c])
            loads.append(pb.dem[c])
        else:
            i, p = best_at
            routes[i] = routes[i][:p] + [c] + routes[i][p:]
            loads[i] += pb.dem[c]
    return routes


def sisr(pb: Problem, start: Routes, iterations: int, seed: int) -> Routes:
    rng = random.Random(seed)
    cur = [list(r) for r in start if r]
    cur_cost = pb.cost(cur)
    best, best_cost = [list(r) for r in cur], cur_cost
    if pb.n < 3:
        return best
    cooling = (SISR_TF / SISR_T0) ** (1.0 / max(1, iterations))
    temp = SISR_T0
    for _ in range(iterations):
        ruined, removed = _ruin(pb, cur, rng)
        cand = _recreate(pb, ruined, removed, rng)
        cand_cost = pb.cost(cand)
        if cand_cost < cur_cost - temp * math.log(1.0 - rng.random()):
            cur, cur_cost = cand, cand_cost
            if cur_cost < best_cost - EPS:
                best, best_cost = [list(r) for r in cur], cur_cost
        temp *= cooling
    return best


# =============================================================================================
# Ladder, points, protocol
# =============================================================================================


def ladder(case: Case) -> list[Routes]:
    pb = Problem(case)
    rungs = [trivial(pb), nearest(pb), savings(pb)]
    rungs.append(local_search(pb, rungs[2]))
    rungs.append(sisr(pb, rungs[3], STRONG_ITERS_PER_CUSTOMER * pb.n, SOLVER_SEED))
    return rungs


def to_answer(routes: Routes) -> list[list[int]]:
    return [[c - 1 for c in r] for r in routes if r]


def evaluate(problem: str, case: Case, answer: Any) -> float:
    if problem != "cvrp":
        raise OpError("unknown_problem", f"unknown problem {problem!r}")
    return float(cvrp_evaluate(case, answer))


def ladder_values(problem: str, case: Case) -> list[float]:
    """Objective of each rung, made non-increasing (a stronger solver could return the weaker
    answer)."""
    pb = Problem(case)
    values = [pb.cost(r) for r in ladder(case)]
    for k in range(1, len(values)):
        values[k] = min(values[k], values[k - 1])
    return values


def ladder_points(y: float, lad: list[float]) -> float:
    """Piecewise-linear rung position of objective *y* (minimisation); above the top rung one more
    point per ``max(last gap, TOP_UNIT_FRACTION x top value)`` of improvement: no upper limit."""
    if y >= lad[0]:
        return 0.0
    for k in range(len(lad) - 1):
        if y >= lad[k + 1]:
            return k + (lad[k] - y) / (lad[k] - lad[k + 1])
    top = len(lad) - 1
    unit = max(lad[top - 1] - lad[top], TOP_UNIT_FRACTION * abs(lad[top]))
    return top + (lad[top] - y) / unit


def solve(problem: str, case: Case) -> list[list[int]]:
    return to_answer(ladder(case)[TOP_RUNG])


def handle(req: Any) -> dict[str, Any]:
    try:
        if not isinstance(req, dict) or not isinstance(req.get("op"), str):
            raise invalid("request must be an object with a string op")
        if req["op"] == "ping":
            return {"ok": True}
        if req["op"] != "solve":
            raise OpError("unknown_op", f"unknown op {req['op']!r}")
        if req.get("problem") != "cvrp":
            raise OpError("unknown_problem", "problem is not cvrp")
        if not isinstance(req.get("case"), dict):
            raise invalid("case must be an object")
        return {"ok": True, "answer": solve("cvrp", req["case"])}
    except OpError as err:
        return {"ok": False, "error": err.code, "message": err.message}


def main() -> None:
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
            resp = handle(req)
        out.write(json.dumps(resp) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
