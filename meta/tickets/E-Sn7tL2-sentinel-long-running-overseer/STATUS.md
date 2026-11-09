# STATUS

- ID: `E-Sn7tL2-sentinel-long-running-overseer`
- Updated At: `2026-11-09`
- State: `Draft` (ready for implementation)
- Owner: `avadhoot`

## This update
- By: claude-opus-5-5 · Role: architect · Date: 2026-11-09 · Comment: Rev 3. All owner decisions resolved (D1–D4,
  §10). 13 task folders created; final checklist in EPIC §11; combined implementation handoff in `HANDOFF.md`
  (co-delivery with E-aYG5vn).

- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: **T-Sn0Spk done — GO-WITH-CHANGES** for the
  soft barrier (FR-8/FR-12); §4.4.4 fallback not needed unless invariant I1 slips. Evidence: `output/E-Sn7tL2-sentinel-long-running-overseer/spike/RESULT.md`.
  Spec changes to carry into T-Sn1Adr: **FR-8** reshape only via `_apply_pending_edits` (validate on copy → commit → persist →
  reshaped triple); **FR-10** early checkpoint must trim `inputs` as well as `depends_on` (inferred edges) and persist the edit
  (`RunState.graph_edits`, else resume reverts it); **FR-12** extra checker rules (no duplicate output producer, no edge into a
  non-pending task, cycle check on a copy, no edits inside any route cone/loop clone); **FR-5/6** `start_new_session` + `killpg`
  TERM→grace→KILL (today's timeout kill orphans grandchildren), `idle_kill_minutes` default **15**, floor **12** (healthy max gap
  600.4 s = Bash 10-min cap, 0/34 196 gaps >15 min); **NFR-8** tick 0.35 ms / reshape 1.9 ms @1k tasks, `RunState.save` 10 ms @831 KB
  (save on mutation only); new finding: `ao run` has no signal handling — SIGTERM kills it instantly, leaves `state=running` and
  orphan `claude` children (T-Sn3Tck/T-Sn4Rec).

- By: architect · Role: agent · Date: 2026-11-09 · Comment: **T-Sn1Adr written** — ADR-0024 + `docs-md/sentinel-hld.md`
  (status Proposed). **Owner sign-off pending (checkpoint 2); not recorded here.** Needs owner confirmation on: signal handling
  for all runs (ADR D-10) and idle_kill_minutes floor 12.

- By: avadhoot (recorded by claude-sonnet-5-5) · Role: user · Date: 2026-11-09 · Comment: **OWNER SIGN-OFF: ADR-0024 + `docs-md/sentinel-hld.md` ACCEPTED.** D-10 (SIGTERM/SIGINT + process-group kill for ALL runs) CONFIRMED; `idle_kill_minutes` floor 12 with explicit `force` escape CONFIRMED. P1 engine work unblocked.

- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: **T-Sn3Tck done** — engine tick/inbox/live overrides/metrics/fail-safe (disabled path unchanged).
- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: **T-Sn2Set done** — sentinel settings on every CLI/env/config/workflow/template surface; engine only stores them.

## Task rollup
| Task | Phase | State |
|---|---|---|
| T-Sn0Spk-midflight-reshape-kill-spike | P0 | Done (GO-WITH-CHANGES) |
| T-Sn1Adr-sentinel-adr-and-hld | P0 | Done — ADR Accepted (owner sign-off 2026-11-09) |
| T-Sn2Set-sentinel-settings-plumbing | P1 | Done |
| T-Sn3Tck-main-loop-tick-inbox-overrides | P1 | Done |
| T-Sn3Ctl-ao-ctl-cli | P1 | Draft |
| T-Sn4Rec-liveness-kill-recovery-ladder | P1 | Draft |
| T-Sn5Ui1-sentinel-panel-and-control-api | P1 | Draft |
| T-Sn6Brr-soft-barrier-and-pending-edits | P2 | Draft |
| T-Sn7Bkl-backlog-and-early-checkpoint | P2 | Draft |
| T-Sn8Gen-llm-generations-pack-handoff | P3 | Draft |
| T-Sn9Pow-llm-powers-directives-routed | P3 | Draft |
| T-SnXAcc-e2e-and-live-smoke | P3 close | Draft |
| T-SnXDoc-docs-readme-learnings | each phase | Draft |

## Evidence
- Utilisation baseline (EPIC §1): 7–41% slot utilisation at P=6 over the last 10 overseer-runner runs; 50–80% of
  wave span is tail after the median unit finishes.
- Code findings (EPIC §2), notably DRAIN has no timeout (`engine.py:999`); no per-task kill or liveness.

## Risks / Blockers
- In-flight graph rebuild: verified safe with 6 invariants to add (I1–I6, spike RESULT.md); **owner checkpoint 1 (HANDOFF §4): not a no-go, so no fallback confirmation needed** — owner may still veto GO-WITH-CHANGES.

## Next actions
1. ~~T-Sn0Spk~~ done ∥ ~~E-aYG5vn T-zP28k9~~ done, ~~T-f0d7gm~~ done.
2. ~~T-Sn1Adr~~ signed off 2026-11-09 → P1 (T-Sn2Set in progress).
