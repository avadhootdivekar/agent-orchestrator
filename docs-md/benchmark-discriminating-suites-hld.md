# HLD — Discriminating benchmark suites (behavioural-spec + large-codebase tiers)

- Status: **IMPLEMENTED (P1–P3 as-built, 2026-10-23)** — review decisions in §2b/§4.1/§9; as-built deviations and what is still open in §12. Tracked in `meta/tickets/E-Bn7Dsc-discriminating-benchmark-suites/`.
- Builds on: [`benchmarking-framework-hld.md`](benchmarking-framework-hld.md) (harness, as-built),
  [ADR-0008](adr/ADR-0008-benchmark-harness-approach.md), [ADR-0009](adr/ADR-0009-benchmark-tiers-external-suites-budgets-parallelism.md),
  `benchmarks/README.md`.
- Owner input: the three benchmark goals are **speed (time), cost, correctness**, with the constraint that a single
  developer can afford to run the comparison repeatedly on a local machine.

---

## 1. Problem statement (why the current suites don't answer the question)

Evidence from the committed results (`benchmarks/results/2026-07-22-*`, single sample per cell):

| Suite | Tasks | Solve rate, every subject | What it still measures |
|---|---|---|---|
| `dev-core` (small) | 6 | 6/6 for haiku, sonnet, opus, ao-epic-haiku, ao-epic-sonnet | cost + wall-clock only |
| `dev-medium` (medium) | 6 | 6/6 for sonnet, opus, ao-epic-sonnet, ao-epic-plus-sonnet | cost + wall-clock only |
| `swe-verified-mini` (large) | 10 | 9/10 for sonnet, opus, ao-epic-sonnet | one task = 10 pp; inside noise |

Four defects, in order of damage:

1. **Saturation.** Easy suites pass for everything; the only surviving signal is "cheaper model is cheaper".
2. **No resolution at the hard end.** n=10, r=1: a 10 pp difference is one task. The two failures that differ
   (`pytest-10356`, `django-11138`) swap between subjects, and `ao`'s `django-11138` failure cost $0.09 — an
   infrastructure abort, not a capability signal. The harness cannot tell these apart from real misses.
3. **Cost/time of the hard tier scales badly.** ~$8–10 and ~30 min per subject for 10 SWE-bench instances with
   1 GB Docker images. Full SWE-bench Verified (500) or repeats ×3 across 4 subjects is out of a developer's budget.
4. **Contamination + visibility.** SWE-bench Verified instances are in model training data. `dev-medium` keeps
   its "held-out" `.grading/` *inside* the fixture the agent can read.

Secondary: no variance estimate (r=1 everywhere), wall-clock mixes API latency with tool time, `ao` subjects'
parallelism makes wall-clock vs summed agent time ambiguous, and correctness is binary per task (no partial credit
for "core works, pagination breaks under concurrent insert").

**The owner's reading is correct**, with one refinement: the problem is not "hard suites are too expensive" in
absolute terms — it is that *public* hard suites are expensive *per bit of information*. The fix is tasks that
are cheap to grade, costly for the agent to get fully right, and whose difficulty we control.

## 2. Goals / non-goals

**Goals**
- **G1 Discrimination.** Every task in a live suite has a pooled pass rate in the 20–80 % band across current
  subjects; saturated/impossible tasks are retired automatically.
- **G2 Three metrics, separable.** Correctness (graded score ∈ [0,1] + solved bool), agent time (wall, with API vs
  tool split where available), cost (USD; per solved; per score point). Plus **efficiency of the produced code**
  (latency and scaling of the artefact), which is a correctness dimension the current harness lacks entirely.
- **G3 Developer-affordable.** A full comparison (4 subjects × live suites × repeats where needed) ≤ ~$80 and
  ≤ one evening of wall-clock on one machine. Grading is seconds per task; no mandatory Docker.
- **G4 Bias control.** No single problem shape dominates; instances are seeded/generated; hidden assets never
  touch the agent-visible workspace; public-repo tasks are post-training-cutoff or synthetic.
- **G5 Two task kinds.** Greenfield (spec → service) *and* brownfield (bugfix/enhancement inside a large existing
  codebase), because greenfield alone is low-complexity and brownfield alone is what every public benchmark is.
- **G6 Statistical honesty.** Paired comparisons, repeats only where they buy information, confidence intervals
  in the report, infra failures excluded from capability stats and reported separately.

**Non-goals (this epic)**
- LLM-as-judge grading of code quality (behaviour + tests only; diff size is reported, never scored).
- Non-dev domains. Polyglot from day one (Python first; language is a suite parameter, see §9 Q1).
- Replacing `dev-core` (keep as the cheap smoke suite) or the SWE-bench provider (keep; reuse for Tier 2a).
- A leaderboard/dashboard. Results stay git-committed `run.json` + markdown, as today.

## 2b. Subject parity (apples-to-apples) — review outcome, hard requirement

The primary purpose of the harness is to evaluate **`ao` itself**. An `ao` subject carries its own agent
instructions, skills/commands, per-agent effort/model settings and limits (max turns, autocompact thresholds,
permission mode). If the bare `claude -p` subjects do not receive the *same* instructions, skills and limits, a
measured difference says nothing about orchestration — it may only be "ao had better instructions".

**Rule:** every subject in a campaign runs under one shared **parity bundle**, declared once in the suite (or
overridden per campaign with `--parity <file>`), and the bundle's fingerprint is recorded in every `run.json`.
`build_comparison` refuses to compare runs whose `parity_fingerprint` differs (override: `--allow-mixed`, which
also stamps the report as *non-parity*).

```jsonc
// benchmarks/parity/default/parity.json
{
  "version": "1.0",
  "claude_dir": "claude",          // dir copied to <repo_dir>/.claude  (skills/, commands/, settings.json)
  "claude_md": "CLAUDE.md",        // copied to <repo_dir>/CLAUDE.md  — same operating instructions for all
  "env": {                         // applied to EVERY subject's subprocess environment
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "16000"
    // autocompact / effort knobs as the Claude Code CLI exposes them; values are opaque to the harness
  },
  "max_turns": 30,                 // applied to every subject unless the CLI overrides it for ALL subjects
  "permission_mode": "bypassPermissions"
}
```

What parity **does** equalise: operating instructions (`CLAUDE.md`), skills/commands/settings (`.claude/`),
environment knobs, turn limit, permission mode. What it **does not**: the model (that is often the variable under
test) and the subject's own one-line prompt wrapper (`prompt_template` for `claude_cli`, `agents.json` prompts for
`ao`) — those are deliberately thin and must only say "read `INSTRUCTION.md` / `CLAUDE.md` and do the task".
A lint in `ao-bench validate --parity` flags a subject whose wrapper prompt exceeds a length budget or contains
task-domain words, so instruction content cannot leak back into the wrapper.

Mechanics: `materialize_workspace` copies the bundle into `repo_dir` *after* the provider populates it; when the
repo is a git checkout (Tier 2) the copied paths are added to `.git/info/exclude` so they never appear in the
agent's diff or in collateral-diff statistics. Both Claude Code (`claude -p`, cwd = `repo_dir`) and every `ao`
agent (`working_dir: repo`) therefore read identical `CLAUDE.md` + `.claude/` from the same place.

### 2c. Environment isolation (`T-Ws7Iso`, as built 2026-10-24)

Parity controls what the harness *ships*; isolation makes sure the environment *adds nothing*. Claude Code loads
context the harness never wrote: `CLAUDE.md`/`CLAUDE.local.md`/`AGENTS.md`/`.claude/` from every ancestor of the
agent's cwd, user-level config (`~/.claude`), account-level claude.ai connectors and auto-memory. Four mechanisms
(`bench/envcheck.py`), all recorded in `run.json`:

1. **Root outside any instruction tree.** Workspaces live under `${AO_BENCH_WORKSPACE_ROOT:-/var/tmp/ao-bench/<uid>}/ws`
   and grader-owned assets under the sibling `<base>/_ao_bench_grading` (never under the workspace root).
   `run_suite` refuses an agent subject (`claude_cli`, `ao_workflow`) when the workspace root or any ancestor holds
   one of those markers; `--allow-contaminated-env` runs anyway and stamps `env_contaminated=true`. The user config
   dir is exempt from the ancestor check (it loads regardless of cwd; item 4 records it). `ao-bench env [--probe]`
   is the preflight (exit 2 when contaminated).
2. **Environment fingerprint.** Every transcript's `system/init` event (`skills`, `agents`, `slash_commands`,
   `mcp_servers` by name, `plugins` by source) is parsed after the subject runs — `capture/transcript.jsonl` for
   `claude -p`, every `.orchestrator/runs/*/*/attempt-*/transcript.jsonl` for `ao` — and stored per task as the
   union (`divergent` when sessions disagree). The *expected set* is measured, not hard-coded: once per run a
   **baseline probe** starts `claude -p` in an empty parity-applied workspace under the same root, reads its init
   event and kills the process group (≈1 s; Claude Code built-ins + user-level config + the bundle). Entries a task
   saw beyond the baseline are `env_foreign`.
3. **Hidden-asset audit.** A `tool_use` whose input contains `_ao_bench_grading` marks the task
   `suspect=true, suspect_reason="grading_access"`; `tool_result` mentions are counted as exposure only. Suspect
   tasks are not capability evidence: `results.is_attempted` is false for them, so they leave cells, rates,
   roll-ups, pairwise tests and calibration pooling (they are listed, never silently dropped).
4. **User-level config — decision: record, do not relocate `CLAUDE_CONFIG_DIR`.** A bench-owned config dir needs
   the operator's OAuth credentials copied or linked; a copy forks the refresh token (whichever copy refreshes
   first can invalidate the other, logging the operator out or failing a campaign midway) and a link is replaced
   on rewrite. Instead `user_config_fingerprint` records the user `settings.json` keys that change behaviour
   (`model`, `effortLevel`, `alwaysThinkingEnabled`, `outputStyle`, `autoMemoryEnabled`,
   `disableClaudeAiConnectors` verbatim; `enabledPlugins`/`hooks`/`env`/`permissions` as sha256), the user
   `CLAUDE.md` hash and the `ANTHROPIC_*`/`CLAUDE_*` env (secret-looking names redacted). User skills and plugins are
   already visible in the init event. The default bundle neutralises the two account-level leaks through env:
   `ENABLE_CLAUDEAI_MCP_SERVERS=false` (connectors such as Google Drive loaded asynchronously — present for some
   agents and not others in the 2026-10-23 run) and `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`. Subjects also get the
   operator's environment minus Claude Code's *session* variables (`CLAUDECODE`, `CLAUDE_CODE_CHILD_SESSION`,
   `CLAUDE_CODE_SESSION_*`, messaging/bridge sockets, `CLAUDE_EFFORT`, …) so an `ao-bench` launched from inside a
   Claude Code session does not make every agent a child of it (`envcheck.subject_env`, shared by both subjects and
   the probe).

`build_comparison` refuses (`EnvMismatchError`; `--allow-mixed` stamps `env_mixed` + `env_problems`, rendered as a
**NON-ISOLATED** banner): an `env_contaminated` run; any foreign entry; agent subjects whose init-event, baseline
or user-config digests differ; a pre-`T-Ws7Iso` agent run mixed with fingerprinted ones (legacy-only reports still
render, marked "not fingerprinted"). A campaign that hits this after stage 1 stops its repeat rounds
(`stage_stop_reason="environment_mismatch"`) and still writes the stamped comparison.

Limits: names cannot tell a repo skill from a same-named built-in (`workflow-authoring` exists in both) — item 1
is what rules that out; the audit matches the literal grading dir name, so a glob such as `../../_ao*` evades it
(exposure through `ls` output is still counted); and model-side contamination (the public trap catalogue in future
training data) is not fixable by layout — fresh seeds and hidden probes are the defence (§7).

## 3. Design principles (inherited + added)

Inherited from the harness HLD: subjects are subprocesses; workspaces repo-local, gitignored; declarative JSON
specs validated by schema; payloads by path; deterministic *harness*, stochastic *solutions*; `bench/` never enters
the engine import graph.

Added:
- **Grade behaviour, never source.** Graders talk to the artefact over a fixed run contract (HTTP/JSONL/CLI).
  Implementation freedom is the agent's; what we score is what the thing does.
- **Hidden assets are grader-owned.** Probes, reference implementation, injected-bug location, excised tests live
  under the result dir / provider cache, never under `repo_dir`.
- **Difficulty is a dial, not a fixed property.** Each generated task has a difficulty vector we can turn until it
  sits in the discriminating band.
- **Relative, not absolute, for performance.** Efficiency scores are ratios against a hidden reference
  implementation run on the same machine in the same session, so a slower laptop doesn't change rankings.

## 4. Task tiers

### 4.1 Tier 1 — behavioural-spec tasks (greenfield, generated)

The agent receives: a prose spec, a run contract, an empty project skeleton (`run.sh`, `pyproject.toml` with
allowed deps), and nothing else. It must build the thing. The grader never looks at the code.

**Run contracts** (per task, declared in the suite):

| Contract | Agent must provide | Grader drives it via |
|---|---|---|
| `http` | `./run.sh` serves JSON over HTTP on `$PORT` until SIGTERM | HTTP client probes |
| `jsonl` | `./run.sh` reads one JSON request per stdin line, writes one JSON response per stdout line | piped process |
| `cli` | `./run.sh <args>` with documented exit codes/stdout | subprocess |

**Stack (review outcome).** Tier 1 is polyglot from the start: `language ∈ {python, typescript, go}` is a task
parameter; a suite draws instances across languages so no subject is scored on one runtime only. Allowed
persistence per language (configured in the suite's `stack` block, not hard-coded): embedded SQL via
`sqlite3` (Python stdlib / `better-sqlite3` or `node:sqlite` / `modernc.org/sqlite`) and an embedded
Mongo-compatible document store where a lightweight one exists (`montydb` for Python; TS/Go fall back to SQLite
or in-memory + file persistence). No external services (Postgres/Redis/Mongo daemons) — they are neither light
nor reproducible on a developer machine.

**Sandbox (review outcome): light Docker.** The agent develops on the host as today (subjects are host
subprocesses, C1), but the *artefact is graded inside a per-language Docker image* with the allow-listed
dependencies pre-installed, CPU/memory/pid limits, and no network except the published port. This gives every
subject the identical runtime, avoids host contamination and version conflicts, and makes "runs in the image with
the declared deps" part of the contract. Images are derived once per suite from a generated Dockerfile
(`base image + allow-list`) and cached by content hash (`ao-bench images --build`). A `process` sandbox (plain
subprocess + `ulimit`) remains as an explicit, tagged fallback for machines without Docker and for CI unit tests.

**Families** (each a generator module `bench/gen/<family>.py`; seed → concrete spec instance + hidden reference
implementation + probe set):

| Family | Shape | Intrinsic traps |
|---|---|---|
| `crud-relational` | 3–6 entities with 1:N / M:N, list/get/create/patch/delete, filters, cursor pagination | pagination stability under concurrent insert/delete, cascade vs restrict, unique constraints, PATCH null-vs-absent, idempotency keys, lost update, N+1 |
| `job-queue` | enqueue/claim/ack/nack with priorities, retries, dedupe keys, visibility timeout | at-least-once semantics, starvation, clock injection, duplicate claim under concurrency |
| `ledger-inventory` | accounts/stock with transfers/reservations and invariants | non-negative balance under concurrent reservation, money precision (cents, no floats), audit replay equals state |
| `rate-limiter` | multi-tenant sliding/fixed window quotas, burst, injected clock | window edge, tenant isolation, clock monotonicity, memory growth with tenants |
| `search-index` | document add/update/remove, tokenised query, prefix, ranking, incremental update | unicode case-folding/normalisation, tie-break stability, update-after-delete, scaling of query vs corpus |
| `expr-evaluator` | small expression/filter language: parse, evaluate, report errors with positions | precedence, associativity, deep nesting (recursion limit), error positions, large inputs |
| `file-sync` | CLI: compare two trees, plan/apply sync, rename detection, dry-run | symlinks, empty files, same-content-different-name, idempotent apply, large tree scaling |
| `event-state-machine` | append events, replay to state, snapshots, idempotent handlers | out-of-order delivery, duplicate events, snapshot/replay equivalence, replay time vs log size |

**Instance generation** (`seed`, `difficulty` → instance): entity/field/relationship names and counts are drawn
from word lists; the trap set is sampled from the family's catalogue with size controlled by difficulty;
numeric limits (page size, N for scaling, quota numbers) are randomised. Names are rotated so memorised
solutions ("the todo app") don't transfer. The reference implementation is parameterised by the same instance, so
it always matches the spec; its own probe pass is asserted at generation time (a task that the reference fails is
a generator bug and is rejected before any agent sees it).

**Difficulty vector** (`difficulty: {entities, traps, concurrency, scale}`), each 0–3. Calibration (§7) moves a
family's instances along this vector until pooled pass rate lands in band.

**Probe set** per instance, three groups with fixed weights:

| Group | Weight | Content | Counts toward `solved`? |
|---|---|---|---|
| `core` | 0.5 | happy-path functional probes | yes — all must pass |
| `trap` | 0.35 | one probe per sampled trap | no (score only) |
| `perf` | 0.15 | latency + scaling probes (§5.2) | no (score only) |

`solved = all core pass AND score ≥ pass_threshold` (default 0.8). The score is what rankings use; `solved` keeps
backward-compat with the existing `comparison.md` columns.

### 4.2 Tier 2 — large-codebase tasks (brownfield)

Four sources, all materialised by workspace providers so the agent sees only a repo at a commit plus an
instruction; hidden tests/mutation metadata stay in the provider cache.

| Source | Provider | What the agent gets | How we grade |
|---|---|---|---|
| **2a Fresh PR mining** | `pr-mined` (new; sibling of `swebench`) | issue text (rewritten to remove PR hints), repo at pre-PR commit | PR's fail-to-pass + a pass-to-pass sample, targeted tests only |
| **2b Bug injection** | `repo-mutation` (new) | repo at a known-good commit + a *symptom-level* bug report (observed behaviour, not location) | the hidden test(s) that catch the mutation + pass-to-pass sample |
| **2c Feature excision** | `repo-mutation` (mode `excise`) | repo with a feature and its tests removed + the feature's docs/spec | restored original tests |
| **2d Enhancement with hidden tests** | `repo-mutation` (mode `enhance`) | repo + prose description of new behaviour | hand-written hidden tests (curated, small set) |

**Repo pool** (Python, 50k–500k LOC, fast targeted tests, active): candidates `pydantic`, `httpx`, `sqlalchemy`,
`flask`, `rich`, `typer`, `attrs`, `pandas` (targeted modules only). Final pool chosen in P3 by measuring targeted
test time (< 60 s) and clone size; recorded in `instances.json` like the SWE-bench subset.

**Contamination controls.** 2a filters by PR merge date ≥ a `cutoff_date` recorded in the suite (re-mined
quarterly; `ao-bench import-prs --since`). 2b/2c/2d are synthetic by construction. The repo pool itself is public
and known to models — that is acceptable and realistic (developers work in known frameworks); what must be fresh is
the *task*.

**Mutation operators for 2b** (symptom-first; the agent must localise): boundary flip (`<`→`<=`), swapped
arguments, dropped null-check, wrong default, off-by-one in slicing, inverted condition in a rarely-hit branch,
unit/timezone mix-up. Difficulty = distance between the symptom (which test/module fails) and the site (how many
files/call-hops away), plus whether the failing test exists or the agent has to reproduce from a prose report.

**Grading cost control.** Targeted tests only (`fail_to_pass` + ≤ 30 `pass_to_pass`), per-task test timeout,
shared bare-clone cache (as `swebench_provider` does), no Docker unless the repo cannot be installed with `uv`.

**Reported but not scored:** diff size outside the needed change (collateral), whether the agent's own added tests
pass, number of files touched. These are quality signals for the human reader; scoring them would invite gaming.

#### 4.2a As-built notes (Tier-2 providers, 2026-10-23, `T-T2-5Ij6`)

Deviations/additions relative to the text above; code in `src/agent_orchestrator/bench/{tier2_common,mutation_provider,pr_mined_provider,pr_mined_import,pytest_targeted}.py`, usage in `benchmarks/README.md`.

- **Single-commit workspace snapshot.** After mutation/pruning, the provider replaces `.git` with a fresh one-commit repo. Otherwise `git diff` shows the injected bug (and `git stash`/`git checkout .` undoes it), and for PR-mined tasks `git log --all`/`git fetch origin` reaches the merge commit. The grader's collateral baseline is that snapshot commit (`baseline_commit` in `recipe.resolved.json`), not the upstream `base_commit`. `swebench_provider` still exposes full history to the agent (pre-existing; not changed here).
- **Recipe additions:** `hidden_from_repo` (stash existing tests from the checkout, remove them from `repo_dir`, restore at grade time -- avoids committing copies of upstream tests), `description`, `test_targets`. The provider also parks pristine files in `grading_dir/reference/` so `FakeSubject copy-reference` can prove a recipe solvable.
- **`pr_mined`:** `base_commit` is the merge commit's first parent (exact pre-merge state; the PR API `base.sha` is kept as `pr_base_sha` and used only if the parent lookup fails). Whole-file `test_targets` grading unless the instance carries verified `fail_to_pass`/`pass_to_pass` lists; the merge commit's version of each changed source file is parked as the gold patch in `reference/`.
- **`pytest_targeted`:** vanished node ids grade `missing` (pytest exits 4 on an unknown id; the grader drops it and re-runs), `--continue-on-collection-errors` so one broken module cannot blank the run, `VIRTUAL_ENV` is scrubbed from install/test subprocesses so `uv pip install` can never touch the harness's own environment, a failing `install` is `GraderError`, skipped `fail_to_pass` ids count as failures but skipped `pass_to_pass` ids are excluded.
- **Scoring caveat:** the spec'd `0.7*f2p + 0.3*p2p` gives a do-nothing run 0.3 on a freshly injected bug (its p2p sample passes); rankings should lean on `solved`/f2p until calibrated.

## 5. Metrics

### 5.1 Per (subject × task × repeat) record — extends `SubjectResult` / `GradeResult.detail`

| Field | Source | New? |
|---|---|---|
| `solved`, `score` | grader | `score` now meaningful (weighted probe groups) |
| `probe_results[]` (group, id, pass, ms) | blackbox grader | new |
| `perf` {`p50_ms`,`p95_ms` per N, `alpha`, `ref_ratio_p95`} | blackbox grader | new |
| `cost_usd`, tokens in/out/cache | existing plumbing | — |
| `wall_seconds` | existing | — |
| `api_seconds`, `tool_seconds` | from `ao` run state (per task) / `claude -p` duration fields | new, best-effort, nullable |
| `agent_seconds_sum` | sum over parallel tasks for `ao` subjects | new |
| `status` ∈ succeeded/failed/timed_out/budget_skipped/**infra_error** | subject + grader | `infra_error` new |
| `collateral_lines`, `files_touched` | git diff of workspace | new, Tier 2 |

`infra_error` (process crash before any LLM turn, provider failure, sandbox failure, cost < floor) is **excluded**
from solve-rate denominators and listed in its own table. This is what the `django-11138` $0.09 case should have been.

### 5.2 Artefact efficiency (Tier 1 `perf` group)

For each task the generator emits a fixed operation mix (e.g. 70 % reads / 20 % writes / 10 % list-paginated)
and three dataset sizes `N ∈ {1e2, 1e4, 1e5}` (scaled by difficulty). The grader:

1. Starts the agent's artefact, loads N records, runs the mix for a fixed count, records per-op latency.
2. Repeats 3× per N, takes the median p50/p95 (median across repeats kills GC/scheduler spikes).
3. Fits `log(p95) = α·log(N) + c`; `alpha` is the scaling exponent. Expected α per op class is declared by the
   generator (e.g. indexed get ≤ 0.3, paginated list ≤ 0.3, full-text ≤ 1.1).
4. Runs the same protocol against the hidden reference implementation in the same session, giving
   `ref_ratio_p95 = agent_p95 / ref_p95` at the largest N.

Perf probe passes if `alpha ≤ expected + 0.2` **and** `ref_ratio_p95 ≤ 5`. The generous ratio keeps this a check
for accidental O(N²)/N+1, not micro-optimisation. Absolute ms are recorded for the human reader only.

Artefacts run under `ulimit` (CPU, memory, pids) and a hard timeout; a perf probe that times out scores 0 and is
tagged, not an `infra_error`.

### 5.3 Agent time

Three numbers, all reported: `wall_seconds` (what the developer waits), `api_seconds` (model latency, best-effort),
`agent_seconds_sum` (compute consumed; = wall for single-agent subjects, ≥ wall for `ao`). Rankings use wall;
the split explains *why* a subject is slow.

### 5.4 Cost

Unchanged source. Add `cost_per_score_point = cost / Σ score` beside `cost_per_solved`, because with partial credit
the latter under-rewards near-misses.

## 6. Statistical protocol

- **Paired design.** All subjects run the identical instance set (same seeds, same mutation ids) in one campaign.
  Comparisons are per-task differences, never independent means.
- **Repeats, staged.** Stage 1: `r=1` everywhere. Compute paired bootstrap 95 % CI (resample tasks) on the headline
  metric (score) for each subject pair. Stage 2: for pairs whose CI straddles 0, add repeats (`r=2`, then `r=3`)
  only on the tasks with highest observed variance or disagreement between subjects. Stop when CI excludes 0 or
  `r=3`. Budget is capped per campaign by the existing tier mechanism.
- **Per-family roll-up.** Report score per family as well as overall; a subject that wins only on `crud-relational`
  is visibly so. Family weights are equal.
- **Significance.** Paired per-task score differences → bootstrap CI + Wilcoxon signed-rank p (small n, no normality
  assumption). Reported, not gated.
- **Time/cost.** Medians across repeats; CI by bootstrap. Both are reported as *per solved* and *per score point*.
- **Infra errors** → separate table with counts and causes; a campaign with > 10 % infra errors is marked
  `unreliable` in `comparison.md`.

## 7. Calibration and lifecycle

1. **Generate** a candidate instance (Tier 1) or task (Tier 2). Reference/hidden tests must pass; otherwise reject.
2. **Calibrate** against two anchor subjects (currently cheapest and strongest, e.g. haiku and opus) at `r=2`.
   Pooled pass rate < 0.2 or > 0.8 → adjust difficulty vector (Tier 1) or drop (Tier 2) and repeat. Calibration
   cost is bounded (~$2–4 per instance) and happens once per instance.
3. **Live.** Instance joins a suite with its calibration record (`calibration.json`: subjects, rates, date).
4. **Retire** when a campaign's pooled pass rate leaves the band two campaigns running, or when its seed/PR
   age exceeds a configurable window. Retired instances stay in git for reproducibility but `enabled: false`.
5. **Rotate** Tier 1 seeds every campaign by default (`--rotate-seeds`), keeping a small set of *anchor*
   instances fixed across campaigns for longitudinal tracking.
6. **Held-out seeds (policy, `T-Ws7Iso`).** A campaign meant to *rank* subjects uses a `--seed` that appears in no
   committed suite (`grep -rn '"seed"' benchmarks/suites` before generating) and records it in its
   `benchmarks/README.md` results entry. While `ao` templates/instructions are being tuned, at least one generated
   family stays unseen (never generated, run or inspected) so a ranking can include instances the tuning could
   not have overfit to. Committed suites are for smoke, calibration and longitudinal anchors, not for ranking.

## 8. Integration with the existing harness

All additions register through the existing registries; nothing in the engine changes.

| Piece | Kind | Notes |
|---|---|---|
| `grader.type: "blackbox"` | new `Grader` | cfg: `contract`, `probes` (path, grader-owned), `perf` (op mix, N list, expected α), `weights`, `pass_threshold`, `limits` (ulimit/timeout). Emits §5.1 detail. |
| `source.type: "generated"` | new `WorkspaceProvider` | cfg: `family`, `seed`, `difficulty`, `language`. Writes skeleton into `repo_dir`; writes probes + reference into the grader-owned dir and returns their paths via the provider result. |
| `source.type: "pr-mined"` | new `WorkspaceProvider` | like `swebench` but reads a locally mined `instances.json`; importer `ao-bench import-prs --repo --since`. |
| `source.type: "repo-mutation"` | new `WorkspaceProvider` | modes `inject`, `excise`, `enhance`; recipe files under `tasks/<id>/recipe.json`; hidden tests restored at grade time by the `pytest-targeted` grader. |
| `grader.type: "pytest-targeted"` | new `Grader` (or extend `pytest`) | fail-to-pass / pass-to-pass lists, per-test timeout, collateral diff stats. |
| `bench/gen/` | new package | one module per family; shared trap catalogue, word lists, reference scaffolding, instance schema. |
| `bench/stats.py` | new | paired bootstrap, Wilcoxon, CI formatting. |
| campaign/comparison | extend | `repeats`, staged repeats, family roll-up, infra table, new columns. Existing columns unchanged. |
| suite schema | extend | `enabled`, `family`, `calibration`, `difficulty`, `cutoff_date` (optional fields; old suites validate unchanged). |
| `ao-bench calibrate` | new CLI verb | runs §7 step 2 for a suite or instance. |

**Hidden-asset rule (hard):** providers may write only the skeleton/repo into `repo_dir`. The runner creates a
`grading_dir` per (subject × task × repeat) under the grading root `<base>/_ao_bench_grading`, a sibling of the
workspace root `<base>/ws` (since `T-Ws7Iso`; before, `_grading/` sat inside the run's workspace root), excluded
from any reposet. Same-user processes can still reach it, so the transcript audit (§2c item 3) flags any access. `dev-medium`'s in-fixture `.grading/` is migrated to this in P1 as a
side-effect (small, mechanical).

**Suites (proposed):**

| Suite | Tier | Size | Est. cost / subject (sonnet-class) | Est. wall / subject |
|---|---|---|---|---|
| `gen-services` | medium | 8 families × 2 instances = 16 | ~$8–14 at r=1 | ~1–2 h (parallel 4) |
| `repo-live` (2a) | large | 8–12 | ~$8–12 | ~45–90 min |
| `repo-mutant` (2b/2c/2d) | large | 12 (6 inject, 4 excise, 2 enhance) | ~$8–14 | ~1 h |
| `dev-core` | small | 6 (unchanged) | ~$1 | 3 min |

A full 4-subject campaign at r=1 ≈ $100–160; the staged-repeat protocol typically adds 20–40 %. That overshoots
G3 for the *first* campaign; steady-state campaigns rotate only `gen-services` and one Tier-2 suite (~$80).
`dev-core` stays the free smoke.

## 9. Review decisions (2026-10-23)

| # | Question | Decision |
|---|---|---|
| 1 | Language | **Python + Go + TypeScript** from the start; `language` is a task parameter (§4.1 Stack). |
| 2 | Allowed deps | **sqlite3 everywhere; Mongo-equivalent only if embedded/lightweight** (`montydb` for Python). No external services. Allow-list lives in the suite `stack` block. |
| 3 | Run contracts | **All three** (`http`, `jsonl`, `cli`); each family declares which it uses, suites mix them. |
| 4 | Sandbox | **Light Docker preferred** (per-language images with deps pre-installed); `process` fallback tagged in results. |
| 5 | `ao` time | **Agreed**: rank on wall-clock, show `agent_seconds_sum` beside it. |
| 6 | Perf thresholds | Keep first guesses; calibrate in P1. `ref_ratio` enforced only when agent language == reference language (Python); for other languages it is informational and the α check carries the perf verdict. |
| 7 | Tier-2 repo pool | Candidates stand; final pick by measured targeted-test time in P3. |
| 8 | Weights/threshold | Keep 0.5 / 0.35 / 0.15 and 0.8; revisit after the first calibrated campaign. |
| + | **Parity** | Added as a hard requirement (§2b): shared instructions/skills/env/limits for every subject; fingerprinted; comparison refuses mixed-parity runs. |

Probe/instance contracts that the generators and the black-box grader share are specified in
[`bench-probe-dsl.md`](bench-probe-dsl.md).

## 10. Phasing (for the epic, after review)

| Phase | Delivers | Exit criterion |
|---|---|---|
| **P1** | `blackbox` grader, `generated` provider, `grading_dir` separation (+ `dev-medium` migration), 2 families (`crud-relational`, `job-queue`), `ao-bench calibrate` | both families calibrated at haiku/sonnet/opus; a `gen-services-mini` result committed |
| **P2** | `bench/stats.py`, staged repeats, paired CIs, family roll-up, infra table, new report columns | `comparison.md` shows CIs; infra errors separated |
| **P3** | `repo-mutation` provider (inject + excise), `pytest-targeted` grader, 6–8 curated mutations on 3 repos | `repo-mutant-mini` campaign committed |
| **P4** | `pr-mined` provider + importer, remaining 6 families, seed rotation + retirement tooling, full campaign | one full 4-subject campaign at ≤ $160 committed with CIs |

Each phase is a separate epic-task group with the usual ticket/HLD sync; this document becomes the epic HLD once
accepted, with §9 answers folded in.

## 11. Known limits (stated up front)

- Generated tasks test *known* trap classes; a model can be good at exactly these and still fail on real work.
  Tier 2 exists to keep that honest, and the trap catalogue must grow from observed failures, not only from design.
- Behaviour-only grading cannot see code quality, security, or maintainability. Collateral-diff stats are a weak
  proxy; anything stronger needs a judge (out of scope).
- Calibrating against today's models bakes today's difficulty in; the band check per campaign is what keeps the
  suite from silently saturating next year.
- `api_seconds` depends on what the subject CLI exposes; may be null for bare `claude -p`.

## 12. As-built notes (2026-10-23) — deviations from the design above

Delivered on `ad/enhancements-20oct` by four parallel tracks plus the core seams; bench suite 1144 tests
(21 opt-in skips), docker tier 7/7, network tier 10/10, mypy clean. **Integration evidence:** `gen-services-mini`
(crud-relational http + job-queue jsonl, python) graded end-to-end in Docker with the hidden reference copied in
as the artefact: 2/2 solved, score 1.0, crud 21 core / 4 trap / 5 perf checks, job-queue 11 / 4 / 6,
`ref_ratio_p95` 0.39 / 0.82 (reference vs itself), derived image `ao-bench-python-<hash>` with `montydb`.

| Area | Design said | As built | Why |
|---|---|---|---|
| Infra status | new `infra_error` status | `subject_status="error"` IS the infra status; `Aggregate.infra_errors/attempted/*_attempted` added; `total`/`solve_rate` keep old semantics | every committed `run.json` and status-based test keeps its meaning |
| Grader config | typed per-grader fields | `GraderConfig` is `extra="allow"` (mirrors `source`); each grader validates its own keys | no spec.py edit per new grader |
| Suite `stack` → grader | runner passes it | the generator embeds a copy at `grader.sandbox.stack` | graders only see `GraderConfig` |
| Parity `CLAUDE.md` | into the repo | into `<ws>/CLAUDE.md` (parent of `repo/`); `.claude/` into the repo + `.git/info/exclude` | no diff pollution for Tier-2 checkouts; both CLIs read ancestor CLAUDE.md |
| Docker http artefact | `docker run -d` | foreground `docker run --rm` via Popen, plus `--user`, `--cap-drop ALL`, `no-new-privileges`, `--memory-swap` | a container that crashed at boot lost its logs with `-d` (8/10 trials) |
| Templating | strings | a `{{var}}` that is the whole string keeps its JSON type; in perf `mix`, `{{i}}` is a seeded index in [0, N) and `{{N}}` the size | probes need typed ids and addressable perf records |
| Perf scoring | fraction of checks | one check per `expected_alpha` class + `ref_ratio` (only same-language) + `timeout`; weights renormalise over non-empty groups | `ref_ratio` across languages is informational |
| crud ids | server-assigned | optional client-supplied `id` (1–64 `[A-Za-z0-9_-]`, 409 on reuse) | perf `get` must address `perf-{{i}}` deterministically |
| Perf sizes | 3 fixed sizes | by `difficulty.scale`: [100,500] / [100,1000] / [100,10000] / [100,10000,100000] | keep the mini suite fast |
| Tier-2 history | repo at `base_commit` | both providers replace `.git` with ONE parentless snapshot commit; collateral measured against `baseline_commit` | `git diff/stash/log --all` would expose or undo the injected bug / reach the PR merge |
| Tier-2 hidden tests | copied into `tasks/<id>/hidden/` | also `hidden_from_repo` (stash existing upstream tests out of the checkout, restore at grade) | avoids committing copies of upstream tests |
| pr-mined `base_commit` | PR `base.sha` | merge commit's first parent (`pr_base_sha` kept) | matches the tree the PR was merged onto |
| Comparison | extend 1.0 | `comparison.json` schema 2.0 (1.0 still loads): `parity_*`, `repeats_per_subject`, `subject_stats`, `infra`, `per_family`, `per_language`, `pairwise`, `unreliable_subjects`; cells gain `score_mean/score_sd/n/solve_rate/cost_median/wall_median`; `solved` under repeats = strict majority of attempted repeats | |
| Staged repeats | "tasks where subjects disagree" | disagree ∪ own-repeat variance ∪ no attempted sample (infra retry); stop when all pairwise CIs exclude 0, max repeats, cap spent, < 2 subjects, or no candidates | |

**Environment-isolation finding (2026-10-24, `T-Ws7Iso-bench-env-isolation`, blocks ranking):** the bench workspace
root is `<repo>/playground/.tmp/bench`, inside the checkout and inside the `ao-runner-ao` control-plane workspace.
Claude Code loads `CLAUDE.md`/`.claude/` from every ancestor, so every agent under test — both subjects — ran with
`ao-runner-ao/CLAUDE.md` and `agent-orchestrator/CLAUDE.md` on top of the parity `<ws>/CLAUDE.md`, and with the repo's
skills (`benchmarking`, `repo-intel`, `workflow-authoring`), the repo's agent roster and the control-plane skills
(`git-commit-push`, `memorize`, `verify`, …) available. Evidence: the `system/init` event (`skills`, `agents`) of every
2026-10-23 transcript. One ao `verify` agent ran `cat ../../.gitignore`, so parent probing happens; `_grading/` is
three levels up and not blocked under `bypassPermissions` (no access observed). Parity (§2b) still held — both
subjects got the identical leak — so the 2026-10-23 comparison is fair but not clean, and the "same instructions"
claim is violated. Fix: (1) default root outside any tree with `CLAUDE.md`/`AGENTS.md`/`.claude/`
(`AO_BENCH_WORKSPACE_ROOT`, refuse otherwise), `_grading/` as a sibling root; (2) record an environment fingerprint
parsed from the `init` event per task and refuse mixed-fingerprint comparisons; (3) mark any task whose tool calls
touch the grading root `suspect`; (4) decide `CLAUDE_CONFIG_DIR` handling for user-level settings/skills/memory
(today `~/.claude/settings.json` `model`/`effortLevel`/`enabledPlugins` and user skills apply to every subject,
unfingerprinted). Note that running from the branch checkout is *required*: `AoWorkflowSubject` runs `uv run ao` from
`REPO_ROOT`, so the branch's `ao` is the subject (`ao_version` stamped; run from a clean tree). Developer-side
overfitting (tuning `ao` templates against a known trap catalogue) is a policy matter: ranking campaigns use a
fresh `--seed` absent from every committed suite, and at least one generated family stays unseen during tuning.

**Resolved 2026-10-24 (`T-Ws7Iso`):** all four fixes landed as §2c describes, plus the held-out-seed policy (§7
step 6). Evidence: `make bench-gen-smoke` 2/2 from `/var/tmp/ao-bench/<uid>/ws`; a no-cost end-to-end run of both
real subject adapters (`claude_cli` and `ao_workflow` through `uv run ao run`) against a `claude` shim found every
transcript, matched the baseline and flagged a planted `_ao_bench_grading` access for both; a real haiku run of one
`dev-core` task per subject (see the `T-Ws7Iso` STATUS for the init-event comparison). Fake-tier tests:
`tests/bench/test_env_isolation.py`. The 2026-10-23 results stay a pipeline proof only (README caveat).

**Calibration + ranking work (2026-10-24, `T-Cb8Rk2`, `T-Ov3Sbj`):**

| Area | Before | As built | Why |
|---|---|---|---|
| Reference self-check | not run by `ao-bench gen`; only `bench-gen-smoke` at the easy vector | `make bench-refcheck SUITE=…` is a mandatory free gate before any agent spend (N/N or it is a validity bug) | the first calibration round at `traps=3,scale=2` failed the *reference* on 4/4 instances |
| jsonl probes | `json` had to be an object | any JSON value | job-queue `trap-invalid-input` (traps ≥ 3) sends non-object lines, as SPEC.md promises |
| HTTP transport | short-lived connection per request via the published port | same semantics, but direct to the container bridge IP when reachable + `SO_LINGER 0` | ~30k connections at perf `scale=2` left TIME_WAIT on the `docker-proxy`→container leg (container IP reused) and reset the ~28k-th request; keep-alive was tried and rejected (Nagle + delayed ACK = ~40 ms floor per request on Python servers) |
| Grader exception | subject cost recorded as $0 | the finished subject's cost/tokens are kept | budgets and $/task must reflect real spend |
| `ao` subjects | fixed `workflow.json` only | template mode (`template`, `template_params`, `prompt_file`, `init_git`) — e.g. `ao-overseer-sonnet` runs the builtin `overseer-runner` via `ao new … --run` | the overseer is a template, not a static DAG |
| Report | rates, pairwise, roll-ups | + Cost & efficiency ($/task mean/median, $/solved, $/score-pt, wall/task, out-tokens/task), per-probe-group pass fractions, rule-based observations | the ranking question is quality *and* cost |
| Parity bundle | user `effortLevel` applied silently | `CLAUDE_CODE_EFFORT_LEVEL=high` pinned | equal, declared effort for every subject |
| Go sandbox | `golang:1.23-alpine` (could not build: `modernc.org/sqlite` needs Go ≥ 1.26) | `golang:1.26-alpine` | image builds; TS/Go still unfair while the agent host lacks Node ≥ 22 / Go |

**Ranking outcome (2026-10-24, `T-Cb8Rk2`):** haiku / sonnet / opus / ao-overseer-sonnet all solved 10/10 (gen-rank-v1 +
repo-mutant-mini); quality is tied at the instrument's ceiling, cost and time separate them (see `benchmarks/README.md`).
New open items: (1) a harder instrument for quality signal (below); (2) perf-phase `ConnectionRefused` flake under
parallel grading (1 of 40 gradings, not reproducible alone); (3) crud perf at `scale=3` (reference exceeds the perf
budget at 100k); (4) `overseer-runner` budget staging at small `run_budget_usd` (backstop fired in 5/6 runs at $2).

**Still open (next epic tasks):** the six remaining Tier-1 families (§4.1); TypeScript/Go derived images built and
run (Dockerfiles only `--check`ed; process fallback has no pre-installed deps for them); `ao-bench gen` does not yet
run the generation-time "reference passes its probes" check (the structural validator runs; the full check is the
`bench-gen-smoke` make target); `api_seconds`/`agent_seconds_sum` (§5.3) have no record seam yet; Tier-2 scoring
gives a do-nothing run 0.3 on inject tasks (p2p passes) — `solved` is exact, treat `score` as secondary there;
agent-modified `conftest.py`/pytest config is not reset before Tier-2 grading; `calibrate --write` re-indents a
hand-compacted suite.json once; `.bench-solution/` is still readable inside `dev-core`/`dev-medium` fixtures
(pre-existing). Real-LLM calibration of the perf thresholds (§9 Q6) has not been run.

