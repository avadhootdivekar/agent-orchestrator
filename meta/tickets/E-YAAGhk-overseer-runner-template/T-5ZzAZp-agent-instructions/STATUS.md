# STATUS

- ID: `T-5ZzAZp-agent-instructions`
- Updated At: 2026-09-26
- State: Done — including the `branch_policy` amendment below, reviewed (approve with nits /
  blocking issues fixed)
- Owner: developer

## Amendment: `branch_policy` param (mid-epic, added after this ticket's initial Done)

New requirement confirmed by the user: an optional `branch_policy` free-text param plus a
deterministic git-fact-gathering pass in `01-git-branch-off.md`, so the `git-operator` agent
never guesses at git state before applying policy text. Implemented by dev-epic directly (small,
well-bounded change, full context already in hand from the initial implementation):
- `template.yaml`: new optional `branch_policy` param (free text, default `""`).
- `overseer-config.json.tmpl`: renders it as `"branch_policy": "{{ params.branch_policy }}"`.
- `workflow.json.tmpl`: added `overseer-config.json` to `git-branch-off`'s `inputs` (consistent
  with how `intake` already lists it, despite neither being task-produced).
- `01-git-branch-off.md`: new "Step 0" — mandatory fact pass (`fetch`, `merge-base
  --is-ancestor HEAD origin/<mainline>` for the real "already merged" check, `status
  --porcelain`, upstream presence, mainline-branch identity) run BEFORE any policy text is
  applied; combines facts + 3 illustrative (not exhaustive) policy readings to decide
  branch-fresh / continue / ask-a-human; never destructive under any reading. A genuine
  policy/reality conflict reuses this task's own existing `git-abort.md`-and-don't-write-
  `git-go-ahead.md` pattern (not the overseer's checkpoint hold, which doesn't structurally
  exist yet at this pre-`intake` point in the DAG).
- `README.md`: new "Branch policy" section + Params table entry.
- 11 new tests total (9 initial + 2 post-review fixes) across
  `tests/test_builtin_overseer_runner_assets.py`/`tests/test_builtin_overseer_runner_instructions.py`.

By: reviewer · Role: reviewer · Date: 2026-09-26 · Comment: **Verdict: Blocking issues found**
(pre-fix). Verified the plumbing by reading the actual code, not trusting claims: `_render`'s
`escape_json`/`_json_escape_value` genuinely prevents a free-text policy value from breaking out
of its JSON string; `load_config` only reads named fields and tolerates the extra
`branch_policy` key without error; the new `workflow.json.tmpl` input is consistent with
`intake`'s existing pattern. Found the core Step 0 design (mandatory deterministic facts before
any policy text, reusing the existing abort pattern instead of inventing one) architecturally
sound. **Critical (blocking)**: the rewrite silently DROPPED the pre-amendment detached-HEAD
ABORT gate — the new Step 0 recorded the fact but never mandated acting on it, and neither
Procedure branch (main-or-master / non-main) covers an empty branch name, reopening exactly the
"guess at git state" failure mode this section exists to eliminate. **Warnings**: (2) `docs-md/
overseer-runner-hld.md` §13.1 and its embedded `git-branch-off` inputs snippet went stale (still
16 params, still missing the new input); (3) the merge-base check hardcoded `origin/main`,
inconsistent with the file's own main-or-master awareness; (4) the "keep working... unless
already merged" policy example never addressed starting on `main` itself; (5)
`test_overseer_config_json_tmpl_scalar_defaults` wasn't extended for the new field even though
`DUMMY_VALUES` was. All reproduction claims (121/400/4383 passed, ruff, pyright) confirmed exact.

By: dev-epic · Role: manager · Date: 2026-09-26 · Comment: Fixed all 5 findings. (1) Restored an
unconditional, policy-independent detached-HEAD ABORT in Step 0, with Procedure step 1 now
explicitly noting it already fired if reached; added a regression test asserting the ABORT
directive is on the same line as "detached HEAD", not just present somewhere in the file. (3)
Introduced a single `<mainline>` fact (`main` if `origin/main` exists else `master`) and used it
consistently in both the branch-identity check and the merge-base check, replacing the hardcoded
literal. (4) Added the missing "if currently on `<mainline>` itself, do Procedure step 3 as
usual" clause to the second policy example. (5) Added the missing `branch_policy` assertion, plus
(per the reviewer's own Suggestion) a dedicated quote/backslash escaping regression test rather
than relying only on generic coverage. (2) Updated HLD §13.1's params block and the embedded
`git-branch-off` inputs snippet — a mechanical doc-sync fix, not a design re-litigation. Re-ran
everything: `pytest tests/test_builtin_overseer_runner_assets.py
tests/test_builtin_overseer_runner_instructions.py -q` → 123 passed; full suite → **4385 passed,
8 skipped, 0 failed**; `ruff`/`pyright` clean.

## Original implementation (this update)
- Ticket created by the architect design pass (Rev 2, after the Phase-4 consultations). Sprint: S1 (after T-ltBLUY).

By: architect · Role: architect · Date: 2026-09-26 · Comment: Created from `docs-md/overseer-runner-hld.md` Rev 2. The ACs are pass/fail and agent-executable, and the task is sized ≤3 days. Not started.

By: developer · Role: developer · Date: 2026-09-26 · Comment: Implemented all 8 MVP instruction
files under
`src/agent_orchestrator/templates/builtin/overseer-runner/instructions/`: `01-git-branch-off.md`,
`00-intake.md`, `10-work-unit.md`, `11-stabilize-unit.md`, `20-checkpoint.md`,
`40-final-verify.md`, `41-closeout.md`, `90-final-push.md`. Every file opens with the exact
`instructions-version: 1` header and the verbatim "contract wins" clause (matched routed-runner's
own established convention of plain prose, not YAML frontmatter — confirmed by reading its
instruction files, none of which use frontmatter or carry any `instructions-version` marker
today). Each file points at `overseer-contract.md`/`overseer-config.json` for ids, paths, and JSON
shapes rather than restating them, per T-ltBLUY's own forward note ("instruction prose should
reference this contract as authoritative rather than restating the shapes"). `30-expander.md`/
`31-sub-aggregate.md` (FR-15) were deliberately NOT created (out of this task's scope per its own
table; left for `T-zLHc7Q`).
- Cross-checked the pause/halt flag names used in `90-final-push.md`
  (`control/pause.flag`/`-2`/`-3`) against `workflow.json.tmpl`'s actual `circuit_breakers` list —
  they match exactly (breaker ids `human-input-gate`/`-2`/`-3`, `operator-kill`/`-2`); added a
  drift-guard test that reads `workflow.json.tmpl` directly rather than hardcoding the expectation
  twice.
- Tests: new file `tests/test_builtin_overseer_runner_instructions.py` (65 tests) covering AC1
  (version header + contract-wins clause verbatim, every file), AC2 (every content-marker phrase
  from the ticket table, per file), AC3 (no `workflows/overseer-runner/runs/` literal in any
  file), plus a path-generic/no-`{{ }}`-token check (mirrors `routed-runner`'s own convention for
  workspace-asset instructions) and the FR-15 negative check (`30-expander.md`/
  `31-sub-aggregate.md` do not exist).
- **Judgment call (disclosed)**: `tests/test_builtin_overseer_runner_assets.py` had a pre-existing
  temporal scope-boundary guard, `test_no_instructions_dir_created_yet`, whose own docstring named
  this task as the trigger that would flip it (mirroring the same pattern T-ltBLUY's own review
  documented for the contract-file guard it replaced). Renamed/updated it to
  `test_instructions_dir_has_the_8_mvp_files`, asserting the real, now-required directory shape
  instead of its absence — this is the same "each task updates the shared file's own temporal
  guard as its scope lands" precedent T-ltBLUY set, not a new pattern. No other test in that file
  was touched.
- **Judgment call (disclosed)**: no explicit branch-naming convention for this template exists in
  the HLD/contract (unlike `routed-runner`'s documented `epic/<epic-id>`). Chose `overseer/<run-id>`
  (the run id being the instance directory's own last path segment, i.e. `{{ id }}` from
  `template.yaml`'s `id_pattern`) — distinct from `routed-runner`'s prefix so the two templates'
  branches are visually distinguishable if a workspace uses both. Documented in
  `01-git-branch-off.md` itself; flagging here in case the epic owner wants a different prefix.
- **Judgment call (disclosed)**: the header style. The ticket's own text offered a YAML-frontmatter
  block as one option and a plain first line as the fallback if `routed-runner` doesn't use
  frontmatter. Checked `routed-runner/instructions/*.md` — none use frontmatter, none carry any
  `instructions-version` marker at all — so used the plain-prose form (`**instructions-version:
  1**` as the file's first line, contract-wins clause as the next paragraph), matching the
  established convention rather than introducing a new one.

## Evidence (branch_policy amendment, post-review-fix)
- `.venv/bin/pytest -q tests/test_builtin_overseer_runner_assets.py tests/test_builtin_overseer_runner_instructions.py`
  → `123 passed` (47+74 baseline + 2 new regression tests from the review fix).
- Full repo suite: `.venv/bin/pytest -q` → `4385 passed, 8 skipped, 0 failed`.
- `ruff check`/`ruff format --check` clean. Real `pyright` run on both touched test files → 0
  real errors (only known/ignorable pytest-import and yaml-source-resolution noise).

## Evidence (original implementation)
- `.venv/bin/pytest -q tests/test_builtin_overseer_runner_instructions.py` → `65 passed`.
- `.venv/bin/pytest -q tests/test_builtin_overseer_runner_assets.py` → `47 passed` (unchanged
  count from T-ltBLUY's own baseline — one test renamed/repointed, none added/removed in this
  file).
- Full repo suite: `.venv/bin/pytest -q` → `4274 passed, 8 skipped, 0 failed` (zero regressions vs.
  the pre-task baseline of 4209 passed/8 skipped after T-ltBLUY; net +65 new tests).
- `.venv/bin/ruff check` + `.venv/bin/ruff format --check` on the new/touched test files → clean
  (one formatting pass applied by `ruff format` to the new test file's line wrapping).
- `mypy` on `src` is this repo's CI gate (`mypy src` per `.github/workflows/ci.yml`) — no `src`
  file was touched by this task, so N/A. `mypy` run directly against the two touched test files
  in isolation shows the same pre-existing, environment-wide `import-untyped` condition
  T-ltBLUY's own STATUS.md already documented (no `py.typed` marker shipped by
  `agent_orchestrator`; reproduced against an untouched file, `tests/test_dag.py`, to confirm it
  is not specific to this task's changes).
- Manual grep confirmation: `grep -rn "workflows/overseer-runner/runs/" instructions/` → no
  matches; `grep -rn '{{' instructions/` → no matches.
- Code: `src/agent_orchestrator/templates/builtin/overseer-runner/instructions/*.md` (8 new
  files).
- Tests: `tests/test_builtin_overseer_runner_instructions.py` (new),
  `tests/test_builtin_overseer_runner_assets.py` (one test updated).

## Risks / Blockers
- AC4 (reviewer dry review) is explicitly not self-arranged per this ticket's own text — the epic
  owner requests it separately. Until that review lands, this task's State stays "Implemented —
  pending reviewer dry review" rather than "Done".
- Manual real-LLM validation is deferred to `T-23yMMB`, per this ticket's own AC4 note — these
  instructions are unverified against an actual dispatched agent's behavior until then.
- See TASK.md "Risks" for the pre-existing risk note (instruction quality vs. real-LLM adherence,
  mitigated by the mechanical checkers once `T-HPJcc6`/`T-tAKBBB` land, plus `T-23yMMB` evidence).

## Next actions
1. None outstanding — done, reviewed (approve with nits), the one Warning fixed.
2. Downstream: `T-WruPiv` (e2e harness, references these instruction paths),
   `T-23yMMB` (live real-LLM validation of these instructions).
3. A separate, later amendment (git-branch-off `branch_policy` param) is tracked as its own
   follow-up change to this ticket — see the epic STATUS.md for the new requirement and its
   own evidence once implemented.

## Reviewer dry review (AC4)

By: reviewer · Role: reviewer · Date: 2026-09-26 · Comment:

**Verdict: Approve with nits.** All 8 files read in full against the ticket's per-file "must
instruct" table, cross-checked line-for-line against `overseer-contract.md.tmpl` /
`overseer-config.json.tmpl` / `workflow.json.tmpl`, and re-ran independently:
`pytest tests/test_builtin_overseer_runner_instructions.py` → 65 passed;
`pytest tests/test_builtin_overseer_runner_assets.py` → 47 passed; both together → 112 passed;
`ruff check` + `ruff format --check` on both touched test files → clean. Confirmed
`30-expander.md`/`31-sub-aggregate.md` do not exist on disk, and an independent
`grep -rn "workflows/overseer-runner/runs/" instructions/` returns zero matches (AC3
satisfied). `01-git-branch-off.md`/`90-final-push.md` correctly mirror `routed-runner`'s
own two git-operator instructions with only the necessary template-specific changes
(branch prefix, "one push in the whole run" framing); the `pause.flag`/`pause-2.flag`/
`pause-3.flag` names in `90-final-push.md` were re-derived independently from
`workflow.json.tmpl`'s `circuit_breakers` list (not trusted from the developer's own claim)
and match exactly. The two disclosed judgment calls (plain-prose header matching
`routed-runner`'s own convention; `overseer/<run-id>` branch naming absent an
HLD-specified one) are both reasonable and correctly disclosed. The one pre-existing test
edit (`test_instructions_dir_has_the_8_mvp_files`) follows the same accepted precedent
`T-ltBLUY` already set — no new concern there.

**Warning (should fix before this ticket moves to Done):** `00-intake.md`'s Step 1/3
("Write these into `charter.json` using the `ao.overseer.charter/v1` schema **exactly** as
given in `overseer-contract.md`'s 'Agent-authored artifact schemas' section") is factually
wrong. That contract section explicitly excludes the charter — its own opening line reads
"`charter.json` is written once, by `intake`, and is out of scope for a checkpoint" — and
only documents `brief`/`breadcrumb`/`verdict`/`hold-request`. The actual charter schema
lives at `docs-md/overseer-runner-hld.md` §13.4, which `00-intake.md` never cites. The
field list `00-intake.md` restates (`ask_id`, `statement`, `deliverable_type`, `acceptance`,
`usable_bar`, `priority`, `global_constraints`, `assumptions`, `out_of_scope`,
`open_questions`) does match HLD §13.4 correctly, so the practical content isn't wrong —
but the citation is, and a real intake agent told to check the contract "exactly" for this
schema will not find it there, only a disclaimer that it's out of scope. Fix: point at HLD
§13.4 (or state plainly that this instruction's own field list is authoritative for the
charter shape, since the contract doesn't carry it), rather than attributing it to a
contract section that disclaims it.

Related to the same gap: HLD §13.4 defines each charter `acceptance` entry as an object
`{id: "A<n>.<m>", text: str≤400}`, and the contract's own verdict schema requires
`criteria[]` to be "one entry per charter acceptance criterion: `{id, status:
met|unmet|deferred}`" (`overseer-contract.md.tmpl` line 178) — i.e. each acceptance
criterion needs its own stable `id` for the checkpoint to reference. Neither `00-intake.md`
("Write its acceptance criteria: concrete, checkable statements...") nor `20-checkpoint.md`
("set its `criteria[]` status") ever tells the agent that acceptance items must carry this
`id`. Fix: add one sentence to `00-intake.md`'s Step 1 (acceptance items are `{id: "A<n>.<m>",
text}` objects, not bare strings) so `20-checkpoint.md`'s `criteria[]` has something stable
to key against.

**Suggestions (non-blocking):** `90-final-push.md` drops the "Completion checklist"
section `routed-runner/instructions/90-final-push.md` ends with; likely fine since
`41-closeout.md` already owns the run's completion marker, but worth a one-line
confirmation that this was intentional rather than an oversight.

**Everything else checked and clean:** `20-checkpoint.md`'s read order (digest.json first,
then charter, ledger last-2-waves, wave reports/breadcrumbs, hold answer, prior
check-result.json) matches what the contract's digest/verdict artifacts require; decisions
are correctly constrained to `digest.allowed_decisions`; the hold-emission shape
(`depends_on: [<own id>]`, empty wave) matches the contract's hold shape exactly; the early-closeout
verify-evidence requirement is stated near-verbatim from the contract. `10-work-unit.md`'s
"never exit non-zero" rule is its own bolded, prominent section, and the `needs_input`
vs. checkpoint-decides-hold boundary is unambiguous and consistent with
`11-stabilize-unit.md`'s identical framing. No instruction contradicts the contract on ids,
hook names, or field names outside the one item above. Spot-checked 8 of the 65 new tests
for substance (marker tests are intentionally shallow per the ticket's own AC2 wording, but
`test_20_checkpoint_reads_digest_json_first`, the two pause-flag drift-guard tests, and
`test_10_work_unit_covers_every_non_stabilize_kind` all check a real, falsifiable claim
rather than a bare substring) — no rubber-stamp tests found.

This satisfies AC4's "reviewer-agent dry review... recorded in STATUS.md" requirement. The
one Warning above should be fixed (a small, mechanical prose edit to `00-intake.md`) before
flipping this ticket's State to Done; it does not require re-running the mechanical test
suite (AC1-AC3 are unaffected) but does affect the substantive correctness this dry review
was asked to verify beyond the marker tests.

By: dev-epic · Role: manager · Date: 2026-09-26 · Comment: Fixed the Warning. `00-intake.md`
Step 1 now cites `docs-md/overseer-runner-hld.md` §13.4 (not the contract, which explicitly
disclaims the charter) and states this explicitly; also added the missing sentence
establishing `acceptance[]` entries as `{id: "A<n>.<m>", text}` objects, and a matching
one-line clarification in `20-checkpoint.md`'s "Update progress" section so `criteria[]` has
a stable id to key against. Re-verified: `pytest tests/test_builtin_overseer_runner_instructions.py`
→ 65 passed (unaffected, as the reviewer predicted). The Suggestion (dropping
`90-final-push.md`'s "Completion checklist" section) is confirmed intentional, not an
oversight: `41-closeout.md` is this template's own completion-marker owner
(`outputs/final/closeout.md`, per the README's "Completion marker" section), so
`90-final-push.md` correctly doesn't duplicate that reporting — no change made.
