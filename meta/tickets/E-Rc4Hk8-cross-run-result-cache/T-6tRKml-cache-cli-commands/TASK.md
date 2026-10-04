# TASK: T-6tRKml-cache-cli-commands

## Metadata
- Task ID: `T-6tRKml-cache-cli-commands`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev C)
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 3)
- Status: `Draft`
- Estimate: `17 focus hours (2.1 days)` · Sprint 2 · surfaces

## Requirements Mapping
- Requirement IDs: FR-12, FR-17, NFR-10 (M-2, M-15)
- HLD: §8.9, §13.4, §14.2
- ADR-0019: D27, D34

## Description
Implement the `ao cache` subcommands on the `cache_app` skeleton from T-28J9oR, as specified in
the HLD §8.9 table: **`ls`, `stats`, `show`, `rm <key|prefix>`, `prune`, `clear`, `verify`**.

**Deferred in Rev 3 (do not implement):** `rm --run R --task T` (it reads the agent-writable
`state.json`) and `verify --repair` (non-MVP 15–16).

**Shared behaviour:** `--workspace/-w` → `AO_WORKSPACE_ROOT` → config discovery (lazy import of
`cli._resolve_workspace_root`); `--json` prints exactly one JSON document, including on exit 1;
entry-derived strings go through `safeio.strip_control_chars` in text output; every regex check
uses `fullmatch`; the wall clock is a module-level `_now()`; the commands work when the run mode
is off.

**Prefix resolution** (`show`, `rm`): `KEY_PREFIX_RE.fullmatch`, list only that shard directory,
require a unique match.

**Fixtures.** Store the §13.4 JSON Schemas under `tests/fixtures/result_cache/schemas/`.

## File scope (exclusive)
- `src/agent_orchestrator/cache/cli.py` (commands; the skeleton came from T-28J9oR)
- `tests/test_e2e_cli_result_cache_admin.py` (new)
- `tests/fixtures/result_cache/schemas/**` (new)

## Inputs / Outputs
- **Inputs:** T-HjxNQ0 (`CacheAdmin` backend), T-U7ckfd, T-28J9oR (skeleton, `CacheConfig`).
- **Outputs:** an operator CLI for the result cache; the `ao cache stats --json` fields the G0
  protocol reads.

## Acceptance Criteria
All tests are E-7, run through `CliRunner` against a temp workspace populated through the store
API.

1. **`ls`.** Text shows key[:12], last used, created, outputs, bytes, source task/run, cost;
   `--json` validates against `ao.result-cache.ls/v1`; `--limit` and `--sort lru|created|size`
   work; an invalid `--sort` exits 2; an empty cache exits 0.
2. **`stats`.** `--json` validates against `ao.result-cache.stats/v1` and contains the fields the
   G0 protocol reads (`entries`, `bytes.total`, `expired_entries`, `oldest_created_at`,
   `newest_created_at`); `foreign_version_dirs` lists a planted `entries/v2/`; `exists: false`
   with exit 0 when there is no cache directory.
3. **`show`.** Exit 0 for a unique prefix (with blob presence and `components`); exit 1 for an
   unknown or ambiguous prefix (candidates listed); exit 2 for a malformed prefix (`XYZ`, `../x`,
   3 characters, uppercase).
4. **`rm`.** `rm <prefix>` removes exactly one entry (exit 0; `--json` validates against
   `ao.result-cache.rm/v1`); exit 1 for an unknown or ambiguous prefix; exit 2 for a malformed
   prefix. `rm --run` is not an accepted option (Typer usage error).
5. **`prune`.** Counts by reason, bytes before and after; `--older-than 0` expires every entry;
   a negative value exits 2; `--max-bytes` overrides the config; `--dry-run` deletes nothing;
   `--json` validates against `ao.result-cache.prune/v1`.
6. **`clear`.** `--yes` empties the store (exit 0); without `--yes` and with stdin not a TTY it
   exits 1 with "refusing to clear without --yes"; a patched removal failure exits 1; `--json`
   validates against `ao.result-cache.clear/v1`.
7. **`verify`.** Exit 0 when clean (orphan blobs and foreign versions alone still count as
   clean); exit 1 when corruption is found; it deletes nothing; `--repair` is not an accepted
   option; `--json` validates against `ao.result-cache.verify/v1`.
8. **M-15.** An entry whose `source.task_id` contains `\x1b[31m` and `\x07` prints without control
   characters in text mode.
9. **Workspace resolution.** `-w` beats `AO_WORKSPACE_ROOT`, which beats discovery.
10. **Hygiene.** Line coverage of `cache/cli.py` ≥ 85%; ruff (≤ 100 columns) and mypy are clean;
    `pytest -q` has no new failures.

## Test requirements
- `tests/test_e2e_cli_result_cache_admin.py`: AC-1..AC-9, with `jsonschema` against the fixtures.

## Risks
- **Destructive commands on the wrong workspace.** Mitigation: explicit resolution; `clear` needs
  `--yes` when stdin is not a TTY.

## Dependencies
- T-HjxNQ0, T-28J9oR, T-U7ckfd.

## Pseudocode / Algorithm
```text
HLD §8.9 (table, prefix resolution) verbatim.
```

## Schemas / Interface Notes
- **CLI:** HLD §14.2. **JSON:** HLD §13.4.

## Handoff Boundary
- **Upstream:** T-HjxNQ0, T-28J9oR.
- **Downstream:** T-JCOAsq Part 3, T-fXWbqg (G2), T-bdQZW4; the G0 protocol (T-nPMuz4) reads
  `ao cache stats --json`.

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-6tRKml-cache-cli-commands/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: `ao cache` admin commands.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2: `rm`, control-character
  stripping, `foreign_version`, `--older-than` semantics.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 (manager B): `rm --run
  --task` and `verify --repair` deferred (`verify` is read-only); `stats --json` carries the G0
  fields; re-estimated from 20 h to 17 h; Sprint 2.
