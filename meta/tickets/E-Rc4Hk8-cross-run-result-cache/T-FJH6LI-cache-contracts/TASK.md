# TASK: T-FJH6LI-cache-contracts

## Metadata
- Task ID: `T-FJH6LI-cache-contracts`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev A)
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 2)
- Status: `Draft`
- Estimate: `16 focus hours (2 days)` · Sprint 1, Wave 1

## Requirements Mapping
- Requirement IDs: FR-7, FR-8, NFR-4, NFR-6, NFR-7, NFR-10 (M-2, M-4, M-6, M-9, M-13, M-14, M-15), NFR-11
- HLD: §8.0, §8.1.5, §8.2.1, §8.2.2, §8.4.2, §8.4.3 (ABCs only), §8.6.1, §14.1, §15
- ADR-0019: D17, D18, D21, D28, D29, D31

## Description
Create the interface-first foundation of `agent_orchestrator.cache`. Every other cache task
develops against these names in parallel. Land it as **three commits, in this order**:

1. **Commit 1 (≤ 3 h): `cache/__init__.py` + `cache/constants.py`.**
   - `__init__.py`: a docstring only, no imports. This task owns the file.
   - `constants.py`:
     - every constant listed in HLD §8.1.5;
     - a `REASON_*` constant for every reason string in HLD §8.3.3 and §8.6.4;
     - an `EVENT_*` constant for every event in HLD §15: `cache.hit`, `cache.would_hit`,
       `cache.miss`, `cache.skip`, `cache.store`, `cache.evict`, `cache.corrupt`,
       `cache.disabled`.
   - Notify T-28J9oR as soon as commit 1 is on the branch.
2. **Commit 2 (≤ 5 h): `cache/safeio.py`** (HLD §8.2.2). Notify T-8tr1H4 when it lands. It
   contains:
   - the flags `O_SAFE_READ` and `O_SAFE_CREATE`;
   - the errors `SafeIOError`, `NotRegularFileError`, `TooLargeError` and `UnsafePathError`;
   - the functions `open_regular_read`, `read_bounded`, `create_exclusive`, `check_dir_chain`,
     `ensure_dir_chain`, `check_root_dir`, `is_sensitive_rel_path`, `posix_rel` and
     `strip_control_chars`.
3. **Commit 3: `cache/types.py`, plus test fakes and the reusable store contract test.**
   - **`canonical_json(obj) -> str`.** The ONE canonical serializer, shared by `types`, `hashing`
     and `keys`; it lives here to keep the import graph acyclic. It is `json.dumps` with sorted
     keys, separators `(",", ":")`, `ensure_ascii=True` and `allow_nan=False`.
   - **Entry models.** `OutputRecord`, `EntryUsage`, `EntrySource`, `KeySummary` and `CacheEntry`,
     exactly as in HLD §8.4.2:
     - bounded fields;
     - `AwareDatetime`;
     - `allow_inf_nan=False`;
     - `CacheEntry.schema_` aliased to `schema`, with `populate_by_name=True` and
       `extra="ignore"`;
     - `CacheEntry.to_canonical_bytes()`.
   - **`parse_entry_bytes(raw, expected_key) -> CacheEntry`**, the TOTAL parse boundary (D28). Any
     failure raises `CacheIntegrityError` and nothing else.
   - **Dataclasses.** `Digest`, `BlobRef`, `EntryInfo`, `CacheStats`, `PruneReport` (including
     `deferred: bool` and a `total_removed` property), `ClearReport`, `VerifyProblem` and
     `VerifyReport`, with field shapes per HLD §13.4.
   - **Key and lookup contracts.** `KeyRequest`, `KeyDeps`, `CacheKey` (including `components` and
     `cli_version`), `LookupRequest`, `PendingStore`, `LookupOutcome` and `StoreResult`
     (HLD §8.2.1, §8.6.1).
   - **`ResultCacheHook`**, a `typing.Protocol` with `lookup(request, log)` and
     `store_success(pending, *, ts, now, log)`.
   - **ABCs, both PROVISIONAL**, with docstrings saying so (HLD §8.4.3):
     - `CacheStore`: `check`, `get_entry`, `put_entry`, `touch_entry`, `delete_entry`,
       `has_blob`, `put_blob`, `read_blob`, `delete_blob`, `maybe_enforce_limits`;
     - `CacheAdmin`: `iter_entries`, `stats`, `prune`, `clear`, `verify`.
   - **Errors** (HLD §14.1): `CacheError(reason)`, `CacheIntegrityError`, `CacheBlobMissingError`,
     `CacheTooLargeError`, `CacheLayoutError`, `UncacheableError(reason, detail)`,
     `StoreSkip(reason, detail)` and `RestoreMiss(reason, evict, blob, detail)`.
   - **`tests/cache/fakes.py`:**
     - `InMemoryCacheStore(CacheStore, CacheAdmin)`, dict-backed, enforcing the same key and sha
       regex checks as the real store;
     - `FakeRepoHeadReader`, `FakeWorktreeProbe`, `fake_cli_version_of` and a fake VCS runner,
       each configurable to return a value or raise.
   - **`tests/cache/store_contract.py`.** A parametrizable contract suite that T-U7ckfd reruns
     against `LocalFsCacheStore`.
   - **`tests/cache/test_ast_guard.py` (U-AST)**, with a negative self-test.

**Layering rule (HLD §8.0).**
- `constants` imports only `re`.
- `safeio` imports only the stdlib and `constants`.
- `types` imports `constants`, `safeio`, `pydantic` and `models`.
- None of these imports `engine`, `runstate`, `cli` or `ui`.

## File scope (exclusive)
- `src/agent_orchestrator/cache/__init__.py`, `constants.py`, `safeio.py`, `types.py` (new)
- `tests/cache/__init__.py`, `fakes.py`, `store_contract.py`, `test_types.py`, `test_safeio.py`,
  `test_ast_guard.py` (new)
- `tests/fixtures/result_cache/corpus/**` (new; shared read-only with T-JCOAsq)

## Inputs / Outputs
- **Inputs:** HLD §8.0–§8.6 contracts, §13.3 / §13.4 shapes, §14.1, §15.
- **Outputs:** importable constants, safe-I/O helpers, contracts, ABCs, errors, fakes, the
  contract suite and the hostile-entry corpus. The names are frozen in `HANDOFF.md`.

## Acceptance Criteria
1. **Constants.**
   - `constants.py` defines every name in HLD §8.1.5.
   - A test compares the set of `REASON_*` values with a fixture list copied from the HLD
     reason tables (§8.3.3, the §8.6.4 miss and store-skip lists, §15 evict reasons). A reason
     missing on either side fails.
   - `EVENT_*` values equal the eight §15 event names.
   - The module imports only `re`.
2. **Regexes.**
   - `SHA256_HEX_RE.fullmatch` accepts exactly 64 lowercase hex characters. It rejects uppercase,
     63 or 65 characters, `../x`, NUL, and a valid sha followed by `\n`.
   - `KEY_PREFIX_RE.fullmatch` accepts 4–64 lowercase hex characters.
   - `HEX64_TOKEN_RE_BYTES.finditer` finds every 64-hex token in raw bytes, including in an
     unparseable file.
3. **`safeio` (U-IO1..IO7).**
   - `open_regular_read`:
     - raises `NotRegularFileError` for a symlink (final component), a directory and a FIFO;
     - the FIFO case returns within 5 s (thread + join timeout);
     - `FileNotFoundError` passes through.
   - `read_bounded` raises `TooLargeError` at `max_bytes + 1`.
   - `create_exclusive` fails if the path exists and creates files with mode `0o600`.
   - `check_dir_chain` and `ensure_dir_chain` raise `UnsafePathError` on a symlinked component.
   - `check_root_dir`:
     - rejects a symlinked root;
     - rejects a root owned by another uid (simulate with monkeypatched `os.geteuid`);
     - rejects a group-writable root it does not own;
     - chmods a group-writable root it **does** own to `0o700`.
   - `is_sensitive_rel_path` is table-tested against every component and basename in HLD D29,
     plus negatives (`docs/claude.md`, `out/.gitkeep`).
   - `strip_control_chars` removes C0, C1 and DEL, and keeps tabs and newlines out of single-line
     output.
   - `posix_rel` is table-tested.
4. **Entry models (U-T1..T5).**
   - The HLD §8.4.2 example (with valid 64-hex shas) round-trips.
   - These are rejected:
     - a naive `created_at`;
     - `cost_usd = inf` or `nan`;
     - `mode > 0o777`;
     - `size < 0`;
     - empty `outputs`;
     - a non-hex `key`;
     - a 300-character `run_id`.
   - An unknown extra field is ignored.
   - `model_dump(by_alias=True)` emits `"schema"`.
   - `to_canonical_bytes()` is byte-identical across two constructions with different field
     orders.
5. **`parse_entry_bytes` is total.** For a corpus file it raises `CacheIntegrityError` and nothing
   else. The corpus is:
   - 100 000 nested `[`;
   - invalid UTF-8;
   - `[]`;
   - `{"schema": "x"}`;
   - a 1 MiB string field;
   - a key mismatch.

   Reasons: `corrupt_entry` for every corpus case except key mismatch; `key_mismatch` when the
   entry key differs from `expected_key`. The corpus files are shared with T-JCOAsq (ADV-9).
6. **`canonical_json`.** Equals `json.dumps(obj, sort_keys=True, separators=(",", ":"),
   ensure_ascii=True, allow_nan=False)` and raises `ValueError` on NaN.
7. **ABCs.**
   - `CacheStore` and `CacheAdmin` raise `TypeError` when instantiated.
   - Their abstract-method sets equal the lists in this task's description.
   - `ResultCacheHook` is a `Protocol` with exactly `lookup` and `store_success`.
8. **Errors.**
   - Every error exposes `.reason`.
   - `RestoreMiss` also exposes `.evict`, `.blob` and `.detail`.
   - `UncacheableError` and `StoreSkip` expose `.detail`.
9. **Fakes.**
   - `InMemoryCacheStore` passes `store_contract.py`: put/get entry, put/read blob, dedupe,
     touch, delete, `has_blob`, `check`, an invalid key (`ValueError`), and iteration.
   - `FakeRepoHeadReader` and `FakeWorktreeProbe` can each be set to return a value or raise
     `UncacheableError`.
10. **U-AST.**
    - `test_ast_guard.py` walks every `.py` file in `src/agent_orchestrator/cache/`. It fails on:
      - an `import` of `pickle`, `marshal` or `shelve`;
      - a call to `eval` or `exec`;
      - any call with `shell=True`;
      - any bare `open(` or `os.open(` outside `safeio.py`.
    - A negative self-test feeds it a synthetic module source and expects each violation to be
      reported.
11. **Hygiene.**
    - `ruff check`, `ruff format --check` and `mypy src` are clean for the new files.
    - `pytest -q` has no new failures against the baseline (5041 passed / 8 skipped / 2 known
      bench failures).
    - `tests/conftest.py` is not edited.

## Test requirements
- `tests/cache/test_types.py`: AC-4..AC-8.
- `tests/cache/test_safeio.py`: AC-3.
- `tests/cache/test_ast_guard.py`: AC-10.
- `tests/cache/store_contract.py`, run against the fake: AC-9.
- Each new test module controls `AO_CACHE` itself (`monkeypatch.delenv("AO_CACHE", raising=False)`).

## Risks
- **Contract churn breaks parallel tasks.** Mitigation: freeze the names in `HANDOFF.md`. Any
  change must be noted in every consumer's `STATUS.md` and in HLD §14.1.
- **`check_root_dir` ownership tests need root-independent simulation.** Mitigation: monkeypatch
  `os.geteuid` and the stat results; never `chown`.
- **FIFO tests can hang the suite.** Mitigation: always run them in a thread joined with a 5 s
  timeout.

## Dependencies
- None. This is the first task of the epic.

## Pseudocode / Algorithm
```text
constants.py: assignments only (HLD §8.1.5) + REASON_* + EVENT_*; no logic, no imports besides re
safeio.py: exactly HLD §8.2.2 (getattr(os, flag, 0) for portability; never follow a final symlink;
           O_NONBLOCK + fstat S_ISREG before any read; bounded reads)
types.py:
  canonical_json(obj)
  pydantic entry models per §8.4.2 (+ Annotated[str, Field(max_length=MAX_PATH_CHARS)] list items)
  parse_entry_bytes(raw, expected_key): try json.loads -> dict check -> schema check ->
      CacheEntry.model_validate; except CacheIntegrityError: raise; except Exception: raise
      CacheIntegrityError(REASON_CORRUPT_ENTRY, type(exc).__name__) from None;
      key != expected_key -> CacheIntegrityError(REASON_KEY_MISMATCH)
  dataclasses (frozen) per §8.2.1 / §8.6.1 / §13.4
  class ResultCacheHook(Protocol); class CacheStore(ABC); class CacheAdmin(ABC)
  errors per §14.1
fakes.py: InMemoryCacheStore keeps {key: CacheEntry}, {sha: bytes}, {key: mtime}; same regex checks
```

## Schemas / Interface Notes
- **Interface / API:** `CacheStore`, `CacheAdmin` and `ResultCacheHook` (HLD §8.4.3, §8.6.1, §14.1).
- **Spec / data schema:** entry JSON v1 (HLD §13.3).
- **Triggers / events:** event name constants only (HLD §15).
- **Artifacts:** the hostile-entry corpus under `tests/fixtures/result_cache/corpus/`, shared
  with T-JCOAsq.

## Handoff Boundary
- **Upstream:** none.
- **Downstream:**
  - T-28J9oR: commit 1.
  - T-8tr1H4: commit 2.
  - T-uoYW6b, T-U7ckfd, T-u3jG8F, T-gDNjN2, T-HjxNQ0, T-eyn5UG, T-6tRKml and T-JCOAsq: commit 3.

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-FJH6LI-cache-contracts/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Interface-first task. It exists so
  that the key builder, the store and restore can be built in parallel.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 scope, re-estimated from
  12 h to 16 h. Changes:
  - adds `safeio.py`, the total parse boundary, the `CacheAdmin` split, `ResultCacheHook` and the
    lookup contracts;
  - adds `canonical_json` (moved here so that `types`, `hashing` and `keys` can share it without
    an import cycle);
  - adds the AST guard and the hostile corpus;
  - this task now owns `cache/__init__.py`.

  Driven by developer #10, dev-security S1/S4 and reviewer R2/R5/R9 (HLD §23.4).
