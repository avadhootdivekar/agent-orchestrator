# TASK: T-rwj4nV-schedule-cli-limits-usage

## Metadata
- Task ID: `T-rwj4nV-schedule-cli-limits-usage`
- Epic ID: `E-Qp4Zm8-scheduled-run-governance`
- Owner: developer agent
- Created: 2026-11-10
- Last Updated: 2026-11-11
- Status: Implemented (AC1-11); `resume` blocked on T-nI4DBf commit - see STATUS.md
- Estimate: 2 days

## Requirements Mapping
- Requirement IDs: FR-1, FR-5, FR-6 (see `../EPIC.md`); owner decisions D-2, D-3

## Description
The CLI surface for governance on top of E-Sc9Rt4's `ao schedule` sub-app:
- limit flags on `add`, plus `set-limits` and `pin-prompt`;
- `usage` (text + `--json`);
- `resume`;
- new `list`/`history` columns.

Read HLD §14.1 (normative), §11 and §9.5. Read the **merged** code of E-Sc9Rt4 `T-Cl6Jn9` (`schedules/cli.py`) and of
`T-Uz0Uol`, `T-PpE5lf` and `T-nI4DBf`.

Files you own:
- `src/agent_orchestrator/schedules/cli.py` (edit, additive): flags and subcommands below.
- `src/agent_orchestrator/schedules/usage_view.py` (new): pure formatter `render_usage(doc) -> str` and builder
  `build_usage_doc(...) -> dict` (`schema: "ao.schedule.usage/v1"`). It is shared with `T-8knqVe`.
- `tests/schedules/test_cli_governance_e2e.py` (new; `CliRunner` over the root `agent_orchestrator.cli:app`).

Do NOT touch: `cli.py` (root; the sub-app is already mounted by E-Sc9Rt4), `service/` (read-only imports),
`engine.py`, `ui/`.

## Acceptance Criteria
1. `ao schedule add ... --quota day=3 --quota week=10 --cost-per-run 2 --on-cap pause --pause-ttl 259200 --cost-cap
   week=20 --week-start monday` writes exactly the HLD §6.1 `limits` block (assert the YAML). Malformed `--quota`
   (`day`, `day=0`, `year=3`) ⇒ exit 2 with the offending token named.
2. **D-2:** `ao schedule add` with no limit flags and no `defaults.limits` ⇒ exit 2, message = `LIMITS_REQUIRED` plus
   a hint listing `--quota`, `--cost-per-run`, `--cost-cap`. Nothing is written.
3. `--prompt TEXT` writes `.ao/schedule-prompts/<id>.md` (refusing to overwrite an existing different file unless
   `--force`) and sets `prompt_file`. `--prompt` and `--prompt-file` are mutually exclusive. `--pin-prompt` stores the
   current sha256. `ao schedule pin-prompt ID` re-pins (exit 2 if the binding has no `prompt_file`).
4. `ao schedule set-limits ID` rewrites only `limits` via the locked `mutate()`. `--clear-quotas`/`--clear-cost` that
   would leave no limit ⇒ exit 2 (D-2).
5. `ao schedule usage [ID] [--at ISO] [--json]` text output matches the HLD §14.1 layout. Pinned by a golden test under
   a fixed clock with: runs, actual + reserved spend, max, local reset time, paused runs with overshoot and expiry, and
   the last 5 governance events. `--json` validates against a JSON Schema checked into the test. `--at` is read-only
   (assert no file mtime changes).
6. `ao schedule resume ID --run RUN [--add-usd X] [--local|--queue]` calls `request_capped_resume`. Success ⇒ exit 0
   and prints the new cap. `ResumeRefused` ⇒ **exit 3** with the reason and detail (`expired at …`, `remaining $0.55 in
   week 2026-11-09`).
7. `ao schedule list` gains `LIMITS` (compact `3/d 10/w $2/run $20/w`), `TODAY` (used/max of the smallest quota
   period) and `PAUSED` (count). `history` renders governance skips/caps with reason + period. `--json` carries the
   `governance` object.
8. `ao schedule next` annotates upcoming instants with `would skip: <reason> (resets …)` using the pure `gate()`,
   with no launches and no writes.
9. Every subcommand works without a live daemon (reads state dir via `AO_SERVICE_STATE_DIR` in tests). No test
   touches `$HOME`.
10. ruff/format/mypy clean; all E-Sc9Rt4 CLI tests green.
11. **(L4, L7) `ao schedule validate` warnings:** (a) `sentinel.llm.budget_usd >= per_run_usd` (generation spend counts
    toward the run cap, so the lane can use up the whole cap); (b) `overlap: allow` without `per_run_usd`
    (period-only caps give one run the whole remaining period budget as its cap, and `allow` serializes in practice).
    One test each, warning text asserted.

## Risks
- Text output drift breaks the golden test. Keep formatting in `usage_view.py` only, so the dashboard shares the
  builder, not the strings.

## Dependencies
- Upstream: E-Sc9Rt4 `T-Cl6Jn9`; `T-Uz0Uol`; `T-PpE5lf`; `T-nI4DBf` (for `resume` only; build the rest first if it
  lags).
- Downstream: `T-8knqVe` reuses `build_usage_doc`; `T-88wSLC`.

## Pseudocode / Algorithm
```text
parse_kv(token) -> (period, number) with period in {day, week, month}; else BadParameter
usage: windows = windows_for(at or now, periods(limits), tz, week_start)
       doc = build_usage_doc(ledger.usage(...), paused=ledger.paused(...), events=events.tail(sid, 5))
       print(json.dumps(doc) if --json else render_usage(doc))
```

## Schemas / Interface Notes
- Interface / CLI: HLD §14.1; exit codes 0/1/2/3.
- Spec / data schema: `ao.schedule.usage/v1`.
- Triggers / events: reads `events.jsonl`; writes none (except `resume` via its request).
- Artifacts: `.ao/schedules.yaml`, `.ao/schedule-prompts/<id>.md`.

## Handoff Boundary
- Upstream: ledger, gate, capped resume.
- Downstream: the `build_usage_doc` dict shape is the dashboard API body (`T-8knqVe`).

## Artifacts
- Docs/comments: `meta/tickets/E-Qp4Zm8-scheduled-run-governance/T-rwj4nV-schedule-cli-limits-usage/`
- Large outputs: N/A
