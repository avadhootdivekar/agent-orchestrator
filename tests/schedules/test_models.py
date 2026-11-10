"""Binding model: rejection cases name the schedule id + field; defaults inheritance round-trips."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator import run_options
from agent_orchestrator.schedules import (
    MIN_INTERVAL_SECONDS,
    SCHEDULE_RUN_ARG_NAMES,
    DuplicateScheduleIdError,
    RunArgs,
    ScheduleBinding,
    ScheduleFile,
    ScheduleFileError,
)

SRC = "sched.yaml"


def cron(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "nightly",
        "kind": "cron",
        "schedule": "0 2 * * *",
        "workflow": "workflows/x/workflow.json",
    }
    base.update(over)
    return base


def load(*rows: dict[str, Any], **top: Any) -> ScheduleFile:
    return ScheduleFile.from_data({"schedules": list(rows), **top}, source=SRC)


def reject(row: dict[str, Any], *needles: str) -> str:
    with pytest.raises(ScheduleFileError) as ei:
        load(row)
    msg = str(ei.value)
    assert SRC in msg
    for n in needles:
        assert n in msg, msg
    return msg


class TestRejections:
    def test_unknown_run_args_key(self) -> None:
        reject(cron(run_args={"bogus": 1}), "'nightly'", "run_args.bogus")

    @pytest.mark.parametrize("key", ["cost_cap_usd", "run_id", "sentinel.llm.powers"])
    def test_excluded_run_args_rejected(self, key: str) -> None:
        reject(cron(run_args={key: "x"}), "'nightly'", key)

    @pytest.mark.parametrize("path", ["/etc/passwd", "../up.json", "a/../../b", "~/x"])
    @pytest.mark.parametrize("field", ["workflow", "prompt_file"])
    def test_non_relative_paths(self, field: str, path: str) -> None:
        row = cron(**{field: path})
        if field == "prompt_file":
            row["prompt_file"] = path
            row["workflow"] = "w.json"
        reject(row, "'nightly'", field, "workspace-relative")

    def test_until_and_watch_and_secret_file_paths(self) -> None:
        reject(cron(until={"gate_file": "/abs"}), "until.gate_file")
        reject(cron(until={"artifact_exists": "../x"}), "until.artifact_exists")
        reject(
            {"id": "w", "kind": "file_watch", "workflow": "w.json", "watch": {"paths": ["../a"]}},
            "'w'",
            "watch.paths",
        )
        reject(
            {
                "id": "h",
                "kind": "webhook",
                "workflow": "w.json",
                "webhook": {"secret_file": "/etc/s"},
            },
            "'h'",
            "webhook.secret_file",
        )

    def test_bad_cron(self) -> None:
        reject(cron(schedule="not a cron"), "'nightly'", "schedule", "cron")

    def test_bad_timezone(self) -> None:
        reject(cron(timezone="Mars/Olympus"), "'nightly'", "timezone", "Mars/Olympus")

    def test_bad_default_timezone(self) -> None:
        with pytest.raises(ScheduleFileError, match=r"defaults\.timezone"):
            ScheduleFile.from_data({"defaults": {"timezone": "Nope/Zone"}}, source=SRC)

    def test_duplicate_id(self) -> None:
        with pytest.raises(DuplicateScheduleIdError) as ei:
            load(cron(), cron())
        assert "'nightly'" in str(ei.value)
        assert ei.value.schedule_id == "nightly"

    @pytest.mark.parametrize("secs", [0, 59, -5])
    def test_interval_too_small(self, secs: int) -> None:
        row = {"id": "tick", "kind": "interval", "interval_seconds": secs, "workflow": "w.json"}
        reject(row, "'tick'", "interval_seconds", str(MIN_INTERVAL_SECONDS))

    def test_interval_at_minimum_ok(self) -> None:
        f = load({"id": "tick", "kind": "interval", "interval_seconds": 60, "workflow": "w.json"})
        assert f.schedules[0].interval_seconds == 60

    def test_inline_secret(self) -> None:
        row = {
            "id": "hook",
            "kind": "webhook",
            "workflow": "w.json",
            "webhook": {"secret": "hunter2"},
        }
        msg = reject(row, "'hook'", "secret_env", "secret_file")
        assert "hunter2" not in msg

    def test_webhook_needs_exactly_one_secret_source(self) -> None:
        base = {"id": "hook", "kind": "webhook", "workflow": "w.json"}
        reject({**base, "webhook": {}}, "'hook'", "exactly one")
        reject(
            {**base, "webhook": {"secret_env": "A", "secret_file": "s"}}, "'hook'", "exactly one"
        )

    @pytest.mark.parametrize(
        ("row", "field"),
        [
            ({"kind": "cron", "workflow": "w"}, "schedule"),
            ({"kind": "interval", "workflow": "w"}, "interval_seconds"),
            ({"kind": "file_watch", "workflow": "w"}, "watch"),
            ({"kind": "webhook", "workflow": "w"}, "webhook"),
            ({"kind": "cron", "schedule": "* * * * *"}, "workflow"),
            (
                {"kind": "cron", "schedule": "* * * * *", "workflow": "w", "template": "t"},
                "workflow",
            ),
            (
                {"kind": "cron", "schedule": "* * * * *", "workflow": "w", "params": {"a": "b"}},
                "params",
            ),
            (
                {"kind": "cron", "schedule": "* * * * *", "workflow": "w", "instance_id": "x"},
                "instance_id",
            ),
            (
                {
                    "kind": "interval",
                    "interval_seconds": 90,
                    "schedule": "* * * * *",
                    "workflow": "w",
                },
                "schedule",
            ),
        ],
    )
    def test_cross_field(self, row: dict[str, Any], field: str) -> None:
        reject({"id": "bad-one", **row}, "'bad-one'", f"'{field}'")

    @pytest.mark.parametrize("sid", ["Upper", "-lead", "has space", "a" * 65, ""])
    def test_bad_ids(self, sid: str) -> None:
        with pytest.raises(ScheduleFileError, match="id"):
            load(cron(id=sid))

    def test_id_len_64_ok(self) -> None:
        assert load(cron(id="a" * 64)).schedules[0].id == "a" * 64

    @pytest.mark.parametrize("tpl", ["../t", "a/b", "a.b", "~/t", "/abs/t"])
    def test_template_must_be_bare_name(self, tpl: str) -> None:
        row = cron(template=tpl)
        del row["workflow"]
        reject(row, "'nightly'", "template")

    @pytest.mark.parametrize("j", [-1, 3601])
    def test_jitter_range(self, j: int) -> None:
        reject(cron(jitter_seconds=j), "'nightly'", "jitter_seconds")

    @pytest.mark.parametrize("iid", ["a/b", "{bogus}", "../x", "{date!r}", "{", "{date:>5}"])
    def test_instance_id_shape(self, iid: str) -> None:
        reject(
            {
                "id": "t",
                "kind": "cron",
                "schedule": "* * * * *",
                "template": "epic-runner",
                "instance_id": iid,
            },
            "'t'",
            "instance_id",
        )

    def test_unknown_binding_key_and_bad_enum(self) -> None:
        reject(cron(surprise=1), "'nightly'", "surprise")
        reject(cron(overlap="maybe"), "'nightly'", "overlap")

    def test_unsupported_version(self) -> None:
        with pytest.raises(ScheduleFileError, match="version"):
            ScheduleFile.from_data({"version": "2.0"}, source=SRC)

    def test_empty_until_gate_equals_types(self) -> None:
        f = load(cron(until={"gate_file": "c/s.json", "gate_equals": "done", "max_runs": 3}))
        assert f.schedules[0].until is not None
        assert f.schedules[0].until.gate_equals == "done"
        assert load(cron(until={"max_runs": 1})).schedules[0].until.gate_equals is True  # type: ignore[union-attr]

    def test_file_level_error_without_schedule(self) -> None:
        with pytest.raises(ScheduleFileError, match="schedules"):
            ScheduleFile.from_data({"schedules": "nope"}, source=SRC)

    def test_missing_id_names_index(self) -> None:
        with pytest.raises(ScheduleFileError, match="#0"):
            load({"kind": "cron", "schedule": "* * * * *", "workflow": "w"})


class TestRunArgs:
    def test_names_are_registry_derived(self) -> None:
        reg = set(run_options.allowed_options()) | set(run_options.allowed_bool_options())
        assert set(SCHEDULE_RUN_ARG_NAMES) <= reg
        assert not any(n.startswith("sentinel.llm") for n in SCHEDULE_RUN_ARG_NAMES)
        assert "sentinel.max_parallel_ceiling" not in SCHEDULE_RUN_ARG_NAMES

    def test_as_options_uses_registry_names_and_omits_unset(self) -> None:
        ra = RunArgs.model_validate(
            {"max_parallel": 4, "model": "m", "sentinel.enabled": True, "effort": "high"}
        )
        assert ra.as_options() == {
            "max_parallel": 4,
            "model": "m",
            "sentinel.enabled": True,
            "effort": "high",
        }
        assert RunArgs().as_options() == {}

    def test_effort_choices_come_from_registry(self) -> None:
        assert "xhigh" not in run_options.get_option("effort").choices
        reject(cron(run_args={"effort": "xhigh"}), "run_args.effort")
        assert RunArgs.model_validate({"turn_budget": "xhigh"}).as_options() == {
            "turn_budget": "xhigh"
        }

    def test_type_checked(self) -> None:
        reject(cron(run_args={"max_parallel": "lots"}), "run_args.max_parallel")


class TestDefaultsAndRoundTrip:
    def test_inheritance_resolved_once(self) -> None:
        f = load(
            cron(id="a"),
            cron(id="b", timezone="Europe/Berlin", overlap="allow", jitter_seconds=5),
            defaults={"timezone": "Asia/Tokyo", "overlap": "queue", "max_concurrent": 3},
        )
        a, b = f.bindings
        assert (a.timezone, a.overlap, a.max_concurrent, a.catch_up) == (
            "Asia/Tokyo",
            "queue",
            3,
            "once",
        )
        assert (b.timezone, b.overlap, b.jitter_seconds, b.grace_seconds) == (
            "Europe/Berlin",
            "allow",
            5,
            60,
        )
        # raw bindings stay unresolved
        assert f.schedules[0].timezone is None

    def test_roundtrip_full(self) -> None:
        f = load(
            cron(
                run_args={"max_parallel": 2, "sentinel.enabled": False},
                until={"gate_file": "g.json", "gate_equals": 1, "max_runs": 9},
                jitter_seconds=10,
                catch_up="skip",
                enabled=False,
            ),
            {
                "id": "w",
                "kind": "file_watch",
                "workflow": "w.json",
                "watch": {"paths": ["a/*.md"], "on": ["created"], "debounce_seconds": 0},
            },
            {
                "id": "h",
                "kind": "webhook",
                "workflow": "w.json",
                "webhook": {"secret_env": "HOOK_SECRET"},
            },
            {
                "id": "t",
                "kind": "interval",
                "interval_seconds": 3600,
                "template": "epic-runner",
                "params": {"type": "testing"},
                "instance_id": "nightly-{date}-{rand6}",
                "prompt_file": "p.md",
            },
            defaults={"timezone": "UTC", "grace_seconds": 5},
        )
        again = ScheduleFile.from_data(json.loads(json.dumps(f.to_data())), source=SRC)
        assert again == f

    def test_roundtrip_minimal_binding(self) -> None:
        f = load({"id": "m", "kind": "cron", "schedule": "* * * * *", "workflow": "w"})
        assert ScheduleFile.from_data(f.to_data()) == f


class TestToTrigger:
    def test_cron_and_interval(self) -> None:
        c = ScheduleBinding.model_validate(cron(timezone="Europe/Berlin")).to_trigger()
        assert (c.type, c.schedule, c.timezone) == ("cron", "0 2 * * *", "Europe/Berlin")
        i = ScheduleBinding.model_validate(
            {"id": "i", "kind": "interval", "interval_seconds": 120, "workflow": "w"}
        ).to_trigger()
        assert (i.type, i.interval_seconds) == ("interval", 120)

    def test_event_kinds_raise(self) -> None:
        b = ScheduleBinding.model_validate(
            {"id": "h", "kind": "webhook", "workflow": "w", "webhook": {"secret_env": "S"}}
        )
        with pytest.raises(ValueError, match="TriggerSource"):
            b.to_trigger()


def _normalize(node: Any) -> Any:
    """Sort ``enum`` lists: pydantic emits ``Literal`` choices in hash (per-process) order."""
    if isinstance(node, dict):
        return {k: sorted(v) if k == "enum" else _normalize(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_normalize(v) for v in node]
    return node


def test_checked_in_schema_matches_model() -> None:
    path = Path(__file__).resolve().parents[2] / "specs" / "schedules.schema.json"
    assert _normalize(json.loads(path.read_text())) == _normalize(
        ScheduleFile.model_json_schema(by_alias=True)
    ), (
        "regenerate: python -c 'import json;from agent_orchestrator.schedules import ScheduleFile;"
        "print(json.dumps(ScheduleFile.model_json_schema(by_alias=True),indent=2))' "
        "> specs/schedules.schema.json"
    )
