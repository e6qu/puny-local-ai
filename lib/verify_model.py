"""Checks a downloaded model folder against the files Hugging Face lists for
the pinned commit.

Usage: python3 -I verify_model.py REPO REVISION MODEL_DIR

Large files are compared by SHA-256 (the Git LFS object id), small ones by
their git blob hash. Every listed file must be present with the right hash.
Writes the verified list, one "hash  path" line per file, to
MODEL_DIR/.verified and exits 0. Exits 1 when a file is missing or differs,
and 2 when the file list cannot be fetched, so callers can tell a bad
download from a network problem.
Needs only the standard library.
"""

import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# HF_ENDPOINT is the variable huggingface_hub itself reads for the server
# address; the tests point it at a local fake.
ENDPOINT = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
API = ENDPOINT + "/api/models/{repo}/tree/{revision}?recursive=true"


def listed_files(repo, revision):
    url = API.format(repo=repo, revision=urllib.parse.quote(revision))
    files = []
    while url:
        with urllib.request.urlopen(url, timeout=60) as response:
            files += [e for e in json.load(response) if e["type"] == "file"]
            # Long listings continue at the rel="next" URL in the Link header.
            url = None
            for part in response.headers.get("Link", "").split(","):
                target, _, params = part.partition(";")
                if 'rel="next"' in params:
                    url = target.strip().strip("<>")
    return files


def file_hash(path, entry):
    if "lfs" in entry:
        digest = hashlib.sha256()
        expected = entry["lfs"]["oid"]
    else:
        digest = hashlib.sha1(b"blob %d\0" % path.stat().st_size)
        expected = entry["oid"]
    with path.open("rb") as f:
        while chunk := f.read(1 << 24):
            digest.update(chunk)
    return digest.hexdigest(), expected


def cannot_list(message):
    print(f"error: {message}", file=sys.stderr)
    sys.exit(2)


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    repo, revision, model_dir = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    try:
        entries = listed_files(repo, revision)
    except urllib.error.HTTPError as e:
        cannot_list(f"Hugging Face answered HTTP {e.code} for the files of {repo} at {revision}")
    except (OSError, ValueError) as e:
        cannot_list(f"cannot fetch the file list of {repo} at {revision} from Hugging Face: {e}")
    if not entries:
        cannot_list(f"Hugging Face lists no files for {repo} at {revision}")

    problems, manifest = [], []
    for entry in entries:
        path = model_dir / entry["path"]
        if not path.is_file():
            problems.append(f"missing: {entry['path']}")
            continue
        actual, expected = file_hash(path, entry)
        if actual != expected:
            problems.append(f"hash mismatch: {entry['path']} is {actual}, expected {expected}")
        else:
            manifest.append(f"{expected}  {entry['path']}")

    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        sys.exit(1)
    # Written to a temporary name first: a half-written .verified must never
    # count as verified.
    partial = model_dir / ".verified.partial"
    partial.write_text(
        f"# {repo}@{revision}: SHA-256 for LFS files, git blob SHA-1 otherwise\n"
        + "\n".join(manifest) + "\n"
    )
    os.replace(partial, model_dir / ".verified")
    print(f"Verified {len(manifest)} files of {repo} at {revision}.")


if __name__ == "__main__":
    main()
