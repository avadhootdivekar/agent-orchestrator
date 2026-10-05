# STATUS

- ID: `T-vCgsU6-spa-enroll-account-qr`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (frontend lane F)
- Scope: `MVP` · Sprint: `S2` · Estimate: `3 d`

## This update
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Implemented end to end; State Done.
  Details are under Evidence. Choices to note:
  - **API names.** `authApi.enrollBegin(body)`, `enrollConfirm({code})`, `disableTotp`,
    `regenerateRecoveryCodes`, `changePassword`, `keepalive` (HLD §17.3 names, object bodies). The
    `auth-client-contract.test.ts` "confirm" row now calls `authApi.enrollConfirm` against
    `/api/auth/totp/enroll/confirm`, as T-R7JhTL's STATUS asked (it called it `confirmEnrollment`).
  - **`replayed` message.** Both clients now show the exact §17.8 string, with no attempts suffix.
    The SPA's `authErrorMessage` and `totp-step.test.tsx` changed; the hub already did this.
  - **Keepalive is mounted in `AuthGate`** (`useKeepalive(view.kind === "authenticated")`), not in
    `App`, so it is structurally off in every other state. The first activity after mount posts
    immediately (`lastSent = -Infinity`), then at most once per `KEEPALIVE_MIN_INTERVAL_MS`.
  - **Recovery banner** is `RecoveryBanner` (same module as `AccountMenu`): a second additive
    `App.tsx` line at the top of `<main>`. Dismissal is local state.
  - **Dialog shell** has no Escape/backdrop dismissal on purpose (a stray key must not discard
    once-shown recovery codes); every dialog has an explicit Cancel/Continue.
  - **Shared code:** `ReauthDialog` (Disable + Regenerate), `CopyButton`, `AuthDialog`,
    `clearClientState`, `accountErrorMessage` (a `totp_required` in the account dialogs shows the
    server `detail`, not the login-time string). Two small additive exports: `launch.ts`
    `LAST_LAUNCH_KEY_PREFIX`, `useFormFailure(describe?)`.
  - **Not done here (as scoped):** `ui/README.md` dependency entry belongs to T-otjIkJ; use the
    numbers below.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9). The
  estimate is unchanged (3 d). Changes:
  - **First step: baseline check** (design-review minor 4): `npm ci && npm run build &&
    git diff --exit-code -- src/agent_orchestrator/ui/static` on the pre-epic baseline, recorded
    here; escalate to the manager if not clean.
  - **Single `ui/static` committer** (design-review M1/M2; HLD §16 row 15, cross-epic X5): rebuilds
    and commits the bundle after T-pQ73eO **and** T-R7JhTL have merged. New upstream dependency on
    T-R7JhTL.
  - **`package.json` split** (§16 row 12): this task owns only the `qrcode-generator` dependency
    and the lock; T-R7JhTL owns the scripts.
  - **Moved out:** the `ui/README.md` dependency entry goes to T-otjIkJ (estimate kept at 3 d).
  - New ACs 9–10.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2 rescope after the Phase-4
  consultations (HLD §28). The estimate is unchanged. Changes:
  - **Forced enrollment** now starts with the CLI **enrollment token** (`{enrollment_token}`; v1 sent
    `{}`), per dev-security #5.
  - **New error messages:** `insecure_transport` on begin and regenerate (dev-security #4), and the
    `totp_required` message.
  - **Keepalive moved here** from T-pQ73eO, including an assertion that no timer runs while idle.
  - **Recovery banner** is rendered from `AuthContext.recoveryNotice`.
  - **Explicit logout** also clears the proof.
  - **Process:** a reproducible-bundle AC for the CI rebuild-diff gate (dev-security #13); fake
    timers mandatory (tester T-6).
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the
  E-Da5Tn9 design package (design and tickets only; **no code written**). The QR library choice and
  its budget evidence are in HLD §17.7. `qrcode-generator@2.0.4` was checked at design time: MIT,
  zero runtime dependencies, `dist/qrcode.mjs` 11.2 KB gzip unminified.

## Evidence
All commands run from the worktree root on 2026-10-05 (node via `npm --prefix`/`cd ui`).

**Baseline check (Description item 0, AC-9), run BEFORE any change of this task.**
- Pre-epic baseline: the last commit that touched `ui/` or the bundle before the epic, `c62ec33`
  (`bb6d8a0` is the older budget reference). Extracted with `git archive c62ec33 ui
  src/agent_orchestrator/ui/static` to a scratch directory and run there:
  `cd ui && npm ci && npm run build`, then `diff -r` of the rebuilt `static/` against the archived
  one. **Result: identical (clean).** The committed bundle at the pre-epic baseline IS reproducible,
  so the CI rebuild-diff gate (T-U2ERMo) can be relied on. No escalation needed.
- Literal command on this branch's HEAD before my edits (`npm ci && npm run build && git diff
  --exit-code -- src/agent_orchestrator/ui/static`) exits 1, only because T-pQ73eO (50e0d39) and
  T-R7JhTL (e996c4e) changed `ui/src` but, per the v2.1 ownership rule, did not commit the bundle:
  the diff was exactly `index-CI_hsMR-.js` -> `index-BLhCSbxg.js` and the matching css/RunGraph
  renames (the epic's own source changes, not a reproducibility defect). That diff was discarded
  (`git checkout`/`git clean` of `static/` only) before my changes.

**Gates** (after the change)
- `npm --prefix ui run typecheck` (`tsc -b --noEmit`): clean.
- `npm --prefix ui run test` (`vitest run`): 48 files, 697 tests, all passed. New files:
  `enroll-screen.test.tsx` (11), `qr-code.test.tsx` (4), `account-menu.test.tsx` (16),
  `keepalive.test.ts` (11). Fake timers where time matters (keepalive, blob-URL revoke).
- `npm --prefix ui run build` (`tsc -b && vite build && vite build --config vite.hub.config.ts`):
  green. A second `npm ci && npm run build` produced the identical hashed files:
  `git status --porcelain src/agent_orchestrator/ui/static` shows only the staged bundle, with no
  unstaged difference.
- `.venv/bin/python -m pytest -q tests/ui/test_e2e_ui.py` (auth off): 14 passed.
- Committed bundle parent contains T-pQ73eO (50e0d39) and T-R7JhTL (e996c4e); `ui/package.json`
  changed only in `dependencies` (`"qrcode-generator": "2.0.4"`, exact). Lock resolves
  `node_modules/qrcode-generator` to `2.0.4`, `license: MIT`, no dependencies.
- `cd ui && npm audit --omit=dev --audit-level=high` exits 0. It still prints one **moderate**,
  pre-existing finding in `dompurify` (<=3.4.12, GHSA-55q2-fjhq-7xh7); not introduced here and below
  the gate. Worth a follow-up bump (`dompurify` is a runtime dependency).

**Bundle numbers (`gzip -c`, bytes)**

| Chunk | Pre-epic `bb6d8a0` | Pre-epic `c62ec33` | After this task | Delta vs `bb6d8a0` | Budget |
|---|---|---|---|---|---|
| `index-*.js` (main) | 132,560 | 132,776 | **139,546** | **+6,986 (6.8 KiB)** | <= +12 KB |
| `QrCode-*.js` (lazy) | n/a | n/a | **7,548** | n/a | <= 12 KB |
| `RunGraph-*.js` | 78,064 | n/a | 78,065 | +1 (renamed import hash) | n/a |
| `hub-auth.js` (T-R7JhTL) | n/a | n/a | 2.88 KB (vite gzip) | n/a | <= 6 KB |

Vite's own gzip estimate: main 133.94 -> 141.00 kB, QrCode 7.61 kB, css 5.32 -> 5.64 kB.
Lazy loading: the `QrCode-*.js` chunk holds `getModuleCount` (3 occurrences); the main chunk has 0
occurrences of `getModuleCount` and of `qrcode`/`createSvgTag`. `QrCode.tsx` is the only importer
of `qrcode-generator` (asserted in `qr-code.test.tsx`).

**Committed bundle**: whole `src/agent_orchestrator/ui/static/` staged (new `QrCode-*.js`, renamed
`index-*.js`/`index-*.css`/`RunGraph-*.js`, updated `index.html`, deleted old hashed assets).

**Not run:** a real-browser smoke (QR scan, clipboard, download) is T-U2ERMo's. The QR is verified
structurally in jsdom (viewBox, path, module count), not by decoding it.

## Risks / Blockers
- None. OQ-8 and OQ-9 are DECIDED (D25 ships; every `authApi` call carries the proof).
- Watch: a non-clean baseline rebuild (escalate) and cross-epic bundle conflicts (X5).

## Next actions
1. T-U2ERMo: browser smoke (real QR scan, clipboard, download), e2e and the rebuild-diff CI gate.
2. T-otjIkJ: write the `ui/README.md` `qrcode-generator` entry from the numbers above.
3. Follow-up (not this ticket): bump `dompurify` past 3.4.12 (moderate advisory).
4. Cross-epic X5: if the approvals epic changes `ui/src`, regenerate the bundle once from the
   merged sources; never hand-merge hashed assets.
