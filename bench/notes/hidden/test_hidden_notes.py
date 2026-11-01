import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from notes import cli, store
from notes.model import Note

V1 = [
    {"title": "Plan", "body": "Write it down", "tags": "Work, ideas, work", "created": "30/10/2026"},
    {"title": "Café ☕", "body": "Beans", "tags": "", "created": "01/02/2025"},
]


def note_id(created, title):
    return "n-" + hashlib.sha256(f"{created}\n{title}".encode("utf-8")).hexdigest()[:10]


V2 = {"version": 2, "notes": [
    {"id": note_id("2026-10-30", "Plan"), "title": "Plan", "body": "Write it down", "tags": ["work", "ideas"], "created": "2026-10-30"},
    {"id": note_id("2025-02-01", "Café ☕"), "title": "Café ☕", "body": "Beans", "tags": [], "created": "2025-02-01"},
]}


class HiddenNotesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "notes.json"
        self.backup = Path(str(self.path) + ".v1.bak")

    def tearDown(self):
        self.dir.cleanup()

    def write_v1(self, items=V1):
        self.path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        return self.path.read_bytes()

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(["--file", str(self.path), *args])
            except SystemExit as e:
                code = e.code
        return code or 0, out.getvalue(), err.getvalue()

    def test_migrate_writes_version_2_and_keeps_a_backup(self):
        original = self.write_v1()
        code, out, err = self.run_cli("migrate")
        self.assertEqual((code, out.strip(), err), (0, "migrated 2 notes", ""))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), V2)
        self.assertEqual(self.backup.read_bytes(), original)

    def test_migrating_twice_changes_nothing(self):
        self.write_v1()
        self.run_cli("migrate")
        after = self.path.read_bytes()
        self.backup.unlink()
        code, out, _ = self.run_cli("migrate")
        self.assertEqual((code, out.strip()), (0, "already version 2"))
        self.assertEqual(self.path.read_bytes(), after)
        self.assertFalse(self.backup.exists())

    def test_an_existing_backup_stops_the_migration(self):
        original = self.write_v1()
        self.backup.write_text("older backup")
        code, out, err = self.run_cli("migrate")
        self.assertEqual(code, 1)
        self.assertIn(f"{self.backup} already exists", err)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.backup.read_text(), "older backup")

    def test_dry_run(self):
        original = self.write_v1()
        code, out, _ = self.run_cli("migrate", "--dry-run")
        self.assertEqual((code, out.strip()), (0, "would migrate 2 notes"))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(self.backup.exists())

    def test_a_bad_note_stops_the_whole_migration(self):
        items = V1 + [{"title": "Leap", "body": "", "tags": "x", "created": "29/02/2026"}]
        original = self.write_v1(items)
        code, out, err = self.run_cli("migrate")
        self.assertEqual(code, 1)
        self.assertIn("cannot migrate: ", err)
        self.assertIn("Leap", err)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(self.backup.exists())

    def test_empty_file_list(self):
        self.write_v1([])
        code, out, _ = self.run_cli("migrate")
        self.assertEqual((code, out.strip()), (0, "migrated 0 notes"))
        self.assertEqual(json.loads(self.path.read_text()), {"version": 2, "notes": []})

    def test_load_reads_both_versions_alike(self):
        self.write_v1()
        from_v1 = store.load(self.path)
        self.path.write_text(json.dumps(V2, ensure_ascii=False), encoding="utf-8")
        from_v2 = store.load(self.path)
        self.assertEqual([(n.title, n.body, n.created, n.tags) for n in from_v2],
                         [("Plan", "Write it down", date(2026, 10, 30), ["work", "ideas"]), ("Café ☕", "Beans", date(2025, 2, 1), [])])
        self.assertEqual([(n.title, n.created) for n in from_v1], [(n.title, n.created) for n in from_v2])

    def test_unsupported_version(self):
        self.path.write_text(json.dumps({"version": 3, "notes": []}))
        with self.assertRaises(ValueError) as caught:
            store.load(self.path)
        self.assertEqual(str(caught.exception), "unsupported notes version: 3")

    def test_save_writes_version_2(self):
        store.save(self.path, [Note("Plan", "Write it down", date(2026, 10, 30), ["Work", "ideas", "work"])])
        self.assertEqual(json.loads(self.path.read_text()), {"version": 2, "notes": [V2["notes"][0]]})

    def test_round_trip_keeps_unicode(self):
        notes = [Note("Café ☕", "Beans", date(2025, 2, 1), [])]
        store.save(self.path, notes)
        self.assertEqual(store.load(self.path), notes)
        self.assertIn("Café ☕", self.path.read_text(encoding="utf-8"))

    def test_other_commands_work_on_both_versions(self):
        for version in (1, 2):
            with self.subTest(version=version):
                if version == 1:
                    self.write_v1()
                else:
                    self.path.write_text(json.dumps(V2, ensure_ascii=False), encoding="utf-8")
                code, out, _ = self.run_cli("list", "--tag", "work")
                self.assertEqual((code, out), (0, "2026-10-30  Plan  [work, ideas]\n"))
                code, out, _ = self.run_cli("show", "Café ☕")
                self.assertEqual((code, out), (0, "Café ☕\nBeans\n"))

    def test_adding_to_a_version_1_file_keeps_the_old_notes(self):
        self.write_v1()
        code, out, _ = self.run_cli("add", "New", "text", "--tag", "Fresh")
        self.assertEqual(code, 0)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], 2)
        self.assertEqual([n["title"] for n in data["notes"]], ["Plan", "Café ☕", "New"])
        self.assertEqual(data["notes"][2]["tags"], ["fresh"])
        self.assertEqual(data["notes"][2]["id"], note_id(data["notes"][2]["created"], "New"))


if __name__ == "__main__":
    unittest.main()
