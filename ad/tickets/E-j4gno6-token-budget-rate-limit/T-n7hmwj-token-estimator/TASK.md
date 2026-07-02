# TASK: T-n7hmwj-token-estimator

## Metadata
- Task ID: `T-n7hmwj-token-estimator`
- Epic ID: `E-j4gno6-token-budget-rate-limit`
- Owner: TODO
- Created: 2026-06-18
- Last Updated: 2026-06-18
- Status: Draft
- Estimate: < 2 days

## Requirements Mapping
- Requirement IDs: FR-4, NFR-1, NFR-6, NFR-7

## Description
Build the pluggable pre-run token estimator. New module
`src/agent_orchestrator/estimator.py`:
- `TokenEstimator(ABC)` with `estimate(ctx: TaskContext, cfg: EstimatorConfig) -> int`.
- `HeuristicTokenEstimator(TokenEstimator)`: default impl.

The heuristic estimates **input** tokens from **file sizes** (NOT contents — NFR-1):
sum of byte sizes of `ctx.instruction_path`, every `ctx.input_paths`, and
`ctx.dynamic_input_paths`, divided by `cfg.chars_per_token`. Add `cfg.output_allowance_tokens`
for the (unknown) output. Multiply the whole thing by `cfg.pessimism_buffer`. Round up.

To read file **size** without reading content, add `size(path: str) -> int` to
`ArtifactStore` (and `LocalFsArtifactStore`): `os.stat(resolve(path)).st_size`,
returning 0 for a missing file (the engine's missing-input check handles real misses).
This is an `os.stat`, explicitly **not** a content read — document it as NFR-1-safe.

## Acceptance Criteria
1. Given a task whose instruction file is 400 bytes and one input file 800 bytes, with `chars_per_token=4`, `output_allowance_tokens=100`, `pessimism_buffer=1.3`, When `estimate` runs, Then it returns `ceil(((400+800)/4 + 100) * 1.3) = ceil((300+100)*1.3) = ceil(520) = 520`.
2. Given a missing/zero-size input file, When `estimate` runs, Then that file contributes 0 bytes (no crash) and the rest is computed normally.
3. Given `dynamic_input_paths` are present, When `estimate` runs, Then their sizes are included in the input total.
4. The estimator reads **no file contents** — `ArtifactStore.size()` uses `os.stat` only; a grep/audit confirms no `read_text`/`open(...).read()` on artifact paths in `estimator.py`.
5. `TokenEstimator` is an ABC injectable independently; a trivial stub `class FixedEstimator(TokenEstimator)` can be substituted in a unit test, proving pluggability (NFR-6).
6. All constants (`4`, `1.3`, `100/1000`) come from the injected `EstimatorConfig`, not literals in `estimator.py` (NFR-7). `ruff`/`mypy`/`pytest` green.

## Risks
- Byte size != char count for multi-byte UTF-8 (a 2-byte char inflates the estimate) — acceptable and conservative (pessimistic), document it.
- Directory inputs: `os.stat` on a directory returns dir-entry size, not recursive size. Document as a known limitation / OPEN_QUESTION (recursive sizing is a possible follow-up); MVP uses top-level stat.

## Dependencies
- Upstream: T-oh5gl5 (needs `EstimatorConfig`, `TaskContext`).
- Downstream: T-algywf (engine calls the estimator at the gate point).

## Pseudocode / Algorithm
```text
FUNCTION estimate(ctx, cfg) -> int:
  input_bytes = store.size(ctx.instruction_path)
  FOR p IN ctx.input_paths + ctx.dynamic_input_paths:
    input_bytes += store.size(p)          # os.stat only, 0 if missing
  input_tokens = input_bytes / cfg.chars_per_token
  raw = input_tokens + cfg.output_allowance_tokens
  RETURN ceil(raw * cfg.pessimism_buffer)
```

## Schemas / Interface Notes
- Interface / API: `TokenEstimator.estimate(ctx, cfg) -> int`; `ArtifactStore.size(path) -> int`.
- Spec / data schema: consumes `EstimatorConfig` from T-oh5gl5.
- Triggers / events: N/A.
- Artifacts (inputs/outputs by path): reads file **sizes** of instruction + input + dynamic-input paths; writes nothing.

## Handoff Boundary
- Upstream: T-oh5gl5 model shapes.
- Downstream: T-algywf consumes `estimate()` return as the gate charge.
