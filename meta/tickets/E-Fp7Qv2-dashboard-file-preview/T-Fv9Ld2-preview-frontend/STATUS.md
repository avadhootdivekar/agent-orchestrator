# STATUS

- ID: `T-Fv9Ld2-preview-frontend`
- Updated At: `2026-07-30`
- State: `Done`
- Owner: `claude` (frontend agent)

## This update
- By: architect · Role: architect · Date: 2026-07-30 · Comment: Task ticket scaffolded while the
  frontend agent implements. Scope: CONTRACT 1B.1–1B.7 — deps (`highlight.js` curated subset,
  `marked`, `dompurify`), viewer mode routing by `kind`, `CodeView`/`MarkdownView`/`HtmlPreview`/
  `ImageView`/`TypographyControls`, and vitest coverage. Coded against the contract while
  `T-Bk4Hs7-preview-backend` builds the API in parallel (same pattern as E-Tpl3x9's `T-Tf2end`).
  Design: `docs-md/dashboard-file-preview-hld.md` §5; the attacks these components stop: §2.7
  (markdown XSS), §2.8 (highlighter ReDoS), §2.9 (the sandbox-defeat trap); rationale: ADR-0011 D2.
- By: architect · Role: architect · Date: 2026-07-30 · Comment: Task **Done**. All five components
  landed with vitest coverage; the three "Decisions to record" `TODO`s are now filled from the
  shipped code. Headline decision: the optional per-file enable-scripts toggle was **deliberately
  not implemented**, taking the contract's own "if you are not confident, omit it" branch — which
  the subsequent audit vindicated, since finding H1 demonstrated a live egress channel with
  scripting **fully disabled**. This task owns no audit findings; the frame-level `PREVIEW_CSP` it
  ships was independently verified in real headless Chrome.

## Evidence
- **Gates (post-fix, post-audit), in `ui/`:**
  - `npm run typecheck` → clean.
  - `npm test` → **79 tests passed** across **8 files**.
  - `npm run build` → succeeds.
  - **No Python file modified by this task** (confirmed; the Python side is `T-Bk4Hs7` /
    `T-Sh6Rz3`).
- Code shipped: `ui/package.json` (deps), `ui/src/types.ts`, `ui/src/api.ts`, `ui/src/format.ts`,
  `ui/src/styles.css`, `ui/src/components/FileBrowser.tsx` (viewer pane rework), and
  `ui/src/components/viewer/{CodeView,MarkdownView,HtmlPreview,ImageView,TypographyControls}.tsx`.
- Tests shipped: `ui/src/test/{code-view,html-preview,markdown-view,typography}.test.tsx` plus the
  pre-existing suites, covering the security-relevant criteria: mode selection per `kind`
  (AC-3..7), the highlight cap on both sides of the boundary (AC-10), markdown
  `<script>`/`javascript:`/`<img onerror>` sanitized (AC-14), the iframe carrying **neither**
  `allow-same-origin` **nor** `allow-scripts` (AC-19), dropped refs rendering as text with no
  anchor or `src` produced (AC-23), and typography clamping garbage from `localStorage` (AC-27).
- **Contract reconciliation:** `ui/src/types.ts` mirrors the shipped Python dataclasses exactly —
  `kind`/`mime`/`data_uri` on `FileContent` and all **seven** `DroppedRef.reason` values, each
  mapped to a display label in `HtmlPreview.tsx`'s `REASON_LABEL`. **No drift** despite being
  written against the contract before the backend landed.
- **Frame CSP independently verified** (headless Chrome, `sandbox=""` iframe, the exact policy
  string in a `<meta http-equiv>` inside `srcdoc`): an inlined `<style>` block and a
  `data:image/png` both rendered, confirming `style-src 'unsafe-inline' data:` and `img-src data:`
  are each doing real work rather than being permissive-by-accident. Narrative preserved at
  `PREVIEW_CSP` in `HtmlPreview.tsx`.

## Decisions recorded (formerly TODO)
- **Optional per-file "enable scripts" toggle (AC-24): NOT IMPLEMENTED — deliberately.** Took the
  contract's explicit "if you are not confident, omit it" branch. `sandbox=""` is unconditional,
  with no code path that can add `allow-scripts`. The audit then made this look like the right
  call rather than a missing feature: finding H1 achieved a live one-click egress channel with
  scripting **entirely disabled**, so adding a scripting switch on top would have widened a
  surface that was already not fully understood. Recorded as a non-goal in HLD §8.
- **Final `DOMPurify` configuration:**
  - `ALLOWED_URI_REGEXP`:
    `/^(?:(?:https?|mailto):|[^a-z]|[a-z0-9+.-]+(?:[^a-z0-9+.:-]|$))/i` — `http`, `https`,
    `mailto`, and relative only, exactly the contract's scheme set.
  - `ALLOWED_TAGS` (27): `a`, `p`, `br`, `hr`, `h1`–`h6`, `strong`, `em`, `del`, `code`, `pre`,
    `blockquote`, `ul`, `ol`, `li`, `table`, `thead`, `tbody`, `tr`, `th`, `td`, `img`, `span`.
  - `ALLOWED_ATTR`: `href`, `title`, `alt`, `src`, `class`, `start`, `data-ao-pending`.
  - Note `data-ao-pending`: it carries a relative image ref through sanitization **without ever
    becoming a real `src`**, so the browser never fires a doomed same-origin request before the
    async resolution substitutes the real `data:` URI (or a broken-image note). A small but
    deliberate choice — the naive version leaks a request per relative image.
  - This is an explicit **keep-only allowlist** — worth contrasting with the Python sanitizer,
    which shipped as a blocklist and consequently produced audit findings H1/M1.
- **`highlight.js` registration list as shipped (18 explicit `registerLanguage` calls, no
  all-languages entry point):** `python`, `typescript`, `javascript`, `json`, `yaml`,
  `ini` (+`toml` alias), `rust`, `go`, `c`, `cpp`, `java`, `bash`, `markdown`,
  `xml` (+`html`/`svg`/`xhtml` aliases), `css`, `sql`, `diff`, `dockerfile` — the contract's set,
  with `toml`/`html` covered by alias rather than separate grammars.
  **Bundle delta: 66.2 KB → ~120 KB gzip**, shipped inside the Python wheel (epic NFR-5).
  Accepted tradeoff; the lever is trimming this list, which also *reduces* the ReDoS surface
  (§2.8), so the knob points the same way on both axes. HLD §7.2.

## Judgment calls flagged
1. **Mode-selection defaults shipped exactly as designed** — markdown → **Preview**, `markup` →
   **Source**. No deviation. Worth an explicit note because these are a security posture, not a UX
   preference: previewing untrusted markup stays a deliberate click.
2. **`dangerouslySetInnerHTML` used at two sites for two different reasons** — `hljs` output
   (escapes its own input) and DOMPurify output (sanitized). Raw file text is never concatenated
   into either. Reasoning kept at both call sites, since the distinction *is* the safety argument.
3. **One shared module-scoped `marked` instance** rather than mutating the global singleton per
   render, with only `code` and `image` overridden — `code` routes fenced blocks through the same
   capped `hljs` path as `CodeView` (one highlighting implementation, per AC-15), and `image`
   defers relative refs to the data attribute described above. Everything `marked` emits still
   passes through DOMPurify afterward: the renderer decides *shape*, DOMPurify decides what
   survives.
4. **Typography deliberately not applied to `HtmlPreview`** (AC-28), with a comment saying so —
   that content brings its own styling and injecting ours would misrepresent the file.

## Risks / Blockers
- None; task closed. Original risks and their outcome:
  - **API field names a moving contract** — reconciled with zero drift (see Evidence).
  - **SPA CSP unverified** — verified by `T-Sh6Rz3` with a negative control; the sandboxed
    `srcdoc` frame with inlined `data:` images renders under it, so no workaround was needed and
    the iframe sandbox was never relaxed.
  - **The `allow-scripts` + `allow-same-origin` trap** — AC-20's call-site comment shipped, stating
    that the two together defeat the sandbox and that the framed document could script its way
    back to the parent origin's workspace-read + RCE + spend surface. Paired with a test asserting
    neither flag is present, so the combination cannot be introduced silently.
  - **`dangerouslySetInnerHTML`** — both sites reasoned and commented (judgment call 2).
  - **`localStorage` as untrusted input** — clamped through a fixed family lookup and numeric
    range clamps; a test feeds an injection string, `NaN`, absurd magnitudes, wrong types, and
    malformed JSON.
- Carried forward at epic level: browser coverage is Chrome-only; bundle size (both HLD §9.2/§7.2).

## Next actions
- None for this task. Epic-level follow-ups touching this code if picked up: trim the `hljs`
  language list if wheel size becomes a concern, and consume `ApiError.status` (currently threaded
  through but never read — reviewer's non-blocking suggestion).
