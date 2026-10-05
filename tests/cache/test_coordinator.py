"""T-gDNjN2 AC-1..AC-19 (U-CO1..U-CO10, U-CO12..U-CO20): the `ResultCache` coordinator (HLD 8.6).

Everything runs against fakes: `InMemoryCacheStore`, `FakeRepoHeadReader`, `FakeWorktreeProbe`, a
fake CLI version, a fixed clock and a capturing `LoggerAdapter`. Only the artifact workspace is a
real `tmp_path` (the key builder hashes real files and restore writes real outputs). Event field
sets are asserted exactly against HLD 15 to catch drift.
"""

from __future__ import annotations

import builtins
import logging
import os
import shutil
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import agent_orchestrator
from agent_orchestrator.artifacts import ArtifactStore, LocalFsArtifactStore
from agent_orchestrator.cache import constants as c
from agent_orchestrator.cache import coordinator as coordinator_mod
from agent_orchestrator.cache.coordinator import EXPECTED_ERRORS, ResultCache
from agent_orchestrator.cache.settings import ResultCacheSettings
from agent_orchestrator.cache.store import LocalFsCacheStore
from agent_orchestrator.cache.types import (
    CacheError,
    CacheIntegrityError,
    CacheLayoutError,
    CacheTooLargeError,
    CacheUnsafePathError,
    LookupOutcome,
    LookupRequest,
    PendingStore,
    PruneReport,
    RestoreMiss,
    StoreResult,
    UncacheableError,
)
from agent_orchestrator.logging_setup import _MergingAdapter
from agent_orchestrator.models import (
    RESULT_CACHE_HIT,
    RESULT_CACHE_INELIGIBLE,
    RESULT_CACHE_MISS,
    RESULT_CACHE_WOULD_HIT,
    TaskRunState,
    TaskSpec,
    WorkflowSpec,
)
from tests.cache.fakes import (
    FIXED_CREATED_AT,
    FakeRepoHeadReader,
    FakeWorktreeProbe,
    InMemoryCacheStore,
    fake_cli_version_of,
    make_entry,
    sha_of,
)
from tests.cache.keys_fixture import (
    GV1_COMPONENTS,
    GV1_HEAD,
    default_agent,
    default_task,
    write_files,
)

NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)
OUT_REL = "out/summary.md"
OUT_BODY = b"# summary\nline\n"
CLI_VERSION = "9.9.9 (Claude Code)"
RUN_ID = "wf-20261005T120000Z"
SNAP_A: frozenset[tuple[object, ...]] = frozenset({("/r", "a.py", "M", " ", 1, 2)})
SNAP_B: frozenset[tuple[object, ...]] = frozenset({("/r", "a.py", "M", " ", 9, 9)})

_STD_ATTRS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
_ADAPTER_ATTRS = {"run_id", "task_id"}


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def event_fields(record: logging.LogRecord) -> set[str]:
    """The structured fields of a record: everything but stdlib attributes and adapter ids."""
    return set(record.__dict__) - _STD_ATTRS - _ADAPTER_ATTRS


@dataclass
class Env:
    ws: Path
    store: InMemoryCacheStore
    heads: FakeRepoHeadReader
    probe: FakeWorktreeProbe
    cli: Any
    cache: ResultCache
    settings: ResultCacheSettings
    handler: _Capture
    log: logging.LoggerAdapter
    task: TaskSpec
    workflow: WorkflowSpec
    cycle: int = 0
    notes: dict[str, Any] = field(default_factory=dict)

    # -- requests ------------------------------------------------------------------------
    def request(self, **overrides: Any) -> LookupRequest:
        self.cycle += 1
        base = LookupRequest(
            task=self.task,
            workflow=self.workflow,
            agents={"writer": default_agent()},
            run_id=RUN_ID,
            injected=False,
            integration_active=False,
            artifact_store=LocalFsArtifactStore(str(self.ws)),
            general_instruction_paths=(str(self.ws / ".ao" / "house-rules.md"),),
            dynamic_input_paths=(),
            repo_paths={"core": str(self.ws)},
            dispatch_cycle=self.cycle,
            now=NOW,
        )
        return replace(base, **overrides)

    def lookup(self, **overrides: Any) -> LookupOutcome:
        return self.cache.lookup(self.request(**overrides), self.log)

    # -- fixtures of state -----------------------------------------------------------------
    @property
    def out_path(self) -> Path:
        return self.ws / OUT_REL

    def write_output(self, body: bytes = OUT_BODY) -> None:
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self.out_path.write_bytes(body)

    def plant(
        self, key: str, body: bytes = OUT_BODY, *, created_at: datetime = FIXED_CREATED_AT
    ) -> None:
        """A valid entry (and its blob) for `key` in the fake store."""
        self.store.entries[key] = make_entry(key, outputs=((OUT_REL, body),), created_at=created_at)
        self.store.blobs[sha_of(body)] = body

    def key_of_miss(self) -> str:
        """The key this task has right now (learned from a clean miss; no side effects)."""
        out = self.lookup()
        assert out.record is not None and out.record.key is not None
        # forget the probing lookup: tests then assert on what happens next
        self.probe.calls.clear()
        self.store.calls.clear()
        self.handler.records.clear()
        return out.record.key

    def settle(self, pending: PendingStore, ts: TaskRunState | None = None) -> StoreResult:
        return self.cache.store_success(pending, ts=ts or make_ts(), now=NOW, log=self.log)

    # -- logs ----------------------------------------------------------------------------
    def events(self, name: str) -> list[logging.LogRecord]:
        return [r for r in self.handler.records if getattr(r, "event", None) == name]

    def one(self, name: str) -> logging.LogRecord:
        found = self.events(name)
        assert len(found) == 1, [(r.getMessage(), r.__dict__.get("reason")) for r in found]
        return found[0]


def make_ts(**kw: Any) -> TaskRunState:
    base: dict[str, Any] = {
        "status": "succeeded",
        "attempts": 2,
        "started_at": "2026-10-05T10:00:00+00:00",
        "ended_at": "2026-10-05T10:00:30+00:00",
        "cumulative_input_tokens": 1000,
        "cumulative_output_tokens": 200,
        "cumulative_cache_creation_input_tokens": 7,
        "cumulative_cache_read_input_tokens": 9,
        "cumulative_cost_usd": 0.5,
        "model": "sonnet",
        "effort": "medium",
    }
    base.update(kw)
    return TaskRunState(**base)


def build_settings(**kw: Any) -> ResultCacheSettings:
    base: dict[str, Any] = {
        "mode": c.MODE_ON,
        "source": c.SOURCE_ENV,
        "max_bytes": 10**9,
        "max_entry_bytes": 10**8,
        "ttl_days": 30,
        "include_repo_heads": True,
        "max_input_bytes": 10**9,
        "max_input_files": 10**6,
    }
    base.update(kw)
    return ResultCacheSettings(**base)


MakeEnv = Callable[..., Env]


@pytest.fixture
def make_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[MakeEnv]:
    monkeypatch.delenv(c.ENV_CACHE, raising=False)
    handlers: list[tuple[logging.Logger, _Capture]] = []

    def factory(
        *,
        strict: bool = False,
        task_kw: dict[str, Any] | None = None,
        workflow_kw: dict[str, Any] | None = None,
        probe: FakeWorktreeProbe | None = None,
        **settings_kw: Any,
    ) -> Env:
        ws = tmp_path.resolve()
        write_files(ws)
        store = InMemoryCacheStore()
        heads = FakeRepoHeadReader({"core": GV1_HEAD})
        worktree = probe or FakeWorktreeProbe(SNAP_A)
        cli = fake_cli_version_of(CLI_VERSION)
        settings = build_settings(**settings_kw)
        cache = ResultCache(
            store,
            settings,
            workspace_root=str(ws),
            cache_root=str(ws / ".orchestrator" / "cache"),
            heads=heads,
            worktree=worktree,
            cli_versions=cli,
            environ={},
            strict=strict,
        )
        tkw = {"cache": True} | (task_kw or {})
        task = default_task(**tkw)
        workflow = WorkflowSpec(
            version="1.0", id="wf", repo_set="rs", tasks=[task], **(workflow_kw or {})
        )
        logger = logging.getLogger(f"test.cache.coordinator.{uuid.uuid4().hex}")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        handler = _Capture()
        logger.addHandler(handler)
        handlers.append((logger, handler))
        log = _MergingAdapter(logger, {"run_id": RUN_ID, "task_id": task.id})
        return Env(ws, store, heads, worktree, cli, cache, settings, handler, log, task, workflow)

    yield factory
    for logger, handler in handlers:
        logger.removeHandler(handler)


@pytest.fixture
def env(make_env: MakeEnv) -> Env:
    return make_env()


def miss_then_store(e: Env, body: bytes = OUT_BODY) -> tuple[LookupOutcome, StoreResult]:
    """One full miss -> (agent writes the output) -> settle cycle."""
    first = e.lookup()
    assert first.pending is not None
    e.write_output(body)
    return first, e.settle(first.pending)


# ======================================================================================= U-CO1
class TestNotOptedIn:
    def test_u_co1_no_record_no_store_calls_only_a_debug_event(self, make_env: MakeEnv) -> None:
        e = make_env(task_kw={"cache": None})
        out = e.lookup()
        assert out == LookupOutcome(False, None, None)
        assert e.store.calls == []
        assert e.heads.call_count == 0 and e.probe.call_count == 0
        assert [(r.levelno, r.event, r.reason) for r in e.handler.records] == [  # type: ignore[attr-defined]
            (logging.DEBUG, c.EVENT_SKIP, c.REASON_NOT_OPTED_IN)
        ]

    def test_explicit_opt_out_beats_the_workflow_default(self, make_env: MakeEnv) -> None:
        e = make_env(task_kw={"cache": False}, workflow_kw={"defaults": {"cache": True}})
        assert e.lookup() == LookupOutcome(False, None, None)

    def test_workflow_default_opt_in_is_honoured(self, make_env: MakeEnv) -> None:
        e = make_env(task_kw={"cache": None}, workflow_kw={"defaults": {"cache": True}})
        out = e.lookup()
        assert out.record is not None and out.record.reason == c.REASON_NOT_FOUND

    def test_an_injected_task_cannot_opt_in(self, env: Env) -> None:
        assert env.lookup(injected=True) == LookupOutcome(False, None, None)

    def test_a_coordinator_built_for_mode_off_does_nothing(self, make_env: MakeEnv) -> None:
        e = make_env(mode=c.MODE_OFF)
        assert e.lookup() == LookupOutcome(False, None, None)
        assert e.store.calls == []
        assert e.cache.store_success(MagicMock(), ts=make_ts(), now=NOW, log=e.log) == StoreResult(
            False, c.REASON_CACHE_DISABLED
        )


# ======================================================================================= U-CO2
class TestIneligible:
    def test_u_co2_record_and_skip_event_without_pending(self, make_env: MakeEnv) -> None:
        e = make_env(task_kw={"outputs": []})
        out = e.lookup()
        assert out.hit is False and out.pending is None
        rec = out.record
        assert rec is not None
        assert rec.outcome == RESULT_CACHE_INELIGIBLE
        assert rec.reason == c.REASON_NO_OUTPUTS and rec.key is None
        assert (rec.mode, rec.mode_source) == (c.MODE_ON, c.SOURCE_ENV)
        skip = e.one(c.EVENT_SKIP)
        assert skip.levelno == logging.INFO
        assert event_fields(skip) == {"event", "phase", "reason"}
        assert (skip.phase, skip.reason) == ("lookup", c.REASON_NO_OUTPUTS)  # type: ignore[attr-defined]
        assert e.store.calls == []  # eligibility precedes the store

    def test_the_detail_is_recorded_and_logged(self, make_env: MakeEnv) -> None:
        e = make_env()
        # `command_not_cacheable` carries the command basename as its detail
        agent = default_agent(command_template=["/usr/bin/curl", "{prompt}"])
        out = e.lookup(agents={"writer": agent})
        assert out.record is not None and out.record.reason == c.REASON_COMMAND_NOT_CACHEABLE
        assert out.record.reason_detail == "curl"
        skip = e.one(c.EVENT_SKIP)
        assert event_fields(skip) == {"event", "phase", "reason", "reason_detail"}
        assert skip.reason_detail == "curl"  # type: ignore[attr-defined]

    def test_u_co17_a_non_local_artifact_store_is_ineligible(self, env: Env) -> None:
        foreign = MagicMock(spec=ArtifactStore)
        out = env.lookup(artifact_store=foreign)
        assert out.pending is None and out.record is not None
        assert out.record.outcome == RESULT_CACHE_INELIGIBLE
        assert out.record.reason == c.REASON_ARTIFACT_STORE_UNSUPPORTED
        assert env.one(c.EVENT_SKIP).reason == c.REASON_ARTIFACT_STORE_UNSUPPORTED  # type: ignore[attr-defined]
        assert env.store.calls == []

    @pytest.mark.parametrize(
        ("path_kw", "reason"),
        [
            ({"prompt_path": OUT_REL}, c.REASON_CONTROL_OUTPUT),
        ],
    )
    def test_an_output_that_is_a_control_file_is_ineligible(
        self, make_env: MakeEnv, path_kw: dict[str, str], reason: str
    ) -> None:
        e = make_env(workflow_kw=path_kw)
        out = e.lookup()
        assert out.record is not None and out.record.reason == reason

    def test_unresolvable_control_paths_are_ignored(self, make_env: MakeEnv) -> None:
        e = make_env(workflow_kw={"prompt_path": "../../outside.md"})
        out = e.lookup()
        assert out.record is not None and out.record.reason == c.REASON_NOT_FOUND

    def test_a_key_build_refusal_is_ineligible(self, env: Env) -> None:
        (env.ws / "docs" / "notes.md").unlink()  # a declared input is missing
        out = env.lookup()
        assert out.pending is None and out.record is not None
        assert out.record.outcome == RESULT_CACHE_INELIGIBLE
        assert out.record.reason == c.REASON_INPUT_MISSING

    def test_a_head_read_failure_is_ineligible(self, env: Env) -> None:
        env.heads.fail(UncacheableError(c.REASON_REPO_HEAD_UNAVAILABLE, "core"))
        out = env.lookup()
        assert out.record is not None and out.record.reason == c.REASON_REPO_HEAD_UNAVAILABLE
        assert out.pending is None


# ======================================================================================= U-CO3
class TestNotFound:
    def test_u_co3_miss_record_pending_and_event(self, env: Env) -> None:
        out = env.lookup()
        rec = out.record
        assert out.hit is False and rec is not None and out.pending is not None
        assert rec.outcome == RESULT_CACHE_MISS and rec.reason == c.REASON_NOT_FOUND
        assert rec.key is not None and rec.key == out.pending.key.key
        assert (rec.dispatch_cycle, rec.at) == (1, NOW.isoformat())
        miss = env.one(c.EVENT_MISS)
        assert miss.levelno == logging.INFO
        assert event_fields(miss) == {"event", "reason", "key", "components"}
        assert miss.key == rec.key  # type: ignore[attr-defined]
        assert set(miss.components) == set(GV1_COMPONENTS)  # type: ignore[attr-defined]
        assert len(miss.components) == 11  # type: ignore[attr-defined]
        assert all(len(v) == c.COMPONENT_DIGEST_CHARS for v in miss.components.values())  # type: ignore[attr-defined]

    def test_the_pending_token_carries_the_lookup_time_state(self, env: Env) -> None:
        out = env.lookup()
        p = out.pending
        assert p is not None
        assert p.heads == {"core": GV1_HEAD}
        assert p.worktree == SNAP_A
        assert p.request.run_id == RUN_ID and p.request.dispatch_cycle == 1
        assert env.cli.calls  # the CLI version came from the injected reader

    def test_the_same_inputs_give_the_same_key(self, env: Env) -> None:
        a, b = env.lookup(), env.lookup()
        assert a.record is not None and b.record is not None
        assert a.record.key == b.record.key

    def test_ttl_none_never_expires(self, make_env: MakeEnv) -> None:
        e = make_env(ttl_days=None)
        key = e.key_of_miss()
        e.plant(key, created_at=NOW - timedelta(days=100_000))
        assert e.lookup().hit is True

    def test_a_future_created_at_is_not_expired(self, env: Env) -> None:
        key = env.key_of_miss()
        env.plant(key, created_at=NOW + timedelta(days=3))
        assert env.lookup().hit is True


# ======================================================================================= U-CO4
class TestHit:
    def test_u_co4_restore_touch_event_and_record(self, env: Env) -> None:
        _, stored = miss_then_store(env)
        assert stored == StoreResult(True)
        entry = next(iter(env.store.entries.values()))
        env.out_path.unlink()
        env.handler.records.clear()
        env.probe.calls.clear()
        out = env.lookup()
        assert out.hit is True and out.pending is None
        assert env.out_path.read_bytes() == OUT_BODY  # restored
        assert env.store.called("touch_entry") == 1
        assert env.store.calls[-1][0] == "touch_entry" or env.store.called("touch_entry") == 1
        rec = out.record
        assert rec is not None and rec.outcome == RESULT_CACHE_HIT and rec.hit is True
        assert rec.ended_at is None and rec.key == entry.key
        assert rec.saved_cost_usd == entry.usage.cost_usd == 0.5
        assert rec.saved_input_tokens == entry.usage.input_tokens == 1000
        assert rec.saved_output_tokens == entry.usage.output_tokens == 200
        assert rec.saved_tokens == 1200
        assert rec.saved_seconds == 30.0
        assert rec.source_run_id == RUN_ID
        hit = env.one(c.EVENT_HIT)
        assert hit.levelno == logging.INFO
        assert event_fields(hit) == {
            "event",
            "key",
            "outputs",
            "bytes",
            "saved_cost_usd",
            "source_run_id",
            "source_created_at",
            "source_ao_version",
        }
        assert hit.outputs == [{"path": OUT_REL, "sha256": sha_of(OUT_BODY)}]  # type: ignore[attr-defined]
        assert hit.bytes == len(OUT_BODY)  # type: ignore[attr-defined]
        assert hit.saved_cost_usd == 0.5  # type: ignore[attr-defined]
        assert hit.source_run_id == RUN_ID  # type: ignore[attr-defined]
        assert hit.source_created_at == NOW.isoformat()  # type: ignore[attr-defined]
        assert hit.source_ao_version == agent_orchestrator.__version__  # type: ignore[attr-defined]
        assert env.events(c.EVENT_MISS) == []

    def test_an_edited_output_changes_the_key_so_it_is_not_a_hit(self, env: Env) -> None:
        miss_then_store(env)
        env.write_output(b"stale local edit\n")
        # the key covers the prior (D6), so a changed output file is a different key: not a hit
        out = env.lookup()
        assert out.hit is False

    def test_a_failing_touch_is_best_effort(self, env: Env) -> None:
        miss_then_store(env)
        env.out_path.unlink()
        env.store.fail_with("touch_entry", OSError("read-only"))
        out = env.lookup()
        assert out.hit is True
        env.store.fail_with("touch_entry", CacheUnsafePathError(c.REASON_UNSAFE_PATH))
        env.out_path.unlink()
        assert env.lookup().hit is True


# ======================================================================================= U-CO5
class TestExpiry:
    def test_u_co5_expired_entry_is_evicted_and_storable(self, env: Env) -> None:
        key = env.key_of_miss()
        env.plant(key, created_at=NOW - timedelta(days=31))
        env.handler.records.clear()
        out = env.lookup()
        assert out.record is not None and out.record.reason == c.REASON_EXPIRED
        assert out.pending is not None  # storable
        assert key not in env.store.entries
        evict = env.one(c.EVENT_EVICT)
        assert (evict.key, evict.reason) == (key, c.REASON_EXPIRED)  # type: ignore[attr-defined]
        assert event_fields(evict) == {"event", "key", "reason"}
        assert env.events(c.EVENT_CORRUPT) == []

    def test_just_inside_the_ttl_is_a_hit(self, env: Env) -> None:
        key = env.key_of_miss()
        env.plant(key, created_at=NOW - timedelta(days=30))
        assert env.lookup().hit is True


# ======================================================================================= U-CO6
class TestCorruptEntry:
    @pytest.mark.parametrize("reason", [c.REASON_CORRUPT_ENTRY, c.REASON_KEY_MISMATCH])
    def test_u_co6_evict_warn_and_storable_miss(self, env: Env, reason: str) -> None:
        key = env.key_of_miss()
        env.plant(key)
        env.store.fail_with("get_entry", CacheIntegrityError(reason, "bad"))
        env.handler.records.clear()
        out = env.lookup()
        assert out.record is not None and out.record.reason == reason
        assert out.pending is not None
        assert env.store.called("delete_entry") == 1
        corrupt = env.one(c.EVENT_CORRUPT)
        assert corrupt.levelno == logging.WARNING
        assert event_fields(corrupt) == {"event", "key", "reason"}
        assert (corrupt.key, corrupt.reason) == (key, reason)  # type: ignore[attr-defined]
        assert env.one(c.EVENT_EVICT).reason == reason  # type: ignore[attr-defined]


# ======================================================================================= U-CO7
class TestBlobCorruptOnRestore:
    def test_u_co7_evict_delete_blob_and_no_pending(self, env: Env) -> None:
        key = env.key_of_miss()
        env.plant(key)
        sha = sha_of(OUT_BODY)
        env.store.blobs[sha] = b"tampered content!"  # same name, wrong bytes
        env.handler.records.clear()
        out = env.lookup()
        assert out.hit is False and out.pending is None
        assert out.record is not None and out.record.reason == c.REASON_BLOB_CORRUPT
        assert key not in env.store.entries
        assert sha not in env.store.blobs
        corrupt = env.one(c.EVENT_CORRUPT)
        assert event_fields(corrupt) == {"event", "key", "reason", "blob"}
        assert corrupt.blob == sha  # type: ignore[attr-defined]
        assert env.probe.call_count == 0  # never storable: no snapshot

    def test_a_missing_blob_on_restore_evicts_but_is_not_storable(self, env: Env) -> None:
        key = env.key_of_miss()
        env.plant(key)
        del env.store.blobs[sha_of(OUT_BODY)]
        out = env.lookup()
        assert out.record is not None and out.record.reason == c.REASON_BLOB_MISSING
        assert out.pending is None and key not in env.store.entries

    def test_a_manifest_mismatch_evicts_and_is_not_storable(self, env: Env) -> None:
        key = env.key_of_miss()
        entry = make_entry(key, outputs=(("other/path.md", OUT_BODY),))
        env.store.entries[key] = entry
        out = env.lookup()
        assert out.record is not None and out.record.reason == c.REASON_MANIFEST_MISMATCH
        assert out.pending is None and key not in env.store.entries


# ======================================================================================= U-CO8
class TestRestoreMissWithoutEviction:
    @pytest.mark.parametrize("reason", [c.REASON_RESTORE_FAILED, c.REASON_SENSITIVE_OUTPUT])
    def test_u_co8_miss_no_pending_no_eviction(
        self, env: Env, monkeypatch: pytest.MonkeyPatch, reason: str
    ) -> None:
        key = env.key_of_miss()
        env.plant(key)

        def refuse(*_a: Any, **_k: Any) -> None:
            raise RestoreMiss(reason, evict=False, detail="OSError")

        monkeypatch.setattr(coordinator_mod, "restore_outputs", refuse)
        out = env.lookup()
        assert out.hit is False and out.pending is None
        assert out.record is not None and out.record.reason == reason
        assert out.record.reason_detail == "OSError"
        assert key in env.store.entries  # not evicted
        assert env.store.called("delete_entry") == 0 and env.store.called("delete_blob") == 0
        assert env.events(c.EVENT_EVICT) == [] and env.events(c.EVENT_CORRUPT) == []
        assert env.probe.call_count == 0

    def test_a_real_restore_failure_is_a_plain_miss(self, env: Env) -> None:
        key = env.key_of_miss()
        env.plant(key)
        env.store.fail_with("read_blob", CacheError(c.REASON_STORE_ERROR, "transient"))
        out = env.lookup()
        assert out.record is not None and out.record.reason == c.REASON_STORE_ERROR
        assert out.pending is None and key in env.store.entries


# ======================================================================================= U-CO9
class TestStoreUnavailable:
    def test_u_co9_miss_no_pending_and_one_warning_over_three_lookups(self, env: Env) -> None:
        env.store.fail_with("check", CacheLayoutError(c.REASON_STORE_UNAVAILABLE, "bad layout"))
        outs = [env.lookup() for _ in range(3)]
        for out in outs:
            assert out.pending is None and out.hit is False
            assert out.record is not None
            assert out.record.outcome == RESULT_CACHE_MISS
            assert out.record.reason == c.REASON_STORE_UNAVAILABLE and out.record.key is None
        warnings = [r for r in env.handler.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert event_fields(warnings[0]) == {"event", "phase", "reason", "reason_detail"}
        assert warnings[0].reason == c.REASON_STORE_UNAVAILABLE  # type: ignore[attr-defined]
        assert env.store.called("get_entry") == 0

    def test_a_safeio_error_from_check_is_the_same(self, env: Env) -> None:
        from agent_orchestrator.cache.safeio import SafeIOError

        env.store.fail_with("check", SafeIOError("unsafe"))
        out = env.lookup()
        assert out.record is not None and out.record.reason == c.REASON_STORE_UNAVAILABLE


# ====================================================================================== U-CO10
class TestShadow:
    def test_u_co10_would_hit_without_restore_and_with_pending(
        self, make_env: MakeEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        e = make_env(mode=c.MODE_SHADOW)
        key = e.key_of_miss()
        e.plant(key)
        restores: list[object] = []
        monkeypatch.setattr(
            coordinator_mod, "restore_outputs", lambda *a, **k: restores.append((a, k))
        )
        e.handler.records.clear()
        e.probe.calls.clear()
        out = e.lookup()
        assert out.hit is False and out.pending is not None
        assert restores == []  # never restored
        assert not e.out_path.exists()  # shadow never writes into the workspace
        rec = out.record
        assert rec is not None and rec.outcome == RESULT_CACHE_WOULD_HIT and rec.hit is False
        assert rec.mode == c.MODE_SHADOW and rec.store_reason is None
        assert rec.saved_cost_usd == e.store.entries[key].usage.cost_usd
        wh = e.one(c.EVENT_WOULD_HIT)
        assert event_fields(wh) == {"event", "key", "saved_cost_usd", "source_run_id"}
        assert wh.key == key  # type: ignore[attr-defined]
        assert e.probe.call_count == 1
        assert e.store.called("touch_entry") == 0

    def test_u_co10_a_missing_blob_evicts_and_is_a_storable_miss(self, make_env: MakeEnv) -> None:
        e = make_env(mode=c.MODE_SHADOW)
        key = e.key_of_miss()
        e.plant(key)
        del e.store.blobs[sha_of(OUT_BODY)]
        out = e.lookup()
        assert out.record is not None and out.record.reason == c.REASON_BLOB_MISSING
        assert out.pending is not None
        assert key not in e.store.entries
        assert e.events(c.EVENT_CORRUPT)[-1].reason == c.REASON_BLOB_MISSING  # type: ignore[attr-defined]

    def test_a_shadow_would_hit_pending_can_store_over_the_entry(self, make_env: MakeEnv) -> None:
        e = make_env(mode=c.MODE_SHADOW)
        key = e.key_of_miss()
        e.plant(key)
        out = e.lookup()
        assert out.pending is not None
        e.write_output()
        assert e.settle(out.pending) == StoreResult(True)


# ====================================================================================== U-CO12
class TestStoreGuards:
    def _pending(self, e: Env) -> PendingStore:
        out = e.lookup()
        assert out.pending is not None
        e.write_output()
        return out.pending

    def test_the_happy_path_stores(self, env: Env) -> None:
        assert env.settle(self._pending(env)) == StoreResult(True)

    @pytest.mark.parametrize("include_heads", [True, False])
    def test_u_co12_repo_head_moved(self, make_env: MakeEnv, include_heads: bool) -> None:
        e = make_env(include_repo_heads=include_heads)
        pending = self._pending(e)
        e.heads.heads = {"core": "f" * 40}
        res = e.settle(pending)
        assert res == StoreResult(False, c.REASON_REPO_HEAD_MOVED)
        assert e.store.entries == {}
        skip = e.one(c.EVENT_SKIP)
        assert skip.levelno == logging.INFO and skip.phase == "store"  # type: ignore[attr-defined]
        assert event_fields(skip) == {"event", "phase", "reason"}

    def test_u_co12_key_changed_during_run(self, env: Env) -> None:
        pending = self._pending(env)
        (env.ws / "docs" / "notes.md").write_text("changed while the agent ran\n")
        res = env.settle(pending)
        assert res == StoreResult(False, c.REASON_KEY_CHANGED_DURING_RUN)
        skip = env.one(c.EVENT_SKIP)
        assert event_fields(skip) == {"event", "phase", "reason", "components"}
        assert skip.components["inputs"] != pending.key.components["inputs"]  # type: ignore[attr-defined]
        assert env.store.entries == {}

    def test_an_output_that_is_also_an_input_keeps_its_lookup_time_key(
        self, make_env: MakeEnv
    ) -> None:
        """The preseed (N-5): the agent rewrites the file it also reads; the key is unchanged."""
        e = make_env(task_kw={"inputs": ["docs/notes.md", OUT_REL]})
        e.write_output(b"previous\n")
        out = e.lookup()
        assert out.pending is not None
        e.write_output(b"rewritten by the agent\n")
        assert e.settle(out.pending) == StoreResult(True)

    def test_u_co12_repo_worktree_changed(self, make_env: MakeEnv) -> None:
        e = make_env(probe=FakeWorktreeProbe(SNAP_A, SNAP_B))
        res = e.settle(self._pending(e))
        assert res == StoreResult(False, c.REASON_REPO_WORKTREE_CHANGED)
        assert e.store.entries == {}

    def test_the_settle_snapshot_excludes_the_declared_outputs(self, env: Env) -> None:
        env.settle(self._pending(env))
        assert env.probe.call_count == 2
        for call in env.probe.calls:
            assert call[2] == frozenset({str(env.out_path)})
            assert call[1] == str(env.ws)

    def test_u_co12_repo_head_unavailable_at_settle(self, env: Env) -> None:
        pending = self._pending(env)
        env.heads.fail(UncacheableError(c.REASON_REPO_HEAD_UNAVAILABLE, "core:GitError"))
        assert env.settle(pending) == StoreResult(False, c.REASON_REPO_HEAD_UNAVAILABLE)

    def test_a_probe_failure_at_settle_is_a_skip(self, env: Env) -> None:
        pending = self._pending(env)
        env.probe.fail(UncacheableError(c.REASON_REPO_WORKTREE_PROBE_FAILED, "boom"))
        res = env.settle(pending)
        assert res == StoreResult(False, c.REASON_REPO_WORKTREE_PROBE_FAILED)

    def test_a_key_recompute_refusal_is_a_skip_with_its_reason(self, env: Env) -> None:
        pending = self._pending(env)
        (env.ws / "docs" / "notes.md").unlink()
        assert env.settle(pending) == StoreResult(False, c.REASON_INPUT_MISSING)

    def test_a_missing_output_is_a_skip(self, env: Env) -> None:
        out = env.lookup()
        assert out.pending is not None  # the agent "succeeded" but wrote nothing
        assert env.settle(out.pending) == StoreResult(False, c.REASON_OUTPUT_MISSING)

    def test_an_oversized_result_is_a_skip(self, make_env: MakeEnv) -> None:
        e = make_env(max_entry_bytes=4)
        assert e.settle(self._pending(e)) == StoreResult(False, c.REASON_ENTRY_TOO_LARGE)

    def test_an_oversized_entry_file_is_a_skip(self, env: Env) -> None:
        env.store.fail_with("put_entry", CacheTooLargeError(c.REASON_ENTRY_TOO_LARGE))
        assert env.settle(self._pending(env)) == StoreResult(False, c.REASON_ENTRY_TOO_LARGE)


# ====================================================================================== U-CO13
class TestStorePath:
    def test_u_co13_entry_fields(self, env: Env) -> None:
        _, res = miss_then_store(env)
        assert res == StoreResult(True)
        (entry,) = env.store.entries.values()
        assert entry.schema_ == c.ENTRY_SCHEMA and entry.key_schema == c.KEY_SCHEMA_VERSION
        assert entry.created_at == NOW
        assert entry.usage.cost_usd == 0.5
        assert (entry.usage.input_tokens, entry.usage.output_tokens) == (1000, 200)
        assert (
            entry.usage.cache_creation_input_tokens,
            entry.usage.cache_read_input_tokens,
        ) == (7, 9)
        assert entry.usage.duration_seconds == 30.0 and entry.usage.attempts == 2
        assert (entry.usage.model, entry.usage.effort) == ("sonnet", "medium")
        assert entry.usage.actuals_available is True
        assert entry.source.ao_version == agent_orchestrator.__version__
        assert entry.source.cli_version == CLI_VERSION  # from the key, not re-read
        assert (entry.source.workflow_id, entry.source.task_id) == ("wf", "summarize")
        assert (entry.source.run_id, entry.source.agent) == (RUN_ID, "writer")
        assert [(o.path, o.sha256, o.size) for o in entry.outputs] == [
            (OUT_REL, sha_of(OUT_BODY), len(OUT_BODY))
        ]
        assert env.store.blobs[sha_of(OUT_BODY)] == OUT_BODY
        store_ev = env.one(c.EVENT_STORE)
        assert event_fields(store_ev) == {"event", "key", "outputs", "bytes"}
        assert (store_ev.outputs, store_ev.bytes) == (1, len(OUT_BODY))  # type: ignore[attr-defined]
        assert store_ev.levelno == logging.INFO

    def test_usage_is_clamped(self, env: Env) -> None:
        ts = make_ts(
            cumulative_cost_usd=5_000_000.0,
            cumulative_input_tokens=10**15,
            cumulative_output_tokens=10**15,
            attempts=10**9,
            started_at="2020-01-01T00:00:00+00:00",
            ended_at="2026-01-01T00:00:00+00:00",
        )
        out = env.lookup()
        assert out.pending is not None
        env.write_output()
        assert env.settle(out.pending, ts) == StoreResult(True)
        (entry,) = env.store.entries.values()
        assert entry.usage.cost_usd == c.MAX_ENTRY_COST_USD
        assert entry.usage.input_tokens == entry.usage.output_tokens == c.MAX_ENTRY_TOKENS
        assert entry.usage.attempts == c.MAX_ENTRY_ATTEMPTS
        assert entry.usage.duration_seconds == c.MAX_ENTRY_SECONDS

    def test_no_actuals_means_actuals_unavailable(self, env: Env) -> None:
        ts = make_ts(cumulative_cost_usd=0.0, cumulative_input_tokens=0, cumulative_output_tokens=0)
        out = env.lookup()
        assert out.pending is not None
        env.write_output()
        env.settle(out.pending, ts)
        (entry,) = env.store.entries.values()
        assert entry.usage.actuals_available is False and entry.usage.cost_usd == 0.0

    def test_a_fake_executor_has_no_cli_version(self, make_env: MakeEnv) -> None:
        e = make_env()
        out = e.lookup(agents={"writer": default_agent(executor="fake")})
        assert out.pending is not None
        e.write_output()
        res = e.cache.store_success(out.pending, ts=make_ts(), now=NOW, log=e.log)
        assert res == StoreResult(True)
        (entry,) = e.store.entries.values()
        assert entry.source.cli_version is None

    def test_long_names_are_clipped(self, make_env: MakeEnv) -> None:
        e = make_env()
        out = e.lookup(run_id="r" * 1000)
        assert out.pending is not None
        e.write_output()
        assert e.settle(out.pending) == StoreResult(True)
        (entry,) = e.store.entries.values()
        assert entry.source.run_id == "r" * c.MAX_TEXT_CHARS

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(float("nan"), 0.0), (-5.0, 0.0), (float("inf"), 10.0), (3.0, 3.0)],
    )
    def test_clamp(self, value: float, expected: float) -> None:
        assert coordinator_mod._clamp(value, 10.0) == expected

    @pytest.mark.parametrize(
        ("started", "ended", "expected"),
        [
            (None, "2026-10-05T10:00:30+00:00", 0.0),
            ("not a date", None, 0.0),
            ("2026-10-05T10:00:00", None, 0.0),  # naive vs aware: unusable
            ("2026-10-05T11:59:00+00:00", None, 60.0),  # no end stamp: the settle clock
            ("2026-10-05T10:00:30+00:00", "2026-10-05T10:00:00+00:00", 0.0),  # negative
        ],
    )
    def test_duration(self, started: str | None, ended: str | None, expected: float) -> None:
        assert coordinator_mod._duration_seconds(started, ended, NOW) == expected


# ====================================================================================== U-CO14
class TestErrorBoundary:
    def test_u_co14_an_oserror_is_a_store_error_miss_with_an_error_log(self, env: Env) -> None:
        env.store.fail_with("get_entry", OSError("disk on fire"))
        out = env.lookup()
        assert out.hit is False and out.pending is None
        assert out.record is not None and out.record.reason == c.REASON_STORE_ERROR
        assert out.record.outcome == RESULT_CACHE_MISS and out.record.key is None
        errors = [r for r in env.handler.records if r.levelno == logging.ERROR]
        assert len(errors) == 1 and errors[0].exc_info is not None
        assert (errors[0].event, errors[0].phase) == (c.EVENT_SKIP, "lookup")  # type: ignore[attr-defined]
        # the cache stays enabled
        env.store.fail_with("get_entry", None)
        assert env.lookup().record.reason == c.REASON_NOT_FOUND  # type: ignore[union-attr]

    def test_every_expected_error_class_is_a_store_error(self, env: Env) -> None:
        for exc in (CacheError("x"), ValueError("v"), TypeError("t"), RecursionError("r")):
            env.store.fail_with("get_entry", exc)
            out = env.lookup()
            assert out.record is not None and out.record.reason == c.REASON_STORE_ERROR
        assert {OSError, ValueError, TypeError, RecursionError, OverflowError} <= set(
            EXPECTED_ERRORS
        )

    def test_u_co14_an_unexpected_error_disables_the_cache(self, env: Env) -> None:
        env.store.fail_with("get_entry", RuntimeError("bug"))
        out = env.lookup()
        assert out == LookupOutcome(False, None, None)
        disabled = env.one(c.EVENT_DISABLED)
        assert disabled.levelno == logging.ERROR and disabled.exc_info is not None
        assert event_fields(disabled) == {"event", "error_type"}
        assert disabled.error_type == "RuntimeError"  # type: ignore[attr-defined]
        # every later call returns nothing and touches nothing
        env.store.fail_with("get_entry", None)
        env.store.calls.clear()
        assert env.lookup() == LookupOutcome(False, None, None)
        assert env.store.calls == []
        pending = PendingStore(env.request(), MagicMock(), {}, frozenset())
        assert env.settle(pending) == StoreResult(False, c.REASON_CACHE_DISABLED)

    def test_u_co14_strict_mode_propagates_an_unexpected_error(self, make_env: MakeEnv) -> None:
        e = make_env(strict=True)
        e.store.fail_with("get_entry", RuntimeError("bug"))
        with pytest.raises(RuntimeError, match="bug"):
            e.lookup()
        assert e.events(c.EVENT_DISABLED) == []

    def test_strict_mode_still_wraps_expected_errors(self, make_env: MakeEnv) -> None:
        e = make_env(strict=True)
        e.store.fail_with("get_entry", OSError("x"))
        out = e.lookup()
        assert out.record is not None and out.record.reason == c.REASON_STORE_ERROR

    def test_a_store_side_oserror_is_a_store_error_skip(self, env: Env) -> None:
        out = env.lookup()
        assert out.pending is not None
        env.write_output()
        env.store.fail_with("put_entry", OSError("full"))
        assert env.settle(out.pending) == StoreResult(False, c.REASON_STORE_ERROR)
        errors = [r for r in env.handler.records if r.levelno == logging.ERROR]
        assert len(errors) == 1 and errors[0].phase == "store"  # type: ignore[attr-defined]
        assert env.events(c.EVENT_DISABLED) == []

    def test_a_store_side_unexpected_error_disables(self, env: Env) -> None:
        out = env.lookup()
        assert out.pending is not None
        env.write_output()
        env.store.fail_with("put_entry", RuntimeError("bug"))
        assert env.settle(out.pending) == StoreResult(False, c.REASON_CACHE_DISABLED)
        assert env.one(c.EVENT_DISABLED).error_type == "RuntimeError"  # type: ignore[attr-defined]
        assert env.lookup() == LookupOutcome(False, None, None)

    def test_a_store_side_unexpected_error_in_strict_mode_raises(self, make_env: MakeEnv) -> None:
        e = make_env(strict=True)
        out = e.lookup()
        assert out.pending is not None
        e.write_output()
        e.store.fail_with("put_entry", RuntimeError("bug"))
        with pytest.raises(RuntimeError):
            e.settle(out.pending)

    def test_a_store_error_skip_from_capture_logs_at_warning(
        self, env: Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # capture maps an unreadable output file to StoreSkip(store_error): WARNING (HLD 15)
        from agent_orchestrator.cache import restore as restore_mod

        out = env.lookup()
        assert out.pending is not None
        env.write_output()

        real_open = restore_mod.safeio.open_regular_read

        def unreadable(path: str) -> int:
            if path == str(env.out_path):  # only the capture read; inputs stay readable
                raise PermissionError("denied")
            return real_open(path)

        monkeypatch.setattr(restore_mod.safeio, "open_regular_read", unreadable)
        res = env.settle(out.pending)
        assert res == StoreResult(False, c.REASON_STORE_ERROR)
        skip = env.one(c.EVENT_SKIP)
        assert skip.levelno == logging.WARNING and skip.phase == "store"  # type: ignore[attr-defined]
        assert env.events(c.EVENT_DISABLED) == []


# ====================================================================================== U-CO15
class TestFactory:
    def test_u_co15_no_filesystem_writes_and_a_resolved_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = (tmp_path / "ws").resolve()
        ws.mkdir()
        unresolved = ws / "sub" / ".."
        mutators = [
            (os, "mkdir"),
            (os, "makedirs"),
            (os, "open"),
            (os, "write"),
            (os, "replace"),
            (os, "rename"),
            (os, "unlink"),
            (os, "remove"),
            (os, "rmdir"),
            (shutil, "rmtree"),
            (builtins, "open"),
        ]

        def forbid(*_a: Any, **_k: Any) -> None:
            raise AssertionError("from_settings must not write to the filesystem")

        with monkeypatch.context() as m:
            for mod, name in mutators:
                m.setattr(mod, name, forbid)
            cache = ResultCache.from_settings(workspace_root=unresolved, settings=build_settings())
        assert cache.root == str(ws / ".orchestrator" / "cache")
        assert cache._ws == str(ws)  # resolved
        assert isinstance(cache._store, LocalFsCacheStore)
        assert not (ws / ".orchestrator").exists()
        assert cache.warnings == ()

    def test_u_co15_nothing_is_named_open(self) -> None:
        assert not hasattr(ResultCache, "open")
        assert "open" not in {n for n in dir(ResultCache) if not n.startswith("__")}

    def test_the_default_collaborators_are_the_real_ones(self, tmp_path: Path) -> None:
        from agent_orchestrator.cache.repo_state import RepoHeadReader, WorktreeProbe

        cache = ResultCache(
            InMemoryCacheStore(),
            build_settings(),
            workspace_root=str(tmp_path),
            cache_root=str(tmp_path / "c"),
        )
        assert isinstance(cache._heads, RepoHeadReader)
        assert isinstance(cache._worktree, WorktreeProbe)
        assert cache._environ == {} and cache.warnings == ()

    def test_from_settings_hands_the_store_the_limits(self, tmp_path: Path) -> None:
        cache = ResultCache.from_settings(
            workspace_root=tmp_path, settings=build_settings(max_bytes=123, ttl_days=None)
        )
        store = cache._store
        assert isinstance(store, LocalFsCacheStore)
        assert store.max_bytes == 123 and store.ttl_days is None


# ====================================================================================== U-CO16
class TestInlineEnforcement:
    def _store_once(self, e: Env) -> StoreResult:
        out = e.lookup()
        assert out.pending is not None
        e.write_output()
        return e.settle(out.pending)

    def test_u_co16_a_deferred_report_logs_a_warning(self, env: Env) -> None:
        env.store.enforce_result = PruneReport(deferred=True)
        assert self._store_once(env) == StoreResult(True)
        ev = env.one(c.EVENT_EVICT)
        assert ev.levelno == logging.WARNING
        assert event_fields(ev) == {"event", "reason"}
        assert ev.reason == c.REASON_EVICT_DEFERRED  # type: ignore[attr-defined]
        assert env.store.called("maybe_enforce_limits") == 1

    @pytest.mark.parametrize("exc", [OSError("gone"), CacheError("x"), CacheLayoutError("y")])
    def test_u_co16_an_enforcement_error_is_swallowed_after_a_successful_put(
        self, env: Env, exc: Exception
    ) -> None:
        env.store.fail_with("maybe_enforce_limits", exc)
        assert self._store_once(env) == StoreResult(True)
        assert len(env.store.entries) == 1
        assert env.events(c.EVENT_EVICT) == []
        assert env.events(c.EVENT_DISABLED) == []

    def test_an_lru_eviction_is_logged_at_info(self, env: Env) -> None:
        env.store.enforce_result = PruneReport(removed_entries={c.REASON_EVICT_LRU: 3})
        assert self._store_once(env) == StoreResult(True)
        ev = env.one(c.EVENT_EVICT)
        assert ev.levelno == logging.INFO
        assert event_fields(ev) == {"event", "reason", "entries"}
        assert (ev.reason, ev.entries) == (c.REASON_EVICT_LRU, 3)  # type: ignore[attr-defined]

    def test_a_clean_report_logs_nothing(self, env: Env) -> None:
        env.store.enforce_result = PruneReport()
        assert self._store_once(env) == StoreResult(True)
        assert env.events(c.EVENT_EVICT) == []


# ====================================================================================== U-CO18
class TestLazyGuardThree:
    def test_u_co18_a_hit_never_takes_the_snapshot(self, env: Env) -> None:
        miss_then_store(env)
        env.out_path.unlink()
        env.probe.calls.clear()
        out = env.lookup()
        assert out.hit is True and out.pending is None
        assert env.probe.call_count == 0

    def test_u_co18_a_storable_miss_takes_it_exactly_once(self, env: Env) -> None:
        out = env.lookup()
        assert out.pending is not None and env.probe.call_count == 1

    def test_u_co18_a_would_hit_takes_it_exactly_once(self, make_env: MakeEnv) -> None:
        e = make_env(mode=c.MODE_SHADOW)
        e.plant(e.key_of_miss())
        e.probe.calls.clear()
        assert e.lookup().pending is not None
        assert e.probe.call_count == 1

    def test_a_non_storable_miss_never_takes_it(self, env: Env) -> None:
        env.store.fail_with("check", CacheLayoutError(c.REASON_STORE_UNAVAILABLE))
        env.lookup()
        assert env.probe.call_count == 0

    @pytest.mark.parametrize("exc_reason", [c.REASON_REPO_WORKTREE_PROBE_FAILED])
    def test_u_co18_a_probe_failure_on_a_miss_is_not_storable_not_ineligible(
        self, env: Env, exc_reason: str
    ) -> None:
        env.probe.fail(UncacheableError(exc_reason, "git status exploded"))
        out = env.lookup()
        assert out.pending is None and out.hit is False
        rec = out.record
        assert rec is not None
        assert rec.outcome == RESULT_CACHE_MISS  # outcome unchanged: never ineligible
        assert rec.reason == c.REASON_NOT_FOUND
        assert rec.stored is False and rec.store_reason == c.REASON_REPO_WORKTREE_PROBE_FAILED
        assert rec.key is not None
        skip = env.one(c.EVENT_SKIP)
        assert skip.levelno == logging.INFO
        assert event_fields(skip) == {"event", "phase", "reason", "reason_detail"}
        assert skip.phase == "store"  # type: ignore[attr-defined]
        assert env.probe.call_count == 1

    def test_u_co18_a_probe_failure_on_a_would_hit(self, make_env: MakeEnv) -> None:
        e = make_env(mode=c.MODE_SHADOW)
        e.plant(e.key_of_miss())
        e.probe.fail(UncacheableError(c.REASON_REPO_WORKTREE_PROBE_FAILED))
        out = e.lookup()
        assert out.pending is None
        assert out.record is not None and out.record.outcome == RESULT_CACHE_WOULD_HIT
        assert out.record.store_reason == c.REASON_REPO_WORKTREE_PROBE_FAILED

    def test_a_probe_failure_after_an_evicting_miss(self, env: Env) -> None:
        key = env.key_of_miss()
        env.plant(key, created_at=NOW - timedelta(days=99))
        env.probe.fail(UncacheableError(c.REASON_REPO_WORKTREE_PROBE_FAILED))
        out = env.lookup()
        assert out.record is not None and out.record.reason == c.REASON_EXPIRED
        assert out.pending is None
        assert out.record.store_reason == c.REASON_REPO_WORKTREE_PROBE_FAILED


# ====================================================================================== U-CO19
class TestUnsafePath:
    def test_u_co19_get_entry_unsafe_never_evicts(self, env: Env) -> None:
        env.store.fail_with("get_entry", CacheUnsafePathError(c.REASON_UNSAFE_PATH, "symlink"))
        out = env.lookup()
        assert out.hit is False and out.pending is None
        rec = out.record
        assert rec is not None and rec.outcome == RESULT_CACHE_MISS
        assert rec.reason == c.REASON_UNSAFE_PATH and rec.key is not None
        assert env.store.called("delete_entry") == 0  # never evicted
        assert env.store.called("delete_blob") == 0
        corrupt = env.one(c.EVENT_CORRUPT)
        assert corrupt.levelno == logging.WARNING
        assert event_fields(corrupt) == {"event", "key", "reason"}
        assert (corrupt.key, corrupt.reason) == (rec.key, c.REASON_UNSAFE_PATH)  # type: ignore[attr-defined]
        miss = env.one(c.EVENT_MISS)
        assert miss.reason == c.REASON_UNSAFE_PATH  # type: ignore[attr-defined]
        assert env.events(c.EVENT_EVICT) == []
        assert env.probe.call_count == 0
        assert env.events(c.EVENT_DISABLED) == []

    @pytest.mark.parametrize("method", ["has_blob", "read_blob", "delete_entry"])
    def test_an_unsafe_path_anywhere_in_the_lookup_is_the_same_miss(
        self, make_env: MakeEnv, method: str
    ) -> None:
        e = make_env(mode=c.MODE_SHADOW if method == "has_blob" else c.MODE_ON)
        key = e.key_of_miss()
        if method == "delete_entry":
            e.plant(key, created_at=NOW - timedelta(days=99))  # an expiry eviction
        else:
            e.plant(key)
        e.store.fail_with(method, CacheUnsafePathError(c.REASON_UNSAFE_PATH))
        out = e.lookup()
        assert out.record is not None and out.record.reason == c.REASON_UNSAFE_PATH
        assert out.pending is None
        assert e.store.called("delete_blob") == 0
        assert key in e.store.entries  # a failed delete removed nothing

    def test_an_unsafe_path_at_settle_is_a_store_error_skip(self, env: Env) -> None:
        out = env.lookup()
        assert out.pending is not None
        env.write_output()
        env.store.fail_with("put_blob", CacheUnsafePathError(c.REASON_UNSAFE_PATH))
        assert env.settle(out.pending) == StoreResult(False, c.REASON_STORE_ERROR)


# ====================================================================================== U-CO20
def _has_git_ancestor(path: Path) -> bool:
    return any(os.path.lexists(p / ".git") for p in path.parents)


class TestWarnings:
    def test_u_co20_a_git_above_the_workspace_yields_one_warning(self, tmp_path: Path) -> None:
        outer = (tmp_path / "outer").resolve()
        (outer / ".git").mkdir(parents=True)
        ws = outer / "ws"
        ws.mkdir()
        cache = ResultCache.from_settings(workspace_root=ws, settings=build_settings())
        assert len(cache.warnings) == 1
        assert str(outer) in cache.warnings[0] and str(ws) in cache.warnings[0]
        assert "HEAD is not part of result-cache keys" in cache.warnings[0]

    def test_u_co20_no_warning_otherwise(self, tmp_path: Path) -> None:
        plain = (tmp_path / "plain" / "ws").resolve()
        plain.mkdir(parents=True)
        if _has_git_ancestor(plain):
            pytest.skip("the temp directory itself sits inside a git repository")
        cache = ResultCache.from_settings(workspace_root=plain, settings=build_settings())
        assert cache.warnings == ()

    def test_a_git_inside_the_workspace_is_not_a_warning(self, tmp_path: Path) -> None:
        ws = (tmp_path / "ws").resolve()
        (ws / ".git").mkdir(parents=True)
        if _has_git_ancestor(ws):
            pytest.skip("the temp directory itself sits inside a git repository")
        assert (
            ResultCache.from_settings(workspace_root=ws, settings=build_settings()).warnings == ()
        )


def test_the_module_imports_nothing_from_the_engine() -> None:
    import ast

    tree = ast.parse(Path(coordinator_mod.__file__).read_text())
    imported = {
        n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module is not None
    }
    assert "agent_orchestrator.engine" not in imported
    assert not any(m.startswith("agent_orchestrator.ui") for m in imported)
