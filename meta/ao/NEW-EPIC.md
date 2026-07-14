# Starting a new epic with AO

This is the step-by-step guide to drive a new feature epic from requirements to done using the agent orchestrator.

## Prerequisites

- `ao --help` works (`ao` globally installed via `./install.sh`)
- You have a rough idea of what you want to build

## Step 1 — Pick an epic ID

Use a **lowercase** ID for the workflow (schema requirement). Format: `e-<6-alnum>-<slug>` (e.g. `e-abc123-my-feature`).
The ticket system under `meta/tickets/` uses the standard `E-<6-alnum>-<slug>` uppercase prefix — the `create-tasks` stage handles that mapping.

```bash
EPIC_ID="e-abc123-my-feature"    # lowercase, used for workflow + epics/ dir
```

## Step 2 — Create the epic directory and write your prompt

```bash
mkdir -p meta/ao/epics/$EPIC_ID/outputs

# Write your requirements prompt — be as detailed or rough as you like,
# the gather-requirements stage will structure it.
cat > meta/ao/epics/$EPIC_ID/prompt.md << 'EOF'
# Requirements for <epic title>

## What I want to build
<describe the feature>

## Why
<motivation / problem being solved>

## Key requirements / constraints
<bullet list>

## What I don't want (out of scope)
<bullet list>

## Any other context
<links, examples, prior art>
EOF
```

## Step 3 — Instantiate the workflow

```bash
cp meta/ao/workflows/epic-lifecycle-template.json meta/ao/epics/$EPIC_ID/workflow.json

# Replace __epic_id__ placeholder with the actual epic ID
sed -i "s/__epic_id__/$EPIC_ID/g" meta/ao/epics/$EPIC_ID/workflow.json
```

## Step 4 — Validate the workflow spec

```bash
ao validate \
  --workflow  meta/ao/epics/$EPIC_ID/workflow.json \
  --agents    meta/ao/agents.json \
  --reposets  meta/ao/reposets.json
```

All validation checks must pass before running.

## Step 5 — Run the workflow

```bash
ao run \
  --workflow  meta/ao/epics/$EPIC_ID/workflow.json \
  --agents    meta/ao/agents.json \
  --reposets  meta/ao/reposets.json
```

The pipeline runs sequentially:
1. `gather-requirements` (~20 min) → `outputs/requirements.md`
2. `design-epic` (~40 min, Opus) → `outputs/adr.md` + `hld.md` + `lld.md`
3. `create-tasks` (~30 min) → `outputs/tasks.json` + ticket files in `meta/tickets/`
4. `implement-tasks` (~2-4 hours, manager) → `outputs/impl-report.md`
5. `critic-review` (~30 min) → `outputs/critic-report.md`
6. `architect-review` (~40 min, Opus) → `outputs/final-review.md`

## Step 6 — Resume if interrupted

If the run is interrupted at any stage, resume from where it left off:

```bash
ao resume \
  --workflow  meta/ao/epics/$EPIC_ID/workflow.json \
  --agents    meta/ao/agents.json \
  --reposets  meta/ao/reposets.json
```

Stages with `skip_if_outputs_exist: true` will be skipped if their outputs are already on disk.

## Step 7 — Check status

```bash
ao status --workflow meta/ao/epics/$EPIC_ID/workflow.json
```

## Step 8 — Review outputs

After the workflow completes:
- `outputs/final-review.md` — architect's sign-off verdict (APPROVED / APPROVED WITH CONDITIONS / NOT APPROVED)
- `meta/tickets/$EPIC_ID/` — fully populated ticket files
- `docs-md/` — updated design docs

If the verdict is NOT APPROVED, address the items listed, then rerun the affected stages by deleting the relevant output files and re-running `resume`.

## Notes

- Each task has `timeout_seconds` set conservatively. Increase in the workflow.json if agents need more time.
- The `implement-tasks` task has `skip_if_outputs_exist: false` — it always reruns so the manager can handle partial task completion.
- The critic and architect review tasks also have `skip_if_outputs_exist: false` so they always produce fresh reviews.
- To rerun a specific stage: delete its output file(s) and run `resume`.
