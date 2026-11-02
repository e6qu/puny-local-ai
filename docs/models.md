# Models, memory and benchmarks

Why the default model is KAT-Coder, how `./bin/serve-qwen` decides whether a model fits, and how the candidates compared. Settings named here are described in the [README](../README.md#settings). To look for other models yourself, use `./bin/models` or `./bin/web-ui` ([README](../README.md#finding-and-serving-other-models)); they run the same memory estimate as the server.

## Memory budget

macOS lets the GPU use at most 16 GiB on this 24 GiB machine (`max_recommended_working_set_size` from `mlx.core.device_info()`). Raising that needs `sudo sysctl iogpu.wired_limit_mb`, which this setup avoids.

That rules out the model the [original guide](compatibility.md#differences-from-the-original-guide) assumed. [`mlx-community/Qwen3.6-35B-A3B-4bit`](https://huggingface.co/mlx-community/Qwen3.6-35B-A3B-4bit/tree/38740b847e4cb78f352aba30aa41c76e08e6eb46) weighs 19.0 GiB and the [mxfp4 build](https://huggingface.co/mlx-community/Qwen3.6-35B-A3B-mxfp4/tree/833013b27a1f7c6dbb008b55d37c387ea22ea89d) 18.0 GiB, both over the limit before any context. The default here is [`mlx-community/KAT-Coder-V2.5-Dev-OptiQ-4bit-REAP-18B`](https://huggingface.co/mlx-community/KAT-Coder-V2.5-Dev-OptiQ-4bit-REAP-18B/tree/b33dfde62c2753841aa2b1662529bf7f3b1fdde5), pinned to commit `b33dfde6`:

- It is [Kwaipilot's KAT-Coder-V2.5-Dev](https://huggingface.co/Kwaipilot/KAT-Coder-V2.5-Dev/blob/7be56fe773e72b6f5ca93c1ae45d828ddb893922/README.md), Qwen3.6-35B-A3B further trained for coding agents, scoring 69.4 on SWE-bench Verified against 64.4 for Qwen3.6-35B-A3B in Kwaipilot's runs. Both cards declare Apache-2.0.
- Half of the 256 experts in each layer are pruned, leaving 128; each token still uses 8. mlx-lm loads 11.4 GiB, which is also the whole download. Its model card says this pruned build was not benchmarked on its own; the same recipe applied to Qwen3.6-35B-A3B cost 21.4 points of MMLU and left coding and tool use about level (HumanEval -1.3, BFCL -1.0).
- On this repo's own benchmark, at its default temperature of 0.8, it passed 12 of 12 runs in 57% of the time the pruned Qwen3.6 model took; see [REAP against KAT-Coder](#reap-against-kat-coder).
- Only 10 of its 40 layers use full attention, with 2 key-value heads of 256 dimensions, so the context cache costs about 20 KiB per token at 16-bit. That is about 0.6 GiB at 32K tokens, 2.5 GiB at 128K and 5 GiB at the full 256K, roughly half with 8-bit quantization.

## Memory and disk guards

`QWEN_CONTEXT` defaults to the context window each model card states: 262,144 tokens for KAT-Coder ([card](https://huggingface.co/Kwaipilot/KAT-Coder-V2.5-Dev/blob/7be56fe773e72b6f5ca93c1ae45d828ddb893922/README.md?code=true#L218)), Qwen3.6-35B-A3B ([card](https://huggingface.co/Qwen/Qwen3.6-35B-A3B/blob/995ad96eacd98c81ed38be0c5b274b04031597b0/README.md?code=true#L55)) and Qwen3.5-4B ([card](https://huggingface.co/Qwen/Qwen3.5-4B/blob/851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a/README.md?code=true#L64)). `./bin/serve-qwen` runs mlx-lm through [`lib/serve.py`](../lib/serve.py), which keeps the server from running the Mac out of memory or disk:

- **At start-up** it refuses to load the model when not even a 32,768-token conversation fits in the 16 GiB the GPU may use or in the memory free right now, or when less than 5 GiB of disk is free. When the full window does not fit within the GPU limit, it starts anyway and records the part that fits as the window the agents are told; `./bin/integrate` writes the same number into the agents' configs.
- **For each request** it rejects prompts that, with the reply, exceed the context window, and requests that would not fit in the GPU limit or in the memory free at that moment while leaving 1 GiB for macOS. The agent shows the error, which says how much was needed and what to do. mlx-lm reports these errors with HTTP status 404.
- **For each request** it also picks the prefill step: the largest up to `QWEN_PREFILL_STEP` that fits, down to 16 tokens. Short prompts get 512 tokens per step; only long ones go smaller.
- **For each request** it refuses any model, adapter or draft model other than the one it serves. mlx-lm would otherwise unload the served model and load, or download, the one named, outside every check.
- **Codex's requests** go through the same checks: the server translates them into Chat Completions requests to itself ([how-it-works.md](how-it-works.md#how-codex-is-served)).
- **For each request** it logs the memory the request reached at its peak next to the estimate it was admitted on, and logs a warning when the peak went over. Refused requests are logged with their reason too; mlx-lm itself tells only the agent. `./bin/verify` sums these lines up in its report.
- **While running**, a watchdog stops the server with a message when free memory falls to 0.5 GiB, for example because another application took it, or free disk to 1 GiB, before macOS starts to freeze. If the watchdog itself fails, it stops the server too.
- **Downloads** check that the model's remaining size plus 5 GiB is free first, and the mlx-lm and agent installs want 6 GiB free. uv's and Hugging Face's caches live in `state/`, so these checks see them.

"Free memory" is what macOS can hand out without swapping, read from `vm_stat`: free pages, file-backed pages (cached files, which macOS drops without writing them anywhere) and purgeable pages count in full. Inactive app memory has to go through the memory compressor first, so it counts only for the room compressing it frees, at the compression ratio `vm_stat` reports at that moment.

### What a conversation needs

A request of N tokens needs:

- the context cache for N tokens;
- the attention scores of one prefill step;
- one layer's cache again, because the cache grows by copying it, or the 16-bit and 8-bit copies that briefly coexist when the cache is first quantized at 5,000 tokens, whichever is larger;
- a fixed overhead.

mlx-lm computes attention with an 8-bit cache without the fused kernel, so it holds the full score matrix of one step, 2 bytes for each of the 16 query heads, step token and context token ([`base.py`](https://github.com/ml-explore/mlx-lm/blob/93e6d4a6447b3080861985947f0820e76fcb957d/mlx_lm/models/base.py)). At 262,144 tokens and this setup's default 512-token step, that alone is 4 GiB per copy, which is why the step shrinks for long prompts.

The constants in [`lib/memory.py`](../lib/memory.py) come from measuring prefill peaks on 2026-10-22: with Qwen3.5-4B at 16K to 64K tokens and steps of 32 to 512, the peak above the weights was the context cache, 1.9 to 2.1 copies of the scores, and at most 0.71 GiB more; KAT-Coder at 8K tokens needed 0.26 GiB more. The estimate rounds these up to 2.25 copies and 0.75 GiB. In an end-to-end check with Qwen3.5-4B and a 40,000-token prompt, the server's peak physical footprint (`vmmap --summary`) was 5.2 GiB, below the estimate. Steps of 32 tokens took 14% longer than 512 at 32K tokens. Beyond 64K tokens, the per-request peaks `./bin/verify` logs were measured on 2026-11-02 with Qwen3.5-4B: 2.61 GiB above the weights against an estimate of 3.62 GiB for a 72,376-token request at a step of 256, and 3.74 GiB against 4.72 GiB for a 136,576-token request at a step of 128. With 5 GiB free, the guard chose that step of 128, and reading the 128K prompt took about 7 minutes. Larger requests have not been measured; the estimate there extrapolates the same terms.

What the server needs on a 24 GiB Mac, with an 8-bit context cache and no cached prompts:

| Model | Memory for a 32,768-token conversation | For 131,072 tokens | For 262,144 tokens | Longest request within the 16 GiB GPU limit | Free memory needed to start | Free memory needed for the longest request |
|---|---|---|---|---|---|---|
| KAT-Coder (`kat`, default) | 13.43 GiB | 14.58 GiB | 16.18 GiB | 247,319 tokens | 14.43 GiB | 17.00 GiB |
| Pruned Qwen3.6 (`reap`) | 13.50 GiB | 14.65 GiB | 16.25 GiB | 241,884 tokens | 14.50 GiB | 17.00 GiB |
| Qwen3.5-4B (`small`) | 5.09 GiB | 6.91 GiB | 9.44 GiB | 262,144 tokens | 6.09 GiB | 10.44 GiB |

KAT-Coder starts once 14.43 GiB is free, enough for 32,768 tokens; longer conversations then work as far as free memory allows. Its full 262,144-token window needs 16.18 GiB, just over what the GPU may use, so the server tells the agents its window is 247,319 tokens, and they compact their conversations before reaching it. A request longer than that is refused with an error rather than risk running out of memory. The GPU limit is the one this Mac reports, 16 GiB plus 16 KiB. On a 24 GiB Mac, freeing 14 to 17 GiB usually means closing browsers and chat apps first.

## Sampling

The server samples with `top_p=0.95` and `min_p=0.0`, and with each preset's temperature and `top_k`, as its model card recommends; `lib/mlx.sh` quotes each source. mlx-lm's own default is greedy decoding.

- **Qwen3.6** (`reap`) and the small Qwen3.5: temperature 0.6 and `top_k=20`, the setting the [Qwen3.6 model card](https://huggingface.co/Qwen/Qwen3.6-35B-A3B/blob/995ad96eacd98c81ed38be0c5b274b04031597b0/README.md) recommends for precise coding in thinking mode.
- **KAT-Coder** (`kat`, `kat-full`): 0.8 and `top_k=20`, between Qwen's 0.6 and the 1.0 in KAT-Coder's own `generation_config.json`. The pruned `kat` passed 12 of 12 benchmark runs at that setting.
- **Qwen3.8-27B** (`qwen38-4`, `qwen38`): 1.0 and `top_k=20`, its card's setting for thinking mode ([card](https://huggingface.co/Qwen/Qwen3.8-27B/blob/1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0/README.md?code=true#L252)), in which it runs by default.
- **Qwen3-Coder-Next** (`coder-next`, `coder-next-8`): 1.0 and `top_k=40`, as [its card](https://huggingface.co/Qwen/Qwen3-Coder-Next/blob/a7fbcb5c0e12d62a448eaa0e260346bf5dcc0feb/README.md?code=true#L194) recommends. It has no thinking mode.
- **Muse Glimmer** (`glimmer-4`, `glimmer`): 1.0 and `top_k=64`, as [its card](https://huggingface.co/meta-models/Muse-Glimmer-30B/blob/cec94fcfd6512f2cfe708138c32533b346ea4f69/README.md?code=true#L125-L129) recommends. Its chat template asks for `Reasoning strength: high` unless the system prompt names another, the level the card advises for coding and agentic tasks.

## Presets for larger Macs

A Mac with more memory lets the GPU use more, and can run a larger model with the same guards. Seven presets are for that; choose one with `QWEN_PRESET` and download it with `./bin/setup` followed by its name. The first five are in the same families as the default, with the same hybrid of full and linear attention that the memory estimate was measured on; Muse Glimmer is described [below](#muse-glimmer). They were picked on 2026-11-01 from the MLX builds `./bin/models info` reports as runnable by mlx-lm 0.32.2 with a tool-call parser, and they have not been run here: this Mac's GPU may use only 16 GiB. [Checking a preset on another Mac](#checking-a-preset-on-another-mac) says how to test one.

| Preset | Model and commit | Weights | GPU limit for 32,768 tokens | For 131,072 | For the full 262,144 | Free memory needed to start |
|---|---|---|---|---|---|---|
| `kat-full` | [`mlx-community/KAT-Coder-V2.5-Dev-OptiQ-4bit`](https://huggingface.co/mlx-community/KAT-Coder-V2.5-Dev-OptiQ-4bit/tree/57c6e8372737f8a04aca1c210c926b8aa29c802e) `57c6e837` | 20.42 GiB | 22.45 GiB | 23.59 GiB | 25.19 GiB | 23.45 GiB |
| `qwen38-4` | [`mlx-community/Qwen3.8-27B-4bit`](https://huggingface.co/mlx-community/Qwen3.8-27B-4bit/tree/10c35caafbb80f7dc6a7a432cdd11af10a6d4818) `10c35caa` | 14.95 GiB | 18.01 GiB | 21.36 GiB | 26.05 GiB | 19.01 GiB |
| `qwen38` | [`mlx-community/Qwen3.8-27B-8bit`](https://huggingface.co/mlx-community/Qwen3.8-27B-8bit/tree/815b83c0df8ffd1d1b5244cf75fd6ef14fca9ef9) `815b83c0` | 27.48 GiB | 30.54 GiB | 33.88 GiB | 38.58 GiB | 31.54 GiB |
| `coder-next` | [`mlx-community/Qwen3-Coder-Next-4bit`](https://huggingface.co/mlx-community/Qwen3-Coder-Next-4bit/tree/7b9321eabb85ce79625cac3f61ea691e4ea984b5) `7b9321ea` | 41.76 GiB | 43.88 GiB | 45.21 GiB | 47.08 GiB | 44.88 GiB |
| `coder-next-8` | [`NexVeridian/Qwen3-Coder-Next-8bit`](https://huggingface.co/NexVeridian/Qwen3-Coder-Next-8bit/tree/fbc85334e685d0dd84fa221cc282ebcb6360a478) `fbc85334` | 78.84 GiB | 80.96 GiB | 82.29 GiB | 84.15 GiB | 81.96 GiB |
| `glimmer-4` | [`mlx-community/Muse-Glimmer-30B-4bit`](https://huggingface.co/mlx-community/Muse-Glimmer-30B-4bit/tree/3e7677d7a40d348a3daba263a2b1c0aa41910710) `3e7677d7` | 14.59 GiB | 16.52 GiB | 17.38 GiB, its full window | — | 17.52 GiB |
| `glimmer` | [`mlx-community/Muse-Glimmer-30B-8bit`](https://huggingface.co/mlx-community/Muse-Glimmer-30B-8bit/tree/679c45e1b331a6514a6e38076a353cc5fed21bf6) `679c45e1` | 27.56 GiB | 29.49 GiB | 30.35 GiB, its full window | — | 30.49 GiB |

The figures come from `lib/memory.py` with the default settings: an 8-bit context cache, no cached prompts and a 512 MiB margin. Glimmer's files also hold its image encoder, 3.49 GiB at 4 bits and 3.52 GiB at 8, which mlx-lm's text-only Glimmer drops before loading ([`sanitize`](https://github.com/ml-explore/mlx-lm/blob/93e6d4a6447b3080861985947f0820e76fcb957d/mlx_lm/models/muse_glimmer.py#L245-L250)). So `lib/memory.py` leaves those tensors out, sized from each file's safetensors header, and `./bin/models info` reads the same headers with range requests before downloading; the weights column is what is loaded, against 18.08 and 31.08 GiB of files. Measured from the headers on 2026-11-02. A preset whose full window does not fit still starts, tells the agents the window that does, and refuses longer requests, as `kat` does here.

What the GPU may use depends on the Mac. macOS reports it as `max_recommended_working_set_size`, which `./bin/models info` and `./bin/serve-qwen` read; this 24 GiB Mac reports 16 GiB. Apple does not document the default for other sizes. Community measurements put it at about three quarters of the memory on larger Macs, for example about 48 GiB on a 64 GiB Mac ([contracollective.com](https://contracollective.com/blog/mac-unified-memory-wired-limit-gpu-large-local-llm-2026), [ivanopcode/devnote-override-macos-metal-vram-cap](https://github.com/ivanopcode/devnote-override-macos-metal-vram-cap)). Run `./bin/models info` with a preset's model on the Mac in question to see what fits there.

What each model is:

- **`kat-full`**: the default's model without the pruning, so all 256 experts per layer instead of 128. Kwaipilot reports 69.4 on SWE-bench Verified for it ([card](https://huggingface.co/Kwaipilot/KAT-Coder-V2.5-Dev/blob/7be56fe773e72b6f5ca93c1ae45d828ddb893922/README.md)). Apache-2.0.
- **`qwen38-4`** and **`qwen38`**: Qwen3.8-27B at 4 and 8 bits, the newest dense Qwen of this size (August 2026). On Qwen's own table it leads Qwen3.6-27B on every coding row: Terminal-Bench 2.1 73.0 against 63.4, SWE-bench Pro 61.7 against 53.5, QwenSWEBench 79.0 against 49.3 ([card](https://huggingface.co/Qwen/Qwen3.8-27B/blob/1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0/README.md)). Dense means every token uses all 27 billion parameters, so it answers much more slowly than the 3-billion-active models. The 4-bit build is the smallest of the larger presets, for Macs around 32 GiB; at 4 bits its quality was not measured. Apache-2.0.
- **`coder-next`** and **`coder-next-8`**: Qwen3-Coder-Next, 80 billion parameters with 3 billion active per token, made for coding agents ([card](https://huggingface.co/Qwen/Qwen3-Coder-Next/blob/a7fbcb5c0e12d62a448eaa0e260346bf5dcc0feb/README.md)). Its technical report gives 70.6, 71.1 and 71.3 on SWE-bench Verified with the SWE-Agent, MiniSWE-Agent and OpenHands scaffolds ([Table 3](https://arxiv.org/abs/2603.00729)); those scaffolds differ from the one behind Qwen3.6's figures, so the numbers do not compare directly. The 8-bit build is NexVeridian's: mlx-lm picks the `qwen3_coder` tool-call parser for it, but `json_tools` for the lmstudio-community 8-bit build, which does not match the format the model writes. Apache-2.0.

- **`glimmer-4`** and **`glimmer`**: Meta's Muse Glimmer at 4 and 8 bits, a dense 30-billion-parameter model from August 2026 made for agents running on one machine ([card](https://huggingface.co/meta-models/Muse-Glimmer-30B/blob/cec94fcfd6512f2cfe708138c32533b346ea4f69/README.md), [Meta's page](https://dev.meta.ai/lp/muse-glimmer)). On Meta's table it trails Qwen3.6-27B on coding, 76.0 against 77.2 on SWE-bench Verified and 51.7 against 60.7 on Terminal-Bench 2.1, and leads it on SWE-bench Pro, 51.2 against 50.2, and on tool use, 75.5 against 62.5 on MCP Atlas. Qwen3.8-27B's card gives 73.0 on Terminal-Bench 2.1 and 61.7 on SWE-bench Pro, but with its own harnesses: Meta's table puts Qwen3.6-27B at 60.7 on Terminal-Bench 2.1 where Qwen's puts it at 63.4, so the two cards do not compare directly. Its window is 131,072 tokens. Apache-2.0, and Meta's [usage policy](https://huggingface.co/meta-models/Muse-Glimmer-30B/blob/cec94fcfd6512f2cfe708138c32533b346ea4f69/USAGE_POLICY.md) applies too: it lists prohibited uses and says the model is not meant for anyone under 18. The MLX builds leave that file out.

Not picked: Qwen3.6-27B, which Qwen3.8-27B replaces at the same size. Qwen3.6-35B-A3B at 8 bits (35.13 GiB), the unpruned model that `reap` is pruned from, because Qwen3.8-27B scores higher on Qwen's tables and downloads less. Qwen3.8-Flash-Next (103.86 GiB at 4 bits), whose architecture, `qwen4_exp`, is not in mlx-lm 0.32.2 and whose card states an `other` license; Qwen3.8-2.4T-A95B, far beyond any Mac. gpt-oss-120b has no tool-call parser in mlx-lm 0.32.2. MiniMax-M2.5 at 4 bits loads 119.83 GiB, more than a 128 GiB Mac's GPU may use. GLM-4.7-Flash uses an attention layout the memory estimate does not model, and GLM-5.3-Flash's architecture is not in mlx-lm 0.32.2. Muse Glimmer at 6 bits (24.58 GiB): the 4- and 8-bit builds bracket it, as for the other families.

### Checking a preset on another Mac

On the Mac in question, with this repo, `./bin/setup mlx opencode pi` done and Codex installed if it is to be tested:

```sh
./bin/models info mlx-community/Qwen3.8-27B-4bit@10c35caafbb80f7dc6a7a432cdd11af10a6d4818   # what fits there
./bin/verify qwen38-4
```

`./bin/verify` downloads the preset if needed, starts its own server (stop any other one first), runs `./bin/smoke-test`, `./bin/evals` and `./bin/bench-agents` with OpenCode, Pi and, when installed, Codex, then stops the server. A failed step does not stop the next one. With every step at its defaults it takes hours; `VERIFY_STEPS="smoke evals"` leaves out the benchmark, and the `EVAL_*` and `BENCH_*` settings apply as usual. Its report, `state/verify/PRESET-TIME/report.md`, gives:

- the chip, the memory, the GPU limit macOS reports and the macOS version, but not the Mac's name or serial number;
- the model and commit, the settings, the mlx-lm environment and the agents' versions;
- each step's result and time, the evals and benchmark results;
- the server's memory check, and how each request's peak compared with the estimate the memory guard admitted it on. A peak over its estimate means the guard was too optimistic on that Mac, and is worth reporting with `serve.log`, which sits next to the report.

The evals and benchmark results are also kept under the label `PRESET-TIME`, so `./bin/evals report` and `./bin/bench-agents report` show them next to the ones from this Mac. The same works for `kat` with Codex, which was not benchmarked here for lack of free memory.

### Muse Glimmer

Glimmer is not a Qwen model, so the server does two things for it that mlx-lm 0.32.2 does not:

- **Its sliding-window layers are not quantized.** 39 of its 52 layers attend only to the last 2,048 tokens and keep them in a `RotatingKVCache`, which mlx-lm cannot quantize: past 5,000 tokens, with the 8-bit context cache this setup uses, every request would fail with `NotImplementedError` ([ml-explore/mlx-lm#1573](https://github.com/ml-explore/mlx-lm/issues/1573)). `lib/serve.py` leaves those layers at 16 bits and quantizes the other 13, and `lib/memory.py` counts them that way: 2,048 tokens per layer, plus one prefill step while a prompt is processed.
- **Its replies are read as Muse Spark's.** Glimmer writes each reply as messages with headers: reasoning after `to=self<|message|>`, the answer after `to=user<|message|>`, tool calls in `<atem:function_calls>` ([`response_template` in its `tokenizer_config.json`](https://huggingface.co/mlx-community/Muse-Glimmer-30B-4bit/blob/3e7677d7a40d348a3daba263a2b1c0aa41910710/tokenizer_config.json)). mlx-lm reads that format for Muse Spark but not for Glimmer, so the reasoning and the headers would reach the agents as reply text. `lib/serve.py` gives Glimmer Spark's format; the reasoning then arrives as reasoning and tool calls as tool calls.

The sliding-window estimate was checked on a cut-down Glimmer, with its real layer sizes but 8 or 16 of its 52 layers and random 4-bit weights, at 4,000 to 131,072 tokens. The measured peak stayed under the estimate in every run. The reply format was checked with Glimmer's own tokenizer and chat template, by streaming a reply in its format through mlx-lm's detokenizer and parser. The full model was not run: its 14.6 GiB of language-model weights leave too little of this Mac's 16 GiB for even 32,768 tokens.

The MLX builds' chat template predates [a fix Meta made on 2026-08-11](https://huggingface.co/meta-models/Muse-Glimmer-30B/commit/a4e59da52a): when a system prompt already names a reasoning strength, the older template adds a second one. The agents' prompts name none.

## Other models that fit

A survey on 2026-10-20 of MLX models whose loaded weights stay under 11.5 GiB, that mlx-lm 0.32.2 can run, and whose tool calls it can parse. The pruned Qwen3.6 model was the previous default and is benchmarked below; the other two have not been tried. Download any of them with `./bin/models download`, and serve it with `QWEN_REPO`, `QWEN_REVISION` and `QWEN_CONTEXT`. Benchmark numbers are for the full-precision parent models, not these quantized builds. For servers that run models faster than plain mlx-lm, see [alternatives.md](alternatives.md).

| Model and commit | Loaded weights | Context cache per token | Parent's coding-agent scores | Caveat |
|---|---|---|---|---|
| [`mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit-REAP-19B`](https://huggingface.co/mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit-REAP-19B/tree/95193a560399d81cd6272c393db8e96d59f54f50) `95193a56`, preset `reap` | 11.48 GiB (14 GiB download, with OptiQ add-ons in `optiq/` that [mlx-lm never reads](https://github.com/ml-explore/mlx-lm/blob/93e6d4a6447b3080861985947f0820e76fcb957d/mlx_lm/utils.py#L449)) | 20 KiB | SWE-bench Verified 73.4, Terminal-Bench 2.0 51.5 ([Qwen card](https://huggingface.co/Qwen/Qwen3.6-35B-A3B/blob/995ad96eacd98c81ed38be0c5b274b04031597b0/README.md)) | Same base and pruning as the default without the coding-agent training; its card reports MMLU falling from 83.7 to 62.3 |
| [`NexVeridian/Qwen3.6-27B-3bit`](https://huggingface.co/NexVeridian/Qwen3.6-27B-3bit/tree/01947930271046ebd8ef96663618cf7407158874) `01947930` | 10.96 GiB | 64 KiB | SWE-bench Verified 77.2, Terminal-Bench 2.0 59.3 ([Qwen card](https://huggingface.co/Qwen/Qwen3.6-27B/blob/6a9e13bd6fc8f0983b9b99948120bc37f49c13e9/README.md)) | Strongest parent; 3-bit quality is unmeasured, and a dense model decodes much slower |
| [`mlx-community/Qwen3.5-9B-6bit`](https://huggingface.co/mlx-community/Qwen3.5-9B-6bit/tree/76fe4065e622cf34990d3c13ef80ec8531c9a0f7) `76fe4065` | 7.63 GiB | 32 KiB | BFCL-V4 66.1, TAU2-Bench 79.1 ([Qwen card](https://huggingface.co/Qwen/Qwen3.5-9B/blob/c202236235762e1c871ad0ccb60c8ee5ba337b9a/README.md)) | Weakest of the three, but leaves the most room for long context |

Qwen's 73.4 and Kwaipilot's 64.4 for the same Qwen3.6-35B-A3B come from different harnesses; Kwaipilot's card attributes the gap to harness versions.

## Comparing models

`./bin/bench-agents LABEL` gives each agent seven Python projects from `bench/`, each with a request in `prompt.txt`. Three are small:

- `inventory`: fix four bugs that the failing tests point at, without touching the tests.
- `todo`: add a command and a flag to a command-line tool, and update a test whose expected output changes.
- `billing`: rename a function across several files and change its rate argument from a fraction to a percentage, keeping every result the same.

Four are harder, each with several things to get right at once:

- `jobqueue`: a pool of worker threads with four separate bugs behind three user reports: a shutdown that hangs, a failing job that stops later ones, retries off by one, and results in the wrong order. The hidden tests time out instead of hanging.
- `logstats`: a log report that is correct but quadratic. It must give the same output, character for character, on 200,000 lines in under two seconds. The hidden tests compare it with a frozen copy of the original on hundreds of random logs, and the `check` script refuses an answer read from a file.
- `notes`: move a notes file from format version 1 to version 2, with stable ids, while still reading version 1, and add a `migrate` command that keeps a backup, has a dry run, does nothing twice, and changes nothing when one note is bad.
- `templ`: build a template language from its `SPEC.md`: values, filters, conditions, loops, comments, whitespace control, HTML escaping, and errors that name their line.

Each run works on a fresh copy. When the agent finishes, tests it never saw (`bench/TASK/hidden`) are copied in, and the whole suite plus the task's `check` script decide pass or fail. Results go to `state/bench/LABEL/`, with each run's log and final files. `./bin/bench-agents report` summarizes every label side by side.

Serve one model, run `BENCH_RUNS=2 ./bin/bench-agents first-model`, stop the server, serve the next model and repeat with another label. Sampling is random, so one run per task says little. These settings shape a run:

- `BENCH_RUNS`: runs of each task with each agent, 1 by default; use two or more.
- `BENCH_AGENTS`: the agents to run, `opencode pi` by default; `codex` works too.
- `BENCH_TASKS`: the tasks to run, every folder in `bench/` by default.
- `BENCH_TIMEOUT`: seconds per run, 900 by default.

## Evaluating the model directly

`./bin/evals LABEL` tests the served model through the server's own API, with no agent in between, so a result says how the model does rather than how a model and an agent do together. It has four suites; run some with `./bin/evals LABEL coding speed`, and list the cases with `./bin/evals list`:

- `tool-calls`: ten scripted conversations in `evals/tool-calls/`. The model must pick the right tool among up to twelve similar ones, give arguments that match the schema and the expected values (types, enums, nested objects, ISO dates, optional arguments left out), wait for one result before a call that needs it, recover from a tool error, find one line in a 182-line result, and answer from what the tools returned. A call where none was expected fails the case.
- `coding`: ten problems in `evals/coding/`, written for this project so they cannot be in any model's training data: an exact calculator with error positions, glob matching without exponential time, a cron schedule's next time, JSON Patch, weighted interval coverage, deterministic topological order with cycle reporting, an LRU cache with expiry, nested run-length expansion, shortest paths through a maze with portals, and splitting money by weights to the cent. The model writes a module from `prompt.md`; hidden tests check edge cases, compare it with a brute-force version on random inputs, and give it a time budget.
- `long-context`: an inventory of hosts filling 4,096 tokens and up, as far as the window allows, with the one record asked about at 10%, 50% and 90% of the way through, among decoys that differ in one detail. Prompt sizes are measured with the server's own token counts.
- `speed`: prefill and decode speed, and the time to the first token, at 1,024, 8,192 and 32,768 tokens of prompt, from streamed replies. Each prompt starts with something new, so no cached prompt shortens it.

The coding suite runs the code the model writes, as you, in a fresh temporary folder with a 60-second limit, as a coding agent would. Results go to `state/evals/LABEL/results.jsonl`, with every request and reply in `transcripts/`; `./bin/evals report` compares the labels. A request the server refuses, for example because memory is short, counts as an error, not a failure. These settings shape a run:

- `EVAL_CASES`: the cases to run, separated by spaces, all by default.
- `EVAL_SAMPLES`: how many times each case is asked, 1 by default. Sampling is random, so use three or more to compare models.
- `EVAL_TIMEOUT`: seconds one request may take, 900 by default.
- `EVAL_MAX_OUTPUT`: tokens one reply may use, `QWEN_MAX_OUTPUT` by default. Thinking models need room to think before they answer.
- `EVAL_LONG_CONTEXT`: the largest long-context prompt, in tokens, the window by default. Long prompts take minutes on a small Mac.
- `EVAL_THINKING`: `on` by default, the model's own behaviour. `off` sends `enable_thinking: false` to the chat template, which Qwen's templates follow; a small model that thinks in circles then answers at once, which shows what it can do without the thinking.

### A first baseline

Measured on 2026-11-01 on this Mac with the `small` preset (Qwen3.5-4B at 4 bits), one sample each:

- `./bin/evals`: tool calls 8 of 10 at first (one failure was a flaw in the eval since fixed, the other an argument nobody asked for, which a second try got right); long context 12 of 12 up to 65,536 tokens, the limit set for the run; coding 0 of 10. In 6 of the coding problems it spent its 8,192 output tokens thinking and never answered, and 32,768 tokens did not help `calc`. With `EVAL_THINKING=off` it answered at once and passed 1 of 10.
- Speed: prefill 672, 658 and 545 tokens a second and decode 70, 60 and 43 at 1,024, 8,192 and 32,768 tokens of prompt; the first token came after 1.4, 12.1 and 58.9 seconds.
- `./bin/bench-agents` with Pi on the four harder tasks: 0 of 4. It ran out of output tokens on `jobqueue` and `notes`, broke the report's output on `logstats`, and timed out on `templ`.

A 4-billion-parameter model is for checking that the setup works, not for coding; the harder tasks and the coding problems are there to tell larger models apart.

## REAP against KAT-Coder

Results of `BENCH_RUNS=2 ./bin/bench-agents` on 2026-10-20 and 2026-10-21, 32K context. Each cell shows runs passed and the median time.

| Task | Agent | REAP-19B at 0.6 | KAT-Coder at 0.6 | KAT-Coder at 0.8 (default) |
|---|---|---|---|---|
| billing | OpenCode | 2/2, 274 s | 2/2, 127 s | 2/2, 134 s |
| billing | Pi | 2/2, 80 s | 1/2, 67 s | 2/2, 82 s |
| inventory | OpenCode | 2/2, 418 s | 2/2, 181 s | 2/2, 122 s |
| inventory | Pi | 2/2, 52 s | 2/2, 34 s | 2/2, 49 s |
| todo | OpenCode | 2/2, 274 s | 2/2, 145 s | 2/2, 185 s |
| todo | Pi | 2/2, 112 s | 2/2, 98 s | 2/2, 116 s |
| **All** | | **12/12, 2419 s, 101 requests** | **11/12, 1305 s, 70 requests** | **12/12, 1375 s, 84 requests** |

KAT-Coder needs fewer model requests than REAP for the same work, so it settles on an answer in fewer turns. Its one failure at 0.6 made `compute_tax` work for `Decimal("20")` but crash on a plain `20`, because `20 / 100` is a float. Twelve runs per setting are too few to separate the three on correctness; KAT-Coder is clearly faster than REAP, and 0.6 against 0.8 is within noise.

The two runs at 0.6 used mlx-lm's 2048-token prefill step and up to 10 cached prompts; the 0.8 run used 512-token steps with the prompt cache off. Neither changes the work the model does: the cache never hits in agent loops (see below), and the prefill step changed memory but not speed.

## Agents get no help from the prompt cache

mlx-lm 0.32.2 can reuse a cached prompt only if it is an exact prefix of the new one, unless the cache can be trimmed back. Qwen3.6 and KAT-Coder mix attention with linear-attention layers, whose state cannot be trimmed ([`ArraysCache` inherits `is_trimmable() -> False`](https://github.com/ml-explore/mlx-lm/blob/93e6d4a6447b3080861985947f0820e76fcb957d/mlx_lm/models/cache.py#L164-L165)). The server splits a prompt into reusable system and user parts only when the last message is from the user ([`server.py`](https://github.com/ml-explore/mlx-lm/blob/93e6d4a6447b3080861985947f0820e76fcb957d/mlx_lm/server.py#L703)); in an agent loop it is a tool result. So each turn stores the prompt plus the reply as generated, the next request renders that reply differently, and the server processes the whole conversation again. In the REAP benchmark that was 740,000 prompt tokens over 101 requests.

mlx-lm 0.32.2 applies `--prompt-cache-bytes` only when it adds a request to a batch ([`server.py`](https://github.com/ml-explore/mlx-lm/blob/93e6d4a6447b3080861985947f0820e76fcb957d/mlx_lm/server.py#L886-L889)). With a quantized context cache it serves one request at a time and never applies that limit ([L768-L773](https://github.com/ml-explore/mlx-lm/blob/93e6d4a6447b3080861985947f0820e76fcb957d/mlx_lm/server.py#L768-L773)), so up to `--prompt-cache-size` entries (10 by default) accumulate ([ml-explore/mlx-lm#1807](https://github.com/ml-explore/mlx-lm/issues/1807) reports the same growth). `./bin/serve-qwen` therefore caps the number of entries rather than bytes, and sets it to 0 by default.

