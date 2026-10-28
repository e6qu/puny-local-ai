"""bin/integrate: adds the local model to agent configs and removes it cleanly."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import REPO, Sandbox

sys.path.insert(0, str(REPO / "lib"))
import jsonc  # noqa: E402

USER_JSONC = """{
  // my own settings, kept as they are
  "$schema": "https://opencode.ai/config.json",
  "model": "other/m1",
  "providers": {
    "other": {
      "name": "Other",
      "package": "@opencode/ai/providers/openai-compatible",
      "settings": {"baseURL": "http://127.0.0.1:9/v1", "apiKey": "x"},
      "models": {"m1": {"modelID": "m1", "name": "M1"}}, // trailing comma next
    },
  },
}
"""

USER_PI = """{
  "providers": {
    "ollama": {
      "baseUrl": "http://localhost:11434/v1",
      "api": "openai-completions",
      "apiKey": "ollama",
      "models": [{"id": "llama"}]
    }
  }
}
"""


class IntegrateTest(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)
        self.box.stub("opencode", 'case "$1" in --version) echo "opencode v${STUB_VERSION:-2.1.1}" ;; service) [[ -n "${STUB_SERVICE_FAIL:-}" ]] && { echo boom; exit 1; }; echo "${STUB_SERVICE:-stopped}" ;; esac')
        self.box.stub("pi", "exit 0")
        # The window the agents are told comes from lib/serve.py, faked here.
        self.box.fake_mlx_env()
        self.box.fake_model("mlx-community/KAT-Coder-V2.5-Dev-OptiQ-4bit-REAP-18B", "b33dfde62c2753841aa2b1662529bf7f3b1fdde5")
        self.box.fake_model("mlx-community/Qwen3.5-4B-4bit", "0e7ffd5c629ef7719d4cbc04069232580bfa9d9c")
        self.opencode_dir = self.box.home / ".config" / "opencode"
        self.pi_file = self.box.home / ".pi" / "agent" / "models.json"
        self.codex_file = self.box.home / ".codex" / "puny.config.toml"

    def integrate(self, *args, env=None, ok=True):
        result = self.box.run(["bin/integrate", *args], extra_env=env)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def read_json(self, path):
        return jsonc.plain(jsonc.parse(path.read_text()))

    def home_files(self):
        return sorted(str(p.relative_to(self.box.home)) for p in self.box.home.rglob("*"))

    # ------------------------------------------------------------ fresh home

    def test_fresh_home_install_and_uninstall_leave_nothing(self):
        result = self.integrate("install")
        self.assertIn("codex: skipped, not installed", result.stdout)
        config = self.read_json(self.opencode_dir / "opencode.json")
        self.assertEqual(config["$schema"], "https://opencode.ai/config.json")
        provider = config["providers"]["puny"]
        self.assertEqual(provider["name"], "puny-local-ai")
        self.assertEqual(provider["settings"]["baseURL"], "http://127.0.0.1:8081/v1")
        model = provider["models"]["qwen-local"]
        self.assertEqual(model["modelID"], "default_model")
        self.assertEqual(model["limit"], {"context": 262144, "output": 8192})
        self.assertNotIn("model", config, "the user's default model must not change")
        pi = self.read_json(self.pi_file)["providers"]["puny"]
        self.assertEqual(pi["models"][0]["contextWindow"], 262144)
        self.assertEqual(pi["models"][0]["id"], "default_model")

        status = self.integrate("status").stdout
        self.assertIn("opencode: installed", status)
        self.assertIn("pi: installed", status)
        self.assertIn("codex: not installed", status)

        self.integrate("uninstall")
        self.assertEqual(self.home_files(), [], "uninstall must remove the files and folders it created")
        self.assertEqual(list((self.box.root / "state" / "integrations").glob("*.json")), [])

    def test_existing_folders_are_kept(self):
        self.opencode_dir.mkdir(parents=True)
        (self.opencode_dir / "tui.json").write_text("{}\n")
        self.integrate("install", "opencode")
        self.integrate("uninstall", "opencode")
        self.assertEqual(self.home_files(), [".config", ".config/opencode", ".config/opencode/tui.json"])

    # ------------------------------------------------------------ existing configs

    def test_existing_configs_come_back_byte_for_byte(self):
        self.opencode_dir.mkdir(parents=True)
        user_file = self.opencode_dir / "opencode.jsonc"
        user_file.write_text(USER_JSONC)
        user_file.chmod(0o600)
        self.pi_file.parent.mkdir(parents=True)
        self.pi_file.write_text(USER_PI)

        self.integrate("install")
        self.assertFalse((self.opencode_dir / "opencode.json").exists(), "must edit the .jsonc, not add a .json")
        config = self.read_json(user_file)
        self.assertEqual(config["model"], "other/m1")
        self.assertEqual(set(config["providers"]), {"puny", "other"})
        self.assertIn("// my own settings, kept as they are", user_file.read_text())
        self.assertEqual(user_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(set(self.read_json(self.pi_file)["providers"]), {"puny", "ollama"})
        backups = list((self.box.root / "state" / "integrations" / "backups").iterdir())
        self.assertEqual(len(backups), 2)
        self.assertIn(USER_JSONC, [b.read_text() for b in backups])

        self.integrate("uninstall")
        self.assertEqual(user_file.read_text(), USER_JSONC)
        self.assertEqual(self.pi_file.read_text(), USER_PI)
        self.assertEqual(user_file.stat().st_mode & 0o777, 0o600)

    def test_later_edits_elsewhere_survive_uninstall(self):
        self.pi_file.parent.mkdir(parents=True)
        self.pi_file.write_text(USER_PI)
        self.integrate("install", "pi")
        edited = self.pi_file.read_text().replace('"apiKey": "ollama"', '"apiKey": "changed"')
        self.pi_file.write_text(edited)
        self.integrate("uninstall", "pi")
        self.assertEqual(self.pi_file.read_text(), USER_PI.replace('"apiKey": "ollama"', '"apiKey": "changed"'))

    def test_an_edited_entry_is_still_removed(self):
        self.pi_file.parent.mkdir(parents=True)
        self.pi_file.write_text(USER_PI)
        self.integrate("install", "pi")
        self.pi_file.write_text(self.pi_file.read_text().replace("262144", "100000"))
        self.integrate("uninstall", "pi")
        self.assertEqual(self.read_json(self.pi_file), json.loads(USER_PI))

    def test_uninstall_works_after_state_is_deleted(self):
        self.opencode_dir.mkdir(parents=True)
        (self.opencode_dir / "opencode.jsonc").write_text(USER_JSONC)
        self.integrate("install", "opencode")
        for manifest in (self.box.root / "state" / "integrations").glob("*.json"):
            manifest.unlink()
        self.integrate("uninstall", "opencode")
        self.assertEqual(self.read_json(self.opencode_dir / "opencode.jsonc"), jsonc.plain(jsonc.parse(USER_JSONC)))

    def test_reinstall_replaces_the_entry(self):
        self.pi_file.parent.mkdir(parents=True)
        self.pi_file.write_text(USER_PI)
        self.integrate("install", "pi")
        self.integrate("install", "pi", env={"QWEN_CONTEXT": "65536", "MLX_BASE_URL": "http://127.0.0.1:9999/v1"})
        text = self.pi_file.read_text()
        self.assertEqual(text.count('"puny"'), 1)
        pi = self.read_json(self.pi_file)["providers"]["puny"]
        self.assertEqual(pi["models"][0]["contextWindow"], 65536)
        self.assertEqual(pi["baseUrl"], "http://127.0.0.1:9999/v1")
        self.integrate("uninstall", "pi")
        self.assertEqual(self.pi_file.read_text(), USER_PI)

    def test_symlinked_config_stays_a_symlink(self):
        dotfiles = self.box.dir / "dotfiles"
        dotfiles.mkdir()
        target = dotfiles / "opencode.jsonc"
        target.write_text(USER_JSONC)
        self.opencode_dir.mkdir(parents=True)
        link = self.opencode_dir / "opencode.jsonc"
        link.symlink_to(target)
        self.integrate("install", "opencode")
        self.assertTrue(link.is_symlink())
        self.assertIn('"puny"', target.read_text())
        self.integrate("uninstall", "opencode")
        self.assertTrue(link.is_symlink())
        self.assertEqual(target.read_text(), USER_JSONC)

    def test_environment_overrides_for_config_locations(self):
        env = {
            "XDG_CONFIG_HOME": str(self.box.dir / "xdg"),
            "PI_CODING_AGENT_DIR": str(self.box.dir / "pi-dir"),
            "CODEX_HOME": str(self.box.dir / "codex-home"),
        }
        self.box.stub("codex", "exit 0")
        self.integrate("install", env=env)
        self.assertTrue((self.box.dir / "xdg" / "opencode" / "opencode.json").exists())
        self.assertTrue((self.box.dir / "pi-dir" / "models.json").exists())
        self.assertTrue((self.box.dir / "codex-home" / "puny.config.toml").exists())
        self.assertEqual(self.home_files(), [])
        self.integrate("uninstall", env=env)
        for d in ("xdg", "pi-dir", "codex-home"):
            self.assertFalse((self.box.dir / d).exists(), d)

    # ------------------------------------------------------------ awkward files

    def test_windows_line_endings_and_a_byte_order_mark_survive(self):
        self.pi_file.parent.mkdir(parents=True)
        original = "\ufeff" + USER_PI.replace("\n", "\r\n")
        self.pi_file.write_bytes(original.encode())
        self.integrate("install", "pi")
        text = self.pi_file.read_bytes().decode()
        self.assertNotIn("\n", text.replace("\r\n", ""), "every new line must use CRLF too")
        self.integrate("uninstall", "pi")
        self.assertEqual(self.pi_file.read_bytes().decode(), original)

    def test_a_provider_added_after_ours_survives(self):
        for start in ('{\n  "providers": {\n  }\n}\n', '{"providers": {}}'):
            with self.subTest(start=start):
                self.pi_file.parent.mkdir(parents=True, exist_ok=True)
                self.pi_file.write_text(start)
                self.integrate("install", "pi")
                text = self.pi_file.read_text()
                root = jsonc.parse(text)
                providers = root.get("providers").value
                position = providers.members[-1].end
                self.pi_file.write_text(text[:position] + ', "mine": {"apiKey": "k"}' + text[position:])
                self.integrate("uninstall", "pi")
                self.assertEqual(self.read_json(self.pi_file), {"providers": {"mine": {"apiKey": "k"}}})
                self.integrate("install", "pi")
                self.integrate("uninstall", "pi")

    def test_comments_survive_removal_without_a_record(self):
        self.pi_file.parent.mkdir(parents=True)
        self.pi_file.write_text('{\n  "providers": {\n    "mine": {}, // keep this\n    // and this\n  }\n}\n')
        self.integrate("install", "pi")
        text = self.pi_file.read_text()
        # Move our entry to the end, as a hand edit might, and lose the record.
        root = jsonc.parse(text)
        member = root.get("providers").value.get("puny")
        entry = text[member.start:member.comma_end]
        text = text.replace(entry, "", 1)
        text = text.replace("// and this\n", "// and this\n    " + entry.rstrip(",") + "\n", 1)
        self.pi_file.write_text(text)
        for record in (self.box.root / "state" / "integrations").glob("*.json"):
            record.unlink()
        self.integrate("uninstall", "pi")
        after = self.pi_file.read_text()
        self.assertIn("// keep this", after)
        self.assertIn("// and this", after)
        self.assertEqual(self.read_json(self.pi_file), {"providers": {"mine": {}}})

    def test_a_renamed_config_is_still_cleaned_up(self):
        self.opencode_dir.mkdir(parents=True)
        (self.opencode_dir / "opencode.json").write_text('{"model": "x/y"}\n')
        self.integrate("install", "opencode")
        (self.opencode_dir / "opencode.json").rename(self.opencode_dir / "opencode.jsonc")
        self.integrate("uninstall", "opencode")
        self.assertEqual(self.read_json(self.opencode_dir / "opencode.jsonc"), {"model": "x/y"})

    def test_hard_links_stay_linked(self):
        self.pi_file.parent.mkdir(parents=True)
        self.pi_file.write_text(USER_PI)
        other = self.box.dir / "models-link.json"
        import os
        os.link(self.pi_file, other)
        self.integrate("install", "pi")
        self.assertIn('"puny"', other.read_text())
        self.integrate("uninstall", "pi")
        self.assertEqual(other.read_text(), USER_PI)
        self.assertEqual(self.pi_file.stat().st_ino, other.stat().st_ino)

    def test_backups_are_capped(self):
        self.pi_file.parent.mkdir(parents=True)
        self.pi_file.write_text(USER_PI)
        for _ in range(7):
            self.integrate("install", "pi")
        backups = list((self.box.root / "state" / "integrations" / "backups").glob("pi-*"))
        self.assertEqual(len(backups), 10)

    # ------------------------------------------------------------ refusals

    def test_refuses_a_dangling_symlink(self):
        self.opencode_dir.mkdir(parents=True)
        (self.opencode_dir / "opencode.json").symlink_to(self.box.dir / "dotfiles" / "missing.json")
        result = self.integrate("install", "opencode", ok=False)
        self.assertIn("does not exist; fix the link first", result.stderr)
        self.assertFalse((self.box.dir / "dotfiles").exists())

    def test_refuses_a_read_only_file(self):
        self.pi_file.parent.mkdir(parents=True)
        self.pi_file.write_text(USER_PI)
        self.pi_file.chmod(0o444)
        self.assertIn("read-only", self.integrate("install", "pi", ok=False).stderr)
        self.assertEqual(self.pi_file.read_text(), USER_PI)

    def test_bad_input_gives_errors_not_tracebacks(self):
        self.pi_file.parent.mkdir(parents=True)
        cases = {
            "not UTF-8": b'{"a": "\xff"}',
            "bad escape": b'{"a": "\\q"}',
            "tab in a string": b'{"a": "x\ty"}',
            "duplicate providers": b'{"providers": {}, "providers": {}}',
            "deep nesting": b'{"a": ' + b'{"a": ' * 3000 + b"1" + b"}" * 3001,
        }
        for name, data in cases.items():
            with self.subTest(name):
                self.pi_file.write_bytes(data)
                for command in ("install", "uninstall", "status"):
                    result = self.integrate(command, "pi", ok=command == "status" and False)
                    self.assertNotIn("Traceback", result.stderr, command)
                self.assertEqual(self.pi_file.read_bytes(), data)

    def test_a_damaged_record_is_reported(self):
        self.integrate("install", "pi")
        (self.box.root / "state" / "integrations" / "pi.json").write_text("{not json")
        result = self.integrate("uninstall", "pi", ok=False)
        self.assertIn("is damaged", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_one_run_at_a_time(self):
        import fcntl
        lock_path = self.box.root / "state" / "integrations" / ".lock"
        lock_path.parent.mkdir(parents=True)
        with open(lock_path, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            result = self.integrate("install", "pi", ok=False)
        self.assertIn("another bin/integrate is running", result.stderr)
        self.assertFalse(self.pi_file.exists())


    def test_refuses_a_puny_provider_it_did_not_add(self):
        self.pi_file.parent.mkdir(parents=True)
        foreign = '{"providers": {"puny": {"name": "mine"}}}\n'
        self.pi_file.write_text(foreign)
        result = self.integrate("install", "pi", ok=False)
        self.assertIn("did not add", result.stderr)
        result = self.integrate("uninstall", "pi", ok=False)
        self.assertIn("not added by puny-local-ai", result.stderr)
        self.assertEqual(self.pi_file.read_text(), foreign)

    def test_refuses_invalid_files_and_leaves_them_alone(self):
        self.pi_file.parent.mkdir(parents=True)
        broken = '{"providers": {\n'
        self.pi_file.write_text(broken)
        result = self.integrate("install", "pi", ok=False)
        self.assertIn("cannot read", result.stderr)
        self.assertEqual(self.pi_file.read_text(), broken)

    def test_refuses_providers_that_is_not_an_object(self):
        self.pi_file.parent.mkdir(parents=True)
        self.pi_file.write_text('{"providers": []}')
        self.assertIn("not an object", self.integrate("install", "pi", ok=False).stderr)

    def test_refuses_two_opencode_config_files(self):
        self.opencode_dir.mkdir(parents=True)
        (self.opencode_dir / "opencode.json").write_text("{}")
        (self.opencode_dir / "opencode.jsonc").write_text("{}")
        self.assertIn("both", self.integrate("install", "opencode", ok=False).stderr)

    def test_one_refusal_does_not_stop_the_other_agents(self):
        self.pi_file.parent.mkdir(parents=True)
        self.pi_file.write_text("not json")
        result = self.integrate("install", "opencode", "pi", ok=False)
        self.assertIn("error: pi:", result.stderr)
        self.assertTrue((self.opencode_dir / "opencode.json").exists())

    def test_default_install_skips_missing_agents(self):
        (self.box.stubs / "opencode").unlink()
        result = self.integrate("install")
        self.assertIn("opencode: skipped, not installed", result.stdout)
        self.assertTrue(self.pi_file.exists())
        self.assertFalse((self.box.home / ".config").exists())

    def test_any_model_needs_its_context(self):
        self.box.fake_model("a/b", "3" * 40)
        self.integrate("install", "pi", env={"QWEN_REPO": "a/b", "QWEN_REVISION": "3" * 40, "QWEN_CONTEXT": "4096", "STUB_WINDOW": "4096"})
        self.assertEqual(self.read_json(self.pi_file)["providers"]["puny"]["models"][0]["contextWindow"], 4096)
        self.assertIn("--context 4096", "\n".join(self.box.called("python")))

    def test_the_window_is_what_fits(self):
        self.integrate("install", "opencode", "pi", env={"STUB_WINDOW": "247319"})
        self.assertEqual(self.read_json(self.pi_file)["providers"]["puny"]["models"][0]["contextWindow"], 247319)
        opencode = self.read_json(self.opencode_dir / "opencode.json")
        self.assertEqual(opencode["providers"]["puny"]["models"]["qwen-local"]["limit"]["context"], 247319)

    def test_install_needs_the_model_and_the_environment(self):
        result = self.integrate("install", "pi", env={"QWEN_PRESET": "reap"}, ok=False)
        self.assertIn("is not downloaded yet", result.stderr)
        self.assertIn("setup reap", result.stderr)
        (self.box.root / "state" / "mlx-env" / ".installed").unlink()
        self.assertIn("setup mlx", self.integrate("install", "pi", ok=False).stderr)
        self.assertFalse(self.pi_file.exists())

    def test_install_with_no_agents_present(self):
        (self.box.stubs / "opencode").unlink()
        (self.box.stubs / "pi").unlink()
        result = self.integrate("install", ok=False)
        self.assertIn("no coding agent to set up", result.stderr)
        self.assertEqual(self.home_files(), [])

    def test_usage_errors(self):
        self.assertIn("usage", self.integrate(ok=False).stderr)
        self.assertIn("usage", self.integrate("remove", ok=False).stderr)
        self.assertIn("unknown agent", self.integrate("install", "claude", ok=False).stderr)
        self.assertIn("set QWEN_REPO and QWEN_REVISION together", self.integrate("install", "pi", env={"QWEN_REPO": "a/b"}, ok=False).stderr)
        self.assertIn("set QWEN_CONTEXT with QWEN_REPO", self.integrate("install", "pi", env={"QWEN_REPO": "a/b", "QWEN_REVISION": "3" * 40}, ok=False).stderr)
        self.assertIn("whole number", self.integrate("install", "pi", env={"QWEN_CONTEXT": "lots"}, ok=False).stderr)

    def test_context_follows_the_preset_and_settings(self):
        self.integrate("install", "pi", env={"QWEN_PRESET": "small", "QWEN_MAX_OUTPUT": "4096"})
        model = self.read_json(self.pi_file)["providers"]["puny"]["models"][0]
        self.assertEqual((model["contextWindow"], model["maxTokens"]), (262144, 4096))
        self.assertIn("unknown model preset", self.integrate("install", "pi", env={"QWEN_PRESET": "huge"}, ok=False).stderr)

    # ------------------------------------------------------------ OpenCode 2

    def test_refuses_opencode_1(self):
        result = self.integrate("install", "opencode", env={"STUB_VERSION": "1.14.2"}, ok=False)
        self.assertIn("OpenCode 1.14.2 is too old; this project needs OpenCode 2.0 or later", result.stderr)
        self.assertEqual(self.home_files(), [])
        result = self.integrate("install", env={"STUB_VERSION": "1.14.2"}, ok=False)
        self.assertFalse(self.pi_file.exists(), "nothing is installed when OpenCode is too old")

    def test_uninstall_works_whatever_the_opencode_version(self):
        self.integrate("install", "opencode")
        self.integrate("uninstall", "opencode", env={"STUB_VERSION": "1.0.0"})
        self.assertEqual(self.home_files(), [])

    def test_says_when_the_opencode_service_needs_a_restart(self):
        quiet = self.integrate("install", "opencode").stdout
        self.assertNotIn("background service", quiet)
        running = self.integrate("uninstall", "opencode", env={"STUB_SERVICE": "http://127.0.0.1:49374"}).stdout
        self.assertIn("the background service is running (http://127.0.0.1:49374) and may still use the old config", running)
        self.assertIn("opencode service restart", running)
        self.assertEqual(self.box.called("opencode").count("service restart"), 0, "it must never restart the service itself")
        self.assertNotIn("background service", self.integrate("install", "pi").stdout)
        failing = self.integrate("install", "opencode", env={"STUB_SERVICE_FAIL": "1"}).stdout
        self.assertIn("could not check the background service (boom)", failing)
        # Nothing changed, so nothing needs a restart.
        self.pi_file.parent.mkdir(parents=True, exist_ok=True)
        self.assertNotIn("background service", self.integrate("uninstall", "pi", env={"STUB_SERVICE": "running"}).stdout)

    # ------------------------------------------------------------ codex

    def test_codex_profile(self):
        self.integrate("install", "codex", env={"MLX_BASE_URL": "http://127.0.0.1:7000/v1"})
        text = self.codex_file.read_text()
        self.assertTrue(text.startswith("# Added by puny-local-ai"))
        self.assertIn('base_url = "http://127.0.0.1:7000/v1"', text)
        self.assertIn('wire_api = "responses"', text)
        self.assertIn('model_provider = "puny"', text)
        self.assertIn("model_context_window = 262144", text)
        self.assertIn("codex: installed", self.integrate("status", "codex").stdout)
        self.integrate("uninstall", "codex")
        self.assertEqual(self.home_files(), [])

    def test_codex_edits_are_backed_up(self):
        env = {}
        self.integrate("install", "codex", env=env)
        self.codex_file.write_text(self.codex_file.read_text() + 'model_reasoning_effort = "high"\n')
        result = self.integrate("install", "codex", env=env | {"QWEN_CONTEXT": "4096"})
        backup = Path(result.stdout.split("is in ")[1].split(")")[0])
        self.assertIn("model_reasoning_effort", backup.read_text())
        result = self.integrate("uninstall", "codex")
        self.assertIn("a copy is in", result.stdout)

    def test_codex_refuses_a_profile_it_did_not_write(self):
        self.codex_file.parent.mkdir(parents=True)
        self.codex_file.write_text('model = "mine"\n')
        self.assertIn("not added by", self.integrate("install", "codex", ok=False).stderr)
        self.assertIn("not added by", self.integrate("uninstall", "codex", ok=False).stderr)
        self.assertIn("was not added", self.integrate("status", "codex").stdout)
        self.assertEqual(self.codex_file.read_text(), 'model = "mine"\n')

    def test_uninstall_when_nothing_is_installed(self):
        result = self.integrate("uninstall")
        self.assertIn("opencode: not installed", result.stdout)
        self.assertIn("pi: not installed", result.stdout)
        self.assertIn("codex: not installed", result.stdout)
        self.assertEqual(self.home_files(), [])

    # ------------------------------------------------------------ consistency

    def test_launchers_and_install_use_the_same_provider(self):
        self.integrate("install", "opencode", "pi")
        env = {"PUNY_BASE_URL": "http://127.0.0.1:8081/v1", "PUNY_CONTEXT": "262144", "PUNY_MAX_OUTPUT": "8192"}
        for agent, path in (("opencode", self.opencode_dir / "opencode.json"), ("pi", self.pi_file)):
            out = subprocess.run(
                ["/usr/bin/python3", "-I", str(self.box.root / "lib" / "integrate.py"), "config", agent],
                env=self.box.env(env), capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(json.loads(out.stdout)["providers"]["puny"], self.read_json(path)["providers"]["puny"])


if __name__ == "__main__":
    unittest.main()
