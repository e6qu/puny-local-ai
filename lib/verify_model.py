"""Checks a downloaded model folder against the files its source lists for
the pinned commit.

Usage: python3 -I verify_model.py MODEL REVISION MODEL_DIR

MODEL is a Hugging Face repository or modelscope:REPO (see lib/sources.py).
Files are compared by SHA-256, or by their git blob hash for the small files
Hugging Face stores outside LFS. Every listed file must be present with the
right hash. Writes the verified list, one "hash  path" line per file, to
MODEL_DIR/.verified and exits 0. Exits 1 when a file is missing or differs,
and 2 when the file list cannot be fetched, so callers can tell a bad
download from a network problem.
Needs only the standard library.
"""

import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sources  # noqa: E402


def file_hash(path, entry):
    if "sha256" in entry:
        digest = hashlib.sha256()
        expected = entry["sha256"]
    else:
        digest = hashlib.sha1(b"blob %d\0" % path.stat().st_size)
        expected = entry["git_sha1"]
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
    name, revision, model_dir = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    try:
        entries = sources.listed_files(name, revision)
    except sources.SourceError as e:
        cannot_list(f"cannot fetch the file list of {name} at {revision}: {e}")
    if not entries:
        cannot_list(f"the source lists no files for {name} at {revision}")

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
        f"# {name}@{revision}: SHA-256, or the git blob SHA-1 for small Hugging Face files\n"
        + "\n".join(manifest) + "\n"
    )
    os.replace(partial, model_dir / ".verified")
    print(f"Verified {len(manifest)} files of {name} at {revision}.")


if __name__ == "__main__":
    main()
