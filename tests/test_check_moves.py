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

    def test_substring_of_another_path_is_not_a_hit(self):
        r = make_repo({"app/db.py": "", "app/settings.py": "DATABASE = 'sqlite:///myapp.db'\nX = 'app/db_v2'\n"})
        moved(r, "app/db.py", "app/data/db.py")
        code, out = run_script("check_moves.py", r, "--moves", "app/db.py=app/data/db.py", "--no-imports")
        self.assertEqual(code, 0, out)

    def test_reports_at_repo_root_are_allowlisted(self):
        r = make_repo({"app/db.py": "", "architecture-audit.md": "app/db.py was here\n",
                       "restructure-evidence/notes.txt": "app/db.py\n"})
        moved(r, "app/db.py", "app/data/db.py")
        code, out = run_script("check_moves.py", r, "--moves", "app/db.py=app/data/db.py", "--no-imports")
        self.assertEqual(code, 0, out)

    def test_relocation_table_checked_against_disk(self):
        r = make_repo({"app/keep.py": "", "app/db.py": ""})
        moved(r, "app/db.py", "app/data/db.py")
        code, out = run_script("check_moves.py", r, "--moves", "app/db.py=app/data/db.py",
                               "--moves", "app/keep.py=app/data/keep.py", "--moves", "app/gone.py=app/data/x.py", "--no-imports")
        self.assertEqual(code, 1, out)
        self.assertIn("From still exists on disk: app/keep.py", out)
        self.assertIn("To does not exist on disk: app/data/keep.py", out)
        self.assertIn("To does not exist on disk: app/data/x.py", out)

    def test_shim_left_in_place_is_fine_when_allowed(self):
        r = make_repo({"app/db.py": "# shim\n"})
        write(r, {"app/data/db.py": ""})
        code, out = run_script("check_moves.py", r, "--moves", "app/db.py=app/data/db.py", "--allow", "app/db.py", "--no-imports")
        self.assertEqual(code, 0, out)

    def test_nested_build_dir_is_scanned(self):
        r = make_repo({"old/one.py": "", "internal/build/ref.go": "// old/one.py\n", "dist/x.js": "// old/one.py\n"})
        moved(r, "old/one.py", "new/one.py")
        code, out = run_script("check_moves.py", r, "--moves", "old/one.py=new/one.py", "--no-imports")
        self.assertEqual(code, 1, out)
        self.assertIn("internal/build/ref.go", out)
        self.assertNotIn("dist/x.js", out)

    def test_in_progress_skips_pending_rows_final_does_not(self):
        r = make_repo({"a/one.py": "", "a/two.py": "", "ref.py": "x = 'a/two.py'\n"})
        moved(r, "a/one.py", "b/one.py")
        args = ["--moves", "a/one.py=b/one.py", "--moves", "a/two.py=b/two.py", "--no-imports"]
        code, out = run_script("check_moves.py", r, *args, "--in-progress")
        self.assertEqual(code, 0, out)
        self.assertIn("1 pending row(s)", out)
        code, out = run_script("check_moves.py", r, *args)
        self.assertEqual(code, 1, out)
        self.assertIn("From still exists on disk: a/two.py", out)
        self.assertIn("ref.py", out)

    def test_glob_row_with_file_left_behind(self):
        r = make_repo({"plugins/a.py": "", "plugins/b.py": ""})
        moved(r, "plugins/a.py", "src/plugins/a.py")
        code, out = run_script("check_moves.py", r, "--moves", "plugins/*.py=src/plugins/", "--no-imports")
        self.assertEqual(code, 1, out)
        self.assertIn("plugins/b.py", out)

    def test_split_destinations_both_count_as_updated(self):
        r = make_repo({"src/utils.py": "", "src/main.py": "from src.core.text import a\nfrom src.core.dates import b\n"})
        moved(r, "src/utils.py", "src/core/text.py")
        write(r, {"src/core/dates.py": ""})
        code, out = run_script("check_moves.py", r, "--moves", "src/utils.py=src/core/text.py", "--moves", "src/utils.py=src/core/dates.py", "--no-imports")
        self.assertEqual(code, 0, out)

    def test_python_from_dot_import_name_of_moved_module(self):
        r = make_repo({"pkg/__init__.py": "from .lib.helpers import f\n", "pkg/lib/helpers.py": "def f(): pass\n",
                       "pkg/mod.py": "from . import helpers, f\nfrom . import lib\n"})
        code, out = run_script("check_moves.py", r, "--moves", "pkg/helpers.py=pkg/lib/helpers.py")
        self.assertEqual(code, 1, out)
        self.assertIn("pkg/mod.py:1  . import helpers", out)
        self.assertNotIn("import f", out)
        self.assertNotIn("import lib", out)

    def test_ts_declaration_file_resolves(self):
        r = make_repo({"src/types.d.ts": "export interface U {}\n", "src/a.ts": "import type { U } from './types';\n", "z/b.py": ""})
        code, out = run_script("check_moves.py", r, "--moves", "z/a.py=z/b.py")
        self.assertEqual(code, 0, out)

    def test_module_collectors_reset_between_calls(self):
        import sys, check_moves
        r = make_repo({"src/scss/main.scss": "body{background:url(../img/x.png)}\n", "zz/b.py": ""})
        argv = sys.argv
        try:
            for _ in range(2):
                sys.argv = ["check_moves.py", str(r), "--moves", "zz/a.py=zz/b.py"]
                try:
                    check_moves.main()
                except SystemExit:
                    pass
                self.assertEqual(len(check_moves.UNCERTAIN), 1)
        finally:
            sys.argv = argv

    def test_directory_referenced_from_a_base_constant(self):
        r = make_repo({"include/phpexcel/PHPExcel.php": "", "include/less/Less.php": "",
                       "include/bootstrap.php": "<?php\nrequire AM_INCLUDE_PATH . 'phpexcel/PHPExcel.php';\nrequire AM_INCLUDE_PATH . 'less/Less.php';\n"
                                                "$c = AM_CACHE_PATH . 'less/' . $t;\n$d = $incdir . '/phpexcel/tmp';\n"})
        moved(r, "include/phpexcel", "include/lib/phpexcel")
        moved(r, "include/less", "include/lib/less")
        args = ["--moves", "include/phpexcel=include/lib/phpexcel", "--moves", "include/less=include/lib/less", "--no-imports"]
        code, out = run_script("check_moves.py", r, *args)
        self.assertEqual(code, 1, out)
        self.assertIn("L2", out)
        self.assertIn("L3", out)
        self.assertIn("L4", out)   # AM_CACHE_PATH . 'less/' — same name, other folder: flagged for review
        self.assertNotIn("L5", out)  # '/phpexcel/tmp' after another variable: not anchored, not flagged
        write(r, {"include/bootstrap.php": "<?php\nrequire AM_INCLUDE_PATH . 'lib/phpexcel/PHPExcel.php';\nrequire AM_INCLUDE_PATH . 'lib/less/Less.php';\n"
                                           "$c = AM_CACHE_PATH . 'less/' . $t;\n"})
        code, out = run_script("check_moves.py", r, *args, "--allow", "include/bootstrap.php:4")
        self.assertEqual(code, 0, out)

    def test_file_relative_dir_reference_is_caught(self):
        r = make_repo({"include/phpexcel/PHPExcel.php": "",
                       "include/bootstrap.php": "<?php\nrequire dirname(__FILE__) . '/phpexcel/PHPExcel.php';\nrequire __DIR__ . '/phpexcel/PHPExcel.php';\n$x = $incdir . '/phpexcel/tmp';\n"})
        moved(r, "include/phpexcel", "include/lib/phpexcel")
        code, out = run_script("check_moves.py", r, "--moves", "include/phpexcel=include/lib/phpexcel", "--no-imports")
        self.assertEqual(code, 1, out)
        self.assertIn("L2", out)
        self.assertIn("L3", out)
        self.assertNotIn("L4", out)

    def test_nested_vendor_without_manifest_is_source(self):
        r = make_repo({"old/one.py": "", "development/vendor/tool/x.js": "// old/one.py\n", "vendor/dep/y.js": "// old/one.py\n"})
        moved(r, "old/one.py", "new/one.py")
        code, out = run_script("check_moves.py", r, "--moves", "old/one.py=new/one.py", "--no-imports")
        self.assertIn("development/vendor/tool/x.js", out)
        self.assertNotIn("vendor/dep/y.js", out.replace("development/vendor/tool/x.js", ""))

    def test_expand_globs_without_glob_rows_says_so(self):
        r = make_repo({"a.py": "", "plan.md": "| From | To | Note |\n|---|---|---|\n| a.py | b/a.py | |\n"})
        code, out = run_script("check_moves.py", r, "--plan", "plan.md", "--expand-globs")
        self.assertIn("nothing to expand", out)

    def test_no_imports_flag_skips_resolution(self):
        r = make_repo({"src/a/x.ts": "import { y } from './y';\n", "src/a/y.ts": ""})
        moved(r, "src/a/x.ts", "src/b/x.ts")
        code, out = run_script("check_moves.py", r, "--moves", "src/a/x.ts=src/b/x.ts", "--no-imports")
        self.assertEqual(code, 0, out)

    def test_ts_esm_js_specifier_resolves_to_ts_file(self):
        r = make_repo({"src/a.ts": "import { b } from './b.js';\n", "src/b.ts": "export const b = 1;\n", "zzz/other.py": ""})
        code, out = run_script("check_moves.py", r, "--moves", "zzz/none.py=zzz/other.py")
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
