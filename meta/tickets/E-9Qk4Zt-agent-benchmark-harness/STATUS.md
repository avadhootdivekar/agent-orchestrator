# STATUS

- ID: `E-9Qk4Zt-agent-benchmark-harness`
- Updated At: 2026-07-22
- State: In Progress (1/9 tasks delivered — `T-Sc4Hm2` Done)
- Owner: architect agent (design) → developer/tester (delivery)

## This update
- By: Claude · Role: developer · Date: 2026-07-22 · Comment: `T-Sc4Hm2-suite-subject-schemas`
  complete — the epic's foundation task. Delivered both versioned `additionalProperties:false`
  JSON schemas (`benchmarks/schemas/{benchmark-suite,subject}.schema.json`), the pydantic models
  + `load_suite`/`load_subject` loader/validator (`src/agent_orchestrator/bench/spec.py`), the
  empty `SUBJECT_REGISTRY`/`GRADER_REGISTRY` + register helpers (`bench/registries.py`), the
  bench error hierarchy reusing core `SpecValidationError` (`bench/errors.py`), and a minimal
  `ao-bench validate` Typer app (`bench/cli.py`, no `[project.scripts]` entry yet — `T-Cli8Nf`).
  48 new tests (`tests/bench/`), full suite 857→905 passed/3 deselected (+48, zero regressions),
  `ruff`/`mypy` clean on every touched file, `bench/` confirmed outside the core import graph
  (SI-1) and no file outside `bench/`/`benchmarks/` touched (NFR-1). One scope deviation from
  `TASK.md` (models folded into `spec.py` per the assigning message's explicit file list, not a
  separate `models.py`) and one filled gap (the `Assertion` model's fields, underspecified in the
  design doc) — both recorded, low-risk, and non-blocking for downstream tasks. Full detail:
  `T-Sc4Hm2-suite-subject-schemas/STATUS.md`.

## Evidence
- Design: `docs-md/benchmarking-framework-hld.md` · `docs-md/benchmark-landscape-survey.md` · `docs-md/adr/ADR-0008-benchmark-harness-approach.md`
- Tickets: `meta/tickets/E-9Qk4Zt-agent-benchmark-harness/` (EPIC.md + 9 task folders)
- `T-Sc4Hm2`: `benchmarks/schemas/*.json`, `src/agent_orchestrator/bench/{__init__,errors,registries,spec,cli}.py`,
  `tests/bench/*` (new). `uv run pytest tests/bench -q` = 48 passed. `uv run pytest -q -m "not
  real_llm"` = 905 passed/3 deselected (was 857/3, +48, 0 regressions). `ruff check`/`ruff format
  --check` clean on touched files (2 pre-existing, untouched `test_e2e_cli.py` errors persist,
  out of scope). `mypy src/agent_orchestrator/bench` clean; `mypy src` clean except 4
  pre-existing, untouched `_version.py` errors. See `T-Sc4Hm2-suite-subject-schemas/STATUS.md`
  for the full AC-by-AC verification.

## Risks / Blockers
- Open questions Q1 (ao-epic workflow template shape), Q2 (exact model ids to pin), Q3 (report auto-discovery) — see design §18; proposed defaults recorded, escalate on disagreement.
- Real-LLM smoke (T-Fx6Dp0) cost/flakiness — mitigated by haiku + tiny fixtures; deterministic fake-subject run is the gate.
- Forward note from `T-Sc4Hm2` for `T-Sbj9Ka`/`T-Grd7Vx`: registering concrete Subject/Grader
  classes into `SUBJECT_REGISTRY`/`GRADER_REGISTRY` must be paired with extending
  `bench/spec.py`'s `KNOWN_SUBJECT_TYPES`/`KNOWN_GRADER_TYPES` closed lists, or the new types
  will still be rejected by `ao-bench validate`.

## Next actions
1. ~~Start Sprint 1: `T-Sc4Hm2-suite-subject-schemas` (no deps).~~ — done 2026-07-22.
2. Start `T-Sbj9Ka-subject-adapters` and `T-Grd7Vx-grader-registry` (both unblocked, depend only
   on `T-Sc4Hm2`).
3. Resolve Q1/Q2 before `T-Fx6Dp0` (fixtures) lands.
