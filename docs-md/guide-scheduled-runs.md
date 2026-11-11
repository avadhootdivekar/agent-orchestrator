# Guide — scheduled runs and triggers (`ao schedule`)

Operator guide for the service-owned scheduler (E-Sc9Rt4, [ADR-0014](adr/ADR-0014-service-owned-scheduler-and-triggers.md),
design in [`scheduler-triggers-hld.md`](scheduler-triggers-hld.md)). Budget/quota governance of scheduled runs
is a separate layer: [`scheduled-run-governance-hld.md`](scheduled-run-governance-hld.md).
Everything below was checked against the code on `ad/enhancements-10nov` (2026-11-11); the HLD §22 lists what is
**not** finished.

## What it is, in one paragraph

Schedules are *bindings* in `<workspace>/.ao/schedules.yaml`. The `ao service run` daemon is the only process that
fires them: it loads every registered workspace that has **opted in** (`ao service add/set ... --schedules`), evaluates
the bindings every `DEFAULT_EVAL_INTERVAL_SECONDS` (15 s), and starts runs through the same launch path as the
dashboard. A fire is **at-most-once**: a crash mid-fire can lose that one run, never double it.

Not to be confused with: `scheduling/` (co-scheduling of *tasks* inside one run, `max_parallel`), `scheduler.py` (the
`Scheduler` trigger ABC with `Manual/Cron/Interval` implementations, used here), and the `schedules/` package (the
binding file model, store and `ao schedule` CLI).

## 1. Make a workflow run nightly

```bash
ao service add --workspace /path/to/ws --schedules          # one-time opt-in (default is off)
cd /path/to/ws
ao schedule add nightly --cron "0 2 * * *" --tz Europe/Berlin \
    --workflow workflows/epic-runner/runs/e-abc123/workflow.json
ao schedule validate          # also run by `ao validate`
ao schedule next              # shows upcoming fires (cron/interval only)
ao service run                # or `ao service install` for systemd; the daemon fires, the CLI only edits
```

Or hand-write `.ao/schedules.yaml` (`schedules:` list, optional `defaults:`; kinds `cron | interval | file_watch |
webhook`; see HLD §5.1). Other triggers: `--interval SECONDS` (minimum `MIN_INTERVAL_SECONDS` = 60), `--webhook`
(§6 below). **File-watch**: `ao schedule add inbox --watch "in/*.md" [--watch GLOB]... [--watch-on created|modified]...
[--debounce S] [--poll-seconds S] [--max-files N] --workflow ... --quota day=10` writes a `kind: file_watch` binding
(globs are workspace-relative; `--watch` replaces `--cron/--interval`, cannot combine with `--webhook`; the tuning flags
need `--watch`). The first daemon scan only seeds the baseline, so files that already exist never fire. `ao schedule
next` has no predicted time for event kinds, but lists one (marked `pending event`, `"pending_event": true` in `--json`) when its source already observes a pending change; the preview is read-only and consumes nothing. `--from-workflow PATH` derives bindings from a spec's cron/interval
triggers (file-watch/webhook are not spec trigger types, so they are added with `--watch`/`--webhook`).

No daemon handy (dev/test)? `ao schedule daemon --workspace . [--once]` runs the same engine in the foreground and
refuses to start if another engine holds `schedules/engine.lock`.

## 2. What if my laptop was off?

Catch-up. `catch_up: once` (default) fires **one** run for the missed slot(s) when the daemon comes back, provided the
lateness is within `catch_up_window_seconds` (default 86 400 = 24 h); older misses are recorded as `schedule.missed` and
skipped. `catch_up: skip` never back-fills. It never fires N runs for N missed slots. A slot later than `grace_seconds`
(60) but inside the window is logged `schedule.catch_up`.

## 3. What if the previous run is still going?

`overlap` decides: `skip` (default — the slot is dropped, `schedule.skipped_overlap`), `queue` (one pending fire is
remembered across restarts), `allow` (start anyway, still bounded by `max_concurrent`, default 1 per workspace, and the
global cap). Note `max_concurrent` counts *scheduled* runs only, not runs you started by hand.

## 4. How do I stop it?

| Goal | Command |
|---|---|
| Pause one schedule | `ao schedule disable ID` (re-enable: `enable ID`) — also a dashboard toggle |
| Remove it | `ao schedule remove ID` |
| Stop after N runs / when done | `--max-runs N`, `--until-artifact PATH`, `--until-gate PATH --until-field F [--until-equals V]` (default cap `DEFAULT_UNTIL_MAX_RUNS` = 100 when an `until` is given without `--max-runs`); the schedule becomes `completed` |
| Whole workspace | `ao service set --no-schedules` |
| Whole daemon | start with `ao service run --no-schedules`, or registry `schedules.enabled: false` |
| A run already started | the normal `ao`/dashboard cancel — the scheduler never kills runs |

A schedule whose evaluation fails `MAX_CONSECUTIVE_EVAL_ERRORS` (5) times in a row is auto-disabled
(`status: quarantined`, event `schedule.quarantined`).

## 5. Fire it now

`ao schedule run-now ID` — queued to a live daemon via the drop directory, otherwise launched in-process (`--local`,
`--queue` force one). `--force` bypasses *overlap only*; never caps, `until` or governance. The dashboard's *Run now*
writes the same request (it never launches itself). Requests older than `REQUEST_TTL_SECONDS` (300) are dropped
(`schedule.request_expired`).

## 6. Point a `git push` (or any webhook) at it

```bash
ao schedule add on-push --webhook --workflow workflows/ci/workflow.json \
    --secret-env AO_HOOK_SECRET_CI --webhook-scheme github --rate-per-minute 10
ao service run --webhook [--webhook-host H] [--webhook-port 8771]     # listener is OFF by default; loopback by default
```

* Endpoint: `POST /hooks/{workspace_slug}/{schedule_id}` → `202` accepted; `GET /healthz`. Body ≤ `MAX_WEBHOOK_BODY_BYTES` (64 KiB).
* **Scheme is pinned per binding** (`webhook.scheme`). `github` accepts only `X-Hub-Signature-256` (HMAC-SHA256 over the body — GitHub's
  "Secret" field). `ao` (default) accepts only `X-AO-Signature` = `sha256=` HMAC over `"{ts}.{body}"` and **requires** `X-AO-Timestamp`
  (epoch seconds, within `REPLAY_WINDOW_SECONDS` = 300). Sending the *other* scheme's header is a 401 (`wrong_scheme`).
  GitHub sends no timestamp, so a `github` binding has **no time bound**; its replay defence is the nonce cache (+ `X-GitHub-Delivery`).
  Optional extra: if a `github` delivery also carries an `X-AO-Timestamp` (epoch seconds; GitHub never sends one, a trusted proxy may),
  it must be digits (else 401 `bad_timestamp`) and within 300 s of now (else 401 `stale`, checked after the signature). It is **not signed**,
  so it is only an extra gate, not a replay defence; omitting it is always accepted.
* The secret is `secret_env` (an env var **name**) or `secret_file` (workspace-relative, mode 0600) — never inline; < 16 bytes or an
  ambient variable such as `HOME` is refused (503).
* Non-loopback binding prints a warning and needs `AO_WEBHOOK_ALLOWED_HOSTS` (else 421). Put TLS + per-client limits in front.
* Replay/rate state is in memory and resets when the daemon restarts.
* A delivery only persists a request; the fire goes through the normal overlap/cap/`until`/governance path on the next tick.

## 7. Why did my schedule not fire?

Work down this list; every step leaves evidence.

1. **Is an engine running?** `ao schedule list` prints a warning when no daemon holds the engine lock. Run `ao service run` / install the unit.
2. **Is the workspace opted in?** `ao service list` — `schedules` must be on (default off), and the daemon not started with `--no-schedules`.
3. **Does the file load?** `ao schedule validate` (`schedule.file_invalid` in the events log otherwise).
4. **Is it enabled and active?** `ao schedule list` STATUS: `disabled`, `completed` (an `until` fired; `ao schedule enable ID` or the dashboard Enable restarts it -- with a running engine the restart is queued and applied on its next tick, `runs_count` reset), `quarantined` (5 eval errors). `--until-equals` types `true`/`false`/`-?digits` as bool/int; any other text is a string (a string that looks like a bool/int can only be set in the YAML).
5. **When is it due?** `ao schedule next` (cron/interval; event kinds appear only while a pending event is observed). Cron uses the binding's timezone; DST fall-back can fire an hour twice (use `timezone: UTC`).
6. **What did the engine decide?** `ao schedule history [ID]` (or the dashboard row): `schedule.skipped_overlap`, `schedule.skipped_concurrency`,
   `schedule.missed`, `schedule.governance.skipped` (budget/quota/pause), `schedule.fire_failed`, `schedule.until_satisfied`.
   Raw log: `~/.local/state/ao/service/schedules/events.jsonl` (rolls over at `EVENT_LOG_MAX_BYTES` = 8 MiB).
7. **Webhook?** `webhook.rejected` events carry a `reason` (`bad_signature`, `wrong_scheme`, `stale`, `duplicate`, `rate_limited`, `unknown_schedule`, …); the HTTP status is in the table in HLD §8.2.

## 8. Where things live

| What | Path |
|---|---|
| Bindings | `<workspace>/.ao/schedules.yaml` |
| Engine state, fire intents, drop requests, events | `~/.local/state/ao/service/schedules/` (`AO_SERVICE_STATE_DIR`) |
| Engine singleton | `schedules/engine.lock` |
| Dashboard | **Schedules** view; routes `GET /api/schedules`, `GET /api/schedules/{id}/history`, `POST /api/schedules/{id}/{enable,disable,run-now}` |
| Hub status | `GET /api/service/status` → `schedules` key |
