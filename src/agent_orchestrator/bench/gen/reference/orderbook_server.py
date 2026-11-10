"""Reference implementation of the `order-book` family (grader-owned, never agent-visible).

A single-instrument limit order book over JSON lines (stdin/stdout), stdlib only, parameterised by
``instance.json`` (path in ``$INSTANCE_JSON``). The same ``Engine`` class is imported by the
generator to compute every expected response, so the probes and this program cannot disagree.

Data structures: per side a dict ``price -> deque[order id]`` (FIFO within a level) plus a sorted
list of "priority keys" (bids: price, asks: -price) whose LAST element is the best level, so taking
liquidity, top-of-book and depth queries are O(log n + k). All quantities and prices are integers.

This file is copied verbatim into ``<grading_dir>/reference/`` by the generated provider.
"""

from __future__ import annotations

import bisect
import json
import os
import re
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
PRICE_MAX = 1_000_000_000
QTY_MAX = 1_000_000_000
DEPTH_MAX = 100
DEFAULT_DEPTH = 10


class OpError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def invalid(message: str) -> OpError:
    return OpError("invalid_request", message)


def is_int(v: Any) -> bool:
    return type(v) is int


@dataclass
class Order:
    id: str
    seq: int
    side: str
    type: str
    price: int | None
    qty: int
    tif: str
    post_only: bool
    account: str | None
    stop: int | None
    filled: int = 0
    remaining: int = 0  # resting quantity (open) or pending quantity (stop)
    status: str = "new"
    reserved_price: int | None = None  # limit buy: cash reserved per unit

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "side": self.side,
            "type": self.type,
            "price": self.price,
            "qty": self.qty,
            "filled": self.filled,
            "remaining": self.remaining,
            "status": self.status,
        }


class Side:
    """One side of the book; `keys` ascending with the BEST level last."""

    def __init__(self, sign: int) -> None:
        self.sign = sign  # +1 bids (higher better), -1 asks (lower better)
        self.levels: dict[int, deque[str]] = {}
        self.keys: list[int] = []

    def add(self, price: int, oid: str) -> None:
        level = self.levels.get(price)
        if level is None:
            level = self.levels[price] = deque()
            bisect.insort(self.keys, self.sign * price)
        level.append(oid)

    def remove(self, price: int, oid: str) -> None:
        level = self.levels[price]
        level.remove(oid)
        if not level:
            self._drop(price)

    def _drop(self, price: int) -> None:
        del self.levels[price]
        idx = bisect.bisect_left(self.keys, self.sign * price)
        self.keys.pop(idx)

    def best_price(self) -> int | None:
        return self.sign * self.keys[-1] if self.keys else None

    def prices_best_first(self) -> list[int]:
        return [self.sign * k for k in reversed(self.keys)]


class Engine:
    def __init__(self, limits: dict[str, Any]) -> None:
        self.tier: int = limits["tier"]
        self.stp: str = limits.get("stp", "cancel_resting")
        self.start_cash: int = limits.get("start_cash", 0)
        self.start_asset: int = limits.get("start_asset", 0)
        self.orders: dict[str, Order] = {}
        self.bids = Side(+1)
        self.asks = Side(-1)
        self.last: int | None = None
        self.seq = 0
        self.stops: list[str] = []  # pending stop ids in placement order
        self.accounts: dict[str, dict[str, int]] = {}

    # -- feature flags ------------------------------------------------------------------------
    @property
    def has_market(self) -> bool:
        return self.tier >= 1

    @property
    def has_accounts(self) -> bool:
        return self.tier >= 2

    @property
    def has_funds(self) -> bool:
        return self.tier >= 3

    # -- funds ----------------------------------------------------------------------------------
    def acct(self, name: str) -> dict[str, int]:
        a = self.accounts.get(name)
        if a is None:
            a = self.accounts[name] = {
                "cash": self.start_cash,
                "cash_reserved": 0,
                "asset": self.start_asset,
                "asset_reserved": 0,
            }
        return a

    def _release(self, o: Order, qty: int) -> None:
        """Release the reservation backing `qty` unfilled units of `o`."""
        if not self.has_funds or qty <= 0 or o.account is None:
            return
        a = self.acct(o.account)
        if o.side == "sell":
            a["asset_reserved"] -= qty
            a["asset"] += qty
        elif o.reserved_price is not None:
            amount = o.reserved_price * qty
            a["cash_reserved"] -= amount
            a["cash"] += amount

    # -- dispatch -------------------------------------------------------------------------------
    def handle(self, req: Any) -> dict[str, Any]:
        try:
            if not isinstance(req, dict) or not isinstance(req.get("op"), str):
                raise invalid("request must be an object with a string op")
            op = req["op"]
            handler = {
                "ping": self.op_ping,
                "place": self.op_place,
                "cancel": self.op_cancel,
                "order": self.op_order,
                "book": self.op_book,
            }.get(op)
            if handler is None and self.has_accounts and op == "replace":
                handler = self.op_replace
            if handler is None and self.has_funds and op == "balance":
                handler = self.op_balance
            if handler is None:
                raise OpError("unknown_op", f"unknown op {op!r}")
            return handler(req)
        except OpError as err:
            return {"ok": False, "error": err.code, "message": err.message}

    def op_ping(self, req: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}

    # -- validation -----------------------------------------------------------------------------
    def _id(self, req: dict[str, Any]) -> str:
        oid = req.get("id")
        if not isinstance(oid, str) or not ID_RE.match(oid):
            raise invalid("id must be 1-32 characters [A-Za-z0-9_-]")
        return oid

    def _int(self, req: dict[str, Any], key: str, lo: int, hi: int) -> int:
        v = req.get(key)
        if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
            raise invalid(f"{key} must be an integer from {lo} to {hi}")
        return v

    def _parse_order(self, req: dict[str, Any]) -> Order:
        oid = self._id(req)
        side = req.get("side")
        if side not in ("buy", "sell"):
            raise invalid("side must be buy or sell")
        otype = req.get("type", "limit")
        if otype not in (("limit", "market") if self.has_market else ("limit",)):
            raise invalid("unsupported type")
        qty = self._int(req, "qty", 1, QTY_MAX)
        price: int | None = None
        if otype == "limit":
            price = self._int(req, "price", 0, PRICE_MAX)
        elif "price" in req:
            raise invalid("market orders take no price")
        tifs = ("GTC", "IOC", "FOK") if self.has_market else ("GTC", "IOC")
        tif = req.get("tif", "IOC" if otype == "market" else "GTC")
        if tif not in tifs or (otype == "market" and tif == "GTC"):
            raise invalid("unsupported tif")
        post_only = False
        if self.has_accounts and "post_only" in req:
            if type(req["post_only"]) is not bool:
                raise invalid("post_only must be a boolean")
            post_only = req["post_only"]
            if post_only and (otype != "limit" or tif != "GTC"):
                raise invalid("post_only requires a GTC limit order")
        account: str | None = None
        if self.has_accounts:
            account = req.get("account")
            if not isinstance(account, str) or not ID_RE.match(account):
                raise invalid("account must be 1-32 characters [A-Za-z0-9_-]")
        stop: int | None = None
        if self.has_funds and "stop" in req:
            stop = self._int(req, "stop", 0, PRICE_MAX)
            if post_only:
                raise invalid("stop orders cannot be post_only")
        return Order(oid, 0, side, otype, price, qty, tif, post_only, account, stop)

    # -- place ----------------------------------------------------------------------------------
    def _would_cross(self, side: str, price: int) -> bool:
        if side == "buy":
            best = self.asks.best_price()
            return best is not None and price >= best
        best = self.bids.best_price()
        return best is not None and price <= best

    def _reserve(self, o: Order) -> None:
        """Funds check + reservation for an incoming order (raises insufficient_funds)."""
        if not self.has_funds or o.account is None:
            return
        a = self.acct(o.account)
        if o.side == "sell":
            if a["asset"] < o.qty:
                raise OpError("insufficient_funds", "not enough asset")
            a["asset"] -= o.qty
            a["asset_reserved"] += o.qty
        elif o.type == "limit":
            assert o.price is not None
            cost = o.price * o.qty
            if a["cash"] < cost:
                raise OpError("insufficient_funds", "not enough cash")
            a["cash"] -= cost
            a["cash_reserved"] += cost
            o.reserved_price = o.price
        # market buy: nothing reserved; each fill is limited by available cash

    def op_place(self, req: dict[str, Any]) -> dict[str, Any]:
        o = self._parse_order(req)
        if o.id in self.orders:
            raise OpError("duplicate_id", "order id already used")
        self._reserve(o)
        if o.post_only and o.price is not None and self._would_cross(o.side, o.price):
            self._release_all_reserved(o)
            raise OpError("would_cross", "post-only order would trade")
        self.seq += 1
        o.seq = self.seq
        self.orders[o.id] = o
        if o.stop is not None:
            o.status, o.remaining = "pending", o.qty
            self.stops.append(o.id)
            self._trigger_stops()
            return {"ok": True, "status": "pending", "fills": [], "remaining": o.qty}
        fills = self._execute(o, o.qty)
        self._trigger_stops()
        return {"ok": True, "status": o.status, "fills": fills, "remaining": o.remaining}

    def _release_all_reserved(self, o: Order) -> None:
        self._release(o, o.qty)

    def _fok_feasible(self, o: Order, qty: int) -> bool:
        opp = self.asks if o.side == "buy" else self.bids
        need, cost = qty, 0
        for price in opp.prices_best_first():
            if o.type == "limit":
                assert o.price is not None
                if (o.side == "buy" and price > o.price) or (o.side == "sell" and price < o.price):
                    break
            for mid in opp.levels[price]:
                maker = self.orders[mid]
                if self.has_accounts and maker.account == o.account:
                    if self.stp == "cancel_incoming":
                        return False  # matching would stop here, before the full quantity
                    continue  # cancel_resting: that order would be cancelled, not traded
                take = min(need, maker.remaining)
                need -= take
                cost += take * price
                if need == 0:
                    break
            if need == 0:
                break
        if need > 0:
            return False
        if self.has_funds and o.side == "buy" and o.type == "market":
            return self.acct(o.account or "")["cash"] >= cost
        return True

    def _execute(self, o: Order, qty: int) -> list[dict[str, Any]]:
        """Match `qty` units of incoming order `o`; rest or cancel the remainder."""
        fills: list[dict[str, Any]] = []
        if o.tif == "FOK" and not self._fok_feasible(o, qty):
            self._release(o, qty)
            o.status, o.remaining = "cancelled", 0
            return fills
        opp = self.asks if o.side == "buy" else self.bids
        stp_stop = False
        while qty > 0:
            best = opp.best_price()
            if best is None:
                break
            if o.type == "limit":
                assert o.price is not None
                if (o.side == "buy" and best > o.price) or (o.side == "sell" and best < o.price):
                    break
            level = opp.levels[best]
            maker = self.orders[level[0]]
            if self.has_accounts and maker.account == o.account:
                if self.stp == "cancel_resting":
                    opp.remove(best, maker.id)
                    self._release(maker, maker.remaining)
                    maker.status, maker.remaining = "cancelled", 0
                    continue
                stp_stop = True
                break
            take = min(qty, maker.remaining)
            if self.has_funds and o.side == "buy" and o.type == "market":
                take = min(take, self.acct(o.account or "")["cash"] // best)
                if take == 0:
                    break
            self._settle(o, maker, best, take)
            fills.append({"maker": maker.id, "price": best, "qty": take})
            qty -= take
            o.filled += take
            maker.filled += take
            maker.remaining -= take
            self.last = best
            if maker.remaining == 0:
                maker.status = "filled"
                opp.remove(best, maker.id)
        if qty > 0 and o.type == "limit" and o.tif == "GTC" and not stp_stop:
            assert o.price is not None
            (self.bids if o.side == "buy" else self.asks).add(o.price, o.id)
            o.status, o.remaining = "open", qty
        else:
            self._release(o, qty)
            o.remaining = 0
            o.status = "filled" if qty == 0 else "cancelled"
        return fills

    def _settle(self, taker: Order, maker: Order, price: int, qty: int) -> None:
        if not self.has_funds:
            return
        buyer, seller = (taker, maker) if taker.side == "buy" else (maker, taker)
        b, s = self.acct(buyer.account or ""), self.acct(seller.account or "")
        if buyer.reserved_price is not None:
            b["cash_reserved"] -= buyer.reserved_price * qty
            b["cash"] += (buyer.reserved_price - price) * qty  # price improvement refund
        else:
            b["cash"] -= price * qty
        b["asset"] += qty
        s["asset_reserved"] -= qty
        s["cash"] += price * qty

    def _trigger_stops(self) -> None:
        while self.last is not None:
            hit = None
            for sid in self.stops:
                o = self.orders[sid]
                assert o.stop is not None
                if (o.side == "buy" and self.last >= o.stop) or (
                    o.side == "sell" and self.last <= o.stop
                ):
                    hit = o
                    break
            if hit is None:
                return
            self.stops.remove(hit.id)
            hit.status, hit.remaining = "new", 0
            self._execute(hit, hit.qty)

    # -- cancel / replace ------------------------------------------------------------------------
    def _existing(self, req: dict[str, Any]) -> Order:
        oid = self._id(req)
        o = self.orders.get(oid)
        if o is None:
            raise OpError("unknown_order", "no such order")
        return o

    def op_cancel(self, req: dict[str, Any]) -> dict[str, Any]:
        o = self._existing(req)
        if o.status == "pending":
            self.stops.remove(o.id)
        elif o.status == "open":
            assert o.price is not None
            (self.bids if o.side == "buy" else self.asks).remove(o.price, o.id)
        else:
            raise OpError("not_open", "order is not open")
        self._release(o, o.remaining)
        o.status, o.remaining = "cancelled", 0
        return {"ok": True}

    def op_replace(self, req: dict[str, Any]) -> dict[str, Any]:
        # Spec order: field validation, then unknown_order, then not_open, funds, would_cross.
        oid = self._id(req)
        if "price" not in req and "qty" not in req:
            raise invalid("replace needs price and/or qty")
        new_price = self._int(req, "price", 0, PRICE_MAX) if "price" in req else None
        new_qty = self._int(req, "qty", 1, QTY_MAX) if "qty" in req else None
        o = self.orders.get(oid)
        if o is None:
            raise OpError("unknown_order", "no such order")
        if o.status != "open" or o.type != "limit":
            raise OpError("not_open", "only open limit orders can be replaced")
        price = new_price if new_price is not None else o.price
        qty = new_qty if new_qty is not None else o.remaining
        assert o.price is not None and price is not None
        book = self.bids if o.side == "buy" else self.asks
        if price == o.price and qty <= o.remaining:
            self._release(o, o.remaining - qty)
            o.qty -= o.remaining - qty
            o.remaining = qty
            return {"ok": True, "status": "open", "fills": [], "remaining": qty}
        # Re-entry: checks first, so a rejected replace changes nothing.
        if self.has_funds:
            a = self.acct(o.account or "")
            if o.side == "sell":
                if a["asset"] + o.remaining < qty:
                    raise OpError("insufficient_funds", "not enough asset")
            elif a["cash"] + (o.reserved_price or 0) * o.remaining < price * qty:
                raise OpError("insufficient_funds", "not enough cash")
        if o.post_only and self._would_cross_excluding(o, price):
            raise OpError("would_cross", "post-only order would trade")
        book.remove(o.price, o.id)
        self._release(o, o.remaining)
        o.qty = o.filled + qty
        o.price = price
        o.remaining = 0
        if self.has_funds:
            a = self.acct(o.account or "")
            if o.side == "sell":
                a["asset"] -= qty
                a["asset_reserved"] += qty
            else:
                a["cash"] -= price * qty
                a["cash_reserved"] += price * qty
                o.reserved_price = price
        self.seq += 1
        o.seq = self.seq
        fills = self._execute(o, qty)
        self._trigger_stops()
        return {"ok": True, "status": o.status, "fills": fills, "remaining": o.remaining}

    def _would_cross_excluding(self, o: Order, price: int) -> bool:
        # The order itself is on its own side, never the opposite side, so a plain check works.
        return self._would_cross(o.side, price)

    # -- queries --------------------------------------------------------------------------------
    def op_order(self, req: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "order": self._existing(req).view()}

    def op_book(self, req: dict[str, Any]) -> dict[str, Any]:
        depth = self._int(req, "depth", 1, DEPTH_MAX) if "depth" in req else DEFAULT_DEPTH

        def levels(side: Side) -> list[list[int]]:
            out: list[list[int]] = []
            for price in side.prices_best_first()[:depth]:
                ids = side.levels[price]
                out.append([price, sum(self.orders[i].remaining for i in ids), len(ids)])
            return out

        return {"ok": True, "bids": levels(self.bids), "asks": levels(self.asks), "last": self.last}

    def op_balance(self, req: dict[str, Any]) -> dict[str, Any]:
        name = req.get("account")
        if not isinstance(name, str) or not ID_RE.match(name):
            raise invalid("account must be 1-32 characters [A-Za-z0-9_-]")
        a = self.acct(name)
        return {
            "ok": True,
            "cash": {"available": a["cash"], "reserved": a["cash_reserved"]},
            "asset": {"available": a["asset"], "reserved": a["asset_reserved"]},
        }


def instance_path() -> str:
    return os.environ.get("INSTANCE_JSON") or str(Path(__file__).with_name("instance.json"))


def main() -> None:
    with open(instance_path(), encoding="utf-8") as fh:
        inst = json.load(fh)
    engine = Engine(inst["limits"])
    out = sys.stdout
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            resp: dict[str, Any] = {
                "ok": False,
                "error": "invalid_request",
                "message": "not valid JSON",
            }
        else:
            resp = engine.handle(req)
        out.write(json.dumps(resp) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
