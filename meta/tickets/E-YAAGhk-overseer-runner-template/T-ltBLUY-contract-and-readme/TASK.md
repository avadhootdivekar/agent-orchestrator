# TASK: T-ltBLUY-contract-and-readme

## Metadata
- Task ID: `T-ltBLUY-contract-and-readme`
- Epic ID: `E-YAAGhk-overseer-runner-template`
- Owner: developer
- Created: 2026-09-26
- Last Updated: 2026-09-26
- Status: Draft
- Estimate: 2 days (16 h)

## Requirements Mapping
- Requirement IDs: FR-3, FR-9, FR-14, FR-18 (contract text)
- Design: `docs-md/overseer-runner-hld.md` §7, §8.2, §8.3, §13.3, §13.4, §17

## Description
1. Write `overseer-contract.md.tmpl`. It is rendered per run and is AUTHORITATIVE, mirroring
   `routed-runner/breakdown-contract.md.tmpl`'s discipline. Contents:
   - the run's paths
   - the id patterns (`wJJ-NN-<slug>`, `ck-JJ`, the fixed tail ids, and the expander patterns,
     marked FR-15 / "only if `max_expanders_per_wave` > 0")
   - the exact JSON entry shapes from §13.3 for unit, next checkpoint, and tail, with `{{ instance_dir }}` and `{{ params.overseer_effort }}` substituted
   - the kind→agent/instruction table, which **must equal** the config `kind_map`
   - the brief, breadcrumb, verdict, and hold-request schemas (§13.4)
   - the stage table and allowed decisions (§8.2)
   - the signal types and response rules
   - the `overseer_model` rule: when the config `overseer_model` is non-empty, add `"model"` to
     every `ck-*` entry
   - "**Hard rules (a violation fails the checkpoint and halts the run)**", listing OV-R1…OV-R16
     and OV-R13c verbatim with rule ids
   - a "self-check" section telling emitters to run
     `<python_bin> <tool> ckpt-check --dry-run --task-id <id> …` (and `intake-check` for intake)
     and fix every violation before exiting
   - "if `check-result.json` from a previous attempt exists in your checkpoint dir, read it first"
   - a `contract_version: 1` marker line
2. Write `README.md` with these sections: summary; the DAG shape (static head, emitted everything
   else, and why there is no static tail, D8); Required agents; Params; "Tunable vs fixed"
   (config constants are editable pre-run); "Budget stages and graceful degradation" (the $2000
   example); "Limits & breaker rationale" (why the backstop is hard at 100% and not
   `recommend`; why `runaway-fanout` is hard; latch plus the unit gate); "Human-in-the-loop (hold)"
   (the `HOLD:` failure, answer then resume, and the `consecutive_failures` bump); "Resuming after
   a budget trip" (a plain resume runs only the close-out; continuing needs both the override file
   and `--extend-breaker`); "Engine gaps designed around" (G1–G4, and G5 fixed in engine);
   "Completion marker" (`outputs/final/closeout.md`); "Parallelism & isolation" (the
   `max_parallel` overshoot bound, isolation opt-in as in routed-runner); "Re-rendering the
   tool for a fix" (the path verified in T-eGXqXH); "Preflight" (`python3 --version` ≥ 3.11 on
   the service PATH); "Recommended `--autocompact`" (inline: `manager` at 500000 because the
   overseer reads a lot, and the rest as routed-runner. No asset is shipped, per NFR-X9); and the
   "global `--model` clobbers per-agent/per-task model" caveat.

## Acceptance Criteria
1. The rendered contract (default params) contains every rule id from OV-R1 to OV-R16 plus
   OV-R13c, the literal `contract_version: 1`, the three entry shapes as valid JSON blocks
   (extracted and parsed by the test), and no unrendered `{{`.
2. Drift guard: a test parses the contract's kind→agent/instruction markdown table and asserts it
   equals the rendered config `kind_map`.
3. The JSON shapes extracted from the rendered contract pass `TaskSpec(**entry)` validation after
   `<slug>`/`<id>` placeholders are substituted with sample values. The hook names they reference
   exist in the rendered `workflow.json` `hooks`.
4. README section-presence tests, one assertion per section heading listed in the Description,
   mirroring `test_readme_documents_parallel_isolation_section`.
5. The README's breaker table matches the rendered `workflow.json` breaker ids and thresholds (the
   test compares ids).
6. A `reviewer`-agent review is recorded in STATUS.md.

## Risks
- Contract prose drift versus the checker. Mitigation: ACs 2 and 3. The checker (T-HPJcc6)
  reuses the same rule-id strings.

## Dependencies
- T-eGXqXH (the template layout and config).

## Pseudocode / Algorithm
```text
Structure mirrors routed-runner/breakdown-contract.md.tmpl: Paths → Ids → Shapes → Schemas →
Stages/decisions → Signals → Hard rules → Self-check.
```

## Schemas / Interface Notes
- All from §13.3 and §13.4. No new schema is invented in this task.

## Handoff Boundary
- Upstream: T-eGXqXH.
- Downstream: T-5ZzAZp (the instructions reference the contract), T-HPJcc6/T-tAKBBB (rule ids).

## Artifacts
- `overseer-contract.md.tmpl`, `README.md`; tests appended to `tests/test_builtin_overseer_runner_assets.py`
