# TASK: T-Bk4Hs7-preview-backend

## Metadata
- Task ID: `T-Bk4Hs7-preview-backend`
- Epic ID: `E-Fp7Qv2-dashboard-file-preview`
- Owner: `claude` (backend agent)
- Created: `2026-07-30`
- Last Updated: `2026-07-30`
- Status: `Done`
- Estimate: `< 3 days`

## Requirements Mapping
- Epic FR-1 (classification), FR-2 (SVG is markup), FR-3 (`/api/files/html` + parse-don't-regex
  sanitizer + escape-on-serialize), FR-4 (single URL pipeline, `data:`-only), FR-5 (reuse
  `FileBrowser.resolve`'s containment guard), FR-6 (budgets, drop-whole-never-truncate).
- Epic NFR-1 (`[ui]` stays optional, no new dependency), NFR-2 (a test per mitigation),
  NFR-3 (no magic literals), NFR-4 (existing `tests/ui/*` keep passing).
- CONTRACT sections 1A.1, 1A.2, and the 1A.4 test groups that pin them.
- Design: `docs-md/dashboard-file-preview-hld.md` §2.1–§2.6, §4.1–§4.2 · ADR-0011 D1, D2.

## Description
Extend the dashboard's file-content API with content classification, and add a new endpoint
that returns **sanitized, self-contained** HTML for `.html`/`.htm`/`.svg`/`.xhtml` files —
safe to drop into an `iframe srcdoc` with no live network references of any kind.

Two things make this task security-critical rather than cosmetic. First, the files being
rendered are **agent-written and third-party-imported**, so their bytes are attacker-controlled.
Second, the dashboard origin has no authentication and `POST /api/runs` is an
RCE-and-spend primitive, so any script that reaches that origin owns the workspace. This task
owns invariants **I2** (no egress channel) and **I3** (server-side sanitization is
authoritative) from the epic; **I1** (opaque origin) is the frontend's iframe.

Files owned:
- `src/agent_orchestrator/ui/files.py` (extend)
- `src/agent_orchestrator/ui/htmlpreview.py` (new)
- `src/agent_orchestrator/ui/service.py` (extend — pass-through methods only, preserving the
  framework-free service / thin-adapter split from ADR-0010 D5)
- `src/agent_orchestrator/ui/app.py` (extend — routes only; **middleware belongs to
  `T-Sh6Rz3`**, and this is the file both Python tasks touch)
- `tests/ui/test_files.py` (extend), `tests/ui/test_htmlpreview.py` (new),
  `tests/ui/test_api_integration.py` (extend)

Explicitly **not** owned: `ui/security.py` and the middleware wiring (`T-Sh6Rz3`), anything
under `ui/` (`T-Fv9Ld2`), `install.sh` (`E-Bi5Nw8`).

## Acceptance Criteria

**Classification (1A.1)**
1. Given a `.png` whose bytes are a real PNG, when `GET /api/files/content`, then
   `kind == "image"`, `mime == "image/png"` (from `IMAGE_MIME_BY_EXT`, not sniffed), and
   `data_uri` is `data:image/png;base64,…`.
2. Given a `.png` whose bytes are actually HTML, then `kind != "image"` — the magic-byte check
   is mandatory and extension alone never classifies an image.
3. Given an image larger than `IMAGE_INLINE_MAX_BYTES = 5_000_000`, then `kind == "image"` and
   `data_uri is None` (size still reported so the UI can explain itself).
4. Given `.svg`, `.html`, `.htm`, `.xhtml`, then `kind == "markup"` — **an SVG is never
   classified `image`**, even one that is a valid image, because SVG can carry `<script>`.
5. Given a file with a NUL byte in the sniff block that is not a recognized image, then
   `kind == "binary"`; everything else is `kind == "text"`.
6. `FileContent` additions are **additive**: `is_binary`, `text`, and `truncated` keep their
   existing meaning and every pre-existing `tests/ui/*` assertion still passes.
7. `IMAGE_INLINE_MAX_BYTES`, `IMAGE_MIME_BY_EXT`, `IMAGE_MAGIC_PREFIXES`, and
   `MARKUP_EXTENSIONS` exist as named module constants — no inline literals at use sites.

**Sanitizer (1A.2)**
8. Sanitization is implemented with stdlib `html.parser.HTMLParser` re-serializing from an
   **allowlist**. Pass/fail: a reviewer can point at the allowlist and at the serializer; there
   is no regex that strips tags. No new third-party dependency is added.
9. Given `<scr<script>ipt>alert(1)</script>`, an unquoted-attribute payload, and
   `<!--[if IE]><script>…</script><![endif]-->`, then no `<script>` and no executable markup
   appears in the output — nested-tag reassembly and comment smuggling both fail.
10. Given `<img src=x onerror=alert(1)>` and `<body onload=alert(1)>`, then every `on*`
    attribute is absent from the output (case-insensitive, and after whitespace/control-char
    stripping).
11. Given `script`, `iframe`, `frame`, `frameset`, `object`, `embed`, `applet`, `base`, `form`,
    or `noscript`, then the element **and its entire subtree** are absent from the output.
    Comments are absent. `meta` is absent unless it is a harmless `charset`.
12. `scripts_removed` reports the count of dropped script elements — removals are counted, not
    silently swallowed.
13. Given a file containing `"><img src=x onerror=alert(1)>` in **a text node** and separately
    in **an attribute value**, then it survives re-serialization **escaped**
    (`html.escape(quote=True)`, attribute values double-quoted) and not as live markup. This is
    the bug class in `meta/learnings.md` ("template render escaping/containment") — a
    dedicated test for both positions is required.
14. Given `<a href="https://evil.com/x">text</a>`, then `href` is **absent**, the link text is
    preserved, the truncated original is in `data-ao-href`, and **no `DroppedRef` is recorded**
    (anchors are neutralized, not failed assets).

**URL pipeline (1A.2)**
15. `java\tscript:alert(1)`, `java\nscript:…`, and leading-control-char variants are dropped —
    whitespace/control stripping happens **before** scheme detection.
16. `http://evil.com/x.png` → dropped, reason `external`. `//evil.com/x.png`,
    `\\evil.com\x.png`, `file:///etc/passwd`, `javascript:…`, `vbscript:…`, `blob:…` → dropped,
    reason `unsupported-scheme`. **No non-`data:` scheme appears anywhere in the output HTML** —
    assertable by scanning the returned string.
17. A pre-existing `data:` URI is kept only when its MIME is in the image/font allowlist.
18. `src`, `href` (on `link` only), `poster`, and `srcset` are all processed by the same
    pipeline — each `srcset` candidate individually. `srcdoc`, `formaction`, `xlink:href`,
    `action`, `background`, `dynsrc`, `lowsrc`, `ping`, and `http-equiv` are dropped as
    attributes.

**Inlining, containment, budgets (1A.2)**
19. An in-root relative image, resolved **against the HTML file's own directory**, is inlined as
    a `data:` URI and `inlined` increments.
20. `../../../secret.png` → dropped `outside-root`; `%2e%2e%2fsecret.png` → dropped
    `outside-root` (percent-decoded once before resolving); a **symlink pointing outside the
    root** → dropped `outside-root`; a ref containing a NUL byte after decoding → dropped.
21. Containment uses the **same guard as `FileBrowser.resolve`** (resolve-then-prefix-check).
    Pass/fail: there is exactly one containment implementation in the codebase, shared — not a
    second resolver in `htmlpreview.py`.
22. A missing in-root ref → dropped `not-found`. An asset over `HTML_ASSET_MAX_BYTES` → dropped
    `too-large`. Once cumulative inlining would exceed `HTML_INLINE_BUDGET_BYTES` → dropped
    `budget-exhausted`. In every case the asset is **dropped whole** — no test may observe a
    partially inlined asset.
23. `DroppedRef.url` is truncated to 200 chars.
24. `budget_bytes` / `budget_used` are reported, and `truncated` reflects the source exceeding
    `MAX_READ_BYTES`.

**CSS (1A.2)**
25. `url(...)` in both `<style>` blocks and inline `style` attributes is rewritten by the same
    URL pipeline.
26. `<link rel="stylesheet">` with an in-root `href` has its CSS inlined into a `<style>` block
    and processed; every other `rel` (`prefetch`, `preload`, `dns-prefetch`, …) is dropped.
27. `expression(`, `behavior:`, `-moz-binding`, and `@import` inside a `style` **attribute** are
    stripped.
28. `@import` in a stylesheet resolves in-root only, to `HTML_MAX_IMPORT_DEPTH = 3`
    (`depth-exceeded` beyond), and **an import cycle terminates** via a visited set — a
    self-importing and a mutually-importing pair of sheets both return rather than hang.

**Route (1A.2)**
29. `PathNotAllowedError → 403`, `PathNotFoundError → 404`, a non-markup file → **400**.
30. Traversal on **both** `/api/files/content` and `/api/files/html`
    (`../../etc/passwd`, `%2e%2e%2f`, absolute `/etc/passwd`, symlink-to-outside) → **403** for
    every case on both endpoints.

**Gates**
31. `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .` all run and their **real**
    output reported in `STATUS.md` (test counts before/after, so the delta is auditable and
    no-regression is demonstrated rather than asserted). Nothing in core imports fastapi at
    module scope.

## Risks
- **A hand-written sanitizer will have bugs.** Accepted by design — the frontend's opaque
  origin (`T-Fv9Ld2`, FR-11) means a miss is a rendering bug, not a compromise. Do not treat
  the sanitizer as the only boundary, and do not weaken the iframe on the strength of it.
- **`src/agent_orchestrator/ui/app.py` is also being extended by `T-Sh6Rz3`** (middleware).
  Add routes only; keep the diff narrow and expect to reconcile.
- **The frontend is coding against these field names right now.** `kind`, `mime`, `data_uri`,
  and the exact `DroppedRef.reason` string set are a contract with `T-Fv9Ld2` — a rename is a
  cross-task break, so any deviation must be flagged in `STATUS.md` immediately, not at merge.
- **Percent-decoding is a double-decode hazard.** Decode **once**; decoding twice re-introduces
  a bypass (`%252e%252e%252f` becoming `../`).
- **Resolving refs against the wrong base.** Against the root instead of the HTML file's own
  directory either breaks valid documents or widens the reachable set.
- **Reason-string drift** between the sanitizer and the dataclass docstring makes the frontend's
  grouped-by-reason panel silently incomplete.

## Dependencies
- `docs-md/dashboard-file-preview-hld.md` §4.1–§4.2 (authoritative shapes/constants) and
  ADR-0011 D1/D2 (why inline+sandbox, why parse-don't-regex).
- Existing `FileBrowser.resolve` containment guard (E-Ui7Kq2) — **reuse, do not reimplement**.
- Existing `MAX_READ_BYTES` (1 MB) source read bound.
- Not blocked by `T-Sh6Rz3` or `T-Fv9Ld2`; all three run concurrently.

## Pseudocode / Algorithm
```text
FUNCTION classify(path, head_bytes, size):
  ext = lower(extension(path))
  IF ext IN IMAGE_MIME_BY_EXT AND head_bytes STARTS WITH ANY IMAGE_MAGIC_PREFIXES[ext]:
      mime = IMAGE_MIME_BY_EXT[ext]                  # allowlist ONLY, never sniffed
      data_uri = base64_data_uri(mime, path) IF size <= IMAGE_INLINE_MAX_BYTES ELSE None
      RETURN kind="image", mime, data_uri
  IF ext IN MARKUP_EXTENSIONS:  RETURN kind="markup", mime=None, data_uri=None   # SVG lands here
  IF b"\x00" IN head_bytes:     RETURN kind="binary", None, None
  RETURN kind="text", None, None

FUNCTION build_preview(path, root):
  root_dir, abs_path = file_browser.resolve(root, path)      # raises PathNotAllowed/NotFound
  IF extension(abs_path) NOT IN MARKUP_EXTENSIONS: RAISE NotMarkupError        # -> 400
  source, truncated = read_bounded(abs_path, MAX_READ_BYTES)
  ctx = Ctx(base_dir=parent(abs_path), root=root_dir,
            budget_used=0, inlined=0, dropped=[], scripts_removed=0)
  tree_events = HTMLParser.parse(source)                     # stdlib; tolerant of malformed input
  out = []
  FOR event IN tree_events:
    IF event IS comment:                       CONTINUE                    # smuggling vector
    IF event IS start_tag:
       IF tag IN DROP_WITH_SUBTREE:            skip_until_matching_close(); 
                                               IF tag == "script": ctx.scripts_removed += 1
                                               CONTINUE
       IF tag == "meta" AND NOT is_charset_only(attrs):      CONTINUE
       IF tag == "link":
          IF rel != "stylesheet":              CONTINUE
          css = read_in_root_or_drop(attrs.href, ctx)
          IF css IS None:                      CONTINUE
          out.APPEND("<style>" + process_css(css, ctx, depth=0) + "</style>")
          CONTINUE
       IF tag NOT IN ALLOWED_ELEMENTS:         CONTINUE      # allowlist: unknown => omitted
       kept = {}
       FOR (name, value) IN attrs:
          n = lower(strip_ws_and_controls(name))
          IF n STARTS WITH "on":               CONTINUE
          IF n IN DROPPED_ATTRS:               CONTINUE      # srcdoc, formaction, xlink:href,
                                                             # action, background, dynsrc,
                                                             # lowsrc, ping, http-equiv
          IF tag == "a" AND n == "href":
             kept["data-ao-href"] = truncate(value, 200)     # neutralize, do NOT record dropped
             CONTINUE
          IF n == "style":
             kept["style"] = strip_css_hazards(process_css_urls(value, ctx))
             CONTINUE                                        # expression(, behavior:,
                                                             # -moz-binding, @import
          IF n IN URL_ATTRS:                                 # src, href(link), poster, srcset
             new = process_url_list(value, ctx) IF n == "srcset" ELSE process_url(value, ctx)
             IF new IS None:                   CONTINUE      # dropped; reason recorded in ctx
             kept[n] = new
             CONTINUE
          IF n IN ALLOWED_ATTRS[tag]:          kept[n] = value
       out.APPEND(serialize_start(tag, kept))                # every value html.escape(quote=True),
                                                             # always double-quoted
    IF event IS text:
       out.APPEND(html.escape(event.text, quote=True))       # NEVER raw
    IF event IS end_tag AND tag IN ALLOWED_ELEMENTS:
       out.APPEND(serialize_end(tag))
  RETURN HtmlPreview(path, root, html="".join(out), inlined=ctx.inlined,
                     dropped=ctx.dropped, scripts_removed=ctx.scripts_removed,
                     truncated=truncated, budget_bytes=HTML_INLINE_BUDGET_BYTES,
                     budget_used=ctx.budget_used)

FUNCTION process_url(raw, ctx):                              # THE single URL pipeline
  u = strip_ws_and_controls(raw)                             # BEFORE scheme detection:
                                                             # "java\tscript:" is a real bypass
  scheme = detect_scheme(u)
  IF scheme == "data":
     RETURN u IF data_mime(u) IN DATA_MIME_ALLOWLIST ELSE drop(ctx, raw, "unsupported-scheme")
  IF scheme IN ("http", "https"):  RETURN drop(ctx, raw, "external")
  IF scheme IS NOT None:           RETURN drop(ctx, raw, "unsupported-scheme")
  IF u STARTS WITH "//" OR "\\":   RETURN drop(ctx, raw, "unsupported-scheme")
  u = percent_decode_once(u)
  IF "\x00" IN u:                  RETURN drop(ctx, raw, "unsupported-scheme")
  target = (ctx.base_dir / u)                                # HTML file's OWN directory
  TRY:    _, abs_target = contain(target, ctx.root)          # SAME guard as FileBrowser.resolve
  EXCEPT PathNotAllowed:           RETURN drop(ctx, raw, "outside-root")
  IF NOT exists(abs_target):       RETURN drop(ctx, raw, "not-found")
  n = size(abs_target)
  IF n > HTML_ASSET_MAX_BYTES:     RETURN drop(ctx, raw, "too-large")
  IF ctx.budget_used + n > HTML_INLINE_BUDGET_BYTES:
                                   RETURN drop(ctx, raw, "budget-exhausted")
  ctx.budget_used += n; ctx.inlined += 1
  RETURN base64_data_uri(mime_from_ext(abs_target), abs_target)   # whole asset or none

FUNCTION process_css(css, ctx, depth, visited=SET()):
  css = strip_css_hazards(css)                               # expression(, behavior:, -moz-binding
  FOR each url(...) IN css:  rewrite via process_url(...) OR remove the declaration
  FOR each @import ref IN css:
     IF depth >= HTML_MAX_IMPORT_DEPTH: drop(ctx, ref, "depth-exceeded"); CONTINUE
     resolved = contain_in_root(ref, ctx)  OR  drop(...) AND CONTINUE
     IF resolved IN visited: CONTINUE                        # cycle terminates
     visited.ADD(resolved)
     inline process_css(read(resolved), ctx, depth + 1, visited)
  RETURN css
```

## Schemas / Interface Notes
- Interface / API (HTTP):
  ```text
  GET /api/files/content?path=&root=   -> FileContent  (+ kind, mime, data_uri)
  GET /api/files/html?path=&root=      -> HtmlPreview
     403 PathNotAllowedError · 404 PathNotFoundError · 400 non-markup file
  ```
- Data schema (Python, `ui/htmlpreview.py`):
  ```python
  @dataclass(frozen=True)
  class DroppedRef:
      url: str      # original ref, TRUNCATED to 200 chars
      reason: str   # "external" | "outside-root" | "not-found" | "too-large"
                    # | "budget-exhausted" | "unsupported-scheme" | "depth-exceeded"

  @dataclass(frozen=True)
  class HtmlPreview:
      path: str; root: str; html: str
      inlined: int; dropped: list[DroppedRef]; scripts_removed: int
      truncated: bool; budget_bytes: int; budget_used: int
  ```
  Additive on `FileContent`: `kind: str`, `mime: str | None`, `data_uri: str | None`.
- Constants: `IMAGE_INLINE_MAX_BYTES = 5_000_000`, `IMAGE_MIME_BY_EXT`,
  `IMAGE_MAGIC_PREFIXES`, `MARKUP_EXTENSIONS`, `HTML_INLINE_BUDGET_BYTES = 10_000_000`,
  `HTML_ASSET_MAX_BYTES = 5_000_000`, `HTML_MAX_IMPORT_DEPTH = 3`, existing `MAX_READ_BYTES`.
- Triggers / events (cron/event): `N/A`.
- Artifacts (inputs/outputs by path): reads workspace files under configured dashboard roots;
  writes none. **No network IO in this module at all** — that structural absence is what makes
  server-side SSRF impossible (HLD §2.3).

## Handoff Boundary
- Upstream: the shared implementation contract + `docs-md/dashboard-file-preview-hld.md` §4;
  existing `FileBrowser.resolve` guard.
- Downstream: `T-Fv9Ld2-preview-frontend` consumes `kind`/`mime`/`data_uri` and the
  `HtmlPreview` shape — **field names and `reason` values are a frozen contract**; flag any
  deviation in `STATUS.md` the moment it happens.
- Sibling: `T-Sh6Rz3-origin-host-csrf` owns `ui/security.py` and all middleware wiring in
  `app.py`. Do not add middleware here.
- Not owned: anything under `ui/`, `install.sh`.

## Artifacts
- Docs/comments: `meta/tickets/E-Fp7Qv2-dashboard-file-preview/T-Bk4Hs7-preview-backend/`
- Code (planned): `src/agent_orchestrator/ui/files.py`,
  `src/agent_orchestrator/ui/htmlpreview.py`, `src/agent_orchestrator/ui/service.py`,
  `src/agent_orchestrator/ui/app.py`
- Tests (planned): `tests/ui/test_files.py`, `tests/ui/test_htmlpreview.py`,
  `tests/ui/test_api_integration.py`

## Comments
- By: architect · Role: architect · Date: 2026-07-30 · Comment: Ticket opened for work already
  in progress. Two acceptance criteria are worth reading as design constraints rather than
  checkboxes: (13) escaping on re-serialize in **both** a text node and an attribute value —
  this repo has already shipped that bug once and `meta/learnings.md` records it; and (21) there
  must be exactly **one** containment implementation, shared with `FileBrowser.resolve`, because
  two path guards means two chances to be wrong with no guarantee they agree, and a fix to one
  silently leaves the other exploitable. Criterion (16) is deliberately phrased as a property
  over the whole output string — "no non-`data:` scheme appears anywhere" — since that is the
  epic's I2 invariant stated in a form a test can check directly.
- By: architect · Role: architect · Date: 2026-07-30 · Comment: **Done.** All acceptance criteria
  met, with two important qualifications recorded in `STATUS.md` and HLD §10. (1) **AC-8 was met in
  letter but not in spirit:** the sanitizer parses rather than regexes (the criterion's actual
  requirement, and it held — `<scr<script>ipt>` failed against `html.parser` for the same tokenizer
  reason it fails against browsers), but the element/attribute policy shipped as a **blocklist plus
  special cases** rather than the keep-only allowlist the criterion and HLD §4.2 describe. Audit
  findings H1 (SVG SMIL `<animate>` retargeting `href` at render time — real synthetic click, real
  captured request) and M1 (CSS `image-set()` bypassing the URL pipeline) are the direct
  consequence: both were simply *not on a list*. Both fixed and re-verified; L1–L3 likewise.
  Converting to a true allowlist is the epic's highest-value follow-up. (2) **The `<a href>`
  rationale this ticket was written against was false.** "The sandbox blocks navigation anyway" is
  not true — a sandboxed iframe can always navigate **itself**, and no CSP directive stops
  navigation — so AC-14's `href` stripping is a **genuine security control**, not the cosmetic
  de-cluttering the original text implied. Corrected in HLD §2.2.1/§4.2 and at the
  `htmlpreview.py` call site. Worth noting AC-16's phrasing ("no non-`data:` scheme appears
  anywhere in the output") did its job: it is the criterion H1 violated, which is why the audit
  could state unambiguously that an invariant had been breached rather than debating severity.
