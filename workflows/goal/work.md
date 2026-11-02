You are step {{visit}} of at most {{max_visits}} toward a goal, starting with
a fresh context. Other workers came before you; you know of them only from
the notes below.

The goal, from goal.md:

{{file:goal.md}}

The latest progress notes, from progress.md:

{{tail:progress.md}}

check.sh decides when the goal is met, and it fails now. The end of its
output:

{{tail:check-output.log}}

Make one focused change that brings the check closer to passing: the first
failure above is usually the place to start. Run bash check.sh to see the
effect. Never change goal.md or check.sh.

Then append one line to progress.md: what you changed and what the check
says now. Keep it short; the next worker reads only the last lines.
