"""Where models come from: Hugging Face and ModelScope.

A model is named REPO, as Hugging Face names it (mlx-community/Qwen3.5-4B-4bit),
or modelscope:REPO for the ModelScope copy. Both pin a model to a 40-character
commit hash and list a hash for every file at that commit, so a download from
either is checked the same way.

  - Hugging Face lists SHA-256 for large (LFS) files and the git blob hash
    for small ones. Search filters on the "mlx" tag.
  - ModelScope lists SHA-256 for every file, the same values as Hugging Face
    for the mlx-community copies. Its search has no MLX filter, so results
    are kept only when tagged library:mlx or named like an MLX conversion.

Needs only the standard library. HF_ENDPOINT and MODELSCOPE_ENDPOINT move the
servers; the tests point them at a local fake.
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

HF = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
MODELSCOPE = os.environ.get("MODELSCOPE_ENDPOINT", "https://modelscope.cn").rstrip("/")
PREFIX = "modelscope:"
SOURCES = ("huggingface", "modelscope")
TIMEOUT_SECONDS = 60
COMMIT = re.compile(r"^[0-9a-f]{40}$")
REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")


class SourceError(Exception):
    """The source could not be asked, or did not answer as expected."""


def split(name):
    """(source, repo) for a model name such as modelscope:owner/model."""
    source, repo = ("modelscope", name[len(PREFIX):]) if name.startswith(PREFIX) else ("huggingface", name)
    if not REPO.match(repo):
        raise SourceError(f"{name!r} is not a model name; expected OWNER/MODEL or {PREFIX}OWNER/MODEL")
    return source, repo


def get_json(url, with_headers=False):
    request = urllib.request.Request(url, headers={"User-Agent": "puny-local-ai"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            data = json.load(response)
            return (data, response.headers) if with_headers else data
    except urllib.error.HTTPError as e:
        e.close()
        raise SourceError(f"{url} answered HTTP {e.code}") from e
    except (OSError, ValueError) as e:
        raise SourceError(f"cannot read {url}: {e}") from e


def modelscope_data(url):
    data = get_json(url)
    if not (isinstance(data, dict) and data.get("Code") == 200 and "Data" in data):
        raise SourceError(f"{url} answered {json.dumps(data)[:300]}")
    return data["Data"]


def quote(repo):
    return urllib.parse.quote(repo, safe="/")


# ------------------------------------------------------------ commits and files


def head_commit(name, ref="main"):
    """The commit that a branch or tag of the model points to now."""
    source, repo = split(name)
    if COMMIT.match(ref):
        return ref
    if source == "huggingface":
        info = get_json(f"{HF}/api/models/{quote(repo)}/revision/{urllib.parse.quote(ref, safe='')}")
        return info["sha"]
    if ref not in ("main", "master"):
        raise SourceError(f"ModelScope models are pinned by commit; give a 40-character commit, not {ref!r}")
    commits = modelscope_data(f"{MODELSCOPE}/api/v1/models/{quote(repo)}/commits").get("Commit") or []
    if not commits or not COMMIT.match(commits[0].get("Id", "")):
        raise SourceError(f"ModelScope lists no commits for {repo}")
    return commits[0]["Id"]


def listed_files(name, revision):
    """Every file of the model at the commit: [{"path", "size", "sha256" or "git_sha1"}]."""
    source, repo = split(name)
    if source == "modelscope":
        url = f"{MODELSCOPE}/api/v1/models/{quote(repo)}/repo/files?Revision={revision}&Recursive=true"
        return [
            {"path": f["Path"], "size": f["Size"], "sha256": f["Sha256"]}
            for f in modelscope_data(url).get("Files") or []
            if f.get("Type") == "blob"
        ]
    url = f"{HF}/api/models/{quote(repo)}/tree/{urllib.parse.quote(revision)}?recursive=true"
    files = []
    while url:
        entries, headers = get_json(url, with_headers=True)
        for e in entries:
            if e["type"] == "file":
                lfs = e.get("lfs")
                files.append({"path": e["path"], "size": e["size"],
                              **({"sha256": lfs["oid"]} if lfs else {"git_sha1": e["oid"]})})
        # Long listings continue at the rel="next" URL in the Link header.
        url = None
        for part in (headers.get("Link") or "").split(","):
            target, _, params = part.partition(";")
            if 'rel="next"' in params:
                url = target.strip().strip("<>")
    return files


def file_url(name, revision, path):
    source, repo = split(name)
    base = MODELSCOPE + "/models" if source == "modelscope" else HF
    return f"{base}/{quote(repo)}/resolve/{revision}/{urllib.parse.quote(path)}"


def page_url(name):
    source, repo = split(name)
    return f"{MODELSCOPE}/models/{repo}" if source == "modelscope" else f"{HF}/{repo}"


# ------------------------------------------------------------ search


def looks_like_mlx(model_id, tags):
    return "library:mlx" in tags or "mlx" in tags or bool(re.search(r"(?i)\bmlx\b|mlx-community/", model_id))


def search(query, source="huggingface", limit=20):
    """MLX models matching query, most downloaded first."""
    if source == "huggingface":
        params = urllib.parse.urlencode([
            ("filter", "mlx"), ("search", query), ("sort", "downloads"), ("limit", str(limit)),
            *[("expand[]", e) for e in ("downloads", "likes", "lastModified", "pipeline_tag", "sha", "tags")],
        ])
        return [
            {
                "name": m["id"], "source": "huggingface", "downloads": m.get("downloads", 0),
                "likes": m.get("likes", 0), "updated": m.get("lastModified", ""), "task": m.get("pipeline_tag") or "",
                "commit": m.get("sha", ""), "size_bytes": None, "url": page_url(m["id"]),
            }
            for m in get_json(f"{HF}/api/models?{params}")
        ]
    if source == "modelscope":
        # Asks for more than limit, because results without MLX weights are dropped.
        params = urllib.parse.urlencode({"search": query, "sort": "downloads", "page_size": str(limit * 3)})
        data = get_json(f"{MODELSCOPE}/openapi/v1/models?{params}")
        if not (isinstance(data, dict) and data.get("success")):
            raise SourceError(f"ModelScope search answered {json.dumps(data)[:300]}")
        results = []
        for m in (data.get("data") or {}).get("models") or []:
            if looks_like_mlx(m["id"], m.get("tags") or []):
                name = PREFIX + m["id"]
                results.append({
                    "name": name, "source": "modelscope", "downloads": m.get("downloads", 0),
                    "likes": m.get("likes", 0), "updated": m.get("last_modified", ""),
                    "task": ", ".join(m.get("tasks") or []), "commit": "", "size_bytes": m.get("file_size"),
                    "url": page_url(name),
                })
        return results[:limit]
    raise SourceError(f"unknown source {source!r}; expected one of {', '.join(SOURCES)}")


# ------------------------------------------------------------ downloads


def download(name, revision, dest, files, progress=print):
    """Downloads files ([{"path", "size"}] from listed_files) of the model at revision into dest.

    Files already present at the listed size are kept, so an interrupted
    download continues where it stopped. Each file is written under a
    .partial name and renamed when complete. lib/verify_model.py checks the
    hashes afterwards.
    """
    for entry in files:
        target = dest / entry["path"]
        if target.is_file() and target.stat().st_size == entry["size"]:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".partial")
        url = file_url(name, revision, entry["path"])
        progress(f"Downloading {entry['path']} ({entry['size'] / 2**30:.2f} GiB)")
        request = urllib.request.Request(url, headers={"User-Agent": "puny-local-ai"})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response, partial.open("wb") as out:
                while chunk := response.read(1 << 20):
                    out.write(chunk)
        except OSError as e:
            raise SourceError(f"downloading {url} failed: {e}") from e
        if partial.stat().st_size != entry["size"]:
            raise SourceError(f"{url} sent {partial.stat().st_size} bytes, but the listing says {entry['size']}")
        os.replace(partial, target)
