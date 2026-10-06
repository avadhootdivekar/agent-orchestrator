# STATUS

- ID: `T-R7JhTL-hub-login-page`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (frontend lane F)
- Scope: `MVP` · Sprint: `S1→S2` · Estimate: `3 d`

## This update
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Implemented end to end; all 12 ACs
  met (evidence below). Notes for reviewers and downstream tasks:
  - Session-loss reactions on the hub: 401 `not_authenticated` -> clear proof, login form + the
    timed-out notice; `second_factor_required` -> TOTP form; `enrollment_required` -> token form. No
    extra status refetch (the SPA's single-flight refetch has no hub equivalent: the page itself is
    the state machine). 429 locks only the form that was submitted.
  - **Divergence to decide (not a blocker):** the hub shows the §17.8 `replayed` string exactly,
    without an `(n attempts left)` suffix (the table and AC 3 give no suffix). The SPA's
    `auth/messages.ts` appends the suffix when `attempts_remaining` is present. If the owner wants
    parity, change the hub (one line in `messageFor`) or the SPA; the contract test does not pin it.
  - Not implemented on the hub: the "You signed in with a recovery code. {n} codes left" notice
    (§17.8). The hub redirects to `/` on success and has no element to show it in (the §17.6 DOM
    is frozen); the SPA owns that notice.
  - The `SESSION_LOSS_CODES` row of the contract test compares constants for both clients, and
    behaviour for the SPA (real `api.ts` handler). The hub side is its pure classifier
    `isSessionLossCode`; its page reactions are tested in `hub-auth.test.ts`.
  - SPA "confirm" row: `authApi.confirmEnrollment` does not exist until T-vCgsU6, so the SPA driver
    exercises the same single `request()` path through a generic call. Switch it when T-vCgsU6 lands
    (comment in the test).
  - `vite build` with `build.lib` and `iife` requires `name`; set to `AoHubAuth` (nothing is exported
    or read from the global).
  - `tsconfig.json` include uses `src/hub/**/*` (TypeScript rejects a bare `**` suffix).
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9).
  Estimate 2.5 d → 3 d; sprint S1 → S1→S2 (lane F straddle, HLD §24.3 capacity note). Changes:
  - **Ownership (design-review M2; §16 rows 12, 12b):** this task owns the `ui/package.json`
    `scripts` keys and the `ui/tsconfig.json` include only; T-vCgsU6 owns the `qrcode-generator`
    dependency and the lock.
  - **Bundle rule (§16 row 15, cross-epic X5):** commits only `auth/assets/hub-auth.{js,css}`; never
    `ui/static` (discarded after local builds). T-vCgsU6 is the single `ui/static` committer and now
    depends on this task.
  - **New `auth-client-contract.test.ts`** (design-review minor 7; §17.10): one scenario table run
    against the SPA client and `hubAuthCore.ts`; written after T-pQ73eO lands (soft dependency).
  - **Type (security M2):** `transport.proxy_suspected` in the type subset; banner condition
    unchanged.
  - New ACs 11–12 and a no-`ui/static` check in AC 9.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2 rescope after the Phase-4
  consultations (HLD §28). Estimate raised from 2 d to 2.5 d. Changes:
  - **Session proof (D25 / ADR-0021 D11, dev-security #2).** The proof is stored from issuing
    responses in the hub origin's `localStorage` (memory fallback) and sent as `X-AO-Session-Proof`
    on every `/api` call. It is cleared on logout and on `anonymous`.
  - **Forced enrollment** now asks for the CLI enrollment token first (dev-security #5), and shows
    the `insecure_transport` (dev-security #4) and `totp_required` messages.
  - **Renamed wire state:** `totp_required` → `second_factor_required`.
  - **Code layout:** a testable `hubAuthCore.ts` plus an entry `hubAuth.ts`. The CSS source moved to
    `ui/src/hub/hub-auth.css` and is emitted by the hub build.
  - **Process:** no `mode` option (dev-security #13); a reproducible-build AC feeding the CI
    rebuild-diff gate; fake timers mandatory (tester T-6).
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the
  E-Da5Tn9 design package (design and tickets only; **no code written**). It can start immediately:
  it depends only on the frozen HLD §2 contract and the §17.6 DOM contract.

## Evidence
All commands run from `ui/` in the task worktree, 2026-10-05.
- `npm run test -- hub-auth auth-client-contract`: 2 files, **112 passed** (hub-auth 69, contract 43).
  Full suite `npm run test`: 44 files, **655 passed**, 0 failed (baseline before this task: 42 files,
  543 tests, so no regressions; the +112 are this task's).
- `npm run typecheck` (`tsc -b --noEmit`): clean (exit 0).
- `npm run build`: OK (`tsc -b`, SPA build, hub build). Hub output:
  `hub-auth.js` 6.91 kB raw, `hub-auth.css` 1.87 kB.
- **AC 8 budget:** `gzip -c ../src/agent_orchestrator/auth/assets/hub-auth.js | wc -c` -> **2903**
  bytes (limit 6144).
- **AC 9 reproducible:** two consecutive `npm run build` runs give identical md5 for both files
  (`hub-auth.js` 22e4c2535689e7b0d4fbda9f4ef4526f, `hub-auth.css` 2d212192273f5c510d2a816c046e188e).
  `src/agent_orchestrator/ui/static` was discarded after the build (`git checkout --` + `git clean`
  on that path only); the task commit contains no path under it.
- **AC 11 negative controls** (in `auth-client-contract.test.ts`, run against both clients): a client
  that keeps its proof on `anonymous`, one that drops the proof header, one that adds a `mode`
  option, and one with a different loss-code set each make the matching row fail. Also mutated the
  real `hubAuthCore.ts` (removed the clear on `anonymous`): the contract row and the hub-auth test
  failed; restored.
- AC 5/7/10/12: covered in `hub-auth.test.ts` (injection tests, forbidden-API string scan with
  negative controls, accessibility asserted after every step, banner table incl. `proxy_suspected`).
- Time-dependent tests (429 countdown, keepalive throttle) use `vi.useFakeTimers()` +
  `vi.setSystemTime()`.
- Files: `ui/src/hub/{hubAuthCore.ts,hubAuth.ts,hub-auth.css}`, `ui/vite.hub.config.ts`,
  `ui/tsconfig.json` (include), `ui/package.json` (scripts keys only),
  `src/agent_orchestrator/auth/assets/hub-auth.{js,css}`,
  `ui/src/test/{hub-auth.test.ts,auth-client-contract.test.ts}`,
  `ui/src/test/fixtures/{hub-login.html (verbatim HLD §17.6 lines 4507-4540),hub-index.html,hub.ts}`.

## Risks / Blockers
- None. The HLD §2 and §17.6 contracts are frozen (v2.1 adds `transport.proxy_suspected`, a
  manager-approved additive change); further changes need a manager-approved entry in the epic
  STATUS. OQ-8 and OQ-9 are DECIDED (D25 ships; this script always sends the proof).

## Next actions
0. DONE 2026-10-05 (see This update and Evidence). Downstream: T-KOv2qD serves `auth/assets/*`;
   T-vCgsU6 adds `qrcode-generator` and rebuilds/commits `ui/static`.
1. (original plan) developer (frontend): implement the hub script in S1 after T-pQ73eO; add the contract test once
   T-pQ73eO has landed; build; commit only the two `auth/assets` files (discard `ui/static`); record
   here the `hub-auth.js` gzip size (budget ≤ 6144 bytes) and the vitest and typecheck results.
