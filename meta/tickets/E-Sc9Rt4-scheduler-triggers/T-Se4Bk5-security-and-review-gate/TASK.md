# TASK: T-Se4Bk5-security-and-review-gate

## Metadata
- Task ID: `T-Se4Bk5-security-and-review-gate`
- Epic ID: `E-Sc9Rt4-scheduler-triggers`
- Owner: dev-security agent (audit) + reviewer agent (architecture) — run in parallel
- Created: 2026-09-06
- Last Updated: 2026-09-06
- Status: Draft
- Estimate: 2 days

## Requirements Mapping
- Requirement IDs: NFR-3, NFR-6 (see `../EPIC.md`)

## Description
The late gate. Two independent passes over everything the epic landed, run in parallel and
reported separately:

- **`dev-security`** audits the two genuinely new attack surfaces: `.ao/schedules.yaml` as
  untrusted workspace content that can now cause spend on a clock, and the webhook listener as
  the project's first authenticated endpoint.
- **`reviewer`** audits architecture quality: SOLID/KISS/DRY, the pluggable boundaries, no magic
  literals, correct error handling and logging, testability, and retry/resume/concurrency safety.

Findings are classified the way ADR-0012's early-gate section classified them — **blocking**,
**should-fix**, or **documented bounded limitation** — and every one is either fixed here (if it
is a small, local fix) or handed back to its owning task with the exact proposed change. Do not
silently absorb a blocking finding into a "carried limitation".

Files you own: `meta/tickets/E-Sc9Rt4-scheduler-triggers/T-Se4Bk5-security-and-review-gate/`
(the reports), plus small, local fixes to modules this epic created — each recorded in
`STATUS.md` with the module and the finding it resolves. Do **not** restructure another task's
module here; hand a structural finding back.

## Acceptance Criteria

### Security audit (`dev-security`)
1. **Untrusted-file audit.** Confirm by reading the code, not the docs, that a hostile
   `.ao/schedules.yaml` cannot: inject argv beyond `ALLOWED_OPTIONS` ∪ `ALLOWED_BOOL_OPTIONS` ∪
   `{run_id}`; set `reposets`/`agents`/`env`/`command`; escape the workspace root via `workflow`,
   `prompt_file`, `until.*`, `watch.paths`, `webhook.secret_file`, or a symlink; or cause
   unbounded work (`MIN_INTERVAL_SECONDS`, `max_files`, scan budget, body cap, rate limiter, tick
   budget). Report the exact grep/read evidence for each, not a checklist tick.
2. **Argv-surface diff.** Produce `git diff` evidence that no new entry was added to
   `ALLOWED_OPTIONS` other than `run_id`, and that no module constructs a subprocess argv outside
   `ui/processes.py`. `grep -rn "Popen\|subprocess" src/agent_orchestrator/schedules
   src/agent_orchestrator/service/schedule_*` must be empty.
3. **Webhook audit**: `hmac.compare_digest` is used (no `==` on any signature value); the HMAC
   covers the **raw** body; unknown-schedule and bad-signature responses are byte-identical;
   `SecurityMiddleware` is **mounted** on the app (verified by a real 421, not by an import);
   the nonce cache and rate limiter are bounded; a group/world-readable `secret_file` is refused.
4. **Secret-leak sweep**: capture all log output and all HTTP response bodies across every
   webhook rejection path and confirm none contains the secret, the expected signature, or the
   received signature. Report the method used, not just the conclusion.
5. **Path-traversal fuzz**: a short parametrized sweep (`..`, absolute, `~`, symlink-out,
   URL-encoded, NUL byte, very long) over every path-bearing schedule field, asserting a named
   rejection and no filesystem access outside the root.
6. **Run-id guard**: `ao run --run-id` cannot create a directory outside the runs root and cannot
   reuse an existing run's directory (`T-Ri7Dz2` AC2/AC3 re-verified independently).
7. **Threat-model delta**: state in one paragraph what an attacker can do after this epic that
   they could not before, and what the trust boundary is. Confirm — or refute — the epic's claim
   that `ao service add` remains the only trust boundary and that no exposure class is new apart
   from the opt-in webhook.

### Architecture review (`reviewer`)
8. Boundaries: the engine core (`engine.py`, `executors/`, `dag.py`, `spec.py`) is untouched;
   `hub.py` is untouched; `models.py` / `specs/workflow.schema.json` diffs are confined to
   `Trigger` / `$defs.trigger`; top-level `cli.py` grew only the `add_typer` + `--run-id` +
   `ao validate` hook lines. Report each as a `git diff --stat`.
9. DRY: no second cron implementation, no second launch path, no second workspace slugger, no
   second root guard, no second atomic-write idiom in new code. Flag `service/statefile.py`'s
   three existing un-refactored call sites as a follow-up (deliberately deferred by `T-Sd1Kq7`
   AC8) — and confirm it is still only a follow-up, not a divergence.
10. No magic literals: every threshold, window, cap and interval is a named module constant with
    a comment explaining the value. Spot-check at least the ten in HLD §6 and §8.
11. Error handling and logging: every swallowed exception logs; every skip states a reason from
    the named `FireReason` set; no bare `except: pass`; the outer engine backstop logs a
    traceback.
12. Concurrency and resume safety: the at-most-once claim survives a read of the code (the fsync,
    the write-before-launch ordering, the "any status suppresses" rule); the overlap/concurrency
    key-burning asymmetry is both correct and commented; `state.json`'s single-writer assumption
    is documented and still true.
13. Testability: every collaborator is constructor-injected; no module reads `datetime.now()`
    inside evaluation; no test monkeypatches a private.

### Quality gate (NFR-6)
14. Independently re-run and report: `uv run pytest -q` (count + delta vs. the epic baseline),
    per-module coverage against the ≥80 % floor (≥90 % for `service/webhook.py`),
    `uv run ruff check .` and `ruff format --check .`, `uv run mypy src` as a whole-tree count
    against the 4-error `_version.py` baseline, and `make ui-typecheck` / `make ui-test`.
    Re-run them — do not quote `T-Te3Qw8`'s numbers.
15. Every finding is written up as `**<bold restatement of the defect>.** <explanation.> <Fixed
    here / handed to T-xxxx / documented bounded limitation.>` and classified blocking |
    should-fix | limitation. Blocking findings are resolved before this task closes.
16. `EPIC.md`, this epic's `STATUS.md`, and every affected task's `TASK.md`/`STATUS.md` are
    updated so wording and counts match across all of them (tickets README rule 9). An
    ADR-0014 "Late-gate corrections (date)" section is added if any finding changed a documented
    decision.

## Risks
- A parallel `reviewer` reading a ticket file while `dev-security` is still writing it produces
  race-condition false positives — this exact failure happened in E-GIytcL. Re-read a file's
  final content before reporting a finding about it, and sweep the machine independently before
  claiming something did not happen.
- The temptation at a late gate is to reclassify a blocking finding as a limitation to close on
  time. AC15 forbids it; escalate instead.
- Two agents editing the same `STATUS.md` will conflict. Write two separate report files
  (`SECURITY.md`, `REVIEW.md`) in this task folder and have `STATUS.md` summarize both.

## Dependencies
- `T-Te3Qw8` must have landed (its reported gaps are this task's starting point). Every
  implementation task must be Done.

## Pseudocode / Algorithm
```text
# The sweeps, verbatim -- evidence, not assertions
grep -rn "Popen\|subprocess"      src/agent_orchestrator/schedules src/agent_orchestrator/service/schedule_*
grep -rn "compare_digest"          src/agent_orchestrator/service/webhook.py     # must be present
grep -rnE "signature\s*==|==\s*signature|sig\s*==" src/agent_orchestrator/service/webhook.py  # must be EMPTY
grep -rn "add_middleware"          src/agent_orchestrator/service/webhook.py     # must be present
grep -rn "datetime.now\|time.time" src/agent_orchestrator/service/schedule_engine.py  # must be EMPTY
grep -rn "except.*:\s*pass"        src/agent_orchestrator/schedules src/agent_orchestrator/service
git diff --stat <epic-base>..HEAD -- src/agent_orchestrator/models.py specs/workflow.schema.json \
                                     src/agent_orchestrator/cli.py src/agent_orchestrator/service/hub.py
```

## Schemas / Interface Notes
- Interface / API: none added. This task reviews interfaces; it does not define them.
- Spec / data schema: verifies `specs/schedules.schema.json` matches the pydantic model and that
  `specs/workflow.schema.json`'s diff is confined to `$defs.trigger`.
- Triggers / events: verifies the event vocabulary is complete and leak-free.
- Artifacts: `SECURITY.md` and `REVIEW.md` in this task folder; large logs under
  `output/E-Sc9Rt4-scheduler-triggers/`.

## Handoff Boundary
- Upstream: every implementation task; `T-Te3Qw8`'s report; HLD §13/§18; ADR-0014 D8/D9.
- Downstream: `T-Dc6Zr2` (documents any accepted limitation the gate confirmed, and any ADR-0014
  late-gate correction).

## Artifacts
- Docs/comments: `meta/tickets/E-Sc9Rt4-scheduler-triggers/T-Se4Bk5-security-and-review-gate/`
  (`SECURITY.md`, `REVIEW.md`, `STATUS.md`)
- Large outputs: `output/E-Sc9Rt4-scheduler-triggers/`
