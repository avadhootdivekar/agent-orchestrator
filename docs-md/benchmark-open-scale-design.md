# Open-scale benchmark systems: graded quality instead of pass/fail

Status: design (2026-10-24) · Ticket: `T-Os5Lad-bench-open-scale` (epic `E-Bn7Dsc`) · First system built:
`opt-ladder` + the `objective` grader (§4.1, §6).

## 1. Why a new mechanism

Every pass/fail instrument built so far either saturates or separates for the wrong reason:

- Generated spec tasks in five families: haiku and opus both score 1.0 on 24/24 calibration tasks.
  (The five are crud and queue services, order-book, rules-engine, bug-hunt, algo-puzzle and algo-hard.)
- Exercism with hidden tests: haiku 28/48 against opus 46/48. With visible tests both solve 48/48. The spread
  came from guessing names and message wording, not from capability (`benchmark-task-portfolio.md` §8).

A fully specified task has one right answer and a checker for it. Once a model can reach that answer, more
capability earns nothing. The ceiling is a property of the task type, not of its difficulty knobs. Raising
size, traps or spec length moves the ceiling slightly and keeps it.

The fix is to grade **how good** an outcome is, on a scale that keeps going up:

- the best route rather than *a* route;
- a stronger bot rather than *a legal* bot;
- more digits of accuracy;
- more rungs of a size staircase;
- more faults found.

Stronger models write stronger heuristics, search more cleverly and use the time limit better. Weaker models
stop at the first working approach. The gap between "works" and "good" is where haiku and opus differ most,
and pass/fail grading throws it away.

Targets from the owner, on a 0–1 normalised score:

- haiku at 0.25–0.45 and opus at 0.70–0.85, tunable;
- or a pure-number scale with no upper limit.

The design provides both. Every system reports raw **points**, which are unbounded. A per-suite **par**
divides the points to give the percentage, and par is the tuning knob (§5).

## 2. Principles (unchanged from the epic, restated for open scales)

1. **Fully automatic and deterministic grading.** No LLM judge.
   - A grader-owned checker validates feasibility and computes the objective.
   - Reference points are computed by deterministic reference solvers. They use seeded RNGs and iteration
     budgets, never wall-clock budgets.
2. **The scale is anchored by algorithms, not by models.**
   - Ladder rungs are named, well-known solution tiers: trivial, greedy, local search, metaheuristic, strong.
   - A rung means the same thing to every subject and every campaign, and a point has a meaning ("reached
     local-search quality").
3. **Hidden instances, generated at materialise time** from the task's `source` block, as in every
   generated family. The agent sees the generator definition and small examples, never the graded cases.
4. **Tuning without fitting** (`bench-tune` skill).
   - Knobs are pre-declared: instance size, time limit, par rung, rung strength.
   - Calibration uses anchors haiku and opus only, on disjoint seeds, with an acceptance rule written down
     before the run (§5).
   - The ranking of other subjects is never tuned.
5. **The same protocol across languages.** Each task uses the jsonl stdin/stdout contract, so Python, TS and Go
   all work, and C++ can be added via the toolchain bundle. Scores are compared within a language: the time
   limit favours compiled languages, so the language is fixed per task, not mixed in one ranking.

## 3. Scoring mechanisms (shared by all systems)

### 3.1 Ladder interpolation (optimisation, compression, numerics)

For each case, deterministic reference solvers give objective values `v_0 … v_K` on rungs `0 … K`:

- rung 0 is a trivial feasible answer;
- rung K is the strongest reference.

Values are strictly improving (the generator re-draws a case whose ladder is not strict). For a minimisation
objective, an answer `y` earns:

```
points(y) = 0                                              if infeasible, timed out, or y >= v_0
          = k + (v_k - y) / (v_k - v_{k+1})                if v_{k+1} <= y < v_k
          = K + (v_K - y) / (v_{K-1} - v_K)                if y < v_K   (beyond the strongest reference)
```

Maximisation mirrors this. Points beyond `K` are unbounded: an answer that improves on the strongest reference
by another "last gap" earns K+1. A task's points are the mean over its cases. The normalised score is
`min(1, points / par)`. `par` defaults to K, and `points` is kept raw in `detail`.

Each rung is a known algorithm tier, so the points read as a sentence: "2.6 = local-search quality plus 60% of
the way to the short metaheuristic".

### 3.2 Rating against a fixed ladder of bots (games)

Reference bots `B_0 … B_K` have fixed Elo ratings:

- `B_0` plays random legal moves and is fixed at 0;
- each `B_k` is rated once, offline, by a round robin among the reference bots, and the ratings are frozen in
  the family.

A subject's bot plays `m` games against each rung, alternating colours, with seeded openings. Its rating is the
maximum-likelihood Elo given those results against the frozen ratings. A bot that beats every rung has an
unbounded rating, capped only by the number of games (report a 95% interval). Normalised score:
`rating / rating(B_par)`.

### 3.3 Staircase (scale and performance)

A task is a geometric ladder of instance sizes, `n_j = n_0 · 2^j` for j = 0…J. Points are the number of rungs
answered correctly within the per-request time limit, up to the first failure (later rungs are still run and
reported). The top of the ladder is beyond what the reference achieves, so the scale has headroom.

### 3.4 Count of items (debugging, change chains)

Points are the number of independent items done: planted faults fixed, or amendment stages fully passing. The
item count grows with the difficulty knob. Normalised by the item count.

### 3.5 Portfolio aggregate

`ao-bench portfolio` already pools suites. With open scales it gains:

- the mean **points** per system;
- the mean normalised score;
- an optional **Rasch/IRT ability** per subject, fitted jointly with item difficulties over all tasks, as a
  single pure number across the portfolio. This needs at least 4 subjects and at least 30 items to be stable,
  so it is reported only then.

Continuous per-task scores give paired tests far more power than solved/unsolved. A Wilcoxon signed-rank test
on per-task points needs roughly n=15–20 tasks for an effect that pass/fail needs about 50 to see.

## 4. The systems (domains, inputs, graders)

Each system is a generated family. It is a pure function of `(seed, difficulty, language)` and uses the shared
`Instance`/provider plumbing. Its grader-owned assets live under `grading_dir`.

### 4.1 `opt-ladder` — combinatorial optimisation (BUILT FIRST)

- **Domain:** routing, location and partitioning. NP-hard problems where "a valid answer" is easy and "a good
  answer" is not.
- **Protocol:** `{"op":"solve","problem":p,"case":{...}}` returns `{"ok":true,"answer":<solution>}`. Every case
  must be answered within a per-request time limit stated in SPEC.md (10 s by default).

| problem | instance (generated by the LCG stream, as in algo-puzzle) | answer | objective |
|---|---|---|---|
| `tour` | `n` points with integer coordinates in a square, some clustered | closed tour as a permutation starting at 0 | total Euclidean length (min) |
| `facility` | `m` sites with opening costs and `n` customers; assignment cost = distance × demand | an open site for each customer | opening + assignment cost (min) |
| `partition` | weighted sparse graph, `k` parts | part per vertex, sizes balanced to within ±1 | weight of cut edges (min) |

- **Reference ladder:**
  - rung 0, trivial: identity order, the cheapest single site for everyone, round-robin parts;
  - rung 1, greedy: nearest neighbour, greedy add/drop, BFS-grown parts;
  - rung 2, local search: 2-opt and Or-opt, swap/flip local search, pairwise swap refinement;
  - rung 3, a short simulated-annealing budget;
  - rung 4, a long simulated-annealing budget.
- **Execution:** the reference server runs the rung-4 solver. Refcheck therefore scores exactly K points
  (score 1.0), and the no-op subject scores 0.
- **Difficulty knobs:**
  - `scale` sets the instance size: `tour` n = 200/500/1000/2000; others proportionally;
  - `entities` sets the number of problems (1–3);
  - `concurrency` sets the cases per problem (1–4);
  - `traps` adds feasibility edge cases, such as duplicate points, zero-weight edges and sites nobody can
    use. These earn 0 points when mishandled.
- **Why it should separate:** haiku-class output is typically greedy plus a slow O(n²) 2-opt, which times out
  or stops near rung 2. Opus-class output tends to add neighbour lists, don't-look bits, Or-opt and annealing,
  using the full time budget, which lands near rungs 3–4. This is a hypothesis; the calibration in §5 tests it.

### 4.2 `game-arena` — bots against a rated ladder

- **Domain:** adversarial search and strategy.
- **Protocol:** `{"op":"move","state":...}` returns `{"ok":true,"move":...}` within a per-move time limit.
- **Games:** generated rule variants, so a memorised engine does not apply:
  - connection games (board size and win length drawn: 7×6/4, 9×7/5, …);
  - capture games with drawn rules (Mancala-like: pits, seeds, sowing direction);
  - imperfect information (a 3-card bidding game with drawn deck sizes).
- **Ladder:** random; greedy one-ply; alpha-beta at depth 2, 4 and 6; MCTS at 2k and 20k playouts.
- **Grading:** §3.2. About 20 games per rung with seeded openings, run in parallel by the grader.
- **Knobs:** branching factor (board size), per-move time, rule complexity.
- **Separation:** a legal bot is easy; a bot that beats depth-4 alpha-beta needs a correct search with good
  evaluation and time management.

### 4.3 `scale-staircase` — the same correct answer, at ever larger n

- **Domain:** algorithms and performance engineering. It reuses the `algo-puzzle` and `algo-hard` engines and
  problem sets (`ProblemSet`), so the correctness oracle already exists and is cross-checked by brute force.
- **Change:** instead of fixed small and large probes, each problem gets a geometric size ladder from 10³ to
  10⁷, graded per §3.3.
- **Separation:** O(n log n) against O(n²), constant factors, streaming input parsing, and memory. Today's
  saturation at 2·10⁵ becomes a staircase where the top rungs need a tuned implementation.
- **Knobs:** time limit and ladder top. Languages are fixed per task.

### 4.4 `oracle-clone` — reverse-engineer a black box

- **Domain:** investigation and hypothesis testing.
- **What the agent gets:** a runnable `oracle` CLI, the jsonl protocol and a one-paragraph description. The CLI
  implements a hidden generated function, for example:
  - a pricing rule set with interacting thresholds;
  - a tokenizer with drawn escape rules;
  - a stateful cache with a drawn eviction policy.
  The CLI is shipped as compiled Go, so its source cannot be read.
- **The task:** write a program that agrees with the oracle.
- **Grading:** agreement on 2,000 held-out generated inputs, weighted by rule. The oracle is removed before
  grading; a wrapper that shells out to it then fails.
- **Points:** rules fully reproduced, which grows with the number of rules.
- **Knobs:** number of rules, interaction depth, hidden state.
- **Separation:** a systematic probing strategy against shallow pattern matching.

### 4.5 `change-chain` — long-horizon amendments

- **Domain:** long context and maintenance under change.
- **Task:** SPEC.md is version 1 plus K amendments, with K from 3 to 12. Each amendment changes or extends
  earlier behaviour (errata, new rules that override old ones, deprecations).
- **Grading:** hidden probes per stage. A stage passes only if its probes and all still-valid earlier probes
  pass.
- **Points:** stages passed (§3.4), unbounded with K.
- **Optional multi-turn mode:** the runner feeds amendments one at a time, in the same workspace, as separate
  subject invocations. This needs a runner feature: tasks with `stages`.

### 4.6 `fault-field` — many faults, symptoms only

- **Domain:** debugging at scale.
- **Task:** a medium generated codebase (2–5k lines) carries N planted faults, N from 5 to 40, of mixed types:
  off-by-one, wrong comparator, a dropped branch, state leakage, a concurrency race. The report lists
  user-visible symptoms only and gives no failing test.
- **Points:** faults fixed, each verified by its own hidden probes, without regressions. `bug-hunt` with few
  faults saturated; the count is the scale.

### 4.7 `compress` and `numerics` — pure-number quality

- **`compress`.**
  - Task: write `compress` and `decompress` for a generated corpus family, such as structured logs, sensor CSV
    or a synthetic language.
  - Grading: the grader verifies an exact round trip on held-out corpora, then scores bits per byte against a
    ladder of zlib, bz2, lzma and a context-mixing reference (§3.1).
- **`numerics`.**
  - Task: compute quantities to a stated precision within a time limit. Examples: oscillatory integrals,
    eigenvalues of structured matrices, ODE end states, roots of special functions.
  - Points: `−log10(relative error)`, the digits of accuracy, capped at 15 per case. Naive methods get 3–6
    digits; adaptive or high-order methods get 12 or more.

### 4.8 Coverage matrix

| system | domain | mechanism | languages | horizon | built |
|---|---|---|---|---|---|
| opt-ladder | optimisation / OR | ladder (§3.1) | py/ts/go | medium | **yes (P1)** |
| scale-staircase | algorithms / perf | staircase (§3.3) | py/ts/go | short | P2 |
| game-arena | search / strategy | rating (§3.2) | py/ts/go | medium | P2 |
| oracle-clone | investigation | agreement + items | py/ts/go | medium | P3 |
| change-chain | long context / maintenance | items (§3.4) | py/ts/go | long | P3 (multi-turn: runner feature) |
| fault-field | debugging | items (§3.4) | py | long | P3 |
| compress / numerics | data / numeric | ladder / digits | py/ts/go | short | P4 |

Keep a portfolio that is not biased to one kind of task: the open-scale systems sit next to the existing
pass/fail suites, which remain as an easy and medium floor and a sanity check.

## 5. Tuning to the target bands (without fitting)

- **Knobs, pre-declared per system:**
  - instance size (`scale`);
  - time limit;
  - **par**, the rung or rating that counts as 1.0;
  - rung strength, meaning the reference budgets. These change what a rung means, so they are versioned.
- **Pre-registered calibration (anchors haiku and opus, disjoint seeds):**
  1. Run both anchors at default knobs: 3 seeds and 1 language (Python).
  2. **Accept** if `0.25 ≤ mean_haiku ≤ 0.45` and `0.70 ≤ mean_opus ≤ 0.85`. Also accept if the anchor means
     differ by at least 0.25 and neither is above 0.9; that still discriminates, and the bands are moved by
     par alone (step 4).
  3. If opus is above 0.85, move to a larger size or a shorter time limit first (one step on the declared
     grid), then rerun. If haiku is below 0.25, go the other way.
  4. Par moves only as the last step, and by whole rungs.
  5. Record every knob change and its result in the task STATUS. Never pick knobs per subject.
- **Pure-number reporting needs no tuning:** raw points (and Elo for games) are comparable across campaigns as
  long as the rung definitions are unchanged.

## 6. Grader architecture: `objective`

A new grader type next to `blackbox`. It reuses the sandbox, `Session` and the jsonl transport.

```
grading_dir/
  instance.json            family, seed, difficulty, language, limits (problems, case_seconds, par)
  cases.json               [{id, problem, case, sense, rungs: [v_0..v_K]}]   (grader-owned)
  reference/run.sh, server.py   strongest-rung solver + evaluate(problem, case, answer)
```

Grading:

1. Load `cases.json`. Import `reference/server.py` in-process, only for `evaluate`; it runs the
   grader's own checker on the grader's own files.
2. Start the artefact in the sandbox (jsonl).
3. For each case, send `solve` with a timeout of `case_seconds`, then run `evaluate`:
   - an infeasible answer, or a malformed one, earns 0 points;
   - after a timeout or a crash, restart the artefact and continue.
4. Compute per-case ladder points (§3.1), the task's mean points, `score = min(1, points / par)`, and
   `solved = points ≥ solved_rung` (default rung 2, local-search quality).
5. `detail` holds per-case objective, points and rung values, plus `points_mean`, `par` and the reason for any
   zero.

The reference is *not* rerun at grade time. The rung values are computed at generation time, deterministically,
and stored in `cases.json`. Grading cost is therefore the artefact's run time only. `make bench-refcheck`
proves the reference server reproduces rung K: copy-reference scores 1.0 and no-op scores 0.

Reporting: `comparison.md` and `ao-bench portfolio` show `points_mean` next to the score whenever a grader
provides it.

## 7. Anti-gaming

- **Hidden cases and a strict checker.** Graded cases are hidden and drawn from the stated generator. The
  checker validates every answer strictly: a permutation of the right length, assignments in range, the
  balance constraint.
- **Time limits per request.** A program that precomputes nothing useful gains nothing, because instances are
  drawn per seed.
- **No oracles at grade time.** The `oracle-clone` binary is removed and the network is off in the sandbox.
- **An unbounded scale does not reward cheating.** Beating the strongest reference must still pass the
  feasibility checker; an infeasible answer earns 0.

## 8. Rollout and cost

- **P1 (this session):**
  - the `objective` grader and the `opt-ladder` family (Python reference, three problems);
  - unit tests;
  - refcheck;
  - a small calibration with anchors haiku and opus on Python tasks, at scale 1 and 2.
  At about $0.3 per haiku task and $2 per opus task, 3 seeds × 2 scales costs roughly $15.
- **P2:**
  - `scale-staircase`, which reuses the algo engines and is cheap;
  - `game-arena`, which needs a match runner and the offline Elo rating of the reference bots;
  - points columns in comparison and portfolio.
- **P3:**
  - `oracle-clone` (compiled-Go oracles generated per seed);
  - `change-chain`, plus the runner's multi-stage mode;
  - `fault-field`.
- **P4:** `compress` and `numerics`; IRT ability in portfolio.

## 9. Risks

- **Python's reference speed limits rung strength.** Rung 4 is a long annealing run in Python. A Go solution
  at the same time limit may exceed K, which is fine because the scale is open. Compare within a language.
- **Ladder compression near the optimum.** For easy instances, rungs 3 and 4 can be within 0.5%, making
  points noisy. The generator rejects cases whose last gap is below a minimum relative gap, and sizes grow with
  `scale`.
- **Timing noise.** Agent programs that use "all the time" vary with host load. Run with `--max-parallel` no
  higher than the core count, and use a time limit well above the noise. Optionally re-grade twice and take
  the mean.
- **Models may all write annealing.** If haiku also anneals well, the band test fails and the knobs move:
  larger n, so that neighbour lists are needed; or less time. This is exactly what §5 is for.
