import unittest

from support import DEFAULT_BODY, Fixture

import check


class DriftGateTest(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.cleanup()

    def run_check(self, guide="guide.html"):
        report = check.Report()
        check.check_guide(self.fx.onboard / guide, self.fx.root, report, onboard_dir=self.fx.onboard)
        check.check_exercises(self.fx.onboard / "exercises", self.fx.onboard / "expected", report)
        return report

    def test_clean_fixture_passes(self):
        report = self.run_check()
        self.assertEqual(report.errors, [])

    def test_detects_changed_expected_output(self):
        self.fx.write("docs/onboarding/expected/hello.out", "hello 41\n")
        errors = self.run_check().errors
        self.assertEqual(len(errors), 1)
        self.assertIn("output drifted", errors[0])
        self.assertIn("-hello 41", errors[0])
        self.assertIn("+hello 42", errors[0])

    def test_detects_changed_exercise_behaviour(self):
        self.fx.write("docs/onboarding/exercises/hello.py", '"""PREDICT: x"""\nprint("hello", 43)\n')
        self.assertIn("output drifted", self.run_check().errors[0])

    def test_update_expected_records_output(self):
        self.fx.write("docs/onboarding/expected/hello.out", "stale\n")
        report = check.Report()
        check.check_exercises(self.fx.onboard / "exercises", self.fx.onboard / "expected", report, update=True)
        self.assertEqual((self.fx.onboard / "expected/hello.out").read_text(), "hello 42\n")
        self.assertEqual(self.run_check().errors, [])

    def test_missing_expected_and_predict(self):
        self.fx.write("docs/onboarding/exercises/bare.py", "print(1)\n")
        errors = "\n".join(self.run_check().errors)
        self.assertIn("PREDICT", errors)
        self.assertIn("no recorded output", errors)

    def test_detects_broken_anchor_when_code_moves(self):
        self.fx.write("src/app.py", "def handle(request):\n    return request\n\nclass Service:\n    def start(self):\n        pass\n")
        errors = "\n".join(self.run_check().errors)
        self.assertIn("literal not found: 'def handler(request):'", errors)
        self.assertIn("symbol 'Service.run' not found", errors)

    def test_detects_ambiguous_anchor(self):
        self.fx.write("src/app.py", "def handler(request):\n    pass\ndef handler(request):\n    pass\nclass Service:\n    def run(self): pass\n")
        errors = "\n".join(self.run_check().errors)
        self.assertIn("ambiguous (2 matches at lines 1, 3)", errors)

    def test_contract_errors(self):
        body = """
<section class="module" data-module="m1"><h2>x</h2>
  <div data-q="q1" data-answer="z"><p class="prompt">Q</p><ol><li data-choice="a">a</li><li data-choice="b">b</li></ol></div>
  <div data-q="q1" data-answer="a"><ol><li data-choice="a">a</li><li data-choice="b">b</li></ol></div>
  <div class="exercise" data-exercise="nope"></div>
  <code data-src=".git/config" data-ref="x">x</code>
  <div class="findings"><article class="finding" data-finding="f1" data-src="src/app.py" data-lines="8-99"><h3>F</h3></article></div>
  <script>alert(1)</script>
</section>"""
        self.fx.write_guide("bad.html", body)
        report = check.Report()
        check.check_guide(self.fx.onboard / "bad.html", self.fx.root, report, onboard_dir=self.fx.onboard)
        errors = "\n".join(report.errors)
        for expected in ("duplicate question ids: q1", "data-answer='z' is not one of its choices",
                         "has no .prompt text", "exercise 'nope' not found", "hidden path refused",
                         "past end of file", "inline <script>"):
            self.assertIn(expected, errors)

    def test_guide_assets_must_point_at_engine(self):
        self.fx.write_guide("examples/nested.html", DEFAULT_BODY)
        self.assertEqual(self.run_check("examples/nested.html").errors, [])
        path = self.fx.onboard / "examples/nested.html"
        path.write_text(path.read_text().replace("../app/guide.js", "app/guide.js"))
        errors = "\n".join(self.run_check("examples/nested.html").errors)
        self.assertIn("does not point at docs/onboarding/app/", errors)

    def test_listings_are_checked_against_source(self):
        body = """
<section class="module" data-module="m1"><h2>x</h2>
  <figure class="code-listing" data-src="src/app.py" data-lines="4-5"><pre>def handler(request):
    return request</pre></figure>
  <figure class="code-listing" data-src="src/app.py" data-lines="4-5"><pre>def handler(request):
    return request.upper()</pre></figure>
  <figure class="code-listing" data-src="src/app.py"><pre>class Service:
    ...
        return "ok"</pre></figure>
  <figure class="code-listing"><pre>anything</pre></figure>
  <div class="exercise" data-exercise="hello"><figure class="code-listing"><pre><code><span class="l">print("hello", 6 * 7)</span></code></pre></figure></div>
  <div class="exercise" data-exercise="hello"><figure class="code-listing"><pre><code><span class="l">print("hi")</span></code></pre></figure></div>
</section>"""
        self.fx.write_guide("listings.html", body)
        report = check.Report()
        check.check_guide(self.fx.onboard / "listings.html", self.fx.root, report, onboard_dir=self.fx.onboard)
        self.assertEqual(len(report.errors), 2, report.errors)
        self.assertIn("'return request.upper()'", report.errors[0])
        self.assertIn("exercise hello", report.errors[1])
        self.assertEqual(len(report.warnings), 1)
        self.assertIn("no data-src", report.warnings[0])

    def test_review_guide_needs_sha(self):
        self.fx.write_guide("r.html", DEFAULT_BODY, mode="review")
        report = check.Report()
        check.check_guide(self.fx.onboard / "r.html", self.fx.root, report, onboard_dir=self.fx.onboard)
        self.assertIn("review guides need data-pr", "\n".join(report.errors))


if __name__ == "__main__":
    unittest.main()
