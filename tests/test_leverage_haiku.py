"""Leverage Haiku mode of the builtin `routed-runner` template.

Covers the user-facing surface (`ao new --leverage-haiku` via CliRunner, the dashboard
launcher's param listing), the rendered artifacts (contract present only when the mode is on,
fan-out cap, flag precedence) and the contract itself: its JSON entry shapes are expanded into
a real manifest that must parse as `TaskSpec`s, resolve every `depends_on`, honour the
`forbidden_task_models` guard and the Haiku-placement rules, and fit the fan-out cap.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import agent_orchestrator
from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.cli import app
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import AgentSpec, RepoRef, RepoSet, TaskSpec, WorkflowSpec
from agent_orchestrator.project_config import ProjectConfig
from agent_orchestrator.runstate import RunStateStore
from agent_orchestrator.spec import validate_task_model_policy
from agent_orchestrator.ui.service import DashboardService

runner = CliRunner()

TEMPLATE_DIR = Path(agent_orchestrator.__file__).parent / "templates/builtin/routed-runner"
CONTRACT_NAME = "leverage-haiku-contract.md"
TEST_ANGLES = ["contract", "edge", "regression", "property"]
REVIEW_LENSES = ["spec", "correctness", "maintainability", "testquality"]
# Ids a ladder task emits besides the per-angle / per-lens ones (the contract's "5 + 2N").
FIXED_ENTRIES_PER_TASK = 5
HAIKU_ALLOWED_PREFIXES = ("impl1-", "test-", "impl2-", "verify-")
CONTRACT_HEADER = "## Entries per ladder task"
AGGREGATOR_HEADER = "## Aggregator"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("AO_WORKFLOW", "AO_REPOSETS", "AO_AGENTS"):
        monkeypatch.delenv(var, raising=False)


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / ".ao").mkdir(parents=True, exist_ok=True)
    (ws / ".ao" / "config.yaml").write_text("")
    return ws


def _new(ws: Path, slug: str, *extra: str) -> Path:
    result = runner.invoke(app, ["new", "routed-runner", slug, "-w", str(ws), *extra])
    assert result.exit_code == 0, result.output
    (run_dir,) = (ws / "workflows/routed-runner/runs").glob(f"*-{slug}")
    return run_dir


def _fanout_threshold(run_dir: Path) -> int:
    wf = json.loads((run_dir / "workflow.json").read_text())
    (breaker,) = [b for b in wf["circuit_breakers"] if b["id"] == "runaway-fanout"]
    return int(breaker["threshold"])


class TestScaffolding:
    def test_flag_renders_contract_and_keeps_limits(self, tmp_path: Path) -> None:
        run_dir = _new(_workspace(tmp_path), "on", "--leverage-haiku")
        contract = (run_dir / CONTRACT_NAME).read_text()
        assert "leverage_haiku = on" in contract
        assert "angles per task: **3**" in contract
        assert "(`max_injected_tasks`): **115**" in contract
        assert "{{" not in contract  # every token substituted
        assert _fanout_threshold(run_dir) == 115  # the flag never raises a limit
        assert (run_dir / "agents.leverage-haiku.json").exists()

    def test_default_run_has_no_contract_and_keeps_115(self, tmp_path: Path) -> None:
        run_dir = _new(_workspace(tmp_path), "off")
        assert not (run_dir / CONTRACT_NAME).exists()
        assert not (run_dir / "agents.leverage-haiku.json").exists()
        assert _fanout_threshold(run_dir) == 115

    def test_param_form_is_equivalent_but_keeps_default_cap(self, tmp_path: Path) -> None:
        run_dir = _new(_workspace(tmp_path), "param", "--param", "leverage_haiku=on")
        assert (run_dir / CONTRACT_NAME).exists()
        assert _fanout_threshold(run_dir) == 115

    def test_explicit_param_beats_flag_defaults(self, tmp_path: Path) -> None:
        run_dir = _new(
            _workspace(tmp_path),
            "mix",
            "--leverage-haiku",
            "--param",
            "max_injected_tasks=250",
            "--param",
            "haiku_angles=2",
        )
        assert _fanout_threshold(run_dir) == 250
        assert "angles per task: **2**" in (run_dir / CONTRACT_NAME).read_text()

    def test_invalid_mode_value_rejected(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["new", "routed-runner", "bad", "-w", str(_workspace(tmp_path)),
             "--param", "leverage_haiku=maybe"],
        )  # fmt: skip
        assert result.exit_code != 0
        assert "not in allowed enum" in result.output

    def test_breakdown_instruction_points_at_the_contract(self) -> None:
        text = (TEMPLATE_DIR / "instructions/07-task-breakdown.md").read_text()
        assert CONTRACT_NAME in text and "authoritative" in text

    def test_dashboard_launcher_lists_the_mode_as_an_enum_param(self, tmp_path: Path) -> None:
        ws = _workspace(tmp_path)
        service = DashboardService(str(ws), supervisor=None, project_config=ProjectConfig())  # type: ignore[arg-type]
        (entry,) = [t for t in service.list_templates() if t["name"] == "routed-runner"]
        params = {p["name"]: p for p in entry["params"]}
        assert params["leverage_haiku"]["enum"] == ["on"]
        assert not params["leverage_haiku"]["required"]
        assert "Haiku" in params["leverage_haiku"]["description"]
        assert params["haiku_angles"]["enum"] == ["2", "3", "4"]


# ---------------------------------------------------------------------------
# The contract's JSON entry shapes -> a real manifest
# ---------------------------------------------------------------------------


def _rendered_contract(tmp_path: Path, angles: int) -> str:
    run_dir = _new(
        _workspace(tmp_path), f"c{angles}", "--leverage-haiku", "--param", f"haiku_angles={angles}"
    )
    return (run_dir / CONTRACT_NAME).read_text()


def _json_blocks(section: str) -> list[dict[str, Any]]:
    return [json.loads(b) for b in re.findall(r"```json\n(.*?)\n```", section, re.S)]


def _expand(entry: dict[str, Any], tid: str, angles: list[str], lenses: list[str]) -> list[dict]:
    """Instantiate one contract entry shape for `tid`: fan `<angle>`/`<lens>` ids out and
    expand the `<angle-1>`/`<lens-1>` + "...every..." list placeholders."""

    def fill(text: str, angle: str = "", lens: str = "") -> str:
        return text.replace("<tid>", tid).replace("<angle>", angle).replace("<lens>", lens)

    def expand_list(values: list[str], angle: str, lens: str) -> list[str]:
        out: list[str] = []
        for v in values:
            if v.startswith("..."):
                continue
            if "<angle-1>" in v:
                out += [fill(v.replace("<angle-1>", a)) for a in angles]
            elif "<lens-1>" in v:
                out += [fill(v.replace("<lens-1>", ln)) for ln in lenses]
            else:
                out.append(fill(v, angle, lens))
        return out

    if "<angle>" in entry["id"]:
        variants = [(a, "") for a in angles]
    elif "<lens>" in entry["id"]:
        variants = [("", ln) for ln in lenses]
    else:
        variants = [("", "")]
    result = []
    for angle, lens in variants:
        e = dict(entry)
        e["id"] = fill(entry["id"], angle, lens)
        for key in ("depends_on", "inputs", "outputs"):
            e[key] = expand_list(entry.get(key, []), angle, lens)
        result.append(e)
    return result


def _ladder(tmp_path: Path, n_angles: int, tid: str = "t01-x") -> list[dict]:
    contract = _rendered_contract(tmp_path, n_angles)
    section = contract.split(CONTRACT_HEADER)[1].split(AGGREGATOR_HEADER)[0]
    return [
        e
        for shape in _json_blocks(section)
        for e in _expand(shape, tid, TEST_ANGLES[:n_angles], REVIEW_LENSES[:n_angles])
    ]


@pytest.mark.parametrize("n_angles", [2, 3, 4])
class TestContractManifest:
    def test_every_entry_parses_and_dependencies_resolve(
        self, tmp_path: Path, n_angles: int
    ) -> None:
        entries = _ladder(tmp_path, n_angles)
        tasks = [TaskSpec(**e) for e in entries]  # unknown / invalid fields raise here
        ids = [t.id for t in tasks]
        assert len(ids) == len(set(ids))
        assert len(ids) == FIXED_ENTRIES_PER_TASK + 2 * n_angles
        for t in tasks:
            for dep in t.depends_on:
                assert dep in ids or dep == "task-breakdown", (t.id, dep)

    def test_haiku_only_where_allowed_and_escalation_is_unpinned(
        self, tmp_path: Path, n_angles: int
    ) -> None:
        entries = _ladder(tmp_path, n_angles)
        for e in entries:
            if e["id"].startswith(HAIKU_ALLOWED_PREFIXES):
                assert e["model"] == "haiku", e["id"]
            else:  # reviews, impl3 and retest must inherit the stronger agent model
                assert "model" not in e, e["id"]
        assert "reviewer" not in {
            e["agent"] for e in entries
        }  # Haiku reviewers use `reviewer-haiku`

    def test_guard_accepts_the_ladder_with_recommended_forbid_lists(
        self, tmp_path: Path, n_angles: int
    ) -> None:
        agents = {
            "developer": AgentSpec(executor="fake"),
            "tester": AgentSpec(executor="fake"),
            "reviewer-haiku": AgentSpec(executor="fake"),
        }
        tasks = [TaskSpec(**e) for e in _ladder(tmp_path, n_angles)]
        validate_task_model_policy(tasks, agents)  # no exception

    def test_escalation_is_skippable_by_the_verify_stub(
        self, tmp_path: Path, n_angles: int
    ) -> None:
        by_id = {e["id"]: e for e in _ladder(tmp_path, n_angles)}
        verify_dir = Path(by_id["verify-t01-x"]["outputs"][0]).parent
        for tid in ("impl3-t01-x", "retest-t01-x"):
            assert by_id[tid]["skip_if_outputs_exist"] is True
            assert Path(by_id[tid]["outputs"][0]).parent == verify_dir
        # ...and the verify instruction actually tells the agent to write both stubs.
        verify_md = (TEMPLATE_DIR / "instructions/64-haiku-verify.md").read_text()
        assert "dev-pass-3.md" in verify_md and "test-pass-3.md" in verify_md

    def test_angles_get_distinct_briefs(self, tmp_path: Path, n_angles: int) -> None:
        entries = _ladder(tmp_path, n_angles)
        briefs = [i for e in entries for i in e["inputs"] if "/briefs/" in i]
        assert len(briefs) == len(set(briefs)) == 1 + 2 * n_angles

    def test_entries_fit_the_cap_formula(self, tmp_path: Path, n_angles: int) -> None:
        per_task = len(_ladder(tmp_path, n_angles))
        cap = 400  # an explicitly raised cap leaves room for ~35 tasks at N=3
        assert (cap - 10) // per_task >= 30


def test_every_referenced_instruction_file_exists(tmp_path: Path) -> None:
    contract = _rendered_contract(tmp_path, 3)
    paths = set(re.findall(r"workflows/routed-runner/instructions/([\w.-]+\.md)", contract))
    assert {"60-haiku-impl.md", "67-haiku-aggregate.md", "65-escalate-fix.md"} <= paths
    for name in paths:
        assert (TEMPLATE_DIR / "instructions" / name).is_file(), name


def test_aggregator_block_keeps_the_waited_on_output_path(tmp_path: Path) -> None:
    contract = _rendered_contract(tmp_path, 3)
    (agg,) = _json_blocks(contract.split(AGGREGATOR_HEADER)[1].split("## Cross-task")[0])
    assert agg["id"] == "aggregate-epic-tasks"
    assert agg["outputs"][0].endswith("/outputs/all-tasks-complete.md")
    assert "model" not in agg


# ---------------------------------------------------------------------------
# Engine integration: the ladder runs, and a CLEAN verify skips the Sonnet-tier pair
# ---------------------------------------------------------------------------


class _VerifyingFake(FakeExecutor):
    """FakeExecutor whose `verify-*` task behaves like the real one per the instruction:
    when `clean`, also pre-write the escalation outputs (dev-pass-3.md / test-pass-3.md)."""

    def __init__(self, clean: bool, **kw: Any) -> None:
        super().__init__(**kw)
        self.clean = clean

    def execute(self, ctx: Any) -> Any:
        if self.clean and ctx.task_id.startswith("verify-"):
            report_dir = Path(ctx.output_paths[0]).parent
            for stub in ("dev-pass-3.md", "test-pass-3.md"):
                (report_dir / stub).write_text("SKIPPED - Haiku ladder closed this task\n")
        return super().execute(ctx)


def _run_ladder(tmp_path: Path, clean: bool) -> tuple[Any, _VerifyingFake]:
    ws = _workspace(tmp_path)
    run_dir = _new(ws, "eng", "--leverage-haiku")
    contract = (run_dir / CONTRACT_NAME).read_text()
    section = contract.split(CONTRACT_HEADER)[1].split(AGGREGATOR_HEADER)[0]
    tid = "t01-x"
    entries = [
        e
        for shape in _json_blocks(section)
        for e in _expand(shape, tid, TEST_ANGLES[:3], REVIEW_LENSES[:3])
    ]
    # What the architect writes alongside the manifest: design/tasks + one brief per input.
    for entry in entries:
        for inp in entry["inputs"]:
            path = ws / inp
            if not path.exists() and ("/briefs/" in inp or inp.endswith(("design.md", "tasks.md"))):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x\n")
    (ws / "i.md").write_text("x")
    # Real instruction assets, where the manifest says they live.
    instr = ws / "workflows/routed-runner/instructions"
    instr.mkdir(parents=True, exist_ok=True)
    for src in (TEMPLATE_DIR / "instructions").glob("*.md"):
        (instr / src.name).write_text(src.read_text())

    emitter = TaskSpec(
        id="task-breakdown",
        agent="architect",
        instruction="i.md",
        outputs=[],
        emit_tasks=True,
        task_manifest_path="out/manifest.json",
    )
    wf = WorkflowSpec(version="1.0", id="wf", repo_set="rs", tasks=[emitter])
    agents = {
        name: AgentSpec(executor="fake")
        for name in ("architect", "developer", "tester", "reviewer-haiku")
    }
    reposets = {
        "rs": RepoSet(workspace_root=str(ws), repos=[RepoRef(id="core", path=".", role="primary")])
    }
    store = LocalFsArtifactStore(str(ws))
    executor = _VerifyingFake(clean=clean, emit_payloads={"task-breakdown": {"tasks": entries}})
    state = Orchestrator(executor, store, RunStateStore(str(ws), store)).run(wf, reposets, agents)
    return state, executor


def test_clean_ladder_skips_the_escalation_tier(tmp_path: Path) -> None:
    state, executor = _run_ladder(tmp_path, clean=True)
    assert state.status == "succeeded"
    assert state.tasks["impl3-t01-x"].status == "skipped"
    assert state.tasks["retest-t01-x"].status == "skipped"
    assert "impl3-t01-x" not in executor.contexts  # never dispatched -> no stronger-model spend
    assert state.tasks["verify-t01-x"].status == "succeeded"


def test_unclean_ladder_escalates_to_the_stronger_tier(tmp_path: Path) -> None:
    state, executor = _run_ladder(tmp_path, clean=False)
    assert state.status == "succeeded"
    assert state.tasks["impl3-t01-x"].status == "succeeded"
    assert state.tasks["retest-t01-x"].status == "succeeded"
    # Haiku turn 1 -> parallel angles -> Haiku turn 2 all ran before escalation.
    assert {"impl1-t01-x", "impl2-t01-x", "verify-t01-x"} <= set(executor.contexts)
    injected = {t.id: t for t in state.injected_tasks}
    assert injected["impl3-t01-x"].model is None  # inherits the stronger agent model
    assert injected["impl2-t01-x"].model == "haiku"
