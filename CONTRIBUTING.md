# Contributing

How the repo is laid out, the rules every change follows, and what to run before sending one.

## Getting set up

Follow the [README's bootstrap](README.md#bootstrap). Unless the work concerns the default model, the small one is enough:

```sh
./bin/setup mlx small opencode pi
QWEN_PRESET=small ./bin/serve-qwen
```

You also need [ShellCheck](https://www.shellcheck.net): `brew install shellcheck`.

## Layout

- `bin/`: the commands people run. Each one starts by sourcing `lib/common.sh`.
- `lib/common.sh`: settings shared by every script, including reading `puny.conf`, `die`, and the server checks.
- `lib/config.py`: writes `puny.conf` for `./bin/config` and the web UI, and holds each setting's help. A new setting goes in both its `SETTINGS` and `CONFIG_SETTINGS` in `lib/common.sh`.
- `lib/mlx.sh`: the model presets, the mlx-lm environment and model downloads.
- `lib/agents.sh`: the pinned OpenCode and Pi versions, and `run_agent`, which the smoke test, the benchmark and the workflows use to run an agent once.
- `lib/mlx-lm.lock`: mlx-lm and its dependencies, with hashes.
- `lib/serve.py`: runs mlx_lm.server behind the memory and disk guards, and adds the Responses API and its own `/v1/models`. It hooks into mlx-lm's server internals and refuses to run with any mlx-lm version other than the pinned one, so check it again when moving the pin.
- `lib/responses.py`: translates the Responses API that Codex speaks into Chat Completions and back; [docs/how-it-works.md](docs/how-it-works.md#how-codex-is-served) describes it.
- `lib/memory.py`: the memory estimates and their measured constants. Measure again before changing a constant, and record how in its docstring.
- `lib/sources.py`: Hugging Face and ModelScope: search, commits, file lists with hashes, and ModelScope downloads.
- `lib/verify_model.py`: the model hash check, for either source.
- `lib/verify.py`: sums up the server log for `./bin/verify`'s report: each request's memory peak against its estimate, refusals and watchdog stops. It reads the lines `lib/serve.py` logs, so change both together.
- `lib/models.py`: `./bin/models search`, `info` and `list`, including the memory estimate for a model that is not downloaded.
- `lib/workflow.py`: the workflow engine behind `./bin/workflow`: it checks a graph and runs it ([docs/workflows.md](docs/workflows.md)).
- `lib/evals.py`: `./bin/evals`.
- `ui/`: the web UI, a Go program that uses only the standard library. `main.go` describes it; `handlers.go` holds the pages and actions, `server.go` the model server control, `jobs.go` the jobs, `templates/` the HTML (one file per page in `pages/`, the shared parts in `partials.html`), and `static/` the stylesheet and the one script. Pages may not use inline scripts or styles: the Content-Security-Policy forbids them. Every action goes through a `bin/` command, never around it.
- `lib/webui.sh`: the pinned Go and HTMX, and how `ui/` is built into `state/web-ui/`.
- `lib/integrate.py` and `lib/jsonc.py`: add the provider to the agents' own configs and remove it again, editing JSON and JSONC in place without touching anything else. `lib/integrate.py` also generates the config the launchers pass, so both always agree.
- `tests/`: the test suite; see [Tests](#tests).
- `bench/`: the benchmark tasks. Each has `start/` (the project the agent gets), `hidden/` (tests it never sees), `prompt.txt` and an optional `check` script.
- `evals/`: the cases `./bin/evals` (`lib/evals.py`) asks the model directly: `tool-calls/*.json` conversations, each expected step with an `example` that passes it, and `coding/NAME/` problems with `prompt.md` and hidden tests. Every coding problem and bench task has a reference solution under `tests/fixtures/`, and the tests check that it passes and that the unsolved start fails.
- `workflows/NAME/`: a workflow's `graph.jsonc`, its prompts and an `example/` folder. `workflows/team-solve/` is adapted from Memdoor under Apache-2.0: keep its `LICENSE`, `NOTICE` and `CHANGES.md`, and record every further change in `CHANGES.md`.
- `docs/`: background the README links to. Keep the README to what users do; implementation details go in [docs/how-it-works.md](docs/how-it-works.md).
- `state/`: everything downloaded or produced at run time. It is ignored by git; delete it to start over.
- `puny.conf`: the user's settings, also ignored by git. The tests never read the real one: each runs in a copy of the repo.

## Rules

- **Download, do not vendor.** Third-party code, models and data are downloaded at run time, never committed. The exception is code we fork and change; it then keeps its license and is listed in [THIRD_PARTY.md](THIRD_PARTY.md) with its source, commit and changes.
- **Pin everything and check it.** Models are pinned to a full 40-character commit hash, Python packages to exact versions with hashes, and npm packages to exact versions. Never pin to a branch or a range.
- **Wait 24 hours before adopting a release.** `./bin/install-agents` enforces this for OpenCode and Pi. When updating the lock, pass `--exclude-newer` as shown in its header. A release that fixes a reported security advisory may skip the wait; name the advisory in the change.
- **Fail loudly.** Scripts stop with `die` and a message that says what went wrong and how to fix it. No silent fallbacks, guessed defaults or ignored errors.
- **Never mix up bytes and tokens.** Every quantity says its unit: names end in `_bytes`, `_tokens`, `_pages` or `_layers`, settings say theirs in the docs, and messages print GiB or tokens. A context window is tokens; what it costs in memory is bytes, worked out in `lib/memory.py` and nowhere else.
- **Never let the machine run out of memory or disk.** A change that can use more memory, such as a new model, a larger step or a new server flag, must keep the estimates in `lib/memory.py` at or above what it measures.
- **Leave the user's own setup alone.** Only `./bin/integrate` writes to the agents' configs, only when asked, and only through `lib/integrate.py`, which backs files up, adds one entry, removes exactly that entry again, and refuses anything it did not add. Apart from that and the global npm installs that `./bin/install-agents` performs, nothing may write outside this repo and the system temp folder. Inside the repo, only `./bin/config` and the web UI, through it, write `puny.conf`; uv's and Hugging Face's caches are pointed into `state/`. Nothing may need `sudo`.
- **Run on a fresh Mac.** Scripts run with the bash and Python that macOS ships: bash 3.2 and, from Apple's command line tools, Python 3.9. So no `declare -A`, no `${x,,}`, no empty `"${array[@]}"` under `set -u`, and nothing newer than Python 3.9 in code run by `python3`, such as `datetime.UTC`. Code that runs in the mlx-lm environment (`lib/serve.py`, `lib/memory.py`, `lib/verify_model.py`) may use Python 3.14. The tests run every script with `/bin/bash` and `/usr/bin/python3` on the PATH, so they catch this.
- **OpenCode 2 only.** Configs use the OpenCode 2 format; check the version with `require_opencode_2` before relying on it.
- **Test every change.** New behavior comes with tests, and a fixed bug with a test that fails without the fix.
- **Source every claim.** Facts about mlx-lm, models and agents link to the exact commit or version they come from, as the existing docs do. Measurements say when and how they were taken.
- **Write plainly and briefly.** Full sentences, active voice, no invented shorthand, nothing a reader does not need. Lists of three or more items are bullet points. Commands in docs start with `./`.

## Changing pins

- **mlx-lm or its dependencies:** regenerate `lib/mlx-lm.lock` with the command in its header and put the header back. The next `./bin/setup mlx` reinstalls `state/mlx-env`.
- **OpenCode or Pi:** change the version in `lib/agents.sh`, then run `./bin/setup opencode pi`.
- **Go or HTMX:** change the version and hashes in `lib/webui.sh`, taking Go's SHA-256 from https://go.dev/dl/?mode=json and HTMX's from the `integrity` field of https://registry.npmjs.org/htmx.org/VERSION, and the file's SHA-256 from the package, compared against a CDN copy. Raise `go` in `ui/go.mod` with Go's minor version. Then run `./bin/setup web-ui` and `./tests/run`.
- **A model preset:** change it in `lib/mlx.sh`, including `preset_upstream`, the model it was converted from. Run `./bin/setup` for that preset; it fails unless every file matches Hugging Face's hashes for the commit.

After any pin change, run `./bin/licenses` and replace the tables in THIRD_PARTY.md with its output. Update any docs that name the old version.

## Tests

```sh
./tests/run              # fast tests, about five minutes: no network, no model, no real agents
./tests/run --live       # adds the live tests against the small model; allow two hours
./tests/run integrate    # only tests whose name contains "integrate"
```

Every test runs the scripts from a temporary copy of the repo with its own `state/` and home folder, so it never touches your configs, downloads or `~/`. `./tests/run` also builds the web UI, runs `go vet`, `gofmt` and the Go tests in `ui/`, and drives the built program over HTTP in `tests/test_webui.py`. For the Go tests alone, in bash: `source lib/common.sh; source lib/webui.sh; go_in_ui test ./...`.

- **Fast tests** cover every script and library. Stubs stand in for `npm`, `node`, `df`, `uv` and the agents (the workflow tests use one that runs the `STUB:` lines of its prompt); fakes stand in for the mlx-lm environment, the models, `mlx_lm.server`, Hugging Face and ModelScope. Reference solutions in `tests/fixtures/solutions/` show that each benchmark task can be solved and that its hidden tests catch the unsolved start.
- **Live tests** need `./bin/setup mlx small opencode pi`, Codex, network access and about 7 GiB of free memory. They run the real server, agents and workflows with the small model, `./bin/integrate` against the real agents, and check that every pinned model commit still exists and that `./bin/licenses` matches THIRD_PARTY.md.

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

4. The README and `docs/` match the change, and every new doc is linked from the README's "More documentation".

One change per pull request; each lands as one commit with a one-line message.

## License

By contributing, you agree that your contribution is licensed under the [MIT license](LICENSE) of this repo.
