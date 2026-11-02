# Compatibility notes

What works with each agent, and mlx-lm bugs that agents can hit.

## What works with each agent

| | OpenCode 2.1.1 | Pi 1.3.0 | Codex 0.166.0 |
|---|---|---|---|
| API it uses | Chat Completions | Chat Completions | Responses |
| `./bin/integrate install` | yes | yes | yes, as the profile `codex -p puny` |
| Launcher | `./bin/opencode-qwen` | `./bin/pi-qwen` | `./bin/codex-qwen` |
| `./bin/smoke-test` creates a file through its tools | yes | yes | yes, with the small model on 2026-10-28 |
| `./bin/bench-agents` | yes, benchmarked | yes, benchmarked | yes, benchmarked with the small model only; see below |

On 2026-11-02 the three agents ran the `billing`, `inventory` and `todo` tasks once each with the `small` preset (Qwen3.5-4B) and the default 900-second limit; `kat` could not start for lack of free memory:

| Task | OpenCode | Pi | Codex |
|---|---|---|---|
| billing | passed in 548 s | passed in 388 s | passed in 617 s |
| inventory | passed in 174 s | passed in 119 s | passed in 215 s |
| todo | out of time | out of time | exited with an error after 239 s |

The Codex column is from after two fixes described below, the `apply_patch` call and the ChatGPT apps; before them, Codex passed 1 of 3 in 2,669 s. One run each is too few to rank the agents. `kat` with Codex is still to be run: `./bin/verify kat` does it on a Mac with about 17 GiB free ([models.md](models.md#checking-a-preset-on-another-mac)). With `kat`, OpenCode and Pi passed 23 of 24 runs ([models.md](models.md#reap-against-kat-coder)).

Codex speaks only the Responses API, which mlx-lm 0.32.2 lacks ([#1263](https://github.com/ml-explore/mlx-lm/pull/1263) and [#1207](https://github.com/ml-explore/mlx-lm/pull/1207) would add it; both were open on 2026-10-28), so the server adds it ([how](how-it-works.md#how-codex-is-served)). Codex behaves differently from its OpenAI setup:

- **No apply_patch tool.** Codex offers its freeform `apply_patch` tool only to models it knows, through their entry in its model catalog. For any other model name, such as `default_model`, the tool is left out ([`spec_plan.rs` at 0.163.0](https://github.com/openai/codex/blob/83086188ae0614bd882818ff1aa23f3194962012/codex-rs/core/src/tools/spec_plan.rs#L1432), [`model_info.rs`](https://github.com/openai/codex/blob/83086188ae0614bd882818ff1aa23f3194962012/codex-rs/models-manager/src/model_info.rs#L98-L124)). The model edits files through shell commands instead. A model trained on Codex calls `apply_patch` anyway, and Codex would answer "unsupported call", so the server sends such a call back as the `apply_patch` shell command, which Codex applies itself ([how-it-works.md](how-it-works.md#how-codex-is-served)). Checked on 2026-11-02 with Codex 0.166.0 against a stand-in server: Codex applied the patch. Offering the tool itself would need a `model_catalog_json` entry carrying Codex's whole system prompt.
- **No ChatGPT apps.** With a ChatGPT login, Codex offers about a hundred tools of ChatGPT's app connectors, which run on OpenAI's servers. They made the first request of a run 202 KB instead of 42 KB. The launcher and the profile turn them off with `features.apps = false` ([agents.md](agents.md#codex)).
- **No web search.** Web search runs on OpenAI's servers, so the server leaves that tool out of the request and logs that it did.
- **Text only.** Images attached to a Codex prompt are refused with an error, because the served models read text only.
- **A warning at the start**, "Model metadata for `default_model` not found", because there is no Codex model catalog. Codex then uses its defaults with the profile's context window; it is harmless.
- **Codex is not pinned.** It comes from Homebrew. A new Codex release may send a request item the translation does not know; the server then refuses the request with an error that names the item, rather than guess.

Claude Code is not covered. It speaks Anthropic's Messages API, which `mlx_lm.server` does not serve, and this project leaves it out by choice. Several of the servers in [alternatives.md](alternatives.md) serve that API.

## Known mlx-lm issues

- [ml-explore/mlx-lm#1992](https://github.com/ml-explore/mlx-lm/issues/1992): the `qwen3_coder` parser cuts a value at its first `</parameter>`, so a file write whose content contains that text arrives truncated.
- [ml-explore/mlx-lm#1946](https://github.com/ml-explore/mlx-lm/issues/1946): when a tool call fails to parse, the reply says `finish_reason: "tool_calls"` but carries none.
- `GET /v1/models` in mlx-lm 0.32.2 lists models from the Hugging Face cache, which this setup keeps in `state/cache/huggingface`. When that folder does not exist, the request fails after the headers were sent, and the client gets an empty reply. `./bin/serve-qwen` answers this route itself with `default_model`, the only model it serves, and an empty catalog in the format Codex reads from the same path.
