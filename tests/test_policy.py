# SPDX-License-Identifier: Apache-2.0
import os
import unittest

from support import Fixture

import guidelib as gl


class PathPolicyTest(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.policy = gl.PathPolicy(self.fx.root, ("docs/onboarding/local",))

    def tearDown(self):
        self.fx.cleanup()

    def refused(self, rel, code="forbidden"):
        with self.assertRaises(gl.PolicyError) as ctx:
            self.policy.check(rel)
        self.assertEqual(ctx.exception.code, code, rel)

    def test_allows_source(self):
        self.assertEqual(self.policy.check("src/app.py"), (self.fx.root / "src/app.py").resolve())

    def test_rejects_traversal_and_absolute(self):
        for rel in ("../outside.py", "src/../../x.py", "src/./app.py", "/etc/passwd", "src//app.py",
                    "C:/x.py", "src\\app.py", "", "src/app.py\x00"):
            self.refused(rel)

    def test_rejects_hidden_and_git(self):
        self.fx.write(".git/config", "[core]\n")
        self.fx.write("config/.env", "SECRET=1\n")
        self.fx.write(".github/workflows/ci.yml", "on: push\n")
        for rel in (".git/config", "config/.env", ".github/workflows/ci.yml"):
            self.refused(rel)

    def test_rejects_secret_names_regardless_of_extension(self):
        for rel in ("deploy/env.storage.json", "deploy/env.storage.py", "deploy/ENV.STORAGE.yaml",
                    "certs/server.pem", "keys/id_rsa.txt", "config/credentials.json",
                    "secrets/settings.py", "infra/prod.env", "infra/main.tfvars"):
            self.fx.write(rel, "x\n")
            self.refused(rel)

    def test_rejects_local_state_and_unknown_types(self):
        self.fx.write("docs/onboarding/local/guide.html", "<p>x</p>")
        self.fx.write("assets/logo.png", "png")
        self.refused("docs/onboarding/local/guide.html")
        self.refused("assets/logo.png")

    def test_missing_file_is_not_found(self):
        self.refused("src/missing.py", code="not_found")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unsupported")
    def test_symlink_escape_and_symlink_to_hidden(self):
        outside = self.fx.tmp / "outside.py"
        outside.write_text("print('outside')\n")
        os.symlink(outside, self.fx.root / "src" / "escape.py")
        self.refused("src/escape.py")
        self.fx.write(".git/config", "[core]\n")
        os.symlink(self.fx.root / ".git" / "config", self.fx.root / "src" / "notes.md")
        self.refused("src/notes.md")
        os.symlink(self.fx.root / "src", self.fx.root / "alias")
        self.assertTrue(self.policy.allowed("alias/app.py"))

    def test_walk_lists_only_allowed(self):
        self.fx.write(".git/config", "x")
        self.fx.write("node_modules/pkg/index.js", "x")
        self.fx.write("deploy/env.storage.json", "x")
        files = self.policy.walk()
        self.assertIn("src/app.py", files)
        self.assertIn("docs/onboarding/guide.html", files)
        for bad in (".git/config", "node_modules/pkg/index.js", "deploy/env.storage.json"):
            self.assertNotIn(bad, files)


class AnchorTest(unittest.TestCase):
    TEXT = "a = 1\ndef f():\n    return None\n\ndef g():\n    return None\n"

    def test_unique_anchor(self):
        self.assertEqual(gl.resolve_anchor(self.TEXT, "def g():"), {"line": 5, "end_line": 5, "matches": 1})

    def test_multiline_anchor(self):
        self.assertEqual(gl.resolve_anchor(self.TEXT, "def f():\n    return")["end_line"], 3)

    def test_ambiguous_and_missing(self):
        with self.assertRaisesRegex(gl.AnchorError, "ambiguous"):
            gl.resolve_anchor(self.TEXT, "return None")
        self.assertEqual(gl.resolve_anchor(self.TEXT, "return None", 2)["line"], 6)
        with self.assertRaisesRegex(gl.AnchorError, "only 2"):
            gl.resolve_anchor(self.TEXT, "return None", 3)
        with self.assertRaisesRegex(gl.AnchorError, "not found"):
            gl.resolve_anchor(self.TEXT, "def h():")

    def test_symbols(self):
        py = gl.find_symbols("x.py", "class A:\n    def m(self):\n        pass\nCONST = 1\n")
        self.assertEqual([s["name"] for s in py], ["A", "A.m", "CONST"])
        self.assertEqual(gl.lookup_symbol(py, "m")["line"], 2)
        js = gl.find_symbols("x.js", "export function go() {}\nconst helper = () => 1;\nclass Box {}\n")
        self.assertEqual({s["name"] for s in js}, {"go", "helper", "Box"})


class ClassifyTest(unittest.TestCase):
    def test_four_classes(self):
        self.assertEqual(gl.classify(True, "sure"), "mastered")
        self.assertEqual(gl.classify(True, "guess"), "fragile")
        self.assertEqual(gl.classify(True, "unsure"), "fragile")
        self.assertEqual(gl.classify(False, "sure"), "misconception")
        self.assertEqual(gl.classify(False, "guess"), "gap")
        self.assertEqual(gl.classify(False, "unsure"), "gap")

    def test_result_code(self):
        kind, payload = gl.parse_result_code('QUIZZLER-ONBOARD {"guide": "x"}')
        self.assertEqual((kind, payload), ("onboard", {"guide": "x"}))
        with self.assertRaises(ValueError):
            gl.parse_result_code("OTHER-ONBOARD {}")
        with self.assertRaises(ValueError):
            gl.parse_result_code("QUIZZLER-REVIEW [1]")


if __name__ == "__main__":
    unittest.main()
