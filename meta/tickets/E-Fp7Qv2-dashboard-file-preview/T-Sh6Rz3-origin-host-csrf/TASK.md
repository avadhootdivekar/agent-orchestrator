# TASK: T-Sh6Rz3-origin-host-csrf

## Metadata
- Task ID: `T-Sh6Rz3-origin-host-csrf`
- Epic ID: `E-Fp7Qv2-dashboard-file-preview`
- Owner: `claude` (backend agent)
- Created: `2026-07-30`
- Last Updated: `2026-07-30`
- Status: `Done`
- Estimate: `< 3 days`

## Requirements Mapping
- Epic FR-7 (Host allowlist / Origin check / JSON content-type / response headers / SPA CSP).
- Epic NFR-1 (`[ui]` stays optional), NFR-2 (a test per mitigation), NFR-3 (no magic literals),
  NFR-4 (existing `tests/ui/*` keep passing **and** the `TestClient` accommodation lives in one
  place, paired with rejection tests).
- CONTRACT section 1A.3 + its 1A.4 test group.
- Design: `docs-md/dashboard-file-preview-hld.md` §2.10, §4.3 · ADR-0011 **D3**.

## Description
Add `src/agent_orchestrator/ui/security.py` — Starlette middleware enforcing a `Host`
allowlist, an `Origin` check on mutating methods, a JSON content-type requirement on mutating
routes, hardening response headers on every response, and a CSP on the SPA document only.

**This task closes a pre-existing critical gap and is worth doing on its own merits.** Today
the dashboard validates neither `Origin` nor `Host`. That means:

- **CSRF.** Any website the user visits while `ao ui` is running can `POST /api/runs` with a
  plain HTML form. No same-origin read is required — firing the request blind is enough,
  because that endpoint launches agents against the workspace: arbitrary code execution and
  unbounded token spend. **Loopback binding does not help; the browser is on loopback.**
- **DNS rebinding.** A page on `attacker.com` whose DNS record has a ~1s TTL and rebinds to
  `127.0.0.1`. The browser then treats `http://attacker.com:8765/` as *genuinely* same-origin
  with the attacker's page, so the same-origin policy, CORS, **and an `Origin` check** all pass
  legitimately. The attacker gets full **read and write**: every file under every root, plus
  run control. Only the `Host` check catches this.

It is tracked separately from `T-Bk4Hs7-preview-backend` because it predates the preview
feature, is valuable without it, and should be reviewable and revertable independently. It is
also the premise the rest of the epic rests on: "content that does not reach the dashboard
origin cannot hurt us" is only true if that origin is defensible (ADR-0011 D3).

Files owned:
- `src/agent_orchestrator/ui/security.py` (new)
- `src/agent_orchestrator/ui/app.py` (**middleware wiring + SPA CSP only** — routes belong to
  `T-Bk4Hs7`; this is the one file both Python tasks touch)
- `src/agent_orchestrator/cli.py` (**only the `ui_cmd` body**, to pass the bound host through)
- `tests/ui/test_security.py` (new), `tests/ui/conftest.py` (the single allowed-hosts fixture),
  `tests/ui/test_api_integration.py` (extend)

## Acceptance Criteria

**Host allowlist — kills DNS rebinding**
1. Given `Host: evil.com`, when any request is made, then the response is **421 Misdirected
   Request** and no handler runs.
2. Given `Host` in `localhost`, `127.0.0.1`, `[::1]`, or `::1` — **with or without any port** —
   then the request is allowed. All four forms, and a ported variant of each, are covered.
3. Given `ao ui --host 192.168.1.5`, then `192.168.1.5` is in the allowlist for that process:
   the host the server actually bound to is always permitted.
4. Given `AO_UI_ALLOWED_HOSTS=a.example,b.example`, then those hosts are allowed and others are
   421 (comma-separated parsing covered).
5. Given `AO_UI_ALLOWED_HOSTS=*`, then the check is disabled **and a loud warning is printed at
   startup**. Both halves are asserted — a silent bypass switch is worse than no switch.

**Origin check on mutating methods**
6. Given `POST`/`PUT`/`PATCH`/`DELETE` with `Origin: https://evil.com`, then **403**.
7. Given the same methods with an **absent** `Origin`, then the request is **allowed** — curl,
   the CLI, and the test client are legitimate non-browser callers, and browsers always send
   `Origin` on cross-origin mutating requests, so CSRF stays blocked. This allowance is
   deliberate and must have its own test so nobody later "fixes" it into a rejection and breaks
   scripted use.
8. Given a mutating request with a matching same-origin `Origin`, then it is allowed.
9. Given `GET`/`HEAD` with a foreign `Origin`, then it is **not** rejected on that basis (the
   `Host` check is what guards reads).

**JSON content-type on mutating routes — kills `<form>` CSRF**
10. Given `POST /api/runs` with `Content-Type: text/plain`, then **415**. Same for
    `application/x-www-form-urlencoded` and `multipart/form-data` — precisely the three types an
    HTML form can produce, which is the whole point of this control.
11. Given `Content-Type: application/json` (including with a `; charset=utf-8` parameter), then
    it is allowed.

**Response headers**
12. **Every** response — JSON, HTML, error, and 421/403/415 rejections alike — carries
    `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
    `Cross-Origin-Opener-Policy: same-origin`, and `Cross-Origin-Resource-Policy: same-origin`.

**SPA CSP — must be verified empirically, not guessed**
13. A CSP header is present on the `/` document response and on the SPA-fallback HTML response,
    and is **absent from JSON responses**.
14. **The policy is empirically verified**, not derived from a spec reading: a sandboxed
    `srcdoc` iframe containing inlined `data:` images **still renders** under it. `STATUS.md`
    must record what was tested, in which browsers, and the final policy. Adjust the starting
    point below to match observed reality rather than shipping a guess.
    Starting point:
    ```text
    default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline';
    font-src 'self' data:; connect-src 'self'; frame-src 'self' data:;
    object-src 'none'; base-uri 'none'; form-action 'none'
    ```
    Pass/fail: the preview feature works in a real browser **and** `connect-src`/`img-src` still
    forbid outbound requests to a foreign host. Both halves must be demonstrated — a policy that
    permits everything trivially passes the first half.
15. `HTML_PREVIEW`-adjacent needs are respected: whatever the final policy, `T-Fv9Ld2`'s
    sandboxed `srcdoc` iframe with `data:` images must render, and the frontend's own frame-level
    `<meta>` CSP is unaffected by this header.

**`ao ui` integration**
16. `ao ui` passes its bound host into the allowlist, and **extends** the existing off-loopback
    warning rather than adding a second, duplicate one.

**Test hygiene — the control must not be disabled by fixtures**
17. Starlette's `TestClient` sends `Host: testserver`. The accommodation lives in **exactly one
    place** (`tests/ui/conftest.py`), and `tests/ui/test_security.py` contains explicit tests
    that construct an app **without** that accommodation and assert a bad `Host` is still
    rejected. Pass/fail: a reviewer can point at the single fixture and at the tests proving the
    check is live. A security control every test opts out of is not a control.

**Gates**
18. `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .` all run and their **real**
    output reported in `STATUS.md`, including a demonstration that pre-existing `tests/ui/*`
    still pass (this middleware touches every request in the suite, so regression risk here is
    higher than for any other task in the epic).
19. No magic literals: allowed hosts, the env var name, the header names/values, and the CSP are
    named constants.

## Risks
- **Highest regression risk in the epic.** This middleware runs on every request, so a mistake
  breaks the entire existing `tests/ui/*` suite and, worse, could break real dashboard use in a
  way tests do not catch if the fixture masks it.
- **The fixture is the danger.** Setting allowed hosts globally in `conftest.py` to make the
  suite pass is correct **and** is exactly how the control gets silently disabled. Criterion 17
  exists to prevent that; do not satisfy it by loosening the tests.
- **The absent-`Origin` allowance looks like a hole.** It is a deliberate trade (criterion 7) and
  is covered by criteria 3/10 for the cases it does not handle. Document it at the call site so a
  future reviewer does not "harden" it into breaking the CLI.
- **The CSP is an open question, not a known-good value** (HLD §9). `srcdoc` CSP inheritance and
  `frame-src`/`img-src` interaction differ between browsers and from a plain spec reading.
  Guessing here either breaks the preview feature or silently provides less protection than the
  design document claims — both are worse than reporting what was actually observed.
- **421 is an unusual status code.** Verify it propagates through the stack intact and is not
  rewritten by Starlette or an exception handler.
- **IPv6 host forms** (`[::1]`, `[::1]:8765`, bare `::1`) are easy to get wrong; bracket handling
  and port stripping need explicit cases.
- `src/agent_orchestrator/ui/app.py` is extended concurrently by `T-Bk4Hs7` (routes).

## Dependencies
- `docs-md/dashboard-file-preview-hld.md` §2.10 (the layered control table and what each control
  stops) and §4.3; ADR-0011 D3.
- Existing `ao ui` off-loopback warning in `cli.py::ui_cmd` — **extend, do not duplicate**.
- Existing `create_app` in `ui/app.py`.
- Not blocked by `T-Bk4Hs7` or `T-Fv9Ld2`; all three run concurrently.

## Pseudocode / Algorithm
```text
CONSTANTS:
  DEFAULT_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
  ALLOWED_HOSTS_ENV     = "AO_UI_ALLOWED_HOSTS"
  WILDCARD              = "*"
  MUTATING_METHODS      = {"POST", "PUT", "PATCH", "DELETE"}
  JSON_CONTENT_TYPE     = "application/json"
  SECURITY_HEADERS      = { "X-Content-Type-Options": "nosniff",
                            "Referrer-Policy": "no-referrer",
                            "Cross-Origin-Opener-Policy": "same-origin",
                            "Cross-Origin-Resource-Policy": "same-origin" }
  SPA_CSP               = "<verified value — see AC-14>"

FUNCTION resolve_allowed_hosts(bound_host, env):
  raw = env.get(ALLOWED_HOSTS_ENV)
  IF raw IS SET:
     IF raw.strip() == WILDCARD:
        WARN_LOUDLY("host check DISABLED via " + ALLOWED_HOSTS_ENV + "=*")
        RETURN DISABLED
     RETURN {normalize(h) FOR h IN raw.split(",") IF h.strip()}
  RETURN DEFAULT_ALLOWED_HOSTS | {normalize(bound_host)}     # bound host ALWAYS allowed

FUNCTION normalize_host_header(value):
  h = lower(strip(value))
  IF h STARTS WITH "[":  RETURN h UP TO AND INCLUDING "]"     # IPv6 literal: keep brackets,
                                                              # drop :port after them
  RETURN h.split(":")[0]                                      # strip :port

MIDDLEWARE dispatch(request, call_next):
  # 1. Host allowlist — the DNS-rebinding control. A rebound request still says
  #    Host: attacker.com, which an Origin check would wave through because after
  #    rebinding the origin genuinely matches.
  IF allowed_hosts IS NOT DISABLED:
     IF normalize_host_header(request.headers["host"]) NOT IN allowed_hosts:
        RETURN json_response(421, "misdirected request: host not allowed")

  IF request.method IN MUTATING_METHODS:
     # 2. Origin — classic cross-site CSRF. Absent Origin is ALLOWED on purpose:
     #    curl / the CLI / TestClient are legitimate non-browser callers, and browsers
     #    always send Origin on cross-origin mutating requests. Do not "harden" this.
     origin = request.headers.get("origin")
     IF origin IS PRESENT AND origin NOT IN allowed_origins(allowed_hosts, request):
        RETURN json_response(403, "cross-origin request forbidden")
     # 3. JSON content type — kills <form> CSRF specifically: an HTML form can only send
     #    urlencoded / multipart / text-plain and CANNOT set application/json.
     IF media_type(request.headers.get("content-type")) != JSON_CONTENT_TYPE:
        RETURN json_response(415, "content-type must be " + JSON_CONTENT_TYPE)

  response = await call_next(request)

  # 4. Hardening headers on EVERY response, including the rejections above.
  FOR (k, v) IN SECURITY_HEADERS:  response.headers[k] = v
  # 5. CSP on the SPA document only — never on JSON, where it is noise.
  IF is_html_document(response):   response.headers["Content-Security-Policy"] = SPA_CSP
  RETURN response
```

## Schemas / Interface Notes
- Interface / API: no new routes. Cross-cutting HTTP behaviour on **all** existing and new
  routes:
  ```text
  * any method,  disallowed Host                      -> 421 Misdirected Request
  POST|PUT|PATCH|DELETE, foreign Origin present       -> 403
  POST|PUT|PATCH|DELETE, Origin absent                -> allowed (non-browser clients)
  POST|PUT|PATCH|DELETE, non-JSON Content-Type        -> 415
  every response                                      -> nosniff, no-referrer, COOP, CORP
  SPA document responses only                         -> Content-Security-Policy
  ```
- CLI: `ao ui [--host H] [--port P]` — `H` joins the allowlist; the existing off-loopback
  warning is **extended**, not duplicated.
- Config / env: `AO_UI_ALLOWED_HOSTS` (comma-separated; literal `*` disables with a loud
  warning). Per ADR-0003 this is an env-layer setting; there is deliberately **no** config-file
  or CLI equivalent in this task, keeping the surface minimal.
- Spec / data schema (JSON/YAML): `N/A` — no workflow-spec change.
- Triggers / events (cron/event): `N/A`.
- Artifacts (inputs/outputs by path): `N/A`.

## Handoff Boundary
- Upstream: existing `create_app` and `cli.py::ui_cmd`; HLD §2.10/§4.3; ADR-0011 D3.
- Downstream: `T-Fv9Ld2-preview-frontend` depends on the **verified** SPA CSP permitting a
  sandboxed `srcdoc` iframe with inlined `data:` images. If the policy has to change to make the
  preview render, say so in `STATUS.md` immediately — the frontend cannot discover that on its
  own, and the HLD (§4.3, §9) must be updated to the verified value.
- Sibling: `T-Bk4Hs7-preview-backend` owns routes in `app.py` and all of
  `files.py`/`htmlpreview.py`. Middleware and SPA-CSP wiring only here.
- Not owned: anything under `ui/`, `install.sh`, or any `cli.py` code outside `ui_cmd`.

## Artifacts
- Docs/comments: `meta/tickets/E-Fp7Qv2-dashboard-file-preview/T-Sh6Rz3-origin-host-csrf/`
- Code (planned): `src/agent_orchestrator/ui/security.py`,
  `src/agent_orchestrator/ui/app.py` (middleware + CSP), `src/agent_orchestrator/cli.py`
  (`ui_cmd` body only)
- Tests (planned): `tests/ui/test_security.py`, `tests/ui/conftest.py`,
  `tests/ui/test_api_integration.py`

## Comments
- By: architect · Role: architect · Date: 2026-07-30 · Comment: Carved out of the backend task
  deliberately: this fixes a **pre-existing** critical gap (unauthenticated `POST /api/runs`
  reachable by CSRF from any site the user visits, and full read+write via DNS rebinding), so it
  deserves its own traceable ticket, its own review, and the ability to be reverted without
  touching the preview feature. Three points implementers should not treat as negotiable:
  (a) the **`Host` allowlist**, not the `Origin` check, is what stops DNS rebinding — after
  rebinding the origin genuinely matches, so an Origin-only design fails silently;
  (b) the **absent-`Origin` allowance** is a deliberate trade for non-browser clients and needs
  its own test plus a call-site comment, or someone will "harden" it and break the CLI;
  (c) the **SPA CSP is an open question, not a value to ship on faith** — HLD §9 flags it, and
  AC-14 requires reporting what was actually observed in which browsers, plus demonstrating that
  the policy still blocks outbound requests (a permissive policy trivially passes the "it
  renders" half). Also flagged AC-17 as the subtle one: the `TestClient` `Host: testserver`
  accommodation is both necessary and the most likely way this control gets silently disabled
  across the suite.
- By: architect · Role: architect · Date: 2026-07-30 · Comment: **Done.** All acceptance criteria
  met. **AC-14 is fully discharged** — the CSP was verified empirically in real headless Chrome
  **with a negative control** (removing `data:` from `img-src` blocked the same sampled pixel),
  satisfying both halves of the criterion: it renders what the feature needs *and* the policy
  demonstrably discriminates rather than being permissive-by-accident. The drafted policy shipped
  unchanged; the four verification fields in `STATUS.md` are filled from observation, and the
  narrative lives at `security.py::SPA_CSP` so it cannot drift from the value it justifies.
  Two things to carry forward. (1) **One deviation from a literal AC-10:** the Content-Type gate
  was narrowed to mutating requests that **carry a body**, because `POST /api/runs/{id}/resume`
  and `DELETE /api/runs/{id}` are real bodyless dashboard calls a strict reading would have broken.
  Safe on two independent grounds — the `Origin` check still covers them, and the audit **verified
  with a real cross-origin form POST** that browsers cannot construct the exempted shape — but it
  narrows a stated control, so it is flagged for review on its own terms rather than buried.
  (2) **AC-14's verification has a scope limit that turned out to be the epic's most important
  finding:** it says nothing about navigation, because **no CSP directive governs navigation at
  all**. That is exactly the gap finding H1 exploited, and it is why H1 had to be fixed in
  `T-Bk4Hs7`'s sanitizer rather than by any control this task owns. AC-17 held as intended — the
  `TestClient` accommodation stayed in one place, paired with tests that build an app without it
  and assert rejection still happens. This task also owns audit finding L4 (`Origin`/`Host`
  userinfo normalization silently accepting a spoofed prefix — not browser-reachable, fixed by
  failing closed on any `@`).
