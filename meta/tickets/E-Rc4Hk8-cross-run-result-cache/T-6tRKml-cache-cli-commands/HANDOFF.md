# HANDOFF: T-6tRKml-cache-cli-commands

- Task: `T-6tRKml-cache-cli-commands`
- State: `Done (handoff available)`
- From: `developer` (Dev C)
- To: T-JCOAsq (Part 3), T-fXWbqg (G2), T-bdQZW4 (docs), T-nPMuz4 (reads `stats --json`)

## What was delivered (commit `033dd79`)
- `ao cache ls|stats|show|rm|prune|clear|verify`, each with `--workspace/-w` and `--json`
  (`rm <key|prefix>` only; `verify` read-only). `rm --run/--task`, `verify --repair`, `refresh`
  are not built.
- `cache/cli.py`: Typer surface + the `_now()` clock seam (module-level imports: `typer`,
  `cache.constants`; AST test `TestCacheCliStaysImportLight` stays green). `cache/cli_ops.py`:
  every command's behaviour, imported lazily inside the command bodies. `cache/restore_sweep.py`:
  the prune-side sweep of stale restore staging files. `safeio.open_dir_fd` (new) and
  `safeio.strip_control_chars` (now also strips bidi / zero-width format characters, G1a SEC-14).
- The HLD 13.4 JSON Schemas: `tests/fixtures/result_cache/schemas/*.json`.
- Tests: `tests/test_e2e_cli_result_cache_admin.py` (138, `CliRunner`), `tests/cache/test_restore_sweep.py`
  (32), 21 added to `tests/cache/test_safeio.py`.

## Frozen names / contracts
- Exit codes: 0 ok; 1 not found / ambiguous / store problem / refused / corruption found; 2 bad
  argument (malformed prefix, `--sort`, negative or out-of-range `--limit` / `--older-than` /
  `--max-bytes`, plus Click's own usage errors). Schema ids `ao.result-cache.{ls,stats,show,rm,prune,clear,verify}/v1`
  (named `SCHEMA_*` in `cache/constants.py`).
- **`ao cache stats --json` (the G0 protocol reads it):** `schema`, `root`, `exists`, `entries`,
  `invalid_entries`, `expired_entries`, `foreign_version_dirs[]`, `blobs`, `orphan_blobs`,
  `tmp_files`, `trash_dirs`, `anomalies`, `bytes{entries,blobs,referenced_blobs,orphan_blobs,total}`,
  `limits{max_bytes,max_entry_bytes,ttl_days|null}`, `oldest_created_at`, `newest_created_at`
  (ISO-8601 with offset, or `null`). With no cache directory: `exists: false`, every count 0, both
  dates `null`, exit 0. Counts are the store's (`CacheAdmin.stats`); `limits` come from
  `.ao/config.yaml cache.*` (env and run mode ignored).
- On failure `--json` still prints exactly one document: `schema` plus the schema's required
  fields where known (`removed: []`, zero counts, `ok: false` / `problems: []`, `candidates: []`) and an
  `error` string. Click usage errors raised before the command body are plain stderr text (exit 2).
- `prune --json` additions (additive): `removed_restore_tmp`, `kept_restore_backups`,
  `restore_sweep_truncated`. `verify` problem `kind` may also be `unreadable` (G1a S-3).
- Prefix resolution (`show`, `rm`): `KEY_PREFIX_RE.fullmatch` (4-64 lowercase hex, never
  lower-cased or stripped), only `entries/v1/<prefix[:2]>/` is listed (bounded by `CLI_MAX_DIR_ITEMS`),
  `<64 hex>.json` regular files only (links and anomalies are not candidates), a unique match is
  required, at most `CLI_MAX_CANDIDATES` are listed.
- Output hygiene: every entry-derived or user-supplied string printed in text mode goes through
  `cli_ops.clean` (`safeio.strip_control_chars` + clip to `CLI_MAX_DISPLAY_CHARS`); JSON is escaped
  by `json.dumps`.

## Prune-side restore-leftover sweep (G1a SEC-19, G1b S-1): what it deletes
Only in `ao cache prune`: regular, user-owned files named exactly like a restore staging file
(`.ao-result-cache-*.tmp`, `.tmp.bak`), direct children of the parent directory of an output of a
valid entry (paths re-validated; every component lstat-checked; directory opened `O_NOFOLLOW`),
whose ctime is older than `TMP_SWEEP_GRACE_SECONDS`. A `.bak` with a link count of 1 (the only name
of replaced content) is KEPT and counted. Never through a symlink, never a directory, never outside
the workspace, never a protected path (`.git`, `.claude`, `.orchestrator`, ...). `--dry-run` deletes
nothing. Residual: directories only evicted / never-stored entries name are not visited.

## For G2 (what to attack)
- Hostile arguments: control characters / 10k-char / option-like / traversal prefixes, `--sort`, bounds.
- The sweep's deletion set (above) and `clear` / `rm` / `prune` behind a symlinked `.orchestrator/cache`.
- `rm` removes the entry file only (blobs go with the next prune, after the 1 h grace).

## For T-bdQZW4 (docs)
- HLD 8.9 / import graph: `cache/cli.py` (surface) -> `cache/cli_ops.py` (lazy) -> `store`,
  `restore_sweep`, `settings`, `safeio`; `restore_sweep` imports `safeio`, `constants`, `types`.
- `prune` JSON additions; `verify` `unreadable`; `--older-than 0` does not expire an entry with a
  future `created_at`; ADR-0019 addendum for the sweep.

## Verification the receiver should run
- `pytest -q tests/test_e2e_cli_result_cache_admin.py tests/cache/test_restore_sweep.py tests/cache/test_safeio.py`
- `pytest -q tests/cache/test_cli_result_cache_wiring.py` (import-light AST test, cache-off module allow-list)

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: deferred forms removed.
  State `Draft` mirrors `TASK.md` and `STATUS.md`.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done; handoff available (commit `033dd79`). Contents above replace the stub; `TASK.md`, `STATUS.md` and the epic rollup agree.
