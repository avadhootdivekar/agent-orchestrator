# ADR-0019 — Double-opt-in, content-addressed cross-run result cache

- **Status:** **Proposed** (Rev 2, 2026-10-05).
  - Incorporates the Phase-4 consultation (`developer`, `reviewer`, `tester`, `dev-security`,
    `dev-critic`).
  - It becomes **Accepted** when T-bdQZW4 reconciles it with the implementation.
  - Recommending mode `on` to users additionally waits for the G0 value check (HLD §22.5).
- **Date:** 2026-10-04 (Rev 1), 2026-10-05 (Rev 2)
- **Deciders:** `architect` (design). Inputs: the `manager` analysis (items A–K) and the Phase-4
  consultation.
- **Epic:** `E-Rc4Hk8-cross-run-result-cache`
- **Design:** [`docs-md/cross-run-result-cache-hld.md`](../cross-run-result-cache-hld.md).
  - §7.6 is the full decision log; the D-numbers below match it.
  - §7.7 is the threat model.
  - §23.4 maps every Phase-4 finding to its disposition.
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

**Ship it with a measurement mode (`shadow`). Recommend enabling it only after the G0 value check.**

### D1 / D26 — Double opt-in; operator mode; author policy

**Operator mode (`on | shadow | refresh | off`).** Precedence, highest first:

1. CLI: `--cache` = on, `--no-cache` = off.
2. `AO_CACHE`: `1|true|yes|on`, `0|false|no|off`, `shadow`, `refresh`. Any other value means **off**,
   with one warning.
3. `.ao/config.yaml`: `cache.enabled: true` together with `cache.mode`.
4. Otherwise off.

`--no-cache` and `AO_CACHE=0` are true kill switches.

**Author policy.**

- `policy(task)` = `task.cache` if set, else `defaults.cache` if set, else **False**.
- A task injected by `emit_tasks` cannot opt itself in: its `true` is ignored.
- A task is considered only when the mode is not `off`, the policy is `true`, **and** it is eligible
  (D10).

**Modes.**

| Mode | Behaviour |
|------|-----------|
| `shadow` | Full lookup and key computation, but no restore. A valid entry records `would_hit` with an avoidable-cost estimate; the task still dispatches and still stores. |
| `refresh` | Skips the lookup and overwrites the entry on success. |

**Why.**

- Freezing a non-deterministic result is an operator decision.
- Whether a task's whole effect is captured by its declared outputs is something only the author
  knows. Reviewer R1 made the author layer fail-closed; the manager proposal had it default to
  allowed.
- `shadow` lets G0 measure value at no extra spend.

### D2 — Location `<workspace>/.orchestrator/cache/`

The directory is self-ignoring: it contains a `.gitignore` of `*`, a `CACHEDIR.TAG` and a versioned
`layout.json`.

**Root checks.** The root is created with mode `0o700`. It must:

- not be a symlink;
- be owned by the effective uid;
- have no group/other write bit;
- resolve inside the workspace.

**Per-operation checks.** Every store operation re-checks the root and `lstat`s each path component.

**Rejected:**

- `.ao/`, because it is committed config.
- An XDG user cache, because it is shared across workspaces, tenants, the bench and the service.
- The run directory, because it is not cross-run.

### D3–D8 — Key schema v1

**Key.** `sha256` of canonical JSON (sorted keys, compact, ASCII, no NaN) over these fields:

- `key_schema`
- `agent`: the 12 behaviour-relevant `AgentSpec` fields
- `argv`: the exact `build_claude_argv(effective_agent, normalized_prompt)`, extracted
  behaviour-identically from `ClaudeCliExecutor.execute`
- `executor_fingerprint`: CLI version; a non-secret env allowlist; digests of `CLAUDE.md`,
  `.claude/{settings*,agents,commands,skills}` and `.mcp.json`
- `prompt`: the real `build_prompt`, rendered from a workspace-relative context
- digests of `instruction`, `general_instructions`, `inputs` and `dynamic_inputs`
- `outputs`: `[{path, prior}]`
- `repo_heads`

The golden vector **GV-1 (Rev 2)** is
`6646469e94a695fe1a994d35f54ca74e007262911255552b03c54ce2e5d0319f`.

| Decision | Content | Why |
|----------|---------|-----|
| D4 | The task id is **excluded**. So are the workflow id, run id, registry name, timestamps, absolute paths, timeout, retries and scheduling fields. | The prompt and argv never contain them (tripwires U-K7 and U-K8a). Outputs are in the key, so sharing requires identical output paths. Template instances therefore do **not** share entries. |
| D5 | `repo_heads` is **included** by default; the opt-out is `include_repo_heads: false`. Git-ness is decided from the filesystem, not by a failure-hiding probe. For a git repo, any read failure makes the task uncacheable. | Repo content is an undeclared input of every task. |
| D6 | The **prior content of each declared output** is included: `"absent"` or a sha256. | Without it, an in-place-update hit would overwrite newer edits. |
| D7 | argv and fingerprint are included; a wrapper command basename or an unresolved model makes the task ineligible. | Executor upgrades (`EFFORT_MAX_TURNS`, new flags) and context-file edits must miss without relying on a manual version bump (critic #4). |
| D8 | Directory digest is a sorted canonical manifest. A symlink or special file inside makes the task uncacheable. | Determinism; link safety. |

### D9 — NFR-1 carve-out

- Only `agent_orchestrator.cache` reads artifact bytes.
- `engine.py` and the `ArtifactStore` ABC stay content-free.
- `engine.py` imports cache modules only under `TYPE_CHECKING` or lazily inside one private method.
- The texts `open(` and `.read(` never appear in `engine.py`.

### D10 / D29 — Fail-closed allowlist eligibility

**Classification.** Every field of `TaskSpec`, `AgentSpec`, `WorkflowSpec` and `WorkflowDefaults`
is classified. Tripwire tests fail on any unclassified field.

**Runtime unknown-field rules.** An unclassified field with a non-default value makes the task
ineligible. This applies to:

- `type(task).model_fields`;
- the workflow;
- the workflow defaults.

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
| `dispatch_cycle` | **Keeps its increment.** R-21 stays monotonic, and `ui/activity` finds no transcript for a hit cycle. Usage helpers exclude current hits explicitly. |
| `attempts` | Unchanged. |
| `cumulative_*` | Untouched. |
| Breakers | Not evaluated. |
| Budget | A **stale** charge left by an earlier crash at the same cycle is reversed (`reverse_estimate`). Otherwise untouched. |

**Rejected (Rev 1):** decrementing `dispatch_cycle`, because it broke R-21 and the activity view;
mutating engine state from the plugin.

### D13 — Store only a final settled success, behind three guards

The store runs on the main thread at the top of the `succeeded` branch of `_settle_completed_task`.
It uses the pending token that the lookup put on `_RunContext`. It stores only if all three guards
pass:

1. The key, recomputed with the pre-seeded priors, equals the lookup key.
2. No repo HEAD has moved. This is checked even when `include_repo_heads` is false.
3. No tracked file outside the declared outputs and `.orchestrator` changed (tracked-only status
   plus mtime and size).

Untracked and non-git edits are a documented residual risk.

### D14–D16 — Persistence and reporting

**Persistence.** `RunState.result_cache: dict[str, ResultCacheRecord]` holds one record per task,
never on `TaskRunState`.

**Currency.** A record is current iff:

- `rec.dispatch_cycle == ts.dispatch_cycle`; and,
- for a hit, the task is `succeeded` **and** `rec.ended_at == ts.ended_at`.

No engine code drops stale records.

**Reporting.**

- Report surfaces are additive and omitted when there are no current records: `status.json`, the
  summary line, the `report-usage` keys, and null dashboard fields.
- `report-outcomes` reports `settle_reason: "cached"`.
- `saved_*` is an estimate copied from the entry. It is never netted into cost and never fed to
  breakers, which keeps E-9h3m7k's spend numbers exact.

### D17–D19 — Provisional split ABCs; versioned layout; bounded maintenance

**Interfaces.**

- `CacheStore` (hot path) and `CacheAdmin` (maintenance) are marked **provisional**; a remote
  backend will need an output-I/O seam (non-MVP).
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
- Inline enforcement streams entries and **defers** above `INLINE_PRUNE_MAX_ENTRIES`.
- `clear` moves the root into a trash directory and verifies the removal.

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

### D27 / D28 / D30–D32 — Operability and robustness

| Decision | Content |
|----------|---------|
| D27 | `ao cache rm <key\|prefix>` and `ao cache rm --run R --task T` give single-entry invalidation (critic #2). |
| D28 | Hostile data is parsed by **total functions**. One parse boundary maps any failure (including `RecursionError` and `UnicodeDecodeError`) to `CacheIntegrityError`. Models are strict and bounded (`AwareDatetime`, finite numbers, `max_length`). Internal reads are FIFO-safe (`O_NONBLOCK` + `S_ISREG`). |
| D30 | `cache.miss` carries per-component digests (`sha256[:12]`) so an operator can see why a key changed. |
| D31 | One `safeio` module, plus an AST guard: no pickle, eval or `shell=True`, and no bare `open(` outside `safeio`. |
| D32 | Error boundary. The engine-facing calls never raise. Expected errors become a miss or skip with an ERROR log. An unexpected error emits `cache.disabled` and disables the cache for the rest of the run. `strict=True` re-raises, in tests only. |

## Alternatives considered

| ID | Alternative | Disposition |
|----|-------------|-------------|
| ALT-1 | Status quo: `skip_if_outputs_exist` plus `ao resume` only | Rejected. Not content-keyed, so not safe across runs. |
| ALT-2 | Adopt a library: `diskcache`, `joblib.Memory` / `cachetools`, DVC run-cache, Bazel REAPI | Rejected. Adds dependencies; joblib pickles (code execution from an agent-writable directory); no restore-to-spec semantics; command-line keys rather than agent contracts. |
| ALT-3 | Single operator gate, with the author layer defaulting to allowed (manager proposal A) | Rejected (reviewer R1). Not fail-closed for tasks with undeclared effects. Replaced by double opt-in (D1). |
| ALT-4 | Spec-only opt-in, or fill-in semantics where the spec enables caching | Rejected. A workflow file must not change semantics for an operator who did not ask for it. |
| ALT-5 | Key on the raw dispatched prompt plus output paths (the brief's literal list) | Rejected. The prompt holds absolute paths and no contents; omitting HEADs, priors and argv gives stale and destructive hits. |
| ALT-6 | A user-level (XDG) or cross-workspace cache | Rejected for MVP. Tenant leakage; breaks bench and service isolation. |
| ALT-7 | **Executor-level `CachingExecutor`**, which wraps the executor inside the worker, works inside isolation worktrees and sees hooks (dev-critic #7) | **Deferred; the recorded migration path.** The brief places the lookup in the engine, and the executor level forces a budget pre-charge on hits. Revisit if isolation becomes the default (roadmap §3.4, R-15). |
| ALT-8 | **Explicit `ao run --reuse-from <run-id>`**, where the operator vouches for a source run (dev-critic) | **Recorded fallback.** No hidden store and no key contract, but not what the brief asks for. Pursue it if G0 shows a negligible would-hit rate. |

## Consequences

### Positive

- Identical re-runs avoid real spend.
- With the mode off, the engine executes and imports no cache code, and `status.json` and CLI text
  are byte-identical (§8.7.5 evidence: I-1, I-2, U-AST-E).
- Unchanged: the run loop, the wave scheduler, isolation, budget, breakers, `should_skip` and
  `prepare_resume`.
- Hostile or corrupt cache data degrades to a miss and never kills a run.
- `shadow` mode makes the value measurable before anyone relies on it.

### Negative / trade-offs

- **Conservative hit rate.**
  - Any commit invalidates all keys, unless the operator opts out.
  - Prior-output keys mean `skip_if_outputs_exist: false` tasks hit only from identical starting
    content.
  - A CLI auto-update changes every key.
  - Double opt-in adds author friction.
- **Residual stale-hit sources.**
  - Untracked or non-git edits outside declared inputs.
  - `~/.claude/**`, the network, and env vars outside the allowlist.
  - Model-alias retargeting, bounded by the 30-day TTL.

  Mitigations: `cache: false`, `refresh`, `rm`.
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

The critic's "no-go until value is validated" is answered with `shadow` mode and the G0 gate, not
by the architect overruling the brief. If G0 is negative, the feature stays shipped but off, and
ALT-8 is the next step.

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
- Fixing the missing-inputs `dispatch_cycle` reset in `engine.py` (separate bug ticket).

## Revision history

| Rev | Date | Change |
|-----|------|--------|
| 1 | 2026-10-04 | Initial decision set. |
| 2 | 2026-10-05 | Phase-4 consultation folded in: double opt-in and modes; argv and fingerprint in the key; three store guards; the engine owns the hit transition and keeps the cycle increment; stale budget charges are reversed; total parsing and the error boundary; sensitive destinations; versioned layout and provisional split ABCs; `rm`; alternatives ALT-7/ALT-8 recorded; G0 escalated. |
