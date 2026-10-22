import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from todo.cli import main


class HiddenTodoTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.file = Path(self.dir.name) / "todo.json"

    def tearDown(self):
        self.dir.cleanup()

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = main(["--file", str(self.file), *args])
        return status, out.getvalue(), err.getvalue()

    def test_done_and_list_formats(self):
        for text in ("a", "b", "c"):
            self.run_cli("add", text)
        self.assertEqual(self.run_cli("done", "2"), (0, "done 2\n", ""))
        self.assertEqual(self.run_cli("list")[1], "1. [ ] a\n2. [x] b\n3. [ ] c\n")
        self.assertEqual(self.run_cli("list", "--pending")[1], "1. [ ] a\n3. [ ] c\n")

    def test_done_persists(self):
        self.run_cli("add", "a")
        self.run_cli("done", "1")
        self.assertTrue(json.loads(self.file.read_text())[0]["done"])

    def test_unknown_id(self):
        self.run_cli("add", "a")
        before = self.file.read_text()
        status, out, err = self.run_cli("done", "9")
        self.assertEqual((status, out), (1, ""))
        self.assertEqual(err.strip(), "no item 9")
        self.assertEqual(self.file.read_text(), before)

    def test_old_items_without_done_field(self):
        self.file.write_text(json.dumps([{"id": 1, "text": "old"}]))
        self.assertEqual(self.run_cli("list")[1], "1. [ ] old\n")
        self.assertEqual(self.run_cli("list", "--pending")[1], "1. [ ] old\n")

    def test_add_still_works(self):
        self.assertEqual(self.run_cli("add", "x")[:2], (0, "added 1\n"))


if __name__ == "__main__":
    unittest.main()
