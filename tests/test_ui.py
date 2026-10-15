"""End-to-end browser tests through Firefox Marionette (skipped without Firefox)."""

import json
import shutil
import threading
import time
import unittest

from support import Fixture, git

import check_layout
import review
import serve

FIREFOX = shutil.which("firefox")

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


@unittest.skipUnless(FIREFOX, "Firefox not installed")
class BrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.browser = check_layout.Firefox(FIREFOX)
        cls.browser._cmd("WebDriver:SetWindowRect", {"width": 1300, "height": 900})

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()

    def js(self, script, *args):
        return self.browser._script(script, *args)

    def wait(self, condition, *args, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = self.js("return (" + condition + ")", *args)
            if value:
                return value
            time.sleep(0.1)
        self.fail(f"timed out waiting for: {condition}")

    def serve(self, app):
        server = serve.make_server(app)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.browser._cmd("WebDriver:Navigate", {"url": app.url})
        self.wait("document.documentElement.classList.contains('qz-ready')")

    def answer(self, qid, choice, confidence):
        self.js("""
            const q = document.querySelector(`[data-q="${arguments[0]}"]`);
            q.querySelector(`.qz-choices input[value="${arguments[1]}"]`).click();
            q.querySelector(`.qz-q-form input[value="${arguments[2]}"]`).click();
            q.querySelector('.qz-q-form button[type=submit]').click();
        """, qid, choice, confidence)

    def test_onboarding_flow(self):
        fx = Fixture()
        self.addCleanup(fx.cleanup)
        app = serve.GuideApp(fx.root, fx.onboard / "guide.html", onboard_dir=fx.onboard)
        self.serve(app)
        self.assertEqual(self.js("return document.querySelectorAll('.qz-modules li').length"), 1)

        self.answer("q1", "b", "sure")
        feedback = self.wait("document.querySelector('[data-q=q1] .qz-feedback').textContent")
        self.assertIn("Correct. Mastered", feedback)
        self.answer("q2", "b", "guess")
        self.assertIn("Gap", self.wait("document.querySelector('[data-q=q2] .qz-feedback').textContent"))
        self.assertEqual(self.js("return document.querySelector('.qz-mod-status').textContent"), "2/2")

        # Exercise: a prediction is required before running.
        self.js("document.querySelector('.qz-exercise .qz-primary').click()")
        self.assertIn("prediction first", self.js("return document.querySelector('.qz-exercise .qz-feedback').textContent"))
        self.js("""const t = document.querySelector('.qz-prediction'); t.value = 'hello 42';
                   t.dispatchEvent(new Event('input')); document.querySelector('.qz-exercise .qz-primary').click();""")
        self.wait("document.querySelector('.qz-output').textContent.includes('hello 42')")
        self.assertIn("matched exactly", self.js("return document.querySelector('.qz-exercise .qz-feedback').textContent"))

        # Literal anchor opens the docked source pane on the right line.
        self.js("document.querySelector('code[data-ref]').click()")
        self.wait("document.querySelector('.qz-line.qz-hl')")
        self.assertEqual(self.js("return document.querySelector('.qz-line.qz-hl code').textContent"), "def handler(request):")
        self.assertTrue(self.js("return getComputedStyle(document.querySelector('.qz-code')).position === 'sticky'"))

        # Results autosave under local/ and export as a result code.
        saved = fx.onboard / "local" / "results" / "fixture.json"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not (saved.is_file() and '"hello"' in saved.read_text()):
            time.sleep(0.1)
        data = json.loads(saved.read_text())
        self.assertEqual(data["answers"]["q1"]["confidence"], "sure")
        self.assertTrue(data["exercises"]["hello"]["matched"])
        self.js("[...document.querySelectorAll('.qz-top button')].find(b => b.textContent === 'Export results').click()")
        code = self.wait("document.querySelector('.qz-dialog textarea') && document.querySelector('.qz-dialog textarea').value")
        self.assertTrue(code.startswith("QUIZZLER-ONBOARD {"))

        # Reload restores answered state from the saved results.
        self.browser._cmd("WebDriver:Navigate", {"url": app.url})
        self.wait("document.documentElement.classList.contains('qz-ready')")
        self.assertTrue(self.wait("document.querySelector('[data-q=q1]').classList.contains('qz-answered')"))

    def test_review_flow(self):
        fx = Fixture(git_init=True)
        self.addCleanup(fx.cleanup)
        git(fx.root, "checkout", "-q", "-b", "feature")
        fx.write("src/app.py", (fx.root / "src/app.py").read_text().replace("    return request\n", "    return request.strip()\n"))
        git(fx.root, "commit", "-qam", "strip")
        git(fx.root, "checkout", "-q", "main")
        local = fx.onboard / "local"
        meta = review.prepare(fx.root, 5, review.LocalAdapter("main", "feature"), local)
        guide = fx.write_guide("local/reviews/pr-5/guide.html", REVIEW_BODY, mode="review", guide_id="pr-5",
                               extra=f' data-pr="5" data-head-sha="{meta["head_sha"]}"')
        stored = json.loads((local / "reviews/pr-5/meta.json").read_text())
        app = serve.GuideApp(meta["worktree"], guide, onboard_dir=fx.onboard, review=stored)
        self.serve(app)

        # Findings stay locked until the module's question is answered.
        self.assertTrue(self.js("return document.querySelector('[data-finding]').hidden"))
        self.answer("h1", "a", "unsure")
        self.wait("!document.querySelector('[data-finding]').hidden")
        self.js("""const card = document.querySelector('[data-finding]');
                   card.querySelector('input[value=agree]').click();
                   const s = card.querySelector('.qz-state-select'); s.value = 'ready'; s.dispatchEvent(new Event('change'));""")

        # Changes view lists the file and renders an added line.
        self.js("document.getElementById('qz-tab-changes').click()")
        self.wait("document.querySelector('.qz-change-list button')")
        self.js("document.querySelector('.qz-change-list button').click()")
        added = self.wait("document.querySelector('.qz-d-add code') && document.querySelector('.qz-d-add code').textContent")
        self.assertEqual(added, "    return request.strip()")

        # Add a reviewer comment from the source pane, then see both in the Review view.
        self.js("[...document.querySelectorAll('.qz-diff button')].find(b => b.textContent.includes('source pane')).click()")
        self.wait("document.querySelector('.qz-line[data-line=\"5\"].qz-added')")
        self.js("document.querySelector('.qz-line[data-line=\"5\"] .qz-ln').click()")
        self.js("""const box = document.querySelector('.qz-comment-box'); const t = box.querySelector('textarea');
                   t.value = 'Consider None.'; box.querySelector('select').value = 'ready';
                   [...box.querySelectorAll('button')].find(b => b.textContent === 'Add comment').click();""")
        self.js("document.getElementById('qz-tab-review').click()")
        text = self.wait("document.getElementById('qz-view-review').textContent")
        self.assertIn("Ready (2)", text)
        self.assertIn("Your comment on src/app.py:5", text)
        self.assertIn("Nothing is posted from this page", text)

        results = local / "reviews/pr-5/results.json"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not (results.is_file() and "Consider None." in results.read_text()):
            time.sleep(0.1)
        preview = review.preview(fx.root, local, 5)
        self.assertEqual({c["id"] for c in preview["comments"]} - {"none-input"}, {preview["comments"][-1]["id"]})
        self.assertEqual(len(preview["comments"]), 2)
        self.assertTrue(all(c["in_diff"] for c in preview["comments"]))


if __name__ == "__main__":
    unittest.main()
