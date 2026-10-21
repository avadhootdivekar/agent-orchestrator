# Awaiting-input state, InputRequest and blocking/non-blocking modes — design

**Status:** DESIGN; slices S1–S3 implemented (see §15 As-built), S4 (dashboard) and S5 pending. Author: architect (overseer unit `w02-01-input-state-design`), 2026-10-21.
**Serves:** run-level ask A1 (A1.2–A1.5). Research basis: the wave-1 report `w01-01-input-behavior-research.md`.
Code line numbers below are approximate; re-verify before editing.

## 1. Requirements

1. A distinct task status for "blocked / waiting on a human answer" that is **not** `failed` and is visible in the CLI and the dashboard (A1.2).
2. A structured, front-end-neutral way for a task to declare what it needs (prompt, fields, choices); each front end renders it. No task-supplied HTML (A1.3).
3. An answer path from the CLI (`ao answer`) and from the dashboard (endpoint + form) that share one validator/writer (A1.3).
4. Blocking and non-blocking invocation modes; non-blocking proceeds on explicit, recorded default assumptions (A1.4).
5. Existing flows (overseer `HOLD`, `needs_input` breadcrumbs, `ao resume`) keep working unchanged (A1.5).

Non-goals: the full signed approval-gate system of `human-approval-gates-hld.md`; migrating overseer `HOLD`; interactive stdin to a running agent process.

## 2. Current behaviour (summary of the research)

- `TaskStatus` (`models.py:~1005`) has no waiting state; `RunState.status` is `running|succeeded|failed|cancelled`.
- Agents run with `stdin=DEVNULL` (`executors/claude_cli.py:~637`), so an agent cannot ask a question mid-run.
- The only existing "wait" is the overseer `HOLD`: a pre-hook that exits non-zero → task `failed` → run `failed`; the human writes `control/hold-answer.md` and runs `ao resume`, which resets every non-succeeded task to `pending` (`runstate.py:442 prepare_resume`).
- So the user's belief is **right for the one place that exists today** and there is no mechanism at all for other workflows.

## 3. Scope boundaries and decisions (ADRs)

**ADR-1 — Status lives on the task; the run stays `running`.** Add `awaiting_input` to `TaskStatus`. Do **not** add a run status. A run is "waiting for input" when it has ≥1 `awaiting_input` task and nothing running or dispatchable; this is a *derived* flag `waiting_for_input: true` in `status.json`/summaries.
*Why:* a new `RunState.status` value breaks every consumer (summary counters, service supervisor liveness, dashboard filters, `ui/src/types.ts`). *Consequence:* consumers must tolerate a `running` run with no live process (see §6.3).

**ADR-2 — Structured JSON schema, no trusted HTML.** Fields are `text|choice|bool` (plus `multichoice` reserved, not in v1). The CLI renders prompts; the web renders a form from the same JSON. Task-authored text is escaped on render. *Alternative rejected:* HTML snippets (XSS surface; two renderers cannot share them).

**ADR-3 — Two producers, one consumer.** (a) a spec-level `input:` block (engine opens the request before dispatch; no executor runs); (b) a task-written artifact `input-request.json` read at settle. Both produce the same `InputRequest` record on `RunState.input_requests`. *Why:* (b) is deterministic with no free-text parsing, (a) is for gates known at authoring time. v1 slice 1 ships (a)+(b) in the engine; templates adopt them later.

**ADR-4 — Exit-resumable by default.** When all remaining work is blocked on `awaiting_input` tasks, the engine process settles the run state and exits cleanly (code 0, run `running`, summary says waiting). `ao answer` records the answer; `ao resume` (or the dashboard "Answer & continue", which does both) continues. *Alternative:* hold the process and poll — rejected as default (idle process, supervisor complexity); allowed later as `--input-wait-seconds N` (out of v1).

**ADR-5 — Naming reconciles with E-Ag7Pw3.** `awaiting_input` ≠ `awaiting_approval`; the approvals epic stays deferred and later reuses the same non-failed, non-halting semantic. Shared helper names are deliberately generic (`is_waiting_status`).

**ADR-6 — Opt-in, schema-optional.** Every new spec/state field has a default; old `state.json` and specs load unchanged.

## 4. Data model

### 4.1 Status

```
TaskStatus += "awaiting_input"
RunState.input_mode: Literal["blocking","nonblocking"] = "blocking"
RunState.input_requests: dict[str, InputRequest] = {}      # key = request_id
```
`awaiting_input` is **not** a terminal status, not counted as `failed`, does not consume a retry attempt, is never cached by the result cache, does not hold an executor/`max_parallel` slot, and does not trip breakers/budget windows.

### 4.2 InputRequest

```jsonc
{
  "schema": "ao.input.request/v1",
  "request_id": "q-<task_id>-<n>",     // [a-z0-9][a-z0-9._-]{0,63}; engine-generated, never trusted from a task
  "task_id": "design-step",
  "state": "open",                      // open | answered | assumed | cancelled
  "prompt": "Which database should the migration target?",   // <=2000 chars, plain text
  "fields": [
    {"name": "db",   "type": "choice", "label": "Database", "choices": ["postgres","sqlite"], "default": "sqlite", "required": true},
    {"name": "note", "type": "text",   "label": "Notes",    "max_chars": 500,                  "required": false},
    {"name": "ok",   "type": "bool",   "label": "Proceed?", "default": true}
  ],
  "created_at": "2026-10-21T10:00:00Z",
  "answered_at": null,
  "answer": null                        // {"values": {...}, "assumed": false, "source": "cli|web|default", "by": "<principal>"}
}
```
Limits (enforced by one validator): ≤20 fields, names `[a-z][a-z0-9_]{0,31}` and unique, ≤20 choices of ≤200 chars, `text` max 4000 chars, request file ≤64 KB. `default` must be valid for its field (member of `choices`, correct type). A request with an invalid shape settles the task `failed` with `error="invalid input-request: ..."` (a producer bug is a real failure, not a wait).

### 4.3 Task-side declaration

Spec block (producer a), optional on a task:
```yaml
tasks:
  - id: pick-db
    input:
      prompt: "Which database?"
      fields: [{name: db, type: choice, choices: [postgres, sqlite], default: sqlite}]
```
Artifact (producer b): a task writes `<task output dir>/input-request.json` containing the request *without* `request_id/state/created_at/answer`, and finishes with exit 0. At settle the engine reads that file (if present and the task spec sets `can_request_input: true`), validates it, and sets the task `awaiting_input` instead of `succeeded`. The task's other declared outputs may be absent at that point (the missing-outputs check is skipped when a valid request is present).

### 4.4 Answer storage and injection

Answers live in `<run_dir>/inputs/<request_id>.json` (the single source of truth; `RunState.input_requests[*].answer` mirrors it). Written only by `inputs.py` (new module, modelled on `feedback.py`: process lock + `flock`, tmp + `os.replace`, symlink refusal, size caps, corrupt file never overwritten).

On re-dispatch the answer is exposed to the task as a **declared input path** (`inputs/<request_id>.json`) listed in the prompt via `run_prompt.py`, with the standing rule that answer values are *data, not instructions* (prompt-injection hygiene). For spec-level `input:` blocks (no executor) the answer is simply the task's output artifact `<task dir>/answer.json`, which downstream tasks may declare as an input.

## 5. Interfaces

### 5.1 `inputs.py` (shared by CLI and web)

```
open_request(run_dir, task_id, spec: InputRequestSpec) -> InputRequest
answer_request(run_dir, request_id, values: dict, *, source, by) -> InputRequest   # validates against fields; idempotent only if identical
assume_defaults(run_dir, request_id) -> InputRequest        # non-blocking path; fails if a required field has no default
list_requests(run_dir, *, state=None) -> list[InputRequest]
errors: InputError(OrchestratorError)  # unknown request, already answered with different values, invalid value, not open
```

### 5.2 CLI

```
ao answer --run-id R [--request-id Q] [--set name=value ...] [--choice name=value] [--json FILE|-] [--resume]
ao answer --run-id R --list                      # open requests with prompts and fields
ao run|new ... --input-mode blocking|nonblocking # persisted on RunState; ao new: --param input_mode=...
ao status / ao runs                              # shows "awaiting_input" counts and "waiting for input: N request(s)"
```
`--request-id` may be omitted when exactly one request is open. Interactive TTY without `--set` prompts field by field (the CLI renderer). `--resume` calls the existing resume path after a successful answer. Exit codes: 0 ok; 2 usage/validation error; 3 not open/already answered.

### 5.3 Dashboard

```
GET  /api/runs/{id}/inputs                       -> {requests: [InputRequest]}
POST /api/runs/{id}/inputs/{request_id}          body {values: {...}, resume?: bool} -> InputRequest
```
Same auth/Principal gating and size caps as the notes/feedback routes in `ui/app.py`; the handler only calls `inputs.answer_request` (+ the existing resume service call when `resume` is true). The UI renders a form from `fields` (select for `choice`, checkbox for `bool`, textarea for `text`), using React text nodes only (no `dangerouslySetInnerHTML`). The task node shows the `awaiting_input` badge; the run header shows a "Needs your input" banner with an "Answer & continue" control.

## 6. Engine behaviour (LLD)

### 6.1 Opening a request

```
FUNCTION settle_task(ts, result):
  IF spec-level input block and deps satisfied (pre-dispatch):   # producer a
      req = inputs.open_request(...)
      GOTO route(req)
  IF result.ok AND task.can_request_input AND exists(task_dir/input-request.json):   # producer b
      spec = validate(read(...))            # invalid -> ts.status=failed, error recorded
      req  = inputs.open_request(...)
      GOTO route(req)
  ... existing settle ...

route(req):
  IF run.input_mode == "nonblocking":
      inputs.assume_defaults(req)           # raises -> task failed, clear error ("no default for required field X in non-blocking mode")
      log + summary entry "ASSUMED: <task>.<field>=<value> (default)"
      ts.status = "pending"                 # re-dispatch with the assumed answer (producer b) or finish (producer a)
  ELSE:
      ts.status = "awaiting_input"; persist request; free slot; DO NOT halt
```

New settle signal `await_input` (alongside `settled|requeue|halt|reshaped`): the task is parked, independent branches keep dispatching, aggregate `failed` ignores it, final run-status computation (`engine.py:~997`) treats "only awaiting tasks remain" as `running` + derived `waiting_for_input`.

### 6.2 Detecting an answer

Each scheduling pass (and at resume), for every `awaiting_input` task: if `inputs/<request_id>.json` exists with `answer`, set `request.state=answered`, task → `pending` (re-dispatch, attempts unchanged). Polling the filesystem on the existing loop tick is enough; no new watcher.

### 6.3 Exit and liveness

If no task is running/dispatchable and ≥1 task is `awaiting_input`: write state, emit event `run.waiting_for_input`, exit 0 with a one-line hint (`ao answer ...`). Supervisor/`live` checks must treat `status=running ∧ waiting_for_input ∧ no process` as **idle, not stuck**. The dashboard service resumes on answer.

### 6.4 `prepare_resume`

Special case before the generic reset: an `awaiting_input` task with an **unanswered** request stays `awaiting_input` (request preserved); with an **answered** request it becomes `pending`; a request whose task no longer exists is marked `cancelled`. The run exits waiting again if nothing else can proceed. This prevents the "reset everything → re-ask answered questions" bug.

### 6.5 Cancel / timeouts

`ao cancel` marks open requests `cancelled`. Optional spec field `input_timeout_seconds` (v1: informational/recorded only; a later slice may auto-assume defaults or fail on expiry).

## 7. Blocking vs non-blocking

| | blocking (default) | non-blocking (opt-in) |
|---|---|---|
| Set by | default; `--input-mode blocking` | `--input-mode nonblocking`, `--param input_mode=nonblocking` |
| On request | task `awaiting_input`, run exits waiting | engine auto-answers each field with `default`, marks `answer.assumed=true, source="default"` |
| Recorded | request + answer file | same, plus run log line and an "Assumptions" section in the run summary; `status.json.assumptions[]` |
| Required field, no default | n/a (human supplies) | task `failed`: "no default for required field X in non-blocking mode" (never hangs) |
| Resume | `ao answer` then `ao resume` | no wait; normal resume |

Mode is run-level in v1 (open question from the charter resolved: per-task override is deferred — one mode keeps behaviour predictable). Mode can be changed on resume (`ao resume --input-mode nonblocking`), which converts currently open requests via `assume_defaults`.

## 8. Compatibility

- **Overseer HOLD:** untouched. `hold-request.json`/`hold-answer.md` and the failing pre-hook keep their semantics; `needs_input` breadcrumbs and `blocked_units` signals remain advisory and unread by the engine. Existing overseer tool tests must pass unmodified. A later change may let `HOLD` emit an `InputRequest` (not in A1).
- **Old state/specs:** `input_requests` and `input_mode` default; specs without `input:` / `can_request_input` behave identically. `status.json` emits `waiting_for_input`, `counts.awaiting_input` and `assumptions` **only when non-empty** (two existing tests pin the exact `status.json` key set — same precaution as approvals T-9).
- **Status mirrors to update with the new literal:** `ui/src/types.ts`, `ui/src/format.ts`, `ui/src/graph/model.ts`, `ui/src/graph/TaskNode.tsx`, `runstate.py` summary counters, any JSON schema export of the spec (`input:` block).
- **Result cache:** `awaiting_input` results are never stored.

## 9. Security

Answers and requests are untrusted: request/field names validated by regex (no path traversal; file names derive only from engine-generated `request_id`); size caps on every field and file; symlink refusal; dashboard answer route behind the existing auth/Principal; values rendered as text only and injected into prompts as data. A task cannot answer its own request (answers come only through `inputs.py` entry points called from CLI/HTTP, never from task output paths).

## 10. Test plan (A1.5)

Unit (`tests/test_inputs.py`, `tests/test_models_input.py`):
- InputRequest validation: limits, duplicate/invalid names, default-not-in-choices, oversize, bad type.
- `answer_request`: valid/invalid values, required missing, idempotent re-answer, conflicting re-answer, unknown/closed request; corrupt file not overwritten; symlink refused; concurrent writers serialized.
- `assume_defaults`: all defaults present; required-no-default raises.
- Old `state.json` (no new fields) loads; `status.json` key set unchanged for runs without requests.

Engine/integration (temp workspaces, fake executor):
- producer (a) and (b) → task `awaiting_input`, run `running`, independent branch still completes, process exits 0 waiting, no retry attempt consumed, not cached.
- Answer detected → task re-dispatched with answer path in prompt → succeeds.
- `prepare_resume`: unanswered stays waiting; answered → pending; orphan request cancelled; succeeded tasks untouched.
- Non-blocking: defaults applied, `assumed=true` recorded in answer file, log and summary; missing default → task `failed` with the specific error.
- Invalid `input-request.json` → task `failed` (not waiting).
- Aggregate failed/run-status computation with only awaiting tasks left.
- Regression: the existing overseer HOLD/answer/resume tests and `needs_input` breadcrumb tests pass unmodified.

CLI e2e (`CliRunner`, outermost boundary): `ao new/run --input-mode`, `ao status` shows awaiting_input, `ao answer --list`, `ao answer --set ... --resume`, error exit codes, non-blocking end-to-end run to success with an assumptions section.

Dashboard (slice 4): `GET/POST` route tests (auth, validation, size cap, resume flag); UI component tests for the form render (choice/bool/text, escaping of hostile prompt strings), badge/banner; `npm` UI tests + bundle build. Gates: `pytest`, `ruff check/format`, `mypy`.

## 11. Implementation slicing (ordered; each slice independently landable)

| # | Slice | Contents | Depends | Est. |
|---|---|---|---|---|
| S1 | Models + `inputs.py` | `awaiting_input` literal, `InputRequest`/field models, `RunState.input_requests/input_mode`, validator/writer, unit tests; status mirrors in `ui/src` types only (no UI behaviour) | — | 2d |
| S2 | Engine core | `await_input` settle signal, spec `input:` block + `can_request_input` artifact producer, answer detection, exit-waiting, `prepare_resume` special-casing, status/summary counters + derived flag, engine tests | S1 | 3d |
| S3 | CLI + non-blocking | `ao answer` (+`--list`, `--resume`, TTY prompting), `--input-mode` on `run/new/resume`, defaults assumption + recorded assumptions, CLI e2e tests, `ao status` rendering | S2 | 3d |
| S4 | Dashboard | `GET/POST` inputs routes, form component, badge/banner, "Answer & continue", UI tests + bundle | S2 (S3 for parity) | 3d |
| S5 | Docs | `docs-md` reconciliation (this doc → as-built, `live-steering.md` "no paused state" note, CLI reference), ticket sync | S3/S4 | 1d |

**Usable-bar cut line:** S1–S3 give the engine state, non-blocking mode and `ao answer` end-to-end via CLI without the dashboard form (charter's fallback). S4 may be cut without breaking anything; the dashboard still *shows* the status badge once S1's type mirrors land.

## 12. Edge cases

- Two requests open on one task (re-ask after an answer): request ids carry a counter; only one open per task at a time in v1 (a second `input-request.json` after an answer opens `…-2`).
- Answer arrives while the run process is still alive: picked up on the next loop tick.
- Answer for a task whose spec changed on resume: request kept only if the task id still exists; otherwise `cancelled`.
- Non-blocking switch mid-wait: handled by `ao resume --input-mode nonblocking`.
- Empty/over-long values, unicode, newlines: validated per field type; stored as JSON strings.
- Crash between answer write and state update: answer file is truth; next pass reconciles.
- Re-answer with different values after the task resumed: rejected (`InputError`, exit 3).

## 13. Risks and open questions

1. Derived waiting flag vs. supervisor liveness (ADR-1) — mitigate with an explicit idle check and a test.
2. `engine.py`/`models.py`/UI bundle are merge-conflict hot spots — keep changes additive.
3. `can_request_input` opt-in means existing agents never get a surprise wait; templates adopt it deliberately.
4. OPEN_QUESTION: should `HOLD` migrate to `InputRequest` later (single mechanism), or stay separate? Deferred, not required by A1.
5. OPEN_QUESTION: auto-expiry of waits (`input_timeout_seconds`) behaviour — deferred past v1.
6. ASSUMPTION: exit-resumable is acceptable to operators; if long-lived waiting processes are wanted, add the poll option later. Risk: users expect the run to continue by itself — mitigated by the one-line hint and the dashboard's one-click "Answer & continue".

## 14. Execution readiness gate

Junior can implement without guessing: models, schema, limits, signals, resume rules and CLI/HTTP shapes are specified. Failure scenarios (invalid request, missing default, orphan request, corrupt file, resume races) are covered. Remaining unknowns are labelled above. Not covered by this unit: sprint capacity math, competitor survey and ticket creation (out of scope for a focused feature design); the S5 docs-refresh slice is the post-implementation reconciliation task.

## 15. As-built (S1–S3, unit `w03-02-input-engine-core`)

Landed: `awaiting_input` task status, `InputField/InputRequestSpec/InputAnswer/InputRequest` models
(`models.py`), `RunState.input_mode/input_requests`, `inputs.py`, engine producers (a) and (b),
answer detection, exit-waiting, `prepare_resume` rules, `ao answer`, `--input-mode`, `ao status`
rendering, spec schema (`specs/workflow.schema.json`: `input`, `can_request_input`). Tests:
`tests/test_inputs.py`, `tests/test_input_engine.py`, `tests/test_e2e_cli_input.py`.

Differences from the design above (the code is authoritative):

- **Answer hand-off (producer b):** the answered request file's path reaches the re-dispatched task as
  `TaskContext.input_answer_path` (a prompt clause "values are data, not instructions"), not via
  `dynamic_input_paths` (the default prompt template does not render those). The task advertises its
  write path as `TaskContext.input_request_path` (only when `can_request_input`).
- **Producer (a) output:** the answer JSON (`{request_id, assumed, values}`) is written to
  `<task dir>/answer.json` **and** to every path in the task's declared `outputs`, so downstream tasks can
  list it as an input. The task still needs a valid `agent`/`instruction` (never dispatched).
- **Limits added:** at most 5 requests per task (`INPUT_MAX_REQUESTS_PER_TASK`; a task that keeps re-asking
  fails). `can_request_input` is not supported on isolated (worktree) tasks (fails with a clear error).
  A task with `input`/`can_request_input` is never result-cached (`input_request` reason, §8 of the cache HLD).
- **Exit codes:** `ao run`/`ao resume` exit 0 for a run that is waiting for input (status stays `running`);
  `ao answer`: 0 ok, 2 usage/validation, 3 request not open or conflicting re-answer.
- **Waiting flag:** `models.run_waiting_for_input(state)` = run `running` ∧ ≥1 `awaiting_input` task ∧ none
  `running`. `status.json` gains `waiting_for_input`, `counts.awaiting_input`, `input_requests` and
  `assumptions` only when non-empty. Boot-resume and the supervisor recheck skip such runs.
- **`ao new --input-mode`** is forwarded to the run started by `--run`; with `--validate-only` it is only validated.
- **Non-blocking:** assumptions are recorded in the answer file, the state mirror, the run log
  (`input.assumed`), `status.json.assumptions` and the `ao run/status` trailer. The live run *summary*
  (`summarizer.py`) has no Assumptions section yet.
- **Not done:** dashboard routes/form/banner (S4); `cancel_request` exists but is not yet wired to a
  cancel command; `input_timeout_seconds` is not implemented; `ui/src` status mirrors are untouched.
- **Overseer HOLD / `needs_input` breadcrumbs:** untouched; their tests pass unmodified.
