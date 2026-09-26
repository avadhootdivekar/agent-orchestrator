# TASK: T-5ZzAZp-agent-instructions

## Metadata
- Task ID: `T-5ZzAZp-agent-instructions`
- Epic ID: `E-YAAGhk-overseer-runner-template`
- Owner: developer
- Created: 2026-09-26
- Last Updated: 2026-09-26
- Status: Draft
- Estimate: 3 days (24 h)

## Requirements Mapping
- Requirement IDs: FR-2, FR-3, FR-5, FR-9, FR-11, FR-12
- Design: `docs-md/overseer-runner-hld.md` §8.4 M4, §12, §13.3

## Description
Write the instruction files under `overseer-runner/instructions/`. They materialize to
`workflows/overseer-runner/instructions/` with `keep_existing: true`. Each file starts with
`instructions-version: 1` and the clause "The per-run `overseer-contract.md` is authoritative for
ids, paths, and JSON shapes. Where this file and the contract disagree, the contract wins." The
instructions are path-generic: concrete paths come from the prompt's inputs/outputs and the
contract.

| File | Must instruct |
|---|---|
| `01-git-branch-off.md` | Same semantics as routed-runner's (create/verify the run branch; write `git-go-ahead.md`). A copy with its own header, since templates are self-contained |
| `00-intake.md` | Split `prompt.md` into asks (a verbatim `statement` excerpt for each). Write acceptance criteria and a **usable bar** per ask. Write `charter.json` + `charter.md` (prompt_sha256 = sha256 of `prompt.md`). Plan wave 1 (≤ `wave_size` units, briefs with work_item keys `A<n>/<slug>`). Write briefs and `manifests/intake.json`. If the prompt is genuinely ambiguous: an empty wave + `ck-01` + a hold request. Run `intake-check --dry-run` and fix until clean |
| `10-work-unit.md` | Read your brief. Do the kind-specific playbook (research/design/implement/test/review/fix/document/verify). Write the report `waves/wJJ/<id>.md` and the breadcrumb `progress/<id>.json` (schema), with honest outcome/verdict and `changed_paths` as `<repo_id>:<path>`. **Never exit non-zero because work is hard**: report `blocked`/`partial` and exit 0. Needs human input: set `needs_input: true` and write `needs-input/<id>.md` (do NOT touch control flags; the overseer decides on a hold) |
| `11-stabilize-unit.md` | Bring the named ask to its **usable bar**: the build or tests pass (code); half-done code removed, reverted, or feature-flagged off; docs describe actual behavior with open items listed; nothing "half-finished someone has to clean up". The same breadcrumb rules apply |
| `20-checkpoint.md` | Read `digest.json` FIRST, then the charter, the ledger (last 2 waves), the wave reports, and any hold answer. Judge alignment per ask (quote the ask's statement), loops (answer EVERY signal id), and progress (update the `criteria[]` statuses). Choose a decision allowed by the digest. Write `verdict.json`, `report.md` (a human-readable state of the run: done / in progress / usable status per ask / budget stage), the next briefs, and `manifests/ck-KK.json`. Wave size ≤ `allowed_wave_size`. In stabilize: only stabilize/verify/document. In closeout or `must_close`: only the tail. Early closeout needs verify-pass evidence per ask. Hold: write `control/hold-request.json` + `needs-input/ck-KK.md` and emit `ck-(K+1)` with zero units. Run `ckpt-check --dry-run` and fix until clean. If a previous `check-result.json` exists, read it first |
| `40-final-verify.md` | Verify each ask against its acceptance criteria and usable bar (build/tests for code; coherence/completeness for docs). Write an honest `final/verify.md` with a table per ask. Do not fix things (report only) |
| `41-closeout.md` | Write `final/closeout.md`. Per ask: done / usable (yes/no + why) / not done / deferred (with reason) / how to continue. Also: the budget summary (spent, stage history from digests), a loop/rework summary (signals and responses), and a pointer to the ledger |
| `90-final-push.md` | Same semantics as routed-runner's final push (runs unisolated; pushes the run branch; writes `push-report.md`) |
| `30-expander.md`, `31-sub-aggregate.md` | FR-15 only (T-zLHc7Q may author these; this task leaves them out) |

## Acceptance Criteria
1. All 8 MVP instruction files exist with the version header and the "contract wins" clause (test).
2. Content-marker tests (one assertion per bullet in the table, using stable marker phrases):
   - `00-intake.md` mentions `usable_bar`, `prompt_sha256`, `intake-check --dry-run`, and the
     hold path
   - `10-work-unit.md` mentions `exit 0`/`blocked`, the breadcrumb path, and `needs_input`
   - `20-checkpoint.md` mentions `digest.json` first, `every signal`, `allowed_wave_size`,
     `must_close`, `ckpt-check --dry-run`, `check-result.json`, and `hold-request.json`
   - `41-closeout.md` mentions `deferred` and `how to continue`
3. No instruction hardcodes a run-instance path (a regex test: no `workflows/overseer-runner/runs/`
   literal).
4. Manual real-LLM validation is deferred to T-23yMMB. This task records a dry review by the
   `reviewer` agent in STATUS.md (the single owner stays `developer`).

## Risks
- Instruction quality determines real-LLM adherence. Mitigation: the mechanical checkers, plus
  T-23yMMB evidence.

## Dependencies
- T-ltBLUY (the contract is the reference for every path/shape these files cite).

## Pseudocode / Algorithm
```text
N/A — prose, structured per the table.
```

## Schemas / Interface Notes
- References the §13.4 schemas only through the contract. No schema text is duplicated in the instructions.

## Handoff Boundary
- Upstream: T-ltBLUY.
- Downstream: T-WruPiv (paths), T-23yMMB (live validation).

## Artifacts
- `src/agent_orchestrator/templates/builtin/overseer-runner/instructions/*.md`
