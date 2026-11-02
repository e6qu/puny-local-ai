"""Runs a workflow: a state machine whose states are fresh agent runs and commands.

    python3 lib/workflow.py check GRAPH_DIR
    python3 lib/workflow.py run GRAPH_DIR FOLDER

GRAPH_DIR holds graph.jsonc and the prompt files it names; FOLDER is where
every step works and the only way steps hand on to each other. A command
state goes on by its exit status, an agent state by "next" or by the word it
writes to verdict.txt, or by "failed" when the run went wrong. Every loop must pass through a state with max_visits,
and max_steps caps a run. docs/workflows.md describes the format, the
placeholders and the settings (WORKFLOW_AGENT, WORKFLOW_TIMEOUT,
WORKFLOW_INLINE_BYTES).
"""

import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jsonc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ("pi", "opencode", "codex")
KINDS = ("agent", "command", "end")
KEYS = {
    "agent": {"agent", "next", "choices", "writes", "using", "failed", "max_visits", "exhausted", "timeout"},
    "command": {"command", "output", "pass", "fail", "max_visits", "exhausted", "timeout"},
    "end": {"end"},
}
TOP_KEYS = {"description", "needs", "start", "max_steps", "states"}
NAME = re.compile(r"^[a-z][a-z0-9-]*$")
WORD = re.compile(r"^[a-z][a-z0-9-]*$")
TAIL_LINES = 60
TIMEOUT_STATUS = 142  # perl's alarm, in run_agent


class WorkflowError(Exception):
    pass


class StepError(WorkflowError):
    """An agent step went wrong: it failed, timed out, or left out a file or its verdict."""


def positive(value, what):
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise WorkflowError(f"{what} must be a positive whole number, not {value!r}")
    return value


def load(graph_dir):
    """The checked graph of GRAPH_DIR/graph.jsonc, as a dict."""
    path = Path(graph_dir) / "graph.jsonc"
    if not path.is_file():
        raise WorkflowError(f"{path} does not exist")
    try:
        graph = jsonc.plain(jsonc.parse(path.read_text()))
    except jsonc.JsoncError as e:
        raise WorkflowError(f"{path}: {e}") from None
    check(graph, Path(graph_dir))
    return graph


def check(graph, graph_dir):
    """Raises WorkflowError naming the first thing wrong with the graph."""
    unknown = set(graph) - TOP_KEYS
    if unknown:
        raise WorkflowError(f"unknown key {sorted(unknown)[0]!r}; expected any of {sorted(TOP_KEYS)}")
    for key in ("start", "max_steps", "states"):
        if key not in graph:
            raise WorkflowError(f"the graph needs {key!r}")
    positive(graph["max_steps"], "max_steps")
    needs = graph.get("needs", [])
    if not isinstance(needs, list) or not all(isinstance(n, str) and n for n in needs):
        raise WorkflowError("needs must be a list of file names")
    states = graph["states"]
    if not isinstance(states, dict) or not states:
        raise WorkflowError("states must be an object with at least one state")
    if graph["start"] not in states:
        raise WorkflowError(f"the start state {graph['start']!r} is not in states")

    for name, state in states.items():
        where = f"state {name!r}"
        if not NAME.match(name):
            raise WorkflowError(f"{where}: a state name may hold only lowercase letters, digits and '-'")
        if not isinstance(state, dict):
            raise WorkflowError(f"{where} must be an object")
        kinds = [k for k in KINDS if k in state]
        if len(kinds) != 1:
            raise WorkflowError(f"{where} must have exactly one of {', '.join(KINDS)}")
        kind = kinds[0]
        unknown = set(state) - KEYS[kind]
        if unknown:
            raise WorkflowError(f"{where}: {sorted(unknown)[0]!r} does not belong in a{'n' if kind[0] in 'ae' else ''} {kind} state")
        for target in targets(state).values():
            if target not in states:
                raise WorkflowError(f"{where} leads to {target!r}, which is not a state")
        if "max_visits" in state:
            positive(state["max_visits"], f"{where}: max_visits")
        elif "exhausted" in state:
            raise WorkflowError(f"{where}: exhausted needs max_visits")
        if "timeout" in state:
            positive(state["timeout"], f"{where}: timeout")
        if kind == "end":
            if state["end"] not in ("success", "failure"):
                raise WorkflowError(f"{where}: end must be success or failure")
        elif kind == "command":
            if not isinstance(state["command"], str) or not state["command"].strip():
                raise WorkflowError(f"{where}: command must be a shell command")
            for key in ("pass", "fail"):
                if key not in state:
                    raise WorkflowError(f"{where}: a command state needs {key!r}")
            if "output" in state:
                safe_path(state["output"], f"{where}: output")
        else:
            prompt = graph_dir / state["agent"]
            if not prompt.is_file():
                raise WorkflowError(f"{where}: the prompt {prompt} does not exist")
            if ("next" in state) == ("choices" in state):
                raise WorkflowError(f"{where}: an agent state needs either next or choices")
            if "choices" in state:
                choices = state["choices"]
                if not isinstance(choices, dict) or len(choices) < 2:
                    raise WorkflowError(f"{where}: choices must map at least two words to states")
                for word in choices:
                    if not WORD.match(word):
                        raise WorkflowError(f"{where}: the choice {word!r} must be one lowercase word")
            if "using" in state and state["using"] not in AGENTS:
                raise WorkflowError(f"{where}: using must be one of {', '.join(AGENTS)}")
            writes = state.get("writes", [])
            if not isinstance(writes, list):
                raise WorkflowError(f"{where}: writes must be a list of file names")
            for path in writes:
                safe_path(path, f"{where}: writes")

    reachable = walk(states, graph["start"])
    unreached = [n for n in states if n not in reachable]
    if unreached:
        raise WorkflowError(f"state {unreached[0]!r} cannot be reached from {graph['start']!r}")
    if not any(states[n].get("end") == "success" for n in reachable):
        raise WorkflowError("no success end can be reached")
    cycle = unbounded_cycle(states)
    if cycle:
        raise WorkflowError(
            f"the loop {' -> '.join(cycle)} has no state with max_visits, so nothing bounds it but max_steps"
        )


def safe_path(path, what):
    if not isinstance(path, str) or not path or path.startswith("/") or ".." in Path(path).parts:
        raise WorkflowError(f"{what}: {path!r} must be a path inside the folder")


def targets(state):
    """{label: next state} for every way out of a state."""
    out = {}
    if "next" in state:
        out["next"] = state["next"]
    for key in ("pass", "fail", "failed", "exhausted"):
        if key in state:
            out[key] = state[key]
    for word, target in state.get("choices", {}).items():
        out[word] = target
    return out


def walk(states, start):
    seen, todo = set(), [start]
    while todo:
        name = todo.pop()
        if name not in seen:
            seen.add(name)
            todo.extend(targets(states[name]).values())
    return seen


def unbounded_cycle(states):
    """A cycle through states without max_visits, as a list of names, or None."""
    free = {n for n, s in states.items() if "max_visits" not in s and "end" not in s}
    colour = {}

    def visit(name, path):
        colour[name] = "open"
        path.append(name)
        for target in targets(states[name]).values():
            if target not in free:
                continue
            if colour.get(target) == "open":
                return path[path.index(target):] + [target]
            if target not in colour:
                found = visit(target, path)
                if found:
                    return found
        path.pop()
        colour[name] = "done"
        return None

    for name in sorted(free):
        if name not in colour:
            found = visit(name, [])
            if found:
                return found
    return None


def describe(graph):
    """The graph as text, one state per line."""
    lines = [graph.get("description", ""), f"start: {graph['start']}; at most {graph['max_steps']} steps"]
    for name, state in graph["states"].items():
        if "end" in state:
            lines.append(f"  {name}: end ({state['end']})")
            continue
        what = f"agent {state['agent']}" if "agent" in state else f"command `{state['command']}`"
        bound = f", at most {state['max_visits']} times" if "max_visits" in state else ""
        ways = ", ".join(f"{label} -> {target}" for label, target in targets(state).items())
        lines.append(f"  {name}: {what}{bound}; {ways}")
    return "\n".join(line for line in lines if line)


class Run:
    def __init__(self, graph_dir, folder):
        self.graph_dir = Path(graph_dir).resolve()
        self.graph = load(self.graph_dir)
        self.folder = Path(folder).resolve()
        self.agent = os.environ.get("WORKFLOW_AGENT") or "pi"
        if self.agent not in AGENTS:
            raise WorkflowError(f"WORKFLOW_AGENT must be one of {', '.join(AGENTS)}, not {self.agent!r}")
        self.timeout = self.setting("WORKFLOW_TIMEOUT", 600)
        self.inline_bytes = self.setting("WORKFLOW_INLINE_BYTES", 16384)
        for need in self.graph.get("needs", []):
            if not (self.folder / need).exists():
                raise WorkflowError(f"{self.folder / need} is missing; this workflow needs it to start")
        self.run_id, self.logs = None, None
        self.visits = {}
        self.trace = []

    @staticmethod
    def setting(name, default):
        value = os.environ.get(name) or str(default)
        if not value.isdigit() or int(value) < 1:
            raise WorkflowError(f"{name} must be a positive whole number, not {value!r}")
        return int(value)

    def inline(self, kind, path):
        safe_path(path, f"{{{{{kind}:...}}}}")
        file = self.folder / path
        if not file.is_file():
            return f"({path} does not exist yet)"
        text = file.read_text(errors="replace")
        if kind == "tail":
            lines = text.splitlines()
            if len(lines) > TAIL_LINES:
                text = f"(the first {len(lines) - TAIL_LINES} lines are left out)\n" + "\n".join(lines[-TAIL_LINES:])
        size = len(text.encode())
        if size > self.inline_bytes:
            raise WorkflowError(
                f"{path} is {size} bytes, over WORKFLOW_INLINE_BYTES ({self.inline_bytes}); "
                f"inline less of it, or raise the limit"
            )
        return text

    def prompt(self, name, state):
        text = (self.graph_dir / state["agent"]).read_text()
        values = {
            "run": self.run_id,
            "state": name,
            "visit": str(self.visits[name]),
            "max_visits": str(state.get("max_visits", "")),
            "trace": "\n".join(self.trace) or "(this is the first state)",
        }

        def replace(match):
            key = match.group(1)
            if ":" in key:
                kind, path = key.split(":", 1)
                if kind in ("file", "tail"):
                    return self.inline(kind, path.strip())
            if key in values:
                return values[key]
            raise WorkflowError(f"{state['agent']}: unknown placeholder {{{{{key}}}}}")

        text = re.sub(r"\{\{([^{}]+)\}\}", replace, text).rstrip() + "\n"
        if "choices" in state:
            words = " or ".join(state["choices"])
            text += (
                f"\nWhen you have finished, write exactly one word, {words}, to the file verdict.txt "
                f"in the current folder, and nothing else.\n"
            )
        return text

    def run_agent(self, name, state, log):
        agent = state.get("using", self.agent)
        verdict = self.folder / "verdict.txt"
        verdict.unlink(missing_ok=True)
        before = {p: mtime(self.folder / p) for p in state.get("writes", [])}
        prompt = self.prompt(name, state)
        data = Path(tempfile.mkdtemp(prefix="puny-workflow."))
        try:
            with open(log, "w") as out:
                status = subprocess.run(
                    ["bash", "-c", 'source "$1/lib/common.sh"; source "$1/lib/agents.sh"; shift; run_agent "$@"',
                     "_", str(ROOT), agent, str(data), str(state.get("timeout", self.timeout)), prompt],
                    cwd=self.folder, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                ).returncode
        finally:
            shutil.rmtree(data, ignore_errors=True)
        if status == TIMEOUT_STATUS:
            raise StepError(f"{name}: {agent} gave no answer within {state.get('timeout', self.timeout)}s; see {log}")
        if status != 0:
            raise StepError(f"{name}: {agent} exited with status {status}; see {log}")
        for path, then in before.items():
            now = mtime(self.folder / path)
            if now is None or now == then:
                raise StepError(f"{name}: {agent} did not write {path}; see {log}")
        if "next" in state:
            return "next", state["next"]
        if not verdict.is_file():
            raise StepError(f"{name}: {agent} wrote no verdict.txt; expected one of {', '.join(state['choices'])}; see {log}")
        word = verdict.read_text().strip().lower()
        verdict.unlink()
        if word not in state["choices"]:
            raise StepError(f"{name}: the verdict {word!r} is not one of {', '.join(state['choices'])}; see {log}")
        return word, state["choices"][word]

    def run_command(self, name, state, log):
        command = state["command"].replace("{{graph}}", str(self.graph_dir))
        timeout = state.get("timeout", self.timeout)
        # Its own process group, so a timeout ends whatever the command started.
        process = subprocess.Popen(["bash", "-c", command], cwd=self.folder, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            out, _ = process.communicate(timeout=timeout)
            status = process.returncode
            text = out.decode(errors="replace")
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            out, _ = process.communicate()
            text = out.decode(errors="replace") + f"\nThe command did not finish within {timeout}s.\n"
            status = TIMEOUT_STATUS
        log.write_text(text)
        if "output" in state:
            output = self.folder / state["output"]
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text)
        last = text.strip().splitlines()[-1] if text.strip() else "(no output)"
        return ("pass", state["pass"]) if status == 0 else ("fail", state["fail"]), last

    def run(self):
        states = self.graph["states"]
        # A second run in the same second gets -2, -3 and so on.
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        (self.folder / "workflow-logs").mkdir(exist_ok=True)
        for n in range(1, 100):
            self.run_id = stamp if n == 1 else f"{stamp}-{n}"
            self.logs = self.folder / "workflow-logs" / self.run_id
            try:
                self.logs.mkdir()
                break
            except FileExistsError:
                continue
        else:
            raise WorkflowError(f"cannot make a new log folder under {self.folder / 'workflow-logs'}")
        print(f"workflow {self.graph_dir.name} {self.run_id} in {self.folder}, agent {self.agent}", flush=True)
        name, steps = self.graph["start"], 0
        with open(self.logs / "trace.tsv", "w") as trace:
            trace.write("step\tstate\toutcome\tnext\tseconds\n")
            while True:
                state = states[name]
                if "end" in state:
                    print(f"== {name}: {state['end']}", flush=True)
                    return state["end"] == "success"
                limit = state.get("max_visits")
                if limit and self.visits.get(name, 0) >= limit:
                    if "exhausted" not in state:
                        raise WorkflowError(f"{name} has run {limit} times, its max_visits, and has no exhausted state")
                    print(f"== {name}: ran {limit} times; going to {state['exhausted']}", flush=True)
                    self.trace.append(f"{name}: ran its {limit} times -> {state['exhausted']}")
                    name = state["exhausted"]
                    continue
                steps += 1
                if steps > self.graph["max_steps"]:
                    raise WorkflowError(f"the run reached max_steps ({self.graph['max_steps']}) at {name}")
                self.visits[name] = self.visits.get(name, 0) + 1
                visit = self.visits[name]
                log = self.logs / f"{steps:02d}-{name}.log"
                print(f"== {name}" + (f" ({visit} of {limit})" if limit else "") + ": ", end="", flush=True)
                started = time.monotonic()
                if "agent" in state:
                    try:
                        (outcome, target), detail = self.run_agent(name, state, log), ""
                    except StepError as e:
                        if "failed" not in state:
                            raise
                        # The graph says where to go when this step goes wrong;
                        # the reason is printed and kept in the trace.
                        outcome, target, detail = "failed", state["failed"], str(e).removeprefix(f"{name}: ")
                else:
                    (outcome, target), detail = self.run_command(name, state, log)
                seconds = round(time.monotonic() - started)
                print(f"{outcome} in {seconds}s" + (f" ({detail})" if detail else "") + f" -> {target}", flush=True)
                trace.write(f"{steps}\t{name}\t{outcome}\t{target}\t{seconds}\n")
                trace.flush()
                self.trace.append(f"{name}" + (f" ({visit})" if visit > 1 else "") + f": {outcome} -> {target}")
                name = target


def mtime(path):
    try:
        return path.stat().st_mtime_ns
    except FileNotFoundError:
        return None


def main(argv):
    usage = f"usage: {argv[0]} check GRAPH_DIR | run GRAPH_DIR FOLDER"
    try:
        if len(argv) == 3 and argv[1] == "check":
            print(describe(load(argv[2])))
            return 0
        if len(argv) == 4 and argv[1] == "run":
            run = Run(argv[2], argv[3])
            if run.run():
                print(f"Done: the workflow reached its success state. Logs: {run.logs}")
                return 0
            print(f"error: the workflow ended in failure; see {run.logs}", file=sys.stderr)
            return 1
    except WorkflowError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(usage, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
