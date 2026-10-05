# STATUS

- ID: `T-kzEzwy-auth-foundation`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (lane B)
- Scope: `MVP` · Sprint: `S1` · Estimate: `1 d`

## This update
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Implemented end to end; all ACs met.
  - Source: `errors.py` (+`EXIT_CONFIG = 78`, 6 added lines), `auth/__init__.py` (PEP 562 lazy
    skeleton, empty `_LAZY_EXPORTS`), `auth/constants.py`, `auth/errors.py`, `auth/seams.py`,
    `auth/model.py`.
  - Tests: `tests/auth/{__init__,helpers/__init__,helpers/core,test_foundation,test_import_boundary}.py`;
    `tests/conftest.py` (+20 lines, autouse `_hermetic_auth_env`).
  - Deviation to note: the full suite showed `tests/test_nfr2_regression_gate.py` (byte-identity of
    pre-epic test files) failing on the required `tests/conftest.py` edit. Added one justified
    entry to its `_EPIC_MODIFIED_PRE_EPIC_TESTS` (the gate's documented mechanism). No other
    existing test was touched.
  - Design notes: the constants pin test also asserts the set of public constants equals the
    pinned set, so adding a constant is a deliberate two-place edit. `SystemClock` keeps its
    "logged once" flag per instance (no module-global state). `LAYERS` marks `auth/__init__.py`
    as `Layer(4, "pkg")`; `from <package> import name` candidates are resolved to submodules only
    for known package bases, so attribute imports are not false positives.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9).
  Estimate unchanged (1 d).
  - `tests/auth/helpers.py` becomes the **`tests/auth/helpers/` package**, one module per owning
    task (design-review M2); this task owns the docstring-only `__init__.py` and `core.py`.
  - `AuditEventName` grows to **24** events (`auth.startup.disabled_by_config`,
    `auth.startup.totp_downgraded_by_config`; security M3).
  - `constants.py` gains the v2.1 §12.6 rows (security L1, M2, L4, L7).
  - The import-boundary skeleton knows `http/routes_second_factor.py` and `http/hub_routes.py`, and
    allows fastapi/starlette in the three route modules plus the middleware; `http/__init__.py`
    stays empty.
  - New downstream: T-Hd4wQ2-auth-browse-denial-log-scrub.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Task created in the v2 re-baseline.
  - **Why it exists:** in v1, `constants.py`, `errors.py` and `seams.py` were spread across S1
    tasks, and the hermetic env fixture landed too late (developer finding D-3, tester finding T-1).
  - **What it holds:** constants, error vocabulary (including `insecure_transport`), seams
    (`CLOCK_BOOTTIME`), the state and event vocabulary, the test helpers, the root hermetic fixture
    (moved here from T-PlEROT), and the import-boundary skeleton (reviewer finding R-1, rule R4).
  - Root `errors.py` gains `EXIT_CONFIG` (reviewer finding R-10).
  - Design and tickets only; **no code written**.

## Evidence
Commands run from the worktree root with `.venv/bin/...`.

- `.venv/bin/python -m pytest -q tests/auth` -> `183 passed, 1 warning` (the warning is the
  existing starlette/httpx deprecation notice).
- `.venv/bin/python -m pytest -q tests/auth --cov=agent_orchestrator.auth --cov-report=term-missing`
  -> `constants.py` 100 %, `errors.py` 100 %, `model.py` 100 %, `seams.py` 100 %,
  `__init__.py` 89 % (line 32, the success branch of the lazy export, unreachable while
  `_LAZY_EXPORTS` is empty); total 99 %.
- AC-7 whole suite, with the shell exporting `AO_UI_AUTH=1 AO_UI_AUTH_TOTP=required
  AO_AUTH_DIR=/nonexistent AO_AUTH_STATE_DIR=/nonexistent`:
  `.venv/bin/python -m pytest -q -p no:cacheprovider` -> `1 failed, 5339 passed, 10 skipped in
  668.56s`. The single failure was `tests/test_nfr2_regression_gate.py::TestPreEpicTestsUnedited`
  (the `tests/conftest.py` edit was undeclared). After declaring it:
  `AO_UI_AUTH=1 ... .venv/bin/python -m pytest -q tests/test_nfr2_regression_gate.py tests/auth`
  -> `190 passed`. Every other test passed with the hostile environment exported, so the fixture
  is hermetic. A child-pytest test (`test_hermetic_env_overrides_exported_shell_values`) also
  asserts this inside the auth suite.
- `.venv/bin/ruff check src/agent_orchestrator/auth src/agent_orchestrator/errors.py tests/auth
  tests/conftest.py tests/test_nfr2_regression_gate.py` -> `All checks passed!`
- `.venv/bin/ruff format --check` on the same paths -> clean.
- `.venv/bin/mypy src` -> only the 4 pre-existing `_version.py` errors; no error in
  `src/agent_orchestrator/auth/` or `errors.py`.
- `git diff --numstat src/agent_orchestrator/errors.py` -> `6 0` (additions only).

AC map: 1 `test_exit_config*`; 2 `test_constants_pin*` (+ completeness and derived checks); 3
`test_errors_*`; 4 `test_model_*`; 5 `test_seams_*`; 6 `test_helpers_*`; 7 `test_hermetic_env*`;
8 and 9 `test_import_boundary.py` (real tree, subprocess blocked-framework import, synthetic
violation tests for L0 -> L2, fastapi in a non-http module, http <-> cli, ui.security, R5, missing
LAYERS entry, imports in `http/__init__.py`); 10 as above.

## Risks / Blockers
- None. Lanes A, B, C and Q can start from this commit.
- Residual: `LAYERS` is stricter than the AC for R2 (any `agent_orchestrator.*` import outside the
  shared set and `ui.security` fails); a later task that needs another shared module must amend
  `SHARED_MODULES` in `test_import_boundary.py` deliberately.

## Next actions
1. manager: sync the epic `EPIC.md`/`STATUS.md` rollup; unblock lanes A, B, C and Q.
