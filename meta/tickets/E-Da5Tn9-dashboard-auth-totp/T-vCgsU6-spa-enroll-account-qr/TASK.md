# TASK: T-vCgsU6-spa-enroll-account-qr

## Metadata
- Task ID: `T-vCgsU6-spa-enroll-account-qr`
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Owner: `developer` (frontend lane F)
- Created: `2026-10-04`
- Last Updated: `2026-10-05`
- Status: `Draft`
- Estimate: `3 days` · Sprint `S2`

## Requirements Mapping
- Requirement IDs:
  - FR-6 (QR plus secret and URI);
  - FR-18 (re-authentication UI);
  - FR-24 (forced enrollment, account menu);
  - FR-28 (enrollment-token field), FR-29 (`insecure_transport` message);
  - FR-11 (keepalive, client side);
  - NFR-2 (dependency and budget), NFR-10.
- ACs:
  - AC-9 (SPA part);
  - AC-29: `enroll-screen`, `qr-code`, `account-menu`, `keepalive`;
  - AC-30 (SPA bundle, budget and audit part).
- Invariants: S20 (client side: an idle tab never slides the session).
- Design:
  - HLD §1 D20; §2.4 (E4–E10, including the forced and voluntary E4 bodies); §17.1, §17.5,
    §17.7–§17.11; §16 #11, #12 (dependency + lock only) and **#15 (single `ui/static` owner)**,
    cross-epic X5;
  - HLD §28.9: design-review M1/M2 (bundle ownership), minor 4 (baseline check);
  - ADR-0021 D5 (enrollment token, `insecure_transport`).

## Description
0. **FIRST STEP — baseline check (v2.1, design-review minor 4; HLD §16 row 15, AC-30).** Before any
   change, on the pre-epic baseline (`main` at the epic's branch point), run
   `cd ui && npm ci && npm run build && cd .. && git diff --exit-code -- src/agent_orchestrator/ui/static`
   and record the command and its result in STATUS. If the diff is **not** clean (the committed
   bundle does not equal a clean rebuild for reasons unrelated to auth), **stop and escalate to the
   manager** before anyone relies on the CI rebuild-diff gate (T-U2ERMo, §16 row 18).
1. **Dependency.** Add `qrcode-generator` pinned to **exactly** `"2.0.4"` in `ui/package.json`
   `dependencies`, and update `package-lock.json`. `npm audit --omit=dev --audit-level=high` must be
   clean. **v2.1 (§16 row 12):** this task owns only the `dependencies` entry and the lock file; the
   `scripts` keys belong to T-R7JhTL.
2. **`ui/src/auth/QrCode.tsx`** is the **only** importer of `qrcode-generator`. It is a default
   export, loaded with `React.lazy(() => import("./QrCode"))`.
   - Use `qrcode(0, QR_ECC_LEVEL)` → `addData(uri)` → `make()`.
   - Build a single `<path d>` from `isDark(r, c)`, with a `QR_QUIET_ZONE_MODULES` quiet zone.
   - Render `<svg role="img" aria-label="QR code for the setup URI">` at `QR_RENDER_PX`, black on white
     in both themes (§17.7).
   - **Never** `createSvgTag`, `createImgTag` or `dangerouslySetInnerHTML`.
3. **`EnrollScreen.tsx`** has two modes.
   - **Forced** (gate state `enroll`): first an **enrollment-token** field (the §17.8 prompt), then
     `authApi.enrollBegin({enrollment_token})`. This replaces T-pQ73eO's placeholder.
   - **Voluntary** (from the account menu, via `EnableTotpDialog`): the current password first, then
     `enrollBegin({current_password})`.
   - After begin: the QR (with a `Suspense` fallback), **always also** the secret grouped in 4s and
     the URI as text, each with a Copy button.
   - Then `enrollConfirm({code})` → `RecoveryCodes.tsx`. That shows an ordered list, Copy all, and
     Download `.txt` (Blob URL, revoked after use). Continue is gated by "I have stored these codes".
   - Error strings (§17.8):
     - 403 `insecure_transport` → its string;
     - 401 `invalid_code` on begin (bad token) → "That code didn't work…" or the server `detail`;
     - 401 `invalid_credentials` (voluntary) → stay on the password step.
4. **`AccountMenu.tsx`** sits in the `App.tsx` sidebar footer and renders only when
   `status.enabled`. This is the one additive `App.tsx` edit (§16 #11). It contains:
   - the username and `auth_method`;
   - **Change password** (`ChangePasswordDialog`): policy hints from `status.policy`, and the
     `violations` list on 400 `password_policy`;
   - **Two-factor:**
     - Enable, when `can_enroll_totp` → `EnableTotpDialog`;
     - Disable, when `can_disable_totp` → `DisableTotpDialog` (password plus code). A 403
       `totp_required` → the server `detail`.
     - Regenerate codes, when enrolled → `RegenerateCodesDialog` (password plus code), which then
       shows the new codes behind the acknowledgement gate. A 403 `insecure_transport` → its string.
   - **Log out** and **Log out everywhere** (`{everywhere: true}`).
   - Explicit logout also clears the `ao-tabs` localStorage key and the `LAST_LAUNCH_KEY_PREFIX*`
     sessionStorage keys (guarded), and the proof (via `authApi.logout`).
5. **Recovery banner.** Render `AuthContext.recoveryNotice` as the §17.8 "After a recovery-code
   login" string, dismissible.
6. **`useKeepalive.ts`** (§17.5):
   - active only in `authenticated`;
   - passive listeners for `pointerdown`, `keydown`, `wheel` and `touchstart`, plus
     `visibilitychange` when the page becomes visible;
   - posts when visible **and** `now - lastSent >= KEEPALIVE_MIN_INTERVAL_MS`;
   - no timers while idle; errors are ignored, because 401s are handled globally.
7. **`authApi` additions** (same `request()` helper, so the proof header and rotation are
   automatic): `enrollBegin`, `enrollConfirm`, `disableTotp`, `regenerateRecoveryCodes`,
   `changePassword`, `keepalive`. Use the HLD §2.4 bodies.
8. **Build — the epic's only `ui/static` committer (v2.1, HLD §16 row 15; design-review M1/M2).**
   After T-pQ73eO **and** T-R7JhTL have merged, run `npm ci && npm run build` on the merged `ui/src`
   and **commit** the rebuilt SPA bundle under `src/agent_orchestrator/ui/static/` (stage the whole
   directory, including deleted hashed assets). No other task of this epic commits it. Measure and
   record the gzip size of every emitted chunk against the pre-epic baseline (`bb6d8a0`).
   **Cross-epic X5:** if the approvals epic (`T-pIZq3q`) has also changed `ui/src`, the bundle is
   regenerated once from the merged sources at the second epic's merge; hashed assets are never
   hand-merged.
9. *(Moved in v2.1 to T-otjIkJ:* the `ui/README.md` runtime-dependency entry for `qrcode-generator`
   and the layout lines. Record the measured gzip sizes in STATUS so T-otjIkJ can cite them.)
10. **vitest**, with fake timers wherever time matters: `enroll-screen.test.tsx`, `qr-code.test.tsx`,
    `account-menu.test.tsx`, `keepalive.test.ts`.

## Inputs / Outputs
- **Inputs:** T-pQ73eO (gate, context, `authApi`, `proof.ts`, `constants.ts`); T-R7JhTL (merged
  `package.json` scripts and hub build, so the final bundle includes everything); HLD §2 and §17.
- **Outputs:**
  - `ui/src/auth/{QrCode,EnrollScreen,RecoveryCodes,AccountMenu,ChangePasswordDialog,DisableTotpDialog,RegenerateCodesDialog,EnableTotpDialog}.tsx`
  - `ui/src/auth/useKeepalive.ts`
  - `ui/src/api.ts` (additions), `ui/src/App.tsx` (AccountMenu and banner)
  - `ui/package.json` (`dependencies` entry only) + lock
  - the rebuilt, committed bundle (`src/agent_orchestrator/ui/static/**`; sole owner) and the four
    test files
  - STATUS: the baseline-check result (Description item 0) and the gzip numbers

## Acceptance Criteria
1. **Forced enrollment (AC-9, SPA part).**
   - With a mocked status of `enrollment_required`, the token field shows first.
   - Begin is called with `{enrollment_token}` exactly.
   - On 200: the QR (mocked `QrCode`), the grouped secret **and** the URI all render.
   - Confirm with a code → 10 codes render. Continue is disabled until the checkbox is ticked, and
     then the gate becomes `authenticated`.
   - A 403 `insecure_transport` on begin → the exact §17.8 string, and no QR.
2. **Voluntary enrollment** requires the password field before begin (`{current_password}`). A 401
   `invalid_credentials` shows the error and stays on the password step.
3. **QrCode** (real library, jsdom), for
   `otpauth://totp/ao%40devbox:alice?secret=JBSWY3DPEHPK3PXP&issuer=ao%40devbox&algorithm=SHA1&digits=6&period=30`:
   - exactly one `<svg role="img">`;
   - its `viewBox` side equals module count + 2 × quiet zone;
   - a non-empty `<path d>`.

   A string test on the `QrCode.tsx` source finds no `dangerouslySetInnerHTML`, `createSvgTag` or
   `createImgTag`.
4. **Account menu.**
   - A 400 `password_policy` with `violations: ["too_short"]` shows the hint; a 200 closes the
     dialog.
   - Disable is hidden when `can_disable_totp` is false.
   - Regenerate shows the new codes behind the acknowledgement gate.
   - Log out everywhere sends `{everywhere: true}`.
   - Explicit logout removes `ao-tabs`, the last-launch keys and the proof (guarded; it passes with
     storage throwing).
   - The recovery banner shows `{n}` from `recoveryNotice`.
5. **Keepalive** (fake timers):
   - pointer events every 10 s for 3 minutes → exactly 3 POSTs, each carrying the proof header;
   - `document.visibilityState = "hidden"` → 0;
   - not mounted outside `authenticated`;
   - no timer is scheduled while idle (`vi.getTimerCount() === 0`).
6. **Lazy loading.**
   - The build emits a separate chunk containing `qrcode-generator`.
   - The main chunk does not contain the string `getModuleCount`.
7. **Budgets (AC-30, SPA part; NFR-2, §17.11).**
   - Main-chunk growth ≤ 12 KB gzip against `bb6d8a0`, covering every auth screen without QR.
   - QR chunk ≤ 12 KB gzip.
   - `npm audit --omit=dev --audit-level=high` exits 0.
   - The lock file resolves `qrcode-generator` to `2.0.4` exactly.
   - The numbers are recorded in STATUS.
8. **Gates.**
   - `npm run test`, `npm run typecheck` and `npm run build` are green, and the bundle is committed.
   - A fresh rebuild leaves `git status --porcelain src/agent_orchestrator/ui/static` empty.
   - `python -m pytest -q tests/ui/test_e2e_ui.py` still passes with auth off.
9. **Baseline (v2.1, AC-30 part).** STATUS records the Description item 0 command and result on the
   pre-epic baseline **before** any change of this task; a non-clean result was escalated to the
   manager (recorded) before the bundle commit.
10. **Single owner (v2.1).** The committed bundle is built from `ui/src` after T-pQ73eO and T-R7JhTL
    have merged (the commit's parent contains both); `ui/package.json` changes by this task touch
    only `dependencies`.

## Risks
- **Supply chain.** An exact pin, `npm audit` and a one-file wrapper keep it replaceable (§17.7).
- **Budget overrun** once T-pQ73eO's screens are included. Shared strings and constants keep it down.
  If over budget, stop and escalate to the manager; do not drop features silently.
- **Non-reproducible baseline bundle** (v2.1). Description item 0 catches it before the CI gate is
  relied on.
- **Cross-epic bundle conflicts** (X5) with the approvals epic. Regenerate from merged sources; never
  hand-merge hashed assets.

## Dependencies
- **Upstream:** T-pQ73eO-spa-auth-gate-login; **T-R7JhTL-hub-login-page** (v2.1: the single
  `ui/static` committer runs last; design-review M2).
- **Downstream:** T-U2ERMo-auth-e2e-regression-sweep (browser smoke, e2e, rebuild-diff gate);
  T-otjIkJ (writes the `ui/README.md` dependency entry from this task's recorded numbers).

## Pseudocode / Algorithm
- **QR path:** for each dark `(r, c)`, append `M{c+q} {r+q}h1v1h-1z`. Use
  `shapeRendering="crispEdges"`, `fill="#000"` on a `#fff` rect, and `viewBox="0 0 {n+2q} {n+2q}"`.
- **Secret grouping:** `secret.match(/.{1,4}/g)!.join(" ")`.
- **Keepalive guard:** `if (document.visibilityState === "visible" && now - lastSent >= KEEPALIVE_MIN_INTERVAL_MS) { lastSent = now; void authApi.keepalive().catch(() => {}); }`

## Schemas / Interface Notes
- HLD §2.4 bodies for E4–E10, and §2.5 types (`TotpEnrollment`, `AuthStepResponse`,
  `KeepaliveResponse`).
- Forced vs voluntary E4 bodies: `{enrollment_token}` vs `{current_password}`. Never both.

## Handoff Boundary
- **Upstream:** T-pQ73eO's gate and context.
- **Downstream:** the complete SPA auth UI and the rebuilt, committed bundle.

## Verification

```
# first step, on the pre-epic baseline (record the result in STATUS):
cd ui && npm ci && npm run build && cd .. && git diff --exit-code -- src/agent_orchestrator/ui/static
# after T-pQ73eO and T-R7JhTL have merged:
cd ui
npm ci && npm run test && npm run typecheck && npm run build && npm audit --omit=dev --audit-level=high
for f in ../src/agent_orchestrator/ui/static/assets/*.js; do echo "$f $(gzip -c "$f" | wc -c)"; done
cd .. && python -m pytest -q tests/ui/test_e2e_ui.py
```

## Artifacts
- `meta/tickets/E-Da5Tn9-dashboard-auth-totp/T-vCgsU6-spa-enroll-account-qr/`
