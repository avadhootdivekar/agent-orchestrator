# HANDOFF — FR-23 live smoke (T-SnXAcc AC2/AC3) — owner action required

- Prepared: 2026-11-09 · By: claude-sonnet-5-5 · Role: developer · Wave: W11 (w11-02-hld-reconcile-handoff)
- Status: **NOT RUN.** Nothing below has been executed; no live evidence exists. Needs the owner's budget confirmation
  (HANDOFF.md §4 checkpoint 3), then a human/owner-launched session — runs must not spend this budget on their own.

## 1. Decision needed from the owner
| Item | Proposal |
|---|---|
| Run cost cap | **$15** total: a `run_cost_usd` breaker of 15 in the smoke workflow's breakers (the circuit-breaker the sentinel's `llm.budget_pct`/`budget_usd` derive from, `docs-md/guide-sentinel.md`); sentinel LLM spend counts inside it (`llm.budget_usd` ≈ 1.5) |
| Work-unit models | sonnet (heavy units) / haiku (filler units); sentinel LLM = sonnet; never opus |
| Wall clock | ~60–90 min, one temp workspace |
Reply "approved $15" (or a different cap) in `STATUS.md` of this task to unblock.

## 2. Safety (non-negotiable)
Temp workspace only (`mktemp -d`; never this workspace's `workflows/**/runs/`). The `ao` used is the installed snapshot —
if it predates the sentinel code, the owner must promote first via the live-install flow (build UI → `install.sh --force --yes`
→ restart `ao.service`); the smoke itself never runs `install.sh`/`uv tool install`/restarts `ao.service`.

## 3. Steps (FR-23, one run covers all five)
1. `W=$(mktemp -d)`; scaffold a workspace there (as in `tests/test_sentinel_e2e_matrix.py` fixtures); use the builtin `overseer-runner` template (or a small `emit_tasks` heavy-tail workflow:
   6 short units + 1 long unit + ≥6 held backlog entries).
2. Launch with the sentinel on and a short generation TTL (flags verified against `ao run --help`; leaf keys per `guide-sentinel.md`):
   `ao run <workflow> --sentinel --sentinel-llm --sentinel-powers act --max-parallel 4 --sentinel-budget-usd 1.5 \
     --sentinel-set llm.generation_ttl_minutes=5 --sentinel-set recovery.idle_kill_minutes=12`
   (the long flag `--sentinel-budget-…` is truncated in `--help`; confirm its full name there). Confirm effective values with
   `ao ctl status <run> --json` before spending.
3. Open the dashboard on the run (`ao ui`, or the daemon) — verify Sentinel panel + Limits & settings.
4. **(a) rotations**: wait for ≥3 generations; ledger + handoff files appear under the run's `sentinel/` dir.
5. **(b) stalled task**: include a scripted unit that sleeps silently past `idle_kill_minutes` (idle_kill_minutes floor is 12; a lower value needs the explicit `force` escape — only with owner agreement); expect kill → retry → (escalate) in the ladder log.
6. **(c) idle-slot**: with backlog held and slots idle, expect an L1 release and an early checkpoint with carry-over.
7. **(d) LLM pending edit**: let the sentinel LLM propose one `edit_pending`/`inject`; checker must accept it (`GE-*` otherwise
   logged — record which).
8. **(e) live `max_parallel`**: change it once from the dashboard panel (or `ao ctl set <run> max_parallel=N`; check `ao ctl set --help` for the exact syntax); confirm
   `state.effective_settings` source `live-override` and the new dispatch width.
9. Let the run finish (or `ao ctl cancel`). Do not hand-edit run state.

## 4. Evidence to collect → `output/E-Sn7tL2-sentinel-long-running-overseer/smoke/` (+ `README.md` with numbers)
- Utilisation (slot-seconds busy/idle) vs the EPIC §1 baseline (7–41 % at P=6, 50–80 % tail) — `ao ctl status --json` metrics.
- Cost split work / overseer / sentinel (`ao usage`), and combined overhead % (NFR-7 budget in EPIC §3).
- Ledger, handoffs, kill/ladder log lines, the accepted LLM edit, screenshot or JSON of the live-override.
- Tick-latency (NFR-8) from metrics; any `GE-*` rejections and sentinel errors.
- Verdict per FR-23 item (a–e): pass / fail / not exercised, honestly.

## 5. What closes after the smoke (each is blocked until then)
| Box / item | Why it is open now | Action after smoke |
|---|---|---|
| EPIC §11 "FR-23 live smoke under `output/…/smoke/`" | not run (owner budget) | write `smoke/README.md`, tick |
| EPIC §11 "Mechanical-sentinel default flipped on in both builtin templates" | owner §10.1: only after FR-23 evidence | if utilisation up and overhead within NFR-7: flip `sentinel_enabled` default in both builtin templates + READMEs + a golden-test update; LLM lane stays opt-in. If evidence is negative: leave opt-in and record why |
| EPIC §11 "Settings visible in all surfaces" | `ao config show` / `GET /api/schema/sentinel` were never built (HLD §21 — settings are visible via `ao ctl status`, `effective_settings`, `GET /api/schema/run-options`); the run-page limits panel is only checked with fixtures | owner decides: accept the as-built surfaces (amend the box) or open a follow-up; verify the panel on the live run |
| EPIC §11 "PR from the branch to `main` opened" | PR is a human/owner step; runs push the branch only | open the PR once the smoke is recorded |
| EPIC §11 "Full pytest/ruff/mypy/UI green" | `mypy .` was never green (1097 errors in tests/output scratch scripts; `mypy src` is clean) — see T-SnXAcc STATUS | owner decides whether `mypy .` is a gate or `mypy src` |
| T-9ymKja (dashboard epic) | separate owner gate (all workspaces migrated + one deprecation release) | untouched; stays Blocked |

Non-live items already green (EPIC §11 scenario boxes) are listed in `MATRIX.md` and `smoke/README-nonlive.md`.
