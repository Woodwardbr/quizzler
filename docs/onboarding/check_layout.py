#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Layout gate: render guides in a headless browser and report broken layouts.

    python docs/onboarding/check_layout.py                  # tracked guides
    python docs/onboarding/check_layout.py --guide G --require-browser

Each guide is served by serve.py's GuideApp on loopback and loaded with
``?layoutcheck=1`` at several viewport sizes. app/layout_check.js measures
clipped or overlapping diagram labels, tables without a scroll wrapper,
horizontal page overflow, clipped content and unusable controls.

The browser is headless Firefox, driven over Marionette with the standard
library only (GitHub's Ubuntu runners ship Firefox). Without a browser the gate is
skipped (exit 0) unless ``--require-browser`` is given (exit 2).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import guidelib as gl
import serve

SIZES = ((1280, 900), (820, 1000), (375, 812))


class BrowserUnavailable(Exception):
    pass


def find_firefox() -> str:
    path = shutil.which("firefox")
    if not path:
        raise BrowserUnavailable("Firefox not found on PATH")
    return path


class Firefox:
    """Minimal Marionette client (length-prefixed JSON over TCP)."""

    def __init__(self, binary: str):
        self.binary = binary
        # Snap-packaged Firefox cannot see the host /tmp; use a dir it can read.
        base = None
        if "/snap/" in os.path.realpath(binary) or Path("/snap/bin/firefox").exists() and binary.startswith("/usr/bin"):
            base = Path.home() / "snap" / "firefox" / "common"
            base.mkdir(parents=True, exist_ok=True)
        self.profile = Path(tempfile.mkdtemp(prefix="quizzler-ff-", dir=base))
        self.port = _free_port()
        prefs = {
            "marionette.port": self.port, "browser.shell.checkDefaultBrowser": False,
            "datareporting.policy.dataSubmissionEnabled": False, "toolkit.telemetry.reportingpolicy.firstRun": False,
            "browser.startup.homepage_override.mstone": "ignore", "app.update.disabledForTesting": True,
            "browser.aboutwelcome.enabled": False, "network.proxy.type": 0,
        }
        (self.profile / "user.js").write_text(
            "".join(f"user_pref({json.dumps(k)}, {json.dumps(v)});\n" for k, v in prefs.items()))
        self.proc = subprocess.Popen(
            [binary, "--headless", "--marionette", "--no-remote", "--profile", str(self.profile)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.sock = self._connect()
        self.msg_id = 0
        self._recv()  # handshake
        self._cmd("WebDriver:NewSession", {"capabilities": {}})

    def _connect(self) -> socket.socket:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise BrowserUnavailable("Firefox exited during startup")
            try:
                return socket.create_connection(("127.0.0.1", self.port), timeout=60)
            except OSError:
                time.sleep(0.25)
        raise BrowserUnavailable("timed out waiting for Firefox Marionette")

    def _recv(self):
        length = b""
        while not length.endswith(b":"):
            chunk = self.sock.recv(1)
            if not chunk:
                raise RuntimeError("Marionette connection closed")
            length += chunk
        size = int(length[:-1])
        data = b""
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise RuntimeError("Marionette connection closed")
            data += chunk
        return json.loads(data)

    def _cmd(self, name: str, params: dict):
        self.msg_id += 1
        body = json.dumps([0, self.msg_id, name, params]).encode()
        self.sock.sendall(str(len(body)).encode() + b":" + body)
        while True:
            message = self._recv()
            if isinstance(message, list) and message[0] == 1 and message[1] == self.msg_id:
                if message[2]:
                    raise RuntimeError(f"{name}: {message[2]}")
                return message[3]

    def measure(self, url: str, width: int, height: int) -> dict:
        # Firefox will not make a window narrower than ~490px, so narrow sizes
        # are measured inside a same-origin iframe of exactly that width.
        framed = width < 600
        self._cmd("WebDriver:SetWindowRect", {"width": max(width, 800) if framed else width,
                                              "height": height + 100 if framed else height})
        # Scripts cannot run in the privileged start page, so load the page
        # first, then correct for window chrome.
        self._cmd("WebDriver:Navigate", {"url": url.replace("?layoutcheck=1", "")})
        if framed:
            self._script(
                "const f = document.createElement('iframe');"
                "f.id = 'qz-layout-frame'; f.src = arguments[0];"
                "f.style.cssText = `position:fixed;top:0;left:0;border:0;width:${arguments[1]}px;"
                "height:${arguments[2]}px`;"
                "document.body.replaceChildren(f);", url, width, height)
            probe = ("const f = document.getElementById('qz-layout-frame');"
                     "const n = f && f.contentDocument && f.contentDocument.getElementById('layout-check-result');"
                     "return n ? n.textContent : null;")
        else:
            inner = self._script("return [window.innerWidth, window.innerHeight]")
            if inner and inner[0] != width:
                self._cmd("WebDriver:SetWindowRect", {"width": 2 * width - inner[0], "height": 2 * height - inner[1]})
            self._cmd("WebDriver:Navigate", {"url": url})
            probe = "const n = document.getElementById('layout-check-result'); return n ? n.textContent : null;"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            value = self._script(probe)
            if value:
                return json.loads(value)
            time.sleep(0.2)
        raise RuntimeError("layout check did not finish in Firefox")

    def _script(self, script: str, *args):
        return self._cmd("WebDriver:ExecuteScript", {"script": script, "args": list(args)})["value"]

    def close(self):
        try:
            self._cmd("Marionette:Quit", {"flags": ["eForceQuit"]})
        except Exception:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(guides: list[Path], repo_root: Path, sizes=SIZES) -> dict:
    browser = Firefox(find_firefox())
    results = {"browser": "firefox", "guides": {}}
    try:
        for guide in guides:
            app = serve.GuideApp(repo_root, guide, allow_self_framing=True)
            server = serve.make_server(app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                per_size = {}
                for width, height in sizes:
                    per_size[f"{width}x{height}"] = browser.measure(app.url + "?layoutcheck=1", width, height)
                results["guides"][str(guide)] = per_size
            finally:
                server.shutdown()
                server.server_close()
    finally:
        browser.close()
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--guide", type=Path, action="append")
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--require-browser", action="store_true", help="fail instead of skipping without a browser")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    import check
    guides = [g.resolve() for g in (args.guide or check.default_guides())]
    try:
        results = run(guides, args.repo_root or gl.find_repo_root())
    except BrowserUnavailable as exc:
        print(f"SKIP: {exc}")
        return 2 if args.require_browser else 0

    failures = 0
    if args.json:
        print(json.dumps(results, indent=2))
    for guide, sizes in results["guides"].items():
        for size, data in sizes.items():
            issues = data["issues"]
            failures += len(issues)
            if not args.json:
                label = f"{Path(guide).name} @ {size} (viewport {data['viewport']}px)"
                print(f"{'FAIL' if issues else 'ok  '} {label}")
                for issue in issues:
                    print(f"     {issue['kind']}: {issue['detail']}  [{issue['where']}]")
    if not args.json:
        print(f"{'FAILED' if failures else 'OK'}: {len(results['guides'])} guide(s) in {results['browser']}, {failures} issue(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
