"""Memory and disk estimates for mlx_lm.server, shared by lib/serve.py.

The figures come from measuring prefill peaks with mlx-lm 0.32.2 and an 8-bit
context cache (mx.get_peak_memory, cache limit 0) on 2026-10-22:

  - Qwen3.5-4B at 16K, 32K and 64K tokens with steps of 32 to 512: the peak
    above the weights was the context cache, plus 1.9 to 2.1 times the
    attention score matrix of one prefill step, plus at most 0.71 GiB.
  - KAT-Coder at 8K tokens with a step of 64: 0.26 GiB beyond the cache and
    the scores.

With a quantized cache, mlx-lm computes attention without the fused kernel
and holds the scores of one step in memory, 2 bytes for each query head,
step token and context token:
https://github.com/ml-explore/mlx-lm/blob/93e6d4a6447b3080861985947f0820e76fcb957d/mlx_lm/models/base.py
"""

import json
import os
import re
import subprocess
from pathlib import Path

GIB = 2**30
MIB = 2**20

# mlx_lm.server keeps the first tokens of the context cache at 16 bits and
# quantizes it once it grows past this many tokens (--quantized-kv-start).
# This and KV_GROUP_SIZE repeat mlx-lm 0.32.2's defaults:
# https://github.com/ml-explore/mlx-lm/blob/93e6d4a6447b3080861985947f0820e76fcb957d/mlx_lm/server.py#L2062-L2073
QUANTIZED_KV_START_TOKENS = 5000
# Quantized arrays also store a 16-bit scale and bias per group of values
# (--kv-group-size).
KV_GROUP_SIZE = 64

# Measured above, rounded up.
SCORE_COPIES = 2.25
FIXED_OVERHEAD_BYTES = 768 * MIB
# Freed buffers MLX keeps for reuse; lib/serve.py caps them at this.
MLX_CACHE_LIMIT_BYTES = 256 * MIB

# Memory left for macOS and other applications after a request is admitted.
SYSTEM_RESERVE_BYTES = 1 * GIB
# The watchdog stops the server when available memory falls below this.
EMERGENCY_FLOOR_BYTES = 512 * MIB
# Free disk kept for macOS swap and logs.
DISK_RESERVE_BYTES = 5 * GIB

# Prefill steps tried, largest first. Smaller steps hold smaller score
# matrices; on Qwen3.5-4B at 32K tokens, step 32 took 14% longer than 512.
MIN_PREFILL_STEP_TOKENS = 16


def parse_bytes(text):
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([KMG]?)", text.strip().upper())
    if not match:
        raise SystemExit(f"error: cannot read {text!r} as a size such as 512M or 1G")
    number, unit = match.groups()
    return int(float(number) * {"": 1, "K": 2**10, "M": 2**20, "G": 2**30}[unit])


def gib(size_bytes):
    """A byte count as GiB, for messages."""
    return f"{size_bytes / GIB:.2f} GiB"


def load_config(model_dir):
    config = json.loads((Path(model_dir) / "config.json").read_text())
    return config.get("text_config", config)


def weights_bytes(model_dir):
    # The same files mlx_lm.utils.load_model reads; add-ons in subfolders,
    # such as OptiQ's optiq/, are never loaded.
    files = list(Path(model_dir).glob("model*.safetensors"))
    if not files:
        raise SystemExit(f"error: no model*.safetensors files in {model_dir}")
    return sum(f.stat().st_size for f in files)


class UnsupportedModel(ValueError):
    pass


def layer_counts(config):
    """(full-attention layers, linear-attention layers), as mlx-lm builds them."""
    layers = config["num_hidden_layers"]
    layer_types = config.get("layer_types")
    if layer_types:
        unknown = sorted(set(layer_types) - {"full_attention", "linear_attention"})
        if unknown:
            raise UnsupportedModel(
                f"the memory estimate does not know the layer types {', '.join(unknown)}; "
                "it covers full and linear attention only"
            )
        full_layers = layer_types.count("full_attention")
        return full_layers, len(layer_types) - full_layers
    interval = config.get("full_attention_interval")
    if interval:
        # mlx-lm's Qwen3.5 models make every interval-th layer full attention.
        full_layers = sum(1 for i in range(layers) if (i + 1) % interval == 0)
        return full_layers, layers - full_layers
    return layers, 0


def kv_values_per_token(config):
    """Keys and values one token adds across the full-attention layers."""
    full_layers, _ = layer_counts(config)
    heads = config["num_attention_heads"]
    kv_heads = config.get("num_key_value_heads") or heads
    head_dim = config.get("head_dim") or config["hidden_size"] // heads
    return full_layers * 2 * kv_heads * head_dim


def kv_cache_bytes(config, tokens, kv_bits):
    """Context cache for one conversation of `tokens` tokens.

    mlx-lm keeps the cache at 16 bits until it reaches QUANTIZED_KV_START_TOKENS
    tokens, then quantizes it (generate.py, maybe_quantize_kv_cache).
    """
    full_layers, linear_layers = layer_counts(config)
    values_per_token = kv_values_per_token(config)
    if tokens >= QUANTIZED_KV_START_TOKENS:
        bytes_per_value = kv_bits / 8 + 2 * 2 / KV_GROUP_SIZE
    else:
        bytes_per_value = 2
    attention_part_bytes = values_per_token * bytes_per_value * tokens

    # Linear-attention layers keep a fixed-size 32-bit state per conversation.
    linear_part_bytes = 0
    if linear_layers:
        linear_part_bytes = (
            linear_layers
            * config["linear_num_value_heads"]
            * config["linear_key_head_dim"]
            * config["linear_value_head_dim"]
            * 4
        )
    return int(attention_part_bytes + linear_part_bytes)


def attention_bytes(config, step_tokens, tokens):
    """Score matrices held while one prefill step attends to `tokens` tokens."""
    return int(SCORE_COPIES * config["num_attention_heads"] * min(step_tokens, tokens) * tokens * 2)


def growth_bytes(config, tokens, kv_bits):
    """Copies held for a moment while the cache grows.

    The cache grows by concatenation, which copies one layer's cache, and the
    16-bit cache and its quantized copy exist together once, when the cache
    is quantized (mlx-lm cache.py, KVCache.update_and_fetch and to_quantized).
    """
    full_layers, _ = layer_counts(config)
    one_layer_bytes = kv_cache_bytes(config, tokens, kv_bits) // max(full_layers, 1)
    if tokens < QUANTIZED_KV_START_TOKENS:
        return one_layer_bytes
    # The two copies at the switch outweigh one layer's growth only for short
    # conversations; taking the larger keeps the estimate from ever falling.
    at_switch_bytes = kv_values_per_token(config) * 2 * QUANTIZED_KV_START_TOKENS
    return max(one_layer_bytes, at_switch_bytes)


def request_bytes(config, tokens, step_tokens, kv_bits):
    """Memory one request of `tokens` tokens adds on top of the loaded model.

    It never falls as `tokens` grows, so a bisection over it is sound.
    """
    return (
        kv_cache_bytes(config, tokens, kv_bits)
        + growth_bytes(config, tokens, kv_bits)
        + attention_bytes(config, step_tokens, tokens)
        + FIXED_OVERHEAD_BYTES
        + MLX_CACHE_LIMIT_BYTES
    )


def prefill_steps(max_step_tokens):
    """Steps to try, from max_step down, always ending at MIN_PREFILL_STEP_TOKENS."""
    if max_step_tokens < MIN_PREFILL_STEP_TOKENS:
        raise ValueError(f"the prefill step must be at least {MIN_PREFILL_STEP_TOKENS}")
    step_tokens = max_step_tokens
    while step_tokens > MIN_PREFILL_STEP_TOKENS:
        yield step_tokens
        step_tokens //= 2
    yield MIN_PREFILL_STEP_TOKENS


def pick_step(config, tokens, kv_bits, room_bytes, max_step_tokens):
    """The largest prefill step whose request fits in `room`, or None."""
    for step_tokens in prefill_steps(max_step_tokens):
        if request_bytes(config, tokens, step_tokens, kv_bits) <= room_bytes:
            return step_tokens
    return None


def max_tokens_fitting(config, kv_bits, room_bytes, max_step_tokens, limit_tokens, cache_entries=0):
    """The most tokens, up to `limit`, a request can hold within `room`.

    Each of `cache_entries` cached prompts is counted at the same length.
    """
    def fits(tokens):
        left_bytes = room_bytes - cache_entries * kv_cache_bytes(config, tokens, kv_bits)
        return pick_step(config, tokens, kv_bits, left_bytes, max_step_tokens) is not None

    low, high = 0, limit_tokens
    while low < high:
        mid = (low + high + 1) // 2
        low, high = (mid, high) if fits(mid) else (low, mid - 1)
    return low


def available_from_vm_stat(out):
    """What macOS can hand out without swapping, from `vm_stat` output.

    Free pages, file-backed pages (cached files, dropped without writing) and
    purgeable pages count in full. Inactive app memory beyond the file-backed
    pages has to go through the memory compressor, so it counts only for the
    room compressing it frees, at the compression ratio vm_stat reports now.
    """
    match = re.search(r"page size of (\d+) bytes", out)
    if not match:
        raise RuntimeError("vm_stat did not report its page size")
    page_size_bytes = int(match.group(1))
    pages = {}
    for line in out.splitlines()[1:]:
        name, _, value = line.partition(":")
        if value.strip().rstrip(".").isdigit():
            pages[name.strip().strip('"')] = int(value.strip().rstrip("."))
    needed = ["Pages free", "Pages inactive", "Pages purgeable", "File-backed pages",
              "Pages stored in compressor", "Pages occupied by compressor"]
    missing = [name for name in needed if name not in pages]
    if missing:
        raise RuntimeError(f"vm_stat did not report {', '.join(missing)}")
    stored_pages = pages["Pages stored in compressor"]
    occupied_pages = pages["Pages occupied by compressor"]
    # With nothing compressed yet there is no ratio to go by; count nothing.
    saving = 1 - occupied_pages / stored_pages if stored_pages else 0
    inactive_app_pages = max(0, pages["Pages inactive"] - pages["File-backed pages"])
    available_pages = (
        pages["Pages free"]
        + pages["File-backed pages"]
        + pages["Pages purgeable"]
        + int(inactive_app_pages * max(0.0, saving))
    )
    return available_pages * page_size_bytes


def available_system_bytes():
    out = subprocess.run(["vm_stat"], capture_output=True, text=True, check=True).stdout
    return available_from_vm_stat(out)


def free_disk_bytes(path):
    stat = os.statvfs(path)
    return stat.f_bavail * stat.f_frsize
