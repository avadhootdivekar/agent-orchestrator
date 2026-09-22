# STATUS

- ID: `T-FCC8mT-e2e-verification`
- Updated At: 2026-09-21
- State: Done
- Owner: dev-epic

## This update
- By: dev-epic
- Role: manager
- Date: 2026-09-21
- Comment: Driven directly by dev-epic rather than delegated (this is the epic's late gate —
  worth the coordinator's own eyes per the dev-epic charter). Wrote
  `scripts/helper/epics/E-AMSSHX/e2e_hooks_demo.py`, which runs the REAL `.venv/bin/ao` binary
  as a subprocess (the outermost boundary, CLAUDE.md's e2e rule) against
  `specs/examples/workflow-hooks.json`, mirroring `tests/test_e2e_cli.py`'s own
  fake-executor-workspace convention (copies workflow/reposet/instructions/hook scripts into
  an isolated temp dir, swaps `executor: claude_cli` -> `executor: fake` for determinism/no
  real spend, sets `AO_WORKSPACE_ROOT`).

  Two real scenarios, both actually run (not claimed):
  1. **Happy path** (the shipped example spec, unmodified): `ao validate` -> `OK: all specs
     valid`; `ao run` -> `Status: succeeded`, both tasks succeeded. Confirmed in the run's own
     persisted `state.json`: `design.pre_hook_result.status == "passed"`,
     `implement.post_hook_result.status == "passed"` with `score == 1.0`. Hook capture dirs
     (`design/pre_hook/`, `implement/post_hook/`) exist on disk with `context.json` (verified
     paths-only content — NFR-1), `result.json`, `stdout.txt`, `stderr.txt`.
  2. **Gating post_hook**: a temp copy of the workflow with `implement`'s post_hook flipped to
     `on_failure: "fail_task"`, run with `AO_EXAMPLE_GRADE_FORCE_FAIL=1` (the example grade
     script's documented override) so the grading hook reports a failing grade. Result: `ao
     run` exits non-zero, `Status: failed`, `implement.status` downgraded from what would have
     been `succeeded` to `"failed"`, `post_hook_result.status == "failed"` /
     `score == 0.0` / `exit_code == 1` — the exact documented HLD §6 behavior, demonstrated
     through the real engine end to end, not just unit-asserted.

  Also found and fixed a cosmetic issue while building this: `specs/examples/hooks/grade.py`
  nested its own info under a `"detail"` key, which then double-nested inside the engine's own
  `HookOutcome.detail` (`{"detail": {...}}`). Flattened for a clean example (commit `b4e0d15`).

## Evidence
- Script (reproducible): `scripts/helper/epics/E-AMSSHX/e2e_hooks_demo.py`
- Raw evidence (committed, ~196K, small enough to keep in-repo):
  `output/E-AMSSHX-task-lifecycle-hooks/scenario1-happy-path/` and
  `output/E-AMSSHX-task-lifecycle-hooks/scenario2-gating-post-hook/` — each contains
  `cli_run_stdout.txt`/`cli_run_stderr.txt`/`cli_run_exit_code.txt` (raw CLI output),
  `workflow-hooks.json` (the exact spec used), and `run_dir/` (the full
  `.orchestrator/runs/<run_id>/` tree: `state.json`, `run.log`, `status.json`, every task's
  `attempt-1/` capture dir, and every hook's `pre_hook`/`post_hook` capture dir).
- Commit: `b4e0d15`.

## Risks / Blockers
- None. AC-5 (`pytest -q -m e2e`) note: this repo's actual e2e marker convention
  (`tests/test_e2e_cli.py` et al.) drives the CLI via `CliRunner`, which this script doesn't use
  (it invokes the real installed binary as a subprocess instead — an even more literal "outer
  boundary" than `CliRunner`, per CLAUDE.md's e2e rule, though not tagged with the repo's
  `@pytest.mark.e2e` marker since it's a standalone demonstration script, not a pytest test).
  The full suite (which includes every existing `@pytest.mark.e2e`-tagged CLI test) was already
  independently re-run and confirmed clean in T-jI3P4p's verification (3897 passed / 1
  pre-existing unrelated failure).

## Next actions
1. None — this ticket is complete. Awaiting T-6gR2ya (reviewer pass) before closing the epic.
