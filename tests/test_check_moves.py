import json
import unittest
from helpers import make_repo, git, run_script, write


def moved(root, src, dst):
    (root / dst).parent.mkdir(parents=True, exist_ok=True)
    git(root, "mv", src, dst)


class CheckMoves(unittest.TestCase):
    def test_python_absolute_import_left_behind(self):
        r = make_repo({"app/utils.py": "def f(): pass\n", "app/main.py": "from app.utils import f\n", "app/__init__.py": ""})
        moved(r, "app/utils.py", "app/core/text.py")
        code, out = run_script("check_moves.py", r, "--moves", "app/utils.py=app/core/text.py")
        self.assertEqual(code, 1, out)
        self.assertIn("app/main.py", out)

    def test_python_absolute_import_updated_is_clean(self):
        r = make_repo({"app/utils.py": "def f(): pass\n", "app/main.py": "from app.utils import f\n", "app/__init__.py": ""})
        moved(r, "app/utils.py", "app/core/text.py")
        write(r, {"app/main.py": "from app.core.text import f\n"})
        code, out = run_script("check_moves.py", r, "--moves", "app/utils.py=app/core/text.py")
        self.assertEqual(code, 0, out)

    def test_ts_relative_import_left_behind(self):
        r = make_repo({
            "src/components/widget.ts": "export const w = 1;\n",
            "src/components/index.ts": "import { w } from './widget';\n",
        })
        moved(r, "src/components/widget.ts", "src/widgets/widget.ts")
        code, out = run_script("check_moves.py", r, "--moves", "src/components/widget.ts=src/widgets/widget.ts")
        self.assertEqual(code, 1, out)
        self.assertIn("src/components/index.ts:1", out)
        self.assertIn("src/widgets/widget.ts", out)  # hint where it went

    def test_ts_moved_file_own_relative_import_breaks(self):
        r = make_repo({"src/a/x.ts": "import { y } from './y';\n", "src/a/y.ts": "export const y = 1;\n"})
        moved(r, "src/a/x.ts", "src/b/x.ts")
        code, out = run_script("check_moves.py", r, "--moves", "src/a/x.ts=src/b/x.ts")
        self.assertEqual(code, 1, out)
        self.assertIn("src/b/x.ts:1", out)

    def test_ts_two_imports_one_line_both_checked(self):
        r = make_repo({"src/a/w.ts": "", "src/a/u.ts": "", "src/a/i.ts": "import a from './w'; import b from './u';\n"})
        moved(r, "src/a/w.ts", "src/c/w.ts")
        moved(r, "src/a/u.ts", "src/c/u.ts")
        code, out = run_script("check_moves.py", r, "--moves", "src/a/w.ts=src/c/w.ts", "--moves", "src/a/u.ts=src/c/u.ts")
        self.assertEqual(code, 1, out)
        self.assertEqual(out.count("src/a/i.ts:1"), 2, out)

    def test_python_relative_import_breaks(self):
        r = make_repo({"pkg/__init__.py": "", "pkg/mod.py": "from .helpers import f\n", "pkg/helpers.py": "def f(): pass\n"})
        moved(r, "pkg/helpers.py", "pkg/lib/helpers.py")
        code, out = run_script("check_moves.py", r, "--moves", "pkg/helpers.py=pkg/lib/helpers.py")
        self.assertEqual(code, 1, out)
        self.assertIn("pkg/mod.py:1", out)

    def test_allowlisted_shim_is_accepted(self):
        r = make_repo({"app/utils.py": "def f(): pass\n", "app/shim.py": "# re-export from app.utils\n"})
        moved(r, "app/utils.py", "app/core/text.py")
        code, out = run_script("check_moves.py", r, "--moves", "app/utils.py=app/core/text.py", "--allow", "app/shim.py")
        self.assertEqual(code, 0, out)

    def test_two_stale_refs_on_one_line_both_reported(self):
        r = make_repo({"old/one.py": "", "old/two.py": "", "run.sh": "cp old/one.py x; cp old/two.py y\n"})
        moved(r, "old/one.py", "new/one.py")
        moved(r, "old/two.py", "new/two.py")
        code, out = run_script("check_moves.py", r, "--moves", "old/one.py=new/one.py", "--moves", "old/two.py=new/two.py",
                               "--json", r / "hits.json")
        self.assertEqual(code, 1, out)
        data = json.loads((r / "hits.json").read_text())
        self.assertEqual(len([h for h in data["stale"] if h["file"] == "run.sh"]), 2)

    def test_no_imports_flag_skips_resolution(self):
        r = make_repo({"src/a/x.ts": "import { y } from './y';\n", "src/a/y.ts": ""})
        moved(r, "src/a/x.ts", "src/b/x.ts")
        code, out = run_script("check_moves.py", r, "--moves", "src/a/x.ts=src/b/x.ts", "--no-imports")
        self.assertEqual(code, 0, out)

    def test_ts_esm_js_specifier_resolves_to_ts_file(self):
        r = make_repo({"src/a.ts": "import { b } from './b.js';\n", "src/b.ts": "export const b = 1;\n"})
        code, out = run_script("check_moves.py", r, "--moves", "zzz/none.py=zzz/other.py")
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
