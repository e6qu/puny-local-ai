"""./bin/setup, ./bin/install-agents, ./bin/serve-qwen, lib/common.sh, lib/mlx.sh, ./bin/licenses."""

import datetime
import subprocess
import unittest

from helpers import REPO, FakeServer, Sandbox, free_port, run_in_terminal

PRESETS = {
    "kat": ("mlx-community/KAT-Coder-V2.5-Dev-OptiQ-4bit-REAP-18B", "b33dfde62c2753841aa2b1662529bf7f3b1fdde5"),
    "reap": ("mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit-REAP-19B", "95193a560399d81cd6272c393db8e96d59f54f50"),
    "small": ("mlx-community/Qwen3.5-4B-4bit", "0e7ffd5c629ef7719d4cbc04069232580bfa9d9c"),
}

# df output with 1 MiB free, for the disk checks.
FULL_DISK_DF = 'echo "Filesystem 1024-blocks Used Available Capacity Mounted on"; echo "fs 1 1 1024 1% /"'


def bash(box, script, extra_env=None):
    """Runs a snippet with lib/common.sh and lib/mlx.sh sourced from the sandbox."""
    return subprocess.run(
        ["/bin/bash", "-c", f'source "{box.root}/lib/common.sh"; source "{box.root}/lib/mlx.sh"; {script}'],
        env=box.env(extra_env), capture_output=True, text=True, timeout=60,
    )


class CommonTest(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)

    def test_defaults(self):
        out = bash(self.box, 'echo "$MLX_BASE_URL|$MLX_HOST|$MLX_PORT|$MLX_MODEL|$QWEN_MAX_OUTPUT|$QWEN_CONTEXT|$(served_record)"')
        self.assertEqual(out.stdout.strip(), f"http://127.0.0.1:8081/v1|127.0.0.1|8081|default_model|8192||{self.box.root}/state/served/port-8081")

    def test_base_url_forms(self):
        good = {
            "http://localhost:9000/v1/": ("localhost", "9000"),
            "http://[::1]:8081/v1": ("::1", "8081"),
            "http://127.0.0.1:08081/v1": ("127.0.0.1", "8081"),
        }
        for url, (host, port) in good.items():
            with self.subTest(url):
                out = bash(self.box, 'echo "$MLX_HOST $MLX_PORT"', {"MLX_BASE_URL": url})
                self.assertEqual(out.stdout.split(), [host, port], out.stderr)
        bad = {
            "https://127.0.0.1:8081/v1": "MLX_BASE_URL must look like",
            "http://127.0.0.1/v1": "MLX_BASE_URL must look like",
            "http://127.0.0.1:8081/v1?x=1": "MLX_BASE_URL must look like",
            "http://127.0.0.1:8081/v2": "MLX_BASE_URL must look like",
            "http://127.0.0.1:99999/v1": "between 1 and 65535",
            "http://127.0.0.1:0/v1": "between 1 and 65535",
        }
        for url, message in bad.items():
            with self.subTest(url):
                out = bash(self.box, "true", {"MLX_BASE_URL": url})
                self.assertNotEqual(out.returncode, 0)
                self.assertIn(message, out.stderr)

    def test_settings_are_validated(self):
        for name, value, message in (
            ("QWEN_CONTEXT", "0", "QWEN_CONTEXT"),
            ("QWEN_CONTEXT", "1e5", "QWEN_CONTEXT"),
            ("QWEN_MAX_OUTPUT", "-1", "QWEN_MAX_OUTPUT"),
            ("MLX_MODEL", "other", "MLX_MODEL is not a setting"),
        ):
            with self.subTest(name=name, value=value):
                out = bash(self.box, "true", {name: value})
                self.assertNotEqual(out.returncode, 0)
                self.assertIn(message, out.stderr)
        self.assertEqual(bash(self.box, "true", {"MLX_MODEL": "default_model"}).returncode, 0)

    def test_require_free_disk(self):
        self.box.stub("df", FULL_DISK_DF)
        self.assertEqual(bash(self.box, f'require_free_disk 0 "{self.box.dir}" x').returncode, 0)
        out = bash(self.box, f'require_free_disk 1 "{self.box.dir}" "The thing"')
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("The thing needs 1 GiB of free disk, and only 0 GiB is free", out.stderr)
        self.box.stub("df", "echo garbage")
        self.assertIn("cannot read the free space", bash(self.box, f'require_free_disk 1 "{self.box.dir}" x').stderr)

    def test_require_command(self):
        out = bash(self.box, 'require_command no-such-tool "Install it somehow."')
        self.assertIn("'no-such-tool' is not installed. Install it somehow.", out.stderr)

    def test_damaged_server_record(self):
        server = FakeServer({"GET /health": (200, {"status": "ok"})})
        self.addCleanup(server.stop)
        record = self.box.root / "state" / "served" / f"port-{server.port}"
        record.parent.mkdir(parents=True)
        for content in ("", "123\n", "abc 4096\n"):
            with self.subTest(content=content):
                record.write_text(content)
                out = bash(self.box, "load_served_context", {"MLX_BASE_URL": server.base_url})
                self.assertIn("is damaged", out.stderr)

    def test_missing_lsof_is_reported(self):
        out = subprocess.run(
            ["/bin/bash", "-c", f'source "{self.box.root}/lib/common.sh"; PATH=/usr/bin:/bin; listener_pid 8081'],
            env=self.box.env(), capture_output=True, text=True,
        )
        self.assertIn("'lsof' is not installed", out.stderr)


class MlxLibTest(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)

    def test_presets_are_pinned_to_full_commits(self):
        for preset, (repo, revision) in PRESETS.items():
            with self.subTest(preset):
                out = bash(self.box, f'preset_info {preset}; echo "$preset_repo $preset_revision $preset_context $preset_temp $preset_upstream"')
                repo_out, rev, context, temp, upstream = out.stdout.split()
                self.assertEqual((repo_out, rev, context), (repo, revision, "262144"))
                self.assertEqual(temp, "0.8" if preset == "kat" else "0.6")
                self.assertRegex(upstream, r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")

    def test_unknown_preset(self):
        self.assertIn("unknown model preset 'huge'", bash(self.box, "preset_info huge").stderr)

    def test_caches_stay_in_state(self):
        out = bash(self.box, 'echo "$UV_CACHE_DIR|$UV_PYTHON_INSTALL_DIR|$HF_HOME|$PYTHON_VERSION"')
        state = self.box.root / "state"
        self.assertEqual(out.stdout.strip(), f"{state}/cache/uv|{state}/python|{state}/cache/huggingface|3.14.6")

    def test_model_dir(self):
        out = bash(self.box, "model_dir_for org/name 0123")
        self.assertEqual(out.stdout, f"{self.box.root}/state/models/org--name@0123")

    def test_mlx_env_needs_apple_silicon(self):
        self.box.stub("uname", 'echo "Darwin x86_64"')
        self.assertIn("needs a Mac with Apple Silicon; this is Darwin x86_64", bash(self.box, "ensure_mlx_env").stderr)

    def test_mlx_env_ready_follows_the_lock_and_python(self):
        self.box.fake_mlx_env()
        self.assertEqual(bash(self.box, "mlx_env_ready").returncode, 0)
        self.assertNotEqual(bash(self.box, "PYTHON_VERSION=3.14.7; mlx_env_ready").returncode, 0)
        lock = self.box.root / "lib" / "mlx-lm.lock"
        lock.write_text(lock.read_text() + "\n# changed\n")
        self.assertNotEqual(bash(self.box, "mlx_env_ready").returncode, 0)

    def test_mlx_env_install(self):
        self.box.stub("uv", 'mkdir -p "${@: -1}/bin" 2>/dev/null; exit 0')
        out = bash(self.box, "ensure_mlx_env")
        self.assertEqual(out.returncode, 0, out.stderr)
        root = self.box.root
        self.assertEqual(self.box.called("uv"), [
            f"venv -q --python 3.14.6 --python-preference only-managed {root}/state/mlx-env",
            f"pip install -q --python {root}/state/mlx-env/bin/python --require-hashes -r {root}/lib/mlx-lm.lock",
        ])
        self.assertEqual(bash(self.box, "mlx_env_ready").returncode, 0)

    def test_failed_mlx_env_install_is_not_marked_ready(self):
        self.box.stub("uv", '[[ "$1" == pip ]] && exit 1; mkdir -p "${@: -1}/bin"; exit 0')
        self.assertNotEqual(bash(self.box, "ensure_mlx_env").returncode, 0)
        self.assertFalse((self.box.root / "state" / "mlx-env" / ".installed").exists())

    def test_mlx_env_install_needs_uv_and_disk(self):
        self.assertIn("brew install uv", bash(self.box, "ensure_mlx_env").stderr)
        self.box.stub("uv", "exit 0")
        self.box.stub("df", FULL_DISK_DF)
        out = bash(self.box, "ensure_mlx_env")
        self.assertIn("Installing mlx-lm needs 6 GiB of free disk", out.stderr)
        self.assertEqual(self.box.called("uv"), [])

    def test_mlx_env_is_not_replaced_under_a_running_server(self):
        self.box.stub("uv", "exit 0")
        sleeper = subprocess.Popen(["/bin/bash", "-c", f"exec -a 'python {self.box.root}/lib/serve.py' sleep 30"])
        self.addCleanup(sleeper.wait)
        self.addCleanup(sleeper.kill)
        record = self.box.root / "state" / "served" / "port-8081"
        record.parent.mkdir(parents=True)
        record.write_text(f"{sleeper.pid} 262144\n")
        out = bash(self.box, "ensure_mlx_env")
        self.assertIn(f"(pid {sleeper.pid}) still runs", out.stderr)
        self.assertEqual(self.box.called("uv"), [])

    def test_ensure_model(self):
        self.box.fake_mlx_env()
        repo, revision = "org/model", "1" * 40
        folder = self.box.root / "state" / "models" / f"org--model@{revision}"
        out = bash(self.box, f'ensure_model {repo} {revision}; echo "$model_dir"')
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.splitlines()[-1], str(folder))
        calls = self.box.called("python")
        self.assertEqual(len(calls), 2, calls)
        self.assertTrue(calls[0].startswith("-I -c"))
        self.assertIn(f"verify_model.py {repo} {revision} {folder}", calls[1])
        self.assertTrue((folder / ".verified").exists())
        # A verified model is not downloaded again.
        bash(self.box, f"ensure_model {repo} {revision}")
        self.assertEqual(len(self.box.called("python")), 2)

    def test_ensure_model_failures(self):
        self.box.fake_mlx_env()
        cases = [
            ({"STUB_DOWNLOAD": "1"}, None, 1),
            ({"STUB_VERIFY": "1"}, "does not match org/model", 2),
            ({"STUB_VERIFY": "2"}, "could not verify", 2),
        ]
        for i, (env, message, calls) in enumerate(cases):
            with self.subTest(env=env):
                (self.box.calls / "python").unlink(missing_ok=True)
                out = bash(self.box, f"ensure_model org/model {str(i) * 40}", env)
                self.assertNotEqual(out.returncode, 0)
                if message:
                    self.assertIn(message, out.stderr)
                self.assertEqual(len(self.box.called("python")), calls)
        self.assertIn("full 40-character commit hash", bash(self.box, "ensure_model org/name main").stderr)

    def test_the_lock_pins_everything_with_hashes(self):
        blocks, current = [], None
        for line in (REPO / "lib" / "mlx-lm.lock").read_text().splitlines():
            if not line or line.startswith("#"):
                continue
            if not line.startswith(" "):
                current = [line]
                blocks.append(current)
            else:
                current.append(line)
        self.assertGreater(len(blocks), 20)
        for block in blocks:
            with self.subTest(block[0]):
                self.assertRegex(block[0], r"^[A-Za-z0-9_.-]+==[\w.]+ \\$")
                self.assertTrue(any("--hash=sha256:" in line for line in block[1:]))
        self.assertIn("mlx-lm==0.32.2 \\", [b[0] for b in blocks])


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)
        self.box.fake_mlx_env()
        for repo, revision in PRESETS.values():
            self.box.fake_model(repo, revision)
        # install-agents is tested on its own below.
        stub = self.box.root / "bin" / "install-agents"
        stub.write_text('#!/bin/bash\necho "install-agents $*" >> "$STUB_CALLS/install-agents"\n[[ "$1" != "${FAIL_AGENT:-}" ]]\n')
        stub.chmod(0o755)
        for agent in ("opencode", "pi"):
            self.box.stub(agent, 'echo "v1.0.0"')

    def setup_cmd(self, *args, extra=None, tty_input=None):
        if tty_input is None:
            return self.box.run(["bin/setup", *args], extra_env=extra)
        status, output = run_in_terminal(
            [str(self.box.root / "bin" / "setup"), *args], self.box.env(extra), self.box.work,
            tty_input, "Numbers separated by spaces",
        )
        return subprocess.CompletedProcess([], status, output, output)

    def test_named_items(self):
        result = self.setup_cmd("mlx", "opencode", "pi")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.box.called("install-agents"), ["install-agents opencode", "install-agents pi"])
        self.assertRegex(result.stdout, r"mlx\s+installed")
        self.assertRegex(result.stdout, r"opencode\s+installed 1.0.0")

    def test_model_items(self):
        repo, revision = PRESETS["small"]
        (self.box.root / "state" / "models" / f"{repo.replace('/', '--')}@{revision}" / ".verified").unlink()
        self.assertRegex(self.setup_cmd("pi").stdout, r"small\s+not downloaded")
        result = self.setup_cmd("small")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"small\s+downloaded and verified")
        self.assertIn(f"verify_model.py {repo} {revision}", "\n".join(self.box.called("python")))

    def test_a_failed_item_does_not_stop_the_others(self):
        result = self.setup_cmd("opencode", "pi", extra={"FAIL_AGENT": "opencode"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed: opencode", result.stderr)
        self.assertEqual(self.box.called("install-agents"), ["install-agents opencode", "install-agents pi"])

    def test_refuses_unknown_items_before_installing_anything(self):
        result = self.setup_cmd("pi", "claude")
        self.assertIn("unknown item 'claude'", result.stderr)
        self.assertEqual(self.box.called("install-agents"), [])

    def test_no_terminal_no_menu(self):
        self.assertIn("no items given and no terminal for the menu", self.setup_cmd().stderr)

    def test_one_needs_an_item(self):
        self.assertIn("usage:", self.setup_cmd("--one").stderr)

    def test_menu(self):
        result = self.setup_cmd(tty_input="6\n")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("What should be installed?", result.stdout)
        self.assertIn("6) pi", result.stdout)
        self.assertEqual(self.box.called("install-agents"), ["install-agents pi"])

    def test_menu_defaults(self):
        result = self.setup_cmd(tty_input="\n")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("[1 2 5 6]", result.stdout)
        self.assertIn("== kat", result.stdout)
        self.assertEqual(self.box.called("install-agents"), ["install-agents opencode", "install-agents pi"])
        self.assertEqual(self.box.called("python"), [], "kat is already verified, so nothing is downloaded")

    def test_menu_rejects_bad_numbers(self):
        for answer in ("9", "0", "08", "x"):
            with self.subTest(answer):
                result = self.setup_cmd(tty_input=answer + "\n")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"'{answer}' is not a number from 1 to 6", result.stdout)
                self.assertNotIn("value too great", result.stdout)


NPM_STUB = r'''
case "$1 $2" in
  "root -g") echo "$STUB_NPM_ROOT" ;;
  "view "*)
    [[ -n "${STUB_VIEW_FAIL:-}" ]] && exit 1
    if [[ "$3" == time ]]; then
      printf '{"%s": "%s"}\n' "${STUB_TIME_VERSION:-${2##*@}}" "$STUB_PUBLISHED"
    elif [[ "$3" == license ]]; then
      echo "${STUB_LICENSE-MIT}"
    elif [[ "$3" == repository.url ]]; then
      echo "${STUB_REPO-git+https://github.com/example/${2%@*}.git}"
    fi ;;
  "install -g")
    # Installing records the version where the agent stubs read it.
    spec="$3"
    case "$spec" in
      @opencode/cli@*) echo "${STUB_INSTALLS:-${spec##*@}}" > "$STUB_CALLS/opencode.version" ;;
      @earendil-works/pi-coding-agent@*) echo "${STUB_INSTALLS:-${spec##*@}}" > "$STUB_CALLS/pi.version" ;;
    esac ;;
  *) echo "unexpected npm $*" >&2; exit 2 ;;
esac
'''

# An agent that reports the version npm or its own upgrade last installed.
VERSIONED_AGENT = r'''
name="$(basename "$0")"
case "$1" in
  --version) cat "$STUB_CALLS/$name.version" 2>/dev/null || echo "0.0.0" ;;
  upgrade) [[ -n "${STUB_UPGRADE_FAIL:-}" ]] && exit 4; echo "$2" > "$STUB_CALLS/$name.version" ;;
esac
'''


class InstallAgentsTest(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)
        self.box.stub("npm", NPM_STUB)
        self.box.stub("node", 'echo "${STUB_NODE:-v22.19.0}"')
        self.box.stub("df", 'echo "$*" >> "$STUB_CALLS/df.args"; echo "Filesystem 1024-blocks Used Available Capacity Mounted on"; echo "fs 1 1 104857600 1% /"')
        old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=3)
        self.env = {"STUB_NPM_ROOT": str(self.box.dir / "npm-root"), "STUB_PUBLISHED": old.strftime("%Y-%m-%dT%H:%M:%S.000Z")}
        for name in ("opencode", "pi"):
            self.box.stub(name, VERSIONED_AGENT)

    def install(self, *args, extra=None):
        return self.box.run(["bin/install-agents", *args], extra_env=self.env | (extra or {}))

    def npm_installs(self):
        return [c for c in self.box.called("npm") if c.startswith("install")]

    def installed(self, name, version):
        (self.box.calls / f"{name}.version").write_text(version + "\n")

    def test_installs_the_pinned_version_from_npm(self):
        (self.box.stubs / "opencode").unlink()
        self.box.stub("opencode-later", "true")
        result = self.install("pi")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.npm_installs(), ["install -g @earendil-works/pi-coding-agent@1.3.0"])
        self.assertIn(self.env["STUB_NPM_ROOT"], (self.box.calls / "df.args").read_text())

    def test_full_install_summary(self):
        self.installed("opencode", "2.0.32")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("OpenCode: 2.1.1 (pinned 2.1.1)", result.stdout)
        self.assertIn("Pi:       1.3.0 (pinned 1.3.0)", result.stdout)

    def test_an_existing_opencode_upgrades_itself(self):
        self.installed("opencode", "2.0.32")
        result = self.install("opencode")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("upgrade 2.1.1", self.box.called("opencode"))
        self.assertEqual(self.npm_installs(), [])

    def test_the_installed_version_is_checked(self):
        self.box.stub("opencode", '[[ "$1" == --version ]] && echo "2.0.32"; exit 0')
        result = self.install("opencode")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("opencode reports version '2.0.32' after installing 2.1.1", result.stderr)
        result = self.install("pi", extra={"STUB_INSTALLS": "1.2.9"})
        self.assertIn("pi reports version '1.2.9' after installing 1.3.0", result.stderr)

    def test_skips_what_is_already_installed(self):
        self.installed("pi", "1.3.0")
        self.installed("opencode", "2.1.1")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Pi 1.3.0 is already installed.", result.stdout)
        self.assertIn("OpenCode 2.1.1 is already installed.", result.stdout)
        self.assertEqual(self.npm_installs(), [])
        self.assertNotIn("upgrade 2.1.1", self.box.called("opencode"))

    def test_refuses_releases_younger_than_a_day(self):
        fresh = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)
        stamp = fresh.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        result = self.install("pi", extra={"STUB_PUBLISHED": stamp})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("can be installed after", result.stderr)
        self.assertEqual(self.npm_installs(), [])
        result = self.install("pi", extra={"STUB_PUBLISHED": stamp, "ALLOW_FRESH_RELEASE": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_npm_problems(self):
        self.assertIn("cannot read the publish time of @earendil-works/pi-coding-agent@1.3.0", self.install("pi", extra={"STUB_VIEW_FAIL": "1"}).stderr)
        result = self.install("pi", extra={"STUB_TIME_VERSION": "9.9.9"})
        self.assertIn("npm lists no publish time for @earendil-works/pi-coding-agent@1.3.0", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_one_refusal_does_not_stop_the_other_agent(self):
        self.installed("opencode", "2.0.32")
        result = self.install(extra={"STUB_UPGRADE_FAIL": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("install -g @earendil-works/pi-coding-agent@1.3.0", self.npm_installs())

    def test_needs_a_recent_node(self):
        for version in ("v20.11.0", "v22.18.9"):
            with self.subTest(version):
                self.assertIn(f"Node {version} is too old", self.install(extra={"STUB_NODE": version}).stderr)
        self.assertEqual(self.install("pi", extra={"STUB_NODE": "v24.0.0"}).returncode, 0)
        self.assertIn("cannot read the Node version", self.install(extra={"STUB_NODE": "nightly"}).stderr)

    def test_needs_free_disk(self):
        self.box.stub("df", FULL_DISK_DF)
        result = self.install("pi")
        self.assertIn("Installing the coding agents needs 6 GiB of free disk", result.stderr)
        self.assertEqual(self.npm_installs(), [])

    def test_arguments(self):
        self.assertIn("unknown agent 'claude'", self.install("claude").stderr)
        self.assertIn("usage:", self.install("opencode", "pi").stderr)
        (self.box.stubs / "npm").unlink()
        self.assertIn("'npm' is not installed", self.install().stderr)


class ServeQwenTest(unittest.TestCase):
    """./bin/serve-qwen up to the point where it hands over to lib/serve.py."""

    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)
        self.box.fake_mlx_env()
        for repo, revision in PRESETS.values():
            self.box.fake_model(repo, revision)
        self.port = free_port()
        self.env = {"MLX_BASE_URL": f"http://127.0.0.1:{self.port}/v1"}

    def serve(self, extra=None):
        return self.box.run(["bin/serve-qwen"], extra_env=self.env | (extra or {}), timeout=120)

    def handed_over(self):
        return (self.box.calls / "serve.args").read_text().splitlines()

    def value(self, option):
        args = self.handed_over()
        return args[args.index(option) + 1]

    def test_hands_over_with_the_right_settings_and_records_itself(self):
        result = self.serve()
        self.assertEqual(result.returncode, 0, result.stderr)
        args = self.handed_over()
        repo, revision = PRESETS["kat"]
        state = self.box.root / "state"
        self.assertEqual(args[:args.index("--")], [
            "-I", str(self.box.root / "lib" / "serve.py"),
            "--model-dir", str(state / "models" / f"{repo.replace('/', '--')}@{revision}"),
            "--context", "262144", "--kv-bits", "8", "--cache-entries", "0",
            "--margin", "512M", "--max-prefill-step", "512", "--state-dir", str(state),
            "--record", str(state / "served" / f"port-{self.port}"),
            "--base-url", f"http://127.0.0.1:{self.port}/v1",
        ])
        self.assertEqual(args[args.index("--") + 1:], [
            "--host", "127.0.0.1", "--port", str(self.port), "--max-tokens", "8192",
            "--temp", "0.8", "--top-p", "0.95", "--top-k", "20", "--min-p", "0.0",
        ])
        self.assertIn("Starting mlx-community/KAT-Coder", result.stdout)

    def test_presets_and_overrides(self):
        self.serve({"QWEN_PRESET": "small", "QWEN_CONTEXT": "65536", "QWEN_KV_BITS": "4", "QWEN_TEMP": "0.2"})
        self.assertIn("Qwen3.5-4B-4bit", self.value("--model-dir"))
        self.assertEqual([self.value(o) for o in ("--context", "--kv-bits", "--temp")], ["65536", "4", "0.2"])
        self.serve({"QWEN_PRESET": "reap"})
        self.assertEqual(self.value("--temp"), "0.6")

    def test_any_model_at_a_commit(self):
        self.box.fake_model("org/model", "2" * 40)
        result = self.serve({"QWEN_REPO": "org/model", "QWEN_REVISION": "2" * 40, "QWEN_CONTEXT": "4096"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([self.value("--context"), self.value("--temp")], ["4096", "0.6"])

    def test_settings_are_validated(self):
        repo = {"QWEN_REPO": "org/model", "QWEN_REVISION": "0" * 40}
        cases = [
            ({"QWEN_TEMP": "warm"}, "QWEN_TEMP must be a number"),
            ({"QWEN_PREFILL_STEP": "8"}, "QWEN_PREFILL_STEP must be a whole number of at least 16"),
            ({"QWEN_PREFILL_STEP": "x"}, "QWEN_PREFILL_STEP must be a whole number of at least 16"),
            ({"QWEN_KV_BITS": "16"}, "QWEN_KV_BITS must be 4 or 8"),
            ({"QWEN_PROMPT_CACHE_ENTRIES": "two"}, "QWEN_PROMPT_CACHE_ENTRIES must be a whole number"),
            ({"QWEN_MEMORY_MARGIN": "lots"}, "QWEN_MEMORY_MARGIN must be a size"),
            ({"QWEN_PRESET": "huge"}, "unknown model preset"),
            ({"QWEN_REPO": "org/model"}, "set QWEN_REPO and QWEN_REVISION together"),
            (repo | {"QWEN_PRESET": "kat"}, "set either QWEN_PRESET or QWEN_REPO"),
            (repo, "set QWEN_CONTEXT with QWEN_REPO"),
            ({"QWEN_REPO": "org/model", "QWEN_REVISION": "main", "QWEN_CONTEXT": "4096"}, "full 40-character commit hash"),
            ({"MLX_BASE_URL": "http://127.0.0.1/v1"}, "MLX_BASE_URL must look like"),
        ]
        for extra, message in cases:
            with self.subTest(message):
                result = self.serve(extra)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertFalse((self.box.calls / "serve.args").exists(), "nothing may start")

    def test_refuses_a_busy_port(self):
        busy = FakeServer({})
        self.addCleanup(busy.stop)
        result = self.serve({"MLX_BASE_URL": busy.base_url})
        self.assertIn(f"port {busy.port} is already in use", result.stderr)
        self.assertFalse((self.box.calls / "serve.args").exists())


class LicensesTest(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox(link_mlx_env=True)
        self.addCleanup(self.box.cleanup)
        if not (REPO / "state" / "mlx-env" / ".installed").exists():
            self.skipTest("the mlx-lm environment is not installed; run ./bin/setup mlx")
        self.box.stub("npm", NPM_STUB)
        card = b"---\nlicense: apache-2.0\nlanguage: en\n---\n# Model\n"
        self.hf = FakeServer({})
        self.addCleanup(self.hf.stop)
        for preset in ("kat", "reap", "small"):
            out = bash(self.box, f'preset_info {preset}; echo "$preset_repo $preset_revision $preset_upstream"').stdout.split()
            for repo, rev in ((out[0], out[1]), tuple(out[2].split("@"))):
                self.hf.routes[f"GET /{repo}/raw/{rev}/README.md"] = (200, card)
        self.env = {"HF_ENDPOINT": f"http://127.0.0.1:{self.hf.port}"}

    def licenses(self, extra=None):
        return self.box.run(["bin/licenses"], extra_env=self.env | (extra or {}))

    def test_prints_every_table(self):
        result = self.licenses()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("| mlx-lm | 0.32.2 | MIT |", result.stdout)
        self.assertIn("| @opencode/cli | 2.1.1 | MIT | https://github.com/example/@opencode/cli.git |", result.stdout)
        self.assertRegex(result.stdout, r"\| kat \| \[mlx-community/KAT-Coder.*\| apache-2.0 \| .*\| apache-2.0 \|")

    def test_missing_card_and_missing_license_line(self):
        small_repo = PRESETS["small"]
        del self.hf.routes[f"GET /{small_repo[0]}/raw/{small_repo[1]}/README.md"]
        kat = PRESETS["kat"]
        self.hf.routes[f"GET /{kat[0]}/raw/{kat[1]}/README.md"] = (200, b"# No front matter\n")
        result = self.licenses()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"\| small \| .*\| no model card \|")
        self.assertRegex(result.stdout, r"\| kat \| .*\| not stated in the model card \|")

    def test_failures_stop_the_script(self):
        kat = PRESETS["kat"]
        self.hf.routes[f"GET /{kat[0]}/raw/{kat[1]}/README.md"] = (500, b"oops")
        result = self.licenses()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("answered HTTP 500", result.stderr)
        self.assertNotIn("| kat |", result.stdout)
        self.assertIn("cannot fetch", self.licenses({"HF_ENDPOINT": f"http://127.0.0.1:{free_port()}"}).stderr)
        self.assertIn("declares no license", self.licenses({"STUB_LICENSE": ""}).stderr)
        self.assertIn("declares no repository", self.licenses({"STUB_REPO": ""}).stderr)

    def test_needs_the_mlx_env(self):
        (self.box.root / "state" / "mlx-env").unlink()
        self.assertIn("setup mlx", self.licenses().stderr)


if __name__ == "__main__":
    unittest.main()
