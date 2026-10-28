# puny-local-ai

Run a local coding model on a 24 GiB Apple Silicon Mac with [mlx-lm](https://github.com/ml-explore/mlx-lm), and use it from the OpenCode, Pi and Codex coding agents, or from a chat page in your browser.

- **Three coding agents work with it: OpenCode, Pi and Codex.** All three talk to the same local server, at `http://127.0.0.1:8081/v1`. One command adds the local model to their configs, and one removes it again. Your usual agent setups keep working beside it.
- **The default model is KAT-Coder-V2.5-Dev.** It is a Qwen3.6-35B-A3B fine-tune for coding agents, pruned to fit in the 16 GiB that macOS lets the GPU use. [docs/models.md](docs/models.md) explains the choice and the benchmarks.
- **Find other MLX models.** `./bin/models`, or the web page from `./bin/web-ui`, searches Hugging Face and ModelScope. It tells you whether a model fits in memory and can make the tool calls agents need, then downloads it pinned to a commit and checked against published hashes.
- **The Mac stays responsive.** The server refuses to start, or refuses a request with an error the agent shows, rather than let macOS swap or freeze. On a 24 GiB Mac, KAT-Coder gets a 247,319-token context window, and the agents are told that number so they compact their conversations in time ([how the guards work](docs/models.md#memory-and-disk-guards)).
- **Every download is pinned and checked.** Python packages and models are checked against recorded hashes, and npm checks the agents against the registry's integrity hash. Nothing third-party is stored in this repo; [THIRD_PARTY.md](THIRD_PARTY.md) lists what is downloaded and its license.

## Bootstrap

These steps take a Mac with nothing installed to a coding agent running on the local model.

1. **Check the hardware.** You need Apple Silicon (`uname -m` prints `arm64`). The memory figures in these docs are for 24 GiB.
2. **Install Apple's command line tools**, which provide `git` and `python3` (`curl`, `perl` and the other tools the scripts use come with macOS):

   ```sh
   xcode-select --install
   ```

3. **Install [Homebrew](https://brew.sh)** with the command shown on its home page, then run the commands its installer prints under "Next steps", so that `brew` is on your PATH.
4. **Install uv and Node.js, and Codex if you want it.** uv downloads Python 3.14.6 for mlx-lm into `state/python`. Pi 1.3.0 needs Node.js 22.19 or newer. This project does not pin Codex; it was tested with Codex 0.165.0.

   ```sh
   brew install uv node
   brew install codex    # optional
   ```

5. **Get this repo:**

   ```sh
   git clone https://github.com/e6qu/puny-local-ai.git
   cd puny-local-ai
   ```

6. **Install the pinned pieces.** This downloads about 11.4 GiB for the model, plus Python, mlx-lm and its dependencies, and installs OpenCode and Pi globally through npm. It needs about 18 GiB of free disk: the downloads plus 5 GiB it keeps free for macOS.

   ```sh
   ./bin/setup mlx kat opencode pi
   ```

   Run `./bin/setup` with no arguments for a menu that shows what is installed. It refuses OpenCode or Pi releases younger than 24 hours; if that happens, it prints when the release becomes installable.
7. **Start the server** in its own terminal and leave it running:

   ```sh
   ./bin/serve-qwen
   ```

   It refuses to start when too little memory is free, and says how much it needs: about 14.4 GiB of free memory for KAT-Coder to start, and 17 GiB for its longest requests. Close large applications such as browsers and chat apps, then try again. [docs/models.md](docs/models.md#memory-and-disk-guards) has the numbers.
8. **Check that everything works**, in a second terminal:

   ```sh
   ./bin/smoke-test
   ```

   It checks that the model returns structured tool calls, and, when Codex is installed, that the server answers Codex's API. Then it has OpenCode, Pi and Codex each create a file in a throwaway folder.
9. **Add the local model to your agents:**

   ```sh
   ./bin/integrate install
   ```

   This adds a provider called `puny` to the configs of OpenCode, Pi and Codex, whichever are installed, and changes nothing else. `./bin/integrate uninstall` removes it again.
10. **Start an agent** in the project you want to work on, with the local model:

    ```sh
    cd ~/code/my-project
    opencode --model puny/qwen-local
    pi --provider puny --model default_model
    codex -p puny
    ```

To try the setup with a smaller 2.8 GiB model first, run `./bin/setup mlx small` and then `QWEN_PRESET=small ./bin/serve-qwen`.

## Using the coding agents

There are two ways in. Both need the server from `./bin/serve-qwen` running while you use the agent, and both leave your own agent setup working as before.

- **Add the model to the agents' own configs** with `./bin/integrate install`, as in the bootstrap. Then start the agents as you normally would and pick the local model:
  - **OpenCode:** `opencode --model puny/qwen-local`, or choose "Qwen (local MLX)" with `/models`. If OpenCode's background service is running, the command tells you to restart it with `opencode service restart`.
  - **Pi:** `pi --provider puny --model default_model`, or choose it with `/model`.
  - **Codex:** `codex -p puny`.

  `./bin/integrate status` shows what is installed. To set up only some agents, name them: `./bin/integrate install pi codex`.
- **Or use the launchers**, which start an agent on the local model for one run without writing to any config: `./bin/opencode-qwen`, `./bin/pi-qwen` and `./bin/codex-qwen`. Run them from any folder; arguments go straight to the agent.

[docs/agents.md](docs/agents.md) explains what each way changes and how it keeps your files safe, and shows the provider entries for setting them up by hand. [docs/compatibility.md](docs/compatibility.md#what-works-with-each-agent) lists what works with each agent.

## The web UI

```sh
./bin/web-ui
```

This serves a page at `http://127.0.0.1:8090` with two tabs:

- **Chat** talks to the model that `./bin/serve-qwen` is running, with its thinking shown separately. The header shows whether the server runs, which model it serves and its context window.
- **Models** searches Hugging Face and ModelScope for MLX models. **Check** shows a model's size, whether mlx-lm can run it and parse its tool calls, and whether it fits in memory. **Download** fetches it the same way `./bin/models download` does. The tab also lists the models already downloaded.

The page listens only on this Mac and refuses requests from other web sites. [docs/how-it-works.md](docs/how-it-works.md#the-web-ui) has the details.

## Finding and serving other models

```sh
./bin/models search "qwen3.6 coder"
./bin/models info mlx-community/Qwen3.5-9B-6bit
./bin/models download mlx-community/Qwen3.5-9B-6bit
./bin/models list
```

`info` reads a model's files and config without downloading the weights. It reports:

- the download size;
- whether mlx-lm 0.32.2 has the architecture;
- which tool-call parser mlx-lm picks for the model, if any (without one, agents cannot edit files);
- the context window in its config;
- the longest conversation that fits next to the weights.

It also prints the commands to download and serve the model. Put `modelscope:` before a name to use the copy on ModelScope, for example `modelscope:mlx-community/Qwen3.5-4B-4bit`.

Downloads are pinned to the commit the branch points to at that moment, and every file is checked against the hashes the source publishes, as for the presets. To serve a downloaded model, give its name, commit and context window:

```sh
QWEN_REPO=mlx-community/Qwen3.5-9B-6bit QWEN_REVISION=<commit from info> QWEN_CONTEXT=262144 ./bin/serve-qwen
```

Take the context window from the model's card. Then run `./bin/integrate install` again with the same settings, so that the agents are told the new window. [docs/models.md](docs/models.md#other-models-that-fit) lists candidates that were checked by hand. [docs/alternatives.md](docs/alternatives.md) surveys other local inference projects for Apple Silicon.

## Scripts

- `./bin/setup [ITEM...]` installs what you name, or what you pick from a menu: `mlx` (the mlx-lm environment), the models `kat`, `reap` and `small`, `opencode` and `pi`.
- `./bin/serve-qwen` downloads and verifies the chosen model if needed, and runs the server in the foreground behind the memory and disk guards. It serves the Chat Completions API that OpenCode and Pi use and the Responses API that Codex uses.
- `./bin/integrate install|uninstall|status [AGENT...]` adds the local model to the agents' own configs, or removes it.
- `./bin/opencode-qwen`, `./bin/pi-qwen` and `./bin/codex-qwen` start each agent on the model for one run.
- `./bin/web-ui` serves the chat and model search page.
- `./bin/models search|info|download|list` finds, checks and downloads MLX models from Hugging Face and ModelScope.
- `./bin/check-tool-calls` fails unless the model answers a tool request with a structured call. Agents need structured calls to edit files and run commands.
- `./bin/check-responses-api [BASE_URL]` fails unless the server answers the Responses API that Codex uses.
- `./bin/smoke-test [opencode|pi|codex]` runs `./bin/check-tool-calls`, and `./bin/check-responses-api` when Codex is among the agents, then has each agent create a file and checks its contents. Without arguments it runs OpenCode, Pi and, if installed, Codex. Runs happen in the system temp folder and stay out of your session history. `SMOKE_TIMEOUT` caps each agent at 300 seconds by default.
- `./bin/bench-agents LABEL` runs the coding benchmark in `bench/`, and `./bin/bench-agents report` compares the results; see [docs/models.md](docs/models.md#comparing-models).
- `./bin/install-agents [opencode|pi]` installs the pinned agent versions; `./bin/setup` uses it. It refuses releases younger than 24 hours. Set `ALLOW_FRESH_RELEASE=1` only for a release that fixes a reported security advisory.
- `./bin/licenses` prints the license of every downloaded package and model, for [THIRD_PARTY.md](THIRD_PARTY.md).
- `./tests/run` runs the test suite; `./tests/run --live` adds the tests against the real model and agents. [CONTRIBUTING.md](CONTRIBUTING.md#tests) explains both.

## Settings

All settings are environment variables with working defaults. `MLX_MODEL` is not one of them: the agents always send `default_model`, and the scripts stop if `MLX_MODEL` is set to anything else ([why](docs/agents.md#the-model-name-is-always-default_model)).

- `MLX_BASE_URL`: where the server listens and the agents connect, in the form `http://HOST:PORT/v1`; `http://127.0.0.1:8081/v1` by default, because port 8080 is often taken.
- `QWEN_PRESET`: the pinned model to serve.
  - `kat`, the default: KAT-Coder-V2.5-Dev, pruned to 11.4 GiB.
  - `reap`: Qwen3.6-35B-A3B pruned the same way, the previous default.
  - `small`: Qwen3.5-4B, 2.8 GiB, for quick tests.
- `QWEN_REPO` and `QWEN_REVISION`: any other model, as `./bin/models` names it, at a full 40-character commit hash. Set both instead of a preset. Its files are verified like a preset's.
- `QWEN_CONTEXT`: the context window, in tokens. It defaults to what the model card states, 262144 tokens for all three presets, and is required with `QWEN_REPO`. When it does not fit in the memory the GPU may use, the server tells the agents the part that does and refuses longer requests. Set it when starting `./bin/serve-qwen`; the launchers read the server's value, so you need not repeat it. [docs/models.md](docs/models.md#memory-and-disk-guards) shows what each size needs.
- `QWEN_MAX_OUTPUT`: the most tokens one reply may use, 8192 tokens by default.
- `QWEN_PROMPT_CACHE_ENTRIES`: how many conversations' context caches the server keeps, 0 by default. Agents gain nothing from them ([why](docs/models.md#agents-get-no-help-from-the-prompt-cache)), and each costs as much memory as a full context.
- `QWEN_KV_BITS`: context-cache quantization, 8 (default) or 4. The memory guard relies on the quantized cache, which makes the server take one request at a time.
- `QWEN_TEMP`: the sampling temperature, 0.8 for `kat` and 0.6 for every other model.
- `QWEN_PREFILL_STEP`: the most prompt tokens processed per step, 512 tokens by default and at least 16. Each request uses the largest step up to this that fits in memory, down to 16 tokens.
- `QWEN_MEMORY_MARGIN`: memory, in bytes with an optional K, M or G suffix, set aside for the server's own use outside MLX, such as Python and the tokenizer; `512M` (512 MiB) by default.
- `WEB_UI_URL`: where `./bin/web-ui` listens, `http://127.0.0.1:8090` by default. It must be an address on this Mac.

`./bin/integrate install` records the server address, context window and reply limit in the agents' configs. If you start the server with other settings, such as another `QWEN_PRESET`, `QWEN_REPO`, `QWEN_CONTEXT`, `MLX_BASE_URL` or `QWEN_MAX_OUTPUT`, run it again with the same ones.

## More documentation

- [docs/agents.md](docs/agents.md): connecting OpenCode, Pi and Codex in detail, what `./bin/integrate` changes, and the provider entries for setting them up by hand.
- [docs/models.md](docs/models.md): the memory budget and guards, why KAT-Coder is the default, other models that fit, and the benchmark.
- [docs/compatibility.md](docs/compatibility.md): what works with each agent, known mlx-lm issues, and where this setup differs from the guide it started from.
- [docs/how-it-works.md](docs/how-it-works.md): implementation details, such as how the server answers Codex, where models come from and how they are verified, and how the web UI is protected.
- [docs/alternatives.md](docs/alternatives.md): other local inference servers for Apple Silicon, from a survey of Hacker News and the projects' own docs.
- [THIRD_PARTY.md](THIRD_PARTY.md): every download, how it is pinned and checked, and its license.
- [CONTRIBUTING.md](CONTRIBUTING.md): how to change things and what to check before sending a change.

## License

MIT; see [LICENSE](LICENSE).
