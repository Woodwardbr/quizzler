# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures: a throwaway repository with its own docs/onboarding copy."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

ONBOARD = Path(__file__).resolve().parents[1] / "docs" / "onboarding"
sys.path.insert(0, str(ONBOARD))

GUIDE_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Fixture</title>
<link rel="stylesheet" href="{prefix}app/guide.css">
<script src="{prefix}app/guide.js" defer></script></head>
<body>
<main data-guide data-mode="{mode}" data-guide-id="{guide_id}" data-version="1"{extra}>
<header><h1>Fixture guide</h1></header>
{body}
</main></body></html>
"""

OVERVIEW = """
<section class="module" data-module="overview" data-overview data-title="Overview">
  <h2>Overview</h2>
  <table><thead><tr><th>Module</th><th>What</th></tr></thead><tbody>
    <tr data-plan="m1" data-importance="high" data-complexity="low" data-intensity="standard"><td>Module one</td><td>x</td></tr>
  </tbody></table>
</section>
"""

DEFAULT_BODY = OVERVIEW + """
<section class="module" data-module="m1" data-title="Module one">
  <h2>Module one</h2>
  <p><code data-src="src/app.py" data-ref="def handler(request):">handler</code>
     <code data-src="src/app.py" data-sym="Service.run">run</code></p>
  <div class="exercise" data-exercise="hello"></div>
  <div data-q="q1" data-answer="b"><p class="prompt">Pick b.</p>
    <ol class="choices"><li data-choice="a">a</li><li data-choice="b">b</li></ol></div>
  <div data-q="q2" data-answer="a"><p class="prompt">Pick a.</p>
    <ol class="choices"><li data-choice="a">a</li><li data-choice="b">b</li></ol></div>
</section>
"""

APP_PY = textwrap.dedent('''\
    """Fixture application."""


    def handler(request):
        return request


    class Service:
        def run(self):
            return "ok"
    ''')

HELLO = textwrap.dedent('''\
    """Hello exercise.

    PREDICT: What does it print?
    """
    print("hello", 6 * 7)
    ''')


def git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


class Fixture:
    """A temp repo at ``root`` with docs/onboarding (engine copy) inside it."""

    def __init__(self, *, git_init: bool = False):
        self.tmp = Path(tempfile.mkdtemp(prefix="quizzler-test-"))
        self.root = self.tmp / "repo"
        self.onboard = self.root / "docs" / "onboarding"
        (self.onboard / "app").mkdir(parents=True)
        for name in ("guide.js", "guide.css", "layout_check.js"):
            shutil.copy(ONBOARD / "app" / name, self.onboard / "app" / name)
        (self.onboard / "exercises").mkdir()
        (self.onboard / "expected").mkdir()
        self.write("src/app.py", APP_PY)
        self.write("docs/onboarding/exercises/hello.py", HELLO)
        self.write("docs/onboarding/expected/hello.out", "hello 42\n")
        self.write_guide("guide.html")
        shutil.copy(ONBOARD.parents[1] / ".gitignore", self.root / ".gitignore")
        if git_init:
            git(self.root, "init", "-q", "-b", "main")
            git(self.root, "add", "-A")
            git(self.root, "commit", "-q", "-m", "base")

    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def write_guide(self, rel: str, body: str = DEFAULT_BODY, mode: str = "onboard",
                    guide_id: str = "fixture", extra: str = "") -> Path:
        depth = rel.count("/")
        text = GUIDE_TEMPLATE.format(prefix="../" * depth, mode=mode, guide_id=guide_id, extra=extra, body=body)
        return self.write(f"docs/onboarding/{rel}", text)

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
