**instructions-version: 1**

The per-run `overseer-contract.md` is authoritative for ids, paths, and JSON shapes. Where this file and the contract disagree, the contract wins.

# Intake — split the prompt into a locked charter, plan wave 1

## Your role
You are the `architect` agent dispatched as `intake`, the second (and last) statically
declared task in this run, right after `git-branch-off`. Your job is the single most
consequential step in the whole run: turn the user's open-ended `prompt.md` into a
locked **charter** — the fixed reference every later checkpoint judges alignment
against — and plan the first wave of work. Everything downstream (every wave unit,
every `ck-JJ` checkpoint, the close-out tail) only exists because you emit it or a
later checkpoint does; there is no other static task after this one.

## Inputs
Your task's own `inputs` give you the exact paths for this run — read them from there,
never hardcode a path. They are, in order: `prompt.md`, `overseer-contract.md`,
`overseer-config.json`, and `git-go-ahead.md` (git-branch-off's report — read it for
context; you do not need to re-derive anything from it, its mere presence as your
input already proves the branch is safe).

## Step 1 — Split the prompt into asks
Read `prompt.md` in full. Identify every **distinct, independently-completable ask** it
contains (a prompt may hold several, one per bullet under its "Asks" section, or
implied by its prose). For each ask:
- Pick a `statement`: a **verbatim excerpt** quoted directly from `prompt.md` — do not
  paraphrase or summarize it. Every later checkpoint quotes this exact `statement` back
  as evidence when it judges alignment (per `overseer-contract.md`'s verdict schema),
  so word/select it as precisely as you want that judgment to be anchored.
- Classify its `deliverable_type` (`code|doc|plan|analysis|other`).
- Write its acceptance criteria: concrete, checkable statements of what "done" means
  for this ask specifically.
- Write its **`usable_bar`**: what still counts as a *usable* deliverable for this ask
  if the run has to stop early (budget exhausted, blocked, or an operator calls it
  done). This is the bar every later `stabilize`/`closeout` decision protects for this
  ask — write it as if you were told "you may only get 60% of the way there on this
  ask; what must still be true for it to be worth having?".
- Assign a `priority` (lower-priority-number asks are protected first if the budget
  runs tight — see the prompt's own "Budget notes" section for the user's guidance).

Write these into `charter.json` using the `ao.overseer.charter/v1` schema **exactly**
as given in `docs-md/overseer-runner-hld.md` §13.4 (**not** `overseer-contract.md`'s
"Agent-authored artifact schemas" section — that section is explicit that `charter.json`
is out of scope there, since it's written once by you and read-only afterward; the
contract only documents the brief/breadcrumb/verdict/hold-request files a checkpoint
itself reads and writes): each `asks[]` entry carries `ask_id`, `statement`,
`deliverable_type`, `acceptance`, `usable_bar`, `priority`; the charter also carries
`global_constraints`, `assumptions`, `out_of_scope`, and `open_questions` — populate
these from the prompt's own "Constraints" / "Budget notes" / "Out of scope" sections
where present, and from anything you had to assume or couldn't resolve.

Each `acceptance[]` entry is `{id: "A<n>.<m>", text}` — `<n>` is this ask's own number
(matching its `ask_id`, e.g. ask `A2`'s criteria are `A2.1`, `A2.2`, ...) and `<m>` is a
1-based counter within that ask. Every checkpoint's verdict later reports one
`criteria[]` status per exactly these ids (met/unmet/deferred, per the contract's
verdict schema) — get the ids right here, since nothing renumbers them later.

## Step 2 — Lock the prompt hash
Compute `prompt_sha256`: the **sha256 hex digest of `prompt.md`'s raw bytes** (hash the
file's bytes as read from disk, not a re-serialized or re-encoded copy of its text).
Store it as `charter.json`'s top-level `prompt_sha256` field. This is what later lets
the tool detect the `prompt_changed` signal / `OV-INT-1` (the prompt was edited after
you locked this charter) — get the hashing right, byte for byte.

## Step 3 — Write the charter files
Write `charter.json` (machine-readable, schema above) and `charter.md` (the same
content, human-readable — one section per ask: statement, deliverable type,
acceptance, usable bar, priority, plus the global constraints/assumptions/out-of-scope/
open-questions lists) at the exact output paths your task was given.

## Step 4 — Plan wave 1
Read `wave_size` from the rendered `overseer-config.json` — plan **at most that many**
work units for wave 1 (fewer is fine if the work genuinely doesn't need that many).
For each unit:
- Give it an id `w01-NN-<slug>` (see `overseer-contract.md`'s "Id patterns" section).
- Pick a `kind` from the enum in the contract's "Kind → agent/instruction map" table,
  and set `agent`/`instruction` to **exactly** what that map says for the kind — never
  invent a different agent or a different instruction path for a kind.
- Give it a `work_item` key (`A<n>/<slug>`) naming the stable thread of work this unit
  — and any later rework on the same thing, across future waves — belongs to. This is
  the key loop/rework detection tracks, so keep it stable once chosen.
- Reference the real charter `ask_id`(s) this unit serves in its `ask_ids`.
- Write its brief at the path the contract's brief schema specifies
  (`outputs/waves/w01/briefs/<unit-id>.json`), before that unit is meant to dispatch —
  the unit reads this brief as its own primary input.

Write the manifest (strict JSON `{"tasks": [...]}` — unit entries plus the next
checkpoint entry `ck-01`) at the exact `task_manifest_path` your task declares,
following the contract's "Unit" and "Next checkpoint" shapes literally: field names,
`pre_hook`/`post_hook` wiring, and `depends_on` exactly as shown there.

## If the prompt is genuinely ambiguous or unworkable
Do not guess at scope you can't ground in the prompt. Instead, emit an **empty wave**
(zero unit entries) plus `ck-01` in your manifest — per the contract's hold shape,
`ck-01`'s `depends_on` is `["intake"]` (your own id) when there are no wave units — and
write a hold request: `control/hold-request.json` (schema `ao.overseer.hold/v1` —
`questions`, `needs_input_path`) plus `needs-input/ck-01.md` explaining exactly what
you need answered before any real work can start. See this template's `README.md`,
"Human-in-the-loop (hold)" section, for the operator-side half of this flow (writing
`control/hold-answer.md` and resuming is what unblocks it). Still write
`charter.json`/`charter.md` first if you honestly can — a hold means "I don't know how
to plan safe work yet", not "I don't understand the asks" — write the best charter you
can even when the wave itself must wait on an answer.

## Self-check before you exit
Once the checker lands, run this template's own checker in dry-run mode and fix every
violation it reports, before you exit:

```bash
<python_bin> <workspace_root>/<instance_dir>/tools/overseer_tool.py \
  intake-check --dry-run --task-id intake \
  --workspace-root <workspace_root> --instance-dir <instance_dir>
```

(`<python_bin>`, `<workspace_root>`, `<instance_dir>` are the values your own task
prompt and `overseer-contract.md`'s "Paths for this run" section give you — never a
literal you invent.) **Note:** `intake-check` does not exist as a subcommand yet in
this epic (a later, M3 task adds it) — until then, re-read `overseer-contract.md`'s
"Hard rules" section by hand (the rules whose ids start `OV-R`) and check your own
manifest and charter against them before you exit, rather than trying to run a command
that isn't wired up yet.
