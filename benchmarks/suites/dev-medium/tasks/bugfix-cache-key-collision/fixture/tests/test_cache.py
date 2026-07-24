from cache.store import LRUCache
from cache.ttl import get_if_fresh, set_with_ttl


def test_set_and_get_roundtrip() -> None:
    cache = LRUCache(capacity=3)
    cache.set("a", 1)
    assert cache.get("a") == 1


def test_get_missing_key_returns_none() -> None:
    cache = LRUCache(capacity=3)
    assert cache.get("missing") is None


def test_get_is_case_insensitive() -> None:
    cache = LRUCache(capacity=3)
    cache.set("Alpha", 1)
    assert cache.get("alpha") == 1


def test_ttl_expiry() -> None:
    clock_time = [1000.0]
    cache = LRUCache(capacity=3)
    set_with_ttl(cache, "a", "value", ttl_seconds=10, clock=lambda: clock_time[0])
    assert get_if_fresh(cache, "a", clock=lambda: clock_time[0]) == "value"
    clock_time[0] = 1011.0
    assert get_if_fresh(cache, "a", clock=lambda: clock_time[0]) is None


def test_store_evicts_least_recently_used() -> None:
    """A capacity=3 cache holding 4 distinct users: touch three of them again after
    insertion (moving them to "recently used"), then insert a 4th distinct user --
    the ONE we never touch again after its initial insert must be the one evicted.
    """
    cache = LRUCache(capacity=3)
    cache.set("alice", "A1")
    cache.set("bob", "B1")
    cache.set("ali ce", "A2")

    # Keep "alice" and "ali ce" both fresh; "bob" is not touched again.
    cache.get("alice")
    cache.get("ali ce")

    cache.set("carol", "C1")

    assert cache.get("bob") is None, "bob was least-recently-used and should be evicted"
    assert cache.get("alice") == "A1"
    assert cache.get("ali ce") == "A2"
    assert cache.get("carol") == "C1"
