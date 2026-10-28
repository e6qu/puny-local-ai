# Connecting the coding agents

How OpenCode, Pi and Codex reach the local model, what `./bin/integrate` and the launchers change, and the provider entries for setting the agents up by hand. The [README](../README.md#using-the-coding-agents) has the short version.

All three agents use the server that `./bin/serve-qwen` starts, at `MLX_BASE_URL` (`http://127.0.0.1:8081/v1` by default). OpenCode and Pi send Chat Completions requests. Codex sends Responses API requests, which the same server answers at the same address; [how-it-works.md](how-it-works.md#how-codex-is-served) explains how.

## Adding the model to the agents' own configs

`./bin/integrate install` adds a provider called `puny` to each installed agent, and `./bin/integrate uninstall` removes it. Name agents to set up only some: `./bin/integrate install opencode codex`.

What it changes:

- **OpenCode:** adds `providers.puny` to `~/.config/opencode/opencode.jsonc` or `opencode.json`, whichever you have, or creates `opencode.json`. If both exist, it stops and asks you to merge them first. Comments and formatting are kept. `XDG_CONFIG_HOME` moves the folder. The entry uses the OpenCode 2 format, so the command first checks that OpenCode 2.0 or later is installed. OpenCode's background service reads its config when first used and keeps it until it restarts. When the service is running, the command says so, and `opencode service restart` loads the change. It never restarts the service itself, because that ends the sessions running in it.
- **Pi:** adds `providers.puny` to `~/.pi/agent/models.json`, or creates it. `PI_CODING_AGENT_DIR` moves the folder.
- **Codex:** writes its own profile, `~/.codex/puny.config.toml`, and touches nothing else. `CODEX_HOME` moves the folder. Codex layers the profile over your usual config when you run `codex -p puny`.

How it keeps your setup safe:

- Before changing a file, it saves a copy in `state/integrations/backups/`. It records the exact text it added, and uninstalling removes exactly that text, so each file goes back byte for byte to what it was, plus any edits you made since. Files and folders it created are deleted.
- It refuses to touch:
  - a `puny` entry or profile it did not add;
  - a file it cannot parse;
  - a read-only file;
  - a symlink that points nowhere;
  - a file that changes while it runs.
- A symlinked config, as dotfile managers make, stays a symlink; the file it points to changes. Line endings, a byte-order mark and hard links are kept.
- Only one `./bin/integrate` runs at a time, and it keeps the ten newest backups per agent.
- Keep `state/` until you have uninstalled: the record of what was added lives in `state/integrations/`. Without it, uninstalling still finds and removes the `puny` entry, but cannot restore the file byte for byte, and says so if it leaves an empty `providers` object.

The configs record the server address, context window and reply limit at install time. The window is the one `./bin/serve-qwen` will tell the agents for the same settings. That is the model card's window for `QWEN_PRESET` (or `QWEN_CONTEXT`), or less when it does not fit in the memory the GPU may use: 247,319 tokens for KAT-Coder on a 24 GiB Mac. So `./bin/integrate install` needs the model downloaded first. If you start the server with other settings, run `./bin/integrate install` again with the same ones.

## The launchers

`./bin/opencode-qwen`, `./bin/pi-qwen` and `./bin/codex-qwen` start each agent with the same provider for one run, without writing to your configs. Run them from any folder; arguments go straight to the agent. They tell the agent the context window the running server can serve, which `./bin/serve-qwen` records in `state/served/`. They stop with an explanation when no server started by `./bin/serve-qwen` is running.

- `./bin/opencode-qwen` passes the config through `OPENCODE_CONFIG_CONTENT` and adds `--standalone`, because OpenCode's shared background service never sees the launcher's environment.
- `./bin/pi-qwen` gives Pi its own agent folder, `state/pi-agent`, through `PI_CODING_AGENT_DIR`; sessions from these runs live there too.
- `./bin/codex-qwen` passes the provider with `-c` overrides, so `~/.codex/config.toml` is not changed. For a one-off task, `./bin/codex-qwen exec "..."` works as `codex exec` does.

## The provider, for setting it up by hand

`./bin/integrate` and the launchers write these entries, here with the default settings. Two settings matter for every agent.

### The model name is always default_model

`mlx_lm.server` treats any other model name as a model to load, and would swap out the one in memory ([`server.py`](https://github.com/ml-explore/mlx-lm/blob/93e6d4a6447b3080861985947f0820e76fcb957d/mlx_lm/server.py#L388-L395)). The server's guard refuses such requests, and `/v1/models` lists only `default_model`. The agents show their own label for it, such as "Qwen (local MLX)", whichever model is served.

### Thinking text arrives in a reasoning field

mlx-lm returns the model's thinking in a `reasoning` field ([`server.py`](https://github.com/ml-explore/mlx-lm/blob/93e6d4a6447b3080861985947f0820e76fcb957d/mlx_lm/server.py#L1511)), so OpenCode and Pi are told to read it there. Codex receives it as reasoning items.

### OpenCode

The entry goes in OpenCode's [global config file](https://github.com/anomalyco/opencode/blob/ba546ec1598a70db2570e9c6258028d955ba8f58/packages/web/src/content/docs/config.mdx). These are the OpenCode v2 key names (`providers`, `package`, `settings`, `modelID`). They are defined in its [provider schema](https://github.com/anomalyco/opencode/blob/ba546ec1598a70db2570e9c6258028d955ba8f58/packages/schema/src/config/provider.ts) and [model compatibility schema](https://github.com/anomalyco/opencode/blob/ba546ec1598a70db2570e9c6258028d955ba8f58/packages/schema/src/model-compatibility.ts) at v2.1.1; the v2.1.1 docs still show the v1 names.

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

### Pi

The entry goes in `models.json` in Pi's agent folder ([Pi's model docs](https://github.com/earendil-works/pi/blob/71023aec1546d6d677a65669c5665c0927ce0dd0/packages/coding-agent/docs/models.md), [environment variables](https://github.com/earendil-works/pi/blob/71023aec1546d6d677a65669c5665c0927ce0dd0/packages/coding-agent/docs/environment-variables.md)).

- The `apiKey` is a placeholder: Pi hides models without credentials, and the server ignores the key.
- `thinkingFormat` lets Pi switch Qwen's thinking on and off.
- It and the other `compat` settings are defined in Pi's [compat schema](https://github.com/earendil-works/pi/blob/71023aec1546d6d677a65669c5665c0927ce0dd0/packages/ai/src/providers/compat-schema.ts#L195-L264).

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

### Codex

The entry is a profile, `puny.config.toml` in `~/.codex`, that Codex layers over your usual config when you run `codex -p puny`.

- `wire_api = "responses"` is the only protocol Codex speaks; the server answers it at the same address as the other agents.
- The provider id must not be a built-in one such as `openai`, `ollama` or `lmstudio`, because Codex ignores user entries that reuse those ids ([`lib.rs` at 0.163.0](https://github.com/openai/codex/blob/83086188ae0614bd882818ff1aa23f3194962012/codex-rs/model-provider-info/src/lib.rs#L694-L699)).

```toml
# Added by puny-local-ai. Remove it with: ./bin/integrate uninstall codex
model_provider = "puny"
model = "default_model"
model_context_window = 262144

[model_providers.puny]
name = "puny-local-ai"
base_url = "http://127.0.0.1:8081/v1"
wire_api = "responses"
```
