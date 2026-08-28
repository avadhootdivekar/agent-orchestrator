# STATUS

- ID: `T-Sh6Rz3-origin-host-csrf`
- Updated At: `2026-07-30`
- State: `Done`
- Owner: `claude` (backend agent)

## This update
- By: architect · Role: architect · Date: 2026-07-30 · Comment: Task ticket scaffolded while
  implementation is underway. Scope: CONTRACT 1A.3 (`ui/security.py` — Host allowlist → 421,
  Origin check on mutating methods → 403, JSON content-type → 415, hardening response headers,
  SPA-document CSP, `ao ui` bound-host wiring) + its 1A.4 test group. Tracked separately from
  `T-Bk4Hs7-preview-backend` because it closes a **pre-existing** critical gap (CSRF against
  `POST /api/runs`; full read+write via DNS rebinding) that stands on its own merits and should
  be independently reviewable and revertable. Rationale: ADR-0011 D3; controls table:
  `docs-md/dashboard-file-preview-hld.md` §2.10.
- By: architect · Role: architect · Date: 2026-07-30 · Comment: Task **Done**. All five controls
  landed; **AC-14's CSP verification is complete and the four verification fields below are filled
  from observation** (real headless Chrome, with a negative control), which was this task's one
  outstanding gate. The drafted policy shipped unchanged. This task also owns audit finding L4
  (`Origin`/`Host` userinfo normalization). One scope decision made during implementation is
  flagged below and is worth a reviewer's eye: the Content-Type gate was narrowed to mutating
  requests **that carry a body**, which the audit then independently confirmed is safe.

## Evidence
- **Gates (post-fix, post-audit):**
  - `uv run pytest -q` → **1793 passed, 7 skipped** (pre-epic baseline **1651 passed, 7 skipped**).
    This middleware runs on **every** request in the suite, so a clean full run is the primary
    no-regression signal for this task — it was the highest regression surface in the epic.
  - `uv run ruff check .` → clean.
  - `uv run ruff format --check .` → clean except `tests/test_e2e_builtin_routed_runner.py`,
    confirmed failing **identically at HEAD** and untouched by this task.
  - `uv run mypy src` → clean except the 4 pre-existing `_version.py` errors, confirmed failing
    identically at HEAD.
- Code shipped: new `src/agent_orchestrator/ui/security.py` (`SecurityMiddleware`,
  `resolve_allowed_hosts`, `SPA_CSP`, `DEFAULT_ALLOWED_HOSTS`, `AO_UI_ALLOWED_HOSTS_ENV`,
  `DISABLE_HOST_CHECK_SENTINEL`, `MUTATING_METHODS` — all named constants, AC-19),
  `ui/app.py` (middleware wiring + SPA-document-only CSP via `_is_spa_document`), `cli.py`
  (`ui_cmd` bound-host pass-through).
- Tests shipped: `tests/ui/test_security.py` (new), `tests/ui/conftest.py` (the **single**
  allowed-hosts accommodation, AC-17), `tests/ui/test_api_integration.py` (extended).

### CSP verification record (AC-14) — COMPLETE
- **Final policy shipped:**
  ```text
  default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline';
  font-src 'self' data:; connect-src 'self'; frame-src 'self' data:;
  object-src 'none'; base-uri 'none'; form-action 'none'
  ```
  Live as `security.py::SPA_CSP`. **No deviation from the HLD §4.3 starting point** — verification
  confirmed the drafted policy rather than forcing a change.
- **Browsers tested:** real **headless Chrome** (`google-chrome-stable --headless=new`).
  Firefox and WebKit were **not** tested — recorded as a known-open limitation (HLD §9.2) rather
  than glossed as "verified in browsers."
- **Demonstrated that a sandboxed `srcdoc` iframe with inlined `data:` images renders:** yes.
  Served this exact header string from a plain `http.server` response, loaded it in headless
  Chrome, screenshotted. The page embedded a `sandbox=""` iframe (no `allow-scripts`, no
  `allow-same-origin`) whose `srcDoc` contained `<img src="data:image/png;base64,…">` — a solid-red
  2×2 PNG. **Pixel-sampling the screenshot at the image's location returned the exact red RGB
  value**, i.e. the frame rendered and its inlined `data:` image loaded under this precise policy.
- **Demonstrated that the policy still blocks (i.e. it discriminates):** yes — via a **negative
  control**. Re-ran with `img-src 'self'` (`data:` removed), everything else identical: the same
  pixel location came back as the page's background color instead. That isolates `data:` in
  `img-src` as the directive doing the work and rules out a methodology false-positive. `frame-src
  'self' data:` covers the sandboxed `srcDoc` frame itself; `connect-src 'self'` with no wildcard
  host anywhere is what upholds I2.
- **Independent second verification:** the *frame-level* `PREVIEW_CSP` (owned by `T-Fv9Ld2`) was
  verified the same way — headless Chrome, `sandbox=""` iframe, policy in a `<meta http-equiv>`
  inside `srcdoc`; an inlined `<style>` block and a `data:image/png` both rendered, confirming
  `style-src 'unsafe-inline' data:` and `img-src data:` each do real work.
- **Regression guard, not a substitute:**
  `tests/ui/test_security.py::TestSecurityHeaders::test_spa_document_response_carries_the_csp`
  pins the header value. It cannot drive a browser, so it guards against silent edits only. The
  narrative is preserved **at the constant** so it cannot drift from the value it justifies.
- **Scope limit, which turned out to matter:** this verification says nothing about
  **navigation**, because **no CSP directive governs navigation**. That is precisely the gap
  finding H1 exploited (HLD §2.2.1, §10.1).

## Audit finding owned by this task (fixed and re-verified)
- **L4 (Low).** `Origin`/`Host` normalization silently stripped userinfo and ignored scheme:
  `urlsplit("http://evil.com@localhost").hostname` returns `"localhost"`, normalizing straight
  past a spoofed prefix — i.e. accepting exactly the input a spoofing attempt would send. **Not
  browser-reachable** (browsers never construct an `Origin` with userinfo), so there was no live
  exposure. **Fixed** by failing closed on any `@` in either header, applied to both
  `_normalize_host` and `_normalize_origin`, each with the reasoning at the docstring.

**Attacked and found sound:** the audit verified with a **real cross-origin form POST** that
browsers **cannot** produce a bodyless cross-origin state-changing request without an `Origin`
header. That is the evidence making the Content-Type gate's bodyless exemption (judgment call 2
below) a safe scoping decision rather than a bypass.

## Judgment calls flagged
1. **Origin derived from the allowed-host set** by hostname comparison after normalization
   (bracket/port stripped, lowercased), with `Host` and `Origin` parsed differently on purpose:
   `Host` is bare (`host[:port]`) and needs a `//` prefix for `urlsplit` to see a netloc, whereas
   `Origin` already carries its scheme and would be **corrupted** by that same prefix
   (`"//http://host"` parses as host `"http"`). Two functions, each documented, rather than one
   that silently mishandles one input shape.
2. **Content-Type gate narrowed to mutating requests that carry a body** — the notable deviation
   from a literal AC-10 reading. `POST /api/runs/{id}/resume` and `DELETE /api/runs/{id}` are real,
   already-tested dashboard behaviour with no payload, and requiring a JSON content type on a
   bodyless request would have broken them. Safe because the `Origin` check still covers those
   requests regardless of body, **and** the audit independently confirmed browsers cannot
   construct the exempted shape cross-origin. Reasoning is at
   `_requires_json_content_type`'s docstring. Flagged because it narrows a stated control and
   deserves review on its own terms.
3. **`DEFAULT_ALLOWED_HOSTS` covers bracketed IPv6 only.** A bare unbracketed `::1` Host value is
   ambiguous with `host:port` syntax and `urlsplit` cannot recover a hostname from it, so it
   normalizes to `None` and is **rejected**. Fail-closed and therefore safe, but it means a client
   sending `Host: ::1` unbracketed is refused rather than accepted — documented at the constant in
   case that ever needs to change.
4. **The absent-`Origin` allowance was kept and given its own test plus a call-site comment**
   (AC-7), exactly as the ticket required, so a future "hardening" pass cannot remove it without a
   test going red and a comment explaining why not to.

## Risks / Blockers
- None; task closed. Original risks and their outcome:
  - **Highest regression surface in the epic** — full suite clean at 1793/7.
  - **Fixture-driven erosion of the `Host` allowlist** — avoided: the accommodation lives in one
    place in `conftest.py` and is paired with tests that construct an app **without** it and assert
    a bad `Host` is still rejected (AC-17).
  - **The absent-`Origin` allowance** — kept deliberately, test + comment in place.
  - **The CSP was unverified** — now verified with a negative control; drafted policy unchanged.
  - **`421` unusual** — propagates intact.
  - **IPv6 host forms** — covered, with the bare-`::1` limitation documented (judgment call 3).
- Carried forward: browser coverage is Chrome-only (HLD §9.2).

## Next actions
- None for this task. Epic-level follow-up if picked up: re-run both CSP verifications under
  Firefox and WebKit, and record the result at the constants alongside the Chrome narrative.
