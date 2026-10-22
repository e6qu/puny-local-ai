# Models, memory and benchmarks

Why the default model is KAT-Coder, how `./bin/serve-qwen` decides whether a model fits, and how the candidates compared. Settings named here are described in the [README](../README.md#settings).

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

The constants in [`lib/memory.py`](../lib/memory.py) come from measuring prefill peaks on 2026-10-22: with Qwen3.5-4B at 16K to 64K tokens and steps of 32 to 512, the peak above the weights was the context cache, 1.9 to 2.1 copies of the scores, and at most 0.71 GiB more; KAT-Coder at 8K tokens needed 0.26 GiB more. The estimate rounds these up to 2.25 copies and 0.75 GiB. In an end-to-end check with Qwen3.5-4B and a 40,000-token prompt, the server's peak physical footprint (`vmmap --summary`) was 5.2 GiB, below the estimate. Steps of 32 tokens took 14% longer than 512 at 32K tokens. Peaks beyond 64K tokens have not been measured; the estimate there extrapolates the same terms.

What the server needs on a 24 GiB Mac, with an 8-bit context cache and no cached prompts:

| Model | Memory for a 32,768-token conversation | For 131,072 tokens | For 262,144 tokens | Longest request within the 16 GiB GPU limit | Free memory needed to start | Free memory needed for the longest request |
|---|---|---|---|---|---|---|
| KAT-Coder (`kat`, default) | 13.43 GiB | 14.58 GiB | 16.18 GiB | 247,319 tokens | 14.43 GiB | 17.00 GiB |
| Pruned Qwen3.6 (`reap`) | 13.50 GiB | 14.65 GiB | 16.25 GiB | 241,884 tokens | 14.50 GiB | 17.00 GiB |
| Qwen3.5-4B (`small`) | 5.09 GiB | 6.91 GiB | 9.44 GiB | 262,144 tokens | 6.09 GiB | 10.44 GiB |

KAT-Coder starts once 14.43 GiB is free, enough for 32,768 tokens; longer conversations then work as far as free memory allows. Its full 262,144-token window needs 16.18 GiB, just over what the GPU may use, so the server tells the agents its window is 247,319 tokens, and they compact their conversations before reaching it. A request longer than that is refused with an error rather than risk running out of memory. The GPU limit is the one this Mac reports, 16 GiB plus 16 KiB. On a 24 GiB Mac, freeing 14 to 17 GiB usually means closing browsers and chat apps first.

## Sampling

The server samples with `top_p=0.95, top_k=20, min_p=0.0` and a temperature of 0.6, the setting the [Qwen3.6 model card](https://huggingface.co/Qwen/Qwen3.6-35B-A3B/blob/995ad96eacd98c81ed38be0c5b274b04031597b0/README.md) recommends for precise coding in thinking mode. mlx-lm's own default is greedy decoding. KAT-Coder is the exception: it runs at 0.8, between Qwen's 0.6 and the 1.0 in KAT-Coder's own `generation_config.json`, and passed 12 of 12 benchmark runs at that setting.

## Other models that fit

A survey on 2026-10-20 of MLX models whose loaded weights stay under 11.5 GiB, that mlx-lm 0.32.2 can run, and whose tool calls it can parse. The pruned Qwen3.6 model was the previous default and is benchmarked below; the other two have not been tried. Serve any of them with `QWEN_REPO` and `QWEN_REVISION`. Benchmark numbers are for the full-precision parent models, not these quantized builds.

| Model and commit | Loaded weights | Context cache per token | Parent's coding-agent scores | Caveat |
|---|---|---|---|---|
| [`mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit-REAP-19B`](https://huggingface.co/mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit-REAP-19B/tree/95193a560399d81cd6272c393db8e96d59f54f50) `95193a56`, preset `reap` | 11.48 GiB (14 GiB download, with OptiQ add-ons in `optiq/` that [mlx-lm never reads](https://github.com/ml-explore/mlx-lm/blob/93e6d4a6447b3080861985947f0820e76fcb957d/mlx_lm/utils.py#L449)) | 20 KiB | SWE-bench Verified 73.4, Terminal-Bench 2.0 51.5 ([Qwen card](https://huggingface.co/Qwen/Qwen3.6-35B-A3B/blob/995ad96eacd98c81ed38be0c5b274b04031597b0/README.md)) | Same base and pruning as the default without the coding-agent training; its card reports MMLU falling from 83.7 to 62.3 |
| [`NexVeridian/Qwen3.6-27B-3bit`](https://huggingface.co/NexVeridian/Qwen3.6-27B-3bit/tree/01947930271046ebd8ef96663618cf7407158874) `01947930` | 10.96 GiB | 64 KiB | SWE-bench Verified 77.2, Terminal-Bench 2.0 59.3 ([Qwen card](https://huggingface.co/Qwen/Qwen3.6-27B/blob/6a9e13bd6fc8f0983b9b99948120bc37f49c13e9/README.md)) | Strongest parent; 3-bit quality is unmeasured, and a dense model decodes much slower |
| [`mlx-community/Qwen3.5-9B-6bit`](https://huggingface.co/mlx-community/Qwen3.5-9B-6bit/tree/76fe4065e622cf34990d3c13ef80ec8531c9a0f7) `76fe4065` | 7.63 GiB | 32 KiB | BFCL-V4 66.1, TAU2-Bench 79.1 ([Qwen card](https://huggingface.co/Qwen/Qwen3.5-9B/blob/c202236235762e1c871ad0ccb60c8ee5ba337b9a/README.md)) | Weakest of the three, but leaves the most room for long context |

Qwen's 73.4 and Kwaipilot's 64.4 for the same Qwen3.6-35B-A3B come from different harnesses; Kwaipilot's card attributes the gap to harness versions.

## Comparing models

`./bin/bench-agents LABEL` gives each agent three small Python projects from `bench/`, each with a request in `prompt.txt`:

- `inventory`: fix four bugs that the failing tests point at, without touching the tests.
- `todo`: add a command and a flag to a command-line tool, and update a test whose expected output changes.
- `billing`: rename a function across several files and change its rate argument from a fraction to a percentage, keeping every result the same.

Each run works on a fresh copy. When the agent finishes, tests it never saw (`bench/TASK/hidden`) are copied in, and the whole suite plus the task's `check` script decide pass or fail. Results go to `state/bench/LABEL/`, with each run's log and final files. `./bin/bench-agents report` summarizes every label side by side.

Serve one model, run `BENCH_RUNS=2 ./bin/bench-agents first-model`, stop the server, serve the next model and repeat with another label. Sampling is random, so one run per task says little. These settings shape a run:

- `BENCH_RUNS`: runs of each task with each agent, 1 by default; use two or more.
- `BENCH_AGENTS`: the agents to run, `opencode pi` by default.
- `BENCH_TASKS`: the tasks to run, every folder in `bench/` by default.
- `BENCH_TIMEOUT`: seconds per run, 900 by default.

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

