"""Adds the local model to the coding agents' own configs, and removes it again.

Usage:
  python3 -I integrate.py install AGENT...
  python3 -I integrate.py uninstall AGENT...
  python3 -I integrate.py status AGENT...
  python3 -I integrate.py config AGENT       print the config a launcher passes

AGENT is opencode, pi or codex. Settings come from the environment, set by
bin/integrate: PUNY_BASE_URL, PUNY_CONTEXT, PUNY_MAX_OUTPUT and PUNY_STATE_DIR.

Each agent gets one provider with the id "puny" and the name
"puny-local-ai", and nothing else changes:
  - OpenCode: added to the global config, opencode.json or opencode.jsonc in
    $XDG_CONFIG_HOME/opencode (default ~/.config/opencode). Comments and
    formatting are kept. The default model is left alone; pick
    puny/qwen-local with --model or /models.
  - Pi: added to models.json in $PI_CODING_AGENT_DIR (default ~/.pi/agent).
  - Codex: a profile file of its own, puny.config.toml in $CODEX_HOME
    (default ~/.codex), used with `codex -p puny`.

Before changing a file it saves a copy under PUNY_STATE_DIR/integrations, and
records the exact text it added. Uninstalling takes out exactly that text, so
the file goes back byte for byte to what it was plus any edits made since;
a file it created is deleted. It refuses to touch a "puny" entry it did not
add, and checks after every change that nothing else in the file changed.
"""

import datetime
import fcntl
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jsonc  # noqa: E402

PROVIDER_ID = "puny"
MARKER = "puny-local-ai"
OPENCODE_MODEL = "qwen-local"
SERVED_MODEL = "default_model"
CODEX_FILE = f"{PROVIDER_ID}.config.toml"
CODEX_HEADER = f"# Added by {MARKER}. Remove it with: ./bin/integrate uninstall codex\n"
AGENTS = ("opencode", "pi", "codex")


class Refusal(Exception):
    pass


def env(name):
    value = os.environ.get(name, "")
    if not value:
        raise Refusal(f"{name} is not set; run this through bin/integrate")
    return value


def settings():
    return {
        "base_url": env("PUNY_BASE_URL"),
        "context": int(env("PUNY_CONTEXT")),
        "max_output": int(env("PUNY_MAX_OUTPUT")),
    }


def opencode_provider(s):
    return {
        "name": MARKER,
        "package": "@opencode/ai/providers/openai-compatible",
        "settings": {"baseURL": s["base_url"], "apiKey": "local"},
        # mlx_lm.server returns thinking text in the "reasoning" field.
        "compatibility": {"reasoningField": "reasoning"},
        "models": {
            OPENCODE_MODEL: {
                "modelID": SERVED_MODEL,
                "name": "Qwen (local MLX)",
                "capabilities": {
                    "tools": True,
                    "input": ["text"],
                    "output": ["text"],
                    "reasoning": {"supported": True},
                },
                "limit": {"context": s["context"], "output": s["max_output"]},
            }
        },
    }


def pi_provider(s):
    return {
        "name": MARKER,
        "baseUrl": s["base_url"],
        "api": "openai-completions",
        # Pi hides models it has no credentials for; the server ignores the key.
        "apiKey": "local",
        "models": [
            {
                "id": SERVED_MODEL,
                "name": "Qwen (local MLX)",
                "reasoning": True,
                "input": ["text"],
                "contextWindow": s["context"],
                "maxTokens": s["max_output"],
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                "compat": {
                    "supportsDeveloperRole": False,
                    "supportsReasoningEffort": False,
                    # Toggles Qwen thinking through chat_template_kwargs, which
                    # mlx_lm.server passes to the chat template.
                    "thinkingFormat": "qwen-chat-template",
                },
            }
        ],
    }


def codex_profile(s):
    # The provider id must not be a built-in one (openai, ollama, lmstudio):
    # Codex ignores user entries that reuse those ids.
    return (
        CODEX_HEADER
        + f'model_provider = "{PROVIDER_ID}"\n'
        + f'model = "{SERVED_MODEL}"\n'
        + f"model_context_window = {s['context']}\n"
        + "\n"
        + f"[model_providers.{PROVIDER_ID}]\n"
        + f'name = "{MARKER}"\n'
        # bin/serve-qwen answers the Responses API that Codex speaks at the
        # same address as Chat Completions (lib/responses.py).
        + f"base_url = {json.dumps(s['base_url'])}\n"
        + 'wire_api = "responses"\n'
    )


def launcher_config(agent, s):
    """The whole config a launcher passes for a one-off run."""
    if agent == "opencode":
        return {
            "$schema": "https://opencode.ai/config.json",
            "model": f"{PROVIDER_ID}/{OPENCODE_MODEL}",
            "providers": {PROVIDER_ID: opencode_provider(s)},
        }
    if agent == "pi":
        return {
            "$schema": "https://pi.dev/schemas/models.schema.json",
            "providers": {PROVIDER_ID: pi_provider(s)},
        }
    raise Refusal(f"no launcher config for {agent}")


# ---------------------------------------------------------------- files


def home(name, default):
    value = os.environ.get(name)
    return Path(value) if value else Path.home() / default


def present(path):
    """True when path exists. A symlink that points nowhere is refused."""
    if path.is_symlink() and not path.exists():
        raise Refusal(f"{path} is a symlink to {os.readlink(path)}, which does not exist; fix the link first")
    return path.exists()


def config_file(agent):
    if agent == "opencode":
        directory = home("XDG_CONFIG_HOME", ".config") / "opencode"
        existing = [directory / n for n in ("opencode.jsonc", "opencode.json") if present(directory / n)]
        if len(existing) > 1:
            raise Refusal(
                f"both {existing[0]} and {existing[1]} exist; merge them into one first, "
                "so it is clear which one OpenCode reads"
            )
        return existing[0] if existing else directory / "opencode.json"
    if agent == "pi":
        directory = Path(os.environ["PI_CODING_AGENT_DIR"]) if os.environ.get("PI_CODING_AGENT_DIR") else Path.home() / ".pi" / "agent"
        return directory / "models.json"
    if agent == "codex":
        return home("CODEX_HOME", ".codex") / CODEX_FILE
    raise Refusal(f"unknown agent {agent!r}; expected one of {', '.join(AGENTS)}")


def new_file_text(agent):
    if agent == "opencode":
        return '{\n  "$schema": "https://opencode.ai/config.json"\n}\n'
    return "{\n}\n"


def state_dir():
    path = Path(env("PUNY_STATE_DIR")) / "integrations"
    path.mkdir(parents=True, exist_ok=True)
    return path


def manifest_path(agent):
    return state_dir() / f"{agent}.json"


def load_manifest(agent):
    path = manifest_path(agent)
    if not path.exists():
        return None
    try:
        manifest = json.loads(path.read_text())
        manifest["file"]
    except (ValueError, KeyError, TypeError):
        raise Refusal(f"the record {path} is damaged; delete it and remove the entry by hand if needed")
    return manifest


def read_text(path):
    """The file's text, exactly as stored: line endings and a BOM are kept."""
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise Refusal(f"{path} is not UTF-8 text; leaving it alone")


def parse(text, path):
    try:
        return jsonc.parse(text)
    except jsonc.JsoncError as e:
        raise Refusal(f"cannot read {path}: {e}. Fix the file, then run this again.")


def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


def missing_dirs(path):
    """The folders above path that do not exist yet, outermost first."""
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(str(parent))
        parent = parent.parent
    return list(reversed(missing))


def remove_dirs(dirs):
    """Removes folders this project created, innermost first, if they are empty."""
    for directory in reversed(dirs or []):
        path = Path(directory)
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()


def write_file(path, text, expected_before):
    """Replaces path's contents, refusing if it changed since it was read.

    A symlinked config, as dotfile managers make, is written through: the
    file it points to changes and the link stays. A file with several hard
    links is written in place, so every link sees the change. Otherwise the
    new text goes to a temporary file that replaces the old one in one step,
    with the same permissions.
    """
    target = path.resolve() if path.is_symlink() else path
    if target.exists():
        if read_text(target) != expected_before:
            raise Refusal(f"{path} changed while this ran; run it again")
        if not os.access(target, os.W_OK):
            raise Refusal(f"{path} is read-only; leaving it alone")
    data = text.encode("utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_nlink > 1:
        with open(target, "r+b") as f:
            f.write(data)
            f.truncate()
            f.flush()
            os.fsync(f.fileno())
        return
    mode = target.stat().st_mode & 0o7777 if target.exists() else 0o644
    fd, temp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp, mode)
        os.replace(temp, target)
    except BaseException:
        if os.path.exists(temp):
            os.unlink(temp)
        raise


BACKUPS_KEPT = 10


def back_up(agent, path):
    """Copies path into the backups folder, keeping the newest BACKUPS_KEPT per agent."""
    folder = state_dir() / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    target = folder / f"{agent}-{stamp}-{path.name}"
    shutil.copy2(path, target)
    for old in sorted(folder.glob(f"{agent}-*"))[:-BACKUPS_KEPT]:
        old.unlink()
    return target


def providers_of(root, path):
    try:
        member = root.get("providers")
    except jsonc.JsoncError as e:
        raise Refusal(f"cannot read {path}: {e}")
    if member is None:
        return None
    if not isinstance(member.value, jsonc.Obj):
        raise Refusal(f'"providers" in {path} is not an object')
    return member.value


def our_member(providers, path):
    try:
        member = providers.get(PROVIDER_ID) if providers else None
    except jsonc.JsoncError as e:
        raise Refusal(f"cannot read {path}: {e}")
    return member


def ours(entry):
    return isinstance(entry, dict) and entry.get("name") == MARKER


def without_ours(plain):
    """The parsed file as it would be without this project's entry."""
    plain = json.loads(json.dumps(plain))
    providers = plain.get("providers")
    if isinstance(providers, dict):
        providers.pop(PROVIDER_ID, None)
    return plain


# ---------------------------------------------------------------- JSON agents


def install_json(agent, s):
    entry = opencode_provider(s) if agent == "opencode" else pi_provider(s)
    if load_manifest(agent) is not None or has_our_entry(config_file(agent)):
        uninstall_json(agent, quiet=True)
    path = config_file(agent)

    created = not present(path)
    original = new_file_text(agent) if created else read_text(path)
    root = parse(original, path)
    providers = providers_of(root, path)
    if our_member(providers, path) is not None:
        raise Refusal(
            f'{path} already has a provider "{PROVIDER_ID}" that {MARKER} did not add. '
            "Rename or remove it, then run this again."
        )
    if providers is None:
        text, inserted = jsonc.insert_first(original, root, "providers", {PROVIDER_ID: entry})
        added_providers = True
    else:
        text, inserted = jsonc.insert_first(original, providers, PROVIDER_ID, entry)
        added_providers = False

    expected = json.loads(json.dumps(jsonc.plain(root)))
    expected.setdefault("providers", {})[PROVIDER_ID] = entry
    if jsonc.plain(parse(text, path)) != expected:
        raise Refusal(f"internal error: the edited {path} would not hold exactly the old settings plus the new entry")

    backup = None if created else back_up(agent, path)
    created_dirs = missing_dirs(path) if created else []
    write_file(path, text, None if created else original)
    manifest = {
        "file": str(path),
        "created": created,
        "created_dirs": created_dirs,
        "new_file_text": new_file_text(agent) if created else None,
        "added_providers": added_providers,
        "inserted": inserted,
        "backup": str(backup) if backup else None,
    }
    manifest_path(agent).write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"{agent}: added provider {PROVIDER_ID} to {path}" + (f" (backup: {backup})" if backup else " (new file)"))


def has_our_entry(path):
    if not present(path):
        return False
    root = parse(read_text(path), path)
    member = our_member(providers_of(root, path), path)
    return member is not None and ours(jsonc.plain(member.value))


def uninstall_json(agent, quiet=False):
    manifest = load_manifest(agent)
    # Prefer the file the record names; if it is gone, for example renamed
    # from opencode.json to opencode.jsonc, look where the agent reads now.
    path = Path(manifest["file"]) if manifest and present(Path(manifest["file"])) else config_file(agent)
    if not present(path):
        if manifest is not None:
            manifest_path(agent).unlink()
        if not quiet:
            print(f"{agent}: not installed ({path} does not exist)")
        return

    text = read_text(path)
    root = parse(text, path)
    providers = providers_of(root, path)
    member = our_member(providers, path)
    if member is None:
        if manifest is not None:
            manifest_path(agent).unlink()
        if not quiet:
            print(f"{agent}: not installed (no provider {PROVIDER_ID} in {path})")
        return
    if not ours(jsonc.plain(member.value)):
        raise Refusal(f'the provider "{PROVIDER_ID}" in {path} was not added by {MARKER}; leaving it alone')

    expected = without_ours(jsonc.plain(root))
    # The record says whether the install added "providers" itself; that holds
    # even if the file was renamed since.
    drop_providers = bool(manifest and manifest.get("added_providers"))
    if drop_providers and expected.get("providers") == {}:
        del expected["providers"]
    else:
        drop_providers = False

    # First choice: take out exactly the text that was added, which restores
    # the file byte for byte. If that text was edited, or a later edit made
    # the result invalid, remove the entry by its position instead.
    result = None
    if manifest and manifest["file"] == str(path):
        candidate = jsonc.remove_inserted(text, manifest["inserted"])
        if candidate is not None:
            try:
                if jsonc.plain(jsonc.parse(candidate)) == expected:
                    result = candidate
            except jsonc.JsoncError:
                pass  # falls through to removal by position, which is checked below
    if result is None:
        result = jsonc.remove(text, providers, PROVIDER_ID)
        if drop_providers:
            reparsed = parse(result, path)
            result = jsonc.remove(result, reparsed, "providers")
    if jsonc.plain(parse(result, path)) != expected:
        raise Refusal(f"internal error: removing the entry from {path} would change other settings; leaving it alone")

    if manifest and manifest.get("created") and result == manifest.get("new_file_text") and not path.is_symlink():
        if read_text(path) != text:
            raise Refusal(f"{path} changed while this ran; run it again")
        path.unlink()
        remove_dirs(manifest.get("created_dirs"))
        done = f"deleted {path}, which it had created"
    else:
        back_up(agent, path)
        write_file(path, result, text)
        done = f"removed provider {PROVIDER_ID} from {path}"
        remaining = providers_of(parse(result, path), path)
        if manifest is None and remaining is not None and not remaining.members:
            done += '; it left an empty "providers" object, which you can delete'
    if manifest is not None:
        manifest_path(agent).unlink()
    if not quiet:
        print(f"{agent}: {done}")


# ---------------------------------------------------------------- Codex


def install_codex(s):
    path = config_file("codex")
    text = codex_profile(s)
    before = None
    if present(path):
        before = read_text(path)
        if not before.startswith(CODEX_HEADER):
            raise Refusal(f"{path} exists and was not added by {MARKER}; leaving it alone")
    previous = load_manifest("codex")
    created_dirs = previous.get("created_dirs", []) if previous else missing_dirs(path)
    note = ""
    if before is not None and before != text:
        note = f" (the previous version, with any edits, is in {back_up('codex', path)})"
    write_file(path, text, before)
    manifest_path("codex").write_text(json.dumps({"file": str(path), "created_dirs": created_dirs}, indent=2) + "\n")
    print(f"codex: wrote {path}; start Codex with: codex -p {PROVIDER_ID}{note}")


def uninstall_codex():
    path = config_file("codex")
    record = manifest_path("codex")
    manifest = load_manifest("codex")
    if not present(path):
        if manifest is not None:
            record.unlink()
        print(f"codex: not installed ({path} does not exist)")
        return
    if not read_text(path).startswith(CODEX_HEADER):
        raise Refusal(f"{path} was not added by {MARKER}; leaving it alone")
    backup = back_up("codex", path)
    path.unlink()
    if manifest is not None:
        remove_dirs(manifest.get("created_dirs"))
        record.unlink()
    print(f"codex: deleted {path} (a copy is in {backup})")


# ---------------------------------------------------------------- commands


def status(agent):
    path = config_file(agent)
    if agent == "codex":
        if not present(path):
            return f"codex: not installed ({path})"
        if read_text(path).startswith(CODEX_HEADER):
            return f"codex: installed in {path}"
        return f"codex: {path} exists but was not added by {MARKER}"
    if has_our_entry(path):
        return f"{agent}: installed in {path}"
    return f"{agent}: not installed ({path})"


def run(command, agent):
    if command == "status":
        print(status(agent))
    elif command == "install":
        install_codex(settings()) if agent == "codex" else install_json(agent, settings())
    else:
        uninstall_codex() if agent == "codex" else uninstall_json(agent)


def main(argv):
    if len(argv) < 2 or argv[0] not in ("install", "uninstall", "status", "config"):
        raise SystemExit(__doc__)
    command, agents = argv[0], argv[1:]
    for agent in agents:
        if agent not in AGENTS:
            raise Refusal(f"unknown agent {agent!r}; expected one of {', '.join(AGENTS)}")
    if command == "config":
        print(json.dumps(launcher_config(agents[0], settings()), indent=2))
        return
    # One run at a time: two at once could each read a file before the other wrote it.
    lock = open(state_dir() / ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise Refusal("another bin/integrate is running; wait for it to finish")
    failed = []
    for agent in agents:
        try:
            run(command, agent)
        except Refusal as e:
            print(f"error: {agent}: {e}", file=sys.stderr)
            failed.append(agent)
        except (OSError, ValueError) as e:
            print(f"error: {agent}: {e}", file=sys.stderr)
            failed.append(agent)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Refusal as e:
        raise SystemExit(f"error: {e}")
