import contextlib
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from notes import cli, store
from notes.model import Note


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "notes.json"

    def tearDown(self):
        self.dir.cleanup()

    def test_round_trip(self):
        notes = [Note("Plan", "Write it down", date(2026, 10, 30), ["work", "ideas"])]
        store.save(self.path, notes)
        self.assertEqual(store.load(self.path), notes)

    def test_file_format(self):
        store.save(self.path, [Note("Plan", "b", date(2026, 1, 2), ["X", "x"])])
        data = json.loads(self.path.read_text())
        self.assertEqual(data["version"], 2)
        self.assertEqual(data["notes"][0]["tags"], ["x"])
        self.assertEqual(data["notes"][0]["created"], "2026-01-02")
        self.assertRegex(data["notes"][0]["id"], r"^n-[0-9a-f]{10}$")

    def test_reads_version_1(self):
        self.path.write_text(json.dumps([{"title": "a", "body": "b", "tags": "T, t", "created": "02/01/2026"}]))
        self.assertEqual(store.load(self.path), [Note("a", "b", date(2026, 1, 2), ["t"])])

    def test_missing_file(self):
        self.assertEqual(store.load(self.path), [])

    def test_migrate(self):
        self.path.write_text(json.dumps([{"title": "a", "body": "b", "tags": "", "created": "02/01/2026"}]))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["--file", str(self.path), "migrate"]), 0)
        self.assertEqual(out.getvalue(), "migrated 1 notes\n")
        self.assertTrue(Path(str(self.path) + ".v1.bak").exists())
        self.assertEqual(json.loads(self.path.read_text())["version"], 2)


if __name__ == "__main__":
    unittest.main()
