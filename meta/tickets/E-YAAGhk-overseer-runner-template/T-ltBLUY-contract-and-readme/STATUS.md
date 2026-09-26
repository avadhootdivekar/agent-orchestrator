# STATUS

- ID: `T-ltBLUY-contract-and-readme`
- Updated At: 2026-09-26
- State: Done — reviewed, approve with nits, both warnings fixed
- Owner: developer

## This update
- Ticket created by the architect design pass (Rev 2, after the Phase-4 consultations). Sprint: S1 (after T-eGXqXH).

By: architect · Role: architect · Date: 2026-09-26 · Comment: Created from `docs-md/overseer-runner-hld.md` Rev 2. The ACs are pass/fail and agent-executable, and the task is sized ≤3 days. Not started.

By: developer · Role: developer · Date: 2026-09-26 · Comment: Implemented both deliverables.
- `overseer-contract.md.tmpl` (new): paths, id patterns, the Unit/Next-checkpoint/Tail/Expander
  emitted-entry shapes (HLD §13.3, `{{ instance_dir }}`/`{{ params.overseer_effort }}` rendered),
  the kind→agent/instruction table (byte-identical content to `overseer-config.json.tmpl`'s
  `kind_map`), the brief/breadcrumb/verdict/hold-request schemas (§13.4), the budget stage table
  and allowed decisions (§8.2), the 10 signal types and response rules (§8.3), the
  `overseer_model` rule, the "Hard rules" section (both the 17 rule ids the shipped tool actually
  raises today — grepped from `tools/overseer_tool.py`, not retyped from the HLD — and the 17
  forthcoming `OV-R1`..`OV-R16`+`OV-R13c` checker rules copied verbatim from HLD §13.3), the
  self-check section (phrased as forward-looking — `ckpt-check`/`intake-check --dry-run` don't
  exist yet), and the `contract_version: 1` marker.
- `README.md` (new): all 16 required sections (summary, DAG shape/no-static-tail rationale,
  required agents, params, tunable-vs-fixed, the $2,000 budget-stage walkthrough, breaker
  rationale incl. the latch/unit-gate interaction, hold, resuming after a budget trip (both the
  close-out and extend+override paths, with the real `--extend-by-same`/`--extend-by-seconds`
  flags verified against `cli.py`, not just `--extend-breaker` alone), engine gaps G1-G5,
  completion marker, parallelism/isolation, re-rendering for a fix, preflight, recommended
  `--autocompact` (manager overridden to 500000 for this template specifically), and the global
  `--model`/`AO_MODEL` clobber caveat).
- `template.yaml`: added exactly one `files:` entry (`overseer-contract.md.tmpl` →
  `overseer-contract.md`), per this ticket's "Review handoff from T-eGXqXH" section. No other line
  changed (diff verified).
- Tests: appended 20 new test functions to `tests/test_builtin_overseer_runner_assets.py`
  (28 pre-existing → 47 total after the rename below removes one function name; net +19)
  covering AC1-AC5 (rule-id presence incl. the OV-prefix/bare-prefix split, no-unrendered-token
  check, the three entry shapes parsed + validated via `TaskSpec(**entry)` + hook-name
  cross-check against rendered `workflow.json`, the kind-map drift guard, all 16 README section
  headings, and the breaker-id/threshold drift guard against rendered `workflow.json`/
  `overseer-config.json`).
- **Judgment call (disclosed, see "Deviations" below)**: two of T-eGXqXH's own pre-existing tests
  in this file were temporal scope-boundary guards that named THIS task as their trigger
  (`test_no_overseer_contract_tmpl_or_instructions_dir_created_yet`'s docstring literally says
  "`overseer-contract.md.tmpl` (T-ltBLUY) ... must NOT be created by this task" — "this task"
  meaning T-eGXqXH). Landing this ticket's own mandatory `files:`-entry requirement necessarily
  flips both assertions. Updated `test_template_yaml_files_shape` (4→5 files, `overseer-contract.md`
  entry) and narrowed/renamed the guard test to `test_no_instructions_dir_created_yet` (drops the
  now-obsolete contract-file assertion; `instructions/` still correctly guarded for T-5ZzAZp). No
  other existing test's body was changed.

By: dev-epic · Role: manager · Date: 2026-09-26 · Comment: Independently re-verified: read both
new files in full (332 + 253 lines) against the design and the actual tool source, confirmed
`template.yaml`'s diff is exactly the one new `files:` entry, re-ran the test file (47 passed) and
the full suite (4209 passed/8 skipped/0 failed) myself, confirmed ruff/mypy clean.

By: reviewer · Role: reviewer · Date: 2026-09-26 · Comment: **Verdict: approve with nits, no
blocking issues.** Independently grepped `overseer_tool.py` and confirmed all 17 "currently
enforced" rule ids the contract lists genuinely match what the tool raises today (none missing,
none extra); compared all 17 "forthcoming" `OV-R1`-`OV-R16`/`OV-R13c` descriptions word-for-word
against HLD §13.3/§8.3 (faithful, not loosened); confirmed the kind→agent/instruction table is
row-for-row identical to `overseer-config.json.tmpl`'s `kind_map`; independently verified every
README factual claim against real code rather than trusting the design doc (the re-render/
`--force` claim against `cli.py`/`templates/__init__.py`, the `--extend-breaker
--extend-by-same`/`--extend-by-seconds` CLI syntax against `cli.py`'s actual `typer.Option`
definitions, the `breakers.py:603` latch citation — confirmed byte-accurate, the `--autocompact`
180000/500000 numbers against `routed-runner/README.md`'s own section); sanity-checked the two
touched pre-existing tests and confirmed the edit is minimal with no other assertion weakened;
reproduced the full test run (47 passed, full suite 4209 passed/8 skipped/0 failed) and ruff/mypy
results independently.
**Two Warnings, both fixed by dev-epic:**
1. **W1 (fixed)**: the README's "Preflight" section didn't disclose that the template isn't
   runnable end to end yet (`intake`'s `post_hook` wires to `ov-intake-check`, which doesn't exist
   as a subcommand until `T-HPJcc6`/`T-tAKBBB` land) — the contract itself already said this in its
   own "Self-check" section, but an operator reading only the README wouldn't see it before their
   first `ao run`. **Fixed**: added a "Current epic status: not yet runnable end to end" paragraph
   to the top of "Preflight", cross-referencing the contract's own caveat. Re-verified: 47 passed,
   no test broke.
2. **W2 (fixed)**: `STATUS.md` claimed "27 new tests" / "20 pre-existing + 27 new"; the reviewer's
   own `git diff`/`grep -c '^def test_'` count showed 28 pre-existing → 47 total (20 added, 1
   removed via the `test_no_overseer_contract_tmpl_or_instructions_dir_created_yet` →
   `test_no_instructions_dir_created_yet` rename), i.e. **19 net new**, not 27. **Fixed**: corrected
   the count above and in the epic `STATUS.md` rollup. The "47 total" and full-suite "4209 passed"
   figures were already numerically correct; only the "new tests added" figure was wrong.

## Evidence
- `.venv/bin/pytest -q tests/test_builtin_overseer_runner_assets.py -v` → `47 passed` (28
  pre-existing + 20 new − 1 renamed = 47; 2 pre-existing tests updated per the disclosed judgment
  call above, 0 deleted).
- Full repo suite: `.venv/bin/pytest -q` → `4209 passed, 8 skipped, 0 failed` (zero regressions
  vs. the pre-task baseline of 4190 passed/8 skipped after T-C6uQJW).
- `.venv/bin/ruff check` + `.venv/bin/ruff format --check` on the touched test file → clean (one
  formatting pass applied by `ruff format` for line-length wrapping in the new helper/assert
  lines).
- `.venv/bin/mypy src` → clean net of 4 pre-existing, unrelated errors in `_version.py` (a
  build-generated file, untouched by this task). `mypy` run directly against `tests/` (not part
  of this repo's CI gate, which only runs `mypy src`) shows a large, pre-existing,
  environment-wide `import-untyped` condition across ~136 test files (784 errors) because
  `agent_orchestrator` ships no `py.typed` marker — confirmed pre-existing and out of this task's
  scope (not introduced by, or specific to, the two files this task touches).
- `git diff -- src/agent_orchestrator/templates/builtin/overseer-runner/template.yaml` → exactly
  the one new `files:` entry, nothing else changed.
- Code/docs: `src/agent_orchestrator/templates/builtin/overseer-runner/overseer-contract.md.tmpl`,
  `src/agent_orchestrator/templates/builtin/overseer-runner/README.md`,
  `src/agent_orchestrator/templates/builtin/overseer-runner/template.yaml`.
- Tests: `tests/test_builtin_overseer_runner_assets.py`.

## Risks / Blockers
- None outstanding. Both reviewer warnings are fixed (see above).
- Forward note for `T-HPJcc6`/`T-tAKBBB`: the contract's "Hard rules" section is now the frozen
  source of truth for every `OV-R1`..`OV-R16`/`OV-R13c` rule id and one-line description — build
  the checker's rule ids and messages to match this file exactly (a later drift-guard test is
  expected to enforce this, per the ticket's own risk note). The reviewer also suggests a
  byte-identity test between the checker's emitted messages and this contract's text once M3 lands.
- Forward note for `T-5ZzAZp`: the contract's brief/breadcrumb/verdict/hold-request field lists
  and the kind→agent/instruction table are now frozen; instruction prose should reference this
  contract as authoritative rather than restating the shapes.
- Reviewer Suggestion S1 (non-blocking, not actioned): `OV-ST-1`/`OV-INT-3`'s contract
  descriptions are narrower than every call site that reuses those ids (e.g. `OV-ST-1` also covers
  a malformed `workflow.json` in the operator-only `request-closeout` path). Not fixed here since
  the contract's audience (an emitting `intake`/`ck-*` agent) never encounters those extra call
  sites via a hook failure; noted for `T-gbccdr`'s docs pass if it's worth a parenthetical later.

## Next actions
1. None outstanding — done, reviewed (approve with nits, both warnings fixed).
2. Unblocks `T-5ZzAZp` (agent instructions reference this contract) and `T-HPJcc6`/`T-tAKBBB`
   (the checker's rule ids/messages must match the "Hard rules" section here).
