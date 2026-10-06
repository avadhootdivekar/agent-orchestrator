# HLD + LLD — Cross-run result cache (E-Rc4Hk8)

- **Status:** **Implemented** (Rev 4, reconciled with the code by `T-bdQZW4`, 2026-10-05). All 20
  tasks are Done and review gates G1a, G1b and G2 closed PASS. **Where the shipped code differs
  from the design below, §0 wins: read it first.** The design body (§0A onward) is the Rev 3
  design as hardened by the gates; the statements that the implementation or an ADR-0019 addendum
  superseded have been corrected in place and are flagged "as built".
  - **Rev 2** folded in the Phase-4 consultation: `developer`, `reviewer`, `tester`,
    `dev-security` and `dev-critic` reviewed Rev 1 against the code at `bb6d8a0` (§23.3–§23.4).
  - **Rev 3** applies an independent early-gate review of the committed design (`94dac52`,
    verdict GO-WITH-FIXES) and the manager's scope decisions (§23.5).
  - **Rev 4** is the as-built reconciliation: §0, plus corrections to §7.6, §7.7, §8.x, §16,
    §17, §18, §22.5, §23 and §24.2 where the code or a gate remediation differs.
  - The design passed the Execution Readiness Gate (§21).
  - The value gate G0 (§22.5) is a post-merge follow-up owned by the parent or operator. The
    protocol shipped (`docs-md/result-cache-g0-protocol.md`); **execution is post-merge** and was
    not run in this epic.
- **Epic:** [`E-Rc4Hk8-cross-run-result-cache`](../meta/tickets/E-Rc4Hk8-cross-run-result-cache/EPIC.md)
- **ADR:** [`ADR-0019`](adr/ADR-0019-cross-run-result-cache.md)
- **Date:** 2026-10-04 (Rev 1) / 2026-10-05 (Rev 2, Rev 3, Rev 4) · **Author:** `architect`
  (design), `developer` (as-built reconciliation, `T-bdQZW4`) · **Base:** `main` @ `bb6d8a0`;
  implemented on branch `worktree-agent-a18ce2c08e42a3a5a`, merge base `0b980e3`
- **Related:**
  - ADR-0003 / ADR-0006: settings precedence, and fill-in vs. kill switch.
  - ADR-0007: the main-thread engine core.
  - ADR-0013: isolation.
  - ADR-0015: the Claude *prompt*-cache scope. This is a different feature; see the terminology box
    below.
  - ADR-0017: RunState-level maps, and validating a sha before using it in a path.
  - E-9h3m7k: cumulative usage.
  - Sibling epics E-Ag7Pw3 (approval gates) and E-Da5Tn9 (dashboard auth). See the merge notes in
    §24.2.

> **Terminology: "result cache" vs "prompt cache".** This epic adds a **result cache**: it reuses
> the *declared output files* of a previous, identical, successful task instead of dispatching the
> agent again. It is unrelated to Anthropic **prompt caching**, which covers
> `cache_creation_input_tokens` / `cache_read_input_tokens`, `reporting.CacheEffectiveness`, the
> cache-hit rate in `ao report-timing`, and ADR-0015.
>
> Naming rules:
> - User-facing text says "result cache".
> - Python names use `result_cache` / `ResultCache`.
> - The CLI group is `ao cache`, and its help text says "result cache".
> - The `cache.*` log-event namespace belongs to the result cache.
> - Any user-facing string that could be read either way must say "result cache".

---

## 0. Implementation outcome and deviations (post-implementation, `T-bdQZW4`)

Reconciled against the code at branch `worktree-agent-a18ce2c08e42a3a5a` (HEAD `7a28289`, merge
base `0b980e3`), after gates G1a, G1b and G2 closed PASS. Every claim here was grep- or
run-checked; the commands are listed in `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-bdQZW4-cache-docs-refresh/STATUS.md`.
Where a gate remediation or an ADR-0019 addendum superseded a design statement, the statement
elsewhere in this document has been corrected and carries an "as built" marker; this section
lists every such place.

### 0.1 What shipped (as designed)

- **Package `agent_orchestrator.cache`**: 17 modules plus an import-free `__init__.py`, 5 472
  lines: `constants`, `safeio`, `types`, `settings`, `hashing`, `repo_state`, `fingerprint`, `keys`,
  `eligibility`, `store`, `restore`, `records`, `coordinator`, `report`, `cli`, and (not in the
  Rev 3 layout) `cli_ops` and `restore_sweep` (DV-17). Nothing in the package imports `engine`,
  `runstate`, `cli` or `ui`; `executors/claude_cli.py` and `models.py` import nothing from it.
- **Double opt-in, default off.** Operator mode: `--cache` / `--no-cache` > `AO_CACHE`
  (`1|true|yes|on`, `0|false|no|off`, `shadow`; anything else, including `refresh`, is off plus one
  warning) > `.ao/config.yaml cache.enabled` + `cache.mode` > off. Author policy:
  `task.cache` > `defaults.cache` > `DEFAULT_TASK_CACHE_POLICY` (**False**, the one flip point;
  test U-S4 pins it). An injected task's `true` is ignored. `cli._build_result_cache` is the one
  helper for `ao run` and `ao resume`.
- **Key schema v1** (`KEY_SCHEMA_VERSION = 1`), argv and executor fingerprint included, task id
  excluded, prior output content included, repo HEADs included by default. GV-1 re-verified (§0.5).
- **Engine seams.** `Orchestrator(..., result_cache=None)`, `_result_cache_lookup` (before the
  budget gate; the engine settles the hit itself and returns `DispatchPrep(signal="skipped")`),
  `_result_cache_store` (top of the `succeeded` settle branch), and `_reverse_stale_charge` (the
  existing stale-charge block moved verbatim, shared by the budget gate and the hit path).
  `git diff --numstat 0b980e3..HEAD -- src/agent_orchestrator/engine.py`: 135 added, 30 removed,
  **net +105** (budget +110); 12 added lines inside existing functions (budget 12); `engine.py`
  contains neither `open(` nor `.read(`, and imports cache modules only under `TYPE_CHECKING`
  (`engine.py:118`) or lazily inside `_result_cache_lookup` (`engine.py:1498`).
- **Eligibility, store, restore, coordinator** as §8.3 to §8.6, with the hardening in §0.3.
- **Surfaces.** `status.json` (`tasks[].result_cache`, top-level `result_cache`), the run-summary
  line, `ao report-usage` text and `--json` (`result_cache` object), `ao report-outcomes`
  (`settle_reason: "cached"`), the `cache.*` events, dashboard payload keys, the "cached" tag and the
  "Result cache" tile, `ao cache ls|stats|show|rm|prune|clear|verify` (each `--json`).
- **Bench forced off**: `bench/subjects.py` passes `--no-cache` and `AO_CACHE=0`
  (`_AO_NO_CACHE_FLAG`, `_AO_CACHE_OFF_VALUE`).
- **CI**: one additive step "Result cache tests + coverage (E-Rc4Hk8)" in `.github/workflows/ci.yml`.
- **Tests and measured outcomes.**
  - Full suite at the last gate (G2): **6903 passed, 10 skipped, 0 failed**. Baseline at design time
    was 5041 passed / 8 skipped / 2 known bench failures; the two bench failures no longer occur.
  - The CI step set (`tests/cache`, `tests/test_e2e_cli_result_cache.py`,
    `tests/test_e2e_cli_result_cache_admin.py`): 1 707 tests; package coverage **98.71%**; `keys`
    100%, `store` 96%, `restore` 97%, `coordinator` 100% (floors 85% and 90%).
  - Other result-cache tests outside that set: `tests/ui/test_result_cache_ui.py` (32),
    `tests/bench/test_bench_cache_forced_off.py` (7), `tests/test_claude_cli_argv_builder.py`,
    vitest `run-detail-result-cache.test.tsx` (10).
  - NFR-1 proof: I-1 (a working `find_spec` poison, ten cache-off workflows, subprocess) and I-2
    (byte-identical `status.json` and stdout against goldens captured at base `bb6d8a0`, serial and
    `max_parallel=3`) in `tests/cache/test_noop_proof.py`; E-2 in `tests/test_e2e_cli_result_cache.py`.

### 0.2 Resolved assumptions and open questions

- **G0 (A-11, D34, OQ-6): protocol shipped; execution is post-merge.** `docs-md/result-cache-g0-protocol.md`
  (T-nPMuz4, guarded by `tests/cache/test_g0_protocol_doc.py`), the measurement fields and the smoke
  evidence `output/E-Rc4Hk8-cross-run-result-cache/g0-protocol-smoke.md` shipped. **G0 was not run**: it needs multi-day
  shadow-mode runs of a real consumer workflow with the operator's consent. The decision rule's
  thresholds (§22.5) remain placeholders for the parent to confirm (OQ-6). No claim of a value result
  is made anywhere in the docs.
- **A-9 (env allowlist): checked, list unchanged, residual accepted.** `fingerprint.CLAUDE_FINGERPRINT_ENV_VARS`
  has 9 names: the six model and output-limit variables the CLI documents (checked against the
  published environment-variable page, T-uoYW6b) plus `CLAUDE_CODE_SUBAGENT_MODEL`,
  `MAX_THINKING_TOKENS` and `CLAUDE_CONFIG_DIR`, which the page summary did not list and which are
  kept because an unused name costs at most a false miss. The provider and
  endpoint variables are **not** in it (accepted residual R-A1, §0.4).
- **A-12 (nested workspace): as designed.** `find_git_toplevel` stops at the workspace root; the
  banner warns through `rc.warnings` (U-CO20). Accepted residual R-A5.
- **OQ-1** (`ao validate` warning): not built; the reason shows in `status.json`. **OQ-2**: `ao prune`
  does not touch the result cache; `ao cache prune` does. **OQ-3**: `include_repo_heads` stays
  config-only. **OQ-4**: still open: E-Ag7Pw3 is not in this tree, so the approval-ordering rule is
  recorded at the seam comment and in §24.2 and has no test (N4, §0.6). **OQ-5**: not revisited; the design default stands (root-relative resolution, a missing path makes the task uncacheable).
  **OQ-7**: `result_cache` kept as the container name. **OQ-8**: `DEFAULT_TASK_CACHE_POLICY` stays
  **False**; flipping it is one constant plus an ADR-0019 addendum.

### 0.3 Deviations from this design

IDs are DV-n. "Gate" names the review finding that caused the change; "T-xxx" the task that recorded
it in its `HANDOFF.md` or `STATUS.md`.

| # | Design said | Shipped | Why / status |
|---|---|---|---|
| DV-1 | §8.2.1: `OutputRecord.kind = KIND_FILE`. `types` imports `safeio`. | `kind: Literal["file"] = "file"`; `types.py` does not import `safeio`. | mypy cannot narrow a plain `str` constant to a `Literal`, and `constants.py` may import only `re`; a test pins `KIND_FILE == "file"`. Nothing in `types` uses `safeio` (T-FJH6LI). |
| DV-2 | §8.1.4: `mode: Literal["on","shadow"] = MODE_ON`. | The default is the literal `"on"`; `settings.py` reads constants through the module (`constants.X`). | Same Literal reason; a test pins `"on" == constants.MODE_ON`. The module access makes test U-S4's monkeypatch of the flip point effective (a name import would capture the old value) (T-28J9oR). |
| DV-3 | §8.1.4 template: `mode: on`. | `mode: "on"` **quoted**, with a comment. | Under YAML 1.1 a bare `on` loads as boolean `true`, so the uncommented template failed `CacheConfig` validation. A user who writes bare `mode: on` gets a loud `ConfigError` (fail-closed). §8.1.4 corrected (T-28J9oR). |
| DV-4 | §8.2.4: child git environment `{GIT_OPTIONAL_LOCKS: "0"}`. | `repo_state.git_read_env()`: scrubs repository-selecting and config-injecting variables (`GIT_SCRUBBED_ENV_VARS`, `GIT_CONFIG_KEY_*`/`GIT_CONFIG_VALUE_*`), keeps `GIT_OPTIONAL_LOCKS=0`, injects `core.fsmonitor=false` and `core.untrackedCache=false` through `GIT_CONFIG_COUNT` (git >= 2.31). | Gate G1a SEC-03 (a planted `core.fsmonitor` ran code on `git status`), SEC-04 (an inherited `GIT_DIR` redirected the HEAD read). Residual `filter.<x>.clean`: R-A2. The HEAD memo is per-toplevel within one `read()`, never across calls; `RepoHeadReader` and `WorktreeProbe` share one private `_GitReaders` base (T-8tr1H4). |
| DV-5 | §8.2.6: refusals use the listed reasons. | `summary_from_doc` over-bound summary maps to `UncacheableError(key_encoding, "summary_out_of_bounds")`; an empty `command_template` maps to `executor_fingerprint_unavailable`; a prior-output `lstat` `OSError` other than `FileNotFoundError` maps to `input_unreadable`. | A `ValidationError` or `IndexError` would otherwise escape `build_cache_key`; gate G1a S-11 for the last (T-uoYW6b). |
| DV-6 | D29 / §7.6 / §8.1.5: sensitive match is exact-case; list of 7 components and 5 basenames. | **Case-insensitive** (`casefold()`), so `docs/claude.md` and `.GIT/hooks` are sensitive. Components also `.githooks .circleci .vscode .devcontainer .cursor .idea`; basenames also `.gitlab-ci.yml Jenkinsfile .travis.yml azure-pipelines.yml bitbucket-pipelines.yml .pre-commit-config.yaml .gitmodules .gitattributes`. | G1a SEC-06 (case) and G1a SEC-15 / G2-S3 (list). ADR-0019 D29 addenda. The key schema and GV-1 do not read the lists. §7.6 D29, §8.1.5 and §7.7 M-14 corrected. |
| DV-7 | D8: directory digest skips `.git`, `<ws>/.orchestrator` and the task's own outputs. | Unchanged **after G2**. The G1b remediation had added an exemption for restore-staging names (`.ao-result-cache-*.tmp[.bak]`); G2-S1 **removed** it. | Any writer of an input directory could hide a file from the key with such a name (a demonstrated poisoned hit). A crash leftover now costs a false miss; `ao cache prune` sweeps it (`restore_sweep`). `safeio.is_restore_tmp_name` remains for the sweep only. ADR-0019 D8 addendum. |
| DV-8 | D19 / §8.4.3: inline enforcement is bounded by `INLINE_PRUNE_MAX_ENTRIES` and `INLINE_PRUNE_MAX_ENTRY_FILE_BYTES`; `_scan_sizes(entry_limit, entry_bytes_limit)`. | A private per-phase `_Budget` bounds **everything inline maintenance reads**: files, file bytes, directory entries visited and blobs, over `entries/**` of any version and depth, in the size scan **and** in the prune it triggers. Two new constants `INLINE_PRUNE_MAX_WALK_ITEMS = 100000`, `INLINE_PRUNE_MAX_BLOBS = 50000`. `_scan_sizes(budget)`. The mark phase **fails closed** (`_Marks.complete`): an unreadable entry file means no blob is swept. The sweep re-lstats each blob before deleting it. | Gate G1a SEC-01 (the one MUST-FIX: a planted sparse tree stalled the scheduler thread), SEC-02, S-1, S-2, S-4, S-5. The explicit `ao cache prune` stays unbounded. ADR-0019 D19 addendum. §7.6 D19, §8.1.5 and §8.4.3 corrected. |
| DV-9 | §8.4.3 store pseudocode. | `put_entry` checks the 1 MiB bound before `ensure_layout`; `ensure_layout` creates `.orchestrator` itself (mode `0o777` under the umask; only the cache root and below are forced to `0o700`), maps `UnsafePathError` to `CacheUnsafePathError`; `_ws` is `abspath`, not `realpath`; `put_blob` dedupes only onto a **regular** file of the right size and otherwise replaces it (a planted directory is moved to a root-level `trash-*` directory); `read_blob` treats only `NotRegularFileError` as `blob_corrupt`, any other `OSError` is `CacheError(store_error)`; `delete_entry` / `delete_blob` on a directory raise `CacheUnsafePathError`; `os.utime(..., follow_symlinks=False)` has no Windows fallback (POSIX first, A-4). | T-U7ckfd; gate G1a SEC-08, SEC-09, S-7. Same-size forged blob content is not re-hashed at dedupe; restore's hash check self-heals it. |
| DV-10 | §8.4.3 `clear` / `iter_entries` / `verify`. | `clear` wraps a failed `rmtree` into `CacheError(store_error)` and verifies the trash directory itself is gone; `iter_entries` also treats a directory named like an entry, a non-hex name, a wrong-shard file and an **unreadable** entry file as anomalies (never parsed or deleted; `verify` fails on `unreadable`); `prune`/`stats`/`verify` check `entries/v1`, `blobs/`, `tmp/` up front; `stats`/`verify` on a missing root return an empty report; `verify` hashes through a private `_Sha256Sink` (the store may import only `types`, `safeio`, `constants`). `CacheStats.max_entry_bytes` is `None` in the store (the CLI fills it from settings). | T-HjxNQ0; gate G1a S-3. |
| DV-11 | §8.5 restore pseudocode: `chmod` by path, then `os.replace`; a hard crash may leave `.ao-result-cache-*.tmp` litter. | Phase 1 also refuses a destination that is a directory (`restore_failed`); the mode is applied with `os.fchmod` on the open staging descriptor **after** hash verification, never by path; each existing destination is hard-linked to `<staging>.bak` before phase 2 and a failing rename **rolls the earlier ones back**; a transient `read_blob` error is `RestoreMiss(store_error, evict=False)`; `capture_outputs` refuses a path whose `realpath` differs from itself. | T-u3jG8F; gate G1a SEC-05, SEC-07, SEC-09. Residuals: created parent directories stay; where hard links are unavailable one file is not rolled back. ADR-0019 D20 addendum. `0o777` parent mode is the named `_RESTORE_DIR_MODE`. |
| DV-12 | §8.6.2: one `CacheUnsafePathError` handler around `get_entry`. | `_lookup` wraps everything after the key build in one D33 handler; `CacheTooLargeError` from `put_entry` is the skip `entry_too_large`; a coordinator built for a mode other than `on`/`shadow` disables itself; `cli_versions` is a `Callable[[str], str]`; two small `Protocol`s (`HeadReader`, `Worktree`) type the collaborators; `ttl_days` is `int \| None` end to end (no `cast`); `_miss` takes an optional `detail`, `_evict` an optional `blob`; duration is computed from the ISO timestamps (0.0 when unparsable); shared helpers `types.clip_text` and `fingerprint.try_resolve` replace per-module copies. | T-gDNjN2; G1b rev S-3 and S-4. |
| DV-13 | §8.7: `LookupRequest.injected = (ts.origin == "injected")`; constructor keyword after `run_prompt`. | `injected` is `spawn is not None and spawn.loop_id is None` from `RunState.spawned_by` (no `.origin` comparison anywhere under `src/`, enforced by `tests/test_spawn_provenance.py`); the keyword is last, after `summarizer`; the hit path uses `record = outcome.record`. | T-XpF1pF, G1b rev N-1. By the `SpawnRecord` invariant a spawn record without `loop_id` is exactly an `emit_tasks`-injected task. I-27 "no lookup" is verified as no record, no store activity and no cache directory (the coordinator owns the opt-in policy). |
| DV-14 | §8.8: `report.py` and `usage.py` shapes. | `run_block` and `usage_counters` round summed floats (6 and 3 digits); `ResultCacheUsage` lives in `usage.py`; a current hit's **verdict is still read** (a reviewer served from the cache still describes this run's producers; site B filters producers only); a hit-after-spend with no other task in the group creates a group row with `tasks == 0`; `UNKNOWN_REASON` buckets a record with no reason; `format_summary_line` is **total** (a non-dict or any non-finite, negative, non-numeric or oversized field gives no line); `cli._echo_result_cache_line` strips control characters; the `ao status` fallback catches `ValueError` (JSON integer-digit limit). | T-eyn5UG; G1b sec S-2 (`status.json` is agent-writable). |
| DV-15 | §8.1.7 banner. | In mode `on` the banner ends with `(agent-writable; avoid for untrusted prompts)` (`_RESULT_CACHE_TRUST_NOTE`); the shadow banner does not. | G1b sec S-5: `on` serves stored outputs and the cache directory is writable by the agent (R-A4). |
| DV-16 | §8.7.5 / E-2 / U-LZ: a cache-off CLI process loads only `cache`, `cache.constants`, `cache.settings`. | The allow-list is `{cache, cache.constants, cache.settings, cache.cli}`. The **engine** process (I-1) keeps `{cache, cache.constants}`. | `cli.py:44` registers the `ao cache` group eagerly (`from .cache.cli import cache_app`), so every `ao` start loads `cache.cli`. Manager decision (T-o95l1M): keep `cache/cli.py` import-light (module level: at most `typer`, `constants`, `settings`; today `typer` and `constants`; everything else lazy inside the command bodies), pinned by `TestCacheCliStaysImportLight` and `TestLazyImports`. **Decision recorded in ADR-0019 (Rev 4 addendum).** |
| DV-17 | §8.0 / §8.9: `cache/cli.py` holds the commands. | Surface in `cache/cli.py`; behaviour in `cache/cli_ops.py` (lazy); the prune-side sweep in `cache/restore_sweep.py`; new `safeio.open_dir_fd`; `safeio.strip_control_chars` also strips bidi, zero-width and tag characters (U+061C, U+00AD, U+E0000 to U+E007F added at G2). | T-6tRKml; G1a SEC-14, G2-N1. Import graph: `cli` -> `cli_ops` (lazy) -> `store`, `restore_sweep`, `settings`, `safeio`; `restore_sweep` imports `safeio`, `constants`, `types`. |
| DV-18 | §8.9: `--workspace` / `AO_WORKSPACE_ROOT` / config discovery; `prune` JSON per §13.4. | Same resolver (`cli._resolve_workspace_root`), but a workspace that is **not an existing directory is exit 2** for every command (text: one `ERROR:` line; `--json`: a document with `error`). Limits (`max_bytes`, `ttl_days`) come from the `.ao/config.yaml` discovered from the **current directory**, not from `--workspace`. `prune --json` adds `removed_restore_tmp`, `kept_restore_backups`, `restore_sweep_truncated`; `verify` problem `kind` may be `unreadable`; `--older-than 0` does not expire an entry with a future `created_at`; `rm` removes the entry file only (its blobs go with the next `prune`, after the 1 h grace). | T-6tRKml; G2-S5 (a mistyped `--workspace` used to exit 0), G2-N6 (accepted: run `prune` from the project, use `--dry-run`). `--help` names only `AO_WORKSPACE_ROOT` (and says the `.ao/config.yaml` `workspace_root` override is not read, which is true); the remaining fallback is the repo set's `workspace_root` of the workflow found via `.ao/config.yaml`, the same workspace `ao run` uses (re-checked by the architect, T-bdQZW4 sign-off). |
| DV-19 | §8.10: `ui/files.py` about +6 lines, case-sensitive comparison. | `ui/files.py` is +13 lines: the cache-root comparison is on **casefolded** path parts and a NUL byte is a 403. `tests/ui/test_run_graph_endpoint.py` (+12/-3) is an existing test edited: it pins exact payload key sets, so `result_cache` was added to both. | G2-N2. Backend only; no bundle rebuild. |
| DV-20 | §8.11: `bench/subjects.py` +3 lines. | +9 lines: a second named constant `_AO_CACHE_OFF_VALUE = "0"` next to `_AO_NO_CACHE_FLAG`, and a module-level import of `cache.constants.ENV_CACHE`. | NFR-7, no bare literal (T-ZTxN1x). |
| DV-21 | §24.2 merge-notes figures. | See the corrected table in §24.2 (`runstate.py` +12/-1, `ui/files.py` +13, `cli.py` +115/-2 including an eager `cache.cli` import and `except (ValueError, KeyError)` in `status`, `usage.py` +109/-23, `ci.yml` +8, and the unlisted `tests/ui/test_run_graph_endpoint.py`). | G2-S3. |
| DV-22 | §23.5 B7: G0 would-hit rate from `ao cache stats --json`. | Unchanged: it comes from `ao report-usage --json` (`result_cache.would_hits / lookups`); `ao cache stats --json` supplies store growth. | Recorded in Rev 3; the G0 protocol encodes it. |
| DV-23 | §18: test files listed per task. | Extra shared helper modules: `tests/cache/{fakes,keys_fixture,_report_states,_wiring_fixture,_hardening_rig,_noop_poison,_noop_scenarios,_noop_subprocess,_noop_capture}.py`, `tests/fixtures/result_cache/{corpus,schemas,golden}/`. `tests/conftest.py` and `tests/test_nfr2_regression_gate.py` are **not edited**. | Test infrastructure only. |
| DV-24 | §18 I-1: a `sys.meta_path` poison finder. | The finder must implement `find_spec` (Python 3.12 never calls the legacy `find_module`). Part 1's first delivery was rejected as vacuous; the rework has self-tests and negative controls, ten cache-off workflows and base-captured goldens. | T-JCOAsq. Learning captured (`meta/learnings.md`). |

### 0.4 Accepted residuals

Stated here and in ADR-0019 (Rev 4 addendum). Each was judged and accepted at a gate; none is
fixed in code. Operator mitigation: `--no-cache` for untrusted repositories or prompts.

| ID | Residual | Source | Precondition and mitigation |
|---|---|---|---|
| R-A1 | **Provider and endpoint environment is not in the key.** `ANTHROPIC_BASE_URL`, `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX` and the provider region are outside `CLAUDE_FINGERPRINT_ENV_VARS`, so a hit can be served across a backend or endpoint switch with an unchanged model alias. Same model name, operator-controlled, non-secret; adding them would change GV-1. | G2 rev S4, G1a SEC-11 | Pass `--no-cache`, or run `ao cache clear`, when switching provider or endpoint. |
| R-A2 | **`git status` in the guard-3 probe executes a `filter.<x>.clean` command** named in the agent-writable git config (re-confirmed live on git 2.39.5; `core.fsmonitor` is closed by DV-4). Same family as G1a SEC-03. | G2 sec S2, G1b S-3 | Cache on **and** the task opted in **and** a writer of the git config or `.gitattributes` that cannot already run code (a tool-restricted agent without Bash). An agent with Bash already has the same power, and the isolation integrator already runs `git status` in agent-touched trees. Use `--no-cache` for untrusted repositories. |
| R-A3 | **Dirty tracked edits made before the lookup are not in the key** (accepted at Rev 3, R-20, EC-26). Guard 3 catches edits made during the run only. It is also blind to `assume-unchanged` / `skip-worktree` files and to untracked or non-git writes, and after a quota or self-heal requeue the next lookup re-takes the baseline, so tracked edits made by the abandoned partial attempt are absorbed and the successful retry can be stored. | Rev 3; G1b sec N-7, N-9 | Opt in only tasks whose effect is captured by their declared outputs. |
| R-A4 | **The cache directory is agent-writable, so same-uid forgery is not defended** (keys are not secrets): an agent that can write `.orchestrator/cache` can plant an entry and blobs that a later `on` run restores. Includes a forged future `created_at` that never expires (SEC-18), a stored mode `0o000` restored unreadable (SEC-16) and a hard-linked blob. | §7.7; G1b sec S-5; G1a SEC-16, 18, 21 | The `on` banner says so (DV-15). `ao cache verify` / `clear`; HMAC and `dir_fd` walking are non-MVP 2 and 9. |
| R-A5 | **A workspace nested inside a parent repository is treated as non-git** (HEAD not keyed; guards 2 and 3 inactive). | A-12, R-19 | The banner warns. Do not opt tasks in such a layout. |
| R-A6 | **Retention.** Outputs persist in `.orchestrator/cache` (mode `0o700`, self-gitignored, refused by the dashboard) until `ao cache rm`, `clear` or `prune`; **shadow mode also stores output copies**. A crash leftover `.ao-result-cache-*.tmp[.bak]` in an output directory is swept only by `ao cache prune`, and only in the output directories of entries still in the store: after `ao cache clear` a leftover is never swept (G2-N7). | G2 sec N4, N7 | Run `ao cache clear --yes` after a shadow measurement. The G0 protocol now ends with "Step 9 — cleanup and retention" (`ao cache clear --yes --workspace "$WS"`; added at the architect sign-off of T-bdQZW4, manager-authorized, closing G2 sec N4). |
| R-A7 | **Active TOCTOU races.** Persistent planted links are caught; a concurrent same-uid racer is not (§7.7). The prune-side sweep follows an intermediate directory component swapped between `check_dir_chain` and `open_dir_fd` (G2-N5; blast radius: regular, user-owned, staging-named files older than the grace). | §7.7; G2 sec N5 | Non-MVP 9. |
| R-A8 | **Smaller accepted items:** `ResultCacheRecord.outcome/mode/mode_source` have no `max_length` and `reason_detail` can carry host paths (G2-N8, G1b N-4/N-5); `ao cache prune` takes limits from the CWD config (DV-18); a same-size forged blob is not re-hashed at dedupe (DV-9); `assume-unchanged` and partial-attempt baselines (R-A3); the restore rollback leaves created parent directories (DV-11). | gates | Same-uid or hygiene class; one post-merge hygiene ticket. |

### 0.5 GV-1 and the component digests (re-verified)

`.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache/test_keys_golden.py
tests/test_claude_cli_argv_builder.py` -> `102 passed`. A direct run of the GV-1 fixture
(`tests/cache/keys_fixture.py`: `key_for` over the workspace in §8.2.7) printed:

```text
key = 6646469e94a695fe1a994d35f54ca74e007262911255552b03c54ce2e5d0319f   (== GV1_KEY, unchanged since Rev 2)
agent ced58570dab6 · argv 315cfbdef1cf · dynamic_inputs 4f53cda18c2b · executor_fingerprint 620dc66502c3 ·
general_instructions 80c58b832f5c · inputs 6518d7ac7319 · instruction 8f7ad8e7e8d2 · key_schema 6b86b273ff34 ·
outputs 2789b49e50b4 · prompt 25e61c2662b6 · repo_heads b1e77f36ac12
```

All eleven digests equal the §8.2.7 worked example. None of the gate remediations changed the key:
the sensitive lists, the restore protocol, the git environment and the hashing exemption removal do
not feed the key document for the GV-1 workspace (no restore-staging names, no sensitive outputs).

### 0.6 Follow-ups (not done in this epic)

| ID | Follow-up | Source | Tracking |
|---|---|---|---|
| FU-1 | **Run G0** (shadow mode on a real consumer workflow, then the §22.5 decision rule). | D34, OQ-6 | Parent or operator, post-merge. The parent confirms the thresholds. |
| FU-2 | **Merge with E-Ag7Pw3 and E-Da5Tn9**: follow §24.2 (classify new fields RULED; approval check before call site (c); recapture the I-2 goldens if `status.json` or `ao run` output changed; rebuild the UI bundle, never hand-merge). Add the approval-seam pin test once that code exists (G2 N4). | §24.2, OQ-4 | Parent, at merge. |
| FU-3 | **CI supply-chain gates**: keep the `permissions:` block and the `pip-audit` job (T-2wE08U), add `cli_ops restore_sweep safeio` to the per-module coverage loop. | G2 sec N3 | Merge-time item. |
| FU-4 | One **hygiene ticket** for the deferred NITs: `claude --version` stdin and output bounds (SEC-10), strict models (SEC-13), `ResultCacheRecord` string bounds (G2-N8), the `--cache` option declared twice (rev G2-N1), `SHA256_HEX_RE` / `TypedDict` (rev G2-N3), `_LS_HEADER` widths (rev G2-N6), `Path.open` / `read_bytes` AST checks (S-9), `PruneReport` live total (S-6). | G1a, G1b, G2 | Backlog. |
| FU-5 | **Bug ticket** (pre-existing): the missing-inputs branch in `_prepare_and_maybe_dispatch` resets `dispatch_cycle` (§23.2). D14's `ended_at` binding keeps this epic robust to it. | §23.2 | Backlog; **deferred 2026-10-06** (engine bug outside the closure scope, no auth impact; needs a regression test). |
| FU-6 | The pre-existing `ui/files.read_file` FIFO open (§23.2) and the pre-existing red lint step on `output/E-YAAGhk-overseer-runner-template/repro_emit_lost_on_breaker_trip.py` (G2 N7). | §23.2, G2 | **Done 2026-10-06** (`read_file` refuses non-regular files; lint file fixed). |
| FU-7 | Hardening options for R-A2 if it is ever promoted: stat-compare via `git ls-files --debug`, skip guard 3 when `.git/config` has `filter.`, or an empty attributes source. Making `--help` mention the config-discovered workspace fallback (DV-18). | G1b S-3 | Architect decision. |
| FU-8 | Non-MVP items of §2.3 (remote backends, HMAC, `CachingExecutor` / ALT-7 if isolation becomes the default (R-15), `--reuse-from` / ALT-8, `refresh`, `rm --run/--task`, `verify --repair`, ...). | §2.3 | `meta/ROADMAP.md` §3.6 and §3.4. |

---

## 0A. Design summary (Rev 3)

This epic adds a **double-opt-in, content-addressed, workspace-local result cache**. It is off by
default.

**When a task is cached.** Both of these must be true:

1. **The operator turns it on for the run.** The switch is resolved from `--cache` / `--no-cache`,
   then `AO_CACHE`, then `.ao/config.yaml cache.enabled`/`cache.mode`.
2. **The workflow author opts the task in.** This is done with `defaults.cache: true` or
   `tasks[].cache: true`. The author is the only party who can vouch that a task's entire effect is
   captured by its declared output files.

**Modes.** `on` restores hits. `shadow` only measures, and records "would hit"; it never restores.
To re-roll one cached result, remove it with `ao cache rm <key>` and re-run. A `refresh` mode is
deferred (non-MVP 14).

**The cache key.** For each opted-in, structurally eligible plain agent task, the engine computes a
sha256 key over a canonical JSON document describing exactly what the agent would be asked to do:

- the prompt, rendered by the real `build_prompt`;
- the **exact argv** the claude executor would run (from an extracted `build_claude_argv`);
- the effective agent;
- an **executor fingerprint**: CLI version, model-relevant env vars, and `CLAUDE.md` / `.claude/**`
  / `.mcp.json` digests;
- content digests of the instruction, general instructions, inputs and dynamic inputs;
- the declared outputs, with each output's **prior** content;
- the HEAD of every git repo in the repo set.

The key is computed after the existing skip, join and missing-input checks, and **before** the
budget gate.

**On a hit** (mode `on`):

- The stored blobs are restored to spec-derived destinations. Each blob is hash-verified and staged
  before any atomic rename.
- Sensitive destinations (`.git`, `.claude`, `CLAUDE.md`, …) are never written.
- The engine marks the task `succeeded` itself, without dispatching an agent, consuming a retry, or
  charging a budget.
- Avoided spend is recorded separately from real usage.

**On a miss** the task dispatches as today. Its final settled success is stored only if three
guards pass: the key is unchanged at settle, no repo HEAD moved, and no tracked file outside the
declared outputs changed. The tracked-file snapshot (guard 3) is taken lazily, only for storable
outcomes: a hit never pays for it and never fails because of it.

**Where the code lives.** All new logic is in a new package, `agent_orchestrator.cache`. Shared
files get small, additive hooks (§24.2).

**When the switch is off (the default), the engine runs and imports no cache code.** The only
cache modules a cache-off process can load are `agent_orchestrator.cache` and
`agent_orchestrator.cache.constants` (via `project_config`), plus `cache.settings` and
`cache.cli` on the CLI path (as built: `cli.py` registers the `ao cache` group eagerly, DV-16).
Read-side helpers import `cache.report` lazily, and only when a run has result-cache records, so
`status.json` and the CLI text are byte-identical.

**Key decisions.** The full log is §7.6; the ADR is ADR-0019.

| Decision | Ref |
|----------|-----|
| Double opt-in (one named default, `DEFAULT_TASK_CACHE_POLICY = False`); modes `on`/`shadow`; `--no-cache` / `AO_CACHE=0` is a kill switch | D1, D26 |
| Key excludes the task id and includes repo HEADs | D4, D5 |
| Key includes prior output content | D6 |
| Key includes argv and an executor fingerprint | D7 |
| Fail-closed allowlist with tripwires and runtime unknown-field rules | D10 |
| Lookup before the budget gate; hit reuses `"skipped"`; engine-owned settle; `dispatch_cycle` keeps its increment | D11, D12 |
| Store at settle with three purity guards; guard 3 lazy | D13 |
| Records on `RunState`, staleness derived | D14 |
| Hostile data is parsed by total functions; a cache error never kills a run | D28, D32 |
| Store ABCs are split and provisional; entries are versioned | D17, D18 |
| Restore paths come only from the current spec; sensitive paths are refused | D20, D29 |
| `ao cache rm <key\|prefix>` for single-entry invalidation | D27 |
| An unsafe (symlinked) cache path is never evicted or followed | D33 |
| G0 is a post-merge, operator-owned measurement; the epic ships its protocol and tooling | D34 |
| Record and report fields `hit`, `key`, `saved_cost_usd`, `saved_tokens`, `saved_seconds` inside a `result_cache` container | D35 |

> **Value risk, recorded rather than hidden.** `dev-critic` reviewed Rev 1 and returned a
> STRATEGIC finding: the hit rate for the primary consumer is unproven, and the fail-closed key
> components (D5, D6) narrow it further. The design responds in three ways:
> - adds `shadow` mode;
> - defines a **G0 value check** that must pass before `on` is recommended to any consumer
>   (§22.5). This epic ships G0's protocol and tooling. Running it needs multi-day runs of a real
>   consumer workflow with operator consent, so it is a post-merge follow-up owned by the parent
>   or operator (D34);
> - records the alternatives (`--reuse-from <run>`, an executor-level `CachingExecutor`) in
>   ADR-0019.

---

## 1. Requirements

### 1.1 Stated by the parent brief (binding, summarized)

| # | Statement |
|---|-----------|
| P-1 | Opt-in and default OFF. Precedence is CLI `--cache/--no-cache` > env `AO_CACHE` > `.ao/config.yaml cache.enabled`. Plus `WorkflowDefaults.cache` and per-task `cache: bool \| None`. Task beats workflow default. An explicit `cache: false` always means never. `ao run` and `ao resume` share one helper. |
| P-2 | Key = sha256 over versioned canonical JSON of: the prompt as dispatched; the effective agent (no secrets or env); the content hashes of declared inputs (directories as a manifest; a missing input makes the task uncacheable); and the sorted declared output paths. Exclude the run id, timestamps and absolute paths. Decide whether the task id is part of the key. |
| P-3 | Eligibility is a fail-closed ALLOWLIST: at least 1 output; not emit/router/loop/control; isolation resolves to none; no hooks; paths go through the artifact-store guard. Only a FINAL settled success is stored. |
| P-4 | `CacheStore` ABC + `LocalFsCacheStore`: content-addressed blobs, entry JSON, atomic writes, concurrency-safe, LRU size cap, optional TTL. The location is chosen deliberately. |
| P-5 | Restore safety: paths come only from the current spec; the manifest is compared; blobs are verified (corruption is a miss plus evict plus log); restore is atomic. |
| P-6 | Accounting: a hit is `succeeded`, costs no attempt, $0 and 0 tokens, charges no budget and feeds no breaker. Record `saved_*` without double counting against E-9h3m7k. Records live on a `RunState` map. A hit survives `ao resume`. |
| P-7 | `ao-bench` forces the cache off: `--no-cache` in argv plus `AO_CACHE=0` in env. Add a regression test. |
| P-8 | Events `cache.hit\|miss\|store\|evict\|corrupt\|skip` with a reason. `ao cache ls\|stats\|show\|prune\|clear\|verify`, each with `--json`. A run-summary line. A minimal dashboard. |
| P-9 | The lookup sits in `_prepare_and_maybe_dispatch` after `should_skip`; the store happens at `_settle_completed_task`. The parallel and isolation paths stay untouched when the cache is off, and this must be provable. |

### 1.2 Derived requirements

- **R-D1. Prompts carry paths, not contents.** A prompt contains only absolute **paths**, never file
  contents (NFR-1). The key therefore content-hashes the instruction and general-instruction files,
  and renders the prompt from a **normalized** (workspace-relative) context.
- **R-D2. Agents read ambient state.** Agents read repos and outputs that are edited in place. The
  key needs repo HEADs and prior-output content. Store time needs HEAD-moved and
  tracked-worktree-change guards.
- **R-D3. Per-task state survives resume only on `RunState`.** `prepare_resume` replaces
  `TaskRunState` wholesale (`LRN-20260928-taskrunstate-wholesale-replaced-on-resume`), so per-task
  cache state lives on a `RunState` map.
- **R-D4. The cache directory is agent-writable.**
  - Never derive a filesystem path from an entry.
  - Parse cache files with total functions.
  - Never follow symlinks inside the store (`LRN-20260928-agent-writable-launch-record-path-fallback-risk`,
    ADR-0017).
- **R-D5. Naming.** "Cache" already means prompt caching here (see the terminology box).
- **R-D6. Usage metrics.** `aggregate_usage` counts settled tasks with `dispatch_cycle >= 1` as
  dispatched, so current hits must be excluded explicitly.
- **R-D7. `status.json` is a snapshot contract.** When the cache is off, the output must stay
  byte-identical.
- **R-D8. The Claude CLI reads ambient context.** `CLAUDE.md`, `.claude/settings*.json`,
  `.claude/{agents,commands,skills}` and `.mcp.json` shape what an agent does. The primary
  consumer's workspace root is not a git repository, so repo HEADs cannot cover these files, and
  their digests belong in the key.
- **R-D9. Approval ordering.** An approval or human gate from a sibling epic must never be bypassed
  by a hit.

### 1.3 Requirement table (IDs used by tickets)

| ID | Requirement | MVP | Verification |
|----|-------------|-----|--------------|
| FR-1 | **Run-level mode.** `--cache` (on) / `--no-cache` (off) > `AO_CACHE` (`1/true/yes/on`, `0/false/no/off`, `shadow`; empty = unset; anything else, including the deferred `refresh`, = OFF plus a warning) > `.ao/config.yaml cache.enabled` (+ `cache.mode`) > off. One helper, `cli._build_result_cache`, serves `ao run` and `ao resume`. | ✅ | unit (resolver matrix) + e2e |
| FR-2 | **Author opt-in (double opt-in).** `policy(task)` = `task.cache` (an injected task's `true` is ignored), else `defaults.cache`, else `DEFAULT_TASK_CACHE_POLICY` (**False**; the one named flip point, D1). Tasks that are not opted in get no record and no event. | ✅ | unit + integration + e2e |
| FR-3 | **Key schema v1** (§8.2): canonical JSON, GV-1 golden vector, normalization rules, task id excluded, argv and executor fingerprint included. | ✅ | unit |
| FR-4 | **Fail-closed eligibility** (§8.3): full classification of `TaskSpec`, `AgentSpec`, `WorkflowSpec` and `WorkflowDefaults`; tripwires; runtime unknown-field rules for all four models (`unknown_task_field`, `unknown_agent_field`, `unknown_workflow_field`); command-basename and resolved-model rules; one reason per exclusion. | ✅ | unit |
| FR-5 | **Lookup placement and hit semantics** (§8.7). The engine settles the hit itself. | ✅ | integration |
| FR-6 | **Store only a final settled success**, guarded by: key recompute, HEAD-moved, tracked-worktree-changed. The tracked-worktree snapshot is taken lazily, only for storable outcomes; a probe failure makes the result not storable and never makes the task ineligible. | ✅ | integration |
| FR-7 | **`CacheStore` (hot path) + `CacheAdmin` (maintenance)**, both provisional, plus `LocalFsCacheStore` (§8.4). | ✅ | unit + multiprocess race |
| FR-8 | **Restore and capture safety** (§8.5): spec-derived and re-validated destinations, sensitive-path refusal, verify-before-commit, mode masking. | ✅ | unit + adversarial |
| FR-9 | **Accounting** (§8.8): `saved_*` kept separate; hits never touch `cumulative_*`, budget counters (except reversing a stale charge) or breakers. | ✅ | integration |
| FR-10 | **Resume.** A restored task stays `succeeded`. Records stay consistent across cache-on and cache-off sessions. | ✅ | integration + e2e |
| FR-11 | **Observability:** `cache.*` events (with key-component digests on a miss); `status.json`; summary line; `report-usage`; `report-outcomes` (`settle_reason: cached`). Every per-task surface exposes exactly `hit`, `key`, `saved_cost_usd`, `saved_tokens` and `saved_seconds` inside a `result_cache` object; run-level and cross-run surfaces expose `hits`, `saved_cost_usd`, `saved_tokens` and `saved_seconds`. Other fields are additive (D35). | ✅ | unit + e2e |
| FR-12 | **`ao cache ls\|stats\|show\|prune\|clear\|verify\|rm`**, each with `--json` and exit codes (§8.9). `verify` is read-only (`--repair` is deferred, non-MVP 16). | ✅ | e2e |
| FR-13 | **Dashboard:** a "cached" tag and a "Result cache" tile. The dashboard file browser does not serve `.orchestrator/cache`. | ✅ | pytest + vitest |
| FR-14 | **`ao-bench` forced off.** | ✅ | regression test |
| FR-15 | **Config:** `cache.{enabled,mode,max_bytes,max_entry_bytes,ttl_days,include_repo_heads,max_input_bytes,max_input_files}`, with bounds; plus the `ao init` template. | ✅ | unit |
| FR-16 | **Shadow mode.** `shadow` records `would_hit`, never restores, and still stores. (`refresh` is deferred, non-MVP 14.) | ✅ | integration + e2e |
| FR-17 | **Single-entry invalidation:** `ao cache rm <key\|prefix>`. (`rm --run R --task T` is deferred, non-MVP 15.) | ✅ | e2e |
| NFR-1 | **No-op when off** (§8.7.5). The engine executes no cache code. A cache-off engine process loads no cache module except `agent_orchestrator.cache` and `.constants` (via `project_config`); the CLI path may also load `.settings` and `.cli` (as built, DV-16: `cli.py` registers the `ao cache` group eagerly, and `cache/cli.py` stays import-light). `runstate`, `usage`, `outcomes`, `cli` and `ui/runs` import `cache.report` lazily, only when `state.result_cache` is non-empty. `status.json` and CLI text are byte-identical. `state.json` gains `result_cache: {}`. The dashboard JSON gains null keys, following the `integration` precedent. `report-usage --json` omits its `result_cache` object when there are no records. | ✅ | structural guards, I-1/I-2, full suite |
| NFR-2 | **`engine.py` stays content-free.** All byte I/O happens in `agent_orchestrator.cache`. | ✅ | existing static audits + review |
| NFR-3 | **Deterministic and replayable:** injected clock, canonical JSON, golden vectors. | ✅ | unit |
| NFR-4 | **Bounded work and memory:** hash caps, entry caps, size checks before reads, bounded reads, inline maintenance bounded by entry count **and** entry-file bytes. | ✅ | unit |
| NFR-5 | **Concurrency-safe across processes;** a same-key race is benign. | ✅ | multiprocess test |
| NFR-6 | **Backward and forward compatible:** versioned entry directory, layout file, open-set record fields. | ✅ | unit |
| NFR-7 | **No magic literals** (`cache/constants.py`). | ✅ | review |
| NFR-8 | **Small, additive shared-file edits.** `engine.py` changes by at most +110 formatted lines net (measured ≈ +103: about 123 added, 20 moved out of `_prepare_and_maybe_dispatch` into `_reverse_stale_charge`), with at most 12 added lines inside existing functions (§8.7.1). Every added line is ≤ 100 columns (`ruff`). | ✅ | review (diff stat) |
| NFR-9 | **Coverage** of at least 85% for `agent_orchestrator.cache`. | ✅ | CI / T-JCOAsq |
| NFR-10 | **Threat-model mitigations** M-1…M-16 (§7.7). | ✅ | gates G1a, G1b, G2 |
| NFR-11 | **Hostile data never raises:** every cache-file parser is total; any failure is a miss or a skip. The engine-facing API never raises cache errors; an unexpected bug disables the cache for the rest of the run (D32). | ✅ | hostile-entry corpus test |

---

## 2. Scope

### 2.1 In scope (MVP)

**New package `src/agent_orchestrator/cache/`.** It contains:

- `constants`, `safeio`, `types` (contracts), `settings`;
- `hashing`, `repo_state`, `fingerprint`, `keys`;
- `eligibility`, `store`, `restore`;
- `records`, `coordinator`, `report`;
- `cli` (the `ao cache` sub-app: the Typer surface), and, as built, `cli_ops` (the commands'
  behaviour, imported lazily) and `restore_sweep` (the prune-side sweep of stale restore
  staging files).

**Additive shared-file hooks:**

| File | Change |
|------|--------|
| `models.py` | spec fields, `ResultCacheRecord`, `RunState.result_cache`, predicate |
| `specs/workflow.schema.json` | schema entries for the new spec fields |
| `project_config.py` | `CacheConfig` |
| `engine.py` | two private methods plus two call sites |
| `executors/claude_cli.py` | behaviour-identical extraction of `build_claude_argv` |
| `cli.py` | flags, helper, summary lines, sub-app, `report-usage` line (lazy `cache.report` import) |
| `runstate.py` | `write_status` (lazy `cache.report` import) |
| `usage.py` | both dispatch-counting sites skip current hits (real spend kept); `result_cache` totals (lazy import) |
| `outcomes.py` | `settle_reason: "cached"` (lazy import) |
| `ui/runs.py`, `ui/files.py` | payload fields (lazy import); `ui/files.py` denies the cache dir |
| `ui/src/types.ts`, `RunDetail.tsx` | frontend types and display |
| `bench/subjects.py` | force the cache off |
| `.github/workflows/ci.yml` | one additive step: result-cache tests with a coverage floor |

**Tests:**

- unit, integration, e2e and adversarial tests, plus a hostile-entry corpus;
- tripwires;
- an AST guard;
- a no-op proof.

**Docs:**

- this HLD and ADR-0019;
- a "Result cache" section in the workflow-authoring skill;
- the docs-refresh ticket;
- the G0 protocol and tooling hand-off (`docs-md/result-cache-g0-protocol.md`). Running G0 is out
  of scope (§2.2).

### 2.2 Out of scope (explicit)

- Any change to `should_skip`, `prepare_resume`, the wave scheduler, isolation/integration, budget
  or breaker logic. The one exception is a single call to the existing
  `BudgetManager.reverse_estimate` on a hit (D12).
- Any change to executor *behaviour*. The `build_claude_argv` extraction is behaviour-identical,
  and a golden argv test proves it.
- Making agents deterministic.
- Caching Python values or transcripts.
- **Executing the G0 value check** on real consumer workflows. It needs multi-day runs and the
  operator's consent, so the parent or operator runs it after the merge (for example on finplan).
  It does not block epic closure (D34).

### 2.3 Non-MVP (recorded, not designed as deliverables)

1. Remote, shared or S3 backends. The ABCs are **provisional** (§7.5).
2. HMAC-authenticated entries. The key would live under `isolation.paths.state_dir()`, and entries
   are already canonical bytes.
3. An executor-level `CachingExecutor` design that works inside isolation worktrees and with hooks
   (ADR-0019 alternative ALT-7).
4. An explicit `ao run --reuse-from <run-id>`, where the operator vouches for a source run
   (ADR-0019 alternative ALT-8).
5. Caching with isolation, with integration active, or with hooks; directory outputs; dynamic
   outputs.
6. Dependency-aware invalidation (for example, a FAIL verdict evicting its producers' keys), cache
   warming, cost-aware eviction, and cross-workspace sharing.
7. Recording the resolved model id from the transcript.
8. User-level (`~/.claude/**`) context fingerprinting.
9. `dir_fd`-walk (openat-style) store and restore I/O, which would close active-race TOCTOU windows
   (§7.7 residual).
10. Memoizing hashes within a run, and reusing a lookup across budget re-gating passes.
11. An `ao validate` warning; `ao cache explain`; dashboard launch controls; a runs-list column;
    surfacing on the Usage tab.
12. Per-entry hit counters.
13. Fixing the pre-existing missing-inputs branch that resets `dispatch_cycle` (engine.py ~1174):
    recommended as a separate bug ticket (§23.2).
14. **`refresh` mode** (deferred in Rev 3). Re-rolling is `ao cache rm <key>` followed by a re-run.
    A run-wide overwrite mode adds a third semantics to every lookup for little extra value.
15. **`ao cache rm --run R --task T`** (deferred in Rev 3). It reads the agent-writable
    `state.json` to find a key, which is an extra trust boundary. Operators copy the key from
    `status.json` or `ao cache ls` instead.
16. **`ao cache verify --repair`** (deferred in Rev 3). Automated destructive repair is not
    needed in MVP: lookups already evict corrupt entries, and `prune`, `rm` and `clear` cover
    manual repair.

---

## 3. Assumption log

| ID | Assumption | Risk if wrong | Mitigation |
|----|-----------|---------------|------------|
| A-1 | **Double opt-in** (operator gate + author opt-in) is acceptable to the parent. Manager proposal A had the author layer defaulting to *allowed*. Reviewer MUST-FIX R1 changes the default to *not opted in* (D1). The default is one named constant, `DEFAULT_TASK_CACHE_POLICY = False`; it is THE flip point if the parent prefers "operator enabled ⇒ every eligible task cached unless `cache: false`". | Friction: users must also edit workflows. | The banner reports the opted-in count, the authoring guide explains the step, and templates can opt in their pure tasks. |
| A-2 | `include_repo_heads` defaults to `true`. The HEAD-moved store guard is always active. | Low hit rate in repos where commits are frequent. | Operator opt-out. G0 measures the effect. |
| A-3 | Default limits: `max_bytes` 1 GiB; `max_entry_bytes` 64 MiB (clamped to `max_bytes` when unset); `ttl_days` 30; `max_input_bytes` 512 MiB; `max_input_files` 20 000. | Limits too small or too large. | Bounded config knobs. |
| A-4 | Target platform is POSIX/Linux first. | Weaker symlink and FIFO defences on Windows. | `getattr(os, flag, 0)`. Windows is documented as best-effort. |
| A-5 | An opted-in task's effect is fully captured by its declared outputs. The author vouches; the guards spot-check. | A stale or partial replay. | The tracked-worktree guard, the authoring guide, and per-task `cache: false`. |
| A-6 | Model aliases and the CLI version are acceptable key material. | A retargeted alias gives a stale hit. | The CLI version is in the key; the TTL defaults to 30 days; the guide recommends pinned ids. |
| A-7 | E-Ag7Pw3 adds a `TaskSpec` or `WorkflowSpec` field, or a kind. | An approval inferred from an existing value would bypass the allowlist. | Runtime unknown-field rules for both specs, plus the merge checklist (§24.2). |
| A-8 | A display-only dashboard is acceptable. | Users want a launch toggle. | Non-MVP item 11. |
| A-9 | The env allowlist in `fingerprint.CLAUDE_FINGERPRINT_ENV_VARS` names the behaviour-relevant, **non-secret** Claude CLI variables. It is deliberately small and closed; adding a name needs review. **Checked in T-uoYW6b (§0.2):** the list was compared with the CLI's documented env vars and is unchanged; the provider and endpoint variables are an accepted residual (R-A1). | An unlisted variable causes a stale hit. | The list is easy to extend. Secrets (`*_API_KEY`, auth tokens) are never included. |
| A-10 | `claude --version` is cheap: memoized per binary identity (resolved path, mtime and size), with a 10 s timeout. A binary replaced mid-process is re-read. | Auto-updates churn the key, which costs misses but is safe. | Documented, and visible in G0. |
| A-11 | **Corrected in Rev 3.** G0 needs multi-day shadow-mode runs of a **real consumer** workflow, with the operator's consent. This repo cannot supply that workload: `specs/self-dev/` holds only agent and reposet files, the built-in templates write per-instance output paths, and the bench forces the cache off. Shadow mode adds hashing and storage but no model spend. | G0 never runs, so `on` is never recommended. | G0 is a post-merge follow-up owned by the parent or operator (finplan, with consent). T-nPMuz4 ships a runnable protocol, the measurement fields and a report template (D34). |
| A-12 | The workspace root is a repository toplevel, or contains the repositories of the repo set. Repository detection walks up only to the workspace root (D5). | A workspace nested inside a parent repository is treated as non-git: its HEAD is not keyed and guards 2 and 3 do not apply (fail-open for that layout). | The `ao run` banner warns when a `.git` marker exists above the workspace root (§8.1.7). The authoring guide says not to opt in tasks in such a layout. |

---

## 4. Standards survey

| Area | Standard or practice | Applied as |
|------|--------------------|-----------|
| Architecture docs | C4; ADRs (MADR-style) | §7.1–§7.3; ADR-0019 |
| Data contracts | JSON Schema 2020-12 | §13 |
| Content-addressed storage | git object layout; Bazel CAS; DVC cache | `blobs/<sha[:2]>/<sha>`, `entries/v1/<k[:2]>/<k>.json` |
| Cache-dir hygiene | Cache Directory Tagging Spec (`CACHEDIR.TAG`); self-ignoring dirs | `CACHEDIR.TAG` + `.gitignore` containing `*` |
| Atomic writes | POSIX `rename(2)`; temp file in the same directory | temp file + `os.replace` |
| Secure file handling | CWE-22, -59, -367, -400, -248, -674, -150, -94/-73 | spec-derived paths; `O_NOFOLLOW`; component checks; `O_EXCL`; `S_ISREG`; caps; total parsers; control-character stripping; sensitive-path refusal |
| Hashing | SHA-256 (FIPS 180-4) | keys, blobs, digests |
| Key derivation | Bazel action key (argv + inputs + env); Turborepo hash; Gradle relative paths | normalized paths; hashed argv; env allowlist; content digests |
| Config precedence | ADR-0003/0006 | FR-1; the kill switch works like `--no-isolation` |
| Observability | structured JSON log events | §15 |
| Testing | test pyramid; fixed clocks; golden vectors; hostile corpus | §18 |

---

## 5. Solution landscape (build vs buy vs hybrid)

| Option | What | Verdict |
|--------|------|---------|
| B-1 `diskcache` | SQLite KV store with LRU | **Rejected.** It adds a dependency and stores values in its own DB. Restore-to-spec, verification and key derivation would still have to be built, and it does not dedupe content. |
| B-2 `joblib.Memory` / `cachetools` | Function memoization | **Rejected.** It **pickles** values, and unpickling from an agent-writable directory is code execution. It also has no file-restore semantics. |
| B-3 Embed DVC run-cache or Bazel REAPI | Artifact caches | **Rejected.** These are heavy dependencies that assume a VCS or a daemon, and their keys are command lines rather than agent contracts. |
| B-4 **Build a small CAS on the standard library** | ~1.5k LOC + tests | **Chosen.** It borrows the DVC, Bazel and Turborepo conventions, adds zero runtime dependencies, and gives exact control over the safety properties. |
| B-5 **Explicit reuse** (`ao run --reuse-from <run>`; dev-critic) | The operator names a trusted source run and outputs are copied from it | **Recorded as ADR-0019 alternative ALT-8.** It needs no hidden store and no key contract, but it is not what the brief asks for. It is the fallback if G0 shows a negligible would-hit rate. |

---

## 6. Orchestration landscape and competitor analysis

### 6.1 Comparison matrix (result reuse / memoization)

| Tool | Spec model | Reuse mechanism | Key | What is restored | Expiry / eviction | Concurrency | Known complaints |
|------|-----------|-----------------|-----|------------------|-------------------|-------------|------------------|
| **Airflow** | Python DAGs | None built in. Idempotency is the user's job; `ShortCircuitOperator`; Datasets/Assets | n/a | n/a | n/a | n/a | Re-running re-runs everything unless the user writes skip logic |
| **Prefect** | Python flows/tasks | `cache_policy` (INPUTS, TASK_SOURCE, …), `cache_key_fn`, `cache_expiration`, `refresh_cache` | inputs + task source | Persisted (serialized) Python result | Per-task expiration | Isolation levels / locks (3.x) | Unhashable inputs; hidden dependencies; needs result storage configured; deserializes persisted results |
| **Dagster** | Python assets | Data/code versions + staleness; legacy memoization is deprecated | `code_version` + upstream data versions | Nothing (skip or advise) | n/a | n/a | Heavy versioning model; staleness is advisory |
| **Temporal** | Code | Event-history replay within one execution | n/a | Activity results within the same execution | History retention | Durable | No cross-execution reuse |
| **Argo Workflows** | YAML/K8s | Template `memoize` | **user-written** key | Output parameters (ConfigMap) | `maxAge` | Per ConfigMap | Manual keys; size limits; stale results if the key misses an input |
| **Luigi** | Python | `complete()` = targets exist | existence | Nothing (skip) | n/a | n/a | Stale outputs are never re-run; ao's `skip_if_outputs_exist` has the same shape |
| **Step Functions** | ASL JSON | None (redrive) | n/a | n/a | n/a | n/a | DIY caching |
| **GitHub Actions** | YAML | `actions/cache` | **user-written** key + `hashFiles()` | Directories | 7-day unused eviction; size cap | Branch-scoped | Manual keys → stale or poisoned caches |
| **n8n** | Visual JSON | Pinned data (dev only) | n/a | Node output | n/a | n/a | Not for production |
| **Windmill** | Scripts/flows | `cache_ttl` | script hash + args | Result value | TTL | Server-side | Results only, not files |
| *Adjacent:* **Turborepo / Nx** | JSON graph | Local + remote cache; `--force` | declared inputs + env + deps | Declared outputs + logs | Size/age; optional HMAC signing | Content-addressed | Undeclared env → incorrect hits |
| *Adjacent:* **Bazel** | Starlark | Action cache + CAS | command + inputs + env digest | Declared outputs | LRU / remote | Hermetic sandbox | Steep learning curve |
| *Adjacent:* **DVC** | YAML stages | `dvc.lock` + run-cache | md5(deps/outs) + cmd | Declared outs | `dvc gc` | Content-addressed | Large-data hashing; cache growth |

### 6.2 Gap analysis

**What others do well:**

- content-addressed output reuse (Turborepo, Bazel, DVC);
- explicit expiry (Prefect, Argo, Windmill);
- forced refresh (Turborepo `--force`, Prefect `refresh_cache`);
- signed remote caches (Turborepo).

**Where they fall short for LLM-agent DAGs:**

1. Keys written by users omit inputs.
2. Value caches deserialize payloads.
3. Build tools assume hermetic, deterministic steps; agents read ambient context (repos, `CLAUDE.md`) and are non-deterministic.
4. Nobody separates avoided LLM spend from real spend.
5. Nobody offers a measure-before-trust mode.

**Known complaints that apply to us:** stale hits from undeclared inputs and env; poisoned shared caches; surprising semantics when caching is on by default; no way to re-roll a bad cached result.

### 6.3 Differentiation and positioning

```
We will:
- Match DVC / Turborepo in content-addressed input→output caching with exact, hash-verified
  restore of declared output files, plus single-entry re-roll (`ao cache rm`, then re-run).
- Beat Argo Workflows / GitHub Actions in key ergonomics: no hand-written keys — the key is
  derived from the task's declared contract, the exact executor argv, and the agent's ambient
  context files (CLAUDE.md / .claude/**), with repo HEADs and prior output content.
- Beat Prefect in payload safety: we restore bytes to spec-derived, non-sensitive paths; nothing
  is ever deserialized or executed from the cache directory.
- Beat all of them on trust-building: a shadow mode measures would-hit rates and avoidable spend
  before anyone lets a cached result stand in for an agent run.
- Avoid the complexity of Bazel (hermetic sandboxes, remote CAS/RBE) and Dagster (versioned
  asset staleness) — one local directory, double opt-in, provisional ABC seams.
```

Each positioning point maps to design decisions. The design follows from three facts about agents,
plus a guard against feature creep:

- **Agents are non-deterministic.** Hence double opt-in, the kill switch, shadow mode before `on`,
  and `ao cache rm` for re-rolling (D1, D26, D27).
- **Agents read ambient state.** Hence repo HEADs, prior outputs, the CLI fingerprint, and the
  worktree guard (D5–D7, D13).
- **The store is agent-writable.** Hence spec-derived destinations, sensitive-path refusal, total
  parsers, and verification of every byte (D17, D20, D28, D29).
- **Feature-creep guard.** Each new key component must name the stale-hit class it prevents, and
  remote, HMAC, dependency-invalidation and explain features stay non-MVP.

---
## 7. High-level design

### 7.1 Context (C4 level 1)

```mermaid
flowchart LR
  OP[Operator / CI / ao service] -- "ao run|resume --cache/--no-cache, AO_CACHE=on|off|shadow, cache.enabled/mode" --> AO[ao CLI + engine]
  AUTH[Workflow author] -- "defaults.cache / tasks[].cache = true (opt-in)" --> SPEC[(workflow spec)]
  SPEC --> AO
  AO -- "dispatch on miss" --> CLAUDE[claude CLI agent]
  AO -- "lookup / store / restore" --> RC[(workspace result cache<br/>.orchestrator/cache/)]
  AO -- "hash inputs + context files, write outputs, read HEADs" --> WS[(workspace files + git repos)]
  OP -- "ao cache ls|stats|show|prune|clear|verify|rm" --> RC
  DASH[ao ui dashboard] -- "reads state.json (result_cache records)" --> AO
  BENCH[ao-bench] -- "ao run --no-cache, AO_CACHE=0" --> AO
```

### 7.2 Containers and change set (C4 level 2)

| Container | Change | Notes |
|-----------|--------|-------|
| `agent_orchestrator.cache` (NEW) | All logic | §8.0 modules and owners |
| `models.py` | +~65 lines, additive (as built: +81/-1) | `TaskSpec.cache` and `WorkflowDefaults.cache` (both `StrictBool \| None`); `ResultCacheRecord` (with computed `hit` and `saved_tokens`) + outcome constants; `RunState.result_cache`; `is_current_result_cache_record()`. `models.py` never imports from `cache/`. |
| `specs/workflow.schema.json` | +2 properties | `defaults.cache`, `task.cache` (`boolean`) |
| `project_config.py` | +~45 lines (as built: +71/-1) | `CacheConfig` (bounded), `ProjectConfig.cache`, `_INIT_TEMPLATE` block |
| `engine.py` | net ≤ +110 formatted lines (measured ≈ +103); ≤ 12 added lines inside existing functions; every line ≤ 100 columns | ctor kwarg; `_RunContext` field; `_result_cache_lookup()` + `_result_cache_store()` private methods; one call site in prepare, one in settle; the existing stale-charge reversal extracted verbatim into `_reverse_stale_charge()` (net-negative inside `_prepare_and_maybe_dispatch`) and shared by the budget gate and the hit path |
| `executors/claude_cli.py` | refactor, behaviour-identical | extract pure `build_claude_argv(agent, prompt) -> list[str]`; `execute()` calls it |
| `cli.py` | ~+75 lines (as built: +115/-2; it also imports `cache.cli` eagerly and `status` catches `ValueError`) | `--cache/--no-cache` on run/resume; `_build_result_cache`; `add_typer(cache_app)`; summary lines and `report-usage` line, importing `cache.report` lazily and only when there are records |
| `runstate.py` | +~6 lines (as built: +12/-1) | `write_status` merges `cache.report.result_cache_status_fields(state)`, imported lazily and only when `state.result_cache` is non-empty |
| `usage.py` | +~30 lines (as built: +109/-23) | both dispatch-counting sites (group metrics and producer attribution) skip current hits, while a hit's real carried spend still counts; `UsageReport.result_cache` totals object; payload omits it when absent; lazy `cache.report` import |
| `outcomes.py` | +~6 lines (as built: +12/-3) | `_settle_reason(ts, *, state=None, tid=None)` returns `"cached"` for a current hit (lazy import) |
| `ui/runs.py` | +~14 lines (as built: +15) | `TaskStat.result_cache`, `RunDetail.result_cache` (nullable; lazy import) |
| `ui/files.py` | +~6 lines (as built: +13: casefolded comparison and a NUL guard) | the browser refuses paths under `<root>/.orchestrator/cache` |
| `ui/src/types.ts`, `RunDetail.tsx` | +~30 lines | types, "cached" tag, "Result cache" tile; bundle rebuilt |
| `bench/subjects.py` | +3 lines (as built: +9) | `--no-cache` + `AO_CACHE=0` |
| `.github/workflows/ci.yml` | +1 step (as built: +8 lines) | result-cache tests with `--cov-fail-under=85` for the package and a 90% floor per core module |

### 7.3 Component breakdown

```mermaid
flowchart TB
  subgraph cache["agent_orchestrator.cache (new)"]
    C[constants.py] --- SIO[safeio.py: safe open/read/walk, component checks, sensitive-path predicate]
    T[types.py: canonical_json, entry models + parse boundary, KeyRequest/CacheKey, LookupRequest/LookupOutcome/PendingStore/StoreResult, ResultCacheHook protocol, CacheStore + CacheAdmin ABCs, errors]
    S[settings.py: mode resolution + author policy]
    H[hashing.py: bounded file/dir digests]
    RS[repo_state.py: RepoHeadReader, WorktreeProbe]
    F[fingerprint.py: claude_cli executor fingerprint]
    K[keys.py: build_cache_key, key summary, component digests]
    E[eligibility.py: classification + check_eligibility]
    ST[store.py: LocalFsCacheStore]
    R[restore.py: capture_outputs / restore_outputs]
    RE[records.py: record builders]
    CO[coordinator.py: ResultCache implements ResultCacheHook]
    RP[report.py: status fields, summary line, usage counts, task views]
    CLI[cli.py: ao cache sub-app surface] -.lazy.-> OPS[cli_ops.py: command behaviour] --> ST & SW[restore_sweep.py]
  end
  ENG[engine.py] -- "ResultCacheHook (lazy import)" --> CO
  CO --> E & K & ST & R & RE & RS
  K --> H & F & SIO
  H --> SIO
  ST --> SIO & T
  R --> SIO & T
  SW --> SIO & T
  ARGV[executors/claude_cli.build_claude_argv] --> K
  RSTATE[runstate.write_status] --> RP
  AOCLI[cli.py] --> S & CO & RP & CLI
  UIR[ui/runs.py] --> RP
  US[usage.py] --> RP
  OUT[outcomes.py] --> RP
```

**Layering rules.**

- `cache/*` may import `models`, `artifacts`, `executors.prompt`, `executors.claude_cli`
  (`build_claude_argv` only), `isolation.git`, `usage.verdict_path_for`,
  `feedback.validate_run_id` (from `cli.py` only), and `agent_orchestrator.__version__`.
- `cache/*` must **never** import `engine`, `runstate`, `cli` or `ui`.
- `models.py` imports nothing from `cache/`.
- `project_config.py` imports only `cache/constants.py`, which is a leaf module.
- `cache/__init__.py` holds only a docstring.
- `engine.py` imports cache modules **only** under `TYPE_CHECKING`, or lazily inside its two
  private cache methods. This is what makes the off path import-free (§8.7.5).

### 7.4 Integration points

| Seam | Where | Direction |
|------|-------|-----------|
| Run-level mode | `cli._build_result_cache(cache_flag, workspace, wf)` → `Orchestrator(result_cache=...)` | CLI → engine |
| Lookup | `Orchestrator._prepare_and_maybe_dispatch` → `self._result_cache_lookup(...)` | engine → `ResultCacheHook.lookup` |
| Store | `Orchestrator._settle_completed_task` (`succeeded` branch) → `self._result_cache_store(...)` | engine → `ResultCacheHook.store_success` |
| Argv | `keys.build_cache_key` → `executors.claude_cli.build_claude_argv` | cache → executor (pure function) |
| Persistence | `RunState.result_cache[tid]`, written by the engine on the main thread | engine |
| Snapshot | `RunStateStore.write_status` → `cache.report.result_cache_status_fields` | runstate → cache |
| Summary | `cli._print_state` / `_print_status_snapshot` → `cache.report.format_summary_line` | CLI → cache |
| Usage | `usage.aggregate_usage` → `cache.report.current_hit`, `usage_counters` (lazy import) | usage → cache |
| Outcomes | `outcomes._settle_reason` → `cache.report.current_hit` | outcomes → cache |
| Dashboard | `ui.runs.RunRepository.detail` → `cache.report.task_view` / `run_block`; `ui.files` deny-list | ui → cache |
| Bench | `bench.subjects.AoWorkflowSubject.run` argv/env | bench → CLI |
| Management | `ao cache …` → `LocalFsCacheStore` (`CacheStore` + `CacheAdmin`) | CLI → store |

### 7.5 Plugin and extension strategy

- **Core (opinionated).** These are not configurable. They change only through
  `KEY_SCHEMA_VERSION`, the entry-directory version, or an ADR addendum.
  - the key document and its canonicalization;
  - the eligibility allowlist;
  - the restore protocol and sensitive-path refusal;
  - the accounting semantics.
- **Edges (extensible).**
  - **Engine seam.** `ResultCacheHook` is a Protocol with two methods that return value objects.
    The engine depends on the Protocol, not on `ResultCache`, which mirrors the
    `BudgetManager`/`Monitor` injection pattern. Tests inject fakes.
  - **Storage — PROVISIONAL.**
    - The interfaces are `CacheStore` (hot path: entries, blobs, touch, delete, `has_blob`,
      `maybe_enforce_limits`) and `CacheAdmin` (iteration, stats, prune, clear, verify).
    - Their **signatures are provisional for MVP** and are expected to change for a remote backend:
      a blob store with `missing(shas)`, `put(sha, size, src)` and `get(sha)`; an entry store; and
      optional admin.
    - Entries are already written as canonical JSON bytes, so a future signature covers stable
      bytes.
    - Remote backends need per-tenant namespacing and HMAC first (non-MVP 1–2).
  - **Executors.** `CACHEABLE_EXECUTORS` and `CACHEABLE_COMMAND_BASENAMES` are allowlists. Each
    cacheable executor must provide two things:
    - an argv builder (claude_cli: `build_claude_argv`; fake: none);
    - a fingerprint function (`fingerprint.py`).

    A new executor stays ineligible until both are reviewed.
  - **New task kinds or fields.** Tripwire tests U-E1, U-E2 and U-K8, plus the runtime
    unknown-field rules (§8.3).

### 7.6 Decision log

Each decision lists its alternatives and, in the last column, its link to manager proposals A–K,
to the Phase-4 findings in §23.4, or to the Rev 3 early-gate items in §23.5.

| ID | Decision | Alternatives | Rationale | Source |
|----|----------|--------------|-----------|--------|
| D1 | **Double opt-in; operator mode gate; author policy.** `mode` = CLI (`--cache`→on, `--no-cache`→off) > `AO_CACHE` (on/off/shadow; anything unknown, including `refresh`, → off + warning) > config (`cache.enabled: true` + `cache.mode`) > off. `policy(task)` = `task.cache` (an injected `true` is ignored) → `defaults.cache` → `DEFAULT_TASK_CACHE_POLICY`. That constant is **False**, is used only by `settings.task_cache_policy`, and is THE single flip point if the parent prefers "operator enabled ⇒ every eligible task cached unless `cache: false`" (pinned by test U-S4). Effective = mode ≠ off AND policy AND eligible. Tasks that are not opted in get no record and no event. | (a) Fill-in semantics like `--isolation`. (b) Spec-only opt-in. (c) Default on. (d) Rev 1's operator-only gate with the author default *allowed*. | Only the author can vouch that a task's whole effect is captured by its declared outputs (reviewer R1). Only the operator can decide that freezing one non-deterministic result is acceptable. Both must agree. `--no-cache` / `AO_CACHE=0` stay true kill switches, which keeps the bench force-off robust. | A (refined), Rev2: reviewer R1; Rev3: manager B (named default) |
| D2 | **Location `<ws>/.orchestrator/cache/`.** It is self-ignoring, holds `CACHEDIR.TAG` and `layout.json`, and is created with mode `0o700`. The root must not be a symlink, must be owned by the effective uid, must have no group/other write (`0o700` is restored if we own it), and must resolve inside the workspace. These checks run per operation. | `.ao/cache/` (committed config dir); XDG cache (cross-workspace, non-MVP); the run dir (not cross-run). | `.orchestrator/` is already engine-managed state: it is in `RESERVED_SHARED_PREFIXES`, discovery skips it, and isolated runs exclude it via `info/exclude`. Self-ignoring prevents committed blobs. | F + security S4 |
| D3 | **Key = sha256(canonical JSON v1)** over `{key_schema, agent, argv, executor_fingerprint, prompt, instruction, general_instructions, inputs, dynamic_inputs, outputs, repo_heads}` (§8.2). | Hash the raw dispatched prompt (absolute paths, no contents). | Covers everything the agent is told, location-independently, by content. | C |
| D4 | **The task id is NOT in the key**, and neither are the workflow id, run id, agent registry name, timeout or retries. | Include the task id. | Neither the prompt nor the argv contains it; tripwires U-K7 and U-K8a prove this. Outputs are in the key, so sharing requires identical declared files. The benefit is limited to renamed tasks and to two workflows producing the same files. **Correction (dev-critic):** built-in templates render `{{ instance_dir }}` into prompt and output paths, so **cross-instance hits do not happen** with them. The Rev 1 claim to the contrary was wrong. | decided; Rev2: critic #1 |
| D5 | **`repo_heads` in the key by default** (`{repo_id: sha \| "unborn"}`). Non-git repos are omitted. Git-ness is decided from the filesystem: a `.git` entry found walking up from the repo path **to the workspace root and never above it** (`find_git_toplevel`); the directory holding that entry is the repo toplevel. No probe that hides failures, and no private `GitRepo._run`. For a git repo, any failure (`GitError`/`OSError`/`RuntimeError`, short timeout) → uncacheable. Repository reads set `GIT_OPTIONAL_LOCKS=0`. A workspace nested inside a parent repository is therefore treated as non-git; that residual is documented and the banner warns about it (A-12). `include_repo_heads: false` drops the field, but the **HEAD-moved store guard stays active**. | Omit; always on with no opt-out. | Every prompt names the repos, so committed state is an undeclared input. `GitRepo.probe()` returns None both for "not a repo" and for failures, and a transient failure must not silently drop HEAD (reviewer R8). | D(i); Rev2: reviewer R1/R8, developer #1; Rev3: early-gate A2 (a `.git` in `$HOME` no longer makes plain project dirs "git") |
| D6 | **The prior content of each declared output is in the key** (`outputs: [{path, prior}]`). The settle-time recompute reuses the lookup-time priors (preseed). | Paths only. | Without it, in-place-update tasks get stale hits that **overwrite newer edits**. With `skip_if_outputs_exist: true`, priors are always `"absent"`. | new (Rev 1) |
| D7 | **The exact argv and an executor fingerprint are in the key.** argv = `build_claude_argv(effective_agent, normalized_prompt)`, a behaviour-identical extraction from `ClaudeCliExecutor.execute`; it is `null` for `fake`. The fingerprint (claude_cli) is `{cli_version, env, context_files}`: `claude --version` (memoized per binary identity: resolved path, mtime and size; failure → uncacheable), a small, closed allowlist of behaviour-relevant **non-secret** env vars, and digests or absence markers for `CLAUDE.md`, `CLAUDE.local.md`, `.mcp.json`, `.claude/settings*.json` and `.claude/{agents,commands,skills}` at the workspace root and on the path down to the agent cwd. | Rev 1 relied on a "bump `KEY_SCHEMA_VERSION`" convention; omit ambient files. | A missed version bump is a silently wrong success; an extra bump is just a miss. Hashing the real argv makes `EFFORT_MAX_TURNS` changes and flag-injection changes invalidate automatically. Context files steer every agent, and the primary consumer's root is not a git repo. | Rev2: critic #4, reviewer R7 |
| D8 | **Directory digest** = canonical JSON of `[["D", rel] \| ["F", rel, size, sha]]`, sorted by `os.fsencode(rel)`. A symlink or special file inside → uncacheable. `.git` entries, `<ws>/.orchestrator` and the task's own declared outputs are skipped, and **nothing else**: as built, a restore-staging name (`.ao-result-cache-*.tmp[.bak]`) is hashed like any other file (ADR-0019 D8 addendum, G2-S1). | Follow symlinks; hash `.git`. | Fail-closed, stable, no false impurity. | refines P-2 |
| D9 | **NFR-1 carve-out and lazy imports.** Only `agent_orchestrator.cache` reads artifact bytes. `engine.py` and the `ArtifactStore` ABC stay content-free. Modules the engine imports (`runstate`, `usage`) and the other read sides (`outcomes`, `cli`, `ui/runs`) import `cache.report` **inside the function, only when `state.result_cache` is non-empty**, so a cache-off process loads no cache module beyond `cache` + `cache.constants` (via `project_config`) and, on the CLI path, `cache.settings` and `cache.cli` (as built, DV-16). | Add reads to `ArtifactStore`; module-level imports of `cache.report`. | Keeps the engine invariant and its static audits intact, and makes the NFR-1 no-import claim literally true (I-1). | C; Rev3: early-gate C (NFR-1 literal) |
| D10 | **Allowlist eligibility (§8.3).** Every field of `TaskSpec`, `AgentSpec`, `WorkflowSpec` and `WorkflowDefaults` is classified, and tripwires fail on unclassified fields. **Runtime unknown-field rules** apply to `type(task).model_fields`, the **effective `AgentSpec`** (`unknown_agent_field`; any field outside `AGENT_KEY_FIELDS \| AGENT_NON_KEY_FIELDS` with a non-default value), `WorkflowSpec` and `WorkflowDefaults`. Further rules: `executor` ∈ {claude_cli, fake}; `command_template[0]` basename ∈ {claude} for claude_cli; the effective `model` must be resolved for claude_cli; integration must not be active; any verdict sidecar must be a declared output; the task must not be a verdict breaker's source. Control-file and sensitive outputs are checked on **resolved** paths during key build. | Denylist; lexical control-path checks. | A future kind or field is non-cacheable by default. A label (`executor: claude_cli`) is not proof that `claude` is the binary. An unresolved model (CLI default) cannot be keyed. | E; Rev2: critic #8c, reviewer R7b/R11, developer #7; Rev3: early-gate A3 (an unclassified `AgentSpec` field was silently unkeyed) |
| D11 | **Lookup placement.** After `should_skip`, the isolation resolution, `_apply_join`, the missing-inputs check and dynamic-input collection; **before** the budget gate. Implemented as `if self._result_cache is not None and self._result_cache_lookup(...): return DispatchPrep(signal="skipped")`. The cache contracts are imported lazily inside the method. | A new `"cached"` signal; lookup before `should_skip`. | Reuses the `skipped` handling of the fill loop. Barriers, `_ready_ids` and the wave scheduler are unchanged (verified by the reviewer). | B |
| D12 | **Hit bookkeeping is owned by the engine** (in `_result_cache_lookup`). `status="succeeded"`, `outputs_present=True`. **`dispatch_cycle` keeps its increment**, so R-21 stays monotonic and `ui/activity.locate_attempt_dirs` finds no transcript for a hit instead of a stale one. `attempts` is unchanged. `started_at` is set if unset, and `ended_at` is set. A `task.end` event is logged with `cached: true`. **A stale charged estimate of the previous dispatch cycle (`dispatch_cycle - 1`) is reversed** when a budget manager is present (crash→resume→hit), through the same extracted helper the budget gate uses (`_reverse_stale_charge`), which also emits the existing `budget.resume_reverse` event. `cumulative_*`, breakers and the quota timer are untouched. Both `usage` dispatch-counting sites skip current hits; a hit's real carried spend (hit after an earlier paid attempt) still counts. `outcomes._settle_reason` returns `"cached"`. | Rev 1: the decrement plus mutations in `cache.records`; `attempts=0`. | The reviewer and the critic showed that the decrement breaks R-21 and makes the dashboard show a failed attempt's transcript for a hit. State transitions belong to the engine, as with the ABC-injection pattern (reviewer R5). The budget leak was reviewer R3 / developer #14. | B/H; Rev2: reviewer R3/R4/R5/R6, critic #8a/#8b; Rev3: early-gate C (shared helper, event) |
| D13 | **Store at settle with three purity guards.** The store runs on the main thread at the top of the `succeeded` branch, via `_result_cache_store`. The pending token lives on `_RunContext.result_cache_pending`, is popped at every prepare, and is set only for a storable outcome (a storable miss or a shadow `would_hit`). The guards are: (1) the key recomputed with preseeded priors must equal the lookup key; (2) no repo HEAD moved between lookup and settle (independent of `include_repo_heads`); (3) **no tracked file outside the declared outputs changed** (tracked-only status set + mtime/size at lookup vs settle, `.orchestrator` excluded, `GIT_OPTIONAL_LOCKS=0`). The guard-3 snapshot is taken **lazily**, only once an outcome is known to be storable: a hit never takes it. A snapshot failure makes the outcome **not storable** (`store_reason: repo_worktree_probe_failed`); it never makes the task ineligible. `restore_failed`, `unsafe_path`, `store_unavailable` and `cache_disabled` misses are not storable. | Store on the worker; ad-hoc checks. | One mechanism per impurity class. Guard (3) spot-checks the author's opt-in claim for the most common violation: editing tracked files without committing (reviewer R1). | B, D(ii); Rev2: reviewer R1, developer #11; Rev3: manager B (lazy guard 3, lock-free reads) |
| D14 | **`RunState.result_cache: dict[str, ResultCacheRecord]`** (default `{}`). The record exposes the brief's fields `hit` and `saved_tokens` as pydantic computed fields, derived from `outcome` and the token split, so they can never disagree with the stored data (D35). A record is *current* iff `rec.dispatch_cycle == ts.dispatch_cycle`, and, for `hit`, also `ts.status == "succeeded"` and `rec.ended_at == ts.ended_at` (binding). `outcome`, `mode` and `mode_source` are open-set strings. `reason` is a closed vocabulary; `reason_detail` carries the variable part. | Rev 1 used cycle equality only, and the engine dropped records. | Staleness is derived, so no engine code runs in cache-off sessions. The `ended_at` binding survives the pre-existing `dispatch_cycle` reset in the missing-inputs branch (reviewer R4). | H; Rev2: reviewer R4, critic #9 |
| D15 | **Report surfaces are additive.** `status.json` keys and the CLI summary line are omitted when the run has no current records. `report-usage --json` omits its `result_cache` object when the scanned runs have no records. Dashboard payloads gain null keys, following the `integration: null` precedent. | Always emit keys. | Byte-identical `status.json` and CLI text when off (R-D7). | H; Rev2: reviewer R10 |
| D16 | **`saved_*` is an estimate**: the source entry's cumulative usage, including the source run's retries. It is labelled "est.", never netted into cost, and never fed to breakers. | Net savings out of cost. | Keeps E-9h3m7k's real-spend numbers exact. | H |
| D17 | **Store trust boundary.** The store receives only its own namespace. The one exception is `LocalFsCacheStore.for_workspace(ws, ...)`, which computes and validates its root. The ABCs are split into `CacheStore` (hot path) and `CacheAdmin` (maintenance), both **provisional**. `LocalFsCacheStore` derives from `CacheStore` when T-U7ckfd lands; T-HjxNQ0 adds the `CacheAdmin` base and its methods. Callers verify every byte. | A single 14-method ABC that takes `check_root(workspace)`. | Interface segregation. A remote backend need not implement admin operations. Matches D17's own rule (reviewer R9). | Rev2: reviewer R9, critic #6 |
| D18 | **Layout**: `entries/v1/<k[:2]>/<k>.json` (major version in the path), shared `blobs/<s[:2]>/<s>`, `tmp/`. Entries are canonical JSON bytes. Blobs are written before the entry, via tmp + `os.replace` with unique names; dedupe touches the blob's mtime. **Never overwrite or delete entries in another version directory.** The sweep's mark phase collects every 64-hex token from **every** entry file under `entries/**`, so blobs that newer versions reference are protected. | A single entries dir; overwrite of unparseable entries. | Stable and beta installs run side by side, and the service auto-resumes runs (critic #5). | G; Rev2: critic #5 |
| D19 | **Eviction.** LRU by entry mtime (touched on hit) down to 90% of `max_bytes`. TTL measured from `created_at`. Mark-and-sweep of blobs with a 1 h grace period. **Inline enforcement is bounded** (as built, ADR-0019 D19 addendum): a private per-phase `_Budget` over `entries/**` of any version and depth, applied to the size scan **and** to the prune it triggers, defers to `ao cache prune` (WARNING) when the store holds more than `INLINE_PRUNE_MAX_ENTRIES` entries, more than `INLINE_PRUNE_MAX_ENTRY_FILE_BYTES` of entry files (the mark phase reads every entry file), more than `INLINE_PRUNE_MAX_WALK_ITEMS` directory entries, or more than `INLINE_PRUNE_MAX_BLOBS` blobs. The mark phase fails closed (an unreadable entry file protects every blob) and the sweep re-checks a blob's age right before deleting it. `clear` creates its trash dir first and verifies the removal. | Full prune inline; unbounded lists. | A planted or huge cache must not stall the scheduler thread (security S7). The Rev 1 `clear` silently did nothing (security S5). | G; Rev2: security S5/S7; Rev3: manager B (byte bound) |
| D20 | **Restore.** Every output is staged first: a temp file in the destination's directory (`O_EXCL\|O_NOFOLLOW\|O_CLOEXEC`, `0o600`), with size and sha verified. Each destination is re-validated (`realpath(dest) == dest`, inside the workspace, not sensitive). Missing parent directories are created component by component, refusing symlinks. Then `os.replace`; as built, the mode (`mode & 0o755`) is applied with `fchmod` on the open staging descriptor after hash verification (never by path), and each existing destination is first hard-linked to a `.bak` so a failing rename rolls the earlier ones back (ADR-0019 D20 addendum; DV-11). Any failure → temps removed → miss (evict and delete the blob if corrupt). | Direct writes; exact modes. | Partial writes are never accepted, and no special or writable-by-others bits come from an agent-writable store. | G; Rev2: security S4 |
| D21 | **Entry metadata.** Only a non-sensitive key *summary* is stored; never the argv, the prompt template, `extra_args` or the key document. Every string and list in an entry is length-bounded. | Store the key document. | Secrets in argv. The key is one-way. | new |
| D22 | **Naming.** "Result cache", `ao cache`, `--cache`, `AO_CACHE`, `cache:`, `ResultCache`, `RunState.result_cache`, `cache.*` events. The factory is `ResultCache.from_settings`. The text `open(` and `.read(` never appear anywhere in `engine.py`, comments included, because the static NFR-1 audits regex the raw text. | `--result-cache` flags. | The parent fixed the flag names. The audits are regex-based (developer #4). | J; Rev2: developer #4 |
| D23 | **Bench forced off** via argv `--no-cache` plus env `AO_CACHE=0`. | One of the two. | Belt and braces. | P-7 |
| D24 | **Approval gates (E-Ag7Pw3).** The allowlist and the runtime unknown-field rules (task and workflow) reject new fields until they are classified as RULED. The seam comment and the merge checklist require **any approval or human gate to run before the lookup**, and G2 verifies this. | — | A hit must never bypass approval (security S6). | coordination; Rev2: security S6 |
| D25 | **Runs with `integration.active` are ineligible.** | Allow non-isolated tasks in mixed runs. | The checkout HEAD moves at barriers. **Recorded tension:** if isolation becomes the default (roadmap §3.4), the eligible set shrinks. The executor-level alternative (ADR-0019 ALT-7) is the migration path. | new; Rev2: critic #7 |
| D26 | **Shadow mode.** `shadow`: full lookup, but no restore. A valid entry whose blobs are present records `would_hit` (with the `saved_*` estimate) and emits `cache.would_hit`. The task dispatches normally and still stores. **`refresh` is deferred (Rev 3, non-MVP 14):** re-rolling one result is `ao cache rm <key>` followed by a re-run. | Ship `on` only; keep `refresh`. | Measure before trusting (critic STRATEGIC #1). A run-wide overwrite mode adds a third lookup semantics for little value once single-entry `rm` exists. | Rev2: critic #1/#2; Rev3: manager B |
| D27 | **`ao cache rm <key\|prefix>`.** The prefix is validated with `KEY_PREFIX_RE.fullmatch` before any path is built and must match exactly one entry. **`rm --run R --task T` is deferred (Rev 3, non-MVP 15)**: it reads the agent-writable `state.json`, an extra trust boundary. | Clear everything; wait for the TTL; the run form. | Single-entry invalidation (critic MUST #2) with the smallest trust surface. | Rev2: critic #2; Rev3: manager B |
| D28 | **Hostile data is parsed by total functions.** One parse boundary (`types.parse_entry_bytes`) maps *any* failure (including `RecursionError` and `UnicodeDecodeError`) to `CacheIntegrityError`. Models use `AwareDatetime`, finite and bounded numbers (`allow_inf_nan=False`, `le=`), and `max_length` on every string and list. All internal cache-file reads go through `safeio` (`O_NOFOLLOW\|O_NONBLOCK\|O_CLOEXEC` + `S_ISREG`). | `except ValueError` only. | Verified exploits: nested JSON → `RecursionError`; naive datetimes → `TypeError`; `inf` → an unreadable `state.json`; a FIFO at an entry path → a hang (security S1, reviewer R2, developer #6). | Rev2: security S1 |
| D29 | **Sensitive destinations are refused.** An output whose resolved workspace-relative path has a component in `SENSITIVE_PATH_COMPONENTS` or a basename in `SENSITIVE_BASENAMES` (§8.1.5), **matched case-insensitively** (as built, ADR-0019 D29 addenda; DV-6), makes the task uncacheable (`sensitive_output`). Designed lists: components `{.git, .claude, .github, .gitlab, .husky, .ao, .orchestrator}`, basenames `{CLAUDE.md, CLAUDE.local.md, AGENTS.md, .mcp.json, .envrc}`; extended at G2 with the CI, IDE and dev-container sinks. `restore_outputs` re-checks this. | Rely on spec-derived paths only. | An in-workspace symlink `out/a.md → .git/hooks/pre-commit` turns a restore into code execution. A restored `CLAUDE.md` steers the next agent (security S3). | Rev2: security S3 |
| D30 | **Miss diagnostics.** `cache.miss` carries `components` (the first 12 hex characters of the sha256 of each top-level key-document field), so an operator can diff two runs and see which component changed. | Opaque misses. | Diagnosability (reviewer NIT). | Rev2 |
| D31 | **One safe-I/O module (`cache/safeio.py`) plus an AST guard test.** The guard rejects `pickle`, `marshal`, `shelve`, `eval`, `exec`, `subprocess` with `shell=True`, and any bare `open(` / `os.open(` in `cache/` outside `safeio.py`. | Per-module copies of the safe-open logic. | One audited I/O choke point (security S9, reviewer NIT). | Rev2 |
| D32 | **Engine-facing error boundary.** `ResultCache.lookup` and `store_success` never raise to the engine. Expected failures (`OSError`, `CacheError`, `ValidationError`, `GitError`, `ValueError`, `TypeError`, `RecursionError`, `OverflowError`) become a miss or skip with an ERROR log. Any other exception logs ERROR with the traceback, **disables the cache for the rest of the run** (`cache.disabled`) and returns a miss/skip. `ResultCache(strict=True)`, used in tests, re-raises. | Rev 1: propagate unexpected errors. | A cache bug must not kill a long, paid run, or lose a paid success before it is saved (developer #9, reviewer R2). The error is logged loudly, not swallowed. | Rev2: developer #9, reviewer R2 |
| D33 | **An unsafe cache path is never followed or evicted.** Every store operation, including `delete_entry`, `touch_entry`, `has_blob`, `read_blob` and `delete_blob`, runs the root and component checks first. A symlinked shard, entry directory or entry raises `CacheUnsafePathError` (reason `unsafe_path`), distinct from `CacheIntegrityError`. The coordinator turns it into a miss that is **not storable and never evicted**, plus a `cache.corrupt` WARNING. | Treat it as corruption and evict (Rev 2). | Rev 2 evicted every integrity error while `delete_entry` skipped the checks, so `unlink` could follow a planted link (CWE-59). | Rev3: early-gate A4 |
| D34 | **G0 is a post-merge, operator-owned measurement.** The epic delivers a runnable protocol (`docs-md/result-cache-g0-protocol.md`), the measurement fields (`ao report-usage --json` `result_cache` object, `ao cache stats --json`) and a report template, smoke-validated on a fake workflow. Executing G0 needs multi-day shadow runs of a real consumer workflow with operator consent (finplan), and does **not** block epic closure. | Run G0 inside the epic on this repo's workflows (Rev 2). | This repo has no representative workload: `specs/self-dev/` holds only agent and reposet files, built-in templates use per-instance output paths, and the bench forces the cache off. | Rev3: manager A5 |
| D35 | **Field contract and container name.** Every per-task surface (`ResultCacheRecord`, `status.json` task view, dashboard task payload) exposes exactly `hit`, `key`, `saved_cost_usd`, `saved_tokens` (input + output) and `saved_seconds`; run-level and cross-run surfaces expose `hits`, `saved_cost_usd`, `saved_tokens` and `saved_seconds`. Splits such as `saved_input_tokens` are additive. The container is named `result_cache`, not the brief's `cache`. | Use `cache` as the container; expose only the split token fields. | `cache_*` names already mean Claude **prompt**-cache counters in usage, status and the dashboard (`cache_read_input_tokens`, `cache_hit_rate`, `CacheDetails`). A `cache` object beside them would be read as prompt-cache data. The manager flags the name to the parent (OQ-7). | brief #6; Rev3: manager B |

### 7.7 Threat model

**Trust boundary.**

- The cache directory is **agent-writable**: agents run as the same user with the workspace as
  their cwd. It is the same trust domain as `state.json` and `.orchestrator/runs/`.
- **Keys are not secrets.** They appear in entry filenames, `status.json`, `state.json` and event
  logs, so tampering requires no key computation (security S2).
- The defences below cover corruption, accidents, persistent planted links, hostile data, resource
  exhaustion and execution sinks.
- They do **not** cover a deliberate same-uid attacker who rewrites an entry and its blobs, or who
  races the cache I/O. That requires HMAC with a key outside the agent's reach, plus OS sandboxing
  (non-MVP 2 and 9).

| # | Threat | Vector | Mitigation | Test |
|---|--------|--------|------------|------|
| M-1 | Path traversal on restore (CWE-22) | Manifest names `../escape` or an absolute path | Destinations come **only** from `store.resolve(declared_output)`; the manifest is used only as a set to compare against; a mismatch is a miss plus evict. | ADV-1 |
| M-2 | Path splicing | Tampered sha or key; CLI argument | `fullmatch` against a hex regex before any path is built; `entry.key` must equal the lookup key and the filename. (The `state.json`-sourced `rm --run` form is deferred.) | ADV-2, U-ST3 |
| M-3 | Corrupt or tampered blob | Bit rot; edits | Size and sha are verified while staging; failure → miss, evict, delete the blob, `cache.corrupt`. | ADV-3 |
| M-4 | Link following inside the cache dir (CWE-59) | Symlinked root, `.orchestrator`, shard dir, `entries/v1` dir, entry or blob | Root ownership and mode check plus a per-operation component `lstat` walk under the root, **including before `unlink` and `utime`**; `O_NOFOLLOW` opens; walks skip and report symlinks; an unsafe path is never evicted (D33). | ADV-4, ADV-4b, U-ST15 |
| M-5 | Link following at inputs or outputs | Output symlink; symlink inside an input dir | `resolve()` containment; capture refuses non-regular files; a symlink inside an input dir makes the task uncacheable; restore re-validates `realpath(dest) == dest`. | ADV-5 |
| M-6 | Hang on a special file | FIFO or device as an input, an output, **or an internal cache file** | `O_NONBLOCK` + `S_ISREG` before any read, everywhere (`safeio`). | ADV-6a/b/c |
| M-7 | Resource exhaustion (CWE-400) | Huge inputs, outputs or entry JSON; a lying `size` field; a huge planted cache | Caps; stat before read; reads bounded at size + 1; bounded inline maintenance. | U-H*, U-SM*, ADV-7 |
| M-8 | Dangerous mode bits | `mode: 0o4777` in an entry | Schema `≤ 0o777`, so such an entry is corrupt; restore applies `& 0o755`. | ADV-8a/b |
| M-9 | Unsafe deserialization | pickle, eval, YAML loading | None used; the AST guard enforces this. | U-AST |
| M-10 | Secret retention and exposure | Outputs persist after deletion; dashboard browsing | Root mode `0o700`; self-`.gitignore`; entries hold only a summary; the **dashboard refuses `.orchestrator/cache`**; `rm`/`clear`; digests are display-only; retention is documented. | U-ST1, D-1 |
| M-11 | Semantic stale hit | Ambient state; alias drift; non-determinism | Double opt-in; HEADs, priors, argv and fingerprint in the key; three store guards; TTL; `shadow`; `ao cache rm`. | integration |
| M-12 | Approval bypass (E-Ag7Pw3) | A gated task served from the cache | Allowlist plus runtime unknown-field rules; gate ordering before the lookup; G2 check. | U-E*, G2 |
| M-13 | Hostile-data crash (CWE-248/674/20) | Nested JSON, naive datetimes, `inf`, unbounded strings | Total parse boundary; strict and bounded models. | ADV-9 (hostile corpus) |
| M-14 | Execution sink via restore (CWE-94/73) | An output resolves into `.git/hooks`, `.claude/`, `CLAUDE.md`, CI config | Sensitive-path refusal at key build **and** at restore, case-insensitive, with the extended list of §8.1.5 (as built, DV-6). | ADV-10 |
| M-15 | Terminal escape injection (CWE-150) | Entry strings printed by `ao cache ls`/`show` | Control characters stripped in text output. | E-7 |
| M-16 | A cache bug kills a run | Any unexpected exception | Error boundary; cache disabled for the run; strict mode in tests. | U-CO14, I-25 |
| — | **Residual: deliberate poisoning** by a same-uid writer | Rewrite an entry and its blobs (keys are visible) | **Not mitigated in MVP.** Recommend `--no-cache` for untrusted workflows, plus `ao cache verify`/`clear`. HMAC is non-MVP 2 and does not stop an attacker who can read its key. | — |
| — | **Residual: active TOCTOU race** | A concurrent malicious writer swaps a path component between check and use | **Not mitigated in MVP.** Persistent plants are caught. `dir_fd` walking is non-MVP 9. | — |
| — | **Residual: user-level context** | `~/.claude/settings.json`, `~/.claude/CLAUDE.md` | **Not fingerprinted.** The CLI version and TTL bound the impact (non-MVP 8). | — |
| — | **Residual: offline guessing** | `key_summary.digests` of low-entropy inputs | Documented. The digests are display-only and live in the same trust domain as the inputs. | — |
| — | **Residual: dirty tracked edits** (accepted, Rev 3; blind spots added at G1b N-7, N-9) | Uncommitted edits to tracked files that are not declared inputs exist **before** the lookup | **Not in the key.** Guard 3 only catches edits made *during* the run. A hit can replay a result computed against a different dirty tree. Guard 3 is also blind to `assume-unchanged` / `skip-worktree` files and to untracked or non-git writes, and after a quota or self-heal requeue the next lookup re-takes the baseline, so tracked edits of the abandoned partial attempt are absorbed. Documented in §17 and R-20; opt in only tasks whose inputs are declared (R-A3). | — |
| — | **Residual: nested workspace** | The workspace root sits inside a parent repository | Treated as non-git (D5): HEAD not keyed, guards 2 and 3 inactive. The banner warns (A-12, R-19). | U-G7 |
| — | **Residual: provider and endpoint environment not in the key** (accepted at G2, SEC-11, R-A1) | `ANTHROPIC_BASE_URL`, `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX`, the provider region | **Not in `CLAUDE_FINGERPRINT_ENV_VARS`**: a hit can be served across a backend or endpoint switch with an unchanged model alias. Same model name, operator-controlled, non-secret; adding them would change GV-1. Pass `--no-cache` (or `ao cache clear`) when switching provider or endpoint. | — |
| — | **Residual: git `filter.<x>.clean` executed by the guard-3 probe** (accepted at G2, G1b S-3, G2 sec S2, R-A2) | `git status` in `WorktreeProbe.snapshot` runs a `filter.<x>.clean` command named in the agent-writable `.git/config` (re-confirmed on git 2.39.5; `core.fsmonitor` and `core.untrackedCache` are neutralised by `git_read_env`, DV-4) | **Not mitigated.** Precondition: cache on **and** the task opted in **and** a writer of the git config or `.gitattributes` that cannot already run code (a tool-restricted agent without Bash). An agent with Bash has the same power, and the isolation integrator already runs `git status` in agent-touched trees. Use `--no-cache` for untrusted repositories. | — |
| — | **Residual: agent-writable cache directory; retention** (accepted at G1b S-5, G2) | Same-uid forgery of an entry and its blobs; stored outputs outlive their deletion; shadow mode also stores copies; forged future `created_at` never expires; restored mode `0o000`; crash leftovers `.ao-result-cache-*.tmp[.bak]` survive `ao cache clear` (the sweep visits only output directories of entries still in the store) | The `on` banner carries `(agent-writable; avoid for untrusted prompts)` (DV-15); root `0o700`, self-`.gitignore`, dashboard refusal; `ao cache verify`, `rm`, `clear`, `prune` (R-A4, R-A6). | — |

---
## 8. Low-level design

### 8.0 Package layout, ownership, dependency rules

**Code-block convention (Rev 3).** Blocks fenced as `python` are copy-ready: names, signatures
and bodies are binding, and every line fits the 100-column `ruff` limit (run `ruff format` after
pasting). Blocks fenced as `text` are pseudocode: the logic, names and order are binding, the
syntax is not.

```text
src/agent_orchestrator/cache/
  __init__.py      docstring only, no imports                                       (T-FJH6LI)
  constants.py     every named constant: defaults, bounds, schemas, modes, the author-policy
                   default, AgentSpec field sets, REASON_*, EVENT_*     (T-FJH6LI, commit 1)
  safeio.py        safe open/read/create, dir-chain checks, sensitive-path predicate,
                   control-char stripping; its own small exception types (T-FJH6LI, commit 2)
  types.py         canonical_json (the ONE canonical serializer); entry models + parse
                   boundary; Digest/BlobRef/report dataclasses; KeyRequest/KeyDeps/CacheKey;
                   LookupRequest/PendingStore/LookupOutcome/StoreResult; ResultCacheHook
                   Protocol; CacheStore + CacheAdmin ABCs; error types  (T-FJH6LI, commit 3)
  settings.py      ResultCacheSettings, resolve_result_cache_settings, task_cache_policy,
                   opted_in_count                                                   (T-28J9oR)
  cli.py           `ao cache` Typer surface; import-light (skeleton T-28J9oR; commands T-6tRKml)
  cli_ops.py       behaviour of the `ao cache` commands, imported lazily    (T-6tRKml, as built)
  restore_sweep.py prune-side sweep of stale restore staging files          (T-6tRKml, as built)
  hashing.py       HashBudget, digest_path (bounded, safe)                          (T-8tr1H4)
  repo_state.py    find_git_toplevel, nested_repo_marker, RepoHeadReader, WorktreeProbe
                                                                                    (T-8tr1H4)
  fingerprint.py   CliVersionReader, claude_cli_fingerprint, guarded_resolve/_abs   (T-uoYW6b)
  keys.py          build_cache_key, summary_from_doc, component digests             (T-uoYW6b)
  eligibility.py   field classification tables, Eligibility, check_eligibility      (T-QgQy08)
  store.py         LocalFsCacheStore: CacheStore half + is_expired                  (T-U7ckfd)
                   + CacheAdmin base and methods, bounded maybe_enforce_limits      (T-HjxNQ0)
  restore.py       capture_outputs, restore_outputs                                 (T-u3jG8F)
  records.py       ResultCacheRecord builders (no RunState/TaskRunState mutation)   (T-gDNjN2)
  coordinator.py   ResultCache (implements ResultCacheHook)                         (T-gDNjN2)
  report.py        current_records, current_hit, task_view, run_block, status fields,
                   format_summary_line, usage totals (pure)                         (T-eyn5UG)
src/agent_orchestrator/executors/claude_cli.py
                   + pure build_claude_argv(agent, prompt) extracted from execute() (T-OeRYSO)
```

**T-FJH6LI commit numbering** is the same everywhere (tickets, §22, EPIC): commit 1 =
`__init__.py` + `constants.py`; commit 2 = `safeio.py`; commit 3 = `types.py`, the test fakes,
the store contract suite, the hostile corpus and the AST guard. T-28J9oR needs commit 1. Every
other cache task needs commit 3 (the whole of T-FJH6LI).

Import graph (acyclic). `constants` is a leaf.

| Module | Imports |
|--------|---------|
| `safeio` | `constants` |
| `types` | `constants`, `safeio`, `pydantic`, `models` (existing types only). `models.ResultCacheRecord`, which T-28J9oR adds, is imported **only under `TYPE_CHECKING`** (annotations are postponed), so T-FJH6LI has no dependency on T-28J9oR. |
| `hashing` | `safeio`, `types` |
| `repo_state` | `constants`, `types`, `isolation.git` (public `GitRepo` methods only) |
| `fingerprint` | `hashing`, `safeio`, `types` |
| `keys` | `constants`, `hashing`, `fingerprint`, `types`, `models`, `executors.prompt`, `executors.claude_cli` |
| `eligibility` | `constants`, `settings`, `models`, `usage` (`verdict_path_for`) |
| `store`, `restore` | `safeio`, `types` |
| `records` | `models`, `types` |
| `coordinator` | all of the above, plus `artifacts` and `agent_orchestrator.__version__` |
| `report` | `models`, `constants` |
| `settings` | `constants`; imports `project_config.CacheConfig` lazily |
| `cli` | `constants` at module level; everything else lazy (an AST test pins it). As built: `cli_ops` -> `store`, `restore_sweep`, `settings`, `safeio`, `types` (lazy); `restore_sweep` -> `safeio`, `constants`, `types` |

`executors/claude_cli.py` imports nothing from `cache/`. `models.py` imports nothing from
`cache/`. `runstate`, `usage`, `outcomes`, `cli` and `ui/runs` import `cache.report` **inside the
function, and only when `state.result_cache` is non-empty** (D9), so a cache-off process loads at
most `cache` + `cache.constants` (via `project_config`) and, on the CLI path, `cache.settings` and
`cache.cli` (as built: `cli.py` registers the `ao cache` group eagerly, DV-16).

### 8.1 Module M1 — spec, config and settings (`models.py`, schema, `project_config.py`, `cache/settings.py`, `cache/constants.py`, `cli.py` flags)

**Module definition.**

| | |
|---|---|
| **Purpose** | Declare the spec, config and runtime switches, and resolve the run-level **mode** and the per-task **author policy**. |
| **Inputs** | The CLI flag (`bool \| None`), `os.environ`, `ProjectConfig.cache`, `WorkflowSpec.defaults.cache` and `TaskSpec.cache`. |
| **Outputs** | `ResultCacheSettings`, warnings, `task_cache_policy(...) -> bool`, and `opted_in_count(workflow) -> (n, m)`. |
| **Dependencies** | pydantic, Typer. Nothing from the engine. |

#### 8.1.1 `models.py` additions (exact)

```python
from pydantic import StrictBool, model_validator  # added to the existing pydantic import

# --- E-Rc4Hk8 result cache (ADR-0019). Outcome values the engine writes (open set on read).
RESULT_CACHE_HIT: Literal["hit"] = "hit"
RESULT_CACHE_WOULD_HIT: Literal["would_hit"] = "would_hit"
RESULT_CACHE_MISS: Literal["miss"] = "miss"
RESULT_CACHE_INELIGIBLE: Literal["ineligible"] = "ineligible"
# Record bounds (ADR-0019 D28): an inf/NaN or unbounded value must never reach state.json.
RESULT_CACHE_MAX_TEXT = 256
RESULT_CACHE_MAX_REASON = 64
RESULT_CACHE_MAX_USD = 1_000_000.0
RESULT_CACHE_MAX_TOKENS = 10**12
RESULT_CACHE_MAX_SECONDS = 10**8
RESULT_CACHE_KEY_PATTERN = r"^[0-9a-f]{64}$"


class TaskSpec(BaseModel):
    ...
    # Result cache (E-Rc4Hk8, ADR-0019 D1): AUTHOR opt-in. True = the author vouches that this
    # task's whole effect is captured by its declared outputs. None = inherit defaults.cache,
    # then cache.constants.DEFAULT_TASK_CACHE_POLICY (False). False = never. It only matters
    # when the OPERATOR enabled the result cache (--cache / AO_CACHE / cache.enabled). An
    # emit_tasks-injected task's True is ignored (narrow-only). StrictBool: the JSON schema is
    # not packaged, so pydantic is the real gate.
    cache: StrictBool | None = None


class WorkflowDefaults(BaseModel):
    ...
    cache: StrictBool | None = None  # workflow-wide author opt-in default for TaskSpec.cache


class ResultCacheRecord(BaseModel):
    """One task's result-cache outcome for its most recent lookup (E-Rc4Hk8, ADR-0019 D14).

    Lives on RunState.result_cache keyed by task id, never on TaskRunState (prepare_resume
    replaces it wholesale). Written ONLY by engine.py on the main thread. `outcome`, `mode`
    and `mode_source` are open-set strings (SpawnRecord.origin precedent). Read only through
    `is_current_result_cache_record`; a non-current record is ignored by every reader.
    `source_run_id` is display-only provenance from an agent-writable entry: never a path.
    `hit` and `saved_tokens` are the parent brief's exact fields (ADR-0019 D35). They are
    DERIVED by the validator below on every construction or load, never trusted from input.
    """

    outcome: str  # hit | would_hit | miss | ineligible
    mode: str  # on | shadow
    mode_source: str  # cli | env | config
    reason: str | None = Field(default=None, max_length=RESULT_CACHE_MAX_REASON)
    reason_detail: str | None = Field(default=None, max_length=RESULT_CACHE_MAX_TEXT)
    key: str | None = Field(default=None, pattern=RESULT_CACHE_KEY_PATTERN)
    hit: bool = False  # derived: outcome == "hit"
    dispatch_cycle: int = Field(ge=0)  # the TaskRunState.dispatch_cycle it belongs to
    at: str = Field(max_length=RESULT_CACHE_MAX_REASON)  # ISO-8601 UTC, engine clock
    # Hit binding: equals ts.ended_at while the record is current (ADR-0019 D14).
    ended_at: str | None = Field(default=None, max_length=RESULT_CACHE_MAX_REASON)
    stored: bool = False
    store_reason: str | None = Field(default=None, max_length=RESULT_CACHE_MAX_REASON)
    saved_cost_usd: float = Field(
        default=0.0, ge=0, le=RESULT_CACHE_MAX_USD, allow_inf_nan=False
    )
    saved_tokens: int = Field(default=0, ge=0)  # derived: input + output
    saved_input_tokens: int = Field(default=0, ge=0, le=RESULT_CACHE_MAX_TOKENS)
    saved_output_tokens: int = Field(default=0, ge=0, le=RESULT_CACHE_MAX_TOKENS)
    saved_seconds: float = Field(
        default=0.0, ge=0, le=RESULT_CACHE_MAX_SECONDS, allow_inf_nan=False
    )
    source_run_id: str | None = Field(default=None, max_length=RESULT_CACHE_MAX_TEXT)

    @model_validator(mode="after")
    def _derive_brief_fields(self) -> ResultCacheRecord:
        # D35: never trust these two from input. model_copy(update=...) skips validators, so
        # callers must not change outcome or the token split through it (the engine never does).
        self.hit = self.outcome == RESULT_CACHE_HIT
        self.saved_tokens = self.saved_input_tokens + self.saved_output_tokens
        return self


class RunState(BaseModel):
    ...
    # Result cache records (E-Rc4Hk8): task id -> most recent lookup/store outcome. The default
    # {} keeps every pre-epic state.json loadable (NFR-6).
    result_cache: dict[str, ResultCacheRecord] = {}


def is_current_result_cache_record(rec: ResultCacheRecord, ts: TaskRunState | None) -> bool:
    """True iff *rec* describes *ts*'s current incarnation (ADR-0019 D14).

    Shared by every reader (status.json, summary line, usage, outcomes, dashboard) so that
    they can never disagree.
    """
    if ts is None or rec.dispatch_cycle != ts.dispatch_cycle:
        return False
    if rec.outcome == RESULT_CACHE_HIT:
        bound = rec.ended_at is not None and rec.ended_at == ts.ended_at
        return ts.status == "succeeded" and bound
    return True
```

#### 8.1.2 Workflow schema additions (`specs/workflow.schema.json`)

`defaults` and `$defs.task` are `additionalProperties: false`, so each needs this property:

```json
"cache": {
  "type": "boolean",
  "description": "Result cache AUTHOR opt-in (E-Rc4Hk8; NOT Claude prompt caching). true = this task's whole effect is captured by its declared outputs, so an identical previous success may be reused when the OPERATOR enabled the result cache (--cache / AO_CACHE / .ao/config.yaml cache.enabled). Task value beats defaults.cache; unset defaults to false; false always means never."
}
```

#### 8.1.3 Mode and policy precedence (complete)

| Layer | Source | Values | Effect |
|-------|--------|--------|--------|
| 1 | `ao run/resume --cache` / `--no-cache` | flag | mode `on` / `off` (kill switch) |
| 2 | `AO_CACHE` | `1/true/yes/on` → `on`; `0/false/no/off` → `off`; `shadow`; empty or unset → next layer; anything else, including the deferred `refresh`, → **`off`** plus one stderr WARNING | mode |
| 3 | `.ao/config.yaml` `cache.enabled` + `cache.mode` | `enabled: true` → `cache.mode` (default `on`); `enabled: false` → `off`; absent → next layer | mode |
| 4 | default | — | `off` |
| author | `tasks[].cache` → `defaults.cache` → `DEFAULT_TASK_CACHE_POLICY` (**False**) | `true` / `false` / unset | opt-in; an injected task's `true` is ignored |

**Effective rule.** A task is cached only when all three hold:

```
mode ≠ off  AND  policy(task)  AND  eligible(task)
```

**Modes:**

- `on`: look up and restore on a hit.
- `shadow`: look up, but **never** restore; record `would_hit`.

Both modes store on success. To re-roll one result, run `ao cache rm <key>` and re-run the task;
`refresh` is deferred (non-MVP 14).

**The flip point.** `DEFAULT_TASK_CACHE_POLICY` (`cache/constants.py`) is the value of an unset
`tasks[].cache` and an unset `defaults.cache`. It is `False`, which makes the cache double opt-in.
If the parent prefers "operator enabled ⇒ every eligible task is cached unless it says
`cache: false`", this one constant changes (with an ADR-0019 addendum); test U-S4 pins it, and
nothing else in the code compares against `True`/`False` defaults.

**Other entry points:**

- **`ao resume`.** The mode is resolved fresh on every invocation, so a run can change mode between
  sessions. Hit tasks stay `succeeded`.
- **`ao new --run`, dashboard launches and service-triggered runs.** These inherit layers 2–4.
- **Committed config.** If `.ao/config.yaml` is committed, `cache.enabled: true` enables the cache
  for every clone and every service run. The authoring guide warns about this (dev-critic #3). The
  resolved mode and its source are recorded on every record (`mode`, `mode_source`) and printed in
  the stderr banner.

#### 8.1.4 `CacheConfig` (`project_config.py`)

```python
class CacheConfig(BaseModel):
    """`cache:` block: the RESULT cache (E-Rc4Hk8, ADR-0019), not Claude prompt caching."""

    enabled: StrictBool | None = None
    mode: Literal["on", "shadow"] = MODE_ON
    max_bytes: int = Field(default=DEFAULT_CACHE_MAX_BYTES, ge=1, le=MAX_CONFIG_BYTES)
    # None -> min(DEFAULT_CACHE_MAX_ENTRY_BYTES, max_bytes); see effective_max_entry_bytes.
    max_entry_bytes: int | None = Field(default=None, ge=1, le=MAX_CONFIG_BYTES)
    # None (YAML null) = never expire.
    ttl_days: int | None = Field(default=DEFAULT_CACHE_TTL_DAYS, ge=1, le=MAX_TTL_DAYS)
    include_repo_heads: StrictBool = DEFAULT_CACHE_INCLUDE_REPO_HEADS
    max_input_bytes: int = Field(
        default=DEFAULT_CACHE_MAX_INPUT_BYTES, ge=1, le=MAX_CONFIG_BYTES
    )
    max_input_files: int = Field(
        default=DEFAULT_CACHE_MAX_INPUT_FILES, ge=1, le=MAX_INPUT_FILES_LIMIT
    )

    @model_validator(mode="after")
    def _entry_fits_total(self) -> CacheConfig:
        if self.max_entry_bytes is not None and self.max_entry_bytes > self.max_bytes:
            raise ValueError(
                f"cache.max_entry_bytes ({self.max_entry_bytes}) must be <= "
                f"cache.max_bytes ({self.max_bytes})"
            )
        return self

    @property
    def effective_max_entry_bytes(self) -> int:
        """Unset -> min(64 MiB, max_bytes), so a small max_bytes alone never errors."""
        if self.max_entry_bytes is not None:
            return self.max_entry_bytes
        return min(DEFAULT_CACHE_MAX_ENTRY_BYTES, self.max_bytes)


class ProjectConfig(BaseModel):
    ...
    cache: CacheConfig = CacheConfig()
```

`project_config.py` adds `Field` and `StrictBool` to its pydantic import, `Literal` from `typing`,
and imports the named defaults from `cache.constants` (a leaf module).

**As built (DV-2, DV-3).** `mode` defaults to the literal `"on"` (pinned equal to `MODE_ON` by a
test), and the template line is `mode: "on"` **quoted**: under YAML 1.1 a bare `on` loads as
boolean `true`, which fails `CacheConfig` validation. A user who writes bare `mode: on` gets a
loud `ConfigError` (fail-closed). The block below is the shipped template text.

Add this block to `_INIT_TEMPLATE` after the isolation block:

```yaml
# --- Result cache (E-Rc4Hk8): reuse an identical, previously SUCCESSFUL task's declared output
# files across runs instead of re-dispatching the agent. NOT Claude prompt caching. Off by default.
# DOUBLE opt-in: the operator enables it here / via AO_CACHE / --cache, AND the workflow author
# opts tasks in with `defaults.cache: true` or `tasks[].cache: true`. Agent output is
# non-deterministic: a hit replays ONE earlier result. Committing `enabled: true` turns it on for
# every clone and service run of this repo.
# cache:
#   enabled: false            # AO_CACHE=1|0|shadow / --cache / --no-cache (CLI/env win)
#   mode: "on"                # "on" | "shadow" (measure only); quote it, bare on = YAML true
#   max_bytes: 1073741824     # total size cap; least-recently-used entries evicted to 90%
#   max_entry_bytes: null     # results bigger than this are not stored
#                             # (default min(64 MiB, max_bytes))
#   ttl_days: 30              # entries older than this are misses (null = never expire)
#   include_repo_heads: true  # key includes HEAD of each git repo in the repo set
#   max_input_bytes: 536870912  # per-lookup hashing cap (bigger => task not cacheable)
#   max_input_files: 20000      # per-lookup file-count cap for directory inputs
# Manage it with `ao cache ls|stats|show|prune|clear|verify|rm`.
```

#### 8.1.5 `cache/constants.py` (complete list; no literals elsewhere)

```python
import re

# ---- operator mode (D1, D26)
ENV_CACHE = "AO_CACHE"
MODE_OFF = "off"
MODE_ON = "on"
MODE_SHADOW = "shadow"
ENV_ON_VALUES = frozenset({"1", "true", "yes", "on"})
ENV_OFF_VALUES = frozenset({"0", "false", "no", "off"})
SOURCE_CLI, SOURCE_ENV, SOURCE_CONFIG, SOURCE_DEFAULT = "cli", "env", "config", "default"
# THE author-policy flip point (D1): the policy of a task whose task.cache AND defaults.cache
# are both unset. False = double opt-in. Changing it needs an ADR-0019 addendum (test U-S4).
DEFAULT_TASK_CACHE_POLICY = False

# ---- layout (D2, D18)
CACHE_DIR_PARTS = (".orchestrator", "cache")
ENTRIES_DIR = "entries"
ENTRIES_VERSION_DIR = "v1"
BLOBS_DIR = "blobs"
TMP_DIR = "tmp"
TRASH_DIR_PREFIX = "trash-"
ENTRY_SUFFIX = ".json"
TMP_SUFFIX = ".tmp"
GITIGNORE_NAME = ".gitignore"
GITIGNORE_BODY = "# ao result cache -- never commit\n*\n"
CACHEDIR_TAG_NAME = "CACHEDIR.TAG"
CACHEDIR_TAG_BODY = (
    "Signature: 8a477f597d28d172789f06886806bc55\n"
    "# This file is a cache directory tag created by ao (result cache).\n"
)
LAYOUT_FILE = "layout.json"
LAYOUT_SCHEMA = "ao.result-cache.layout/v1"
ENTRY_SCHEMA = "ao.result-cache.entry/v1"
DIR_DIGEST_SCHEMA = "ao.result-cache.dir/v1"
DIR_ENTRY_DIR, DIR_ENTRY_FILE = "D", "F"
KEY_SCHEMA_VERSION = 1
CACHE_DIR_MODE, TMP_FILE_MODE = 0o700, 0o600
RESTORED_MODE_MASK, STORED_MODE_MASK, GROUP_OTHER_WRITE_BITS = 0o755, 0o777, 0o022

# ---- validation: always .fullmatch() (or .finditer() for the byte pattern)
SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}")
KEY_PREFIX_RE = re.compile(r"[0-9a-f]{4,64}")
HEX64_TOKEN_RE_BYTES = re.compile(rb"[0-9a-f]{64}")  # sweep mark phase over raw entry bytes
SHARD_CHARS = 2

# ---- bounds (D19, D28)
HASH_CHUNK_BYTES = 1024 * 1024
MAX_ENTRY_FILE_BYTES = 1024 * 1024
MAX_LAYOUT_FILE_BYTES = 4096
MAX_PATH_CHARS = 4096
MAX_TEXT_CHARS = 256
MAX_REASON_CHARS = 64
MAX_LIST_ITEMS = 4096
MAX_ENTRY_COST_USD = 1_000_000.0
MAX_ENTRY_TOKENS = 10**12
MAX_ENTRY_SECONDS = 10**8
MAX_ENTRY_ATTEMPTS = 10**6
EVICT_LOW_WATER_RATIO = 0.9
INLINE_PRUNE_MAX_ENTRIES = 5000
INLINE_PRUNE_MAX_ENTRY_FILE_BYTES = 64 * 1024**2  # the mark phase reads every entry file
# As built (G1a SEC-01/02): inline maintenance bounds EVERYTHING it touches, not only v1 entries.
INLINE_PRUNE_MAX_WALK_ITEMS = 100_000  # directory entries visited
INLINE_PRUNE_MAX_BLOBS = 50_000
BLOB_SWEEP_GRACE_SECONDS = 3600
TMP_SWEEP_GRACE_SECONDS = 3600

# ---- config defaults and limits (FR-15)
DEFAULT_CACHE_MAX_BYTES = 1024**3
DEFAULT_CACHE_MAX_ENTRY_BYTES = 64 * 1024**2
DEFAULT_CACHE_TTL_DAYS = 30
DEFAULT_CACHE_INCLUDE_REPO_HEADS = True
DEFAULT_CACHE_MAX_INPUT_BYTES = 512 * 1024**2
DEFAULT_CACHE_MAX_INPUT_FILES = 20_000
MAX_CONFIG_BYTES = 2**50
MAX_TTL_DAYS = 36_500
MAX_INPUT_FILES_LIMIT = 10**7

# ---- external processes (D5, D7, D13)
CACHE_GIT_TIMEOUT_SECONDS = 10
GIT_OPTIONAL_LOCKS_VAR = "GIT_OPTIONAL_LOCKS"  # set to "0" for every repository read:
GIT_OPTIONAL_LOCKS_OFF = "0"  # parallel agents must never race our index.lock
# As built (G1a SEC-04/SEC-03): every repository read drops repository-selecting variables
# (GIT_SCRUBBED_ENV_VARS, and GIT_CONFIG_KEY_*/GIT_CONFIG_VALUE_* by prefix) and neutralises
# the config that makes `git status` execute a program, via GIT_CONFIG_COUNT (git >= 2.31).
GIT_SCRUBBED_ENV_VARS = frozenset(
    {
        "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
        "GIT_PREFIX", "GIT_CEILING_DIRECTORIES", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS",
        "GIT_EXTERNAL_DIFF", "GIT_PAGER", "GIT_SSH", "GIT_SSH_COMMAND",
    }
)
GIT_SCRUBBED_ENV_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
GIT_NEUTRALISED_CONFIG = (("core.fsmonitor", "false"), ("core.untrackedCache", "false"))
CLI_VERSION_TIMEOUT_SECONDS = 10
MAX_CLI_VERSION_CHARS = 256

# ---- key document (D3-D8)
UNBORN_HEAD, PRIOR_ABSENT = "unborn", "absent"
KIND_FILE, KIND_DIR, KIND_ABSENT = "file", "dir", "absent"
KEY_PLACEHOLDER_ID, KEY_PLACEHOLDER_TIMEOUT = "-", 1
COMPONENT_DIGEST_CHARS = 12
RESTORE_TMP_PREFIX = ".ao-result-cache-"
RESTORE_BACKUP_SUFFIX = ".bak"  # the hard-link backup of a replaced destination (as built)
DIR_WALK_SKIP_NAMES = frozenset({".git"})
# AgentSpec classification (D7, D10). Tripwire U-K8: KEY | NON_KEY == AgentSpec.model_fields.
# keys.py projects KEY into the key; eligibility.py rejects any OTHER non-default field.
AGENT_KEY_FIELDS = frozenset(
    {
        "executor",
        "command_template",
        "prompt_template",
        "context_window",
        "extra_args",
        "model",
        "effort",
        "max_turns",
        "working_dir",
        "disallowed_tools",
        "forced_disallowed_tools",
        "exclude_dynamic_system_prompt_sections",
    }
)
AGENT_NON_KEY_FIELDS = frozenset({"forbidden_task_models"})  # validation-only, never in argv

# ---- eligibility and restore (D10, D29)
CACHEABLE_EXECUTORS = frozenset({"claude_cli", "fake"})
CACHEABLE_COMMAND_BASENAMES = frozenset({"claude"})
MODEL_FLAGS = ("--model", "-m")
# As built (G1a SEC-06, G2-S3 / SEC-15): matched case-insensitively (`safeio.is_sensitive_rel_path`).
SENSITIVE_PATH_COMPONENTS = frozenset(
    {
        ".git", ".claude", ".github", ".gitlab", ".husky", ".ao", ".orchestrator",
        ".githooks", ".circleci", ".vscode", ".devcontainer", ".cursor", ".idea",
    }
)
SENSITIVE_BASENAMES = frozenset(
    {
        "CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", ".mcp.json", ".envrc",
        ".gitlab-ci.yml", "Jenkinsfile", ".travis.yml", "azure-pipelines.yml",
        "bitbucket-pipelines.yml", ".pre-commit-config.yaml", ".gitmodules", ".gitattributes",
    }
)

# ---- CLI (as built: also EXIT_*, SCHEMA_*, CLI_MAX_* and RESTORE_SWEEP_MAX_DIRS, T-6tRKml)
DEFAULT_LS_LIMIT = 50
LS_SORT_KEYS = ("lru", "created", "size")
EXIT_OK, EXIT_ERROR, EXIT_USAGE = 0, 1, 2
SCHEMA_LS = "ao.result-cache.ls/v1"  # likewise SCHEMA_STATS, _SHOW, _RM, _PRUNE, _CLEAR, _VERIFY
# REASON_* (§8.3.3, §8.6.4) and EVENT_* (§15): every string listed there is a constant here.
```

#### 8.1.6 `cache/settings.py`

```text
@dataclass(frozen=True)
class ResultCacheSettings:
    mode: str              # MODE_OFF | MODE_ON | MODE_SHADOW
    source: str            # SOURCE_*
    max_bytes: int; max_entry_bytes: int; ttl_days: int | None
    include_repo_heads: bool; max_input_bytes: int; max_input_files: int

FUNCTION resolve_result_cache_settings(cli_flag, environ, cfg) -> (ResultCacheSettings, warnings):
    warnings = []
    IF cli_flag is True:  mode, source = MODE_ON, SOURCE_CLI
    ELIF cli_flag is False: mode, source = MODE_OFF, SOURCE_CLI
    ELSE:
        raw = environ.get(ENV_CACHE, "").strip().lower()
        IF raw in ENV_ON_VALUES: mode, source = MODE_ON, SOURCE_ENV
        ELIF raw in ENV_OFF_VALUES: mode, source = MODE_OFF, SOURCE_ENV
        ELIF raw == MODE_SHADOW: mode, source = MODE_SHADOW, SOURCE_ENV
        ELIF raw != "":                                      # includes the deferred "refresh"
            mode, source = MODE_OFF, SOURCE_ENV              # fail-closed
            warnings.append(f"{ENV_CACHE}={raw!r} not recognised (use 1|0|shadow); "
                            "result cache OFF")
        ELIF cfg is not None and cfg.enabled is True: mode, source = cfg.mode, SOURCE_CONFIG
        ELIF cfg is not None and cfg.enabled is False: mode, source = MODE_OFF, SOURCE_CONFIG
        ELSE: mode, source = MODE_OFF, SOURCE_DEFAULT
    base = cfg if cfg is not None else CacheConfig()
    RETURN ResultCacheSettings(mode, source, base.max_bytes, base.effective_max_entry_bytes,
                               base.ttl_days, base.include_repo_heads, base.max_input_bytes,
                               base.max_input_files), warnings

FUNCTION task_cache_policy(task, workflow, *, injected: bool) -> bool:    # AUTHOR layer (D1)
    flag = task.cache
    IF injected AND flag is True: flag = None        # agent-authored manifests may only narrow
    IF flag is not None: RETURN flag
    IF workflow.defaults.cache is not None: RETURN workflow.defaults.cache
    RETURN DEFAULT_TASK_CACHE_POLICY                  # False; THE flip point (§8.1.3, U-S4)

FUNCTION opted_in_count(workflow) -> (int, int):     # static tasks only (banner)
    n = sum(task_cache_policy(t, workflow, injected=False) for t in workflow.tasks)
    RETURN n, len(workflow.tasks)
```

#### 8.1.7 CLI flags and the ONE shared helper (`cli.py`)

The same option on `run` **and** `resume`, appended as the last parameter:

```python
    cache: bool | None = typer.Option(
        None,
        "--cache/--no-cache",
        help=(
            "Result cache: reuse an identical, previously successful task's declared "
            "outputs across runs instead of re-dispatching the agent (NOT Claude prompt "
            "caching). Default off. Only tasks the workflow opts in (defaults.cache / "
            "tasks[].cache: true) are cached. --no-cache is a kill switch that wins over "
            "env/config. Env: AO_CACHE (1|0|shadow). Config: cache.enabled / cache.mode."
        ),
    ),
```

The shared helper (`cli.py` has `from __future__ import annotations`; `ResultCacheHook` is imported
under `TYPE_CHECKING`):

```python
def _build_result_cache(
    cache_flag: bool | None, workspace: str, wf: WorkflowSpec
) -> ResultCacheHook | None:
    """ONE helper for `ao run` and `ao resume` (FR-1). None => the engine runs no cache code."""
    from .cache.constants import MODE_OFF
    from .cache.settings import opted_in_count, resolve_result_cache_settings

    cfg = _load_project_config_or_exit()
    settings, warnings = resolve_result_cache_settings(
        cache_flag, os.environ, cfg.cache if cfg else None
    )
    for w in warnings:
        typer.echo(f"WARNING: {w}", err=True)
    if settings.mode == MODE_OFF:
        return None
    from .cache.coordinator import ResultCache  # construction half: T-o95l1M

    rc = ResultCache.from_settings(workspace_root=workspace, settings=settings)
    n, m = opted_in_count(wf)
    note = (
        f"{n} of {m} static task(s) opted in"
        if n
        else "but no task opts in (set defaults.cache: true or tasks[].cache: true)"
    )
    typer.echo(
        f"Result cache: {settings.mode} (source={settings.source}), {note}, at {rc.root}",
        err=True,
    )
    for w in rc.warnings:  # e.g. a .git marker above the workspace root (A-12)
        typer.echo(f"WARNING: {w}", err=True)
    return rc
```

T-28J9oR delivers the resolution half: everything up to `return None` for the off mode, and a
final `return None` in place of the construction half, because construction is not wired yet.
T-o95l1M completes the helper and passes `result_cache=_build_result_cache(cache, workspace, wf)`
to both `Orchestrator(...)` constructions.

**As built (DV-15).** The banner in mode `on` ends with ` (agent-writable; avoid for untrusted
prompts)` (`cli._RESULT_CACHE_TRUST_NOTE`), because `on` serves stored outputs and the cache
directory is writable by the agent. The shadow banner does not carry it (shadow never serves a
hit). The helper imports `MODE_ON` for this. Both `ao run` and `ao resume` bind the helper's
result and pass it as `result_cache=`.

**Subtasks.**

1. Model fields, the record model (with its derived fields) and the predicate.
2. Schema changes.
3. `CacheConfig` and its template.
4. `settings.py`, including `DEFAULT_TASK_CACHE_POLICY` use.
5. CLI option and the helper's resolution half.
6. `cache/cli.py` skeleton and `app.add_typer(cache_app, name="cache")`.
7. Tests.

**Edge cases.**

- `AO_CACHE=" Shadow "` → shadow.
- `AO_CACHE=2` → off plus a warning.
- `AO_CACHE=refresh` → off plus a warning (`refresh` is deferred).
- `--cache` with `AO_CACHE=shadow` → on (the CLI wins).
- `cache.mode: shadow` without `enabled: true` → off.
- `cache.mode: refresh` in config → `ConfigError` (not in the `Literal`).
- `cache.max_bytes: 1000000` alone → valid. The entry cap is clamped to 1 000 000.
- `ttl_days: 0` → `ConfigError`. `ttl_days: 40000` → `ConfigError` (above `MAX_TTL_DAYS`).
- `cache: "yes"` in a spec → rejected by `StrictBool` even without the JSON schema.
- An injected task with `cache: true` and `defaults.cache: false` → not opted in.
- A record loaded with `"hit": true, "outcome": "miss"` → `hit` is re-derived as `false`.

### 8.2 Module M2 — key building (`safeio.py`, `hashing.py`, `repo_state.py`, `fingerprint.py`, `keys.py`, `claude_cli.build_claude_argv`)

**Module definition.**

| | |
|---|---|
| **Purpose** | Turn "what this agent would be asked to do" into a deterministic sha256 key, a non-sensitive summary and per-component digests. |
| **Inputs** | `KeyRequest` and `KeyDeps`. |
| **Outputs** | `CacheKey`, or `UncacheableError(reason, detail)`. |
| **Dependencies** | `hashlib`, `json`, `os`, `stat`, `shutil`, `subprocess` (CLI version only, `shell=False`), `models`, `executors.prompt.build_prompt`, `executors.claude_cli.build_claude_argv`, `isolation.git.GitRepo`. |

#### 8.2.1 Contracts (`types.py`)

```python
@dataclass(frozen=True)
class Digest:
    kind: str  # KIND_FILE | KIND_DIR
    sha256: str
    size: int  # file: bytes; dir: manifest entries


class UncacheableError(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        # reason is a REASON_* constant; detail is clipped to MAX_TEXT_CHARS
        ...


@dataclass(frozen=True)
class KeyRequest:
    task: TaskSpec
    agent: AgentSpec  # the EFFECTIVE agent (models.resolve_effective_agent)
    workspace_root: str  # LocalFsArtifactStore.root (resolved)
    artifact_store: LocalFsArtifactStore  # resolve() is the engine's path guard
    cache_root: str
    # ABSOLUTE paths, from Orchestrator._resolve_general_instructions
    general_instruction_paths: tuple[str, ...]
    dynamic_input_paths: tuple[str, ...]  # raw strings exactly as handed to the executor
    repo_paths: Mapping[str, str]  # ABSOLUTE ctx.repo_paths
    # PRECOMPUTED by the coordinator; the HEAD guard (D13) compares against them
    repo_heads: Mapping[str, str]
    include_repo_heads: bool
    # resolved engine-read control files (router/loop/breaker verdicts, prompt_path)
    control_paths_abs: frozenset[str]
    environ: Mapping[str, str]  # for the fingerprint env allowlist
    max_input_bytes: int
    max_input_files: int


@dataclass(frozen=True)
class KeyDeps:
    # fingerprint.CliVersionReader.version (memoized by binary identity); tests inject a fake
    cli_version_of: Callable[[str], str]


@dataclass(frozen=True)
class CacheKey:
    key: str
    summary: KeySummary
    output_paths: tuple[str, ...]  # normalized relative paths, sorted
    output_abs: Mapping[str, str]  # rel -> absolute destination (spec-derived, resolved)
    preseed: Mapping[str, Digest | None]  # abs output path -> prior digest (None = absent)
    components: Mapping[str, str]  # top-level doc field -> sha256[:12] (miss diagnostics)
    cli_version: str | None  # executor_fingerprint.cli_version (None for fake); provenance
```

#### 8.2.2 `safeio.py` (shared by hashing, fingerprint, store and restore)

```python
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
O_SAFE_READ = os.O_RDONLY | _NOFOLLOW | _NONBLOCK | _CLOEXEC
O_SAFE_CREATE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC


class SafeIOError(Exception): ...


class NotRegularFileError(SafeIOError): ...  # symlink (ELOOP), dir, FIFO, device, socket


class TooLargeError(SafeIOError): ...


class UnsafePathError(SafeIOError): ...  # symlinked or foreign component, outside the root


def open_regular_read(path: str) -> int:
    """fd for a REGULAR file; never follows a final symlink, never blocks on a FIFO."""
    try:
        fd = os.open(path, O_SAFE_READ)
    except OSError as e:  # FileNotFoundError passes through unchanged
        if e.errno == errno.ELOOP:
            raise NotRegularFileError(path) from e
        raise
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise NotRegularFileError(path)
    return fd


def read_bounded(path: str, max_bytes: int) -> bytes:
    """open_regular_read + an fstat size check; never reads more than max_bytes + 1."""


def create_exclusive(path: str, mode: int = TMP_FILE_MODE) -> int:
    """os.open(path, O_SAFE_CREATE, mode)."""


def check_dir_chain(root: str, path: str) -> None:
    """Every EXISTING component from root down to path: lstat, S_ISDIR, not a symlink."""


def ensure_dir_chain(root: str, path: str, mode: int) -> None:
    """os.mkdir one component at a time; on EEXIST, lstat-check that component."""


def check_root_dir(root: str, *, workspace_root: str) -> None:
    """Root: not a symlink, S_ISDIR, st_uid == geteuid(), no group/other write (chmod 0o700
    instead when we own it), realpath inside the workspace; `.orchestrator` not a symlink."""


def is_sensitive_rel_path(rel: str) -> bool:
    """Any component in SENSITIVE_PATH_COMPONENTS, or basename in SENSITIVE_BASENAMES.

    As built: compared case-insensitively (`casefold()`), so `.GIT/hooks` and `docs/claude.md`
    are sensitive (ADR-0019 D29 addendum)."""


def posix_rel(path: str, base: str) -> str:
    """normpath(relpath(path, base)) with "/" separators."""


def strip_control_chars(text: str) -> str:
    """Remove C0/C1 control characters and DEL from text printed by the CLI (CWE-150).

    As built: also bidi and zero-width format characters, U+061C, U+00AD and the Unicode tag
    block U+E0000-U+E007F (G1a SEC-14, G2-N1)."""
```

#### 8.2.3 Bounded hashing (`hashing.py`)

```text
class HashBudget:
    def __init__(self, max_bytes: int, max_files: int): self.bytes = 0; self.files = 0; ...
    def charge(self, nbytes, nfiles):
        self.bytes += nbytes; self.files += nfiles
        IF self.bytes > self.max_bytes OR self.files > self.max_files:
            RAISE UncacheableError(REASON_INPUT_TOO_LARGE, f"bytes={self.bytes} files={self.files}")

FUNCTION hash_regular_file(abs_path, budget) -> Digest:
    TRY fd = safeio.open_regular_read(abs_path)
    EXCEPT FileNotFoundError: RAISE UncacheableError(REASON_INPUT_MISSING, abs_path)
    EXCEPT NotRegularFileError: RAISE UncacheableError(REASON_INPUT_NOT_REGULAR, abs_path)
    EXCEPT OSError: RAISE UncacheableError(REASON_INPUT_UNREADABLE, abs_path)
    TRY:
        size = os.fstat(fd).st_size; budget.charge(size, 1)              # bounded BEFORE reading
        h = sha256(); n = 0
        WHILE chunk := os.read(fd, HASH_CHUNK_BYTES):
            n += len(chunk)
            IF n > size: RAISE UncacheableError(REASON_INPUT_UNSTABLE, abs_path)
            h.update(chunk)
        IF n != size: RAISE UncacheableError(REASON_INPUT_UNSTABLE, abs_path)
        RETURN Digest(KIND_FILE, h.hexdigest(), n)
    EXCEPT OSError: RAISE UncacheableError(REASON_INPUT_UNREADABLE, abs_path)
    FINALLY os.close(fd)

FUNCTION hash_directory(abs_dir, budget, *, exclude_abs, skip_abs) -> Digest:
    entries = []; stack = [abs_dir]
    WHILE stack:
        d = stack.pop()
        TRY it = os.scandir(d) EXCEPT OSError: RAISE UncacheableError(REASON_INPUT_UNREADABLE, d)
        WITH it:
            FOR e IN it:
                IF e.name in DIR_WALK_SKIP_NAMES OR e.path in skip_abs OR e.path in exclude_abs: CONTINUE
                rel = posix_rel(e.path, abs_dir)
                IF e.is_symlink(): RAISE UncacheableError(REASON_INPUT_NOT_REGULAR, e.path)
                IF e.is_dir(follow_symlinks=False): budget.charge(0, 1); entries.append([DIR_ENTRY_DIR, rel]); stack.append(e.path)
                ELIF e.is_file(follow_symlinks=False):
                    dg = hash_regular_file(e.path, budget); entries.append([DIR_ENTRY_FILE, rel, dg.size, dg.sha256])
                ELSE: RAISE UncacheableError(REASON_INPUT_NOT_REGULAR, e.path)     # FIFO/socket/device: never opened
    entries.sort(key=lambda x: os.fsencode(x[1]))
    RETURN Digest(KIND_DIR, sha256(canonical_json({"schema": DIR_DIGEST_SCHEMA, "entries": entries}).encode("ascii")).hexdigest(), len(entries))

FUNCTION digest_path(abs_path, budget, *, exclude_abs=frozenset(), skip_abs=frozenset()) -> Digest:
    TRY st = os.lstat(abs_path) EXCEPT FileNotFoundError: RAISE UncacheableError(REASON_INPUT_MISSING, abs_path)
                                EXCEPT OSError: RAISE UncacheableError(REASON_INPUT_UNREADABLE, abs_path)
    IF S_ISDIR: RETURN hash_directory(...); IF S_ISREG: RETURN hash_regular_file(...)
    RAISE UncacheableError(REASON_INPUT_NOT_REGULAR, abs_path)
```

**Key hygiene (documented, accepted).** Directory walks skip only `.git`, `<ws>/.orchestrator`
and the task's own declared outputs. Editor and OS noise inside a declared input directory
(`.DS_Store`, `__pycache__`, swap files) is hashed, so it causes **false misses**, never false
hits. Authors who see such misses declare narrower inputs.

#### 8.2.4 Repo state (`repo_state.py`)

Only public `GitRepo` methods are used (`rev_parse`, `current_branch`, `status_porcelain`). Never
the private `GitRepo._run`, and never `GitRepo.probe()`, which returns None for both "not a repo"
and "git failed" and uses the 300 s default timeout (reviewer R8).

```text
FUNCTION find_git_toplevel(path, *, workspace_root) -> str | None:
    """The directory holding the nearest `.git` entry (dir or file; lstat only), walking up
    from *path* to *workspace_root* INCLUSIVE and never above it (D5). None = not a git repo.
    A `.git` in $HOME or any other ancestor of the workspace is never consulted."""
    p = normpath(abspath(path)); ws = normpath(abspath(workspace_root))
    IF p != ws AND NOT p.startswith(ws + os.sep):
        RAISE UncacheableError(REASON_PATH_REJECTED, "repo path outside the workspace")
    WHILE True:
        IF os.path.lexists(join(p, ".git")): RETURN p
        IF p == ws: RETURN None
        p = dirname(p)

FUNCTION nested_repo_marker(workspace_root) -> str | None:     # banner warning only (A-12)
    """First ancestor ABOVE the workspace root holding a `.git` entry, else None."""

class RepoHeadReader:
    def __init__(self, *, workspace_root, runner=None, hooks_dir=None,
                 timeout=CACHE_GIT_TIMEOUT_SECONDS): ...       # tests inject runner + hooks_dir
    FUNCTION read(self, repo_paths: Mapping[str, str]) -> dict[str, str]:
        out = {}
        FOR rid IN sorted(repo_paths):
            top = find_git_toplevel(repo_paths[rid], workspace_root=self._ws)
            IF top is None: CONTINUE                                   # non-git: omitted
            TRY:
                g = self._repo(top)   # memo per toplevel: GitRepo(top, timeout=self._timeout,
                                      #   runner=self._runner, hooks_dir=self._hooks_dir,
                                      #   env={GIT_OPTIONAL_LOCKS_VAR: GIT_OPTIONAL_LOCKS_OFF})
                sha = g.rev_parse("HEAD")
                IF sha is None:
                    IF g.current_branch() is not None: out[rid] = UNBORN_HEAD; CONTINUE
                    RAISE UncacheableError(REASON_REPO_HEAD_UNAVAILABLE, rid)
                out[rid] = sha
            EXCEPT (GitError, OSError, RuntimeError, subprocess.TimeoutExpired) as e:
                RAISE UncacheableError(REASON_REPO_HEAD_UNAVAILABLE, f"{rid}:{type(e).__name__}")
        RETURN out

class WorktreeProbe:                         # store guard (3), D13 -- called LAZILY (§8.6.2)
    FUNCTION snapshot(self, repo_paths, workspace_root, exclude_abs) -> frozenset[tuple]:
        """Tracked changes only: {(toplevel, path, index_code, worktree_code, mtime_ns|None,
        size|None)} from status_porcelain(top, untracked=False) of every git repo in the set,
        EXCLUDING paths under <ws>/.orchestrator and paths in *exclude_abs* (the outputs)."""
        tops = {find_git_toplevel(p, workspace_root=workspace_root) for p in repo_paths.values()}
        entries = set()
        FOR top IN sorted(tops - {None}):
            FOR se IN self._repo(top).status_porcelain(top, untracked=False):   # same GitRepo
                a = normpath(join(top, se.path))                  # settings as RepoHeadReader
                IF a IN exclude_abs OR under(a, join(workspace_root, CACHE_DIR_PARTS[0])):
                    CONTINUE
                TRY st = os.lstat(a); sig = (st.st_mtime_ns, st.st_size)
                EXCEPT FileNotFoundError: sig = (None, None)
                entries.add((top, se.path, se.index, se.worktree, *sig))
        RETURN frozenset(entries)
    # Any failure (GitError, OSError, RuntimeError, TimeoutExpired, UncacheableError) ->
    #   UncacheableError(REASON_REPO_WORKTREE_PROBE_FAILED). The coordinator maps it to a
    #   NOT-STORABLE outcome (store_reason), never to an ineligible task or a failed lookup.
```

**As built (DV-4).** The child environment is not `{GIT_OPTIONAL_LOCKS: "0"}` alone (which would
also replace `PATH` and `HOME`). `repo_state.git_read_env()` starts from `os.environ`, drops
`GIT_SCRUBBED_ENV_VARS` and the `GIT_CONFIG_KEY_*` / `GIT_CONFIG_VALUE_*` pairs, sets
`GIT_OPTIONAL_LOCKS=0`, and injects `core.fsmonitor=false` and `core.untrackedCache=false`
through `GIT_CONFIG_COUNT` (git >= 2.31; older git ignores it). `RepoHeadReader` and
`WorktreeProbe` share one private `_GitReaders` base, and a HEAD is read once per toplevel within
a `read()` and never cached across calls. A `filter.<x>.clean` driver reached through
`.gitattributes` cannot be neutralised by a config key (accepted residual R-A2).

**Behaviour.** A `.git` marker that is not a working repository (a stray file, an empty
directory) makes `rev_parse` fail, so the task is uncacheable (`repo_head_unavailable`), which is
fail-closed. A workspace nested inside a parent repository has no marker at or below its root, so
it is treated as non-git; `nested_repo_marker` feeds the banner warning (A-12).

#### 8.2.5 Executor fingerprint (`fingerprint.py`) and argv (`claude_cli.build_claude_argv`)

```text
# executors/claude_cli.py (T-OeRYSO) -- behaviour-identical extraction; execute() now calls it.
def build_claude_argv(agent: AgentSpec, prompt: str) -> list[str]:
    """The exact argv ClaudeCliExecutor.execute runs for *agent* and *prompt* (pure; no I/O).
    Shared with the result-cache key (ADR-0019 D7) so ANY argv-construction change -- EFFORT_MAX_TURNS,
    flag injection, tool policy, stream flags -- changes cache keys automatically."""
    argv = [(a.replace("{prompt}", prompt) if "{prompt}" in a else a)
            for a in agent.command_template] + agent.extra_args
    IF agent.model and "--model" not in argv and "-m" not in argv:
        argv += ["--model", agent.model]
    IF "--max-turns" not in argv:
        mt = agent.max_turns    # explicit max_turns wins
        IF mt is None AND agent.effort: mt = EFFORT_MAX_TURNS[agent.effort]   # lazy import kept
        IF mt is not None: argv += ["--max-turns", str(mt)]
    argv = _apply_tool_policy(argv, tuple(agent.disallowed_tools),
                              tuple(agent.forced_disallowed_tools))
    argv = _ensure_exclude_dynamic_sections(argv, agent.exclude_dynamic_system_prompt_sections)
    RETURN _ensure_stream_capture_flags(argv)

# cache/fingerprint.py (T-uoYW6b)
CLAUDE_FINGERPRINT_ENV_VARS = ("ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL",
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS", "MAX_THINKING_TOKENS", "CLAUDE_CONFIG_DIR")
    # Deliberately SMALL and CLOSED (A-9): adding a name needs review. A-9 TODO: verify
    # against the installed CLI's documented env vars. NEVER add a secret (API keys, tokens).
CLAUDE_CONTEXT_PATHS = ("CLAUDE.md", "CLAUDE.local.md", ".mcp.json", ".claude/settings.json",
    ".claude/settings.local.json", ".claude/agents", ".claude/commands", ".claude/skills")

class CliVersionReader:          # memoized per binary IDENTITY: (realpath, mtime_ns, size)
    FUNCTION version(self, binary: str) -> str:
        fail = lambda: UncacheableError(REASON_EXECUTOR_FINGERPRINT_UNAVAILABLE, binary)
        path = shutil.which(binary)
        IF path is None: RAISE fail()
        TRY real = os.path.realpath(path); st = os.stat(real)
        EXCEPT OSError: RAISE fail()
        ident = (real, st.st_mtime_ns, st.st_size)      # an auto-updated binary is re-read
        IF ident not in self._memo:
            TRY cp = subprocess.run([real, "--version"], capture_output=True,
                                    timeout=CLI_VERSION_TIMEOUT_SECONDS, check=False)
            EXCEPT (OSError, subprocess.TimeoutExpired): RAISE fail()
            out = cp.stdout.decode("utf-8", "replace").strip()
            IF cp.returncode != 0 OR NOT out: RAISE fail()
            self._memo[ident] = out[:MAX_CLI_VERSION_CHARS]
        RETURN self._memo[ident]

FUNCTION claude_cli_fingerprint(req: KeyRequest, deps: KeyDeps, budget) -> dict:
    cli_version = deps.cli_version_of(req.agent.command_template[0])
    env = {k: req.environ[k] for k in CLAUDE_FINGERPRINT_ENV_VARS if k in req.environ}
    guard = lambda raw: guarded_resolve(req, raw)                # defined in this module, shared with keys.py
    cwd_abs = guard(req.agent.working_dir or ".")                 # same resolution the dispatch uses
    dirs = [req.workspace_root] + every directory strictly below it on the way down to cwd_abs   # D7
    context = []
    FOR d IN dirs:
        FOR c IN CLAUDE_CONTEXT_PATHS:
            raw = posix_rel(join(d, c), req.workspace_root)
            IF NOT os.path.lexists(join(d, c)): context.append({"path": raw, "kind": KIND_ABSENT}); CONTINUE
            a = guard(raw)                                       # a symlinked CLAUDE.md is followed ONLY inside the workspace
            dg = digest_path(a, budget)                          # symlinks INSIDE context dirs -> uncacheable (fail-closed)
            context.append({"path": raw, "kind": dg.kind, "sha256": dg.sha256, "size": dg.size})
    RETURN {"cli_version": cli_version, "env": env, "context_files": context}
```

#### 8.2.6 `build_cache_key` (`keys.py`)

`AGENT_KEY_FIELDS` and `AGENT_NON_KEY_FIELDS` live in `cache/constants.py` (§8.1.5). `keys.py`
projects `AGENT_KEY_FIELDS` into the key; `eligibility.py` rejects any other non-default field
(`unknown_agent_field`, §8.3), so a sibling-added `AgentSpec` field can never be silently unkeyed.

```text

# Shared path guards -- defined ONCE in fingerprint.py (keys.py imports them; fingerprint must not import keys):
FUNCTION guarded_resolve(req, raw) -> str:
    TRY a = req.artifact_store.resolve(raw)
    EXCEPT (ArtifactPathError, OSError, RuntimeError, ValueError):     # Path.resolve: RuntimeError (loop), ValueError (NUL)
        RAISE UncacheableError(REASON_PATH_REJECTED, raw)
    IF a == req.cache_root OR a.startswith(req.cache_root + os.sep): RAISE UncacheableError(REASON_PATH_IN_CACHE_DIR, raw)
    RETURN a
FUNCTION guarded_abs(req, a) -> str: containment in workspace_root + not in cache root, else the same reasons

FUNCTION build_cache_key(req: KeyRequest, deps: KeyDeps, *, preseed=None) -> CacheKey:
    ws = req.workspace_root; rel = lambda p: safeio.posix_rel(p, ws)
    guard = lambda raw: guarded_resolve(req, raw); guard_abs = lambda a: guarded_abs(req, a)
    instr = guard(req.task.instruction); gis = [guard_abs(p) for p in req.general_instruction_paths]
    ins = [guard(p) for p in req.task.inputs]; dyn = [guard(p) for p in req.dynamic_input_paths]
    outs = [guard(p) for p in req.task.outputs]
    IF len(set(outs)) != len(outs): RAISE UncacheableError(REASON_DUPLICATE_OUTPUT)
    FOR o IN outs:
        IF safeio.is_sensitive_rel_path(rel(o)): RAISE UncacheableError(REASON_SENSITIVE_OUTPUT, rel(o))   # D29
        IF o IN req.control_paths_abs: RAISE UncacheableError(REASON_CONTROL_OUTPUT, rel(o))
    repos = {rid: rel(guard_abs(p)) for rid, p in sorted(req.repo_paths.items())}
    budget = HashBudget(req.max_input_bytes, req.max_input_files); memo = dict(preseed or {})
    skip_abs = frozenset({os.path.join(ws, CACHE_DIR_PARTS[0])}); exclude_abs = frozenset(outs)
    FOR o IN outs:                                                     # priors (D6)
        IF o NOT IN memo:
            IF NOT os.path.lexists(o): memo[o] = None
            ELSE:
                st = os.lstat(o)
                IF NOT stat.S_ISREG(st.st_mode): RAISE UncacheableError(REASON_OUTPUT_NOT_REGULAR, rel(o))
                memo[o] = hash_regular_file(o, budget)
    FUNCTION dg(a):                                                    # inputs; outputs reuse the memo (N-5)
        IF a IN memo:
            IF memo[a] is None: RAISE UncacheableError(REASON_INPUT_MISSING, rel(a))
            RETURN memo[a]
        memo[a] = digest_path(a, budget, exclude_abs=exclude_abs, skip_abs=skip_abs); RETURN memo[a]
    entry = lambda a: {"path": rel(a), "kind": dg(a).kind, "sha256": dg(a).sha256, "size": dg(a).size}
    norm = TaskContext(run_id=KEY_PLACEHOLDER_ID, task_id=KEY_PLACEHOLDER_ID, agent=req.agent,
        instruction_path=rel(instr), general_instruction_paths=[rel(p) for p in gis],
        input_paths=[rel(p) for p in ins], output_paths=[rel(p) for p in outs], output_manifest_path=None,
        dynamic_input_paths=[rel(p) for p in dyn], repo_paths=repos, timeout_seconds=KEY_PLACEHOLDER_TIMEOUT)
    TRY prompt = build_prompt(norm) EXCEPT Exception as e: RAISE UncacheableError(REASON_PROMPT_RENDER_ERROR, type(e).__name__)
    is_claude = req.agent.executor == "claude_cli"
    argv = build_claude_argv(req.agent, prompt) IF is_claude ELSE None
    fp = claude_cli_fingerprint(req, deps, budget) IF is_claude ELSE None
    doc = {"key_schema": KEY_SCHEMA_VERSION,
           "agent": {f: getattr(req.agent, f) for f in sorted(AGENT_KEY_FIELDS)},
           "argv": argv, "executor_fingerprint": fp, "prompt": prompt,
           "instruction": entry(instr), "general_instructions": [entry(p) for p in gis],
           "inputs": [entry(p) for p in ins], "dynamic_inputs": [entry(p) for p in dyn],
           "outputs": sorted(({"path": rel(o), "prior": PRIOR_ABSENT if memo[o] is None else memo[o].sha256}
                              for o in outs), key=lambda x: x["path"]),
           "repo_heads": dict(req.repo_heads) IF req.include_repo_heads ELSE None}
    TRY text = canonical_json(doc) EXCEPT (TypeError, ValueError): RAISE UncacheableError(REASON_KEY_ENCODING)
    components = {f: sha256(canonical_json(doc[f]).encode("ascii")).hexdigest()[:COMPONENT_DIGEST_CHARS] for f in doc}
    RETURN CacheKey(key=sha256(text.encode("ascii")).hexdigest(), summary=summary_from_doc(doc, components),
                    output_paths=tuple(sorted(rel(o) for o in outs)), output_abs={rel(o): o for o in outs},
                    preseed={o: memo[o] for o in outs}, components=components,
                    cli_version=fp["cli_version"] IF fp ELSE None)

# types.py (T-FJH6LI) -- defined ONCE there because types.to_canonical_bytes and hashing's directory
# digest need it too, and neither may import keys.py (keys imports both: the graph must stay acyclic).
def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)

# keys.py (T-uoYW6b) -- the non-sensitive summary stored in the entry (D21): paths, model/effort/max_turns,
# display digests, repo heads and component digests. NEVER argv, prompt, prompt_template or extra_args.
FUNCTION summary_from_doc(doc, components) -> KeySummary:
    agent = doc["agent"]
    files = [doc["instruction"], *doc["general_instructions"], *doc["inputs"], *doc["dynamic_inputs"]]
    RETURN KeySummary(key_schema=doc["key_schema"], executor=agent["executor"], model=agent["model"],
        effort=agent["effort"], max_turns=agent["max_turns"], instruction=doc["instruction"]["path"],
        inputs=[e["path"] for e in doc["inputs"]], dynamic_inputs=[e["path"] for e in doc["dynamic_inputs"]],
        general_instructions=[e["path"] for e in doc["general_instructions"]],
        outputs=[o["path"] for o in doc["outputs"]],
        digests={e["path"]: e["sha256"] for e in files if "sha256" in e},
        repo_heads=doc["repo_heads"], components=dict(components))
```

**Normalization rules** (each one has a test in §18):

| Rule | Detail |
|------|--------|
| N-1 | Every path in the key is the **resolved** (symlinks collapsed), workspace-relative, POSIX, `normpath`ed path. |
| N-2 | Inputs, dynamic inputs, general instructions and argv keep their order. Outputs are sorted in the structured field, but keep their declared order inside the prompt. |
| N-3 | The absolute workspace root never appears in the key, so a moved workspace keeps its keys. |
| N-4 | Excluded from the key: run id, task id, timestamps, timeout, retries, `skip_if_outputs_exist`, `depends_on`, `join`, `touches`, `forbidden_task_models` (D4). |
| N-5 | An input that is also a declared output uses its **prior** digest, so lookup-time and settle-time digests match. |
| N-6 | Directory walks exclude this task's declared outputs, `.git`, and `<ws>/.orchestrator`. |
| N-7 | `dynamic_input_paths` are resolved with `artifact_store.resolve(raw)`, i.e. relative to the workspace root. Pre-existing ambiguity: when the agent has a non-root `working_dir`, an agent may resolve a relative dynamic path against that cwd instead. A missing file at the root-relative path → `input_missing` (OQ-5). |
| N-8 | Context files are listed for every directory from the workspace root down to the agent cwd, in a fixed path order. Absent files appear as `{"path", "kind": "absent"}`. |

**What the key deliberately does not contain (documented residuals, §17).**

- **Dirty tracked edits that are not declared inputs.** The key has repo HEADs, not a dirty-tree
  digest. Guard 3 catches tracked edits made *during* a run, not edits that existed before the
  lookup, so a hit can replay a result computed against a different dirty tree (accepted in
  Rev 3; R-20).
- **Untracked files outside declared inputs.** Neither the key nor guard 3 sees them; this is why
  the cache is double opt-in.

#### 8.2.7 Key schema v1

| Field | Type | Content |
|-------|------|---------|
| `key_schema` | int | `KEY_SCHEMA_VERSION` (1) |
| `agent` | object | `AGENT_KEY_FIELDS` projection of the EFFECTIVE agent |
| `argv` | array \| null | `build_claude_argv(effective_agent, prompt)` for claude_cli; null for fake |
| `executor_fingerprint` | object \| null | `{cli_version, env, context_files}` for claude_cli; null for fake |
| `prompt` | string | `build_prompt(normalized TaskContext)` |
| `instruction` | object | `{path, kind, sha256, size}` |
| `general_instructions` / `inputs` / `dynamic_inputs` | array | the same objects, in order |
| `outputs` | array | `[{path, prior}]` sorted by path; `prior` is `"absent"` or a sha256 |
| `repo_heads` | object \| null | `{repo_id: sha \| "unborn"}`; null when `include_repo_heads: false` |

**Versioning.**

- Composition changes alter keys automatically. This includes any change to argv construction, now
  that argv is hashed (D7).
- `KEY_SCHEMA_VERSION` must still be bumped when the **meaning** of a field changes while its
  serialized form stays the same. Example: the executor starts reading a new file that is not in
  `CLAUDE_CONTEXT_PATHS`.
- A pointer comment placed **next to `EFFORT_MAX_TURNS` in `models.py`, and above
  `build_claude_argv` in `claude_cli.py`** (added by T-bdQZW4) states the rule.
- The U-K8 and U-K8a tripwires fail when the `AgentSpec` fields or the argv golden change.

**Worked example (golden vector GV-1, Rev 2).** T-uoYW6b's test must reproduce this exactly. It
uses real files in a temp workspace, a `KeyDeps` whose `cli_version_of` returns
`"2.1.278 (Claude Code)"`, `environ={}`, no context files,
`repo_heads={"core": "0123456789abcdef0123456789abcdef01234567"}` and `include_repo_heads=True`.

- Agent: `AgentSpec(executor="claude_cli", model="sonnet", effort="medium")`, otherwise defaults.
- Instruction `specs/instr/summarize.md` = `"Summarize the inputs.\n"`.
- Input `docs/notes.md` = `"alpha\n"`.
- General instruction `.ao/house-rules.md` = `"Be concise.\n"`.
- Output `out/summary.md`, absent.
- Repo set `{core: <ws root>}`.

The values below were computed at base `bb6d8a0` by running the real `build_prompt` and the real
`claude_cli` argv helpers:

```text
argv = ["claude","-p","<prompt>","--model","sonnet","--max-turns","30","--output-format","stream-json","--verbose"]
canonical JSON =
{"agent":{"command_template":["claude","-p","{prompt}"],"context_window":"isolated","disallowed_tools":[],"effort":"medium","exclude_dynamic_system_prompt_sections":false,"executor":"claude_cli","extra_args":[],"forced_disallowed_tools":[],"max_turns":null,"model":"sonnet","prompt_template":"Follow the instructions in {instruction}. Input artifacts: {inputs}. Write outputs to: {outputs}. Repos: {repos}.","working_dir":null},"argv":["claude","-p","Follow the instructions in specs/instr/summarize.md. Input artifacts: docs/notes.md. Write outputs to: out/summary.md. Repos: core=.. Also follow the general instructions that apply to every task in this workspace: .ao/house-rules.md.","--model","sonnet","--max-turns","30","--output-format","stream-json","--verbose"],"dynamic_inputs":[],"executor_fingerprint":{"cli_version":"2.1.278 (Claude Code)","context_files":[{"kind":"absent","path":"CLAUDE.md"},{"kind":"absent","path":"CLAUDE.local.md"},{"kind":"absent","path":".mcp.json"},{"kind":"absent","path":".claude/settings.json"},{"kind":"absent","path":".claude/settings.local.json"},{"kind":"absent","path":".claude/agents"},{"kind":"absent","path":".claude/commands"},{"kind":"absent","path":".claude/skills"}],"env":{}},"general_instructions":[{"kind":"file","path":".ao/house-rules.md","sha256":"f06035e1c8efe836f95ef6287060ff940639c2ba63a572588174e6d6af74f4a7","size":12}],"inputs":[{"kind":"file","path":"docs/notes.md","sha256":"b6a98d9ce9a2d9149288fa3df42d377c3e42737afdcdaf714e33c0a100b51060","size":6}],"instruction":{"kind":"file","path":"specs/instr/summarize.md","sha256":"11d258df326d3c80a3cb7ddaa1515ea6b15b46f52c43cfd444f219a561c61ae9","size":22},"key_schema":1,"outputs":[{"path":"out/summary.md","prior":"absent"}],"prompt":"Follow the instructions in specs/instr/summarize.md. Input artifacts: docs/notes.md. Write outputs to: out/summary.md. Repos: core=.. Also follow the general instructions that apply to every task in this workspace: .ao/house-rules.md.","repo_heads":{"core":"0123456789abcdef0123456789abcdef01234567"}}
key = 6646469e94a695fe1a994d35f54ca74e007262911255552b03c54ce2e5d0319f
components = agent ced58570dab6 · argv 315cfbdef1cf · dynamic_inputs 4f53cda18c2b ·
             executor_fingerprint 620dc66502c3 · general_instructions 80c58b832f5c · inputs 6518d7ac7319 ·
             instruction 8f7ad8e7e8d2 · key_schema 6b86b273ff34 · outputs 2789b49e50b4 ·
             prompt 25e61c2662b6 · repo_heads b1e77f36ac12
```

The Rev 1 GV-1 (`536533b0…`) is superseded, because Rev 2 adds `argv` and `executor_fingerprint`.

**Trade-offs.**

- With repo heads in the key (the default), any commit invalidates every key. Without them
  (`include_repo_heads: false`), correctness rests on declared inputs, the context fingerprint and
  the store guards. G0 measures the difference.
- The CLI version in the key means an auto-update invalidates the cache. That is safe but churns.

**Subtasks.**

| Task | Scope |
|------|-------|
| T-OeRYSO | Argv extraction, plus golden argv tests |
| T-8tr1H4 | hashing, plus `repo_state` |
| T-uoYW6b | fingerprint, plus keys and the GV-1 / invariance / tripwire tests |

`safeio` lives in T-FJH6LI.

**Edge cases.**

- Input-side failures, each with its own reason: missing, FIFO, symlink inside a directory, too
  large, unreadable, or unstable (the file changes mid-hash).
- `duplicate_output`; `output_not_regular_file`; `path_rejected` (including a symlink loop or a
  NUL byte).
- `path_in_cache_dir`; `sensitive_output` (including an output symlinked into `.git/hooks`);
  `control_output`.
- Git: an unborn HEAD gives `"unborn"`; any git failure, or a read-only HOME, gives
  `repo_head_unavailable`. A `.git` above the workspace root is ignored (U-G7).
- Missing `claude` binary or a version failure → `executor_fingerprint_unavailable`.
- A `CLAUDE.md` symlink pointing outside the workspace → `path_rejected`.
- An unknown template placeholder → `prompt_render_error`.
- Non-UTF-8 names go through surrogate escapes. Reordered inputs give a new key.

### 8.3 Module M3 — eligibility (`cache/eligibility.py`)

**Module definition.**

| | |
|---|---|
| **Purpose** | A fail-closed, **structural** allowlist predicate, run after the author-policy check: "is this a plain agent-dispatch task that the cache can handle?" |
| **Inputs** | `task`, `workflow`, `agents`, `integration_active`. |
| **Outputs** | `Eligibility(eligible, reason, detail)`. |
| **Dependencies** | `models` (`resolve_task_isolation`, `strip_iter_suffix`, `resolve_effective_agent`), `usage.verdict_path_for`. |

It is pure: no I/O, no logging. Checks on resolved paths (control and sensitive outputs) live in
`keys.py`.

#### 8.3.1 Field classification (tripwire-tested)

**`TaskSpec`.** Tripwire U-E1 asserts:

```python
set(TaskSpec.model_fields) == TASK_ANY_VALUE | set(TASK_VALUE_RULED)
```

| Field | Class | Rule and reason |
|-------|-------|-----------------|
| `id` | any | not in the key (D4) |
| `agent`, `model`, `effort`, `max_turns` | any | covered by the effective agent and argv in the key |
| `instruction`, `inputs`, `outputs` | any | content and paths are in the key |
| `depends_on`, `retries`, `timeout_seconds`, `skip_if_outputs_exist`, `join`, `touches` | any | scheduling or execution bounds only |
| `cache` | any | author policy (`settings.task_cache_policy`) |
| `emit_tasks` | ruled | must be `False` → `emit_tasks` |
| `task_manifest_path` | ruled | must be `None` → `task_manifest_path` |
| `output_manifest` | ruled | must be `None` → `output_manifest` |
| `pre_hook`, `post_hook` | ruled | must be `None` → `pre_hook` / `post_hook` |
| `isolation` | ruled | `resolve_task_isolation(...)` must be `"none"` → `isolation_worktree` |
| `verdict_path` | ruled | covered by the verdict-sidecar rule → `verdict_sidecar_undeclared` |

**Runtime rule.** This iterates `type(task).model_fields`, so subclass fields are seen. Any field
outside both sets with a non-default value makes the task ineligible: reason `unknown_task_field`,
detail = the field name.

The tripwire's failure message reads:

> classify the field in cache/eligibility.py — ANY only if it can change neither what the agent is
> asked to do nor which files the task produces; otherwise RULED (default-only); an approval/
> human-gate field (E-Ag7Pw3) is ALWAYS RULED

**`AgentSpec`.** Covered by `AGENT_KEY_FIELDS` / `AGENT_NON_KEY_FIELDS` (§8.1.5), with tripwire
U-K8. **Runtime rule (Rev 3):** the predicate iterates `type(eff).model_fields` of the **effective**
agent. Any field outside both sets with a non-default value makes the task ineligible: reason
`unknown_agent_field`, detail = the field name. Without this rule, a field added to `AgentSpec`
by a sibling epic would change agent behaviour without changing the key (early-gate A3).

**`WorkflowSpec` and `WorkflowDefaults`.** Tripwire U-E2 asserts that the coverage maps
`WORKFLOW_FIELD_COVERAGE` / `DEFAULTS_FIELD_COVERAGE` cover every field. A runtime rule applies the
same check: any uncovered field with a non-default value → `unknown_workflow_field`, with the field
name as detail.

| Field | How it is covered |
|-------|-------------------|
| `version`, `id`, `name` | not semantic |
| `repo_set` | repo paths enter the prompt through `repo_paths`; HEADs enter the key |
| `tasks` | per-task eligibility |
| `defaults` | its subfields, classified below |
| `defaults.retries`, `defaults.timeout_seconds` | not semantic |
| `defaults.isolation` | isolation rule |
| `defaults.model` | effective agent and argv |
| `defaults.cache` | author policy |
| `budget`, `triggers`, `scheduling` | not semantic for outputs |
| `loops`, `branches` | loop-member and router rules; control outputs |
| `circuit_breakers` | breaker-verdict-source rule; control outputs |
| `general_instructions`, `prompt_path` | content in the key; `prompt_path` is also a control path |
| `integration` | integration-active rule |
| `hooks` | no-hooks rule |

#### 8.3.2 Predicate

```text
FUNCTION check_eligibility(task, workflow, agents, *, integration_active) -> Eligibility:
    # The author policy (opt-in) is checked by the coordinator BEFORE this; tasks that are not
    # opted in never get here. Public model helpers only; no engine-private helpers.
    IF integration_active: RETURN no(REASON_RUN_INTEGRATION_ACTIVE)
    FOR f IN sorted(type(task).model_fields):
        IF f IN TASK_ANY_VALUE: CONTINUE
        IF f IN TASK_VALUE_RULED:
            rule = TASK_VALUE_RULED[f]
            IF rule.ok is not None AND NOT rule.ok(getattr(task, f)): RETURN no(rule.reason)
            CONTINUE
        IF getattr(task, f) != default_of(type(task), f):    # get_default(call_default_factory=True)
            RETURN no(REASON_UNKNOWN_TASK_FIELD, detail=f)
    FOR (obj, cov, prefix) IN ((workflow, WORKFLOW_FIELD_COVERAGE, ""),
                               (workflow.defaults, DEFAULTS_FIELD_COVERAGE, "defaults.")):
        FOR f IN sorted(type(obj).model_fields):
            IF f NOT IN cov AND getattr(obj, f) != default_of(type(obj), f):
                RETURN no(REASON_UNKNOWN_WORKFLOW_FIELD, detail=prefix + f)
    IF resolve_task_isolation(task, workflow) != ISOLATION_NONE: RETURN no(REASON_ISOLATION_WORKTREE)
    IF any(r.router_task_id == task.id for r in workflow.branches): RETURN no(REASON_ROUTER_TASK)
    base = strip_iter_suffix(task.id)
    IF any(base == l.gate_task_id OR base IN l.body for l in workflow.loops):
        RETURN no(REASON_LOOP_MEMBER)
    IF NOT task.outputs: RETURN no(REASON_NO_OUTPUTS)
    agent = agents.get(task.agent)
    IF agent is None: RETURN no(REASON_AGENT_UNKNOWN)
    eff = resolve_effective_agent(task, agent, workflow.defaults.model)
    FOR f IN sorted(type(eff).model_fields):                          # AgentSpec runtime rule
        IF f IN AGENT_KEY_FIELDS OR f IN AGENT_NON_KEY_FIELDS: CONTINUE
        IF getattr(eff, f) != default_of(type(eff), f):
            RETURN no(REASON_UNKNOWN_AGENT_FIELD, detail=f)
    IF eff.executor NOT IN CACHEABLE_EXECUTORS:
        RETURN no(REASON_EXECUTOR_NOT_CACHEABLE, detail=eff.executor)
    IF eff.executor == "claude_cli":
        base_cmd = posixpath.basename(eff.command_template[0]) IF eff.command_template ELSE ""
        IF base_cmd NOT IN CACHEABLE_COMMAND_BASENAMES:
            RETURN no(REASON_COMMAND_NOT_CACHEABLE, detail=base_cmd[:MAX_TEXT_CHARS])
        tokens = [*eff.command_template, *eff.extra_args]
        IF eff.model is None AND NOT any(t in MODEL_FLAGS OR t.startswith("--model=")
                                         for t in tokens):
            RETURN no(REASON_MODEL_UNRESOLVED)            # the CLI's own default can't be keyed
    outs = {posixpath.normpath(o) for o in task.outputs}
    sidecar = verdict_path_for(task)
    IF sidecar is not None AND posixpath.normpath(sidecar) NOT IN outs:
        RETURN no(REASON_VERDICT_SIDECAR_UNDECLARED)
    IF any(b.condition == "verdict" AND b.task_id == task.id for b in workflow.circuit_breakers):
        RETURN no(REASON_BREAKER_VERDICT_SOURCE)
    RETURN Eligibility(True)
```

**Consistency with the isolation resolver.** A test (U-E28) asserts that every task for which
`models._is_structural_task(task, workflow)` is true (emit, router, loop gate) is ineligible. The
production code does not call that private helper; the test only checks agreement.

#### 8.3.3 Ineligibility reasons (complete; `outcome: "ineligible"`, a record is written)

Not-opted-in tasks get **no record**; at most a DEBUG-level `cache.skip` with `reason=not_opted_in`.

| `reason` (+ `reason_detail`) | Trigger | Owner |
|---|---|---|
| `run_integration_active` | `state.integration.active` (D25) | eligibility |
| `unknown_task_field` (+name) / `unknown_agent_field` (+name) / `unknown_workflow_field` (+name) | unclassified field with a non-default value | eligibility |
| `emit_tasks` / `task_manifest_path` / `output_manifest` / `pre_hook` / `post_hook` | ruled-field violation | eligibility |
| `isolation_worktree` | resolved isolation ≠ none | eligibility |
| `router_task` / `loop_member` / `no_outputs` / `agent_unknown` | structural | eligibility |
| `executor_not_cacheable` (+executor) / `command_not_cacheable` (+basename) / `model_unresolved` | executor allowlist | eligibility |
| `verdict_sidecar_undeclared` / `breaker_verdict_source` | control semantics | eligibility |
| `artifact_store_unsupported` | the engine store is not a `LocalFsArtifactStore` | coordinator |
| `path_rejected` / `path_in_cache_dir` / `duplicate_output` / `sensitive_output` / `control_output` | path guards | keys |
| `input_missing` / `input_not_regular` / `input_unstable` / `input_too_large` / `input_unreadable` / `output_not_regular_file` | bounded hashing | hashing / keys |
| `repo_head_unavailable` | git state (HEADs are part of the key) | repo_state |
| `executor_fingerprint_unavailable` / `prompt_render_error` / `key_encoding` | key document | fingerprint / keys |

`repo_worktree_probe_failed` is **not** an ineligibility reason any more (Rev 3): the guard-3
snapshot is taken lazily for storable outcomes, and its failure only makes that outcome not
storable (`store_reason`, §8.6.4).

**Subtasks.**

1. Classification tables and coverage maps.
2. `check_eligibility`, including the `AgentSpec` runtime rule.
3. Tripwires U-E1 and U-E2 (U-K8 lives with the key builder).
4. One test per row of §8.3.3 that eligibility owns, plus U-E28.

**Edge cases.**

- Injected tasks: `cache: true` with `defaults.cache: false` → not opted in (no record).
- `dev__iter3` → `loop_member`.
- An agent with `command_template[0] = "/usr/local/bin/claude"` → basename `claude` → eligible.
- A wrapper script → `command_not_cacheable`.
- An agent whose `command_template` bakes in `--model opus` → resolved → eligible.
- An `AgentSpec` subclass with an extra field at its default → eligible; non-default →
  `unknown_agent_field`.
- A `review.md` output without a declared sibling `review-verdict.json` → ineligible.
- Isolation degraded at runtime → still ineligible (the declared mode is used).

---
### 8.4 Module M4 — store (`types.py` contracts, `store.py` `LocalFsCacheStore`)

**Module definition.**

| Aspect | Details |
|--------|---------|
| Purpose | A local store of entries and blobs. It is content-addressed, crash-safe, safe across processes and against hostile data. It enforces size and age limits and provides maintenance operations. |
| Inputs | Keys, `CacheEntry` objects and binary file objects. The only workspace path it ever receives is the one passed to its factory (D17). |
| Outputs | Entries, blob streams, reports. |
| Dependencies | `os`, `json`, `uuid`, `threading`, `shutil`, `pydantic`, `safeio`, `types`, `constants`. |

#### 8.4.1 Layout

```text
<workspace>/.orchestrator/cache/          (0o700 on creation; owned by euid; no group/other write; not a symlink)
  .gitignore            "# ao result cache -- never commit\n*\n"
  CACHEDIR.TAG          Cache Directory Tagging Spec signature
  layout.json           {"schema": "ao.result-cache.layout/v1"}
  entries/v1/<k[:2]>/<key>.json     (major entry version IN THE PATH; other version dirs are never touched)
  blobs/<s[:2]>/<sha256>            (shared by all entry versions)
  tmp/<pid>-<thread_ident>-<uuid4hex>.<blob|entry>.tmp
  trash-<uuid4hex>/                 (transient; created by clear())
```

**Factory.** `LocalFsCacheStore.for_workspace(workspace_root, *, max_bytes, ttl_days)` computes
`root = <ws>/.orchestrator/cache` and records the resolved workspace root for its checks. Nothing is
created until the first write.

**Per-operation checks.** Before every operation:

- `safeio.check_root_dir(root, workspace_root=…)`. The root must:
  - not be a symlink;
  - be a directory;
  - be owned by `geteuid()`;
  - have no group or other write bit. If we own it, `chmod 0o700` instead of failing.
  - resolve inside the workspace.

  `.orchestrator` itself must not be a symlink.
- `safeio.check_dir_chain(root, target_dir)`: every existing path component under the root must be
  a real directory, not a symlink.

These checks run before **every** operation, including `delete_entry`, `touch_entry`, `has_blob`,
`read_blob`, `delete_blob` and the admin operations. A failed root check raises
`CacheLayoutError`. A failed component check (a symlinked `entries/`, `entries/v1/`, shard or
blob directory) raises **`CacheUnsafePathError`** (reason `unsafe_path`), never
`CacheIntegrityError`, and nothing under that path is read, unlinked or touched (D33). A
symlink or FIFO as the **final** component (the entry or blob file itself) is still corruption:
it is opened with `O_NOFOLLOW`, and evicting it unlinks the link, not its target.

**Lazy layout.** `ensure_layout()` runs on the first write:

- Directories are created one component at a time with `safeio.ensure_dir_chain`.
- The layout files are written only if absent, via a temp file plus `os.replace`.
- `layout.json` is read through `safeio.read_bounded(MAX_LAYOUT_FILE_BYTES)`.
- An unknown layout schema raises `CacheLayoutError`.

#### 8.4.2 Entry schema and parse boundary (`types.py`)

Bounded, strict pydantic models. `extra="ignore"` gives additive forward compatibility within v1.

```python
PathStr = Annotated[str, Field(max_length=MAX_PATH_CHARS)]
TextStr = Annotated[str, Field(max_length=MAX_TEXT_CHARS)]
HexStr = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class OutputRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: PathStr  # normalized relative path: COMPARISON ONLY, never used for I/O
    kind: Literal["file"] = KIND_FILE  # reserved for future directory outputs (critic #5)
    sha256: HexStr
    size: int = Field(ge=0, le=MAX_CONFIG_BYTES)
    mode: int = Field(ge=0, le=STORED_MODE_MASK)


class EntryUsage(BaseModel):
    """The source run's actuals; the `saved_*` estimates are copied from here (D16)."""

    model_config = ConfigDict(extra="ignore")
    cost_usd: float = Field(default=0.0, ge=0, le=MAX_ENTRY_COST_USD, allow_inf_nan=False)
    input_tokens: int = Field(default=0, ge=0, le=MAX_ENTRY_TOKENS)
    output_tokens: int = Field(default=0, ge=0, le=MAX_ENTRY_TOKENS)
    # Claude PROMPT-cache counters of the source run, copied verbatim.
    cache_creation_input_tokens: int = Field(default=0, ge=0, le=MAX_ENTRY_TOKENS)
    cache_read_input_tokens: int = Field(default=0, ge=0, le=MAX_ENTRY_TOKENS)
    duration_seconds: float = Field(
        default=0.0, ge=0, le=MAX_ENTRY_SECONDS, allow_inf_nan=False
    )
    attempts: int = Field(default=0, ge=0, le=MAX_ENTRY_ATTEMPTS)
    model: TextStr | None = None
    effort: TextStr | None = None
    actuals_available: bool = False


class EntrySource(BaseModel):
    """Provenance; display-only."""

    model_config = ConfigDict(extra="ignore")
    workflow_id: TextStr
    task_id: TextStr
    run_id: TextStr
    agent: TextStr
    ao_version: TextStr
    cli_version: TextStr | None = None


class KeySummary(BaseModel):
    """Non-sensitive key description (D21): never argv, prompt, templates or extra_args."""

    model_config = ConfigDict(extra="ignore")
    key_schema: int = Field(ge=1, le=1000)
    executor: str = Field(max_length=MAX_REASON_CHARS)
    model: TextStr | None = None
    effort: str | None = Field(default=None, max_length=MAX_REASON_CHARS)
    max_turns: int | None = Field(default=None, ge=0, le=MAX_ENTRY_ATTEMPTS)
    instruction: PathStr
    inputs: list[PathStr] = Field(default=[], max_length=MAX_LIST_ITEMS)
    dynamic_inputs: list[PathStr] = Field(default=[], max_length=MAX_LIST_ITEMS)
    general_instructions: list[PathStr] = Field(default=[], max_length=MAX_LIST_ITEMS)
    outputs: list[PathStr] = Field(default=[], max_length=MAX_LIST_ITEMS)
    # rel path -> sha256, display-only
    digests: dict[PathStr, HexStr] = Field(default={}, max_length=MAX_LIST_ITEMS)
    repo_heads: dict[TextStr, TextStr] | None = None
    # D30; also shown by `ao cache show`
    components: dict[TextStr, TextStr] = Field(default={}, max_length=MAX_LIST_ITEMS)


class CacheEntry(BaseModel):
    # The alias avoids shadowing BaseModel.schema().
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    schema_: str = Field(alias="schema", max_length=MAX_REASON_CHARS)
    key: HexStr
    key_schema: int = Field(ge=1, le=1000)
    created_at: AwareDatetime  # naive datetimes are rejected (D28)
    source: EntrySource
    usage: EntryUsage
    outputs: list[OutputRecord] = Field(min_length=1, max_length=MAX_LIST_ITEMS)
    key_summary: KeySummary

    def to_canonical_bytes(self) -> bytes:
        """Sorted keys, compact, ASCII: stable bytes for a future signature (non-MVP 2)."""
        return canonical_json(self.model_dump(mode="json", by_alias=True)).encode("ascii")


def parse_entry_bytes(raw: bytes, expected_key: str) -> CacheEntry:
    """THE parse boundary for hostile cache data (ADR-0019 D28).

    A TOTAL function: it returns an entry or raises CacheIntegrityError, never anything else.
    """
    try:
        data = json.loads(raw)  # ValueError, RecursionError or UnicodeDecodeError possible
        if not isinstance(data, dict):
            raise CacheIntegrityError(REASON_CORRUPT_ENTRY, "not an object")
        if data.get("schema") != ENTRY_SCHEMA:
            raise CacheIntegrityError(REASON_CORRUPT_ENTRY, "schema")
        entry = CacheEntry.model_validate(data)
    except CacheIntegrityError:
        raise
    except Exception as exc:  # hostile input: ANY failure is "corrupt"
        raise CacheIntegrityError(REASON_CORRUPT_ENTRY, type(exc).__name__) from None
    if entry.key != expected_key:
        raise CacheIntegrityError(REASON_KEY_MISMATCH, "entry key != file name")
    return entry
```

Every string, list item and mapping key or value is bounded through the `PathStr`, `TextStr`
and `HexStr` aliases.

**Example entry** (`entries/v1/66/6646469e…319f.json`, pretty-printed here; stored as canonical
compact bytes):

```text
{"schema": "ao.result-cache.entry/v1",
 "key": "6646469e94a695fe1a994d35f54ca74e007262911255552b03c54ce2e5d0319f",
 "key_schema": 1, "created_at": "2026-10-05T10:15:02+00:00",
 "source": {"workflow_id": "doc-pipeline", "task_id": "summarize", "run_id": "doc-pipeline-20261005T101450Z",
            "agent": "writer", "ao_version": "0.1.0", "cli_version": "2.1.278 (Claude Code)"},
 "usage": {"cost_usd": 0.4123, "input_tokens": 12000, "output_tokens": 3400, "cache_creation_input_tokens": 0,
           "cache_read_input_tokens": 8000, "duration_seconds": 95.2, "attempts": 1, "model": "sonnet",
           "effort": "medium", "actuals_available": true},
 "outputs": [{"path": "out/summary.md", "kind": "file", "sha256": "9c1f…(64 hex)", "size": 1834, "mode": 420}],
 "key_summary": {"key_schema": 1, "executor": "claude_cli", "model": "sonnet", "effort": "medium", "max_turns": null,
                 "instruction": "specs/instr/summarize.md", "inputs": ["docs/notes.md"], "dynamic_inputs": [],
                 "general_instructions": [".ao/house-rules.md"], "outputs": ["out/summary.md"],
                 "digests": {"specs/instr/summarize.md": "11d2…", "docs/notes.md": "b6a9…", ".ao/house-rules.md": "f060…"},
                 "repo_heads": {"core": "0123456789abcdef0123456789abcdef01234567"},
                 "components": {"agent": "ced58570dab6", "argv": "315cfbdef1cf", "...": "..."}}}
```

#### 8.4.3 ABCs (provisional; D17) and `LocalFsCacheStore`

```python
class CacheStore(ABC):
    """HOT PATH, used by the coordinator. PROVISIONAL (D17): a remote backend will need an
    output-I/O seam. Every method validates keys and shas before building a path."""

    root: str

    @abstractmethod
    def check(self) -> None:
        """Pre-flight: root, ownership, layout. Creates nothing.
        Raises CacheLayoutError or SafeIOError."""

    @abstractmethod
    def get_entry(self, key: str) -> CacheEntry | None:
        """None if absent. Raises CacheIntegrityError or CacheUnsafePathError."""

    @abstractmethod
    def put_entry(self, entry: CacheEntry) -> int:
        """Bytes written. Raises CacheTooLargeError, CacheUnsafePathError or OSError."""

    @abstractmethod
    def touch_entry(self, key: str, at: datetime) -> None:
        """LRU touch. A missing entry is a no-op. Raises CacheUnsafePathError."""

    @abstractmethod
    def delete_entry(self, key: str) -> bool:
        """False if absent. Raises CacheUnsafePathError (and then deletes nothing)."""

    @abstractmethod
    def has_blob(self, sha256: str) -> bool:
        """Shadow-mode restorability check. Raises CacheUnsafePathError."""

    @abstractmethod
    def put_blob(self, src: BinaryIO, *, max_bytes: int) -> BlobRef:
        """Raises CacheTooLargeError, CacheUnsafePathError or OSError."""

    @abstractmethod
    def read_blob(self, sha256: str, dest: BinaryIO, *, max_bytes: int) -> int:
        """Bytes copied. Raises CacheBlobMissingError, CacheIntegrityError or
        CacheUnsafePathError."""

    @abstractmethod
    def delete_blob(self, sha256: str) -> bool:
        """False if absent. Raises CacheUnsafePathError."""

    @abstractmethod
    def maybe_enforce_limits(self, *, now: datetime) -> PruneReport | None:
        """Bounded inline enforcement (D19)."""


class CacheAdmin(ABC):
    """MAINTENANCE (`ao cache ...`). PROVISIONAL."""

    @abstractmethod
    def iter_entries(self) -> Iterator[EntryInfo]:
        """Streaming; never follows symlinks."""

    @abstractmethod
    def stats(self, *, now: datetime) -> CacheStats: ...

    @abstractmethod
    def prune(
        self,
        *,
        now: datetime,
        max_bytes: int | None,
        ttl_days: int | None,
        dry_run: bool = False,
    ) -> PruneReport: ...

    @abstractmethod
    def clear(self) -> ClearReport: ...

    @abstractmethod
    def verify(self) -> VerifyReport:
        """Read-only (`--repair` is deferred, non-MVP 16)."""
```

`LocalFsCacheStore` derives from `CacheStore` when T-U7ckfd lands. T-HjxNQ0 adds `CacheAdmin` to
its bases together with the admin methods and the real `maybe_enforce_limits`, so every
intermediate state of the code is importable and type-correct.

```text
class LocalFsCacheStore(CacheStore):      # T-U7ckfd. T-HjxNQ0: (CacheStore, CacheAdmin).
    @classmethod
    def for_workspace(cls, workspace_root, *, max_bytes, ttl_days) -> LocalFsCacheStore: # no I/O

    # ---------- paths (validate BEFORE splicing; M-2) ----------
    def _entry_path(self, key):
        IF NOT SHA256_HEX_RE.fullmatch(key): RAISE ValueError("invalid key")
        RETURN join(root, ENTRIES_DIR, ENTRIES_VERSION_DIR, key[:SHARD_CHARS], key + ENTRY_SUFFIX)
    def _blob_path(self, sha):
        IF NOT SHA256_HEX_RE.fullmatch(sha): RAISE ValueError("invalid sha")
        RETURN join(root, BLOBS_DIR, sha[:SHARD_CHARS], sha)

    # ---------- checks: run before EVERY operation (D33) ----------
    def _checks(self, target_dir):                                     # creates nothing
        safeio.check_root_dir(self.root, workspace_root=self._ws)      # -> CacheLayoutError
        TRY safeio.check_dir_chain(self.root, target_dir)              # missing dirs are fine
        EXCEPT UnsafePathError as e: RAISE CacheUnsafePathError(REASON_UNSAFE_PATH, str(e))

    def check(self):                     # coordinator pre-flight (every lookup); creates nothing
        IF NOT os.path.lexists(self.root): RETURN                     # created lazily by a write
        safeio.check_root_dir(self.root, workspace_root=self._ws)     # also: .orchestrator link
        self._read_layout_if_present()                                # unknown schema -> error

    # ---------- entries ----------
    def get_entry(self, key):
        path = self._entry_path(key); self._checks(dirname(path))
        TRY raw = safeio.read_bounded(path, MAX_ENTRY_FILE_BYTES)
        EXCEPT FileNotFoundError: RETURN None
        EXCEPT (NotRegularFileError, TooLargeError) as e:              # final component only
            RAISE CacheIntegrityError(REASON_CORRUPT_ENTRY, type(e).__name__)
        RETURN types.parse_entry_bytes(raw, key)                       # total function

    def put_entry(self, entry):
        self.ensure_layout(); body = entry.to_canonical_bytes()
        IF len(body) > MAX_ENTRY_FILE_BYTES: RAISE CacheTooLargeError(REASON_ENTRY_TOO_LARGE)
        path = self._entry_path(entry.key); self._checks(dirname(path))
        self._atomic_write_bytes(path, body, kind="entry"); self._add_approx(len(body))
        RETURN len(body)

    def touch_entry(self, key, at):
        path = self._entry_path(key); self._checks(dirname(path))      # never utime via a link
        TRY os.utime(path, (at.timestamp(), at.timestamp()), follow_symlinks=False)
        EXCEPT FileNotFoundError: pass                                 # other OSError -> boundary

    def delete_entry(self, key):
        path = self._entry_path(key); self._checks(dirname(path))      # never unlink via a link
        TRY os.unlink(path); RETURN True
        EXCEPT FileNotFoundError: RETURN False

    # ---------- blobs ----------
    def has_blob(self, sha):
        path = self._blob_path(sha); self._checks(dirname(path))
        TRY RETURN stat.S_ISREG(os.lstat(path).st_mode)
        EXCEPT FileNotFoundError: RETURN False

    def put_blob(self, src, *, max_bytes):
        self.ensure_layout(); tmp, fd = self._new_tmp("blob")         # create_exclusive, 0o600
        TRY:
            h = sha256(); n = 0
            WHILE chunk := src.read(min(HASH_CHUNK_BYTES, max_bytes + 1 - n)):
                n += len(chunk)
                IF n > max_bytes: RAISE CacheTooLargeError(REASON_ENTRY_TOO_LARGE)
                h.update(chunk); write all of chunk to fd           # loop on short writes
            os.close(fd); fd = None; sha = h.hexdigest(); dst = self._blob_path(sha)
            self._checks(dirname(dst)); safeio.ensure_dir_chain(self.root, dirname(dst),
                                                                CACHE_DIR_MODE)
            IF os.path.lexists(dst):                                   # dedupe: refresh grace
                os.utime(dst, None, follow_symlinks=False); RETURN BlobRef(sha, n, new=False)
            os.replace(tmp, dst); self._add_approx(n); RETURN BlobRef(sha, n, new=True)
        FINALLY: close fd if open; unlink tmp if it still exists

    def read_blob(self, sha, dest, *, max_bytes):
        path = self._blob_path(sha); self._checks(dirname(path))
        TRY fd = safeio.open_regular_read(path)
        EXCEPT FileNotFoundError: RAISE CacheBlobMissingError(sha)
        EXCEPT (NotRegularFileError, OSError):
            RAISE CacheIntegrityError(REASON_BLOB_CORRUPT, "unreadable/irregular")
        TRY: n = 0
             WHILE chunk := os.read(fd, min(HASH_CHUNK_BYTES, max_bytes + 1 - n)):
                 n += len(chunk); dest.write(chunk)
                 IF n > max_bytes: BREAK                    # caller sees n > expected -> corrupt
             RETURN n
        FINALLY os.close(fd)

    def delete_blob(self, sha):
        path = self._blob_path(sha); self._checks(dirname(path))
        TRY os.unlink(path); RETURN True
        EXCEPT FileNotFoundError: RETURN False

    def maybe_enforce_limits(self, *, now):            # T-U7ckfd: placeholder returning None
        RETURN None                                    # TODO(T-HjxNQ0): the version below

# ---------------- added by T-HjxNQ0: class LocalFsCacheStore(CacheStore, CacheAdmin) ----------
    def iter_entries(self):           # STREAMING generator over entries/v1/*/*.json
        self._checks(join(root, ENTRIES_DIR, ENTRIES_VERSION_DIR))
        FOR shard IN scandir(join(root, ENTRIES_DIR, ENTRIES_VERSION_DIR)):
            IF shard is not a real directory: YIELD anomaly record; CONTINUE   # never followed
            FOR f IN scandir(shard):
                IF f.is_symlink() OR NOT f.name.endswith(ENTRY_SUFFIX): YIELD anomaly; CONTINUE
                key = f.name[:-len(ENTRY_SUFFIX)]
                IF NOT SHA256_HEX_RE.fullmatch(key): YIELD anomaly; CONTINUE
                st = f.stat(follow_symlinks=False)
                TRY entry = self.get_entry(key); err = None
                EXCEPT CacheIntegrityError as e: entry, err = None, e.reason
                YIELD EntryInfo(key, size=st.st_size, mtime=st.st_mtime, entry=entry, error=err)

    def _referenced_blobs(self, *, exclude_v1_keys: set[str]) -> set[str]:
        """MARK phase: every 64-hex token in EVERY entry file under entries/** (any version,
        parseable or not) protects the blob of that name, except v1 entries being removed by
        this prune (D18). Symlinked directories are skipped, never followed."""
        refs = set()
        FOR each regular file under join(root, ENTRIES_DIR) (recursive, no symlink following):
            IF it is entries/v1/<shard>/<key>.json AND key IN exclude_v1_keys: CONTINUE
            TRY raw = safeio.read_bounded(path, MAX_ENTRY_FILE_BYTES)
            EXCEPT (SafeIOError, OSError): CONTINUE
            refs.update(m.group(0).decode("ascii") for m in HEX64_TOKEN_RE_BYTES.finditer(raw))
        RETURN refs

    def prune(self, *, now, max_bytes, ttl_days, dry_run=False):
        remove = {}                                                     # key -> reason (v1 only)
        live = []                                                       # (mtime, key, size, shas)
        FOR info IN self.iter_entries():                    # anomalies counted, never deleted
            IF info.entry is None: remove[info.key] = "invalid"; CONTINUE
            IF ttl_days is not None AND is_expired(info.entry.created_at, now, ttl_days):
                remove[info.key] = "expired"; CONTINUE
            live.append((info.mtime, info.key, info.size, [o.sha256 for o in info.entry.outputs]))
        blobs = {sha: (size, mtime)} from scanning blobs/*/* (regular files with hex names only)
        refcount = Counter(s for (_, _, _, shas) in live for s in shas)
        total = sum(size for (_, _, size, _) in live) + sum(blobs[s][0] for s in refcount if s in blobs)
        IF max_bytes is not None AND total > max_bytes:          # "is not None": 0 is valid
            target = int(max_bytes * EVICT_LOW_WATER_RATIO)
            FOR (mtime, key, size, shas) IN sorted(live):          # LRU; key tie-break
                IF total <= target: BREAK
                remove[key] = "lru"; total -= size
                FOR s IN shas:
                    refcount[s] -= 1
                    IF refcount[s] == 0 AND s IN blobs: total -= blobs[s][0]
        protected = self._referenced_blobs(exclude_v1_keys=set(remove))  # dry_run: same reads
        orphans = [s for s, (sz, mt) in blobs.items()
                   if s not in protected and now.timestamp() - mt > BLOB_SWEEP_GRACE_SECONDS]
        stale_tmp = [t for t in scandir(tmp) if now.timestamp() - mtime(t) > TMP_SWEEP_GRACE_SECONDS]
        stale_trash = [d for d in scandir(root) if d.name.startswith(TRASH_DIR_PREFIX) and is real dir]
        IF NOT dry_run: delete the removed entries, orphans, stale_tmp; rmtree(stale_trash)
                        # each deletion goes through delete_entry/delete_blob (checks first)
                        # and tolerates FileNotFoundError
        self._approx_total = None
        RETURN PruneReport(removed_entries=Counter(remove.values()), removed_blobs=len(orphans),
                           removed_tmp=len(stale_tmp), bytes_before=..., bytes_after=...,
                           dry_run=dry_run)

    def maybe_enforce_limits(self, *, now):                    # INLINE, bounded (D19)
        IF self._approx_total is None:
            scan = self._scan_sizes(budget)     # AS BUILT: one private _Budget (files, file bytes,
            #   directory entries, blobs) over entries/** of any version and depth; the SAME budget
            #   bounds the prune triggered below. Caps: INLINE_PRUNE_MAX_ENTRIES,
            #   INLINE_PRUNE_MAX_ENTRY_FILE_BYTES, INLINE_PRUNE_MAX_WALK_ITEMS, INLINE_PRUNE_MAX_BLOBS.
            # lstat-only walk; stops as soon as any limit is exceeded
            IF scan.over_limit: RETURN PruneReport(deferred=True)   # coordinator: WARNING
            self._approx_total = scan.total_bytes                  # entries + blobs bytes
        IF self._approx_total <= self.max_bytes: RETURN None
        RETURN self.prune(now=now, max_bytes=self.max_bytes, ttl_days=self.ttl_days)

    def clear(self):
        self._checks(self.root)
        trash = join(root, TRASH_DIR_PREFIX + uuid4().hex); os.mkdir(trash, CACHE_DIR_MODE)
        FOR d IN (ENTRIES_DIR, BLOBS_DIR, TMP_DIR):
            TRY os.replace(join(root, d), join(trash, d)) EXCEPT FileNotFoundError: pass
        counts = count files under trash (no symlink following); shutil.rmtree(trash)  # no ignore
        FOR d IN (ENTRIES_DIR, BLOBS_DIR, TMP_DIR):
            IF os.path.lexists(join(root, d)):
                RAISE CacheError(REASON_STORE_ERROR, f"clear incomplete: {d}")
        also rmtree any stale trash-* dirs; self._approx_total = None; RETURN ClearReport(...)

    def verify(self):                                           # read-only (Rev 3)
        problems = []
        FOR info IN self.iter_entries():
            invalid -> corrupt_entry / key_mismatch
            FOR o IN info.entry.outputs: NOT has_blob(o.sha256) -> missing_blob
        FOR other version dirs under entries/: foreign_version (informational)
        FOR each blob: re-hash via read_blob(max_bytes=st_size) -> name != sha OR size != st_size
                       -> corrupt_blob
        orphans (unreferenced by any hex token) -> orphan_blob (informational, NOT a failure)
        symlinks / junk -> symlink / unexpected_file
        RETURN VerifyReport(ok = no corrupt_entry/key_mismatch/missing_blob/corrupt_blob/symlink
                            problems, problems=problems)

def is_expired(created_at: datetime, now: datetime, ttl_days: int) -> bool:  # ONE TTL helper
    RETURN (now - created_at) > timedelta(days=ttl_days)   # both aware; ttl <= MAX_TTL_DAYS
```

**`_atomic_write_bytes(dst, body)`.**

1. `ensure_dir_chain(root, dirname(dst))`.
2. Write to the temp file returned by `safeio.create_exclusive`. Loop until every byte is written,
   then close.
3. `os.replace(tmp, dst)`.
4. In `finally`, unlink the temp file if it still exists.

There is no fsync, which matches `RunStateStore.save`. A torn blob fails verification and becomes
a miss.

#### 8.4.4 Failure matrix (store)

| Situation | Behaviour | Outcome / event |
|-----------|-----------|-----------------|
| Entry absent | `get_entry` returns None | miss `not_found` |
| Entry is oversized, a FIFO, a symlink, invalid JSON, deeply nested, schema-invalid, has a naive `created_at`, contains `inf`, or has an unbounded string | `parse_entry_bytes` or `safeio` raises `CacheIntegrityError` | evict; miss `corrupt_entry`; `cache.corrupt` (WARNING) |
| `entry.key` ≠ filename or lookup key | `CacheIntegrityError(key_mismatch)` | evict; miss `key_mismatch` |
| Entry in another version directory (`entries/v2/…`) | never looked at by v1 lookups | `ao cache verify` reports `foreign_version`; prune never deletes it; its blob references are protected (D18) |
| Blob absent | `CacheBlobMissingError` | evict; miss `blob_missing` |
| Blob bytes or size wrong, or the blob is irregular | detected by restore or `read_blob` | evict + delete the blob; miss `blob_corrupt` |
| Root or `.orchestrator` is a symlink, foreign-owned, group/other-writable while not ours, outside the workspace, or has an unknown layout | `CacheLayoutError` | lookup `store_unavailable` (not storable); WARNING once per run |
| A symlinked `entries/`, `entries/v1/`, shard or blob directory | `CacheUnsafePathError` (from `_checks`, before any read, `utime` or `unlink`) | miss `unsafe_path`; **not storable; never evicted** (D33); `cache.corrupt` WARNING with `reason=unsafe_path` |
| The entry file itself is a symlink or FIFO (final component) | `O_NOFOLLOW` / `S_ISREG` → `CacheIntegrityError(corrupt_entry)` | evict (unlinks the link itself, after `_checks` passed on its directory); miss `corrupt_entry` |
| Disk full / EACCES / ENAMETOOLONG | `OSError` | store skipped `store_error` (WARNING); run unaffected |
| Concurrent same-key writers | blobs first, then `os.replace` of the entry | last writer wins; each entry self-consistent |
| Concurrent `prune` vs `put` | grace period on fresh blobs; dedupe touch | rarely a dangling entry, which is later a miss + evict (benign) |
| Concurrent `clear` vs `put` | the put's `os.replace` fails ENOENT, or the entry lands referencing trashed blobs | skipped store, or later a miss + evict (benign) |
| Huge or planted cache (more than `INLINE_PRUNE_MAX_ENTRIES` entries, `INLINE_PRUNE_MAX_ENTRY_FILE_BYTES` of entry files, `INLINE_PRUNE_MAX_WALK_ITEMS` directory entries or `INLINE_PRUNE_MAX_BLOBS` blobs, under `entries/**` of any version) | inline enforcement deferred | `cache.evict` with `reason=deferred` (WARNING); `ao cache prune` needed |
| Clock skew (future `created_at`) | age is negative, so not expired | documented |

**Concurrency.**

- **Within a run:** lookups and stores happen only on the engine's main thread (ADR-0007 D3).
- **Across processes:** atomic renames, content addressing and grace periods. No locks.

**Subtasks.**

| Task | Scope |
|------|-------|
| T-U7ckfd | `LocalFsCacheStore(CacheStore)`: factory; `check` and per-operation `_checks` (with `CacheUnsafePathError`); `ensure_layout`; entries; blobs; `has_blob`; `_atomic_write_bytes`; `is_expired`; placeholder `maybe_enforce_limits` |
| T-HjxNQ0 | adds the `CacheAdmin` base: streaming `iter_entries`; the mark phase; `stats`; `prune`; `maybe_enforce_limits` bounded by entry count and entry-file bytes; `clear` (fixed); read-only `verify` |

**Edge cases.**

- An empty cache is a no-op.
- If `max_bytes` is smaller than one entry, the entry just written may be evicted immediately. The
  coordinator logs it.
- Junk files are reported, never parsed as entries.
- A leftover `trash-*` directory is removed by the next `prune` or `clear`.

### 8.5 Module M5 — capture and restore (`cache/restore.py`)

**Module definition.**

| Aspect | Details |
|--------|---------|
| Purpose | All workspace-side byte I/O. Reads declared outputs into blobs (store), and writes verified blobs to spec-derived, re-validated, non-sensitive destinations (hit). |
| Inputs | Spec-derived `{rel: abs}` maps, `CacheEntry`, `CacheStore`, limits, `workspace_root`. |
| Outputs | `capture_outputs` returns `list[OutputRecord]` or raises `StoreSkip(reason)`. `restore_outputs` returns `RestoreResult` or raises `RestoreMiss(reason, evict, blob, detail)`; a restore miss is never storable. |
| Dependencies | `os`, `stat`, `hashlib`, `threading`, `uuid`, `safeio`, `types`, `constants`. |

```text
FUNCTION capture_outputs(output_abs, store, *, max_entry_bytes) -> list[OutputRecord]:
    records = []; remaining = max_entry_bytes
    FOR rel IN sorted(output_abs):
        a = output_abs[rel]
        TRY fd = safeio.open_regular_read(a)
        EXCEPT FileNotFoundError: RAISE StoreSkip(REASON_OUTPUT_MISSING, rel)
        EXCEPT NotRegularFileError: RAISE StoreSkip(REASON_OUTPUT_NOT_REGULAR, rel)
        EXCEPT OSError: RAISE StoreSkip(REASON_STORE_ERROR, rel)
        WITH os.fdopen(fd, "rb") as fh:
            st = os.fstat(fh.fileno())
            IF st.st_size > remaining: RAISE StoreSkip(REASON_ENTRY_TOO_LARGE, rel)
            TRY ref = store.put_blob(fh, max_bytes=remaining) EXCEPT CacheTooLargeError: RAISE StoreSkip(REASON_ENTRY_TOO_LARGE, rel)
            remaining -= ref.size
            records.append(OutputRecord(path=rel, sha256=ref.sha256, size=ref.size, mode=stat.S_IMODE(st.st_mode) & STORED_MODE_MASK))
    RETURN records

FUNCTION restore_outputs(entry, expected, store, *, workspace_root, max_entry_bytes) -> RestoreResult:
    """`expected` = {normalized rel path: absolute destination} derived ONLY from the current spec."""
    manifest = {}
    FOR o IN entry.outputs:
        IF o.path IN manifest: RAISE RestoreMiss(REASON_MANIFEST_MISMATCH, evict=True)
        manifest[o.path] = o
    IF set(manifest) != set(expected): RAISE RestoreMiss(REASON_MANIFEST_MISMATCH, evict=True)   # '../escape' lands here (M-1)
    IF sum(o.size for o in entry.outputs) > max_entry_bytes: RAISE RestoreMiss(REASON_CORRUPT_ENTRY, evict=True)
    staged = []
    TRY:
        FOR rel IN sorted(expected):                                     # phase 1: validate + stage + verify ALL
            rec = manifest[rel]; dest = expected[rel]
            IF safeio.is_sensitive_rel_path(rel): RAISE RestoreMiss(REASON_SENSITIVE_OUTPUT, evict=False)  # re-assert (M-14)
            parent = os.path.dirname(dest)
            safeio.ensure_dir_chain(workspace_root, parent, mode=0o777)  # component-wise, refuses symlinks (umask applies)
            IF os.path.realpath(dest) != dest: RAISE RestoreMiss(REASON_RESTORE_FAILED, evict=False)   # a link appeared (M-5)
            tmp = os.path.join(parent, f"{RESTORE_TMP_PREFIX}{os.getpid()}-{threading.get_ident()}-{uuid4().hex}{TMP_SUFFIX}")
            fd = safeio.create_exclusive(tmp, TMP_FILE_MODE); staged.append((tmp, dest, rec.mode))
            WITH os.fdopen(fd, "wb") as fh:
                hw = HashingWriter(fh)
                TRY n = store.read_blob(rec.sha256, hw, max_bytes=rec.size)
                EXCEPT CacheBlobMissingError: RAISE RestoreMiss(REASON_BLOB_MISSING, evict=True)
                EXCEPT CacheIntegrityError: RAISE RestoreMiss(REASON_BLOB_CORRUPT, evict=True, blob=rec.sha256)
            IF n != rec.size OR hw.hexdigest() != rec.sha256: RAISE RestoreMiss(REASON_BLOB_CORRUPT, evict=True, blob=rec.sha256)
        FOR (tmp, dest, mode) IN staged:                                 # phase 2: commit
            os.chmod(tmp, mode & RESTORED_MODE_MASK)                     # M-8
            os.replace(tmp, dest)
        committed = len(staged); staged = []
        RETURN RestoreResult(files=committed, bytes=sum(o.size for o in entry.outputs))
    EXCEPT (OSError, SafeIOError) as e: RAISE RestoreMiss(REASON_RESTORE_FAILED, evict=False, detail=type(e).__name__)
    FINALLY:
        FOR (tmp, _, _) IN staged: TRY os.unlink(tmp) EXCEPT FileNotFoundError: pass
```

**As built (DV-11, ADR-0019 D20 addendum).** Phase 1 also refuses a destination that is a
directory (`restore_failed`). Phase 2 does not `chmod` by path: the mode (`mode & 0o755`) is applied
with `os.fchmod` on the still-open staging descriptor after its hash is verified. Before phase 2
each existing destination is hard-linked to `<staging>.bak` (`RESTORE_BACKUP_SUFFIX`); a failing
rename rolls the earlier renames back and a successful restore leaves no backup litter. Where hard
links are unavailable that one file is not rolled back, and parent directories created for the
restore stay. A transient `read_blob` `OSError` is `RestoreMiss(store_error, evict=False)`;
`CacheUnsafePathError` still propagates. `capture_outputs` also refuses (`output_not_regular_file`)
a path whose `realpath` differs from itself, so a swapped parent directory is never followed.

Every `RestoreMiss` raised by `restore_outputs` is **not storable** for this dispatch: the coordinator
sets no pending token. A partially committed restore contradicts the "absent" prior preseed
(developer #11). The task still dispatches normally.

**Atomicity statement.**

- Every output is validated, staged and verified **before any destination is touched**.
- Each commit is an atomic `rename(2)`.
- A crash or `OSError` part-way through the commit loop can leave some destinations new and others
  untouched. That state is never accepted as a success:
  - the engine marks the task `succeeded` only after `restore_outputs` returns;
  - a failed restore falls through to a real dispatch, which rewrites every declared output;
  - after a crash, the task resumes as `pending`.
- Temp files are removed on every failure path. Only a hard crash can leave
  `.ao-result-cache-*.tmp[.bak]` litter. As built, `ao cache prune` sweeps it (`restore_sweep`,
  see §8.9); it is never exempted from directory hashing (D8 addendum), so a leftover costs at
  worst a false miss.

**Failure matrix (restore).**

| Situation | Reason | Evict | Delete blob | Storable this dispatch |
|-----------|--------|-------|-------------|------------------------|
| Manifest ≠ spec outputs; duplicates; `../escape` | `manifest_mismatch` | yes | no | no |
| Sum of sizes > `max_entry_bytes` | `corrupt_entry` | yes | no | no |
| Blob missing | `blob_missing` | yes | no | no |
| Blob bytes, size or type wrong | `blob_corrupt` | yes | yes | no |
| Destination sensitive (re-check) | `sensitive_output` | no | no | no |
| Destination is a directory; a link appeared; EACCES; ENOSPC; unsafe parent chain | `restore_failed` | no | no | no |

**Subtasks.**

1. `HashingWriter`.
2. `capture_outputs`.
3. `restore_outputs`: validate, stage, verify, commit.
4. Temp-file cleanup.
5. Adversarial tests ADV-1, ADV-3, ADV-5, ADV-6, ADV-8 and ADV-10, run against `InMemoryCacheStore`.

**Edge cases.**

- A missing parent directory is created component by component.
- An existing destination file is replaced atomically.
- A zero-byte output becomes a valid empty blob.
- When two outputs share a blob, it is read twice.
- A stored mode of `0o000` is restored as `0o000`.

---
### 8.6 Module M6 — coordinator and records (`cache/coordinator.py`, `cache/records.py`)

**Module definition.**

| | |
|---|---|
| **Purpose** | The single engine-facing implementation of `ResultCacheHook`. It runs the author policy, eligibility, repo state, key build, store lookup, mode handling, restore, guards, logging and record construction. **It never raises into the engine** (D32), and it **never mutates `RunState`**; the engine does that (D12). |
| **Inputs** | `LookupRequest` (at prepare time). `PendingStore` plus `TaskRunState` (at settle time). |
| **Outputs** | `LookupOutcome(hit, record, pending)`, `StoreResult(stored, reason)`. |
| **Dependencies** | `settings`, `eligibility`, `repo_state`, `fingerprint`, `keys`, `store`, `restore`, `records`, `artifacts.LocalFsArtifactStore`, `agent_orchestrator.__version__`. |

#### 8.6.1 Contracts (`types.py`)

`types.py` has `from __future__ import annotations` and imports `ResultCacheRecord`,
`TaskRunState`, `TaskSpec`, `WorkflowSpec`, `AgentSpec` and `ArtifactStore` **under
`TYPE_CHECKING` only**, so T-FJH6LI does not depend on T-28J9oR (which adds
`ResultCacheRecord`).

```python
@dataclass(frozen=True)
class LookupRequest:
    task: TaskSpec
    workflow: WorkflowSpec
    agents: Mapping[str, AgentSpec]
    run_id: str
    injected: bool  # state.tasks[tid].origin == "injected"
    integration_active: bool  # state.integration.active
    artifact_store: ArtifactStore  # the engine's self._store
    general_instruction_paths: tuple[str, ...]
    dynamic_input_paths: tuple[str, ...]
    repo_paths: Mapping[str, str]  # ctx.repo_paths (absolute)
    dispatch_cycle: int  # ts.dispatch_cycle AFTER the engine's increment (kept, D12)
    now: datetime  # engine clock


@dataclass(frozen=True)
class PendingStore:  # carried on _RunContext.result_cache_pending[tid]
    request: LookupRequest
    key: CacheKey
    heads: Mapping[str, str]  # lookup-time HEADs (guard 2, always)
    worktree: frozenset[tuple[object, ...]]  # lookup-time snapshot (guard 3), taken lazily


@dataclass(frozen=True)
class LookupOutcome:
    hit: bool
    record: ResultCacheRecord | None  # None => not opted in / cache disabled: write nothing
    pending: PendingStore | None  # set ONLY for a storable outcome


@dataclass(frozen=True)
class StoreResult:
    stored: bool
    reason: str | None = None


class ResultCacheHook(Protocol):
    """What engine.py depends on (mirrors the BudgetManager / Monitor injection pattern)."""

    def lookup(self, request: LookupRequest, log: logging.LoggerAdapter) -> LookupOutcome: ...

    def store_success(
        self,
        pending: PendingStore,
        *,
        ts: TaskRunState,
        now: datetime,
        log: logging.LoggerAdapter,
    ) -> StoreResult: ...
```

#### 8.6.2 `ResultCache`

```text
EXPECTED_ERRORS = (OSError, CacheError, SafeIOError, ValidationError, GitError, ValueError,
                   TypeError, RecursionError, OverflowError, subprocess.SubprocessError)

class ResultCache:                                                  # implements ResultCacheHook
    def __init__(self, store, settings, *, workspace_root, cache_root, heads=None,
                 worktree=None, cli_versions=None, environ=None, strict=False,
                 warnings=()): ...      # every collaborator injectable; heads defaults to
                                        # RepoHeadReader(workspace_root=workspace_root)
    @classmethod
    def from_settings(cls, *, workspace_root, settings) -> ResultCache:   # NEVER named `open`
        ws = str(Path(workspace_root).resolve())
        store = LocalFsCacheStore.for_workspace(ws, max_bytes=settings.max_bytes,
                                                ttl_days=settings.ttl_days)   # no I/O
        marker = nested_repo_marker(ws)                                       # lstat only
        warnings = [] IF marker is None ELSE [
            f"workspace {ws} is inside the git repository at {marker}; its HEAD is not part "
            "of result-cache keys (see the authoring guide)"]
        RETURN cls(store, settings, workspace_root=ws, cache_root=store.root,
                   environ=os.environ, warnings=tuple(warnings))
    root = property(lambda self: self._cache_root)
    warnings = property(lambda self: self._warnings)                # printed by the CLI banner

    # ---------------- engine-facing boundary (D32) ----------------
    FUNCTION lookup(self, req, log) -> LookupOutcome:
        IF self._disabled: RETURN LookupOutcome(False, None, None)
        TRY RETURN self._lookup(req, log)
        EXCEPT EXPECTED_ERRORS as e:
            log.error("result cache lookup failed (%s); treating as a miss", type(e).__name__,
                      exc_info=True, extra={"event": EVENT_SKIP, "phase": "lookup",
                                            "reason": REASON_STORE_ERROR})
            RETURN LookupOutcome(False, make_miss_record(req, REASON_STORE_ERROR, None,
                                                         self._mode, self._source), None)
        EXCEPT Exception:
            IF self._strict: RAISE
            self._disabled = True
            log.error("result cache disabled for the rest of this run after an unexpected "
                      "error", exc_info=True, extra={"event": EVENT_DISABLED})
            RETURN LookupOutcome(False, None, None)

    FUNCTION store_success(self, pending, *, ts, now, log) -> StoreResult:   # same shape
        IF self._disabled: RETURN StoreResult(False, REASON_CACHE_DISABLED)
        TRY RETURN self._store_success(pending, ts=ts, now=now, log=log)
        EXCEPT EXPECTED_ERRORS: log.error(..., exc_info=True, extra={"event": EVENT_SKIP,
                                    "phase": "store", "reason": REASON_STORE_ERROR})
                                RETURN StoreResult(False, REASON_STORE_ERROR)
        EXCEPT Exception: IF self._strict: RAISE
                          self._disabled = True; log.error(... EVENT_DISABLED ...)
                          RETURN StoreResult(False, REASON_CACHE_DISABLED)

    # ---------------- lookup ----------------
    FUNCTION _lookup(self, req, log) -> LookupOutcome:
        IF NOT task_cache_policy(req.task, req.workflow, injected=req.injected):
            log.debug("not opted in", extra={"event": EVENT_SKIP, "phase": "lookup",
                                             "reason": REASON_NOT_OPTED_IN})
            RETURN LookupOutcome(False, None, None)                 # no record (D1)
        el = check_eligibility(req.task, req.workflow, req.agents,
                               integration_active=req.integration_active)
        IF NOT el.eligible: RETURN self._ineligible(req, el.reason, el.detail, log)
        IF NOT isinstance(req.artifact_store, LocalFsArtifactStore):
            RETURN self._ineligible(req, REASON_ARTIFACT_STORE_UNSUPPORTED, None, log)
        TRY self._store.check()                                     # root + ownership + layout
        EXCEPT (CacheLayoutError, SafeIOError) as e:
            self._warn_store_unavailable_once(log, e)
            RETURN LookupOutcome(False, make_miss_record(req, REASON_STORE_UNAVAILABLE, None,
                                                         self._mode, self._source), None)
        TRY:
            heads = self._heads.read(req.repo_paths)                # key + guard 2 (always)
            agent = resolve_effective_agent(req.task, req.agents[req.task.agent],
                                            req.workflow.defaults.model)
            key = build_cache_key(self._key_request(req, agent, heads), self._deps)
        EXCEPT UncacheableError as e: RETURN self._ineligible(req, e.reason, e.detail, log)
        TRY entry = self._store.get_entry(key.key)
        EXCEPT CacheUnsafePathError as e:                           # D33: never evict
            log.warning("result cache path is unsafe; not evicted", extra={
                "event": EVENT_CORRUPT, "key": key.key, "reason": REASON_UNSAFE_PATH})
            RETURN self._miss(req, REASON_UNSAFE_PATH, key, None, None, log)  # not storable
        EXCEPT CacheIntegrityError as e:
            self._evict(key.key, e.reason, log, corrupt=True)       # delete_entry checks first
            RETURN self._miss_storable(req, e.reason, key, heads, log)
        IF entry is None: RETURN self._miss_storable(req, REASON_NOT_FOUND, key, heads, log)
        IF self._ttl is not None AND is_expired(entry.created_at, req.now, self._ttl):
            self._evict(key.key, REASON_EXPIRED, log)
            RETURN self._miss_storable(req, REASON_EXPIRED, key, heads, log)
        IF self._mode == MODE_SHADOW:                               # measure, never restore
            IF NOT all(self._store.has_blob(o.sha256) for o in entry.outputs):
                self._evict(key.key, REASON_BLOB_MISSING, log, corrupt=True)
                RETURN self._miss_storable(req, REASON_BLOB_MISSING, key, heads, log)
            pending, store_reason = self._storable(req, key, heads, log)    # lazy guard 3
            log.info("result cache would hit", extra={"event": EVENT_WOULD_HIT, "key": key.key,
                     "saved_cost_usd": entry.usage.cost_usd,
                     "source_run_id": entry.source.run_id})
            RETURN LookupOutcome(False, make_would_hit_record(entry, key.key, req, self._mode,
                                 self._source, store_reason=store_reason), pending)
        TRY restore_outputs(entry, key.output_abs, self._store, workspace_root=self._ws,
                            max_entry_bytes=self._max_entry)
        EXCEPT RestoreMiss as m:
            IF m.evict:
                self._evict(key.key, m.reason, log, corrupt=True)
                IF m.blob: self._store.delete_blob(m.blob)          # content-addressed + wrong
            RETURN self._miss(req, m.reason, key, None, None, log)  # NOT storable
        TRY self._store.touch_entry(key.key, req.now)
        EXCEPT (OSError, CacheUnsafePathError): log.debug(...)      # LRU is best effort
        log.info("result cache hit", extra={"event": EVENT_HIT, "key": key.key,
                 "outputs": [{"path": o.path, "sha256": o.sha256} for o in entry.outputs],
                 "bytes": sum(o.size for o in entry.outputs),
                 "saved_cost_usd": entry.usage.cost_usd, "source_run_id": entry.source.run_id,
                 "source_created_at": entry.created_at.isoformat(),
                 "source_ao_version": entry.source.ao_version})
        # A hit never takes the guard-3 snapshot: it is store-only (manager B, Rev 3).
        RETURN LookupOutcome(True, make_hit_record(entry, key.key, req, self._mode,
                                                   self._source), None)

    FUNCTION _storable(self, req, key, heads, log) -> (PendingStore | None, str | None):
        """Lazy guard-3 snapshot, taken only once an outcome is known to be storable."""
        TRY wt = self._worktree.snapshot(req.repo_paths, self._ws,
                                         exclude_abs=frozenset(key.output_abs.values()))
        EXCEPT UncacheableError as e:                    # REASON_REPO_WORKTREE_PROBE_FAILED
            log.info("result cache: outcome not storable", extra={"event": EVENT_SKIP,
                     "phase": "store", "reason": e.reason, "reason_detail": e.detail})
            RETURN None, e.reason                        # NOT storable; never ineligible
        RETURN PendingStore(req, key, heads, wt), None

    FUNCTION _miss_storable(self, req, reason, key, heads, log) -> LookupOutcome:
        pending, store_reason = self._storable(req, key, heads, log)
        RETURN self._miss(req, reason, key, pending, store_reason, log)

    # ---------------- store ----------------
    FUNCTION _store_success(self, pending, *, ts, now, log) -> StoreResult:
        req = pending.request
        TRY:
            heads_now = self._heads.read(req.repo_paths)
            IF dict(heads_now) != dict(pending.heads):
                RETURN self._skip(REASON_REPO_HEAD_MOVED, log)                   # guard 2
            agent = resolve_effective_agent(req.task, req.agents[req.task.agent],
                                            req.workflow.defaults.model)
            again = build_cache_key(self._key_request(req, agent, heads_now), self._deps,
                                    preseed=pending.key.preseed)
            IF again.key != pending.key.key:                                     # guard 1
                RETURN self._skip(REASON_KEY_CHANGED_DURING_RUN, log,
                                  components=again.components)
            wt = self._worktree.snapshot(req.repo_paths, self._ws,
                                         exclude_abs=frozenset(pending.key.output_abs.values()))
            IF wt != pending.worktree: RETURN self._skip(REASON_REPO_WORKTREE_CHANGED, log)
        EXCEPT UncacheableError as e: RETURN self._skip(e.reason, log)          # incl. probe
        TRY outputs = capture_outputs(pending.key.output_abs, self._store,
                                      max_entry_bytes=self._max_entry)
        EXCEPT StoreSkip as s: RETURN self._skip(s.reason, log)
        entry = CacheEntry(schema=ENTRY_SCHEMA, key=pending.key.key,
            key_schema=KEY_SCHEMA_VERSION, created_at=now,
            source=EntrySource(workflow_id=clip(req.workflow.id), task_id=clip(req.task.id),
                               run_id=clip(req.run_id), agent=clip(req.task.agent),
                               ao_version=clip(__version__),
                               cli_version=pending.key.cli_version),
            usage=EntryUsage(cost_usd=min(ts.cumulative_cost_usd, MAX_ENTRY_COST_USD),
                             input_tokens=..., output_tokens=...,
                             cache_creation_input_tokens=..., cache_read_input_tokens=...,
                             duration_seconds=clamped(ts.started_at, ts.ended_at),
                             attempts=ts.attempts, model=ts.model, effort=ts.effort,
                             actuals_available=ts.cumulative_cost_usd > 0
                                               or ts.cumulative_input_tokens > 0),
            outputs=outputs, key_summary=pending.key.summary)
        self._store.put_entry(entry)
        log.info("result cache store", extra={"event": EVENT_STORE, "key": entry.key,
                 "outputs": len(outputs), "bytes": sum(o.size for o in outputs)})
        TRY report = self._store.maybe_enforce_limits(now=now)
        EXCEPT (CacheError, OSError, SafeIOError): report = None
        IF report is not None AND report.deferred:
            log.warning("result cache over its size cap; run `ao cache prune`",
                        extra={"event": EVENT_EVICT, "reason": REASON_EVICT_DEFERRED})
        ELIF report is not None AND report.total_removed:
            log.info("result cache evicted", extra={"event": EVENT_EVICT,
                     "reason": REASON_EVICT_LRU, "entries": report.total_removed})
        RETURN StoreResult(True)

    FUNCTION _control_paths_abs(self, req) -> frozenset[str]:   # unresolvable paths ignored
        raw = [r.verdict_path for r in req.workflow.branches]
              + [l.gate_output_path for l in req.workflow.loops]
              + [b.verdict_path for b in req.workflow.circuit_breakers if b.verdict_path]
              + [b.path for b in req.workflow.circuit_breakers if b.path]
              + ([req.workflow.prompt_path] if req.workflow.prompt_path else [])
        RETURN frozenset(a for a in (try_resolve(req.artifact_store, p) for p in raw)
                         if a is not None)
```

The helpers behave as follows:

| Helper | Behaviour |
|---|---|
| `_ineligible` | Builds `make_ineligible_record` and emits `cache.skip` (`phase=lookup`, INFO). It returns no pending. |
| `_miss(req, reason, key, pending, store_reason, log)` | Builds `make_miss_record` (with `store_reason` when the outcome is not storable because the probe failed) and emits `cache.miss` (INFO). The event carries `reason`, `key`, and `components` when a key exists. It returns the given pending. |
| `_storable` | Takes the guard-3 snapshot lazily. On failure it returns no pending and the `store_reason`; it never makes the task ineligible. |
| `_evict` | Calls `delete_entry` (which runs the component checks first), then emits `cache.evict`. When `corrupt=True`, it also emits `cache.corrupt` (WARNING). It is never called for `unsafe_path`. |
| `_skip` | Emits `cache.skip` (`phase=store`, INFO). For `store_error` it logs at WARNING. It returns `StoreResult(False, reason)`. |
| `clip()` | Truncates to `MAX_TEXT_CHARS`. |

#### 8.6.3 `records.py` (builders only)

```text
FUNCTION make_hit_record(entry, key, req, mode, source) -> ResultCacheRecord:
    RETURN ResultCacheRecord(outcome=RESULT_CACHE_HIT, mode=mode, mode_source=source, key=key,
        dispatch_cycle=req.dispatch_cycle, at=req.now.isoformat(), ended_at=None,  # engine fills
        saved_cost_usd=entry.usage.cost_usd, saved_input_tokens=entry.usage.input_tokens,
        saved_output_tokens=entry.usage.output_tokens,
        saved_seconds=entry.usage.duration_seconds, source_run_id=entry.source.run_id)
        # hit and saved_tokens are derived by the record's validator (D35)
FUNCTION make_would_hit_record(entry, key, req, mode, source, *, store_reason=None)
    -> the same, with outcome=RESULT_CACHE_WOULD_HIT and store_reason
FUNCTION make_miss_record(req, reason, key, mode, source, detail=None, *, store_reason=None)
    -> outcome=RESULT_CACHE_MISS
FUNCTION make_ineligible_record(req, reason, detail, mode, source)
    -> outcome=RESULT_CACHE_INELIGIBLE, key=None
```

#### 8.6.4 Record lifecycle and reasons

| Moment | Who writes | What |
|--------|-----------|------|
| A prepare pass reaches the lookup (cache on) and the task is opted in | engine (`_result_cache_lookup`) | `state.result_cache[tid] = outcome.record` (overwrites). `dispatch_cycle` = the post-increment value. |
| Not opted in, or the cache was disabled after an error | — | nothing |
| Hit | engine | additionally sets `record.ended_at = ts.ended_at` (binding, D14) |
| Settle of that dispatch, success with a pending token | engine (`_result_cache_store`) | `stored` / `store_reason` |
| Settle failure or requeue | — | nothing; the next prepare overwrites it |
| A later session with the cache off re-dispatches the task | — | nothing; the record goes **stale** (cycle or `ended_at` mismatch) and every reader ignores it |

**Miss reasons:**

| Reason | Storable? |
|--------|-----------|
| `not_found` | storable |
| `expired` | storable |
| `corrupt_entry` | storable |
| `key_mismatch` | storable |
| `blob_missing` (shadow mode) | storable |
| `manifest_mismatch` | **not** storable |
| `blob_missing` (restore) | **not** storable |
| `blob_corrupt` | **not** storable |
| `sensitive_output` (restore re-check) | **not** storable |
| `restore_failed` | **not** storable |
| `unsafe_path` (D33) | **not** storable; never evicted |
| `store_unavailable` | **not** storable |
| `store_error` | **not** storable |

A storable miss or a shadow `would_hit` whose lazy guard-3 snapshot fails has no pending token;
its record carries `stored: false` and `store_reason: repo_worktree_probe_failed`.

**Store-skip reasons:**

- `repo_head_moved`
- `key_changed_during_run`
- `repo_worktree_changed`
- `repo_worktree_probe_failed` (at lookup, lazily, or at settle)
- `output_missing`
- `output_not_regular_file`
- `entry_too_large`
- `store_error`
- `cache_disabled`
- any key-build reason from the recompute

**Subtasks:**

1. Contracts (T-FJH6LI).
2. `from_settings` (no I/O besides the `lstat`-only nested-repo check) and the per-lookup
   `check`.
3. `_lookup` with the two modes, the unsafe-path branch and the lazy `_storable`.
4. `_store_success` with the three guards.
5. The boundary and strict flag.
6. Events.
7. `records.py`.
8. Unit tests using fakes for store, heads, worktree, CLI version and clock.

**Edge cases:**

- `ttl_days=None` means the cache never expires.
- A `created_at` in the future is not expired.
- `shadow` mode never writes into the workspace.
- A hit never calls `WorktreeProbe.snapshot`.
- A HEAD read that fails at settle → `repo_head_unavailable` skip.
- A disabled cache returns nothing for the rest of the run.

### 8.7 Module M7 — engine integration (`engine.py`)

#### 8.7.1 Exact seams (net ≤ +110 formatted lines; ≤ 12 added lines inside existing functions)

**Budget, measured honestly (Rev 3).** The snippets below were run through `ruff format`
(line length 100): about **123 lines added and 20 removed, net ≈ +103**. The 20 removed lines
are the existing stale-charge reversal block in `_prepare_and_maybe_dispatch` (engine.py
~1215–1243), which moves verbatim into `_reverse_stale_charge` and is replaced there by one call.
The stated budget is therefore **net ≤ +110 formatted lines**, with **≤ 12 added lines inside
existing functions**: `__init__` (4), the prepare call site (5), the shared-helper call (1) and
the settle call site (2). Every added line is ≤ 100 columns (`ruff check` and
`ruff format --check` run in CI).

**(a) Constructor.** Add one keyword parameter after `run_prompt`, plus its docstring entry. Import
`ResultCacheHook` and `PendingStore` from `.cache.types` **under `TYPE_CHECKING` only**.

```python
        result_cache: ResultCacheHook | None = None,
    ...
        # Result cache (E-Rc4Hk8, ADR-0019). None (the default, and what the CLI passes
        # unless the operator enabled it) means NO cache code runs or is imported (NFR-1).
        self._result_cache = result_cache
```

**(b) `_RunContext` field.**

```python
    # E-Rc4Hk8: task id -> PendingStore for a storable result-cache lookup of the CURRENT
    # dispatch; popped at every prepare pass and at settle; never written when the cache is off.
    result_cache_pending: dict[str, PendingStore] = field(default_factory=dict)
```

**(c) Call site in `_prepare_and_maybe_dispatch`.** Place it immediately after the dynamic-input
collection loop and immediately **before** `_estimate = 0` (engine.py ~1182–1189). It is 5
formatted lines:

```python
        # E-Rc4Hk8 result cache (ADR-0019 D11). Approval/human gates (E-Ag7Pw3) run BEFORE this.
        if self._result_cache is not None and self._result_cache_lookup(
            tid, task, workflow, state, ctx, ts_pre, dynamic_input_paths, task_log
        ):
            return DispatchPrep(signal="skipped")
```

**(c') The shared stale-charge helper.** Inside the budget-gate block, the existing inline block
becomes one call, with identical behaviour (the existing budget resume tests cover it):

```python
            self._reverse_stale_charge(tid, ts_pre, state, run_log)
```

**(d) Call site in `_settle_completed_task`.** Place it at the top of
`if ts.status == "succeeded":` (engine.py ~2032), before `task_log.info("Task succeeded", ...)`. It
is 2 lines:

```python
            if self._result_cache is not None:
                self._result_cache_store(tid, ts, state, ctx, task_log)
```

**(e) Three new private methods.** Put them next to the other private helpers.

```python
    def _reverse_stale_charge(
        self,
        tid: str,
        ts: TaskRunState,
        state: RunState,
        run_log: logging.LoggerAdapter,
    ) -> None:
        """Release the previous cycle's charge left by a crash (R2, NFR-3, R-1b).

        Moved verbatim from the budget-gate block so the gate and the result-cache hit path
        (ADR-0019 D12) can never drift apart. `ts.dispatch_cycle` was already incremented for
        THIS dispatch, so a stale charge, if any, is exactly one cycle behind it.
        """
        if self._budget_manager is None:
            return
        stale_cycle = ts.dispatch_cycle - 1
        if cycle_key(tid, stale_cycle) not in state.budget_counters.charged_estimate:
            return
        self._budget_manager.reverse_estimate(tid, state.budget_counters, cycle=stale_cycle)
        run_log.info(
            "Reversed stale estimate for task %s on resume",
            tid,
            extra={"event": "budget.resume_reverse", "task_id": tid, "cycle": stale_cycle},
        )

    def _result_cache_lookup(
        self,
        tid: str,
        task: TaskSpec,
        workflow: WorkflowSpec,
        state: RunState,
        ctx: _RunContext,
        ts: TaskRunState,
        dynamic_input_paths: list[str],
        task_log: logging.LoggerAdapter,
    ) -> bool:
        """E-Rc4Hk8 (ADR-0019 D11/D12): result-cache lookup; on a hit, settle the task HERE.

        Mirrors the should_skip branch (done + save + "skipped"): a hit never reaches the
        budget gate, a worker, breaker evaluation or the quota timer. dispatch_cycle keeps the
        increment made above, so R-21 stays monotonic. Cache contracts are imported lazily, so
        the cache-off path imports nothing.
        """
        from .cache.types import LookupRequest

        assert self._result_cache is not None
        ctx.result_cache_pending.pop(tid, None)
        started = self._clock()
        request = LookupRequest(
            task=task,
            workflow=workflow,
            agents=ctx.agents,
            run_id=state.run_id,
            injected=ts.origin == SPAWN_ORIGIN_INJECTED,
            integration_active=state.integration.active,
            artifact_store=self._store,
            general_instruction_paths=tuple(self._resolve_general_instructions(workflow)),
            dynamic_input_paths=tuple(dynamic_input_paths),
            repo_paths=ctx.repo_paths,
            dispatch_cycle=ts.dispatch_cycle,
            now=started,
        )
        outcome = self._result_cache.lookup(request, task_log)
        if outcome.record is not None:
            state.result_cache[tid] = outcome.record
        if not outcome.hit:
            if outcome.pending is not None:
                ctx.result_cache_pending[tid] = outcome.pending
            return False
        ended = self._clock().isoformat()
        ts.status = "succeeded"
        ts.outputs_present = True
        if ts.started_at is None:
            ts.started_at = started.isoformat()
        ts.ended_at = ended
        record = state.result_cache[tid]
        state.result_cache[tid] = record.model_copy(update={"ended_at": ended})
        # crash -> resume -> hit: release the stale cycle's charge, exactly like the gate does
        self._reverse_stale_charge(tid, ts, state, ctx.run_log)
        task_log.info(
            "Task succeeded (result cache hit)",
            extra={"event": "task.end", "status": "succeeded", "cached": True},
        )
        ctx.done.add(tid)
        self._runstate.save(state)
        return True

    def _result_cache_store(
        self,
        tid: str,
        ts: TaskRunState,
        state: RunState,
        ctx: _RunContext,
        task_log: logging.LoggerAdapter,
    ) -> None:
        """E-Rc4Hk8 (ADR-0019 D13): store this dispatch's FINAL settled success (main thread)."""
        assert self._result_cache is not None
        pending = ctx.result_cache_pending.pop(tid, None)
        if pending is None:
            return
        result = self._result_cache.store_success(pending, ts=ts, now=self._clock(), log=task_log)
        record = state.result_cache.get(tid)
        if record is not None:
            state.result_cache[tid] = record.model_copy(
                update={"stored": result.stored, "store_reason": result.reason}
            )
```

The record update persists with the next `self._runstate.save(state)` in the same settle body, which
happens before breaker evaluation. `model_copy(update=...)` changes only `ended_at`, `stored` and
`store_reason`, so the record's derived fields stay valid (D35).

**Static-audit rule (developer #4).** The NFR-1 audits regex the raw text of `engine.py`, comments
included, for `\bopen\s*\(` and `\.read\s*\(`. So the factory is named `from_settings`, and no
comment in `engine.py` may contain either pattern.

**Implementation note (T-XpF1pF, 2026-10-05).** Shipped as in the blocks above, with three
differences. (1) `LookupRequest.injected` is derived from `RunState.spawned_by` (a spawn record
whose `loop_id` is `None` means emit_tasks-injected; loop clones carry loop coordinates) instead of
`ts.origin == SPAWN_ORIGIN_INJECTED`: `tests/test_spawn_provenance.py` forbids any `.origin`
comparison under `src/`. (2) The constructor keyword is the LAST parameter (after `summarizer`,
which landed after this block was written), so no positional caller changes. (3) The call-site (c)
block is followed directly by the `_estimate` comment (no blank line) to keep the added lines
inside existing functions at exactly 12. Measured against the pre-task commit: 133 lines added,
30 removed (net +103), all added lines <= 100 columns.

#### 8.7.2 Order inside `_prepare_and_maybe_dispatch` with the cache on

```
not_taken? -> skipped
in done?   -> skipped
should_skip & integration_allows_skip -> status=skipped                (UNCHANGED)
ts.dispatch_cycle += 1 ; provenance                                     (UNCHANGED)
isolation resolve / activate / ensure / sync_checkout                   (UNCHANGED)
_apply_join -> not_taken                                                (UNCHANGED)
missing required inputs -> failed/halt                                  (UNCHANGED)
collect dynamic_input_paths                                             (UNCHANGED)
[approval/human gates of E-Ag7Pw3, if any, MUST be inserted here]
>>> _result_cache_lookup (NEW; guarded) -> hit: settled, done, save, "skipped"
budget gate / charge -> mark running -> DISPATCH                        (UNCHANGED)
```

#### 8.7.3 Behaviour under parallelism, resume, budget, breakers, quota and cancellation

| Concern | Behaviour |
|---------|-----------|
| `max_parallel > 1` | Lookups and stores run inside the serialized main-thread prepare and settle. A hit returns `skipped`, so it **takes no worker slot**. A hit's dependents become ready on the next `_ready_ids`. Restore I/O briefly blocks the main thread (bounded); workers keep running. A sibling that commits concurrently trips guard 2, and one that edits tracked files trips guard 3, so a task running alongside it is not stored. That is safe and documented (test I-19). |
| Barriers | Every barrier task is ineligible. Barrier logic is unchanged. |
| Isolation | Ineligible (resolved mode or integration active). The isolation path runs **before** the lookup and is unmodified. Test I-12. |
| Budget | A hit never calls `gate`, `charge_estimate` or `reconcile`. A **stale** charge left by a crash in the previous dispatch cycle (`dispatch_cycle - 1`) is reversed on hit through the shared `_reverse_stale_charge`, which emits the existing `budget.resume_reverse` event (reviewer R3). Note that budget `blocked` re-gating repeats the lookup on each pass; that is documented, and memoization is non-MVP 10. Test I-6, I-6b. |
| Breakers | A hit never reaches `evaluate_breakers`, mirroring `should_skip`. `cumulative_*` stay 0 for a first-pass hit, so `task_cost_usd` and `run_cost_usd` never see saved amounts. A `verdict` breaker's source task is ineligible. Test I-7. |
| Quota | A hit does not reset `ctx.quota_exhausted_since`. A requeued task (quota, 429, self-heal) gets a fresh lookup on its next pass. A hit then keeps the real spend already in `cumulative_*` and counts as a current hit. Test I-20. |
| Resume | A hit stays `succeeded` with its outputs present. `prepare_resume` keeps it, and the initial `done` set includes it, so it is never looked up again. If its outputs were deleted, it is reset to pending and then either re-hit (cache on) or re-dispatched (cache off, so the record goes stale). Tests I-8, I-9. |
| `join: any`, `not_taken` | Join resolution happens **before** the lookup, so a `not_taken` task never hits. Test I-21. |
| Missing inputs | The existing failure happens **before** the lookup. Test I-22. |
| Cancellation | Unchanged (checked at the top of the loop). Restore is bounded. |
| `_drain_remaining` | Drained successes go through the same store seam. |
| Exit codes | A hit is a success. Exit codes are unchanged. |
| `ui/activity` | A hit task has `dispatch_cycle = N` but no capture directory, so `locate_attempt_dirs` returns `[]`. It shows "no activity" rather than an older attempt (critic #8a). |

#### 8.7.4 Hit path compared with `should_skip` (and the success choke point)

**Same as `should_skip`:**
- The `TaskRunState` is mutated in place.
- Then `ctx.done.add(tid)` → `save` → `"skipped"`.

**Different:**
- The status is `succeeded`, so `prepare_resume` keeps the task.
- A record is written.
- Timestamps are set.
- A `task.end` event is logged with `cached: true`.
- A stale budget charge is reversed.

**Success side effects that live only in `_settle_completed_task`** (critic #8b). Hits do not run
these:
- `ctx.quota_exhausted_since = None`
- the router hook (routers are ineligible)
- emit and loop handling (ineligible)
- breaker evaluation
- `start_heads` / `end_heads` recording. A hit has no commit window, so empty heads are correct for
  `report-survival`.

**Rule for future success-side effects** (recorded in the merge notes): any new success-side effect
added to `_settle_completed_task` must state whether it applies to result-cache hits. The comment at
seam (c) points here.

#### 8.7.5 No-op when the mode is off (NFR-1): the exact claim and the evidence

**Claim (Rev 3, literal).** When the mode is off:
- `engine.py` **executes no cache code and imports no cache module**.
- An engine process (`Orchestrator(..., result_cache=None)`) loads **no** module from
  `agent_orchestrator.cache` except `agent_orchestrator.cache` and `.constants`, which arrive via
  `project_config` if anything imports it. The CLI path may additionally load `.settings`
  (mode resolution) and, **as built (DV-16)**, `.cli`: `cli.py` registers the `ao cache` group
  eagerly (`from .cache.cli import cache_app`), so every `ao` start loads it. `cache/cli.py` is
  kept import-light (module level: `typer`, `constants`, `settings` only; an AST test pins this),
  so the CLI-path allow-list is `{cache, cache.constants, cache.settings, cache.cli}`; the engine
  process keeps `{cache, cache.constants}`.
- `runstate.write_status`, `usage`, `outcomes`, `cli` and `ui/runs` import `cache.report` inside
  the function and **only when `state.result_cache` is non-empty**, so a run the cache never
  touched never loads it.
- `status.json` and all CLI text output are **byte-identical** to before this epic.
- `state.json` gains `"result_cache": {}`, following the `spawned_by` precedent.
- `report-usage --json` is byte-identical, because the `result_cache` object is omitted.
- The dashboard JSON gains `null` keys (`tasks[].result_cache`, `result_cache`), following the
  `integration: null` precedent, and renders identically.

**Structural argument.**
1. `_build_result_cache` returns None for mode `off` and constructs nothing.
2. Call sites (c) and (d) are guarded by `self._result_cache is not None`.
3. The only module-level imports of cache types in `engine.py` are under `TYPE_CHECKING`. The
   runtime import is lazy, inside `_result_cache_lookup`.
4. `_RunContext.result_cache_pending` stays empty.
5. Every read side checks `state.result_cache` before importing `cache.report`; with an empty map
   it adds no keys, prints no line and pops the usage object.

**Evidence.** T-XpF1pF's acceptance criteria include I-1, I-2, U-AST-E and the full suite. T-JCOAsq
Part 1 authors I-1 and I-2 against base code only, before T-XpF1pF starts.

- **I-1, poisoned imports.** In a **subprocess** (so earlier imports in the test session cannot
  contaminate it), install a `sys.meta_path` finder that raises `ImportError` for every
  `agent_orchestrator.cache.*` module except `agent_orchestrator.cache` and `.constants`, patch
  `Orchestrator._result_cache_lookup` and `Orchestrator._result_cache_store` to raise (with
  `raising=False`, so the test is valid before T-XpF1pF lands), and run representative workflows
  with `result_cache=None`: serial; `max_parallel=3`; emit; loop; router with `join: any`; budget
  wait; breakers; hooks; isolation on a real repository. Expected: they all complete; no
  `.orchestrator/cache` directory exists; `status.json` has no `result_cache` key at any level;
  `state.result_cache == {}`; and the loaded `agent_orchestrator.cache*` modules are a subset of
  `{agent_orchestrator.cache, agent_orchestrator.cache.constants}`. The finder needs no cache
  code to exist, so Part 1 depends on nothing.
- **I-2, byte-identical snapshot.** For the golden fixture workflow, with a fixed clock, a fixed
  run id and the cache off, `status.json` and `ao run` stdout equal the golden files captured at
  base `bb6d8a0`, in two variants: serial and `max_parallel=3`. Normalize the absolute workspace
  path to `<WS>`, because `output_artifact_path` is absolute. **Merge checklist:** after the
  sibling epics merge, the parent recaptures these goldens at the merge base if a sibling changed
  `status.json` or `ao run` output on purpose (§24.2).
- **U-AST-E (static).** Parse `engine.py` and assert that every `ImportFrom` with module
  `.cache...` sits inside either an `if TYPE_CHECKING:` block or the body of
  `_result_cache_lookup`.
- **U-LZ (lazy read sides).** With an empty `result_cache`, calling `write_status`,
  `aggregate_usage`, `_settle_reason`, `_print_state` and the dashboard run detail does not add
  `agent_orchestrator.cache.report` to `sys.modules` (subprocess check).
- **Full suite.** As built: 6903 passed / 10 skipped / 0 failed at G2 (the design-time baseline was
  5041 passed / 8 skipped / 2 known bench failures, which no longer occur). The
  no-op proof makes **no** edit to `tests/conftest.py`, because the NFR-2 regression gate
  (`tests/test_nfr2_regression_gate.py::TestPreEpicTestsUnedited`) forbids it (developer #2). Each
  new cache test module controls `AO_CACHE` itself, with `monkeypatch.delenv` / `setenv`.

### 8.8 Module M8 — observability and reporting (`cache/report.py`, `runstate.py`, `usage.py`, `outcomes.py`, `cli.py`)

#### 8.8.1 `report.py` helpers

These are pure, O(tasks), and depend only on `models` and `constants`. Callers import this module
lazily, and only when `state.result_cache` is non-empty (D9).

```python
def current_records(state: RunState) -> dict[str, ResultCacheRecord]:
    """Records for which is_current_result_cache_record(rec, state.tasks.get(tid)) holds."""


def current_hit(state: RunState, tid: str) -> bool:
    """A current record with outcome == hit."""


def task_view(rec: ResultCacheRecord) -> dict[str, object]:
    """The per-task object (§13.5): exactly hit, key, saved_cost_usd, saved_tokens,
    saved_seconds, plus the additive fields."""


def run_block(state: RunState) -> dict[str, object] | None:
    """The run-level object (§13.5); None when there are no current records."""


def result_cache_status_fields(
    state: RunState,
) -> tuple[dict[str, dict[str, object]], dict[str, object] | None]:
    """(per-task views by task id, run block); ({}, None) when nothing is current."""


def format_summary_line(block: dict[str, object] | None) -> str | None:
    """The §8.8.3 line; None for None."""


def usage_counters(state: RunState) -> dict[str, object] | None:
    """One run's contribution to the cross-run `result_cache` usage object (§13.6)."""
```

#### 8.8.2 `status.json` additions (`runstate.write_status`, as built +12/-1 lines)

```python
        rc_tasks: dict[str, dict[str, object]] = {}
        rc_run: dict[str, object] | None = None
        if state.result_cache:  # lazy: a run the cache never touched never imports cache.report
            from .cache.report import result_cache_status_fields

            rc_tasks, rc_run = result_cache_status_fields(state)
        # per-task dict: **({"result_cache": rc_tasks[tid]} if tid in rc_tasks else {}),
        # after building `snapshot`:
        if rc_run is not None:
            snapshot["result_cache"] = rc_run
```

The per-task object for a hit. The brief's fields come first; everything after `saved_seconds` is
additive (D35):

```json
{"hit": true, "key": "6646469e94a695fe1a994d35f54ca74e007262911255552b03c54ce2e5d0319f",
 "saved_cost_usd": 0.4123, "saved_tokens": 15400, "saved_seconds": 95.2,
 "outcome": "hit", "mode": "on", "mode_source": "env", "reason": null, "reason_detail": null,
 "stored": false, "store_reason": null, "saved_input_tokens": 12000,
 "saved_output_tokens": 3400, "source_run_id": "doc-pipeline-20261005T101450Z"}
```

Other outcomes:
- shadow: `{"hit": false, ..., "outcome": "would_hit", "mode": "shadow", "stored": true, ...}`
- ineligible: `{"hit": false, "key": null, ..., "outcome": "ineligible",
  "reason": "unknown_task_field", "reason_detail": "approval", ...}`
- not storable after a probe failure: `{..., "outcome": "miss", "reason": "not_found",
  "stored": false, "store_reason": "repo_worktree_probe_failed", ...}`

The run block is present only if at least one current record exists:

```json
{"result_cache": {"hits": 2, "saved_cost_usd": 1.2345, "saved_tokens": 54000,
                  "saved_seconds": 312.4, "would_hits": 0, "misses": 1, "ineligible": 1,
                  "stored": 1, "lookups": 3, "saved_input_tokens": 45000,
                  "saved_output_tokens": 9000, "avoidable_cost_usd": 0.0}}
```

`saved_*` sums current **hits**. `avoidable_cost_usd` sums current **would_hits**. `lookups`
counts eligible lookups (hits + would_hits + misses). `usage_totals` (E-9h3m7k) is **unchanged**
and never double-counts.

#### 8.8.3 Run-summary line (`_print_state`, `_print_status_snapshot`)

The line is printed after `Total cost:`, and only when the run block exists. The same formatter is
used for both callers; both import `cache.report` lazily, only when `state.result_cache` is
non-empty. The token figure is `saved_tokens`.

```text
Result cache: hits=2 (saved ~$1.2345 est., ~54000 tokens, ~312s) would_hits=0 misses=1 stored=1 ineligible=1
```

#### 8.8.4 `ao report-usage` and `ao report-outcomes`

- **`usage.aggregate_usage`: two dispatch-counting sites.** Both test `dispatch_cycle >= 1` today
  and both must exclude current hits (D12, R-D6). Each calls `current_hit` through a lazy import,
  only when `state.result_cache` is non-empty.
  - **Site A, group metrics (usage.py ~409).** A current hit is **not** counted in `tasks`,
    `succeeded`, `failed` or `retried`. Its **real** carried spend still counts: when an earlier
    paid attempt crashed or failed and `prepare_resume` carried `cumulative_*` into the hit
    (hit-after-spend), its `cost_usd`, `input_tokens` and `output_tokens` are added to the group
    sums. A first-pass hit adds 0, because its `cumulative_*` are 0.
  - **Site B, producer attribution (usage.py ~472).** A producer that is a current hit is skipped,
    exactly like a never-dispatched producer: no model of this run produced its output.
- **`UsageReport.result_cache: ResultCacheUsage | None = None`**: the cross-run object of §13.6,
  summed from `report.usage_counters(state)` over the scanned runs. It exposes exactly `hits`,
  `saved_cost_usd`, `saved_tokens` and `saved_seconds`, plus the additive G0 fields `lookups`,
  `would_hits`, `misses`, `ineligible`, `avoidable_cost_usd`, `miss_reasons` and
  `store_skip_reasons` (D34). `usage_report_payload` omits the key when it is None.
- **Text output.** One line after the header, only when hits or would_hits are greater than 0:

  ```text
  Result cache: 3 hit(s) across scanned runs, ~$1.6500 avoided (est.; source-run cost incl. retries; not in costs below)
  ```

  When would_hits > 0 it also prints
  `Result cache (shadow): 5 would-hit(s) of 12 lookup(s), ~$2.1000 avoidable (est.)`.
- **`outcomes._settle_reason(ts, *, state=None, tid=None)`** returns `"cached"` for a current
  hit, so `ao report-outcomes --grade` hooks know that the agent did not run (reviewer R6). The
  new parameters are keyword-only and optional; the one caller passes `state` and `task_id`. The
  `SettleReason` Literal is widened additively.

### 8.9 Module M9 — `ao cache` CLI (`cache/cli.py`)

The sub-app is registered with `app.add_typer(cache_app, name="cache")`. Group help:

> Manage the RESULT cache (reuse of identical, previously successful task outputs across runs) —
> unrelated to Claude prompt caching.

**Options shared by every subcommand.**

- `--workspace/-w`. The workspace is resolved as `--workspace`, then `AO_WORKSPACE_ROOT`, then the
  config-discovered spec triplet (via `cli._resolve_workspace_root(ws, None, None, None)`, imported
  lazily). **As built (DV-18):** a workspace that is not an existing directory is exit 2 for every
  command; `--help` mentions only `AO_WORKSPACE_ROOT`.
- `--json`: prints exactly **one** JSON document on stdout, including on exit 1.

**Common behaviour.**

- Limits come from `.ao/config.yaml cache.*`, **discovered from the current directory** (as built;
  not from `--workspace`: run `ao cache prune` from the project, and use `--dry-run`, G2-N6).
- The wall clock is read through a module-level `_now()` so tests can patch it.
- Text output passes every entry-derived string through `safeio.strip_control_chars`.
- Every regex check uses `fullmatch`.
- The commands work even when the run mode is off.

| Command | Options | Text output | `--json` schema id | Exit codes |
|---------|---------|-------------|--------------------|------------|
| `ls` | `--limit N` (default `DEFAULT_LS_LIMIT`), `--sort` (a `str` validated against `LS_SORT_KEYS`, default `lru` = most recent first) | key[:12], last used, created, outputs, bytes, source task/run, cost | `ao.result-cache.ls/v1` | 0. An empty cache also exits 0 and prints "(result cache is empty: <root>)". 2 for a bad `--sort`. |
| `stats` | — | counts, bytes, limits, oldest/newest, expired, anomalies, foreign versions | `ao.result-cache.stats/v1` | 0 |
| `show <key-or-prefix>` | — | pretty-printed entry, blob presence, `components` | `ao.result-cache.show/v1` | 0 found; 1 not found or ambiguous (candidates listed); 2 malformed (`KEY_PREFIX_RE.fullmatch` fails) |
| `rm <key-or-prefix>` | — | removed key | `ao.result-cache.rm/v1` | 0 removed; 1 not found or ambiguous (candidates listed); 2 malformed prefix |
| `prune` | `--max-bytes N`, `--older-than DAYS` (overrides `ttl_days`; `0` = every entry expired, like `ao prune --older-than 0`; negative → exit 2), `--dry-run` | removed counts by reason; bytes before and after | `ao.result-cache.prune/v1` | 0 |
| `clear` | `--yes` | removed counts | `ao.result-cache.clear/v1` | 0; 1 when the removal could not be verified, or when it refused (no `--yes` and stdin is not a TTY: "refusing to clear without --yes") |
| `verify` | — (read-only; `--repair` is deferred, non-MVP 16) | problems by kind | `ao.result-cache.verify/v1` | 0 clean (orphan blobs and foreign versions alone count as clean); 1 corruption found |

**As built (T-6tRKml, DV-18).**

- `prune --json` adds `removed_restore_tmp`, `kept_restore_backups` and `restore_sweep_truncated`.
  The sweep (`ao cache prune` only) deletes regular, user-owned files named exactly like a restore
  staging file (`.ao-result-cache-*.tmp`, `.tmp.bak`) that are direct children of the parent
  directory of an output of a **valid entry still in the store**, with ctime older than
  `TMP_SWEEP_GRACE_SECONDS`; never through a symlink, never a directory, never outside the
  workspace, never a protected path; a `.bak` with link count 1 (the only name of replaced content)
  is kept and counted. `--dry-run` deletes nothing. After `ao cache clear` a leftover is not swept
  (R-A6).
- `verify` problem `kind` may also be `unreadable`; `--older-than 0` does not expire an entry whose
  `created_at` is in the future; `rm` removes the entry file only (its blobs go with the next
  `prune`, after the 1 h grace).
- On failure `--json` still prints exactly one document (`schema`, the schema's required fields
  where known, and `error`); Click usage errors raised before the command body are plain stderr
  text (exit 2).

**Deferred in Rev 3.** `rm --run R --task T` (non-MVP 15) and `verify --repair` (non-MVP 16) are
not implemented. Operators copy a key from `status.json` (`tasks[].result_cache.key`), the
`cache.hit` line in `run.log`, or `ao cache ls`.

**G0 fields.** The G0 protocol (D34) reads `ao cache stats --json` for store growth: `entries`,
`bytes.total`, `expired_entries`, `oldest_created_at` and `newest_created_at`. The would-hit rate
itself comes from `ao report-usage --json` (`result_cache.would_hits` / `result_cache.lookups`),
because the store keeps no per-lookup history.

**Prefix resolution (`show`, `rm`).**

1. `KEY_PREFIX_RE.fullmatch`.
2. List `entries/v1/<prefix[:2]>/`; this is safe because step 1 already passed.
3. Keep stems that fullmatch `SHA256_HEX_RE` and start with the prefix.
4. Expect exactly one stem.

### 8.10 Module M10 — dashboard (`ui/runs.py`, `ui/files.py`, `ui/src/types.ts`, `ui/src/components/RunDetail.tsx`)

**Backend.**

`ui/runs.py` gets two nullable fields:

```python
@dataclass(frozen=True)
class TaskStat:
    ...
    # E-Rc4Hk8 RESULT cache (not the prompt-cache fields above): task_view(rec) or None
    result_cache: dict[str, object] | None = None


@dataclass(frozen=True)
class RunDetail:
    ...
    # run_block(state); None when the run has no current records
    result_cache: dict[str, object] | None = None
```

`ui/runs.py` imports `cache.report` lazily, only when `state.result_cache` is non-empty.

`ui/files.py`: the browser refuses any resolved path equal to or under `<root>/.orchestrator/cache`
and raises `PathNotAllowedError`. As built (G2-N2) the comparison is on **casefolded** path parts
and a NUL byte in a path is also refused (HTTP 403). Blobs are opaque copies of outputs, some already deleted, and
`ao cache show` is the right tool for inspecting them (M-10, critic #8d, security NIT d).

`ui/app.py` and `ui/service.py` are **not** touched.

**Frontend.**

- `types.ts`: add two interfaces, `ResultCacheTaskView` and `ResultCacheRunBlock`, and the optional
  fields that use them.
- `RunDetail.tsx`:
  - show a `cached` tag next to the existing origin tag when `task.result_cache?.hit`. Its tooltip
    shows the source run id as **plain text**.
  - show a "Result cache" tile when `detail.result_cache` is present: `${hits} hit(s)` /
    `~${formatCost(saved)} saved (est.)`. When `would_hits > 0`, show
    `${would_hits} would-hit(s) (shadow)` instead.
- Never reuse the prompt-cache components or names (`CacheDetails`, `cache_hit_rate`).
- Rebuild the committed bundle (`npm ci && npm run build`) and commit it as a **separate
  commit**, so the parent can drop and re-run the build after merging the sibling epics. **Never
  hand-merge it.**
- Backend tests live in `tests/ui/` so they count toward the existing dashboard coverage gate
  (`--cov=agent_orchestrator.ui --cov-fail-under=80` in CI); frontend tests in `ui/src/test/`.

### 8.11 Module M11 — bench forced off (`bench/subjects.py`)

```python
_AO_NO_CACHE_FLAG = "--no-cache"  # the bench must measure real dispatch cost (ADR-0019 D23)
_AO_CACHE_OFF_VALUE = "0"  # as built: named, so the env value is not a bare literal (NFR-7)

        argv = [
            "uv",
            "run",
            "ao",
            "run",
            "--workflow",
            str(workflow_path),
            "--reposets",
            str(rendered_path),
            "--agents",
            str(agents_path),
            _AO_NO_CACHE_FLAG,
        ]
        ...
        env = dict(os.environ)
        env[ENV_CACHE] = _AO_CACHE_OFF_VALUE  # ENV_CACHE from agent_orchestrator.cache.constants
```

**Regression test.** Monkeypatch `_run_with_timeout` to capture argv and env. Assert that
`--no-cache` is in argv and that `env["AO_CACHE"] == "0"`, including when the outer env sets `AO_CACHE=1`.

### 8.12 Cross-module edge-case catalogue

| # | Case | Expected |
|---|------|----------|
| EC-1 | A malformed spec `cache` value (string, number) | `StrictBool` and the schema reject it; `ao validate` exits 1 |
| EC-2 | Cyclic dependencies | Unchanged: `CycleError` is raised before any lookup |
| EC-3 | Missing inputs | The engine failure happens before the lookup. A missing optional input under `join: any` → `input_missing` |
| EC-4 | A failed, retried or cancelled task | Never stored. A requeued task gets a fresh lookup |
| EC-5 | Duplicate node ids or outputs | Ids: unchanged spec validation. Outputs: `duplicate_output` |
| EC-6 | Store or backend failure | `store_unavailable` / `store_error`. An unexpected bug disables the cache for the run, and the run continues |
| EC-7 | Clock or timezone | UTC engine clock. Aware datetimes only. A future `created_at` stays valid |
| EC-8 | Two runs, same workspace, same key | Benign race |
| EC-9 | Workspace moved | Keys unchanged (N-3) |
| EC-10 | Task renamed | Hit (D4). Template instances do **not** share entries, because their output paths differ |
| EC-11 | Agent prompt template, global `--model` or `EFFORT_MAX_TURNS` changed | Miss (agent projection or argv) |
| EC-12 | `CLAUDE.md` / `.claude/agents/*.md` edited (even uncommitted) | Miss (fingerprint) |
| EC-13 | Outputs present, `skip_if_outputs_exist: true` | `should_skip` → `skipped`; no lookup |
| EC-14 | Outputs present with different starting content, `skip: false` | Miss (prior); the task runs and stores under a new key |
| EC-15 | The agent mutates its own input or instruction | Not stored (`key_changed_during_run`) |
| EC-16 | The agent commits | Not stored (`repo_head_moved`), whatever `include_repo_heads` is set to |
| EC-17 | The agent edits an undeclared tracked file without committing | Not stored (`repo_worktree_changed`) |
| EC-18 | A hit task's outputs are deleted, then `ao resume` | Reset to pending → re-hit (on) or re-dispatched (off, so the record goes stale) |
| EC-19 | An E-Ag7Pw3 approval-gate task | `unknown_task_field` / `unknown_workflow_field` until it is classified RULED. The gate must run before the lookup |
| EC-20 | Users delete outputs to force a redo with the cache on | They get a hit (replay). The authoring guide says to use `ao cache rm <key>` (then re-run) or `--no-cache` |
| EC-21 | A committed `.ao/config.yaml` with `cache.enabled: true` | The cache is on for every clone and service run. The stderr banner and the `mode_source` field show it; the docs warn about it |
| EC-22 | A `.git` in `$HOME` (or any ancestor of the workspace) | Ignored: repository detection stops at the workspace root (D5); a plain project dir stays non-git |
| EC-23 | The workspace sits inside a parent repository | Treated as non-git; HEAD not keyed; the banner warns (A-12, R-19) |
| EC-24 | A symlinked shard or `entries/v1` directory planted by an agent | Miss `unsafe_path`; nothing read, touched or unlinked through it; not evicted (D33) |
| EC-25 | `.DS_Store` or editor files appear inside a declared input directory | The digest changes: a false miss, never a false hit (documented) |
| EC-26 | Dirty tracked edits to undeclared files existed before the lookup | Not in the key: a hit may replay a result computed against a different dirty tree (accepted residual, R-20) |
| EC-27 | `AO_CACHE=refresh` (deferred) | Treated as unknown: off plus a warning |

---
## 9. ADR log

[ADR-0019](adr/ADR-0019-cross-run-result-cache.md) records the durable decisions:

| Topic | Decisions |
|-------|-----------|
| Double opt-in (named default), shadow mode | D1, D26 |
| Location | D2 |
| Key contents: task id excluded, repo heads, priors, argv and fingerprint | D3–D7 |
| NFR-1 carve-out | D9 |
| Allowlist eligibility | D10 |
| Lookup placement and engine-owned hit settle | D11, D12 |
| Store guards | D13 |
| Persistence and staleness | D14 |
| Provisional split ABCs and versioned layout | D17, D18 |
| Restore protocol and sensitive paths | D20, D29 |
| Hostile-data parsing | D28 |
| Error boundary | D32 |
| Naming | D22 |
| Unsafe cache paths are never followed or evicted | D33 |
| G0 is a post-merge, operator-owned measurement | D34 |
| Field contract (`hit`, `key`, `saved_*`) and the `result_cache` container | D35 |

The ADR also records the alternatives that were not chosen (ALT-1…ALT-8). Two of them stay open: the executor-level
`CachingExecutor` (ALT-7, the migration path if isolation becomes the default) and explicit `--reuse-from` (ALT-8, the
fallback if G0 is negative).
§7.6 is the complete per-decision log, including the disposition of the manager's proposals. §23.4 maps every Phase-4 finding to the change it caused, and §23.5 does the same for the Rev 3 early-gate review.

---

## 10. Block diagram

```mermaid
flowchart LR
  subgraph CLI["ao CLI (cli.py)"]
    RUN["run / resume<br/>--cache/--no-cache"] --> BRC["_build_result_cache()<br/>mode + banner"]
    CAPP["ao cache ls|stats|show|rm|prune|clear|verify"]
    SUM["_print_state / _print_status_snapshot<br/>+ Result cache line"]
  end
  BRC -- "ResultCacheHook | None" --> ORCH
  subgraph ENGINE["engine.py (Orchestrator)"]
    PREP["_prepare_and_maybe_dispatch<br/>... missing inputs, dynamic inputs<br/>[approval gates here]<br/>>> _result_cache_lookup << <br/>budget gate, mark running"]
    SETTLE["_settle_completed_task<br/>... integration settle<br/>>> _result_cache_store << (succeeded)"]
    ORCH[Orchestrator] --> PREP & SETTLE
  end
  subgraph CACHE["agent_orchestrator.cache"]
    CO[ResultCache] --> EL[eligibility] & KB[keys + fingerprint + hashing] & RSt[repo_state] & RS[restore/capture] & STO[LocalFsCacheStore]
    REC[records]
    REP[report]
    SIO[safeio]
  end
  PREP -- LookupRequest --> CO
  SETTLE -- PendingStore --> CO
  KB --> ARGV[claude_cli.build_claude_argv]
  STO --- DISK[(.orchestrator/cache/<br/>entries/v1/ blobs/ tmp/)]
  RS --- WS[(workspace outputs)]
  KB --- WS
  RSt --- GIT[(git HEADs + tracked status)]
  RSTATE["runstate.write_status"] --> REP
  USAGE["usage.aggregate_usage"] --> REP
  OUT["outcomes._settle_reason"] --> REP
  UI["ui/runs.py detail + ui/files.py deny"] --> REP
  CAPP --> STO
  BENCH["bench AoWorkflowSubject"] -- "--no-cache, AO_CACHE=0" --> RUN
```

---

## 11. Spec and data schema diagram

```mermaid
classDiagram
  class TaskSpec { +cache: StrictBool|None (author opt-in) }
  class WorkflowDefaults { +cache: StrictBool|None }
  class CacheConfig { enabled: StrictBool|None; mode: on|shadow; max_bytes; max_entry_bytes|None; ttl_days|None; include_repo_heads; max_input_bytes; max_input_files }
  class ProjectConfig { +cache: CacheConfig }
  class RunState { +result_cache: dict~str, ResultCacheRecord~ }
  class ResultCacheRecord { hit (derived); key; saved_cost_usd; saved_tokens (derived); saved_seconds; outcome; mode; mode_source; reason; reason_detail; dispatch_cycle; at; ended_at; stored; store_reason; saved_cost_usd; saved_input_tokens; saved_output_tokens; saved_seconds; source_run_id }
  class CacheEntry { schema; key; key_schema; created_at: AwareDatetime; source; usage; outputs[]; key_summary }
  class OutputRecord { path; kind="file"; sha256; size; mode<=0o777 }
  class EntryUsage { cost_usd; input_tokens; output_tokens; cache_creation_input_tokens; cache_read_input_tokens; duration_seconds; attempts; model; effort; actuals_available }
  class EntrySource { workflow_id; task_id; run_id; agent; ao_version; cli_version }
  class KeySummary { key_schema; executor; model; effort; max_turns; instruction; inputs; dynamic_inputs; general_instructions; outputs; digests; repo_heads; components }
  ProjectConfig --> CacheConfig
  RunState --> ResultCacheRecord
  CacheEntry --> OutputRecord
  CacheEntry --> EntryUsage
  CacheEntry --> EntrySource
  CacheEntry --> KeySummary
  ResultCacheRecord ..> CacheEntry : saved_* copied from usage (hit / would_hit)
```

---

## 12. Sequence diagrams

### 12.1 Miss → dispatch → store (run 1, mode on)

```mermaid
sequenceDiagram
  participant E as Engine (main thread)
  participant C as ResultCache
  participant G as repo_state
  participant K as keys/fingerprint
  participant S as LocalFsCacheStore
  participant W as Worker
  E->>E: should_skip? no · ++dispatch_cycle · isolation=none · join ok · inputs exist
  E->>C: lookup(LookupRequest)
  C->>C: policy(task) = opted in · check_eligibility -> eligible · store.check()
  C->>G: heads.read(repo_paths)
  C->>K: build_cache_key (argv, fingerprint, digests, priors, heads)
  C->>S: get_entry(k) -> None
  C->>G: worktree.snapshot(exclude=outputs)   [lazy: storable outcome only]
  C-->>E: miss(not_found, components) + PendingStore
  E->>E: state.result_cache[tid]=record · pending[tid]=PendingStore
  E->>E: budget gate/charge · mark running
  E->>W: dispatch
  W-->>E: WorkerOutcome(succeeded)
  E->>E: settle: reconcile · cumulative_* += actuals · outputs present -> succeeded
  E->>C: store_success(pending, ts, now)
  C->>G: heads.read == lookup heads? (guard 2)
  C->>K: build_cache_key(preseed) == k? (guard 1)
  C->>G: worktree.snapshot == lookup snapshot? (guard 3)
  C->>S: put_blob x N · put_entry(k) · maybe_enforce_limits (bounded)
  C-->>E: StoreResult(stored)
  E->>E: record.stored=True · save · breakers (unchanged)
```

### 12.2 Hit → restore (run 2, outputs absent, mode on)

```mermaid
sequenceDiagram
  participant E as Engine (main thread)
  participant C as ResultCache
  participant S as LocalFsCacheStore
  participant R as restore_outputs
  E->>E: should_skip? no · ++dispatch_cycle (kept) · join/inputs ok
  E->>C: lookup(LookupRequest)
  C->>C: opted in · eligible · heads · key k (same as run 1) · NO worktree snapshot
  C->>S: get_entry(k) -> CacheEntry (total parse, key==k, not expired)
  C->>R: restore_outputs(entry, spec-derived {rel: abs})
  R->>R: manifest set == spec outputs? · not sensitive? · realpath(dest)==dest?
  R->>S: read_blob(sha) -> temp next to dest (bounded, hashed)
  R->>R: ALL verified -> chmod(mode & 0o755) + os.replace each
  R-->>C: ok
  C->>S: touch_entry(k, now)
  C-->>E: hit(record: saved_* from entry.usage)
  E->>E: status=succeeded · outputs_present · started/ended_at · record.ended_at
  E->>E: _reverse_stale_charge (budget.resume_reverse if any) · task.end(cached) · done.add · save
  E-->>E: DispatchPrep("skipped")  (no budget gate, no worker, no breakers)
```

### 12.3 Shadow mode (measure only)

```mermaid
sequenceDiagram
  participant E as Engine
  participant C as ResultCache
  participant S as Store
  E->>C: lookup
  C->>S: get_entry(k) -> valid entry
  C->>S: has_blob(sha) for each output
  C->>C: worktree.snapshot (lazy; failure -> no pending, store_reason)
  C-->>E: would_hit (record saved_* = avoidable estimate) + PendingStore
  E->>E: dispatch normally (agent runs) -> settle -> store_success (refreshes entry)
```

### 12.4 Failure — corrupt blob on restore

```mermaid
sequenceDiagram
  participant E as Engine
  participant C as ResultCache
  participant S as Store
  participant R as restore_outputs
  E->>C: lookup
  C->>S: get_entry(k) -> entry
  C->>R: restore_outputs
  R->>S: read_blob(sha) -> bytes hash != sha
  R-->>C: RestoreMiss(blob_corrupt, evict, blob=sha)   [no destination touched]
  C->>S: delete_entry(k) · delete_blob(sha)
  C-->>E: miss(blob_corrupt), NO pending   [cache.corrupt WARNING, cache.evict]
  E->>E: normal dispatch -> settle -> no store this dispatch (next run re-populates)
```

### 12.5 Failure — tampered manifest (`../escape`) and hostile entry

- **Tampered manifest.** `entry.outputs[0].path == "../escape"`, so
  `set(manifest) != set(spec outputs)`. Result: `RestoreMiss(manifest_mismatch, evict=True)`. **No
  filesystem write happens at any path named by the entry.** The task dispatches for real. Test
  ADV-1.
- **Hostile entry.** Covers nested `[[[[…`, a naive `created_at`, `cost_usd: 1e999`, a 1 MiB
  `run_id`, and a FIFO at the entry path. `parse_entry_bytes` and `safeio` raise
  `CacheIntegrityError`. Result: a miss plus eviction. The run is never killed, and `state.json`
  still loads. Test ADV-9 (hostile corpus).
- **Planted symlinked shard directory.** `entries/v1/66 -> /victim/dir`. `_checks` raises
  `CacheUnsafePathError` before any read, `utime` or `unlink`. Result: a miss with reason
  `unsafe_path`, not storable, **not evicted**, so nothing under `/victim/dir` is touched
  (D33). Test ADV-4b.

### 12.6 Resume after a hit

```mermaid
sequenceDiagram
  participant CLI as ao resume
  participant RS as RunStateStore.prepare_resume
  participant E as Engine
  CLI->>RS: load + prepare_resume
  RS->>RS: A (hit): succeeded + outputs present -> kept
  RS->>RS: B (failed): reset to pending (cumulative_* carried, dispatch_cycle carried)
  CLI->>E: run(run_state=existing, result_cache=None or ResultCache)
  E->>E: done = {A, ...} -> A never looked up; record A current (cycle & ended_at match)
  E->>E: B: lookup (cache on) or plain dispatch (cache off)
```

### 12.7 Concurrent same-key stores (two processes)

```mermaid
sequenceDiagram
  participant P1 as Run 1
  participant P2 as Run 2
  participant S as Store (same dir)
  P1->>S: put_blob X1 (tmp1 -> blobs/x1)
  P2->>S: put_blob X2 (or dedupe + touch if identical)
  P1->>S: put_entry k (tmpA -> entries/v1/kk/k.json)
  P2->>S: put_entry k (tmpB -> same path)  last os.replace wins
  Note over S: entry references X2 (present). X1 unreferenced -> swept by a later prune after grace.
```

---

## 13. Spec and data schemas (JSON Schema 2020-12)

### 13.1 Workflow spec additions

Two new `boolean` properties: `defaults.cache` and `$defs.task.cache` (§8.1.2). Both are optional
and absence means unset. The pydantic type is `StrictBool | None`.

### 13.2 Project config (`.ao/config.yaml` → `cache`)

```json
{"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "ao.project-config.cache/v1",
 "type": "object", "additionalProperties": false,
 "properties": {
   "enabled": {"type": ["boolean", "null"]},
   "mode": {"enum": ["on", "shadow"], "default": "on"},
   "max_bytes": {"type": "integer", "minimum": 1, "maximum": 1125899906842624, "default": 1073741824},
   "max_entry_bytes": {"type": ["integer", "null"], "minimum": 1, "maximum": 1125899906842624},
   "ttl_days": {"type": ["integer", "null"], "minimum": 1, "maximum": 36500, "default": 30},
   "include_repo_heads": {"type": "boolean", "default": true},
   "max_input_bytes": {"type": "integer", "minimum": 1, "maximum": 1125899906842624, "default": 536870912},
   "max_input_files": {"type": "integer", "minimum": 1, "maximum": 10000000, "default": 20000}}}
```

`CacheConfig`, a pydantic model, is the authoritative gate; this schema documents it. Test U-C3
asserts that pydantic rejects every invalid example of this schema.

### 13.3 Cache entry (`entries/v1/<k[:2]>/<k>.json`)

```json
{"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "ao.result-cache.entry/v1",
 "type": "object",
 "required": ["schema", "key", "key_schema", "created_at", "source", "usage", "outputs", "key_summary"],
 "properties": {
   "schema": {"const": "ao.result-cache.entry/v1"},
   "key": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
   "key_schema": {"type": "integer", "minimum": 1, "maximum": 1000},
   "created_at": {"type": "string", "format": "date-time", "description": "timezone-aware"},
   "source": {"type": "object", "required": ["workflow_id", "task_id", "run_id", "agent", "ao_version"],
              "properties": {"workflow_id": {"type": "string", "maxLength": 256}, "task_id": {"type": "string", "maxLength": 256},
                             "run_id": {"type": "string", "maxLength": 256}, "agent": {"type": "string", "maxLength": 256},
                             "ao_version": {"type": "string", "maxLength": 256}, "cli_version": {"type": ["string", "null"], "maxLength": 256}}},
   "usage": {"type": "object", "properties": {
       "cost_usd": {"type": "number", "minimum": 0, "maximum": 1000000},
       "input_tokens": {"type": "integer", "minimum": 0, "maximum": 1000000000000},
       "output_tokens": {"type": "integer", "minimum": 0, "maximum": 1000000000000},
       "cache_creation_input_tokens": {"type": "integer", "minimum": 0, "maximum": 1000000000000},
       "cache_read_input_tokens": {"type": "integer", "minimum": 0, "maximum": 1000000000000},
       "duration_seconds": {"type": "number", "minimum": 0, "maximum": 100000000},
       "attempts": {"type": "integer", "minimum": 0, "maximum": 1000000},
       "model": {"type": ["string", "null"], "maxLength": 256}, "effort": {"type": ["string", "null"], "maxLength": 256},
       "actuals_available": {"type": "boolean"}}},
   "outputs": {"type": "array", "minItems": 1, "maxItems": 4096, "items": {"type": "object",
       "required": ["path", "sha256", "size", "mode"],
       "properties": {"path": {"type": "string", "maxLength": 4096}, "kind": {"const": "file"},
                      "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                      "size": {"type": "integer", "minimum": 0}, "mode": {"type": "integer", "minimum": 0, "maximum": 511}}}},
   "key_summary": {"type": "object"}}}
```

**Parsing.** Entries are stored as canonical JSON bytes (sorted keys, compact, ASCII). A v1 reader
accepts additive fields via `extra="ignore"`. Other major versions live in `entries/vN/` and are
never read or modified by a v1 reader.

### 13.4 `ao cache --json` outputs

```json
{"$id": "ao.result-cache.ls/v1", "type": "object", "required": ["schema", "root", "entries"],
 "properties": {"schema": {"const": "ao.result-cache.ls/v1"}, "root": {"type": "string"},
   "entries": {"type": "array", "items": {"type": "object", "required": ["key", "status"],
     "properties": {"key": {"type": "string"}, "status": {"enum": ["ok", "invalid", "expired"]},
       "created_at": {"type": ["string", "null"]}, "last_used": {"type": "string"},
       "outputs": {"type": "integer"}, "bytes": {"type": "integer"},
       "source": {"type": ["object", "null"]}, "cost_usd": {"type": ["number", "null"]},
       "model": {"type": ["string", "null"]}, "error": {"type": ["string", "null"]}}}}}}

{"$id": "ao.result-cache.stats/v1", "required": ["schema", "root", "exists"],
 "properties": {"schema": {"const": "ao.result-cache.stats/v1"}, "root": {"type": "string"},
   "exists": {"type": "boolean"}, "entries": {"type": "integer"}, "invalid_entries": {"type": "integer"},
   "expired_entries": {"type": "integer"}, "foreign_version_dirs": {"type": "array", "items": {"type": "string"}},
   "blobs": {"type": "integer"}, "orphan_blobs": {"type": "integer"}, "tmp_files": {"type": "integer"},
   "trash_dirs": {"type": "integer"}, "anomalies": {"type": "integer"},
   "bytes": {"type": "object", "properties": {"entries": {"type": "integer"}, "blobs": {"type": "integer"},
       "referenced_blobs": {"type": "integer"}, "orphan_blobs": {"type": "integer"}, "total": {"type": "integer"}}},
   "limits": {"type": "object", "properties": {"max_bytes": {"type": "integer"},
       "max_entry_bytes": {"type": "integer"}, "ttl_days": {"type": ["integer", "null"]}}},
   "oldest_created_at": {"type": ["string", "null"]}, "newest_created_at": {"type": ["string", "null"]}}}

{"$id": "ao.result-cache.show/v1", "required": ["schema", "entry", "blobs"],
 "properties": {"schema": {"const": "ao.result-cache.show/v1"}, "entry": {"$ref": "ao.result-cache.entry/v1"},
   "last_used": {"type": "string"}, "expired": {"type": "boolean"},
   "blobs": {"type": "array", "items": {"type": "object", "properties": {"sha256": {"type": "string"},
       "present": {"type": "boolean"}, "size_on_disk": {"type": ["integer", "null"]}}}}}}

{"$id": "ao.result-cache.rm/v1", "required": ["schema", "removed"],
 "properties": {"schema": {"const": "ao.result-cache.rm/v1"}, "removed": {"type": "array", "items": {"type": "string"}},
   "error": {"type": ["string", "null"]}}}

{"$id": "ao.result-cache.prune/v1", "required": ["schema", "dry_run", "removed_entries", "removed_blobs", "bytes_before", "bytes_after"],
 "properties": {"schema": {"const": "ao.result-cache.prune/v1"}, "dry_run": {"type": "boolean"},
   "removed_entries": {"type": "object", "properties": {"expired": {"type": "integer"}, "lru": {"type": "integer"},
       "invalid": {"type": "integer"}}},
   "removed_blobs": {"type": "integer"}, "removed_tmp": {"type": "integer"}, "removed_trash": {"type": "integer"},
   "bytes_before": {"type": "integer"}, "bytes_after": {"type": "integer"}}}

{"$id": "ao.result-cache.clear/v1", "required": ["schema", "removed_entries", "removed_blobs", "bytes_freed"],
 "properties": {"schema": {"const": "ao.result-cache.clear/v1"}, "removed_entries": {"type": "integer"},
   "removed_blobs": {"type": "integer"}, "bytes_freed": {"type": "integer"}, "error": {"type": ["string", "null"]}}}

{"$id": "ao.result-cache.verify/v1", "required": ["schema", "ok", "problems"],
 "properties": {"schema": {"const": "ao.result-cache.verify/v1"}, "ok": {"type": "boolean"},
   "entries_checked": {"type": "integer"}, "blobs_checked": {"type": "integer"},
   "problems": {"type": "array", "items": {"type": "object", "required": ["kind"],
     "properties": {"kind": {"enum": ["corrupt_entry", "key_mismatch", "missing_blob", "corrupt_blob",
         "orphan_blob", "foreign_version", "symlink", "unexpected_file"]},
       "key": {"type": ["string", "null"]}, "blob": {"type": ["string", "null"]}, "detail": {"type": ["string", "null"]}}}}}}
```

T-6tRKml stores these schemas as test fixtures under `tests/fixtures/result_cache/schemas/`.

### 13.5 `status.json` and `RunState` record additions

The brief's exact fields (`hit`, `key`, `saved_cost_usd`, `saved_tokens`, `saved_seconds`) are
required; everything else is additive (D35).

```json
{"$defs": {
  "resultCacheTask": {"type": "object",
    "required": ["hit", "key", "saved_cost_usd", "saved_tokens", "saved_seconds", "outcome",
                 "mode", "stored"],
    "properties": {"hit": {"type": "boolean"},
      "key": {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"},
      "saved_cost_usd": {"type": "number"}, "saved_tokens": {"type": "integer"},
      "saved_seconds": {"type": "number"},
      "outcome": {"type": "string"}, "mode": {"type": "string"}, "mode_source": {"type": "string"},
      "reason": {"type": ["string", "null"]}, "reason_detail": {"type": ["string", "null"]},
      "stored": {"type": "boolean"}, "store_reason": {"type": ["string", "null"]},
      "saved_input_tokens": {"type": "integer"}, "saved_output_tokens": {"type": "integer"},
      "source_run_id": {"type": ["string", "null"]}}},
  "resultCacheRun": {"type": "object",
    "required": ["hits", "saved_cost_usd", "saved_tokens", "saved_seconds", "would_hits",
                 "misses", "ineligible", "stored", "lookups"],
    "properties": {"hits": {"type": "integer"}, "saved_cost_usd": {"type": "number"},
      "saved_tokens": {"type": "integer"}, "saved_seconds": {"type": "number"},
      "would_hits": {"type": "integer"}, "misses": {"type": "integer"},
      "ineligible": {"type": "integer"}, "stored": {"type": "integer"},
      "lookups": {"type": "integer"}, "saved_input_tokens": {"type": "integer"},
      "saved_output_tokens": {"type": "integer"}, "avoidable_cost_usd": {"type": "number"}}}},
 "comment": "tasks[].result_cache (resultCacheTask) and top-level result_cache (resultCacheRun) are OMITTED when there is no current record"}
```

`ResultCacheRecord` in `state.json` carries the same per-task fields; `hit` and `saved_tokens` are
derived by its validator (§8.1.1).

### 13.6 `ao report-usage --json` → `result_cache` (cross-run; the G0 measurement object)

```json
{"$id": "ao.result-cache.usage/v1", "type": "object",
 "required": ["hits", "saved_cost_usd", "saved_tokens", "saved_seconds", "lookups",
              "would_hits", "misses", "ineligible", "avoidable_cost_usd"],
 "properties": {"hits": {"type": "integer"}, "saved_cost_usd": {"type": "number"},
   "saved_tokens": {"type": "integer"}, "saved_seconds": {"type": "number"},
   "lookups": {"type": "integer"}, "would_hits": {"type": "integer"},
   "misses": {"type": "integer"}, "ineligible": {"type": "integer"},
   "avoidable_cost_usd": {"type": "number"},
   "miss_reasons": {"type": "object", "additionalProperties": {"type": "integer"}},
   "store_skip_reasons": {"type": "object", "additionalProperties": {"type": "integer"}}},
 "comment": "Omitted from the report-usage payload when no scanned run has result-cache records. Would-hit rate for G0 = would_hits / lookups over shadow-mode runs."}
```

---

## 14. Interface and API contracts

### 14.1 Python

The full signatures are in §8.4.3 (`CacheStore`, `CacheAdmin`), §8.6.1 (`ResultCacheHook` and the
contracts), §8.2 (keys, fingerprint, `repo_state`, `safeio`), §8.5 (restore/capture) and §8.8.1
(report).

The error types live in `types.py`, except `safeio` errors, which live in `safeio.py`.

```python
class CacheError(Exception):  # never escapes ResultCache to the engine (D32)
    reason: str


class CacheIntegrityError(CacheError): ...  # corrupt_entry | key_mismatch | blob_corrupt


class CacheUnsafePathError(CacheError): ...  # unsafe_path: never followed, never evicted (D33)


class CacheBlobMissingError(CacheError): ...  # blob_missing


class CacheTooLargeError(CacheError): ...  # entry_too_large


class CacheLayoutError(CacheError): ...  # store_unavailable


class UncacheableError(Exception):  # eligibility / key build
    reason: str
    detail: str


class StoreSkip(Exception):  # capture-time skip
    reason: str
    detail: str


class RestoreMiss(Exception):  # never storable
    reason: str
    evict: bool
    blob: str | None
    detail: str


# safeio.py: SafeIOError, and its subclasses NotRegularFileError, TooLargeError, UnsafePathError
```

**Engine contract.** `Orchestrator(..., result_cache: ResultCacheHook | None = None)`. Neither
`ResultCacheHook.lookup` nor `store_success` raises to the engine. In strict mode (tests only),
unexpected exceptions propagate.

**Idempotency.**
- A lookup on an unchanged workspace restores the same bytes. A hit is read-only on the store
  apart from the LRU touch.
- Storing the same key again overwrites the entry with an equivalent one.
- The engine's record writes overwrite, so repeating one produces the same state.
- `rm` of a missing entry returns exit 1, with no side effects.

**Versioning.**

| Item | Rule |
|------|------|
| `KEY_SCHEMA_VERSION` | see §8.2.7 |
| Entry major version | lives in the path (`entries/v1`) |
| `ENTRY_SCHEMA` | must match inside v1 |
| `LAYOUT_SCHEMA` | an unknown value means `store_unavailable` |
| Record `outcome`, `mode`, `mode_source` | open string sets |
| `--json` schema ids | `…/v1` |

### 14.2 CLI

```
ao run    [...existing] [--cache | --no-cache]
ao resume [...existing] [--cache | --no-cache]
AO_CACHE=on|off|shadow   (1/true/yes/on, 0/false/no/off; anything else, incl. refresh: off)
ao cache ls      [-w WS] [--json] [--limit N] [--sort lru|created|size]
ao cache stats   [-w WS] [--json]
ao cache show    KEY_OR_PREFIX [-w WS] [--json]
ao cache rm      KEY_OR_PREFIX [-w WS] [--json]
ao cache prune   [-w WS] [--json] [--max-bytes N] [--older-than DAYS] [--dry-run]
ao cache clear   [-w WS] [--json] [--yes]
ao cache verify  [-w WS] [--json]
```

**Errors.**
- Errors go to stderr as `ERROR: <message>`, with the exit codes from §8.9.
- With `--json`, the command prints exactly one JSON document, which includes an `error`, `ok` or
  `problems` field on failure.

---

## 15. Trigger and event schema

**Triggers.** None are added. Cron- and event-triggered runs (the E-Sc9Rt4 service) inherit the
mode from env/config. Set `AO_CACHE` in the service environment.

**Event catalog.** Events are structured JSON lines written through the run logger, which adds
`run_id`; `task_id` comes from the task logger. The `cache.*` namespace belongs to the result cache.

| Event | Level | Phase | Fields (besides `run_id`/`task_id`) | When |
|-------|-------|-------|-------------------------------------|------|
| `cache.hit` | INFO | lookup | `key`, `outputs` ([{path, sha256}] — durable audit trail), `bytes`, `saved_cost_usd`, `source_run_id`, `source_created_at`, `source_ao_version` | outputs restored |
| `cache.would_hit` | INFO | lookup (shadow) | `key`, `saved_cost_usd`, `source_run_id` | a valid entry exists; nothing restored |
| `cache.miss` | INFO | lookup | `key`, `reason` (incl. `unsafe_path`), `components` (field → sha256[:12], D30) | eligible, not served |
| `cache.skip` | INFO (DEBUG for `not_opted_in`; WARNING for `store_unavailable`/`store_error`) | `lookup` \| `store` | `phase`, `reason`, `reason_detail?`, `key?`, `components?` | ineligible / not stored (a lazy guard-3 probe failure at lookup is logged with `phase: store`) |
| `cache.store` | INFO | store | `key`, `outputs`, `bytes` | entry written |
| `cache.evict` | INFO (WARNING for `deferred`) | lookup \| store \| prune | `key?`, `reason` (`expired`/`lru`/`corrupt_entry`/`key_mismatch`/`manifest_mismatch`/`blob_missing`/`blob_corrupt`/`invalid`/`deferred`), `entries?`, `bytes?` | entry removed / inline prune deferred |
| `cache.corrupt` | WARNING | lookup \| verify | `key`, `reason` (incl. `unsafe_path`, which is never evicted), `blob?` | integrity failure or unsafe path |
| `cache.disabled` | ERROR | lookup \| store | `error_type` (+ `exc_info`) | an unexpected error disabled the cache for the rest of the run (D32) |
| `task.end` (existing event, new field) | INFO | lookup (hit) | `status: succeeded`, `cached: true` | a hit settles the task |
| `budget.resume_reverse` (existing event) | INFO | lookup (hit) or budget gate | `task_id`, `cycle` | a stale charge of the previous cycle is released (shared `_reverse_stale_charge`) |

`ao cache` maintenance commands log to the package logger (stderr), not to `run.log`.

---

## 16. Deployment and upgrade

**Packaging.**
- No new runtime dependency.
- `specs/workflow.schema.json` is not packaged (pre-existing). `StrictBool` keeps the installed wheel
  strict.

**Upgrading (old → new).**
- Old `state.json` files load with `result_cache={}`.
- Old specs remain valid.
- No migration is needed.
- **One-time cosmetic warning:** the new spec fields change every static-spec sha256
  (`canonical_spec_json` dumps defaults), so resuming a run started before the upgrade logs
  `run.spec_changed_on_resume` once. This is expected, and the release notes document it
  (developer #15).

**Downgrading (new → old).**
- An old `ao` ignores `RunState.result_cache` (`extra="ignore"`). If it **resumes** a run, it
  re-saves `state.json` without the records (critic #5c). The durable trail survives in the
  `run.log` `cache.hit` events.
- An old `ao` rejects specs that contain `cache:` (`additionalProperties: false`), as it would any
  newer field.
- `.orchestrator/cache/` is inert to an old `ao`.

**Side-by-side flavours (stable/beta).** Entry versions sit in versioned directories. Blobs are
shared and protected across versions, and neither version overwrites or deletes the other's entries
(D18).

**Global install staleness.** Re-run `install.sh --reinstall`. On a stale install,
`ao run --cache` fails loudly ("no such option").

**Rollout.**
1. Merge with the mode off (the default). The epic closes here.
2. **G0, post-merge (§22.5, D34):** the parent or operator runs the G0 protocol
   (`docs-md/result-cache-g0-protocol.md`) with `AO_CACHE=shadow` on a real consumer workflow
   (finplan, with the operator's consent) over several days, then measures the would-hit rate
   and the avoidable spend.
3. Only if G0 is positive: document `on` for specific workflows. Authors opt in their pure tasks.
4. Never enable the cache in `ao-bench`.

**Release note.** There is no changelog file. The release-note line goes into `README.md`
("Result cache (opt-in)") and into this section:

> **New: opt-in result cache.** `ao run --cache` (or `AO_CACHE=1|shadow`) reuses an identical,
> previously successful task's declared outputs instead of re-dispatching the agent, for tasks
> whose workflow opts in with `cache: true`. Off by default; `--no-cache` is a kill switch;
> `ao cache ls|stats|show|rm|prune|clear|verify` manage it. Not Claude prompt caching.

**Disk.** Bounded by `max_bytes` plus transient tmp space. `ao prune` does **not** touch the result
cache; use `ao cache prune`.

**Multi-workspace service.** Each workspace has its own cache, so the service needs no change.

---

## 17. Developer and operator experience

**Operator.**
- **Visibility:**
  - a stderr banner (mode, source, opted-in task count, root);
  - the run-summary line;
  - `status.json` per-task outcome, reason and `reason_detail`;
  - dashboard tag and tile;
  - `ao cache stats|ls|show|verify`.
- **Diagnosis:** `status.json` `tasks[].result_cache.reason`, plus `run.log`
  `cache.miss.components`. Diff the components of two runs' miss events to see which key component
  changed.
- **Kill switch:** `--no-cache` or `AO_CACHE=0`.
- **Re-roll one result:** `ao cache rm <key>` (the key is in `status.json`
  `tasks[].result_cache.key`, the `cache.hit` line of `run.log`, or `ao cache ls`), then re-run.
  (`refresh` and `rm --run/--task` are deferred.)
- **Reset:** `ao cache clear --yes`.

**Workflow authors.** The "Result cache" section of `.claude/skills/workflow-authoring/SKILL.md`
(delivered by T-bdQZW4) and the README section "Result cache (opt-in)" follow this outline; the
residuals of §0.4 are added to it as item 11:

1. **What it is.** The result cache is a double opt-in: the operator turns it on, and the author
   opts each task in. It is not prompt caching, and a hit replays **one** earlier successful
   result. The author default is one named constant, `DEFAULT_TASK_CACHE_POLICY = False`; if the
   project ever flips it, every eligible task is cached whenever the operator enables the cache,
   unless it says `cache: false` (ADR-0019 D1).
2. **When to opt a task in** (`cache: true`, or `defaults.cache: true` plus per-task `false`):
   only when the task's **whole effect is captured by its declared outputs**. Pure document
   transforms are the typical fit.
3. **Never opt in a task that:**
   - edits undeclared files. The worktree guard refuses to store tasks that change tracked files,
     but untracked and non-git edits are not detected.
   - has external side effects (network, tickets, git push);
   - exists to produce a fresh answer (status checks, research);
   - reads ambient state that the key does not cover (`~/.claude`, the network).
4. **Eligibility.** Follow the table in §8.3.3. Opted-in tasks that cannot be cached show the
   reason in `status.json`.
5. **`skip_if_outputs_exist` interplay.**
   - With `true` (the default), the cache only helps when the outputs are absent: a clean
     checkout, a fresh clone with the same cache, or deleted outputs.
   - With `false` and outputs already present, a hit requires the outputs to start with identical
     content (D6).
6. **Deleting outputs to force a redo does NOT redo the work when the cache is on.** It restores the
   cached result. Use `ao cache rm <key>` and re-run, or `--no-cache`, instead (EC-20).
7. **Key behaviour you should expect.**
   - Committed repo changes invalidate the key, unless `include_repo_heads: false`.
   - Uncommitted edits to declared inputs, the instruction, `CLAUDE.md` or `.claude/**` also
     invalidate it.
   - Uncommitted edits to other files do **not**, whether they existed before the run or not.
     Edits made *during* the run to tracked files are caught by guard 3 (the result is not
     stored). Dirty tracked edits made *before* the lookup are an accepted residual (R-20).
   - Noise files inside a declared input directory (`.DS_Store`, `__pycache__`) cause false
     misses. Declare narrower inputs if that happens.
   - A workspace nested inside a parent repository is treated as non-git: its HEAD is not
     keyed. The banner warns; do not opt in tasks there (A-12).
8. **Pin full model ids** rather than aliases such as `sonnet`. Aliases drift. The CLI version is
   part of the key, and the TTL defaults to 30 days.
9. **Do not commit `cache.enabled: true`** unless every clone and service run should use the cache.
10. **Bench and CI.** `ao-bench` always runs with `--no-cache`. Do the same for cost measurements.
11. **Residuals to know (as built, §0.4).** Provider and endpoint environment
    (`ANTHROPIC_BASE_URL`, `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX`, the region) is not in
    the key: pass `--no-cache` or run `ao cache clear` when switching. The guard-3 probe can execute
    a `filter.<x>.clean` command from the agent-writable git config: use `--no-cache` for untrusted
    repositories. The cache directory is agent-writable (same-uid forgery is not defended), and
    cached outputs persist until `ao cache rm|clear|prune`.
12. **Example.** A workflow that opts in its pure summarization task and keeps its review task out:

    ```json
    {
      "version": "1.0",
      "id": "doc-pipeline",
      "repo_set": "default",
      "defaults": {"cache": false},
      "tasks": [
        {
          "id": "summarize",
          "agent": "writer",
          "instruction": "specs/instr/summarize.md",
          "inputs": ["docs/notes.md"],
          "outputs": ["out/summary.md"],
          "cache": true
        },
        {
          "id": "review",
          "agent": "reviewer",
          "instruction": "specs/instr/review.md",
          "inputs": ["out/summary.md"],
          "outputs": ["out/review.md"],
          "depends_on": ["summarize"]
        }
      ]
    }
    ```

    Run it with `ao run --cache …` (or `AO_CACHE=shadow` to measure first). Only `summarize` is
    looked up; `review` gets no record because it is not opted in.

**Local iteration example.** A developer has a 10-task linear workflow in which every task is opted
in. They edit task 7's instruction **without committing** and re-run with `--cache` in a checkout
where all outputs were deleted:

- Tasks 1–6 hit.
- Task 7 misses, because its instruction content changed.
- Tasks 8–10 miss, because their inputs changed.

If the edit had been **committed**, every task would miss with `include_repo_heads: true`, the
default. That is the fail-closed trade-off, and G0 measures it.

---
## 18. Test strategy

### Pyramid

- **Unit tests (most).** Pure modules, tested with fakes kept in `tests/cache/fakes.py`
  (delivered by T-FJH6LI): `InMemoryCacheStore`, `FakeRepoHeadReader`, `FakeWorktreeProbe`, a fake
  `cli_version_of`, and a fake VCS runner.
- **Integration tests.** An `Orchestrator` driven by a test-local `CountingExecutor(FakeExecutor)`,
  using a fixed clock, temporary workspaces, and real repositories where a test needs them.
- **End-to-end tests.** `typer.testing.CliRunner` against `agent_orchestrator.cli.app`, using
  `executor: fake` agents. Every e2e test:
  - calls `monkeypatch.chdir(tmp_path)`, so the repo's committed `.ao/config.yaml` is never picked up;
  - writes a test workflow that **opts in**, with `"defaults": {"cache": true}`;
  - counts dispatches by monkeypatching `agent_orchestrator.executors.fake.FakeExecutor.execute`;
  - monkeypatches `agent_orchestrator.runstate._utc_now` to an advancing clock, so back-to-back runs
    get distinct run ids (run ids have 1 s granularity; developer #15).
- **Adversarial tests and the hostile-entry corpus** live in their own modules.

### Executors that report cost (Rev 3)

`FakeExecutor` reports token counts only through `token_outputs` and **never reports `cost_usd`**.
Every test that asserts a non-zero `saved_cost_usd`, `avoidable_cost_usd` or a `~$` figure in a
summary line must therefore supply cost itself:

- **Integration:** a test-local `CostlyFakeExecutor(FakeExecutor)` whose `execute` returns the
  parent's `TaskResult` with `cost_usd`, `input_tokens` and `output_tokens` set (via
  `model_copy(update=...)`).
- **End to end:** the dispatch-counting wrapper on `FakeExecutor.execute` also sets those fields on
  successful results.

### Determinism and portability

- Fixed and stepping clocks only. No `sleep`.
- FIFO tests run in a thread joined with a 5 s timeout, so a FIFO cannot hang the suite.
- **Skip markers are explicit:** FIFO tests use `@pytest.mark.skipif(not hasattr(os, "mkfifo"), ...)`;
  symlink, ownership and `geteuid` tests use `@pytest.mark.skipif(sys.platform == "win32", ...)`.
- **Permission errors are simulated**, never produced with `chmod`: CI may run as root, where
  `EACCES` never happens. Tests monkeypatch `os.open` (or the `safeio` function) to raise
  `PermissionError`.
- Multiprocess tests use a bounded number of iterations and joins with timeouts. A concurrency
  test is either **non-vacuous** (it proves contention happened, see U-SM13) or is explicitly
  labelled **best-effort** in its docstring (I-15).
- VCS tests use temporary repos, an injected `hooks_dir`, and `AO_STATE_DIR` pointed at
  `tmp_path`, following the pattern already used in `tests/test_survival.py`.
- Every new cache test module sets or deletes `AO_CACHE` itself. **`tests/conftest.py` is NOT
  edited**, because the NFR-2 regression gate forbids it.

### Coverage and baseline (hard gates)

- **Package:** line coverage of `agent_orchestrator.cache` ≥ **85%**.
- **Core modules:** `keys.py`, `store.py`, `restore.py` and `coordinator.py` each ≥ **90%**.
- Both are pass/fail acceptance criteria of T-JCOAsq, enforced by an additive CI step in
  `.github/workflows/ci.yml` (T-JCOAsq's file scope), modelled on the existing per-package bench
  and dashboard steps:

  ```yaml
      - name: Result cache tests + coverage (E-Rc4Hk8)
        run: |
          pytest tests/cache tests/test_e2e_cli_result_cache.py \
            tests/test_e2e_cli_result_cache_admin.py -q \
            --cov=agent_orchestrator.cache --cov-report=term --cov-fail-under=85
          for m in keys store restore coordinator; do
            coverage report --include="*/agent_orchestrator/cache/$m.py" --fail-under=90
          done
  ```

- The project-wide 80% floor must still hold.
- Baseline (design time): 5041 passed / 8 skipped / 2 known bench failures. **No new failures are
  allowed.** As built, at G2: 6903 passed / 10 skipped / 0 failed.

### 18.1 Test catalogue (mapped to requirements)

| ID | Level | What | Req | Task |
|----|-------|------|-----|------|
| U-S1 | unit | Mode resolver matrix. CLI is None/True/False. `AO_CACHE` is unset/""/"1"/" ON "/"0"/"off"/"shadow"/" Shadow "/"refresh"/"maybe" (the last two → off plus one warning each). Config is None, enabled True with each mode, or False. Checks `mode`, `source` and the warnings. | FR-1 | T-28J9oR |
| U-S2 | unit | `task_cache_policy`: task False/True/None × defaults False/True/None × injected. Unset means NOT opted in; an injected `true` is ignored. | FR-2 | T-28J9oR |
| U-S3 | unit | `opted_in_count` returns the right number. | FR-1 | T-28J9oR |
| U-S4 | unit | **Flip-point pin:** `constants.DEFAULT_TASK_CACHE_POLICY is False`, and `task_cache_policy` returns exactly that constant when both levels are unset (monkeypatching the constant to `True` flips the result). The failure message names ADR-0019 D1. | FR-2 | T-28J9oR |
| U-C1..C4 | unit | `CacheConfig` defaults; bounds (0, too large, `ttl_days` 0/40000); `max_entry_bytes` clamped when unset and rejected when explicitly above `max_bytes`; `StrictBool`; mode enum (`refresh` rejected); the init template parses. | FR-15 | T-28J9oR |
| U-M1..M4 | unit | Spec fields reject `"yes"`/`1` (`StrictBool`). An old `state.json` loads. Truth table for `is_current_result_cache_record`, including the `ended_at` binding. A stand-in pre-epic model loads new JSON. A record with `saved_cost_usd=inf` is rejected. | FR-2, NFR-6, D14 | T-28J9oR |
| U-M5 | unit | Derived fields: `hit` equals `outcome == "hit"`; `saved_tokens` equals input + output; a loaded record with contradictory `hit`/`saved_tokens` values is normalized, not trusted. | FR-11, D35 | T-28J9oR |
| U-T1..T5 | unit | Entry bounds and patterns. `AwareDatetime` rejects naive values. `inf`/`nan` are rejected. `to_canonical_bytes` is deterministic. `parse_entry_bytes` is **total** over the corpus. | NFR-11 | T-FJH6LI |
| U-IO1..IO7 | unit | `safeio` (symlink, directory, FIFO, bounds, exclusive create, chain checks, ownership simulated with `geteuid`, sensitive table, control characters), with explicit skip markers. | M-4, M-6, M-14, M-15 | T-FJH6LI |
| U-AST | unit | AST guard over `cache/`: no `pickle`/`marshal`/`shelve`/`eval`/`exec`/`shell=True`, and no bare `open(`/`os.open(` outside `safeio.py`. | M-9, D31 | T-FJH6LI |
| U-A1..A3 | unit | Behaviour-identical argv extraction; purity; non-mutation. **U-K8a:** argv invariant to run id and task id. | D7 | T-OeRYSO |
| U-H1 | unit | File digest equals `sha256(content)`, with the byte size. | FR-3 | T-8tr1H4 |
| U-H2 | unit | Directory digest is independent of creation order; a rename changes it. | FR-3 | T-8tr1H4 |
| U-H3 | unit | Empty directory; non-UTF-8 names are deterministic. | FR-3 | T-8tr1H4 |
| U-H4 | unit | Symlink inside a directory → `input_not_regular`. | M-5 | T-8tr1H4 |
| U-H5 | unit | FIFO inside a directory and as a direct input → `input_not_regular`, within 5 s. | M-6 | T-8tr1H4 |
| U-H6 | unit | Budget on bytes and files → `input_too_large`, checked before reading (spy on `os.read`). | NFR-4 | T-8tr1H4 |
| U-H7 | unit | A file that grows while being read → `input_unstable`. | NFR-4 | T-8tr1H4 |
| U-H8 | unit | Missing path → `input_missing`; simulated `PermissionError` → `input_unreadable`. | FR-3 | T-8tr1H4 |
| U-H9 | unit | `.git` and `<ws>/.orchestrator` are skipped by directory walks. | N-6 | T-8tr1H4 |
| U-H10 | unit | The task's own declared outputs (`exclude_abs`) are skipped. | N-6 | T-8tr1H4 |
| U-G1..G6 | unit | `find_git_toplevel` (repo, subdirectory, `.git` file, plain dir); head reader cases (non-git omitted, unborn, sha; `rev_parse` failure, timeout, `OSError`, `RuntimeError` → `repo_head_unavailable`); memoization; worktree snapshot (excludes outputs and `.orchestrator`; detects a tracked edit by mtime/size; ignores untracked files); probe failure → `repo_worktree_probe_failed`. | D5, D13 | T-8tr1H4 |
| U-G7 | unit | **A `.git` above the workspace root is ignored** (simulated `$HOME` repository): a plain project dir stays non-git and nothing is run against the outer repo. A repo path outside the workspace → `path_rejected`. `nested_repo_marker` reports the outer marker. | D5, A-12 | T-8tr1H4 |
| U-G8 | unit | Repository reads use only public `GitRepo` methods and pass `GIT_OPTIONAL_LOCKS=0` (spy on the runner's env); the timeout passed to `GitRepo` is 10 s. | D13 | T-8tr1H4 |
| U-F1..F5 | unit | `CliVersionReader`: memoized per binary identity; a replaced binary (new mtime or size) is re-read; a timeout, a missing binary or a non-zero exit → `executor_fingerprint_unavailable`. The env allowlist copies only listed vars and **`ANTHROPIC_API_KEY` never appears**. Context files (absent, symlink inside the workspace followed, outside → `path_rejected`, the cwd chain, a symlink inside `.claude/agents` → uncacheable). | D7 | T-uoYW6b |
| U-K1 | unit | **GV-1 (Rev 2) exact**, including the 11 component digests. | FR-3 | T-uoYW6b |
| U-K2 | unit | Determinism (100 builds); a moved workspace keeps its key (N-3). | NFR-3 | T-uoYW6b |
| U-K3 | unit | Sensitivity: every listed change alters the key. | FR-3 | T-uoYW6b |
| U-K4 | unit | Insensitivity: task/run id, timeout, retries, scheduling fields, `forbidden_task_models`, non-allowlisted env vars, workspace path. | FR-3 | T-uoYW6b |
| U-K5 | unit | Normalization N-1…N-4 and N-6…N-8. | FR-3 | T-uoYW6b |
| U-K6 | unit | Preseed (N-5): an input that is also an output uses the prior digest; the settle-time recompute with the preseed equals the lookup key. | D6, D13 | T-uoYW6b |
| U-K7 | unit | `build_prompt` is invariant to the run and task id placeholders. | D4 | T-uoYW6b |
| U-K8 | unit | Tripwire: `AGENT_KEY_FIELDS \| AGENT_NON_KEY_FIELDS == set(AgentSpec.model_fields)`. | FR-4 | T-uoYW6b |
| U-K9 | unit | Path rules: `path_rejected` (traversal, symlink loop, NUL, `CLAUDE.md` symlink out of the workspace), `path_in_cache_dir`, `duplicate_output`. | FR-4 | T-uoYW6b |
| U-K10 | unit | Output rules: `sensitive_output` (including an output symlinked into `.git/hooks`), `control_output`, `output_not_regular_file`. | D29 | T-uoYW6b |
| U-K11 | unit | Document rules: `prompt_render_error` (unknown placeholder), `key_encoding`. | FR-3 | T-uoYW6b |
| U-K12 | unit | D21 summary hygiene: a secret in `extra_args` never appears in `KeySummary`; argv, prompt and templates are absent. | D21 | T-uoYW6b |
| U-E1 / U-E2 | unit | **Tripwires** for `TaskSpec` and for `WorkflowSpec`/`WorkflowDefaults`, with instructive failure messages. | FR-4 | T-QgQy08 |
| U-E3..E26 | unit | One test per eligibility-owned reason row of §8.3.3, plus the positive cases, determinism of the first reason, and purity (no I/O). | FR-4 | T-QgQy08 |
| U-E27 | unit | **`unknown_agent_field`:** an `AgentSpec` subclass field at its default → eligible; non-default → ineligible with the field name. | FR-4, D10 | T-QgQy08 |
| U-E28 | unit | Consistency: every task for which `models._is_structural_task` is true is ineligible. | FR-4 | T-QgQy08 |
| U-ST1..ST14 | unit | Store core: factory without I/O; checks on **every** operation; layout and modes; `git check-ignore`; `entries/v1`; canonical bytes; key validated before any filesystem access; hostile entry files → `CacheIntegrityError`; symlinked root → `CacheLayoutError`; atomic writes; dedupe; bounds; `has_blob`; unknown layout. | FR-7, M-2, M-4, M-6 | T-U7ckfd |
| U-ST15 | unit | **Unsafe paths (D33):** with `entries/v1/<shard>` (or `entries/v1` itself) a symlink to a victim directory holding `<key>.json`, `get_entry`, `touch_entry` and `delete_entry` raise `CacheUnsafePathError`; the victim file still exists with unchanged mtime. The same for a symlinked blob shard with `has_blob`, `read_blob` and `delete_blob`. | M-4, D33 | T-U7ckfd |
| U-SM1..SM12 | unit | Maintenance: TTL; LRU with low-water mark and tie-break; shared blobs; grace; tmp sweep; version safety (hex-token mark, foreign dirs never deleted); stale trash; `clear` creates its trash and verifies; read-only `verify` kinds; streaming `iter_entries`; bounded inline enforcement by **entry count** (U-SM10a) and by **entry-file bytes** (U-SM10b), each deferring without parsing; dry run; report shapes. | FR-7, D18, D19 | T-HjxNQ0 |
| U-SM13 | unit (multiprocess) | **Same-key race, non-vacuous:** 8 writers and 2 readers start behind a `multiprocessing.Barrier`; at least two writers complete a `put_entry` for the same key (proved by their return values), readers observe at least one complete entry, every observed entry is valid with its blobs present, and `verify().ok` holds afterwards. Skipped with a reason on < 2 CPUs. | NFR-5 | T-HjxNQ0 |
| U-R1..R12 | unit | Capture and restore (§8.5), including ADV-1/3/5/6/8/10 at unit level and the `O_CLOEXEC` flag, with skip markers. | FR-8 | T-u3jG8F |
| U-CO1..CO10, U-CO12..CO16 | unit | Coordinator (fakes): not opted in → no record; ineligible; `not_found`; hit (events, `saved_*`); expired; corrupt → evict; blob corrupt → evict + delete, not storable; `restore_failed`/`sensitive_output` not storable; `store_unavailable` warned once; **shadow** → `would_hit`, no restore, pending; the three store guards; the boundary and strict mode; the factory; inline-enforcement logging. (U-CO11, the Rev 2 `refresh` test, is retired.) | FR-5, FR-6, FR-16, D32 | T-gDNjN2 |
| U-CO17 | unit | `artifact_store_unsupported`: a non-`LocalFsArtifactStore` engine store → ineligible record. | FR-4 | T-gDNjN2 |
| U-CO18 | unit | **Lazy guard 3:** a hit never calls `WorktreeProbe.snapshot` (spy); a storable miss or would_hit calls it once; a probe failure gives no pending, `stored: false`, `store_reason: repo_worktree_probe_failed`, and the lookup outcome itself is unchanged (never ineligible). | D13 | T-gDNjN2 |
| U-CO19 | unit | **Unsafe path:** `get_entry` raising `CacheUnsafePathError` → miss `unsafe_path`, no pending, `delete_entry` **never called**, `cache.corrupt` WARNING. | D33 | T-gDNjN2 |
| U-CO20 | unit | `from_settings` warnings: a `.git` above the workspace root yields one warning; none otherwise. | A-12 | T-gDNjN2 |
| U-RC1..RC3 | unit | Record builders (hit, would_hit, miss with `store_reason`, ineligible), including bounds and derived fields. | D14, D35 | T-gDNjN2 |
| U-RP1..RP8 | unit | `current_records` (cycle plus `ended_at` binding); `task_view` and `run_block` validate against §13.5 with **exactly** the D35 fields present; sums including `saved_tokens`, `lookups`, `would_hits` and `avoidable`; exact `format_summary_line` text; `usage_counters`; `current_hit`; stale filtering through every helper. | FR-11, D35 | T-eyn5UG |
| U-RS1 | unit | With an empty map, `write_status` produces the exact pre-epic top-level key set and per-task key set (literal lists). | NFR-1 | T-eyn5UG |
| U-US1..US4 | unit | `aggregate_usage` **site A**: a current hit is not counted in tasks/succeeded/failed/retried, but a hit-after-spend task's real `cumulative_*` still counts. **Site B**: a current-hit producer gets no review attribution. Cross-run `result_cache` object (§13.6) sums; `usage_report_payload` omits it when absent. | FR-9, FR-11 | T-eyn5UG |
| U-OC1 | unit | `_settle_reason(ts, state=…, tid=…)` returns `"cached"` for a current hit, `"dispatched"` for a stale hit; `_settle_reason(ts)` is unchanged. | FR-11 | T-eyn5UG |
| U-LZ1 | unit (subprocess) | With `result_cache == {}`, `write_status`, `aggregate_usage` and `_settle_reason` do not load `agent_orchestrator.cache.report`. | NFR-1 | T-eyn5UG |
| U-LZ2 | unit (subprocess) | The same for `_print_state` / `_print_status_snapshot` (T-o95l1M) and the dashboard run detail (T-bLpoze). | NFR-1 | T-o95l1M, T-bLpoze |
| U-AST-E | unit | AST check that `engine.py` imports cache modules only under `TYPE_CHECKING` or inside `_result_cache_lookup`, and that the text contains no `open(`/`.read(`. | NFR-1, NFR-2 | T-XpF1pF |
| I-1 | integ | **Poisoned imports** (subprocess `sys.meta_path` finder) plus patched engine methods; cache modules loaded ⊆ {`cache`, `cache.constants`} (§8.7.5). | NFR-1 | T-JCOAsq Part 1 (gate for T-XpF1pF) |
| I-2 | integ | `status.json` and stdout byte-identical to the base-commit golden, serial and `max_parallel=3`, with `<WS>` normalized. | NFR-1 | T-JCOAsq Part 1 (gate for T-XpF1pF) |
| I-3 | integ | Miss → store → (outputs deleted) → hit: 0 dispatches, identical bytes, records, events. Uses `CostlyFakeExecutor` so `saved_cost_usd > 0`. | FR-5, FR-6 | T-XpF1pF |
| I-4 | integ | Chain `a → b`: both hit. | FR-5 | T-XpF1pF |
| I-5 | integ | `max_parallel=4` with mixed hits and misses: hits never reach a worker, and the result equals the serial run. | FR-5 | T-XpF1pF |
| I-6 / I-6b | integ | Budget never charged by a hit. **Crash after charge, then resume, then hit → the stale charge is reversed** (`charged_estimate` empty, consumed tokens restored) **and `budget.resume_reverse` is logged with the stale cycle.** | FR-9, D12 | T-XpF1pF |
| I-7 | integ | Breakers are not fed by hits; a verdict-source task is ineligible. | FR-9 | T-XpF1pF |
| I-8 | integ | Resume after a hit. | FR-10 | T-XpF1pF |
| I-9 | integ | Cache-off resume after a deleted-output hit: re-dispatched, the record is stale, and it is omitted everywhere. | FR-10, D14 | T-JCOAsq |
| I-10 | integ | Purity: an input mutated → `key_changed_during_run`; a commit → `repo_head_moved` (also with `include_repo_heads: false`); an undeclared tracked edit → `repo_worktree_changed`. | FR-6 | T-JCOAsq |
| I-11 | integ | The prior-output rule. | D6 | T-JCOAsq |
| I-12 | integ | Isolation workflow with the cache on: all tasks ineligible, and integration commits identical. | FR-4 | T-JCOAsq |
| I-13 | integ | Corrupt blob between runs: miss (not storable) on run 2, re-stored on run 3. | FR-8 | T-JCOAsq |
| I-14 | integ/adv | `../escape` tamper: no file outside the workspace. | M-1 | T-JCOAsq |
| I-15 | integ | **Best-effort smoke:** two processes on the same workspace both succeed, and `verify` is ok. Contention itself is proved by U-SM13. | NFR-5 | T-JCOAsq |
| I-16 | integ | `emit_tasks` children; an injected task can only narrow. | FR-2 | T-JCOAsq |
| I-17 | integ | TTL with a stepping clock. | FR-7 | T-JCOAsq |
| I-18 | integ | `dispatch_cycle` keeps its increment on a hit; `ui.activity.locate_attempt_dirs` returns `[]`; a later real dispatch uses `cycle-2/`. | D12 | T-XpF1pF |
| I-19 | integ | A committing sibling at `max_parallel=2` → a concurrent cacheable task is **not stored**, which is safe. | D13 | T-JCOAsq |
| I-20 | integ | A quota requeue followed by a hit: real spend is preserved in `cumulative_*`, the task counts as a hit, and usage site A still counts that real spend. | D12, FR-9 | T-JCOAsq |
| I-21 | integ | `join: any` with a `not_taken` dependency → the task never hits and gets no record. | FR-5 | T-XpF1pF |
| I-22 | integ | A missing input fails **before** the lookup → no record. | FR-5 | T-XpF1pF |
| I-23 | integ | **Shadow** end-to-end: `would_hit`, the agent still runs, outputs are untouched by the cache, the entry is refreshed. (I-24, the Rev 2 `refresh` test, is retired.) | FR-16 | T-JCOAsq |
| I-25 | integ | An unexpected exception injected into the coordinator → `cache.disabled`, the run **completes** normally, and strict mode re-raises. | D32 | T-JCOAsq |
| I-26 | integ | A concurrent `prune` and store race is benign. | NFR-5 | T-JCOAsq |
| I-27 | integ | In-engine asserts: a first-pass hit has `attempts == 0`; a task with `cache: false` under `defaults.cache: true` gets no lookup and no record. | FR-2, D12 | T-XpF1pF |
| E-1 | e2e | `ao run --cache` twice, with outputs deleted in between (opted-in workflow): the second run makes **0** dispatches and prints `Result cache: hits=2 …`. Companion negative: the same with the cache off counts 2 dispatches. | FR-5, FR-11 | T-JCOAsq |
| E-2 | e2e | A plain run: no cache directory and no "Result cache" text; stdout matches the golden; in a subprocess, cache modules loaded ⊆ {`cache`, `cache.constants`, `cache.settings`, `cache.cli`} (as built, DV-16; `settings` and `cli` must be present, so the check is not vacuous). | NFR-1 | T-JCOAsq |
| E-3 / E-4 | e2e | `--no-cache` wins over env and config. The full precedence matrix, including `AO_CACHE=shadow` and the unknown values `refresh` and `maybe` (off plus a warning). | FR-1 | T-JCOAsq |
| E-5 | e2e | Double opt-in: the operator enables, but a workflow without `cache: true` → banner "no task opts in" and no records. `defaults.cache: false` plus one task with `true` → only that task is cached. | FR-2 | T-JCOAsq |
| E-6 | e2e | `ao resume --cache` and `--no-cache` after a failure. | FR-10 | T-JCOAsq |
| E-7 | e2e | `ao cache ls/stats/show/rm/prune/clear/verify`, text and `--json`, validated against the §13.4 fixtures, including every exit code and control-character stripping. | FR-12, FR-17, M-15 | T-6tRKml |
| E-8 | e2e/unit | Bench forced off. | FR-14 | T-ZTxN1x |
| E-9 / E-10 | e2e | The status line; the `report-usage` lines and the JSON `result_cache` object (§13.6), omitted when absent. Cost comes from the wrapper, because `FakeExecutor` reports none. | FR-11 | T-o95l1M |
| E-11 | e2e | The banner shows the mode, source, opted-in count and, for a nested workspace, the warning. | FR-1 | T-o95l1M |
| D-1 | pytest + vitest | Dashboard payload fields (exact D35 fields); tag and tile; text-only `source_run_id`; **the file browser refuses `.orchestrator/cache/**`**. Backend tests in `tests/ui/`. | FR-13, M-10 | T-bLpoze |
| ADV-1..10 | adversarial | `../escape`; key splicing (CLI arguments, entry names); corrupt blob; symlinked root/shard/blob (4); output symlink and symlink inside an input dir; FIFO as input, inside a directory and as a cache file (6a/b/c); lying `size`; mode `0o4777` (corrupt) vs `0o777` restored as `0o755` (8a/b); **hostile-entry corpus** (9); **sensitive outputs** including `.git/hooks`, `CLAUDE.md`, `.github/workflows` (10). | NFR-10 | T-JCOAsq Part 2 (+ the unit owners) |
| ADV-4b | adversarial | **Planted symlinked shard / `entries/v1` directory during a real lookup**: miss `unsafe_path`, the victim directory is untouched (nothing read, `utime`d or unlinked), the entry is not evicted. | M-4, D33 | T-JCOAsq Part 2 |

## 19. Acceptance criteria matrix

| Req | Acceptance criterion (pass/fail) | Tests |
|-----|----------------------------------|-------|
| FR-1 | **No setting:** a plain `ao run` creates no cache directory, runs no cache code, and prints no banner. **Kill switch:** `--no-cache` wins over `AO_CACHE=1` and `cache.enabled: true`. **Unrecognized env value:** `AO_CACHE=maybe` turns the cache off and prints one warning. **Shadow:** resolves from env and from config; `refresh` is unknown (off plus a warning). | U-S1, E-2, E-3, E-4, E-11 |
| FR-2 | The operator enables the cache, but no task is opted in → no records, and the banner says so. Only tasks with an effective `cache: true` are considered. An injected `true` is ignored. The default is the pinned constant `DEFAULT_TASK_CACHE_POLICY = False`. | U-S2, U-S4, E-5, I-16, I-27 |
| FR-3 | GV-1 (Rev 2) is reproduced exactly. Every sensitivity change alters the key; every insensitivity change does not. | U-K* |
| FR-4 | Each eligibility reason is produced only by its trigger. A new unclassified field on `TaskSpec`, `AgentSpec`, `WorkflowSpec` or `WorkflowDefaults` fails a tripwire **and**, if its value is not the default, makes the task ineligible at runtime (`unknown_task_field`, `unknown_agent_field`, `unknown_workflow_field`). | U-E*, U-K8, U-CO17 |
| FR-5 | Run 2 with outputs absent makes 0 executor calls for opted-in, eligible tasks. Output bytes are identical and the tasks are `succeeded`. `join` / `not_taken` and missing-inputs paths never hit. | I-3, I-4, I-5, I-21, I-22, E-1 |
| FR-6 | Only a final `succeeded` dispatch is stored. Mutated inputs, a moved HEAD and undeclared tracked edits all prevent storing. A hit never takes the guard-3 snapshot; a probe failure makes the outcome not storable and never ineligible. | U-CO*, U-CO18, I-10, I-19 |
| FR-7 | Writes are atomic, the race test passes, and LRU, TTL, sweep and inline bounds (entries and bytes) follow §8.4.3. Foreign-version entries and the blobs they reference survive maintenance. An unsafe path is never followed or evicted. | U-ST*, U-ST15, U-SM*, ADV-4b |
| FR-8 | A restore never writes outside spec-derived, non-sensitive, re-validated destinations. A corrupt or missing blob, or a mismatched manifest, gives a miss with eviction and no destination modified. File modes are masked. | U-R*, ADV-*, I-13, I-14 |
| FR-9 | A hit leaves `cumulative_*` and `tripped_breakers` unchanged. `budget_counters` change only to reverse a **stale** charge of the previous cycle, with a `budget.resume_reverse` event. `saved_*` equals the entry's usage. Usage keeps a hit's real carried spend. | I-6, I-6b, I-7, I-20, U-CO*, U-US* |
| FR-10 | After `ao resume`, a restored task is still `succeeded`, is not re-dispatched, and its record is current. Stale records are ignored. | I-8, I-9, E-6 |
| FR-11 | Every §15 event is emitted with its fields. `status.json`, the summary line, `report-usage` and `report-outcomes` match §8.8 exactly, with exactly the D35 fields on every per-task surface. | U-RP*, U-US*, U-OC1, U-M5, E-9, E-10 |
| FR-12 / FR-17 | Every `ao cache` `--json` output validates against §13.4, and the exit codes match §8.9. `rm <key\|prefix>` removes exactly one entry. `verify` is read-only. | E-7 |
| FR-13 | The tag appears only for current hits; the tile appears only when the run block exists. The file browser refuses the cache directory. | D-1 |
| FR-14 | The bench argv contains `--no-cache` and `AO_CACHE == "0"`. | E-8 |
| FR-15 | Config keys validate, including bounds and clamping. The init template documents them. | U-C* |
| FR-16 | Shadow never writes to the workspace, records `would_hit`, and stores. | I-23, U-CO10 |
| NFR-1 | I-1, I-2 (serial and `max_parallel=3`), U-AST-E, U-LZ1, U-LZ2 and E-2 pass. The full suite has no new failures. `tests/conftest.py` is unedited. | I-1, I-2, U-AST-E, U-LZ*, E-2 |
| NFR-2 / NFR-8 | The `engine.py` static audits pass; the diff is net ≤ +110 formatted lines with ≤ 12 added lines inside existing functions, every line ≤ 100 columns. | existing tests + U-AST-E + review + `ruff` |
| NFR-4 / NFR-11 | Every cap triggers its reason. A FIFO never hangs. The hostile corpus never raises past the parse boundary, and `state.json` stays loadable. | U-H*, U-T*, ADV-6, ADV-7, ADV-9 |
| NFR-5 | U-SM13, I-15 and I-26 pass. | U-SM13, I-15, I-26 |
| NFR-9 | Package coverage ≥ 85% and each core module (`keys`, `store`, `restore`, `coordinator`) ≥ 90%, enforced by the CI step (§18). | CI + T-JCOAsq |
| NFR-10 | Gates G1a, G1b and G2 each close with 0 MUST-FIX. | T-fXWbqg |

---

## 20. Design artifacts checklist

**After HLD:**
- [x] Logical architecture diagram (§7.1, §10).
- [x] Component breakdown (§7.3).
- [x] Integration points (§7.4).
- [x] Plugin/extension strategy, including provisional ABCs (§7.5).

**After LLD:**
- [x] All interfaces and contracts defined (§14, §8.6.1).
- [x] All schemas defined (§13, §8.4.2).
- [x] Pseudocode for every module (§8.1–§8.11).
- [x] Edge cases covered (per module and §8.12).
- [x] ADR created (ADR-0019; §7.6).

**Before sprint planning:**
- [x] Tasks are atomic and have exclusive file scopes (§22.2).
- [x] Tasks are testable (`TASK.md` acceptance criteria).
- [x] Tasks are unambiguous: exact seams, names, reasons, JSON shapes, and GV-1.

**Phase 4 (consultation):**
- [x] Five consultations completed.
- [x] Every finding dispositioned in §23.4.

---

## 21. Execution readiness gate

| Question | Answer |
|----------|--------|
| Can a junior implement this without guessing? | **Yes.** Each module specifies exact names, signatures, constants, reasons, JSON shapes and pseudocode. GV-1 is pinned by a golden test. The engine seams are given as code (§8.7.1). |
| Can an AI agent execute it without ambiguity? | **Yes.** Each task has an exclusive file scope, explicit dependencies and pass/fail acceptance criteria. Commit-level dependencies on T-FJH6LI are stated identically everywhere (§8.0, §22.2): T-28J9oR needs commit 1; every other cache task needs the whole task (commit 3). T-FJH6LI depends on nothing (it references `ResultCacheRecord` only under `TYPE_CHECKING`). The dependency graph in §22.2 is acyclic and was rechecked in Rev 3. |
| Are all interfaces and schemas fully defined? | **Yes.** See §8.6.1, §13 and §14. |
| Are all failure scenarios handled? | **Yes.** See §8.4.4, §8.5, §8.6.4, §8.7.3, §8.12 and the threat model in §7.7. |
| Remaining ambiguity | OQ-1…OQ-8 (§23.2). Each has a default and none blocks implementation. **G0 (§22.5) is a post-merge business go/no-go for recommending `on`, not a design ambiguity, and it does not block epic closure.** |

**Gate: PASS.** The design is ready for implementation.

---
## 22. Sprint plan

### 22.1 Team and capacity math

**Team.** Three developers with less than 4 years of experience (Dev A, Dev B, Dev C) and one tester
(T). The review gates are run by the `reviewer` and `dev-security` agents; T-fXWbqg accounts for
those hours separately. Sprints are 2 weeks of 5-day weeks, with 40% overhead.

**Execution mode chosen by the manager (Rev 3).** The manager runs the tasks **sequentially through
agents**, grouped by cohesion (§22.3). The staffed plan below is the human-team equivalent the
architect role requires; it is also the fallback if the work is handed to a team.

**Estimate convention.** Estimates are in **focus hours**. "1 day" means 8 focus hours, the repo's
ticket convention. Each task is at most 24 focus hours, which is 3 days. At 60% net focus, a task
takes about 1.7× as long in calendar time (4.8 focus hours per working day).

**Capacity per sprint** (team_size = 4):

- `GrossHoursPerSprint = 4 × 10 × 8 = 320 h`
- `NetFocusHoursPerSprint = 320 × 0.60 = 192 h`
- `CommitmentHoursPerSprint = 192 × (0.70..0.85) = 134..163 h`
- Per person: 80 h gross, 48 h net, 34–41 h committed.

**Totals: 288 h over 20 tasks.**

| Group | Hours |
|-------|-------|
| Developers (17 tasks) | 234 |
| Tester (T-JCOAsq 24, T-nPMuz4 6) | 30 |
| Review gates (T-fXWbqg) | 24 |

**Dependency critical path (Rev 3, recomputed).** T-FJH6LI (16) → T-8tr1H4 (14) → T-uoYW6b (20)
→ T-gDNjN2 (20) → T-XpF1pF (16) → T-JCOAsq Part 2 (10) → T-JCOAsq Part 3 (8) → G2 (8) →
T-bdQZW4 (8) = **120 focus hours**, about 25 working days. The Rev 2 figure (104 h) left out
T-8tr1H4, which T-uoYW6b needs (T-8tr1H4 in turn needs T-FJH6LI commit 3), and T-JCOAsq Part 2;
the Rev 2 "resource path" figures are replaced by the staffed schedule below.

**Staffed schedule.** With per-person commitment kept within 34–41 h per sprint, the plan below
finishes at about **134 focus hours ≈ 28 working days**. Three sprints (30 working days) leave
about 2 days of slack, so gate fix-up loops must stay small; S3 carries the slack.

| Sprint | Planned | Dev A | Dev B | Dev C | Tester / gates |
|--------|---------|-------|-------|-------|----------------|
| S1 | 110 h | T-FJH6LI 16, T-uoYW6b 18 of 20 (34) | T-28J9oR 16, T-QgQy08 13, T-ZTxN1x 4 (33) | T-OeRYSO 6, T-8tr1H4 14, T-U7ckfd 17 (37) | T-JCOAsq Part 1 6 |
| S2 | 108 h | T-uoYW6b 2, T-gDNjN2 20, T-XpF1pF 12 of 16 (34) | T-u3jG8F 16, T-eyn5UG 18 (34) | T-HjxNQ0 15, T-6tRKml 17 (32) | G1a 8 |
| S3 | 70 h + slack | T-XpF1pF 4; fix-ups | T-o95l1M 8, T-bdQZW4 8 (16) | T-bLpoze 10 | T-JCOAsq Parts 2–3 18, T-nPMuz4 6; G1b 8, G2 8 |

Every per-person sprint load is within the 34–41 h band or below it; S3 is deliberately light. The
slack absorbs:

- fix-up loops from G1a, G1b and G2 (a junior team on security-sensitive code);
- the merge with the two sibling epics (R-7), including the golden recapture (§24.2).

### 22.2 Tasks

T-FJH6LI commit numbering is identical everywhere: commit 1 = `__init__.py` + `constants.py`;
commit 2 = `safeio.py`; commit 3 = `types.py`, fakes, contract suite, corpus, AST guard (§8.0).

| # | Task | Owner | Est | Sprint | Depends on | Exclusive file scope |
|---|------|-------|-----|--------|------------|----------------------|
| 1 | `T-FJH6LI-cache-contracts` | Dev A | 16 h | S1 | — | `cache/__init__.py`, `cache/constants.py` (commit 1), `cache/safeio.py` (commit 2), `cache/types.py` (commit 3), `tests/cache/{__init__,fakes,store_contract,test_types,test_safeio,test_ast_guard}.py`, `tests/fixtures/result_cache/corpus/**` |
| 2 | `T-28J9oR-cache-spec-config-surface` | Dev B | 16 h | S1 | T-FJH6LI commit 1 | `models.py`, `specs/workflow.schema.json`, `project_config.py`, `cli.py` (options + helper resolution half + `add_typer`), `cache/settings.py`, `cache/cli.py` (skeleton), tests |
| 3 | `T-OeRYSO-executor-argv-builder` | Dev C | 6 h | S1 | — | `executors/claude_cli.py` (extraction only), `tests/test_claude_cli_argv_builder.py` |
| 4 | `T-8tr1H4-cache-hashing` | Dev C | 14 h | S1 | T-FJH6LI (commit 3) | `cache/hashing.py`, `cache/repo_state.py`, tests |
| 5 | `T-uoYW6b-cache-key-builder` | Dev A | 20 h | S1→S2 | T-FJH6LI, T-OeRYSO, T-8tr1H4 | `cache/fingerprint.py`, `cache/keys.py`, tests (GV-1) |
| 6 | `T-QgQy08-cache-eligibility` | Dev B | 13 h | S1 | T-28J9oR, T-FJH6LI | `cache/eligibility.py`, tests |
| 7 | `T-ZTxN1x-bench-cache-force-off` | Dev B | 4 h | S1 | T-28J9oR | `bench/subjects.py`, `tests/bench/test_bench_cache_forced_off.py` |
| 8 | `T-U7ckfd-cache-store-core` | Dev C | 17 h | S1 | T-FJH6LI | `cache/store.py` (`LocalFsCacheStore(CacheStore)`, `is_expired`), tests |
| 9 | `T-u3jG8F-cache-restore-capture` | Dev B | 16 h | S2 | T-FJH6LI | `cache/restore.py`, tests |
| 10 | `T-eyn5UG-cache-reporting` | Dev B | 18 h | S2 | T-28J9oR, T-FJH6LI | `cache/report.py`, `runstate.py` (`write_status`), `usage.py`, `outcomes.py`, tests |
| 11 | `T-HjxNQ0-cache-store-maintenance` | Dev C | 15 h | S2 | T-U7ckfd | `cache/store.py` (adds the `CacheAdmin` base and methods, real `maybe_enforce_limits`), tests |
| 12 | `T-gDNjN2-cache-coordinator` | Dev A | 20 h | S2 | T-uoYW6b, T-QgQy08, T-U7ckfd, T-u3jG8F, T-8tr1H4, T-28J9oR | `cache/coordinator.py`, `cache/records.py`, tests |
| 13 | `T-XpF1pF-cache-engine-integration` | Dev A | 16 h | S2→S3 | T-gDNjN2, **T-JCOAsq Part 1** | `engine.py`, `tests/cache/test_engine_result_cache.py` |
| 14 | `T-o95l1M-cache-cli-wiring` | Dev B | 8 h | S3 | T-XpF1pF, T-eyn5UG, T-gDNjN2 | `cli.py` (construction half, `Orchestrator(result_cache=)`, banner, summary lines, `report-usage` lines), `tests/cache/test_cli_result_cache_wiring.py` |
| 15 | `T-bLpoze-cache-dashboard-surface` | Dev C | 10 h | S3 | T-eyn5UG | `ui/runs.py`, `ui/files.py` (deny), `ui/src/types.ts`, `ui/src/components/RunDetail.tsx`, `tests/ui/test_result_cache_ui.py`, `ui/src/test/run-detail-result-cache.test.tsx`, `src/agent_orchestrator/ui/static/**` (separate commit) |
| 16 | `T-6tRKml-cache-cli-commands` | Dev C | 17 h | S2 | T-HjxNQ0, T-28J9oR | `cache/cli.py`, `tests/test_e2e_cli_result_cache_admin.py`, `tests/fixtures/result_cache/schemas/**` |
| 17 | `T-JCOAsq-cache-test-hardening` | Tester | 24 h | Part 1 S1 (6) / Parts 2–3 S3 (18) | Part 1: none (base code). Part 2: T-XpF1pF, T-u3jG8F, T-HjxNQ0. Part 3: Part 2, T-o95l1M, T-6tRKml, T-ZTxN1x, T-bLpoze | `tests/cache/test_noop_proof.py`, `tests/cache/test_adversarial.py`, `tests/cache/test_integration_hardening.py`, `tests/test_e2e_cli_result_cache.py`, `tests/fixtures/result_cache/golden/**`, **`.github/workflows/ci.yml` (one additive step)**; never `tests/conftest.py` |
| 18 | `T-fXWbqg-cache-review-gates` | reviewer + dev-security | 24 h | G1a S2 / G1b S3 / G2 S3 | per gate (§22.4) | ticket docs; `output/E-Rc4Hk8-cross-run-result-cache/review-*.md` |
| 19 | `T-nPMuz4-cache-shadow-value-check` | Tester (+ manager sign-off) | 6 h | S3 | T-o95l1M, T-eyn5UG | `docs-md/result-cache-g0-protocol.md`, `output/E-Rc4Hk8-cross-run-result-cache/g0-protocol-smoke.md`, epic `STATUS.md` (G0 line) |
| 20 | `T-bdQZW4-cache-docs-refresh` | Dev B (+ architect sign-off) | 8 h | S3 | G2 PASS, **T-nPMuz4** | `docs-md/**` (except the G0 protocol doc), `README.md`, `meta/ROADMAP.md`, `.claude/skills/workflow-authoring/SKILL.md`, pointer comments in `models.py` (next to `EFFORT_MAX_TURNS`) and `executors/claude_cli.py` |

**Shared files.** Every shared file is touched by tasks that are ordered by dependency, so no two
tasks edit the same file at the same time.

| File | Tasks, in order |
|------|-----------------|
| `models.py` | T-28J9oR, then T-bdQZW4 (comment only) |
| `cli.py` | T-28J9oR, then T-o95l1M |
| `executors/claude_cli.py` | T-OeRYSO, then T-bdQZW4 (comment) |
| `cache/store.py` | T-U7ckfd, then T-HjxNQ0 |
| `cache/cli.py` | T-28J9oR, then T-6tRKml |
| `engine.py` | T-XpF1pF only |
| `runstate.py`, `usage.py`, `outcomes.py` | T-eyn5UG only |
| `ui/*` | T-bLpoze only |
| `.github/workflows/ci.yml` | T-JCOAsq only |

### 22.3 Execution order

**Sequential agent execution (the manager's plan, Rev 3).** One task at a time, grouped by
cohesion, with independent review passes as gates:

1. **Core set:** T-FJH6LI → T-28J9oR → T-OeRYSO → T-QgQy08 → T-8tr1H4 → T-uoYW6b → T-U7ckfd →
   T-u3jG8F → T-HjxNQ0.
   - T-OeRYSO has an independent file scope and may run at any time, including in parallel right
     now.
   - T-JCOAsq Part 1 needs only base code; it may run at any point before T-XpF1pF.
2. **Gate G1a** (key, store and restore core).
3. **Engine set:** T-gDNjN2 → (T-JCOAsq Part 1, if not done yet) → T-XpF1pF → T-eyn5UG →
   T-o95l1M → T-ZTxN1x.
4. **Gate G1b** (coordinator, engine, CLI wiring, reporting).
5. **Surfaces:** T-nPMuz4 (G0 protocol and shadow smoke validation) → T-6tRKml → T-bLpoze.
6. **Hardening:** T-JCOAsq Parts 2–3 → **Gate G2** → T-bdQZW4 (last).

Every dependency in §22.2 is satisfied by this order. (T-6tRKml only needs T-HjxNQ0 and
T-28J9oR, so it can move earlier if convenient.)

**Staffed parallel order (the fallback).** Follow the §22.1 table; within a sprint, start the
tasks in the order of the table rows.

### 22.4 Gates

| Gate | When | Scope | Exit criteria |
|------|------|-------|---------------|
| **G1a** (reviewer + dev-security) | after the core set | T-FJH6LI (`safeio`, parse boundary, AST guard), T-28J9oR and T-QgQy08 (kill switch, policy flip point, eligibility incl. `unknown_agent_field`), T-OeRYSO, T-8tr1H4 (repo detection stops at the workspace root), T-uoYW6b, T-U7ckfd (unsafe paths), T-u3jG8F, T-HjxNQ0 (destructive maintenance) | 0 MUST-FIX. M-1…M-9, M-13 and M-14 verified with file:line evidence. Hostile corpus, AST guard, U-ST15 and U-G7 green. GV-1 reproduced. U-A* (argv identity) green. |
| **G1b** (reviewer + dev-security) | after the engine set | T-gDNjN2 (lazy guard 3, unsafe-path no-evict, boundary), T-XpF1pF, T-eyn5UG (exact fields, usage sites, lazy imports), T-o95l1M, T-ZTxN1x, T-JCOAsq Part 1 | 0 MUST-FIX. M-10, M-11, M-12 and M-16 verified. I-1, I-2 (serial and `max_parallel=3`), U-LZ1 and U-AST-E green. Engine diff net ≤ +110 formatted lines, ≤ 12 added lines inside existing functions, every line ≤ 100 columns. |
| **G2** (reviewer + dev-security delta) | after T-JCOAsq Part 3 | everything | 0 MUST-FIX. Full suite numbers pasted; the CI coverage step passes (package ≥ 85%, each core module ≥ 90%); merge notes match the diff; approval-ordering check; CLI argument handling; dashboard text-only rendering; the bundle is a separate commit. |

### 22.5 G0 — value check before recommending `on` (dev-critic STRATEGIC #1)

**This is a post-merge business go/no-go, not a build blocker and not part of epic closure (D34).
Status as built: protocol shipped; execution post-merge. G0 was not run in this epic.**

**Why.** The critic argued that, for the primary consumer, hits may be rare. Fail-closed key
components (repo HEADs, priors), committing tasks, per-epic output paths, and
`skip_if_outputs_exist: false` tasks all reduce the hit rate. G0 measures that rate before anyone
enables `on`.

**Why not inside this epic (Rev 3).** This repository cannot supply a representative workload:
`specs/self-dev/` holds only agent and reposet files, the built-in templates write per-instance
output paths, and the bench forces the cache off. G0 needs multi-day shadow-mode runs of a real
consumer workflow (finplan) with the operator's consent.

**What this epic delivers (T-nPMuz4).**

1. **A runnable protocol** in `docs-md/result-cache-g0-protocol.md`:
   - install the build as the beta flavour (`install.sh --flavor beta`, giving `ao-beta`), so the
     stable `ao` is untouched;
   - opt in only tasks that are pure by inspection (`cache: true`), recording which and why;
   - run them under `AO_CACHE=shadow` over the observation window (no model spend added);
   - collect with `ao report-usage --json` (`result_cache` object, §13.6) and
     `ao cache stats --json` (store growth);
   - fill in the report template.
2. **The measurement fields**: `result_cache.lookups`, `would_hits`, `misses`, `ineligible`,
   `miss_reasons`, `store_skip_reasons` and `avoidable_cost_usd` in `ao report-usage --json`;
   `entries`, `bytes.total`, `expired_entries`, `oldest_created_at` and `newest_created_at` in
   `ao cache stats --json`. The would-hit rate is `would_hits / lookups`.
3. **A report template** (in the protocol doc): method, workflows and tasks, metrics table per
   workflow, dominant miss reasons and components, recommendation.
4. **A smoke validation** on a fake-executor workflow run twice under `AO_CACHE=shadow`, showing
   the procedure end to end and producing a non-zero would-hit rate
   (`output/E-Rc4Hk8-cross-run-result-cache/g0-protocol-smoke.md`). This validates the tooling,
   not the value.

**Executing G0** is a follow-up owned by the parent or the operator, after the merge.

**Decision rule (a recommendation to the parent).**

| Outcome | Recommendation |
|---------|----------------|
| would-hit rate ≥ 10% of eligible lookups, **or** avoidable spend ≥ $5 per week per workflow | Document `on` for the measured workflows. |
| Otherwise | Keep the feature shipped but off. Record the dominant miss components. Re-evaluate with `include_repo_heads: false` or the `--reuse-from` alternative (ADR-0019 ALT-8). |

The thresholds are placeholders: **OQ-6, for the parent to confirm.**

---

## 23. Risks, dependencies, open questions

### 23.1 Risk register

| ID | Risk | L | I | Mitigation | Owner |
|----|------|---|---|------------|-------|
| R-1 | **Stale hits from ambient state the key does not cover.** Examples: uncommitted non-input files, `~/.claude`, the network, unlisted env vars. | M | H | Double opt-in; key includes HEADs, priors, argv and a fingerprint; three store guards; `shadow` and `ao cache rm`; TTL; guide. | architect / docs |
| R-2 | **Model-alias drift.** | M | M | CLI version and TTL in the key; guide recommends pinned ids. | docs |
| R-3 | **A bad non-deterministic result gets frozen.** | M | M | `ao cache rm <key>` then a re-run, the "cached" tag, durable `cache.hit` audit lines. | operator |
| R-4 | **Deliberate cache poisoning** by a same-uid writer. | L | H | Documented in §7.7; `--no-cache` for untrusted workflows; HMAC is non-MVP. | dev-security |
| R-5 | **Disk growth.** | L | M | Caps, LRU, TTL, bounded inline pruning, `ao cache prune`, `CACHEDIR.TAG`. | store |
| R-6 | **Clock skew.** | L | L | Aware datetimes; a future `created_at` stays valid; LRU only affects eviction order. | store |
| R-7 | **Merge conflicts with E-Ag7Pw3 / E-Da5Tn9.** | H | M | Small additive hunks; merge notes in §24.2; tripwires; the bundle is rebuilt, never merged. | parent |
| R-8 | **Hashing cost on the main thread.** | M | L | Caps; lazy guard 3 (hits skip the status read); observed in G0; memoization is non-MVP. | dev |
| R-9 | **Junior-written, security-sensitive code.** | M | H | Pseudocode-level LLD; adversarial tests and the hostile corpus; gates G1a, G1b and G2. | reviewer / dev-security |
| R-10 | **Eligibility drift as specs grow.** | M | H | Tripwires plus runtime unknown-field rules. | tester |
| R-11 | **Users think the cache "doesn't work"** (`skip_if_outputs_exist`, double opt-in). | H | L | Banner shows the opted-in count; summary line; guide; records with reasons. | docs |
| R-12 | **Users delete outputs to redo work and get a replay instead.** | M | M | Guide (EC-20); `ao cache rm`; a hit is visible in the summary and the dashboard. | docs |
| R-13 | **A stale global install doesn't know the new flags.** | M | L | Loud Typer error; release note. | operator |
| R-14 | **STRATEGIC: low hit rate / unproven value** (dev-critic #1). | M | H | Shadow mode; the G0 protocol and tooling ship with the epic; G0 itself runs post-merge (§22.5, D34); ADR-0019 alternatives ALT-7/ALT-8. **The parent decides.** | parent / operator |
| R-15 | **STRATEGIC: roadmap tension.** If isolation becomes the default (roadmap §3.4), almost no task is eligible (D25). | M | M | Migrate to the executor-level design (ADR-0019 ALT-7) when isolation is defaulted. | architect |
| R-16 | **Churn from CLI auto-updates** (version in the key). | M | L | Memoized by binary identity, so an update mid-process is seen; measured in G0; documented. | operator |
| R-17 | **The env allowlist drifts from the CLI's real env vars.** | M | M | A-9 checked in T-uoYW6b (list unchanged); easy to extend; never include secrets. Provider and endpoint variables are an accepted residual (R-22). | dev |
| R-18 | **Double opt-in friction** slows adoption. | M | L | Templates opt in their pure tasks; the banner gives guidance; one named flip point (`DEFAULT_TASK_CACHE_POLICY`) if the parent decides otherwise. | docs / parent |
| R-19 | **Nested workspace** (inside a parent repository) is treated as non-git: HEAD not keyed, guards 2 and 3 inactive. | L | M | Banner warning (`nested_repo_marker`); guide says not to opt in such tasks (A-12). | docs |
| R-20 | **Dirty tracked edits made before the lookup are not in the key** (accepted residual, Rev 3). | M | M | Guard 3 catches edits made during the run; double opt-in; documented in §17; a dirty-tree digest is not planned. | docs |
| R-21 | **False misses from noise files** (`.DS_Store`, `__pycache__`) inside declared input directories. | M | L | Fail-closed by design; guide recommends narrower inputs. | docs |
| R-22 | **Provider and endpoint environment not in the key** (accepted at G2, §0.4 R-A1). | L | M | `--no-cache` or `ao cache clear` when switching; documented in the guide. | operator |
| R-23 | **`filter.<x>.clean` executed by the guard-3 probe** (accepted at G2, §0.4 R-A2). | L | M | Needs a git-config writer that cannot already run code; `--no-cache` for untrusted repositories; documented. | operator |
| R-24 | **Agent-writable cache directory and output retention** (accepted at G1b and G2, §0.4 R-A4, R-A6). | L | M | The `on` banner notes it; `0o700`; `ao cache verify\|rm\|clear\|prune`; shadow runs should end with `ao cache clear --yes` (G0 protocol Step 9, R-A6). | operator |

### 23.2 Dependencies and open questions

**Dependencies.**
- No hard dependency on sibling epics.
- Merge-time coordination with E-Ag7Pw3 and E-Da5Tn9 (§24.2).
- External: git, for HEAD and status reads; the `claude` CLI, for the version fingerprint.

**Follow-ups found during design (outside this epic).**
1. **Bug.** The missing-inputs branch in `_prepare_and_maybe_dispatch` (engine.py ~1174) rebuilds
   `TaskRunState` without carrying `dispatch_cycle`, which violates R-21 (reviewer R4). Recommended
   as a separate bug ticket. D14's `ended_at` binding makes this epic robust to it.
2. **Pre-existing.** `ui/files.read_file` opens a planted FIFO (security NIT d). Recommended for
   E-Da5Tn9 or a security follow-up.
3. **Shared HEAD reader.** Share one HEAD reader with `survival.current_heads`. Their failure
   semantics differ today: `survival` omits a repo where the cache must fail closed (critic NIT).

**Open questions.** None blocked implementation; each had a default. As built (§0.2): OQ-1 to
OQ-3, OQ-5, OQ-7 and OQ-8 kept their defaults; OQ-4 (E-Ag7Pw3 representation) is open until that
merge; OQ-6 (G0 thresholds, owner) is open for the parent.

| ID | Question | Default chosen |
|----|----------|----------------|
| OQ-1 | `ao validate` warning for `cache: true` on an ineligible task? | Not in MVP; the reason shows in `status.json`. |
| OQ-2 | Should `ao prune` also prune the result cache? | No; it stays separate (`ao cache prune`). |
| OQ-3 | Should `include_repo_heads` be per workflow? | Config-only; revisit after G0. |
| OQ-4 | How does E-Ag7Pw3 represent approval gates (a field or a kind)? | Either way, the runtime unknown-field rules reject it until it is classified RULED. Confirm at merge. |
| OQ-5 | Pre-existing ambiguity: raw relative `dynamic_input_paths` with a non-root `working_dir`. | Root-relative resolution; a missing path → uncacheable. |
| OQ-6 | **G0 thresholds, owner and timing.** | Placeholders in §22.5; G0 runs post-merge, owned by the parent or operator; the parent confirms the thresholds. |
| OQ-7 | **Container name** `result_cache` instead of the brief's `cache` (D35). | Keep `result_cache` (collision with prompt-cache fields); the manager flags it to the parent. |
| OQ-8 | **Author-policy default.** Keep double opt-in, or flip to "operator enabled ⇒ every eligible task cached unless `cache: false`"? | Keep `DEFAULT_TASK_CACHE_POLICY = False`; flipping is a one-constant change plus an ADR addendum. |

### 23.3 Phase-4 hardening consultation record

The round ran on 2026-10-04/05. Each agent received a read-only brief and checked the documents
against the code at `bb6d8a0`. No files were edited.

| Order | Agent | Verdict on Rev 1 | MUST-FIX | SHOULD-FIX | NIT / other | Rev 2 response |
|-------|-------|------------------|----------|------------|-------------|----------------|
| 1 | `manager` | (input) parent brief + analysis A–K | — | — | — | Disposition of every item is in §7.6 |
| 2 | `developer` | feasible with fixes | 2 | 8 | 6 | All adopted (§23.4) |
| 3 | `reviewer` | approve with changes | 3 | 7 | 7 | All adopted (§23.4) |
| 4 | `tester` | conditional pass | 3 | 5 | 4 | All adopted or already covered (§23.4) |
| 5 | `dev-security` | sound but not G1-ready | 1 | 6 | 5 + gate scoping | 1 + 5 adopted; HMAC and `dir_fd` deferred with reasons; `gates_cleared` replaced by checklist + G2 (§23.4) |
| 6 | `dev-critic` | **no-go on the full build until value is validated** | 2 | 5 | 2 STRATEGIC + NITs | 2 MUST adopted. STRATEGIC: shadow mode + G0 + alternatives recorded, and **the go/no-go is escalated to the parent**. |

**Residual concerns.**

1. **Unproven value (critic).** Addressed by G0. The architect cannot overrule the parent's brief,
   so the go/no-go decision belongs to the parent.
2. **Not protected against a deliberate same-uid poisoner** (security). This is documented, and
   HMAC is non-MVP.
3. **Tension with an isolation-by-default future** (critic). The migration path is ADR-0019 ALT-7.

### 23.4 Finding-by-finding disposition (Phase 4)

The table holds 61 dispositions (findings and NITs). Rev 3 superseded three of them: the
`refresh` mode, `ao cache rm --run/--task` and `verify --repair` are deferred (§23.5).

The status column uses these values:

- **Adopted:** applied, with the location given.
- **Partial:** part applied, the remainder explained.
- **Deferred:** moved to non-MVP, with a reason.
- **Declined:** not applied, with a reason.
- **Covered:** already in the design.

| # | Finding (source) | Severity | Status | Where / why |
|---|------------------|----------|--------|-------------|
| 1 | Golden capture determinism: run id, clock, tmp paths (tester T1) | MUST | Adopted | §8.7.5 I-2: `<WS>` normalization, fixed clock and run id. The T-JCOAsq part 1 HANDOFF documents the fixture. |
| 2 | Module-attribute patch discipline (tester T2) | MUST | Adopted (superseded) | The engine now calls the injected hook and its own private methods. I-1 patches `Orchestrator._result_cache_*` and `ResultCache`. U-AST-E. |
| 3 | E-1 dispatch counting validity (tester T3) | MUST | Adopted | Spy on `FakeExecutor.execute`, plus a companion test with the cache off (§18.1 E-1). |
| 4 | Stale filtering in every consumer (tester T4) | SHOULD | Adopted | U-RP8 runs through every helper. |
| 5 | Unsupported-schema entries waste disk (tester T5) | SHOULD | Adopted (by design) | Versioned `entries/vN/`, never touched by other versions; `verify` reports `foreign_version` (D18). |
| 6 | FIFO inside a directory (tester T6) | SHOULD | Adopted | ADV-6b/6c. |
| 7 | Config negative tests (tester T7) | SHOULD | Covered + extended | U-C1..C4 (bounds, clamp). |
| 8 | Concurrent prune + store race (tester T8) | SHOULD | Adopted | I-26. |
| 9 | Multiprocess race may pass vacuously (tester T9) | NIT | Adopted | U-SM13 notes the ≥ 2 CPU assumption; bounded. |
| 10 | Per-module coverage targets (tester T10) | NIT | Adopted | Task ACs set ≥ 90% on core modules. |
| 11 | Root-symlink test (tester T11) | NIT | Covered | U-ST (per-operation checks). |
| 12 | Store JSON schemas as fixtures (tester T12) | NIT | Adopted | §13.4; T-6tRKml. |
| 13 | **Value unproven / no consumer (critic #1)** | STRATEGIC | Partial | Shadow mode (D26) and G0 (§22.5). D4 and §17 claims corrected. ADR-0019 alternatives ALT-7/ALT-8 recorded. "No-go on the full build" is **escalated to the parent**; the architect cannot overrule the brief. |
| 14 | No single-entry invalidation (critic #2) | MUST | Adopted | `ao cache rm <key\|prefix>` (D27). Rev 3: the `refresh` mode and the `--run/--task` form are deferred (non-MVP 14–15). Evicting on a FAIL verdict is non-MVP 6. |
| 15 | A committed config enables the cache silently (critic #3) | SHOULD | Partial | `mode`/`mode_source` on every record, the banner, and a docs warning (EC-21). **Declined:** a non-committed-only config layer, because the brief binds `cache.enabled` and it would add a new config layer. |
| 16 | The `KEY_SCHEMA_VERSION` convention will rot; ambient files (critic #4) | MUST / SHOULD | Adopted | argv hashed via `build_claude_argv` (D7); fingerprint (CLI version, env allowlist, context files); `model_unresolved`; pointer comment next to `EFFORT_MAX_TURNS`. Recording the resolved model is non-MVP 7. |
| 17 | Version skew: overwrites, evictions, downgrade-resume (critic #5) | SHOULD | Adopted | Versioned entry dirs; never overwrite or delete foreign versions; hex-token mark phase; `kind` reserved; durable trail via `run.log` `cache.hit` events (instead of a separate jsonl). |
| 18 | ABC overstates remote readiness (critic #6) | SHOULD | Adopted | Split `CacheStore`/`CacheAdmin`, marked **provisional**; canonical entry bytes; no workspace path except the factory. S3 output-I/O seam is non-MVP (`artifact_store_unsupported`). |
| 19 | Executor-level design fits the isolation roadmap better (critic #7) | STRATEGIC | Deferred | Recorded as ADR-0019 ALT-7 and R-15. Not MVP: the brief (P-9) places the lookup in the engine, and the executor-level design forces a budget pre-charge on hits. |
| 20 | `dispatch_cycle` decrement breaks R-21 and the dashboard (critic #8a, reviewer R4) | SHOULD | Adopted | The increment is kept; usage excludes hits explicitly (D12). |
| 21 | Hits bypass settle-side effects (critic #8b) | SHOULD | Adopted | `task.end` emitted on a hit; a documented list of settle-side effects; merge-note rule (§8.7.4). |
| 22 | Unknown WorkflowSpec fields are only test-caught (critic #8c) | SHOULD | Adopted | Runtime `unknown_workflow_field` rule (D10). |
| 23 | Dashboard exposes blobs (critic #8d, security NIT d) | SHOULD | Adopted | `ui/files.py` denies `.orchestrator/cache`. |
| 24 | Cut `--json` ids, tile, totals, `--repair` (critic #9) | NIT | Partial | `--json` ids, the tile and totals stay (the brief requires them; labelled "est."). Rev 3 defers `--repair` (non-MVP 16). |
| 25 | Split reason and detail (critic #9) | NIT | Adopted | `reason` + `reason_detail` (D14). |
| 26 | Share the HEAD reader with survival (critic #9) | NIT | Deferred | The failure semantics differ (follow-up 3). |
| 27 | **Hostile data is not parsed by total functions (security S1)** | MUST | Adopted | D28: `parse_entry_bytes`, bounded strict models, `safeio`, FIFO-safe internal reads; ADV-9 corpus. |
| 28 | Poisoning needs no key computation; HMAC (security S2) | SHOULD | Partial | Threat model reworded; `cache.hit` logs restored paths and sha256; canonical bytes. HMAC deferred (non-MVP 2): a same-uid agent can read the key, so the benefit is marginal in MVP. |
| 29 | Execution sinks via restore (security S3) | SHOULD | Adopted | D29, with checks at key build and at restore; ADV-10. |
| 30 | TOCTOU / symlinked components (security S4) | SHOULD | Partial | Per-operation root and chain checks, ownership and mode, realpath re-validation, component-wise mkdir. `dir_fd` walking deferred (non-MVP 9): active races are outside the MVP threat model. |
| 31 | `clear` silently does nothing (security S5) | SHOULD | Adopted | Trash dir created first; removal verified (D19). |
| 32 | Approval ordering is prose (security S6) | SHOULD | Partial | Seam comment, merge checklist, G2 check, runtime unknown-field rules. **Declined:** a required `gates_cleared` field, because there is no gate to test and it would be a speculative API. |
| 33 | Inline prune can stall the scheduler (security S7) | SHOULD | Adopted | Bounded, streaming, deferred above `INLINE_PRUNE_MAX_ENTRIES` (D19). |
| 34 | Mode test contradiction; `fullmatch`; control chars; digest oracle (security S8) | NIT | Adopted | ADV-8a/b; `fullmatch` everywhere; `strip_control_chars`; oracle documented. |
| 35 | Gate scoping too narrow (security S9) | SHOULD | Adopted | G1a, G1b and G2 with evidence requirements (§22.4). |
| 36 | **Default policy is not fail-closed (reviewer R1)** | MUST | Adopted | Double opt-in (D1); HEAD guard always on; tracked-worktree guard (D13). |
| 37 | The "never leaks I/O errors" contract is broken (reviewer R2) | MUST | Adopted | D32 boundary and strict flag; bounded models. |
| 38 | Budget double-charge on crash → resume → hit (reviewer R3, developer #14) | MUST | Adopted | Stale-cycle reverse on a hit (D12, I-6b). |
| 39 | Staleness assumes a monotonic cycle (reviewer R4) | SHOULD | Adopted | `ended_at` binding (D14). Engine bug recorded as follow-up 1. |
| 40 | A plugin owns a core state transition (reviewer R5) | SHOULD | Adopted | The engine owns the transition; `ResultCacheHook` Protocol; lazy imports. |
| 41 | `report-outcomes` is told cached tasks ran (reviewer R6) | SHOULD | Adopted | `settle_reason: "cached"`. |
| 42 | argv golden; command basename; `model=None` / CLI version (reviewer R7) | SHOULD | Adopted | U-A*, U-K8a; `command_not_cacheable`; `model_unresolved`; CLI version in the fingerprint. |
| 43 | `probe()` hides failures (reviewer R8) | SHOULD | Adopted | Filesystem marker walk (D5); Rev 3 renames it `find_git_toplevel` and stops it at the workspace root. |
| 44 | `check_root(workspace)` contradicts D17; ISP (reviewer R9) | SHOULD | Adopted | Factory plus the split ABCs (D17). |
| 45 | The no-op claim overstates (reviewer R10) | SHOULD | Adopted | Claim reworded; I-1/I-2 gate T-XpF1pF; `report-usage` JSON omits empty keys. |
| 46 | NITs: single safe-open helper, TTL helper, literals, resolved control paths, `type(task)`, miss components, §23.4 citation (reviewer R11) | NIT | Adopted | `safeio`, `is_expired`, constants list, keys-phase control check, `type(task)`, D30, this section. |
| 47 | `RepoHeadReader` misses OSError/RuntimeError; 300 s timeout (developer #1) | MUST | Adopted | §8.2.4: exception set, `CACHE_GIT_TIMEOUT_SECONDS`, injected runner and `hooks_dir`. |
| 48 | `tests/conftest.py` edit fails the NFR-2 gate (developer #2) | MUST | Adopted | No conftest edit; per-module `AO_CACHE` control. |
| 49 | Engine line budget (developer #3) | SHOULD | Adopted | Private methods. Rev 3 re-measured with `ruff format`: net ≤ +110 formatted lines, ≤ 12 added inside existing functions (§8.7.1). |
| 50 | Static audit regex vs "open(" in comments (developer #4) | SHOULD | Adopted | `from_settings`; the D22 rule. |
| 51 | `resolve()` raises RuntimeError/ValueError (developer #5) | SHOULD | Adopted | `guarded_resolve`. |
| 52 | pydantic pitfalls (developer #6) | SHOULD | Adopted | `AwareDatetime`, `allow_inf_nan`, bounds, `to_canonical_bytes`, `schema=` construction. |
| 53 | `type(task).model_fields`; `tasks` in the table (developer #7) | SHOULD | Adopted | §8.3.1–8.3.2. |
| 54 | Version string forks git (developer #8) | SHOULD | Adopted | `agent_orchestrator.__version__`. |
| 55 | `store_success` exceptions lose paid work (developer #9) | SHOULD | Adopted | D32. |
| 56 | Sizing, dependencies, ownership, CI gate (developer #10) | SHOULD | Adopted | §22.1 convention; tasks split (+4); key-set check removes the golden dependency; single owner for `__init__`; coverage item. |
| 57 | `restore_failed` must be non-storable (developer #11) | NIT | Adopted | §8.5, §8.6.4. |
| 58 | `StrictBool` (developer #12) | NIT | Adopted | §8.1.1. |
| 59 | `max_entry_bytes` clamp (developer #13) | NIT | Adopted | §8.1.4. |
| 60 | `spec_sha` warning on upgrade; run-id collisions (developer #15) | NIT | Adopted | §16; e2e advancing clock. |
| 61 | `fullmatch`, `--sort` as str, `O_CLOEXEC` (developer #16) | NIT | Adopted | §8.1.5, §8.9, `safeio`. |

---

### 23.5 Rev 3: independent early-gate review and manager scope decisions

A fresh `reviewer` reviewed the committed design (`94dac52`) and returned **GO-WITH-FIXES**. The
manager added scope decisions. Every item was applied; the one deviation is noted in row B7.

| # | Item | Severity | Status | Where |
|---|------|----------|--------|-------|
| A1a | `LocalFsCacheStore(CacheStore, CacheAdmin)` declared before the admin methods exist | MUST | Adopted | T-U7ckfd derives from `CacheStore`; T-HjxNQ0 adds `CacheAdmin` (§8.4.3, D17) |
| A1b | T-XpF1pF depended on T-JCOAsq "parts 1–2" (cycle) | MUST | Adopted | Depends on Part 1 only; Part 1 needs only base code (§22.2, §8.7.5) |
| A1c | Commit numbering differed between T-8tr1H4 and §22/EPIC; critical path wrong | MUST | Adopted | One numbering (§8.0, §22.2); critical path recomputed: 120 h (§22.1) |
| A1d | T-FJH6LI commit 3 needed `ResultCacheRecord` from T-28J9oR | MUST | Adopted | `types.py` imports it only under `TYPE_CHECKING` (§8.0, §8.6.1); false §21 claim replaced |
| A1e | T-bdQZW4 did not depend on T-nPMuz4; T-JCOAsq Part 1 needed store/coordinator | MUST | Adopted | Dependency added; parts re-split (Part 1 base-only; ADV-9 harness in Part 2) |
| A2 | `has_git_marker` walked to `/` (a `.git` in `$HOME` made plain dirs "git") | MUST | Adopted | `find_git_toplevel` stops at the workspace root (D5, §8.2.4, U-G7); nested-workspace residual documented (A-12, R-19) |
| A3 | Unclassified `AgentSpec` fields were silently unkeyed | MUST | Adopted | `unknown_agent_field` runtime rule (D10, §8.3, U-E27) |
| A4 | Evicting on an unsafe path could `unlink` through a link (CWE-59) | MUST | Adopted | `CacheUnsafePathError`; checks before every operation; never evicted (D33, U-ST15, U-CO19, ADV-4b) |
| A5 | G0 premise false (no representative workload here) | MUST | Adopted | G0 = protocol + tooling hand-off; execution post-merge (D34, §22.5, A-11 corrected) |
| B1 | Defer `refresh`, `rm --run/--task`, `verify --repair` | scope | Adopted | Non-MVP 14–16; removed from requirements, tests, events, schemas and CLI |
| B2 | Lazy guard 3; probe failure = not storable; `GIT_OPTIONAL_LOCKS=0` | scope | Adopted | D13, §8.2.4, §8.6.2, U-CO18, U-G8 |
| B3 | Inline prune bounded by bytes too | scope | Adopted | `INLINE_PRUNE_MAX_ENTRY_FILE_BYTES` (D19, U-SM10b) |
| B4 | Small env allowlist; memoize `claude --version` by binary identity | scope | Adopted | A-9, A-10, D7, U-F |
| B5 | One named author-policy default | scope | Adopted | `DEFAULT_TASK_CACHE_POLICY` (D1, §8.1.3, U-S4); OQ-8 |
| B6 | Exact brief #6 fields; keep the `result_cache` container | scope | Adopted | D35, §13.5, §13.6, U-M5; OQ-7 |
| B7 | G0 tooling via `ao cache stats --json` | scope | **Adopted with a deviation** | The would-hit rate needs per-lookup history, which the store does not keep; it comes from `ao report-usage --json` (`result_cache`, §13.6). `ao cache stats --json` supplies store growth (§22.5). |
| B8 | Accept the dirty-tracked-edit residual | scope | Adopted | §8.2.6, §17, R-20, EC-26 |
| C1 | Two `usage.py` dispatch sites; keep a hit's real carried spend | SHOULD | Adopted | §8.8.4, U-US*, I-20 |
| C2 | Make NFR-1 literal: lazy `cache.report` imports; I-1 module check; I-2 at `max_parallel=3`; golden recapture | SHOULD | Adopted | D9, §8.7.5, U-LZ*, §24.2 |
| C3 | Drift: "same cycle" wording; `budget.resume_reverse` on the hit path; unowned test ids; `artifact_store_unsupported` test; hard coverage AC | SHOULD | Adopted | ADR-0019 D12, §8.7.1 (`_reverse_stale_charge`), §18 (U-H9/H10, U-K5…K12 owned), U-CO17, §18 coverage gates |
| C4 | Test hygiene: cost-reporting executor; `attempts == 0`; `cache: false` under `defaults.cache: true`; non-vacuous concurrency; simulated `EACCES`; skip markers | SHOULD | Adopted | §18, U-SM13, I-15 (best-effort), I-27 |
| C5 | Key hygiene notes (`.DS_Store`, dirty tracked edits) | SHOULD | Adopted | §8.2.3, §8.2.6, EC-25, EC-26, R-21 |
| D1 | Engine seam size measured honestly; ≤ 100 columns | NIT | Adopted | Net ≤ +110 lines measured with `ruff format` (§8.7.1); copy-ready blocks ≤ 100 columns; pseudocode fenced as `text` (§8.0) |
| D2 | "61 findings" wording; epic status wording | NIT | Adopted | §23.4 ("dispositions"); EPIC `Draft` (design complete / not started) |
| D3 | Prefer public APIs; never `GitRepo._run` | NIT | Adopted | §8.2.4; U-E28 checks agreement with `_is_structural_task` without calling it in production |
| D4 | CI coverage step in T-JCOAsq's scope; bundle as a separate commit; example spec; release note | NIT | Adopted | §18, §22.2, §8.10, §17 (example), §16 (release note) |
| E | Sequential execution order | plan | Adopted | §22.3 |

## 24. Handoffs and ownership

### 24.1 Ownership and handoff chain

- **Contracts.** T-FJH6LI hands over the constants, `safeio`, types and fakes to every cache task.
  Its `HANDOFF.md` lists the frozen names; changing one requires a note in each consumer's
  `STATUS.md`.
- **Surface.** T-28J9oR feeds T-QgQy08, T-eyn5UG, T-ZTxN1x, T-6tRKml and T-o95l1M.
- **Key path.** T-OeRYSO and T-8tr1H4 feed T-uoYW6b. T-uoYW6b, T-QgQy08, T-U7ckfd and T-u3jG8F
  feed T-gDNjN2. T-gDNjN2 feeds T-XpF1pF, which feeds T-o95l1M.
- **Surfaces.** T-eyn5UG feeds T-o95l1M and T-bLpoze. T-HjxNQ0 feeds T-6tRKml.
- **Verification.** T-JCOAsq Part 1 feeds T-XpF1pF. All implementation tasks feed T-JCOAsq Parts
  2–3, then T-fXWbqg (G2), then T-bdQZW4. T-nPMuz4 (G0 protocol and tooling) feeds T-bdQZW4.
- **After the epic.** The parent or operator runs G0 with the T-nPMuz4 protocol.
- **Ticket sync.** `manager` / `dev-epic` mirror every status change across the task's `TASK.md`,
  `STATUS.md` and `HANDOFF.md`, and the epic's `EPIC.md` and `STATUS.md`.

### 24.2 Merge notes (every shared-file touchpoint)

All changes are **additive and localized**. Sibling epics: E-Ag7Pw3 (approval gates) and E-Da5Tn9
(dashboard auth).

| File | Exact additive change | Conflict guidance |
|------|----------------------|-------------------|
| `src/agent_orchestrator/models.py` | Add `StrictBool` and `model_validator` to the pydantic import. New constants `RESULT_CACHE_*` and their bounds. `TaskSpec.cache: StrictBool \| None = None` (after `verdict_path`). `WorkflowDefaults.cache` (after `model`). `class ResultCacheRecord`. `RunState.result_cache` (after `record_git_heads`). `def is_current_result_cache_record`. T-bdQZW4 later adds one pointer comment next to `EFFORT_MAX_TURNS`. | E-Ag7Pw3 likely adds a `TaskSpec` or `WorkflowSpec` field. **After the merge, tripwires U-E1/U-E2 fail until that field is classified in `cache/eligibility.py` as RULED (default-only).** An approval field is never ANY. The runtime unknown-field rules keep cached runs safe in the meantime. |
| `specs/workflow.schema.json` | `cache` property in `defaults` and in `$defs.task` | Take both sides. |
| `src/agent_orchestrator/project_config.py` | `class CacheConfig`, `ProjectConfig.cache`, template block | Take both sides. |
| `src/agent_orchestrator/engine.py` | `TYPE_CHECKING` imports; ctor kwarg; `_RunContext.result_cache_pending`; call site (c) before `_estimate = 0`; call site (d) at the top of the `succeeded` branch; three private methods, one of which (`_reverse_stale_charge`) is the existing stale-charge block moved verbatim and called from the budget gate. Net ≤ +110 formatted lines. | **E-Ag7Pw3 ordering rule:** an approval or human-gate check must run **before** call site (c). A hit must never satisfy or bypass an approval; G2 verifies this. A new `DispatchSignal` from E-Ag7Pw3 does not affect the lookup. **Any new success-side effect** added to `_settle_completed_task` must state whether it applies to hits (§8.7.4). |
| `src/agent_orchestrator/executors/claude_cli.py` | Extract `build_claude_argv(agent, prompt)` (behaviour-identical); `execute()` calls it. Pointer comment (T-bdQZW4). | If another epic changes argv construction, make the change **inside** `build_claude_argv`, and re-run U-A* and GV-1. |
| `src/agent_orchestrator/cli.py` | `--cache/--no-cache` on run/resume (last parameter); `_build_result_cache`; `add_typer(cache_app)`; summary lines; `report_usage` line | Additive; take both sides. |
| `src/agent_orchestrator/runstate.py` | About 6 lines in `write_status` (as built **+12/-1**; the `-1` retypes `snapshot` as `dict[str, object]`), with a lazy `cache.report` import only when `state.result_cache` is non-empty | Independent. |
| `src/agent_orchestrator/usage.py` | Both dispatch-counting sites skip current hits (real carried spend still counts); `UsageReport.result_cache`; payload omits it when None; lazy import | Independent. |
| `src/agent_orchestrator/outcomes.py` | `SettleReason` gains `"cached"`; `_settle_reason(ts, *, state=None, tid=None)`; lazy import | Independent. |
| `src/agent_orchestrator/ui/runs.py` | `TaskStat.result_cache`, `RunDetail.result_cache`, and the code that fills them (lazy import) | This epic touches **no** `ui/app.py` or `ui/service.py`. |
| `src/agent_orchestrator/ui/files.py` | About 6 lines (as built **+13**, with a casefolded comparison and a NUL guard): refuse paths under `<root>/.orchestrator/cache` | If E-Da5Tn9 touches `files.py`, take both sides; the deny-list is independent of auth. |
| `ui/src/types.ts`, `ui/src/components/RunDetail.tsx` | Interfaces; `cached` tag; "Result cache" tile | Small hunks. |
| `src/agent_orchestrator/ui/static/**` (committed bundle) | Rebuilt, in a **separate commit** (T-bLpoze) | **Never hand-merge.** Drop that commit if it conflicts; after all epics are merged, run `npm ci && npm run build` once. |
| `src/agent_orchestrator/bench/subjects.py` | `--no-cache` argv and `AO_CACHE=0` env | Independent. |
| `.github/workflows/ci.yml` | One additive step: result-cache tests with `--cov-fail-under=85` and a 90% floor per core module (T-JCOAsq) | Take both sides; steps are independent. |
| `tests/conftest.py`, `tests/test_nfr2_regression_gate.py` | **NOT edited** (NFR-2 gate) | — |

**As-built corrections to the table above (T-bdQZW4; gate G2-S3).** Measured with
`git diff --numstat 0b980e3..HEAD` before the T-bdQZW4 comment-only edits.

| File | +/- as built | Difference from the design |
|------|--------------|----------------------------|
| `models.py` | +81 / -1 | The `-1` is the pydantic import line. T-bdQZW4 adds a comment-only pointer next to `EFFORT_MAX_TURNS`. |
| `specs/workflow.schema.json` | +8 / -0 | As designed. |
| `project_config.py` | +71 / -1 | The `-1` is the pydantic import line; **new module-level import of `cache.constants`** (a leaf module). |
| `engine.py` | +135 / -30 (net **+105**) | One moved block (the stale-charge guard, -29 lines, verbatim into `_reverse_stale_charge`) and the `from typing import Literal` line rewritten to add `TYPE_CHECKING`. 12 added lines inside existing functions. |
| `executors/claude_cli.py` | no diff in `0b980e3..HEAD` | The `build_claude_argv` extraction (T-OeRYSO) is already in the merge base; T-bdQZW4 adds a comment-only pointer. |
| `cli.py` | +115 / -2 | The `-2`: the `BudgetSpec` import line gains `WorkflowSpec` (under `TYPE_CHECKING`), and **`status` changes `except (json.JSONDecodeError, KeyError)` to `except (ValueError, KeyError)`** (a total-parse fix, G1b S-2). **`cli.py` imports `agent_orchestrator.cache.cli` eagerly at module level** (`app.add_typer(cache_app, name="cache")`, DV-16). |
| `runstate.py` | +12 / -1 | Not "about 6": it also retypes `snapshot` as `dict[str, object]`. |
| `usage.py` | +109 / -23 | Additive in behaviour, not in text: site A's 20-line group-metrics block is re-indented under the current-hit guard; site B's `producer is None or producer.dispatch_cycle < 1` test gains `or pid in rc_hits`; the `typing` import gains `cast`. |
| `outcomes.py` | +12 / -3 | As designed. |
| `ui/runs.py` | +15 / -0 | As designed. |
| `ui/files.py` | +13 / -0 | Not "about 6" (+9 at T-bLpoze, a few more at G2); **new module-level import of `cache.constants`**. |
| `ui/src/types.ts`, `ui/src/components/RunDetail.tsx` | +41 / -0, +40 / -0 | As designed. |
| `ui/static/**` | 3 renamed hashed files, `index.html` +1/-1 | Generated bundle, separate commit `4e61e68`: drop and rebuild after merging sibling dashboard epics. |
| `bench/subjects.py` | +9 / -0 | **New module-level import of `cache.constants`**. |
| `.github/workflows/ci.yml` | +8 / -0 | One additive step. At merge keep the E-Da5Tn9 / T-2wE08U `permissions:` block and `pip-audit` job, and add `cli_ops restore_sweep safeio` to the per-module coverage loop (G2 sec N3). |
| **`tests/ui/test_run_graph_endpoint.py`** | +12 / -3 | **An existing test edited that the table above omitted.** It pins the exact key sets of the run payload, so `result_cache` was added to both. Merge guidance: take both sides; the key set must contain every sibling's key. |
| `tests/conftest.py`, `tests/test_nfr2_regression_gate.py` | no diff | Not edited. |

Also at merge: the E-2 allow-list of §8.7.5 and §18.1 names `cache.cli` (DV-16); a sibling that
loads another cache module at import time on the CLI path fails test E-2.

**Post-merge verification (parent).**

1. Full `pytest`: as built 6903 passed / 10 skipped / 0 failed at G2 (the 2 design-time bench failures no longer occur); expect no failures.
2. U-E1/U-E2 green after the approval field is classified.
3. `ruff` and `mypy` clean.
4. Rebuild the UI bundle and run vitest.
5. GV-1 and U-A* still green. A sibling's change to argv construction **is expected** to change
   GV-1: update GV-1 with the reason, and decide whether `KEY_SCHEMA_VERSION` must be bumped.
6. **Recapture the I-2 goldens** at the merge base if a sibling epic intentionally changed
   `status.json` or `ao run` output; record the reason in T-JCOAsq's `STATUS.md`.
7. U-E27 (`unknown_agent_field`) still green if a sibling added an `AgentSpec` field: classify it
   in `constants.AGENT_KEY_FIELDS` or `AGENT_NON_KEY_FIELDS` first (U-K8).

---

## 25. Post-implementation docs refresh (mandatory)

**Ticket:** `T-bdQZW4-cache-docs-refresh`. It runs last, after G2 PASS. Mark it Done only after every
statement has been confirmed against the implemented code.

**Status:** all ten steps were carried out; the evidence (the `grep` and run commands) is in the
ticket's `STATUS.md`. Architect sign-off: APPROVE-WITH-NOTES, 2026-10-05 (independent spot-check
and corrections recorded in the ticket's `STATUS.md`); `T-bdQZW4` is Done.

**Steps.**

1. **This HLD.** Add a "§0 Implementation outcome and deviations" section, following the
   `run-graph-canvas-hld.md` precedent. It must cover:
   - what shipped;
   - the resolved open questions (G0 status: protocol shipped; execution post-merge);
   - every deviation, with its reason;
   - follow-ups.

   Also re-verify GV-1 and the component digests against the code.
2. **ADR-0019.** Mark it Accepted, or add an addendum.
3. **`docs-md/hld-agent-orchestrator.md`.** Add:
   - the cache component (§2);
   - the NFR-1 carve-out (§4);
   - the lookup step (§5).
4. **`.claude/skills/workflow-authoring/SKILL.md`.** Add a "Result cache" section based on the §17
   outline: double opt-in and the `DEFAULT_TASK_CACHE_POLICY` flip point, when to opt in, guards,
   `skip_if_outputs_exist`, re-rolling with `ao cache rm`, pinned models, the residuals, and the
   `cache: true` example workflow from §17.
5. **`README.md`.** Add a "Result cache (opt-in)" section covering the flags, env, config, the
   shadow mode and `ao cache` commands, with a "not prompt caching" note and the release-note
   line from §16 (there is no changelog file). Link `docs-md/result-cache-g0-protocol.md`.
6. **`meta/ROADMAP.md`.** Add a "Just landed" entry, put the non-MVP list (§2.3) in §3.6, and note
   R-15 under §3.4.
7. **`docs-md/usage-analytics.md`** and **`docs-md/benchmarking-framework-hld.md`.** Document the
   `result_cache` usage object (§13.6), how current hits are excluded at both usage sites,
   `settle_reason: cached`, and that the bench always runs with `--no-cache`.
8. **Cross-links.** `docs-md/cost-caching-optimization-hld.md` gets the terminology box and a
   pointer. `docs-md/token-budgeting-hld.md` notes that a hit charges no budget and that stale
   charges are reversed.
9. **Pointer comments.** Next to `models.EFFORT_MAX_TURNS` and in `executors/claude_cli.py`, add a
   comment saying that a behaviour-relevant change not visible in argv or the fingerprint must bump
   `KEY_SCHEMA_VERSION`.
10. **Learnings.** Only if genuinely new; candidates:
    - the tripwire-plus-runtime-rule classification pattern;
    - prior-output key components;
    - "hostile cache data needs total parsers".
