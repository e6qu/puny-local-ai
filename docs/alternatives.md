# Other local inference servers for Apple Silicon

A survey made on 2026-10-28 of projects that offer faster or easier local inference on a Mac than a generic llama.cpp or mlx-lm setup. It is for comparison and for ideas; this project still runs plain mlx-lm 0.32.2.

## How the survey was made

- Hacker News stories since 2025-01-01 were searched through the [Algolia API](https://hn.algolia.com/api), with queries such as "MLX", "Apple Silicon inference", "faster than llama.cpp", "local coding agent" and the project names below.
- Each project's README, docs or code was then read on 2026-10-28, and its release, license and stars were taken from GitHub.

"Serves" below means the README, docs or code says so; none of these were installed or run here. Every speed figure is the project's own claim, usually on a faster Mac than the 24 GiB one this project targets.

## Servers that load ordinary MLX models

These load any MLX checkpoint, such as the KAT-Coder model used here. Each serves the OpenAI Responses API that Codex uses and Anthropic's Messages API, and parses Qwen-style tool calls.

| Project | Release | License | Notes |
|---|---|---|---|
| [oMLX](https://github.com/jundot/omlx) | v0.7.3rc1, 2026-10-27 | Apache-2.0 | Web admin page with Hugging Face model search and download, chat and one-click setup for OpenCode, Codex and Pi; a menu-bar app. Continuous batching and a context cache that spills to SSD; one Show HN reports a coding agent's first-token time falling from 90 s to 1 s ([HN](https://news.ycombinator.com/item?id=47247294)). |
| [Rapid-MLX](https://github.com/raullenchai/Rapid-MLX) | v0.16.10, 2026-10-26 | Apache-2.0 per its README | `rapid-mlx launch codex`, `opencode` or `pi` sets up the agent; 27 tool-call parsers. Claims Qwen3.6-35B decoding at 173 tokens/s on an M4 Pro, against 146 for oMLX and 125 for LM Studio, using multi-token prediction ([HN](https://news.ycombinator.com/item?id=47816238)). |
| [vllm-mlx](https://github.com/waybarrios/vllm-mlx) | v0.6.0, 2026-10-20 | Apache-2.0 | Command line only. Multi-token prediction, a prefix cache and 19 tool-call parsers. The README says it was checked with OpenCode, Pi and Codex. Claims 128 tokens/s for Qwen3-30B-A3B at 4-bit ([HN](https://news.ycombinator.com/item?id=46642846)). |
| [Swama](https://github.com/Trans-N-ai/swama) | v2.5.1, 2026-10-07 | MIT | Swift and MLX, a menu-bar app. Serves part of the Responses API (no `previous_response_id`, which Codex does not need). |
| [mlx-serve](https://github.com/ddalcu/mlx-serve) | v26.10.3, 2026-10-22 | MIT per its README | Written in Zig, no Python. OpenAI, Anthropic and Ollama APIs and a native app. Needs macOS 26.2 or later. |

## Servers built around particular models

These reach their speeds by specializing the engine for a fixed set of models, mostly with speculative decoding. On a 24 GiB Mac they point to smaller or more heavily quantized builds than the one used here. Whether they load an arbitrary MLX fine-tune such as KAT-Coder is not documented.

- **[Kapitsa](https://github.com/nace-ai/kapitsa)** (Apache-2.0, v0.2.23):
  - its own engine with tree speculative decoding;
  - `kapitsa codex`, `opencode` and `pi` launch the agents;
  - claims 161 tokens/s for Qwen3.6-35B-A3B at 4-bit on an M4 Pro with 48 GB, against 127 for oMLX and 89 for Ollama;
  - for 24 GB Macs it offers 2-bit GGUF builds.
- **[Splash](https://github.com/incoai/splash)** (Apache-2.0, from Inco AI):
  - one engine per model;
  - Chat Completions, Responses and Messages APIs;
  - `splash codex` launches Codex;
  - needs an M3 or later and macOS 26.4 or later;
  - LM Studio ships it as an engine;
  - its "DFlash 2" post had 99 points on Hacker News ([HN](https://news.ycombinator.com/item?id=49366792)).
- **[Magnitude](https://github.com/magnitudedev/magnitude)** (Apache-2.0, YC S25):
  - a desktop app that tunes its kernels to the chip;
  - its own model catalog;
  - serves the Responses and Messages APIs under a non-standard `/inference/` path prefix;
  - claims decoding 2.19 times faster than Ollama on average over 11 models;
  - its Launch HN had 194 points ([HN](https://news.ycombinator.com/item?id=49911995)).
- **[MTPLX](https://github.com/youssofal/MTPLX)** (Apache-2.0): multi-token prediction for a few models, OpenAI- and Anthropic-compatible.

## Larger projects that now use MLX

- **Ollama** runs models on MLX by default on Apple Silicon since v0.40.0. "Ollama is now powered by MLX" was the most discussed post in the survey, with 648 points ([HN](https://news.ycombinator.com/item?id=47582482)).
  - It serves `/v1/responses`, and `ollama launch codex` sets up Codex.
  - Its MLX models come from its own registry in its own format, not as MLX checkpoints.
- **LM Studio** serves `/v1/responses`. Its MLX engine is MIT-licensed; the app itself is closed source.
- **llama.cpp's `llama-server`** serves `/v1/responses` and has a built-in web UI. It runs GGUF models, not MLX.

## What this means for this project

- **Codex no longer needs any of these.** The server here answers the Responses API itself ([how-it-works.md](how-it-works.md#how-codex-is-served)). Any of the servers above can still stand in for `./bin/serve-qwen`, with `MLX_BASE_URL` pointing at them. They do not have this project's memory guards, so watch free memory yourself.
- **Most of the speed gains come from speculative decoding and multi-token prediction.** mlx-lm 0.32.2 has a draft-model option, which `lib/serve.py` refuses for now because the memory estimate does not cover a second model. Supporting it would mean measuring a draft model's memory first.
- **oMLX's SSD-backed context cache targets the cost described in [models.md](models.md#agents-get-no-help-from-the-prompt-cache).** There, every agent turn processes the whole conversation again. It is the clearest candidate for a speed-up on this hardware.
- **[mlx-optiq](https://mlx-optiq.com)** publishes per-layer mixed-precision MLX builds, the "OptiQ" in the default model's name. Newer builds from it may give better quality at the same size.
