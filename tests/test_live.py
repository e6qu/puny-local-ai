"""Live tests: real agents, a real server with the small model, real Hugging Face and npm.

Run with ./tests/run --live. They need ./bin/setup mlx small opencode pi, network
access and about 6 GiB of free memory. Everything still runs in a sandbox
with its own home folder.
"""

import json
import os
import re
import subprocess
import sys
import time
import unittest
import urllib.request
from pathlib import Path

from helpers import REPO, FakeServer, Sandbox, free_port, which
from test_integrate import USER_JSONC, USER_PI

LIVE = os.environ.get("PUNY_LIVE_TESTS") == "1"
# The sandbox blocks Hugging Face; live tests that check the real sources opt back in.
REAL_HUGGING_FACE = {"HF_ENDPOINT": "https://huggingface.co", "HF_HUB_OFFLINE": "0"}
sys.path.insert(0, str(REPO / "lib"))


def real_tool_dirs():
    return [str(Path(which(name)).resolve().parent) for name in ("node", "opencode", "pi")] + [str(Path(which("node")).parent)]


@unittest.skipUnless(LIVE, "live test: run ./tests/run --live")
class RealAgentsIntegrateTest(unittest.TestCase):
    """bin/integrate against the real OpenCode, Pi and Codex, in a sandboxed home."""

    def setUp(self):
        self.box = Sandbox(link_mlx_env=True)
        # bin/integrate works out the window from the real model; fast tests never link it.
        (self.box.root / "state" / "models").symlink_to(REPO / "state" / "models")
        self.addCleanup(self.box.cleanup)
        self.paths = real_tool_dirs() + [str(Path(which("codex")).parent)]
        self.opencode_file = self.box.home / ".config" / "opencode" / "opencode.jsonc"
        self.opencode_file.parent.mkdir(parents=True)
        self.opencode_file.write_text(USER_JSONC)
        self.pi_file = self.box.home / ".pi" / "agent" / "models.json"
        self.pi_file.parent.mkdir(parents=True)
        self.pi_file.write_text(USER_PI)

    def run_tool(self, args, timeout=120):
        # Empty input: codex exec reads more prompt text from stdin when it is not a terminal.
        return subprocess.run(args, env=self.box.env(path_dirs=self.paths), capture_output=True, text=True,
                              timeout=timeout, cwd=self.box.work, stdin=subprocess.DEVNULL)

    def test_agents_see_the_model_and_uninstall_restores_everything(self):
        # A proxy that answers at once with an error, so Codex prints its
        # settings and stops instead of retrying a refused connection.
        proxy = FakeServer({"POST /v1/responses": (400, {"error": {"message": "test proxy"}})})
        self.addCleanup(proxy.stop)
        env = {"CODEX_BASE_URL": proxy.base_url}
        result = self.box.run(["bin/integrate", "install"], extra_env=env, path_dirs=self.paths)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        models = self.run_tool(["opencode", "models", "--standalone"])
        self.assertIn("puny/qwen-local", models.stdout, models.stderr)
        self.assertIn("other/m1", models.stdout, "the user's own provider must still load")

        listed = self.run_tool(["pi", "--list-models", "default_model"])
        self.assertIn("puny", listed.stdout + listed.stderr)
        self.assertIn("ollama", self.run_tool(["pi", "--list-models", "llama"]).stdout)

        codex = self.run_tool(["codex", "-p", "puny", "--strict-config", "exec", "--skip-git-repo-check", "hi"], timeout=60)
        header = codex.stdout + codex.stderr
        self.assertRegex(header, r"(?m)^provider: puny$")
        self.assertRegex(header, r"(?m)^model: default_model$")

        result = self.box.run(["bin/integrate", "uninstall"], path_dirs=self.paths)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.opencode_file.read_text(), USER_JSONC)
        self.assertEqual(self.pi_file.read_text(), USER_PI)
        self.assertFalse((self.box.home / ".codex" / "puny.config.toml").exists())
        self.assertNotIn("puny", self.run_tool(["opencode", "models", "--standalone"]).stdout)


@unittest.skipUnless(LIVE, "live test: run ./tests/run --live")
class RealOpenCodeServiceTest(unittest.TestCase):
    """OpenCode's background service reads its config only when it starts."""

    def setUp(self):
        self.box = Sandbox(link_mlx_env=True)
        # bin/integrate works out the window from the real model; fast tests never link it.
        (self.box.root / "state" / "models").symlink_to(REPO / "state" / "models")
        self.addCleanup(self.box.cleanup)
        self.paths = real_tool_dirs()
        # The managed service has a fixed default port; give the sandbox its own.
        self.opencode(["service", "set", "port", str(free_port())])
        self.addCleanup(self.opencode, ["service", "stop"])
        started = self.opencode(["service", "start"])
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)

    def opencode(self, args):
        return subprocess.run(["opencode", *args], env=self.box.env(path_dirs=self.paths), capture_output=True,
                              text=True, timeout=120, cwd=self.box.work)

    def test_install_says_to_restart_and_the_restart_loads_the_model(self):
        # A service in use has read its config already, as yours has.
        self.assertNotIn("puny/qwen-local", self.opencode(["models"]).stdout)
        result = self.box.run(["bin/integrate", "install", "opencode"], path_dirs=self.paths)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("opencode service restart", result.stdout)
        self.assertNotIn("puny/qwen-local", self.opencode(["models"]).stdout)
        self.opencode(["service", "restart"])
        self.assertIn("puny/qwen-local", self.opencode(["models"]).stdout)

        result = self.box.run(["bin/integrate", "uninstall", "opencode"], path_dirs=self.paths)
        self.assertIn("opencode service restart", result.stdout)
        self.opencode(["service", "restart"])
        self.assertNotIn("puny/qwen-local", self.opencode(["models"]).stdout)


@unittest.skipUnless(LIVE, "live test: run ./tests/run --live")
class RealServerTest(unittest.TestCase):
    """bin/serve-qwen with the small model, then the checks and both agents against it."""

    @classmethod
    def setUpClass(cls):
        if not list((REPO / "state" / "models").glob("*Qwen3.5-4B*/.verified")):
            raise unittest.SkipTest("the small model is not downloaded; run ./bin/setup mlx small")
        cls.box = Sandbox(link_mlx_env=True)
        # Live tests use the real downloaded models; fast tests never do.
        (cls.box.root / "state" / "models").symlink_to(REPO / "state" / "models")
        cls.paths = real_tool_dirs()
        cls.port = free_port()
        cls.env = {"MLX_BASE_URL": f"http://127.0.0.1:{cls.port}/v1", "QWEN_PRESET": "small"}
        cls.log = open(cls.box.dir / "server.log", "w")
        cls.server = subprocess.Popen(
            [str(cls.box.root / "bin" / "serve-qwen")], env=cls.box.env(cls.env, cls.paths),
            stdout=cls.log, stderr=subprocess.STDOUT, cwd=cls.box.work,
        )
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if cls.server.poll() is not None:
                cls.tearDownClass()
                raise AssertionError("the server stopped:\n" + (cls.box.dir / "server.log").read_text())
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{cls.port}/health", timeout=2)
                return
            except OSError:
                time.sleep(1)
        cls.tearDownClass()
        raise AssertionError("the server did not become healthy:\n" + (cls.box.dir / "server.log").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait(timeout=30)
        cls.log.close()
        cls.box.cleanup()

    def server_log(self):
        return (self.box.dir / "server.log").read_text()

    def run_script(self, args, extra=None, timeout=600):
        return self.box.run(args, extra_env=self.env | (extra or {}), path_dirs=self.paths, timeout=timeout)

    def chat(self, payload):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/chat/completions",
            data=json.dumps({"model": "default_model", **payload}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.load(e)

    def test_startup_report_and_record(self):
        log = self.server_log()
        self.assertIn("The agents are told the context window is 262144 tokens.", log)
        self.assertIn("needed for the full context window", log)
        record = next((self.box.root / "state" / "served").iterdir()).read_text().split()
        self.assertEqual(record, [str(self.server.pid), "262144"])

    def test_tool_calls(self):
        result = self.run_script(["bin/check-tool-calls"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_guard_rejects_a_request_over_the_window(self):
        status, body = self.chat({"messages": [{"role": "user", "content": "hi"}], "max_tokens": 300000})
        self.assertNotEqual(status, 200)
        self.assertIn("over the 262144-token context window", body["error"])

    def test_guard_admits_a_long_prompt_and_logs_its_step(self):
        text = "alpha beta gamma delta\n" * 6000
        status, body = self.chat({
            "messages": [{"role": "user", "content": "Count the lines.\n" + text}],
            "max_tokens": 8, "chat_template_kwargs": {"enable_thinking": False},
        })
        self.assertEqual(status, 200, body)
        self.assertGreater(body["usage"]["prompt_tokens"], 30000)
        self.assertRegex(self.server_log(), r"Admitted \d+ tokens with prefill step \d+")

    def test_codex_launcher_stops_without_a_responses_api(self):
        result = self.run_script(["bin/codex-qwen"], extra={"CODEX_BASE_URL": self.env["MLX_BASE_URL"]})
        self.assertIn("has no Responses API", result.stderr)
        self.assertNotEqual(result.returncode, 0)

    def test_smoke_test_with_both_real_agents(self):
        result = self.run_script(["bin/smoke-test"], timeout=900)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("All smoke tests passed.", result.stdout)


@unittest.skipUnless(LIVE, "live test: run ./tests/run --live")
class RealSourcesTest(unittest.TestCase):
    """The pinned sources still exist and say what the docs say."""

    def test_every_pinned_model_commit_lists_its_files(self):
        import verify_model

        box = Sandbox()
        self.addCleanup(box.cleanup)
        for preset in ("kat", "reap", "small"):
            out = subprocess.run(
                ["bash", "-c", f'source "{box.root}/lib/common.sh"; source "{box.root}/lib/mlx.sh"; preset_info {preset}; echo "$preset_repo $preset_revision"'],
                env=box.env(), capture_output=True, text=True,
            ).stdout.split()
            with self.subTest(preset):
                files = verify_model.listed_files(out[0], out[1])
                names = {f["path"] for f in files}
                self.assertIn("config.json", names)
                self.assertTrue(any(re.fullmatch(r"model.*\.safetensors", n) for n in names))

    def test_licenses_match_third_party_md(self):
        box = Sandbox(link_mlx_env=True)
        self.addCleanup(box.cleanup)
        result = box.run(["bin/licenses"], extra_env=REAL_HUGGING_FACE,
                         path_dirs=[str(Path(which("npm")).parent), str(Path(which("node")).parent)], timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr)
        third_party = (REPO / "THIRD_PARTY.md").read_text()
        for line in result.stdout.splitlines():
            if line.startswith("| ") and not line.startswith("| Package") and not line.startswith("| Preset"):
                with self.subTest(line):
                    self.assertIn(line, third_party)


if __name__ == "__main__":
    unittest.main()
