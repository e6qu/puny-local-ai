"""The web UI (ui/, built by bin/web-ui), driven over HTTP against a sandboxed repo.

ui/'s own Go tests cover its parts with fake commands; these run the built
program with the repo's real bin/ commands: bin/config saving puny.conf,
bin/serve-qwen (a stand-in) started and stopped, bin/models listing the
presets, the chat passed to a fake model server, and bin/web-ui itself.
tests/run builds the program and names it in PUNY_WEB_UI_BIN.
"""

import hashlib
import json
import os
import shutil
import signal
import subprocess
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from helpers import REPO, FakeServer, Sandbox, free_port, mlx_health_routes, record_served

SMALL_REPO = "mlx-community/Qwen3.5-4B-4bit"
COMMIT = "a" * 40

# A stand-in for bin/serve-qwen: answers /health at MLX_BASE_URL and records
# itself as the real one does, with lib/serve.py in its command line.
FAKE_SERVE = r'''#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/../lib/common.sh"
mkdir -p "$PUNY_ROOT/state/served"
echo "$$ 4096" > "$PUNY_ROOT/state/served/port-$MLX_PORT"
exec /usr/bin/python3 -I -c '
import http.server, sys
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"{\"status\": \"ok\"}")
http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
' "$MLX_PORT" "$PUNY_ROOT/lib/serve.py" --model-dir "$PUNY_ROOT/state/models/org--m@aaaa"
'''


def built():
    """The program tests/run built, and the HTMX it serves."""
    program, htmx = os.environ.get("PUNY_WEB_UI_BIN"), os.environ.get("PUNY_WEB_UI_HTMX")
    if not (program and Path(program).is_file() and htmx and Path(htmx).is_file()):
        raise AssertionError("PUNY_WEB_UI_BIN and PUNY_WEB_UI_HTMX must name the built UI; run the tests with ./tests/run")
    return program, htmx


class WebUiCase(unittest.TestCase):
    """Starts the built UI on a free port, for a sandboxed repo."""

    def setUp(self):
        self.box = Sandbox(link_mlx_env=True)
        self.addCleanup(self.box.cleanup)
        self.addCleanup(self.stop_servers)
        self.configure()
        program, htmx = built()
        self.port = free_port()
        self.origin = f"http://127.0.0.1:{self.port}"
        self.ui = subprocess.Popen([program, "--listen", self.origin, "--repo", str(self.box.root), "--htmx", htmx,
                                    "--htmx-version", "test"], env=self.box.env(), stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        self.addCleanup(self.stop_ui)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                self.call("/api/status")
                return
            except OSError:
                time.sleep(0.1)
        self.fail("the UI did not start")

    def configure(self):
        """Prepares the sandbox before the UI starts."""

    def stop_ui(self):
        self.ui.terminate()
        self.ui.wait(timeout=10)
        self.ui.stderr.close()

    def stop_servers(self):
        for record in (self.box.root / "state" / "served").glob("port-*"):
            pid = int(record.read_text().split()[0])
            if pid != os.getpid():
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

    def call(self, path, form=None, headers=None, json_body=None):
        """(status, headers, body text) of one request, as the page's HTMX sends it."""
        data, kind = None, None
        if form is not None:
            data, kind = urllib.parse.urlencode(form, doseq=True).encode(), "application/x-www-form-urlencoded"
        if json_body is not None:
            data, kind = json.dumps(json_body).encode(), "application/json"
        request = urllib.request.Request(self.origin + path, data=data,
                                         headers={"HX-Request": "true", **({"Content-Type": kind} if kind else {}),
                                                  **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, response.headers, response.read().decode()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.headers, e.read().decode()

    def status(self):
        return json.loads(self.call("/api/status")[2])


class SettingsTest(WebUiCase):
    """Settings saved through the real bin/config."""

    def configure(self):
        (self.box.root / "puny.conf").write_text("# mine\nQWEN_TEMP=0.7\n")

    def test_form_saves_through_bin_config(self):
        status, _, page = self.call("/settings")
        self.assertEqual(status, 200)
        self.assertIn("QWEN_TEMP", page)
        self.assertIn('value="0.7"', page)
        form = {"set.QWEN_TEMP": "0.4", "loaded.QWEN_TEMP": "0.7", "set.QWEN_KV_BITS": "4", "loaded.QWEN_KV_BITS": ""}
        status, _, body = self.call("/settings", form)
        self.assertEqual(status, 200, body)
        self.assertIn("Saved to", body)
        self.assertEqual((self.box.root / "puny.conf").read_text(), "# mine\nQWEN_TEMP=0.4\nQWEN_KV_BITS=4\n")

        # A value the scripts refuse is never written.
        status, _, body = self.call("/settings", {"set.QWEN_KV_BITS": "3", "loaded.QWEN_KV_BITS": "4"})
        self.assertIn("QWEN_KV_BITS must be 4 or 8", body)
        self.assertIn("Nothing was saved.", body)
        self.assertIn("QWEN_KV_BITS=4\n", (self.box.root / "puny.conf").read_text())

        # Emptying a field removes the setting, so its default applies.
        self.call("/settings", {"set.QWEN_KV_BITS": "", "loaded.QWEN_KV_BITS": "4"})
        self.assertEqual((self.box.root / "puny.conf").read_text(), "# mine\nQWEN_TEMP=0.4\n")

    def test_file_editor_replaces_the_file_through_bin_config(self):
        status, _, page = self.call("/settings/file")
        self.assertIn("# mine\nQWEN_TEMP=0.7\n", page)
        loaded = hashlib.sha256(b"# mine\nQWEN_TEMP=0.7\n").hexdigest()
        self.assertIn(loaded, page)
        status, _, body = self.call("/settings/file", {"text": "# edited\r\nQWEN_PRESET=small\r\n", "hash": loaded})
        self.assertEqual(status, 200, body)
        self.assertEqual((self.box.root / "puny.conf").read_text(), "# edited\nQWEN_PRESET=small\n")

        edited = hashlib.sha256(b"# edited\nQWEN_PRESET=small\n").hexdigest()
        status, _, body = self.call("/settings/file", {"text": "QWEN_PRESET=small\nQWEN_REPO=org/m\n", "hash": edited})
        self.assertEqual(status, 400)
        self.assertIn("set QWEN_REPO and QWEN_REVISION together", body)
        status, _, body = self.call("/settings/file", {"text": "NOPE=1\n", "hash": edited})
        self.assertIn("the new puny.conf line 1: NOPE is not a puny-local-ai setting", body)
        self.assertEqual((self.box.root / "puny.conf").read_text(), "# edited\nQWEN_PRESET=small\n")

        # Changed in the terminal meanwhile: refused.
        (self.box.root / "puny.conf").write_text("QWEN_TEMP=0.2\n")
        status, _, body = self.call("/settings/file", {"text": "QWEN_TEMP=0.9\n", "hash": edited})
        self.assertEqual(status, 409)
        self.assertEqual((self.box.root / "puny.conf").read_text(), "QWEN_TEMP=0.2\n")

        # An empty file removes puny.conf.
        status, _, body = self.call("/settings/file", {"text": "", "hash": hashlib.sha256(b"QWEN_TEMP=0.2\n").hexdigest()})
        self.assertEqual(status, 200, body)
        self.assertFalse((self.box.root / "puny.conf").exists())

    def test_presets_and_use(self):
        status, _, page = self.call("/models")
        self.assertEqual(status, 200)
        self.assertIn("Qwen3.5-4B, for quick tests", page)
        self.assertIn("not downloaded", page)
        status, _, body = self.call("/models/preset", {"name": "small"})
        self.assertEqual(status, 200, body)
        self.assertIn("QWEN_PRESET=small", (self.box.root / "puny.conf").read_text())
        status, _, body = self.call("/models/preset", {"name": "nope"})
        self.assertIn("nothing was saved", body)

        status, _, body = self.call("/models/use", {"name": SMALL_REPO, "commit": COMMIT, "context": "32768"})
        conf = (self.box.root / "puny.conf").read_text()
        self.assertIn(f"QWEN_REPO={SMALL_REPO}\n", conf)
        self.assertIn("QWEN_CONTEXT=32768\n", conf)
        self.assertNotIn("QWEN_PRESET", conf)


class ServerTest(WebUiCase):
    """Start, restart and stop, with a stand-in for bin/serve-qwen."""

    def configure(self):
        (self.box.root / "bin" / "serve-qwen").write_text(FAKE_SERVE)
        self.mlx_port = free_port()
        (self.box.root / "puny.conf").write_text(f"MLX_BASE_URL=http://127.0.0.1:{self.mlx_port}/v1\n")

    def wait_for(self, state):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status = self.status()
            if status["state"] == state:
                return status
            time.sleep(0.2)
        self.fail(f"the server never became {state}: {status}")

    def test_start_restart_and_stop(self):
        self.assertEqual(self.status()["state"], "stopped")
        status, _, body = self.call("/server/start", {})
        self.assertEqual(status, 200, body)
        running = self.wait_for("running")
        self.assertEqual(running["window_tokens"], 4096)
        self.assertEqual(running["model"], "org/m")
        self.assertIn("org/m", self.call("/fragments/statusbar")[2])
        status, _, body = self.call("/server/start", {})
        self.assertEqual(status, 400)
        self.assertIn("already runs", body)

        # A new port in the settings: the old server is found and stopped.
        new_port = free_port()
        self.call("/settings", {"set.MLX_BASE_URL": f"http://127.0.0.1:{new_port}/v1",
                                "loaded.MLX_BASE_URL": f"http://127.0.0.1:{self.mlx_port}/v1"})
        self.assertIn(f"runs on port {self.mlx_port}", self.status()["elsewhere"])
        self.assertEqual(self.call("/server/restart", {})[0], 200)
        self.wait_for("running")
        records = [p.name for p in (self.box.root / "state" / "served").glob("port-*")]
        self.assertIn(f"port-{new_port}", records)

        status, _, body = self.call("/server/stop", {})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.status()["state"], "stopped")
        self.assertEqual(self.call("/server/fly", {})[0], 400)

    def test_a_server_that_fails_to_start_shows_why(self):
        (self.box.root / "bin" / "serve-qwen").write_text("#!/usr/bin/env bash\necho 'error: only 1 GiB is free' >&2\nexit 1\n")
        self.call("/server/start", {})
        status = self.wait_for("stopped (exit 1)")
        self.assertIn("only 1 GiB is free", status["log"])

    def test_save_and_restart(self):
        self.call("/server/start", {})
        self.wait_for("running")
        status, _, body = self.call("/settings", {"set.QWEN_TEMP": "0.5", "loaded.QWEN_TEMP": "", "then": "restart"})
        self.assertIn("Restarting the server", body)
        self.wait_for("running")
        self.call("/server/stop", {})
        status, _, body = self.call("/settings", {"set.QWEN_TEMP": "0.6", "loaded.QWEN_TEMP": "0.5", "then": "restart"})
        self.assertIn("The server is not running", body)

    def test_agents_page_and_integrate_errors(self):
        status, _, page = self.call("/agents")
        self.assertEqual(status, 200)
        status, _, body = self.call("/agents/integrate/install", {})
        self.assertEqual(status, 400)
        self.assertIn("no coding agent to set up", body)


class ChatTest(WebUiCase):
    """Chat goes to the model server as default_model."""

    def configure(self):
        self.mlx = FakeServer(mlx_health_routes())
        self.mlx.routes["POST /v1/chat/completions"] = (200, (b'data: {"choices": [{"delta": {"content": "hi"}}]}\n\ndata: [DONE]\n\n',
                                                              {"Content-Type": "text/event-stream"}))
        self.addCleanup(self.mlx.stop)
        record_served(self.box, self.mlx.base_url, 65536)
        (self.box.root / "puny.conf").write_text(f"MLX_BASE_URL={self.mlx.base_url}\n")

    def test_status_and_chat(self):
        self.assertEqual(self.status()["state"], "running")
        status, _, text = self.call("/api/chat", json_body={"model": "other", "messages": [{"role": "user", "content": "x"}], "stream": True})
        self.assertEqual(status, 200)
        self.assertIn('"content": "hi"', text)
        self.assertEqual(json.loads(self.mlx.requests[-1][2])["model"], "default_model")
        self.mlx.stop()
        status, _, text = self.call("/api/chat", json_body={"messages": []})
        self.assertEqual(status, 502)
        self.assertIn("Start it on the Server page", text)

    def test_refuses_other_sites(self):
        self.assertEqual(self.call("/api/status", headers={"Host": "evil.example"})[0], 403)
        status, _, _ = self.call("/jobs/download", {"model": f"org/m@{COMMIT}"}, headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        self.assertFalse((self.box.root / "state" / "jobs").exists())


class JobsTest(WebUiCase):
    """Jobs run the repo's commands and keep their output."""

    def configure(self):
        (self.box.root / "bin" / "bench-agents").write_text('#!/usr/bin/env bash\necho "agents=$BENCH_AGENTS tasks=$BENCH_TASKS label=$1"\n')

    def test_benchmark_job(self):
        status, headers, body = self.call("/benchmarks/agents", {"label": "kat", "agent": ["pi"], "task": ["todo", "billing"]})
        self.assertEqual(status, 204, body)
        job = headers["HX-Redirect"]
        deadline = time.monotonic() + 20
        while "pill good\">done" not in (page := self.call(job)[2]) and time.monotonic() < deadline:
            time.sleep(0.2)
        self.assertIn("agents=pi tasks=billing todo label=kat", page)
        self.assertIn("BENCH_AGENTS=pi BENCH_TASKS=&#39;billing todo&#39; ./bin/bench-agents kat", page)
        self.assertIn("Agent benchmark kat", self.call("/jobs")[2])


class BinWebUiTest(unittest.TestCase):
    """bin/web-ui builds once, then serves, refusing an address off this Mac."""

    def test_build_then_serve(self):
        box = Sandbox()
        self.addCleanup(box.cleanup)
        state = box.root / "state"
        # The pinned Go that tests/run installed, its build cache, and HTMX.
        _, htmx = built()
        go_versions = REPO / "state" / "go"
        (state / "go").symlink_to(go_versions)
        (state / "cache").mkdir()
        (state / "cache" / "go").symlink_to(REPO / "state" / "cache" / "go")
        (state / "web-ui").mkdir()
        shutil.copy(htmx, state / "web-ui" / "htmx.min.js")
        (state / "mlx-env").symlink_to(REPO / "state" / "mlx-env")

        result = box.run(["bin/web-ui", "--build"], timeout=600)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Building the web UI", result.stdout)
        result = box.run(["bin/web-ui", "--build"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Building", result.stdout)

        result = box.run(["bin/web-ui"], extra_env={"WEB_UI_URL": "http://192.168.1.2:8090"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only listens on this Mac", result.stderr)

        port = free_port()
        ui = subprocess.Popen([str(box.root / "bin" / "web-ui")], env=box.env({"WEB_UI_URL": f"http://127.0.0.1:{port}"}),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True)
        self.addCleanup(ui.wait, 10)
        self.addCleanup(ui.terminate)
        self.assertIn("The web UI is at", ui.stdout.readline())
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/static/htmx.min.js", timeout=10) as response:
            self.assertEqual(hashlib.sha256(response.read()).hexdigest(),
                             "d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717")
        ui.stdout.close()


if __name__ == "__main__":
    unittest.main()
