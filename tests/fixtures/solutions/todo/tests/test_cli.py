import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from todo.cli import main


class CliTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.file = str(Path(self.dir.name) / "todo.json")

    def tearDown(self):
        self.dir.cleanup()

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            status = main(["--file", self.file, *args])
        return status, out.getvalue()

    def test_add_then_list(self):
        self.assertEqual(self.run_cli("add", "buy milk"), (0, "added 1\n"))
        self.run_cli("add", "call mum")
        self.assertEqual(self.run_cli("list"), (0, "1. [ ] buy milk\n2. [ ] call mum\n"))

    def test_done_and_pending(self):
        self.run_cli("add", "a")
        self.run_cli("add", "b")
        self.assertEqual(self.run_cli("done", "1"), (0, "done 1\n"))
        self.assertEqual(self.run_cli("list", "--pending"), (0, "2. [ ] b\n"))
        self.assertEqual(self.run_cli("done", "7"), (1, ""))


if __name__ == "__main__":
    unittest.main()
