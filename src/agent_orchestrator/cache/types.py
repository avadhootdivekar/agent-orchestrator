"""Contracts of the result cache (HLD 8.2.1, 8.4.2, 8.4.3, 8.6.1, 14.1; ADR-0019 D17, D28).

Interface-first module: the canonical serializer, the strict entry models and THE parse boundary
for hostile cache data, the report / key / lookup dataclasses, the `ResultCacheHook` protocol, the
two PROVISIONAL store ABCs and the error types. Imports `constants`, `safeio`, `pydantic` and
(type-only) `models` / `artifacts`: `ResultCacheRecord` is added to `models` by a later task, so
it is referenced under `TYPE_CHECKING` only (annotations are postponed).
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, BinaryIO, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from agent_orchestrator.cache.constants import (
    ENTRY_SCHEMA,
    MAX_CONFIG_BYTES,
    MAX_ENTRY_ATTEMPTS,
    MAX_ENTRY_COST_USD,
    MAX_ENTRY_SECONDS,
    MAX_ENTRY_TOKENS,
    MAX_LIST_ITEMS,
    MAX_PATH_CHARS,
    MAX_REASON_CHARS,
    MAX_TEXT_CHARS,
    REASON_BLOB_MISSING,
    REASON_CORRUPT_ENTRY,
    REASON_KEY_MISMATCH,
    STORED_MODE_MASK,
)

if TYPE_CHECKING:
    from agent_orchestrator.artifacts import ArtifactStore, LocalFsArtifactStore

    # `ResultCacheRecord` is added to `models` by T-28J9oR; drop the ignore once it lands
    # (`unused-ignore` keeps warn_unused_ignores quiet in the meantime).
    from agent_orchestrator.models import (  # type: ignore[attr-defined,unused-ignore]
        AgentSpec,
        ResultCacheRecord,
        TaskRunState,
        TaskSpec,
        WorkflowSpec,
    )


def canonical_json(obj: object) -> str:
    """THE canonical serializer: sorted keys, compact, ASCII, no NaN/Infinity (ValueError)."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


# ---------------------------------------------------------------------------------- errors (14.1)
def _clip(text: str) -> str:
    return text[:MAX_TEXT_CHARS]


class CacheError(Exception):
    """Base of store/entry failures. Never escapes ResultCache to the engine (D32)."""

    reason: str
    detail: str

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = _clip(detail)
        super().__init__(f"{reason}: {self.detail}" if self.detail else reason)


class CacheIntegrityError(CacheError):
    """corrupt_entry | key_mismatch | blob_corrupt."""


class CacheUnsafePathError(CacheError):
    """unsafe_path: never followed, never evicted (D33). NOT an integrity error."""


class CacheBlobMissingError(CacheError):
    """blob_missing. Constructed from the missing blob's sha256 (the HLD 8.4.3 read_blob form)."""

    def __init__(self, sha256: str = "") -> None:
        super().__init__(REASON_BLOB_MISSING, sha256)


class CacheTooLargeError(CacheError):
    """entry_too_large."""


class CacheLayoutError(CacheError):
    """store_unavailable."""


class UncacheableError(Exception):
    """Eligibility / key-build refusal: `reason` is a REASON_* constant."""

    reason: str
    detail: str

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = _clip(detail)
        super().__init__(f"{reason}: {self.detail}" if self.detail else reason)


class StoreSkip(Exception):
    """Capture-time skip: the outcome is simply not stored."""

    reason: str
    detail: str

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = _clip(detail)
        super().__init__(f"{reason}: {self.detail}" if self.detail else reason)


class RestoreMiss(Exception):
    """A restore-time miss. Never storable. `evict` says whether the entry should be removed;
    `blob` is the offending blob sha256 (deleted too when the blob itself is corrupt)."""

    reason: str
    evict: bool
    blob: str | None
    detail: str

    def __init__(
        self, reason: str, evict: bool = False, blob: str | None = None, detail: str = ""
    ) -> None:
        self.reason = reason
        self.evict = evict
        self.blob = blob
        self.detail = _clip(detail)
        super().__init__(f"{reason}: {self.detail}" if self.detail else reason)


# ------------------------------------------------------------------ entry models (8.4.2)
# Bounded, strict pydantic models. `extra="ignore"` gives additive forward compatibility in v1.
PathStr = Annotated[str, Field(max_length=MAX_PATH_CHARS)]
TextStr = Annotated[str, Field(max_length=MAX_TEXT_CHARS)]
HexStr = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class OutputRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: PathStr  # normalized relative path: COMPARISON ONLY, never used for I/O
    # Reserved for future directory outputs (critic #5). A literal, not KIND_FILE: mypy cannot
    # narrow a plain str constant to Literal["file"]; a test pins the two together.
    kind: Literal["file"] = "file"
    sha256: HexStr
    size: int = Field(ge=0, le=MAX_CONFIG_BYTES)
    mode: int = Field(ge=0, le=STORED_MODE_MASK)


class EntryUsage(BaseModel):
    """The source run's actuals; the `saved_*` estimates are copied from here (D16)."""

    model_config = ConfigDict(extra="ignore")
    cost_usd: float = Field(default=0.0, ge=0, le=MAX_ENTRY_COST_USD, allow_inf_nan=False)
    input_tokens: int = Field(default=0, ge=0, le=MAX_ENTRY_TOKENS)
    output_tokens: int = Field(default=0, ge=0, le=MAX_ENTRY_TOKENS)
    # Claude PROMPT-cache counters of the source run, copied verbatim.
    cache_creation_input_tokens: int = Field(default=0, ge=0, le=MAX_ENTRY_TOKENS)
    cache_read_input_tokens: int = Field(default=0, ge=0, le=MAX_ENTRY_TOKENS)
    duration_seconds: float = Field(default=0.0, ge=0, le=MAX_ENTRY_SECONDS, allow_inf_nan=False)
    attempts: int = Field(default=0, ge=0, le=MAX_ENTRY_ATTEMPTS)
    model: TextStr | None = None
    effort: TextStr | None = None
    actuals_available: bool = False


class EntrySource(BaseModel):
    """Provenance; display-only."""

    model_config = ConfigDict(extra="ignore")
    workflow_id: TextStr
    task_id: TextStr
    run_id: TextStr
    agent: TextStr
    ao_version: TextStr
    cli_version: TextStr | None = None


class KeySummary(BaseModel):
    """Non-sensitive key description (D21): never argv, prompt, templates or extra_args."""

    model_config = ConfigDict(extra="ignore")
    key_schema: int = Field(ge=1, le=1000)
    executor: str = Field(max_length=MAX_REASON_CHARS)
    model: TextStr | None = None
    effort: str | None = Field(default=None, max_length=MAX_REASON_CHARS)
    max_turns: int | None = Field(default=None, ge=0, le=MAX_ENTRY_ATTEMPTS)
    instruction: PathStr
    inputs: list[PathStr] = Field(default=[], max_length=MAX_LIST_ITEMS)
    dynamic_inputs: list[PathStr] = Field(default=[], max_length=MAX_LIST_ITEMS)
    general_instructions: list[PathStr] = Field(default=[], max_length=MAX_LIST_ITEMS)
    outputs: list[PathStr] = Field(default=[], max_length=MAX_LIST_ITEMS)
    # rel path -> sha256, display-only
    digests: dict[PathStr, HexStr] = Field(default={}, max_length=MAX_LIST_ITEMS)
    repo_heads: dict[TextStr, TextStr] | None = None
    # D30; also shown by `ao cache show`
    components: dict[TextStr, TextStr] = Field(default={}, max_length=MAX_LIST_ITEMS)


class CacheEntry(BaseModel):
    # The alias avoids shadowing BaseModel.schema().
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    schema_: str = Field(alias="schema", max_length=MAX_REASON_CHARS)
    key: HexStr
    key_schema: int = Field(ge=1, le=1000)
    created_at: AwareDatetime  # naive datetimes are rejected (D28)
    source: EntrySource
    usage: EntryUsage
    outputs: list[OutputRecord] = Field(min_length=1, max_length=MAX_LIST_ITEMS)
    key_summary: KeySummary

    def to_canonical_bytes(self) -> bytes:
        """Sorted keys, compact, ASCII: stable bytes for a future signature (non-MVP 2)."""
        return canonical_json(self.model_dump(mode="json", by_alias=True)).encode("ascii")


def parse_entry_bytes(raw: bytes, expected_key: str) -> CacheEntry:
    """THE parse boundary for hostile cache data (ADR-0019 D28).

    A TOTAL function: it returns an entry or raises CacheIntegrityError, never anything else.
    """
    try:
        data = json.loads(raw)  # ValueError, RecursionError or UnicodeDecodeError possible
        if not isinstance(data, dict):
            raise CacheIntegrityError(REASON_CORRUPT_ENTRY, "not an object")
        if data.get("schema") != ENTRY_SCHEMA:
            raise CacheIntegrityError(REASON_CORRUPT_ENTRY, "schema")
        entry = CacheEntry.model_validate(data)
    except CacheIntegrityError:
        raise
    except Exception as exc:  # hostile input: ANY failure is "corrupt"
        raise CacheIntegrityError(REASON_CORRUPT_ENTRY, type(exc).__name__) from None
    if entry.key != expected_key:
        raise CacheIntegrityError(REASON_KEY_MISMATCH, "entry key != file name")
    return entry


# ------------------------------------------------------------------ key contracts (8.2.1)
@dataclass(frozen=True)
class Digest:
    kind: str  # KIND_FILE | KIND_DIR
    sha256: str
    size: int  # file: bytes; dir: manifest entries


@dataclass(frozen=True)
class KeyRequest:
    task: TaskSpec
    agent: AgentSpec  # the EFFECTIVE agent (models.resolve_effective_agent)
    workspace_root: str  # LocalFsArtifactStore.root (resolved)
    artifact_store: LocalFsArtifactStore  # resolve() is the engine's path guard
    cache_root: str
    # ABSOLUTE paths, from Orchestrator._resolve_general_instructions
    general_instruction_paths: tuple[str, ...]
    dynamic_input_paths: tuple[str, ...]  # raw strings exactly as handed to the executor
    repo_paths: Mapping[str, str]  # ABSOLUTE ctx.repo_paths
    # PRECOMPUTED by the coordinator; the HEAD guard (D13) compares against them
    repo_heads: Mapping[str, str]
    include_repo_heads: bool
    # resolved engine-read control files (router/loop/breaker verdicts, prompt_path)
    control_paths_abs: frozenset[str]
    environ: Mapping[str, str]  # for the fingerprint env allowlist
    max_input_bytes: int
    max_input_files: int


@dataclass(frozen=True)
class KeyDeps:
    # fingerprint.CliVersionReader.version (memoized by binary identity); tests inject a fake
    cli_version_of: Callable[[str], str]


@dataclass(frozen=True)
class CacheKey:
    key: str
    summary: KeySummary
    output_paths: tuple[str, ...]  # normalized relative paths, sorted
    output_abs: Mapping[str, str]  # rel -> absolute destination (spec-derived, resolved)
    preseed: Mapping[str, Digest | None]  # abs output path -> prior digest (None = absent)
    components: Mapping[str, str]  # top-level doc field -> sha256[:12] (miss diagnostics)
    cli_version: str | None  # executor_fingerprint.cli_version (None for fake); provenance


# ------------------------------------------------------------------ store contracts (8.4.3, 13.4)
@dataclass(frozen=True)
class BlobRef:
    sha256: str
    size: int
    new: bool  # False when the blob already existed (dedupe)


@dataclass(frozen=True)
class EntryInfo:
    """One `entries/v1` file as seen by CacheAdmin.iter_entries (streaming, never follows links).

    `entry` is None for an invalid file (`error` holds the reason, e.g. corrupt_entry). An
    `anomaly` is a non-entry (symlinked shard, junk file, non-hex name): it is counted and
    reported, never parsed and never deleted; `key` then carries its name.
    """

    key: str
    size: int
    mtime: float  # file mtime: the LRU "last used" clock
    entry: CacheEntry | None = None
    error: str | None = None
    anomaly: bool = False


@dataclass(frozen=True)
class CacheStats:
    """`ao cache stats` (13.4 `ao.result-cache.stats/v1`), flattened."""

    root: str
    exists: bool
    entries: int = 0
    invalid_entries: int = 0
    expired_entries: int = 0
    foreign_version_dirs: tuple[str, ...] = ()
    blobs: int = 0
    orphan_blobs: int = 0
    tmp_files: int = 0
    trash_dirs: int = 0
    anomalies: int = 0
    entries_bytes: int = 0
    blobs_bytes: int = 0
    referenced_blobs_bytes: int = 0
    orphan_blobs_bytes: int = 0
    total_bytes: int = 0
    max_bytes: int | None = None
    max_entry_bytes: int | None = None
    ttl_days: int | None = None
    oldest_created_at: datetime | None = None
    newest_created_at: datetime | None = None


@dataclass(frozen=True)
class PruneReport:
    """`ao cache prune` (13.4) and the inline enforcement result.

    `deferred=True` (everything else empty) means inline enforcement was skipped because the
    store exceeds the inline bounds (D19): the coordinator logs a WARNING.
    """

    removed_entries: Mapping[str, int] = field(default_factory=dict)  # reason -> count
    removed_blobs: int = 0
    removed_tmp: int = 0
    removed_trash: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    dry_run: bool = False
    deferred: bool = False

    @property
    def total_removed(self) -> int:
        return sum(self.removed_entries.values())


@dataclass(frozen=True)
class ClearReport:
    removed_entries: int = 0
    removed_blobs: int = 0
    bytes_freed: int = 0


@dataclass(frozen=True)
class VerifyProblem:
    # corrupt_entry | key_mismatch | missing_blob | corrupt_blob | orphan_blob |
    # foreign_version | symlink | unexpected_file  (13.4 `ao.result-cache.verify/v1`)
    kind: str
    key: str | None = None
    blob: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class VerifyReport:
    """Read-only (`verify --repair` is deferred, non-MVP 16): there is no `repaired` field."""

    ok: bool
    problems: tuple[VerifyProblem, ...] = ()
    entries_checked: int = 0
    blobs_checked: int = 0


class CacheStore(ABC):
    """HOT PATH, used by the coordinator. PROVISIONAL (D17): a remote backend will need an
    output-I/O seam. Every method validates keys and shas before building a path."""

    root: str

    @abstractmethod
    def check(self) -> None:
        """Pre-flight: root, ownership, layout. Creates nothing.
        Raises CacheLayoutError or SafeIOError."""

    @abstractmethod
    def get_entry(self, key: str) -> CacheEntry | None:
        """None if absent. Raises CacheIntegrityError or CacheUnsafePathError."""

    @abstractmethod
    def put_entry(self, entry: CacheEntry) -> int:
        """Bytes written. Raises CacheTooLargeError, CacheUnsafePathError or OSError."""

    @abstractmethod
    def touch_entry(self, key: str, at: datetime) -> None:
        """LRU touch. A missing entry is a no-op. Raises CacheUnsafePathError."""

    @abstractmethod
    def delete_entry(self, key: str) -> bool:
        """False if absent. Raises CacheUnsafePathError (and then deletes nothing)."""

    @abstractmethod
    def has_blob(self, sha256: str) -> bool:
        """Shadow-mode restorability check. Raises CacheUnsafePathError."""

    @abstractmethod
    def put_blob(self, src: BinaryIO, *, max_bytes: int) -> BlobRef:
        """Raises CacheTooLargeError, CacheUnsafePathError or OSError."""

    @abstractmethod
    def read_blob(self, sha256: str, dest: BinaryIO, *, max_bytes: int) -> int:
        """Bytes copied. Raises CacheBlobMissingError, CacheIntegrityError or
        CacheUnsafePathError."""

    @abstractmethod
    def delete_blob(self, sha256: str) -> bool:
        """False if absent. Raises CacheUnsafePathError."""

    @abstractmethod
    def maybe_enforce_limits(self, *, now: datetime) -> PruneReport | None:
        """Bounded inline enforcement (D19)."""


class CacheAdmin(ABC):
    """MAINTENANCE (`ao cache ...`). PROVISIONAL."""

    @abstractmethod
    def iter_entries(self) -> Iterator[EntryInfo]:
        """Streaming; never follows symlinks."""

    @abstractmethod
    def stats(self, *, now: datetime) -> CacheStats: ...

    @abstractmethod
    def prune(
        self,
        *,
        now: datetime,
        max_bytes: int | None,
        ttl_days: int | None,
        dry_run: bool = False,
    ) -> PruneReport: ...

    @abstractmethod
    def clear(self) -> ClearReport: ...

    @abstractmethod
    def verify(self) -> VerifyReport:
        """Read-only (`--repair` is deferred, non-MVP 16)."""


# ------------------------------------------------------------------ coordinator contracts (8.6.1)
@dataclass(frozen=True)
class LookupRequest:
    task: TaskSpec
    workflow: WorkflowSpec
    agents: Mapping[str, AgentSpec]
    run_id: str
    injected: bool  # state.tasks[tid].origin == "injected"
    integration_active: bool  # state.integration.active
    artifact_store: ArtifactStore  # the engine's self._store
    general_instruction_paths: tuple[str, ...]
    dynamic_input_paths: tuple[str, ...]
    repo_paths: Mapping[str, str]  # ctx.repo_paths (absolute)
    dispatch_cycle: int  # ts.dispatch_cycle AFTER the engine's increment (kept, D12)
    now: datetime  # engine clock


@dataclass(frozen=True)
class PendingStore:  # carried on _RunContext.result_cache_pending[tid]
    request: LookupRequest
    key: CacheKey
    heads: Mapping[str, str]  # lookup-time HEADs (guard 2, always)
    worktree: frozenset[tuple[object, ...]]  # lookup-time snapshot (guard 3), taken lazily


@dataclass(frozen=True)
class LookupOutcome:
    hit: bool
    record: ResultCacheRecord | None  # None => not opted in / cache disabled: write nothing
    pending: PendingStore | None  # set ONLY for a storable outcome


@dataclass(frozen=True)
class StoreResult:
    stored: bool
    reason: str | None = None


class ResultCacheHook(Protocol):
    """What engine.py depends on (mirrors the BudgetManager / Monitor injection pattern)."""

    def lookup(self, request: LookupRequest, log: logging.LoggerAdapter) -> LookupOutcome: ...

    def store_success(
        self,
        pending: PendingStore,
        *,
        ts: TaskRunState,
        now: datetime,
        log: logging.LoggerAdapter,
    ) -> StoreResult: ...
