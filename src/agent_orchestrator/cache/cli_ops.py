"""Behaviour of the `ao cache` commands (E-Rc4Hk8, T-6tRKml; HLD 8.9, 13.4).

`cache/cli.py` is loaded by EVERY `ao` start, so it holds only the Typer surface and imports this
module lazily inside each command body; everything heavy (the store, pydantic models, `json`,
`heapq`) lives here. Every `run_*` function prints its result and RETURNS the process exit code.

Contract shared by all commands:

* The workspace is `--workspace`, then `AO_WORKSPACE_ROOT`, then config discovery
  (`cli._resolve_workspace_root`). Limits come from `.ao/config.yaml cache.*`; the run mode is
  irrelevant (the commands inspect the store even when the cache is off) and nothing here ever
  creates the cache directory.
* `--json` prints exactly ONE JSON document, also on a non-zero exit (an `error` field is added).
  Text mode prints failures to stderr as `ERROR: <message>`.
* Entries are hostile data. Text output passes every entry-derived string through
  `safeio.strip_control_chars` (and clips it); JSON output is escaped by `json.dumps`
  (`ensure_ascii`), so a control character can never reach the terminal either way.
* Exit codes: 0 success, 1 not found / ambiguous / store problem / refused, 2 bad arguments.
* No command deletes through any path but the store API (`delete_entry`, `prune`, `clear`);
  `verify` is read-only.
"""

from __future__ import annotations

import heapq
import json
import os
import stat
import sys
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

import typer

from agent_orchestrator.cache import restore_sweep, safeio
from agent_orchestrator.cache.constants import (
    BLOBS_DIR,
    CLI_MAX_CANDIDATES,
    CLI_MAX_DIR_ITEMS,
    CLI_MAX_DISPLAY_CHARS,
    CLI_MAX_LIMIT,
    ENTRIES_DIR,
    ENTRIES_VERSION_DIR,
    ENTRY_SUFFIX,
    EXIT_ERROR,
    EXIT_OK,
    EXIT_USAGE,
    KEY_DISPLAY_CHARS,
    KEY_PREFIX_RE,
    LS_SORT_KEYS,
    MAX_CONFIG_BYTES,
    MAX_TTL_DAYS,
    REASON_EVICT_INVALID,
    REASON_EVICT_LRU,
    REASON_EXPIRED,
    SCHEMA_CLEAR,
    SCHEMA_LS,
    SCHEMA_PRUNE,
    SCHEMA_RM,
    SCHEMA_SHOW,
    SCHEMA_STATS,
    SCHEMA_VERIFY,
    SHA256_HEX_RE,
    SHARD_CHARS,
    SORT_CREATED,
    SORT_SIZE,
)
from agent_orchestrator.cache.settings import ResultCacheSettings, resolve_result_cache_settings
from agent_orchestrator.cache.store import LocalFsCacheStore, is_expired
from agent_orchestrator.cache.types import (
    CacheEntry,
    CacheError,
    CacheIntegrityError,
    EntryInfo,
    PruneReport,
    VerifyProblem,
)

_STATUS_OK, _STATUS_INVALID, _STATUS_EXPIRED = "ok", "invalid", "expired"
_UNKNOWN_TIME = "unknown"
# Column order of `_ls_line`: key[:12], last used, created, outputs, bytes, task/run, cost.
_LS_HEADER = (
    f"{'KEY':<12}  {'LAST USED':<25}  {'CREATED':<25}  {'OUT':>3}  {'BYTES':>10}  SOURCE  COST"
)
_PRUNE_REASONS = (REASON_EXPIRED, REASON_EVICT_LRU, REASON_EVICT_INVALID)
JsonDoc = dict[str, object]


class _Fail(Exception):
    """A command-level failure: `message` and the exit `code`; `fields` are merged into the JSON
    document (so a failed `rm` still carries `removed: []`). `silent` = already reported."""

    def __init__(
        self,
        message: str,
        code: int = EXIT_ERROR,
        *,
        fields: Mapping[str, object] | None = None,
        silent: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.silent = silent
        self.fields: Mapping[str, object] = fields or {}


def clean(text: object, limit: int = CLI_MAX_DISPLAY_CHARS) -> str:
    """Printable form of an entry-derived (or user-supplied) value: control and invisible format
    characters removed, then clipped."""
    out = safeio.strip_control_chars(str(text))
    return out if len(out) <= limit else out[: limit - 1] + "…"


def _emit(doc: Mapping[str, object]) -> None:
    typer.echo(json.dumps(doc, sort_keys=True))


def _guard(
    schema: str, as_json: bool, body: Callable[[], int], defaults: Mapping[str, object]
) -> int:
    """Run a command body; turn every failure into its exit code and ONE report (JSON document or
    `ERROR:` line). This is the single place errors are reported (never logged below it)."""
    try:
        return body()
    except _Fail as fail:
        message, code, fields, silent = fail.message, fail.code, fail.fields, fail.silent
    except (CacheError, safeio.SafeIOError, OSError) as exc:
        # `str(exc)` of a CacheError is "<reason>: <clipped detail>"; of an OSError it may carry a
        # path. Either way it is cleaned before printing.
        message, code, fields, silent = f"{type(exc).__name__}: {exc}", EXIT_ERROR, {}, False
    message = clean(message)
    if as_json:
        _emit({"schema": schema, **defaults, **fields, "error": message})
    elif not silent:
        typer.echo(f"ERROR: {message}", err=True)
    return code


# ------------------------------------------------------------------ workspace, limits, store
def _resolve_workspace(workspace: str | None, as_json: bool) -> str:
    """`--workspace` > `AO_WORKSPACE_ROOT` > config discovery, via the one shared resolver."""
    from agent_orchestrator import cli as ao_cli  # lazy: this module is loaded by `ao_cli`

    try:
        ws = ao_cli._resolve_workspace_root(workspace, None, None, None)
    except typer.Exit as exc:  # the resolver already printed `ERROR: ...` to stderr
        raise _Fail(
            "cannot resolve the workspace (use --workspace or set AO_WORKSPACE_ROOT)",
            exc.exit_code or EXIT_ERROR,
            silent=not as_json,
        ) from exc
    # A mistyped path must not read as a successful (or "cleared 0 entries") run. A workspace
    # without a cache directory yet is a different, fine case handled by each command.
    if not os.path.isdir(ws):
        raise _Fail(f"workspace does not exist or is not a directory: {ws}", EXIT_USAGE)
    return ws


def _limits() -> ResultCacheSettings:
    """`cache.*` of `.ao/config.yaml`: limits only (env and run mode are irrelevant here)."""
    from agent_orchestrator import cli as ao_cli
    from agent_orchestrator.errors import ConfigError

    try:
        cfg = ao_cli._load_project_config_or_none()
    except ConfigError as exc:
        raise _Fail(f"invalid project config: {exc}") from exc
    settings, _warnings = resolve_result_cache_settings(None, {}, cfg.cache if cfg else None)
    return settings


@dataclass(frozen=True)
class _Setup:
    workspace: str
    store: LocalFsCacheStore
    settings: ResultCacheSettings


def _setup(workspace: str | None, as_json: bool) -> _Setup:
    ws = _resolve_workspace(workspace, as_json)
    settings = _limits()
    store = LocalFsCacheStore.for_workspace(
        ws, max_bytes=settings.max_bytes, ttl_days=settings.ttl_days
    )
    return _Setup(ws, store, settings)


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


def _iso_mtime(mtime: float) -> str:
    """UTC ISO form of a file mtime; a forged out-of-range mtime must not crash a listing."""
    try:
        return datetime.fromtimestamp(mtime, UTC).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return _UNKNOWN_TIME


# ------------------------------------------------------------------ ls
@dataclass(frozen=True)
class _Row:
    info: EntryInfo
    status: str

    @property
    def entry(self) -> CacheEntry | None:
        return self.info.entry

    @property
    def bytes(self) -> int:
        entry = self.info.entry
        return sum(o.size for o in entry.outputs) if entry else self.info.size

    @property
    def created_ts(self) -> float | None:
        entry = self.info.entry
        return entry.created_at.timestamp() if entry else None


def _sort_key(sort: str) -> Callable[[_Row], tuple[float, str]]:
    """Ascending key for the "first = most relevant" order; the key name breaks ties."""
    if sort == SORT_CREATED:
        # an invalid entry has no created_at: it sorts after every valid one
        return lambda r: (-(r.created_ts if r.created_ts is not None else -(10.0**18)), r.info.key)
    if sort == SORT_SIZE:
        return lambda r: (-float(r.bytes), r.info.key)
    return lambda r: (-r.info.mtime, r.info.key)  # SORT_LRU: most recently used first


def _rows(store: LocalFsCacheStore, ttl_days: int | None, now: datetime) -> Iterator[_Row]:
    for info in store.iter_entries():
        if info.anomaly:
            continue  # reported by `stats` / `verify`, not an entry
        if info.entry is None:
            yield _Row(info, _STATUS_INVALID)
        elif ttl_days is not None and is_expired(info.entry.created_at, now, ttl_days):
            yield _Row(info, _STATUS_EXPIRED)
        else:
            yield _Row(info, _STATUS_OK)


def _ls_json(row: _Row) -> JsonDoc:
    entry = row.entry
    return {
        "key": row.info.key,
        "status": row.status,
        "created_at": _iso(entry.created_at) if entry else None,
        "last_used": _iso_mtime(row.info.mtime),
        "outputs": len(entry.outputs) if entry else 0,
        "bytes": row.bytes,
        "source": entry.source.model_dump(mode="json") if entry else None,
        "cost_usd": entry.usage.cost_usd if entry else None,
        "model": entry.usage.model if entry else None,
        "error": row.info.error if not entry else None,
    }


def _ls_line(row: _Row) -> str:
    entry = row.entry
    created = entry.created_at.isoformat(timespec="seconds") if entry else "-"
    source = f"{entry.source.task_id}/{entry.source.run_id}" if entry else "-"
    cost = f"${entry.usage.cost_usd:.4f}" if entry else "-"
    flag = "" if row.status == _STATUS_OK else f"  [{row.status}{_error_suffix(row)}]"
    return clean(
        f"{row.info.key[:KEY_DISPLAY_CHARS]}  {_iso_mtime(row.info.mtime)}  {created}  "
        f"{len(entry.outputs) if entry else 0:>3}  {row.bytes:>10}  {source}  {cost}{flag}"
    )


def _error_suffix(row: _Row) -> str:
    return f": {row.info.error}" if row.info.error else ""


def run_ls(workspace: str | None, as_json: bool, *, limit: int, sort: str, now: datetime) -> int:
    def body() -> int:
        if sort not in LS_SORT_KEYS:
            raise _Fail(
                f"invalid --sort {clean(sort, 32)!r} (expected one of: {', '.join(LS_SORT_KEYS)})",
                EXIT_USAGE,
            )
        if not 1 <= limit <= CLI_MAX_LIMIT:
            raise _Fail(f"--limit must be between 1 and {CLI_MAX_LIMIT}", EXIT_USAGE)
        setup = _setup(workspace, as_json)
        # nsmallest keeps `limit` rows, so a hostile store of millions of entries costs O(limit)
        rows = heapq.nsmallest(
            limit, _rows(setup.store, setup.settings.ttl_days, now), key=_sort_key(sort)
        )
        if as_json:
            _emit(
                {
                    "schema": SCHEMA_LS,
                    "root": setup.store.root,
                    "entries": [_ls_json(r) for r in rows],
                }
            )
        elif not rows:
            typer.echo(f"(result cache is empty: {clean(setup.store.root)})")
        else:
            typer.echo(_LS_HEADER)
            for row in rows:
                typer.echo(_ls_line(row))
        return EXIT_OK

    return _guard(SCHEMA_LS, as_json, body, {})


# ------------------------------------------------------------------ stats
def run_stats(workspace: str | None, as_json: bool, *, now: datetime) -> int:
    def body() -> int:
        setup = _setup(workspace, as_json)
        st = setup.store.stats(now=now)
        doc: JsonDoc = {
            "schema": SCHEMA_STATS,
            "root": st.root,
            "exists": st.exists,
            "entries": st.entries,
            "invalid_entries": st.invalid_entries,
            "expired_entries": st.expired_entries,
            "foreign_version_dirs": list(st.foreign_version_dirs),
            "blobs": st.blobs,
            "orphan_blobs": st.orphan_blobs,
            "tmp_files": st.tmp_files,
            "trash_dirs": st.trash_dirs,
            "anomalies": st.anomalies,
            "bytes": {
                "entries": st.entries_bytes,
                "blobs": st.blobs_bytes,
                "referenced_blobs": st.referenced_blobs_bytes,
                "orphan_blobs": st.orphan_blobs_bytes,
                "total": st.total_bytes,
            },
            "limits": {
                "max_bytes": setup.settings.max_bytes,
                "max_entry_bytes": setup.settings.max_entry_bytes,
                "ttl_days": setup.settings.ttl_days,
            },
            "oldest_created_at": _iso(st.oldest_created_at),
            "newest_created_at": _iso(st.newest_created_at),
        }
        if as_json:
            _emit(doc)
            return EXIT_OK
        if not st.exists:
            typer.echo(f"(result cache is empty: {clean(st.root)})")
            return EXIT_OK
        limits = setup.settings
        ttl = "never" if limits.ttl_days is None else f"{limits.ttl_days} day(s)"
        for line in (
            f"root:        {clean(st.root)}",
            f"entries:     {st.entries} ({st.invalid_entries} invalid, "
            f"{st.expired_entries} expired)",
            f"bytes:       {st.total_bytes} total ({st.entries_bytes} entries, "
            f"{st.blobs_bytes} blobs, {st.orphan_blobs_bytes} orphaned)",
            f"blobs:       {st.blobs} ({st.orphan_blobs} orphaned)",
            f"limits:      max_bytes={limits.max_bytes} "
            f"max_entry_bytes={limits.max_entry_bytes} ttl={ttl}",
            f"oldest:      {_iso(st.oldest_created_at) or '-'}",
            f"newest:      {_iso(st.newest_created_at) or '-'}",
            f"anomalies:   {st.anomalies}, temp files: {st.tmp_files}, trash: {st.trash_dirs}",
            f"foreign versions: {', '.join(clean(n, 64) for n in st.foreign_version_dirs) or '-'}",
        ):
            typer.echo(line)
        return EXIT_OK

    return _guard(SCHEMA_STATS, as_json, body, {})


# ------------------------------------------------------------------ prefix resolution
def _resolve_prefix(store: LocalFsCacheStore, prefix: str) -> str:
    """The ONE entry key `prefix` names (HLD 8.9): `KEY_PREFIX_RE.fullmatch`, then only that shard
    directory is listed (safe because the prefix is hex), keeping `<64 hex>.json` regular files."""
    if not KEY_PREFIX_RE.fullmatch(prefix):
        raise _Fail(
            f"malformed key prefix {clean(prefix, 64)!r} (4 to 64 lowercase hex characters)",
            EXIT_USAGE,
        )
    store.check()
    shard_dir = os.path.join(store.root, ENTRIES_DIR, ENTRIES_VERSION_DIR, prefix[:SHARD_CHARS])
    safeio.check_dir_chain(store.root, shard_dir)  # a symlinked component is an error, not a miss
    matches: list[str] = []
    try:
        listing = os.scandir(shard_dir)
    except (FileNotFoundError, NotADirectoryError):
        listing = None
    if listing is not None:
        with listing:
            for scanned, item in enumerate(listing, start=1):
                if scanned > CLI_MAX_DIR_ITEMS:
                    raise _Fail("too many files in one entry shard to resolve a prefix")
                stem = item.name.removesuffix(ENTRY_SUFFIX)
                if (
                    item.name.endswith(ENTRY_SUFFIX)
                    and SHA256_HEX_RE.fullmatch(stem)
                    and stem.startswith(prefix)
                    and item.is_file(follow_symlinks=False)
                ):
                    matches.append(stem)
                    if len(matches) > CLI_MAX_CANDIDATES:
                        break
    matches.sort()
    shown = clean(prefix, 64)
    if not matches:
        raise _Fail(f"no cache entry matches {shown!r}", fields={"candidates": []})
    if len(matches) > 1:
        listed = matches[:CLI_MAX_CANDIDATES]
        more = " (more matches exist)" if len(matches) > CLI_MAX_CANDIDATES else ""
        raise _Fail(
            f"ambiguous prefix {shown!r}{more}; candidates: {', '.join(listed)}",
            fields={"candidates": listed},
        )
    return matches[0]


# ------------------------------------------------------------------ show
def _blob_view(store: LocalFsCacheStore, sha: str) -> JsonDoc:
    """Presence and on-disk size of one blob; a link or non-regular file is reported absent."""
    present, size = False, None
    if SHA256_HEX_RE.fullmatch(sha):
        directory = os.path.join(store.root, BLOBS_DIR, sha[:SHARD_CHARS])
        safeio.check_dir_chain(store.root, directory)
        try:
            st = os.lstat(os.path.join(directory, sha))
        except FileNotFoundError:
            pass
        else:
            present = stat.S_ISREG(st.st_mode)
            size = st.st_size if present else None
    return {"sha256": sha, "present": present, "size_on_disk": size}


def run_show(workspace: str | None, as_json: bool, *, prefix: str, now: datetime) -> int:
    def body() -> int:
        setup = _setup(workspace, as_json)
        store = setup.store
        key = _resolve_prefix(store, prefix)
        try:
            entry = store.get_entry(key)
        except CacheIntegrityError as exc:
            raise _Fail(f"entry {key[:KEY_DISPLAY_CHARS]} is invalid ({exc.reason})") from exc
        if entry is None:  # removed since the listing
            raise _Fail(f"no cache entry matches {key[:KEY_DISPLAY_CHARS]!r}")
        blobs = [_blob_view(store, o.sha256) for o in entry.outputs]
        entry_path = os.path.join(
            store.root, ENTRIES_DIR, ENTRIES_VERSION_DIR, key[:SHARD_CHARS], key + ENTRY_SUFFIX
        )
        last_used = _iso_mtime(os.lstat(entry_path).st_mtime)
        ttl = setup.settings.ttl_days
        expired = ttl is not None and is_expired(entry.created_at, now, ttl)
        if as_json:
            _emit(
                {
                    "schema": SCHEMA_SHOW,
                    "entry": entry.model_dump(mode="json", by_alias=True),
                    "last_used": last_used,
                    "expired": expired,
                    "blobs": blobs,
                }
            )
            return EXIT_OK
        for line in _show_lines(entry, last_used, expired, blobs):
            typer.echo(clean(line))
        return EXIT_OK

    return _guard(SCHEMA_SHOW, as_json, body, {})


def _show_lines(
    entry: CacheEntry, last_used: str, expired: bool, blobs: list[JsonDoc]
) -> list[str]:
    src, use = entry.source, entry.usage
    lines = [
        f"key:        {entry.key}",
        f"created:    {entry.created_at.isoformat(timespec='seconds')}"
        f"{'  (EXPIRED)' if expired else ''}",
        f"last used:  {last_used}",
        f"source:     workflow={src.workflow_id} task={src.task_id} run={src.run_id} "
        f"agent={src.agent} ao={src.ao_version}",
        f"usage:      cost=${use.cost_usd:.4f} in={use.input_tokens} out={use.output_tokens} "
        f"duration={use.duration_seconds:.1f}s attempts={use.attempts} model={use.model or '-'}",
        "outputs:",
    ]
    for out, blob in zip(entry.outputs, blobs, strict=True):
        state = "present" if blob["present"] else "MISSING"
        blob_ref = out.sha256[:KEY_DISPLAY_CHARS]
        lines.append(
            f"  {out.path}  {out.size} bytes  mode={out.mode:04o}  blob {blob_ref} {state}"
        )
    lines.append("components:")
    lines += [
        f"  {name}: {digest}" for name, digest in sorted(entry.key_summary.components.items())
    ]
    if not entry.key_summary.components:
        lines.append("  (none recorded)")
    return lines


# ------------------------------------------------------------------ rm
def run_rm(workspace: str | None, as_json: bool, *, prefix: str) -> int:
    def body() -> int:
        setup = _setup(workspace, as_json)
        key = _resolve_prefix(setup.store, prefix)
        if not setup.store.delete_entry(key):  # raced with another remover
            raise _Fail(f"no cache entry matches {key[:KEY_DISPLAY_CHARS]!r}")
        if as_json:
            _emit({"schema": SCHEMA_RM, "removed": [key], "error": None})
        else:
            typer.echo(f"removed {key}")
        return EXIT_OK

    return _guard(SCHEMA_RM, as_json, body, {"removed": []})


# ------------------------------------------------------------------ prune
def _bounded(value: int | None, *, name: str, upper: int) -> None:
    if value is not None and not 0 <= value <= upper:
        raise _Fail(f"{name} must be between 0 and {upper}", EXIT_USAGE)


def run_prune(
    workspace: str | None,
    as_json: bool,
    *,
    max_bytes: int | None,
    older_than: int | None,
    dry_run: bool,
    now: datetime,
) -> int:
    def body() -> int:
        _bounded(max_bytes, name="--max-bytes", upper=MAX_CONFIG_BYTES)
        _bounded(older_than, name="--older-than", upper=MAX_TTL_DAYS)
        setup = _setup(workspace, as_json)
        # Directories to sweep for stale restore staging files are read BEFORE the prune: it may
        # evict the very entries that name them.
        dirs, dirs_cut = _output_dirs(setup.store)
        report = setup.store.prune(
            now=now,
            max_bytes=max_bytes if max_bytes is not None else setup.settings.max_bytes,
            ttl_days=older_than if older_than is not None else setup.settings.ttl_days,
            dry_run=dry_run,
        )
        sweep = restore_sweep.sweep_restore_leftovers(
            setup.workspace, dirs, now=now, dry_run=dry_run
        )
        if as_json:
            _emit(_prune_doc(report, sweep, truncated=dirs_cut))
        else:
            for line in _prune_lines(report, sweep):
                typer.echo(line)
        return EXIT_OK

    return _guard(SCHEMA_PRUNE, as_json, body, {})


def _output_dirs(store: LocalFsCacheStore) -> tuple[list[str], bool]:
    """Parent directories of every output of every VALID entry (see `restore_sweep`)."""
    entries = (info.entry for info in store.iter_entries() if info.entry is not None)
    return restore_sweep.collect_output_dirs(entries)


def _prune_doc(
    report: PruneReport, sweep: restore_sweep.RestoreSweepReport, *, truncated: bool
) -> JsonDoc:
    return {
        "schema": SCHEMA_PRUNE,
        "dry_run": report.dry_run,
        "removed_entries": {r: report.removed_entries.get(r, 0) for r in _PRUNE_REASONS},
        "removed_blobs": report.removed_blobs,
        "removed_tmp": report.removed_tmp,
        "removed_trash": report.removed_trash,
        "removed_restore_tmp": sweep.removed,
        "kept_restore_backups": sweep.kept_backups,
        "restore_sweep_truncated": truncated or sweep.truncated,
        "bytes_before": report.bytes_before,
        "bytes_after": report.bytes_after,
    }


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def _prune_lines(report: PruneReport, sweep: restore_sweep.RestoreSweepReport) -> list[str]:
    verb = "would remove" if report.dry_run else "removed"
    by_reason = ", ".join(f"{report.removed_entries.get(r, 0)} {r}" for r in _PRUNE_REASONS)
    lines = [
        f"{verb} {_plural(report.total_removed, 'entry', 'entries')} ({by_reason}), "
        f"{report.removed_blobs} blob(s), {report.removed_tmp} temp file(s), "
        f"{report.removed_trash} trash dir(s)",
        f"bytes: {report.bytes_before} before, {report.bytes_after} after"
        f"{' (dry run: nothing deleted)' if report.dry_run else ''}",
    ]
    if sweep.removed or sweep.kept_backups:
        kept = f"; kept {sweep.kept_backups} sole-copy backup(s)" if sweep.kept_backups else ""
        lines.append(
            f"{verb} {sweep.removed} stale restore temp file(s) in output directories{kept}"
        )
    return lines


# ------------------------------------------------------------------ clear
def _stdin_is_tty() -> bool:
    """Module-level seam: tests replace it (CliRunner's stdin is never a TTY)."""
    return sys.stdin.isatty()


def run_clear(workspace: str | None, as_json: bool, *, yes: bool) -> int:
    zero: JsonDoc = {"removed_entries": 0, "removed_blobs": 0, "bytes_freed": 0}

    def body() -> int:
        setup = _setup(workspace, as_json)
        if not yes:
            # `--json` must print exactly one document, so it never prompts.
            if as_json or not _stdin_is_tty():
                raise _Fail("refusing to clear without --yes")
            question = f"Delete the whole result cache at {clean(setup.store.root)}?"
            if not typer.confirm(question, err=True):
                raise _Fail("aborted: nothing was cleared")
        report = setup.store.clear()
        if as_json:
            _emit(
                {
                    "schema": SCHEMA_CLEAR,
                    "removed_entries": report.removed_entries,
                    "removed_blobs": report.removed_blobs,
                    "bytes_freed": report.bytes_freed,
                    "error": None,
                }
            )
        else:
            typer.echo(
                f"cleared {_plural(report.removed_entries, 'entry', 'entries')}, "
                f"{report.removed_blobs} blob(s), {report.bytes_freed} bytes freed"
            )
        return EXIT_OK

    return _guard(SCHEMA_CLEAR, as_json, body, zero)


# ------------------------------------------------------------------ verify
def _problem_json(problem: VerifyProblem) -> JsonDoc:
    return {
        "kind": problem.kind,
        "key": problem.key,
        "blob": problem.blob,
        "detail": problem.detail,
    }


def run_verify(workspace: str | None, as_json: bool) -> int:
    """READ-ONLY: deletes and modifies nothing (`--repair` is deferred, non-MVP 16)."""

    def body() -> int:
        setup = _setup(workspace, as_json)
        report = setup.store.verify()
        if as_json:
            _emit(
                {
                    "schema": SCHEMA_VERIFY,
                    "ok": report.ok,
                    "entries_checked": report.entries_checked,
                    "blobs_checked": report.blobs_checked,
                    "problems": [_problem_json(p) for p in report.problems],
                }
            )
        else:
            state = "OK" if report.ok else "PROBLEMS FOUND"
            typer.echo(
                f"verify: {state} ({report.entries_checked} entries, "
                f"{report.blobs_checked} blobs checked)"
            )
            for problem in report.problems:
                target = (problem.key or problem.blob or "")[: KEY_DISPLAY_CHARS * 2]
                detail = f" {problem.detail}" if problem.detail else ""
                typer.echo(clean(f"  {problem.kind}: {target}{detail}"))
        return EXIT_OK if report.ok else EXIT_ERROR

    return _guard(SCHEMA_VERIFY, as_json, body, {"ok": False, "problems": []})
