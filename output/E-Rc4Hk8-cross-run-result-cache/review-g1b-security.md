# G1b security review: E-Rc4Hk8 cross-run result cache (engine set)

- Reviewer: dev-security (Gate G1b, security pass). Date: 2026-10-05.
- Scope: `git diff 9f9a400..HEAD -- src tests specs` at HEAD `62d14c2` (worktree
  `.claude/worktrees/agent-a18ce2c08e42a3a5a`). Code: `cache/coordinator.py`, `cache/records.py`, `cache/report.py`,
  `engine.py` seams, `runstate.py` / `usage.py` / `outcomes.py` edits, `cli.py` wiring (`--cache/--no-cache`, `AO_CACHE`,
  project config, summary lines), `bench/subjects.py` force-off.
- Baseline: HLD `docs-md/cross-run-result-cache-hld.md` section 7.7 (M-1..M-16), 8.6, 8.7, 8.8, 8.9; ADR-0019 (+ addenda);
  ADR-0020 cross-epic note (cache must never settle, restore or skip an approval gate); G1a reports and their deferred items.
- Threat model used: the HLD's own. The cache dir, `state.json`, `status.json` and `.git` are agent-writable (same uid).
  Deliberate same-uid poisoning and active TOCTOU races are accepted residuals, reported only where the PoC adds information.
- No source or test file was modified. All adversarial scripts live in the scratchpad
  (`/tmp/claude-1000/-usr-avadhoot-mounted-agent-orchestrator/c3bbc271-ec14-4ba6-9c94-0ca585be9938/scratchpad/`: `a1.py` .. `l1.py`).

## Verdict: **PASS** (0 MUST-FIX)

Counts: MUST-FIX 0, SHOULD-FIX 5, NIT 10.

Posture: a cache hit cannot bypass an approval, verdict, budget-spend, hook or breaker evaluation that exists in this repo today.
The coordinator error boundary, the kill switch, shadow mode and the unsafe-path no-evict rule all held under adversarial tests.
Restore never wrote outside the spec-declared outputs. The weak spots are all low-impact: a crash-orphaned restore temp file in the
workspace, unvalidated `status.json` parsing in `ao status`, a git `filter.*.clean` execution path in the guard-3 probe, the dashboard
half of M-10 not being implemented yet (G2), and the already-accepted forged-entry residual, which is demonstrable and currently
unannounced to the operator.

Top risks (all SHOULD-FIX): S-1 orphaned restore temp files, S-2 `ao status` totality, S-3 git filter execution in the probe,
S-4 dashboard still serves `.orchestrator/cache` (G2 hard gate), S-5 forged-entry hit with no operator-facing warning.

## Findings

| id | severity | file:line | issue | fix / verification |
|----|----------|-----------|-------|--------------------|
| S-1 | SHOULD-FIX | `cache/restore.py:61` (comment claims "every restore sweep also finds it"), `:120-127` (`_backup_existing`), `:222-224` (staging name) | **A crash mid-restore leaves a full copy of a cached output in the workspace forever.** Staging files `.ao-result-cache-<pid>-<tid>-<uuid>.tmp` (and `.tmp.bak` hard links) are created next to the destination, in the user's output dir, not in the 0700 cache root. They are only removed by the `finally` block, which a SIGKILL / power loss skips; there is no sweep anywhere (`grep RESTORE_TMP_PREFIX src` shows one use). PoC `e1.py`: child process `os._exit(9)` after the first rename of a 2-output restore -> workspace has `a.md` restored, `b.md` absent, plus the `.tmp` orphan; `ao resume` then ran the task as a miss (safe) and the orphan was still there, and a downstream task with `inputs: ["out"]` hashed the orphan into its key and stored an entry over it. Impact: (1) secret retention outside the root-mode boundary (M-10), the orphan sits in a dir that `git add -A` may commit; (2) nondeterministic downstream keys and junk in a dir input; (3) the code comment promises a sweep that does not exist. | Sweep stale `RESTORE_TMP_PREFIX*` (and `.bak`) siblings (regular, owned, older than a grace) in each output parent before staging, and/or skip that prefix in `hashing.digest_path` directory walks. Add a test: kill-style leftover is removed by the next restore and never enters a dir-input digest. Fix the comment. |
| S-2 | SHOULD-FIX | `cli.py:334` + `:434-446` (`_echo_result_cache_line`), `cache/report.py:115-126` (`format_summary_line`), `cli.py:1720` (`except (JSONDecodeError, KeyError)`) | **`ao status` reads the `result_cache` block of `status.json` without validation.** `status.json` is an agent-writable file; every other cache read side goes through the validated `ResultCacheRecord` model, this one does not. PoC `b1.py`: block `"pwn"` or `[1,2]` -> uncaught `TypeError`, exit 1; numbers replaced by strings -> `ValueError: Unknown format code 'f'`; `saved_cost_usd: null` -> `TypeError`; `hits: "\x1b]0;PWNED\x07\x1b[2J"` -> the escape sequence is echoed verbatim to the operator's terminal (CWE-150, the M-15 class). A missing key falls back to `state.json` correctly (KeyError caught). Pre-existing fields in `_print_status_snapshot` have the same fragility, so this is not a regression, but HLD 8.8 relies on these parsers, and the new line adds an escape-injection sink. | Make the formatter total: `isinstance(block, dict)`, coerce with `int()`/`float()` inside try, clamp, strip C0/C1 control chars; or render the line from `RunState` (validated) instead of the raw snapshot; widen the `except` at `cli.py:1720` to `(JSONDecodeError, KeyError, TypeError, ValueError)`. Test with the 8 hostile blocks from `b1.py`. |
| S-3 | SHOULD-FIX | `cache/repo_state.py:178-209` (`WorktreeProbe.snapshot`), `:98-112` (`git_read_env`), called from `coordinator.py:432` (lookup, storable outcomes) and `:478` (settle, every store) | **Guard 3 executes commands named in the agent-writable `.git/config`.** G1a SEC-03 neutralised only `core.fsmonitor`; it recorded `filter.<x>.clean` as a residual without a PoC. PoC `j2.py`: repo with `.gitattributes: *.txt filter=pwn` and `filter.pwn.clean = <script>`; after a tracked file becomes stat-dirty, `WorktreeProbe().snapshot(...)` runs the script (marker file created). Attack path: a task agent that can write `.git/config` (Write/Edit tool without Bash, e.g. `--disallowedTools Bash`) plants the filter and touches a tracked file; the orchestrator's settle-time probe then runs it with the orchestrator's authority. Not MUST-FIX: it needs an in-`.git` write, the feature is double opt-in, an agent with Bash already has the same power, and the isolation integrator already runs `git status` in agent-touched worktrees (`isolation/integrator.py:365,698,1102`). | Record it in HLD 7.7 as a named residual with its precondition. Hardening options: compare tracked files by stat against `git ls-files --debug` instead of `git status` (no filters), or skip guard 3 when `.git/config` / `.git/info/attributes` contain `filter.` entries, or run the probe with `GIT_ATTR_SOURCE=<empty tree>` plus a check of `info/attributes`. |
| S-4 | SHOULD-FIX (G2 hard gate) | `ui/files.py:3-6` (shows `.orchestrator/` "exactly what an operator needs"); T-bLpoze `Draft` | **M-10 is only half implemented.** Store-side retention controls exist (see M-10 below). The dashboard half ("refuses `.orchestrator/cache`") is not in the diff: today the file browser lists and previews `.orchestrator/cache/blobs/**`, i.e. sha-named copies of every cached output, including outputs that were later deleted. Incremental exposure is bounded (the dashboard already serves the workspace, ADR-0021 auth applies), but deleted-output retention is the point of M-10. | Not a G1b blocker (owned by T-bLpoze). Make it an explicit G2 exit item with a test that `GET files?path=.orchestrator/cache/...` is refused, including via symlink and `..`, and that ADR-0020's `approvals/` denial merges into the same helper. |
| S-5 | SHOULD-FIX (docs / banner) | `cli.py:426-429` (banner), HLD 7.7 "Residual: deliberate poisoning" | **A forged entry is served as a hit, silently.** Accepted residual, now demonstrated end to end (`i1.py`): an agent rewrites one entry JSON and drops one blob (keys are visible in `status.json` and computable from visible files); the next run for that key dispatched nothing, wrote `FORGED BY AGENT` as the task output, and reported `saved ~$999999.0000 est.`. A prompt-injected agent in task B can therefore replace the work of a later cacheable task C (with `skip_if_outputs_exist: false`, or any C whose output is absent). Hooks, approval and verdict routing are not reachable (those tasks are ineligible), so the blast radius is "agent-writable output of an opted-in task". The banner gives no hint that this trust assumption exists. | Add one clause to the `on`-mode banner and the authoring guide: "the cache is agent-writable; do not enable for untrusted prompts (use shadow or --no-cache)". Optional: cap the displayed `saved_cost_usd` to something plausible. No code change to the trust model (HMAC stays non-MVP 2). |
| N-1 | NIT | `cache/store.py:412-415` (`delete_entry`), HLD 7.7 M-4 / 8.4 (line 2355) | An entry-leaf symlink is classified as `corrupt_entry` and evicted (the link is `unlink`ed), while the HLD text lists "entry" among the unsafe paths that are never evicted (D33). `a2.py`: target outside the cache stayed byte-identical, so nothing is followed; only the doc and the "entry symlink" test expectation disagree. Directory components (shard, `entries/v1`, `blobs`) correctly give `unsafe_path`, no eviction, link kept. | Align the HLD wording (leaf links are removed, never followed) and add a one-line test. |
| N-2 | NIT | `engine.py:1534-1541`, `restore.py:210-262` | After a hit the outputs are restored before `runstate.save`. A crash between the two makes the resume a **miss** (the restored output changed the key's "prior"), so the task is paid for again. Safe, never a wrong hit; `e1.py` also shows a half-restored workspace after a mid-phase-2 crash is regenerated cleanly. | Document in the authoring guide (crash after restore costs one dispatch). |
| N-3 | NIT | `cache/fingerprint.py:36-46` | Provider-routing env (`ANTHROPIC_BASE_URL`, `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX`, ...) is not in the key allowlist. Operator-controlled, not agent-controlled, so staleness only (HLD "user-level context" residual family). | Consider adding the non-secret routing names; keep the "never a secret" rule. |
| N-4 | NIT | `models.py:1234-1236` | `ResultCacheRecord.outcome`, `mode`, `mode_source` have no `max_length`, unlike every other string on the model. A hostile `state.json` can carry a huge string that `task_view` copies into `status.json`. No terminal sink today; the dashboard (T-bLpoze) will render them. | Add `Field(max_length=RESULT_CACHE_MAX_REASON)` (or a Literal-plus-fallback) before G2. |
| N-5 | NIT | `cache/hashing.py:62,108,117,136-143`, `cache/fingerprint.py:74-86` -> `coordinator.py:612-617` -> `records.py:112-116` | `reason_detail` carries **absolute** host paths (`l1.py`: `ineligible input_not_regular | /tmp/.../indir/link`). They land in `status.json`, `state.json` and run logs, which get shared via the dashboard/hub. Not secret, but leaks the host layout. | Use `rel()` / `posix_rel` for details; keep absolute paths out of persisted records. |
| N-6 | NIT | `cache/store.py:123-126` | A future `created_at` is never expired (documented as clock skew). A planted entry (`f1.py`) with year 2999 hits forever; it is already a poisoned entry, so no new capability. | Optionally treat `created_at > now + skew` as corrupt. |
| N-7 | NIT | `repo_state.py:178-209`, HLD 7.7 residual rows | Guard 3 is blind to: files marked `assume-unchanged` / `skip-worktree` (`a3.py`: both stored), untracked writes (stored), non-git workspaces (stored, documented D5). It is correct for the rest of the matrix: clean->dirty, dirty->dirtier, same-size edit with restored mtime, and an agent `git commit` (`repo_head_moved`) were all NOT stored. | Add `skip-worktree` / `assume-unchanged` to the documented blind spots. |
| N-8 | NIT | `coordinator.py:313-349`, `keys.py` hashing, `repo_state.py` (10 s git timeout x2) | Key build, input hashing (default cap 512 MiB) and two `git status` probes run synchronously on the engine main thread; with `max_parallel>1` they stall every settle. Bounded by caps, but there is no wall-clock budget and no cancel poll. | Document; optionally add an inline deadline that turns into `not storable`. |
| N-9 | NIT | `engine.py:1500` (pending popped per prepare), `coordinator.py:423-446` | After a quota/self-heal requeue the next lookup re-takes the guard-3 baseline, so tracked-file edits made by the **abandoned partial attempt** are absorbed and the successful retry can be stored. Same class as the accepted "dirty tracked edits before lookup" residual. Found by code reading, not exercised. | Mention under that residual in 7.7. |
| N-10 | NIT (G2 carry-over) | `engine.py:1219-1223` (seam comment), ADR-0020 D12 / rev 3 R-14 | E-Ag7Pw3 is not in this tree (no `approval` anywhere in `models.py` / `engine.py`). The only barrier for a gate recognised by **persisted** `TaskRunState.approval` (rather than a spec field) is that the gate handler runs before the cache seam; eligibility sees specs only. Spec-level fields are covered (tripwire `unknown_task_field`, `g1.py`). | G2: re-verify ordering once the approvals code lands; test a gate recognised only by persisted state. |

## M-item evidence

### M-10 secret retention and exposure: PARTIAL (store side verified, dashboard side = S-4)

| control | evidence |
|---|---|
| root mode `0o700`, dirs created `0o700` | `cache/constants.py:43` (`CACHE_DIR_MODE, TMP_FILE_MODE = 0o700, 0o600`); `store.py:328,335,356,445,940` (`ensure_dir_chain(..., CACHE_DIR_MODE)`); `restore.py` staging files `TMP_FILE_MODE` |
| self `.gitignore` + `CACHEDIR.TAG` | `constants.py:30-33`; `store.py:337-338` |
| entries hold only a summary, never argv / prompt / extra_args | `keys.py:66-91` (`summary_from_doc` docstring and field list); `keys.py:188-223` (summary built from `doc`, argv/prompt only in the hashed doc) |
| digests display-only | `keys.py:61-63`, `components` logged as 12-hex digests only (`a1.py` log lines) |
| no secret in env fingerprint | `fingerprint.py:34-46` (closed allowlist, comment "NEVER add a secret") |
| summary lines / report-usage / `status.json` carry counts and keys only | `report.py:115-126`, `cli.py:2061-2072`, `task_view` `report.py:57-77`; the only free text is clipped `reason_detail` and `source_run_id` (N-5, N-4) |
| dashboard refuses `.orchestrator/cache` | **NOT PRESENT** (`ui/files.py`, S-4) |
| orphaned plaintext copies outside the root | S-1 |

### M-11 semantic stale hit: VERIFIED (with the documented residuals)

| control | evidence |
|---|---|
| double opt-in | operator: `cache/settings.py:38-89` (flag > env > config > off); author: `settings.py:92-108`, enforced `coordinator.py:314-319` (not opted in -> no record, no key, no I/O) |
| HEADs, priors, argv, fingerprint in the key | `keys.py:131-147` (priors), `:175-203` (argv, fingerprint, prompt, `repo_heads`) |
| guard 1 (key changed during run) | `coordinator.py:468-477` |
| guard 2 (HEAD moved) | `coordinator.py:465-467`; `a3.py` "commit" -> `repo_head_moved`, not stored |
| guard 3 (tracked worktree changed) | `coordinator.py:478-482`, lazy snapshot `:423-446`, `repo_state.py:178-209`; `a3.py`: modify clean, modify dirty, same-size/restored-mtime edit all `repo_worktree_changed`, not stored. Blind spots: N-7, N-9 |
| lazy guard 3 (U-CO18): hit never snapshots; probe failure = not storable, never ineligible | `coordinator.py:418-421`, `:435-445`; tests `test_coordinator.py:1055-1098` green |
| TTL | `coordinator.py:366-368`, `store.py:123-126` (N-6) |
| shadow never restores | `coordinator.py:369-386` returns before `restore_outputs` (`:388`); `d1.py`: 3 shadow runs, the executor was dispatched every time, `outcome=would_hit`, no `cache.hit`, `hit=False` |
| `ao cache rm` | **not implemented yet** (`cache/cli.py` is a 19-line skeleton, T-6tRKml); not in G1b scope |
| forged entries | accepted residual, S-5 |

### M-12 approval bypass: VERIFIED for everything present in the tree; ordering check deferred to G2 (N-10)

- Allowlist + runtime unknown-field rules: `eligibility.py:95-127` (`TASK_ANY_VALUE`, `TASK_VALUE_RULED`), `:208-238` (runtime rule on unclassified task / workflow / defaults fields), `:256-260` (`unknown_agent_field`). Subclass probe `g1.py`: `approval="required"` -> `unknown_task_field: approval`, `human_gate=True` on the workflow -> `unknown_workflow_field: human_gate`, an extra agent field -> `unknown_agent_field: sandbox`.
- Also ineligible: `pre_hook`, `post_hook`, `emit_tasks`, `task_manifest_path`, `output_manifest`, worktree isolation, router task, loop member / gate, breaker verdict source, any task while run integration is active (`eligibility.py:117-127,218-273`). Verdict / gate / breaker / prompt paths cannot be a cached output (`coordinator.py:595-607` feeds `control_paths_abs`, `keys.py:125-126` raises `control_output`).
- Seam position: `engine.py:1219-1223`, after `should_skip`, isolation resolution, join, required-inputs check and dynamic-input collection, before the budget gate (`:1228-`) and the single dispatch site (`:922`, the only `pool.submit`). The comment "Approval/human gates (E-Ag7Pw3) run BEFORE this" is in place.
- E-Ag7Pw3 code is absent (`grep -i approval src/.../models.py engine.py` empty): the G2 check "gate runs before seam (c)" cannot be executed yet.

### M-16 a cache bug kills a run: VERIFIED

- Boundaries: `coordinator.py:258-276` (`lookup`), `:278-302` (`store_success`), `_disable` `:304-310`; `EXPECTED_ERRORS` `:110-121` -> `store_error` miss / skip with the cache kept; any other `Exception` -> cache disabled for the rest of the run (`cache.disabled` event); `strict` is a constructor argument only (no env or CLI exposure: `grep strict src/.../cache` shows none besides the coordinator).
- Adversarial run `k1.py` (real CLI, real engine): `get_entry` -> `RuntimeError`, `get_entry` -> `KeyError`, `restore_outputs` -> `AttributeError`, `put_entry` -> `RuntimeError`: every run exited 0, the task completed, `cache.disabled` logged.
- Engine side of the seam never raises on its own: `engine.py:1479-1540` only builds a dataclass and calls the hook; `_resolve_general_instructions` drops bad paths with a warning (`engine.py:2432-2475`), so it adds no new failure mode for non-opted-in tasks.
- Tests: U-CO14 `test_coordinator.py:845-920`.

### Other audit questions (answers with evidence)

| question | result |
|---|---|
| Hit bypasses approval / verdict / hook / breaker? | No. Ineligibility list above; a hit settles like `should_skip` (`engine.py:1524-1541`): no worker, no breaker evaluation (nothing was spent), no quota-timer reset. |
| Hit bypasses budget? | By design (ADR-0019 D12) a hit skips the budget **pre-flight**: `d1.py` with `--budget-total 1` exits 0 with a hit, and exits 1 with `builtin.budget_unsatisfiable` when the cache is off. No spend occurs, so no cap is exceeded; the stale previous-cycle charge is released through the shared helper (`engine.py:1454-1475`, called at `:1534` and `:1250`; existing budget tests green). |
| Hit forged from workspace content? | Yes if the writer owns the cache dir: S-5 (accepted residual). Cannot be forged by outputs, inputs or run state alone: a hit only comes from the live `lookup`, never from `state.json` / `status.json` (those only feed reporting). |
| Hostile entries at lookup | `f1.py`, 14 mutations through the real CLI: traversal path, absolute path, extra output, mode `0o4777`, size lie, sha lie, key mismatch, 5 MB junk, 100 000-deep nesting, naive datetime, huge cost, sensitive output path, ESC in `run_id`: all misses (or, for ESC `run_id`, a hit with the string only in JSON), no write outside the workspace, no crash. |
| Restore overwrites files outside the declared outputs? | No. Destinations come only from `key.output_abs` (`coordinator.py:388-394`, `restore.py:178-190`); manifest mismatch evicts (`f1.py` traversal / absolute / extra output, nothing written outside). Restore only replaces a file whose content already equals the entry's "prior" (it is in the key), so edits are never overwritten. Leftover temp files: S-1. |
| Unsafe path never evicted (U-CO19) | `a2.py`: symlinked shard dir, `entries/v1`, `blobs` -> `miss unsafe_path`, link kept, target byte-identical; symlinked `.orchestrator/cache` or its parent -> `store_unavailable`. Leaf entry / blob links are removed, not followed (N-1). `coordinator.py:343-349`, tests `test_coordinator.py:1119`. |
| Kill switch total | `a1.py`: `AO_CACHE=0|false|OFF|garbage`, `--no-cache` (also over `AO_CACHE=1` and over `cache.enabled: true`) -> no cache lines, no events, nothing restored (`settings.py:38-69`, `cli.py:394-431`: `MODE_OFF` returns `None` before the coordinator is imported). Every `Orchestrator(` caller other than the CLI passes no cache (default `None`). `--cache` beats `AO_CACHE=0` by design (explicit flag). Dashboard-launched children inherit the service env (`ui/processes.py:361`). |
| Bench force-off | `bench/subjects.py:94-95,456,470`: `--no-cache` AND `AO_CACHE=0`, the only `ao run` invocation in `bench/` (`:447-449`); test `tests/bench/test_bench_cache_forced_off.py` green. |
| Key poisoning via env / CLI args | Env enters the key only through the closed allowlist (`fingerprint.py:36-46,162-163`), values hashed, never stored; agents cannot change the orchestrator's environment. CLI prefix handling: `--cache/--no-cache` are plain Typer booleans; `ao cache rm/show <prefix>` is not implemented yet (G2). |
| Information disclosure | No secret, prompt or argv in summary lines, `report-usage`, `status.json` or events. Findings: absolute paths in `reason_detail` (N-5); the banner prints the absolute cache root (stderr, operator only); hit logs include output sha256 (display-only digests, documented residual "offline guessing"). |
| Concurrency (`max_parallel>1`) | Lookup (prepare) and store (settle) run on the main thread only; `ctx.result_cache_pending` is a plain dict touched only there (`engine.py:495,1500,1523,1553`); one dispatch site. `c1.py`: 6 concurrent `ao run` processes x 4 rounds against one cache: all exit 0, 2 valid entries (absent-prior and present-prior keys), no tmp/trash leftovers, no `cache.disabled`. I-2 serial and `max_parallel=3` green. |
| Resume after a crash | Mid-store: put is atomic, orphans pruned (G1a). Mid-restore: `e1.py`, resume is a miss and re-dispatches (N-2), temp orphan remains (S-1). Stale pending is popped at every prepare (`engine.py:1500`); a hostile `state.json` hit record cannot cause a hit (reporting only). |
| Reporting totality | `RunState` records are validated (pydantic) and `is_current_result_cache_record` binds cycle + `ended_at` (`models.py:1335-1346`); a forged hit record on a failed task / wrong cycle / ghost task is not current (`g1.py`). Raw `status.json` consumer is not total: S-2. |
| Cost accounting integrity | A hit carries 0 spend, a first-pass hit adds nothing, hit-after-spend is still counted: `h1.py` set `cumulative_cost_usd=3.0` on a hit task and `report-usage` showed the group at $3.75 (0.75 real + 3.00 carried) with `Tasks=1`, JSON agrees; a forged hit record cannot hide spend (`usage.py` Site A adds cost and tokens for hits with carried spend). `mean_cost_usd` / `retry_rate` divide by `tasks` only when non-zero (`usage.py:305-310`). `saved_*` is explicitly an estimate, excluded from group cost; a planted entry can inflate it (S-5). |

## Commands and results

Worktree `/usr/avadhoot/mounted/agent-orchestrator/.claude/worktrees/agent-a18ce2c08e42a3a5a`, `.venv/bin/python`.

| command | result |
|---|---|
| `.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache tests/test_spawn_provenance.py tests/test_nfr2_regression_gate.py` | **1190 passed** in 14.77 s (re-run: 1190 passed in 15.38 s) |
| `pytest -q -p no:cacheprovider tests/cache -k "u_lz1 or u_ast_e or u_co18 or u_co19 or i1 or i2 or i_1 or i_2 or noop or hostile"` | 60 passed (U-LZ1, U-AST-E, U-CO18, U-CO19, I-1 / I-2 cache-off proof included) |
| `ruff check` on the changed `src` files (`cache/`, `engine.py`, `cli.py`, `usage.py`, `runstate.py`, `outcomes.py`, `bench/subjects.py`) | All checks passed |
| engine diff budget (spot check, owned by the other G1b reviewer) | `git diff --numstat 9f9a400..HEAD -- engine.py`: +133 / -30 = net +103 (limit +110); no added line over 100 columns |
| `bandit`, `pip-audit` | not installed in the venv; no new dependency is introduced by the diff |
| `git status --short` after all scripts | only the other reviewer's untracked `review-g1b.md`; nothing of mine in the repo tree |

Adversarial scripts (scratchpad), result in one line each:

| script | result |
|---|---|
| `a1.py` | on / hit works; kill switches (`AO_CACHE` 0/false/OFF/garbage, `--no-cache`, over config) produce no cache events |
| `a2.py` | symlinked shard / `entries/v1` / `blobs`: `unsafe_path`, never evicted, target unchanged; root / `.orchestrator` symlink: `store_unavailable`; leaf entry / blob links removed, not followed (N-1) |
| `a3.py` | guard 3 matrix (stored vs skipped) as in N-7 |
| `b1.py` | hostile `status.json` block crashes `ao status` and echoes ESC (S-2) |
| `c1.py` | 6 concurrent processes x 4 rounds: clean |
| `d1.py` | shadow never restores or skips dispatch; hit passes a 1-token budget (design D12) |
| `e1.py` | crash-orphaned restore temp file, never swept, enters a dir input (S-1, N-2) |
| `f1.py` | 14 hostile entry mutations: no escape, no crash |
| `g1.py` | unknown task / workflow / agent field -> ineligible; stale hit records not current |
| `h1.py` | hit-after-spend counted; no spend hiding |
| `i1.py` | forged entry served as a hit (S-5) |
| `j1.py`, `j2.py` | `filter.<x>.clean` executed by the guard-3 probe (j2; j1's `sh -c` variant did not trigger) (S-3) |
| `k1.py` | M-16: four injected unexpected errors, all runs exit 0 with `cache.disabled` |
| `l1.py` | absolute path in `reason_detail` (N-5) |

## Pre-submit checklist

- [x] Engagement criteria: explicit request (Gate G1b security pass).
- [x] Untrusted spec / payload execution: no `eval` / `exec` / shell interpolation in the diff; the only subprocesses are fixed-argv git (`repo_state.py`) and `claude --version` (`fingerprint.py:111`, G1a). Finding S-3 (git filter execution).
- [x] Sandboxing / isolation: hit path is cancellable between tasks, bounded by hashing caps; no fan-out added. N-8 (main-thread latency).
- [x] Artifact / path safety: clean (restore destinations spec-only, link checks, `f1.py`, `a2.py`). S-1 (orphan temp files).
- [x] Secrets handling: S-1, S-4 (retention); no secret in records, logs or summaries.
- [x] Trigger authn / authz: N/A for this diff (no new trigger or control API; dashboard launch inherits env, `ui/processes.py:361`).
- [x] Input validation: hostile entries, status.json (S-2), env allowlist; no new YAML/JSON loader (project config uses the existing safe loader and strict `CacheConfig`, `project_config.py:170-198`).
- [x] Rate limiting / DoS: caps verified in G1a; N-8 (no wall-clock bound on inline work).
- [x] Supply chain: N/A for the diff (no dependency or lockfile change); `pip-audit` not installed here.
- [x] CI / merge gates: N/A for this diff; recommendations below.
- [x] Every finding has a location and a demonstrated or code-evident path (N-9 is by code reading and says so).

## CI and process follow-ups

- Add a hostile-input test for `_echo_result_cache_line` (the 8 blocks from `b1.py`) and for the restore-orphan sweep (S-1, S-2).
- G2 exit items: dashboard denial of `.orchestrator/cache` (S-4); approval-gate ordering once E-Ag7Pw3 lands, including a gate recognised only through persisted state (N-10); `ao cache rm/show` prefix handling (`KEY_PREFIX_RE.fullmatch`, one match); the SEC-15 CI-config sensitive-list decision from G1a (`.gitlab-ci.yml`, `.githooks/*` still restore).
- Keep `gitleaks` / `pip-audit` / Bandit (or Semgrep) on the PR checks per CLAUDE.md; none of them would have caught S-1..S-3.
- HLD 7.7: add residual rows for git filter drivers (S-3), `skip-worktree` / `assume-unchanged` and partial-attempt baselines (N-7, N-9), and the operator banner clause (S-5).

## Follow-ups (owners)

| item | owner | when |
|---|---|---|
| S-1 sweep + dir-input skip + test | T-u3jG8F (restore) owner | before G2 |
| S-2 total `format_summary_line` / `ao status` | T-eyn5UG / T-o95l1M owner | before G2 |
| S-3 decide: harden vs document | architect + T-uoYW6b owner | before G2 |
| S-4 dashboard denial | T-bLpoze owner | G2 exit |
| S-5 banner / doc clause | T-o95l1M owner + T-bdQZW4 | before release |
| N-1..N-10 | one hygiene ticket | G2 |
