"""T-gDNjN2 AC-20 (U-RC1..U-RC3): the `ResultCacheRecord` builders (HLD 8.6.3, D14 / D35)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

import agent_orchestrator.cache.records as records
from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.cache.constants import (
    ENTRY_SCHEMA,
    MAX_ENTRY_COST_USD,
    MAX_ENTRY_SECONDS,
    MAX_ENTRY_TOKENS,
    MAX_REASON_CHARS,
    MAX_TEXT_CHARS,
    MODE_ON,
    MODE_SHADOW,
    REASON_NOT_FOUND,
    REASON_REPO_WORKTREE_PROBE_FAILED,
    SOURCE_ENV,
)
from agent_orchestrator.cache.types import (
    CacheEntry,
    EntrySource,
    EntryUsage,
    KeySummary,
    LookupRequest,
    OutputRecord,
)
from agent_orchestrator.models import (
    RESULT_CACHE_HIT,
    RESULT_CACHE_INELIGIBLE,
    RESULT_CACHE_MISS,
    RESULT_CACHE_WOULD_HIT,
    AgentSpec,
    ResultCacheRecord,
    TaskSpec,
    WorkflowSpec,
)
from tests.cache.fakes import key_of, make_entry, sha_of

NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)
CYCLE = 3
KEY = key_of("records")


@pytest.fixture
def req(tmp_path: Path) -> LookupRequest:
    task = TaskSpec(id="t", agent="a", instruction="i.md", outputs=["o.md"])
    return LookupRequest(
        task=task,
        workflow=WorkflowSpec(version="1.0", id="wf", repo_set="rs", tasks=[task]),
        agents={"a": AgentSpec(executor="fake")},
        run_id="r1",
        injected=False,
        integration_active=False,
        artifact_store=LocalFsArtifactStore(str(tmp_path)),
        general_instruction_paths=(),
        dynamic_input_paths=(),
        repo_paths={},
        dispatch_cycle=CYCLE,
        now=NOW,
    )


class TestHitAndWouldHit:
    def test_u_rc1_hit_record_fields(self, req: LookupRequest) -> None:
        entry = make_entry(KEY, cost_usd=0.4123)
        rec = records.make_hit_record(entry, KEY, req, MODE_ON, SOURCE_ENV)
        assert rec.outcome == RESULT_CACHE_HIT
        assert rec.hit is True
        assert (rec.mode, rec.mode_source, rec.key) == (MODE_ON, SOURCE_ENV, KEY)
        assert rec.dispatch_cycle == CYCLE
        assert rec.at == NOW.isoformat()
        assert rec.ended_at is None  # the engine fills it (D14)
        assert rec.stored is False and rec.store_reason is None
        assert rec.saved_cost_usd == entry.usage.cost_usd
        assert rec.saved_input_tokens == 10 and rec.saved_output_tokens == 5
        assert rec.saved_tokens == 15  # derived: input + output (D35)
        assert rec.saved_seconds == entry.usage.duration_seconds
        assert rec.source_run_id == entry.source.run_id
        assert rec.reason is None

    def test_u_rc1_would_hit_record_carries_store_reason(self, req: LookupRequest) -> None:
        entry = make_entry(KEY)
        rec = records.make_would_hit_record(
            entry,
            KEY,
            req,
            MODE_SHADOW,
            SOURCE_ENV,
            store_reason=REASON_REPO_WORKTREE_PROBE_FAILED,
        )
        assert rec.outcome == RESULT_CACHE_WOULD_HIT
        assert rec.hit is False
        assert rec.mode == MODE_SHADOW
        assert rec.store_reason == REASON_REPO_WORKTREE_PROBE_FAILED
        assert rec.stored is False
        assert rec.saved_cost_usd == entry.usage.cost_usd

    def test_would_hit_store_reason_defaults_to_none(self, req: LookupRequest) -> None:
        rec = records.make_would_hit_record(make_entry(KEY), KEY, req, MODE_SHADOW, SOURCE_ENV)
        assert rec.store_reason is None

    def test_builders_do_not_mutate_the_entry_or_request(self, req: LookupRequest) -> None:
        entry = make_entry(KEY)
        before = entry.model_dump_json()
        records.make_hit_record(entry, KEY, req, MODE_ON, SOURCE_ENV)
        assert entry.model_dump_json() == before


class TestMissAndIneligible:
    def test_u_rc2_miss_record_fields(self, req: LookupRequest) -> None:
        rec = records.make_miss_record(
            req,
            REASON_NOT_FOUND,
            KEY,
            MODE_ON,
            SOURCE_ENV,
            "some detail",
            store_reason=REASON_REPO_WORKTREE_PROBE_FAILED,
        )
        assert rec.outcome == RESULT_CACHE_MISS
        assert rec.hit is False
        assert (rec.reason, rec.reason_detail, rec.key) == (REASON_NOT_FOUND, "some detail", KEY)
        assert rec.dispatch_cycle == CYCLE and rec.at == NOW.isoformat()
        assert (rec.mode, rec.mode_source) == (MODE_ON, SOURCE_ENV)
        assert rec.store_reason == REASON_REPO_WORKTREE_PROBE_FAILED
        assert rec.saved_cost_usd == 0 and rec.saved_tokens == 0

    def test_miss_without_a_key_or_detail(self, req: LookupRequest) -> None:
        rec = records.make_miss_record(req, "store_error", None, MODE_ON, SOURCE_ENV)
        assert rec.key is None and rec.reason_detail is None and rec.store_reason is None

    def test_u_rc2_texts_are_clipped_to_the_model_bounds(self, req: LookupRequest) -> None:
        rec = records.make_miss_record(
            req,
            "r" * 500,
            KEY,
            MODE_ON,
            SOURCE_ENV,
            "d" * 5000,
            store_reason="s" * 500,
        )
        assert rec.reason == "r" * MAX_REASON_CHARS
        assert rec.reason_detail == "d" * MAX_TEXT_CHARS
        assert rec.store_reason == "s" * MAX_REASON_CHARS
        # and the clipped record survives a state.json round trip
        assert ResultCacheRecord.model_validate_json(rec.model_dump_json()) == rec

    def test_ineligible_record(self, req: LookupRequest) -> None:
        rec = records.make_ineligible_record(req, "no_outputs", "x" * 999, MODE_ON, SOURCE_ENV)
        assert rec.outcome == RESULT_CACHE_INELIGIBLE
        assert rec.key is None
        assert rec.reason == "no_outputs"
        assert rec.reason_detail == "x" * MAX_TEXT_CHARS
        assert rec.dispatch_cycle == CYCLE and rec.at == NOW.isoformat()

    def test_ineligible_without_detail(self, req: LookupRequest) -> None:
        rec = records.make_ineligible_record(req, "pre_hook", None, MODE_SHADOW, SOURCE_ENV)
        assert rec.reason_detail is None and rec.mode == MODE_SHADOW


class TestHostileButValidEntry:
    def test_u_rc3_extreme_entry_values_validate(self, req: LookupRequest) -> None:
        # control + bidi override + non-ASCII, exactly at the bound
        hostile = ("\x1b[31m‮" + "é" * MAX_TEXT_CHARS)[:MAX_TEXT_CHARS]
        entry = CacheEntry(
            schema=ENTRY_SCHEMA,
            key=KEY,
            key_schema=1,
            created_at=NOW,
            source=EntrySource(
                workflow_id=hostile,
                task_id=hostile,
                run_id=hostile,
                agent=hostile,
                ao_version=hostile,
            ),
            usage=EntryUsage(
                cost_usd=MAX_ENTRY_COST_USD,
                input_tokens=MAX_ENTRY_TOKENS,
                output_tokens=MAX_ENTRY_TOKENS,
                duration_seconds=MAX_ENTRY_SECONDS,
            ),
            outputs=[OutputRecord(path="o.md", sha256=sha_of(b"x"), size=1, mode=0o644)],
            key_summary=KeySummary(key_schema=1, executor="fake", instruction="i.md"),
        )
        rec = records.make_hit_record(entry, KEY, req, MODE_ON, SOURCE_ENV)
        assert rec.saved_cost_usd == MAX_ENTRY_COST_USD
        assert rec.saved_tokens == 2 * MAX_ENTRY_TOKENS
        assert rec.saved_seconds == MAX_ENTRY_SECONDS
        assert rec.source_run_id is not None and len(rec.source_run_id) <= MAX_TEXT_CHARS
        assert ResultCacheRecord.model_validate_json(rec.model_dump_json()) == rec
