# Benchmark ideas backlog (open-scale and discriminating tasks)

Status: living list (2026-11-02). Owner: bench. Ticket: `meta/tickets/E-Bn7Dsc-discriminating-benchmark-suites/`.
Every idea below was proposed or considered while building the game arena; this file keeps them so a
few can be picked, revised and built later. Entries say what is built, what was judged and why.

## 0. Selection criteria (what "discriminating" has to mean)

A task earns a place only if all of these hold; the first failure to meet them is what retired the
earlier families.

1. **No ceiling.** A score the strongest model can reach by doing the obvious thing saturates (every
   pass/fail family saturated: see `bench-portfolio-findings`). Prefer a hidden objective that keeps
   improving (route length, rating, throughput) and report the **uncapped** number; a capped
   `score = min(1, x / par)` is only a convenience.
2. **A ladder that extends past the best model.** The Othello arena capped at `ab6`, so sonnet and
   opus tied at the ladder's ceiling (1399 Elo is the all-wins cap of a one-virtual-game prior).
   Check, before spending on models, that a strong reference sits above anything expected.
3. **Gaps larger than the intervals.** Measured gaps were about 90 Elo against intervals of about
   +/-150 with 8-12 games per rung. Count samples (cases, positions, repeats) so the interval is
   narrower than the gap you want to see.
4. **Objective and mechanical.** No judge, no fix-verification by hand (the owner rejects
   debugging-an-existing-repo tasks: hard to measure the fix objectively).
5. **Not gameable.** Hidden, seeded, regenerable instances; held-out pool for final comparisons;
   reference self-check; tune the instrument, never fit to it (benchmarking skill rules).
6. **Known-solution tasks saturate.** If a textbook design solves it (LSM tree, hash join) the models
   tie on correctness and differ only by machine and language noise.

## 1. Built

| Id | What | Grader | Where | State |
| --- | --- | --- | --- | --- |
| G1 | **Game arena**: write a game bot, rated by Elo against a frozen ladder of reference bots (Othello, Connect-K; Breakthrough held out) | `arena` | `bench/games/`, `bench/arena.py`, `gen/game_arena.py`, `docs-md/benchmark-game-arena-design.md` | built; first run orders haiku < sonnet <= opus but intervals overlap and the old ladder capped at `ab6`; Othello 8x8 now has `ab7`/`ab8` (rerun in progress) |
| G2 | **Game scenarios**: one move in each of N fixed positions, scored by rank against a hidden depth-6 search (percentile of the chosen move, rescaled so random = 0) | `scenarios` | `bench/games/scenarios.py`, `bench/scenarios_grader.py`, `gen/game_scenarios.py` | built; calibration pending |
| C1 | **CVRP ladder**: capacitated vehicle routing vs trivial / nearest-neighbour / savings / local search / SISR | `objective` | `gen/cvrp_ladder.py`, `gen/reference/cvrp_server.py`, `docs-md/benchmark-cvrp-ladder-design.md` | built; calibration pending |
| O1 | `opt-ladder`: tour / capacitated facility / balanced partition vs a rung ladder | `objective` | `gen/opt_ladder.py` | built earlier; haiku reached the top rung with an 8.5 s annealing, so the top rung needs compute comparable to the agent's |
| O2 | `algo-staircase` | `objective` (`rungs`) | `gen/algo_puzzle.py` | built earlier; staircase |

## 2. Combinatorial optimisation candidates (CVRP is the first; same shape for the rest)

Shape: the subject writes a solver (`solve` over jsonl, per-case time limit); a hidden score is the
gap to a reference ladder; reference solvers are deterministic with iteration budgets, never wall
clock; `objective` grader and `ladder_points` already exist.

| Idea | Why it discriminates | Notes |
| --- | --- | --- |
| **Capacitated VRP** (C1) | NP-hard, no known optimum at 100-800 customers; greedy ~ +45% vs savings, savings ~ +4-6% vs SISR; open end of the scale | built |
| **Job-shop scheduling** | dispatching rules are easy, tabu search / CP-style search beats them; lower bounds exist for the gap | ladder: FIFO, SPT, shifting bottleneck, tabu |
| **Weighted max-SAT / set cover** | greedy simple, WalkSAT-style search clearly better; instance structure (random vs industrial-like) is a hidden split | ladder: greedy, local search, WalkSAT |
| **Bin packing with conflicts / 2-D packing** | first-fit is decent, structured search wins on tight instances | shape of the answer is large; check size |
| **Vehicle routing with time windows** | harder cousin of CVRP (feasibility is itself hard) | after C1 shows the shape works |
| **Pickup and delivery / dial-a-ride** | same family, more constraints | |
| **Graph colouring, max-cut, QAP** | classical metaheuristic ladders | cheap evaluators |

## 3. Systems and performance candidates

| Idea | Verdict | Reason |
| --- | --- | --- |
| **Key-value store with range scans and crash recovery** | **dropped** (owner and analysis agree) | textbook design (LSM / B-tree + WAL) passes the gate; throughput spread is language noise |
| **Event reconciliation engine** (L2-04, streams with duplicates and reordering) | **dropped as a ranking task**; keep as a correctness task if wanted | known solution (streaming hash join + dedup); correctness saturates |
| **Time-series ingest and query** | weak | naive scan vs chunked index is the only axis |
| **Interpreter / bytecode VM performance** | candidate | correctness tiers (staircase) then instructions per second on hidden programs; unbounded |
| **Emulator (CHIP-8 / 6502) with hidden ROM suite** | candidate | conformance tiers plus cycles per second |
| **Workload-adaptive storage engine** | candidate if storage returns | the best design depends on a hidden access mix |
| **Compiler to a stack machine, scored by executed instructions** | candidate | optimisation passes give an unbounded scale |
| **Compression (ratio on a hidden corpus under time/memory)** | candidate | easy to build; gameable if the corpus leaks |

## 4. Games and adversarial candidates

| Idea | Verdict | Notes |
| --- | --- | --- |
| Arena rating (G1) | built | final strength rating; ladder must extend past the best model |
| **Scenario suite** (G2) | built | main tuning signal: deterministic, low variance, no ladder to outgrow; capped at matching the reference depth |
| Fixed-time score games ("most discs when time ends") | not built | needs a symmetric time model and makes results machine dependent; keep bots budgeted by depth |
| Target-based goals ("avoid mate for N moves", "win this endgame") | extension of G2 | scenario positions with an objective instead of a rank |
| **Model-versus-model tournament** | candidate, final comparison only | rating is relative, so there is no ceiling by construction; round robin of subjects is expensive |
| Imperfect-information games (poker, auctions, negotiation) | candidate | Elo vs a ladder of fixed strategies; variance is high |
| More games (Hex, Go 9x9, Amazons, Konane, Gomoku) | cheap to add | one module under `bench/games/`, one line in `pools.py`, a rules test, `arena-rate` |
| Connect-K | weak discriminator | the depth rungs are flat at the top (depth 2..8 within about 60-100 Elo) and MCTS rungs did not lift the ceiling; prefer Othello and Breakthrough |

## 5. Verification and testing candidates

| Idea | Notes |
| --- | --- |
| **Mutation score**: write tests that kill a hidden mutant pool | `repo-mutant-mini` exists; the kill rate is unbounded-ish |
| **Property-test synthesis** | grade by the share of hidden faulty implementations rejected |
| **Staircase specs**: the spec grows tier by tier, score = tiers cleared | exists as `algo-staircase`; combine with C1 or a VM |
| Fuzzer quality (coverage or crashes found in a hidden target) | measurable but noisy |

## 6. Lessons that shaped the list

- Saturation killed fully specified families; open objectives are the fix, but only with a ladder
  that outruns the best model (the Othello cap, the opt-ladder top rung beaten by 8.5 s annealing).
- Give the top reference rung compute comparable to the agent's time limit, and floor the open-end
  unit so a tiny last gap cannot explode the scale.
- Open-ended tasks need `--max-turns 80` for every subject (30 turns made opus fail by running out of
  turns, not by being weaker).
- Report the uncapped number and its interval; never rank on a capped score alone.
- Debugging tasks are out; generated, hidden, seeded instances are in.

## 7. How to pick one up

1. Write a short design under `docs-md/` (shape, ladder, knobs, traps, risks).
2. Reuse the `objective` grader (ladder + `evaluate` + `ladder_points`) or the games plugin.
3. Add a family under `bench/gen/`, a reference under `bench/gen/reference/`, traps, tests.
4. Generate a calibration suite, run the no-LLM checks (no-op scores 0, reference scores par), then
   haiku/sonnet/opus; judge against the band rule in the open-scale design.
