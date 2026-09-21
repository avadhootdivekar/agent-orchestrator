# Security design review — E-Wk9Tz3-task-isolation

- Reviewer: dev-security (agent)
- Date: 2026-09-07
- Scope: design only — `docs-md/task-isolation-hld.md`, `docs-md/adr/ADR-0013-*.md`, and
  `meta/tickets/E-Wk9Tz3-task-isolation/**`. No code exists yet; no files under `src/` were changed by
  this review. Cross-checked against `docs-md/adr/ADR-0005-headless-claude-tool-policy.md`,
  `docs-md/adr/ADR-0011-untrusted-workspace-content-rendering.md`,
  `src/agent_orchestrator/artifacts.py` + `tests/test_artifacts.py`, `src/agent_orchestrator/cli.py`
  (`prune`), and `src/agent_orchestrator/service/paths.py` (state-dir precedent).
- Trust model assumed (per task brief): workflow/reposet/agent spec files and the repos they point at
  arrive via `git clone` and are **semi-trusted at best** — the same tier as any agent-authored or
  agent-consumed content in this system today.

## Summary of what the design already gets right

Before the findings: HLD §14 and ADR-0013's "Consequences" section already reason carefully about
several of the classic hazards (path-guard producer-restriction, ref-namespace confinement, no `git
config` mutation, no network calls from the engine itself, bounded git timeouts, CAS instead of a
working-tree merge). `ao prune`'s worktree/ref GC and `WorktreeManager.reconcile` are correctly scoped
to the `ao/<run_id>/...` ref namespace and the `$AO_STATE_DIR/worktrees/<ws>/<run_id>/` path prefix, so
they cannot delete a user's own branches or worktrees — validated clean, no finding needed. Commit
messages are ids/refs only (NFR-1), validated clean. `rerere` args are passed per-invocation (`-c
rerere.enabled=...`), never written to the user's real git config — validated clean, and a test for
this is already an explicit AC in `T-Gt4Pw8`.

---

## Findings

### Blocking

**S-1 — Engine-issued git commands do not suppress repo-local hooks; `git worktree add`/`commit`/`rebase` become an unattended, per-task hook-execution amplifier.**
- Threat area: git-specific hazards (#3).
- Location: HLD §11 M1 (`GitRepo._run`, `worktree_add`, `commit`, `rebase_onto`, `rebase_continue`);
  `T-Gt4Pw8-git-porcelain` AC1-AC13.
- Risk: `git worktree add` runs a checkout (fires `post-checkout`); the auto-commit step
  (§8.2/FR-5) runs `git commit` (fires `pre-commit`/`commit-msg`); rebase fires
  `post-checkout`/`post-rewrite`. None of these are pre-existing risks from the *tracked* content of a
  cloned repo (`.git/hooks/*` is never tracked/cloned), but many real projects (including the primary
  consumer, per its own guidelines doc) install local hooks via a bootstrap step (husky, pre-commit,
  cargo hooks) that the operator ran once, trusting it to fire on their *own* interactive commits. This
  design turns that same hook into something that fires **automatically, unattended, once per task**
  (dozens to ~100 times for the consumer's real epics), at engine cadence and often concurrently across
  worktrees — with the same ambient privileges as the `ao` process. A hook that phones home, mutates
  the staged tree, or shells out is now a per-task amplifier rather than a one-time, human-observed
  event. This is squarely a "new place a cloned/bootstrapped repo can run something the operator didn't
  explicitly accept happening 100 times unattended," which is exactly the class of risk the task brief
  flags as high priority.
- Mitigation (design-level, cheap): every git invocation the **engine itself** issues through
  `GitRepo._run` should pass `-c core.hooksPath=<empty/nonexistent dir under $AO_STATE_DIR>` (a
  directory, not `/dev/null`, for Windows portability) so no hook script is ever found, in addition to
  the existing `RERERE_ARGS`. Add this as a named constant (`NO_HOOKS_ARGS`) alongside
  `RERERE_ARGS` in `T-Gt4Pw8`, and add the AC-6-style test this ticket already uses for rerere ("assert
  `rerere.enabled` never lands in git config") to also assert **a planted `post-checkout` hook in the
  test fixture repo never fires** across `worktree_add`, `commit`, and `rebase_onto`/`rebase_continue`.
  This does not touch any hook the **task's own agent** chooses to invoke via its own `git` commands
  (out of scope, same trust tier as today) — only the engine's own plumbing.
- Owning ticket: `T-Gt4Pw8-git-porcelain` (the single choke point); flow the constant through to every
  other module that calls `GitRepo` (no other ticket needs its own fix).

**S-2 — The T2 LLM merge-resolver is a new automatic dispatch point that hands attacker-influenceable conflict content to an agent with default allow-all tool access, with no enforced containment beyond prompt text.**
- Threat area: LLM merge-resolver prompt injection (#4).
- Location: HLD §8.4 T2, §11 M7 (`isolation/escalation.py`, `conflict-<n>.json`, `merge-resolve.md`);
  `T-Lr6Ka3-llm-resolver-and-rerun`; compare `docs-md/adr/ADR-0005-headless-claude-tool-policy.md`.
- Risk: `conflict-<n>.json` itself is NFR-1-safe (paths/refs only, no orchestrator read of contents) —
  but the resolver **agent** is instructed to open the conflicted *files* directly with its own
  Read/Bash tools, because that's the only way it can resolve them. Those files' conflict hunks contain
  real content authored by up to two different tasks — one of which may itself be running against
  semi-trusted spec/repo content. A crafted line inside a conflicting hunk (a code comment, a string
  literal, a lockfile entry) reaches an LLM agent that — per ADR-0005's own accepted default — has
  **allow-all tools**: `Bash`, `WebFetch`, `WebSearch`, `Task` subagent spawn are all enabled unless the
  operator opts a specific `AgentSpec.disallowed_tools` out. `merge-resolve.md`'s "do NOT push, do NOT
  switch branches, do NOT run `git rebase --continue`" is **prompt text**, not an enforced boundary —
  it does not stop a hijacked agent from running `git push` (the worktree shares the main repo's
  remotes and any cached credential helper / SSH agent, per git-worktree semantics) or exfiltrating via
  `WebFetch`. This is materially different from "a workflow spec can already run arbitrary agents" (HLD
  §14's framing): today's agents are dispatched against **declared inputs the workflow author chose**;
  this is a **new, engine-triggered** dispatch fed by **unresolved raw conflict content neither task
  author reviewed together**, which is a strictly larger and less-audited input surface than any
  existing dispatch path.
- Mitigation (design-level): (a) require — not just recommend — that whatever `AgentSpec` is named in
  `integration.resolver_agent` has network-egress tools (`WebFetch`, `WebSearch`) disabled; either
  enforce this at `cross_validate` (V2/V3 already gate `resolver_agent`'s existence — add a check that
  it carries `WebFetch`/`WebSearch` in `disallowed_tools`, fatal if not, mirroring how V1/V2 are already
  fatal) or have the T2 dispatch path **force-inject** `--disallowedTools WebFetch WebSearch` regardless
  of the named agent's own config (same pattern ADR-0005 §5 already uses for the background-shell set).
  (b) Recommend removing/blackholing the `origin` remote (or setting
  `-c http.https::proxy=127.0.0.1:1 -c credential.helper=` for the duration) in the **resolver's**
  worktree specifically, so even a hijacked `git push` has nowhere real to go — worktrees have no
  legitimate reason to push. (c) State this explicitly in HLD §14's security table (it currently has no
  row for the resolver agent's tool policy at all) and cross-reference ADR-0005.
- Owning ticket: `T-Lr6Ka3-llm-resolver-and-rerun` (schema/validation + dispatch-path changes);
  `T-Tp7Zs2-instructions-and-templates` (document the required `disallowed_tools` set on
  `merge-resolver`-role agents in the shipped template).

### Major

**S-3 — Engine-forced, unconditional `git add -A` + auto-commit widens the secret-sweep surface versus today's agent-initiated commits, and the "ao-owned branch, never pushed" mitigation undersells that the same commit lands on the fast-forwarded main checkout.**
- Threat area: secrets handling (#6).
- Location: HLD §8.2 (squash mechanics), §14 ("Auto-commit sweeping secrets" row); D4 consequence
  ("publishing the integration branch stays an explicit agent task").
- Risk: Pre-epic, `git add`/`commit` only happens when an agent's own instructions choose to run it —
  a human-reviewed workflow decision. This design makes the **engine itself** run `git add -A &&
  commit` **unconditionally at the end of every isolated task**, with no per-task opt-out and no
  content inspection. §14's mitigation ("respects `.gitignore`... commit lands only on an ao-owned
  branch, never pushed by the engine") is real but incomplete: the ao-owned integration branch is
  exactly the branch that D4/D5 fast-forward into the user's **real, main checkout** at barrier points
  and at run end, and D4's own "Consequence" section says publishing that branch is an explicit,
  expected **agent** task later in the workflow (`90-final-push.md`-style). So an untracked,
  non-gitignored secret file dropped into a worktree during a task (a downloaded credential, a
  debug-dump, a `.env` the repo's own `.gitignore` happens not to cover) is not merely committed to a
  throwaway branch — it flows into the integration branch, then into the main checkout, then is a
  candidate for an entirely ordinary, sanctioned `git push` by a later task in the same run. This is a
  strictly larger blast radius than "an agent chose to commit it," because there is no longer any
  decision point where the engine's own action could have been skipped.
- Mitigation (design-level): add a documented, named guard in the auto-commit step (owned by
  `T-Ib5Qy9-integrator-core`) that refuses to stage (or at minimum warns loudly and requires
  `integration.allow_unignored_secrets: true` to proceed) paths matching a small, named
  denylist (`.env`, `.env.*`, `*.pem`, `id_rsa*`, `*credentials*.json`) that are untracked at auto-commit
  time — cheap, no false-negative claims, purely a backstop for the common footgun. Document in
  `conflict-friendly-coding.md` (M10) that the engine auto-commits everything staged, so tasks must not
  write secret material into the worktree at all, full stop — not merely rely on `.gitignore`.
- Owning ticket: `T-Ib5Qy9-integrator-core` (auto-commit step); `T-Tp7Zs2-instructions-and-templates`
  (instruction wording).

**S-4 — `extra_roots`/`IsolatedArtifactView` is stated as "producer-restricted" but not explicitly stated or tested as *per-task* scoped; a naive implementation could let task A's declared path resolve into task B's (a sibling, concurrently-running, not-yet-integrated) worktree.**
- Threat area: path guard widening (#2).
- Location: HLD §7.3 ("Widening the artifact path guard"), §7.4 (artifact visibility rules); R4 in
  HLD §21; `T-Ee3Mn8` AC11(a).
- Risk: §7.3's four numbered rules all restrict **who can produce** an `extra_roots` entry
  (`WorktreeManager`, never a spec/manifest/agent) — that part is sound and I found no gap in it. What
  is *not* explicitly stated is that a given task's `IsolatedArtifactView` must be built from **only
  that task's own** `task_iso.repos[*].worktree_root` values, never the full set of worktrees the
  `WorktreeManager` has ever created for the run. The pseudocode in §7.2/§11 M5 is consistent with
  per-task scoping (`effective_path(resolved_abs, task_iso)` takes the dispatching task's own
  `TaskIsolation`), but nothing in §7.3, §7.4, or the stated AC (`T-Ee3Mn8` #11(a): "confirm `extra_
  roots` can only be produced by `WorktreeManager`") tests the narrower, more important property: that
  task A cannot declare an **absolute** output/input path that resolves into task B's worktree root. If
  `IsolatedArtifactView` is implemented by handing it "the `WorktreeManager`'s full root registry" rather
  than "this task's own `task_iso`," isolation is broken in a security-relevant way: a compromised or
  merely buggy task's agent could read a sibling's uncommitted work, or — worse — write into it,
  laundering hostile content through a task that never asked for it and whose own review/verify step
  would run against tampered input it didn't produce.
- Mitigation (design-level): make the scoping explicit as a numbered rule in §7.3 (a "rule 5"), and add
  a dedicated AC/test distinct from #11(a): *"an absolute path under task B's worktree, submitted as an
  input/output/`cwd` for task A, is rejected by task A's `IsolatedArtifactView`."* This belongs in
  `T-Wk3Nv6` (where `IsolatedArtifactView`/`ensure` are built) with a verifying test in `T-Ee3Mn8`.
- Owning ticket: `T-Wk3Nv6-worktree-lifecycle` (design/implementation); `T-Ee3Mn8-e2e-and-review`
  (the missing AC).

**S-5 — `rerere` replay is landed at the same "free, zero-review" tier as a clean auto-merge, but the *documented default* verify step cannot catch a wrong-but-syntactically-valid replayed resolution.**
- Threat area: git-specific hazards (#3), specifically rerere cache poisoning.
- Location: HLD §8.3 (default verify), §8.4 T1, OQ-4, R2 in HLD §21.
- Risk: The design already acknowledges (OQ-4, R2) that a rerere resolution, once recorded, is replayed
  automatically "forever after" across runs, and that "a bad-but-verified resolution can persist." But
  the *documented, out-of-the-box default* verify (§8.3) is a **structural** check only — leftover
  conflict markers and `git diff --check` — which cannot detect a resolution that is syntactically
  clean but semantically wrong (e.g., silently keeping "ours" when "theirs" was the fix). Because T1 is
  explicitly the free/no-agent tier, a poisoned or merely-wrong `rr-cache` entry can compound silently,
  landing on the integration branch — and then the main checkout — run after run, with no distinguishing
  signal beyond a log line (`resolver=rerere`) nobody is watching. This is worth flagging as a
  security-adjacent (integrity) gap even though it is not a classic injection: it is a mechanism, shared
  and mutated across runs, that self-reinforces an unreviewed decision under the same default posture
  the design recommends for fast local iteration (§16).
- Mitigation (design-level): (a) surface a per-run and cumulative *rerere-tier resolution count* in
  `status.json`/the dashboard column (M9 already adds a dashboard column for `integration_status` —
  extend it to show `tier_reached`, which already exists per §10.3, so this is display-only, not a new
  field); (b) strengthen the docs (M10) to state plainly: *"if `verify_command` is unset, a rerere
  replay is functionally unreviewed — set a real `verify_command` on any repo where isolation is more
  than a convenience."* This is a documentation/observability fix, not a new mechanism.
- Owning ticket: `T-Cx4Jf1-cli-config-prune-observability` (dashboard/status.json surfacing);
  `T-Tp7Zs2-instructions-and-templates` (docs wording).

### Minor

**S-6 — `resolvers.regenerate[].command` has no schema-defined timeout; the M6 pseudocode references an undefined `cfg_timeout`.**
- Threat area: input validation / DoS (#5, #6 boundary).
- Location: HLD §10.1 (`resolvers.regenerate` schema: only `glob`, `command`, `take` — no timeout
  field) vs. §11 M6 (`apply_plan`: `run(rule.command, cwd=wt, env=run_env, timeout=cfg_timeout)`).
- Risk: A regenerate command (e.g., a lockfile regen that prompts for input, or hits the network and
  hangs) has no first-class bound of its own; it is only backstopped by `integration.lock_timeout_
  seconds` (default 1800s), since it runs while the per-repo lock is held. That is a correct backstop
  but a coarse one — a hung regenerate command stalls every other task touching that repository for up
  to 30 minutes before the lock times out, rather than failing fast.
- Mitigation: add `regenerate[].timeout_seconds` (default something small, e.g. 120s) to
  `RegenerateRule`/the JSON Schema, and pass it explicitly rather than an undefined `cfg_timeout`.
- Owning ticket: `T-Sc7Rm2-isolation-schema-models` (schema/model field);
  `T-Rm2Lx7-mechanical-resolvers` (wire it through `apply_plan`).

**S-7 — No cumulative cap on disk consumed by retained failed worktrees within a single run.**
- Threat area: resource/DoS (#5).
- Location: HLD §10.1 `keep_worktrees` (default `on_failure`); §16 NFR-6 disk discussion; R5 in §21.
- Risk: A systemic misconfiguration (e.g., a broken `verify_command` that fails for every task) could
  drive many-to-all tasks in a large run (the consumer runs ~101-task epics) to `failed`, and
  `keep_worktrees: on_failure` retains **every one** of them by design (so the operator can inspect
  them) — there is no run-level cap on the number of simultaneously-retained failed worktrees or the
  cumulative disk they consume before `ao prune --worktrees-only` is run by hand. R5 already names the
  general leak class and its cleanup tooling; this finding is narrower: cleanup tooling exists, but
  nothing bounds the peak within one still-running, still-failing run.
- Mitigation: document the interaction with existing failure-count/cost breakers (a run that fails
  systemically should already trip an existing breaker and HALT — confirm this is true for a
  verify-failure storm specifically, not just task-exception storms), and consider a soft warning log
  (`worktree.retention_high`) once N failed-and-retained worktrees accumulate in one run, pointing at
  `ao prune --worktrees-only`.
- Owning ticket: `T-Cx4Jf1-cli-config-prune-observability` (the warning/event);
  `T-En8Hd4-engine-isolation-wiring` (confirm breaker interaction).

**S-8 — No pre-flight check that a pre-existing, user-created branch already occupies the reserved `ao/<run_id>/...` namespace.**
- Threat area: git-specific hazards (#3), ref/branch confinement.
- Location: HLD §11 M3 (`ensure`: "branch exists and points somewhere unrelated is a hard error"); D1.
- Risk: Low likelihood (a `run_id` embeds a UTC timestamp plus the workflow id) but not structurally
  impossible if an operator or another tool has independently created branches under an `ao/` prefix
  (the design itself popularizes that prefix, so copying the convention is plausible). `ensure` already
  treats an unexpected existing branch as a hard error rather than silently reusing it — which is the
  right failure mode — but this is worth a one-line explicit callout in the docs (`ao/` is reserved for
  engine use; do not create branches under it) rather than only an implicit consequence of `ensure`'s
  error path.
- Mitigation: add the reservation notice to `T-Dr5Yq6-docs-refresh`'s output and to the `.ao/config.yaml`
  template comment block (M9).
- Owning ticket: `T-Dr5Yq6-docs-refresh`.

### Info

**S-9 — Worktree directory permissions under `$AO_STATE_DIR` are unspecified.**
Given S-3 (auto-commit secret-sweep risk), recommend worktree parent directories be created `0700`
(user-only) on POSIX, consistent with typical `~/.local/state` conventions, in case `$AO_STATE_DIR` is
ever pointed at a shared-host location. No dedicated ticket needed — fold into `T-Wk3Nv6`'s directory-
creation code as a one-line convention, no design change required.

**S-10 — `verify_command`/`regenerate[].command` captured stdout/stderr could echo secrets into run artifacts.**
Already size-capped and already the same pattern used for agent transcripts elsewhere in this system
(not a new gap introduced by this epic) — documented as a known limitation, no new mitigation required
beyond what HLD §8.3 already states (captured, size-capped, only exit code enters engine memory).

**S-11 — `git worktree prune` scope.** Confirmed clean: plain `git worktree prune` only removes stale
*administrative* registrations for worktrees whose working directory is already gone — it never deletes
a live directory — so its use inside `ensure`/`reconcile` cannot touch a user's own still-present
worktree. No finding.

---

## Pre-submit checklist

- [x] Confirmed engagement criteria met — explicit security-review request on a large-surface design
  (new git worktree isolation, artifact path-guard widening, new automatic LLM-agent dispatch point).
- [x] Untrusted spec/payload execution surface checked — §10.4 cross-validation (V1-V9) is schema-level,
  no `eval`/`exec`; `verify_command`/`regenerate[].command` are argv lists only (`verify_shell` not
  offered); confirmed these fields live only in the **workflow-root** spec (`WorkflowSpec.integration`),
  not in the per-task schema an injected/emitted task manifest can populate (`read_task_manifest` only
  constructs `TaskSpec`, whose new fields are `isolation`/`touches`) — so an agent-authored task
  manifest cannot inject a command field, only a mode enum and advisory globs. Findings: S-6 (timeout
  gap), S-2 (the resolver *agent*, not the command fields, is the real new execution-adjacent surface).
- [x] Sandboxing/isolation checked — resource bounds (git timeouts, `verify_timeout_seconds`,
  `lock_timeout_seconds`) present; cancellability preserved via ADR-0007 D7 drain semantics; workspace
  confinement is the subject of S-4. No unbounded fan-out/recursion — worktree count is bounded by
  `max_parallel`/task count, not recursive.
- [x] Artifact/path safety checked — traversal/symlink-escape defenses preserved (`resolve()` before
  containment, unchanged); findings: S-4 (per-task scoping not explicit/tested).
- [x] Secrets handling checked — findings: S-3 (engine-forced auto-commit), S-9/S-10 (info-level).
- [ ] Trigger authn/authz checked — **N/A**: this epic adds no new cron/webhook/API trigger or control
  endpoint; it only extends `ao run`/`resume`/`prune`/`hotspots` CLI surface, which carries the same
  trust model as today (operator-invoked CLI). ADR-0010 D7's no-auth dashboard posture is unrelated and
  untouched by this design.
- [x] Input validation checked — safe JSON loaders already in place (`artifacts.py`'s `json.loads`,
  bounded by `MAX_CONTROL_FILE_BYTES` for control files); no new YAML/deserialization surface; no new
  SSRF surface (the only new network-adjacent risk is the resolver agent's own tool access, S-2); no
  archive/zip-slip surface introduced by this design. Finding: S-6 (missing timeout field).
- [x] Rate limiting / abuse / DoS bounds checked — findings: S-6, S-7 (both Minor; existing budget/cost
  breakers already bound T2/T3 spend per D9 with "zero new plumbing", validated clean).
- [x] Supply chain checked — **N/A for this design doc**: no new third-party dependency, base image, or
  CI action is introduced by the HLD/ADR; this is a pure-stdlib/git-subprocess design. Revisit at
  implementation time if any resolver/regenerate tooling pulls in a new package.
- [x] CI/CD merge-gate posture checked — `T-Ee3Mn8` AC15 already requires coverage >=80%, green
  `ruff`/`mypy` vs. baseline, consistent with `CLAUDE.md`'s existing CI conventions; no new CI gap is
  introduced by this design (no new workflow files are in scope here).
- [x] Every finding above has a location + a plausible attack/misconfig path — no purely theoretical
  claims; S-8 is explicitly labeled low-likelihood and Minor rather than inflated.

---

## Verdict

**Conditional pass — proceed to implementation planning, but S-1 and S-2 must be designed in (not
merely reviewed for later) before `T-Ib5Qy9-integrator-core` and `T-Lr6Ka3-llm-resolver-and-rerun`
merge, respectively; S-3 and S-4 must be resolved before `T-Ee3Mn8-e2e-and-review`'s security pass is
considered complete.**

The overall architecture (worktree-per-task, squash+rebase, CAS landing, producer-restricted path-guard
widening, reserved ref namespace, cost-bounded ladder) is sound and the authors already reasoned
carefully about the obvious hazards (§14, ADR-0013 Consequences). The gaps found here are concrete,
narrow, and each maps to a one- or two-line design addition plus a test — none require re-architecting
the epic.

**Must-address before implementation proceeds past the owning tickets:**
- **S-1** (git hooks) — `T-Gt4Pw8-git-porcelain`, blocking.
- **S-2** (LLM resolver tool policy) — `T-Lr6Ka3-llm-resolver-and-rerun` + `T-Tp7Zs2-instructions-and-templates`, blocking.
- **S-3** (auto-commit secret sweep) — `T-Ib5Qy9-integrator-core` + `T-Tp7Zs2-instructions-and-templates`, major.
- **S-4** (per-task path-guard scoping + missing AC) — `T-Wk3Nv6-worktree-lifecycle` + `T-Ee3Mn8-e2e-and-review`, major.

S-5 through S-8 should be tracked but do not need to block the start of implementation; S-9/S-10/S-11
are informational.

---
- By: dev-security · Role: reviewer · Date: 2026-09-07 · Comment: Design-only review per the epic's
  E-Wk9Tz3 security-pass requirement (`T-Ee3Mn8` AC11) and this review's own explicit task scope. No
  HLD/ADR/ticket files or `src/` were edited; no commits made.
