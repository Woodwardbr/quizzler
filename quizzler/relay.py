#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Hand button presses in the guide to the Claude session.

    relay.py wait   [--guide G | --review N] [--timeout SECONDS]
    relay.py done   ID... | --all  [--guide G | --review N]
    relay.py list   [--guide G | --review N]

The page queues requests through the server, for example "write module
X" when the reader opens a module that is only an outline. Claude runs
``wait`` as a background command. It exits as soon as a request is
pending, which wakes the session. Claude handles the requests, marks them
``done`` and starts ``wait`` again. Exit codes for ``wait``: 0 requests
pending, 3 timed out with nothing to do.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import guidelib as gl


def queue_path(guide: Path | None, review: int | None) -> Path:
    if review is not None:
        guide = gl.LOCAL_DIR / "reviews" / f"pr-{review}" / "guide.html"
    elif guide is None:
        local = gl.LOCAL_DIR / "guide.html"
        guide = local if local.is_file() else gl.ONBOARD_DIR / "guide.html"
    guide = guide.resolve()
    return gl.requests_path_for(guide, gl.load_guide(guide).guide_id)


def pending(path: Path) -> list[dict]:
    return [r for r in gl.update_requests(path) if r.get("status") == "pending"]


def claim(path: Path) -> list[dict]:
    """Move pending requests to "working" (the page shows Claude picked them up)."""
    claimed: list[dict] = []

    def change(items):
        for item in items:
            if item.get("status") == "pending":
                item["status"] = "working"
                claimed.append(dict(item))

    gl.update_requests(path, change)
    return claimed


def heartbeat(path: Path) -> None:
    beat = gl.listener_path(path)
    beat.parent.mkdir(parents=True, exist_ok=True)
    beat.touch()


def wait(path: Path, timeout: float | None, interval: float = 1.0) -> list[dict]:
    """Block until requests are pending, then claim and return them.

    Requests left "working" by a session that died are handed out again.
    """
    deadline = None if timeout is None else time.monotonic() + timeout
    stale = [r for r in gl.update_requests(path) if r.get("status") == "working"]
    if stale:
        heartbeat(path)
        return stale
    while True:
        heartbeat(path)
        if pending(path):
            return claim(path)
        if deadline is not None and time.monotonic() >= deadline:
            return []
        time.sleep(interval)


def mark_done(path: Path, ids: list[str] | None) -> list[str]:
    marked: list[str] = []

    def change(items):
        for item in items:
            if item.get("status") in ("pending", "working") and (ids is None or item.get("id") in ids):
                item["status"] = "done"
                marked.append(item["id"])

    gl.update_requests(path, change)
    return marked


def describe(item: dict) -> str:
    if item["action"] == "write-modules":
        listed = ", ".join(f"{m} ({i})" for m, i in (item.get("modules") or {}).items())
        return f"{item['id']}: write these modules, in order: {listed}"
    if item["action"] == "help-module":
        return f"{item['id']}: rewrite module {item['module']!r} to help with: {item.get('message', '')!r}"
    return f"{item['id']}: " + {
        "next-module": "write the next module",
        "final-review": "write the final review for this round",
        "new-round": "go back over missed content: start a refresher round from the refresh plan",
        "summary": "the reader calls it a day: write the final report",
    }.get(item["action"], item["action"])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--guide", type=Path)
    parser.add_argument("--review", type=int, metavar="N")
    sub = parser.add_subparsers(dest="command", required=True)
    w = sub.add_parser("wait")
    w.add_argument("--timeout", type=float, default=None, help="seconds (default: wait indefinitely)")
    d = sub.add_parser("done")
    d.add_argument("ids", nargs="*")
    d.add_argument("--all", action="store_true")
    sub.add_parser("list")
    args = parser.parse_args(argv)

    path = queue_path(args.guide, args.review)
    if args.command == "wait":
        found = wait(path, args.timeout)
        if not found:
            print("No requests yet. Start `relay.py wait` again to keep listening.")
            return 3
        print("The reader asked for:")
        for item in found:
            print(f"  - {describe(item)}")
        print("Handle these, run `relay.py done <ids>`, then start `relay.py wait` again in the background.")
        print(json.dumps(found))
    elif args.command == "done":
        if not args.ids and not args.all:
            raise SystemExit("give request ids or --all")
        marked = mark_done(path, None if args.all else args.ids)
        print(f"Marked done: {', '.join(marked) or 'nothing'}")
    elif args.command == "list":
        items = gl.update_requests(path)
        for item in items:
            print(f"{item['status']:<8} {describe(item)}")
        if not items:
            print("No requests.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
