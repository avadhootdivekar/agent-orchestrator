"""I-1 subprocess entry point: run every cache-off scenario under poisoned imports.

Run as ``python -m tests.cache._noop_subprocess`` from the repo root (a fresh interpreter, so
nothing the pytest session imported can contaminate the module check). It

1. installs the poisoned finder BEFORE `agent_orchestrator` is imported,
2. replaces `Orchestrator._result_cache_lookup` / `_result_cache_store` with raisers (create
   if absent, the `raising=False` equivalent, so it is valid before T-XpF1pF lands),
3. runs each scenario in `_noop_scenarios.SCENARIOS` with the cache off, and
4. prints one JSON report line and exits 0 only if every check holds.

Two test-only flags prove the proof itself can fail (negative controls):
``--sabotage-import MODULE`` makes the first executor dispatch import MODULE, and
``--no-poison`` skips step 1 so the loaded-modules check is exercised on its own.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from tests.cache import _noop_poison  # imports no agent_orchestrator module

REPORT_PREFIX = "NOOP_REPORT="
_FORBIDDEN_KEY = "result_cache"
_CACHE_DIR_NAME = "cache"
_ENGINE_HOOKS = ("_result_cache_lookup", "_result_cache_store")
# Environment that would leak the developer's real git/ao state into the isolation scenario.
_SCRUBBED_ENV = ("XDG_STATE_HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM")


def contains_key(node: Any, key: str) -> bool:
    """True if `key` appears as a dict key at any depth of a parsed-JSON structure."""
    if isinstance(node, dict):
        return key in node or any(contains_key(v, key) for v in node.values())
    if isinstance(node, list):
        return any(contains_key(v, key) for v in node)
    return False


def install_engine_hook_raisers(orchestrator_cls: Any, calls: list[str]) -> None:
    """Make the engine's cache hooks raise if the cache-off path ever reaches them."""

    def make(name: str) -> Any:
        def _raiser(*_args: Any, **_kwargs: Any) -> None:
            calls.append(name)
            raise AssertionError(f"cache-off run reached Orchestrator.{name}")

        return _raiser

    for name in _ENGINE_HOOKS:
        setattr(orchestrator_cls, name, make(name))


def install_import_sabotage(executor_cls: Any, module: str, log: list[str]) -> None:
    """Make the first executor dispatch import `module`, recording what happened."""
    original = executor_cls.execute

    def sabotaged(self: Any, *args: Any, **kwargs: Any) -> Any:
        if not log:
            try:
                importlib.import_module(module)
            except ImportError as exc:
                log.append(f"ImportError: {exc}")
                raise
            log.append(f"imported {module}")
        return original(self, *args, **kwargs)

    executor_cls.execute = sabotaged


def workspace_problems(ws: Path) -> list[str]:
    """Cache-off footprint checks on one scenario workspace."""
    problems: list[str] = []
    root = ws / ".orchestrator"
    if (root / _CACHE_DIR_NAME).exists():
        problems.append(".orchestrator/cache exists")
    stray = [p for p in root.rglob(_CACHE_DIR_NAME) if p.is_dir()] if root.exists() else []
    if stray:
        problems.append(f"cache directories under .orchestrator: {stray}")
    snapshots = sorted(root.glob("runs/*/status.json"))
    if not snapshots:
        problems.append("no status.json written (check would be vacuous)")
    for snap in snapshots:
        if contains_key(json.loads(snap.read_text()), _FORBIDDEN_KEY):
            problems.append(f"{_FORBIDDEN_KEY} key present in {snap.relative_to(ws)}")
    return problems


def run_scenarios(only: list[str] | None) -> dict[str, dict[str, Any]]:
    from tests.cache import _noop_scenarios

    results: dict[str, dict[str, Any]] = {}
    for name, scenario in _noop_scenarios.SCENARIOS.items():
        if only and name not in only:
            continue
        problems: list[str] = []
        status = None
        expected = None
        with tempfile.TemporaryDirectory(prefix=f"noop-{name}-") as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            try:
                outcome = scenario(ws)
            except Exception as exc:
                problems.append(f"scenario raised {type(exc).__name__}: {exc}")
            else:
                status, expected = outcome.state.status, outcome.expected_status
                if status != expected:
                    problems.append(f"status {status!r} != expected {expected!r}")
                if outcome.state.result_cache != {}:
                    problems.append(f"state.result_cache not empty: {outcome.state.result_cache}")
                problems.extend(workspace_problems(ws))
        results[name] = {"status": status, "expected": expected, "problems": problems}
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-poison", action="store_true")
    parser.add_argument("--sabotage-import", metavar="MODULE")
    parser.add_argument("--only", help="comma-separated scenario names")
    args = parser.parse_args()

    if "agent_orchestrator" in sys.modules:
        raise SystemExit("agent_orchestrator was imported before the poison was installed")
    if not args.no_poison:
        _noop_poison.install()

    with tempfile.TemporaryDirectory(prefix="noop-home-") as home:
        os.environ["HOME"] = home
        os.environ["AO_STATE_DIR"] = str(Path(home) / "ao-state")
        for var in _SCRUBBED_ENV:
            os.environ.pop(var, None)

        from agent_orchestrator.engine import Orchestrator
        from agent_orchestrator.executors.fake import FakeExecutor

        hook_calls: list[str] = []
        sabotage_log: list[str] = []
        install_engine_hook_raisers(Orchestrator, hook_calls)
        if args.sabotage_import:
            install_import_sabotage(FakeExecutor, args.sabotage_import, sabotage_log)

        only = args.only.split(",") if args.only else None
        results = run_scenarios(only)

    loaded = _noop_poison.loaded_cache_modules()
    unexpected = sorted(loaded - _noop_poison.ALLOWED_CACHE_MODULES)
    failures = [f"{n}: {p}" for n, r in results.items() for p in r["problems"]]
    if hook_calls:
        failures.append(f"engine cache hooks were called: {hook_calls}")
    if unexpected:
        failures.append(f"unexpected cache modules loaded: {unexpected}")
    report = {
        "poison_installed": not args.no_poison,
        "scenarios": results,
        "loaded_cache_modules": sorted(loaded),
        "unexpected_cache_modules": unexpected,
        "engine_hook_calls": hook_calls,
        "sabotage": sabotage_log,
        "failures": failures,
    }
    print(REPORT_PREFIX + json.dumps(report, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
