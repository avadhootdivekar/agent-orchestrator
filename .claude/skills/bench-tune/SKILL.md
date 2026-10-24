---
name: bench-tune
description: Tune ao-bench difficulty knobs so generated suites discriminate between subjects, without fitting the instrument to a model or a desired ranking. Use when a suite saturates (everyone scores ~1.0) or floors (everyone ~0), when asked to calibrate, raise/lower difficulty, or pick a difficulty vector before a ranking campaign.
---

# Tuning ao-bench difficulty (and not fitting it)

Reference: `docs-md/benchmark-discriminating-suites-hld.md` §2c, §7 and the worked example in ticket
`meta/tickets/E-Bn7Dsc-discriminating-benchmark-suites/T-Cb8Rk2-bench-calibrate-and-rank/`. Read the
`benchmarking` skill first (parity, env isolation, real money).

## The one distinction that matters
- **Tuning** changes the instrument so it *can* tell subjects apart, using a rule written down before results
  and applied to every subject alike (a global difficulty vector, chosen on anchors, on seeds that are never
  ranked).
- **Fitting** changes the instrument *after seeing who wins* — dropping instances a model failed, re-weighting
  the probe group where a favoured subject is strong, moving the pass threshold, re-running until the order
  "looks right", editing prompts/bundle for one subject. Any of these makes the ranking an artefact.
- **Validity fixes** are a third category and are allowed at any time: the hidden reference fails its *own*
  probes, a probe cannot be executed, or the harness errors. Justify every grader/generator change by a
  reference or harness failure, never by a subject's score, and say so in the commit.

## Knobs (and what each one moves)
| Knob | Where | Effect | Tune it? |
|---|---|---|---|
| `entities` 0-3 | `ao-bench gen --difficulty` | 2/3/4/6 entities (+M:N from 3) — spec size, core probe count | yes (global) |
| `traps` 0-3 | same | 1/2/4/6 sampled traps (idempotency, pagination stability, lost update, casefold, ...) | yes — usually the most discriminating axis |
| `concurrency` 0-3 | same | parallel workers in race probes | yes, carefully (races are noisier) |
| `scale` 0-3 | same | perf sizes `[100,500]/[100,1000]/[100,10000]/[100,10000,100000]` | keep <= 2; perf is timing-noisy and only 15 % of the score |
| families / languages | `--family`, `--languages` | problem shapes; TS/Go need the agent host to have Node>=22/Go (not true here, 2026-10) | balance, don't cherry-pick |
| `--count`, seeds | `--count`, `--seed` | instances per family | fresh seeds for ranking |
| grader weights / `pass_threshold` | `blackbox` defaults 0.5/0.35/0.15, 0.8 | what "solved" means | **no** — freeze; changing them after results is fitting |
| parity bundle (`max_turns`, effort, env) | `benchmarks/parity/default` | same for every subject | only before spend, record it |
| subject configs | `benchmarks/subjects/*.json` | the thing being measured | not during a ranking cycle |

## Protocol
1. **Pre-register** in the ticket (commit it before spending): anchors, families/languages, seed ranges
   (calibration vs ranking), acceptance rule, max rounds, what is frozen.
2. **Generate** a calibration suite at the candidate vector with calibration seeds:
   `uv run ao-bench gen --out benchmarks/suites/gen-calib-v<n> --id gen-calib-v<n> --family crud-relational
   --family job-queue --count 2 --languages python --seed <calib-base> --difficulty entities=3,traps=3,concurrency=2,scale=2`
3. **Reference self-check (mandatory, free):** the hidden reference must score **1.0** on every instance (not merely `solved`: 0.85 still passes the 0.8 threshold and once hid a broken perf group) —
   `make bench-refcheck SUITE=benchmarks/suites/gen-calib-v<n>/suite.json` (fake `copy-reference` subject,
   Docker). Anything below N/N is a generator/grader bug: fix it (validity fix) before any agent spend.
4. **Anchors only:** run the cheapest and the strongest bare subjects (e.g. haiku + opus) at r=1:
   `uv run ao-bench calibrate --suite <calib suite> --subject benchmarks/subjects/claude-haiku.json
   --subject benchmarks/subjects/claude-opus.json --repeats 1 --cost-budget-usd 12 --out-dir benchmarks/results`.
   Never run the subjects you will rank most closely (e.g. sonnet, ao) during calibration.
5. **Decide by the pre-registered rule** (e.g. pooled anchor pass rate in [0.2, 0.8], or anchors' mean scores
   differ by >= 0.10 with neither > 0.95). Too easy → raise axes toward max (traps first); too hard → lower
   traps. Record every round (vector, per-instance scores per probe group, cost, decision) — including rejected
   ones. Stop at the round limit and report saturation honestly rather than touching the grader.
6. **Freeze** the vector, then generate the ranking suite with *fresh* seeds (`grep -rn '"seed"'
   benchmarks/suites` first) and run its reference self-check. Never inspect or filter ranking instances by
   result; every generated instance is run and reported.

## Fitting checks to report with any ranking
- Which probe group carries the between-subject differences (`comparison.md` → Cost & efficiency → probe
  groups). Differences driven by `perf` alone are weak evidence.
- Repeat variance vs between-subject differences (if repeats exist): a gap smaller than a subject's own
  repeat spread is noise.
- Per-family consistency: does the order hold in each family, or does one family produce it?
- Was any knob moved after a non-anchor subject ran? (Must be "no".)
- Did calibration instances leak into ranking? (Seeds must differ.)

## Before building another family: what does and does not discriminate (T-Pf4Mix, 2026-10-24)
- **Spec-complete + locally runnable = saturated, whatever the depth.** Haiku and opus both scored 1.0, at the
  maximum vector, on every family tried:
  - order-book: matching engine, stops, FOK, funds;
  - bug-hunt: 5 or 8 planted faults;
  - rules-engine: a 10k-character pricing spec;
  - algo-puzzle: 6 problems, seeded variants, 200k inputs;
  - algo-hard: story-form slope trick, suffix automaton, combinatorics.

  Haiku often costs $0.02–0.20 per task. More rules, traps or faults raise cost and time, not failure rate. Do not
  expect a new family of this kind to fix saturation.
- **Hidden tests can measure guessing.** On the same 48 Exercism tasks, haiku solved 28 with hidden tests and 48
  with visible tests; sonnet and opus solved 48/48 with visible tests. The spread came from API names that appear
  only in the tests (empty C++ stub namespaces) and from misleading stubs. Before attributing a spread to
  capability, read the failing tasks' grader output: compile errors against unknown names mean under-specification.
- **Remaining candidates for real spread** (unproven): convention inference that a competent human could do from the
  materials given; research-level problems; long-horizon multi-component work; tasks without a local oracle.
- Use `ao-bench portfolio` item-difficulty bands to see where a suite discriminates. The bands come from results, so
  never select or re-weight tasks by them.
