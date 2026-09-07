"""Tests for `isolation/locks.py::IntegrationLock` (E-Wk9Tz3 T-Ib5Qy9, TASK.md AC-1).

Thread-serialization tests are event-synchronized (never `sleep`-based) per AC-1(a).
Cross-process tests use a REAL second process (`multiprocessing`) per AC-1(b)/(c), not a
mock -- `flock` is a kernel/process-level primitive that a fake can't exercise faithfully.
"""

from __future__ import annotations

import multiprocessing
import os
import threading
import time
from pathlib import Path

import pytest

from agent_orchestrator.errors import IntegrationLockTimeoutError
from agent_orchestrator.isolation.locks import LOCK_FILENAME, IntegrationLock

# Generous bound for cross-process tests on a loaded CI box; not used for the deterministic
# thread tests, which synchronize on `threading.Event` instead of a timeout race.
_PROCESS_TEST_TIMEOUT = 10.0


@pytest.fixture
def common_dir(tmp_path: Path) -> str:
    d = tmp_path / "repo.git"
    d.mkdir()
    return str(d)


# --------------------------------------------------------------------------------------
# Basic acquire/release
# --------------------------------------------------------------------------------------


def test_acquire_then_release_allows_reacquire(common_dir: str) -> None:
    lock = IntegrationLock(common_dir)
    assert lock.acquire(1.0) is True
    lock.release()
    assert lock.acquire(1.0) is True
    lock.release()


def test_release_without_acquire_is_a_noop(common_dir: str) -> None:
    IntegrationLock(common_dir).release()  # must not raise


def test_release_is_idempotent(common_dir: str) -> None:
    lock = IntegrationLock(common_dir)
    assert lock.acquire(1.0) is True
    lock.release()
    lock.release()  # second call must not raise


def test_acquire_os_error_releases_thread_lock_before_reraising(tmp_path: Path) -> None:
    """A companion-file open failure (e.g. the common dir doesn't exist) must not leak
    the thread lock -- a subsequent acquire on the SAME key by another caller must not be
    blocked by this failed attempt."""
    bad_common_dir = str(tmp_path / "does" / "not" / "exist")
    lock = IntegrationLock(bad_common_dir)
    with pytest.raises(OSError):
        lock.acquire(1.0)

    # The thread lock (keyed by common_dir) must have been released on the OSError path --
    # a fresh instance for the SAME key can still acquire promptly.
    other = IntegrationLock(bad_common_dir)
    (tmp_path / "does" / "not" / "exist").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "does" / "not" / "exist").mkdir()
    assert other.acquire(1.0) is True
    other.release()


def test_lock_file_created_under_common_dir(common_dir: str) -> None:
    lock = IntegrationLock(common_dir)
    assert lock.acquire(1.0) is True
    lock.release()
    assert (Path(common_dir) / LOCK_FILENAME).exists()


def test_reacquire_on_same_instance_without_release_raises(common_dir: str) -> None:
    lock = IntegrationLock(common_dir)
    assert lock.acquire(1.0) is True
    try:
        with pytest.raises(RuntimeError):
            lock.acquire(1.0)
    finally:
        lock.release()


def test_context_manager_success(common_dir: str) -> None:
    with IntegrationLock(common_dir, timeout=1.0) as lock:
        assert lock.common_dir == os.path.normpath(common_dir)


def test_context_manager_raises_on_timeout(common_dir: str) -> None:
    holder = IntegrationLock(common_dir)
    assert holder.acquire(1.0) is True
    try:
        with pytest.raises(IntegrationLockTimeoutError):
            with IntegrationLock(common_dir, timeout=0.2):
                pass
    finally:
        holder.release()


# --------------------------------------------------------------------------------------
# AC-1(a): two threads genuinely serialize -- event-synchronized, not sleep-based
# --------------------------------------------------------------------------------------


def test_two_threads_serialize_deterministically(common_dir: str) -> None:
    """Thread A acquires and holds until told to release; thread B's `.acquire()` is
    started only after A confirms it holds the lock (event 1), and the test asserts B has
    NOT yet acquired (event 2, with a bounded, expected-to-time-out wait) until A actually
    releases -- then B's acquire is observed to complete. Ordering is proven by an
    append-only, lock-protected event list, never by wall-clock comparison.
    """
    events: list[str] = []
    events_guard = threading.Lock()

    def record(label: str) -> None:
        with events_guard:
            events.append(label)

    a_holds = threading.Event()
    release_a = threading.Event()
    b_done = threading.Event()

    def thread_a() -> None:
        lock = IntegrationLock(common_dir)
        assert lock.acquire(_PROCESS_TEST_TIMEOUT) is True
        record("a_acquired")
        a_holds.set()
        release_a.wait(timeout=_PROCESS_TEST_TIMEOUT)
        record("a_released")
        lock.release()

    def thread_b() -> None:
        assert a_holds.wait(timeout=_PROCESS_TEST_TIMEOUT)
        lock = IntegrationLock(common_dir)
        assert lock.acquire(_PROCESS_TEST_TIMEOUT) is True
        record("b_acquired")
        lock.release()
        b_done.set()

    ta = threading.Thread(target=thread_a)
    tb = threading.Thread(target=thread_b)
    ta.start()
    tb.start()

    assert a_holds.wait(timeout=_PROCESS_TEST_TIMEOUT)
    # B is contending on a real lock A holds -- give it a bounded window to (incorrectly)
    # sneak in, then prove it hasn't.
    assert not b_done.wait(timeout=0.3)
    release_a.set()
    assert b_done.wait(timeout=_PROCESS_TEST_TIMEOUT)

    ta.join(timeout=_PROCESS_TEST_TIMEOUT)
    tb.join(timeout=_PROCESS_TEST_TIMEOUT)
    assert events == ["a_acquired", "a_released", "b_acquired"]


def test_two_threads_opposite_repo_order_no_deadlock(tmp_path: Path) -> None:
    """Two distinct repos' locks, acquired in OPPOSITE order by two threads. This alone
    does not guarantee deadlock-freedom in general (that's `Integrator`'s job: always
    acquire in sorted repo-key order) -- this test only proves `IntegrationLock` itself
    never deadlocks or hangs past its bound even under a worst-case interleaving; a bounded
    `acquire(timeout=...)` on each side means the pathological ordering degrades to one
    side timing out, not a hang.
    """
    dir_a = tmp_path / "a.git"
    dir_b = tmp_path / "b.git"
    dir_a.mkdir()
    dir_b.mkdir()

    results: dict[str, bool] = {}

    def worker(label: str, first: str, second: str) -> None:
        lock1 = IntegrationLock(first)
        lock2 = IntegrationLock(second)
        got1 = lock1.acquire(2.0)
        if got1:
            got2 = lock2.acquire(2.0)
            results[label] = got1 and got2
            if got2:
                lock2.release()
            lock1.release()
        else:
            results[label] = False

    t1 = threading.Thread(target=worker, args=("t1", str(dir_a), str(dir_b)))
    t2 = threading.Thread(target=worker, args=("t2", str(dir_b), str(dir_a)))
    t1.start()
    t2.start()
    t1.join(timeout=10.0)
    t2.join(timeout=10.0)

    assert not t1.is_alive()
    assert not t2.is_alive()
    # Both threads ran their worker body to completion (recorded a result) rather than
    # hanging silently past the join timeout -- the property under test is "no hang", not
    # "both always succeed" (one side may legitimately time out under the opposite-order
    # interleaving).
    assert set(results) == {"t1", "t2"}


# --------------------------------------------------------------------------------------
# AC-1(b)/(c): a REAL second process
# --------------------------------------------------------------------------------------


def _hold_flock_until_released(
    lock_path: str,
    ready: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event,
) -> None:
    """Subprocess body: open + flock the companion file, signal ready, hold until told to
    release (or the process is killed, in which case the kernel releases it for us)."""
    import fcntl

    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    ready.set()
    release.wait(timeout=30.0)
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def _hold_flock_forever(lock_path: str, ready: multiprocessing.synchronize.Event) -> None:
    """Subprocess body for the kill test: holds the flock until terminated."""
    import fcntl

    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    ready.set()
    time.sleep(30.0)
    fcntl.flock(fd, fcntl.LOCK_UN)  # pragma: no cover -- normally killed before reaching here
    os.close(fd)


def test_real_second_process_blocks_and_timeout_returns_false(common_dir: str) -> None:
    ctx = multiprocessing.get_context("fork")
    lock_path = str(Path(common_dir) / LOCK_FILENAME)
    ready = ctx.Event()
    release = ctx.Event()
    proc = ctx.Process(target=_hold_flock_until_released, args=(lock_path, ready, release))
    proc.start()
    try:
        assert ready.wait(timeout=_PROCESS_TEST_TIMEOUT)
        lock = IntegrationLock(common_dir)
        start = time.monotonic()
        got = lock.acquire(0.5)
        elapsed = time.monotonic() - start
        assert got is False
        assert elapsed < _PROCESS_TEST_TIMEOUT  # bounded, not a hang
    finally:
        release.set()
        proc.join(timeout=_PROCESS_TEST_TIMEOUT)


def test_lock_released_when_holding_process_is_killed(common_dir: str) -> None:
    ctx = multiprocessing.get_context("fork")
    lock_path = str(Path(common_dir) / LOCK_FILENAME)
    ready = ctx.Event()
    proc = ctx.Process(target=_hold_flock_forever, args=(lock_path, ready))
    proc.start()
    try:
        assert ready.wait(timeout=_PROCESS_TEST_TIMEOUT)
        lock = IntegrationLock(common_dir)
        assert lock.acquire(0.5) is False  # still held

        proc.kill()
        proc.join(timeout=_PROCESS_TEST_TIMEOUT)
        assert not proc.is_alive()

        # Kernel releases the flock on process death -- should now succeed promptly.
        assert lock.acquire(_PROCESS_TEST_TIMEOUT) is True
        lock.release()
    finally:
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=_PROCESS_TEST_TIMEOUT)
