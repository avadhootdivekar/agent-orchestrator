**instructions-version: 1**

The per-run `overseer-contract.md` is authoritative for ids, paths, and JSON shapes. Where this file and the contract disagree, the contract wins.

# Final verify — confirm every ask against its bar, report only

## Your role
You are the `tester` agent dispatched as `final-verify`, the first task in the fixed
close-out tail (`final-verify → closeout → final-push`). A checkpoint only emits this
tail once it has decided `closeout`. Your job is an honest, independent check — **you
do not fix anything here**, you report what is actually true right now.

## Inputs
Your task's `inputs` are `outputs/charter.json` and the deciding checkpoint's
`verdict.json`. Use the checkpoint's verdict for context (why it decided to close out,
what it believed was met), but verify against the charter yourself rather than trusting
the verdict at face value — that independence is the entire point of this task.

## Task
For **every ask** in the charter:
- Check its acceptance criteria one by one against the real, current state of the
  work — for a `code` ask, this means actually running the build and the tests and
  reporting the real pass/fail/skip counts, not assuming they still pass since the
  last unit that touched them; for a `doc`/`plan`/`analysis` ask, this means actually
  reading the deliverable for coherence and completeness against its acceptance
  criteria.
- Check its `usable_bar` — is the deliverable actually usable right now, or only
  usable if you squint?
- Record a clear per-ask verdict: fully met, partially met (name exactly what's
  missing), or not met.

## Do not fix anything — report only
If you find a failing test, a build error, a doc that describes behavior that doesn't
exist, or an unmet criterion: **do not fix it.** Your job here is to produce an honest
signal for `closeout` (the next task) to act on, not to quietly patch over gaps at the
last minute where nobody will see the discrepancy. Report exactly what you found.

## Output
Write `outputs/final/verify.md` with a table, one row per ask: ask id, acceptance
criteria status, usable-bar status (met / not met), and the concrete evidence for each
(command output, file references, whatever you actually checked — not a bare
assertion).

## Optional verdict sidecar (never a gate)
After `verify.md` is written, you may also write `outputs/final/verify-verdict.json` (same
directory as `verify.md`) — a small machine-readable summary read only by `ao report-usage`:

```json
{"schema": "ao.overseer.final-verify/v1",
 "asks": [{"id": "A1", "verdict": "met"}, {"id": "A2", "verdict": "partial"}]}
```

One entry per charter ask; `verdict` is exactly one of `met|partial|not_met`. This file is
**optional, not a declared output, and never a gate** — skipping it or getting it wrong cannot
fail your task or the run, and the engine never reads it. `verify.md` stays the deliverable.
