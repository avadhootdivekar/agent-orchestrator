# TASK: T-kzEzwy-auth-foundation

## Metadata
- Task ID: `T-kzEzwy-auth-foundation`
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Owner: `developer` (lane B)
- Created: `2026-10-05`
- Last Updated: `2026-10-05`
- Status: `Draft`
- Estimate: `1 day` · Sprint `S1`

## Requirements Mapping
- Requirement IDs: NFR-1 (hermetic fixture), NFR-3 (import-boundary skeleton), NFR-4 (seams and fakes), NFR-7 (named constants), FR-2 (`EXIT_CONFIG`), FR-20 (error details never carry secrets)
- ACs: AC-27 (skeleton part), AC-2 (hermetic-fixture part)
- Design: HLD §11.1 (layout, rules R1–R5), §11.2, §2.2 (error codes and HTTP status), §12.4 (event catalogue), §12.6 (constants), §16 rows 10 and 19, §20.2 (test helpers); HLD D15, D23, D24; ADR-0021 D1 (`CLOCK_BOOTTIME`), D6 (layering), D8 (`EXIT_CONFIG`)

## Description
This task lands the shared vocabulary that every other auth task imports. With it in place, S1
lanes A, B, C and Q can start in parallel on day 2 (developer finding D-3, tester finding T-1).
It contains no auth logic.

- **`src/agent_orchestrator/errors.py`** (shared, additive; ledger row 19): add
  `EXIT_CONFIG = 78` with a docstring (sysexits `EX_CONFIG`; used by `ao ui`, `ao service run`,
  `ao auth`, the supervisor and the systemd unit). Change nothing else in the file.
- **`auth/__init__.py`:** a module docstring and a PEP 562 `__getattr__` lazy re-export skeleton,
  following `ui/__init__.py`. Importing `agent_orchestrator.auth` MUST NOT import pydantic, yaml,
  fastapi, starlette or uvicorn.
- **`auth/constants.py`:** **every** constant of HLD §12.6 with its exact value, except
  `EXIT_CONFIG` (root `errors.py`) and the frontend-only constants (`ui/src/auth/constants.ts`).
  Group by area with one comment line per group. Byte sizes are written as `64 * 1024 * 1024`.
  **v2.1 (HLD §28.9)** this includes the new rows: `LOCKOUT_NAME_KEY_BYTES` (32),
  `INVALID_USERNAME_BUCKET` (`""`), `LOOPBACK_HOSTNAMES`, `FORWARDED_HEADER` /
  `FORWARDING_HEADER_PREFIX`, `SERVICE_ENV_RELATIVE_PATH` and `EPHEMERAL_PORT` (security L1, M2,
  L7, L4).
- **`auth/errors.py`** (HLD §11.2):
  - `ErrorCode` (StrEnum) with **all 22 codes of HLD §2.2**, including `INSECURE_TRANSPORT =
    "insecure_transport"` (present in §2.2 but missing from the §11.2 listing) and the reserved
    `FORBIDDEN`;
  - `STATUS_BY_CODE` (exactly the §2.2 table) and `DEFAULT_DETAIL` (one user-facing message per
    code, no `{` or `}`);
  - `AuthError(OrchestratorError)` with `code`, `detail`, `extra`, `headers`, `cause_for_log` and a
    `status` property; `TooManyAttemptsError`, `BusyError`, `StoreUnavailableError`;
  - `AuthConfigError(ConfigError)`, `AuthNotReadyError(AuthConfigError)`,
    `UnsafePermissionsError(AuthConfigError)`, `StoreCorruptError(OrchestratorError)`,
    `StoreLockTimeoutError(OrchestratorError)`.
- **`auth/seams.py`:** the `Clock` and `Entropy` protocols; `SYSTEM_CLOCK` (a `SystemClock`
  instance) whose `monotonic()` uses `time.clock_gettime(time.CLOCK_BOOTTIME)` when the attribute
  exists, else `time.monotonic()` with one DEBUG log line; `SYSTEM_ENTROPY`
  (`secrets.token_bytes`); `async def run_sync(fn, *args, **kwargs)`, which runs `fn` in the
  default executor.
- **`auth/model.py`:** `AuthMethod`, `TotpPolicy`, `SecondFactor`, `SessionState` (with
  `.api_state` and `.denial_code`), the module function `denial_code(state: SessionState | None)
  -> ErrorCode`, `TotpRequirement` (`none` / `enroll_allowed` / `blocked`), and `AuditEventName`
  (exactly the **24** events of §12.4; v2.1 adds `auth.startup.disabled_by_config` and
  `auth.startup.totp_downgraded_by_config`, security M3).
- **Tests and harness:**
  - `tests/auth/__init__.py`;
  - **v2.1 (design-review M2): the `tests/auth/helpers/` package**, one module per owning task, so
    S1 lanes never edit one file (HLD §20.2). This task creates `helpers/__init__.py` (empty apart
    from a docstring listing every module and its owner: `core.py` T-kzEzwy, `crypto.py` T-s6sJmB,
    `store.py` T-8NQP8J, `sessions.py` T-kwwJ82, `stub_runtime.py` and `enumeration.py` T-G7qByZ)
    and owns **`helpers/core.py`**: `FakeClock`, `SeededEntropy`, `run_async`, `make_client`,
    `same_origin_headers`. Tests import from the owning module
    (`from tests.auth.helpers.core import FakeClock`); there are no re-exports;
  - the **root** autouse fixture `_hermetic_auth_env` in `tests/conftest.py` (ledger row 10);
  - the `tests/auth/test_import_boundary.py` skeleton (R1, R4, R5 and a blocked-framework import).

## Inputs / Outputs
- **Inputs:** HLD §2.2, §11.1, §11.2, §12.4, §12.6, §16 rows 10 and 19, §20.2.
- **Outputs:**
  - `src/agent_orchestrator/errors.py` (+`EXIT_CONFIG`)
  - `src/agent_orchestrator/auth/__init__.py`, `constants.py`, `errors.py`, `seams.py`, `model.py`
  - `tests/conftest.py` (additive fixture only)
  - `tests/auth/__init__.py`, `tests/auth/helpers/__init__.py`, `tests/auth/helpers/core.py`
  - `tests/auth/test_foundation.py`, `tests/auth/test_import_boundary.py`

## Acceptance Criteria
1. `from agent_orchestrator.errors import EXIT_CONFIG` gives `78`. `git diff` of `errors.py` shows
   additions only. (`test_foundation.py::test_exit_config`)
2. **Constants pin.** A parametrized test lists every `(name, value)` pair of HLD §12.6, minus the
   frontend rows and `EXIT_CONFIG`, and asserts `getattr(constants, name) == value`. Derived checks:
   - `SESSION_TOKEN_B64_CHARS == SESSION_PROOF_B64_CHARS == 43`, which equals
     `len(base64.urlsafe_b64encode(bytes(32)).rstrip(b"="))`;
   - `MAX_PASSWORD_BYTES == 4 * MAX_LOGIN_PASSWORD_CHARS`;
   - every `AUTH_*_PATH` starts with `API_PREFIX + "/auth/"`;
   - `KNOWN_STORE_FEATURES == frozenset()`.
   (`test_foundation.py::test_constants_pin`)
3. **Errors.**
   - `{c.value for c in ErrorCode}` equals the 22 codes of §2.2 exactly.
   - `STATUS_BY_CODE` has an entry for every code and matches §2.2 (for example `INVALID_CODE` →
     401, `INSECURE_TRANSPORT` → 403, `BODY_TOO_LARGE` → 413, `BUSY` → 503).
   - No `DEFAULT_DETAIL` value contains `{` or `}`.
   - `AuthError(ErrorCode.INVALID_CODE)` has `.detail == DEFAULT_DETAIL[...]` and
     `.status == 401`. A `cause_for_log="SENTINEL"` value appears in neither `str(err)` nor
     `err.detail`.
   - `TooManyAttemptsError(30)` has `extra == {"retry_after_seconds": 30}` and
     `headers == {"Retry-After": "30"}`. `BusyError()` sends `Retry-After: 1`.
     `StoreUnavailableError()` sends `Retry-After: STORE_RETRY_AFTER_SECONDS`.
   - Class hierarchy: `AuthError` ⊂ `OrchestratorError`; `AuthConfigError` ⊂ `ConfigError`;
     `AuthNotReadyError` and `UnsafePermissionsError` ⊂ `AuthConfigError`.
   (`test_foundation.py::test_errors_*`)
4. **Model.**
   - `api_state` is `second_factor_required`, `enrollment_required` and `authenticated` for the
     three states.
   - `denial_code(None)` is `NOT_AUTHENTICATED`; `denial_code(PARTIAL_SECOND_FACTOR)` is
     `SECOND_FACTOR_REQUIRED`; `denial_code(PARTIAL_ENROLL)` is `ENROLLMENT_REQUIRED`.
     `SessionState.FULL.denial_code` raises `ValueError`.
   - `{e.value for e in AuditEventName}` equals the **24** events of §12.4 (v2.1), including
     `auth.startup.disabled_by_config` and `auth.startup.totp_downgraded_by_config`.
   - `TotpPolicy` is {`off`, `optional`, `required`}; `TotpRequirement` is {`none`,
     `enroll_allowed`, `blocked`}.
   (`test_foundation.py::test_model_*`)
5. **Seams.**
   - `SYSTEM_CLOCK.now_utc().utcoffset() == timedelta(0)`.
   - With `time.CLOCK_BOOTTIME` present, `monotonic()` returns the value of a patched
     `time.clock_gettime`. With the attribute removed by `monkeypatch.delattr`, it returns the
     value of `time.monotonic()` and logs exactly one DEBUG record across two calls.
   - `len(SYSTEM_ENTROPY.token_bytes(32)) == 32`.
   - `run_async(run_sync(fn, 1, k=2))` returns `fn(1, k=2)`, and `fn` runs on a thread other than
     the test thread.
   (`test_foundation.py::test_seams_*`)
6. **Helpers.**
   - `FakeClock`: `advance(10)` moves wall and monotonic time by 10 s; `advance_wall` and
     `advance_mono` move only one; `now_utc()` is timezone-aware.
   - `SeededEntropy(7).token_bytes(16)` is identical for two instances seeded 7 and differs for
     seed 8.
   - `make_client(app)` does not follow redirects, and a probe route sees `request.client.host ==
     "127.0.0.1"`.
   - `same_origin_headers(client, proof="p")` contains `Origin: http://testserver`,
     `Sec-Fetch-Site: same-origin` and `X-AO-Session-Proof: p`.
   - `tests/auth/helpers/` is a package; `helpers/__init__.py` defines no names (no re-exports),
     and its docstring lists each module with its owning task.
   (`test_foundation.py::test_helpers_*`)
7. **Hermetic fixture (AC-2 part).** Inside any test, `os.environ` has:
   - no key starting with `AO_UI_AUTH`;
   - no `AO_UI_BOUND_PORT` and no `AO_AUTH_STATE_DIR`;
   - `AO_AUTH_DIR` under the pytest tmp root.

   The **whole existing suite** passes with `AO_UI_AUTH=1`, `AO_UI_AUTH_TOTP=required`,
   `AO_AUTH_DIR=/nonexistent` and `AO_AUTH_STATE_DIR=/nonexistent` exported in the invoking shell.
   That run is recorded in STATUS. (`test_foundation.py::test_hermetic_env`)
8. **Import-boundary skeleton (AC-27 part).** `test_import_boundary.py` contains:
   - (a) a `LAYERS` table covering every module of HLD §11.1, including modules that do not exist
     yet (v2.1: also `http/routes_second_factor.py` and `http/hub_routes.py`). Any `auth/**.py`
     file missing from the table fails with "add it to LAYERS".
   - (b) An AST check of R4: a module imports only from its own or a lower layer, plus `errors`,
     `fsutil`, `xdg` and `project_config`. `http/*` and `cli.py` never import each other. Only
     `auth/http/middleware.py` may import `ui.security`.
   - (c) An AST check of R1: `fastapi` and `starlette` are imported only by
     `auth/http/middleware.py`, `auth/http/routes.py`, `auth/http/routes_second_factor.py` and
     `auth/http/hub_routes.py` (v2.1, design-review M2). `auth/http/__init__.py` stays empty (no
     imports), so importing a pure http module (`origin`, `responses`, `hub_page`) never pulls in
     fastapi.
   - (d) A subprocess that sets `sys.modules["fastapi"|"starlette"|"uvicorn"] = None`, then imports
     `agent_orchestrator.auth` and every existing non-http auth module. It must exit 0.
   - (e) An R5 check: no module other than `constants.py` contains the string literals `"ao_auth"`,
     `"auth_session"`, `"auth_enabled"` or `"auth_proof_ok"`.
   - The checker functions are unit-tested against synthetic modules in `tmp_path`. An L0 → L2
     import and a `fastapi` import in a non-http module both fail, so the skeleton is proven to
     catch violations.
9. A subprocess `import agent_orchestrator.auth` leaves `pydantic`, `yaml`, `fastapi`,
   `starlette` and `uvicorn` absent from `sys.modules`. (`test_import_boundary.py`)
10. `ruff check`, `ruff format --check` and `mypy` are clean. Line coverage of `constants.py`,
    `errors.py`, `seams.py` and `model.py` is ≥ 95 %.

## Risks
- **Constant drift between the HLD and the code.** The pin test (AC-2) makes any change a deliberate
  two-place edit.
- **An autouse fixture runs for every repository test.** It does environment operations only and
  never imports `agent_orchestrator.auth`, so non-auth tests are unaffected.
- **Later tasks extend `constants.py` and `model.py`.** Edits are additive and appended within
  their group; nothing is redefined.
- **The `LAYERS` table must list planned modules.** Later tasks then only create files, and the
  boundary test grows automatically.

## Dependencies
- **Upstream:** none.
- **Downstream:** every implementation task: T-s6sJmB, T-8NQP8J, T-kwwJ82, T-PlEROT, T-CsT5gk,
  T-XchniS, T-yfrfxv, T-G7qByZ, T-QJ1vyQ, T-rpKCjP, T-KQ6ZrY, T-j9dfsw, T-jVqH8w, T-KOv2qD,
  T-PDGw9p, and (v2.1) T-Hd4wQ2-auth-browse-denial-log-scrub. The tester T-U2ERMo reuses the
  helpers.

## Pseudocode / Algorithm
```text
SystemClock.monotonic():
  boot = getattr(time, "CLOCK_BOOTTIME", None)
  IF boot IS NOT None: RETURN time.clock_gettime(boot)       # counts suspend (D23)
  log_debug_once("CLOCK_BOOTTIME unavailable; using time.monotonic (may not count suspend)")
  RETURN time.monotonic()

async run_sync(fn, *args, **kwargs):
  RETURN await asyncio.get_running_loop().run_in_executor(None, functools.partial(fn, *args, **kwargs))

_hermetic_auth_env(monkeypatch, tmp_path):                   # tests/conftest.py, autouse
  FOR key IN list(os.environ): IF key.startswith("AO_UI_AUTH"): monkeypatch.delenv(key)
  monkeypatch.delenv("AO_UI_BOUND_PORT", raising=False); monkeypatch.delenv("AO_AUTH_STATE_DIR", raising=False)
  monkeypatch.setenv("AO_AUTH_DIR", str(tmp_path / "ao-auth-store"))   # state dir then resolves to <that>/state

layer_check(module_path, tree):                               # test_import_boundary.py
  own = LAYERS[rel(module_path)]   (missing -> FAIL "add it to LAYERS")
  FOR each Import / ImportFrom (relative imports resolved against the package):
     IF target in SHARED_ALLOWED: CONTINUE
     IF target startswith "agent_orchestrator.auth.": ASSERT LAYERS[target] <= own, and not (http <-> cli)
     ELIF target == "agent_orchestrator.ui.security": ASSERT module is auth/http/middleware.py
     ELIF target startswith "agent_orchestrator.": FAIL (R2)
     IF top-level target in {"fastapi", "starlette"}: ASSERT module in {http/middleware.py, http/routes.py}
```

## Schemas / Interface Notes
- Signatures: exactly HLD §11.2. `ErrorCode` adds `INSECURE_TRANSPORT`, as noted above.
- `FakeClock(start: datetime = datetime(2026, 1, 1, tzinfo=UTC), mono_start: float = 1000.0)`.
- `SeededEntropy(seed: int)` uses `random.Random(seed).randbytes(n)`. It is for tests only and is
  never imported by `src/`.
- `make_client` imports `starlette.testclient` inside the function, so `helpers/core.py` itself stays
  importable without the `[ui]` extra.

## Handoff Boundary
- **Upstream:** the HLD.
- **Downstream:** every auth module imports its constants, error codes, states and event names from
  here. No other module defines them. Every auth test uses the `tests/auth/helpers/` package
  (each module owned by one task; this task owns `__init__.py` and `core.py`).

## Verification
From the worktree root. In agent worktrees, use the manager-provided interpreter instead of
`uv run`.

```
python -m pytest -q tests/auth/test_foundation.py tests/auth/test_import_boundary.py
python -m pytest -q tests/auth --cov=agent_orchestrator.auth --cov-report=term-missing
python -m pytest -q                      # whole suite, once more with the AO_UI_AUTH* / AO_AUTH_* vars of AC-7 exported
ruff check src/agent_orchestrator tests/auth tests/conftest.py && ruff format --check src/agent_orchestrator/auth tests/auth && mypy src/agent_orchestrator/auth src/agent_orchestrator/errors.py
```

If the harness refuses inline environment assignments, export the AC-7 variables in a separate step
or use a small wrapper script.

## Artifacts
- Docs and comments: `meta/tickets/E-Da5Tn9-dashboard-auth-totp/T-kzEzwy-auth-foundation/`
- Large outputs: none
