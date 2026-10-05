"""``ao auth``: the only account-administration and enrollment-token surface (HLD 11.19, L4).

Import rule (R1/R4, developer D-8): module scope imports only ``typer``, the L0 constants and
errors, and the standard library. Everything else (store, passwords, totp, lockouts, audit,
settings, paths, policy) is imported inside function bodies, so ``ao --help`` startup is
unaffected and ``ao auth`` works without fastapi/starlette/uvicorn (NFR-3). Nothing here imports
``auth/http``.

Secrets: passwords come only from a hidden prompt or ``--password-stdin``; no option or
environment variable carries a password, token or code. Enrollment tokens and recovery codes are
printed once to stdout and never logged or audited.

Exit codes: 0 success; 1 operational error; ``EXIT_CONFIG`` (78) for any ``AuthConfigError``
(invalid env/config, tighten-only violations, unsafe permissions).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import sys
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from agent_orchestrator.errors import EXIT_CONFIG

from .constants import (
    AUDIT_FILENAME,
    CLI_REALM,
    CLI_TOTP_CONFIRM_ATTEMPTS,
    ENROLLMENT_TOKEN_TTL_SECONDS,
    LOCKOUTS_FILENAME,
    MAX_LOGIN_PASSWORD_CHARS,
    SOURCE_CLI,
    USERNAME_PATTERN,
    USERS_FILENAME,
)
from .errors import AuthConfigError

if TYPE_CHECKING:
    from .audit import AuditLog
    from .lockouts import LockoutStore
    from .model import AuditEventName
    from .seams import Clock, Entropy
    from .settings import AuthSettings
    from .store import UserRecord, UserStore, UserStoreFile

_log = logging.getLogger(__name__)

EXIT_FAILURE = 1
STORE_NOW_BANNER = "=== STORE THESE NOW — shown only once ==="
RECOVERY_CODE_GROUPING = 4  # the secret is shown in groups of this many characters
RECENT_STARTUP_EVENTS = 5
STARTUP_EVENT_PREFIX = "auth.startup."
STATUS_PLACEHOLDER = "-"
_USERNAME_RE = re.compile(USERNAME_PATTERN)
_USERNAME_RULE = (
    "lowercase letters, digits, '.', '_' and '-'; 1 to 32 characters; "
    "starting with a letter or digit"
)

app = typer.Typer(
    name="auth",
    help="Manage dashboard accounts, TOTP enrollment and lockouts (local, CLI-only bootstrap).",
    no_args_is_help=True,
    add_completion=False,
)

AuthDirOption = Annotated[
    str | None,
    typer.Option(
        "--auth-dir",
        help="Credential store directory (default: the ui.auth / AO_AUTH_DIR / XDG resolution).",
    ),
]
WorkspaceOption = Annotated[
    str | None,
    typer.Option(
        "--workspace",
        "-w",
        help=(
            "Workspace whose ui.auth config applies (default: $AO_WORKSPACE_ROOT or the current "
            "directory), exactly like `ao ui`."
        ),
    ),
]
PasswordStdinOption = Annotated[
    bool,
    typer.Option(
        "--password-stdin",
        help="Read the password from the first line of stdin instead of prompting.",
    ),
]
YesOption = Annotated[bool, typer.Option("--yes", help="Do not ask for confirmation.")]


class CliFailure(Exception):
    """An operational error with an operator-facing message (exit 1 unless ``code`` says so)."""

    def __init__(self, message: str, code: int = EXIT_FAILURE) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass
class _Ctx:
    """Everything one command needs, built once by :func:`_open`."""

    settings: AuthSettings
    users: UserStore
    lockouts: LockoutStore
    audit: AuditLog
    clock: Clock
    entropy: Entropy


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def _classify(exc: BaseException) -> tuple[str, int] | None:
    """Map a known failure to ``(operator message, exit code)``; ``None`` = not ours."""
    from agent_orchestrator import fsutil

    from . import store as st
    from .errors import StoreCorruptError, StoreLockTimeoutError, StoreUnavailableError

    if isinstance(exc, CliFailure):
        return exc.message, exc.code
    if isinstance(exc, AuthConfigError | fsutil.UnsafePathError):
        return str(exc), EXIT_CONFIG
    if isinstance(exc, typer.Abort):
        return "aborted (no input)", EXIT_FAILURE
    if isinstance(exc, StoreLockTimeoutError):
        return (
            f"the store is busy (locked by another ao process); try again ({exc})",
            EXIT_FAILURE,
        )
    if isinstance(exc, StoreUnavailableError):
        return f"{exc} ({exc.cause_for_log or 'no detail'})", EXIT_FAILURE
    if isinstance(exc, st.StoreMissingError):
        return f"{exc}; create the first account with `ao auth add-user`", EXIT_FAILURE
    if isinstance(exc, st.AlreadyEnrolledError):
        return f"{exc}; run `ao auth reset-2fa <user>` first", EXIT_FAILURE
    if isinstance(exc, st.StaleIdentityError):
        return "the account changed concurrently; retry", EXIT_FAILURE
    if isinstance(
        exc,
        st.UserExistsError
        | st.UserNotFoundError
        | st.NotEnrolledError
        | st.TooManyUsersError
        | StoreCorruptError,
    ):
        return str(exc), EXIT_FAILURE
    return None


@contextlib.contextmanager
def _errors() -> Iterator[None]:
    """The boundary: debug-log the chain, print one ``ERROR:`` line, exit with the mapped code."""
    try:
        yield
    except typer.Exit:
        raise
    except Exception as exc:  # typer.Abort is an Exception too
        mapped = _classify(exc)
        if mapped is None:
            raise
        message, code = mapped
        _log.debug("ao auth failed", exc_info=True)
        typer.echo(f"ERROR: {message}", err=True)
        raise typer.Exit(code) from exc


# ---------------------------------------------------------------------------
# Settings, directories and the command context
# ---------------------------------------------------------------------------


def _resolve_settings(auth_dir: str | None, workspace: str | None) -> AuthSettings:
    from .settings import AuthCliOverrides, resolve_auth_settings

    if auth_dir is not None and not auth_dir.strip():
        raise AuthConfigError("--auth-dir must not be empty")
    root = Path(workspace or os.environ.get("AO_WORKSPACE_ROOT") or os.getcwd()).resolve()
    if not root.is_dir():
        raise CliFailure(f"workspace root is not a directory: {root}")
    return resolve_auth_settings(
        cli=AuthCliOverrides(store_dir=auth_dir), env=os.environ, workspace_root=root
    )


def _notice(text: str) -> None:
    typer.echo(f"NOTICE: {text}", err=True)


def _config_store_error(settings: AuthSettings, detail: str) -> AuthConfigError:
    return AuthConfigError(
        f"ui.auth.store_dir from {settings.config_path} must already exist and be private "
        "(0700, owned by you); create it yourself, or pass --auth-dir explicitly "
        f"({detail})"
    )


def _prepare_mutating(settings: AuthSettings) -> None:
    """Create/tighten (or, for a config-sourced store, only verify) the directories (HLD 11.5)."""
    from agent_orchestrator import fsutil

    store, state = settings.store_dir, settings.state_dir
    users_file, lockouts_file = store / USERS_FILENAME, settings.state_dir / LOCKOUTS_FILENAME
    notices: list[str] = []
    try:
        if settings.store_dir_from_config:
            # Security M6: a cloned repository chose this path; never create or chmod it.
            try:
                notices += fsutil.ensure_private_dir(store, create=False, fix=False)
            except FileNotFoundError as exc:
                raise _config_store_error(settings, f"{store} does not exist") from exc
            except fsutil.UnsafePathError as exc:
                raise _config_store_error(settings, str(exc)) from exc
            fix = False
        else:
            notices += fsutil.ensure_private_dir(store, create=True, fix=True)
            fix = True
        # Only <store>/state may be created inside the verified directory; never chmod-ed.
        notices += fsutil.ensure_private_dir(state, create=True, fix=fix)
        for path in (users_file, lockouts_file):
            with contextlib.suppress(FileNotFoundError):
                notices += fsutil.check_private_file(path, fix=fix)
    except fsutil.UnsafePathError as exc:
        raise AuthConfigError(str(exc)) from exc
    for text in notices:
        _notice(text)


def _prepare_readonly(settings: AuthSettings) -> None:
    """Read-only commands never fix anything; a problem is reported, not raised."""
    from agent_orchestrator import fsutil

    try:
        for text in fsutil.ensure_private_dir(settings.store_dir, create=False, fix=False):
            _notice(text)
    except FileNotFoundError:
        return
    except fsutil.UnsafePathError as exc:
        typer.echo(f"WARNING: {exc}", err=True)


def _begin(auth_dir: str | None, workspace: str | None, *, header_to_stderr: bool = False) -> _Ctx:
    from .audit import AuditLog
    from .lockouts import LockoutStore
    from .paths import StorePaths
    from .seams import SYSTEM_CLOCK, SYSTEM_ENTROPY
    from .store import UserStore

    settings = _resolve_settings(auth_dir, workspace)
    typer.echo(f"store: {settings.store_dir}", err=header_to_stderr)
    typer.echo(f"state: {settings.state_dir}", err=header_to_stderr)
    paths = StorePaths.at(settings.store_dir, settings.state_dir)
    return _Ctx(
        settings=settings,
        users=UserStore(paths, clock=SYSTEM_CLOCK, entropy=SYSTEM_ENTROPY),
        lockouts=LockoutStore(paths, clock=SYSTEM_CLOCK, entropy=SYSTEM_ENTROPY),
        audit=AuditLog(paths, clock=SYSTEM_CLOCK),
        clock=SYSTEM_CLOCK,
        entropy=SYSTEM_ENTROPY,
    )


def _open(auth_dir: str | None, workspace: str | None, *, mutating: bool) -> _Ctx:
    """Resolve settings, print the ``store:`` header, prepare directories (and the name key)."""
    from .errors import StoreCorruptError, StoreLockTimeoutError, StoreUnavailableError

    ctx = _begin(auth_dir, workspace)
    for text in ctx.settings.warnings:
        typer.echo(f"WARNING: {text}", err=True)
    if not mutating:
        _prepare_readonly(ctx.settings)
        return ctx
    _prepare_mutating(ctx.settings)
    try:
        ctx.lockouts.ensure_name_key()  # v2.1, security L1: the phantom/audit HMAC key
    except (StoreUnavailableError, StoreCorruptError, StoreLockTimeoutError) as exc:
        # A broken lockouts.json must not block unrelated account work; `unlock` repairs it.
        typer.echo(
            f"WARNING: lockouts.json is unusable ({exc}); servers refuse logins until "
            "`ao auth unlock <user>` repairs it",
            err=True,
        )
    return ctx


# ---------------------------------------------------------------------------
# Small helpers shared by several commands
# ---------------------------------------------------------------------------


def _normalize_username(raw: str) -> str:
    """NFKC, strip, lower (the login normalization, HLD 11.9)."""
    return unicodedata.normalize("NFKC", raw).strip().lower()


def _valid_username(raw: str) -> str:
    name = _normalize_username(raw)
    if not _USERNAME_RE.fullmatch(name):
        raise CliFailure(f"invalid username: it must be {_USERNAME_RULE}")
    return name


def _require_user(ctx: _Ctx, username: str) -> UserRecord:
    from .store import UserNotFoundError

    rec = ctx.users.snapshot().users.get(username)
    if rec is None:
        raise UserNotFoundError(f"no such user: {username}")
    return rec


def _read_password(from_stdin: bool) -> str:
    """Hidden prompt with confirmation (click re-prompts on a mismatch, EOF aborts) or stdin."""
    if not from_stdin:
        password: str = typer.prompt("Password", hide_input=True, confirmation_prompt=True)
        return password
    # Bounded read: a password longer than the login limit is rejected by the policy anyway.
    line = sys.stdin.readline(MAX_LOGIN_PASSWORD_CHARS + 2)
    if line == "":
        raise CliFailure("no password on stdin (end of input)")
    if line.endswith("\r\n"):
        line = line[:-2]
    elif line.endswith("\n"):
        line = line[:-1]
    if not line:
        raise CliFailure("the password on stdin is empty")
    return line


def _checked_password(ctx: _Ctx, username: str, from_stdin: bool) -> str:
    """Read a password and enforce the policy; returns its hash (never the password)."""
    from . import passwords

    raw = _read_password(from_stdin)
    policy = passwords.PasswordPolicy(ctx.settings.min_password_length)
    violations = policy.violations(raw, username=username)
    if violations:
        raise CliFailure(
            f"password rejected by policy: {', '.join(violations)} "
            f"(minimum length {ctx.settings.min_password_length})"
        )
    # CURRENT_PARAMS is read at call time so tests can swap in cheap parameters.
    return passwords.hash_password(raw, params=passwords.CURRENT_PARAMS)


def _audit(
    ctx: _Ctx, event: AuditEventName, rec_username: str, user_id: str, **details: str | int | bool
) -> None:
    from .audit import AuditEvent, AuditOutcome

    ctx.audit.record(
        AuditEvent(
            event,
            AuditOutcome.SUCCESS,
            username=rec_username,
            user_id=user_id,
            realm=CLI_REALM,
            details={"source": SOURCE_CLI, **details},
        )
    )


def _confirm(prompt: str, yes: bool) -> None:
    if not yes:
        typer.confirm(prompt, abort=True)


def _show_token(username: str, token: str, expires_at: str) -> None:
    typer.echo(STORE_NOW_BANNER)
    typer.echo(
        f"Enrollment token for {username} (single use; expires {expires_at}, "
        f"valid {ENROLLMENT_TOKEN_TTL_SECONDS // 60} minutes). The user enters it at the "
        "dashboard login after their password:"
    )
    typer.echo(token)


def _policy_source(ctx: _Ctx) -> str:
    return f"{ctx.settings.totp.value!r} from {ctx.settings.sources['totp']}"


def _token_policy_note(ctx: _Ctx, rec: UserRecord) -> None:
    """Notes, never refusals: the server's effective policy may come from another env (AC-6)."""
    from .model import TotpRequirement
    from .policy import totp_requirement

    requirement = totp_requirement(ctx.settings.totp, rec.totp_required)
    if requirement is TotpRequirement.NONE:
        typer.echo(
            f"NOTE: with ui.auth.totp {_policy_source(ctx)} and totp_required=false for "
            f"{rec.username}, TOTP enrollment is not currently required; the token only matters "
            "if the server runs with totp=required (for example from another environment)."
        )
    elif requirement is TotpRequirement.BLOCKED:
        typer.echo(
            f"NOTE: ui.auth.totp is {_policy_source(ctx)} but {rec.username} requires TOTP: "
            "the user cannot log in until the policy is set to optional or required "
            "(AO_UI_AUTH_TOTP) or you clear the requirement with "
            f"`ao auth disable-2fa {rec.username}`."
        )


def _issue_token(ctx: _Ctx, f: UserStoreFile, username: str) -> tuple[str, str]:
    """Issue inside a mutate: returns ``(token, expires_at)`` (the token is shown once)."""
    from .store import issue_enrollment_token

    token = issue_enrollment_token(f, username, entropy=ctx.entropy, now=ctx.clock.now_utc())
    stored = f.users[username].enrollment_token
    assert stored is not None  # issue_enrollment_token just set it
    return token, stored.expires_at


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command("add-user")
def add_user_cmd(
    username: Annotated[str, typer.Argument(help="Login name (normalized to lowercase).")],
    password_stdin: PasswordStdinOption = False,
    require_totp: Annotated[
        bool,
        typer.Option(
            "--require-totp", help="Require TOTP for this user and print an enrollment token."
        ),
    ] = False,
    auth_dir: AuthDirOption = None,
    workspace: WorkspaceOption = None,
) -> None:
    """Create an account (creates the store on first use)."""
    from . import store as st
    from .model import AuditEventName

    with _errors():
        ctx = _open(auth_dir, workspace, mutating=True)
        name = _valid_username(username)
        password_hash = _checked_password(ctx, name, password_stdin)
        now = ctx.clock.now_utc()

        def op(f: st.UserStoreFile) -> tuple[str, tuple[str, str] | None]:
            rec = st.add_user(
                f,
                name,
                password_hash,
                now=now,
                entropy=ctx.entropy,
                totp_required=require_totp,
            )
            return rec.user_id, _issue_token(ctx, f, name) if require_totp else None

        user_id, issued = ctx.users.mutate(op, create=True)
        _audit(ctx, AuditEventName.USER_ADDED, name, user_id)
        typer.echo(f"Created user '{name}'.")
        if issued is not None:
            _audit(ctx, AuditEventName.ENROLLMENT_TOKEN_ISSUED, name, user_id)
            _show_token(name, *issued)
            _token_policy_note(ctx, ctx.users.snapshot().users[name])
        if not ctx.settings.enabled:
            typer.echo("Dashboard authentication is off; enable it with `ao ui --auth`.")


@app.command("set-password")
def set_password_cmd(
    username: Annotated[str, typer.Argument(help="The account.")],
    password_stdin: PasswordStdinOption = False,
    auth_dir: AuthDirOption = None,
    workspace: WorkspaceOption = None,
) -> None:
    """Set a new password (revokes the account's sessions)."""
    from . import store as st
    from .model import AuditEventName

    with _errors():
        ctx = _open(auth_dir, workspace, mutating=True)
        name = _normalize_username(username)
        rec = _require_user(ctx, name)
        password_hash = _checked_password(ctx, name, password_stdin)
        now = ctx.clock.now_utc()
        epoch = ctx.users.mutate(lambda f: st.set_password_hash(f, name, password_hash, now=now))
        _audit(ctx, AuditEventName.PASSWORD_CHANGED, name, rec.user_id)
        typer.echo(
            f"Password changed for '{name}' (credential epoch {epoch}); existing sessions are "
            "revoked on their next request."
        )


@app.command("list-users")
def list_users_cmd(auth_dir: AuthDirOption = None, workspace: WorkspaceOption = None) -> None:
    """List accounts (never prints hashes, secrets or tokens)."""
    from .errors import StoreCorruptError, StoreUnavailableError
    from .lockouts import load_lockout_file
    from .store import parse_timestamp

    with _errors():
        ctx = _open(auth_dir, workspace, mutating=False)
        users = ctx.users.snapshot().users
        locked: dict[str, str | None] | None = {}
        try:
            lockout_file = load_lockout_file(ctx.users.paths.lockouts_file)
            locked = {uid: s.locked_until for uid, s in lockout_file.accounts.items()}
        except FileNotFoundError:
            pass
        except (StoreCorruptError, StoreUnavailableError):
            locked = None  # unreadable: show "?" rather than claim nobody is locked
        now = ctx.clock.now_utc()
        rows = [("USERNAME", "TOTP", "TOTP_REQUIRED", "RECOVERY_LEFT", "LOCKED", "LAST_LOGIN")]
        for name in sorted(users):
            rec = users[name]
            if locked is None:
                lock_cell = "?"
            else:
                until = locked.get(rec.user_id)
                parsed = parse_timestamp(until) if until else None
                lock_cell = until if (until and parsed is not None and parsed > now) else "no"
            enrolled = rec.totp is not None
            rows.append(
                (
                    name,
                    "yes" if enrolled else "no",
                    "yes" if rec.totp_required else "no",
                    str(sum(1 for c in rec.recovery_codes if c.used_at is None))
                    if enrolled
                    else STATUS_PLACEHOLDER,
                    str(lock_cell),
                    rec.last_login_at or STATUS_PLACEHOLDER,
                )
            )
        widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
        for row in rows:
            typer.echo(
                "  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True)).rstrip()
            )
        if not users:
            typer.echo("(no users)")


@app.command("remove-user")
def remove_user_cmd(
    username: Annotated[str, typer.Argument(help="The account.")],
    yes: YesOption = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Allow removing the last account even with auth enabled."),
    ] = False,
    auth_dir: AuthDirOption = None,
    workspace: WorkspaceOption = None,
) -> None:
    """Delete an account and its lockout state."""
    from .errors import StoreLockTimeoutError, StoreUnavailableError
    from .model import AuditEventName
    from .store import remove_user

    def guard_last_user(ctx: _Ctx, count: int) -> None:
        if count <= 1 and ctx.settings.enabled and not force:
            raise CliFailure(
                "refusing to remove the last account while dashboard auth is enabled: "
                "`ao ui` / `ao service run` would then refuse to start (exit 78). "
                "Pass --force to remove it anyway, or disable auth first (--no-auth)."
            )

    with _errors():
        ctx = _open(auth_dir, workspace, mutating=True)
        name = _normalize_username(username)
        _require_user(ctx, name)
        guard_last_user(ctx, len(ctx.users.snapshot().users))
        _confirm(f"Remove user '{name}'?", yes)

        def op(f: UserStoreFile) -> str:
            guard_last_user(ctx, len(f.users))  # re-checked under the lock
            return remove_user(f, name)

        user_id = ctx.users.mutate(op)
        try:
            ctx.lockouts.forget(user_id)
        except (StoreUnavailableError, StoreLockTimeoutError) as exc:
            typer.echo(f"WARNING: could not clear the lockout entry ({exc})", err=True)
        _audit(ctx, AuditEventName.USER_REMOVED, name, user_id)
        typer.echo(f"Removed user '{name}'.")


@app.command("enable-2fa")
def enable_2fa_cmd(
    username: Annotated[str, typer.Argument(help="The account.")],
    auth_dir: AuthDirOption = None,
    workspace: WorkspaceOption = None,
) -> None:
    """Enroll TOTP for an account interactively (prints the secret and 10 recovery codes)."""
    from . import store as st
    from .model import AuditEventName, TotpPolicy
    from .recovery import generate_recovery_codes, new_recovery_records
    from .totp import (
        b32encode_secret,
        match_totp_step,
        new_totp_secret,
        normalize_totp_code,
        otpauth_uri,
    )

    with _errors():
        ctx = _open(auth_dir, workspace, mutating=True)
        name = _normalize_username(username)
        rec = _require_user(ctx, name)  # identity (user_id, epoch) fixed at the start
        if rec.totp is not None:
            raise CliFailure(
                f"{name} is already enrolled; run `ao auth reset-2fa {name}` to enroll again"
            )
        if ctx.settings.totp is TotpPolicy.OFF:
            typer.echo(
                f"NOTE: ui.auth.totp is {_policy_source(ctx)}; enrollment is sticky, so this "
                "user is still asked for a code at login."
            )
        secret = new_totp_secret(ctx.entropy)
        secret_b32 = b32encode_secret(secret)
        grouped = " ".join(
            secret_b32[i : i + RECOVERY_CODE_GROUPING]
            for i in range(0, len(secret_b32), RECOVERY_CODE_GROUPING)
        )
        typer.echo(f"Secret (base32): {grouped}")
        typer.echo("URI: " + otpauth_uri(secret_b32, issuer=ctx.settings.totp_issuer, account=name))
        step: int | None = None
        for _ in range(CLI_TOTP_CONFIRM_ATTEMPTS):
            code = normalize_totp_code(typer.prompt("Code from your authenticator"))
            if code is not None:
                step = match_totp_step(secret, code, ctx.clock.now_utc().timestamp())
            if step is not None:
                break
            typer.echo("That code is not valid; try the next one.", err=True)
        if step is None:
            raise CliFailure(
                f"no valid code after {CLI_TOTP_CONFIRM_ATTEMPTS} attempts; nothing was changed"
            )
        codes = generate_recovery_codes(ctx.entropy)
        records = new_recovery_records(codes, ctx.entropy)
        now = ctx.clock.now_utc()
        confirmed_step = step
        try:
            epoch = ctx.users.mutate(
                lambda f: st.enroll_totp(
                    f,
                    name,
                    user_id=rec.user_id,
                    epoch=rec.credential_epoch,
                    secret_b32=secret_b32,
                    step=confirmed_step,
                    records=records,
                    now=now,
                    completes_login=False,
                )
            )
        except st.StaleIdentityError as exc:
            raise CliFailure("account changed during enrollment; retry") from exc
        _audit(
            ctx,
            AuditEventName.TOTP_ENROLLED,
            name,
            rec.user_id,
            recovery_codes_remaining=len(codes),
        )
        typer.echo(f"TOTP enrolled for '{name}' (credential epoch {epoch}).")
        typer.echo(STORE_NOW_BANNER)
        typer.echo("Recovery codes (each works once):")
        for recovery_code in codes:
            typer.echo(recovery_code)
        typer.echo(
            "The code you just used counts as used: a web login with the same code in the "
            "same 30 s step is rejected as replayed; wait for the next code."
        )


@app.command("disable-2fa")
def disable_2fa_cmd(
    username: Annotated[str, typer.Argument(help="The account.")],
    yes: YesOption = False,
    auth_dir: AuthDirOption = None,
    workspace: WorkspaceOption = None,
) -> None:
    """Remove TOTP (and clear a per-user TOTP requirement)."""
    from . import store as st
    from .model import AuditEventName, TotpPolicy

    def check(rec: UserRecord) -> None:
        if rec.totp is None and not rec.totp_required:
            raise CliFailure(f"{rec.username} is not enrolled and does not require TOTP")

    with _errors():
        ctx = _open(auth_dir, workspace, mutating=True)
        name = _normalize_username(username)
        rec = _require_user(ctx, name)
        check(rec)
        if ctx.settings.totp is TotpPolicy.REQUIRED:
            typer.echo(
                f"NOTE: ui.auth.totp is {_policy_source(ctx)}: {name} will be forced to enroll "
                f"again at the next login (issue a token with `ao auth enrollment-token {name}`)."
            )
        _confirm(f"Disable 2FA for '{name}'?", yes)
        now = ctx.clock.now_utc()

        def op(f: st.UserStoreFile) -> int:
            current = f.users.get(name)
            if current is not None:  # re-checked under the lock; a missing user raises below
                check(current)
            return st.remove_totp(f, name, now=now, set_required=False)

        epoch = ctx.users.mutate(op)
        _audit(ctx, AuditEventName.TOTP_DISABLED, name, rec.user_id)
        typer.echo(f"2FA disabled for '{name}' (credential epoch {epoch}).")


@app.command("reset-2fa")
def reset_2fa_cmd(
    username: Annotated[str, typer.Argument(help="The account.")],
    yes: YesOption = False,
    auth_dir: AuthDirOption = None,
    workspace: WorkspaceOption = None,
) -> None:
    """Remove TOTP, require re-enrollment and print a fresh enrollment token."""
    from . import store as st
    from .model import AuditEventName

    with _errors():
        ctx = _open(auth_dir, workspace, mutating=True)
        name = _normalize_username(username)
        rec = _require_user(ctx, name)
        _confirm(f"Reset 2FA for '{name}' (removes the authenticator and recovery codes)?", yes)
        now = ctx.clock.now_utc()

        def op(f: st.UserStoreFile) -> tuple[int, tuple[str, str]]:
            epoch = st.remove_totp(f, name, now=now, set_required=True)
            return epoch, _issue_token(ctx, f, name)

        epoch, issued = ctx.users.mutate(op)
        _audit(ctx, AuditEventName.TOTP_RESET, name, rec.user_id)
        _audit(ctx, AuditEventName.ENROLLMENT_TOKEN_ISSUED, name, rec.user_id)
        typer.echo(f"2FA reset for '{name}' (credential epoch {epoch}); TOTP is now required.")
        _show_token(name, *issued)
        _token_policy_note(ctx, ctx.users.snapshot().users[name])
        typer.echo(
            f"If the device may have been lost together with the password, also run "
            f"`ao auth set-password {name}`."
        )


@app.command("enrollment-token")
def enrollment_token_cmd(
    username: Annotated[str, typer.Argument(help="The account.")],
    auth_dir: AuthDirOption = None,
    workspace: WorkspaceOption = None,
) -> None:
    """Issue a fresh one-time enrollment token (replaces any previous one)."""
    from .model import AuditEventName

    with _errors():
        ctx = _open(auth_dir, workspace, mutating=True)
        name = _normalize_username(username)
        rec = _require_user(ctx, name)
        if rec.totp is not None:
            raise CliFailure(
                f"{name} is already enrolled; run `ao auth reset-2fa {name}` to enroll again"
            )
        issued = ctx.users.mutate(lambda f: _issue_token(ctx, f, name))
        _audit(ctx, AuditEventName.ENROLLMENT_TOKEN_ISSUED, name, rec.user_id)
        _show_token(name, *issued)
        _token_policy_note(ctx, rec)


@app.command("unlock")
def unlock_cmd(
    username: Annotated[str, typer.Argument(help="The account.")],
    auth_dir: AuthDirOption = None,
    workspace: WorkspaceOption = None,
) -> None:
    """Clear an account's login lockout (also repairs a corrupt lockouts.json)."""
    from .model import AuditEventName

    with _errors():
        ctx = _open(auth_dir, workspace, mutating=True)
        name = _normalize_username(username)
        rec = _require_user(ctx, name)
        ctx.lockouts.reset(rec.user_id, repair_corrupt=True)
        ctx.lockouts.ensure_name_key()  # a repair writes a fresh key; this is then a no-op
        _audit(ctx, AuditEventName.USER_UNLOCKED, name, rec.user_id)
        typer.echo(f"Unlocked '{name}'.")


@app.command("revoke-sessions")
def revoke_sessions_cmd(
    username: Annotated[str, typer.Argument(help="The account.")],
    auth_dir: AuthDirOption = None,
    workspace: WorkspaceOption = None,
) -> None:
    """Revoke every session of an account (bumps its credential epoch)."""
    from .model import AuditEventName
    from .store import bump_epoch

    with _errors():
        ctx = _open(auth_dir, workspace, mutating=True)
        name = _normalize_username(username)
        rec = _require_user(ctx, name)
        now = ctx.clock.now_utc()
        epoch = ctx.users.mutate(lambda f: bump_epoch(f, name, now=now))
        _audit(ctx, AuditEventName.SESSIONS_REVOKED, name, rec.user_id)
        typer.echo(
            f"Sessions of '{name}' revoked (credential epoch {epoch}); servers notice on the "
            "next request."
        )


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

# Settings shown by `status`, in order: (label, AuthSettings attribute or derived value).
_SETTING_ROWS: tuple[tuple[str, str], ...] = (
    ("enabled", "enabled"),
    ("totp", "totp"),
    ("session_idle_minutes", "session_idle_seconds"),
    ("session_absolute_hours", "session_absolute_seconds"),
    ("lockout_threshold", "lockout_threshold"),
    ("lockout_base_seconds", "lockout_base_seconds"),
    ("lockout_max_seconds", "lockout_max_seconds"),
    ("address_threshold", "address_threshold"),
    ("min_password_length", "min_password_length"),
    ("store_dir", "store_dir"),
    ("state_dir", "state_dir"),
    ("trusted_proxies", "trusted_proxies"),
    ("totp_issuer", "totp_issuer"),
)
_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 3600


def _setting_value(settings: AuthSettings, label: str, attr: str) -> Any:
    value = getattr(settings, attr)
    if label == "session_idle_minutes":
        return value // _SECONDS_PER_MINUTE
    if label == "session_absolute_hours":
        return value // _SECONDS_PER_HOUR
    if hasattr(value, "value"):  # enums
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    return value


def _check_report(check: Callable[[], list[str]]) -> dict[str, Any]:
    """Run an fsutil check without raising: ``ok`` / ``missing`` / ``unsafe`` plus detail."""
    from agent_orchestrator import fsutil

    try:
        notices = check()
    except FileNotFoundError:
        return {"state": "missing", "detail": None, "notices": []}
    except fsutil.UnsafePathError as exc:
        return {"state": "unsafe", "detail": str(exc), "notices": []}
    return {"state": "ok", "detail": None, "notices": notices}


def _stray_temp_files(directory: Path, filename: str) -> list[str]:
    try:
        return sorted(p.name for p in directory.glob(f".{filename}.*.tmp"))
    except OSError:
        return []


def _recent_startup_events(audit_file: Path) -> list[dict[str, Any]]:
    """The newest ``auth.startup.*`` lines of the audit log (never raises)."""
    events: list[dict[str, Any]] = []
    try:
        with audit_file.open(encoding="ascii", errors="replace") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                name = record.get("event") if isinstance(record, dict) else None
                if isinstance(name, str) and name.startswith(STARTUP_EVENT_PREFIX):
                    events.append(
                        {
                            "ts": record.get("ts"),
                            "event": name,
                            "outcome": record.get("outcome"),
                            "details": record.get("details") or {},
                        }
                    )
    except OSError:
        return []
    return events[-RECENT_STARTUP_EVENTS:]


def _risk_text(risk: str, settings: AuthSettings) -> str:
    from .settings import AO_UI_AUTH_TOTP_ENV, ConfigRisk

    if risk == ConfigRisk.DISABLED_BY_CONFIG.value:
        return (
            "ao ui in this workspace will refuse to start (exit 78); "
            "use --no-auth / AO_UI_AUTH=0 to disable on purpose"
        )
    return (
        f"totp={settings.totp.value} comes only from {settings.config_path}; a repository change "
        f"can lower it. Pin it with {AO_UI_AUTH_TOTP_ENV} or --auth-totp"
    )


def _account_report(ctx: _Ctx) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(users.json health, counts)``; counts are ``None`` when the store is unreadable."""
    from .errors import StoreCorruptError, StoreUnavailableError
    from .lockouts import load_lockout_file
    from .store import parse_timestamp

    health: dict[str, Any] = {"users_file": "ok", "lockouts_file": "ok"}
    counts: dict[str, Any] = {
        "users": None,
        "enrolled": None,
        "totp_required_unenrolled": None,
        "locked": None,
        "enrollment_tokens_pending": None,
    }
    now = ctx.clock.now_utc()
    paths = ctx.users.paths
    accounts: dict[str, str | None] = {}
    try:
        lockout_file = load_lockout_file(paths.lockouts_file)
        accounts = {uid: s.locked_until for uid, s in lockout_file.accounts.items()}
    except FileNotFoundError:
        health["lockouts_file"] = "missing"
    except (StoreCorruptError, StoreUnavailableError):
        health["lockouts_file"] = "corrupt"
    if not ctx.users.exists():
        health["users_file"] = "missing"
        counts.update(users=0, enrolled=0, totp_required_unenrolled=0, locked=0)
        counts["enrollment_tokens_pending"] = 0
        return health, counts
    try:
        records = list(ctx.users.snapshot().users.values())
    except (StoreCorruptError, StoreUnavailableError):
        health["users_file"] = "corrupt"
        return health, counts

    def active(stamp: str | None) -> bool:
        parsed = parse_timestamp(stamp) if stamp else None
        return parsed is not None and parsed > now

    counts.update(
        users=len(records),
        enrolled=sum(1 for r in records if r.totp is not None),
        totp_required_unenrolled=sum(1 for r in records if r.totp is None and r.totp_required),
        locked=sum(1 for r in records if active(accounts.get(r.user_id))),
        enrollment_tokens_pending=sum(
            1 for r in records if r.enrollment_token and active(r.enrollment_token.expires_at)
        ),
    )
    return health, counts


def _build_status(ctx: _Ctx) -> dict[str, Any]:
    from agent_orchestrator import fsutil

    from .model import TotpPolicy
    from .store import format_timestamp

    settings = ctx.settings
    paths = ctx.users.paths
    permissions = {
        "store_dir": _check_report(
            lambda: fsutil.ensure_private_dir(settings.store_dir, create=False, fix=False)
        ),
        "users_file": _check_report(lambda: fsutil.check_private_file(paths.users_file, fix=False)),
        "state_dir": _check_report(
            lambda: fsutil.ensure_private_dir(settings.state_dir, create=False, fix=False)
        ),
        "lockouts_file": _check_report(
            lambda: fsutil.check_private_file(paths.lockouts_file, fix=False)
        ),
    }
    health, counts = _account_report(ctx)
    try:
        audit_bytes: int | None = paths.audit_file.stat().st_size
    except OSError:
        audit_bytes = None
    stray = _stray_temp_files(settings.store_dir, USERS_FILENAME) + _stray_temp_files(
        settings.state_dir, LOCKOUTS_FILENAME
    )
    risks = sorted(r.value for r in settings.config_risks)

    notes: list[str] = []
    if settings.totp is TotpPolicy.REQUIRED:
        notes.append(
            "users who are not enrolled need an operator-issued enrollment token "
            "(`ao auth enrollment-token <user>`) to finish their first login"
        )
    if settings.totp is TotpPolicy.OFF and counts["enrolled"]:
        notes.append(
            f"{counts['enrolled']} enrolled user(s) are still asked for a code "
            "(enrollment is sticky)"
        )
    warnings = list(settings.warnings)
    for label, report in permissions.items():
        if report["state"] == "unsafe":
            warnings.append(f"{label}: {report['detail']}")
        warnings.extend(report["notices"])
    if health["lockouts_file"] == "corrupt":
        warnings.append(
            "lockouts.json is corrupt: servers refuse logins until `ao auth unlock <user>` "
            "repairs it"
        )
    if health["users_file"] == "corrupt":
        warnings.append("users.json is unreadable or corrupt; servers refuse to start")
    if settings.enabled and counts["users"] == 0:
        warnings.append("auth is enabled but no account exists; create one with `ao auth add-user`")
    if stray:
        warnings.append(f"stray temp files found: {', '.join(stray)}")

    return {
        "store_dir": str(settings.store_dir),
        "state_dir": str(settings.state_dir),
        "settings": {
            label: {
                "value": _setting_value(settings, label, attr),
                "source": settings.sources[label],
            }
            for label, attr in _SETTING_ROWS
        },
        "permissions": permissions,
        "files": {**health, "audit_bytes": audit_bytes},
        "counts": counts,
        "stray_temp_files": stray,
        "server_time_utc": format_timestamp(ctx.clock.now_utc()),
        "config_risks": [{"flag": r, "text": _risk_text(r, settings)} for r in risks],
        "recent_startup_events": _recent_startup_events(paths.audit_file),
        "notes": notes,
        "warnings": warnings,
    }


def _print_status(report: dict[str, Any]) -> None:
    echo = typer.echo
    echo("settings:")
    for label, item in report["settings"].items():
        value = item["value"]
        shown = str(value).lower() if isinstance(value, bool) else str(value)
        echo(f"  {label}: {shown} ({item['source']})")
    echo("permissions:")
    for label, item in report["permissions"].items():
        extra = f" - {item['detail']}" if item["detail"] else ""
        echo(f"  {label}: {item['state']}{extra}")
    echo("files:")
    files = report["files"]
    echo(f"  users.json: {files['users_file']}")
    echo(f"  lockouts.json: {files['lockouts_file']}")
    audit_bytes = files["audit_bytes"]
    echo(f"  {AUDIT_FILENAME}: {'missing' if audit_bytes is None else f'{audit_bytes} bytes'}")
    echo("counts:")
    for key, count in report["counts"].items():
        echo(f"  {key}: {STATUS_PLACEHOLDER if count is None else count}")
    echo(f"stray temp files: {', '.join(report['stray_temp_files']) or 'none'}")
    echo(f"server time (UTC): {report['server_time_utc']}")
    if report["config_risks"]:
        echo("flags:")
        for risk in report["config_risks"]:
            echo(f"  {risk['flag']}: {risk['text']}")
    echo("recent startup events:")
    for event in report["recent_startup_events"] or [None]:
        if event is None:
            echo("  none")
        else:
            echo(f"  {event['ts']} {event['event']} ({event['outcome']}) {event['details']}")
    for note in report["notes"]:
        echo(f"NOTE: {note}")
    for warning in report["warnings"]:
        echo(f"WARNING: {warning}")


@app.command("status")
def status_cmd(
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Print one JSON document on stdout (header goes to stderr)."),
    ] = False,
    auth_dir: AuthDirOption = None,
    workspace: WorkspaceOption = None,
) -> None:
    """Show effective settings and where each came from, permissions and account counts.

    Read-only. Reports config risks (never exits 78 for them) and never prints secrets.
    """
    with _errors():
        ctx = _begin(auth_dir, workspace, header_to_stderr=as_json)
        report = _build_status(ctx)
        if as_json:
            typer.echo(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_status(report)
