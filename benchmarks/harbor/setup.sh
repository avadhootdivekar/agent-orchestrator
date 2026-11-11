#!/usr/bin/env bash
# setup.sh -- install + configure Harbor on this host for ao external benchmarks.
#
# Usage:
#   bash benchmarks/harbor/setup.sh            # install/upgrade Harbor, create config + dirs
#   bash benchmarks/harbor/setup.sh --check    # report status only; exit 1 if not ready
#   bash benchmarks/harbor/setup.sh --smoke    # also run the no-LLM oracle smoke task
#
# What it does (idempotent):
#   1. Checks docker (daemon reachable, free space under its data-root), uv, git, claude.
#   2. Installs the pinned Harbor CLI as a uv tool (Python 3.12; Harbor needs >= 3.12).
#   3. Credentials, either:
#      - the host's Claude login (run.sh --auth host, the default when the file below has no
#        credential): only its current access token is passed to containers, per job; or
#      - the file $AO_HARBOR_ENV_FILE (default ~/.config/ao-harbor/env, mode 600, created from a
#        template if missing) with ONE of CLAUDE_CODE_OAUTH_TOKEN=... (`claude setup-token`) or
#        ANTHROPIC_API_KEY=... (run.sh --auth file).
#   4. Creates the work root $AO_HARBOR_ROOT (default /var/tmp/ao-harbor): jobs/, wheels/.
#      It is outside any checkout on purpose (no CLAUDE.md / .claude/ above it).
# Nothing here touches the live `ao` install or ao.service.
set -euo pipefail

HARBOR_VERSION="${HARBOR_VERSION:-0.25.0}"
HARBOR_PYTHON="${HARBOR_PYTHON:-3.12}"
AO_HARBOR_ROOT="${AO_HARBOR_ROOT:-/var/tmp/ao-harbor}"
AO_HARBOR_ENV_FILE="${AO_HARBOR_ENV_FILE:-$HOME/.config/ao-harbor/env}"
MIN_DOCKER_FREE_GB="${MIN_DOCKER_FREE_GB:-60}"

mode="install"
case "${1:-}" in
  --check) mode="check" ;;
  --smoke) mode="smoke" ;;
  "") ;;
  -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
  *) echo "unknown option: $1" >&2; exit 2 ;;
esac

ok=1
say()  { printf '%-10s %s\n' "$1" "$2"; }
fail() { say "MISSING" "$1"; ok=0; }

# 1. prerequisites -----------------------------------------------------------------------
if docker info >/dev/null 2>&1; then
  root="$(docker info --format '{{.DockerRootDir}}')"
  free_gb="$(df -BG --output=avail "$root" | tail -1 | tr -dc '0-9')"
  say "ok" "docker $(docker version --format '{{.Server.Version}}'), data-root $root, ${free_gb}G free"
  if [ "${free_gb:-0}" -lt "$MIN_DOCKER_FREE_GB" ]; then
    say "WARN" "under ${MIN_DOCKER_FREE_GB}G free for images; prune or run with --delete (run.sh default)"
  fi
else
  fail "docker daemon not reachable (is the user in the docker group?)"
fi
for bin in uv git claude; do
  if command -v "$bin" >/dev/null 2>&1; then say "ok" "$bin: $(command -v "$bin")"; else fail "$bin on PATH"; fi
done
command -v claude >/dev/null 2>&1 && say "info" "host claude: $(claude --version 2>/dev/null) (run.sh pins this version in containers)"

# 2. Harbor ------------------------------------------------------------------------------
installed="$(harbor --version 2>/dev/null || true)"
if [ "$installed" = "$HARBOR_VERSION" ]; then
  say "ok" "harbor $installed"
elif [ "$mode" = "check" ]; then
  fail "harbor $HARBOR_VERSION (found: ${installed:-none}); run setup.sh"
else
  say "install" "harbor $HARBOR_VERSION (was: ${installed:-none})"
  uv tool install --force --python "$HARBOR_PYTHON" "harbor==$HARBOR_VERSION" >/dev/null
  say "ok" "harbor $(harbor --version)"
fi

# 3. credentials file --------------------------------------------------------------------
if [ ! -f "$AO_HARBOR_ENV_FILE" ] && [ "$mode" != "check" ]; then
  mkdir -p "$(dirname "$AO_HARBOR_ENV_FILE")"
  umask 077
  cat >"$AO_HARBOR_ENV_FILE" <<'EOF'
# ao-harbor credentials (read by benchmarks/harbor/run.sh via `harbor run --env-file`).
# Set exactly ONE of the two. Never commit this file.
# Subscription: run `claude setup-token` and paste the token.
CLAUDE_CODE_OAUTH_TOKEN=
# API billing:
ANTHROPIC_API_KEY=
EOF
  say "created" "$AO_HARBOR_ENV_FILE (fill in a credential)"
fi
if [ -f "$AO_HARBOR_ENV_FILE" ]; then
  perms="$(stat -c %a "$AO_HARBOR_ENV_FILE")"
  [ "$perms" = "600" ] || say "WARN" "$AO_HARBOR_ENV_FILE mode $perms; chmod 600 it"
fi
if [ -f "$AO_HARBOR_ENV_FILE" ] && grep -Eq '^(CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY)=.+' "$AO_HARBOR_ENV_FILE"; then
  say "ok" "credential present in $AO_HARBOR_ENV_FILE (run.sh --auth file)"
elif hours="$(python3 -c 'import json,os,time;o=json.load(open(os.path.expanduser("~/.claude/.credentials.json")))["claudeAiOauth"];print(round((o["expiresAt"]/1000-time.time())/3600,1)) if o.get("accessToken") else exit(1)' 2>/dev/null)"; then
  say "ok" "no credential file; host Claude login usable (run.sh --auth host), token valid ${hours} h"
else
  fail "a credential: $AO_HARBOR_ENV_FILE (CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY) or a host Claude login"
fi

# 4. work root ---------------------------------------------------------------------------
if [ "$mode" != "check" ]; then mkdir -p "$AO_HARBOR_ROOT/jobs" "$AO_HARBOR_ROOT/wheels"; fi
if [ -d "$AO_HARBOR_ROOT/jobs" ]; then say "ok" "work root $AO_HARBOR_ROOT"; else fail "work root $AO_HARBOR_ROOT"; fi

# 5. optional smoke (no LLM): Harbor's oracle agent applies the reference solution -------
if [ "$mode" = "smoke" ]; then
  say "smoke" "oracle on hello-world/hello-world (pulls an image; ~1-5 min)"
  (cd "$AO_HARBOR_ROOT" && harbor run -t hello-world/hello-world -a oracle \
      -o "$AO_HARBOR_ROOT/jobs" --job-name "smoke-oracle-$(date +%Y%m%d-%H%M%S)" \
      --verifier-timeout-multiplier 5 -y -q) && say "ok" "oracle smoke finished (see jobs/)"
fi

if [ "$ok" = 1 ]; then echo "READY"; else echo "NOT READY"; exit 1; fi
