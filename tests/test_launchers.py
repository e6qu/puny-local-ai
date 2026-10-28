"""bin/opencode-qwen, bin/pi-qwen, bin/codex-qwen, bin/check-tool-calls, bin/check-responses-api."""

import json
import unittest

from helpers import FakeServer, ServerCase, record_served, tool_call_reply


class OpenCodeLauncherTest(ServerCase):
    def setUp(self):
        super().setUp()
        self.agent_stubs("opencode")

    def config(self):
        return json.loads((self.box.calls / "opencode.config").read_text())

    def test_passes_the_config_inline_and_standalone(self):
        result = self.box.run(["bin/opencode-qwen"], extra_env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.box.called("opencode"), ["--version", "--standalone"])
        config = self.config()
        self.assertEqual(config["model"], "puny/qwen-local")
        provider = config["providers"]["puny"]
        self.assertEqual(provider["settings"]["baseURL"], self.server.base_url)
        self.assertEqual(provider["models"]["qwen-local"]["limit"], {"context": 262144, "output": 8192})
        self.assertEqual(provider["models"]["qwen-local"]["modelID"], "default_model")

    def test_subcommands_and_options_keep_their_order(self):
        self.box.run(["bin/opencode-qwen", "run", "do it"], extra_env=self.env)
        self.box.run(["bin/opencode-qwen", "-m", "x"], extra_env=self.env)
        calls = [c for c in self.box.called("opencode") if c != "--version"]
        self.assertEqual(calls, ["run --standalone do it", "--standalone -m x"])

    def test_context_comes_from_the_running_server(self):
        record_served(self.box, self.server.base_url, 65536)
        self.box.run(["bin/opencode-qwen"], extra_env=self.env)
        self.assertEqual(self.config()["providers"]["puny"]["models"]["qwen-local"]["limit"]["context"], 65536)

    def test_qwen_context_overrides_the_record(self):
        self.box.run(["bin/opencode-qwen"], extra_env=self.env | {"QWEN_CONTEXT": "4096", "QWEN_MAX_OUTPUT": "100"})
        self.assertEqual(self.config()["providers"]["puny"]["models"]["qwen-local"]["limit"], {"context": 4096, "output": 100})

    def test_refuses_a_server_it_did_not_start(self):
        record_served(self.box, self.server.base_url, 262144, pid=1)
        result = self.box.run(["bin/opencode-qwen"], extra_env=self.env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("was not started by bin/serve-qwen", result.stderr)
        self.assertEqual(self.box.called("opencode"), ["--version"])

    def test_refuses_without_a_record(self):
        for path in (self.box.root / "state" / "served").iterdir():
            path.unlink()
        result = self.box.run(["bin/opencode-qwen"], extra_env=self.env)
        self.assertIn("no record of a bin/serve-qwen server", result.stderr)

    def test_refuses_without_a_server(self):
        self.server.stop()
        result = self.box.run(["bin/opencode-qwen"], extra_env=self.env)
        self.assertIn("cannot reach", result.stderr)

    def test_refuses_an_unhealthy_server(self):
        self.server.routes["GET /health"] = (200, {"status": "starting"})
        result = self.box.run(["bin/opencode-qwen"], extra_env=self.env)
        self.assertIn("is not a healthy mlx_lm.server", result.stderr)

    def test_refuses_opencode_1(self):
        result = self.box.run(["bin/opencode-qwen"], extra_env=self.env | {"STUB_VERSION": "1.3.0"})
        self.assertIn("needs OpenCode 2.0 or later", result.stderr)
        self.assertEqual(self.box.called("opencode"), ["--version"])

    def test_refuses_without_opencode(self):
        (self.box.stubs / "opencode").unlink()
        result = self.box.run(["bin/opencode-qwen"], extra_env=self.env)
        self.assertIn("bin/setup opencode", result.stderr)


class PiLauncherTest(ServerCase):
    def setUp(self):
        super().setUp()
        self.agent_stubs("pi")

    def test_writes_its_own_models_json_and_selects_the_model(self):
        result = self.box.run(["bin/pi-qwen", "-p", "hi"], extra_env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.box.called("pi"), ["--provider puny --model default_model -p hi"])
        agent_dir = self.box.root / "state" / "pi-agent"
        self.assertIn(f"PI_CODING_AGENT_DIR={agent_dir}", self.env_log("pi"))
        config = json.loads((agent_dir / "models.json").read_text())
        provider = config["providers"]["puny"]
        self.assertEqual(provider["baseUrl"], self.server.base_url)
        self.assertEqual(provider["models"][0]["contextWindow"], 262144)
        self.assertEqual(provider["models"][0]["compat"]["thinkingFormat"], "qwen-chat-template")
        self.assertFalse((self.box.home / ".pi").exists(), "must not touch ~/.pi")

    def test_refuses_without_pi(self):
        (self.box.stubs / "pi").unlink()
        self.assertIn("bin/setup pi", self.box.run(["bin/pi-qwen"], extra_env=self.env).stderr)


class CodexLauncherTest(ServerCase):
    def setUp(self):
        super().setUp()
        self.box.stub("codex", "exit 0")

    def test_starts_codex_on_the_server(self):
        result = self.box.run(["bin/codex-qwen", "exec", "hi"], extra_env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        call = self.box.called("codex")[0]
        self.assertIn("model_provider=puny", call)
        self.assertIn(f'base_url="{self.server.base_url}"', call)
        self.assertIn('wire_api="responses"', call)
        self.assertIn("model_context_window=262144", call)
        self.assertTrue(call.endswith("-m default_model exec hi"))
        self.assertFalse((self.box.home / ".codex").exists())

    def test_refuses_a_server_it_did_not_start(self):
        record_served(self.box, self.server.base_url, 262144, pid=1)
        result = self.box.run(["bin/codex-qwen"], extra_env=self.env)
        self.assertIn("was not started by bin/serve-qwen", result.stderr)
        self.assertEqual(self.box.called("codex"), [])

    def test_refuses_without_codex(self):
        (self.box.stubs / "codex").unlink()
        self.assertIn("brew install codex", self.box.run(["bin/codex-qwen"], extra_env=self.env).stderr)


class CheckToolCallsTest(ServerCase):
    def run_check(self):
        return self.box.run(["bin/check-tool-calls"], extra_env=self.env)

    def test_passes_on_a_structured_tool_call(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK: the model called get_version", result.stdout)
        method, path, body = self.server.requests[-1]
        request = json.loads(body)
        self.assertEqual(request["model"], "default_model")
        self.assertEqual(request["tools"][0]["function"]["name"], "get_version")

    def test_fails_on_a_tool_call_written_as_text(self):
        self.server.routes["POST /v1/chat/completions"] = (
            200, {"choices": [{"message": {"content": "<tool_call>get_version</tool_call>"}}]}
        )
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("not parsing this model's tool-call format", result.stderr)

    def test_fails_on_another_tool(self):
        self.server.routes["POST /v1/chat/completions"] = (200, tool_call_reply("other"))
        self.assertEqual(self.run_check().returncode, 1)

    def test_fails_on_a_non_completion(self):
        self.server.routes["POST /v1/chat/completions"] = (200, b"not json")
        result = self.run_check()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a chat completion", result.stderr)


class CheckResponsesApiTest(ServerCase):
    def run_check(self):
        return self.box.run(["bin/check-responses-api", self.server.base_url], extra_env=self.env)

    def test_missing_endpoint(self):
        result = self.run_check()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not exist (HTTP 404)", result.stderr)

    def test_working_endpoint(self):
        self.server.routes["POST /v1/responses"] = (200, {"object": "response", "output": [{"type": "message"}]})
        self.assertEqual(self.run_check().returncode, 0)

    def test_wrong_object(self):
        self.server.routes["POST /v1/responses"] = (200, {"object": "chat.completion"})
        self.assertIn("not a Responses API object", self.run_check().stderr)

    def test_error_status(self):
        self.server.routes["POST /v1/responses"] = (500, b"<html>")
        self.assertIn("non-JSON body", self.run_check().stderr)


if __name__ == "__main__":
    unittest.main()
