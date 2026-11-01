"""Reads and edits puny.conf, the settings file of puny-local-ai.

Usage:
  python3 -I config.py keys                     print the setting names, one per line
  python3 -I config.py read FILE                print the file's settings as JSON
  python3 -I config.py write FILE KEY=VALUE...  set settings; an empty VALUE removes one
  python3 -I config.py describe                 print every setting with its group and help, as JSON

The file holds one KEY=VALUE per line, as in a shell script, with blank
lines and # comments allowed. Values are taken literally, without shell
expansion; one pair of surrounding quotes is removed. lib/common.sh reads
the file for every command, and an environment variable of the same name
wins over the file.

Writing keeps comments, blank lines and the order of the other lines, and
replaces the file in one step, so a reader never sees half a file.

Needs only the standard library and runs with the Python 3.9 that macOS
ships.
"""

import json
import os
import re
import sys
import tempfile
from pathlib import Path

# The settings the file may hold, with the help the web UI shows. lib/common.sh
# keeps the same names in CONFIG_SETTINGS; the tests check that they agree.
SETTINGS = [
    ("MLX_BASE_URL", "server", "Where the model server listens and the agents connect: http://HOST:PORT/v1."),
    ("QWEN_PRESET", "model", "A pinned model: kat (default), reap, small, or for larger Macs kat-full, qwen38-4, qwen38, coder-next or coder-next-8. Leave empty when QWEN_REPO is set."),
    ("QWEN_REPO", "model", "Any other model, as ./bin/models names it. Needs QWEN_REVISION and QWEN_CONTEXT."),
    ("QWEN_REVISION", "model", "The full 40-character commit of QWEN_REPO."),
    ("QWEN_CONTEXT", "model", "The context window in tokens. Defaults to the preset's model card; required with QWEN_REPO."),
    ("QWEN_MAX_OUTPUT", "server", "The most tokens one reply may use (default 8192)."),
    ("QWEN_TEMP", "server", "Sampling temperature (default: the preset's; 0.6 for any other model)."),
    ("QWEN_KV_BITS", "server", "Context-cache quantization: 8 (default) or 4."),
    ("QWEN_PROMPT_CACHE_ENTRIES", "server", "Conversations whose context cache the server keeps (default 0)."),
    ("QWEN_PREFILL_STEP", "server", "The most prompt tokens processed per step (default 512, at least 16)."),
    ("QWEN_MEMORY_MARGIN", "server", "Memory kept for the server outside MLX, such as 512M (default) or 1G."),
    ("WEB_UI_URL", "tools", "Where ./bin/web-ui listens (default http://127.0.0.1:8090)."),
    ("SMOKE_TIMEOUT", "tools", "Seconds each agent may take in ./bin/smoke-test (default 300)."),
    ("BENCH_AGENTS", "tools", "Agents ./bin/bench-agents runs (default: opencode pi)."),
    ("BENCH_TASKS", "tools", "Tasks ./bin/bench-agents runs (default: every folder in bench/)."),
    ("BENCH_RUNS", "tools", "Runs of each task with each agent (default 1)."),
    ("BENCH_TIMEOUT", "tools", "Seconds per benchmark run (default 900)."),
    ("TEAM_SOLVE_AGENT", "tools", "The agent every ./bin/team-solve step runs: pi (default), opencode or codex."),
    ("TEAM_SOLVE_TIMEOUT", "tools", "Seconds each ./bin/team-solve step may take (default 600)."),
]
KEYS = [key for key, _, _ in SETTINGS]
LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
HEADER = (
    "# puny-local-ai settings, read by every ./bin command. An environment variable\n"
    "# of the same name wins over this file. ./bin/config and ./bin/web-ui edit it;\n"
    "# README.md#settings describes each setting.\n"
)


class ConfigError(Exception):
    pass


def unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse(text, path="puny.conf"):
    """The settings in text, as a dict, refusing unknown names and malformed lines."""
    values = {}
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = LINE.match(line)
        if not match:
            raise ConfigError(f"{path} line {number} is not KEY=VALUE: {line!r}")
        key = match.group(1)
        if key not in KEYS:
            raise ConfigError(f"{path} line {number}: {key} is not a puny-local-ai setting; expected one of {', '.join(KEYS)}")
        value = unquote(match.group(2))
        if "\n" in value or "\0" in value:
            raise ConfigError(f"{path} line {number}: the value of {key} must be one line")
        values[key] = value
    return values


def read(path):
    path = Path(path)
    return parse(path.read_text(), path) if path.exists() else {}


def updated_text(text, changes):
    """text with each changed key replaced in place, appended, or removed when its value is empty."""
    lines = text.splitlines()
    done = set()
    kept = []
    for line in lines:
        match = LINE.match(line)
        key = match.group(1) if match else None
        if key in changes:
            if key not in done and changes[key] != "":
                kept.append(f"{key}={changes[key]}")
            done.add(key)
        else:
            kept.append(line)
    for key in KEYS:
        if key in changes and key not in done and changes[key] != "":
            kept.append(f"{key}={changes[key]}")
    if not any(line.strip() for line in kept):
        return ""
    if not text:
        kept.insert(0, HEADER.rstrip("\n"))
    return "\n".join(kept) + "\n"


def write(path, changes):
    path = Path(path)
    for key, value in changes.items():
        if key not in KEYS:
            raise ConfigError(f"{key} is not a puny-local-ai setting; expected one of {', '.join(KEYS)}")
        if "\n" in value or "\r" in value or "\0" in value:
            raise ConfigError(f"the value of {key} must be one line")
        if value != value.strip():
            raise ConfigError(f"the value of {key} must not start or end with spaces")
    text = path.read_text() if path.exists() else ""
    parse(text, path)
    new = updated_text(text, changes)
    parse(new, path)
    if new == text:
        return
    if not new:
        path.unlink()
        return
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".puny.conf.")
    try:
        with os.fdopen(handle, "w") as f:
            f.write(new)
        if path.exists():
            os.chmod(temporary, path.stat().st_mode & 0o777)
        else:
            os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def main():
    args = sys.argv[1:]
    try:
        if args == ["keys"]:
            print("\n".join(KEYS))
        elif args == ["describe"]:
            print(json.dumps([{"key": k, "group": g, "help": h} for k, g, h in SETTINGS]))
        elif len(args) == 2 and args[0] == "read":
            print(json.dumps(read(args[1])))
        elif len(args) >= 3 and args[0] == "write":
            changes = {}
            for pair in args[2:]:
                key, sep, value = pair.partition("=")
                if not sep:
                    raise ConfigError(f"{pair!r} is not KEY=VALUE")
                changes[key] = value
            write(args[1], changes)
        else:
            sys.exit(__doc__)
    except ConfigError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
