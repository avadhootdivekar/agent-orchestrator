"""IntervalScheduler with a fixed clock, and the additive ``Trigger`` / schema changes."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jsonschema
import pytest

from agent_orchestrator.models import Trigger
from agent_orchestrator.scheduler import IntervalScheduler

T0 = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def test_unset_interval_is_none() -> None:
    assert IntervalScheduler().next_fire(Trigger(type="interval"), T0) is None


def test_series_is_evenly_spaced_when_anchored_on_prior_result() -> None:
    s, trig = IntervalScheduler(), Trigger(type="interval", interval_seconds=90)
    fires = [T0]
    for _ in range(3):
        nxt = s.next_fire(trig, fires[-1])
        assert nxt is not None
        fires.append(nxt)
    assert [b - a for a, b in zip(fires, fires[1:], strict=False)] == [timedelta(seconds=90)] * 3


def test_default_triggers_still_manual() -> None:
    from agent_orchestrator.models import WorkflowSpec

    assert WorkflowSpec.model_fields["triggers"].default == [Trigger(type="manual")]


@pytest.fixture(scope="module")
def trigger_schema() -> dict[str, object]:
    spec = Path(__file__).resolve().parents[2] / "specs" / "workflow.schema.json"
    full = json.loads(spec.read_text())
    return {"$defs": full["$defs"], "$ref": "#/$defs/trigger"}


def test_schema_accepts_interval_and_requires_seconds(trigger_schema: dict[str, object]) -> None:
    jsonschema.validate({"type": "interval", "interval_seconds": 60}, trigger_schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"type": "interval"}, trigger_schema)
    jsonschema.validate({"type": "manual"}, trigger_schema)
