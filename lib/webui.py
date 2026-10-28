"""A local web page for chatting with the served model and finding new ones.

Usage: python -I webui.py --listen http://HOST:PORT --mlx-base-url URL --repo DIR

Serves lib/webui.html and a small JSON API for it:
  GET  /api/status            the server at MLX_BASE_URL: running, model, window
  POST /api/chat              a chat completion, streamed, passed to the server
  GET  /api/search?q=&source= MLX models on Hugging Face and ModelScope
  GET  /api/info?model=       size, architecture, tool calls and fit (lib/models.py)
  GET  /api/models            the models in state/models
  POST /api/downloads         {"model": "REPO@COMMIT"}: runs bin/models download
  GET  /api/downloads         each download's state and the end of its log

It listens on one address, 127.0.0.1 by default, and answers only requests
whose Host header names that address, and POSTs only from its own page, so
other web sites cannot use it through your browser. Downloads run one at a
time through bin/models, with the same pinning, hash checks and disk checks
as the command line.
"""

import argparse
import http.server
import json
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


def server_status(mlx_base_url, repo):
    """Whether bin/serve-qwen runs at mlx_base_url, and what it serves."""
    root = mlx_base_url.rsplit("/v1", 1)[0]
    port = urllib.parse.urlsplit(mlx_base_url).port
    status = {"base_url": mlx_base_url, "running": False, "window_tokens": None, "model": None}
    try:
        with urllib.request.urlopen(root + "/health", timeout=3) as response:
            status["running"] = json.load(response).get("status") == "ok"
    except (OSError, ValueError):
        return status
    record = repo / "state" / "served" / f"port-{port}"
    if record.is_file():
        fields = record.read_text().split()
        if len(fields) == 2 and fields[1].isdigit():
            status["window_tokens"] = int(fields[1])
            command = subprocess.run(["ps", "-o", "command=", "-p", fields[0]], capture_output=True, text=True).stdout
            parts = command.split()
            if "--model-dir" in parts and parts.index("--model-dir") + 1 < len(parts):
                status["model"] = Path(parts[parts.index("--model-dir") + 1]).name
    return status


def make_handler(origin, mlx_base_url, repo, downloads):
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
                    self.send(200, server_status(mlx_base_url, repo))
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
            else:
                self.send(404, {"error": "not found"})

        def chat(self, body):
            """Passes a chat request to the server and its streamed reply back."""
            body["model"] = "default_model"
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
    parser.add_argument("--mlx-base-url", required=True)
    parser.add_argument("--repo", required=True, type=Path)
    args = parser.parse_args()
    parts = urllib.parse.urlsplit(args.listen)
    origin = f"http://{parts.netloc}"
    handler = make_handler(origin, args.mlx_base_url.rstrip("/"), args.repo, Downloads(args.repo))
    httpd = http.server.ThreadingHTTPServer((parts.hostname, parts.port), handler)
    print(f"The web UI is at {origin}/ (Ctrl-C stops it). Chat needs ./bin/serve-qwen running at {args.mlx_base_url}.",
          flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
