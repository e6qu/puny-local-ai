"""lib/sources.py, lib/models.py, lib/webui.py and bin/models, against fake Hugging Face and ModelScope servers."""

import hashlib
import http.server
import json
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from helpers import REPO, FakeServer, Sandbox, mlx_health_routes, record_served

sys.path.insert(0, str(REPO / "lib"))
import models  # noqa: E402
import sources  # noqa: E402
import webui  # noqa: E402

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

    def test_model_folders_for_both_sources(self):
        out = subprocess.run(
            ["bash", "-c", f'source "{self.box.root}/lib/common.sh"; source "{self.box.root}/lib/mlx.sh"; '
                           f'model_dir_for org/m {COMMIT}; echo; model_dir_for modelscope:org/m {COMMIT}'],
            env=self.box.env(), capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual([Path(p).name for p in out], [f"org--m@{COMMIT}", f"modelscope--org--m@{COMMIT}"])


class WebUiTest(unittest.TestCase):
    """lib/webui.py's handler, with a fake model server."""

    def setUp(self):
        self.mlx = FakeServer(mlx_health_routes())
        self.mlx.routes["POST /v1/chat/completions"] = (200, (b'data: {"choices": [{"delta": {"content": "hi"}}]}\n\ndata: [DONE]\n\n',
                                                              {"Content-Type": "text/event-stream"}))
        self.addCleanup(self.mlx.stop)
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)
        record_served(self.box, self.mlx.base_url, 65536)
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: None)
        self.origin = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.httpd.RequestHandlerClass = webui.make_handler(self.origin, self.mlx.base_url, self.box.root,
                                                            webui.Downloads(self.box.root))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def call(self, path, body=None, headers=None):
        request = urllib.request.Request(self.origin + path, data=json.dumps(body).encode() if body is not None else None,
                                         headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read().decode()

    def test_page_and_status(self):
        status, text = self.call("/")
        self.assertEqual(status, 200)
        self.assertIn("<title>puny-local-ai</title>", text)
        status, text = self.call("/api/status")
        self.assertEqual(json.loads(text)["running"], True)
        self.assertEqual(json.loads(text)["window_tokens"], 65536)

    def test_chat_is_passed_through_as_default_model(self):
        status, text = self.call("/api/chat", {"model": "other", "messages": [{"role": "user", "content": "x"}], "stream": True})
        self.assertEqual(status, 200)
        self.assertIn('"content": "hi"', text)
        self.assertEqual(json.loads(self.mlx.requests[-1][2])["model"], "default_model")

    def test_refuses_other_sites(self):
        self.assertEqual(self.call("/api/status", headers={"Host": "evil.example"})[0], 403)
        status, _ = self.call("/api/downloads", {"model": f"org/m@{COMMIT}"}, headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)

    def test_downloads_need_a_pinned_model(self):
        for spec in ("org/m", "org/m@main", "bad name@" + COMMIT):
            with self.subTest(spec):
                self.assertEqual(self.call("/api/downloads", {"model": spec})[0], 400)
        self.assertEqual(json.loads(self.call("/api/downloads")[1]), [])

    def test_chat_without_a_server(self):
        self.mlx.stop()
        status, text = self.call("/api/chat", {"messages": []})
        self.assertEqual(status, 502)
        self.assertIn("bin/serve-qwen", text)


if __name__ == "__main__":
    unittest.main()
