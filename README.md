# puny-local-ai

Run a local coding model on a 24 GiB Apple Silicon Mac with [mlx-lm](https://github.com/ml-explore/mlx-lm), and use it from the OpenCode, Pi and Codex coding agents, or from a chat page in your browser.

- **Three coding agents use it: OpenCode, Pi and Codex.** One command adds the local model to their configs and one removes it; your own setups keep working beside it.
- **The default model is KAT-Coder-V2.5-Dev**, a Qwen3.6-35B-A3B fine-tune for coding agents, pruned to fit the 16 GiB macOS lets the GPU use ([why](docs/models.md)).
- **The Mac stays responsive.** The server refuses to start, or refuses a request with an error the agent shows, rather than let macOS swap or freeze ([how](docs/models.md#memory-and-disk-guards)).
- **Workflows let a small model do larger jobs** as a state machine of short, fresh agent runs that loop until a check passes ([docs/workflows.md](docs/workflows.md)).
- **Every download is pinned and checked** against published hashes; nothing third-party is vendored ([THIRD_PARTY.md](THIRD_PARTY.md)).
- **Settings live in `puny.conf`**, edited with `./bin/config` or the web UI.

## Bootstrap

These steps take a Mac with nothing installed to a coding agent running on the local model.

1. **Check the hardware.** You need Apple Silicon (`uname -m` prints `arm64`). The memory figures in these docs are for 24 GiB.
2. **Install Apple's command line tools**, which provide `git` and `python3` (`curl`, `perl` and the other tools the scripts use come with macOS):

   ```sh
   xcode-select --install
   ```

3. **Install [Homebrew](https://brew.sh)** with the command shown on its home page, then run the commands its installer prints under "Next steps", so that `brew` is on your PATH.
4. **Install uv and Node.js, and Codex if you want it.** uv downloads Python 3.14.6 for mlx-lm into `state/python`. Pi needs Node.js 22.19 or newer. Codex is not pinned; it was tested with 0.166.0.

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

   On a Mac with more memory a larger preset may fit ([presets for larger Macs](docs/models.md#presets-for-larger-macs)). `./bin/setup` with no arguments shows a menu of what is installed.
7. **Start the server** in its own terminal and leave it running:

   ```sh
   ./bin/serve-qwen
   ```

   It refuses to start when too little memory is free, and says how much it needs: about 14.4 GiB for KAT-Coder, 17 GiB for its longest requests. Close large applications such as browsers, then try again.
8. **Check that everything works**, in a second terminal:

   ```sh
   ./bin/smoke-test
   ```

   It checks the model's tool calls and Codex's API, then has each agent create a file in a throwaway folder.
9. **Add the local model to your agents:**

   ```sh
   ./bin/integrate install
   ```

   This adds a provider called `puny` to whichever agents are installed, and changes nothing else. `./bin/integrate uninstall` removes it.
10. **Start an agent** in the project you want to work on, with the local model:

    ```sh
    cd ~/code/my-project
    opencode --model puny/qwen-local
    pi --provider puny --model default_model
    codex -p puny
    ```

To try the setup with a smaller 2.8 GiB model first, run `./bin/setup mlx small` and then `QWEN_PRESET=small ./bin/serve-qwen`.

## Using the coding agents

Both ways need `./bin/serve-qwen` running, and both leave your own agent setup as it was.

- **In the agents' own configs**, after `./bin/integrate install`: `opencode --model puny/qwen-local`, `pi --provider puny --model default_model`, or `codex -p puny`, or pick the model inside the agent. `./bin/integrate status` shows what is installed; name agents to set up only some.
- **With the launchers**, which change no config: `./bin/opencode-qwen`, `./bin/pi-qwen` and `./bin/codex-qwen`, from any folder, with the agent's own arguments.

[docs/agents.md](docs/agents.md) explains what each way changes and shows the provider entries; [docs/compatibility.md](docs/compatibility.md) lists what works with each agent.

## The web UI

```sh
./bin/web-ui
```

This serves a page at `http://127.0.0.1:8090` with a sidebar of sections and a status bar showing the server, model, context window and free memory and disk:

- **Server**: start, stop and restart `./bin/serve-qwen`, and read its log.
- **Models** and **Discover**: the presets and downloaded models, and a search of Hugging Face and ModelScope that checks whether a model fits and can make tool calls, then downloads it.
- **Chat** with the model, its thinking shown apart.
- **Workflows**: run team-solve or a goal loop on a folder in `state/`.
- **Benchmarks**: `./bin/bench-agents`, `./bin/evals` and `./bin/verify`, and their reports.
- **Agents**: `./bin/integrate`, agent installs and `./bin/smoke-test`.
- **Jobs**: every long command started from the page, with live output and **Stop**. Jobs keep running when the page stops.
- **Settings**: `puny.conf` as a form or as the file, saved through `./bin/config`.

The first run downloads Go and HTMX at pinned versions and builds `ui/`. The page listens only on this Mac and refuses requests from other sites ([details](docs/how-it-works.md#the-web-ui)).

## Finding and serving other models

```sh
./bin/models search "qwen3.6 coder"
./bin/models info mlx-community/Qwen3.5-9B-6bit
./bin/models download mlx-community/Qwen3.5-9B-6bit
./bin/models list
```

`info` reads a model's config and file list without downloading the weights, and reports the download size, whether mlx-lm 0.32.2 can run it and parse its tool calls (without that, agents cannot edit files), its context window, and the longest conversation that fits next to the weights. Put `modelscope:` before a name to use ModelScope's copy. Downloads are pinned to the current commit and every file is checked against the source's hashes.

To serve a downloaded model, save it in place of the preset, then restart the server and run `./bin/integrate install` so the agents learn the new window:

```sh
./bin/config set QWEN_PRESET= QWEN_REPO=mlx-community/Qwen3.5-9B-6bit QWEN_REVISION=<commit from info> QWEN_CONTEXT=262144
```

Take the context window from the model's card. [docs/models.md](docs/models.md#other-models-that-fit) lists candidates checked by hand, and [docs/alternatives.md](docs/alternatives.md) other inference servers for Apple Silicon.

## Workflows

A workflow runs short, fresh agent runs as a state machine on a folder: each step does one narrow job and hands on through files, and a check, not the model, decides when the work is done. Two come bundled:

```sh
./bin/workflow goal FOLDER    # loop: check, one small step, check… until check.sh passes
./bin/team-solve FOLDER       # plan, two ideas, solve, then review and repair until the goal passes
```

`goal` needs `goal.md` and `check.sh` in the folder. `team-solve` needs `task.md`, plus samples (`tests/N.in` with `tests/N.out`), a `check.sh`, or both; the result can be any files in any language. Each has an example in `workflows/NAME/example/`. [docs/workflows.md](docs/workflows.md) describes both and how to write your own graph.

## Scripts

- `./bin/setup [ITEM...]`: install `mlx`, model presets, `opencode`, `pi` or `web-ui`, or pick from a menu.
- `./bin/serve-qwen`: run the server in the foreground behind the memory and disk guards, downloading the model first if needed.
- `./bin/integrate install|uninstall|status [AGENT...]`: add the local model to the agents' configs, or remove it.
- `./bin/opencode-qwen`, `./bin/pi-qwen`, `./bin/codex-qwen`: run an agent on the local model without changing its config.
- `./bin/config [show | set KEY=VALUE... | unset KEY... | check | raw | replace]`: show and change `puny.conf`.
- `./bin/web-ui`: the web UI.
- `./bin/models search|info|download|list|presets|remove`: find, check, download and remove MLX models.
- `./bin/workflow NAME [FOLDER] | check NAME | list` and `./bin/team-solve [FOLDER]`: run [workflows](docs/workflows.md).
- `./bin/smoke-test [AGENT...]`: check tool calls and the Responses API, then have each agent create a file.
- `./bin/check-tool-calls` and `./bin/check-responses-api`: the smoke test's first two checks on their own.
- `./bin/bench-agents LABEL` and `./bin/evals LABEL`: benchmark the agents on `bench/`, or test the model directly; `report` compares labels ([docs/models.md](docs/models.md#comparing-models)).
- `./bin/verify [PRESET]`: check a preset end to end on this Mac and write a report to bring back ([how](docs/models.md#checking-a-preset-on-another-mac)).
- `./bin/install-agents [opencode|pi]`: install the pinned agents; it refuses releases younger than 24 hours unless `ALLOW_FRESH_RELEASE=1`, which is only for a security fix.
- `./bin/licenses`: the license of every download, for THIRD_PARTY.md.
- `./tests/run [--live]`: the test suite ([CONTRIBUTING.md](CONTRIBUTING.md#tests)).

## Settings

Every setting has a working default. To change one for good, save it in `puny.conf` in this repo:

```sh
./bin/config                                  # each setting, its value and where it comes from
./bin/config set QWEN_PRESET=small QWEN_TEMP=0.7
./bin/config unset QWEN_TEMP                  # back to the default
```

`./bin/config set` saves nothing a command would refuse. The file holds one `KEY=VALUE` per line with `#` comments, is never run as shell code, and is ignored by git. An environment variable wins over the file for one run, as in `QWEN_PRESET=small ./bin/serve-qwen`; when the environment names any of the four model settings (`QWEN_PRESET`, `QWEN_REPO`, `QWEN_REVISION`, `QWEN_CONTEXT`), the file's four are all left out. `PUNY_CONFIG` names another file.

`MLX_MODEL` is not a setting: the agents always send `default_model`, and the scripts stop if it is set to anything else ([why](docs/agents.md#the-model-name-is-always-default_model)).

- `MLX_BASE_URL`: where the server listens and the agents connect, in the form `http://HOST:PORT/v1`; `http://127.0.0.1:8081/v1` by default, because port 8080 is often taken.
- `QWEN_PRESET`: the pinned model to serve.
  - `kat`, the default: KAT-Coder-V2.5-Dev, pruned to 11.4 GiB.
  - `reap`: Qwen3.6-35B-A3B pruned the same way, the previous default.
  - `small`: Qwen3.5-4B, 2.8 GiB, for quick tests.
  - For Macs whose GPU may use more than 16 GiB: `kat-full` (KAT-Coder unpruned, 20.4 GiB), `qwen38-4` and `qwen38` (Qwen3.8-27B at 4 and 8 bits, 15.0 and 27.5 GiB), `coder-next` (Qwen3-Coder-Next at 4 bits, 41.8 GiB), `coder-next-8` (at 8 bits, 78.8 GiB), and `glimmer-4` and `glimmer` (Meta's Muse Glimmer at 4 and 8 bits, 18.1 and 31.1 GiB to download, 14.6 and 27.6 GiB loaded). [docs/models.md](docs/models.md#presets-for-larger-macs) shows what each needs.
- `QWEN_REPO` and `QWEN_REVISION`: any other model, as `./bin/models` names it, at a full 40-character commit hash. Set both instead of a preset. Its files are verified like a preset's.
- `QWEN_CONTEXT`: the context window in tokens, by default the model card's; required with `QWEN_REPO`. When it does not fit, the server tells the agents the part that does and refuses longer requests.
- `QWEN_MAX_OUTPUT`: the most tokens one reply may use, 8192 tokens by default.
- `QWEN_PROMPT_CACHE_ENTRIES`: conversations whose context cache the server keeps, 0 by default; agents gain nothing from them ([why](docs/models.md#agents-get-no-help-from-the-prompt-cache)).
- `QWEN_KV_BITS`: context-cache quantization, 8 (default) or 4.
- `QWEN_TEMP`: the sampling temperature, by default the preset's ([sampling](docs/models.md#sampling)).
- `QWEN_PREFILL_STEP`: the most prompt tokens processed per step, 512 by default; long prompts use smaller steps to fit in memory.
- `QWEN_MEMORY_MARGIN`: bytes set aside for the server outside MLX, with an optional K, M or G suffix; `512M` by default.
- `WEB_UI_URL`: where `./bin/web-ui` listens, `http://127.0.0.1:8090` by default; it must be on this Mac.
- `WORKFLOW_AGENT` and `WORKFLOW_TIMEOUT`: the agent workflow steps run, `pi` by default, and the seconds each may take, 600 by default.
- `SMOKE_TIMEOUT`, `BENCH_*` and `EVAL_*`: see [docs/models.md](docs/models.md#comparing-models).

After changing the model, `MLX_BASE_URL` or `QWEN_MAX_OUTPUT`, restart the server and run `./bin/integrate install` again, since the agents' configs record the address, window and reply limit.

## More documentation

- [docs/agents.md](docs/agents.md): connecting the agents, what `./bin/integrate` changes, and the provider entries.
- [docs/models.md](docs/models.md): the memory budget and guards, the presets, and the benchmarks.
- [docs/workflows.md](docs/workflows.md): the workflow engine, the bundled workflows and the graph format.
- [docs/compatibility.md](docs/compatibility.md): what works with each agent, and known mlx-lm issues.
- [docs/how-it-works.md](docs/how-it-works.md): how the server answers Codex, where models come from, and how the web UI is protected.
- [docs/alternatives.md](docs/alternatives.md): other local inference servers for Apple Silicon.
- [THIRD_PARTY.md](THIRD_PARTY.md): every download, how it is checked, and its license.
- [CONTRIBUTING.md](CONTRIBUTING.md): the layout, the rules and the tests.

## License

MIT; see [LICENSE](LICENSE).
