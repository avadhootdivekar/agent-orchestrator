"""`job-queue` family (HLD §4.1): enqueue / claim / ack / nack / stats over JSON lines.

``generate(seed, difficulty, language)`` draws the queue parameters (priority levels and direction,
default attempts, lease length, fair-share period) and the sampled traps, then derives ``SPEC.md``,
``probes.json`` and the Python reference (``reference/queue_server.py``) from them.

Time never comes from the system clock: a virtual clock starts at 0 and only
``{"op": "advance_clock", "seconds": n}`` moves it, so every lease/expiry probe is deterministic.

Difficulty: ``entities`` -> priority levels 2/3/4/5; ``traps`` -> see ``traps.TRAP_COUNT_BY_LEVEL``;
``concurrency`` -> parallel claimers in the duplicate-claim probe (0 = sequential only);
``scale`` -> perf dataset sizes.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import Any

from . import dsl
from .base import Difficulty, Family, Instance, register_family
from .dsl import jsonl_step, mix_entry, perf_spec, probe
from .refs import reference_files
from .skeleton import allow_list_text, resolve_allow, run_contract_text, skeleton_files
from .traps import sample_traps, trap_count

FAMILY_NAME = "job-queue"
PRIORITY_LEVELS_BY_LEVEL = (2, 3, 4, 5)
MAX_ATTEMPTS_MAX = 10
DEFAULT_ATTEMPTS_RANGE = (3, 5)
VISIBILITY_TIMEOUT_CHOICES = (10, 20, 30, 60)
FAIRNESS_EVERY_RANGE = (4, 8)  # >= 4 so a 3-claim ordering probe never meets the fair-share claim
WORKER_MAX_LENGTH = 64
DEDUPE_KEY_MAX_LENGTH = 128
STATS_KEYS = ("queued", "in_flight", "done", "dead", "total")
PERF_WORKER = "perf-worker"

JsonDict = dict[str, Any]


def _build_limits(rng: random.Random, difficulty: Difficulty, traps: Sequence[str]) -> JsonDict:
    levels = PRIORITY_LEVELS_BY_LEVEL[difficulty.entities]
    high_first = rng.random() < 0.5
    return {
        "priority_levels": levels,
        "priority_order": "high_first" if high_first else "low_first",
        # the least urgent level, so an unprioritised job never jumps the queue
        "default_priority": 0 if high_first else levels - 1,
        "default_max_attempts": rng.randint(*DEFAULT_ATTEMPTS_RANGE),
        "max_attempts_max": MAX_ATTEMPTS_MAX,
        "default_visibility_timeout": rng.choice(VISIBILITY_TIMEOUT_CHOICES),
        "leases": "visibility_timeout" in traps,
        "dedupe": "dedupe_key" in traps,
        "fairness_every": rng.randint(*FAIRNESS_EVERY_RANGE) if "starvation" in traps else 0,
        "max_records": dsl.PERF_SIZES_BY_SCALE[difficulty.scale][-1],
        "parallel_workers": dsl.WORKERS_BY_CONCURRENCY[difficulty.concurrency],
    }


# ---------------------------------------------------------------------------------------------
# SPEC.md
# ---------------------------------------------------------------------------------------------


def _md_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(r) + " |" for r in rows)
    return "\n".join(lines)


def _priority_text(limits: JsonDict) -> str:
    levels = limits["priority_levels"]
    if limits["priority_order"] == "high_first":
        return (
            f"Priorities are integers from 0 to {levels - 1}; a **higher** number is more urgent "
            f"({levels - 1} is served first, 0 last)."
        )
    return (
        f"Priorities are integers from 0 to {levels - 1}; a **lower** number is more urgent "
        f"(0 is served first, {levels - 1} last)."
    )


def _trap_requirements(traps: Sequence[str], limits: JsonDict) -> list[str]:
    out: list[str] = []
    default_vt = limits["default_visibility_timeout"]
    if "visibility_timeout" in traps:
        out.append(
            f"**Leases.** A claim is a lease of `visibility_timeout` seconds (an integer >= 1; "
            f"default {default_vt}; `claim` may pass its own value) on the virtual clock. The "
            "lease ends at `claim time + visibility_timeout` **inclusive**: at exactly that time "
            "the job is no longer in flight and is queued again (it can be claimed immediately, "
            "keeping its original place in line). When a lease has ended, the receipt of that "
            "claim is no longer valid -- `ack`/`nack` with it answer `stale_receipt` -- even if "
            "nobody has claimed the job again yet. A redelivered job gets a new receipt."
        )
    if "at_least_once" in traps:
        out.append(
            "**Redelivery and retry budget.** Delivery is at-least-once: a job whose lease ended "
            "without `ack`/`nack` is delivered again. Each delivery counts as an attempt (exactly "
            "like a `nack`): if the lease of the last allowed attempt ends, the job becomes "
            "`dead` instead of queued, so it is never lost and never redelivered forever. A job "
            "that was acknowledged is never delivered again, however much time passes."
        )
    if "duplicate_claim" in traps:
        out.append(
            "**Exclusive claims.** A job is handed to at most one worker at a time. The service "
            "may be driven by several workers at once, each calling `claim` concurrently: "
            "no job may ever be returned by two claims unless its previous claim has ended "
            "(by `ack`, `nack` or a lease ending) in between."
        )
    if "starvation" in traps:
        every = limits["fairness_every"]
        out.append(
            f"**Fair share.** Every {every}th successful claim (the {every}th, {2 * every}th, "
            f"{3 * every}th, ... claim that returns a job, counted from the start of the "
            "process) ignores priorities and returns the queued job that was enqueued first "
            "(the smallest enqueue order, whatever its priority and however many times it has "
            "been delivered). All other claims follow the priority rule above. A claim that "
            "returns `null` is not counted."
        )
    if "dedupe_key" in traps:
        out.append(
            "**Deduplication.** `enqueue` accepts an optional `dedupe_key` (a string of 1 to "
            f"{DEDUPE_KEY_MAX_LENGTH} characters). If a job with the same `dedupe_key` is "
            "currently "
            '`queued` or `in_flight`, nothing is created: the response is `{"ok": true, "job": '
            '<the existing job, unchanged>, "deduplicated": true}`. Once that job is `done` or '
            "`dead` its key is free again and the same key creates a new job. Without a "
            "`dedupe_key` a job is never deduplicated."
        )
    if "invalid_input_4xx" in traps:
        extra = []
        if limits["leases"]:
            extra.append("a `visibility_timeout` that is not an integer >= 1")
        if limits["dedupe"]:
            extra.append("a `dedupe_key` that is not a non-empty string")
        out.append(
            "**Strict validation.** Invalid requests never change state and never crash the "
            'process; the response is `{"ok": false, "error": "invalid_request", ...}` for: '
            "a line that is not a JSON object, or an object without a string `op`; a `priority` "
            "that is not an integer in range (booleans, floats and numeric strings are not "
            "integers); a `max_attempts` that is not an integer from 1 to "
            f"{limits['max_attempts_max']}; a `payload` that is not an object; a `worker` that is "
            f"not a string of 1 to {WORKER_MAX_LENGTH} characters; `id` or `receipt` missing or "
            "not a non-empty string; `seconds` that is not an integer >= 0"
            + "".join(f"; {e}" for e in extra)
            + ". An unknown `op` is answered with the error `unknown_op`."
        )
    return out


def render_spec(traps: Sequence[str], limits: JsonDict, language: str, allow: Sequence[str]) -> str:
    leases, dedupe = limits["leases"], limits["dedupe"]
    levels = limits["priority_levels"]
    job_fields = [
        ["`id`", "string", "Unique per job, assigned by `enqueue`"],
        ["`state`", "string", "`queued`, `in_flight`, `done` or `dead`"],
        ["`priority`", "integer", f"0 to {levels - 1}"],
        ["`attempts`", "integer", "Number of times the job has been claimed (0 until first claim)"],
        ["`max_attempts`", "integer", "Attempts allowed before the job becomes `dead`"],
        ["`payload`", "object", "Exactly the object given to `enqueue` (`{}` if none)"],
        ["`dedupe_key`", "string or null", "The key given to `enqueue`, else `null`"]
        if dedupe
        else ["`dedupe_key`", "null", "Always `null`"],
    ]
    claim_req = "`worker` (string, 1 to 64 characters, required)" + (
        f"; `visibility_timeout` (integer >= 1 seconds, default "
        f"{limits['default_visibility_timeout']})"
        if leases
        else ""
    )
    enqueue_req = (
        f"`payload` (object, default `{{}}`); `priority` (integer 0 to {levels - 1}, default "
        f"{limits['default_priority']}); `max_attempts` (integer 1 to "
        f"{limits['max_attempts_max']}, "
        f"default {limits['default_max_attempts']})" + ("; `dedupe_key` (string)" if dedupe else "")
    )
    ops = [
        ["`ping`", "-", "`{\"ok\": true}`"],
        ["`enqueue`", enqueue_req, "`{\"ok\": true, \"job\": <job>, \"deduplicated\": false}`"],
        ["`claim`", claim_req,
         "`{\"ok\": true, \"job\": <job>}` -- the job is `in_flight`, `attempts` is increased, "
         "and it carries two more fields: `receipt` (string, unique per claim) and `worker`; or "
         "`{\"ok\": true, \"job\": null}` when nothing can be claimed"],
        ["`ack`", "`id`, `receipt` (strings, required)",
         "`{\"ok\": true}`; the job becomes `done`"],
        ["`nack`", "`id`, `receipt` (strings, required)",
         "`{\"ok\": true, \"state\": \"queued\" or \"dead\"}`; the job becomes `queued` again, "
         "or `dead` when `attempts` has reached `max_attempts`"],
        ["`stats`", "-",
         "`{\"ok\": true, \"stats\": {\"queued\": n, \"in_flight\": n, \"done\": n, \"dead\": n, "
         "\"total\": n}}` -- `total` counts every job ever created"],
        ["`advance_clock`", "`seconds` (integer >= 0, required)",
         "`{\"ok\": true, \"now\": <clock after advancing>}`"],
    ]  # fmt: skip
    errors = [
        ["`unknown_op`", "`op` is not one of the operations above"],
        ["`unknown_job`", "`ack`/`nack` with an `id` no job has"],
        [
            "`stale_receipt`",
            "`ack`/`nack` for a job that is not currently `in_flight` under exactly that `receipt`",
        ],
    ]
    if "invalid_input_4xx" in traps:
        errors.insert(
            0, ["`invalid_request`", "The request is malformed (see Additional requirements)"]
        )
    extras = _trap_requirements(traps, limits)
    parts = [
        "# Specification: job queue over JSON lines",
        "",
        "Implement the queue service described here, in this directory. Everything in this "
        "document is a requirement. The service is checked only through its standard input and "
        "output.",
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
        "not listed for an operation are ignored. Blank lines are skipped without a response.",
        '- A failed request answers `{"ok": false, "error": "<code>", "message": '
        '"<text>"}` and changes nothing. Codes:',
        "",
        _md_table(["`error`", "When"], errors),
        "",
        "- The queue has no real-time behaviour. Time is a virtual clock that starts at 0 and "
        "moves only when `advance_clock` is called; never read the system clock for anything that "
        "affects a result.",
        "",
        "## 4. Jobs",
        "",
        _md_table(["Field", "Type", "Meaning"], job_fields),
        "",
        "A job is `queued` from `enqueue` until it is claimed, `in_flight` while claimed, and ends "
        "`done` (after `ack`) or `dead` (retries exhausted). Jobs are kept in memory; the "
        "service never forgets a job.",
        "",
        "## 5. Operations",
        "",
        _md_table(["`op`", "Request fields", "Response"], ops),
        "",
        "## 6. Ordering and retries",
        "",
        f"- {_priority_text(limits)}",
        "- `claim` returns the queued job with the most urgent priority; among jobs of that "
        "priority, the one enqueued first. A job that returns to the queue (by `nack`"
        + (" or a lease ending" if leases else "")
        + ") keeps its original place in line: it ranks by its original enqueue order, not by the "
        "time it came back.",
        "- A claim counts as an attempt (`attempts` increases by one when the job is claimed). "
        "`nack` makes the job claimable again immediately if `attempts` < `max_attempts`, and "
        "`dead` otherwise.",
        "- `ack` and `nack` require the `receipt` of the job's current claim; the receipt of an "
        "earlier claim of the same job is `stale_receipt`.",
    ]
    if not leases:
        parts.append(
            "- A claimed job stays `in_flight` until it is acknowledged or nacked; claims have no "
            "time limit."
        )
    if extras:
        parts += ["", "## 7. Additional requirements", ""]
        parts += [f"{i}. {text}" for i, text in enumerate(extras, 1)]
    parts += [
        "",
        f"## {8 if extras else 7}. Scale",
        "",
        f"The queue may hold up to {limits['max_records']:,} jobs. `enqueue`, `claim`, `ack` and "
        "`stats` must not take longer as the number of jobs grows: do not scan or sort all jobs "
        "to serve them.",
        "",
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------------------------
# probes
# ---------------------------------------------------------------------------------------------


def _request(op: str, **fields: Any) -> JsonDict:
    return {"op": op, **fields}


OK = {"ok": True}


class Probes:
    def __init__(self, traps: Sequence[str], limits: JsonDict, difficulty: Difficulty) -> None:
        self.traps = list(traps)
        self.limits = limits
        self.levels = limits["priority_levels"]
        self.vt = limits["default_visibility_timeout"]
        self.workers = dsl.WORKERS_BY_CONCURRENCY[difficulty.concurrency]
        self.difficulty = difficulty

    # ---- priorities -------------------------------------------------------------------------
    def prio(self, rank: int) -> int:
        """Priority number of urgency *rank* (0 = most urgent)."""
        top = self.levels - 1
        return top - rank if self.limits["priority_order"] == "high_first" else rank

    # ---- step builders ----------------------------------------------------------------------
    def enqueue(
        self,
        *,
        rank: int | None = None,
        save: str | None = None,
        match: JsonDict | None = None,
        repeat: int | None = None,
        **fields: Any,
    ) -> JsonDict:
        req = _request("enqueue", **fields)
        if rank is not None:
            req["priority"] = self.prio(rank)
        job_match = {"state": "queued", "attempts": 0}
        if "priority" in req:
            job_match["priority"] = req["priority"]
        expect = match if match is not None else {"ok": True, "job": job_match}
        return jsonl_step(
            req, match=expect, save={save: "$.job.id"} if save else None, repeat=repeat
        )

    @staticmethod
    def claim(
        *,
        worker: str = "w1",
        job: str | None | bool = True,
        attempts: int | None = None,
        vt: int | None = None,
        save_id: str | None = None,
        save_receipt: str | None = None,
        not_id: str | None = None,
    ) -> JsonDict:
        """Claim step.  job: a variable name -> that job is expected; None -> null job; True ->
        any job (no check on the id)."""
        req = _request("claim", worker=worker)
        if vt is not None:
            req["visibility_timeout"] = vt
        if job is None:
            expect: JsonDict = {"ok": True, "job": None}
        else:
            jm: JsonDict = {"state": "in_flight", "worker": worker,
                            "receipt": {"$type": "string"}}  # fmt: skip
            if isinstance(job, str):
                jm["id"] = {"$var": job}
            if not_id is not None:
                jm["id"] = {"$not": {"$var": not_id}}
            if attempts is not None:
                jm["attempts"] = attempts
            expect = {"ok": True, "job": jm}
        save = {}
        if save_id:
            save[save_id] = "$.job.id"
        if save_receipt:
            save[save_receipt] = "$.job.receipt"
        return jsonl_step(req, match=expect, save=save or None)

    @staticmethod
    def ack(job_var: str, receipt_var: str, *, match: JsonDict | None = None) -> JsonDict:
        return jsonl_step(
            _request("ack", id="{{" + job_var + "}}", receipt="{{" + receipt_var + "}}"),
            match=match if match is not None else dict(OK),
        )

    @staticmethod
    def nack(job_var: str, receipt_var: str, state: str) -> JsonDict:
        return jsonl_step(
            _request("nack", id="{{" + job_var + "}}", receipt="{{" + receipt_var + "}}"),
            match={"ok": True, "state": state},
        )

    @staticmethod
    def advance(seconds: int, now: int | None = None) -> JsonDict:
        return jsonl_step(
            _request("advance_clock", seconds=seconds),
            match={"ok": True, "now": now} if now is not None else dict(OK),
        )

    @staticmethod
    def stats(**counts: int) -> JsonDict:
        return jsonl_step(_request("stats"), match={"ok": True, "stats": counts})

    @staticmethod
    def error(code: str) -> JsonDict:
        return {"ok": False, "error": code}

    # ---- core -------------------------------------------------------------------------------
    def core(self) -> list[JsonDict]:
        lo = self.levels - 1  # least urgent rank
        mid = (self.levels - 1) // 2
        default_p = self.limits["default_priority"]
        attempts_default = self.limits["default_max_attempts"]
        return [
            probe("core-ping", [jsonl_step(_request("ping"), match=dict(OK))]),
            probe(
                "core-enqueue-shape",
                [
                    self.enqueue(
                        rank=mid,
                        payload={"n": 1},
                        max_attempts=2,
                        match={
                            "ok": True,
                            "job": {
                                "id": {"$type": "string"},
                                "state": "queued",
                                "priority": self.prio(mid),
                                "attempts": 0,
                                "max_attempts": 2,
                                "payload": {"n": 1},
                            },
                            "deduplicated": False,
                        },
                    ),
                    self.enqueue(
                        match={
                            "ok": True,
                            "job": {
                                "state": "queued",
                                "priority": default_p,
                                "attempts": 0,
                                "max_attempts": attempts_default,
                                "payload": {},
                            },
                        }
                    ),
                    self.stats(queued=2, in_flight=0, done=0, dead=0, total=2),
                ],
            ),
            probe(
                "core-claim-priority-order",
                [
                    self.enqueue(rank=lo, save="a"),
                    self.enqueue(rank=0, save="b"),
                    self.enqueue(rank=mid, save="c"),
                    self.claim(job="b", attempts=1),
                    self.claim(job="c", attempts=1),
                    self.claim(job="a", attempts=1),
                    self.claim(job=None),
                ],
            ),
            probe(
                "core-fifo-within-priority",
                [
                    self.enqueue(rank=mid, save="a"),
                    self.enqueue(rank=mid, save="b"),
                    self.enqueue(rank=mid, save="c"),
                    self.claim(job="a"),
                    self.claim(job="b"),
                    self.claim(job="c"),
                ],
            ),
            probe(
                "core-ack",
                [
                    self.enqueue(save="j"),
                    self.claim(job="j", save_receipt="r"),
                    self.ack("j", "r"),
                    self.stats(queued=0, in_flight=0, done=1, dead=0, total=1),
                    self.claim(job=None),
                    self.ack("j", "r", match=self.error("stale_receipt")),
                ],
            ),
            probe(
                "core-nack-retry",
                [
                    self.enqueue(max_attempts=3, save="j"),
                    self.claim(job="j", attempts=1, save_receipt="r1"),
                    self.nack("j", "r1", "queued"),
                    self.claim(job="j", attempts=2, save_receipt="r2"),
                    self.ack("j", "r2"),
                    self.stats(queued=0, in_flight=0, done=1, dead=0, total=1),
                ],
            ),
            probe(
                "core-retry-exhaustion",
                [
                    self.enqueue(max_attempts=2, save="j"),
                    self.claim(job="j", attempts=1, save_receipt="r1"),
                    self.nack("j", "r1", "queued"),
                    self.claim(job="j", attempts=2, save_receipt="r2"),
                    self.nack("j", "r2", "dead"),
                    self.stats(queued=0, in_flight=0, done=0, dead=1, total=1),
                    self.claim(job=None),
                ],
            ),
            probe(
                "core-stats",
                [
                    self.enqueue(rank=0, save="a"),
                    self.enqueue(rank=0, save="b"),
                    self.enqueue(rank=0, save="c"),
                    self.claim(job="a", save_receipt="ra"),
                    self.claim(job="b", save_receipt="rb"),
                    self.ack("b", "rb"),
                    self.stats(queued=1, in_flight=1, done=1, dead=0, total=3),
                ],
            ),
            probe(
                "core-advance-clock",
                [
                    self.advance(5, 5),
                    self.advance(7, 12),
                    self.advance(0, 12),
                ],
            ),
            probe(
                "core-ack-unknown-job",
                [
                    jsonl_step(
                        _request("ack", id="no-such-job", receipt="r"),
                        match=self.error("unknown_job"),
                    ),
                    jsonl_step(
                        _request("nack", id="no-such-job", receipt="r"),
                        match=self.error("unknown_job"),
                    ),
                ],
            ),
            probe(
                "core-receipt-checked",
                [
                    self.enqueue(save="j"),
                    self.claim(job="j", save_receipt="r1"),
                    jsonl_step(
                        _request("ack", id="{{j}}", receipt="not-the-receipt"),
                        match=self.error("stale_receipt"),
                    ),
                    self.nack("j", "r1", "queued"),
                    self.claim(job="j", save_receipt="r2"),
                    self.ack("j", "r1", match=self.error("stale_receipt")),
                    self.ack("j", "r2"),
                ],
            ),
        ]

    # ---- traps ------------------------------------------------------------------------------
    def trap_probes(self) -> list[JsonDict]:
        builders = {
            "visibility_timeout": self._t_visibility,
            "at_least_once": self._t_at_least_once,
            "duplicate_claim": self._t_duplicate_claim,
            "starvation": self._t_starvation,
            "dedupe_key": self._t_dedupe,
            "invalid_input_4xx": self._t_invalid,
        }
        return [builders[t]() for t in self.traps]

    def _t_visibility(self) -> JsonDict:
        v = self.vt
        steps = [
            self.enqueue(save="j1"),
            self.claim(job="j1", attempts=1, save_receipt="r1"),
            self.advance(v - 1),
            self.claim(worker="w2", job=None),
            self.advance(1),
            self.claim(worker="w2", job="j1", attempts=2, save_receipt="r2"),
            self.ack("j1", "r1", match=self.error("stale_receipt")),
            self.ack("j1", "r2"),
            # a claim-specific timeout overrides the default
            self.enqueue(save="j2"),
            self.claim(job="j2", vt=3, save_receipt="r3"),
            self.advance(2),
            self.claim(worker="w2", job=None),
            self.advance(1),
            self.claim(worker="w2", job="j2", attempts=2, save_receipt="r4"),
            self.ack("j2", "r4"),
            # a lease that has just ended is invalid even before anyone re-claims the job
            self.enqueue(save="j3"),
            self.claim(job="j3", save_receipt="r5"),
            self.advance(v),
            self.ack("j3", "r5", match=self.error("stale_receipt")),
            self.claim(worker="w3", job="j3", attempts=2),
        ]
        return probe("trap-visibility-timeout", steps, trap="visibility_timeout")

    def _t_at_least_once(self) -> JsonDict:
        v = self.vt
        steps = [
            self.enqueue(max_attempts=2, save="j1"),
            self.claim(job="j1", attempts=1),
            self.advance(v),
            self.claim(job="j1", attempts=2, save_receipt="r2"),
            self.advance(v),
            self.stats(queued=0, in_flight=0, done=0, dead=1, total=1),
            self.claim(job=None),
            # an acknowledged job is never delivered again
            self.enqueue(save="j2"),
            self.claim(job="j2", save_receipt="r"),
            self.ack("j2", "r"),
            self.advance(10 * v),
            self.claim(job=None),
            self.stats(queued=0, in_flight=0, done=1, dead=1, total=2),
            # a lease ending and a nack draw on the same attempts budget
            self.enqueue(max_attempts=2, save="j3"),
            self.claim(job="j3", attempts=1),
            self.advance(v),
            self.claim(job="j3", attempts=2, save_receipt="r3"),
            self.nack("j3", "r3", "dead"),
        ]
        return probe("trap-at-least-once", steps, trap="at_least_once")

    def _t_duplicate_claim(self) -> JsonDict:
        steps = [
            self.enqueue(rank=0, repeat=5),
            self.claim(worker="w1", save_id="first"),
            self.claim(worker="w2", not_id="first"),
            self.stats(queued=3, in_flight=2, done=0, dead=0, total=5),
        ]
        parallel = after = None
        if self.workers:
            parallel = [
                [jsonl_step(_request("claim", worker=f"worker-{k}"), match=dict(OK), repeat=2)]
                for k in range(self.workers)
            ]
            after = [self.stats(queued=0, in_flight=5, done=0, dead=0, total=5)]
        return probe(
            "trap-duplicate-claim", steps, trap="duplicate_claim", parallel=parallel, after=after
        )

    def _t_starvation(self) -> JsonDict:
        k = self.limits["fairness_every"]
        lo = self.levels - 1
        steps = [self.enqueue(rank=lo, save="low")]
        steps += [self.enqueue(rank=0, save=f"h{i}") for i in range(1, k + 1)]
        steps += [self.claim(job=f"h{i}") for i in range(1, k)]
        steps += [self.claim(job="low"), self.claim(job=f"h{k}")]
        return probe("trap-starvation", steps, trap="starvation")

    def _t_dedupe(self) -> JsonDict:
        def dedupe_resp(job_var: str | None, dup: bool, payload: JsonDict) -> JsonDict:
            job: JsonDict = {"payload": payload}
            if job_var:
                job["id"] = {"$var": job_var}
            return {"ok": True, "job": job, "deduplicated": dup}

        steps = [
            self.enqueue(
                dedupe_key="dk-1",
                payload={"v": 1},
                save="j1",
                match=dedupe_resp(None, False, {"v": 1}),
            ),
            self.enqueue(
                dedupe_key="dk-1", payload={"v": 2}, match=dedupe_resp("j1", True, {"v": 1})
            ),
            self.stats(queued=1, in_flight=0, done=0, dead=0, total=1),
            self.claim(job="j1", save_receipt="r1"),
            self.enqueue(dedupe_key="dk-1", match=dedupe_resp("j1", True, {"v": 1})),
            self.ack("j1", "r1"),
            self.enqueue(
                dedupe_key="dk-1",
                save="j2",
                match={"ok": True, "job": {"id": {"$not": {"$var": "j1"}}}, "deduplicated": False},
            ),
            self.enqueue(dedupe_key="dk-2", save="j3"),
            self.stats(queued=2, in_flight=0, done=1, dead=0, total=3),
            # a job that ended `dead` frees its key as well
            self.enqueue(dedupe_key="dk-3", max_attempts=1, save="j4"),
            self.claim(job="j2"),
            self.claim(job="j3"),
            self.claim(job="j4", save_receipt="r4"),
            self.nack("j4", "r4", "dead"),
            self.enqueue(
                dedupe_key="dk-3",
                match={"ok": True, "job": {"id": {"$not": {"$var": "j4"}}}, "deduplicated": False},
            ),
        ]
        return probe("trap-dedupe-key", steps, trap="dedupe_key")

    def _t_invalid(self) -> JsonDict:
        bad = self.error("invalid_request")
        levels, attempts_max = self.levels, self.limits["max_attempts_max"]
        raw = [
            [1, 2], "enqueue", {"no_op": True}, {"op": 5},
            _request("enqueue", priority=-1), _request("enqueue", priority=levels),
            _request("enqueue", priority="1"), _request("enqueue", priority=True),
            _request("enqueue", priority=1.5),
            _request("enqueue", max_attempts=0), _request("enqueue", max_attempts=attempts_max + 1),
            _request("enqueue", max_attempts="3"), _request("enqueue", max_attempts=True),
            _request("enqueue", payload=[1]), _request("enqueue", payload="x"),
            _request("claim"), _request("claim", worker=""), _request("claim", worker=7),
            _request("claim", worker="w" * (WORKER_MAX_LENGTH + 1)),
            _request("ack"), _request("ack", id="j"), _request("ack", receipt="r"),
            _request("ack", id=5, receipt="r"), _request("nack", id="j"),
            _request("advance_clock"), _request("advance_clock", seconds=-1),
            _request("advance_clock", seconds="5"), _request("advance_clock", seconds=1.5),
            _request("advance_clock", seconds=True),
        ]  # fmt: skip
        if self.limits["leases"]:
            raw += [_request("claim", worker="w", visibility_timeout=v) for v in (0, "5", -3)]
        if self.limits["dedupe"]:
            raw += [_request("enqueue", dedupe_key=""), _request("enqueue", dedupe_key=5)]
        steps = [jsonl_step(r, match=dict(bad)) for r in raw]
        steps.append(jsonl_step(_request("frobnicate"), match=self.error("unknown_op")))
        steps.append(self.stats(queued=0, in_flight=0, done=0, dead=0, total=0))
        steps.append(jsonl_step(_request("ping"), match=dict(OK)))
        return probe("trap-invalid-input", steps, trap="invalid_input_4xx")

    # ---- perf -------------------------------------------------------------------------------
    def perf(self) -> JsonDict:
        lo = self.levels - 1
        ok = {"ok": True}
        # The load step seeds `claimed_id`/`receipt` (receipt = the id, i.e. not a valid receipt)
        # so an `ack` drawn before the first `claim` is still a well-formed request.
        load = jsonl_step(
            _request("enqueue", payload={"n": "{{i}}"}, priority=self.prio(lo)),
            match=dict(ok),
            save={"claimed_id": "$.job.id", "receipt": "$.job.id"},
            repeat=1,
        )
        mix = [
            mix_entry(
                0.2,
                "enqueue_urgent",
                "write",
                jsonl_step(
                    _request("enqueue", payload={"n": "x"}, priority=self.prio(0)), match=dict(ok)
                ),
            ),
            mix_entry(
                0.2,
                "enqueue_low",
                "write",
                jsonl_step(
                    _request("enqueue", payload={"n": "y"}, priority=self.prio(lo)), match=dict(ok)
                ),
            ),
            mix_entry(
                0.25,
                "claim",
                "claim",
                jsonl_step(
                    _request("claim", worker=PERF_WORKER),
                    save={"claimed_id": "$.job.id", "receipt": "$.job.receipt"},
                ),
            ),
            mix_entry(
                0.15,
                "ack",
                "ack",
                jsonl_step(_request("ack", id="{{claimed_id}}", receipt="{{receipt}}")),
            ),
            mix_entry(0.2, "stats", "read", jsonl_step(_request("stats"), match=dict(ok))),
        ]
        return perf_spec(self.difficulty.scale, load, mix)


@register_family
class JobQueueFamily(Family):
    name = FAMILY_NAME
    contract = "jsonl"

    def generate(
        self,
        seed: int,
        difficulty: Difficulty,
        language: str,
        allow: Sequence[str] | None = None,
    ) -> Instance:
        allow_list = resolve_allow(language, allow)
        rng = random.Random(seed)
        traps = sample_traps(rng, self.name, trap_count(difficulty.traps))
        limits = _build_limits(rng, difficulty, traps)
        builder = Probes(traps, limits, difficulty)
        probes = dsl.probes_doc(
            "jsonl", language, builder.core(), builder.trap_probes(), builder.perf()
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
            spec_md=render_spec(traps, limits, language, allow_list),
            skeleton=skeleton_files("jsonl", language, allow_list, title="job queue service"),
            probes=probes,
            reference=reference_files("queue_server.py"),
        )
