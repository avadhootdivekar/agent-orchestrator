# No-repo-set mode — HLD

- **Status:** Proposed (design only; no code lands with this document). Decision record:
  [ADR-0022](adr/ADR-0022-no-repo-set-mode.md).
- **Date:** 2026-10-06
- **Ask:** overseer run `o-969ifr-requirement`, ask **A2** — "Enable the ao tool to be invoked
  *without* any repo set defined — in that case all inputs / outputs will be in the workspace
  directories only."
- **Related:** ADR-0003 (settings precedence), ADR-0013 / [`task-isolation-hld.md`](task-isolation-hld.md)
  (git isolation), ADR-0019 / [`cross-run-result-cache-hld.md`](cross-run-result-cache-hld.md)
  (result cache), [`multi-workspace-service-hld.md`](multi-workspace-service-hld.md).

## 1. Problem

Today every workflow **must** name a `repo_set`, and every entry point that builds an engine
needs a reposets file to resolve it. The reposet supplies two unrelated things:

1. the **workspace root** (`RepoSet.workspace_root`) — where `.orchestrator/`, artifacts and run
   state live; and
2. the **repo paths** (`RepoSet.repos` → `ctx.repo_paths`) — git checkouts agents may modify, and
   the substrate for isolation, diff-survival and the cache's HEAD guard.

A user who only wants to run agents over files in a plain directory (docs, research, data
pipelines — no git repo) must still invent a reposet. Goal: make both optional so a workflow can
run with **no `repo_set`, no reposets file**, and everything — inputs, outputs, run state — lives
under one workspace directory.

### Non-goals

- Making `--agents` optional (agents stay required; a no-agent workflow is meaningless).
- Changing any workflow that declares a `repo_set` — it behaves byte-identically (§9).
- A separate "workspace-only" workflow dialect; this is the same schema with one field optional.

## 2. Decisions (summary)

| # | Decision |
|---|---|
| D1 | `WorkflowSpec.repo_set` becomes **optional** (`str \| None = None`). Absent, `null` or `""` ⇒ **no-repo-set mode**. A non-empty string means exactly what it means today. |
| D2 | `--reposets` / `AO_REPOSETS` / `reposets:` become optional **iff the workflow has no `repo_set`**. When the workflow has one, the existing "reposets required" error is unchanged. A reposets file supplied anyway is still loaded and validated. |
| D3 | In this mode `ctx.repo_paths == {}`. Every consumer already iterates a dict, so "no repos" is the empty case, not a new code path (§5). |
| D4 | **Workspace root** (no-repo mode): `--workspace`/`-w` > `AO_WORKSPACE_ROOT` > project-config `workspace_root` > the directory **containing** the discovered `.ao/` config dir > `cwd`. Answers the charter's open question: default is the project-config location, then cwd. |
| D5 | With a `repo_set`, workspace resolution is **unchanged**: `AO_WORKSPACE_ROOT` > `reposet.workspace_root`. Project-config `workspace_root` is *not* newly applied there (it is declared today but read nowhere in the engine/CLI path; wiring it for repo-set runs would change behaviour). |
| D6 | Isolation (`worktree`) needs git repos. With none it degrades to `none` exactly like today's existing `no_git_repos` degrade, plus a **validate-time warning V13** so the user is told. Not fatal (CLI `--isolation` is applied after validation, so a fatal rule could not see it). |
| D7 | `RunState.repo_set` becomes `str \| None`; old `state.json` files still load. |
| D8 | Built-in templates make the `repo_set` param **optional** (default `""`); `ao new` prints a NOTE in no-repo mode and a WARNING if a reposets file is configured but the param is empty (§6). |

## 3. Spec and CLI surface

### 3.1 Workflow spec

```json
{ "version": "1", "id": "summarise-notes", "tasks": [ ... ] }
```

`repo_set` is simply omitted. A `before` validator on `WorkflowSpec.repo_set` normalises `""` and
whitespace-only to `None` (so a template that renders `"repo_set": ""` works without a templating
conditional). `specs/workflow.schema.json`: drop `repo_set` from `required`, allow
`["string","null"]`.

Not auto-detected: if the workflow omits `repo_set` but a reposets file exists, the reposets are
ignored for this run (no implicit "pick the only one" magic — that would make behaviour depend on
an unrelated file).

### 3.2 CLI

| Command | Change |
|---|---|
| `ao validate` | `--reposets` not required when the workflow has no `repo_set`. |
| `ao run` | same; **new** `--workspace/-w` (env `AO_WORKSPACE_ROOT` already honoured) so no-repo runs need no env var. |
| `ao resume` | same as `run` (new `--workspace/-w`). Resume reads workspace from the flag/env/project config, and the recorded run state is found under it. |
| `ao status` / `report-*` / `prune` | Already take `--workspace`/`AO_WORKSPACE_ROOT`; the spec-triplet fallback (`_resolve_workspace_root`) now also works for a no-repo workflow and, with *no* triplet either, falls through to the D4 chain instead of erroring. |
| `ao new` | `repo_set` param optional (§6); `--validate-only`/`--run` forward no `--reposets` when none. `--workspace` already exists. |
| `ao ui` / `ao service` | Already workspace-first (`--workspace`/cwd). Dashboard fixes in §4 row S-UI. |

Single resolver: add `cli._resolve_workspace(wf, reposet_map, workspace_flag)` implementing D4/D5
and replace the three duplicated `os.environ.get("AO_WORKSPACE_ROOT") or reposet_map[wf.repo_set]
.workspace_root` expressions (and `status`' / `_resolve_workspace_root`'s copies). One function, so
the rule cannot drift between commands (DRY rule in CLAUDE.md).

### 3.3 `.ao/config.yaml`

`reposets:` stays optional there (already `str | None`). `workspace_root:` becomes meaningful in
no-repo mode (D4). `ao init`'s template comment is updated ("required for workflows that declare a
repo_set").

## 4. Use-site inventory (every `repo_set` / `reposets` consumer)

Surveyed with `grep -rn "repo_set\|reposets" src/` (153 hits incl. tests) — all non-test sites:

| # | Site | Today | Required change |
|---|---|---|---|
| S1 | `models.py:731` `WorkflowSpec.repo_set: str` | required | Optional + normalising validator (D1). |
| S2 | `models.py:1273` `RunState.repo_set: str` | required | `str \| None = None` (D7). |
| S3 | `runstate.py:200` `new_run` copies `workflow.repo_set` | copy | none (copies `None`). |
| S4 | `spec.py:105` `cross_validate` raises `Unknown repo_set` | fatal | Guard: only when `repo_set is not None`. Docstring updated. |
| S5 | `spec.py:361` `_check_no_foreign_worktree_collisions` (V8) | `reposets.get(repo_set)` | Early-return when `repo_set is None` (no reposet ⇒ nothing to collide). |
| S6 | `spec.py:234,613` `_cross_validate_isolation` | V3/V8/V10 | Add **V13**: isolation active (`_any_task_isolated`) and `repo_set is None` ⇒ warning "isolation has no effect without repos; running un-isolated". Returned in the existing warnings list. |
| S7 | `cli.py:124` `_resolve_config_defaults` | returns triplet | none (reposets may stay `None`). |
| S8 | `cli.py:163` `_load_all` | errors if no reposets | Load workflow first; require reposets only if `wf.repo_set`; else `reposet_map = {}` (or the loaded file if supplied). Error text/exit code unchanged for repo-set workflows. |
| S9 | `cli.py:1258` (`run`), `:1511` (`resume`) workspace | `reposet_map[wf.repo_set]` | `_resolve_workspace` (D4/D5). |
| S10 | `cli.py:1722` (`status`), `:1767` (`_resolve_workspace_root`), `:1907` (`report-*` `--grade`) | same index | `_resolve_workspace`; `_resolve_workspace_root` falls back to D4 when no triplet. |
| S11 | `cli.py:3368–3505` `ao new` `--validate-only/--run`, `_invoke_run_in_process` | forwards `--reposets` if set | none beyond §6; already conditional. |
| S12 | `cli.py:2336` `ao init` hint text, `project_config.py:439` template | says reposets required | wording only. |
| S13 | `engine.py:692,755–756` `Engine.run` | `reposets[workflow.repo_set]` | `repo_paths = {} if workflow.repo_set is None else {...}` — the single engine change. `reposets` param keeps its signature (callers pass `{}`). |
| S14 | `engine.py` isolation/integration (`group_repos`, `_activate_isolation` ~2761, `_reconcile` ~2894) | `group_repos(ctx.repo_paths)`; `if not repos: _degrade("no_git_repos")` | **No code change**; verify with a test that empty `repo_paths` takes the degrade branch and logs once (D6). Reconcile path likewise a no-op. |
| S15 | `survival.record_git_start` (`engine.py:815`) | `discover_git_repos(repo_paths)` | No change: empty ⇒ `git_repos={}`, heads `{}`. `ao report-survival` already reports "no git repos recorded"; test it. |
| S16 | `cache/keys.py:127`, `cache/repo_state.py:149,183` | iterate `repo_paths` | No change: `repos={}`, `heads={}`, dirty-set empty. Workspace inputs are still content-hashed, so caching remains sound. **Test** that a no-repo hit/miss round-trips. |
| S17 | `cache/eligibility.py:135` `WORKFLOW_FIELD_COVERAGE["repo_set"]` | doc string | Wording: "repo paths (if any) enter via repo_paths". Key set unchanged (coverage test). |
| S18 | `executors/prompt.py:62` (`repos=` line), `executors/claude_cli.py:604`, `executors/fake.py:244` | iterate / `.get` | No change. Prompt renders `repos=` empty — **drop the line when empty** so agents are not told about phantom repos (golden-prompt test for the repo-set case stays identical). |
| S19 | Agent cwd: `engine.py:4042` `st.resolve(working_dir or ".")` | workspace-rooted already | No change; this is why §5 path-safety holds. |
| S20 | `ui/service.py:433–481` `_repo_set_names`, `_check_repo_set`, `_validate_before_spawn` | `workflow.repo_set not in reposets` | Skip when `workflow.repo_set is None`; reposets file no longer needed for validation; `REPO_SET_PARAM` empty ⇒ skip `_check_repo_set` (already guarded by truthiness). |
| S21 | `ui/processes.py:402–466`, `service/supervisor.py:504`, `service/boot_resume.py:64–170` | pass `--reposets` only if set | No change — already conditional on `None`; test boot-resume of a no-repo run (recovered `reposets=None`). Resume must also forward `--workspace`. |
| S22 | `ui/graph.py:334` placeholder shell `repo_set=_UNAVAILABLE_SHELL_REPO_SET` | sentinel string | No change (still valid; could pass `None` later). |
| S23 | `config.py:43` `load_reposets`, `validate.py` | file loaders | No change. |
| S24 | `bench/subjects.py:343–460`, `bench/spec.py:175` | bench subjects require reposets | Out of scope (benchmarks target repos); documented. |
| S25 | `outcomes.py:177` | comment: `repo_paths` need reposets | None — outcomes already works from run-dir artifacts. |
| S26 | Templates `routed-runner` / `overseer-runner` `template.yaml` + `workflow.json.tmpl` (`"repo_set": "{{ params.repo_set }}"`) | param `required: true` | Param `required: false`, `default: ""` (D8). Template instructions that assume git (branch-off, final-push stages) are unchanged; they remain useful only with repos — README gets a "workspace-only" note. |
| S27 | `specs/workflow.schema.json:8,13` | `repo_set` required | Optional (§3.1). `specs/reposet.schema.json` unchanged. |
| S28 | `RunState` consumers reading `.repo_set` (dashboard run list, `reporting`) | display | Render `—` for `None`; grep in implementation to confirm none index with it. |

## 5. Where things live; path safety (A2.3)

Layout is the existing one, with the workspace now explicit rather than reposet-derived:

```
<workspace>/                     # D4/D5 root
  .orchestrator/runs/<run-id>/   # state.json, status.json, run.log, snapshot (unchanged)
  .orchestrator/cache/           # result cache (unchanged)
  <task inputs/outputs>          # workspace-relative paths declared in the workflow
```

Path safety needs **no new mechanism**: `LocalFsArtifactStore.resolve` already raises
`ArtifactPathError` for any path escaping the root, task `inputs`/`outputs`/`output_manifest_path`
all go through it, and the agent cwd is `store.resolve(working_dir or ".")`. In repo-set mode a
reposet repo may legitimately live *outside* the workspace (`repo_paths` are `resolve`d too — the
reposet author controls them); in no-repo mode there is nothing outside the root to reach. So the
guarantee is strictly tighter. Tests (§8) assert: `../escape` input/output ⇒ `ArtifactPathError`,
absolute-outside-root ⇒ error, symlink-out ⇒ error, and that no file is written outside the
temp workspace after a full fake run.

`cwd` as default workspace (last D4 fallback) carries the usual footgun (running `ao run` in `$HOME`
writes `.orchestrator/` there). Mitigation: when the root is chosen by the cwd fallback, print one
line to stderr: `NOTE: no repo_set / workspace configured; using current directory as workspace
(<path>)`. Explicit sources stay silent.

## 6. Templates and `ao new`

`ao new <template> <slug>` (no `--param repo_set=`) now scaffolds a no-repo workflow:
`repo_set` renders `""` → normalised to `None`. `ao new` prints NOTE in that case; if a reposets
file is configured in the workspace config it prints a WARNING instead (likely forgot the param).
This is the single deliberate behaviour delta (omitting the param was an error). An example
workflow `specs/examples/workflow-no-repo-set.json` (two tasks, workspace-relative files) is added
and covered by `python -m agent_orchestrator.validate specs`.

## 7. Backward-compat guarantees

1. Any workflow with a non-empty `repo_set` takes the identical code path: `repo_paths`,
   workspace resolution (env > reposet), validation errors, isolation, cache keys, prompts.
2. `canonical_spec_json` (workflow identity sha, ADR-0017) is unchanged for existing specs:
   `repo_set` keeps its position and value; only specs with it omitted serialise `null`.
3. Old `state.json` (string `repo_set`) loads; new no-repo state with `repo_set: null` is an
   additive change. Older `ao` builds reading it would fail validation — acceptable (forward
   compat is not promised across installs; the self-hosting rule keeps prod pinned).
4. Error messages for "reposets required" / "Unknown repo_set" are verbatim unchanged.
5. The only intentional deltas: template param optional (§6), prompt omits an empty `repos=`
   line, V13 warning, new flags/NOTE line — none affect repo-set runs.

## 8. Implementation plan (units) and test strategy

Units are ordered; U1 must land first, the rest are mostly independent after it.

| Unit | Scope (files) | Notes / acceptance |
|---|---|---|
| **U1 model+spec** | `models.py` (S1,S2), `spec.py` (S4,S5,S6/V13), `specs/workflow.schema.json`, `cache/eligibility.py` wording | Optional field + validator; `cross_validate` accepts `repo_set=None` with `reposets={}`; V13 warning. |
| **U2 CLI resolution** | `cli.py` (S7–S12): `_load_all`, `_resolve_workspace`, `--workspace` on `run`/`resume`, `project_config.py` hint | One resolver; messages unchanged for repo-set. |
| **U3 engine+executors** | `engine.py` (S13, verify S14), `executors/prompt.py` (S18 empty `repos=`) | `repo_paths = {}`; isolation degrade verified; survival/cache verified (S15,S16). |
| **U4 dashboard/service** | `ui/service.py` (S20), `service/boot_resume.py`+`ui/processes.py` workspace forwarding (S21), run list `—` (S28) | No-repo run launches from dashboard and boot-resumes. |
| **U5 templates+example** | builtin `template.yaml`s (S26), `ao new` NOTE/WARNING, `specs/examples/workflow-no-repo-set.json` | Template tests updated; example validated. |
| **U6 tests** | `tests/` (see below) | Can be written alongside each unit; this unit owns the end-to-end test. |
| **U7 docs** | README ("Workspace-only mode"), CLI `--help`, `hld-agent-orchestrator.md` pointer, ADR status → Accepted, ROADMAP/ticket STATUS | Describe only what exists (A2.5). |

### Test strategy

- **Unit (U1):** `WorkflowSpec` accepts missing / `null` / `""` repo_set; `cross_validate(wf,{},agents)`
  passes; with a repo_set and `{}` still raises `Unknown repo_set` (regression); V13 emitted for
  `defaults.isolation=worktree` and for a per-task `isolation`; V8 early-return.
- **CLI (U2):** typer `CliRunner` against a `tmp_path` workspace: `ao validate --workflow wf.json
  --agents agents.json` (no reposets) exits 0; same workflow with `repo_set` and no reposets
  still exits 1 with the old message; workspace precedence table (flag, env, project config,
  `.ao` parent, cwd NOTE) for no-repo mode, and env > reposet for repo-set mode.
- **End to end (U6, A2.4):** temp workspace, `FakeExecutor` (writes declared outputs), real
  `Engine`/`RunStateStore`: 3-task DAG with chained workspace artifacts → `succeeded`; assert
  state under `<tmp>/.orchestrator/runs/`, `state.repo_set is None`, artifacts present, **nothing
  written outside `tmp_path`** (snapshot the parent dir listing). Then `ao resume` on a
  failed-then-fixed run, `ao status`, `report-timing` against the same workspace. Cache on/off
  variant; `isolation: worktree` variant asserts the degrade log + success.
- **Safety (A2.3):** traversal / absolute / symlink escapes on inputs, outputs, `working_dir`.
- **Dashboard (U4):** `ui.service` launch + validate-before-spawn with a no-repo workflow and no
  reposets configured; boot-resume candidate with `reposets=None`.
- **Backward-compat (A2.4):** the entire existing suite unchanged and green is the primary gate;
  plus golden checks — repo-set prompt text byte-equal, `canonical_spec_json` sha for a fixture
  spec equal to a recorded pre-change value.
- **Isolation of testing (A3):** run via `uv run pytest` / a temp venv only; never
  `uv tool install` or touch the live `ao`.

## 9. Risks and open questions

- **Risk — mis-set workspace.** cwd fallback may create `.orchestrator/` in an unintended dir →
  NOTE line (§5); explicit `--workspace` recommended in docs.
- **Risk — template stages assuming git** (branch-off, final-push in overseer/routed runners)
  fail on non-git workspaces. Not changed here; README note. A later epic could add a "no-git"
  variant of those templates.
- **Risk — `Engine.run(reposets, …)` signature** is public to tests; kept as-is, callers pass `{}`.
- **Open (low):** should a workflow without `repo_set` *and* exactly one configured reposet adopt
  it? Decided **no** (§3.1) — implicit behaviour keyed to an unrelated file. Revisit only if users ask.
- **Open (low):** make `--agents` optional for the same "zero config" story — out of scope here.
