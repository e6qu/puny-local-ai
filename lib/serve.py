"""Runs mlx_lm.server with guards against running out of memory or disk.

Usage: python -I serve.py --model-dir DIR --context TOKENS --kv-bits BITS
           --cache-entries N --margin SIZE --max-prefill-step TOKENS
           --state-dir DIR --base-url URL -- MLX_LM_SERVER_ARGS...

Before loading the model it refuses to start when:
  - not even MIN_START_TOKENS of context fit in the memory macOS lets the GPU
    use, or in the memory free right now;
  - the disk has less than memory.DISK_RESERVE_BYTES free.
When the full context window does not fit the GPU limit, it starts anyway;
the window it records for the agents is the part that fits, and longer
requests are refused.

While serving it:
  - rejects any request whose prompt plus reply would exceed the context
    window, or would not fit in the memory free at that moment, with an error
    the agent shows, instead of letting macOS swap;
  - rejects requests for any model, adapter or draft model other than the
    one it was started with, which mlx-lm would otherwise load unchecked;
  - picks the prefill step for each request, the largest that fits;
  - serves the OpenAI Responses API at /v1/responses, which Codex needs and
    mlx-lm lacks, by translating it to the server's own Chat Completions
    (lib/responses.py), so those requests pass the same guards;
  - lists only default_model at /v1/models, the one name it accepts;
  - keeps sliding-window layers at 16 bits when it quantizes the context
    cache, since mlx-lm cannot quantize them and would fail instead;
  - reads Muse Glimmer's replies as mlx-lm reads Muse Spark's, so its
    reasoning and message headers do not reach the agents as reply text;
  - stops the server, with a message, when free memory or disk falls to an
    emergency floor, for example because another application took the
    memory, or when the watchdog itself fails.

It hooks into mlx_lm.server's internals, so it runs only with the mlx-lm
version it was written against.
"""

import argparse
import importlib
import logging
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory  # noqa: E402
import responses  # noqa: E402

import mlx.core as mx  # noqa: E402
import mlx_lm  # noqa: E402
from mlx_lm import server  # noqa: E402
from mlx_lm.models import muse_glimmer  # noqa: E402
from mlx_lm.models.cache import RotatingKVCache  # noqa: E402

# mlx_lm/__init__.py exports a function named generate, which hides the module.
generate = importlib.import_module("mlx_lm.generate")

MLX_LM_VERSION = "0.32.2"
# Agents send long system prompts; refuse to start when not even this many
# tokens fit.
MIN_START_TOKENS = 32768
WATCH_INTERVAL_SECONDS = 2
DISK_EMERGENCY_FLOOR_BYTES = 1 * memory.GIB
# Server options this script sets itself, because the estimates depend on them.
GUARDED_OPTIONS = (
    "--model", "--kv-bits", "--kv-group-size", "--quantized-kv-start",
    "--prompt-cache-size", "--prompt-cache-bytes", "--prefill-step-size",
    "--adapter-path", "--draft-model",
)


def die(message):
    print(f"error: {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--context", dest="context_tokens", type=int, required=True)
    parser.add_argument("--kv-bits", type=int, required=True)
    parser.add_argument("--cache-entries", type=int, required=True)
    parser.add_argument("--margin", dest="margin_bytes", type=memory.parse_bytes, required=True)
    parser.add_argument("--max-prefill-step", dest="max_prefill_step_tokens", type=int, required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--base-url", required=True, help="the server's own address, http://HOST:PORT/v1, for the Responses API")
    parser.add_argument("--record", help="where to record the process id and the usable window for the launchers")
    parser.add_argument("--print-window", action="store_true", help="print the usable window and exit")
    parser.add_argument("server_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.server_args[:1] == ["--"]:
        args.server_args = args.server_args[1:]
    for option in args.server_args:
        if option.split("=")[0] in GUARDED_OPTIONS:
            die(f"{option} is set by lib/serve.py itself; the memory guard depends on it")
    if args.max_prefill_step_tokens < memory.MIN_PREFILL_STEP_TOKENS:
        die(f"QWEN_PREFILL_STEP must be at least {memory.MIN_PREFILL_STEP_TOKENS}")
    # Free disk is read on the repo folder, which, unlike state/, cannot be
    # deleted while the server runs.
    args.disk_path = str(Path(args.state_dir).resolve().parent)
    return args


def usable_window(args, config, weights_bytes, gpu_limit_bytes):
    """The context window agents are told: QWEN_CONTEXT, or less when the GPU limit allows less."""
    return memory.max_tokens_fitting(
        config, args.kv_bits, gpu_limit_bytes - weights_bytes - args.margin_bytes, args.max_prefill_step_tokens,
        args.context_tokens, args.cache_entries,
    )


def check_startup(args, config, weights_bytes, gpu_limit_bytes):
    cached_bytes = args.cache_entries * memory.kv_cache_bytes(config, args.context_tokens, args.kv_bits)
    conversation_bytes = memory.request_bytes(config, args.context_tokens, memory.MIN_PREFILL_STEP_TOKENS, args.kv_bits)
    full_bytes = weights_bytes + args.margin_bytes + cached_bytes + conversation_bytes
    available_bytes = memory.available_system_bytes()
    room_now_bytes = available_bytes - weights_bytes - args.margin_bytes - memory.SYSTEM_RESERVE_BYTES
    tokens_now = memory.max_tokens_fitting(
        config, args.kv_bits, room_now_bytes, args.max_prefill_step_tokens, args.context_tokens, args.cache_entries
    )
    window_tokens_gpu = usable_window(args, config, weights_bytes, gpu_limit_bytes)
    disk_bytes = memory.free_disk_bytes(args.disk_path)

    print("Memory check:")
    print(f"  {memory.gib(weights_bytes):>10}  model weights_bytes")
    print(f"  {memory.gib(conversation_bytes):>10}  one {args.context_tokens}-token conversation at {args.kv_bits}-bit")
    print(f"  {memory.gib(cached_bytes):>10}  {args.cache_entries} cached prompts (QWEN_PROMPT_CACHE_ENTRIES)")
    print(f"  {memory.gib(args.margin_bytes):>10}  margin (QWEN_MEMORY_MARGIN)")
    print(f"  {memory.gib(full_bytes):>10}  needed for the full context window")
    print(f"  {memory.gib(gpu_limit_bytes):>10}  GPU limit; {window_tokens_gpu} tokens fit within it")
    print(f"  {memory.gib(available_bytes):>10}  free now; {tokens_now} tokens fit right now")
    print(f"  {memory.gib(disk_bytes):>10}  free disk", flush=True)

    shortest_tokens = min(MIN_START_TOKENS, args.context_tokens)
    if window_tokens_gpu < shortest_tokens:
        die(
            f"not even {shortest_tokens} tokens of context fit in the {memory.gib(gpu_limit_bytes)} the GPU may use. "
            "Lower QWEN_PROMPT_CACHE_ENTRIES or QWEN_CONTEXT, or choose a smaller model."
        )
    if window_tokens_gpu < args.context_tokens:
        print(
            f"note: the full {args.context_tokens}-token window needs {memory.gib(full_bytes)}, over the GPU limit, "
            f"so the agents are told {window_tokens_gpu} tokens and longer requests are refused.",
            flush=True,
        )
    if tokens_now < shortest_tokens:
        die(
            f"only {memory.gib(available_bytes)} is free now, enough for {tokens_now} tokens of context. "
            f"Close other applications until about {memory.gib(full_bytes + memory.SYSTEM_RESERVE_BYTES)} is free "
            "for the full context window, then start again."
        )
    if disk_bytes < memory.DISK_RESERVE_BYTES:
        die(
            f"only {memory.gib(disk_bytes)} of disk is free; keep at least {memory.gib(memory.DISK_RESERVE_BYTES)} "
            "free so macOS can swap."
        )


def guard_requests(args, config, gpu_limit_bytes):
    """Checks each request before the server processes it."""
    original = server.ResponseGenerator._tokenize

    def tokenize(self, tokenizer, request, gen_args):
        result = original(self, tokenizer, request, gen_args)
        prompt_tokens = len(result[0])
        tokens = prompt_tokens + gen_args.max_tokens
        if tokens > args.context_tokens:
            raise ValueError(
                f"the prompt has {prompt_tokens} tokens and the reply may use {gen_args.max_tokens}, "
                f"{tokens} in all, over the {args.context_tokens}-token context window. "
                "Compact the conversation or start a new one."
            )
        gpu_room_bytes = gpu_limit_bytes - mx.get_active_memory() - args.margin_bytes
        system_room_bytes = (
            memory.available_system_bytes() + mx.get_cache_memory() - memory.SYSTEM_RESERVE_BYTES
        )
        room_bytes = min(gpu_room_bytes, system_room_bytes)
        step = memory.pick_step(config, tokens, args.kv_bits, room_bytes, args.max_prefill_step_tokens)
        if step is None:
            need_bytes = memory.request_bytes(config, tokens, memory.MIN_PREFILL_STEP_TOKENS, args.kv_bits)
            raise MemoryError(
                f"a {tokens}-token request needs {memory.gib(need_bytes)} but only {memory.gib(room_bytes)} is free. "
                "Close other applications, or compact the conversation."
            )
        # mlx_lm.server reads this right after tokenizing, on the same thread,
        # and serves one request at a time with a quantized context cache.
        self.cli_args.prefill_step_size = step
        logging.info(f"Admitted {tokens} tokens with prefill step {step}; {memory.gib(room_bytes)} free")
        return result

    server.ResponseGenerator._tokenize = tokenize


def guard_models():
    """Refuses requests for any model other than the one being served.

    mlx-lm would otherwise drop the served model and load, or download, the
    one named, outside every memory and disk check.
    """
    original = server.ModelProvider.load

    def load(self, model_path, adapter_path=None, draft_model_path=None):
        if model_path != "default_model" or adapter_path is not None or draft_model_path not in (None, "default_model"):
            raise ValueError(
                "this server only serves default_model, without adapters or a draft model; "
                f"the request asked for model {model_path!r}"
                + (f", adapter {adapter_path!r}" if adapter_path is not None else "")
                + (f", draft model {draft_model_path!r}" if draft_model_path not in (None, "default_model") else "")
            )
        return original(self, model_path, adapter_path, draft_model_path)

    server.ModelProvider.load = load


def keep_sliding_layers_unquantized():
    """Quantizes the context cache as mlx-lm does, except sliding-window layers.

    Once a conversation passes --quantized-kv-start tokens, mlx-lm 0.32.2
    calls to_quantized on every layer's cache
    (https://github.com/ml-explore/mlx-lm/blob/93e6d4a6447b3080861985947f0820e76fcb957d/mlx_lm/generate.py#L305-L310).
    For a RotatingKVCache, the cache of a sliding-window layer such as Muse
    Glimmer's, that raises NotImplementedError
    (https://github.com/ml-explore/mlx-lm/blob/93e6d4a6447b3080861985947f0820e76fcb957d/mlx_lm/models/cache.py#L553-L554),
    so every longer request would fail:
    https://github.com/ml-explore/mlx-lm/issues/1573
    This repeats mlx-lm's function (MIT, Copyright © 2023 Apple Inc.) with
    one check added.
    Those layers hold at most sliding_window tokens, so they stay at 16 bits,
    as lib/memory.py counts them. generate_step looks the function up when it
    runs, so replacing it here takes effect.
    """
    def maybe_quantize_kv_cache(prompt_cache, quantized_kv_start, kv_group_size, kv_bits):
        if kv_bits is None:
            return
        for e, c in enumerate(prompt_cache):
            if isinstance(c, RotatingKVCache):
                continue
            if hasattr(c, "to_quantized") and c.offset >= quantized_kv_start:
                prompt_cache[e] = c.to_quantized(group_size=kv_group_size, bits=kv_bits)

    generate.maybe_quantize_kv_cache = maybe_quantize_kv_cache


def read_glimmer_replies_like_muse_spark():
    """Gives Muse Glimmer the reply format mlx-lm has for Muse Spark.

    Both write each reply as messages with headers: reasoning after
    "to=self<|message|>", the answer after "to=user<|message|>", tool calls
    in <atem:function_calls>. Glimmer's tokenizer_config.json describes this
    in its response_template, the same channels as mlx-lm's Muse Spark format
    (https://github.com/ml-explore/mlx-lm/blob/93e6d4a6447b3080861985947f0820e76fcb957d/mlx_lm/chat_templates/muse_spark.py).
    mlx-lm 0.32.2 attaches that format only to models whose module names it
    in TOKENIZER_DEFAULTS, and muse_glimmer.py does not
    (https://github.com/ml-explore/mlx-lm/blob/93e6d4a6447b3080861985947f0820e76fcb957d/mlx_lm/models/muse_spark.py#L36-L41,
    https://github.com/ml-explore/mlx-lm/blob/93e6d4a6447b3080861985947f0820e76fcb957d/mlx_lm/tokenizer_utils.py#L725-L733).
    Without it the reasoning and the headers reach the agents as reply text.
    The checkpoint's own chat template is kept: a default chat_template_type
    replaces only a missing one.
    """
    if hasattr(muse_glimmer, "TOKENIZER_DEFAULTS"):
        die("mlx-lm's muse_glimmer.py now has TOKENIZER_DEFAULTS; check read_glimmer_replies_like_muse_spark against it")
    muse_glimmer.TOKENIZER_DEFAULTS = {"chat_template_type": "muse_spark"}


def add_routes(base_url):
    """Adds the Responses API and replaces the model list.

    POST /v1/responses is answered through the server's own
    /v1/chat/completions. GET /v1/models lists default_model, the only name
    guard_models accepts; mlx-lm would list the Hugging Face cache instead,
    which this setup keeps elsewhere, and fails when that folder is missing.
    The list also carries an empty "models" catalog, the shape Codex reads
    from the same path; with it, Codex uses its default model settings
    instead of logging a decode error.
    """
    original_post, original_get = server.APIHandler.do_POST, server.APIHandler.do_GET
    chat_url = base_url.rstrip("/") + "/chat/completions"

    def do_POST(self):
        if self.path in responses.PATHS:
            responses.serve(self, chat_url)
        else:
            original_post(self)

    def do_GET(self):
        if self.path.split("?")[0] in ("/v1/models", "/v1/models/default_model"):
            model = {"id": "default_model", "object": "model", "created": self.created, "owned_by": "puny-local-ai"}
            body = model if self.path.split("?")[0].endswith("/default_model") else {
                "object": "list", "data": [model], "models": []}
            responses.send_json(self, 200, body)
        else:
            original_get(self)

    server.APIHandler.do_POST = do_POST
    server.APIHandler.do_GET = do_GET


def watch(args):
    """Stops the server before macOS runs out of memory or disk.

    If a reading fails, the server stops too: it must not run unguarded.
    """
    while True:
        time.sleep(WATCH_INTERVAL_SECONDS)
        try:
            available_bytes = memory.available_system_bytes()
            disk_bytes = memory.free_disk_bytes(args.disk_path)
        except Exception as e:  # noqa: BLE001 - any failure leaves the server unguarded
            print(f"error: stopping the server: the memory watchdog failed: {e!r}", file=sys.stderr, flush=True)
            os._exit(1)
        problem = None
        if available_bytes < memory.EMERGENCY_FLOOR_BYTES:
            problem = f"only {memory.gib(available_bytes)} of memory is available. Close other applications"
        elif disk_bytes < DISK_EMERGENCY_FLOOR_BYTES:
            problem = f"only {memory.gib(disk_bytes)} of disk is free. Free some disk space"
        if problem:
            print(
                f"error: stopping the server to keep macOS responsive: {problem}, then start it again.",
                file=sys.stderr,
                flush=True,
            )
            os._exit(1)


def main():
    args = parse_args()
    if mlx_lm.__version__ != MLX_LM_VERSION:
        die(
            f"lib/serve.py was written against mlx-lm {MLX_LM_VERSION} and hooks into its server; "
            f"this environment has {mlx_lm.__version__}. Check lib/serve.py against the new version first."
        )
    if args.kv_bits not in (4, 8):
        die("QWEN_KV_BITS must be 4 or 8")
    config = memory.load_config(args.model_dir)
    weights_bytes = memory.weights_bytes(args.model_dir)
    gpu_limit_bytes = mx.device_info()["max_recommended_working_set_size"]

    try:
        if args.print_window:
            print(usable_window(args, config, weights_bytes, gpu_limit_bytes))
            return
        check_startup(args, config, weights_bytes, gpu_limit_bytes)
        window_tokens = usable_window(args, config, weights_bytes, gpu_limit_bytes)
    except memory.UnsupportedModel as e:
        die(f"{args.model_dir}: {e}")
    if args.record:
        # This process becomes mlx_lm.server, so the launchers can match the
        # process id with the one listening on the port.
        record = Path(args.record)
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(f"{os.getpid()} {window_tokens}\n")
    print(f"The agents are told the context window is {window_tokens} tokens.", flush=True)

    mx.set_cache_limit(memory.MLX_CACHE_LIMIT_BYTES)
    guard_requests(args, config, gpu_limit_bytes)
    guard_models()
    keep_sliding_layers_unquantized()
    read_glimmer_replies_like_muse_spark()
    add_routes(args.base_url)
    threading.Thread(target=watch, args=(args,), daemon=True).start()

    # The estimates in lib/memory.py assume these values, so they are passed
    # explicitly rather than left to mlx-lm's defaults.
    sys.argv = [
        "mlx_lm.server",
        "--model", args.model_dir,
        "--kv-bits", str(args.kv_bits),
        "--kv-group-size", str(memory.KV_GROUP_SIZE),
        "--quantized-kv-start", str(memory.QUANTIZED_KV_START_TOKENS),
        "--prompt-cache-size", str(args.cache_entries),
        "--prefill-step-size", str(args.max_prefill_step_tokens),
        *args.server_args,
    ]
    server.main()


if __name__ == "__main__":
    main()
