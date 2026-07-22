# TASK: T-Sw5Hd9-swebench-import-provider

## Metadata
- Task ID: `T-Sw5Hd9-swebench-import-provider`
- Epic ID: `E-Bt4Xk9-complex-benchmark-tiers`
- Owner: developer agent
- Created: 2026-07-22
- Last Updated: 2026-07-22
- Status: Draft
- Estimate: 3.0 days

## Requirements Mapping
- FR-5 (SWE-bench importer + pinned instances + `swebench` WorkspaceProvider + optional deps)

## Description
Make the large tier real. Ship (1) an importer that reads a **committed, pinned instance list** and a **pinned dataset revision** of `princeton-nlp/SWE-bench_Verified` and generates `benchmarks/suites/swe-verified-mini/suite.json` (tier `large`, one task per instance, `source.type="swebench"`, `grader.type="swebench"`); (2) a `swebench` `WorkspaceProvider` that checks out the instance's repo at `base_commit` into `ws/repo`; (3) the optional `swebench`/`datasets` dependency extra. Grading is a separate task (T-Sg6Jf2). `swebench`/`datasets` must be lazy-imported so core + network-free CI are unaffected.

## File ownership (exclusive)
- `src/agent_orchestrator/bench/providers_swebench.py` — NEW: `SweBenchWorkspaceProvider` (+ instance→repo checkout). Registers `"swebench"` into `WORKSPACE_PROVIDER_REGISTRY`.
- `src/agent_orchestrator/bench/spec.py` — add `"swebench"` to `KNOWN_WORKSPACE_PROVIDER_TYPES` (one-line; sequenced AFTER T-Wp4Nz5).
- `benchmarks/importers/swebench_import.py` — NEW: standalone importer script (`python -m ...`), keeps optional-dep imports OUT of the always-loaded `cli.py`.
- `benchmarks/suites/swe-verified-mini/instances.json` — NEW: pinned `{dataset, revision, instance_ids: [...]}`.
- `benchmarks/suites/swe-verified-mini/suite.json` — NEW: generated + committed.
- `pyproject.toml` — add `[project.optional-dependencies] swebench = ["swebench", "datasets"]`.
- (read-only) `bench/registries.py`, `bench/workspace.py` (provider ABC).

## Inputs / Outputs
- Inputs: `instances.json` (pinned ids + dataset + revision); the HF dataset (network, run-time only).
- Outputs: a committed `suite.json`; at run time, `ws/repo` = repo@base_commit.

## Acceptance Criteria
1. **Given** `instances.json` with a pinned `dataset`, `revision`, and N instance ids **When** the importer runs **Then** it writes a schema-valid `suite.json` with N tasks, each `{id, category, source:{type:"swebench", instance_id, dataset, revision}, grader:{type:"swebench"}, instruction: <problem_statement file>}`, `tier:"large"` — and `ao-bench validate --suite` passes.
2. Instruction files are the instance `problem_statement` written to `tasks/<instance-id>/instruction.md` (path-referenced, never inlined into the spec).
3. **Given** a task with `source.type="swebench"` **When** `SweBenchWorkspaceProvider.prepare` runs **Then** `ws/repo` is the instance repo checked out at exactly `base_commit` (verified: `git -C ws/repo rev-parse HEAD` == base_commit), using a gitignored local clone cache under `playground/.tmp/swebench-repos/` (never `/tmp`, C2).
4. `swebench`/`datasets` are imported **only inside function bodies** in `providers_swebench.py`/`swebench_import.py`; **Given** the `swebench` extra is NOT installed **When** `import agent_orchestrator.bench.cli` and `ao-bench validate` on a non-swebench suite run **Then** both succeed (asserted in a test that simulates the extra missing).
5. Dataset access is pinned by `revision` (a HF dataset commit sha), committed — reproducible instance content.
6. Instance selection is documented in `instances.json` (comment/`_notes`): mixed repos, biased to smaller Docker images / faster test suites; N default = 10 (list the exact ids).
7. SI-1: no core import of `bench/`; `providers_swebench` not in `sys.modules` after importing core cli.

## Risks
- Repo checkout disk: full clones of large repos (e.g. django) are heavy on 37 GB. Mitigation: shallow/partial clone where the base_commit is fetchable (`git fetch --depth`), or a per-repo bare cache reused across instances of the same repo; clone cache under the gitignored `playground/.tmp/`. Document the disk budget alongside T-Sg6Jf2's image cleanup.
- Instance selection must prefer instances whose SWE-bench Docker image + test suite are small/fast (for T-Sg6Jf2's eval) — record the rationale so the pinned list is auditable.
- `datasets.load_dataset` caches under `~/.cache/huggingface` by default; set/΅document `HF_HOME`/cache location so it does not surprise the disk budget.

## Pseudocode / Algorithm
```text
# providers_swebench.py
class SweBenchWorkspaceProvider(WorkspaceProvider):
    def prepare(self, task, repo_dir, *, suite_base_dir, **_):
        import subprocess           # git; datasets only in importer
        inst = task.source          # {instance_id, dataset, revision}
        meta = _instance_meta(inst)  # repo (e.g. "django/django"), base_commit -> from dataset OR embedded in suite
        cache = BENCH_REPO_CACHE / meta.repo.replace("/","__")   # playground/.tmp/swebench-repos/<repo>
        ensure_clone(cache, meta.repo)                            # clone once, reuse
        run(["git","-C",cache,"fetch","origin",meta.base_commit])
        run(["git","--work-tree",repo_dir,"-C",cache,"checkout",meta.base_commit,"--","."])  # or worktree add
        assert rev_parse(repo_dir) == meta.base_commit
register_workspace_provider("swebench", SweBenchWorkspaceProvider)

# benchmarks/importers/swebench_import.py
def main(instances_path, out_suite):
    from datasets import load_dataset
    cfg = read_json(instances_path)          # {dataset, revision, instance_ids}
    ds  = load_dataset(cfg["dataset"], split="test", revision=cfg["revision"])
    by_id = {r["instance_id"]: r for r in ds}
    tasks = []
    for iid in cfg["instance_ids"]:
        r = by_id[iid]
        write(f"tasks/{iid}/instruction.md", r["problem_statement"])
        tasks.append({ "id": _slug(iid), "category": "bugfix",
                       "instruction": f"tasks/{iid}/instruction.md",
                       "source": {"type":"swebench","instance_id":iid,"repo":r["repo"],
                                  "base_commit":r["base_commit"],"dataset":cfg["dataset"],"revision":cfg["revision"]},
                       "grader": {"type":"swebench"} })
    write_json(out_suite, {"version":"1.0","id":"swe-verified-mini","domain":"software","tier":"large","tasks":tasks})
```
Note: embed `repo` + `base_commit` into each task's `source` at import time so the *provider* does not need `datasets` at run time (only `git`) — `datasets` is then needed only by the importer and the grader.

## Schemas / Interface Notes
- `source` object (swebench): `{type:"swebench", instance_id, repo, base_commit, dataset, revision}`.
- Optional dep: `[project.optional-dependencies] swebench = ["swebench", "datasets"]`; install via `uv sync --extra swebench`.
- Triggers/events: N/A. Artifacts: committed `swe-verified-mini/{instances.json,suite.json,tasks/*}`; run-time gitignored repo cache.

## Handoff Boundary
- Upstream: T-Wp4Nz5 (provider seam), T-Tr1Km8 (tier).
- Downstream: T-Sg6Jf2 (grader reads `ws/repo` + `task.source.instance_id`/`dataset`/`revision`). Coordinate spec.py/registries.py edits: this task adds the provider type + registration; T-Sg6Jf2 adds the grader type — sequence T-Sw5Hd9 → T-Sg6Jf2 to avoid the shared-file conflict.

## Artifacts
- Docs/comments: this folder. Large outputs: `benchmarks/suites/swe-verified-mini/` (committed, markdown-light — instructions are per-task .md).
