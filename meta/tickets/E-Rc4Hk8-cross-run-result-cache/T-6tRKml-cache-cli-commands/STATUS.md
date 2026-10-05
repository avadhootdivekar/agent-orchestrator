# STATUS

- ID: `T-6tRKml-cache-cli-commands`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (Dev C)

## This update
`ao cache ls|stats|show|rm|prune|clear|verify` (commit `033dd79`). `cache/cli.py` is the Typer
surface only (module-level imports: `typer` and `cache.constants`; `_now()` seam); the behaviour
is in the new `cache/cli_ops.py` (lazy-imported inside each command body, so the import-light AST
rule and the cache-off module allow-list still hold), plus the new `cache/restore_sweep.py`
(prune-side sweep, see Decisions). `rm --run/--task`, `verify --repair` and `refresh` are NOT built
(deferred, non-MVP). The commands inspect the store whatever the run mode / config `cache.enabled`
is, never create the cache directory, and never follow a link.

## Acceptance criteria
| AC | Result | Evidence |
|----|--------|----------|
| 1 `ls` | PASS | `TestLs`: text columns key[:12] / last used / created / outputs / bytes / task/run / cost; `--json` validates against `ao.result-cache.ls/v1`; `--limit`, `--sort lru|created|size` (orders pinned); invalid `--sort` (incl. control characters, empty) exits 2 in text and JSON; `--limit 0/-3/huge` exits 2; empty or missing cache exits 0 with `(result cache is empty: <root>)` and creates nothing; invalid / expired / ok statuses; junk files and symlinked shards are not listed |
| 2 `stats` | PASS | `TestStats`: `--json` validates against `ao.result-cache.stats/v1` and carries `entries`, `bytes.total` (= entries + blobs), `expired_entries`, `oldest_created_at`, `newest_created_at` (+ limits from config, `ttl_days: null` never expires); a planted `entries/v2/` is listed in `foreign_version_dirs` and never read as v1; `exists: false`, exit 0, no directory created |
| 3 `show` | PASS | `TestShow`: unique prefix exits 0 (text and `--json` against `ao.result-cache.show/v1` with `$ref` to the entry schema, blob presence and `size_on_disk`, `components`, `expired`); unknown / ambiguous exit 1 with the candidates listed (capped at 10, `candidates` in JSON); `XYZ`, `../x`, 3 chars, uppercase, 65 chars, `ab/cd`, escape sequences exit 2; an option-like argument is a Typer usage error (2); a corrupt entry exits 1; a symlinked entry is never followed |
| 4 `rm` | PASS | `TestRm`: removes exactly one entry (others and all blobs untouched), `--json` validates against `ao.result-cache.rm/v1`; unknown / ambiguous exit 1 and the store tree is byte-identical afterwards; malformed exits 2; `rm --run/--task` is "No such option" (2); a corrupt entry is removable by prefix; a symlink or directory in an entry slot is never removed |
| 5 `prune` | PASS | `TestPrune`: counts by reason (expired / lru / invalid), bytes before/after, validates against `ao.result-cache.prune/v1`; `--older-than 0` expires every entry; negative, above `MAX_TTL_DAYS` / `MAX_CONFIG_BYTES` and non-numeric values exit 2 and delete nothing; `--max-bytes` beats the config (and `0` is a valid budget); `--dry-run` leaves the tree byte-identical; an unsafe (symlinked) root is an error, nothing outside is touched |
| 6 `clear` | PASS | `TestClear`: `--yes` empties the store (JSON validates against `ao.result-cache.clear/v1`); without `--yes` and without a TTY exit 1 "refusing to clear without --yes" and the tree is unchanged; a patched `shutil.rmtree` failure exits 1 with `store_error` (text and JSON); `--json` never prompts even on a TTY; the interactive y/n path; a symlinked root is refused; `.orchestrator/runs` is untouched |
| 7 `verify` | PASS | `TestVerify`: clean exits 0; orphan blobs and a foreign `v2` alone are clean (0); corrupt entry, key mismatch, missing blob, corrupt blob, symlinked entry exit 1; the whole store tree (incl. mtimes) is byte-identical after `verify` on a store that has every kind of problem and a sweepable orphan; `--repair` is "No such option" (2); validates against `ao.result-cache.verify/v1` |
| 8 M-15 | PASS | `TestControlCharactersAreNeverPrinted`: a `source.task_id` with `\x1b[31m`, `\x07`, a bidi override and a zero-width space prints without any control or invisible format character in `ls` and `show` text; `--json` carries `\u001b` escapes, never the raw bytes; hostile file / directory names in the store (verify, stats, ls) and hostile CLI arguments (errors) are cleaned; a 10k-character argument is clipped |
| 9 Workspace | PASS | `TestWorkspaceResolution`: `-w` and `--workspace` beat `AO_WORKSPACE_ROOT`, which beats the config-discovered spec triplet (three workspaces with 1 / 2 / 3 entries); unresolvable -> exit 1 with one JSON document; a workspace that is a file is an error, not a traceback |
| 10 Hygiene | PASS | line coverage of `cache/cli.py` 100%, `cache/cli_ops.py` 100%, `cache/restore_sweep.py` 100% (`--cov`, target 85%); ruff and mypy clean (see Evidence); no new failures (full suite) |

Also: `TestCommandsWorkWhateverTheRunMode` (config `enabled: false`, `shadow`, `AO_CACHE=0`: every command works; malformed config -> exit 1 with one document; help lists exactly the seven MVP commands and no `refresh` / `repair`; importing `agent_orchestrator.cli` does not load `cli_ops`, `store`, `restore_sweep`, `types`, `report`, `coordinator`).

## Decisions
1. **Prune-side restore-leftover sweep: BUILT, narrowly.** `ao cache prune` (only) removes stale `.ao-result-cache-*.tmp` / `.tmp.bak` staging files (`safeio.is_restore_tmp_name`, the same predicate hashing already uses to ignore them) from the output directories named by VALID entries still in the store when `prune` starts (read before it evicts anything). Safety rules, each with a test: the entry paths (hostile data) are re-validated (plain normalized relative path, no `..`, no control / bidi characters, not a sensitive location) and every existing component is lstat-checked as a real directory; the directory is opened `O_NOFOLLOW` and lstat / unlink go through that descriptor (direct children only); only REGULAR files owned by the user; ctime (not mtime: a `.bak` hard link keeps the old file's mtime) older than `TMP_SWEEP_GRACE_SECONDS`; a `.bak` that is the ONLY remaining name of its inode (a crash after the commit rename: the sole copy of the replaced content) is kept and reported, never deleted; `--dry-run` counts and deletes nothing; bounded by `RESTORE_SWEEP_MAX_DIRS` / `CLI_MAX_DIR_ITEMS`. Residual (documented, not fixable without a spec): a directory that only an evicted or never-stored entry names is not visited. JSON additions to `ao.result-cache.prune/v1` (additive): `removed_restore_tmp`, `kept_restore_backups`, `restore_sweep_truncated`.
2. **Output sanitisation (SEC-14).** `safeio.strip_control_chars` (the one definition) now also removes the invisible Unicode format characters (U+200B-200F, U+2028-202E, U+2060-2064, U+2066-206F, U+FEFF) on top of C0/C1/DEL; the CLI additionally clips every entry-derived string. JSON output is escaped by `json.dumps`.
3. **`--json` on failure** prints one document `{"schema": <id>, ..., "error": <message>}` (with the schema's own required fields where known: `removed: []`, zero counts, `ok: false`, `problems: []`, `candidates`); Typer usage errors that happen before the command body (an unknown option, a non-integer value, a missing argument) are Click's text on stderr with exit 2.

## Evidence
- `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_e2e_cli_result_cache_admin.py tests/cache/test_restore_sweep.py tests/cache/test_safeio.py --cov=agent_orchestrator.cache.cli --cov=agent_orchestrator.cache.cli_ops --cov=agent_orchestrator.cache.restore_sweep` -> **260 passed**, 100% / 100% / 100%.
- `.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache tests/test_spawn_provenance.py tests/test_nfr2_regression_gate.py tests/test_e2e_cli_result_cache_admin.py tests/test_e2e_cli.py tests/test_cli.py` -> **1510 passed** (I-1 / I-2 goldens `tests/cache/test_noop_proof.py`, `TestCacheCliStaysImportLight`, the cache-off module allow-list, spawn-provenance and the NFR-2 gate included, all unedited).
- FULL suite `.venv/bin/python -m pytest -q -p no:cacheprovider` -> **6572 passed, 10 skipped, 1 warning in 695.65 s** = 6582 collected (`pytest --collect-only -q | tail -1` -> `6582 tests collected`; the base was 6391 + 191 new). No failures.
- `.venv/bin/ruff check src tests` -> All checks passed; `.venv/bin/ruff format --check src tests` -> only generated `_build_info.py` would change; `.venv/bin/mypy src tests/cache` -> only the 4 pre-existing `_version.py` errors.
- `grep` of `.origin ==` / `!=` / `in` under `src/`: none added. `src/agent_orchestrator/ui/`, credentials / auth code, `tests/conftest.py` and `tests/test_nfr2_regression_gate.py` untouched; `cli.py` (the main one) untouched.

## Deviations from the ticket / HLD (reason)
1. **New modules `cache/cli_ops.py` and `cache/restore_sweep.py`** (the ticket scope names only `cache/cli.py`): the AST guard (`TestCacheCliStaysImportLight`) forbids every module-level import except typer / constants / settings, and mypy cannot resolve annotations that need `typing` / `datetime` there, so the commands live in a lazily imported module; the sweep is separate so it can be unit-tested with a fixed clock. HLD import graph: `cli` -> `cli_ops` (lazy) -> `store`, `restore_sweep`, `settings`, `safeio`.
2. **Edits outside the exclusive file scope:** `cache/constants.py` (CLI constants, schema ids, bounds; additive), `cache/safeio.py` (`open_dir_fd`; `strip_control_chars` extended per G1a SEC-14, which the gate notes assign to this task) and `tests/cache/test_safeio.py` (tests for both).
3. **`ao.result-cache.verify/v1` `kind` enum** in the stored fixture has one more value than HLD 13.4, `unreadable` (a file that cannot be read; added by the G1a S-3 remediation in `store.py`, which `verify` reports as a failing problem). `show/v1`'s bare `$ref` resolves against the id's own "directory", so the fixture test registers the entry schema under that spelling too.
4. **`prune` JSON/text extras** (Decision 1) and `limit` bound `CLI_MAX_LIMIT`; `--older-than` is bounded by `MAX_TTL_DAYS` and `--max-bytes` by `MAX_CONFIG_BYTES` (a larger value would overflow `timedelta` / is meaningless): exit 2.
5. **`--older-than 0`** expires every entry whose `created_at` is strictly before the clock (`is_expired` is `age > ttl`): an entry forged with a FUTURE `created_at` (G1a SEC-18) survives it; `ao cache clear` removes those.
6. **`rm` removes only the entry file.** Its blobs are reclaimed by the next `ao cache prune` (after the one-hour sweep grace), as the HLD text says "removed key".

## Risks / Blockers
- None blocking. Residuals: the restore-leftover sweep sees only directories of entries still in the store; the real wall clock is read only in `cache.cli._now()` (patched in every test); file ctime in the sweep tests is real, so those tests move the patched clock two hours forward instead.

## Next actions
1. T-nPMuz4 (G0 protocol): `ao cache stats --json` fields are frozen (see HANDOFF).
2. T-JCOAsq Part 3, T-fXWbqg (G2: hostile arguments / prefix handling / the sweep), T-bdQZW4 (docs: HLD 8.9 module list, prune JSON additions, `verify` `unreadable`).

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD §23.5). State stays `Draft`
  (surfaces); this file, `TASK.md`, `HANDOFF.md` (when present) and the epic `STATUS.md` rollup
  agree.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: G1b carry-over: orphaned `.ao-result-cache-*.tmp[.bak]` restore leftovers are only ignored by hashing (sec S-1), not deleted; a prune-side sweep (with a liveness / age rule) belongs here (see also G1a SEC-19). See `T-fXWbqg-cache-review-gates/STATUS.md` (G1b remediation).
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done (commit `033dd79`). All ten acceptance criteria pass; the carry-over is implemented (Decision 1). Evidence, deviations and decisions above; `TASK.md`, `HANDOFF.md` and the epic `EPIC.md` / `STATUS.md` rollup (4 Draft, 1 In Progress, 15 Done) agree.
