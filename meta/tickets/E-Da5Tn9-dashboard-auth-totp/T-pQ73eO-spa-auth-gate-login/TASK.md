# TASK: T-pQ73eO-spa-auth-gate-login

## Metadata
- Task ID: `T-pQ73eO-spa-auth-gate-login`
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Owner: `developer` (frontend lane F)
- Created: `2026-10-04`
- Last Updated: `2026-10-05`
- Status: `Done` (2026-10-05; AC 11 manual dev-proxy login against a real `ao ui --auth` deferred to T-U2ERMo, see STATUS)
- Estimate: `3 days` · Sprint `S1`. It runs in parallel with the backend against the frozen HLD §2
  contract, using a mocked `fetch`.

## Requirements Mapping
- Requirement IDs:
  - FR-24 (login, second-factor step, global 401 handling);
  - FR-21 (transport banner);
  - FR-27 (session proof, client side);
  - NFR-1 (auth-off parity);
  - NFR-10 (accessibility).
- ACs:
  - AC-29: `auth-reducer`, `auth-gate`, `auth-api`, `login-screen`, `totp-step`, `proof` and
    `fetch-mode-ban`;
  - the client half of AC-35: the proof is always sent;
  - AC-30 build part: a local `npm run build` succeeds (v2.1: this task does **not** commit the
    bundle; T-vCgsU6 does).
- Invariants: S21 (client side: every `/api` call carries the proof).
- Design:
  - HLD §1 D20, D25; §2 (contract; §2.5 types verbatim, incl. v2.1 `transport.proxy_suspected`);
    §17.1–§17.4, §17.8–§17.10; §15 #7 and #8; §16 #11, #13 and **#15 (v2.1 bundle rule)**,
    cross-epic X5; §12.6 (frontend `PROOF_STORAGE_KEY`);
  - HLD §28.9: design-review M1/M2 (bundle ownership), security M2 (type);
  - ADR-0021 D2 (fetch only, no `mode`) and D11 (proof).

## Description
1. **`ui/src/types.ts`:** add the HLD §2.5 types verbatim: `AuthStatus`, `AuthStepResponse`,
   `AuthErrorCode`, `SESSION_LOSS_CODES`, `SESSION_PROOF_HEADER`, and the rest. **v2.1 (security
   M2; manager-approved additive contract change):** `AuthStatus.transport` is
   `{ secure: boolean; client_is_loopback: boolean; proxy_suspected: boolean } | null`. The
   transport-banner logic is unchanged (`!secure && !client_is_loopback`); a request through an
   unconfigured proxy now arrives with `client_is_loopback: false`, so the banner already covers it.
2. **`ui/src/auth/proof.ts`:**
   - `readProof()`, `writeProof(p)`, `clearProof()` and `proofStorageBlocked()`;
   - the key is `PROOF_STORAGE_KEY = "ao-session-proof"`, in `ui/src/auth/constants.ts`;
   - **every storage access sits in try/catch**, with an in-memory fallback;
   - `readProof()` reads storage **on every call**, so all tabs share the latest rotation (R15).
3. **`ui/src/api.ts`**, per HLD §17.3:
   - `ApiError` gains optional `code`, `retryAfterSeconds` and `extra`, and stays source-compatible
     with `new ApiError(message, status)`.
   - `request()`:
     - merges headers so that callers cannot drop `Content-Type`;
     - adds `X-AO-Session-Proof` whenever a proof is stored;
     - parses `code` and the `Retry-After` header;
     - calls the session-loss handler **only** for 401s whose `code` is in `SESSION_LOSS_CODES`;
     - after a 2xx, stores any `session_proof` in the body.
   - **Never** a `mode` option.
   - `setSessionLossHandler`.
   - `authApi` with `status`, `login`, `verifyTotp`, `verifyRecovery` and `logout(everywhere?)`.
     `logout` always calls `clearProof()`. T-vCgsU6 adds `enrollBegin`, `enrollConfirm`,
     `disableTotp`, `regenerateRecoveryCodes`, `changePassword` and `keepalive` with the same
     helper.
   - The existing `api.*` call sites are unchanged.
4. **`ui/src/auth/`:**
   - **`authReducer.ts`.** Pure. Implements the HLD §17.2 table, with the state `second_factor{username, factors}`.
     - `authenticated` carries `recoveryNotice?: {remaining}` when `VERIFY_OK` had
       `used_recovery_code: true`. T-vCgsU6 renders that banner.
   - **`context.ts`.** `AuthContext {status, refresh(), logout(everywhere?), recoveryNotice}`.
   - **`constants.ts`.** `PROOF_STORAGE_KEY`, `KEEPALIVE_MIN_INTERVAL_MS = 60_000` (used by
     T-vCgsU6), and the §17.8 strings as named constants.
   - **`AuthGate.tsx`:**
     - fetches status;
     - a 404 from an old backend → `disabled`;
     - an error shows a Retry button;
     - registers the **single-flight** session-loss handler (§17.4);
     - on status `anonymous` or `disabled` → `clearProof()`;
     - listens for `pageshow`: `event.persisted === true` → `onLoss("not_authenticated")`;
     - renders `<App/>` **only** in `disabled` or `authenticated`;
     - in `enroll` it renders a placeholder (the §17.8 "Forced enrollment intro" plus Sign out) until
       T-vCgsU6 plugs in `EnrollScreen`;
     - treats a missing `enrollment_token_required` (the disabled E1 body has no such key) as `null`.
   - **`LoginScreen.tsx`:**
     - the transport banner;
     - the blocked-storage notice when `proofStorageBlocked()` returns true: "This browser is
       blocking site storage, so you will need to sign in again after reloading the page.";
     - the 429 countdown;
     - 403 `totp_required` and the other §17.8 strings.
   - **`TotpStep.tsx`** (the second-factor step):
     - a 6-digit code entry, plus the "Use a recovery code instead" toggle;
     - the `invalid`/`replayed` strings and `attempts_remaining`;
     - a 401 `not_authenticated` → login with the timeout notice;
     - "Sign out".
5. **`ui/src/main.tsx`:** render `<AuthGate><App/></AuthGate>`. **`App.tsx` is not modified in this
   task.** T-vCgsU6 adds `AccountMenu` there.
6. **`ui/vite.config.ts`:** a dev-only `server.proxy["/api"].configure` hook that sets the `origin`
   header to the proxy target, so `npm run dev` passes the server's Origin check (HLD §15 #8).
7. **vitest**, under `ui/src/test/`: `auth-reducer.test.ts`, `auth-gate.test.tsx`,
   `auth-api.test.ts`, `login-screen.test.tsx`, `totp-step.test.tsx`, `proof.test.ts` and
   `fetch-mode-ban.test.ts`. **Every time-dependent test uses `vi.useFakeTimers()` and
   `vi.setSystemTime()`** (tester T-6).
8. **Build (v2.1 rule, HLD §16 row 15 and cross-epic X5; design-review M1/M2).** Run `npm run build`
   locally to verify the build and measure the interim gzip delta, then **discard** the output with
   `git checkout -- src/agent_orchestrator/ui/static`. This task does **not** commit
   `src/agent_orchestrator/ui/static/`: only the last frontend task of the epic, T-vCgsU6,
   regenerates and commits it after this task and T-R7JhTL have merged (hashed asset names would
   otherwise conflict).

## Inputs / Outputs
- **Inputs:** HLD §2, §17.
- **Outputs:**
  - `ui/src/types.ts`, `ui/src/api.ts`, `ui/src/main.tsx`, `ui/vite.config.ts`
  - `ui/src/auth/{proof,constants,context,authReducer}.ts`
  - `ui/src/auth/{AuthGate,LoginScreen,TotpStep}.tsx`
  - the seven test files above
  - **no** committed bundle (v2.1: `src/agent_orchestrator/ui/static/` is T-vCgsU6's)

## Acceptance Criteria
1. **Reducer.** Every row of the HLD §17.2 table is a table-driven test case. An unexpected event
   leaves the state unchanged (same object).
2. **AuthGate, disabled.** Status `{enabled: false, state: "disabled", …}` → `<App/>` renders.
   Exactly **one** extra request (`GET /api/auth/status`) is made, and it has **no** proof header
   when none is stored (NFR-1).
3. **AuthGate, other states.**
   - `anonymous` → `LoginScreen`, and `App` is not mounted (no `/api/runs` fetch).
   - Status 404 → `App` renders.
   - A network failure → an error plus Retry, and Retry re-fetches.
   - A `pageshow` event with `persisted: true` → exactly one status re-fetch; `persisted: false` →
     none.
4. **Single flight (`auth-api.test.ts`).**
   - 10 concurrent requests rejected with 401 `not_authenticated` → **exactly one**
     `GET /api/auth/status`.
   - `App` then unmounts, and after advancing fake timers by 10 × `POLL_MS` (from
     `ui/src/usePolling.ts`) there are no new fetches.
   - A 401 `invalid_credentials` or `invalid_code`, or any 403, does **not** call the handler.
   - `ApiError.code`, `retryAfterSeconds` and `extra` are parsed.
5. **Proof (`proof.test.ts`).**
   - After a login response with `session_proof: "P1"`, **every** later request carries
     `X-AO-Session-Proof: P1`. That includes status and an existing call such as `api.runs()`.
   - A verify response with `"P2"` replaces it.
   - `logout()` and `logout(true)` remove it, and so does a status of `anonymous`.
   - With `localStorage` throwing, requests within the page still carry the in-memory proof,
     `proofStorageBlocked()` is true, and `LoginScreen` shows the blocked-storage string.
   - No URL passed to `fetch` ever contains the proof value.
6. **`fetch-mode-ban.test.ts`** (dev-security #13).
   - Scan `ui/src/**/*.{ts,tsx}`, excluding `ui/src/test/**`.
   - Every file containing `fetch(` must be in the allowlist `{ui/src/api.ts, ui/src/hub/hubAuthCore.ts}`
     (a missing allowlisted file is fine).
   - No allowlisted file matches `/\bmode\s*:/`.
   - No scanned file uses `XMLHttpRequest` or `navigator.sendBeacon`, which would bypass the proof
     header.
   - A negative control proves that the checker flags the synthetic source
     `fetch(u, { mode: "cors" })`.
7. **LoginScreen.**
   - Submit sends `{username, password}`.
   - 401 → "Invalid username or password.".
   - 429 with `Retry-After: 5` → submit disabled, a countdown 5 → 0, then enabled.
   - `transport {secure: false, client_is_loopback: false}` → the §17.8 banner.
   - 403 `totp_required` → the §17.8 string.
   - `autocomplete` is `username` / `current-password`, and the first field is autofocused.
8. **TotpStep.**
   - A 6-digit submit sends `{code}`; the toggle sends `{recovery_code}`.
   - `reason: "replayed"` → the "already used" string; `attempts_remaining: 2` is shown.
   - A 401 `not_authenticated` → login with the timeout notice.
   - `inputMode="numeric"` and `autocomplete="one-time-code"`.
   - A response with `used_recovery_code: true` and `recovery_codes_remaining: 9` → the reducer
     state holds `recoveryNotice.remaining === 9`.
9. **Regression.**
   - Every existing vitest file passes unmodified.
   - `new ApiError("x", 404)` still compiles.
   - No `dangerouslySetInnerHTML` in `ui/src/auth/**` (string check).
10. **Gates.**
    - `npm run typecheck`, `npm run test` and `npm run build` are green.
    - **(v2.1)** The task's commits contain no path under `src/agent_orchestrator/ui/static`
      (`git diff --name-only <base>..HEAD -- src/agent_orchestrator/ui/static` prints nothing).
    - The interim main-chunk gzip delta against the pre-epic bundle (`bb6d8a0`) is recorded in
      STATUS. The final budget gate and the committed bundle are T-vCgsU6's.
    - **(v2.1)** `types.ts` declares `transport.proxy_suspected`; a status with
      `{secure: false, client_is_loopback: false, proxy_suspected: true}` shows the §17.8 transport
      banner on `LoginScreen`.
11. **Dev proxy.** With `npm run dev` against `ao ui --auth`, login from `http://localhost:5173`
    succeeds. This is a manual check per §16 #13, recorded in STATUS.

## Risks
- **Contract drift.** Build strictly against HLD §2.5. T-U2ERMo verifies against the real server.
- **Breaking the existing vitest suites.** They render `<App/>` directly. `AuthGate` is added only in
  `main.tsx`.
- **Multi-tab proof rotation (R15).** It is handled by reading storage on every request plus the
  single-flight re-check.

## Dependencies
- **Upstream:** the frozen HLD §2 contract.
- **Downstream:**
  - T-vCgsU6-spa-enroll-account-qr (enrollment, account menu, keepalive, budget, **the committed
    bundle**);
  - T-R7JhTL-hub-login-page (v2.1: its `auth-client-contract.test.ts` runs against this task's
    `api.ts` and `proof.ts`);
  - T-U2ERMo-auth-e2e-regression-sweep.
  - `fetch-mode-ban.test.ts` also covers T-R7JhTL's `hubAuthCore.ts` once it lands.

## Pseudocode / Algorithm
- HLD §17.2 (reducer table), §17.3 (`request()`), §17.4 (single flight and `pageshow`).
- Proof storage:

```
readProof():  TRY v = localStorage.getItem(KEY); RETURN v ?? memory  EXCEPT: blocked = true; RETURN memory
writeProof(p): memory = p; TRY localStorage.setItem(KEY, p) EXCEPT: blocked = true
clearProof(): memory = null; TRY localStorage.removeItem(KEY) EXCEPT: blocked = true
```

## Schemas / Interface Notes
- HLD §2.5 types; `SESSION_LOSS_CODES`; `SESSION_PROOF_HEADER = "X-AO-Session-Proof"`.
- §17.8 strings, verbatim.

## Handoff Boundary
- **Upstream:** the HLD.
- **Downstream:** `AuthContext`, `authApi`, `proof.ts` and the gate. T-vCgsU6 plugs `EnrollScreen`,
  `AccountMenu`, the recovery banner and `useKeepalive` into them.

## Verification

```
cd ui
npm ci && npm run test && npm run typecheck && npm run build
for f in ../src/agent_orchestrator/ui/static/assets/*.js; do echo "$f $(gzip -c "$f" | wc -c)"; done
git checkout -- ../src/agent_orchestrator/ui/static      # v2.1: never commit ui/static in this task
```

## Artifacts
- `meta/tickets/E-Da5Tn9-dashboard-auth-totp/T-pQ73eO-spa-auth-gate-login/`
