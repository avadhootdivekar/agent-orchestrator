"""`cvrp-ladder` family (T-Ga4Rna follow-up; design `docs-md/benchmark-cvrp-ladder-design.md`):
capacitated vehicle routing, graded by the `objective` grader against a ladder of reference
solvers (trivial, nearest-neighbour, savings, local search, SISR annealing).

The subject writes a solver that answers ``solve`` requests for hidden CVRP instances within a
per-case time limit. Every feasible answer is accepted and scored by route length on the unbounded
ladder scale (`opt-ladder`'s `ladder_points`: linear between rungs, one point per 1% of the top
value above the top rung). CVRP is NP-hard with no known optimum at these sizes, so no solver can
reach a ceiling; what separates solvers is the algorithm and how well it uses the time limit.

Difficulty axes: ``entities`` unused; ``concurrency`` -> hidden cases (1, 2, 3, 4); ``scale`` ->
customers per case (100, 200, 400, 800); ``traps`` -> degenerate inputs (duplicate locations, a
customer whose demand fills a whole vehicle).
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from functools import cache
from types import ModuleType
from typing import Any

from .algo_puzzle import load_reference
from .base import Difficulty, Family, Instance, register_family
from .opt_ladder import MIN_REL_GAP, strict_ladder
from .refs import reference_files
from .skeleton import allow_list_text, resolve_allow, run_contract_text, skeleton_files

FAMILY_NAME = "cvrp-ladder"
REFERENCE_PROGRAM = "cvrp_server.py"
OBJECTIVE_KIND = "objective"
CASES_BY_LEVEL = (1, 2, 3, 4)
SIZES = (100, 200, 400, 800)
CASE_SECONDS = 10
DEFAULT_SOLVED_POINTS = 2.0  # savings quality on average
COORD_MAX = 1000
DEMAND_MAX = 30
ROUTE_LENGTHS = (6, 10, 16, 24)  # mean customers per vehicle
MAX_DRAWS = 6
JsonDict = dict[str, Any]


@cache
def reference() -> ModuleType:
    return load_reference(REFERENCE_PROGRAM)


def _location(rng: random.Random, centres: list[list[int]], clustered: bool) -> list[int]:
    if clustered:
        cx, cy = centres[rng.randrange(len(centres))]
        spread = COORD_MAX // 14
        return [
            min(COORD_MAX, max(0, cx + rng.randint(-spread, spread))),
            min(COORD_MAX, max(0, cy + rng.randint(-spread, spread))),
        ]
    return [rng.randint(0, COORD_MAX), rng.randint(0, COORD_MAX)]


def gen_case(rng: random.Random, n: int, traps: int) -> JsonDict:
    layout = rng.choice(("uniform", "clustered", "mixed"))
    centres = [
        [rng.randint(0, COORD_MAX), rng.randint(0, COORD_MAX)] for _ in range(rng.randint(3, 8))
    ]
    depot = {
        "centre": [COORD_MAX // 2, COORD_MAX // 2],
        "corner": [0, 0],
        "random": [rng.randint(0, COORD_MAX), rng.randint(0, COORD_MAX)],
    }[rng.choice(("centre", "corner", "random"))]
    customers = []
    for i in range(n):
        clustered = layout == "clustered" or (layout == "mixed" and i % 2 == 1)
        x, y = _location(rng, centres, clustered)
        customers.append([x, y, rng.randint(1, DEMAND_MAX)])
    mean_len = rng.choice(ROUTE_LENGTHS)
    capacity = max(DEMAND_MAX, sum(c[2] for c in customers) * mean_len // n)
    if traps:  # duplicate locations, and a customer that fills a whole vehicle on its own
        for i in rng.sample(range(n), max(2, n // 50)):
            customers[i][0], customers[i][1] = customers[rng.randrange(n)][:2]
        customers[rng.randrange(n)][2] = capacity
    return {"depot": depot, "capacity": capacity, "customers": customers}


def make_case(rng: random.Random, n: int, traps: int) -> tuple[JsonDict, list[float]]:
    """Draw a case whose reference ladder is strictly improving (re-draw otherwise)."""
    for _ in range(MAX_DRAWS):
        case = gen_case(rng, n, traps)
        values = reference().ladder_values("cvrp", case)
        if strict_ladder(values):
            return case, values
    raise ValueError(f"cvrp-ladder: no strict ladder for n={n} in {MAX_DRAWS} draws")


def render_spec(limits: JsonDict, language: str, allow: Sequence[str]) -> str:
    rungs = reference().RUNGS
    parts = [
        "# Vehicle routing solver",
        "",
        "Build a program that answers capacitated vehicle routing requests over stdin/stdout. "
        "Every feasible answer is accepted; **shorter routes score more**. There is no single "
        "right answer, and the problem is hard: nobody knows the optimum for the sizes asked.",
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
        '- `{"op": "solve", "problem": "cvrp", "case": <case>}` → '
        '`{"ok": true, "answer": <routes>}`.',
        "- Requests arrive one at a time to the same process; answer each before the next arrives.",
        f"- **Time limit:** each `solve` must be answered within **{limits['case_seconds']} "
        "seconds** of the request (measured by the grader). A late, missing or infeasible answer "
        "scores 0 for that case. Use the time: answers that keep improving until shortly before "
        "the limit score better.",
        f"- **Sizes:** up to {limits['size']:,} customers per case.",
        "",
        "## 4. The problem",
        "",
        '`case = {"depot": [x, y], "capacity": Q, "customers": [[x, y, demand], ...]}` '
        "(integer coordinates and demands; several customers may share a location; every demand "
        "is at most `Q`). **Answer:** an array of routes; each route is an array of customer "
        "indices `0..n-1` in visiting order (a vehicle leaves the depot, visits them in that "
        "order and returns). Every customer must appear in exactly one route, and the demands "
        "on a route must sum to at most `Q`. The number of routes is free. **Objective "
        "(minimise):** the total length of all routes, using Euclidean distances "
        "`sqrt(dx^2 + dy^2)` including the legs from and to the depot.",
        "",
        "## 5. Scoring",
        "",
        "Each hidden case is solved by a ladder of reference solvers of increasing strength "
        f"({', '.join(f'rung {i} {r}' for i, r in enumerate(rungs))}). An answer earns points by "
        "where its total length falls on that ladder, linearly between rungs: matching rung `k` "
        f"earns `k` points, and improving on the strongest rung earns more than {len(rungs) - 1} "
        "(no upper limit). Infeasible, malformed or late answers earn 0. The task's points are the "
        "mean over its cases.",
        "",
    ]
    return "\n".join(parts).rstrip() + "\n"


@register_family
class CvrpLadderFamily(Family):
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
        size = SIZES[difficulty.scale]
        top = reference().TOP_RUNG
        limits: JsonDict = {
            "problems": ["cvrp"],
            "size": size,
            "case_seconds": CASE_SECONDS,
            "par": float(top),
            "solved_points": DEFAULT_SOLVED_POINTS,
        }
        cases = []
        for j in range(CASES_BY_LEVEL[difficulty.concurrency]):
            case, values = make_case(rng, size, difficulty.traps)
            cases.append(
                {
                    "id": f"cvrp-{j + 1}",
                    "problem": "cvrp",
                    "sense": "min",
                    "case": case,
                    "ladder": values,
                }  # fmt: skip
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
            traps=["vehicle_capacity"] + (["cvrp_degenerate"] if difficulty.traps else []),
            limits=limits,
            allow=allow_list,
            spec_md=render_spec(limits, language, allow_list),
            skeleton=skeleton_files("jsonl", language, allow_list, title="vehicle routing solver"),
            probes=probes,
            reference=reference_files(REFERENCE_PROGRAM),
        )


__all__ = ["CvrpLadderFamily", "FAMILY_NAME", "MIN_REL_GAP", "make_case", "render_spec"]
