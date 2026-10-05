"""T-o95l1M AC-1..AC-5 (E-9, E-10, E-11, U-LZ2) and the flag/env/config precedence, end to end.

Everything goes through `CliRunner` (`ao run`, `ao resume`, `ao status`, `ao report-usage`) with
`executor: fake`. `FakeExecutor` reports no cost, so a dispatch wrapper adds cost and tokens to
successful results (the cache stores what the task cost; a hit then reports it as saved).
"""

from __future__ import annotations

import ast
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from typer.testing import CliRunner

import agent_orchestrator
from agent_orchestrator import cli as cli_mod
from agent_orchestrator import runstate as runstate_mod
from agent_orchestrator.cache import cli as cache_cli
from agent_orchestrator.cache.coordinator import ResultCache
from agent_orchestrator.cli import app
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import TaskResult
from tests.cache._report_states import RESULT_CACHE_USAGE_SCHEMA
from tests.cache._wiring_fixture import TASK_OUTPUT, write_fixture

runner = CliRunner()

COST_USD = 0.75
IN_TOKENS = 1200
OUT_TOKENS = 340
SAVED_TOKENS = IN_TOKENS + OUT_TOKENS
BANNER_PREFIX = "Result cache: "
SUMMARY_PREFIX = "Result cache: hits="
REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = Path(agent_orchestrator.__file__).resolve().parents[1]
# E-2-style allow-list for a cache-off CLI process (HLD 8.7.5 + the T-o95l1M cache.cli deviation).
CLI_PATH_ALLOWED_CACHE_MODULES = {
    "agent_orchestrator.cache",
    "agent_orchestrator.cache.constants",
    "agent_orchestrator.cache.settings",
    "agent_orchestrator.cache.cli",
}
REPORT_MODULE = "agent_orchestrator.cache.report"
SUBPROCESS_TIMEOUT_SECONDS = 180
_CLOCK_START = datetime(2026, 10, 5, 9, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in ("AO_CACHE", "AO_WORKSPACE_ROOT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AO_WORKSPACE_ROOT", str(tmp_path))
    # Run ids are second-granular (`<workflow>-<UTC timestamp>`): two runs inside one real second
    # would share a directory. A ticking clock (one second per read) keeps every run distinct and
    # the test deterministic.
    ticks = itertools.count()
    monkeypatch.setattr(
        runstate_mod, "_utc_now", lambda: _CLOCK_START + timedelta(seconds=next(ticks))
    )


@pytest.fixture
def costly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `FakeExecutor` report cost and tokens on every successful result."""
    original = FakeExecutor.execute

    def execute(self: FakeExecutor, ctx: Any) -> TaskResult:
        result = original(self, ctx)
        if result.status != "succeeded":
            return result
        return result.model_copy(
            update={
                "actuals_available": True,
                "input_tokens": IN_TOKENS,
                "output_tokens": OUT_TOKENS,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "cost_usd": COST_USD,
            }
        )

    monkeypatch.setattr(FakeExecutor, "execute", execute)


def _lines(text: str, prefix: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.startswith(prefix)]


def _banners(res: Any) -> list[str]:
    return [ln for ln in _lines(res.stderr, BANNER_PREFIX)]


def _run(args: list[str], *extra: str) -> Any:
    return runner.invoke(app, ["run", *args, *extra])


def _cache_root(ws: Path) -> str:
    return str(ws.resolve() / ".orchestrator" / "cache")


def _rerun_args(tmp_path: Path) -> None:
    """Delete the task output so the next run dispatches (or hits) instead of skip-resuming."""
    shutil.rmtree(tmp_path / "out", ignore_errors=True)


def _run_dirs(ws: Path) -> list[str]:
    return sorted(p.name for p in (ws / ".orchestrator" / "runs").iterdir())


class TestBanner:
    """E-11 (AC-1)."""

    def test_cache_flag_prints_exactly_one_banner_line(self, tmp_path: Path) -> None:
        res = _run(write_fixture(tmp_path), "--cache")
        assert res.exit_code == 0, res.output
        assert _banners(res) == [
            f"Result cache: on (source=cli), 1 of 1 static task(s) opted in, "
            f"at {_cache_root(tmp_path)}"
        ]
        assert "WARNING" not in res.stderr

    def test_env_shadow_banner(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AO_CACHE", "shadow")
        res = _run(write_fixture(tmp_path))
        assert res.exit_code == 0, res.output
        assert _banners(res) == [
            f"Result cache: shadow (source=env), 1 of 1 static task(s) opted in, "
            f"at {_cache_root(tmp_path)}"
        ]

    def test_config_enabled_banner_names_the_config_source(self, tmp_path: Path) -> None:
        (tmp_path / ".ao").mkdir()
        (tmp_path / ".ao" / "config.yaml").write_text(
            'cache:\n  enabled: true\n  mode: "shadow"\n', encoding="utf-8"
        )
        res = _run(write_fixture(tmp_path))
        assert res.exit_code == 0, res.output
        assert _banners(res)[0].startswith("Result cache: shadow (source=config), 1 of 1")

    def test_no_opted_in_task_gets_the_explanatory_variant(self, tmp_path: Path) -> None:
        res = _run(write_fixture(tmp_path, opted_in=False), "--cache")
        assert res.exit_code == 0, res.output
        assert _banners(res) == [
            "Result cache: on (source=cli), but no task opts in "
            "(set defaults.cache: true or tasks[].cache: true), "
            f"at {_cache_root(tmp_path)}"
        ]

    def test_nested_workspace_prints_the_repository_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".git").mkdir()  # a repository ABOVE the workspace root (A-12)
        ws = tmp_path / "inner" / "ws"
        specs = tmp_path / "specs-dir"
        specs.mkdir()
        monkeypatch.setenv("AO_WORKSPACE_ROOT", str(ws))
        res = _run(write_fixture(specs, workspace=ws), "--cache")
        assert res.exit_code == 0, res.output
        warnings = _lines(res.stderr, "WARNING:")
        assert len(warnings) == 1
        assert (
            f"workspace {ws.resolve()} is inside the git repository at {tmp_path.resolve()}"
            in (warnings[0])
        )
        assert len(_banners(res)) == 1  # banner first, then the warning
        assert res.stderr.index(BANNER_PREFIX) < res.stderr.index("WARNING:")

    @pytest.mark.parametrize("mode", ["default", "no-cache-flag", "env-0"])
    def test_mode_off_prints_no_banner_and_builds_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
    ) -> None:
        args = write_fixture(tmp_path)
        if mode == "env-0":
            monkeypatch.setenv("AO_CACHE", "0")
        res = _run(args, *(["--no-cache"] if mode == "no-cache-flag" else []))
        assert res.exit_code == 0, res.output
        assert "result cache" not in (res.stdout + res.stderr).lower()
        assert not (tmp_path / ".orchestrator" / "cache").exists()

    def test_no_cache_flag_beats_env_and_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".ao").mkdir()
        (tmp_path / ".ao" / "config.yaml").write_text("cache:\n  enabled: true\n", encoding="utf-8")
        monkeypatch.setenv("AO_CACHE", "1")
        res = _run(write_fixture(tmp_path), "--no-cache")
        assert res.exit_code == 0, res.output
        assert _banners(res) == []

    def test_cache_flag_beats_env_shadow(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AO_CACHE", "shadow")
        res = _run(write_fixture(tmp_path), "--cache")
        assert _banners(res)[0].startswith("Result cache: on (source=cli)")

    def test_env_beats_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / ".ao").mkdir()
        (tmp_path / ".ao" / "config.yaml").write_text("cache:\n  enabled: true\n", encoding="utf-8")
        monkeypatch.setenv("AO_CACHE", "0")
        res = _run(write_fixture(tmp_path))
        assert _banners(res) == []


class TestSummaryLine:
    """E-9 (AC-2)."""

    @staticmethod
    def _two_runs(tmp_path: Path) -> tuple[Any, Any, str]:
        args = write_fixture(tmp_path)
        first = _run(args, "--cache")
        assert first.exit_code == 0, first.output
        _rerun_args(tmp_path)
        second = _run(args, "--cache")
        assert second.exit_code == 0, second.output
        return first, second, _run_dirs(tmp_path)[-1]

    def test_miss_run_prints_a_line_with_the_store(self, costly: None, tmp_path: Path) -> None:
        first, _, _ = self._two_runs(tmp_path)
        assert _lines(first.stdout, SUMMARY_PREFIX) == [
            "Result cache: hits=0 (saved ~$0.0000 est., ~0 tokens, ~0s) "
            "would_hits=0 misses=1 stored=1 ineligible=0"
        ]

    def test_hit_run_prints_the_exact_line(self, costly: None, tmp_path: Path) -> None:
        _, second, _ = self._two_runs(tmp_path)
        lines = _lines(second.stdout, SUMMARY_PREFIX)
        assert len(lines) == 1
        pattern = re.compile(
            rf"Result cache: hits=1 \(saved ~\${COST_USD:.4f} est\., ~{SAVED_TOKENS} tokens, "
            r"~\d+s\) would_hits=0 misses=0 stored=0 ineligible=0"
        )
        assert pattern.fullmatch(lines[0]), lines[0]
        # printed after `Total cost:` (HLD 8.8.3)
        assert second.stdout.index("Total cost:") < second.stdout.index(SUMMARY_PREFIX)
        # the task really was served from the cache
        assert (tmp_path / TASK_OUTPUT).exists()

    def test_status_command_prints_the_same_line(self, costly: None, tmp_path: Path) -> None:
        _, second, run_id = self._two_runs(tmp_path)
        status = runner.invoke(app, ["status", "--run-id", run_id])
        assert status.exit_code == 0, status.output
        assert _lines(status.stdout, SUMMARY_PREFIX) == _lines(second.stdout, SUMMARY_PREFIX)

    def test_a_run_without_current_records_prints_no_such_line(self, tmp_path: Path) -> None:
        res = _run(write_fixture(tmp_path))
        assert res.exit_code == 0, res.output
        assert _lines(res.stdout, SUMMARY_PREFIX) == []
        status = runner.invoke(app, ["status", "--run-id", _run_dirs(tmp_path)[-1]])
        assert status.exit_code == 0, status.output
        assert "Result cache" not in status.stdout

    def test_status_json_carries_the_brief_fields_for_the_hit(
        self, costly: None, tmp_path: Path
    ) -> None:
        _, _, run_id = self._two_runs(tmp_path)
        snap = json.loads(
            (tmp_path / ".orchestrator" / "runs" / run_id / "status.json").read_text("utf-8")
        )
        view = snap["tasks"][0]["result_cache"]
        assert view["hit"] is True and view["saved_tokens"] == SAVED_TOKENS
        assert view["saved_cost_usd"] == COST_USD and len(view["key"]) == 64
        assert snap["result_cache"]["hits"] == 1
        # the hit touched no cumulative spend (FR-9): the run total is zero
        assert snap["usage_totals"]["cost_usd"] == 0.0


class TestReportUsage:
    """E-10 (AC-3)."""

    @staticmethod
    def _usage(tmp_path: Path, *extra: str) -> Any:
        res = runner.invoke(app, ["report-usage", "--workspace", str(tmp_path), *extra])
        assert res.exit_code == 0, res.output
        return res

    def test_hit_runs_print_the_hits_line_only(self, costly: None, tmp_path: Path) -> None:
        TestSummaryLine._two_runs(tmp_path)
        res = self._usage(tmp_path)
        assert _lines(res.stdout, "Result cache") == [
            f"Result cache: 1 hit(s) across scanned runs, ~${COST_USD:.4f} avoided "
            "(est.; source-run cost incl. retries; not in costs below)"
        ]

    def test_shadow_runs_print_the_shadow_line_only(
        self, costly: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AO_CACHE", "shadow")
        args = write_fixture(tmp_path)
        assert _run(args).exit_code == 0
        _rerun_args(tmp_path)
        assert _run(args).exit_code == 0
        res = self._usage(tmp_path)
        assert _lines(res.stdout, "Result cache") == [
            f"Result cache (shadow): 1 would-hit(s) of 2 lookup(s), ~${COST_USD:.4f} "
            "avoidable (est.)"
        ]

    def test_json_includes_the_13_6_object(self, costly: None, tmp_path: Path) -> None:
        TestSummaryLine._two_runs(tmp_path)
        payload = json.loads(self._usage(tmp_path, "--json").stdout)
        obj = payload["result_cache"]
        jsonschema.validate(obj, RESULT_CACHE_USAGE_SCHEMA)
        assert (obj["hits"], obj["lookups"], obj["misses"]) == (1, 2, 1)
        assert obj["saved_cost_usd"] == COST_USD and obj["saved_tokens"] == SAVED_TOKENS

    def test_no_records_means_no_line_and_no_json_key(self, tmp_path: Path) -> None:
        assert _run(write_fixture(tmp_path)).exit_code == 0
        text = self._usage(tmp_path)
        assert "Result cache" not in text.stdout
        assert "result_cache" not in json.loads(self._usage(tmp_path, "--json").stdout)


class TestBothEntryPoints:
    """AC-4: `ao run` and `ao resume` pass the same helper's result to the Orchestrator."""

    @staticmethod
    def _spy(monkeypatch: pytest.MonkeyPatch) -> tuple[list[object], list[object]]:
        built: list[object] = []
        passed: list[object] = []
        real_build = cli_mod._build_result_cache
        real_init = Orchestrator.__init__

        def build(*a: Any, **kw: Any) -> object:
            result = real_build(*a, **kw)
            built.append(result)
            return result

        def init(self: Orchestrator, *a: Any, **kw: Any) -> None:
            passed.append(kw["result_cache"])  # keyword, in both constructions
            real_init(self, *a, **kw)

        monkeypatch.setattr(cli_mod, "_build_result_cache", build)
        monkeypatch.setattr(Orchestrator, "__init__", init)
        return built, passed

    def test_run_and_resume_pass_the_built_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built, passed = self._spy(monkeypatch)
        args = write_fixture(tmp_path)
        assert _run(args, "--cache").exit_code == 0
        run_id = _run_dirs(tmp_path)[-1]
        res = runner.invoke(app, ["resume", "--run-id", run_id, *args, "--cache"])
        assert res.exit_code == 0, res.output
        assert len(built) == len(passed) == 2
        assert all(isinstance(b, ResultCache) for b in built)
        assert all(p is b for p, b in zip(passed, built, strict=True))

    def test_cache_off_passes_none_on_both(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built, passed = self._spy(monkeypatch)
        args = write_fixture(tmp_path)
        assert _run(args).exit_code == 0
        res = runner.invoke(app, ["resume", "--run-id", _run_dirs(tmp_path)[-1], *args])
        assert res.exit_code == 0, res.output
        assert built == [None, None] and passed == [None, None]

    def test_resume_prints_the_banner_too(self, tmp_path: Path) -> None:
        args = write_fixture(tmp_path)
        assert _run(args).exit_code == 0
        res = runner.invoke(app, ["resume", "--run-id", _run_dirs(tmp_path)[-1], *args, "--cache"])
        assert res.exit_code == 0, res.output
        assert len(_banners(res)) == 1


# ------------------------------------------------------------------------------ U-LZ2 (subprocess)
_LAZY_SCRIPT = textwrap.dedent(
    """
    import json, sys
    from pathlib import Path
    from typer.testing import CliRunner
    from tests.cache._report_states import hit_rec, run_state, settled
    from tests.cache._wiring_fixture import write_fixture

    mode = sys.argv[1]
    if mode == "print":
        from agent_orchestrator.cli import _print_state, _print_status_snapshot
        recs = {"a": hit_rec()} if sys.argv[2] == "records" else {}
        state = run_state({"a": settled(), "b": settled()}, recs)
        _print_state(state)
        snap = {"run_id": "r", "status": "succeeded", "tasks": []}
        if recs:
            snap["result_cache"] = {"hits": 1, "saved_cost_usd": 0.1, "saved_tokens": 2,
                                    "saved_seconds": 3.0, "would_hits": 0, "misses": 0,
                                    "ineligible": 0, "stored": 0, "lookups": 1}
        _print_status_snapshot(snap)
    else:  # a cache-off `ao run` through the real CLI
        import os, tempfile
        from agent_orchestrator.cli import app
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            os.environ["AO_WORKSPACE_ROOT"] = d
            os.chdir(d)
            res = CliRunner().invoke(app, ["run", *write_fixture(root)])
            assert res.exit_code == 0, res.output
    loaded = [m for m in sys.modules if m.startswith("agent_orchestrator.cache")]
    print("REPORT=" + json.dumps(sorted(loaded)))
    """
)


def _loaded_cache_modules(*args: str) -> list[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AO_")}
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SRC_DIR), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
    )
    proc = subprocess.run(
        [sys.executable, "-c", _LAZY_SCRIPT, *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-1500:]
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("REPORT="))
    modules: list[str] = json.loads(line[len("REPORT=") :])
    return modules


@pytest.fixture(scope="module")
def modules_after_print_empty() -> Iterator[list[str]]:
    yield _loaded_cache_modules("print", "empty")


class TestLazyImports:
    def test_print_helpers_leave_report_unloaded_for_an_empty_map(
        self, modules_after_print_empty: list[str]
    ) -> None:  # U-LZ2
        assert REPORT_MODULE not in modules_after_print_empty

    def test_print_helpers_load_report_when_records_exist(self) -> None:  # positive control
        assert REPORT_MODULE in _loaded_cache_modules("print", "records")

    def test_cache_off_cli_run_loads_only_the_allow_listed_cache_modules(self) -> None:
        loaded = set(_loaded_cache_modules("run"))
        assert loaded <= CLI_PATH_ALLOWED_CACHE_MODULES, sorted(
            loaded - CLI_PATH_ALLOWED_CACHE_MODULES
        )
        assert "agent_orchestrator.cache.constants" in loaded  # the check is not vacuous
        assert "agent_orchestrator.cache.settings" in loaded
        assert REPORT_MODULE not in loaded


class TestCacheCliStaysImportLight:
    """Manager decision (HLD E-2 deviation): `cache/cli.py` is loaded by every `ao` start, so its
    MODULE-LEVEL imports are limited to typer plus `cache.constants` / `cache.settings`; every
    other cache import lives inside a command body. T-6tRKml must keep this rule."""

    ALLOWED_ABSOLUTE = {
        "__future__",
        "typer",
        "agent_orchestrator.cache.constants",
        "agent_orchestrator.cache.settings",
    }
    ALLOWED_RELATIVE = {"constants", "settings"}  # `from . import constants` / `from .settings`

    def test_module_level_imports_are_only_typer_constants_and_settings(self) -> None:
        tree = ast.parse(Path(cache_cli.__file__).read_text(encoding="utf-8"))
        for node in tree.body:  # module level only: function bodies may import lazily
            if isinstance(node, ast.Import):
                assert {a.name for a in node.names} <= self.ALLOWED_ABSOLUTE, ast.dump(node)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                assert node.module in self.ALLOWED_ABSOLUTE, ast.dump(node)
            elif isinstance(node, ast.ImportFrom):  # relative
                names = {a.name for a in node.names} if node.module is None else {node.module}
                assert names <= self.ALLOWED_RELATIVE, ast.dump(node)
