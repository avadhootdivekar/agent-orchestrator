# ADR-0014 — The `ao service` supervisor owns cron and event triggers; fires are at-most-once, non-backfilling, and launched through the existing run path

- Status: **Proposed** (2026-09-06) — design complete, not implemented; the ten decisions below are the ones an implementer must not re-derive
- Date: 2026-09-06
- Deciders: Avadhoot Divekar (user — decisions D1, D3, D4, D5, D8 were locked by the user before design started), Claude (architect role)
- Related: **ADR-0012** (multi-workspace service supervisor — the daemon, registry, state dir, singleton lock, and detached-run guarantee this ADR extends rather than replaces) · **ADR-0010** (dashboard architecture — the subprocess launch path, `LaunchRecord`s, the argv allow-list, and the unauthenticated/loopback posture D7 established) · design [`scheduler-triggers-hld.md`](../scheduler-triggers-hld.md) · epic ticket [`meta/tickets/E-Sc9Rt4-scheduler-triggers/EPIC.md`](../../meta/tickets/E-Sc9Rt4-scheduler-triggers/EPIC.md) · [`meta/ROADMAP.md`](../../meta/ROADMAP.md) §1 (status row "Cron / event triggers — Spec'd, not scheduled") / §3.2 (this epic) / §3.1 (the auth gap this ADR deliberately does not close)

---

## Context

`Trigger(type=manual|cron|event, schedule, timezone, event)` has been on `WorkflowSpec.triggers`
and in `specs/workflow.schema.json` since the MVP, and `scheduler.py` ships a working
timezone-aware `CronScheduler` backed by `croniter` (already a core dependency) plus an
`EventScheduler` sentinel-file stub with an injectable clock. None of it is reachable: nothing
in `src/` imports `scheduler.py`, and the engine never reads `WorkflowSpec.triggers`. The
roadmap has recorded this as "Spec'd, not scheduled" since §3.2 was written.

Meanwhile `ao service` (ADR-0012) shipped a user-level supervisor daemon that is already the
only always-on process on the machine, already holds the registry of every workspace, already
owns a singleton lock and a state directory, already runs a 2-second monitor loop, and already
launches runs headlessly (`Supervisor._decide_and_act` → `ProcessSupervisor.launch_resume`) for
boot-resume. Adding a scheduler is therefore mostly a wiring problem — and the decisions that
matter are about *policy under failure*, not about mechanism.

Ten decisions were coupled enough to record together. Three of them (D5 at-most-once, D6 no
backfill, D9 the webhook's own listener) exist specifically because an agent run is not a cheap,
idempotent data-pipeline task: it costs real money, takes hours, and mutates a working tree.
Scheduler defaults that are merely inconvenient in Airflow are wallet incidents here.

---

## D1 — The supervisor owns scheduling; a per-workspace scheduler process is rejected as the production topology and kept only as a dev/test mode

**Decision.** `ao service run` gains a `ScheduleEngine` sibling of `Supervisor`, driven from the
same monitor loop, evaluating every registered workspace's schedules and materializing runs.
There is exactly one scheduler per user, protected by the supervisor's existing
`state_dir/supervisor.lock` flock. `ao schedule daemon [--workspace ROOT] [--once]` runs the
identical engine in the foreground against one workspace for local iteration and tests.

**Why the supervisor.** Every property a scheduler needs is already built and tested there:
a singleton guarantee (so no leader election has to be invented), the workspace registry, a
durable XDG state dir with an established atomic write-then-rename idiom, a periodic loop, and
a proven headless launch path. Putting the scheduler anywhere else means re-deriving all five.

**Why not one scheduler process per workspace.** It would need N clocks, N fire stores, N
singleton locks, and it makes a service-wide concurrency cap structurally impossible — which is
the one guard that actually protects the user when four workspaces all want 02:00. Worse, the
natural home for such a process is the per-workspace child, and the supervisor *restarts
children with backoff*: a restart-prone process is the worst conceivable owner of something that
must not double-fire.

**Why a sibling of `Supervisor`, not a member of it.** The supervisor's single responsibility is
child processes. A sibling can be omitted entirely (`--no-schedules`) without touching
supervisor code, and it keeps `status_snapshot()`'s existing shape untouched — the scheduler's
own snapshot is merged in by `service/cli.py::build_status_provider`, the enrichment point
ADR-0010 D5's thin-adapter rule already designates, so `hub.py` stays a pure renderer.

**Consequence.** A scheduler bug can take the supervisor down and with it every dashboard. This
is bounded three ways: each schedule's evaluation is individually exception-isolated and
auto-quarantined after `MAX_CONSECUTIVE_EVAL_ERRORS`; schedule work is capped by a per-tick
wall-clock budget so it cannot starve child monitoring; and the generated systemd unit is
already `Restart=on-failure`. The complementary consequence is that **schedules do not fire when
the daemon is not running** — which is why `ao schedule list` prints a warning when it cannot
find a live daemon, since "my schedule never fired" is otherwise a silent failure.

---

## D2 — Schedules are bound in a workspace-level `.ao/schedules.yaml`; `WorkflowSpec.triggers` remains a declaration of intent

**Decision.** The authoritative binding lives in `<workspace>/.ao/schedules.yaml`: a list of
bindings, each pairing a trigger with *a workflow (or template + params), a prompt, and run
arguments*. `WorkflowSpec.triggers` is unchanged and stays a declaration; `ao schedule add
--from-workflow <path>` reads a workflow's own cron/interval triggers and materializes bindings
from them, and `ao validate` **warns** (never errors) when a spec declares a non-manual trigger
with no binding.

**Why not the workflow spec alone.** A workflow file cannot say which params, which prompt, or
which run arguments a scheduled invocation should use — and for a template-based workflow it
cannot even say which instance. The real consumer's pattern is `ao new epic-runner <slug>
--param type=epic --prompt-file …` followed by `ao run --workflow <rendered>`; a trigger inside
the rendered spec would have no way to express the half that came from the CLI.

**Why a new file rather than a `schedules:` key in `.ao/config.yaml`.** `ao schedule
enable/disable/add/remove` **writes** this data. `.ao/config.yaml` is hand-authored and, in the
real consumer, is more comment than configuration — a machine rewrite would destroy exactly the
commentary that makes it maintainable. A separate, ao-managed file can be rewritten atomically
under a lock with no such loss.

**Why not both.** Two sources of truth for "when does this fire" is the failure mode this
decision exists to avoid. `.ao/config.yaml` gains nothing from this epic.

**Consequence.** Three levels with disjoint ownership: the workspace author owns
`.ao/schedules.yaml` (what fires), the machine operator owns the registry's per-workspace
`schedules: bool` (whether this daemon honors it) and the service-wide caps. Nothing is
configured twice. The cost is one more file to learn, mitigated by `ao schedule add` writing it
for you.

---

## D3 — Overlap policy is `skip` | `queue` | `allow`, defaulting to `skip`; there is deliberately no `replace`

**Decision.** Each binding carries `overlap`. `skip` (default) suppresses a fire while a run
from that schedule is active. `queue` holds exactly one pending fire and launches it on the
first tick after the active run ends. `allow` launches regardless, still bounded by the
per-workspace and service-wide concurrency caps.

**Why these three names.** They are Temporal's `SKIP` / `BUFFER_ONE` / `ALLOW_ALL` renamed to
plain English, and `skip`-as-default matches Argo `CronWorkflow`'s `concurrencyPolicy: Forbid`.
Borrowing vocabulary means an operator who has run either system already knows what these do.

**Why `skip` is the default.** An agent run costs real money and takes hours. If a nightly
epic-runner is still going at the next 02:00, silently starting a second one doubles the spend
*and* puts two agents in one working tree — the exact contention the parallel-execution epic
already documented as an unsolved hazard (roadmap §4).

**Why not `replace` / `CANCEL_OTHER` / `TERMINATE_OTHER`.** Killing an in-flight agent run
destroys unfinished paid work and would fight the cancel/resume semantics ADR-0012 D2
established. There is no scenario in this system where "throw away the last three hours because
the clock struck 2" is the right answer; an operator who wants it can `ao cancel` and
`ao schedule run-now`.

**Consequence.** `queue` is deliberately depth-one and first-wins, so a schedule that fires
faster than it completes accumulates at most one pending fire rather than an unbounded backlog.
Suppressed fires are still *recorded* (as `suppressed` fire records with a reason), so
`ao schedule history` shows the pile-up rather than hiding it.

---

## D4 — Catch-up is "run once if missed, within a bounded window"; Airflow-style backfill is structurally excluded

**Decision.** `catch_up: once` (default) fires exactly once for the most recent missed instant,
provided its lateness is within `catch_up_window_seconds` (default 24 h). `catch_up: skip` fires
nothing. In both cases every earlier missed instant is recorded `schedule.missed` and burned.
The evaluator collapses a backlog to its most recent due instant *before* any policy check, so
non-backfill is a property of the algorithm, not merely of a default.

**Why systemd's model and not Airflow's.** systemd timers' `Persistent=true` means "if the
machine was off when this should have run, run it once at boot" — which is exactly what a
person would have done by hand. Airflow's `catchup=True` firing one run per missed interval is
the single most famous footgun in that ecosystem, and at agent-run prices a week of downtime
would translate to a hundred multi-hour paid runs starting simultaneously.

**Why 24 hours.** It is the honest compromise for the actual deployment: a laptop off overnight
still gets its 02:00 epic-runner when it boots at 09:00, while a machine off for a week does not
surprise its owner with a run the instant it comes back — it does exactly one, deliberately.
Deriving the window from the schedule's own interval is more principled and less explainable;
it is recorded as an open question rather than shipped.

**Consequence.** A schedule that must never be skipped has no way to express that, and a
schedule whose downtime exceeded the window silently waits for its next natural instant. Both
are visible: `schedule.missed` is logged at WARNING and surfaced in `ao schedule history` and
hub status.

---

## D5 — A fire is at-most-once: the intent record is written before the launch, and an orphaned intent is never relaunched

**Decision.** Every logical fire has a `fire_key = sha256(workspace_root|schedule_id|
scheduled_for)`. A record is written atomically (write-then-rename + fsync) with status
`intended` **before** the launch, then updated to `launched` with pid and run id. On daemon
start, an `intended` record whose pid is dead and whose run directory does not exist is marked
`orphaned` and **is not relaunched**. A record in *any* status suppresses every future attempt
at that `fire_key`, including catch-up.

**Why at-most-once and not at-least-once.** The uncertain window is the few milliseconds
between writing the intent and `Popen` returning. A relaunch there risks a duplicate multi-hour,
real-money run against a working tree the first launch may already be mutating. For an
idempotent data task at-least-once is obviously right; for this workload it is obviously wrong.

**Why the fire key is derived from `scheduled_for`, not from wall-clock "now".** It makes the
key a *logical* identity, which buys two properties for free: a backwards NTP or DST correction
re-derives a key that already exists and is therefore suppressed (no double fire), and a
file-watch fire keyed on the newest changed mtime is replay-safe across a crash. No separate
clock-skew reconciliation is needed anywhere.

**Why a separate store rather than generalizing `BootResumeGuard`.** That guard answers "may we
attempt this resume again, given how many times we already tried and how recently" — a
retry-budget question. This store answers "has this logical instant already been attempted at
all" — an identity question. Merging them would couple two unrelated policies to save one file.
What *is* extracted is the fourth copy of the same atomic-JSON-state code, into
`service/statefile.py`.

**Consequence.** A crash inside the fire window means a **missed run**, not a duplicated one.
That is a deliberate, stated limitation: the operator sees `schedule.fire_orphaned` at WARNING
in `ao schedule history` and in hub status, and `ao schedule run-now` is one command away.

---

## D6 — Scheduled runs go through `ProcessSupervisor.launch_run`, and `ao run` gains `--run-id`

**Decision.** A scheduled fire launches through the same `ProcessSupervisor.launch_run(...)`
the dashboard uses, producing a real `LaunchRecord`. `ao run` gains a `--run-id` option; the
scheduler supplies `ao-<schedule_id>-<scheduled_for>` and constructs its `ProcessSupervisor`
with `run_id_discovery_timeout=0.0`.

**Why not a second launch path.** ADR-0010 D4 made the dashboard launch runs as subprocesses of
the same CLI precisely so there is one execution path through the engine; boot-resume then
reused it headlessly, proving it works with no HTTP involved. A scheduler that bypassed it
would lose launch records, cancellation, log capture, and the existing argv allow-list in one
stroke — and would immediately diverge.

**Why `--run-id` is in scope here rather than deferred.** Today the launcher learns a run's id
by diffing the runs directory, polling for up to ten seconds (roadmap §4 records the resulting
mis-attribution risk). Ten blocking seconds per launch inside a monitor loop is unacceptable,
and an *inferred* fire→run link makes overlap detection and `ao schedule history` unreliable at
exactly the moment they matter. Supplying the id makes the link exact and costs about a day.

**Consequence.** Roadmap §4's run-id attribution caveat is closed for the scheduled path (and
available to close for the dashboard path later). `ao run --run-id` must refuse an id whose run
directory already exists, or it becomes a way to corrupt an existing run's state.

---

## D7 — Event triggers reuse the existing `Scheduler` ABC unchanged; file watching is polling, not inotify

**Decision.** `Scheduler.next_fire(trigger, now) -> datetime | None` is kept verbatim, with its
contract stated precisely: *a returned value at or before the engine's current time means "fire
now"*. `FileWatchScheduler` and `WebhookScheduler` join `ManualScheduler` / `CronScheduler` /
the new `IntervalScheduler` behind it; `EventScheduler` survives as a deprecated alias (a
sentinel path is a degenerate one-file watch) so its existing tests keep passing untouched.
File watching polls a workspace-relative glob set, comparing an `(mtime_ns, size)` digest.

**Why one interface really works here.** The existing sentinel stub already returns `now` when
its file exists — "has this fired?" was always expressible in this signature. Nothing had to be
bent to fit; a second `EventSource` abstraction would have been two abstractions where one
suffices.

**Why polling and not inotify.** No new dependency; identical behavior on every platform the
CLI supports; trivially deterministic under a fixed clock in tests; and no risk of exhausting
inotify watch descriptors on a large workspace. The `Scheduler` seam is exactly where a
`watchdog` backend would later slot in without touching the engine.

**Why `scheduled_for` is the newest changed mtime, not the observation time.** It makes a
file-watch fire *content-derived*, so the same file state derives the same `fire_key` and D5's
suppression makes the fire idempotent across a crash. Dagster's sensor cursors are the same
idea. A default ignore list (`.git/`, `node_modules/`, `.venv/`, `__pycache__/`,
`.orchestrator/`) prevents the classic loop where a watch is triggered by the runs it produced.

**Consequence.** Latency is bounded below by the poll interval (default 30 s) plus a debounce
window, and a scan is bounded by `max_files` and a per-tick time budget — a truncated scan
never fires, so a huge workspace degrades to "no event triggers" rather than to a stalled
daemon. `Trigger` gains an additive optional `interval_seconds` and an `"interval"` enum member;
absent both, every existing spec behaves identically.

---

## D8 — The webhook is authenticated with an HMAC shared secret and listens on its own default-off port — not on the hub, not on the dashboard

**Decision.** Webhook ingress is a dedicated FastAPI app on `--webhook-port` (default 8771),
**disabled by default**, bound to loopback unless explicitly told otherwise, exposing only
`POST /hooks/{workspace_slug}/{schedule_id}` and `GET /healthz`. Every request must carry a
valid `X-AO-Signature: sha256=<hex>` (or GitHub's `X-Hub-Signature-256`) HMAC-SHA256 over the
raw body, compared with `hmac.compare_digest`, plus a bounded-LRU delivery-id nonce check, an
optional ±300 s timestamp window, a 64 KiB body cap, and a per-schedule token bucket. Secrets
are referenced by env-var name or by mode-checked file path — never inline. `SecurityMiddleware`
is **mounted on the app**, not merely imported.

**Why not the hub.** The hub is unauthenticated by design and lists every registered workspace.
Mounting the webhook there means that exposing the webhook to a git host also exposes the
machine's workspace inventory — a trade no operator would make consciously. The per-workspace
dashboard is strictly worse: it is the unauthenticated, file-browsing, run-starting surface.

**Why authentication at all, when nothing else here has it.** ADR-0010 D7's posture is "the
threat model is a single developer on their own machine, so loopback plus a warning is the whole
mitigation." A webhook's entire purpose is to be reached from off-box, which invalidates that
premise for this one endpoint. An HMAC over the body with a replay window is the minimum that is
actually safe, is what every git host already speaks, and is deliberately *not* a down-payment
on the authentication framework roadmap §3.1 owns.

**Why a verified delivery only enqueues.** The fire happens on the next engine tick through the
identical overlap / concurrency / `until` path as a cron fire, so a webhook can never bypass a
guard or outrun the concurrency cap, and a delivery flood costs one queue entry rather than one
agent run.

**Why unknown-schedule and bad-signature both return 401.** Distinguishing them turns the
endpoint into a free oracle for enumerating a machine's workspaces and schedule ids.

**Consequence.** This is one authenticated endpoint, not an auth system: there is no identity,
no session, no authorization model, and no audit trail of who triggered what. Those remain
roadmap §3.1. Mounting `SecurityMiddleware` is called out explicitly because ADR-0012's
early-gate correction #6 found the exact mistake of importing the allow-list constants without
attaching the middleware, which enforces nothing.

---

## D9 — `.ao/schedules.yaml` is untrusted workspace content: no free-form argv, no path escapes, no inline secrets

**Decision.** A binding's `run_args` is a closed pydantic model (`extra="forbid"`) whose fields
are exactly `ui/processes.py`'s existing `ALLOWED_OPTIONS` ∪ `ALLOWED_BOOL_OPTIONS`. There is no
`command:`, `env:`, `shell:`, or free-form flag list, and none may be added without a superseding
ADR. Every path field (`workflow`, `prompt_file`, `until.*`, `watch.paths`) resolves through
`LocalFsArtifactStore`'s existing workspace-root guard. Webhook secrets are referenced, never
inlined. `MIN_INTERVAL_SECONDS = 60`, `max_files`, the scan budget, the body cap, and the rate
limiter bound the work a hostile or careless file can cause.

**Why this needs saying.** `.ao/schedules.yaml` arrives via `git clone` like everything else in
a workspace. A `command:` field would turn "clone this repo and register it" into arbitrary code
execution triggered by a clock — a materially worse primitive than anything the dashboard offers
today, because it needs no human in the loop at all.

**Why reuse the existing allow-list rather than write a new one.** It already exists, is already
tested, and already governs the dashboard's launch path. Reusing it means the scheduler adds
*zero* new argv surface. Where it silently drops unknown keys, the schedule loader instead
raises a named error — a config file should tell you it is wrong.

**Consequence.** `ao service add <dir>` is, and remains, the trust boundary: an explicit
operator action stating that this workspace's files may drive agent runs on this machine. This
epic does not widen it. `reposets`/`agents` deliberately are not schedule fields — they come
from `.ao/config.yaml`, exactly as boot-resume already resolves them.

---

## D10 — The dashboard and CLI request fires through a drop directory; the engine remains the only launch authority

**Decision.** `ao schedule run-now` and the dashboard's *Run now* write a request file into
`<state_dir>/schedules/requests/`; the engine drains, deletes, and evaluates it through the full
policy path at the top of the next tick. `--force` bypasses overlap only — never the concurrency
caps, never `until`. Requests older than `REQUEST_TTL_SECONDS = 300` are discarded. When no
daemon is running, the CLI detects that and performs the fire in-process through the same
`ScheduledLauncher`.

**Why not let the dashboard launch directly.** It would be a second launch authority with its
own view of overlap and concurrency, and the dashboard child process cannot see the engine's
in-memory state. One authority means one place where the guards live.

**Why the TTL.** A request queued while the service was down must not fire surprisingly hours
later when it comes back; five minutes is long enough to survive a restart and short enough that
nobody is surprised.

**Consequence.** *Run now* is asynchronous — the UI returns 202 and the run appears within one
tick (≤15 s), not instantly. `enable`/`disable` take the other route: they write
`.ao/schedules.yaml` directly under its file lock, and the engine re-reads it each tick. These
routes let an unauthenticated dashboard caller toggle a schedule and request a fire, which is
**not** a new class of exposure — the same caller can already `POST /api/runs` and start any
workflow — but it is stated here so it is a chosen posture rather than an overlooked one.

---

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| Adopt an existing scheduler (APScheduler, Celery beat, embed Prefect/Temporal) | Every one of them brings a broker, a database, or a server process, and none of them knows about workspaces, launch records, budgets, or quota waits. The scheduling *mechanism* here is `croniter` plus a loop we already run; all the value is in the policy (D3-D5), which no library would supply. (D1) |
| One scheduler process per registered workspace | N clocks, N fire stores, N locks; no service-wide concurrency cap possible; and its natural home is a child the supervisor restarts with backoff. Kept only as `ao schedule daemon` for dev/test. (D1) |
| Drive schedules purely from `WorkflowSpec.triggers` | A workflow file cannot express which params, prompt, or run arguments a scheduled invocation should use. (D2) |
| Put schedules in `.ao/config.yaml` | The CLI must write this data; rewriting a hand-authored, comment-heavy config destroys the comments. (D2) |
| Airflow-style `catchup=True` backfill | One paid multi-hour run per missed interval. Structurally excluded by the backlog-collapse step, not merely defaulted off. (D4) |
| At-least-once fires (relaunch an orphaned intent) | Risks a duplicate multi-hour, real-money run against an already-mutated working tree. (D5) |
| Generalize `BootResumeGuard` into a shared attempt limiter | It answers a retry-budget question; the fire store answers an identity question. Coupling two policies to save one file. (D5) |
| Infer the run id by directory diff, as the dashboard does | Up to 10 s of blocking wall-time inside the monitor loop, and an inferred fire→run link makes overlap detection unreliable. (D6) |
| A second `EventSource` abstraction for event triggers | The existing `next_fire` signature already expresses "has this fired?" — the shipped sentinel stub proves it. (D7) |
| inotify / `watchdog` for file watching | New dependency, platform-specific, harder to make deterministic in tests, and watch-descriptor limits on large workspaces. Left as a future backend behind the same seam. (D7) |
| Mount the webhook on the hub (:8770) or on a per-workspace dashboard | Exposing the webhook would then also expose the unauthenticated workspace inventory, or the file-browsing dashboard itself. (D8) |
| No webhook authentication, relying on network placement | The endpoint's entire purpose is to be reachable off-box, which invalidates ADR-0010 D7's loopback premise for this one route. (D8) |
| Allow a free-form `args:`/`command:` in a schedule binding | Turns a `git clone` into clock-triggered arbitrary code execution with no human in the loop. (D9) |
| Let the dashboard launch scheduled runs directly | A second launch authority with its own view of overlap and concurrency. (D10) |

---

## Consequences summary

- **Gained.** Schedules that fire themselves, for every registered workspace, through the one
  existing launch path — with overlap, catch-up, jitter, and concurrency policies borrowed from
  Temporal/Argo/systemd rather than invented; at-most-once fires that survive crashes, reboots,
  and clock changes without a second mechanism; event triggers (file-watch and an authenticated
  webhook) behind the `Scheduler` ABC that was already there; and an exact fire→run link via
  `ao run --run-id`, which also closes roadmap §4's attribution caveat for this path.
- **Given up, deliberately.** Backfill; run replacement; at-least-once delivery; inotify;
  distributed scheduling; and any general authentication or authorization story — the webhook
  HMAC is one endpoint's minimum, not a framework.
- **New failure modes, all surfaced rather than silent.** No daemon ⇒ nothing fires (warned by
  `ao schedule list`). A crash inside the fire window ⇒ one missed run (`schedule.fire_orphaned`,
  WARNING). Downtime beyond the catch-up window ⇒ a skipped fire (`schedule.missed`, WARNING). A
  repeatedly-failing schedule ⇒ quarantined after five consecutive evaluation errors, with the
  rest of the workspace unaffected.
- **Carried limitations.** A DST fall-back repeated local hour is two distinct UTC instants and
  therefore fires twice (`timezone: UTC` is the mitigation); a spring-forward gap skips that
  day. `launch_id` is microsecond-resolution, so a future *batched* launcher would need a
  collision-safe id. And until the per-task isolation epic (`ADR-0013`) lands, roadmap §4's
  parallel-write-conflict caveat applies to concurrently scheduled runs too — which is why
  `max_concurrent` defaults to 1.
- **Roadmap effect on close.** §1's status row ("Cron / event triggers — Spec'd, not scheduled")
  and §3.2 both become deliverable; §3.1 (authentication, authorization on triggers, audit) is
  explicitly *not* addressed and its priority is unchanged.
