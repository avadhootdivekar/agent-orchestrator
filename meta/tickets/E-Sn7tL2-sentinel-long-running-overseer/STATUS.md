# STATUS

- ID: `E-Sn7tL2-sentinel-long-running-overseer`
- Updated At: `2026-11-09`
- State: `Draft` (ready for implementation)
- Owner: `avadhoot`

## This update
- By: claude-opus-5-5 · Role: architect · Date: 2026-11-09 · Comment: Rev 3. All owner decisions resolved (D1–D4,
  §10). 13 task folders created; final checklist in EPIC §11; combined implementation handoff in `HANDOFF.md`
  (co-delivery with E-aYG5vn).

## Task rollup
| Task | Phase | State |
|---|---|---|
| T-Sn0Spk-midflight-reshape-kill-spike | P0 | Draft |
| T-Sn1Adr-sentinel-adr-and-hld | P0 | Draft |
| T-Sn2Set-sentinel-settings-plumbing | P1 | Draft |
| T-Sn3Tck-main-loop-tick-inbox-overrides | P1 | Draft |
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
- In-flight graph rebuild safety unverified → T-Sn0Spk (fallback defined in EPIC §4.4.4).

## Next actions
1. T-Sn0Spk ∥ E-aYG5vn T-zP28k9.
2. T-Sn1Adr → owner sign-off → P1.
