# puny-local-ai

Run a local coding model on a 24 GiB Apple Silicon Mac with [mlx-lm](https://github.com/ml-explore/mlx-lm), and connect the OpenCode, Pi and Codex coding agents to it.

- The default model is KAT-Coder-V2.5-Dev, a Qwen3.6-35B-A3B fine-tune for coding agents, pruned to fit in the 16 GiB that macOS lets the GPU use. [docs/models.md](docs/models.md) explains the choice and the benchmarks.
- OpenCode and Pi work today. Codex needs a Responses API proxy, which mlx-lm does not provide; see [docs/compatibility.md](docs/compatibility.md).
- One command, `./bin/integrate install`, adds the local model to the agents' own configs, and `./bin/integrate uninstall` takes it out again, restoring each file exactly. The launchers in `bin/` do the same for one run without writing to your configs. Either way, your usual agent setups keep working side by side.
- OpenCode must be version 2.0 or later; the scripts check, and `./bin/setup opencode` installs the pinned 2.1.1.
- Each model gets the context window its model card states, 262,144 tokens, as far as it fits in memory. Guards keep the server from running the Mac out of memory or disk: it refuses to start, or refuses a request with an error the agent shows, rather than let macOS swap or freeze. On a 24 GiB Mac, KAT-Coder's window is 247,319 tokens, and the agents are told that number so they compact their conversations in time; see [docs/models.md](docs/models.md#memory-and-disk-guards).
- Every download is pinned to an exact version. Python packages and models are checked against hashes this repo records, and npm checks the agents against the registry's integrity hash. Nothing third-party is stored in this repo; [THIRD_PARTY.md](THIRD_PARTY.md) lists what is downloaded and its license.

## Bootstrap

These steps take a Mac with nothing installed to a coding agent running on the local model.

1. **Check the hardware.** You need Apple Silicon (`uname -m` prints `arm64`). The memory figures in these docs are for 24 GiB.
2. **Install Apple's command line tools**, which provide `git` and `python3` (`curl`, `perl` and the other tools the scripts use come with macOS):

   ```sh
   xcode-select --install
   ```

3. **Install [Homebrew](https://brew.sh)** with the command shown on its home page, then run the commands its installer prints under "Next steps", so that `brew` is on your PATH.
4. **Install uv and Node.js.** uv downloads Python 3.14.6 for mlx-lm into `state/python`. Pi 1.3.0 needs Node.js 22.19 or newer.

   ```sh
   brew install uv node
   brew install codex    # only if you want to try Codex
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

   It checks that the model returns structured tool calls, then has OpenCode and Pi each create a file in a throwaway folder.
9. **Add the local model to your agents:**

   ```sh
   ./bin/integrate install
   ```

   This adds a provider called `puny` to the OpenCode and Pi configs in your home folder and changes nothing else; `./bin/integrate uninstall` removes it again. If OpenCode's background service is running, the command says so: restart it with `opencode service restart` when no session needs it.
10. **Start an agent** in the project you want to work on, with the local model:

    ```sh
    cd ~/code/my-project
    opencode --model puny/qwen-local          # or: pi --provider puny --model default_model
    ```

    To leave your configs alone instead, skip step 9 and run `~/path/to/puny-local-ai/bin/opencode-qwen` or `~/path/to/puny-local-ai/bin/pi-qwen`.

To try the setup with a smaller 2.8 GiB model first, run `./bin/setup mlx small` and then `QWEN_PRESET=small ./bin/serve-qwen`.

## Connecting the coding agents

There are two ways in. Both leave your own agent setup working as before, and both need the server from `./bin/serve-qwen` running while you use the agent.

### Add the model to your agents' own configs

One command adds a provider called `puny` to each installed agent:

```sh
./bin/integrate install
```

Then start the agents as you normally would and pick the local model:

- **OpenCode:** `opencode --model puny/qwen-local`, or choose "Qwen (local MLX)" with `/models`. Your default model stays as it was.
- **Pi:** `pi --provider puny --model default_model`, or choose it with `/model`.
- **Codex:** `codex -p puny`, once a Responses API proxy runs; install it with `CODEX_BASE_URL=http://HOST:PORT/v1 ./bin/integrate install codex`. Without `CODEX_BASE_URL`, `./bin/integrate install` skips Codex and says why, and `./bin/integrate install codex` stops with an error.

`./bin/integrate status` shows what is installed, and `./bin/integrate uninstall` removes it again. To set up only some agents, name them: `./bin/integrate install pi`.

What it changes, and how it keeps your setup safe:

- **OpenCode:** adds `providers.puny` to `~/.config/opencode/opencode.jsonc` or `opencode.json`, whichever you have, or creates `opencode.json`. If both exist, it stops and asks you to merge them first. Comments and formatting are kept. `XDG_CONFIG_HOME` moves the folder. The entry uses the OpenCode 2 format, so the command first checks that OpenCode 2.0 or later is installed. OpenCode's background service reads its config when first used and keeps it until it restarts; when the service is running, the command says so, and `opencode service restart` loads the change. It never restarts the service itself, because that ends the sessions running in it.
- **Pi:** adds `providers.puny` to `~/.pi/agent/models.json`, or creates it. `PI_CODING_AGENT_DIR` moves the folder.
- **Codex:** writes its own profile, `~/.codex/puny.config.toml`, and touches nothing else. `CODEX_HOME` moves the folder.
- Before changing a file, it saves a copy in `state/integrations/backups/`. It records the exact text it added, and uninstalling removes exactly that text, so each file goes back byte for byte to what it was, plus any edits you made since. Files and folders it created are deleted.
- It refuses to touch a `puny` entry or profile it did not add, a file it cannot parse, a read-only file, a symlink that points nowhere, and a file that changes while it runs. A symlinked config, as dotfile managers make, stays a symlink; the file it points to changes. Line endings, a byte-order mark and hard links are kept. Only one `./bin/integrate` runs at a time, and it keeps the ten newest backups per agent.
- Keep `state/` until you have uninstalled: the record of what was added lives in `state/integrations/`. Without it, uninstalling still finds and removes the `puny` entry, but cannot restore the file byte for byte, and says so if it leaves an empty `providers` object.
- The configs record the server address, context window and reply limit at install time. The window is the one `./bin/serve-qwen` will tell the agents for the same settings: the model card's window for `QWEN_PRESET` (or `QWEN_CONTEXT`), or less when that does not fit in the memory the GPU may use, 247,319 tokens for KAT-Coder on a 24 GiB Mac. So `./bin/integrate install` needs the model downloaded first. If you start the server with other settings, such as another `QWEN_PRESET`, `QWEN_CONTEXT`, `MLX_BASE_URL` or `QWEN_MAX_OUTPUT`, run `./bin/integrate install` again with the same ones.

### Or use the launchers

`./bin/opencode-qwen`, `./bin/pi-qwen` and `./bin/codex-qwen` start each agent with the same provider for one run, without writing to your configs. Run them from any folder; arguments go straight to the agent. They tell the agent the context window the running server can serve, which `./bin/serve-qwen` records in `state/served/`.

- `./bin/opencode-qwen` passes the config through `OPENCODE_CONFIG_CONTENT` and adds `--standalone`, because OpenCode's shared background service never sees the launcher's environment.
- `./bin/pi-qwen` gives Pi its own agent folder, `state/pi-agent`, through `PI_CODING_AGENT_DIR`; sessions from these runs live there too.
- `./bin/codex-qwen` checks for a Responses API at `CODEX_BASE_URL` first and stops with an explanation when there is none ([docs/compatibility.md](docs/compatibility.md#codex-needs-a-responses-api-proxy)).

### The provider, for setting it up by hand

`./bin/integrate` and the launchers write these entries, here with the default settings. Two settings matter for every agent:

- **The model name must be `default_model`.** `mlx_lm.server` treats any other name as a model to load, and would swap out the one in memory; the guard refuses such requests ([`server.py`](https://github.com/ml-explore/mlx-lm/blob/93e6d4a6447b3080861985947f0820e76fcb957d/mlx_lm/server.py#L388-L395)).
- **Thinking text arrives in a `reasoning` field** ([`server.py`](https://github.com/ml-explore/mlx-lm/blob/93e6d4a6447b3080861985947f0820e76fcb957d/mlx_lm/server.py#L1511)), so each agent is told to read it there.

OpenCode, in its [global config file](https://github.com/anomalyco/opencode/blob/ba546ec1598a70db2570e9c6258028d955ba8f58/packages/web/src/content/docs/config.mdx). These are the OpenCode v2 key names (`providers`, `package`, `settings`, `modelID`), defined in its [provider schema](https://github.com/anomalyco/opencode/blob/ba546ec1598a70db2570e9c6258028d955ba8f58/packages/schema/src/config/provider.ts) and [model compatibility schema](https://github.com/anomalyco/opencode/blob/ba546ec1598a70db2570e9c6258028d955ba8f58/packages/schema/src/model-compatibility.ts) at v2.1.1; the v2.1.1 docs still show the v1 names.

```json
{
  "providers": {
    "puny": {
      "name": "puny-local-ai",
      "package": "@opencode/ai/providers/openai-compatible",
      "settings": {"baseURL": "http://127.0.0.1:8081/v1", "apiKey": "local"},
      "compatibility": {"reasoningField": "reasoning"},
      "models": {
        "qwen-local": {
          "modelID": "default_model",
          "name": "Qwen (local MLX)",
          "capabilities": {"tools": true, "input": ["text"], "output": ["text"], "reasoning": {"supported": true}},
          "limit": {"context": 262144, "output": 8192}
        }
      }
    }
  }
}
```

Pi, in `models.json` in its agent folder ([Pi's model docs](https://github.com/earendil-works/pi/blob/71023aec1546d6d677a65669c5665c0927ce0dd0/packages/coding-agent/docs/models.md), [environment variables](https://github.com/earendil-works/pi/blob/71023aec1546d6d677a65669c5665c0927ce0dd0/packages/coding-agent/docs/environment-variables.md)). The `apiKey` is a placeholder: Pi hides models without credentials, and the server ignores it. `thinkingFormat` lets Pi switch Qwen's thinking on and off; it and the other `compat` settings are defined in Pi's [compat schema](https://github.com/earendil-works/pi/blob/71023aec1546d6d677a65669c5665c0927ce0dd0/packages/ai/src/providers/compat-schema.ts#L195-L264).

```json
{
  "providers": {
    "puny": {
      "name": "puny-local-ai",
      "baseUrl": "http://127.0.0.1:8081/v1",
      "api": "openai-completions",
      "apiKey": "local",
      "models": [
        {
          "id": "default_model",
          "name": "Qwen (local MLX)",
          "reasoning": true,
          "input": ["text"],
          "contextWindow": 262144,
          "maxTokens": 8192,
          "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
          "compat": {"supportsDeveloperRole": false, "supportsReasoningEffort": false, "thinkingFormat": "qwen-chat-template"}
        }
      ]
    }
  }
}
```

Codex, as a profile that it layers over your usual config when you run `codex -p puny`. The provider id must not be a built-in one such as `openai`, `ollama` or `lmstudio`, because Codex ignores user entries that reuse those ids ([`lib.rs` at 0.163.0](https://github.com/openai/codex/blob/83086188ae0614bd882818ff1aa23f3194962012/codex-rs/model-provider-info/src/lib.rs#L694-L699)).

```toml
# Added by puny-local-ai. Remove it with: ./bin/integrate uninstall codex
model_provider = "puny"
model = "default_model"
model_context_window = 262144

[model_providers.puny]
name = "puny-local-ai"
base_url = "http://HOST:PORT/v1"
wire_api = "responses"
```

## Scripts

- `./bin/setup [ITEM...]` installs what you name, or what you pick from a menu: `mlx` (the mlx-lm environment), the models `kat`, `reap` and `small`, `opencode` and `pi`.
- `./bin/serve-qwen` downloads and verifies the chosen model if needed, and runs `mlx_lm.server` in the foreground behind the memory and disk guards.
- `./bin/integrate install|uninstall|status [AGENT...]` adds the local model to the agents' own configs, or removes it; see above.
- `./bin/opencode-qwen`, `./bin/pi-qwen` and `./bin/codex-qwen` start each agent on the model for one run; see above.
- `./bin/check-tool-calls` fails unless the model answers a tool request with a structured call in `choices[0].message.tool_calls`. Agents need structured calls to edit files and run commands.
- `./bin/smoke-test [opencode|pi]` runs `check-tool-calls`, then has each agent create a file and checks its contents. Runs happen in the system temp folder with a throwaway OpenCode data folder, so they stay out of your session history. `SMOKE_TIMEOUT` caps each agent at 300 seconds by default.
- `./bin/bench-agents LABEL` runs the coding benchmark in `bench/`, and `./bin/bench-agents report` compares the results; see [docs/models.md](docs/models.md#comparing-models).
- `./bin/check-responses-api [BASE_URL]` reports whether an endpoint serves the Responses API.
- `./bin/install-agents [opencode|pi]` installs the pinned agent versions; `./bin/setup` uses it. It refuses releases younger than 24 hours. Set `ALLOW_FRESH_RELEASE=1` only for a release that fixes a reported security advisory.
- `./bin/licenses` prints the license of every downloaded package and model, for [THIRD_PARTY.md](THIRD_PARTY.md).
- `./tests/run` runs the test suite; `./tests/run --live` adds the tests against the real model and agents. [CONTRIBUTING.md](CONTRIBUTING.md#tests) explains both.

## Settings

All settings are environment variables with working defaults. `MLX_MODEL` is not one of them: the agents always send `default_model`, and the scripts stop if `MLX_MODEL` is set to anything else.

- `MLX_BASE_URL`: where the server listens and the agents connect, in the form `http://HOST:PORT/v1`; `http://127.0.0.1:8081/v1` by default, because port 8080 is often taken.
- `QWEN_PRESET`: the pinned model to serve.
  - `kat`, the default: KAT-Coder-V2.5-Dev, pruned to 11.4 GiB.
  - `reap`: Qwen3.6-35B-A3B pruned the same way, the previous default.
  - `small`: Qwen3.5-4B, 2.8 GiB, for quick tests.
- `QWEN_REPO` and `QWEN_REVISION`: any other Hugging Face model, at a full 40-character commit hash. Set both instead of a preset. Its files are verified like a preset's.
- `QWEN_CONTEXT`: the context window, in tokens. It defaults to what the model card states, 262144 tokens for all three presets, and is required with `QWEN_REPO`. When it does not fit in the memory the GPU may use, the server tells the agents the part that does and refuses longer requests. Set it when starting `./bin/serve-qwen`; the launchers read the server's value, so you need not repeat it. [docs/models.md](docs/models.md#memory-and-disk-guards) shows what each size needs.
- `QWEN_MAX_OUTPUT`: the most tokens one reply may use, 8192 tokens by default.
- `QWEN_PROMPT_CACHE_ENTRIES`: how many conversations' context caches the server keeps, 0 by default. Agents gain nothing from them ([why](docs/models.md#agents-get-no-help-from-the-prompt-cache)), and each costs as much memory as a full context.
- `QWEN_KV_BITS`: context-cache quantization, 8 (default) or 4. The memory guard relies on the quantized cache, which makes the server take one request at a time.
- `QWEN_TEMP`: the sampling temperature, 0.8 for `kat` and 0.6 for every other model.
- `QWEN_PREFILL_STEP`: the most prompt tokens processed per step, 512 tokens by default and at least 16. Each request uses the largest step up to this that fits in memory, down to 16 tokens.
- `QWEN_MEMORY_MARGIN`: memory, in bytes with an optional K, M or G suffix, set aside for the server's own use outside MLX, such as Python and the tokenizer; `512M` (512 MiB) by default.

## More documentation

- [docs/models.md](docs/models.md): the memory budget, why KAT-Coder is the default, other models that fit, and the benchmark.
- [docs/compatibility.md](docs/compatibility.md): Codex and the Responses API, known mlx-lm issues, and where this setup differs from the guide it started from.
- [THIRD_PARTY.md](THIRD_PARTY.md): every download, how it is pinned and checked, and its license.
- [CONTRIBUTING.md](CONTRIBUTING.md): how to change things and what to check before sending a change.

## License

MIT; see [LICENSE](LICENSE).
