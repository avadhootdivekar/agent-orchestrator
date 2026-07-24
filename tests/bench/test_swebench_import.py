"""Tests for `agent_orchestrator.bench.swebench_import` (E-Bt4Xk9 T-Sw5Hd9).

Network-free by default: every test here injects a `rows_loader` that reads from the
committed `tests/bench/data/swebench_sample.jsonl` fixture (3 real SWE-bench Verified
rows, trimmed) instead of calling HuggingFace -- `import_swebench`'s `rows_loader`
seam (dependency injection, CLAUDE.md's pluggable-boundaries rule) is exactly what
makes this possible. The one real-network path (`_load_rows_from_hf`) is exercised by
the `swebench`-marked opt-in test at the bottom of this file (skipped unless
AO_E2E_SWEBENCH=1 -- mirrors tests/playground/conftest.py's `real_llm` gate).
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from agent_orchestrator.bench.errors import BenchError, SpecValidationError
from agent_orchestrator.bench.spec import load_suite
from agent_orchestrator.bench.swebench_import import (
    DEFAULT_DATASET,
    PINNED_REVISION,
    ImportResult,
    _as_str_list,
    _parse_instance_ids,
    _probe_image_size_bytes,
    _swebench_image_ref,
    import_swebench,
    import_swebench_command,
)

_SAMPLE_JSONL = Path(__file__).parent / "data" / "swebench_sample.jsonl"
_SAMPLE_IDS = ["django__django-12304", "psf__requests-2931", "sympy__sympy-20590"]
_TEST_REVISION = "0000000000000000000000000000000000000000"  # not the real pin -- deliberate


def _sample_rows() -> dict[str, dict[str, Any]]:
    return {
        r["instance_id"]: r
        for r in (json.loads(line) for line in _SAMPLE_JSONL.read_text().splitlines())
    }


def _fixture_rows_loader(
    dataset: str, revision: str, instance_ids: Sequence[str]
) -> dict[str, dict[str, Any]]:
    rows = _sample_rows()
    return {iid: rows[iid] for iid in instance_ids if iid in rows}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_swebench_image_ref_replaces_double_underscore() -> None:
    assert _swebench_image_ref("django__django-12304") == (
        "docker.io/swebench/sweb.eval.x86_64.django_1776_django-12304:latest"
    )


def test_as_str_list_accepts_json_encoded_string() -> None:
    # The real HF dataset shape: FAIL_TO_PASS/PASS_TO_PASS are JSON-ENCODED STRINGS.
    assert _as_str_list('["a::b", "c::d"]') == ["a::b", "c::d"]


def test_as_str_list_accepts_native_list() -> None:
    assert _as_str_list(["a::b"]) == ["a::b"]


def test_as_str_list_rejects_non_list_json() -> None:
    with pytest.raises(SpecValidationError):
        _as_str_list('{"not": "a list"}')


def test_parse_instance_ids_comma_separated() -> None:
    assert _parse_instance_ids("a,b, c ,") == ["a", "b", "c"]


def test_parse_instance_ids_from_file(tmp_path: Path) -> None:
    p = tmp_path / "ids.txt"
    p.write_text("a\n# a comment\n\nb\n")
    assert _parse_instance_ids(str(p)) == ["a", "b"]


def test_probe_image_size_bytes_sums_layers(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = {"layers": [{"size": 100}, {"size": 250}]}
    captured = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=json.dumps(manifest), stderr=""
    )

    import agent_orchestrator.bench.swebench_import as mod

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: captured)
    size = _probe_image_size_bytes("some/image:latest", warn=lambda _m: None)
    assert size == 350


def test_probe_image_size_bytes_returns_none_and_warns_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="not found")
    warnings: list[str] = []

    import agent_orchestrator.bench.swebench_import as mod

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: captured)
    size = _probe_image_size_bytes("some/image:latest", warn=warnings.append)
    assert size is None
    assert len(warnings) == 1


def test_probe_image_size_bytes_returns_none_on_missing_docker_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings: list[str] = []

    import agent_orchestrator.bench.swebench_import as mod

    def raise_oserror(*args: Any, **kwargs: Any) -> Any:
        raise OSError("docker not found")

    monkeypatch.setattr(mod.subprocess, "run", raise_oserror)
    size = _probe_image_size_bytes("some/image:latest", warn=warnings.append)
    assert size is None
    assert warnings


# ---------------------------------------------------------------------------
# import_swebench (core logic, fixture-loader injected -- network-free)
# ---------------------------------------------------------------------------


def test_import_swebench_writes_expected_suite_shape(tmp_path: Path) -> None:
    out_dir = tmp_path / "swe-verified-mini"
    result = import_swebench(
        dataset=DEFAULT_DATASET,
        revision=_TEST_REVISION,
        instance_ids=_SAMPLE_IDS,
        out_dir=out_dir,
        probe_image_sizes=False,
        rows_loader=_fixture_rows_loader,
    )
    assert isinstance(result, ImportResult)
    assert result.task_count == 3

    suite = json.loads(result.suite_path.read_text())
    assert suite["tier"] == "large"
    assert suite["id"] == "swe-verified-mini"
    assert [t["id"] for t in suite["tasks"]] == sorted(_SAMPLE_IDS)  # sorted, deterministic
    for t in suite["tasks"]:
        assert t["source"] == {"type": "swebench", "instance_id": t["id"]}
        assert t["grader"] == {"type": "swebench"}
        assert "fixture" not in t  # swebench tasks use `source`, never a fixture path
        instr_path = out_dir / t["instruction"]
        assert instr_path.is_file()
        text = instr_path.read_text()
        assert text.startswith("# Task: resolve this issue")
        assert ".grading" in text  # preamble safety instruction present

    instances = json.loads(result.instances_path.read_text())
    assert instances["dataset"] == DEFAULT_DATASET
    assert instances["revision"] == _TEST_REVISION
    assert "_notes" in instances and len(instances["_notes"]) > 0
    ids_in_instances = {i["instance_id"] for i in instances["instances"]}
    assert ids_in_instances == set(_SAMPLE_IDS)
    for inst in instances["instances"]:
        assert inst["image"]["size_bytes"] is None  # probe_image_sizes=False
        assert isinstance(inst["fail_to_pass"], list)
        assert isinstance(inst["pass_to_pass"], list)


def test_import_swebench_loads_with_bench_spec(tmp_path: Path) -> None:
    """The generated suite.json must itself pass `bench/spec.py`'s `load_suite` --
    this is the actual `ao-bench validate` contract (AC1), exercised end-to-end.
    """
    out_dir = tmp_path / "swe-verified-mini"
    import_swebench(
        dataset=DEFAULT_DATASET,
        revision=_TEST_REVISION,
        instance_ids=_SAMPLE_IDS,
        out_dir=out_dir,
        probe_image_sizes=False,
        rows_loader=_fixture_rows_loader,
    )
    suite = load_suite(out_dir / "suite.json")
    assert suite.tier == "large"
    assert len(suite.tasks) == 3
    assert {t.source.instance_id for t in suite.tasks if t.source} == set(_SAMPLE_IDS)


def test_import_swebench_is_deterministic_regardless_of_input_order(tmp_path: Path) -> None:
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    import_swebench(
        dataset=DEFAULT_DATASET,
        revision=_TEST_REVISION,
        instance_ids=list(reversed(_SAMPLE_IDS)),
        out_dir=out_a,
        probe_image_sizes=False,
        rows_loader=_fixture_rows_loader,
    )
    import_swebench(
        dataset=DEFAULT_DATASET,
        revision=_TEST_REVISION,
        instance_ids=_SAMPLE_IDS,
        out_dir=out_b,
        probe_image_sizes=False,
        rows_loader=_fixture_rows_loader,
    )
    files_a = sorted(p.relative_to(out_a) for p in out_a.rglob("*") if p.is_file())
    files_b = sorted(p.relative_to(out_b) for p in out_b.rglob("*") if p.is_file())
    assert files_a == files_b
    for rel in files_a:
        assert (out_a / rel).read_text() == (out_b / rel).read_text()


def test_import_swebench_deduplicates_ids_and_warns(tmp_path: Path) -> None:
    warnings: list[str] = []
    result = import_swebench(
        dataset=DEFAULT_DATASET,
        revision=_TEST_REVISION,
        instance_ids=[_SAMPLE_IDS[0], _SAMPLE_IDS[0], _SAMPLE_IDS[1]],
        out_dir=tmp_path,
        probe_image_sizes=False,
        rows_loader=_fixture_rows_loader,
        warn=warnings.append,
    )
    assert result.task_count == 2
    assert any("duplicate" in w for w in warnings)


def test_import_swebench_empty_instance_ids_raises() -> None:
    with pytest.raises(SpecValidationError, match="no instance ids"):
        import_swebench(
            dataset=DEFAULT_DATASET,
            revision=_TEST_REVISION,
            instance_ids=[],
            out_dir=Path("/unused"),
            rows_loader=_fixture_rows_loader,
        )


def test_import_swebench_unknown_instance_id_raises(tmp_path: Path) -> None:
    with pytest.raises(SpecValidationError, match="not found"):
        import_swebench(
            dataset=DEFAULT_DATASET,
            revision=_TEST_REVISION,
            instance_ids=["does-not-exist-123"],
            out_dir=tmp_path,
            probe_image_sizes=False,
            rows_loader=_fixture_rows_loader,
        )


def test_import_swebench_missing_required_field_raises(tmp_path: Path) -> None:
    def broken_loader(
        dataset: str, revision: str, instance_ids: Sequence[str]
    ) -> dict[str, dict[str, Any]]:
        rows = _sample_rows()
        broken = dict(rows[_SAMPLE_IDS[0]])
        broken["base_commit"] = ""  # required field, blanked out
        return {_SAMPLE_IDS[0]: broken}

    with pytest.raises(SpecValidationError, match="base_commit"):
        import_swebench(
            dataset=DEFAULT_DATASET,
            revision=_TEST_REVISION,
            instance_ids=[_SAMPLE_IDS[0]],
            out_dir=tmp_path,
            probe_image_sizes=False,
            rows_loader=broken_loader,
        )


def test_load_rows_from_hf_missing_datasets_package_raises_bencherror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulates the optional `swebench` extra NOT being installed (AC4) by making
    `import datasets` fail, WITHOUT actually uninstalling it from this test env --
    `sys.modules[name] = None` is the standard stdlib-documented way to force the next
    `import <name>` to raise ImportError.
    """
    monkeypatch.setitem(sys.modules, "datasets", None)
    from agent_orchestrator.bench.swebench_import import _load_rows_from_hf

    with pytest.raises(BenchError, match="swebench"):
        _load_rows_from_hf(DEFAULT_DATASET, _TEST_REVISION, _SAMPLE_IDS)


# ---------------------------------------------------------------------------
# CLI command (typer CliRunner) -- registered onto a throwaway local app so this
# stays independent of cli.py's own (separately owned/hooked) `ao-bench` app.
# ---------------------------------------------------------------------------


@pytest.fixture()
def cli_app() -> typer.Typer:
    app = typer.Typer()
    app.command(name="import-swebench")(import_swebench_command)
    return app


def test_cli_import_swebench_happy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_app: typer.Typer
) -> None:
    import agent_orchestrator.bench.swebench_import as mod

    monkeypatch.setattr(mod, "_load_rows_from_hf", _fixture_rows_loader)
    out_dir = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        [
            "--dataset",
            DEFAULT_DATASET,
            "--revision",
            _TEST_REVISION,
            "--instances",
            ",".join(_SAMPLE_IDS),
            "--out",
            str(out_dir),
            "--no-probe-images",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (out_dir / "suite.json").is_file()
    assert (out_dir / "instances.json").is_file()


def test_cli_import_swebench_unknown_instance_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_app: typer.Typer
) -> None:
    import agent_orchestrator.bench.swebench_import as mod

    monkeypatch.setattr(mod, "_load_rows_from_hf", _fixture_rows_loader)
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        [
            "--dataset",
            DEFAULT_DATASET,
            "--revision",
            _TEST_REVISION,
            "--instances",
            "nope-does-not-exist",
            "--out",
            str(tmp_path / "out"),
            "--no-probe-images",
        ],
    )
    assert result.exit_code == 1
    assert "ERROR" in result.output


# ---------------------------------------------------------------------------
# Committed suite (swe-verified-mini) -- validates the ACTUAL shipped artifact.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMMITTED_SUITE_DIR = _REPO_ROOT / "benchmarks" / "suites" / "swe-verified-mini"


@pytest.mark.skipif(
    not _COMMITTED_SUITE_DIR.is_dir(), reason="swe-verified-mini not generated in this checkout"
)
def test_committed_swe_verified_mini_suite_is_valid() -> None:
    suite = load_suite(_COMMITTED_SUITE_DIR / "suite.json")
    assert suite.tier == "large"
    assert len(suite.tasks) == 10

    instances = json.loads((_COMMITTED_SUITE_DIR / "instances.json").read_text())
    assert instances["revision"] == PINNED_REVISION
    assert instances["dataset"] == DEFAULT_DATASET
    assert len(instances["instances"]) == 10

    repos = {i["repo"] for i in instances["instances"]}
    assert len(repos) >= 4  # spans >= 4 distinct repos (selection requirement)
    sympy_count = sum(1 for i in instances["instances"] if i["repo"] == "sympy/sympy")
    assert sympy_count <= 2

    difficulty_counts: dict[str, int] = {}
    for inst in instances["instances"]:
        difficulty_counts[inst["difficulty"]] = difficulty_counts.get(inst["difficulty"], 0) + 1
    assert difficulty_counts.get("15 min - 1 hour") == 6
    assert difficulty_counts.get("1-4 hours") == 3
    assert difficulty_counts.get("<15 min fix") == 1


# ---------------------------------------------------------------------------
# Opt-in real network test (swebench marker -- see tests/bench/conftest.py's gate)
# ---------------------------------------------------------------------------


@pytest.mark.swebench
def test_real_load_rows_from_hf_matches_fixture_jsonl() -> None:
    """Opt-in (AO_E2E_SWEBENCH=1): pulls the REAL pinned HF dataset revision and
    checks the 3 sample rows match this repo's own `tests/bench/data` fixture
    byte-for-byte on every field the importer reads -- proves the HF-driven path and
    the jsonl-driven path are equivalent for a real pinned revision (not just an
    injected fake), matching the epic's "both must produce identical output" bar.
    """
    from agent_orchestrator.bench.swebench_import import _load_rows_from_hf

    real_rows = _load_rows_from_hf(DEFAULT_DATASET, PINNED_REVISION, _SAMPLE_IDS)
    fixture_rows = _sample_rows()
    for iid in _SAMPLE_IDS:
        for field in (
            "repo",
            "base_commit",
            "environment_setup_commit",
            "difficulty",
            "problem_statement",
        ):
            assert real_rows[iid][field] == fixture_rows[iid][field], (iid, field)
