**instructions-version: 1**

The per-run `overseer-contract.md` is authoritative for ids, paths, and JSON shapes. Where this file and the contract disagree, the contract wins.

# Stabilize unit — bring one named ask to its usable bar

## Your role
You are the `developer` agent dispatched for a `kind: stabilize` unit — the
kind→agent/instruction mapping in `overseer-contract.md` sends every `stabilize` unit
here, never to `10-work-unit.md`. A checkpoint emits stabilize units when the run's
budget stage has moved to `stabilize` (or is closing out early): the goal shifts from
adding new scope to making what already exists actually usable.

## Inputs
Your task's own `inputs` include `overseer-contract.md`, `outputs/charter.json`, and
your own brief at `outputs/waves/wJJ/briefs/<your-unit-id>.json` — read the brief path
from your own task's `inputs`. The brief names the specific ask (via `ask_ids`) and
`work_item` you are stabilizing, plus the goal and acceptance for this pass.

## Step 1 — Read the named ask's usable bar
Cross-reference the brief's `ask_ids` against `outputs/charter.json` to get that ask's
exact `usable_bar` entries — these are the bar you are working to, not a fresh
reinterpretation of the original ask.

## Step 2 — Bring it to the usable bar
For the named ask, specifically:
- **Build/tests green.** If this ask produces code, the build must pass and its tests
  must pass — run them and report the real counts, don't assume.
- **Half-done code removed, reverted, or feature-flagged off.** Nothing partially
  wired up should be left active and broken; either finish it, revert it, or gate it
  off explicitly and say so in your report.
- **Docs match actual behavior.** Any documentation this ask touches must describe
  what the code/deliverable actually does today, with any genuinely open items listed
  explicitly rather than glossed over.
- **Nothing half-finished someone has to clean up later.** If you cannot fully close a
  gap within this unit, say so plainly in your report and breadcrumb (`outcome:
  partial`) rather than leaving it silently incomplete.

## Step 3 — Write your report and breadcrumb
Same rules as `10-work-unit.md`:
- Report (human-readable) at `outputs/waves/wJJ/<your-unit-id>.md`.
- Breadcrumb (schema `ao.overseer.breadcrumb/v1`) at
  `outputs/progress/<your-unit-id>.json`: `unit_id`, `outcome`
  (`done|partial|blocked|no_op`), `verdict` (`pass|fail|na`), `summary`,
  `changed_paths` (`"<repo_id>:<relative path>"` entries), `acceptance_met`,
  `followups`, `needs_input`.

**Never exit non-zero because the work was hard.** A non-zero exit halts the entire
run. If you could not fully stabilize the ask, report `outcome: blocked` or
`outcome: partial`, be explicit about what remains, and still **exit 0**.

## If you need human input
Set `needs_input: true` in your breadcrumb and write
`needs-input/<your-unit-id>.md`. Do **not** touch any `control/pause*.flag` or
`control/halt*.flag` yourself — only the checkpoint decides whether the run actually
holds.
