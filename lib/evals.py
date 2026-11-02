"""Evaluates the served model through the server's Chat Completions API.

No agent sits in between: each suite sends requests itself and grades the
replies. ./bin/evals runs it; see that script for the settings.

Usage:
  python3 -I evals.py run OUT_DIR LABEL SUITE...   run suites, append to OUT_DIR/results.jsonl
  python3 -I evals.py report RESULTS_FILE...       summarize results, per label
  python3 -I evals.py list                         print each suite and its cases

Suites:
  tool-calls    conversations in evals/tool-calls/*.json: the model must call
                the right tools with arguments that match the schema and the
                expected values, read the tool results, and answer from them
  coding        problems in evals/coding/NAME/: the model writes a module from
                prompt.md, and the hidden tests next to it grade the module
  long-context  a generated inventory of hosts at several lengths up to the
                served window, with the fact asked about placed at several
                depths among near-duplicates
  speed         prefill and decode speed, and time to the first token, at
                several prompt lengths, from streamed replies

Each result is one JSON line: label, suite, case, sample, result (pass,
fail or error), seconds, detail, and for speed the measurements. Each
request and reply is kept under OUT_DIR/transcripts/.

Needs only the standard library and runs with the Python 3.9 that macOS
ships.
"""

import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVALS_DIR = ROOT / "evals"
SUITES = ("tool-calls", "coding", "long-context", "speed")

# A reply may make this many requests' worth of tool calls before a
# tool-calls case gives up; real agents loop longer, but a case that needs
# more than this is lost.
MAX_TOOL_ROUNDS = 8
# Seconds a coding problem's hidden tests may run.
CODING_TEST_SECONDS = 60
# Prompt lengths the long-context and speed suites try, in tokens, before
# being cut to the window.
LONG_CONTEXT_TOKENS = (4096, 16384, 32768, 65536, 131072, 262144)
SPEED_PROMPT_TOKENS = (1024, 8192, 32768)
# Where the fact sits in the long-context prompt, as a fraction of its length.
LONG_CONTEXT_DEPTHS = (0.1, 0.5, 0.9)
# Tokens kept free below the window for the question and the template.
WINDOW_SLACK_TOKENS = 1024
# The share of a nominal prompt size that prompts aim for.
PROMPT_SIZE_MARGIN = 0.98
# Tokens each speed request generates.
SPEED_OUTPUT_TOKENS = 256


class EvalError(Exception):
    """A request failed, as opposed to the model answering wrongly."""


def die(message):
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def env_int(name, default):
    value = os.environ.get(name, "")
    if not value:
        return default
    if not re.fullmatch(r"[1-9][0-9]*", value):
        die(f"{name} must be a positive whole number, not {value!r}")
    return int(value)


class Client:
    """Posts to /chat/completions of the served model."""

    def __init__(self, base_url, model, timeout_s, max_output_tokens, thinking=True):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.timeout_s = timeout_s
        self.max_output_tokens = max_output_tokens
        self.thinking = thinking

    def request_body(self, messages, tools=None, max_tokens=None, stream=False):
        body = {"model": self.model, "messages": messages, "max_tokens": max_tokens or self.max_output_tokens}
        if not self.thinking:
            # Qwen's chat templates read this; mlx-lm passes it on (server.py, chat_template_kwargs).
            body["chat_template_kwargs"] = {"enable_thinking": False}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if stream:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
        return body

    def post(self, body):
        request = urllib.request.Request(
            self.url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        try:
            return urllib.request.urlopen(request, timeout=self.timeout_s)
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            raise EvalError(f"HTTP {e.code} from {self.url}: {text[:500]}") from None
        except OSError as e:
            raise EvalError(f"POST {self.url} failed: {e}") from None

    def chat(self, messages, tools=None, max_tokens=None):
        """The reply message and the usage of one request."""
        body = self.request_body(messages, tools, max_tokens)
        with self.post(body) as response:
            raw = response.read().decode("utf-8", "replace")
        try:
            reply = json.loads(raw)
            return reply["choices"][0]["message"], reply.get("usage") or {}, body
        except (ValueError, KeyError, IndexError, TypeError):
            raise EvalError(f"the server did not send a chat completion: {raw[:500]}") from None

    def stream(self, messages, max_tokens):
        """Times a streamed reply: (seconds to the first token, seconds in all, usage)."""
        started = time.monotonic()
        first = None
        usage = None
        with self.post(self.request_body(messages, max_tokens=max_tokens, stream=True)) as response:
            for line in response:
                line = line.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    raise EvalError(f"the server sent a chunk that is not JSON: {data[:200]}") from None
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if first is None and (delta.get("content") or delta.get("reasoning") or delta.get("tool_calls")):
                        first = time.monotonic() - started
        total = time.monotonic() - started
        if first is None:
            raise EvalError("the streamed reply had no tokens")
        if not usage:
            raise EvalError("the streamed reply did not report its usage")
        return first, total, usage


class Recorder:
    """Appends results and keeps transcripts."""

    def __init__(self, out_dir, label):
        self.out_dir = Path(out_dir)
        self.label = label
        self.results = self.out_dir / "results.jsonl"
        self.transcripts = self.out_dir / "transcripts"
        self.transcripts.mkdir(parents=True, exist_ok=True)
        self.run = self.next_run()

    def next_run(self):
        """Repeat invocations add runs rather than overwrite earlier ones."""
        run = 0
        if self.results.exists():
            for line in self.results.read_text().splitlines():
                if line.strip():
                    run = max(run, json.loads(line).get("run", 0))
        return run + 1

    def add(self, suite, case, sample, result, seconds, detail="", transcript=None, **measurements):
        row = {"label": self.label, "run": self.run, "suite": suite, "case": case, "sample": sample,
               "result": result, "seconds": round(seconds, 2), "detail": detail}
        row.update(measurements)
        with self.results.open("a") as f:
            f.write(json.dumps(row) + "\n")
        if transcript is not None:
            name = f"run{self.run}-{suite}-{case}-{sample}.json"
            (self.transcripts / name).write_text(json.dumps(transcript, indent=1))
        shown = f"{result} in {seconds:.0f}s"
        if detail:
            shown += f" ({detail})"
        print(f"== {self.label} / {suite} / {case} sample {sample}: {shown}", flush=True)
        return row


# ---------------------------------------------------------------- tool calls

def check_schema(value, schema, where="arguments"):
    """Problems with `value` against the subset of JSON Schema tools use."""
    problems = []
    kind = schema.get("type")
    types = {"string": str, "integer": int, "number": (int, float), "boolean": bool,
             "array": list, "object": dict}
    if kind in types:
        ok = isinstance(value, types[kind])
        # JSON has no separate booleans among numbers; Python does.
        if kind in ("integer", "number") and isinstance(value, bool):
            ok = False
        if not ok:
            return [f"{where} should be {kind}, not {type(value).__name__} {value!r}"]
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{where} is {value!r}, not one of {schema['enum']}")
    if kind == "object":
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                problems.append(f"{where} lacks {name!r}")
        for name, item in value.items():
            if name in properties:
                problems += check_schema(item, properties[name], f"{where}.{name}")
            elif schema.get("additionalProperties", True) is False:
                problems.append(f"{where} has {name!r}, which the schema does not define")
    if kind == "array" and "items" in schema:
        for i, item in enumerate(value):
            problems += check_schema(item, schema["items"], f"{where}[{i}]")
    return problems


def matches(value, rule):
    """Whether one argument value satisfies an expectation from a case file."""
    if not isinstance(rule, dict):
        return value == rule
    for kind, expected in rule.items():
        if kind == "equals":
            ok = value == expected
        elif kind == "iequals":
            ok = isinstance(value, str) and value.strip().lower() == expected.lower()
        elif kind == "contains":
            ok = isinstance(value, str) and expected.lower() in value.lower()
        elif kind == "regex":
            ok = isinstance(value, str) and re.search(expected, value) is not None
        elif kind == "one_of":
            ok = value in expected
        elif kind == "near":
            ok = isinstance(value, (int, float)) and not isinstance(value, bool) and abs(value - expected[0]) <= expected[1]
        elif kind == "same_items":
            ok = isinstance(value, list) and sorted(map(json.dumps, value)) == sorted(map(json.dumps, expected))
        elif kind == "fields":
            ok = isinstance(value, dict) and all(k in value and matches(value[k], r) for k, r in expected.items())
        elif kind == "optional":
            # Checked by check_call: the argument may be left out.
            ok = True
        else:
            raise ValueError(f"unknown expectation {kind!r}")
        if not ok:
            return False
    return True


def call_arguments(call):
    """A tool call's name and its arguments as a dict, or an error string."""
    function = call.get("function") or {}
    name = function.get("name")
    raw = function.get("arguments")
    if isinstance(raw, dict):
        return name, raw, None
    try:
        args = json.loads(raw or "{}")
    except ValueError:
        return name, None, f"the arguments of {name} are not JSON: {raw!r}"
    if not isinstance(args, dict):
        return name, None, f"the arguments of {name} are not an object: {raw!r}"
    return name, args, None


def check_call(name, args, expected, schemas):
    """Why a call does not match one expected call, or None."""
    if name != expected["tool"]:
        return f"called {name}, expected {expected['tool']}"
    problems = check_schema(args, schemas[name])
    for arg, rule in expected.get("args", {}).items():
        if isinstance(rule, dict) and "absent" in rule:
            if arg in args:
                problems.append(f"{arg} should be left out, but is {args[arg]!r}")
        elif arg not in args:
            if not (isinstance(rule, dict) and rule.get("optional")):
                problems.append(f"{arg} is missing")
        elif not matches(args[arg], rule):
            problems.append(f"{arg} is {args[arg]!r}, expected {rule}")
    return "; ".join(problems) or None


def check_answer(text, rule):
    text_lower = text.lower()
    for word in rule.get("contains", []):
        alternatives = word if isinstance(word, list) else [word]
        if not any(a.lower() in text_lower for a in alternatives):
            return f"the answer lacks {' or '.join(map(repr, alternatives))}: {text[:300]!r}"
    for word in rule.get("not_contains", []):
        if word.lower() in text_lower:
            return f"the answer mentions {word!r}: {text[:300]!r}"
    return None


def run_tool_case(client, case):
    """Plays one conversation; returns (result, detail, transcript)."""
    schemas = {t["function"]["name"]: t["function"]["parameters"] for t in case["tools"]}
    messages = list(case["messages"])
    transcript = []
    steps = list(case["steps"])
    rounds = 0
    while steps:
        step = steps[0]
        if rounds >= MAX_TOOL_ROUNDS:
            return "fail", f"still not done after {MAX_TOOL_ROUNDS} replies", transcript
        rounds += 1
        message, usage, body = client.chat(messages, tools=case["tools"])
        transcript.append({"request": body, "reply": message, "usage": usage})
        calls = message.get("tool_calls") or []
        if "answer" in step:
            if calls:
                names = ", ".join(str((c.get("function") or {}).get("name")) for c in calls)
                return "fail", f"called {names} where an answer was expected", transcript
            if not (message.get("content") or "").strip():
                return "fail", no_answer(usage, client.max_output_tokens), transcript
            problem = check_answer(message.get("content") or "", step["answer"])
            return ("fail", problem, transcript) if problem else ("pass", "", transcript)
        if not calls:
            wanted = ", ".join(c["tool"] for c in step["calls"])
            return "fail", f"answered without calling {wanted}: {(message.get('content') or '')[:300]!r}", transcript
        # The calls of a step may come in one reply or spread over several.
        pending = step.setdefault("_pending", list(step["calls"]))
        results = []
        for call in calls:
            name, args, error = call_arguments(call)
            if error:
                return "fail", error, transcript
            reasons = []
            for expected in pending:
                reason = check_call(name, args, expected, schemas) if name in schemas else f"called {name}, which is not a tool"
                if reason is None:
                    pending.remove(expected)
                    results.append((call, expected["result"]))
                    break
                reasons.append(reason)
            else:
                return "fail", f"unexpected call {name}({json.dumps(args)}): {' | '.join(reasons)}", transcript
        messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
        for call, result in results:
            content = result if isinstance(result, str) else json.dumps(result)
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "name": call["function"]["name"],
                             "content": content})
        if not pending:
            steps.pop(0)
    return "pass", "", transcript


def tool_cases():
    cases = []
    for path in sorted((EVALS_DIR / "tool-calls").glob("*.json")):
        case = json.loads(path.read_text())
        case["name"] = path.stem
        cases.append(case)
    return cases


# -------------------------------------------------------------------- coding

def extract_code(text):
    """The last fenced Python block of a reply, or the whole reply.

    A reply cut off inside its last block has no closing fence; the code
    after the opening one is used, so the hidden tests say what is missing.
    """
    blocks = re.findall(r"```(?:python|py)?[ \t]*\n(.*?)```", text, re.DOTALL)
    opened = list(re.finditer(r"```(?:python|py)?[ \t]*\n", text))
    if opened and text.count("```") % 2 == 1:
        return text[opened[-1].end():]
    return blocks[-1] if blocks else text


def run_hidden_tests(problem_dir, code):
    """(passed, detail) for `code` as solution.py against the problem's tests."""
    work = Path(tempfile.mkdtemp(prefix="puny-eval-"))
    try:
        (work / "solution.py").write_text(code)
        for test in problem_dir.glob("test_*.py"):
            shutil.copy(test, work)
        # -E and -s keep the user's settings out; the current folder stays
        # importable so the tests find solution.py.
        command = [sys.executable, "-E", "-s", "-m", "unittest", "-q"]
        env = {"PATH": "/usr/bin:/bin", "HOME": str(work), "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            done = subprocess.run(command, cwd=work, env=env, capture_output=True, text=True,
                                  timeout=CODING_TEST_SECONDS, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return False, f"the tests ran over {CODING_TEST_SECONDS}s"
        if done.returncode == 0:
            return True, ""
        lines = [l for l in done.stderr.splitlines() if l.strip()]
        summary = lines[-1] if lines else f"exit status {done.returncode}"
        failed = [l for l in lines if l.startswith(("FAIL:", "ERROR:"))]
        # A module that does not import shows up as one _FailedTest; its own error says why.
        if any("_FailedTest" in l for l in failed):
            reasons = [l.strip() for l in lines if re.match(r"\s*\w+(Error|Exception):", l)]
            failed = ["the module does not import: " + (reasons[-1] if reasons else summary)]
        # A subtest names its inputs, which can be long; the transcript has the rest.
        failed = [l if len(l) <= 160 else l[:157] + "..." for l in failed]
        return False, "; ".join(failed[:3] + [summary])
    finally:
        shutil.rmtree(work, ignore_errors=True)


CODING_SYSTEM = (
    "You write correct, complete Python 3.9 modules using only the standard library. "
    "Reply with the whole module in one ```python block."
)


def no_answer(usage, max_output_tokens):
    """Why a reply has no text: out of tokens, most likely while thinking."""
    used = usage.get("completion_tokens") or 0
    if used >= max_output_tokens:
        return f"no answer: it used all {max_output_tokens} output tokens, so it was cut off, likely while thinking (raise EVAL_MAX_OUTPUT)"
    return "the reply has no text"


def run_coding_case(client, problem_dir):
    prompt = (problem_dir / "prompt.md").read_text()
    messages = [{"role": "system", "content": CODING_SYSTEM}, {"role": "user", "content": prompt}]
    message, usage, body = client.chat(messages)
    content = message.get("content") or ""
    transcript = [{"request": body, "reply": message, "usage": usage}]
    if not content.strip():
        return "fail", no_answer(usage, client.max_output_tokens), transcript
    passed, detail = run_hidden_tests(problem_dir, extract_code(content))
    if not passed and (usage.get("completion_tokens") or 0) >= client.max_output_tokens:
        detail = f"the reply was cut off at {client.max_output_tokens} tokens (raise EVAL_MAX_OUTPUT); {detail}"
    return ("pass" if passed else "fail"), detail, transcript


def coding_cases():
    return sorted(p for p in (EVALS_DIR / "coding").iterdir() if (p / "prompt.md").exists())


# -------------------------------------------------------------- long context

SERVICES = ("postgres", "redis", "nginx", "kafka", "etcd", "minio", "grafana", "vault",
            "consul", "rabbitmq", "clickhouse", "haproxy")
TEAMS = ("kappa", "lambda", "sigma", "omega", "delta", "theta", "zeta", "iota")


def inventory_line(rng, host):
    return (f"host-{host:05d} in rack R{rng.randint(1, 60):02d} runs {rng.choice(SERVICES)} "
            f"{rng.randint(9, 17)}.{rng.randint(0, 9)}, owner team-{rng.choice(TEAMS)}, "
            f"last patched 2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}.")


LONG_CONTEXT_QUESTION = (
    "Above is the host inventory. Which host runs billing-ledger-v3 and is not decommissioned, "
    "and in which rack is it? Answer with the host name and the rack.")
# The target and its decoys, on top of the inventory lines asked for.
LONG_CONTEXT_EXTRA_LINES = 4


def long_context_prompt(lines_before, lines_after, seed):
    """An inventory with one live billing-ledger-v3 host among decoys, and the answer.

    The decoys run billing-ledger-v2, or v3 on a host marked decommissioned,
    so only reading the record itself gives the answer.
    """
    rng = random.Random(seed)
    hosts = rng.sample(range(1, 99999), lines_before + lines_after + 8)
    target_host, target_rack = hosts[-1], rng.randint(1, 60)
    target = (f"host-{target_host:05d} in rack R{target_rack:02d} runs billing-ledger-v3 1.4, "
              f"owner team-ledger, last patched 2026-09-30.")
    decoys = [
        f"host-{hosts[-2]:05d} in rack R{rng.randint(1, 60):02d} runs billing-ledger-v2 0.9, owner team-ledger, last patched 2026-02-11.",
        f"host-{hosts[-3]:05d} in rack R{rng.randint(1, 60):02d} runs billing-ledger-v3 1.4, owner team-ledger, decommissioned 2026-08-01.",
        f"host-{hosts[-4]:05d} in rack R{rng.randint(1, 60):02d} runs billing-ledger-v2 1.1, owner team-ledger, last patched 2026-05-19.",
    ]
    before = [inventory_line(rng, h) for h in hosts[:lines_before]]
    after = [inventory_line(rng, h) for h in hosts[lines_before:lines_before + lines_after]]
    # Decoys go on both sides of the target, never next to it.
    for decoy in decoys:
        side = before if rng.random() < 0.5 and before else after
        side.insert(rng.randint(0, len(side)), decoy)
    text = "\n".join(before + [target] + after)
    return text, LONG_CONTEXT_QUESTION, (f"host-{target_host:05d}", f"R{target_rack:02d}")


class PromptSizes:
    """Token counts measured with the server's own tokenizer, to build prompts of a given size.

    line: tokens one inventory line takes; overhead: tokens of the chat
    template around a prompt; question: tokens the long-context question adds.
    """

    def __init__(self, client):
        rng = random.Random(7)
        sample = [inventory_line(rng, h) for h in range(1, 201)]
        short, full, asked = (self.count(client, text) for text in (
            "\n".join(sample[:20]), "\n".join(sample), "\n".join(sample[:20]) + "\n\n" + LONG_CONTEXT_QUESTION))
        self.line = (full - short) / (len(sample) - 20)
        self.overhead = short - 20 * self.line
        self.question = asked - short

    @staticmethod
    def count(client, text):
        _, usage, _ = client.chat([{"role": "user", "content": text}], max_tokens=1)
        if not usage.get("prompt_tokens"):
            raise EvalError("the server did not report prompt_tokens")
        return usage["prompt_tokens"]

    def lines_for(self, tokens, extra_tokens=0, extra_lines=0):
        """How many inventory lines fill `tokens`, leaving room for the rest of the prompt.

        Lines differ in length, so it aims PROMPT_SIZE_MARGIN under, to stay
        within `tokens` whichever lines are drawn.
        """
        room = tokens * PROMPT_SIZE_MARGIN - self.overhead - extra_tokens
        return max(int(room / self.line) - extra_lines, 1)


def long_context_sizes(window_tokens, max_output_tokens, cap_tokens):
    room = min(window_tokens - max_output_tokens - WINDOW_SLACK_TOKENS, cap_tokens)
    if room <= 0:
        return []
    # A window smaller than every listed size still gets one case, as large as fits.
    return [t for t in LONG_CONTEXT_TOKENS if t <= room] or [room]


def long_context_cases(window_tokens, max_output_tokens, cap_tokens):
    return [(size, depth) for size in long_context_sizes(window_tokens, max_output_tokens, cap_tokens)
            for depth in LONG_CONTEXT_DEPTHS]


def run_long_context_case(client, size, depth, sizes, seed):
    lines = sizes.lines_for(size, sizes.question, LONG_CONTEXT_EXTRA_LINES)
    before = int(lines * depth)
    text, question, answer = long_context_prompt(before, lines - before, seed)
    message, usage, body = client.chat([{"role": "user", "content": text + "\n\n" + question}])
    content = message.get("content") or ""
    # The prompt itself is large; the transcript keeps its length, not its text.
    body = dict(body, messages=[{"role": "user", "chars": len(text) + len(question), "question": question}])
    transcript = [{"request": body, "reply": message, "usage": usage, "answer": answer}]
    missing = [a for a in answer if a.lower() not in content.lower()]
    measured = {"prompt_tokens": usage.get("prompt_tokens")}
    if not content.strip():
        return "fail", no_answer(usage, client.max_output_tokens), transcript, measured
    if missing:
        return "fail", f"the answer lacks {', '.join(missing)}: {content[:200]!r}", transcript, measured
    return "pass", "", transcript, measured


# --------------------------------------------------------------------- speed

# The words around the filler of a speed prompt, in tokens; a generous guess.
SPEED_PROMPT_EXTRA_TOKENS = 64


def filler(tokens_wanted, sizes, seed):
    rng = random.Random(seed)
    lines = sizes.lines_for(tokens_wanted, SPEED_PROMPT_EXTRA_TOKENS)
    return "\n".join(inventory_line(rng, h) for h in range(lines))


def speed_sizes(window_tokens):
    return [t for t in SPEED_PROMPT_TOKENS if t + SPEED_OUTPUT_TOKENS + WINDOW_SLACK_TOKENS <= window_tokens]


def run_speed_case(client, size, sizes, seed):
    # A fresh prefix every time, so no cached prompt shortens the prefill.
    text = f"Request {seed}-{time.time_ns()}.\n" + filler(size, sizes, seed)
    prompt = text + "\n\nCount from 1 upwards, one number per line, and do not stop."
    first_s, total_s, usage = client.stream([{"role": "user", "content": prompt}], SPEED_OUTPUT_TOKENS)
    prompt_tokens = usage.get("prompt_tokens") or 0
    output_tokens = usage.get("completion_tokens") or 0
    decode_s = total_s - first_s
    measured = {
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "first_token_s": round(first_s, 3),
        "prefill_tps": round(prompt_tokens / first_s, 1) if first_s > 0 else None,
        "decode_tps": round((output_tokens - 1) / decode_s, 1) if output_tokens > 1 and decode_s > 0 else None,
    }
    if output_tokens < 2:
        return "fail", f"only {output_tokens} tokens came back, too few to time decoding", None, measured
    return "pass", "", None, measured


# ---------------------------------------------------------------------- main

def selected(name, wanted):
    return not wanted or name in wanted


def run(out_dir, label, suites):
    for suite in suites:
        if suite not in SUITES:
            die(f"unknown suite {suite!r}; expected any of: {' '.join(SUITES)}")
    base_url = os.environ.get("MLX_BASE_URL") or die("MLX_BASE_URL is not set")
    model = os.environ.get("MLX_MODEL") or die("MLX_MODEL is not set")
    window_tokens = env_int("QWEN_CONTEXT", 0) or die("QWEN_CONTEXT is not set")
    max_output_tokens = env_int("EVAL_MAX_OUTPUT", env_int("QWEN_MAX_OUTPUT", 8192))
    samples = env_int("EVAL_SAMPLES", 1)
    timeout_s = env_int("EVAL_TIMEOUT", 900)
    cap_tokens = env_int("EVAL_LONG_CONTEXT", window_tokens)
    wanted = set(os.environ.get("EVAL_CASES", "").split())
    thinking = os.environ.get("EVAL_THINKING", "") or "on"
    if thinking not in ("on", "off"):
        die(f"EVAL_THINKING must be on or off, not {thinking!r}")
    client = Client(base_url, model, timeout_s, max_output_tokens, thinking == "on")
    recorder = Recorder(out_dir, label)

    def attempt(suite, case, sample, fn):
        started = time.monotonic()
        try:
            out = fn()
        except EvalError as e:
            return recorder.add(suite, case, sample, "error", time.monotonic() - started, str(e))
        result, detail, transcript = out[:3]
        measured = out[3] if len(out) > 3 else {}
        return recorder.add(suite, case, sample, result, time.monotonic() - started, detail or "", transcript, **measured)

    sizes = None
    if any(s in suites for s in ("long-context", "speed")):
        try:
            sizes = PromptSizes(client)
        except EvalError as e:
            die(f"cannot measure prompt sizes: {e}")

    for suite in suites:
        for sample in range(1, samples + 1):
            if suite == "tool-calls":
                for case in tool_cases():
                    if selected(case["name"], wanted):
                        attempt(suite, case["name"], sample, lambda case=case: run_tool_case(client, json.loads(json.dumps(case))))
            elif suite == "coding":
                for problem in coding_cases():
                    if selected(problem.name, wanted):
                        attempt(suite, problem.name, sample, lambda problem=problem: run_coding_case(client, problem))
            elif suite == "long-context":
                cases = long_context_cases(window_tokens, max_output_tokens, cap_tokens)
                if not cases:
                    die(f"the {window_tokens}-token window leaves no room for a long-context prompt")
                for size, depth in cases:
                    name = f"{size // 1024}k-at-{int(depth * 100)}"
                    if selected(name, wanted):
                        seed = size * 1000 + int(depth * 100) + sample
                        attempt(suite, name, sample, lambda size=size, depth=depth, seed=seed: run_long_context_case(
                            client, size, depth, sizes, seed))
            elif suite == "speed":
                for size in speed_sizes(window_tokens):
                    name = f"{size // 1024}k"
                    if selected(name, wanted):
                        attempt(suite, name, sample, lambda size=size: run_speed_case(client, size, sizes, sample))


def report(paths):
    rows = []
    for path in paths:
        for line in Path(path).read_text().splitlines():
            if line.strip():
                rows.append(json.loads(line))
    if not rows:
        die("the results files hold no results")
    labels = sorted({r["label"] for r in rows})
    w = max(16, *(len(label) for label in labels))
    print(f"{'label':<{w}} {'suite':<13} {'passed':>9} {'errors':>7}  failed cases")
    for label in labels:
        for suite in SUITES:
            mine = [r for r in rows if r["label"] == label and r["suite"] == suite]
            if not mine:
                continue
            passed = sum(r["result"] == "pass" for r in mine)
            errors = sum(r["result"] == "error" for r in mine)
            failed = sorted({r["case"] for r in mine if r["result"] != "pass"})
            shown = ", ".join(failed[:6]) + (f" and {len(failed) - 6} more" if len(failed) > 6 else "")
            print(f"{label:<{w}} {suite:<13} {passed:>4}/{len(mine):<4} {errors:>7}  {shown}")
    speed = [r for r in rows if r["suite"] == "speed" and r["result"] == "pass"]
    if speed:
        print()
        print(f"{'label':<{w}} {'prompt':>8} {'prefill tok/s':>14} {'decode tok/s':>13} {'first token s':>14}")
        for label in labels:
            for case in sorted({r["case"] for r in speed if r["label"] == label}, key=lambda c: int(c.rstrip("k"))):
                mine = [r for r in speed if r["label"] == label and r["case"] == case]

                def median(key):
                    values = [r[key] for r in mine if r.get(key) is not None]
                    return statistics.median(values) if values else float("nan")

                print(f"{label:<{w}} {case:>8} {median('prefill_tps'):>14.1f} {median('decode_tps'):>13.1f} "
                      f"{median('first_token_s'):>14.2f}")


def list_cases():
    print("tool-calls:   " + " ".join(c["name"] for c in tool_cases()))
    print("coding:       " + " ".join(p.name for p in coding_cases()))
    print("long-context: SIZEk-at-DEPTH for sizes " + ", ".join(f"{t // 1024}k" for t in LONG_CONTEXT_TOKENS)
          + " that fit the window, at depths " + ", ".join(str(int(d * 100)) for d in LONG_CONTEXT_DEPTHS))
    print("speed:        " + " ".join(f"{t // 1024}k" for t in SPEED_PROMPT_TOKENS) + " that fit the window")


def main(argv):
    if len(argv) >= 4 and argv[1] == "run":
        run(argv[2], argv[3], argv[4:] or list(SUITES))
    elif len(argv) >= 3 and argv[1] == "report":
        report(argv[2:])
    elif argv[1:] == ["list"]:
        list_cases()
    else:
        die(__doc__.split("\n\n")[1])


if __name__ == "__main__":
    main(sys.argv)
