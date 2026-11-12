# STATUS

- ID: `E-Sn7tL2-sentinel-long-running-overseer`
- Updated At: `2026-11-09`
- State: `Implemented — live smoke + default flip pending owner budget` (11 of 13 tasks Done; T-SnXAcc In Progress, T-SnXDoc Partial)
- Owner: `avadhoot`

## This update
- By: claude-opus-5-5 · Role: architect · Date: 2026-11-09 · Comment: Rev 3. All owner decisions resolved (D1–D4,
  §10). 13 task folders created; final checklist in EPIC §11; combined implementation handoff in `HANDOFF.md`
  (co-delivery with E-aYG5vn).

- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: **T-Sn0Spk done — GO-WITH-CHANGES** for the
  soft barrier (FR-8/FR-12); §4.4.4 fallback not needed unless invariant I1 slips. Evidence: `output/E-Sn7tL2-sentinel-long-running-overseer/spike/RESULT.md`.
  Spec changes to carry into T-Sn1Adr: **FR-8** reshape only via `_apply_pending_edits` (validate on copy → commit → persist →
  reshaped triple); **FR-10** early checkpoint must trim `inputs` as well as `depends_on` (inferred edges) and persist the edit
  (`RunState.graph_edits`, else resume reverts it); **FR-12** extra checker rules (no duplicate output producer, no edge into a
  non-pending task, cycle check on a copy, no edits inside any route cone/loop clone); **FR-5/6** `start_new_session` + `killpg`
  TERM→grace→KILL (today's timeout kill orphans grandchildren), `idle_kill_minutes` default **15**, floor **12** (healthy max gap
  600.4 s = Bash 10-min cap, 0/34 196 gaps >15 min); **NFR-8** tick 0.35 ms / reshape 1.9 ms @1k tasks, `RunState.save` 10 ms @831 KB
  (save on mutation only); new finding: `ao run` has no signal handling — SIGTERM kills it instantly, leaves `state=running` and
  orphan `claude` children (T-Sn3Tck/T-Sn4Rec).

- By: architect · Role: agent · Date: 2026-11-09 · Comment: **T-Sn1Adr written** — ADR-0024 + `docs-md/sentinel-hld.md`
  (status Proposed). **Owner sign-off pending (checkpoint 2); not recorded here.** Needs owner confirmation on: signal handling
  for all runs (ADR D-10) and idle_kill_minutes floor 12.

- By: avadhoot (recorded by claude-sonnet-5-5) · Role: user · Date: 2026-11-09 · Comment: **OWNER SIGN-OFF: ADR-0024 + `docs-md/sentinel-hld.md` ACCEPTED.** D-10 (SIGTERM/SIGINT + process-group kill for ALL runs) CONFIRMED; `idle_kill_minutes` floor 12 with explicit `force` escape CONFIRMED. P1 engine work unblocked.

- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: **T-Sn3Tck done** — engine tick/inbox/live overrides/metrics/fail-safe (disabled path unchanged).
- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: **T-Sn2Set done** — sentinel settings on every CLI/env/config/workflow/template surface; engine only stores them.

- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: **T-Sn3Ctl done** — `ao ctl` CLI + shared `control.py` writer; `status --json` schema `ao.ctl.status/v1` documented in HLD §12.1 and snapshot-tested.
- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: **T-Sn8Gen done** — LLM generations (pack, handoff, memory, rotation, cost rows, budget, auto-pause); HLD §8.6 as-built; fake-driver tests only, live smoke in T-SnXAcc.
- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: **T-Sn5Ui1 done** — sentinel control API (`GET /sentinel`, `POST /control`, verdict probe) + Sentinel panel v1 + Sentinel launch-form group; HLD §12.2 as-built.
- By: claude-sonnet-5-5 · Role: tester · Date: 2026-11-09 · Comment: **T-SnXAcc non-live e2e matrix done** (`tests/test_sentinel_e2e_matrix.py`, 17 pass; `T-SnXAcc*/MATRIX.md`). Live smoke pending owner budget. mypy gate red (see MATRIX findings).

## Task rollup
| Task | Phase | State |
|---|---|---|
| T-Sn0Spk-midflight-reshape-kill-spike | P0 | Done (GO-WITH-CHANGES) |
| T-Sn1Adr-sentinel-adr-and-hld | P0 | Done — ADR Accepted (owner sign-off 2026-11-09) |
| T-Sn2Set-sentinel-settings-plumbing | P1 | Done |
| T-Sn3Tck-main-loop-tick-inbox-overrides | P1 | Done |
| T-Sn3Ctl-ao-ctl-cli | P1 | Done |
| T-Sn4Rec-liveness-kill-recovery-ladder | P1 | Done (gates: see its STATUS) |
| T-Sn5Ui1-sentinel-panel-and-control-api | P1 | Done |
| T-Sn6Brr-soft-barrier-and-pending-edits | P2 | Done (AC5 closed in W10: graph API `graph_edits`, edited-edge + sentinel-node styling) |
| T-Sn7Bkl-backlog-and-early-checkpoint | P2 | Done (NFR-13: idle-with-work 3.5s -> 0.01s) |
| T-Sn8Gen-llm-generations-pack-handoff | P3 | Done |
| T-Sn9Pow-llm-powers-directives-routed | P3 | Done (edited-edge styling closed in W10 under T-Sn6Brr) |
| T-SnXAcc-e2e-and-live-smoke | P3 close | In Progress (AC1 non-live matrix + AC3 HLD reconcile done; AC2 live smoke pending owner budget — `T-SnXAcc*/HANDOFF.md`) |
| T-SnXDoc-docs-readme-learnings | each phase | Partial (docs done; default flip pending FR-23 evidence) |

## Evidence
- Utilisation baseline (EPIC §1): 7–41% slot utilisation at P=6 over the last 10 overseer-runner runs; 50–80% of
  wave span is tail after the median unit finishes.
- Code findings (EPIC §2), notably DRAIN has no timeout (`engine.py:999`); no per-task kill or liveness.

## Risks / Blockers
- In-flight graph rebuild: verified safe with 6 invariants to add (I1–I6, spike RESULT.md); **owner checkpoint 1 (HANDOFF §4): not a no-go, so no fallback confirmation needed** — owner may still veto GO-WITH-CHANGES.

## Next actions
1. ~~T-Sn0Spk~~ done ∥ ~~E-aYG5vn T-zP28k9~~ done, ~~T-f0d7gm~~ done.
2. ~~T-Sn1Adr~~ signed off 2026-11-09; P1–P3 implemented.
3. **Owner:** confirm FR-23 live-smoke budget (proposal $15 run cap, sonnet/haiku) → run per `T-SnXAcc-e2e-and-live-smoke/HANDOFF.md` → then default flip (T-SnXDoc), remaining EPIC §11 boxes, PR to `main`.
4. Owner decisions pending: `mypy .` vs `mypy src` as the gate; whether the as-built settings surfaces (no `ao config show`) satisfy §11.

- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: **T-Sn6Brr done** — soft barrier, shared checker, `inject`/`edit_pending`, persisted graph edits (HLD §6.8). Graph-UI styling (AC5) deferred.

- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: **T-Sn9Pow done (see its STATUS for evidence and the honest gaps)** — LLM powers gate, directives + OV-R12b, routed-runner planner, panel v2, sentinel graph styling (HLD §8.7).

- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: **T-SnXDoc partial** — docs/READMEs/guide/HLD §21 reconciled; EPIC §11 ticked only for spike, ADR sign-off, NFR-13 fixture, docs; default flip deferred (no FR-23 evidence).

- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: **W11 reconcile (w11-02):** rolled up consistently with the task STATUS files; HLD §21 Deviations re-checked against code (no stale claims); live-smoke handoff written (`T-SnXAcc*/HANDOFF.md`: steps, $15 cap proposal, evidence dir, default-flip + §11 follow-ups). Live smoke NOT run; default NOT flipped. Dashboard epic E-aYG5vn rollup unchanged (5 Done, T-9ymKja Blocked by owner gate).

- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: **W11 gates-green (w11-01-gates-green):** full `pytest -q` run to completion on branch `ad/enhancements-20oct` = **11012 passed, 32 skipped, 0 failed** (1012 s; run with `VIRTUAL_ENV` set — the 2 bench `test_fake_subject_full_suite_*` failures seen earlier were environmental: the dev-core/dev-medium graders run `uv run python check*.py` and need pytest from the activated venv; without `VIRTUAL_ENV` system python3 lacks pytest. They pass with it set, no code change). `ruff check` + `ruff format --check` (701 files) clean; `mypy src` (the CI gate, `.github/workflows/ci.yml`) clean after fixing `overseer_tool.py` limit-report typing and `_version.py` BUILD_* annotations; `pyproject.toml` mypy now excludes `benchmarks/suites/` (duplicate `conftest`/`run` modules) but `mypy .` over tests/ + scratch scripts remains red by construction (owner picks gate). UI `npm test` 901/901, `npm run build` ok. `test_sentinel_e2e_matrix.py` 17/17 green. Stale `ao ctl kill/release --help` docstrings fixed. FR-23 live smoke / default flip still owner-gated.

- By: claude-sonnet-5-5 · Role: other · Date: 2026-11-10 · Comment: **Bug filed `T-Rk4Wn7-stuck-kill-retry-loop-and-run-stranding`** — live finplan overseer run failed after the sentinel stuck-killed one task 3× in 32 s (retry keeps the original `started_at`), tripped the systemic alarm, stranded 2 ready tasks, and the LLM lane could not write outputs (permission mode `default`). Evidence paths in the task's TASK.md.

- By: claude-sonnet-5-5 · Role: tester · Date: 2026-11-11 · Comment: **T-Rk4Wn7 (bug) closed: D1-D4 + CTL-SCHEMA landed with regression tests (independently verified on HEAD, w21-07).** One open OWNER question (not a blocker): `defaults.fail_fast` opt-out for D3; recommendation keep continue-on-failure. Live engine needs the human `install.sh` step.
- 2026-11-11: filed T-Fp9Hng-finplan-run-wedge-enospc-injection-cycle-and-operator-controls (second incident: ENOSPC wedge, idle-kill vs child work, injection cycle, operator controls, heavy-resource policy).
- 2026-11-12: T-Rs4Qv8-named-resources-and-concurrency-groups filed (generic resource slots + concurrency groups, from T-Fp9Hng E5/E6). T-Fp9Hng E5/E6 narrowed: test scope/lanes/target dirs moved to fin_plan; no template change.
- 2026-11-12: T-Rs4Qv8 and T-Fp9Hng revised after review; FinPlan-side throughput fixes landed in fin_plan 30b3fa85 (no AO change needed to unblock).
