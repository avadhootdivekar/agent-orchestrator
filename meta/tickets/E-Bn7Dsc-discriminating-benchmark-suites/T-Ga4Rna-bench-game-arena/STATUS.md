# STATUS

- ID: `T-Ga4Rna-bench-game-arena`
- Updated At: 2026-10-29
- State: **In Progress** (calibration with anchors pending)

## This update
- By: Claude · Role: developer · Date: 2026-10-29 · Comment: **ACs 1-6 built; AC 7 (haiku/opus calibration) not run.**
  - Games plugin `bench/games/` with Othello (perft 1-6 matches published counts), Connect-K and Breakthrough; reference
    bots (random, greedy, alpha-beta, MCTS), referee, Elo MLE with a one-virtual-game prior, ladder version stamp.
  - `arena` grader and families `game-arena` (dev pool) / `game-arena-heldout`; `ao-bench arena-rate` + `make
    bench-arena-rate`; nine games rated (40 games per pair, 4 opening plies) and stored in `bench/games/ratings/`.
  - Othello 8x8 ladder: 0 / 63 / 365 / 593 / 732 / 935 / 1098 / 1152 Elo. Connect-K ladders compress at the top
    (depth 2 to 8 within about 60-100 Elo), so Connect-K will discriminate weakly; Othello and Breakthrough spread well.
  - Bench suite: 1330 passed, 2 failed. One failure was the trap catalogue (fixed: arena traps added); the other is
    `test_noop_subject_run_materialises_the_grading_dir`, a `cmake` toolchain sha256 mismatch in `toolchains.py` (not
    arena code; not checked against a clean `main`).
- By: Claude · Role: architect · Date: 2026-10-29 · Comment: opened. Design in
  `docs-md/benchmark-game-arena-design.md` (plugin interface, dev/held-out pools, early-stopping rungs, rating rule).
