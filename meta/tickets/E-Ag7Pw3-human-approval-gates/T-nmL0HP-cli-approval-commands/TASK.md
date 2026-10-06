# TASK: T-nmL0HP-cli-approval-commands

## Metadata
- Task ID: `T-nmL0HP-cli-approval-commands`
- Epic ID: `E-Ag7Pw3-human-approval-gates`
- Owner: developer
- Created: 2026-10-04
- Last Updated: 2026-10-05
- Status: Draft
- Estimate: 2.5 days (rev 1: 3 d; the listing and view builders moved to `approvals/views.py`, Gate 1 R-05)

## Requirements Mapping
- Requirement IDs: FR-8, NFR-7
- Design: HLD §9.13 (commands, exit codes 0–11, flow, render, JSON), §9.12 (signer, views), §9.14.3
  (`REFUSAL_EXIT_CODE`), §9.11 (marker), §18.5 (e2e design), §7.4 TM-2/9/13/20/27, §26 row 19; Gate 1 R-02,
  R-05, R-08, suggestion S-06

## Description
Implement `approvals/cli.py`: a Typer sub-app `approvals` (`list`, `show`) and
`register_decision_commands(app)` adding top-level `approve` and `reject`; register both from `cli.py`
(3 lines, like `service_app`) **as the first commit** (it unblocks `T-ZPGoSN`, which also edits `cli.py`).
Only `typer` is imported at module scope.

- Workspace resolution: `--workspace` > `AO_WORKSPACE_ROOT` > `cli._resolve_workspace_root` (lazy import).
- `list` = `views.scan_pending`; `show` = `views.build_task_view(channel="cli", identity=identity_for_cli())`
  + audit tail + key path/id. No listing or view logic of its own.
- `approve`/`reject` flow of HLD §9.13.3: marker check first (exit 3, unauthenticated audit line, no key
  access); TTY seam `stdin_is_tty()`; non-TTY without `--yes` → exit 4; identity from `pwd`;
  `prepare_decision`; `--expect-digest`; render; typed confirmation word; `commit_decision` with the
  displayed hashes.
- Every `ApprovalSignerError` maps to its exit code through `REFUSAL_EXIT_CODE` (never message text);
  `--json` refusals carry `reason` and `detail_code`; `strip_terminal_controls` on every printed string
  that came from the spec/state/agents.

Files — new: `approvals/cli.py`; tests `tests/approvals/test_cli_approvals.py`,
`tests/approvals/test_e2e_cli_background_run.py`. Shared: `cli.py` (§26 row 19). **Exclusive files** (HLD
§22.3): these; never in parallel with `T-ZPGoSN` on `cli.py` (registration commit first).

## Acceptance Criteria
1. `AO_IN_AGENT=1` → `ao approve`/`ao reject` exit 3 with `--yes` and without; the injected key store
   records **zero** calls; an unauthenticated `approval.refused` (`in_agent`) audit line is written when
   the run dir is resolvable.
2. Non-TTY stdin without `--yes` → exit 4, nothing written; with the TTY seam patched to True and input
   `"approve\n"` → exit 0 and one record; input `"y\n"` → exit 9, nothing written.
3. `USER=mallory LOGNAME=mallory` does not change the recorded identity (pwd-derived).
4. `test_exit_codes_follow_mapping_table`: each refusal maps to its `REFUSAL_EXIT_CODE` (5 unauthorized /
   require_dashboard / require_2fa; 6 not_pending / already_decided / expired; 7 stale_hashes /
   review_unavailable; 8 request_invalid incl. key mismatch with both key ids in the message, key errors,
   tamper, identity; 10 not_found incl. an unreadable `state.json`; 11 comment_required for `--reason ""`).
5. A review file changed between render and commit (patched prompt side effect) → exit 7 and no record.
6. `--expect-digest` mismatch → exit 7; match → exit 0.
7. `--json` outputs validate against the §9.13.5 shapes for list, show, approve success and refusal
   (`reason` + `detail_code`); `test_show_json_equals_task_view_json` (the `show --json` object is
   `views.task_view_json` plus `key` and `audit_tail`).
8. `show` strips ANSI/OSC control sequences from message, comment and paths (test with `"\x1b[2J"` and an
   OSC 8 hyperlink); reports "changed since request opened" and "not applied: request already decided".
9. E2E (`test_e2e_cli_background_run.py`, HLD §18.5): a real child `ao run` (PYTHONPATH = this worktree's
   `src`, asserted to exist; `AO_IN_AGENT` removed; `AO_APPROVAL_KEY_DIR` = the test's
   `tmp_path_factory.mktemp("approval-keys")` key dir, outside the workspace (Gate 1 R-02); poll 0.1 s;
   `executor: fake` agents) reaches `awaiting_approval`; in-process `ao approve --yes` exits 0; the child
   exits 0 and the run is `succeeded` (≤ 60 s); the reject variant makes the child exit 1. The child's
   process group is always killed in `finally`.
10. Targeted tests green; ruff/mypy clean (full suite at the stage checkpoint); `reviewer`-agent review
    recorded.

## Risks
- `CliRunner` swaps global stdout: never two overlapping in-process invocations (the background run is a
  real subprocess).
- The e2e depends on `T-vwIpSw`; land the unit tests first and the e2e once `T-vwIpSw` has merged (note it
  in STATUS).

## Dependencies
- `T-pfJiXw` (signer, views); `T-vwIpSw` for the e2e test; `T-1B8hu4` for `in_agent_context`.

## Pseudocode / Algorithm
```text
HLD §9.13.3 decide_cmd; exit = REFUSAL_EXIT_CODE[exc.reason].
```

## Schemas / Interface Notes
- CLI surface: HLD §9.13.1; exit codes §9.13.2; JSON §9.13.5; mapping §9.14.3.

## Verification
```
WT=/usr/avadhoot/mounted/agent-orchestrator/.claude/worktrees/agent-a513b4d78ac3b20bb
PY=/usr/avadhoot/mounted/agent-orchestrator/.venv/bin/python
cd $WT && $PY -m pytest -o pythonpath=src -p no:cacheprovider -q tests/approvals/test_cli_approvals.py tests/approvals/test_e2e_cli_background_run.py tests/test_cli.py
cd $WT && $PY -m ruff check src/agent_orchestrator tests/approvals && $PY -m mypy src/agent_orchestrator/approvals src/agent_orchestrator/cli.py
```

## Handoff Boundary
- Upstream: `T-pfJiXw`, `T-1B8hu4`, `T-vwIpSw` (e2e).
- Downstream: `T-ZPGoSN` (after the registration commit), `T-pdLR96`, `T-wH71UE` (docs of the CLI).

## Artifacts
- Code as listed; evidence in STATUS.md.
