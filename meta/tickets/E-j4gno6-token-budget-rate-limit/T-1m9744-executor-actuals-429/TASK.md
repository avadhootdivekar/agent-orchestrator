# TASK: T-1m9744-executor-actuals-429

## Metadata
- Task ID: `T-1m9744-executor-actuals-429`
- Epic ID: `E-j4gno6-token-budget-rate-limit`
- Owner: TODO
- Created: 2026-06-18
- Last Updated: 2026-06-18
- Status: Draft
- Estimate: < 3 days

## Requirements Mapping
- Requirement IDs: FR-5, FR-8, NFR-4

## Description
Capture **actual** token usage and detect **provider 429** in `ClaudeCliExecutor`.

1. Invoke `claude` with `--output-format json` so stdout carries a parseable result
   object. Append the flag (idempotently — only if not already in `command_template`/`extra_args`)
   in `executors/claude_cli.py`.
2. Parse the `usage` block from stdout JSON: `input_tokens`, `output_tokens`,
   `cache_creation_input_tokens`, `cache_read_input_tokens`. Set the new `TaskResult`
   fields and `actuals_available=True`. Keep writing the raw stdout/stderr capture as today.
3. **Fallback**: if stdout is not JSON / missing `usage` / parse error / timeout,
   leave token fields `None` and `actuals_available=False` (engine keeps the estimate — FR-5).
4. **429 detection** (FR-8): if the CLI returns a 429 signal (non-zero exit with a
   rate-limit error string, or a JSON error object with a rate-limit type, or a
   `retry-after` value), set `provider_rate_limited=True` and
   `provider_retry_after_epoch` from the surfaced reset time (None if absent). Isolate
   all of this in one function `parse_usage_and_429(stdout, stderr, returncode, now_epoch)`.
5. Extend `FakeExecutor` so tests can inject token actuals and a 429 signal per task
   (e.g. `token_outputs: dict[task_id, dict]`, `rate_limit_after: dict[task_id, float]`).

VERIFY FIRST (OPEN_QUESTION): run the installed `claude --output-format json -p "hi"`
once and record the real `usage` JSON shape into a test fixture before freezing the
parser. If the shape differs from the assumed field names, update the parser and note
it in this TASK's STATUS.

## Acceptance Criteria
1. Given a stdout JSON containing `{"usage":{"input_tokens":120,"output_tokens":80,"cache_creation_input_tokens":0,"cache_read_input_tokens":10}}` and exit 0, When `execute` runs, Then `TaskResult.input_tokens==120`, `output_tokens==80`, `cache_read_input_tokens==10`, `actuals_available==True`, `status=="succeeded"`.
2. Given non-JSON stdout (e.g. plain text) and exit 0, When `execute` runs, Then token fields are `None`, `actuals_available==False`, and `status=="succeeded"` (graceful fallback — no crash).
3. Given a timeout, When `execute` runs, Then `status=="timed_out"`, `actuals_available==False` (unchanged timeout behavior preserved).
4. Given a CLI exit indicating 429 (rate-limit error string OR JSON error with `retry-after`/reset), When `execute` runs, Then `provider_rate_limited==True` and `provider_retry_after_epoch` equals the surfaced reset epoch (or `None` when not surfaced).
5. Given `--output-format json` is **already** present in the agent's command/extra args, When `execute` builds argv, Then the flag is **not** duplicated.
6. `parse_usage_and_429` is pure (string + ints in → result out) and unit-tested across: valid usage, missing `usage`, malformed JSON, 429 with retry-after, 429 without retry-after.
7. `FakeExecutor` honors injected `token_outputs` / `rate_limit_after` so integration tests in T-67kiia exercise reconcile + 429 paths. `ruff`/`mypy`/`pytest` green; NFR-1 preserved (parsing stdout the executor itself produced is not an artifact-content read).

## Risks
- R4 (epic): real `usage` JSON shape unverified — MUST confirm against the installed CLI (AC-6 fixture) before freezing. If unavailable in the env, mark BLOCKED with the assumed shape documented.
- `--output-format json` may change how the agent's own outputs are surfaced; ensure the agent still writes its declared output files (the JSON wraps the run result, not the agent's artifact writes).

## Dependencies
- Upstream: T-oh5gl5 (`TaskResult` token fields).
- Downstream: T-algywf (engine reads `TaskResult` token fields to reconcile + route 429 to the budget handler).

## Pseudocode / Algorithm
```text
FUNCTION parse_usage_and_429(stdout, stderr, returncode, now_epoch) -> dict:
  result = {actuals_available: False, provider_rate_limited: False, retry_after_epoch: None,
            input:None, output:None, cache_creation:None, cache_read:None}
  TRY:
    obj = json.loads(stdout)
    usage = obj.get("usage")
    IF usage is dict:
      result.input  = usage.get("input_tokens")
      result.output = usage.get("output_tokens")
      result.cache_creation = usage.get("cache_creation_input_tokens")
      result.cache_read     = usage.get("cache_read_input_tokens")
      result.actuals_available = (result.input is not None OR result.output is not None)
    # JSON error object with rate limit
    err = obj.get("error") if isinstance(obj, dict) else None
    IF err and is_rate_limit(err): result.provider_rate_limited=True; result.retry_after_epoch=reset_of(err, now_epoch)
  EXCEPT json.JSONDecodeError: pass            # fallback: actuals stay unavailable
  # exit/stderr-based 429 detection
  IF returncode != 0 AND looks_like_429(stderr or stdout):
    result.provider_rate_limited = True
    result.retry_after_epoch = result.retry_after_epoch or retry_after_from(stderr, now_epoch)
  RETURN result
```

## Schemas / Interface Notes
- Interface / API: `TaskResult` gains `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `actuals_available`, `provider_rate_limited`, `provider_retry_after_epoch` (from T-oh5gl5 contract). New executor helper `parse_usage_and_429(...)`.
- Spec / data schema: parses provider CLI `usage` JSON (recorded fixture under `tests/fixtures/claude_usage.json`).
- Triggers / events: N/A (engine emits `budget.provider_429`).
- Artifacts: writes `stdout.txt`/`stderr.txt` as today; reads only its own subprocess stdout.

## Handoff Boundary
- Upstream: T-oh5gl5 (`TaskResult` fields).
- Downstream: engine consumes `TaskResult` token fields + 429 flags.
