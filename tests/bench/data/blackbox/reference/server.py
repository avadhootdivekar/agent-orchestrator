"""Stdlib reference implementation used by the blackbox grader tests.

One entity (named by instance.json) with CRUD, unique names and id-based cursor pagination.
Everything instance-specific (entity path, page size cap) comes from $INSTANCE_JSON, so one
program serves every instance of the family -- exactly the contract bench-probe-dsl.md §5 states.
"""

import bisect
import json
import os
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# The grader sets $INSTANCE_JSON for the reference run. The sibling copy lets this same
# directory also run standalone (FakeSubject "copy-reference" turns it into the artefact,
# which gets no env), without the grader ever handing the hidden instance to an artefact.
INSTANCE_PATH = os.environ.get("INSTANCE_JSON") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "instance.json"
)
with open(INSTANCE_PATH, encoding="utf-8") as fh:
    INSTANCE = json.load(fh)
BASE = "/" + INSTANCE["entities"][0]["plural"]
PAGE_MAX = INSTANCE["limits"]["page_size_max"]
PAGE_DEFAULT = 10

LOCK = threading.Lock()
ITEMS = {}  # id -> item
NAMES = {}  # unique name -> id
IDS = []  # ascending ids (ids are never reused)
NEXT_ID = [1]


def list_page(cursor, limit):
    start = bisect.bisect_right(IDS, cursor)  # cursor is the last seen id
    chunk = IDS[start : start + limit]
    more = start + limit < len(IDS)
    next_cursor = chunk[-1] if (chunk and more) else None
    return [ITEMS[i] for i in chunk], next_cursor


def valid_name(v):
    return isinstance(v, str) and v != ""


def valid_qty(v):
    return isinstance(v, int) and not isinstance(v, bool)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, status, payload=None):
        body = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _body(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def _route(self):
        url = urlparse(self.path)
        return url.path, {k: v[0] for k, v in parse_qs(url.query).items()}

    def _item_id(self, path):
        tail = path[len(BASE) + 1 :]
        return int(tail) if tail.isdigit() else None

    def do_GET(self):
        path, query = self._route()
        if path == "/healthz":
            return self._send(200, {"status": "ok"})
        if path == BASE:
            return self._list(query)
        if path.startswith(BASE + "/"):
            with LOCK:
                item = ITEMS.get(self._item_id(path))
            return self._send(200, item) if item else self._send(404, {"error": "not_found"})
        return self._send(404, {"error": "not_found"})

    def _list(self, query):
        limit_raw, cursor_raw = query.get("limit", str(PAGE_DEFAULT)), query.get("cursor", "0")
        if not (limit_raw.isdigit() and 1 <= int(limit_raw) <= PAGE_MAX):
            return self._send(400, {"error": f"limit must be 1..{PAGE_MAX}"})
        if not cursor_raw.isdigit():
            return self._send(400, {"error": "bad cursor"})
        with LOCK:
            if "name" in query:
                item = ITEMS.get(NAMES.get(query["name"]))
                items = [item] if item and item["id"] > int(cursor_raw) else []
                return self._send(200, {"items": items, "next_cursor": None})
            items, next_cursor = list_page(int(cursor_raw), int(limit_raw))
        return self._send(200, {"items": items, "next_cursor": next_cursor})

    def do_POST(self):
        path, _ = self._route()
        if path != BASE:
            return self._send(404, {"error": "not_found"})
        body = self._body()
        if body is None or not valid_name(body.get("name")) or not valid_qty(body.get("qty", 0)):
            return self._send(400, {"error": "name (string) and qty (integer) required"})
        with LOCK:
            if body["name"] in NAMES:
                return self._send(409, {"error": "duplicate_name"})
            item = {
                "id": NEXT_ID[0],
                "name": body["name"],
                "qty": body.get("qty", 0),
                "created_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017 -- py<3.11 compatible
            }
            NEXT_ID[0] += 1
            ITEMS[item["id"]] = item
            NAMES[item["name"]] = item["id"]
            IDS.append(item["id"])
        return self._send(201, item)

    def do_PATCH(self):
        path, _ = self._route()
        body = self._body()
        if not path.startswith(BASE + "/"):
            return self._send(404, {"error": "not_found"})
        if body is None or set(body) - {"name", "qty"}:
            return self._send(400, {"error": "only name/qty are patchable"})
        if ("name" in body and not valid_name(body["name"])) or (
            "qty" in body and not valid_qty(body["qty"])
        ):
            return self._send(400, {"error": "invalid field type"})
        with LOCK:
            item = ITEMS.get(self._item_id(path))
            if item is None:
                return self._send(404, {"error": "not_found"})
            new_name = body.get("name", item["name"])
            if new_name != item["name"] and new_name in NAMES:
                return self._send(409, {"error": "duplicate_name"})
            NAMES.pop(item["name"])
            item.update(body)
            NAMES[item["name"]] = item["id"]
        return self._send(200, item)

    def do_DELETE(self):
        path, _ = self._route()
        if not path.startswith(BASE + "/"):
            return self._send(404, {"error": "not_found"})
        with LOCK:
            item = ITEMS.pop(self._item_id(path), None)
            if item is None:
                return self._send(404, {"error": "not_found"})
            NAMES.pop(item["name"], None)
            IDS.remove(item["id"])
        return self._send(204)


class Server(ThreadingHTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    Server((os.environ.get("HOST", "127.0.0.1"), int(os.environ["PORT"])), Handler).serve_forever()
