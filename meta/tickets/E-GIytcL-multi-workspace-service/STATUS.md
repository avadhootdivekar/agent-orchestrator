# STATUS

- ID: `E-GIytcL-multi-workspace-service`
- Updated At: 2026-08-28T00:00:00Z
- State: In Progress
- Owner: dev-epic agent

## This update
- Early gate complete: `reviewer` + `architect` ran in parallel against the HLD/ADR/task
  breakdown before any implementation code landed. 3 blocking + 6 should-fix findings, all
  folded into HLD, ADR-0012 ("Early-gate corrections"), and the three implementation task
  tickets' acceptance criteria (new NFR-4 added to EPIC.md with traceability). No finding
  touched the user's locked design decisions themselves.
- `T-Gr8s2a-registry-ports-config` **Done** (2026-08-28): registry/ports/`ProjectConfig.ui`
  foundation layer shipped — `service/paths.py`, `service/registry.py` (with the required
  file-locked `mutate()` primitive, AC7), `service/ports.py` (tier-first port conflict
  tie-break, AC8), `ProjectConfig.ui.port`. 24 new tests, full suite 1841 passed/7 skipped/
  0 failed, ruff clean, mypy unchanged at the 4-error `_version.py` baseline. See task
  `STATUS.md` for full evidence. `T-Sv9d4k-supervisor-boot-resume` is now unblocked.
- `T-Sv9d4k-supervisor-boot-resume` **Done** (2026-08-28): supervisor daemon + boot-resume
  shipped — `service/supervisor.py` (`Supervisor.start/tick/shutdown/status_snapshot`,
  `ManagedChild`, `RestartBackoff`, singleton flock), `service/boot_resume.py`
  (`scan_resumable_runs`, `BootResumeGuard`). All three blocking early-gate items (AC15
  singleton lock, AC16 idempotency re-check, AC18 orphan reclamation) implemented and each
  independently tested with real subprocesses/real flock (not mocked), including the
  AC15-mandated "kill an actual lock-holding subprocess and confirm a fresh acquire
  succeeds" test and the NFR-1 crux assertion (a `start_new_session=True` decoy survives
  `.shutdown()`). Should-fix AC19-22 (EADDRINUSE backstop, log redirection, OS boot id,
  spawn stagger) also implemented. 42 new tests (15 boot-resume + 27 supervisor), full
  suite 1883 passed/7 skipped/0 failed (+42 vs. prior baseline, zero regressions), ruff
  clean, mypy unchanged at the 4-error `_version.py` baseline. See task `STATUS.md` for
  full per-AC evidence. `T-Hb3x7q-hub-systemd-cli` is now unblocked.
- `T-Hb3x7q-hub-systemd-cli` **Done** (2026-08-28): hub HTTP server, systemd unit
  generation, and the `ao service` CLI sub-app shipped — `service/hub.py`
  (`build_hub_app`), `service/systemd.py` (`resolve_ao_executable`, `render_unit`,
  `install_unit`, `systemctl_available`, `start_via_systemctl`/`stop_via_systemctl`),
  `service/cli.py` (`add`/`remove`/`list`/`status`/`install`/`start`/`stop`/`run` +
  `build_status_provider`). All three early-gate items (AC14 hub `SecurityMiddleware`
  actually mounted — tested via a real `TestClient` 421 on a disallowed `Host` header, not
  an import check; AC16 six systemd-hardening lines — `RestartSec`/`StartLimitIntervalSec`/
  `StartLimitBurst`/`TimeoutStopSec`/`EnvironmentFile`/explicit `--hub-port`; AC17
  `resolve_ao_executable` rejecting a relative `sys.argv[0]` even with basename `ao`)
  implemented and each independently tested. Top-level `cli.py` changed by exactly the
  two specified lines (`git diff` confirmed). 50 new tests (11 hub + 20 systemd + 19 CLI
  e2e), full suite 1933 passed/7 skipped/0 failed (+50 vs. prior baseline, zero
  regressions), ruff clean, mypy unchanged at the 4-error `_version.py` baseline. See task
  `STATUS.md` for full per-AC evidence. `T-Rv5m1t-test-review-e2e` is now unblocked.

## Evidence
- `docs-md/multi-workspace-service-hld.md` — updated with 6 corrections (singleton lock,
  registry read-lock-merge-write, orphan reclamation, EADDRINUSE backstop, tier-first port
  conflict tie-break, mounted hub SecurityMiddleware, plus systemd unit hardening).
- `docs-md/adr/ADR-0012-multi-workspace-service-supervisor.md` — "Early-gate corrections"
  section added.
- `meta/tickets/E-GIytcL-multi-workspace-service/T-*/TASK.md` — all three implementation
  tickets updated with new, numbered ACs tracing to the corrections.
- `meta/tickets/E-GIytcL-multi-workspace-service/EPIC.md` — NFR-4 added.

## Risks / Blockers
- None blocking start of implementation. Carried risk: `KillMode=process` has no automated
  test (locked "no systemd in tests" decision); a manual verification is attempted at
  late-gate (`T-Rv5m1t` AC9) if the sandbox supports a systemd user session, else the gap
  is documented, not silently closed.

## Update — 2026-08-28: post-review hardening pass

**By:** Claude (developer, fork of dev-epic)
**Role:** developer
**Date:** 2026-08-28

Final `reviewer` gate (run after `T-Hb3x7q` landed) found no blocking code defects across
all three implementation tasks -- its one "blocking" finding (that the late-gate task never
ran) was a race-condition false positive: it read `T-Rv5m1t/STATUS.md` before the
concurrently-running `tester` agent had finished writing its real subprocess + real systemd
evidence to that same file. Confirmed via the file's actual final content and an
independent process/systemd-unit sweep of the machine that the tester's exercise genuinely
happened (see `T-Rv5m1t/STATUS.md`).

The reviewer's four legitimate should-fix findings were applied directly:
1. **Observability**: added a module `logger` to `boot_resume.py`, `supervisor.py`, and
   `cli.py` (mirroring `ui/security.py`'s pattern) with `logger.warning(...)` at the four
   previously-silent exception swallows (corrupt `boot_resume.json`, corrupt
   `supervisor.json`, a run directory pruned mid-scan, a workspace's `aggregate()` failing
   during a hub status poll). Swallow-and-continue behavior itself is unchanged -- only the
   missing log line was added.
2. **DRY**: `ports.py`'s `_pick_free_port`/`_MAX_FREE_PORT_ATTEMPTS` promoted to public
   `pick_free_port`/`MAX_FREE_PORT_ATTEMPTS`; `supervisor.py`'s near-identical duplicate
   deleted, now imports and calls `ports.pick_free_port` for its EADDRINUSE-reassignment
   pick. `ui/processes.py`'s separately-duplicated `_pid_alive` was deliberately left alone
   (crosses the epic's own "do not edit `ui/processes.py`" boundary; already documented as
   intentional duplication).
3. **Shutdown latency**: `service/cli.py`'s `run()` monitor loop now uses
   `stop_event.wait(HUB_TICK_INTERVAL_SECONDS)` instead of `time.sleep(...)` -- a SIGTERM
   now interrupts the wait immediately instead of a PEP-475 sleep retrying for its full
   remaining duration.
4. **Exception safety**: `run()`'s `supervisor.start()` call now has a broad
   `except Exception` (in addition to the existing `SupervisorLockHeldError` handling) that
   runs a best-effort `supervisor.shutdown()` before re-raising, so a mid-`start()` failure
   (e.g. after boot-resume ran but before every child spawned) doesn't leak spawned
   children or the singleton lock.

No new dedicated tests were added for items 3/4: both live inside `ao service run`'s CLI
body, which the epic's own `T-Hb3x7q` AC12 deliberately excludes from direct testing (it
blocks and binds a real port) in favor of testing its parts (`Supervisor`, `hub.py`)
independently -- consistent with that existing policy rather than a gap introduced now.

**Verification**: `uv run pytest -q tests/service/` -> 116 passed (unchanged count -- these
are hardening fixes, not new behavior); full suite `uv run pytest -q` -> 1933 passed,
7 skipped, 0 failed (identical to pre-fix, zero regressions); `ruff check`/
`ruff format --check` on `src/agent_orchestrator/service/` -> clean (one file needed
`ruff format` applied after the edits, now clean); `uv run mypy src` -> 4 pre-existing
errors, all in `_version.py`, unchanged.

## Next actions
1. Delegate `T-Rv5m1t-test-review-e2e` to a `tester`/`reviewer` pass: full-suite
   baseline/after counts across the whole epic, a late-gate e2e exercise of the CLI/hub/
   systemd surface `T-Hb3x7q` shipped (including, if a systemd user session is available
   in that execution environment, the manual `KillMode=process` verification carried
   forward as an open item), a `reviewer` pass, and `meta/ROADMAP.md`/HLD sync.

## Epic close-out

By: Claude (dev-epic)
Role: manager
Date: 2026-08-28

All four tasks Done. Late-gate e2e (real `ao service run` subprocess exercise + real,
non-persistent `systemd-run --user` `KillMode=process` verification) passed with hard
evidence in `T-Rv5m1t-test-review-e2e/STATUS.md`. Final reviewer pass raised one false-
positive "blocking" finding (a race between the concurrently-dispatched tester and reviewer
agents reading the same in-progress ticket file — resolved by re-reading the file's final
content plus an independent process/systemd-unit sweep of the machine) and four legitimate
should-fix items, all four applied and verified (logging at silent-swallow points, DRY dedup
of a duplicated port-picking helper, `stop_event.wait` over `time.sleep` for faster SIGTERM
response, exception-safety cleanup around `Supervisor.start()`).

Final state: `tests/service/` = 116 tests, all passing. Full suite = 1933 passed, 7 skipped,
0 failed (baseline before this epic: 1817 passed, 7 skipped, 0 failed -- +116 net new tests,
zero regressions across four independent verification passes at each task boundary plus this
final one). `ruff check .` / `ruff format --check .` / `uv run mypy src` clean (mypy's 4
pre-existing `_version.py` errors are unrelated to this epic and untouched). `cli.py`'s diff
confirmed to be exactly the promised 2-line `add_typer` registration throughout.

Traceability re-check: every MVP requirement (FR-1..FR-7, NFR-1..NFR-4) maps to landed code
with dedicated tests (see `docs-md/ai-epics/E-GIytcL-multi-workspace-service.md` for the
full mapping). Docs synced: `meta/ROADMAP.md` (§2a + table row + "Recently delivered"
bullet), HLD and ADR-0012 status headers flipped to Accepted/shipped.

Epic status: **Done**.

---

## Post-epic field defect: boot-resume spawned an unusable `ao resume` (2026-08-29)

By: Claude (Opus 5)
Role: developer
Date: 2026-08-29

Found on the **first live deployment** of the shipped service, not by the suite. Two
distinct bugs, one symptom.

**Symptom.** Boot-resume approved run `e-0hdsi3-ad-firstdraft-20260827T105508Z` in
`ao-runner-ai-models`, spawned it, and the child exited immediately with
`--workflow is required`. The run stayed `status=="running"` with no process owning it, and
the hub's `boot_resume_decisions` showed `approve` followed by `skip_already_this_boot`.

**Bug 1 — candidates carried no spec paths.** `ResumeCandidate` held only
`(workspace_root, run_id)`, so `launch_resume` was called with `--run-id` alone. That is
fine only for a workspace whose `.ao/config.yaml` sets a global `workflow`; both live
runner workspaces deliberately do NOT (each epic owns its own workflow file), so the resume
could never start. Fix: `scan_resumable_runs` now recovers `workflow_path` from the
`LaunchRecord` field and `--reposets`/`--agents` from the recorded argv (neither has a
first-class record field), and `Supervisor._decide_and_act` passes all three through.

**Bug 2 — the recovery was defeated by the wreckage Bug 1 left behind.** Each failed
boot-resume had persisted its *own* `LaunchRecord`, carrying `workflow_path=None` and a bare
`--run-id`-only argv. `scan_resumable_runs` emitted one candidate **per record** and
`BootResumeGuard` dedupes by `run_id`, so the candidate that actually reached the spawn was
built from whichever record sorted newest — and `reconcile()` returns newest-first, i.e. one
of the information-free records the bug itself created. Verified against the live workspace's
real four-record history: record `[0]` (newest) had all three paths `None` while `[2]`/`[3]`
had all of them. **The Bug 1 fix alone would have failed silently on exactly the machine it
was written to repair.** Fix: `scan_resumable_runs` groups records by `run_id` and emits one
candidate per *run*, merging each spec path independently from the newest record that
actually carries it (`_recover_spec_paths`); `workflow_path` also falls back to its own
`--workflow` argv flag. Liveness is now judged across **all** of a run's records, so a live
launch plus older finished ones no longer yields a candidate.

**Evidence.** Fixed `scan_resumable_runs`, run against a copy of the live
`ao-runner-ai-models` launch records, returns exactly 1 candidate with all three paths
populated (pre-fix: 4 candidates, the winning one all-`None`). Regression tests in
`tests/service/test_boot_resume_record_merge.py` are built from those real record shapes.

Carried limitation (unchanged): boot-resume still only sees dashboard-launched runs — a bare
terminal `ao run` leaves no `LaunchRecord`, so there is no argv to recover paths from.
