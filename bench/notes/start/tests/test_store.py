import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from notes import store
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
        store.save(self.path, [Note("Plan", "b", date(2026, 1, 2), ["x"])])
        self.assertEqual(json.loads(self.path.read_text()), [{"title": "Plan", "body": "b", "tags": "x", "created": "02/01/2026"}])

    def test_missing_file(self):
        self.assertEqual(store.load(self.path), [])


if __name__ == "__main__":
    unittest.main()
