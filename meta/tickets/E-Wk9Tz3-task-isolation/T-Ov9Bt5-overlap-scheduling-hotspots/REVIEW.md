# REVIEW: T-Ov9Bt5-overlap-scheduling-hotspots

- Reviewer: reviewer-agent · Role: reviewer · Date: 2026-09-07
- Scope reviewed (uncommitted, branch `ad/task-isolation`): `src/agent_orchestrator/scheduling/__init__.py`,
  `scheduling/overlap.py`, `src/agent_orchestrator/isolation/hotspots.py`,
  `src/agent_orchestrator/isolation/git.py` (`_log_name_only` only), `src/agent_orchestrator/cli.py`
  (`ao hotspots` only), `tests/test_overlap_ranking.py`, `tests/test_hotspots.py`,
  `tests/test_e2e_cli_hotspots.py`, `tests/fixtures/git_log_name_only.txt`, this task's `TASK.md`/`STATUS.md`.
  Did not review/edit `T-Wk3Nv6`'s (`isolation/paths.py`, `worktrees.py`, `view.py`, `service/paths.py`,
  `tests/isolation/*`, `tests/service/*`) or `T-Tp7Zs2`'s (`templates/**`) uncommitted work; read
  `templates/builtin/routed-runner/instructions/07-task-breakdown.md` read-only to cross-check the shared
  `.ao/hotspots.json` contract.

## Summary

Solid, well-documented implementation that honors its own ticket contract: `rank_wave` is genuinely pure
(no git/filesystem/clock imports), the `n<=1` no-op is an unconditional early return (not just
empirically true), the "never withholds a slot" guarantee holds by construction (pass 2 admits every
remaining candidate unconditionally), and `engine.py`/`models.py`/`integrator.py` are untouched exactly as
instructed — confirmed via `git diff --stat` (empty) and a `rank_wave`/`scheduling` grep (no hits). Test
suite is thorough (AC-1..AC-14 all have a dedicated test), 100% coverage on both new modules, ruff/mypy
clean, full suite green (3244 passed / 7 skipped, ~108s, no regression). Three real correctness/robustness
gaps remain, all matching the review brief's own named challenges: a structural test-safety-net evasion
via an underscore-prefixed cross-module method, a non-atomic `.ao/hotspots.json` write where an atomic
precedent already exists in the codebase, and an unhandled git path-quoting case that silently drops any
churned file with a non-ASCII/special-character name. None of these compromise the soft/never-a-gate
guarantee or corrupt a run — they degrade the hotspot *signal's* quality/durability, which is exactly the
already-acknowledged "noisy signal" limitation, just with one more instance of it. Goal alignment (project
DAG/determinism/pluggability principles, epic FR-10/FR-11, R-5/R-18 ownership resolution, code-level intent)
is otherwise strong.

## Blocking

None.

## Major

### C-1 — `GitRepo._log_name_only` evades the structural no-forbidden-verb safety net (should be public)
- **Location**: `src/agent_orchestrator/isolation/git.py:886-915` (method), called cross-module at
  `src/agent_orchestrator/isolation/hotspots.py:234`; the test it evades is
  `tests/isolation/test_git.py:284-341` (`TestStructuralNoNetworkSurface::
  test_public_method_surface_never_reaches_a_forbidden_verb`).
- **Observation**: The method is deliberately named with a leading underscore specifically so
  `inspect.getmembers(GitRepo, predicate=inspect.isfunction)` filtered to `not name.startswith("_")`
  (`test_git.py:293-297`) never sees it, and is then called from a *different module*
  (`isolation/hotspots.py`) — Python doesn't enforce the underscore convention across module boundaries,
  so this is a real, not just cosmetic, encapsulation breach. STATUS.md's own "Boundary note" confirms this
  was a deliberate choice to route around the sweep rather than extend it, because extending it would
  require a one-line edit to `tests/isolation/test_git.py`, which this ticket's concurrency boundary
  reserves for `T-Wk3Nv6`.
- **Why it matters**: That structural test's entire stated purpose (`test_git.py:278-282`) is to catch a
  *future* method that smuggles a forbidden verb "even if nobody writes a dedicated per-method test for
  it" — i.e., it is meant to be exhaustive over every git-invoking method, by construction, forever. Naming
  a new method with a leading underscore is now a standing, discoverable way to add a git-invoking method
  that this security-relevant (S-1) sweep will never see again. Today's `_log_name_only` is safe (hardcoded
  subcommand, no interpolated verb, routes through `_run`'s hardening) — but the pattern itself, once
  established, is a hole the next git-touching module can walk through without even trying.
- **Fix**: Rename to public `log_name_only` and add it to `call_args` in
  `tests/isolation/test_git.py:304-336` (one dict entry, e.g. `"log_name_only": (("/wt",), {})`) — a
  purely additive, one-line change with near-zero collision risk against `T-Wk3Nv6`'s actual edits to that
  file (paths/worktrees/view logic). Given the ticket's own precedent for exactly this situation (the
  `T-En8Hd4` engine-call-site handoff), the right move is to flag/coordinate the one-line test edit with
  `T-Wk3Nv6`'s owner rather than silently opting the method out of the sweep. If the one-line edit truly
  cannot land this round, at minimum say so explicitly in STATUS.md as a known gap (it currently reads as
  a closed decision, not a flagged one).

### C-2 — `.ao/hotspots.json` is not written atomically, despite an existing atomic-write precedent
- **Location**: `src/agent_orchestrator/cli.py:1525-1526` (`output_path.parent.mkdir(...)` then
  `output_path.write_text(...)`, direct, no temp file). Precedent:
  `src/agent_orchestrator/runstate.py:176-189` (`RunStateStore.save`/`write_status`) —
  `tmp = p.with_suffix(".tmp"); tmp.write_text(...); os.replace(tmp, p)`.
- **Observation**: The write is a plain `Path.write_text`, not write-then-rename. The review brief
  explicitly asked to verify "file written atomically (temp + rename)"; it is not.
- **Why it matters**: A crash/kill mid-write (long-running `ao hotspots` against a large repo, or a CI job
  timeout) leaves a truncated/corrupt `.ao/hotspots.json`. `load_hotspots` degrades this gracefully (empty
  + one warning, AC-11) so no run *fails* — but the hotspot signal silently resets to empty until the next
  successful `ao hotspots`, which is a durability regression relative to the rest of the codebase's own
  convention, and the codebase already has the exact pattern needed one file away. It also means two
  concurrent `ao hotspots` invocations against the same output path (e.g. two `--repo` jobs in parallel CI)
  can interleave writes rather than each producing a clean atomic version.
- **Fix**: Mirror `runstate.py`'s `tmp = output_path.with_suffix(".tmp"); tmp.write_text(...);
  os.replace(tmp, output_path)` at `cli.py:1526`. Not worth extracting to a shared helper yet (only two
  call sites, in different modules with different serialization) — inline the three lines to match the
  existing local idiom.

### C-3 — Churn parsing silently drops files with quoted/non-ASCII filenames (the `-z` gap)
- **Location**: `src/agent_orchestrator/isolation/git.py:907` (`_log_name_only` builds
  `["log", "--pretty=format:", "--name-only", ...]` with no `-z` and no `core.quotePath=false`);
  `src/agent_orchestrator/isolation/hotspots.py:156-159` (`parse_churn`'s `line.strip()`); consequence at
  `hotspots.py:251-252` (`is_tracked` returns `False` for the mangled path, silently `continue`s).
- **Observation**: Verified directly — a repo with `src/café.rs` produces `git log --name-only` output of
  `"src/caf\303\251.rs"` (git's default core.quotePath quoting/C-style-octal-escaping for non-ASCII paths;
  reproduced in a scratch repo, not asserted from memory). `parse_churn` keeps this exact quoted+escaped
  string as the "path". `compute_hotspots` then calls `is_tracked(cwd, "\"src/caf\\303\\251.rs\"")`, which
  never matches the real tracked path `src/café.rs`, so the entry is dropped by the same code path AC-9
  uses for genuinely-deleted files (`hotspots.py:252`, `# HLD §9.2 / AC-9: dropped, not merely
  deprioritized`) — silently conflating "renamed/quoted" with "deleted." No test in
  `tests/fixtures/git_log_name_only.txt` or `tests/test_hotspots.py` exercises a non-ASCII or
  quote/backslash-containing filename, so this regressed nothing visibly.
- **Why it matters**: This is exactly the "-z"/"--name-only" robustness question the review brief asked to
  verify, and it fails for any non-ASCII path (not an edge case in a lot of real codebases). The failure
  mode is silent under-ranking (a genuinely hot non-ASCII-named file never surfaces as a hotspot), which
  degrades the *soft signal's* usefulness without ever affecting the hard "never a gate" guarantee — so it
  doesn't block a run, but it does quietly undermine the one thing `ao hotspots` exists to produce.
- **Fix**: Add `-z` to `_log_name_only`'s args and NUL-split (`.split("\0")`) in `parse_churn` instead of
  `.splitlines()` (also sidesteps the blank-line-as-separator convention entirely, since `-z` uses a
  literal NUL between entries and between commits). Add one fixture/test case with a non-ASCII filename.

## Warnings

### W-1 — `compute_hotspots` spawns one `git` subprocess per churned path to check `is_tracked`
- **Location**: `src/agent_orchestrator/isolation/hotspots.py:248-252` (loop calling
  `git_repo.is_tracked(cwd, path)` per candidate), backed by
  `src/agent_orchestrator/isolation/git.py:882-884` (`git ls-files --error-unmatch` — one process per
  call).
- **Observation**: The drop-untracked-before-truncate logic is correct (iterates the full ranked list,
  not top_k-then-filter — verified by reading; `test_drops_paths_no_longer_at_head` and `test_top_k_truncates`
  both pass), but each candidate path costs a separate `git` subprocess spawn. The HLD's own stated
  real-world scenario (§9.2: a deleted `main.rs` outranked by churn, a real hotspot ranked 6th) implies
  exactly the case where many untracked paths must be probed before `top_k` tracked ones are found.
- **Why it matters**: `ao hotspots` is an offline/on-demand command, not on the run's hot path, so this
  isn't a correctness risk — but it's an O(distinct churned paths) subprocess-spawn cost for what could be
  a single `git ls-files` (or `git ls-tree -r --name-only HEAD`) call producing one in-memory set, checked
  by membership. On a repo with thousands of files touched inside the `--since-days` window this is a
  needless multi-second-to-minutes wait for an operator running one CLI command.
- **Fix**: Replace the per-path `is_tracked` loop with one `git ls-files` call up front (e.g. a new
  `GitRepo` method or reuse an existing tracked-paths listing) and an in-memory set-membership check inside
  `compute_hotspots`'s loop.

## Suggestions

### S-1 — AC-14's "no second copy" claim is tested by signature-arity only
- **Location**: `tests/test_overlap_ranking.py:136-141`
  (`test_rank_wave_signature_is_exactly_four_positional_args`), cited by STATUS.md as the test that "pins
  that this module has no second copy of that rule (AC-14)."
- **Observation**: The test only asserts `inspect.signature(rank_wave).parameters == ["ready", "touches",
  "hotspots", "n"]`. Reading `overlap.py` end-to-end confirms it genuinely contains no reimplementation of
  `resolve_overlap_preference`'s derivation rule today — so AC-14's substance holds — but the cited test
  doesn't structurally guard against a future regression (e.g. an internal helper later re-deriving "soft
  iff any task isolated"); it only pins the call signature, which is AC-1, not AC-14.
- **Fix (optional)**: Either add a real guard (e.g. assert `"OVERLAP_SOFT"`/`"resolve_task_isolation"` don't
  appear in `inspect.getsource(overlap)`), or adjust STATUS.md's phrasing so it doesn't overclaim what the
  existing test proves. Not worth blocking on — the code is correct today.

### S-2 — Eager top-level import for two int constants
- **Location**: `src/agent_orchestrator/cli.py:33`
  (`from .isolation.hotspots import DEFAULT_SINCE_DAYS, DEFAULT_TOP_K`).
- **Observation**: Every other `isolation.*` dependency in this file is imported lazily inside the command
  function body (see `cli.py:1480-1488` inside `hotspots_cmd` itself); this one top-level import is the
  only new eager import, needed only because Typer needs concrete literal defaults at decoration time, and
  it transitively pulls in `isolation.git`/pydantic/xdg for every `ao` invocation, not just `ao hotspots`.
- **Fix (optional)**: Default the two Typer options to `None` and resolve them to
  `DEFAULT_SINCE_DAYS`/`DEFAULT_TOP_K` inside the function body, keeping the import lazy like its
  siblings. Low real-world cost (pydantic is already a hard dependency elsewhere at startup) — purely a
  consistency nit.

### S-3 — Test volume is high but empirically not a runtime problem; one property test could be folded in
- **Location**: `tests/test_overlap_ranking.py` — three separate `@pytest.mark.parametrize("trial",
  range(200))` blocks (`test_n_equals_one_is_a_provable_no_op`, `test_never_withholds_a_slot`,
  `test_output_is_always_a_valid_subset_permutation`).
- **Observation**: Measured directly (see Verification below): the three new test files total 661 tests in
  0.72s; the full repo suite (3244 passed, 7 skipped) runs in 108.26s against a stated ~105s baseline — no
  material regression. AC-2 and AC-3 explicitly require a ">= 200-input randomized corpus" each, so two of
  the three blocks are contractually required at that size. The third (`test_output_is_always_a_valid_
  subset_permutation`) isn't AC-mandated at 200 trials and asserts a structural invariant that could be
  folded into one of the other two loops (assert permutation-validity inside the same 200-trial loop) to
  cut ~200 test IDs with no coverage loss.
- **Fix (optional)**: Not worth doing given the measured runtime is negligible — noted only because the
  review brief asked for the measurement; the answer is "no action needed."

### S-4 — No test at the "20 tasks x 10 globs" scale named in the review brief
- **Location**: `tests/test_overlap_ranking.py` — random corpus caps at 12 tasks x up to 3 touches/task
  each drawn from a 6-glob pool (`_random_ready`/`_random_touches`/`_GLOB_POOL`, lines 144-162).
- **Observation**: `rank_wave`/`overlap_score`/`glob_intersection` are simple bounded-polynomial pure
  functions with no I/O; a 20x10 case is well within the margin the 200-trial property tests already
  exercise at smaller scale. No correctness or performance risk identified.
- **Fix (optional)**: Add one named test at that explicit scale for documentation/regression-anchoring if
  the team wants an anchor; not required.

## Verification performed

- `uv run ruff check .` — clean (whole repo). (Note: invoking ruff with the fixture `.txt` path passed
  explicitly force-lints it as Python and errors — this is a ruff CLI quirk from bypassing extension-based
  discovery, not a real issue; `ruff check .` normally excludes it and passes.)
- `uv run ruff format --check .` — clean, 264 files already formatted.
- `uv run mypy src` — exactly 4 pre-existing `_version.py` errors, as expected, no new errors.
- `uv run pytest tests/test_overlap_ranking.py tests/test_hotspots.py tests/test_e2e_cli_hotspots.py
  tests/isolation/test_git.py tests/test_cli.py -q -p no:cacheprovider --durations=10` — 814 passed in
  3.43s; slowest test (1.11s) is a pre-existing `test_git.py` wallclock-boundary test, not from this
  ticket's files.
- `uv run pytest tests/test_overlap_ranking.py tests/test_hotspots.py tests/test_e2e_cli_hotspots.py -q
  --durations=10` (new files only) — **661 passed in 0.72s**. No disproportionate runtime; over-
  parametrization concern does not materialize (see S-3).
- `uv run pytest -q -p no:cacheprovider` (full suite) — **3244 passed, 7 skipped, 0 failed in 108.26s**
  (stated baseline ~105s) — no regression, confirms STATUS.md's "no regression anywhere" claim.
- Coverage: `scheduling/__init__.py` 100%, `scheduling/overlap.py` 100% (75/75 stmts),
  `isolation/hotspots.py` 100% (104/104 stmts) — matches STATUS.md's claim.
- `git diff --stat -- engine.py models.py isolation/integrator.py` — empty; `grep -n "rank_wave|scheduling"
  engine.py` — no hits. Confirms the "Do NOT touch" boundary was honored and `rank_wave` is correctly
  flagged as not-yet-wired (owned by `T-En8Hd4`).
- `git diff -- cli.py` — confirmed the only changes are the command-list docstring line, the top-level
  import, `_resolve_templates_workspace_root`'s docstring, and the new `hotspots_cmd` function/registration
  — no encroachment into `T-Cx4Jf1`'s `--isolation`/`ao prune` territory.
- Manually reproduced the git path-quoting behavior behind C-3 in a scratch repo (`git log --name-only`
  without `-z`/`core.quotePath=false` on a `café.rs` file emits a quoted, octal-escaped path) and confirmed
  git log's rename handling is *not* a problem (`--name-only` lists old+new paths as separate lines, no
  ambiguous arrow syntax, unlike `--stat`) — the review brief's "renames" concern is unfounded, the
  "unicode" concern is confirmed (C-3).
- Read `templates/builtin/routed-runner/instructions/07-task-breakdown.md` (T-Tp7Zs2, read-only per
  boundary) — it references `.ao/hotspots.json` as free-form LLM-consumed guidance ("if present, use it to
  steer touches"), not a strictly-parsed structured input, so the shared-contract risk from any field-name
  drift is low; the JSON shape produced (`hotspots.py:71-100`) matches HLD §11 M8's
  `{"version","generated_at","window_days","repos":{"<id>":{"entries":[{"path","churn","conflicts",
  "weight"}]}}}` field-for-field.

## Dimension checklist (CLAUDE.md rubric)

- SOLID/KISS: checked, no issues — `overlap.py` stays single-purpose/pure, `hotspots.py` correctly
  separates IO (git/filesystem) from pure parsing (`parse_churn`), matching the HLD's own M8 dependency
  constraint.
- DRY: checked — one meaningful gap (C-2, established atomic-write idiom not reused); no other duplicated
  logic found (`resolve_overlap_preference` is correctly not re-derived, AC-14 honored in substance).
- Magic literals: checked, clean — `CONFLICT_WEIGHT`, `DEFAULT_SINCE_DAYS`, `DEFAULT_TOP_K`,
  `BASELINE_OVERLAP_WEIGHT`, `HOTSPOTS_SCHEMA_VERSION`, `_DEFAULT_HOTSPOTS_REPO_LABEL`,
  `_RUNS_STATE_GLOB` are all named constants.
- Pluggable architecture: checked, good — `compute_hotspots` takes an injected `GitRepo`/`cwd`/`clock`,
  never constructs its own; CLI wires the concrete instance.
- Spec/DAG correctness: checked — `.ao/hotspots.json` shape matches HLD §11 M8 byte-for-byte (see
  Verification); N/A for DAG/cycle concerns, this ticket adds no graph edges.
- Determinism/resume safety: checked, good — `rank_wave` is pure/deterministic (verified by 200-trial
  property tests + structural early-return), `compute_hotspots`'s `clock` is injected not called directly;
  `ao hotspots` is a standalone CLI command, not part of run resume, so no resume-corruption surface here.
- Errors/logging: checked, good — `load_hotspots`/`observed_conflicts`/`compute_hotspots` each degrade with
  exactly one warning, never raise into a run; `ao hotspots`'s CLI errors wrap with context and set exit 1
  consistently with the rest of `cli.py`.
- Testability: checked, excellent — 100% coverage, real temp-git-repo integration tests via the locked
  `conftest.py::make_repo`, `CliRunner` e2e per CLAUDE.md's outermost-boundary rule, fixed seeds throughout.
- Concurrency/rollout: partially flagged — C-2 (non-atomic write) is the one gap; schema is versioned
  (`"1.0"`), `merge_hotspots` correctly makes multi-repo re-runs idempotent and non-clobbering.

## Verdict: APPROVE WITH CHANGES

Must-fix before merge: **C-1, C-2, C-3**. None require a design change — each has a small, local, mechanical
fix. W-1 should be fixed in the same pass if convenient (same file, same function) but isn't a merge
blocker. S-1..S-4 are optional.
