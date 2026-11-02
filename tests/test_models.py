"""lib/sources.py, lib/models.py and bin/models, against fake Hugging Face and ModelScope servers."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import REPO, FakeServer, Sandbox

sys.path.insert(0, str(REPO / "lib"))
import models  # noqa: E402
import sources  # noqa: E402

COMMIT = "a" * 40
OLD = "b" * 40
WEIGHTS = b"w" * 4096
CONFIG = {"model_type": "qwen3", "num_hidden_layers": 2, "num_attention_heads": 2, "num_key_value_heads": 1,
          "hidden_size": 8, "head_dim": 4, "max_position_embeddings": 40960}
TEMPLATE = "{% for tool in tools %}{{ tool }}{% endfor %}<tool_call>\n<function=x>"


def git_blob(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class SourcesCase(unittest.TestCase):
    """A fake Hugging Face and a fake ModelScope with one small model each."""

    def setUp(self):
        config = json.dumps(CONFIG).encode()
        self.hf = FakeServer({
            "GET /api/models": (200, [{"id": "mlx-community/Tiny-4bit", "downloads": 9, "likes": 1,
                                       "lastModified": "2026-10-01T00:00:00Z", "pipeline_tag": "text-generation",
                                       "sha": COMMIT}]),
            "GET /api/models/mlx-community/Tiny-4bit/revision/main": (200, {"sha": COMMIT}),
            f"GET /api/models/mlx-community/Tiny-4bit/tree/{COMMIT}": (200, [
                {"type": "file", "path": "config.json", "size": len(config), "oid": git_blob(config)},
                {"type": "file", "path": "chat_template.jinja", "size": len(TEMPLATE), "oid": git_blob(TEMPLATE.encode())},
                {"type": "file", "path": "model.safetensors", "size": len(WEIGHTS),
                 "oid": "x", "lfs": {"oid": hashlib.sha256(WEIGHTS).hexdigest()}},
            ]),
            f"GET /mlx-community/Tiny-4bit/resolve/{COMMIT}/config.json": (200, config),
            f"GET /mlx-community/Tiny-4bit/resolve/{COMMIT}/chat_template.jinja": (200, TEMPLATE.encode()),
        })
        self.ms = FakeServer({
            "GET /openapi/v1/models": (200, {"success": True, "data": {"models": [
                {"id": "mlx-community/Tiny-4bit", "downloads": 5, "tags": ["library:mlx"], "file_size": 4200,
                 "last_modified": "2026-09-01", "tasks": ["text-generation"]},
                {"id": "Qwen/Qwen3-8B", "downloads": 500, "tags": ["library:safetensors"]},
            ]}}),
            "GET /api/v1/models/mlx-community/Tiny-4bit/commits": (200, {"Code": 200, "Data": {"Commit": [{"Id": COMMIT}, {"Id": OLD}]}}),
            "GET /api/v1/models/mlx-community/Tiny-4bit/repo/files": (200, {"Code": 200, "Data": {"Files": [
                {"Path": "config.json", "Size": len(config), "Sha256": hashlib.sha256(config).hexdigest(), "Type": "blob"},
                {"Path": "model.safetensors", "Size": len(WEIGHTS), "Sha256": hashlib.sha256(WEIGHTS).hexdigest(), "Type": "blob"},
                {"Path": "sub", "Size": 0, "Type": "tree"},
            ]}}),
            f"GET /models/mlx-community/Tiny-4bit/resolve/{COMMIT}/config.json": (200, config),
            f"GET /models/mlx-community/Tiny-4bit/resolve/{COMMIT}/model.safetensors": (200, WEIGHTS),
        })
        for server in (self.hf, self.ms):
            self.addCleanup(server.stop)
        for name, server in (("HF", self.hf), ("MODELSCOPE", self.ms)):
            self.addCleanup(setattr, sources, name, getattr(sources, name))
            setattr(sources, name, f"http://127.0.0.1:{server.port}")


class SourcesTest(SourcesCase):
    def test_names(self):
        self.assertEqual(sources.split("org/m"), ("huggingface", "org/m"))
        self.assertEqual(sources.split("modelscope:org/m"), ("modelscope", "org/m"))
        for bad in ("m", "org/m/x", "modelscope:", "../x/y", "org/m;rm"):
            with self.subTest(bad), self.assertRaises(sources.SourceError):
                sources.split(bad)

    def test_hugging_face_search_filters_on_mlx(self):
        results = sources.search("tiny", "huggingface", 5)
        self.assertEqual([r["name"] for r in results], ["mlx-community/Tiny-4bit"])
        query = self.hf.requests[-1][1]
        self.assertIn("filter=mlx", query)
        self.assertIn("search=tiny", query)

    def test_modelscope_search_keeps_only_mlx(self):
        results = sources.search("tiny", "modelscope", 5)
        self.assertEqual([r["name"] for r in results], ["modelscope:mlx-community/Tiny-4bit"])
        self.assertEqual(results[0]["size_bytes"], 4200)

    def test_head_commits(self):
        self.assertEqual(sources.head_commit("mlx-community/Tiny-4bit"), COMMIT)
        self.assertEqual(sources.head_commit("modelscope:mlx-community/Tiny-4bit"), COMMIT)
        self.assertEqual(sources.head_commit("modelscope:mlx-community/Tiny-4bit", OLD), OLD)
        with self.assertRaises(sources.SourceError):
            sources.head_commit("modelscope:mlx-community/Tiny-4bit", "v1")

    def test_listings_and_failures(self):
        files = sources.listed_files("modelscope:mlx-community/Tiny-4bit", COMMIT)
        self.assertEqual([f["path"] for f in files], ["config.json", "model.safetensors"])
        self.assertIn(f"Revision={COMMIT}", self.ms.requests[-1][1])
        self.ms.routes["GET /api/v1/models/mlx-community/Tiny-4bit/repo/files"] = (200, {"Code": 10990101004, "Message": "no"})
        with self.assertRaises(sources.SourceError):
            sources.listed_files("modelscope:mlx-community/Tiny-4bit", COMMIT)
        with self.assertRaises(sources.SourceError):
            sources.listed_files("mlx-community/Missing", COMMIT)

    def test_modelscope_download_and_verify(self):
        name = "modelscope:mlx-community/Tiny-4bit"
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder)
            files = sources.listed_files(name, COMMIT)
            sources.download(name, COMMIT, dest, files, progress=lambda message: None)
            self.assertEqual((dest / "model.safetensors").read_bytes(), WEIGHTS)
            self.assertEqual(list(dest.glob("*.partial")), [])
            env = {"MODELSCOPE_ENDPOINT": sources.MODELSCOPE, "PATH": "/usr/bin:/bin"}
            verify = [sys.executable, "-I", str(REPO / "lib" / "verify_model.py"), name, COMMIT, folder]
            result = subprocess.run(verify, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            (dest / "model.safetensors").write_bytes(b"x" * len(WEIGHTS))
            self.assertEqual(subprocess.run(verify, env=env, capture_output=True, text=True).returncode, 1)

    def test_short_download_fails(self):
        self.ms.routes[f"GET /models/mlx-community/Tiny-4bit/resolve/{COMMIT}/model.safetensors"] = (200, b"short")
        name = "modelscope:mlx-community/Tiny-4bit"
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(sources.SourceError) as caught:
                sources.download(name, COMMIT, Path(folder), sources.listed_files(name, COMMIT), progress=lambda m: None)
            self.assertIn("sent 5 bytes", str(caught.exception))
            self.assertFalse((Path(folder) / "model.safetensors").exists())


class ModelsTest(SourcesCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, models, "gpu_limit_bytes", models.gpu_limit_bytes)
        models.gpu_limit_bytes = lambda: 16 * 2**30
        self.addCleanup(setattr, models, "STATE", models.STATE)
        self.state = Path(tempfile.mkdtemp())
        models.STATE = self.state

    def test_info_checks_fit_architecture_and_tool_calls(self):
        info = models.info("mlx-community/Tiny-4bit")
        self.assertEqual(info["commit"], COMMIT)
        self.assertEqual(info["weights_bytes"], len(WEIGHTS))
        self.assertTrue(info["architecture_supported"])
        self.assertEqual(info["tool_parser"], "qwen3_coder")
        self.assertEqual(info["config_context_tokens"], 40960)
        self.assertTrue(info["fits"])
        self.assertEqual(info["window_tokens"], 40960)
        self.assertIn(f"QWEN_REPO=mlx-community/Tiny-4bit QWEN_REVISION={COMMIT} QWEN_CONTEXT=40960", info["serve_command"])
        self.assertFalse(info["downloaded"])

    def test_info_leaves_out_the_weights_the_model_drops(self):
        import struct
        header = json.dumps({"vision_tower.w": {"dtype": "U8", "shape": [300], "data_offsets": [0, 300]},
                             "language_model.model.w": {"dtype": "U8", "shape": [1000], "data_offsets": [300, 1300]}}).encode()
        blob = struct.pack("<Q", len(header)) + header + b"\0" * 1300
        ranges = []

        def serve_range(handler, body):
            start, end = (int(n) for n in handler.headers["Range"].removeprefix("bytes=").split("-"))
            ranges.append((start, end))
            return blob[start:end + 1], {"Content-Range": f"bytes {start}-{end}/{len(blob)}"}

        config = json.dumps(CONFIG | {"model_type": "muse_glimmer"}).encode()
        self.hf.routes[f"GET /mlx-community/Tiny-4bit/resolve/{COMMIT}/config.json"] = (200, config)
        self.hf.routes[f"GET /mlx-community/Tiny-4bit/resolve/{COMMIT}/model.safetensors"] = (206, serve_range)
        tree = self.hf.routes[f"GET /api/models/mlx-community/Tiny-4bit/tree/{COMMIT}"][1]
        tree[2]["size"] = len(blob)
        self.assertEqual(models.info("mlx-community/Tiny-4bit")["weights_bytes"], len(blob) - 300)
        self.assertEqual(ranges, [(0, 7), (8, 8 + len(header) - 1)], "only the header is read")

        # A server that ignores the range is refused before the body is read.
        self.hf.routes[f"GET /mlx-community/Tiny-4bit/resolve/{COMMIT}/model.safetensors"] = (200, blob)
        with self.assertRaisesRegex(sources.SourceError, "did not answer with bytes 0-7"):
            models.info("mlx-community/Tiny-4bit")

    def test_info_on_a_model_that_does_not_fit(self):
        models.gpu_limit_bytes = lambda: len(WEIGHTS)
        info = models.info("mlx-community/Tiny-4bit")
        self.assertFalse(info["fits"])

    def test_info_on_an_unknown_architecture(self):
        config = json.dumps(CONFIG | {"model_type": "nonexistent_arch"}).encode()
        self.hf.routes[f"GET /mlx-community/Tiny-4bit/resolve/{COMMIT}/config.json"] = (200, config)
        self.assertFalse(models.info("mlx-community/Tiny-4bit")["architecture_supported"])

    def test_downloaded_lists_both_sources(self):
        for folder in (f"mlx-community--Tiny-4bit@{COMMIT}", f"modelscope--mlx-community--Tiny-4bit@{OLD}"):
            (self.state / "models" / folder).mkdir(parents=True)
        (self.state / "models" / f"mlx-community--Tiny-4bit@{COMMIT}" / ".verified").write_text("ok\n")
        found = models.downloaded()
        self.assertEqual([(m["name"], m["commit"], m["verified"]) for m in found], [
            ("mlx-community/Tiny-4bit", COMMIT, True),
            ("modelscope:mlx-community/Tiny-4bit", OLD, False),
        ])
        self.assertEqual(str(models.model_dir("modelscope:mlx-community/Tiny-4bit", OLD)), found[1]["folder"])


class ModelsCommandTest(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)
        self.box.fake_mlx_env()

    def test_usage(self):
        for args in (["bin/models"], ["bin/models", "bogus"], ["bin/models", "download"]):
            with self.subTest(args):
                result = self.box.run(args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("usage:", result.stderr)

    def test_presets(self):
        result = self.box.run(["bin/models", "presets", "--json"])
        self.assertEqual(result.returncode, 0, result.stderr)
        presets = {p["name"]: p for p in json.loads(result.stdout)}
        self.assertEqual(presets["small"]["repo"], "mlx-community/Qwen3.5-4B-4bit")
        self.assertEqual(presets["small"]["context_tokens"], 262144)
        self.assertFalse(presets["small"]["downloaded"])
        folder = self.box.root / "state" / "models" / f"mlx-community--Qwen3.5-4B-4bit@{presets['small']['revision']}"
        folder.mkdir(parents=True)
        (folder / ".verified").touch()
        presets = {p["name"]: p for p in json.loads(self.box.run(["bin/models", "presets", "--json"]).stdout)}
        self.assertTrue(presets["small"]["downloaded"])
        self.assertIn("kat           not downloaded", self.box.run(["bin/models", "presets"]).stdout)

    def test_remove(self):
        folder = self.box.root / "state" / "models" / f"org--m@{COMMIT}"
        (folder / "weights").mkdir(parents=True)
        for spec, message in [("org/m", "usage:"), ("org/m@main", "full 40-character commit"),
                              (f"../m@{COMMIT}", "not a model name"), (f"org/other@{COMMIT}", "is not downloaded")]:
            with self.subTest(spec):
                result = self.box.run(["bin/models", "remove", spec])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
        # A running server that uses it keeps it.
        server = subprocess.Popen(["/usr/bin/python3", "-c", "import time; time.sleep(60)", "--model-dir", str(folder)])
        self.addCleanup(server.wait)
        self.addCleanup(server.kill)
        record = self.box.root / "state" / "served" / "port-8081"
        record.parent.mkdir(parents=True)
        record.write_text(f"{server.pid} 4096\n")
        result = self.box.run(["bin/models", "remove", f"org/m@{COMMIT}"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stop it first", result.stderr)
        self.assertTrue(folder.exists())
        server.kill()
        server.wait()
        result = self.box.run(["bin/models", "remove", f"org/m@{COMMIT}"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(folder.exists())

    def test_model_folders_for_both_sources(self):
        out = subprocess.run(
            ["bash", "-c", f'source "{self.box.root}/lib/common.sh"; source "{self.box.root}/lib/mlx.sh"; '
                           f'model_dir_for org/m {COMMIT}; echo; model_dir_for modelscope:org/m {COMMIT}'],
            env=self.box.env(), capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual([Path(p).name for p in out], [f"org--m@{COMMIT}", f"modelscope--org--m@{COMMIT}"])


if __name__ == "__main__":
    unittest.main()
