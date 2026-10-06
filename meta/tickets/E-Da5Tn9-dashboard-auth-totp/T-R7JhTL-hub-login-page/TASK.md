# TASK: T-R7JhTL-hub-login-page

## Metadata
- Task ID: `T-R7JhTL-hub-login-page`
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Owner: `developer` (frontend lane F)
- Created: `2026-10-04`
- Last Updated: `2026-10-05`
- Status: `Done`
- Estimate: `3 days` (v2: 2.5; +0.5 d for the v2.1 shared contract test) · Sprint `S1→S2` (lane
  F starts it after T-pQ73eO and finishes it early in S2; HLD §24.2–§24.3). It runs in parallel
  with the backend and depends only on the frozen HLD §2 contract, plus T-pQ73eO for the contract
  test only.

## Requirements Mapping
- Requirement IDs:
  - FR-6 (text-only enrollment), FR-15 (hub login UX), FR-21 (transport banner);
  - FR-27 (session proof, client side), FR-28 (enrollment-token step), FR-29 (`insecure_transport`
    message);
  - NFR-2 (budget), NFR-10 (accessibility).
- ACs: AC-9 (hub text-only part), AC-29 (`hub-auth.test.ts`, **v2.1** `auth-client-contract.test.ts`),
  AC-30 (`hub-auth.js` budget and build). Also the client half of AC-35: the hub script always sends
  the proof.
- Design:
  - HLD §1 D13, D20, D25; §2 (E1–E5, E9, E10, hub-only routes; §2.4–§2.5 v2.1 `transport.proxy_suspected`);
    §17.6 (**DOM contract**); §17.8 (exact strings); §17.9; §17.10 (v2.1 contract test); §17.11;
    §16 rows 12 (scripts keys), 12b (`tsconfig.json`), 14 and 15 (bundle rule), cross-epic X5.
  - HLD §28.9: design-review M2 (ownership), minor 7 (shared contract test); security M2 (type).
  - ADR-0021: D2 (fetch only, no `mode`, no form posts), D5 (enrollment token,
    `insecure_transport`), D11 (proof).

## Description
1. **`ui/src/hub/hubAuthCore.ts`.** Framework-free. Uses only DOM APIs: `textContent`, `hidden`,
   `disabled`, `addEventListener`, `createElement`. **No** `innerHTML`, `outerHTML`,
   `insertAdjacentHTML`, `document.write`, `eval` or `new Function`. No dependencies.
   - It exports `initHubAuth(doc: Document, win: Window): void`, which drives the HLD §17.6 page by
     element id.
   - **Proof handling (D25).** Keep the proof in `localStorage["ao-session-proof"]` (the frontend
     `PROOF_STORAGE_KEY`, §12.6), inside try/catch, with an in-memory fallback.
     - Store `session_proof` from **every** response that carries it: login, verify, confirm.
     - Send `X-AO-Session-Proof` on **every** `/api/*` call: status, login, verify, enroll,
       keepalive, logout.
     - Clear it on logout and whenever status reports `anonymous`.
     - Never log it, put it in a URL, or render it.
   - **Login page** (`#ao-auth` present):
     - On init, call `GET /api/auth/status`, with the proof if one is stored, and show the step for
       the state:
       - `authenticated` → `location.replace("/")`;
       - `second_factor_required` → the TOTP form;
       - `enrollment_required` → the enrollment-token form;
       - `anonymous` → clear the proof and show the login form.
     - Show `#ao-transport-warning` iff `transport.secure === false && transport.client_is_loopback === false`.
     - **Password step:** `POST /api/auth/login` with `{username, password}`.
       - `authenticated` → `location.replace("/")`;
       - `second_factor_required` → the TOTP form;
       - `enrollment_required` → the token form;
       - 403 `totp_required` → the §17.8 string.
     - **TOTP step:** `POST /api/auth/totp/verify` with `{code}`, or with `{recovery_code}` after the
       toggle.
       - Show the `invalid`/`replayed` strings and `attempts_remaining`.
       - On 429, run a countdown from `Retry-After` with submit disabled.
       - On a 401 `not_authenticated`, show "Your sign-in timed out…" and return to login.
       - "Sign out" posts logout, clears the proof and resets the page.
     - **Forced enrollment (text only):**
       1. `#ao-enroll-token-form` → `POST /api/auth/totp/enroll/begin` with `{enrollment_token}`.
       2. Show `#ao-enroll-secret` (grouped in 4s) and `#ao-enroll-uri` as text, each with a guarded
          `navigator.clipboard.writeText` Copy button.
       3. `#ao-enroll-form` → `POST /api/auth/totp/enroll/confirm` with `{code}`.
       4. Show the recovery codes as `<li>` elements (`textContent`).
       5. Continue is enabled only after `#ao-codes-ack`, then calls `location.replace("/")`.
       - A 401 `invalid_code` on begin shows the server's `detail`.
       - A 403 `insecure_transport` shows the §17.8 string.
   - **Index page** (`#ao-logout` present):
     - logout posts `/api/auth/logout` with the proof, clears the proof, then calls
       `location.replace("/login")`;
     - keepalive posts `/api/auth/keepalive` on `pointerdown`/`keydown`, at most once per
       `KEEPALIVE_MIN_INTERVAL_MS` (60 s), only while `document.visibilityState === "visible"`;
     - a keepalive 401 → `location.replace("/login")`.
   - **All requests:**
     - `fetch(path, {method, headers: {"Content-Type": "application/json", [SESSION_PROOF_HEADER]: proof}, body})`;
     - **no `mode` option, ever** (HLD §2.1);
     - every submit handler calls `preventDefault()`.
     - Codes without a §17.8 string show the server's `detail`.
2. **`ui/src/hub/hubAuth.ts`** is the build entry. It imports `./hub-auth.css` and calls
   `initHubAuth(document, window)` on `DOMContentLoaded`. It exports nothing, so the IIFE needs no
   global name.
3. **`ui/src/hub/hub-auth.css`.** Small, readable in light and dark via `prefers-color-scheme`, no
   external fonts, visible focus rings (NFR-10).
4. **`ui/vite.hub.config.ts`.** A library build of `src/hub/hubAuth.ts`:
   - `formats: ["iife"]`, `fileName: () => "hub-auth.js"`, `cssFileName: "hub-auth"`;
   - `outDir: "../src/agent_orchestrator/auth/assets"`, `emptyOutDir: false`;
   - minified, no sourcemap, no hashing.

   Also (this task is the single owner of these keys, HLD §16 rows 12 and 12b; design-review M2):
   - add `vite.hub.config.ts` and `src/hub/**` to `ui/tsconfig.json` `include`;
   - `ui/package.json` **`scripts` keys only**: `"build": "tsc -b && vite build && vite build --config vite.hub.config.ts"`
     and `"build:hub": "vite build --config vite.hub.config.ts"`. The `dependencies` entry
     (`qrcode-generator`) and the lock file belong to T-vCgsU6, which runs after this task.
5. Run the build and **commit only** the fixed-name `src/agent_orchestrator/auth/assets/hub-auth.js`
   and `hub-auth.css` (HLD A-10, §16 row 14). **Do not commit `src/agent_orchestrator/ui/static`**
   (v2.1 rule, §16 row 15 and cross-epic X5: only the last frontend task of the epic, T-vCgsU6,
   regenerates and commits it after merge). After a local `npm run build`, discard it with
   `git checkout -- src/agent_orchestrator/ui/static`.
6. **`ui/src/test/hub-auth.test.ts`** (vitest + jsdom):
   - load `ui/src/test/fixtures/hub-login.html`, a verbatim copy of the §17.6 skeleton, plus a small
     index fixture containing `#ao-logout`;
   - mock `fetch`, stub `location.replace`;
   - use `vi.useFakeTimers()` / `vi.setSystemTime()` for all time-dependent cases.
7. **NEW (v2.1, design-review minor 7; HLD §17.6, §17.10): `ui/src/test/auth-client-contract.test.ts`.**
   The SPA client (`ui/src/api.ts` + `ui/src/auth/proof.ts`, from T-pQ73eO) and `hubAuthCore.ts`
   duplicate the client protocol on purpose. One table of scenarios runs against **both** clients with
   a mocked `fetch`:
   - the proof header name and the storage key are equal (`X-AO-Session-Proof`, `ao-session-proof`);
   - `session_proof` from every issuing response (login, verify, confirm, and for the SPA also the
     account flows) replaces the stored proof;
   - the proof is sent on every `/api` call once stored;
   - it is cleared on logout and on an `anonymous` status;
   - the session-loss codes are exactly `SESSION_LOSS_CODES` (`not_authenticated`,
     `second_factor_required`, `enrollment_required`);
   - no `fetch` call passes a `mode` option;
   - the memory fallback works when `localStorage` throws.

   Write it after T-pQ73eO has landed (soft dependency, contract test only).
8. **Type subset (v2.1, security M2).** The copied §2.5 subset includes
   `transport.proxy_suspected: boolean` (manager-approved additive contract change). The banner rule
   is unchanged: show `#ao-transport-warning` iff `!secure && !client_is_loopback`; a proxied request
   now arrives with `client_is_loopback: false`, so the banner covers it.

## Inputs / Outputs
- **Inputs:** HLD §2, §17.6, §17.8, §17.9, §17.10, §17.11; T-pQ73eO's `api.ts` / `proof.ts` (contract
  test only).
- **Outputs:**
  - `ui/src/hub/hubAuthCore.ts`, `ui/src/hub/hubAuth.ts`, `ui/src/hub/hub-auth.css`
  - `ui/vite.hub.config.ts`, `ui/tsconfig.json` (include), `ui/package.json` (**scripts keys only**)
  - built and committed: `src/agent_orchestrator/auth/assets/hub-auth.js` and `hub-auth.css` (never
    `src/agent_orchestrator/ui/static`)
  - `ui/src/test/hub-auth.test.ts`, `ui/src/test/fixtures/hub-login.html`
  - **v2.1:** `ui/src/test/auth-client-contract.test.ts`

## Acceptance Criteria
1. **Login flow.**
   - Submit calls `fetch("/api/auth/login", {method: "POST", …})` with a JSON body.
   - `{"state": "second_factor_required", "session_proof": "P1"}` → the TOTP form is visible, the
     login form hidden, and `localStorage["ao-session-proof"] === "P1"`.
   - `authenticated` → `location.replace("/")` is called exactly once.
   - 403 `totp_required` → the exact §17.8 string.
2. **Proof.**
   - After P1 is stored, **every** later `fetch` call carries `X-AO-Session-Proof: P1`.
   - A verify response with `session_proof: "P2"` replaces it.
   - Logout and an `anonymous` status remove the key.
   - With `localStorage.setItem` throwing, the flow still completes, using the in-memory proof.
3. **TOTP errors.**
   - `invalid_code` with `reason: "replayed"` → the exact "already used" string.
   - `reason: "invalid"` → the invalid string including `attempts_remaining`.
   - A 429 with `Retry-After: 3` disables submit and counts 3 → 0 under fake timers, then re-enables.
   - The toggle sends `{recovery_code}`.
   - A 401 `not_authenticated` → the timeout notice plus the login form.
4. **Forced enrollment (AC-9, hub part).**
   - The token form shows first.
   - Begin is called with `{enrollment_token: "<typed>"}`.
   - On 200, `#ao-enroll-secret` holds the secret grouped in 4s and `#ao-enroll-uri` the URI. The
     page contains no `svg` or `canvas` (text only).
   - Confirm success fills 10 `<li>` elements. Continue stays disabled until the checkbox is ticked.
   - 403 `insecure_transport` → the exact §17.8 string.
5. **Injection safety.** A username, `detail` or code containing `<img src=x onerror=alert(1)>` is
   rendered as text. No `img` element exists, and `textContent` equals the input.
6. **Index page.**
   - Logout posts with the proof, clears it, and redirects to `/login`.
   - Keepalive under fake timers: pointer events every 10 s for 3 minutes → exactly 3 POSTs.
   - `document.visibilityState = "hidden"` → 0 POSTs.
   - A 401 → `location.replace("/login")`.
7. **Forbidden APIs.** A string check in `hub-auth.test.ts` over `ui/src/hub/**` finds no
   `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval(`, `new Function` and no
   `mode:` key. The no-`mode` rule is also enforced epic-wide by `fetch-mode-ban.test.ts`
   (T-pQ73eO).
8. **Budget (AC-30, hub part).**
   - `gzip -c src/agent_orchestrator/auth/assets/hub-auth.js | wc -c` ≤ 6144.
   - The number is recorded in STATUS.
9. **Build.**
   - `npm run build` emits the SPA (unchanged location) plus `hub-auth.js` and `hub-auth.css` with
     fixed names.
   - Once the built assets are committed, a fresh `npm run build` leaves
     `git status --porcelain src/agent_orchestrator/auth/assets` empty (reproducible; the same
     property T-U2ERMo's CI rebuild-diff step enforces).
   - **(v2.1)** The task's commits contain no path under `src/agent_orchestrator/ui/static`
     (`git diff --name-only <base>..HEAD -- src/agent_orchestrator/ui/static` prints nothing).
   - `npm run typecheck` is clean and `npm run test` is green.
10. **Accessibility.** Every input has a label; errors go to the `role="alert"` region; the code input
    keeps `inputmode="numeric"` and `autocomplete="one-time-code"` (asserted on the fixture after
    each step).
11. **Shared client contract (v2.1, design-review minor 7; AC-29 part).** `auth-client-contract.test.ts`
    runs every scenario of Description item 7 against both clients and passes. A deliberately broken
    variant (e.g. a client that does not clear the proof on `anonymous`) makes the corresponding row
    fail (negative control).
12. **Type (v2.1, security M2).** The hub type subset declares `transport.proxy_suspected`; a status
    fixture with `{secure: false, client_is_loopback: false, proxy_suspected: true}` shows the transport
    banner, and `{secure: false, client_is_loopback: true, proxy_suspected: false}` does not.

## Risks
- **Contract drift.** Build strictly against HLD §2 and the §17.6 element ids, which are frozen.
  T-KOv2qD's pytest and T-U2ERMo's browser smoke (AC-34) validate the script against the real
  server-rendered page.
- **CSP.** The page must need no inline script or style. Everything ships as external files, and the
  browser smoke checks the console for CSP violations.
- **The fixture and the server page diverging.** The fixture is a verbatim copy of §17.6. Any id
  change needs a manager-approved contract change.
- **SPA/hub client drift (v2.1).** The shared contract test (AC 11) pins both clients to one protocol.
- **Lane load.** The task straddles S1→S2 on lane F (HLD §24.3 capacity note).

## Dependencies
- **Upstream:** the frozen HLD §2 / §17.6 contract; T-pQ73eO (soft: needed only for
  `auth-client-contract.test.ts`).
- **Downstream:**
  - T-KOv2qD-hub-service-auth (serves `auth/assets/*`; wheel `artifacts`);
  - T-vCgsU6-spa-enroll-account-qr (v2.1: the single `ui/static` committer runs after this task, and
    adds the `qrcode-generator` dependency to the `package.json` this task edits);
  - T-U2ERMo-auth-e2e-regression-sweep (browser smoke, rebuild-diff gate).

## Pseudocode / Algorithm

```
STATE step ∈ {password, totp, enroll_token, enroll_confirm, codes}
FUNCTION api(method, path, body?) -> {ok, status, data, retryAfter}     # method: "GET" for status, else "POST"
  headers = {"Content-Type": "application/json"}; p = readProof(); IF p: headers[SESSION_PROOF_HEADER] = p
  r = AWAIT fetch(path, {method, headers, body: body === undefined ? undefined : JSON.stringify(body)})   # no `mode`
  data = TRY r.json() ELSE {}
  IF typeof data.session_proof == "string": writeProof(data.session_proof)
  RETURN {ok: r.ok, status: r.status, data, retryAfter: positiveInt(r.headers.get("Retry-After"))}
FUNCTION show(step): toggle `hidden` on #ao-login-form / #ao-totp-form / #ao-enroll (+ token vs confirm forms) / #ao-recovery-codes
FUNCTION showError(codeOrDetail): #ao-error.textContent = STRINGS[code] ?? data.detail; #ao-error.hidden = false
```

## Schemas / Interface Notes
- HLD §2.5 types. Copy the subset needed. This script shares no imports with the SPA, so the
  `SESSION_PROOF_HEADER` and `PROOF_STORAGE_KEY` values are duplicated as named constants in
  `hubAuthCore.ts`.
- DOM ids: HLD §17.6 (frozen).

## Handoff Boundary
- **Upstream:** the HLD contract.
- **Downstream:** the committed `auth/assets/hub-auth.js` and `hub-auth.css`. T-KOv2qD wires them into
  `/login` and the index head tags.

## Verification

```
cd ui
npm ci && npm run test -- hub-auth auth-client-contract && npm run typecheck && npm run build
gzip -c ../src/agent_orchestrator/auth/assets/hub-auth.js | wc -c                 # must be <= 6144
npm run build && git status --porcelain ../src/agent_orchestrator/auth/assets    # must print nothing
git checkout -- ../src/agent_orchestrator/ui/static                              # v2.1: never commit ui/static here
```

## Artifacts
- `meta/tickets/E-Da5Tn9-dashboard-auth-totp/T-R7JhTL-hub-login-page/`
