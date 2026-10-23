"""Held-out grading tests for bugfix-cache-key-collision -- NOT part of the visible
tests/ suite an agent sees. Directly probes `cache.keys.normalize_key`'s contract
(case-insensitive + surrounding-whitespace-only) so a fix that only patches
`cache/store.py`'s eviction logic (the module the failing visible test superficially
points at) without touching the actual root cause in `cache/keys.py` still fails here.
"""

from __future__ import annotations

from cache.keys import normalize_key
from cache.store import LRUCache


def test_case_insensitive_and_trims_surrounding_whitespace_only() -> None:
    assert normalize_key("  Foo Bar  ") == normalize_key("foo bar")
    assert normalize_key("FOO") == normalize_key("foo")


def test_distinct_keys_with_internal_whitespace_do_not_collide() -> None:
    assert normalize_key("ali ce") != normalize_key("alice")
    assert normalize_key("New York") != normalize_key("Newyork")


def test_cache_treats_keys_with_internal_whitespace_as_distinct_entries() -> None:
    cache = LRUCache(capacity=5)
    cache.set("alice", "A1")
    cache.set("ali ce", "A2")
    assert cache.get("alice") == "A1"
    assert cache.get("ali ce") == "A2"
    assert len(cache) == 2


def test_eviction_count_is_correct_with_many_similarly_spelled_keys() -> None:
    """Regression for the root cause: if key normalization ever collapses distinct
    keys, capacity is silently under-utilized and eviction stops firing when it
    should. With 4 genuinely distinct keys and capacity=3, exactly one eviction must
    occur, and it must be the least-recently-used one.
    """
    cache = LRUCache(capacity=3)
    cache.set("room 12", "R1")
    cache.set("room 21", "R2")
    cache.set("roo m12", "R3")  # distinct from "room 12" -- must not collide
    cache.get("room 12")
    cache.get("roo m12")
    cache.set("room 99", "R4")

    assert len(cache) == 3
    assert cache.get("room 21") is None
    assert cache.get("room 12") == "R1"
    assert cache.get("roo m12") == "R3"
    assert cache.get("room 99") == "R4"


def test_internal_tab_or_newline_is_not_surrounding_whitespace() -> None:
    """ "Surrounding whitespace" means leading/trailing -- an internal tab or newline
    is part of the key's identity exactly like an internal space, regardless of
    which whitespace character it is."""
    assert normalize_key("ali\tce") != normalize_key("alice")
    assert normalize_key("ali\nce") != normalize_key("alice")
    # But a LEADING/TRAILING tab or newline is still surrounding whitespace and must
    # be trimmed, same as a leading/trailing space.
    assert normalize_key("\talice\t") == normalize_key("alice")
    assert normalize_key("\nalice\n") == normalize_key("alice")


def test_three_way_near_collision_pattern_evicts_the_correct_entry() -> None:
    """A harder tracing case than the two-key visible scenario: THREE pairwise-similar
    keys (each a space/case variant that must NOT collide with either of the other
    two) share a capacity=2 cache; only the correct LRU victim may be evicted.
    """
    cache = LRUCache(capacity=2)
    cache.set("data set", "D1")
    cache.set("Data Set", "D1")  # same normalized key as "data set" -- SHOULD collide
    assert len(cache) == 1  # case-insensitivity means these two really are one entry

    cache.set("dataset", "D2")  # distinct: no internal space at all
    assert len(cache) == 2
    cache.get("data set")  # keep the "data set"/"Data Set" entry fresh

    cache.set("data  set", "D3")  # distinct: TWO internal spaces, not one
    assert len(cache) == 2
    # "dataset" was least-recently-used (never touched after its own insert) and
    # must be the one evicted -- not "data set" (kept fresh) and not a spurious
    # non-eviction caused by an earlier silent collision inflating capacity headroom.
    assert cache.get("dataset") is None
    assert cache.get("data set") == "D1"
    assert cache.get("data  set") == "D3"
