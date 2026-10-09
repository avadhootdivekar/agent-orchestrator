"""The coordinator: THE engine-facing `ResultCacheHook` implementation (HLD 8.6, ADR-0019).

It runs the author policy, eligibility, repo state, key build, store lookup, mode handling,
restore, the three store guards, logging and record construction. It never raises into the engine
(D32: strict mode excepted) and never mutates `RunState` (the engine writes the records, D12).

Outcome semantics (HLD 8.6.4):
  * a *storable* outcome (`not_found`, `expired`, `corrupt_entry`, `key_mismatch`, shadow
    `blob_missing`, shadow `would_hit`) carries a `PendingStore`; guard 3's worktree snapshot is
    taken lazily, only for those outcomes, and a probe failure makes the outcome not storable
    (never ineligible);
  * a hit, a restore-time miss, `unsafe_path`, `store_unavailable` and `store_error` are not
    storable. An unsafe cache path is NEVER evicted (D33).
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast

from pydantic import ValidationError

from agent_orchestrator import __version__
from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.cache.constants import (
    ENTRY_SCHEMA,
    EVENT_CORRUPT,
    EVENT_DISABLED,
    EVENT_EVICT,
    EVENT_HIT,
    EVENT_MISS,
    EVENT_SKIP,
    EVENT_STORE,
    EVENT_WOULD_HIT,
    KEY_SCHEMA_VERSION,
    MAX_ENTRY_ATTEMPTS,
    MAX_ENTRY_COST_USD,
    MAX_ENTRY_SECONDS,
    MAX_ENTRY_TOKENS,
    MODE_ON,
    MODE_SHADOW,
    REASON_ARTIFACT_STORE_UNSUPPORTED,
    REASON_BLOB_MISSING,
    REASON_CACHE_DISABLED,
    REASON_ENTRY_TOO_LARGE,
    REASON_EVICT_DEFERRED,
    REASON_EVICT_LRU,
    REASON_EXPIRED,
    REASON_KEY_CHANGED_DURING_RUN,
    REASON_NOT_FOUND,
    REASON_NOT_OPTED_IN,
    REASON_REPO_HEAD_MOVED,
    REASON_REPO_WORKTREE_CHANGED,
    REASON_STORE_ERROR,
    REASON_STORE_UNAVAILABLE,
    REASON_UNSAFE_PATH,
)
from agent_orchestrator.cache.eligibility import check_eligibility
from agent_orchestrator.cache.fingerprint import CliVersionReader, try_resolve
from agent_orchestrator.cache.keys import build_cache_key
from agent_orchestrator.cache.records import (
    make_hit_record,
    make_ineligible_record,
    make_miss_record,
    make_would_hit_record,
)
from agent_orchestrator.cache.repo_state import (
    RepoHeadReader,
    WorktreeProbe,
    nested_repo_marker,
)
from agent_orchestrator.cache.restore import capture_outputs, restore_outputs
from agent_orchestrator.cache.safeio import SafeIOError
from agent_orchestrator.cache.settings import ResultCacheSettings, task_cache_policy
from agent_orchestrator.cache.store import LocalFsCacheStore, is_expired
from agent_orchestrator.cache.types import (
    CacheEntry,
    CacheError,
    CacheIntegrityError,
    CacheKey,
    CacheLayoutError,
    CacheStore,
    CacheTooLargeError,
    CacheUnsafePathError,
    EntrySource,
    EntryUsage,
    KeyDeps,
    KeyRequest,
    LookupOutcome,
    LookupRequest,
    OutputRecord,
    PendingStore,
    RestoreMiss,
    StoreResult,
    StoreSkip,
    UncacheableError,
    clip_text,
)
from agent_orchestrator.errors import GitError
from agent_orchestrator.models import AgentSpec, TaskRunState, resolve_effective_agent

# Anything in this tuple is a failure of the cache or of its inputs, not a bug: it becomes a
# `store_error` miss / skip (ERROR log) and the cache stays enabled. Anything else is unexpected:
# the cache disables itself for the rest of the run (D32; strict mode re-raises for tests).
EXPECTED_ERRORS = (
    OSError,
    CacheError,
    SafeIOError,
    ValidationError,
    GitError,
    ValueError,
    TypeError,
    RecursionError,
    OverflowError,
    subprocess.SubprocessError,
)

_PHASE_LOOKUP = "lookup"
_PHASE_STORE = "store"


def _clamp(value: float, hi: float) -> float:
    """`value` limited to [0, hi]; NaN counts as 0 (the entry model rejects NaN / out-of-range)."""
    if value != value:  # NaN
        return 0.0
    return max(0.0, min(float(value), float(hi)))


def _clamp_int(value: int, hi: int) -> int:
    return max(0, min(int(value), hi))


def _duration_seconds(started_at: str | None, ended_at: str | None, now: datetime) -> float:
    """Wall-clock seconds of the source dispatch, clamped; 0 when the stamps are unusable."""
    if started_at is None:
        return 0.0
    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(ended_at) if ended_at is not None else now
        return _clamp((end - start).total_seconds(), MAX_ENTRY_SECONDS)
    except (TypeError, ValueError, OverflowError):  # unparsable, or naive-vs-aware mix
        return 0.0


class HeadReader(Protocol):
    """`repo_state.RepoHeadReader` (production) or `FakeRepoHeadReader` (tests)."""

    def read(self, repo_paths: Mapping[str, str]) -> dict[str, str]: ...


class Worktree(Protocol):
    """`repo_state.WorktreeProbe` (production) or `FakeWorktreeProbe` (tests)."""

    def snapshot(
        self,
        repo_paths: Mapping[str, str],
        workspace_root: str,
        exclude_abs: frozenset[str] | set[str],
    ) -> frozenset[tuple[object, ...]]: ...


class ResultCache:
    """Implements `types.ResultCacheHook`. Every collaborator is injectable (tests use fakes)."""

    def __init__(
        self,
        store: CacheStore,
        settings: ResultCacheSettings,
        *,
        workspace_root: str,
        cache_root: str,
        heads: HeadReader | None = None,
        worktree: Worktree | None = None,
        cli_versions: Callable[[str], str] | None = None,
        environ: Mapping[str, str] | None = None,
        strict: bool = False,
        warnings: tuple[str, ...] = (),
    ) -> None:
        self._store = store
        self._settings = settings
        self._ws = workspace_root
        self._cache_root = cache_root
        self._heads = heads if heads is not None else RepoHeadReader(workspace_root=workspace_root)
        self._worktree = worktree if worktree is not None else WorktreeProbe()
        self._deps = KeyDeps(
            cli_version_of=cli_versions if cli_versions is not None else CliVersionReader().version
        )
        self._environ: Mapping[str, str] = environ if environ is not None else {}
        self._strict = strict
        self._warnings = tuple(warnings)
        self._mode = settings.mode
        self._source = settings.source
        self._ttl = settings.ttl_days
        self._max_entry = settings.max_entry_bytes
        # A coordinator built for a mode that is neither on nor shadow does nothing at all.
        self._disabled = settings.mode not in (MODE_ON, MODE_SHADOW)
        self._store_warned = False

    @classmethod
    def from_settings(
        cls, *, workspace_root: str | Path, settings: ResultCacheSettings
    ) -> ResultCache:
        """Build the production coordinator. Never named `open`: it performs no store I/O.

        The only filesystem access is the `lstat`-only nested-repo marker check, whose result
        fills `warnings` (printed by the CLI banner).
        """
        ws = str(Path(workspace_root).resolve())
        store = LocalFsCacheStore.for_workspace(
            ws, max_bytes=settings.max_bytes, ttl_days=settings.ttl_days
        )
        marker = nested_repo_marker(ws)
        warnings: tuple[str, ...] = ()
        if marker is not None:
            warnings = (
                f"workspace {ws} is inside the git repository at {marker}; its HEAD is not part "
                "of result-cache keys (see the authoring guide)",
            )
        return cls(
            store,
            settings,
            workspace_root=ws,
            cache_root=store.root,
            environ=os.environ,
            warnings=warnings,
        )

    @property
    def root(self) -> str:
        return self._cache_root

    @property
    def warnings(self) -> tuple[str, ...]:
        return self._warnings

    # ------------------------------------------------------------ engine-facing boundary (D32)
    def lookup(self, request: LookupRequest, log: logging.LoggerAdapter) -> LookupOutcome:
        if self._disabled:
            return LookupOutcome(False, None, None)
        try:
            return self._lookup(request, log)
        except EXPECTED_ERRORS as exc:
            log.error(
                "result cache lookup failed (%s); treating as a miss",
                type(exc).__name__,
                exc_info=True,
                extra={"event": EVENT_SKIP, "phase": _PHASE_LOOKUP, "reason": REASON_STORE_ERROR},
            )
            record = make_miss_record(request, REASON_STORE_ERROR, None, self._mode, self._source)
            return LookupOutcome(False, record, None)
        except Exception as exc:
            if self._strict:
                raise
            self._disable(log, exc)
            return LookupOutcome(False, None, None)

    def store_success(
        self,
        pending: PendingStore,
        *,
        ts: TaskRunState,
        now: datetime,
        log: logging.LoggerAdapter,
    ) -> StoreResult:
        if self._disabled:
            return StoreResult(False, REASON_CACHE_DISABLED)
        try:
            return self._store_success(pending, ts=ts, now=now, log=log)
        except EXPECTED_ERRORS as exc:
            log.error(
                "result cache store failed (%s); the result is not cached",
                type(exc).__name__,
                exc_info=True,
                extra={"event": EVENT_SKIP, "phase": _PHASE_STORE, "reason": REASON_STORE_ERROR},
            )
            return StoreResult(False, REASON_STORE_ERROR)
        except Exception as exc:
            if self._strict:
                raise
            self._disable(log, exc)
            return StoreResult(False, REASON_CACHE_DISABLED)

    def _disable(self, log: logging.LoggerAdapter, exc: BaseException) -> None:
        self._disabled = True
        log.error(
            "result cache disabled for the rest of this run after an unexpected error",
            exc_info=True,
            extra={"event": EVENT_DISABLED, "error_type": type(exc).__name__},
        )

    # ------------------------------------------------------------------------------ lookup
    def _lookup(self, req: LookupRequest, log: logging.LoggerAdapter) -> LookupOutcome:
        if not task_cache_policy(req.task, req.workflow, injected=req.injected):
            log.debug(
                "not opted in",
                extra={"event": EVENT_SKIP, "phase": _PHASE_LOOKUP, "reason": REASON_NOT_OPTED_IN},
            )
            return LookupOutcome(False, None, None)  # no record (D1)
        el = check_eligibility(
            req.task, req.workflow, req.agents, integration_active=req.integration_active
        )
        if not el.eligible:
            return self._ineligible(req, el.reason or "", el.detail, log)
        if not isinstance(req.artifact_store, LocalFsArtifactStore):
            return self._ineligible(req, REASON_ARTIFACT_STORE_UNSUPPORTED, None, log)
        try:
            self._store.check()  # root + ownership + layout; creates nothing
        except (CacheLayoutError, SafeIOError) as exc:
            self._warn_store_unavailable_once(log, exc)
            record = make_miss_record(req, REASON_STORE_UNAVAILABLE, None, self._mode, self._source)
            return LookupOutcome(False, record, None)
        try:
            heads = self._heads.read(req.repo_paths)  # key + guard 2 (always)
            agent = resolve_effective_agent(
                req.task, req.agents[req.task.agent], req.workflow.defaults.model
            )
            key = build_cache_key(self._key_request(req, agent, heads), self._deps)
        except UncacheableError as exc:
            return self._ineligible(req, exc.reason, exc.detail, log)
        try:
            return self._serve(req, key, heads, log)
        except CacheUnsafePathError:
            # D33: a symlinked / foreign cache component. Never followed, never evicted.
            log.warning(
                "result cache path is unsafe; not evicted",
                extra={"event": EVENT_CORRUPT, "key": key.key, "reason": REASON_UNSAFE_PATH},
            )
            return self._miss(req, REASON_UNSAFE_PATH, key, None, None, log)

    def _serve(
        self,
        req: LookupRequest,
        key: CacheKey,
        heads: Mapping[str, str],
        log: logging.LoggerAdapter,
    ) -> LookupOutcome:
        """Everything after the key is known. A store `CacheUnsafePathError` propagates (D33)."""
        try:
            entry = self._store.get_entry(key.key)
        except CacheIntegrityError as exc:
            self._evict(key.key, exc.reason, log, corrupt=True)
            return self._miss_storable(req, exc.reason, key, heads, log)
        if entry is None:
            return self._miss_storable(req, REASON_NOT_FOUND, key, heads, log)
        if self._ttl is not None and is_expired(entry.created_at, req.now, self._ttl):
            self._evict(key.key, REASON_EXPIRED, log)
            return self._miss_storable(req, REASON_EXPIRED, key, heads, log)
        if self._mode == MODE_SHADOW:  # measure, never restore
            if not all(self._store.has_blob(o.sha256) for o in entry.outputs):
                self._evict(key.key, REASON_BLOB_MISSING, log, corrupt=True)
                return self._miss_storable(req, REASON_BLOB_MISSING, key, heads, log)
            pending, store_reason = self._storable(req, key, heads, log)  # lazy guard 3
            log.info(
                "result cache would hit",
                extra={
                    "event": EVENT_WOULD_HIT,
                    "key": key.key,
                    "saved_cost_usd": entry.usage.cost_usd,
                    "source_run_id": entry.source.run_id,
                },
            )
            record = make_would_hit_record(
                entry, key.key, req, self._mode, self._source, store_reason=store_reason
            )
            return LookupOutcome(False, record, pending)
        try:
            restore_outputs(
                entry,
                key.output_abs,
                self._store,
                workspace_root=self._ws,
                max_entry_bytes=self._max_entry,
            )
        except RestoreMiss as miss:
            if miss.evict:
                self._evict(key.key, miss.reason, log, corrupt=True, blob=miss.blob)
                if miss.blob:
                    self._store.delete_blob(miss.blob)  # content-addressed and wrong
            return self._miss(req, miss.reason, key, None, None, log, detail=miss.detail or None)
        try:
            self._store.touch_entry(key.key, req.now)
        except (OSError, CacheUnsafePathError):  # LRU is best effort
            log.debug("result cache: touch failed", exc_info=True)
        log.info(
            "result cache hit",
            extra={
                "event": EVENT_HIT,
                "key": key.key,
                "outputs": [{"path": o.path, "sha256": o.sha256} for o in entry.outputs],
                "bytes": sum(o.size for o in entry.outputs),
                "saved_cost_usd": entry.usage.cost_usd,
                "source_run_id": entry.source.run_id,
                "source_created_at": entry.created_at.isoformat(),
                "source_ao_version": entry.source.ao_version,
            },
        )
        # A hit never takes the guard-3 snapshot: it is store-only (manager B, Rev 3).
        return LookupOutcome(
            True, make_hit_record(entry, key.key, req, self._mode, self._source), None
        )

    def _storable(
        self,
        req: LookupRequest,
        key: CacheKey,
        heads: Mapping[str, str],
        log: logging.LoggerAdapter,
    ) -> tuple[PendingStore | None, str | None]:
        """Lazy guard-3 snapshot, taken only once an outcome is known to be storable."""
        try:
            worktree = self._worktree.snapshot(
                req.repo_paths, self._ws, exclude_abs=frozenset(key.output_abs.values())
            )
        except UncacheableError as exc:  # REASON_REPO_WORKTREE_PROBE_FAILED
            log.info(
                "result cache: outcome not storable",
                extra={
                    "event": EVENT_SKIP,
                    "phase": _PHASE_STORE,
                    "reason": exc.reason,
                    "reason_detail": exc.detail,
                },
            )
            return None, exc.reason  # NOT storable; never ineligible
        return PendingStore(req, key, heads, worktree), None

    def _miss_storable(
        self,
        req: LookupRequest,
        reason: str,
        key: CacheKey,
        heads: Mapping[str, str],
        log: logging.LoggerAdapter,
    ) -> LookupOutcome:
        pending, store_reason = self._storable(req, key, heads, log)
        return self._miss(req, reason, key, pending, store_reason, log)

    # ------------------------------------------------------------------------------- store
    def _store_success(
        self, pending: PendingStore, *, ts: TaskRunState, now: datetime, log: logging.LoggerAdapter
    ) -> StoreResult:
        req = pending.request
        try:
            heads_now = self._heads.read(req.repo_paths)
            if dict(heads_now) != dict(pending.heads):
                return self._skip(REASON_REPO_HEAD_MOVED, log)  # guard 2
            agent = resolve_effective_agent(
                req.task, req.agents[req.task.agent], req.workflow.defaults.model
            )
            again = build_cache_key(
                self._key_request(req, agent, heads_now),
                self._deps,
                preseed=pending.key.preseed,
            )
            if again.key != pending.key.key:  # guard 1
                return self._skip(REASON_KEY_CHANGED_DURING_RUN, log, components=again.components)
            worktree = self._worktree.snapshot(
                req.repo_paths, self._ws, exclude_abs=frozenset(pending.key.output_abs.values())
            )
            if worktree != pending.worktree:  # guard 3
                return self._skip(REASON_REPO_WORKTREE_CHANGED, log)
        except UncacheableError as exc:  # incl. a probe failure at settle
            return self._skip(exc.reason, log)
        try:
            outputs = capture_outputs(
                pending.key.output_abs, self._store, max_entry_bytes=self._max_entry
            )
        except StoreSkip as skip:
            return self._skip(skip.reason, log)
        entry = self._build_entry(pending, outputs, ts, now)
        try:
            self._store.put_entry(entry)
        except CacheTooLargeError:
            return self._skip(REASON_ENTRY_TOO_LARGE, log)
        log.info(
            "result cache store",
            extra={
                "event": EVENT_STORE,
                "key": entry.key,
                "outputs": len(outputs),
                "bytes": sum(o.size for o in outputs),
            },
        )
        try:
            report = self._store.maybe_enforce_limits(now=now)
        except (
            CacheError,
            OSError,
            SafeIOError,
        ):  # the entry is stored; maintenance is best effort
            report = None
        if report is not None and report.deferred:
            log.warning(
                "result cache over its size cap; run `ao cache prune`",
                extra={"event": EVENT_EVICT, "reason": REASON_EVICT_DEFERRED},
            )
        elif report is not None and report.total_removed:
            log.info(
                "result cache evicted",
                extra={
                    "event": EVENT_EVICT,
                    "reason": REASON_EVICT_LRU,
                    "entries": report.total_removed,
                },
            )
        return StoreResult(True)

    def _build_entry(
        self,
        pending: PendingStore,
        outputs: list[OutputRecord],
        ts: TaskRunState,
        now: datetime,
    ) -> CacheEntry:
        req = pending.request
        return CacheEntry(
            schema=ENTRY_SCHEMA,
            key=pending.key.key,
            key_schema=KEY_SCHEMA_VERSION,
            created_at=now,
            source=EntrySource(
                workflow_id=clip_text(req.workflow.id),
                task_id=clip_text(req.task.id),
                run_id=clip_text(req.run_id),
                agent=clip_text(req.task.agent),
                ao_version=clip_text(__version__),
                cli_version=clip_text(pending.key.cli_version),
            ),
            usage=EntryUsage(
                cost_usd=_clamp(ts.cumulative_cost_usd, MAX_ENTRY_COST_USD),
                input_tokens=_clamp_int(ts.cumulative_input_tokens, MAX_ENTRY_TOKENS),
                output_tokens=_clamp_int(ts.cumulative_output_tokens, MAX_ENTRY_TOKENS),
                cache_creation_input_tokens=_clamp_int(
                    ts.cumulative_cache_creation_input_tokens, MAX_ENTRY_TOKENS
                ),
                cache_read_input_tokens=_clamp_int(
                    ts.cumulative_cache_read_input_tokens, MAX_ENTRY_TOKENS
                ),
                duration_seconds=_duration_seconds(ts.started_at, ts.ended_at, now),
                attempts=_clamp_int(ts.attempts, MAX_ENTRY_ATTEMPTS),
                model=clip_text(ts.model),
                effort=clip_text(ts.effort),
                actuals_available=ts.cumulative_cost_usd > 0 or ts.cumulative_input_tokens > 0,
            ),
            outputs=outputs,
            key_summary=pending.key.summary,
        )

    # ------------------------------------------------------------------------------ helpers
    def _key_request(
        self,
        req: LookupRequest,
        agent: AgentSpec,
        heads: Mapping[str, str],
    ) -> KeyRequest:
        return KeyRequest(
            task=req.task,
            agent=agent,  # the EFFECTIVE agent (resolve_effective_agent)
            workspace_root=self._ws,
            # `lookup` already refused every non-LocalFs store (artifact_store_unsupported).
            artifact_store=cast("LocalFsArtifactStore", req.artifact_store),
            cache_root=self._cache_root,
            general_instruction_paths=req.general_instruction_paths,
            dynamic_input_paths=req.dynamic_input_paths,
            repo_paths=req.repo_paths,
            repo_heads=heads,
            include_repo_heads=self._settings.include_repo_heads,
            control_paths_abs=self._control_paths_abs(req),
            environ=self._environ,
            max_input_bytes=self._settings.max_input_bytes,
            max_input_files=self._settings.max_input_files,
            operator_notes_path=req.operator_notes_path,
        )

    def _control_paths_abs(self, req: LookupRequest) -> frozenset[str]:
        """Files the engine itself reads (verdicts, gates, prompt): never a cached output.

        Unresolvable paths are ignored.
        """
        wf = req.workflow
        raw: list[str | None] = [r.verdict_path for r in wf.branches]
        raw += [loop.gate_output_path for loop in wf.loops]
        raw += [b.verdict_path for b in wf.circuit_breakers]
        raw += [b.path for b in wf.circuit_breakers]
        raw.append(wf.prompt_path)
        resolved = (try_resolve(req.artifact_store, p) for p in raw if p)
        return frozenset(r for r in resolved if r is not None)

    def _ineligible(
        self, req: LookupRequest, reason: str, detail: str | None, log: logging.LoggerAdapter
    ) -> LookupOutcome:
        extra: dict[str, object] = {"event": EVENT_SKIP, "phase": _PHASE_LOOKUP, "reason": reason}
        if detail:
            extra["reason_detail"] = detail
        log.info("result cache: task not cacheable", extra=extra)
        record = make_ineligible_record(req, reason, detail or None, self._mode, self._source)
        return LookupOutcome(False, record, None)

    def _miss(
        self,
        req: LookupRequest,
        reason: str,
        key: CacheKey | None,
        pending: PendingStore | None,
        store_reason: str | None,
        log: logging.LoggerAdapter,
        *,
        detail: str | None = None,
    ) -> LookupOutcome:
        extra: dict[str, object] = {"event": EVENT_MISS, "reason": reason}
        if key is not None:
            extra["key"] = key.key
            extra["components"] = dict(key.components)
        log.info("result cache miss", extra=extra)
        record = make_miss_record(
            req,
            reason,
            key.key if key is not None else None,
            self._mode,
            self._source,
            detail,
            store_reason=store_reason,
        )
        return LookupOutcome(False, record, pending)

    def _evict(
        self,
        key: str,
        reason: str,
        log: logging.LoggerAdapter,
        *,
        corrupt: bool = False,
        blob: str | None = None,
    ) -> None:
        """Remove an entry (`delete_entry` runs the component checks first). Never reached for
        `unsafe_path`: that error propagates before any eviction (D33)."""
        self._store.delete_entry(key)
        log.info("result cache evict", extra={"event": EVENT_EVICT, "key": key, "reason": reason})
        if corrupt:
            extra: dict[str, object] = {"event": EVENT_CORRUPT, "key": key, "reason": reason}
            if blob:
                extra["blob"] = blob
            log.warning("result cache entry is corrupt; evicted", extra=extra)

    def _skip(
        self,
        reason: str,
        log: logging.LoggerAdapter,
        *,
        components: Mapping[str, str] | None = None,
    ) -> StoreResult:
        extra: dict[str, object] = {"event": EVENT_SKIP, "phase": _PHASE_STORE, "reason": reason}
        if components is not None:
            extra["components"] = dict(components)
        level = logging.WARNING if reason == REASON_STORE_ERROR else logging.INFO
        log.log(level, "result cache: not stored", extra=extra)
        return StoreResult(False, reason)

    def _warn_store_unavailable_once(self, log: logging.LoggerAdapter, exc: Exception) -> None:
        if self._store_warned:
            return
        self._store_warned = True
        log.warning(
            "result cache store is unavailable; lookups are misses",
            extra={
                "event": EVENT_SKIP,
                "phase": _PHASE_LOOKUP,
                "reason": REASON_STORE_UNAVAILABLE,
                "reason_detail": clip_text(str(exc)),
            },
        )


__all__ = ["EXPECTED_ERRORS", "ResultCache"]
