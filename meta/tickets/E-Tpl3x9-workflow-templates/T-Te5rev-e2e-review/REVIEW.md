# Review: E-Tpl3x9 workflow-templates (T-Te5rev, review half)

Reviewer: `reviewer` agent · Date: 2026-07-24
Scope: `git diff 8858761..c8ca466` (commits `404986a` feat(templates) core+CLI+builtin+frontend,
`c8ca466` feat(ui) dashboard template endpoints), agent-orchestrator repo, branch
`ad/workflow-templates`. Plus the sibling `ao-runner-finplan` workspace's
`workflows/epic-runner/template/`, `new-epic-run.sh`, `.ao/config.yaml`, `README.md`
(read-only assessment, no git there — see §6). Uncommitted changes in agent-orchestrator
(concurrent tester agent's e2e test + docs work) were ignored per instructions. No files
were edited except this one.

Verification method: every finding below is labeled **[EXECUTED]** (independently
reproduced by running real code from this diff against a throwaway `tmp_path`/`tmpdir`,
transcripts kept in this session) or **[READ]** (established by reading the code/tests,
not independently executed). Full test suite run: `1640 passed, 7 skipped, 0 failed`
(no regressions) — **[EXECUTED]**.

---

## Summary

The core templates module (`src/agent_orchestrator/templates/__init__.py`) is well
structured — clean dataclass API, pydantic manifest validation, deliberate separation of
path-template tokens vs content rendering, and the write-side path containment
(`instance_dir`/`dirs`/`files[].target`/`assets[].target`) is genuinely solid because it
routes through `LocalFsArtifactStore.resolve()`, which normalizes and checks containment
regardless of how a `..`/absolute path got introduced. However, two **read/render-side**
gaps break the "workspace-contained paths" and "structured specs" project goals outright,
and are trivially triggerable by a typo, not just malice: (1) `source:` in `files[]`/
`assets[]` accepts absolute paths and reads **outside the template directory entirely**
(confirmed: exfiltrates arbitrary host files/directories into the workspace), and (2)
param values are substituted into JSON templates with **zero escaping**, so a value
containing a stray `"` corrupts or injects arbitrary structure into the generated
`workflow.json` — which can then be launched via `start: true` with no schema validation
in between. A third, confirmed-via-execution issue: the dashboard's
`POST /api/templates/{name}/instances` reuses the CLI's ad-hoc-path-accepting
`load_template()`, so `name` is not actually constrained to the vetted list
`GET /api/templates` returns — an unregistered, un-vetted template directory reachable by
a bare (slash-free) name relative to the server's CWD can be instantiated. The CLI
(`ao new --run`), the built-in `routed-runner` template's fidelity to the original script,
and the frontend are all solid — verified by direct execution and cross-checks. Goal
alignment: the read-side/render-side gaps are direct violations of CLAUDE.md's "safe by
default" and "declarative structured specs" principles and the HLD's own "all paths
workspace-contained" NFR-1; everything else is consistent with the HLD and epic scope.

---

## Critical (BLOCKER — must fix before merge)

### B1. `source:` in `files[]`/`assets[]` is not contained to the template directory — arbitrary host file/directory read into the workspace

**Location**: `src/agent_orchestrator/templates/__init__.py:171-176` (`_FileEntry._no_traversal`),
`:194-198` (`_AssetEntry._no_traversal`), `:266-268` (`_reject_traversal`), `:314-321`
(`_read_template_file`, the vulnerable join at line 315: `p = template_dir / rel`), and
`:637` (`_materialize_asset`'s `source_path = template_dir / asset.source`).

**Observation**: `_reject_traversal` only rejects a `".."` path *segment*; it never checks
for an absolute path. `source:` values then flow into `template_dir / rel`
(`_read_template_file`) or `template_dir / asset.source` (`_materialize_asset`) — plain
`pathlib.Path.__truediv__`. Python's `/` operator **discards the left operand entirely**
when the right operand is absolute (`Path("/a") / "/etc/passwd" == Path("/etc/passwd")`),
so an absolute `source:` silently escapes `template_dir` with no error. Unlike the
write-side (`target:`, `instance_dir`, `dirs`), which is contained via
`LocalFsArtifactStore.resolve()`'s normalize-then-check-prefix logic (a real containment
guard, confirmed correct — see "Verified safe" below), the read side has **no equivalent
guard at all**.

**[EXECUTED]** — reproduced twice, end to end through the real `instantiate()`:
1. A `files[]` entry with `source: /tmp/secret-xxx/super-secret.txt` (outside any
   template/workspace dir) → `instantiate()` copies the file's contents verbatim into the
   run instance (`created: [..., 'runs/.../exfiltrated.txt']`), confirmed by reading the
   written file back. (The call afterwards raises `TemplateError` for missing
   `workflow.json`, but the leak already happened — see B1a below on partial-write
   cleanup.)
2. An `assets[]` entry with `source: /tmp/secretdir-xxx` (an arbitrary directory outside
   the template dir, standing in for e.g. `~/.ssh`) → `instantiate()` **recursively
   copies every file in that directory** into the workspace (`created` lists both
   `id_rsa` and `id_rsa.pub`), `instantiate()` returns *successfully* (201-equivalent),
   no error at all.

**Why it matters**: This is exactly the failure mode review-focus #1 asked to check
("read outside the template dir via source?") and it is real, not theoretical — no
crafted malice is even required, a fat-fingered absolute path in a hand-authored
`template.yaml` (or one generated by tooling that doesn't quote/relativize a path
correctly) silently exfiltrates arbitrary host files the server process can read into a
workspace directory that later gets committed, browsed, or handed to an agent. Directly
violates HLD NFR-1 ("all paths workspace-contained") and CLAUDE.md's "safe by default."

**Fix**: Apply the same containment discipline already used for write-side paths.
Concretely: (a) in `_reject_traversal`, also reject `Path(value).is_absolute()` for
`source` fields (cheap, closes the demonstrated case immediately); (b) more robustly,
route `_read_template_file`'s and `_materialize_asset`'s source resolution through a
small `_safe_join(template_dir, rel)` helper that computes `(template_dir / rel).resolve()`
and raises `TemplateError` unless the result `.is_relative_to(template_dir.resolve())` —
mirroring `LocalFsArtifactStore.resolve()`'s pattern — so relative-traversal-through-symlinks
edge cases are covered too, not just literal absolute paths.

**B1a (folded in, same location)**: `instantiate()` has no rollback on failure — the
leaked file/asset copy from B1 (or any partial write before a later `TemplateError`, e.g.
the missing-`workflow.json` check at `_validate_rendered_workflow`) is left on disk. Not a
new finding on its own (idempotent re-scaffold is the documented recovery model and it
does work — see "Verified correct" below), but worth noting alongside B1 since it means a
failed `instantiate()` call is not "as if it never happened" — partial artifacts,
including any exfiltrated file, persist.

---

### B2. Unescaped `{{ params.* }}` substitution into JSON templates permits structural injection into the generated `workflow.json`

**Location**: `src/agent_orchestrator/templates/__init__.py:467-477` (`_render`, pure
text substitution, no format awareness), `:538-561` (`_resolve_params`, no character
validation on free-text param values); triggering template:
`src/agent_orchestrator/templates/builtin/routed-runner/workflow.json.tmpl:5`
(`"repo_set": "{{ params.repo_set }}",` — `repo_set` is a **required**, no-default,
free-text param per `template.yaml:18-22`) and reproduced identically in
`ao-runner-finplan/workflows/epic-runner/template/workflow.json.tmpl:5`.

**Observation**: `_render()` is a context-free regex substitution — it has no idea it's
writing into a JSON string literal vs. a JSON number vs. Markdown. `_resolve_params()`
places no restriction on param value characters beyond `enum` membership (and `repo_set`
declares no enum). A `repo_set` value containing a raw `"` therefore breaks out of the
JSON string it's meant to sit inside.

**[EXECUTED]** — instantiated the real built-in `routed-runner` template with
`params={"repo_set": 'fin-plan", "injected_key": "pwned'}`. The rendered
`workflow.json`'s `repo_set` line becomes:
```
"repo_set": "fin-plan", "injected_key": "pwned",
```
which is **syntactically valid JSON** (`json.loads()` succeeds) containing an
attacker-controlled extra top-level key — i.e. a demonstrated, general **template/JSON
injection primitive**, not merely "a typo breaks the file." `_validate_rendered_workflow`
(`__init__.py:669-702`) only checks the parsed JSON is a dict with `id`/`tasks`/non-empty
`prompt_path` — it does **not** run the full `WorkflowSpec` pydantic model or
`cross_validate`, so an injected-but-schema-shaped payload (e.g. an entire extra task
object with a valid `agent`/`instruction`/`depends_on: []`, which is well within reach of
this primitive since arbitrary JSON structure can be spliced in) would sail through
`instantiate()` unnoticed.

**Why this is a BLOCKER, not a MINOR**: per `tests/ui/test_templates_api.py` and
`T-Tu1api-ui-endpoints/STATUS.md` item 2 (confirmed by reading `service.py:299-367`),
`DashboardService.create_instance(..., start=True)` launches through
`ProcessSupervisor.launch_run` at the **same shallow validation depth as `start_run`** —
i.e. no full spec/schema validation between `instantiate()` and actually spawning
`ao run`. `ao run` itself *would* catch a structurally-invalid spec via its own
`_load_all()`, but a well-formed injected task (valid agent id + instruction path) is
indistinguishable from a legitimately-authored one by the time it reaches that check. This
directly undermines the project's core "declarative structured specs validated against a
schema" principle: the injection happens *before* schema validation, in a way schema
validation cannot subsequently distinguish from legitimate authorship.

**Fix**: Minimal/immediate: constrain free-text param values in `_resolve_params()` to a
safe character allowlist by default (reject `"`, backslash, control/newline characters)
unless a param explicitly opts out — closes the demonstrated quote-breakout without
requiring a templating-engine rewrite (keeps NFR-1's "no Jinja2"). Longer-term/more
correct: either JSON-escape substituted values automatically for `.json`/`.json.tmpl`
targets (detect via the file's rendered target suffix), or add a `pattern:` constraint
option to `_ParamSpec` so authors of free-text params like `repo_set` can pin an
identifier-safe shape. Independently: `_validate_rendered_workflow` should be strengthened
to run the real `WorkflowSpec` pydantic model (not just the ad hoc dict/id/tasks/
prompt_path check) before `instantiate()` reports success, closing the "shallow check
today, full check only if the caller separately validates" gap this finding relies on.

**Verified untested**: `tests/test_templates.py::TestPathSafety` covers exactly one
scenario (a `..`-traversal param value escaping via a rendered *target* path,
`__init__.py` write side) — there is no test anywhere in `tests/test_templates.py`,
`tests/test_e2e_cli_templates.py`, or `tests/ui/test_templates_api.py` for either B1
(absolute `source:`) or B2 (quote/structural injection via a param value). The
1640-passed green suite does not exercise either.

---

## Major (should fix before merge)

### M1. Dashboard `POST /api/templates/{name}/instances` does not restrict `name` to the discovered/vetted template list — falls through to the CLI's ad-hoc-path affordance

**Location**: `src/agent_orchestrator/ui/service.py:318-321` (`create_instance` calls
`load_template(name, self.workspace_root, self._config)` directly) and
`src/agent_orchestrator/ui/app.py:140-141` (`@app.post(".../{name}/instances") def
create_instance(name: str, ...)`, plain single-segment FastAPI path param, no allow-list
check against `service.list_templates()`).

**Observation**: `templates.load_template()` (`__init__.py:439-459`) is deliberately
dual-purpose per HLD §2.1 point 3 / §2.5: it accepts either a discovered template's
`name:`, **or an ad-hoc filesystem path** (`ao new /path/to/template ...`, a CLI-only
affordance). `DashboardService.create_instance` reuses this same function unmodified,
so the HTTP `name` path segment inherits the ad-hoc-path branch too — even though HLD §2.6
only ever documents name-based lookup against the `GET /api/templates` list, and the ad-hoc
path form is explicitly scoped to the CLI (§2.1/§2.5), never the dashboard.

**[EXECUTED]**, two variants against the real FastAPI app via `TestClient`:
1. Multi-segment absolute path via `%2F`-encoded `name`: Starlette decodes the path
   *before* route matching, so this does not match `/api/templates/{name}/instances` at
   all (falls through to the SPA catch-all → `405 Method Not Allowed`) — this specific
   vector is **not** exploitable, confirmed.
2. **Single-segment bare directory name, exploitable**: registered only `mini` as the
   vetted template (`GET /api/templates` returns `["mini"]`); a second, completely
   unregistered template directory named `sneaky` exists elsewhere. With the server
   process's CWD `chdir`'d into that directory's *parent* (a realistic layout — operators
   commonly launch `ao ui` from the workspace root, and `name="."` would hit the workspace
   root itself the same way), `POST /api/templates/sneaky/instances` returns **201** and
   actually scaffolds the unregistered template — confirmed via direct request/response
   inspection.

**Why it matters**: violates the HLD §2.6 contract (name-based lookup against the
discovered list) and widens what an unauthenticated-but-loopback-trusted caller (any local
process/page, per the documented threat model in HLD §3 non-goals) can reach through the
API versus what the UI ever shows them. Combined with B1 (arbitrary-file-read via a
malicious `source:`), this becomes a chain: point the dashboard at *any*
`template.yaml`-containing directory the server process can read (not just registered
ones) and that manifest's `source:` fields can then read further outside the template dir
again. Neither `tests/ui/test_templates_api.py` nor the service-level tests exercise a
`name` that isn't already in the fixture's registered set.

**Fix**: In `DashboardService.create_instance`, resolve `name` against
`discover_templates(...)` only (the same list `list_templates()` returns) rather than
calling the CLI's general `load_template()` — e.g. `next((t for t in
discover_templates(self.workspace_root, self._config) if t.name == name), None)`, raising
the existing `TEMPLATE_NOT_FOUND_PREFIX` `DashboardError` on no match. Leave
`load_template()`'s ad-hoc-path branch for the CLI only.

---

## Minor / Warnings (worth fixing, not merge-blocking)

### W1. No file-level atomicity or locking for concurrent `instantiate()` calls on the same instance id

**Location**: `src/agent_orchestrator/templates/__init__.py:705-829` (`instantiate()`) —
every write is a direct `target_path.write_text(...)` (e.g. lines 792, 623, 603), no
temp-file+rename, no advisory lock.

**Observation [READ]**: Two concurrent `instantiate()` calls on the same `slug_or_id`
(e.g. a double-clicked "Create & run" in the dashboard, or a retried `ao new`) can
interleave writes to the same `workflow.json`/`prompt.md`, since nothing serializes
access to a given instance directory. `mkdir(parents=True, exist_ok=True)` is safely
idempotent, but the file-content writes are not protected against a torn/interleaved
write from a second concurrent caller.

**Why it matters**: HLD explicitly frames `instantiate()`'s idempotent-re-scaffold
behavior as a first-class contract (§2.4), and CLAUDE.md calls for "safe concurrent task
updates." The risk window is narrow (scaffold-time, small files, low frequency) so this is
not launch-blocking, but it's a real gap against the stated idempotency contract.

**Fix**: Write via temp-file-in-same-dir + `os.replace()` for each target (atomic on
POSIX), and/or hold a short-lived lock file (e.g. `<instance_dir>/.instantiate.lock`) for
the duration of one `instantiate()` call.

### W2. `_resolve_id` can misinterpret a bare slug that happens to match the full `id_pattern` shape

**Location**: `src/agent_orchestrator/templates/__init__.py:514-530` (`_resolve_id`) —
it tries the full-id regex match *before* falling back to bare-slug validation.

**[EXECUTED]**: with the default `id_pattern = "e-{rand6}-{slug}"`, a user-intended bare
slug `"e-abc123-widget"` (6 lowercase-alnum chars sandwiched between hyphens, purely by
coincidence) matches the compiled full-id regex, so `_resolve_id` returns `slug="widget"`
instead of the user's whole intended slug — `id_` itself stays correct (same literal
string), but the `{{ slug }}` content variable and any downstream text render as
`"widget"`, silently dropping context the user typed. Narrow (needs a slug that happens to
look like `e-<6 alnum>-<rest>`) but a real least-surprise violation.

**Fix**: Prefer the bare-slug interpretation when the caller's own value doesn't already
look like something previously generated by this exact tool (e.g. require an explicit
`--resume`/"treat as full id" signal), or at minimum document the precedence in `ao new
--help` / HLD.

### W3. `ao new --run` re-parses project config 2–3× per invocation

**Location**: `src/agent_orchestrator/cli.py` — `new_cmd` calls
`_load_project_config_or_none()` once (line ~1429) for template discovery, `_load_all` →
`_resolve_config_defaults` reloads it again for `--validate-only`/`--run`, and (when
`--run`) the freshly in-process-invoked `run` command's own `_load_all` reloads it a third
time.

**Observation [READ]**: Each parse is a cheap, deterministic YAML read of an immutable
file within one process invocation, so this is not a correctness bug — `_load_project_config_or_none`'s
own docstring already accepts a 2×-per-command cost as "not worth the added state." `ao
new --run` adds a third call on top of that existing pattern. Performance-only; not
blocking.

**Fix (optional)**: Not necessary to fix now; if it's ever revisited, thread the already-loaded
`cfg`/`workflow_path` through to `_invoke_run_in_process` instead of re-deriving.

---

## Nit / Suggestions

### N1. `except SystemExit: return` in `new_cmd` (cli.py, mirrors the pre-existing pattern in `run`) is dead code under the installed typer/click versions

**[EXECUTED]**: confirmed `typer.Exit.__mro__` is `(Exit, RuntimeError, Exception,
BaseException, object)` in the pinned `typer==0.26.7`/`click==8.4.2` — `typer.Exit` is not
a `SystemExit` subclass, so `except SystemExit` never catches it; `_load_all`'s own
internal `raise typer.Exit(1)` calls simply propagate past this handler (harmlessly —
Click's `main(standalone_mode=False)` still catches `Exit` correctly further up and
returns the right exit code, confirmed by direct reproduction). Not a new bug (copied from
the existing `run` command's identical pattern, consistent with "follow existing
patterns"), just worth a follow-up cleanup since it silently does nothing.

### N2. finplan `epic-runner` template's `workflow.json.tmpl` still emits `"name": "routed-runner: {{ id }}"` rather than `"epic-runner: {{ id }}"`

Already self-flagged by the implementer in `T-Tw4fpl-finplan-wiring/STATUS.md` deviation
#2, kept deliberately for byte-equivalence with the pre-existing script's output. Cosmetic
(a display `name`, not an id), fine to leave as a documented follow-up rather than block
on.

---

## Verified correct (confirmed, not findings)

- **Write-side path containment** (`instance_dir`, `dirs`, `files[].target`,
  `assets[].target`) — routes through `LocalFsArtifactStore.resolve()`
  (`src/agent_orchestrator/artifacts.py:57-67`), which normalizes and checks
  `full.startswith(root + sep)` regardless of how many `../` or how an absolute path was
  smuggled in (e.g. via a malicious `id_pattern`'s literal segments feeding the `{id}`
  token). **[EXECUTED]** `tests/test_templates.py::TestPathSafety` plus my own
  `.resolve()` trace confirm this is solid — the *write* side of the "can a
  malicious/typo'd template.yaml write OUTSIDE the workspace" question is genuinely
  guarded. Only the *read* side (B1) is not.
- **`when:` delete-on-unset semantics** (pin-bug fix, HLD §2.2) — **[EXECUTED]**
  independently: `--type bug` writes `outputs/forced-type.txt`; re-scaffolding without
  `type` deletes it and the file is gone. Matches HLD prose and `T-Tw4fpl`'s claimed
  evidence exactly.
- **Idempotent re-scaffold / prompt-conflict** — `_write_prompt_text`
  (`__init__.py:582-604`) correctly skips on identical content, raises `TemplateError` on
  differing content, and `keep_existing` on the `prompt.md` entry means an unrelated
  re-scaffold (no `prompt_text` passed) never touches an existing prompt. Covered by
  existing tests (`TestCreateInstanceService::test_identical_re_instantiate_is_idempotent_not_a_conflict`,
  `::test_conflicting_prompt_on_an_existing_instance_raises_prompt_conflict`) and read
  directly.
- **No second-order `{{ }}` re-expansion** — explicitly checked per review-focus #1's
  "rendering injection (params containing `{{`)": **[EXECUTED]** a param value containing
  literal `{{ params.secret }}` text is substituted once and never rescanned (`re.sub`
  with a function does a single left-to-right pass over the *original* text), so this
  specific injection class is not exploitable. (B2's JSON-quote-breakout is a different,
  and exploitable, class — see above.)
- **`ao new --run` exit-code fidelity** — **[EXECUTED]** reproduced the exact mechanism
  `T-Tc0r3a-core-templates/STATUS.md` claims: `click.BaseCommand.main(standalone_mode=False)`
  returns the invoked command's `typer.Exit(N)` payload as a plain `int` (not a
  `SystemExit`) when the command raises it — verified both for the success path and for a
  command that raises deep inside its call stack. Traced every exit path of the `run`
  command (`cli.py:640-869`) and confirmed it *always* ends in an explicit
  `raise typer.Exit(...)`, so `int(command.main(...))` in `_invoke_run_in_process`
  (`cli.py`) never receives `None`.
- **Built-in `routed-runner` DAG fidelity** — `grep`-verified directly against
  `workflow.json.tmpl`: exactly the 30 claimed task ids, all 11 claimed circuit breakers,
  and — the specific load-bearing check this review was asked to make —
  `skip_if_outputs_exist: false` on exactly `git-branch-off`, `classify`, `task-breakdown`,
  `full-test`, `test-run` (every other task is `true`). **[EXECUTED via grep, not full
  byte-diff]** — matches `T-Tb3rtr-builtin-routed-runner/STATUS.md`'s claims.
- **Frontend request shaping** (`ui/src/components/TemplateLaunch.tsx`,
  `ui/src/api.ts:73-77`) — `CreateInstanceRequest` fields match the FastAPI
  `CreateInstanceRequest` pydantic model exactly; `name` is `encodeURIComponent`-escaped
  before going into the URL (so the legitimate UI flow cannot trigger M1 — M1 requires a
  non-browser HTTP client). Blank param values are correctly omitted so the server's own
  `default` applies; required-param enforcement is duplicated client-side as a UX nicety
  without weakening the server-side check. `NewRun.tsx`'s mode-toggle diff is a clean,
  mechanical wrap with no dead code.
- **Dashboard error-status mapping** (`app.py:150-158`) — 404/400/409 dispatch on
  `TEMPLATE_NOT_FOUND_PREFIX`/`PROMPT_CONFLICT_PREFIX`/fallback is correct and covered by
  both service- and HTTP-level tests; the deliberate prefix-based disambiguation (avoiding
  a substring collision between `load_template`'s and `_render`'s differently-scoped
  "unknown template..." messages) is a sound, documented design choice.

---

## 6. ao-runner-finplan sibling-workspace assessment (plausibility, not re-derivation)

Per instructions, I did not re-run `ao new`/`new-epic-run.sh` against the live
`ao-runner-finplan` workspace (would create real run-instance artifacts there, outside
this review's write scope) — I read the files as they stand and cross-checked structural
claims:

- `.ao/config.yaml`'s `templates: [../workflows/epic-runner/template]` — the `../` prefix
  (deviation #1 in `T-Tw4fpl-finplan-wiring/STATUS.md`) is present and matches the
  existing `agents: ../specs/agents.json` / `reposets: ../specs/reposets.json` anchoring
  convention in the same file. Plausible and consistent — **[READ]**.
- `new-epic-run.sh` is 295 lines (`wc -l` confirms), matches the claimed "729 → 295"
  slimming; `require_template_support()` mirrors `require_routing_support()`'s structure
  and stale-`ao` remediation message closely; scaffolding is delegated to
  `"$AO_BIN" new epic-runner "$EPIC_ID" "${NEW_ARGS[@]}"` with `--param type=...`/
  `--prompt-file` passthrough exactly as claimed; `--resume`/`--extend-*` passthrough and
  the interactive confirm UX (`--run`/`--auto`/`--validate-only`) are all intact and
  unchanged from what the header comment documents. **[READ]**.
- `workflow.json.tmpl` task-id-set diff: `diff <(grep task ids from finplan tmpl)
  <(grep task ids from builtin tmpl)` is **byte-identical** (empty diff) —
  **[EXECUTED, grep-based]**. `repo_set: "fin-plan"` and budget thresholds `75.0`/`1500.0`
  are hardcoded exactly as the deviations doc describes (workspace policy, not a param).
  The flagged `"name": "routed-runner: {{ id }}"` artifact (deviation #2) is present as
  described (see N2 above).
- `template.yaml`'s `type` param, `dirs`/`files` shape, and `required_agents` list match
  the builtin's shape 1:1 apart from the intentional finplan-specific deltas (hardcoded
  `repo_set`/budgets, no `assets:` since instructions already live in place) — **[READ]**.

Overall: the equivalence evidence in `T-Tw4fpl-finplan-wiring/STATUS.md` is **plausible
and corroborated** by direct inspection; I found no contradiction between its claims and
the files on disk. Note that this sibling-workspace code inherits both B1 and B2 (same
templates engine, same unescaped-JSON-param pattern for `repo_set` in its own
`workflow.json.tmpl:5`) — fixing B1/B2 in `agent-orchestrator` fixes both call sites.

---

## Testing notes

- **What to mock**: for B1's fix, no new mocking needed — `tests/test_templates.py`
  already has the fixture-building helpers (`_write_template`) to add a
  `source: <absolute tmp_path>` case analogous to the existing
  `TestPathSafety::test_param_value_cannot_escape_workspace_via_rendered_target`. For M1's
  fix, `tests/ui/conftest.py`'s `write_template` + a second, deliberately *unregistered*
  template dir is enough (as demonstrated in this review's reproduction) — no new fixture
  infrastructure required.
- **What to integration-test**: B2 needs an integration-level test that renders the real
  `builtin/routed-runner/workflow.json.tmpl` (not just a synthetic fixture) with a
  quote-containing `repo_set` and asserts either a `TemplateError` (if the fix is
  input-validation) or that the output round-trips through the *real* `WorkflowSpec`
  pydantic model, not just dict/id/tasks — this is the level at which B2's actual
  consequence (spec integrity) lives, not just "is regex-shaped."
- **Coverage gaps confirmed present** (i.e., not test flakiness, actual absence): no test
  exercises an absolute `source:` in either `files[]` or `assets[]`; no test exercises a
  param value containing structurally-significant characters (`"`, `\`) for a JSON-target
  template; no test exercises `POST /api/templates/{name}/instances` with a `name` outside
  the fixture's registered set. All three should be added alongside the corresponding
  fixes, not just as regression tests after the fact.
- **Pure logic already well isolated**: `_render`, `_resolve_id`, `_resolve_params`,
  `_compile_id_pattern` are all pure functions independently unit-tested without I/O —
  good testability posture worth preserving as fixes land.

---

## Verdict

**Not merge-ready as-is.** Two BLOCKERs (B1: unbounded file/directory read via `source:`
outside the template dir; B2: unescaped param substitution enabling JSON/spec structural
injection, reachable through `start: true` with no intervening schema validation) must be
fixed — both have concrete, scoped, same-file fixes described above and are not
architectural rewrites. One MAJOR (M1: dashboard `name` not restricted to the discovered
list) should be fixed in the same pass since it compounds B1's blast radius and is a
one-line change in `service.py`. The three Warnings (W1 concurrency/atomicity, W2 id/slug
regex edge case, W3 redundant config reloads) and two Nits are safe to defer to follow-up
tickets. Everything else reviewed — CLI exit-code fidelity, built-in template DAG
fidelity, frontend request shaping, dashboard error-status mapping, idempotent
re-scaffold/`when:`-delete semantics, and the ao-runner-finplan wiring's claimed
equivalence — held up under direct execution/inspection and needs no changes.

---

## Fixes applied (post-review remediation)

By: developer | Role: developer | Date: 2026-07-24

All three merge-blocking findings (B1, B2, M1) are fixed on this branch
(`ad/workflow-templates`), smallest-correct-change scope, `cli.py` untouched. Full
mapping:

| Finding | Fix | Location | Regression test |
|---|---|---|---|
| **B1** — absolute `source:` escapes `template_dir` | (a) `_reject_traversal` now also rejects `Path(value).is_absolute()`, applied at manifest-validation time to every field it already guards (`files[].source`/`target`, `assets[].source`/`target`, `dirs[]`). (b) New `_safe_join(template_dir, rel, desc)` helper — `(template_dir / rel).resolve()` then `is_relative_to(template_dir.resolve())`, mirroring `LocalFsArtifactStore.resolve()` — now used by `_read_template_file` and `_materialize_asset`'s single-file/dir source resolution, closing the relative-traversal-through-symlink gap beyond the literal `..` segment check. (c) Asset-directory copies additionally `resolve()` and re-check containment for every `rglob()`-yielded file before reading it, so a symlink planted inside the asset dir can't smuggle an outside file into the copy. | `src/agent_orchestrator/templates/__init__.py`: `_reject_traversal`, `_safe_join` (new), `_read_template_file`, `_materialize_asset` | `tests/test_templates.py::TestPathSafety::{test_absolute_source_in_files_rejected_at_manifest_time, test_absolute_source_in_assets_rejected_at_manifest_time, test_symlink_escape_blocked_at_read_site_for_file_source, test_symlink_escape_blocked_in_asset_directory_copy}` |
| **B2** — unescaped `{{ params.* }}` into JSON | (a) `_render()` gained `escape_json: bool = False`; when `True` (set by both content-render call sites — `instantiate()`'s `files[]` loop and `_materialize_asset_file` — whenever the resolved target's suffix is `.json`) each substituted value is JSON-string-escaped via new `_json_escape_value` (`json.dumps(value)[1:-1]`, characters only, no added quotes) unless it's already a bare JSON number (`^-?\d+(\.\d+)?$`), which renders unescaped so unquoted numeric splices (budget thresholds) keep working. (b) `_validate_rendered_workflow` now additionally calls `spec.load_workflow(candidate)` (the same JSON-Schema + `WorkflowSpec`-pydantic loader `ao validate`/`ao run` use — reused, not re-implemented) after its existing shallow id/tasks/prompt_path check, wrapping any `OrchestratorError` in `TemplateError`. | `src/agent_orchestrator/templates/__init__.py`: `_json_escape_value` (new), `_render`, `_materialize_asset_file`, `instantiate`'s `files[]` loop, `_validate_rendered_workflow` | `tests/test_templates.py::TestJsonEscaping::{test_quote_breakout_payload_renders_as_inert_string (review's exact payload), test_backslash_and_control_chars_also_escaped, test_rendered_workflow_failing_full_spec_validation_raises, test_builtin_routed_runner_budget_thresholds_render_as_bare_json_numbers}` |
| **M1** — dashboard `name` not restricted to discovered list | `DashboardService.create_instance` no longer calls `templates.load_template()` (which has the ad-hoc-filesystem-path branch); it now resolves `name` via exact match against `discover_templates(self.workspace_root, self._config)` — the same list `list_templates()`/`GET /api/templates` returns — and raises the existing `TEMPLATE_NOT_FOUND_PREFIX`-prefixed `DashboardError` for anything else. `load_template` import removed from `service.py`. The CLI's `ao new` keeps ad-hoc-path support unchanged (`cli.py` not touched). | `src/agent_orchestrator/ui/service.py`: `DashboardService.create_instance` | `tests/ui/test_templates_api.py::TestCreateInstanceService::test_ad_hoc_filesystem_path_as_name_is_rejected`, `::TestTemplatesApi::test_post_cwd_relative_path_to_unregistered_template_returns_404` (reproduces this review's exact "sneaky" scenario, chdir included) |

Verification: `uv run pytest -q` → 1651 passed, 7 skipped, 0 failed (review's baseline was
1640 passed/7 skipped; delta includes both these regression tests and the concurrent
e2e-suite task's additions). `ruff check`, `ruff format --check`, and `mypy` clean on
every touched file (mypy's handful of pre-existing errors in test files are unchanged
before/after this diff, confirmed via `git stash` A/B compare). `ao new routed-runner ...
--validate-only` smoke-tested end-to-end through the real CLI against the real built-in
template — scaffolds and reports "OK: rendered workflow is valid", confirming the new
full-`WorkflowSpec` validation layer (B2b) does not regress the shipped template.
