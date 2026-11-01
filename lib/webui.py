"""A local web page for chatting with the model, changing settings and finding new models.

Usage: python -I webui.py --listen http://HOST:PORT --repo DIR

Serves lib/webui.html and a small JSON API for it:
  GET  /api/status            the server: running, starting or stopped, model, window, log
  POST /api/server            {"action": "start" | "stop" | "restart"} for bin/serve-qwen
  GET  /api/config            every setting, its value and source (bin/config show)
  POST /api/config            {"set": {KEY: VALUE}, "unset": [KEY]}, through bin/config
  POST /api/integrate         runs bin/integrate install, so the agents get the new settings
  POST /api/chat              a chat completion, streamed, passed to the server
  GET  /api/search?q=&source= MLX models on Hugging Face and ModelScope
  GET  /api/info?model=       size, architecture, tool calls and fit (lib/models.py)
  GET  /api/models            the models in state/models
  POST /api/downloads         {"model": "REPO@COMMIT"}: runs bin/models download
  GET  /api/downloads         each download's state and the end of its log

Every change goes through the same commands as the terminal: settings through
bin/config, which checks them before saving puny.conf; the server through
bin/serve-qwen, with its memory guards; downloads through bin/models.
The settings are read again for each request, so a change made in the
terminal shows up too. bin/web-ui starts this without the values it read from
puny.conf, so the commands it runs read the file afresh.

A server started here runs in its own session and keeps running when the
page stops; its output goes to state/logs/serve-qwen.log. Stop stops any
server this repo started, wherever it was started.

It listens on one address, 127.0.0.1 by default, and answers only requests
whose Host header names that address, and POSTs only from its own page, so
other web sites cannot use it through your browser.
"""

import argparse
import http.server
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import models  # noqa: E402
import sources  # noqa: E402

PAGE = Path(__file__).resolve().parent / "webui.html"
LOG_TAIL_BYTES = 4096
STOP_TIMEOUT_SECONDS = 30


class CommandError(Exception):
    pass


def run_command(repo, *args, timeout=600):
    """Runs one of the repo's commands and returns its output, or raises with what it printed."""
    result = subprocess.run([str(repo / "bin" / args[0]), *args[1:]], capture_output=True, text=True,
                            stdin=subprocess.DEVNULL, timeout=timeout)
    if result.returncode != 0:
        raise CommandError((result.stderr or result.stdout).strip() or f"{args[0]} failed")
    return result.stdout


def tail(path):
    if not path.is_file():
        return ""
    with path.open("rb") as f:
        f.seek(max(0, path.stat().st_size - LOG_TAIL_BYTES))
        return f.read().decode(errors="replace")


def settings(repo):
    """{"file": ..., "settings": [{"key", "value", "source", "group", "help"}]} from bin/config."""
    return json.loads(run_command(repo, "config", "show", "--json"))


def setting(repo, key):
    return next(s["value"] for s in settings(repo)["settings"] if s["key"] == key)


class Downloads:
    """bin/models download runs, one at a time, with their logs in state/downloads."""

    def __init__(self, repo):
        self.repo = repo
        self.jobs = []
        self.lock = threading.Lock()

    def start(self, spec):
        with self.lock:
            if any(job["process"].poll() is None for job in self.jobs):
                raise ValueError("a download is already running; wait for it to finish")
            log_dir = self.repo / "state" / "downloads"
            log_dir.mkdir(parents=True, exist_ok=True)
            log = log_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{len(self.jobs) + 1}.log"
            with log.open("wb") as out:
                process = subprocess.Popen([str(self.repo / "bin" / "models"), "download", spec],
                                           stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            self.jobs.append({"model": spec, "log": log, "process": process, "started": time.time()})

    def status(self):
        with self.lock:
            result = []
            for job in self.jobs:
                code = job["process"].poll()
                with job["log"].open("rb") as f:
                    f.seek(max(0, job["log"].stat().st_size - LOG_TAIL_BYTES))
                    tail = f.read().decode(errors="replace")
                result.append({
                    "model": job["model"], "log": str(job["log"]), "tail": tail,
                    "state": "running" if code is None else ("done" if code == 0 else f"failed (exit {code})"),
                })
            return result


class Server:
    """Starts and stops the bin/serve-qwen servers of this repo."""

    def __init__(self, repo):
        self.repo = repo
        self.process = None  # the server this page started last
        self.stopped = False  # whether Stop ended it, rather than an error
        self.log = repo / "state" / "logs" / "serve-qwen.log"
        self.lock = threading.Lock()

    def running(self):
        """(pid, port) of each server of this repo that bin/serve-qwen recorded and that still runs."""
        found = []
        for record in sorted((self.repo / "state" / "served").glob("port-*")):
            fields = record.read_text().split()
            if not fields or not fields[0].isdigit():
                continue
            command = subprocess.run(["ps", "-o", "command=", "-p", fields[0]], capture_output=True, text=True).stdout
            if str(self.repo / "lib" / "serve.py") in command:
                found.append((int(fields[0]), record.name[len("port-"):]))
        return found

    def starting(self):
        return self.process is not None and self.process.poll() is None

    def start(self):
        with self.lock:
            running = self.running()
            if running or self.starting():
                pid = running[0][0] if running else self.process.pid
                raise CommandError(f"a server already runs (pid {pid}); stop it first, or use Restart")
            self.log.parent.mkdir(parents=True, exist_ok=True)
            with self.log.open("wb") as out:
                # Its own session, so it keeps running when this page stops.
                self.stopped = False
                self.process = subprocess.Popen([str(self.repo / "bin" / "serve-qwen")], stdout=out,
                                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                                start_new_session=True)

    def stop(self):
        with self.lock:
            pids = [pid for pid, _ in self.running()]
            if self.starting() and self.process.pid not in pids:
                pids.append(self.process.pid)
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
            while time.monotonic() < deadline and any(alive(pid) for pid in pids):
                if self.process is not None:
                    self.process.poll()  # collects it once it exits
                time.sleep(0.2)
            self.stopped = True
            left = [pid for pid in pids if alive(pid)]
            if left:
                raise CommandError(f"the server (pid {left[0]}) did not stop within {STOP_TIMEOUT_SECONDS}s")
            return pids

    def status(self, base_url):
        root = base_url.rsplit("/v1", 1)[0]
        port = urllib.parse.urlsplit(base_url).port
        status = {"base_url": base_url, "state": "stopped", "window_tokens": None, "model": None,
                  "log": tail(self.log), "elsewhere": False}
        healthy = False
        try:
            with urllib.request.urlopen(root + "/health", timeout=3) as response:
                healthy = json.load(response).get("status") == "ok"
        except (OSError, ValueError):
            pass
        if healthy:
            status["state"] = "running"
        elif self.starting():
            status["state"] = "starting"
        elif self.process is not None and not self.stopped:
            status["state"] = f"stopped (exit {self.process.returncode})"
        others = [p for _, p in self.running() if p != str(port)]
        if others:
            # Settings changed since it started, such as the port.
            status["elsewhere"] = f"a server of this repo runs on port {others[0]}, not at {base_url}; restart it to apply the settings"
        record = self.repo / "state" / "served" / f"port-{port}"
        if healthy and record.is_file():
            fields = record.read_text().split()
            if len(fields) == 2 and fields[1].isdigit():
                status["window_tokens"] = int(fields[1])
                command = subprocess.run(["ps", "-o", "command=", "-p", fields[0]], capture_output=True, text=True).stdout
                parts = command.split()
                if "--model-dir" in parts and parts.index("--model-dir") + 1 < len(parts):
                    status["model"] = Path(parts[parts.index("--model-dir") + 1]).name
        return status


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def make_handler(origin, repo, downloads, server):
    host = urllib.parse.urlsplit(origin).netloc

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            sys.stderr.write(f"webui: {self.address_string()} {fmt % args}\n")

        def send(self, status, payload, kind="application/json"):
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def allowed(self, post=False):
            # A page on another site could otherwise reach this server through
            # the browser, by DNS rebinding or a cross-site POST.
            if self.headers.get("Host") != host:
                self.send(403, {"error": f"this page answers only at {origin}"})
                return False
            if post and self.headers.get("Origin") not in (None, origin):
                self.send(403, {"error": "requests must come from this page"})
                return False
            return True

        def query(self):
            return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}

        def do_GET(self):
            if not self.allowed():
                return
            path = urllib.parse.urlsplit(self.path).path
            try:
                if path == "/":
                    self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                elif path == "/api/status":
                    self.send(200, server.status(setting(repo, "MLX_BASE_URL")))
                elif path == "/api/config":
                    self.send(200, settings(repo))
                elif path == "/api/search":
                    q = self.query()
                    results, problems = models.search(q.get("q", ""), q.get("source", "all"), 20)
                    self.send(200, {"results": results, "problems": problems})
                elif path == "/api/info":
                    self.send(200, models.info(self.query().get("model", "")))
                elif path == "/api/models":
                    self.send(200, models.downloaded())
                elif path == "/api/downloads":
                    self.send(200, downloads.status())
                else:
                    self.send(404, {"error": "not found"})
            except sources.SourceError as e:
                self.send(502, {"error": str(e)})
            except CommandError as e:
                self.send(500, {"error": str(e)})

        def read_json(self):
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}")

        def do_POST(self):
            if not self.allowed(post=True):
                return
            path = urllib.parse.urlsplit(self.path).path
            try:
                body = self.read_json()
            except ValueError:
                self.send(400, {"error": "the body must be JSON"})
                return
            if path == "/api/downloads":
                spec = str(body.get("model", ""))
                name, _, commit = spec.partition("@")
                try:
                    sources.split(name)
                    if not sources.COMMIT.match(commit):
                        raise ValueError("give the model as NAME@COMMIT, with the full commit from Check")
                    downloads.start(spec)
                except (sources.SourceError, ValueError) as e:
                    self.send(400, {"error": str(e)})
                    return
                self.send(202, {"started": spec})
            elif path == "/api/chat":
                self.chat(body)
            elif path == "/api/config":
                self.change_settings(body)
            elif path == "/api/server":
                self.control_server(body.get("action"))
            elif path == "/api/integrate":
                try:
                    self.send(200, {"output": run_command(repo, "integrate", "install")})
                except CommandError as e:
                    self.send(400, {"error": str(e)})
            else:
                self.send(404, {"error": "not found"})

        def change_settings(self, body):
            values, removed = body.get("set") or {}, body.get("unset") or []
            if not isinstance(values, dict) or not isinstance(removed, list) or not (values or removed):
                self.send(400, {"error": 'send {"set": {KEY: VALUE}, "unset": [KEY]}'})
                return
            pairs = [f"{k}={v}" for k, v in values.items()] + [f"{k}=" for k in removed]
            try:
                # One bin/config call, so the change is checked as a whole before it is saved.
                output = run_command(repo, "config", "set", *pairs)
            except CommandError as e:
                self.send(400, {"error": str(e)})
                return
            self.send(200, {"output": output, "config": settings(repo)})

        def control_server(self, action):
            try:
                if action in ("stop", "restart"):
                    server.stop()
                if action in ("start", "restart"):
                    server.start()
                elif action != "stop":
                    raise CommandError("action must be start, stop or restart")
            except CommandError as e:
                self.send(400, {"error": str(e)})
                return
            self.send(200, server.status(setting(repo, "MLX_BASE_URL")))

        def chat(self, body):
            """Passes a chat request to the server and its streamed reply back."""
            body["model"] = "default_model"
            try:
                mlx_base_url = setting(repo, "MLX_BASE_URL")
            except CommandError as e:
                self.send(500, {"error": str(e)})
                return
            request = urllib.request.Request(mlx_base_url + "/chat/completions", data=json.dumps(body).encode(),
                                             headers={"Content-Type": "application/json"})
            try:
                upstream = urllib.request.urlopen(request, timeout=3600)
            except urllib.error.HTTPError as e:
                with e:
                    self.send(e.code, e.read() or b'{"error": "the server refused the request"}')
                return
            except OSError as e:
                self.send(502, {"error": f"cannot reach the model server at {mlx_base_url}: {e}. Start it with ./bin/serve-qwen."})
                return
            with upstream:
                self.send_response(200)
                self.send_header("Content-Type", upstream.headers.get("Content-Type", "application/json"))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                while chunk := upstream.read1(65536):
                    self.wfile.write(chunk)
                    self.wfile.flush()

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--listen", required=True, help="http://HOST:PORT to serve the page at")
    parser.add_argument("--repo", required=True, type=Path)
    args = parser.parse_args()
    parts = urllib.parse.urlsplit(args.listen)
    origin = f"http://{parts.netloc}"
    handler = make_handler(origin, args.repo, Downloads(args.repo), Server(args.repo))
    httpd = http.server.ThreadingHTTPServer((parts.hostname, parts.port), handler)
    print(f"The web UI is at {origin}/ (Ctrl-C stops it; a server started from it keeps running).", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
