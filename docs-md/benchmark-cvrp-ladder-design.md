# CVRP ladder design (`cvrp-ladder`)

Status: built 2026-11-02 (T-Ga4Rna follow-up). Reuses the `objective` grader and the open-scale
ladder maths of `benchmark-open-scale-design.md` (§3.1); family `bench/gen/cvrp_ladder.py`, reference
`bench/gen/reference/cvrp_server.py`.

## 1. Why CVRP

The capacitated vehicle routing problem is NP-hard and has no known optimum at 100-800 customers, so
no solver reaches a ceiling: a model's score is set by the algorithm it chooses and how well it uses
a short Python time limit. Known-solution tasks (key-value store, event reconciliation) saturate on
correctness; CVRP separates "any feasible routes" (a star), nearest-neighbour, savings, local search
and ruin-and-recreate annealing by percentages that keep growing. See `benchmark-ideas-backlog.md`.

## 2. Task

The subject builds a jsonl solver (same run contract as `opt-ladder`). `{"op":"solve","problem":
"cvrp","case":{"depot":[x,y],"capacity":Q,"customers":[[x,y,demand],...]}}` -> `{"ok":true,"answer":
[[c,...],...]}`: routes of customer indices `0..n-1`; every customer exactly once, route demand <= Q,
number of routes free. Objective: total Euclidean length including depot legs. Each `solve` has
`case_seconds = 10`. Late, malformed or infeasible answers score 0 for that case; the process is
restarted.

## 3. Ladder (deterministic, iteration budgets, never wall clock)

| Rung | Solver | Typical distance vs savings (n = 200) |
| --- | --- | --- |
| 0 trivial | one route per customer | ~ +500% |
| 1 nearest | nearest-neighbour fill | ~ +40-60% |
| 2 savings | Clarke-Wright parallel savings | 0 (reference) |
| 3 local_search | savings + relocate / swap / 2-opt / 2-opt* (granular, first improvement) | ~ -0.3..-3% |
| 4 strong | SISR (string-removal ruin and greedy-with-blinks recreate, SA acceptance), 500 iterations per customer from rung 3 | ~ -3..-6% |

Points (`ladder_points`): piecewise linear between rungs (rung k = k points); above rung 4 one point per
`max(last gap, 1% of the top value)`, so the scale is unbounded and stable. `par` = 4 (score 1.0),
`solved_points` = 2 (savings quality on average). A copied reference scores at least 4.

## 4. Instances and difficulty axes

Seeded generator: layout uniform / clustered / mixed, depot centre / corner / random, demands 1..30,
capacity set for a mean route of 6, 10, 16 or 24 customers, integer coordinates in `[0, 1000]`.
`concurrency` -> hidden cases (1..4); `scale` -> customers (100, 200, 400, 800); `traps` -> duplicate
locations and a customer whose demand equals Q. Cases are re-drawn until the ladder is strictly
improving. The cases and rung values are hidden in `probes.json`; SPEC.md states the problem, format,
limits and the scoring rule, never the cases.

## 5. Calibration and risks

- The top rung must keep ahead of an agent's 10 s of Python (lesson from `opt-ladder`, where an 8.5 s
  annealing matched a 1 s reference). SISR at 500 iterations per customer is about 100k iterations at
  n = 200 (about 10 s of reference CPU); raise it if haiku reaches par.
- Rank on raw points, not the capped score. Report the per-case spread; with 2-4 cases per task use
  several seeds.
- Language matters: a Python-only subject is limited to roughly 10^5 cheap moves in 10 s. That is the
  point (algorithm quality), but a faster language set would shift the scale; keep the language fixed
  within a comparison.
- Public benchmark instances (CVRPLIB) are not used because their best-known solutions are public.
