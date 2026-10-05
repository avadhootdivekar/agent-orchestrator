"""NFR-1 no-op proof, Part 1 of T-JCOAsq (HLD 8.7.5; catalogue rows I-1 and I-2).

I-1  A cache-off engine, run in a FRESH interpreter under a poisoned-import finder, completes
     ten representative workflows without importing any `agent_orchestrator.cache.*` module
     other than `cache` and `cache.constants`, without calling the engine's cache hooks, and
     without leaving a cache directory or a `result_cache` key behind. The poison itself is
     proven (self-tests + negative controls) so the check cannot be vacuous.
I-2  `ao run` stdout and `status.json` are byte-identical (after `<WS>` normalisation) to
     goldens captured from base commit `bb6d8a0`, serial and `max_parallel=3`. The very same
     capture script (`_noop_capture.py`) produced the goldens on the base tree and runs here.

Needs only code that exists on base plus the empty `cache` package marker, so it stays valid
before and after T-XpF1pF. See the T-JCOAsq HANDOFF for the golden recapture command.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

import agent_orchestrator
from tests.cache import _noop_poison as poison
from tests.cache._noop_capture import STATUS_FILE, STDOUT_FILE, VARIANTS, WORKSPACE_PLACEHOLDER
from tests.cache._noop_subprocess import REPORT_PREFIX, contains_key

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = Path(agent_orchestrator.__file__).resolve().parents[1]
GOLDEN_DIR = REPO_ROOT / "tests" / "fixtures" / "result_cache" / "golden"
SUBPROCESS_TIMEOUT_SECONDS = 180

# The ticket's required I-1 workflows (T-JCOAsq Part 1, item 3), by scenario name.
REQUIRED_SCENARIOS = {
    "serial",
    "parallel",  # max_parallel=3
    "emit",
    "loop",
    "router_join_any",
    "budget_wait",
    "breakers_quiet",
    "breakers_trip",
    "hooks",
    "isolation",
}
POISONED_MODULE = "agent_orchestrator.cache.keys"


def _subprocess_env() -> dict[str, str]:
    """Environment for child interpreters: this tree's `src` first, no ambient AO_* settings."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("AO_")}
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join([str(SRC_DIR), *([existing] if existing else [])])
    return env


def _run_module(module: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=REPO_ROOT,
        env=_subprocess_env(),
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
        check=False,
    )


def _report_of(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith(REPORT_PREFIX)]
    detail = f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr[-2000:]}"
    assert len(lines) == 1, f"no report line.\n{detail}"
    report: dict[str, Any] = json.loads(lines[0][len(REPORT_PREFIX) :])
    return report


# ---------------------------------------------------------------------------
# The poison works (I-1 self-tests)
# ---------------------------------------------------------------------------


class TestPoisonedFinder:
    """Prove the finder fires; the first draft defined `find_module`, which 3.12 ignores."""

    @pytest.mark.parametrize(
        "name",
        [
            "agent_orchestrator.cache.keys",
            "agent_orchestrator.cache.store",
            "agent_orchestrator.cache.settings",
            "agent_orchestrator.cache.report",
            "agent_orchestrator.cache.deeper.still",
        ],
    )
    def test_find_spec_raises_for_every_cache_submodule(self, name: str) -> None:
        with pytest.raises(ImportError, match="poisoned"):
            poison.PoisonedFinder().find_spec(name, None)

    @pytest.mark.parametrize(
        "name",
        [
            "agent_orchestrator.cache",
            "agent_orchestrator.cache.constants",
            "agent_orchestrator.cachefoo",  # a sibling that merely shares the prefix
            "agent_orchestrator.engine",
            "json",
        ],
    )
    def test_find_spec_defers_for_allowed_and_unrelated_modules(self, name: str) -> None:
        assert poison.PoisonedFinder().find_spec(name, None) is None

    def test_finder_uses_the_hook_python_actually_calls(self) -> None:
        assert callable(getattr(poison.PoisonedFinder, "find_spec", None))
        assert not hasattr(poison.PoisonedFinder, "find_module")

    def test_import_statement_fails_in_a_fresh_interpreter(self) -> None:
        """End to end through the real import machinery, with the allowed modules still fine."""
        code = textwrap.dedent(
            f"""
            import importlib
            from tests.cache import _noop_poison
            _noop_poison.install()

            failures = []
            for how in ("import_module", "statement"):
                try:
                    if how == "import_module":
                        importlib.import_module("{POISONED_MODULE}")
                    else:
                        import agent_orchestrator.cache.keys
                except ImportError as exc:
                    assert "poisoned" in str(exc), exc
                else:
                    failures.append(how)
            assert not failures, f"poison did not fire for: {{failures}}"

            import agent_orchestrator.cache.constants  # allowed
            import agent_orchestrator.cache  # allowed
            assert "{POISONED_MODULE}" not in __import__("sys").modules
            print("POISON_OK")
            """
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=REPO_ROOT,
            env=_subprocess_env(),
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
            check=False,
        )
        assert proc.returncode == 0 and "POISON_OK" in proc.stdout, proc.stdout + proc.stderr


class TestNegativeControls:
    """The I-1 harness must FAIL when a cache submodule really is imported during a run."""

    def test_poisoned_import_during_a_scenario_fails_the_run(self) -> None:
        proc = _run_module(
            "tests.cache._noop_subprocess", "--only", "serial", "--sabotage-import", POISONED_MODULE
        )
        report = _report_of(proc)
        assert proc.returncode != 0
        assert report["sabotage"] and report["sabotage"][0].startswith("ImportError: poisoned")
        assert report["failures"], report

    def test_module_check_fails_on_its_own_when_poison_is_off(self) -> None:
        """Without the finder the import succeeds; the loaded-modules subset check must catch it."""
        proc = _run_module(
            "tests.cache._noop_subprocess",
            "--only",
            "serial",
            "--no-poison",
            "--sabotage-import",
            POISONED_MODULE,
        )
        report = _report_of(proc)
        assert proc.returncode != 0
        assert report["sabotage"] == [f"imported {POISONED_MODULE}"]
        assert POISONED_MODULE in report["unexpected_cache_modules"]
        assert all(not r["problems"] for r in report["scenarios"].values())  # only the module check

    def test_allowed_module_import_is_not_a_failure(self) -> None:
        proc = _run_module(
            "tests.cache._noop_subprocess",
            "--only",
            "serial",
            "--sabotage-import",
            "agent_orchestrator.cache.constants",
        )
        report = _report_of(proc)
        assert proc.returncode == 0, report["failures"]
        assert report["sabotage"] == ["imported agent_orchestrator.cache.constants"]

    def test_status_key_scan_finds_nested_result_cache(self) -> None:
        assert contains_key({"a": [{"b": {"result_cache": {}}}]}, "result_cache")
        assert not contains_key({"a": [{"b": {"cache_hits": 1}}]}, "result_cache")


# ---------------------------------------------------------------------------
# I-1
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def i1_report() -> dict[str, Any]:
    """One poisoned-import subprocess running every scenario; tests below read its report."""
    proc = _run_module("tests.cache._noop_subprocess")
    report = _report_of(proc)
    report["returncode"] = proc.returncode
    return report


class TestI1PoisonedImports:
    """I-1: cache off -> every workflow completes with no cache code reached or imported."""

    def test_i1_runs_every_required_workflow(self, i1_report: dict[str, Any]) -> None:
        assert set(i1_report["scenarios"]) == REQUIRED_SCENARIOS

    @pytest.mark.parametrize("scenario", sorted(REQUIRED_SCENARIOS))
    def test_i1_scenario_completes_cleanly(self, i1_report: dict[str, Any], scenario: str) -> None:
        result = i1_report["scenarios"][scenario]
        assert result["problems"] == []
        assert result["status"] == result["expected"]

    def test_i1_poison_was_active(self, i1_report: dict[str, Any]) -> None:
        assert i1_report["poison_installed"] is True

    def test_i1_engine_cache_hooks_never_called(self, i1_report: dict[str, Any]) -> None:
        assert i1_report["engine_hook_calls"] == []

    def test_i1_loaded_cache_modules_subset_of_allowed(self, i1_report: dict[str, Any]) -> None:
        loaded = set(i1_report["loaded_cache_modules"])
        assert loaded <= poison.ALLOWED_CACHE_MODULES, loaded - poison.ALLOWED_CACHE_MODULES
        assert i1_report["unexpected_cache_modules"] == []

    def test_i1_overall_pass(self, i1_report: dict[str, Any]) -> None:
        assert i1_report["failures"] == []
        assert i1_report["returncode"] == 0


# ---------------------------------------------------------------------------
# I-2
# ---------------------------------------------------------------------------


def capture_current_tree(variant: str, out_dir: Path) -> None:
    """Run the shared capture script against THIS tree (the goldens came from base)."""
    proc = _run_module("tests.cache._noop_capture", "--variant", variant, "--out-dir", str(out_dir))
    assert proc.returncode == 0, proc.stdout + proc.stderr


def diff_against_golden(actual_dir: Path, golden_dir: Path) -> list[str]:
    """Names of the artefacts whose bytes differ (empty list = byte-identical)."""
    return [
        name
        for name in (STATUS_FILE, STDOUT_FILE)
        if (actual_dir / name).read_bytes() != (golden_dir / name).read_bytes()
    ]


class TestI2GoldenSnapshot:
    """I-2: cache-off `ao run` output equals the base-commit goldens, byte for byte."""

    @pytest.mark.parametrize("variant", sorted(VARIANTS))
    def test_i2_matches_base_golden(self, variant: str, tmp_path: Path) -> None:
        capture_current_tree(variant, tmp_path / "actual")
        assert diff_against_golden(tmp_path / "actual", GOLDEN_DIR / variant) == []

    @pytest.mark.parametrize("variant", sorted(VARIANTS))
    def test_golden_is_real_captured_output(self, variant: str) -> None:
        status_text = (GOLDEN_DIR / variant / STATUS_FILE).read_text()
        stdout_text = (GOLDEN_DIR / variant / STDOUT_FILE).read_text()
        snapshot = json.loads(status_text)
        assert snapshot["status"] == "succeeded"
        assert [t["id"] for t in snapshot["tasks"]] == ["a", "b", "c"]
        assert all(t["status"] == "succeeded" for t in snapshot["tasks"])
        # Normalised, and no temp-dir path leaked into either artefact.
        assert WORKSPACE_PLACEHOLDER in status_text
        for text in (status_text, stdout_text):
            assert "/tmp/" not in text and "noop-golden-" not in text
        assert "Run:    golden-fixture-20260101T000000Z" in stdout_text
        # The cache must be invisible: no result_cache key, no cache line in the CLI text.
        assert not contains_key(snapshot, "result_cache")
        assert "result_cache" not in stdout_text and "result cache" not in stdout_text.lower()

    @pytest.mark.parametrize("name", [STATUS_FILE, STDOUT_FILE])
    def test_comparison_detects_a_perturbed_golden(self, name: str, tmp_path: Path) -> None:
        """Mutation check: the comparison really fails if a golden byte changes."""
        mutated = tmp_path / "mutated"
        shutil.copytree(GOLDEN_DIR / "serial", mutated)
        original = (mutated / name).read_bytes()
        (mutated / name).write_bytes(original.replace(b"succeeded", b"succeeded ", 1))
        assert diff_against_golden(GOLDEN_DIR / "serial", mutated) == [name]
