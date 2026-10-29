# TASK: T-Ga4Rna-bench-game-arena

## Metadata
- Task ID: `T-Ga4Rna-bench-game-arena`
- Epic ID: `E-Bn7Dsc-discriminating-benchmark-suites`
- Owner: Claude (architect/developer) for avadhoot
- Created: 2026-10-29
- Last Updated: 2026-10-29
- Status: In Progress
- Estimate: 3 days

## Requirements Mapping
- Follow-on of `T-Os5Lad-bench-open-scale` (design §3.2, §4.2, §11.2): the rating mechanism and the `game-arena` system.

## Description
Owner ask (2026-10-29): build the `game-arena` system with a **games plugin**, so new games and rule variants are cheap to add
later. The subject writes a game-playing bot from a rules spec; the grader plays it against a ladder of frozen-rating reference
bots and scores the maximum-likelihood Elo (unbounded). Design: `docs-md/benchmark-game-arena-design.md`.

## Acceptance Criteria
1. A `Game` plugin interface and registry (`bench/games/`), with Othello and Connect-K (dev pool) and Breakthrough (held-out
   pool). Rules are checked (Othello perft, win/pass/terminal tests) and every game's spec text states the wire format.
2. Game-agnostic reference bots (random, greedy, alpha-beta, MCTS), deterministic from a seed, and a ladder per game.
3. A referee/match runner (seeded openings, alternating colours, illegal move / timeout / crash = forfeit) and an Elo
   maximum-likelihood fit with a documented prior.
4. An `arena` grader (new grader type) and the families `game-arena` (dev pool) and `game-arena-heldout`; a generated suite
   builds, validates and grades end to end with a process sandbox.
5. Ratings are frozen per game class from an offline round robin (`ao-bench arena-rate`), stamped with a ladder version the
   grader checks.
6. Unit + e2e tests (fixed seeds, fake bots); lint, types and the bench test suite stay green.
7. Calibration with anchors haiku and opus (design §5) if budget allows; otherwise tracked as a follow-on.

## Risks
- Wall-clock timing noise: games run serially by default; reference bots use node/depth budgets, not time.
- Python reference speed bounds the top rung; compare within a language.
- A degenerate or unbalanced rule variant: balance check on generated variants (first-player score of the MCTS bots).

## Dependencies
- `T-Os5Lad-bench-open-scale` (objective grader, open-scale scoring conventions).

## Handoff Boundary
- Upstream: design `docs-md/benchmark-open-scale-design.md` §3.2/§4.2/§11.2.
- Downstream: calibration campaign (`bench-tune` skill) once ratings are frozen.
