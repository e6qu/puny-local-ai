"""The bench/ tasks, bin/bench-agents and bin/smoke-test."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import REPO, SYSTEM_PATH, ServerCase

TASKS = sorted(p.name for p in (REPO / "bench").iterdir() if p.is_dir())
SOLUTIONS = Path(__file__).resolve().parent / "fixtures" / "solutions"


def grade(task, solved):
    """Runs a task's visible and hidden tests and its check, as bin/bench-agents does."""
    work = Path(tempfile.mkdtemp(prefix=f"puny-grade-{task}-"))
    try:
        shutil.copytree(REPO / "bench" / task / "start", work, dirs_exist_ok=True)
        if solved:
            shutil.copytree(SOLUTIONS / task, work, dirs_exist_ok=True)
        for hidden in (REPO / "bench" / task / "hidden").iterdir():
            shutil.copy(hidden, work)
        # The same Python and bash a fresh Mac has, as ./bin/bench-agents uses them.
        env = {"PATH": SYSTEM_PATH, "HOME": str(work)}
        tests = subprocess.run(["/usr/bin/python3", "-m", "unittest", "-q"], cwd=work, capture_output=True, text=True, env=env)
        check = REPO / "bench" / task / "check"
        checked = subprocess.run([str(check), str(REPO / "bench" / task)], cwd=work, capture_output=True, text=True, env=env) if check.exists() else None
        return tests, checked
    finally:
        shutil.rmtree(work)


class TasksTest(unittest.TestCase):
    def test_every_task_is_complete(self):
        self.assertEqual(TASKS, ["billing", "inventory", "todo"])
        for task in TASKS:
            with self.subTest(task):
                for part in ("prompt.txt", "start", "hidden"):
                    self.assertTrue((REPO / "bench" / task / part).exists(), part)
                hidden = list((REPO / "bench" / task / "hidden").iterdir())
                self.assertTrue(hidden and all(h.name.startswith("test_hidden_") for h in hidden),
                                "unittest discovery only finds files named test*")

    def test_the_reference_solution_passes(self):
        for task in TASKS:
            with self.subTest(task):
                tests, checked = grade(task, solved=True)
                self.assertEqual(tests.returncode, 0, tests.stderr)
                self.assertIn("OK", tests.stderr)
                if checked:
                    self.assertEqual(checked.returncode, 0, checked.stdout)

    def test_the_unsolved_start_fails(self):
        for task in TASKS:
            with self.subTest(task):
                tests, _ = grade(task, solved=False)
                self.assertNotEqual(tests.returncode, 0)

    def test_checks_catch_forbidden_shortcuts(self):
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work)
        shutil.copytree(REPO / "bench" / "inventory" / "start", work, dirs_exist_ok=True)
        (work / "test_inventory.py").write_text("# emptied\n")
        result = subprocess.run([str(REPO / "bench" / "inventory" / "check"), str(REPO / "bench" / "inventory")], cwd=work, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        shutil.copytree(SOLUTIONS / "billing", work / "b", dirs_exist_ok=True)
        (work / "b" / "billing" / "old.py").write_text("calc_tax = None\n")
        result = subprocess.run([str(REPO / "bench" / "billing" / "check")], cwd=work / "b", capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("calc_tax is still mentioned", result.stdout)


class BenchAgentsTest(ServerCase):
    def bench(self, label, behavior, task="inventory", agents="opencode pi", extra=None):
        env = self.env | {
            "BENCH_TASKS": task, "BENCH_AGENTS": agents, "STUB_BEHAVIOR": behavior,
            "STUB_SOLUTION": str(SOLUTIONS / task), "BENCH_TIMEOUT": "20",
        } | (extra or {})
        return self.box.run(["bin/bench-agents", label], extra_env=env, timeout=300)

    def results(self, label):
        lines = (self.box.root / "state" / "bench" / label / "results.tsv").read_text().splitlines()
        return [line.split("\t") for line in lines[1:]]

    def setUp(self):
        super().setUp()
        self.agent_stubs("opencode", "pi")

    def test_a_solving_agent_passes_every_task(self):
        for task in TASKS:
            with self.subTest(task):
                result = self.bench("good", "solve", task=task)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rows = self.results("good")
        self.assertEqual(len(rows), 6)
        self.assertEqual({r[4] for r in rows}, {"pass"}, rows)
        # Agents ran in temporary folders outside the repo, with the prompt as given.
        cwds = [line for line in self.env_log("opencode").splitlines() if line.startswith("cwd=")]
        self.assertEqual(len(cwds), 3)
        self.assertTrue(all(str(self.box.root) not in line for line in cwds))
        prompt = (REPO / "bench" / "todo" / "prompt.txt").read_text().strip()
        self.assertIn(prompt, (self.box.calls / "pi").read_text())

    def test_failures_are_classified(self):
        self.bench("bad", "nothing")
        self.bench("bad", "fail")
        self.bench("bad", "hang", extra={"BENCH_TIMEOUT": "1"}, agents="pi")
        results = [r[4] for r in self.results("bad")]
        self.assertEqual(results, ["fail", "fail", "agent-error", "agent-error", "timeout"])

    def test_a_failed_check_fails_the_run(self):
        # Solving the task but editing the visible test breaks the inventory check.
        solution = self.box.dir / "cheat"
        import shutil
        shutil.copytree(SOLUTIONS / "inventory", solution)
        (solution / "test_inventory.py").write_text("import unittest\n")
        result = self.bench("cheat", "solve", agents="pi", extra={"STUB_SOLUTION": str(solution)})
        self.assertIn("fail", result.stdout)
        self.assertIn("test_inventory.py was changed", result.stdout)
        self.assertEqual([r[4] for r in self.results("cheat")], ["fail"])

    def test_runs_continue_numbering_and_keep_their_files(self):
        self.bench("again", "solve", agents="pi", extra={"BENCH_RUNS": "2"})
        self.bench("again", "solve", agents="pi")
        self.assertEqual([r[3] for r in self.results("again")], ["1", "2", "3"])
        kept = self.box.root / "state" / "bench" / "again" / "inventory-pi-3"
        self.assertTrue((kept / "work" / "inventory.py").exists())
        self.assertTrue((kept / "agent.log").exists())

    def test_report(self):
        self.bench("a-label", "solve", agents="pi")
        self.bench("b", "nothing", agents="pi")
        result = self.box.run(["bin/bench-agents", "report"], extra_env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("a-label inventory  pi          1/1", result.stdout)
        self.assertIn("b       inventory  pi          0/1", result.stdout)
        self.assertIn("a-label: 1/1 passed", result.stdout)

    def test_report_without_results(self):
        self.assertIn("no results", self.box.run(["bin/bench-agents", "report"], extra_env=self.env).stderr)

    def test_bad_settings(self):
        cases = [
            (["bin/bench-agents"], {}, "usage"),
            (["bin/bench-agents", "bad label"], {}, "LABEL may contain only"),
            (["bin/bench-agents", "x"], {"BENCH_RUNS": "0"}, "BENCH_RUNS"),
            (["bin/bench-agents", "x"], {"BENCH_TIMEOUT": "soon"}, "BENCH_TIMEOUT"),
            (["bin/bench-agents", "x"], {"BENCH_TASKS": "nope"}, "bench/nope/prompt.txt is missing"),
            (["bin/bench-agents", "x"], {"BENCH_TASKS": "  "}, "BENCH_TASKS names no task"),
            (["bin/bench-agents", "x"], {"BENCH_AGENTS": "  "}, "BENCH_AGENTS names no agent"),
            (["bin/bench-agents", "x"], {"BENCH_AGENTS": "opencode codex"}, "unknown agent 'codex' in BENCH_AGENTS"),
        ]
        for args, extra, message in cases:
            with self.subTest(message):
                result = self.box.run(args, extra_env=self.env | extra)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)


class SmokeTestTest(ServerCase):
    def setUp(self):
        super().setUp()
        self.agent_stubs("opencode", "pi")

    def smoke(self, behavior, *agents, extra=None):
        env = self.env | {"STUB_BEHAVIOR": behavior, "SMOKE_TIMEOUT": "20"} | (extra or {})
        return self.box.run(["bin/smoke-test", *agents], extra_env=env, timeout=120)

    def test_passes_when_agents_write_the_file(self):
        result = self.smoke("hello")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(list(self.box.dir.glob("puny-smoke-*")), [], "passing runs clean up after themselves")
        self.assertIn("OK: opencode created hello.txt", result.stdout)
        self.assertIn("OK: pi created hello.txt", result.stdout)
        self.assertIn("All smoke tests passed.", result.stdout)
        # OpenCode gets a throwaway data folder, never the real one.
        self.assertIn(f"XDG_DATA_HOME={self.box.dir}", self.env_log("opencode"))

    def test_reports_each_kind_of_failure(self):
        cases = [
            ("nothing", {}, "did not create hello.txt"),
            ("wrong", {}, "wrote 'something else', expected 'hello from pi'"),
            ("fail", {"STUB_SOLUTION": "/nonexistent"}, "exited with status 3"),
            ("hang", {"SMOKE_TIMEOUT": "1"}, "did not finish within 1s"),
        ]
        for behavior, extra, message in cases:
            with self.subTest(behavior):
                result = self.smoke(behavior, "pi", extra=extra)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stdout)
                self.assertIn("failed: pi", result.stderr)

    def test_bad_arguments(self):
        self.assertIn("unknown agent 'codex'", self.smoke("hello", "codex").stderr)
        self.assertIn("SMOKE_TIMEOUT must be a positive whole number", self.smoke("hello", extra={"SMOKE_TIMEOUT": "0"}).stderr)
        self.assertEqual(self.box.called("opencode"), [])

    def test_fails_when_tool_calls_do_not_work(self):
        self.server.routes["POST /v1/chat/completions"] = (200, {"choices": [{"message": {"content": "hi"}}]})
        result = self.smoke("hello", "pi")
        self.assertIn("failed: raw tool call", result.stderr)


if __name__ == "__main__":
    unittest.main()
