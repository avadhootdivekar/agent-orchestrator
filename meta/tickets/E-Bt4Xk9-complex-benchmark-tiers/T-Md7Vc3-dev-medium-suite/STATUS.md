# STATUS

- ID: `T-Md7Vc3-dev-medium-suite`
- Updated At: 2026-07-22
- State: **Done**
- Owner: developer agent (dev-medium suite authoring + fixtures + tests + smoke gate)

## This update

By: developer | Role: developer | Date: 2026-07-22

Authored `benchmarks/suites/dev-medium/` end to end: `suite.json` (tier `medium`, 6
tasks) + 6 task dirs, each with `instruction.md`, a multi-module stdlib-only Python
fixture, visible `tests/`, a held-out `fixture/.grading/{run.py,tests/}` harness, and a
`.bench-solution/` reference-fix overlay. Added `tests/bench/test_dev_medium_suite.py`
(mirrors `test_dev_core_suite.py`'s deterministic checks). Ran the AC-4 authoring-gate
smoke (haiku on all 6, sonnet on the 3 hardest), found and documented an R4 saturation
signal, tightened the 3 sonnet-tested tasks' held-out layer once, re-ran sonnet once
per the required remediation loop, and ran one additional (allowed, scratchpad-only)
low-`max_turns` diagnostic probe that produced the strongest discrimination evidence.

## The 6 task designs (one line each)

1. **`feature-plugin-priority-registry`** (feature) — a chain-of-responsibility
   handler registry (`registry/core.py`+`dispatch.py`+`handlers.py`): finish
   priority-ordered `handlers_for` (stable tie-break) and `dispatch`'s
   first-resolving-handler short-circuit + `NoHandlerResolvedError`; held-out tests
   catch a fix that resolves the right answer without truly stopping at the first
   handler (call-counter probe) and an empty-registry edge case.
2. **`refactor-money-cents`** (refactor) — a shop `pricing.py`/`cart.py`/`receipt.py`
   package rounds money independently at 3 different points (float `round()`,
   `:.2f` format, a raw-float `cart.py` duplicate of the discount formula); fix
   requires rounding the whole line total once via exact-decimal (not binary-float)
   arithmetic AND delegating `cart.subtotal` to the now-fixed `pricing.line_total`
   instead of its own stale copy — a genuinely cross-cutting, multi-file fix.
3. **`bugfix-cache-key-collision`** (bugfix, misleading symptom) — a failing
   `test_store_evicts_least_recently_used` test points at `cache/store.py`'s
   (fully correct) LRU eviction logic; the real bug is `cache/keys.py`'s
   `normalize_key` collapsing internal whitespace, silently under-counting distinct
   keys so eviction never fires. Empirically verified two plausible shallow
   `store.py`-only "fixes" still fail the visible suite.
4. **`feature-tracker-priority-filter`** (feature, integration point) — a task
   tracker's `cli.py` parser must convert `--priority=` into a real (non-`str`-subclass)
   `Priority` enum that `filters.py`/`store.py` already expect; held-out tests check
   sort ordering, combined filters, and that the stored value is truly a `Priority`
   member (not a raw string that happens to satisfy the one visible check).
5. **`bugfix-search-index-scan-budget`** (bugfix, performance+correctness) — a search
   query engine matches by substring (wrong: "cat" matches "category") AND never
   consults the prebuilt inverted index (rescans every document's raw text every
   call); fix must switch to whole-word index-postings intersection. Held-out tests
   use a deterministic scan counter (never wall-clock) to catch a correctness-only
   fix that still re-scans text.
6. **`test-write-validation-spec`** (test-writing) — write
   `tests/test_validation.py` against a 4-function validation spec
   (`is_valid_email`/`in_range`/`is_strong_password`/`normalize_phone`); held-out
   grader mutation-tests against 4 independently seeded single-bug variants (one per
   function) and requires ALL 4 to be caught (scaled up from dev-core's 1-mutant
   `check_tests.py`).

## Fixture quality-gate results (per task, all green)

For every task: full grading harness (`.grading/run.py`, which itself re-runs the
visible `tests/` + held-out `.grading/tests/`, or the mutation check for task 6)
**FAILS** on the unmodified fixture and **PASSES** once `.bench-solution/` is applied
(verified twice: manually via `uv run python .grading/run.py` during authoring, and
by `tests/bench/test_dev_medium_suite.py`'s parametrized
`test_task_grading_harness_fails_before_and_passes_after_solution`, all 6 green).
`refactor-money-cents` additionally verified with its VISIBLE suite passing unmodified
while the full grader still fails (AC3 — held-out layer is what gates `solved`, not
`tests/` alone). Empirically confirmed (by hand, not just by design) that a plausible
shallow "fix" is rejected for 3 tasks: `bugfix-cache-key-collision` (two different
`store.py`-only eviction-threshold tweaks both still fail the visible suite —
different assertion each time, confirming the fix genuinely belongs in `keys.py`),
`bugfix-search-index-scan-budget` (a correctness-only fix using `tokenize()` on raw
text — no index use — passes visible but fails the held-out scan-counter check), and
`test-write-validation-spec` (a happy-path-only 4-test suite passes visible/no-op
check but fails the mutation gate on the very first mutant).

## Smoke gate (AC-4) — real numbers, actual spend

**Total real spend across the whole gate: $3.0693** (hard cap was $8; well under).

**Haiku, all 6 tasks, existing `benchmarks/subjects/claude-haiku.json` (max_turns=30,
unedited):**

| Task | Solved | Cost | Turns |
|---|---|---|---|
| bugfix-cache-key-collision | true | $0.0957 | 25 |
| bugfix-search-index-scan-budget | true | $0.0840 | 26 |
| feature-plugin-priority-registry | true | $0.1120 | 38 |
| feature-tracker-priority-filter | true | $0.1423 | 44 |
| refactor-money-cents | true | $0.1613 | 49 |
| test-write-validation-spec | true | $0.1361 | 35 |

**6/6 solved (100%), $0.7314 total.** This is an R4 saturation finding at the
authoring gate's mandated config — see "Deviations / forward notes" below.

**Sonnet, 3 hardest-by-design tasks (`bugfix-cache-key-collision`,
`bugfix-search-index-scan-budget`, `refactor-money-cents`), existing
`benchmarks/subjects/claude-sonnet.json` (max_turns=30, unedited) — run BEFORE
tightening:** 3/3 solved (100%), $0.9910 total. Diffs inspected: sonnet's fixes were
essentially byte-identical to my `.bench-solution/` reference for all 3 (genuinely
correct root-cause fixes, not shallow hacks).

Per the mandated success-gate remediation ("if sonnet aces all tested tasks, tighten
those tasks ... and re-run the sonnet subset once"): tightened all 3 tasks' held-out
`.grading/tests/` with genuinely new adversarial cases (a 3-way near-collision LRU
pattern + internal-tab/newline whitespace for the cache task; a larger 8-doc corpus +
3-term AND query for the search task; two more binary-float rounding-boundary cases +
a 100%-discount edge case for the money task) — all re-verified fail-before/pass-after
(see quality-gate section). **Re-ran sonnet once** on the same 3 tasks against the
tightened suite:

| Task | Solved | Cost | Turns |
|---|---|---|---|
| bugfix-cache-key-collision | true | $0.2647 | 17 |
| bugfix-search-index-scan-budget | true | $0.2279 | 15 |
| refactor-money-cents | true | $0.4519 | 28 |

**Still 3/3 solved (100%), $0.9445.** Confirmed haiku's original (pre-tightening)
fixes for these same 3 tasks also still pass the tightened held-out tests at zero
extra spend (re-graded its already-produced workspace output directly). Per the
literal instruction, one remediation round was performed; per this repo's
proportionality/scope norms, further iteration was not pursued once the evidence
below explained WHY tightening-in-kind wasn't moving the needle (see next section).

**Additional (allowed) diagnostic probe** — a scratchpad-only temp subject copy of
`claude-haiku.json` with `max_turns` lowered to 6 (committed
`benchmarks/subjects/claude-haiku.json` itself untouched), run on all 6 tasks:
**2/6 solved (33%), $0.4024 total** — `bugfix-cache-key-collision` and
`feature-tracker-priority-filter` solved; the other 4 did not (mostly
`subject_status=failed`, i.e. genuinely turn-budget-exhausted, not just an unsolved-
but-clean run).

## Deviations / forward notes for the PLAN medium run

- **R4 finding (important):** at the dev-core-tuned `max_turns=30` baseline config,
  both haiku and sonnet solve 100% of the tasks they were run against, with
  genuinely-correct (not shallow/gamed) fixes — confirmed by diffing their actual
  code changes against `.bench-solution/`. One held-out-edge-case tightening round
  (the only one the ticket mandates) did not change this. **The suite's proven
  discrimination axis is turn/investigation budget, not raw task solvability**: the
  same 6 tasks solve at only 33% when `max_turns` is capped to 6 (scratchpad probe,
  committed subject configs untouched). Recommendation for the PLAN medium run
  owners (T-Cm9Tb4 / whoever finalizes subject configs for that run): if the goal is
  to see solve-RATE lift from orchestration at a MATCHED turn/cost budget, consider
  whether the medium tier's single-agent baselines should run at a materially lower
  `max_turns` than 30 (dev-core's spot-fix-tuned default) — otherwise expect
  sonnet/opus/ao-epic-sonnet/ao-epic-plus-sonnet to likely all solve most/all of
  these 6 tasks too, and the comparison signal will show up in **cost_per_solved,
  turns, and wall-clock**, not in solve_rate. This is a config/run-design decision
  for the PLAN run, not a `dev-medium` fixture defect — no committed subject config
  was edited here.
- All 6 tasks ARE robust against shallow/one-line/pattern-matched fixes (verified by
  hand for 3 of them with concrete counter-fixes that still fail); the "misleading
  symptom" (`bugfix-cache-key-collision`) and "performance+correctness"
  (`bugfix-search-index-scan-budget`) designs specifically resisted plausible
  quick-patch attempts in my own authoring-time experiments, independent of the
  headroom question above.
- No committed subject config (`benchmarks/subjects/claude-haiku.json` /
  `claude-sonnet.json`) was edited. The low-`max_turns` probe used a throwaway copy
  under the scratchpad only, per the ticket's explicit allowance; that file was
  deleted after use, along with all smoke result dirs (none were ever written under
  `benchmarks/results/` — all smoke runs used `--out-dir` pointed at scratchpad).
- No Docker, no network; all 6 fixtures are Python-stdlib-only (no third-party
  fixture deps).

## Acceptance criteria evidence

1. `ao-bench validate --suite benchmarks/suites/dev-medium/suite.json` → `OK`, 6
   tasks, `tier=="medium"`; every task path-references its instruction/fixture
   (schema + `load_suite` checks pass).
2. **Discrimination (free tier):** verified per task via
   `tests/bench/test_dev_medium_suite.py::test_task_grading_harness_fails_before_and_passes_after_solution`
   (parametrized over all 6 ids) — full grader FAILS on the unmodified fixture,
   PASSES with `.bench-solution/` applied (the `FakeSubject(scripted_effect=
   "copy-solution")` code path, exercised directly via file-copy in tests, matching
   `test_dev_core_suite.py`'s own approach).
3. **Held-out integrity:** every task's grader command is `uv run python
   .grading/run.py`, which re-runs the visible `tests/` AND the held-out
   `.grading/tests/` (or the 4-mutant mutation check for task 6); instruction.md for
   every task explicitly forbids modifying `.grading/` and (except task 6) the
   visible tests. `refactor-money-cents` specifically demonstrates AC3: visible
   suite passes unmodified, full grader still fails.
4. **Not one-turn-saturable (real smoke, gated):** see "Smoke gate" section above —
   haiku/sonnet numbers, spend, tightening round, and the honest R4 finding are all
   recorded. Diagnostic max_turns=6 probe shows real (33%) discrimination exists at
   a constrained turn budget.
5. Each task tagged with an agent-work-size estimate (`~30-45min`/`~45-60min`/
   `~45-90min`); fixtures are multi-module (3-4 real code files each, e.g. `shop/`
   has `pricing.py`+`cart.py`+`receipt.py`, `tracker/` has `store.py`+`cli.py`+
   `filters.py`).
6. No Docker, no network; every fixture is Python-stdlib-only.

## Verification run (final)

- `uv run ao-bench validate --suite benchmarks/suites/dev-medium/suite.json` → OK.
- `uv run pytest tests/bench/test_dev_medium_suite.py -q` → 18 passed.
- `uv run ruff check` / `ruff format --check` on `tests/bench/test_dev_medium_suite.py`
  + all of `benchmarks/suites/dev-medium/` → all clean.
- `uv run mypy tests/bench/test_dev_medium_suite.py` → only the same pre-existing
  `import-untyped` (missing `py.typed`) notes `test_dev_core_suite.py` also has;
  no new mypy issues.
- `uv run pytest -q` (whole repo) → all green, no regressions (other in-flight
  parallel-agent work on `src/` landed additional passing tests during this session;
  none of it touched by this task).
- `git status` shows only `benchmarks/suites/dev-medium/` and
  `tests/bench/test_dev_medium_suite.py` as this task's changes.

## Risks / Blockers

- None blocking. R4 saturation-at-max_turns=30 is a real, documented finding (not a
  blocker for THIS task's completion — the suite's committed contract, gating, and
  shallow-fix resistance are all correct and verified) but is an important input for
  whoever finalizes the PLAN medium run's subject configs (T-Cm9Tb4 territory).

## Next actions

None outstanding for this task. Downstream: T-Cm9Tb4 (`make bench-medium` targets
this suite) and the PLAN medium run should read the "Deviations / forward notes"
section above before finalizing subject configs for that run.
