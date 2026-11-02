"""puny.conf: lib/common.sh's loader, lib/config.py and bin/config; tests/test_webui.py covers the web UI's side."""

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import REPO, Sandbox, free_port
from test_setup import bash

sys.path.insert(0, str(REPO / "lib"))
import config  # noqa: E402

KAT = "mlx-community/KAT-Coder-V2.5-Dev-OptiQ-4bit-REAP-18B"
SMALL_REPO = "mlx-community/Qwen3.5-4B-4bit"
SMALL_REVISION = "0e7ffd5c629ef7719d4cbc04069232580bfa9d9c"


class LoaderTest(unittest.TestCase):
    """lib/common.sh reads puny.conf for every command."""

    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)
        self.conf = self.box.root / "puny.conf"

    def values(self, text, extra=None, names="MLX_PORT QWEN_TEMP QWEN_REPO QWEN_CONTEXT"):
        self.conf.write_text(text)
        script = "resolve_model_settings; echo " + " ".join(f'"${name}"' for name in names.split())
        result = bash(self.box, script, extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.split()

    def test_the_file_sets_what_the_environment_does_not(self):
        text = '# mine\n\nMLX_BASE_URL=http://127.0.0.1:9100/v1\n  QWEN_TEMP = "0.7"\nQWEN_PRESET=small\n'
        self.assertEqual(self.values(text), ["9100", "0.7", SMALL_REPO, "262144"])
        self.assertEqual(self.values(text, {"QWEN_TEMP": "1.0"}), ["9100", "1.0", SMALL_REPO, "262144"])

    def test_a_model_from_the_environment_replaces_the_files_model_settings(self):
        text = f"QWEN_REPO=org/m\nQWEN_REVISION={'a' * 40}\nQWEN_CONTEXT=4096\n"
        self.assertEqual(self.values(text, names="QWEN_REPO QWEN_CONTEXT"), ["org/m", "4096"])
        self.assertEqual(self.values(text, {"QWEN_PRESET": "kat"}, names="QWEN_REPO QWEN_CONTEXT"), [KAT, "262144"])

    def test_values_are_never_run(self):
        marker = self.box.dir / "ran"
        self.conf.write_text(f"QWEN_TEMP=$(touch {marker})\nBENCH_AGENTS=`touch {marker}`\n")
        result = bash(self.box, 'echo "$BENCH_AGENTS"')
        self.assertEqual(result.stdout.strip(), f"`touch {marker}`")
        self.assertFalse(marker.exists())

    def test_bad_files(self):
        for text, message in [
            ("QWEN_TEMPERATURE=1\n", "line 1: QWEN_TEMPERATURE is not a puny-local-ai setting"),
            ("# ok\nnot a setting\n", "line 2 is not KEY=VALUE"),
            ("PATH=/tmp\n", "PATH is not a puny-local-ai setting"),
        ]:
            with self.subTest(text):
                self.conf.write_text(text)
                result = bash(self.box, "true")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_puny_config_names_another_file(self):
        other = self.box.dir / "other.conf"
        other.write_text("QWEN_TEMP=0.3\n")
        self.conf.write_text("QWEN_TEMP=0.9\n")
        self.assertEqual(bash(self.box, 'echo "$QWEN_TEMP"', {"PUNY_CONFIG": str(other)}).stdout.strip(), "0.3")

    def test_the_settings_lists_agree(self):
        names = bash(self.box, 'echo "${CONFIG_SETTINGS[*]}"').stdout.split()
        self.assertEqual(names, config.KEYS)
        documented = (REPO / "README.md").read_text()
        for key in config.KEYS:
            with self.subTest(key):
                self.assertIn(f"`{key}`", documented + (REPO / "docs" / "models.md").read_text())

    def test_every_setting_a_script_reads_can_be_saved(self):
        read = set()
        for path in list((REPO / "bin").iterdir()) + list((REPO / "lib").glob("*.sh")):
            read |= set(re.findall(r'"\$\{((?:QWEN|MLX|BENCH|SMOKE|WEB_UI)_[A-Z_]*)[:-]', path.read_text()))
        self.assertEqual(read - set(config.KEYS), {"MLX_MODEL"})


class ConfigFileTest(unittest.TestCase):
    """lib/config.py edits the file in place."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "puny.conf"

    def tearDown(self):
        for f in self.dir.iterdir():
            f.unlink()
        self.dir.rmdir()

    def test_edits_keep_comments_and_order(self):
        self.path.write_text("# mine\nQWEN_TEMP=0.7\n\n# port\nMLX_BASE_URL=http://127.0.0.1:1/v1\n")
        config.write(self.path, {"QWEN_TEMP": "0.5", "QWEN_PRESET": "small", "MLX_BASE_URL": ""})
        self.assertEqual(self.path.read_text(), "# mine\nQWEN_TEMP=0.5\n\n# port\nQWEN_PRESET=small\n")
        self.assertEqual(config.read(self.path), {"QWEN_TEMP": "0.5", "QWEN_PRESET": "small"})
        self.assertEqual(list(self.dir.glob(".puny.conf.*")), [])

    def test_a_new_file_gets_a_header(self):
        config.write(self.path, {"QWEN_TEMP": "0.5"})
        self.assertTrue(self.path.read_text().startswith("# puny-local-ai settings"))
        self.assertEqual(config.read(self.path), {"QWEN_TEMP": "0.5"})

    def test_refusals(self):
        for changes, message in [({"PATH": "x"}, "not a puny-local-ai setting"),
                                 ({"QWEN_TEMP": "1\nQWEN_PRESET=x"}, "one line"),
                                 ({"QWEN_TEMP": " 1"}, "spaces")]:
            with self.subTest(message), self.assertRaises(config.ConfigError) as caught:
                config.write(self.path, changes)
            self.assertIn(message, str(caught.exception))
        self.assertFalse(self.path.exists())


class ConfigCommandTest(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)
        self.conf = self.box.root / "puny.conf"

    def config(self, *args, env=None):
        return self.box.run(["bin/config", *args], extra_env=env)

    def show(self, env=None):
        result = self.config("show", "--json", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return {s["key"]: (s["source"], s["value"]) for s in json.loads(result.stdout)["settings"]}

    def test_set_show_and_unset(self):
        result = self.config("set", "QWEN_PRESET=small", "QWEN_TEMP=0.7")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Restart ./bin/serve-qwen", result.stdout)
        shown = self.show({"QWEN_MAX_OUTPUT": "100"})
        self.assertEqual(shown["QWEN_PRESET"], ("puny.conf", "small"))
        self.assertEqual(shown["QWEN_REPO"], ("default", SMALL_REPO))
        self.assertEqual(shown["QWEN_MAX_OUTPUT"], ("environment", "100"))
        self.assertEqual(shown["WEB_UI_URL"], ("default", "http://127.0.0.1:8090"))
        self.assertEqual(self.config("unset", "QWEN_TEMP").returncode, 0)
        self.assertEqual(config.read(self.conf), {"QWEN_PRESET": "small"})
        self.assertIn("QWEN_PRESET                puny.conf    small", self.config().stdout)

    def test_invalid_values_are_never_saved(self):
        self.config("set", "QWEN_TEMP=0.7")
        before = self.conf.read_text()
        for pair, message in [("QWEN_KV_BITS=5", "QWEN_KV_BITS must be 4 or 8"),
                              ("QWEN_REPO=org/m", "set QWEN_REPO and QWEN_REVISION together"),
                              ("MLX_BASE_URL=http://x/v1", "MLX_BASE_URL must look like"),
                              ("WEB_UI_URL=http://0.0.0.0:80", "WEB_UI_URL must look like"),
                              ("QWEN_PRESET=huge", "unknown model preset"),
                              ("NOPE=1", "not a puny-local-ai setting")]:
            with self.subTest(pair):
                result = self.config("set", pair)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertIn("nothing was saved", result.stderr)
                self.assertEqual(self.conf.read_text(), before)
        self.assertEqual([p.name for p in self.box.root.glob(".puny.conf.*")], [])

    def test_the_file_is_checked_without_the_environment(self):
        # QWEN_REPO in the environment would hide the file's bad preset.
        env = {"QWEN_REPO": "org/m", "QWEN_REVISION": "a" * 40, "QWEN_CONTEXT": "4096"}
        self.assertNotEqual(self.config("set", "QWEN_PRESET=huge", env=env).returncode, 0)

    def test_raw_and_replace(self):
        self.assertEqual(self.config("raw").stdout, "")
        result = self.box.run(["bin/config", "replace"], input="# notes kept\nQWEN_PRESET=small\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.config("raw").stdout, "# notes kept\nQWEN_PRESET=small\n")
        self.conf.chmod(0o600)
        for text, message in [("QWEN_KV_BITS=3\n", "QWEN_KV_BITS must be 4 or 8"),
                              ("NOPE=1\n", "the new puny.conf line 1: NOPE is not a puny-local-ai setting"),
                              ("not a setting\n", "the new puny.conf line 1 is not KEY=VALUE")]:
            with self.subTest(text):
                result = self.box.run(["bin/config", "replace"], input=text)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertIn("nothing was saved", result.stderr)
                self.assertEqual(self.conf.read_text(), "# notes kept\nQWEN_PRESET=small\n")
        result = self.box.run(["bin/config", "replace"], input="QWEN_TEMP=0.5\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.conf.stat().st_mode & 0o777, 0o600)
        self.assertEqual([p.name for p in self.box.root.glob(".puny.conf.*")], [])
        result = self.box.run(["bin/config", "replace"], input="\n  \n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("every setting has its default", result.stdout)
        self.assertFalse(self.conf.exists())

    def test_switching_to_another_model_in_one_change(self):
        self.config("set", "QWEN_PRESET=small")
        result = self.config("set", "QWEN_PRESET=", "QWEN_REPO=org/m", f"QWEN_REVISION={'a' * 40}", "QWEN_CONTEXT=4096")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(config.read(self.conf), {"QWEN_REPO": "org/m", "QWEN_REVISION": "a" * 40, "QWEN_CONTEXT": "4096"})

    def test_check_and_usage(self):
        self.assertIn("OK", self.config("check").stdout)
        self.conf.write_text("QWEN_KV_BITS=2\n")
        self.assertIn("QWEN_KV_BITS must be 4 or 8", self.config("check").stderr)
        self.assertIn("usage:", self.config("bogus").stderr)
        self.assertIn("usage:", self.config("set").stderr)

    def test_serve_qwen_uses_the_file(self):
        self.box.fake_mlx_env()
        self.box.fake_model(SMALL_REPO, SMALL_REVISION)
        port = free_port()
        self.config("set", f"MLX_BASE_URL=http://127.0.0.1:{port}/v1", "QWEN_PRESET=small", "QWEN_TEMP=0.3")
        result = self.box.run(["bin/serve-qwen"])
        self.assertEqual(result.returncode, 0, result.stderr)
        args = (self.box.calls / "serve.args").read_text().split()
        self.assertEqual(args[args.index("--port") + 1], str(port))
        self.assertEqual(args[args.index("--temp") + 1], "0.3")
        self.assertIn(SMALL_REPO.replace("/", "--"), args[args.index("--model-dir") + 1])


if __name__ == "__main__":
    unittest.main()
