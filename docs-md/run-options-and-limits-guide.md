# Run options, limits and the dashboard (guide)

Covers epic E-aYG5vn: one registry of run options, the dashboard forms and "Limits & settings"
panel built from it, the `effort` → `turn_budget` rename, and provider-neutral `autocompact`.
Behaviour described here is what ships today; the gated final flip (T-9ymKja) is called out
explicitly as **not yet done**.

## Vocabulary

| Term | Meaning | CLI | Env | Config / spec key |
|---|---|---|---|---|
| **Thinking effort** | model reasoning depth: `low`…`max`. Unset = agent default. claude-cli only. | `--thinking-effort` | `AO_THINKING_EFFORT` | `thinking_effort` |
| **Turn budget** | max-turn *tier*: low=15, medium=30, high=60, xhigh=120 turns | `--turn-budget` | `AO_TURN_BUDGET` | `turn_budget` |
| **Max turns** | explicit integer; overrides the turn budget | `--max-turns` | `AO_MAX_TURNS` | `max_turns` |
| **Autocompact** | `auto` or a context-token threshold 100000–1000000. claude-cli only | `--autocompact` | `AO_AUTOCOMPACT` | `autocompact` |

Precedence at run level is always CLI > env > `.ao/config.yaml` > default. For agents/tasks the
spec key wins in the order task > agent > run level.

## Launching from the dashboard

* **New run**, **From template** and the **Resume** panel render the same schema-driven form
  (`GET /api/schema/run-options`; the allow-list behind `POST /api/runs`,
  `POST /api/templates/{name}/instances` and `POST /api/runs/{id}/resume` is derived from the same
  registry). A body key that is not a registry option is dropped, never forwarded to argv.
* Options the executor does not support are labelled "applies to: claude-cli" and warned about in
  the run log, never fatal.
* Every registry option is also a CLI flag on `ao run` (checked by a test); the dashboard adds no
  setting the CLI lacks.

## Reading limits on a run

* **Limits & settings** (run page): each effective setting + its source, and each limit with
  consumption vs cap. `settings_recorded` is `false` for runs started before the snapshot existed
  (the panel shows "—"; nothing is invented).
* **Resume history**: `state.json` keeps `effective_settings` (latest) and
  `effective_settings_history[]` (one entry per `start`/`resume`, each with its full settings), so a
  resume with changed options shows what the earlier invocation used.
* **Task panel / Now running**: per task the limits in force at its latest dispatch
  (`effective_limits`): thinking effort, turn budget, max turns, autocompact, timeout, attempts.
  `max_turns` is the cap actually applied (explicit value, else derived from the tier).

## Mid-run cost cap (`--cost-cap-usd`)

> **Status (2026-11-11):** committed. `ao run`/`ao resume --cost-cap-usd USD` and `COST_CAP_OPTION` in
> `run_options.OPTIONS` landed in `3e2870b` (hardened in `4b9d6a3`: bounded by `MAX_COST_CAP_USD`; a schedule-governed
> run's generic resume refuses `cost_cap_usd`, and the CLI warns when you raise a governed run's cap). Design/as-built:
> `scheduled-run-governance-hld.md` §9 and §26.

Behaviour as implemented in the engine:

* The cap is a positive finite USD number (zero/negative/NaN/inf are rejected, `validate_cost_cap_usd`).
* The run halts **resumably** (`failed`, like every breaker action) once its settled cost reaches the cap. It is
  checked when tasks are dispatched and at task boundaries, and the boundary check trips only while work remains.
* **Overshoot:** in-flight tasks are drained, not killed, so spend can exceed the cap by roughly the in-flight work.
  A run that completes all its work is `succeeded` even if it finished over the cap.
* **Sticky on resume** (`Option.sticky_on_resume`): the cap is persisted in `RunState.cost_cap_usd`; a resume without
  the flag keeps it, an explicit value replaces it.
* **Non-latching:** unlike `run_cost_usd` breakers, a resumed run with a higher cap is gated again at the new value.
* **Blind executors:** a task whose executor reports no cost counts as $0 in-run (`attempts_without_actuals` is
  incremented); for scheduled runs the ledger charges such runs their reservation.
* The sentinel LLM lane budget is `min(run_cost_usd breaker cap, cost cap)`, see `guide-sentinel.md`.

## Walkthrough: a recurring job with limits (schedule → usage → cap hit → pause → resume)

Design: [`scheduled-run-governance-hld.md`](scheduled-run-governance-hld.md) §26 (as built),
[`scheduler-triggers-hld.md`](scheduler-triggers-hld.md) (firing), [`guide-scheduled-runs.md`](guide-scheduled-runs.md).

**How this was verified (2026-11-11, branch `ad/enhancements-10nov`).** Every command below was run against the
clone's CLI (`uv run ao ...`, a temp workspace and a temp `AO_SERVICE_STATE_DIR`/`AO_SERVICE_CONFIG`). The output is
copied from that session. Note the *installed* `ao` snapshot at that time had no `schedule` command at all; this
needs a build from `main`/this branch. **What was not run live:** no daemon fired a real run and no agent spent
money, so the "cap hit" state in step 4 was *seeded* (a fire record with `cap_state: paused` plus a
`schedule.governance.cost_cap_paused` event, written with the same store classes the engine uses). Engine-side
pause/resume behaviour is covered by `tests/service/test_capped_resume.py` and
`tests/test_e2e_cli_cost_cap.py`, not by this walkthrough. Two other deviations from a real run: the `list` output
below shows the "no live engine"/"not registered" warnings because nothing was started, and `resume` was run with
`--queue` (the request file is written; no daemon drained it).

**1. Declare the schedule with limits** (one run a day, at most 5 a week, $2 per run, $10 per week, pause on cap, keep a
paused run resumable for 3 days):

```bash
cd /path/to/ws          # needs .ao/ ; w.json is any workflow spec
ao schedule add nightly --interval 86400 --workflow w.json --tz UTC \
    --quota day=1 --quota week=5 --cost-per-run 2 --cost-cap week=10 \
    --on-cap pause --pause-ttl 259200
# Added schedule 'nightly' to /path/to/ws/.ao/schedules.yaml
ao schedule validate
# OK: 1 schedule(s) valid in /path/to/ws/.ao/schedules.yaml
```

It writes (this is the whole `limits` block; edit it by hand if you prefer):

```yaml
  limits:
    quotas: [{period: day, max_runs: 1}, {period: week, max_runs: 5}]
    cost: {per_run_usd: 2.0, on_cap: pause, pause_ttl_seconds: 259200,
           periods: [{period: week, max_usd: 10.0}]}
```

`--per-run`/`--cost-per-run` is the per-run cap handed to the engine as `--cost-cap-usd`; `--on-cap abort` makes a hit
final instead of resumable. The daemon fires it once opted in (`ao service add --workspace . --schedules`, then
`ao service run`).

**2. Check usage** (read-only, works without a daemon; `--json` for machines, `--at ISO8601` to evaluate a past instant):

```text
$ ao schedule usage nightly
nightly  (UTC, week starts monday)                          limits digest 0946a7
  day   2026-11-11  runs 0/1  spend $0.00 (+$0.00 reserved) / -       resets 2026-11-12 00:00 UTC
  week  2026-11-09  runs 0/5  spend $0.00 (+$0.00 reserved) / $10.00  resets 2026-11-16 00:00 UTC
```

**3. A run spends money** — a fire that stays under quota consumes one slot per window; once the day quota is used
the next tick is suppressed (`schedule.governance.skipped`, reason `quota_exhausted`, visible in `ao schedule history`).

**4. A run hits its per-run cap and pauses.** (State below was seeded, see above.) `usage` now shows the paused run and
its expiry; `list` has `TODAY` and `PAUSED` columns:

```text
$ ao schedule usage nightly
  day   2026-11-11  runs 2/1  spend $2.90 (+$0.00 reserved) / -       resets 2026-11-12 00:00 UTC
  week  2026-11-09  runs 2/5  spend $2.90 (+$0.00 reserved) / $10.00  resets 2026-11-16 00:00 UTC
  paused: nightly-run-2  $2.00 of $2.00  expires 2026-11-14 05:38 UTC
  last events:
    2026-11-11 05:08  cost_cap_paused
$ ao schedule list
ID       KIND      ENABLED  STATUS  NEXT FIRE  LAST FIRE  LAST RUN  RUNS  LIMITS                TODAY  PAUSED
nightly  interval  yes      active  -          -          -         0     1/d 5/w $2/run $10/w  2/1    1
$ ao schedule history nightly
TIME                       EVENT                                SCHEDULE  REASON  RUN / DETAIL
2026-11-11T05:08:03+00:00  schedule.governance.cost_cap_paused  nightly   -       nightly-run-2
```

(The `runs 2/1` is an artefact of the hand-seeded records; a real gate would have suppressed the second fire.) The
paused run is an ordinary resumable `failed` run (sentinel and `ao resume` see it too).

**5. Resume it** — only an explicit operator action ever resumes; nothing resumes automatically. Top up with
`--add-usd` (default: the per-run cap); the request is refused (exit **3**) when the pause expired, the run was
aborted, it is not paused, or the period caps leave no headroom:

```text
$ ao schedule resume nightly --run nightly-run-2 --add-usd 1.5 --queue
queued; the daemon resumes it within one evaluation interval
Resume of nightly-run-2 accepted: +$1.50, new cap $1.50
```

`--queue` hands the request to a live daemon (the single launch authority); `--local` drains it in this process.
A resume is the same fire, so it takes no new quota slot and is charged to the original fire's windows.

**6. Adjust or stop.** `set-limits` rewrites only `limits` (flags merge by period; `--clear-quotas`/`--clear-cost`
drop first) and `disable` keeps the schedule but never fires it:

```text
$ ao schedule set-limits nightly --cost-cap week=25 --quota week=7
Updated limits of 'nightly': 1/d 7/w $2/run $25/w
$ ao schedule disable nightly
Disabled schedule 'nightly'
```

Observed quirk: re-issuing the same `resume --queue` for a run whose request is still queued is accepted again (the
second printed the same "accepted" line); the drain re-checks every condition, so it is harmless, but the CLI does not
de-duplicate pending requests. Fixed prompts: `--prompt-file`/`--prompt` with `--pin-prompt`, or `ao schedule pin-prompt ID`
later (a later edit then skips fires).

## Migration: `effort` → `turn_budget`

Until now `effort` (key, `--effort`, `AO_EFFORT`) meant the max-turn tier. That tier is now called
**turn budget**, because *effort* is going to mean **thinking effort**.

**This release (current behaviour)**

* `turn_budget` / `--turn-budget` / `AO_TURN_BUDGET` are canonical.
* `effort` / `--effort` / `AO_EFFORT` still work and still mean the turn budget, with a
  deprecation warning. Setting both to different values is an error; the same value is accepted.
* `ao status` / `ao usage` / the dashboard Usage view group by turn budget and thinking effort.

**What you should do**

```bash
ao migrate effort-keys --workspace .            # dry run: prints the diff, changes nothing
ao migrate effort-keys --workspace . --apply    # rewrites effort -> turn_budget (idempotent)
```

It renames the exact key `effort` (never `thinking_effort` / `overseer_effort`; commented YAML lines
are skipped) as a textual edit that preserves formatting, in `.ao/config.yaml`, `.ao/overlays/**`,
`specs/**` and `workflows/**` (`.json`/`.yaml`/`.yml`/`.tmpl`, including scaffolded run instances);
it skips `.orchestrator/`, `outputs/` and `.git`. A mapping that already holds *both* `effort` and
`turn_budget` is reported as a CONFLICT, left alone, and the command exits 1. A second `--apply`
finds nothing to do. Change any `AO_EFFORT` env vars / CI variables and `--effort` flags to the
`turn_budget` equivalents by hand — the command does not see those.

**Not done yet (T-9ymKja, blocked by an owner gate)** — after every workspace is migrated and one
deprecation release has shipped, `effort` / `--effort` / `AO_EFFORT` will become aliases of
**thinking effort**. Do not rely on the old meaning past that release.

Template note: the built-in `routed-runner` / `overseer-runner` still emit the legacy `effort` key
on injected tasks (and `overseer_effort` is a turn-budget tier); they are covered by the same alias
until the flip.

## Tests

`tests/ui/test_e2e_run_options_dashboard.py` drives a real `ProcessSupervisor` + real `ao run` /
`ao resume` children (fake executor, temp workspace) through the API for New Run, Template Launch
and Resume, and `CliRunner` for the flags, deprecation, `ao migrate effort-keys` and the CLI/
schema parity check. `tests/test_run_options.py` and `tests/test_turn_budget_autocompact.py` cover
the registry and the alias matrix.
