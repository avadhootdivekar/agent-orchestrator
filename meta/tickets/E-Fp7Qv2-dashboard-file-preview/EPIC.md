# EPIC: E-Fp7Qv2-dashboard-file-preview

## Metadata
- Epic ID: `E-Fp7Qv2-dashboard-file-preview`
- Title: `Dashboard file preview — markdown/code/image/HTML viewing for untrusted workspace content, plus Origin/Host/CSRF hardening`
- Owner: `avadhoot`
- Created: `2026-07-30`
- Last Updated: `2026-07-30`
- Status: `Done`

## Summary
- Goal: Turn the dashboard's plain-text viewer pane into a real file viewer — rendered
  markdown, syntax-highlighted code, inline images, sandboxed HTML/SVG preview, and
  user-adjustable typography — **without ever letting agent-written workspace content
  execute in the dashboard's unauthenticated, RCE-capable origin**. The security model is
  the design, not a caveat on it (see `docs-md/dashboard-file-preview-hld.md` §1–§3).
- Scope In: additive `kind`/`mime`/`data_uri` classification on
  `GET /api/files/content`; new `GET /api/files/html` returning sanitized, self-contained
  HTML with in-root assets inlined as `data:` URIs and every other ref dropped-and-reported;
  new `ui/security.py` Origin/Host/CSRF middleware + response headers + SPA CSP (closes a
  pre-existing critical gap); frontend viewer split into `CodeView`/`MarkdownView`/
  `HtmlPreview`/`ImageView`/`TypographyControls`; Python + vitest coverage for every
  mitigation.
- Scope Out: **authentication** (ADR-0010 D7 stands; roadmap §3.1); **any raw-bytes file
  endpoint** — explicitly rejected in ADR-0011 D1, not deferred; scripts enabled in previews
  by default; in-browser editing of workspace files; video/audio preview; server-side
  markdown rendering; a trusted-external-host allowlist for assets.

## Threat model (drives every requirement below)
1. **Workspace content is attacker-controlled.** Agents write these files, and the benchmark
   tiers (E-Bt4Xk9) import third-party SWE-bench repos into the workspace. `.html`, `.svg`,
   and `.md` bytes are unreviewed.
2. **The dashboard origin is privileged.** No auth (ADR-0010 D7); `POST /api/runs` is an
   arbitrary-code-execution-and-spend primitive, and `GET /api/files*` reads every root.
   One line of JS in that origin is a full workspace compromise.
3. **Egress needs no JavaScript.** `img`/CSS `url()`/`@font-face`/`meta refresh`/`@import`
   all fire without script, and reach hosts (loopback, LAN, metadata endpoints) an external
   attacker cannot.

Three invariants follow, and every FR below traces to one:
- **I1** — previewed content never executes in the dashboard origin (opaque-origin boundary).
- **I2** — previewed content has no network egress channel (`data:` is the only surviving scheme).
- **I3** — sanitization is server-side and authoritative; client guards are defence in depth.

## Requirements
- FR-1: `GET /api/files/content` gains `kind` (`text|image|markup|binary`), `mime`,
  `data_uri` — **additive only**; every existing field keeps its meaning. `image` requires
  an allowlisted extension **AND** confirming magic bytes; emitted MIME comes from the
  extension allowlist only (never sniffed, never echoed from the request). Named constants:
  `IMAGE_INLINE_MAX_BYTES`, `IMAGE_MIME_BY_EXT`, `IMAGE_MAGIC_PREFIXES`, `MARKUP_EXTENSIONS`.
- FR-2: **SVG is `markup`, never `image`** — it is an XML document that can carry `<script>`,
  so it routes through the sanitize pipeline and never reaches an `<img>` tag or the page DOM.
- FR-3: New `GET /api/files/html?path=&root=` → `HtmlPreview` (sanitized self-contained HTML,
  `inlined`, `dropped[]`, `scripts_removed`, `truncated`, `budget_bytes`, `budget_used`).
  Sanitizer **parses** (stdlib `html.parser.HTMLParser`) and re-serializes from an allowlist —
  regex tag-stripping is not acceptable. All text/attribute values `html.escape(quote=True)`
  on re-serialize; attribute values always double-quoted. No new dependency. (I1, I3)
- FR-4: One URL pipeline for every URL site (attributes, `srcset` candidates, CSS `url()`,
  `@import`): strip whitespace/control chars **before** scheme detection; **only `data:`
  survives**; http/https → `external`, everything else (`file`, `javascript`, `vbscript`,
  `blob`, `//host`, `\\host`) → `unsupported-scheme`. (I2)
- FR-5: In-root refs resolve against **the HTML file's own directory**, percent-decoded once,
  NUL-rejected, then contained by **`FileBrowser.resolve`'s existing guard** (resolve-then-
  prefix-check, so `..` and symlink escapes both fail closed). No second path resolver.
- FR-6: Budgets as named constants — `HTML_INLINE_BUDGET_BYTES = 10_000_000`,
  `HTML_ASSET_MAX_BYTES = 5_000_000`, `HTML_MAX_IMPORT_DEPTH = 3`, source bounded by existing
  `MAX_READ_BYTES`. Exceeding a budget **drops the ref whole** with the correct reason —
  never truncates an asset mid-stream.
- FR-7: New `ui/security.py` Starlette middleware in `create_app`: `Host` allowlist → **421**
  (kills DNS rebinding); `Origin` check on `POST/PUT/PATCH/DELETE` → **403** (absent `Origin`
  allowed for non-browser clients); `Content-Type: application/json` required on mutating
  routes → **415** (kills `<form>` CSRF); `nosniff` + `Referrer-Policy: no-referrer` +
  `COOP`/`CORP: same-origin` on every response; CSP on the SPA document only.
  `AO_UI_ALLOWED_HOSTS` configures; literal `*` disables with a loud startup warning.
  `ao ui` passes its bound host in and **extends** its existing off-loopback warning.
- FR-8: Frontend viewer mode selected by `kind`: markdown → Preview|Source (default
  **Preview**); other text → `CodeView`; `markup` → Preview|Source (default **Source** —
  previewing untrusted markup is a deliberate click); `image` → `ImageView` from `data_uri`
  (null → size + reason); `binary` → existing metadata banner.
- FR-9: `CodeView` — `highlight.js` with a curated language subset only (bundle ships in the
  wheel); `HIGHLIGHT_MAX_BYTES = 256_000` cap → plain text with a note beyond it; unknown
  language → plain text; line numbers via CSS counters / `user-select: none` so copy yields
  code only.
- FR-10: `MarkdownView` — `marked` → `DOMPurify.sanitize` with an explicit tag/attr allowlist
  and `ALLOWED_URI_REGEXP` limited to `http`/`https`/`mailto`/relative; external links get
  `target="_blank" rel="noopener noreferrer"`; relative image refs resolved by **calling
  `/api/files/content`** and substituting `data_uri` (cap `MARKDOWN_MAX_IMAGES = 50`), with a
  403/404 rendering as a broken-image note and **never** as a raw URL elsewhere.
- FR-11: `HtmlPreview` renders into `<iframe sandbox="" referrerPolicy="no-referrer"
  srcDoc={…}>` — **no `allow-scripts`, never `allow-same-origin`** (the two together defeat
  the sandbox entirely; a code comment saying so is a required deliverable). Prepends a frame
  `<meta http-equiv="Content-Security-Policy">` as defence in depth, and surfaces
  `scripts_removed`/`inlined`/`dropped` (grouped by reason) above the frame **as text, never
  links**.
- FR-12: `TypographyControls` — family/size/line-height/letter-spacing via CSS custom
  properties on the viewer container, persisted under one namespaced `localStorage` key,
  **validated and clamped on read-back** (never interpolate a stored string into a style
  attribute). Applies to `CodeView`/`MarkdownView`/plain text, **not** `HtmlPreview` (that
  content brings its own styling) — with a comment saying so.
- NFR-1: The `[ui]` extra stays optional; nothing in core imports fastapi at module scope; no
  new Python runtime dependency for sanitization (stdlib only).
- NFR-2: Every mitigation in HLD §2 has at least one regression test whose failure means the
  mitigation is gone. A security control with no test is a comment.
- NFR-3: No magic literals — every cap, allowlist, and MIME map is a named constant.
- NFR-4: Existing `tests/ui/*` keep passing; the `TestClient` `Host: testserver`
  accommodation lives in **one** place (`tests/ui/conftest.py`) and is paired with tests that
  assert rejection still happens.
- NFR-5: Bundle-size discipline — `highlight.js` grammars are registered from a curated list,
  since the built frontend ships inside the Python wheel.

## Task List
- [x] `T-Bk4Hs7-preview-backend` — **Done** — CONTRACT 1A.1/1A.2 + the 1A.4 tests for
  them: classification on `/api/files/content`, new `ui/htmlpreview.py` sanitizer + inliner,
  `GET /api/files/html` route and error mapping. Maps FR-1..FR-6.
- [x] `T-Sh6Rz3-origin-host-csrf` — **Done** — CONTRACT 1A.3 + its 1A.4 tests: new
  `ui/security.py` middleware, response headers, SPA CSP (**empirically verified in real
  headless Chrome, with a negative control**), `ao ui` bound-host wiring. Carved out of the
  backend task because it closes a **pre-existing critical gap** and is valuable, reviewable,
  and revertable independently of preview. Maps FR-7.
- [x] `T-Fv9Ld2-preview-frontend` — **Done** — CONTRACT 1B.1..1B.7: deps, viewer mode
  routing, `CodeView`/`MarkdownView`/`HtmlPreview`/`ImageView`/`TypographyControls`, vitest.
  Maps FR-8..FR-12.

Task numbering note: CONTRACT §1A.3 sits numerically inside the backend block but is tracked
as its own task above; §1A.4 (backend tests) is split so each test group ships with the code
it pins. Together the three tasks cover CONTRACT 1A.1–1A.4 and 1B.1–1B.7 with no gaps.

## Risks and Dependencies

All original risks are resolved or explicitly carried forward. Outcome per risk:

- **Three agents implementing in parallel on one branch** — *resolved.* Ownership partitioning
  held; `ui/app.py` (routes + middleware) took edits from both Python tasks without a lost
  change.
- **The frontend codes against a contract the backend is building at the same time** —
  *resolved.* Field names (`kind`, `mime`, `data_uri`, the seven `DroppedRef.reason` values)
  matched at integration; `ui/src/types.ts` mirrors the shipped dataclasses.
- **The SPA CSP is not yet known to be correct** — **RESOLVED, verified, drafted policy shipped
  unchanged.** Verified twice independently in **real headless Chrome**, each with a
  **negative control** (removing `data:` from `img-src` blocked the same test pixel), so the
  policy demonstrably discriminates rather than being permissive-by-accident. Method in HLD
  §4.3; the narratives live at `security.py::SPA_CSP` and `HtmlPreview.tsx::PREVIEW_CSP`.
- **A hand-written sanitizer will have bugs** — **materialized, and the mitigation worked as
  designed.** An adversarial audit found one High (H1) and one Medium (M1); the layering
  contained both (M1 was blocked by the frame CSP and never exploitable in shipped form; H1
  was confined to the iframe with the top frame never moving). All fixed and re-verified. See
  HLD §10. The ordering (sandbox layer 1, sanitizer layer 2) was **not** inverted, which is
  precisely why neither finding was serious.
- **Fixture-driven control erosion** — *resolved.* The `TestClient` accommodation stayed in one
  place and is paired with tests that build an app without it and assert rejection still
  happens (`T-Sh6Rz3` AC-17).
- **Asset budget may prove too tight** — *carried forward as an assumption*, unchanged; HLD
  §9.2. No evidence either way yet.
- Depends on the E-Ui7Kq2 dashboard (`FileBrowser.resolve` guard, thin service/adapter split)
  and inherits its no-auth posture. Shares branch `ad/workflow-templates` with E-Tpl3x9
  (merged) and `E-Bi5Nw8-dual-flavor-install` (independent scope, also `Done`).

### Discovered risk not anticipated at planning time
- **A false premise in the design document.** The HLD justified stripping `<a href>` with "the
  sandbox blocks navigation anyway." That is **false** — a sandboxed iframe can always navigate
  **itself**, and **no CSP directive governs navigation at all**. It was stated as settled fact
  rather than flagged as an assumption, so nothing prompted anyone to check it, and it is the
  reasoning that let H1 through all three defence layers. Corrected in HLD §2.2.1 + §4.2 and
  recorded as the central lesson in ADR-0011's post-audit note. Contrast with the CSP question,
  which *was* flagged open and consequently got verified with controls: **the dangerous items
  are the ones nobody thought to list.**

### Known-open follow-ups (recorded, not silently dropped)
1. **Element/attribute policy is a blocklist, not the keep-only allowlist the design
   specified.** Now correct against every payload tried, but structurally a bet that the
   dangerous set was fully enumerated — H1/M1 are evidence that bet loses periodically.
   Highest-value follow-up; deferrable only because layers 1 and 3 demonstrably catch layer-2
   misses (ADR-0011 post-audit note, HLD §9.2).
2. **Browser coverage** — verification was headless Chrome only; Firefox/WebKit untested.
3. **Flaky pre-existing test, unrelated to this epic.**
   `test_wave_scheduler.py::test_two_independent_tasks_overlap_at_max_parallel_two` is a
   wall-clock overlap assertion that flaked once under load and passes on rerun. Pre-existing
   and out of scope here, but it **will** bite CI intermittently and is recorded so the next
   person to see it red does not go hunting in this epic's changes.
4. **Reviewer's remaining non-blocking suggestions:** `ApiError.status` is threaded through but
   never consumed; `FileContent.truncated` is semantically overloaded for images; the CSS
   sub-concern could be split out of the 735-line `htmlpreview.py`; and beta
   `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR` interaction with a real `uv tool install` is untested **by
   design** (the fake-`uv` harness deliberately never touches the network).

## Links
- Design doc: `docs-md/dashboard-file-preview-hld.md`
- ADR: `docs-md/adr/ADR-0011-untrusted-workspace-content-rendering.md`
- Prior art / prerequisites: `docs-md/dashboard-and-general-instructions-hld.md` §2.3, §2.7 ·
  `docs-md/adr/ADR-0010-dashboard-architecture-and-general-instructions.md` D5, D7
- Deferred security work this epic does **not** close: `meta/ROADMAP.md` §3.1
- Escaping bug class this epic must not repeat: `meta/learnings.md`
  ("template render escaping/containment")
- Sibling epic on the same branch: `meta/tickets/E-Bi5Nw8-dual-flavor-install/EPIC.md`

## Comments
- By: architect · Role: architect · Date: 2026-07-30 · Comment: Epic scaffolded from the
  shared implementation contract while three agents implement in parallel. Wrote
  `docs-md/dashboard-file-preview-hld.md` (threat model §1, ten attack classes with adopted
  mitigations §2, inline-vs-proxy rationale and the accepted budget tradeoff §3) and
  `ADR-0011` (D1 inline+sandbox over a raw-bytes proxy, D2 opaque origin as the boundary with
  sanitization second, D3 the dashboard-origin trust boundary). Carved 1A.3 into its own task
  because it fixes a pre-existing CSRF/DNS-rebinding gap that stands on its own merits.
  Flagged one open question that implementation must answer empirically rather than by
  reading the spec: whether the SPA CSP as drafted permits a sandboxed `srcdoc` frame with
  inlined `data:` images (HLD §9, owned by `T-Sh6Rz3`). Documentation and traceability only —
  no production code or tests written under this epic by the architect role.
- By: architect · Role: architect · Date: 2026-07-30 · Comment: Epic **closed**. All three tasks
  Done, implementation reviewed, security-audited, fixed, and re-verified — gates: **1793 passed
  / 7 skipped** (baseline 1651/7), ruff clean, mypy clean but for 4 pre-existing `_version.py`
  errors, `ui/` typecheck clean with **79 frontend tests** passing and `npm run build`
  succeeding. Docs synced to what actually shipped, and three substantive changes are worth
  calling out rather than treating as a status flip. (1) **A factual correction, not a status
  update:** the HLD's rationale that stripping `<a href>` is safe because "the sandbox blocks
  navigation anyway" was **false**. A sandboxed iframe can always navigate *itself* — only other
  browsing contexts are restricted — and **no CSP directive stops navigation at all**. Corrected
  in HLD §2.2.1/§4.2. This mattered: it is the reasoning that let finding H1 pass unnoticed
  through all three defence layers. (2) **HLD §10 added** cataloguing the adversarial audit —
  H1 (SVG SMIL `<animate>` retargeting `href` at render time, defeating `href` stripping;
  confirmed by real synthetic click and a captured request; contained to the iframe, so a real
  egress channel but not a sandbox escape), M1 (CSS `image-set()` bypassing the URL pipeline,
  blocked by the frame CSP so never exploitable in shipped form), and L1–L4 — plus §10.4's
  *negative* results, which are real evidence of design quality: parse-don't-regex defeated
  `<scr<script>ipt>`, percent-decode-once resisted double-encoding, and browsers proved unable
  to construct a bodyless cross-origin state-changing request without an `Origin` header.
  (3) **ADR-0011's post-audit note** records the architectural lesson: the layering is
  **load-bearing, not belt-and-braces** — layer 3 alone neutralized M1, layer 1 alone contained
  H1 — but because the sanitizer shipped as a **blocklist** rather than the specified keep-only
  allowlist, layer 2 will keep having gaps, so the layers must stay independent. Also recorded:
  the CSP open question is resolved (verified twice in real headless Chrome **with a negative
  control**, drafted policy unchanged), the `allow-scripts` toggle was **deliberately not
  implemented** per the contract's own "if not confident, omit it", the bundle grew 66.2 KB →
  ~120 KB gzip as an accepted tradeoff whose lever is trimming the curated hljs list, and four
  known-open follow-ups including the pre-existing `test_wave_scheduler.py` wall-clock flake
  that will intermittently redden CI. Still documentation and traceability only — no production
  code or tests written by the architect role at any point in this epic.
