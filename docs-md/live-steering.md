# Live steering: queued operator notes

Status: **backend and dashboard UI implemented** (A6). The run page "Live inputs" section has a notes box (list, send, 2000-char cap, errors, and the "reaches later tasks only" limitation).

## What it is

An operator can send free-form guidance to a **running** workflow, with no pending request or
approval gate involved. The note is queued and reaches every task that is **dispatched after it
was submitted** (including a retry of an earlier task). It is guidance, not a command: the
prompt tells the agent to follow it only where it does not conflict with the task's own
instructions.

## What it cannot do (limitation)

- **No mid-task input.** A task that is already running never sees a note. The only executor
  spawns `claude --print` with `stdin=DEVNULL` and builds the prompt once, before launch, so
  there is no channel to a live task. To stop a task, Cancel the run (and Resume it later).
- **No pause.** There is no paused run state; notes do not pause anything.
- **No approval gates.** Those are a separate, not-yet-built design
  ([human-approval-gates-hld.md](human-approval-gates-hld.md)); notes do not depend on them.
- Notes are accepted only while the run is **live** (state `running` and its process alive under
  the dashboard supervisor). Otherwise the endpoint returns 409 instead of silently ignoring the
  note. Runs started from the CLI outside the dashboard therefore do not accept notes.

## Storage

Under the run dir `<ws>/.orchestrator/runs/<run_id>/`:

| File | Role |
|---|---|
| `operator-notes.json` | Source of truth: `{schema_version, notes:[{id, ts, source, text}]}`. |
| `operator-notes.md` | Derived view handed to agents by path. |

One writer: `feedback.add_operator_note` (same discipline as `add_feedback`: thread lock plus
`flock`, atomic tmp+replace, symlinks refused, a corrupt index is never overwritten).
Text is stripped, 1..2000 chars, no NUL; at most 100 notes per run. Over-limit input is
**rejected, never trimmed**. `source` (`cli`/`dashboard`) is set by the server, never the client.

## Delivery

`Orchestrator._operator_notes_path` does an existence-only `lstat` of `operator-notes.md` at each
dispatch (and each attempt). If it is a regular file, `TaskContext.operator_notes_path` is set and
`executors.prompt.build_prompt` appends one clause naming the path. The engine and prompt handle
the **path only, never the content** (NFR-1). With no notes file the prompt is byte-identical to
before. The file lives in the run dir, outside any worktree, so isolated tasks use the same
absolute path.

## Result cache

The cache key covers the notes file's **content** (`operator_notes` entry, like a general
instruction), and the key prompt uses a fixed stand-in path so equal notes give equal keys across
runs. Consequences:

- A task cached before a note was sent is **not served** after it (different key).
- A note that arrives while a cacheable task runs makes the settle-time key differ from the
  lookup-time key, so that result is **not stored** (`key_changed_during_run`).
- A run with no notes has exactly the pre-feature key (the field is only added when present).

## API

Both routes sit behind the dashboard's deny-by-default `AuthMiddleware` (no policy entry means
authenticated); there is no unauthenticated write path.

- `GET /api/runs/{id}/notes` -> `{run_id, accepting, max_chars, max_notes, notes:[...]}`
- `POST /api/runs/{id}/notes` body `{"text": "..."}` (`extra="forbid"`) -> 201
  `{note, notes}`. 404 unknown run, 409 not live or note cap reached, 400/422 invalid body or
  over-long text, 500 corrupt store.

Text is stored and returned verbatim as JSON; clients must render it as text, not HTML.
