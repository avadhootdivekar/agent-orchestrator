"""Tests for `overseer_tool.py`'s signal detectors and progress digest (M2).

Ticket: `meta/tickets/E-YAAGhk-overseer-runner-template/T-C6uQJW-tool-loop-progress-detectors/`.
Covers AC1 (`detect_period` table), AC2 (`detect_mirror` table), AC3 (`content_oscillation` on
a real git repo fixture, including the git-diff/status "omitted path" extension to
`update_path_history`), AC4 (`stall`), AC5 (`attempt_cap`/`repeated_failure`/`ask_starvation`/
`blocked_units`/`prompt_changed`), AC6 (`breadcrumb_integrity`), AC7 (signal id/order
determinism under ledger-line shuffling), AC8 (`compute_progress` hand-computed fixtures), plus
extra coverage for the `update_path_history` trim/repo_heads extensions this task also owns.

Follows the same load-by-path + local fixture-helper convention as
`tests/test_overseer_tool_{budget,ledger,gates}.py` (M1) -- those files are NOT imported from
here (each M1 test file already duplicates its own helpers rather than sharing a conftest, so
this file matches that established local convention).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
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
    spec = importlib.util.spec_from_file_location("overseer_tool_under_test_detectors", _TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ov = _load_overseer_tool()

_NOW = "2026-01-01T00:00:00+00:00"


# =============================================================================================
# ===== Fixture helpers (mirrors the M1 test files' own local-helper convention) =============
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


def load_config(inst: Path, **overrides: object):
    write_config(inst, **overrides)
    return ov.load_config(inst)


def write_breadcrumb(inst: Path, unit_id: str, **fields: object) -> None:
    path = ov.breadcrumb_path(inst, unit_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema": "ao.overseer.breadcrumb/v1",
        "unit_id": unit_id,
        "outcome": "done",
        "verdict": "pass",
        "summary": "did the thing",
        "changed_paths": [],
        "needs_input": False,
    }
    data.update(fields)
    path.write_text(json.dumps(data))


def append_unit_line(inst: Path, **overrides: object) -> dict:
    base: dict = {
        "type": "unit",
        "wave": 1,
        "unit_id": "w01-01-a",
        "ask_ids": ["A1"],
        "work_item": "A1/parser",
        "kind": "implement",
        "attempt_no": 1,
        "outcome": "done",
        "verdict": "pass",
        "engine_status": "succeeded",
        "cost_usd": 1.0,
        "duration_s": 10.0,
        "changed_paths": [],
        "needs_input": False,
    }
    base.update(overrides)
    write_ledger_lines(inst, [*read_raw_ledger_lines(inst), base])
    return base


def read_raw_ledger_lines(inst: Path) -> list[dict]:
    path = ov.ledger_path(inst)
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_ledger_lines(inst: Path, lines: list[dict]) -> None:
    path = ov.ledger_path(inst)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines) + ("\n" if lines else ""))


def write_verdict(inst: Path, k: int, **fields: object) -> None:
    path = ov.verdict_path(inst, k)
    path.parent.mkdir(parents=True, exist_ok=True)
    base: dict = {
        "schema": "ao.overseer.verdict/v1",
        "checkpoint": f"ck-{k:02d}",
        "stage": "explore",
        "decision": "continue",
        "rationale": "ok",
        "next_wave_goal": "",
        "alignment": [],
        "criteria": [],
        "signal_responses": [],
    }
    base.update(fields)
    path.write_text(json.dumps(base))


def write_charter(inst: Path, asks: list[dict], **fields: object) -> dict:
    path = inst / "outputs" / "charter.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {
        "schema": "ao.overseer.charter/v1",
        "prompt_sha256": "0" * 64,
        "asks": asks,
        "global_constraints": [],
        "assumptions": [],
        "out_of_scope": [],
        "open_questions": [],
    }
    data.update(fields)
    path.write_text(json.dumps(data))
    return data


def write_path_history(inst: Path, entries: dict) -> None:
    path = ov.path_history_path(inst)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries))


# ----- real git repo fixture (AC3) -- mirrors tests/test_hotspots.py's own local pattern ----

_TEST_AUTHOR_ENV = {
    "GIT_AUTHOR_NAME": "ao-test",
    "GIT_AUTHOR_EMAIL": "ao-test@example.invalid",
    "GIT_COMMITTER_NAME": "ao-test",
    "GIT_COMMITTER_EMAIL": "ao-test@example.invalid",
}


@pytest.fixture(autouse=True)
def _detectors_isolated_git_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mirrors `tests/test_hotspots.py::_hotspots_isolated_git_env` -- every real `git`
    subprocess this file spawns resolves config under this test's own `tmp_path`, never the
    real home/state dir.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AO_STATE_DIR", str(tmp_path / "ao-state"))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    monkeypatch.delenv("GIT_CONFIG_SYSTEM", raising=False)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, **_TEST_AUTHOR_ENV},
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "ao-test")
    _git(repo, "config", "user.email", "ao-test@example.invalid")


def _commit_all(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


# =============================================================================================
# ===== AC1: detect_period table tests =========================================================
# =============================================================================================


@pytest.mark.parametrize(
    "seq,expected",
    [
        (["r:f", "x:p", "r:f", "x:p"], (2, 2)),
        (["a", "b", "c", "d", "a", "b", "c", "d"], (4, 2)),
        (["a", "a", "a"], (1, 3)),
        (["a", "a"], None),
        (["a", "b", "a"], None),
        (["a", "b", "c", "a", "b"], None),
        (["a", "b", "a", "b", "a", "b", "a", "b"], (2, 4)),  # smallest period wins
    ],
)
def test_detect_period_table(seq: list[str], expected: tuple[int, int] | None) -> None:
    assert ov.detect_period(seq) == expected


# =============================================================================================
# ===== AC2: detect_mirror table tests =========================================================
# =============================================================================================


@pytest.mark.parametrize(
    "seq,expected",
    [
        (["a", "b", "b", "a"], 2),
        (["a", "b", "c", "c", "b", "a"], 3),
        (["a", "a", "a", "a"], None),  # first half needs >= 2 distinct tokens
        (["a", "b", "a", "b"], None),
    ],
)
def test_detect_mirror_table(seq: list[str], expected: int | None) -> None:
    assert ov.detect_mirror(seq) == expected


def test_returns_to_earlier_pure() -> None:
    assert ov.returns_to_earlier(["A", "B", "A"]) == [2]
    assert ov.returns_to_earlier(["A", "B", "C"]) == []
    # A->B->A->C->A: two separate qualifying returns (index 2 and index 4).
    assert ov.returns_to_earlier(["A", "B", "A", "C", "A"]) == [2, 4]


# =============================================================================================
# ===== AC3: content_oscillation on a real temp git repo fixture ==============================
# =============================================================================================


def test_content_oscillation_single_path_a_b_a_is_one_medium_signal(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    now = ov.parse_now(_NOW)
    repo = tmp_path / "repo1"
    _init_repo(repo)
    (repo / "f.txt").write_text("A")
    _commit_all(repo, "A")

    write_breadcrumb(inst, "w01-01-a", changed_paths=["repo1:f.txt"])
    ov.update_path_history(inst, 1, {"repo1": str(repo)}, ["w01-01-a"], now)

    (repo / "f.txt").write_text("B")
    _commit_all(repo, "B")
    write_breadcrumb(inst, "w02-01-a", changed_paths=["repo1:f.txt"])
    ov.update_path_history(inst, 2, {"repo1": str(repo)}, ["w02-01-a"], now)

    (repo / "f.txt").write_text("A")
    _commit_all(repo, "back to A")
    write_breadcrumb(inst, "w03-01-a", changed_paths=["repo1:f.txt"])
    ov.update_path_history(inst, 3, {"repo1": str(repo)}, ["w03-01-a"], now)

    signals = ov._content_oscillation_signals(inst, 3)
    assert len(signals) == 1
    assert signals[0]["type"] == "content_oscillation"
    assert signals[0]["severity"] == "medium"
    assert signals[0]["path"] == str((repo / "f.txt").resolve())


def test_content_oscillation_two_paths_is_high(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    now = ov.parse_now(_NOW)
    repo = tmp_path / "repo1"
    _init_repo(repo)
    (repo / "f1.txt").write_text("A1")
    (repo / "f2.txt").write_text("A2")
    _commit_all(repo, "init")

    write_breadcrumb(inst, "w01-01-a", changed_paths=["repo1:f1.txt", "repo1:f2.txt"])
    ov.update_path_history(inst, 1, {"repo1": str(repo)}, ["w01-01-a"], now)

    (repo / "f1.txt").write_text("B1")
    (repo / "f2.txt").write_text("B2")
    _commit_all(repo, "change both")
    write_breadcrumb(inst, "w02-01-a", changed_paths=["repo1:f1.txt", "repo1:f2.txt"])
    ov.update_path_history(inst, 2, {"repo1": str(repo)}, ["w02-01-a"], now)

    (repo / "f1.txt").write_text("A1")
    (repo / "f2.txt").write_text("A2")
    _commit_all(repo, "revert both")
    write_breadcrumb(inst, "w03-01-a", changed_paths=["repo1:f1.txt", "repo1:f2.txt"])
    ov.update_path_history(inst, 3, {"repo1": str(repo)}, ["w03-01-a"], now)

    signals = ov._content_oscillation_signals(inst, 3)
    assert len(signals) == 2
    assert {s["path"] for s in signals} == {
        str((repo / "f1.txt").resolve()),
        str((repo / "f2.txt").resolve()),
    }
    assert all(s["severity"] == "high" for s in signals)


def test_content_oscillation_delete_then_restore_counts(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    now = ov.parse_now(_NOW)
    repo = tmp_path / "repo1"
    _init_repo(repo)
    (repo / "g.txt").write_text("A")
    _commit_all(repo, "init")

    write_breadcrumb(inst, "w01-01-a", changed_paths=["repo1:g.txt"])
    ov.update_path_history(inst, 1, {"repo1": str(repo)}, ["w01-01-a"], now)

    (repo / "g.txt").unlink()
    _commit_all(repo, "delete")
    write_breadcrumb(inst, "w02-01-a", changed_paths=["repo1:g.txt"])
    entry2 = ov.update_path_history(inst, 2, {"repo1": str(repo)}, ["w02-01-a"], now)
    assert entry2["paths"][str((repo / "g.txt").resolve())] == "<absent>"

    (repo / "g.txt").write_text("A")
    _commit_all(repo, "restore")
    write_breadcrumb(inst, "w03-01-a", changed_paths=["repo1:g.txt"])
    ov.update_path_history(inst, 3, {"repo1": str(repo)}, ["w03-01-a"], now)

    signals = ov._content_oscillation_signals(inst, 3)
    assert len(signals) == 1
    assert signals[0]["severity"] == "medium"


def test_content_oscillation_detects_path_omitted_from_breadcrumb_via_git_diff(
    tmp_path: Path,
) -> None:
    """AC3's last bullet: a unit that reverts a file but OMITS it from its own
    `changed_paths` still gets caught, via `update_path_history`'s `git diff --name-only
    <head_at_ck(K-1)>` extension (T-C6uQJW)."""
    inst = tmp_path / "instance"
    now = ov.parse_now(_NOW)
    repo = tmp_path / "repo1"
    _init_repo(repo)
    (repo / "h.txt").write_text("A")
    _commit_all(repo, "init")

    write_breadcrumb(inst, "w01-01-a", changed_paths=["repo1:h.txt"])
    entry1 = ov.update_path_history(inst, 1, {"repo1": str(repo)}, ["w01-01-a"], now)
    assert entry1["repo_heads"]["repo1"] == _git(repo, "rev-parse", "HEAD").stdout.strip()

    (repo / "h.txt").write_text("B")
    _commit_all(repo, "change")
    write_breadcrumb(inst, "w02-01-a", changed_paths=["repo1:h.txt"])
    ov.update_path_history(inst, 2, {"repo1": str(repo)}, ["w02-01-a"], now)

    # Revert back to A and commit, but the unit's OWN breadcrumb says nothing changed.
    (repo / "h.txt").write_text("A")
    _commit_all(repo, "silent revert")
    write_breadcrumb(inst, "w03-01-a", changed_paths=[])
    ov.update_path_history(inst, 3, {"repo1": str(repo)}, ["w03-01-a"], now)

    signals = ov._content_oscillation_signals(inst, 3)
    assert len(signals) == 1
    assert signals[0]["path"] == str((repo / "h.txt").resolve())


def test_git_rev_parse_head_and_changed_paths_on_non_git_dir_are_none_and_empty(
    tmp_path: Path,
) -> None:
    not_a_repo = tmp_path / "plain-dir"
    not_a_repo.mkdir()
    assert ov._git_rev_parse_head(str(not_a_repo)) is None
    assert ov._git_changed_paths(str(not_a_repo), None) == []


def test_confine_repo_relative_rejects_traversal_and_accepts_nested_path(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo1"
    repo_root.mkdir()
    assert ov._confine_repo_relative(repo_root, "../outside.txt") is None
    assert ov._confine_repo_relative(repo_root, "/etc/passwd") is None
    nested = ov._confine_repo_relative(repo_root, "sub/dir/file.txt")
    assert nested == str((repo_root / "sub" / "dir" / "file.txt").resolve())


# =============================================================================================
# ===== update_path_history: MAX_TRACKED_PATHS "most recently changed" trim + info note ======
# =============================================================================================


def test_update_path_history_trim_drops_current_wave_overflow_with_info_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inst = tmp_path / "instance"
    now = ov.parse_now(_NOW)
    monkeypatch.setattr(ov, "MAX_TRACKED_PATHS", 2)
    repo = tmp_path / "repo1"
    repo.mkdir()
    for name in ("p1.txt", "p2.txt", "p3.txt"):
        (repo / name).write_text(name)
    write_breadcrumb(
        inst,
        "w01-01-a",
        changed_paths=["repo1:p1.txt", "repo1:p2.txt", "repo1:p3.txt"],
    )

    entry = ov.update_path_history(inst, 1, {"repo1": str(repo)}, ["w01-01-a"], now)

    assert len(entry["paths"]) == 2
    assert "info" in entry and "dropped 1" in entry["info"][0]


def test_update_path_history_trim_carries_forward_most_recent_older_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inst = tmp_path / "instance"
    now = ov.parse_now(_NOW)
    monkeypatch.setattr(ov, "MAX_TRACKED_PATHS", 5)
    repo = tmp_path / "repo1"
    repo.mkdir()
    for name in ("p1.txt", "p2.txt", "p3.txt", "p4.txt"):
        (repo / name).write_text(name)

    write_breadcrumb(
        inst, "w01-01-a", changed_paths=["repo1:p1.txt", "repo1:p2.txt", "repo1:p3.txt"]
    )
    ov.update_path_history(inst, 1, {"repo1": str(repo)}, ["w01-01-a"], now)

    write_breadcrumb(inst, "w02-01-a", changed_paths=["repo1:p4.txt"])
    entry2 = ov.update_path_history(inst, 2, {"repo1": str(repo)}, ["w02-01-a"], now)

    # remaining_slots = 5 - 1 = 4 >= the 3 older paths -> all carried forward, nothing dropped.
    expected_paths = {str((repo / n).resolve()) for n in ("p1.txt", "p2.txt", "p3.txt", "p4.txt")}
    assert set(entry2["paths"]) == expected_paths
    assert "info" not in entry2


def test_update_path_history_trim_prioritizes_git_derived_over_declared_when_over_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for a reviewer finding on T-C6uQJW's review (W1): if a busy wave's
    breadcrumb-DECLARED paths alone reach MAX_TRACKED_PATHS, a declared-first trim order
    would silently drop exactly the git-derived, UNDECLARED paths this whole feature exists
    to catch (HLD S8.3 Rev 2 reviewer #6). The undeclared path must survive the trim, at the
    expense of a declared one, not the other way around."""
    inst = tmp_path / "instance"
    now = ov.parse_now(_NOW)
    monkeypatch.setattr(ov, "MAX_TRACKED_PATHS", 2)
    repo = tmp_path / "repo1"
    _init_repo(repo)
    for name in ("p1.txt", "p2.txt", "p3.txt"):
        (repo / name).write_text("v1")
    _commit_all(repo, "initial")

    # The unit's breadcrumb declares 2 paths -- already at the cap -- but LEAVES OUT p3.txt,
    # which it also (truthfully or not) modified on disk. `git status --porcelain` sees it.
    (repo / "p3.txt").write_text("v2 -- undeclared change")
    write_breadcrumb(inst, "w01-01-a", changed_paths=["repo1:p1.txt", "repo1:p2.txt"])

    entry = ov.update_path_history(inst, 1, {"repo1": str(repo)}, ["w01-01-a"], now)

    tracked_paths = set(entry["paths"])
    p3_abs = str((repo / "p3.txt").resolve())
    assert p3_abs in tracked_paths, (
        "the undeclared, git-derived path must survive the trim -- it is exactly what this "
        "feature exists to catch, and must not be the one sacrificed to make room"
    )
    assert len(tracked_paths) == 2  # still capped
    assert "info" in entry and "dropped 1" in entry["info"][0]


# =============================================================================================
# ===== AC6: breadcrumb_integrity ==============================================================
# =============================================================================================


def test_breadcrumb_integrity_signals_surface_m1_rejected_entries(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    write_path_history(
        inst,
        {
            "01": {
                "generated_at": _NOW,
                "paths": {},
                "rejected": [
                    {
                        "unit_id": "w01-01-a",
                        "entry": "repo1:/etc/passwd",
                        "reason": "absolute path",
                    },
                    {"unit_id": "w01-02-b", "entry": "bad-entry", "reason": "malformed"},
                ],
                "repo_heads": {},
            }
        },
    )

    signals = ov._breadcrumb_integrity_signals(inst, 1)

    assert len(signals) == 2
    assert all(s["type"] == "breadcrumb_integrity" and s["severity"] == "high" for s in signals)
    assert {s["path"] for s in signals} == {"repo1:/etc/passwd", "bad-entry"}


def test_breadcrumb_integrity_no_entry_for_checkpoint_is_empty(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    assert ov._breadcrumb_integrity_signals(inst, 1) == []


# =============================================================================================
# ===== AC4: stall =============================================================================
# =============================================================================================


def test_consecutive_stall_waves_counts_non_productive_waves(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    cfg = load_config(inst, stall_waves=2)
    lines = [
        append_unit_line(inst, wave=1, unit_id="w01-01-a", outcome="partial", verdict="fail"),
        append_unit_line(inst, wave=2, unit_id="w02-01-a", outcome="partial", verdict="fail"),
        append_unit_line(inst, wave=3, unit_id="w03-01-a", outcome="partial", verdict="fail"),
    ]
    assert ov._consecutive_stall_waves(cfg, inst, 3, lines) == 3

    signals = ov.detect_signals(cfg, inst, 3)
    stall_signals = [s for s in signals if s["type"] == "stall"]
    assert len(stall_signals) == 1
    assert stall_signals[0]["severity"] == "high"


def test_consecutive_stall_waves_skips_hold_waves(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    cfg = load_config(inst, stall_waves=2)
    lines = [
        append_unit_line(inst, wave=1, unit_id="w01-01-a", outcome="partial", verdict="fail"),
        append_unit_line(inst, wave=2, unit_id="w02-01-a", outcome="partial", verdict="fail"),
        # wave 3 is a hold wave: zero units ingested, no line at all.
        append_unit_line(inst, wave=4, unit_id="w04-01-a", outcome="partial", verdict="fail"),
    ]
    assert ov._consecutive_stall_waves(cfg, inst, 4, lines) == 3


def test_consecutive_stall_waves_reset_by_a_done_unit(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    cfg = load_config(inst, stall_waves=2)
    lines = [
        append_unit_line(inst, wave=1, unit_id="w01-01-a", outcome="partial", verdict="fail"),
        append_unit_line(inst, wave=2, unit_id="w02-01-a", outcome="done", verdict="pass"),
        append_unit_line(inst, wave=3, unit_id="w03-01-a", outcome="partial", verdict="fail"),
    ]
    # wave2 is productive -> streak resets to 0, then wave3 alone -> streak 1.
    assert ov._consecutive_stall_waves(cfg, inst, 3, lines) == 1


def test_consecutive_stall_waves_reset_by_rising_criteria_met(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    cfg = load_config(inst, stall_waves=2)
    lines = [
        append_unit_line(inst, wave=1, unit_id="w01-01-a", outcome="partial", verdict="fail"),
        append_unit_line(inst, wave=2, unit_id="w02-01-a", outcome="partial", verdict="fail"),
        append_unit_line(inst, wave=3, unit_id="w03-01-a", outcome="partial", verdict="fail"),
    ]
    write_verdict(inst, 1, criteria=[{"id": "c1", "status": "unmet"}])
    write_verdict(inst, 2, criteria=[{"id": "c1", "status": "met"}])  # criteria_met rose 0 -> 1

    # wave2's rise resets the streak; wave3 has no verdict yet (criteria_met unknown -> carried
    # forward, not a rise) so it alone counts -> streak 1.
    assert ov._consecutive_stall_waves(cfg, inst, 3, lines) == 1


# =============================================================================================
# ===== AC5: attempt_cap / repeated_failure / ask_starvation / blocked_units / prompt_changed =
# =============================================================================================


def test_attempt_cap_fires_at_max_attempts_per_item(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    cfg = load_config(inst, max_attempts_per_item=3)
    lines = [
        append_unit_line(inst, wave=1, unit_id="w01-01-a", work_item="A1/x"),
        append_unit_line(inst, wave=2, unit_id="w02-01-a", work_item="A1/x"),
        append_unit_line(inst, wave=3, unit_id="w03-01-a", work_item="A1/x"),
    ]
    signals = ov._attempt_cap_signals(cfg, lines)
    assert len(signals) == 1
    assert signals[0]["severity"] == "high"
    assert signals[0]["work_item"] == "A1/x"
    assert signals[0]["evidence"]["attempts"] == 3


def test_repeated_failure_fires_on_last_two_fails(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    lines = [
        append_unit_line(inst, wave=1, unit_id="w01-01-a", work_item="A1/y", verdict="fail"),
        append_unit_line(inst, wave=2, unit_id="w02-01-a", work_item="A1/y", verdict="fail"),
    ]
    signals = ov._repeated_failure_signals(lines)
    assert len(signals) == 1
    assert signals[0]["severity"] == "medium"
    assert signals[0]["work_item"] == "A1/y"


def test_repeated_failure_does_not_fire_on_a_single_fail(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    lines = [
        append_unit_line(inst, wave=1, unit_id="w01-01-a", work_item="A1/y", verdict="pass"),
        append_unit_line(inst, wave=2, unit_id="w02-01-a", work_item="A1/y", verdict="fail"),
    ]
    assert ov._repeated_failure_signals(lines) == []


def test_ask_starvation_fires_for_untouched_non_met_ask(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    cfg = load_config(inst, stall_waves=2)
    write_charter(inst, [{"ask_id": "A1"}, {"ask_id": "A2"}])
    lines = [
        append_unit_line(inst, wave=2, unit_id="w02-01-a", ask_ids=["A1"]),
        append_unit_line(inst, wave=3, unit_id="w03-01-a", ask_ids=["A1"]),
    ]
    signals = ov._ask_starvation_signals(cfg, inst, 3, lines)
    assert len(signals) == 1
    assert signals[0]["ask_id"] == "A2"
    assert signals[0]["severity"] == "medium"


def test_ask_starvation_suppressed_when_alignment_is_met_or_deferred(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    cfg = load_config(inst, stall_waves=2)
    write_charter(inst, [{"ask_id": "A1"}, {"ask_id": "A2"}])
    write_verdict(
        inst,
        2,
        alignment=[{"ask_id": "A2", "status": "met", "evidence": "done"}],
    )
    lines = [append_unit_line(inst, wave=3, unit_id="w03-01-a", ask_ids=["A1"])]
    assert ov._ask_starvation_signals(cfg, inst, 3, lines) == []


def test_ask_starvation_no_charter_is_no_signals_not_an_error(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    cfg = load_config(inst, stall_waves=2)
    assert ov._ask_starvation_signals(cfg, inst, 1, []) == []


def test_blocked_units_fires_for_blocked_and_needs_input_in_current_wave(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    lines = [
        append_unit_line(inst, wave=3, unit_id="w03-01-a", outcome="blocked", verdict="na"),
        append_unit_line(inst, wave=3, unit_id="w03-02-b", needs_input=True),
        # A blocked unit in a PAST wave must not be reported for the current wave (K=3).
        append_unit_line(inst, wave=1, unit_id="w01-01-a", outcome="blocked", verdict="na"),
    ]
    signals = ov._blocked_units_signals(lines, 3)
    assert {s["evidence"]["unit_id"] for s in signals} == {"w03-01-a", "w03-02-b"}
    assert all(s["severity"] == "medium" for s in signals)


def test_prompt_changed_fires_on_sha_mismatch(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    inst.mkdir(parents=True)
    (inst / "prompt.md").write_text("the real prompt")
    write_charter(inst, [], prompt_sha256="0" * 64)

    signals = ov._prompt_changed_signal(inst)
    assert len(signals) == 1
    assert signals[0]["severity"] == "high"


def test_prompt_changed_matching_sha_is_not_a_signal(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    inst.mkdir(parents=True)
    (inst / "prompt.md").write_text("the real prompt")
    sha = hashlib.sha256((inst / "prompt.md").read_bytes()).hexdigest()
    write_charter(inst, [], prompt_sha256=sha)

    assert ov._prompt_changed_signal(inst) == []


def test_prompt_changed_both_missing_is_not_a_signal(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    inst.mkdir(parents=True)
    assert ov._prompt_changed_signal(inst) == []


# =============================================================================================
# ===== AC7: signal id/order determinism under ledger-line shuffling =========================
# =============================================================================================


def test_detect_signals_order_is_deterministic_under_ledger_line_shuffle(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    cfg = load_config(inst, stall_waves=2, max_attempts_per_item=2)
    write_charter(inst, [{"ask_id": "A1"}, {"ask_id": "A2"}])

    lines = [
        # period_repeat candidate on A1/loop: review:fail, fix:pass, review:fail, fix:pass
        append_unit_line(
            inst, wave=1, unit_id="w01-01-a", work_item="A1/loop", kind="review", verdict="fail"
        ),
        append_unit_line(
            inst, wave=1, unit_id="w01-02-b", work_item="A1/loop", kind="fix", verdict="pass"
        ),
        append_unit_line(
            inst, wave=2, unit_id="w02-01-a", work_item="A1/loop", kind="review", verdict="fail"
        ),
        append_unit_line(
            inst, wave=2, unit_id="w02-02-b", work_item="A1/loop", kind="fix", verdict="pass"
        ),
        # attempt_cap candidate on A1/capped (max_attempts_per_item=2)
        append_unit_line(inst, wave=1, unit_id="w01-03-c", work_item="A1/capped"),
        append_unit_line(inst, wave=2, unit_id="w02-03-c", work_item="A1/capped"),
        # blocked unit in the current wave (2)
        append_unit_line(inst, wave=2, unit_id="w02-04-d", work_item="A2/x", outcome="blocked"),
    ]

    write_ledger_lines(inst, lines)
    signals_in_order = ov.detect_signals(cfg, inst, 2)

    shuffled = list(reversed(lines))
    write_ledger_lines(inst, shuffled)
    signals_shuffled = ov.detect_signals(cfg, inst, 2)

    assert signals_in_order == signals_shuffled
    assert len(signals_in_order) >= 3
    ids = [s["id"] for s in signals_in_order]
    assert ids == sorted(ids)  # S-02-01, S-02-02, ... assigned in ascending order


# =============================================================================================
# ===== AC8: compute_progress hand-computed fixture ============================================
# =============================================================================================


def test_compute_progress_matches_hand_computed_fixture(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    cfg = load_config(inst, stall_waves=5)
    write_charter(inst, [{"ask_id": "A1"}, {"ask_id": "A2"}])
    write_verdict(
        inst, 1, criteria=[{"id": "c1", "status": "met"}, {"id": "c2", "status": "unmet"}]
    )

    lines = [
        append_unit_line(
            inst,
            wave=1,
            unit_id="w01-01-a",
            work_item="A1/x",
            ask_ids=["A1"],
            outcome="done",
            verdict="pass",
            cost_usd=2.0,
        ),
        append_unit_line(
            inst,
            wave=2,
            unit_id="w02-01-a",
            work_item="A1/y",
            ask_ids=["A1"],
            outcome="done",
            verdict="pass",
            cost_usd=3.0,
        ),
        append_unit_line(
            inst,
            wave=2,
            unit_id="w02-02-b",
            work_item="A2/z",
            ask_ids=["A2"],
            outcome="partial",
            verdict="fail",
            cost_usd=1.5,
        ),
    ]
    write_ledger_lines(inst, lines)

    progress = ov.compute_progress(cfg, inst, 2)

    assert progress["work_items_total"] == 3
    assert progress["work_items_done"] == 2
    assert progress["newly_done"] == 1  # A1/y became done at wave 2; A1/x already was
    assert progress["criteria_met_prev"] == 1  # from ck-1's verdict
    assert progress["stall_waves"] == 0  # both waves had a done+pass unit

    per_ask = {p["ask_id"]: p for p in progress["per_ask"]}
    assert per_ask["A1"]["units"] == 2
    assert per_ask["A1"]["cost_usd"] == 5.0
    assert per_ask["A1"]["done_items"] == 2
    assert per_ask["A1"]["last_wave_touched"] == 2
    assert per_ask["A2"]["units"] == 1
    assert per_ask["A2"]["cost_usd"] == 1.5
    assert per_ask["A2"]["done_items"] == 0
    assert per_ask["A2"]["last_wave_touched"] == 2


def test_compute_progress_no_charter_falls_back_to_ledger_ask_ids(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    cfg = load_config(inst, stall_waves=5)
    lines = [
        append_unit_line(inst, wave=1, unit_id="w01-01-a", work_item="A1/x", ask_ids=["A1"]),
    ]
    write_ledger_lines(inst, lines)

    progress = ov.compute_progress(cfg, inst, 1)
    assert [p["ask_id"] for p in progress["per_ask"]] == ["A1"]
    assert progress["criteria_met_prev"] == 0  # no ck-0 verdict


# =============================================================================================
# ===== Integration: detect_signals / compute_progress wired through ckpt_prep ===============
# =============================================================================================


def test_ckpt_prep_wires_real_detect_signals_and_compute_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst = ws / "instance"
    write_config(inst, max_attempts_per_item=1, stall_waves=5)

    path = ov.brief_path(inst, 1, "w01-01-a")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": "ao.overseer.brief/v1",
                "unit_id": "w01-01-a",
                "wave": 1,
                "ask_ids": ["A1"],
                "work_item": "A1/x",
                "kind": "implement",
                "goal": "do it",
                "acceptance": ["it works"],
            }
        )
    )
    write_breadcrumb(inst, "w01-01-a", changed_paths=[])

    run_id = "run-1"
    state_dict = {
        "run_id": run_id,
        "workflow_id": "wf-1",
        "repo_set": "main",
        "started_at": _NOW,
        "updated_at": _NOW,
        "status": "running",
        "tasks": {
            "w01-01-a": {
                "status": "succeeded",
                "attempts": 1,
                "cumulative_cost_usd": 5.0,
                "started_at": _NOW,
                "ended_at": _NOW,
            }
        },
        "injected_tasks": [{"id": "w01-01-a"}],
        "breaker_overrides": {},
    }
    state_file = ws / ".orchestrator" / "runs" / run_id / "state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state_dict))

    ctx_path = tmp_path / "context.json"
    ctx_path.write_text(
        json.dumps({"run_id": run_id, "task_id": "ck-01", "repo_paths": {}, "output_paths": []})
    )

    args = argparse.Namespace(
        workspace_root=str(ws),
        instance_dir="instance",
        now=_NOW,
        dry_run=False,
        task_id=None,
        reason=None,
    )
    monkeypatch.setenv("AO_HOOK_CONTEXT_PATH", str(ctx_path))
    ov.ckpt_prep(args)

    digest = json.loads(ov.digest_path(inst, 1).read_text())
    # attempt_cap fires immediately since max_attempts_per_item=1 and the item has 1 unit.
    assert any(s["type"] == "attempt_cap" for s in digest["signals"])
    assert digest["progress"]["work_items_total"] == 1
    assert digest["progress"]["work_items_done"] == 1


# =============================================================================================
# ===== Extra coverage: mirror_flipflop, tolerant/malformed-data branches, git edge cases =====
# =============================================================================================


def test_returns_to_earlier_skips_unchanged_consecutive_values() -> None:
    # idx=2: hist[1] == hist[2] ("A" == "A") -> the "no change since last checkpoint" branch
    # is skipped rather than counted as a return.
    assert ov.returns_to_earlier(["A", "A", "A"]) == []


def test_period_mirror_signals_emits_mirror_flipflop(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    lines = [
        append_unit_line(
            inst, wave=1, unit_id="w01-01-a", work_item="A1/z", kind="design", verdict="pass"
        ),
        append_unit_line(
            inst, wave=1, unit_id="w01-02-b", work_item="A1/z", kind="review", verdict="fail"
        ),
        append_unit_line(
            inst, wave=2, unit_id="w02-01-a", work_item="A1/z", kind="review", verdict="fail"
        ),
        append_unit_line(
            inst, wave=2, unit_id="w02-02-b", work_item="A1/z", kind="design", verdict="pass"
        ),
    ]
    signals = ov._period_mirror_signals(lines)
    mirror = [s for s in signals if s["type"] == "mirror_flipflop"]
    assert len(mirror) == 1
    assert mirror[0]["severity"] == "medium"
    assert mirror[0]["work_item"] == "A1/z"


def test_git_rev_parse_head_handles_subprocess_exceptions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(ov.subprocess, "run", _raise)
    assert ov._git_rev_parse_head(str(tmp_path)) is None


def test_git_changed_paths_handles_subprocess_exceptions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(*args: object, **kwargs: object) -> None:
        raise OSError("git binary not found")

    monkeypatch.setattr(ov.subprocess, "run", _raise)
    assert ov._git_changed_paths(str(tmp_path), "deadbeef") == []


def test_git_changed_paths_parses_a_rename_from_status_porcelain(tmp_path: Path) -> None:
    repo = tmp_path / "repo1"
    _init_repo(repo)
    (repo / "old.txt").write_text("content")
    _commit_all(repo, "init")
    _git(repo, "mv", "old.txt", "new.txt")

    rel_paths = ov._git_changed_paths(str(repo), None)
    assert "new.txt" in rel_paths


def test_confine_repo_relative_rejects_symlink_escape(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo1"
    outside = tmp_path / "outside"
    repo_root.mkdir()
    outside.mkdir()
    (repo_root / "escape-link").symlink_to(outside)

    assert ov._confine_repo_relative(repo_root, "escape-link/secret.txt") is None


def test_trim_tracked_paths_tolerates_malformed_history_entries() -> None:
    history = {
        "01": {"paths": {"/a": "hashA"}},
        "not-a-number": {"paths": {"/b": "hashB"}},
        "02": "not-a-dict",
        "03": {"paths": "not-a-dict"},
    }
    trimmed, dropped = ov._trim_tracked_paths(history, 4, ["/current"], {"/current"})
    assert trimmed == ["/current", "/a"]
    assert dropped == 0


def test_criteria_met_count_non_list_criteria_is_none(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    write_verdict(inst, 1, criteria="not-a-list")
    assert ov._criteria_met_count(inst, 1) is None


def test_content_oscillation_tolerates_malformed_history_entries(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    write_path_history(
        inst,
        {
            "01": {"paths": {"/p": "A"}},
            "not-a-number": {"paths": {"/p": "Z"}},
            "02": "not-a-dict",
            "03": {"paths": "not-a-dict"},
            "04": {"paths": {"/p": "B"}},
            "05": {"paths": {"/p": "A"}},
        },
    )
    signals = ov._content_oscillation_signals(inst, 5)
    assert len(signals) == 1
    assert signals[0]["path"] == "/p"


def test_breadcrumb_integrity_non_list_rejected_and_non_dict_item(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    write_path_history(inst, {"01": {"rejected": "not-a-list"}})
    assert ov._breadcrumb_integrity_signals(inst, 1) == []

    rejected_with_a_bad_item: list[dict[str, str] | None] = [
        None,
        {"unit_id": "u", "entry": "e", "reason": "r"},
    ]
    write_path_history(inst, {"02": {"rejected": rejected_with_a_bad_item}})
    signals = ov._breadcrumb_integrity_signals(inst, 2)
    assert len(signals) == 1


def test_ask_starvation_non_list_asks_and_malformed_ask_entries(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    cfg = load_config(inst, stall_waves=2)

    write_charter(inst, "not-a-list")  # type: ignore[arg-type]
    assert ov._ask_starvation_signals(cfg, inst, 1, []) == []

    malformed_asks: list[dict[str, object] | None] = [
        None,
        {"ask_id": ""},
        {"no_ask_id": True},
        {"ask_id": "A1"},
    ]
    write_charter(inst, malformed_asks)  # type: ignore[arg-type]
    signals = ov._ask_starvation_signals(cfg, inst, 1, [])
    assert [s["ask_id"] for s in signals] == ["A1"]
