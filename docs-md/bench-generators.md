# Generated-task families (`bench/gen`) — as built

- Companion to [`benchmark-discriminating-suites-hld.md`](benchmark-discriminating-suites-hld.md) §4.1/§7/§8 and
  [`bench-probe-dsl.md`](bench-probe-dsl.md) (the output contract; unchanged by this work).
- Ticket: `meta/tickets/E-Bn7Dsc-discriminating-benchmark-suites/T-Gn3Ef4-generators/`.

## 1. Pieces

| Module | Role |
|---|---|
| `bench/gen/base.py` | `Difficulty` (entities/traps/concurrency/scale, each 0–3, strict parse), `Instance` (DSL §5 fields + `spec_md`, `skeleton`, `probes`, `reference` file dict), `Family` ABC, `FAMILY_REGISTRY` / `register_family`. |
| `bench/gen/traps.py` | Trap catalogue (key → families, `requires`) and seeded sampling; count by level `(1, 2, 4, 6)` (+ any required trap); for one seed a higher level is a superset of a lower one. |
| `bench/gen/wordlists.py` | Entity / field / enum name pools. |
| `bench/gen/skeleton.py` | Per-language `run.sh` + manifest + stub (python / typescript / go), run-contract and allow-list prose for `SPEC.md`. |
| `bench/gen/dsl.py`, `validate.py` | `probes.json` builders and a structural validator (shapes, matcher directives, JSONPath-lite, `{{var}}`/`$var` scoping, perf invariants). |
| `bench/gen/crud_relational.py`, `job_queue.py` | The two P1 families. |
| `bench/gen/reference/{crud,queue}_server.py` | Python-stdlib reference programs, read verbatim into `Instance.reference` (linted + type-checked like any module). |
| `bench/gen/materialize.py` | `generate_instance` (validated) and `write_instance` (agent files → `repo_dir`, grader files → `grading_dir`). |
| `bench/generated_provider.py` | `source.type: generated` workspace provider. |
| `bench/gen/cli.py` | `ao-bench gen`. |

Generation is a pure function of `(family, seed, difficulty, language, allow)`: one `random.Random(seed)`, no clock, no
`set` iteration in emitted data; byte-identical across runs and `PYTHONHASHSEED`s (tested). Hidden assets are therefore
never stored in a suite — they are regenerated into the grader-owned dir at materialise time.

## 2. Suite shape (what `ao-bench gen` emits)

```jsonc
{ "id": "gen-services-mini", "tier": "medium", "parity": "../../parity/default",
  "stack": { "python": { "deps": ["montydb"] } },               // third-party only; what the sandbox installs
  "tasks": [ {
    "id": "crud-relational-py-s4242", "category": "feature",
    "instruction": "tasks/_generated/instruction-http.md",       // generic per contract, no task content
    "source": { "type": "generated", "family": "crud-relational", "seed": 4242,
                "difficulty": {"entities": 2, "traps": 2, "concurrency": 1, "scale": 1},
                "language": "python", "deps": ["montydb"] },
    "grader": { "type": "blackbox", "sandbox": { "stack": { "python": { "deps": ["montydb"] } } } },
    "family": "...", "language": "python", "difficulty": {...}, "tags": [...] } ] }
```

`stack.<language>.deps` defaults: python `["montydb"]`, typescript `[]`, go `["modernc.org/sqlite"]`. The allow-list stated
in `SPEC.md` and the manifest is the language built-ins (`sqlite3`, `node:sqlite`) plus `deps`. The grader gets its own
copy of `stack` because the runner does not pass it. Instance `i` of a family uses seed `BASE+i`; languages are assigned
round-robin over the task sequence.

## 3. Files written per task

`repo_dir`: `SPEC.md`, `run.sh` (0755), manifest (`pyproject.toml` / `package.json`+`tsconfig.json` / `go.mod`) and a stub
entry (`app/__main__.py` / `app.js` / `main.go`). TypeScript runs `node app.js` (Node 22 ESM, built-ins only): write plain
JS or compile TS to `app.js`.
`grading_dir`: `instance.json`, `probes.json`, `reference/{run.sh, server.py, instance.json}`. The reference reads
`$INSTANCE_JSON`, else the sibling `instance.json`, so it also works when FakeSubject `copy-reference` turns it into the
artefact. http artefacts bind `$HOST:$PORT`.

## 4. Families

**crud-relational** (http). `entities` → 2/3/4/6 entities (1:N tree with cascade/restrict; one M:N between non-adjacent
entities from 3 up); every entity has a required label, a required enum and optional typed fields (the root has all
types). Client-supplied `id` is optional (`[A-Za-z0-9_-]{1,64}`, 409 on reuse) so perf records are addressable.
Traps (spec headings in `**bold**`): pagination_stability (keyset cursor survives deleting the cursor row, asc and desc),
unique_constraint (+ concurrent race), unicode_casefold (NFKC∘casefold∘NFKC key; requires unique), cascade_delete
(all-or-nothing across the cascade closure), patch_null_vs_absent, idempotency_key (+ concurrent), lost_update
(`If-Match` + `version`), invalid_input_4xx, sort_tiebreak.

**job-queue** (jsonl). `entities` → 2/3/4/5 priority levels (direction drawn per seed). Ops: ping, enqueue, claim, ack,
nack, stats, advance_clock; time is a virtual clock only `advance_clock` moves. Core: priority + FIFO order, receipts,
retries/dead, stats. Traps: visibility_timeout (inclusive lease expiry, stale receipts, per-claim override),
at_least_once (requires visibility_timeout; expiry burns an attempt, last attempt → dead), duplicate_claim,
starvation (every k-th claim returns the oldest queued job), dedupe_key, invalid_input_4xx.

**Difficulty**: `traps` = number sampled (see above); `concurrency` = parallel workers in the race probes (0/4/8/16; 0 = no
`parallel` phase); `scale` = perf sizes `[100,500] / [100,1000] / [100,10000] / [100,10000,100000]`.

## 5. Interpretations of the DSL (confirm in the integration pass)

- `perf.load.repeat` is a placeholder (set to `sizes[0]`); the grader substitutes each N.
- Perf mix steps use no saved variables: records are addressed with `{{i}}` (seeded random index in `[0, N)`; load stores
  `perf-{{i}}`), jobs with ids that do not exist. `{{N}}` is allowed but unused.
- `$sorted_by … tiebreak` is only asserted for `order: asc` (the DSL does not define the id direction under `desc`).
- Probes that inspect whole collections use `fresh_state: true`; independent CRUD probes use `false`.
- job-queue probes other than the starvation and duplicate-claim probes make at most 3 successful claims in a row, so
  they never meet the fair-share claim (period 4–8) by accident.

## 6. Testing

`tests/bench/dsl_runner.py` is a *test-only* evaluator of the DSL (not the grader). With it the unit tests prove: every
instance's reference passes every core/trap probe and a perf smoke (in-process and through the real `run.sh`); a set of
naive "mutant" references (offset cursors, missing unique check, `lower()` casefold, restrict-as-cascade, null-as-absent,
no idempotency, ignored `If-Match`, bool-as-int, non-inclusive lease expiry, …) fail the probe of the trap they get
wrong while passing the core. The full "reference passes all its probes **under the blackbox grader**" check belongs to the
integration pass after both tracks merge.
