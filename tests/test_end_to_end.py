"""One complete migration on a small Python package, driven exactly as SKILL.md prescribes:
inventory → baseline before → branch → git mv + import edits → check_moves --in-progress →
baseline --compare → final check_moves → rollback. Then the same with an injected regression."""
import json
import unittest
from helpers import make_repo, git, run_script, write

PKG = {
    "setup.cfg": "[metadata]\nname = demo\n",
    "demo/__init__.py": "from .utils import slug\n",
    "demo/utils.py": "def slug(s):\n    return s.lower().replace(' ', '-')\n",
    "demo/cli.py": "from demo.utils import slug\n\ndef main():\n    print(slug('A B'))\n",
    "tests/test_slug.py": "from demo import slug\nfrom demo.utils import slug as s2\n\ndef test_slug():\n    assert slug('A B') == 'a-b' == s2('A B')\n",
    "Makefile": "run:\n\tpython -m demo.cli\n",
}
PLAN = "| From | To | Note |\n|---|---|---|\n| demo/utils.py | demo/core/text.py | leaf first |\n| demo/cli.py | demo/app/cli.py | entry point |\n"


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.r = make_repo(PKG)
        write(self.r, {"docs/architecture-migration-plan.md": PLAN})
        git(self.r, "add", "docs")
        git(self.r, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "plan")

    def step1(self):
        r = self.r
        (r / "demo/core").mkdir()
        git(r, "mv", "demo/utils.py", "demo/core/text.py")
        write(r, {"demo/core/__init__.py": "", "demo/__init__.py": "from .core.text import slug\n",
                  "demo/cli.py": "from demo.core.text import slug\n\ndef main():\n    print(slug('A B'))\n",
                  "tests/test_slug.py": PKG["tests/test_slug.py"].replace("demo.utils", "demo.core.text")})

    def step2(self):
        r = self.r
        (r / "demo/app").mkdir()
        git(r, "mv", "demo/cli.py", "demo/app/cli.py")
        write(r, {"demo/app/__init__.py": "", "Makefile": "run:\n\tpython -m demo.app.cli\n"})

    def test_full_migration_passes_every_gate_and_rolls_back(self):
        r = self.r
        ev = r / "docs/restructure-evidence"  # where SKILL.md puts every script output
        ev.mkdir()
        code, out = run_script("inventory.py", r, "--out", ev / "inventory.json")
        self.assertEqual(code, 0, out)
        self.assertIn("setup.cfg", json.dumps(json.loads((ev / "inventory.json").read_text())["path_pinning_files"]))
        code, out = run_script("baseline.py", r, "--only", "pytest,pytest-collect", "--out", r / "before.json")
        self.assertEqual(code, 0, out)
        git(r, "checkout", "-qb", "restructure/demo")
        self.step1()
        code, out = run_script("check_moves.py", r, "--plan", "docs/architecture-migration-plan.md", "--in-progress")
        self.assertEqual(code, 0, out)
        self.assertIn("1 pending row(s)", out)
        code, out = run_script("baseline.py", r, "--only", "pytest,pytest-collect", "--out", r / "step1.json", "--compare", r / "before.json")
        self.assertEqual(code, 0, out)
        git(r, "add", "-A")
        git(r, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "step 1")
        self.step2()
        code, out = run_script("check_moves.py", r, "--plan", "docs/architecture-migration-plan.md")
        self.assertEqual(code, 0, out)  # final: every row done, no stale ref, imports resolve
        code, out = run_script("baseline.py", r, "--only", "pytest,pytest-collect", "--out", r / "after.json", "--compare", r / "before.json")
        self.assertEqual(code, 0, out)
        self.assertIn("same", out)
        # rollback
        git(r, "add", "-A")
        git(r, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "step 2")
        git(r, "checkout", "-q", "master")
        self.assertTrue((r / "demo/utils.py").is_file())
        self.assertFalse((r / "demo/core").exists())

    def test_missed_import_edit_is_caught_by_both_gates(self):
        r = self.r
        run_script("baseline.py", r, "--only", "pytest", "--out", r / "before.json")
        self.step1()
        write(r, {"tests/test_slug.py": PKG["tests/test_slug.py"]})  # forgot to update the test import
        code, out = run_script("check_moves.py", r, "--plan", "docs/architecture-migration-plan.md", "--in-progress")
        self.assertEqual(code, 1, out)
        self.assertIn("tests/test_slug.py", out)
        code, out = run_script("baseline.py", r, "--only", "pytest", "--out", r / "step1.json", "--compare", r / "before.json")
        self.assertEqual(code, 2, out)
        self.assertIn("REGRESSION", out)

    def test_makefile_left_pointing_at_old_module_is_caught(self):
        r = self.r
        self.step1()
        self.step2()
        write(r, {"Makefile": PKG["Makefile"]})
        code, out = run_script("check_moves.py", r, "--plan", "docs/architecture-migration-plan.md")
        self.assertEqual(code, 1, out)
        self.assertIn("Makefile", out)


if __name__ == "__main__":
    unittest.main()
