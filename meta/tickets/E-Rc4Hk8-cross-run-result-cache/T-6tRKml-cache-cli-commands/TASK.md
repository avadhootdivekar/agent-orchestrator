# TASK: T-6tRKml-cache-cli-commands

## Metadata
- Task ID: `T-6tRKml-cache-cli-commands`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev C)
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 2)
- Status: `Draft`
- Estimate: `20 focus hours (2.5 days)` · Sprint 2 tail (about 8 h) → Sprint 3 (about 12 h)

## Requirements Mapping
- Requirement IDs: FR-12, FR-17, NFR-10 (M-2, M-15)
- HLD: §8.9, §13.4, §14.2
- ADR-0019: D27

## Description
Implement the `ao cache` subcommands on the `cache_app` skeleton from T-28J9oR. The behaviour,
options, outputs and exit codes are specified in the HLD §8.9 table.

**Commands:**

- **Read-only** (do these first, at the end of Sprint 2): `ls`, `stats` and `show`.
- **Mutating** (Sprint 3): `rm`, `prune`, `clear` and `verify`.

**Shared behaviour:**

- `--workspace/-w`, resolved as `--workspace`, then `AO_WORKSPACE_ROOT`, then config discovery
  through a lazy import of `cli._resolve_workspace_root`.
- `--json` prints exactly one JSON document, including on exit 1.
- Every entry-derived string passes through `safeio.strip_control_chars` in text output.
- Every regex check uses `fullmatch`.
- The wall clock is read through a module-level `_now()`.
- The commands work even when the run mode is off.

**`rm --run R --task T`.** Follow HLD §8.9 steps 1–5:

1. `feedback.validate_run_id`;
2. load the run through `RunStateStore`;
3. read the record;
4. check its key with `SHA256_HEX_RE.fullmatch`. If it fails, exit 1 with "tampered record";
5. `delete_entry`.

**Prefix resolution** (`show`, `rm`): `KEY_PREFIX_RE.fullmatch`, then list only that shard
directory, and require a unique match.

**Fixtures.** Store the §13.4 JSON Schemas as fixtures under
`tests/fixtures/result_cache/schemas/`.

## File scope (exclusive)
- `src/agent_orchestrator/cache/cli.py` (commands; the skeleton came from T-28J9oR)
- `tests/test_e2e_cli_result_cache_admin.py` (new)
- `tests/fixtures/result_cache/schemas/**` (new)

## Inputs / Outputs
- **Inputs:**
  - T-HjxNQ0 (the `CacheAdmin` backend);
  - T-U7ckfd (entries, `delete_entry`);
  - T-28J9oR (the skeleton, `CacheConfig`);
  - `feedback.validate_run_id`, `RunStateStore` and `LocalFsArtifactStore`.
- **Outputs:** an operator CLI for the result cache.

## Acceptance Criteria
All tests are E-7, run through `CliRunner` against a temp workspace populated through the store
API.

1. **`ls`.**
   - The text output shows key[:12], last used, created, outputs, bytes, source task/run and cost.
   - `--json` validates against `ao.result-cache.ls/v1`.
   - `--limit` and `--sort lru|created|size` work. An invalid `--sort` exits 2.
   - An empty cache exits 0.
2. **`stats`.**
   - `--json` validates against `ao.result-cache.stats/v1`.
   - `foreign_version_dirs` lists a planted `entries/v2/`.
   - `exists: false` when there is no cache directory, with exit 0.
3. **`show`.**
   - Exit 0 for a unique prefix. `--json` validates against `ao.result-cache.show/v1` and includes
     blob presence and `components`.
   - Exit 1 for an unknown or ambiguous prefix; candidates are listed.
   - Exit 2 for a malformed prefix: `XYZ`, `../x`, 3 characters, or uppercase.
4. **`rm`.**
   - `rm <prefix>` removes exactly one entry: exit 0, `--json` validates against
     `ao.result-cache.rm/v1`.
   - `rm --run R --task T` removes the record's entry.
   - Exit 2 for a malformed run id, both forms, or neither form.
   - Exit 1 for a missing run, a missing record, or a **tampered record key** (for example
     `"../../etc"` written into `state.json`); in the tampered case no path is built (spy).
5. **`prune`.**
   - Removed counts are reported by reason, with bytes before and after.
   - `--older-than 0` expires every entry. A negative value exits 2.
   - `--max-bytes` overrides the config.
   - `--dry-run` deletes nothing.
   - `--json` validates against `ao.result-cache.prune/v1`.
6. **`clear`.**
   - `--yes` empties the store, exit 0.
   - Without `--yes` and with stdin not a TTY, it exits 1 with "refusing to clear without --yes".
   - A patched removal failure exits 1.
   - `--json` validates against `ao.result-cache.clear/v1`.
7. **`verify`.**
   - Exit 0 when clean; orphan blobs and foreign versions alone still count as clean.
   - Exit 1 when corruption is found, even with `--repair`, which still repairs it.
   - `--json` validates against `ao.result-cache.verify/v1`.
8. **M-15 (control characters).** An entry whose `source.task_id` contains `\x1b[31m` and `\x07`
   prints without control characters in text mode.
9. **Workspace resolution.** `-w` wins over `AO_WORKSPACE_ROOT`, which wins over discovery.
10. **Hygiene.**
    - Line coverage of `cache/cli.py` is at least 85%.
    - `ruff` and `mypy` are clean.
    - `pytest -q` has no new failures.

## Test requirements
- `tests/test_e2e_cli_result_cache_admin.py`: AC-1..AC-9, using `jsonschema` against the fixtures.

## Risks
- **Destructive commands on the wrong workspace.** Mitigation: explicit resolution, and `clear`
  requires `--yes` when stdin is not a TTY.
- **Agent-writable `state.json` feeding `rm --run`.** Mitigation: the regex is checked before any
  path is built (M-2).

## Dependencies
- T-HjxNQ0, T-28J9oR, T-U7ckfd.

## Pseudocode / Algorithm
```text
HLD §8.9 (table, rm --run steps 1–5, prefix resolution 1–4) verbatim.
```

## Schemas / Interface Notes
- **CLI:** HLD §14.2.
- **JSON:** HLD §13.4 (`ls`, `stats`, `show`, `rm`, `prune`, `clear`, `verify`, all v1).

## Handoff Boundary
- **Upstream:** T-HjxNQ0, T-28J9oR.
- **Downstream:** T-JCOAsq (final suite), T-fXWbqg (G2 checks argument handling), T-bdQZW4.

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-6tRKml-cache-cli-commands/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: `ao cache` admin commands.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 changes:
  - adds `rm` (critic #2: single-entry invalidation);
  - control-character stripping (security S8);
  - `fullmatch` and `--sort` as str (developer #16);
  - `foreign_version` reporting;
  - `--older-than 0` / negative semantics;
  - **schedule:** starts at the Sprint 2 tail, finishes in Sprint 3.
