"""Sums up a server log for ./bin/verify's report.

    python3 lib/verify.py peaks SERVE_LOG

lib/serve.py logs, for each request it admits, the memory the request
reached at its peak beside the estimate it was admitted on, and logs refused
requests and watchdog stops as errors. This prints those as Markdown.
"""

import re
import sys

PEAK = re.compile(
    r" - (?P<level>INFO|WARNING) - Peak (?P<peak>[\d.]+) GiB above the loaded model for (?P<tokens>\d+) tokens "
    r"at prefill step (?P<step>\d+); estimated (?P<estimate>[\d.]+) GiB"
)
ADMITTED = re.compile(r" - INFO - Admitted \d+ tokens")
REFUSED = re.compile(r" - WARNING - Refused a request: (.*)")
STOPPED = re.compile(r"^error: stopping the server.*", re.MULTILINE)


def requests(n):
    return f"{n} request" if n == 1 else f"{n} requests"


def peaks(text):
    """The Markdown summary of one server log's memory lines."""
    found = [m.groupdict() for m in PEAK.finditer(text)]
    lines = []
    if not found:
        lines.append("No request was measured: the log has no peak lines.")
    else:
        over = [p for p in found if p["level"] == "WARNING"]
        highest = max(found, key=lambda p: float(p["peak"]))
        closest = max(found, key=lambda p: float(p["peak"]) / float(p["estimate"]))
        lines.append(
            f"- {requests(len(found))} measured; {len(over)} went over the estimate they were admitted on."
        )
        lines.append(
            f"- Highest peak: {highest['peak']} GiB above the loaded model, for {highest['tokens']} tokens "
            f"(prompt plus the most the reply may use) at prefill step {highest['step']}; "
            f"estimated {highest['estimate']} GiB."
        )
        ratio = float(closest["peak"]) / float(closest["estimate"])
        lines.append(
            f"- Closest to its estimate: {closest['peak']} of {closest['estimate']} GiB ({ratio:.0%}), "
            f"for {closest['tokens']} tokens at prefill step {closest['step']}."
        )
        for p in over:
            lines.append(
                f"- **Over the estimate:** {p['peak']} GiB against {p['estimate']} GiB, for {p['tokens']} tokens "
                f"at prefill step {p['step']}."
            )
    admitted = len(ADMITTED.findall(text))
    if admitted != len(found):
        lines.append(f"- {requests(admitted)} admitted and {len(found)} measured.")
    refused = REFUSED.findall(text)
    if refused:
        lines.append(f"- {requests(len(refused))} refused. The first: {refused[0]}")
    for stop in STOPPED.findall(text):
        lines.append(f"- **The watchdog stopped the server:** {stop.removeprefix('error: ')}")
    return "\n".join(lines)


def main(argv):
    if len(argv) != 3 or argv[1] != "peaks":
        sys.exit(f"usage: {argv[0]} peaks SERVE_LOG")
    with open(argv[2], encoding="utf-8", errors="replace") as f:
        print(peaks(f.read()))


if __name__ == "__main__":
    main(sys.argv)
