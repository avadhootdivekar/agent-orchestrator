# HANDOFF: T-g7rjh0-real-llm-gated-harness

## Tier Bridge: Deterministic ↔ Real-LLM

### One Spec, Two Tiers

The **same** `playground/sum-of-array/workflow.json` runs through both tiers:

1. **Deterministic Tier (T-r21p4y)**
   - Uses `agents.fake.json` (FakeExecutor, payload-less)
   - **Pre-seeds** control files from fixtures:
     - `fixtures/tasks-manifest.json` → `output/tasks-manifest.json`
     - `fixtures/final-verdict.json` → `output/final-verdict.json`
     - `fixtures/final-verdict-iter2.json` → `output/final-verdict__iter2.json` (for 2-round variant)
   - Assertions: paths, structure, order, counts, token math — **never content**
   - Always green in CI; zero token burn

2. **Real-LLM Tier (T-g7rjh0)**
   - Uses `agents.claude.json` (ClaudeCliExecutor)
   - **NO pre-seeding** — real architect/reviewer agents WRITE control files themselves
   - Same CLI invocation shape as deterministic tier (same flags, workflow path)
   - Assertions: structure, exit, completion, path existence — **never content**
   - Gated by `@pytest.mark.real_llm` + `AO_E2E_REAL_LLM=1` env var
   - Skipped by default; opt-in only

### Why This Works

**Design Fact 1 (CLI → payload-less FakeExecutor):**
- FakeExecutor is constructed with NO `emit_payloads` / `gate_payloads` / `manifest_payloads` / `token_outputs` config.
- It **only** writes declared output files + stdout/stderr capture stubs.
- It **never** writes control files (`task_manifest_path`, `gate_output_path`, `output_manifest`).

**Design Fact 2 (Control-file pre-seeding, ADR-002):**
- The engine reads three machine-written control files by path:
  - `task_manifest_path` (emit_tasks injection point)
  - `gate_output_path` (loop verdict, iteration-suffixed)
  - `output_manifest` (optional dynamic outputs)
- Because FakeExecutor leaves these untouched, the deterministic tier **pre-seeds them as fixtures on disk**.
- The SAME engine run path works with both tiers: it just reads whatever files exist.

**Design Fact 5 (Ordering after dynamic injection):**
- The `integrate` task depends on `output/tasks/t1/review.md` (produced by injected `taskreview-t1`).
- Once the manifest is injected, the DAG is rebuilt, and the inferred edge ensures correct ordering.
- This works identically whether the manifest was pre-seeded (deterministic) or agent-written (real).

### Fixture-to-Real Mapping

| Aspect | Deterministic | Real-LLM |
|--------|---------------|----------|
| Agents file | `agents.fake.json` | `agents.claude.json` |
| `tasks-manifest.json` | Pre-seeded from `fixtures/` | Agent-written by architect-breakdown |
| `final-verdict.json` | Pre-seeded (one or two rounds) | Agent-written by final-review |
| Assertion target | Structure, paths, order, counts | Structure, exit, completion |
| Content assertion | Never | Never |
| CI gate | Always runs | Skip unless `AO_E2E_REAL_LLM=1` |
| Token burn | 0 (FakeExecutor) | Real token cost (agent calls) |

### Harness Reusability

Both tiers use identical helper functions:
- `copy_example()` — isolates example into tmp_path
- `run_cli()` — invokes `ao` CLI via CliRunner
- `agents_for()` — selects agent file by tier
- `_extract_run_id()` — parses CLI output for run_id
- `_read_state()` — loads state.json from run artifacts

The **only difference** in invocation:
```python
# Deterministic
seed_control_files(tmp_path, rounds=1)
result = run_cli([..., "--agents", agents_for("fake")], tmp_path)

# Real-LLM
requires_claude()  # Skip if claude binary absent
# NO pre-seeding
result = run_cli([..., "--agents", agents_for("real")], tmp_path)
```

### Extending to New Examples

When adding `sorting/` or `student-data/` examples (T-n477z9, T-94tepb):

1. Create `playground/<example>/workflow.json` with the same spine pattern (architect → review → breakdown → {injected chain} → integrate → review-loop → done).
2. Create `playground/<example>/fixtures/tasks-manifest.json` with the injected tasks.
3. Create `playground/<example>/fixtures/final-verdict.json` (and optionally `-iter2.json` for 2-round testing).
4. Create `playground/<example>/fixtures/expected_paths.json` and `expected_events.json` (with `one_round`/`two_round` scenarios).
5. Add deterministic tests to `test_sum_of_array_deterministic.py` via parametrization, or create `test_sorting_deterministic.py` following the same pattern.
6. Real-LLM tier automatically works: re-run `test_sum_of_array_real_llm.py` with a new example (or parametrize it).

### Known Limitations and Future Work

- **OQ-2 (Multi-chain generalization):** MVP fixes N=1 injected chain. Multiple parallel chains require an injected aggregator task so `final-review` doesn't hardcode chain count. Deferred.
- **OQ-1 (Fuzzy input variation):** Phase 1 uses fixed tiny arrays. A seeded generator for randomized inputs is future work.
- **Real-tier flakiness:** Agents may occasionally timeout or produce unexpected manifest shapes. Mitigation: never assert exact ids/paths, only structure. If an agent-quality issue occurs, it's tracked separately (e.g., OQ-2 or instruction-quality signal).

### References

- Design doc: `docs-md/e2e-playground-testing.md` (§7 HLD, §8 tiers, §9 ADRs)
- ADR-002: Control-file pre-seeding vs scripted executor
- ADR-003: Real-LLM tier gating (marker + env, belt-and-suspenders)
- ADR-005: One spec, two agents files (vs per-tier workflow forks)
- Key design facts: EPIC.md §"Key Design Facts"
