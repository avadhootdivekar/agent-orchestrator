# TASK: T-g7rjh0-real-llm-gated-harness

## Metadata
- Task ID: `T-g7rjh0-real-llm-gated-harness`
- Epic ID: `E-dvehbb-e2e-playground-tests`
- Owner: tester
- Created: 2026-07-01
- Last Updated: 2026-07-01
- Status: Done
- Estimate: `< 2 days`
- MVP: **yes** (harness skeleton + one gated smoke test)

## Requirements Mapping
- FR-5, FR-8, NFR-1, NFR-3, NFR-4. ADR-003, ADR-005.

## Description
Implement **Tier 3 — fuzzy / nondeterministic real-LLM** harness and one gated smoke test
for `sum-of-array`. The SAME workflow spec runs against `agents.claude.json`
(`ClaudeCliExecutor`), so the real architect/reviewer agents WRITE the control files
(`output/tasks-manifest.json`, `output/final-verdict.json`) themselves — no pre-seeding.
Every test here is marked `@pytest.mark.real_llm` and additionally guarded by
`AO_E2E_REAL_LLM=1`; it is skipped by default so normal CI never burns tokens or flakes.

Only **structure / exit / completion / path existence** are asserted — never
LLM-generated content. This task delivers the harness skeleton (reusable by later
examples) plus the single `sum-of-array` smoke test.

## Inputs / Outputs
- Inputs: `playground/sum-of-array/{workflow.json,reposet.json,agents.claude.json,instructions/*}`,
  the gate from `T-7592ux` (`conftest` marker skip + `agents_for("real")`), the deterministic
  run steps from `T-r21p4y` (mirrored without pre-seeding).
- Outputs: `tests/playground/test_sum_of_array_real_llm.py`; any shared real-tier helper in
  `tests/playground/_playground.py` (`requires_claude()` skip if `claude` binary absent).

## Acceptance Criteria
1. Given `AO_E2E_REAL_LLM` unset, When `uv run pytest -q` runs, Then every test in this
   module reports `skipped` (reason references `AO_E2E_REAL_LLM`); no `claude` subprocess spawns.
2. Given `AO_E2E_REAL_LLM=1` AND a `claude` binary is available, When
   `uv run pytest -m real_llm tests/playground/test_sum_of_array_real_llm.py` runs, Then
   `ao run` on `sum-of-array` with `agents.claude.json` exits 0 and `status.json`.status
   == "succeeded".
3. Given the same real run, When artifacts are inspected, Then the spine outputs exist
   (`output/design.md`, `output/design-review.md`, `output/integrated.md`,
   `output/final-review.md`, `output/summary.md`), `output/tasks-manifest.json` exists and
   parses as `{"tasks":[...]}`, and `output/final-verdict.json` parses as `{"continue":<bool>}`.
   NO assertion on the textual content of any of these.
4. Given `AO_E2E_REAL_LLM=1` but no `claude` binary, When the test runs, Then it `skips`
   with a clear reason (`requires_claude()`), not fails.
5. The real run reuses the deterministic run's CLI invocation shape (same flags) so the two
   tiers stay in lock-step; the only differences are the agents file and the absence of
   pre-seeding.
6. No `src/` change (NFR-1); `ruff`/`mypy` clean; default-collected suite unaffected (NFR-3).

## Risks
- Real agents may produce a manifest that doesn't match the pinned shape → assert only that
  a well-formed `{"tasks":[...]}` exists and the run completes; do NOT assert exact ids/paths.
  (If the run fails because the agent emitted a differently-shaped graph, that is a genuine
  instruction-quality signal, tracked as OQ-2, not a test bug.)
- Token burn / latency → problems are trivial and low-burn; the whole tier is opt-in.
- Environment coupling (`claude` auth) → `requires_claude()` skip keeps it non-fatal.

## Dependencies
- `T-1vuzyi` (spec + `agents.claude.json` + instructions), `T-7592ux` (gate),
  `T-r21p4y` (run-step shape to mirror).

## Pseudocode / Algorithm
```text
pytestmark = pytest.mark.real_llm     # module-level; gate hook skips unless AO_E2E_REAL_LLM=1

def requires_claude():
    if shutil.which("claude") is None: pytest.skip("claude binary not available")

def test_sum_of_array_real_completes(tmp_path):
    requires_claude()
    tmp = copy_example("sum-of-array", tmp_path)   # NO pre-seeding — real agents write control files
    r = run_cli(["run","--workflow","workflow.json","--reposets","reposet.json",
                 "--agents","agents.claude.json"], tmp)
    assert r.exit_code == 0
    st = read_status(tmp); assert st["status"] == "succeeded"
    assert (tmp/"output/tasks-manifest.json").exists()
    m = json.load(open(tmp/"output/tasks-manifest.json")); assert "tasks" in m
    for p in SPINE_OUTPUTS: assert (tmp/p).exists()     # existence only, no content
```

## Schemas / Interface Notes
- Interface / API: `ao` CLI via `CliRunner` (NFR-4); real `ClaudeCliExecutor` path.
- Spec / data schema: reads control files as `{"tasks":[...]}` / `{"continue":bool}` (structure only).
- Triggers / events: manual.
- Artifacts: real run writes under `<tmp>/output` + `<tmp>/.orchestrator`.

## Handoff Boundary
- Upstream: `T-1vuzyi`, `T-7592ux`, `T-r21p4y`.
- Downstream: `T-92o31p` (correctness tier reuses real-run outputs), later examples reuse the harness.

## Artifacts
- Docs/comments: this task folder + `HANDOFF.md` (tier bridge notes).
- Large outputs: none committed (real run artifacts are ephemeral in `<tmp>`).
