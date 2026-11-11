#!/usr/bin/env bash
# run.sh -- run one benchmark arm (Claude Code or ao, on one model) through Harbor.
#
# Usage:
#   benchmarks/harbor/run.sh --arm cc|ao --model sonnet|opus|<claude-model-id> \
#       (--dataset ORG/NAME[@VER] | --task ORG/NAME | --path DIR) \
#       [--tasks-file FILE] [--include GLOB]... [--n-tasks N] [-n CONCURRENCY] \
#       [--effort LEVEL] [--attempts K] [--job-name NAME] [--subject JSON] \
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
effort="high" attempts="1" job_name="" subject="" install_only=0 dry_run=0
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
    --attempts) attempts="$2"; shift 2 ;;
    --job-name) job_name="$2"; shift 2 ;;
    --subject) subject="$2"; shift 2 ;;
    --install-only) install_only=1; shift ;;
    --dry-run) dry_run=1; shift ;;
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
[ "$install_only" = 1 ] && args+=(--install-only)
if [ -n "$tasks_file" ]; then
  while IFS= read -r line; do
    line="${line%%#*}"; line="$(echo "$line" | xargs)"
    [ -n "$line" ] && includes+=("$line")
  done <"$tasks_file"
fi
for inc in ${includes[@]+"${includes[@]}"}; do args+=(-i "$inc"); done
while IFS= read -r line; do  # parity env: identical for every arm
  line="${line%%#*}"; line="$(echo "$line" | xargs)"
  [ -n "$line" ] && args+=(--ae "$line")
done <"$PARITY_ENV"
if [ "$install_only" = 0 ]; then
  [ -f "$AO_HARBOR_ENV_FILE" ] || [ "$dry_run" = 1 ] || die "no credentials file $AO_HARBOR_ENV_FILE (run setup.sh)"
  args+=(--env-file "$AO_HARBOR_ENV_FILE")
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
    # One wheel per checkout commit; a dirty tree gets a fresh build every time.
    sha="$(git -C "$REPO_ROOT" rev-parse --short HEAD)"
    if [ -n "$(git -C "$REPO_ROOT" status --porcelain -- src pyproject.toml)" ]; then
      sha="$sha-dirty-$(date +%s)"
      echo "run.sh: WARNING: src/ has uncommitted changes; the wheel is stamped dirty" >&2
    fi
    wheel_dir="$AO_HARBOR_ROOT/wheels/$sha"
    wheel="$(ls "$wheel_dir"/agent_orchestrator-*.whl 2>/dev/null | tail -1 || true)"
    if [ -z "$wheel" ] && [ "$dry_run" = 0 ]; then
      uv build --wheel --out-dir "$wheel_dir" "$REPO_ROOT" >/dev/null
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
cd "$AO_HARBOR_ROOT"  # never inside a checkout: no CLAUDE.md/.claude above the job
exec harbor "${args[@]}"
