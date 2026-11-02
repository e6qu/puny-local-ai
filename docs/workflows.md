# Workflows

A workflow runs agents as a state machine. Each state does one narrow job, either as a fresh agent run or as a command. States hand work on only through files in one folder, and a check decides when the work is done. A small local model with a short context can then make progress on a task that would overwhelm it in one long session:

- **Fresh context per step.** No agent run inherits another's conversation, so none fills its window. What a step needs, such as the goal and the end of the last test output, is put into its prompt, so a weak model does not have to search for it.
- **The check decides, not the model.** A command's exit status picks the next state. An agent never declares the work finished.
- **Every loop is bounded.** A cycle must pass through a state with `max_visits`, and `max_steps` caps the whole run. `./bin/workflow check` refuses a graph with an unbounded loop.

```sh
./bin/workflow list                  # the bundled workflows
./bin/workflow check goal            # a graph's states and transitions
./bin/workflow goal path/to/folder   # run one on a folder
```

A run prints one line per step and writes `workflow-logs/RUN/` into the folder: `trace.tsv`, with each step's state, outcome, next state and seconds, and one log per step. It exits 0 when it reaches a `success` end. An agent step that fails, times out, or leaves out a file it must write goes to its `failed` state when it has one, and otherwise stops the run with a message naming the step's log. The web UI's Workflows page runs both bundled workflows on folders in `state/`.

Settings: `WORKFLOW_AGENT` picks the agent, `pi` by default, unless a state names its own. `WORKFLOW_TIMEOUT` limits each step, 600 seconds by default, unless a state sets its own. `WORKFLOW_INLINE_BYTES` is the most a prompt may inline from one file, 16,384 bytes by default.

## The bundled workflows

### goal

The smallest loop: check, and while the check fails, one worker takes one step and notes it.

```
check ──pass──▶ done
  │ fail
  ▼
work (at most 8) ──▶ check
  └─ after 8 ──▶ gave-up
```

The folder needs `goal.md`, what to achieve, and `check.sh`, which exits 0 once it is achieved, for example by running the project's tests. Each worker's prompt holds:

- the goal;
- the last 60 lines of `progress.md`, where each worker appends one line;
- the last 60 lines of the check's output.

The worker makes one focused change, runs the check, and appends its line. [`workflows/goal/example/`](../workflows/goal/example/) is a half-written Python module with failing tests:

```sh
cp -R workflows/goal/example /tmp/textstats
./bin/workflow goal /tmp/textstats
```

### team-solve

A planner, two ideators and a solver, then a loop of check, review and repair. It follows [GVS5H](https://github.com/slee-persis/GVS5H)'s method as written up in [Memdoor's team-solve](https://github.com/guregodevo/memdoor/tree/cee3ca70ff62acf3018cedea8b7b3b742c597682/examples/workflows/team-solve):

```
plan ▶ ideate-a ▶ ideate-b ▶ solve ▶ check ──pass──▶ solved
                                       │ fail
                                       ▼
                     review (at most 3) ▶ repair ▶ check
                       └─ after 3 ──▶ unsolved
```

`./bin/team-solve FOLDER` runs it. The folder needs `task.md`, what to build, and a goal:

- **samples**, pairs of `tests/N.in` and `tests/N.out`. The team writes `run.sh`, the command that runs its program on a sample, and each sample's output is compared with `diff`;
- **`check.sh`**, any command that exits 0 when the task is done;
- or both, in which case both must pass.

The result can be any files in any language. The steps:

- **plan** writes `plan.md` and starts the shared ledger, `notes.md`;
- **ideate-a** and **ideate-b** each add a proposal, the most direct one and one from another family of technique;
- **solve** picks one and builds it;
- **check** runs the samples and `check.sh`;
- **review** reads the check's output and writes repair instructions to `review.md`;
- **repair** follows them; the check then runs again.

Only the planner is essential: an ideator, the solver, a reviewer or a repair that goes wrong is noted and the run goes on, since the check decides in the end. The ideators run one after the other, because the server takes one request at a time. [`workflows/team-solve/example/`](../workflows/team-solve/example/) is a small algorithm problem with three samples. The prompts are adapted from Memdoor's under Apache-2.0; [CHANGES.md](../workflows/team-solve/CHANGES.md) lists the changes.

## Writing a workflow

A workflow is a folder with `graph.jsonc` and the prompt files it names. Put it under `workflows/NAME/` to run it by name, or anywhere to run it by path: `./bin/workflow path/to/graph-folder FOLDER`.

```jsonc
{
  "description": "One line, shown by ./bin/workflow list",
  "needs": ["goal.md", "check.sh"],     // files the folder must hold to start
  "start": "check",
  "max_steps": 20,                       // the most steps one run takes
  "states": {
    "check":  { "command": "bash check.sh", "output": "check-output.log",
                "pass": "done", "fail": "triage" },
    "triage": { "agent": "triage.md", "choices": { "fix": "work", "rethink": "plan" }, "failed": "work" },
    "plan":   { "agent": "plan.md", "writes": ["plan.md"], "next": "work", "max_visits": 2,
                "exhausted": "gave-up" },
    "work":   { "agent": "work.md", "using": "codex", "next": "check", "max_visits": 6,
                "exhausted": "gave-up" },
    "done":   { "end": "success" },
    "gave-up": { "end": "failure" }
  }
}
```

A state is one of three kinds:

- **An agent state** names its prompt file in `agent` and leads on with either `next` or `choices`. With `choices`, the prompt gets a closing line asking the agent to write one of the words to `verdict.txt`. That word picks the next state, and any other word stops the run. `writes` lists files the step must create or change. `using` runs that state with another agent than `WORKFLOW_AGENT`. `failed` is where to go when the step goes wrong: the agent exits with an error or times out, leaves out a file in `writes`, or gives no valid verdict. The reason is printed and kept in the trace, so later prompts see it through `{{trace}}`. Mistakes in the graph itself, such as an unknown placeholder, always stop the run.
- **A command state** runs `command` with bash in the folder and goes to `pass` on exit status 0 and to `fail` otherwise. Its output goes to the step's log and, with `output`, to that file in the folder for later prompts to inline. `{{graph}}` in the command is the workflow's own folder, for scripts that ship with it.
- **An end state** is `success` or `failure`.

Any state but an end may set `timeout` in seconds, and `max_visits` with an `exhausted` state to go to once they are used up. Without `exhausted`, one visit too many stops the run as a failure.

Prompts may use these placeholders:

| Placeholder | Becomes |
|---|---|
| `{{file:PATH}}` | the folder's file, whole; `(PATH does not exist yet)` if it is missing |
| `{{tail:PATH}}` | its last 60 lines, saying how many were left out |
| `{{trace}}` | the steps so far, one line each, such as `check (2): fail -> review` |
| `{{visit}}`, `{{max_visits}}` | this state's visit number and its bound |
| `{{state}}`, `{{run}}` | the state's name and the run's identifier |

An inlined file over `WORKFLOW_INLINE_BYTES` stops the run rather than being cut silently, and so does an unknown placeholder.

`./bin/workflow check` names the first problem it finds:

- a state that leads nowhere or cannot be reached;
- no `success` end;
- a key that does not belong;
- a missing prompt;
- a loop without a bound.

[`lib/workflow.py`](../lib/workflow.py) is the engine, and `tests/test_workflow.py` shows each rule at work.
