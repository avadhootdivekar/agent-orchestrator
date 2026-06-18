"""Tests for HeuristicTokenEstimator and ArtifactStore.size() (T-n7hmwj)."""

from __future__ import annotations

import math

import pytest

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.estimator import HeuristicTokenEstimator, TokenEstimator
from agent_orchestrator.models import AgentSpec, EstimatorConfig, TaskContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(
    tmp_path,
    *,
    instruction_bytes: int = 0,
    input_sizes: list[int] | None = None,
    dynamic_input_sizes: list[int] | None = None,
) -> tuple[LocalFsArtifactStore, TaskContext]:
    """Create a TaskContext with files of the given byte sizes on disk."""
    store = LocalFsArtifactStore(str(tmp_path))

    # Write instruction file
    instr_file = tmp_path / "instruction.md"
    instr_file.write_bytes(b"x" * instruction_bytes)

    # Write input files
    input_paths: list[str] = []
    for i, sz in enumerate(input_sizes or []):
        p = tmp_path / f"input_{i}.txt"
        p.write_bytes(b"x" * sz)
        input_paths.append(f"input_{i}.txt")

    # Write dynamic_input files
    dynamic_input_paths: list[str] = []
    for i, sz in enumerate(dynamic_input_sizes or []):
        p = tmp_path / f"dynamic_{i}.txt"
        p.write_bytes(b"x" * sz)
        dynamic_input_paths.append(f"dynamic_{i}.txt")

    ctx = TaskContext(
        run_id="run-1",
        task_id="t1",
        agent=AgentSpec(executor="fake"),
        instruction_path="instruction.md",
        input_paths=input_paths,
        output_paths=["output/out.txt"],
        dynamic_input_paths=dynamic_input_paths,
        repo_paths={},
        timeout_seconds=300,
    )
    return store, ctx


# ---------------------------------------------------------------------------
# ArtifactStore.size() tests
# ---------------------------------------------------------------------------


class TestArtifactStoreSize:
    def test_size_returns_correct_bytes(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        f = tmp_path / "data.txt"
        f.write_bytes(b"hello")
        assert store.size("data.txt") == 5

    def test_size_missing_file_returns_zero(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        assert store.size("no_such_file.txt") == 0

    def test_size_path_traversal_returns_zero(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        # Path traversal should not raise — returns 0
        assert store.size("../escape.txt") == 0

    def test_size_empty_file_returns_zero(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "empty.txt").write_bytes(b"")
        assert store.size("empty.txt") == 0

    def test_size_large_file(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        data = b"a" * 10_000
        (tmp_path / "large.bin").write_bytes(data)
        assert store.size("large.bin") == 10_000

    def test_size_does_not_read_contents(self, tmp_path, monkeypatch) -> None:
        """Verify size() only calls os.stat, not open/read_text/read_bytes."""
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "secret.txt").write_bytes(b"secret payload")

        opened: list[str] = []
        original_open = open  # noqa: WPS421

        def mock_open(file, *args, **kwargs):
            opened.append(str(file))
            return original_open(file, *args, **kwargs)

        monkeypatch.setattr("builtins.open", mock_open)
        store.size("secret.txt")
        assert opened == [], "size() must not open any file"


# ---------------------------------------------------------------------------
# TokenEstimator ABC tests
# ---------------------------------------------------------------------------


class TestTokenEstimatorABC:
    def test_is_abstract(self) -> None:
        """TokenEstimator cannot be instantiated directly."""
        with pytest.raises(TypeError):
            TokenEstimator()  # type: ignore[abstract]

    def test_stub_subclass_works(self, tmp_path) -> None:
        """A concrete stub subclass satisfies the ABC."""

        class StubEstimator(TokenEstimator):
            def estimate(self, ctx: TaskContext, cfg: EstimatorConfig) -> int:
                return 42

        store, ctx = _make_ctx(tmp_path)
        cfg = EstimatorConfig()
        assert StubEstimator().estimate(ctx, cfg) == 42


# ---------------------------------------------------------------------------
# HeuristicTokenEstimator tests
# ---------------------------------------------------------------------------


class TestHeuristicTokenEstimator:
    def test_acceptance_criteria_example(self, tmp_path) -> None:
        """Acceptance criterion: 400-byte instruction + 800-byte input → 520 tokens."""
        # instruction=400, one input=800, chars_per_token=4, output_allowance=100, pessimism=1.3
        # raw = (1200/4 + 100) * 1.3 = 400 tokens + 100 = 500 * 1.3 = 650? Wait…
        # Let me recompute per spec:
        # input_bytes = 400 + 800 = 1200
        # raw = (1200 / 4) + 100 = 300 + 100 = 400
        # estimate = ceil(400 * 1.3) = ceil(520.0) = 520
        store, ctx = _make_ctx(
            tmp_path,
            instruction_bytes=400,
            input_sizes=[800],
        )
        cfg = EstimatorConfig(
            chars_per_token=4,
            output_allowance_tokens=100,
            pessimism_buffer=1.3,
        )
        est = HeuristicTokenEstimator(store)
        result = est.estimate(ctx, cfg)
        assert result == 520

    def test_missing_file_does_not_crash_and_uses_zero(self, tmp_path) -> None:
        """A TaskContext referencing a nonexistent input should not raise; size → 0."""
        store = LocalFsArtifactStore(str(tmp_path))
        # Write only the instruction file; input file is absent
        (tmp_path / "instruction.md").write_bytes(b"x" * 400)
        ctx = TaskContext(
            run_id="run-1",
            task_id="t1",
            agent=AgentSpec(executor="fake"),
            instruction_path="instruction.md",
            input_paths=["nonexistent_input.txt"],
            output_paths=[],
            repo_paths={},
            timeout_seconds=300,
        )
        cfg = EstimatorConfig(chars_per_token=4, output_allowance_tokens=0, pessimism_buffer=1.0)
        est = HeuristicTokenEstimator(store)
        # nonexistent → size 0; estimate = ceil((400/4 + 0) * 1.0) = 100
        assert est.estimate(ctx, cfg) == 100

    def test_dynamic_input_paths_included(self, tmp_path) -> None:
        """dynamic_input_paths must be included in the byte count."""
        store, ctx = _make_ctx(
            tmp_path,
            instruction_bytes=0,
            input_sizes=[],
            dynamic_input_sizes=[400],
        )
        cfg = EstimatorConfig(chars_per_token=4, output_allowance_tokens=0, pessimism_buffer=1.0)
        est = HeuristicTokenEstimator(store)
        # 400 dynamic bytes / 4 + 0 allowance = 100, *1.0 = 100
        assert est.estimate(ctx, cfg) == 100

    def test_no_inputs_returns_output_allowance_only(self, tmp_path) -> None:
        """With no inputs, estimate = ceil(output_allowance * pessimism_buffer)."""
        store, ctx = _make_ctx(tmp_path, instruction_bytes=0)
        cfg = EstimatorConfig(chars_per_token=4, output_allowance_tokens=500, pessimism_buffer=1.0)
        est = HeuristicTokenEstimator(store)
        assert est.estimate(ctx, cfg) == 500

    def test_pessimism_buffer_applied(self, tmp_path) -> None:
        """Pessimism buffer is applied last, result is ceiling."""
        store, ctx = _make_ctx(tmp_path, instruction_bytes=100)
        cfg = EstimatorConfig(chars_per_token=4, output_allowance_tokens=0, pessimism_buffer=1.5)
        est = HeuristicTokenEstimator(store)
        # raw = (100/4 + 0) * 1.5 = 25 * 1.5 = 37.5 → ceil = 38
        assert est.estimate(ctx, cfg) == math.ceil(37.5)

    def test_combined_input_and_dynamic_input(self, tmp_path) -> None:
        """Both input_paths and dynamic_input_paths are summed."""
        store, ctx = _make_ctx(
            tmp_path,
            instruction_bytes=0,
            input_sizes=[200],
            dynamic_input_sizes=[200],
        )
        cfg = EstimatorConfig(chars_per_token=4, output_allowance_tokens=0, pessimism_buffer=1.0)
        est = HeuristicTokenEstimator(store)
        # (200+200)/4 = 100
        assert est.estimate(ctx, cfg) == 100

    def test_no_magic_literals_in_default_config(self, tmp_path) -> None:
        """Default EstimatorConfig uses model-level named constants, not inlined values."""
        from agent_orchestrator.models import (
            DEFAULT_CHARS_PER_TOKEN,
            DEFAULT_OUTPUT_ALLOWANCE_TOKENS,
            DEFAULT_PESSIMISM_BUFFER,
        )

        cfg = EstimatorConfig()
        assert cfg.chars_per_token == DEFAULT_CHARS_PER_TOKEN
        assert cfg.pessimism_buffer == DEFAULT_PESSIMISM_BUFFER
        assert cfg.output_allowance_tokens == DEFAULT_OUTPUT_ALLOWANCE_TOKENS
