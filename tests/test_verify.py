"""bin/verify and lib/verify.py: one preset checked end to end, and its report.

bin/serve-qwen, bin/smoke-test, bin/evals and bin/bench-agents are replaced in
the sandbox by stand-ins, so these tests check how bin/verify starts, runs,
stops and reports, not the steps themselves, which have their own tests.
"""

import socket
import subprocess
import sys
import unittest

from helpers import AGENT_STUB, REPO, Sandbox, free_port

sys.path.insert(0, str(REPO / "lib"))
import verify  # noqa: E402

SMALL = ("mlx-community/Qwen3.5-4B-4bit", "0e7ffd5c629ef7719d4cbc04069232580bfa9d9c")

# Stands in for bin/serve-qwen: prints a memory check, records its pid as
# lib/serve.py does, then becomes a server on the port that logs one admitted
# and measured request. STUB_SERVE=fail exits instead, like a failed check.
FAKE_SERVE = r'''#!/bin/bash
root="$(cd "$(dirname "$0")/.." && pwd)"
printf '%s\n' "QWEN_PRESET=$QWEN_PRESET" >> "$STUB_CALLS/serve-qwen"
echo "Memory check:"
echo "    2.50 GiB  model weights_bytes"
echo "   16.00 GiB  GPU limit; 262144 tokens fit within it"
echo "   60.00 GiB  free disk"
if [[ "${STUB_SERVE:-}" == fail ]]; then
  echo "error: only 1.00 GiB is free now, enough for 0 tokens of context."
  exit 1
fi
port="${MLX_BASE_URL##*:}"
port="${port%%/*}"
mkdir -p "$root/state/served"
echo "$$ 262144" > "$root/state/served/port-$port"
exec python3 -c '
import http.server, sys
print("2026-11-02 10:00:00,000 - INFO - Admitted 9192 tokens with prefill step 512; 9.00 GiB free", flush=True)
print("2026-11-02 10:00:05,000 - INFO - Peak 0.90 GiB above the loaded model for 9192 tokens at prefill step 512; estimated 1.80 GiB", flush=True)
http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), http.server.BaseHTTPRequestHandler).serve_forever()
' "$port"
'''

# Stands in for one step: logs its arguments, and exits with STUB_<NAME>
# (default 0). STUB_KILL_SERVER=1 makes the smoke test end the server first.
FAKE_STEP = r'''#!/bin/bash
name="$(basename "$0")"
root="$(cd "$(dirname "$0")/.." && pwd)"
printf '%s\n' "$* BENCH_AGENTS=${BENCH_AGENTS:-}" >> "$STUB_CALLS/$name"
if [[ "$name" == bench-agents && "$1" == report ]]; then
  echo "bench report for $2"
  exit 0
fi
if [[ "$name" == smoke-test && -n "${STUB_KILL_SERVER:-}" ]]; then
  port="${MLX_BASE_URL##*:}"
  kill "$(cut -d' ' -f1 < "$root/state/served/port-${port%%/*}")"
  sleep 1
fi
if [[ "$name" == bench-agents ]]; then
  mkdir -p "$root/state/bench/$1"
  printf 'label\ttask\tagent\trun\tresult\tseconds\n' > "$root/state/bench/$1/results.tsv"
fi
echo "$name ran"
case "$name" in
  smoke-test) exit "${STUB_SMOKE:-0}" ;;
  evals) exit "${STUB_EVALS:-0}" ;;
  *) exit "${STUB_BENCH:-0}" ;;
esac
'''


class VerifyCommandTest(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)
        self.box.fake_mlx_env()
        self.box.fake_model(*SMALL)
        self.box.link_tool("lsof")
        self.box.stub("pi", AGENT_STUB)
        for name, script in (("serve-qwen", FAKE_SERVE), ("smoke-test", FAKE_STEP),
                             ("evals", FAKE_STEP), ("bench-agents", FAKE_STEP)):
            path = self.box.root / "bin" / name
            path.write_text(script)
            path.chmod(0o755)
        self.port = free_port()
        self.env = {"MLX_BASE_URL": f"http://127.0.0.1:{self.port}/v1", "VERIFY_AGENTS": "pi"}

    def verify(self, *args, **env):
        return self.box.run(["bin/verify", *args], {**self.env, **env}, timeout=120)

    def report(self):
        reports = list((self.box.root / "state" / "verify").glob("small-*/report.md"))
        self.assertEqual(len(reports), 1, reports)
        return reports[0].read_text()

    def server_pids(self):
        out = subprocess.run(["/usr/sbin/lsof", "-nP", "-t", f"-iTCP:{self.port}", "-sTCP:LISTEN"],
                             capture_output=True, text=True).stdout
        return out.split()

    def test_runs_every_step_and_reports(self):
        result = self.verify("small")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.box.called("serve-qwen"), ["QWEN_PRESET=small"])
        label = self.box.called("evals")[0].split()[0]
        self.assertRegex(label, r"^small-\d{8}-\d{6}$")
        self.assertEqual(self.box.called("smoke-test"), ["pi BENCH_AGENTS="])
        self.assertEqual(self.box.called("bench-agents"), [f"{label} BENCH_AGENTS=pi", f"report {label} BENCH_AGENTS="])
        report = self.report()
        self.assertIn("# small on ", report)
        self.assertIn("- GPU limit (max_recommended_working_set_size): 16.00 GiB", report)
        self.assertIn(f"- Model: `{SMALL[0]}` at `{SMALL[1]}`", report)
        for step in ("smoke", "evals", "bench"):
            self.assertRegex(report, rf"\| {step} \| finished \| \d+ \|")
        self.assertIn("   60.00 GiB  free disk", report)
        self.assertIn("- 1 request measured; 0 went over the estimate", report)
        self.assertIn(f"bench report for {label}", report)
        self.assertEqual(self.server_pids(), [], "the server must be stopped at the end")

    def test_a_failed_step_does_not_stop_the_others(self):
        result = self.verify("small", STUB_SMOKE="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not every step finished", result.stderr)
        report = self.report()
        self.assertRegex(report, r"\| smoke \| failed with status 1 \|")
        self.assertRegex(report, r"\| bench \| finished \|")
        self.assertEqual(self.server_pids(), [])

    def test_steps_can_be_chosen(self):
        result = self.verify("small", VERIFY_STEPS="bench")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.box.called("smoke-test"), [])
        self.assertEqual(self.box.called("evals"), [])
        self.assertNotIn("| smoke |", self.report())

    def test_a_server_that_stops_ends_the_run_with_a_report(self):
        result = self.verify("small", STUB_KILL_SERVER="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("the server stopped during smoke", result.stderr)
        self.assertEqual(self.box.called("evals"), [])
        report = self.report()
        self.assertIn("**The server stopped during smoke**", report)
        self.assertNotIn("| evals |", report)

    def test_a_server_that_does_not_start(self):
        result = self.verify("small", STUB_SERVE="fail")
        self.assertEqual(result.returncode, 1)
        self.assertIn("enough for 0 tokens of context", result.stderr)
        self.assertIn("the server did not start", result.stderr)
        self.assertEqual(self.box.called("smoke-test"), [])

    def test_refuses_a_busy_port(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", self.port))
            s.listen()
            result = self.verify("small")
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"port {self.port} is in use", result.stderr)
        self.assertEqual(self.box.called("serve-qwen"), [])

    def test_refuses_bad_input(self):
        cases = [
            ((["nope"], {}), "unknown preset 'nope'"),
            ((["small", "kat"], {}), "usage:"),
            ((["small"], {"VERIFY_STEPS": "smoke lint"}), "unknown step 'lint'"),
            ((["small"], {"VERIFY_AGENTS": "claude"}), "unknown agent 'claude'"),
            ((["small"], {"VERIFY_AGENTS": "opencode"}), "opencode is not installed"),
            ((["small"], {"VERIFY_START_TIMEOUT": "0"}), "VERIFY_START_TIMEOUT must be"),
            ((["small"], {"QWEN_REPO": "a/b", "QWEN_REVISION": "0" * 40}), "unset them to verify the preset small"),
        ]
        for (args, env), message in cases:
            with self.subTest(message):
                result = self.verify(*args, **env)
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
        self.assertEqual(self.box.called("serve-qwen"), [])


LINE = "2026-11-02 10:00:00,000 - {level} - Peak {peak} GiB above the loaded model for {tokens} tokens at prefill step 512; estimated {estimate} GiB"


class PeaksTest(unittest.TestCase):
    def test_no_requests(self):
        self.assertEqual(verify.peaks("Memory check:\n"), "No request was measured: the log has no peak lines.")

    def test_within_the_estimates(self):
        text = "\n".join([
            "x - INFO - Admitted 9192 tokens with prefill step 512; 9.00 GiB free",
            LINE.format(level="INFO", peak="0.90", tokens=9192, estimate="1.80"),
            "x - INFO - Admitted 40000 tokens with prefill step 512; 9.00 GiB free",
            LINE.format(level="INFO", peak="1.70", tokens=40000, estimate="2.00"),
        ])
        summary = verify.peaks(text)
        self.assertIn("- 2 requests measured; 0 went over the estimate", summary)
        self.assertIn("- Highest peak: 1.70 GiB above the loaded model, for 40000 tokens", summary)
        self.assertIn("- Closest to its estimate: 1.70 of 2.00 GiB (85%)", summary)
        self.assertNotIn("admitted and", summary)
        self.assertNotIn("Not measured", summary)

    def test_names_the_request_left_unmeasured(self):
        text = "\n".join([
            "x - INFO - Admitted 820 tokens with prefill step 512; 4.48 GiB free",
            LINE.format(level="INFO", peak="0.95", tokens=820, estimate="1.11"),
            "x - INFO - Admitted 136576 tokens with prefill step 128; 5.14 GiB free",
            "x - INFO - Prompt processing progress: 128384/128384",
        ])
        self.assertEqual(verify.unmeasured(text), [("136576", "128")])
        summary = verify.peaks(text)
        self.assertIn("- 2 requests admitted and 1 measured.", summary)
        self.assertIn("- Not measured: a request of 136576 tokens at prefill step 128.", summary)

    def test_over_the_estimate(self):
        text = LINE.format(level="WARNING", peak="2.10", tokens=9192, estimate="1.80") + ". Over the estimate"
        summary = verify.peaks(text)
        self.assertIn("1 went over the estimate", summary)
        self.assertIn("- **Over the estimate:** 2.10 GiB against 1.80 GiB, for 9192 tokens", summary)

    def test_refusals_and_a_watchdog_stop(self):
        text = "\n".join([
            "x - INFO - Admitted 9192 tokens with prefill step 512; 9.00 GiB free",
            "x - WARNING - Refused a request: a 300000-token request needs 9.00 GiB but only 2.00 GiB is free.",
            "error: stopping the server to keep macOS responsive: only 0.40 GiB of memory is available.",
        ])
        summary = verify.peaks(text)
        self.assertIn("- 1 request admitted and 0 measured.", summary)
        self.assertIn("- Not measured: a request of 9192 tokens at prefill step 512.", summary)
        self.assertIn("- 1 request refused. The first: a 300000-token request needs 9.00 GiB", summary)
        self.assertIn("- **The watchdog stopped the server:** stopping the server to keep macOS responsive", summary)


if __name__ == "__main__":
    unittest.main()
