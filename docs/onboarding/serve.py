#!/usr/bin/env python3
"""Serve a Quizzler guide on loopback with a per-run access token.

    python docs/onboarding/serve.py                     # shared onboarding guide
    python docs/onboarding/serve.py --guide docs/onboarding/local/guide.html
    python docs/onboarding/serve.py --review 42         # PR review guide

Every route except ``/healthz`` lives under ``/t/<token>/``. The token is
random per process and is only printed to the terminal. Source access goes
through :class:`guidelib.PathPolicy`; exercises run only when registered by
the active guide (see ``guidelib.run_exercise`` for the limits).
"""

from __future__ import annotations

import argparse
import hmac
import json
import secrets
import signal
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import guidelib as gl

HOST = "127.0.0.1"
MAX_BODY = 1_000_000
MAX_SNIPPET_LINES = 400
CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; font-src 'self'; base-uri 'none'; "
       "form-action 'none'; frame-ancestors 'none'")
# Only check_layout.py relaxes this, to measure phone widths in a same-origin iframe.
LAYOUT_CSP = CSP.replace("frame-ancestors 'none'", "frame-ancestors 'self'; frame-src 'self'")


class HTTPError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status


class GuideApp:
    """Everything the request handler needs; one instance per server run."""

    def __init__(self, repo_root: Path, guide_path: Path, *, onboard_dir: Path = gl.ONBOARD_DIR,
                 review: dict | None = None, token: str | None = None,
                 exercise_timeout: float = 10.0, allow_self_framing: bool = False):
        self.repo_root = Path(repo_root).resolve()
        self.onboard_dir = Path(onboard_dir).resolve()
        self.local_dir = self.onboard_dir / "local"
        self.guide_path = Path(guide_path).resolve()
        if not self.guide_path.is_relative_to(self.onboard_dir):
            raise ValueError(f"guide must live under {self.onboard_dir}")
        self.guide_rel = self.guide_path.relative_to(self.onboard_dir).as_posix()
        self._guide_mtime = None
        self.guide_lock = threading.Lock()
        self.refresh()
        if not self.guide.has_main:
            raise ValueError(f"{self.guide_path} has no <main data-guide> element")
        self.token = token or secrets.token_urlsafe(32)
        deny = []
        if self.local_dir.is_relative_to(self.repo_root):
            deny.append(self.local_dir.relative_to(self.repo_root).as_posix())
        self.policy = gl.PathPolicy(self.repo_root, tuple(deny))
        self.exercise_timeout = exercise_timeout
        self.exercise_lock = threading.Lock()
        self.results_path = gl.results_path_for(self.guide_path, self.guide.guide_id, self.local_dir)
        self.requests_path = self.results_path.with_name(f"{self.results_path.stem}.requests.json")
        self.results_lock = threading.Lock()
        self.review = review
        self.allow_self_framing = allow_self_framing
        self.port = 0

    def refresh(self) -> None:
        """Re-read the guide when Claude has rewritten it, so new modules and
        newly registered exercises take effect without restarting."""
        with self.guide_lock:
            mtime = self.guide_path.stat().st_mtime_ns
            if mtime == self._guide_mtime:
                return
            guide = gl.load_guide(self.guide_path)
            dirs = gl.guide_exercise_dirs(self.guide_path, self.onboard_dir)
            exercises = {}
            for name in guide.exercises:
                path = gl.resolve_exercise(name, dirs)
                if path is not None:
                    exercises[name] = path
            self.guide, self.exercises, self._guide_mtime = guide, exercises, mtime

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.port}/t/{self.token}/{self.guide_rel}"

    # -- review helpers ---------------------------------------------------
    def _review_base(self, key: str | None) -> str:
        if not self.review:
            raise HTTPError(HTTPStatus.NOT_FOUND, "not a review guide")
        bases = self.review.get("bases", {})
        key = key or self.review.get("diff_base")
        if key not in bases:
            raise HTTPError(HTTPStatus.BAD_REQUEST, "unknown diff base")
        return bases[key]

    def _worktree(self) -> Path:
        return Path(self.review["worktree"])

    def changes(self, base_key: str | None) -> list[dict]:
        base = self._review_base(base_key)
        changes = gl.diff_changes(self._worktree(), base, self.review["head_sha"])
        for change in changes:
            try:
                self.policy.check_name(change["path"])
                change["viewable"] = True
            except gl.PolicyError:
                change["viewable"] = False
        return changes

    def change_for(self, base_key: str | None, path: str) -> dict:
        for change in self.changes(base_key):
            if change["path"] == path:
                return change
        raise HTTPError(HTTPStatus.NOT_FOUND, "file is not part of this change")

    def meta(self) -> dict:
        self.refresh()
        info = {
            "guide_mtime": self._guide_mtime,
            "mode": self.guide.mode, "guide": self.guide.guide_id, "version": self.guide.version,
            "guide_path": self.guide_rel, "exercises": sorted(self.exercises), "server": True,
        }
        if self.review:
            head = gl.git(self._worktree(), "rev-parse", "HEAD", check=False).strip()
            info["review"] = {
                "pr": self.review.get("pr"), "title": self.review.get("title"),
                "url": self.review.get("url"), "head_sha": self.review["head_sha"],
                "base_sha": self.review.get("base_sha"), "bases": self.review.get("bases", {}),
                "diff_base": self.review.get("diff_base"),
                "stale": head != self.review["head_sha"],
            }
        return info


class Handler(BaseHTTPRequestHandler):
    server_version = "quizzler"
    sys_version = ""
    app: GuideApp  # set on the subclass created by make_server

    # -- plumbing ---------------------------------------------------------
    def log_message(self, fmt, *args):  # never log paths: they carry the token
        pass

    def _headers(self, status, content_type, length):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN" if self.app.allow_self_framing else "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", LAYOUT_CSP if self.app.allow_self_framing else CSP)

    def _send(self, status, body: bytes, content_type: str):
        self._headers(status, content_type, len(body))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, status=HTTPStatus.OK):
        self._send(status, json.dumps(data).encode(), "application/json; charset=utf-8")

    def _error(self, status, message):
        self._json({"error": message}, status)

    def _host_ok(self) -> bool:
        host = self.headers.get("Host", "")
        return host in (f"{HOST}:{self.app.port}", f"localhost:{self.app.port}")

    def _route(self):
        """Return (rest_of_path, query) for token-scoped routes, or None."""
        parts = urlsplit(self.path)
        raw = parts.path
        if not raw.startswith("/t/"):
            return None
        token, sep, rest = raw[3:].partition("/")
        if not sep or not hmac.compare_digest(token.encode(), self.app.token.encode()):
            return None
        return unquote(rest), parse_qs(parts.query, max_num_fields=20)

    def _dispatch(self, method):
        try:
            if not self._host_ok():
                return self._error(HTTPStatus.MISDIRECTED_REQUEST, "unexpected Host header")
            if method == "GET" and urlsplit(self.path).path == "/healthz":
                return self._send(HTTPStatus.OK, b"ok\n", "text/plain; charset=utf-8")
            routed = self._route()
            if routed is None:
                return self._error(HTTPStatus.NOT_FOUND, "not found")
            rest, query = routed
            if method == "POST":
                return self._post(rest)
            return self._get(rest, query)
        except HTTPError as exc:
            self._error(exc.status, str(exc))
        except gl.PolicyError as exc:
            status = HTTPStatus.FORBIDDEN if exc.code == "forbidden" else HTTPStatus.NOT_FOUND
            self._error(status, str(exc))
        except gl.GitError as exc:
            self._error(HTTPStatus.BAD_GATEWAY, str(exc))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # keep the server alive; report without a traceback
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(exc).__name__}: {exc}")

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    # -- GET routes -------------------------------------------------------
    def _get(self, rest: str, query: dict):
        app = self.app
        q = {k: v[0] for k, v in query.items()}
        if rest == "":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", app.guide_rel)
            self.send_header("Content-Length", "0")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            return
        if rest == app.guide_rel:
            return self._send(HTTPStatus.OK, app.guide_path.read_bytes(), "text/html; charset=utf-8")
        if rest.startswith("app/"):
            name = rest[4:]
            if name not in gl.APP_FILES:
                raise HTTPError(HTTPStatus.NOT_FOUND, "not found")
            ctype = "text/css" if name.endswith(".css") else "text/javascript"
            return self._send(HTTPStatus.OK, (app.onboard_dir / "app" / name).read_bytes(), ctype + "; charset=utf-8")
        handler = {
            "api/meta": lambda: app.meta(),
            "api/outline": lambda: self._outline(),
            "api/source": lambda: self._source(q),
            "api/anchor": lambda: self._anchor(q),
            "api/symbols": lambda: {"path": q.get("path"), "symbols": self._symbols(q.get("path", ""))},
            "api/symbol": lambda: self._symbol(q),
            "api/exercise": lambda: self._exercise_info(q),
            "api/results": lambda: self._load_results(),
            "api/requests": lambda: {"requests": gl.update_requests(app.requests_path),
                                     "listening": gl.listener_alive(app.requests_path)},
            "api/changes": lambda: {"changes": app.changes(q.get("base"))},
            "api/diff": lambda: self._diff(q),
        }.get(rest)
        if handler is None:
            raise HTTPError(HTTPStatus.NOT_FOUND, "not found")
        self._json(handler())

    def _outline(self):
        files = self.app.policy.walk()
        changed = []
        if self.app.review:
            changed = [c["path"] for c in self.app.changes(None)]
        return {"files": files, "changed": changed}

    def _read(self, rel: str) -> tuple[Path, str]:
        path = self.app.policy.check(rel)
        return path, gl.read_text(path)

    def _source(self, q):
        rel = q.get("path", "")
        _, text = self._read(rel)
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        total = len(lines)
        try:
            if "around" in q:
                around = int(q["around"])
                start = max(1, around - MAX_SNIPPET_LINES // 2)
            else:
                start = max(1, int(q.get("start", 1)))
            end = min(total, int(q.get("end", start + MAX_SNIPPET_LINES - 1)), start + MAX_SNIPPET_LINES - 1)
        except ValueError:
            raise HTTPError(HTTPStatus.BAD_REQUEST, "bad line numbers") from None
        result = {"path": rel, "start": start, "end": end, "total": total,
                  "lines": lines[start - 1:end] if start <= total else []}
        if self.app.review:
            change = next((c for c in self.app.changes(q.get("base")) if c["path"] == rel), None)
            if change and not change["binary"]:
                base = self.app._review_base(q.get("base"))
                result["changed"] = gl.changed_lines(self.app._worktree(), base, self.app.review["head_sha"], rel)
                result["status"] = change["status"]
        return result

    def _anchor(self, q):
        rel = q.get("path", "")
        _, text = self._read(rel)
        occurrence = q.get("occurrence")
        try:
            found = gl.resolve_anchor(text, q.get("ref", ""), int(occurrence) if occurrence else None)
        except gl.AnchorError as exc:
            raise HTTPError(HTTPStatus.NOT_FOUND, str(exc)) from None
        return {"path": rel, **found}

    def _symbols(self, rel):
        _, text = self._read(rel)
        return gl.find_symbols(rel, text)

    def _symbol(self, q):
        rel = q.get("path", "")
        symbol = gl.lookup_symbol(self._symbols(rel), q.get("name", ""))
        if symbol is None:
            raise HTTPError(HTTPStatus.NOT_FOUND, f"symbol not found: {q.get('name')}")
        return {"path": rel, **symbol, "end_line": symbol.get("end_line", symbol["line"])}

    def _exercise_path(self, name) -> Path:
        self.app.refresh()
        path = self.app.exercises.get(name)
        if path is None:
            raise HTTPError(HTTPStatus.NOT_FOUND, "exercise is not registered for this guide")
        return path

    def _exercise_info(self, q):
        name = q.get("name", "")
        path = self._exercise_path(name)
        return {"name": name, "file": path.name, "predict": gl.exercise_predict(path),
                **gl.exercise_listing(path)}

    def _load_results(self):
        path = self.app.results_path
        if not path.is_file():
            return {"results": None}
        try:
            return {"results": json.loads(path.read_text(encoding="utf-8"))}
        except json.JSONDecodeError:
            return {"results": None}

    def _diff(self, q):
        path = q.get("path", "")
        self.app.policy.check_name(path)
        change = self.app.change_for(q.get("base"), path)
        if change["binary"]:
            return {"path": path, "binary": True, "diff": ""}
        base = self.app._review_base(q.get("base"))
        text = gl.diff_file(self.app._worktree(), base, self.app.review["head_sha"], path, change["old_path"])
        return {"path": path, "binary": False, "diff": text, "status": change["status"], "old_path": change["old_path"]}

    # -- POST routes ------------------------------------------------------
    def _body(self) -> dict:
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://{HOST}:{self.app.port}", f"http://localhost:{self.app.port}"):
            raise HTTPError(HTTPStatus.FORBIDDEN, "cross-origin request refused")
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            raise HTTPError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "JSON body required")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 0 < length <= MAX_BODY:
            raise HTTPError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "body missing or too large")
        try:
            data = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            raise HTTPError(HTTPStatus.BAD_REQUEST, "invalid JSON") from None
        if not isinstance(data, dict):
            raise HTTPError(HTTPStatus.BAD_REQUEST, "JSON object required")
        return data

    def _post(self, rest: str):
        data = self._body()
        if rest == "api/exercise/run":
            path = self._exercise_path(str(data.get("name", "")))
            if not self.app.exercise_lock.acquire(timeout=1):
                raise HTTPError(HTTPStatus.CONFLICT, "another exercise is running")
            try:
                return self._json(gl.run_exercise(path, timeout=self.app.exercise_timeout))
            finally:
                self.app.exercise_lock.release()
        if rest == "api/results":
            results = data.get("results")
            if not isinstance(results, dict):
                raise HTTPError(HTTPStatus.BAD_REQUEST, "results object required")
            if results.get("guide") != self.app.guide.guide_id:
                raise HTTPError(HTTPStatus.BAD_REQUEST, "results belong to another guide")
            with self.app.results_lock:
                gl.write_json(self.app.results_path, results)
            return self._json({"saved": True})
        if rest == "api/request":
            return self._json(self._add_request(data))
        raise HTTPError(HTTPStatus.NOT_FOUND, "not found")

    def _add_request(self, data: dict) -> dict:
        """Queue a request for the Claude session (see relay.py)."""
        import datetime as dt
        app = self.app
        app.refresh()
        action = data.get("action")
        if action not in gl.REQUEST_ACTIONS:
            raise HTTPError(HTTPStatus.BAD_REQUEST, "unknown action")
        extra: dict = {}
        module = None
        if action == "write-modules":
            # {module id: intensity} for every module to write, in plan order.
            wanted = data.get("modules")
            if not isinstance(wanted, dict) or not wanted:
                raise HTTPError(HTTPStatus.BAD_REQUEST, "modules to write are required")
            for mid, intensity in wanted.items():
                if mid not in app.guide.modules or intensity not in gl.INTENSITIES or intensity == "skip":
                    raise HTTPError(HTTPStatus.BAD_REQUEST, f"cannot write module {mid!r} at {intensity!r}")
            extra["modules"] = wanted
        elif action == "help-module":
            module = data.get("module")
            message = data.get("message")
            if module not in app.guide.modules:
                raise HTTPError(HTTPStatus.BAD_REQUEST, "unknown module")
            if not isinstance(message, str) or not message.strip() or len(message) > gl.MAX_HELP_CHARS:
                raise HTTPError(HTTPStatus.BAD_REQUEST, f"a help message of 1-{gl.MAX_HELP_CHARS} characters is required")
            extra["message"] = message.strip()
        created = {}

        def add(items):
            for item in items:
                if item.get("status") in ("pending", "working") and item.get("action") == action \
                        and item.get("module") == module:
                    item.update(extra)
                    created["request"] = item
                    return
            item = {
                "id": f"r{len(items) + 1}", "action": action, "module": module, **extra,
                "status": "pending",
                "created_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
            }
            items.append(item)
            created["request"] = item

        gl.update_requests(app.requests_path, add)
        return created


def make_server(app: GuideApp, port: int = 0) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = ThreadingHTTPServer((HOST, port), handler)
    server.daemon_threads = True
    app.port = server.server_address[1]
    return server


def load_review(number: int, onboard_dir: Path = gl.ONBOARD_DIR) -> tuple[dict, Path]:
    review_dir = onboard_dir / "local" / "reviews" / f"pr-{number}"
    meta_path = review_dir / "meta.json"
    if not meta_path.is_file():
        raise SystemExit(f"no review prepared for PR {number}; run review.py prepare {number}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    guide = review_dir / "guide.html"
    if not guide.is_file():
        raise SystemExit(f"{guide} does not exist yet; generate the first module before serving")
    return meta, guide


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--guide", type=Path, help="guide HTML under docs/onboarding/")
    parser.add_argument("--review", type=int, metavar="N", help="serve the prepared review for PR N")
    parser.add_argument("--repo-root", type=Path, help="repository whose source is shown")
    parser.add_argument("--port", type=int, default=0, help="loopback port (default: random)")
    parser.add_argument("--open", action="store_true", help="open the guide in a browser")
    parser.add_argument("--exercise-timeout", type=float, default=10.0)
    args = parser.parse_args(argv)

    review = None
    if args.review is not None:
        review, guide = load_review(args.review)
        repo_root = Path(review["worktree"])
    else:
        local_guide = gl.LOCAL_DIR / "guide.html"
        guide = args.guide or (local_guide if local_guide.is_file() else gl.ONBOARD_DIR / "guide.html")
        repo_root = args.repo_root or gl.find_repo_root()
    app = GuideApp(repo_root, guide, review=review, exercise_timeout=args.exercise_timeout)
    server = make_server(app, args.port)

    def stop(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    print(f"Quizzler guide ({app.guide.mode}, v{app.guide.version}): {app.url}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    if args.open:
        webbrowser.open(app.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("Server stopped.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
