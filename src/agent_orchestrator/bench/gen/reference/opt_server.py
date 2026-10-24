"""Reference implementation of the `opt-ladder` family (grader-owned, never agent-visible).

Open-scale optimisation (design `docs-md/benchmark-open-scale-design.md` §4.1): every answer is
feasible or not, and a feasible answer has an objective value (lower is better). The score is how
far up a *ladder* of deterministic reference solvers the answer reaches:

- rung 0 `trivial`: identity order / first fit in site order / round-robin parts;
- rung 1 `greedy`: nearest neighbour / cheapest feasible site, big customers first / BFS-grown
  parts;
- rung 2 `local_search`: 2-opt / customer moves and swaps / vertex swaps, to a local optimum;
- rung 3: tour = 2-opt + Or-opt; facility, partition = short simulated annealing;
- rung 4: tour = iterated local search; facility, partition = long simulated annealing.

Every solver is deterministic: a fixed-seed RNG and iteration budgets, never wall-clock time. The
generator stores the rung values with each hidden case, and the reference server answers with the
rung-4 solution, so a copied reference scores exactly the top rung. ``evaluate`` is the grader's
feasibility checker and objective, the single definition of "objective" used everywhere.

This file is copied verbatim into ``<grading_dir>/reference/`` by the generated provider.
"""

from __future__ import annotations

import json
import math
import random
import sys
from collections import deque
from collections.abc import Callable
from typing import Any

Case = dict[str, Any]
Answer = list[int]

RUNGS = ("trivial", "greedy", "local_search", "metaheuristic", "strong")
TOP_RUNG = len(RUNGS) - 1
SOLVER_SEED = 20261024
EPS = 1e-9
TOUR_NEIGHBOURS = 8
# Budgets of rung 3 (metaheuristic, ~1 s) and rung 4 (strong, continued from rung 3 for a CPU time
# comparable to the agent's per-case limit on a typical host). Iteration counts, never wall clock.
TOUR_ILS_ROUNDS = (1, 12)  # per point
TOUR_KICK_WINDOW = 100  # double-bridge cut points lie within this many positions (local kick)
FACILITY_SA_ITERS = (1000, 3000)  # per customer
PARTITION_SA_ITERS = (220, 2200)  # per vertex
# Above the top rung, one point per this fraction of the top value, or the last gap if larger
# (a tiny last gap would otherwise make the open end of the scale explode).
TOP_UNIT_FRACTION = 0.01
SA_START_TEMP_FRACTION = 0.05  # facility: start temperature = this x the mean opening cost


class OpError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def invalid(message: str) -> OpError:
    return OpError("invalid_request", message)


def _dist(p: list[int], q: list[int]) -> float:
    return math.hypot(p[0] - q[0], p[1] - q[1])


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _int_list(answer: Any, n: int, lo: int, hi: int) -> str | None:
    """None if `answer` is a list of `n` ints in [lo, hi), else the reason it is not."""
    if not isinstance(answer, list):
        return "answer must be an array"
    if len(answer) != n:
        return f"answer must have {n} entries, got {len(answer)}"
    if any(not _is_int(x) or not lo <= x < hi for x in answer):
        return f"answer entries must be integers from {lo} to {hi - 1}"
    return None


# =============================================================================================
# tour: closed tour through all points, minimise the Euclidean length
# =============================================================================================


def tour_length(pts: list[list[int]], order: Answer) -> float:
    n = len(order)
    return sum(_dist(pts[order[i]], pts[order[(i + 1) % n]]) for i in range(n))


def tour_evaluate(case: Case, answer: Any) -> float:
    pts = case["points"]
    reason = _int_list(answer, len(pts), 0, len(pts))
    if reason is None and len(set(answer)) != len(pts):
        reason = "answer must visit every point exactly once"
    if reason:
        raise invalid(reason)
    return tour_length(pts, answer)


class _Tour:
    """Array tour with position index; 2-opt and Or-opt driven by a queue of dirty cities."""

    def __init__(self, pts: list[list[int]], order: Answer, neigh: list[list[int]]) -> None:
        self.pts, self.t, self.neigh = pts, list(order), neigh
        self.n = len(order)
        self.pos = [0] * self.n
        self._index()

    def _index(self) -> None:
        for i, c in enumerate(self.t):
            self.pos[c] = i

    def d(self, a: int, b: int) -> float:
        return _dist(self.pts[a], self.pts[b])

    def succ(self, c: int) -> int:
        return self.t[(self.pos[c] + 1) % self.n]

    def pred(self, c: int) -> int:
        return self.t[(self.pos[c] - 1) % self.n]

    def _reverse(self, i: int, j: int) -> None:
        """Make edges (t[i], t[j]) and (t[i+1], t[j+1]) by reversing the path t[i+1..j]."""
        n, t = self.n, self.t
        if i > j:  # reverse the complementary path instead: same resulting cycle
            i, j = j, i
        seg = t[i + 1 : j + 1]
        seg.reverse()
        t[i + 1 : j + 1] = seg
        for k in range(i + 1, j + 1):
            self.pos[t[k]] = k
        _ = n

    def two_opt(self, queue: deque[int]) -> bool:
        queued = [False] * self.n
        for c in queue:
            queued[c] = True
        improved = False
        while queue:
            a = queue.popleft()
            queued[a] = False
            for back in (False, True):
                b = self.pred(a) if back else self.succ(a)
                dab = self.d(a, b)
                for c in self.neigh[a]:
                    dac = self.d(a, c)
                    if dac >= dab - EPS:
                        break
                    e = self.pred(c) if back else self.succ(c)
                    if e in (a, b):
                        continue
                    delta = dac + self.d(b, e) - dab - self.d(c, e)
                    if delta < -EPS:
                        if back:  # edges (b, a) and (e, c) -> (b, e), (a, c)
                            self._reverse(self.pos[b], self.pos[e])
                        else:  # edges (a, b) and (c, e) -> (a, c), (b, e)
                            self._reverse(self.pos[a], self.pos[c])
                        for x in (a, b, c, e):
                            if not queued[x]:
                                queued[x] = True
                                queue.append(x)
                        improved = True
                        break
                else:
                    continue
                break
        return improved

    def or_opt(self) -> bool:
        """One pass of segment moves (length 1-3, either orientation); True if any applied."""
        improved = False
        for start in range(self.n):
            for length in (1, 2, 3):
                if self.n < length + 3:
                    break
                if self._move_segment(start, length):
                    improved = True
                    break
        return improved

    def _move_segment(self, s0: int, length: int) -> bool:
        n, t = self.n, self.t
        i = self.pos[s0]
        seg = [t[(i + k) % n] for k in range(length)]
        sl = seg[-1]
        p, nx = t[(i - 1) % n], t[(i + length) % n]
        gain = self.d(p, s0) + self.d(sl, nx) - self.d(p, nx)
        if gain <= EPS:
            return False
        inside = set(seg)
        for c in self.neigh[s0] + self.neigh[sl]:
            if c in inside:
                continue
            e = self.succ(c)
            if e in inside:
                continue
            fwd = self.d(c, s0) + self.d(sl, e) - self.d(c, e)
            rev = self.d(c, sl) + self.d(s0, e) - self.d(c, e)
            best = min(fwd, rev)
            if gain - best > EPS:
                rest = [x for x in t if x not in inside]
                k = rest.index(c) + 1
                rest[k:k] = seg if fwd <= rev else seg[::-1]
                self.t = rest
                self._index()
                return True
        return False

    def local_search(self, queue: deque[int] | None = None, or_opt: bool = True) -> None:
        q = deque(range(self.n)) if queue is None else queue
        while True:
            self.two_opt(q)
            if not or_opt or not self.or_opt():
                return
            q = deque(range(self.n))


def _neighbours(pts: list[list[int]], k: int) -> list[list[int]]:
    n = len(pts)
    out = []
    for a in range(n):
        pa = pts[a]
        ds = sorted(
            range(n), key=lambda b: ((pts[b][0] - pa[0]) ** 2 + (pts[b][1] - pa[1]) ** 2, b)
        )
        out.append([b for b in ds if b != a][:k])
    return out


def _nearest_neighbour(pts: list[list[int]]) -> Answer:
    n = len(pts)
    left = set(range(1, n))
    order = [0]
    while left:
        last = pts[order[-1]]
        nxt = min(left, key=lambda b: (_dist(last, pts[b]), b))
        left.remove(nxt)
        order.append(nxt)
    return order


def tour_ladder(case: Case) -> list[Answer]:
    pts = case["points"]
    n = len(pts)
    trivial = list(range(n))
    greedy = _nearest_neighbour(pts)
    neigh = _neighbours(pts, TOUR_NEIGHBOURS)
    t2 = _Tour(pts, greedy, neigh)
    t2.local_search(or_opt=False)
    t3 = _Tour(pts, t2.t, neigh)
    t3.local_search()
    meta = _ils(pts, t3.t, neigh, TOUR_ILS_ROUNDS[0] * n, SOLVER_SEED)
    strong = _ils(pts, meta, neigh, TOUR_ILS_ROUNDS[1] * n, SOLVER_SEED + 1)
    return [trivial, greedy, list(t2.t), meta, strong]


def _ils(
    pts: list[list[int]], start: Answer, neigh: list[list[int]], rounds: int, seed: int
) -> Answer:
    """Iterated local search: local double-bridge kick + 2-opt repair, keep if shorter."""
    n = len(start)
    best = list(start)
    best_len = tour_length(pts, best)
    rng = random.Random(seed)
    for _ in range(rounds if n >= 8 else 0):
        r = rng.randrange(n)
        base = best[r:] + best[:r]
        a, b, c = sorted(rng.sample(range(1, min(n, TOUR_KICK_WINDOW)), 3))
        kicked = base[:a] + base[b:c] + base[a:b] + base[c:]
        tr = _Tour(pts, kicked, neigh)
        ends = {kicked[x % n] for x in (0, a - 1, a, b - 1, b, c - 1, c, n - 1)}
        tr.local_search(deque(sorted(ends)), or_opt=False)
        length = tour_length(pts, tr.t)
        if length < best_len - EPS:
            best, best_len = list(tr.t), length
    return best


# =============================================================================================
# facility: capacitated, single-source: every customer to one site, site load <= capacity;
# minimise opening cost of the used sites + demand x distance
# =============================================================================================


def facility_cost(case: Case, answer: Answer) -> float:
    sites, cust = case["sites"], case["customers"]
    opened = sum(sites[s][2] for s in sorted(set(answer)))
    return opened + sum(c[2] * _dist(c, sites[s]) for c, s in zip(cust, answer, strict=True))


def facility_evaluate(case: Case, answer: Any) -> float:
    sites, cust = case["sites"], case["customers"]
    reason = _int_list(answer, len(cust), 0, len(sites))
    if reason is None:
        load = [0] * len(sites)
        for c, s in zip(cust, answer, strict=True):
            load[s] += c[2]
        over = [s for s in range(len(sites)) if load[s] > sites[s][3]]
        if over:
            reason = f"site {over[0]} serves demand {load[over[0]]} over its capacity"
    if reason:
        raise invalid(reason)
    return facility_cost(case, answer)


class _Facility:
    """Assignment state with O(1) move/swap deltas over a precomputed cost matrix."""

    def __init__(self, case: Case) -> None:
        self.f = [s[2] for s in case["sites"]]
        self.cap = [s[3] for s in case["sites"]]
        self.dem = [c[2] for c in case["customers"]]
        self.m, self.n = len(self.f), len(self.dem)
        self.a = [[c[2] * _dist(c, s) for s in case["sites"]] for c in case["customers"]]

    def first_fit(self) -> Answer:
        load, out, s = [0] * self.m, [], 0
        for c in range(self.n):
            while load[s] + self.dem[c] > self.cap[s]:
                s += 1
            load[s] += self.dem[c]
            out.append(s)
        return out

    def greedy(self) -> Answer:
        load, used, out = [0] * self.m, [False] * self.m, [0] * self.n
        for c in sorted(range(self.n), key=lambda c: (-self.dem[c], c)):
            ok = [s for s in range(self.m) if load[s] + self.dem[c] <= self.cap[s]]
            s = min(ok, key=lambda s: (self.a[c][s] + (0 if used[s] else self.f[s]), s))
            load[s] += self.dem[c]
            used[s] = True
            out[c] = s
        return out

    def _state(self, assign: Answer) -> tuple[list[int], list[int]]:
        load, count = [0] * self.m, [0] * self.m
        for c, s in enumerate(assign):
            load[s] += self.dem[c]
            count[s] += 1
        return load, count

    def move_delta(
        self, c: int, t: int, assign: Answer, load: list[int], count: list[int]
    ) -> float | None:
        s = assign[c]
        if s == t or load[t] + self.dem[c] > self.cap[t]:
            return None
        return (
            self.a[c][t]
            - self.a[c][s]
            + (self.f[t] if count[t] == 0 else 0.0)
            - (self.f[s] if count[s] == 1 else 0.0)
        )

    def swap_delta(self, c1: int, c2: int, assign: Answer, load: list[int]) -> float | None:
        s, t = assign[c1], assign[c2]
        if s == t:
            return None
        d1, d2 = self.dem[c1], self.dem[c2]
        if load[s] - d1 + d2 > self.cap[s] or load[t] - d2 + d1 > self.cap[t]:
            return None
        return self.a[c1][t] + self.a[c2][s] - self.a[c1][s] - self.a[c2][t]

    def _apply_move(
        self, c: int, t: int, assign: Answer, load: list[int], count: list[int]
    ) -> None:
        s = assign[c]
        load[s] -= self.dem[c]
        count[s] -= 1
        load[t] += self.dem[c]
        count[t] += 1
        assign[c] = t

    def local_search(self, assign: Answer) -> Answer:
        assign = list(assign)
        load, count = self._state(assign)
        improved = True
        while improved:
            improved = False
            for c in range(self.n):
                for t in range(self.m):
                    d = self.move_delta(c, t, assign, load, count)
                    if d is not None and d < -EPS:
                        self._apply_move(c, t, assign, load, count)
                        improved = True
            for c1 in range(self.n):
                for c2 in range(c1 + 1, self.n):
                    d = self.swap_delta(c1, c2, assign, load)
                    if d is not None and d < -EPS:
                        s, t = assign[c1], assign[c2]
                        self._apply_move(c1, t, assign, load, count)
                        self._apply_move(c2, s, assign, load, count)
                        improved = True
        return assign

    def _relocate(
        self, c: int, avoid: int, assign: Answer, load: list[int], count: list[int]
    ) -> float | None:
        """Move c to its cheapest feasible site other than `avoid`; the delta, or None."""
        best_t, best_d = -1, 0.0
        for t in range(self.m):
            if t == avoid:
                continue
            d = self.move_delta(c, t, assign, load, count)
            if d is not None and (best_t < 0 or d < best_d):
                best_t, best_d = t, d
        if best_t < 0:
            return None
        self._apply_move(c, best_t, assign, load, count)
        return best_d

    def _close(self, s: int, assign: Answer, load: list[int], count: list[int]) -> float | None:
        """Compound move: empty site s (biggest customers first)."""
        total = 0.0
        for c in sorted(
            (c for c in range(self.n) if assign[c] == s), key=lambda c: (-self.dem[c], c)
        ):
            d = self._relocate(c, s, assign, load, count)
            if d is None:
                return None
            total += d
        return total

    def _open(self, t: int, assign: Answer, load: list[int], count: list[int]) -> float:
        """Compound move: move every customer that is cheaper at site t there, best gain first."""
        total = 0.0
        gains = sorted((self.a[c][t] - self.a[c][assign[c]], c) for c in range(self.n))
        for g, c in gains:
            if g >= 0:
                break
            d = self.move_delta(c, t, assign, load, count)
            if d is not None:
                self._apply_move(c, t, assign, load, count)
                total += d
        return total

    def anneal(self, assign: Answer, iters: int, seed: int, temp0: float) -> Answer:
        rng = random.Random(seed)
        assign = list(assign)
        load, count = self._state(assign)
        cur = sum(self.a[c][s] for c, s in enumerate(assign)) + sum(
            self.f[s] for s in range(self.m) if count[s]
        )
        best, best_cost = list(assign), cur
        for it in range(iters):
            temp = temp0 * (1 - it / iters) + EPS
            r = rng.random()
            c = rng.randrange(self.n)
            d: float | None
            if r < 0.02:  # compound: close or open a whole site, undone if rejected
                saved = (list(assign), list(load), list(count))
                s = rng.randrange(self.m)
                d = (
                    self._close(s, assign, load, count)
                    if count[s]
                    else self._open(s, assign, load, count)
                )
                if d is None or not (d <= 0 or rng.random() < math.exp(-d / temp)):
                    assign, load, count = saved
                    continue
            elif r < 0.7:
                t = rng.randrange(self.m)
                d = self.move_delta(c, t, assign, load, count)
                if d is None or not (d <= 0 or rng.random() < math.exp(-d / temp)):
                    continue
                self._apply_move(c, t, assign, load, count)
            else:
                c2 = rng.randrange(self.n)
                d = self.swap_delta(c, c2, assign, load)
                if d is None or not (d <= 0 or rng.random() < math.exp(-d / temp)):
                    continue
                s, t = assign[c], assign[c2]
                self._apply_move(c, t, assign, load, count)
                self._apply_move(c2, s, assign, load, count)
            cur += d
            if cur < best_cost - EPS:
                best, best_cost = list(assign), cur
        return best


def facility_ladder(case: Case) -> list[Answer]:
    fac = _Facility(case)
    trivial = fac.first_fit()
    greedy = fac.greedy()
    local = fac.local_search(greedy)
    temp0 = SA_START_TEMP_FRACTION * sum(fac.f) / fac.m
    meta = fac.anneal(local, FACILITY_SA_ITERS[0] * fac.n, SOLVER_SEED, temp0)
    strong = fac.anneal(meta, FACILITY_SA_ITERS[1] * fac.n, SOLVER_SEED + 1, temp0)
    return [trivial, greedy, local, meta, strong]


# =============================================================================================
# partition: k balanced parts (sizes differ by at most 1), minimise the weight of cut edges
# =============================================================================================


def cut_weight(edges: list[list[int]], part: Answer) -> int:
    return sum(w for u, v, w in edges if part[u] != part[v])


def partition_evaluate(case: Case, answer: Any) -> int:
    n, k = case["n"], case["k"]
    reason = _int_list(answer, n, 0, k)
    if reason is None:
        sizes = [0] * k
        for p in answer:
            sizes[p] += 1
        if max(sizes) - min(sizes) > 1:
            reason = f"part sizes must differ by at most 1, got {min(sizes)}..{max(sizes)}"
    if reason:
        raise invalid(reason)
    return cut_weight(case["edges"], answer)


class _Partition:
    def __init__(self, case: Case) -> None:
        self.n, self.k, self.edges = case["n"], case["k"], case["edges"]
        self.adj: list[dict[int, int]] = [{} for _ in range(self.n)]
        for u, v, w in self.edges:
            if u != v:
                self.adj[u][v] = self.adj[u].get(v, 0) + w
                self.adj[v][u] = self.adj[v].get(u, 0) + w

    def sizes(self) -> list[int]:
        q, r = divmod(self.n, self.k)
        return [q + 1 if p < r else q for p in range(self.k)]

    def bfs_grow(self) -> Answer:
        order, seen = [], [False] * self.n
        for root in range(self.n):
            if seen[root]:
                continue
            seen[root] = True
            queue = deque([root])
            while queue:
                u = queue.popleft()
                order.append(u)
                for v in sorted(self.adj[u]):
                    if not seen[v]:
                        seen[v] = True
                        queue.append(v)
        part = [0] * self.n
        i = 0
        for p, size in enumerate(self.sizes()):
            for u in order[i : i + size]:
                part[u] = p
            i += size
        return part

    def to_part(self, u: int, p: int, part: Answer) -> int:
        """Weight from u into part p."""
        return sum(w for v, w in self.adj[u].items() if part[v] == p)

    def swap_delta(self, u: int, v: int, part: Answer) -> int:
        """Change in cut weight if u and v (different parts) swap parts."""
        pu, pv = part[u], part[v]
        gain_u = self.to_part(u, pv, part) - self.to_part(u, pu, part)
        gain_v = self.to_part(v, pu, part) - self.to_part(v, pv, part)
        return -(gain_u + gain_v - 2 * self.adj[u].get(v, 0))

    def local_search(self, part: Answer) -> Answer:
        part = list(part)
        improved = True
        while improved:
            improved = False
            for u in range(self.n):
                cands = sorted({x for v in self.adj[u] for x in (v, *self.adj[v])})
                for v in cands:
                    if part[v] != part[u] and self.swap_delta(u, v, part) < 0:
                        part[u], part[v] = part[v], part[u]
                        improved = True
                        break
        return part

    def anneal(self, part: Answer, iters: int, seed: int) -> Answer:
        rng = random.Random(seed)
        part = list(part)
        cur = cut_weight(self.edges, part)
        best, best_cut = list(part), cur
        temp0 = 2.0
        for it in range(iters):
            temp = temp0 * (1 - it / iters) + EPS
            u = rng.randrange(self.n)
            nb = list(self.adj[u])
            if nb and rng.random() < 0.8:  # near swap: a vertex next to u's neighbour
                w = nb[rng.randrange(len(nb))]
                near = list(self.adj[w]) + [w]
                v = near[rng.randrange(len(near))]
            else:
                v = rng.randrange(self.n)
            if part[u] == part[v]:
                continue
            delta = self.swap_delta(u, v, part)
            if delta <= 0 or rng.random() < math.exp(-delta / temp):
                part[u], part[v] = part[v], part[u]
                cur += delta
                if cur < best_cut:
                    best, best_cut = list(part), cur
        return best


def partition_ladder(case: Case) -> list[Answer]:
    g = _Partition(case)
    trivial = [u % g.k for u in range(g.n)]
    greedy = g.bfs_grow()
    local = g.local_search(greedy)
    meta = g.anneal(local, PARTITION_SA_ITERS[0] * g.n, SOLVER_SEED)
    strong = g.anneal(meta, PARTITION_SA_ITERS[1] * g.n, SOLVER_SEED + 1)
    return [trivial, greedy, local, meta, strong]


# =============================================================================================
# Shared: problems table, ladder points, protocol
# =============================================================================================

PROBLEMS: dict[str, tuple[Callable[[Case], list[Answer]], Callable[[Case, Any], float]]] = {
    "tour": (tour_ladder, tour_evaluate),
    "facility": (facility_ladder, facility_evaluate),
    "partition": (partition_ladder, partition_evaluate),
}


def evaluate(problem: str, case: Case, answer: Any) -> float:
    """Objective of a feasible answer (lower is better); raises OpError if it is infeasible."""
    if problem not in PROBLEMS:
        raise OpError("unknown_problem", f"unknown problem {problem!r}")
    return float(PROBLEMS[problem][1](case, answer))


def ladder_values(problem: str, case: Case) -> list[float]:
    """Objective of each rung's answer, made non-increasing (a rung is at least as good as the
    one below it: a stronger solver could always return the weaker answer)."""
    values = [evaluate(problem, case, a) for a in PROBLEMS[problem][0](case)]
    for k in range(1, len(values)):
        values[k] = min(values[k], values[k - 1])
    return values


def ladder_points(y: float, ladder: list[float]) -> float:
    """Design §3.1: piecewise-linear rung position of objective `y` (minimisation); beyond the top
    rung one more point per `max(last gap, TOP_UNIT_FRACTION x top value)` of improvement, so the
    scale has no upper limit but stays stable. Rungs must be strictly decreasing."""
    if y >= ladder[0]:
        return 0.0
    for k in range(len(ladder) - 1):
        if y >= ladder[k + 1]:
            return k + (ladder[k] - y) / (ladder[k] - ladder[k + 1])
    top = len(ladder) - 1
    unit = max(ladder[top - 1] - ladder[top], TOP_UNIT_FRACTION * abs(ladder[top]))
    return top + (ladder[top] - y) / unit


def solve(problem: str, case: Case) -> Answer:
    return PROBLEMS[problem][0](case)[TOP_RUNG]


def handle(req: Any) -> dict[str, Any]:
    try:
        if not isinstance(req, dict) or not isinstance(req.get("op"), str):
            raise invalid("request must be an object with a string op")
        if req["op"] == "ping":
            return {"ok": True}
        if req["op"] != "solve":
            raise OpError("unknown_op", f"unknown op {req['op']!r}")
        if req.get("problem") not in PROBLEMS:
            raise OpError("unknown_problem", "problem is not one of this engine's problems")
        if not isinstance(req.get("case"), dict):
            raise invalid("case must be an object")
        return {"ok": True, "answer": solve(req["problem"], req["case"])}
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
