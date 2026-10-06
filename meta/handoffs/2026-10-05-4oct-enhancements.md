# Handoff — ad/4oct-enhancements (2026-10-05)

Branch: `ad/4oct-enhancements` (based on `main` @ `c62ec33`, which includes PR #16 cost-summary).

## Done
- **E-Da5Tn9 dashboard auth + TOTP** — 23/23 tasks, security re-audit approved. Local accounts, optional
  authenticator-app 2FA (`ui.auth.totp = off|optional|required`), `ao auth` CLI, hub + `ao ui` wiring,
  SPA login/enrollment. Auth off by default. HLD/ADR-0021 updated to as-built; user guide
  `docs-md/dashboard-authentication.md`.
- **E-Rc4Hk8 cross-run result cache** — 20/20 tasks, gates G1a/G1b/G2 PASS. Off by default
  (`--cache`, `--no-cache`, `AO_CACHE=0`), `ao cache` commands, `ao-bench` forces it off, byte-identical
  no-op proof when off. HLD/ADR-0019 updated; reports under `output/E-Rc4Hk8-cross-run-result-cache/`.
- Merge follow-ups: py3.11-safe `PoisonedFinder` test, import order, UI bundle rebuilt from merged sources.

## Pending
- **E-Ag7Pw3 human approval gates** — design rev 2 only (HLD `docs-md/human-approval-gates-hld.md`, ADR-0020,
  15 tickets), 0 implementation tasks started. Needs the design re-gate (reviewer + dev-security) first.
  Owner decisions recorded 2026-10-05: approvals require auth (anonymous refused); resume policy is
  per-gate, add allowed, remove/weaken/detach refused; OQ-12..15 and CE-1 (cache never settles a gate)
  stand as designed. Must honor the auth `Principal` contract (`roles: list[str]`).
- Cross-epic checks X1–X6 (auth <-> approval gates) open until gates land.
- Cache: G0 value check (post-merge, owner/operator); non-MVP: `refresh`, `rm --run/--task`, `verify --repair`.
- Auth: AC-34 real-browser smoke never run (Playwright not installed); owner to confirm that
  `totp=required` first enrollment needs an operator-issued CLI token, and OQ-11 (agent-lane timeline).
- Not installed locally: owner will request `ao` install after verification.
- `ad/provider-keys-and-executors` (E-Pk4Vn7) worktree has old uncommitted edits — untouched.
- Repo-wide `ruff check .` fails on one old output script
  (`output/E-YAAGhk-overseer-runner-template/repro_emit_lost_on_breaker_trip.py`); not changed.

## Verification
See the commit message / final report for the full-suite numbers on the merged branch
(baseline before this work: 5159 passed / 8 skipped).
