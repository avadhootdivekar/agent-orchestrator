# STATUS

- ID: `T-SnXDoc-docs-readme-learnings`
- Updated At: `2026-11-09`
- State: `Partial`
- Owner: `unassigned`

## This update
- By: claude-opus-5-5 · Role: architect · Date: 2026-11-09 · Comment: Task created from epic breakdown (owner-approved design).

- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: Docs pass for P1-P3 as built: new `docs-md/guide-sentinel.md`; HLD status Accepted + §21 deviations table; sentinel params in both builtin template READMEs; soft-barrier/sentinel-origin section in the injection guide; README `ao ctl` line; `.ao/config.yaml` commented sample; learning LRN-20261109-sentinel-doc-reconcile. **Default flip NOT done** (AC2): no FR-23 smoke evidence. EPIC §11 ticked: spike, ADR sign-off, NFR-13 fixture, docs; rest unticked with reasons.

## Evidence
- `ruff check .` clean; `ruff format --check .` clean (700 files). `mypy .` fails on 1 benchmark-fixture duplicate-module error and `mypy src` shows 11 pre-existing errors (`_version.py` etc.) — not from this docs-only change. Targeted pytest (`-k "template or readme or sentinel_settings or run_options"`): 422 passed; `-k "readme or docs or template or ctl or sentinel"`: 573 passed, 1 failed (`test_sentinel_e2e_matrix.py::test_disabled_sentinel_equals_absent_state_golden`, another unit's uncommitted WIP in the shared tree). Full `pytest -q` and UI npm test/build not re-run (no src/ui change by this unit).
- Found: `ao ctl kill/release` --help still says "not_enabled_yet until … lands" (stale src string; follow-up, not edited here).

## Risks / Blockers
- None

## Next actions
1. After T-SnXAcc live smoke: flip mechanical default in both templates, tick remaining EPIC §11 boxes, fix stale `ao ctl` help text.
