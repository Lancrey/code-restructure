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

    def test_unreferenced_file_detected(self):
        r = self.repo()
        write(r, {"Sources/b.swift": "let b = 2\n"})
        code, out = run_script("pbxproj_check.py", r)
        self.assertEqual(code, 1, out)
        self.assertIn("not referenced", out)


if __name__ == "__main__":
    unittest.main()
