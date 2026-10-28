# How it works

Implementation details for people who want to know what runs, or want to change it. The [README](../README.md) covers using it; [CONTRIBUTING.md](../CONTRIBUTING.md#layout) maps the files.

## The server

`./bin/serve-qwen` does not run `mlx_lm.server` directly. It runs [`lib/serve.py`](../lib/serve.py), which checks memory and disk, then hands over to mlx-lm's own server in the same process with some of its internals wrapped:

- **Memory and disk guards.** These check each request before mlx-lm processes it, and stop the server before macOS runs out of memory or disk. [models.md](models.md#memory-and-disk-guards) describes them and the measurements behind them.
- **One model only.** Any request for a model name other than `default_model`, an adapter or a draft model is refused. Otherwise mlx-lm would unload the served model and load, or download, the one named, outside every check.
- **`GET /v1/models`** answers with `default_model`. It also carries an empty `models` catalog, the format Codex reads from the same path. See [compatibility.md](compatibility.md#known-mlx-lm-issues) for why mlx-lm's own answer is replaced.
- **`POST /v1/responses`** answers the OpenAI Responses API; see the next section.

`lib/serve.py` hooks into mlx-lm 0.32.2's server classes, so it refuses to run with any other mlx-lm version. Moving the pin means checking each hook again.

## How Codex is served

Codex sends only Responses API requests ([compatibility.md](compatibility.md#differences-from-the-original-guide)), and mlx-lm 0.32.2 answers only Chat Completions. [`lib/responses.py`](../lib/responses.py) translates between them inside the server:

1. Codex posts to `/v1/responses` at the same address the other agents use.
2. The request is translated into a Chat Completions request and sent to the server's own `/v1/chat/completions`. So it passes the same memory guards as a request from OpenCode or Pi, and the model's chat template and tool-call parser apply as usual.
3. The streamed reply is translated back into Responses events as it arrives.

What the translation does with a request:

- Codex puts its instructions in a developer message at the start of `input`. Developer and system messages before the first user message become the one system message that Qwen's chat template allows at the start; later ones become user messages.
- Function calls and their outputs become assistant tool calls and tool messages.
- Freeform (custom) tools, such as Codex's `apply_patch`, become functions with one string argument, `input`, and their grammar goes into the description.
- Tools grouped in a namespace, such as an MCP server's, reach the model as `NAMESPACE__NAME` and go back to Codex under their own name and namespace.
- Reasoning items become the `reasoning_content` of the next assistant message, which Qwen's template renders as earlier thinking.
- Tools that only OpenAI's servers run, such as `web_search`, are left out, and the server logs that it did so.
- `max_output_tokens`, `temperature`, `top_p` and `tool_choice` carry over. A reasoning effort of `none` or `minimal` turns thinking off.
- Anything it does not know, such as an image or an unknown item type, is refused with HTTP 400 and a message naming it. It is never dropped silently.

What it does with the reply:

- Reasoning, text and each tool call become output items, with the delta events Codex displays as they stream.
- The reply ends with `response.completed`, carrying the token usage, or `response.incomplete` when it hit `max_output_tokens`.
- While mlx-lm reads a long prompt, it sends progress comments. These become `response.in_progress` events, so Codex's 300-second idle timer does not run out during a long prefill.
- A refusal from the memory guards becomes an HTTP 400 error with the guard's message. A prompt over the context window gets the code `context_length_exceeded`, which OpenAI also uses for that case.

The translation keeps no state between requests: Codex sends the whole conversation each time, with `store: false`. The [Codex source at 0.163.0](https://github.com/openai/codex/tree/83086188ae0614bd882818ff1aa23f3194962012/codex-rs) defines the request (`codex-api/src/common.rs`) and the events it reads (`codex-api/src/sse/responses.rs`). `./bin/check-responses-api` and `./bin/smoke-test codex` check the translation against the running server.

## Where models come from

[`lib/sources.py`](../lib/sources.py) knows two sources, and both can pin a model to a commit:

| | Hugging Face | ModelScope |
|---|---|---|
| Name | `OWNER/MODEL` | `modelscope:OWNER/MODEL` |
| Search | `/api/models?filter=mlx`, the `mlx` tag | `/openapi/v1/models`; results kept only when tagged `library:mlx` or named like an MLX conversion |
| Current commit | `/api/models/REPO/revision/main` | `/api/v1/models/REPO/commits`, the newest |
| Files at a commit | `/api/models/REPO/tree/COMMIT`: SHA-256 for LFS files, git blob hash for small ones | `/api/v1/models/REPO/repo/files?Revision=COMMIT`: SHA-256 for every file |
| Download | `huggingface_hub.snapshot_download`, into the model folder | `/models/REPO/resolve/COMMIT/FILE`, each file under a `.partial` name until complete |

ModelScope hosts copies of many `mlx-community` models. Its SHA-256 values are the same as Hugging Face's for the same files.

Other places were considered and left out:

- **LM Studio's catalog** has no public search API, and its MLX builds live on Hugging Face under `lmstudio-community`, which the Hugging Face search already finds.
- **Ollama's MLX models** are stored one tensor per layer in its own registry format, which mlx-lm cannot load.
- **hf-mirror.com** redirects to Hugging Face.
- **Modelers.cn** has no documented API for pinning or hashes.

Every download, from either source and through any command, goes the same way. `ensure_model` in [`lib/mlx.sh`](../lib/mlx.sh) checks that the missing bytes plus 5 GiB are free on disk, then downloads. [`lib/verify_model.py`](../lib/verify_model.py) hashes every file against the source's list for the commit. Only then does it write `.verified` in the model folder. A model without `.verified` is never served.

[`lib/models.py`](../lib/models.py) answers `./bin/models info` from the file list and `config.json` alone, without downloading the weights:

- **Weights:** the `model*.safetensors` files at the top level, the files mlx-lm loads.
- **Architecture:** supported when mlx-lm 0.32.2 has a module for the config's `model_type`.
- **Tool calls:** the parser mlx-lm would pick from the chat template, using mlx-lm's own detection.
- **Fit:** the longest conversation that fits next to the weights in the GPU limit. `lib/memory.py` works it out with the same defaults the server uses. A model fits when at least 32,768 tokens do, as the server requires at start-up.

## The web UI

`./bin/web-ui` runs [`lib/webui.py`](../lib/webui.py) in the mlx-lm environment. It serves one page, [`lib/webui.html`](../lib/webui.html), plain HTML and JavaScript with nothing loaded from elsewhere, and a JSON API:

- **Chat** requests go to the page's own server, which passes them to `MLX_BASE_URL` with the model name set to `default_model` and streams the reply back. The browser never talks to the model server directly.
- **Search, info and the downloaded list** call `lib/models.py`.
- **Downloads** run `./bin/models download MODEL@COMMIT`, one at a time, with the same pinning, disk checks and verification as the command line. Logs go to `state/downloads/`. The page accepts only a model pinned to a full commit, the one Check showed.

Because the page can start downloads, it guards against other web sites:

- It listens only on a loopback address; `./bin/web-ui` refuses any other `WEB_UI_URL`.
- It answers only requests whose `Host` header names that address, which stops DNS rebinding.
- It accepts a POST only without an `Origin` header or with its own, which stops cross-site form posts.

It has no login: anyone who can reach `127.0.0.1` on this Mac can use it, as with the model server itself.
