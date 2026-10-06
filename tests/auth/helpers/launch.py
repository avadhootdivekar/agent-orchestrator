"""``prepare_auth`` / ``ao ui`` test helpers (owner: T-jVqH8w; HLD section 20.2).

``make_launch_env`` seeds a real private user store (via ``make_store``) and a workspace, and
returns the environment that points ``resolve_auth_settings`` at them. Test-only.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from tests.auth.helpers.store import make_store

AUDIT_FILENAME = "audit.jsonl"


@dataclass
class LaunchEnv:
    root: Path
    store_dir: Path
    state_dir: Path
    workspace: Path
    env: dict[str, str] = field(default_factory=dict)


def make_launch_env(tmp_path: Path, users: Sequence[str] = ("alice",)) -> LaunchEnv:
    """``<tmp>/store`` (seeded with ``users``), ``<tmp>/state`` and ``<tmp>/ws`` (a git root).

    ``env`` carries ``AO_AUTH_DIR`` and ``AO_AUTH_STATE_DIR`` so every read lands in them.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = make_store(tmp_path, users)
    workspace = tmp_path / "ws"
    (workspace / ".git").mkdir(parents=True)
    return LaunchEnv(
        root=tmp_path,
        store_dir=store.paths.store_dir,
        state_dir=store.paths.state_dir,
        workspace=workspace,
        env={
            "AO_AUTH_DIR": str(store.paths.store_dir),
            "AO_AUTH_STATE_DIR": str(store.paths.state_dir),
        },
    )


def audit_events(state_dir: Path) -> list[str]:
    """The event names in ``<state_dir>/audit.jsonl`` (empty when there is no file)."""
    path = state_dir / AUDIT_FILENAME
    if not path.exists():
        return []
    return [json.loads(line)["event"] for line in path.read_text().splitlines() if line]


def write_ws_config(workspace: Path, text: str) -> Path:
    path = workspace / ".ao" / "config.yaml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path.resolve()


def auth_block(*lines: str) -> str:
    return "ui:\n  auth:\n" + "".join(f"    {line}\n" for line in lines)


class RecordingUvicorn:
    """Stand-in for the uvicorn module that records ``run()`` calls instead of serving."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def run(self, *args: object, **kwargs: object) -> None:
        self.calls.append((args, kwargs))
