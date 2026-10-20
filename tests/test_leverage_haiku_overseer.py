"""Leverage Haiku mode of the builtin `overseer-runner` template.

The model choice is tool-enforced (OV-R9m), so most of this exercises `overseer_tool.py`
directly: the precedence rule, the failed-attempt count off the ledger, and the checker's
verdict on a manifest unit. The scaffolding tests drive `ao new --leverage-haiku` through
CliRunner and confirm the dashboard launcher lists the param.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from typer.testing import CliRunner

from agent_orchestrator.cli import app
from agent_orchestrator.project_config import ProjectConfig
from agent_orchestrator.ui.service import DashboardService

runner = CliRunner()

_TEMPLATE_DIR = (
    Path(__file__).resolve().parents[1] / "src/agent_orchestrator/templates/builtin/overseer-runner"
)
CONTRACT_NAME = "leverage-haiku-contract.md"
WORK_UNIT = "workflows/overseer-runner/instructions/10-work-unit.md"


def _load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "overseer_tool_under_test_leverage", _TEMPLATE_DIR / "tools" / "overseer_tool.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ov = _load_tool()


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("AO_WORKFLOW", "AO_REPOSETS", "AO_AGENTS"):
        monkeypatch.delenv(var, raising=False)


def _scaffold(tmp_path: Path, *extra: str) -> Path:
    ws = tmp_path / "ws"
    (ws / ".ao").mkdir(parents=True, exist_ok=True)
    (ws / ".ao" / "config.yaml").write_text("")
    result = runner.invoke(app, ["new", "overseer-runner", "lev", "-w", str(ws), *extra])
    assert result.exit_code == 0, result.output
    (run_dir,) = (ws / "workflows/overseer-runner/runs").glob("*-lev")
    return run_dir


def _cfg(run_dir: Path) -> Any:
    return ov.load_config(run_dir)


class TestScaffolding:
    def test_flag_enables_the_mode_without_touching_limits(self, tmp_path: Path) -> None:
        run_dir = _scaffold(tmp_path, "--leverage-haiku")
        raw = json.loads((run_dir / "overseer-config.json").read_text())
        assert raw["leverage_haiku"] == "on" and raw["haiku_turns"] == 2
        # every limit keeps its template default
        assert (raw["wave_size"], raw["max_waves"], raw["max_injected_tasks"]) == (6, 12, 160)
        assert raw["run_budget_usd"] == 2000 and raw["task_budget_usd"] == 75
        contract = (run_dir / CONTRACT_NAME).read_text()
        assert "leverage_haiku = on" in contract and "{{" not in contract
        assert "`max_injected_tasks` (160)" in contract
        cfg = _cfg(run_dir)
        assert cfg.leverage_haiku is True and cfg.haiku_turns == 2

    def test_default_run_is_unchanged(self, tmp_path: Path) -> None:
        run_dir = _scaffold(tmp_path)
        assert not (run_dir / CONTRACT_NAME).exists()
        cfg = _cfg(run_dir)
        assert cfg.leverage_haiku is False
        assert ov.expected_unit_model(cfg, {}, "implement") == ""

    def test_haiku_turns_param(self, tmp_path: Path) -> None:
        run_dir = _scaffold(tmp_path, "--leverage-haiku", "--param", "haiku_turns=3")
        assert _cfg(run_dir).haiku_turns == 3

    def test_intake_and_checkpoint_instructions_point_at_the_contract(self) -> None:
        for name in ("00-intake.md", "20-checkpoint.md"):
            assert CONTRACT_NAME in (_TEMPLATE_DIR / "instructions" / name).read_text(), name

    def test_dashboard_launcher_lists_the_mode(self, tmp_path: Path) -> None:
        ws = tmp_path / "w"
        (ws / ".ao").mkdir(parents=True)
        service = DashboardService(str(ws), supervisor=None, project_config=ProjectConfig())  # type: ignore[arg-type]
        (entry,) = [t for t in service.list_templates() if t["name"] == "overseer-runner"]
        params = {p["name"]: p for p in entry["params"]}
        assert params["leverage_haiku"]["enum"] == ["on"]
        assert not params["leverage_haiku"]["required"]


class TestModelRule:
    @pytest.fixture
    def cfg(self, tmp_path: Path) -> Any:
        return _cfg(_scaffold(tmp_path, "--leverage-haiku"))

    @pytest.mark.parametrize("kind", ["implement", "fix", "document", "test", "verify"])
    def test_eligible_kinds_get_haiku_until_two_failures(self, cfg: Any, kind: str) -> None:
        assert ov.expected_unit_model(cfg, {}, kind, 0) == "haiku"
        assert ov.expected_unit_model(cfg, {}, kind, 1) == "haiku"
        assert ov.expected_unit_model(cfg, {}, kind, 2) == ""  # escalated: agent's own model

    @pytest.mark.parametrize("kind", ["research", "design", "review", "stabilize", "expand"])
    def test_judgement_kinds_never_get_haiku(self, cfg: Any, kind: str) -> None:
        assert ov.expected_unit_model(cfg, {}, kind, 0) == ""

    def test_brief_and_kind_map_models_outrank_the_mode(self, cfg: Any) -> None:
        assert ov.expected_unit_model(cfg, {"model": "sonnet"}, "implement", 0) == "sonnet"
        cfg.kind_map["implement"]["model"] = "opus"  # mutable dict inside the frozen config
        assert ov.expected_unit_model(cfg, {}, "implement", 0) == "opus"

    def test_default_unit_model_is_the_fallback_after_escalation(self, cfg: Any) -> None:
        cfg = dataclasses.replace(cfg, default_unit_model="sonnet")
        assert ov.expected_unit_model(cfg, {}, "implement", 2) == "sonnet"

    def test_allowlist_without_haiku_disables_the_mode(self, cfg: Any) -> None:
        cfg = dataclasses.replace(cfg, allowed_models=("sonnet",))
        assert ov.expected_unit_model(cfg, {}, "implement", 0) == ""

    def test_failed_attempts_are_counted_per_work_item_from_the_ledger(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def unit(wave: int, uid: str, item: str, **kw: str) -> dict[str, Any]:
            return {"type": "unit", "wave": wave, "unit_id": uid, "work_item": item, **kw}

        lines = [
            unit(1, "w01-01-a", "A1/x", verdict="fail"),
            unit(1, "w01-02-b", "A1/x", verdict="pass"),
            unit(2, "w02-01-c", "A1/x", outcome="blocked"),
            unit(2, "w02-02-d", "A2/y", verdict="fail"),
        ]
        monkeypatch.setattr(ov, "read_ledger_lines", lambda inst: lines)
        inst = Path("unused")
        assert ov._failed_attempts(inst, 2, "A1/x") == 2
        assert ov._failed_attempts(inst, 1, "A1/x") == 1  # only waves <= k
        assert ov._failed_attempts(inst, 2, "A3/new") == 0
        assert ov._failed_attempts(inst, None, "A1/x") == 0  # intake: no history


class TestCheckerEnforcesIt:
    @staticmethod
    def _check(
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        unit_model: str | None,
        prior_verdicts: list[str],
    ) -> list[Any]:
        """Run OV-R9m on one wave-2 `implement` unit for work item A1/x whose ledger history
        is *prior_verdicts* (one prior unit per entry)."""
        unit_id = "w02-01-impl"
        path = ov.brief_path(run_dir, 2, unit_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"kind": "implement", "work_item": "A1/x", "wave": 2}))
        history = [
            {"type": "unit", "wave": 1, "unit_id": f"w01-0{n}-u", "work_item": "A1/x", "verdict": v}
            for n, v in enumerate(prior_verdicts, 1)
        ]
        monkeypatch.setattr(ov, "read_ledger_lines", lambda inst: history)
        raw: dict[str, Any] = {"id": unit_id}
        if unit_model is not None:
            raw["model"] = unit_model
        entry = ov.ClassifiedEntry(raw=raw, index=0, entry_id=unit_id, entry_class="unit")
        ctx = ov.WaveEntryCheckContext(
            cfg=_cfg(run_dir),
            inst=run_dir,
            instance_dir=str(run_dir),
            emitter_id="ck-01",
            next_wave=2,
            manifest_ids=frozenset({unit_id}),
            charter_ask_ids=None,
        )
        return ov._check_entry_r9m_model(ctx, entry)

    def test_unit_without_the_expected_model_is_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_dir = _scaffold(tmp_path, "--leverage-haiku")
        (violation,) = self._check(run_dir, monkeypatch, None, [])
        assert "must be 'haiku'" in violation.message

    def test_unit_with_haiku_passes_on_a_fresh_or_once_failed_item(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_dir = _scaffold(tmp_path, "--leverage-haiku")
        assert self._check(run_dir, monkeypatch, "haiku", []) == []
        assert self._check(run_dir, monkeypatch, "haiku", ["fail", "pass"]) == []

    def test_after_two_failures_the_unit_must_drop_haiku(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_dir = _scaffold(tmp_path, "--leverage-haiku")
        assert self._check(run_dir, monkeypatch, None, ["fail", "fail"]) == []
        (violation,) = self._check(run_dir, monkeypatch, "haiku", ["fail", "fail"])
        assert "omit `model`" in violation.message

    def test_mode_off_still_rejects_a_stray_haiku_model(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_dir = _scaffold(tmp_path)
        (violation,) = self._check(run_dir, monkeypatch, "haiku", [])
        assert "omit `model`" in violation.message
