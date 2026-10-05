# G2 final review: E-Rc4Hk8 cross-run result cache (HLD Rev 3, ADR-0019)

- Gate: G2 (T-fXWbqg), reviewer + dev-security delta, final
- Tree: worktree `agent-a18ce2c08e42a3a5a`, HEAD `370e9d8`, range `0b980e3..HEAD` (193 files, +29363/-440)
- Date: 2026-10-05
- Method: read-only review. No source, test, spec or ticket file was modified. Probe scripts live
  only in the session scratchpad (UI deny-list, `ao cache` CLI, workspace resolution).

## VERDICT: PASS (0 MUST-FIX, 6 SHOULD-FIX, 8 NIT)

The cache is off by default, double opt-in, and a cache-off run provably loads nothing beyond
`cache`, `cache.constants` and `cache.cli`. Every G2 criterion is met with evidence below. No
finding blocks the merge. The SEC-15 sensitive-path list is judged SHOULD-FIX, **not** MUST-FIX
(G2-S1, reasoning there); it is cheap enough that I recommend doing it before merge.

## Findings

| id | severity | file:line | issue | fix / verification |
|----|----------|-----------|-------|--------------------|
| G2-S1 | SHOULD-FIX | `src/agent_orchestrator/cache/constants.py:152-157`; HLD `docs-md/cross-run-result-cache-hld.md:619` (M-14); ADR-0019 D29 | SEC-15. The sensitive-path list covers `.git .claude .github .gitlab .husky .ao .orchestrator` and `CLAUDE.md CLAUDE.local.md AGENTS.md .mcp.json .envrc`. I probed `is_sensitive_rel_path`: `.gitlab-ci.yml`, `Jenkinsfile`, `.githooks/pre-commit`, `.circleci/config.yml`, `.pre-commit-config.yaml`, `.vscode/tasks.json`, `.devcontainer/devcontainer.json`, `.gitmodules`, `.gitattributes`, `.cursor/rules/x.mdc`, `azure-pipelines.yml` and `.travis.yml` are all **restorable**. HLD M-14 says the mitigation covers "CI config" generally, so claim and implementation differ. Not MUST-FIX because: (a) a destination must be a path the spec author declared as an output of a double-opted-in task; (b) forging an entry needs same-uid write access to `.orchestrator/cache`, which already gives direct workspace write (the accepted "deliberate poisoning" residual, banner note in `cli.py:_RESULT_CACHE_TRUST_NOTE`); (c) the agent that fills the entry could write the same file in an uncached run. So the refusal is defence in depth with no capability delta. | Extend now (about 12 literals plus 2 parametrised tests, and an ADR D29 addendum): components `.githooks .circleci .vscode .devcontainer .cursor .idea`; basenames `.gitlab-ci.yml Jenkinsfile .travis.yml azure-pipelines.yml bitbucket-pipelines.yml .pre-commit-config.yaml .gitmodules .gitattributes`. The list is shared by key build, restore, capture and `restore_sweep.safe_parent_dir`; the key schema and GV-1 do not read it (no golden workspace uses these names), so no `KEY_SCHEMA_VERSION` bump. Architect confirms the list (ADR D29 decision). If not done before merge: name it in HLD 7.7 as a residual (T-bdQZW4). Verify with `tests/cache/test_safeio.py` sensitive table and `test_restore.py` case tests. |
| G2-S2 | SHOULD-FIX | `src/agent_orchestrator/ui/files.py:206-210` | M-10 deny-list compares `resolved` to `cache_root` by exact path components. Verified refused on Linux: direct path, `..` traversal, absolute path, `.//` forms, symlink to the dir, symlink to a blob, htmlpreview of both (see probe in Commands). On a case-insensitive filesystem (macOS, Windows) `.Orchestrator/Cache/...` resolves to the same directory without matching the lowercase constant; `Path.resolve()` does not canonicalise case there. The same epic treats case-insensitive filesystems as in scope for the sensitive list (SEC-06, `casefold()`). Impact is low (the dashboard is authenticated and the blobs equal declared outputs), but the guard is inconsistent. | Compare with `os.path.normcase` on both sides, or test `os.path.samefile` on the ancestors of `resolved` against `cache_root`. Add one test using a monkeypatched `normcase`. |
| G2-S3 | SHOULD-FIX | `docs-md/cross-run-result-cache-hld.md` section 24.2 (rows `runstate.py`, `ui/files.py`, `cli.py`, tests row); T-bdQZW4 carry-over | The merge notes do not match the diff in these places (full table below): `tests/ui/test_run_graph_endpoint.py` was edited (+12/-3) and is not listed; `cli.py` `status` also changes `except (json.JSONDecodeError, KeyError)` to `except (ValueError, KeyError)`; `runstate.py` is +12/-1, not "about 6 lines" (it also retypes `snapshot`); `ui/files.py` is +9, not "about 6"; the new module-level imports of `cache.constants` in `project_config.py`, `bench/subjects.py` and `ui/files.py` are not mentioned; `cache.cli` is loaded by every `ao` start (HLD 8.7.5 / 18.1 E-2 list omits it). | Add these to T-bdQZW4's list (its STATUS already carries the G1b items; it lacks the ones here). The sibling-epic merger needs the `test_run_graph_endpoint.py` row: "take both sides; the key set must contain every sibling's key". |
| G2-S4 | SHOULD-FIX | `src/agent_orchestrator/cache/fingerprint.py:39-47`, `:173-174` | SEC-11 (deferred at G1a). The env allowlist holds only model-selection variables. `ANTHROPIC_BASE_URL`, `CLAUDE_CODE_USE_BEDROCK` / `CLAUDE_CODE_USE_VERTEX` and provider region are not keyed, so a hit can be served across a backend switch with an unchanged model alias. Low impact (same model name), but it is a stated "semantic stale hit" (M-11) vector and cheap to decide while no entry exists in the wild. | Architect decision before merge: add the names (changes GV-1; bump `KEY_SCHEMA_VERSION` is unnecessary pre-release, just recapture GV-1), or document as a named residual in HLD 7.7 and the authoring guide (T-bdQZW4). |
| G2-S5 | SHOULD-FIX | `src/agent_orchestrator/cache/cli_ops.py:143-154`, `cache/cli.py:19-21` | `ao cache <cmd> --workspace /typo` exits 0 and prints "(result cache is empty ...)"; `ao cache clear --yes --workspace /typo` exits 0 with "cleared 0 entries". A mistyped path makes a destructive or verifying command look successful. Nothing is created (verified: no cache directory appears). Also the help text says the default workspace comes from "the project config's workspace", but the shared resolver (`cli._resolve_workspace_root`) uses `AO_WORKSPACE_ROOT` or the workflow/reposets triplet; `workspace_root:` in `.ao/config.yaml` is not read (probe: exit 1 "--workflow is required"). | In `_resolve_workspace`, exit 1 with "workspace does not exist" when the path is not a directory (read-only commands may keep exit 0 for a missing `.orchestrator/cache`, which is a different case). Fix the help string. Add one e2e test. |
| G2-S6 | SHOULD-FIX | T-fXWbqg `STATUS.md:88` (G1b verification line); epic `STATUS.md` gate tracker | Inconsistent evidence record. G1b recorded a full suite of "7480 passed, 13 skipped"; G1a recorded 6081/10; T-JCOAsq and the brief record 6805/10. `pytest --collect-only` on this HEAD collects **6815** tests (= 6805 + 10), so 7480 cannot be reproduced and was never explained ("not investigated further"). The last src/tests/ci change is `a947986` (`git diff a947986 HEAD -- src tests .github` is empty), so 6805/10 is current. | Correct the G1b figure in `STATUS.md` when mirroring this verdict (likely a run that also collected nested worktree copies of `tests/`); do not re-run the 12-minute suite for this. |
| G2-N1 | NIT | `src/agent_orchestrator/cli.py:1160-1168` and `:1468-1476` | The `--cache/--no-cache` Option (10-line help) is declared twice verbatim, while `cache/cli.py` already shares `_WORKSPACE` / `_AS_JSON` option objects. | One module-level `_CACHE_OPTION` constant used by `run` and `resume` (CLAUDE.md DRY rule). |
| G2-N2 | NIT | `src/agent_orchestrator/cache/cli_ops.py:80` | `_WORKSPACE_ENV = "AO_WORKSPACE_ROOT"` is unused (the resolver reads the env itself). | Delete it. |
| G2-N3 | NIT | `src/agent_orchestrator/models.py:35` vs `cache/constants.py:47`; `usage.py` `_sum_result_cache` | `RESULT_CACHE_KEY_PATTERN` repeats `SHA256_HEX_RE` (pinned by `test_g1a_remediation_misc`, so drift is caught); `_sum_result_cache` uses a `dict[str, object]` plus `cast` instead of a typed counters model. | Optional: build the pydantic `pattern` from `SHA256_HEX_RE.pattern` (G1a S-10 deferral); a small `TypedDict` for the counters. |
| G2-N4 | NIT | `src/agent_orchestrator/engine.py:1219` | The approval-ordering rule is one comment line plus the merge note; no test fails if a future gate is inserted after call site (c). The field tripwires (U-E1/U-E2) catch the new field, not the gate position. | Optional pin test: AST-assert that nothing named like a gate sits between the comment and the `_estimate = 0` line, or simply that the comment text is still directly above the lookup call. |
| G2-N5 | NIT | `src/agent_orchestrator/cache/fingerprint.py:119-130` | `claude --version` runs with inherited stdin and environment and an unbounded `capture_output` (G1a SEC-10, deferred). The binary already runs at dispatch; low value. | Optional: `stdin=subprocess.DEVNULL` and a bounded read. |
| G2-N6 | NIT | `src/agent_orchestrator/cache/cli_ops.py:84-85` | Column widths `<12`, `<25`, `>3`, `>10` in `_LS_HEADER` are inline literals. | Name them next to `KEY_DISPLAY_CHARS`, or ignore (display only). |
| G2-N7 | NIT | `.github/workflows/ci.yml` (existing steps); `output/E-YAAGhk-overseer-runner-template/repro_emit_lost_on_breaker_trip.py:3`; `src/agent_orchestrator/_version.py:24-27` | Pre-existing, not from this epic (none of these files is in the diff): `ruff check .` reports I001 on the `output/E-YAAGhk` repro script, `ruff format --check .` flags the same file, and `mypy src` reports 4 errors in the tracked `_version.py`. The existing lint and type-check CI steps are red at base for these reasons. `ruff check src tests`, `ruff format --check src tests` and `mypy src tests/cache` are clean for everything this epic touched. | Out of scope; flag to the parent before relying on a green CI at merge. |
| G2-N8 | NIT | HLD 18 "Baseline: 5041 passed / 2 known bench failures" | Current full suite is 6805 passed / 10 skipped / 0 failed; the "2 known bench failures" are no longer observed. | Refresh the baseline sentence in the docs refresh. |

## Criteria evidence

### (1) Full-suite numbers and the CI coverage step

| check | result |
|-------|--------|
| Last full suite (T-JCOAsq Part 3, still valid: `git diff a947986 HEAD -- src tests .github` is empty) | 6805 passed, 10 skipped, 0 failed; `pytest --collect-only` on HEAD: **6815 tests collected** (matches) |
| `.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache tests/test_spawn_provenance.py tests/test_nfr2_regression_gate.py tests/ui` | **2062 passed, 2 skipped**, 0 failed (37.7 s) |
| CI coverage step, exactly as in `ci.yml` (`tests/cache` + the two e2e files, `--cov=agent_orchestrator.cache --cov-fail-under=85`) | **1620 passed**; package coverage **98.71%** (2644 stmts, 34 missed); required 85% reached |
| Core modules, `coverage report --include=*/agent_orchestrator/cache/<m>.py --fail-under=90` | keys **100%**, store **96%**, restore **97%**, coordinator **100%**; all four pass |
| `pytest tests/bench/test_bench_cache_forced_off.py tests/test_engine_budget.py` | 26 passed (budget tests unedited, so the `_reverse_stale_charge` move is behaviour-identical) |
| `vitest run src/test/run-detail-result-cache.test.tsx` | 10 passed |
| UI bundle reproducibility | `vite build --outDir <scratch>` produced `index-B2cn_9HF.js`, `RunGraph-DYQ5dEzN.js`, `index-CQm5Jase.css`, `RunGraph-CeJeskZg.css`: identical hashed names to the committed bundle |
| Python 3.11 syntax (CI uses 3.11, local venv 3.12) | `/usr/bin/python3.11` `ast.parse` over all `src/` and `tests/` files: 0 syntax errors |
| `ruff check src tests`, `ruff format --check src tests`, `mypy src tests/cache` | clean, clean, only the 4 pre-existing `_version.py` errors (an ignored generated `_build_info.py` also trips format locally; it is untracked) |
| Engine budget re-measure | `git diff --numstat 0b980e3..HEAD -- engine.py` = 135 added / 30 removed (net **+105**, budget +110); added lines inside existing functions: ctor param 1 + ctor body 3 + call site (c) 5 + call site (d) 2 + the one-line `_reverse_stale_charge` call 1 = **12** (budget 12); no added line over 100 columns |

### (2) Merge notes (HLD 24.2) against the diff, file by file

I re-measured each row with `git diff --numstat 0b980e3..HEAD` and read each hunk. The T-JCOAsq HANDOFF table is accurate for line counts. Deviations and my judgement:

| file | HLD 24.2 claim | actual | judgement |
|------|----------------|--------|-----------|
| `models.py` | imports, `RESULT_CACHE_*` constants, `TaskSpec.cache` after `verdict_path`, `WorkflowDefaults.cache` after `model`, `ResultCacheRecord`, `RunState.result_cache` after `record_git_heads`, `is_current_result_cache_record`; pointer comment later | +81/-1, all as listed; pointer comment not yet added | Match. Pointer comment is T-bdQZW4 work. |
| `specs/workflow.schema.json` | `cache` in `defaults` and `$defs.task` | +8/-0, both places | Match. |
| `project_config.py` | `CacheConfig`, `ProjectConfig.cache`, template block | +71/-1; also a **module-level** `from .cache.constants import ...` | Match; the new import edge is allowed (`constants` is a leaf, E-2 allow-list) but unlisted (G2-S3). |
| `engine.py` | TYPE_CHECKING imports, ctor kwarg, `_RunContext.result_cache_pending`, site (c) before `_estimate = 0`, site (d) top of the `succeeded` branch, three private methods, net <= +110 | exactly that; net +105; ctor kwarg is last (after `summarizer`); `injected` derived from `spawned_by` | Match. Both deviations are recorded in HLD 8.7.1's implementation note and are sound (`test_spawn_provenance.py` forbids `.origin` reads under `src/`). |
| `executors/claude_cli.py` | extract `build_claude_argv`; pointer comment later | **no diff in range** (extraction `ac35e73` is in the base `0b980e3`); `keys.py:53` imports it | Match in substance. Pointer comment pending (T-bdQZW4). |
| `cli.py` | flags last parameter, `_build_result_cache`, `add_typer`, summary lines, `report_usage` line | +115/-2; all present; flags are the last parameter on `run` and `resume`. **Unlisted:** `status` fallback `except (ValueError, KeyError)` (G1b sec S-2 fix, comment in place); `_echo_result_cache_line`; `cache.cli` imported eagerly at module level | Benign deviation, intentional. Add to notes (G2-S3). |
| `runstate.py` | about 6 lines, lazy import when `state.result_cache` is non-empty | +12/-1 (adds `snapshot: dict[str, object]` annotation and a trailing `if rc_run is not None`) | Benign; "about 6" understates. Lazy condition as claimed. |
| `usage.py` | both dispatch-counting sites skip current hits (carried spend still counts), `UsageReport.result_cache`, payload omits when None, lazy import | +109/-23; site A group block re-indented under the guard, site B adds `or pid in rc_hits`; `ResultCacheUsage` model; payload `del` when None; lazy `cache.report` imports | Match. Re-indent makes the diff look like churn but the behaviour is additive (I-20 and usage tests cover hit-after-spend). |
| `outcomes.py` | `SettleReason` gains `"cached"`, `_settle_reason(ts, *, state, tid)`, lazy import | as stated | Match. |
| `ui/runs.py` | `TaskStat.result_cache`, `RunDetail.result_cache`, fill code, lazy import; no `ui/app.py` / `ui/service.py` | +15/-0; neither `app.py` nor `service.py` is in the diff | Match. |
| `ui/files.py` | about 6 lines, refuse `<root>/.orchestrator/cache` | +9/-0 (adds the `cache.constants` import) | Match; see G2-S2 for the case-folding gap. |
| `ui/src/types.ts`, `RunDetail.tsx` | interfaces, `cached` tag, "Result cache" tile | +41 and +40, as stated | Match. |
| `ui/static/**` | rebuilt in a separate commit | `4e61e68` touches only the 3 static files; the source commit `faf8f57` has none | Match. |
| `bench/subjects.py` | `--no-cache` argv, `AO_CACHE=0` env | +9/-0; both present | Match. |
| `.github/workflows/ci.yml` | one additive step | +8/-0, byte-identical to HLD 18 | Match. |
| `tests/conftest.py`, `tests/test_nfr2_regression_gate.py` | NOT edited | no diff | Match. |
| *not listed:* `tests/ui/test_run_graph_endpoint.py` | (absent) | +12/-3: adds `result_cache` to two exact key-set assertions | Necessary and additive (the test pins exact payload keys). Must be added to 24.2 with "take both sides" guidance (G2-S3). |
| *not listed:* `pyproject.toml` | (absent) | untouched; the hatch wheel config packages `src/agent_orchestrator` recursively, so `cache/` ships | Match. |

### (3) Approval ordering

E-Ag7Pw3 code is **not** in this tree (`grep -ri approval src/` finds only the tripwire message and the seam comment). In place: the seam comment at `engine.py:1219` ("Approval/human gates (E-Ag7Pw3) run BEFORE this"), the rule in the `_result_cache_lookup` docstring, the merge checklist (HLD 24.2 engine row, D24, T-XpF1pF `HANDOFF.md` "For the approval-gate merge"), the tripwire message `TASK_TRIPWIRE_MESSAGE` ("an approval/human-gate field is ALWAYS RULED", pinned by `test_eligibility.py:87`), and the runtime unknown-field rules.

Walk of `_prepare_and_maybe_dispatch` (engine.py 1015-1450): before (c) run not_taken, `done`, `should_skip`, worktree activation/ensure, `apply_join`, missing-inputs and dynamic inputs; (c) follows them. After (c): budget gate, quota wait, mark running. A hit skips only what the HLD says it skips: the budget gate and quota (a hit costs nothing; `_reverse_stale_charge` still releases a crash-stale charge), worker dispatch, and `_settle_completed_task`. Checks that exist in this tree and could matter are covered by eligibility rather than by the engine: `pre_hook` / `post_hook`, `emit_tasks`, `output_manifest`, `task_manifest_path`, worktree isolation and `integration.active`, router and loop members, breaker verdict sources, `verdict_path` all make the task ineligible (`cache/eligibility.py:117-130, 208-274`), so none can be skipped by a hit. A hit's `ts.outputs_present` and hash-verified restore replace the post-run output check. Verdict: nothing in the engine lets a hit skip an existing check. G2-N4 suggests a cheap pin test.

### (4) `ao cache` argument and prefix handling (CliRunner probe, 40+ cases)

- Prefix: `KEY_PREFIX_RE = [0-9a-f]{4,64}` applied with `fullmatch` before any path is built. Rejected with exit 2 and no I/O: empty, 3 chars, uppercase, non-hex, `../../etc`, `abcd/..`, trailing newline, NUL, ESC sequences, space, Arabic-Indic digits, `%s%s`, 65 chars. `-abcd` and `--help` are Click options (exit 2 / exit 0 help); a leading-dash prefix after `--` is rejected as malformed.
- A well-formed unknown prefix: exit 1 (`no cache entry matches`); ambiguous prefix: exit 1, candidates capped at `CLI_MAX_CANDIDATES`, scan capped at `CLI_MAX_DIR_ITEMS`; only the one shard directory is listed; entries must be regular non-link files (`test_a_symlinked_entry_is_never_followed`).
- Numeric options: `--limit 0/-1/10^20` exit 2 (bounded by `CLI_MAX_LIMIT`); `--sort` validated; `prune --older-than -1 / 99999999` exit 2; non-integer rejected by Click (exit 2).
- `clear` without `--yes` refuses on non-TTY and under `--json` (exit 1, still one JSON document); `verify` read-only, exit 1 on problems.
- Output sanitisation: every entry-derived and user-supplied string passes `safeio.strip_control_chars` (C0/C1, DEL, bidi and zero-width) and is clipped to 1024 chars; error text for the bad prefix shows `abcd[31m` (ESC stripped); `--json` is `json.dumps` escaped; `--json` always emits exactly one document, also on failure (`error` field). One reporting point (`_guard`), no layered logging.
- Exit codes: 0 ok, 1 not found / ambiguous / store problem / refused, 2 bad arguments. Consistent with the module contract.
- Prune sweep (`restore_sweep.py`): paths come from entries (hostile), so each is re-validated (plain normalised relative path, no `..`, not sensitive), the chain is `check_dir_chain`-ed, the directory is opened `O_NOFOLLOW` and files are lstat/unlinked through the descriptor; only regular, own, old (ctime) files named exactly like a restore staging file; a sole-copy `.bak` is kept. Residual is the documented race on intermediate components. Finding: G2-S5 (typo'd workspace reported as success).

### (5) Dashboard text-only rendering, deny-list, bundle commit

- `RunDetail.tsx:64-99`: `ResultCacheTag` renders a fixed `cached` label shown only for `rc.hit === true`; the untrusted `source_run_id` goes into a plain `title` attribute. `ResultCacheTile` renders numbers through `formatCost` and template strings. No `dangerouslySetInnerHTML`, `innerHTML`, `href` or `src` is introduced (grep). Types carry the "untrusted text" warning. Vitest: 10 passed (hostile `source_run_id` cases included).
- Deny-list probe against a real `FileBrowser` (Linux): `list_dir` and `read_file` refuse `.orchestrator/cache`, `.orchestrator/cache/blobs/secret.txt`, a symlink to the directory (`link`, `link/blobs/secret.txt`), a symlink to a blob (`flink`), `dotdot/../.orchestrator/cache/...`, the absolute path, `.orchestrator/./cache`, `.orchestrator//cache/blobs`; `build_html_preview` refuses both `.orchestrator/cache/index.html` and `link/index.html`. Listing `.orchestrator` shows only the name `cache` (no content). Case variant on a case-insensitive filesystem: G2-S2. The service builds a single workspace root per `ui` instance (`ui/service.py:268`), so the root-relative constant matches the real cache location.
- Bundle: `git log --stat -- src/agent_orchestrator/ui/static` shows exactly one commit, `4e61e68` ("build(ui): rebuild dashboard bundle ..."), 3 files, and no source file; `faf8f57` (UI source and deny-list) is a different commit. The bundle rebuilds byte-name-identically.

### (6) G0 protocol document

`docs-md/result-cache-g0-protocol.md` exists (290 lines). Its opening paragraph states that executing G0 is a post-merge follow-up, "it does not claim that G0 has been run, and nothing in this repository contains G0 results"; the smoke file `output/.../g0-protocol-smoke.md` is described as tooling validation ("proves every command below works, not that the cache is valuable"). `tests/cache/test_g0_protocol_doc.py` guards the doc. Pass.

### (7) Bench off, kill switches

- `bench/subjects.py`: `_AO_NO_CACHE_FLAG = "--no-cache"` appended to the `uv run ao run` argv, and `env[ENV_CACHE] = "0"` after `dict(os.environ)` (overrides an operator's `AO_CACHE`). `AoWorkflowSubject` is the only bench code path that shells out to `ao` (grep). `tests/bench/test_bench_cache_forced_off.py` (E-8a..c) passes, including an operator environment that tries to enable the cache.
- Default off: `resolve_result_cache_settings` precedence is `--cache/--no-cache` > `AO_CACHE` (1/true/yes/on, 0/false/no/off, shadow; any other value fails closed to off with a warning) > `.ao/config.yaml cache.enabled` > off. `--no-cache` wins over env and config (E-3, 5 cases); the author layer is `task.cache > defaults.cache > DEFAULT_TASK_CACHE_POLICY (False)` and an `emit_tasks`-injected `True` is ignored. `StrictBool` on every opt-in field. The on-mode banner carries the trust note.

### (8) SEC-15: see G2-S1 (SHOULD-FIX, not MUST-FIX; recommend extending now)

### (9) Whole-epic design conformance

- **Pluggability and DI.** `ResultCacheHook`, `CacheStore` and `CacheAdmin` are contracts in `cache/types.py` with fakes; the engine takes the hook by constructor and never imports a cache module on the off path. `ao cache` goes through the store API only.
- **Lazy imports (NFR-1).** `python -c "import agent_orchestrator.cli, ...ui.files, ...usage, ...outcomes"` loads exactly `agent_orchestrator.cache`, `agent_orchestrator.cache.cli`, `agent_orchestrator.cache.constants`, which is the E-2 allow-list {cache, constants, settings, cli} (settings loads only on `ao run/resume`). `cache/cli.py` imports only `typer` and `constants` at module level; `cli_ops` is imported inside each command. `runstate.py`, `usage.py`, `outcomes.py`, `ui/runs.py` and `cli.py` import `cache.report` lazily and only when `state.result_cache` is non-empty. I-1 (poisoned finder) and I-2 (goldens) are green.
- **Determinism.** No direct clock or RNG in the cache logic: `now` is injected into lookup/store/prune; `cache/cli.py:_now` is a single patchable seam; `uuid4` and `getpid` appear only in temp/trash names (`store.py:350,476,941`, `restore.py:226`), which never affect content or keys.
- **Error handling.** One authoritative report point in the CLI (`_guard`); the engine boundary turns any coordinator exception into a disabled cache for the run (M-16, I-25).
- **DRY / literals.** Shared helpers are single-sourced (`clip_text`, `RESOLVE_FAILURES` / `try_resolve`, `is_restore_tmp_name`, `check_dir_chain`, `strip_control_chars`, `_reverse_stale_charge`, `is_current_result_cache_record`); constants are named in `cache/constants.py` and `models.py`. Residual duplicates and literals are NITs G2-N1, N2, N3, N6; `CacheConfig.mode` uses a literal default with a pinning test (documented in the code).
- **Concurrency.** Lookup and store run on the main thread; `max_parallel` is exercised by I-5b and I-25; same-key concurrent stores are covered.
- **Resume.** A hit settles like the `should_skip` branch (done + save), keeps the `dispatch_cycle` increment (R-21), releases a stale charge, and is a current record only while `ended_at` is bound; `ao resume --cache` is covered by E-6.

### (10) Deferred items across all gates: none blocks the merge

| item | judgement |
|------|-----------|
| G1a SEC-15 sensitive-path list | SHOULD-FIX G2-S1 (recommend now) |
| G1a SEC-11 provider env in key | SHOULD-FIX G2-S4 (decide or document) |
| G1a SEC-10 `claude --version` bounds | NIT G2-N5 |
| G1a SEC-13 strict pydantic models, SEC-16 mode 0o000, SEC-17 `ClearReport` count through a symlinked `blobs/`, SEC-18 future `created_at`, SEC-20, SEC-21, S-6 inline thrash, S-9 attribute-call AST checks (no `Path.open` / `read_bytes` / `io.open` exists in `cache/`, grep clean) | Same-uid or reporting-only; accepted residuals; do not block. Record in the docs refresh. |
| G1a SEC-14 bidi stripping, SEC-19 restore temp sweep | **Done** (`_CONTROL_CHARS_RE`; `restore_sweep.py` plus `ao cache prune`) |
| G1b sec S-3 (`filter.<x>.clean` run by the guard-3 probe), N-7 / N-9 (guard-3 blind spots, partial-attempt baselines), N-10 (approval ordering) | Accepted residuals with a stated precondition (an in-`.git` write by a task agent); the double opt-in, the banner trust note and `--no-cache` for untrusted repos cover them. **T-bdQZW4 must list them** (already in its STATUS carry-over). Approval ordering is verified at (3). |
| G1b rev S-5 / sec S-4 dashboard deny-list; I-5b / I-25 tests; prune-side tmp sweep | **Done** (T-bLpoze, T-JCOAsq Part 2, T-6tRKml) |
| G1b rev N-2 (R-1b rationale lost from `_reverse_stale_charge` docstring) | Cosmetic; the engine budget is net +105 of +110. Optional ~5-line docstring. |
| `ao cache` deferred commands (`rm --run/--task`, `verify --repair`, `refresh`) | Non-MVP by design (HLD 2.3); not exposed (absent from help). |
| G0 execution | Post-merge business decision, not a build blocker (D34); protocol and tooling shipped. |

### (11) Acceptance criterion 4 (traceability)

The epic `STATUS.md` gate tracker still reads "G2 ready to start" and T-fXWbqg is `Draft`. Mirroring this verdict into T-fXWbqg `STATUS.md` and the epic `STATUS.md` (same day) is the manager's step; correct the G1b suite figure while doing it (G2-S6). Nothing was edited here.

## Commands run and results

```text
git diff 0b980e3..HEAD --stat | tail -1
  193 files changed, 29363 insertions(+), 440 deletions(-)
.venv/bin/python -m pytest --collect-only -q -p no:cacheprovider | tail -1
  6815 tests collected
.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache tests/test_spawn_provenance.py \
    tests/test_nfr2_regression_gate.py tests/ui
  2062 passed, 2 skipped, 1 warning in 37.68s
.venv/bin/python -m pytest -p no:cacheprovider tests/cache tests/test_e2e_cli_result_cache.py \
    tests/test_e2e_cli_result_cache_admin.py -q --cov=agent_orchestrator.cache \
    --cov-report=term --cov-fail-under=85 -rs
  1620 passed in 49.71s; TOTAL 2644 stmts, 34 miss, 98.71%; "Required test coverage of 85% reached"
.venv/bin/python -m coverage report --include="*/agent_orchestrator/cache/<m>.py" --fail-under=90
  keys 100% (92/0) | store 96% (620/25) | restore 97% (150/4) | coordinator 100% (260/0)  -> all pass
.venv/bin/python -m pytest -q -p no:cacheprovider tests/bench/test_bench_cache_forced_off.py tests/test_engine_budget.py
  26 passed
ui/node_modules/.bin/vitest run src/test/run-detail-result-cache.test.tsx     10 passed
ui/node_modules/.bin/vite build --outDir <scratchpad>/bundle                  hashed names equal the committed bundle
.venv/bin/ruff check src tests            All checks passed
.venv/bin/ruff format --check src tests   344 files formatted, 1 untracked generated file (_build_info.py, git-ignored)
.venv/bin/ruff check . / format --check . 1 pre-existing issue in output/E-YAAGhk-overseer-runner-template/ (not in diff)
.venv/bin/mypy src tests/cache            only the 4 pre-existing _version.py errors
/usr/bin/python3.11 ast.parse over src/ + tests/                              0 syntax errors
git log --stat --format='%h %s' 0b980e3..HEAD -- src/agent_orchestrator/ui/static
  4e61e68 build(ui): rebuild dashboard bundle ... (3 files, nothing else)
git diff a947986 HEAD -- src tests .github                                    (empty: last full suite is current)
git diff --numstat 0b980e3..HEAD -- src/agent_orchestrator/engine.py          135  30  (net +105)
probe: FileBrowser deny-list (list_dir, read_file, htmlpreview x 10 path forms)      all refused (Linux)
probe: ao cache show|rm|ls|prune|clear|verify with 40+ hostile arguments             exit codes 0/1/2 as designed
probe: import agent_orchestrator.cli (+ui.files, usage, outcomes) -> cache modules   {cache, cache.cli, cache.constants}
probe: is_sensitive_rel_path over 17 CI/hook sink names                              12 restorable (G2-S1)
```

## Pre-submit checklist

- [x] Scope confirmed: everything in `git diff 0b980e3..HEAD` (src, tests, specs, ci.yml, G0 doc, ui, bundle), HEAD `370e9d8`.
- [x] Three alignment levels: project goals (declarative, DAG-first, pluggable, deterministic, observable, safe by default), epic goals (ticket TASK/STATUS read; G2 criteria 1-4 each answered above), code-level intent (docstrings and comments match behaviour; the two inaccurate statements are the `cache/cli.py` workspace help and HLD 24.2 "about N lines").
- [x] Dimensions walked: SOLID/KISS (store, coordinator, ops/cli split, no over-abstraction found), DRY (N1-N3), magic literals (N6), pluggability (hook, store, admin contracts), spec/DAG correctness (StrictBool schema, eligibility allowlist, cycles untouched), determinism and resume (injected clock, R-21, hit settle), errors and logging (single boundary report), testability (seams: `cli._now`, `_stdin_is_tty`, fakes), concurrency and rollout (main-thread lookup, additive schema, `result_cache: {}` default keeps old `state.json` loadable).
- [x] Every finding cites location, observation, why it matters and a concrete next step.
- [x] Bucketing: 0 MUST-FIX (no merge-blocking correctness, data-loss, resume or security defect found); SEC-15 argued SHOULD-FIX.
- [x] Testing notes below.
- [x] No source, test, spec or ticket file modified.

## Testing notes

- **Mock seams in use and sufficient:** `cache.cli._now`, `cli_ops._stdin_is_tty`, the fake store and fake hook in `cache/fakes`, injected `now` and `environ` in the coordinator, `HookedExecutor` / `SteppingClock` in `_hardening_rig.py`.
- **Integration coverage that exists:** I-1/I-2 no-op proof against base goldens, I-5b store at `max_parallel=3`, I-12 integration commits identical, I-20 quota requeue then hit, I-25 coordinator failure boundary, ADV-1..10 hostile filesystem and corpus, e2e E-1..E-7 through `CliRunner`.
- **Gaps to add (all small):** (a) a sensitive-name table test for the G2-S1 additions; (b) a case-variant deny-list test for G2-S2 (monkeypatch `normcase`); (c) an e2e test that `ao cache clear --yes --workspace <missing>` exits 1 (G2-S5); (d) optional pin test for the approval-seam comment (G2-N4); (e) when E-Ag7Pw3 merges, run U-E1/U-E2 and add a test that a gated task is never served from the cache (the one test that cannot exist before that code does).
- **Not verifiable on this Linux host:** case-insensitive filesystem behaviour (G2-S2) and Windows (`os.mkfifo`, symlink tests skip by design with explicit markers).
