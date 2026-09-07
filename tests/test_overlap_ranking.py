"""Tests for T-Ov9Bt5 — soft overlap-aware co-scheduling (E-Wk9Tz3 FR-10, HLD §9.1,
`scheduling/overlap.py`).

Covers: AC-1 (locked 4-arg `rank_wave` signature), AC-2 (n==1 provable no-op over a
randomized >=200-input corpus), AC-3 (never withholds a slot — property test), AC-4
(disjoint-first preference, exact scenario), AC-5 (determinism across repeated calls and
shuffled dict iteration order), AC-6 (no `touches` -> 0.0, never a penalty), AC-7
(`glob_intersection`, table-driven), AC-8 (hotspot weighting beats a cold collision at
equal overlap counts).

Every property test below uses a `random.Random(seed)` instance constructed with a fixed,
named seed — never the bare `random` module — so a failure is always reproducible.
"""

from __future__ import annotations

import random

import pytest

from agent_orchestrator.scheduling.overlap import (
    BASELINE_OVERLAP_WEIGHT,
    glob_intersection,
    overlap_score,
    rank_wave,
)

# Fixed seeds for every randomized/property test in this file (never derived from
# time/os.urandom) so a failure reproduces identically on re-run.
_SEED_NOOP_CORPUS = 20260907
_SEED_NEVER_WITHHOLDS = 20260908
_SEED_DETERMINISM = 20260909
_SEED_PERMUTATION = 20260910


# ---------------------------------------------------------------------------
# AC-7: glob_intersection — table-driven, syntactic and filesystem-free
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mine", "theirs", "expect_intersect"),
    [
        # HLD §9.1's own worked examples, verbatim.
        pytest.param(["src/api/**"], ["src/api/accounts.rs"], True, id="wildcard-vs-literal-under"),
        pytest.param(["src/api/**"], ["src/ui/**"], False, id="disjoint-wildcard-prefixes"),
        pytest.param(["**/mod.rs"], ["src/a/mod.rs"], True, id="leading-wildcard-filename"),
        pytest.param(["src/x.rs"], ["src/x.rs"], True, id="identical-literals"),
        # Additional coverage beyond the HLD's own table.
        pytest.param(["src/x.rs"], ["src/y.rs"], False, id="distinct-literals"),
        pytest.param(["src/api/**"], ["src/api/**"], True, id="identical-wildcards"),
        pytest.param(["a/**"], ["a/b/**"], True, id="nested-wildcard-prefix-contains"),
        pytest.param([], ["src/x.rs"], False, id="empty-mine"),
        pytest.param(["src/x.rs"], [], False, id="empty-theirs"),
    ],
)
def test_glob_intersection_table(
    mine: list[str], theirs: list[str], expect_intersect: bool
) -> None:
    hits = glob_intersection(mine, theirs)
    assert bool(hits) is expect_intersect


def test_glob_intersection_returns_one_hit_per_colliding_pair() -> None:
    # One glob in `mine` collides with BOTH literals in `theirs` -> 2 hits, one per pair.
    hits = glob_intersection(["src/api/**"], ["src/api/accounts.rs", "src/api/other.rs"])
    assert len(hits) == 2


# ---------------------------------------------------------------------------
# AC-6: a task with no `touches` scores 0.0 against everything
# ---------------------------------------------------------------------------


def test_no_touches_hint_scores_zero_never_a_penalty() -> None:
    touches = {"b": ["src/x/**"], "c": ["src/x/**"]}
    # "a" has no entry at all in `touches` -- absent hint, not an empty list.
    assert overlap_score("a", ["b", "c"], touches, {}) == 0.0


def test_empty_touches_list_also_scores_zero() -> None:
    touches = {"a": [], "b": ["src/x/**"]}
    assert overlap_score("a", ["b"], touches, {}) == 0.0


def test_no_touches_hint_never_blocks_greedy_pass1() -> None:
    # Every task lacks touches -> pass 1 admits all of them (all score 0.0), preserving
    # `ready`'s original order (this is also today's exact pre-epic behavior).
    ready = ["a", "b", "c", "d"]
    assert rank_wave(ready, {}, {}, 3) == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# AC-8: hotspot weighting -- equal overlap counts, cold collision preferred
# ---------------------------------------------------------------------------


def test_hotspot_collision_scores_strictly_higher_than_cold_collision() -> None:
    touches = {
        "a": ["src/hot.rs"],
        "b": ["src/hot.rs"],
        "c": ["src/cold.rs"],
        "d": ["src/cold.rs"],
    }
    hotspots = {"src/hot.rs": 5.0}  # "src/cold.rs" absent -> baseline
    hot_score = overlap_score("b", ["a"], touches, hotspots)
    cold_score = overlap_score("d", ["c"], touches, hotspots)
    assert hot_score > cold_score
    assert cold_score == BASELINE_OVERLAP_WEIGHT


def test_rank_wave_prefers_the_cold_collision_pair_at_equal_overlap_counts() -> None:
    # Given a forced overlap either way (pass 1 can admit only one of each pair), the
    # cold-collision task should be preferred over the hotspot-collision one in pass 2.
    ready = ["a", "hot_dup", "c", "cold_dup"]
    touches = {
        "a": ["src/hot.rs"],
        "hot_dup": ["src/hot.rs"],
        "c": ["src/cold.rs"],
        "cold_dup": ["src/cold.rs"],
    }
    hotspots = {"src/hot.rs": 5.0}
    # n=3: pass 1 admits a, c (both zero-overlap against the initially empty selection);
    # hot_dup and cold_dup then compete for the last slot in pass 2 -- cold_dup has the
    # strictly lower score and wins.
    result = rank_wave(ready, touches, hotspots, 3)
    assert result[:2] == ["a", "c"]
    assert result[2] == "cold_dup"


# ---------------------------------------------------------------------------
# AC-1/AC-2: locked signature + n<=1 provable no-op
# ---------------------------------------------------------------------------


def test_rank_wave_signature_is_exactly_four_positional_args() -> None:
    # AC-1: `rank_wave(ready, touches, hotspots, n)` -- no `preference` argument.
    import inspect

    params = list(inspect.signature(rank_wave).parameters)
    assert params == ["ready", "touches", "hotspots", "n"]


def test_module_contains_no_second_copy_of_resolve_overlap_preference() -> None:
    # AC-14 / review S-1: a real structural guard, not just the signature-arity check
    # above -- fails if a future edit re-derives `models.resolve_overlap_preference`'s
    # "soft iff any task isolated" rule as actual CODE in this module. Walks the AST
    # (not a raw substring search) so a docstring/comment cross-reference to the real
    # rule's name -- like this module's own -- never trips a false positive; only a
    # genuine identifier USE (an `ast.Name`/`ast.Attribute` node) counts.
    import ast
    import inspect

    from agent_orchestrator.scheduling import overlap as overlap_module

    tree = ast.parse(inspect.getsource(overlap_module))
    forbidden = {"resolve_task_isolation", "resolve_overlap_preference", "OVERLAP_SOFT"}
    used_names = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id in forbidden
    }
    used_attrs = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in forbidden
    }
    assert not (used_names | used_attrs), f"re-derived in overlap.py: {used_names | used_attrs}"


def _random_ready(rng: random.Random, n_tasks: int) -> list[str]:
    return [f"t{i}" for i in range(n_tasks)]


def _random_touches(rng: random.Random, ready: list[str], glob_pool: list[str]) -> dict:
    touches: dict[str, list[str]] = {}
    for tid in ready:
        if rng.random() < 0.2:
            continue  # some tasks deliberately carry no hint
        k = rng.randint(0, 3)
        touches[tid] = [rng.choice(glob_pool) for _ in range(k)]
    return touches


def _random_hotspots(rng: random.Random, glob_pool: list[str]) -> dict:
    return {g: rng.uniform(1.0, 10.0) for g in glob_pool if rng.random() < 0.5}


_GLOB_POOL = ["src/a/**", "src/b/**", "src/a/x.rs", "src/c.rs", "**/mod.rs", "docs/**"]


@pytest.mark.parametrize("trial", range(200))
def test_n_equals_one_is_a_provable_no_op(trial: int) -> None:
    rng = random.Random(_SEED_NOOP_CORPUS + trial)
    n_tasks = rng.randint(1, 12)
    ready = _random_ready(rng, n_tasks)
    rng.shuffle(ready)
    touches = _random_touches(rng, ready, _GLOB_POOL)
    hotspots = _random_hotspots(rng, _GLOB_POOL)
    assert rank_wave(ready, touches, hotspots, 1) == ready[:1]


def test_n_equals_zero_is_also_a_no_op() -> None:
    assert rank_wave(["a", "b", "c"], {"a": ["x"], "b": ["x"]}, {}, 0) == []


def test_n_negative_matches_python_slice_semantics_and_stays_a_no_op() -> None:
    # n<=1 short-circuits to `ready[:n]` directly -- plain Python slicing, not a special
    # case: `ready[:-1]` drops the last element, exactly as slicing any list would.
    assert rank_wave(["a", "b", "c"], {}, {}, -1) == ["a", "b", "c"][:-1]


# ---------------------------------------------------------------------------
# AC-3: never withholds a slot (property test)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("trial", range(200))
def test_never_withholds_a_slot(trial: int) -> None:
    rng = random.Random(_SEED_NEVER_WITHHOLDS + trial)
    n_tasks = rng.randint(2, 15)
    ready = _random_ready(rng, n_tasks)
    rng.shuffle(ready)
    touches = _random_touches(rng, ready, _GLOB_POOL)
    hotspots = _random_hotspots(rng, _GLOB_POOL)
    n = rng.randint(1, n_tasks)
    result = rank_wave(ready, touches, hotspots, n)
    assert len(result) == n
    assert len(set(result)) == n  # no duplicates
    assert set(result) <= set(ready)


def test_never_withholds_a_slot_when_every_candidate_overlaps_every_other() -> None:
    # Adversarial: every task touches the exact same single glob -- pass 1 admits only
    # one, pass 2 must still fill every remaining slot despite every score being > 0.
    ready = [f"t{i}" for i in range(8)]
    touches = {tid: ["src/only.rs"] for tid in ready}
    for n in range(1, len(ready) + 1):
        result = rank_wave(ready, touches, {}, n)
        assert len(result) == n
        assert len(set(result)) == n


def test_never_withholds_when_n_exceeds_ready_length() -> None:
    ready = ["a", "b", "c"]
    touches = {tid: ["x"] for tid in ready}
    result = rank_wave(ready, touches, {}, 10)
    assert set(result) == set(ready)
    assert len(result) == len(ready)


# ---------------------------------------------------------------------------
# AC-4: preference works -- disjoint task preferred, first-in-ready never displaced
# ---------------------------------------------------------------------------


def test_disjoint_task_preferred_over_same_glob_duplicate() -> None:
    ready = ["a", "b", "c"]
    touches = {"a": ["src/x/**"], "b": ["src/x/**"], "c": ["src/y/**"]}
    assert rank_wave(ready, touches, {}, 2) == ["a", "c"]


def test_first_in_ready_order_is_never_displaced() -> None:
    # "a" is first in `ready` and has zero overlap against the (empty) initial
    # selection, so pass 1 always admits it before anything else is considered.
    ready = ["a", "b", "c", "d"]
    touches = {tid: ["src/only.rs"] for tid in ready}
    result = rank_wave(ready, touches, {}, 2)
    assert result[0] == "a"


# ---------------------------------------------------------------------------
# AC-5: determinism -- same inputs -> same output, tie-break is ready.index(t)
# ---------------------------------------------------------------------------


def test_determinism_across_100_repeated_calls() -> None:
    ready = ["a", "b", "c", "d", "e"]
    touches = {
        "a": ["src/x/**"],
        "b": ["src/x/**"],
        "c": ["src/x/**"],
        "d": ["src/y/**"],
        "e": ["src/y/**"],
    }
    hotspots = {"src/x/**": 3.0}
    first = rank_wave(ready, touches, hotspots, 3)
    for _ in range(100):
        assert rank_wave(ready, touches, hotspots, 3) == first


def test_determinism_is_independent_of_dict_iteration_order() -> None:
    ready = ["a", "b", "c", "d", "e"]
    touches_items = [
        ("a", ["src/x/**"]),
        ("b", ["src/x/**"]),
        ("c", ["src/x/**"]),
        ("d", ["src/y/**"]),
        ("e", ["src/y/**"]),
    ]
    hotspots_items = [("src/x/**", 3.0), ("src/y/**", 1.0)]

    baseline = rank_wave(ready, dict(touches_items), dict(hotspots_items), 3)

    rng = random.Random(_SEED_DETERMINISM)
    for _ in range(20):
        shuffled_touches = list(touches_items)
        shuffled_hotspots = list(hotspots_items)
        rng.shuffle(shuffled_touches)
        rng.shuffle(shuffled_hotspots)
        result = rank_wave(ready, dict(shuffled_touches), dict(shuffled_hotspots), 3)
        assert result == baseline


def test_tie_break_is_ready_index() -> None:
    # Three tasks with identical touches (identical overlap scores against each other)
    # -- pass 1 admits the first one (zero-overlap against empty selection); the
    # remaining two tie on overlap_score in pass 2 and must break on ready.index(t).
    ready = ["z", "y", "x"]  # deliberately not alphabetical -- index order matters
    touches = {tid: ["src/only.rs"] for tid in ready}
    result = rank_wave(ready, touches, {}, 3)
    assert result == ["z", "y", "x"]


# ---------------------------------------------------------------------------
# Property test: rank_wave's output is always a permutation of a subset of ready,
# with the n==len(ready) case being an exact permutation of the whole input.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("trial", range(200))
def test_output_is_always_a_valid_subset_permutation(trial: int) -> None:
    rng = random.Random(_SEED_PERMUTATION + trial)
    n_tasks = rng.randint(1, 10)
    ready = _random_ready(rng, n_tasks)
    rng.shuffle(ready)
    touches = _random_touches(rng, ready, _GLOB_POOL)
    hotspots = _random_hotspots(rng, _GLOB_POOL)
    n = rng.randint(0, n_tasks + 3)  # exercise n > len(ready) too (always >= 0 here)

    result = rank_wave(ready, touches, hotspots, n)

    expected_len = min(n, n_tasks)
    assert len(result) == expected_len
    assert len(set(result)) == len(result)  # no duplicates
    assert set(result) <= set(ready)
    if n >= n_tasks:
        # Every ready id must appear exactly once -- a true permutation of the input.
        assert sorted(result) == sorted(ready)
