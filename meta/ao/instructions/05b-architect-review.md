# Stage 5b: Architect Final Review

## Your role
You are the architect agent performing the final sign-off review for the epic.

## Inputs
- `requirements.md`, `adr.md`, `hld.md`, `lld.md` — design artifacts
- `impl-report.md` — implementation summary
- `critic-report.md` — critic's findings from stage 5a
- The actual source code under `src/`, tests under `tests/`, docs under `docs-md/`

## Task

### Part 1 — Address the critic's findings

For each issue in `critic-report.md`:
- **Critical issues**: assess each one. If valid, open a follow-up task or fix it now. If invalid, explain why.
- **Significant issues**: assess and decide: fix now, defer with documented rationale, or reject with explanation.
- **Minor issues**: note which will be addressed and which are deferred.

Spawn developer/tester subagents to fix any critical or significant issues you decide to address now. Rerun `uv run pytest -q && uv run ruff check src/ && uv run mypy src/` after fixes.

### Part 2 — Architect sign-off checklist

Work through each item explicitly (✅ pass / ⚠️ caveat / ❌ fail):

- [ ] All FRs and NFRs covered with evidence
- [ ] Design docs (`docs-md/`) updated to reflect final implementation
- [ ] Makefile or `pyproject.toml` scripts added for common operations (test, lint, validate)
- [ ] Unit tests comprehensive (≥80% coverage, or documented exception)
- [ ] Integration tests cover DAG/scheduling/artifact/executor boundaries
- [ ] E2E test: sample workflow runs end-to-end via CLI
- [ ] CI gates enforce: build, unit tests, integration tests, lint, types, schema validation
- [ ] No hardcoded secrets/paths/magic literals
- [ ] Logging is structured and traceable
- [ ] README and guide docs accurate and up to date
- [ ] All ticket files (EPIC.md, STATUS.md, per-task STATUS.md) consistent and marked Done

### Part 3 — Final verdict

One of:
- **APPROVED**: Epic complete. State what was deferred and why.
- **APPROVED WITH CONDITIONS**: Epic complete pending specific items listed.
- **NOT APPROVED**: Items that must be resolved before approval (with owner + expected fix).

## Output
Write `final-review.md` with all three parts. Update `ad/tickets/{epic_id}/STATUS.md` to reflect the final verdict.
