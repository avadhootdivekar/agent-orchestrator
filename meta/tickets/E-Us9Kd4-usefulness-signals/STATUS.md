# STATUS

- ID: `E-Us9Kd4-usefulness-signals`
- Updated At: 2026-10-02
- State: Done (8/8 tasks done, incl. follow-ups FR-12/13)
- Owner: dev-epic

## This update
- By: dev-epic | Role: manager | Date: 2026-10-02 | Comment: All six tasks Done. Early gate (reviewer) outcome recorded in the HLD and incorporated; late gate (tester) e2e passed; dev-security review findings fixed.

## Evidence
- Full suite: 4692 passed, 8 skipped (baseline 4481/8). vitest 101 passed. `ruff check`/`ruff format --check` clean; mypy: only the 4 known `_version.py` errors.
- Live `ao ui` smoke + CliRunner e2e (tests/test_e2e_cli_{rate,survival,usage_join}.py, tests/ui/test_{feedback,usage}_api.py).

## Risks / Not done
- Non-MVP: prompt-similarity re-run detection, human-vs-agent authorship, pause event log. Survival is a content-match heuristic.
- Browser: Usage/run-detail rendered in headless Chrome; dark theme, outcomes section, live rating submit not visually verified.

## Next actions
1. User review of decisions in the final report; push/PR is the user's call.
- By: developer | Role: developer | Date: 2026-10-02 | Comment: T-Gh8Mn2 (FR-12) Done; T-for FR-13 still open.
- By: developer | Role: developer | Date: 2026-10-02 | Comment: T-Pr9Qs3 (FR-13) Done; run prompt in RunState + dashboard Prompt panel/list preview.

## Follow-up update (By: dev-epic | Role: manager | Date: 2026-10-02)
- T-Gh8Mn2 (record_git_heads opt-out) and T-Pr9Qs3 (run prompt in RunState + dashboard) Done. Final gates: pytest 4729 passed / 8 skipped; vitest 108 passed; ruff check clean; mypy only the 4 known _version.py errors.
