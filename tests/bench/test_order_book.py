"""T-Pf4Mix `order-book` family: the reference engine against HAND-COMPUTED expectations (the
generator derives every probe from this engine, so an engine bug would silently become a wrong
probe), plus generator validity/determinism and the spec/probe contract."""

from __future__ import annotations

from typing import Any

import pytest

from agent_orchestrator.bench.gen import FAMILY_REGISTRY
from agent_orchestrator.bench.gen.base import Difficulty
from agent_orchestrator.bench.gen.order_book import new_engine
from agent_orchestrator.bench.gen.traps import TRAP_CATALOGUE
from agent_orchestrator.bench.gen.validate import validate_probes


def eng(tier: int, stp: str = "cancel_resting", cash: int = 1000, asset: int = 100) -> Any:
    return new_engine({"tier": tier, "stp": stp, "start_cash": cash, "start_asset": asset})


def place(e: Any, oid: str, side: str, price: int | None, qty: int, **kw: Any) -> dict[str, Any]:
    req: dict[str, Any] = {"op": "place", "id": oid, "side": side, "qty": qty, **kw}
    if price is None:
        req["type"] = "market"
    else:
        req["price"] = price
    resp: dict[str, Any] = e.handle(req)
    return resp


def book(e: Any) -> dict[str, Any]:
    resp: dict[str, Any] = e.handle({"op": "book"})
    return resp


def order(e: Any, oid: str) -> dict[str, Any]:
    resp: dict[str, Any] = e.handle({"op": "order", "id": oid})["order"]
    return resp


def test_fifo_maker_price_and_partial_rest() -> None:
    e = eng(0)
    place(e, "s1", "sell", 100, 4)
    place(e, "s2", "sell", 100, 4)
    place(e, "s3", "sell", 101, 4)
    r = place(e, "b1", "buy", 105, 10)
    assert r == {
        "ok": True,
        "status": "filled",
        "fills": [
            {"maker": "s1", "price": 100, "qty": 4},
            {"maker": "s2", "price": 100, "qty": 4},
            {"maker": "s3", "price": 101, "qty": 2},
        ],
        "remaining": 0,
    }
    r = place(e, "b2", "buy", 101, 5)  # 2 left at 101, rest 3 rests at 101
    assert r["status"] == "open" and r["remaining"] == 3
    assert book(e) == {"ok": True, "bids": [[101, 3, 1]], "asks": [], "last": 101}
    assert order(e, "b2") == {"id": "b2", "side": "buy", "type": "limit", "price": 101,
                              "qty": 5, "filled": 2, "remaining": 3, "status": "open"}  # fmt: skip


def test_ioc_market_and_errors() -> None:
    e = eng(1)
    assert place(e, "m1", "buy", None, 5)["status"] == "cancelled"  # empty book
    place(e, "s1", "sell", 50, 2)
    r = place(e, "i1", "buy", 60, 5, tif="IOC")
    assert r["fills"] == [{"maker": "s1", "price": 50, "qty": 2}] and r["status"] == "cancelled"
    assert book(e)["bids"] == []
    assert place(e, "i1", "buy", 1, 1)["error"] == "duplicate_id"
    assert place(e, "x", "buy", None, 1, tif="GTC")["error"] == "invalid_request"
    assert e.handle({"op": "cancel", "id": "s1"})["error"] == "not_open"
    assert e.handle({"op": "cancel", "id": "zz"})["error"] == "unknown_order"
    assert e.handle({"op": "replace", "id": "zz", "qty": 1})["error"] == "unknown_op"  # tier 1


def test_fok_is_atomic_and_ignores_own_liquidity() -> None:
    e = eng(2, "cancel_resting")
    place(e, "s1", "sell", 100, 3, account="a")
    place(e, "s2", "sell", 101, 4, account="b")  # own
    r = place(e, "f1", "buy", 101, 5, tif="FOK", account="b")  # only 3 non-own -> killed
    assert r == {"ok": True, "status": "cancelled", "fills": [], "remaining": 0}
    assert book(e)["asks"] == [[100, 3, 1], [101, 4, 1]]  # own order NOT cancelled by a kill
    r = place(e, "f2", "buy", 101, 3, tif="FOK", account="b")
    assert r["status"] == "filled"
    assert order(e, "s2")["status"] == "open"  # not reached: s1 filled the whole FOK


def test_fok_under_cancel_incoming_stops_at_own_order() -> None:
    e = eng(2, "cancel_incoming")
    place(e, "s1", "sell", 100, 2, account="b")  # own, at the front
    place(e, "s2", "sell", 100, 9, account="a")
    assert place(e, "f1", "buy", 100, 3, tif="FOK", account="b")["status"] == "cancelled"
    assert book(e)["asks"] == [[100, 11, 2]]


@pytest.mark.parametrize("policy", ["cancel_resting", "cancel_incoming"])
def test_self_trade_prevention(policy: str) -> None:
    e = eng(2, policy)
    place(e, "s1", "sell", 100, 2, account="a")
    place(e, "s2", "sell", 100, 3, account="b")
    r = place(e, "b1", "buy", 100, 4, account="a")
    if policy == "cancel_resting":
        assert r["fills"] == [{"maker": "s2", "price": 100, "qty": 3}]
        assert r["status"] == "open" and r["remaining"] == 1
        assert order(e, "s1")["status"] == "cancelled"
    else:
        assert r == {"ok": True, "status": "cancelled", "fills": [], "remaining": 0}
        assert order(e, "s1")["status"] == "open"


def test_replace_priority_rules_and_failed_replace_changes_nothing() -> None:
    e = eng(2)
    place(e, "b1", "buy", 100, 5, account="a")
    place(e, "b2", "buy", 100, 5, account="b")
    assert e.handle({"op": "replace", "id": "b1", "qty": 4})["remaining"] == 4  # keeps priority
    r = place(e, "s1", "sell", 100, 2, account="c")
    assert r["fills"][0]["maker"] == "b1"
    e.handle({"op": "replace", "id": "b1", "qty": 9})  # larger -> back of the queue
    r = place(e, "s2", "sell", 100, 1, account="c")
    assert r["fills"][0]["maker"] == "b2"
    place(e, "p1", "sell", 110, 1, account="c", post_only=True)
    resp = e.handle({"op": "replace", "id": "p1", "price": 99})  # would cross the bids
    assert resp["error"] == "would_cross" and order(e, "p1")["price"] == 110


def test_funds_refund_release_and_check_order() -> None:
    e = eng(3, cash=1000, asset=10)
    place(e, "s1", "sell", 80, 3, account="a")
    place(e, "b1", "buy", 100, 5, account="b")  # reserves 500, fills 3 @80, rests 2 @100
    bal = e.handle({"op": "balance", "account": "b"})
    assert bal["cash"] == {"available": 1000 - 500 + 3 * 20, "reserved": 200}
    assert bal["asset"] == {"available": 13, "reserved": 0}
    e.handle({"op": "cancel", "id": "b1"})
    assert e.handle({"op": "balance", "account": "b"})["cash"] == {"available": 760, "reserved": 0}
    # insufficient_funds wins over would_cross (spec check order)
    place(e, "s2", "sell", 90, 1, account="a")
    r = place(e, "p1", "buy", 95, 100, account="c", post_only=True)
    assert r["error"] == "insufficient_funds"


def test_stop_cascade_in_placement_order() -> None:
    e = eng(3, cash=10**6, asset=10**4)
    place(e, "s1", "sell", 101, 2, account="a")
    place(e, "s2", "sell", 103, 2, account="a")
    place(e, "s3", "sell", 106, 5, account="a")
    place(e, "t1", "buy", None, 2, account="b", stop=103)  # placed first
    place(e, "t2", "buy", None, 1, account="c", stop=101)
    place(e, "b1", "buy", 101, 2, account="b")  # trade @101 -> t2 triggers (101 >= 101)
    # t2 buys 1 @103 -> last 103 -> t1 triggers, buys 1 @103 + 1 @106
    assert order(e, "t2")["filled"] == 1 and order(e, "t2")["status"] == "filled"
    assert order(e, "t1")["filled"] == 2
    assert book(e)["asks"] == [[106, 4, 1]] and book(e)["last"] == 106


def test_generator_valid_deterministic_and_spec_has_no_trap_keys() -> None:
    fam = FAMILY_REGISTRY["order-book"]()
    for tier in range(4):
        d = Difficulty(entities=tier, traps=3, concurrency=1, scale=1)
        a = fam.generate(42, d, "python")
        assert validate_probes(a.probes) == []
        assert a.probes == fam.generate(42, d, "python").probes
        assert set(a.traps) <= set(TRAP_CATALOGUE)
        assert not any(k in a.spec_md for k in a.traps)
        assert "orderbook_server" not in a.spec_md
    assert len(fam.generate(1, Difficulty(entities=3, traps=3, concurrency=3, scale=1),
                            "python").probes["groups"]["core"]) == 9 + 6  # fmt: skip


def test_typescript_and_go_instances_have_skeletons() -> None:
    fam = FAMILY_REGISTRY["order-book"]()
    for lang in ("typescript", "go"):
        inst = fam.generate(7, Difficulty(entities=1, traps=1, concurrency=0, scale=1), lang)
        assert "run.sh" in inst.skeleton and inst.contract == "jsonl"


def test_int_template_renders_integers() -> None:
    import random

    from agent_orchestrator.bench.blackbox import Templater, template_refs
    from agent_orchestrator.bench.errors import GraderError

    t = Templater(random.Random(0))
    assert t.render({"price": "{{int:i}}", "id": "p{{i}}"}, {}, 7) == {"price": 7, "id": "p7"}
    assert t.render("{{int:n}}", {"n": "12"}) == 12
    assert template_refs({"price": "{{int:x}}"}) == {"x"}
    with pytest.raises(GraderError):
        t.render("{{int:missing}}", {})
