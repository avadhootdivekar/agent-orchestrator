"""T-HjxNQ0 U-SM13: a non-vacuous same-key race between processes (HLD 8.4.4, 18.1).

8 writers and 2 readers start behind one `multiprocessing.Barrier`; every writer stores the SAME
key (with different content) through `put_blob` + `put_entry`. The test proves contention
happened (several writers completed a put, and their [first put, last put] windows overlap) and
that every entry a reader observed was complete and valid with its blob present. Iterations are
bounded and every join has a timeout. Skipped on a single CPU, where no overlap can be proved.
"""

from __future__ import annotations

import io
import multiprocessing
import os
import queue
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.cache.constants import TMP_DIR
from agent_orchestrator.cache.store import LocalFsCacheStore
from tests.cache.fakes import key_of, make_entry, sha_of

WRITERS = 8
READERS = 2
WRITE_ITERATIONS = 40
READ_ITERATION_BOUND = 20_000
READ_PAUSE_SECONDS = 0.0005
BARRIER_TIMEOUT_SECONDS = 60
RESULT_TIMEOUT_SECONDS = 90
JOIN_TIMEOUT_SECONDS = 30
RACE_KEY = key_of("race-key")
RACE_PATH = "out/race.txt"
RACE_PAYLOAD = b"race payload " * 64
BLOB_BOUND = 1024 * 1024
MAX_BYTES = 1024**3
FIXED_NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)


def _cpu_count() -> int:
    getter = getattr(os, "sched_getaffinity", None)
    return len(getter(0)) if getter is not None else (os.cpu_count() or 1)


pytestmark = pytest.mark.skipif(
    _cpu_count() < 2, reason="the same-key race needs at least 2 CPUs to be able to overlap"
)


def _store(workspace: str) -> LocalFsCacheStore:
    return LocalFsCacheStore.for_workspace(workspace, max_bytes=MAX_BYTES, ttl_days=30)


def _writer(workspace: str, index: int, barrier: Any, results: Any) -> None:
    result: dict[str, Any] = {"role": "writer", "index": index, "puts": [], "error": None}
    try:
        store = _store(workspace)
        # Same key for every writer; the content (cost) differs so the last writer visibly wins.
        entry = make_entry(RACE_KEY, outputs=[(RACE_PATH, RACE_PAYLOAD)], cost_usd=float(index + 1))
        barrier.wait(BARRIER_TIMEOUT_SECONDS)
        result["start"] = time.monotonic()
        for _ in range(WRITE_ITERATIONS):
            store.put_blob(io.BytesIO(RACE_PAYLOAD), max_bytes=BLOB_BOUND)
            result["puts"].append(store.put_entry(entry))
        result["end"] = time.monotonic()
    except BaseException as exc:  # noqa: BLE001 - reported to the parent process
        result["error"] = f"{type(exc).__name__}: {exc}"
    results.put(result)


class _Sink(io.RawIOBase):
    def __init__(self) -> None:
        self.data = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, b: Any) -> int:
        self.data += bytes(b)
        return len(b)


def _observe(store: LocalFsCacheStore, observed: list[float], problems: list[str]) -> None:
    entry = store.get_entry(RACE_KEY)  # a torn or invalid entry raises: that is a problem
    if entry is None:
        return
    for output in entry.outputs:
        if not store.has_blob(output.sha256):
            problems.append(f"entry observed without its blob {output.sha256[:12]}")
            continue
        sink = _Sink()
        store.read_blob(output.sha256, sink, max_bytes=output.size)  # type: ignore[arg-type]
        if sha_of(bytes(sink.data)) != output.sha256:
            problems.append("blob content does not match its sha256")
    observed.append(entry.usage.cost_usd)


def _reader(workspace: str, index: int, barrier: Any, stop: Any, results: Any) -> None:
    result: dict[str, Any] = {"role": "reader", "index": index, "observed": 0, "problems": []}
    try:
        store = _store(workspace)
        observed: list[float] = []
        problems: list[str] = []
        barrier.wait(BARRIER_TIMEOUT_SECONDS)
        for _ in range(READ_ITERATION_BOUND):
            if stop.is_set():
                break
            try:
                _observe(store, observed, problems)
            except BaseException as exc:  # noqa: BLE001 - a torn entry is the failure we look for
                problems.append(f"{type(exc).__name__}: {exc}")
            time.sleep(READ_PAUSE_SECONDS)
        _observe(store, observed, problems)  # one last look once the writers are done
        result["observed"], result["problems"] = len(observed), problems
    except BaseException as exc:  # noqa: BLE001 - reported to the parent process
        result["problems"] = [f"{type(exc).__name__}: {exc}"]
    results.put(result)


def _collect(results: Any, expected: int) -> list[dict[str, Any]]:
    collected = []
    for _ in range(expected):
        try:
            collected.append(results.get(timeout=RESULT_TIMEOUT_SECONDS))
        except queue.Empty:
            pytest.fail("a worker process did not report in time")
    return collected


def test_same_key_race_between_processes_is_consistent_and_really_contended(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "race-ws"
    workspace.mkdir()
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(WRITERS + READERS)
    stop = ctx.Event()
    results = ctx.Queue()
    writers = [
        ctx.Process(target=_writer, args=(str(workspace), i, barrier, results), daemon=True)
        for i in range(WRITERS)
    ]
    readers = [
        ctx.Process(target=_reader, args=(str(workspace), i, barrier, stop, results), daemon=True)
        for i in range(READERS)
    ]
    processes = [*writers, *readers]
    try:
        for proc in processes:
            proc.start()
        reports = _collect(results, WRITERS)  # writers first; readers keep looking meanwhile
        stop.set()
        reports += _collect(results, READERS)
        for proc in processes:
            proc.join(JOIN_TIMEOUT_SECONDS)
            assert not proc.is_alive(), "a worker did not exit after reporting"
    finally:
        stop.set()
        for proc in processes:
            if proc.is_alive():
                proc.terminate()

    written = [r for r in reports if r["role"] == "writer"]
    read = [r for r in reports if r["role"] == "reader"]
    assert [r["error"] for r in written if r["error"]] == []
    assert [p for r in read for p in r["problems"]] == []

    # Non-vacuous (1): several writers completed a put_entry for the SAME key (return values).
    finished = [r for r in written if len(r["puts"]) == WRITE_ITERATIONS and all(r["puts"])]
    assert len(finished) >= 2
    # Non-vacuous (2): their put windows genuinely overlapped in time (the barrier released them
    # together; CLOCK_MONOTONIC is shared by every process on the host).
    overlaps = [
        (a["index"], b["index"])
        for n, a in enumerate(finished)
        for b in finished[n + 1 :]
        if a["start"] < b["end"] and b["start"] < a["end"]
    ]
    assert overlaps, "no two writers overlapped: the race was vacuous"
    # Readers observed complete entries (each observation was validated inside the reader).
    assert sum(r["observed"] for r in read) >= 1

    store = _store(str(workspace))
    final = store.get_entry(RACE_KEY)
    assert final is not None and final.outputs[0].sha256 == sha_of(RACE_PAYLOAD)
    assert final.usage.cost_usd in {float(i + 1) for i in range(WRITERS)}  # one writer's, whole
    report = store.verify()
    assert report.ok, report.problems
    assert report.entries_checked == 1 and report.blobs_checked == 1
    assert list((workspace / ".orchestrator" / "cache" / TMP_DIR).iterdir()) == []
