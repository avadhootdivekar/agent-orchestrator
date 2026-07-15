"""CliRunner end-to-end tests for agent-based monitoring & self-healing (T-QyNnf5,
epic E-XyfjuZ) — the CLI-wiring layer, distinct from the engine-API integration tests in
`tests/test_monitoring_breaker_consult.py` / `tests/test_monitoring_self_heal.py` (memory
`engine-api-tests-dont-cover-cli`: CLI flag parsing, config discovery, and command routing
are a separate code path that only CliRunner tests exercise).

Scope note on self-heal specifically: `DispatchExecutor` (used by every real `ao run`/
`ao resume` invocation) always constructs a bare, unconfigurable `FakeExecutor()` for
`executor: "fake"` agents — there is no way to make a "fake" agent fail deterministically
through the unmodified CLI path (confirmed: existing e2e tests needing a genuine first-run
failure construct the engine-API `Orchestrator` directly with a custom `FakeExecutor`, per
`tests/test_e2e_cli.py::test_resume_via_cli_first_run_fails_then_resumes` and the
`meta/learning-compact.md` convention it documents). To prove the FULL CLI-to-engine wiring
for self-heal on a GENUINE dispatch failure (not a manufactured one), these tests instead
use the REAL `claude_cli` executor with a deterministic, fast, network-free `sh -c
"...; exit 1"` command_template in place of `claude` — no `claude` binary or API key is
required, and the failure is exactly as real as any other subprocess-based task failure.
The "heals AND eventually succeeds" happy path is proven at the engine-API level
(`tests/test_monitoring_self_heal.py`, deterministic via a scripted test-double executor);
these CLI tests prove the config/flag resolution, a genuine dispatch failure reaching
Consult Point B, transient-pattern classification, and the retry-requeue mechanics --
all through the real `ao run`/`ao resume` commands.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.cli import app
from agent_orchestrator.models import RunState, TaskRunState
from agent_orchestrator.runstate import RunStateStore

runner = CliRunner()

_ALWAYS_FAILS_COMMAND = ["sh", "-c", "echo 'connection reset by peer' >&2; exit 1"]


def _write_breaker_specs(
    tmp_path: Path, *, mode_a: str, mode_b: str | None = None
) -> tuple[Path, Path, Path]:
    """A 2-task workflow ("a" -> "b") with one or two task_failures breakers."""
    circuit_breakers = [
        {
            "id": "fails-cap-a",
            "condition": "task_failures",
            "action": "stop",
            "threshold": 1,
            "mode": mode_a,
        }
    ]
    if mode_b is not None:
        circuit_breakers.append(
            {
                "id": "fails-cap-b",
                "condition": "task_failures",
                "action": "stop",
                "threshold": 1,
                "mode": mode_b,
            }
        )
    wf_path = tmp_path / "workflow.json"
    wf_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "id": "monitor-wf",
                "repo_set": "rs",
                "tasks": [
                    {
                        "id": "a",
                        "agent": "ag",
                        "instruction": "specs/examples/instructions/design.md",
                        "outputs": ["out/a.txt"],
                    },
                    {
                        "id": "b",
                        "agent": "ag",
                        "instruction": "specs/examples/instructions/design.md",
                        "depends_on": ["a"],
                        "outputs": ["out/b.txt"],
                    },
                ],
                "circuit_breakers": circuit_breakers,
            }
        )
    )
    rs_path = tmp_path / "reposets.json"
    rs_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "repo_sets": {
                    "rs": {
                        "workspace_root": str(tmp_path),
                        "repos": [{"id": "core", "path": ".", "role": "primary"}],
                    }
                },
            }
        )
    )
    ag_path = tmp_path / "agents.json"
    ag_path.write_text(json.dumps({"version": "1.0", "agents": {"ag": {"executor": "fake"}}}))
    return wf_path, rs_path, ag_path


def _manufacture_phantom_failure_run(tmp_path: Path, wf_id: str = "monitor-wf") -> str:
    """Persist a RunState with ONE phantom already-"failed" entry that doesn't correspond
    to any real workflow task (see test_monitoring_breaker_consult.py's module docstring
    for why this is needed: a REAL dispatched failure ends the run via a separate,
    unconditional check regardless of breakers, so a phantom failure is what lets a
    breaker trip at a boundary where the just-dispatched task itself succeeds)."""
    store = LocalFsArtifactStore(str(tmp_path))
    rs_store = RunStateStore(str(tmp_path), store)
    now = datetime.now(UTC)
    state = RunState(
        run_id=f"{wf_id}-20260101T000000Z",
        workflow_id=wf_id,
        repo_set="rs",
        started_at=now.isoformat(),
        updated_at=now.isoformat(),
        status="running",
        tasks={
            "phantom-0": TaskRunState(
                status="failed", ended_at=(now - timedelta(hours=1)).isoformat()
            )
        },
    )
    rs_store.save(state)
    return state.run_id


def _load_state(tmp_path: Path, run_id: str) -> dict:
    state_path = tmp_path / ".orchestrator" / "runs" / run_id / "state.json"
    return json.loads(state_path.read_text())


def _invoke_resume(tmp_path: Path, wf: Path, rs: Path, ag: Path, run_id: str, extra: list[str]):
    return runner.invoke(
        app,
        [
            "resume",
            "--run-id",
            run_id,
            "--workflow",
            str(wf),
            "--reposets",
            str(rs),
            "--agents",
            str(ag),
            *extra,
        ],
        env={"AO_WORKSPACE_ROOT": str(tmp_path)},
    )


# ---------------------------------------------------------------------------
# Consult Point A via the real CLI (ao resume)
# ---------------------------------------------------------------------------


class TestBreakerConsultViaCli:
    def test_recommend_mode_breaker_extends_and_run_succeeds_via_cli(self, tmp_path: Path) -> None:
        wf, rs, ag = _write_breaker_specs(tmp_path, mode_a="recommend")
        run_id = _manufacture_phantom_failure_run(tmp_path)

        result = _invoke_resume(tmp_path, wf, rs, ag, run_id, [])

        assert result.exit_code == 0, result.output
        state = _load_state(tmp_path, run_id)
        assert state["status"] == "succeeded"
        assert state["tasks"]["a"]["status"] == "succeeded"
        assert state["tasks"]["b"]["status"] == "succeeded"
        assert state["tripped_breakers"] == []  # un-latched by the extension
        assert state["breaker_overrides"] == {"fails-cap-a": 2.0}
        assert len(state["monitor_decisions"]) == 1
        assert state["monitor_decisions"][0]["decision"] == "extend"
        assert state["monitor_decisions"][0]["monitor"] == "rules"

    def test_hard_mode_breaker_halts_via_cli_no_consult(self, tmp_path: Path) -> None:
        wf, rs, ag = _write_breaker_specs(tmp_path, mode_a="hard")
        run_id = _manufacture_phantom_failure_run(tmp_path)

        result = _invoke_resume(tmp_path, wf, rs, ag, run_id, [])

        assert result.exit_code == 1, result.output
        state = _load_state(tmp_path, run_id)
        assert state["status"] == "failed"
        assert state["monitor_decisions"] == []
        assert [tb["id"] for tb in state["tripped_breakers"]] == ["fails-cap-a"]

    def test_mixed_hard_and_recommend_halts_via_cli_no_consult(self, tmp_path: Path) -> None:
        wf, rs, ag = _write_breaker_specs(tmp_path, mode_a="hard", mode_b="recommend")
        run_id = _manufacture_phantom_failure_run(tmp_path)

        result = _invoke_resume(tmp_path, wf, rs, ag, run_id, [])

        assert result.exit_code == 1, result.output
        state = _load_state(tmp_path, run_id)
        assert state["status"] == "failed"
        assert state["monitor_decisions"] == []
        assert {tb["id"] for tb in state["tripped_breakers"]} == {"fails-cap-a", "fails-cap-b"}


# ---------------------------------------------------------------------------
# Self-heal via the real CLI (ao run), using a deterministic always-failing subprocess
# ---------------------------------------------------------------------------


def _write_self_heal_specs(tmp_path: Path) -> tuple[Path, Path, Path]:
    wf_path = tmp_path / "workflow.json"
    wf_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "id": "heal-wf",
                "repo_set": "rs",
                "tasks": [
                    {
                        "id": "flaky",
                        "agent": "real-ish",
                        "instruction": "specs/examples/instructions/design.md",
                        "outputs": ["out/flaky.txt"],
                    }
                ],
            }
        )
    )
    rs_path = tmp_path / "reposets.json"
    rs_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "repo_sets": {
                    "rs": {
                        "workspace_root": str(tmp_path),
                        "repos": [{"id": "core", "path": ".", "role": "primary"}],
                    }
                },
            }
        )
    )
    ag_path = tmp_path / "agents.json"
    ag_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "agents": {
                    "real-ish": {
                        "executor": "claude_cli",
                        "command_template": _ALWAYS_FAILS_COMMAND,
                    }
                },
            }
        )
    )
    return wf_path, rs_path, ag_path


def _write_ao_config(tmp_path: Path, monitoring: dict) -> None:
    ao_dir = tmp_path / ".ao"
    ao_dir.mkdir(parents=True, exist_ok=True)
    (ao_dir / "config.yaml").write_text(
        "workflow: workflow.json\n"
        "reposets: reposets.json\n"
        "agents: agents.json\n"
        "monitoring:\n" + "".join(f"  {k}: {v}\n" for k, v in monitoring.items())
    )


class TestSelfHealViaCli:
    def test_config_enables_self_heal_and_a_genuine_failure_is_consulted_and_retried(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No --self-heal flag at all -- purely config-driven. The command deterministically
        fails every time (same transient stderr message both attempts), so: attempt 1 fails
        -> consulted -> "retry" (prior_heal_retries=0) -> waits (heal_wait_seconds, tiny for
        test speed) -> attempt 2 fails again -> consulted again, but the bound
        (max_heal_retries_per_task default 1) is now exhausted -> accept_failure without a
        second consult -> run fails. This exercises config resolution, a REAL dispatch
        failure reaching Consult Point B, transient classification, and the retry-requeue
        mechanics, all through the unmodified `ao run` command.

        `find_project_config()` walks up from `Path.cwd()` (NOT `AO_WORKSPACE_ROOT`), so
        `.ao/config.yaml` is only discovered once cwd is actually `tmp_path` (matches the
        established `monkeypatch.chdir(tmp_path)` convention in `test_project_config.py`).
        """
        monkeypatch.chdir(tmp_path)
        wf, rs, ag = _write_self_heal_specs(tmp_path)
        _write_ao_config(tmp_path, {"self_heal": "true", "heal_wait_seconds": "0.01"})

        result = runner.invoke(
            app,
            ["run", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )

        assert result.exit_code == 1, result.output
        run_dirs = list((tmp_path / ".orchestrator" / "runs").iterdir())
        assert len(run_dirs) == 1
        state = json.loads((run_dirs[0] / "state.json").read_text())
        assert state["status"] == "failed"
        assert state["tasks"]["flaky"]["status"] == "failed"
        assert len(state["monitor_decisions"]) == 1
        assert state["monitor_decisions"][0]["decision"] == "retry"
        assert state["monitor_decisions"][0]["consult_point"] == "task_failure"

    def test_no_self_heal_flag_overrides_config_enabled_self_heal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """D7 (revised post early-gate review): --no-self-heal must be able to force self
        heal OFF even when config enables it -- the failure is never even consulted."""
        monkeypatch.chdir(tmp_path)
        wf, rs, ag = _write_self_heal_specs(tmp_path)
        _write_ao_config(tmp_path, {"self_heal": "true", "heal_wait_seconds": "0.01"})

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
                "--no-self-heal",
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )

        assert result.exit_code == 1, result.output
        run_dirs = list((tmp_path / ".orchestrator" / "runs").iterdir())
        state = json.loads((run_dirs[0] / "state.json").read_text())
        assert state["status"] == "failed"
        assert state["monitor_decisions"] == []  # never consulted at all

    def test_self_heal_flag_enables_with_no_config_self_heal_key_at_all(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The CLI flag alone (no `monitoring.self_heal` key in config at all -- config only
        supplies a fast `heal_wait_seconds` so this test doesn't block on the real 30s
        default `time.sleep`) is sufficient to enable self-heal."""
        monkeypatch.chdir(tmp_path)
        wf, rs, ag = _write_self_heal_specs(tmp_path)
        _write_ao_config(tmp_path, {"heal_wait_seconds": "0.01"})

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
                "--self-heal",
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )

        assert result.exit_code == 1, result.output
        run_dirs = list((tmp_path / ".orchestrator" / "runs").iterdir())
        state = json.loads((run_dirs[0] / "state.json").read_text())
        assert len(state["monitor_decisions"]) == 1
        assert state["monitor_decisions"][0]["decision"] == "retry"


# ---------------------------------------------------------------------------
# Invalid monitor config -> clean CLI error
# ---------------------------------------------------------------------------


class TestInvalidMonitorConfig:
    def test_unknown_monitor_agent_name_exits_1_before_dispatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        wf, rs, ag = _write_breaker_specs(tmp_path, mode_a="hard")
        _write_ao_config(tmp_path, {"monitor": "does-not-exist"})

        result = runner.invoke(
            app,
            ["run", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )

        assert result.exit_code == 1, result.output
        assert "does-not-exist" in result.output
        # Never dispatched: no run directory was even created.
        runs_dir = tmp_path / ".orchestrator" / "runs"
        assert not runs_dir.exists() or list(runs_dir.iterdir()) == []
