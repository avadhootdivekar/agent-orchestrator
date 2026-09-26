**instructions-version: 1**

The per-run `overseer-contract.md` is authoritative for ids, paths, and JSON shapes. Where this file and the contract disagree, the contract wins.

# Closeout — the run's completion marker

## Your role
You are the `manager` agent dispatched as `closeout`, the second task in the fixed
close-out tail (`final-verify → closeout → final-push`). **`outputs/final/closeout.md`,
the file you write here, is literally what makes this run "done"** — see this
template's `README.md`, "Completion marker" section: a run whose engine status reads
`succeeded` but has no `outputs/final/closeout.md` is anomalous and should be treated
as incomplete regardless of what the engine says.

## Inputs
Your task's `inputs` are `outputs/charter.json` and `outputs/final/verify.md` (the
honest, report-only findings `40-final-verify.md` just produced) — use `verify.md` as
your ground truth for what's actually true right now, not the deciding checkpoint's own
optimism.

## Write `outputs/final/closeout.md`

### Per ask
For every ask in the charter, state exactly one of:
- **Done** — fully met, with the evidence from `verify.md`.
- **Usable** (yes/no + why) — even if not fully done, is it usable per its
  `usable_bar`? Say why, concretely.
- **Not done** — what's missing.
- **Deferred** (with reason) — explicitly out of scope for this run's ending, and why
  (out of budget, blocked on human input, descoped by an operator, or infeasible). This
  mirrors any ask a checkpoint's own verdict already marked `deferred` in its
  `alignment[]`/`criteria[]` — carry that same reason forward rather than inventing a
  new one.

For every ask, whatever its status, also state **how to continue**: what a human or a
future run would need to do next to finish or improve it. Never leave an ask's section
without this — even a "done" ask can note what a natural next iteration would be.

### Budget summary
Total spent (from the ledger/state) against `run_budget_usd`, and the stage history of
this run (`explore → converge → stabilize → closeout`, with the wave/checkpoint at
which each transition happened) — read this from each checkpoint's own `digest.json`
across the run, not just the last one.

### Loop/rework summary
Across the **whole run**, which signals fired (`period_repeat`, `mirror_flipflop`,
`content_oscillation`, `stall`, `repeated_failure`, `attempt_cap`, `ask_starvation`,
`blocked_units`, `breadcrumb_integrity`, `prompt_changed`, etc.) and how each
checkpoint's `signal_responses[]` answered them — pull this from every checkpoint's
`verdict.json` in `outputs/checkpoints/`, not just the last wave's.

### Ledger pointer
Point at `outputs/ledger.jsonl` as the full, append-only, hash-chained record of every
unit and checkpoint event this run produced, for anyone who wants to audit beyond this
summary.
