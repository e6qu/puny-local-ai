# Compatibility notes

What stops Codex today, mlx-lm bugs that agents can hit, and where this setup differs from the ChatGPT guide it started from.

## Codex needs a Responses API proxy

Codex 0.163 only speaks the OpenAI Responses API. `wire_api = "chat"` is a hard error since Codex 0.95.0 ([openai/codex#10157](https://github.com/openai/codex/pull/10157)). `mlx_lm.server` has no `/v1/responses` route, neither in release 0.32.2 nor on `main` as of 2026-10-20; [ml-explore/mlx-lm#1263](https://github.com/ml-explore/mlx-lm/pull/1263) would add one but is unmerged.

So `./bin/codex-qwen` checks for the endpoint and stops with an explanation when it is missing. Once a proxy that translates Responses to Chat Completions runs in front of MLX, start Codex with `CODEX_BASE_URL=http://HOST:PORT/v1 ./bin/codex-qwen`, or add it to Codex's own config with `CODEX_BASE_URL=http://HOST:PORT/v1 ./bin/integrate install codex` ([README](../README.md#add-the-model-to-your-agents-own-configs)).

## Known mlx-lm issues

- [ml-explore/mlx-lm#1992](https://github.com/ml-explore/mlx-lm/issues/1992): the `qwen3_coder` parser cuts a value at its first `</parameter>`, so a file write whose content contains that text arrives truncated.
- [ml-explore/mlx-lm#1946](https://github.com/ml-explore/mlx-lm/issues/1946): when a tool call fails to parse, the reply says `finish_reason: "tool_calls"` but carries none.

## Differences from the original guide

The guide this setup started from was out of date in several places:

- **OpenCode v2 renamed its config keys.** `provider`, `npm`, `options` and `model` became `providers`, `package`, `settings` and `modelID` ([migration notes](https://opencode.ai/v2/docs/migrate-v1/)). OpenCode 1 does not read the new keys, so `./bin/integrate` and `./bin/opencode-qwen` stop unless OpenCode 2.0 or later is installed. Config injected through `OPENCODE_CONFIG_CONTENT` only applies with `--standalone`, because the shared background service does not see the launcher's environment, and that service reads the config files when first used and keeps them until `opencode service restart`.
- **Pi moved to a new npm scope.** `@mariozechner/pi-coding-agent` is deprecated in favor of `@earendil-works/pi-coding-agent`. Pi reads `models.json` only from its agent directory, so `./bin/pi-qwen` gives Pi its own directory, `state/pi-agent`, through `PI_CODING_AGENT_DIR`. Sessions from these runs live there too.
- **Codex cannot reach mlx-lm directly**, as explained above.
- **The model name matters to the server.** A client-side alias such as `qwen-local` makes `mlx_lm.server` try to load a model by that name, so the agents send `default_model`.
- **Qwen thinking text arrives in a `reasoning` field.** The OpenCode and Pi configs tell each agent to read it ([`server.py` line 1511](https://github.com/ml-explore/mlx-lm/blob/93e6d4a6447b3080861985947f0820e76fcb957d/mlx_lm/server.py#L1511)). Pi switches thinking on and off through `chat_template_kwargs`.
- **Claude Code is not covered.** It speaks Anthropic's Messages API, which `mlx_lm.server` does not serve, and this project leaves it out by choice.

