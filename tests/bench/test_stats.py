"""Unit tests for bench/stats.py (HLD §6) -- known-answer, determinism and edge cases.

Pure functions, no I/O: every assertion is either an exact known answer (the exact
Wilcoxon p-values below are hand-checkable from the 2^n sign patterns) or a seeded,
therefore replayable, bootstrap property.
"""

from __future__ import annotations

import math

import pytest

from agent_orchestrator.bench import stats

# ---------------------------------------------------------------------------
# mean / median / sd / summarize
# ---------------------------------------------------------------------------


def test_median_odd_even_and_unsorted() -> None:
    assert stats.median([3.0]) == 3.0
    assert stats.median([5.0, 1.0, 3.0]) == 3.0
    assert stats.median([4.0, 1.0, 3.0, 2.0]) == 2.5


def test_mean_and_sd_known_values() -> None:
    assert stats.mean([1.0, 2.0, 3.0, 4.0]) == 2.5
    # sample sd of 2,4,4,4,5,5,7,9 is sqrt(32/7)
    assert stats.sd([2, 4, 4, 4, 5, 5, 7, 9]) == pytest.approx(math.sqrt(32 / 7))
    assert stats.sd([7.0]) == 0.0


@pytest.mark.parametrize("fn", [stats.mean, stats.median, stats.sd, stats.bootstrap_ci_mean])
def test_empty_input_raises_value_error(fn: object) -> None:
    with pytest.raises(ValueError, match="at least one"):
        fn([])  # type: ignore[operator]


def test_summarize_keys_and_values() -> None:
    s = stats.summarize([1.0, 2.0, 3.0, 4.0, 5.0], n_boot=500, seed=1)
    assert set(s) == {"n", "mean", "median", "sd", "lo", "hi"}
    assert s["n"] == 5
    assert s["mean"] == 3.0
    assert s["median"] == 3.0
    assert s["sd"] == pytest.approx(math.sqrt(2.5))
    assert s["lo"] is not None and s["hi"] is not None
    assert s["lo"] <= 3.0 <= s["hi"]
    assert 1.0 <= s["lo"] and s["hi"] <= 5.0


def test_summarize_empty_sample_is_all_none_with_n_zero() -> None:
    assert stats.summarize([]) == {
        "n": 0,
        "mean": None,
        "median": None,
        "sd": None,
        "lo": None,
        "hi": None,
    }


# ---------------------------------------------------------------------------
# bootstrap CIs
# ---------------------------------------------------------------------------


def test_paired_bootstrap_all_positive_excludes_zero() -> None:
    m, lo, hi = stats.paired_bootstrap_ci([0.2, 0.4, 0.3, 0.5, 0.25, 0.35])
    assert m == pytest.approx(0.333333, abs=1e-5)
    assert lo > 0.0
    assert lo <= m <= hi
    assert stats.ci_excludes_zero(lo, hi)


def test_paired_bootstrap_symmetric_straddles_zero() -> None:
    m, lo, hi = stats.paired_bootstrap_ci([-0.5, -0.25, 0.0, 0.25, 0.5, -0.4, 0.4])
    assert m == pytest.approx(0.0, abs=1e-9)
    assert lo < 0.0 < hi
    assert not stats.ci_excludes_zero(lo, hi)


def test_bootstrap_is_deterministic_by_seed_and_varies_across_seeds() -> None:
    data = [0.1, 0.9, 0.4, 0.4, 0.7, 0.0, 1.0, 0.3]
    a = stats.paired_bootstrap_ci(data, seed=7)
    b = stats.paired_bootstrap_ci(data, seed=7)
    assert a == b  # bit-identical
    c = stats.paired_bootstrap_ci(data, seed=8)
    assert c[0] == a[0]  # the point estimate never depends on the seed
    assert (c[1], c[2]) != (a[1], a[2])


def test_bootstrap_does_not_touch_global_random_state() -> None:
    import random

    random.seed(1234)
    expected = random.random()
    random.seed(1234)
    stats.paired_bootstrap_ci([0.1, 0.2, 0.3], seed=5)
    assert random.random() == expected


def test_bootstrap_constant_and_single_value_are_degenerate_intervals() -> None:
    assert stats.bootstrap_ci_mean([0.5, 0.5, 0.5]) == (0.5, 0.5, 0.5)
    assert stats.bootstrap_ci_mean([0.25]) == (0.25, 0.25, 0.25)


def test_bootstrap_ci_narrows_with_more_data() -> None:
    small = stats.bootstrap_ci_mean([0.0, 1.0] * 3)
    big = stats.bootstrap_ci_mean([0.0, 1.0] * 50)
    assert (big[2] - big[1]) < (small[2] - small[1])


def test_bootstrap_alpha_widens_interval() -> None:
    data = [0.0, 1.0, 0.5, 0.2, 0.9, 0.4]
    _, lo95, hi95 = stats.bootstrap_ci_mean(data, alpha=0.05)
    _, lo50, hi50 = stats.bootstrap_ci_mean(data, alpha=0.5)
    assert lo95 <= lo50 and hi50 <= hi95


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [({"n_boot": 0}, "n_boot"), ({"alpha": 0.0}, "alpha"), ({"alpha": 1.0}, "alpha")],
)
def test_bootstrap_rejects_bad_parameters(kwargs: dict[str, float], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        stats.bootstrap_ci_mean([1.0, 2.0], **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Wilcoxon signed-rank
# ---------------------------------------------------------------------------


def test_wilcoxon_all_positive_exact_p() -> None:
    # 6 positive diffs: W- = 0, exact two-sided p = 2 * (1/2^6) = 1/32.
    stat, p = stats.wilcoxon_signed_rank([1, 2, 3, 4, 5, 6])
    assert stat == 0.0
    assert p == pytest.approx(2 / 64)
    assert p < 0.05


def test_wilcoxon_symmetric_p_is_one() -> None:
    # |d| ties pairwise -> ranks 1.5,1.5,3.5,3.5,5.5,5.5 -> W+ == W- == 10.5.
    stat, p = stats.wilcoxon_signed_rank([-3, -2, -1, 1, 2, 3])
    assert stat == 10.5
    assert p == pytest.approx(1.0)


def test_wilcoxon_known_small_cases() -> None:
    # diffs 1, 2, -3: ranks 1,2,3; W+ = W- = 3 -> P(W <= 3) = 5/8, doubled > 1 -> p = 1.
    assert stats.wilcoxon_signed_rank([1, 2, -3]) == (3.0, 1.0)
    # diffs 1,2,3,-4: W- = 4; subset sums <= 4 of {1,2,3,4}: 7 of 16 patterns -> p = 14/16.
    stat, p = stats.wilcoxon_signed_rank([1, 2, 3, -4])
    assert stat == 4.0 and p == pytest.approx(14 / 16)
    # diffs 1,2,3,4,-5: W- = 5; subset sums <= 5 of {1..5}: 10 of 32 patterns -> p = 20/32.
    stat, p = stats.wilcoxon_signed_rank([1, 2, 3, 4, -5])
    assert stat == 5.0 and p == pytest.approx(20 / 32)


def _brute_force_p(diffs: list[float]) -> float:
    """Reference two-sided exact p by enumerating all 2^n sign patterns over the
    observed (tie-averaged) ranks -- independent of the DP in stats.py."""
    import itertools

    nonzero = [d for d in diffs if d != 0]
    abs_sorted = sorted(abs(d) for d in nonzero)
    ranks = []
    for d in nonzero:
        first = abs_sorted.index(abs(d))
        count = abs_sorted.count(abs(d))
        ranks.append(first + (count + 1) / 2.0)
    total = sum(ranks)
    w_plus = sum(r for r, d in zip(ranks, nonzero, strict=True) if d > 0)
    stat = min(w_plus, total - w_plus)
    hits = 0
    for signs in itertools.product([0, 1], repeat=len(ranks)):
        w = sum(r for r, s in zip(ranks, signs, strict=True) if s)
        if w <= stat + 1e-9:
            hits += 1
    return min(1.0, 2.0 * hits / 2 ** len(ranks))


@pytest.mark.parametrize(
    "data",
    [
        [0.5, -0.1, 0.9, 0.3, -0.7, 0.2, 0.2],
        [1.0, 1.0, -1.0, 0.5, 0.5, -0.5, 0.0, 1.0],
        [0.25, 0.25, 0.25, -0.25, 0.5, 0.75, -0.125],
        [3, 1, 4, 1, 5, -9, 2, 6, -5],
    ],
)
def test_wilcoxon_exact_matches_brute_force_including_ties(data: list[float]) -> None:
    assert stats.wilcoxon_signed_rank(data)[1] == pytest.approx(_brute_force_p(data))


def test_wilcoxon_zeros_are_dropped() -> None:
    with_zeros = stats.wilcoxon_signed_rank([0, 0, 1, 2, 3, 4, 5, 6, 0])
    without = stats.wilcoxon_signed_rank([1, 2, 3, 4, 5, 6])
    assert with_zeros == without


def test_wilcoxon_all_zero_or_empty_is_nothing_to_distinguish() -> None:
    assert stats.wilcoxon_signed_rank([0.0, 0.0, 0.0]) == (0.0, 1.0)
    assert stats.wilcoxon_signed_rank([]) == (0.0, 1.0)


def test_wilcoxon_ties_use_average_ranks() -> None:
    # |d| = 1,1,1,1 -> all rank 2.5; three +, one - -> W- = 2.5, W+ = 7.5.
    # Patterns of the 4 equal ranks: p = 2 * P(#neg <= 1) = 2 * 5/16 = 0.625.
    stat, p = stats.wilcoxon_signed_rank([1, 1, 1, -1])
    assert stat == 2.5
    assert p == pytest.approx(0.625)


def test_wilcoxon_is_sign_symmetric() -> None:
    data = [0.5, -0.1, 0.9, 0.3, -0.7, 0.2, 0.2]
    assert stats.wilcoxon_signed_rank(data) == stats.wilcoxon_signed_rank([-d for d in data])


def test_wilcoxon_normal_branch_all_positive_small_p_and_symmetric_large_p() -> None:
    n = stats.WILCOXON_EXACT_MAX_N + 10
    _, p_pos = stats.wilcoxon_signed_rank([float(i) for i in range(1, n + 1)])
    assert p_pos < 1e-6
    sym = [float(i) * (1 if i % 2 else -1) for i in range(1, n + 1)]
    _, p_sym = stats.wilcoxon_signed_rank(sym)
    assert p_sym > 0.5
    assert 0.0 <= p_sym <= 1.0


def test_wilcoxon_normal_branch_handles_heavy_ties() -> None:
    n = stats.WILCOXON_EXACT_MAX_N + 20
    # Scores are tie-heavy in practice: diffs only ever +-1 / +-0.5.
    data = [1.0] * (n // 2) + [-0.5] * (n - n // 2)
    stat, p = stats.wilcoxon_signed_rank(data)
    assert 0.0 <= p <= 1.0
    assert stat >= 0.0


# ---------------------------------------------------------------------------
# format_ci
# ---------------------------------------------------------------------------


def test_format_ci() -> None:
    assert stats.format_ci(0.25, 0.1, 0.4) == "+0.250 [0.100, 0.400]"
    assert stats.format_ci(-0.5, -0.75, -0.25, digits=2) == "-0.50 [-0.75, -0.25]"
