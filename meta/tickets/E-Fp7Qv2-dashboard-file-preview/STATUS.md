# STATUS: E-Fp7Qv2-dashboard-file-preview

- Status: `Done`
- Last Updated: `2026-07-30`
- Owner: `avadhoot`

## Rollup
| Task | Status |
|------|--------|
| T-Bk4Hs7-preview-backend | Done |
| T-Sh6Rz3-origin-host-csrf | Done |
| T-Fv9Ld2-preview-frontend | Done |

Epic status is `Done` because all three tasks are `Done`, with evidence for every FR/NFR reported
in the task `STATUS.md` files and summarized below. Implementation was additionally **reviewed,
security-audited, fixed, and re-verified** before close.

## This update
- By: architect · Role: architect · Date: 2026-07-30 · Comment: Ticket + design scaffolding
  created for an epic already in flight. Three tasks opened and all marked In Progress; design
  doc and ADR written; no production code or tests touched.
- By: architect · Role: architect · Date: 2026-07-30 · Comment: Epic **closed** and all docs
  synced to what actually shipped. Beyond the status flip, three substantive changes: a **factual
  correction** to the HLD's false "the sandbox blocks navigation anyway" rationale (§2.2.1 —
  sandboxed frames can always navigate *themselves*, and no CSP directive stops navigation; this
  premise is what let finding H1 through all three defence layers); a new **HLD §10** cataloguing
  the adversarial audit (H1/M1/L1–L4 with fixes, plus §10.4's attacked-and-found-sound negative
  results); and **ADR-0011's post-audit note** recording that the layering is load-bearing rather
  than belt-and-braces. The CSP open question is resolved, and four follow-ups are recorded as
  known-open rather than dropped.

## Evidence
- Epic + task tickets: `EPIC.md`, this file, and
  `T-{Bk4Hs7,Sh6Rz3,Fv9Ld2}-*/{TASK.md,STATUS.md}` (per-task evidence logs live in the task
  `STATUS.md` files; summarized here).
- **Gates, run post-fix and post-audit:**

  | Gate | Result |
  |---|---|
  | `uv run pytest -q` | **1793 passed, 7 skipped** (pre-epic baseline: **1651 passed, 7 skipped**) |
  | `uv run ruff check .` | clean |
  | `uv run ruff format --check .` | clean except `tests/test_e2e_builtin_routed_runner.py` — confirmed failing **identically at HEAD** and untouched by this epic |
  | `uv run mypy src` | clean except the 4 pre-existing `_version.py` errors — confirmed failing **identically at HEAD** and untouched |
  | `ui/`: `npm run typecheck` | clean |
  | `ui/`: `npm test` | **79 tests passed** across 8 files |
  | `ui/`: `npm run build` | succeeds |

  Both non-clean gates were checked against HEAD to establish they are pre-existing rather than
  introduced — worth recording, since "clean except X" is otherwise indistinguishable from a
  regression being waved through.
- **CSP empirically verified (was the epic's one open question).** Real headless Chrome, twice
  independently (SPA document policy and frame `<meta>` policy), each with a **negative
  control**: removing `data:` from `img-src` made the same sampled pixel return page background
  instead of the test image's red. That control is what makes it evidence rather than a demo —
  it proves the policy *discriminates* instead of being permissive-by-accident. Drafted policy
  shipped unchanged. Method and scope limits: HLD §4.3. Narratives preserved at
  `security.py::SPA_CSP` and `HtmlPreview.tsx::PREVIEW_CSP`.
- **Adversarial security audit** (execution-confirmed, real browser, real captured requests):
  1 High + 1 Medium + 4 Low, **all fixed and re-verified**. Catalogued with evidence in HLD §10.
- **Accepted tradeoff:** frontend bundle grew **66.2 KB → ~120 KB gzip**, and it ships inside the
  Python wheel. Lever if it becomes a problem: trim the curated `highlight.js` language list in
  `CodeView.tsx` (18 explicit `registerLanguage` calls in one place; an unregistered language
  degrades to plain text, never an error) — which also *reduces* the ReDoS surface, so the knob
  points the same way on both axes. HLD §7.2.
- Docs synced: `docs-md/dashboard-file-preview-hld.md` (status; §2.2.1 navigation correction;
  §4.2 corrected `<a href>` rationale + new SMIL row; §4.3 resolved CSP with method; §7.1
  verified gate results; §7.2 bundle-size tradeoff; §8 toggle not implemented; §9
  resolved-vs-still-open; **§10 adversarial audit findings**) and
  `docs-md/adr/ADR-0011-untrusted-workspace-content-rendering.md` (status; D3's CSP consequence
  resolved; **post-audit note** with the layering lesson).

## Risks / Blockers
- Not blocked; epic closed. Original risks and their outcome (detail in the task `STATUS.md`
  files and `EPIC.md`):
  1. **Hand-written sanitizer bugs** — **materialized**, and the mitigation worked as designed.
     H1 and M1 were both real sanitizer gaps; the layering contained both (M1 blocked by the
     frame CSP, never exploitable in shipped form; H1 confined to the iframe with the top frame
     verified unmoved). Fixed and re-verified. The sandbox-first/sanitizer-second ordering was
     **not** inverted in review, which is exactly why neither was serious.
  2. **SPA CSP correctness** — resolved by empirical verification with a negative control.
  3. **`ui/app.py` edited by two tasks concurrently** — no lost change.
  4. **Frontend/backend contract drift** — none; field names and all seven `DroppedRef.reason`
     values matched at integration.
  5. **Fixture-driven erosion of the `Host` allowlist** — the accommodation stayed in one place,
     paired with tests that build an app without it and assert rejection still happens.
- **Discovered risk not anticipated at planning time:** a **false premise in this epic's own
  design doc** ("the sandbox blocks navigation anyway"). Stated as settled fact rather than
  flagged as an assumption, so nothing prompted verification — and it is the reasoning that let
  H1 reach a shipped build. Corrected in HLD §2.2.1 and filed as ADR-0011's central lesson: an
  unexamined premise is more dangerous than an acknowledged unknown.

## Known-open follow-ups (recorded, not dropped)
1. **Sanitizer element/attribute policy is a blocklist**, not the keep-only allowlist the design
   specified. Correct against every payload tried, but structurally a bet that the dangerous set
   was fully enumerated. Highest-value follow-up (HLD §9.2, ADR-0011 post-audit note).
2. **Browser coverage** — headless Chrome only; Firefox/WebKit untested.
3. **Pre-existing flake, unrelated to this epic:**
   `test_wave_scheduler.py::test_two_independent_tasks_overlap_at_max_parallel_two` is a
   wall-clock overlap assertion that flaked once under load and passes on rerun. Recorded so the
   next person who sees it red does not hunt through this epic's changes.
4. **Reviewer's remaining non-blocking suggestions:** `ApiError.status` threaded through but never
   consumed; `FileContent.truncated` semantically overloaded for images; CSS sub-concern could be
   split out of the 735-line `htmlpreview.py`; beta `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR` interaction
   with a real `uv tool install` untested **by design** (the fake-`uv` harness never touches the
   network).

## Next actions
- None outstanding for this epic. Recommended, not blocking:
  1. Convert the sanitizer to a true keep-only allowlist (follow-up 1) — the one change that
     would move layer 2 from "correct against known payloads" to "safe by construction."
  2. Re-run the CSP verification under Firefox/WebKit (follow-up 2).
  3. Leave the layering alone. Both audit findings were contained by layers the sanitizer does
     not control; treating layers 1 and 3 as formalities is the specific mistake ADR-0011's
     post-audit note exists to prevent.
