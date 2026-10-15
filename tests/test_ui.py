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

    def answer(self, qid, choice, confidence=None):
        """Pick an answer (and optionally a confidence), then press the module's Check answers."""
        self.js("""
            const q = document.querySelector(`[data-q="${arguments[0]}"]`);
            q.querySelector(`.qz-choices input[value="${arguments[1]}"]`).click();
            if (arguments[2]) q.querySelector(`.qz-q-form input[value="${arguments[2]}"]`).click();
            q.closest('section').querySelector('.qz-quiz-actions button').click();
        """, qid, choice, confidence)

    def test_onboarding_flow(self):
        fx = Fixture()
        self.addCleanup(fx.cleanup)
        app = serve.GuideApp(fx.root, fx.onboard / "guide.html", onboard_dir=fx.onboard)
        self.serve(app)
        self.assertEqual(self.js("return [...document.querySelectorAll('.qz-mod-label')].map(l => l.firstChild.textContent)"),
                         ["Overview", "Module one", "Final review"])
        self.js("document.querySelectorAll('.qz-modules a')[1].click()")
        self.wait("!document.getElementById('module-m1').hidden")

        self.answer("q1", "b", "sure")
        feedback = self.wait("document.querySelector('[data-q=q1] .qz-feedback').textContent")
        self.assertIn("Correct. Mastered", feedback)
        # q2 has no answer yet: Check answers says so and stays available.
        self.assertIn("1 question has no answer yet", self.js("return document.querySelector('.qz-quiz-actions .qz-feedback').textContent"))
        self.assertFalse(self.js("return document.querySelector('.qz-quiz-actions').hidden"))
        self.answer("q2", "b")  # confidence is optional
        self.assertIn("Gap", self.wait("document.querySelector('[data-q=q2] .qz-feedback').textContent"))
        self.assertTrue(self.js("return document.querySelector('.qz-quiz-actions').hidden"))
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
        self.assertEqual((data["answers"]["q1"]["confidence"], data["answers"]["q2"]["confidence"]), ("sure", ""))
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

    def test_generate_modules_button_and_reload_on_done(self):
        import guidelib as gl
        import relay
        fx = Fixture()
        self.addCleanup(fx.cleanup)
        from support import OVERVIEW
        overview = OVERVIEW.replace("</tbody>", '<tr data-plan="m2" data-importance="low" data-complexity="low" data-intensity="skim"><td>2</td><td>y</td></tr></tbody>')
        body = overview + ('<section class="module" data-module="m1" data-written="skeleton"><h2>One</h2><ol><li>outline</li></ol></section>'
                           '<section class="module" data-module="m2" data-written="skeleton"><h2>Two</h2><ol><li>outline</li></ol></section>')
        guide = fx.write_guide("guide.html", body)
        app = serve.GuideApp(fx.root, guide, onboard_dir=fx.onboard)
        self.serve(app)
        # Opening a module never asks Claude for anything.
        self.assertFalse(self.js("return [...document.querySelectorAll('button')].some(b => b.textContent.startsWith('Start the first'))"))
        self.js("document.querySelectorAll('.qz-modules a')[1].click()")
        self.wait("!document.getElementById('module-m1').hidden")
        time.sleep(0.5)
        self.assertEqual(gl.update_requests(app.requests_path), [])
        self.assertIn("Go to the overview to generate modules", self.js("return document.querySelector('#module-m1 .qz-pending-banner').textContent"))
        # Generate modules asks for every pending module at its chosen depth, in one request.
        self.js("document.querySelectorAll('.qz-modules a')[0].click()")
        # The big button is the last thing in the overview.
        self.assertTrue(self.js("return document.getElementById('module-overview').lastElementChild.classList.contains('qz-generate')"))
        self.assertIn("Writes 2 modules", self.js("return document.querySelector('.qz-generate').textContent"))
        self.js("document.querySelector('.qz-generate button').click()")
        self.wait("document.querySelector('#module-overview .qz-work') && document.querySelector('#module-m2 .qz-work')")
        statuses = self.js("return [...document.querySelectorAll('.qz-mod-status')].map(s => s.textContent)")
        self.assertEqual(statuses[1:3], ["writing…", "writing…"])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not gl.update_requests(app.requests_path):
            time.sleep(0.1)
        queued = gl.update_requests(app.requests_path)
        self.assertEqual([(r["action"], r["modules"]) for r in queued], [("write-modules", {"m1": "standard", "m2": "skim"})])
        # Claude picks it up and writes m1 first: the page must not reload mid-write.
        relay.heartbeat(app.requests_path)
        relay.claim(app.requests_path)
        self.wait("document.querySelector('#module-overview .qz-work .qz-step-done:nth-child(2)')", timeout=10)
        self.js("window.__qzMarker = 1")
        time.sleep(0.05)
        guide.write_text(guide.read_text().replace('data-written="skeleton"><h2>One</h2><ol><li>outline</li></ol>',
                                                   'data-written="standard"><h2>One</h2><p id="w1">Written.</p>'))
        time.sleep(5)
        self.assertEqual(self.js("return window.__qzMarker"), 1)  # still the same page: no flicker
        guide.write_text(guide.read_text().replace('data-written="skeleton"><h2>Two</h2><ol><li>outline</li></ol>',
                                                   'data-written="skim"><h2>Two</h2><p id="w2">Written.</p>'))
        relay.mark_done(app.requests_path, [queued[0]["id"]])
        self.wait("document.getElementById('w1') && document.getElementById('w2') && !window.__qzMarker", timeout=20)
        self.assertFalse(self.js("return Boolean(document.querySelector('.qz-work, .qz-pending-banner'))"))

    def test_help_with_this_module(self):
        import guidelib as gl
        fx = Fixture()
        self.addCleanup(fx.cleanup)
        app = serve.GuideApp(fx.root, fx.onboard / "guide.html", onboard_dir=fx.onboard)
        self.serve(app)
        self.js("document.querySelectorAll('.qz-modules a')[1].click()")
        self.js("[...document.querySelectorAll('.qz-pager button')].find(b => b.textContent === 'Help with this module').click()")
        text = self.wait("!document.querySelector('.qz-help').hidden && document.querySelector('.qz-help').textContent")
        self.assertIn("What do you need help with?", text)
        self.assertIn("move on to the next module and come back", text)
        self.js("""const t = document.querySelector('.qz-help textarea'); t.value = 'Why does run return ok?';
                   [...document.querySelectorAll('.qz-help button')].find(b => b.textContent === 'Send').click();""")
        self.wait("document.querySelector('#module-m1 .qz-work')")
        self.assertIn("Rewriting this module for you", self.js("return document.querySelector('#module-m1 .qz-work').textContent"))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not gl.update_requests(app.requests_path):
            time.sleep(0.1)
        queued = gl.update_requests(app.requests_path)
        self.assertEqual([(r["action"], r["module"], r["message"]) for r in queued], [("help-module", "m1", "Why does run return ok?")])
        self.assertEqual(self.js("return [...document.querySelectorAll('.qz-pager button')].find(b => b.textContent.startsWith('Help')).textContent"),
                         "Help requested…")

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
        # Before the review is written there is no per-question breakdown.
        self.assertFalse(self.js("return Boolean(document.querySelector('#module-final-review .qz-result'))"))
        # The button says what it does and what comes after.
        gen = self.js("return document.querySelector('.qz-generate-review').textContent")
        self.assertIn("keep learning", gen)
        self.assertIn("call it a day", gen)
        self.js("[...document.querySelectorAll('#module-final-review button')].find(b => b.textContent === 'Generate review').click()")
        self.wait("document.querySelector('#module-final-review .qz-work')")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not gl.update_requests(app.requests_path):
            time.sleep(0.1)
        self.assertEqual([r["action"] for r in gl.update_requests(app.requests_path)], ["final-review"])
        # Claude writes the final review with a refresh plan; the page reloads into it.
        final = """<section class="module" data-module="final-review" data-final data-title="Final review"><h2>Final review</h2>
  <div class="key-points"><h3>Verdict</h3><ul><li>Good start.</li></ul></div>
  <h3 class="got">What you've got</h3><ul><li class="got">The handler.</li></ul>
  <h3 class="missed">What you missed</h3><ul><li class="missed">You skipped q2.<span class="right-idea">Pick a.</span></li></ul>
  <h3 class="plan">Refresh plan</h3>
  <table><thead><tr><th>Module</th><th>Why</th></tr></thead><tbody>
  <tr data-refresh="m1" data-intensity="deep"><td>Module one</td><td>missed q2</td></tr></tbody></table></section>"""
        head, tail = guide.read_text().rsplit("</main>", 1)
        guide.write_text(head + final + "</main>" + tail)
        import relay
        relay.mark_done(app.requests_path, None)  # Claude marks the request done; only then does the page reload
        self.wait("document.querySelector('#module-final-review [data-refresh] select')", timeout=20)
        self.assertEqual(self.js("return document.querySelector('[data-refresh] select').value"), "deep")
        cards = self.js("return [...document.querySelectorAll('#module-final-review > .qz-review-card')].map(c => [c.className, c.querySelector('h3').textContent])")
        self.assertEqual(cards, [["qz-review-card qz-tone-neutral", "Question by question"],
                                 ["qz-review-card qz-tone-got", "What you've got"],
                                 ["qz-review-card qz-tone-missed", "What you missed"],
                                 ["qz-review-card qz-tone-plan", "Refresh plan"]])
        self.assertTrue(self.js("return Boolean(document.querySelector('.qz-tone-plan [data-refresh] select'))"))
        # The written review includes the colour-coded question-by-question card.
        results = self.js("return [...document.querySelectorAll('#module-final-review .qz-result')].map(r => [r.dataset.result, r.className])")
        self.assertEqual([r[0] for r in results], ["mastered", "unanswered"])
        self.assertIn("qz-result-got", results[0][1])
        self.assertEqual(self.js("return getComputedStyle(document.querySelector('li.missed'), '::before').content"), '"✗"')
        self.js("const s = document.querySelector('[data-refresh] select'); s.value = 'skim'; s.dispatchEvent(new Event('change'))")
        buttons = self.js("return [...document.querySelectorAll('.qz-final-actions button')].map(b => b.textContent)")
        self.assertEqual(buttons, ["Write my final report", "Start round 2"])
        self.assertIn("Call it a day", self.js("return document.querySelector('.qz-final-actions').textContent"))
        self.js("[...document.querySelectorAll('.qz-final-actions button')].find(b => b.textContent === 'Start round 2').click()")
        self.wait("document.querySelector('#module-final-review .qz-work')")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and len(gl.update_requests(app.requests_path)) < 2:
            time.sleep(0.1)
        self.assertEqual(gl.update_requests(app.requests_path)[-1]["action"], "new-round")
        saved = json.loads(app.results_path.read_text())
        self.assertEqual((saved["refresh"], saved["round"]), ({"m1": "skim"}, 1))

    def test_overview_shows_repository_map(self):
        fx = Fixture()
        self.addCleanup(fx.cleanup)
        from support import OVERVIEW
        fx.write("src/util/helpers.py", "def helper():\n    return 1\n")
        overview = OVERVIEW.replace("<h2>Overview</h2>", """<h2>Overview</h2>
  <table><thead><tr><th>Path</th><th>What</th></tr></thead><tbody>
    <tr data-area="src/" id="area-src"><td>src/</td><td>Application code.</td></tr>
    <tr data-area="src/util/"><td>src/util/</td><td>Small helpers.</td></tr>
  </tbody></table>""")
        fx.write_guide("guide.html", overview + """
<section class="module" data-module="m1"><h2>One</h2><p><code data-src="src/app.py" data-ref="def handler(request):">handler</code></p></section>""")
        app = serve.GuideApp(fx.root, fx.onboard / "guide.html", onboard_dir=fx.onboard)
        self.serve(app)
        # On the overview the pane is a repository map with descriptions.
        self.wait("document.querySelector('.qz-map')")
        self.assertEqual(self.js("return document.querySelector('.qz-code-title').textContent"), "Repository map")
        self.assertIn("Application code.", self.js("return document.querySelector('li[data-path=\"src\"] .qz-map-desc').textContent"))
        self.assertTrue(self.js("return document.querySelector('li[data-path=\"src/util\"] > details').open"))
        # Clicking a table row highlights its folder; filtering narrows the tree.
        self.js("document.getElementById('area-src').click()")
        self.wait("document.querySelector('li[data-path=\"src\"].qz-map-hl')")
        self.js("const f = document.querySelector('.qz-map-filter'); f.value = 'helpers'; f.dispatchEvent(new Event('input'))")
        self.assertTrue(self.js("return document.querySelector('li[data-path=\"src/app.py\"]').hidden"))
        self.assertFalse(self.js("return document.querySelector('li[data-path=\"src/util/helpers.py\"]').hidden"))
        # Opening a file keeps a way back to the map, and auto-follow does not undo it.
        self.js("document.querySelector('li[data-path=\"src/util/helpers.py\"] button').click()")
        self.wait("document.querySelector('.qz-code-title').textContent === 'src/util/helpers.py'")
        time.sleep(1)
        self.assertEqual(self.js("return document.querySelector('.qz-code-title').textContent"), "src/util/helpers.py")
        self.js("[...document.querySelectorAll('.qz-code button')].find(b => b.textContent.includes('Repository map')).click()")
        self.wait("document.querySelector('.qz-map')")
        # Leaving the overview switches the pane back to code.
        self.js("document.querySelectorAll('.qz-modules a')[1].click()")
        self.wait("document.querySelector('.qz-code-title').textContent === 'src/app.py'")

    def test_light_dark_switch(self):
        fx = Fixture()
        self.addCleanup(fx.cleanup)
        app = serve.GuideApp(fx.root, fx.onboard / "guide.html", onboard_dir=fx.onboard)
        self.serve(app)
        bg = "return getComputedStyle(document.body).backgroundColor"
        button = "document.querySelector('.qz-top-actions .qz-theme-toggle')"
        self.assertTrue(self.js(f"return {button} === document.querySelector('.qz-top-actions').lastElementChild"))
        start = self.js("return document.documentElement.dataset.theme || 'system'")
        first_bg = self.js(bg)
        self.js(f"{button}.click()")
        theme = self.js("return document.documentElement.dataset.theme")
        self.assertIn(theme, ("light", "dark"))
        self.assertNotEqual(self.js(bg), first_bg)
        self.js(f"{button}.click()")
        other = self.js("return document.documentElement.dataset.theme")
        self.assertNotEqual(other, theme)
        # The choice is saved with progress and survives a reload on a new port.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not (app.results_path.exists() and other in app.results_path.read_text()):
            time.sleep(0.1)
        self.assertEqual(json.loads(app.results_path.read_text())["prefs"], {"theme": other})
        self.js("localStorage.clear()")
        self.browser._cmd("WebDriver:Navigate", {"url": app.url})
        self.wait("document.documentElement.classList.contains('qz-ready')")
        self.assertEqual(self.js("return document.documentElement.dataset.theme"), other)

    def test_code_pane_colours_and_concept_focus(self):
        fx = Fixture()
        self.addCleanup(fx.cleanup)
        fx.write("src/svc.py", "\n".join([
            "import os",
            "",
            "",
            "def fetch(url, retries=3):",
            '    """Fetch with retries."""',
            "    attempt = 0",
            "    while attempt < retries:",
            "        attempt += 1",
            "        if os.environ.get('OFFLINE'):",
            "            return None  # give up quietly",
            "    return url",
            ""]))
        body = """
<section class="module" data-module="m1"><h2>One</h2>
  <ul><li><code data-src="src/svc.py" data-sym="fetch" data-key="while attempt &lt; retries:">fetch</code> loops while
    <code>attempt</code> is below <code>retries</code>.</li></ul>
  <figure class="code-listing" data-src="src/svc.py" data-lines="4-6" data-key="attempt = 0"><pre>def fetch(url, retries=3):
    \"\"\"Fetch with retries.\"\"\"
    attempt = 0</pre></figure>
</section>"""
        fx.write_guide("guide.html", body, mode="review", extra=' data-pr="1" data-head-sha="' + "0" * 40 + '"')
        app = serve.GuideApp(fx.root, fx.onboard / "guide.html", onboard_dir=fx.onboard)
        self.serve(app)
        self.js("document.querySelector('code[data-sym]').click()")
        self.wait("document.querySelector('.qz-line[data-line=\"4\"] .tk-kw')")
        line = lambda n, sel: self.js(f"return [...document.querySelectorAll('.qz-line[data-line=\"{n}\"] {sel}')].map(e => e.textContent)")
        # Syntax colours.
        self.assertEqual(line(4, ".tk-kw"), ["def"])
        self.assertEqual(line(4, ".tk-def"), ["fetch"])
        self.assertEqual(line(4, ".tk-num"), ["3"])
        self.assertEqual(line(5, ".tk-str"), ['"""Fetch with retries."""'])
        self.assertEqual(line(10, ".tk-com"), ["# give up quietly"])
        self.assertEqual(line(10, ".tk-const"), ["None"])
        # The whole function is the range, but only key lines are strong.
        classes = self.js("return Object.fromEntries([...document.querySelectorAll('.qz-line')].map(l => [l.dataset.line, l.className]))")
        self.assertIn("qz-hl", classes["8"])
        self.assertIn("qz-key", classes["7"].split())
        self.assertNotIn("qz-key", classes["8"].split())
        self.assertNotIn("qz-hl", classes["2"])
        # Concept identifiers from the sentence are marked and listed as chips.
        self.assertEqual(self.js("return [...document.querySelectorAll('.qz-focus-chip')].map(c => c.textContent)"),
                         ["fetch", "attempt", "retries"])
        self.assertGreaterEqual(len(line(8, ".tk-focus")), 1)
        self.js("[...document.querySelectorAll('.qz-focus-chip')].find(c => c.textContent === 'retries').click()")
        self.assertEqual(self.js("return document.querySelector('.tk-flash').textContent"), "retries")
        # Listings in the guide get the same colours and key lines.
        self.assertEqual(self.js("return document.querySelector('.code-listing .tk-kw').textContent"), "def")
        self.assertTrue(self.js("return [...document.querySelectorAll('.code-listing .l')][2].classList.contains('qz-key')"))

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
