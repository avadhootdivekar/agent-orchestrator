"""The store-busy CLI path across a real process boundary (T-U2ERMo item 6; AC-26 part, FR-22).

A ``spawn``-context child process holds the real ``users.lock`` (``flock``) while the real
``ao auth set-password`` command (the root ``ao`` app through ``CliRunner``, ``--password-stdin``)
runs with the store lock timeout shortened. The command must give up with exit 1, name the lock
file and say "busy", well inside ``MAX_ELAPSED_SECONDS``, and must not have touched the store.
The holder is always released in a ``finally`` and joined with a timeout, so the test cannot hang
(HLD 20.2: multiprocess rules).
"""

from __future__ import annotations

import multiprocessing
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agent_orchestrator import cli as root_cli
from agent_orchestrator import fsutil
from agent_orchestrator.auth import passwords
from agent_orchestrator.auth import store as store_module
from agent_orchestrator.auth.constants import (
    STORE_LOCK_TIMEOUT_SECONDS,
    USERS_FILENAME,
    USERS_LOCK_FILENAME,
)
from tests.auth.helpers.crypto import TEST_PARAMS

runner = CliRunner()

PATCHED_LOCK_TIMEOUT_SECONDS = 0.2  # ticket item 6: STORE_LOCK_TIMEOUT_SECONDS patched to 0.2
MAX_ELAPSED_SECONDS = 2.0
HOLDER_READY_TIMEOUT_SECONDS = 30.0  # spawn start-up (imports) can be slow on a loaded box
HOLDER_JOIN_TIMEOUT_SECONDS = 15.0
HOLDER_LOCK_TIMEOUT_SECONDS = 10.0
PW = "correct horse battery"
NEW_PW = "another long passphrase"


def hold_lock(lock_path: str, ready: Any, release: Any) -> None:
    """Spawn target (module level so the child can import it): hold ``flock`` until released."""
    with fsutil.FileLock(Path(lock_path), timeout=HOLDER_LOCK_TIMEOUT_SECONDS):
        ready.set()
        release.wait(HOLDER_READY_TIMEOUT_SECONDS)


@contextmanager
def lock_held_by_another_process(lock_path: Path) -> Iterator[None]:
    ctx = multiprocessing.get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    proc = ctx.Process(target=hold_lock, args=(str(lock_path), ready, release))
    proc.start()
    try:
        assert ready.wait(HOLDER_READY_TIMEOUT_SECONDS), "the holder never took the lock"
        yield
    finally:
        release.set()
        proc.join(timeout=HOLDER_JOIN_TIMEOUT_SECONDS)
        if proc.is_alive():  # pragma: no cover - only on a wedged child
            proc.kill()
            proc.join(timeout=HOLDER_JOIN_TIMEOUT_SECONDS)
    assert proc.exitcode == 0


@pytest.fixture(autouse=True)
def hermetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AO_UI_AUTH", "AO_UI_AUTH_TOTP", "AO_AUTH_DIR", "AO_AUTH_STATE_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(passwords, "CURRENT_PARAMS", TEST_PARAMS)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)


@pytest.fixture()
def short_lock_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shorten the store lock timeout.

    ``UserStore.__init__`` binds ``STORE_LOCK_TIMEOUT_SECONDS`` as a keyword default at definition
    time, so patching the constant would not reach it; the keyword default itself is patched.
    """
    kwdefaults = store_module.UserStore.__init__.__kwdefaults__
    assert kwdefaults is not None and kwdefaults["lock_timeout"] == STORE_LOCK_TIMEOUT_SECONDS
    monkeypatch.setitem(kwdefaults, "lock_timeout", PATCHED_LOCK_TIMEOUT_SECONDS)


def test_set_password_reports_busy_when_another_process_holds_the_lock(
    tmp_path: Path, short_lock_timeout: None
) -> None:
    auth_dir = (tmp_path / "auth").resolve()
    added = runner.invoke(
        root_cli.app,
        ["auth", "add-user", "alice", "--password-stdin", "--auth-dir", str(auth_dir)],
        input=PW + "\n",
    )
    assert added.exit_code == 0, added.output
    users_before = (auth_dir / USERS_FILENAME).read_bytes()
    lock_path = auth_dir / USERS_LOCK_FILENAME
    assert lock_path.exists()

    with lock_held_by_another_process(lock_path):
        started = time.monotonic()
        result = runner.invoke(
            root_cli.app,
            ["auth", "set-password", "alice", "--password-stdin", "--auth-dir", str(auth_dir)],
            input=NEW_PW + "\n",
        )
        elapsed = time.monotonic() - started

    assert result.exit_code == 1, result.output
    assert "busy" in result.stderr
    assert USERS_LOCK_FILENAME in result.stderr
    assert elapsed < MAX_ELAPSED_SECONDS, f"gave up after {elapsed:.2f}s"
    assert elapsed >= PATCHED_LOCK_TIMEOUT_SECONDS * 0.5  # it did wait for the lock first
    assert (auth_dir / USERS_FILENAME).read_bytes() == users_before  # nothing was written
    assert NEW_PW not in result.output  # the password is never echoed, even on failure


def test_the_same_command_succeeds_once_the_holder_has_released(
    tmp_path: Path, short_lock_timeout: None
) -> None:
    """Control: the failure above is the held lock, not the setup."""
    auth_dir = (tmp_path / "auth").resolve()
    for args, text in (
        (["add-user", "alice", "--password-stdin"], PW + "\n"),
        (["set-password", "alice", "--password-stdin"], NEW_PW + "\n"),
    ):
        result = runner.invoke(
            root_cli.app, ["auth", *args, "--auth-dir", str(auth_dir)], input=text
        )
        assert result.exit_code == 0, result.output
