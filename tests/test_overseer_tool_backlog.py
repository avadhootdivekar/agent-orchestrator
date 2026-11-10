"""T-Sn7Bkl overseer-tool tests: backlog schema/checker/publication, carry-over, soft barrier.

Reuses the fixture helpers of ``test_overseer_tool_checker_semantic`` (same tool module load).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.test_overseer_tool_checker_semantic import (
    _INSTANCE_DIR,
    make_args,
    make_config_dict,
    make_next_ckpt_entry,
    make_unit_entry,
    mutate_digest,
    ov,
    rule_ids_of,
    run_r11_case,
    setup_r11_baseline,
    write_brief,
    write_manifest_entries,
    write_state,
    write_verdict,
)

K = 1


def _entry(slug: str = "extra", **kw: Any) -> dict[str, Any]:
    e: dict[str, Any] = {
        "id": slug,
        "priority": 3,
        "brief_path": f"{_INSTANCE_DIR}/outputs/waves/w02/briefs/{slug}.json",
        "kind": "implement",
        "ask_ids": ["A1"],
        "work_item": f"A1/{slug}",
        "rationale": "independent follow-up that fills idle slots",
        "expires_at_ckpt": 2,
    }
    e.update(kw)
    return e


def _write_backlog(inst: Path, entries: list[Any], **kw: Any) -> None:
    body = {"schema": ov.BACKLOG_SCHEMA, "checkpoint_id": "ck-01", "entries": entries, **kw}
    (inst / "outputs" / "backlog.json").write_text(json.dumps(body))


def _with_briefs(inst: Path, *entries: dict[str, Any], **brief_kw: Any) -> None:
    for e in entries:
        write_brief(inst, 2, e["id"], work_item=e["work_item"], kind=e["kind"], **brief_kw)


def _case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str = "explore"
) -> tuple[Path, Any]:
    inst = run_r11_case(
        tmp_path, tmp_path, monkeypatch, stage=stage, new_work_item="A1/wave", new_kind="implement"
    )
    write_verdict(inst, K, stage=stage)  # a decided checkpoint (publication needs a verdict)
    return inst, ov.load_config(inst)


def _check(inst: Path, cfg: Any, verdict: Any = None, manifest: Any = None, **dg: Any) -> Any:
    digest = ov._load_digest_for_check(inst, K)
    digest.update(dg)
    tasks = manifest if manifest is not None else []
    return ov.check_backlog(
        cfg,
        inst,
        _INSTANCE_DIR,
        K,
        verdict,
        digest,
        ov._read_optional_json(inst / "outputs" / "charter.json"),
        tasks,
    )


def _rules(result: Any) -> set[str]:
    return rule_ids_of(result[0])


# ---- schema / checker ----------------------------------------------------------------------
def test_absent_backlog_is_no_op(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inst, cfg = _case(tmp_path, monkeypatch)
    assert _check(inst, cfg) == ([], None)


def test_valid_backlog_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inst, cfg = _case(tmp_path, monkeypatch)
    e = _entry()
    _with_briefs(inst, e)
    _write_backlog(inst, [e])
    violations, data = _check(inst, cfg)
    assert violations == [] and data is not None


@pytest.mark.parametrize(
    ("mutate", "rule"),
    [
        (lambda e: e.update(id="Bad_ID"), "BL2"),
        (lambda e: e.pop("rationale"), "BL2"),
        (lambda e: e.update(surprise=1), "BL2"),
        (lambda e: e.update(priority=-1), "BL2"),
        (lambda e: e.update(priority=True), "BL2"),
        (lambda e: e.update(expires_at_ckpt=1), "BL5"),  # already expired when published
        (lambda e: e.update(expires_at_ckpt=9), "BL5"),  # too far out
        (lambda e: e.update(kind="stabilize"), "BL3"),
        (lambda e: e.update(ask_ids=[]), "BL4"),
        (lambda e: e.update(ask_ids=["A9"]), "BL4"),
        (lambda e: e.update(brief_path="elsewhere/brief.json"), "BL4"),
        (lambda e: e.update(work_item="A1/wave"), "BL6"),  # same work item as this wave's unit
    ],
)
def test_invalid_entries_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutate: Any, rule: str
) -> None:
    inst, cfg = _case(tmp_path, monkeypatch)
    e = _entry()
    _with_briefs(inst, e)
    mutate(e)
    _write_backlog(inst, [e])
    wave_unit = make_unit_entry(2, 1, "new", emitter="ck-01")
    assert rule in _rules(_check(inst, cfg, manifest=[wave_unit]))


def test_missing_or_mismatching_brief_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inst, cfg = _case(tmp_path, monkeypatch)
    e = _entry()
    _write_backlog(inst, [e])
    assert "BL4" in _rules(_check(inst, cfg))  # brief missing
    write_brief(inst, 2, "extra", work_item="A1/other", kind="implement")
    assert "BL4" in _rules(_check(inst, cfg))  # brief work_item differs


def test_file_level_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inst, cfg = _case(tmp_path, monkeypatch)
    _write_backlog(inst, [], checkpoint_id="ck-07")
    assert "BL1" in _rules(_check(inst, cfg))
    (inst / "outputs" / "backlog.json").write_text(json.dumps({"schema": "nope", "entries": []}))
    assert "BL1" in _rules(_check(inst, cfg))
    (inst / "outputs" / "backlog.json").write_text("{broken")
    assert "BL1" in _rules(_check(inst, cfg))
    _write_backlog(inst, [_entry(f"item-{i}") for i in range(21)])
    assert "BL1" in _rules(_check(inst, cfg))  # entry cap
    e = _entry()
    _write_backlog(inst, [e, dict(e)])
    _with_briefs(inst, e)
    assert "BL2" in _rules(_check(inst, cfg))  # duplicate id


def test_duplicate_entry_id_and_stage_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inst, cfg = _case(tmp_path, monkeypatch, stage="converge")
    e = _entry()  # new work item => converge forbids new scope
    _with_briefs(inst, e)
    _write_backlog(inst, [e])
    assert "BL3" in _rules(_check(inst, cfg))
    inst2, cfg2 = _case(tmp_path / "s", monkeypatch, stage="stabilize")
    v = _entry("verifyit", kind="verify", work_item="A1/parser")
    _with_briefs(inst2, v)
    _write_backlog(inst2, [v])
    assert _check(inst2, cfg2)[0] == []  # verify of an existing item is allowed in stabilize


@pytest.mark.parametrize("decision", ["closeout", "hold"])
def test_backlog_must_be_empty_when_closing_or_holding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, decision: str
) -> None:
    inst, cfg = _case(tmp_path, monkeypatch)
    e = _entry()
    _with_briefs(inst, e)
    _write_backlog(inst, [e])
    assert "BL7" in _rules(_check(inst, cfg, verdict={"decision": decision}))
    assert "BL7" in _rules(_check(inst, cfg, must_close=True))


def test_attempt_cap_blocks_backlog_item(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_overseer_tool_checker_semantic import append_ledger_unit

    inst, cfg = _case(tmp_path, monkeypatch)
    for n in range(cfg.max_attempts_per_item):
        append_ledger_unit(
            inst,
            wave=1,
            unit_id=f"w01-0{n + 5}-x",
            work_item="A1/extra",
            kind="implement",
            unit_verdict="fail",
            outcome="blocked",
        )
    e = _entry()
    _with_briefs(inst, e)
    _write_backlog(inst, [e])
    assert "BL6" in _rules(_check(inst, cfg))


# ---- carry-over (never re-scheduled) ---------------------------------------------------------
CARRY = [{"unit_id": "w01-02-slow", "wave": 1, "status": "running", "work_item": "A1/slow"}]


def test_carry_over_work_never_rescheduled_by_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inst, cfg_dict, emitter, next_wave = setup_r11_baseline(
        tmp_path, tmp_path, monkeypatch, stage="explore"
    )
    mutate_digest(inst, K, carry_over=CARRY)
    unit_id = "w02-01-redo"
    write_brief(inst, 2, unit_id, work_item="A1/slow", kind="implement")
    write_manifest_entries(
        inst,
        "ck-01.json",
        [
            make_unit_entry(2, 1, "redo", emitter="ck-01"),
            make_next_ckpt_entry(2, [unit_id], emitter="ck-01", cfg_dict=cfg_dict),
        ],
    )
    with pytest.raises(ov.CheckViolations) as exc:
        ov.ckpt_check(make_args(tmp_path))
    assert "R17" in rule_ids_of(exc.value.violations)


def test_carry_over_different_work_item_is_fine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inst, cfg_dict, *_ = setup_r11_baseline(tmp_path, tmp_path, monkeypatch, stage="explore")
    mutate_digest(inst, K, carry_over=CARRY)
    write_brief(inst, 2, "w02-01-new", work_item="A1/fresh", kind="implement")
    write_manifest_entries(
        inst,
        "ck-01.json",
        [
            make_unit_entry(2, 1, "new", emitter="ck-01"),
            make_next_ckpt_entry(2, ["w02-01-new"], emitter="ck-01", cfg_dict=cfg_dict),
        ],
    )
    ov.ckpt_check(make_args(tmp_path))


def test_backlog_may_not_target_carry_over_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inst, cfg = _case(tmp_path, monkeypatch)
    e = _entry("slow", work_item="A1/slow")
    _with_briefs(inst, e)
    _write_backlog(inst, [e])
    assert "BL6" in _rules(_check(inst, cfg, carry_over=CARRY))


def test_carry_over_entries_only_unsettled_and_only_through_k(tmp_path: Path) -> None:
    inst = tmp_path / "inst"
    write_brief(inst, 1, "w01-02-slow", work_item="A1/slow", kind="implement")
    ws = tmp_path / "ws"
    p = write_state(
        ws,
        "r",
        {
            "w01-02-slow": {"status": "running"},
            "w01-03-done": {"status": "succeeded"},
            "w02-01-late": {"status": "running"},
        },
    )
    data = json.loads(p.read_text())
    data["sentinel"] = {
        "enabled": True,
        "early_checkpoints": [
            {"checkpoint_id": "ck-01", "carry_over": ["w01-02-slow", "w01-03-done"]},
            {"checkpoint_id": "ck-03", "carry_over": ["w02-01-late"]},
        ],
    }
    p.write_text(json.dumps(data))
    state = ov.load_state(p)
    got = ov.carry_over_entries(inst, state, 2)
    assert [(c["unit_id"], c["work_item"]) for c in got] == [("w01-02-slow", "A1/slow")]
    assert [c["unit_id"] for c in ov.carry_over_entries(inst, state, 3)] == [
        "w01-02-slow",
        "w02-01-late",
    ]


# ---- soft barrier on checkpoints -------------------------------------------------------------
def _barrier_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, sentinel: bool, barrier: str | None
) -> None:
    inst, cfg_dict, *_ = setup_r11_baseline(tmp_path, tmp_path, monkeypatch, stage="explore")
    if sentinel:
        p = tmp_path / ".orchestrator" / "runs" / "run-1" / "state.json"
        data = json.loads(p.read_text())
        data["sentinel"] = {"enabled": True}
        p.write_text(json.dumps(data))
    write_brief(inst, 2, "w02-01-new", work_item="A1/parser", kind="implement")
    extra = {"barrier": barrier} if barrier else {}
    write_manifest_entries(
        inst,
        "ck-01.json",
        [
            make_unit_entry(2, 1, "new", emitter="ck-01"),
            make_next_ckpt_entry(2, ["w02-01-new"], emitter="ck-01", cfg_dict=cfg_dict, **extra),
        ],
    )


@pytest.mark.parametrize(
    ("sentinel", "barrier", "ok"),
    [(True, "soft", True), (True, None, False), (False, None, True), (False, "soft", False)],
)
def test_checkpoint_barrier_soft_iff_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sentinel: bool, barrier: str | None, ok: bool
) -> None:
    _barrier_case(tmp_path, monkeypatch, sentinel=sentinel, barrier=barrier)
    if ok:
        ov.ckpt_check(make_args(tmp_path, dry_run=True))
    else:
        with pytest.raises(ov.CheckViolations) as exc:
            ov.ckpt_check(make_args(tmp_path, dry_run=True))
        assert "R10" in rule_ids_of(exc.value.violations)


# ---- publication ---------------------------------------------------------------------------
def _published(tmp_path: Path) -> dict[str, Any]:
    return json.loads(
        (tmp_path / ".orchestrator" / "runs" / "run-1" / "control" / "backlog.json").read_text()
    )


def test_ckpt_check_publishes_engine_ready_backlog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inst, cfg = _case(tmp_path, monkeypatch)
    a, b = _entry("alpha"), _entry("beta", priority=7)
    _with_briefs(inst, a, b)
    _write_backlog(inst, [a, b])
    ov.ckpt_check(make_args(tmp_path))
    pub = _published(tmp_path)
    assert pub["schema"] == ov.PUBLISHED_BACKLOG_SCHEMA and pub["checkpoint_id"] == "ck-01"
    assert pub["stage"] == "explore" and "A1/parser" in pub["known_work_items"]
    ids = [e["task"]["id"] for e in pub["entries"]]
    assert ids == ["s-01-alpha", "s-02-beta"]
    t = pub["entries"][1]["task"]
    assert t["depends_on"] == [] and t["pre_hook"] == {"use": "ov-unit-gate"}
    assert t["agent"] == "developer" and t["outputs"][1].endswith("progress/s-02-beta.json")
    assert f"{_INSTANCE_DIR}/outputs/waves/w02/briefs/s-02-beta.json" in t["inputs"]
    assert pub["entries"][1]["priority"] == 7 and pub["entries"][1]["expires_at_ckpt"] == 2
    brief = json.loads(ov.brief_path(inst, 2, "s-02-beta").read_text())
    assert brief["unit_id"] == "s-02-beta" and brief["work_item"] == "A1/beta"


def test_republish_keeps_ids_and_empty_backlog_clears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inst, cfg = _case(tmp_path, monkeypatch)
    a = _entry("alpha")
    _with_briefs(inst, a)
    _write_backlog(inst, [a])
    ov.ckpt_check(make_args(tmp_path))
    ids1 = [e["task"]["id"] for e in _published(tmp_path)["entries"]]
    ov.ckpt_check(make_args(tmp_path))  # re-confirmed: same unit id, no renumbering
    assert [e["task"]["id"] for e in _published(tmp_path)["entries"]] == ids1
    (inst / "outputs" / "backlog.json").unlink()
    ov.ckpt_check(make_args(tmp_path))  # no backlog.json => previous entries die
    assert _published(tmp_path)["entries"] == []


def test_default_off_publishes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _case(tmp_path, monkeypatch)
    ov.ckpt_check(make_args(tmp_path))
    assert not (tmp_path / ".orchestrator" / "runs" / "run-1" / "control").exists()


def test_dry_run_publishes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inst, cfg = _case(tmp_path, monkeypatch)
    a = _entry("alpha")
    _with_briefs(inst, a)
    _write_backlog(inst, [a])
    ov.ckpt_check(make_args(tmp_path, dry_run=True))
    assert not (tmp_path / ".orchestrator" / "runs" / "run-1" / "control").exists()


# ---- state / ledger plumbing ------------------------------------------------------------------
def test_load_state_excludes_sentinel_injections_from_fanout_count(tmp_path: Path) -> None:
    p = write_state(tmp_path, "r", {"a": {"status": "succeeded"}, "s-01-x": {"status": "running"}})
    data = json.loads(p.read_text())
    data["tasks"]["s-01-x"]["origin"] = "sentinel"
    p.write_text(json.dumps(data))
    assert ov.load_state(p).injected_ids == ["a"]


def test_late_units_and_ingest_use_their_own_wave(tmp_path: Path) -> None:
    inst = tmp_path / "inst"
    ws = tmp_path / "ws"
    write_brief(inst, 1, "w01-02-slow", work_item="A1/slow", kind="implement")
    write_brief(inst, 2, "s-01-extra", work_item="A1/extra", kind="implement")
    for uid in ("w01-02-slow", "s-01-extra"):
        (inst / "outputs" / "progress").mkdir(parents=True, exist_ok=True)
        (inst / "outputs" / "progress" / f"{uid}.json").write_text(
            json.dumps({"unit_id": uid, "outcome": "done", "verdict": "pass"})
        )
    p = write_state(
        ws,
        "r",
        {
            "w01-02-slow": {"status": "succeeded"},
            "s-01-extra": {"status": "succeeded"},
            "w03-01-now": {"status": "running"},
        },
    )
    state = ov.load_state(p)
    late = ov.late_units(inst, state, 3, set())
    assert late == {"w01-02-slow": 1, "s-01-extra": 2}
    ov.ingest_ledger(
        inst, sorted(late), state, 3, ov.parse_now("2026-01-01T00:00:00+00:00"), unit_waves=late
    )
    waves = {
        ln["unit_id"]: ln["wave"] for ln in ov.read_ledger_lines(inst) if ln.get("type") == "unit"
    }
    assert waves == {"w01-02-slow": 1, "s-01-extra": 2}
    assert ov.late_units(inst, state, 3, set(waves)) == {}  # idempotent


def test_config_dict_unchanged_by_feature() -> None:
    # The config schema gained no required key: old configs keep loading.
    assert "sentinel_enabled" not in make_config_dict()
