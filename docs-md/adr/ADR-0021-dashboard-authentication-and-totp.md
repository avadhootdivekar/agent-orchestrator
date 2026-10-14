# ADR-0021 — Dashboard and hub authentication: local accounts, optional TOTP, per-server sessions

- **Status:** **Accepted** (2026-10-05). Implemented by `E-Da5Tn9-dashboard-auth-totp` (all 23 tasks
  Done) and signed off by the post-implementation security review (T-2wE08U). The decisions below
  are the approved design (v2.1, gates folded, HLD §28.9); every change made during implementation
  or review is recorded in [Amendments (as built)](#amendments-as-built-2026-10-05) at the end,
  and the HLD's "As built" section holds the full deviation list and residual risks.
- **Date:** 2026-10-04 (v1); revised 2026-10-05 (v2, then v2.1). v2 incorporates the Phase-4
  consultations with manager, developer, reviewer, tester, dev-security and dev-critic (HLD
  §28.1–§28.8); v2.1 incorporates the two independent gates (HLD §28.9).
- **Deciders:** Avadhoot Divekar (owner, binding brief); architect (agent); manager (agent)
- **Epic:** `E-Da5Tn9-dashboard-auth-totp` · **Design:**
  [`dashboard-auth-hld.md`](../dashboard-auth-hld.md). Its decision log §1 holds all of D1–D25;
  this ADR records the hard-to-reverse subset. Each heading names the HLD decisions it covers.
- **Related:**
  - ADR-0003 (settings precedence)
  - ADR-0005 (headless tool policy; recommended `disallowed_tools` hardening, HLD §2.6)
  - ADR-0010 (dashboard architecture)
  - ADR-0011 D3 (the dashboard origin is a transport-layer trust boundary) and its "navigation"
    note
  - ADR-0012 (multi-workspace service: supervisor, children, hub)
  - [`meta/ROADMAP.md`](../../meta/ROADMAP.md) §3.1

## Context

The `ao ui` dashboard can read every workspace file and launch runs, and launching a run means
executing agents as the OS user and spending API budget. The `ao service` hub lists every
workspace's root, port, pid and log path. Both are **unauthenticated**. Loopback binding and
`ui/security.py`'s Host/Origin/Content-Type hardening are the only protection.

The owner's brief asks for opt-in local accounts with an optional, configurable TOTP second factor.
It sets these constraints:
- no new Python dependencies;
- deny by default;
- a usable identity contract for the approval-gates epic;
- safety across the hub plus N dashboards, all on one host but on different ports.

**Two facts shape most decisions below:**
- **Browsers scope cookies by host, not by port** (RFC 6265 §8.5). Every local listener the
  browser talks to therefore receives every `ao` cookie.
- **The workspace (`.ao/config.yaml`, cloned repositories) is attacker-influenced input** (HLD A12).

Several choices below shape every later security feature (SSO, RBAC, API tokens, multi-user
hosting). That is why they get an ADR rather than living only in the HLD.

---

## D1 — Per-server sessions with stable realm ids; in memory; the port only names the cookie (HLD D3, D23)

**Decision.**
- **Realms.** Each listening process is a realm:
  - realm id `hub` for the service hub;
  - realm id `ui:<workspace_id>` for a dashboard, where `workspace_id = sha256(resolved root)[:12]`.

  Ids are **stable labels** that survive port reassignment. They appear in sessions, audit lines
  and `Principal.realm`.
- **Session table.** Each realm owns an in-memory session table. It holds only `sha256(token)` and
  `sha256(proof)` (D11) for 256-bit random values.
- **Cookie.** The cookie is named `ao_sid_<own port>`, or `__Host-ao_sid_<own port>` over https. Its
  flags are `HttpOnly; SameSite=Strict; Path=/`, plus `Secure` on https. It has no `Domain` and no
  `Max-Age`.
- **Lifetime.** Sessions do not survive a server restart. Idle limit 30 min; absolute limit 12 h;
  partial sessions 300 s. Expiry is measured on **one `CLOCK_BOOTTIME` timeline**, which counts
  suspend.
- **Sliding.** A session slides only on user activity carrying the session proof (mutations,
  keepalive), or on a browser-attested navigation to the single cookie-only route, the hub index
  (v2.1, security M1). Polling never extends it, and neither does any request without the proof.
- **Rotation.** The token and the proof rotate on login, on completing the second factor, on
  enrollment, and on any credential change.

**Why the port is in the cookie name.** With one shared cookie name, logging in to the hub would
overwrite the dashboard's cookie. With per-port names and per-process tables, a token is valid only
in the process that issued it, so realms are isolated by construction.

**Alternatives.**
- **(D) Hub-run one-time handoff ticket.** The hub mints a single-use ticket for a dashboard link.
  **This is the recommended first follow-up** (dev-critic C-6): a single login across the hub and
  the dashboards without a shared on-disk session table.
- **(B) Store-scoped SSO.** A shared `sessions.json` and a store-named cookie. Second follow-up. Its
  seams are the `SessionStore` ABC, the `realm` and `store_id` fields, and the single
  `Realm.cookie_name()`.
- **(C) Stateless signed cookies.** Rejected: they cannot be revoked per session, and the brief
  requires server-side, hash-only sessions.

**Consequences.**
- Every realm needs its own login.
- With TOTP, a second realm within the same 30 s step needs the next code (D5).
- Restarting a dashboard logs its users out.
- The cookie-harvesting class is addressed by D11, which is a separate, revisitable decision.

---

## D2 — Strengthened CSRF for cookie-authenticated requests; no-store; no synchronizer token (HLD D4)

**Decision.** When auth is enabled:
- **Origin on every mutation.** Every `POST/PUT/PATCH/DELETE`, including login and logout, MUST carry
  an `Origin` equal to the request's own scheme + `Host` host + port.
  - A missing `Origin` → 403 `origin_required`; a mismatch → 403 `origin_mismatch`.
  - This check is independent of the Host allowlist, so it also holds under
    `AO_UI_ALLOWED_HOSTS=*`.
  - The existing `SecurityMiddleware` check is unchanged and still runs first.
- **Fetch Metadata.** A protected route, and every mutation, requires `Sec-Fetch-Site` to be
  `same-origin` or `none` whenever the header is present. The exception is top-level `GET`/`HEAD`
  navigation. Anything else → 403 `cross_site_request`. This is what stops a same-site page on
  another localhost port from driving the dashboard.
- **Duplicate cookies.** More than one realm cookie in a request means **no session** (anti-tossing),
  and a WARNING is logged once.
- **Caching.**
  - `X-Frame-Options: DENY` on every response.
  - `Cache-Control: no-store` on every `/api` response and every non-PUBLIC response.
  - `Clear-Site-Data: "cache"` on logout.
  - The SPA re-checks auth status when the page is restored from the bfcache.
- **Auth off.** None of this applies when auth is off, so responses are byte-identical.

**Alternatives.**
- A synchronizer or double-submit token. Rejected: it adds nothing against browser attackers beyond
  Origin + Fetch Metadata + SameSite + no-CORS. The origin-bound secret that *does* add something is
  D11.
- Keeping "absent Origin = non-browser, allowed" (ADR-0011 D3) for cookie-authenticated requests.
  Rejected: when cookies carry authority, an absent Origin is an ambiguity, not a signal.

**Consequences.**
- With auth on, non-browser clients (curl, scripts) cannot mutate state. API tokens for automation
  are a follow-up with their own rule.
- The SPA and the hub page must use `fetch()` with no `mode` option, never `<form>` posts. A vitest
  string check enforces this. Under `Referrer-Policy: no-referrer`, a same-origin `fetch` carries
  the real `Origin` (verified in Chrome 138); a form post sends `null`.

---

## D3 — Credentials in a user-level config directory, mutable security state in a state directory; forward-compatible JSON (HLD D5)

**Decision.**
- **Credential store.** `$AO_AUTH_DIR` > `$XDG_CONFIG_HOME/ao/auth` > `~/.config/ao/auth` (0700).
  It holds:
  - `users.json` (0600) and its `users.lock` flock sidecar;
  - per user: an immutable `user_id`, the password hash, `credential_epoch`, TOTP, recovery-code
    hashes, `totp_required`, and an optional enrollment-token hash.
- **State store.** `$AO_AUTH_STATE_DIR` > `$XDG_STATE_HOME/ao/auth`, or `<store_dir>/state` when the
  store is overridden. It holds `lockouts.json`, `audit.jsonl` and their locks.
  - **Failed logins never rewrite the credential file.** They therefore never invalidate
    revalidation caches, and a credential backup is not churned by attacks.
- **Writes.** Through a neutral `agent_orchestrator/fsutil.py`:
  - flock with a timeout;
  - an O_EXCL/O_NOFOLLOW temp file, fsync, `os.replace`, then a directory fsync;
  - stale temp-file cleanup;
  - owner and permission checks on the file, its directory and the **parent** directory.
- **Forward compatibility.**
  - Models use `extra="allow"`, so a field written by a newer `ao` flavor survives a rewrite by an
    older one.
  - A store carrying `required_features` the reader does not know, or `schema_version > 1`, is
    refused.
  - Never migrate on a login write. Breaking changes are future work for a later epic; this epic
    ships no migration command (v2.1, design-review M3).
- **Never browsable.** Both directories are denied by `FileBrowser.resolve()` (list, read, HTML
  preview, symlinks, absolute paths) **whether or not auth is enabled**, and so is
  `~/.config/ao/service.env` (v2.1, security L7). This is the one deliberate behaviour change with
  auth off. The denial is a generic `denied_paths` mechanism that the approval-gates epic reuses
  (HLD §16 X2).
- **Permissions (v2.1).** Directories are created with `os.mkdir(mode=0o700)` and fixed through an
  `O_NOFOLLOW` fd + `fchmod`. A parent that is group-writable but owned by the user (`umask 002`
  hosts) warns instead of refusing. A `store_dir` chosen by workspace config is never created or
  chmod-ed by `ao auth` (security M6, L6).
- **Administration and bootstrap** are CLI-only. There is no web sign-up and there are no default
  credentials.

**Alternatives.**
- A store per workspace. Rejected: duplicated accounts, it lives where agents write, and it would be
  browsable.
- SQLite. Rejected: unnecessary for fewer than 1000 users, and harder to inspect.
- The OS keyring. Rejected: unavailable headless.
- `extra="forbid"` with migrate-on-write (v1). Rejected (dev-critic C-1): it corrupts or rejects a
  store shared by `ao` and `ao-beta` flavors at different versions.

**Consequences.**
- Agents running as the same OS user *can* read or modify both stores. That is out of scope (HLD A6)
  and stated explicitly.
- A local POSIX filesystem is required (flock).
- Losing the state directory is harmless: lockouts reset, and audit history is lost.

---

## D4 — Cryptographic parameters (stdlib only) (HLD D6, D8)

**Decision.**
- **Passwords.**
  - Hashing: `hashlib.scrypt` with `N=2^15, r=8, p=3` (32 MiB, about 165 ms measured; equivalent to
    OWASP's `2^17/8/1` cost), `dklen=32`, a 16-byte salt, and explicit `maxmem=64 MiB`. OpenSSL's
    default limit rejects these parameters (verified).
  - Encoding: `$scrypt$v=1$ln=15,r=8,p=3$<b64 salt>$<b64 dk>`. `v=1` pins NFKC + UTF-8.
  - **Parse bounds:** `ln ≤ 17`, `r ≤ 16`, `p ≤ 16`, at most 128 MiB per hash. A tampered hash
    string cannot become a memory bomb.
  - Upgrade: an opportunistic rehash on login, written with **compare-and-swap** (D10). It does not
    bump the epoch.
  - Server limits: hashing concurrency 2 per process; a queue cap that returns 503, and the address
    throttle counts that 503.
  - Unknown users are verified against a dummy hash with the current parameters.
- **TOTP.** RFC 6238: HMAC-SHA-1, 6 digits, 30 s, `T0=0`, ±1 step, a 160-bit Base32 secret, and an
  ASCII `otpauth://` URI. It passes the RFC 4226 and RFC 6238 SHA-1 vectors.
- **Recovery codes and enrollment tokens.** 80 bits each (`XXXX-XXXX-XXXX-XXXX`, Crockford Base32),
  stored as salted SHA-256, single-use.
- **Comparisons.** All use `hmac.compare_digest`.

**Alternatives.**
- argon2id and bcrypt. Rejected: they need third-party dependencies (forbidden by the brief).
- PBKDF2. Rejected: not memory-hard.
- SHA-256 or longer TOTP codes. Rejected: authenticator-app compatibility.
- **`ln=16, r=8, p=1`** (64 MiB, about 120 ms; dev-security #12). Recorded as an equally valid
  choice for owners who prefer memory hardness over parallelism. Switching later is non-breaking,
  because the parameters live in each hash.

**Consequences.**
- On Pi-class hardware, logins may take about 1 s.
- TOTP seeds are stored in clear inside the 0600 file. At-rest seed encryption is a follow-up, **deferred 2026-10-06** pending an owner decision on key custody (a key file beside the store adds little against the same-uid attacker of accepted residual A10).

---

## D5 — Global TOTP replay protection; sticky enrollment; forced enrollment needs a CLI-issued token over a safe transport (HLD D7)

**Decision.**
- **Replay.** A TOTP code is accepted only if its step is **strictly greater** than the user's
  `last_used_step`. That value lives in the shared store and is updated under the flock, so a code
  works **once across every realm** (RFC 6238 §5.2).
- **Policy.**
  - `off`: no new enrollment.
  - `optional`: users may enroll and disable.
  - `required`: users without TOTP get an enrollment-only partial session, and cannot disable it
    themselves.
- **Per-user requirement.** `totp_required` applies `required` to one user. One pure function,
  `totp_requirement(policy, user)`, returns:
  - `NONE` (not required);
  - `ENROLL_ALLOWED` (required, and the policy is not `off`);
  - `BLOCKED` (required, but the policy is `off`). A BLOCKED user's login is refused with 403
    `totp_required`. Policy `off` can never silently downgrade a required user.
- **Sticky enrollment.** An enrolled user is challenged **whatever the policy**.
- **Forced enrollment** needs a one-time **enrollment token**, which the operator issues with
  `ao auth enrollment-token` (also printed by `add-user --require-totp` and `reset-2fa`).
  - Without it, anyone who learned the password could bind their own authenticator first
    (dev-security #5).
  - The token expires after `ENROLLMENT_TOKEN_TTL_SECONDS` (3600) and its attempts count toward the
    lockout.
- **Safe transport.** Web enrollment, confirmation and recovery-code regeneration are refused with
  403 `insecure_transport` unless the request is https or comes from a loopback client. A shared
  secret sent in clear is compromised permanently. CLI enrollment is unaffected.
  - **v2.1 (security M2):** a loopback client is a loopback peer **with** a loopback `Host` **and**
    no `Forwarded` / `X-Forwarded-*` header. An unconfigured reverse proxy on the same host cannot
    make remote clients look local; it is reported (`proxy_suspected`, one WARNING).

**Alternatives.**
- Per-realm replay tracking. Rejected: violates RFC 6238 §5.2.
- Policy governs whether to challenge. Rejected: silent downgrade.
- First-come enrollment (v1). Rejected (dev-security #5).
- Refusing plain-HTTP logins entirely. Rejected: it would break today's documented LAN use. The
  loud warning plus the enrollment refusal is the balance.

**Consequences.**
- A second realm within the same step needs the next code (the UI explains `reason: "replayed"`).
- Switching to `required` with existing users needs one CLI command per user (HLD §18 #8).

---

## D6 — Deny by default: an always-installed ASGI middleware with per-app policy tables, and a stable principal contract (HLD D12, D19, D24)

**Decision.**
- **Installation.** `AuthMiddleware` is a pure ASGI middleware, installed by `create_app` and
  `build_hub_app` **in every mode**, inside `SecurityMiddleware`. It is added *before*
  `SecurityMiddleware` in code, because the last `add_middleware` call is the outermost.
- **Classification.** Each request is classified by the **route it will hit**, using the router's
  own `route.matches(scope)` order on the `root_path`-stripped path. The route is looked up in **one
  explicit policy table per app** (`DASHBOARD_ROUTE_POLICIES`, `HUB_ROUTE_POLICIES`).
  - **Anything not in the table is AUTHENTICATED**, including routes hidden inside an
    `include_router` sub-router.
  - Under `/api/`, only an `/api` route can be opened, so the SPA fallback never can.
  - Auth routes are registered **flat** (`add_api_route`), and this is checked at construction.
- **Route enumeration.** A test asserts that the computed non-AUTHENTICATED set equals the
  documented allowlist exactly, for every method, and that no table entry is stale.
- **Principal (contract v2.1; owner decision, final).** The middleware always sets
  `request.state.principal` and `request.state.auth_enabled`.

  ```python
  @dataclass(frozen=True, slots=True)
  class Principal:
      username: str
      auth_method: Literal["password", "password+totp"]
      roles: list[str] = field(default_factory=list, hash=False)   # fresh [] per Principal; excluded from hash
      user_id: str = field(kw_only=True)                           # realm, session_id, amr (tuple),
      ...                                                          # auth_time, provider: all kw_only=True
  ```

  - `roles` keeps the brief's `list[str]`; `hash(p)` works because the list is excluded from the
    hash (`==` still compares it). Positional `Principal(username, auth_method, roles)` works.
  - `SessionRecord`, `VerifiedIdentity` and `UserView` keep tuples. `SessionManager.principal_for`
    is the only converter and returns a **fresh** `list(record.roles)` per request, so mutating a
    principal's list never reaches session or store state.
  - A principal is set **only when the session proof matches** (v2.1, security M1), except on the
    single route flagged `COOKIE_ONLY_NAVIGATION` (the hub index).
  - `current_principal(request)`, `auth_enabled(request)` and `require_principal(request)` are
    exported, together with `AuditLog.record`.
  - `principal is None` alone never means "auth off".
  - Persist `user_id`, never `username`.
- **Error envelope.** Auth errors use `{"detail": str, "code": str, …}`.

**Alternatives.**
- A path-prefix allowlist. Rejected: a future non-API route would be public by accident.
- Per-route decorators or markers (v1). Rejected (developer D-1, reviewer R-7):
  - `include_router` hides route objects;
  - decorators scatter the policy across files;
  - a table is one reviewable diff.
- FastAPI global dependencies. Rejected: they do not cover mounts or static files.
- `BaseHTTPMiddleware`. Rejected: it cannot buffer and replay the body cleanly.

**Consequences.**
- A new public route is a deliberate, two-place change: the table plus the allowlist test.
- Partial sessions are confined structurally.
- **The `Principal` shape is a cross-epic contract.** v2.1 settles it by owner decision (HLD OQ-8,
  decided): `roles: list[str]` as in the brief, plus keyword-only additive fields. (v2 had proposed
  `tuple[str, ...]`; superseded.) Any later change needs the manager's sign-off.
- Roles are frozen into a session at issue. **When RBAC lands, any role change must bump
  `credential_epoch`** (or revalidation must re-read roles).

---

## D7 — The provider seam: an ABC, a callable route-builder registry, an injectable runtime (HLD D11)

**Decision.**
- **The ABC.** `AuthProvider` (FastAPI-free) defines `check_ready()`,
  `revalidate(user_id, epoch) -> VALID | REVOKED | UNAVAILABLE` and `user_view(username)`.
  - `LocalPasswordProvider` implements the login side.
  - `LocalTotpService` implements the second factor.
  - Both route every credential check through one `AttemptGuard`.
- **Routes.** Route builders are registered as callables (`register_route_builder(id, fn)`).
  v2.1: a provider may register several builders, run in registration order; the local provider
  registers one per module (`auth/http/routes.py`, `auth/http/routes_second_factor.py`).
  `build_auth_runtime(..., provider=)` injects any provider.
- **Core stays credential-free.** Sessions, middleware and principal consume only
  `VerifiedIdentity`.
- **Proof.** A test registers a **redirect-shaped** fake provider (start → 302 → cross-site
  callback GET → session) and signs a user in without editing any core module.

**Alternatives.**
- One concrete class with no seam. Rejected: OIDC would mean rewriting.
- Capability protocols plus `"module:function"` strings (v1). Rejected (reviewer R-5): speculative
  indirection with no second implementation.
- Entry-point plugins. Rejected: premature.

**Consequences.**
- OIDC, LDAP and trusted-header providers add a provider plus a route builder. A real redirect
  provider also needs two follow-up seams that the MVP deliberately leaves out (HLD OQ-10):
  - provider-contributed PUBLIC policy entries, because the per-app tables are static today;
  - a one-time **proof handoff** for redirect flows. A cookie-only proof endpoint would defeat D11.

  The seam test composes its own app and policy table, so it proves the session seam without
  claiming more.
- WebAuthn needs a canonical hostname (HLD §10.7).
- API tokens need their own middleware step and their own Origin rule.

---

## D8 — Settings: layered, workspace config may only tighten, fail-closed, one launch sequence (HLD D2, D15, D16, D17, D22)

**Decision.**
- **Layering.**
  - Dashboard: CLI > env > workspace `ui.auth.*` > default (off).
  - Hub: CLI > env > default.
  - Parsing is strict: an empty env value is an error.
- **Tighten-only workspace layer** (dev-security #10). The workspace config may enable auth, raise
  the TOTP policy, shorten timeouts, lower thresholds, raise the minimum password length, or point
  `store_dir` at an absolute path outside the workspace that passes the permission checks. **Any
  loosening value is refused** (exit 78) with a message naming the env or CLI alternative.
  `trusted_proxies` is accepted **only** from CLI or env.
- **Fail closed.** A pure `decide()` maps every combination of layer validity, enablement and user
  count to run or refuse (HLD §11.3.4). An unreadable user count, while auth might be intended,
  refuses.
- **v2.1 — the workspace layer cannot silently switch protection off** (security M3):
  - `enabled: false` from workspace config **only**, while accounts exist (or the count is
    unknown) → refuse (exit 78), pointing at `--no-auth` / `AO_UI_AUTH=0`; audit
    `auth.startup.disabled_by_config`; flagged by `ao auth status`.
  - A `totp` value below `required` from workspace config only, with auth on and accounts present
    → start with a warning, audit `auth.startup.totp_downgraded_by_config`, and a status flag.
  - Settings record these as `ConfigRisk` data; only `prepare_auth` enforces them, so `ao auth`
    keeps working.
- **v2.1 — launch hygiene:** `--port 0` is refused with auth on (the cookie is named after the
  port); `ao ui --reload` passes the same uvicorn kwargs as the non-reload path (security L3, L4).
- **One launch sequence.** `auth/launch.py::prepare_auth(...) -> AuthLaunch` covers resolve, build,
  readiness, warnings, log redaction, uvicorn kwargs, child env and denied paths. `ao ui`,
  `create_app_from_env` and `ao service run` call it.
  - `EXIT_CONFIG = 78` is defined once in root `errors.py`.
  - The supervisor treats a child exiting 78 as terminal: no restart, no port reassignment.
  - The systemd unit gains `RestartPreventExitStatus=78`.
- **Proxy headers.** With auth on, uvicorn runs with `proxy_headers=False` unless trusted proxies are
  configured, and the proxy must preserve `Host`. v2.1: an unconfigured proxy is detected (D5) and
  reported; with `trusted_proxies=127.0.0.1`, any local process can claim a client address via
  `X-Forwarded-For`, so the documented advice is a unix socket or a firewalled app port.
- **Warnings.**
  - A loud plain-HTTP warning when auth is on with a non-loopback bind and no trusted proxy.
  - A deprecation notice when auth is off with a non-loopback bind.
- **Recommended deployment.** `AO_UI_AUTH=1` in `~/.config/ao/service.env`.

**Consequences.**
- A cloned repository's `.ao/config.yaml` can make an `ao` instance *stricter*, never weaker.
- A stale globally installed `ao` silently ignores these settings. Docs, `ao auth status` and
  `install.sh --force` (which runs `uv ... --reinstall`; there is no `--reinstall` flag, see
  Amendment 2) are the mitigation.
- `ao service list/status` treat a hub 401 as "running, login required" and fall back to the
  persisted state.

---

## D9 — Throttling: canonical client keys in memory, lockouts in the state directory, phantoms for unknown names (HLD D9)

**Decision.**
- **Per-address throttle.** In memory, keyed by a **canonical client key**: IPv4-mapped IPv6
  unwrapped; IPv6 aggregated to /64; an unparseable peer becomes `unknown`. It is checked **before**
  any scrypt work. Busy (503) rejections count as failures.
- **Per-account lockout.** Exponential backoff (5 failures, then 30 s doubling to a 900 s cap; reset
  after 24 h), persisted in `lockouts.json` **keyed by `user_id`**, so it is shared by every realm.
- **Unknown usernames.** Tracked as **phantom** entries, capped at 4096 with oldest-first eviction.
  Responses, hasher calls and lockout writes are therefore identical for "no such user" and "wrong
  password". **v2.1 (security L1):** the phantom key, and the audit `username_hash`, is
  `HMAC-SHA256(name_key, normalized name)` with a per-store random key kept in `lockouts.json`, so
  a pasted password in the username field cannot be brute-forced offline from the state files;
  every malformed name shares one bucket.
- **Serialization.** A per-username `asyncio.Lock` serializes attempts within a process.
- **Escape hatch.** `ao auth unlock` clears a lockout.

**Alternatives.**
- In-memory lockouts only. Rejected: N realms would give N times the guesses.
- Lockouts inside `users.json` (v1). Rejected (reviewer R-7b): every failed login would rewrite the
  credential file and bust revalidation caches.

**Consequences.**
- An attacker can lock out a known account for up to 15 minutes at a time (`unlock`; audited).
- An attacker spraying more than 4096 names can evict one phantom entry. This oracle is documented
  and bounded, and real accounts are never evicted.

---

## D10 — Immutable `user_id`, snapshot-consistent epochs, compare-and-swap writes, tri-state revalidation (HLD D10)

**Decision.**
- **`user_id`.** Every account gets an immutable random `user_id` (128-bit hex). Sessions,
  lockouts, audit lines and `Principal` carry it. A removed and re-added username is a **different
  user**, so old sessions never revive (dev-security #1a).
- **Snapshot consistency.** `authenticate()` reads the user's hash and `credential_epoch` from **one
  snapshot** and issues the session with that epoch. A credential change that lands between verify
  and issue therefore revokes the new session on its next request (#1b).
- **Compare-and-swap.** Opportunistic writes (rehash, `last_login_at`) and second-factor writes
  (`consume_totp`, `consume_recovery`, `enroll_totp`) apply only if `user_id` and the epoch still
  match. Otherwise they return STALE and change nothing (#1c).
- **Revalidation.** Every request revalidates `(user_id, epoch)` against the stat-cached store:
  - VALID → continue;
  - REVOKED → destroy the session, 401;
  - UNAVAILABLE (store corrupt or locked) → 503, **session kept**.

**Alternatives.**
- Username plus epoch (v1). Rejected: revival on re-add.
- Re-reading the epoch after verify. Rejected: it reopens the race.

**Consequences.** Restoring an older `users.json` lowers epochs, so newer sessions die. This is
documented and the safe direction.

---

## D11 — Session proof header: a second, origin-bound secret on every API call (HLD D25) — **DECIDED (v2.1): in the MVP**

**Context.** Any process listening on another localhost port receives the realm cookie: on
navigations and on credentialed requests from its own page. It can then replay the cookie with
curl. D2 stops such a page from *driving* the dashboard through the browser. It cannot stop
out-of-browser replay of a harvested cookie (HLD A4).

**Decision.**
- **Issue.** Every session-issuing response returns a `session_proof`: 256-bit random, stored
  server-side as a hash only. It is returned in the JSON body, never in a cookie.
- **Store.** The SPA and the hub page keep it in **origin-scoped `localStorage`**, which a page on
  another port cannot read. If storage is blocked, it is kept in memory with a clear message.
- **Send.** Every `/api` request sends it as `X-AO-Session-Proof`.
- **Check.** The middleware requires a matching proof for every non-PUBLIC route (v2.1: API or not),
  except the one route flagged `COOKIE_ONLY_NAVIGATION`.
  - A missing or wrong proof means "anonymous for this request". It never destroys the session and
    never clears the cookie, so a harvester cannot log the victim out through it.
  - **v2.1 (security M1):** on every route, PUBLIC ones included, a request without the proof gets
    no principal and never slides the idle timer.
  - `GET /api/auth/status` reports a session only with the proof.
  - Logout destroys the server session only with the proof.
- **Rotation.** The proof rotates with the token.
- **Navigations:** the SPA shell is PUBLIC (no session needed). The hub index is the **single**
  `COOKIE_ONLY_NAVIGATION` route: a cookie-only principal there, sliding only for browser-attested
  navigations.

**Alternatives.**
- **Accept the residual** (v1). Viable, and cheaper by about 1.5 dev-days. The README must then
  state that another local listener can replay a dashboard session.
- **Distinct `*.localhost` hostnames per server.** Rejected for the MVP: it changes URLs, the Host
  allowlist and the service's link generation.
- **Bearer tokens only (no cookie).** Rejected: navigations and the hub index need a cookie.

**Consequences.**
- Harvested-cookie API replay is closed.
- **Residuals:**
  - the hub index (a cookie-only navigation) can still be rendered with a harvested cookie;
  - a tossed duplicate cookie forces a logout (D2).
- Any future API client must carry the proof, or use the follow-up API tokens.
- **Decided (HLD OQ-9, v2.1):** D11 ships in the MVP. It is security-driven and is not on the
  schedule cut-lines (HLD §24.1).

---

## Consequences summary

- **Auth off:** byte-identical behaviour. The only additions are `GET /api/auth/status` (and its
  OpenAPI entry) and the browsing denial of the store and state directories and `service.env`.
- **Auth on:**
  - deny by default through per-app policy tables;
  - per-realm sessions with stable realm ids;
  - a cookie plus an origin-bound proof (D11);
  - global single-use TOTP codes;
  - sticky enrollment, with token-gated forced enrollment over safe transports;
  - immutable user ids with CAS writes;
  - tighten-only, fail-closed configuration, where a config-only disable with accounts refuses
    (v2.1);
  - CLI-only administration.
- **Documented residual risks:**
  - same-user agents can read or modify the stores (out of scope; `disallowed_tools` hardening
    recommended);
  - with D11: hub index rendering and forced logout via cookie tossing;
  - plain-HTTP non-loopback use (loud warning; enrollment refused);
  - lockout used as a DoS (bounded; `unlock`);
  - the phantom-eviction oracle (bounded);
  - (v2.1) a config-sourced TOTP policy can be lowered by a repository change (warned and audited;
    pin it with env/CLI); users behind an unconfigured proxy share one throttle bucket until
    `trusted_proxies` is set.
- **Follow-ups this ADR enables without redesign, in priority order:**
  1. the hub-run handoff;
  2. store-scoped SSO;
  3. RBAC on `Principal.roles`;
  4. API tokens;
  5. OIDC, LDAP and trusted-header providers;
  6. WebAuthn (needs a canonical hostname);
  7. fail-closed remote binds;
  8. the hub showing child auth state;
  9. at-rest TOTP seed encryption;
  10. native TLS flags.

---

## Amendments (as built, 2026-10-05)

Decisions D1–D11 stand. These amendments record what implementation and the security review
changed. Code references and the remaining deviations are in the HLD's "As built" section.

1. **D8 (settings and service enablement; HLD OQ-3 reversed).** `ao service install --auth` now
   exists and bakes ` --auth` into the unit's ExecStart (T-2wE08U M-1). Rationale: enablement by
   `service.env` alone **fails open** on a stale global `ao` snapshot (it ignores the unknown
   `AO_UI_AUTH`), whereas an old binary *rejects* an unknown `--auth` flag, so the flag fails
   closed. `service.env` stays supported; `install` prints the alternative and a stale-snapshot
   warning.
2. **D8 (stale installs).** The mitigation command is `bash install.sh --force` (it passes
   `--reinstall` to `uv` internally), verified by `ao auth status`; there is no
   `install.sh --reinstall` flag.
3. **D8 (`trusted_proxies`).** Environment-only (`AO_UI_AUTH_TRUSTED_PROXIES`); no CLI flag exists.
   The workspace-file key is refused with a message naming the variable.
4. **D8 (config-only weakening, security M3).** The detector probes accounts in the resolved store
   **and** in the config-ignored store(s), and treats an unknown count as "accounts may exist"
   (T-2wE08U H1). Otherwise a hostile `store_dir` pointing at an empty directory could hide the
   accounts and let a config-only `enabled: false` start an unauthenticated dashboard.
5. **D3 (store durability).** `users.json` and `lockouts.json` reads are capped at
   `STORE_FILE_MAX_BYTES` (16 MiB); an over-cap file is corrupt (count probe: unknown; lockouts:
   fail closed).
6. **D1 (sessions).** `destroy_user_sessions` revokes only the manager's own realm; other realms
   die by the credential-epoch bump (as §11.10 specifies). `SessionManager.lookup` additionally
   ignores a record whose realm differs from its own.
7. **D4/D5 (log hygiene).** Redaction of uvicorn access records keeps a tuple `args` of the same
   length (numeric slots kept, the redacted line in the first string slot, other slots emptied), so
   the access formatter never fails and no secret fragment survives (H2, N1).
8. **D6 (identity contract), confirmed.** As built: `Principal.roles: list[str]`
   (`hash=False`, a fresh list per principal), additive fields keyword-only (HLD OQ-8).
9. **D11 (session proof), confirmed.** It shipped in the MVP and was not cut (HLD OQ-9, no
   cut-line taken).
10. **D1 (hub).** A malformed `/auth-assets/` path matches no route, so an anonymous caller gets
    401 rather than 404 (never served). A pre-existing hub `/openapi.json` 500 was fixed on the
    way.

### Residual risks accepted at sign-off (supplements "Consequences summary")

`start_run` accepts an absolute `workflow_path`; a local process can claim a client address via
`X-Forwarded-For` under `trusted_proxies=127.0.0.1`; account-lockout DoS and a shared throttle
bucket behind an unconfigured proxy; IPv6 throttling per `/64`; the phantom-eviction oracle; a
hard link bypasses the file-browser path denial; a stale binary fails open when auth is enabled
only through `service.env`; per-session counters are not concurrency-exact; TOTP seeds in clear in `users.json`. (The `dompurify` advisory was resolved on 2026-10-06 by bumping to 3.4.16.) The real-browser smoke (HLD AC-34) was not
run at close; on 2026-10-06 it ran and passed (3 passed, Chrome 138; hub-page and Firefox/WebKit cases still unwritten). Full table:
[HLD "As built" section D](../dashboard-auth-hld.md#as-built-2026-10-05).
