# shellcheck shell=bash disable=SC2034
# The pinned models and the mlx-lm environment. Source after common.sh.
#
# Nothing here is vendored: mlx-lm and its dependencies are downloaded at the
# versions and hashes in lib/mlx-lm.lock, and models at a pinned commit, then
# checked against the hashes their source lists for that commit. A model is a
# Hugging Face repository, or modelscope:REPO for one on ModelScope
# (lib/sources.py).

# An exact, uv-managed Python, so every machine gets the same interpreter.
PYTHON_VERSION="3.14.6"
# Keep uv's and Hugging Face's downloads and caches inside state/, where the
# disk checks look, instead of ~/.cache and ~/.local/share.
export UV_CACHE_DIR="$PUNY_ROOT/state/cache/uv"
export UV_PYTHON_INSTALL_DIR="$PUNY_ROOT/state/python"
export HF_HOME="$PUNY_ROOT/state/cache/huggingface"
MODEL_PRESETS=(kat reap small)
MLX_ENV_DIR="$PUNY_ROOT/state/mlx-env"
MLX_LOCK="$PUNY_ROOT/lib/mlx-lm.lock"

# preset_info NAME sets preset_repo, preset_revision, preset_temp,
# preset_context (the context window its model card states), preset_about
# and preset_upstream (the original model it was converted from,
# as REPO@COMMIT) for one of MODEL_PRESETS; ./bin/licenses reads the licenses of
# both. The unpruned Qwen3.6-35B-A3B at 4-bit (19 GiB) is over the 16 GiB that
# macOS lets the GPU use on a 24 GiB Mac without sudo.
preset_info() {
  case "$1" in
    kat)
      preset_repo="mlx-community/KAT-Coder-V2.5-Dev-OptiQ-4bit-REAP-18B"
      preset_revision="b33dfde62c2753841aa2b1662529bf7f3b1fdde5"
      preset_temp="0.8"
      # "maximum context length 262,144 tokens":
      # https://huggingface.co/Kwaipilot/KAT-Coder-V2.5-Dev/blob/7be56fe773e72b6f5ca93c1ae45d828ddb893922/README.md?code=true#L218
      preset_context="262144"
      preset_about="KAT-Coder, the default model (11.4 GiB download)"
      preset_upstream="Kwaipilot/KAT-Coder-V2.5-Dev@7be56fe773e72b6f5ca93c1ae45d828ddb893922"
      ;;
    reap)
      preset_repo="mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit-REAP-19B"
      preset_revision="95193a560399d81cd6272c393db8e96d59f54f50"
      preset_temp="0.6"
      # "Context Length: 262,144 natively":
      # https://huggingface.co/Qwen/Qwen3.6-35B-A3B/blob/995ad96eacd98c81ed38be0c5b274b04031597b0/README.md?code=true#L55
      preset_context="262144"
      preset_about="pruned Qwen3.6-35B-A3B, the previous default (14 GiB download)"
      preset_upstream="Qwen/Qwen3.6-35B-A3B@995ad96eacd98c81ed38be0c5b274b04031597b0"
      ;;
    small)
      preset_repo="mlx-community/Qwen3.5-4B-4bit"
      preset_revision="0e7ffd5c629ef7719d4cbc04069232580bfa9d9c"
      preset_temp="0.6"
      # "Context Length: 262,144 natively":
      # https://huggingface.co/Qwen/Qwen3.5-4B/blob/851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a/README.md?code=true#L64
      preset_context="262144"
      preset_about="Qwen3.5-4B, for quick tests (2.8 GiB download)"
      preset_upstream="Qwen/Qwen3.5-4B@851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a"
      ;;
    *) die "unknown model preset '$1'; expected ${MODEL_PRESETS[*]}" ;;
  esac
}

# resolve_model_settings sets QWEN_REPO, QWEN_REVISION, QWEN_CONTEXT, QWEN_TEMP,
# QWEN_KV_BITS, QWEN_PROMPT_CACHE_ENTRIES, QWEN_MEMORY_MARGIN and
# QWEN_PREFILL_STEP from the environment and the preset, and checks them.
# ./bin/serve-qwen and ./bin/integrate both use it, so they describe the same
# server. bin/serve-qwen documents each setting.
resolve_model_settings() {
  if [[ -n "${QWEN_REPO:-}" || -n "${QWEN_REVISION:-}" ]]; then
    [[ -n "${QWEN_REPO:-}" && -n "${QWEN_REVISION:-}" ]] \
      || die "set QWEN_REPO and QWEN_REVISION together"
    [[ -z "${QWEN_PRESET:-}" ]] || die "set either QWEN_PRESET or QWEN_REPO, not both"
    [[ -n "$QWEN_CONTEXT" ]] \
      || die "set QWEN_CONTEXT with QWEN_REPO, to the context window its model card states"
    preset_temp="0.6"
    preset_context="$QWEN_CONTEXT"
  else
    preset_info "${QWEN_PRESET:-kat}"
    QWEN_REPO="$preset_repo"
    QWEN_REVISION="$preset_revision"
  fi
  QWEN_CONTEXT="${QWEN_CONTEXT:-$preset_context}"
  QWEN_KV_BITS="${QWEN_KV_BITS:-8}"
  [[ "$QWEN_KV_BITS" == 4 || "$QWEN_KV_BITS" == 8 ]] || die "QWEN_KV_BITS must be 4 or 8"
  QWEN_PROMPT_CACHE_ENTRIES="${QWEN_PROMPT_CACHE_ENTRIES:-0}"
  [[ "$QWEN_PROMPT_CACHE_ENTRIES" =~ ^[0-9]+$ ]] \
    || die "QWEN_PROMPT_CACHE_ENTRIES must be a whole number"
  QWEN_MEMORY_MARGIN="${QWEN_MEMORY_MARGIN:-512M}"
  [[ "$QWEN_MEMORY_MARGIN" =~ ^[0-9]+(\.[0-9]+)?[KMGkmg]?$ ]] \
    || die "QWEN_MEMORY_MARGIN must be a size such as 512M or 1G"
  QWEN_PREFILL_STEP="${QWEN_PREFILL_STEP:-512}"
  if [[ ! "$QWEN_PREFILL_STEP" =~ ^[1-9][0-9]*$ ]] || (( QWEN_PREFILL_STEP < 16 )); then
    die "QWEN_PREFILL_STEP must be a whole number of at least 16"
  fi
  QWEN_TEMP="${QWEN_TEMP:-$preset_temp}"
  [[ "$QWEN_TEMP" =~ ^[0-9]+(\.[0-9]+)?$ ]] || die "QWEN_TEMP must be a number such as 0.6"
}

# guard_args sets guard, the lib/serve.py options for the resolved settings
# and model_dir.
guard_args() {
  guard=(
    --model-dir "$model_dir"
    --context "$QWEN_CONTEXT"
    --kv-bits "$QWEN_KV_BITS"
    # A cap on entries, not bytes: mlx-lm 0.32.2 never applies
    # --prompt-cache-bytes with a quantized cache, only on its batched path.
    --cache-entries "$QWEN_PROMPT_CACHE_ENTRIES"
    --margin "$QWEN_MEMORY_MARGIN"
    --max-prefill-step "$QWEN_PREFILL_STEP"
    --state-dir "$PUNY_ROOT/state"
    --record "$(served_record)"
    # The server answers /v1/responses through its own chat endpoint here.
    --base-url "$MLX_BASE_URL"
  )
}

model_dir_for() {
  local name="${1/#modelscope:/modelscope--}"
  printf '%s/state/models/%s@%s' "$PUNY_ROOT" "${name//\//--}" "$2"
}

# What an installed environment records about itself: the lock and Python.
mlx_env_stamp() {
  printf 'lock %s python %s\n' "$(shasum -a 256 "$MLX_LOCK" | cut -d' ' -f1)" "$PYTHON_VERSION"
}

mlx_env_ready() {
  [[ -f "$MLX_ENV_DIR/.installed" && "$(<"$MLX_ENV_DIR/.installed")" == "$(mlx_env_stamp)" ]]
}

# Exits if a server started by ./bin/serve-qwen still runs from the environment.
require_no_running_server() {
  local record pid context
  for record in "$PUNY_ROOT"/state/served/port-*; do
    [[ -f "$record" ]] || continue  # the pattern itself, when nothing matches
    pid=""
    # read reports end of file when the line has no newline; the value is checked next.
    read -r pid context < "$record" || true
    [[ "$pid" =~ ^[0-9]+$ ]] || die "$record is damaged; delete it"
    if ps -p "$pid" -o command= | grep -qF "$PUNY_ROOT/lib/serve.py"; then
      die "the server started by bin/serve-qwen (pid $pid) still runs from $MLX_ENV_DIR; stop it before reinstalling mlx-lm"
    fi
  done
}

# Installs lib/mlx-lm.lock into state/mlx-env with hash checking, unless that
# exact lock is already installed. Sets mlx_python.
ensure_mlx_env() {
  [[ "$(uname -sm)" == "Darwin arm64" ]] \
    || die "mlx-lm needs a Mac with Apple Silicon; this is $(uname -sm)"
  if ! mlx_env_ready; then
    require_command uv "Install it with: brew install uv"
    require_no_running_server
    # The environment and Python take about 1.5 GiB; the rest keeps room for swap.
    require_free_disk 6 "$PUNY_ROOT" "Installing mlx-lm"
    echo "Installing Python $PYTHON_VERSION and mlx-lm from lib/mlx-lm.lock into $MLX_ENV_DIR"
    rm -rf "$MLX_ENV_DIR"
    uv venv -q --python "$PYTHON_VERSION" --python-preference only-managed "$MLX_ENV_DIR"
    uv pip install -q --python "$MLX_ENV_DIR/bin/python" --require-hashes -r "$MLX_LOCK"
    mlx_env_stamp > "$MLX_ENV_DIR/.installed"
  fi
  mlx_python="$MLX_ENV_DIR/bin/python"
}

# ensure_model MODEL REVISION downloads the model at that commit unless it is
# already verified, and checks every file against the hashes its source
# lists for the commit. MODEL is a Hugging Face repository or modelscope:REPO.
# Sets model_dir.
ensure_model() {
  local repo="$1" revision="$2"
  [[ "$revision" =~ ^[0-9a-f]{40}$ ]] \
    || die "the revision of $repo must be a full 40-character commit hash, not a branch name"
  ensure_mlx_env
  model_dir="$(model_dir_for "$repo" "$revision")"
  # .verified is written only after every file matches.
  if [[ ! -f "$model_dir/.verified" ]]; then
    echo "Downloading $repo at $revision to $model_dir"
    mkdir -p "$model_dir"
    REPO="$repo" REVISION="$revision" DEST="$model_dir" LIB="$PUNY_ROOT/lib" "$mlx_python" -I -c '
import os, sys
from pathlib import Path
sys.path.insert(0, os.environ["LIB"])
import memory, sources
repo, revision, dest = os.environ["REPO"], os.environ["REVISION"], Path(os.environ["DEST"])
try:
    files = sources.listed_files(repo, revision)
except sources.SourceError as e:
    sys.exit(f"error: cannot list the files of {repo} at {revision}: {e}")
missing = sum(
    f["size"] for f in files
    if not ((dest / f["path"]).is_file() and (dest / f["path"]).stat().st_size == f["size"])
)
free = memory.free_disk_bytes(dest)
if missing + memory.DISK_RESERVE_BYTES > free:
    sys.exit(f"error: the download needs {memory.gib(missing)} plus {memory.gib(memory.DISK_RESERVE_BYTES)} kept free, and only {memory.gib(free)} is free.")
if sources.split(repo)[0] == "huggingface":
    from huggingface_hub import snapshot_download
    snapshot_download(repo, revision=revision, local_dir=dest)
else:
    try:
        sources.download(repo, revision, dest, files)
    except sources.SourceError as e:
        sys.exit(f"error: {e}. Run the same command again to continue the download.")
'
    local status=0
    "$mlx_python" -I "$PUNY_ROOT/lib/verify_model.py" "$repo" "$revision" "$model_dir" || status=$?
    case "$status" in
      0) ;;
      1) die "$model_dir does not match $repo at $revision; delete it and run again to download it afresh" ;;
      *) die "could not verify $model_dir against its source; check the network and run again (the download is kept)" ;;
    esac
  fi
}
