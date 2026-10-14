# Dashboard authentication (user guide)

Opt-in login for the `ao ui` dashboard and the `ao service` hub: local accounts, a password
(scrypt-hashed) and an optional TOTP second factor. It is **off by default**; upgrading changes
nothing until you turn it on.

> Design and rationale: [`dashboard-auth-hld.md`](dashboard-auth-hld.md) (as-built state in its
> "As built" section) and [ADR-0021](adr/ADR-0021-dashboard-authentication-and-totp.md).
> The short version of this page is the *Authentication* subsection of the
> [README](../README.md#authentication).

## 1. What it protects, and what it does not

- **Authenticated means full access.** There are no roles: a signed-in user can browse every
  workspace file the dashboard can read, and can start, resume, cancel and delete runs (which
  execute agents as your OS user and spend API budget).
- **Same-user processes are out of scope.** Anything running as your OS user (including the
  agents `ao` launches) can read the credential and state directories directly. The dashboard
  never *serves* those directories (see [File browser](#9-the-file-browser-never-serves-the-stores)),
  but file permissions (`0700`/`0600`) are the only protection against a local process.
- **A harvested session cookie cannot call the API.** Every API request also needs a per-session
  proof header (`X-AO-Session-Proof`) that lives in the page, not in a cookie. Remaining local-listener
  residuals: a cookie alone still renders the hub's index page (the workspace list), and anyone who
  can plant a cookie for the host can force a logout.
- **Plain HTTP on a network is not safe.** Passwords and cookies cross the wire in clear. Use a TLS
  reverse proxy for anything but loopback ([section 6](#6-reverse-proxies-and-tls)).

## 2. Quick start

```bash
ao auth add-user alice          # prompts for a password (min length 12); creates the store
ao ui --auth                    # exits 78 if there are no accounts
```

Other ways to enable it (precedence: CLI > env > workspace `.ao/config.yaml` > default):

| Where | How |
|---|---|
| CLI | `ao ui --auth` / `--no-auth`, `--auth-totp off\|optional\|required`, `--auth-dir DIR` |
| Environment | `AO_UI_AUTH=1`, `AO_UI_AUTH_TOTP=required`, `AO_AUTH_DIR=DIR` |
| Workspace config | `ui.auth.enabled: true` (see [section 4](#4-configuration)) |
| Service | `ao service install --auth`, or `AO_UI_AUTH=1` in `~/.config/ao/service.env` ([section 7](#7-the-ao-service-hub-and-systemd)) |

Boolean env values: `1/true/yes/on` and `0/false/no/off`. An auth variable that is set but empty
or unparseable is an error (exit 78), never silently ignored.

`ao auth status` shows every effective setting and where it came from, the store/state paths and
permissions, account counts and recent startup events. Run it after any change.

**Restrictions with auth on:** `--port 0` is refused (the cookie is named after the port); the
framework API docs pages (`/api/docs`, the hub's `/docs`) cannot work in a browser (a page
navigation cannot send the proof header) -- browse the API docs with auth off locally.

## 3. Accounts and the `ao auth` commands

Account administration is **CLI-only** (there is no self-registration). All commands accept
`--auth-dir DIR` and `-w/--workspace DIR` (whose `ui.auth` config applies, as for `ao ui`) and
print `store:` / `state:` paths first. No command takes a secret as an option.

| Command | What it does |
|---|---|
| `ao auth add-user USER [--password-stdin] [--require-totp]` | Create an account (creates the store on first use). `--require-totp` marks the user as requiring a second factor and prints an enrollment token. |
| `ao auth set-password USER [--password-stdin]` | New password; revokes the account's sessions. |
| `ao auth list-users` | Table of accounts (never prints hashes, seeds or tokens). |
| `ao auth remove-user USER [--yes] [--force]` | Delete an account and its lockout state. `--force` allows removing the last account while auth is enabled. |
| `ao auth enable-2fa USER` | Interactive TOTP enrollment on the host: prints the secret, asks for a confirming code, prints 10 recovery codes once. |
| `ao auth disable-2fa USER [--yes]` | Remove TOTP and clear a per-user TOTP requirement. |
| `ao auth reset-2fa USER [--yes]` | Lost device: remove TOTP, require re-enrollment, print a fresh one-time enrollment token. |
| `ao auth enrollment-token USER` | Issue a one-time enrollment token (valid 60 minutes; replaces any earlier one). |
| `ao auth unlock USER` | Clear a login lockout (also repairs a corrupt `lockouts.json`). |
| `ao auth revoke-sessions USER` | Revoke every session of the account. |
| `ao auth status [--json]` | Effective settings with sources, permissions, counts, notes. |

Users sign in at the dashboard login screen and manage their own password, two-factor and
recovery codes from the account menu; "log out everywhere" is there too.

## 4. Configuration

All settings can come from the CLI, the environment, or the workspace config (`.ao/config.yaml`,
`ui.auth` block). `ao init` writes a commented example.

| Setting | Env | Default | Notes |
|---|---|---|---|
| `enabled` | `AO_UI_AUTH` | off | CLI `--auth/--no-auth` |
| `totp` | `AO_UI_AUTH_TOTP` | `off` | `off`, `optional`, `required`; CLI `--auth-totp` |
| `session_idle_minutes` | `AO_UI_AUTH_IDLE_MINUTES` | 30 | idle expiry; only user *activity* extends it (polling does not) |
| `session_absolute_hours` | `AO_UI_AUTH_ABSOLUTE_HOURS` | 12 | hard limit |
| `lockout_threshold` | `AO_UI_AUTH_LOCKOUT_THRESHOLD` | 5 | failures before the account is locked |
| `lockout_base_seconds` / `lockout_max_seconds` | `AO_UI_AUTH_LOCKOUT_BASE_SECONDS` / `..._MAX_SECONDS` | 30 / 900 | exponential backoff, capped at 15 min |
| `address_threshold` | `AO_UI_AUTH_ADDRESS_THRESHOLD` | 20 | per-client-address failures per 15 min |
| `min_password_length` | `AO_UI_AUTH_MIN_PASSWORD_LENGTH` | 12 | floor 8 |
| `totp_issuer` | `AO_UI_AUTH_TOTP_ISSUER` | `ao@<hostname>` | label in the authenticator app |
| `store_dir` | `AO_AUTH_DIR` | `~/.config/ao/auth` | credential directory (CLI `--auth-dir`) |
| (state dir) | `AO_AUTH_STATE_DIR` | `~/.local/state/ao/auth` (follows `store_dir` as `<store>/state` if that is overridden) | lockouts and audit log |
| `trusted_proxies` | `AO_UI_AUTH_TRUSTED_PROXIES` | none | **env only**; comma-separated proxy addresses |

`$XDG_CONFIG_HOME` / `$XDG_STATE_HOME` are honoured for the two defaults.

**The workspace config may only tighten.** A cloned repository's config is attacker-influenced
input, so a value that weakens a default (longer sessions, a higher lockout threshold, a shorter
minimum password, `trusted_proxies`, ...) is refused at startup (exit 78) with a message naming the
env/CLI setting to use instead. Lowering session lifetimes or raising lockout severity in the config is fine.

Two configuration traps are guarded:

- A workspace-config-only `enabled: false` **refuses to start** (exit 78) while accounts exist
  (a `git pull` must not silently disable login). To disable on purpose, pass `--no-auth` or set
  `AO_UI_AUTH=0`; the dashboard then starts with a note that accounts exist.
- A TOTP policy below `required` that comes **only** from the config file logs a warning (and an
  audit event). Pin the policy with `AO_UI_AUTH_TOTP` / `--auth-totp` if you rely on it.

A config-chosen `store_dir` is never created or `chmod`-ed by `ao auth`; create it yourself
(`mkdir -m 700`) or pass `--auth-dir`.

## 5. Two-factor (TOTP) policies, recovery codes, lost devices

Policy (`totp`):

| Policy | Behaviour |
|---|---|
| `off` | Web enrollment is refused (`totp_disabled_by_policy`) and TOTP is not required. **Users who are already enrolled are still asked for a code** (enrollment is sticky: lowering the policy must not drop a second factor). |
| `optional` | Users may enroll from the account menu (current password needed), and may disable their own second factor (password + a current code); enrolled users are challenged at login. |
| `required` | Every user must have a second factor and cannot disable it from the account menu (`totp_required`). A user who has not enrolled **needs an operator-issued enrollment token at first login** (`ao auth enrollment-token USER`), even if the account was created without `--require-totp` -- a stolen password alone must not be able to bind an authenticator. |

`ao auth status` prints the same notes. A per-user requirement (`add-user --require-totp`) under
policy `off` is refused at login (`totp_required`): raise the policy, or `ao auth disable-2fa`.

**Switching an existing deployment to `required`:** for each user who is not enrolled, run
`ao auth enrollment-token USER` and hand over the printed token. The user signs in with their
password, enters the token (valid 60 minutes, single use), scans the QR code (or types the secret)
and confirms a code; the ten recovery codes are shown once. Alternatively enroll them on the host
with `ao auth enable-2fa USER`.

**Remote enrollment needs a safe transport.** Enrollment is refused (`403 insecure_transport`)
from a non-loopback client over plain HTTP; enroll over TLS, on the host, or through an SSH
tunnel to loopback.

**Recovery codes.** Ten single-use codes (shown once, stored only as salted hashes). At the
second-factor step choose "use a recovery code". Case, dashes and spaces are ignored. The account
menu can regenerate the set (needs the current password and a current code). Each code works once; a login with a
recovery code shows how many remain.

**Lost device.** Sign in with a recovery code and re-enroll from the account menu, or the operator
runs `ao auth reset-2fa USER` and gives the user the printed enrollment token.

**TOTP details.** SHA-1, 6 digits, 30 s step, one step of clock skew tolerated; a code can be used
**once globally** (the same code is a replay in every realm). If codes are rejected, the clock is
off: `ao auth status` prints the server's UTC time.

## 6. Reverse proxies and TLS

`ao` serves plain HTTP; it has no TLS flags. For remote access put a TLS reverse proxy in front
(Caddy, nginx, ...) and:

1. Set `AO_UI_AUTH_TRUSTED_PROXIES` to the proxy's address (env only), e.g. `127.0.0.1`.
2. Make the proxy **preserve the `Host` header** (nginx: `proxy_set_header Host $host;`) and send
   `X-Forwarded-Proto`, and `X-Forwarded-For`.
3. Cookies then become `Secure` with the `__Host-` prefix automatically.

If you forget step 1, requests count as remote (so enrollment is refused), the server logs one
`proxy_suspected` warning naming `AO_UI_AUTH_TRUSTED_PROXIES`, and every remote user shares the
proxy's throttle bucket.

With `trusted_proxies=127.0.0.1`, **any local process** can send its own `X-Forwarded-For` to the
app port and choose its client address (the per-address throttle then applies to the claimed
address; the per-account lockout still applies). Bind the app to a unix socket, or firewall the app
port so only the proxy reaches it.

A non-loopback bind without TLS prints a loud `traffic is not encrypted` warning. A non-loopback
bind **without** auth prints a deprecation notice (a future release will refuse it).

## 7. The `ao service` hub and systemd

The hub (`:8770`) and every workspace dashboard it supervises can share one login configuration.

```bash
ao auth add-user alice
ao service install --auth        # bakes --auth into the unit's ExecStart (preferred)
systemctl --user restart ao
```

or put `AO_UI_AUTH=1` (and optionally `AO_UI_AUTH_TOTP=required`) in `~/.config/ao/service.env`
and restart. CLI-sourced values are relayed to the supervised children; the children also inherit
the environment. `ao service run` accepts `--auth/--no-auth`, `--auth-totp`, `--auth-dir`.

- **Env-only enablement fails open on a stale binary.** An `ao` snapshot that predates this
  feature silently ignores `AO_UI_AUTH` in `service.env` and starts the dashboards
  **unauthenticated, without any error**. `ao service install --auth` is safer because an old
  binary rejects the unknown flag. After any upgrade or restart, verify with `ao auth status`
  or `GET /api/auth/status` (`"enabled": true`).
- **Exit code 78 is terminal.** A configuration error (auth on with no accounts, loose store
  permissions, an invalid `ui.auth` block, `--port 0`) exits **78**. The unit carries
  `RestartPreventExitStatus=78`, so systemd does not restart-loop a bad config; a supervised child
  that exits 78 is marked `stopped` (`configuration error: see <log>`) with no backoff and no
  respawn. Fix the cause, then restart. Re-run `ao service install` to pick up the unit line;
  an older unit still works but gives up after its start-limit burst.
- `ao service list` / `status` show `login required` for the live view when hub auth is on (they
  read persisted state without credentials; the browser shows live state).

## 8. Hub vs workspace logins (realms)

Browsers scope cookies by host, **not port**, so every local `ao` listener would receive every
other listener's cookie. Each server therefore has its own **realm**: the hub, and one per
workspace dashboard, each with its own in-memory sessions, its own cookie (`ao_sid_<port>`) and its
own login. Signing in to the hub does not sign you in to a dashboard (single sign-on across realms
is a roadmap follow-up). Sessions live in memory, so restarting a server signs everyone out.
Use one host name consistently (`localhost` and `127.0.0.1` are different cookie hosts).

## 9. The file browser never serves the stores

The dashboard file browser and previews refuse the credential directory, the state directory, any
`AO_AUTH_DIR` / `AO_AUTH_STATE_DIR` override, and `~/.config/ao/service.env` (which can hold
environment secrets) -- even for a signed-in user, and even if a workspace contains them. Known
gap: the check is by resolved path, so a **hard link** to one of these files elsewhere in the
workspace would be served (a same-user process creating it is out of scope).

## 10. Files, backups and the audit log

| Path | Holds | Notes |
|---|---|---|
| `~/.config/ao/auth/users.json` (`0600`, dir `0700`) | account records: password hashes, **TOTP seeds in clear**, recovery-code hashes | the credential store |
| `~/.local/state/ao/auth/lockouts.json`, `audit.jsonl` | lockout counters, one JSON audit line per auth event (rotated at 10 MiB, 5 backups) | safe to lose |

> **The TOTP seed is stored in clear next to the password hash.** A stolen `users.json` (or backup)
> hands over the second factor along with the hashes. Back the credential directory up
> **encrypted only**. Seeds are not encrypted at rest (deferred: key custody is an owner decision; see the HLD "As built" section D). Restoring an older
> `users.json` lowers credential epochs, which invalidates newer sessions.

Permissions are checked at startup, by `ao auth status`, and again while running (on login and, throttled, on session revalidation; a loosened store fails closed and is audited); a loose store refuses to start
(exit 78: `chmod 700 <dir> && chmod 600 <dir>/users.json`). Network or other file systems without
`flock` are unsupported. Logs and audit lines never contain passwords, codes, seeds, tokens or
session proofs.

## 11. Upgrading and rolling back

`ao` installed via `install.sh` is a **non-editable snapshot**. A build that predates this feature
rejects `--auth` but **silently ignores** `AO_UI_AUTH` and the `ui.auth` config. After upgrading:
`bash install.sh --force` (it rebuilds the snapshot with `uv ... --reinstall`; `--check` reports staleness), then `ao auth status` (it only exists in new builds), then restart
`ao.service`. To roll back: `AO_UI_AUTH=0` / `--no-auth` and restart (the store stays on disk). Downgrading the binary has
the stale-install effect (auth off, silently).

## 12. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ao ui` exits 78 "... has no accounts" | auth on, empty store | `ao auth add-user NAME` (with `--auth-dir` if shown) |
| exit 78 "permissions ... chmod 600" | store copied with lax modes | `chmod 700 ~/.config/ao/auth && chmod 600 ~/.config/ao/auth/users.json` |
| exit 78 "invalid ui.auth configuration ... Extra inputs are not permitted" | typo in a key | fix the key (`enabled`) |
| exit 78 "... would weaken the default" | the workspace config tried to loosen a setting | set it with env/CLI instead |
| exit 78 "ui.auth.enabled=false in <config> while N account(s) exist" | config disables auth while accounts exist | revert the config, or on purpose `--no-auth` / `AO_UI_AUTH=0` |
| warning "totp=... comes only from <config>" | policy depends on a repository file | pin with `AO_UI_AUTH_TOTP` / `--auth-totp` |
| exit 78 "--port 0 cannot be used with dashboard authentication" | ephemeral port | choose a fixed port |
| exit 78 "ui.auth.store_dir from <config> must already exist and be private" | a config chose the store dir | `mkdir -m 700` it, or pass `--auth-dir` |
| warning "<parent> is group-writable" | umask 002 host | optional `chmod g-w <parent>` |
| "That code didn't work" | clock skew | `ao auth status` shows server UTC; enable NTP |
| "This code was already used" | the same code was used in another realm in the same 30 s | wait for the next code |
| 429 "Too many attempts" | lockout | wait (at most 15 min) or `ao auth unlock USER` |
| "Enter the one-time enrollment token" | forced enrollment (policy `required` or `--require-totp`) | `ao auth enrollment-token USER` |
| 403 `insecure_transport` | enrolling over plain HTTP from another machine | enroll on the host, over TLS, or via an SSH tunnel |
| 403 `totp_required` at login | per-user requirement but policy `off` | raise the policy or `ao auth disable-2fa USER` |
| warning "requests from 127.0.0.1 carry proxy headers" / status `proxy_suspected` | proxy not in `trusted_proxies` | set `AO_UI_AUTH_TRUSTED_PROXIES` |
| signed out on every reload | browser blocks site storage (the proof cannot persist) | allow site storage for the origin |
| signed out on every restart | sessions are in memory | expected |
| logged in at `localhost:8765`, not at `127.0.0.1:8765` | different cookie hosts | use one host name |
| `/api/docs` shows a login page or 401 | doc pages cannot send the proof | browse API docs with auth off locally |
| `ao service status` says "login required" | hub auth is on | expected |
| a child shows `stopped`, "configuration error (exit 78)" | that workspace's config/store is invalid | read the child log shown, fix, restart |
| dashboards started unauthenticated after enabling via `service.env` | stale `ao` snapshot ignores the variable | `bash install.sh --force`, `ao service install --auth`, verify with `ao auth status` |

## 13. Known limitations

Accepted residuals and follow-ups are listed in the HLD's "As built" section
([`dashboard-auth-hld.md`](dashboard-auth-hld.md#as-built-2026-10-05)): scripted API access
must repeat the browser handshake (log in, keep the cookie, send the returned proof header; API
tokens are a follow-up), no RBAC, no cross-realm SSO, no hub
view of child auth state, seeds in clear, IPv6 clients are throttled per `/64`.

## Release note

*Dashboard authentication (opt-in).* New `ao auth` command group; `ao ui` and `ao service run`
gain `--auth`, `--auth-totp`, `--auth-dir`; `ao service install --auth`; unit files gain
`RestartPreventExitStatus=78`. Nothing changes unless you enable it. A stale global `ao` ignores
`AO_UI_AUTH` / `ui.auth` silently: run `bash install.sh --force` and verify with `ao auth status`.
