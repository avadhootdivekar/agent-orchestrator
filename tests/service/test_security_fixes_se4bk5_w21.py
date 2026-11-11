"""T-Se4Bk5 w21 regressions: S-2 FIFO-safe prompt read, S-3 NUL at load, S-4 secret TOCTOU/NUL,
S-5 durable atomic webhook enqueue."""

from __future__ import annotations

import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.schedules.errors import ScheduleFileError
from agent_orchestrator.schedules.models import ScheduleFile
from agent_orchestrator.service import statefile, webhook
from agent_orchestrator.service.governance_gate import MAX_PROMPT_BYTES
from agent_orchestrator.service.schedule_launch import LaunchResolutionError, ScheduledLauncher
from agent_orchestrator.service.webhook import WebhookDelivery, WebhookRequestQueue, resolve_secret


def _launcher(tmp_path: Path) -> ScheduledLauncher:
    return ScheduledLauncher(
        tmp_path / "state",
        process_supervisor_factory=lambda root: None,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )


def _store(root: Path) -> LocalFsArtifactStore:
    return LocalFsArtifactStore(str(root))


def _run_with_timeout(fn: Any, seconds: float = 3.0) -> tuple[bool, Any]:
    out: dict[str, Any] = {}

    def target() -> None:
        try:
            out["v"] = fn()
        except BaseException as exc:  # noqa: BLE001 - propagate to the assertion
            out["e"] = exc

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(seconds)
    return (not t.is_alive()), out


# -- S-2 ------------------------------------------------------------------------------------------


def test_read_prompt_fifo_does_not_hang(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    os.mkfifo(root / "p.md")
    lau = _launcher(tmp_path)
    done, out = _run_with_timeout(lambda: lau._read_prompt(_store(root), "p.md", None))
    assert done, "FIFO prompt_file blocked the read"
    assert isinstance(out.get("e"), LaunchResolutionError)


def test_read_prompt_size_cap_and_ok(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "big.md").write_bytes(b"x" * (MAX_PROMPT_BYTES + 1))
    (root / "ok.md").write_text("hello", encoding="utf-8")
    lau = _launcher(tmp_path)
    with pytest.raises(LaunchResolutionError):
        lau._read_prompt(_store(root), "big.md", None)
    assert lau._read_prompt(_store(root), "ok.md", None) == "hello"


# -- S-3 ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["workflow", "prompt_file"])
@pytest.mark.parametrize("value", ["a\x00b", "a\nb", "a\x7fb"])
def test_control_chars_in_path_refused_at_load(field: str, value: str) -> None:
    raw: dict[str, Any] = {
        "id": "s1",
        "kind": "cron",
        "schedule": "0 * * * *",
        "workflow": "w.json",
        "limits": {"cost": {"per_run_usd": 1.0}},
    }
    raw[field] = value
    with pytest.raises(ScheduleFileError):
        ScheduleFile.from_data({"version": "1.0", "schedules": [raw]}, source="t")


# -- S-4 ------------------------------------------------------------------------------------------


def _target(root: Path, rel: str) -> Any:
    from tests.service.test_webhook import wh_binding

    binding = wh_binding(secret_env=None, secret_file="placeholder")
    assert binding.webhook is not None
    # bypass the load-time NUL check (S-3a) to exercise the use-time wall
    object.__setattr__(binding.webhook, "secret_file", rel)
    return webhook.WebhookTarget(str(root), binding)


def test_secret_file_nul_is_none_not_valueerror(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    assert resolve_secret(_target(root, "a\x00b")) is None


def test_secret_file_fifo_does_not_hang(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    os.mkfifo(root / "s.key", 0o600)
    done, out = _run_with_timeout(lambda: resolve_secret(_target(root, "s.key")))
    assert done and out.get("v") is None


def test_secret_file_symlink_swapped_after_check_not_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    real = root / "s.key"
    real.write_text("k" * 32)
    real.chmod(0o600)
    outside = tmp_path / "outside.key"
    outside.write_text("o" * 32)
    outside.chmod(0o600)
    real_open = os.open

    def swapping_open(path: Any, flags: int, *a: Any, **k: Any) -> int:
        if str(path).endswith("s.key"):  # swap in between the check and the open
            real.unlink()
            os.symlink(outside, real)
        return real_open(path, flags, *a, **k)

    monkeypatch.setattr(webhook.os, "open", swapping_open)
    assert resolve_secret(_target(root, "s.key")) is None


def _reasons(target: Any) -> tuple[bytes | None, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    out = resolve_secret(target, emit=lambda event, **f: events.append({"event": event, **f}))
    return out, events


def test_r5_secret_rejections_state_cause_and_never_leak(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    os.mkfifo(root / "d", 0o600)
    (root / "empty.key").write_text("")
    (root / "empty.key").chmod(0o600)
    real = root / "real.key"
    real.write_text("TOPSECRET" * 5)
    real.chmod(0o600)
    cases = {
        "missing.key": ("secret_unreadable", "ENOENT"),
        "d": ("secret_not_regular_file", None),
        "empty.key": ("secret_empty", None),
        "a\x00b": ("secret_invalid_path", None),
    }
    for rel, (reason, detail) in cases.items():
        out, events = _reasons(_target(root, rel))
        assert out is None, rel
        assert [e["reason"] for e in events] == [reason], rel
        assert events[0].get("detail") == detail, rel
        assert "TOPSECRET" not in repr(events)


# -- S-5 ------------------------------------------------------------------------------------------


def _delivery() -> WebhookDelivery:
    return WebhookDelivery(
        workspace_slug="ws",
        schedule_id="s1",
        received_at=datetime(2026, 1, 1, tzinfo=UTC),
        delivery_id="d1",
    )


def test_enqueue_uses_durable_atomic_write_and_leaves_no_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[bool] = []
    real = statefile.atomic_write_bytes

    def spy(path: Path, data: bytes, *, durable: bool = False) -> None:
        calls.append(durable)
        real(path, data, durable=durable)

    monkeypatch.setattr(webhook, "atomic_write_bytes", spy)
    q = WebhookRequestQueue(tmp_path)
    path = q.enqueue(_delivery())
    assert calls == [True] and path.is_file()
    assert not list(path.parent.glob("*.tmp"))


def test_enqueue_failure_leaves_no_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(src: Any, dst: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(statefile.os, "replace", boom)
    q = WebhookRequestQueue(tmp_path)
    with pytest.raises(OSError):
        q.enqueue(_delivery())
    assert not [p for p in q._dir.iterdir()]


def test_webhook_has_no_private_fsync_idiom() -> None:
    assert not hasattr(webhook, "_fsync_dir")
