import copy
import unittest

from solution import PatchError, apply_patch


class ApplyPatchTest(unittest.TestCase):
    def fails_at(self, document, operations, index):
        before = copy.deepcopy(document)
        ops_before = copy.deepcopy(operations)
        with self.assertRaises(PatchError) as caught:
            apply_patch(document, operations)
        self.assertEqual(caught.exception.index, index)
        self.assertEqual(document, before, "a failed patch must not change the document")
        self.assertEqual(operations, ops_before)

    def test_example(self):
        doc = {"a": [1, 2], "b": {"c~d": True}}
        result = apply_patch(doc, [
            {"op": "add", "path": "/a/-", "value": 3},
            {"op": "move", "from": "/b/c~0d", "path": "/flag"},
            {"op": "test", "path": "/a", "value": [1, 2, 3.0]},
        ])
        self.assertEqual(result, {"a": [1, 2, 3], "b": {}, "flag": True})
        self.assertEqual(doc, {"a": [1, 2], "b": {"c~d": True}})

    def test_add(self):
        self.assertEqual(apply_patch({"a": 1}, [{"op": "add", "path": "/b", "value": [1]}]), {"a": 1, "b": [1]})
        self.assertEqual(apply_patch({"a": 1}, [{"op": "add", "path": "/a", "value": 2}]), {"a": 2})
        self.assertEqual(apply_patch([1, 3], [{"op": "add", "path": "/1", "value": 2}]), [1, 2, 3])
        self.assertEqual(apply_patch([1], [{"op": "add", "path": "/1", "value": 2}]), [1, 2])
        self.assertEqual(apply_patch({}, [{"op": "add", "path": "", "value": [7]}]), [7])
        self.assertEqual(apply_patch({}, [{"op": "add", "path": "/", "value": 1}]), {"": 1})
        self.assertEqual(apply_patch({"x/y": {}}, [{"op": "add", "path": "/x~1y/z", "value": 1}]), {"x/y": {"z": 1}})

    def test_remove_and_replace(self):
        self.assertEqual(apply_patch({"a": 1, "b": 2}, [{"op": "remove", "path": "/a"}]), {"b": 2})
        self.assertEqual(apply_patch([1, 2, 3], [{"op": "remove", "path": "/0"}]), [2, 3])
        self.assertEqual(apply_patch({"a": [1, 2]}, [{"op": "replace", "path": "/a/1", "value": None}]), {"a": [1, None]})
        self.assertEqual(apply_patch({"a": 1}, [{"op": "replace", "path": "", "value": "all"}]), "all")

    def test_move_and_copy(self):
        self.assertEqual(apply_patch([1, 2, 3], [{"op": "move", "from": "/0", "path": "/2"}]), [2, 3, 1])
        self.assertEqual(apply_patch({"a": {"b": 1}}, [{"op": "move", "from": "/a/b", "path": "/c"}]), {"a": {}, "c": 1})
        self.assertEqual(apply_patch({"a": {"b": 1}}, [{"op": "move", "from": "/a", "path": "/a"}]), {"a": {"b": 1}})
        result = apply_patch({"a": {"b": [1]}}, [{"op": "copy", "from": "/a", "path": "/c"}, {"op": "add", "path": "/c/b/-", "value": 2}])
        self.assertEqual(result, {"a": {"b": [1]}, "c": {"b": [1, 2]}})

    def test_values_are_copied_in(self):
        value = {"deep": [1]}
        operations = [{"op": "add", "path": "/v", "value": value}]
        result = apply_patch({}, operations)
        result["v"]["deep"].append(2)
        self.assertEqual(value, {"deep": [1]})
        self.assertEqual(operations, [{"op": "add", "path": "/v", "value": {"deep": [1]}}])

    def test_success_leaves_the_input_alone(self):
        doc = {"a": [1, {"b": 2}]}
        apply_patch(doc, [{"op": "replace", "path": "/a/1/b", "value": 3}, {"op": "remove", "path": "/a/0"}])
        self.assertEqual(doc, {"a": [1, {"b": 2}]})

    def test_json_equality_in_test(self):
        self.assertEqual(apply_patch({"n": 1}, [{"op": "test", "path": "/n", "value": 1.0}]), {"n": 1})
        self.assertEqual(apply_patch({"o": {"a": 1, "b": [True]}}, [{"op": "test", "path": "/o", "value": {"b": [True], "a": 1}}]), {"o": {"a": 1, "b": [True]}})
        self.fails_at({"n": 1}, [{"op": "test", "path": "/n", "value": True}], 0)
        self.fails_at({"n": False}, [{"op": "test", "path": "/n", "value": 0}], 0)
        self.fails_at({"n": [1, True]}, [{"op": "test", "path": "/n", "value": [True, 1]}], 0)
        self.fails_at({"n": [0]}, [{"op": "test", "path": "/n", "value": [False]}], 0)
        self.fails_at({"n": {"a": 1}}, [{"op": "test", "path": "/n", "value": {"a": 1, "b": None}}], 0)

    def test_errors_carry_the_index_and_change_nothing(self):
        doc = {"a": [1, 2], "b": {}}
        self.fails_at(doc, [{"op": "add", "path": "/a/-", "value": 3}, {"op": "remove", "path": "/missing"}], 1)
        self.fails_at(doc, [{"op": "replace", "path": "/b/x", "value": 1}], 0)
        self.fails_at(doc, [{"op": "add", "path": "/a/3", "value": 1}], 0)
        self.fails_at(doc, [{"op": "add", "path": "/a/01", "value": 1}], 0)
        self.fails_at(doc, [{"op": "remove", "path": "/a/-"}], 0)
        self.fails_at(doc, [{"op": "add", "path": "/a/-1", "value": 1}], 0)
        self.fails_at(doc, [{"op": "add", "path": "a", "value": 1}], 0)
        self.fails_at(doc, [{"op": "add", "path": "/x/y", "value": 1}], 0)
        self.fails_at(doc, [{"op": "move", "from": "/b", "path": "/b/inner"}], 0)
        self.fails_at(doc, [{"op": "copy", "from": "/nope", "path": "/c"}], 0)
        self.fails_at(doc, [{"op": "jump", "path": "/a"}], 0)
        self.fails_at(doc, [{"op": "add", "path": "/c"}], 0)
        self.fails_at(doc, [{"path": "/c", "value": 1}], 0)
        self.fails_at(doc, [{"op": "add", "path": "/b/~2", "value": 1}], 0)
        self.fails_at(doc, [{"op": "test", "path": "/a/0", "value": 1}, {"op": "add", "path": "/z", "value": 0}, {"op": "test", "path": "/z", "value": 1}], 2)

    def test_scalars_have_no_children(self):
        self.fails_at({"a": 5}, [{"op": "add", "path": "/a/b", "value": 1}], 0)
        self.fails_at({"a": "text"}, [{"op": "remove", "path": "/a/0"}], 0)

    def test_patch_error_is_a_value_error(self):
        self.assertTrue(issubclass(PatchError, ValueError))

    def test_long_patch(self):
        operations = [{"op": "add", "path": "/items/-", "value": {"id": i}} for i in range(3000)]
        operations += [{"op": "remove", "path": "/items/0"} for _ in range(1000)]
        result = apply_patch({"items": []}, operations)
        self.assertEqual(len(result["items"]), 2000)
        self.assertEqual(result["items"][0], {"id": 1000})


if __name__ == "__main__":
    unittest.main()
