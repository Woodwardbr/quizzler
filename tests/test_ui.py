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
        self.assertEqual(self.js("return [...document.querySelectorAll('.qz-mod-label')].map(l => l.firstChild.textContent)"),
                         ["Overview", "Module one", "Final review"])
        self.js("document.querySelector('.qz-plan-actions .qz-primary').click()")  # start from the overview
        self.wait("!document.getElementById('module-m1').hidden")

        self.answer("q1", "b", "sure")
        feedback = self.wait("document.querySelector('[data-q=q1] .qz-feedback').textContent")
        self.assertIn("Correct. Mastered", feedback)
        self.answer("q2", "b", "guess")
        self.assertIn("Gap", self.wait("document.querySelector('[data-q=q2] .qz-feedback').textContent"))
        self.assertEqual(self.js("return document.querySelectorAll('.qz-mod-status')[1].textContent"), "2/2")

        # Exercise: a prediction is required before running.
        self.js("document.querySelector('.qz-exercise .qz-primary').click()")
        self.assertIn("prediction first", self.js("return document.querySelector('.qz-exercise .qz-feedback').textContent"))
        self.js("""const t = document.querySelector('.qz-prediction'); t.value = 'hello 42';
                   t.dispatchEvent(new Event('input')); document.querySelector('.qz-exercise .qz-primary').click();""")
        self.wait("document.querySelector('.qz-output').textContent.includes('hello 42')")
        self.assertIn("matched exactly", self.js("return document.querySelector('.qz-exercise .qz-feedback').textContent"))

        # The exercise shows its own code from the file, with print lines marked.
        self.wait("document.querySelector('.qz-exercise .code-listing .l.out')")
        self.assertEqual(self.js("return document.querySelector('.qz-exercise .code-listing .l.out').textContent"),
                         'print("hello", 6 * 7)')
        self.assertEqual(self.js("return [...document.querySelectorAll('.qz-exercise .qz-step')].map(s => s.textContent)"),
                         ["1Read the code", "2Predict the output", "3Run it and compare"])

        # Literal anchor opens the docked source pane on the right line.
        self.js("document.querySelector('code[data-sym]').click()")
        self.wait("document.querySelector('.qz-line.qz-hl code') && document.querySelector('.qz-line.qz-hl code').textContent === '    def run(self):'")
        self.js("document.querySelector('code[data-ref]').click()")
        self.wait("document.querySelector('.qz-line.qz-hl code') && document.querySelector('.qz-line.qz-hl code').textContent === 'def handler(request):'")
        self.assertTrue(self.js("return document.querySelector('code[data-ref]').classList.contains('qz-ref-active')"))
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

    def test_study_plan_intensity_and_skip(self):
        fx = Fixture()
        self.addCleanup(fx.cleanup)
        from support import OVERVIEW
        overview = OVERVIEW.replace("</tbody>", '<tr data-plan="m2" data-importance="low" data-complexity="high" data-intensity="skim"><td>Two</td><td>y</td></tr></tbody>')
        body = overview + """
<section class="module" data-module="m1"><h2>One</h2>
  <p data-depth="standard" id="std">standard detail</p><p data-depth="deep" id="deep">deep detail</p>
  <div data-q="q1" data-answer="a"><p class="prompt">P</p><ol class="choices"><li data-choice="a">a</li><li data-choice="b">b</li></ol></div>
  <div data-q="q2" data-answer="a" data-depth="deep"><p class="prompt">P</p><ol class="choices"><li data-choice="a">a</li><li data-choice="b">b</li></ol></div>
</section>
<section class="module" data-module="m2"><h2>Two</h2>
  <div data-q="q3" data-answer="a"><p class="prompt">P</p><ol class="choices"><li data-choice="a">a</li><li data-choice="b">b</li></ol></div>
</section>"""
        fx.write_guide("guide.html", body)
        app = serve.GuideApp(fx.root, fx.onboard / "guide.html", onboard_dir=fx.onboard)
        self.serve(app)
        visible = "(id) => getComputedStyle(document.getElementById(id)).display !== 'none'"
        js_visible = lambda i: self.js(f"document.getElementById('module-m1').hidden = false; return ({visible})(arguments[0])", i)
        # Ratings and recommendations render; standard hides deep content and deep questions.
        self.assertEqual(self.js("return [...document.querySelectorAll('[data-plan=m1] .qz-rating')].map(r => r.textContent)"), ["high", "low"])
        self.assertTrue(js_visible("std"))
        self.assertFalse(js_visible("deep"))
        self.assertEqual(self.js("return document.querySelectorAll('.qz-mod-status')[1].textContent"), "0/1")
        # Deep shows everything and counts the deep question.
        self.js("const s = document.querySelector('[data-plan=m1] select'); s.value = 'deep'; s.dispatchEvent(new Event('change'))")
        self.assertTrue(js_visible("deep"))
        self.assertEqual(self.js("return document.querySelectorAll('.qz-mod-status')[1].textContent"), "0/2")
        # Skip drops m2 from the pager and progress.
        self.js("const s = document.querySelector('[data-plan=m2] select'); s.value = 'skip'; s.dispatchEvent(new Event('change'))")
        self.assertEqual(self.js("return document.querySelectorAll('.qz-mod-status')[2].textContent"), "skipped")
        self.js("document.querySelectorAll('.qz-modules a')[1].click()")
        self.js("document.querySelector('.qz-pager .qz-primary').click()")  # Next skips m2
        self.wait("!document.getElementById('module-final-review').hidden")
        self.js("[...document.querySelectorAll('.qz-btn')].find(b => b.textContent.startsWith('Export')).click()")
        code = self.wait("document.querySelector('.qz-dialog textarea') && document.querySelector('.qz-dialog textarea').value")
        exported = json.loads(code.split(" ", 1)[1])
        self.assertEqual(exported["plan"], {"m1": "deep", "m2": "skip"})
        self.assertEqual(exported["pending"], {})

    def test_skeleton_outline_and_pending_notice(self):
        fx = Fixture()
        self.addCleanup(fx.cleanup)
        from support import OVERVIEW
        overview = OVERVIEW.replace("</tbody>", '<tr data-plan="m2" data-importance="low" data-complexity="low" data-intensity="skim"><td>2</td><td>y</td></tr></tbody>')
        body = overview + """
<section class="module" data-module="m1" data-written="skim"><h2>One</h2><p>skim layer</p></section>
<section class="module" data-module="m2" data-written="skeleton"><h2>Two</h2><ol><li>outline item</li></ol></section>"""
        fx.write_guide("guide.html", body)
        app = serve.GuideApp(fx.root, fx.onboard / "guide.html", onboard_dir=fx.onboard)
        self.serve(app)
        statuses = "return [...document.querySelectorAll('.qz-mod-status')].map(s => s.textContent)"
        self.assertEqual(self.js(statuses), ["", "", "outline", ""])
        banners = "return [...document.querySelectorAll('.qz-pending-banner')].map(b => b.closest('section').dataset.module)"
        self.assertEqual(self.js(banners), ["m1", "m2"])  # m1 is written to skim but the plan says standard
        self.js("const s = document.querySelector('[data-plan=m1] select'); s.value = 'skim'; s.dispatchEvent(new Event('change'))")
        self.js("const s = document.querySelector('[data-plan=m2] select'); s.value = 'skip'; s.dispatchEvent(new Event('change'))")
        self.assertEqual(self.js(banners), [])
        self.assertEqual(self.js(statuses), ["", "", "skipped", ""])

    def test_opening_an_outline_asks_claude_and_reloads_when_written(self):
        import guidelib as gl
        fx = Fixture()
        self.addCleanup(fx.cleanup)
        from support import OVERVIEW
        body = OVERVIEW + '<section class="module" data-module="m1" data-written="skeleton"><h2>One</h2><ol><li>outline</li></ol></section>'
        guide = fx.write_guide("guide.html", body)
        app = serve.GuideApp(fx.root, guide, onboard_dir=fx.onboard)
        self.serve(app)
        self.js("document.querySelector('.qz-plan-actions .qz-primary').click()")  # Start the first module
        # The section shows it is being written straight away, before Claude starts.
        self.wait("document.querySelector('#module-m1 .qz-work')")
        self.assertIn("Writing this module", self.js("return document.querySelector('#module-m1 .qz-work').textContent"))
        self.assertEqual(self.js("return document.querySelector('.qz-modules a[data-index=\"1\"] .qz-mod-status').textContent"), "writing…")
        # Nobody is listening yet: the panel says so instead of spinning forever.
        self.wait("document.querySelector('.qz-work-stuck')", timeout=10)
        self.assertIn("No Claude session is listening", self.js("return document.querySelector('.qz-work').textContent"))
        # A listener picks it up: the panel moves to "Claude picked it up".
        import relay
        relay.heartbeat(app.requests_path)
        relay.claim(app.requests_path)
        self.wait("!document.querySelector('.qz-work-stuck') && document.querySelector('.qz-work .qz-step-done:nth-child(2)')", timeout=10)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not gl.update_requests(app.requests_path):
            time.sleep(0.1)
        queued = gl.update_requests(app.requests_path)
        self.assertEqual([(r["action"], r["module"], r["wanted"]) for r in queued], [("write-module", "m1", "standard")])
        self.assertEqual(queued[0]["status"], "working")
        # "Claude" writes the module; the page notices and reloads into it.
        time.sleep(0.05)
        guide.write_text(guide.read_text().replace('data-written="skeleton"><h2>One</h2><ol><li>outline</li></ol>',
                                                   'data-written="standard"><h2>One</h2><p id="written">Now written.</p>'))
        self.wait("document.getElementById('written') && !document.getElementById('module-m1').hidden", timeout=20)
        self.assertFalse(self.js("return Boolean(document.querySelector('.qz-pending-banner'))"))

    def test_final_review_round_trip(self):
        import guidelib as gl
        fx = Fixture()
        self.addCleanup(fx.cleanup)
        guide = fx.onboard / "guide.html"
        app = serve.GuideApp(fx.root, guide, onboard_dir=fx.onboard)
        self.serve(app)
        self.answer("q1", "b", "sure")
        self.js("[...document.querySelectorAll('.qz-modules a')].find(a => a.textContent.includes('Final review')).click()")
        self.wait("document.querySelector('#module-final-review table')")
        row = self.js("return [...document.querySelectorAll('#module-final-review tbody td')].map(td => td.textContent)")
        self.assertEqual(row, ["Module onestandard", "1 of 2", "1 mastered"])
        self.js("[...document.querySelectorAll('#module-final-review button')].find(b => b.textContent.includes('Generate')).click()")
        self.wait("document.querySelector('#module-final-review .qz-work')")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not gl.update_requests(app.requests_path):
            time.sleep(0.1)
        self.assertEqual([r["action"] for r in gl.update_requests(app.requests_path)], ["final-review"])
        # Claude writes the final review with a refresh plan; the page reloads into it.
        final = """<section class="module" data-module="final-review" data-final data-title="Final review"><h2>Final review</h2>
  <table><thead><tr><th>Module</th><th>Why</th></tr></thead><tbody>
  <tr data-refresh="m1" data-intensity="deep"><td>Module one</td><td>missed q2</td></tr></tbody></table></section>"""
        head, tail = guide.read_text().rsplit("</main>", 1)
        guide.write_text(head + final + "</main>" + tail)
        self.wait("document.querySelector('#module-final-review [data-refresh] select')", timeout=20)
        self.assertEqual(self.js("return document.querySelector('[data-refresh] select').value"), "deep")
        self.js("const s = document.querySelector('[data-refresh] select'); s.value = 'skim'; s.dispatchEvent(new Event('change'))")
        buttons = self.js("return [...document.querySelectorAll('.qz-final-actions button')].map(b => b.textContent)")
        self.assertEqual(buttons, ["Start round 2 with this plan", "I'm done: write my summary"])
        self.js("document.querySelector('.qz-final-actions .qz-primary').click()")
        self.wait("document.querySelector('#module-final-review .qz-work')")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and len(gl.update_requests(app.requests_path)) < 2:
            time.sleep(0.1)
        self.assertEqual(gl.update_requests(app.requests_path)[-1]["action"], "new-round")
        saved = json.loads(app.results_path.read_text())
        self.assertEqual((saved["refresh"], saved["round"]), ({"m1": "skim"}, 1))

    def test_code_pane_open_by_default_and_follows_scroll(self):
        fx = Fixture()
        self.addCleanup(fx.cleanup)
        filler = "".join(f"<p>Filler paragraph {i}.</p>" for i in range(60))
        body = f"""
<section class="module" data-module="m1"><h2>One</h2>
  <p><code data-src="src/app.py" data-ref="def handler(request):">handler</code></p>
  {filler}
  <p id="later"><code data-src="src/app.py" data-sym="Service.run">run</code></p>
  {filler}
</section>"""
        fx.write_guide("guide.html", body, mode="review", extra=' data-pr="1" data-head-sha="' + "0" * 40 + '"')
        app = serve.GuideApp(fx.root, fx.onboard / "guide.html", onboard_dir=fx.onboard)
        self.serve(app)
        self.wait("!document.getElementById('qz-code').hidden")
        self.wait("document.querySelector('.qz-line.qz-hl code') && document.querySelector('.qz-line.qz-hl code').textContent === 'def handler(request):'")
        self.assertTrue(self.js("return document.querySelector('code[data-ref]').classList.contains('qz-ref-active')"))
        scroll_before = self.js("return window.scrollY")
        self.js("window.scrollTo(0, document.getElementById('later').getBoundingClientRect().top + window.scrollY - 100)")
        self.wait("document.querySelector('.qz-line.qz-hl code') && document.querySelector('.qz-line.qz-hl code').textContent.includes('def run(self)')")
        self.assertTrue(self.js("return document.querySelector('code[data-sym]').classList.contains('qz-ref-active')"))
        self.assertGreater(self.js("return window.scrollY"), scroll_before)  # following never yanks the guide back
        # Closing pauses following; clicking a reference resumes it.
        self.js("[...document.querySelectorAll('.qz-code button')].find(b => b.textContent === 'Close').click()")
        self.js("window.scrollTo(0, 0)")
        time.sleep(0.5)
        self.assertTrue(self.js("return document.getElementById('qz-code').hidden"))
        self.js("document.querySelector('code[data-ref]').click()")
        self.wait("!document.getElementById('qz-code').hidden")

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
