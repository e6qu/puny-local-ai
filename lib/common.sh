# shellcheck shell=bash disable=SC2034
# Shared helpers for the launchers and checks. Source this file; do not run it.
#
# Settings come from the environment, then from puny.conf (see load_config):
#   MLX_BASE_URL  OpenAI-compatible base URL of the running mlx_lm.server, in
#                 the form http://HOST:PORT/v1. Defaults to
#                 http://127.0.0.1:8081/v1, because 8080 is often taken by
#                 other local services.

set -euo pipefail

PUNY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

# The settings puny.conf may hold; lib/config.py lists the same names with
# their help, and the tests check that the two agree.
CONFIG_SETTINGS=(MLX_BASE_URL QWEN_PRESET QWEN_REPO QWEN_REVISION QWEN_CONTEXT QWEN_MAX_OUTPUT QWEN_TEMP
  QWEN_KV_BITS QWEN_PROMPT_CACHE_ENTRIES QWEN_PREFILL_STEP QWEN_MEMORY_MARGIN WEB_UI_URL SMOKE_TIMEOUT
  BENCH_AGENTS BENCH_TASKS BENCH_RUNS BENCH_TIMEOUT TEAM_SOLVE_AGENT TEAM_SOLVE_TIMEOUT)
# Which model to serve is one choice: when the environment names a model,
# the file's model settings are all left out, so the two never mix.
CONFIG_MODEL_SETTINGS=" QWEN_PRESET QWEN_REPO QWEN_REVISION QWEN_CONTEXT "

# load_config exports each setting in PUNY_CONFIG (default puny.conf in the
# repo) that the environment does not set. The file is parsed, never run:
# one KEY=VALUE per line, # comments, and values taken literally apart from
# one pair of surrounding quotes. lib/config.py writes it the same way.
load_config() {
  PUNY_CONFIG="${PUNY_CONFIG:-$PUNY_ROOT/puny.conf}"
  # Where each setting came from, for ./bin/config show; the scripts fill in
  # defaults later, so this is the only point where it can be told.
  CONFIG_FROM_FILE=" "
  CONFIG_FROM_ENV=" "
  local line key value number=0 model_from_env=0 setting
  for setting in "${CONFIG_SETTINGS[@]}"; do
    # Each name is one of CONFIG_SETTINGS, so eval only ever sees a known name.
    eval "[[ -n \${$setting+set} ]]" && CONFIG_FROM_ENV="$CONFIG_FROM_ENV$setting "
  done
  for setting in $CONFIG_MODEL_SETTINGS; do
    [[ "$CONFIG_FROM_ENV" == *" $setting "* ]] && model_from_env=1
  done
  [[ -e "$PUNY_CONFIG" ]] || return 0
  [[ -f "$PUNY_CONFIG" && -r "$PUNY_CONFIG" ]] || die "$PUNY_CONFIG is not a readable file"
  while IFS= read -r line || [[ -n "$line" ]]; do
    number=$((number + 1))
    [[ "$line" =~ ^[[:space:]]*(#.*)?$ ]] && continue
    [[ "$line" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=(.*)$ ]] \
      || die "$PUNY_CONFIG line $number is not KEY=VALUE: $line"
    key="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"
    [[ " ${CONFIG_SETTINGS[*]} " == *" $key "* ]] \
      || die "$PUNY_CONFIG line $number: $key is not a puny-local-ai setting; expected one of ${CONFIG_SETTINGS[*]}"
    # Trims spaces, then removes one pair of matching quotes.
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    if [[ ${#value} -ge 2 && ( "$value" == \"*\" || "$value" == \'*\' ) ]]; then
      value="${value:1:${#value}-2}"
    fi
    [[ "$CONFIG_FROM_ENV" == *" $key "* ]] && continue
    [[ $model_from_env == 1 && "$CONFIG_MODEL_SETTINGS" == *" $key "* ]] && continue
    export "$key=$value"
    CONFIG_FROM_FILE="$CONFIG_FROM_FILE$key "
  done < "$PUNY_CONFIG"
}
load_config

MLX_BASE_URL="${MLX_BASE_URL:-http://127.0.0.1:8081/v1}"
MLX_BASE_URL="${MLX_BASE_URL%/}"
# Parsed here once, so every script agrees on the host and port.
[[ "$MLX_BASE_URL" =~ ^http://(\[([0-9A-Fa-f:]+)\]|([A-Za-z0-9.-]+)):([0-9]{1,5})/v1$ ]] \
  || die "MLX_BASE_URL must look like http://HOST:PORT/v1, not $MLX_BASE_URL"
MLX_HOST="${BASH_REMATCH[2]}${BASH_REMATCH[3]}"
MLX_PORT="$((10#${BASH_REMATCH[4]}))"
(( MLX_PORT >= 1 && MLX_PORT <= 65535 )) || die "the port in MLX_BASE_URL must be between 1 and 65535"

# The model name the agents send. mlx_lm.server maps default_model to the
# model it was started with; any other name makes it load that model instead,
# swapping out the one in memory. The agent configs name it too, so it is
# fixed rather than a setting.
[[ -z "${MLX_MODEL:-}" || "$MLX_MODEL" == default_model ]] \
  || die "MLX_MODEL is not a setting: the agents must send default_model, the model the server was started with. Unset it."
MLX_MODEL="default_model"

# require_free_disk GIB PATH WHAT: dies unless PATH's volume has GIB GiB free.
require_free_disk() {
  local free_kib
  free_kib="$(df -Pk "$2" | awk 'NR==2 {print $4}')"
  [[ "$free_kib" =~ ^[0-9]+$ ]] || die "cannot read the free space of $2 from df"
  (( free_kib >= $1 * 1024 * 1024 )) \
    || die "$3 needs $1 GiB of free disk, and only $((free_kib / 1024 / 1024)) GiB is free on the volume of $2"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' is not installed. $2"
}

# Prints the process id listening on TCP port $1, or nothing.
listener_pid() {
  require_command lsof "It comes with macOS in /usr/sbin; add /usr/sbin to PATH."
  # lsof exits 1 when nothing listens; that is an answer, not an error.
  lsof -nP -t -iTCP:"$1" -sTCP:LISTEN || true
}

# Exits with an explanation unless an mlx_lm.server answers at MLX_BASE_URL.
# mlx_lm.server serves /health at the root, next to /v1.
require_mlx_server() {
  local root="${MLX_BASE_URL%/v1}" body status
  body="$(curl -sS -m 5 -w '\n%{http_code}' "$root/health")" \
    || die "cannot reach $root. Start the server with $PUNY_ROOT/bin/serve-qwen, or set MLX_BASE_URL."
  status="${body##*$'\n'}"
  body="${body%$'\n'*}"
  [[ "$status" == 200 && "$body" == *'"status": "ok"'* ]] \
    || die "$root/health returned HTTP $status ($body), which is not a healthy mlx_lm.server. Another service may own this port; check with: lsof -nP -iTCP -sTCP:LISTEN"
}

# Prints the address ./bin/web-ui listens on, WEB_UI_URL, after checking that
# it is on this Mac: the page can start downloads and the server.
web_ui_url() {
  local url="${WEB_UI_URL:-http://127.0.0.1:8090}"
  url="${url%/}"
  [[ "$url" =~ ^http://(127\.0\.0\.1|localhost|\[::1\]):([0-9]{1,5})$ ]] \
    || die "WEB_UI_URL must look like http://127.0.0.1:PORT; the page only listens on this Mac, not $url"
  printf '%s\n' "$url"
}

# QWEN_CONTEXT is the context window. ./bin/serve-qwen defaults it to what the
# model card states and records it; the launchers read that record through
# load_served_context. QWEN_MAX_OUTPUT is the most tokens one reply may use.
QWEN_CONTEXT="${QWEN_CONTEXT:-}"
QWEN_MAX_OUTPUT="${QWEN_MAX_OUTPUT:-8192}"
[[ -z "$QWEN_CONTEXT" || "$QWEN_CONTEXT" =~ ^[1-9][0-9]*$ ]] \
  || die "QWEN_CONTEXT must be a whole number of tokens"
[[ "$QWEN_MAX_OUTPUT" =~ ^[1-9][0-9]*$ ]] \
  || die "QWEN_MAX_OUTPUT must be a whole number of tokens"

# Where ./bin/serve-qwen records the server it started on MLX_PORT. Keyed by
# port, so localhost and 127.0.0.1 find the same record.
served_record() {
  printf '%s/state/served/port-%s' "$PUNY_ROOT" "$MLX_PORT"
}

# Sets QWEN_CONTEXT, unless already set, to the context window of the server
# that ./bin/serve-qwen started on MLX_PORT.
load_served_context() {
  [[ -z "$QWEN_CONTEXT" ]] || return 0
  require_mlx_server
  local record pid="" context=""
  record="$(served_record)"
  [[ -f "$record" ]] \
    || die "no record of a bin/serve-qwen server on port $MLX_PORT. Start the server with $PUNY_ROOT/bin/serve-qwen, or set QWEN_CONTEXT."
  # read reports end of file when the line has no newline; the values are checked next.
  read -r pid context < "$record" || true
  [[ "$pid" =~ ^[0-9]+$ && "$context" =~ ^[1-9][0-9]*$ ]] \
    || die "$record is damaged; restart the server with $PUNY_ROOT/bin/serve-qwen"
  [[ "$(listener_pid "$MLX_PORT")" == "$pid" ]] \
    || die "the server on port $MLX_PORT was not started by bin/serve-qwen (no listener with pid $pid from $record). Restart it with $PUNY_ROOT/bin/serve-qwen, or set QWEN_CONTEXT."
  QWEN_CONTEXT="$context"
}

# Sends a real Responses API request to the base URL given as $1 and exits
# with an explanation unless a Responses object comes back. A GET status code
# alone cannot tell a missing route from one that only accepts POST.
require_responses_api() {
  local base="${1%/}" request body status
  request="$(MODEL="$MLX_MODEL" python3 -I -c '
import json, os
print(json.dumps({"model": os.environ["MODEL"], "input": "Reply with the word ready.", "max_output_tokens": 16}))
')"
  body="$(curl -sS -m 300 -w '\n%{http_code}' "$base/responses" \
    -H 'Content-Type: application/json' -d "$request")" \
    || die "POST $base/responses failed"
  status="${body##*$'\n'}"
  body="${body%$'\n'*}"
  STATUS="$status" BODY="$body" BASE="$base" python3 -I -c '
import json, os, sys
status, body, base = os.environ["STATUS"], os.environ["BODY"], os.environ["BASE"]
if status == "404":
    sys.exit(f"error: {base}/responses does not exist (HTTP 404). Start the server with bin/serve-qwen, which serves the Responses API that Codex needs; plain mlx_lm.server does not.")
try:
    data = json.loads(body)
except ValueError:
    sys.exit(f"error: {base}/responses answered HTTP {status} with a non-JSON body:\n{body}")
if status == "200" and data.get("object") == "response" and isinstance(data.get("output"), list):
    sys.exit(0)
sys.exit(f"error: {base}/responses answered HTTP {status}, not a Responses API object:\n" + json.dumps(data, indent=2))
'
}
