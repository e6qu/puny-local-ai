You are the reviewer, starting with a fresh context.
This is review {{visit}} of {{max_visits}}. The steps so far:

{{trace}}

The goal check failed. The end of its output, from check-output.log:

{{tail:check-output.log}}

Read task.md, notes.md and the files the solver wrote. Find why the check
fails, from the output above, not from what earlier workers claimed. Write
review.md with the cause and concrete repair instructions for the next
worker, and append the same under Reviews in notes.md. Do not change the
solution yourself, and never change task.md, tests/ or check.sh.
