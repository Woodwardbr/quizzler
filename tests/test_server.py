# SPDX-License-Identifier: Apache-2.0
import http.client
import json
import threading
import unittest

from support import DEFAULT_BODY, Fixture

import serve


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.fx.write("quizzler/exercises/unlisted.py", '"""PREDICT: x"""\nprint("should not run")\n')
        self.fx.write("deploy/env.storage.json", '{"key": "secret"}\n')
        self.fx.write("config/.env", "SECRET=1\n")
        self.fx.write("quizzler/local/results/other.json", "{}\n")
        self.app = serve.GuideApp(self.fx.root, self.fx.onboard / "guide.html", onboard_dir=self.fx.onboard)
        self.server = serve.make_server(self.app)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.prefix = f"/t/{self.app.token}/"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.fx.cleanup()

    def request(self, method, path, body=None, headers=None, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=30)
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        conn.putheader("Host", host or f"127.0.0.1:{self.app.port}")
        payload = json.dumps(body).encode() if body is not None else b""
        hdrs = {"Content-Type": "application/json"} if body is not None else {}
        hdrs.update(headers or {})
        for key, value in hdrs.items():
            conn.putheader(key, value)
        conn.putheader("Content-Length", str(len(payload)))
        conn.endheaders(payload)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response, data

    def get_json(self, route, **query):
        from urllib.parse import urlencode
        response, data = self.request("GET", self.prefix + route + ("?" + urlencode(query) if query else ""))
        return response.status, json.loads(data)

    def test_binds_loopback_with_unguessable_token(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertGreaterEqual(len(self.app.token), 40)
        other = serve.GuideApp(self.fx.root, self.fx.onboard / "guide.html", onboard_dir=self.fx.onboard)
        self.assertNotEqual(self.app.token, other.token)

    def test_token_required_everywhere_but_health(self):
        self.assertEqual(self.request("GET", "/healthz")[0].status, 200)
        for path in ("/", "/guide.html", "/app/guide.js", "/api/meta", "/t/wrong/guide.html",
                     "/t/wrong/api/source?path=src/app.py", f"/t/{self.app.token[:-1]}/api/meta"):
            self.assertEqual(self.request("GET", path)[0].status, 404, path)
        response, _ = self.request("GET", self.prefix)
        self.assertEqual(response.status, 302)
        self.assertEqual(response.getheader("Location"), "guide.html")
        response, body = self.request("GET", self.prefix + "guide.html")
        self.assertEqual(response.status, 200)
        self.assertIn(b"data-guide", body)
        self.assertIn("frame-ancestors 'none'", response.getheader("Content-Security-Policy"))
        self.assertEqual(response.getheader("Referrer-Policy"), "no-referrer")

    def test_rejects_foreign_host_header(self):
        response, _ = self.request("GET", self.prefix + "api/meta", host="evil.example:80")
        self.assertEqual(response.status, 421)
        response, _ = self.request("GET", "/healthz", host=f"attacker.test:{self.app.port}")
        self.assertEqual(response.status, 421)

    def test_serves_only_engine_assets(self):
        self.assertEqual(self.request("GET", self.prefix + "app/guide.js")[0].status, 200)
        self.assertEqual(self.request("GET", self.prefix + "app/serve.py")[0].status, 404)
        self.assertEqual(self.request("GET", self.prefix + "serve.py")[0].status, 404)
        self.assertEqual(self.request("GET", self.prefix + "local/results/other.json")[0].status, 404)

    def test_source_and_anchor(self):
        status, data = self.get_json("api/source", path="src/app.py")
        self.assertEqual(status, 200)
        self.assertEqual(data["lines"][3], "def handler(request):")
        status, data = self.get_json("api/anchor", path="src/app.py", ref="def handler(request):")
        self.assertEqual((status, data["line"]), (200, 4))
        status, data = self.get_json("api/symbol", path="src/app.py", name="Service.run")
        self.assertEqual((status, data["line"]), (200, 9))

    def test_source_refuses_traversal_and_secrets(self):
        cases = {
            "../../etc/passwd": 403, "src/../../repo/src/app.py": 403, "%2e%2e/%2e%2e/etc/passwd": 403,
            "config/.env": 403, "deploy/env.storage.json": 403, ".git/config": 403,
            "quizzler/local/results/other.json": 403, "/etc/passwd": 403, "src/nope.py": 404,
        }
        for path, expected in cases.items():
            # Pass the path pre-encoded so the server sees the raw form.
            response, body = self.request("GET", f"{self.prefix}api/source?path={path}")
            self.assertEqual(response.status, expected, path)
            self.assertNotIn(b"secret", body.lower().replace(b"secret-like", b""), path)

    def test_source_is_bounded(self):
        self.fx.write("src/big.py", "".join(f"x{i} = {i}\n" for i in range(2000)))
        status, data = self.get_json("api/source", path="src/big.py")
        self.assertEqual((data["start"], data["end"], data["total"]), (1, 400, 2000))
        status, data = self.get_json("api/source", path="src/big.py", start=1, end=5000)
        self.assertEqual(len(data["lines"]), 400)

    def test_outline_hides_refused_files(self):
        status, data = self.get_json("api/outline")
        self.assertIn("src/app.py", data["files"])
        for bad in ("deploy/env.storage.json", "config/.env", "quizzler/local/results/other.json"):
            self.assertNotIn(bad, data["files"])

    def test_runs_only_registered_exercises(self):
        status, data = self.get_json("api/exercise", name="hello")
        self.assertEqual((status, data["predict"]), (200, "What does it print?"))
        response, body = self.request("POST", self.prefix + "api/exercise/run", {"name": "hello"})
        result = json.loads(body)
        self.assertEqual((response.status, result["stdout"], result["exit_code"]), (200, "hello 42\n", 0))
        for name in ("unlisted", "../exercises/hello", "hello.py", ""):
            response, _ = self.request("POST", self.prefix + "api/exercise/run", {"name": name})
            self.assertEqual(response.status, 404, name)
        response, _ = self.request("POST", self.prefix + "api/exercise/run", {"name": "hello", "code": "print(1)"})
        self.assertEqual(response.status, 200)  # extra fields are ignored, never executed

    def test_post_requires_json_and_same_origin(self):
        response, _ = self.request("POST", self.prefix + "api/exercise/run", {"name": "hello"},
                                   headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status, 403)
        response, _ = self.request("POST", self.prefix + "api/exercise/run", {"name": "hello"},
                                   headers={"Content-Type": "text/plain"})
        self.assertEqual(response.status, 415)

    def test_results_saved_under_local(self):
        response, _ = self.request("POST", self.prefix + "api/results", {"results": {"guide": "fixture", "answers": {}}})
        self.assertEqual(response.status, 200)
        saved = self.fx.onboard / "local" / "results" / "fixture.json"
        self.assertTrue(saved.is_file())
        status, data = self.get_json("api/results")
        self.assertEqual(data["results"]["guide"], "fixture")
        response, _ = self.request("POST", self.prefix + "api/results", {"results": {"guide": "other"}})
        self.assertEqual(response.status, 400)

    def test_local_guide_results_sit_next_to_it(self):
        guide = self.fx.write_guide("local/reviews/pr-3/guide.html", DEFAULT_BODY)
        app = serve.GuideApp(self.fx.root, guide, onboard_dir=self.fx.onboard)
        self.assertEqual(app.results_path, guide.parent / "results.json")
        self.assertEqual(app.guide_rel, "local/reviews/pr-3/guide.html")


class ExerciseLimitsTest(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_timeout_and_output_cap(self):
        import guidelib as gl
        slow = self.fx.write("ex/slow.py", "while True:\n    pass\n")
        result = gl.run_exercise(slow, timeout=1)
        self.assertTrue(result["timed_out"])
        noisy = self.fx.write("ex/noisy.py", "import sys\nfor _ in range(100000):\n    sys.stdout.write('x' * 100)\n")
        result = gl.run_exercise(noisy, timeout=10, max_output=1000)
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["stdout"]), 1000)

    def test_scrubbed_environment(self):
        import os
        import guidelib as gl
        os.environ["QUIZZLER_TEST_SECRET"] = "leak"
        try:
            probe = self.fx.write("ex/env.py", "import os\nprint(sorted(k for k in os.environ if 'SECRET' in k))\nprint(hash('a') == hash('a'))\n")
            result = gl.run_exercise(probe)
        finally:
            del os.environ["QUIZZLER_TEST_SECRET"]
        self.assertEqual(result["stdout"], "[]\nTrue\n")


if __name__ == "__main__":
    unittest.main()
