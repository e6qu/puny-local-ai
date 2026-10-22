# Contributing

Thanks for helping. This file covers how the repo is laid out, the rules every change follows, and what to run before you send one.

## Getting set up

Follow the [bootstrap in the README](README.md#bootstrap). For work that does not touch the default model, the small model is enough and saves memory:

```sh
./bin/setup mlx small opencode pi
QWEN_PRESET=small ./bin/serve-qwen
```

You also need [ShellCheck](https://www.shellcheck.net) (`brew install shellcheck`).

## Layout

- `bin/`: the commands people run. Each one starts by sourcing `lib/common.sh`.
- `lib/common.sh`: settings shared by every script, `die`, and the server checks.
- `lib/mlx.sh`: the model presets, the mlx-lm environment and model downloads.
- `lib/agents.sh`: the pinned OpenCode and Pi versions and how to run them.
- `lib/mlx-lm.lock`: mlx-lm and its dependencies, with hashes.
- `lib/serve.py`: runs mlx_lm.server behind the memory and disk guards. It hooks into mlx-lm's server internals and refuses to run with any mlx-lm version other than the pinned one, so check it again when moving the pin.
- `lib/memory.py`: the memory estimates and their measured constants. Measure again before changing a constant, and record how in its docstring.
- `lib/verify_model.py`: the model hash check.
- `lib/integrate.py` and `lib/jsonc.py`: add the provider to the agents' own configs and remove it again, editing JSON and JSONC in place without touching anything else. `lib/integrate.py` also generates the config the launchers pass, so both always agree.
- `tests/`: the test suite; see [Tests](#tests).
- `bench/`: the benchmark tasks. Each has `start/` (the project the agent gets), `hidden/` (tests it never sees), `prompt.txt` and an optional `check` script.
- `docs/`: background that does not belong in the README.
- `state/`: everything downloaded or produced at run time. It is ignored by git; delete it to start over.

## Rules

- **Download, do not vendor.** Third-party code, models and data are downloaded at run time, never committed. The exception is code we fork and change; it then keeps its license and is listed in [THIRD_PARTY.md](THIRD_PARTY.md) with its source, commit and changes.
- **Pin everything and check it.** Models are pinned to a full 40-character commit hash, Python packages to exact versions with hashes, and npm packages to exact versions. Never pin to a branch or a range.
- **Wait 24 hours before adopting a release.** `./bin/install-agents` enforces this for OpenCode and Pi. When updating the lock, pass `--exclude-newer` as shown in its header. A release that fixes a reported security advisory may skip the wait; name the advisory in the change.
- **Fail loudly.** Scripts stop with `die` and a message that says what went wrong and how to fix it. No silent fallbacks, guessed defaults or ignored errors.
- **Never mix up bytes and tokens.** Every quantity says its unit: names end in `_bytes`, `_tokens`, `_pages` or `_layers`, settings say theirs in the docs, and messages print GiB or tokens. A context window is tokens; what it costs in memory is bytes, worked out in `lib/memory.py` and nowhere else.
- **Never let the machine run out of memory or disk.** A change that can use more memory, such as a new model, a larger step or a new server flag, must keep the estimates in `lib/memory.py` at or above what it measures.
- **Leave the user's own setup alone.** Only `./bin/integrate` writes to the agents' configs, only when asked, and only through `lib/integrate.py`, which backs files up, adds one entry, removes exactly that entry again, and refuses anything it did not add. Apart from that and the global npm installs that `./bin/install-agents` performs, nothing may write outside this repo and the system temp folder; uv's and Hugging Face's caches are pointed into `state/`. Nothing may need `sudo`.
- **Run on a fresh Mac.** Scripts run with the bash and Python that macOS ships: bash 3.2 and, from Apple's command line tools, Python 3.9. So no `declare -A`, no `${x,,}`, no empty `"${array[@]}"` under `set -u`, and nothing newer than Python 3.9 in code run by `python3`, such as `datetime.UTC`. Code that runs in the mlx-lm environment (`lib/serve.py`, `lib/memory.py`, `lib/verify_model.py`) may use Python 3.14. The tests run every script with `/bin/bash` and `/usr/bin/python3` on the PATH, so they catch this.
- **OpenCode 2 only.** Configs use the OpenCode 2 format; check the version with `require_opencode_2` before relying on it.
- **Test every change.** New behavior comes with tests, and a fixed bug with a test that fails without the fix.
- **Source every claim.** Facts about mlx-lm, models and agents link to the exact commit or version they come from, as the existing docs do. Measurements say when and how they were taken.
- **Write plainly.** Full sentences, active voice, no invented shorthand. Lists of three or more items are bullet points.

## Changing pins

- **mlx-lm or its dependencies:** regenerate `lib/mlx-lm.lock` with the command in its header and put the header back. The next `./bin/setup mlx` reinstalls `state/mlx-env`.
- **OpenCode or Pi:** change the version in `lib/agents.sh`, then run `./bin/setup opencode pi`.
- **A model preset:** change it in `lib/mlx.sh`, including `preset_upstream`, the model it was converted from. Run `./bin/setup` for that preset; it fails unless every file matches Hugging Face's hashes for the commit.

After any pin change, run `./bin/licenses` and replace the tables in THIRD_PARTY.md with its output. Update any docs that name the old version.

## Tests

```sh
./tests/run              # fast tests, under two minutes: no network, no model, no real agents
./tests/run --live       # adds the live tests, about six minutes
./tests/run integrate    # only tests whose name contains "integrate"
```

Every test runs the scripts from a temporary copy of the repo, with its own `state/` and its own home folder, so tests never touch your agent configs, downloads or `~/`. They run in the mlx-lm environment, which `./tests/run` installs if needed.

- **Fast tests** cover every script and library. Stub programs stand in for `npm`, `node`, `df`, `uv`, `opencode`, `pi` and `codex`; a fake mlx-lm environment and fake model folders stand in for the real ones; a local fake stands in for `mlx_lm.server` and for Hugging Face, which is otherwise blocked; and the memory readings are replaced where a test needs a full or empty machine. They never download anything or touch the real `state/`. The benchmark tests apply reference solutions from `tests/fixtures/solutions/` to show that each task can be solved and that its hidden tests catch the unsolved start.
- **Live tests** need `./bin/setup mlx small opencode pi`, Codex (`brew install codex`) for the integration test, network access and about 7 GiB of free memory. They start the guarded server with the small model and run the checks and both real agents against it; run `./bin/integrate` against the real OpenCode, Pi and Codex; start a private OpenCode background service on its own port to check the restart notice; and check that every pinned model commit still exists and that `./bin/licenses` still matches THIRD_PARTY.md.

## Before sending a change

1. `shellcheck bin/* lib/*.sh bench/*/check ./tests/run` reports nothing. The settings are in `.shellcheckrc`.
2. `./tests/run --live` passes.
3. If the change could affect how well agents do their work, for example a new model, a sampling change or a server flag, run the benchmark before and after and include both reports:

   ```sh
   BENCH_RUNS=2 ./bin/bench-agents before
   # make the change, restart the server
   BENCH_RUNS=2 ./bin/bench-agents after
   ./bin/bench-agents report
   ```

4. The README and `docs/` match the change.

Keep one change per pull request. Pull requests are squash-merged into a single commit with a one-line message, so the branch history does not need tidying.

## License

By contributing, you agree that your contribution is licensed under the [MIT license](LICENSE) of this repo.
