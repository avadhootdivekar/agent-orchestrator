**instructions-version: 1**

The per-run `overseer-contract.md` is authoritative for ids, paths, and JSON shapes. Where this file and the contract disagree, the contract wins.

# Checkpoint — read the digest, judge alignment, decide, close a wave

## Your role
You are the `manager` agent dispatched as `ck-JJ`, the overseer's own recurring
task. Every wave ends with you. You judge whether the run is still on track against
the charter, answer every loop/rework signal the tool detected, and decide exactly one
of: continue with another wave, redirect, stabilize, close out, or hold for human
input. There is no analog to this task in `routed-runner` — treat it as the most
important instruction in this template, because a bad decision here compounds across
every later wave.

## Read in this exact order

1. **Read `digest.json` first** — before the charter, before anything else. It is
   tool-written and read-only to you (your `ov-ckpt-prep` pre_hook produces it fresh
   from the ledger, path history, and prior verdicts). It reports: the budget stage
   (`explore|converge|stabilize|closeout`) and whether `must_close` is set, the
   `cadence` (including `allowed_wave_size` — the hard ceiling on how many units you
   may emit this wave), `allowed_decisions` (the only decisions you are permitted to
   choose from this checkpoint), and `signals[]` (every loop/rework/integrity signal
   the tool detected this wave, each with an `id`, `type`, `severity`, and evidence).
2. Read `outputs/charter.json` (the locked asks, their `statement`s, `acceptance`,
   `usable_bar`).
3. Read the ledger's last ~2 waves (`outputs/ledger.jsonl`) for recent history —
   verdicts, decisions, and unit outcomes across those waves, not just this one.
4. Read this wave's own unit reports (`outputs/waves/wJJ/*.md`) and breadcrumbs
   (`outputs/progress/*.json`) — your task's `inputs` list every breadcrumb path for
   this wave explicitly.
5. Read any hold answer (`control/hold-answer.md`) if one exists — it means a prior
   checkpoint held and an operator has since answered.
6. If a previous `check-result.json` already exists in your own checkpoint directory
   (`outputs/checkpoints/<your-checkpoint-id>/check-result.json`), **read it first,
   before writing anything** — it names exactly which rule your last attempt at this
   checkpoint failed, once the checker exists (see "Self-check" below).

## Judge alignment, per ask
For **every** ask in the charter, decide its `alignment[]` status
(`on_track|at_risk|drifted|met|deferred`) and write `evidence` that **quotes that ask's
own charter `statement`** back — not a paraphrase — so the judgment is traceably
anchored to the exact text intake locked in. If you mark an ask `deferred`, give a
`deferred_reason` (`out_of_budget|blocked_needs_input|human_descoped|infeasible`).

## Answer every signal — no exceptions
Your verdict's `signal_responses[]` must contain **one entry per signal id** in
`digest.signals[]` — every signal, with no exceptions; this is mechanically checked
(`OV-R12`, once the checker lands). For each: pick a `response`
(`redirect|descope|accept|hold`); `accept` on a `high`-severity signal requires a
`rationale` of at least 40 characters explaining why accepting the repeat/stall/drift
is actually the right call, not a shrug.

## Update progress
For **every** acceptance criterion across the charter, set its `criteria[]` status
(`met|unmet|deferred`), using the exact `id` (`A<n>.<m>`) intake assigned in
`charter.json`'s `asks[].acceptance[].id` — do not renumber or invent new ids here.

## Choose your decision
Pick `decision` from `digest.allowed_decisions` **only** — never a decision the current
stage forbids, even if you think it would be the better call (see the contract's
"Budget stages and allowed decisions" table: `explore`/`converge` allow
`continue|redirect|hold|stabilize|closeout`*; `stabilize` allows only
`stabilize|closeout|hold`; `closeout`/`must_close` forces `closeout`). In `converge`,
`continue`/`redirect` may only target **existing** work items — no new scope.

\* **Early closeout** (deciding `closeout` before the run has reached the `stabilize`
or `closeout` stage) is only valid when the ledger already shows, for every
non-deferred ask, a `verify`-kind unit with `verdict: pass` recorded after that ask's
last `implement`/`fix`/`stabilize`/`document` unit — "done early" must be verified with
evidence, not merely asserted in your own rationale.

## Write your outputs
- `verdict.json` (schema `ao.overseer.verdict/v1` in the contract): `decision`,
  `rationale`, `next_wave_goal`, `alignment[]`, `criteria[]`, `signal_responses[]`, and
  `hold_questions` (required, ≥1, iff `decision == "hold"`).
- `report.md`: the human-readable state of the run — this is what an operator reads
  via `ao status`. State, per ask: done / in progress / current usable status; and the
  run's overall budget stage.
- If continuing/redirecting/stabilizing: the next wave's briefs
  (`outputs/waves/w(JJ+1)/briefs/*.json`) and the manifest at
  `outputs/manifests/ck-JJ.json` (your own checkpoint id, not the next one's), per the
  contract's "Unit" and "Next checkpoint" shapes.

## Wave-size and stage rules for what you emit
- Any wave you emit must hold **at most `digest.cadence.allowed_wave_size`** units —
  this ceiling can be tighter than the config's raw `wave_size` once the time cap or the
  stage (`stabilize`/`closeout`, driven by actual spend) applies.
- In the **`stabilize`** stage: emit **only** `stabilize`/`verify`/`document` kind
  units — no `implement`/`research`/`design`/`fix` (new scope is not allowed here).
- In the **`closeout`** stage, or whenever `digest.must_close` is true: emit **only**
  the tail — `final-verify → closeout → final-push` (the last iff `final_push` is
  true for this run) — per the contract's "Tail" shape. Do not emit a wave alongside
  the tail; it is one or the other, never both.

## If you decide to hold
Write `control/hold-request.json` (schema `ao.overseer.hold/v1`: `questions`,
`needs_input_path`) plus `needs-input/ck-JJ.md` (your own checkpoint id) explaining
what you need answered. Then emit the **next** checkpoint with a completely **empty**
wave and `depends_on` set to only **your own checkpoint id** — this is exactly the
contract's hold shape (`["E"]` where `E` is you, the emitter). That next checkpoint's
own `ckpt-prep` will refuse to dispatch (a `HOLD:` failure, at $0) until an operator
writes `control/hold-answer.md` and resumes the run — see this template's `README.md`,
"Human-in-the-loop (hold)".

## Self-check before you exit
Once the checker lands, run this template's own checker in dry-run mode and fix every
violation it reports:

```bash
<python_bin> <workspace_root>/<instance_dir>/tools/overseer_tool.py \
  ckpt-check --dry-run --task-id <your-checkpoint-id> \
  --workspace-root <workspace_root> --instance-dir <instance_dir>
```

**Note:** `ckpt-check` does not exist as a subcommand yet in this epic (a later, M3
task adds it) — until then, re-read `overseer-contract.md`'s "Hard rules" section by
hand (the `OV-R*` rules) and check your own manifest and verdict against them before
you exit, rather than trying to run a command that isn't wired up yet. If a previous
`check-result.json` exists in your own checkpoint directory, you already read it in
step 6 above — fix exactly what it named before you emit anything new.
