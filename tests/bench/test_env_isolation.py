"""T-Ws7Iso: environment isolation of the agent under test (fake tier: no LLM, no network).

Covers the four checks in `bench/envcheck.py` and their wiring:
  1. workspace root outside any instruction tree (`AO_BENCH_WORKSPACE_ROOT`, sibling grading
     root, `run_suite` refusal / `--allow-contaminated-env` stamp);
  2. environment fingerprint from each transcript's `system/init` event, foreign entries vs
     the run's baseline probe, `build_comparison` refusals;
  3. hidden-asset audit -> `suspect` tasks excluded from capability stats;
  4. user-level config fingerprint + operator-session env scrub.

Agent-type subjects are simulated by temporarily registering a scripted `Subject` under the
real `claude_cli` type, which writes a stream-json transcript instead of spawning `claude`;
the baseline probe is injected. The one test that spawns a process uses a shell-script
`claude` shim on PATH.
"""

from __future__ import annotations

import json
import os
import stat
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agent_orchestrator.bench import envcheck
from agent_orchestrator.bench import subjects as subjects_mod
from agent_orchestrator.bench.campaign import STOP_ENV_MISMATCH, run_campaign
from agent_orchestrator.bench.cli import app
from agent_orchestrator.bench.envcheck import (
    EnvFingerprint,
    audit_grading_access,
    find_instruction_ancestors,
    find_transcripts,
    fingerprint_events,
    foreign_entries,
    merge_fingerprints,
    subject_env,
    user_config_fingerprint,
)
from agent_orchestrator.bench.errors import BenchError, EnvMismatchError
from agent_orchestrator.bench.registries import SUBJECT_REGISTRY
from agent_orchestrator.bench.results import build_comparison, write_comparison, write_summary_md
from agent_orchestrator.bench.runner import (
    RUN_JSON_FILENAME,
    BenchRunRecord,
    resolve_result_dir,
    run_suite,
)
from agent_orchestrator.bench.spec import BenchTask, load_subject, load_suite
from agent_orchestrator.bench.subjects import Subject, SubjectResult
from agent_orchestrator.bench.workspace import (
    DEFAULT_BENCH_BASE_DIR,
    ENV_BENCH_WORKSPACE_ROOT,
    GRADING_ROOT_DIRNAME,
    RunContext,
    bench_base_dir,
    bench_grading_root,
    bench_workspace_root,
)

cli = CliRunner()

BASE_SKILLS = ["code-review", "simplify"]
BASE_AGENTS = ["Explore", "general-purpose"]


def _init(skills: list[str], *, agents: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "type": "system",
        "subtype": "init",
        "skills": skills,
        "agents": agents if agents is not None else BASE_AGENTS,
        "slash_commands": ["clear", *skills],
        "mcp_servers": [],
        "plugins": [{"name": "pyright-lsp", "source": "pyright-lsp@official"}],
        "claude_code_version": "9.9.9",
        **extra,
    }


def _tool_use(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": name, "input": payload}]},
    }


def _tool_result(text: str) -> dict[str, Any]:
    return {
        "type": "user",
        "message": {
            "content": [{"type": "tool_result", "content": [{"type": "text", "text": text}]}]
        },
    }


def _write_jsonl(path: Path, events: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    return path


BASELINE = fingerprint_events([_init(BASE_SKILLS)])


def _baseline_probe(**_: Any) -> EnvFingerprint | None:
    return BASELINE


# A behaviour maps (task, ctx) -> the events the scripted agent "emits".
Behaviour = Callable[[BenchTask, RunContext], list[dict[str, Any]]]


def _clean(_task: BenchTask, _ctx: RunContext) -> list[dict[str, Any]]:
    return [_init(BASE_SKILLS)]


def _leaky(_task: BenchTask, _ctx: RunContext) -> list[dict[str, Any]]:
    return [_init([*BASE_SKILLS, "benchmarking"])]


def _peeks_on(task_ids: set[str]) -> Behaviour:
    def _b(task: BenchTask, ctx: RunContext) -> list[dict[str, Any]]:
        events = [_init(BASE_SKILLS)]
        if task.id in task_ids:
            events.append(_tool_use("Bash", {"command": f"cat {ctx.grading_dir}/probes.json"}))
        return events

    return _b


@pytest.fixture()
def scripted_agents(monkeypatch: pytest.MonkeyPatch) -> dict[str, Behaviour]:
    """Register a scripted Subject under the real `claude_cli` type; the returned dict maps
    subject id -> behaviour (default: clean)."""
    behaviours: dict[str, Behaviour] = {}

    class _ScriptedAgent(Subject):
        def run(self, task: BenchTask, ctx: RunContext) -> SubjectResult:
            events = behaviours.get(self.spec.id, _clean)(task, ctx)
            _write_jsonl(Path(ctx.capture_dir) / "transcript.jsonl", events)
            return SubjectResult(
                status="succeeded",
                wall_clock_seconds=1.0,
                cost_usd=0.5,
                capture_dir=ctx.capture_dir,
            )

    monkeypatch.setitem(SUBJECT_REGISTRY, "claude_cli", _ScriptedAgent)
    return behaviours


def _two_task_suite(suite_factory: Callable[..., Path], suite_id: str) -> Path:
    tasks = []
    for tid in ("task-a", "task-b"):
        tasks.append(
            {
                "id": tid,
                "category": "bugfix",
                "instruction": f"tasks/{tid}/instruction.md",
                "fixture": f"tasks/{tid}/fixture",
                "grader": {"type": "pytest", "command": "true", "cwd": "."},
                "timeout_seconds": 60,
            }
        )
    return suite_factory(suite_id=suite_id, tasks=tasks)


def _agent_subject(tmp_path: Path, subject_id: str) -> Path:
    path = tmp_path / f"{subject_id}.json"
    path.write_text(json.dumps({"version": "1.0", "id": subject_id, "type": "claude_cli"}))
    return path


def _run(suite: Path, subject: Path, out: Path, **kw: Any) -> Path:
    record = run_suite(suite, subject, out_dir=out, env_probe=_baseline_probe, **kw)
    return resolve_result_dir(record.bench_run_id, out_dir=out)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# 1. Roots and the instruction-ancestor guard
# ---------------------------------------------------------------------------


def test_default_base_is_var_tmp_per_uid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_BENCH_WORKSPACE_ROOT, raising=False)
    assert bench_base_dir() == DEFAULT_BENCH_BASE_DIR / str(os.getuid())


def test_env_override_and_sibling_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ENV_BENCH_WORKSPACE_ROOT, str(tmp_path / "base"))
    ws, grading = bench_workspace_root(), bench_grading_root()
    assert ws.parent == grading.parent == tmp_path / "base"
    assert grading.name == GRADING_ROOT_DIRNAME
    assert ws not in grading.parents and grading not in ws.parents


@pytest.mark.parametrize("marker", ["CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", ".claude"])
def test_any_instruction_marker_in_an_ancestor_is_found(tmp_path: Path, marker: str) -> None:
    checkout = tmp_path / "checkout"
    target = checkout / "playground" / ".tmp" / "bench"
    target.mkdir(parents=True)
    (checkout / marker).mkdir() if marker == ".claude" else (checkout / marker).write_text("x")
    hits = find_instruction_ancestors(target, config_dir=tmp_path / "nowhere")
    assert hits == [str(checkout / marker)]


def test_clean_root_and_not_yet_existing_root_have_no_hits(tmp_path: Path) -> None:
    assert find_instruction_ancestors(tmp_path / "a" / "b", config_dir=tmp_path / "x") == []


def test_user_config_dir_is_exempt_but_a_home_claude_md_is_not(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    target = home / "bench" / "ws"
    assert find_instruction_ancestors(target, config_dir=home / ".claude") == []
    (home / "CLAUDE.md").write_text("user memory in the home dir is an ancestor file\n")
    assert find_instruction_ancestors(target, config_dir=home / ".claude") == [
        str(home / "CLAUDE.md")
    ]


def _contaminated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    checkout = tmp_path / "checkout"
    (checkout / ".claude" / "skills").mkdir(parents=True)
    (checkout / "CLAUDE.md").write_text("repo instructions\n")
    monkeypatch.setenv(ENV_BENCH_WORKSPACE_ROOT, str(checkout / "playground" / ".tmp"))
    return checkout


def test_run_suite_refuses_contaminated_root_for_agent_subjects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suite_factory: Callable[..., Path],
    scripted_agents: dict[str, Behaviour],
) -> None:
    checkout = _contaminated_root(tmp_path, monkeypatch)
    suite = _two_task_suite(suite_factory, f"iso-{_uid()}")
    with pytest.raises(BenchError, match="instruction files") as exc:
        _run(suite, _agent_subject(tmp_path, "agent"), tmp_path / "res")
    assert str(checkout / "CLAUDE.md") in str(exc.value)
    assert not (tmp_path / "res").exists()  # refused before anything ran or was written


def test_allow_flag_runs_and_stamps_env_contaminated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suite_factory: Callable[..., Path],
    scripted_agents: dict[str, Behaviour],
) -> None:
    _contaminated_root(tmp_path, monkeypatch)
    suite = _two_task_suite(suite_factory, f"iso-{_uid()}")
    run_dir = _run(
        suite, _agent_subject(tmp_path, "agent"), tmp_path / "res", allow_contaminated_env=True
    )
    record = BenchRunRecord.model_validate_json((run_dir / RUN_JSON_FILENAME).read_text())
    assert record.env_contaminated is True
    assert record.env_isolation is not None and record.env_isolation.allowed_by_flag
    with pytest.raises(EnvMismatchError, match="env_contaminated"):
        build_comparison([run_dir])
    assert build_comparison([run_dir], allow_mixed=True).env_mixed is True


def test_fake_subject_on_contaminated_root_runs_and_is_not_stamped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suite_factory: Callable[..., Path]
) -> None:
    _contaminated_root(tmp_path, monkeypatch)
    suite = _two_task_suite(suite_factory, f"iso-{_uid()}")
    subject = tmp_path / "fake.json"
    subject.write_text(json.dumps({"version": "1.0", "id": "fake-noop", "type": "fake"}))
    record = run_suite(suite, subject, out_dir=tmp_path / "res")
    assert record.env_contaminated is False
    assert record.env_isolation is not None and record.env_isolation.instruction_ancestors
    assert record.env_baseline is None and record.user_config is None  # no agent, no probe


def test_grading_assets_live_under_the_sibling_root(
    tmp_path: Path, suite_factory: Callable[..., Path], scripted_agents: dict[str, Behaviour]
) -> None:
    seen: dict[str, str] = {}

    def _record(task: BenchTask, ctx: RunContext) -> list[dict[str, Any]]:
        seen[task.id] = str(ctx.grading_dir)
        return [_init(BASE_SKILLS)]

    scripted_agents["agent"] = _record
    suite = _two_task_suite(suite_factory, f"iso-{_uid()}")
    _run(suite, _agent_subject(tmp_path, "agent"), tmp_path / "res")
    ws_root = bench_workspace_root().resolve()
    for grading_dir in seen.values():
        resolved = Path(grading_dir).resolve()
        assert bench_grading_root().resolve() in resolved.parents
        assert ws_root not in resolved.parents


# ---------------------------------------------------------------------------
# 2. Fingerprint
# ---------------------------------------------------------------------------


def test_fingerprint_reads_names_and_ignores_mcp_status() -> None:
    pending = fingerprint_events(
        [_init(BASE_SKILLS, mcp_servers=[{"name": "drive", "status": "pending"}])]
    )
    connected = fingerprint_events(
        [_init(BASE_SKILLS, mcp_servers=[{"name": "drive", "status": "connected"}])]
    )
    assert pending is not None and connected is not None
    assert pending.mcp_servers == ["drive"] and pending.plugins == ["pyright-lsp@official"]
    assert pending.digest == connected.digest
    assert pending.claude_code_versions == ["9.9.9"] and pending.divergent is False
    assert fingerprint_events([]) is None


def test_fingerprint_union_and_divergence() -> None:
    fp = fingerprint_events([_init(BASE_SKILLS), _init([*BASE_SKILLS, "extra"])])
    assert fp is not None and fp.divergent and "extra" in fp.skills and fp.init_events == 2
    merged = merge_fingerprints([BASELINE, None, fingerprint_events([_init(BASE_SKILLS)])])
    assert merged is not None and merged.digest == BASELINE.digest and not merged.divergent
    assert merged.init_events == 2
    assert merge_fingerprints([None]) is None


def test_auto_memory_flag_comes_from_memory_paths() -> None:
    fp = fingerprint_events([_init(BASE_SKILLS, memory_paths={"auto": "/x/memory/"})])
    assert fp is not None and fp.auto_memory is True


def test_foreign_entries_are_what_the_baseline_lacked() -> None:
    observed = fingerprint_events([_init([*BASE_SKILLS, "repo-intel"], agents=["architect"])])
    assert foreign_entries(observed, BASELINE) == {
        "agents": ["architect"],
        "skills": ["repo-intel"],
        "slash_commands": ["repo-intel"],
    }
    assert foreign_entries(observed, None) == {}


def test_find_transcripts_covers_claude_and_ao_layouts(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    a = _write_jsonl(ws / "capture" / "transcript.jsonl", [_init(BASE_SKILLS)])
    b = _write_jsonl(
        ws / ".orchestrator" / "runs" / "r1" / "implement" / "attempt-1" / "transcript.jsonl",
        [_init(BASE_SKILLS)],
    )
    c = _write_jsonl(
        ws / ".orchestrator" / "runs" / "r1" / "verify" / "attempt-2" / "transcript.jsonl", []
    )
    assert find_transcripts(ws) == sorted([a, b, c])
    assert find_transcripts(tmp_path / "missing") == []


def test_task_record_carries_fingerprint_and_foreign_entries(
    tmp_path: Path, suite_factory: Callable[..., Path], scripted_agents: dict[str, Behaviour]
) -> None:
    scripted_agents["leaky"] = _leaky
    suite = _two_task_suite(suite_factory, f"iso-{_uid()}")
    run_dir = _run(suite, _agent_subject(tmp_path, "leaky"), tmp_path / "res")
    record = BenchRunRecord.model_validate_json((run_dir / RUN_JSON_FILENAME).read_text())
    assert record.env_baseline is not None and record.env_baseline.digest == BASELINE.digest
    assert record.user_config is not None and record.user_config.digest
    for task in record.tasks:
        assert task.env_fingerprint is not None and "benchmarking" in task.env_fingerprint.skills
        assert task.env_foreign == {"skills": ["benchmarking"], "slash_commands": ["benchmarking"]}
        assert task.suspect is False
    summary = write_summary_md(record, run_dir).read_text()
    assert "## Environment isolation" in summary and "foreign entries" in summary


# ---------------------------------------------------------------------------
# Comparison refusals
# ---------------------------------------------------------------------------


def _pair(
    tmp_path: Path,
    suite_factory: Callable[..., Path],
    scripted_agents: dict[str, Behaviour],
    a: Behaviour,
    b: Behaviour,
) -> list[Path]:
    suite = _two_task_suite(suite_factory, f"cmp-{_uid()}")
    scripted_agents["subj-a"], scripted_agents["subj-b"] = a, b
    out = tmp_path / "res"
    return [
        _run(suite, _agent_subject(tmp_path, "subj-a"), out),
        _run(suite, _agent_subject(tmp_path, "subj-b"), out),
    ]


def test_clean_shared_environment_compares(
    tmp_path: Path, suite_factory: Callable[..., Path], scripted_agents: dict[str, Behaviour]
) -> None:
    dirs = _pair(tmp_path, suite_factory, scripted_agents, _clean, _clean)
    cmp = build_comparison(dirs)
    assert cmp.env_mixed is False and cmp.env_problems == []
    assert cmp.env_by_subject["subj-a"].fingerprint_digest == BASELINE.digest
    assert cmp.env_by_subject["subj-a"].foreign == {}


def test_foreign_entries_refuse_the_comparison(
    tmp_path: Path, suite_factory: Callable[..., Path], scripted_agents: dict[str, Behaviour]
) -> None:
    dirs = _pair(tmp_path, suite_factory, scripted_agents, _clean, _leaky)
    with pytest.raises(EnvMismatchError, match="absent from the baseline"):
        build_comparison(dirs)
    cmp = build_comparison(dirs, allow_mixed=True)
    assert cmp.env_mixed is True
    assert cmp.env_by_subject["subj-b"].foreign["skills"] == ["benchmarking"]
    json_path, md_path = write_comparison(cmp, tmp_path / "cmp")
    md = md_path.read_text()
    assert "NON-ISOLATED" in md and "benchmarking" in md


def test_differing_fingerprints_between_subjects_refuse(
    tmp_path: Path, suite_factory: Callable[..., Path], scripted_agents: dict[str, Behaviour]
) -> None:
    def _fewer(_t: BenchTask, _c: RunContext) -> list[dict[str, Any]]:
        return [_init(BASE_SKILLS[:1])]  # a subset of the baseline: not foreign, but different

    dirs = _pair(tmp_path, suite_factory, scripted_agents, _clean, _fewer)
    with pytest.raises(EnvMismatchError, match="init-event fingerprint"):
        build_comparison(dirs)


def test_legacy_agent_run_mixed_with_fingerprinted_is_refused(
    tmp_path: Path, suite_factory: Callable[..., Path], scripted_agents: dict[str, Behaviour]
) -> None:
    dirs = _pair(tmp_path, suite_factory, scripted_agents, _clean, _clean)
    legacy = json.loads((dirs[1] / RUN_JSON_FILENAME).read_text())
    for key in ("env_contaminated", "env_isolation", "env_baseline", "user_config"):
        legacy.pop(key)
    (dirs[1] / RUN_JSON_FILENAME).write_text(json.dumps(legacy))
    with pytest.raises(EnvMismatchError, match="predates environment fingerprinting"):
        build_comparison(dirs)
    # Legacy-only comparisons (e.g. regenerating an old report) still work.
    assert build_comparison([dirs[1]]).env_by_subject["subj-b"].legacy is True


# ---------------------------------------------------------------------------
# 3. Hidden-asset audit
# ---------------------------------------------------------------------------


def test_audit_flags_tool_use_and_counts_tool_result_exposure(tmp_path: Path) -> None:
    transcript = _write_jsonl(
        tmp_path / "t.jsonl",
        [
            _init(BASE_SKILLS),
            _tool_use("Bash", {"command": "ls ../../.."}),
            _tool_result(f"ws\n{GRADING_ROOT_DIRNAME}\n"),
            _tool_use("Read", {"file_path": f"/x/{GRADING_ROOT_DIRNAME}/r/s/t/probes.json"}),
        ],
    )
    (tmp_path / "junk.jsonl").write_text("not json\n{broken\n")
    audit = audit_grading_access([transcript, tmp_path / "junk.jsonl"], [GRADING_ROOT_DIRNAME])
    assert audit.accessed and len(audit.tool_use_hits) == 1
    assert audit.tool_use_hits[0].startswith("Read: ")
    assert audit.tool_result_mentions == 1 and audit.transcripts_scanned == 2


def test_audit_has_no_false_positive_on_ordinary_work(tmp_path: Path) -> None:
    transcript = _write_jsonl(
        tmp_path / "t.jsonl", [_tool_use("Bash", {"command": "uv run pytest -q tests/grading"})]
    )
    assert not audit_grading_access([transcript], [GRADING_ROOT_DIRNAME]).accessed


def test_grading_access_marks_task_suspect_and_excludes_it_from_stats(
    tmp_path: Path, suite_factory: Callable[..., Path], scripted_agents: dict[str, Behaviour]
) -> None:
    dirs = _pair(tmp_path, suite_factory, scripted_agents, _clean, _peeks_on({"task-a"}))
    record = BenchRunRecord.model_validate_json((dirs[1] / RUN_JSON_FILENAME).read_text())
    by_id = {t.task_id: t for t in record.tasks}
    assert by_id["task-a"].suspect and by_id["task-a"].suspect_reason == "grading_access"
    assert by_id["task-a"].grading_audit is not None
    assert not by_id["task-b"].suspect

    cmp = build_comparison(dirs)  # suspects are excluded, never a refusal
    stats_b = cmp.subject_stats["subj-b"]
    assert stats_b is not None and stats_b.attempted == 1 and stats_b.tasks_attempted == 1
    assert cmp.matrix["task-a"]["subj-b"] is not None and cmp.matrix["task-a"]["subj-b"].n == 0
    (pair,) = cmp.pairwise
    assert pair.n_tasks == 1  # task-a dropped from the paired test
    assert cmp.env_by_subject["subj-b"].grading_access_task_ids == ["task-a"]


# ---------------------------------------------------------------------------
# 4. User config + subject env
# ---------------------------------------------------------------------------


def test_subject_env_scrubs_operator_session_and_applies_overrides() -> None:
    base = {
        "PATH": "/bin",
        "CLAUDECODE": "1",
        "CLAUDE_CODE_CHILD_SESSION": "1",
        "CLAUDE_CODE_SESSION_ID": "abc",
        "CLAUDE_CODE_MESSAGING_SOCKET": "/s",
        "CLAUDE_EFFORT": "high",
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "1000",  # operator CONFIG: kept (and fingerprinted)
    }
    env = subject_env({"ENABLE_CLAUDEAI_MCP_SERVERS": "false"}, base=base)
    assert env == {
        "PATH": "/bin",
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "1000",
        "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
    }


def test_user_config_fingerprint_records_keys_redacts_secrets_and_is_path_independent(
    tmp_path: Path,
) -> None:
    def _cfg(name: str) -> Path:
        cfg = tmp_path / name
        cfg.mkdir()
        (cfg / "settings.json").write_text(
            json.dumps(
                {
                    "model": "opus",
                    "effortLevel": "xhigh",
                    "enabledPlugins": {"p@x": True},
                    "theme": "dark",
                }
            )
        )
        (cfg / "CLAUDE.md").write_text("user memory\n")
        return cfg

    env = {"ANTHROPIC_API_KEY": "sk-secret", "CLAUDE_CODE_EFFORT_LEVEL": "low", "HOME": "/h"}
    one = user_config_fingerprint({**env, "CLAUDE_CONFIG_DIR": str(_cfg("one"))})
    two = user_config_fingerprint({**env, "CLAUDE_CONFIG_DIR": str(_cfg("two"))})
    assert one.settings["model"] == "opus" and one.settings["effortLevel"] == "xhigh"
    assert "enabledPlugins_sha256" in one.settings and "theme" not in one.settings
    assert one.env == {"ANTHROPIC_API_KEY": "<set>", "CLAUDE_CODE_EFFORT_LEVEL": "low"}
    assert one.user_claude_md_sha256 is not None
    assert one.digest == two.digest  # same content, different config-dir path
    changed = user_config_fingerprint({**env, "CLAUDE_CODE_EFFORT_LEVEL": "high"})
    assert changed.digest != one.digest


def test_claude_cli_subject_spawns_with_scrubbed_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def _fake_run(argv: list[str], **kw: Any) -> tuple[int, bool]:
        captured.update(kw)
        Path(kw["stdout_path"]).write_text("")
        Path(kw["stderr_path"]).write_text("")
        return 0, False

    monkeypatch.setattr(subjects_mod, "_run_with_timeout", _fake_run)
    monkeypatch.setattr(subjects_mod.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "operator-session")
    spec = load_subject(_agent_subject(tmp_path, "real-claude"))
    ctx = RunContext(
        workspace=str(tmp_path),
        repo_dir=str(tmp_path),
        instruction_path=str(tmp_path / "I.md"),
        capture_dir=str(tmp_path / "cap"),
        timeout_seconds=5,
        env={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"},
    )
    subjects_mod.ClaudeCliSubject(spec).run(load_suite(_suite_for_task(tmp_path)).tasks[0], ctx)
    assert "CLAUDECODE" not in captured["env"]
    assert "CLAUDE_CODE_SESSION_ID" not in captured["env"]
    assert captured["env"]["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"


def _suite_for_task(tmp_path: Path) -> Path:
    (tmp_path / "s" / "fx").mkdir(parents=True)
    (tmp_path / "s" / "i.md").write_text("x\n")
    suite = tmp_path / "s" / "suite.json"
    suite.write_text(
        json.dumps(
            {
                "version": "1.0",
                "id": "one",
                "domain": "software",
                "tasks": [
                    {
                        "id": "t",
                        "category": "bugfix",
                        "instruction": "i.md",
                        "fixture": "fx",
                        "grader": {"type": "pytest", "command": "true", "cwd": "."},
                    }
                ],
            }
        )
    )
    return suite


def _claude_shim(bin_dir: Path, init_event: dict[str, Any]) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "claude"
    shim.write_text(f"#!/bin/sh\necho '{json.dumps(init_event)}'\nexec sleep 30\n")
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)


def test_probe_reads_init_event_and_kills_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _claude_shim(tmp_path / "bin", _init(BASE_SKILLS))
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    fp = envcheck.probe_env_baseline(cwd=tmp_path, env=dict(os.environ), timeout_seconds=10)
    assert fp is not None and fp.digest == BASELINE.digest


def test_probe_is_none_without_claude(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(envcheck.shutil, "which", lambda _: None)
    assert envcheck.probe_env_baseline(cwd=tmp_path, env={}) is None


# ---------------------------------------------------------------------------
# Campaign + CLI (outer boundary)
# ---------------------------------------------------------------------------


def test_campaign_stops_repeats_on_env_mismatch_and_stamps_the_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suite_factory: Callable[..., Path],
    scripted_agents: dict[str, Behaviour],
) -> None:
    import agent_orchestrator.bench.runner as runner_mod

    monkeypatch.setattr(runner_mod, "probe_env_baseline", _baseline_probe)
    suite = _two_task_suite(suite_factory, f"camp-{_uid()}")
    scripted_agents["c-a"], scripted_agents["c-b"] = _clean, _leaky
    result = run_campaign(
        suite,
        [_agent_subject(tmp_path, "c-a"), _agent_subject(tmp_path, "c-b")],
        out_dir=tmp_path / "res",
        repeats=3,
    )
    assert result.record.stage_stop_reason == STOP_ENV_MISMATCH
    assert result.record.repeats_done == 1
    assert result.record.env_problems and result.comparison is not None
    assert result.comparison.env_mixed is True


def test_cli_run_refuses_contaminated_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suite_factory: Callable[..., Path]
) -> None:
    _contaminated_root(tmp_path, monkeypatch)
    suite = _two_task_suite(suite_factory, f"cli-{_uid()}")
    res = cli.invoke(
        app,
        [
            "run",
            "--suite",
            str(suite),
            "--subject",
            str(_agent_subject(tmp_path, "agent")),
            "--out-dir",
            str(tmp_path / "res"),
        ],
    )
    assert res.exit_code == 1 and "--allow-contaminated-env" in res.output


def test_cli_env_preflight_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clean = cli.invoke(app, ["env"])
    assert clean.exit_code == 0, clean.output
    payload = json.loads(clean.output)
    assert payload["contaminated"] is False
    assert payload["grading_root"].endswith(GRADING_ROOT_DIRNAME)
    assert payload["user_config"]["digest"]

    checkout = _contaminated_root(tmp_path, monkeypatch)
    dirty = cli.invoke(app, ["env"])
    assert dirty.exit_code == 2
    assert str(checkout / "CLAUDE.md") in json.loads(dirty.output)["instruction_ancestors"]


def test_cli_report_refuses_env_mismatch(
    tmp_path: Path, suite_factory: Callable[..., Path], scripted_agents: dict[str, Behaviour]
) -> None:
    dirs = _pair(tmp_path, suite_factory, scripted_agents, _clean, _leaky)
    args = ["report", *[a for d in dirs for a in ("--run-dir", str(d))]]
    args += ["--out-dir", str(tmp_path / "rep")]
    refused = cli.invoke(app, args)
    assert refused.exit_code == 1 and "T-Ws7Iso" in refused.output
    allowed = cli.invoke(app, [*args, "--allow-mixed"])
    assert allowed.exit_code == 0, allowed.output
