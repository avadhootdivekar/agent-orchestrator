# TASK: T-FJH6LI-cache-contracts

## Metadata
- Task ID: `T-FJH6LI-cache-contracts`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev A)
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 3)
- Status: `Draft`
- Estimate: `16 focus hours (2 days)` · Sprint 1 · first task of the core set

## Requirements Mapping
- Requirement IDs: FR-7, FR-8, NFR-4, NFR-6, NFR-7, NFR-10 (M-2, M-4, M-6, M-9, M-13, M-14, M-15), NFR-11
- HLD: §8.0, §8.1.5, §8.2.1, §8.2.2, §8.4.2, §8.4.3 (ABCs only), §8.6.1, §14.1, §15
- ADR-0019: D17, D18, D21, D28, D29, D31, D33

## Description
Create the interface-first foundation of `agent_orchestrator.cache`. Every other cache task
develops against these names. **This task depends on nothing**: `types.py` references the
`ResultCacheRecord` model (which T-28J9oR adds) **only under `TYPE_CHECKING`**. Land it as three
commits, with the numbering used everywhere in the epic:

1. **Commit 1 (≤ 3 h): `cache/__init__.py` + `cache/constants.py`.**
   - `__init__.py`: a docstring only, no imports. This task owns the file.
   - `constants.py`: exactly HLD §8.1.5, which includes:
     - the modes `MODE_OFF`, `MODE_ON`, `MODE_SHADOW` (there is **no** `MODE_REFRESH`);
     - **`DEFAULT_TASK_CACHE_POLICY = False`**, the single author-policy flip point;
     - `AGENT_KEY_FIELDS` and `AGENT_NON_KEY_FIELDS` (used by keys and eligibility);
     - `INLINE_PRUNE_MAX_ENTRIES` and `INLINE_PRUNE_MAX_ENTRY_FILE_BYTES`;
     - `GIT_OPTIONAL_LOCKS_VAR` and `GIT_OPTIONAL_LOCKS_OFF`;
     - a `REASON_*` constant for every reason string in HLD §8.3.3 and §8.6.4, including
       `unknown_agent_field` and `unsafe_path`;
     - an `EVENT_*` constant for every `cache.*` event in HLD §15: `cache.hit`,
       `cache.would_hit`, `cache.miss`, `cache.skip`, `cache.store`, `cache.evict`,
       `cache.corrupt`, `cache.disabled`.
   - T-28J9oR can start as soon as commit 1 is on the branch.
2. **Commit 2 (≤ 5 h): `cache/safeio.py`** (HLD §8.2.2, copy-ready):
   - the flags `O_SAFE_READ` and `O_SAFE_CREATE`;
   - the errors `SafeIOError`, `NotRegularFileError`, `TooLargeError` and `UnsafePathError`;
   - `open_regular_read`, `read_bounded`, `create_exclusive`, `check_dir_chain`,
     `ensure_dir_chain`, `check_root_dir`, `is_sensitive_rel_path`, `posix_rel` and
     `strip_control_chars`.
3. **Commit 3: `cache/types.py`, plus test fakes, the store contract suite, the hostile corpus
   and the AST guard.** Every other cache task (T-8tr1H4 included) needs this commit.
   - `types.py` starts with `from __future__ import annotations` and imports `ResultCacheRecord`,
     `TaskRunState`, `TaskSpec`, `WorkflowSpec`, `AgentSpec` and `ArtifactStore` **under
     `TYPE_CHECKING` only** (HLD §8.6.1).
   - **`canonical_json(obj) -> str`**: the one canonical serializer (`json.dumps` with sorted
     keys, separators `(",", ":")`, `ensure_ascii=True`, `allow_nan=False`).
   - **Entry models** exactly as HLD §8.4.2 (copy-ready): the `PathStr`, `TextStr` and `HexStr`
     aliases; `OutputRecord`, `EntryUsage`, `EntrySource`, `KeySummary`, `CacheEntry` (with
     `to_canonical_bytes()`).
   - **`parse_entry_bytes(raw, expected_key)`**, the TOTAL parse boundary (D28).
   - **Dataclasses**: `Digest`, `BlobRef`, `EntryInfo`, `CacheStats`, `PruneReport` (with
     `deferred` and a `total_removed` property), `ClearReport`, `VerifyProblem`, `VerifyReport`
     (no `repaired` field: `verify --repair` is deferred), `KeyRequest`, `KeyDeps`, `CacheKey`
     (with `components` and `cli_version`), `LookupRequest`, `PendingStore`, `LookupOutcome`,
     `StoreResult`.
   - **`ResultCacheHook`**, a `typing.Protocol` with `lookup` and `store_success`.
   - **ABCs, both PROVISIONAL** (HLD §8.4.3, copy-ready):
     - `CacheStore`: `check`, `get_entry`, `put_entry`, `touch_entry`, `delete_entry`,
       `has_blob`, `put_blob`, `read_blob`, `delete_blob`, `maybe_enforce_limits`;
     - `CacheAdmin`: `iter_entries`, `stats`, `prune`, `clear`, `verify()` (no `repair`
       parameter).
   - **Errors** (HLD §14.1): `CacheError`, `CacheIntegrityError`, **`CacheUnsafePathError`**,
     `CacheBlobMissingError`, `CacheTooLargeError`, `CacheLayoutError`, `UncacheableError`,
     `StoreSkip`, `RestoreMiss(reason, evict, blob, detail)`.
   - **`tests/cache/fakes.py`**: `InMemoryCacheStore(CacheStore, CacheAdmin)` (dict-backed, with
     the same key and sha regex checks as the real store), `FakeRepoHeadReader`,
     `FakeWorktreeProbe`, `fake_cli_version_of` and a fake VCS runner, each configurable to
     return a value or raise.
   - **`tests/cache/store_contract.py`**: a parametrizable contract suite for the `CacheStore`
     half only. T-U7ckfd reruns it against `LocalFsCacheStore`; `CacheAdmin` behaviour is tested
     by T-HjxNQ0.
   - **`tests/fixtures/result_cache/corpus/`**: the hostile-entry corpus (AC-5).
   - **`tests/cache/test_ast_guard.py`** (U-AST), with a negative self-test.

**Layering rule (HLD §8.0).** `constants` imports only `re`; `safeio` only the stdlib and
`constants`; `types` imports `constants`, `safeio`, `pydantic` and (type-only) `models`. None of
them imports `engine`, `runstate`, `cli` or `ui`.

## File scope (exclusive)
- `src/agent_orchestrator/cache/__init__.py`, `constants.py`, `safeio.py`, `types.py` (new)
- `tests/cache/__init__.py`, `fakes.py`, `store_contract.py`, `test_types.py`, `test_safeio.py`,
  `test_ast_guard.py` (new)
- `tests/fixtures/result_cache/corpus/**` (new; T-JCOAsq reads it)

## Inputs / Outputs
- **Inputs:** HLD §8.0–§8.6 contracts, §13.3 / §13.4 shapes, §14.1, §15.
- **Outputs:** constants, safe-I/O helpers, contracts, ABCs, errors, fakes, the contract suite
  and the hostile corpus. The names are frozen in `HANDOFF.md`.

## Acceptance Criteria
1. **Constants.**
   - `constants.py` defines every name in HLD §8.1.5, and no `MODE_REFRESH`.
   - `DEFAULT_TASK_CACHE_POLICY is False`.
   - A test compares the set of `REASON_*` values with a fixture list copied from the HLD
     reason tables (§8.3.3, the §8.6.4 miss and store-skip lists, §15 evict reasons). A reason
     missing on either side fails.
   - `EVENT_*` values equal the eight `cache.*` event names of §15.
   - The module imports only `re`.
2. **Regexes.**
   - `SHA256_HEX_RE.fullmatch` accepts exactly 64 lowercase hex characters. It rejects uppercase,
     63 or 65 characters, `../x`, NUL, and a valid sha followed by `\n`.
   - `KEY_PREFIX_RE.fullmatch` accepts 4–64 lowercase hex characters.
   - `HEX64_TOKEN_RE_BYTES.finditer` finds every 64-hex token in raw bytes, including in an
     unparseable file.
3. **`safeio` (U-IO1..IO7).**
   - `open_regular_read` raises `NotRegularFileError` for a final-component symlink, a directory
     and a FIFO; the FIFO case returns within 5 s (thread + join timeout); `FileNotFoundError`
     passes through.
   - `read_bounded` raises `TooLargeError` at `max_bytes + 1`.
   - `create_exclusive` fails if the path exists and creates files with mode `0o600`.
   - `check_dir_chain` and `ensure_dir_chain` raise `UnsafePathError` on a symlinked component.
   - `check_root_dir` rejects a symlinked root, a root owned by another uid (simulated with a
     monkeypatched `os.geteuid`) and a group-writable root it does not own; it chmods a
     group-writable root it **does** own to `0o700`.
   - `is_sensitive_rel_path` is table-tested against every component and basename in HLD D29,
     plus negatives (`docs/claude.md`, `out/.gitkeep`).
   - `strip_control_chars` removes C0, C1 and DEL; `posix_rel` is table-tested.
   - FIFO tests carry `@pytest.mark.skipif(not hasattr(os, "mkfifo"), ...)`; symlink and
     ownership tests carry `@pytest.mark.skipif(sys.platform == "win32", ...)`.
4. **Entry models (U-T1..T5).**
   - The HLD §8.4.2 example (with valid 64-hex shas) round-trips.
   - Rejected: a naive `created_at`; `cost_usd = inf` or `nan`; `mode > 0o777`; `size < 0`;
     empty `outputs`; a non-hex `key`; a 300-character `run_id`; a 5000-character path in
     `KeySummary.inputs`.
   - An unknown extra field is ignored; `model_dump(by_alias=True)` emits `"schema"`.
   - `to_canonical_bytes()` is byte-identical across two constructions with different field
     orders.
5. **`parse_entry_bytes` is total.** For each corpus file it raises `CacheIntegrityError` and
   nothing else: 100 000 nested `[`; invalid UTF-8; `[]`; `{"schema": "x"}`; a 1 MiB string
   field; a key mismatch. Reasons: `corrupt_entry`, except `key_mismatch` for the key-mismatch
   file.
6. **`canonical_json`** equals the `json.dumps` call above and raises `ValueError` on NaN.
7. **ABCs and protocol.**
   - `CacheStore` and `CacheAdmin` raise `TypeError` when instantiated; their abstract-method
     sets equal the lists above; `CacheAdmin.verify` takes no `repair` parameter.
   - `ResultCacheHook` is a `Protocol` with exactly `lookup` and `store_success`.
8. **No dependency on T-28J9oR.** On a tree where `models.py` has no `ResultCacheRecord`,
   `python -c "import agent_orchestrator.cache.types"` succeeds (test: import in a subprocess
   with `models.ResultCacheRecord` deleted via a monkeypatched module, or simply run on the
   branch before T-28J9oR lands).
9. **Errors.** Every error exposes `.reason`; `RestoreMiss` also exposes `.evict`, `.blob` and
   `.detail`; `UncacheableError` and `StoreSkip` expose `.detail`; `CacheUnsafePathError` is a
   `CacheError` and **not** a `CacheIntegrityError`.
10. **Fakes.** `InMemoryCacheStore` passes `store_contract.py` (put/get entry, put/read blob,
    dedupe, touch, delete, `has_blob`, `check`, invalid key → `ValueError`). The fakes can each
    be set to return a value or raise.
11. **U-AST.** `test_ast_guard.py` walks every `.py` file in `src/agent_orchestrator/cache/` and
    fails on `pickle`/`marshal`/`shelve` imports, `eval`/`exec` calls, any `shell=True`, and any
    bare `open(` or `os.open(` outside `safeio.py`. A negative self-test feeds it a synthetic
    module and expects each violation.
12. **Hygiene.** `ruff check`, `ruff format --check` and `mypy src` are clean; every line is
    ≤ 100 columns. `pytest -q` has no new failures against the baseline (5041 passed / 8 skipped
    / 2 known bench failures). `tests/conftest.py` is not edited.

## Test requirements
- `tests/cache/test_types.py`: AC-4..AC-9.
- `tests/cache/test_safeio.py`: AC-3.
- `tests/cache/test_ast_guard.py`: AC-11.
- `tests/cache/store_contract.py`, run against the fake: AC-10.
- Each new test module controls `AO_CACHE` itself (`monkeypatch.delenv("AO_CACHE", raising=False)`).
- Permission errors are simulated with monkeypatched `os.open`, never with `chmod` (CI may run as
  root).

## Risks
- **Contract churn breaks later tasks.** Mitigation: freeze the names in `HANDOFF.md`; any change
  is noted in every consumer's `STATUS.md` and in HLD §14.1.
- **FIFO tests can hang the suite.** Mitigation: a thread joined with a 5 s timeout.

## Dependencies
- None.

## Pseudocode / Algorithm
```text
constants.py: HLD §8.1.5 verbatim (assignments only; imports only re)
safeio.py:    HLD §8.2.2 verbatim
types.py:     from __future__ import annotations; TYPE_CHECKING imports of models types
              canonical_json; HLD §8.4.2 models + parse_entry_bytes; dataclasses per §8.2.1,
              §8.6.1 and §13.4; ResultCacheHook (Protocol); CacheStore / CacheAdmin (§8.4.3);
              errors per §14.1
fakes.py:     InMemoryCacheStore keeps {key: CacheEntry}, {sha: bytes}, {key: mtime}
```

## Schemas / Interface Notes
- **Interface / API:** `CacheStore`, `CacheAdmin`, `ResultCacheHook` (HLD §8.4.3, §8.6.1, §14.1).
- **Spec / data schema:** entry JSON v1 (HLD §13.3).
- **Triggers / events:** event name constants only (HLD §15).
- **Artifacts:** the hostile-entry corpus under `tests/fixtures/result_cache/corpus/`.

## Handoff Boundary
- **Upstream:** none.
- **Downstream:** T-28J9oR (commit 1); every other cache task (commit 3, i.e. the whole task).

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-FJH6LI-cache-contracts/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Interface-first task.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 scope (safeio, parse
  boundary, split ABCs, hook protocol, canonical_json, corpus, AST guard), 16 h.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 (early-gate A1c/A1d, A4,
  manager B):
  - **No dependency on T-28J9oR:** `ResultCacheRecord` is referenced only under
    `TYPE_CHECKING`.
  - **Commit numbering** is fixed for the whole epic: T-8tr1H4 needs commit 3, not commit 2.
  - **New constants:** `DEFAULT_TASK_CACHE_POLICY`, `AGENT_KEY_FIELDS`/`AGENT_NON_KEY_FIELDS`,
    the inline byte bound, the `GIT_OPTIONAL_LOCKS` pair; `MODE_REFRESH` removed.
  - **New error** `CacheUnsafePathError`; `CacheAdmin.verify()` loses `repair`.
