"""A Sass project compiled for real, before and after a partial moves: the compiled CSS must be
byte-identical, and a forgotten @use must fail both the baseline comparison and check_moves.
Skipped when the `sass` binary is not installed (CI installs it with npm)."""
import shutil
import unittest
from helpers import make_repo, git, run_script, write

SASS = shutil.which("sass")
PROJECT = {
    "src/scss/main.scss": '@use "partials/vars";\n@use "partials/buttons";\nbody { color: vars.$text; background: url(../img/bg.png); }\n',
    "src/scss/partials/_vars.scss": "$text: #222;\n$accent: #c00;\n",
    "src/scss/partials/_buttons.scss": '@use "vars";\n.btn { color: vars.$accent; }\n',
    "public/img/bg.png": "",
    "public/index.html": '<link rel="stylesheet" href="css/main.css"><img src="img/bg.png">\n',
    "docs/architecture-migration-plan.md": "| From | To | Note |\n|---|---|---|\n| src/scss/partials/_vars.scss | src/scss/tokens/_vars.scss | design tokens |\n",
}
BUILD = f"{SASS} --no-source-map src/scss/main.scss public/css/main.css" if SASS else ""


@unittest.skipUnless(SASS, "sass not installed")
class SassEndToEnd(unittest.TestCase):
    def setUp(self):
        self.r = make_repo(PROJECT)

    def compiled(self):
        return (self.r / "public/css/main.css").read_text()

    def test_moved_partial_with_updated_uses_compiles_identically(self):
        r = self.r
        code, out = run_script("baseline.py", r, "--extra", BUILD, "--out", r / "before.json")
        self.assertEqual(code, 0, out)
        before_css = self.compiled()
        (r / "src/scss/tokens").mkdir()
        git(r, "mv", "src/scss/partials/_vars.scss", "src/scss/tokens/_vars.scss")
        write(r, {"src/scss/main.scss": PROJECT["src/scss/main.scss"].replace('"partials/vars"', '"tokens/vars"'),
                  "src/scss/partials/_buttons.scss": '@use "../tokens/vars";\n.btn { color: vars.$accent; }\n'})
        code, out = run_script("check_moves.py", r, "--plan", "docs/architecture-migration-plan.md")
        self.assertEqual(code, 0, out)
        self.assertIn("compiled CSS", out)  # url(../img/bg.png) in the source is reported as uncertain, not broken
        code, out = run_script("baseline.py", r, "--extra", BUILD, "--out", r / "after.json", "--compare", r / "before.json")
        self.assertEqual(code, 0, out)
        self.assertEqual(before_css, self.compiled())

    def test_forgotten_use_fails_build_and_check(self):
        r = self.r
        run_script("baseline.py", r, "--extra", BUILD, "--out", r / "before.json")
        (r / "src/scss/tokens").mkdir()
        git(r, "mv", "src/scss/partials/_vars.scss", "src/scss/tokens/_vars.scss")
        write(r, {"src/scss/main.scss": PROJECT["src/scss/main.scss"].replace('"partials/vars"', '"tokens/vars"')})
        # _buttons.scss still says @use "vars"
        code, out = run_script("check_moves.py", r, "--plan", "docs/architecture-migration-plan.md")
        self.assertEqual(code, 1, out)
        self.assertIn("_buttons.scss:1  @import vars", out)
        code, out = run_script("baseline.py", r, "--extra", BUILD, "--out", r / "after.json", "--compare", r / "before.json")
        self.assertEqual(code, 2, out)


if __name__ == "__main__":
    unittest.main()
