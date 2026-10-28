"""Finds MLX models, says whether they fit, and lists the downloaded ones.

Usage: python -I models.py search QUERY [--source huggingface|modelscope|all] [--limit N] [--json]
       python -I models.py info MODEL[@REVISION] [--json]
       python -I models.py list [--json]

MODEL is a Hugging Face repository or modelscope:REPO (lib/sources.py).
info resolves REVISION (default main) to a commit and reads the model's
files and config at that commit, without downloading the weights. It
reports:
  - the download size and the weights mlx-lm loads;
  - whether mlx-lm 0.32.2 has the architecture, and which tool-call parser
    it would pick from the chat template; agents need one;
  - the context window from config.json (check it against the model card);
  - the longest conversation that fits next to the weights in the memory
    macOS lets the GPU use, worked out by lib/memory.py as lib/serve.py
    does with the default settings.
It prints the bin/serve-qwen command that serves the model.

Runs in the mlx-lm environment, which it needs for the GPU limit and the
architecture check.
"""

import argparse
import importlib.util
import json
import re
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory  # noqa: E402
import sources  # noqa: E402

# lib/serve.py's defaults: an 8-bit context cache, 512-token prefill steps,
# a 512 MiB margin, and at least 32,768 tokens to start.
KV_BITS = 8
PREFILL_STEP_TOKENS = 512
MARGIN_BYTES = 512 * memory.MIB
MIN_START_TOKENS = 32768
WEIGHTS = re.compile(r"^model.*\.safetensors$")
STATE = Path(__file__).resolve().parent.parent / "state"


def gpu_limit_bytes():
    import mlx.core as mx
    return mx.device_info()["max_recommended_working_set_size"]


def parse_name(text):
    name, _, ref = text.partition("@")
    sources.split(name)
    return name, ref or "main"


def fetch_json(name, commit, path):
    return sources.get_json(sources.file_url(name, commit, path))


def fetch_text(name, commit, path):
    import urllib.request
    request = urllib.request.Request(sources.file_url(name, commit, path), headers={"User-Agent": "puny-local-ai"})
    with urllib.request.urlopen(request, timeout=sources.TIMEOUT_SECONDS) as response:
        return response.read().decode()


def architecture_supported(model_type):
    from mlx_lm.utils import MODEL_REMAPPING
    return importlib.util.find_spec(f"mlx_lm.models.{MODEL_REMAPPING.get(model_type, model_type)}") is not None


def tool_parser(name, commit, paths):
    """The tool-call parser mlx-lm would pick, from the tokenizer config or the chat template."""
    config = fetch_json(name, commit, "tokenizer_config.json") if "tokenizer_config.json" in paths else {}
    if config.get("tool_parser_type"):
        return config["tool_parser_type"]
    template = config.get("chat_template")
    if "chat_template.jinja" in paths:
        template = fetch_text(name, commit, "chat_template.jinja")
    if isinstance(template, list):
        template = "\n".join(t.get("template", "") for t in template)
    if not template:
        return None
    # mlx-lm 0.32.2 picks the parser this way when it loads the tokenizer;
    # only the chat template is needed for the check.
    from mlx_lm.tokenizer_utils import _infer_tool_parser
    return _infer_tool_parser(types.SimpleNamespace(chat_template=template, get_vocab=dict))


def info(text):
    name, ref = parse_name(text)
    commit = sources.head_commit(name, ref)
    files = sources.listed_files(name, commit)
    paths = {f["path"] for f in files}
    if "config.json" not in paths:
        raise sources.SourceError(f"{name} at {commit} has no config.json, so it is not a model mlx-lm can load")
    weights = sum(f["size"] for f in files if WEIGHTS.match(f["path"]))
    full_config = fetch_json(name, commit, "config.json")
    config = full_config.get("text_config", full_config)
    model_type = full_config.get("model_type", "")
    context = config.get("max_position_embeddings")
    result = {
        "name": name, "commit": commit, "url": sources.page_url(name),
        "download_bytes": sum(f["size"] for f in files), "weights_bytes": weights,
        "model_type": model_type, "architecture_supported": bool(model_type) and architecture_supported(model_type),
        "tool_parser": tool_parser(name, commit, paths),
        "quantization": full_config.get("quantization") or full_config.get("quantization_config"),
        "config_context_tokens": context, "gpu_limit_bytes": gpu_limit_bytes(),
        "window_tokens": None, "estimate_problem": None,
        "downloaded": (Path(model_dir(name, commit)) / ".verified").is_file(),
    }
    if not weights:
        result["estimate_problem"] = "no model*.safetensors files, so this is not an MLX conversion"
    else:
        room = result["gpu_limit_bytes"] - weights - MARGIN_BYTES
        try:
            result["window_tokens"] = memory.max_tokens_fitting(
                config, KV_BITS, room, PREFILL_STEP_TOKENS, context or 262144, 0)
        except (memory.UnsupportedModel, KeyError, TypeError) as e:
            result["estimate_problem"] = f"the memory estimate cannot read this config: {e}"
    result["fits"] = bool(result["window_tokens"] and result["window_tokens"] >= min(MIN_START_TOKENS, context or MIN_START_TOKENS))
    result["serve_command"] = (
        f"QWEN_REPO={name} QWEN_REVISION={commit} QWEN_CONTEXT={context or 'TOKENS'} ./bin/serve-qwen"
    )
    return result


def model_dir(name, commit):
    folder = name.replace(sources.PREFIX, "modelscope--", 1).replace("/", "--")
    return STATE / "models" / f"{folder}@{commit}"


def downloaded():
    """The model folders in state/models, verified or not."""
    found = []
    for folder in sorted((STATE / "models").glob("*@*")):
        stem, _, commit = folder.name.rpartition("@")
        owner, _, rest = stem.partition("--")
        if owner == "modelscope":
            owner, _, rest = rest.partition("--")
            name = f"{sources.PREFIX}{owner}/{rest}"
        else:
            name = f"{owner}/{rest}"
        found.append({
            "name": name, "commit": commit, "folder": str(folder),
            "verified": (folder / ".verified").is_file(),
            "size_bytes": sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()),
        })
    return found


def search(query, source, limit):
    names = sources.SOURCES if source == "all" else (source,)
    results, problems = [], []
    for name in names:
        try:
            results += sources.search(query, name, limit)
        except sources.SourceError as e:
            problems.append(f"{name}: {e}")
    if problems and not results:
        raise sources.SourceError("; ".join(problems))
    return results, problems


def size(value):
    return memory.gib(value) if value else "?"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    find = commands.add_parser("search")
    find.add_argument("query")
    find.add_argument("--source", choices=(*sources.SOURCES, "all"), default="all")
    find.add_argument("--limit", type=int, default=15)
    find.add_argument("--json", action="store_true")
    about = commands.add_parser("info")
    about.add_argument("model")
    about.add_argument("--json", action="store_true")
    listing = commands.add_parser("list")
    listing.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        if args.command == "search":
            results, problems = search(args.query, args.source, args.limit)
            if args.json:
                print(json.dumps({"results": results, "problems": problems}))
                return
            for problem in problems:
                print(f"warning: {problem}", file=sys.stderr)
            if not results:
                print(f"No MLX models match {args.query!r}.")
            for r in results:
                print(f"{r['name']:<70} {r['downloads']:>9} downloads  {size(r['size_bytes']):>10}  {r['updated'][:10]}")
            print("\nNext: ./bin/models info MODEL shows whether one fits and how to serve it.")
        elif args.command == "info":
            result = info(args.model)
            if args.json:
                print(json.dumps(result))
                return
            print(f"{result['name']} at {result['commit']}\n  {result['url']}")
            print(f"  download {size(result['download_bytes'])}, weights mlx-lm loads {size(result['weights_bytes'])}")
            print(f"  architecture {result['model_type'] or '?'}: "
                  + ("supported by mlx-lm 0.32.2" if result["architecture_supported"] else "NOT supported by mlx-lm 0.32.2"))
            print(f"  tool calls: {result['tool_parser'] + ' parser' if result['tool_parser'] else 'no parser found; agents cannot edit files with it'}")
            print(f"  context window in config.json: {result['config_context_tokens'] or '?'} tokens; check the model card")
            if result["estimate_problem"]:
                print(f"  memory: {result['estimate_problem']}")
            elif result["fits"]:
                print(f"  memory: fits; {result['window_tokens']} tokens of context fit in the {size(result['gpu_limit_bytes'])} the GPU may use")
            else:
                print(f"  memory: does NOT fit; only {result['window_tokens']} tokens of context fit in the {size(result['gpu_limit_bytes'])} the GPU may use")
            print(f"  {'downloaded and verified' if result['downloaded'] else 'not downloaded'}")
            print(f"\nDownload: ./bin/models download {result['name']}@{result['commit']}")
            print(f"Serve:    {result['serve_command']}")
        else:
            found = downloaded()
            if args.json:
                print(json.dumps(found))
                return
            if not found:
                print("No models downloaded yet.")
            for m in found:
                state = "verified" if m["verified"] else "NOT verified (incomplete download?)"
                print(f"{m['name']}@{m['commit']}  {size(m['size_bytes'])}  {state}")
    except sources.SourceError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
