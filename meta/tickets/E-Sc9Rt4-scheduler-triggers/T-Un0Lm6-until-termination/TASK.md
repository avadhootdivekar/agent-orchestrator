# TASK: T-Un0Lm6-until-termination

## Metadata
- Task ID: `T-Un0Lm6-until-termination`
- Epic ID: `E-Sc9Rt4-scheduler-triggers`
- Owner: developer agent
- Created: 2026-09-06
- Last Updated: 2026-09-06
- Status: Draft
- Estimate: 2 days

## Requirements Mapping
- Requirement IDs: FR-13 (see `../EPIC.md`)

## Description
Loop-style triggers: "keep running the epic-runner nightly **until** its STATUS says done."
An `until` block on a schedule binding turns a repeating schedule into a bounded, terminating
one — the inter-run sibling of the engine's existing intra-run `LoopSpec`.

The relationship to `LoopSpec` matters and must be preserved: `LoopSpec` clones body tasks
*inside one run* and is evaluated by the engine (`docs-md/logging-dynamic-workflows-hld.md`);
`until` is evaluated *between whole runs* by the scheduler. They share a gate-file *shape* but
deliberately not a model — merging them would put scheduler concerns inside the orchestration
engine, which is the boundary this whole epic maintains. A schedule may of course point at a
workflow that itself uses `LoopSpec`; they compose without knowing about each other.

Read HLD §6.8, and `docs-md/logging-dynamic-workflows-hld.md` §3.2/§4.5 for the `LoopSpec`
semantics you are mirroring in shape but not in code.

Files you own (create/edit freely):
- `src/agent_orchestrator/service/schedule_engine.py` (edit: **only** the `until_satisfied`
  helper and its single call site at the documented point in `evaluate` — coordinate with
  `T-Ev3Qm5`'s owner if concurrent)
- `tests/service/test_until.py` (new)
- additive: `--until-artifact`, `--until-gate`, `--until-field`, `--until-equals`, `--max-runs`
  on `ao schedule add`; `enable` resetting a `completed` schedule

Do NOT touch: `engine.py`, `models.py` (`LoopSpec` is not changed by this task), `spec.py`,
`schedules/models.py` (`UntilSpec` already landed in `T-Sd1Kq7`), `service/fire_store.py`.

## Acceptance Criteria
1. `until_satisfied(workspace_root, binding, state) -> bool` implements HLD §6.8 exactly, in
   this order: `max_runs` first (so termination is guaranteed regardless of the other
   conditions), then `artifact_exists`, then the `gate_file`/`gate_field`/`gate_equals` triple.
2. It is called at the documented point in `evaluate` — **after** the status/enabled check and
   **before** the queued-drain and `next_fire` computation — so a satisfied schedule stops
   without computing a next fire and without draining a queued one.
3. Satisfaction sets `ScheduleState.status = "completed"`, emits `schedule.until_satisfied` with
   `runs_count`, and leaves the binding in `.ao/schedules.yaml` untouched. It does **not** delete
   the binding: disabling is reversible and auditable, deletion is neither (HLD §20 open question
   2 records the trade-off).
4. `ao schedule enable <id>` on a `completed` schedule clears the status **and resets
   `runs_count` to 0**, which is the documented way to restart an `until` loop. Without the
   reset, `max_runs` would immediately re-satisfy and the enable would appear to do nothing.
5. Every path in `UntilSpec` resolves through `LocalFsArtifactStore`'s workspace-root guard —
   `T-Sd1Kq7` rejects absolute and `..` paths at load, and this module must not bypass that at
   read time either.
6. **A gate file that is missing, unreadable, malformed JSON, or whose field is absent or of the
   wrong type never terminates the loop.** It returns `False` and emits
   `schedule.until_unreadable` at WARNING. Rationale: a transient write or a crashed run must not
   silently end a multi-day schedule. (Note the deliberate contrast with `LoopSpec`, where a bad
   gate **fails the run** — there, an unreadable gate means the run is broken; here, it means the
   next fire simply proceeds.)
7. `gate_equals` compares with `==` against a `bool | str | int` and defaults to `True`. A
   float or object value in the JSON compares unequal rather than raising.
8. `ao schedule add` requires an `until` to be bounded: when `--until-artifact` or `--until-gate`
   is given without `--max-runs`, the CLI supplies `DEFAULT_UNTIL_MAX_RUNS (100)` and says so on
   stdout. No `until` schedule may loop forever because a gate file never appears.
9. `ao schedule list` shows a `completed` schedule with its status and `runs_count`, so a
   finished loop is visible rather than looking like a silently dead schedule.

### Tests
10. One test per condition: `max_runs` reached exactly; `artifact_exists` present/absent;
    `gate_file` with the field `true` / `false` / absent / wrong type / malformed JSON /
    unreadable / missing file. Each asserts both the return value and, where applicable, the
    emitted event.
11. Ordering: a schedule with `max_runs` reached **and** a gate saying "continue" still
    terminates (AC1's ordering); a satisfied schedule does not compute a next fire and does not
    drain a queued fire (AC2's placement).
12. Lifecycle: satisfy → `completed` → `ao schedule enable` → `runs_count == 0` and the next tick
    fires again. This round-trip is the AC4 payoff and must be a single e2e test through
    `CliRunner` + `daemon --once`.
13. Composition: a workflow that itself declares a `LoopSpec` runs unchanged under an `until`
    schedule — a characterization test asserting the engine's loop behavior is untouched by this
    task.
14. `uv run pytest -q` green with the delta reported; ruff + format clean; mypy whole-tree count
    reported; coverage ≥80 % on the new code.

## Risks
- AC6's "never terminate on a bad read" is the opposite of `LoopSpec`'s "bad gate fails the run".
  A developer who read the loop code first will get this backwards; the docstring must state the
  contrast and the reason explicitly.
- AC4's `runs_count` reset is easy to miss and produces a baffling bug (enable appears to do
  nothing). AC12 exists to catch it.
- This task edits a file `T-Ev3Qm5` owns. Keep the diff to the helper plus its one call site and
  confirm with `git diff` at handoff.
- `max_runs` counts **fires**, not successes. A schedule whose runs all fail still terminates at
  `max_runs` — correct (it bounds spend) but worth documenting, since a user may expect
  "100 successful runs".

## Dependencies
- `T-Sd1Kq7` (`UntilSpec`), `T-Ev3Qm5` (`evaluate`'s call-site contract and `ScheduleState`),
  `T-Cl6Jn9` (`add` / `enable`).
- Reads (read-only): `artifacts.py` (root guard), `docs-md/logging-dynamic-workflows-hld.md`
  (the `LoopSpec` contrast).

## Pseudocode / Algorithm
```text
FUNCTION until_satisfied(root, b, st) -> bool:
  u = b.until
  IF u IS NULL: RETURN False
  IF u.max_runs IS NOT NULL AND st.runs_count >= u.max_runs:
      RETURN True                                   # FIRST: guarantees termination
  IF u.artifact_exists AND safe_join(root, u.artifact_exists).exists():
      RETURN True
  IF u.gate_file:
      p = safe_join(root, u.gate_file)
      IF NOT p.is_file(): RETURN False
      TRY:
          data = json.loads(p.read_text(encoding="utf-8"))
      EXCEPT (OSError, UnicodeDecodeError, json.JSONDecodeError) AS e:
          events.emit("schedule.until_unreadable", "WARNING", schedule_id=b.id, error=str(e))
          RETURN False                              # a bad read NEVER ends the loop (cf. LoopSpec)
      IF NOT isinstance(data, dict): RETURN False
      RETURN data.get(u.gate_field) == u.gate_equals
  RETURN False
```

## Schemas / Interface Notes
- Interface / API: `service.schedule_engine.until_satisfied`; `DEFAULT_UNTIL_MAX_RUNS`.
- Spec / data schema: consumes `UntilSpec` (`max_runs`, `artifact_exists`, `gate_file`,
  `gate_field`, `gate_equals`); sets `ScheduleState.status = "completed"`.
- Triggers / events: emits `schedule.until_satisfied`, `schedule.until_unreadable`. Turns any
  `cron` / `interval` / event kind into a terminating loop.
- Artifacts: reads the workspace-relative gate/artifact paths declared by the binding; writes
  nothing into the workspace.

## Handoff Boundary
- Upstream: `T-Sd1Kq7`, `T-Ev3Qm5`, `T-Cl6Jn9`; HLD §6.8; `docs-md/logging-dynamic-workflows-hld.md`.
- Downstream: `T-Ap1Xs3` (renders `completed` + `runs_count` / `max_runs` progress),
  `T-Te3Qw8` (e2e tier), `T-Dc6Zr2` (documents the `LoopSpec` vs `until` distinction).

## Artifacts
- Docs/comments: `meta/tickets/E-Sc9Rt4-scheduler-triggers/T-Un0Lm6-until-termination/`
- Large outputs: N/A
