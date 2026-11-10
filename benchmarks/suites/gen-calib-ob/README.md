# gen-calib-ob

Generated suite (Tier 1, behavioural-spec tasks) -- see
[`docs-md/benchmark-discriminating-suites-hld.md`](../../../docs-md/benchmark-discriminating-suites-hld.md)
and [`docs-md/bench-probe-dsl.md`](../../../docs-md/bench-probe-dsl.md).

Regenerate (byte-identical):

```
ao-bench gen --out <suite dir> --id gen-calib-ob --family order-book --count 2 --languages python --seed 7401 --difficulty entities=3,traps=3,concurrency=2,scale=2 --tier medium --parity ../../parity/default
```

## Tasks

| Task | Family | Language | Seed | Difficulty |
|---|---|---|---|---|
| `order-book-py-s7401` | order-book | python | 7401 | entities=3, traps=3, concurrency=2, scale=2 |
| `order-book-py-s7402` | order-book | python | 7402 | entities=3, traps=3, concurrency=2, scale=2 |

## How it works

- Each task's `source` (`type: generated`, family, seed, difficulty, language) is all that is
  stored. `materialize_workspace` calls the generated provider, which writes the agent-visible
  skeleton + `SPEC.md` into the run's `repo/` and the grader-owned `instance.json`, `probes.json`
  and Python `reference/` into the run's grading dir (never under the agent workspace).
- Grading is `grader.type: blackbox`: the artefact is started with `run.sh` (in the per-language
  Docker image, or the tagged `process` fallback) and driven with the hidden probes. Score =
  0.50 core + 0.35 trap + 0.15 perf; `solved` needs every core probe and score >= 0.8.
- `stack` lists the third-party dependencies per language (`stack.<language>.deps`); it is copied
  into each task's `source.deps` (skeleton manifest + `SPEC.md` allow-list) and into
  `grader.sandbox.stack` (what the sandbox installs), so all three always agree.
- Rotate seeds (`--seed`) per campaign; keep a few anchor instances fixed (HLD section 7).
