# STATUS

- ID: `T-Bx2Cd3-blackbox-sandbox`
- Updated At: 2026-10-23
- State: **Done** (branch `ad/bench-blackbox`, awaiting review/merge)

## This update
- By: Claude · Role: developer · Date: 2026-10-23 · Comment: implemented `bench/sandbox.py` (docker +
  process sandboxes, `ao-bench images`), `bench/blackbox.py` (matcher, probe executor, perf, grader) per
  `docs-md/bench-probe-dsl.md`, a hand-written stdlib fixture under `tests/bench/data/blackbox/`, and
  fake-tier tests. `ruff format/check`, `mypy src/agent_orchestrator/bench` clean; `pytest tests/bench`:
  431 passed / 4 skipped before -> 696 passed / 11 skipped after (7 new skips are the `docker`-marked tests);
  with `AO_E2E_DOCKER=1 pytest -m docker tests/bench`: 7 passed against python:3.12-slim (already local).
  Epic `EPIC.md`/`STATUS.md` rollup not touched (shared with parallel tracks; manager syncs on merge).

## Interface for the other tracks (generators, runner)

`grader` cfg (`GraderConfig` common fields + `model_extra`):

| key | meaning |
|---|---|
| `type: "blackbox"` | |
| `assets` | dir copied into `grading_dir` by `materialize_workspace` (probes, instance, reference) |
| `probes` | file name inside `grading_dir` (default `probes.json`; may not escape it) |
| `sandbox` | `SandboxConfig`, see below; unknown keys -> `GraderError` |
| `limits` | `{probe_timeout_seconds: 60, request_timeout_seconds: 10, perf_timeout_seconds: cfg.timeout_seconds or 300}` |
| `weights`, `pass_threshold` | override probes.json (cfg wins); defaults 0.5/0.35/0.15 and 0.8 |
| `contract`, `language` | optional; probes.json / instance.json win (a `contract` that disagrees -> `GraderError`) |

`grader.sandbox` (`SandboxConfig`, `extra="forbid"`):
`type` `"auto"|"docker"|"process"` (default `auto` = docker if `docker info` works else process) ·
`language` (instance.json `language` wins) · `image` (full override, used as-is, no derived image) ·
`cpus` 1.0 · `memory` `"1g"` · `pids` 256 · `startup_timeout_seconds` (default = probes.json value, else 30) ·
`build_missing` true (false + image absent -> `GraderError`, "run `ao-bench images --build`") ·
`stack`.

`stack` block shape (the suite-level `BenchSuite.stack` has the same shape; the runner does not pass it to
graders, so the generator must embed a copy at `grader.sandbox.stack`):
`{"python": {"image": "python:3.12-slim", "deps": ["montydb==2.5.2"]}, "typescript": {"deps": ["better-sqlite3@11.3.0"]}, "go": {"deps": ["modernc.org/sqlite@v1.33.1"]}}`.
Both keys optional per language; unknown sub-keys ignored. Default images: python `python:3.12-slim`,
typescript `node:22-alpine`, go `golang:1.23-alpine`. Non-empty `deps` -> derived image from a generated
Dockerfile (`base + pip install | npm install | go get`), tag `ao-bench-<lang>-<sha256(dockerfile)[:12]>`.
`ao-bench images --list|--build [--suite suite.json] [-l python -l go]`.

`grading_dir` contract: `probes.json`, `reference/` (its own `run.sh` at the root), optional `instance.json`
(`language`, `seed`, `reference.language` default python). Per run the grader snapshots the dir into
`<grading_dir>/blackbox-*/run-*` (removed afterwards). Environment:
- reference: `INSTANCE_JSON` (the grader's copy, placed at `<run dir>/.ao-instance.json`; translated to
  `/app/...` in docker), `PORT`, `HOST`, `PYTHONUNBUFFERED=1`;
- artefact: `PORT`, `HOST`, `PYTHONUNBUFFERED=1` only. Never the instance (test:
  `TestHiddenAssetsStayHidden`). `HOST` is `0.0.0.0` in docker (required for the published port) and
  `127.0.0.1` in process mode: **SPEC.md must tell the agent to bind `$HOST:$PORT`**, answer `/healthz`
  (http) or `{"op":"ping"}` -> `{"ok":true}` (jsonl), and flush stdout.
- the reference must ALSO run standalone, because FakeSubject `copy-reference` turns it into the artefact
  (which gets no `INSTANCE_JSON`): ship a sibling `reference/instance.json` and fall back to it
  (see `tests/bench/data/blackbox/reference/server.py`).

`GradeResult.detail`: `contract`, `language`, `sandbox`, `image`, `reference_ok`, `reference_language`,
`reference_trap_failed` (ids; informational), `core_all_passed`, `pass_threshold`,
`score_breakdown{core,trap,perf: {weight, effective_weight, passed, total, fraction}}`,
`probe_results[{id, group, trap, passed, mismatches, ms}]`, `artefact_error` (only when the artefact never
ran), `perf` (null without a perf group) =
`{sizes: {"<N>": {<op name>: {p50_ms, p95_ms}}}, classes: {<class>: {alpha, expected_alpha, alpha_pass}},
ref_ratio_p95, ref_ratio_max, ref_ratio_enforced, ref_ratio_pass (null when not enforced), p95_ms_largest,
ref_p95_ms_largest, timed_out, failed_ops, error, sandbox, image}`. `raw_tail` = one `FAIL group:id: first
mismatch` line per failing probe.

## DSL interpretations and extensions (the DSL doc is unchanged, per instruction)
1. `{{var}}` that is the whole string and names a saved variable keeps its JSON type (saved int id -> int in
   a body); embedded it is stringified. `{{i}}`, `{{rand}}` (8 hex, seeded), `{{now_iso}}` (UTC, ms) are
   always strings. `save` into `i/rand/now_iso` is rejected.
2. Perf mix: `{{i}}` is a seeded pseudo-random index in `[0, N)` so `GET /things/{{i}}` hits loaded keys;
   `{{N}}` = current size; the `load` step's `repeat` is overridden with N. Mix ops that fail their `expect`
   are excluded from percentiles and counted in `failed_ops` (a fast error must not improve p95); a class
   with no samples has `alpha: null` -> its check fails. Reference perf must yield samples for every class
   else `GraderError`.
3. Perf checks counted toward the perf fraction: one per `expected_alpha` class (if `alpha` in `checks`),
   one `ref_ratio` (only when enforced = artefact language == reference language; the ratio is still
   computed and reported when `ref_ratio` is in `checks`), one `timeout`. `ref_ratio_p95` = pooled p95 of
   the whole mix at the largest N, artefact / reference. A load failure / crash / timeout = perf fraction 0,
   `timed_out` tagged, never infra. Optional `perf.timeout_seconds` overrides the perf budget.
4. Score weights are renormalised over groups that have something to count (no `perf` group -> core+trap
   carry 1.0). `solved = all core pass AND score >= threshold`.
5. Matchers: `{"$not": {"$any": true}}` asserts a key is absent (null counts as present); `$gte/$lte` also
   compare ISO strings; `$sorted_by` accepts dotted fields; `$type: "integer"` accepts `5.0`.
6. Static validation up front: unknown keys in probe/step/op/expect/perf, `trap` outside the trap group,
   op/expect keys that do not fit the contract, unknown `{{var}}`/`$var` (scoping per DSL §3: `after` and
   parallel lists see `steps` saves; parallel saves are local) are all `GraderError`. `description` is
   accepted as an annotation key on probes/steps. If `steps` or `parallel` fail, `after` is skipped.
7. `jsonl` is driven interactively (`Sandbox.start_jsonl` -> `handle.request`), because a step may use a
   variable saved from the previous response; `Sandbox.run_jsonl(lines)` is the batch convenience on top.
8. `sleep_ms` sleeps before each execution of the step.

## Deviations from the task text
- Docker http artefacts run as a foreground `docker run --rm` through Popen, not `docker run --rm -d`: with
  `-d` a container that crashed at boot was removed before its logs could be read (lost in 8/10 trials;
  10/10 captured now), so a failing artefact could not explain itself. Stop = `docker rm -f`.
- Extra container flags: `--user <host uid:gid>` (files stay removable), `--cap-drop ALL`,
  `no-new-privileges`, `--memory-swap = --memory`.
- Process sandbox: python uses `RLIMIT_AS`; typescript/go use `RLIMIT_DATA` (V8/Go reserve address space).
  `RLIMIT_NPROC` = (system-wide task count + `pids`) because it counts every task of the uid.
  Host env is scrubbed to PATH/LANG/LC_ALL/TZ/TMPDIR (+HOME=run dir).
- `fresh_state` is implemented as a fresh *copy* of the artefact dir per probe (state files do not leak;
  the agent's repo is never mutated; `.git` and tool caches are not copied).

## Open issues / not verified
- Derived images for typescript/go and their runtime were not built or run live (no network use beyond the
  already-present python image): Dockerfiles were only syntax-checked with `docker build --check`;
  `better-sqlite3` on alpine may need a build toolchain; Go runs with `GOPROXY=off` against the module cache.
- Process fallback uses the host toolchain, no pre-installed deps: ts/go artefacts needing deps only work
  there if the deps are inside the artefact dir.
- Per-probe snapshot copies the artefact dir including `.venv`/`node_modules` (slow for big dirs).
- HTTP client opens one connection per request (robust, thread-safe); docker adds docker-proxy latency to
  absolute ms (ratios against the reference are taken in the same sandbox, so it cancels).
- Rootless docker / SELinux mount labels untested. `docker_available()` is cached per process.
- Perf thresholds in the fixture are deliberately generous; real families need calibration (P1).
