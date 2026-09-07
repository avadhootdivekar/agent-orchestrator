# TASK: T-Ov9Bt5-overlap-scheduling-hotspots

## Metadata
- Task ID: `T-Ov9Bt5-overlap-scheduling-hotspots`
- Epic ID: `E-Wk9Tz3-task-isolation`
- Owner: unassigned (developer)
- Created: 2026-09-06
- Last Updated: 2026-09-07
- Status: Draft
- Estimate: 2.5 days

## Requirements Mapping
- Requirement IDs: FR-10, FR-11 · Design: HLD §9, §11 M8
- Review findings folded in: **R-5 / R-18** (the engine call site is **`T-En8Hd4`'s**, not this
  ticket's — the HLD's subtask list previously contradicted this ticket and left the call site owned by
  nobody). Estimate unchanged at 2.5 days.

## Description
Soft, deterministic overlap-aware co-scheduling plus the hotspot signal that feeds it and the
breakdown agent. **`touches` is a preference, never a gate** — the single most important property of
this task.

Files you own:
- `src/agent_orchestrator/scheduling/__init__.py`, `src/agent_orchestrator/scheduling/overlap.py` (new)
- `src/agent_orchestrator/isolation/hotspots.py` (new)
- `src/agent_orchestrator/cli.py` (edit — **only** the new `ao hotspots` command; coordinate with
  `T-Cx4Jf1`, which owns the `--isolation` option and `ao prune` changes in the same file)
- `tests/test_overlap_ranking.py`, `tests/test_hotspots.py`, `tests/test_e2e_cli_hotspots.py` (new)

Do NOT touch: `engine.py` (the one-line wave-fill call site is `T-En8Hd4`'s; if it is not there yet,
ship `rank_wave` standalone and flag it), `integrator.py`, `worktrees.py`, `models.py`.

## Acceptance Criteria
1. `rank_wave(ready, touches, hotspots, n) -> list[str]` implements HLD §9.1 exactly and is **pure**
   (no git, no filesystem, no clock).
2. **`n == 1` is a provable no-op**: `rank_wave(ready, any_touches, any_hotspots, 1) == ready[:1]` for
   a randomized corpus of >= 200 inputs. This is what preserves ADR-0007 D1/D6 and this epic's NFR-2.
3. **Never withholds a slot**: for every input where `len(ready) >= n`, `len(rank_wave(...)) == n`,
   even when every candidate overlaps every other. Property test over a randomized corpus.
4. Preference works: given `ready = [a, b, c]` with `touches[a] == touches[b] == ["src/x/**"]` and
   `touches[c] == ["src/y/**"]`, `rank_wave(..., n=2)` returns `[a, c]` — `b` is deferred in favour of
   the disjoint `c`, while `a` (first in order) is never displaced.
5. Determinism: the same inputs give the same output across 100 runs and across shuffled dict
   iteration order; `ready.index(t)` is the tie-break everywhere.
6. A task with **no** `touches` scores `0.0` against everything (absent hint = no opinion, never a
   penalty). Dedicated test.
7. `glob_intersection` is syntactic and filesystem-free: `src/api/**` vs `src/api/accounts.rs` →
   intersect; `src/api/**` vs `src/ui/**` → do not; `**/mod.rs` vs `src/a/mod.rs` → intersect;
   identical literals → intersect. Table-driven test.
8. Hotspot weighting: a collision on a hotspot path scores strictly higher than a collision on a cold
   path, so given equal overlap counts the cold-collision pair is preferred. Test.
9. `parse_churn(git_log_text)` is **pure** over captured `git log --pretty=format: --name-only`
   output, handles blank separator lines and merge-commit suppression, and is tested against a
   fixture file. `compute_hotspots` merges churn with observed conflicts from prior runs'
   `state.json` (`CONFLICT_WEIGHT = 5`, a named constant) and drops paths that no longer exist at HEAD.
10. `ao hotspots [--workspace DIR] [--repo ID] [--since-days 180] [--top 40] [--output PATH]
    [--include-conflicts/--no-include-conflicts]` writes the JSON in HLD §11 M8 and prints a short
    summary. e2e via `CliRunner` against a real temp repo with a scripted history: assert the top
    entry is the file touched most, and that a `--since-days` window excludes older commits.
11. Load-with-fallback: a missing, unreadable or schema-invalid `hotspots_path` yields an empty
    hotspot map with **one** warning and never fails a run. Three tests.
12. `uv run pytest -q` green with recorded counts; `ruff` clean; `uv run mypy src` zero new errors.

### Amendments from the 2026-09-07 review gates

13. **R-5 / R-18 — ownership, settled.** This ticket ships the **pure** `rank_wave` plus
    `load_hotspots(path) -> Hotspots` (empty-on-any-error). The `engine.py` wave-fill call site is
    **`T-En8Hd4` AC-18** and has its own live-dispatch test there. HLD §11 M8's subtask (6) previously
    read "engine wiring", contradicting this ticket's own "Do NOT touch `engine.py`" — the effect was
    that **no** ticket required the call site to exist and `rank_wave` would have shipped as dead code.
    It now reads `load_hotspots`. Do not add the call site here; do confirm in your handoff that
    `T-En8Hd4` has landed it (or flag it if not).
14. `resolve_overlap_preference` lives in `models.py` (`T-Sc7Rm2`), not here — call it, do not
    reimplement the derived default. A test asserts this module contains no second copy of that rule.

## Risks
- **Scope creep into hard gating** (R9). Mitigation: AC-3 is a property test that a slot is never
  withheld; keep the schema description ("SOFT hint ... never a gate") verbatim.
- **Churn is a noisy hotspot signal.** In the real consumer, the top churn file had already been
  deleted by a prior epic and a 740-byte `mod` declaration list ranked 6th. Mitigations: the
  conflict-observation term (ground truth), the default 180-day window, and dropping paths absent at
  HEAD. Document the limitation in the command's `--help`.
- `cli.py` is shared with `T-Cx4Jf1`. Agree the split up front: this ticket adds **only** the
  `hotspots` command function and its registration.

## Dependencies
- Upstream: `T-Sc7Rm2` (`SchedulingSpec`, `touches`).
- Downstream: `T-En8Hd4` calls `rank_wave` (or, if it landed first, add the call site there and flag
  it); `T-Tp7Zs2` declares `.ao/hotspots.json` as a breakdown input.

## Pseudocode / Algorithm
```text
HLD §9.1 rank_wave / overlap_score / glob_intersection; §9.2 compute_hotspots.
```

## Schemas / Interface Notes
- Interface / API (locked): `rank_wave`, `overlap_score`, `glob_intersection`, `Hotspots`,
  `compute_hotspots`, `parse_churn`, `load_hotspots`.
- Spec / data schema: `.ao/hotspots.json` — `{"version":"1.0","generated_at":...,"window_days":...,
  "repos":{"<repo_id>":{"entries":[{"path":...,"churn":N,"conflicts":M,"weight":W}]}}}`.
- Triggers / events: `scheduling.overlap_preferred` (one line per wave, only at `soft`).
- Artifacts: `.ao/hotspots.json` (workspace-relative).

## Handoff Boundary
- Upstream: `T-Sc7Rm2`'s models.
- Downstream: publish `rank_wave`'s exact signature in `STATUS.md`.

## Artifacts
- Docs/comments: `meta/tickets/E-Wk9Tz3-task-isolation/T-Ov9Bt5-overlap-scheduling-hotspots/`
- Large outputs: none

---
- By: architect · Role: architect · Date: 2026-09-07 · Comment: Phase-2 amendment. R-5/R-18 resolved
  in this ticket's favour: it keeps the pure function and `load_hotspots`; `T-En8Hd4` owns the engine
  call site and now carries an AC and a live-dispatch test for it. Also pinned that the derived default
  comes from `models.resolve_overlap_preference` rather than a local copy. Estimate unchanged.
