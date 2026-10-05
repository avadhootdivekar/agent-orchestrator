# ADR-0020 — Human approval gates: an engine-native gate with HMAC-signed, content-bound decisions

- Status: **Proposed — revision 3, frozen for implementation after Gate 2** (2026-10-05). Epic
  `E-Ag7Pw3-human-approval-gates`. Rev 2 answered the early design Gate 1 (dev-security FAIL, reviewer
  PASS-WITH-CHANGES): it changed D3, D4, D5, D6, D7 and D11 and added D12 and D13. Rev 3 answers the Gate 2
  delta re-gate (both PASS-WITH-CHANGES): it adds D14 (the out-of-workspace gated marker), simplifies the
  re-hash bound in D3, extends D12, and corrects the erasure row of the threat summary again (HLD §0.5 maps
  every finding of both gates). Not implemented; implementation deviations are recorded by an addendum.
- Deciders: the parent session (user), `manager`, `architect`.
- Related: ADR-0007 (wave/barrier scheduler — the gate never occupies a slot), ADR-0010 D7 / ADR-0011
  (dashboard trust boundary and untrusted rendering), ADR-0013 (isolation — gates are forced to
  `isolation: none`), ADR-0017 (spawn provenance, workflow snapshots), ADR-0019 (result cache, sibling
  epic — see Consequences), ADR-0021 (dashboard auth, sibling epic — supplies the principal).
- Design: [`docs-md/human-approval-gates-hld.md`](../human-approval-gates-hld.md) (threat model §7, LLD §9).

## Context

Users want a DAG node that pauses a run until a human approves or rejects — "review the plan before the
expensive fan-out", "confirm before the destructive merge". The engine already waits in-process for quota
resets, so an in-process wait is consistent. The hard part is the **decision channel**:

1. **Agents run as the same OS user as the human**, with shell access, write access to the whole workspace
   (including `.orchestrator/runs/<id>/state.json` and anything we put next to it), and network access to
   the loopback dashboard. A prompt-injected agent can write any file the engine reads, run `ao approve`,
   or `curl` the dashboard.
2. The dashboard's security middleware deliberately allows requests with **no `Origin` header** (non-browser
   clients), and dashboard authentication is being built in parallel (ADR-0021).
3. `state.json` and the spec are agent-writable and can be edited between sessions; `ao resume` and
   boot-resume read them. Gate 1 showed that *any* integrity decision derived from these files (for example
   "is this a resume?" from the unsigned `spec_sessions` list) is a bypass.
4. An approval is only meaningful if it is bound to **what the human saw**.

## Decisions

### D1 — An approval gate is a `TaskSpec` with an `approval:` block

A task with `approval: {message, review, approvers, require_2fa, require_dashboard, timeout_seconds,
on_timeout: reject}` dispatches no agent (`agent` and `instruction` absent or `""` — string sentinels,
so neither field changes type; dispatch-only fields forbidden; `"approval": null` is an ordinary task).
When its dependencies settle the engine opens a request and the task becomes `awaiting_approval`; it never
holds an executor or `max_parallel` slot (D12); independent tasks keep running. Approve → `succeeded`;
reject or timeout → `failed` with the existing halt-and-resume semantics, never retried, never
self-healed. `review` defaults to the gate's `inputs` (materialised at validation so loop clones keep it)
and must be a subset of them; a gate in a loop body must reach every in-body producer of a review file
through explicit `depends_on`, because loop clones lose the inferred input/output edges (validation rule
AG-4). A gate-free task serializes exactly as before (a wrap serializer omits an absent `approval`), so
spec hashes and snapshots of gate-free workflows do not change.
*Alternatives:* a new node type (more schema and UI surface for no gain); a "sensor" agent task polling a
flag (costs tokens and a slot; forgeable); park-and-exit (needs a resume trigger owned by `ao service`;
deferred).

### D2 — Request state lives on `RunState.approvals`, not `TaskRunState`

`prepare_resume` replaces `TaskRunState` wholesale; the request (and its `request_id`/expiry) must survive.
Same reasoning as `task_integration` and `spawned_by`.

### D3 — Decisions are HMAC-SHA256 records bound to content, verified by the engine

Signers (CLI, dashboard) write `ao.approval.decision/v1` records signed with a per-user 256-bit key stored
**outside the workspace**: `$AO_APPROVAL_KEY_DIR` > `$XDG_CONFIG_HOME/ao/approvals` >
`<pw_dir>/.config/ao/approvals`, where both overrides count only from the real process environment and
`pw_dir` comes from `pwd.getpwuid(os.geteuid())` — never `$HOME` (rev 2, Gate 1 S-08). Dir 0700, file 0600,
owner and symlink checks, refused inside the workspace (a workspace at `$HOME` therefore refuses gated
runs), created only by the engine via an O_EXCL temp file + `link()` and nothing else (a filesystem
without hard links fails with a clear error). Because the workspace's `.ao/config.yaml` `env:` block is
agent-writable and is exported into every `ao` process, it may not set `AO_APPROVAL_KEY_DIR`,
`XDG_CONFIG_HOME`, `AO_IN_AGENT`, `HOME`, `USER` or `LOGNAME` (otherwise an agent could point the engine
and the CLI at a key it planted). The secret never reaches a log, an exception, a `repr` or a traceback
(the root Typer app shows no frame locals). Records bind `{run_id, task_id, request_id, decision, review
{path: sha256}, identity, decided_at}`. Requests and the run's policy are signed by the engine with the
same key. The engine accepts a record only after checks V0–V12 (format, bounds, one bounded strict parser,
`key_id`, MAC, run, task, request, already-decided, `decided_at < expires_at`, authorization, review set,
and a **re-hash of the review files at consume time**; rev 3 bounds it to at most one re-hash per poll
across all gates, round-robin).
Refused records are audited and moved aside so each is evaluated once; the first valid record wins; a
signer refuses to publish when a valid record already exists.
*Alternatives:* plain flag files (forgeable by any agent); Ed25519 signatures (new dependency, and the
private key would be readable by the same uid — no gain); a separate approval broker under another OS user
(the right long-term answer, too much installation surface for an MVP — roadmap R-AG-1).

### D4 — The dashboard never signs for an anonymous principal, and never serves the approvals tree

The decision route requires `request.state.principal` (set by the ADR-0021 middleware; read through one
defensive reader). With auth disabled or no session the answer is 403 `anonymous`, **even when the gate
lists no approvers**. Binding requirement: empty `approvers` means "any *authenticated* identity or local
CLI". Accepting anonymous would turn the dashboard into a signing oracle for any local process (curl
without `Origin` passes the middleware). Consequence: until dashboard auth is enabled, decisions go
through the CLI. Rev 2 (Gate 1 S-06): the dashboard file browser refuses every path inside a run's
`approvals/` directory, so it is not a confused deputy for the raw records and the audit trail; the scoped
review endpoint is the only way review content is served.
*Alternative rejected:* accept anonymous for empty `approvers` with a warning (the manager's initial
recommendation).

### D5 — Speed bumps against naive agents: `AO_IN_AGENT` and TTY confirmation

The engine exports `AO_IN_AGENT=1` into every agent-influenced child — 8 edit sites covering 10 spawn
points (agent CLI, hooks, verify and the regenerate resolver, every `git` call including
`GitRepo.version`/`probe`, the validate-time probe, benchmark subjects and graders) — always as the last
assignment to the child environment, so no overlay can clear it; never into its own environment.
`ao approve`/`ao reject` refuse under the marker (exit 3, audited), require an interactive TTY and a typed
confirmation word unless `--yes` is given, and refuse `--yes` under the marker. CLI identity comes from the
real uid (`pwd`), never `$USER`. These are **speed bumps, not boundaries**: any process, a naive agent
included, defeats them by unsetting the variable and allocating a pty.

### D6 — Dedicated gate settle; the breaker block is extracted, not duplicated

A decided gate settles through `_settle_approval_gate`, which shares `_evaluate_boundary_breakers` (a pure
move of `_settle_completed_task`'s breaker + Consult Point A block, landed as its own first commit so the
sibling epic's merge can replay it). Feeding a synthetic `WorkerOutcome` through `_settle_completed_task`
was rejected: it needs at least four gate guards (self-heal must never retry a rejection; no budget
reconcile; no quota-timer reset; no git heads) inside an 840-line method that the result-cache epic is
likely to edit.

### D7 — Resume integrity: engine knowledge, a gate-scoped signed policy, fail closed

(Rewritten in rev 2 after Gate 1 S-01, S-02, S-03, R-03, R-04.) Nothing read from `state.json`, the run
directory or the spec may skip an integrity check; in-file data may only add checks.

- **Resume detection** comes from the engine's own knowledge: `run()` was handed a `RunState`
  (`is_resume = run_state is not None`). Any in-file gate evidence also triggers the checks. The
  create-policy path runs only for a fresh run and never overwrites an existing policy.
- **Gate-scoped policy** (`ao.approval.policy/v1`, signed): per gate, its security digest (approvers,
  `require_*`, review list, timeout, `on_timeout`, and the message hash) and its **static forward closure**
  (every static task downstream of it). A resumed session is refused (run marked failed) if a gate is
  removed or its digest changes, or if a recorded closure member is removed, renamed or no longer reachable
  from the gate. Everything else may change; additions (new gates, new downstream tasks) are allowed and
  the policy is re-signed with them. This replaces rev 1's whole-DAG freeze — **OQ-2 (modified):
  gate-scoped policy instead of whole-DAG freeze**, a parent-session decision point.
- **Missing policy fails closed** when the run is evidenced to be gated: first by the out-of-workspace
  gated marker (D14, rev 3), otherwise by in-state facts (approval entries, a non-pending gate, an awaiting
  task, a present-but-invalid policy — rev 3 dropped the bare `approvals/` directory, Gate 2 S-12); without
  evidence, a gate added to a gate-free run gets its policy (and marker) at that session ("adding a gate is
  allowed, removing one is not").
- **Gate statuses are re-derived**: a `succeeded` gate needs a valid signed request bound to this run and
  task plus an accepted approve record; `skipped` is never legitimate for a gate; `not_taken` survives only
  if the engine itself derives it from `route_decisions` and the graph — for the gate **and every gate
  ancestor**, since a forged `not_taken` ancestor propagates to the gate. Anything else is reset to
  `pending` and re-asked.
- **Loop clones** of gated loops are re-derived with the engine's own `_clone_body`; any difference in
  gate-ness, gate fields or `depends_on` fails closed, and a clone gate's spec always comes from its static
  base gate.
- **Re-entry**: a pending request is re-entered with its original `request_id` and absolute `expires_at`
  after verifying its signature, run/task binding and `spec_digest`; a decision signed before expiry
  while the engine was down is honoured (and applied before anything else is dispatched). Rejected/expired
  gates get a new request linked by `supersedes`.

*Alternatives rejected:* a per-request spec digest only (cannot protect gates that were not yet opened,
nor a downstream task whose `depends_on: [gate]` was edited away); a whole-DAG freeze (rev 1 — blocks the
ordinary "edit the spec, `ao resume`" loop); detecting resumes from `spec_sessions` (rev 1 — unsigned and
agent-writable); signing routing verdicts (a router's verdict is agent-authored by design — documented
residual instead).

### D8 — Agent-authored `emit_tasks` manifests cannot declare `approval`

`read_task_manifest` rejects the key (emitter fails `manifest_error`), consistent with the AC-15
containment of `touches` and hooks. Injected tasks can still omit a dependency on a gate; the authoring
rule is "gate before the emitter", with an `ao validate` warning for gates downstream of emitters.

### D9 — Append-only audit log with a per-line MAC

`<run_dir>/approvals/audit.jsonl` (`O_APPEND`, 0600, ≤ 4 KiB per line) records every request, decision,
expiry, refusal and policy event from the engine, the CLI and the dashboard; the engine mirrors its events
to `run.log`. Per-line HMAC makes edits and forgeries (without the key) detectable; deletion, truncation
and reordering are not (no hash chain across three independent writers). Refusal audits are capped per
request; there are no extra flood or suppression events.

### D10 — UI: status tone, an `approval` tab, two-step confirm, `X-Frame-Options: DENY`

`awaiting_approval` gets an "attention" tone and its own glyph (no new node shape; no change to the
contract-pinned `RunDetail`/`TaskStat`/`GraphNode` shapes). The review view is a new tab kind (validated
params, deep-linkable). The review view binds the bytes it displays to the hashes it echoes. Messages and
artifacts render only through the ADR-0011 viewers (HTML/SVG as source). `X-Frame-Options: DENY` is added
to every response; CSP `frame-ancestors` waits for the same in-browser verification the existing CSP had.
The run view shows an "Approval wait" figure next to wall time, computed with the same end marker so it
never exceeds it.

### D11 — Poll interval through the constructor and an environment variable only; injectable driver

`Orchestrator(approval_poll_seconds=…)` > `AO_APPROVAL_POLL_SECONDS` > 5 s; `.ao/config.yaml` reaches it
via its existing `env:` block. No new `ao run`/`ao resume` flag and no new config key (both are the most
conflict-prone surfaces in the three-epic merge). The approval driver is built by an injectable
`approval_driver_factory` (tests, park-and-exit, a future broker); a gate without a driver fails the run
closed instead of relying on an `assert`.

### D12 — Ready gates open in a pre-pass before FILL (rev 2, Gate 1 R-01)

Every ready gate is opened (or re-entered, then polled at once) in a pre-pass before the FILL loop,
whatever the free `max_parallel` capacity; gates never enter `rank_wave` or the capacity count. Only while
integration is active and isolated work is in flight does a gate wait (it needs a synced checkout), and
FILL then launches nothing new until in-flight work drains (the ADR-0013 barrier rule). Rev 3 (Gate 2
R-14): both scheduler detection sites (`_open_ready_gates`, `_prepare_and_maybe_dispatch`) recognize a gate
by its persisted `approval` **or** the static base gate's spec (`effective_approval`), the same rule
`open_gate` uses.
*Alternative rejected:* opening gates from the FILL loop (rev 1) — with `max_parallel = 1` an earlier
independent task held the only slot, so nobody was asked and a decision recorded while the engine was down
was not consumed, contradicting "the gate holds no slot".

### D13 — One closed refusal vocabulary and one read model (rev 2, Gate 1 R-05, R-08)

Every refusal carries a `RefusalReason` (closed enum) and an optional `RefusalDetail`; HTTP statuses and
CLI exit codes come from one mapping table, never from message text. The CLI and the dashboard share one
read model (`approvals/views.py`: pending listing, task view, `can_decide` through the signer's own
checks) instead of building listings and views twice.

### D14 — An out-of-workspace gated marker ("F-15-lite", rev 3, Gate 2 S-11, S-12, R-11)

Gate 2 showed that rev 2's in-workspace evidence (the policy in `state.json`, approval entries, gate
statuses, the run's `approvals/` directory) is made of self-named artifacts: an agent merely *instructed*
to "remove the approval gate" edits the spec, nulls the policy and deletes `approvals/`, and the run
resumed gate-free — no key and no knowledge of this design needed. Decision:

- The engine — and only the engine — writes a MAC'd marker `<key_dir>/gated/<run_id>.json` (MAC kind
  `gated`; same custody rules as the key: 0700/0600, owner, no symlinks) holding the digest of the run's
  signed policy. It is written **after** the signed policy has been saved in `state.json`, and rewritten
  whenever the engine legitimately re-signs the policy (additions only).
- On every resumed session the driver probes the marker (even when the current spec has no gate). A
  present, valid marker makes the run authoritatively gated: the policy in `state.json` must exist, verify,
  and match the marker's digest — or be its immediate signed successor (`previous_sha256`), which covers a
  crash between saving a re-signed policy and updating the marker. Otherwise the resume fails closed
  (`missing_with_marker`, `marker_mismatch`, `marker_invalid`). A valid policy without a marker
  self-heals (the marker is written).
- Crash-safe order: sign the policy → save `state.json` → write the marker → create `<run_dir>/approvals/`
  lazily at the first open. A bare `approvals/` directory is no longer evidence (S-12), so a planted one
  cannot brick a gate-free run.
- Limits, stated: stale markers accumulate (cleanup is F-16); a resume in another account or machine has
  no marker and falls back to in-state evidence; two runs with the same run id collide (the second refuses
  to start). Deleting or forging the marker means touching the key directory — the RR-1 class.

*Alternatives rejected:* in-workspace evidence only (rev 2; erasable by an instructed agent); the full
policy outside the workspace (F-15: a per-workspace store and cleanup rules — larger than this epic needs
once the digest is outside); treating a bare `approvals/` directory as evidence (a foot-gun and a DoS).

## Threat-model summary (corrected in rev 2)

| Adversary | Outcome with this design |
|---|---|
| Naive or prompt-injected agent (same uid) writing files, running `ao approve`, curling the dashboard, editing `state.json`, the spec, loop clones or `.ao/config.yaml` before a resume | **Stopped** deterministically for the attack paths with an MVP mitigation: unsigned/forged/replayed records refused (D3); key redirection through workspace config refused (D3); `ao approve` refused under the marker (D5); dashboard refuses anonymous (D4); tampered gate statuses re-derived or re-asked, removed/weakened gates and detached downstream work refused, loop clones re-derived (D7); deleting the policy together with the gate and its workspace artifacts refused while the run has a gated marker (D14). **Not stopped** (documented residuals that need no design knowledge): a CLI approval of a gate without `require_dashboard` by an agent that bypasses the speed bumps (RR-2); work that does not depend on the gate, e.g. injected tasks (RR-9, RR-13); an agent-chosen route that skips a gate placed in an optional route (RR-12); changes to files the gate does not review (RR-14). Rev 1's "stopped deterministically … spec edits refused" over-claimed and was corrected at Gate 1 |
| Agent that deliberately unsets the marker and allocates a pty | Can approve gates that allow CLI decisions. **Not** gates with `require_dashboard` + listed approvers + enabled dashboard auth |
| Agent that erases the gate's workspace artifacts before a resume (edits the spec, nulls the policy, deletes `approvals/`) — reachable by an agent merely **instructed** to "remove the approval gate", no design knowledge needed (corrected in rev 3; rev 2 claimed it needed design knowledge) | **Stopped** for every run that has a gated marker (D14): the resume fails closed. **Not stopped** where no marker exists yet — the milliseconds between the first policy save and the first marker write, or a resume in an account or machine whose key directory has no marker for the run — then only in-state evidence protects the run (RR-5). Deleting or forging the marker itself touches the key directory: the RR-1 class |
| **Determined same-uid attacker who reads the approval key** | **Not stopped.** Can forge any request, decision or policy record (residual risk RR-1) |
| Other local user | Cannot forge (no access to the 0600 key) and cannot decide through the dashboard without a principal; the approvals tree is 0700 and not served by the dashboard. Approval metadata in `state.json`/`status.json`/`run.log` is as readable as every other run artifact — through an unauthenticated dashboard or a world-readable workspace (RR-15) |
| Remote web page | Stopped by the existing Host/Origin/Content-Type middleware, `X-Frame-Options: DENY`, and the two-step confirm |
| Malicious workflow author | Out of scope: the author defines the gates |

**Residual risk, stated plainly:** a process running as the same OS user can read the 0600 key and forge
every decision. A real boundary requires running agents under a **separate OS user or sandbox** without
read access to the key directory and without write access to the run's approvals directory, an
**approval broker** process under its own uid that holds the key, or **out-of-band user presence** (a
WebAuthn/FIDO2 assertion over the request digest) that no process can produce. This is roadmap item
R-AG-1. Further residual risks (HLD §7.6): speed-bump bypass (RR-2), post-approval artifact changes (RR-3),
DoS by same-uid processes (RR-4), erasure of the gate's workspace evidence where no gated marker exists
(RR-5; F-15 would remove that window), audit truncation (RR-6), work that does not depend on a gate (RR-9, RR-13), clock jumps (RR-10),
agent-authored routing verdicts (RR-12), unreviewed downstream files (RR-14), approval metadata in run
files (RR-15). Closed at Gate 1: parser denial of service, dashboard exposure of the approvals tree,
resume detection from `spec_sessions`, gate status flips, loop-clone tampering, `$HOME` injection.
Closed at Gate 2: erasure by an instructed agent for runs with a marker (D14), the planted-directory DoS,
the directory-before-policy crash window.

## Consequences

- Positive: approvals are content-bound and auditable; gate-free workflows are byte-identical (including
  spec hashes); waits cost nothing and never hold a slot; resume never silently re-asks or silently
  approves, and never trusts agent-writable files to skip a check; the ordinary "edit the spec, resume"
  loop keeps working for gated runs, except for edits that would weaken a gate.
- Negative: dashboard decisions require dashboard auth; removing, weakening or detaching anything a gate
  protects requires a new run, and so does changing a gated loop's body structure while iterations exist;
  loop-body gates need explicit `depends_on`; losing or rotating the key voids in-flight gated runs; an
  older `ao` cannot read a state that contains gates (one-way upgrade); a workspace config can no longer
  set `XDG_CONFIG_HOME`/`AO_APPROVAL_KEY_DIR`/`AO_IN_AGENT`/`HOME`/`USER`/`LOGNAME`; a workspace at `$HOME`
  cannot run gated workflows; the file browser no longer shows run `approvals/` directories; a gated
  isolated run holds the workspace run lock while it waits; resumed runs (gated or not) resolve the key
  directory and `lstat` one gated-marker path; gated runs leave one small marker file each in the key
  directory until a cleanup exists (F-16).
- Cross-epic: the result cache (ADR-0019) **must never** settle, restore or skip an approval gate — a cache
  hit on a gate would be a silent approval. The dashboard-auth contract (ADR-0021) is consumed only through
  `ui/approvals_principal.read_principal`; both epics' config-env denylists and file-browser denials must
  merge into one helper each.
- Narrow exception to the engine's "never read payload content" invariant: `approvals/hashing.py` streams
  declared review files through SHA-256 (bounded, never parsed, never logged).
- The `ao validate` warning W-AG-8 (unreviewed downstream files, rev 2) was withdrawn in rev 3 as too noisy
  to be useful; RR-14 and the authoring rule document the gap.
