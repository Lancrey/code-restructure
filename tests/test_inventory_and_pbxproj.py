import json
import unittest
from helpers import make_repo, run_script, write


class Inventory(unittest.TestCase):
    def test_reports_manifest_and_files(self):
        r = make_repo({"package.json": '{"name":"x","main":"src/index.js"}', "src/index.js": "console.log(1)\n", "node_modules/a/b.js": "x"})
        code, _ = run_script("inventory.py", r, "--out", r / "inv.json")
        self.assertEqual(code, 0)
        data = json.loads((r / "inv.json").read_text())
        self.assertIn("package.json", json.dumps(data["path_pinning_files"]))
        self.assertNotIn("node_modules", json.dumps(data["tree"]))


    def test_test_files_need_a_test_dir_or_name_and_dsstore_is_ignored(self):
        r = make_repo({"src/testimonials.py": "x=1\n", "tests/test_a.py": "x=1\n", "src/latest_spec.py": "x=1\n", ".DS_Store": "junk"})
        run_script("inventory.py", r, "--out", r / "inv.json")
        data = json.loads((r / "inv.json").read_text())
        self.assertEqual(data["test_files"], ["tests/test_a.py"])
        self.assertNotIn(".DS_Store", data["tree"]["root_files"])

    def test_tests_inside_plugin_folders_are_flagged(self):
        r = make_repo({"public/js/plugins/x/test/test_a.js": "", "tests/test_b.py": ""})
        code, out = run_script("inventory.py", r, "--out", r / "inv.json")
        self.assertIn("1 of them inside plugin/vendor/lib folders", out)

    def test_tsup_config_and_frontend_summary(self):
        r = make_repo({"tsup.config.ts": "export default {}\n", "src/index.ts": "", "src/styles.css": "", "examples/a.html": ""})
        code, out = run_script("inventory.py", r, "--out", r / "inv.json")
        data = json.loads((r / "inv.json").read_text())
        self.assertIn("tsup.config.ts", data["path_pinning_files"]["bundler"])
        self.assertTrue(data["frontend"]["load_frontend_reference"])
        self.assertIn("load references/frontend-assets.md", out)

    def test_build_dir_excluded_only_at_root(self):
        r = make_repo({"internal/build/b.go": "x\n", "dist/x.js": "y\n"})
        run_script("inventory.py", r, "--out", r / "ev/inv.json")  # new directory created on the fly
        data = json.loads((r / "ev/inv.json").read_text())
        self.assertEqual(data["excluded_dirs"], ["dist"])
        self.assertEqual(data["file_count"], 1)


PBX = """// !$*UTF8*$!
{
\tobjects = {
\t\tAAAAAAAAAAAAAAAAAAAAAAA1 /* a.swift */ = {isa = PBXFileReference; path = a.swift; sourceTree = "<group>"; };
\t\tBBBBBBBBBBBBBBBBBBBBBBB2 /* Sources */ = {
\t\t\tisa = PBXGroup;
\t\t\tchildren = (
\t\t\t\tAAAAAAAAAAAAAAAAAAAAAAA1,
\t\t\t);
\t\t\tpath = Sources;
\t\t\tsourceTree = "<group>";
\t\t};
\t\tCCCCCCCCCCCCCCCCCCCCCCC3 = {
\t\t\tisa = PBXGroup;
\t\t\tchildren = (
\t\t\t\tBBBBBBBBBBBBBBBBBBBBBBB2,
\t\t\t);
\t\t\tsourceTree = "<group>";
\t\t};
\t};
\tmainGroup = CCCCCCCCCCCCCCCCCCCCCCC3;
}
"""


class Pbxproj(unittest.TestCase):
    def repo(self, with_file=True):
        files = {"App.xcodeproj/project.pbxproj": PBX}
        if with_file:
            files["Sources/a.swift"] = "let a = 1\n"
        return make_repo(files)

    def test_consistent_project(self):
        code, out = run_script("pbxproj_check.py", self.repo())
        self.assertEqual(code, 0, out)

    def test_missing_file_detected(self):
        code, out = run_script("pbxproj_check.py", self.repo(with_file=False))
        self.assertEqual(code, 1, out)
        self.assertIn("MISSING", out)

    def test_space_indented_project_is_parsed(self):
        r = make_repo({"App.xcodeproj/project.pbxproj": PBX.replace("\t", "    ")})
        code, out = run_script("pbxproj_check.py", r)
        self.assertEqual(code, 1, out)
        self.assertIn("MISSING", out)

    def test_unparseable_project_is_a_problem_not_a_pass(self):
        r = make_repo({"App.xcodeproj/project.pbxproj": "// garbage\n{ objects = { }; mainGroup = CCCCCCCCCCCCCCCCCCCCCCC3; }\n"})
        code, out = run_script("pbxproj_check.py", r)
        self.assertEqual(code, 1, out)
        self.assertIn("0 file references", out)

    def test_synchronized_project_is_not_verified_exit_3(self):
        r = make_repo({"App.xcodeproj/project.pbxproj": "// !$*UTF8*$!\n{ objects = { X = {isa = PBXFileSystemSynchronizedRootGroup; }; }; }\n"})
        code, out = run_script("pbxproj_check.py", r)
        self.assertEqual(code, 3, out)
        self.assertIn("NOT VERIFIED", out)

    def test_no_project_is_exit_3(self):
        code, out = run_script("pbxproj_check.py", make_repo({"a.txt": "x\n"}))
        self.assertEqual(code, 3, out)

    def test_unreferenced_file_detected(self):
        r = self.repo()
        write(r, {"Sources/b.swift": "let b = 2\n"})
        code, out = run_script("pbxproj_check.py", r)
        self.assertEqual(code, 1, out)
        self.assertIn("not referenced", out)


if __name__ == "__main__":
    unittest.main()
