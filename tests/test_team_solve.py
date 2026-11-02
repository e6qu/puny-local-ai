"""./bin/team-solve and workflows/team-solve/run-checks.sh."""

import subprocess
import unittest
from pathlib import Path

from helpers import REPO, ServerCase

GOOD = "a, b = map(int, input().split())\nprint(a + b)\n"
BAD = "a, b = map(int, input().split())\nprint(a - b)\n"

# A stand-in agent that plays whichever team role its prompt names. It logs
# the role, then does what the STUB_* settings say: STUB_SOLVER writes a good
# or bad solution.py with run.sh (or neither), STUB_REPAIR=fix rewrites it
# correctly, STUB_FAIL=<role> exits 3 in that role, STUB_HANG=<role> sleeps,
# and STUB_NO_NOTES=1 makes the planner skip notes.md.
TEAM_STUB = r"""
name="$(basename "$0")"
if [[ "${1:-}" == --version ]]; then echo "2.1.1"; exit 0; fi
prompt="${@: -1}"
case "$prompt" in
  *"You are the planner"*) role=plan ;;
  *"You are ideator A"*) role=ideate-a ;;
  *"You are ideator B"*) role=ideate-b ;;
  *"You are the solver"*) role=solve ;;
  *"You are the reviewer"*) role=review ;;
  *"You are the repair worker"*) role=repair ;;
  *) role=unknown ;;
esac
echo "$role" >> "$STUB_CALLS/roles"
printf '%s\n' "$prompt" > "$STUB_CALLS/prompt-$role"
pwd > "$STUB_CALLS/cwd-$role"
[[ "${STUB_FAIL:-}" == "$role" ]] && exit 3
[[ "${STUB_HANG:-}" == "$role" ]] && exec sleep 30
case "$role" in
  plan)
    echo "# Plan" > plan.md
    [[ -n "${STUB_NO_NOTES:-}" ]] || printf '# Notes\n## Proposal A\n<!-- proposal-a -->\n## Proposal B\n<!-- proposal-b -->\n' > notes.md ;;
  ideate-a) echo "Proposal A: add" >> notes.md ;;
  ideate-b) echo "Proposal B: also add" >> notes.md ;;
  solve)
    case "${STUB_SOLVER:-good}" in
      good) printf '%s' "$STUB_GOOD" > solution.py; echo "python3 solution.py" > run.sh ;;
      bad) printf '%s' "$STUB_BAD" > solution.py; echo "python3 solution.py" > run.sh ;;
      none) ;;
    esac ;;
  review) echo "review of: $(tail -n 1 check-output.log)" > review.md ;;
  repair) [[ "${STUB_REPAIR:-}" == fix ]] && printf '%s' "$STUB_GOOD" > solution.py ;;
esac
exit 0
"""

FIRST = ["plan", "ideate-a", "ideate-b", "solve"]


class TeamSolveTest(ServerCase):
    def setUp(self):
        super().setUp()
        for name in ("pi", "opencode", "codex"):
            self.box.stub(name, TEAM_STUB)
        self.problem = self.box.work / "problem"
        (self.problem / "tests").mkdir(parents=True)
        (self.problem / "task.md").write_text("Add two numbers.\n")
        for n, (given, expected) in enumerate((("2 3", "5"), ("10 -4", "6")), start=1):
            (self.problem / "tests" / f"{n}.in").write_text(given + "\n")
            (self.problem / "tests" / f"{n}.out").write_text(expected + "\n")

    def solve(self, *args, extra=None):
        env = self.env | {"STUB_GOOD": GOOD, "STUB_BAD": BAD, "WORKFLOW_TIMEOUT": "20"} | (extra or {})
        return self.box.run(["bin/team-solve", *(args or (str(self.problem),))], extra_env=env, timeout=120)

    def roles(self):
        return self.box.called("roles")

    def test_a_good_solution_needs_no_review(self):
        result = self.solve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("== solved: success", result.stdout)
        self.assertEqual(self.roles(), FIRST)
        for name in ("plan.md", "notes.md", "solution.py", "run.sh", "check-output.log"):
            self.assertTrue((self.problem / name).exists(), name)
        self.assertIn("Samples: 2; failures: 0", (self.problem / "check-output.log").read_text())
        runs = list((self.problem / "workflow-logs").iterdir())
        self.assertEqual(len(runs), 1)
        self.assertEqual(sorted(p.name for p in runs[0].iterdir()),
                         ["01-plan.log", "02-ideate-a.log", "03-ideate-b.log", "04-solve.log", "05-check.log", "trace.tsv"])

    def runs_of(self, agent):
        """The agent's calls; a prompt spans lines in the log, so count only the lines a call starts with."""
        starts = {
            "pi": "--provider puny --model default_model -p --no-session ",
            "opencode": "run --standalone --auto ",
            "codex": "-c model_provider=puny ",
        }
        return [line for line in self.box.called(agent) if line.startswith(starts[agent])]

    def test_every_step_is_a_fresh_sessionless_run_in_the_problem_folder(self):
        self.solve()
        self.assertEqual(len(self.runs_of("pi")), len(FIRST))
        for role in FIRST:
            self.assertEqual((self.box.calls / f"cwd-{role}").read_text().strip(), str(self.problem))

    def test_the_prompts_carry_the_task_and_the_run(self):
        result = self.solve()
        run = result.stdout.split("team-solve ")[1].split()[0]
        plan = (self.box.calls / "prompt-plan").read_text()
        self.assertIn(f"the run identifier {run}", plan)
        self.assertIn("Add two numbers.", plan)
        self.assertNotIn("{{", plan)

    def test_a_failing_solution_is_reviewed_and_repaired(self):
        result = self.solve(extra={"STUB_SOLVER": "bad", "STUB_REPAIR": "fix"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.roles(), FIRST + ["review", "repair"])
        self.assertIn("review of: Checks failed: 2", (self.problem / "review.md").read_text())
        review = (self.box.calls / "prompt-review").read_text()
        self.assertIn("FAIL tests/1.in: output differs", review)
        self.assertIn("This is review 1 of 3", review)
        self.assertIn("check: fail -> review", review)

    def test_an_unrepaired_solution_stops_after_three_reviews(self):
        result = self.solve(extra={"STUB_SOLVER": "bad"})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.roles(), FIRST + ["review", "repair"] * 3)
        self.assertIn("review: ran 3 times; going to unsolved", result.stdout)
        self.assertIn("the workflow ended in failure", result.stderr)

    def test_a_missing_solution_fails_the_check(self):
        result = self.solve(extra={"STUB_SOLVER": "none"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("there is no run.sh", (self.problem / "check-output.log").read_text())

    def test_a_check_script_is_a_goal_too(self):
        (self.problem / "check.sh").write_text('grep -q "a + b" solution.py\n')
        result = self.solve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS check.sh", (self.problem / "check-output.log").read_text())
        for path in (self.problem / "tests").iterdir():
            path.unlink()
        (self.problem / "check.sh").write_text("exit 1\n")
        result = self.solve()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("FAIL check.sh", (self.problem / "check-output.log").read_text())

    def test_a_planner_without_notes_stops_the_run(self):
        result = self.solve(extra={"STUB_NO_NOTES": "1"})
        self.assertIn("plan: pi did not write notes.md", result.stderr)
        self.assertEqual(self.roles(), ["plan"])

    def test_a_failing_ideator_is_noted_and_the_run_goes_on(self):
        result = self.solve(extra={"STUB_FAIL": "ideate-b"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"== ideate-b: failed in \d+s \(pi exited with status 3; see .*03-ideate-b.log\) -> solve")
        self.assertEqual(self.roles(), FIRST)
        (logs,) = (self.problem / "workflow-logs").iterdir()
        self.assertIn("3\tideate-b\tfailed\tsolve\t", (logs / "trace.tsv").read_text())

    def test_a_failing_planner_stops_the_run_and_names_its_log(self):
        result = self.solve(extra={"STUB_FAIL": "plan"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("plan: pi exited with status 3; see", result.stderr)
        self.assertIn("01-plan.log", result.stderr)
        self.assertEqual(self.roles(), ["plan"])

    def test_a_solver_that_hangs_is_stopped_and_the_check_decides(self):
        result = self.solve(extra={"STUB_HANG": "solve", "WORKFLOW_TIMEOUT": "1"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("== solve: failed", result.stdout)
        self.assertIn("pi gave no answer within 1s", result.stdout)
        self.assertIn("== unsolved: failure", result.stdout)

    def test_other_agents(self):
        for agent in ("opencode", "codex"):
            with self.subTest(agent):
                for name in ("roles", "pi", "opencode", "codex"):
                    (self.box.calls / name).unlink(missing_ok=True)
                result = self.solve(extra={"WORKFLOW_AGENT": agent})
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(len(self.runs_of(agent)), len(FIRST))
                self.assertEqual(self.roles(), FIRST)
                self.assertEqual(self.box.called("pi"), [])

    def test_temporary_data_is_removed(self):
        self.solve()
        self.solve(extra={"STUB_FAIL": "plan"})
        self.assertEqual(list(self.box.dir.glob("puny-workflow.*")), [])

    def test_bad_input(self):
        empty = self.box.work / "empty"
        empty.mkdir()
        (self.box.work / "no-goal").mkdir()
        (self.box.work / "no-goal" / "task.md").write_text("x\n")
        (self.problem / "tests" / "3.in").write_text("1 1\n")
        cases = [
            ((str(empty),), {}, "task.md is missing"),
            ((str(self.box.work / "no-goal"),), {}, "has no goal to check"),
            ((str(self.problem),), {}, "3.out is missing"),
            ((str(self.box.work / "nowhere"),), {}, "is not a folder"),
            ((str(self.problem), "extra"), {}, "usage:"),
        ]
        for args, extra, message in cases:
            with self.subTest(message):
                result = self.solve(*args, extra=extra)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
        (self.problem / "tests" / "3.in").unlink()
        for extra, message in (
            ({"WORKFLOW_AGENT": "claude"}, "WORKFLOW_AGENT must be one of"),
            ({"WORKFLOW_TIMEOUT": "0"}, "WORKFLOW_TIMEOUT must be a positive whole number"),
        ):
            with self.subTest(message):
                self.assertIn(message, self.solve(extra=extra).stderr)
        self.server.stop()
        self.assertIn("cannot reach", self.solve().stderr)
        self.assertEqual(self.roles(), [])

    def test_the_current_folder_is_the_default(self):
        result = self.box.run(["bin/team-solve"], extra_env=self.env | {"STUB_GOOD": GOOD, "STUB_BAD": BAD},
                              cwd=self.problem, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.problem / "solution.py").exists())


class TeamSolveSettingsTest(unittest.TestCase):
    """./bin/config checks the workflow settings as ./bin/workflow does."""

    def setUp(self):
        from helpers import Sandbox
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)

    def test_bad_values_are_never_saved(self):
        for pair, message in (("WORKFLOW_AGENT=claude", "WORKFLOW_AGENT must be one of"),
                              ("WORKFLOW_TIMEOUT=soon", "WORKFLOW_TIMEOUT must be a positive whole number")):
            with self.subTest(pair):
                result = self.box.run(["bin/config", "set", pair])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertFalse((self.box.root / "puny.conf").exists())

    def test_good_values_are_saved_and_shown(self):
        result = self.box.run(["bin/config", "set", "WORKFLOW_AGENT=opencode", "WORKFLOW_TIMEOUT=120"])
        self.assertEqual(result.returncode, 0, result.stderr)
        shown = self.box.run(["bin/config"]).stdout
        self.assertRegex(shown, r"WORKFLOW_AGENT\s+puny.conf\s+opencode")
        self.assertRegex(shown, r"WORKFLOW_TIMEOUT\s+puny.conf\s+120")
        self.assertRegex(self.box.run(["bin/config"], extra_env={"WORKFLOW_AGENT": "pi"}).stdout, r"WORKFLOW_AGENT\s+environment\s+pi")


class RunChecksTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = Path(tempfile.mkdtemp(prefix="puny-check-")).resolve()
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.dir)])
        (self.dir / "tests").mkdir()
        (self.dir / "tests" / "1.in").write_text("2 3\n")
        (self.dir / "tests" / "1.out").write_text("5\n")

    def check(self):
        return subprocess.run(
            ["/bin/bash", str(REPO / "workflows" / "team-solve" / "run-checks.sh")],
            cwd=self.dir, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"}, timeout=60,
        )

    def test_pass_and_fail(self):
        (self.dir / "solution.py").write_text(GOOD)
        (self.dir / "run.sh").write_text("python3 solution.py\n")
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("PASS tests/1.in", result.stdout)
        (self.dir / "solution.py").write_text(BAD)
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL tests/1.in: output differs", result.stdout)

    def test_a_crashing_program(self):
        (self.dir / "run.sh").write_text("exit 2\n")
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("run.sh exited unsuccessfully", result.stdout)

    def test_check_sh_alone_and_with_samples(self):
        (self.dir / "run.sh").write_text("echo 5\n")
        (self.dir / "check.sh").write_text("test -f run.sh\n")
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("PASS check.sh", result.stdout)
        (self.dir / "check.sh").write_text("exit 1\n")
        self.assertEqual(self.check().returncode, 1)
        for path in (self.dir / "tests").iterdir():
            path.unlink()
        (self.dir / "check.sh").write_text("true\n")
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("Samples:", result.stdout)

    def test_missing_pieces(self):
        self.assertIn("there is no run.sh", self.check().stdout)
        (self.dir / "run.sh").write_text("echo 5\n")
        (self.dir / "tests" / "2.in").write_text("1 1\n")
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing tests/2.out", result.stdout)
        for path in (self.dir / "tests").iterdir():
            path.unlink()
        self.assertIn("no goal to check", self.check().stdout)

    def test_the_example_problem_is_solvable(self):
        example = REPO / "workflows" / "team-solve" / "example"
        solution = (
            "n = int(input())\n"
            "a = list(map(int, input().split()))\n"
            "best = run = 1\n"
            "for i in range(1, n):\n"
            "    run = run + 1 if a[i] > a[i - 1] else 1\n"
            "    best = max(best, run)\n"
            "print(best)\n"
        )
        for path in example.rglob("*"):
            if path.is_file():
                target = self.dir / path.relative_to(example)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(path.read_text())
        (self.dir / "solution.py").write_text(solution)
        (self.dir / "run.sh").write_text("python3 solution.py\n")
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Samples: 3; failures: 0", result.stdout)


if __name__ == "__main__":
    unittest.main()
