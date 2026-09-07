"""Soft, deterministic overlap-aware co-scheduling preference (E-Wk9Tz3 FR-10, HLD §9.1).

This module is intentionally **pure** — no git, no filesystem, no clock, no imports from
`isolation/` (HLD §11 M8: "Dependencies. M2 only — deliberately: `rank_wave` must be
importable and testable without git"). It answers exactly one question: given a batch of
READY task ids and each task's advisory `touches` globs, which ordering of the first `n`
of them prefers disjoint (non-overlapping) work over overlapping work? `touches` is a
*hint*, never a gate — see `rank_wave`'s docstring for the load-bearing guarantee this
carries (AC-3 / R9: a slot is never withheld).

**Interface note on `rank_wave`'s signature (AC-1).** HLD §9.1's pseudocode sketches the
function as consulting a `preference` variable ("IF n <= 1 OR preference == 'off': RETURN
ready[:n]") but this ticket's own AC-1/AC-2/AC-3/AC-4/AC-5 all pin the call as exactly
``rank_wave(ready, touches, hotspots, n)`` — four positional arguments, no preference.
The resolution: `preference` is the CALLER's concern (`models.resolve_overlap_preference`,
owned by `T-Sc7Rm2` — AC-14 forbids a second copy of that rule here), not this function's.
A caller only invokes `rank_wave` when the resolved preference is `"soft"`; at `"off"` the
caller keeps doing exactly what it does today (`ready[:n]`), which is byte-identical to
what `rank_wave` would return anyway if it were invoked with empty `touches`/`hotspots` —
so nothing is lost by keeping the off-check outside this module. See this ticket's
STATUS.md "hook points" note for the exact call site `T-En8Hd4` wires this into.
"""

from __future__ import annotations

import fnmatch

# Baseline overlap weight for a colliding glob pair that touches no known hotspot (HLD
# §9.1: "1.0 baseline, higher for hotspots"). A hotspot's own weight (from
# `isolation/hotspots.py::compute_hotspots`) is always >= 1.0, so a plain collision never
# outranks a hotspot collision at equal overlap counts (AC-8).
BASELINE_OVERLAP_WEIGHT: float = 1.0

# Characters that make a glob pattern non-literal (Python `fnmatch` wildcard syntax).
_WILDCARD_CHARS = ("*", "?", "[")


def _has_wildcard(pattern: str) -> bool:
    return any(ch in pattern for ch in _WILDCARD_CHARS)


def _wildcard_prefix(pattern: str) -> str:
    """The non-wildcard prefix of *pattern* — everything before its first wildcard char."""
    cut = len(pattern)
    for ch in _WILDCARD_CHARS:
        idx = pattern.find(ch)
        if idx != -1:
            cut = min(cut, idx)
    return pattern[:cut]


def _globs_intersect(a: str, b: str) -> bool:
    """Syntactic, filesystem-free glob intersection (HLD §9.1 / AC-7).

    Two patterns "intersect" when one matches the other as a literal (`fnmatch`, exact
    case), or — when BOTH carry a wildcard — when their non-wildcard prefixes are a
    prefix of one another. Never touches the filesystem, so this is pure and O(1) per
    pair.
    """
    if a == b:
        return True
    a_wild, b_wild = _has_wildcard(a), _has_wildcard(b)
    if not a_wild and not b_wild:
        return False  # distinct literals never intersect (equality already checked above)
    if not a_wild:
        return fnmatch.fnmatchcase(a, b)
    if not b_wild:
        return fnmatch.fnmatchcase(b, a)
    prefix_a, prefix_b = _wildcard_prefix(a), _wildcard_prefix(b)
    return prefix_a.startswith(prefix_b) or prefix_b.startswith(prefix_a)


def _more_specific(a: str, b: str) -> str:
    """Pick the more literal/specific of two intersecting globs, for hotspot lookup.

    A hotspot entry (`isolation/hotspots.py::Hotspots`) is always keyed by a literal file
    path pulled from `git log`, never a glob — so when scoring a collision we want the
    most concrete of the two `touches` patterns to check against it. Deterministic tie
    -break: `a` wins on an exact tie (matches `rank_wave`'s own "first in `ready` order
    wins" convention).
    """
    a_wild, b_wild = _has_wildcard(a), _has_wildcard(b)
    if not a_wild:
        return a
    if not b_wild:
        return b
    return a if len(_wildcard_prefix(a)) >= len(_wildcard_prefix(b)) else b


def glob_intersection(mine: list[str], theirs: list[str]) -> list[str]:
    """Every pairwise intersection between *mine* and *theirs* (HLD §9.1), one entry per
    colliding pair, represented by the more specific (more literal) of the two patterns
    (see `_more_specific`) so callers can look it up against a literal-path hotspot map.

    Pure and syntactic only — never touches the filesystem. Order is deterministic:
    outer loop over `mine`, inner over `theirs`, both in their given order.
    """
    hits: list[str] = []
    for g1 in mine:
        for g2 in theirs:
            if _globs_intersect(g1, g2):
                hits.append(_more_specific(g1, g2))
    return hits


def _hotspot_weight(glob: str, hotspots: dict[str, float]) -> float:
    """Weight of a colliding *glob* against the *hotspots* path->weight map (HLD §9.1).

    Exact match first (the common case — `glob` came from a literal `touches` entry or is
    `_more_specific`'s pick of one). Falls back to the highest weight among hotspot paths
    the (possibly wildcarded) `glob` matches, so a wildcard collision still benefits from
    a hotspot hit inside its scope. `BASELINE_OVERLAP_WEIGHT` when nothing matches — a
    plain collision is never scored below baseline (AC-8).
    """
    if glob in hotspots:
        return hotspots[glob]
    if _has_wildcard(glob):
        matched = [w for path, w in hotspots.items() if fnmatch.fnmatchcase(path, glob)]
        if matched:
            return max(matched)
    return BASELINE_OVERLAP_WEIGHT


def overlap_score(
    tid: str,
    selected: list[str],
    touches: dict[str, list[str]],
    hotspots: dict[str, float],
) -> float:
    """Sum of hotspot-weighted glob collisions between task *tid* and every task already
    in *selected* (HLD §9.1). A task with no `touches` hint scores `0.0` against
    everything — "no hint == no opinion, never a penalty" (FR-10, AC-6) — since an absent
    hint carries no information, not evidence of safety or danger.
    """
    mine = touches.get(tid) or []
    if not mine:
        return 0.0
    score = 0.0
    for other in selected:
        theirs = touches.get(other) or []
        for glob in glob_intersection(mine, theirs):
            score += _hotspot_weight(glob, hotspots)
    return score


def rank_wave(
    ready: list[str],
    touches: dict[str, list[str]],
    hotspots: dict[str, float],
    n: int,
) -> list[str]:
    """Reorder the first `n` slots of *ready* to prefer disjoint (non-overlapping) work
    (HLD §9.1). Pure, deterministic, and — this is the load-bearing property of the whole
    ticket — **never a gate**: every task in `ready` remains eligible, none is ever
    dropped.

    Algorithm (verbatim HLD §9.1, `preference` handling factored out — see module
    docstring):
      1. `n <= 1` is a provable no-op: returns `ready[:n]` unchanged (AC-2, ADR-0007
         D1/D6, NFR-2 — preserves today's exact serial-scheduling behavior).
      2. Pass 1 (greedy): walk `ready` in its given order, admitting any task whose
         overlap score against what's already selected is exactly `0.0`, until `n` slots
         fill or `ready` is exhausted. This preserves `ready`'s original relative order
         among the tasks it admits (AC-4: the first task in `ready` is never displaced).
      3. Pass 2 (fill): if slots remain, every REMAINING candidate is admitted regardless
         of overlap — sorted by `(overlap_score, ready.index(t))` so the least-overlapping
         (then earliest-in-`ready`) candidates fill first, but every one of them is
         eventually admitted (AC-3: a slot is never withheld, even when every candidate
         overlaps every other).

    Determinism: same `(ready, touches, hotspots, n)` -> same output, always. Every sort
    key ends in `ready.index(t)`, so no two tasks can tie (AC-5).

    Returns a list with the exact same length as `ready[:n]` would have (never fewer,
    never more) and the exact same *set* of ids as `ready` when `n >= len(ready)`.
    """
    if n <= 1:
        return ready[:n]

    selected: list[str] = []
    for tid in ready:
        if len(selected) >= n:
            break
        if overlap_score(tid, selected, touches, hotspots) == 0.0:
            selected.append(tid)

    if len(selected) < n:
        selected_set = set(selected)
        rest = [t for t in ready if t not in selected_set]
        rest.sort(key=lambda t: (overlap_score(t, selected, touches, hotspots), ready.index(t)))
        for tid in rest:
            if len(selected) >= n:
                break
            selected.append(tid)  # ALWAYS admitted here -- soft preference, never a gate

    return selected
