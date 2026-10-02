# EPIC: E-Us9Kd4-usefulness-signals

## Metadata
- Epic ID: `E-Us9Kd4-usefulness-signals`
- Title: Usefulness signals — verdicts, diff survival, local user feedback
- Owner: dev-epic
- Created: 2026-10-02
- Last Updated: 2026-10-02
- Status: In Progress (follow-up tasks FR-12/13 open)
- Branch: `ad/1-oct-enhancements` (local commits, not pushed)

## Summary
- Goal: tell whether workflows/models/efforts produce USEFUL work beyond pass/fail; local-first, no telemetry, backward compatible.
- Scope In: generic verdict capture (+overseer), `ao report-survival`, `ao rate` + implicit signals, join into `ao report-usage`, dashboard feedback + Usage view.
- Scope Out: LLM judge, bench reviewer calibration, N-repeat/paired bench, opt-in telemetry, prompt-similarity re-run detection, human-vs-agent authorship.
- Design: `docs-md/usage-signals-hld.md` (data model, schemas, algorithms, limits). Architect/reviewer early gate: reviewer pass on the HLD requested before implementation (outcome recorded in STATUS.md).

## Requirements (all MVP unless noted)
- FR-1: any workflow task can declare a non-gating verdict (TaskSpec.verdict_path / `*verdict.json` output / sibling table); report-usage attributes it. Verified by: unit + e2e.
- FR-2: overseer checkpoint + final-verify verdicts surfaced as per-run "outcome vs charter"; final-verify gets a non-gating machine-readable sidecar. Verified by: unit on real template fixtures + instruction test.
- FR-3: `ao report-survival` (+`--json`): per-task/per-run survival from git, isolation + serial + time-window attribution, worthless/reverted flags. Verified by: temp-git-repo tests.
- FR-4: backward-compatible recording of git heads (run start, task settle). Verified by: model round-trip + old-state load tests.
- FR-5: feedback.json store (atomic, bounded, validated) + `ao rate`. Verified by: unit + CliRunner e2e.
- FR-6: implicit signals (landed, follow-up commits, reverts, cancelled/breakers); unreliable ones listed Non-MVP.
- FR-7: report-usage per-group feedback counts, coverage, reviewer-error candidates, sample-floor flags.
- FR-8: survival joined into report-usage groups; degrades when git unavailable.
- FR-9: dashboard rating UI + endpoints sharing `feedback.py`; implicit/survival panel.
- FR-10: dashboard Usage view + `/api/usage`.
- FR-11: docs/CLI help updated; e2e through CliRunner; manual `ao ui` smoke.
- NFR-1 old runs load unchanged. NFR-2 deterministic (temp repos, fixed identity). NFR-3 bounded sizes, atomic writes, no path traversal. NFR-4 security tests (bad run id, oversize note, bad rating, Origin).
- Non-MVP/Stretch: see HLD "Non-goals / later".

## Task List (order; ∥ = parallel)
- [x] `T-Vd1Ab2-generic-verdict-capture` — FR-1, FR-2, NFR-1 (∥)
- [x] `T-Sv2Cd3-diff-survival-report` — FR-3, FR-4, NFR-2 (∥)
- [x] `T-Fb3Ef4-local-feedback-cli` — FR-5, FR-6, NFR-3 (∥)
- [x] `T-Jn4Gh5-usage-rollup-join` — FR-7, FR-8 (after the three above)
- [x] `T-Ui5Ij6-dashboard-usage-feedback` — FR-9, FR-10, NFR-4 (after join)
- [x] `T-Dc6Kl7-docs-e2e-verification` — FR-11 (last)
- [ ] `T-Gh8Mn2-record-git-heads-opt-out` — FR-12 (follow-up A)
- [ ] `T-Pr9Qs3-run-prompt-in-runstate-dashboard` — FR-13 (follow-up B)

## Risks
- Concurrent edits of models.py/cli.py by parallel tasks (mitigation: targeted edits, orchestrator commits).
- Survival heuristics are approximate (documented).
- Frontend build artifacts under `ui/static` are tracked and hash-named.

## Links
- Design doc: `docs-md/usage-signals-hld.md`; prior: `docs-md/usage-analytics.md`
