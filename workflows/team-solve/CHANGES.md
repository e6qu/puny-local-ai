# Changes from Memdoor's team-solve

Source: [`examples/workflows/team-solve`](https://github.com/guregodevo/memdoor/tree/cee3ca70ff62acf3018cedea8b7b3b742c597682/examples/workflows/team-solve) at commit `cee3ca70`, Apache-2.0 (see [NOTICE](NOTICE) and [LICENSE](LICENSE)). The method itself is GVS5H's ([slee-persis/GVS5H](https://github.com/slee-persis/GVS5H)).

Every file here was changed:

- **The steps are a graph.** Memdoor's `steps/*.yaml` (agent, `requires:`, `timeout:`, `max_retries:`) became [`graph.jsonc`](graph.jsonc), run by `./bin/workflow`, with each `prompt:` field moved into its own Markdown file.
- **Review and repair loop.** Memdoor ran one review, one repair and a final test. Here, check, review and repair repeat until the check passes, for at most three reviews, and the review runs only when the check failed.
- **Any task and any goal.** The prompts no longer ask for one `solution.py` read from standard input. The task says what to build, in any files and language. The goal is the samples, run through a `run.sh` the solver writes, or the folder's `check.sh`, or both. `check-samples.sh` became [`run-checks.sh`](run-checks.sh), which runs whichever the folder has.
- **What a step needs is in its prompt.** The prompts carry `task.md`, the end of the check's output and the review, so a small model need not search for them.
- **The ideators run one after the other**, not in parallel, because the local server serves one request at a time. The lines about concurrent writers and retrying an `apply_patch` insertion are gone, and so are the `<!-- proposal-a -->` markers, which a small planner got wrong: each ideator writes under its own heading of `notes.md`.
- **Only the planner is essential.** An ideator, the solver, a reviewer or a repair that fails, times out or leaves out its file is noted and the run goes on to the next step; the goal check decides in the end.
- **`{{.partition}}`**, Memdoor's run identifier, became `{{run}}`.
- **No retries.** Memdoor retried an agent step once (`max_retries: 1`). Here a step is never repeated as such; a failed planner stops the run with a message naming its log.
