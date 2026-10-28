"""The docs match the code: config examples, script lists and links."""

import json
import os
import re
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request

from helpers import REPO

sys.path.insert(0, str(REPO / "lib"))
import integrate  # noqa: E402

DOCS = ["README.md", "CONTRIBUTING.md", "THIRD_PARTY.md", "docs/models.md", "docs/compatibility.md",
        "docs/agents.md", "docs/how-it-works.md", "docs/alternatives.md"]
README = (REPO / "README.md").read_text()
AGENTS_DOC = (REPO / "docs" / "agents.md").read_text()


def blocks(text, language):
    return re.findall(rf"```{language}\n(.*?)```", text, re.S)


def anchors(path):
    found = set()
    for heading in re.findall(r"(?m)^#+ (.*)$", path.read_text()):
        found.add(re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-"))
    return found


def links():
    for name in DOCS:
        path = REPO / name
        for link in re.findall(r"\]\(([^)\s]+)\)", path.read_text()):
            yield path, link


class ReadmeTest(unittest.TestCase):
    def settings(self):
        return {"base_url": "http://127.0.0.1:8081/v1", "context": 262144, "max_output": 8192}

    def test_config_examples_match_what_integrate_writes(self):
        examples = [json.loads(b) for b in blocks(AGENTS_DOC, "json")]
        s = self.settings()
        self.assertIn({"providers": {"puny": integrate.opencode_provider(s)}}, examples)
        self.assertIn({"providers": {"puny": integrate.pi_provider(s)}}, examples)
        self.assertIn(integrate.codex_profile(s), blocks(AGENTS_DOC, "toml"))

    def test_every_doc_is_linked_from_the_readme(self):
        for path in sorted((REPO / "docs").glob("*.md")):
            with self.subTest(path.name):
                self.assertIn(f"](docs/{path.name})", README)
                self.assertIn(f"docs/{path.name}", DOCS)

    def test_every_command_is_documented(self):
        for script in sorted((REPO / "bin").iterdir()):
            with self.subTest(script.name):
                self.assertIn(f"`./bin/{script.name}", README)

    def test_commands_in_the_docs_start_with_dot_slash(self):
        for name in DOCS:
            text = (REPO / name).read_text()
            for match in re.finditer(r"(?<![./\w~-])(?:bin|tests)/(setup|run|integrate|serve-qwen|smoke-test|[a-z]+-qwen|install-agents|licenses|bench-agents|check-[a-z-]+|models|web-ui)\b", text):
                with self.subTest(file=name, command=match.group()):
                    self.fail(f"{name} shows {match.group()} without ./")

    def test_every_setting_is_documented(self):
        settings = set()
        for path in list((REPO / "bin").iterdir()) + list((REPO / "lib").glob("*.sh")):
            settings |= set(re.findall(r'"\$\{((?:QWEN|MLX|BENCH|SMOKE|CODEX|WEB_UI)_[A-Z_]*)[:-]', path.read_text()))
        self.assertTrue(settings)
        documented = (REPO / "README.md").read_text() + (REPO / "docs" / "models.md").read_text()
        for setting in sorted(settings):
            with self.subTest(setting):
                self.assertTrue(f"`{setting}`" in documented, f"{setting} is not documented in README.md or docs/models.md")


class FileModesTest(unittest.TestCase):
    """Commands are executable; sourced libraries and Python modules are not."""

    def tracked_modes(self):
        out = subprocess.run(["git", "ls-files", "-s"], cwd=REPO, capture_output=True, text=True, check=True).stdout
        return {line.split("\t")[1]: line.split()[0] for line in out.splitlines()}

    def test_modes(self):
        modes = self.tracked_modes()
        if not modes:
            self.skipTest("not a git checkout")
        for path, mode in modes.items():
            with self.subTest(path):
                runnable = path.startswith("bin/") or path in ("tests/run",) or path.endswith("/check")
                self.assertEqual(mode, "100755" if runnable else "100644")
                if runnable:
                    self.assertTrue((REPO / path).read_text().startswith("#!/usr/bin/env bash\n"))


class LinksTest(unittest.TestCase):
    def test_local_links_and_anchors_resolve(self):
        for path, link in links():
            if link.startswith("http"):
                continue
            with self.subTest(file=path.name, link=link):
                target, _, fragment = link.partition("#")
                resolved = (path.parent / target).resolve() if target else path
                self.assertTrue(resolved.exists())
                if fragment and resolved.suffix == ".md":
                    self.assertIn(fragment, anchors(resolved))

    @unittest.skipUnless(os.environ.get("PUNY_LIVE_TESTS") == "1", "live test: run tests/run --live")
    def test_external_links_answer(self):
        urls = sorted({link for _, link in links() if link.startswith("http")})
        self.assertTrue(urls)
        for url in urls:
            with self.subTest(url):
                request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                try:
                    response = urllib.request.urlopen(request, timeout=30)
                except urllib.error.HTTPError as e:
                    e.close()
                    # Hacker News, for one, answers 503 to many requests in a
                    # row; one slower retry tells that from a dead link.
                    if e.code not in (429, 503):
                        raise
                    time.sleep(10)
                    response = urllib.request.urlopen(request, timeout=30)
                with response:
                    self.assertEqual(response.status, 200)


if __name__ == "__main__":
    unittest.main()


class CommandsTest(unittest.TestCase):
    """Every command works from anywhere, including through a symlink."""

    def test_commands_work_through_a_symlink(self):
        import tempfile
        from helpers import Sandbox
        box = Sandbox()
        self.addCleanup(box.cleanup)
        with tempfile.TemporaryDirectory() as folder:
            link = f"{folder}/integrate"
            os.symlink(box.root / "bin" / "integrate", link)
            result = subprocess.run([link, "status", "pi"], env=box.env(), capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pi: not installed", result.stdout)

    def test_every_command_stops_on_a_missing_library(self):
        for script in sorted((REPO / "bin").iterdir()):
            with self.subTest(script.name):
                text = script.read_text()
                self.assertTrue(text.startswith("#!/usr/bin/env bash\n"))
                self.assertLess(text.index("set -euo pipefail"), text.index("source "))

    def test_runner_arguments(self):
        result = subprocess.run([str(REPO / "tests" / "run"), "a", "b"], capture_output=True, text=True, timeout=60)
        self.assertIn("usage:", result.stderr)
        result = subprocess.run([str(REPO / "tests" / "run"), "--bogus"], capture_output=True, text=True, timeout=60)
        self.assertIn("usage:", result.stderr)
