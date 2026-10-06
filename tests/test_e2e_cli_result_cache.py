"""E-1 .. E-6 (T-JCOAsq Part 3, HLD 18.1): the cross-run result cache, end to end through the CLI.

Every test enters at the outermost boundary: `ao run`, `ao resume`, `ao status` and
`ao report-usage` through typer's `CliRunner` on the real `agent_orchestrator.cli.app`, with
`executor: fake` agents. The conventions are the HLD 18 ones:

* `monkeypatch.chdir(tmp_path)` so the repository's own `.ao/config.yaml` is never picked up;
* the workflows opt in with `"defaults": {"cache": true}` (or a task-level flag where the test is
  about opt-in itself);
* dispatches are counted by a wrapper on `FakeExecutor.execute`; the same wrapper sets cost and
  token usage on successful results (`FakeExecutor` reports none) and stamps each output with the
  dispatch ordinal, so a restored file is provably the stored one and not a fresh dispatch;
* `agent_orchestrator.runstate._utc_now` is a ticking clock (run ids have 1 s granularity).

Where each id lives: E-1 `TestE1SecondRunIsAllHits`; E-2 `TestE2PlainRun`; E-3 `TestE3NoCacheWins`;
E-4 `TestE4PrecedenceMatrix`; E-5 `TestE5DoubleOptIn`; E-6 `TestE6Resume`. E-7 (the `ao cache`
admin commands) is `test_e2e_cli_result_cache_admin.py`; E-8 is `tests/bench`; E-9/E-10/E-11 are
`tests/cache/test_cli_result_cache_wiring.py`.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import agent_orchestrator
from agent_orchestrator import runstate as runstate_mod
from agent_orchestrator.cli import app
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import TaskResult

runner = CliRunner()

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = Path(agent_orchestrator.__file__).resolve().parents[1]
GOLDEN_DIR = Path(__file__).parent / "fixtures" / "result_cache" / "golden"
SUBPROCESS_TIMEOUT_SECONDS = 180

COST_USD = 0.5
IN_TOKENS = 1000
OUT_TOKENS = 270
SAVED_TOKENS_PER_TASK = IN_TOKENS + OUT_TOKENS
KEY_HEX_LENGTH = 64
BANNER_PREFIX = "Result cache: "
SUMMARY_PREFIX = "Result cache: hits="
ENV_VARS = ("AO_CACHE", "AO_WORKSPACE_ROOT")
_CLOCK_START = datetime(2026, 10, 5, 9, 0, 0, tzinfo=UTC)

# HLD 18.1 E-2 allow-list. Manager decision (T-JCOAsq Part 3, recorded in HANDOFF.md): the CLI
# registers the `ao cache` group eagerly, so `cache.cli` is loaded by every `ao` start; its
# module-level imports stay limited to typer + constants + settings (guarded by
# tests/cache/test_cli_result_cache_wiring.py::TestCacheCliStaysImportLight).
CLI_PATH_ALLOWED_CACHE_MODULES = {
    "agent_orchestrator.cache",
    "agent_orchestrator.cache.constants",
    "agent_orchestrator.cache.settings",
    "agent_orchestrator.cache.cli",
}


# --------------------------------------------------------------------------------------------
# rig
# --------------------------------------------------------------------------------------------
@dataclass
class Dispatches:
    """What the `FakeExecutor.execute` wrapper saw."""

    calls: list[str] = field(default_factory=list)
    failing: set[str] = field(default_factory=set)

    def count(self, task_id: str | None = None) -> int:
        return len(self.calls) if task_id is None else self.calls.count(task_id)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AO_WORKSPACE_ROOT", str(tmp_path))
    ticks = itertools.count()
    monkeypatch.setattr(
        runstate_mod, "_utc_now", lambda: _CLOCK_START + timedelta(seconds=next(ticks))
    )


@pytest.fixture
def dispatches(monkeypatch: pytest.MonkeyPatch) -> Dispatches:
    """Count every dispatch; report cost and tokens; stamp outputs with their dispatch ordinal."""
    seen = Dispatches()
    original = FakeExecutor.execute

    def execute(self: FakeExecutor, ctx: Any) -> TaskResult:
        seen.calls.append(ctx.task_id)
        if ctx.task_id in seen.failing:
            return TaskResult(
                task_id=ctx.task_id,
                status="failed",
                attempts=1,
                exit_code=1,
                error="injected failure",
                output_artifact_path=ctx.output_dir or None,
            )
        result = original(self, ctx)
        if result.status != "succeeded":
            return result
        ordinal = seen.calls.count(ctx.task_id)
        for path in ctx.output_paths:
            Path(path).write_text(f"{ctx.task_id} written by dispatch {ordinal}\n", "utf-8")
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
    return seen


def _task(
    task_id: str,
    *,
    depends_on: tuple[str, ...] = (),
    inputs: tuple[str, ...] = (),
    cache: bool | None = None,
) -> dict[str, object]:
    spec: dict[str, object] = {
        "id": task_id,
        "agent": "ag",
        "instruction": f"instructions/{task_id}.md",
        "depends_on": list(depends_on),
        "inputs": list(inputs),
        "outputs": [f"out/{task_id}.md"],
        "skip_if_outputs_exist": False,  # a lookup is only reachable past should_skip
    }
    if cache is not None:
        spec["cache"] = cache
    return spec


def chain(*, cache: bool | None = None) -> list[dict[str, object]]:
    """`a -> b`: b reads a's output, so b's key depends on a's restored bytes."""
    return [
        _task("a", cache=cache),
        _task("b", depends_on=("a",), inputs=("out/a.md",), cache=cache),
    ]


def write_workspace(
    root: Path, tasks: list[dict[str, object]], *, default_cache: bool | None = True
) -> list[str]:
    """Write instructions + workflow + reposets + agents under *root*; return the CLI args."""
    (root / "instructions").mkdir(exist_ok=True)
    for t in tasks:
        (root / str(t["instruction"])).write_text(f"do {t['id']}\n", encoding="utf-8")
    defaults: dict[str, object] = {}
    if default_cache is not None:
        defaults["cache"] = default_cache
    (root / "wf.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "id": "e2e-cache",
                "repo_set": "rs",
                "defaults": defaults,
                "tasks": tasks,
            }
        ),
        encoding="utf-8",
    )
    (root / "rs.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "repo_sets": {
                    "rs": {
                        "workspace_root": str(root),
                        "repos": [{"id": "core", "path": ".", "role": "primary"}],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "ag.json").write_text(
        json.dumps({"version": "1.0", "agents": {"ag": {"executor": "fake"}}}), encoding="utf-8"
    )
    return [
        "--workflow",
        str(root / "wf.json"),
        "--reposets",
        str(root / "rs.json"),
        "--agents",
        str(root / "ag.json"),
    ]


def ao_run(args: list[str], *extra: str) -> Any:
    return runner.invoke(app, ["run", *args, *extra])


def ao_resume(run_id: str, args: list[str], *extra: str) -> Any:
    return runner.invoke(app, ["resume", "--run-id", run_id, *args, *extra])


def run_ids(ws: Path) -> list[str]:
    return sorted(p.name for p in (ws / ".orchestrator" / "runs").iterdir())


def status_of(ws: Path, run_id: str) -> dict[str, Any]:
    path = ws / ".orchestrator" / "runs" / run_id / "status.json"
    doc: dict[str, Any] = json.loads(path.read_text("utf-8"))
    return doc


def task_status(snapshot: dict[str, Any], task_id: str) -> dict[str, Any]:
    return next(t for t in snapshot["tasks"] if t["id"] == task_id)


def lines(text: str, prefix: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.startswith(prefix)]


def banners(res: Any) -> list[str]:
    return lines(res.stderr, BANNER_PREFIX)


def warnings(res: Any) -> list[str]:
    return lines(res.stderr, "WARNING:")


def cache_dir(ws: Path) -> Path:
    return ws / ".orchestrator" / "cache"


def outputs_of(ws: Path, *task_ids: str) -> dict[str, bytes]:
    return {t: (ws / "out" / f"{t}.md").read_bytes() for t in task_ids}


def delete_outputs(ws: Path) -> None:
    shutil.rmtree(ws / "out")


def has_key_anywhere(doc: object, key: str) -> bool:
    if isinstance(doc, dict):
        return key in doc or any(has_key_anywhere(v, key) for v in doc.values())
    if isinstance(doc, list):
        return any(has_key_anywhere(v, key) for v in doc)
    return False


def write_config(ws: Path, body: str) -> None:
    (ws / ".ao").mkdir(exist_ok=True)
    (ws / ".ao" / "config.yaml").write_text(body, encoding="utf-8")


def two_runs(ws: Path, args: list[str], *extra: str) -> tuple[Any, Any]:
    """Run, delete the outputs, run again (same flags); both must exit 0."""
    first = ao_run(args, *extra)
    assert first.exit_code == 0, first.output
    delete_outputs(ws)
    second = ao_run(args, *extra)
    assert second.exit_code == 0, second.output
    return first, second


# --------------------------------------------------------------------------------------------
# E-1
# --------------------------------------------------------------------------------------------
class TestE1SecondRunIsAllHits:
    """E-1 (FR-5, FR-11): `ao run --cache` twice with the outputs deleted in between."""

    def test_second_run_makes_zero_dispatches_and_prints_the_hit_line(
        self, tmp_path: Path, dispatches: Dispatches
    ) -> None:
        args = write_workspace(tmp_path, chain())
        first = ao_run(args, "--cache")
        assert first.exit_code == 0, first.output
        assert dispatches.calls == ["a", "b"]  # run 1 really paid for both
        stored = outputs_of(tmp_path, "a", "b")
        assert stored == {
            "a": b"a written by dispatch 1\n",
            "b": b"b written by dispatch 1\n",
        }
        assert lines(first.stdout, SUMMARY_PREFIX) == [
            "Result cache: hits=0 (saved ~$0.0000 est., ~0 tokens, ~0s) "
            "would_hits=0 misses=2 stored=2 ineligible=0"
        ]
        delete_outputs(tmp_path)

        second = ao_run(args, "--cache")
        assert second.exit_code == 0, second.output
        # zero executor calls in the second run: the counter did not move
        assert dispatches.calls == ["a", "b"]
        # the restored bytes are the stored ones (a re-dispatch would say "dispatch 2")
        assert outputs_of(tmp_path, "a", "b") == stored
        hit_lines = lines(second.stdout, SUMMARY_PREFIX)
        assert len(hit_lines) == 1
        expected = (
            rf"Result cache: hits=2 \(saved ~\${2 * COST_USD:.4f} est\., "
            rf"~{2 * SAVED_TOKENS_PER_TASK} tokens, ~\d+s\) "
            r"would_hits=0 misses=0 stored=0 ineligible=0"
        )
        assert re.fullmatch(expected, hit_lines[0]), hit_lines[0]
        # printed after `Total cost:` (HLD 8.8.3), and the run total really is zero
        assert second.stdout.index("Total cost:") < second.stdout.index(SUMMARY_PREFIX)
        assert "Total cost:   $0.0000" in second.stdout

    def test_status_json_and_usage_report_describe_the_hits(
        self, tmp_path: Path, dispatches: Dispatches
    ) -> None:
        args = write_workspace(tmp_path, chain())
        two_runs(tmp_path, args, "--cache")
        first_id, second_id = run_ids(tmp_path)
        snap = status_of(tmp_path, second_id)
        assert snap["status"] == "succeeded"
        for tid in ("a", "b"):
            ts = task_status(snap, tid)
            assert ts["status"] == "succeeded"
            assert ts["attempts"] == 0  # a first-pass hit never dispatches
            view = ts["result_cache"]
            assert view["hit"] is True and view["outcome"] == "hit"
            assert len(view["key"]) == KEY_HEX_LENGTH
            assert view["saved_cost_usd"] == COST_USD
            assert view["saved_tokens"] == SAVED_TOKENS_PER_TASK
            assert view["source_run_id"] == first_id
        block = snap["result_cache"]
        assert (block["hits"], block["lookups"], block["misses"]) == (2, 2, 0)
        assert block["saved_cost_usd"] == pytest.approx(2 * COST_USD)
        assert snap["usage_totals"]["cost_usd"] == 0.0  # a hit spends nothing (FR-9)
        # run 1 stored: its own block counts misses and stores, no hits
        first_block = status_of(tmp_path, first_id)["result_cache"]
        assert (first_block["hits"], first_block["misses"], first_block["stored"]) == (0, 2, 2)

        usage = runner.invoke(app, ["report-usage", "--workspace", str(tmp_path), "--json"])
        assert usage.exit_code == 0, usage.output
        agg = json.loads(usage.stdout)["result_cache"]
        assert (agg["hits"], agg["lookups"]) == (2, 4)
        assert agg["saved_cost_usd"] == pytest.approx(2 * COST_USD)

    def test_a_hand_edited_prior_output_changes_the_key_and_forces_a_dispatch(
        self, tmp_path: Path, dispatches: Dispatches
    ) -> None:
        """Control for the claim above: the prior content of a declared output is part of the key
        (D6), so a leftover file that is not the stored one is a miss, and b's input then differs
        too. The hits of the plain second run are therefore real key matches, not a free pass."""
        args = write_workspace(tmp_path, chain())
        two_runs(tmp_path, args, "--cache")
        assert dispatches.calls == ["a", "b"]
        delete_outputs(tmp_path)
        (tmp_path / "out").mkdir()
        (tmp_path / "out" / "a.md").write_text("tampered by hand\n", encoding="utf-8")
        third = ao_run(args, "--cache")
        assert third.exit_code == 0, third.output
        assert dispatches.calls == ["a", "b", "a", "b"]
        assert outputs_of(tmp_path, "a", "b") == {
            "a": b"a written by dispatch 2\n",
            "b": b"b written by dispatch 2\n",
        }
        assert lines(third.stdout, SUMMARY_PREFIX)[0].startswith("Result cache: hits=0 ")

    def test_companion_negative_cache_off_dispatches_both_tasks_again(
        self, tmp_path: Path, dispatches: Dispatches
    ) -> None:
        """Same workflow and the same sequence without `--cache`: 2 dispatches per run."""
        args = write_workspace(tmp_path, chain())
        first, second = two_runs(tmp_path, args)
        assert dispatches.calls == ["a", "b", "a", "b"]  # 2 on the second run, vs 0 with --cache
        assert outputs_of(tmp_path, "a", "b") == {
            "a": b"a written by dispatch 2\n",
            "b": b"b written by dispatch 2\n",
        }
        for res in (first, second):
            assert "result cache" not in (res.stdout + res.stderr).lower()
        assert not cache_dir(tmp_path).exists()
        for rid in run_ids(tmp_path):
            assert not has_key_anywhere(status_of(tmp_path, rid), "result_cache")


# --------------------------------------------------------------------------------------------
# E-2
# --------------------------------------------------------------------------------------------
_MODULE_SCRIPT = textwrap.dedent(
    """
    import json, os, sys, tempfile
    from pathlib import Path

    mode = sys.argv[1]
    if mode == "golden":  # the ONE capture script of the I-2 golden, in this very process
        from tests.cache import _noop_capture
        rc = _noop_capture.capture(sys.argv[2], Path(sys.argv[3]))
        assert rc == 0, rc
    else:  # a cache-ON run through the real CLI: the positive control
        from typer.testing import CliRunner
        from agent_orchestrator.cli import app
        from tests.cache._wiring_fixture import write_fixture
        with tempfile.TemporaryDirectory() as d:
            os.environ["AO_WORKSPACE_ROOT"] = d
            os.chdir(d)
            res = CliRunner().invoke(app, ["run", *write_fixture(Path(d)), "--cache"])
            assert res.exit_code == 0, res.output
    loaded = [m for m in sys.modules if m.startswith("agent_orchestrator.cache")]
    print("REPORT=" + json.dumps(sorted(loaded)))
    """
)


def _subprocess_cache_modules(*args: str) -> list[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AO_")}
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SRC_DIR), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
    )
    proc = subprocess.run(
        [sys.executable, "-c", _MODULE_SCRIPT, *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-1500:]
    report = next(ln for ln in proc.stdout.splitlines() if ln.startswith("REPORT="))
    modules: list[str] = json.loads(report[len("REPORT=") :])
    return modules


class TestE2PlainRun:
    """E-2 (NFR-1, FR-1): a run with no cache setting is exactly the pre-epic run."""

    def test_plain_run_leaves_no_trace_of_the_cache(
        self, tmp_path: Path, dispatches: Dispatches
    ) -> None:
        # the workflow DOES opt in; the operator never enabled the layer, so nothing happens
        args = write_workspace(tmp_path, chain())
        res = ao_run(args)
        assert res.exit_code == 0, res.output
        assert dispatches.calls == ["a", "b"]
        assert not cache_dir(tmp_path).exists()
        assert "result cache" not in (res.stdout + res.stderr).lower()
        assert warnings(res) == []
        snap = status_of(tmp_path, run_ids(tmp_path)[-1])
        assert not has_key_anywhere(snap, "result_cache")
        status = runner.invoke(app, ["status", "--run-id", run_ids(tmp_path)[-1]])
        usage = runner.invoke(app, ["report-usage", "--workspace", str(tmp_path)])
        usage_json = runner.invoke(app, ["report-usage", "--workspace", str(tmp_path), "--json"])
        for out in (status, usage):
            assert out.exit_code == 0, out.output
            assert "result cache" not in out.output.lower()
        assert "result_cache" not in json.loads(usage_json.stdout)

    @pytest.mark.parametrize("variant", ["serial", "parallel"])
    def test_plain_run_matches_the_base_golden_and_loads_only_allowed_modules(
        self, tmp_path: Path, variant: str
    ) -> None:
        """stdout and status.json equal the base-commit golden byte for byte (I-2), and the
        interpreter that produced them loaded no cache module outside the allow-list."""
        out_dir = tmp_path / "captured"
        loaded = set(_subprocess_cache_modules("golden", variant, str(out_dir)))
        for name in ("stdout.txt", "status.json"):
            assert (out_dir / name).read_bytes() == (GOLDEN_DIR / variant / name).read_bytes(), name
        assert loaded <= CLI_PATH_ALLOWED_CACHE_MODULES, sorted(
            loaded - CLI_PATH_ALLOWED_CACHE_MODULES
        )
        # non-vacuous: the CLI path does load the settings resolver and the admin group
        assert {"agent_orchestrator.cache.settings", "agent_orchestrator.cache.cli"} <= loaded

    def test_the_module_check_would_notice_a_cache_run(self) -> None:
        """Positive control: a run WITH `--cache` loads the engine-side modules."""
        loaded = set(_subprocess_cache_modules("cache-on"))
        extra = loaded - CLI_PATH_ALLOWED_CACHE_MODULES
        assert {
            "agent_orchestrator.cache.coordinator",
            "agent_orchestrator.cache.store",
            "agent_orchestrator.cache.keys",
        } <= extra


# --------------------------------------------------------------------------------------------
# E-3 / E-4
# --------------------------------------------------------------------------------------------
class TestE3NoCacheWins:
    """E-3 (FR-1): `--no-cache` beats `AO_CACHE` and `.ao/config.yaml`."""

    @pytest.mark.parametrize(
        ("env", "config"),
        [
            ("1", None),
            ("shadow", None),
            (None, "cache:\n  enabled: true\n"),
            (None, 'cache:\n  enabled: true\n  mode: "shadow"\n'),
            ("1", "cache:\n  enabled: true\n"),
        ],
        ids=["env-on", "env-shadow", "config-on", "config-shadow", "env-and-config"],
    )
    def test_no_cache_flag_disables_everything(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        dispatches: Dispatches,
        env: str | None,
        config: str | None,
    ) -> None:
        args = write_workspace(tmp_path, chain())
        if env is not None:
            monkeypatch.setenv("AO_CACHE", env)
        if config is not None:
            write_config(tmp_path, config)
        first, second = two_runs(tmp_path, args, "--no-cache")
        assert dispatches.calls == ["a", "b", "a", "b"]  # the second run paid again
        for res in (first, second):
            assert banners(res) == [] and warnings(res) == []
            assert "result cache" not in (res.stdout + res.stderr).lower()
        assert not cache_dir(tmp_path).exists()
        for rid in run_ids(tmp_path):
            assert not has_key_anywhere(status_of(tmp_path, rid), "result_cache")

    @pytest.mark.parametrize(
        ("env", "config", "mode"),
        [
            ("1", None, "on"),
            (None, "cache:\n  enabled: true\n", "on"),
        ],
        ids=["env-on", "config-on"],
    )
    def test_positive_control_the_same_setting_without_the_flag_hits(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        dispatches: Dispatches,
        env: str | None,
        config: str | None,
        mode: str,
    ) -> None:
        args = write_workspace(tmp_path, chain())
        if env is not None:
            monkeypatch.setenv("AO_CACHE", env)
        if config is not None:
            write_config(tmp_path, config)
        _, second = two_runs(tmp_path, args)
        assert dispatches.calls == ["a", "b"]  # the second run was served from the cache
        assert len(banners(second)) == 1 and banners(second)[0].startswith(f"Result cache: {mode}")
        assert lines(second.stdout, "Result cache: hits=2 ")


@dataclass(frozen=True)
class Row:
    """One precedence case: inputs and the independently stated expectation (HLD 8.1.3)."""

    flag: str | None  # None | "--cache" | "--no-cache"
    env: str | None  # None = unset
    config: str | None  # `.ao/config.yaml` body
    mode: str  # expected: off | on | shadow
    source: str | None  # expected source (None when off: no banner is printed)
    warn: str | None = None  # expected AO_CACHE value named in the single warning


ON_CFG = "cache:\n  enabled: true\n"
SHADOW_CFG = 'cache:\n  enabled: true\n  mode: "shadow"\n'
OFF_CFG = "cache:\n  enabled: false\n"

PRECEDENCE_ROWS = [
    # --- default and the env spellings ------------------------------------------------------
    Row(None, None, None, "off", None),
    Row(None, "", None, "off", None),
    Row(None, "   ", None, "off", None),
    Row(None, "1", None, "on", "env"),
    Row(None, "true", None, "on", "env"),
    Row(None, "yes", None, "on", "env"),
    Row(None, " ON ", None, "on", "env"),
    Row(None, "0", None, "off", None),
    Row(None, "false", None, "off", None),
    Row(None, "no", None, "off", None),
    Row(None, "off", None, "off", None),
    Row(None, "shadow", None, "shadow", "env"),
    Row(None, " Shadow ", None, "shadow", "env"),
    # --- unknown values fail closed, with exactly one warning (`refresh` is deferred) -------
    Row(None, "refresh", None, "off", None, warn="refresh"),
    Row(None, "maybe", None, "off", None, warn="maybe"),
    Row(None, "maybe", ON_CFG, "off", None, warn="maybe"),  # never falls through to config
    # --- config alone ----------------------------------------------------------------------
    Row(None, None, ON_CFG, "on", "config"),
    Row(None, None, SHADOW_CFG, "shadow", "config"),
    Row(None, None, OFF_CFG, "off", None),
    Row(None, None, "cache:\n  mode: shadow\n", "off", None),  # `enabled` unset = off
    # --- env beats config ------------------------------------------------------------------
    Row(None, "0", ON_CFG, "off", None),
    Row(None, "off", SHADOW_CFG, "off", None),
    Row(None, "1", OFF_CFG, "on", "env"),
    Row(None, "shadow", ON_CFG, "shadow", "env"),
    Row(None, "1", SHADOW_CFG, "on", "env"),
    # --- the flag beats everything, and the env is not even consulted (no warning) ---------
    Row("--cache", None, None, "on", "cli"),
    Row("--cache", "0", OFF_CFG, "on", "cli"),
    Row("--cache", "shadow", None, "on", "cli"),
    Row("--cache", "maybe", None, "on", "cli"),
    Row("--no-cache", None, None, "off", None),
    Row("--no-cache", "1", ON_CFG, "off", None),
    Row("--no-cache", "shadow", SHADOW_CFG, "off", None),
    Row("--no-cache", "maybe", ON_CFG, "off", None),
]


def _row_id(row: Row) -> str:
    return f"{row.flag or 'noflag'}|env={row.env!r}|cfg={'-' if row.config is None else 'yes'}"


class TestE4PrecedenceMatrix:
    """E-4 (FR-1): flag > env > config > off, with the env spellings and unknown values.

    Each row is one real `ao run`. The expectation is stated literally from HLD 8.1.3 (it is not
    computed by the resolver under test), and checked on three independent surfaces: the banner,
    the warning lines and whether the engine really ran lookups (status.json).
    """

    @pytest.mark.parametrize("row", PRECEDENCE_ROWS, ids=[_row_id(r) for r in PRECEDENCE_ROWS])
    def test_row(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        dispatches: Dispatches,
        row: Row,
    ) -> None:
        args = write_workspace(tmp_path, chain())
        if row.env is not None:
            monkeypatch.setenv("AO_CACHE", row.env)
        if row.config is not None:
            write_config(tmp_path, row.config)
        res = ao_run(args, *([row.flag] if row.flag else []))
        assert res.exit_code == 0, res.output
        assert dispatches.calls == ["a", "b"]  # a first run always dispatches, whatever the mode
        found = banners(res)
        snap = status_of(tmp_path, run_ids(tmp_path)[-1])
        if row.warn is None:
            assert warnings(res) == []
        else:
            assert warnings(res) == [
                f"WARNING: AO_CACHE={row.warn!r} not recognised (use 1|0|shadow); result cache OFF"
            ]
        if row.mode == "off":
            assert found == []
            assert not cache_dir(tmp_path).exists()
            assert not has_key_anywhere(snap, "result_cache")
            return
        assert len(found) == 1, found
        assert found[0].startswith(
            f"Result cache: {row.mode} (source={row.source}), 2 of 2 static task(s) opted in, at "
        ), found[0]
        # the engine really ran lookups in this mode: two misses, both stored
        block = snap["result_cache"]
        assert (block["lookups"], block["misses"], block["stored"]) == (2, 2, 2)
        assert {task_status(snap, t)["result_cache"]["mode"] for t in ("a", "b")} == {row.mode}

    def test_matrix_covers_every_resolved_state(self) -> None:
        """Guard against a silently shrunk table: all three modes and all three sources appear."""
        assert {r.mode for r in PRECEDENCE_ROWS} == {"off", "on", "shadow"}
        assert {r.source for r in PRECEDENCE_ROWS if r.mode != "off"} == {"cli", "env", "config"}
        assert {r.warn for r in PRECEDENCE_ROWS} >= {"refresh", "maybe"}


# --------------------------------------------------------------------------------------------
# E-5
# --------------------------------------------------------------------------------------------
class TestE5DoubleOptIn:
    """E-5 (FR-2): the operator enables the layer AND the workflow author opts tasks in."""

    def test_operator_on_but_no_task_opts_in(self, tmp_path: Path, dispatches: Dispatches) -> None:
        args = write_workspace(tmp_path, chain(), default_cache=None)
        first, second = two_runs(tmp_path, args, "--cache")
        assert dispatches.calls == ["a", "b", "a", "b"]  # nothing was ever served
        for res in (first, second):
            assert len(banners(res)) == 1
            assert banners(res)[0].startswith(
                "Result cache: on (source=cli), but no task opts in "
                "(set defaults.cache: true or tasks[].cache: true), at "
            )
            assert lines(res.stdout, SUMMARY_PREFIX) == []  # no records, so no summary line
        for rid in run_ids(tmp_path):
            assert not has_key_anywhere(status_of(tmp_path, rid), "result_cache")
        # no entry was stored either (the layer may create its directory; it holds no entry)
        entries = cache_dir(tmp_path) / "entries"
        assert not entries.exists() or not list(entries.rglob("*.json"))

    def test_defaults_false_plus_one_opted_in_task_caches_only_that_task(
        self, tmp_path: Path, dispatches: Dispatches
    ) -> None:
        tasks = [_task("a", cache=True), _task("b"), _task("c", cache=False)]
        args = write_workspace(tmp_path, tasks, default_cache=False)
        first, second = two_runs(tmp_path, args, "--cache")
        # run 1: three dispatches; run 2: ONLY the two non-opted tasks dispatch again
        assert sorted(dispatches.calls) == ["a", "b", "b", "c", "c"]
        assert banners(second)[0].count("1 of 3 static task(s) opted in") == 1
        assert outputs_of(tmp_path, "a") == {"a": b"a written by dispatch 1\n"}  # restored
        assert outputs_of(tmp_path, "b", "c") == {
            "b": b"b written by dispatch 2\n",
            "c": b"c written by dispatch 2\n",
        }
        snap = status_of(tmp_path, run_ids(tmp_path)[-1])
        assert task_status(snap, "a")["result_cache"]["hit"] is True
        assert "result_cache" not in task_status(snap, "b")
        assert "result_cache" not in task_status(snap, "c")
        assert snap["result_cache"]["lookups"] == 1
        assert lines(second.stdout, "Result cache: hits=1 ")
        # run 1 recorded exactly one lookup too: the opted-in task
        assert status_of(tmp_path, run_ids(tmp_path)[0])["result_cache"]["lookups"] == 1

    def test_task_level_false_beats_defaults_true(
        self, tmp_path: Path, dispatches: Dispatches
    ) -> None:
        tasks = [_task("a"), _task("b", cache=False)]
        args = write_workspace(tmp_path, tasks, default_cache=True)
        two_runs(tmp_path, args, "--cache")
        assert sorted(dispatches.calls) == ["a", "b", "b"]


# --------------------------------------------------------------------------------------------
# E-6
# --------------------------------------------------------------------------------------------
class TestE6Resume:
    """E-6 (FR-10): `ao resume --cache` / `--no-cache` after a failure.

    Setup (the same for both flags): run 1 `--cache` stores a, b, c. Run 2 (cache OFF, outputs
    deleted) re-dispatches a and b and then FAILS at c. The resume then differs only in the flag.
    """

    @staticmethod
    def _failed_run(ws: Path, dispatches: Dispatches) -> tuple[list[str], str]:
        args = write_workspace(
            ws, [_task("a"), _task("b", depends_on=("a",)), _task("c", depends_on=("b",))]
        )
        first = ao_run(args, "--cache")
        assert first.exit_code == 0, first.output
        assert dispatches.calls == ["a", "b", "c"]
        delete_outputs(ws)
        dispatches.failing.add("c")
        second = ao_run(args)  # cache OFF: pays again for a and b, fails at c
        assert second.exit_code != 0, second.output
        assert dispatches.calls == ["a", "b", "c", "a", "b", "c"]
        dispatches.failing.clear()
        failed_run = run_ids(ws)[-1]
        assert status_of(ws, failed_run)["status"] == "failed"
        assert task_status(status_of(ws, failed_run), "c")["status"] == "failed"
        return args, failed_run

    def test_resume_with_cache_serves_the_failed_task_from_the_cache(
        self, tmp_path: Path, dispatches: Dispatches
    ) -> None:
        args, failed_run = self._failed_run(tmp_path, dispatches)
        res = ao_resume(failed_run, args, "--cache")
        assert res.exit_code == 0, res.output
        # c was served by run 1's entry: no new dispatch for ANY task (a, b stay succeeded)
        assert dispatches.calls == ["a", "b", "c", "a", "b", "c"]
        assert len(banners(res)) == 1 and banners(res)[0].startswith(
            "Result cache: on (source=cli)"
        )
        assert (tmp_path / "out" / "c.md").read_bytes() == b"c written by dispatch 1\n"
        snap = status_of(tmp_path, failed_run)  # resume continues the SAME run
        assert snap["status"] == "succeeded"
        assert {t["status"] for t in snap["tasks"]} == {"succeeded"}
        view = task_status(snap, "c")["result_cache"]
        assert view["hit"] is True and view["saved_cost_usd"] == COST_USD
        assert "result_cache" not in task_status(snap, "a")  # a, b ran cache-off: no record
        assert snap["result_cache"]["hits"] == 1
        assert lines(res.stdout, "Result cache: hits=1 ")

    def test_resume_with_no_cache_redispatches_the_failed_task(
        self, tmp_path: Path, dispatches: Dispatches
    ) -> None:
        args, failed_run = self._failed_run(tmp_path, dispatches)
        res = ao_resume(failed_run, args, "--no-cache")
        assert res.exit_code == 0, res.output
        assert dispatches.calls == ["a", "b", "c", "a", "b", "c", "c"]  # only c ran again
        assert banners(res) == [] and warnings(res) == []
        assert (tmp_path / "out" / "c.md").read_bytes() == b"c written by dispatch 3\n"
        snap = status_of(tmp_path, failed_run)
        assert snap["status"] == "succeeded"
        assert not has_key_anywhere(snap, "result_cache")
        assert "result cache" not in res.output.lower()

    def test_resume_never_redispatches_a_restored_task(
        self, tmp_path: Path, dispatches: Dispatches
    ) -> None:
        """A task restored in this run is still `succeeded` after `ao resume`; its record stays
        current and it is not dispatched (FR-10)."""
        args = write_workspace(
            tmp_path, [_task("a"), _task("b", depends_on=("a",)), _task("c", depends_on=("b",))]
        )
        assert ao_run(args, "--cache").exit_code == 0
        delete_outputs(tmp_path)
        dispatches.failing.add("c")
        # c is the only task that cannot hit: opt it out so the second run fails there
        wf = json.loads((tmp_path / "wf.json").read_text("utf-8"))
        wf["tasks"][2]["cache"] = False
        (tmp_path / "wf.json").write_text(json.dumps(wf), encoding="utf-8")
        failed = ao_run(args, "--cache")
        assert failed.exit_code != 0, failed.output
        assert dispatches.calls == ["a", "b", "c", "c"]  # a, b were hits in the failed run
        dispatches.failing.clear()
        run_id = run_ids(tmp_path)[-1]
        before = status_of(tmp_path, run_id)
        assert task_status(before, "a")["result_cache"]["hit"] is True

        res = ao_resume(run_id, args, "--cache")
        assert res.exit_code == 0, res.output
        assert dispatches.calls == ["a", "b", "c", "c", "c"]  # only the failed c ran again
        after = status_of(tmp_path, run_id)
        assert after["status"] == "succeeded"
        for tid in ("a", "b"):
            assert task_status(after, tid)["status"] == "succeeded"
            assert task_status(after, tid)["attempts"] == 0
            assert task_status(after, tid)["result_cache"]["hit"] is True  # record still current
        assert after["result_cache"]["hits"] == 2
        assert lines(res.stdout, "Result cache: hits=2 ")
