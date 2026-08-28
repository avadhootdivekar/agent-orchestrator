# TASK: T-Fv9Ld2-preview-frontend

## Metadata
- Task ID: `T-Fv9Ld2-preview-frontend`
- Epic ID: `E-Fp7Qv2-dashboard-file-preview`
- Owner: `claude` (frontend agent)
- Created: `2026-07-30`
- Last Updated: `2026-07-30`
- Status: `Done`
- Estimate: `< 3 days`

## Requirements Mapping
- Epic FR-8 (mode selection by `kind`), FR-9 (`CodeView` + highlight cap), FR-10
  (`MarkdownView` + DOMPurify + relative-image resolution via the API), FR-11 (`HtmlPreview`
  sandboxed iframe + frame CSP + provenance panel), FR-12 (`TypographyControls` + clamped
  `localStorage`).
- Epic NFR-2 (a test per mitigation), NFR-5 (bundle-size discipline — the built frontend ships
  inside the Python wheel).
- CONTRACT sections 1B.1–1B.7.
- Design: `docs-md/dashboard-file-preview-hld.md` §2.7, §2.8, §2.9, §5 · ADR-0011 D2.

## Description
Rework the dashboard's viewer pane from "plain monospace text" into a real viewer: rendered
markdown, highlighted code, inline images, sandboxed HTML/SVG preview, and adjustable
typography.

This code renders **untrusted, agent-written content inside the dashboard's own origin**,
which has no authentication and can `POST /api/runs` (arbitrary agent execution + spend). Two
consequences shape every component here:

- **`HtmlPreview` gets an opaque origin, and that is the actual security boundary** (epic
  invariant I1). `sandbox=""` — no `allow-scripts`, and **never** `allow-same-origin`. The
  server's sanitizer is layer 2, not the boundary (ADR-0011 D2).
- **`MarkdownView` has no such boundary** — markdown renders in the app origin on purpose, so
  DOMPurify *is* the guard there. That asymmetry is why markdown defaults to Preview while
  `markup` defaults to Source.

Files owned:
- `ui/package.json` (add deps)
- `ui/src/types.ts`, `ui/src/api.ts`, `ui/src/format.ts`, `ui/src/styles.css`
- `ui/src/components/FileBrowser.tsx` (rework the viewer pane)
- `ui/src/components/viewer/*.tsx` (new: `CodeView`, `MarkdownView`, `HtmlPreview`, `ImageView`,
  `TypographyControls`)
- `ui/src/test/*.test.tsx`

**Do NOT touch any Python file.** The backend is being built in parallel (`T-Bk4Hs7`,
`T-Sh6Rz3`); code against the contract.

## Acceptance Criteria

**Dependencies (1B.1)**
1. `highlight.js`, `marked`, and `dompurify` are added to `ui/package.json`.
2. `highlight.js` languages are registered from a **curated explicit list** — `python,
   typescript, javascript, json, yaml, toml, rust, go, c, cpp, java, bash, markdown, xml/html,
   css, sql, diff, dockerfile, ini` — not the full bundle. Pass/fail: no import of the
   all-languages entry point. The built bundle ships inside the Python wheel, so this is a cost
   every `pip install` pays, and fewer grammars is also less regex surface.

**Mode selection (1B.2)**
3. `kind: "text"` + a markdown extension → **Preview | Source** toggle, defaulting to
   **Preview**.
4. `kind: "text"` otherwise → `CodeView`.
5. `kind: "markup"` → **Preview | Source** toggle, defaulting to **Source**. Previewing
   untrusted markup must be a deliberate click, never automatic on selecting a file.
6. `kind: "image"` → `ImageView` from `data_uri`; when `data_uri` is `null` (over the server's
   cap), show **size + reason** rather than a broken image.
7. `kind: "binary"` → the existing metadata banner, unchanged.
8. Switching files resets view mode and any per-file toggles — no state leaks from one file's
   preview into another's.

**`CodeView` (1B.3)**
9. `hljs.highlight(code, { language, ignoreIllegals: true })`; an unknown/unmapped language
   renders as plain text (no grammar guessing).
10. **Highlighting is capped at `HIGHLIGHT_MAX_BYTES = 256_000`**; beyond it, plain text renders
    with a visible note. A test asserts both sides of the boundary. Rationale: regex highlighters
    can hang the tab on adversarial input and workspace files are untrusted — a hung tab is a
    denial of service on the operator's only view of their runs.
11. `dangerouslySetInnerHTML` is used **only** on `hljs` output, with a comment stating that
    `hljs` escapes its input and that raw file text is never concatenated into that HTML. Both
    halves of the comment are required — that distinction is the entire safety argument.
12. Line numbers render via **CSS counters or `user-select: none`**, never as text in the same
    node as the code, so selecting and copying yields code only. A test or an explicit DOM
    assertion covers this.

**`MarkdownView` (1B.4)**
13. `marked` output passes through `DOMPurify.sanitize` with an **explicit tag/attribute
    allowlist** and `ALLOWED_URI_REGEXP` limited to `http`, `https`, `mailto`, and relative.
    Not a default-allow, block-the-bad configuration.
14. Given markdown containing `<script>…</script>`, `[x](javascript:alert(1))`, a reference-style
    `[1]: javascript:alert(1)` link, `<img src=x onerror=alert(1)>`, and `<div onmouseover=…>`,
    then none survives as live markup or a live `javascript:` URL.
15. Fenced code blocks highlight through the **same capped `hljs` path** as `CodeView` — one
    implementation, not two.
16. External links get `target="_blank"` **and** `rel="noopener noreferrer"`.
17. Relative image refs are resolved by calling `/api/files/content` for the sibling path and
    substituting the returned `data_uri`, capped at `MARKDOWN_MAX_IMAGES = 50`. Pass/fail: there
    is **no second path resolver in TypeScript** — containment is the server's guard, reused.
18. A ref that 403s or 404s renders as a **broken-image note**, never as a raw URL pointing
    elsewhere (which would turn a failed local read into an outbound request).

**`HtmlPreview` (1B.5)**
19. The frame is rendered as:
    ```tsx
    <iframe sandbox="" referrerPolicy="no-referrer" srcDoc={preview.html}
            title={`Preview of ${preview.path}`} />
    ```
    with **no `allow-scripts`** and **no `allow-same-origin`** by default. A test asserts the
    absence of both.
20. **A comment at the iframe states that `allow-scripts` + `allow-same-origin` together defeat
    the sandbox entirely** — framed content would run in the dashboard's own origin and could
    strip the `sandbox` attribute from its own element. This comment is a required deliverable,
    not a nicety: it is the control that stops a future "the charts don't work, let me add a
    flag" change. See HLD §2.9 / ADR-0011 D2.
21. A `<meta http-equiv="Content-Security-Policy">` is prepended to the framed document as
    defence in depth on top of the server-side stripping:
    ```text
    default-src 'none'; img-src data:; style-src 'unsafe-inline' data:; font-src data:;
    script-src 'none'; connect-src 'none'; form-action 'none'; base-uri 'none'
    ```
22. Provenance is surfaced honestly above the frame: `scripts_removed`, `inlined`, and the
    `dropped` list **grouped by reason**. The user asked for containment; they should be able to
    see what was contained.
23. **Dropped URLs render as text, never as links or as any URL-bearing attribute** — otherwise
    the provenance panel becomes the egress channel the whole design just closed. A test asserts
    no anchor/`src` is produced for a dropped ref.
24. Optional per-file "enable scripts" toggle, **if** shipped: `sandbox="allow-scripts"` only
    (never with `allow-same-origin`), off by default, reset on file change. **If not confident,
    omit it** — a half-safe version advertises safety it does not have. Whichever way, record the
    decision in `STATUS.md`.

**`TypographyControls` (1B.6)**
25. Font family (system / serif / mono), font size, line height, and letter spacing are
    adjustable, applied via **CSS custom properties** on the viewer container.
26. Settings persist to `localStorage` under **one namespaced key**.
27. **Values read back from storage are validated and clamped**: a stored value of
    `12px; background: url(https://evil.com/)` must not reach a style attribute. Parse to a
    number, clamp to a range, map family names through a fixed lookup. A test feeds garbage
    (injection string, `NaN`, absurd magnitude, wrong type, malformed JSON) and asserts sane
    clamped output.
28. Typography applies to `CodeView`, `MarkdownView`, and plain text, and **not** to
    `HtmlPreview` — that content brings its own styling and injecting ours would misrepresent it.
    A comment must say so; it reads as an oversight otherwise.

**Gates**
29. `npm run typecheck` and `npm test` in `ui/` both run, with **real** output reported in
    `STATUS.md`. No Python file is modified by this task.

## Risks
- **The backend contract is being built concurrently.** `kind`, `mime`, `data_uri`, and the
  `DroppedRef.reason` value set come from `T-Bk4Hs7` as it is written. Mirror the HLD §4 shapes
  exactly in `types.ts` and reconcile once the backend lands — the same pattern E-Tpl3x9's
  `T-Tf2end` used. A mismatch surfaces only at integration.
- **The SPA CSP from `T-Sh6Rz3` is not yet verified** (HLD §9). If it turns out to block a
  sandboxed `srcdoc` frame with inlined `data:` images, the preview will not render and the fix
  belongs in that task, not here. Report it rather than working around it by weakening the
  sandbox.
- **The `allow-scripts` + `allow-same-origin` trap** is the single most likely way this design
  gets silently broken later, and the realistic path is mundane: interactive content does not
  work, someone adds one flag, then the other. Criterion 20's comment exists for that person.
- **`dangerouslySetInnerHTML` appears twice in this task for different reasons** (hljs output;
  DOMPurify output). Both are legitimate; neither may ever receive raw file text. Keep the
  reasoning at each call site.
- **DOMPurify defaults are not sufficient on their own** — an explicit allowlist plus
  `ALLOWED_URI_REGEXP` is required (criterion 13).
- **`localStorage` is attacker-writable** by anything that ever ran in the origin, and
  user-editable regardless. Treat it as untrusted input, not as our own state.
- **Bundle size**: `highlight.js` full-bundle is large and ships inside the Python wheel.

## Dependencies
- `docs-md/dashboard-file-preview-hld.md` §5 (component design), §2.7–§2.9 (the attacks these
  components must stop); ADR-0011 D2.
- `T-Bk4Hs7-preview-backend` for the live API — not a blocker for writing the components, but
  required for integration and for any test that hits real responses.
- `T-Sh6Rz3-origin-host-csrf` for the verified SPA CSP under which the iframe must render.
- Existing `ui/src/components/FileBrowser.tsx` viewer pane and `ui/src/api.ts` fetch helpers.

## Pseudocode / Algorithm
```text
FUNCTION selectView(content, path):
  IF content.kind == "binary":  RETURN BinaryBanner
  IF content.kind == "image":   RETURN ImageView(content.data_uri, content.size)
  IF content.kind == "markup":  RETURN Toggle(default="source",
                                      source=CodeView(content.text, lang="xml"),
                                      preview=HtmlPreview(fetch("/api/files/html", path)))
  IF is_markdown_ext(path):     RETURN Toggle(default="preview",
                                      preview=MarkdownView(content.text, dir_of(path)),
                                      source=CodeView(content.text, lang="markdown"))
  RETURN CodeView(content.text, lang=guess_from_ext(path))

FUNCTION renderCode(text, language):
  IF byte_length(text) > HIGHLIGHT_MAX_BYTES:
     RETURN <pre>{text}</pre> + note("highlighting skipped: file over cap")   # plain text node
  IF language NOT IN REGISTERED_LANGUAGES:
     RETURN <pre>{text}</pre>                                                 # plain text node
  html = hljs.highlight(text, { language, ignoreIllegals: true }).value
  # SAFETY: hljs escapes its input, so its OUTPUT is safe for dangerouslySetInnerHTML.
  # Never concatenate raw file text into this string ourselves.
  RETURN <code dangerouslySetInnerHTML={{ __html: html }} />

FUNCTION renderMarkdown(text, base_dir):
  raw   = marked.parse(text)
  clean = DOMPurify.sanitize(raw, { ALLOWED_TAGS: [...], ALLOWED_ATTR: [...],
                                    ALLOWED_URI_REGEXP: /^(?:https?:|mailto:|[^a-z]|[a-z+.\-]+(?:[^a-z+.\-:]|$))/i })
  node  = parse_to_dom(clean)
  FOR img IN node.images WHERE is_relative(img.src) AND count < MARKDOWN_MAX_IMAGES:
     res = GET /api/files/content?path=join(base_dir, img.src)      # server's guard, reused
     IF res.ok AND res.data_uri:  img.src = res.data_uri
     ELSE:                        replace(img, broken_image_note()) # NEVER leave a raw URL
  FOR a IN node.anchors WHERE is_external(a.href):
     a.target = "_blank"; a.rel = "noopener noreferrer"
  FOR block IN node.code_blocks:  block.innerHTML = renderCode(block.text, block.lang)
  RETURN node

FUNCTION readTypography():
  raw = localStorage.getItem(TYPOGRAPHY_KEY)
  # localStorage is UNTRUSTED input: attacker-writable by anything that ran in this origin,
  # and user-editable regardless. Parse and clamp; never interpolate into a style attribute.
  TRY:    stored = JSON.parse(raw) IF raw ELSE {}
  EXCEPT: stored = {}
  RETURN { family:  FAMILY_MAP[stored.family] ?? FAMILY_MAP.system,   # fixed lookup, not passthrough
           size:    clamp(to_number(stored.size),        SIZE_MIN,   SIZE_MAX,   SIZE_DEFAULT),
           line:    clamp(to_number(stored.line),        LINE_MIN,   LINE_MAX,   LINE_DEFAULT),
           spacing: clamp(to_number(stored.spacing),     SPACE_MIN,  SPACE_MAX,  SPACE_DEFAULT) }
  # Applied as CSS custom properties on the viewer container — and deliberately NOT on
  # HtmlPreview, whose content brings its own styling.
```

## Schemas / Interface Notes
- Interface / API consumed (must mirror HLD §4 exactly):
  ```ts
  type FileKind = "text" | "image" | "markup" | "binary";
  interface FileContent { /* existing fields */ is_binary: boolean; text: string | null;
                          truncated: boolean;
                          kind: FileKind; mime: string | null; data_uri: string | null; }
  interface DroppedRef { url: string;
                         reason: "external" | "outside-root" | "not-found" | "too-large"
                               | "budget-exhausted" | "unsupported-scheme" | "depth-exceeded"; }
  interface HtmlPreview { path: string; root: string; html: string;
                          inlined: number; dropped: DroppedRef[]; scripts_removed: number;
                          truncated: boolean; budget_bytes: number; budget_used: number; }
  // GET /api/files/content?path=&root=  -> FileContent
  // GET /api/files/html?path=&root=     -> HtmlPreview   (403 / 404 / 400 non-markup)
  ```
- Frontend constants (named, no magic literals): `HIGHLIGHT_MAX_BYTES = 256_000`,
  `MARKDOWN_MAX_IMAGES = 50`, `TYPOGRAPHY_KEY` (one namespaced `localStorage` key),
  `REGISTERED_LANGUAGES`, `FAMILY_MAP`, and the clamp bounds.
- Spec / data schema (JSON/YAML): `N/A` — no workflow-spec change.
- Triggers / events (cron/event): `N/A`.
- Artifacts (inputs/outputs by path): reads workspace files via the API only; never constructs a
  filesystem path itself (criterion 17).

## Handoff Boundary
- Upstream: `T-Bk4Hs7-preview-backend` (API shapes — coded against the contract ahead of it
  landing) and `T-Sh6Rz3-origin-host-csrf` (the SPA CSP the iframe must render under).
- Downstream: none inside the epic; this is the user-facing surface.
- Not owned: **any** Python file, `install.sh`, `tests/`.

## Artifacts
- Docs/comments: `meta/tickets/E-Fp7Qv2-dashboard-file-preview/T-Fv9Ld2-preview-frontend/`
- Code (planned): `ui/package.json`, `ui/src/types.ts`, `ui/src/api.ts`, `ui/src/format.ts`,
  `ui/src/styles.css`, `ui/src/components/FileBrowser.tsx`,
  `ui/src/components/viewer/{CodeView,MarkdownView,HtmlPreview,ImageView,TypographyControls}.tsx`
- Tests (planned): `ui/src/test/*.test.tsx`

## Comments
- By: architect · Role: architect · Date: 2026-07-30 · Comment: Ticket opened for work already in
  progress. Three criteria are load-bearing rather than cosmetic. (20) The comment at the iframe
  explaining that `allow-scripts` + `allow-same-origin` together defeat the sandbox **is** the
  mitigation for the most likely future regression in this epic — the realistic break is someone
  finding interactive content broken and adding one flag, then the other, in a diff that looks
  harmless in review. (23) Dropped URLs must render as text, never links: a provenance panel that
  linkifies the refs the sanitizer just dropped would hand back the exact egress channel the
  design closed. (27) `localStorage` is untrusted input, so typography values are parsed and
  clamped rather than interpolated — a stored `12px; background: url(https://evil.com/)` is a CSS
  injection with an egress channel. Also note the deliberate asymmetry between the two render
  paths: `MarkdownView` renders in the **dashboard origin** with DOMPurify as the actual boundary,
  while `HtmlPreview` renders in an **opaque origin** where the server's sanitizer is only layer 2
  — which is why markdown defaults to Preview and `markup` defaults to Source (HLD §5.1).
- By: architect · Role: architect · Date: 2026-07-30 · Comment: **Done** — `npm run typecheck`
  clean, **79 tests across 8 files**, `npm run build` succeeds, no Python file touched. All three
  load-bearing criteria shipped: AC-20's call-site comment about the two sandbox flags, AC-23's
  dropped-refs-as-text (no anchor, no `src`), and AC-27's clamped `localStorage` read-back.
  **AC-24 resolved as NOT IMPLEMENTED** — the enable-scripts toggle was deliberately omitted per
  the contract's own "if you are not confident, omit it" branch, and the subsequent audit
  vindicated that: finding H1 achieved a live one-click egress channel with scripting **fully
  disabled**, so a scripting switch would have widened a surface not yet fully understood. Two
  points of contrast worth preserving. (1) This task's DOMPurify config is a genuine **keep-only
  allowlist** (27 tags, 7 attrs, scheme-restricted `ALLOWED_URI_REGEXP`), whereas the Python
  sanitizer shipped as a **blocklist** — and H1/M1 came out of the blocklist side, which is the
  clearest available argument for the allowlist follow-up. (2) The `data-ao-pending` attribute is a
  small choice worth remembering: it carries a relative image ref through sanitization without
  becoming a real `src`, so the browser never fires a doomed same-origin request before async
  resolution substitutes the `data:` URI. Contract fidelity held with **zero drift** despite being
  written before the backend landed — all seven `DroppedRef.reason` values matched.
