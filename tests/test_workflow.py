"""./bin/workflow and lib/workflow.py: graphs, their checks, and runs."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import REPO, ServerCase

sys.path.insert(0, str(REPO / "lib"))
import workflow  # noqa: E402

# A stand-in agent that runs the lines of its prompt that start with "STUB:"
# as shell commands, so each test's prompts say what the agent does.
STEP_STUB = r'''
if [[ "${1:-}" == --version ]]; then echo "2.1.1"; exit 0; fi
prompt="${@: -1}"
printf '%s\n' "$prompt" >> "$STUB_CALLS/prompts"
printf '%s\n' "$(basename "$0")" >> "$STUB_CALLS/agents"
while IFS= read -r line; do
  [[ "$line" == STUB:* ]] && eval "${line#STUB:}"
done <<< "$prompt"
exit 0
'''

# textstats.py as the goal example's tests want it.
TEXTSTATS = '''import re
from collections import Counter


def words(text):
    return [w.strip(".,!?;:'\\"").lower() for w in text.split() if w.strip(".,!?;:'\\"")]


def word_count(text):
    return len(words(text))


def most_common(text, n):
    return sorted(Counter(words(text)).items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def sentences(text):
    return len(re.findall(r"[^.!?]*[.!?]+", text))
'''


def graph(states, start=None, max_steps=20, **extra):
    return {"start": start or next(iter(states)), "max_steps": max_steps, "states": states, **extra}


class GraphCheckTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="puny-graph-"))
        self.addCleanup(shutil.rmtree, self.dir)
        (self.dir / "a.md").write_text("do a\n")

    def refused(self, g):
        with self.assertRaises(workflow.WorkflowError) as caught:
            workflow.check(g, self.dir)
        return str(caught.exception)

    def test_the_bundled_workflows_are_valid(self):
        for path in sorted((REPO / "workflows").glob("*/graph.jsonc")):
            with self.subTest(path.parent.name):
                g = workflow.load(path.parent)
                self.assertTrue(g["description"])

    def test_the_example_in_the_docs_is_valid(self):
        import re
        text = (REPO / "docs" / "workflows.md").read_text()
        (self.dir / "graph.jsonc").write_text(re.search(r"```jsonc\n(.*?)```", text, re.S).group(1))
        g = workflow.jsonc.plain(workflow.jsonc.parse((self.dir / "graph.jsonc").read_text()))
        for state in g["states"].values():
            if "agent" in state:
                (self.dir / state["agent"]).write_text("x\n")
        workflow.load(self.dir)

    def test_a_loop_needs_a_bound(self):
        loop = {"a": {"agent": "a.md", "next": "c"},
                "c": {"command": "true", "pass": "done", "fail": "a"},
                "done": {"end": "success"}}
        self.assertIn("the loop a -> c -> a has no state with max_visits", self.refused(graph(loop)))
        loop["a"]["max_visits"] = 3
        workflow.check(graph(loop), self.dir)

    def test_mistakes_are_named(self):
        end = {"done": {"end": "success"}}
        cases = [
            (graph({"a": {"agent": "a.md", "next": "nowhere"}, **end}), "leads to 'nowhere', which is not a state"),
            (graph({"a": {"agent": "a.md", "next": "done"}, "b": {"agent": "a.md", "next": "done"}, **end}),
             "state 'b' cannot be reached"),
            (graph({"a": {"agent": "a.md", "next": "f"}, "f": {"end": "failure"}}), "no success end can be reached"),
            (graph({"a": {"agent": "a.md"}, **end}), "needs either next or choices"),
            (graph({"a": {"agent": "a.md", "choices": {"Yes!": "done", "no": "done"}}, **end}), "must be one lowercase word"),
            (graph({"a": {"agent": "a.md", "choices": {"yes": "done"}}, **end}), "at least two words"),
            (graph({"a": {"agent": "missing.md", "next": "done"}, **end}), "missing.md does not exist"),
            (graph({"a": {"agent": "a.md", "next": "done", "exhausted": "done"}, **end}), "exhausted needs max_visits"),
            (graph({"a": {"agent": "a.md", "next": "done", "using": "claude"}, **end}), "using must be one of"),
            (graph({"a": {"agent": "a.md", "next": "done", "writes": ["../x"]}, **end}), "must be a path inside the folder"),
            (graph({"a": {"agent": "a.md", "next": "done", "pass": "done"}, **end}), "'pass' does not belong in an agent state"),
            (graph({"a": {"command": "true", "pass": "done", "fail": "done", "failed": "done"}, **end}),
             "'failed' does not belong in a command state"),
            (graph({"a": {"agent": "a.md", "command": "true", "next": "done"}, **end}), "exactly one of"),
            (graph({"a": {"command": "true", "pass": "done"}, **end}), "needs 'fail'"),
            (graph({"a": {"end": "maybe"}}), "end must be success or failure"),
            (graph({"A": {"end": "success"}}), "lowercase letters"),
            (graph(end, max_steps=0), "max_steps must be a positive whole number"),
            (graph(end, start="b"), "the start state 'b' is not in states"),
            ({**graph(end), "extra": 1}, "unknown key 'extra'"),
        ]
        for g, message in cases:
            with self.subTest(message):
                self.assertIn(message, self.refused(g))


class WorkflowRunTest(ServerCase):
    def setUp(self):
        super().setUp()
        for name in ("pi", "opencode", "codex"):
            self.box.stub(name, STEP_STUB)
        self.graph_dir = self.box.work / "graph"
        self.graph_dir.mkdir()
        self.folder = self.box.work / "folder"
        self.folder.mkdir()

    def write(self, g, **prompts):
        (self.graph_dir / "graph.jsonc").write_text(json.dumps(g))
        for name, text in prompts.items():
            (self.graph_dir / f"{name}.md").write_text(text)

    def run_workflow(self, *args, extra=None, default=True):
        if not args and default:
            args = (str(self.graph_dir), str(self.folder))
        return self.box.run(["bin/workflow", *args], extra_env=self.env | {"WORKFLOW_TIMEOUT": "20"} | (extra or {}),
                            timeout=120)

    def trace(self):
        (logs,) = (self.folder / "workflow-logs").iterdir()
        return [line.split("\t") for line in (logs / "trace.tsv").read_text().splitlines()[1:]]

    def test_a_verdict_picks_the_next_state(self):
        self.write(graph({
            "triage": {"agent": "triage.md", "choices": {"left": "l", "right": "r"}},
            "l": {"end": "failure"}, "r": {"end": "success"},
        }), triage="Pick a side.\nSTUB: echo ' Right ' > verdict.txt\n")
        result = self.run_workflow()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("== triage: right in", result.stdout)
        prompt = (self.box.calls / "prompts").read_text()
        self.assertIn("write exactly one word, left or right, to the file verdict.txt", prompt)
        self.assertFalse((self.folder / "verdict.txt").exists())
        self.assertEqual([row[:4] for row in self.trace()], [["1", "triage", "right", "r"]])

    def test_a_missing_or_unknown_verdict_stops_the_run(self):
        states = {"triage": {"agent": "triage.md", "choices": {"left": "end", "right": "end"}}, "end": {"end": "success"}}
        self.write(graph(states), triage="STUB: true\n")
        self.assertIn("triage: pi wrote no verdict.txt; expected one of left, right", self.run_workflow().stderr)
        self.write(graph(states), triage="STUB: echo up > verdict.txt\n")
        self.assertIn("the verdict 'up' is not one of left, right", self.run_workflow().stderr)

    def test_a_failed_step_goes_where_the_graph_says(self):
        states = {
            "try": {"agent": "try.md", "writes": ["out.txt"], "next": "done", "failed": "fallback"},
            "fallback": {"agent": "fallback.md", "next": "done"},
            "done": {"end": "success"},
        }
        self.write(graph(states), **{"try": "STUB: true\n", "fallback": "So far:\n{{trace}}\nSTUB: true\n"})
        result = self.run_workflow()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"== try: failed in \d+s \(pi did not write out.txt; see .*01-try.log\) -> fallback")
        self.assertIn("try: failed -> fallback", (self.box.calls / "prompts").read_text())
        self.assertEqual([row[2] for row in self.trace()], ["failed", "next"])

    def test_a_broken_prompt_stops_the_run_even_with_failed(self):
        self.write(graph({"a": {"agent": "a.md", "next": "done", "failed": "done"}, "done": {"end": "success"}}),
                   a="{{nonsense}}\n")
        result = self.run_workflow()
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown placeholder {{nonsense}}", result.stderr)

    def test_a_bounded_loop_ends_in_its_exhausted_state(self):
        self.write(graph({
            "check": {"command": "test -f done", "output": "logs/check.log", "pass": "ok", "fail": "work"},
            "work": {"agent": "work.md", "next": "check", "max_visits": 2, "exhausted": "stuck"},
            "ok": {"end": "success"}, "stuck": {"end": "failure"},
        }), work="Try {{visit}} of {{max_visits}}.\nSTUB: echo tried >> tries\n")
        result = self.run_workflow()
        self.assertEqual(result.returncode, 1)
        self.assertIn("== work (2 of 2): next", result.stdout)
        self.assertIn("work: ran 2 times; going to stuck", result.stdout)
        self.assertIn("== stuck: failure", result.stdout)
        self.assertEqual((self.folder / "tries").read_text(), "tried\ntried\n")
        self.assertIn("Try 2 of 2.", (self.box.calls / "prompts").read_text())
        self.assertTrue((self.folder / "logs" / "check.log").exists())
        self.assertEqual([row[1] for row in self.trace()], ["check", "work", "check", "work", "check"])

    def test_a_loop_that_reaches_its_goal(self):
        self.write(graph({
            "check": {"command": "test $(wc -l < tries) -ge 2", "pass": "ok", "fail": "work"},
            "work": {"agent": "work.md", "next": "check", "max_visits": 5},
            "ok": {"end": "success"},
        }), work="STUB: echo tried >> tries\n")
        (self.folder / "tries").write_text("")
        result = self.run_workflow()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Done: the workflow reached its success state.", result.stdout)
        self.assertEqual((self.folder / "tries").read_text(), "tried\ntried\n")

    def test_bounds_without_an_exit_and_max_steps_stop_the_run(self):
        loop = {"check": {"command": "false", "pass": "ok", "fail": "work"},
                "work": {"agent": "work.md", "next": "check", "max_visits": 2},
                "ok": {"end": "success"}}
        self.write(graph(loop), work="STUB: true\n")
        self.assertIn("work has run 2 times, its max_visits, and has no exhausted state", self.run_workflow().stderr)
        self.write(graph(loop, max_steps=3), work="STUB: true\n")
        self.assertIn("the run reached max_steps (3) at work", self.run_workflow().stderr)

    def test_prompts_inline_files_and_the_trace(self):
        (self.folder / "goal.md").write_text("Reach the goal.\n")
        (self.folder / "long.log").write_text("".join(f"line {n}\n" for n in range(1, 101)))
        self.write(graph({
            "first": {"command": "echo checked", "pass": "read", "fail": "read"},
            "read": {"agent": "read.md", "next": "done"},
            "done": {"end": "success"},
        }), read="Goal: {{file:goal.md}}\nLog:\n{{tail:long.log}}\nMissing: {{file:none.md}}\n"
                 "Run {{run}}, state {{state}}.\nSo far:\n{{trace}}\n")
        result = self.run_workflow()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("== first: pass in 0s (checked) -> read", result.stdout)
        prompt = (self.box.calls / "prompts").read_text()
        self.assertIn("Goal: Reach the goal.", prompt)
        self.assertIn("(the first 40 lines are left out)\nline 41\n", prompt)
        self.assertIn("line 100", prompt)
        self.assertNotIn("line 40\n", prompt)
        self.assertIn("Missing: (none.md does not exist yet)", prompt)
        self.assertIn("state read.", prompt)
        self.assertIn("So far:\nfirst: pass -> read", prompt)

    def test_too_large_an_inline_file_and_unknown_placeholders_stop_the_run(self):
        (self.folder / "big.txt").write_text("x" * 2000)
        self.write(graph({"a": {"agent": "a.md", "next": "done"}, "done": {"end": "success"}}), a="{{file:big.txt}}\n")
        result = self.run_workflow(extra={"WORKFLOW_INLINE_BYTES": "1000"})
        self.assertIn("big.txt is 2000 bytes, over WORKFLOW_INLINE_BYTES (1000)", result.stderr)
        self.write(graph({"a": {"agent": "a.md", "next": "done"}, "done": {"end": "success"}}), a="{{nonsense}}\n")
        self.assertIn("unknown placeholder {{nonsense}}", self.run_workflow().stderr)
        self.assertFalse((self.box.calls / "prompts").exists())

    def test_a_state_can_use_its_own_agent(self):
        self.write(graph({
            "a": {"agent": "a.md", "next": "b"},
            "b": {"agent": "a.md", "next": "done", "using": "codex"},
            "done": {"end": "success"},
        }), a="STUB: true\n")
        result = self.run_workflow(extra={"WORKFLOW_AGENT": "opencode"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.box.called("agents"), ["opencode", "codex"])

    def test_a_command_that_hangs_is_stopped_with_what_it_started(self):
        self.write(graph({
            "slow": {"command": "sleep 30 & echo $! > child; wait", "timeout": 1, "pass": "done", "fail": "f"},
            "done": {"end": "success"}, "f": {"end": "failure"},
        }))
        result = self.run_workflow()
        self.assertEqual(result.returncode, 1)
        self.assertIn("== slow: fail", result.stdout)
        child = (self.folder / "child").read_text().strip()
        self.assertNotEqual(subprocess.run(["kill", "-0", child], capture_output=True).returncode, 0)

    def test_the_folder_must_hold_what_the_workflow_needs(self):
        self.write(graph({"done": {"end": "success"}}, needs=["goal.md"]))
        self.assertIn("goal.md is missing; this workflow needs it to start", self.run_workflow().stderr)

    def test_the_goal_example_passes_once_the_work_is_done(self):
        folder = self.box.work / "goal"
        shutil.copytree(REPO / "workflows" / "goal" / "example", folder)
        (self.box.work / "textstats.py").write_text(TEXTSTATS)
        work = self.box.root / "workflows" / "goal" / "work.md"
        work.write_text(work.read_text() + f"STUB: cp {self.box.work}/textstats.py . && echo finished >> progress.md\n")
        result = self.run_workflow("goal", str(folder))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([row[1] for row in self.trace_of(folder)], ["check", "work", "check"])
        prompt = (self.box.calls / "prompts").read_text()
        self.assertIn("You are step 1 of at most 8", prompt)
        self.assertIn("Make every test in\ntest_textstats.py pass.", prompt)
        self.assertIn("(progress.md does not exist yet)", prompt)
        self.assertIn("NotImplementedError", prompt)

    def trace_of(self, folder):
        (logs,) = (folder / "workflow-logs").iterdir()
        return [line.split("\t") for line in (logs / "trace.tsv").read_text().splitlines()[1:]]

    def test_list_check_and_usage(self):
        listed = self.run_workflow("list").stdout
        self.assertRegex(listed, r"goal\s+Work in short fresh steps")
        self.assertRegex(listed, r"team-solve\s+Plan, two ideas")
        shown = self.run_workflow("check", "goal")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("work: agent work.md, at most 8 times; next -> check, exhausted -> gave-up", shown.stdout)
        for args, message in ((("nope",), "workflows/nope/graph.jsonc does not exist"),
                              (("Bad Name",), "is not a workflow name"),
                              ((), "usage:"),
                              (("goal", str(self.folder), "x"), "usage:")):
            with self.subTest(message):
                result = self.run_workflow(*args, default=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)


if __name__ == "__main__":
    unittest.main()
