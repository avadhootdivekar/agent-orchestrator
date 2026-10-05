# STATUS

- ID: `T-pQ73eO-spa-auth-gate-login`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (frontend lane F)
- Scope: `MVP` · Sprint: `S1` · Estimate: `3 d`

## This update
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Implemented end to end against the
  frozen HLD §2 contract with a mocked `fetch`; no Python touched; `ui/static` not committed.
  Design notes for downstream tasks (T-vCgsU6, T-R7JhTL):
  - **Reducer refinement of §17.2.** Session-issuing responses carry no `policy`/`session`/
    `transport`, so `LOGIN_OK(authenticated)`, `VERIFY_OK` and `ENROLL_DONE` go to `loading`, which
    re-fetches status and lands in `authenticated{status}`. A recovery-code notice rides on
    `loading{recoveryNotice}` and lands on `authenticated.recoveryNotice` (`remaining` =
    `response.user.recovery_codes_remaining ?? 0`). The table's "authenticated" for these rows is
    reached after that one status fetch.
  - **Added events** `RECHECK` (any state to `loading`; used by `pageshow` with `persisted: true`, so a
    bfcache restore re-fetches in every state, not just `authenticated`) and `RETRY` (error to
    loading). `disabled` + `SESSION_LOST` goes to `loading` (a 401 there means auth was switched on
    under a stale page); other unexpected events return the same state object.
  - **Single flight** is structural: the `loading` state triggers the one status fetch (guarded by an
    in-flight promise); N concurrent 401s dispatch `SESSION_LOST` N times and only the first changes
    state. The handler is registered once in `AuthGate` and cleared on unmount.
  - **`AuthContext`** (`ui/src/auth/context.ts`): `{status, refresh, logout, recoveryNotice}` plus a
    `useAuth()` hook; the default value means "auth off", so `<App/>` still renders without a gate.
    `refresh()` re-fetches status in place (no unmount). `logout(true)` rethrows on failure; `logout()`
    always lands on the login screen (the local proof is cleared in `authApi.logout`'s `finally`).
  - `enroll` view also carries `enrollmentTokenRequired` (additive) for EnrollScreen. All absent
    nullable status keys (incl. `enrollment_token_required`) are normalized to `null` by
    `normalizeStatus`.
  - Small extras that avoid duplication: `auth/messages.ts` (error code to §17.8 string),
    `auth/useFormFailure.ts` (429 countdown + error state shared by LoginScreen and TotpStep), a
    delimited `.auth-*` block appended to `ui/src/styles.css`, and `ui/src/test/fixtures/auth.ts`
    (shared mock-fetch builders). `fetch-mode-ban.test.ts` exports `findViolations` and scans via
    `import.meta.glob` (no `@types/node` in `ui/`); it already allowlists `hub/hubAuthCore.ts`.
  - `parseRetryAfter` prefers the `Retry-After` header and falls back to the body's
    `retry_after_seconds`; it tolerates responses without `headers` (hand-rolled doubles).
  - TotpStep only enables Verify for a 6-digit code (attempts are limited), sends it with whitespace
    stripped, and clears the field after a failure.
  - Vite dev proxy: `server.proxy["/api"].configure` sets `origin` to the target; the `proxyReq`
    typing is a local interface because the proxy's `EventEmitter` types need `@types/node`.
  - Not unit-tested: the header-merge branch of `request()` ("callers cannot drop Content-Type"),
    because no existing caller passes `headers` and `request` is not exported.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9). The
  estimate is unchanged (3 d). Changes:
  - **Bundle rule reversed (design-review M1/M2; HLD §16 row 15, cross-epic X5):** this task no
    longer commits `ui/static`. It builds locally, records the interim gzip delta and discards the
    output; T-vCgsU6 is the epic's single `ui/static` committer. This supersedes the v2 comment
    below. Description item 8, AC 10, Outputs and Verification updated.
  - **Type (security M2):** `types.ts` adds `transport.proxy_suspected` (manager-approved additive
    contract change); the banner logic is unchanged.
  - **Downstream:** T-R7JhTL's `auth-client-contract.test.ts` exercises this task's `api.ts` /
    `proof.ts` (design-review minor 7).
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2 rescope after the Phase-4
  consultations (HLD §28). The estimate is unchanged. Changes:
  - **Added: the session proof (D25 / ADR-0021 D11, dev-security #2).** That covers `proof.ts` (with
    a memory fallback and the blocked-storage notice), the `X-AO-Session-Proof` header on every
    request, storing `session_proof` from issuing responses, and clearing it on logout and on
    `anonymous`.
  - **Added: a `pageshow` bfcache re-check** (dev-security #7).
  - **Added: `fetch-mode-ban.test.ts`** with a file allowlist and a negative control
    (dev-security #13).
  - **Renamed: the second-factor state** to `second_factor_required` / `second_factor`.
  - **Moved out:** keepalive goes to T-vCgsU6, which balances the scope.
  - **Changed: the bundle rule.** This task now rebuilds and commits the SPA bundle, following HLD
    A-10. In v1 that was deferred to T-vCgsU6. The new CI rebuild-diff gate requires committed
    bundles to match a clean rebuild at every merge.
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the
  E-Da5Tn9 design package (design and tickets only; **no code written**). It can start immediately
  against the frozen HLD §2 contract, using mocked fetch.

## Evidence
- By: developer · Role: developer · Date: 2026-10-05 · Comment: commands run from the worktree root.
  - Baseline before the change: `npm --prefix ui run test` -> 35 files / 409 tests passed.
  - `npm --prefix ui run typecheck` -> clean (`tsc -b --noEmit`, no output).
  - `npm --prefix ui run test` (run 5 times, no flakes) -> **42 files / 543 tests passed**; the 35
    existing files pass unmodified (409 + 134 new). New files: `auth-reducer.test.ts`,
    `auth-gate.test.tsx`, `auth-api.test.ts`, `login-screen.test.tsx`, `totp-step.test.tsx`,
    `proof.test.ts`, `fetch-mode-ban.test.ts` (+ `fixtures/auth.ts`). Time-dependent tests
    (`auth-api`, `login-screen`, `totp-step`) use `vi.useFakeTimers()` + `vi.setSystemTime(...)`.
    No coverage gate is configured in `ui/` (none run).
  - `npm --prefix ui run build` -> OK (247 modules). Interim gzip (level 6, via a node script, as
    `gzip -c` loops are blocked in this sandbox), main chunk `index-*.js`:
    pre-epic committed bundle **132,953 B** -> **136,487 B** (**+3,534 B, +3.5 KB**, budget +12 KB for
    the whole epic); lazy `RunGraph-*.js` 78,336 -> 78,337 B (unchanged). Index CSS 5.41 KB gzip.
  - Discarded the build: `git checkout -- src/agent_orchestrator/ui/static` plus `git clean -fdq` on
    that path (removes the new hashed files). `git status` shows nothing under `ui/static`.
  - Bundle rule: `git diff --name-only 4d2aafd..HEAD -- src/agent_orchestrator/ui/static` prints
    nothing.
  - Dev proxy (partial, AC 11): ran `npx vite --port 5199` against a stub echo server on :8765 and
    POSTed `/api/auth/login` through it with `Origin: http://localhost:5199`; the upstream saw
    `Origin: http://127.0.0.1:8765` (rewritten), `Host: 127.0.0.1:8765`. The full manual login
    against a real `ao ui --auth` could not run: the backend lanes had not landed in this worktree.
    **Deferred to T-U2ERMo** (or the developer at integration) as the §16 #13 manual check.

## Risks / Blockers
- None. The HLD §2 contract is frozen (v2.1 adds only `transport.proxy_suspected`); further changes
  need a manager-approved entry in the epic STATUS. OQ-8 and OQ-9 are DECIDED (D25 ships, so the
  proof handling is in scope).

## Next actions
1. T-vCgsU6: plug `EnrollScreen`, `AccountMenu`, the recovery banner (`useAuth().recoveryNotice`) and
   `useKeepalive` into `AuthGate`/`context.ts`; add the remaining `authApi` calls with `postJson`;
   regenerate and commit `ui/static` as the epic's single committer.
2. T-R7JhTL: extend `fetch-mode-ban.test.ts` coverage to `hubAuthCore.ts` (already allowlisted) and
   write `auth-client-contract.test.ts` against `api.ts` / `proof.ts`.
3. T-U2ERMo / integrator: the manual `npm run dev` login against `ao ui --auth` (AC 11).
