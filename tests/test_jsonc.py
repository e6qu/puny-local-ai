import json
import sys
import unittest

from helpers import REPO

sys.path.insert(0, str(REPO / "lib"))
import jsonc  # noqa: E402

ENTRY = {"name": "puny-local-ai", "models": {"m": {"limit": [1, 2]}}}

DOCUMENTS = {
    "empty object": "{}",
    "empty object on two lines": "{\n}\n",
    "one line": '{"a": 1}',
    "comments and trailing commas": (
        "{\n"
        "  // line comment\n"
        '  "$schema": "https://opencode.ai/config.json", /* block */\n'
        '  "providers": {\n'
        '    "other": {"k": [1, 2,], "url": "http://x//y"},\n'
        "  },\n"
        "}\n"
    ),
    "tabs": '{\n\t"providers": {\n\t\t"x": 1\n\t}\n}',
    "empty providers": '{\n  "providers": {}\n}\n',
    "four spaces": '{\n    "a": {\n        "b": true\n    },\n    "c": null\n}',
    "string with comment markers": '{"s": "/* not */ // a comment", "providers": {"x": 1}}',
    "escaped quotes": '{"s": "say \\"hi\\" \\\\", "n": -1.5e3}',
    "no final newline, CRLF": '{\r\n  "a": 1\r\n}',
    "unicode": '{"name": "Mârza ✓"}',
}


def add(text):
    root = jsonc.parse(text)
    member = root.get("providers")
    if member is None:
        return jsonc.insert_first(text, root, "providers", {"puny": ENTRY})
    return jsonc.insert_first(text, member.value, "puny", ENTRY)


class ParseTest(unittest.TestCase):
    def test_matches_json_for_plain_json(self):
        for name, text in DOCUMENTS.items():
            if "comment" in name and "markers" not in name:
                continue
            with self.subTest(name):
                self.assertEqual(jsonc.plain(jsonc.parse(text)), json.loads(text))

    def test_comments_and_trailing_commas(self):
        parsed = jsonc.plain(jsonc.parse(DOCUMENTS["comments and trailing commas"]))
        self.assertEqual(
            parsed,
            {
                "$schema": "https://opencode.ai/config.json",
                "providers": {"other": {"k": [1, 2], "url": "http://x//y"}},
            },
        )

    def test_rejects_invalid_documents(self):
        for text in ["", "[]", "{", '{"a" 1}', '{"a": 1} x', '{"a": tru}', '{"a": "x\n"}', "{/* open", '{"a": 1,,}']:
            with self.subTest(text=text):
                with self.assertRaises(jsonc.JsoncError):
                    jsonc.parse(text)

    def test_duplicate_keys_are_an_error_when_looked_up(self):
        root = jsonc.parse('{"a": 1, "a": 2}')
        with self.assertRaises(jsonc.JsoncError):
            root.get("a")


class EditTest(unittest.TestCase):
    def test_insert_adds_exactly_the_entry(self):
        for name, text in DOCUMENTS.items():
            with self.subTest(name):
                new, _ = add(text)
                before = jsonc.plain(jsonc.parse(text))
                before.setdefault("providers", {})["puny"] = ENTRY
                self.assertEqual(jsonc.plain(jsonc.parse(new)), before)

    def test_insert_keeps_every_other_byte(self):
        for name, text in DOCUMENTS.items():
            with self.subTest(name):
                new, inserted = add(text)
                position = new.index(inserted)
                self.assertEqual(new[:position] + new[position + len(inserted):], text)

    def test_remove_inserted_restores_the_bytes(self):
        for name, text in DOCUMENTS.items():
            with self.subTest(name):
                new, inserted = add(text)
                self.assertEqual(jsonc.remove_inserted(new, inserted), text)

    def test_remove_inserted_keeps_later_edits_elsewhere(self):
        text = DOCUMENTS["comments and trailing commas"]
        new, inserted = add(text)
        edited = new.replace("// line comment", "// edited later")
        self.assertEqual(jsonc.remove_inserted(edited, inserted), text.replace("// line comment", "// edited later"))

    def test_remove_inserted_refuses_when_the_text_changed(self):
        new, inserted = add(DOCUMENTS["four spaces"])
        self.assertIsNone(jsonc.remove_inserted(new.replace('"limit"', '"limits"'), inserted))
        self.assertIsNone(jsonc.remove_inserted(new + new, inserted))

    def test_new_text_uses_the_files_indentation(self):
        new, inserted = add(DOCUMENTS["tabs"])
        self.assertIn('\n\t\t"puny": {', new)
        new, inserted = add(DOCUMENTS["four spaces"])
        self.assertIn('\n    "providers": {', new)

    def test_remove_by_structure(self):
        cases = {
            "first of several": ('{\n  "p": {\n    "puny": 1,\n    "b": 2\n  }\n}', '{\n  "p": {\n    "b": 2\n  }\n}'),
            "last of several": ('{\n  "p": {\n    "b": 2,\n    "puny": 1\n  }\n}', '{\n  "p": {\n    "b": 2\n  }\n}'),
            "middle": ('{"p": {"a": 1, "puny": 1, "b": 2}}', '{"p": {"a": 1, "b": 2}}'),
            "only": ('{\n  "p": {\n    "puny": 1\n  }\n}', '{\n  "p": {\n  }\n}'),
            "trailing comma": ('{\n  "p": {\n    "b": 2,\n    "puny": 1,\n  }\n}', '{\n  "p": {\n    "b": 2,\n  }\n}'),
            "one line": ('{"p": {"puny": 1}}', '{"p": {}}'),
        }
        for name, (text, expected) in cases.items():
            with self.subTest(name):
                root = jsonc.parse(text)
                result = jsonc.remove(text, root.get("p").value, "puny")
                self.assertEqual(result, expected)
                self.assertEqual(jsonc.plain(jsonc.parse(result)), json.loads(expected.replace(",\n  }", "\n  }")))

    def test_byte_order_mark_and_windows_line_endings(self):
        text = "\ufeff{\r\n  \"providers\": {\r\n    \"x\": 1\r\n  }\r\n}\r\n"
        new, inserted = add(text)
        self.assertNotIn("\n", new.replace("\r\n", ""))
        self.assertEqual(jsonc.remove_inserted(new, inserted), text)
        self.assertEqual(jsonc.plain(jsonc.parse(new))["providers"]["x"], 1)

    def test_removing_the_last_member_keeps_comments(self):
        text = '{\n  "p": {\n    "mine": {}, // keep\n    // notes\n    "puny": {"a": 1}\n  }\n}\n'
        root = jsonc.parse(text)
        result = jsonc.remove(text, root.get("p").value, "puny")
        self.assertEqual(result, '{\n  "p": {\n    "mine": {} // keep\n    // notes\n  }\n}\n')

    def test_bad_strings_and_deep_nesting_are_jsonc_errors(self):
        for text in ('{"a": "\\q"}', '{"a": "x\ty"}', '{"a": ' * 5000 + "1" + "}" * 5000):
            with self.subTest(text=text[:20]), self.assertRaises(jsonc.JsoncError):
                jsonc.parse(text)

    def test_remove_missing_member_is_an_error(self):
        root = jsonc.parse('{"a": 1}')
        with self.assertRaises(jsonc.JsoncError):
            jsonc.remove('{"a": 1}', root, "b")


if __name__ == "__main__":
    unittest.main()
