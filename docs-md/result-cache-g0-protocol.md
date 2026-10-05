# Result cache — G0 value-check protocol

**Executing G0 is a post-merge follow-up owned by the parent or the operator (finplan, with the
operator's consent); it does not block closing the result-cache epic. This document is the
protocol and the report template only: it does not claim that G0 has been run, and nothing in this
repository contains G0 results.** The epic validated the *tooling* of this protocol on a
fake-executor workflow (`output/E-Rc4Hk8-cross-run-result-cache/g0-protocol-smoke.md`); that proves
every command below works, not that the cache is valuable.

- Epic: `E-Rc4Hk8-cross-run-result-cache` · Task: `T-nPMuz4-cache-shadow-value-check`
- Design refs: [HLD](cross-run-result-cache-hld.md) §22.5 (G0), §13.4 (`ao cache --json`), §13.6
  (`result_cache` usage object), §15 (events), §23.2 OQ-6;
  [ADR-0019](adr/ADR-0019-cross-run-result-cache.md) D26, D34, ALT-8.

## 1. What G0 answers

Whether the result cache would actually hit often enough, on a real consumer workflow, to justify
recommending `on`. Key components fail closed (repo HEADs, executor fingerprint, prior output
content), tasks that commit never store, and per-instance output paths put different paths in the
key, so the hit rate may be low. `AO_CACHE=shadow` measures it without risking a stale result:
every lookup runs and is recorded, nothing is ever restored, every task still dispatches normally
(so shadow adds hashing and storage, never model spend), and a successful run still stores (and
refreshes) its entry (ADR-0019 D26).

- **Would-hit rate** = `would_hits / lookups`. `lookups = hits + would_hits + misses`; `ineligible`
  tasks (and tasks that are not opted in) are outside the denominator.
- **Avoidable spend** = `avoidable_cost_usd`, the sum of the stored source entries' cost over the
  would-hits (an estimate: the source run's cost, retries included).

## 2. Procedure

All commands are bash and copy-pasteable. `python3` (3.8 or newer) is the only tool used beyond `ao`.

### Step 0 — set the variables

```bash
export AO=ao-beta                 # the beta flavour (Step 1); the stable `ao` is never used for G0
export WS=/abs/path/to/workspace  # the repo set's workspace_root: cache and runs live under $WS/.orchestrator
export WF_ID=<workflow id>        # the `id` field of the workflow spec being measured
export WINDOW_START=19700101T000000Z   # run-id timestamp (UTC) where the window begins; the default keeps every run
export DAYS=7                     # length of the observation window in days (used for $/week)
```

Run ids look like `<workflow id>-<UTC YYYYMMDDTHHMMSSZ>` (second granularity: two runs of one
workflow started in the same second would collide, which does not happen in real use).

### Step 1 — install the epic build as the beta flavour

```bash
bash install.sh --flavor beta     # from a checkout of the merged commit, UI bundle built first
ao --version; command -v ao       # stable: note it, it must not change
ao-beta --version; command -v ao-beta
```

Expected: `ao-beta` resolves to `~/.local/bin/ao-beta` and reports the merged commit; the stable `ao`
output is unchanged. (Flavours: [install-flavors.md](install-flavors.md).) An old install fails with
`No such option: --cache`, which also means the build is not the epic build.

### Step 2 — choose workflows and opt in only pure tasks

Opt in (`cache: true` on the task, or `defaults.cache: true` plus `cache: false` where needed) only
tasks whose **whole effect is captured by their declared `outputs`**, judged by reading the task
(HLD §17). Do **not** opt in a task that edits undeclared files, has external side effects
(network, tickets, `git push`), exists to produce a fresh answer, or reads ambient state the key
does not cover. Record the decision and the reason per task in the report (section 3, table 2).
Opting in an impure task would measure a rate that `on` could not safely deliver.

Check what is eligible before the window opens (the banner and `ineligible` reasons name any
task that cannot be cached, e.g. `no_outputs`, `pre_hook`, `isolation_worktree`, HLD §8.3.3).

### Step 3 — open the window: record the start state

<!-- g0-cmd:stats-start -->
```bash
date -u +%Y-%m-%dT%H:%M:%SZ                       # window start, write it in the report
$AO cache stats --workspace "$WS" --json | python3 -c '
import json, sys
d = json.load(sys.stdin)
print("entries=%s expired_entries=%s bytes.total=%s oldest_created_at=%s newest_created_at=%s"
      % (d["entries"], d["expired_entries"], d["bytes"]["total"],
         d["oldest_created_at"], d["newest_created_at"]))'
```

<!-- g0-fields:stats entries expired_entries bytes.total oldest_created_at newest_created_at -->

Expected output fields (`ao cache stats --json`, HLD §13.4): `entries`, `bytes.total`,
`expired_entries`, `oldest_created_at`, `newest_created_at`. With no cache directory yet the
command still exits 0 with `exists: false`, `entries=0` and both dates `null`.

### Step 4 — run under shadow for the observation window

Run the real workflow(s) as usual, with the env var set; add nothing else:

```bash
AO_CACHE=shadow $AO run --workflow <workflow.json> --reposets <reposets.json> --agents <agents.json>
```

(`ao resume` honours `AO_CACHE` too; in a service environment set `AO_CACHE=shadow` there.)
The first stderr line must be the banner
`Result cache: shadow (source=env), N of M static task(s) opted in, at <WS>/.orchestrator/cache`,
and the summary at the end of the run ends with
`Result cache: hits=0 (...) would_hits=N misses=N stored=N ineligible=N`. `hits` stays 0 in shadow
mode. `N of M` with `N = 0` means no task is opted in: stop and fix the spec.

Two things shape what the window can observe:

- With the default `skip_if_outputs_exist: true`, a task whose outputs already exist is skipped
  **before** any lookup, so it is not in `lookups` (HLD EC-13). Lookups come from tasks that reach
  dispatch with their outputs absent: fresh checkouts, new epics, deleted outputs, or
  `skip_if_outputs_exist: false` tasks. **Never delete real outputs just to feed G0.**
- The declared output paths and their prior content are part of the key (D6), so workflows that
  write per-instance paths show low would-hit rates. That is the measurement, not a defect.

Keep the window long enough to contain repeats of the same work (the HLD suggests several days).

### Step 5 — collect at the end of the window

<!-- g0-cmd:collect -->
```bash
RUNIDS=$(ls -1 "$WS/.orchestrator/runs" \
  | grep -E "^${WF_ID}-[0-9]{8}T[0-9]{6}Z$" \
  | awk -F- -v s="$WINDOW_START" '$NF >= s' | sed 's/^/--run-id /')
echo $RUNIDS     # the runs that make up the window (one --run-id per run)
$AO report-usage --workspace "$WS" $RUNIDS --json | python3 -c '
import json, sys
print(json.dumps(json.load(sys.stdin).get("result_cache"), indent=2))'
```

<!-- g0-fields:result_cache lookups would_hits misses ineligible miss_reasons store_skip_reasons avoidable_cost_usd -->

Expected output fields (the `result_cache` object of `ao report-usage --json`, HLD §13.6):
`lookups`, `would_hits`, `misses`, `ineligible`, `miss_reasons` (`{reason: n}`),
`store_skip_reasons` (`{reason: n}`) and `avoidable_cost_usd`; the object also carries `hits`,
`saved_cost_usd`, `saved_tokens`, `saved_seconds` (all 0 in shadow mode). The key `result_cache` is
**absent** (the command prints `null`) when no scanned run has cache records: check the runs
were started with `AO_CACHE=shadow` and a task is opted in. Without `--run-id` the command scans
every run in the workspace, so use one workspace per measured workflow, or the `RUNIDS` filter
above. A text form is `ao report-usage --workspace "$WS" $RUNIDS | grep 'Result cache'`.

Then the end-of-window store state, with the same command as Step 3:

<!-- g0-cmd:stats-end -->
```bash
date -u +%Y-%m-%dT%H:%M:%SZ                       # window end
$AO cache stats --workspace "$WS" --json | python3 -c '
import json, sys
d = json.load(sys.stdin)
print("entries=%s expired_entries=%s bytes.total=%s oldest_created_at=%s newest_created_at=%s"
      % (d["entries"], d["expired_entries"], d["bytes"]["total"],
         d["oldest_created_at"], d["newest_created_at"]))'
```

`entries` grows with the number of **distinct keys** stored; a repeated key refreshes its entry
(and its `created_at`) instead of adding one, so `newest_created_at` moves with every successful
shadow run and `oldest_created_at` is not the window start. `bytes.total` is the storage price of
the feature for this workload. `expired_entries > 0` means entries outlived `ttl_days` (default
30).

### Step 6 — dominant miss components from `run.log`

`miss_reasons` is almost always `not_found`: the entry for *this* key does not exist. What differs
is *which key component changed* since the previous lookup of the same task. Every `cache.miss`
event in `run.log` carries `reason` and `components` (component name → first 12 hex of its
sha256, HLD §15). This script diffs each miss against the previous miss of the same task and
counts which components changed, and also counts the `cache.skip` events (why tasks were
ineligible, or not stored):

<!-- g0-cmd:miss-components -->
```bash
cd "$WS" && python3 -c '
import collections as C, glob, json, os, re
ok = re.compile(re.escape(os.environ["WF_ID"]) + r"-\d{8}T\d{6}Z").fullmatch
logs = [f for f in glob.glob(".orchestrator/runs/*/run.log") if ok(f.split("/")[2])]
ev = [json.loads(l) for f in logs for l in open(f) if l.startswith("{")]
miss = sorted((e for e in ev if e.get("event") == "cache.miss"), key=lambda e: e["ts"])
last, changed, reasons = {}, C.Counter(), C.Counter(e["reason"] for e in miss)
for e in miss:
    prev = last.get(e["task_id"]); last[e["task_id"]] = e["components"]
    if prev: changed.update(k for k, v in e["components"].items() if v != prev.get(k))
print("miss reasons:", dict(reasons.most_common()))
print("components that changed vs the same task last miss:", dict(changed.most_common()))
skip = C.Counter((e["phase"], e["reason"]) for e in ev if e.get("event") == "cache.skip")
print("cache.skip (phase/reason):", {"/".join(k): n for k, n in skip.most_common()})
'
```

Reading it: the component names are `key_schema`, `agent`, `argv`, `executor_fingerprint`,
`prompt`, `instruction`, `general_instructions`, `inputs`, `dynamic_inputs`, `outputs` and
`repo_heads`. A dominant `repo_heads` means every commit invalidates the key: that is what
`include_repo_heads: false` would relax (OQ-3). A dominant `outputs` is the per-instance-paths
effect. `instruction`/`inputs` are real content changes. `executor_fingerprint`/`argv` mean the CLI
version, model or flags drifted. `cache.skip` rows are the `ineligible` count by reason (e.g.
`lookup/no_outputs`) and the not-stored reasons (`store/...`, cf. `store_skip_reasons`). The first
miss of a task has no previous miss to diff against and contributes only to `miss reasons`.

### Step 7 — compute the would-hit rate and the avoidable spend per week

<!-- g0-cmd:rate -->
```bash
$AO report-usage --workspace "$WS" $RUNIDS --json | python3 -c '
import json, os, sys
rc = json.load(sys.stdin).get("result_cache")
if not rc:
    sys.exit("no result_cache object: no scanned run has cache records")
n = rc["lookups"]
print("lookups=%d would_hits=%d" % (n, rc["would_hits"]))
print("would_hit_rate=%.3f" % (rc["would_hits"] / n if n else 0.0))
print("avoidable_usd_per_week=%.2f" % (rc["avoidable_cost_usd"] / float(os.environ["DAYS"]) * 7))'
```

Run Steps 5-7 once per measured workflow (change `WF_ID`), and once for the whole window if one
workspace hosts several.

### Step 8 — apply the decision rule (a recommendation)

> **Recommendation only.** The thresholds are placeholders pending OQ-6 (section 5). The
> parent decides.

| Outcome (per measured workflow) | Recommendation |
|---------------------------------|----------------|
| would-hit rate >= 10% of eligible lookups (`would_hits / lookups`), **or** avoidable spend >= $5 per week | Recommend documenting `on` for the measured workflows (authors opt in their pure tasks; never in `ao-bench`). |
| Otherwise | Recommend keeping the feature shipped but off. Record the dominant miss components (Step 6). Re-evaluate with `include_repo_heads: false`, or pursue the explicit `ao run --reuse-from <run-id>` alternative (ADR-0019 ALT-8). |

A tiny `lookups` (a handful) is not evidence either way: extend the window instead of deciding.

## 3. Report template

Copy this section into the G0 report (e.g. `output/<epic>/g0-report.md`) and fill it in.

```markdown
# G0 report — result cache value check

- Date / author / reviewed by:
- Build under test (`ao-beta --version`, commit):
- Stable `ao --version` before / after (must match):

## 1. Method
- Window: <start UTC> to <end UTC> (<DAYS> days); mode: `AO_CACHE=shadow` (env source confirmed by banner)
- Workspace(s) and workflow id(s):
- Operator consent recorded (who, when):
- Deviations from docs-md/result-cache-g0-protocol.md:

## 2. Workflows and tasks (the opt-in decision, one row per opted-in task)
| Workflow | Task id | Opted in? | Why it is pure by inspection (or why not) | Eligible per banner/records? |
|----------|---------|-----------|--------------------------------------------|-------------------------------|

## 3. Metrics per workflow (`ao report-usage --json` -> `result_cache`)
| Workflow | Runs | lookups | would_hits | would-hit rate | misses | ineligible | avoidable_cost_usd | avoidable $/week |
|----------|------|---------|------------|----------------|--------|------------|--------------------|-------------------|

Store growth (`ao cache stats --json`):
| Point | entries | bytes.total | expired_entries | oldest_created_at | newest_created_at |
|-------|---------|-------------|-----------------|-------------------|-------------------|
| start | | | | | |
| end   | | | | | |

## 4. Dominant miss reasons and components
- `miss_reasons`:
- `store_skip_reasons`:
- Components that changed most often (Step 6 output, pasted):
- `cache.skip` reasons (ineligible / not stored):
- Interpretation (which key components limit reuse, and whether they are relaxable):

## 5. Recommendation (decision rule of HLD 22.5; thresholds pending OQ-6)
| Workflow | Rule outcome | Recommendation |
|----------|--------------|----------------|

## 6. Decision (parent)
- Decision: `on for <workflows>` / `keep off` / `pursue ALT-8` / `extend the window`
- By: <parent> · Role: <role> · Date: <YYYY-MM-DD>
```

## 4. Troubleshooting

| Symptom | Cause |
|---------|-------|
| `No such option: --cache`, or no banner | The installed `ao` predates the epic; reinstall the beta flavour (`bash install.sh --flavor beta --force`) |
| `result_cache` is `null` | No scanned run has records: `AO_CACHE` not `shadow` in the process that ran the workflow, no task opted in, or the tasks were all skipped (outputs already existed) |
| `lookups` is 0 but `ineligible` > 0 | Every opted-in task is ineligible; read `cache.skip` in Step 6 |
| `would_hits` stays 0 across repeats | Read Step 6: the key changes every run (HEADs, per-instance outputs, instruction edits) |
| `avoidable_cost_usd` is 0 with would-hits | The source executions reported no cost (e.g. the fake executor). Real agents report cost |
| `ao cache stats` says `exists: false` | Nothing was stored yet (first run, or the cache dir is under another workspace) |

## 5. OQ-6 note

The thresholds in Step 8 (10% would-hit rate, $5 per week per workflow), the owner of G0 and its
timing are **OQ-6 in HLD §23.2, for the parent to confirm**. They are placeholders taken from
HLD §22.5, not tuned to any workload. Until the parent confirms or replaces them, the table is a
recommendation, the report's section 6 records the parent's actual decision, and `on` must not be
documented as recommended on the strength of this protocol alone.
