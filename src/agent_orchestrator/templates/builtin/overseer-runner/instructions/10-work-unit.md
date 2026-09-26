**instructions-version: 1**

The per-run `overseer-contract.md` is authoritative for ids, paths, and JSON shapes. Where this file and the contract disagree, the contract wins.

# Wave unit — do the kind-appropriate work, report honestly, never fail the run over it

## Your role
You are whichever agent `overseer-contract.md`'s "Kind → agent/instruction map" names
for your unit's `kind` (`research`/`design` → `architect`; `implement`/`fix`/
`document` → `developer`; `test`/`verify` → `tester`; `review` → `reviewer`). This one
instruction file serves all of those kinds — at dispatch time you are one specific
unit with one specific kind, read from your own brief. (`kind: stabilize` uses a
separate instruction, `11-stabilize-unit.md`, not this file.)

## Inputs
Your task's own `inputs` include `overseer-contract.md`, `outputs/charter.json`, and
your own brief at `outputs/waves/wJJ/briefs/<your-unit-id>.json` — read the brief path
from your own task's `inputs`, never assume or hardcode it. The brief (schema
`ao.overseer.brief/v1`, in the contract) tells you your `kind`, `goal`, `acceptance`
list, `ask_ids`, `work_item` key, any `prior_attempts` on this same work item, and
`approach_change` if this is a repeat attempt — read `approach_change` first if it is
present: it exists because a prior attempt on this same `work_item` didn't land, and it
tells you what to do differently this time.

## Step 1 — Read your brief and the ask(s) it serves
Cross-reference `ask_ids` against `outputs/charter.json` to see the real ask
`statement`(s) and `usable_bar`(s) your unit is in service of — your `goal` and
`acceptance` in the brief should already be scoped to this, but the charter is the
ground truth if anything is unclear.

## Step 2 — Do the kind-appropriate work
| kind | what "the work" means |
|---|---|
| `research` | Investigate the brief's open question(s) directly; produce a clear, evidence-backed answer — not more open questions unless genuinely unavoidable. |
| `design` | Produce a concrete design/plan artifact sized to the brief's `goal` — enough to implement from, not a restatement of the ask. |
| `implement` | Write real, working changes in the target repo(s) that satisfy the brief's `acceptance` — production code, not pseudocode or a plan. |
| `test` | Write and run tests that exercise the brief's `acceptance`; report the actual pass/fail counts you observed. |
| `review` | Critically review the named prior work against the brief's `acceptance` and the ask's `usable_bar`; report concrete findings, not a rubber stamp. |
| `fix` | Address the specific defect/gap the brief names; do not silently expand scope beyond it. |
| `document` | Write or update documentation so it accurately describes the actual current behavior — never describe planned/aspirational behavior as if it already exists. |
| `verify` | Independently confirm whether the ask's acceptance criteria and usable bar are actually met right now — **do not fix anything**, report only (the same "report, don't fix" rule `40-final-verify.md` uses, applied mid-run to one ask). |

Use `context_paths`/`touches` from your brief as your starting map of what's relevant;
they are hints, not an exhaustive boundary.

## Step 3 — Write your report and breadcrumb
- **Report** (human-readable) at `outputs/waves/wJJ/<your-unit-id>.md`: what you did,
  what evidence backs it, and your own honest verdict.
- **Breadcrumb** (machine-readable, schema `ao.overseer.breadcrumb/v1` in the
  contract) at `outputs/progress/<your-unit-id>.json`: `unit_id` (must equal your own
  id), `outcome` (`done|partial|blocked|no_op`), `verdict` (`pass|fail|na`), `summary`,
  `changed_paths` (each entry `"<repo_id>:<relative path>"`, `repo_id` from your
  prompt's Repos line), `acceptance_met` (which of the brief's `acceptance` items you
  actually satisfied), `followups`, and `needs_input`.

## The critical rule — never exit non-zero because the work itself was hard
**A non-zero exit halts the entire run.** "This ask turned out to be hard, blocked, or
only partially achievable" is not a task failure — it is information for the
overseer's next checkpoint. If you could not finish: set `outcome: blocked` or
`outcome: partial` in your breadcrumb, write an honest report explaining exactly what's
blocking you or what's left, and **still exit 0**. Reserve a non-zero exit for genuine
tooling/environment failures that make it impossible to even write your report and
breadcrumb at all — never for "the task was hard" or "I disagree with the brief's
scope".

## If you need human input
Set `needs_input: true` in your breadcrumb, and write `needs-input/<your-unit-id>.md`
describing exactly what you need answered and why you can't proceed without it. **Do
NOT touch any control flag yourself** (no `control/pause*.flag`, no
`control/halt*.flag`) — only the checkpoint that reviews your wave decides whether the
run actually holds for human input; your job is only to surface the need honestly via
your breadcrumb and the `needs-input/` file.
