"""Named constants for dashboard/hub authentication (HLD section 12.6, v2.1).

The single source for every auth constant except ``EXIT_CONFIG`` (root ``errors.py``) and the
frontend-only constants (``ui/src/auth/constants.ts``). L0: stdlib only, no framework import.
``tests/auth/test_foundation.py`` pins every value, so a change is a deliberate two-place edit
(HLD and code). Later tasks append within their group; nothing is redefined.
"""

from __future__ import annotations

# --- Cookie, session token and proof ---
COOKIE_BASENAME = "ao_sid"
SECURE_COOKIE_PREFIX = "__Host-"
SESSION_TOKEN_BYTES = 32
SESSION_TOKEN_B64_CHARS = 43
SESSION_PROOF_BYTES = 32
SESSION_PROOF_B64_CHARS = 43
SESSION_PROOF_HEADER = "X-AO-Session-Proof"

# --- Session lifetime and bounds ---
PARTIAL_SESSION_TTL_SECONDS = 300
MAX_SECOND_FACTOR_ATTEMPTS = 5
MAX_ENROLL_CONFIRM_ATTEMPTS = 5
MAX_SESSIONS_TOTAL = 10_000
MAX_SESSIONS_PER_USER = 32
MAX_PARTIAL_SESSIONS = 1_000
DEFAULT_SESSION_IDLE_MINUTES = 30
DEFAULT_SESSION_ABSOLUTE_HOURS = 12
MAX_IDLE_MINUTES = 1440  # env/CLI ceiling
MAX_ABSOLUTE_HOURS = 720  # env/CLI ceiling

# --- Account lockout ---
DEFAULT_LOCKOUT_THRESHOLD = 5
DEFAULT_LOCKOUT_BASE_SECONDS = 30
DEFAULT_LOCKOUT_MAX_SECONDS = 900
MAX_LOCKOUT_THRESHOLD = 100
MAX_LOCKOUT_BASE_SECONDS = 3600
MAX_LOCKOUT_MAX_SECONDS = 86_400
LOCKOUT_RESET_AFTER_SECONDS = 86_400
LOCKOUT_NAME_KEY_BYTES = 32  # v2.1
INVALID_USERNAME_BUCKET = ""  # v2.1
PHANTOM_LOCKOUT_MAX_ENTRIES = 4096

# --- Address throttle and username gates ---
DEFAULT_ADDRESS_THRESHOLD = 20
MAX_ADDRESS_THRESHOLD = 10_000
ADDRESS_WINDOW_SECONDS = 900
ADDRESS_BACKOFF_BASE_SECONDS = 1
ADDRESS_BACKOFF_MAX_SECONDS = 900
ADDRESS_TABLE_MAX_ENTRIES = 4096
ADDRESS_HISTORY_SLACK = 32
IPV6_THROTTLE_PREFIX_LEN = 64
UNKNOWN_CLIENT_KEY = "unknown"
USERNAME_GATE_MAX_ENTRIES = 4096

# --- Usernames and passwords ---
USERNAME_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,31}$"
MAX_USERNAME_CHARS = 64
DEFAULT_MIN_PASSWORD_LENGTH = 12
MIN_PASSWORD_LENGTH_FLOOR = 8
MAX_PASSWORD_LENGTH = 256
MAX_LOGIN_PASSWORD_CHARS = 1024
MAX_PASSWORD_BYTES = 4 * MAX_LOGIN_PASSWORD_CHARS

# --- scrypt password hashing ---
SCRYPT_LOG2_N = 15
SCRYPT_R = 8
SCRYPT_P = 3
SCRYPT_DKLEN = 32
SCRYPT_SALT_BYTES = 16
SCRYPT_MAXMEM_BYTES = 64 * 1024 * 1024
SCRYPT_MAX_LOG2_N = 17
SCRYPT_MAX_R = 16
SCRYPT_MAX_P = 16
SCRYPT_MAX_HASH_MEMORY_BYTES = 128 * 1024 * 1024
HASH_FORMAT_VERSION = 1
HASH_CONCURRENCY = 2
HASH_QUEUE_MAX = 16

# --- TOTP and recovery codes ---
TOTP_DIGITS = 6
TOTP_PERIOD_SECONDS = 30
TOTP_ALGORITHM = "SHA1"
TOTP_SECRET_BYTES = 20
TOTP_WINDOW_STEPS = 1
DEFAULT_TOTP_ISSUER_PREFIX = "ao@"
MAX_TOTP_ISSUER_CHARS = 64
RECOVERY_CODE_COUNT = 10
RECOVERY_CODE_CHARS = 16
RECOVERY_CODE_GROUP = 4
RECOVERY_CODE_BYTES = 10
RECOVERY_SALT_BYTES = 16
ENROLLMENT_TOKEN_TTL_SECONDS = 3600
CLI_TOTP_CONFIRM_ATTEMPTS = 3

# --- Request limits ---
MAX_AUTH_BODY_BYTES = 16_384

# --- User store (users.json), locks and file modes ---
STORE_LOCK_TIMEOUT_SECONDS = 5.0
LOCK_POLL_SECONDS = 0.05
STORE_RETRY_AFTER_SECONDS = 5
MAX_USERS = 1000
# Upper bound on a store/lockout JSON file we will parse (L-5): ~1000 users are well under 4 MiB;
# anything larger is treated as corrupt rather than read into memory.
STORE_FILE_MAX_BYTES = 16 * 1024 * 1024
USER_ID_BYTES = 16
STORE_SCHEMA_VERSION = 1
KNOWN_STORE_FEATURES: frozenset[str] = frozenset()
STORE_DIR_MODE = 0o700
STORE_FILE_MODE = 0o600
USERS_FILENAME = "users.json"
USERS_LOCK_FILENAME = "users.lock"
LOCKOUTS_FILENAME = "lockouts.json"
LOCKOUTS_LOCK_FILENAME = "lockouts.lock"
STATE_SUBDIR = "state"

# --- Audit log ---
AUDIT_FILENAME = "audit.jsonl"
AUDIT_LOCK_FILENAME = "audit.lock"
AUDIT_SCHEMA_VERSION = 1
AUDIT_MAX_BYTES = 10 * 1024 * 1024
AUDIT_BACKUP_COUNT = 5
AUDIT_LOCK_TIMEOUT_SECONDS = 2.0
AUDIT_USERNAME_HASH_CHARS = 16
AUDIT_MAX_DETAIL_CHARS = 200
AUDIT_FAILURE_EVENTS_PER_MINUTE = 60
MAX_QUOTED_CONFIG_CHARS = 64

# --- HTTP paths (API_PREFIX equals ui.app.API_PREFIX; a test pins equality) ---
API_PREFIX = "/api"
AUTH_API_PREFIX = API_PREFIX + "/auth"
HEALTH_PATH = "/api/health"
SPA_FALLBACK_PATH = "/{full_path:path}"
SPA_ASSETS_MOUNT_NAME = "assets"
AUTH_STATUS_PATH = AUTH_API_PREFIX + "/status"
AUTH_LOGIN_PATH = AUTH_API_PREFIX + "/login"
AUTH_LOGOUT_PATH = AUTH_API_PREFIX + "/logout"
AUTH_KEEPALIVE_PATH = AUTH_API_PREFIX + "/keepalive"
AUTH_PASSWORD_PATH = AUTH_API_PREFIX + "/password"
AUTH_TOTP_VERIFY_PATH = AUTH_API_PREFIX + "/totp/verify"
AUTH_ENROLL_BEGIN_PATH = AUTH_API_PREFIX + "/totp/enroll/begin"
AUTH_ENROLL_CONFIRM_PATH = AUTH_API_PREFIX + "/totp/enroll/confirm"
AUTH_TOTP_DISABLE_PATH = AUTH_API_PREFIX + "/totp/disable"
AUTH_RECOVERY_CODES_PATH = AUTH_API_PREFIX + "/totp/recovery-codes"
HUB_LOGIN_PATH = "/login"
HUB_ASSET_ROUTE_PATH = "/auth-assets/{name}"
HUB_REALM_ID = "hub"
WORKSPACE_ID_HEX_CHARS = 12

# --- Application / ASGI scope state keys (R5: no other module spells these strings) ---
APP_STATE_AUTH_KEY = "ao_auth"
SCOPE_PRINCIPAL_KEY = "principal"
SCOPE_SESSION_KEY = "auth_session"
SCOPE_AUTH_ENABLED_KEY = "auth_enabled"
SCOPE_PROOF_OK_KEY = "auth_proof_ok"

# --- Transport, protocol and provider identifiers ---
WWW_AUTHENTICATE_SCHEME = "AO-Session"
WS_POLICY_VIOLATION = 1008
LOCAL_PROVIDER_ID = "local-password"
CLI_REALM = "cli"
SOURCE_CLI = "cli"
SOURCE_WEB = "web"
LOOPBACK_HOSTNAMES: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})  # v2.1
FORWARDED_HEADER = "forwarded"  # v2.1
FORWARDING_HEADER_PREFIX = "x-forwarded-"  # v2.1

# --- Process and deployment ---
AO_UI_BOUND_PORT_ENV = "AO_UI_BOUND_PORT"
SERVICE_ENV_RELATIVE_PATH = ".config/ao/service.env"  # v2.1: the systemd unit's EnvironmentFile
EPHEMERAL_PORT = 0  # v2.1: refused with auth on
