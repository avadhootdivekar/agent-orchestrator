# Retry context and the `retry=false` outcome marker

Implemented in `src/agent_orchestrator/retry_context.py`, wired into `Orchestrator._run_with_retries`.

## Prior-attempt context
From attempt 2 onward the engine writes `<task output_dir>/attempt-<n>/prior-attempts.md` before the
executor runs, and the prompt gets a one-sentence clause naming that path (`TaskContext.prior_attempts_path`;
paths only, NFR-1). Attempt 1 prompts are unchanged.

The file lists the newest failed attempt in full (status, exit code, failure class, `error` truncated to 500
chars, a fixed hint, and the paths of its `stderr.txt` / `transcript.jsonl` / `result.json`) and older attempts
as one line each. It is capped at 4 KB. Failure classes: `timeout`, `max_turns` (`error_max_turns` in
`result.json`), `exit_nonzero`, `other`. It is deterministic; there is no model summarizer.
Only attempts within one `_run_with_retries` call are listed (not previous cycles or resumed runs).

## Non-retryable marker
A task may write `<attempt output_dir>/outcome.json`:

```json
{"schema": "ao.task.outcome/v1", "retry": false, "reason": "<=300 chars"}
```

If the attempt did not succeed and the marker is valid (<= 4 KB, correct schema, `retry` is `false`), the engine
logs `task.retry_suppressed`, skips the remaining attempts and backoff, and returns the failed result. A missing,
oversized, malformed or `retry: true` marker means normal retry, so default and transient-failure retry
behaviour (including a killed process, which cannot write a marker) is unchanged. The marker never turns a
failure into a success.

`RetryPolicy.honor_marker` (default `false`) only controls whether the prompt tells the agent about the marker
(`TaskContext.outcome_marker_path`); the engine honours a valid marker either way.

## Not done
Self-heal is not yet told about the marker; the optional haiku summarizer and automatic timeout/max-turns
budget escalation are not implemented.
