"""P6 (AC6): run-id guard -- traversal shapes refused, reuse refused, atomic reservation under
two concurrent callers (threads AND processes)."""

import multiprocessing as mp
import tempfile
import threading
from pathlib import Path

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.errors import OrchestratorError
from agent_orchestrator.runstate import RunAlreadyExistsError, RunStateStore

ws = Path(tempfile.mkdtemp())
runs = ws / ".orchestrator" / "runs"


def mk():
    return RunStateStore(str(ws), LocalFsArtifactStore(str(ws)))


store = mk()
bad = [
    "..",
    ".",
    "../x",
    "a/b",
    "/abs",
    "~x",
    ".hidden",
    "a\x00b",
    "a\nb",
    "x" * 129,
    "",
    "a\\..\\b",
    "%2e%2e",
    "é",
]
for b in bad:
    try:
        store.reserve_run_dir(b)
        print(f"{b!r:12} ACCEPTED")
    except RunAlreadyExistsError:
        print(f"{b!r:12} exists")
    except OrchestratorError:
        print(f"{b!r:12} rejected")
outside = [p for p in ws.parent.iterdir() if p.name in ("x", "abs")]
print(
    "escaped dirs:",
    [
        str(p)
        for p in ws.rglob("*")
        if not str(p).startswith(str(runs)) and p != ws / ".orchestrator" and p != runs
    ],
)
store.reserve_run_dir("r1")
try:
    store.reserve_run_dir("r1")
    print("reuse: ACCEPTED")
except RunAlreadyExistsError:
    print("reuse: RunAlreadyExistsError")
(runs / "victim").mkdir()
(runs / "lnk").symlink_to(runs / "victim")
try:
    store.reserve_run_dir("lnk")
    print("symlink reuse: ACCEPTED")
except RunAlreadyExistsError:
    print("symlink reuse: RunAlreadyExistsError")
# threads
for trial in range(50):
    res = []
    barrier = threading.Barrier(2)

    def go():
        barrier.wait()
        try:
            store.reserve_run_dir(f"t{trial}")
            res.append("ok")
        except RunAlreadyExistsError:
            res.append("exists")

    ts = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(res) == ["exists", "ok"], res
print("threads: 50/50 trials exactly one winner")


def proc(rid, q, ev):
    ev.wait()
    try:
        mk().reserve_run_dir(rid)
        q.put("ok")
    except RunAlreadyExistsError:
        q.put("exists")


wins = 0
for trial in range(20):
    q = mp.Queue()
    ev = mp.Event()
    ps = [mp.Process(target=proc, args=(f"p{trial}", q, ev)) for _ in range(2)]
    [p.start() for p in ps]
    ev.set()
    [p.join() for p in ps]
    r = sorted([q.get(), q.get()])
    assert r == ["exists", "ok"], r
    wins += 1
print(f"processes: {wins}/20 trials exactly one winner")
