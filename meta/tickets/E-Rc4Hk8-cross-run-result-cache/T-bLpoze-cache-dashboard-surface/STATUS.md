# STATUS

- ID: `T-bLpoze-cache-dashboard-surface`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (Dev C)

## This update
- Implemented: `RunDetail.result_cache` / `TaskStat.result_cache` (lazy `cache.report` import only
  when `state.result_cache` is non-empty), the file-browser deny of `.orchestrator/cache`, the
  `cached` tag and "Result cache" tile (plain text only), and the rebuilt bundle (own commit).

## Evidence
- Commits on `worktree-agent-a18ce2c08e42a3a5a`: `faf8f57` (source + tests), `4e61e68` (bundle only,
  `src/agent_orchestrator/ui/static/**`).
- `ui/runs.py` +15 lines, `ui/files.py` +9 lines (import + 6-line deny hunk in `FileBrowser.resolve`,
  which backs `list_dir`, `read_file` and the html preview). `ui/app.py`, `ui/service.py`, auth code untouched.
- New tests: `tests/ui/test_result_cache_ui.py` (21: D-1a payload x7, D-1b deny incl. `..`, absolute,
  symlink to blob / dir, symlinked cache root, HTTP 403 on `/files`, `/files/content`, `/files/html`,
  U-LZ2 subprocess pair); `ui/src/test/run-detail-result-cache.test.tsx` (10: tag only on hit,
  `<img onerror>` renders as text, tile hits / shadow / null / absent).
- `pytest -q -p no:cacheprovider tests/ui tests/cache tests/test_spawn_provenance.py tests/test_nfr2_regression_gate.py`:
  all passed after the one amendment below. Dashboard CI step
  (`pytest tests/ui tests/test_general_instructions.py tests/test_e2e_cli_prompt_and_instructions.py -q --cov=agent_orchestrator.ui --cov-fail-under=80`):
  659 passed, 2 skipped, coverage 94.42% (files.py 97%, runs.py 99%).
- `ruff check src tests` / `ruff format --check src tests` clean; `mypy src tests/cache`: only the 4
  pre-existing errors in `src/agent_orchestrator/_version.py`. `npx vitest run`: 36 files / 419 tests passed
  (409 existing unedited + 10 new); `npm run typecheck` clean; `npm ci && npm run build` succeeds.
- Deviation: `tests/ui/test_run_graph_endpoint.py` (existing key-pin test) needed `result_cache` added to
  its two expected key sets (D-1a mandates the null keys); no other existing test edited.
- Full suite (`pytest -q -p no:cacheprovider`, after both commits): 6603 passed, 10 skipped, 0 failed (baseline 6572 + 10 G0 guard tests + 21 new; zero regressions).
- By: developer · Role: developer · Date: 2026-10-05

## Risks / Blockers
- None. Merge: drop `4e61e68` and re-run `npm ci && npm run build` after merging the sibling dashboard
  epics (E-Da5Tn9 touches the bundle); a textual conflict in `ui/files.py` `resolve()` is the only expected
  source conflict (additive hunk).

## Next actions
1. T-fXWbqg (G2): confirm text-only rendering, the deny-list (hard G2 exit item, now delivered) and the separate bundle commit.
2. Parent: rebuild the bundle after merging the sibling dashboard epics.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD §23.5). State stays `Draft`
  (surfaces); this file, `TASK.md`, `HANDOFF.md` (when present) and the epic `STATUS.md` rollup
  agree.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: G1b carry-over: the dashboard deny-list for `.orchestrator/cache` (rev S-5 / sec S-4) is a hard G2 exit item; the cache must not be merged to main switchable on before it lands. See `T-fXWbqg-cache-review-gates/STATUS.md` (G1b remediation).
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Done (commits `faf8f57`, `4e61e68`). The G1b hard G2 exit item (dashboard deny-list for `.orchestrator/cache`) is delivered. Rollup, `EPIC.md`, `TASK.md` and `HANDOFF.md` updated to match.
By: developer · Role: developer · Date: 2026-10-05 · Comment: G2 remediation: the `ui/files.py` cache deny-list compares casefolded path parts and a NUL byte in a path is a 403 instead of a 500 (rev G2-S2 / sec G2-N2); backend only, no bundle rebuild; tests in `tests/ui/test_result_cache_ui.py`. Task state unchanged. See `T-fXWbqg-cache-review-gates/STATUS.md` (G2 remediation).
