# TASK: T-nVWE1W-resume-extend-breaker-cli

## Metadata
- Task ID: `T-nVWE1W-resume-extend-breaker-cli`
- Epic ID: `E-3JTmVu-breaker-resume-extend`
- Owner: developer
- Created: 2026-07-14
- Last Updated: 2026-07-14
- Status: Done
- Estimate: 1 day

## Requirements Mapping
- Requirement IDs: FR-2d

## Description
Wire `apply_breaker_extension` (T-yX1Oi5) into `cli.py`'s `resume` command ONLY (not `run` —
extension only makes sense for a run that already exists):
- `--extend-breaker <id>`: the `CircuitBreakerSpec.id` to extend; must exist in
  `wf.circuit_breakers` or exit 1 with a clear error.
- Exactly one of `--extend-by-seconds <float>` / `--extend-by-same` (flag, no arg). Validate
  mutual exclusivity/requiredness with `typer.echo(..., err=True); raise typer.Exit(1)` (matching
  the existing error style already in `resume`, e.g. the `--effort` validation block).
- Apply the extension via `apply_breaker_extension` on the loaded `RunState` BEFORE calling
  `orch.run()` — confirmed by reading the current `resume` body: `existing = rs_store.load(...)`
  then `existing = rs_store.prepare_resume(existing, wf)` happens first (lines ~698-703), so the
  extension applies to `existing` after `prepare_resume` and before `orch.run(..., run_state=
  existing)` (line ~735). Persistence: `orch.run()`'s own save path covers it since the mutation
  is applied to the same `state` object passed in — no extra `rs_store.save()` call needed
  (confirmed against `Orchestrator.run`'s first action being `self._runstate.save(state)`,
  engine.py ~line 135).
- `typer.echo` a confirmation line showing old -> new effective threshold.

## Acceptance Criteria
1. `--extend-breaker <unknown-id>` exits 1 with a clear error naming the id.
2. Both `--extend-by-seconds` and `--extend-by-same` given together -> exit 1, clear error.
3. Neither given (but `--extend-breaker` given) -> exit 1, clear error.
4. `--extend-breaker` omitted -> resume behaves exactly as before this task (no regression).
5. CliRunner end-to-end test: trip a `run_wall_clock_seconds` (or `run_active_seconds`) breaker
   (manufactured `state.json`, matching `tests/test_resume_replay.py`'s
   `TestInjectedTaskCountBreakerResume` pattern of directly constructing `RunState` + `rs_store
   .save`), `ao resume --extend-breaker <id> --extend-by-seconds N` (and a second test for
   `--extend-by-same`), confirm the run continues past the point it previously stopped, confirm
   the printed confirmation line, and confirm the breaker CAN trip again later once the extended
   threshold is also exceeded (e.g. via a persisted `breaker_overrides` value that a subsequent
   resume's real elapsed time exceeds).

## Risks
- Risk: CLI's `resume` command has no way to inject a fake/stepping clock (unlike engine-API
  tests), so CLI-level breaker-trip determinism must rely on real wall-clock deltas (hours) or
  directly-manufactured `RunState`/`tripped_breakers`/`breaker_overrides` fixtures rather than
  driving the trip live through two full `ao run`/`ao resume` invocations. Mitigation: construct
  the "already tripped" and "still exceeds override" states directly (matching existing
  `test_resume_replay.py` conventions), consistent with how `T-t4m8x1`'s own CLI-adjacent tests
  already manufacture `RunState` objects directly rather than always driving a live trip.

## Dependencies
- Depends on `T-yX1Oi5-breaker-extend-core` (`apply_breaker_extension` must exist first).

## Pseudocode / Algorithm
```text
# cli.py resume(), new params:
extend_breaker: str | None = typer.Option(None, "--extend-breaker", ...)
extend_by_seconds: float | None = typer.Option(None, "--extend-by-seconds", ...)
extend_by_same: bool = typer.Option(False, "--extend-by-same", ...)

# after existing = rs_store.prepare_resume(existing, wf):
if extend_breaker is not None:
    spec = next((b for b in wf.circuit_breakers if b.id == extend_breaker), None)
    if spec is None:
        typer.echo(f"ERROR: no circuit breaker with id {extend_breaker!r}", err=True)
        raise typer.Exit(1)
    try:
        old_effective = existing.breaker_overrides.get(spec.id, spec.threshold)
        new_threshold = apply_breaker_extension(
            existing, spec,
            extend_by_seconds=extend_by_seconds,
            extend_by_same=extend_by_same,
        )
    except (SpecValidationError, ValueError) as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Extended breaker {spec.id!r}: {old_effective} -> {new_threshold}")
```

## Schemas / Interface Notes
- Interface: CLI flags only, `resume` command in `cli.py`.
- Spec/data schema: none new (consumes existing `CircuitBreakerSpec.id`/`.threshold`).
- Triggers/events: none new at the CLI layer (the `breaker.extend` log event is emitted by
  `apply_breaker_extension` itself, T-yX1Oi5).
- Artifacts: mutates the loaded `RunState`, persisted via the existing `orch.run()`/
  `RunStateStore.save` path.

## Handoff Boundary
- Upstream: `T-yX1Oi5-breaker-extend-core` (must land first or be implemented together).
- Downstream: `T-gzG0EI-docs-and-verification` documents this flag in the LLD addendum.

## Artifacts
- Docs/comments: `ad/tickets/E-3JTmVu-breaker-resume-extend/T-nVWE1W-resume-extend-breaker-cli/`
- Large outputs: none.
