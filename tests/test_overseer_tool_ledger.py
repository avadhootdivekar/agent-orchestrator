"""Tests for `overseer_tool.py`'s ledger/chain/path-confinement/charter-lock/determinism (M1).

Ticket: `meta/tickets/E-YAAGhk-overseer-runner-template/T-ABDjSj-tool-state-ledger-budget/`.
Covers AC3 (ledger idempotency + chain integrity + breadcrumb synthesis/mismatch), AC6 (path
confinement), AC9 (byte-identical `digest.json` across two `ckpt-prep` runs).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TOOL_PATH = (
    _REPO_ROOT
    / "src"
    / "agent_orchestrator"
    / "templates"
    / "builtin"
    / "overseer-runner"
    / "tools"
    / "overseer_tool.py"
)


def _load_overseer_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("overseer_tool_under_test_ledger", _TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ov = _load_overseer_tool()

_NOW = "2026-01-01T00:00:00+00:00"


# =============================================================================================
# ===== Fixture helpers ========================================================================
# =============================================================================================


def make_config_dict(**overrides: object) -> dict:
    base: dict = {
        "schema": "ao.overseer.config/v1",
        "converge_pct": 80,
        "stabilize_pct": 90,
        "closeout_pct": 95,
        "wave_size": 6,
        "max_waves": 12,
        "wave_max_minutes": 90,
        "max_attempts_per_item": 3,
        "max_expanders_per_wave": 0,
        "max_injected_tasks": 160,
        "final_push": True,
        "overseer_effort": "high",
        "overseer_model": "",
        "python_bin": "python3",
        "run_budget_usd": 2000,
        "task_budget_usd": 75,
        "stall_waves": 2,
        "stabilize_wave_size": 4,
        "max_stabilize_passes": 2,
        "sub_wave_size": 4,
        "default_unit_cost_usd": 8,
        "default_ckpt_cost_usd": 5,
        "runs_root": ".orchestrator/runs",
        "contract_version": 1,
        "kind_map": {},
    }
    base.update(overrides)
    return base


def write_config(inst: Path, **overrides: object) -> None:
    inst.mkdir(parents=True, exist_ok=True)
    (inst / "overseer-config.json").write_text(json.dumps(make_config_dict(**overrides)))


def write_brief(inst: Path, k: int, unit_id: str, **fields: object) -> None:
    path = ov.brief_path(inst, k, unit_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema": "ao.overseer.brief/v1",
        "unit_id": unit_id,
        "wave": k,
        "ask_ids": ["A1"],
        "work_item": "A1/parser",
        "kind": "implement",
        "goal": "do the thing",
        "acceptance": ["it works"],
    }
    data.update(fields)
    path.write_text(json.dumps(data))


def write_breadcrumb(
    inst: Path, unit_id: str, *, file_unit_id: str | None = None, **fields: object
) -> None:
    path = ov.breadcrumb_path(inst, unit_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema": "ao.overseer.breadcrumb/v1",
        "unit_id": file_unit_id if file_unit_id is not None else unit_id,
        "outcome": "done",
        "verdict": "pass",
        "summary": "did the thing",
        "changed_paths": [],
        "needs_input": False,
    }
    data.update(fields)
    path.write_text(json.dumps(data))


def build_state_dict(tasks: dict[str, dict], **overrides: object) -> dict:
    base: dict = {
        "run_id": "run-1",
        "workflow_id": "wf-1",
        "repo_set": "main",
        "started_at": _NOW,
        "updated_at": _NOW,
        "status": "running",
        "tasks": tasks,
        "injected_tasks": [{"id": tid} for tid in tasks],
        "breaker_overrides": {},
    }
    base.update(overrides)
    return base


def make_state(tasks: dict[str, dict], **overrides: object):
    return ov.State(
        tasks={
            tid: ov.TaskInfo(
                status=t.get("status", "succeeded"),
                attempts=t.get("attempts", 1),
                started_at=t.get("started_at"),
                ended_at=t.get("ended_at"),
                cumulative_cost_usd=t.get("cumulative_cost_usd", 0.0),
            )
            for tid, t in tasks.items()
        },
        injected_ids=list(tasks.keys()),
        breaker_overrides={},
        spent=sum(t.get("cumulative_cost_usd", 0.0) for t in tasks.values()),
        status=overrides.get("status", "running"),
    )


# =============================================================================================
# ===== AC3: ledger idempotency + hash-chain integrity + breadcrumb handling ==================
# =============================================================================================


def test_ingest_ledger_idempotent_zero_new_lines_on_rerun(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    now = ov.parse_now(_NOW)
    write_brief(inst, 1, "w01-01-a")
    write_breadcrumb(inst, "w01-01-a")
    state = make_state({"w01-01-a": {"status": "succeeded", "cumulative_cost_usd": 5.0}})

    appended_first = ov.ingest_ledger(inst, ["w01-01-a"], state, 1, now)
    assert appended_first == 1
    appended_second = ov.ingest_ledger(inst, ["w01-01-a"], state, 1, now)
    assert appended_second == 0

    unit_lines = [line for line in ov.read_ledger_lines(inst) if line["type"] == "unit"]
    assert len(unit_lines) == 1
    assert unit_lines[0]["unit_id"] == "w01-01-a"
    assert unit_lines[0]["engine_status"] == "succeeded"


def test_verify_ledger_chain_passes_on_untouched_ledger(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    now = ov.parse_now(_NOW)
    for i in range(3):
        ov.append_chained(
            inst,
            {"type": "event", "event": "checkpoint", "decision": "continue", "seq_hint": i},
            now,
        )
    ov.verify_ledger_chain(inst)  # must not raise


def test_verify_ledger_chain_fails_when_an_earlier_line_is_edited(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    now = ov.parse_now(_NOW)
    ov.append_chained(inst, {"type": "event", "event": "checkpoint", "decision": "continue"}, now)
    ov.append_chained(inst, {"type": "event", "event": "hold_answered"}, now)

    path = ov.ledger_path(inst)
    lines = path.read_text().splitlines()
    # Flip a semantic byte in the FIRST line (not just whitespace) so its canonical JSON --
    # and therefore the hash the second line depends on -- changes.
    corrupted_first = lines[0].replace('"decision":"continue"', '"decision":"redirect"')
    assert corrupted_first != lines[0]
    path.write_text(corrupted_first + "\n" + lines[1] + "\n")

    with pytest.raises(ov.Violation) as excinfo:
        ov.verify_ledger_chain(inst)
    assert excinfo.value.rule_id == "INT-3"


def test_ingest_ledger_synthesizes_breadcrumb_for_failed_unit_missing_one(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    now = ov.parse_now(_NOW)
    write_brief(inst, 1, "w01-01-a")
    # No breadcrumb written -- a failed unit never got to write one.
    state = make_state({"w01-01-a": {"status": "failed", "cumulative_cost_usd": 1.5}})

    ov.ingest_ledger(inst, ["w01-01-a"], state, 1, now)

    unit_lines = [line for line in ov.read_ledger_lines(inst) if line["type"] == "unit"]
    assert unit_lines[0]["outcome"] == "failed"
    assert unit_lines[0]["verdict"] == "fail"
    assert unit_lines[0]["engine_status"] == "failed"


def test_ingest_ledger_missing_breadcrumb_for_non_failed_unit_is_bc2(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    now = ov.parse_now(_NOW)
    write_brief(inst, 1, "w01-01-a")
    state = make_state({"w01-01-a": {"status": "succeeded", "cumulative_cost_usd": 1.0}})

    with pytest.raises(ov.Violation) as excinfo:
        ov.ingest_ledger(inst, ["w01-01-a"], state, 1, now)
    assert excinfo.value.rule_id == "BC-2"


def test_ingest_ledger_breadcrumb_unit_id_mismatch_is_bc1(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    now = ov.parse_now(_NOW)
    write_brief(inst, 1, "w01-01-a")
    write_breadcrumb(inst, "w01-01-a", file_unit_id="w01-99-wrong")
    state = make_state({"w01-01-a": {"status": "succeeded", "cumulative_cost_usd": 1.0}})

    with pytest.raises(ov.Violation) as excinfo:
        ov.ingest_ledger(inst, ["w01-01-a"], state, 1, now)
    assert excinfo.value.rule_id == "BC-1"


def test_ingest_ledger_missing_brief_is_br1(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    now = ov.parse_now(_NOW)
    write_breadcrumb(inst, "w01-01-a")
    state = make_state({"w01-01-a": {"status": "succeeded", "cumulative_cost_usd": 1.0}})

    with pytest.raises(ov.Violation) as excinfo:
        ov.ingest_ledger(inst, ["w01-01-a"], state, 1, now)
    assert excinfo.value.rule_id == "BR-1"


def test_ingest_ledger_skips_units_not_yet_settled(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    now = ov.parse_now(_NOW)
    write_brief(inst, 1, "w01-01-a")
    state = make_state({"w01-01-a": {"status": "running", "cumulative_cost_usd": 0.0}})

    appended = ov.ingest_ledger(inst, ["w01-01-a"], state, 1, now)
    assert appended == 0
    assert ov.read_ledger_lines(inst) == []


# =============================================================================================
# ===== AC6: path confinement (classify_path_entry) ===========================================
# =============================================================================================


def test_classify_path_entry_accepts_a_valid_entry(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo1"
    repo_root.mkdir()
    (repo_root / "file.py").write_text("x")

    result = ov.classify_path_entry("repo1:file.py", {"repo1": str(repo_root)})

    assert result.ok is True
    assert result.repo_id == "repo1"
    assert result.abs_path == str((repo_root / "file.py").resolve())


def test_classify_path_entry_rejects_absolute_path() -> None:
    result = ov.classify_path_entry("repo1:/etc/passwd", {"repo1": "/tmp/repo1"})
    assert result.ok is False
    assert result.reason == "absolute path"


def test_classify_path_entry_rejects_path_traversal() -> None:
    result = ov.classify_path_entry("repo1:../../etc/passwd", {"repo1": "/tmp/repo1"})
    assert result.ok is False
    assert result.reason == "path traversal ('..')"


def test_classify_path_entry_rejects_unknown_repo_id() -> None:
    result = ov.classify_path_entry("unknown-repo:some/file.py", {"repo1": "/tmp/repo1"})
    assert result.ok is False
    assert result.reason == "unknown repo_id"


def test_classify_path_entry_rejects_symlink_escaping_repo_root(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo1"
    outside = tmp_path / "outside"
    repo_root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("shh")
    (repo_root / "escape-link").symlink_to(outside)

    result = ov.classify_path_entry("repo1:escape-link/secret.txt", {"repo1": str(repo_root)})

    assert result.ok is False
    assert result.reason == "escapes repo root"


def test_update_path_history_records_rejected_entries_and_hashes_accepted_ones(
    tmp_path: Path,
) -> None:
    inst = tmp_path / "instance"
    now = ov.parse_now(_NOW)
    repo_root = tmp_path / "repo1"
    repo_root.mkdir()
    tracked_file = repo_root / "tracked.py"
    tracked_file.write_text("print('hi')")

    write_breadcrumb(
        inst,
        "w01-01-a",
        changed_paths=["repo1:tracked.py", "repo1:/etc/passwd", "unknown-repo:x.py"],
    )
    entry = ov.update_path_history(inst, 1, {"repo1": str(repo_root)}, ["w01-01-a"], now)

    assert str(tracked_file.resolve()) in entry["paths"]
    assert entry["paths"][str(tracked_file.resolve())] == hashlib.sha256(b"print('hi')").hexdigest()
    rejected_reasons = {r["reason"] for r in entry["rejected"]}
    assert "absolute path" in rejected_reasons
    assert "unknown repo_id" in rejected_reasons

    on_disk = json.loads(ov.path_history_path(inst).read_text())
    assert "01" in on_disk


# =============================================================================================
# ===== Charter-lock verify (INT-1) ===========================================================
# =============================================================================================


def test_verify_charter_lock_absent_is_not_an_error(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    inst.mkdir(parents=True)
    ov.verify_charter_lock(inst)  # intake-check hasn't run yet -- must not raise


def test_verify_charter_lock_matching_hashes_pass(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    (inst / "outputs" / "overseer").mkdir(parents=True)
    charter_bytes = b'{"asks": []}'
    (inst / "outputs" / "charter.json").write_bytes(charter_bytes)
    (inst / "prompt.md").write_text("do the thing")
    lock = {
        "sha256": hashlib.sha256(charter_bytes).hexdigest(),
        "prompt_sha256": hashlib.sha256((inst / "prompt.md").read_bytes()).hexdigest(),
    }
    (inst / "outputs" / "overseer" / "charter.lock.json").write_text(json.dumps(lock))

    ov.verify_charter_lock(inst)  # must not raise


def test_verify_charter_lock_charter_mismatch_is_int1(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    (inst / "outputs" / "overseer").mkdir(parents=True)
    (inst / "outputs" / "charter.json").write_bytes(b'{"asks": []}')
    lock = {"sha256": "0" * 64, "prompt_sha256": "0" * 64}
    (inst / "outputs" / "overseer" / "charter.lock.json").write_text(json.dumps(lock))

    with pytest.raises(ov.Violation) as excinfo:
        ov.verify_charter_lock(inst)
    assert excinfo.value.rule_id == "INT-1"


def test_verify_charter_lock_prompt_mismatch_is_int1(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    (inst / "outputs" / "overseer").mkdir(parents=True)
    charter_bytes = b'{"asks": []}'
    (inst / "outputs" / "charter.json").write_bytes(charter_bytes)
    (inst / "prompt.md").write_text("the real prompt")
    lock = {"sha256": hashlib.sha256(charter_bytes).hexdigest(), "prompt_sha256": "0" * 64}
    (inst / "outputs" / "overseer" / "charter.lock.json").write_text(json.dumps(lock))

    with pytest.raises(ov.Violation) as excinfo:
        ov.verify_charter_lock(inst)
    assert excinfo.value.rule_id == "INT-1"


# =============================================================================================
# ===== AC9: determinism -- byte-identical digest.json across two ckpt-prep runs =============
# =============================================================================================


def _build_ckpt_prep_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    ws = tmp_path
    inst = ws / "instance"
    write_config(inst)
    write_brief(inst, 1, "w01-01-a")
    write_breadcrumb(inst, "w01-01-a", changed_paths=[])

    run_id = "run-1"
    state_dict = build_state_dict(
        {
            "w01-01-a": {
                "status": "succeeded",
                "cumulative_cost_usd": 5.0,
                "started_at": _NOW,
                "ended_at": _NOW,
            }
        }
    )
    state_file = ws / ".orchestrator" / "runs" / run_id / "state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state_dict))

    ctx_path = tmp_path / "context.json"
    ctx_path.write_text(
        json.dumps({"run_id": run_id, "task_id": "ck-01", "repo_paths": {}, "output_paths": []})
    )
    monkeypatch.setenv("AO_HOOK_CONTEXT_PATH", str(ctx_path))
    return ws, inst


def test_ckpt_prep_digest_is_byte_identical_across_two_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws, inst = _build_ckpt_prep_fixture(tmp_path, monkeypatch)
    args = argparse.Namespace(
        workspace_root=str(ws),
        instance_dir="instance",
        now=_NOW,
        dry_run=False,
        task_id=None,
        reason=None,
    )

    ov.ckpt_prep(args)
    first_bytes = ov.digest_path(inst, 1).read_bytes()

    ov.ckpt_prep(args)
    second_bytes = ov.digest_path(inst, 1).read_bytes()

    assert first_bytes == second_bytes
    digest = json.loads(first_bytes)
    assert digest["generated_at"] == ov.now_iso(ov.parse_now(_NOW))
    assert digest["checkpoint"] == "ck-01"
    # Idempotent ingest: still exactly one unit line after two full ckpt-prep runs.
    unit_lines = [line for line in ov.read_ledger_lines(inst) if line["type"] == "unit"]
    assert len(unit_lines) == 1


# =============================================================================================
# ===== Extra coverage: malformed-input edge branches =========================================
# =============================================================================================


def test_read_ledger_lines_malformed_json_line_is_int3(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    path = ov.ledger_path(inst)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json\n")
    with pytest.raises(ov.Violation) as excinfo:
        ov.read_ledger_lines(inst)
    assert excinfo.value.rule_id == "INT-3"


def test_read_ledger_lines_non_object_line_is_int3(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    path = ov.ledger_path(inst)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([1, 2, 3]) + "\n")
    with pytest.raises(ov.Violation) as excinfo:
        ov.read_ledger_lines(inst)
    assert excinfo.value.rule_id == "INT-3"


def test_read_brief_oversize_is_br1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inst = tmp_path / "instance"
    write_brief(inst, 1, "w01-01-a")
    monkeypatch.setattr(ov, "JSON_MAX_BYTES", 1)
    with pytest.raises(ov.Violation) as excinfo:
        ov.read_brief(inst, 1, "w01-01-a")
    assert excinfo.value.rule_id == "BR-1"


def test_read_brief_malformed_json_is_br1(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    path = ov.brief_path(inst, 1, "w01-01-a")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid")
    with pytest.raises(ov.Violation) as excinfo:
        ov.read_brief(inst, 1, "w01-01-a")
    assert excinfo.value.rule_id == "BR-1"


def test_read_brief_non_dict_root_is_br1(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    path = ov.brief_path(inst, 1, "w01-01-a")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([1, 2]))
    with pytest.raises(ov.Violation) as excinfo:
        ov.read_brief(inst, 1, "w01-01-a")
    assert excinfo.value.rule_id == "BR-1"


def test_read_breadcrumb_oversize_is_bc1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inst = tmp_path / "instance"
    write_breadcrumb(inst, "w01-01-a")
    monkeypatch.setattr(ov, "JSON_MAX_BYTES", 1)
    with pytest.raises(ov.Violation) as excinfo:
        ov.read_breadcrumb(inst, "w01-01-a")
    assert excinfo.value.rule_id == "BC-1"


def test_read_breadcrumb_non_dict_root_is_bc1(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    path = ov.breadcrumb_path(inst, "w01-01-a")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([1, 2]))
    with pytest.raises(ov.Violation) as excinfo:
        ov.read_breadcrumb(inst, "w01-01-a")
    assert excinfo.value.rule_id == "BC-1"


def test_duration_seconds_handles_unparseable_timestamps() -> None:
    assert ov._duration_seconds("not-a-date", "2026-01-01T00:00:00+00:00") is None
    assert ov._duration_seconds("2026-01-01T00:00:00+00:00", "not-a-date") is None


def test_hash_path_absent_and_too_large(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert ov._hash_path(tmp_path / "does-not-exist.py") == "<absent>"

    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * 10)
    monkeypatch.setattr(ov, "HASH_SKIP_BYTES", 5)
    assert ov._hash_path(big) == "<skipped:too-large>"


def test_classify_path_entry_malformed_entry_missing_colon() -> None:
    result = ov.classify_path_entry("no-colon-here", {"repo1": "/tmp/repo1"})
    assert result.ok is False
    assert "separator" in (result.reason or "")


def test_update_path_history_skips_units_without_a_breadcrumb(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    now = ov.parse_now(_NOW)
    entry = ov.update_path_history(inst, 1, {}, ["w01-99-no-crumb"], now)
    assert entry["paths"] == {}
    assert entry["rejected"] == []


def test_verify_charter_lock_malformed_json_is_int1_not_an_uncaught_exception(
    tmp_path: Path,
) -> None:
    """Regression for a reviewer finding on T-ABDjSj's review: a corrupted
    charter.lock.json used to bubble an uncaught `json.JSONDecodeError` instead of
    failing closed through INT-1 -- exactly the tamper scenario INT-1 exists to catch."""
    inst = tmp_path / "instance"
    (inst / "outputs" / "overseer").mkdir(parents=True)
    (inst / "outputs" / "overseer" / "charter.lock.json").write_text("{not valid json")
    with pytest.raises(ov.Violation) as excinfo:
        ov.verify_charter_lock(inst)
    assert excinfo.value.rule_id == "INT-1"


def test_verify_charter_lock_non_dict_lock_root_is_int1(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    (inst / "outputs" / "overseer").mkdir(parents=True)
    (inst / "outputs" / "overseer" / "charter.lock.json").write_text(json.dumps([1, 2]))
    with pytest.raises(ov.Violation) as excinfo:
        ov.verify_charter_lock(inst)
    assert excinfo.value.rule_id == "INT-1"


def test_verify_charter_lock_missing_charter_file_is_int1(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    (inst / "outputs" / "overseer").mkdir(parents=True)
    lock = {"sha256": "0" * 64, "prompt_sha256": "0" * 64}
    (inst / "outputs" / "overseer" / "charter.lock.json").write_text(json.dumps(lock))
    # outputs/charter.json is deliberately absent.
    with pytest.raises(ov.Violation) as excinfo:
        ov.verify_charter_lock(inst)
    assert excinfo.value.rule_id == "INT-1"


def test_ckpt_prep_dry_run_prints_digest_and_writes_no_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ws, inst = _build_ckpt_prep_fixture(tmp_path, monkeypatch)
    before = sorted(str(p) for p in ws.rglob("*") if p.is_file())
    args = argparse.Namespace(
        workspace_root=str(ws),
        instance_dir="instance",
        now=_NOW,
        dry_run=True,
        task_id=None,
        reason=None,
    )

    ov.ckpt_prep(args)

    after = sorted(str(p) for p in ws.rglob("*") if p.is_file())
    assert before == after  # dry-run: ledger/path-history/digest are all skipped
    captured = capsys.readouterr()
    printed = json.loads(captured.out)
    assert printed["checkpoint"] == "ck-01"
