# STATUS

- ID: `T-Sw5Hd9-swebench-import-provider`
- Updated At: 2026-07-22
- State: Done
- Owner: developer agent

## This update
- By: developer agent / Role: developer / Date: 2026-07-22
- Comment: Shipped the SWE-bench Verified importer (`bench/swebench_import.py`), the
  `swebench` `WorkspaceProvider` (`bench/swebench_provider.py`), the `swebench`
  optional dependency extra, and the committed, pinned 10-instance
  `benchmarks/suites/swe-verified-mini/` suite (generated via a REAL HuggingFace
  download at the pinned revision, not synthesized). Deviations from this task's
  original TASK.md pseudocode (module paths, `source` shape, checkout strategy) are
  documented in TASK.md's "Reconciliation note" and summarized below -- all were
  either explicitly authorized by the orchestrator's dispatch message (which carried
  verified ground-truth probes newer than the architect's original draft) or forced
  by a real, verified environment failure (partial-clone chaining).

## Evidence (AC-by-AC, TASK.md numbering)

1. **Importer -> schema-valid suite.json, N tasks, tier "large", `ao-bench validate`
   passes.** `import_swebench()` writes `suite.json` with `tier="large"`, one task per
   instance, `source={"type":"swebench","instance_id":...}` (minimal shape -- see
   TASK.md reconciliation note #2), `grader={"type":"swebench"}`,
   `instruction=tasks/<id>/instruction.md` (never inlined). Verified against the
   REAL committed suite: `uv run python -c "from agent_orchestrator.bench.spec import
   load_suite; s=load_suite('benchmarks/suites/swe-verified-mini/suite.json');
   print(s.tier, len(s.tasks))"` -> `large 10`. `uv run ao-bench validate --suite
   benchmarks/suites/swe-verified-mini/suite.json` -> `OK`. Also proven WITHOUT the
   `swebench` extra installed at all (a throwaway venv built from base `pyproject.toml`
   deps only, `datasets`/`swebench` confirmed absent via `importlib.util.find_spec`):
   `ao-bench validate` on this exact suite still exits 0 (AC4 evidence, same run).
   Tests: `test_import_swebench_writes_expected_suite_shape`,
   `test_import_swebench_loads_with_bench_spec`,
   `test_committed_swe_verified_mini_suite_is_valid` (validates the actual shipped
   artifact, not just a fixture).
2. **Instruction files are `problem_statement` at `tasks/<id>/instruction.md`, path-
   referenced.** Each instruction file is the shared safety/task preamble (fix the
   issue, run relevant tests, never touch `.grading`/`git config`/commit/reset) +
   `problem_statement`; `suite.json` only ever references it by relative path.
   Verified: every committed `tasks/<instance-id>/instruction.md` exists and starts
   with the preamble (spot-checked `django__django-12304`'s real issue text).
3. **`SweBenchWorkspaceProvider.prepare` checks out `base_commit` exactly.** Verified
   THREE ways: (a) network-free unit test with a real local fake-git origin (`git
   init` + 2 commits under `tmp_path`, cloned via a local path -- git treats this
   identically to a `file://` remote) --
   `test_prepare_checks_out_exactly_base_commit` asserts `git rev-parse HEAD ==
   base_sha`, working tree matches the base commit's content (not the later commit),
   `git status --porcelain` is clean, and `.git/info/exclude` contains
   `INSTRUCTION.md`. (b) full integration through `materialize_workspace`'s public
   seam (not calling the provider directly) --
   `test_materialize_workspace_end_to_end_with_swebench_source`. (c) a REAL, opt-in,
   network test (`@pytest.mark.swebench`, `AO_E2E_SWEBENCH=1`) cloning the actual
   `psf/requests` repo from GitHub and checking out the real committed instance
   `psf__requests-2931` -- `test_real_prepare_checks_out_one_pinned_instance` -- run
   once by hand, PASSED (`HEAD == 5f7a3a74aab1625c2bb65f643197ee885e3da576`, matches
   `instances.json`'s `base_commit`; `INSTRUCTION.md` materialized with the expected
   preamble text). Cache path: gitignored `playground/.tmp/swebench-repo-cache/` (a
   SIBLING of `BENCH_WORKSPACE_ROOT`, never nested inside it -- T-Wp4Nz5's forward
   note), NOT `playground/.tmp/swebench-repos/` as this task's original TASK.md named
   it -- same rationale (repo-local, gitignored, outside the per-run `ws` teardown
   path), different literal path.
4. **`datasets`/`swebench` imported only inside function bodies; core/`cli.py`/
   `validate` unaffected when the extra is absent.** Grep-verified: `swebench_import
   .py`'s only `datasets` import is inside `_load_rows_from_hf`'s function body;
   `swebench_provider.py` imports neither package at all (only `subprocess`, `json`,
   `threading` -- confirmed by reading the file). Proven with the extra ACTUALLY
   absent (not just "not imported at module scope" by inspection): built a fresh venv
   from base `pyproject.toml` (`pip install -e .`, no extras), confirmed
   `datasets`/`swebench` both absent via `importlib.util.find_spec`, then in THAT venv:
   `import agent_orchestrator; import agent_orchestrator.cli; import
   agent_orchestrator.bench.spec; import agent_orchestrator.bench.cli` all succeed,
   AND `ao-bench validate --suite benchmarks/suites/swe-verified-mini/suite.json`
   (the actual swebench suite, not just a non-swebench one) exits 0 via a
   `typer.testing.CliRunner` invocation in that venv. Test:
   `test_load_rows_from_hf_missing_datasets_package_raises_bencherror` (simulates the
   missing extra via `sys.modules["datasets"] = None`, asserts a clean `BenchError`
   with an install hint, never a raw `ImportError`/traceback).
5. **Pinned by `revision` (HF commit sha), committed.** `revision =
   "c104f840cc67f8b6eec6f759ebc8b2693d585d4a"` (`PINNED_REVISION` constant,
   ground-truth-verified: `load_dataset(..., revision=...)` returns exactly 500 rows,
   matching the epic's ground truth), committed in `instances.json`'s top-level
   `revision` field and as the importer's CLI default. Cross-checked: the REAL
   HuggingFace-driven import and a jsonl-fixture-driven import (same ids, same pinned
   revision, different `rows_loader`) produce **byte-identical**
   `suite.json`/`instances.json`/every `instruction.md` (diffed with `diff -r`, zero
   output) -- satisfies "both must produce identical output for the same revision"
   from the dispatch message.
6. **Selection documented, N=10, exact ids listed.** `instances.json`'s `_notes` field
   is auto-derived (difficulty/repo distribution computed FROM the data, never
   hand-written prose that could drift) plus the exact ids are the `instances` array
   itself. Chosen 10 (full rationale + sizes in the report below): 5x django, 2x
   pytest, 1x requests, 1x sphinx, 1x sympy; 6x "15 min - 1 hour", 3x "1-4 hours", 1x
   "<15 min fix"; every image manifest size verified via `docker manifest inspect`
   (no pull) BEFORE finalizing, recorded in `instances.json`. Test:
   `test_committed_swe_verified_mini_suite_is_valid` asserts repo count >= 4, sympy
   count <= 2, and the exact 6/3/1 difficulty mix against the real committed file.
7. **SI-1: no core import of `bench/`; `swebench_provider` absent from `sys.modules`
   after importing core.** Verified live (not just grep):
   `import agent_orchestrator; import agent_orchestrator.cli` then asserting no
   `"bench"`-containing module name is in `sys.modules` -- passes. Separately,
   `import agent_orchestrator.bench.cli` DOES register `swebench_provider` (by
   design -- the provider itself never imports the optional extra, module docstring
   explains why this is safe/necessary so `ao-bench run` can dispatch to it) while
   still never importing `datasets`/`swebench` (AC4 evidence covers this).

## Deviations (see TASK.md "Reconciliation note" for full detail)
1. Module paths: `src/agent_orchestrator/bench/{swebench_import,swebench_provider}.py`
   (not `benchmarks/importers/swebench_import.py` / `providers_swebench.py`) --
   per the orchestrator dispatch message's explicit instruction.
2. `source` is `{"type": "swebench", "instance_id": ...}` only -- `repo`/`base_commit`
   live in `instances.json`, looked up by the provider at `prepare()` time. Per the
   dispatch message's explicit deliverable text (deviates from this task's own
   TASK.md pseudocode, which the dispatch message superseded).
3. Checkout uses FULL local clones (cache -> repo_dir), not a chained
   `--filter=blob:none` partial clone -- the partial-partial chain hit real,
   reproduced "filtering not recognized by server" / promisor-fetch failures in this
   environment. Documented in `swebench_provider.py`'s module docstring as a verified
   finding, not a theoretical concern.
4. `KNOWN_GRADER_TYPES` gained `"swebench"` (dispatch message explicitly authorized
   this as a stub-note addition; T-Sg6Jf2 still owns the actual grader class).
5. Cache lives at `playground/.tmp/swebench-repo-cache/` (sibling of
   `BENCH_WORKSPACE_ROOT`), not `playground/.tmp/swebench-repos/` as originally named
   in TASK.md -- same rationale, different literal path.
6. Added a defense-in-depth `.git/info/exclude` entry for `INSTRUCTION.md` in every
   checked-out `repo_dir` (not requested verbatim in either TASK.md or the dispatch
   message, but a low-risk addition directly protecting T-Sg6Jf2's patch-extraction
   contract -- see Next actions).

## Test run (actual numbers)
- New tests: `tests/bench/test_swebench_import.py` (21 tests) +
  `tests/bench/test_swebench_provider.py` (12 tests) = 33 collected (31 run by
  default + 2 `swebench`-marked opt-in, skipped by default).
- Scoped run (mirrors the epic's parallel-safe ignore set):
  `uv run pytest tests/bench -q --ignore=tests/bench/test_runner.py
  --ignore=tests/bench/test_cli_bench.py --ignore=tests/bench/test_budget.py
  --ignore=tests/bench/test_dev_medium_suite.py` -> **285 passed, 3 skipped**
  (before this task's own new tests, within this same scoped run: 254 passed, 1
  skipped -- net +31 passed, +2 skipped, **zero regressions**; other in-flight
  parallel tasks' own tests are included in both numbers, all green).
- Full-repo regression check:
  `uv run pytest -q --ignore=tests/bench/test_runner.py
  --ignore=tests/bench/test_cli_bench.py --ignore=tests/bench/test_budget.py
  --ignore=tests/bench/test_dev_medium_suite.py -m "not real_llm and not swebench"`
  -> **1142 passed, 6 deselected**.
- Opt-in real run: `AO_E2E_SWEBENCH=1 uv run pytest tests/bench/test_swebench_import
  .py tests/bench/test_swebench_provider.py -q -m swebench` -> **2 passed** (real HF
  dataset row match + real GitHub clone/checkout of `psf__requests-2931`), run once
  by hand as required, reported here.
- `ruff check` / `ruff format --check` clean on all 7 touched files
  (`swebench_import.py`, `swebench_provider.py`, `spec.py`, `cli.py`,
  `test_swebench_import.py`, `test_swebench_provider.py`, `conftest.py`). `mypy src`
  (the actual CI gate, `.github/workflows/ci.yml`) clean except 4 PRE-EXISTING,
  out-of-scope `_version.py` errors (confirmed via `git log`/`git diff` untouched by
  this task). `mypy <touched-src-files>` directly: clean (0 errors). Running mypy
  directly against the two new TEST files surfaces the same "missing py.typed
  marker" noise `test_workspace.py` already exhibits under the identical invocation
  (pre-existing project-wide condition, not a regression -- CI only ever gates
  `mypy src`, confirmed by reading `.github/workflows/ci.yml`).

## Disk footprint (NFR-2 / risk R1 audit)
- Committed suite: 148K (`benchmarks/suites/swe-verified-mini/`, text only -- no
  binary/Docker content committed).
- Retained local cache after all verification: `playground/.tmp/swebench-repo-cache/`
  = **19M** (one repo, `psf/requests`, from the opt-in real test) -- well under the
  "≤3GB retained" bar; gitignored (`playground/.tmp/` pre-existing entry, no
  `.gitignore` edit needed). No Docker images were pulled (only `docker manifest
  inspect`, which never pulls layers) -- disk-neutral on that front.

## Chosen 10 instances (id / repo / difficulty / image size)
| instance_id | repo | difficulty | image size (manifest, no pull) |
|---|---|---|---|
| django__django-11138 | django/django | 1-4 hours | 1,108,549,492 B (~1.03 GiB) |
| django__django-11292 | django/django | 15 min - 1 hour | 1,108,665,836 B (~1.03 GiB) |
| django__django-12304 | django/django | <15 min fix | 1,111,165,127 B (~1.03 GiB) |
| django__django-14007 | django/django | 1-4 hours | 1,149,313,040 B (~1.07 GiB) |
| django__django-14053 | django/django | 15 min - 1 hour | 1,148,696,011 B (~1.07 GiB) |
| psf__requests-2931 | psf/requests | 15 min - 1 hour | 974,054,200 B (~0.91 GiB) |
| pytest-dev__pytest-10356 | pytest-dev/pytest | 1-4 hours | 987,762,475 B (~0.92 GiB) |
| pytest-dev__pytest-7571 | pytest-dev/pytest | 15 min - 1 hour | 985,208,578 B (~0.92 GiB) |
| sphinx-doc__sphinx-10466 | sphinx-doc/sphinx | 15 min - 1 hour | 1,061,988,599 B (~0.99 GiB) |
| sympy__sympy-20590 | sympy/sympy | 15 min - 1 hour | 1,046,529,606 B (~0.97 GiB) |

Mix: 6x "15 min - 1 hour", 3x "1-4 hours", 1x "<15 min fix" (sanity anchor); 5 distinct
repos (>= 4 required); sympy capped at 1 (<= 2 required); matplotlib/astropy (heavy
compiled-dep images) deliberately excluded; django favored for small images + fast
targeted test runs per the dispatch message's guidance. Selection prioritized low
`PASS_TO_PASS` counts within each difficulty bucket (faster targeted test runs) among
otherwise-similar-sized candidates.

## Risks / Blockers
- None outstanding for this task's own scope.
- **Forward risk flagged for T-Sg6Jf2** (see Next actions #1): this task's own
  `.git/info/exclude` addition protects `INSTRUCTION.md` from a naive `git add -A`,
  but T-Sg6Jf2 should still confirm its exact patch-extraction incantation.
- **Pre-existing, out of scope**: `_version.py`'s 4 mypy errors (confirmed pre-dating
  this task, untouched file, not in this task's file-ownership list).

## Next actions (forward notes for T-Sg6Jf2)
1. **Patch-extraction baseline**: `repo_dir` (`ws/repo`) is a real, independent,
   non-bare git checkout with its own `.git`, HEAD detached at exactly `base_commit`,
   clean working tree. Use a plain `git -C ws/repo diff` (no `git add -A` first) to
   extract the agent's patch -- this already excludes the untracked `INSTRUCTION.md`
   (materialized by `bench/workspace.py`'s `materialize_workspace`, not part of the
   pinned commit) since `git diff` alone only reports tracked-file changes. As
   defense-in-depth, `INSTRUCTION.md` is also pre-registered in
   `ws/repo/.git/info/exclude` (local-only, never committed), so `git status
   --porcelain` / `git add -A` stay clean too if your extraction ever uses either.
2. **Instance metadata for grading**: read `task.source.instance_id` off the task,
   then look up the FULL instance record (repo, base_commit, environment_setup_commit,
   difficulty, image name+size, `fail_to_pass`/`pass_to_pass` test id lists,
   `problem_statement`) from `<suite_base_dir>/instances.json` -- exactly the same
   suite-relative lookup `SweBenchWorkspaceProvider._load_instances_index` already
   does (feel free to reuse that function directly, or copy its shape:
   `{inst["instance_id"]: inst for inst in data["instances"]}`).
3. **Predictions-file shape**: `instances.json`'s `fail_to_pass`/`pass_to_pass` are
   already normalized to native `list[str]` (NOT the JSON-encoded-string shape the
   raw HF dataset uses) -- ready to feed directly into whatever predictions
   structure `swebench.harness.run_evaluation` expects alongside your extracted
   patch and `instance_id`.
4. **Cache layout** (yours to reuse or ignore): `playground/.tmp/swebench-repo-cache/
   <owner>__<name>/` is a full, persistent, shared git clone per repo -- if your
   Docker-eval step ever needs repo source outside `ws/repo` (it shouldn't, the
   agent's mutated `ws/repo` IS the input), this cache is available but is NOT
   guaranteed to be checked out at any particular commit (it's just an object store);
   don't read files out of it directly.
5. **Sequencing**: `spec.py`'s `KNOWN_GRADER_TYPES` already includes `"swebench"`
   (this task added it, stub-note comment in place) -- you only need to register the
   actual `SweBenchGrader` class into `GRADER_REGISTRY`
   (`registries.register_grader("swebench", SweBenchGrader)`), no `spec.py` edit
   required unless your design needs additional closed-list entries.
