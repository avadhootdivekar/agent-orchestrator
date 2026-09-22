# Late-gate evidence — `specs/examples/workflow-dry-run-flag.json`

Real end-to-end engine execution of Epic C's worked example, using the `fake` executor (no
network/LLM calls) so it's reproducible and free. This is in addition to C3's own `ao validate`
schema-validation evidence (`meta/tickets/E-DOiDqE-workflow-authoring-skill/T-PLsJdO-worked-example/STATUS.md`)
— `ao validate` proves the spec is schema-valid; this proves the DAG actually **resolves and
dispatches** in the right order, `pre_hook`/`post_hook` actually fire, and
`ao report-outcomes --grade` actually works against a real run derived from it.

## How this was produced

`agents-fake.json` (copied here) sets every agent's `executor` to `"fake"` — the only change
from `specs/examples/agents.json`. Run from the repo root so the hooks' relative script paths
(`specs/examples/hooks/*.py`) resolve, with the real `specs/examples/reposet.json`:

```bash
ao run --workflow specs/examples/workflow-dry-run-flag.json \
  --reposets specs/examples/reposet.json --agents agents-fake.json
# -> run.jsonl.txt (this dir), status.json (this dir)

ao report-outcomes --run-id <run_id> \
  --workflow specs/examples/workflow-dry-run-flag.json \
  --reposets specs/examples/reposet.json --agents agents-fake.json --grade grade
# -> report-outcomes.txt (this dir)
```

(`.txt` suffixes are deliberate — this repo's `.gitignore` has a blanket `*.log` rule, and these
transcripts are meant to be committed evidence, not ephemeral logs.)

## Result

- `run.jsonl.txt`: full structured event log (one JSON object per line, plus the human-readable
  summary table `ao run` prints at the end). `run.end` status `succeeded`; all 5 tasks
  (`design`, `implement-cli`, `implement-docs`, `test`, `review`) `succeeded`; both
  `implement-*` tasks' `pre_hook check_disk_space` passed; `test`'s `post_hook grade` passed.
  Confirms `implement-cli`/`implement-docs` both became ready (and, with `max_parallel`, would
  co-dispatch) only after `design` settled, and `test` only after both settled — the DAG edges
  from `depends_on` resolve exactly as the skill and the spec's own inline reasoning describe.
- `status.json`: engine's persisted run-state snapshot at completion (per-task status/attempts/
  timing) for the same run.
- `report-outcomes.txt`: `ao report-outcomes --grade grade` output for the same run — local
  outcome counts (attempts/dispatch-cycles/reruns/self-heal, all nominal) plus the **separate**
  post-run grading pass (all 5 tasks graded `passed`, score `1.00`) — the whole-run signal
  `.claude/skills/workflow-authoring/SKILL.md`'s "Hooks & grading" section distinguishes from
  the inline `post_hook`.

Not reproduced here: `.orchestrator/runs/<run_id>/` (the full run directory — per-task
transcripts, hook stdout/stderr, `settlement_grades.json`) and `output/dryrun/*.md` (the
FakeExecutor's generic stub content) were deleted after capturing the above — they are
regenerable by re-running the commands, and `.orchestrator/`/ad-hoc `output/dryrun/` are not
meant to be committed (no prior run state has ever been committed to this repo; checked via
`git log --all --diff-filter=A --name-only | grep '^\.orchestrator/'`, `.ao/runs/` before it,
same result).
