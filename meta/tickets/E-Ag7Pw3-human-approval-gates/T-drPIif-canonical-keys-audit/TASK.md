# TASK: T-drPIif-canonical-keys-audit

## Metadata
- Task ID: `T-drPIif-canonical-keys-audit`
- Epic ID: `E-Ag7Pw3-human-approval-gates`
- Owner: developer
- Created: 2026-10-05 (split from rev-1 `T-csusci-signing-keys-hashing-authz`, Gate 1 R-06)
- Last Updated: 2026-10-05 (rev 3)
- Status: Draft
- Estimate: 3 days (rev 2: 2.5 d; rev 3 adds the gated marker module). Merge order: `canonical.py` +
  `keys.py` by the end of the first day (`T-1MgGb4`'s records need them, day 2); `audit.py` +
  `accounting.py` by day 3.5 (`T-pfJiXw` starts then); `gated_marker.py` last, by day 4 (needed by
  `T-vwIpSw` from day 6.5) — so the extra half day stays off the critical chain

## Requirements Mapping
- Requirement IDs: FR-5, FR-6 (gated marker I/O), FR-11 (audit), FR-12 (wait accounting, pure part), NFR-3,
  NFR-4, NFR-6, NFR-7
- Design: HLD §9.3 (canonical, bounded parser, HMAC, keys, denylist, no tracebacks), §9.3.4 (gated
  marker, rev 3), §9.7 (audit), §9.2.5 (wait accounting), §7.4 TM-4/17/18/28/31/36, §26 rows 19b, 28, 35;
  Gate 1 S-04, S-05, S-08, CUT 3, R-07, T-16; Gate 2 S-11

## Description
The foundation half of the decision channel: byte-exact signing input, the one bounded strict parser, the
key with its custody rules, the workspace-config denylist, the audit log and the pure wait accounting. No
engine, CLI-command or UI wiring.

- `approvals/canonical.py`: `canonical_bytes(obj)` and `parse_strict(raw)` exactly per HLD §9.3.2 — depth
  pre-scan (`MAX_JSON_DEPTH`), `parse_int` digit cap (`MAX_JSON_INT_DIGITS`), duplicate keys / NaN / floats
  rejected, `ValueError` and `RecursionError` converted to `RecordMalformed` with a `RefusalDetail`.
- `approvals/keys.py`: `ApprovalKey` (`mac`, `verify`, `key_id`, secret in a `repr=False` field, never in
  exceptions), `ApprovalKeyStore` protocol, `FileKeyStore(key_dir=None, environ=os.environ, geteuid=os.geteuid)`
  with `load` / `load_or_create` per §9.3.3: home from `pwd.getpwuid(os.geteuid()).pw_dir` (never `$HOME`,
  `~`, `expanduser`, `Path.home()`), overrides only from the given real environment, O_EXCL temp +
  `link()` only (no fallback, no read retries; `ApprovalKeyUnavailable` with the "no hard links" message),
  workspace-containment message that names the `$HOME`-as-workspace case; and `gated_marker_path(run_id)`
  (pure path computation, `None` when the key directory cannot be resolved, never raises; HLD §9.3.3).
- `xdg.py`: new `resolve_config_dir(override_env, xdg_subdir, default_subdir, *, environ, home)` mirroring
  `resolve_state_dir`.
- `project_config.py::apply_project_config_env`: never export a key in
  `approvals.models.CONFIG_ENV_DENYLIST` (`AO_APPROVAL_KEY_DIR`, `XDG_CONFIG_HOME`, `AO_IN_AGENT`, `HOME`,
  `USER`, `LOGNAME`); log `config.env_denied` (key name only).
- `cli.py`: the root app becomes `typer.Typer(..., pretty_exceptions_show_locals=False)` (one line, §26
  row 19b).
- `approvals/audit.py`: `AuditLog.append` and `read_audit_tail` per §9.7 (lines parsed with `parse_strict`).
- `approvals/accounting.py`: `approval_wait_spans`, `compute_run_approval_wait_seconds`,
  `request_wait_seconds`, `pending_approval_rows` per §9.2.4/§9.2.5 (union of waits inside the wall-time
  window).
- `approvals/gated_marker.py` (rev 3, Gate 2 S-11, HLD §9.3.4): `marker_exists`, `read_marker` (custody
  checks reused from `keys.py`, `O_NOFOLLOW`, size cap, `parse_strict`, `GatedMarker` strict model,
  `key_id`, MAC kind `gated`, `run_id`), `write_marker` (engine-only; `gated/` created 0700; temp file
  `O_CREAT|O_EXCL|O_NOFOLLOW` 0600, fsync, `os.replace`, directory fsync best effort; `GatedMarkerUnwritable`
  on any `OSError`). No policy logic here: the comparison is `policy.check_marker` (`T-1MgGb4`).

Files — new: `approvals/{canonical,keys,audit,accounting,gated_marker}.py`; tests
`tests/approvals/test_{canonical_hmac,keys,gated_marker,config_env_denylist,audit,wait_accounting}.py`.
Shared (HLD §26): `xdg.py` (row 28), `project_config.py` (row 35), `cli.py` (row 19b, one line).
**Exclusive files during stage B** (HLD §22.3): exactly these; do not touch `approvals/models.py`
(owned by `T-AGO2L6`) — ask for any missing constant there.

## Acceptance Criteria
1. Canonical/HMAC: three pinned test vectors (payload → canonical bytes → HMAC hex with a fixed test key)
   match exactly; floats, NaN/Infinity, duplicate keys, non-object roots and non-UTF-8 raise; a record
   re-serialized with different whitespace/key order still verifies; a MAC of kind `request` never verifies
   as `decision`; a non-64-hex `sig` is rejected before comparison; a payload that cannot be canonicalized
   never verifies.
2. Parser bombs (Gate 1 S-04): a bracket bomb deeper than `MAX_JSON_DEPTH` (also one deeper than the
   interpreter's recursion limit) and a 5,000-digit integer each raise `RecordMalformed`
   (`depth_exceeded` / `number_out_of_range`), never `RecursionError` or a bare `ValueError`; brackets inside
   strings are accepted (`test_parse_strict_depth_bomb_malformed`, `::test_parse_strict_big_int_malformed`,
   `::test_parse_strict_brackets_inside_strings_ok`).
3. Keys: 8 concurrent processes calling `load_or_create` on an empty dir produce exactly one key file and
   all return the same `key_id`; a reader never observes a partial key; modes 0700/0600. Refusals (each
   naming the path and the fix): key dir inside the workspace (also via a symlinked path, and the
   `$HOME`-as-workspace case with its hint), any `0o077` bit, symlinked dir or file, foreign owner (via the
   `geteuid` seam), size ≠ 32; `os.link` forced to raise `EPERM` → `ApprovalKeyUnavailable` with the
   "no hard links" message (no fallback). `load()` never creates.
4. Home and overrides (Gate 1 S-08): with `HOME=/somewhere/else` in the process env and no overrides, the
   resolved key dir is under `pwd`'s `pw_dir` (`test_default_home_ignores_HOME_env`); `XDG_CONFIG_HOME` and
   `AO_APPROVAL_KEY_DIR` are honoured only from the environment passed in (`test_xdg_from_real_env_only`).
5. No key material anywhere (Gate 1 S-05): `repr(key)`, `str(exc)` and captured logs never contain the
   secret bytes or hex (`test_key_never_in_env_argv_or_logs`); `test_cli_never_shows_locals` asserts
   `cli.app.pretty_exceptions_show_locals is False` and that a forced exception inside a command holding a
   key prints no secret hex.
6. Config-env denylist: a workspace `.ao/config.yaml` with `env:` setting all six denied keys plus
   `SOME_OTHER: "x"` leaves the six unset in `os.environ` when they were unset before (one
   `config.env_denied` warning each, value never logged) and still exports `SOME_OTHER`; an explicit
   real-env `AO_APPROVAL_KEY_DIR` keeps working; the pre-existing `tests/test_project_config.py` passes
   unedited.
7. Audit: every line ≤ 4096 bytes and verifies with the key; an edited line reads `verified: False`; a
   `"mac": null` line reads `verified: None`; a 10 MB audit log is tailed within 256 KiB / 200 lines; a
   symlink at `audit.jsonl` makes `append` return False without writing through it; a depth-bomb line is
   counted `unparseable` (`test_depth_bomb_line_unparseable`).
8. Wait accounting (Gate 1 R-07): `test_union_of_parallel_gate_waits` (two overlapping gates count once),
   `test_wait_never_exceeds_wall` (property test over generated states: `0 <= wait <= _wall_seconds`),
   `test_closed_waits_bounded`, unparseable timestamps drop the span without raising; pending rows match
   §9.2.4 exactly.
9. Gated marker (Gate 2 S-11): `test_write_then_read_roundtrip`; `test_tampered_marker_invalid` (edited
   field, bad MAC, wrong `run_id`, oversize, depth bomb); `test_symlinked_marker_refused` (file and `gated/`
   directory); `test_other_key_marker_invalid` (`key_id` mismatch); `test_run_id_pattern_enforced` (a
   `run_id` with `/` or `..` never becomes a path); `test_only_temp_plus_replace_writes` (no partial marker
   is ever visible; mode 0600; `gated/` 0700); `marker_exists(None)` and an unresolvable key directory
   return False without raising.
10. Line coverage ≥ 90% for each new module; `ruff`/`mypy` clean on changed modules; targeted tests green
   (the full suite runs at the manager's stage-B checkpoint).
11. A `reviewer`-agent review is recorded in STATUS.md.

## Risks
- The process-based concurrency test must be bounded (timeout) and clean up.
- `pwd` lookups in containers without a passwd entry: covered by the A-5 error path (no fallback to `$HOME`).

## Dependencies
- `T-AGO2L6` (`approvals/models.py`, `approvals/errors.py` merged at the end of its day 1).

## Pseudocode / Algorithm
```text
HLD §9.3.2 (canonical_bytes, parse_strict), §9.3.3 (resolution order, check_key_dir, read_key, load,
load_or_create with link() only), §9.7 (audit), §9.2.5 (approval_wait_spans, union inside [started_at,
updated_at]).
```

## Schemas / Interface Notes
- Interfaces: HLD §9.3.2, §9.3.3, §9.7, §9.2.5. Constants: HLD §9 table (`MAX_JSON_DEPTH`,
  `MAX_JSON_INT_DIGITS`, `CONFIG_ENV_DENYLIST`, `MAX_CLOSED_WAITS`, …).
- Events: `config.env_denied`; audit line schema §9.7.

## Verification
```
WT=/usr/avadhoot/mounted/agent-orchestrator/.claude/worktrees/agent-a513b4d78ac3b20bb
PY=/usr/avadhoot/mounted/agent-orchestrator/.venv/bin/python
cd $WT && $PY -m pytest -o pythonpath=src -p no:cacheprovider -q tests/approvals/test_canonical_hmac.py tests/approvals/test_keys.py tests/approvals/test_gated_marker.py tests/approvals/test_config_env_denylist.py tests/approvals/test_audit.py tests/approvals/test_wait_accounting.py tests/test_project_config.py
cd $WT && $PY -m pytest -o pythonpath=src -p no:cacheprovider -q --cov=agent_orchestrator.approvals --cov-report=term-missing tests/approvals
cd $WT && $PY -m ruff check src/agent_orchestrator/approvals src/agent_orchestrator/xdg.py src/agent_orchestrator/project_config.py src/agent_orchestrator/cli.py tests/approvals && $PY -m mypy src/agent_orchestrator/approvals src/agent_orchestrator/xdg.py src/agent_orchestrator/project_config.py
```

## Handoff Boundary
- Upstream: `T-AGO2L6`.
- Downstream: `T-1MgGb4` (records use `canonical`/`keys`), `T-pfJiXw` (store/signer/views use keys, audit,
  accounting), `T-ZPGoSN` (surfaces use accounting), `T-vwIpSw` (engine uses keys, audit and the marker
  writer), `T-otHPGB` (marker reader).

## Artifacts
- Code as listed; coverage numbers in STATUS.md.
