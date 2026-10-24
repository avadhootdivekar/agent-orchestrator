"""`algo-hard` family (T-Pf4Mix): story-form algorithmic problems that need a non-obvious technique.

Same protocol, probes and generated-case stream as `algo-puzzle` (see `algo_puzzle.py`); the
problems differ. SPEC.md states each one as a story and never names the method, and the large
hidden cases rule out the direct approach:

- `smoothing` -- min-cost monotone fit under |.| cost (slope trick; the direct DP is O(n * values));
- `substrings` -- distinct (or repeated) substrings of a 200k-letter text (suffix automaton/array);
- `grid_paths` -- monotone walks around blocked cells on a 10^5 x 10^5 grid (combinatorics over the
  blocked cells; the grid DP is 10^10 cells);
- `window_medians` -- sum of sliding-window medians (order statistics over a window);
- `lis_count` -- the number of longest increasing selections, mod 1e9+7 (Fenwick over ranks).

Reference: ``reference/algo_hard_server.py``, cross-checked against the brute force below at
generation time.
"""

from __future__ import annotations

import itertools
import random
from collections.abc import Callable

from .algo_puzzle import AlgoPuzzleFamily, JsonDict, ProblemSet
from .base import register_family

FAMILY_NAME = "algo-hard"
REFERENCE_PROGRAM = "algo_hard_server.py"
MOD = 1_000_000_007
GRID_BLOCKED_MAX = 1000
VARIANTS: dict[str, tuple[str, ...]] = {
    "smoothing": ("non_decreasing", "strict"),
    "substrings": ("all", "repeated"),
    "grid_paths": ("count",),
    "window_medians": ("lower", "upper"),
    "lis_count": ("strict", "non_decreasing"),
}
PROBLEMS_BY_LEVEL = (2, 3, 4, 5)


# -- brute force ------------------------------------------------------------------------------


def brute_smoothing(c: JsonDict, variant: str) -> int:
    a = list(c["heights"])
    if variant == "strict":
        a = [x - i for i, x in enumerate(a)]
    values = sorted(set(a))  # an optimal L1 monotone fit only uses input values
    best = [abs(a[0] - v) for v in values]
    for x in a[1:]:
        run, row = float("inf"), []
        for j, v in enumerate(values):
            run = min(run, best[j])
            row.append(int(run) + abs(x - v))
        best = row
    return min(best)


def brute_substrings(c: JsonDict, variant: str) -> int:
    t = c["text"]
    seen: dict[str, int] = {}
    for i in range(len(t)):
        for j in range(i + 1, len(t) + 1):
            seen[t[i:j]] = seen.get(t[i:j], 0) + 1
    return len(seen) if variant == "all" else sum(1 for n in seen.values() if n >= 2)


def brute_grid_paths(c: JsonDict, _variant: str) -> int:
    rows, cols = c["rows"], c["cols"]
    blocked = {tuple(b) for b in c["blocked"]}
    ways = [[0] * cols for _ in range(rows)]
    for r, col in itertools.product(range(rows), range(cols)):
        if (r, col) in blocked:
            continue
        if r == col == 0:
            ways[r][col] = 1
        else:
            ways[r][col] = ((ways[r - 1][col] if r else 0) + (ways[r][col - 1] if col else 0)) % MOD
    return ways[rows - 1][cols - 1]


def brute_window_medians(c: JsonDict, variant: str) -> int:
    a, w = c["values"], c["w"]
    pos = (w + 1) // 2 - 1 if variant == "lower" else w // 2
    return sum(sorted(a[i : i + w])[pos] for i in range(len(a) - w + 1)) if w <= len(a) else 0


def brute_lis_count(c: JsonDict, variant: str) -> int:
    a = c["values"]
    length, count = [1] * len(a), [1] * len(a)
    for j in range(len(a)):
        for i in range(j):
            if a[i] < a[j] or (variant == "non_decreasing" and a[i] == a[j]):
                if length[i] + 1 > length[j]:
                    length[j], count[j] = length[i] + 1, count[i]
                elif length[i] + 1 == length[j]:
                    count[j] += count[i]
    top = max(length)
    return sum(n for n, ln in zip(count, length, strict=True) if ln == top) % MOD


BRUTE: dict[str, Callable[[JsonDict, str], int]] = {
    "smoothing": brute_smoothing,
    "substrings": brute_substrings,
    "grid_paths": brute_grid_paths,
    "window_medians": brute_window_medians,
    "lis_count": brute_lis_count,
}


# -- cases ------------------------------------------------------------------------------------


def small_case(rng: random.Random, problem: str) -> JsonDict:
    if problem == "smoothing":
        return {"heights": [rng.randint(0, 8) for _ in range(rng.randint(1, 10))]}
    if problem == "substrings":
        k = rng.randint(1, 3)
        return {"text": "".join(rng.choice("abc"[:k]) for _ in range(rng.randint(1, 12)))}
    if problem == "grid_paths":
        rows, cols = rng.randint(1, 6), rng.randint(1, 6)
        blocked = [[rng.randrange(rows), rng.randrange(cols)] for _ in range(rng.randint(0, 6))]
        return {"rows": rows, "cols": cols, "blocked": blocked}
    if problem == "window_medians":
        a = [rng.randint(0, 9) for _ in range(rng.randint(1, 12))]
        return {"values": a, "w": rng.randint(1, len(a) + 1)}
    return {"values": [rng.randint(0, 5) for _ in range(rng.randint(1, 12))]}


def gen_params(rng: random.Random, problem: str, n: int) -> JsonDict:
    seed = rng.randrange(2**32)
    if problem == "smoothing":
        return {"seed": seed, "n": n, "max": rng.choice((1000, 10**9))}
    if problem == "substrings":
        return {"seed": seed, "n": n, "letters": rng.choice((2, 4, 26))}
    if problem == "grid_paths":
        side = min(10**5, max(2, n // 2))
        blocked = min(GRID_BLOCKED_MAX, max(2, n // 100))
        return {"seed": seed, "rows": side, "cols": side - rng.randint(0, side // 2),
                "blocked": blocked}  # fmt: skip
    if problem == "window_medians":
        return {"seed": seed, "n": n, "max": rng.choice((100, 10**9)),
                "w": rng.randint(max(1, n // 10), max(1, n // 2))}  # fmt: skip
    return {"seed": seed, "n": n, "max": rng.choice((n // 10 + 1, 10**9))}


# -- SPEC.md text -----------------------------------------------------------------------------

_SMOOTH = (
    "A road has `heights` measured at consecutive posts. Each post may be raised or lowered; "
    "changing a post's height by 1 costs 1. The answer is the minimum total cost to make the "
    "heights "
)
_SUBS = (
    "`text` is a message of lowercase letters. The answer is how many different non-empty "
    "strings appear in it as a contiguous piece"
)
_MED = (
    "`values` are consecutive sensor readings. For every window of `w` consecutive readings, take "
    "the window sorted ascending and its element at 1-based position "
)
_LIS = (
    "A *selection* is a choice of positions `i1 < i2 < ... < ik` of `values` (any `k >= 1`). The "
    "answer is the number of selections of the greatest possible length `k` whose values "
)
STATEMENT = {
    ("smoothing", "non_decreasing"): _SMOOTH + "**never decrease** from one post to the next "
    "(equal neighbours allowed). New heights may be any integers.",
    ("smoothing", "strict"): _SMOOTH + "**strictly increase** from one post to the next. New "
    "heights may be any integers, including negative ones.",
    ("substrings", "all"): _SUBS + ".",
    ("substrings", "repeated"): _SUBS + " **at least twice** (the occurrences may overlap).",
    ("grid_paths", "count"): "A robot walks on a grid of `rows` x `cols` cells from the top-left "
    "cell `(0, 0)` to the bottom-right cell `(rows - 1, cols - 1)`; each step moves one cell down "
    "(row + 1) or right (col + 1). It may never enter a `blocked` cell `[row, col]` (the list may "
    "repeat a cell). The answer is the number of different walks modulo 1000000007 (0 if a corner "
    "is blocked).",
    ("window_medians", "lower"): _MED + "`ceil(w / 2)` (for even `w`, the lower of the two middle "
    "readings). The answer is the sum of these over all windows (0 if `w` exceeds the number of "
    "readings).",
    ("window_medians", "upper"): _MED + "`floor(w / 2) + 1` (for even `w`, the upper of the two "
    "middle readings). The answer is the sum of these over all windows (0 if `w` exceeds the "
    "number of readings).",
    ("lis_count", "strict"): _LIS + "**strictly increase**, modulo 1000000007 (selections that "
    "differ in any position are different, even if their values are equal).",
    ("lis_count", "non_decreasing"): _LIS + "**never decrease** (equal values allowed), modulo "
    "1000000007 (selections that differ in any position are different, even if their values are "
    "equal).",
}
EXPLICIT = {
    "smoothing": '`{"heights": [int, ...]}` (at least one)',
    "substrings": '`{"text": "<lowercase letters>"}` (non-empty)',
    "grid_paths": '`{"rows": int, "cols": int, "blocked": [[row, col], ...]}` with '
    "`1 <= rows, cols <= 100000`, at most 3000 blocked cells, all inside the grid",
    "window_medians": '`{"values": [int, ...], "w": int}` with `w >= 1`',
    "lis_count": '`{"values": [int, ...]}` (at least one)',
}
GEN_TEXT = {
    "smoothing": '`{"seed", "n", "max"}` → `heights` = `n` draws `draw(0, max)`.',
    "substrings": '`{"seed", "n", "letters"}` → `text` = `n` letters, each the letter number '
    "`draw(0, letters - 1)` of the alphabet (`0` = `a`).",
    "grid_paths": '`{"seed", "rows", "cols", "blocked"}` → `blocked` cells, each from two draws in '
    "this order: `row = draw(0, rows - 1)`, `col = draw(0, cols - 1)`; `rows`, `cols` as given.",
    "window_medians": '`{"seed", "n", "max", "w"}` → `values` = `n` draws `draw(0, max)`; `w` as '
    "given.",
    "lis_count": '`{"seed", "n", "max"}` → `values` = `n` draws `draw(0, max)`.',
}

HARD = ProblemSet(
    reference=REFERENCE_PROGRAM,
    variants=VARIANTS,
    brute=BRUTE,
    small_case=small_case,
    gen_params=gen_params,
    statement=STATEMENT,
    explicit=EXPLICIT,
    gen_text=GEN_TEXT,
    size_text="Sequences and texts have up to 200,000 elements; grids are up to 100,000 x 100,000.",
    problems_by_level=PROBLEMS_BY_LEVEL,
    single_variant_trap="blocked_cells",
)


@register_family
class AlgoHardFamily(AlgoPuzzleFamily):
    name = FAMILY_NAME
    problem_set = HARD


__all__ = ["FAMILY_NAME", "HARD", "AlgoHardFamily"]
