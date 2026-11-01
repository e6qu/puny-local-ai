"""./bin/team-solve and workflows/team-solve/check-samples.sh."""

import subprocess
import unittest
from pathlib import Path

from helpers import REPO, ServerCase

GOOD = "a, b = map(int, input().split())\nprint(a + b)\n"
BAD = "a, b = map(int, input().split())\nprint(a - b)\n"

# A stand-in agent that plays whichever team role its prompt names. It logs
# the role, then does what the STUB_* settings say: STUB_SOLVER writes a good
# or bad solution.py (or none), STUB_REPAIR=fix rewrites it correctly,
# STUB_FAIL=<role> exits 3 in that role, STUB_HANG=<role> sleeps, and
# STUB_NO_NOTES=1 makes the planner skip notes.md.
TEAM_STUB = r'''
name="$(basename "$0")"
if [[ "${1:-}" == --version ]]; then echo "2.1.1"; exit 0; fi
prompt="${@: -1}"
case "$prompt" in
  *"You are the planner"*) role=plan ;;
  *"ideator A"*) role=ideate-a ;;
  *"ideator B"*) role=ideate-b ;;
  *"You are the solver"*) role=solve ;;
  *"manager/reviewer"*) role=review ;;
  *"repair worker"*) role=repair ;;
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
      good) printf '%s' "$STUB_GOOD" > solution.py ;;
      bad) printf '%s' "$STUB_BAD" > solution.py ;;
      none) ;;
    esac ;;
  review) echo "reviewed status $(cat test-status.txt)" > review.md ;;
  repair) [[ "${STUB_REPAIR:-}" == fix ]] && printf '%s' "$STUB_GOOD" > solution.py ;;
esac
exit 0
'''

ROLES = ["plan", "ideate-a", "ideate-b", "solve", "review", "repair"]


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
        env = self.env | {"STUB_GOOD": GOOD, "STUB_BAD": BAD, "TEAM_SOLVE_TIMEOUT": "20"} | (extra or {})
        return self.box.run(["bin/team-solve", *(args or (str(self.problem),))], extra_env=env, timeout=120)

    def roles(self):
        return self.box.called("roles")

    def test_a_good_solution_runs_every_step_once(self):
        result = self.solve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Solved:", result.stdout)
        self.assertEqual(self.roles(), ROLES)
        for name in ("plan.md", "notes.md", "solution.py", "review.md", "test-output.log", "final-test-output.log"):
            self.assertTrue((self.problem / name).exists(), name)
        self.assertEqual((self.problem / "test-status.txt").read_text(), "0\n")
        self.assertIn("reviewed status 0", (self.problem / "review.md").read_text())
        self.assertIn("Samples: 2; failures: 0", (self.problem / "final-test-output.log").read_text())
        runs = list((self.problem / "team-solve-logs").iterdir())
        self.assertEqual(len(runs), 1)
        self.assertEqual(sorted(p.name for p in runs[0].iterdir()), sorted(f"{r}.log" for r in ROLES))

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
        self.assertEqual(len(self.runs_of("pi")), len(ROLES))
        for role in ROLES:
            self.assertEqual((self.box.calls / f"cwd-{role}").read_text().strip(), str(self.problem))

    def test_the_run_identifier_reaches_the_planner(self):
        result = self.solve()
        run = result.stdout.split("team-solve ")[1].split()[0]
        plan = (self.box.calls / "prompt-plan").read_text()
        self.assertIn(f"the run identifier {run}", plan)
        self.assertNotIn("{{run}}", plan)
        self.assertEqual(plan.strip(), (REPO / "workflows" / "team-solve" / "plan.md").read_text().replace("{{run}}", run).strip())

    def test_a_failing_solution_is_repaired(self):
        result = self.solve(extra={"STUB_SOLVER": "bad", "STUB_REPAIR": "fix"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.problem / "test-status.txt").read_text(), "1\n")
        self.assertIn("FAIL tests/1.in: output differs", (self.problem / "test-output.log").read_text())
        self.assertIn("reviewed status 1", (self.problem / "review.md").read_text())
        self.assertIn("Solved:", result.stdout)

    def test_an_unrepaired_solution_fails_the_run(self):
        result = self.solve(extra={"STUB_SOLVER": "bad"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("solution.py fails the samples", result.stderr)
        self.assertIn("Samples: 2; failures: 2", result.stdout)
        self.assertEqual(self.roles(), ROLES)

    def test_a_missing_solution_stops_before_testing(self):
        result = self.solve(extra={"STUB_SOLVER": "none"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("the solver wrote no solution.py", result.stderr)
        self.assertEqual(self.roles(), ROLES[:4])

    def test_a_planner_without_notes_stops_the_run(self):
        result = self.solve(extra={"STUB_NO_NOTES": "1"})
        self.assertIn("the planner wrote no notes.md", result.stderr)
        self.assertEqual(self.roles(), ["plan"])

    def test_a_failing_step_stops_the_run_and_names_its_log(self):
        result = self.solve(extra={"STUB_FAIL": "ideate-b"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("the ideate-b step failed; see", result.stderr)
        self.assertIn("ideate-b.log", result.stderr)
        self.assertEqual(self.roles(), ["plan", "ideate-a", "ideate-b"])

    def test_a_step_that_hangs_is_stopped(self):
        result = self.solve(extra={"STUB_HANG": "solve", "TEAM_SOLVE_TIMEOUT": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no answer within 1s", result.stdout)
        self.assertIn("the solve step did not finish", result.stderr)

    def test_other_agents(self):
        for agent in ("opencode", "codex"):
            with self.subTest(agent):
                for name in ("roles", "pi", "opencode", "codex"):
                    (self.box.calls / name).unlink(missing_ok=True)
                result = self.solve(extra={"TEAM_SOLVE_AGENT": agent})
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(len(self.runs_of(agent)), len(ROLES))
                self.assertEqual(self.roles(), ROLES)
                self.assertEqual(self.box.called("pi"), [])

    def test_temporary_data_is_removed(self):
        self.solve()
        self.solve(extra={"STUB_FAIL": "plan"})
        self.assertEqual(list(self.box.dir.glob("puny-team-solve.*")), [])

    def test_bad_input(self):
        empty = self.box.work / "empty"
        empty.mkdir()
        (self.box.work / "no-tests").mkdir()
        (self.box.work / "no-tests" / "task.md").write_text("x\n")
        (self.problem / "tests" / "3.in").write_text("1 1\n")
        cases = [
            ((str(empty),), {}, "task.md is missing"),
            ((str(self.box.work / "no-tests"),), {}, "holds no samples"),
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
            ({"TEAM_SOLVE_AGENT": "claude"}, "TEAM_SOLVE_AGENT must be one of"),
            ({"TEAM_SOLVE_TIMEOUT": "0"}, "TEAM_SOLVE_TIMEOUT must be a positive whole number"),
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
    """./bin/config checks the team-solve settings as ./bin/team-solve does."""

    def setUp(self):
        from helpers import Sandbox
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)

    def test_bad_values_are_never_saved(self):
        for pair, message in (("TEAM_SOLVE_AGENT=claude", "TEAM_SOLVE_AGENT must be one of"),
                              ("TEAM_SOLVE_TIMEOUT=soon", "TEAM_SOLVE_TIMEOUT must be a positive whole number")):
            with self.subTest(pair):
                result = self.box.run(["bin/config", "set", pair])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertFalse((self.box.root / "puny.conf").exists())

    def test_good_values_are_saved_and_shown(self):
        result = self.box.run(["bin/config", "set", "TEAM_SOLVE_AGENT=opencode", "TEAM_SOLVE_TIMEOUT=120"])
        self.assertEqual(result.returncode, 0, result.stderr)
        shown = self.box.run(["bin/config"]).stdout
        self.assertRegex(shown, r"TEAM_SOLVE_AGENT\s+puny.conf\s+opencode")
        self.assertRegex(shown, r"TEAM_SOLVE_TIMEOUT\s+puny.conf\s+120")
        self.assertRegex(self.box.run(["bin/config"], extra_env={"TEAM_SOLVE_AGENT": "pi"}).stdout, r"TEAM_SOLVE_AGENT\s+environment\s+pi")


class CheckSamplesTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = Path(tempfile.mkdtemp(prefix="puny-check-")).resolve()
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.dir)])
        (self.dir / "tests").mkdir()
        (self.dir / "tests" / "1.in").write_text("2 3\n")
        (self.dir / "tests" / "1.out").write_text("5\n")

    def check(self):
        return subprocess.run(
            ["/bin/bash", str(REPO / "workflows" / "team-solve" / "check-samples.sh")],
            cwd=self.dir, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"}, timeout=60,
        )

    def test_pass_and_fail(self):
        (self.dir / "solution.py").write_text(GOOD)
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("PASS tests/1.in", result.stdout)
        (self.dir / "solution.py").write_text(BAD)
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL tests/1.in: output differs", result.stdout)

    def test_a_crashing_solution(self):
        (self.dir / "solution.py").write_text("raise SystemExit(2)\n")
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("solution exited unsuccessfully", result.stdout)

    def test_missing_pieces(self):
        self.assertIn("there is no solution.py", self.check().stdout)
        (self.dir / "solution.py").write_text(GOOD)
        (self.dir / "tests" / "2.in").write_text("1 1\n")
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing tests/2.out", result.stdout)
        for path in (self.dir / "tests").iterdir():
            path.unlink()
        self.assertIn("no sample inputs found", self.check().stdout)

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
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Samples: 3; failures: 0", result.stdout)


if __name__ == "__main__":
    unittest.main()
