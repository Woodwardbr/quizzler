# SPDX-License-Identifier: Apache-2.0
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
        self.fx.write_guide("listings.html", body, mode="review", extra=' data-pr="1" data-head-sha="' + "0" * 40 + '"')
        report = check.Report()
        check.check_guide(self.fx.onboard / "listings.html", self.fx.root, report, onboard_dir=self.fx.onboard)
        self.assertEqual(len(report.errors), 2, report.errors)
        self.assertIn("'return request.upper()'", report.errors[0])
        self.assertIn("exercise hello", report.errors[1])
        self.assertEqual(len(report.warnings), 1)
        self.assertIn("no data-src", report.warnings[0])

    def test_overview_and_plan_rules(self):
        from support import OVERVIEW
        no_overview = DEFAULT_BODY.replace(OVERVIEW, "")
        self.fx.write_guide("a.html", no_overview)
        bad_plan = OVERVIEW.replace('data-importance="high"', 'data-importance="huge"') \
            .replace("</tbody>", '<tr data-plan="ghost" data-importance="low" data-complexity="low" data-intensity="fast"><td>g</td></tr></tbody>') \
            .replace("</section>", '<div data-q="oq" data-answer="a"><p class="prompt">p</p><ol><li data-choice="a">a</li><li data-choice="b">b</li></ol></div></section>')
        self.fx.write_guide("b.html", DEFAULT_BODY.replace(OVERVIEW, bad_plan)
                            + '<section class="module" data-module="m2"><h2>2</h2><p data-depth="extreme">x</p></section>')
        errors = {}
        for guide in ("a.html", "b.html"):
            report = check.Report()
            check.check_guide(self.fx.onboard / guide, self.fx.root, report, onboard_dir=self.fx.onboard)
            errors[guide] = "\n".join(report.errors)
        self.assertIn("must start with an overview module", errors["a.html"])
        for expected in ("data-importance must be one of", "data-plan='ghost' is not a later module",
                         "data-intensity must be one of", "must not contain questions",
                         "no row for: m2", "data-depth must be one of"):
            self.assertIn(expected, errors["b.html"])

    def test_skeleton_rules(self):
        from support import OVERVIEW
        body = OVERVIEW + """
<section class="module" data-module="m1" data-written="skeleton"><h2>One</h2>
  <div data-q="q1" data-answer="a"><p class="prompt">P</p><ol><li data-choice="a">a</li><li data-choice="b">b</li></ol></div>
  <div class="exercise" data-exercise="hello"></div>
</section>"""
        self.fx.write_guide("s.html", body)
        self.fx.write_guide("d.html", OVERVIEW + '<section class="module" data-module="m1" data-written="skim"><h2>1</h2>'
                            '<p data-depth="standard">too deep</p></section>')
        self.fx.write_guide("w.html", OVERVIEW + '<section class="module" data-module="m1" data-written="mostly"><h2>1</h2></section>')
        errors = {}
        for guide in ("s.html", "d.html", "w.html"):
            report = check.Report()
            check.check_guide(self.fx.onboard / guide, self.fx.root, report, onboard_dir=self.fx.onboard)
            errors[guide] = "\n".join(report.errors)
        self.assertIn("skeleton and must not contain questions", errors["s.html"])
        self.assertIn("skeleton and must not contain exercises", errors["s.html"])
        self.assertIn("only written to 'skim'; raise data-written", errors["d.html"])
        self.assertIn("data-written must be one of", errors["w.html"])

    def test_final_and_summary_rules(self):
        from support import OVERVIEW
        final = ('<section class="module" data-module="final-review" data-final><h2>F</h2><table><tbody>'
                 '<tr data-refresh="m1" data-intensity="deep"><td>1</td></tr>'
                 '<tr data-refresh="ghost" data-intensity="hard"><td>g</td></tr></tbody></table></section>')
        m1 = '<section class="module" data-module="m1"><h2>1</h2></section>'
        summary = '<section class="module" data-module="summary" data-summary><h2>S</h2></section>'
        self.fx.write_guide("ok.html", OVERVIEW + m1 + final.replace(
            '<tr data-refresh="ghost" data-intensity="hard"><td>g</td></tr>', "") + summary, extra=' data-round="2"')
        self.fx.write_guide("bad.html", OVERVIEW + final + m1)
        errors = {}
        for guide in ("ok.html", "bad.html"):
            report = check.Report()
            check.check_guide(self.fx.onboard / guide, self.fx.root, report, onboard_dir=self.fx.onboard)
            errors[guide] = "\n".join(report.errors)
        self.assertEqual(errors["ok.html"], "")
        for expected in ("final review section must be the last module", "data-refresh='ghost' is not a study module",
                         "data-intensity must be one of"):
            self.assertIn(expected, errors["bad.html"])

    def test_area_rules(self):
        from support import OVERVIEW
        body = OVERVIEW.replace("</section>", '<p data-area="src/">ok</p><p data-area="nope/">x</p><p data-area=".git/">x</p></section>') + \
            '<section class="module" data-module="m1"><h2>1</h2><p data-area="src/">x</p></section>'
        self.fx.write_guide("a.html", body)
        report = check.Report()
        check.check_guide(self.fx.onboard / "a.html", self.fx.root, report, onboard_dir=self.fx.onboard)
        errors = "\n".join(report.errors)
        self.assertIn("'nope/' does not exist", errors)
        self.assertIn("'.git/' must be a visible repository path", errors)
        self.assertIn("data-area belongs in the overview", errors)
        self.assertEqual(len(report.errors), 3)

    def test_data_key_must_be_in_anchored_range(self):
        body = """
<section class="module" data-module="m1"><h2>1</h2>
  <code data-src="src/app.py" data-sym="Service.run" data-key='return "ok"'>ok</code>
  <code data-src="src/app.py" data-sym="Service.run" data-key="return request">wrong range</code>
  <code data-src="src/app.py" data-lines="4-5" data-key="def handler">ok</code>
  <figure class="code-listing" data-src="src/app.py" data-lines="4-5" data-key="nope"><pre>def handler(request):
    return request</pre></figure>
</section>"""
        self.fx.write_guide("k.html", body, mode="review", extra=' data-pr="1" data-head-sha="' + "0" * 40 + '"')
        report = check.Report()
        check.check_guide(self.fx.onboard / "k.html", self.fx.root, report, onboard_dir=self.fx.onboard)
        self.assertEqual(len(report.errors), 2, report.errors)
        self.assertIn("data-key 'return request' is not in the anchored lines 9-10", report.errors[0])
        self.assertIn("data-key 'nope' is not in the listing", report.errors[1])

    def test_review_guide_needs_sha(self):
        self.fx.write_guide("r.html", DEFAULT_BODY, mode="review")
        report = check.Report()
        check.check_guide(self.fx.onboard / "r.html", self.fx.root, report, onboard_dir=self.fx.onboard)
        self.assertIn("review guides need data-pr", "\n".join(report.errors))


if __name__ == "__main__":
    unittest.main()
