"""T-nPMuz4: the G0 protocol document's commands and field names stay true to the real CLI.

The document (`docs-md/result-cache-g0-protocol.md`) marks its machine-checkable parts with HTML
comments: `<!-- g0-fields:<group> f1 f2 ... -->` (dotted paths into the CLI JSON) and
`<!-- g0-cmd:<name> -->` right before a fenced bash block. The test runs a tiny fixture twice
under `AO_CACHE=shadow` through `CliRunner` (outputs deleted in between), then

- asserts every documented field exists in the real `ao report-usage --json` / `ao cache stats
  --json` output and that the second run shows lookups > 0 and would_hits > 0;
- executes the documented collection blocks verbatim with bash, against that workspace, with
  `$AO` pointing at a launcher for the in-tree CLI (so a doc edit that breaks a command, or a CLI
  rename that the doc missed, fails here).
"""

from __future__ import annotations

import itertools
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agent_orchestrator import runstate as runstate_mod
from agent_orchestrator.cli import app
from tests.cache._wiring_fixture import write_fixture

DOC = Path(__file__).resolve().parents[2] / "docs-md" / "result-cache-g0-protocol.md"
WORKFLOW_ID = "wiring-wf"  # `id` in tests/cache/_wiring_fixture.py
BASH_TIMEOUT_SECONDS = 120
_CLOCK_START = datetime(2026, 10, 5, 9, 0, 0, tzinfo=UTC)
_FIELDS_RE = re.compile(r"<!-- g0-fields:(\w+) ([^>]*?) -->")
_CMD_RE = re.compile(r"<!-- g0-cmd:([\w-]+) -->\n```bash\n(.*?)```", re.S)

runner = CliRunner()


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _fields() -> dict[str, list[str]]:
    return {name: rest.split() for name, rest in _FIELDS_RE.findall(_doc())}


def _commands() -> dict[str, str]:
    return {name: body for name, body in _CMD_RE.findall(_doc())}


def _dig(doc: dict[str, Any], dotted: str) -> bool:
    cur: Any = doc
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """The wiring fixture, run twice under `AO_CACHE=shadow` with the output deleted in between."""
    monkeypatch.delenv("AO_CACHE", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AO_WORKSPACE_ROOT", str(tmp_path))
    ticks = itertools.count()  # run ids are second-granular: tick the clock once per read
    monkeypatch.setattr(
        runstate_mod, "_utc_now", lambda: _CLOCK_START + timedelta(seconds=next(ticks))
    )
    monkeypatch.setenv("AO_CACHE", "shadow")
    args = write_fixture(tmp_path)
    for _ in range(2):
        res = runner.invoke(app, ["run", *args])
        assert res.exit_code == 0, res.output
        shutil.rmtree(tmp_path / "out", ignore_errors=True)
    yield tmp_path


def _json(workspace: Path, *argv: str) -> dict[str, Any]:
    res = runner.invoke(app, [*argv, "--workspace", str(workspace), "--json"])
    assert res.exit_code == 0, res.output
    doc: dict[str, Any] = json.loads(res.stdout)
    return doc


class TestDocumentedFields:
    def test_doc_declares_both_field_groups(self) -> None:
        groups = _fields()
        assert set(groups) == {"stats", "result_cache"}
        assert {"lookups", "would_hits", "misses", "ineligible", "miss_reasons"} <= set(
            groups["result_cache"]
        )
        assert {"entries", "bytes.total", "expired_entries"} <= set(groups["stats"])

    def test_documented_result_cache_fields_exist_in_report_usage_json(
        self, workspace: Path
    ) -> None:
        rc = _json(workspace, "report-usage")["result_cache"]
        assert [f for f in _fields()["result_cache"] if not _dig(rc, f)] == []

    def test_documented_stats_fields_exist_in_cache_stats_json(self, workspace: Path) -> None:
        stats = _json(workspace, "cache", "stats")
        assert [f for f in _fields()["stats"] if not _dig(stats, f)] == []

    def test_window_shows_would_hits_on_the_repeat(self, workspace: Path) -> None:
        rc = _json(workspace, "report-usage")["result_cache"]
        assert rc["lookups"] > 0
        assert rc["would_hits"] > 0
        assert rc["hits"] == 0  # shadow never restores


class TestDocumentedCommands:
    """Run the document's bash blocks verbatim (only the Step 0 variables are supplied)."""

    @pytest.fixture
    def env(self, workspace: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
        launcher = tmp_path_factory.mktemp("g0-launcher") / "ao"
        entry = "from agent_orchestrator.cli import app; app()"
        launcher.write_text(
            f'#!/bin/sh\nexec {sys.executable} -c "{entry}" "$@"\n', encoding="utf-8"
        )
        launcher.chmod(0o755)
        return {
            "PATH": "/usr/bin:/bin",
            "AO": str(launcher),
            "WS": str(workspace),
            "WF_ID": WORKFLOW_ID,
            "WINDOW_START": "19700101T000000Z",
            "DAYS": "7",
        }

    def _bash(self, name: str, env: dict[str, str], *, prelude: str = "") -> str:
        script = prelude + _commands()[name]
        proc = subprocess.run(
            ["bash", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=BASH_TIMEOUT_SECONDS,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout

    def test_every_documented_command_is_present(self) -> None:
        assert set(_commands()) == {
            "stats-start",
            "collect",
            "stats-end",
            "miss-components",
            "rate",
        }

    @pytest.mark.parametrize("name", ["stats-start", "stats-end"])
    def test_stats_blocks_print_the_documented_fields(self, name: str, env: dict[str, str]) -> None:
        out = self._bash(name, env)
        for field in ("entries=", "expired_entries=", "bytes.total=", "oldest_created_at="):
            assert field in out
        assert "entries=1 " in out  # the fixture's one opted-in task is stored once

    def test_collect_block_prints_the_result_cache_object(self, env: dict[str, str]) -> None:
        out = self._bash("collect", env)
        assert out.count("--run-id ") == 2  # the window is exactly the two shadow runs
        obj = json.loads(out[out.index("{") :])
        assert obj["lookups"] == 2 and obj["would_hits"] == 1 and obj["misses"] == 1

    def test_rate_block_computes_rate_and_weekly_spend(self, env: dict[str, str]) -> None:
        out = self._bash("rate", env, prelude=_commands()["collect"].split("\n$AO")[0] + "\n")
        assert "would_hit_rate=0.500" in out
        assert "avoidable_usd_per_week=" in out

    def test_miss_components_block_reads_run_log(self, env: dict[str, str]) -> None:
        out = self._bash("miss-components", env)
        assert "miss reasons: {'not_found': 1}" in out
        assert "components that changed vs the same task last miss:" in out
        assert "cache.skip (phase/reason):" in out
