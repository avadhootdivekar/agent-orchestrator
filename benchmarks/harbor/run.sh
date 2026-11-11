#!/usr/bin/env bash
# run.sh -- run one benchmark arm (Claude Code or ao, on one model) through Harbor.
#
# Usage:
#   benchmarks/harbor/run.sh --arm cc|ao --model sonnet|opus|<claude-model-id> \
#       (--dataset ORG/NAME[@VER] | --task ORG/NAME | --path DIR) \
#       [--tasks-file FILE] [--include GLOB]... [--n-tasks N] [-n CONCURRENCY] \
#       [--effort LEVEL] [--budget-usd USD] [--attempts K] [--job-name NAME] [--subject JSON] \
#       [--install-only] [--dry-run] [-- EXTRA_HARBOR_ARGS...]
#
# Arms. Both get the same net input (see README "Input parity"): same container, pinned
# Claude Code version, model, effort, credentials; the same task prompt (the subject's
# wrapper + the instruction, byte-identical); the same parity env (parity/env) and the same
# user-level Claude config (parity/claude/ -> a fresh CLAUDE_CONFIG_DIR).
#   cc   ao_harbor.agent:CcAgent: Harbor's claude-code, one `claude -p` session.
#   ao   ao_harbor.agent:AoAgent: the ao workflow of --subject (default
#        benchmarks/subjects/ao-overseer-harbor.json) driving Claude Code in the container.
#
# --tasks-file   one task name per line (# comments ok); becomes --include-task-name args.
#                Use the same file for every arm so arms see identical tasks.
# --subject      ao_workflow subject (template mode). The cc arm uses its prompt wrapper too,
#                so pass the SAME --subject to both arms of a comparison.
# --install-only install the agent in each container and stop (no LLM spend): checks the
#                ao wheel/uv/claude install path on a dataset's images.
# --auth         file (credentials file; default when it holds one) or host (the host's
#                current Claude login access token, per job, deleted after; never the
#                refresh token). --min-token-hours H (default 3): refuse if it expires sooner.
# Env: AO_HARBOR_ROOT (/var/tmp/ao-harbor), AO_HARBOR_ENV_FILE (~/.config/ao-harbor/env),
#      CLAUDE_CODE_VERSION (default: the host's `claude --version`).
# Results land in $AO_HARBOR_ROOT/jobs/<job-name>/; summarize with summarize.py.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
AO_HARBOR_ROOT="${AO_HARBOR_ROOT:-/var/tmp/ao-harbor}"
AO_HARBOR_ENV_FILE="${AO_HARBOR_ENV_FILE:-$HOME/.config/ao-harbor/env}"
DEFAULT_SUBJECT="$REPO_ROOT/benchmarks/subjects/ao-overseer-harbor.json"
PARITY_ENV="$HERE/parity/env"

arm="" model="" dataset="" task="" path="" tasks_file="" n_tasks="" concurrency="2"
effort="high" budget_usd="20" attempts="1" job_name="" subject="" install_only=0 dry_run=0
auth="" min_token_hours="3"
includes=() extra=()
while [ $# -gt 0 ]; do
  case "$1" in
    --arm) arm="$2"; shift 2 ;;
    --model) model="$2"; shift 2 ;;
    --dataset) dataset="$2"; shift 2 ;;
    --task) task="$2"; shift 2 ;;
    --path) path="$2"; shift 2 ;;
    --tasks-file) tasks_file="$2"; shift 2 ;;
    --include) includes+=("$2"); shift 2 ;;
    --n-tasks) n_tasks="$2"; shift 2 ;;
    -n|--concurrency) concurrency="$2"; shift 2 ;;
    --effort) effort="$2"; shift 2 ;;
    --budget-usd) budget_usd="$2"; shift 2 ;;
    --attempts) attempts="$2"; shift 2 ;;
    --job-name) job_name="$2"; shift 2 ;;
    --subject) subject="$2"; shift 2 ;;
    --install-only) install_only=1; shift ;;
    --dry-run) dry_run=1; shift ;;
    --auth) auth="$2"; shift 2 ;;
    --min-token-hours) min_token_hours="$2"; shift 2 ;;
    --) shift; extra=("$@"); break ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

die() { echo "run.sh: $*" >&2; exit 2; }
[ -n "$arm" ] || die "--arm cc|ao is required"
[ -n "$model" ] || die "--model is required"
[ -n "$dataset$task$path" ] || die "one of --dataset / --task / --path is required"

case "$model" in
  sonnet) model_id="claude-sonnet-5-5" ;;
  opus) model_id="claude-opus-5-5" ;;
  haiku) model_id="claude-haiku-5-5" ;;
  *) model_id="$model" ;;
esac
CLAUDE_CODE_VERSION="${CLAUDE_CODE_VERSION:-$(claude --version 2>/dev/null | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' | head -1)}"
[ -n "$CLAUDE_CODE_VERSION" ] || die "set CLAUDE_CODE_VERSION (no host claude to copy it from)"

subject="${subject:-$DEFAULT_SUBJECT}"
[ -f "$subject" ] || die "no subject file $subject"
src="${dataset:-${task:-$(basename "$path")}}"
src_slug="$(echo "$src" | tr '/@:' '---')"
job_name="${job_name:-$(date +%Y%m%d-%H%M%S)-$src_slug-$arm-${model}}"

args=(run -o "$AO_HARBOR_ROOT/jobs" --job-name "$job_name" -m "anthropic/$model_id"
      -n "$concurrency" -k "$attempts" --ak "version=$CLAUDE_CODE_VERSION" -y)
[ -n "$dataset" ] && args+=(-d "$dataset")
[ -n "$task" ] && args+=(-t "$task")
[ -n "$path" ] && args+=(-p "$path")
[ -n "$n_tasks" ] && args+=(-l "$n_tasks")
# Thinking effort is always explicit and identical: the cc arm gets `claude --effort`, the
# ao arm AO_THINKING_EFFORT (-> `--effort` on every agent), and every session the env
# fallback CLAUDE_CODE_EFFORT_LEVEL.
args+=(--effort "$effort" --ae "CLAUDE_CODE_EFFORT_LEVEL=$effort")
# Spend cap per benchmark task, identical for both arms: the plain arm's one session gets
# `claude --max-budget-usd`, the whole ao run (all sessions) `run_budget_usd` (AoAgent maps it).
args+=(--ak "max_budget_usd=$budget_usd")
# Agent install (claude, and for ao also uv + Python + the wheel) runs in Harbor's setup phase,
# not on the task's agent clock; its 6-min default is too tight for ao on heavier images.
# Applied to both arms; the agent timeout (the comparison's clock) is untouched.
args+=(--agent-setup-timeout-multiplier "${AO_HARBOR_SETUP_TIMEOUT_MULT:-3}")
[ "$install_only" = 1 ] && args+=(--install-only)
if [ -n "$tasks_file" ]; then
  while IFS= read -r line; do
    line="${line%%#*}"; line="$(echo "$line" | xargs)"
    [ -n "$line" ] && includes+=("$line")
  done <"$tasks_file"
fi
# Harbor names a dataset's tasks "<org>/<task>"; add the dataset's org to bare names.
org=""
[ -n "$dataset" ] && case "$dataset" in */*) org="${dataset%%/*}" ;; esac
for inc in ${includes[@]+"${includes[@]}"}; do
  case "$inc" in */*) ;; *) [ -n "$org" ] && inc="$org/$inc" ;; esac
  args+=(-i "$inc")
done
while IFS= read -r line; do  # parity env: identical for every arm
  line="${line%%#*}"; line="$(echo "$line" | xargs)"
  [ -n "$line" ] && args+=(--ae "$line")
done <"$PARITY_ENV"
# Credentials. file: $AO_HARBOR_ENV_FILE (setup-token or API key). host: the host's current
# Claude login access token ONLY (never the refresh token, so containers cannot rotate it and
# log the host out), written to a per-job 0600 env file that is deleted when the job ends.
# The job must finish before the token expires: --min-token-hours (default 3) is enforced.
if [ -z "$auth" ]; then
  if [ -f "$AO_HARBOR_ENV_FILE" ] && grep -Eq '^(CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY)=.+' "$AO_HARBOR_ENV_FILE"; then
    auth="file"
  else
    auth="host"
  fi
fi
secret_env=""
if [ "$install_only" = 0 ]; then
  case "$auth" in
    file)
      [ -f "$AO_HARBOR_ENV_FILE" ] || [ "$dry_run" = 1 ] || die "no credentials file $AO_HARBOR_ENV_FILE (run setup.sh)"
      args+=(--env-file "$AO_HARBOR_ENV_FILE") ;;
    host)
      secret_env="$AO_HARBOR_ROOT/secrets/$job_name.env"
      args+=(--env-file "$secret_env") ;;
    *) die "--auth must be file or host" ;;
  esac
fi

export PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}"  # makes ao_harbor importable
case "$arm" in
  cc)
    # The plain arm's prompt template, generated from the same subject prompt by the same
    # function ao uses (ao-bench harbor-prompt): both arms see byte-identical task prompts.
    template="$AO_HARBOR_ROOT/prompts/$(basename "$subject" .json).j2"
    if [ "$dry_run" = 0 ]; then
      uv run --project "$REPO_ROOT" ao-bench harbor-prompt --subject "$subject" --out "$template" >/dev/null
    fi
    args+=(-a ao_harbor.agent:CcAgent --ak "prompt_template_path=$template")
    ;;
  ao)
    # One wheel per commit (AO_WHEEL_REF, default HEAD), built from a clean `git archive`
    # export of that commit: uncommitted edits (yours, or an ao run working in this same
    # checkout) never leak into the ao under test.
    sha="$(git -C "$REPO_ROOT" rev-parse --short "${AO_WHEEL_REF:-HEAD}")"
    if [ -n "$(git -C "$REPO_ROOT" status --porcelain -- src pyproject.toml)" ]; then
      echo "run.sh: NOTE: src/ has uncommitted changes; they are NOT in the wheel (built from $sha)" >&2
    fi
    wheel_dir="$AO_HARBOR_ROOT/wheels/$sha"
    wheel="$(ls "$wheel_dir"/agent_orchestrator-*.whl 2>/dev/null | tail -1 || true)"
    if [ -z "$wheel" ] && [ "$dry_run" = 0 ]; then
      export_dir="$(mktemp -d "$AO_HARBOR_ROOT/wheels/src-$sha.XXXX")"
      git -C "$REPO_ROOT" archive "$sha" | tar -x -C "$export_dir"
      uv build --wheel --out-dir "$wheel_dir" "$export_dir" >/dev/null
      rm -rf "$export_dir"
      wheel="$(ls "$wheel_dir"/agent_orchestrator-*.whl | tail -1)"
    fi
    args+=(-a ao_harbor.agent:AoAgent --ak "ao_subject=$subject")
    [ -n "$wheel" ] && args+=(--ak "ao_wheel=$wheel")  # empty only on --dry-run
    ;;
  *) die "--arm must be cc or ao" ;;
esac
args+=(${extra[@]+"${extra[@]}"})

echo "job: $AO_HARBOR_ROOT/jobs/$job_name"
echo "+ harbor ${args[*]}"
[ "$dry_run" = 1 ] && exit 0
mkdir -p "$AO_HARBOR_ROOT/jobs"
if [ -n "$secret_env" ]; then
  (umask 077 && mkdir -p "$AO_HARBOR_ROOT/secrets")
  trap 'rm -f "$secret_env"' EXIT
  MIN_TOKEN_HOURS="$min_token_hours" OUT="$secret_env" python3 - <<'PY' || die "host login unusable (see above)"
import json, os, sys, time
oauth = json.load(open(os.path.expanduser("~/.claude/.credentials.json"))).get("claudeAiOauth") or {}
token, expires_ms = oauth.get("accessToken"), oauth.get("expiresAt") or 0
hours_left = (expires_ms / 1000 - time.time()) / 3600
if not token:
    sys.exit("no host Claude login (run `claude` and log in)")
if hours_left < float(os.environ["MIN_TOKEN_HOURS"]):
    sys.exit(f"host token expires in {hours_left:.1f} h < --min-token-hours; "
             "use any claude command on the host to refresh it, then retry")
fd = os.open(os.environ["OUT"], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as fh:
    fh.write(f"CLAUDE_CODE_OAUTH_TOKEN={token}\nCLAUDE_FORCE_OAUTH=1\n")
print(f"run.sh: auth=host (access token valid {hours_left:.1f} h more)", file=sys.stderr)
PY
fi
cd "$AO_HARBOR_ROOT"  # never inside a checkout: no CLAUDE.md/.claude above the job
harbor "${args[@]}"
