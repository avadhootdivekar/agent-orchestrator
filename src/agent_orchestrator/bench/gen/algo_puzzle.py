"""`algo-puzzle` family (T-Pf4Mix): algorithmic problems, seeded variants, huge hidden inputs.

Each instance asks for a stateless solver of several problems (LIS, weighted interval scheduling,
pair counting, subarray-sum counting, shortest path with k free edges, offline connectivity), each
in a seeded *variant* (strict vs non-decreasing, touching intervals allowed vs conflicting, directed
vs undirected, ...) so that recalled textbook code is close to, but not exactly, what is asked.
Large inputs are given as a seed for a fixed linear congruential stream (``gen`` cases), so the
quadratic solution of every problem times out while the request stays one short line.

Expected answers come from the reference (``reference/algo_server.py``), whose efficient solvers are
cross-checked here against brute-force solvers on many small random cases at generation time.

Difficulty: ``entities`` = number of problems; ``traps`` = cases per variant-trap probe;
``concurrency`` = small random cases per core probe; ``scale`` = size of the large ``gen`` cases.
"""

from __future__ import annotations

import importlib.util
import itertools
import random
import sys
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import cache
from types import ModuleType
from typing import Any

from . import dsl
from .base import Difficulty, Family, Instance, register_family
from .dsl import jsonl_step, mix_entry, perf_spec, probe
from .refs import REFERENCE_DIR, reference_files
from .skeleton import allow_list_text, resolve_allow, run_contract_text, skeleton_files

FAMILY_NAME = "algo-puzzle"
REFERENCE_PROGRAM = "algo_server.py"
PROBLEMS_BY_LEVEL = (2, 3, 4, 6)
VARIANT_CASES_BY_LEVEL = (1, 2, 3, 4)
SMALL_CASES_BY_LEVEL = (4, 6, 8, 10)
LARGE_N_BY_LEVEL = (2_000, 20_000, 100_000, 200_000)
CROSS_CHECK_CASES = 150
SEARCH_ATTEMPTS = 3000
REQUEST_SECONDS = 10
VARIANTS: dict[str, tuple[str, ...]] = {
    "lis": ("strict", "non_decreasing"),
    "intervals": ("touching_ok", "touching_conflicts"),
    "pair_count": ("sum", "abs_difference"),
    "subarray_sums": ("count",),
    "free_edges_path": ("directed", "undirected"),
    "connected_at": ("inclusive", "exclusive"),
}

JsonDict = dict[str, Any]


@cache
def load_reference(program: str) -> ModuleType:
    path = REFERENCE_DIR / program
    spec = importlib.util.spec_from_file_location(f"ao_bench_ref_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _engine_module() -> ModuleType:
    return load_reference(REFERENCE_PROGRAM)


def new_engine(limits: JsonDict, ps: ProblemSet | None = None) -> Any:
    return load_reference((ps or PUZZLE).reference).Engine(limits)


# ---------------------------------------------------------------------------------------------
# Brute-force solvers (explicit cases only) -- the independent check on the reference
# ---------------------------------------------------------------------------------------------


def brute_lis(c: JsonDict, variant: str) -> int:
    a = c["values"]
    best = [1] * len(a)
    for j in range(len(a)):
        for i in range(j):
            if a[i] < a[j] or (variant == "non_decreasing" and a[i] == a[j]):
                best[j] = max(best[j], best[i] + 1)
    return max(best, default=0)


def brute_intervals(c: JsonDict, variant: str) -> int:
    jobs = c["jobs"]

    def compatible(x: list[int], y: list[int]) -> bool:
        if variant == "touching_ok":
            return x[1] <= y[0] or y[1] <= x[0]
        return x[1] < y[0] or y[1] < x[0]

    best = 0
    for r in range(len(jobs) + 1):
        for subset in itertools.combinations(jobs, r):
            if all(compatible(x, y) for x, y in itertools.combinations(subset, 2)):
                best = max(best, sum(j[2] for j in subset))
    return best


def brute_pair_count(c: JsonDict, variant: str) -> int:
    a, lo, hi = c["values"], c["lo"], c["hi"]
    f = (lambda x, y: x + y) if variant == "sum" else (lambda x, y: abs(x - y))
    return sum(lo <= f(a[i], a[j]) <= hi for i in range(len(a)) for j in range(i + 1, len(a)))


def brute_subarray_sums(c: JsonDict, _variant: str) -> int:
    a, lo, hi = c["values"], c["lo"], c["hi"]
    return sum(lo <= sum(a[i:j]) <= hi for i in range(len(a)) for j in range(i + 1, len(a) + 1))


def brute_free_edges_path(c: JsonDict, variant: str) -> int:
    n, k = c["n"], c["k"]
    arcs = [tuple(e) for e in c["edges"]]
    if variant == "undirected":
        arcs += [(v, u, w) for u, v, w in c["edges"]]
    inf = float("inf")
    dist = [[inf] * (k + 1) for _ in range(n)]
    dist[0][0] = 0
    for _ in range(n * (k + 1) + 1):  # Bellman-Ford over (node, free edges used)
        for u, v, w in arcs:
            for used in range(k + 1):
                d = dist[u][used]
                dist[v][used] = min(dist[v][used], d + w)
                if used < k:
                    dist[v][used + 1] = min(dist[v][used + 1], d)
    best = min(dist[n - 1])
    return -1 if best == inf else int(best)


def brute_connected_at(c: JsonDict, variant: str) -> int:
    total = 0
    for u, v, t in c["queries"]:
        adj: dict[int, list[int]] = {}
        for a, b, lt in c["links"]:
            if lt <= t if variant == "inclusive" else lt < t:
                adj.setdefault(a, []).append(b)
                adj.setdefault(b, []).append(a)
        seen, todo = {u}, deque([u])
        while todo:
            x = todo.popleft()
            for y in adj.get(x, []):
                if y not in seen:
                    seen.add(y)
                    todo.append(y)
        total += v in seen
    return total


BRUTE: dict[str, Callable[[JsonDict, str], int]] = {
    "lis": brute_lis,
    "intervals": brute_intervals,
    "pair_count": brute_pair_count,
    "subarray_sums": brute_subarray_sums,
    "free_edges_path": brute_free_edges_path,
    "connected_at": brute_connected_at,
}


# ---------------------------------------------------------------------------------------------
# Random cases
# ---------------------------------------------------------------------------------------------


def small_case(rng: random.Random, problem: str) -> JsonDict:
    """A small explicit case (brute-forceable), biased towards ties and boundary values."""
    n = rng.randint(1, 9)
    if problem == "lis":
        return {"values": [rng.randint(0, 6) for _ in range(rng.randint(1, 12))]}
    if problem == "intervals":
        jobs = []
        for _ in range(n):
            s = rng.randint(0, 12)
            jobs.append([s, s + rng.randint(1, 5), rng.randint(1, 9)])
        return {"jobs": jobs}
    if problem in ("pair_count", "subarray_sums"):
        lo = rng.randint(-8, 6)
        return {"values": [rng.randint(-6, 6) for _ in range(rng.randint(1, 12))], "lo": lo,
                "hi": lo + rng.randint(-1, 8)}  # fmt: skip
    if problem == "free_edges_path":
        m = rng.randint(0, 2 * n)
        edges = [[rng.randrange(n), rng.randrange(n), rng.randint(0, 9)] for _ in range(m)]
        return {"n": n, "k": rng.randint(0, 2), "edges": edges}
    links = [
        [rng.randrange(n), rng.randrange(n), rng.randint(0, 6)]
        for _ in range(rng.randint(0, 2 * n))
    ]
    queries = [
        [rng.randrange(n), rng.randrange(n), rng.randint(0, 6)] for _ in range(rng.randint(1, 6))
    ]
    return {"n": n, "links": links, "queries": queries}


def gen_params(rng: random.Random, problem: str, n: int) -> JsonDict:
    """`gen` parameters for a case of size about `n` (see SPEC.md for each problem's expansion)."""
    seed = rng.randrange(2**32)
    if problem == "lis":
        return {"seed": seed, "n": n, "max": rng.choice((n // 4 + 1, 10**6, 10**9))}
    if problem == "intervals":
        span, max_len = n * rng.choice((5, 50)), rng.choice((20, 500))
        return {"seed": seed, "n": n, "span": span, "max_len": max_len,
                "max_value": rng.choice((10, 1000))}  # fmt: skip
    if problem == "pair_count":
        m = rng.choice((1000, 10**6))
        lo = rng.randint(-m, m)
        return {"seed": seed, "n": n, "max": m, "lo": lo, "hi": lo + rng.randint(0, m)}
    if problem == "subarray_sums":
        m = rng.choice((10, 1000))
        return {
            "seed": seed,
            "n": n,
            "max": m,
            "lo": -rng.randint(0, 5 * m),
            "hi": rng.randint(0, 50 * m),
        }
    if problem == "free_edges_path":
        return {"seed": seed, "n": max(2, n // 4), "m": n, "k": rng.randint(1, 3), "max_w": 1000}
    return {"seed": seed, "n": max(2, n // 2), "m": n // 2, "q": n, "max_t": 10**6}


def cross_check(rng: random.Random, ps: ProblemSet | None = None) -> None:
    """The reference's efficient solvers agree with brute force on every problem and variant."""
    ps = ps or PUZZLE
    module = load_reference(ps.reference)
    for problem, variants in ps.variants.items():
        for variant in variants:
            for _ in range(CROSS_CHECK_CASES):
                case = ps.small_case(rng, problem)
                got = module.PROBLEMS[problem][1](case, variant)
                want = ps.brute[problem](case, variant)
                if got != want:
                    raise AssertionError(
                        f"{problem}/{variant}: reference {got} != brute {want} on {case}"
                    )


# ---------------------------------------------------------------------------------------------
# SPEC.md
# ---------------------------------------------------------------------------------------------

_STATEMENT = {
    ("lis", "strict"): "the length of the longest **strictly increasing** subsequence of "
    "`values` (not necessarily contiguous).",
    ("lis", "non_decreasing"): "the length of the longest **non-decreasing** subsequence of "
    "`values` (not necessarily contiguous; equal neighbours are allowed).",
    ("intervals", "touching_ok"): "the largest total `value` of a set of jobs no two of which "
    "overlap. A job `[start, end, value]` occupies the half-open time range `[start, end)`, so a "
    "job ending at `t` and one starting at `t` **may both be chosen**.",
    ("intervals", "touching_conflicts"): "the largest total `value` of a set of jobs no two of "
    "which overlap. A job `[start, end, value]` occupies the closed range `[start, end]`, so a job "
    "ending at `t` and one starting at `t` **conflict**.",
    ("pair_count", "sum"): "the number of index pairs `i < j` with "
    "`lo <= values[i] + values[j] <= hi`.",
    ("pair_count", "abs_difference"): "the number of index pairs `i < j` with "
    "`lo <= |values[i] - values[j]| <= hi`.",
    ("subarray_sums", "count"): "the number of non-empty contiguous subarrays "
    "`values[i..j]` whose sum `s` satisfies `lo <= s <= hi`. Values may be negative.",
    ("free_edges_path", "directed"): "the length of the shortest path from node `0` to node "
    "`n - 1`, where each edge `[u, v, w]` is **directed** from `u` to `v` with weight `w`, and up "
    "to `k` edges of the path may be used at cost 0. `-1` if node `n - 1` is unreachable (`0` when "
    "`n = 1`).",
    ("free_edges_path", "undirected"): "the length of the shortest path from node `0` to node "
    "`n - 1`, where each edge `[u, v, w]` is **undirected** (usable both ways), weight `w`, and "
    "up to `k` edges of the path may be used at cost 0. `-1` if node `n - 1` is unreachable (`0` "
    "when `n = 1`).",
    ("connected_at", "inclusive"): "how many of the `queries` `[u, v, t]` are answered yes: `u` "
    "and `v` are connected using only the (undirected) links `[a, b, time]` with `time <= t`. A "
    "node is always connected to itself.",
    ("connected_at", "exclusive"): "how many of the `queries` `[u, v, t]` are answered yes: `u` "
    "and `v` are connected using only the (undirected) links `[a, b, time]` with `time < t` "
    "(strictly before). A node is always connected to itself.",
}

_EXPLICIT = {
    "lis": '`{"values": [int, ...]}` (at least one value)',
    "intervals": '`{"jobs": [[start, end, value], ...]}` with `start < end`, `value >= 0`',
    "pair_count": '`{"values": [int, ...], "lo": int, "hi": int}`',
    "subarray_sums": '`{"values": [int, ...], "lo": int, "hi": int}`',
    "free_edges_path": '`{"n": int, "k": 0-5, "edges": [[u, v, w], ...]}` with nodes `0..n-1`, '
    "`w >= 0` (parallel edges and self-loops may occur)",
    "connected_at": '`{"n": int, "links": [[a, b, time], ...], "queries": [[u, v, t], ...]}` '
    "with nodes `0..n-1`",
}

_GEN = {
    "lis": '`{"seed", "n", "max"}` → `values` = `n` draws `draw(0, max)`.',
    "intervals": '`{"seed", "n", "span", "max_len", "max_value"}` → `n` jobs, each from three '
    "draws in this order: `start = draw(0, span)`, `length = draw(1, max_len)`, "
    "`value = draw(1, max_value)`; the job is `[start, start + length, value]`.",
    "pair_count": '`{"seed", "n", "max", "lo", "hi"}` → `values` = `n` draws `draw(-max, max)`; '
    "`lo`, `hi` as given.",
    "subarray_sums": '`{"seed", "n", "max", "lo", "hi"}` → `values` = `n` draws '
    "`draw(-max, max)`; `lo`, `hi` as given.",
    "free_edges_path": '`{"seed", "n", "m", "k", "max_w"}` → `m` edges, each from three draws in '
    "this order: `u = draw(0, n - 1)`, `v = draw(0, n - 1)`, `w = draw(1, max_w)`; `n`, `k` as "
    "given.",
    "connected_at": '`{"seed", "n", "m", "q", "max_t"}` → first `m` links, then `q` queries, each '
    "from three draws in this order: `draw(0, n - 1)`, `draw(0, n - 1)`, `draw(0, max_t)`; `n` as "
    "given.",
}


def render_spec(
    limits: JsonDict, language: str, allow: Sequence[str], ps: ProblemSet | None = None
) -> str:
    ps = ps or PUZZLE
    sections = []
    for i, problem in enumerate(limits["problems"], start=1):
        variant = limits["variants"][problem]
        sections += [
            f"### 4.{i} `{problem}`",
            "",
            f"Answer: {ps.statement[(problem, variant)]}",
            "",
            f"- Explicit case: {ps.explicit[problem]}.",
            f'- Generated case: `{{"gen": {{...}}}}` with {ps.gen_text[problem]}',
            "",
        ]
    parts = [
        "# Specification: algorithmic solver over JSON lines",
        "",
        "Implement the solver described here, in this directory. Everything in this document is a "
        "requirement. The program is checked only through its standard input and output and has "
        "no state between requests.",
        "",
        "## 1. Run contract",
        "",
        run_contract_text("jsonl", language),
        "",
        "## 2. Allowed dependencies",
        "",
        allow_list_text(language, allow),
        "",
        "## 3. Protocol",
        "",
        "- Every request is one line holding a JSON object with a string field `op`; every "
        "response is one line holding a JSON object with a boolean field `ok`. Blank lines are "
        "skipped.",
        '- `{"op": "ping"}` → `{"ok": true}`.',
        '- `{"op": "solve", "problem": <name>, "case": <case>}` → `{"ok": true, "answer": <int>}`.',
        '- Errors: `{"ok": false, "error": <code>, "message": <text>}` with code `unknown_op`, '
        "`unknown_problem` (a name not in section 4) or `invalid_request` (the line is not JSON, "
        "or "
        "`case` is not an object). Cases are otherwise always well-formed.",
        f"- **Size and time.** {ps.size_text} Every request must be answered within "
        f"{REQUEST_SECONDS} seconds. Answers fit in a signed 64-bit integer.",
        '- **Generated cases.** A case may be `{"gen": {...}}` instead of the explicit fields: the '
        "input is then produced from this stream of 32-bit numbers: `x_0 = seed`, "
        "`x_{k+1} = (1664525 * x_k + 1013904223) mod 2^32`. Each *draw* advances the stream once "
        "and uses the new value: `draw(lo, hi) = lo + (x mod (hi - lo + 1))`. The first draw uses "
        "`x_1`. Each problem below says how its input is drawn. Mind integer overflow: the product "
        "`1664525 * x` does not fit in 32 bits.",
        "",
        "## 4. Problems",
        "",
        *sections,
    ]
    return "\n".join(parts).rstrip() + "\n"


# ---------------------------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------------------------


def _expect(resp: JsonDict) -> JsonDict:
    return {"ok": False, "error": resp["error"]} if resp.get("ok") is False else resp


@dataclass(frozen=True)
class ProblemSet:
    """Everything a problem family needs besides the shared protocol, probes and spec frame."""

    reference: str
    variants: dict[str, tuple[str, ...]]
    brute: dict[str, Callable[[JsonDict, str], int]]
    small_case: Callable[[random.Random, str], JsonDict]
    gen_params: Callable[[random.Random, str, int], JsonDict]
    statement: dict[tuple[str, str], str]
    explicit: dict[str, str]
    gen_text: dict[str, str]
    size_text: str
    problems_by_level: tuple[int, ...]
    single_variant_trap: str = "negative_values"

    @property
    def order(self) -> tuple[str, ...]:
        return tuple(self.variants)


PUZZLE = ProblemSet(
    reference=REFERENCE_PROGRAM,
    variants=VARIANTS,
    brute=BRUTE,
    small_case=small_case,
    gen_params=gen_params,
    statement=_STATEMENT,
    explicit=_EXPLICIT,
    gen_text=_GEN,
    size_text=f"Inputs have up to {LARGE_N_BY_LEVEL[-1]:,} values, edges, links, queries or jobs.",
    problems_by_level=PROBLEMS_BY_LEVEL,
)


class Probes:
    def __init__(self, rng: random.Random, limits: JsonDict, ps: ProblemSet | None = None) -> None:
        self.rng = rng
        self.limits = limits
        self.ps = ps or PUZZLE
        self.engine = new_engine(limits, self.ps)

    def scenario(self, requests: Sequence[Any]) -> list[JsonDict]:
        return [jsonl_step(r, match=_expect(self.engine.handle(r))) for r in requests]

    def solve(self, problem: str, case: JsonDict) -> JsonDict:
        return {"op": "solve", "problem": problem, "case": case}

    def core(self, n_small: int, large_n: int) -> list[JsonDict]:
        first = self.limits["problems"][0]
        out = [
            probe("core-ping", self.scenario([{"op": "ping"}])),
            probe("core-errors", self.scenario([
                {"op": "solve", "problem": "no_such_problem", "case": {}},
                {"op": "answer"},
                {"op": "solve", "problem": first, "case": [1, 2]},
            ])),
        ]  # fmt: skip
        for problem in self.limits["problems"]:
            slug = problem.replace("_", "-")
            reqs = [self.solve(problem, self.ps.small_case(self.rng, problem))
                    for _ in range(n_small)]  # fmt: skip
            reqs.append(self.solve(problem, {"gen": self.ps.gen_params(self.rng, problem, 25)}))
            out.append(probe(f"core-{slug}", self.scenario(reqs)))
            large = self.solve(problem, {"gen": self.ps.gen_params(self.rng, problem, large_n)})
            out.append(probe(f"core-large-{slug}", self.scenario([large])))
        return out

    def variant_cases(self, problem: str, count: int) -> list[JsonDict]:
        """Small cases on which the other variant(s) of `problem` give a different answer."""
        module = load_reference(self.ps.reference)
        own = self.limits["variants"][problem]
        others = [v for v in self.ps.variants[problem] if v != own]
        found: list[JsonDict] = []
        for _ in range(SEARCH_ATTEMPTS):
            if len(found) == count:
                break
            case = self.ps.small_case(self.rng, problem)
            want = module.PROBLEMS[problem][1](case, own)
            if not others:  # no sibling variant: an edge case (negative values / blocked ends)
                if self._edge(case, want):
                    found.append(case)
            elif any(module.PROBLEMS[problem][1](case, o) != want for o in others):
                found.append(case)
        return found

    def _edge(self, case: JsonDict, want: int) -> bool:
        if "blocked" in case:  # grid paths: a blocked cell on many paths, answer still nonzero
            return bool(want) and len(case["blocked"]) >= 2
        return case.get("lo", 0) < 0 and any(v < 0 for v in case.get("values", [])) and bool(want)

    def trap(self, count: int) -> list[JsonDict]:
        out = []
        for problem in self.limits["problems"]:
            cases = self.variant_cases(problem, count)
            if not cases:
                continue
            single = len(self.ps.variants[problem]) == 1
            key = self.ps.single_variant_trap if single else "variant_semantics"
            reqs = [self.solve(problem, c) for c in cases]
            out.append(probe(f"trap-{problem.replace('_', '-')}", self.scenario(reqs), trap=key))
        return out

    def perf(self, scale: int) -> JsonDict:
        problem = self.limits["problems"][0]
        quick = self.solve(problem, self.ps.small_case(self.rng, problem))
        load = jsonl_step({"op": "ping"}, match={"ok": True}, repeat=1)
        step = jsonl_step(quick, match={"ok": True})
        return perf_spec(scale, load, [mix_entry(1.0, "solve", "solve", step)])


@register_family
class AlgoPuzzleFamily(Family):
    name = FAMILY_NAME
    contract = "jsonl"
    in_default_set = False
    problem_set = PUZZLE

    def generate(
        self,
        seed: int,
        difficulty: Difficulty,
        language: str,
        allow: Sequence[str] | None = None,
    ) -> Instance:
        allow_list = resolve_allow(language, allow)
        rng = random.Random(seed)
        ps = self.problem_set
        cross_check(random.Random(seed ^ 0x5EED), ps)
        chosen = rng.sample(ps.order, ps.problems_by_level[difficulty.entities])
        problems = sorted(chosen, key=ps.order.index)
        limits: JsonDict = {
            "problems": problems,
            "variants": {p: rng.choice(ps.variants[p]) for p in problems},
        }
        builder = Probes(rng, limits, ps)
        core = builder.core(SMALL_CASES_BY_LEVEL[difficulty.concurrency],
                            LARGE_N_BY_LEVEL[difficulty.scale])  # fmt: skip
        trap = builder.trap(VARIANT_CASES_BY_LEVEL[difficulty.traps])
        probes = dsl.probes_doc("jsonl", language, core, trap, builder.perf(difficulty.scale))
        return Instance(
            family=self.name,
            seed=seed,
            difficulty=difficulty,
            language=language,
            contract="jsonl",
            entities=[],
            traps=sorted({p["trap"] for p in trap}),
            limits=limits,
            allow=allow_list,
            spec_md=render_spec(limits, language, allow_list, ps),
            skeleton=skeleton_files("jsonl", language, allow_list, title="algorithmic solver"),
            probes=probes,
            reference=reference_files(ps.reference),
        )


__all__ = ["FAMILY_NAME", "PUZZLE", "AlgoPuzzleFamily", "ProblemSet", "new_engine", "render_spec"]
