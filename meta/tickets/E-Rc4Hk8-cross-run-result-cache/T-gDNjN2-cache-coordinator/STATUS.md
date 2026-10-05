# STATUS

- ID: `T-gDNjN2-cache-coordinator`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (Dev A)

## This update
Implemented in commit `d14f07d` (branch `worktree-agent-a18ce2c08e42a3a5a`):
`src/agent_orchestrator/cache/coordinator.py` (`ResultCache`) and
`src/agent_orchestrator/cache/records.py` (the four builders); tests in
`tests/cache/test_coordinator.py` (97) and `tests/cache/test_records.py` (10). No existing module
was touched (so no full-suite run was needed); `engine.py` is untouched (T-XpF1pF). The cache stays
OFF by default.

## Acceptance criteria
| AC | Result | Evidence |
|----|--------|----------|
| 1 U-CO1 | PASS | not opted in (also explicit opt-out, injected `true`, workflow default) -> `LookupOutcome(False, None, None)`; `store.calls == []`; heads and probe untouched; the only log record is the DEBUG `cache.skip` (`not_opted_in`) |
| 2 U-CO2 | PASS | `no_outputs` and `command_not_cacheable` (detail `curl`): record `ineligible` with reason/detail, INFO `cache.skip` with exactly `{event, phase=lookup, reason[, reason_detail]}`, no pending, no store call |
| 3 U-CO3 | PASS | `not_found` miss + pending (lookup-time HEADs and snapshot); `cache.miss` has exactly `{event, reason, key, components}` and `components` holds the 11 GV-1 field names with 12-hex digests |
| 4 U-CO4 | PASS | miss -> store -> delete output -> hit: output restored, `touch_entry` once, `cache.hit` has the 8 HLD 15 fields (`outputs [{path, sha256}]`, `bytes`, `saved_cost_usd`, `source_run_id`, `source_created_at`, `source_ao_version`); record `saved_*` equals `entry.usage` and `saved_tokens == 1000 + 200` |
| 5 U-CO5 | PASS | 31-day-old entry (ttl 30) -> evicted, `cache.evict` (`expired`), storable `expired` miss (pending set); exactly 30 days is a hit; `ttl_days=None` and a future `created_at` never expire |
| 6 U-CO6 | PASS | `corrupt_entry` and `key_mismatch` from `get_entry` -> `delete_entry` once, WARNING `cache.corrupt`, `cache.evict`, storable miss |
| 7 U-CO7 | PASS | real restore over a tampered blob -> entry and blob both gone, miss `blob_corrupt`, `cache.corrupt` carries `blob`, **no pending and no snapshot call** (also `blob_missing` and `manifest_mismatch` on restore: evicted, not storable) |
| 8 U-CO8 | PASS | `restore_failed` and `sensitive_output` -> miss with the detail, no pending, entry kept, `delete_entry`/`delete_blob` never called, no evict/corrupt event |
| 9 U-CO9 | PASS | `store.check()` raising `CacheLayoutError` (and `SafeIOError`): three lookups -> three `store_unavailable` misses, no pending, exactly one WARNING, `get_entry` never called |
| 10 U-CO10 | PASS | shadow: `would_hit` record (`hit False`), `cache.would_hit` (exactly 4 fields), `restore_outputs` spy never called, nothing written to the workspace, pending set, snapshot once; a missing blob -> evict + storable `blob_missing` miss; a would-hit pending can store |
| 11 U-CO12 | PASS | `repo_head_moved` (also with `include_repo_heads=False`), `key_changed_during_run` (the skip event carries the changed `components`), `repo_worktree_changed`, `repo_head_unavailable` (failing settle HEAD read); plus the preseed case (an output that is also an input), probe failure at settle, key-recompute refusal, `output_missing`, `entry_too_large` (restore cap and `CacheTooLargeError` from `put_entry`) |
| 12 U-CO13 | PASS | entry `usage` from `ts.cumulative_*` set directly on a `TaskRunState`; clamped (cost, tokens, attempts, duration); `source.ao_version == agent_orchestrator.__version__`; `source.cli_version` equals the key's (and `None` for the `fake` executor); `cache.store` has exactly `{event, key, outputs, bytes}` |
| 13 U-CO14 | PASS | store `OSError` (and `CacheError`/`ValueError`/`TypeError`/`RecursionError`) -> `store_error` miss + ERROR log, cache stays enabled; `RuntimeError` -> `cache.disabled` (`error_type`), later lookups return nothing and touch no store, `store_success` -> `StoreResult(False, cache_disabled)`; `strict=True` re-raises (lookup and store), and still wraps expected errors; store-side `OSError` -> `StoreResult(False, store_error)`; capture's `StoreSkip(store_error)` logs at WARNING |
| 14 U-CO15 | PASS | `from_settings` with `os.mkdir/makedirs/open/write/replace/rename/unlink/remove/rmdir`, `shutil.rmtree` and `builtins.open` all patched to raise: succeeds, root resolved (`ws/sub/..` -> `ws`), `.orchestrator` not created; no attribute of `ResultCache` is named `open`; limits reach the store (`ttl_days=None` too) |
| 15 U-CO16 | PASS | `PruneReport(deferred=True)` -> WARNING `cache.evict` (`reason=deferred`, exactly `{event, reason}`); `OSError`/`CacheError`/`CacheLayoutError` from `maybe_enforce_limits` swallowed with the entry stored and the cache still enabled; an LRU report logs INFO `entries=3` |
| 16 U-CO17 | PASS | a `MagicMock(spec=ArtifactStore)` -> ineligible `artifact_store_unsupported`, no store call |
| 17 U-CO18 | PASS | hit: probe `call_count == 0`; storable miss and would_hit: exactly 1; non-storable miss: 0; probe failure (miss, would_hit, evicting miss): no pending, record `stored False`, `store_reason repo_worktree_probe_failed`, outcome unchanged (`miss`/`would_hit`, never `ineligible`), INFO `cache.skip` with `phase=store` |
| 18 U-CO19 | PASS | `get_entry` raising `CacheUnsafePathError` -> miss `unsafe_path` with a key, no pending, `delete_entry`/`delete_blob` never called (spy), WARNING `cache.corrupt`, cache not disabled; the same from `has_blob`, `read_blob` and a failing eviction `delete_entry`; at settle an unsafe `put_blob` is a `store_error` skip |
| 19 U-CO20 | PASS | `.git` in a parent of the workspace -> exactly one warning naming both paths; none for a plain workspace or a `.git` inside it (skips if the temp dir itself lies inside a repository) |
| 20 U-RC1..RC3 | PASS | builders set `dispatch_cycle`, `at`, `mode`, `mode_source`, `store_reason`; `reason`/`detail`/`store_reason` clipped to 64/256/64; hit record from an entry at every bound (cost 1e6, tokens 1e12, seconds 1e8, control + bidi characters) validates and round-trips through JSON; builders never mutate the entry |
| 21 hygiene | PASS | see Evidence |

## Evidence
- `.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache/test_coordinator.py tests/cache/test_records.py --cov=agent_orchestrator.cache.coordinator --cov=agent_orchestrator.cache.records --cov-report=term` -> 107 passed; coverage of `coordinator.py` **100%** (268 statements), `records.py` **100%** (target: >= 90%).
- `.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache tests/test_spawn_provenance.py` -> **1045 passed**, 0 failed.
- `.venv/bin/ruff check src tests` -> All checks passed. `.venv/bin/ruff format --check src tests` -> clean except the generated `src/agent_orchestrator/_build_info.py` (pre-existing).
- `.venv/bin/mypy src tests/cache` -> only the 4 pre-existing `_version.py` errors.
- AST guard (`tests/cache/test_ast_guard.py`) passes over the two new modules; `grep -n '\.origin' src/agent_orchestrator/cache/*.py` -> no match (`test_spawn_provenance.py` passes).
- Full suite not run: no existing module was edited (new files only).

## Deviations from the HLD code block (reason)
1. **`_serve` split and uniform D33 handling.** The HLD handles `CacheUnsafePathError` only around `get_entry`. D33 says every store operation can raise it and the coordinator turns it into an `unsafe_path` miss that is never evicted, so `_lookup` wraps everything after the key build (`has_blob`, `read_blob` via restore, an eviction's `delete_entry` / `delete_blob`) in one handler. Without it those would fall to the boundary as a `store_error` (still not evicted, but the wrong reason).
2. **`CacheTooLargeError` from `put_entry` is a skip `entry_too_large`** (an existing store-skip reason), not `store_error`: the 1 MiB entry-file bound is the same condition as the blob bound.
3. **A coordinator built with a mode other than `on`/`shadow` is permanently disabled** (returns nothing, `StoreResult(False, cache_disabled)`), so it can never restore or store for `off`. The HLD never builds one; this is a fail-closed default.
4. **`cli_versions` is a `Callable[[str], str]`** (the HLD only names the parameter): it feeds `KeyDeps(cli_version_of=...)`; the default is `CliVersionReader().version`. `environ` defaults to `{}` in the constructor (`from_settings` passes `os.environ`).
5. **Collaborator types are two small `Protocol`s** (`HeadReader`, `Worktree`) so the fakes satisfy mypy.
6. **`from_settings` passes `settings.ttl_days` to `LocalFsCacheStore.for_workspace` through `typing.cast("int", ...)`**: the store's annotation says `int` but the store itself handles `None` ("never expire") at every use. Follow-up for the store owner: widen the annotation to `int | None` (not done here: out of this task's file scope).
7. **`_miss` has an optional `detail`** (a restore miss forwards `RestoreMiss.detail`, e.g. `restore_failed` -> exception class name) and `_evict` an optional `blob` (carried on `cache.corrupt`, HLD 15 `blob?`).
8. **Duration** (`entry.usage.duration_seconds`) is computed from the ISO strings `ts.started_at` / `ts.ended_at`; a missing end uses the settle clock `now`, and anything unparsable (or a naive/aware mix) gives 0.0. The HLD only says `clamped(...)`.
9. **`_control_paths_abs` uses a local `_try_resolve`** (the HLD names a `try_resolve` helper that does not exist in the tree).

## Risks / Blockers
- No blockers. `lookup` / `store_success` take the engine clock (`req.now`, `now`); no `time`/`datetime.now`/`random` is called in the module (determinism).
- The `except Exception` boundary disables the cache for the rest of the run; an unexpected bug therefore costs only the cache, never the run (M-16), at the price of a lost store when `maybe_enforce_limits` raises something outside `(CacheError, OSError, SafeIOError)` after a successful `put_entry`.
- A hit's `touch_entry` is best effort. The default `WorktreeProbe` / `RepoHeadReader` run `git` subprocesses once per lookup (HEAD), once per storable outcome (snapshot) and twice at settle (HEAD, snapshot); the cost under `max_parallel` is for T-XpF1pF to measure.

## Next actions
1. T-XpF1pF (engine seams) and T-o95l1M (CLI construction: `ResultCache.from_settings`, banner, `rc.warnings`) can start; T-JCOAsq Part 1 any time.
2. Optional follow-up (store owner): widen `LocalFsCacheStore(ttl_days=...)` to `int | None` and drop the `cast`.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD §23.5).
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done (commit `d14f07d`).
  Every acceptance criterion passes; deviations 1-9 above are small and documented; frozen names
  unchanged. Epic `EPIC.md` / `STATUS.md` rollup updated to match.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: G1b remediation touched this task's code (rev S-3 (shared clip_text / try_resolve) and rev S-4 (ttl_days int or None, cast removed)) in commit `6ba90ba`; findings and regression tests are listed in `T-fXWbqg-cache-review-gates/STATUS.md` (G1b remediation). Task state stays Done.
