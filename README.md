# puny-local-ai

Run a local coding model on a 24 GiB Apple Silicon Mac with [mlx-lm](https://github.com/ml-explore/mlx-lm), and use it from the OpenCode, Pi and Codex coding agents, or from a chat page in your browser.

- **Three coding agents work with it: OpenCode, Pi and Codex.** All three talk to the same local server, at `http://127.0.0.1:8081/v1`. One command adds the local model to their configs, and one removes it again. Your usual agent setups keep working beside it.
- **The default model is KAT-Coder-V2.5-Dev.** It is a Qwen3.6-35B-A3B fine-tune for coding agents, pruned to fit in the 16 GiB that macOS lets the GPU use. [docs/models.md](docs/models.md) explains the choice and the benchmarks.
- **Settings live in one file, `puny.conf`.** Edit it with `./bin/config`, by hand, or in the web UI's Settings, as a form or as the file; every command reads it, and an environment variable still wins for one run.
- **Find other MLX models.** `./bin/models`, or Discover in `./bin/web-ui`, searches Hugging Face and ModelScope. It tells you whether a model fits in memory and can make the tool calls agents need, then downloads it pinned to a commit and checked against published hashes.
- **The Mac stays responsive.** The server refuses to start, or refuses a request with an error the agent shows, rather than let macOS swap or freeze. On a 24 GiB Mac, KAT-Coder gets a 247,319-token context window, and the agents are told that number so they compact their conversations in time ([how the guards work](docs/models.md#memory-and-disk-guards)).
- **Every download is pinned and checked.** Python packages and models are checked against recorded hashes, and npm checks the agents against the registry's integrity hash. Apart from the team-solve prompts, adapted from Memdoor under Apache-2.0, nothing third-party is stored in this repo; [THIRD_PARTY.md](THIRD_PARTY.md) lists what is downloaded and every license.

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

   On a Mac with more memory, a larger preset may fit: [docs/models.md](docs/models.md#presets-for-larger-macs) lists what each needs, and `./bin/setup mlx kat-full opencode pi` with `QWEN_PRESET=kat-full` in step 7, for example, installs and serves one. Run `./bin/setup` with no arguments for a menu that shows what is installed. It refuses OpenCode or Pi releases younger than 24 hours; if that happens, it prints when the release becomes installable.
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

This serves a page at `http://127.0.0.1:8090`, laid out like Docker Desktop: a sidebar of sections, and a status bar along the bottom that shows whether the server runs, which model it serves, its context window, the memory it holds, the memory and disk free, and any jobs running. The sections:

- **Server** starts, stops and restarts `./bin/serve-qwen`, shows its log, and warns when the running server was started with other settings than the ones saved.
- **Models** lists the presets and the models downloaded to this Mac. **Use** saves a preset as the one the server runs; **Download** fetches it; **Delete** removes a downloaded model, unless a running server uses it.
- **Discover** searches Hugging Face and ModelScope for MLX models. **Check** shows a model's size, whether mlx-lm can run it and parse its tool calls, and whether it fits in memory. **Download** fetches it the same way `./bin/models download` does, and **Use this model** makes it the one the server runs.
- **Chat** talks to the model, with its thinking shown separately.
- **Team-solve** writes a problem, its task and samples, into `state/team-solve/NAME/` and runs `./bin/team-solve` on it, or runs an earlier problem again. The run's page shows the plan, notes, solution, review and test logs the team wrote.
- **Benchmarks** runs `./bin/bench-agents` and `./bin/evals` with the agents, tasks and suites you tick, and shows both reports.
- **Agents** adds the local model to the agents' configs or removes it (`./bin/integrate`), installs OpenCode and Pi, and runs `./bin/smoke-test`.
- **Jobs** lists every download, setup, workflow and benchmark started from the page, with its live output and a **Stop** button. Jobs keep running when the page stops, and keep their output in `state/jobs/`.
- **Settings** (the gear) edits `puny.conf` as a form, with each setting's help, value and where it comes from, or as the file itself in an editor. Both save through `./bin/config`, so a value the scripts would refuse is never written, and **Save and restart server** applies server settings at once. The editor refuses to save over a change made in the terminal since it loaded the file.

The first run downloads Go and HTMX at pinned versions, checks them against their published hashes, and builds `ui/` into `state/web-ui/`; `./bin/setup web-ui` does that ahead of time. A server or job started from the page keeps running when the page stops. **Stop** stops a server from this repo wherever it was started, including one in a terminal.

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

Downloads are pinned to the commit the branch points to at that moment, and every file is checked against the hashes the source publishes, as for the presets. To serve a downloaded model, save its name, commit and context window, and clear the preset, in one change:

```sh
./bin/config set QWEN_PRESET= QWEN_REPO=mlx-community/Qwen3.5-9B-6bit QWEN_REVISION=<commit from info> QWEN_CONTEXT=262144
```

Take the context window from the model's card. Then restart `./bin/serve-qwen` and run `./bin/integrate install`, so that the agents are told the new window. **Use this model** in the web UI saves the same settings; restart the server and update the agents from the Server and Agents pages. [docs/models.md](docs/models.md#other-models-that-fit) lists candidates that were checked by hand. [docs/alternatives.md](docs/alternatives.md) surveys other local inference projects for Apple Silicon.

## Solving a problem with a team of agents

`./bin/team-solve` works on one programming problem the way [Memdoor's team-solve workflow](https://github.com/guregodevo/memdoor/tree/cee3ca70ff62acf3018cedea8b7b3b742c597682/examples/workflows/team-solve) does, after the method of [GVS5H](https://github.com/slee-persis/GVS5H). It is optional and needs nothing beyond the server and one agent.

```sh
mkdir my-problem && cd my-problem
cp -R ~/path/to/puny-local-ai/workflows/team-solve/example/. .    # task.md and tests/1.in … 3.out
~/path/to/puny-local-ai/bin/team-solve
```

For a problem of your own, write `task.md` and put pairs of `tests/N.in` and `tests/N.out` beside it. The answer is `solution.py`, which reads standard input; each sample's output is compared with `diff`. The command runs these steps, each as a fresh agent run that knows only the files in the folder:

- **plan**: the planner writes `plan.md` and starts the shared ledger, `notes.md`;
- **ideate-a** and **ideate-b**: two ideators each add a proposal, the most direct provable algorithm and one from another family of technique;
- **solve**: the solver picks one and writes `solution.py`;
- **test**: the samples run, and the result goes to `test-output.log`;
- **review**: the reviewer reads the actual test output and writes repair instructions if needed;
- **repair**: one repair worker fixes `solution.py`, or leaves it alone when every sample passed;
- **verify**: the samples run again, and only this result decides success.

It exits 0 when every sample passes. A step that fails, or takes longer than `TEAM_SOLVE_TIMEOUT`, stops the run and names its log in `team-solve-logs/`. Every step uses Pi unless `TEAM_SOLVE_AGENT` names `opencode` or `codex`. The two ideators run one after the other, because the server takes one request at a time. The prompts are in [`workflows/team-solve/`](workflows/team-solve/), adapted from Memdoor's under Apache-2.0; [CHANGES.md](workflows/team-solve/CHANGES.md) lists the changes.

## Scripts

- `./bin/setup [ITEM...]` installs what you name, or what you pick from a menu: `mlx` (the mlx-lm environment), the model presets, `opencode`, `pi` and `web-ui`.
- `./bin/serve-qwen` downloads and verifies the chosen model if needed, and runs the server in the foreground behind the memory and disk guards. It serves the Chat Completions API that OpenCode and Pi use and the Responses API that Codex uses.
- `./bin/integrate install|uninstall|status [AGENT...]` adds the local model to the agents' own configs, or removes it.
- `./bin/opencode-qwen`, `./bin/pi-qwen` and `./bin/codex-qwen` start each agent on the model for one run.
- `./bin/config [show | set KEY=VALUE... | unset KEY... | check | raw | replace]` shows and changes the settings saved in `puny.conf`; `replace` checks a whole new file from standard input before saving it.
- `./bin/web-ui` serves the web UI for the server, models, chat, workflows, benchmarks, agents and settings; see above.
- `./bin/models search|info|download|list|presets|remove` finds, checks, downloads and removes MLX models from Hugging Face and ModelScope.
- `./bin/check-tool-calls` fails unless the model answers a tool request with a structured call. Agents need structured calls to edit files and run commands.
- `./bin/check-responses-api [BASE_URL]` fails unless the server answers the Responses API that Codex uses.
- `./bin/smoke-test [opencode|pi|codex]` runs `./bin/check-tool-calls`, and `./bin/check-responses-api` when Codex is among the agents, then has each agent create a file and checks its contents. Without arguments it runs OpenCode, Pi and, if installed, Codex. Runs happen in the system temp folder and stay out of your session history. `SMOKE_TIMEOUT` caps each agent at 300 seconds by default.
- `./bin/team-solve [FOLDER]` has a team of fresh agent runs solve the programming problem in FOLDER; see above.
- `./bin/bench-agents LABEL` runs the coding benchmark in `bench/`, and `./bin/bench-agents report` compares the results; see [docs/models.md](docs/models.md#comparing-models).
- `./bin/evals LABEL` tests the served model directly, with no agent: tool calls, coding, long context and speed; `./bin/evals report` compares the results. See [docs/models.md](docs/models.md#evaluating-the-model-directly).
- `./bin/install-agents [opencode|pi]` installs the pinned agent versions; `./bin/setup` uses it. It refuses releases younger than 24 hours. Set `ALLOW_FRESH_RELEASE=1` only for a release that fixes a reported security advisory.
- `./bin/licenses` prints the license of every downloaded package and model, for [THIRD_PARTY.md](THIRD_PARTY.md).
- `./tests/run` runs the test suite; `./tests/run --live` adds the tests against the real model and agents. [CONTRIBUTING.md](CONTRIBUTING.md#tests) explains both.

## Settings

Every setting has a working default. To change one for good, save it in `puny.conf` in this repo:

```sh
./bin/config                                  # each setting, its value and where it comes from
./bin/config set QWEN_PRESET=small QWEN_TEMP=0.7
./bin/config unset QWEN_TEMP                  # back to the default
```

`./bin/config set` checks the file as it would be after the change, and saves nothing if a command would refuse a value. The web UI's Settings make the same changes, as a form or by editing the file. You can also edit `puny.conf` by hand. It holds one `KEY=VALUE` per line, with `#` comments. Values are taken literally, never run as shell code. The file is ignored by git.

An environment variable of the same name wins over the file, for one run: `QWEN_PRESET=small ./bin/serve-qwen`. The model is one choice: when the environment names any of `QWEN_PRESET`, `QWEN_REPO`, `QWEN_REVISION` or `QWEN_CONTEXT`, the file's values for all four are left out, so the two never mix. `PUNY_CONFIG` names a different file.

`MLX_MODEL` is not a setting: the agents always send `default_model`, and the scripts stop if `MLX_MODEL` is set to anything else ([why](docs/agents.md#the-model-name-is-always-default_model)).

- `MLX_BASE_URL`: where the server listens and the agents connect, in the form `http://HOST:PORT/v1`; `http://127.0.0.1:8081/v1` by default, because port 8080 is often taken.
- `QWEN_PRESET`: the pinned model to serve.
  - `kat`, the default: KAT-Coder-V2.5-Dev, pruned to 11.4 GiB.
  - `reap`: Qwen3.6-35B-A3B pruned the same way, the previous default.
  - `small`: Qwen3.5-4B, 2.8 GiB, for quick tests.
  - For Macs whose GPU may use more than 16 GiB: `kat-full` (KAT-Coder unpruned, 20.4 GiB), `qwen38-4` and `qwen38` (Qwen3.8-27B at 4 and 8 bits, 15.0 and 27.5 GiB), `coder-next` (Qwen3-Coder-Next at 4 bits, 41.8 GiB), `coder-next-8` (at 8 bits, 78.8 GiB), and `glimmer-4` and `glimmer` (Meta's Muse Glimmer at 4 and 8 bits, 18.1 and 31.1 GiB to download, 14.6 and 27.6 GiB loaded). [docs/models.md](docs/models.md#presets-for-larger-macs) shows what each needs.
- `QWEN_REPO` and `QWEN_REVISION`: any other model, as `./bin/models` names it, at a full 40-character commit hash. Set both instead of a preset. Its files are verified like a preset's.
- `QWEN_CONTEXT`: the context window, in tokens. It defaults to what the model card states, 262144 tokens for all three presets, and is required with `QWEN_REPO`. When it does not fit in the memory the GPU may use, the server tells the agents the part that does and refuses longer requests. Set it when starting `./bin/serve-qwen`; the launchers read the server's value, so you need not repeat it. [docs/models.md](docs/models.md#memory-and-disk-guards) shows what each size needs.
- `QWEN_MAX_OUTPUT`: the most tokens one reply may use, 8192 tokens by default.
- `QWEN_PROMPT_CACHE_ENTRIES`: how many conversations' context caches the server keeps, 0 by default. Agents gain nothing from them ([why](docs/models.md#agents-get-no-help-from-the-prompt-cache)), and each costs as much memory as a full context.
- `QWEN_KV_BITS`: context-cache quantization, 8 (default) or 4. The memory guard relies on the quantized cache, which makes the server take one request at a time.
- `QWEN_TEMP`: the sampling temperature, by default the preset's: 0.8 for `kat` and `kat-full`, 1.0 for `qwen38-4`, `qwen38`, `coder-next`, `coder-next-8`, `glimmer-4` and `glimmer`, and 0.6 for the rest and for any other model.
- `QWEN_PREFILL_STEP`: the most prompt tokens processed per step, 512 tokens by default and at least 16. Each request uses the largest step up to this that fits in memory, down to 16 tokens.
- `QWEN_MEMORY_MARGIN`: memory, in bytes with an optional K, M or G suffix, set aside for the server's own use outside MLX, such as Python and the tokenizer; `512M` (512 MiB) by default.
- `WEB_UI_URL`: where `./bin/web-ui` listens, `http://127.0.0.1:8090` by default. It must be an address on this Mac.
- `TEAM_SOLVE_AGENT`: the agent every `./bin/team-solve` step runs, `pi` (default), `opencode` or `codex`. `TEAM_SOLVE_TIMEOUT`: the seconds each step may take, 600 by default.
- `SMOKE_TIMEOUT`, the `BENCH_*` settings of `./bin/smoke-test` and `./bin/bench-agents`, and the `EVAL_*` settings of `./bin/evals` can be saved too; [docs/models.md](docs/models.md#comparing-models) describes the benchmark and eval ones.

`./bin/integrate install` records the server address, context window and reply limit in the agents' configs. After changing `QWEN_PRESET`, `QWEN_REPO`, `QWEN_CONTEXT`, `MLX_BASE_URL` or `QWEN_MAX_OUTPUT`, restart the server and run it again; it reads the same `puny.conf`.

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
