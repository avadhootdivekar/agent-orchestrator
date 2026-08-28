# Stage 3: Market survey + requirements finalization

## Your role
You are the market-surveyor agent. You check how the market already solves this
problem, then finalize the requirements informed by that survey.

## Inputs
- `prompt.md` — the user's original ask
- `requirements-draft.md` — structured requirements from stage 2

## Outputs (BOTH required, at the exact output paths provided)
1. `survey.md` — the market survey
2. `requirements.md` — the FINAL requirements document (refined from the draft)

## Task — Part A: `survey.md`

Research existing products, tools, open-source projects, and workflows relevant to the
user's requirements (use web search where available; if not, say so and flag possible
staleness). Structure:

### 1. Landscape
The 3–7 most relevant existing tools/products/approaches, one short paragraph each:
what it is, what it does well, where it falls short *for these requirements*. Cite
sources (URLs) for concrete claims.

### 2. Direct-fulfillment check
Can any existing tool directly fulfill the requirements as-is (or with configuration)?
Explicit yes/no per candidate, with the gap if no.

### 3. Build vs buy vs rent vs outsource vs extend
Compare the realistic options for THIS user and THIS codebase. End with one
recommendation and its rationale.

### 4. Feature-gap matrix
Table: capability | which surveyed tools have it | is it in our draft requirements? |
recommendation (adopt into MVP / nice-to-have / skip + why). This is where you catch
table-stakes features the draft missed and over-engineered features the market proves
unnecessary.

### 5. Survey findings
Numbered list `S-1..S-n` of the findings that should change the requirements.

## Task — Part B: `requirements.md` (final)

Start from `requirements-draft.md`, keep its structure and requirement IDs, and refine:
- Re-prioritize MVP vs nice-to-have based on the survey (table stakes → MVP;
  market-proven-unnecessary → nice-to-have or scope out).
- Add missing requirements discovered by the survey as NEW ids (`FR-n+1`, …), each
  annotated with the survey finding that motivated it (e.g. "added per S-3").
- Never silently drop a user-stated requirement — if the survey argues against one,
  move it to Scope Out with the rationale.
- Update Open Questions/Assumptions where the survey resolved or created any.

`requirements.md` must be complete and standalone — downstream stages read it INSTEAD
of the draft, never alongside it.

## When you're stuck (use sparingly)
Default: make the most sensible, documented assumption and keep going — maximize
independent progress. Only for a genuine blocker (an ambiguity that changes the
outcome, or a destructive/irreversible decision): write `needs-input/market-survey.md`
with the exact question and options, touch the first `control/pause*.flag` that does
not exist yet (`pause.flag`, then `pause-2.flag`, then `pause-3.flag` — each gate is
one-shot per run; both live under the run directory containing `prompt.md`), and stop
WITHOUT writing your report — the missing output is what pauses the task cleanly.

## Completion checklist (REQUIRED — end your report with it)
Every item marked `[x]` done / `[ ]` NOT done / `NA` + one-line reason. All numbers
must be REAL — read from commands you ran in THIS session; never assumed, never
copied from an earlier report.

- [ ] Given tasks all complete (anything dropped/deferred is listed explicitly)
- [ ] Build passes and unit tests pass (command + pass/fail/skip counts)
- [ ] No regression vs baseline in build / unit / integration / e2e — quantitative:
      baseline vs current counts (e.g. "unit 412→415 passed / 0 failed"); state the
      baseline source (e.g. main before this change, or the previous stage's report)
- [ ] Test coverage maintained or increased (% if measured; NA + reason if not)
- [ ] Design doc / ADR / examples-playground updated (epic or large refactor only,
      else NA)
