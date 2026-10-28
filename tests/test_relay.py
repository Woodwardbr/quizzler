# SPDX-License-Identifier: Apache-2.0
import http.client
import json
import threading
import time
import unittest

from support import Fixture

import guidelib as gl
import relay
import serve


class RelayTest(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.app = serve.GuideApp(self.fx.root, self.fx.onboard / "guide.html", onboard_dir=self.fx.onboard)
        self.server = serve.make_server(self.app)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.fx.cleanup()

    def post(self, body):
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=10)
        conn.request("POST", f"/t/{self.app.token}/api/request", json.dumps(body), {"Content-Type": "application/json"})
        response = conn.getresponse()
        data = json.loads(response.read())
        conn.close()
        return response.status, data

    def test_queue_dedupes_and_validates(self):
        status, first = self.post({"action": "write-modules", "modules": {"m1": "deep"}})
        self.assertEqual((status, first["request"]["status"]), (200, "pending"))
        status, again = self.post({"action": "write-modules", "modules": {"m1": "standard"}})
        self.assertEqual(again["request"]["id"], first["request"]["id"])
        self.assertEqual(again["request"]["modules"], {"m1": "standard"})
        status, helped = self.post({"action": "help-module", "module": "m1", "message": "  explain run  "})
        self.assertEqual((status, helped["request"]["message"]), (200, "explain run"))
        for bad in ({"action": "rm -rf"}, {"action": "write-modules", "modules": {}},
                    {"action": "write-modules", "modules": {"nope": "deep"}},
                    {"action": "write-modules", "modules": {"m1": "skip"}},
                    {"action": "help-module", "module": "m1", "message": ""},
                    {"action": "help-module", "module": "m1", "message": "x" * 3000},
                    {"action": "help-module", "module": "ghost", "message": "hi"}):
            self.assertEqual(self.post(bad)[0], 400, bad)
        items = gl.update_requests(self.app.requests_path)
        self.assertEqual(len(items), 2)
        self.assertTrue(self.app.requests_path.is_relative_to(self.fx.onboard / "local"))

    def test_wait_returns_when_a_request_arrives_and_done_clears_it(self):
        path = self.app.requests_path
        self.assertEqual(relay.wait(path, timeout=0.2, interval=0.05), [])
        threading.Timer(0.3, lambda: self.post({"action": "next-module"})).start()
        started = time.monotonic()
        found = relay.wait(path, timeout=10, interval=0.05)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual([r["action"] for r in found], ["next-module"])
        self.assertEqual(relay.mark_done(path, [found[0]["id"]]), [found[0]["id"]])
        self.assertEqual(relay.pending(path), [])

    def test_claim_and_listener_status(self):
        def queue():
            conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=10)
            conn.request("GET", f"/t/{self.app.token}/api/requests")
            data = json.loads(conn.getresponse().read())
            conn.close()
            return data
        self.assertFalse(queue()["listening"])
        self.post({"action": "final-review"})
        found = relay.wait(self.app.requests_path, timeout=2, interval=0.05)
        self.assertEqual([(r["action"], r["status"]) for r in found], [("final-review", "working")])
        self.assertTrue(queue()["listening"])
        self.assertEqual(queue()["requests"][0]["status"], "working")
        # A session that died mid-request: the next wait hands the work out again.
        again = relay.wait(self.app.requests_path, timeout=0.2, interval=0.05)
        self.assertEqual([r["id"] for r in again], [found[0]["id"]])
        relay.mark_done(self.app.requests_path, [found[0]["id"]])
        self.assertEqual(relay.wait(self.app.requests_path, timeout=0.2, interval=0.05), [])

    def test_server_picks_up_rewritten_guide(self):
        before = self.app.meta()["guide_mtime"]
        self.assertNotIn("extra", self.app.exercises)
        self.fx.write("docs/onboarding/exercises/extra.py", '"""PREDICT: x"""\nprint(1)\n')
        time.sleep(0.01)
        guide = self.fx.onboard / "guide.html"
        head, tail = guide.read_text().rsplit("</section>", 1)
        guide.write_text(head + '<div class="exercise" data-exercise="extra"></div></section>' + tail)
        self.assertNotEqual(self.app.meta()["guide_mtime"], before)
        self.assertIn("extra", self.app.exercises)


if __name__ == "__main__":
    unittest.main()
