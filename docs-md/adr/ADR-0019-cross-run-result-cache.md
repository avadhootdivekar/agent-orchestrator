# ADR-0019 — Double-opt-in, content-addressed cross-run result cache

- **Status:** **Proposed** (Rev 3, 2026-10-05).
  - Rev 2 incorporated the Phase-4 consultation (`developer`, `reviewer`, `tester`,
    `dev-security`, `dev-critic`).
  - Rev 3 applies an independent early-gate review of the committed design (`94dac52`,
    GO-WITH-FIXES) and the manager's scope decisions.
  - It becomes **Accepted** when T-bdQZW4 reconciles it with the implementation.
  - Recommending mode `on` to users additionally waits for the G0 value check (HLD §22.5),
    which runs **after** the merge and does not block epic closure (D34).
- **Date:** 2026-10-04 (Rev 1), 2026-10-05 (Rev 2, Rev 3)
- **Deciders:** `architect` (design). Inputs: the `manager` analysis (items A–K) and scope
  decisions, the Phase-4 consultation, and the Rev 3 early-gate review.
- **Epic:** `E-Rc4Hk8-cross-run-result-cache`
- **Design:** [`docs-md/cross-run-result-cache-hld.md`](../cross-run-result-cache-hld.md).
  - §7.6 is the full decision log; the D-numbers below match it.
  - §7.7 is the threat model.
  - §23.4 (Phase 4) and §23.5 (Rev 3) map every finding to its disposition.
- **Related:**
  - ADR-0003 / ADR-0006: precedence policy; fill-in vs. kill switch.
  - ADR-0007: main-thread engine core and wave scheduler.
  - ADR-0013: per-task isolation.
  - ADR-0015: Claude *prompt*-cache scope, which is a different feature.
  - ADR-0017: `RunState`-level maps; validate a sha before splicing it into a path.
  - E-9h3m7k: cumulative usage accounting.

> **Terminology.** A "result cache" (this ADR) reuses the declared output files of a previous,
> identical, successful task instead of dispatching the agent again. It is unrelated to Anthropic
> prompt caching (`cache_read_input_tokens` and similar, ADR-0015).

## Context

**The cost of re-running.** Re-running a workflow re-dispatches every agent whose declared outputs
are missing. This happens on:

- fresh-run retries;
- runs in clean checkouts or fresh clones;
- re-runs after outputs were deleted.

Each of these spends real money on work that may already have been done identically.

**Existing reuse is not content-keyed.** The two existing mechanisms are:

- `skip_if_outputs_exist`, which means "the outputs exist";
- per-run `ao resume`.

Neither is keyed on content, so neither is a safe cross-run cache.

**Caching LLM-agent work is unlike caching a build step:**

1. **Non-deterministic output.** A cache changes semantics: it freezes one sampled result.
2. **Ambient state beyond declared inputs.** Agents read:
   - the repos named in every prompt;
   - outputs they edit in place;
   - the Claude CLI's own context (`CLAUDE.md`, `.claude/**`, `.mcp.json`), its version and its
     environment.
3. **Agent-writable workspace.** The workspace, including any cache directory in it, is writable by
   agents running as the same user. Cache files are therefore hostile input.
4. **Value is unproven for the primary consumer** (dev-critic). Fail-closed keys, committing tasks
   and per-epic output paths may make hits rare.

## Decision

Build a small, standard-library-only, workspace-local, content-addressed result cache in a new
`agent_orchestrator.cache` package:

- It is **off by default** and **double opt-in**.
- It is consulted by the engine through a narrow `ResultCacheHook` protocol.
- It is stored by `LocalFsCacheStore` behind **provisional** `CacheStore` / `CacheAdmin` ABCs.

**Ship it with a measurement mode (`shadow`) and the G0 protocol. Recommend enabling `on` only
after G0, which the parent or operator runs after the merge.**

### D1 / D26 — Double opt-in; operator mode; author policy; shadow

**Operator mode (`on | shadow | off`).** Precedence, highest first:

1. CLI: `--cache` = on, `--no-cache` = off.
2. `AO_CACHE`: `1|true|yes|on`, `0|false|no|off`, `shadow`. Any other value, including the
   deferred `refresh`, means **off**, with one warning.
3. `.ao/config.yaml`: `cache.enabled: true` together with `cache.mode` (`on` or `shadow`).
4. Otherwise off.

`--no-cache` and `AO_CACHE=0` are true kill switches.

**Author policy.**

- `policy(task)` = `task.cache` if set, else `defaults.cache` if set, else
  **`DEFAULT_TASK_CACHE_POLICY`**, a named constant in `cache/constants.py` whose value is
  **False**.
- That constant is **the single flip point**: if the parent prefers "operator enabled ⇒ every
  eligible task is cached unless it says `cache: false`", only this constant changes (with an
  addendum to this ADR). Test U-S4 pins it.
- A task injected by `emit_tasks` cannot opt itself in: its `true` is ignored.
- A task is considered only when the mode is not `off`, the policy is `true`, **and** it is eligible
  (D10).

**Shadow mode.** Full lookup and key computation, but no restore. A valid entry records `would_hit`
with an avoidable-cost estimate; the task still dispatches and still stores.

**Deferred in Rev 3: `refresh`.** Re-rolling one result is `ao cache rm <key>` followed by a
re-run (D27). A run-wide overwrite mode adds a third lookup semantics for little extra value.

**Why.**

- Freezing a non-deterministic result is an operator decision.
- Whether a task's whole effect is captured by its declared outputs is something only the author
  knows. Reviewer R1 made the author layer fail-closed; the manager proposal had it default to
  allowed. The Rev 3 reviewer confirmed double opt-in is justified, because guard 3 cannot see
  the untracked files agents create.
- `shadow` lets G0 measure value at no extra spend.

### D2 — Location `<workspace>/.orchestrator/cache/`

The directory is self-ignoring: it contains a `.gitignore` of `*`, a `CACHEDIR.TAG` and a versioned
`layout.json`.

**Root checks.** The root is created with mode `0o700`. It must:

- not be a symlink;
- be owned by the effective uid;
- have no group/other write bit;
- resolve inside the workspace.

**Per-operation checks.** Every store operation re-checks the root and `lstat`s each path component
before reading, writing, touching or unlinking anything (D33).

**Rejected:**

- `.ao/`, because it is committed config.
- An XDG user cache, because it is shared across workspaces, tenants, the bench and the service.
- The run directory, because it is not cross-run.

### D3–D8 — Key schema v1

**Key.** `sha256` of canonical JSON (sorted keys, compact, ASCII, no NaN) over these fields:

- `key_schema`
- `agent`: the 12 behaviour-relevant `AgentSpec` fields (`AGENT_KEY_FIELDS`)
- `argv`: the exact `build_claude_argv(effective_agent, normalized_prompt)`, extracted
  behaviour-identically from `ClaudeCliExecutor.execute`
- `executor_fingerprint`: CLI version; a small, closed, non-secret env allowlist; digests of
  `CLAUDE.md`, `.claude/{settings*,agents,commands,skills}` and `.mcp.json`
- `prompt`: the real `build_prompt`, rendered from a workspace-relative context
- digests of `instruction`, `general_instructions`, `inputs` and `dynamic_inputs`
- `outputs`: `[{path, prior}]`
- `repo_heads`

The golden vector **GV-1 (Rev 2)** is
`6646469e94a695fe1a994d35f54ca74e007262911255552b03c54ce2e5d0319f`. Rev 3 does not change the key.

| Decision | Content | Why |
|----------|---------|-----|
| D4 | The task id is **excluded**. So are the workflow id, run id, registry name, timestamps, absolute paths, timeout, retries and scheduling fields. | The prompt and argv never contain them (tripwires U-K7 and U-K8a). Outputs are in the key, so sharing requires identical output paths. Template instances therefore do **not** share entries. |
| D5 | `repo_heads` is **included** by default; the opt-out is `include_repo_heads: false`. Git-ness is decided from the filesystem: the nearest `.git` entry found walking up from the repo path **to the workspace root, never above it**. Only public `GitRepo` methods are used, with `GIT_OPTIONAL_LOCKS=0`. For a git repo, any read failure makes the task uncacheable. | Repo content is an undeclared input of every task. Rev 3: walking to `/` made a `.git` in `$HOME` turn every plain project dir into a "repo". A workspace nested inside a parent repository is now treated as non-git; the banner warns. |
| D6 | The **prior content of each declared output** is included: `"absent"` or a sha256. | Without it, an in-place-update hit would overwrite newer edits. |
| D7 | argv and fingerprint are included; a wrapper command basename or an unresolved model makes the task ineligible. `claude --version` is memoized per binary identity (resolved path, mtime, size), so an auto-updated binary is re-read. | Executor upgrades (`EFFORT_MAX_TURNS`, new flags) and context-file edits must miss without relying on a manual version bump (critic #4). |
| D8 | Directory digest is a sorted canonical manifest. A symlink or special file inside makes the task uncacheable. Only `.git`, `.orchestrator` and the task's own outputs are skipped, so noise files cause false misses, never false hits. | Determinism; link safety. |

### D9 — NFR-1 carve-out and lazy imports

- Only `agent_orchestrator.cache` reads artifact bytes.
- `engine.py` and the `ArtifactStore` ABC stay content-free.
- `engine.py` imports cache modules only under `TYPE_CHECKING` or lazily inside one private method.
- The texts `open(` and `.read(` never appear in `engine.py`.
- **Rev 3:** `runstate`, `usage`, `outcomes`, `cli` and `ui/runs` import `cache.report` inside the
  function and only when `state.result_cache` is non-empty. A cache-off engine process loads no
  cache module except `cache` and `cache.constants` (via `project_config`); the CLI path may also
  load `cache.settings`. Test I-1 asserts this in a subprocess.

### D10 / D29 — Fail-closed allowlist eligibility

**Classification.** Every field of `TaskSpec`, `AgentSpec`, `WorkflowSpec` and `WorkflowDefaults`
is classified. Tripwire tests fail on any unclassified field.

**Runtime unknown-field rules.** An unclassified field with a non-default value makes the task
ineligible. This applies to:

- `type(task).model_fields` (`unknown_task_field`);
- **the effective `AgentSpec` (`unknown_agent_field`, Rev 3)**: any field outside
  `AGENT_KEY_FIELDS | AGENT_NON_KEY_FIELDS`. Without it, a field added by a sibling epic would
  change agent behaviour without changing the key;
- the workflow and the workflow defaults (`unknown_workflow_field`).

**Structural exclusions.** A task is ineligible if any of these holds:

- it is an emit, manifest, router or loop member;
- it has hooks;
- its isolation is not `none`, or the run's integration is active;
- it has no outputs;
- its executor is outside `{claude_cli, fake}`;
- its verdict sidecar is not a declared output, or it is a breaker verdict source;
- one of its outputs is a control-file path;
- one of its paths is inside the cache directory;
- one of its outputs is sensitive (D29).

**Sensitive destinations (D29).** An output is refused, at key build **and** again at restore, if
its resolved path has:

- a component in `{.git, .claude, .github, .gitlab, .husky, .ao, .orchestrator}`; or
- a basename in `{CLAUDE.md, CLAUDE.local.md, AGENTS.md, .mcp.json, .envrc}`.

### D11 / D12 — Lookup placement; the engine owns the hit transition

**Placement.** The lookup runs in `_prepare_and_maybe_dispatch`:

- **after** `should_skip`, isolation resolution, `_apply_join`, the missing-inputs check and
  dynamic-input collection;
- **after** any approval or human gate (E-Ag7Pw3);
- **before** the budget gate.

**Hit transition.** It is performed by the engine's own private `_result_cache_lookup`, not by the
plugin:

- `status = succeeded` and `outputs_present = True`;
- timestamps are set;
- `task.end` is emitted with `cached: true`;
- the task is added to `done`, and state is saved;
- the call returns the existing `DispatchPrep(signal="skipped")`.

**Counters on a hit.**

| Counter | Effect |
|---------|--------|
| `dispatch_cycle` | **Keeps its increment.** R-21 stays monotonic, and `ui/activity` finds no transcript for a hit cycle. Both `usage` dispatch-counting sites exclude current hits explicitly; a hit's real carried spend (hit after an earlier paid attempt) still counts. |
| `attempts` | Unchanged (0 for a first-pass hit). |
| `cumulative_*` | Untouched. |
| Breakers | Not evaluated. |
| Budget | A **stale** charge left by a crash in the **previous dispatch cycle** (`dispatch_cycle - 1`) is reversed. The existing reversal block is extracted verbatim into `_reverse_stale_charge`, shared by the budget gate and the hit path, and it emits the existing `budget.resume_reverse` event. Otherwise untouched. |

**Rejected (Rev 1):** decrementing `dispatch_cycle`, because it broke R-21 and the activity view;
mutating engine state from the plugin.

### D13 — Store only a final settled success, behind three guards

The store runs on the main thread at the top of the `succeeded` branch of `_settle_completed_task`.
It uses the pending token that the lookup put on `_RunContext`. It stores only if all three guards
pass:

1. The key, recomputed with the pre-seeded priors, equals the lookup key.
2. No repo HEAD has moved. This is checked even when `include_repo_heads` is false.
3. No tracked file outside the declared outputs and `.orchestrator` changed (tracked-only status
   plus mtime and size, read with `GIT_OPTIONAL_LOCKS=0`).

**Rev 3: guard 3 is lazy.** Its lookup-time snapshot is taken only once an outcome is known to be
storable (a miss or a shadow `would_hit`). A hit never pays for it and never fails because of it.
A snapshot failure makes the outcome not storable (`store_reason: repo_worktree_probe_failed`); it
never makes the task ineligible.

Untracked and non-git edits are a documented residual risk. Dirty tracked edits that existed
**before** the lookup are not in the key either; that residual is accepted (Rev 3).

### D14–D16, D35 — Persistence, reporting and the field contract

**Persistence.** `RunState.result_cache: dict[str, ResultCacheRecord]` holds one record per task,
never on `TaskRunState`.

**Currency.** A record is current iff:

- `rec.dispatch_cycle == ts.dispatch_cycle`; and,
- for a hit, the task is `succeeded` **and** `rec.ended_at == ts.ended_at`.

No engine code drops stale records.

**Field contract (D35, Rev 3).** Every per-task surface (`ResultCacheRecord`, the `status.json`
task view, the dashboard task payload) exposes exactly `hit`, `key`, `saved_cost_usd`,
`saved_tokens` (input + output) and `saved_seconds`. Run-level and cross-run surfaces expose
`hits`, `saved_cost_usd`, `saved_tokens` and `saved_seconds`. Splits such as `saved_input_tokens`
are additive. On the record, `hit` and `saved_tokens` are derived by a model validator, never
trusted from input.

**Container name.** The object is named `result_cache`, not the brief's `cache`, because `cache_*`
names already mean Claude **prompt**-cache counters in usage, status and the dashboard. The manager
flags this to the parent (HLD OQ-7).

**Reporting.**

- Report surfaces are additive and omitted when there are no current records: `status.json`, the
  summary line, the `report-usage` `result_cache` object, and null dashboard fields.
- `report-outcomes` reports `settle_reason: "cached"`.
- `saved_*` is an estimate copied from the entry. It is never netted into cost and never fed to
  breakers, which keeps E-9h3m7k's spend numbers exact.

### D17–D19 — Provisional split ABCs; versioned layout; bounded maintenance

**Interfaces.**

- `CacheStore` (hot path) and `CacheAdmin` (maintenance) are marked **provisional**; a remote
  backend will need an output-I/O seam (non-MVP).
- `LocalFsCacheStore` derives from `CacheStore` first (T-U7ckfd); T-HjxNQ0 adds the `CacheAdmin`
  base and its methods, so every intermediate state is importable and type-correct.
- The store sees only its own namespace. The only exception is the
  `LocalFsCacheStore.for_workspace` factory.

**Layout.**

- `entries/v1/<k[:2]>/<k>.json`: canonical JSON bytes, with the major version in the path.
- `blobs/<s[:2]>/<s>`: shared.
- `tmp/`.

**Write protocol.**

- Blobs are written before the entry, via tmp plus `os.replace`.
- A same-key race resolves as last-writer-wins, and both versions are self-consistent.
- Entries and directories from **other versions are never overwritten or deleted**. The blob mark
  phase collects hex tokens from every entry file of every version.

**Eviction.**

- LRU by entry mtime, down to 90% of `max_bytes`.
- TTL from `created_at`.
- Blob sweep with a 1 h grace period.
- Inline enforcement is bounded twice: it **defers** when the store holds more than
  `INLINE_PRUNE_MAX_ENTRIES` entries **or** more than `INLINE_PRUNE_MAX_ENTRY_FILE_BYTES` of entry
  files, because the mark phase reads every entry file.
- `clear` moves the root's contents into a trash directory and verifies the removal.
- `verify` is read-only; `--repair` is deferred (Rev 3).

### D20 / D21 — Restore protocol and entry metadata

**Restore.**

1. Destinations come **only** from the current spec. The entry manifest is compared as a set and
   never used as a path.
2. Each blob is staged next to its destination (`O_EXCL|O_NOFOLLOW|O_CLOEXEC`, `0o600`), with its
   size and sha verified. Each destination is re-validated:
   - `realpath(dest) == dest`;
   - inside the workspace;
   - not sensitive.
3. Only then: `chmod(mode & 0o755)` and `os.replace`.

Every restore failure is a miss and is **not storable** for that dispatch. A corrupt entry or blob
is also evicted.

**Entry metadata.** Entries hold a bounded, non-sensitive key *summary*. They never hold the argv,
prompt template, `extra_args` or the key document.

### D22 / D23 / D24 / D25 — Naming, bench, approvals, integration

| Decision | Content |
|----------|---------|
| D22 | Naming: "result cache", `ao cache`, `--cache`, `AO_CACHE`, `cache:`, `ResultCache`, `RunState.result_cache`, `cache.*` events. The factory is `ResultCache.from_settings`, chosen so that `engine.py` static audits never see `open(`. |
| D23 | `ao-bench` forces the cache off: argv `--no-cache` **and** env `AO_CACHE=0`. |
| D24 | Approval gates (E-Ag7Pw3) are rejected by the allowlist until classified RULED, and must run before the lookup seam. |
| D25 | Runs with `integration.active` are ineligible in MVP. |

### D27 / D28 / D30–D34 — Operability and robustness

| Decision | Content |
|----------|---------|
| D27 | `ao cache rm <key\|prefix>` gives single-entry invalidation (critic #2). **Deferred in Rev 3:** `rm --run R --task T`, which would read the agent-writable `state.json` (an extra trust boundary). |
| D28 | Hostile data is parsed by **total functions**. One parse boundary maps any failure (including `RecursionError` and `UnicodeDecodeError`) to `CacheIntegrityError`. Models are strict and bounded (`AwareDatetime`, finite numbers, `max_length`). Internal reads are FIFO-safe (`O_NONBLOCK` + `S_ISREG`). |
| D30 | `cache.miss` carries per-component digests (`sha256[:12]`) so an operator can see why a key changed. |
| D31 | One `safeio` module, plus an AST guard: no pickle, eval or `shell=True`, and no bare `open(` outside `safeio`. |
| D32 | Error boundary. The engine-facing calls never raise. Expected errors become a miss or skip with an ERROR log. An unexpected error emits `cache.disabled` and disables the cache for the rest of the run. `strict=True` re-raises, in tests only. |
| D33 | **An unsafe cache path is never followed or evicted (Rev 3).** Every store operation, including `delete_entry`, `touch_entry`, `has_blob`, `read_blob` and `delete_blob`, runs the root and component checks first. A symlinked `entries/`, `entries/v1/`, shard or blob directory raises `CacheUnsafePathError`; the coordinator turns it into a miss (`unsafe_path`) that is not storable and is **never evicted**. Rev 2 evicted every integrity error while `delete_entry` skipped the checks, so `unlink` could follow a planted link (CWE-59). |
| D34 | **G0 is a post-merge, operator-owned measurement (Rev 3).** This repository has no representative workload: `specs/self-dev/` holds only agent and reposet files, the built-in templates use per-instance output paths, and the bench forces the cache off. The epic therefore ships a runnable protocol (`docs-md/result-cache-g0-protocol.md`), the measurement fields (`ao report-usage --json` → `result_cache`; `ao cache stats --json`), a report template and a smoke validation. The parent or operator executes G0 on a real consumer workflow (finplan, with consent); it does not block epic closure. |

## Alternatives considered

| ID | Alternative | Disposition |
|----|-------------|-------------|
| ALT-1 | Status quo: `skip_if_outputs_exist` plus `ao resume` only | Rejected. Not content-keyed, so not safe across runs. |
| ALT-2 | Adopt a library: `diskcache`, `joblib.Memory` / `cachetools`, DVC run-cache, Bazel REAPI | Rejected. Adds dependencies; joblib pickles (code execution from an agent-writable directory); no restore-to-spec semantics; command-line keys rather than agent contracts. |
| ALT-3 | Single operator gate, with the author layer defaulting to allowed (manager proposal A) | Rejected (reviewer R1). Not fail-closed for tasks with undeclared effects. Replaced by double opt-in (D1); the default stays one named constant, so this alternative is a one-line flip if the parent chooses it. |
| ALT-4 | Spec-only opt-in, or fill-in semantics where the spec enables caching | Rejected. A workflow file must not change semantics for an operator who did not ask for it. |
| ALT-5 | Key on the raw dispatched prompt plus output paths (the brief's literal list) | Rejected. The prompt holds absolute paths and no contents; omitting HEADs, priors and argv gives stale and destructive hits. |
| ALT-6 | A user-level (XDG) or cross-workspace cache | Rejected for MVP. Tenant leakage; breaks bench and service isolation. |
| ALT-7 | **Executor-level `CachingExecutor`**, which wraps the executor inside the worker, works inside isolation worktrees and sees hooks (dev-critic #7) | **Deferred; the recorded migration path.** The brief places the lookup in the engine, and the executor level forces a budget pre-charge on hits. Revisit if isolation becomes the default (roadmap §3.4, R-15). |
| ALT-8 | **Explicit `ao run --reuse-from <run-id>`**, where the operator vouches for a source run (dev-critic) | **Recorded fallback.** No hidden store and no key contract, but not what the brief asks for. Pursue it if G0 shows a negligible would-hit rate. |

## Consequences

### Positive

- Identical re-runs avoid real spend.
- With the mode off, the engine executes and imports no cache code, and `status.json` and CLI text
  are byte-identical (HLD §8.7.5 evidence: I-1, I-2, U-AST-E, U-LZ).
- Unchanged: the run loop, the wave scheduler, isolation, budget, breakers, `should_skip` and
  `prepare_resume`. The only edit to existing budget code is the behaviour-identical extraction of
  `_reverse_stale_charge`.
- Hostile or corrupt cache data degrades to a miss and never kills a run; a planted link is never
  followed.
- `shadow` mode and the G0 protocol make the value measurable before anyone relies on it.

### Negative / trade-offs

- **Conservative hit rate.**
  - Any commit invalidates all keys, unless the operator opts out.
  - Prior-output keys mean `skip_if_outputs_exist: false` tasks hit only from identical starting
    content.
  - A CLI auto-update changes every key.
  - Double opt-in adds author friction.
  - Noise files inside declared input directories cause false misses.
- **Residual stale-hit sources.**
  - Untracked or non-git edits outside declared inputs.
  - Dirty tracked edits that existed before the lookup (accepted in Rev 3).
  - A workspace nested inside a parent repository is treated as non-git (HEAD not keyed); the
    banner warns.
  - `~/.claude/**`, the network, and env vars outside the allowlist.
  - Model-alias retargeting, bounded by the 30-day TTL.

  Mitigations: `cache: false`, `ao cache rm`, `--no-cache`.
- **Not defended:** a deliberate same-uid poisoner, or an active TOCTOU race. Recommend
  `--no-cache` for untrusted workflows; HMAC and `dir_fd` walking are non-MVP.
- **Version-bump rule.** A behaviour-relevant executor change that is visible in neither argv nor
  the fingerprint **must** bump `KEY_SCHEMA_VERSION`. Pointer comments in `models.py` and
  `claude_cli.py` say so.
- **Upgrade cosmetics.** The new spec fields change static spec hashes, so resuming a pre-upgrade
  run logs `run.spec_changed_on_resume` once.
- **Downgrade.** An older `ao` that resumes a run drops `result_cache` records; `run.log` keeps the
  `cache.hit` audit trail.
- **Windows** is best-effort.

### Strategic (escalated to the parent)

The critic's "no-go until value is validated" is answered with `shadow` mode and the G0 protocol,
not by the architect overruling the brief. G0 runs after the merge. If it is negative, the feature
stays shipped but off, and ALT-8 is the next step.

### Follow-ons (non-MVP)

- Remote or shared backends.
- HMAC-authenticated entries.
- `dir_fd`-walking I/O.
- Caching under isolation or with hooks (ALT-7).
- Directory or dynamic outputs.
- Dependency-aware invalidation.
- Resolved-model recording.
- User-level context fingerprinting.
- `ao validate` warning, `ao cache explain`, dashboard launch controls.
- **Deferred in Rev 3:** the `refresh` mode, `ao cache rm --run R --task T`, and
  `ao cache verify --repair`.
- Executing G0 on a real consumer workflow (post-merge, parent or operator).
- Fixing the missing-inputs `dispatch_cycle` reset in `engine.py` (separate bug ticket).

## Revision history

| Rev | Date | Change |
|-----|------|--------|
| 1 | 2026-10-04 | Initial decision set. |
| 2 | 2026-10-05 | Phase-4 consultation folded in: double opt-in and modes; argv and fingerprint in the key; three store guards; the engine owns the hit transition and keeps the cycle increment; stale budget charges are reversed; total parsing and the error boundary; sensitive destinations; versioned layout and provisional split ABCs; `rm`; alternatives ALT-7/ALT-8 recorded; G0 escalated. |
| 3 | 2026-10-05 | Early-gate review (GO-WITH-FIXES) and manager scope decisions: repository detection stops at the workspace root; `unknown_agent_field`; unsafe cache paths never followed or evicted (D33); G0 becomes a post-merge, operator-owned protocol (D34); exact field contract and `result_cache` container (D35); named author-policy default; lazy guard 3 and lock-free reads; inline prune bounded by bytes; lazy `cache.report` imports; shared `_reverse_stale_charge` with the `budget.resume_reverse` event; `refresh`, `rm --run/--task` and `verify --repair` deferred. |
