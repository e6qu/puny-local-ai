"""lib/verify_model.py against a fake Hugging Face tree API."""

import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import REPO, FakeServer

REVISION = "0123456789abcdef0123456789abcdef01234567"
SMALL = b'{"model_type": "test"}\n'
LARGE = b"weights" * 1000


def git_blob(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def entries():
    return [
        {"type": "file", "path": "config.json", "oid": git_blob(SMALL), "size": len(SMALL)},
        {"type": "directory", "path": "sub", "oid": "x", "size": 0},
        {"type": "file", "path": "sub/model.safetensors", "oid": "ignored", "size": len(LARGE),
         "lfs": {"oid": hashlib.sha256(LARGE).hexdigest(), "size": len(LARGE)}},
    ]


class VerifyModelTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="puny-verify-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.dir)]))
        (self.dir / "sub").mkdir()
        (self.dir / "config.json").write_bytes(SMALL)
        (self.dir / "sub" / "model.safetensors").write_bytes(LARGE)
        self.listing = entries()
        tree = f"/api/models/org/model/tree/{REVISION}"
        self.server = FakeServer({f"GET {tree}": (200, lambda handler, body: self.listing)})
        self.addCleanup(self.server.stop)
        self.endpoint = f"http://127.0.0.1:{self.server.port}"

    def verify(self, revision=REVISION):
        return subprocess.run(
            [sys.executable, "-I", str(REPO / "lib" / "verify_model.py"), "org/model", revision, str(self.dir)],
            env={"HF_ENDPOINT": self.endpoint, "PATH": "/usr/bin:/bin"},
            capture_output=True, text=True, timeout=60,
        )

    def test_matching_files_pass_and_are_recorded(self):
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Verified 2 files", result.stdout)
        manifest = (self.dir / ".verified").read_text()
        self.assertIn(f"{git_blob(SMALL)}  config.json", manifest)
        self.assertIn(f"{hashlib.sha256(LARGE).hexdigest()}  sub/model.safetensors", manifest)
        self.assertIn(f"org/model@{REVISION}", manifest)

    def test_changed_small_file_fails(self):
        (self.dir / "config.json").write_bytes(SMALL.replace(b"test", b"evil"))
        result = self.verify()
        self.assertEqual(result.returncode, 1)
        self.assertIn("hash mismatch: config.json", result.stderr)
        self.assertFalse((self.dir / ".verified").exists())

    def test_changed_large_file_fails(self):
        (self.dir / "sub" / "model.safetensors").write_bytes(LARGE[:-1] + b"X")
        result = self.verify()
        self.assertEqual(result.returncode, 1)
        self.assertIn("hash mismatch: sub/model.safetensors", result.stderr)

    def test_missing_file_fails(self):
        (self.dir / "config.json").unlink()
        result = self.verify()
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing: config.json", result.stderr)

    def test_extra_local_files_are_ignored(self):
        (self.dir / "notes.txt").write_text("mine")
        self.assertEqual(self.verify().returncode, 0)

    def test_paged_listing_is_followed(self):
        first, second = self.listing[:1], self.listing[1:]
        page2 = f"/api/models/org/model/tree/{REVISION}/page2"
        self.server.routes[f"GET {page2}"] = (200, second)
        self.listing = (first, {"Link": f'<{self.endpoint}{page2}>; rel="next"'})
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Verified 2 files", result.stdout)

    def test_empty_listing_fails(self):
        self.listing = []
        result = self.verify()
        self.assertEqual(result.returncode, 2)
        self.assertIn("lists no files", result.stderr)

    def test_unknown_revision_is_a_listing_failure(self):
        result = self.verify(revision="f" * 40)
        self.assertEqual(result.returncode, 2)
        self.assertIn("HTTP 404", result.stderr)

    def test_unreachable_server_is_a_listing_failure(self):
        self.server.stop()
        result = self.verify()
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot fetch the file list", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_only_the_next_link_is_followed(self):
        first, second = self.listing[:1], self.listing[1:]
        page2 = f"/api/models/org/model/tree/{REVISION}/page2"
        self.server.routes[f"GET {page2}"] = (200, second)
        link = f'<{self.endpoint}/elsewhere>; rel="prev", <{self.endpoint}{page2}>; rel="next"'
        self.listing = (first, {"Link": link})
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.dir / ".verified.partial").exists())

    def test_usage(self):
        result = subprocess.run([sys.executable, "-I", str(REPO / "lib" / "verify_model.py")], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Usage", result.stderr)


if __name__ == "__main__":
    unittest.main()
