# STATUS

- ID: `T-fXWbqg-cache-review-gates`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `reviewer` + `dev-security`

## This update
- Rev 3 ticket: gate scopes follow the manager's sequential plan; exit criteria name the Rev 3
  tests and the honest engine budget. Estimate unchanged (24 h).

## Evidence
- None yet (not started). Design evidence: HLD Rev 3 and ADR-0019 Rev 3.

## Risks / Blockers
- No blockers.
- Risk: fix-up loops; S3 slack absorbs them.

## Next actions
1. G1a when the core set is done.
2. G1b when the engine set is done.
3. G2 after T-JCOAsq Part 3.

## G1a remediation (fix commit `763375f`; reports `output/E-Rc4Hk8-cross-run-result-cache/review-g1a.md`, `review-g1a-security.md`)

Verdicts as reported: reviewer PASS (0 MUST-FIX, S-1..S-11), dev-security FAIL (SEC-01 MUST-FIX).
G1a: PASS (manager, 2026-10-05) after independent dev-security re-verification; see the Comments.

| finding | status | fix / regression test (all in `tests/cache/`) |
|---------|--------|----------------------------------------------|
| SEC-01 (MUST-FIX) | Fixed | per-phase `_Budget` over `entries/**` any version/depth, in the size scan AND the prune: `test_store_hardening.py::test_inline_defers_on_a_planted_foreign_version_tree_without_reading_it`, `..._nested_under_a_v1_shard`, `..._on_the_bytes_of_a_planted_sparse_tree`, `..._on_the_number_of_directory_entries_visited`, `test_the_planted_sparse_tree_defers_fast_with_the_real_limits` (wall < 1 s), `test_a_tree_planted_after_the_size_scan_is_bounded_by_the_prune_itself` (TOCTOU), `test_the_explicit_prune_stays_unbounded` |
| SEC-02, S-1, S-2 | Fixed | `INLINE_PRUNE_MAX_BLOBS`, `INLINE_PRUNE_MAX_WALK_ITEMS`; FileNotFoundError tolerated: `test_inline_defers_above_the_blob_count`, `test_inline_at_the_blob_limit_is_not_deferred`, `test_a_blob_vanishing_during_the_inline_scan_is_tolerated` |
| SEC-03 | Fixed | `core.fsmonitor=false`, `core.untrackedCache=false` via `GIT_CONFIG_COUNT` (git >= 2.31; older git ignores it): `test_repo_state_hardening.py::test_a_hostile_core_fsmonitor_in_the_workspace_repo_is_never_executed` (fails without the fix) and the env unit tests. Residual (not fixable by config key): `filter.<x>.clean` / `diff.<x>.textconv` reached through in-repo `.gitattributes`; recommend `--no-cache` for untrusted repos (HLD 7.7 residual row, docs refresh T-bdQZW4) |
| SEC-04 (+ N-12) | Fixed | `git_read_env` drops `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_COMMON_DIR`, object/ceiling/namespace/config-injection vars: `test_an_inherited_git_dir_cannot_redirect_the_head_read`, `test_the_runner_never_sees_an_inherited_repo_selector` |
| SEC-05 | Fixed | `capture_outputs` requires `realpath(path) == path`: `test_restore.py::test_capture_through_a_swapped_parent_directory_is_refused` |
| SEC-06 (+ N-8) | Fixed | `casefold()` match: `test_safeio.py::test_sensitive_match_is_case_insensitive`, `test_restore.py::test_case_variants_of_sensitive_paths_are_refused`, `test_keys.py::test_a_case_variant_of_a_sensitive_path_is_refused` (the old "lookalike is not sensitive" tests were inverted deliberately; ADR-0019 D29 addendum) |
| SEC-07 | Fixed, residual documented | mode via `fchmod` on the open fd after hash verification (never by path); existing destinations hard-linked before commit and rolled back if a later rename fails: `test_restore.py::test_the_mode_is_applied_to_the_descriptor_never_to_a_path`, `test_a_failing_commit_rolls_back_every_rename_already_done`, `test_a_rollback_removes_a_destination_that_did_not_exist_before`, `test_a_successful_restore_leaves_no_backup_litter`. Residual: created parent directories stay; without hard-link support that one file is not rolled back (ADR-0019 D20 addendum) |
| SEC-08 (+ S-7) | Fixed | `put_blob` dedupes only onto a regular file of the right size, otherwise replaces it (planted directory moved to `trash-*`); `delete_*` on a directory is `unsafe_path`: `test_put_blob_replaces_a_planted_symlink_and_never_touches_its_target`, `..._a_wrong_size_regular_file`, `..._moves_a_planted_directory_aside_and_installs_the_blob`, `test_deleting_a_directory_at_a_blob_or_entry_slot_is_unsafe_path_not_a_raw_oserror`. Same-size forged content is not re-hashed at dedupe (restore hash check self-heals) |
| SEC-09 | Fixed | `read_blob`: only `NotRegularFileError` is `blob_corrupt`; other OSError is `CacheError(store_error)`; restore maps it to `RestoreMiss(store_error, evict=False)`; verify reports `unreadable`: `test_a_transient_open_error_is_a_store_error_not_corruption`, `test_an_irregular_blob_is_still_corruption`, `test_restore.py::test_a_transient_blob_read_failure_is_a_non_evicting_miss`, `test_verify_reports_an_unreadable_blob_instead_of_calling_it_corrupt` |
| S-3 | Fixed | unreadable entry file is an `unreadable` anomaly (verify fails, never deleted): `test_an_unreadable_entry_does_not_abort_verify_stats_or_prune` |
| S-4 | Fixed | mark phase returns `_Marks(complete=False)`; incomplete protects every blob: `test_the_sweep_fails_closed_when_an_entry_file_cannot_be_read`, `test_a_too_large_foreign_file_also_fails_the_sweep_closed` |
| S-5 | Fixed (trivial) | fresh lstat before each sweep deletion: `test_a_blob_refreshed_between_the_scan_and_the_sweep_is_not_deleted` |
| S-9 | Partly fixed (trivial) | `yaml`, `dill`, `cloudpickle`, `jsonpickle` added to `BANNED_MODULES` + negative self-tests. Deferred: attribute-call checks for `Path.open`/`read_bytes`/`io.open` (needs a design for allowed call sites) |
| S-10 | Fixed (trivial) | `test_g1a_remediation_misc.py::test_the_state_record_bounds_equal_the_cache_entry_bounds`, `test_the_key_pattern_in_models_matches_the_cache_hex_pattern`. Deferred: building `HexStr` from `SHA256_HEX_RE.pattern` |
| S-11 | Fixed (trivial) | `keys.py` lstat of a prior output: FileNotFoundError is the absent prior, other OSError is `input_unreadable`: `test_a_prior_output_removed_between_the_checks_is_the_absent_prior`, `test_an_unreadable_prior_output_is_uncacheable_not_a_raw_oserror` |

Deferred, no code (rationale; batch into the G2 hygiene ticket unless noted):
- S-6 (inline thrash while orphans sit in the 1 h grace): bounded by the new budget and self-limiting; needs a prune-returned live total in `PruneReport` (contract change), revisit at G2.
- S-7: fixed with SEC-08 (see above). S-8 / SEC-15 (more sensitive names: `.gitlab-ci.yml`, `Jenkinsfile`, `.circleci/`, ...): needs an ADR-0019 D29 list decision by the architect; defence in depth only (destinations come from the spec).
- SEC-10 (`claude --version` stdin/env/output bound): the binary already runs at dispatch; low value, G2 hygiene.
- SEC-11 (provider/endpoint env names in the key allowlist): changes key composition and the golden vector GV-1; needs an architect decision.
- SEC-12: same as S-9 remainder. SEC-13 (strict pydantic models): behaviour change across the entry corpus, G2 hygiene.
- SEC-14 (bidi/zero-width stripping in `strip_control_chars`): CLI-only display, lands with T-6tRKml.
- SEC-16 / N-4 (stored mode 0o000/0o200 restored unreadable): needs a decision on `mode | 0o600`; same-uid only.
- SEC-17 (`ClearReport` counts through a symlinked `blobs/`): reporting only.
- SEC-18 (forged future `created_at` immortal): same-uid poisoning, accepted residual.
- SEC-19 (restore staging temp files after SIGKILL): the `.bak` names share the prefix; a prefix sweep belongs to `ao cache prune` (T-6tRKml).
- SEC-20 (root mode 0o755 accepted; plain ValueError on bad key): HLD M-10 wording vs behaviour, architect decision.
- SEC-21 (hardlinked entry/blob mtime): accepted, no content effect.
- Reviewer NITs N-1 (is_within helper), N-2 (magic literals), N-3 (error base class), N-5 (non-evicting entry_too_large), N-6 (cli skeleton, intended staging), N-7 (HLD wording: `cache.cli` is also loaded), N-9 (`ResultCacheRecord` string bounds), N-10 (HLD sentence on final-component links), N-11 (docstring precondition): cosmetic or doc-only, G2 hygiene.
- HLD text still says exact-case sensitive paths and a two-bound inline limit; the ADR-0019 addenda are the authority until the docs refresh (T-bdQZW4).

Verification run for this remediation (worktree `agent-a18ce2c08e42a3a5a`): `.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache tests/test_spawn_provenance.py` 938 passed, 0 failed (the reviewers' 870-test `tests/cache` baseline, plus the new regression tests and `tests/test_spawn_provenance.py`); `.venv/bin/ruff check src tests` clean; `.venv/bin/ruff format --check src tests` clean; `.venv/bin/mypy src tests/cache` only the pre-existing `_version.py` errors. Full suite not run (next gate).

## Comments
- By: developer · Role: developer · Date: 2026-10-05 · Comment: G1a remediation applied in commit `763375f` (findings table above). State stays `Draft`; the manager re-verifies G1a. SEC-01 MUST-FIX fixed with a TOCTOU-proof budget in the prune itself.
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD §23.5). State stays `Draft`
  (G1a after the core set / G1b after the engine set / G2 final); this file, `TASK.md`, `HANDOFF.md` (when present) and the epic `STATUS.md` rollup
  agree.
- By: manager · Role: agent · Date: 2026-10-05 · Comment: G1a PASS. Reviewer: PASS, 0 MUST-FIX. dev-security: SEC-01 MUST-FIX fixed in `763375f`, delta re-verified PASS, 0 open MUST-FIX; full suite 6081 passed / 10 skipped / 0 failed. Carry to G2: SEC-15 sensitive-path list decision; NIT RV-1. State stays `Draft` until G1b/G2 are complete (mirrors TASK.md).
