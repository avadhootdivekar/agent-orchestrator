"""`opt-ladder` family (T-Os5Lad): open-scale combinatorial optimisation, graded by an `objective`
grader against a ladder of reference solvers (design `docs-md/benchmark-open-scale-design.md` §3.1,
§4.1, §6).

Pass/fail families saturate because a fully specified task has one right answer. Here every
feasible answer is accepted and *scored by quality*: rung 0 (trivial) ... rung 4 (long
metaheuristic), interpolated, and extrapolated beyond the top rung, so the raw points have no upper
limit. The hidden cases (explicit instances, not generator parameters) and their rung values are
written to ``probes.json`` (``kind: objective``) in the grader-owned dir; SPEC.md states the
problems, the answer format, the objective and the per-request time limit, never the cases.

Difficulty axes reuse the shared vector:

- ``entities`` -> problems per task (1, 2, 3, 3);
- ``concurrency`` -> hidden cases per problem (1, 2, 3, 4);
- ``scale`` -> instance size (see ``SIZES``);
- ``traps`` -> degenerate inputs that must still get a feasible answer (``traps >= 1``: duplicate
  points / isolated vertices / a free but remote site).

``limits.par`` is the points value that maps to score 1.0 (design §5: the last knob to move, by
whole rungs); the default is the top rung.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from functools import cache
from types import ModuleType
from typing import Any

from .algo_puzzle import load_reference
from .base import Difficulty, Family, Instance, register_family
from .refs import reference_files
from .skeleton import allow_list_text, resolve_allow, run_contract_text, skeleton_files

FAMILY_NAME = "opt-ladder"
REFERENCE_PROGRAM = "opt_server.py"
OBJECTIVE_KIND = "objective"
PROBLEM_ORDER = ("tour", "facility", "partition")
PROBLEMS_BY_LEVEL = (1, 2, 3, 3)
CASES_BY_LEVEL = (1, 2, 3, 4)
# Instance size per scale level: tour points, facility customers, partition vertices.
SIZES: dict[str, tuple[int, int, int, int]] = {
    "tour": (150, 300, 600, 1000),
    "facility": (80, 160, 300, 500),
    "partition": (120, 250, 500, 900),
}
CASE_SECONDS = 10
DEFAULT_SOLVED_POINTS = 2.0  # local-search quality on average
MIN_REL_GAP = 1e-4  # adjacent rungs closer than this (relative) make the case re-drawn
MAX_DRAWS = 8
COORD_MAX = 10_000

JsonDict = dict[str, Any]


@cache
def reference() -> ModuleType:
    return load_reference(REFERENCE_PROGRAM)


# -- case generation (grader side; explicit instances) ---------------------------------------


def _points(rng: random.Random, n: int) -> list[list[int]]:
    """Half uniform, half around 3-8 cluster centres (so greedy and good tours differ)."""
    centres = [
        [rng.randint(0, COORD_MAX), rng.randint(0, COORD_MAX)] for _ in range(rng.randint(3, 8))
    ]
    spread = COORD_MAX // 12
    pts = []
    for i in range(n):
        if i % 2:
            cx, cy = centres[rng.randrange(len(centres))]
            pts.append(
                [
                    min(COORD_MAX, max(0, cx + rng.randint(-spread, spread))),
                    min(COORD_MAX, max(0, cy + rng.randint(-spread, spread))),
                ]
            )
        else:
            pts.append([rng.randint(0, COORD_MAX), rng.randint(0, COORD_MAX)])
    return pts


def gen_tour(rng: random.Random, n: int, traps: int) -> JsonDict:
    pts = _points(rng, n)
    if traps:  # duplicate locations: zero-length edges must not break the answer
        for i in rng.sample(range(n), max(2, n // 50)):
            pts[i] = list(pts[rng.randrange(n)])
    return {"points": pts}


def gen_facility(rng: random.Random, n: int, traps: int) -> JsonDict:
    m = max(8, n // 6)
    customers = [[x, y, rng.randint(1, 10)] for x, y in _points(rng, n)]
    # Capacities: total about 2.5x the demand, so feasibility is easy but tight sites bind.
    mean_cap = max(10, 5 * sum(c[2] for c in customers) // (2 * m))
    sites = [
        [x, y, rng.randint(40_000, 160_000), rng.randint(mean_cap // 2, 3 * mean_cap // 2)]
        for x, y in _points(rng, m)
    ]
    if traps:  # a free site in a corner with a tiny capacity: cheap, but it fills up at once
        sites[rng.randrange(m)] = [0, 0, 0, 10]
    return {"sites": sites, "customers": customers}


def gen_partition(rng: random.Random, n: int, traps: int) -> JsonDict:
    pts = _points(rng, n)
    k = rng.choice((2, 3, 4, 6, 8))
    edges: dict[tuple[int, int], int] = {}
    isolated = set(rng.sample(range(n), max(1, n // 50))) if traps else set()
    for u in range(n):
        if u in isolated:
            continue
        px, py = pts[u]
        near = sorted(
            (v for v in range(n) if v != u and v not in isolated),
            key=lambda v: ((pts[v][0] - px) ** 2 + (pts[v][1] - py) ** 2, v),
        )[:3]
        for v in near:
            edges.setdefault((min(u, v), max(u, v)), rng.randint(1, 9))
    for _ in range(n // 20):  # a few long-range edges
        u, v = rng.sample(range(n), 2)
        if u not in isolated and v not in isolated:
            edges.setdefault((min(u, v), max(u, v)), rng.randint(1, 9))
    return {"n": n, "k": k, "edges": [[u, v, w] for (u, v), w in sorted(edges.items())]}


GENERATORS = {"tour": gen_tour, "facility": gen_facility, "partition": gen_partition}


def strict_ladder(values: Sequence[float]) -> bool:
    return all(a - b > MIN_REL_GAP * max(1.0, abs(a)) for a, b in zip(values, values[1:]))


def make_case(rng: random.Random, problem: str, n: int, traps: int) -> tuple[JsonDict, list[float]]:
    """Draw a case whose reference ladder is strictly improving (re-draw otherwise)."""
    for _ in range(MAX_DRAWS):
        case = GENERATORS[problem](rng, n, traps)
        values = reference().ladder_values(problem, case)
        if strict_ladder(values):
            return case, values
    raise ValueError(f"opt-ladder: no strict ladder for {problem} n={n} in {MAX_DRAWS} draws")


# -- SPEC.md ----------------------------------------------------------------------------------

STATEMENT = {
    "tour": (
        '`case = {"points": [[x, y], ...]}` (integer coordinates, `n` points, duplicates '
        "possible). **Answer:** an array that lists every point index `0..n-1` exactly once: the "
        "order of a closed tour (it returns from the last point to the first). **Objective "
        "(minimise):** the tour length, the sum of Euclidean distances `sqrt(dx^2 + dy^2)` between "
        "consecutive points including the closing edge."
    ),
    "facility": (
        '`case = {"sites": [[x, y, open_cost, capacity], ...], "customers": [[x, y, demand], '
        "...]}`. **Answer:** an array with one site index per customer (in customer order): the "
        "single site that serves all of that customer's demand. The total demand served by a site "
        "must not exceed its `capacity`. **Objective (minimise):** the sum of `open_cost` over the "
        "distinct sites used, plus, for every customer, `demand * distance(customer, its site)` "
        "(Euclidean)."
    ),
    "partition": (
        '`case = {"n": n, "k": k, "edges": [[u, v, weight], ...]}`: an undirected graph on '
        "vertices `0..n-1` (some vertices may have no edges). **Answer:** an array with one part "
        "number `0..k-1` per vertex. Part sizes must differ by at most 1 (every part has "
        "`floor(n/k)` or `ceil(n/k)` vertices). **Objective (minimise):** the total weight of "
        "edges whose two ends are in different parts."
    ),
}


def render_spec(limits: JsonDict, language: str, allow: Sequence[str]) -> str:
    rungs = reference().RUNGS
    sizes = ", ".join(f"`{p}` up to {limits['sizes'][p]:,}" for p in limits["problems"])
    sections = []
    for i, p in enumerate(limits["problems"], start=1):
        sections += [f"### 4.{i} `{p}`", "", STATEMENT[p], ""]
    parts = [
        "# Optimisation solver",
        "",
        "Build a program that answers optimisation requests over stdin/stdout. Every feasible "
        "answer is accepted; **better answers score more**. There is no single right answer.",
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
        '- `{"op": "solve", "problem": <name>, "case": <case>}` → '
        '`{"ok": true, "answer": <array>}`.',
        "- Requests arrive one at a time to the same process; answer each before the next arrives.",
        f"- **Time limit:** each `solve` must be answered within **{limits['case_seconds']} "
        "seconds** of the request (measured by the grader, including your own start-up of that "
        "request's work). A late, missing or infeasible answer scores 0 for that case. Use the "
        "time: answers that keep improving until shortly before the limit score better.",
        f"- **Sizes:** {sizes}.",
        "",
        "## 4. Problems",
        "",
        *sections,
        "## 5. Scoring",
        "",
        "Each hidden case is solved by a ladder of reference solvers of increasing strength "
        f"({', '.join(f'rung {i} {r}' for i, r in enumerate(rungs))}). An answer earns points by "
        "where its objective falls on that ladder, linearly between rungs: matching rung `k` earns "
        f"`k` points, and improving on the strongest rung earns more than {len(rungs) - 1} (no "
        "upper limit). Infeasible, malformed or late answers earn 0. The task's points are the "
        "mean over its cases.",
        "",
    ]
    return "\n".join(parts).rstrip() + "\n"


# -- family -----------------------------------------------------------------------------------


@register_family
class OptLadderFamily(Family):
    name = FAMILY_NAME
    contract = "jsonl"
    grader_type = OBJECTIVE_KIND
    instruction_kind = "optimize"
    in_default_set = False

    def generate(
        self,
        seed: int,
        difficulty: Difficulty,
        language: str,
        allow: Sequence[str] | None = None,
    ) -> Instance:
        allow_list = resolve_allow(language, allow)
        rng = random.Random(seed)
        chosen = rng.sample(PROBLEM_ORDER, PROBLEMS_BY_LEVEL[difficulty.entities])
        problems = sorted(chosen, key=PROBLEM_ORDER.index)
        sizes = {p: SIZES[p][difficulty.scale] for p in problems}
        top = reference().TOP_RUNG
        limits: JsonDict = {
            "problems": problems,
            "sizes": sizes,
            "case_seconds": CASE_SECONDS,
            "par": float(top),
            "solved_points": DEFAULT_SOLVED_POINTS,
        }
        cases = []
        for p in problems:
            for j in range(CASES_BY_LEVEL[difficulty.concurrency]):
                case, values = make_case(rng, p, sizes[p], difficulty.traps)
                cases.append(
                    {
                        "id": f"{p}-{j + 1}",
                        "problem": p,
                        "sense": "min",
                        "case": case,
                        "ladder": values,
                    }
                )
        probes = {
            "version": "1.0",
            "kind": OBJECTIVE_KIND,
            "contract": "jsonl",
            "rungs": list(reference().RUNGS),
            "case_seconds": CASE_SECONDS,
            "par": float(top),
            "solved_points": DEFAULT_SOLVED_POINTS,
            "cases": cases,
        }
        return Instance(
            family=self.name,
            seed=seed,
            difficulty=difficulty,
            language=language,
            contract="jsonl",
            entities=[],
            traps=(["capacity_bound"] if "facility" in problems else [])
            + (["degenerate_inputs"] if difficulty.traps else []),
            limits=limits,
            allow=allow_list,
            spec_md=render_spec(limits, language, allow_list),
            skeleton=skeleton_files("jsonl", language, allow_list, title="optimisation solver"),
            probes=probes,
            reference=reference_files(REFERENCE_PROGRAM),
        )


__all__ = ["FAMILY_NAME", "OBJECTIVE_KIND", "OptLadderFamily", "make_case", "render_spec"]
