# Bench probe DSL and generated-instance contract

- Companion to [`benchmark-discriminating-suites-hld.md`](benchmark-discriminating-suites-hld.md) §4.1 / §5.2 / §8.
- Consumers: `bench/blackbox.py` (evaluates probes), `bench/gen/*` (emits them), `bench/sandbox.py` (runs
  the artefact the probes talk to). This file is the **interface contract** between those modules; change it
  before changing any of them.

## 1. Directory contract (what the provider writes where)

```
<ws>/repo/                      # agent-visible
  SPEC.md                       # the generated specification (prose + endpoint/op tables)
  INSTRUCTION.md                # copied by materialize_workspace: "implement SPEC.md; run contract below"
  run.sh                        # MUST start the artefact (http: serve on $PORT; jsonl: stdin->stdout; cli: argv)
  <language skeleton>           # pyproject.toml | package.json(+tsconfig) | go.mod — allow-listed deps only
<grading_dir>/                  # grader-owned, never under <ws>
  instance.json                 # the generated instance (seed, family, difficulty, language, entities, traps…)
  probes.json                   # this DSL
  reference/                    # Python stdlib reference implementation of the same instance (+ run.sh)
```

`run.sh` is executed **inside the sandbox** with cwd = the artefact dir, `PORT` set for `http`, no network
except the published port, and the suite `stack` allow-list pre-installed in the image. It must be ready within
`startup_timeout_seconds` (probes.json, default 30): for `http` readiness = `GET /healthz` → 200 (every spec
requires `/healthz`); for `jsonl` readiness = the process is alive and answers `{"op":"ping"}` with
`{"ok":true}`; for `cli` there is no long-lived process.

## 2. probes.json

```jsonc
{
  "version": "1.0",
  "contract": "http" | "jsonl" | "cli",
  "startup_timeout_seconds": 30,
  "groups": {
    "core": [ <probe>, ... ],      // weight 0.50 — ALL must pass for `solved`
    "trap": [ <probe>, ... ],      // weight 0.35
    "perf": <perf-spec>            // weight 0.15 — see §4
  },
  "weights": {"core": 0.5, "trap": 0.35, "perf": 0.15},   // optional; defaults as shown
  "pass_threshold": 0.8                                    // optional; default 0.8
}
```

Score = Σ_group weight × (passed_in_group / total_in_group); `perf` contributes weight × fraction of perf checks
passed. `solved = all(core) and score ≥ pass_threshold`.

## 3. Probe

```jsonc
{
  "id": "list-pagination-stable-under-insert",
  "trap": "pagination_stability",          // optional; trap-catalogue key (trap group only)
  "fresh_state": true,                     // default true: grader restarts the artefact before this probe
  "steps": [ <step>, ... ],
  "parallel": [ [ <step>, ... ], [ <step>, ... ] ]   // optional: each inner list runs in its own thread,
                                                     // all lists concurrently, AFTER `steps`; then `after`
  "after": [ <step>, ... ]                            // optional: assertions after the parallel phase
}
```

A probe passes iff every step's `expect` holds (steps in `parallel` lists included). Steps execute in order
within a list; a failing step stops its list. Variables captured by `save` are visible to later steps in the
same probe (parallel lists see variables saved by `steps`; `after` sees variables from `steps` only — parallel
captures are not merged, by design).

### 3.1 Step

```jsonc
{
  "op": <op>,                 // contract-specific request, see §3.2
  "expect": <expect>,         // optional; see §3.3
  "save": {"item_id": "$.id", "cursor": "$.next_cursor"}   // optional: JSONPath-lite into response body
  "repeat": 25,               // optional: run this step N times (template vars `{{i}}` = 0..N-1)
  "sleep_ms": 0               // optional
}
```

Templating: any string value in `op`/`expect` may contain `{{var}}` (saved variable), `{{i}}` (repeat index),
`{{rand}}` (8 hex chars, fresh per evaluation), `{{now_iso}}`. Unknown variables are a probe-definition error
(the grader raises `GraderError` — never a silent miss).

### 3.2 `op` per contract

| contract | op shape |
|---|---|
| `http` | `{"method": "GET\|POST\|PUT\|PATCH\|DELETE", "path": "/items?limit=10&cursor={{cursor}}", "json": {...} \| null, "headers": {...}}` |
| `jsonl` | `{"json": {"op": "create", "entity": "item", "body": {...}}}` — one request line (`json.dumps` of the value; any JSON value, so a non-object line such as `[1, 2]` can be probed); response = next line |
| `cli` | `{"args": ["add", "--name", "{{rand}}"], "stdin": "..."}` |

### 3.3 `expect`

```jsonc
{
  "status": 201,                      // http only; int or [200, 201]
  "exit_code": 0,                     // cli only
  "json": <matcher>,                  // body (http/jsonl) or stdout parsed as JSON (cli)
  "stdout_contains": "...",           // cli only
  "max_ms": 500                       // optional per-step latency bound (used sparingly; perf lives in §4)
}
```

### 3.4 Matcher language (`json`)

A matcher is a JSON value compared *structurally* against the actual value:

- object → every key in the matcher must be present and match (extra keys in actual are allowed);
- array → same length and element-wise match, unless a directive object is used;
- scalar → equality;
- directive objects (exactly one `$key`):
  - `{"$any": true}` — present, any value
  - `{"$type": "string"|"number"|"integer"|"boolean"|"array"|"object"|"null"}`
  - `{"$len": 10}` / `{"$len_min": 1}` / `{"$len_max": 10}`
  - `{"$regex": "^[0-9a-f-]{36}$"}`
  - `{"$in": [..]}` / `{"$gte": 0}` / `{"$lte": 100}`
  - `{"$sorted_by": "created_at", "order": "asc"|"desc"}` — array of objects sorted by field (stable tiebreak checked when `"tiebreak": "id"` given)
  - `{"$set_equals": [..]}` — array equal as a multiset
  - `{"$each": <matcher>}` — every array element matches
  - `{"$contains": <matcher>}` — some element matches
  - `{"$var": "item_id"}` — equals a saved variable
  - `{"$not": <matcher>}`

Matchers are evaluated by `bench/blackbox.py::match(expected, actual) -> list[str]` (empty = match; otherwise
human-readable mismatch paths, surfaced in `GradeResult.detail.probe_results[].mismatches`).

## 4. Perf spec (`groups.perf`)

```jsonc
{
  "sizes": [100, 10000, 100000],       // N records loaded before measuring
  "load": <step>,                      // step with `repeat` = N (templated), executed to populate state
  "mix": [                             // weighted op mix, each a step; `weight` sums to 1.0
    {"weight": 0.7, "name": "get",  "class": "indexed_get",  "step": <step>},
    {"weight": 0.2, "name": "create", "class": "write",     "step": <step>},
    {"weight": 0.1, "name": "list", "class": "paginated_list", "step": <step>}
  ],
  "ops_per_size": 500,                 // total ops drawn from the mix per size
  "repeats": 3,                        // median of repeats per size
  "expected_alpha": {"indexed_get": 0.3, "write": 0.3, "paginated_list": 0.3},  // per op class
  "alpha_slack": 0.2,
  "ref_ratio_max": 5.0,                // enforced only when artefact language == reference language
  "checks": ["alpha", "ref_ratio", "timeout"]   // which checks count toward the perf score fraction
}
```

Grader output (`GradeResult.detail.perf`): per size → per op name `{p50_ms, p95_ms}`; per op class `alpha`,
`alpha_pass`; `ref_ratio_p95` (largest N) and `ref_ratio_pass` (or `null` when not enforced); `timed_out: bool`;
`sandbox: "docker"|"process"`; `image`.

## 5. instance.json (generator output; informational for the grader, authoritative for the reference)

```jsonc
{
  "version": "1.0",
  "family": "crud-relational",
  "seed": 4242,
  "difficulty": {"entities": 2, "traps": 2, "concurrency": 1, "scale": 1},
  "language": "python",
  "contract": "http",
  "entities": [ {"name": "project", "fields": [...], "relations": [...]} ],
  "traps": ["pagination_stability", "unique_constraint", "cascade_delete"],
  "limits": {"page_size_max": 50, ...},
  "reference": {"language": "python", "entry": "reference/run.sh"}
}
```

The reference implementation reads `instance.json` at startup (path via `INSTANCE_JSON` env, set by the grader),
so one reference program per family serves every instance of that family.

## 6. Error classes

| Situation | Outcome |
|---|---|
| Artefact never becomes ready, crashes on a probe, or `run.sh` missing | probe(s) fail; `score` reflects it; `solved=false`; **not** infra |
| Reference fails any `core` probe | `GraderError` → task `infra_error` (generator/reference bug) |
| Sandbox cannot start (docker daemon down, image missing and `--no-build`) | `GraderError` → `infra_error` |
| Probe definition invalid (unknown var, bad matcher) | `GraderError` → `infra_error` |
