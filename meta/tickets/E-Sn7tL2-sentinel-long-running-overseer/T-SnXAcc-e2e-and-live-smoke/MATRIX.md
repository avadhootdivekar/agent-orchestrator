# T-SnXAcc — non-live e2e acceptance matrix (EPIC §11)

Executable index: `tests/test_sentinel_e2e_matrix.py` (`MATRIX` dict + `test_matrix_targets_exist`, which fails if a
referenced test is renamed/removed). All drivers scripted (FakeExecutor / fake LLM driver); temp workspaces only; no live LLM.
Run on 2026-11-09, branch `ad/enhancements-20oct`.

| §11 scenario | Evidence (tests) | Result |
|---|---|---|
| Tick while all workers busy; metrics | test_sentinel_tick: `tick_runs_while_all_workers_blocked`, `metrics_on_scripted_timeline` | pass |
| Stalled → kill → retry → escalate → fail; systemic alarm | test_sentinel_recovery: `idle_kill_retry_escalate_then_fail`, `systemic_alarm_stops_kills`, `real_subprocess_tree_killed_within_2s_no_orphans` | pass |
| `ao ctl` round trip + live `max_parallel` | test_ctl_cli `live_round_trip_set_pause_note_kill_cancel`; test_sentinel_tick `set_max_parallel_up_to_ceiling` | pass |
| Dashboard control API: CSRF/auth, actor audit | tests/ui/test_sentinel_api `TestSecurity` (403 cross-origin, form CT), `actor_is_the_authenticated_principal`; tests/ui/test_sentinel_e2e | pass |
| Heavy-tail idle-slot-seconds ≥50 % lower (NFR-13) | test_sentinel_backlog `heavy_tail_idle_slot_seconds_reduced_at_least_half` | pass |
| Backlog release + expiry; early checkpoint carry-over | test_sentinel_backlog (release-once, expiry, early-ckpt-with-straggler) | pass |
| Pending-edit accept/reject matrix + stage gating | test_graph_check `stage_matrix`; **new** `pending_edit_reject_matrix_through_inbox` (8 rejects: UNKNOWN-TASK, NOT-PENDING x2, CYCLE, PAYLOAD x4 → quarantined with rule_id; one good edit lands; all-or-nothing) | pass |
| Soft-barrier emitter with tasks in flight | test_soft_barrier `soft_emitter_overlaps_three_unrelated_tasks` | pass |
| LLM rotation (≥3), ungraceful exit, budget cap, auto-pause | test_sentinel_generations `e2e_rotation_three_generations…`, `e2e_budget_cap…`, `e2e_consecutive_rejections_auto_pause` | pass |
| LLM powers advise vs act; kill limits; directive ack (R12b) | test_sentinel_powers | pass |
| routed-runner planner injects sentinel-origin unit | test_sentinel_powers `e2e_routed_planner_idle_slots_inject_sentinel_origin` | pass |
| Crash + `ao resume` at each mutation point | kill ladder, edit replay, early-ckpt edit, overrides, generations (existing) + **new** `inject_survives_crash_and_resume_exactly_once` | pass |
| Lane exception ⇒ runs as disabled | test_sentinel_tick `tick_exception_runs_as_if_disabled`; generations `e2e_lane_error_disables_only_llm_lane` | pass |
| NFR-1 disabled == absent | test_sentinel_tick `disabled_is_inert`; **new** `disabled_sentinel_equals_absent_state_golden` (scrubbed state.json + file set identical, no `control/`) | pass |

Numbers: new file 17 tests (14 traceability + 3 e2e) pass; sentinel/soft-barrier/ctl/graph_check/ui-sentinel subset 218 passed.

## Findings
1. **mypy gate not green** (pre-existing + other unit): `mypy .` aborts on duplicate `conftest` modules under
   `benchmarks/suites/dev-core/**/fixture/`; `mypy src` = 11 errors in 3 files: `_version.py` (4), `overseer_tool.py` (6),
   and `ui/graph.py:263` (1, arg-type on `append` — in the *uncommitted* graph.py edits of another unit). Not touched here.
2. `GE-EDGE-INTO-NONPENDING` is not reachable via `edit_pending` (the `GE-NOT-PENDING` target guard fires first); it
   applies only to additive manifests/inject. Not a bug; documented in the matrix test.
3. Shared tree contains other units' uncommitted edits (HLD, UI graph, graph_check.py, test_sentinel_backlog.py…); this unit committed only its own files.

## Pending owner budget confirmation — FR-23 live smoke (NOT run)
- Real Claude CLI in a temp workspace, short TTL forcing ≥3 LLM rotations.
- Scripted stalled task killed + retried live.
- Idle-slot scenario (backlog + early checkpoint) on a real heavy-tail run.
- One real LLM pending edit accepted by the checker.
- One live `max_parallel` change from the dashboard.
- Evidence to collect: ledger, handoffs, utilisation before/after vs EPIC §1 baseline, cost split work/overseer/sentinel, combined overhead → `output/E-Sn7tL2-sentinel-long-running-overseer/smoke/README.md`.
- Also still open: HLD "Deviations" reconciliation (AC3) and the E-aYG5vn T-mQs7CQ dependency check.
