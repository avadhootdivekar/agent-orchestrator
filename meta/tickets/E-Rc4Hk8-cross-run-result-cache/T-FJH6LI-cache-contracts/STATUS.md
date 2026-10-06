# STATUS

- ID: `T-FJH6LI-cache-contracts`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (Dev A)

## This update
Implemented in the three commits the ticket prescribes (branch `worktree-agent-a18ce2c08e42a3a5a`):

| Commit | Subject | Content |
|--------|---------|---------|
| `9b24194` | commit 1 | `cache/__init__.py` (docstring only), `cache/constants.py`, `tests/cache/test_constants.py` |
| `5628556` | commit 2 | `cache/safeio.py`, `tests/cache/test_safeio.py` |
| `d1c1d08` | commit 3 | `cache/types.py`, `tests/cache/{fakes,store_contract,test_fakes,test_types,test_ast_guard}.py`, `tests/fixtures/result_cache/corpus/` (28 hostile files + `MANIFEST.json`) |

T-28J9oR may start (commit 1 landed); every other cache task may start (commit 3 landed). Names and
the few clarifications of under-specified shapes are frozen in `HANDOFF.md`.

## Acceptance criteria
| AC | Result | Evidence |
|----|--------|----------|
| 1 constants | PASS | `test_constants.py`: REASON_* set equals the HLD tables both ways; 8 EVENT_*; no `MODE_REFRESH`; `DEFAULT_TASK_CACHE_POLICY is False`; imports only `re` (AST) |
| 2 regexes | PASS | `test_constants.py` (uppercase, 63/65 chars, `../x`, NUL, trailing `\n`; prefix 4-64; byte-token finds both tokens in an unparseable file) |
| 3 safeio | PASS | `test_safeio.py` (symlink, dir, FIFO within 5 s via thread+join, bound at max+1, `0o600` exclusive create, chain checks, root checks with patched `os.geteuid`, sensitive-path table incl. `docs/claude.md` and `out/.gitkeep`, control chars, `posix_rel`; skip markers as specified) |
| 4 entry models | PASS | `test_types.py` (HLD example round-trips; 27 rejection cases; extras ignored; alias `schema`; canonical bytes order-independent) |
| 5 total parse | PASS | `test_types.py` over all 28 corpus files plus 400 seeded garbage samples: only `CacheIntegrityError`; `key_mismatch` for the mismatch file, `corrupt_entry` otherwise |
| 6 canonical_json | PASS | equals the specified `json.dumps` call; `ValueError` on NaN/inf |
| 7 ABCs / protocol | PASS | `TypeError` on instantiation; abstract sets equal the lists; `verify` takes no `repair`; `ResultCacheHook` is a Protocol with exactly `lookup` and `store_success` |
| 8 no T-28J9oR dependency | PASS | subprocess import with `models.ResultCacheRecord` deleted (and a subprocess check that `models`, `engine`, `runstate`, `cli`, `artifacts` and `ui` are not loaded); AST check that `models`/`artifacts` imports sit under `TYPE_CHECKING` |
| 9 errors | PASS | `.reason` everywhere; `RestoreMiss.evict/.blob/.detail`; `.detail` on `UncacheableError`/`StoreSkip`; `CacheUnsafePathError` is a `CacheError`, not a `CacheIntegrityError` |
| 10 fakes | PASS | `InMemoryCacheStore` passes `store_contract.py` (`test_fakes.py::TestInMemoryCacheStoreContract`); every fake can return or raise |
| 11 U-AST | PASS | `test_ast_guard.py` walks `cache/*.py`; 5 violation classes each detected by a negative self-test; `safeio.py` exempt only for `open`/`os.open` |
| 12 hygiene | PASS with one caveat | ruff/format/mypy clean (below); no line over 100 columns; `tests/conftest.py` untouched; **the full `pytest -q` suite was not run** (told not to run the 12-minute suite): only new files were added, `pytest --collect-only` collects 5413 tests with no error, and a spot run of 4 unrelated modules passed (32) |

## Evidence
- `.venv/bin/python -m pytest -q tests/cache` -> **246 passed** in 0.5 s (after commit 3; 31 after
  commit 1, 93 after commit 2).
- `.venv/bin/ruff check src/agent_orchestrator/cache tests/cache` -> All checks passed.
- `.venv/bin/ruff format --check src/agent_orchestrator/cache tests/cache` -> 12 files already formatted.
- `.venv/bin/mypy src tests/cache` -> only the 4 pre-existing `_version.py` errors (the new
  modules and tests are clean).
- `.venv/bin/python -m pytest -q --collect-only` -> 5413 tests collected, no errors.
- `.venv/bin/python -m pytest -q tests/test_smoke.py tests/test_nfr2_regression_gate.py tests/test_models_task_model.py tests/test_dag.py` -> 32 passed.
- `awk 'length>100'` over `src/agent_orchestrator/cache/*.py tests/cache/*.py` -> 0 lines.
- Import hygiene: `import agent_orchestrator.cache.types` loads only `agent_orchestrator`, `.cache`,
  `.cache.constants`, `.cache.types` (+ pydantic).

## Risks / Blockers
- No blockers.
- Risk: the admin dataclasses (`CacheStats`, `PruneReport`, `ClearReport`, `VerifyReport`,
  `EntryInfo`) were not copy-ready in the HLD; their shape was derived from §13.4 and the §8.4.3
  pseudocode (see `HANDOFF.md`). T-HjxNQ0 / T-6tRKml may add fields with defaults; a rename needs
  a note in each consumer's `STATUS.md`.
- Follow-up for T-28J9oR: when `ResultCacheRecord` lands in `models.py`, drop the
  `# type: ignore[attr-defined,unused-ignore]` on the `TYPE_CHECKING` import in `cache/types.py`.
- The full suite has not been run on this branch (see AC 12).

## Next actions
1. T-28J9oR and T-QgQy08 can import `constants`; T-8tr1H4, T-uoYW6b, T-U7ckfd, T-u3jG8F and the
   rest can import `types`, the fakes, the contract suite and the corpus.
2. T-U7ckfd: subclass `tests.cache.store_contract.CacheStoreContract` with a `LocalFsCacheStore`
   `store` fixture.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD §23.5). State stays `Draft`
  (core set, first); this file, `TASK.md`, `HANDOFF.md` (when present) and the epic `STATUS.md` rollup
  agree.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Implemented as commits 9b24194,
  5628556 and d1c1d08. State -> Done. 246 tests in `tests/cache`; ruff and mypy clean (4
  pre-existing `_version.py` mypy errors only). Full suite not run (AC 12 caveat above).
  Deviations from the HLD code blocks are small and listed in `HANDOFF.md`: the main one is
  `OutputRecord.kind: Literal["file"] = "file"` instead of `= KIND_FILE`, because mypy cannot
  narrow a `str` constant to a `Literal` (a test pins the two together).
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Added two test modules outside the
  ticket's listed file scope but inside its directory: `tests/cache/test_constants.py` (AC 1-2) and
  `tests/cache/test_fakes.py` (AC 10 runs the contract suite). No file outside the exclusive scope
  was touched.
