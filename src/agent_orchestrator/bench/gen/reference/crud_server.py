"""Reference implementation of the `crud-relational` family (grader-owned, never agent-visible).

Stdlib only, in-memory, parameterised by ``instance.json`` (path in ``$INSTANCE_JSON``).  Every
lookup is a dict/bisect index so the scaling exponent is ~0; one global lock serialises mutations
(that is what makes uniqueness, idempotency and If-Match trivially race-free here).

This file is copied verbatim into ``<grading_dir>/reference/`` by the generated provider.
"""

from __future__ import annotations

import base64
import bisect
import json
import os
import re
import signal
import threading
import unicodedata
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, urlsplit

DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
INT_RE = re.compile(r"^-?[0-9]+$")
UINT_RE = re.compile(r"^[0-9]+$")
IDEMPOTENCY_KEY_MAX = 255
LISTEN_BACKLOG = 256


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def bad(message: str) -> ApiError:
    return ApiError(400, "invalid_request", message)


def casefold_key(value: str) -> str:
    """NFKC(casefold(NFKC(value))) -- the caseless-matching recipe from the Unicode standard."""
    nfkc = unicodedata.normalize
    return nfkc("NFKC", nfkc("NFKC", value).casefold())


def now_iso() -> str:
    t = datetime.now(UTC)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def encode_cursor(key: tuple[str, str]) -> str:
    raw = json.dumps(list(key), separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(text: str) -> tuple[str, str]:
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
        a, b = json.loads(raw)
        if isinstance(a, str) and isinstance(b, str):
            return (a, b)
    except (ValueError, TypeError):
        pass
    raise bad("invalid cursor")


class Store:
    def __init__(self, inst: dict[str, Any]) -> None:
        self.lock = threading.RLock()
        self.page_max: int = inst["limits"]["page_size_max"]
        self.page_default: int = inst["limits"]["page_size_default"]
        self.entities: dict[str, dict[str, Any]] = {e["name"]: e for e in inst["entities"]}
        self.by_plural = {e["plural"]: e["name"] for e in inst["entities"]}
        # belongs_to relations: child -> list of {parent, fk, on_delete}
        self.parents: dict[str, list[dict[str, Any]]] = {n: [] for n in self.entities}
        self.children: dict[str, list[tuple[str, str, str]]] = {n: [] for n in self.entities}
        self.m2n: list[tuple[str, str]] = []
        for e in inst["entities"]:
            for rel in e.get("relations", []):
                if rel["kind"] == "belongs_to":
                    self.parents[e["name"]].append(rel)
                    self.children[rel["parent"]].append((e["name"], rel["fk"], rel["on_delete"]))
                elif rel["kind"] == "many_to_many" and e["name"] < rel["other"]:
                    self.m2n.append((e["name"], rel["other"]))
        self.rows: dict[str, dict[str, dict[str, Any]]] = {n: {} for n in self.entities}
        self.order: dict[str, list[tuple[str, str]]] = {n: [] for n in self.entities}
        self.unique: dict[tuple[str, str], dict[str, str]] = {}
        self.kids: dict[tuple[str, str], dict[str, set[str]]] = {}
        for n, e in self.entities.items():
            for f in e["fields"]:
                if f.get("unique"):
                    self.unique[(n, f["name"])] = {}
            for rel in self.parents[n]:
                self.kids[(n, rel["fk"])] = {}
        self.links: dict[tuple[str, str], dict[str, set[str]]] = {p: {} for p in self.m2n}
        self.links_rev: dict[tuple[str, str], dict[str, set[str]]] = {p: {} for p in self.m2n}
        self.idem: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}

    # ---- helpers -------------------------------------------------------------------------
    def uniq_key(self, entity: str, field: str, value: str) -> str:
        spec = next(f for f in self.entities[entity]["fields"] if f["name"] == field)
        return casefold_key(value) if spec.get("unique_casefold") else value

    def validate_value(self, f: dict[str, Any], v: Any) -> None:
        t, name = f["type"], f["name"]
        if t == "string":
            ok = isinstance(v, str) and 1 <= len(v) <= f["max_length"]
        elif t == "int":
            ok = type(v) is int and f["minimum"] <= v <= f["maximum"]
        elif t == "bool":
            ok = type(v) is bool
        elif t == "enum":
            ok = isinstance(v, str) and v in f["values"]
        elif t == "datetime":
            ok = isinstance(v, str) and bool(DATETIME_RE.match(v))
            if ok:
                try:
                    datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ")
                except ValueError:
                    ok = False
        else:  # pragma: no cover - generator bug
            raise AssertionError(t)
        if not ok:
            raise bad(f"invalid value for field '{name}'")

    # ---- CRUD ----------------------------------------------------------------------------
    def create(self, entity: str, body: Any, idem_key: str | None) -> tuple[int, dict[str, Any]]:
        e = self.entities[entity]
        if not isinstance(body, dict):
            raise bad("body must be a JSON object")
        if idem_key is not None and (not idem_key or len(idem_key) > IDEMPOTENCY_KEY_MAX):
            raise bad("invalid Idempotency-Key")
        fields = {f["name"]: f for f in e["fields"]}
        fks = {r["fk"]: r for r in self.parents[entity]}
        unknown = sorted(set(body) - set(fields) - set(fks))
        if unknown:
            raise bad(f"unknown field(s): {', '.join(unknown)}")
        fingerprint = json.dumps(body, sort_keys=True)
        with self.lock:
            if idem_key is not None:
                prior = self.idem.get((entity, idem_key))
                if prior is not None:
                    if prior[0] != fingerprint:
                        raise ApiError(
                            422,
                            "idempotency_key_reuse",
                            "Idempotency-Key reused with a different body",
                        )
                    return 201, prior[1]
            row: dict[str, Any] = {}
            for name, f in fields.items():
                if name in body and body[name] is not None:
                    self.validate_value(f, body[name])
                    row[name] = body[name]
                elif f.get("required"):
                    raise bad(f"field '{name}' is required")
                else:
                    row[name] = None
            for fk, rel in fks.items():
                pid = body.get(fk)
                if not isinstance(pid, str) or pid not in self.rows[rel["parent"]]:
                    raise bad(f"'{fk}' must be the id of an existing {rel['parent']}")
                row[fk] = pid
            self._check_unique(entity, row, own_id=None)
            rid = str(uuid.uuid4())
            created = now_iso()
            full = {"id": rid, "created_at": created, "version": 1}
            full.update(row)
            self._insert(entity, full)
            if idem_key is not None:
                self.idem[(entity, idem_key)] = (fingerprint, dict(full))
            return 201, full

    def _check_unique(self, entity: str, row: dict[str, Any], own_id: str | None) -> None:
        for (ent, name), index in self.unique.items():
            if ent != entity or row.get(name) is None:
                continue
            holder = index.get(self.uniq_key(entity, name, row[name]))
            if holder is not None and holder != own_id:
                raise ApiError(409, "conflict", f"'{name}' must be unique")

    def _insert(self, entity: str, full: dict[str, Any]) -> None:
        self.rows[entity][full["id"]] = full
        bisect.insort(self.order[entity], (full["created_at"], full["id"]))
        for (ent, name), index in self.unique.items():
            if ent == entity and full.get(name) is not None:
                index[self.uniq_key(entity, name, full[name])] = full["id"]
        for rel in self.parents[entity]:
            self.kids[(entity, rel["fk"])].setdefault(full[rel["fk"]], set()).add(full["id"])

    def get(self, entity: str, rid: str) -> dict[str, Any]:
        with self.lock:
            row = self.rows[entity].get(rid)
            if row is None:
                raise ApiError(404, "not_found", f"{entity} not found")
            return row

    def patch(self, entity: str, rid: str, body: Any, if_match: str | None) -> dict[str, Any]:
        with self.lock:
            row = self.get(entity, rid)
            expected: int | None = None
            if if_match is not None:
                text = if_match.strip().strip('"')
                if not UINT_RE.match(text):
                    raise bad("If-Match must be a version number")
                expected = int(text)
            if not isinstance(body, dict):
                raise bad("body must be a JSON object")
            fields = {f["name"]: f for f in self.entities[entity]["fields"]}
            unknown = sorted(set(body) - set(fields))
            if unknown:
                raise bad(f"unknown or read-only field(s): {', '.join(unknown)}")
            changes: dict[str, Any] = {}
            for name, value in body.items():
                f = fields[name]
                if value is None:
                    if f.get("required"):
                        raise bad(f"field '{name}' is required and cannot be null")
                else:
                    self.validate_value(f, value)
                changes[name] = value
            if expected is not None and expected != row["version"]:
                raise ApiError(412, "precondition_failed", "version mismatch")
            candidate = dict(row)
            candidate.update(changes)
            self._check_unique(entity, candidate, own_id=rid)
            for (ent, name), index in self.unique.items():
                if ent == entity and name in changes:
                    if row.get(name) is not None:
                        index.pop(self.uniq_key(entity, name, row[name]), None)
                    if changes[name] is not None:
                        index[self.uniq_key(entity, name, changes[name])] = rid
            candidate["version"] = row["version"] + 1
            self.rows[entity][rid] = candidate
            return candidate

    def delete(self, entity: str, rid: str) -> None:
        with self.lock:
            self.get(entity, rid)
            plan: list[tuple[str, str]] = []
            self._plan_delete(entity, rid, plan, set())
            for ent, i in plan:
                self._remove(ent, i)

    def _plan_delete(
        self, entity: str, rid: str, plan: list[tuple[str, str]], seen: set[tuple[str, str]]
    ) -> None:
        if (entity, rid) in seen:
            return
        seen.add((entity, rid))
        for child, fk, on_delete in self.children[entity]:
            kids = self.kids[(child, fk)].get(rid)
            if not kids:
                continue
            if on_delete == "restrict":
                raise ApiError(409, "conflict", f"{entity} still has {child} records")
            for kid in sorted(kids):
                self._plan_delete(child, kid, plan, seen)
        plan.append((entity, rid))

    def _remove(self, entity: str, rid: str) -> None:
        row = self.rows[entity].pop(rid)
        key = (row["created_at"], rid)
        pos = bisect.bisect_left(self.order[entity], key)
        del self.order[entity][pos]
        for (ent, name), index in self.unique.items():
            if ent == entity and row.get(name) is not None:
                index.pop(self.uniq_key(entity, name, row[name]), None)
        for rel in self.parents[entity]:
            holder = self.kids[(entity, rel["fk"])].get(row[rel["fk"]])
            if holder is not None:
                holder.discard(rid)
        for pair in self.m2n:
            a, b = pair
            if entity == a:
                for other in self.links[pair].pop(rid, set()):
                    self.links_rev[pair][other].discard(rid)
            elif entity == b:
                for other in self.links_rev[pair].pop(rid, set()):
                    self.links[pair][other].discard(rid)

    # ---- listing -------------------------------------------------------------------------
    def parse_paging(self, params: dict[str, str]) -> tuple[int, tuple[str, str] | None, str]:
        limit = self.page_default
        if "limit" in params:
            if not UINT_RE.match(params["limit"]) or not 1 <= int(params["limit"]) <= self.page_max:
                raise bad(f"limit must be an integer between 1 and {self.page_max}")
            limit = int(params["limit"])
        order = params.get("order", "asc")
        if order not in ("asc", "desc"):
            raise bad("order must be 'asc' or 'desc'")
        cursor = decode_cursor(params["cursor"]) if "cursor" in params else None
        return limit, cursor, order

    @staticmethod
    def paginate(
        keys: list[tuple[str, str]],
        limit: int,
        cursor: tuple[str, str] | None,
        order: str,
        accept: Any = None,
    ) -> tuple[list[tuple[str, str]], str | None]:
        if order == "asc":
            start = bisect.bisect_right(keys, cursor) if cursor else 0
            indexes: range = range(start, len(keys))
        else:
            end = bisect.bisect_left(keys, cursor) if cursor else len(keys)
            indexes = range(end - 1, -1, -1)
        page: list[tuple[str, str]] = []
        more = False
        for idx in indexes:
            key = keys[idx]
            if accept is not None and not accept(key):
                continue
            if len(page) == limit:
                more = True
                break
            page.append(key)
        return page, (encode_cursor(page[-1]) if more and page else None)

    def list_items(self, entity: str, params: dict[str, str]) -> dict[str, Any]:
        e = self.entities[entity]
        filterable: dict[str, str] = {}
        for f in e["fields"]:
            if f["type"] != "datetime":
                filterable[f["name"]] = f["type"]
        for rel in self.parents[entity]:
            filterable[rel["fk"]] = "string"
        reserved = {"limit", "cursor", "order"}
        for name in params:
            if name not in reserved and name not in filterable:
                raise bad(f"unknown query parameter '{name}'")
        limit, cursor, order = self.parse_paging(params)
        wanted: dict[str, Any] = {}
        for name, text in params.items():
            if name in reserved:
                continue
            kind = filterable[name]
            if kind == "int":
                if not INT_RE.match(text):
                    raise bad(f"'{name}' must be an integer")
                wanted[name] = int(text)
            elif kind == "bool":
                if text not in ("true", "false"):
                    raise bad(f"'{name}' must be true or false")
                wanted[name] = text == "true"
            else:
                wanted[name] = text
        with self.lock:
            rows = self.rows[entity]

            def accept(key: tuple[str, str]) -> bool:
                row = rows[key[1]]
                return all(row.get(n) == v for n, v in wanted.items())

            page, nxt = self.paginate(
                self.order[entity], limit, cursor, order, accept if wanted else None
            )
            return {"items": [rows[k[1]] for k in page], "next_cursor": nxt}

    # ---- many-to-many --------------------------------------------------------------------
    def relation(self, a: str, b: str) -> tuple[tuple[str, str], bool] | None:
        """((first, second), forward) for the M:N relation between entities a and b."""
        if (a, b) in self.links:
            return (a, b), True
        if (b, a) in self.links:
            return (b, a), False
        return None

    def require_relation(self, a: str, b: str) -> tuple[tuple[str, str], bool]:
        found = self.relation(a, b)
        if found is None:
            raise ApiError(404, "not_found", "no such route")
        return found

    def link(self, a: str, aid: str, b: str, bid: str, add: bool) -> None:
        pair, forward = self.require_relation(a, b)
        left, right = (aid, bid) if forward else (bid, aid)
        with self.lock:
            self.get(a, aid)
            self.get(b, bid)
            fwd, rev = self.links[pair], self.links_rev[pair]
            if add:
                fwd.setdefault(left, set()).add(right)
                rev.setdefault(right, set()).add(left)
            else:
                if right not in fwd.get(left, set()):
                    raise ApiError(404, "not_found", "link not found")
                fwd[left].discard(right)
                rev[right].discard(left)

    def linked(self, a: str, aid: str, b: str, params: dict[str, str]) -> dict[str, Any]:
        pair, forward = self.require_relation(a, b)
        limit, cursor, order = self.parse_paging(params)
        extra = set(params) - {"limit", "cursor", "order"}
        if extra:
            raise bad(f"unknown query parameter '{sorted(extra)[0]}'")
        with self.lock:
            self.get(a, aid)
            index = self.links[pair] if forward else self.links_rev[pair]
            keys = sorted((self.rows[b][i]["created_at"], i) for i in index.get(aid, set()))
            page, nxt = self.paginate(keys, limit, cursor, order)
            return {"items": [self.rows[b][k[1]] for k in page], "next_cursor": nxt}


def make_handler(store: Store) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # silence access log
            return

        def _send(self, status: int, body: Any = None) -> None:
            payload = b"" if body is None else json.dumps(body).encode()
            self.send_response(status)
            if status != 204:
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if payload:
                self.wfile.write(payload)

        @staticmethod
        def _parse(raw: bytes) -> Any:
            if not raw:
                return None
            try:
                return json.loads(raw)
            except ValueError as exc:
                raise bad("body is not valid JSON") from exc

        def _dispatch(self, method: str) -> None:
            # Always drain the request body first so a keep-alive connection stays in sync.
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                parts = urlsplit(self.path)
                segs = [s for s in parts.path.split("/") if s]
                pairs = parse_qsl(parts.query, keep_blank_values=True)
                params = dict(pairs)
                if len(params) != len(pairs):
                    raise bad("duplicate query parameter")
                body = self._parse(raw) if method in ("POST", "PATCH", "PUT") else None
                status, out = self._route(method, segs, params, body)
            except ApiError as err:
                status, out = err.status, {"error": {"code": err.code, "message": err.message}}
            except Exception as exc:  # reference bug: surface, never hide
                status, out = 500, {"error": {"code": "internal", "message": repr(exc)}}
            self._send(status, out)

        def _route(
            self, method: str, segs: list[str], params: dict[str, str], body: Any
        ) -> tuple[int, Any]:
            if segs == ["healthz"] and method == "GET":
                return 200, {"status": "ok"}
            not_found = ApiError(404, "not_found", "no such route")
            if not segs or segs[0] not in store.by_plural:
                raise not_found
            entity = store.by_plural[segs[0]]
            if len(segs) == 1:
                if method == "POST":
                    return store.create(entity, body, self.headers.get("Idempotency-Key"))
                if method == "GET":
                    return 200, store.list_items(entity, params)
                raise ApiError(405, "method_not_allowed", "method not allowed")
            rid = segs[1]
            if len(segs) == 2:
                if method == "GET":
                    return 200, store.get(entity, rid)
                if method == "PATCH":
                    return 200, store.patch(entity, rid, body, self.headers.get("If-Match"))
                if method == "DELETE":
                    store.delete(entity, rid)
                    return 204, None
                raise ApiError(405, "method_not_allowed", "method not allowed")
            if len(segs) in (3, 4) and segs[2] in store.by_plural:
                other = store.by_plural[segs[2]]
                if store.relation(entity, other) is None:
                    raise not_found
                if len(segs) == 3 and method == "GET":
                    return 200, store.linked(entity, rid, other, params)
                if len(segs) == 4 and method in ("PUT", "DELETE"):
                    store.link(entity, rid, other, segs[3], method == "PUT")
                    return 204, None
                raise ApiError(405, "method_not_allowed", "method not allowed")
            raise not_found

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def do_PATCH(self) -> None:
            self._dispatch("PATCH")

        def do_PUT(self) -> None:
            self._dispatch("PUT")

        def do_DELETE(self) -> None:
            self._dispatch("DELETE")

    return Handler


def main() -> None:
    with open(os.environ["INSTANCE_JSON"], encoding="utf-8") as fh:
        inst = json.load(fh)
    store = Store(inst)

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        request_queue_size = LISTEN_BACKLOG

    server = Server(("0.0.0.0", int(os.environ.get("PORT", "8080"))), make_handler(store))
    signal.signal(
        signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start()
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
