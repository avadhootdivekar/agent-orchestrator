# HANDOFF — combined implementation of E-Sn7tL2 (sentinel) + E-aYG5vn (dashboard limits & naming)

- Prepared: 2026-11-09 · By: claude-opus-5-5 · Role: architect · Approved design: owner (avadhoot), 2026-11-09
- Epics: [`EPIC.md`](EPIC.md) (sentinel, 13 tasks) · [`../E-aYG5vn-dashboard-run-limits-and-effort-naming/EPIC.md`](../E-aYG5vn-dashboard-run-limits-and-effort-naming/EPIC.md) (dashboard, 6 tasks)
- Every task folder has `TASK.md` (description, ACs, deps, pseudocode, interfaces, handoff boundary) + `STATUS.md`.

## 1. Read first (in order)
1. `agent-orchestrator/CLAUDE.md`, `AGENTS.md`, role guide for your agent in `.claude/agents/`.
2. Workspace `ao-runner-ao/CLAUDE.md` — self-hosting safety + git workflow (non-negotiable).
3. Sentinel `EPIC.md` §0–§4 and §11; dashboard `EPIC.md` §0–§3 and §5.
4. Your task's `TASK.md`. Background: `docs-md/overseer-runner-hld.md` (§0, §7, §8.2–8.4, §26),
   `docs-md/guide-dynamic-task-injection.md`, ADR-0003, ADR-0007, ADR-0017, ADR-0021.

## 2. What is decided (do not re-litigate)
- Sentinel D1–D4 + §10 (layered bandwidth; mechanical + LLM kill; pending-graph edits default on; engine-wide with live
  CLI/dashboard control; `killed` status; early checkpoints count toward `max_waves`; `ao ctl`; mechanical default on
  only after smoke evidence).
- Dashboard: effort = thinking effort; turn budget = max-turn tier (`effort` legacy alias with warning until the gated
  flip T-9ymKja); autocompact first-class and provider-neutral in CLI + dashboard; one run-options registry drives CLI,
  config, dashboard forms, allow-list and the effective-settings snapshot.
- Anything genuinely ambiguous ⇒ mark the task **BLOCKED** with explicit questions to the owner; do not guess.

## 3. Execution plan (waves of parallel work)

| Wave | Tasks (parallel within a wave) | Exit gate |
|------|--------------------------------|-----------|
| W1 | `T-Sn0Spk` spike · `T-zP28k9` registry + settings snapshot | spike RESULT.md; registry regression test green |
| W2 | `T-Sn1Adr` ADR/HLD · `T-f0d7gm` turn budget + autocompact | **Owner sign-off on ADR/HLD** (stop here until recorded) |
| W3 | `T-Sn2Set` sentinel settings · `T-xNFBww` limits panels | settings on every surface; panels render old + new runs |
| W4 | `T-Sn3Tck` tick/inbox/overrides · `T-pMpo4O` schema-driven forms | golden byte-identity; tick-while-busy test |
| W5 | `T-Sn3Ctl` ao ctl · `T-Sn4Rec` liveness/kill/ladder · `T-Sn8Gen` LLM generations | P1 e2e green |
| W6 | `T-Sn5Ui1` sentinel panel + control API · `T-Sn6Brr` soft barrier + edits (needs spike GO, else fallback) · `T-mQs7CQ` dashboard e2e/docs | E-aYG5vn checklist done |
| W7 | `T-Sn7Bkl` backlog + early checkpoint · `T-Sn9Pow` LLM powers + routed-runner | NFR-13 heavy-tail result |
| W8 | `T-SnXAcc` e2e matrix + live smoke · `T-SnXDoc` docs + default flip | Sentinel EPIC §11 checklist all ticked |
| gated | `T-9ymKja` effort→thinking flip | owner gate (all workspaces migrated + one deprecation release) |

`T-SnXDoc` also runs a short docs pass at the end of each phase (P1 after W5/W6, P2 after W7).

Hot-file contention: `engine.py` (Sn3Tck, Sn4Rec, Sn6Brr, Sn7Bkl), `cli.py` (zP28k9, f0d7gm, Sn2Set, Sn3Ctl), `ui/src/components/RunDetail.tsx` + `NewRun.tsx` (xNFBww, pMpo4O, Sn5Ui1). Tasks touching the same hot file in one wave must
either be sequenced or use worktree isolation with the integration ladder; prefer sequencing for `engine.py`.

## 4. Owner checkpoints (stop and ask)
1. After W1: spike result says no-go for in-flight rebuild ⇒ confirm fallback (EPIC §4.4.4) before W6.
2. After W2: ADR-0024 + HLD sign-off (required before any P1 engine merge).
3. Before W8 live smoke: confirm smoke budget (proposal: run cap $15, sonnet/haiku work units).
4. T-9ymKja gate.

## 5. Branch, safety, evidence
- Git (workspace rule 4/5): continue on `ad/enhancements-20oct` (in sync with origin, unmerged); small commits with
  trailers; push as you go; land via PR to `main`. Never force-push/rebase/reset; never push to `main`.
- Self-hosting: work only in `./agent-orchestrator`; temp workspaces for engine tests; **never** run `install.sh`,
  `uv tool install`, or restart `ao.service` (owner promotes after merge via the live-install flow).
- Per task: `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .`; UI: `npm test`, `npm run build`.
  Report real results only. Update TASK.md + STATUS.md + both epic STATUS rollups together.
- Evidence: `output/E-Sn7tL2-sentinel-long-running-overseer/{spike,smoke}/`, linked from STATUS files.

## 6. Launch options
**A. ao self-hosting run (recommended for W3+ once ADR is signed):**
```bash
cd /usr/avadhoot/mounted/ao-runner-ao
ao new overseer-runner sentinel-dashboard-impl --param repo_set=ao --validate-only   # check, then:
ao new overseer-runner sentinel-dashboard-impl --param repo_set=ao --run
```
with `prompt.md`:
> Implement epics E-Sn7tL2-sentinel-long-running-overseer and E-aYG5vn-dashboard-run-limits-and-effort-naming in
> `agent-orchestrator/` following `meta/tickets/E-Sn7tL2-sentinel-long-running-overseer/HANDOFF.md` (waves §3, gates
> §4, rules §5). One task per unit; each unit's brief = that task's TASK.md. Stop at the owner checkpoints.

**B. Interactive sessions per wave:** one session per task (developer agent), reviewer agent on every engine/CLI task
before merge, tester agent for W8.

## 7. Cross-reference index
| Sentinel task | Needs from / gives to E-aYG5vn |
|---|---|
| T-Sn2Set | **needs** T-zP28k9 registry; registers SentinelSpec options there |
| T-Sn3Tck | writes `live-override` source into `state.effective_settings` (T-zP28k9) |
| T-Sn5Ui1 | **reuses** T-pMpo4O form generator; run-page layout shared with T-xNFBww |
| T-SnXAcc | reuses T-mQs7CQ fixtures; final checklist requires E-aYG5vn §5 done |
| (limits panel) | T-xNFBww shows sentinel cap, live max_parallel/ceiling from Sn3Tck |
