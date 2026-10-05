"""Every named constant of the result cache (HLD 8.1.5): no literals elsewhere.

Leaf module: imports only `re`. REASON_* and EVENT_* strings are the single vocabulary shared by
eligibility, keys, store, coordinator, records, reporting and the CLI.
"""

import re

# ---- operator mode (D1, D26)
ENV_CACHE = "AO_CACHE"
MODE_OFF = "off"
MODE_ON = "on"
MODE_SHADOW = "shadow"
ENV_ON_VALUES = frozenset({"1", "true", "yes", "on"})
ENV_OFF_VALUES = frozenset({"0", "false", "no", "off"})
SOURCE_CLI, SOURCE_ENV, SOURCE_CONFIG, SOURCE_DEFAULT = "cli", "env", "config", "default"
# THE author-policy flip point (D1): the policy of a task whose task.cache AND defaults.cache
# are both unset. False = double opt-in. Changing it needs an ADR-0019 addendum (test U-S4).
DEFAULT_TASK_CACHE_POLICY = False

# ---- layout (D2, D18)
CACHE_DIR_PARTS = (".orchestrator", "cache")
ENTRIES_DIR = "entries"
ENTRIES_VERSION_DIR = "v1"
BLOBS_DIR = "blobs"
TMP_DIR = "tmp"
TRASH_DIR_PREFIX = "trash-"
ENTRY_SUFFIX = ".json"
TMP_SUFFIX = ".tmp"
GITIGNORE_NAME = ".gitignore"
GITIGNORE_BODY = "# ao result cache -- never commit\n*\n"
CACHEDIR_TAG_NAME = "CACHEDIR.TAG"
CACHEDIR_TAG_BODY = (
    "Signature: 8a477f597d28d172789f06886806bc55\n"
    "# This file is a cache directory tag created by ao (result cache).\n"
)
LAYOUT_FILE = "layout.json"
LAYOUT_SCHEMA = "ao.result-cache.layout/v1"
ENTRY_SCHEMA = "ao.result-cache.entry/v1"
DIR_DIGEST_SCHEMA = "ao.result-cache.dir/v1"
DIR_ENTRY_DIR, DIR_ENTRY_FILE = "D", "F"
KEY_SCHEMA_VERSION = 1
CACHE_DIR_MODE, TMP_FILE_MODE = 0o700, 0o600
RESTORED_MODE_MASK, STORED_MODE_MASK, GROUP_OTHER_WRITE_BITS = 0o755, 0o777, 0o022

# ---- validation: always .fullmatch() (or .finditer() for the byte pattern)
SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}")
KEY_PREFIX_RE = re.compile(r"[0-9a-f]{4,64}")
HEX64_TOKEN_RE_BYTES = re.compile(rb"[0-9a-f]{64}")  # sweep mark phase over raw entry bytes
SHARD_CHARS = 2

# ---- bounds (D19, D28)
HASH_CHUNK_BYTES = 1024 * 1024
MAX_ENTRY_FILE_BYTES = 1024 * 1024
MAX_LAYOUT_FILE_BYTES = 4096
MAX_PATH_CHARS = 4096
MAX_TEXT_CHARS = 256
MAX_REASON_CHARS = 64
MAX_LIST_ITEMS = 4096
MAX_ENTRY_COST_USD = 1_000_000.0
MAX_ENTRY_TOKENS = 10**12
MAX_ENTRY_SECONDS = 10**8
MAX_ENTRY_ATTEMPTS = 10**6
EVICT_LOW_WATER_RATIO = 0.9
INLINE_PRUNE_MAX_ENTRIES = 5000
INLINE_PRUNE_MAX_ENTRY_FILE_BYTES = 64 * 1024**2  # the mark phase reads every entry file
BLOB_SWEEP_GRACE_SECONDS = 3600
TMP_SWEEP_GRACE_SECONDS = 3600

# ---- config defaults and limits (FR-15)
DEFAULT_CACHE_MAX_BYTES = 1024**3
DEFAULT_CACHE_MAX_ENTRY_BYTES = 64 * 1024**2
DEFAULT_CACHE_TTL_DAYS = 30
DEFAULT_CACHE_INCLUDE_REPO_HEADS = True
DEFAULT_CACHE_MAX_INPUT_BYTES = 512 * 1024**2
DEFAULT_CACHE_MAX_INPUT_FILES = 20_000
MAX_CONFIG_BYTES = 2**50
MAX_TTL_DAYS = 36_500
MAX_INPUT_FILES_LIMIT = 10**7

# ---- external processes (D5, D7, D13)
CACHE_GIT_TIMEOUT_SECONDS = 10
GIT_OPTIONAL_LOCKS_VAR = "GIT_OPTIONAL_LOCKS"  # set to "0" for every repository read:
GIT_OPTIONAL_LOCKS_OFF = "0"  # parallel agents must never race our index.lock
CLI_VERSION_TIMEOUT_SECONDS = 10
MAX_CLI_VERSION_CHARS = 256

# ---- key document (D3-D8)
UNBORN_HEAD, PRIOR_ABSENT = "unborn", "absent"
KIND_FILE, KIND_DIR, KIND_ABSENT = "file", "dir", "absent"
KEY_PLACEHOLDER_ID, KEY_PLACEHOLDER_TIMEOUT = "-", 1
COMPONENT_DIGEST_CHARS = 12
RESTORE_TMP_PREFIX = ".ao-result-cache-"
DIR_WALK_SKIP_NAMES = frozenset({".git"})
# AgentSpec classification (D7, D10). Tripwire U-K8: KEY | NON_KEY == AgentSpec.model_fields.
# keys.py projects KEY into the key; eligibility.py rejects any OTHER non-default field.
AGENT_KEY_FIELDS = frozenset(
    {
        "executor",
        "command_template",
        "prompt_template",
        "context_window",
        "extra_args",
        "model",
        "effort",
        "max_turns",
        "working_dir",
        "disallowed_tools",
        "forced_disallowed_tools",
        "exclude_dynamic_system_prompt_sections",
    }
)
AGENT_NON_KEY_FIELDS = frozenset({"forbidden_task_models"})  # validation-only, never in argv

# ---- eligibility and restore (D10, D29)
CACHEABLE_EXECUTORS = frozenset({"claude_cli", "fake"})
CACHEABLE_COMMAND_BASENAMES = frozenset({"claude"})
MODEL_FLAGS = ("--model", "-m")
SENSITIVE_PATH_COMPONENTS = frozenset(
    {".git", ".claude", ".github", ".gitlab", ".husky", ".ao", ".orchestrator"}
)
SENSITIVE_BASENAMES = frozenset(
    {"CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", ".mcp.json", ".envrc"}
)

# ---- CLI
DEFAULT_LS_LIMIT = 50
LS_SORT_KEYS = ("lru", "created", "size")

# ---- REASON_* (HLD 8.3.3 ineligibility, 8.6.4 miss / store-skip, 15 evict). The string value is
# the persisted / logged reason; it is always <= MAX_REASON_CHARS.
# Eligibility (8.3.3)
REASON_NOT_OPTED_IN = "not_opted_in"  # cache.skip at DEBUG only; never a record
REASON_RUN_INTEGRATION_ACTIVE = "run_integration_active"
REASON_UNKNOWN_TASK_FIELD = "unknown_task_field"
REASON_UNKNOWN_AGENT_FIELD = "unknown_agent_field"
REASON_UNKNOWN_WORKFLOW_FIELD = "unknown_workflow_field"
REASON_EMIT_TASKS = "emit_tasks"
REASON_TASK_MANIFEST_PATH = "task_manifest_path"
REASON_OUTPUT_MANIFEST = "output_manifest"
REASON_PRE_HOOK = "pre_hook"
REASON_POST_HOOK = "post_hook"
REASON_ISOLATION_WORKTREE = "isolation_worktree"
REASON_ROUTER_TASK = "router_task"
REASON_LOOP_MEMBER = "loop_member"
REASON_NO_OUTPUTS = "no_outputs"
REASON_AGENT_UNKNOWN = "agent_unknown"
REASON_EXECUTOR_NOT_CACHEABLE = "executor_not_cacheable"
REASON_COMMAND_NOT_CACHEABLE = "command_not_cacheable"
REASON_MODEL_UNRESOLVED = "model_unresolved"
REASON_VERDICT_SIDECAR_UNDECLARED = "verdict_sidecar_undeclared"
REASON_BREAKER_VERDICT_SOURCE = "breaker_verdict_source"
REASON_ARTIFACT_STORE_UNSUPPORTED = "artifact_store_unsupported"
REASON_PATH_REJECTED = "path_rejected"
REASON_PATH_IN_CACHE_DIR = "path_in_cache_dir"
REASON_DUPLICATE_OUTPUT = "duplicate_output"
REASON_SENSITIVE_OUTPUT = "sensitive_output"  # also a restore-time miss reason
REASON_CONTROL_OUTPUT = "control_output"
REASON_INPUT_MISSING = "input_missing"
REASON_INPUT_NOT_REGULAR = "input_not_regular"
REASON_INPUT_UNSTABLE = "input_unstable"
REASON_INPUT_TOO_LARGE = "input_too_large"
REASON_INPUT_UNREADABLE = "input_unreadable"
REASON_OUTPUT_NOT_REGULAR = "output_not_regular_file"  # also a store-skip reason
REASON_REPO_HEAD_UNAVAILABLE = "repo_head_unavailable"
REASON_EXECUTOR_FINGERPRINT_UNAVAILABLE = "executor_fingerprint_unavailable"
REASON_PROMPT_RENDER_ERROR = "prompt_render_error"
REASON_KEY_ENCODING = "key_encoding"
# Lookup miss (8.6.4)
REASON_NOT_FOUND = "not_found"
REASON_EXPIRED = "expired"  # also an evict reason
REASON_CORRUPT_ENTRY = "corrupt_entry"  # also an evict / cache.corrupt reason
REASON_KEY_MISMATCH = "key_mismatch"
REASON_BLOB_MISSING = "blob_missing"
REASON_MANIFEST_MISMATCH = "manifest_mismatch"
REASON_BLOB_CORRUPT = "blob_corrupt"
REASON_RESTORE_FAILED = "restore_failed"
REASON_UNSAFE_PATH = "unsafe_path"  # D33: never followed, never evicted
REASON_STORE_UNAVAILABLE = "store_unavailable"
REASON_STORE_ERROR = "store_error"  # also a store-skip reason
# Store-skip (8.6.4)
REASON_REPO_HEAD_MOVED = "repo_head_moved"
REASON_KEY_CHANGED_DURING_RUN = "key_changed_during_run"
REASON_REPO_WORKTREE_CHANGED = "repo_worktree_changed"
REASON_REPO_WORKTREE_PROBE_FAILED = "repo_worktree_probe_failed"
REASON_OUTPUT_MISSING = "output_missing"
REASON_ENTRY_TOO_LARGE = "entry_too_large"
REASON_CACHE_DISABLED = "cache_disabled"
# Evict-only (15): the others (expired, corrupt_entry, key_mismatch, manifest_mismatch,
# blob_missing, blob_corrupt) reuse the constants above.
REASON_EVICT_LRU = "lru"
REASON_EVICT_INVALID = "invalid"
REASON_EVICT_DEFERRED = "deferred"

# ---- EVENT_* (HLD 15): the `cache.*` structured-log namespace
EVENT_HIT = "cache.hit"
EVENT_WOULD_HIT = "cache.would_hit"
EVENT_MISS = "cache.miss"
EVENT_SKIP = "cache.skip"
EVENT_STORE = "cache.store"
EVENT_EVICT = "cache.evict"
EVENT_CORRUPT = "cache.corrupt"
EVENT_DISABLED = "cache.disabled"
