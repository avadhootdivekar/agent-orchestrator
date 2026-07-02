# Playground — AO End-to-End Example Corpus

The `playground/` directory holds small, self-contained workflow examples
designed to be run end-to-end through the `ao` CLI, proving the DAG,
dynamic task injection, loops, token budgeting, logging, and per-task
output capture all work correctly.

Each example is a standalone product asset (committed, like `specs/examples/`)
that the test suite copies into an isolated `tmp_path` before running. Real
runs under the `real_llm` tier use the same specs against live Claude agents.

---

## Corpus layout

```
playground/
  <example>/
    PROBLEM.md            # trivial, low-burn problem statement
    workflow.json         # static spine + emit_tasks fan-out + LoopSpec
    reposet.json          # one repo set, workspace_root "."
    agents.fake.json      # every agent -> {"executor": "fake"}
    agents.claude.json    # every agent -> claude_cli (4 allowed keys only)
    instructions/         # one .md per task role; breakdown + final-review
                          # pin control-file contracts for the real-LLM tier
    fixtures/             # deterministic-tier pre-seed and expected structure
      tasks-manifest.json        # pre-seed for emit_tasks manifest
      final-verdict.json         # pre-seed gate verdict (iter 1): {"continue": false}
      final-verdict-iter2.json   # pre-seed gate verdict (iter 2): {"continue": false}
      expected_paths.json        # golden file/dir tree asserted by the det. tier
      expected_events.json       # expected run.log event types + task.start order
```

### Phase-1 example: `sum-of-array/`

Implements a trivial `sum(nums: list[int]) -> int` function. The workflow
shape (static spine + `emit_tasks` + `LoopSpec`) is the proven template for
higher-complexity examples in Phase 2.

---

## Workflow shape (per example)

Each example workflow encodes the design → implement → review agent pipeline:

```
architect-design → design-review → architect-breakdown(emit_tasks)
    [injected from manifest: impl-t1 → testwrite-t1 → taskreview-t1]
integrate → [loop review-round: bugfix → final-review] → done
```

- `architect-breakdown` emits a fixed task chain at `output/tasks-manifest.json`.
- `integrate` anchors after the injected chain via an inferred edge
  (`inputs: [output/tasks/t1/review.md]`).
- `review-round` (LoopSpec) runs `bugfix → final-review` per iteration; the gate
  task (`final-review`) writes `{"continue": bool}` to `output/final-verdict.json`.
- `done` has `depends_on: ["review-round"]` which the engine resolves to the final
  iteration's last body task.

---

## Test tiers

| Tier | What runs | Gate | Asserts |
|------|-----------|------|---------|
| 1 Fixture | Schema validation + DAG acyclicity | always | specs valid; expanded DAG acyclic |
| 2 Deterministic | `ao run` + FakeExecutor + pre-seeded control files | always | paths/names/log structure/token math |
| 3 Real-LLM (fuzzy) | `ao run` + ClaudeCliExecutor | `real_llm` + `AO_E2E_REAL_LLM=1` | completion/exit — never content |
| 4 Perf + correctness | FakeExecutor (perf) + ClaudeCli (correctness) | perf always; correctness gated | wall-clock; functional |

---

## How to run

```bash
# Tier 1 + 2 + perf (always-green, zero token burn):
uv run pytest tests/playground -q

# Tier 3 real-LLM (opt-in; requires claude CLI and auth):
AO_E2E_REAL_LLM=1 uv run pytest tests/playground -m real_llm

# Tier 4 correctness only (gated):
AO_E2E_REAL_LLM=1 uv run pytest tests/playground -m "real_llm and perf"

# Validate a playground spec without running:
uv run ao validate \
  --workflow playground/sum-of-array/workflow.json \
  --reposets playground/sum-of-array/reposet.json \
  --agents   playground/sum-of-array/agents.fake.json
```

---

## Adding a new example

1. Create `playground/<name>/` with the same structure as `sum-of-array/`.
2. Keep the problem trivial (low token burn for the real-LLM tier).
3. Author `instructions/architect-breakdown.md` to pin the EXACT manifest
   contract (task ids, depends_on, paths).
4. Author `instructions/final-review.md` to pin writing
   `{"continue": <bool>}` to `output/final-verdict.json`.
5. Run `ao validate` on both agents files to confirm the spec is valid.
6. Check the expanded DAG is acyclic (see `tests/playground/harness.py`
   `expanded_workflow` + `build_dag`).
7. Author `fixtures/expected_paths.json` and `fixtures/expected_events.json`.

---

## Key Design Facts (authoritative; full rationale in `docs-md/e2e-playground-testing.md`)

- **CLI drives payload-less FakeExecutor.** The `DispatchExecutor` builds
  `FakeExecutor()` with no payloads, so it never writes control files.
  The deterministic tier **pre-seeds** them as fixtures before `ao run`.
- **One spec, two tiers.** The same `workflow.json` runs deterministic (fake,
  pre-seeded) and real (LLM-generated). Only the `--agents` flag switches.
- **Unique artifact paths** in the injected chain prevent `build_dag` from
  inferring spurious cycles (`output/tasks/t1/{impl,tests,review}.md`).
- **ADR-002:** pre-seed control files rather than adding a scripted executor
  or changing the schema.
