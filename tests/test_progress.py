import json
import unittest

from support import Fixture

import check
import guidelib as gl
import progress


class AdaptiveLoopTest(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.guide_path = self.fx.write_guide("local/guide.html", guide_id="fixture-local")
        self.local = self.fx.onboard / "local"
        self.payload = {
            "kind": "onboard", "guide": "fixture-local", "version": 1,
            "answers": {
                "q1": {"choice": "b", "confidence": "sure", "correct": False},  # correct field is recomputed
                "q2": {"choice": "b", "confidence": "sure", "reasoning": "I thought b"},
            },
            "exercises": {"hello": {"prediction": "hello 41", "ran": True, "matched": False}},
        }

    def tearDown(self):
        self.fx.cleanup()

    def test_full_loop_produces_next_version(self):
        guide = gl.load_guide(self.guide_path)
        directory = progress.progress_dir_for(self.guide_path, guide.guide_id, self.local)
        self.assertEqual(directory, self.guide_path.parent)
        record = progress.record_results(directory, self.payload, guide)
        self.assertEqual(record["summary"]["questions"]["mastered"], ["q1"])
        self.assertEqual(record["summary"]["questions"]["misconception"], ["q2"])
        self.assertEqual(record["summary"]["exercise_predictions_missed"], ["hello"])
        md = (directory / "progress.md").read_text()
        self.assertIn("| q2 | m1 | chose b, answer a | sure | misconception |", md)

        text = progress.report(directory, guide)
        self.assertIn("MISCONCEPTION q2 (chose b, answer a)", text)
        self.assertIn("reasoning: I thought b", text)
        self.assertIn("Retire in the next version: q1", text)

        result = progress.bump(self.guide_path, directory, self.local)
        self.assertEqual((result["version"], result["retired"]), (2, ["q1"]))
        self.assertTrue((self.guide_path.parent / "guide.v1.html").is_file())
        bumped = gl.load_guide(self.guide_path)
        self.assertEqual(bumped.version, 2)
        self.assertEqual(bumped.question("q1").retired, "v1:mastered")
        self.assertIsNone(bumped.question("q2").retired)
        report = check.Report()
        check.check_guide(self.guide_path, self.fx.root, report, onboard_dir=self.fx.onboard)
        self.assertEqual(report.errors, [])

        # A second round on v2 keeps v1 history and does not re-retire.
        second = dict(self.payload, version=2, answers={"q2": {"choice": "a", "confidence": "sure"}})
        record = progress.record_results(directory, second, bumped)
        self.assertEqual([h["version"] for h in record["progress"]["history"]], [1, 2])
        self.assertEqual(set(record["progress"]["retired"]), {"q1", "q2"})
        result = progress.bump(self.guide_path, directory, self.local)
        self.assertEqual((result["version"], result["retired"]), (3, ["q2"]))

    def test_tracked_guide_progress_goes_under_local(self):
        tracked = self.fx.onboard / "guide.html"
        directory = progress.progress_dir_for(tracked, "fixture", self.local)
        self.assertEqual(directory, self.local / "progress" / "fixture")
        progress.record_results(directory, dict(self.payload, guide="fixture"), gl.load_guide(tracked))
        result = progress.bump(tracked, directory, self.local)
        self.assertEqual(result["archived"], str(self.local / "versions" / "fixture.v1.html"))

    def test_cli_rejects_wrong_guide(self):
        code = "QUIZZLER-ONBOARD " + json.dumps(dict(self.payload, guide="someone-else"))
        with self.assertRaisesRegex(SystemExit, "is for guide 'someone-else'"):
            progress.main(["--guide", str(self.guide_path), "ingest", code])


if __name__ == "__main__":
    unittest.main()
