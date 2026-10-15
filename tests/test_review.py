import json
import threading
import unittest

from support import Fixture, git

import check
import guidelib as gl
import review
import serve

REVIEW_BODY = """
<section class="module" data-module="handler" data-title="Handler change">
  <h2>Handler change</h2>
  <p><code data-src="src/app.py" data-ref="return request.strip()">strip</code></p>
  <div data-q="h1" data-answer="a"><p class="prompt">What changed?</p>
    <ol class="choices"><li data-choice="a">strip</li><li data-choice="b">nothing</li></ol></div>
  <div class="findings">
    <article class="finding" data-finding="none-input" data-src="src/app.py" data-lines="5">
      <h3>None input crashes</h3><div class="comment-draft">What if request is None?</div>
    </article>
  </div>
</section>
"""


class ReviewWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture(git_init=True)
        root = self.fx.root
        self.fx.write("src/old_name.py", "VALUE = 1\n" * 20)
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "more base")
        git(root, "checkout", "-q", "-b", "feature")
        self.fx.write("src/app.py", self.fx.root.joinpath("src/app.py").read_text()
                      .replace("    return request\n", "    return request.strip()\n"))
        git(root, "mv", "src/old_name.py", "src/new_name.py")
        (root / "assets").mkdir()
        (root / "assets" / "data.bin").write_bytes(b"\x00\x01\x02binary\x00")
        (root / "assets" / "table.csv").write_bytes(b"a,b\n\x00\x01,2\n")
        self.fx.write("deploy/env.storage.json", '{"token": "x"}\n')
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "feature")
        git(root, "checkout", "-q", "main")
        self.local = self.fx.onboard / "local"
        self.meta = review.prepare(root, 7, review.LocalAdapter("main", "feature"), self.local)

    def tearDown(self):
        self.fx.cleanup()

    def write_guide(self):
        head = self.meta["head_sha"]
        return self.fx.write_guide("local/reviews/pr-7/guide.html", REVIEW_BODY, mode="review",
                                   guide_id="pr-7", extra=f' data-pr="7" data-head-sha="{head}"')

    def start_server(self):
        app = serve.GuideApp(self.meta["worktree"], self.write_guide(), onboard_dir=self.fx.onboard,
                             review=json.loads((self.local / "reviews/pr-7/meta.json").read_text()))
        server = serve.make_server(app)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return app

    def get(self, app, route, **query):
        import http.client
        from urllib.parse import urlencode
        conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=30)
        conn.request("GET", f"/t/{app.token}/{route}?{urlencode(query)}")
        response = conn.getresponse()
        data = json.loads(response.read())
        conn.close()
        return response.status, data

    def test_prepare_records_shas_and_ignored_worktree(self):
        meta = self.meta
        self.assertEqual(meta["head_sha"], git(self.fx.root, "rev-parse", "feature"))
        self.assertEqual(meta["bases"]["merge-base"], git(self.fx.root, "rev-parse", "main"))
        self.assertEqual(meta["diff_base"], "merge-base")
        wt = self.fx.root / ".claude/worktrees/pr-7"
        self.assertEqual(git(wt, "rev-parse", "HEAD"), meta["head_sha"])
        self.assertEqual(git(self.fx.root, "status", "--porcelain", "--untracked-files=all"), "")
        changes = {c["path"]: c for c in meta["changes"]}
        self.assertEqual(changes["src/app.py"]["status"], "M")
        self.assertEqual((changes["src/new_name.py"]["status"], changes["src/new_name.py"]["old_path"]),
                         ("R", "src/old_name.py"))
        self.assertTrue(changes["assets/data.bin"]["binary"])

    def test_changes_view_and_diff_markings(self):
        app = self.start_server()
        status, data = self.get(app, "api/changes")
        by_path = {c["path"]: c for c in data["changes"]}
        self.assertFalse(by_path["deploy/env.storage.json"]["viewable"])
        status, diff = self.get(app, "api/diff", path="src/app.py")
        self.assertIn("+    return request.strip()", diff["diff"])
        status, diff = self.get(app, "api/diff", path="src/new_name.py")
        self.assertEqual((status, diff["old_path"]), (200, "src/old_name.py"))
        status, diff = self.get(app, "api/diff", path="assets/table.csv")
        self.assertTrue(diff["binary"])
        self.assertFalse(by_path["assets/data.bin"]["viewable"])  # not an allowlisted type
        status, _ = self.get(app, "api/diff", path="assets/data.bin")
        self.assertEqual(status, 403)
        status, _ = self.get(app, "api/diff", path="deploy/env.storage.json")
        self.assertEqual(status, 403)
        status, _ = self.get(app, "api/diff", path="src/unchanged.py")
        self.assertEqual(status, 404)
        status, source = self.get(app, "api/source", path="src/app.py")
        self.assertEqual(source["changed"], [5])
        status, meta = self.get(app, "api/meta")
        self.assertFalse(meta["review"]["stale"])
        status, _ = self.get(app, "api/changes", base="not-a-base")
        self.assertEqual(status, 400)

    def test_review_guide_validates_against_worktree(self):
        guide = self.write_guide()
        report = check.Report()
        check.check_guide(guide, self.fx.root / ".claude/worktrees/pr-7", report, onboard_dir=self.fx.onboard)
        self.assertEqual(report.errors, [])
        # The same anchor does not exist in the base checkout.
        report = check.Report()
        check.check_guide(guide, self.fx.root, report, onboard_dir=self.fx.onboard)
        self.assertIn("literal not found", "\n".join(report.errors))

    def test_module_results_to_sha_bound_preview(self):
        self.write_guide()
        head = self.meta["head_sha"]
        payload = {
            "kind": "review", "guide": "pr-7", "version": 1, "pr": "7", "head": head,
            "answers": {"h1": {"choice": "a", "confidence": "sure"}},
            "findings": {"none-input": {"response": "agree", "confidence": "sure", "state": "ready",
                                         "comment": "What if request is None?", "path": "src/app.py", "lines": "5"}},
            "comments": [
                {"id": "c1", "path": "src/app.py", "start": 4, "end": 5, "body": "Nice simplification.", "state": "ready"},
                {"id": "c2", "path": "src/app.py", "start": 9, "end": 10, "body": "Unrelated.", "state": "ready"},
                {"id": "c3", "path": "src/app.py", "start": 1, "end": 1, "body": "Hmm.", "state": "needs-another-look"},
                {"id": "c4", "path": "src/app.py", "start": 1, "end": 1, "body": "No.", "state": "not-posting"},
            ],
        }
        record = review.ingest(self.local, 7, "QUIZZLER-REVIEW " + json.dumps(payload))
        self.assertEqual(record["summary"]["counts"]["mastered"], 1)
        self.assertTrue((self.local / "reviews/pr-7/progress.md").is_file())

        preview = review.preview(self.fx.root, self.local, 7)
        self.assertEqual(preview["head_sha"], head)
        ready = {c["id"]: c for c in preview["comments"]}
        self.assertEqual(set(ready), {"none-input", "c1", "c2"})
        self.assertEqual(ready["none-input"]["snippet"], "    return request.strip()")
        self.assertEqual(ready["none-input"]["commit_id"], head)
        self.assertTrue(ready["c1"]["in_diff"])
        self.assertFalse(ready["c2"]["in_diff"])
        self.assertEqual(preview["needs_another_look"], ["c3"])
        self.assertEqual(preview["not_posting"], ["c4"])
        self.assertTrue(any("outside the diff" in p for p in preview["problems"]))
        text = review.format_preview(preview)
        self.assertIn("QUIZZLER-PREVIEW {", text)
        self.assertIn("Nothing has been posted", text)

    def test_preview_rejects_bad_ranges_and_wrong_head(self):
        results = {"findings": {"f": {"state": "ready", "comment": "x", "path": "src/app.py", "lines": "50-60"}},
                   "comments": [{"id": "c", "path": "src/gone.py", "start": 1, "end": 1, "body": "x", "state": "ready"}]}
        gl.write_json(self.local / "reviews/pr-7/results.json", results)
        problems = "\n".join(review.preview(self.fx.root, self.local, 7)["problems"])
        self.assertIn("past the end of src/app.py", problems)
        self.assertIn("src/gone.py does not exist", problems)
        with self.assertRaisesRegex(SystemExit, "different head SHA"):
            review.ingest(self.local, 7, 'QUIZZLER-REVIEW {"pr": "7", "head": "' + "0" * 40 + '"}')

    def test_stale_review_when_head_moves(self):
        self.write_guide()
        gl.write_json(self.local / "reviews/pr-7/results.json", {"findings": {}, "comments": []})
        root = self.fx.root
        git(root, "checkout", "-q", "feature")
        self.fx.write("src/app.py", "def handler(request):\n    return request\n")
        git(root, "commit", "-qam", "push more")
        git(root, "checkout", "-q", "main")
        state = review.status(root, 7, review.LocalAdapter("main", "feature"), self.local)
        self.assertTrue(state["stale"])
        meta = review.prepare(root, 7, review.LocalAdapter("main", "feature"), self.local)
        self.assertEqual(meta["previous_heads"][0]["head_sha"], self.meta["head_sha"])
        archived = list((self.local / "reviews/pr-7").glob("results.*.json"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(git(root / ".claude/worktrees/pr-7", "rev-parse", "HEAD"), meta["head_sha"])

    def test_no_diff_pr(self):
        git(self.fx.root, "branch", "same", "main")
        meta = review.prepare(self.fx.root, 8, review.LocalAdapter("main", "same"), self.local)
        self.assertEqual(meta["changes"], [])
        gl.write_json(self.local / "reviews/pr-8/results.json", {"findings": {}, "comments": []})
        preview = review.preview(self.fx.root, self.local, 8)
        self.assertEqual((preview["comments"], preview["problems"]), ([], []))

    def test_cleanup_is_dry_run_by_default(self):
        wt = self.fx.root / ".claude/worktrees/pr-7"
        actions = review.cleanup(self.fx.root, self.local, 7, yes=False, keep_state=False)
        self.assertTrue(all(a.startswith("would") or a.startswith("re-run") for a in actions))
        self.assertTrue(wt.exists())
        review.cleanup(self.fx.root, self.local, 7, yes=True, keep_state=False)
        self.assertFalse(wt.exists())
        self.assertFalse((self.local / "reviews/pr-7").exists())
        self.assertNotIn(str(wt), git(self.fx.root, "worktree", "list"))
        self.assertEqual(git(self.fx.root, "status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()
