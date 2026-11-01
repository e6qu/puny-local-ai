# Changes from Memdoor's team-solve

Source: [`examples/workflows/team-solve`](https://github.com/guregodevo/memdoor/tree/cee3ca70ff62acf3018cedea8b7b3b742c597682/examples/workflows/team-solve) at commit `cee3ca70`, Apache-2.0 (see [NOTICE](NOTICE) and [LICENSE](LICENSE)). The method itself is GVS5H's ([slee-persis/GVS5H](https://github.com/slee-persis/GVS5H)).

Every file here was changed. What changed:

- **Prompts, one Markdown file per agent step** (`plan.md`, `ideate-a.md`, `ideate-b.md`, `solve.md`, `review.md`, `repair.md`). They were the `prompt:` field of `steps/*.yaml`; the YAML around them (agent name, `requires:`, `timeout:`, `max_retries:`) is replaced by `./bin/team-solve`, which runs the steps in that order.
- **The ideators run one after the other**, not in parallel, because the local server serves one request at a time. The lines telling each ideator that the other writes concurrently, and to re-read and retry a minimal `apply_patch` insertion, are removed; each ideator still edits only its own section of `notes.md`. "apply_patch insertion" became "edit notes.md", since the agents here have their own editing tools.
- **`{{.partition}}`**, Memdoor's run identifier, became `{{run}}`, which `./bin/team-solve` replaces with the run's date and time.
- **`check-samples.sh`**: the test and verify commands of `steps/test.yaml` and `steps/verify.yaml` moved into `./bin/team-solve`; the checker itself now also reports a missing `solution.py` before running anything, and runs `solution.py` with `python3` from the problem folder, as before.
- **No retries.** Memdoor retried an agent step once (`max_retries: 1`); here a failed step stops the run with a message naming it and its log, as everything in this repository does.
