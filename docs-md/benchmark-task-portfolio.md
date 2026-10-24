# Benchmark task portfolio — what we cover, why it saturates, what discriminates

Status: design + build log, epic `E-Bn7Dsc` follow-on `T-Pf4Mix-bench-task-portfolio` (2026-10-24).
Companion to `benchmark-discriminating-suites-hld.md` (instrument mechanics) and the `bench-tune` /
`bench-campaign-report` skills (procedure).

## 1. What was covered on 2026-10-24 (the saturated campaign)

| Suite | Tasks | Area | Language | Contract | Result (haiku…opus, ao-overseer) |
|---|---|---|---|---|---|
| `gen-rank-v1` | 6 | CRUD over HTTP (crud-relational), work queue over JSON lines (job-queue) | Python | http, jsonl | all 6/6 |
| `repo-mutant-mini` | 4 | injected bug / excised feature in click, attrs, rich | Python | pytest (hidden) | all 4/4 |

Two of the eight Tier-1 families planned in the HLD exist (`crud-relational`, `job-queue`); `ledger-inventory`,
`rate-limiter`, `search-index`, `expr-evaluator`, `file-sync`, `event-state-machine` do not. No algorithmic /
data-structure task, no parser/interpreter, no debugging of unfamiliar generated code, no long-spec task, no
long-horizon task, one language, the `cli` contract unused. So: specific, not "all types".

## 2. Why those saturate

1. **Every requirement is stated.** The generators render traps as plain requirements ("PATCH with null clears…",
   "same Idempotency-Key returns the stored response"). A strong model that reads the whole spec and tests its own
   work implements each one; the hidden probes check exactly what was said.
2. **Conventional shapes.** CRUD + pagination, a priority queue with leases: the solution pattern is known; the
   only work is diligence.
3. **Enough budget.** 30 turns, self-testing, an hour: even haiku iterates to green.
4. **One forgiving language and small scale.** Python, ≤10⁴ records, generous perf thresholds.

The result is still informative — cost and time separate the subjects by 25× and 10× — but capability is tied.

## 3. What actually creates spread (evidence + mechanism)

| Driver | Mechanism | Public evidence | Expected spread here |
|---|---|---|---|
| Algorithmic correctness with combinatorial edge cases, hidden tests | the model must *derive* behaviour (search, matching, parsing) and get every corner right without seeing the checks | Aider polyglot (Exercism hard set): its public leaderboard shows a wide spread between models on the same 225 problems (to be re-measured here, not assumed) | **high** |
| Less common languages / toolchains | ownership, templates, build systems; idioms under-trained in weaker models | expected from training-data skew; measured here per language rather than assumed | medium–high |
| Long, interacting specification | many rules with precedence; weaker models drop or mis-order rules, score degrades per rule | rule/long-context evals; our own crud spec length showed no drop at 6 entities (too few interactions) | medium–high (continuous score) |
| Debugging unfamiliar code | locate K planted faults in a large codebase from symptoms; reading > writing | SWE-bench-style gaps between models | medium–high |
| Performance that needs the right complexity | O(n log n) vs O(n²) at n=2·10⁵ is a 10⁴× gap, robust to timing noise | competitive-programming time limits | medium (binary per task) |
| Long horizon / many steps | partial completion; orchestration should help | Terminal-bench, multi-file features | high, and where `ao` should shine |
| Under-specification | guessing | — | **rejected**: unfair, measures luck |

Same-shape CRUD families in more languages alone would mostly re-saturate; language helps most when combined
with algorithmic depth.

## 4. Where tasks come from

| Source | Contamination | Licence | Cost to adopt | Use |
|---|---|---|---|---|
| **Generated families (ours)** | none (seeded, names rotated) | ours | build a generator + reference | the backbone: tunable difficulty, fresh seeds per campaign |
| **Exercism via `Aider-AI/polyglot-benchmark`** | high (public since 2016/2024) but hidden tests + agentic setting | Exercism tracks are MIT | importer + per-language test grader | algorithmic depth, 6 languages, known to discriminate; report as "contaminated" tier |
| **Recent merged PRs** (`ao-bench import-prs`) | low if after the model cutoff | repo licence | reviewing instructions | brownfield realism |
| SWE-bench Verified | high, ~1 GB image/instance | MIT | heavy | occasional reference point only |
| Codeforces/AtCoder/LeetCode | high; ToS restrict redistribution | restrictive | — | not used |
| LiveCodeBench-style post-cutoff problems | low | mixed | scraping + tests | candidate for later |

## 5. Target mix (so a ranking means something across the range)

| Axis | Bands |
|---|---|
| Difficulty | easy (dev-core, short CRUD) · medium (crud/queue at default) · hard (algorithmic exercises, engines) · very hard (composite engines, multi-bug hunts, long specs) |
| Area | web/CRUD · queues/concurrency · algorithms/data structures (matching engine, puzzles, graphs) · parsing/interpreters (forth, wordy, sgf, expression languages) · business-rule engines (long spec) · debugging (bug-hunt) · brownfield libraries (repo-mutant, pr-mined) |
| Language | Python · Go · TypeScript/JavaScript · Rust · C++ (Java optional) |
| Length | short spec (<2k tokens) · long spec (>8k tokens) · large code to read (≥1k lines) |
| Contract | http · jsonl · cli · unit tests |

Rule: every campaign reports per-band roll-ups; no band is allowed to dominate the headline (equal weight per
band in the headline score, not per task).

## 6. Build plan (this follow-on)

1. **Toolchains for the agent host** — done (`bench/toolchains.py`; Go 1.26, Node 22 for every subject).
2. **`exercism` provider + grader + importer** — a seeded, stratified sample of the polyglot set (not chosen by
   results); hidden official tests run in per-language Docker images; score = fraction of tests passed; reference
   = Exercism's own example solution (self-check must pass 100 %, stub must fail).
3. **`order-book` family** (jsonl, pure in-memory): limit order book with price-time priority, partial fills,
   IOC/FOK/post-only, stops, cancel/replace, funds reservation; probes generated by an in-process reference model.
4. **`rules-engine` family** (jsonl, long spec): a seeded set of 15–60 pricing/eligibility rules with precedence,
   caps and rounding; each probe exercises a subset; continuous score.
5. **`bug-hunt` mode**: K seeded mutations of a family's reference implementation handed to the agent with the
   spec; the agent must find and fix them; score = fraction of probes.
6. Calibrate (anchors only, fresh seeds), then one portfolio campaign over all bands; report per band.

## 7. Guarding against fitting in a mixed portfolio

- External suites: the sample is drawn by a recorded seed, stratified by language (and Exercism difficulty when
  known), *before* any agent runs; never re-drawn after results.
- Generated suites: the bench-tune protocol (anchors only, disjoint seeds, frozen grader).
- New families are accepted on reference/noop checks only; their difficulty vector is calibrated on anchors.
- The headline uses equal band weights fixed in advance; per-band tables are always shown.
