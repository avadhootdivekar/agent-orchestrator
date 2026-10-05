"""Test doubles for the result cache (HLD 18, delivered by T-FJH6LI).

Everything here is deterministic and I/O-free: an in-memory store implementing both ABCs, and
configurable stand-ins for the HEAD reader, the worktree probe, the CLI-version reader and the
git subprocess runner. Each can be set to return a value or to raise, and records its calls so a
test can spy ("a hit never calls WorktreeProbe.snapshot").
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from hashlib import sha256 as _sha256
from typing import Any, BinaryIO

from agent_orchestrator.cache.constants import (
    ENTRY_SCHEMA,
    EVICT_LOW_WATER_RATIO,
    HASH_CHUNK_BYTES,
    MAX_ENTRY_FILE_BYTES,
    REASON_BLOB_CORRUPT,
    REASON_ENTRY_TOO_LARGE,
    REASON_EVICT_LRU,
    REASON_EXPIRED,
    SHA256_HEX_RE,
)
from agent_orchestrator.cache.types import (
    BlobRef,
    CacheAdmin,
    CacheBlobMissingError,
    CacheEntry,
    CacheIntegrityError,
    CacheStats,
    CacheStore,
    CacheTooLargeError,
    ClearReport,
    EntryInfo,
    EntrySource,
    EntryUsage,
    KeySummary,
    OutputRecord,
    PruneReport,
    VerifyProblem,
    VerifyReport,
)

FAKE_CLI_VERSION = "0.0.0-test (Claude Code)"
FAKE_STORE_ROOT = "/in-memory/.orchestrator/cache"


FIXED_CREATED_AT = datetime(2026, 10, 5, 10, 15, 2, tzinfo=UTC)


def sha_of(data: bytes) -> str:
    return _sha256(data).hexdigest()


def key_of(seed: str) -> str:
    """A deterministic valid 64-hex key for tests."""
    return sha_of(seed.encode())


def make_entry(
    key: str | None = None,
    *,
    outputs: Sequence[tuple[str, bytes]] = (("out/summary.md", b"hello\n"),),
    created_at: datetime = FIXED_CREATED_AT,
    cost_usd: float = 0.4123,
) -> CacheEntry:
    """A valid entry whose `outputs` carry the sha256 / size of the given (path, content) pairs."""
    key = key or key_of("default-key")
    records = [OutputRecord(path=p, sha256=sha_of(c), size=len(c), mode=0o644) for p, c in outputs]
    return CacheEntry(
        schema=ENTRY_SCHEMA,
        key=key,
        key_schema=1,
        created_at=created_at,
        source=EntrySource(
            workflow_id="wf",
            task_id="t",
            run_id="wf-20261005T101450Z",
            agent="writer",
            ao_version="0.0.0-test",
        ),
        usage=EntryUsage(cost_usd=cost_usd, input_tokens=10, output_tokens=5),
        outputs=records,
        key_summary=KeySummary(
            key_schema=1,
            executor="fake",
            instruction="specs/instr/t.md",
            outputs=[p for p, _ in outputs],
        ),
    )


def _check_hex(value: str, what: str) -> None:
    """The same validation the real store applies before building any path (M-2)."""
    if not isinstance(value, str) or not SHA256_HEX_RE.fullmatch(value):
        raise ValueError(f"invalid {what}")


class InMemoryCacheStore(CacheStore, CacheAdmin):
    """Dict-backed store: {key: CacheEntry}, {sha: bytes}, {key: mtime}.

    `fail_with(method, exc)` makes one method raise (e.g. `get_entry` ->
    `CacheUnsafePathError`); `calls` records `(method, args)` for spying. `enforce_result` is what
    `maybe_enforce_limits` returns.
    """

    def __init__(
        self,
        root: str = FAKE_STORE_ROOT,
        *,
        max_bytes: int | None = None,
        ttl_days: int | None = None,
    ) -> None:
        self.root = root
        self.max_bytes = max_bytes
        self.ttl_days = ttl_days
        self.entries: dict[str, CacheEntry] = {}
        self.blobs: dict[str, bytes] = {}
        self.mtimes: dict[str, float] = {}
        self.entry_sizes: dict[str, int] = {}
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.enforce_result: PruneReport | None = None
        self._failures: dict[str, BaseException] = {}

    # -- test controls ------------------------------------------------------------------
    def fail_with(self, method: str, exc: BaseException | None) -> None:
        """Make `method` raise `exc` on every call (None clears it)."""
        if exc is None:
            self._failures.pop(method, None)
        else:
            self._failures[method] = exc

    def called(self, method: str) -> int:
        return sum(1 for name, _ in self.calls if name == method)

    def _enter(self, method: str, *args: Any) -> None:
        self.calls.append((method, args))
        exc = self._failures.get(method)
        if exc is not None:
            raise exc

    # -- CacheStore -----------------------------------------------------------------------
    def check(self) -> None:
        self._enter("check")

    def get_entry(self, key: str) -> CacheEntry | None:
        _check_hex(key, "key")
        self._enter("get_entry", key)
        return self.entries.get(key)

    def put_entry(self, entry: CacheEntry) -> int:
        _check_hex(entry.key, "key")
        self._enter("put_entry", entry.key)
        body = entry.to_canonical_bytes()
        if len(body) > MAX_ENTRY_FILE_BYTES:
            raise CacheTooLargeError(REASON_ENTRY_TOO_LARGE)
        self.entries[entry.key] = entry
        self.entry_sizes[entry.key] = len(body)
        self.mtimes.setdefault(entry.key, entry.created_at.timestamp())
        return len(body)

    def touch_entry(self, key: str, at: datetime) -> None:
        _check_hex(key, "key")
        self._enter("touch_entry", key, at)
        if key in self.entries:
            self.mtimes[key] = at.timestamp()

    def delete_entry(self, key: str) -> bool:
        _check_hex(key, "key")
        self._enter("delete_entry", key)
        if key not in self.entries:
            return False
        del self.entries[key]
        self.entry_sizes.pop(key, None)
        self.mtimes.pop(key, None)
        return True

    def has_blob(self, sha256: str) -> bool:
        _check_hex(sha256, "sha")
        self._enter("has_blob", sha256)
        return sha256 in self.blobs

    def put_blob(self, src: BinaryIO, *, max_bytes: int) -> BlobRef:
        self._enter("put_blob")
        digest, chunks, total = _sha256(), [], 0
        while chunk := src.read(min(HASH_CHUNK_BYTES, max_bytes + 1 - total)):
            total += len(chunk)
            if total > max_bytes:
                raise CacheTooLargeError(REASON_ENTRY_TOO_LARGE)
            digest.update(chunk)
            chunks.append(chunk)
        sha = digest.hexdigest()
        new = sha not in self.blobs
        if new:
            self.blobs[sha] = b"".join(chunks)
        return BlobRef(sha, total, new=new)

    def read_blob(self, sha256: str, dest: BinaryIO, *, max_bytes: int) -> int:
        _check_hex(sha256, "sha")
        self._enter("read_blob", sha256)
        data = self.blobs.get(sha256)
        if data is None:
            raise CacheBlobMissingError(sha256)
        if not isinstance(data, bytes):  # a test planted an irregular blob
            raise CacheIntegrityError(REASON_BLOB_CORRUPT, "unreadable/irregular")
        data = data[: max_bytes + 1]  # like the real store: the caller sees n > expected
        dest.write(data)
        return len(data)

    def delete_blob(self, sha256: str) -> bool:
        _check_hex(sha256, "sha")
        self._enter("delete_blob", sha256)
        return self.blobs.pop(sha256, None) is not None

    def maybe_enforce_limits(self, *, now: datetime) -> PruneReport | None:
        self._enter("maybe_enforce_limits", now)
        return self.enforce_result

    # -- CacheAdmin (a simple model of the real semantics; T-HjxNQ0 tests the real one) -----
    def iter_entries(self) -> Iterator[EntryInfo]:
        self._enter("iter_entries")
        for key in sorted(self.entries):
            yield EntryInfo(
                key,
                size=self.entry_sizes[key],
                mtime=self.mtimes[key],
                entry=self.entries[key],
            )

    def _referenced(self) -> set[str]:
        return {o.sha256 for e in self.entries.values() for o in e.outputs}

    def _total_bytes(self) -> int:
        refs = self._referenced()
        blob_bytes = sum(len(b) for sha, b in self.blobs.items() if sha in refs)
        return sum(self.entry_sizes.values()) + blob_bytes

    def stats(self, *, now: datetime) -> CacheStats:
        self._enter("stats", now)
        created = sorted(e.created_at for e in self.entries.values())
        refs = self._referenced()
        expired = 0
        if self.ttl_days is not None:
            expired = sum(1 for c in created if now - c > timedelta(days=self.ttl_days))
        return CacheStats(
            root=self.root,
            exists=True,
            entries=len(self.entries),
            expired_entries=expired,
            blobs=len(self.blobs),
            orphan_blobs=len([s for s in self.blobs if s not in refs]),
            entries_bytes=sum(self.entry_sizes.values()),
            blobs_bytes=sum(len(b) for b in self.blobs.values()),
            total_bytes=self._total_bytes(),
            max_bytes=self.max_bytes,
            ttl_days=self.ttl_days,
            oldest_created_at=created[0] if created else None,
            newest_created_at=created[-1] if created else None,
        )

    def prune(
        self,
        *,
        now: datetime,
        max_bytes: int | None,
        ttl_days: int | None,
        dry_run: bool = False,
    ) -> PruneReport:
        self._enter("prune", now, max_bytes, ttl_days, dry_run)
        before = self._total_bytes()
        remove: dict[str, str] = {}
        for key, entry in self.entries.items():
            if ttl_days is not None and now - entry.created_at > timedelta(days=ttl_days):
                remove[key] = REASON_EXPIRED
        total = before - sum(self.entry_sizes[k] for k in remove)
        if max_bytes is not None and total > max_bytes:
            target = int(max_bytes * EVICT_LOW_WATER_RATIO)
            for key in sorted((k for k in self.entries if k not in remove), key=self._lru_order):
                if total <= target:
                    break
                remove[key] = REASON_EVICT_LRU
                total -= self.entry_sizes[key]
        counts: dict[str, int] = {}
        for reason in remove.values():
            counts[reason] = counts.get(reason, 0) + 1
        orphans: list[str] = []
        if not dry_run:
            for key in remove:
                self.delete_entry(key)
            refs = self._referenced()
            orphans = [s for s in self.blobs if s not in refs]
            for sha in orphans:
                del self.blobs[sha]
        return PruneReport(
            removed_entries=counts,
            removed_blobs=len(orphans),
            bytes_before=before,
            bytes_after=self._total_bytes(),
            dry_run=dry_run,
        )

    def _lru_order(self, key: str) -> tuple[float, str]:
        return (self.mtimes[key], key)  # LRU with a key tie-break, like the real store

    def clear(self) -> ClearReport:
        self._enter("clear")
        report = ClearReport(
            removed_entries=len(self.entries),
            removed_blobs=len(self.blobs),
            bytes_freed=self._total_bytes(),
        )
        self.entries.clear()
        self.blobs.clear()
        self.mtimes.clear()
        self.entry_sizes.clear()
        return report

    def verify(self) -> VerifyReport:
        self._enter("verify")
        problems: list[VerifyProblem] = []
        for key in sorted(self.entries):
            for out in self.entries[key].outputs:
                if out.sha256 not in self.blobs:
                    problems.append(VerifyProblem("missing_blob", key=key, blob=out.sha256))
        for sha, data in sorted(self.blobs.items()):
            if _sha256(data).hexdigest() != sha:
                problems.append(VerifyProblem("corrupt_blob", blob=sha))
        return VerifyReport(
            ok=not problems,
            problems=tuple(problems),
            entries_checked=len(self.entries),
            blobs_checked=len(self.blobs),
        )


class _Spy:
    """Shared plumbing: a return value or an exception, plus a call log."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self._error: BaseException | None = None

    def fail(self, exc: BaseException | None) -> None:
        """Make every subsequent call raise `exc` (None clears it)."""
        self._error = exc

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def _record(self, *args: Any) -> None:
        self.calls.append(args)
        if self._error is not None:
            raise self._error


class FakeRepoHeadReader(_Spy):
    """Stands in for `repo_state.RepoHeadReader`: `read(repo_paths) -> {repo id: HEAD}`.

    Returns the configured `heads` restricted to the requested repo ids (a repo with no
    configured head behaves like a non-git repo: omitted). `fail(exc)` makes it raise, e.g.
    `UncacheableError(REASON_REPO_HEAD_UNAVAILABLE, ...)`. `then(heads)` queues successive
    results for the "HEAD moved during the run" scenario (the last one repeats).
    """

    def __init__(self, heads: Mapping[str, str] | None = None) -> None:
        super().__init__()
        self.heads: dict[str, str] = dict(heads or {})
        self._queue: list[dict[str, str]] = []

    def then(self, heads: Mapping[str, str]) -> FakeRepoHeadReader:
        self._queue.append(dict(heads))
        return self

    def read(self, repo_paths: Mapping[str, str]) -> dict[str, str]:
        self._record(dict(repo_paths))
        if self._queue:
            self.heads = self._queue.pop(0) if len(self._queue) > 1 else self._queue[0]
        return {rid: self.heads[rid] for rid in sorted(repo_paths) if rid in self.heads}


class FakeWorktreeProbe(_Spy):
    """Stands in for `repo_state.WorktreeProbe`: `snapshot(...) -> frozenset[tuple]`.

    `snapshots` are returned in order (the last one repeats), so a test can make the settle-time
    snapshot differ from the lookup-time one. `fail(exc)` makes it raise, e.g.
    `UncacheableError(REASON_REPO_WORKTREE_PROBE_FAILED, ...)`.
    """

    def __init__(self, *snapshots: frozenset[tuple[object, ...]]) -> None:
        super().__init__()
        self.snapshots: list[frozenset[tuple[object, ...]]] = list(snapshots) or [frozenset()]

    def snapshot(
        self,
        repo_paths: Mapping[str, str],
        workspace_root: str,
        exclude_abs: frozenset[str] | set[str],
    ) -> frozenset[tuple[object, ...]]:
        self._record(dict(repo_paths), workspace_root, frozenset(exclude_abs))
        index = min(len(self.calls) - 1, len(self.snapshots) - 1)
        return self.snapshots[index]


class FakeCliVersion(_Spy):
    """A `Callable[[str], str]` for `KeyDeps.cli_version_of`."""

    def __init__(
        self, version: str = FAKE_CLI_VERSION, versions: Mapping[str, str] | None = None
    ) -> None:
        super().__init__()
        self.version = version
        self.versions: dict[str, str] = dict(versions or {})

    def __call__(self, binary: str) -> str:
        self._record(binary)
        return self.versions.get(binary, self.version)


def fake_cli_version_of(
    version: str = FAKE_CLI_VERSION,
    *,
    versions: Mapping[str, str] | None = None,
    error: BaseException | None = None,
) -> FakeCliVersion:
    """Factory for `KeyDeps(cli_version_of=fake_cli_version_of("2.1.0 (Claude Code)"))`.

    Returns a callable `binary -> version` (per-binary overrides via `versions`); with `error`
    it raises that exception instead, e.g. `UncacheableError(REASON_EXECUTOR_FINGERPRINT_...)`.
    """
    fake = FakeCliVersion(version, versions)
    fake.fail(error)
    return fake


class FakeVcsRunner:
    """A scripted `isolation.git.Runner`: `(argv, *, cwd, env, timeout) -> CompletedProcess`.

    `on("rev-parse", "HEAD", stdout=b"<sha>\\n")` answers any call whose argv contains those
    tokens in order; `on(..., raises=exc)` raises instead (OSError, TimeoutExpired, ...).
    Unmatched calls return exit 0 with empty output. Rules are matched in registration order.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._rules: list[
            tuple[tuple[str, ...], subprocess.CompletedProcess[bytes] | BaseException]
        ] = []

    def on(
        self,
        *tokens: str,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        raises: BaseException | None = None,
    ) -> FakeVcsRunner:
        result: subprocess.CompletedProcess[bytes] | BaseException = (
            raises
            if raises is not None
            else subprocess.CompletedProcess([], returncode, stdout, stderr)
        )
        self._rules.append((tokens, result))
        return self

    @staticmethod
    def _contains_in_order(argv: Sequence[str], tokens: Sequence[str]) -> bool:
        it = iter(argv)
        return all(token in it for token in tokens)

    def __call__(
        self, argv: list[str], *, cwd: str, env: dict[str, str] | None, timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append({"argv": list(argv), "cwd": cwd, "env": env, "timeout": timeout})
        for tokens, result in self._rules:
            if self._contains_in_order(argv, tokens):
                if isinstance(result, BaseException):
                    raise result
                return subprocess.CompletedProcess(
                    argv, result.returncode, result.stdout, result.stderr
                )
        return subprocess.CompletedProcess(argv, 0, b"", b"")


__all__ = [
    "FAKE_CLI_VERSION",
    "FAKE_STORE_ROOT",
    "FakeCliVersion",
    "FakeRepoHeadReader",
    "FakeVcsRunner",
    "FakeWorktreeProbe",
    "InMemoryCacheStore",
    "fake_cli_version_of",
]
