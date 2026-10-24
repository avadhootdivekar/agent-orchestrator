"""`order-book` family (T-Pf4Mix): a single-instrument limit order book over JSON lines.

Pure in-memory engine work -- data structures (price levels with FIFO queues), an exact matching
algorithm and many interacting rules -- with no persistence or HTTP. Every expected response in
``probes.json`` is computed by replaying the probe's requests through the reference ``Engine``
(``reference/orderbook_server.py``, the same file the grader runs), so spec, probes and reference
cannot drift apart; what can drift is the prose, which is why every rule below is stated once,
precisely, in ``render_spec``.

Difficulty: ``entities`` = feature tier (0: limit/IOC, cancel, queries; 1: + market orders and FOK;
2: + accounts with self-trade prevention, post-only, replace; 3: + stop orders and funds
reservation); ``traps`` = number of emphasised trap scenarios; ``concurrency`` = number and length
of seeded random "fuzz" scenarios in the core group (the jsonl contract has a single client, so
the axis is reused for interaction depth); ``scale`` = perf book depth.
"""

from __future__ import annotations

import importlib.util
import random
import sys
from collections.abc import Sequence
from functools import cache
from types import ModuleType
from typing import Any

from . import dsl
from .base import Difficulty, Family, Instance, register_family
from .dsl import jsonl_step, mix_entry, perf_spec, probe
from .refs import REFERENCE_DIR, reference_files
from .skeleton import allow_list_text, resolve_allow, run_contract_text, skeleton_files
from .traps import trap_count

FAMILY_NAME = "order-book"
REFERENCE_PROGRAM = "orderbook_server.py"
STP_POLICIES = ("cancel_resting", "cancel_incoming")
START_CASH_CHOICES = (5_000_000, 8_000_000, 12_000_000)
START_ASSET_CHOICES = (300_000, 500_000)  # >= the largest perf load (100k at scale 3)
FUZZ_PROBES_BY_LEVEL = (2, 3, 4, 6)
FUZZ_LENGTH_BY_LEVEL = (40, 60, 80, 120)
MID_PRICE_RANGE = (500, 5000)
ACCOUNTS = ("acct-a", "acct-b", "acct-c")
PERF_ACCOUNT = "perf-maker"
PERF_TAKER = "perf-taker"
TRAP_TIER: dict[str, int] = {
    "invalid_input_4xx": 0,
    "fifo_within_level": 0,
    "maker_price": 0,
    "partial_fill_rests": 0,
    "ioc_remainder": 0,
    "fok_atomic": 1,
    "market_empty_book": 1,
    "post_only_cross": 2,
    "replace_priority": 2,
    "self_trade": 2,
    "stop_cascade": 3,
    "price_improvement_refund": 3,
    "cancel_releases_funds": 3,
}

JsonDict = dict[str, Any]


@cache
def _engine_module() -> ModuleType:
    path = REFERENCE_DIR / REFERENCE_PROGRAM
    spec = importlib.util.spec_from_file_location("ao_bench_orderbook_reference", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def new_engine(limits: JsonDict) -> Any:
    return _engine_module().Engine(limits)


def scenario(limits: JsonDict, requests: Sequence[Any]) -> list[JsonDict]:
    """Steps whose expectations are the reference engine's responses to `requests`."""
    engine = new_engine(limits)
    steps: list[JsonDict] = []
    for req in requests:
        resp = engine.handle(req)
        if resp.get("ok") is False:
            resp = {"ok": False, "error": resp["error"]}  # the message text is free-form
        steps.append(jsonl_step(req, match=resp))
    return steps


# ---------------------------------------------------------------------------------------------
# SPEC.md
# ---------------------------------------------------------------------------------------------


def _md_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(r) + " |" for r in rows)
    return "\n".join(lines)


def render_spec(limits: JsonDict, language: str, allow: Sequence[str]) -> str:
    tier = limits["tier"]
    place_rows = [
        ["`id`", "string, required", "1-32 characters from `A-Z a-z 0-9 _ -`; unique across "
         "every order ever accepted (a rejected request does not use up its id)"],
        ["`side`", "string, required", "`buy` or `sell`"],
        ["`type`", "string, optional", "`limit` (default)" + (" or `market`" if tier >= 1 else "")],
        ["`price`", "integer", "required for limit orders, 0 to 1000000000; "
         + ("must be absent for market orders" if tier >= 1 else "")],
        ["`qty`", "integer, required", "1 to 1000000000"],
        ["`tif`", "string, optional", "time in force: `GTC` (default for limit), `IOC`"
         + (", `FOK`; market orders accept `IOC` (their default) or `FOK`, never `GTC`"
            if tier >= 1 else "")],
    ]  # fmt: skip
    if tier >= 2:
        place_rows += [
            ["`account`", "string, required", "1-32 characters from `A-Z a-z 0-9 _ -`"],
            ["`post_only`", "boolean, optional", "default false; only allowed on GTC limit orders"],
        ]
    if tier >= 3:
        place_rows.append(
            [
                "`stop`",
                "integer, optional",
                "0 to 1000000000: makes this a stop order (below); "
                "not allowed together with `post_only: true`",
            ]  # fmt: skip
        )
    errors = [
        ["`invalid_request`", "the line is not a JSON object with a string `op`, or a field is "
         "missing, has the wrong type or is out of range, or a field value is not supported"],
        ["`unknown_op`", "`op` is not one of the operations below"],
        ["`duplicate_id`", "`place` with an id that an accepted order already used"],
        ["`unknown_order`", "`cancel`/`order`" + ("/`replace`" if tier >= 2 else "")
         + " with an id that no accepted order has"],
        ["`not_open`", "`cancel` of an order that is no longer open"
         + (" or pending" if tier >= 3 else "")
         + ("; `replace` of an order that is not an open limit order" if tier >= 2 else "")],
    ]  # fmt: skip
    if tier >= 2:
        errors.append(
            ["`would_cross`", "a post-only order (or the replacement of one) would trade"]
        )
    if tier >= 3:
        errors.append(["`insufficient_funds`", "the account cannot cover the reservation (below)"])

    ops = [
        '### `ping`\n\n`{"op": "ping"}` → `{"ok": true}`.',
        "### `place`\n\nPlaces an order. Fields:\n\n"
        + _md_table(["Field", "Type", "Rules"], place_rows)
        + '\n\nResponse: `{"ok": true, "status": S, "fills": [...], "remaining": R}` where '
        "`fills` lists the trades this order made **while it was being placed**, in the order "
        'they happened, each `{"maker": <resting order id>, "price": P, "qty": Q}`; '
        "`status` and `remaining` describe the order right after placement (see Order status).",
        '### `cancel`\n\n`{"op": "cancel", "id": ...}` cancels an open'
        + (" or pending" if tier >= 3 else "")
        + " order: it leaves the book immediately, its status becomes `cancelled` and its "
        '`remaining` 0. Response `{"ok": true}`.',
        '### `order`\n\n`{"op": "order", "id": ...}` → `{"ok": true, "order": {"id", '
        '"side", "type", "price", "qty", "filled", "remaining", "status"}}` '
        "(`price` is `null` for market orders; `qty` is the original quantity, or after a "
        "replace `filled` + the new quantity).",
        '### `book`\n\n`{"op": "book", "depth": D}` (`depth` optional, 1-100, default 10) → '
        '`{"ok": true, "bids": [[price, qty, count], ...], "asks": [...], "last": L}`: '
        "at most D price levels per side, best first (bids highest price first, asks lowest "
        "first); `qty` is the sum of the remaining quantities resting at that price and `count` "
        "the number of orders; `last` is the price of the most recent trade, or `null` before "
        "the first trade.",
    ]
    if tier >= 2:
        ops.append(
            '### `replace`\n\n`{"op": "replace", "id": ..., "price": P, "qty": Q}` '
            "(`price` and/or `qty`; an absent one keeps its current value; `qty` is the new "
            "**remaining** quantity, 1 to 1000000000) changes an open limit order:\n\n"
            "- If the price is unchanged and the new quantity is not larger than the current "
            "remaining quantity, the order is reduced **in place and keeps its time priority**. "
            'Response `{"ok": true, "status": "open", "fills": [], "remaining": Q}`.\n'
            "- Otherwise the order loses its priority: it is taken off the book and immediately "
            "re-entered as if it were a new GTC limit order with the same id, side, account and "
            "post-only flag at the new price and quantity — so it may trade right away. Its "
            "`filled` count carries over. The response has the `place` shape and lists only the "
            "trades of the re-entry. If the re-entry is not allowed (`would_cross` for a "
            "post-only order"
            + (", `insufficient_funds`" if tier >= 3 else "")
            + ") the replace fails and the order stays exactly as it was."
        )
    if tier >= 3:
        ops.append(
            '### `balance`\n\n`{"op": "balance", "account": A}` → `{"ok": true, '
            '"cash": {"available": X, "reserved": Y}, "asset": {"available": U, '
            '"reserved": V}}` (see Funds).'
        )

    matching = [
        "Prices and quantities are integers. A **buy** order can trade with resting **sell** "
        "orders priced at or below its limit; a **sell** with resting buys priced at or above "
        "its limit; a market order with any resting order on the other side.",
        "**Price-time priority.** An incoming order always trades with the best-priced resting "
        "order first (lowest ask for a buy, highest bid for a sell) and, within one price, with "
        "the order that has rested there longest (first in, first out). Partial fills keep "
        "their place in the queue.",
        "Every trade happens at the **resting (maker) order's price**, never the incoming "
        "order's limit. The trade quantity is the smaller of the two remaining quantities; a "
        "resting order that is completely filled leaves the book.",
        "Matching continues level by level until the incoming order is filled or no acceptable "
        "resting order is left. What happens to the unfilled rest depends on the time in "
        "force: `GTC` limit orders rest on the book at their limit price, behind every order "
        "already resting there; `IOC` orders (and market orders) are cancelled instead"
        + ("; a `FOK` order is all-or-nothing — see below" if tier >= 1 else "")
        + ".",
    ]
    if tier >= 1:
        matching.append(
            "**FOK (fill or kill).** Before trading anything, the engine checks whether the "
            "whole quantity can be filled right now under the rules above"
            + ((" (resting orders of the same account do not count: they would be cancelled "
                "— see self-trade prevention)" if limits["stp"] == "cancel_resting" else
                " (if a resting order of the same account would be reached before the whole "
                "quantity is filled, it cannot be — see self-trade prevention)")
               if tier >= 2 else "")
            + (" and, for a market buy, whether the account's available cash covers the whole "
               "cost" if tier >= 3 else "")
            + ". If not, the order is accepted but immediately cancelled with no trades and "
            "**nothing else changes** — not the book, not any other order"
            + (", not any balance" if tier >= 3 else "")
            + ". If yes, it trades exactly like an IOC order."
        )  # fmt: skip
    if tier >= 2:
        policy = limits["stp"]
        matching.append(
            "**Self-trade prevention.** Two orders of the same `account` never trade with each "
            "other. When the next resting order an incoming order would trade with belongs to "
            "the same account, "
            + ("that **resting** order is cancelled (it leaves the book with status "
               "`cancelled`) and matching continues with the next resting order."
               if policy == "cancel_resting" else
               "matching **stops**: the incoming order trades no further and its unfilled rest "
               "is cancelled (it does not rest on the book, even if it is a GTC order); the "
               "resting order is left untouched.")
        )  # fmt: skip
        matching.append(
            "**Post-only.** A post-only order is rejected with `would_cross` (and nothing "
            "changes) if, at the moment it arrives, it would trade with any resting order of "
            "the other side — i.e. a post-only buy priced at or above the best ask, or a "
            "post-only sell priced at or below the best bid (whatever the accounts)."
        )
    if tier >= 3:
        matching.append(
            "**Stop orders.** A `place` with `stop` is accepted with status `pending`, does not "
            "rest on the book and never appears in `book`. A pending **buy** stop triggers when "
            "the last trade price is **greater than or equal to** its stop price; a pending "
            "**sell** stop when it is **less than or equal to** it. Trigger checks happen after "
            "every request that places or replaces an order has finished all of its own "
            "matching (including immediately after a stop order is placed, if the last price "
            "already meets its condition). A triggered order is processed exactly like a newly "
            "placed order of its `type`, `price`, `qty` and `tif` (its fills are not part of any "
            "response; use `order` to see them). If several stops are triggered, the one placed "
            "first goes first; after each triggered order is processed the conditions are "
            "checked again (a triggered order's trades can trigger further stops), until no "
            "pending stop meets its condition. The response to placing a stop order is always "
            '`{"ok": true, "status": "pending", "fills": [], "remaining": <qty>}`, '
            "even when it is triggered right away."
        )
    status_lines = [
        "- `open`: resting on the book with `remaining` > 0 (possibly partially filled).",
        "- `filled`: completely filled; `remaining` is 0.",
        "- `cancelled`: no longer on the book and not completely filled (cancelled by request, "
        "an IOC/market rest, a killed FOK"
        + (", self-trade prevention" if tier >= 2 else "")
        + "); `remaining` is 0 and `filled` shows what traded.",
    ]
    if tier >= 3:
        status_lines.append(
            "- `pending`: a stop order not yet triggered; `remaining` is its quantity."
        )

    parts = [
        "# Specification: limit order book over JSON lines",
        "",
        "Implement the order-matching engine described here, in this directory. Everything in "
        "this document is a requirement. The engine is checked only through its standard input "
        "and output, and keeps all state in memory (it starts empty every time it starts).",
        "",
        "## 1. Run contract",
        "",
        run_contract_text("jsonl", language),
        "",
        "## 2. Allowed dependencies",
        "",
        allow_list_text(language, allow),
        "",
        "## 3. Protocol",
        "",
        "- Every request is one line holding a JSON object with a string field `op`; every "
        "response is one line holding a JSON object with a boolean field `ok`. Fields that are "
        "not listed for an operation are ignored. Blank lines are skipped without a response; "
        "a line that is not valid JSON gets an `invalid_request` error.",
        '- A failed request answers `{"ok": false, "error": "<code>", "message": "<text>"}` '
        "and changes nothing. Checks happen in this order: request shape and field validation "
        "(`invalid_request`), then `duplicate_id` / `unknown_order`, then `not_open`"
        + (", then `insufficient_funds`, then `would_cross`" if tier >= 3 else "")
        + (", then `would_cross`" if tier == 2 else "")
        + ".",
        "",
        _md_table(["Error code", "When"], errors),
        "",
        "## 4. Operations",
        "",
        "\n\n".join(ops),
        "",
        "## 5. Matching rules",
        "",
        "\n\n".join(matching),
        "",
        "## 6. Order status",
        "",
        "\n".join(status_lines),
    ]
    if tier >= 3:
        parts += [
            "",
            "## 7. Funds",
            "",
            f"Every account starts with {limits['start_cash']} cash available and "
            f"{limits['start_asset']} asset units available, nothing reserved (an account "
            "exists as soon as it is named). Placing an order reserves what it could need, "
            "moving it from available to reserved; `insufficient_funds` if the available "
            "amount is too small:",
            "",
            "- a **limit buy** (including a stop limit buy) reserves `price × qty` cash;",
            "- a **sell** (limit, market or stop) reserves `qty` asset units;",
            "- a **market buy** reserves nothing; instead each of its trades is limited to as "
            "many units as the account's *available* cash can pay for at that trade's price "
            "(rounded down); when it cannot afford a single unit it stops and its rest is "
            "cancelled.",
            "",
            "On every trade at price P for Q units: the seller's reserved asset falls by Q and "
            "its available cash rises by P × Q; the buyer's available asset rises by Q; a limit "
            "buyer's reserved cash falls by `limit × Q` and the difference `(limit − P) × Q` "
            "goes back to its available cash; a market buyer's available cash falls by P × Q. "
            "Whenever an order (or part of it) is cancelled — by request, as an IOC/market/FOK "
            "rest, by self-trade prevention, or by a replace — the reservation for its "
            "unfilled quantity returns to available. A replace that re-enters the order "
            "releases the old reservation and makes the new one; it fails with "
            "`insufficient_funds` (changing nothing) if available plus the released amount "
            "cannot cover the new one; an in-place reduction just releases the difference.",
        ]
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------------------------


class Probes:
    def __init__(self, rng: random.Random, limits: JsonDict, traps: Sequence[str], fuzz: int,
                 fuzz_len: int) -> None:  # fmt: skip
        self.rng = rng
        self.limits = limits
        self.traps = list(traps)
        self.tier: int = limits["tier"]
        self.mid: int = limits["mid"]
        self.fuzz, self.fuzz_len = fuzz, fuzz_len
        self._n = 0

    def oid(self, prefix: str = "o") -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    def place(self, side: str, price: int | None, qty: int, **extra: Any) -> JsonDict:
        req: JsonDict = {"op": "place", "id": extra.pop("id", None) or self.oid(), "side": side}
        if price is None:
            req["type"] = "market"
        else:
            req["price"] = price
        req["qty"] = qty
        if self.tier >= 2:
            req["account"] = extra.pop("account", ACCOUNTS[0])
        req.update(extra)
        return req

    def scen(self, pid: str, reqs: Sequence[JsonDict], trap: str | None = None) -> JsonDict:
        return probe(pid, scenario(self.limits, reqs), trap=trap)

    @staticmethod
    def book(depth: int = 10) -> JsonDict:
        return {"op": "book", "depth": depth}

    @staticmethod
    def order(oid: str) -> JsonDict:
        return {"op": "order", "id": oid}

    # -- core -------------------------------------------------------------------------------
    def core(self) -> list[JsonDict]:
        m = self.mid
        a, b, c = ACCOUNTS
        out: list[JsonDict] = []
        out.append(self.scen("core-ping", [{"op": "ping"}]))
        out.append(self.scen("core-rest-and-book", [
            self.place("sell", m + 2, 5, id="s1", account=a),
            self.place("sell", m + 1, 3, id="s2", account=a),
            self.place("buy", m - 1, 4, id="b1", account=b),
            self.place("buy", m - 2, 6, id="b2", account=b),
            self.book(), self.order("s2"),
        ]))  # fmt: skip
        out.append(self.scen("core-cross-levels", [
            self.place("sell", m + 1, 3, id="s1", account=a),
            self.place("sell", m + 2, 4, id="s2", account=a),
            self.place("sell", m + 2, 2, id="s3", account=c),
            self.place("buy", m + 2, 8, id="b1", account=b),
            self.book(), self.order("s2"), self.order("b1"),
        ]))  # fmt: skip
        out.append(self.scen("core-cancel", [
            self.place("buy", m, 5, id="b1", account=b),
            {"op": "cancel", "id": "b1"}, {"op": "cancel", "id": "b1"},
            {"op": "cancel", "id": "nope"}, self.order("b1"), self.book(),
        ]))  # fmt: skip
        out.append(self.scen("core-ioc", [
            self.place("sell", m, 2, id="s1", account=a),
            self.place("buy", m, 5, id="b1", tif="IOC", account=b),
            self.book(), self.order("b1"),
        ]))  # fmt: skip
        out.append(self.scen("core-validation", [
            {"op": "place", "id": "x1", "side": "hold", "price": m, "qty": 1},
            {"op": "place", "id": "x2", "side": "buy", "price": m, "qty": 0},
            {"op": "place", "id": "x 3", "side": "buy", "price": m, "qty": 1},
            {"op": "fly"}, self.place("buy", m, 1, id="d1", account=b),
            self.place("buy", m, 1, id="d1", account=b),
        ]))  # fmt: skip
        if self.tier >= 1:
            out.append(self.scen("core-market-and-fok", [
                self.place("sell", m, 3, id="s1", account=a),
                self.place("sell", m + 1, 3, id="s2", account=a),
                self.place("buy", None, 4, id="m1", account=b),
                self.place("buy", m + 1, 5, id="f1", tif="FOK", account=b),
                self.place("buy", m + 1, 2, id="f2", tif="FOK", account=b),
                self.book(), self.order("f1"),
            ]))  # fmt: skip
        if self.tier >= 2:
            out.append(self.scen("core-replace-and-post-only", [
                self.place("buy", m, 5, id="b1", account=b),
                self.place("buy", m, 5, id="b2", account=c),
                {"op": "replace", "id": "b1", "qty": 3},
                self.place("sell", m + 3, 2, id="p1", account=a, post_only=True),
                self.place("sell", m, 2, id="p2", account=a, post_only=True),
                self.place("sell", m, 4, id="s1", account=a),
                self.book(), self.order("b1"),
            ]))  # fmt: skip
        if self.tier >= 3:
            out.append(self.scen("core-funds-and-stop", [
                {"op": "balance", "account": a},
                self.place("sell", m, 4, id="s1", account=a),
                self.place("buy", m + 5, 3, id="b1", account=b),
                {"op": "balance", "account": a}, {"op": "balance", "account": b},
                self.place("buy", None, 2, id="st1", account=c, stop=m),
                self.order("st1"), self.book(),
            ]))  # fmt: skip
        out += [self.fuzz_probe(i) for i in range(self.fuzz)]
        return out

    # -- fuzz -------------------------------------------------------------------------------
    def _random_request(self, live: list[str], used: list[str]) -> JsonDict:
        r = self.rng
        roll = r.random()
        m = self.mid
        if roll < 0.06:
            return r.choice([
                {"op": "place", "id": self.oid("z"), "side": "buy", "price": -1, "qty": 1},
                {"op": "place", "id": self.oid("z"), "side": "sell", "price": m, "qty": 1.5},
                {"op": "cancel", "id": "missing"},
                {"op": "order", "id": r.choice(used) if used else "missing"},
            ])  # fmt: skip
        if roll < 0.14 and live:
            return {"op": "cancel", "id": r.choice(live)}
        if roll < 0.20:
            return self.book(r.randint(1, 5))
        if roll < 0.25 and used:
            return self.order(r.choice(used))
        if self.tier >= 2 and roll < 0.31 and live:
            req: JsonDict = {"op": "replace", "id": r.choice(live)}
            if r.random() < 0.6:
                req["qty"] = r.randint(1, 8)
            if r.random() < 0.5 or "qty" not in req:
                req["price"] = m + r.randint(-4, 4)
            return req
        if self.tier >= 3 and roll < 0.34:
            return {"op": "balance", "account": r.choice(ACCOUNTS)}
        side = r.choice(("buy", "sell"))
        extra: JsonDict = {"account": r.choice(ACCOUNTS)}
        market = self.tier >= 1 and r.random() < 0.12
        skew = -1 if side == "buy" else 1
        price = None if market else m + skew * r.randint(-3, 5)
        if not market:
            tifs = ["GTC"] * 6 + ["IOC"] + (["FOK"] if self.tier >= 1 else [])
            tif = r.choice(tifs)
            if tif != "GTC":
                extra["tif"] = tif
            elif self.tier >= 2 and r.random() < 0.15:
                extra["post_only"] = True
        elif r.random() < 0.3:
            extra["tif"] = "FOK"
        if self.tier >= 3 and r.random() < 0.12 and not extra.get("post_only"):
            extra["stop"] = m + r.randint(-4, 4)
        qty = r.randint(1, 12) if r.random() < 0.9 else r.randint(500, 4000)
        return self.place(side, price, qty, id=self.oid("f"), **extra)

    def fuzz_probe(self, index: int) -> JsonDict:
        live: list[str] = []
        used: list[str] = []
        reqs: list[JsonDict] = []
        engine = new_engine(self.limits)
        for _ in range(self.fuzz_len):
            req = self._random_request(live, used)
            resp = engine.handle(req)
            reqs.append(req)
            if req.get("op") == "place" and resp.get("ok"):
                used.append(req["id"])
            live = [i for i in used if engine.orders[i].status in ("open", "pending")]
        reqs.append(self.book(20))
        return probe(f"core-fuzz-{index + 1}", scenario(self.limits, reqs))

    # -- traps ------------------------------------------------------------------------------
    def trap_probes(self) -> list[JsonDict]:
        builders = {
            "invalid_input_4xx": self._t_invalid,
            "fifo_within_level": self._t_fifo,
            "maker_price": self._t_maker_price,
            "partial_fill_rests": self._t_partial_rests,
            "ioc_remainder": self._t_ioc,
            "fok_atomic": self._t_fok,
            "market_empty_book": self._t_market_empty,
            "post_only_cross": self._t_post_only,
            "replace_priority": self._t_replace,
            "self_trade": self._t_self_trade,
            "stop_cascade": self._t_stop_cascade,
            "price_improvement_refund": self._t_refund,
            "cancel_releases_funds": self._t_cancel_funds,
        }
        return [builders[t]() for t in self.traps]

    def _t_invalid(self) -> JsonDict:
        m = self.mid
        bad: list[Any] = [
            [1, 2], "place", {"op": 7},
            {"op": "place", "id": "i1", "side": "buy", "price": m, "qty": True},
            {"op": "place", "id": "i2", "side": "buy", "price": "10", "qty": 1},
            {"op": "place", "id": "i3", "side": "buy", "price": 1_000_000_001, "qty": 1},
            {"op": "place", "id": "a" * 33, "side": "buy", "price": m, "qty": 1},
            {"op": "place", "id": "i4", "side": "buy", "price": m, "qty": 1, "tif": "DAY"},
            {"op": "book", "depth": 0},
        ]  # fmt: skip
        if self.tier >= 1:
            bad.append({"op": "place", "id": "i5", "side": "buy", "type": "market", "price": m,
                        "qty": 1})  # fmt: skip
            bad.append({"op": "place", "id": "i6", "side": "buy", "type": "market", "qty": 1,
                        "tif": "GTC"})  # fmt: skip
        reqs = [r if not (isinstance(r, dict) and self.tier >= 2 and r.get("op") == "place")
                else {**r, "account": ACCOUNTS[0]} for r in bad]  # fmt: skip
        reqs.append(self.book())
        return self.scen("trap-invalid-input", reqs, "invalid_input_4xx")

    def _t_fifo(self) -> JsonDict:
        m = self.mid
        a, b, c = ACCOUNTS
        return self.scen("trap-fifo", [
            self.place("sell", m, 4, id="s1", account=a),
            self.place("sell", m, 4, id="s2", account=c),
            self.place("sell", m, 4, id="s3", account=a),
            self.place("buy", m, 6, id="b1", account=b),
            self.place("buy", m, 3, id="b2", account=b),
            self.order("s1"), self.order("s2"), self.order("s3"), self.book(),
        ], "fifo_within_level")  # fmt: skip

    def _t_maker_price(self) -> JsonDict:
        m = self.mid
        a, b, _ = ACCOUNTS
        return self.scen("trap-maker-price", [
            self.place("buy", m - 3, 2, id="b1", account=b),
            self.place("sell", m - 10, 3, id="s1", account=a),
            self.place("sell", m + 4, 2, id="s2", account=a),
            self.place("buy", m + 9, 1, id="b2", account=b),
            self.book(),
        ], "maker_price")  # fmt: skip

    def _t_partial_rests(self) -> JsonDict:
        m = self.mid
        a, b, c = ACCOUNTS
        return self.scen("trap-partial-rests", [
            self.place("sell", m, 2, id="s1", account=a),
            self.place("buy", m + 2, 7, id="b1", account=b),
            self.place("buy", m + 2, 1, id="b2", account=c),
            self.place("sell", m + 2, 3, id="s2", account=a),
            self.order("b1"), self.book(),
        ], "partial_fill_rests")  # fmt: skip

    def _t_ioc(self) -> JsonDict:
        m = self.mid
        a, b, _ = ACCOUNTS
        return self.scen("trap-ioc-remainder", [
            self.place("buy", m, 3, id="i1", tif="IOC", account=b),
            self.place("sell", m, 2, id="s1", account=a),
            self.place("buy", m + 1, 5, id="i2", tif="IOC", account=b),
            self.order("i1"), self.order("i2"), self.book(),
        ], "ioc_remainder")  # fmt: skip

    def _t_fok(self) -> JsonDict:
        m = self.mid
        a, b, c = ACCOUNTS
        reqs = [
            self.place("sell", m, 3, id="s1", account=a),
            self.place("sell", m + 1, 3, id="s2", account=c),
        ]
        if self.tier >= 2:  # own resting liquidity must not count towards the FOK check
            reqs.append(self.place("sell", m + 1, 4, id="s3", account=b))
        reqs += [
            self.place("buy", m + 1, 7, id="f1", tif="FOK", account=b),
            self.book(), self.order("s1"),
            self.place("buy", m + 1, 6, id="f2", tif="FOK", account=b),
            self.book(),
        ]  # fmt: skip
        return self.scen("trap-fok-atomic", reqs, "fok_atomic")

    def _t_market_empty(self) -> JsonDict:
        m = self.mid
        a, b, _ = ACCOUNTS
        return self.scen("trap-market-empty", [
            self.place("buy", None, 5, id="m1", account=b),
            self.order("m1"), self.book(),
            self.place("sell", m, 2, id="s1", account=a),
            self.place("buy", None, 5, id="m2", account=b),
            self.order("m2"), self.book(),
        ], "market_empty_book")  # fmt: skip

    def _t_post_only(self) -> JsonDict:
        m = self.mid
        a, b, _ = ACCOUNTS
        return self.scen("trap-post-only", [
            self.place("sell", m, 3, id="s1", account=a),
            self.place("buy", m, 1, id="p1", account=a, post_only=True),
            self.place("buy", m - 1, 2, id="p2", account=b, post_only=True),
            {"op": "replace", "id": "p2", "price": m + 1},
            {"op": "replace", "id": "p2", "price": m - 2},
            self.place("buy", m, 1, id="p1", account=b),
            self.book(), self.order("p2"),
        ], "post_only_cross")  # fmt: skip

    def _t_replace(self) -> JsonDict:
        m = self.mid
        a, b, c = ACCOUNTS
        return self.scen("trap-replace-priority", [
            self.place("buy", m, 5, id="b1", account=b),
            self.place("buy", m, 5, id="b2", account=c),
            {"op": "replace", "id": "b1", "qty": 4},
            self.place("sell", m, 2, id="s1", account=a),
            {"op": "replace", "id": "b2", "qty": 9},
            self.place("sell", m, 3, id="s2", account=a),
            {"op": "replace", "id": "b1", "price": m - 1},
            self.order("b1"), self.order("b2"), self.book(),
        ], "replace_priority")  # fmt: skip

    def _t_self_trade(self) -> JsonDict:
        m = self.mid
        a, b, _ = ACCOUNTS
        return self.scen("trap-self-trade", [
            self.place("sell", m, 2, id="s1", account=a),
            self.place("sell", m, 3, id="s2", account=b),
            self.place("sell", m + 1, 2, id="s3", account=b),
            self.place("buy", m + 1, 6, id="b1", account=a),
            self.order("s1"), self.order("b1"), self.book(),
        ], "self_trade")  # fmt: skip

    def _t_stop_cascade(self) -> JsonDict:
        m = self.mid
        a, b, c = ACCOUNTS
        return self.scen("trap-stop-cascade", [
            self.place("sell", m + 1, 2, id="s1", account=a),
            self.place("sell", m + 3, 2, id="s2", account=a),
            self.place("sell", m + 6, 5, id="s3", account=a),
            self.place("buy", None, 2, id="t1", account=b, stop=m + 3),
            self.place("buy", None, 1, id="t2", account=c, stop=m + 1),
            self.place("buy", m + 1, 2, id="b1", account=b),
            self.order("t1"), self.order("t2"), self.book(),
            self.place("sell", None, 1, id="t3", account=c, stop=m + 10),
            self.order("t3"),
        ], "stop_cascade")  # fmt: skip

    def _t_refund(self) -> JsonDict:
        m = self.mid
        a, b, _ = ACCOUNTS
        return self.scen("trap-price-improvement", [
            self.place("sell", m, 3, id="s1", account=a),
            self.place("buy", m + 50, 5, id="b1", account=b),
            {"op": "balance", "account": b}, {"op": "balance", "account": a},
        ], "price_improvement_refund")  # fmt: skip

    def _t_cancel_funds(self) -> JsonDict:
        m = self.mid
        a, b, _ = ACCOUNTS
        big = self.limits["start_cash"] // (m + 10)
        return self.scen("trap-cancel-releases", [
            self.place("buy", m + 10, big, id="b1", account=b),
            self.place("buy", m, 2, id="b2", account=b),
            {"op": "cancel", "id": "b1"},
            self.place("buy", m, 2, id="b3", account=b),
            self.place("sell", m, 1, id="s1", account=a, tif="IOC"),
            {"op": "balance", "account": b}, {"op": "balance", "account": a},
        ], "cancel_releases_funds")  # fmt: skip

    # -- perf -------------------------------------------------------------------------------
    def perf(self, scale: int) -> JsonDict:
        maker: JsonDict = {"op": "place", "id": "p{{i}}", "side": "sell", "price": "{{int:i}}",
                           "qty": 1}  # fmt: skip
        taker: JsonDict = {"op": "place", "id": "t{{rand}}", "side": "buy", "price": "{{int:i}}",
                           "qty": 1, "tif": "IOC"}  # fmt: skip
        if self.tier >= 2:
            maker["account"], taker["account"] = PERF_ACCOUNT, PERF_TAKER
        load = jsonl_step(maker, match={"ok": True}, repeat=1)
        anyok = {"ok": {"$type": "boolean"}}
        mix = [
            mix_entry(0.4, "book", "book_top", jsonl_step(self.book(5), match=anyok)),
            mix_entry(0.3, "take", "match", jsonl_step(taker, match=anyok)),
            mix_entry(
                0.3, "cancel", "cancel", jsonl_step({"op": "cancel", "id": "p{{i}}"}, match=anyok)
            ),  # fmt: skip
        ]
        return perf_spec(scale, load, mix)


def _build_limits(rng: random.Random, difficulty: Difficulty) -> JsonDict:
    return {
        "tier": difficulty.entities,
        "stp": rng.choice(STP_POLICIES),
        "mid": rng.randint(*MID_PRICE_RANGE),
        "start_cash": rng.choice(START_CASH_CHOICES),
        "start_asset": rng.choice(START_ASSET_CHOICES),
        "max_records": dsl.PERF_SIZES_BY_SCALE[difficulty.scale][-1],
    }


@register_family
class OrderBookFamily(Family):
    name = FAMILY_NAME
    contract = "jsonl"
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
        tier = difficulty.entities
        allowed = [k for k, t in TRAP_TIER.items() if t <= tier]
        shuffled = list(allowed)
        rng.shuffle(shuffled)
        chosen = set(shuffled[: trap_count(difficulty.traps)])
        traps = [k for k in TRAP_TIER if k in chosen]
        limits = _build_limits(rng, difficulty)
        builder = Probes(
            rng,
            limits,
            traps,
            FUZZ_PROBES_BY_LEVEL[difficulty.concurrency],
            FUZZ_LENGTH_BY_LEVEL[difficulty.concurrency],
        )
        probes = dsl.probes_doc(
            "jsonl", language, builder.core(), builder.trap_probes(), builder.perf(difficulty.scale)
        )
        return Instance(
            family=self.name,
            seed=seed,
            difficulty=difficulty,
            language=language,
            contract="jsonl",
            entities=[],
            traps=traps,
            limits=limits,
            allow=allow_list,
            spec_md=render_spec(limits, language, allow_list),
            skeleton=skeleton_files("jsonl", language, allow_list, title="order book engine"),
            probes=probes,
            reference=reference_files(REFERENCE_PROGRAM),
        )


__all__ = ["FAMILY_NAME", "OrderBookFamily", "new_engine", "render_spec", "scenario"]
