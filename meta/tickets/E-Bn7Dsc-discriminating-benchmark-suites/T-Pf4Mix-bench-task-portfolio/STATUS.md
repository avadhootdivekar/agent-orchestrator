# STATUS

- ID: `T-Pf4Mix-bench-task-portfolio`
- Updated At: 2026-10-24
- State: **Done**

## This update
- By: Claude · Role: developer · Date: 2026-10-24 · Comment: **`T-Pf4Mix` done.** All 6 ACs met.
  - New pieces: toolchains (Go and Node on the agent host); the `exercism` provider, grader and importer;
    generated families `order-book`, `rules-engine`, `bug-hunt`, `algo-puzzle` and `algo-hard`; the
    `ao-bench portfolio` cross-suite report.
  - Every spec-complete generated family saturated (haiku and opus 1.0 on all 24 calibration tasks).
  - Exercism discriminates only with hidden tests: haiku 28, sonnet 37, opus 46 of 48. With the same 48 tasks
    and the tests visible, all four subjects (including the overseer) solve 48/48. Grader output shows the
    hidden-test failures are conventions (message text, API names), so visible tests are now the default.
  - The overseer adds 16-17× cost and no measurable quality.
  - Spend $147.58 of $300. Results: `benchmarks/README.md` (T-Pf4Mix entry); design: `docs-md/benchmark-task-portfolio.md`.
  - Commits on `ad/enhancements-20oct`: b2dc911 … (toolchains → exercism → families → visible tests → portfolio → results).
  - Gates: ruff and mypy clean. Full pytest: 10,369 passed, 1 failed; the failure is a timing-sensitive
    blackbox determinism test, run under bench load, that passes 3/3 alone.
- By: Claude · Role: architect · Date: 2026-10-24 · Comment: design doc written; toolchains landed (`b2dc911`, agent shell
  verified with go1.26.10 / node v22.23.4 for $0.002).
