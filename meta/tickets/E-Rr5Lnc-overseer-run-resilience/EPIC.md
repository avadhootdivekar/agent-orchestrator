# EPIC: E-Rr5Lnc-overseer-run-resilience

## Metadata
- Epic ID: `E-Rr5Lnc-overseer-run-resilience`
- Title: `Overseer run resilience: stop runs dying on deterministic tool/contract issues`
- Owner: `unassigned`
- Created: `2026-11-11`
- Last Updated: `2026-11-11`
- Status: `Draft`

## READ FIRST: re-analysis required before implementing
This ticket was filed from a log-level RCA of past failed runs (2026-11-11). The root cause
below is evidence-backed, but the **proposed fix is a starting hypothesis, not a decided
design.** Whoever picks a task in this epic up must, before writing code:
1. Re-read the cited evidence (run logs / hook stderr) and re-confirm the root cause against
   the CURRENT code (the overseer tool and engine change often; line numbers here will drift).
2. Look for other runs/workspaces with the same failure signature since this was filed.
3. Write down at least two fix options with trade-offs (safety, determinism, cost, contract
   impact on `overseer-contract.md` / instructions / existing runs on disk) and pick one,
   recorded in the task's STATUS.md, before implementing.
4. Add a regression test that reproduces the original failure first (fails before the fix).
5. Check compatibility with in-flight runs: per-run copies of `tools/overseer_tool.py` are
   frozen at scaffold time, so a template fix does not reach existing runs.

## Summary
- Goal: an overseer-runner run should only stop for (a) its budget/turn/time limits, (b) a
  deliberate human-input pause shown as such, or (c) genuine infrastructure loss -- never for
  a deterministic tool bug, a contract contradiction, or a single correctable LLM formatting slip.
- Scope In: overseer-runner tool/contract/instructions, engine failure handling for hooks and
  missing outputs, run status model, preflight, resume, related dashboard display.
- Scope Out: changing budgets/limits semantics; non-overseer templates beyond the shared engine
  paths (routed-runner benefits from the engine-level items).

## RCA (cross-workspace, 2026-11-11)
Scope: every run under `ao-runner-ao`, `ao-runner-finplan`, `ao-runner-ai-models`
`.orchestrator/runs/` (32 runs; read-only). 15 runs ended `failed` at least once, 21 stops in
total. **None were cost/turn/time limits. Only 3 were genuine LLM output mistakes.**

| Class | Stops | Root cause | Task |
|---|---|---|---|
| Tool/contract bug | 4 | forced closeout at max_waves vs OV-R14 early-closeout evidence rule (deterministic contradiction) | T-Mw4Clo |
| Tool bug | 1 | path-history.json unbounded growth vs 1 MiB read cap | T-Ph2Cap |
| Config found late | 3 | OV-CFG-3 checked only in the intake hook; resumed twice unchanged | T-Pf8Chk, T-Rs2Gdr |
| Deliberate pause shown as failure | 8 | HOLD x4, git-branch-off abort x2, final-push refusal x2 | T-Aw6Inp, T-Pf8Chk, T-Ng1Wsp |
| LLM output slip | 3 | missing `barrier`, missing breadcrumb `unit_id`, declared output not written | T-Nm3Dfl, T-Mo5Nud |
| Infra | 2 | disk full; supervisor kill alarm (T-Rk4Wn7 stuck-kill bug, fix not installed) | T-Dk4Hyg |

Cross-cutting finding: **any validation failure ends the whole run on first occurrence** -- no
in-session repair with the violation text, no deterministic fallback, and the rules monitor only
retries "transient" failure text (T-Rp7Vfb). Contributing: runs exhaust max_waves at 24-46% of
budget because waves are small and early checkpoints consume them (T-Wv9Bud).

Case run: `o-6w86nm-sched-governance-20261110T175407Z` (`ao-runner-ao`) failed at ck-20 (the
max_waves trap; resumed with max_waves 20->40) and at ck-40 (path-history cap; would also have
hit the max_waves trap). Resumed 2026-11-11 with a LOCAL patch to its per-run tool copy (rolling
path-history window), trimmed history (backup in the run instance's `resume-backup-20261111/`),
max_waves 40->120 and max_injected_tasks 400->1100 (CFG-3 need = 1083).

## Requirements
- FR-1: No deterministic contradiction between overseer rules (forced decision always passable).
- FR-2: Tool-maintained state is bounded; caps apply to agent-authored input only.
- FR-3: Correctable LLM output errors get in-session repair before failing; derivable fields are normalised.
- FR-4: Human-input pauses have their own status; they never count as failures or trip breakers.
- FR-5: Config/environment errors are caught before launch and on resume.
- FR-6: Each stop records a structured, actionable stop reason.
- NFR-1: No weakening of the safety intent of existing OV-* rules (each relaxation justified in STATUS).
- NFR-2: Existing on-disk runs keep working (per-run tool copies are frozen; document migration).

## Task List (priority order)
- [ ] `T-Mw4Clo-forced-closeout-verify-contradiction` -- P0: Forced closeout at max_waves contradicts OV-R14 early-closeout evidence rule
- [ ] `T-Ph2Cap-path-history-unbounded-growth` -- P0: outputs/overseer/path-history.json grows without bound and crashes ckpt-prep at 1 MiB
- [ ] `T-Rp7Vfb-checkpoint-validation-repair-loop` -- P0: A rejected checkpoint verdict/manifest fails the whole run instead of being repaired
- [ ] `T-Nm3Dfl-normalize-derivable-agent-fields` -- P0: Reject-on-missing for fields the tool can derive (barrier, breadcrumb unit_id)
- [ ] `T-Aw6Inp-awaiting-input-run-status` -- P1: Planned human-input stops are reported as run failures (and trip breakers)
- [ ] `T-Pf8Chk-preflight-validation` -- P1: Configuration errors are found only after launch (sometimes after hours)
- [ ] `T-Rs2Gdr-resume-guard-and-doctor` -- P1: `ao resume` re-runs into an unchanged failure; no diagnosis aid
- [ ] `T-Mo5Nud-missing-output-nudge` -- P1: Task fails on missing declared output without asking the agent once
- [ ] `T-Ng1Wsp-no-git-workspace-mode-for-overseer` -- P1: overseer-runner always runs an LLM git-branch-off stage, even with no repo / no-git mode
- [ ] `T-Wv9Bud-wave-accounting-and-churn` -- P2: Runs exhaust max_waves at a fraction of budget (small waves, bookkeeping churn)
- [ ] `T-Dk4Hyg-disk-log-and-supervisor-hygiene` -- P2: Disk-full and supervisor-kill stops; run.log noise

## Risks and Dependencies
- Relaxing OV-R14/R10 could let low-quality closeouts through; each change needs a test that
  the original intent still holds for non-forced cases.
- Per-run tool copies: fixes reach only runs scaffolded after the change (plus manual patching).
- T-Dk4Hyg depends on installing the T-Rk4Wn7 fixes (human install step, see CLAUDE.md).
- Related: E-YAAGhk-overseer-runner-template, E-Sn7tL2-sentinel-long-running-overseer,
  E-XyfjuZ-agent-monitoring-self-healing, E-3JTmVu-breaker-resume-extend.

## Links
- Evidence: run dirs under each workspace's `.orchestrator/runs/<run-id>/` (hook stderr in
  `<task>/pre_hook|post_hook/stderr.txt`).
