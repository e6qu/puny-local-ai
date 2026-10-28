# Compatibility notes

What works with each agent, mlx-lm bugs that agents can hit, and where this setup differs from the ChatGPT guide it started from.

## What works with each agent

| | OpenCode 2.1.1 | Pi 1.3.0 | Codex 0.165.0 |
|---|---|---|---|
| API it uses | Chat Completions | Chat Completions | Responses |
| `./bin/integrate install` | yes | yes | yes, as the profile `codex -p puny` |
| Launcher | `./bin/opencode-qwen` | `./bin/pi-qwen` | `./bin/codex-qwen` |
| `./bin/smoke-test` creates a file through its tools | yes | yes | yes, with the small model on 2026-10-28 |
| `./bin/bench-agents` | yes, benchmarked | yes, benchmarked | runs with `BENCH_AGENTS=codex`; not yet benchmarked |

Codex needs no extra setup. mlx-lm has no Responses API ([ml-explore/mlx-lm#1263](https://github.com/ml-explore/mlx-lm/pull/1263) and [#1207](https://github.com/ml-explore/mlx-lm/pull/1207), which would add one, were open on 2026-10-28, and release 0.32.2 has none), so `./bin/serve-qwen` adds it: [how-it-works.md](how-it-works.md#how-codex-is-served) describes the translation. Codex behaves differently from its OpenAI setup in a few ways:

- **No apply_patch tool.** Codex offers its freeform `apply_patch` tool only to models it knows, through their entry in its model catalog. For any other model name, such as `default_model`, the tool is left out ([`spec_plan.rs` at 0.163.0](https://github.com/openai/codex/blob/83086188ae0614bd882818ff1aa23f3194962012/codex-rs/core/src/tools/spec_plan.rs#L1432), [`model_info.rs`](https://github.com/openai/codex/blob/83086188ae0614bd882818ff1aa23f3194962012/codex-rs/models-manager/src/model_info.rs#L98-L124)). The model edits files through shell commands instead. The translation handles freeform tools too, so a `model_catalog_json` that enables `apply_patch` would work, but it is not set up here.
- **No web search.** Web search runs on OpenAI's servers, so the server leaves that tool out of the request and logs that it did.
- **Text only.** Images attached to a Codex prompt are refused with an error, because the served models read text only.
- **A warning at the start.** Codex prints "Model metadata for `default_model` not found. Defaulting to fallback metadata". The server has no Codex model catalog to offer, so Codex uses its defaults for an unknown model, with the context window from the profile. The runs above worked with this warning.
- **Codex is not pinned.** It comes from Homebrew. A new Codex release may send a request item the translation does not know; the server then refuses the request with an error that names the item, rather than guess.

Claude Code is not covered. It speaks Anthropic's Messages API, which `mlx_lm.server` does not serve, and this project leaves it out by choice. Several of the servers in [alternatives.md](alternatives.md) serve that API.

## Known mlx-lm issues

- [ml-explore/mlx-lm#1992](https://github.com/ml-explore/mlx-lm/issues/1992): the `qwen3_coder` parser cuts a value at its first `</parameter>`, so a file write whose content contains that text arrives truncated.
- [ml-explore/mlx-lm#1946](https://github.com/ml-explore/mlx-lm/issues/1946): when a tool call fails to parse, the reply says `finish_reason: "tool_calls"` but carries none.
- `GET /v1/models` in mlx-lm 0.32.2 lists models from the Hugging Face cache, which this setup keeps in `state/cache/huggingface`. When that folder does not exist, the request fails after the headers were sent, and the client gets an empty reply. `./bin/serve-qwen` answers this route itself with `default_model`, the only model it serves, and an empty catalog in the format Codex reads from the same path.

## Differences from the original guide

The guide this setup started from was out of date in several places:

- **OpenCode v2 renamed its config keys.** `provider`, `npm`, `options` and `model` became `providers`, `package`, `settings` and `modelID` ([migration notes](https://opencode.ai/v2/docs/migrate-v1/)). OpenCode 1 does not read the new keys, so `./bin/integrate` and `./bin/opencode-qwen` stop unless OpenCode 2.0 or later is installed. Config injected through `OPENCODE_CONFIG_CONTENT` only applies with `--standalone`, because the shared background service does not see the launcher's environment. That service reads the config files when first used and keeps them until `opencode service restart`.
- **Pi moved to a new npm scope.** `@mariozechner/pi-coding-agent` is deprecated in favor of `@earendil-works/pi-coding-agent`. Pi reads `models.json` only from its agent directory, so `./bin/pi-qwen` gives Pi its own directory, `state/pi-agent`, through `PI_CODING_AGENT_DIR`. Sessions from these runs live there too.
- **Codex speaks only the Responses API.** `wire_api = "chat"` has been a hard error since Codex 0.95.0 ([openai/codex#10157](https://github.com/openai/codex/pull/10157)). The guide's Chat Completions setup no longer starts, so the server here answers the Responses API itself.
- **The model name matters to the server.** A client-side alias such as `qwen-local` makes `mlx_lm.server` try to load a model by that name, so the agents send `default_model` ([details](agents.md#the-model-name-is-always-default_model)).
- **Qwen thinking text arrives in a `reasoning` field.** The OpenCode and Pi configs tell each agent to read it there ([`server.py` line 1511](https://github.com/ml-explore/mlx-lm/blob/93e6d4a6447b3080861985947f0820e76fcb957d/mlx_lm/server.py#L1511)). Pi switches thinking on and off through `chat_template_kwargs`.
- **The guide's model does not fit.** The unpruned Qwen3.6-35B-A3B at 4-bit needs more than the 16 GiB the GPU may use; [models.md](models.md#memory-budget) explains the default instead.
