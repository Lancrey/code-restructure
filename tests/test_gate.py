"""gate.py: one command runs every gate; a forgotten import edit fails it, the fixed state passes."""
import unittest
from helpers import make_repo, git, run_script, write

REPO = {
    "setup.cfg": "", "demo/__init__.py": "", "demo/utils.py": "def slug(s):\n    return s.lower()\n",
    "tests/test_a.py": "from demo.utils import slug\n\ndef test_a():\n    assert slug('A') == 'a'\n",
    "docs/architecture-audit.md": "# audit\n",
    "docs/architecture-migration-plan.md": "| From | To | Note |\n|---|---|---|\n| demo/utils.py | demo/core/text.py | |\n",
}
ARGS = ["--plan", "docs/architecture-migration-plan.md", "--report-dir", "docs", "--only", "pytest,pytest-collect"]


class Gate(unittest.TestCase):
    def test_record_step_final(self):
        r = make_repo(REPO)
        code, out = run_script("gate.py", r, *ARGS, "--record-before")
        self.assertEqual(code, 0, out)
        for f in ("baseline-before.json", "references-before.json", "secrets-redacted.txt"):
            self.assertTrue((r / "docs/restructure-evidence" / f).is_file(), f)
        (r / "demo/core").mkdir()
        git(r, "mv", "demo/utils.py", "demo/core/text.py")
        write(r, {"demo/core/__init__.py": ""})
        code, out = run_script("gate.py", r, *ARGS, "--step", "1")
        self.assertEqual(code, 1, out)  # tests still import demo.utils
        self.assertIn("FAIL       moves", out)
        self.assertIn("FAIL       baseline", out)
        write(r, {"tests/test_a.py": REPO["tests/test_a.py"].replace("demo.utils", "demo.core.text")})
        code, out = run_script("gate.py", r, *ARGS, "--final")
        self.assertEqual(code, 0, out)
        self.assertIn("All gates passed", out)

    def test_step_without_before_is_refused(self):
        r = make_repo(REPO)
        code, out = run_script("gate.py", r, *ARGS, "--step", "1")
        self.assertEqual(code, 2, out)
        self.assertIn("--record-before", out)


if __name__ == "__main__":
    unittest.main()
