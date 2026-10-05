# STATUS

- ID: `T-fXWbqg-cache-review-gates`
- Updated At: `2026-10-05`
- State: `Done`
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

## G1b remediation (fix commit `6ba90ba`; reports `output/E-Rc4Hk8-cross-run-result-cache/review-g1b.md`, `review-g1b-security.md`)

Verdicts as reported: reviewer PASS (0 MUST-FIX, S-1..S-5, N-1..N-7), dev-security PASS (0 MUST-FIX, S-1..S-5, N-1..N-10). The manager marks the G1b gate itself; this section only records what was fixed or deferred. "rev" = reviewer id, "sec" = dev-security id.

| finding | status | fix / regression test (all in `tests/cache/`) |
|---------|--------|----------------------------------------------|
| sec S-2 (`ao status` untrusted `result_cache` block) | Fixed | `report.format_summary_line` is total (not a dict, missing / non-numeric / bool / negative / NaN / inf / oversized values -> no line, never raises, no foreign string is ever formatted); `cli._echo_result_cache_line` strips control chars at the echo; the `ao status` fallback catches `ValueError` (JSON int-digit limit). Tests: `test_report.py::TestSummaryLineIsTotal` (14 hostile blocks incl. the `b1.py` escape PoC), `test_g1b_remediation.py::TestAoStatusWithHostileBlock` (CLI end to end: exit 0, no line, no ESC; digit-limit file; echo strip) |
| sec S-5 (banner trust note) | Fixed | `on`-mode banner ends with ` (agent-writable; avoid for untrusted prompts)` (`_RESULT_CACHE_TRUST_NOTE`); shadow banner unchanged (never serves a hit). `test_cli_result_cache_wiring.py::TestBanner` (literal `TRUST_NOTE`, shadow asserts its absence) |
| sec S-1 (stale restore temp leftovers) | Fixed (non-poisoning); sweep deferred. **SUPERSEDED at G2 (G2-S1): the hashing exemption below was removed, see "G2 remediation"** | Chosen approach: directory-input hashing ignores regular files named `.ao-result-cache-*.tmp` / `.tmp.bak` (`safeio.is_restore_tmp_name`, the one definition; `RESTORE_BACKUP_SUFFIX` moved to constants), so a crash leftover can never change a key. Symlinks / directories with that name and a file declared directly as an input are still hashed; GV-1 and the key schema are unchanged (the golden workspace has no such names; `test_keys_golden.py` green). Why not a restore-side sweep: deleting files from the user's output dirs on a name pattern is destructive (a `.bak` is the prior content of a destination) and needs a liveness / age rule; it is already planned as `ao cache prune` work (G1a SEC-19, T-6tRKml). Accepted residual: an agent that can write an input dir can hide a file from the key by giving it a staging-shaped name (same trust class as the accepted forged-entry residual, sec S-5). Orphans stay on disk until pruned. Tests: `test_hashing.py` (orphans ignored, exact name shape, symlink / dir / direct input still hashed), `test_g1b_remediation.py::TestRestoreTmpNamePredicate`. The stale code comment (claimed a sweep exists) is replaced |
| rev S-1 (HLD 8.7.4 rule) | Fixed | rule is in the `_result_cache_lookup` docstring. Docstring only; no test applies |
| rev S-3 (DRY) | Fixed | `types.clip_text` (one clip, `None` passes) replaces `types._clip`, `records._text`, `coordinator._clip` and the inline slices in the coordinator; `fingerprint.RESOLVE_FAILURES` + `fingerprint.try_resolve` replace the coordinator's copy of the tuple and helper. `test_g1b_remediation.py::TestSharedHelpers` (including "no module keeps a private copy"); the existing coordinator / records tests are unchanged and green |
| rev S-4 (`ttl_days` annotation) | Fixed | `LocalFsCacheStore.__init__` / `for_workspace` take `int` or `None`; the `cast("int", ...)` in `ResultCache.from_settings` is gone. `TestTtlNever` |
| rev N-1 (hit uses `outcome.record`), rev N-3 (pending-token comment) | Fixed (trivial) | `engine.py`: `record = outcome.record` + assert; comment reworded |
| rev S-2 (I-5b parallel store, I-25 engine-level M-16) | Done (T-JCOAsq Part 2, `4d11a69`) | `tests/cache/test_integration_hardening.py::TestStoreAtMaxParallel` and `::TestCoordinatorFailureBoundary` |
| rev S-5 / sec S-4 (dashboard deny-list, M-10 second half) | Deferred | owned by T-bLpoze; hard G2 exit item (do not merge to `main` with the cache switchable on before it lands) |
| sec S-3 (`filter.<x>.clean` executed by the guard-3 probe) | Deferred, accepted residual | needs an in-`.git` write by a task agent plus a stat-dirty tracked file; the feature is double opt-in; an agent with Bash has the same power; the isolation integrator already runs `git status` in agent-touched trees. Hardening options (stat compare via `git ls-files --debug`, skip guard 3 when `.git/config` has `filter.`, empty-tree attribute source) are an architect decision. **Docs refresh T-bdQZW4 must list it** in HLD 7.7 / the authoring guide as a named residual with its precondition (recommend `--no-cache` for untrusted repos; same family as the G1a SEC-03 residual) |
| rev N-2 (R-1b rationale lost from `_reverse_stale_charge`) | Deferred | needs ~13 docstring lines; engine budget is net +105 of +110 |
| rev N-4 (HLD allow-list lists `cache.cli`) | Deferred | already assigned to T-bdQZW4 |
| rev N-5 (round cross-run totals in `usage.py`), N-6 (`el.reason or ""`), N-7 (hook may raise) | Deferred | cosmetic / speculative; G2 hygiene |
| sec N-1..N-10 | Deferred | one hygiene ticket at G2; N-7 / N-9 (guard-3 blind spots, partial-attempt baselines) and N-10 (approval ordering once E-Ag7Pw3 lands) also go to T-bdQZW4 / G2 |

Engine budget (remeasured after the fix): `git diff --numstat ed8b8c3 HEAD -- src/agent_orchestrator/engine.py` = 135 added, 30 removed, net +105 (budget +110); added lines inside existing functions unchanged at 12 (only a new-method docstring line, one assert and a comment rewording were added); no added line over 100 columns.

Verification: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache tests/test_spawn_provenance.py tests/test_nfr2_regression_gate.py tests/bench/test_bench_cache_forced_off.py tests/test_engine_budget.py` 1264 passed; `ruff check src tests` clean; `ruff format --check src tests` clean; `mypy src tests/cache` only the 4 pre-existing `_version.py` errors; I-1 / I-2 goldens unedited and green. Full suite (`.venv/bin/python -m pytest -q -p no:cacheprovider`, 12 min): ~~7480 passed, 13 skipped, 0 failed~~ **CORRECTED at G2 (G2-S6): the correct figure at that point is 6572 passed / 10 skipped** (matches the T-6tRKml record "6572 passed, 10 skipped, 6582 collected"; 7480 was unreproducible, most likely a run that also collected nested worktree copies of `tests/`) (no skip is in `tests/cache`; the brief's last-known figure was 6333 passed / 10 skipped).

## G2 remediation (fix commit: the `fix(cache): G2 remediation ...` commit following `370e9d8`; reports `output/E-Rc4Hk8-cross-run-result-cache/review-g2.md`, `review-g2-security.md`)

Verdicts as reported: reviewer PASS (0 MUST-FIX, S1..S6, N1..N8), dev-security PASS (0 MUST-FIX, G2-S1..S3, N1..N9). The manager marks the G2 gate itself; this section only records what was fixed or deferred. "rev" = reviewer id, "sec" = dev-security id. GV-1 (`6646469e...0319f`) and the key schema are unchanged (`test_keys_golden.py` green, no recapture).

| finding | status | fix / regression test |
|---------|--------|-----------------------|
| sec G2-S1 (restore-tmp name exemption hides a file from the key) | Fixed | the `is_restore_tmp_name` skip in `cache/hashing.py` is deleted (module docstring and `restore.py` comment fixed); `is_restore_tmp_name` stays for the sweep only. Supersedes the G1b S-1 exemption. ADR-0019 D8 addendum. Tests (`tests/cache/test_hashing.py`, flipped): `test_a_file_with_a_restore_temp_name_changes_the_directory_digest` (both names; content change; removal restores the clean key), `test_a_restore_temp_name_nested_deep_is_hashed_too`, `test_a_directory_carrying_a_temp_name_is_walked_as_a_real_input`. Mutation-checked (skip re-inserted -> 4 failures) |
| sec G2-S3 / G1a SEC-15 / rev G2-S1 (sensitive-path list) | Fixed | `cache/constants.py`: components `+ .githooks .circleci .vscode .devcontainer .cursor .idea`; basenames `+ .gitlab-ci.yml Jenkinsfile .travis.yml azure-pipelines.yml bitbucket-pipelines.yml .pre-commit-config.yaml .gitmodules .gitattributes` (case-insensitive via the existing casefold; list is shared by key build, restore, capture and the sweep). ADR-0019 D29 addendum. Tests: `test_safeio.py` (exact-list pin, 30+ sensitive and case-variant paths, look-alikes stay restorable), `test_restore.py::test_ci_hook_and_ide_sinks_are_refused_at_restore`, `test_keys.py::test_sensitive_outputs_are_refused` (extended). No existing test pinned the old exact list. Mutation-checked |
| rev G2-S2 / sec G2-N2 (UI deny-list case-sensitive; NUL -> 500) | Fixed | `ui/files.py::FileBrowser.resolve` only: the cache-root comparison is on casefolded path parts; one guard line raises `PathNotAllowedError` (HTTP 403) on a NUL byte. Tests in `tests/ui/test_result_cache_ui.py` (case variants incl. absolute with a shouted root prefix, siblings not over-matched, NUL on resolve/list_dir/all three HTTP endpoints). Mutation-checked (both hunks). No bundle rebuild (backend only) |
| rev G2-S5 (`--workspace /typo` exits 0) | Fixed | `cache/cli_ops.py::_resolve_workspace`: a workspace that is not an existing directory is `_Fail(..., EXIT_USAGE)` (exit 2, one `ERROR:` line, or the `--json` document with `error`), for every command and for `AO_WORKSPACE_ROOT`; a missing `.orchestrator/cache` under a real workspace is unchanged (empty cache, exit 0). `--workspace` help corrected (`cache/cli.py`). Two existing e2e tests that pinned the old behaviour were updated (missing workspace: exit 2; workspace that is a file: exit 1 -> 2). New tests in `tests/test_e2e_cli_result_cache_admin.py` (all 7 commands text and JSON, `clear --yes` typo, env var, help text). Mutation-checked. Also removed the unused `_WORKSPACE_ENV` (rev G2-N2) |
| sec G2-N1 (`strip_control_chars` survivors) | Fixed | U+061C, U+00AD and U+E0000-U+E007F added to `_CONTROL_CHARS_RE`; tests incl. neighbours that must survive. Mutation-checked |
| rev G2-S4 / sec SEC-11 (provider env names not in the key) | Deferred, accepted residual | no code change (would change GV-1). Recorded as an explicit carry-over in T-bdQZW4 STATUS |
| sec G2-S2 / G1b S-3 (`filter.<x>.clean` run by the guard-3 probe) | Deferred, accepted residual | no code change. Recorded as an explicit carry-over in T-bdQZW4 STATUS |
| rev G2-S3 (HLD 24.2 merge notes deviate from the diff) | Deferred to T-bdQZW4 | explicit carry-over in T-bdQZW4 STATUS |
| rev G2-S6 (G1b "7480 passed / 13 skipped") | Fixed (record) | corrected in the G1b verification line above: 6572 passed / 10 skipped |
| rev G2-N1 (duplicate `--cache` Option), N3 (`SHA256_HEX_RE` / TypedDict), N4 (approval-seam pin test), N5 / sec SEC-10 (`claude --version` stdin/bounds), N6 (`_LS_HEADER` widths), N7 (pre-existing red lint/type CI steps: `output/E-YAAGhk` repro, `_version.py`), N8 (HLD baseline sentence) | Deferred | cosmetic / out of scope / docs; N4 pin test belongs with the E-Ag7Pw3 merge (the one gated-task test cannot exist before that code); N8 goes to the docs refresh |
| sec G2-N3 (CI supply-chain gates, per-module coverage list), N4 (G0 protocol: shadow retention note, `WF_ID` quoting), N5 (restore sweep intermediate-component TOCTOU), N6 (`ao cache prune` takes limits from the CWD config), N7 (sweep misses leftovers after `clear`), N8 (`ResultCacheRecord` string bounds, `reason_detail` host paths), N9 (stale restore.py comment) | N9 Fixed (with G2-S1); the rest Deferred | N3 is a merge-time item (keep T-2wE08U `permissions:`/`pip-audit`, add `cli_ops restore_sweep safeio` to the per-module loop); N4/N7 go to the docs refresh; N5, N6, N8 same-uid or hygiene, one post-merge ticket |

Verification (worktree `agent-a18ce2c08e42a3a5a`): `.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache tests/ui tests/test_spawn_provenance.py tests/test_nfr2_regression_gate.py tests/test_e2e_cli_result_cache.py tests/test_e2e_cli_result_cache_admin.py` 2353 passed, 2 skipped (the two playwright skips); CI coverage step: 1707 passed, package 98.71% (>= 85), keys 100 / store 96 / restore 97 / coordinator 100 (>= 90); `ruff check src tests` and `ruff format --check src tests` clean; `mypy src tests/cache` only the 4 pre-existing `_version.py` errors; I-1 / I-2 goldens unedited and green; `tests/conftest.py` and `tests/test_nfr2_regression_gate.py` untouched; `engine.py` untouched. Full suite (`.venv/bin/python -m pytest -q -p no:cacheprovider`, 698 s): 6903 passed, 10 skipped, 0 failed (last known 6805 / 10 / 0; +98 new tests, zero regressions).

## Comments
- By: developer · Role: developer · Date: 2026-10-05 · Comment: G2 remediation applied (section "G2 remediation" above; fixed: G2-S1/S3 security, rev S2/S5/S6, strip NIT; deferred with rationale listed there; rev S3 (merge notes) and the two accepted residuals (rev S4 provider env, sec S2 git filter) carried to T-bdQZW4). State stays `Draft`; the manager marks the G2 gate. No G2 PASS is recorded here.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: G1b remediation applied in commit `6ba90ba` (section "G1b remediation" above; deferred items with rationale are listed there). State stays `Draft`; the manager marks the G1b gate.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: G1a remediation applied in commit `763375f` (findings table above). State stays `Draft`; the manager re-verifies G1a. SEC-01 MUST-FIX fixed with a TOCTOU-proof budget in the prune itself.
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD §23.5). State stays `Draft`
  (G1a after the core set / G1b after the engine set / G2 final); this file, `TASK.md`, `HANDOFF.md` (when present) and the epic `STATUS.md` rollup
  agree.
- By: manager · Role: agent · Date: 2026-10-05 · Comment: G1a PASS. Reviewer: PASS, 0 MUST-FIX. dev-security: SEC-01 MUST-FIX fixed in `763375f`, delta re-verified PASS, 0 open MUST-FIX; full suite 6081 passed / 10 skipped / 0 failed. Carry to G2: SEC-15 sensitive-path list decision; NIT RV-1. State stays `Draft` until G1b/G2 are complete (mirrors TASK.md).
- By: manager · Role: agent · Date: 2026-10-05 · Comment: G1b PASS. Reviewer 0 MUST-FIX; dev-security 0 MUST-FIX; remediation `6ba90ba`. Open for G2: dashboard deny-list (T-bLpoze), approval-ordering check, I-5b/I-25/store-at-max_parallel>1 tests (T-JCOAsq Part 2), prune-side tmp sweep (T-6tRKml). State stays `Draft` until G2 (mirrors TASK.md).
- By: manager · Role: agent · Date: 2026-10-05 · Comment: G2 PASS. Reviewer 0 MUST-FIX; dev-security 0 MUST-FIX; remediation `2fa650d`; full suite 6903 passed / 10 skipped / 0 failed. All three gates (G1a, G1b, G2) PASS -> T-fXWbqg Done.
