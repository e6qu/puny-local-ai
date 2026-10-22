"""Shared test helpers.

Every test runs the scripts from a fresh copy of bin/, lib/ and bench/ in a
temporary folder, with its own state/ and its own home folder, so nothing
touches the real repo state, the real agent configs or ~/. Stub programs
stand in for npm, node, opencode, pi and codex, and FakeServer stands in for
mlx_lm.server or a Hugging Face endpoint.
"""

import http.server
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def which(name):
    path = shutil.which(name)
    if path is None:
        raise unittest.SkipTest(f"{name} is not installed")
    return path


class Sandbox:
    """A copy of the repo's scripts with its own state, home and PATH."""

    def __init__(self, link_mlx_env=False):
        # Resolved, because the scripts resolve symlinks such as /var -> /private/var.
        self.dir = Path(tempfile.mkdtemp(prefix="puny-test-")).resolve()
        self.root = self.dir / "repo"
        for part in ("bin", "lib", "bench"):
            shutil.copytree(REPO / part, self.root / part, ignore=shutil.ignore_patterns("__pycache__"))
        (self.root / "state").mkdir()
        if link_mlx_env:
            (self.root / "state" / "mlx-env").symlink_to(REPO / "state" / "mlx-env")
        self.home = self.dir / "home"
        self.home.mkdir()
        self.stubs = self.dir / "stubs"
        self.stubs.mkdir()
        self.work = self.dir / "work"
        self.work.mkdir()
        self.calls = self.dir / "calls"
        self.calls.mkdir()

    def env(self, extra=None, path_dirs=()):
        """An environment with nothing from the real one except what is listed."""
        env = {
            "HOME": str(self.home),
            "PATH": ":".join([str(self.stubs), *path_dirs, SYSTEM_PATH]),
            "TMPDIR": str(self.dir),
            "LANG": "en_US.UTF-8",
            "STUB_CALLS": str(self.calls),
            # Any accidental Hugging Face call fails at once instead of downloading.
            "HF_HUB_OFFLINE": "1",
            "HF_ENDPOINT": "http://127.0.0.1:9",
        }
        env.update(extra or {})
        return env

    def run(self, args, extra_env=None, path_dirs=(), input=None, timeout=120, cwd=None):
        """Runs a repo script by its path relative to the repo, e.g. "bin/setup"."""
        if isinstance(args, str):
            args = [args]
        command = [str(self.root / args[0]), *args[1:]]
        return subprocess.run(
            command,
            env=self.env(extra_env, path_dirs),
            # Empty input unless given, so no program waits on the test runner's.
            input=input if input is not None else "",
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd or self.work,
        )

    def stub(self, name, script):
        """Writes an executable stub. Each call is logged to calls/NAME."""
        path = self.stubs / name
        path.write_text(
            "#!/bin/bash\n"
            f'printf "%s\\n" "$*" >> "$STUB_CALLS/{name}"\n'
            + script
            + "\n"
        )
        path.chmod(0o755)
        return path

    def fake_mlx_env(self):
        """A stand-in for state/mlx-env whose python logs its calls.

        verify_model.py calls exit with $STUB_VERIFY (writing .verified on 0),
        serve.py --print-window prints $STUB_WINDOW, or else the --context it
        was given, other serve.py calls record
        their pid and arguments, and anything else, such as the download
        snippet, exits with $STUB_DOWNLOAD.
        """
        env = self.root / "state" / "mlx-env"
        (env / "bin").mkdir(parents=True)
        python = env / "bin" / "python"
        python.write_text(r'''#!/bin/bash
args="$*"
printf '%s\n' "${args//$'\n'/ }" >> "$STUB_CALLS/python"
case "$*" in
  *verify_model.py*)
    status="${STUB_VERIFY:-0}"
    [[ "$status" == 0 ]] && touch "${@: -1}/.verified"
    exit "$status" ;;
  *serve.py*--print-window*)
    # The window asked for, unless a test says less fits.
    context=""
    previous=""
    for arg in "$@"; do
      [[ "$previous" == --context ]] && context="$arg"
      previous="$arg"
    done
    echo "${STUB_WINDOW:-$context}" ;;
  *serve.py*)
    echo "$$" > "$STUB_CALLS/serve.pid"
    printf '%s\n' "$@" > "$STUB_CALLS/serve.args"
    exit 0 ;;
  *) exit "${STUB_DOWNLOAD:-0}" ;;
esac
''')
        python.chmod(0o755)
        stamp = subprocess.run(
            ["bash", "-c", f'source "{self.root}/lib/common.sh"; source "{self.root}/lib/mlx.sh"; mlx_env_stamp'],
            env=self.env(), capture_output=True, text=True, check=True,
        ).stdout
        (env / ".installed").write_text(stamp)

    def fake_model(self, repo, revision, verified=True):
        """A model folder with a config and a tiny weights file."""
        folder = self.root / "state" / "models" / f"{repo.replace('/', '--')}@{revision}"
        folder.mkdir(parents=True)
        (folder / "config.json").write_text(json.dumps({"num_hidden_layers": 2, "num_attention_heads": 2, "hidden_size": 8}))
        (folder / "model.safetensors").write_bytes(b"0" * 1024)
        if verified:
            (folder / ".verified").write_text("ok\n")
        return folder

    def link_tool(self, name):
        """Makes a real program available on the sandbox PATH."""
        (self.stubs / name).symlink_to(which(name))

    def called(self, name):
        path = self.calls / name
        return path.read_text().splitlines() if path.exists() else []

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class FakeServer:
    """A local HTTP server; `routes` maps "GET /path" to (status, body or callable)."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def handle_any(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                path = self.path.split("?")[0]
                server.requests.append((method, self.path, body))
                route = server.routes.get(f"{method} {path}")
                if route is None:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b'{"error": "not found"}')
                    return
                status, payload = route
                if callable(payload):
                    payload = payload(self, body)
                headers = {}
                if isinstance(payload, tuple):
                    payload, headers = payload
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self.handle_any("GET")

            def do_POST(self):
                self.handle_any("POST")

        # Port 0 lets the system pick a free port, with no race against other programs.
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}/v1"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def mlx_health_routes():
    return {"GET /health": (200, {"status": "ok"})}


def record_served(sandbox, base_url, context, pid=None):
    """Writes the record ./bin/serve-qwen leaves for the launchers."""
    port = base_url.rsplit(":", 1)[1].split("/")[0]
    path = sandbox.root / "state" / "served" / f"port-{port}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid or os.getpid()} {context}\n")
    return path


# A stand-in for opencode and pi. It logs its arguments and the config it was
# given, then acts as STUB_BEHAVIOR says: "solve" copies STUB_SOLUTION into
# the current folder, "hello" writes the file bin/smoke-test asks for,
# "nothing" does nothing, "hang" sleeps, "fail" exits with status 3 after
# solving.
AGENT_STUB = r'''
name="$(basename "$0")"
if [[ "${1:-}" == --version ]]; then
  echo "${STUB_VERSION:-2.1.1}"
  exit 0
fi
{
  printf 'cwd=%s\n' "$PWD"
  printf 'PI_CODING_AGENT_DIR=%s\n' "${PI_CODING_AGENT_DIR:-}"
  printf 'XDG_DATA_HOME=%s\n' "${XDG_DATA_HOME:-}"
} >> "$STUB_CALLS/$name.env"
printf '%s' "${OPENCODE_CONFIG_CONTENT:-}" > "$STUB_CALLS/$name.config"
case "${STUB_BEHAVIOR:-nothing}" in
  solve) cp -R "$STUB_SOLUTION"/. . ;;
  hello) printf 'hello from %s' "$name" > hello.txt ;;
  wrong) printf 'something else' > hello.txt ;;
  hang) exec sleep 30 ;;
  fail) cp -R "$STUB_SOLUTION"/. . 2>/dev/null; exit 3 ;;
  nothing) ;;
esac
'''


def tool_call_reply(name="get_version"):
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "1", "type": "function", "function": {"name": name, "arguments": "{}"}}],
            },
            "finish_reason": "tool_calls",
        }]
    }


class ServerCase(unittest.TestCase):
    """A sandbox plus a fake mlx_lm.server that bin/serve-qwen 'started'."""

    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)
        self.server = FakeServer(mlx_health_routes())
        self.server.routes["POST /v1/chat/completions"] = (200, tool_call_reply())
        self.addCleanup(self.server.stop)
        self.env = {"MLX_BASE_URL": self.server.base_url}
        record_served(self.box, self.server.base_url, 262144)

    def agent_stubs(self, *names):
        for name in names:
            self.box.stub(name, AGENT_STUB)

    def env_log(self, name):
        path = self.box.calls / f"{name}.env"
        return path.read_text() if path.exists() else ""


def run_in_terminal(command, env, cwd, answer, prompt, timeout=60):
    """Runs command on a pseudo-terminal, answers its prompt, and returns (status, output)."""
    import select
    import time

    main, child = os.openpty()
    process = subprocess.Popen(command, stdin=child, stdout=child, stderr=child, env=env, cwd=cwd, start_new_session=True)
    os.close(child)
    output, answered, deadline = b"", False, time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([main], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(main, 4096)
                except OSError:
                    break  # the program ended and closed the terminal
                if not chunk:
                    break
                output += chunk
            if not answered and prompt.encode() in output:
                os.write(main, answer.encode())
                answered = True
            if process.poll() is not None and not ready:
                break
        else:
            # The whole process group, so children such as setup --one stop too.
            os.killpg(process.pid, 9)
            raise TimeoutError(output.decode(errors="replace"))
        return process.wait(timeout=10), output.decode(errors="replace").replace("\r\n", "\n")
    finally:
        os.close(main)
