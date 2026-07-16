"""Tests for the Typer CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from agent_orchestrator.cli import app

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_agents_fake(path: Path) -> None:
    """Write an agents config where all executors are 'fake'."""
    path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "agents": {
                    "architect": {"executor": "fake"},
                    "developer": {"executor": "fake"},
                    "tester": {"executor": "fake"},
                },
            }
        )
    )


def _write_reposets(path: Path, workspace: str) -> None:
    path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "repo_sets": {
                    "default-set": {
                        "workspace_root": workspace,
                        "repos": [{"id": "core", "path": ".", "role": "primary"}],
                    }
                },
            }
        )
    )


def _write_workflow(path: Path, tasks=None) -> None:
    if tasks is None:
        tasks = [
            {
                "id": "design",
                "agent": "architect",
                "instruction": "specs/examples/instructions/design.md",
                "outputs": ["output/design.md"],
            },
        ]
    path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "id": "test-wf",
                "repo_set": "default-set",
                "tasks": tasks,
            }
        )
    )


# ---------------------------------------------------------------------------
# Test: validate command
# ---------------------------------------------------------------------------


class TestValidateCommand:
    def test_valid_specs_exit_0(self, tmp_path) -> None:
        wf = tmp_path / "workflow.json"
        rs = tmp_path / "reposets.json"
        ag = tmp_path / "agents.json"

        _write_workflow(wf)
        _write_reposets(rs, str(tmp_path))
        _write_agents_fake(ag)

        result = runner.invoke(
            app,
            ["validate", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
        )
        assert result.exit_code == 0
        assert "OK" in result.output

    def test_malformed_workflow_exits_1(self, tmp_path) -> None:
        wf = tmp_path / "bad.json"
        wf.write_text('{"version": "1.0"}')  # missing required 'id', 'repo_set', 'tasks'
        rs = tmp_path / "reposets.json"
        ag = tmp_path / "agents.json"
        _write_reposets(rs, str(tmp_path))
        _write_agents_fake(ag)

        result = runner.invoke(
            app,
            ["validate", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
        )
        assert result.exit_code == 1

    def test_missing_reposets_exits_1(self, tmp_path) -> None:
        wf = tmp_path / "workflow.json"
        _write_workflow(wf)

        result = runner.invoke(
            app,
            ["validate", "--workflow", str(wf)],
        )
        assert result.exit_code == 1

    def test_unknown_agent_exits_1(self, tmp_path) -> None:
        wf = tmp_path / "workflow.json"
        _write_workflow(
            wf,
            tasks=[
                {
                    "id": "t1",
                    "agent": "nonexistent-agent",
                    "instruction": "instr.md",
                }
            ],
        )
        rs = tmp_path / "reposets.json"
        ag = tmp_path / "agents.json"
        _write_reposets(rs, str(tmp_path))
        _write_agents_fake(ag)

        result = runner.invoke(
            app,
            ["validate", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
        )
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Test: run command
# ---------------------------------------------------------------------------


class TestRunCommand:
    def test_successful_run_exits_0(self, tmp_path) -> None:
        # Create the instruction file so ArtifactStore.resolve doesn't error
        instr_dir = tmp_path / "specs" / "examples" / "instructions"
        instr_dir.mkdir(parents=True)
        (instr_dir / "design.md").write_text("design instruction")

        wf = tmp_path / "workflow.json"
        rs = tmp_path / "reposets.json"
        ag = tmp_path / "agents.json"

        _write_workflow(
            wf,
            tasks=[
                {
                    "id": "design",
                    "agent": "architect",
                    "instruction": "specs/examples/instructions/design.md",
                    "outputs": ["output/design.md"],
                }
            ],
        )
        _write_reposets(rs, str(tmp_path))
        _write_agents_fake(ag)

        result = runner.invoke(
            app,
            ["run", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )
        assert result.exit_code == 0
        assert "succeeded" in result.output

    def test_failed_run_exits_1(self, tmp_path) -> None:
        """A workflow where FakeExecutor is configured to fail -> exit 1."""
        instr_dir = tmp_path / "specs" / "examples" / "instructions"
        instr_dir.mkdir(parents=True)
        (instr_dir / "design.md").write_text("design instruction")

        # agents.json with executor=fake but behavior hardcoded to fail won't work via CLI directly;
        # instead test missing input detection which also causes a failed run
        wf = tmp_path / "workflow.json"
        rs = tmp_path / "reposets.json"
        ag = tmp_path / "agents.json"

        _write_workflow(
            wf,
            tasks=[
                {
                    "id": "design",
                    "agent": "architect",
                    "instruction": "specs/examples/instructions/design.md",
                    "inputs": ["no/such/file.txt"],
                    "outputs": ["output/design.md"],
                }
            ],
        )
        _write_reposets(rs, str(tmp_path))
        _write_agents_fake(ag)

        result = runner.invoke(
            app,
            ["run", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Test: status command
# ---------------------------------------------------------------------------


class TestStatusCommand:
    def test_status_for_existing_run(self, tmp_path) -> None:
        from agent_orchestrator.artifacts import LocalFsArtifactStore
        from agent_orchestrator.engine import Orchestrator
        from agent_orchestrator.executors.fake import FakeExecutor
        from agent_orchestrator.runstate import RunStateStore

        instr_dir = tmp_path / "specs" / "examples" / "instructions"
        instr_dir.mkdir(parents=True)
        (instr_dir / "design.md").write_text("design instruction")

        store = LocalFsArtifactStore(str(tmp_path))
        rs_store = RunStateStore(str(tmp_path), store)
        executor = FakeExecutor()

        from agent_orchestrator.models import AgentSpec, RepoRef, RepoSet, TaskSpec, WorkflowSpec

        wf = WorkflowSpec(
            version="1.0",
            id="test-wf",
            repo_set="rs",
            tasks=[
                TaskSpec(
                    id="design",
                    agent="ag",
                    instruction="specs/examples/instructions/design.md",
                    outputs=["output/design.md"],
                )
            ],
        )
        reposets = {
            "rs": RepoSet(
                workspace_root=str(tmp_path),
                repos=[RepoRef(id="core", path=".", role="primary")],
            )
        }
        agents = {"ag": AgentSpec(executor="fake")}
        orch = Orchestrator(executor, store, rs_store)
        state = orch.run(wf, reposets, agents)
        run_id = state.run_id

        # Now test CLI status command
        wf_path = tmp_path / "workflow.json"
        rs_path = tmp_path / "reposets.json"
        ag_path = tmp_path / "agents.json"
        _write_reposets(rs_path, str(tmp_path))
        _write_agents_fake(ag_path)
        wf_path.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "id": "test-wf",
                    "repo_set": "default-set",
                    "tasks": [
                        {
                            "id": "design",
                            "agent": "architect",
                            "instruction": "specs/examples/instructions/design.md",
                            "outputs": ["output/design.md"],
                        }
                    ],
                }
            )
        )

        result = runner.invoke(
            app,
            [
                "status",
                "--run-id",
                run_id,
                "--workflow",
                str(wf_path),
                "--reposets",
                str(rs_path),
                "--agents",
                str(ag_path),
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )
        assert result.exit_code == 0
        assert run_id in result.output
        assert "design" in result.output

    def test_status_missing_run_exits_1(self, tmp_path) -> None:
        wf = tmp_path / "workflow.json"
        rs = tmp_path / "reposets.json"
        ag = tmp_path / "agents.json"
        _write_workflow(wf)
        _write_reposets(rs, str(tmp_path))
        _write_agents_fake(ag)

        result = runner.invoke(
            app,
            [
                "status",
                "--run-id",
                "nonexistent-run-id",
                "--workflow",
                str(wf),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Test: _build_effective_budget helper (AC 1-5 from T-xefapr-cli-budget-flags)
# ---------------------------------------------------------------------------


class TestBuildEffectiveBudget:
    """Unit tests for _build_effective_budget: merge logic, no-op path, validation."""

    def _call(
        self,
        wf_budget=None,
        budget_total=None,
        rate_tokens=None,
        rate_window=None,
        on_exhaustion=None,
        pessimism_buffer=None,
    ):
        from agent_orchestrator.cli import _build_effective_budget

        return _build_effective_budget(
            wf_budget, budget_total, rate_tokens, rate_window, on_exhaustion, pessimism_buffer
        )

    def test_no_budget_anywhere_returns_none(self) -> None:
        """AC-2: no spec budget + no CLI flags -> no-op path returns None."""
        result = self._call()
        assert result is None

    def test_cli_total_wins_over_spec(self) -> None:
        """AC-1: spec total_tokens=5000, CLI --budget-total=1000 -> effective total=1000."""
        from agent_orchestrator.models import BudgetSpec

        spec_budget = BudgetSpec(total_tokens=5000)
        result = self._call(wf_budget=spec_budget, budget_total=1000)
        assert result is not None
        assert result.total_tokens == 1000

    def test_cli_budget_total_preserves_spec_rate(self) -> None:
        """CLI --budget-total must NOT wipe a spec rate block (per-field precedence)."""
        from agent_orchestrator.models import BudgetSpec, RateLimit

        spec_budget = BudgetSpec(total_tokens=5000, rate=RateLimit(tokens=200, window="minute"))
        result = self._call(wf_budget=spec_budget, budget_total=1000)
        assert result is not None
        assert result.total_tokens == 1000
        assert result.rate is not None
        assert result.rate.tokens == 200
        assert result.rate.window == "minute"

    def test_cli_rate_overrides_spec_rate(self) -> None:
        """AC-4: --rate-tokens 500 --rate-window minute -> rate.tokens==500, window=='minute'."""
        result = self._call(budget_total=10000, rate_tokens=500, rate_window="minute")
        assert result is not None
        assert result.rate is not None
        assert result.rate.tokens == 500
        assert result.rate.window == "minute"

    def test_cli_rate_tokens_only_uses_spec_window(self) -> None:
        """Partial CLI rate: --rate-tokens supplied; window comes from spec rate."""
        from agent_orchestrator.models import BudgetSpec, RateLimit

        spec_budget = BudgetSpec(rate=RateLimit(tokens=300, window="hour"))
        result = self._call(wf_budget=spec_budget, rate_tokens=999)
        assert result is not None
        assert result.rate is not None
        assert result.rate.tokens == 999
        assert result.rate.window == "hour"

    def test_cli_rate_window_only_uses_spec_tokens(self) -> None:
        """Partial CLI rate: --rate-window supplied; tokens come from spec rate."""
        from agent_orchestrator.models import BudgetSpec, RateLimit

        spec_budget = BudgetSpec(rate=RateLimit(tokens=300, window="hour"))
        result = self._call(wf_budget=spec_budget, rate_window="minute")
        assert result is not None
        assert result.rate is not None
        assert result.rate.tokens == 300
        assert result.rate.window == "minute"

    def test_rate_tokens_without_window_no_spec_exits(self) -> None:
        """AC-5: --rate-tokens without --rate-window and no spec rate -> Exit(1)."""
        import pytest
        import typer

        with pytest.raises(typer.Exit):
            self._call(rate_tokens=500)  # no rate_window, no spec

    def test_invalid_on_exhaustion_exits(self) -> None:
        """Invalid --on-exhaustion value -> Exit(1)."""
        import pytest
        import typer

        with pytest.raises(typer.Exit):
            self._call(budget_total=1000, on_exhaustion="invalid")

    def test_on_exhaustion_cli_wins_over_spec(self) -> None:
        """CLI --on-exhaustion wait overrides spec 'stop'."""
        from agent_orchestrator.models import BudgetSpec

        spec_budget = BudgetSpec(total_tokens=5000, on_exhaustion="stop")
        result = self._call(wf_budget=spec_budget, on_exhaustion="wait")
        assert result is not None
        assert result.on_exhaustion == "wait"

    def test_pessimism_buffer_cli_wins(self) -> None:
        """CLI --pessimism-buffer overrides spec estimator buffer."""
        from agent_orchestrator.models import BudgetSpec

        spec_budget = BudgetSpec(total_tokens=1000)
        result = self._call(wf_budget=spec_budget, pessimism_buffer=2.5)
        assert result is not None
        assert result.estimator.pessimism_buffer == 2.5

    def test_spec_only_budget_passed_through(self) -> None:
        """No CLI flags: spec budget is returned unchanged."""
        from agent_orchestrator.models import BudgetSpec, RateLimit

        spec_budget = BudgetSpec(
            total_tokens=8000, rate=RateLimit(tokens=100, window="ten_minutes")
        )
        result = self._call(wf_budget=spec_budget)
        assert result is not None
        assert result.total_tokens == 8000
        assert result.rate is not None
        assert result.rate.tokens == 100
        assert result.rate.window == "ten_minutes"

    def test_invalid_rate_window_value_exits(self) -> None:
        """An invalid --rate-window string -> Exit(1)."""
        import pytest
        import typer

        with pytest.raises(typer.Exit):
            self._call(budget_total=1000, rate_tokens=500, rate_window="daily")


# ---------------------------------------------------------------------------
# Test: run command with budget flags (CLI-level smoke tests)
# ---------------------------------------------------------------------------


class TestRunCommandBudgetFlags:
    """Smoke tests verifying budget flags are wired through in ao run."""

    def _setup(self, tmp_path: Path) -> tuple:
        instr_dir = tmp_path / "specs" / "examples" / "instructions"
        instr_dir.mkdir(parents=True)
        (instr_dir / "design.md").write_text("design instruction")

        wf = tmp_path / "workflow.json"
        rs = tmp_path / "reposets.json"
        ag = tmp_path / "agents.json"
        _write_workflow(
            wf,
            tasks=[
                {
                    "id": "design",
                    "agent": "architect",
                    "instruction": "specs/examples/instructions/design.md",
                    "outputs": ["output/design.md"],
                }
            ],
        )
        _write_reposets(rs, str(tmp_path))
        _write_agents_fake(ag)
        return wf, rs, ag

    def test_run_with_budget_total_succeeds(self, tmp_path) -> None:
        """ao run --budget-total with a generous limit succeeds (fake executor uses 0 tokens)."""
        wf, rs, ag = self._setup(tmp_path)
        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(wf),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
                "--budget-total",
                "100000",
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )
        assert result.exit_code == 0
        assert "succeeded" in result.output

    def test_run_with_full_rate_flags_succeeds(self, tmp_path) -> None:
        """ao run --rate-tokens + --rate-window succeeds with a generous limit."""
        wf, rs, ag = self._setup(tmp_path)
        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(wf),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
                "--rate-tokens",
                "100000",
                "--rate-window",
                "minute",
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )
        assert result.exit_code == 0
        assert "succeeded" in result.output

    def test_run_invalid_on_exhaustion_exits_1(self, tmp_path) -> None:
        """ao run --on-exhaustion badvalue -> exit 1 with error message."""
        wf, rs, ag = self._setup(tmp_path)
        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(wf),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
                "--budget-total",
                "1000",
                "--on-exhaustion",
                "badvalue",
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )
        assert result.exit_code == 1

    def test_run_rate_tokens_without_window_exits_1(self, tmp_path) -> None:
        """ao run --rate-tokens without --rate-window and no spec rate -> exit 1."""
        wf, rs, ag = self._setup(tmp_path)
        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(wf),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
                "--rate-tokens",
                "500",
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )
        assert result.exit_code == 1


class TestVersionOption:
    """ao --version / -V is an eager top-level flag (install.sh already relies on it)."""

    def test_version_long_flag_exits_0_and_prints_ao_and_semver(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert result.output.startswith("ao ")
        from agent_orchestrator import __version__

        assert __version__ in result.output

    def test_version_short_flag_matches_long_flag(self) -> None:
        long_result = runner.invoke(app, ["--version"])
        short_result = runner.invoke(app, ["-V"])
        assert short_result.exit_code == 0
        assert short_result.output == long_result.output

    def test_version_does_not_require_a_subcommand(self) -> None:
        """--version must short-circuit before subcommand resolution (is_eager)."""
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0

    def test_subcommands_unaffected_by_top_level_callback(self, tmp_path: Path) -> None:
        """Adding the app-level --version callback must not break existing subcommands."""
        result = runner.invoke(app, ["validate", "--workflow", "/nonexistent.yaml"])
        assert result.exit_code == 1
        assert "ERROR" in result.output


class TestVerboseQuietOptions:
    """ao -v/--verbose and -q/--quiet set the 'agent_orchestrator' logger level."""

    @pytest.fixture(autouse=True)
    def _restore_logger_level(self):
        import logging

        pkg_logger = logging.getLogger("agent_orchestrator")
        original = pkg_logger.level
        yield
        pkg_logger.setLevel(original)

    def test_verbose_sets_debug_level(self, tmp_path: Path) -> None:
        import logging

        runner.invoke(app, ["-v", "validate", "--workflow", "/nonexistent.yaml"])
        assert logging.getLogger("agent_orchestrator").level == logging.DEBUG

    def test_quiet_sets_warning_level(self, tmp_path: Path) -> None:
        import logging

        runner.invoke(app, ["-q", "validate", "--workflow", "/nonexistent.yaml"])
        assert logging.getLogger("agent_orchestrator").level == logging.WARNING

    def test_verbose_and_quiet_together_exits_1(self) -> None:
        result = runner.invoke(app, ["-v", "-q", "validate"])
        assert result.exit_code == 1
        assert "mutually exclusive" in result.output

    def test_neither_flag_leaves_logger_level_untouched(self) -> None:
        import logging

        pkg_logger = logging.getLogger("agent_orchestrator")
        pkg_logger.setLevel(logging.NOTSET)
        runner.invoke(app, ["validate", "--workflow", "/nonexistent.yaml"])
        assert pkg_logger.level == logging.NOTSET


class TestShellCompletion:
    """Shell completion is enabled (add_completion=True) — was previously off."""

    def test_help_lists_completion_options(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "--install-completion" in result.output
        assert "--show-completion" in result.output


class TestResolveRunSettingsMaxParallel:
    """Unit tests for _resolve_run_settings's max_parallel resolution (T-JXiI9j AC-1/2/3):
    CLI > env > project config > built-in default (DEFAULT_MAX_PARALLEL=1) -- the identical
    `_int_env` + `or`-chain shape used by quota_max_wait_seconds. Isolates from this repo's own
    .ao/config.yaml via chdir, mirroring TestResolveMonitoringSettings's technique below."""

    def _call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, max_parallel: int | None
    ) -> int:
        from agent_orchestrator.cli import _resolve_run_settings

        monkeypatch.chdir(tmp_path)  # isolate from this repo's own .ao/config.yaml
        result = _resolve_run_settings(None, None, None, None, None, None, max_parallel)
        return result[-1]

    def test_none_everywhere_defaults_to_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AO_MAX_PARALLEL", raising=False)
        assert self._call(tmp_path, monkeypatch, None) == 1

    def test_config_only_resolves_to_config_value(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AO_MAX_PARALLEL", raising=False)
        (tmp_path / ".ao").mkdir()
        (tmp_path / ".ao" / "config.yaml").write_text("max_parallel: 4\n")
        assert self._call(tmp_path, monkeypatch, None) == 4

    def test_env_overrides_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / ".ao").mkdir()
        (tmp_path / ".ao" / "config.yaml").write_text("max_parallel: 4\n")
        monkeypatch.setenv("AO_MAX_PARALLEL", "6")
        assert self._call(tmp_path, monkeypatch, None) == 6

    def test_cli_overrides_env_and_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".ao").mkdir()
        (tmp_path / ".ao" / "config.yaml").write_text("max_parallel: 4\n")
        monkeypatch.setenv("AO_MAX_PARALLEL", "6")
        assert self._call(tmp_path, monkeypatch, 8) == 8

    def test_empty_env_treated_as_unset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-3 / learning #23 (empty-env-is-falsy): AO_MAX_PARALLEL="" must fall through to
        the default rather than raising ValueError from a bare int()."""
        monkeypatch.setenv("AO_MAX_PARALLEL", "")
        assert self._call(tmp_path, monkeypatch, None) == 1

    def test_cli_zero_falls_through_to_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """0 is falsy, so the `or`-chain treats --max-parallel 0 as unset -- identical to how
        --quota-max-wait 0 / --max-attempts 0 already resolve to their defaults in this same
        function -- and it silently becomes DEFAULT_MAX_PARALLEL rather than erroring. Only a
        value that is truthy-but-invalid (negative) survives the chain far enough to reach the
        explicit `< 1` guard below."""
        monkeypatch.delenv("AO_MAX_PARALLEL", raising=False)
        assert self._call(tmp_path, monkeypatch, 0) == 1

    def test_negative_cli_value_exits_1_with_message(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """AC-2: --max-parallel -1 -> exit 1 with a clear 'must be >= 1' error."""
        monkeypatch.delenv("AO_MAX_PARALLEL", raising=False)
        with pytest.raises(typer.Exit):
            self._call(tmp_path, monkeypatch, -1)
        assert "must be >= 1" in capsys.readouterr().err

    def test_negative_env_value_exits_1_with_message(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """AC-2: AO_MAX_PARALLEL=-1 -> exit 1 with a clear 'must be >= 1' error."""
        monkeypatch.setenv("AO_MAX_PARALLEL", "-1")
        with pytest.raises(typer.Exit):
            self._call(tmp_path, monkeypatch, None)
        assert "must be >= 1" in capsys.readouterr().err


class TestResolveMonitoringSettings:
    """Unit tests for _resolve_monitoring_settings (E-XyfjuZ, T-QyNnf5): CLI > env > project
    config > built-in default (tri-state, revised D7 per early-gate reviewer feedback)."""

    def _call(self, self_heal, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from agent_orchestrator.cli import _resolve_monitoring_settings

        monkeypatch.chdir(tmp_path)  # isolate from this repo's own .ao/config.yaml
        return _resolve_monitoring_settings(self_heal)

    def test_no_cli_no_env_no_config_defaults_off(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = self._call(None, tmp_path, monkeypatch)
        assert cfg.self_heal is False
        assert cfg.monitor == "rules"

    def test_cli_true_wins_over_everything(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AO_SELF_HEAL", "0")
        cfg = self._call(True, tmp_path, monkeypatch)
        assert cfg.self_heal is True

    def test_cli_false_wins_over_env_and_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """D7 (revised): --no-self-heal must override an env/config value of True."""
        monkeypatch.setenv("AO_SELF_HEAL", "1")
        (tmp_path / ".ao").mkdir()
        (tmp_path / ".ao" / "config.yaml").write_text("monitoring:\n  self_heal: true\n")
        cfg = self._call(False, tmp_path, monkeypatch)
        assert cfg.self_heal is False

    def test_env_wins_over_config_when_cli_unset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AO_SELF_HEAL", "1")
        (tmp_path / ".ao").mkdir()
        (tmp_path / ".ao" / "config.yaml").write_text("monitoring:\n  self_heal: false\n")
        cfg = self._call(None, tmp_path, monkeypatch)
        assert cfg.self_heal is True

    def test_config_applies_when_cli_and_env_both_unset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AO_SELF_HEAL", raising=False)
        (tmp_path / ".ao").mkdir()
        (tmp_path / ".ao" / "config.yaml").write_text(
            "monitoring:\n  self_heal: true\n  monitor: my-agent\n  max_heal_retries_per_task: 3\n"
        )
        cfg = self._call(None, tmp_path, monkeypatch)
        assert cfg.self_heal is True
        assert cfg.monitor == "my-agent"
        assert cfg.max_heal_retries_per_task == 3

    def test_absent_monitoring_block_is_all_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AO_SELF_HEAL", raising=False)
        (tmp_path / ".ao").mkdir()
        (tmp_path / ".ao" / "config.yaml").write_text("workflow: w.json\n")
        cfg = self._call(None, tmp_path, monkeypatch)
        assert cfg.self_heal is False
        assert cfg.monitor == "rules"
        assert cfg.max_extensions_per_breaker == 1
        assert cfg.max_heal_retries_per_task == 1
        assert cfg.max_monitor_calls_per_run == 10


class TestBuildMonitor:
    """Unit tests for _build_monitor (E-XyfjuZ, T-QyNnf5)."""

    def test_rules_returns_rule_based_monitor(self, tmp_path: Path) -> None:
        from agent_orchestrator.cli import _build_monitor
        from agent_orchestrator.monitoring import RuleBasedMonitor
        from agent_orchestrator.project_config import MonitoringConfig

        cfg = MonitoringConfig(monitor="rules", heal_wait_seconds=5.0)
        monitor = _build_monitor(cfg, {}, None, None)
        assert isinstance(monitor, RuleBasedMonitor)

    def test_unknown_agent_name_exits_1(self, tmp_path: Path) -> None:
        from agent_orchestrator.cli import _build_monitor
        from agent_orchestrator.project_config import MonitoringConfig

        cfg = MonitoringConfig(monitor="ghost-agent")
        with pytest.raises(typer.Exit):
            _build_monitor(cfg, {}, None, None)

    def test_known_agent_name_returns_agent_monitor(self, tmp_path: Path) -> None:
        from agent_orchestrator.cli import _build_monitor
        from agent_orchestrator.executors.fake import FakeExecutor
        from agent_orchestrator.models import AgentSpec
        from agent_orchestrator.monitoring import AgentMonitor
        from agent_orchestrator.project_config import MonitoringConfig

        cfg = MonitoringConfig(monitor="my-monitor-agent")
        agent_map = {"my-monitor-agent": AgentSpec(executor="fake")}
        monitor = _build_monitor(cfg, agent_map, None, FakeExecutor())
        assert isinstance(monitor, AgentMonitor)
        assert monitor.name == "my-monitor-agent"
