"""lib/evals.py, bin/evals and the cases under evals/."""

import json
import re
import sys
import unittest
from pathlib import Path

from helpers import REPO, FakeServer, ServerCase, mlx_health_routes

sys.path.insert(0, str(REPO / "lib"))
import evals  # noqa: E402

SOLUTIONS = Path(__file__).resolve().parent / "fixtures" / "eval-solutions"
PROBLEMS = sorted(p.name for p in (REPO / "evals" / "coding").iterdir() if p.is_dir())
CASES = {c["name"]: c for c in evals.tool_cases()}


def completion(content="", tool_calls=None, prompt_tokens=10, completion_tokens=5):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}}


def calls(*named_args):
    return [{"id": f"call_{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
            for i, (name, args) in enumerate(named_args)]


def fake_tokens(body):
    """A stand-in tokenizer: one token per four characters of every message."""
    return sum(len(m.get("content") or "") for m in body["messages"]) // 4 + 3


class PerfectModel:
    """Answers each tool case with its examples, each coding problem with its reference
    solution, and each long-context question with the right host."""

    def __init__(self, one_call_per_reply=False):
        self.one_call_per_reply = one_call_per_reply

    def tool_reply(self, body):
        user = next(m["content"] for m in body["messages"] if m["role"] == "user")
        case = next(c for c in CASES.values() if c["messages"][-1]["content"] == user)
        done = sum(1 for m in body["messages"] if m["role"] == "tool")
        for step in case["steps"]:
            if "answer" in step:
                return completion(step["answer"]["example"])
            if done >= len(step["calls"]):
                done -= len(step["calls"])
                continue
            todo = step["calls"][done:]
            if self.one_call_per_reply:
                todo = todo[:1]
            return completion(tool_calls=calls(*((c["tool"], c["example"]) for c in todo)))
        raise AssertionError("the conversation went past the last step")

    def __call__(self, handler, raw):
        body = json.loads(raw)
        tokens = fake_tokens(body)
        if body.get("max_tokens") == 1:
            return completion("x", prompt_tokens=tokens, completion_tokens=1)
        if body.get("tools"):
            return self.tool_reply(body)
        if body["messages"][0]["content"] == evals.CODING_SYSTEM:
            prompt = body["messages"][1]["content"]
            name = next(p for p in PROBLEMS if (REPO / "evals" / "coding" / p / "prompt.md").read_text() == prompt)
            code = (SOLUTIONS / name / "solution.py").read_text()
            return completion(f"Here it is.\n\n```python\n{code}```\n", prompt_tokens=tokens)
        text = body["messages"][-1]["content"]
        if "billing-ledger-v3" in text:
            line = next(l for l in text.splitlines() if "billing-ledger-v3" in l and "decommissioned" not in l)
            host, rack = re.match(r"(host-\d+) in rack (R\d+)", line).groups()
            return completion(f"{host}, in rack {rack}.", prompt_tokens=tokens)
        raise AssertionError(f"unexpected request: {text[:100]}")


def stream_reply(handler, raw):
    body = json.loads(raw)
    chunks = [{"choices": [{"delta": {"content": str(i) + "\n"}}]} for i in range(1, 6)]
    chunks.append({"choices": [], "usage": {"prompt_tokens": fake_tokens(body), "completion_tokens": 5}})
    data = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return data.encode(), {"Content-Type": "text/event-stream"}


class Model:
    """Routes a fake server's chat completions: streams to `stream`, the rest to `chat`."""

    def __init__(self, chat, stream=stream_reply):
        self.chat, self.stream = chat, stream

    def __call__(self, handler, raw):
        return self.stream(handler, raw) if json.loads(raw).get("stream") else self.chat(handler, raw)


class EvalCase(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.server = FakeServer(mlx_health_routes())
        self.addCleanup(self.server.stop)
        self.out = Path(tempfile.mkdtemp(prefix="puny-evals-"))
        import shutil

        self.addCleanup(shutil.rmtree, self.out)

    def serve(self, chat, stream=stream_reply):
        self.server.routes["POST /v1/chat/completions"] = (200, Model(chat, stream))

    def run_suites(self, *suites, window=32768, **env):
        import contextlib
        import io
        import os

        values = {"MLX_BASE_URL": self.server.base_url, "MLX_MODEL": "default_model", "QWEN_CONTEXT": str(window),
                  "QWEN_MAX_OUTPUT": "2048", "EVAL_CASES": "", "EVAL_SAMPLES": "", "EVAL_TIMEOUT": "30",
                  "EVAL_MAX_OUTPUT": "", "EVAL_LONG_CONTEXT": "", "EVAL_THINKING": ""}
        values.update(env)
        saved = {k: os.environ.get(k) for k in values}
        os.environ.update(values)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                evals.run(str(self.out), "test", list(suites))
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        rows = [json.loads(l) for l in (self.out / "results.jsonl").read_text().splitlines()]
        latest = max(r["run"] for r in rows)
        return [r for r in rows if r["run"] == latest]


class CasesTest(unittest.TestCase):
    """The case files themselves."""

    def test_every_tool_case_is_well_formed(self):
        self.assertEqual(len(CASES), 10)
        for name, case in CASES.items():
            with self.subTest(name):
                schemas = {t["function"]["name"]: t["function"]["parameters"] for t in case["tools"]}
                self.assertTrue(case["description"])
                self.assertEqual(case["messages"][-1]["role"], "user")
                self.assertIn("answer", case["steps"][-1])
                for step in case["steps"]:
                    self.assertEqual(len(step), 1, "a step either calls tools or answers")
                    if "answer" in step:
                        self.assertIsNone(evals.check_answer(step["answer"]["example"], step["answer"]))
                        continue
                    for call in step["calls"]:
                        self.assertIn(call["tool"], schemas)
                        self.assertIn("result", call)
                        self.assertIsNone(evals.check_call(call["tool"], call["example"], call, schemas))

    def test_tool_schemas_forbid_unknown_arguments(self):
        for name, case in CASES.items():
            for tool in case["tools"]:
                with self.subTest(case=name, tool=tool["function"]["name"]):
                    self.assertIs(tool["function"]["parameters"].get("additionalProperties"), False)

    def test_every_coding_problem_is_complete(self):
        self.assertEqual(len(PROBLEMS), 10)
        for name in PROBLEMS:
            with self.subTest(name):
                folder = REPO / "evals" / "coding" / name
                self.assertEqual(sorted(p.name for p in folder.iterdir()), ["prompt.md", f"test_{name}.py"])
                self.assertIn("solution.py", (folder / "prompt.md").read_text())

    def test_reference_solutions_pass(self):
        for name in PROBLEMS:
            with self.subTest(name):
                passed, detail = evals.run_hidden_tests(REPO / "evals" / "coding" / name, (SOLUTIONS / name / "solution.py").read_text())
                self.assertTrue(passed, detail)

    def test_empty_and_naive_solutions_fail(self):
        for name in PROBLEMS:
            with self.subTest(name):
                passed, detail = evals.run_hidden_tests(REPO / "evals" / "coding" / name, "x = 1\n")
                self.assertFalse(passed)
                self.assertRegex(detail, r"FAILED|Error")

    def test_slow_solutions_fail(self):
        # Right answers, but too slow for the time-budget tests.
        slow_cov = (
            "def covered(intervals, k):\n"
            "    if k <= 0 or any(s > e or w <= 0 for s, e, w in intervals):\n"
            "        raise ValueError\n"
            "    points = sorted({p for s, e, _ in intervals for p in (s, e)})\n"
            "    out = []\n"
            "    for a, b in zip(points, points[1:]):\n"
            "        if sum(w for s, e, w in intervals if s <= a and b <= e) >= k:\n"
            "            if out and out[-1][1] == a:\n"
            "                out[-1] = (out[-1][0], b)\n"
            "            else:\n"
            "                out.append((a, b))\n"
            "    return out\n")
        original = evals.CODING_TEST_SECONDS
        evals.CODING_TEST_SECONDS = 10
        self.addCleanup(setattr, evals, "CODING_TEST_SECONDS", original)
        passed, detail = evals.run_hidden_tests(REPO / "evals" / "coding" / "interval_coverage", slow_cov)
        self.assertFalse(passed)
        # Either the timing test fails, or the whole run is stopped first.
        self.assertRegex(detail, r"FAIL: test_large_input_is_fast|ran over 10s")


class ToolCallsTest(EvalCase):
    def test_a_perfect_model_passes_every_case(self):
        self.serve(PerfectModel())
        rows = self.run_suites("tool-calls")
        self.assertEqual({r["case"]: r["result"] for r in rows}, {name: "pass" for name in CASES}, rows)

    def test_calls_may_come_one_reply_at_a_time(self):
        self.serve(PerfectModel(one_call_per_reply=True))
        rows = self.run_suites("tool-calls", EVAL_CASES="two-cities")
        self.assertEqual([r["result"] for r in rows], ["pass"])

    def failure(self, case, reply, **expect):
        self.serve(lambda handler, raw: reply)
        rows = self.run_suites("tool-calls", EVAL_CASES=case)
        self.assertEqual([r["result"] for r in rows], ["fail"], rows)
        for text in expect.values():
            self.assertIn(text, rows[0]["detail"])

    def test_answering_instead_of_calling_fails(self):
        self.failure("unit-conversion", completion("About 42 km."), detail="answered without calling convert_units")

    def test_wrong_tool_fails(self):
        self.failure("many-similar-tools", completion(tool_calls=calls(("merge_pull_request", {"repo": "acme/storefront", "number": 412}))),
                     detail="called merge_pull_request, expected get_pull_request")

    def test_schema_violations_fail(self):
        self.failure("typed-arguments", completion(tool_calls=calls(("create_event", {
            "title": "Design review", "start": "2026-11-03T14:30", "duration_minutes": "90", "attendees": ["ana@studio.io", "bo@studio.io"]}))),
            detail="should be integer")
        self.failure("unit-conversion", completion(tool_calls=calls(("convert_units", {"value": 26.2, "from_unit": "miles", "to_unit": "km"}))),
                     detail="not one of")
        self.failure("unit-conversion", completion(tool_calls=calls(("convert_units", {"value": 26.2, "from_unit": "mi", "to_unit": "km", "precise": True}))),
                     detail="does not define")

    def test_wrong_values_fail(self):
        self.failure("sequential-lookup", completion(tool_calls=calls(("find_customer", {"email": "dana@example.com"}))),
                     detail="email is 'dana@example.com'")
        self.failure("optional-arguments", completion(tool_calls=calls(("search_code", {"query": "parse_header", "path": "src/net", "case_sensitive": True, "max_results": 5}))),
                     detail="max_results should be left out")

    def test_arguments_that_are_not_json_fail(self):
        bad = [{"id": "c", "type": "function", "function": {"name": "convert_units", "arguments": "{value: 26.2"}}]
        self.failure("unit-conversion", completion(tool_calls=bad), detail="not JSON")

    def test_calling_a_tool_where_none_is_needed_fails(self):
        self.failure("no-tool-needed", completion(tool_calls=calls(("search_web", {"query": "17*23"}))), detail="where an answer was expected")

    def test_wrong_final_answer_fails(self):
        model = PerfectModel()

        def reply(handler, raw):
            out = model(handler, raw)
            if "tool_calls" not in out["choices"][0]["message"]:
                out["choices"][0]["message"]["content"] = "The open order is A-1019."
            return out

        self.serve(reply)
        rows = self.run_suites("tool-calls", EVAL_CASES="sequential-lookup")
        self.assertEqual(rows[0]["result"], "fail")
        self.assertIn("lacks 'A-1023'", rows[0]["detail"])

    def test_naming_a_word_the_answer_must_not_have_fails(self):
        self.assertIn("mentions", evals.check_answer("it is 42 or 41", {"contains": ["42"], "not_contains": ["41"]}))

    def test_a_model_that_never_stops_calling_fails(self):
        self.serve(lambda handler, raw: completion(tool_calls=calls(("read_file", {"path": "config.yml"}))))
        rows = self.run_suites("tool-calls", EVAL_CASES="recover-from-error")
        self.assertEqual(rows[0]["result"], "fail")
        self.assertIn("unexpected call read_file", rows[0]["detail"])

    def test_server_errors_are_errors_not_failures(self):
        self.server.routes["POST /v1/chat/completions"] = (500, {"error": "the model fell over"})
        rows = self.run_suites("tool-calls", EVAL_CASES="unit-conversion")
        self.assertEqual(rows[0]["result"], "error")
        self.assertIn("HTTP 500", rows[0]["detail"])
        self.assertIn("the model fell over", rows[0]["detail"])

    def test_transcripts_are_kept(self):
        self.serve(PerfectModel())
        self.run_suites("tool-calls", EVAL_CASES="sequential-lookup")
        transcript = json.loads((self.out / "transcripts" / "run1-tool-calls-sequential-lookup-1.json").read_text())
        self.assertEqual(len(transcript), 3)
        self.assertEqual(transcript[1]["request"]["messages"][-1]["role"], "tool")


class CodingTest(EvalCase):
    def test_reference_solutions_pass_through_the_server(self):
        self.serve(PerfectModel())
        rows = self.run_suites("coding")
        self.assertEqual({r["case"]: r["result"] for r in rows}, {name: "pass" for name in PROBLEMS}, rows)

    def test_wrong_code_fails_with_the_failing_tests(self):
        self.serve(lambda handler, raw: completion("```python\ndef split(total_cents, weights):\n    return [total_cents]\n```"))
        rows = self.run_suites("coding", EVAL_CASES="money_split")
        self.assertEqual(rows[0]["result"], "fail")
        self.assertIn("FAIL", rows[0]["detail"])

    def test_the_last_code_block_is_graded(self):
        self.assertEqual(evals.extract_code("first\n```python\na = 1\n```\nthen\n```\nb = 2\n```\n"), "b = 2\n")
        self.assertEqual(evals.extract_code("no fences at all"), "no fences at all")
        self.assertEqual(evals.extract_code("Here:\n```python\nimport re\ndef f(\n"), "import re\ndef f(\n")
        self.assertEqual(evals.extract_code("```python\na = 1\n```\nand ```python\nb ="), "b =")

    def test_a_module_that_does_not_import_says_why(self):
        passed, detail = evals.run_hidden_tests(REPO / "evals" / "coding" / "calc", "def evaluate(:\n")
        self.assertFalse(passed)
        self.assertIn("the module does not import: SyntaxError", detail)

    def test_a_cut_off_reply_says_so(self):
        self.serve(lambda handler, raw: completion("```python\ndef split(a, b):\n    return [", completion_tokens=json.loads(raw)["max_tokens"]))
        rows = self.run_suites("coding", EVAL_CASES="money_split", EVAL_MAX_OUTPUT="500")
        self.assertIn("cut off at 500 tokens", rows[0]["detail"])
        self.assertIn("does not import", rows[0]["detail"])

    def test_code_that_hangs_is_stopped(self):
        original = evals.CODING_TEST_SECONDS
        evals.CODING_TEST_SECONDS = 2
        self.addCleanup(setattr, evals, "CODING_TEST_SECONDS", original)
        passed, detail = evals.run_hidden_tests(REPO / "evals" / "coding" / "calc", "while True:\n    pass\n")
        self.assertFalse(passed)
        self.assertIn("over 2s", detail)

    def test_an_empty_reply_fails(self):
        self.serve(lambda handler, raw: completion(""))
        rows = self.run_suites("coding", EVAL_CASES="calc")
        self.assertEqual(rows[0]["result"], "fail")
        self.assertIn("no text", rows[0]["detail"])

    def test_running_out_of_tokens_says_so(self):
        self.serve(lambda handler, raw: completion("", completion_tokens=json.loads(raw)["max_tokens"]))
        for suite, case in (("coding", "calc"), ("tool-calls", "no-tool-needed")):
            with self.subTest(suite):
                rows = self.run_suites(suite, EVAL_CASES=case, EVAL_MAX_OUTPUT="300")
                self.assertEqual(rows[0]["result"], "fail")
                self.assertIn("used all 300 output tokens", rows[0]["detail"])


class LongContextTest(EvalCase):
    def test_the_prompt_hides_one_answer_among_decoys(self):
        text, question, (host, rack) = evals.long_context_prompt(300, 100, seed=5)
        lines = text.splitlines()
        target = [i for i, l in enumerate(lines) if "billing-ledger-v3" in l and "decommissioned" not in l]
        self.assertEqual(len(target), 1)
        self.assertIn(host, lines[target[0]])
        self.assertIn(f"rack {rack}", lines[target[0]])
        self.assertEqual(sum("billing-ledger" in l for l in lines), 4)
        self.assertEqual(text.count(host), 1)
        # The target sits at its depth: 300 inventory lines before it, give or take the decoys.
        self.assertTrue(300 <= target[0] <= 303, target[0])
        self.assertIn("not decommissioned", question)

    def test_prompts_are_reproducible(self):
        self.assertEqual(evals.long_context_prompt(50, 50, 9), evals.long_context_prompt(50, 50, 9))
        self.assertNotEqual(evals.long_context_prompt(50, 50, 9)[2], evals.long_context_prompt(50, 50, 10)[2])

    def test_sizes_fit_the_window(self):
        self.assertEqual(evals.long_context_sizes(262144, 8192, 262144), [4096, 16384, 32768, 65536, 131072])
        self.assertEqual(evals.long_context_sizes(262144, 8192, 20000), [4096, 16384])
        self.assertEqual(evals.long_context_sizes(4000, 1024, 10 ** 6), [4000 - 1024 - evals.WINDOW_SLACK_TOKENS])
        self.assertEqual(evals.long_context_sizes(2000, 1024, 10 ** 6), [])

    def test_a_perfect_model_passes_and_the_prompts_have_the_right_size(self):
        self.serve(PerfectModel())
        rows = self.run_suites("long-context", window=40000)
        self.assertEqual(sorted(r["case"] for r in rows),
                         sorted(f"{s}k-at-{d}" for s in (4, 16, 32) for d in (10, 50, 90)))
        self.assertTrue(all(r["result"] == "pass" for r in rows), rows)
        for row in rows:
            wanted = int(row["case"].split("k")[0]) * 1024
            with self.subTest(row["case"]):
                self.assertLessEqual(row["prompt_tokens"], wanted)
                self.assertGreater(row["prompt_tokens"], wanted * 0.9)

    def test_a_decoy_answer_fails(self):
        def decoy(handler, raw):
            body = json.loads(raw)
            if body.get("max_tokens") == 1:
                return completion("x", prompt_tokens=fake_tokens(body))
            line = next(l for l in body["messages"][-1]["content"].splitlines() if "decommissioned" in l)
            host, rack = re.match(r"(host-\d+) in rack (R\d+)", line).groups()
            return completion(f"{host} in {rack}")

        self.serve(decoy)
        rows = self.run_suites("long-context", window=12000, EVAL_CASES="4k-at-50")
        self.assertEqual(rows[0]["result"], "fail")
        self.assertIn("lacks host-", rows[0]["detail"])


class SpeedTest(EvalCase):
    def test_measurements(self):
        self.serve(PerfectModel())
        rows = self.run_suites("speed", window=40000)
        self.assertEqual([r["case"] for r in rows], ["1k", "8k", "32k"])
        for row in rows:
            with self.subTest(row["case"]):
                self.assertEqual(row["result"], "pass")
                self.assertEqual(row["output_tokens"], 5)
                self.assertGreater(row["prefill_tps"], 0)
                self.assertGreater(row["decode_tps"], 0)
                self.assertGreater(row["prompt_tokens"], int(row["case"].rstrip("k")) * 1024 * 0.9)

    def test_sizes_that_do_not_fit_are_skipped(self):
        self.serve(PerfectModel())
        rows = self.run_suites("speed", window=10000)
        self.assertEqual([r["case"] for r in rows], ["1k", "8k"])

    def test_a_stream_without_usage_is_an_error(self):
        def no_usage(handler, raw):
            return b'data: {"choices": [{"delta": {"content": "1"}}]}\n\ndata: [DONE]\n\n', {"Content-Type": "text/event-stream"}

        self.serve(PerfectModel(), stream=no_usage)
        rows = self.run_suites("speed", window=4000)
        self.assertEqual(rows[0]["result"], "error")
        self.assertIn("usage", rows[0]["detail"])


class RecordingTest(EvalCase):
    def test_runs_are_numbered_and_appended(self):
        self.serve(PerfectModel())
        self.run_suites("tool-calls", EVAL_CASES="no-tool-needed")
        self.run_suites("tool-calls", EVAL_CASES="no-tool-needed", EVAL_SAMPLES="2")
        rows = [json.loads(l) for l in (self.out / "results.jsonl").read_text().splitlines()]
        self.assertEqual([(r["run"], r["sample"]) for r in rows], [(1, 1), (2, 1), (2, 2)])

    def test_report(self):
        import contextlib
        import io

        self.serve(PerfectModel())
        self.run_suites("tool-calls", "speed", window=4000, EVAL_CASES="no-tool-needed unit-conversion 1k")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            evals.report([str(self.out / "results.jsonl")])
        text = out.getvalue()
        self.assertRegex(text, r"test +tool-calls +2/2")
        self.assertRegex(text, r"test +speed +1/1")
        self.assertIn("prefill tok/s", text)

    def test_thinking_can_be_turned_off(self):
        self.serve(PerfectModel())
        self.run_suites("tool-calls", EVAL_CASES="no-tool-needed")
        self.run_suites("tool-calls", EVAL_CASES="no-tool-needed", EVAL_THINKING="off")
        bodies = [json.loads(b) for m, p, b in self.server.requests if p.endswith("/chat/completions")]
        self.assertNotIn("chat_template_kwargs", bodies[0])
        self.assertEqual(bodies[-1]["chat_template_kwargs"], {"enable_thinking": False})

    def test_bad_settings(self):
        import contextlib
        import io

        for env, message in (({"EVAL_SAMPLES": "0"}, "EVAL_SAMPLES"), ({"EVAL_TIMEOUT": "soon"}, "EVAL_TIMEOUT"),
                             ({"EVAL_THINKING": "maybe"}, "EVAL_THINKING")):
            with self.subTest(env), contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit):
                self.run_suites("tool-calls", **env)
            self.assertIn(message, err.getvalue())


class BinEvalsTest(ServerCase):
    """bin/evals, run with the bash and Python 3.9 a fresh Mac has."""

    def setUp(self):
        super().setUp()
        self.server.routes["POST /v1/chat/completions"] = (200, Model(PerfectModel()))

    def test_runs_and_reports(self):
        result = self.box.run(["bin/evals", "small", "tool-calls", "coding"], extra_env=dict(self.env, EVAL_CASES="two-cities calc"), timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("== small / tool-calls / two-cities sample 1: pass", result.stdout)
        self.assertIn("== small / coding / calc sample 1: pass", result.stdout)
        rows = [json.loads(l) for l in (self.box.root / "state" / "evals" / "small" / "results.jsonl").read_text().splitlines()]
        self.assertEqual([r["result"] for r in rows], ["pass", "pass"])
        report = self.box.run(["bin/evals", "report"], extra_env=self.env)
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertRegex(report.stdout, r"small +coding +1/1")
        only = self.box.run(["bin/evals", "report", "small"], extra_env=self.env)
        self.assertEqual(only.returncode, 0, only.stderr)
        self.assertEqual(only.stdout, report.stdout)

    def test_the_window_comes_from_the_served_record(self):
        result = self.box.run(["bin/evals", "small", "speed"], extra_env=self.env, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        # The record says 262,144 tokens, so every speed size runs.
        for size in ("1k", "8k", "32k"):
            self.assertIn(f"/ speed / {size} sample 1: pass", result.stdout)

    def test_bad_arguments(self):
        for args, message in ((["bin/evals"], "usage:"), (["bin/evals", "bad label"], "LABEL 'bad label' may contain"),
                              (["bin/evals", ".hidden"], "may not start with '.'"), (["bin/evals", "report", "../x"], "LABEL '../x'"),
                              (["bin/evals", "x", "vibes"], "unknown suite 'vibes'"), (["bin/evals", "report", "x"], "no results for x")):
            with self.subTest(args):
                result = self.box.run(args, extra_env=self.env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
        result = self.box.run(["bin/evals", "x"], extra_env=dict(self.env, EVAL_SAMPLES="many"))
        self.assertIn("EVAL_SAMPLES must be a positive whole number", result.stderr)

    def test_report_without_results(self):
        result = self.box.run(["bin/evals", "report"], extra_env=self.env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no results", result.stderr)

    def test_needs_a_running_server(self):
        self.server.stop()
        result = self.box.run(["bin/evals", "x", "tool-calls"], extra_env=self.env)
        self.assertNotEqual(result.returncode, 0)

    def test_list(self):
        result = self.box.run(["bin/evals", "list"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("coding:       calc cron_next", result.stdout)


if __name__ == "__main__":
    unittest.main()
