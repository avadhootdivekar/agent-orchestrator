# HANDOFF: T-HjxNQ0-cache-store-maintenance

- Task: `T-HjxNQ0-cache-store-maintenance`
- State: `Done (handoff available)`
- From: `developer` (Dev C)
- To: T-6tRKml, T-gDNjN2

## What was delivered
- `agent_orchestrator.cache.store.LocalFsCacheStore(CacheStore, CacheAdmin)` (commit `6753c71`, branch
  `worktree-agent-a18ce2c08e42a3a5a`): `iter_entries`, `stats`, `prune`, `clear`, read-only `verify`
  and the bounded `maybe_enforce_limits` (the T-U7ckfd placeholder is gone).

## Frozen names / contracts
- Signatures of `CacheAdmin` (HLD 8.4.3) and the report dataclasses of T-FJH6LI; the `verify` problem
  kinds and the `prune` reasons (`invalid`, `expired`, `lru`).
- **Anomalies in `iter_entries`:** `EntryInfo(key=<name relative to entries/v1, e.g. "ab/junk.txt" or
  "ef" for a shard>, size=0, mtime=0.0, error=<"symlink" | "unexpected_file">, anomaly=True)`. Never
  parsed, never deleted. An invalid entry has `entry=None, error=<corrupt_entry | key_mismatch>`.
- **What counts as an anomaly:** a symlinked shard or file; a shard that is not a directory; a
  directory named like an entry; a name that is not `<64 hex>.json`; a hex-named file in the wrong
  shard (its `key[:2]` differs from the directory).
- **Accounting:** `stats.total_bytes = entries_bytes + blobs_bytes` (v1 entry files + every blob
  file); `orphan_blobs` use the same any-version hex-token mark as `prune`; `prune.bytes_before` and
  `bytes_after` use that same notion (entries + all blobs) and `bytes_after` subtracts removed
  entry files and swept blobs. LRU "total" is, as in the HLD pseudocode, live entry files plus the
  distinct blobs they reference.
- `CacheStats.max_entry_bytes` stays `None` (the store does not know it): T-6tRKml fills the 13.4
  `limits.max_entry_bytes` from settings.
- `maybe_enforce_limits` needs a store built with `max_bytes`; `_approx_total` is `None` until the
  first scan, then follows `put_entry` / `put_blob`, and is reset by `prune` / `clear`.

## Deviations from the HLD code block (reason)
1. `clear()` wraps a failed `shutil.rmtree` into `CacheError(store_error)` and also verifies the
   trash directory itself is gone: the HLD's post-check only looked at `entries/blobs/tmp` under the
   root, which the move had already emptied, so a failed removal would not have raised the
   `CacheError` that AC-7 requires. The same helper (`_rmtree_checked`) removes stale trash.
2. `iter_entries` treats a DIRECTORY named like an entry, a non-hex name and a wrong-shard file as
   anomalies (the HLD lists symlinks and non-`.json`/non-hex names): `unlink` of a directory would
   abort a prune, and `get_entry(key)` reads the key's own shard, not the shard the file sits in.
3. `prune`, `stats` and `verify` check `entries/v1`, `blobs/` and `tmp/` up front (`_check_all_dirs`),
   so an unsafe component aborts the pass with `CacheUnsafePathError` before anything is removed.
4. `stats` / `verify` on a missing root return an empty report instead of raising; `prune` and
   `clear` on a missing root are empty no-ops.
5. `verify` re-hashes blobs through a private `_Sha256Sink` (the HLD allows only `types`, `safeio`
   and `constants` as store dependencies, so `restore.HashingWriter` is not imported).
6. Tests patch `store.INLINE_PRUNE_MAX_ENTRIES` / `store.INLINE_PRUNE_MAX_ENTRY_FILE_BYTES` (the
   names are imported by value into the store module).
7. `tests/cache/test_store_core.py`: the T-U7ckfd class-shape test no longer asserts the absence of
   `CacheAdmin` (renamed `test_class_derives_from_the_store_abc_and_is_complete`) and the
   placeholder test is renamed; both belong to the base this task adds.

## Verification the receiver should run
- `pytest -q tests/cache/test_store_maintenance.py tests/cache/test_store_race.py`

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: `CacheAdmin` base added
  here; byte bound; read-only `verify`. State `Draft` mirrors `TASK.md` and `STATUS.md`.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done; handoff available (commit `6753c71`). Deviations 1-7 above are small and behaviour-strengthening.
