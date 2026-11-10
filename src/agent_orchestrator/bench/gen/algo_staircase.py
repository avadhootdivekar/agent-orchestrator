"""`algo-staircase` family (T-Os5Lad; design `docs-md/benchmark-open-scale-design.md` §3.3, §11.1).

The `algo-hard` problems on a geometric size ladder, graded by the `objective` grader in "rungs"
mode.

`algo-hard` saturated: both anchors answer every probe at n = 200k within 10 s. Here each problem is
asked at n = 1000 * 4^j (j = 0..4, up to 256k) under a per-request time limit that the difficulty
sets, and a problem's points are the number of consecutive rungs answered exactly in time. Correct
but slow code stops early; the points (and the time limit as the knob) measure how far an
implementation scales, not only whether it is right.

Problems, variants, the generated-case stream and the reference engine are those of `algo-hard`
(``algo_hard_server.py``, cross-checked against brute force at generation time). Cases are always
``{"gen": {...}}`` so a 256k input is one short request line; the expected answers are computed by
the reference at generation time and stored only in the grader-owned ``probes.json``.

Difficulty: ``entities`` -> problems (1-4); ``concurrency`` -> seeds per rung (1, 1, 2, 2);
``scale`` -> per-request seconds (10, 5, 2, 1); ``traps`` is unused (the variants are the traps).
"""

from __future__ import annotations

import dataclasses
import random
from collections.abc import Sequence

from .algo_hard import HARD
from .algo_puzzle import JsonDict, cross_check, new_engine, render_spec
from .base import Difficulty, Family, Instance, register_family
from .refs import reference_files
from .skeleton import resolve_allow, skeleton_files

FAMILY_NAME = "algo-staircase"
BASE_N = 1000
RUNG_FACTOR = 4
RUNGS = 5  # n = 1000 .. 256000 (the reference engine accepts up to 300k)
PROBLEMS_BY_LEVEL = (1, 2, 3, 4)
SEEDS_BY_LEVEL = (1, 1, 2, 2)
SECONDS_BY_LEVEL = (10, 5, 2, 1)
SOLVED_RUNGS = 3.0  # 64k on average
SPEC_SECONDS_SENTENCE = "Every request must be answered within 10 seconds."


def rung_sizes() -> list[int]:
    return [BASE_N * RUNG_FACTOR**j for j in range(RUNGS)]


def staircase_spec(limits: JsonDict, language: str, allow: Sequence[str]) -> str:
    sizes = rung_sizes()
    ps = dataclasses.replace(
        HARD,
        size_text=f"Inputs have up to {sizes[-1]:,} elements (grids up to 100,000 x 100,000).",
    )
    text = render_spec(limits, language, allow, ps)
    seconds = limits["case_seconds"]
    if SPEC_SECONDS_SENTENCE not in text:  # pragma: no cover -- guards a wording change upstream
        raise ValueError("algo-staircase: the algo-puzzle spec no longer states the time limit")
    text = text.replace(
        SPEC_SECONDS_SENTENCE,
        f"Every request must be answered within **{seconds} second{'s' if seconds != 1 else ''}**"
        " (measured by the grader); a late answer counts as a failure.",
    )
    return text.rstrip() + "\n\n" + "\n".join([
        "## 5. Scoring",
        "",
        "Every problem is asked at growing sizes: `n` = "
        + ", ".join(f"{n:,}" for n in sizes)
        + " (generated cases). A problem earns one point per size, from the smallest, as long as "
        "every case of that size is answered exactly and in time; the first size that fails ends "
        "that problem's run. Correct but slow code therefore stops early.",
        "",
    ])  # fmt: skip


@register_family
class AlgoStaircaseFamily(Family):
    name = FAMILY_NAME
    contract = "jsonl"
    grader_type = "objective"
    instruction_kind = "build"
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
        cross_check(random.Random(seed ^ 0x5EED), HARD)
        chosen = rng.sample(HARD.order, PROBLEMS_BY_LEVEL[difficulty.entities])
        problems = sorted(chosen, key=HARD.order.index)
        seconds = SECONDS_BY_LEVEL[difficulty.scale]
        limits: JsonDict = {
            "problems": problems,
            "variants": {p: rng.choice(HARD.variants[p]) for p in problems},
            "case_seconds": seconds,
            "rungs": rung_sizes(),
        }
        engine = new_engine(limits, HARD)
        cases = []
        for p in problems:
            for j, n in enumerate(rung_sizes()):
                for k in range(SEEDS_BY_LEVEL[difficulty.concurrency]):
                    case = {"gen": HARD.gen_params(rng, p, n)}
                    case_id = f"{p.replace('_', '-')}-r{j}-{k + 1}"
                    cases.append({"id": case_id, "problem": p, "rung": j, "case": case,
                                  "expect": engine.solve(p, case)})  # fmt: skip
        probes = {
            "version": "1.0",
            "kind": "objective",
            "aggregate": "rungs",
            "contract": "jsonl",
            "case_seconds": seconds,
            "par": float(RUNGS),
            "solved_points": SOLVED_RUNGS,
            "cases": cases,
        }
        return Instance(
            family=self.name,
            seed=seed,
            difficulty=difficulty,
            language=language,
            contract="jsonl",
            entities=[],
            traps=["variant_semantics"],
            limits=limits,
            allow=allow_list,
            spec_md=staircase_spec(limits, language, allow_list),
            skeleton=skeleton_files("jsonl", language, allow_list, title="algorithmic solver"),
            probes=probes,
            reference=reference_files(HARD.reference),
        )


__all__ = ["FAMILY_NAME", "AlgoStaircaseFamily", "rung_sizes", "staircase_spec"]
