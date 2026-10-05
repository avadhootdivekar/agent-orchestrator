# TASK: T-U2ERMo-auth-e2e-regression-sweep

## Metadata
- Task ID: `T-U2ERMo-auth-e2e-regression-sweep`
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Owner: `tester` (lane Q)
- Created: `2026-10-04`
- Last Updated: `2026-10-05`
- Status: `Draft`
- Estimate: `3 days` (v2: 2.5; +0.25 d store-busy CLI test from T-j9dfsw, +0.2 d informational
  NFR-5 measurements from T-G7qByZ; HLD §24.2) · Sprint `S3`

## Requirements Mapping
- Requirement IDs:
  - FR-20 (the sweep);
  - FR-11, FR-13 and FR-27 (real-process e2e, including the proof);
  - FR-15 and FR-24 (browser);
  - NFR-1 (auth-off regression), NFR-5 (informational measurement only; a target, not an AC),
    NFR-6 (coverage gate);
  - FR-22 (the store-busy CLI path, moved here from T-j9dfsw in v2.1).
- ACs:
  - AC-24 (scrub sweep);
  - AC-26 part (store busy: moved from T-j9dfsw AC 9, v2.1);
  - AC-30 (the CI rebuild-diff part);
  - AC-32 (coverage gate);
  - AC-33 (subprocess e2e);
  - AC-34 (browser smoke, including the cross-port proof check);
  - a regression re-run of every AC (AC-1..AC-46).
- Invariants: S11, S17, and S21 end to end; the v2.1 invariants S27–S30 are re-run in the full
  regression.
- Design:
  - HLD §20 (all; §20.3 #3, #13 and the v2.1 gates #15–#18), §21, §15 (caller-matrix
    re-verification), §16 #18 (`ci.yml`) and cross-epic rows X1–X6, §14.9 (hub flow), §25.4 (the
    browser premises being re-asserted); §3.5 NFR-5 (target); §24.2 (scope moved in);
  - HLD §28.9: design-review minor 4 (NFR-5 is a target; bundle baseline), security M2/M3 (new
    status and audit surfaces covered by the sweep);
  - ADR-0021 D11.

## Description
1. **`tests/auth/test_log_scrub_sweep.py`** (AC-24 / S11, §20.3 #13).
   - **Sentinels:**
     - a password `PW-<uuid4>` and a new password `NPW-<uuid4>`;
     - a known TOTP secret, and every code used;
     - all recovery codes;
     - the **enrollment tokens** printed by `add-user --require-totp`, `reset-2fa` and
       `enrollment-token`;
     - every **`session_proof`** issued;
     - the session cookie values;
     - the hash strings read back from `users.json`.
   - **Run every flow.**
     - **HTTP:**
       - login (password only, and with TOTP), recovery-code login;
       - forced enrollment with a token, voluntary enrollment;
       - disable, regenerate, password change, keepalive, logout, logout everywhere;
       - plus a failing variant of each: wrong password, wrong or replayed code, wrong or expired
         token, missing or wrong proof, `insecure_transport`.
     - **CLI:** `add-user` (with and without `--require-totp`), `set-password`, `enable-2fa`,
       `disable-2fa`, `reset-2fa`, `enrollment-token`, `unlock`, `revoke-sessions`, `remove-user`,
       `status` (incl. the v2.1 `disabled_by_config` / `totp_downgraded_by_config` flags),
       `list-users`.
     - **v2.1 surfaces:** `GET /api/auth/status` with `transport.proxy_suspected: true` (a
       loopback client sending `X-Forwarded-For`); the startup audit events
       `auth.startup.disabled_by_config` and `auth.startup.totp_downgraded_by_config` (via
       `prepare_auth`), which must carry no secret; the redaction under test is T-Hd4wQ2's
       `auth/scrub.py`.
   - **Run the sweep twice**, with `caplog` capturing at DEBUG for the root and every logger:
     - (a) with `install_log_redaction()` (the LogRecord factory) and the `auth_logger` filters;
     - (b) with the default record factory restored and every redaction filter removed.
   - **Assert that no sentinel appears in:**
     - log text: message, args, `exc_text`;
     - `audit.jsonl`, in the state directory;
     - any response body or header, **except** these allowed locations, checked field by field:

       ```
       ALLOWED_SECRET_LOCATIONS = {E4: {secret, otpauth_uri}, E5: {recovery_codes}, E7: {recovery_codes},
                                   E2/E3/E5/E6/E7/E8: {session_proof}}
       ```

       The cookie token is allowed only in its own issuing `Set-Cookie`.
     - any request URL;
     - CLI output, except the intended one-time printouts: `enable-2fa` (secret, URI, codes) and
       the token from `add-user --require-totp` / `reset-2fa` / `enrollment-token`.
2. **`tests/auth/test_e2e_subprocess.py`** (`-m e2e`, AC-33).
   - **Setup:** a temp workspace and a temp `--auth-dir`. Run `ao auth add-user alice --password-stdin`,
     then `ao auth enable-2fa alice` driven over stdin: parse the secret, send the code for the
     **current** step, and keep the recovery codes.
   - Start a real `ao ui --auth --workspace <ws> --auth-dir <dir> --port <free>` and poll the public
     `/api/health`.
   - Then, with `httpx`, sending `Origin: http://127.0.0.1:<port>`:
     1. status → `anonymous`;
     2. login → `second_factor_required`, keeping proof P1;
     3. verify with the code for **current step + 1**, computed immediately before →
        `authenticated`, keeping P2. The CLI enrollment already consumed the current step, and the
        server enforces strictly increasing steps. Step + 1 is inside the ±1 window and always
        greater, so the run can never hit a same-step replay. No other TOTP code is used in the
        test;
     4. `GET /api/runs` with the cookie and P2 → 200;
     5. the same **without** the proof → 401 `not_authenticated`, then **with** P2 again → 200 (a
        missing proof never destroys the session);
     6. logout with P2 → 200 plus `Clear-Site-Data: "cache"`;
     7. the old cookie plus P2 → 401;
     8. a second login uses a **recovery code**, never a second TOTP code →
        `recovery_codes_remaining` drops by one.
   - Terminate the server and assert it exits within its timeout.
3. **`tests/auth/test_browser_smoke.py`** (`-m browser`, opt-in, AC-34).
   - **Harness:** Playwright with system Chrome (`channel="chrome"` or
     `executable_path="/usr/bin/google-chrome"`), skipping when absent, the same pattern as
     `tests/ui/test_e2e_graph.py`. Firefox and WebKit also run when their Playwright browsers are
     installed; record which ones ran.
   - **CSP detector** on the console, with a **negative control**: an inline-script page must be
     detected.
   - **Dashboard:**
     - run `ao ui --auth --auth-totp required` with a user created by `add-user --require-totp`,
       keeping its token;
     - the login screen renders;
     - forced enrollment: token → `svg[role=img]` QR plus the secret plus the URI → confirm →
       recovery codes → runs view.
   - **Hub:** `build_hub_app` with auth and a fake status provider, served with uvicorn on a free
     port.
     - An anonymous `/` → `/login`.
     - Log in with a **recovery code**, to avoid a same-step replay.
     - The index shows "Signed in as".
   - **Cross-port proof check (S21):**
     1. After the dashboard login, navigate the tab to a capture listener (stdlib `http.server`) on
        another free localhost port B.
     2. Assert that B **received** `ao_sid_<A>`. This re-asserts the A4 premise from §25.4.
     3. Replay that cookie with `httpx` to `http://127.0.0.1:<A>/api/runs`, with a same-origin
        `Origin` and **no proof** → 401 `not_authenticated`.
     4. Navigate back to A: data still loads, so the victim's session survived.
   - **Screenshots:** `output/E-Da5Tn9-dashboard-auth-totp/{login,enroll,hub-login,hub-index}.png`,
     **new files only**. Never modify any other `output/**` PNG.
4. **CI** (`.github/workflows/ci.yml`, §16 #18):
   - (a) in the `test` job, next to the per-package gates:
     `pytest tests/auth -q --cov=agent_orchestrator.auth --cov-report=term --cov-fail-under=90`;
   - (b) in the `frontend` job (working directory `ui`), right after `npm run build`:
     `git diff --exit-code -- ../src/agent_orchestrator/ui/static ../src/agent_orchestrator/auth/assets`
     (dev-security #13).
5. **Full regression:**
   - `pytest -q` (the whole suite) with `AO_UI_AUTH=1` and `AO_AUTH_DIR=/nonexistent` exported in the
     invoking shell. This proves T-kzEzwy's hermetic fixture.
   - `pytest -q -m e2e`; ruff, ruff format and mypy;
   - vitest, typecheck, build and `npm audit`.
   - Re-verify the HLD §15 caller matrix rows #1–#19 against the merged code. **(v2.1)** If the
     approval-gates epic has merged by then, also record the as-merged state of the HLD §16
     cross-epic rows X1–X6 (one `xdg.resolve_config_dir`; the approvals denial goes through
     `FileBrowser._is_denied`; the bundle was regenerated, not hand-merged; the auth-off header
     snapshot baseline per X4).
6. **Store-busy CLI test (v2.1; moved from T-j9dfsw AC 9).** In `tests/auth/test_cli_e2e_busy.py`
   (a new file, so T-j9dfsw's `test_cli_e2e.py` keeps a single owner): a spawn-context process holds
   `users.lock`; with `STORE_LOCK_TIMEOUT_SECONDS` patched to 0.2, `ao auth set-password alice`
   (CliRunner, `--password-stdin`) exits 1 within 2 s and the message names the lock file and
   "busy". `join(timeout=…)` with exit-code asserts; the holder is released in a `finally`.
7. **Informational NFR-5 measurements (v2.1; moved from T-G7qByZ AC 14; design-review minor 4).**
   `tests/auth/test_perf_informational.py` (`-m slow`): p95 of `classify` + `sessions.lookup` +
   `revalidate` over 1000 requests on the dashboard app, and login p95 with the real hasher at
   `CURRENT_PARAMS` over 20 logins. The numbers are **recorded in STATUS only**; the test never
   asserts a threshold and is never a merge gate (NFR-5 is a target).

## Inputs / Outputs
- **Inputs:** every implementation task (#6–#18 and the v2.1 #22 T-Hd4wQ2); HLD §20 and §21.
- **Outputs:**
  - the three test files, plus (v2.1) `tests/auth/test_cli_e2e_busy.py` and
    `tests/auth/test_perf_informational.py`, and the two `ci.yml` steps;
  - the four screenshots;
  - in STATUS: an evidence table (commands, pass/fail/skip counts, coverage numbers, the three e2e
    runs, which browsers ran) and the caller-matrix table.

## Acceptance Criteria
1. **AC-24.** The sweep passes in **both** modes. A leak found in mode (b) is a real leak, because
   redaction is only defence in depth. Each one is filed back to its owning task as a **must-fix**
   and listed in the epic STATUS.
2. **AC-33.** The subprocess e2e passes **3 consecutive times**. Steps 5 (proof) and 8 (recovery)
   are asserted explicitly. The three runs are recorded.
3. **AC-34.** The browser smoke passes locally with system Chrome:
   - the screenshots exist;
   - zero CSP violations, and the negative control fires;
   - the cross-port replay returns 401, and the victim's session still works.

   It is opt-in and not part of CI.
4. **AC-32.** Both CI steps exist.
   - A local run of step (a) shows `agent_orchestrator.auth` coverage ≥ 90 %.
   - The UI gate (`--cov=agent_orchestrator.ui`) shows ≥ 80 %.
   - Both numbers are recorded.
5. **AC-30, CI part.** After a clean `npm ci && npm run build` on the merged tree,
   `git diff --exit-code` over `ui/static` and `auth/assets` exits 0.
6. **Regression.** The full suite (with the auth env exported), `-m e2e`, ruff, mypy, vitest,
   typecheck, build and `npm audit` are all green, and the counts are recorded.
7. **Caller matrix.** The re-verification table is recorded. Each §15 row is marked either "as
   designed" or as a deviation with a ticket reference. (v2.1) If the approvals epic has merged, the
   §16 X1–X6 rows are recorded the same way.
8. **Store busy (v2.1).** Description item 6 passes; the run time is under 2 s.
9. **NFR-5 numbers (v2.1).** Description item 7 runs and its numbers are recorded in STATUS; no
   threshold is asserted.
10. **v2.1 sweep surfaces.** The sweep (AC 1) covers `proxy_suspected` status responses and the two
    `auth.startup.*_by_config` audit events with no sentinel leak.

## Risks
- **Flakiness from real time or a real browser.**
  - Over HTTP, TOTP is used once per e2e run, at step + 1 after the CLI enrollment, and recovery
    codes after that.
  - In the browser smoke, enrollment completes the login, and the hub login uses a recovery code.
  - The browser smoke is opt-in, and processes run with explicit timeouts.
- **Leaks found late** are must-fix and route back to the owning task, tracked in the epic STATUS.
- **CI rebuild diffs** from non-deterministic bundling. If they happen, pin Node in CI to the version
  that produced the committed bundle, and record that in STATUS.

## Dependencies
- **Upstream:** T-XchniS, T-yfrfxv, T-G7qByZ, T-QJ1vyQ, T-rpKCjP, T-KQ6ZrY, T-j9dfsw, T-jVqH8w,
  T-KOv2qD, T-PDGw9p, T-R7JhTL, T-pQ73eO, T-vCgsU6, and (v2.1) **T-Hd4wQ2-auth-browse-denial-log-scrub**
  (its `auth/scrub.py` feeds the sweep; its file-browser denial is part of the regression).
- **Downstream:** T-2wE08U-auth-security-review.

## Pseudocode / Algorithm
- The sweep: HLD §20.3 #13. The proof matrix: §20.3 #3. The harness rules: §20.2. The hub flow:
  §14.9.
- Field-by-field allow check:

```
FOR each captured response (endpoint E, body JSON, headers):
  FOR each sentinel s:
    hits = paths in body where s occurs
    ASSERT hits ⊆ ALLOWED_SECRET_LOCATIONS[E]     # e.g. {"session_proof"} for E2
    ASSERT s not in any header value, except the cookie token in its own issuing Set-Cookie
```

## Schemas / Interface Notes
- `ALLOWED_SECRET_LOCATIONS` as above. Nothing else may carry a secret.
- Screenshot paths: exactly the four names above.

## Handoff Boundary
- **Upstream:** the implemented epic.
- **Downstream:** the evidence package for the security review (T-2wE08U).

## Verification

```
python -m pytest -q tests/auth/test_log_scrub_sweep.py tests/auth/test_cli_e2e_busy.py
python -m pytest -q -m slow tests/auth/test_perf_informational.py # informational; record numbers
python -m pytest -q -m e2e tests/auth/test_e2e_subprocess.py      # three consecutive runs
python -m pytest -q -m browser tests/auth/test_browser_smoke.py   # opt-in, local
python -m pytest tests/auth -q --cov=agent_orchestrator.auth --cov-report=term --cov-fail-under=90
AO_UI_AUTH=1 AO_AUTH_DIR=/nonexistent python -m pytest -q
ruff check . && ruff format --check . && mypy src
cd ui && npm ci && npm run test && npm run typecheck && npm run build && npm audit --omit=dev --audit-level=high && git diff --exit-code -- ../src/agent_orchestrator/ui/static ../src/agent_orchestrator/auth/assets
```

## Artifacts
- `output/E-Da5Tn9-dashboard-auth-totp/{login,enroll,hub-login,hub-index}.png`
- `meta/tickets/E-Da5Tn9-dashboard-auth-totp/T-U2ERMo-auth-e2e-regression-sweep/`
