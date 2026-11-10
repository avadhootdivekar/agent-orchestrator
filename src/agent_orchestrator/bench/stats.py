"""Paired statistics for cross-subject comparison (HLD §6).

Pure functions over lists of floats -- no I/O, no scipy/numpy, no global RNG. Everything
stochastic (the bootstrap) takes an explicit ``seed`` and draws from a private
``random.Random(seed)``, so the same inputs always give the same CI (CLAUDE.md
determinism rule) and a report regenerated from the same run dirs is byte-stable.

What lives here:

  - ``bootstrap_ci_mean`` / ``paired_bootstrap_ci`` -- percentile bootstrap CI of a mean
    (resampling *tasks*, HLD §6 "paired design": the input is already a per-task
    difference, so resampling it is the paired bootstrap);
  - ``wilcoxon_signed_rank`` -- two-sided signed-rank test. Zero differences are dropped
    (Wilcoxon's own convention, ``scipy``'s default ``zero_method="wilcox"``); tied
    ``|d|`` get average ranks. The p-value is EXACT (the signs are iid fair under H0, so
    the statistic is a sum of ``rank_i * Bernoulli(1/2)`` -- an integer DP over the doubled
    ranks, valid with ties because it conditions on the observed ranks) for up to
    ``WILCOXON_EXACT_MAX_N`` non-zero differences, and a continuity-corrected normal
    approximation with the tie correction beyond that;
  - ``median``, ``mean``, ``sd``, ``summarize`` and ``format_ci`` helpers.

Empty input raises ``ValueError`` from every function that has no sensible answer for it
(callers -- ``results.py`` -- guard on ``n`` before calling); the one exception is
``wilcoxon_signed_rank``, where "no non-zero difference" is a legitimate, meaningful
outcome (``p == 1.0``: nothing to distinguish) and is returned rather than raised.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from typing import TypedDict

# Named constants (CLAUDE.md: no magic literals).
DEFAULT_N_BOOT = 2000
DEFAULT_ALPHA = 0.05
DEFAULT_SEED = 0
# Up to this many NON-ZERO differences the Wilcoxon p-value is exact; the DP is
# O(n * n(n+1)) integer ops, instant at 40 (sum of doubled ranks <= 3280).
WILCOXON_EXACT_MAX_N = 40


class Summary(TypedDict):
    """``summarize`` result. Every statistic but ``n`` is ``None`` for an empty sample."""

    n: int
    mean: float | None
    median: float | None
    sd: float | None
    lo: float | None
    hi: float | None


def _require_non_empty(values: Sequence[float], what: str) -> None:
    if len(values) == 0:
        raise ValueError(f"{what}: need at least one value")


def mean(values: Sequence[float]) -> float:
    """Arithmetic mean (``ValueError`` on an empty sequence)."""
    _require_non_empty(values, "mean")
    return math.fsum(values) / len(values)


def median(values: Sequence[float]) -> float:
    """Median; the mean of the two middle values for an even-length sample."""
    _require_non_empty(values, "median")
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def sd(values: Sequence[float]) -> float:
    """Sample standard deviation (``n - 1`` denominator); ``0.0`` for a single value."""
    _require_non_empty(values, "sd")
    if len(values) == 1:
        return 0.0
    m = mean(values)
    return math.sqrt(math.fsum((v - m) ** 2 for v in values) / (len(values) - 1))


def _quantile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolated quantile of an already-sorted, non-empty sequence."""
    pos = q * (len(sorted_values) - 1)
    lo_idx = math.floor(pos)
    hi_idx = math.ceil(pos)
    if lo_idx == hi_idx:
        return sorted_values[lo_idx]
    frac = pos - lo_idx
    return sorted_values[lo_idx] * (1.0 - frac) + sorted_values[hi_idx] * frac


def bootstrap_ci_mean(
    values: Sequence[float],
    n_boot: int = DEFAULT_N_BOOT,
    alpha: float = DEFAULT_ALPHA,
    seed: int = DEFAULT_SEED,
) -> tuple[float, float, float]:
    """Percentile-bootstrap CI of the mean: ``(mean, lo, hi)`` at level ``1 - alpha``.

    *n_boot* resamples (with replacement, same size as the input) are drawn from a
    private ``random.Random(seed)``. A one-element or constant sample gives a degenerate
    ``lo == hi == mean`` interval -- by construction, not an error.
    """
    _require_non_empty(values, "bootstrap_ci_mean")
    if n_boot < 1:
        raise ValueError(f"n_boot must be >= 1, got {n_boot}")
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")
    data = list(values)
    rng = random.Random(seed)
    n = len(data)
    boot_means = sorted(math.fsum(rng.choices(data, k=n)) / n for _ in range(n_boot))
    return (
        mean(data),
        _quantile(boot_means, alpha / 2.0),
        _quantile(boot_means, 1.0 - alpha / 2.0),
    )


def paired_bootstrap_ci(
    diffs: Sequence[float],
    n_boot: int = DEFAULT_N_BOOT,
    alpha: float = DEFAULT_ALPHA,
    seed: int = DEFAULT_SEED,
) -> tuple[float, float, float]:
    """Paired bootstrap CI over per-task differences ``a_i - b_i`` (HLD §6): resampling
    the *differences* resamples *tasks*, preserving the pairing. ``(mean, lo, hi)``."""
    return bootstrap_ci_mean(diffs, n_boot=n_boot, alpha=alpha, seed=seed)


def ci_excludes_zero(lo: float, hi: float) -> bool:
    """True iff the interval ``[lo, hi]`` lies strictly on one side of zero."""
    return lo > 0.0 or hi < 0.0


def _average_ranks(abs_values: Sequence[float]) -> list[float]:
    """1-based ranks of *abs_values*, tied values sharing the average of their ranks."""
    order = sorted(range(len(abs_values)), key=lambda i: abs_values[i])
    ranks = [0.0] * len(abs_values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and abs_values[order[j + 1]] == abs_values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _exact_lower_tail(doubled_ranks: Sequence[int], doubled_stat: int) -> float:
    """``P(W <= stat)`` under H0 where ``W = sum(rank_i * B_i)``, ``B_i ~ Bernoulli(1/2)``.

    Ranks are passed doubled (average ranks of ties are multiples of 0.5, so doubled
    they are integers) and the distribution is built by the standard subset-sum DP.
    """
    total = sum(doubled_ranks)
    counts = [0] * (total + 1)
    counts[0] = 1
    for r in doubled_ranks:
        for s in range(total, r - 1, -1):
            counts[s] += counts[s - r]
    return sum(counts[: doubled_stat + 1]) / float(2 ** len(doubled_ranks))


def wilcoxon_signed_rank(diffs: Sequence[float]) -> tuple[float, float]:
    """Two-sided Wilcoxon signed-rank test of ``median(diffs) == 0``.

    Returns ``(statistic, p_value)`` where ``statistic = min(W+, W-)`` (the sum of the
    ranks of the positive / negative differences). Zeros are dropped before ranking;
    with no non-zero difference left the result is ``(0.0, 1.0)``.
    """
    nonzero = [d for d in diffs if d != 0.0]
    n = len(nonzero)
    if n == 0:
        return 0.0, 1.0
    ranks = _average_ranks([abs(d) for d in nonzero])
    w_plus = math.fsum(r for r, d in zip(ranks, nonzero, strict=True) if d > 0)
    w_minus = math.fsum(r for r, d in zip(ranks, nonzero, strict=True) if d < 0)
    statistic = min(w_plus, w_minus)

    if n <= WILCOXON_EXACT_MAX_N:
        doubled = [int(round(2.0 * r)) for r in ranks]
        tail = _exact_lower_tail(doubled, int(round(2.0 * statistic)))
        return statistic, min(1.0, 2.0 * tail)

    # Normal approximation with the tie correction and a 0.5 continuity correction.
    mu = n * (n + 1) / 4.0
    tie_term = 0.0
    abs_sorted = sorted(abs(d) for d in nonzero)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs_sorted[j + 1] == abs_sorted[i]:
            j += 1
        t = j - i + 1
        tie_term += t**3 - t
        i = j + 1
    var = n * (n + 1) * (2 * n + 1) / 24.0 - tie_term / 48.0
    if var <= 0.0:
        return statistic, 1.0
    z = max(0.0, abs(w_plus - mu) - 0.5) / math.sqrt(var)
    return statistic, min(1.0, math.erfc(z / math.sqrt(2.0)))


def summarize(
    values: Sequence[float],
    n_boot: int = DEFAULT_N_BOOT,
    alpha: float = DEFAULT_ALPHA,
    seed: int = DEFAULT_SEED,
) -> Summary:
    """``{n, mean, median, sd, lo, hi}`` of a sample; ``lo``/``hi`` are the bootstrap CI
    of the mean. An empty sample gives ``n=0`` and ``None`` for the rest."""
    if len(values) == 0:
        return {"n": 0, "mean": None, "median": None, "sd": None, "lo": None, "hi": None}
    m, lo, hi = bootstrap_ci_mean(values, n_boot=n_boot, alpha=alpha, seed=seed)
    return {
        "n": len(values),
        "mean": m,
        "median": median(values),
        "sd": sd(values),
        "lo": lo,
        "hi": hi,
    }


def format_ci(mean_value: float, lo: float, hi: float, digits: int = 3) -> str:
    """``"+0.250 [0.100, 0.400]"`` -- signed mean followed by its CI, for the reports."""
    return f"{mean_value:+.{digits}f} [{lo:.{digits}f}, {hi:.{digits}f}]"


__all__ = [
    "DEFAULT_ALPHA",
    "DEFAULT_N_BOOT",
    "DEFAULT_SEED",
    "WILCOXON_EXACT_MAX_N",
    "Summary",
    "bootstrap_ci_mean",
    "ci_excludes_zero",
    "format_ci",
    "mean",
    "median",
    "paired_bootstrap_ci",
    "sd",
    "summarize",
    "wilcoxon_signed_rank",
]
