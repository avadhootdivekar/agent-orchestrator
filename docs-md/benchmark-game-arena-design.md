# `game-arena`: bots against a rated ladder, with a games plugin

Status: design (2026-10-29) · Ticket: `T-Ga4Rna-bench-game-arena` (epic `E-Bn7Dsc`) · Builds on
`benchmark-open-scale-design.md` §3.2 (rating), §4.2 (system), §11.2 (spec).

## 1. What this adds to the open-scale design

The open-scale design fixes the idea: the subject writes a bot, the grader plays it against reference bots of known
strength and scores the maximum-likelihood Elo. This document fixes what that design left open:

- a **games plugin**, so a new game or rule variant is a small module, not a new runner;
- a **dev pool and a held-out pool** of games, so tuning `ao` on the dev pool does not contaminate the final comparison;
- how a match is **timed, stopped early and rated**, so a task costs minutes and the number has an error bar;
- how the **frozen ratings** are produced and protected against going stale.

## 2. The games plugin

A game is a subclass of `bench.games.base.Game`, registered with `@register_game`. Everything else (referee, reference
bots, rating, grader, family) is generic.

| Method | Meaning |
|---|---|
| `key` | stable id of the game class and parameters, e.g. `othello-8`, `connectk-7x6k4g1` |
| `initial()` | starting state (immutable, hashable) |
| `to_move(s)` | 0 or 1 |
| `legal(s)` | list of moves, never empty unless `terminal(s)` |
| `apply(s, m)` | next state; **passes are handled inside** (a player who has no move is skipped) |
| `terminal(s)`, `result(s)` | result for player 0: 1, 0.5 or 0 |
| `heuristic(s, p)` | static evaluation for player `p`, used by greedy and alpha-beta references |
| `encode_state(s)`, `encode_move(m)`, `decode_move(x)` | JSON wire format (moves are ints or lists of ints) |
| `rules_md()` | the exact rules and the wire format of the state and the moves, for `SPEC.md` |
| `ladder()` | the reference bots, weakest first: `("random",)`, `("greedy",)`, `("ab", depth)`, `("mcts", playouts)` |

Rules of the plugin:

- `legal` never returns an empty list for a non-terminal state, so a bot is never asked to pass. This removes a class of
  protocol errors that measure nothing.
- States are immutable and hashable. The generic bots (MCTS, alpha-beta with a table) rely on it.
- `heuristic` is the only game-specific strategy knowledge in the harness. It is hand-written once per game and is
  deliberately simple; the ladder gets its strength from search depth, not from a tuned evaluation.
- A game is **parameterised**. `othello(size)`, `connectk(width, height, k, gravity)`, `breakthrough(size)` draw their
  parameters from the task seed, so a memorised engine for the standard game does not apply.

### Games shipped

| Pool | Game | Why |
|---|---|---|
| dev | Othello (6×6 or 8×8) | well known, deep, rich heuristics; rules validated by perft counts (8×8, depths 1-6) |
| dev | Connect-K with gravity (6×6/4, 7×6/4, 6×7/4, 9×7/5, 8×8/5) | cheap, parameterised, shows the plugin on a generated rule set |
| held-out | Breakthrough (7×7, 8×8) | simple rules, no draws, little published engine code |

The pools are in `bench/games/pools.py`, indexed by the `entities` level. Only configurations that passed the balance
check are listed. Free-placement Connect-K (an m,n,k-game) is supported by the plugin but not shipped: at shallow depth
it is mostly draws (50 of 60 mirror games at 7×7/5), which carries little information.

Adding a game is: one module under `bench/games/`, a rules test, and an offline rating run (§6). No grader change.

## 3. Protocol (jsonl, one process per task, reused across games)

- `{"op":"ping"}` → `{"ok":true}`.
- `{"op":"new","game":{"id":..., <params>},"you":0|1,"ms_per_move":N}` → `{"ok":true}`. Starts a game. `you` is the
  side the bot plays.
- `{"op":"move","state":{...},"legal":[...],"history":[...]}` → `{"ok":true,"move":<one of legal>}`.
  `state` is the game's JSON state (board, `to_move`, `plies`); `history` is every move so far, including the random
  opening. `legal` is sent so a rules misreading does not forfeit games; it does not make the game easy, because search
  still needs the bot's own simulation of the rules.

An illegal, malformed, late or missing move, or a crash, **forfeits that game**; the process is restarted for the next
game. The time limit per move is `ms_per_move` plus a 20% transport grace, measured by the grader around the request.

No engine is shipped to the subject. It writes its own rules, search and evaluation, and tests them by playing its own
versions against each other. This keeps the task "from scratch" and keeps subjects at parity.

## 4. Match, rungs and early stopping

- **Opening:** the first `opening_plies` plies are random legal moves from a per-pair seed. Each opening is played twice,
  once with each side, so colour advantage cancels and an opening book is useless. `opening_plies` is fixed (4) and
  stored with the ratings, so the ladder is rated on the same kind of position it is graded on.
- **Rungs** are played weakest first. A rung is `games_per_rung` games (a multiple of 2).
- **Early stop:** after a rung, if the subject scored at most `stop_score` (default 0.2), higher rungs are not played
  (they would only be harder). They are reported as `not played`, not as zeros.
- **Cost:** weak rungs (random, greedy) are instant. Only the top rungs use real time: a subject that beats most of the
  ladder spends about `games_per_rung × moves × ms_per_move` per strong rung.
- Games run serially by default (`max_parallel_games` = 1): the wall-clock limit applies to the subject, and parallel
  games would let load change a subject's strength. Reference bots use depth or playout budgets, never wall time, so
  they are deterministic.

## 5. Rating

Elo with the standard logistic, 400 points per factor of 10, random bot fixed at 0.

- A subject's rating is the maximum-likelihood rating given its results against the **frozen** rung ratings. Draws count
  one half.
- **Prior:** each played rung adds one virtual game at score 0.5, so 10 wins of 10 gives a finite rating a few hundred
  points above that rung, not infinity.
- **Interval:** the 95% interval comes from the observed Fisher information (the same likelihood), reported in `detail`.
- **Score:** `min(1, max(0, rating) / rating(par rung))`. `points` in the result is the rating itself (unbounded).
  `solved` means the rating reaches the `solved_rung` (default: the greedy rung plus 100).

## 6. Frozen ratings

- `ao-bench arena-rate --game <key>` plays a round robin among the reference bots (parallel, process pool) and fits the
  ratings with the same estimator and prior. The result is written to
  `src/agent_orchestrator/bench/games/ratings/<key>.json` with a **ladder version**: a hash of the game's rules version
  and its `ladder()`.
- The grader refuses a game whose stored ladder version differs from the live one (a changed bot or rule must be
  re-rated), so a stale number cannot be used silently.
- Ratings are **not fitted to any subject**. They are re-rated only when a reference bot or the rules change.

## 7. Dev pool and held-out pool

- `game-arena` draws from the dev pool; `game-arena-heldout` from the held-out pool. Both are the same code with a
  different pool tuple.
- Tuning `ao` templates or instructions uses the dev pool and the calibration seeds. Final comparisons add a held-out run
  with a seed that appears in no committed suite. Never tune against the held-out pool.

## 8. Difficulty vector

| Axis | Meaning | Levels 0..3 |
|---|---|---|
| `entities` | game parameters (board size, k) | smaller to larger |
| `traps` | unused (the random opening is the anti-memorisation device) | - |
| `concurrency` | games per rung | 4, 8, 12, 20 |
| `scale` | `ms_per_move` | 1000, 500, 250, 100 |

Par defaults to the strongest rung whose strength a Python subject can plausibly match with the same time (see risks).

## 9. Risks

- **Python reference speed** limits rung strength. The top rung must have compute comparable to the subject's, or it is
  beaten trivially (lesson from `opt-ladder`). Ratings are per language of the reference, not of the subject.
- **Elo noise:** with 8-12 games per rung the interval is wide. It is reported, and pairwise comparisons need repeats.
- **Degenerate variants:** drawn rules can be lopsided. `arena-rate` plays 200 mirror games of a mid-ladder bot
  (`ab2`) from random openings and stores the first-player score; a unit test requires it to be within 0.35-0.65 for
  every pooled game (candidates such as Connect-K 6×5/4, 8×6/4 and Breakthrough 6×6 were rejected on this).
- **Timing:** serial games, wall time measured around the request, 20% grace. The subject's own load (it may use all
  cores) is part of the subject.

## 10. As built (2026-10-29)

- Package `bench/games/`: `base` (plugin interface, registry), `othello`, `connectk`, `breakthrough`, `bots` (random,
  greedy, alpha-beta, MCTS; `BOTS_VERSION`), `referee`, `rating`, `ladder` (version stamp, stored ratings), `roundrobin`,
  `pools`. Grader `bench/arena.py`; families `game-arena` and `game-arena-heldout` in `bench/gen/game_arena.py`;
  reference bot `bench/gen/reference/arena_server.py` (needs `AO_BENCH_SRC`, since the process sandbox scrubs the
  environment; pass it through the grader's `artefact_env`).
- Reference bots are fast enough (Othello 8×8 depth 4: 0.6 s per game) that the ladder goes to depth 6 (Othello) and 8
  (Connect-K), deeper than the first sketch.
- Ratings come from `ao-bench arena-rate --pool dev|heldout --games-per-pair N --write`; the stored files carry the
  ladder version, the opening plies and the mirror-match first-player score.
- The grader plays serially, stops climbing once a rung is won at most 20% of the time, and has a wall budget
  (`budget_seconds`, default 3600 s) after which higher rungs are reported as not played.
