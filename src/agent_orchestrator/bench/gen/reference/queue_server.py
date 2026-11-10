"""Reference implementation of the `job-queue` family (grader-owned, never agent-visible).

Stdlib only, in-memory, parameterised by ``instance.json`` (path in ``$INSTANCE_JSON``).  JSON-lines
over stdin/stdout.  Time is a virtual clock that only ``advance_clock`` moves.  Claim/expiry use
lazy-deletion heaps, so every op is O(log n) amortised (scaling exponent ~0).

This file is copied verbatim into ``<grading_dir>/reference/`` by the generated provider.
"""

from __future__ import annotations

import heapq
import json
import os
import sys
from pathlib import Path
from typing import Any

WORKER_MAX_LENGTH = 64
DEDUPE_KEY_MAX_LENGTH = 128
STATES = ("queued", "in_flight", "done", "dead")


class OpError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def invalid(message: str) -> OpError:
    return OpError("invalid_request", message)


def is_int(v: Any) -> bool:
    return type(v) is int


class Queue:
    def __init__(self, limits: dict[str, Any]) -> None:
        self.levels: int = limits["priority_levels"]
        self.high_first: bool = limits["priority_order"] == "high_first"
        self.default_priority: int = limits["default_priority"]
        self.default_attempts: int = limits["default_max_attempts"]
        self.attempts_max: int = limits["max_attempts_max"]
        self.default_vt: int = limits["default_visibility_timeout"]
        self.leases: bool = limits["leases"]
        self.dedupe: bool = limits["dedupe"]
        self.fair_every: int = limits["fairness_every"]
        self.now = 0
        self.jobs: dict[str, dict[str, Any]] = {}
        self.counts = dict.fromkeys(STATES, 0)
        self.prio_heap: list[tuple[int, int, int, str]] = []  # (rank, seq, gen, id)
        self.fifo_heap: list[tuple[int, int, str]] = []  # (seq, gen, id)
        self.lease_heap: list[tuple[int, int, str, str]] = []  # (deadline, n, id, receipt)
        self.live_keys: dict[str, str] = {}
        self.seq = 0
        self.claims = 0
        self.receipts = 0

    # ---- internals -----------------------------------------------------------------------
    def _rank(self, priority: int) -> int:
        return -priority if self.high_first else priority

    def _set_state(self, job: dict[str, Any], state: str) -> None:
        self.counts[job["state"]] -= 1
        job["state"] = state
        self.counts[state] += 1
        if state in ("done", "dead") and job["dedupe_key"] is not None:
            if self.live_keys.get(job["dedupe_key"]) == job["id"]:
                del self.live_keys[job["dedupe_key"]]

    def _requeue(self, job: dict[str, Any]) -> None:
        self._set_state(job, "queued")
        job["_gen"] += 1
        heapq.heappush(
            self.prio_heap, (self._rank(job["priority"]), job["_seq"], job["_gen"], job["id"])
        )
        heapq.heappush(self.fifo_heap, (job["_seq"], job["_gen"], job["id"]))

    def _release(self, job: dict[str, Any]) -> None:
        """A claimed job goes back to the queue, or to dead when its attempts are used up."""
        if job["attempts"] >= job["max_attempts"]:
            self._set_state(job, "dead")
        else:
            self._requeue(job)

    def _expire(self) -> None:
        while self.lease_heap and self.lease_heap[0][0] <= self.now:
            _, _, jid, receipt = heapq.heappop(self.lease_heap)
            job = self.jobs[jid]
            if job["state"] == "in_flight" and job["receipt"] == receipt:
                self._release(job)

    def _pop_valid(self, heap: list[Any], gen_index: int, id_index: int) -> dict[str, Any] | None:
        while heap:
            entry = heap[0]
            job = self.jobs[entry[id_index]]
            if job["state"] == "queued" and job["_gen"] == entry[gen_index]:
                return job
            heapq.heappop(heap)
        return None

    @staticmethod
    def _view(job: dict[str, Any], *, claimed: bool = False) -> dict[str, Any]:
        out = {
            "id": job["id"],
            "state": job["state"],
            "priority": job["priority"],
            "attempts": job["attempts"],
            "max_attempts": job["max_attempts"],
            "payload": job["payload"],
            "dedupe_key": job["dedupe_key"],
        }
        if claimed:
            out["receipt"] = job["receipt"]
            out["worker"] = job["worker"]
        return out

    def _job_for_receipt(self, req: dict[str, Any]) -> dict[str, Any]:
        jid, receipt = req.get("id"), req.get("receipt")
        if not isinstance(jid, str) or not jid or not isinstance(receipt, str) or not receipt:
            raise invalid("'id' and 'receipt' must be non-empty strings")
        job = self.jobs.get(jid)
        if job is None:
            raise OpError("unknown_job", f"no job {jid}")
        self._expire()
        if job["state"] != "in_flight" or job["receipt"] != receipt:
            raise OpError("stale_receipt", "receipt is not valid for the current claim")
        return job

    # ---- ops -----------------------------------------------------------------------------
    def enqueue(self, req: dict[str, Any]) -> dict[str, Any]:
        payload = req.get("payload", {})
        if not isinstance(payload, dict):
            raise invalid("'payload' must be an object")
        priority = req.get("priority", self.default_priority)
        if not is_int(priority) or not 0 <= priority < self.levels:
            raise invalid(f"'priority' must be an integer between 0 and {self.levels - 1}")
        attempts = req.get("max_attempts", self.default_attempts)
        if not is_int(attempts) or not 1 <= attempts <= self.attempts_max:
            raise invalid(f"'max_attempts' must be an integer between 1 and {self.attempts_max}")
        key = req.get("dedupe_key") if self.dedupe else None
        if self.dedupe and "dedupe_key" in req and req["dedupe_key"] is not None:
            if not isinstance(key, str) or not 1 <= len(key) <= DEDUPE_KEY_MAX_LENGTH:
                raise invalid("'dedupe_key' must be a non-empty string")
        self._expire()
        if key is not None and key in self.live_keys:
            return {
                "ok": True,
                "job": self._view(self.jobs[self.live_keys[key]]),
                "deduplicated": True,
            }
        self.seq += 1
        jid = f"job-{self.seq}"
        job = {
            "id": jid, "state": "queued", "priority": priority, "attempts": 0,
            "max_attempts": attempts, "payload": payload, "dedupe_key": key,
            "receipt": None, "worker": None, "_seq": self.seq, "_gen": 0,
        }  # fmt: skip
        self.jobs[jid] = job
        self.counts["queued"] += 1
        heapq.heappush(self.prio_heap, (self._rank(priority), self.seq, 0, jid))
        heapq.heappush(self.fifo_heap, (self.seq, 0, jid))
        if key is not None:
            self.live_keys[key] = jid
        return {"ok": True, "job": self._view(job), "deduplicated": False}

    def claim(self, req: dict[str, Any]) -> dict[str, Any]:
        worker = req.get("worker")
        if not isinstance(worker, str) or not 1 <= len(worker) <= WORKER_MAX_LENGTH:
            raise invalid("'worker' must be a non-empty string")
        vt = req.get("visibility_timeout", self.default_vt) if self.leases else 0
        if self.leases and (not is_int(vt) or vt < 1):
            raise invalid("'visibility_timeout' must be an integer >= 1")
        self._expire()
        fair = self.fair_every > 0 and (self.claims + 1) % self.fair_every == 0
        job = (
            self._pop_valid(self.fifo_heap, 1, 2) if fair else self._pop_valid(self.prio_heap, 2, 3)
        )
        if job is None:
            return {"ok": True, "job": None}
        self.claims += 1
        self.receipts += 1
        self._set_state(job, "in_flight")
        job["_gen"] += 1  # invalidates the heap entries of this queued incarnation
        job["attempts"] += 1
        job["receipt"] = f"r{self.receipts}"
        job["worker"] = worker
        if self.leases:
            heapq.heappush(
                self.lease_heap, (self.now + vt, self.receipts, job["id"], job["receipt"])
            )
        return {"ok": True, "job": self._view(job, claimed=True)}

    def ack(self, req: dict[str, Any]) -> dict[str, Any]:
        job = self._job_for_receipt(req)
        self._set_state(job, "done")
        return {"ok": True}

    def nack(self, req: dict[str, Any]) -> dict[str, Any]:
        job = self._job_for_receipt(req)
        self._release(job)
        return {"ok": True, "state": job["state"]}

    def stats(self) -> dict[str, Any]:
        self._expire()
        return {"ok": True, "stats": {**self.counts, "total": len(self.jobs)}}

    def advance_clock(self, req: dict[str, Any]) -> dict[str, Any]:
        seconds: Any = req.get("seconds")
        if not is_int(seconds) or seconds < 0:
            raise invalid("'seconds' must be a non-negative integer")
        self.now += seconds
        return {"ok": True, "now": self.now}

    def handle(self, req: Any) -> dict[str, Any]:
        if not isinstance(req, dict) or not isinstance(req.get("op"), str):
            raise invalid("request must be an object with a string 'op'")
        op = req["op"]
        if op == "ping":
            return {"ok": True}
        if op == "enqueue":
            return self.enqueue(req)
        if op == "claim":
            return self.claim(req)
        if op == "ack":
            return self.ack(req)
        if op == "nack":
            return self.nack(req)
        if op == "stats":
            return self.stats()
        if op == "advance_clock":
            return self.advance_clock(req)
        raise OpError("unknown_op", f"unknown op {op!r}")


def instance_path() -> str:
    """``$INSTANCE_JSON`` (set by the grader), else the instance.json next to this file."""
    return os.environ.get("INSTANCE_JSON") or str(Path(__file__).with_name("instance.json"))


def main() -> None:
    with open(instance_path(), encoding="utf-8") as fh:
        queue = Queue(json.load(fh)["limits"])
    out = sys.stdout
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            resp = queue.handle(json.loads(line))
        except OpError as err:
            resp = {"ok": False, "error": err.code, "message": err.message}
        except ValueError:
            resp = {"ok": False, "error": "invalid_request", "message": "not valid JSON"}
        out.write(json.dumps(resp) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
