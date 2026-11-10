"""Governance limits on the schedule binding model (T-tPlK1T). Pure model tests: no clock."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml
from pydantic import ValidationError

from agent_orchestrator.schedules import ScheduleFile, ScheduleFileError, ScheduleStore
from agent_orchestrator.schedules.governance import (
    DEFAULT_PAUSE_TTL_SECONDS,
    LIMITS_REQUIRED,
    CostLimits,
    LimitsSpec,
    PeriodCostCap,
    QuotaSpec,
    limits_digest,
)

from ._fixtures import governed_binding, minimal_limits, minimal_limits_data

SRC = "g.yaml"
SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "specs" / "schedules.schema.json").read_text()
)


def row(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "s1",
        "kind": "cron",
        "schedule": "0 2 * * *",
        "workflow": "w.json",
    }
    base.update(over)
    return base


def load(rows: list[dict[str, Any]], **top: Any) -> ScheduleFile:
    return ScheduleFile.from_data({"schedules": rows, **top}, source=SRC)


# -- AC1: model shape and bounds ---------------------------------------------------------------


def test_defaults() -> None:
    c = CostLimits()
    assert c.on_cap == "pause"
    assert c.pause_ttl_seconds == DEFAULT_PAUSE_TTL_SECONDS == 259_200
    assert c.per_run_usd is None
    assert c.periods == []
    assert LimitsSpec().quotas == [] and LimitsSpec().cost is None


@pytest.mark.parametrize(
    "build",
    [
        lambda: QuotaSpec(period="day", max_runs=0),
        lambda: QuotaSpec(period="day", max_runs=10_001),
        lambda: QuotaSpec(period="year", max_runs=1),  # type: ignore[arg-type]
        lambda: QuotaSpec(period="day", max_runs=1, extra=1),  # type: ignore[call-arg]
        lambda: PeriodCostCap(period="day", max_usd=0),
        lambda: PeriodCostCap(period="day", max_usd=100_000.01),
        lambda: PeriodCostCap(period="day", max_usd=float("nan")),
        lambda: PeriodCostCap(period="day", max_usd=float("inf")),
        lambda: CostLimits(per_run_usd=0),
        lambda: CostLimits(per_run_usd=-1),
        lambda: CostLimits(per_run_usd=float("inf")),
        lambda: CostLimits(on_cap="stop"),  # type: ignore[arg-type]
        lambda: CostLimits(pause_ttl_seconds=-1),
        lambda: CostLimits(pause_ttl_seconds=30 * 86400 + 1),
    ],
)
def test_bounds_rejected(build: Any) -> None:
    with pytest.raises(ValidationError):
        build()


def test_bounds_edges_accepted() -> None:
    QuotaSpec(period="month", max_runs=1)
    QuotaSpec(period="week", max_runs=10_000)
    PeriodCostCap(period="day", max_usd=100_000)
    CostLimits(per_run_usd=0.01, pause_ttl_seconds=0)
    CostLimits(pause_ttl_seconds=30 * 86400)


# -- AC2: named validator messages -------------------------------------------------------------


def test_duplicate_quota_period() -> None:
    with pytest.raises(ValidationError, match="duplicate quota period 'day'"):
        LimitsSpec(
            quotas=[QuotaSpec(period="day", max_runs=1), QuotaSpec(period="day", max_runs=2)]
        )


def test_duplicate_cost_period() -> None:
    with pytest.raises(ValidationError, match="duplicate cost period 'week'"):
        CostLimits(
            periods=[
                PeriodCostCap(period="week", max_usd=1),
                PeriodCostCap(period="week", max_usd=2),
            ]
        )


def test_per_run_exceeds_smallest_period() -> None:
    with pytest.raises(ValidationError, match="could never be afforded"):
        CostLimits(
            per_run_usd=5,
            periods=[
                PeriodCostCap(period="day", max_usd=4),
                PeriodCostCap(period="week", max_usd=20),
            ],
        )
    CostLimits(per_run_usd=4, periods=[PeriodCostCap(period="day", max_usd=4)])  # equal is fine


@pytest.mark.parametrize("sha", ["A" * 64, "a" * 63, "a" * 65, "g" * 64, ""])
def test_prompt_sha256_pattern(sha: str) -> None:
    with pytest.raises(ValidationError):
        governed_binding(prompt_file="p.md", prompt_sha256=sha)


def test_prompt_sha256_valid_and_needs_prompt_file() -> None:
    assert governed_binding(prompt_file="p.md", prompt_sha256="a" * 64).prompt_sha256 == "a" * 64
    with pytest.raises(ValidationError, match="prompt_sha256 requires prompt_file"):
        governed_binding(prompt_sha256="a" * 64)


def test_run_args_rejects_cost_cap_usd() -> None:
    # AC11: explicit message, even though the run-options registry allow-lists the option.
    with pytest.raises(ScheduleFileError, match=r"limits\.cost\.per_run_usd") as ei:
        load([row(run_args={"cost_cap_usd": 5}, limits=minimal_limits_data())])
    assert "cost_cap_usd" in str(ei.value)


# -- AC3: D-2 ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "limits",
    [
        pytest.param(None, id="no-limits"),
        pytest.param({}, id="empty-limits"),
        pytest.param({"quotas": []}, id="empty-quotas"),
        pytest.param({"cost": {"on_cap": "pause"}}, id="cost-on_cap-only"),
        pytest.param({"cost": {"periods": []}}, id="cost-empty-periods"),
    ],
)
def test_d2_requires_a_limit(limits: Any) -> None:
    extra = {} if limits is None else {"limits": limits}
    with pytest.raises(ScheduleFileError) as ei:
        load([row(**extra)])
    assert str(ei.value) == f"{SRC}: " + LIMITS_REQUIRED.format(id="s1")
    assert ei.value.schedule_id == "s1"


def test_d2_until_max_runs_is_not_a_limit() -> None:
    with pytest.raises(ScheduleFileError, match="at least one limit"):
        load([row(until={"max_runs": 5})])


def test_d2_satisfied_by_defaults_and_binding() -> None:
    f = load([row()], defaults={"limits": minimal_limits_data()})
    assert f.schedules[0].limits is None
    assert f.bindings[0].limits == minimal_limits()
    load([row(limits={"cost": {"per_run_usd": 1}})])
    load([row(limits={"cost": {"periods": [{"period": "day", "max_usd": 3}]}})])


def test_d2_defaults_alone_without_schedules_is_fine() -> None:
    assert ScheduleFile().schedules == []


def test_d2_binding_empty_limits_does_not_fall_back() -> None:
    # An explicit (empty) binding block replaces defaults wholesale -> still invalid.
    with pytest.raises(ScheduleFileError, match="at least one limit"):
        load([row(limits={})], defaults={"limits": minimal_limits_data()})


# -- AC4: inheritance --------------------------------------------------------------------------


def test_binding_limits_replace_defaults_wholesale() -> None:
    f = load(
        [row(limits={"cost": {"per_run_usd": 2}})],
        defaults={"limits": {"quotas": [{"period": "day", "max_runs": 3}]}},
    )
    eff = f.schedules[0].effective_limits(f.defaults)
    assert eff is not None
    assert eff.quotas == []
    assert eff.cost is not None and eff.cost.per_run_usd == 2
    assert f.bindings[0].limits == eff


def test_week_start_inheritance() -> None:
    f = load(
        [
            row(limits=minimal_limits_data()),
            row(id="s2", week_start="sunday", limits=minimal_limits_data()),
        ],
        defaults={"week_start": "sunday"},
    )
    assert [b.effective_week_start(f.defaults) for b in f.schedules] == ["sunday", "sunday"]
    g = load([row(limits=minimal_limits_data(), week_start="sunday")])
    assert g.schedules[0].effective_week_start(g.defaults) == "sunday"
    h = load([row(limits=minimal_limits_data())])
    assert h.schedules[0].effective_week_start(h.defaults) == "monday"
    assert [b.week_start for b in f.bindings] == ["sunday", "sunday"]


def test_bad_week_start_rejected() -> None:
    with pytest.raises(ScheduleFileError):
        load([row(limits=minimal_limits_data(), week_start="friday")])


# -- AC5: digest -------------------------------------------------------------------------------


def test_digest_stable_across_key_order_and_float_format() -> None:
    a = LimitsSpec.model_validate(
        {
            "quotas": [{"period": "day", "max_runs": 3}],
            "cost": {"per_run_usd": 2, "on_cap": "pause"},
        }
    )
    b = LimitsSpec.model_validate(
        {
            "cost": {"on_cap": "pause", "per_run_usd": 2.0},
            "quotas": [{"max_runs": 3, "period": "day"}],
        }
    )
    assert limits_digest(a) == limits_digest(b)
    assert len(limits_digest(a)) == 64
    # an explicit default equals an omitted one
    c = LimitsSpec.model_validate(
        {"quotas": [{"period": "day", "max_runs": 3}], "cost": {"per_run_usd": 2}}
    )
    assert limits_digest(a) == limits_digest(c)


def test_digest_identity_is_enforcement_not_yaml() -> None:
    """L2: a ``cost`` block declaring no cap is inert, so it must not change the digest."""
    quotas = [{"period": "day", "max_runs": 3}]
    base = limits_digest(LimitsSpec.model_validate({"quotas": quotas}))
    for inert in ({"on_cap": "abort"}, {}, {"pause_ttl_seconds": 5}, {"periods": []}):
        spec = LimitsSpec.model_validate({"quotas": quotas, "cost": inert})
        assert limits_digest(spec) == base, inert
    # ...but once a cap exists, on_cap is enforcement and does matter
    pause = LimitsSpec.model_validate({"cost": {"per_run_usd": 1}})
    abort = LimitsSpec.model_validate({"cost": {"per_run_usd": 1, "on_cap": "abort"}})
    assert limits_digest(pause) != limits_digest(abort)
    # and the digest is computed without mutating the caller's model
    spec = LimitsSpec.model_validate({"quotas": quotas, "cost": {"on_cap": "abort"}})
    limits_digest(spec)
    assert spec.cost is not None and spec.cost.on_cap == "abort"


def test_digest_list_order_irrelevant() -> None:
    q = [{"period": "day", "max_runs": 1}, {"period": "week", "max_runs": 2}]
    p = [{"period": "day", "max_usd": 1}, {"period": "week", "max_usd": 2}]
    a = LimitsSpec.model_validate({"quotas": q, "cost": {"periods": p}})
    b = LimitsSpec.model_validate({"quotas": q[::-1], "cost": {"periods": p[::-1]}})
    assert limits_digest(a) == limits_digest(b)


@pytest.mark.parametrize(
    "other",
    [
        {"quotas": [{"period": "day", "max_runs": 4}], "cost": {"per_run_usd": 2}},
        {"quotas": [{"period": "week", "max_runs": 3}], "cost": {"per_run_usd": 2}},
        {"quotas": [{"period": "day", "max_runs": 3}], "cost": {"per_run_usd": 2.5}},
        {
            "quotas": [{"period": "day", "max_runs": 3}],
            "cost": {"per_run_usd": 2, "on_cap": "abort"},
        },
        {
            "quotas": [{"period": "day", "max_runs": 3}],
            "cost": {"per_run_usd": 2, "pause_ttl_seconds": 1},
        },
        {"quotas": [{"period": "day", "max_runs": 3}]},
    ],
)
def test_digest_differs_on_any_change(other: dict[str, Any]) -> None:
    base = LimitsSpec.model_validate(
        {"quotas": [{"period": "day", "max_runs": 3}], "cost": {"per_run_usd": 2}}
    )
    assert limits_digest(base) != limits_digest(LimitsSpec.model_validate(other))


# -- AC6: JSON-schema parity corpus ------------------------------------------------------------

_Q = {"period": "day", "max_runs": 3}
# (id, document, valid-by-shape). Shape cases: schema and pydantic must agree.
SHAPE_CORPUS: list[tuple[str, dict[str, Any], bool]] = [
    # M1: an integral float is a valid JSON-Schema "integer"; pydantic must agree.
    (
        "ok-max-runs-integral-float",
        {"schedules": [row(limits={"quotas": [{"period": "day", "max_runs": 3.0}]})]},
        True,
    ),
    (
        "ok-pause-ttl-integral-float",
        {"schedules": [row(limits={"cost": {"per_run_usd": 1, "pause_ttl_seconds": 60.0}})]},
        True,
    ),
    (
        "bad-max-runs-fractional",
        {"schedules": [row(limits={"quotas": [{"period": "day", "max_runs": 3.5}]})]},
        False,
    ),
    (
        "bad-max-runs-bool",
        {"schedules": [row(limits={"quotas": [{"period": "day", "max_runs": True}]})]},
        False,
    ),
    ("ok-quota", {"schedules": [row(limits={"quotas": [_Q]})]}, True),
    (
        "ok-cost-full",
        {
            "schedules": [
                row(
                    limits={
                        "cost": {
                            "per_run_usd": 2.5,
                            "on_cap": "abort",
                            "pause_ttl_seconds": 0,
                            "periods": [{"period": "week", "max_usd": 20}],
                        }
                    }
                )
            ]
        },
        True,
    ),
    (
        "ok-defaults-limits",
        {"defaults": {"limits": {"quotas": [_Q]}, "week_start": "sunday"}, "schedules": [row()]},
        True,
    ),
    (
        "ok-sha",
        {"schedules": [row(prompt_file="p.md", prompt_sha256="a" * 64, limits={"quotas": [_Q]})]},
        True,
    ),
    (
        "bad-period",
        {"schedules": [row(limits={"quotas": [{"period": "year", "max_runs": 1}]})]},
        False,
    ),
    (
        "bad-max-runs-0",
        {"schedules": [row(limits={"quotas": [{"period": "day", "max_runs": 0}]})]},
        False,
    ),
    (
        "bad-max-runs-big",
        {"schedules": [row(limits={"quotas": [{"period": "day", "max_runs": 10001}]})]},
        False,
    ),
    (
        "bad-max-runs-str",
        {"schedules": [row(limits={"quotas": [{"period": "day", "max_runs": "3"}]})]},
        False,
    ),
    ("bad-quota-extra", {"schedules": [row(limits={"quotas": [{**_Q, "x": 1}]})]}, False),
    ("bad-limits-extra", {"schedules": [row(limits={"quotas": [_Q], "nope": 1})]}, False),
    ("bad-usd-zero", {"schedules": [row(limits={"cost": {"per_run_usd": 0}})]}, False),
    ("bad-usd-big", {"schedules": [row(limits={"cost": {"per_run_usd": 100001}})]}, False),
    (
        "bad-on_cap",
        {"schedules": [row(limits={"cost": {"per_run_usd": 1, "on_cap": "stop"}})]},
        False,
    ),
    (
        "bad-ttl-neg",
        {"schedules": [row(limits={"cost": {"per_run_usd": 1, "pause_ttl_seconds": -1}})]},
        False,
    ),
    (
        "bad-ttl-big",
        {"schedules": [row(limits={"cost": {"per_run_usd": 1, "pause_ttl_seconds": 2592001}})]},
        False,
    ),
    (
        "bad-sha-short",
        {"schedules": [row(prompt_file="p.md", prompt_sha256="a" * 63, limits={"quotas": [_Q]})]},
        False,
    ),
    (
        "bad-sha-upper",
        {"schedules": [row(prompt_file="p.md", prompt_sha256="A" * 64, limits={"quotas": [_Q]})]},
        False,
    ),
    ("bad-week-start", {"schedules": [row(limits={"quotas": [_Q]}, week_start="friday")]}, False),
    ("bad-defaults-week-start", {"defaults": {"week_start": "friday"}}, False),
]
# Cross-field rules the schema cannot express: pydantic-only (labelled), expected pydantic result.
PYDANTIC_ONLY_CORPUS: list[tuple[str, dict[str, Any], bool]] = [
    # L4 (won't-fix, pinned): Python-``re`` jsonschema lets ``$`` match before a trailing newline
    # (ECMA-262 validators do not); pydantic's Rust regex never does. Fail-closed, so pinned here.
    (
        "sha-trailing-newline",
        {"schedules": [row(prompt_file="p.md", prompt_sha256="a" * 64 + "\n")]},
        False,
    ),
    ("d2-no-limits", {"schedules": [row()]}, False),
    ("d2-empty-limits", {"schedules": [row(limits={})]}, False),
    (
        "d2-inherits-defaults",
        {"defaults": {"limits": {"quotas": [_Q]}}, "schedules": [row()]},
        True,
    ),
    ("dup-quota-period", {"schedules": [row(limits={"quotas": [_Q, _Q]})]}, False),
    (
        "dup-cost-period",
        {"schedules": [row(limits={"cost": {"periods": [{"period": "day", "max_usd": 1}] * 2}})]},
        False,
    ),
    (
        "per-run-gt-period",
        {
            "schedules": [
                row(
                    limits={
                        "cost": {"per_run_usd": 5, "periods": [{"period": "day", "max_usd": 1}]}
                    }
                )
            ]
        },
        False,
    ),
    (
        "sha-without-prompt-file",
        {"schedules": [row(prompt_sha256="a" * 64, limits={"quotas": [_Q]})]},
        False,
    ),
    (
        "run-args-cost-cap",
        {"schedules": [row(run_args={"cost_cap_usd": 5}, limits={"quotas": [_Q]})]},
        False,
    ),
]


def _pydantic_ok(doc: dict[str, Any]) -> bool:
    try:
        ScheduleFile.from_data(doc, source=SRC)
    except ScheduleFileError:
        return False
    return True


def _schema_ok(doc: dict[str, Any]) -> bool:
    return not list(jsonschema.Draft202012Validator(SCHEMA).iter_errors(doc))


def test_corpus_size() -> None:
    assert len(SHAPE_CORPUS) + len(PYDANTIC_ONLY_CORPUS) >= 15


@pytest.mark.parametrize(("name", "doc", "_valid"), SHAPE_CORPUS, ids=[c[0] for c in SHAPE_CORPUS])
def test_schema_and_pydantic_agree_on_shape(name: str, doc: dict[str, Any], _valid: bool) -> None:
    schema_ok = _schema_ok(doc)
    assert schema_ok == _valid, f"schema disagrees on {name}"
    assert _pydantic_ok(doc) == _valid, f"pydantic disagrees on {name}"


@pytest.mark.parametrize(
    ("name", "doc", "valid"),
    PYDANTIC_ONLY_CORPUS,
    ids=["pydantic-only:" + c[0] for c in PYDANTIC_ONLY_CORPUS],
)
def test_pydantic_only_cross_field_rules(name: str, doc: dict[str, Any], valid: bool) -> None:
    assert _pydantic_ok(doc) == valid, name


# -- AC7: store round-trip ---------------------------------------------------------------------


def test_store_roundtrip_byte_for_byte(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path)
    f = ScheduleFile.from_data(
        {
            "defaults": {
                "week_start": "sunday",
                "limits": {"quotas": [{"period": "day", "max_runs": 3}]},
            },
            "schedules": [
                row(
                    prompt_file="p.md",
                    prompt_sha256="b" * 64,
                    week_start="monday",
                    limits={
                        "quotas": [{"period": "week", "max_runs": 10}],
                        "cost": {
                            "per_run_usd": 2.0,
                            "on_cap": "abort",
                            "pause_ttl_seconds": 60,
                            "periods": [{"period": "week", "max_usd": 20.5}],
                        },
                    },
                )
            ],
        },
        source=SRC,
    )
    store.save(f)
    first = store.path.read_bytes()
    loaded = store.load()
    assert loaded == f
    assert loaded.schedules[0].limits == f.schedules[0].limits
    store.save(loaded)
    assert store.path.read_bytes() == first
    on_disk = yaml.safe_load(first)["schedules"][0]
    assert on_disk["prompt_sha256"] == "b" * 64 and on_disk["week_start"] == "monday"
    assert on_disk["limits"]["cost"]["on_cap"] == "abort"


def test_governed_binding_fixture() -> None:
    b = governed_binding(id="x")
    assert b.id == "x" and b.limits == minimal_limits()


def test_integral_float_loads_as_int() -> None:
    """M1: ``3.0`` is accepted *and normalised* to int, so downstream code never sees a float."""
    q = QuotaSpec.model_validate({"period": "day", "max_runs": 3.0})
    assert q.max_runs == 3 and type(q.max_runs) is int
    c = CostLimits.model_validate({"per_run_usd": 1, "pause_ttl_seconds": 60.0})
    assert c.pause_ttl_seconds == 60 and type(c.pause_ttl_seconds) is int
    for bad in ("3", 3.5, True, float("inf"), float("nan"), None):
        with pytest.raises(ValidationError):
            QuotaSpec.model_validate({"period": "day", "max_runs": bad})
