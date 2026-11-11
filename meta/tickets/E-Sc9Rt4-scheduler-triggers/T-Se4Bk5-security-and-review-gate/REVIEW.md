# T-Se4Bk5 — Opus re-review of the security gate and architecture pass (AC6, AC8–AC14)

- By: reviewer agent (overseer unit w22-02-a1-security-gate-rereview, Opus) · Role: reviewer · Date: 2026-11-11
- Reviewed state: committed tip **`4b9d6a3`** on `ad/enhancements-10nov`. It contains the S-1..S-6 fixes
  (`2f93191`, plus the earlier scheme pin) and the probes in
  `output/E-Sc9Rt4-scheduler-triggers/se4bk5-probes/`.
- Method:
  - Extracted the tip with `git archive 4b9d6a3` into a temp tree.
  - Ran `uv sync --extra dev --extra ui` there.
  - Ran every probe and the full gate in that tree, so other units' uncommitted edits in the shared
    checkout could not affect the results.
  - My own fixes are on top: `b93b576` (code) and `f4f0fc8` (probes).
- Epic base for the diff evidence: `8aff618`, the parent of the first E-Sc9Rt4 code commit `47babc1`.
- Not in this pass:
  - AC7 (threat-model paragraph, a `dev-security` item);
  - AC14's `make ui-typecheck` / `make ui-test` and per-module coverage;
  - AC16 (EPIC/ADR-0014 sync).

## Summary

The S-1..S-6 fixes hold. All five original probes and a new run-id probe (p6) give the expected
results on the committed tip. S-8 and S-10 can now be closed (see below).

The architecture largely matches the epic's goals:
- the engine core is untouched apart from the planned `run_id` kwarg;
- there is one launch path and one cron implementation;
- the at-most-once ordering is intact.

There are two real defects in the durability/concurrency layer:
- **R-1:** the fire store had its own atomic-write idiom, with a fixed tmp name and no directory fsync.
  **Fixed here.**
- **R-2:** fire records have a second, unlocked writer process (dashboard/CLI usage views). This
  contradicts the documented single-writer assumption. **High, open, handed to E-Qp4Zm8.**

**Verdict: T-Se4Bk5 is not Done.** R-2 is an open high finding, and AC7, AC16 and part of AC14 are not
started.

## Re-verification of S-1..S-10 (evidence: probes re-run on the `4b9d6a3` archive)

| Finding | Verdict | Evidence |
|---|---|---|
| S-1 (blocking) scheme downgrade | **Closed** | `p1_downgrade.py`: `AO replay a week later: stale`; `downgrade replay: wrong_scheme`. |
| S-2 FIFO / unbounded ungoverned read | **Closed** | `p2_fifo.py`: `LaunchResolutionError('prompt_file unreadable: p.fifo (not a regular file)')`, returned immediately. `_read_prompt` now uses `governance_gate._read_regular_file` on the resolved, root-checked path. |
| S-3 NUL / control characters | **Closed** | `p3_paths.py`: `'a\x00b'=reject` at load for every path field (`workflow`, `prompt_file`, `template`, `watch.paths`, `webhook.secret_file`, `until.*`). `p4_nul.py`: `read_prompt` / `resolve_secret` raise the named `ScheduleFileError … must not contain NUL or control characters`. |
| S-4 secret_file TOCTOU | **Closed for the final component** | `resolve_secret` opens once with `O_RDONLY\|O_NOFOLLOW\|O_NONBLOCK`, `fstat`s the descriptor (S_ISREG, `& 0o077`) and caps the read at `MAX_SECRET_FILE_BYTES`. Residual: R-6 (intermediate directories). |
| S-5 duplicate fsync idiom / silent `pass` | **Closed** | `WebhookRequestQueue.enqueue` calls `statefile.atomic_write_bytes(durable=True)`. `webhook._fsync_dir` is deleted; the statefile version logs fsync failures. |
| S-6 magic `10` | **Closed (n/a)** | The fallback is no longer at the tip. |
| S-7, S-9 | Limitations, unchanged | Not re-opened. |
| **S-8** uvicorn header deadline | **Closed** | **The uvicorn-internals dependency is now guarded by a real-socket contract test.** `tests/service/test_webhook_listener.py` starts a real `uvicorn.Server` with `webhook_uvicorn_kwargs()` and asserts three things: (a) 20 idle sockets and a header-trickle socket are closed by the server after the deadline; (b) `WEBHOOK_LIMIT_CONCURRENCY-1` idle sockets do not lock out `/healthz`; (c) a prompt request is not cut off mid-flight, and the keep-alive deadline is re-armed. If uvicorn renames `handle_events`, `cycle` or `on_response_complete`, (b) or (c) fails, so the break is no longer silent. All 3 pass in the full gate (uvicorn 0.51.0). The `uvicorn>=0.29` lower bound stays unpinned on purpose; the test is the guard. Documented bounded limitation. |
| **S-10** `cost_cap_usd` in `ALLOWED_OPTIONS` | **Closed (deliberate, guarded); help text fixed here** | **`ALLOWED_OPTIONS` grew by exactly `cost_cap_usd`, and `run_id` stays out.** Measured on the tip: 15 value options and 3 bool options. `run_id` has no `ui_group` and is not forwardable (F1 holds). `3e2870b` (T-et7Edf) intentionally edited `test_allow_list_unchanged_and_registry_derived` to add the entry, so AC2's "unchanged, test passes unmodified" claim is **superseded**. The growth is safe on every path: <br>• Dashboard launch: `DashboardService._check_cost_cap_option` bounds the value (finite, > 0, ≤ `MAX_COST_CAP_USD`). <br>• Dashboard resume of a governed run: refused with 409. <br>• Schedules: `RUN_ARG_FORBIDDEN` refuses `run_args.cost_cap_usd`. <br>The registry help still said "CLI only" although the option has a `ui_group` and appears on the dashboard form. **Fixed in `b93b576`** (help text plus the `tests/fixtures/run_options_schema.json` golden). The ADR-0014 late-gate note for the AC2 wording change is handed to AC16. |

## Run-id guard (AC6), re-verified independently

New probe: `se4bk5-probes/p6_run_id.py`, committed in `f4f0fc8`. It targets `RunStateStore.reserve_run_dir`,
which `cli.py:1770` calls for every `ao run --run-id`, including the scheduler's subprocess launches.

- **Rejected with `OrchestratorError`:** `..`, `.`, `../x`, `a/b`, `/abs`, `~x`, `.hidden`, `a\x00b`,
  `a\nb`, 129 characters, the empty string, `a\..\b`, `%2e%2e` and `é`. `RUN_ID_RE` requires a fullmatch
  of `[A-Za-z0-9][A-Za-z0-9._-]{0,127}`.
- **No escape:** no directory was created outside `.orchestrator/runs/`.
- **No reuse:** reusing `r1` raises `RunAlreadyExistsError`. A pre-existing **symlink** named like the
  id also raises `RunAlreadyExistsError`, so it is never followed.
- **Atomic under concurrency:**
  - 2 threads, 50 trials: exactly one winner in 50/50;
  - **2 processes, 20 trials: exactly one winner in 20/20.**

  This works because `os.mkdir` is called without `exist_ok`.

**AC6 holds.**

## Architecture review (AC8–AC13)

### Critical (blocking)
None.

### Warnings (should-fix)

**R-2 (high). Fire records have a second writer process with no lock. A read-only usage view can roll
back the engine's concurrent update to the same record.**
- **Where the extra writes come from:**
  - `UsageLedger.charge` calls `_raise_mark`, which calls `FireIntentStore.update_governance`
    (`service/usage_ledger.py:346-365`). It persists a cost high-water mark whenever the mark rises
    by at least `HWM_PERSIST_STEP_USD` or a run finishes.
  - That runs in every process that builds a ledger, not only the engine:
    - the dashboard's `GET` schedule-usage handler: `UsageLedger(FireIntentStore(sdir)).usage(...)`
      (`ui/service.py:1333`);
    - `ao schedule usage` (`schedules/cli.py:1318`);
    - `ao schedule resume` (`schedules/cli.py:1400`).
- **Why the write is unsafe:**
  - `update_governance` reads the record, merges the governance fields, then rewrites the **whole**
    record (`current.model_copy(update={"governance": merged})`). It takes no lock.
  - Its docstring says the engine is the single writer (`fire_store.py`, `update_governance`). That is
    false once a dashboard tab polls usage.
  - Only `capped_resume` takes the governance flock around its writes; the ledger's mark write
    does not.
- **Failure scenario:**
  - The dashboard reads record K.
  - The engine then writes K, for example `status: launched` plus `run_id`, or a cap transition to
    `cap_state: paused/aborted` (`schedule_engine.py:1506`).
  - The dashboard writes back its stale copy with only a higher `cost_observed_usd`.
  - The engine's transition is lost.
- **Scope of the damage:**
  - At-most-once still holds, because any record suppresses a re-fire.
  - What can be lost is the governance/cap state and launch metadata. On restart, `reconcile_orphans`
    can recover `launched` from an `intended` record. A lost `paused`/`aborted` cap state cannot be
    recovered.
- **Why it matters:** this breaks AC12 ("the single-writer assumption is documented and still true")
  for fire records. `state.json` itself is still single-writer (see AC12 below).
- **Proposed change (pick one):**
  - (a) Give `UsageLedger` a `persist: bool` flag, `False` for the dashboard and CLI view callers. They
    then only use the in-memory high-water mark; the engine's ledger keeps persisting.
  - (b) Make `update_governance` and `update` take the governance flock
    (`<state_dir>/schedules/usage.lock`) and re-read under it.

  (a) is smaller and keeps the single-writer design.
- **Owner:** handed to E-Qp4Zm8 (T-lTW88y / the `usage_ledger.py` owner). It is not fixed here because
  it is a structural change to another epic's module (TASK.md: "do not restructure another task's
  module").
- **Test to add:** call `update_governance` from a second `FireIntentStore` instance between the
  engine's `get` and `update`, and assert that the engine's `cap_state` survives.

**R-1. `FireIntentStore._persist` was a second hand-rolled atomic-write idiom, with a fixed shared tmp
name and no directory fsync.** This is the AC9 "second atomic-write idiom", and it affected AC12
durability.
- **Before:** `<key>.json.tmp`, then fsync of the file, then `os.replace`, and nothing more.
  - The rename itself was not durable: there was no parent-directory fsync, so after a power loss the
    record could be missing even though `write()` had returned before the launch.
  - Two writer processes (see R-2) shared one tmp path.
  - This is the same defect as FU-4 in TEST-QUALITY.md, whose test also pinned the wrong fsync
    count.
- **Fixed here (`b93b576`):** `_persist` now delegates to `statefile.atomic_write_bytes(...,
  durable=True)`. That gives a unique `mkstemp` tmp, fsync of the file, `os.replace`, and fsync of the
  directory, and a failed fsync is logged.
- **Tests (`tests/service/test_fire_store.py`):**
  - `test_write_calls_fsync` now asserts `["file","dir"]` for every persist;
  - new `test_persist_does_not_use_a_fixed_shared_tmp_name`: a directory squatting on the old
    `<key>.json.tmp` name used to break every write.
  - Both **fail against the old `fire_store.py` and pass with the fix** (checked by swapping the HEAD
    file back).
  - `pytest tests/service tests/schedules`: **1283 passed**.

**R-3. Some tests patch private names, and some collaborators are built inside constructors instead
of being injected (AC13).** Counted in `tests/schedules` and `tests/service`:
- `monkeypatch.setattr(sched_cli, "_now", …)` ×11, plus `sched_cli._build_engine`,
  `engine._finalize_governance_once`, `engine._snapshot`, `governance_gate._read_regular_file` and
  `service_cli._probe_hub_status` ×2.
- `ScheduleEngine.__init__` creates its own `FireIntentStore`, `ScheduleStateStore` and
  `ScheduleEventLog` (`schedule_engine.py:~314-316`). This contradicts AC13 ("every collaborator is
  constructor-injected").
- `schedule_launch.py` imports the private `governance_gate._read_regular_file` (the S-2 fix).
- `fire_store.py`, `webhook.py` and `watch.py` import the private `supervisor._slug_for_root`.

Proposed change:
- make the CLI clock a public seam (`now`, or a context-injected clock);
- accept the three stores as optional constructor parameters;
- rename `_read_regular_file` to a public `read_regular_file` and `_slug_for_root` to `slug_for_root`.

Handed to T-Cl6Jn9 (CLI clock) and T-Sd1Kq7 (stores/slug). Should-fix.

**R-4. Root-containment checks are re-implemented in new code instead of reusing
`LocalFsArtifactStore.resolve` (AC9).**
- `webhook.resolve_secret`: `path != root and root not in path.parents`.
- `governance_gate.read_prompt`: `path.is_relative_to(root)`.

Both are correct today, but they are a second and third root guard. Proposed change: resolve through
`LocalFsArtifactStore(root).resolve(rel)`, catching `ArtifactPathError`, as `ScheduledLauncher._guarded_path`
already does. Handed to T-Wh9Kv1 (webhook) and E-Qp4Zm8 T-PpE5lf (governance gate). Should-fix.

### Suggestions

**R-5. Two swallowed exceptions have no log, and one rejection hides its cause (AC11).**
- `ScheduleEngine._snapshot` (`schedule_engine.py:1639` registry load, `:1646` store load):
  `except Exception: entries = []` / `bindings = []` with no log line. The tick path logs the same
  failures elsewhere, so this is only a status-view gap. Add `logger.debug(..., exc_info=True)`.
- `resolve_secret` returns `None` with no reason event for `OSError` (including `ELOOP` from
  `O_NOFOLLOW`), a non-regular file, and `ValueError`. The 503 is then logged only as the generic
  `secret_unresolvable`. Emit a `reason` on each branch.

`grep -E "except.*:\s*pass"` is empty. The one multi-line `except FireAlreadyRecordedError: pass`
(`schedule_engine.py:1355`) is intentional: an existing record suppresses the fire.

**R-6 (documented bounded limitation). `O_NOFOLLOW` protects only the final path component.** A
directory component of `webhook.secret_file` that is swapped for a symlink after `resolve()` is still
followed. To exploit it, an attacker needs write access inside the workspace, which already lets them
replace the secret file itself. This is the same trust level as S-9.

**R-7. A few named constants have no comment explaining their value (AC10).**
- `webhook.py`: `MAX_WEBHOOK_BODY_BYTES`, `REPLAY_WINDOW_SECONDS`, `MAX_LOGGED_FIELD`,
  `WEBHOOK_LIMIT_CONCURRENCY`, `WEBHOOK_TIMEOUT_KEEP_ALIVE_SECONDS`, `WEBHOOK_BODY_READ_TIMEOUT_SECONDS`.
- `watch.py`: `WATCH_SCAN_BUDGET_SECONDS`, `DEFAULT_WATCH_MAX_FILES`, `MAX_EVENT_PATHS`.

There are no inline magic thresholds. The only inline numerics in function bodies are the unit
conversions `// 60`, `* 1000` and `// 10**9`. 41 module constants were spot-checked.

### Per-AC results

- **AC8 boundaries.** Measured with `git diff --stat 8aff618..HEAD`, attributed per commit. Everything
  else in these diffs belongs to other epics (E-Sn7tL2 sentinel T-Rk4Wn7, E-Qp4Zm8 cost cap T-et7Edf,
  T-lTW88y) and was not scored against AC8.

  | Path | Result |
  |---|---|
  | `engine.py` | E-Sc9Rt4's only change is `ad0b6e9` (T-Ri7Dz2): +4/−1 lines for the `run_id` kwarg passed to `new_run`. |
  | `executors/` | `fake.py` changes belong to E-Qp4Zm8 (`1c0bfeb`). |
  | `dag.py`, `spec.py`, `service/hub.py` | Untouched. |
  | `models.py` | E-Sc9Rt4's changes are confined to `Trigger` (`interval` type plus `interval_seconds`) and `MAX_INTERVAL_SECONDS` (`47babc1`, `0704430`, `d8734a6`). |
  | `specs/workflow.schema.json` | 8 lines, confined to `$defs.trigger`. |
  | `cli.py` | E-Sc9Rt4 added the `add_typer(schedule_app)` and `validate_workspace_schedules` hook (`a74a1a6`) and `--run-id` (`ad0b6e9`). |

- **AC9 DRY.**
  - There is one cron implementation: `croniter`, wrapped only in `schedules/models.py`.
  - There is one launch path: `ScheduledLauncher` calls `supervisor.launch_run` (`schedule_launch.py:234`).
  - There is no second workspace slugger. `schedules/cli._slug` derives schedule ids from workflow
    names, which is a different job.
  - Second root guards: see R-4. Second atomic-write idiom: R-1, now fixed.
  - The `statefile` follow-up sites are still the pre-existing `registry.py:136` and
    `supervisor.py:756/775`, plus `boot_resume.py:272`, which predates the epic. They remain a
    follow-up, not a divergence.
- **AC10.** Passes, with the suggestions in R-7.
- **AC11.**
  - Every engine and launcher `except Exception` logs with `logger.exception` or emits an event, apart
    from R-5.
  - The outer backstop is `schedule_engine.py:484`: `logger.exception("schedule tick failed")` plus a
    `schedule.eval_failed` event.
- **AC12.**
  - Write-before-launch: `ScheduledLauncher.launch`, "Phase 1: durable intent BEFORE anything else".
  - "Any status suppresses": `FireIntentStore.exists` docstring and implementation, and
    `FireAlreadyRecordedError` on `write`.
  - The asymmetry between overlap and concurrency key-burning is correct and commented
    (`schedule_engine.py:214`, `:1018`, `:134`).
  - `state.json`'s single-writer, lock-free design is documented (`fire_store.py` module docstring)
    and still true. `run-now --local` and `resume --local` take `engine.lock` (`engine.start()`), or
    queue the request if a daemon holds it.
  - **Not true for fire records: see R-2.** The fsync was incomplete before R-1.
- **AC13.**
  - `datetime.now` appears only in the default clock factories (`schedule_engine.py:145`,
    `fire_store.py:65`, `webhook.py:177`, `schedules/cli.py:134`). Every evaluation path uses the
    injected clock or `monotonic`.
  - The private-patch and injection gaps are listed in R-3.

## Quality gate (AC14), re-run on the committed tip and not quoted

Full gate on the `4b9d6a3` archive (`.venv` from `uv sync --extra dev --extra ui`):

**`pytest -q`: 12155 passed, 5 failed, 35 skipped, 2 xfailed (18 min 58 s).** The 5 failures:

| Test | Classification |
|---|---|
| `tests/test_install_script.py::test_default_flavor_is_stable_and_matches_pre_flavor_behavior`, `::test_beta_flavor_via_env_var_is_fully_namespaced` | **Environment.** The archive has no `.git`, so `git rev-parse` exits 128. Both pass in the real checkout. |
| `tests/auth/test_auth_off_regression.py::test_openapi_differs_by_exactly_the_status_route` | **Real at `4b9d6a3`.** `d7cb6a7` (T-8knqVe) added `/api/schedules/{schedule_id}/usage` and `/api/schedules/governance-events` without updating the baseline. **Already fixed by `0f7dac0`**; 14/14 of that file pass at the current HEAD. |
| `tests/bench/test_dev_core_suite.py::test_fake_subject_full_suite_run_produces_valid_run_json_and_summary`, `tests/bench/test_dev_medium_suite.py::…` | **Real, and they reproduce in the real checkout** (`solved=0` of 6, `infra_errors=0`). Bench code and these tests were not touched by this branch's scheduler work (last changes `991391b` and earlier). Outside this epic; not investigated; flagged for the bench owner. |

The other tools:
- **`ruff check .`: 75 errors, all in the committed `se4bk5-probes/*.py`.** These were T-Se4Bk5's own w20
  debt. **Fixed in `f4f0fc8`.** On a `git archive HEAD` tree with `git init` (so `.gitignore` applies):
  `ruff check .` gives "All checks passed!" and `ruff format --check .` gives "767 files already
  formatted".
- **`ruff format --check .`: 6 files** at `4b9d6a3`: the 5 probes (fixed in `f4f0fc8`) and the
  gitignored generated `_build_info.py`. The latter is an environment artifact: ruff ignores
  `.gitignore` in a tree without `.git`.
- **`mypy src`: "Success: no issues found in 233 source files"**, so the 0-error baseline holds.
  - Side observation (pre-existing since `991391b`, not this epic): on a fresh tree where hatch has not
    yet generated `_build_info.py`, `mypy src` reports 4 `no-redef` errors in `_version.py:26`. The
    0-error baseline assumes a built tree.
- **Targeted at the new tip:** `pytest tests/service tests/schedules` gives **1283 passed**, and
  `tests/test_run_options.py` gives 175 passed. ruff and mypy are clean on the changed files.
- **Not run:** `make ui-typecheck` / `make ui-test`, and per-module coverage.

## Finding counts (this pass)

- Blocking: 0.
- Should-fix: 4.
  - R-1: fixed.
  - R-2 (high): open, handed to E-Qp4Zm8.
  - R-3, R-4: open, handed.
- Suggestions: 2 (R-5, R-7), open.
- Limitations: 1 new (R-6). S-8 is now closed by its contract test, and S-10 is closed as a deliberate,
  guarded change.
- Gate fixes: probe lint/format (`f4f0fc8`).

## Pre-submit checklist

- [x] Scope: the S-1..S-10 re-verify, AC6 and AC8–AC13 on `4b9d6a3`, plus the gate.
- [x] Alignment checked at all three levels: project (DAG/engine untouched, safe-by-default), epic/task
  (TASK.md ACs read), and code intent (docstrings vs behaviour, e.g. R-2).
- [x] Every review dimension walked; dimensions with nothing to flag are stated in the per-AC results.
- [x] Every finding has a location, observation, why it matters and a next step.
- [x] Severity bucketed without inflation. R-2 is high but not blocking, because at-most-once still
  holds.
- [x] Testing notes are given per finding (R-1 tests added; R-2 interleaving test proposed).
- [x] No unrelated refactor. Only R-1 (a small local fix in this epic's module), the S-10 help text and
  the probes were changed.
