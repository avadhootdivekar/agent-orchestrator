# TASK: T-Te3Qw8-integration-e2e-suite

## Metadata
- Task ID: `T-Te3Qw8-integration-e2e-suite`
- Epic ID: `E-Sc9Rt4-scheduler-triggers`
- Owner: tester agent
- Created: 2026-09-06
- Last Updated: 2026-09-06
- Status: Draft
- Estimate: 3 days

## Requirements Mapping
- Requirement IDs: all FRs, NFR-1, NFR-2, NFR-4 (see `../EPIC.md`)

## Description
The cross-module tiers. Each implementation task ships its own unit tests; this task owns what
none of them can own alone: the **integration** tier (the real engine driven against a real
temp state dir with stubbed processes) and the **end-to-end** tier (via the CLI boundary, per
CLAUDE.md: "from as outer a boundary as possible").

It also owns the two claims no single task can prove: **NFR-1** (a workspace without schedules is
unaffected) and **NFR-2** (one bad schedule or one bad file cannot take down the daemon).

Read HLD §18 in full — it is the normative test plan for this task. Read
`tests/service/test_supervisor.py` and `tests/ui/conftest.py` for the fixtures and idioms to
reuse rather than reinvent.

Files you own (create/edit freely):
- `tests/service/test_schedule_integration.py` (new)
- `tests/schedules/test_e2e_schedules.py` (new)
- `tests/conftest.py` and `tests/schedules/conftest.py` (additive fixtures only)
- `Makefile` (additive: a `test-schedules` target mirroring the existing `test-ui`)

Production code: **propose, do not edit.** If a test cannot be written without a production
change, write the exact proposed diff into `STATUS.md` and hand it to the owning task's developer
— do not edit `src/` here. (Exception: a genuinely trivial, agreed fix, recorded in `STATUS.md`
with the owning task named.)

## Acceptance Criteria

### Shared fixtures (write once, reuse everywhere)
1. A `schedules_env` fixture setting `AO_SERVICE_CONFIG` and `AO_SERVICE_STATE_DIR` under
   `tmp_path` — autouse in both new test modules, so **no test touches a real `$HOME`** (NFR-4).
   Mirror `tests/service/test_cli_e2e.py`'s existing autouse fixture.
2. `tests/ui/conftest.py`'s `_allow_testclient_host` autouse fixture is **not** inherited by a
   new top-level directory. Either hoist it to `tests/conftest.py` (preferred — it is a
   cross-cutting concern) or duplicate it with a comment naming the reason. State which, and why,
   in `STATUS.md`.
3. A `make_schedule_workspace(tmp_path, bindings, workflow=...)` helper producing a temp
   workspace with `.ao/config.yaml`, `.ao/schedules.yaml`, a `fake`-executor workflow and a
   reposet — the single place a scheduler test workspace is constructed.

### Integration tier (real engine, stubbed processes, single-stepped)
4. `ScheduleEngine` + a real temp state dir + a fake registry + `tests/ui/conftest.py`'s
   `StubSupervisor`: assert `launch_run` was called with the **exact** expected kwargs — workflow
   path, prompt text, reposets, agents, and the full options dict including the constructed
   `run_id`. No process is spawned anywhere in this module.
5. Restart-mid-fire: write an `intended` fire record with a dead pid and no run directory, call
   `engine.start()`, assert the record becomes `orphaned`, that **zero** launches occurred, and
   that a subsequent `tick()` at the same `scheduled_for` also produces no launch (the burned
   key also suppresses catch-up).
6. Interleaving: `Supervisor.tick()` and `ScheduleEngine.tick()` driven alternately in one loop
   with fake children — assert a child killed mid-schedule-evaluation is still detected and
   restarted, and that with a stubbed `monotonic` forcing a budget overrun the schedules not
   reached are left unchanged for the next tick.
7. NFR-2 isolation, three separate tests: (a) workspace A's `.ao/schedules.yaml` is unparseable
   and workspace B's schedules still fire; (b) one binding whose evaluation raises does not
   prevent the next binding in the same workspace from firing, and quarantines after five; (c) an
   engine that raises out of `tick()` entirely is caught by `T-Sv5Hb3`'s outer backstop and the
   supervisor keeps ticking.
8. Hub status: `GET /api/service/status` through a real `TestClient` against
   `build_hub_app(build_status_provider(supervisor, schedule_engine=engine))` contains the
   `schedules` key with HLD §12's shape, and `hub.py` is confirmed unmodified by this epic
   (`git log --oneline -- src/agent_orchestrator/service/hub.py`).
9. Drop directory: a request file produces exactly one fire; an expired one produces none and
   emits `schedule.request_expired`; a malformed request file is discarded with a warning and
   does not stop the drain.

### End-to-end tier (via `CliRunner` on the root app)
10. Full CLI lifecycle: `ao schedule add` → `list` → `validate` → `disable` → `enable` →
    `history` → `remove`, asserting `.ao/schedules.yaml` content and exit codes after each.
11. **The headline e2e**: a temp workspace with a `fake`-executor workflow and a `kind: interval,
    interval_seconds: 60` binding, driven by `ao schedule daemon --once` with an injected clock —
    assert a real run directory appears, its `status.json` is `succeeded`, its id is exactly the
    `ao-<schedule_id>-<ts>` string `T-Lp4Wt6` constructs, and the fire record links
    `fire_key → run_id`. A second `--once` immediately after produces zero fires.
12. `ao schedule run-now` with no daemon running: a run directory appears; with a simulated live
    daemon: a request file appears and the CLI process launched nothing.
13. File-watch e2e: touch the watched file, advance the fixed clock past the debounce, `--once`
    ⇒ exactly one run; `--once` again with no change ⇒ none; write into `.orchestrator/` ⇒ none
    (the self-trigger guard).
14. Webhook e2e: `TestClient` posts a correctly-signed delivery to the listener app, then
    `--once` fires it and a run appears; a replayed delivery is 409 and produces no second run.
15. `until` e2e: three fires against `max_runs: 3`, the fourth tick reports `until_satisfied` and
    the schedule shows `completed` in `ao schedule list`; `ao schedule enable` then allows a
    fourth fire.
16. **NFR-1 backward compatibility**: a registered workspace with **no** `.ao/schedules.yaml` and
    a registry with no `schedules:` block produces zero schedule events, zero fire records, and
    an `ao service status` payload identical to a captured pre-epic fixture **apart from** the
    new `schedules` key. This is the test that proves the epic is additive.

### Reporting
17. Report the full-suite count before and after (`uv run pytest -q`), the per-module coverage
    for every new module against the ≥80 % floor (≥90 % for `service/webhook.py`), `ruff check` /
    `ruff format --check` status, and `uv run mypy src` as a whole-tree count against the
    4-error `_version.py` baseline. Numbers, not adjectives.
18. Explicitly state what is **not** exercised and why — no real `ao ui`/uvicorn child, no real
    agent CLI, no real systemd, no network — mirroring the "what this does not cover" paragraph
    `multi-workspace-service-hld.md` §10 uses. A gap that is named is a decision; a gap that is
    silent is a defect.

## Risks
- Fixed clocks plus a real filesystem is where scheduler tests become flaky. Every time value
  must come from an injected clock; a bare `datetime.now()` or `time.sleep` in a test is a defect
  in the test, and a `_wait_until(predicate, timeout, interval)` helper (already present in
  `tests/service/test_supervisor.py`) is the sanctioned way to wait on real subprocess state.
- AC2's fixture-inheritance trap is real and easy to miss: a new top-level test directory silently
  loses `tests/ui/conftest.py`'s autouse fixtures, and the resulting 421s look like an app bug.
- AC16 needs a captured pre-epic `ao service status` fixture. Capture it **before** the epic's
  code is on the branch, or reconstruct it from `git show <baseline>:` — say which was done.
- Scope: 16 ACs in 3 days is tight. If it slips, integration (AC4-9) and AC11/AC16 are the ones
  that must land; the per-trigger e2e tests (AC13-15) can follow into Sprint 3's buffer.

## Dependencies
- Every implementation task through `T-Ap1Xs3` must have landed. `T-Fw8Gp4` / `T-Wh9Kv1` /
  `T-Un0Lm6` / `T-Ap1Xs3` gate AC13 / AC14 / AC15 / (part of) AC10 respectively — write the rest
  first rather than waiting.
- Reads (read-only): `tests/ui/conftest.py` (`StubSupervisor`, `write_run`, `make_run_state`,
  `write_workflow`, `_allow_testclient_host`), `tests/service/test_supervisor.py`
  (`_FakeMonotonic`, `_no_sleep`, `_wait_until`, `_free_port`), `tests/conftest.py`
  (`fixed_clock`, `make_orchestrator` + `FakeExecutor`).

## Pseudocode / Algorithm
```text
# AC11 -- the headline e2e, shape only
def test_interval_schedule_fires_a_real_run(tmp_path, monkeypatch, fixed_clock):
    ws = make_schedule_workspace(tmp_path, bindings=[{
        "id": "nightly", "kind": "interval", "interval_seconds": 60,
        "workflow": "workflows/demo/workflow.json"}])          # fake executor
    result = CliRunner().invoke(app, ["schedule", "daemon", "--workspace", str(ws),
                                      "--once", "--state-dir", str(tmp_path / "state")])
    assert result.exit_code == 0
    run_dirs = list((ws / ".orchestrator" / "runs").iterdir())
    assert len(run_dirs) == 1
    assert run_dirs[0].name == scheduled_run_id("nightly", expected_scheduled_for)
    assert json.loads((run_dirs[0] / "state.json").read_text())["status"] == "succeeded"

    again = CliRunner().invoke(app, [... same ...])             # at-most-once
    assert len(list((ws / ".orchestrator" / "runs").iterdir())) == 1
```

## Schemas / Interface Notes
- Interface / API: exercises every public interface this epic added; defines none of its own
  beyond test fixtures.
- Spec / data schema: constructs `.ao/schedules.yaml` fixtures covering every `kind`.
- Triggers / events: asserts the emitted `schedule.*` / `webhook.*` / `watch.*` vocabulary,
  including the **suppressed** outcomes.
- Artifacts: temp workspaces and temp state dirs under `tmp_path` only; a real `$HOME` is never
  touched.

## Handoff Boundary
- Upstream: every implementation task; HLD §18 (normative).
- Downstream: `T-Se4Bk5` (consumes the reported numbers and the named gaps rather than re-running
  everything), `T-Dc6Zr2` (the "what is verified" section of the refreshed docs).

## Artifacts
- Docs/comments: `meta/tickets/E-Sc9Rt4-scheduler-triggers/T-Te3Qw8-integration-e2e-suite/`
- Large outputs: `output/E-Sc9Rt4-scheduler-triggers/test-logs/`
