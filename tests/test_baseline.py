import json
import unittest
from helpers import make_repo, run_script, write
import baseline


class Compare(unittest.TestCase):
    def rows(self, before, after):
        rows, regs = baseline.compare({"checks": before}, {"checks": after})
        return {r[0]: r[-1] for r in rows}, regs

    def test_pass_to_fail_is_regression(self):
        v, regs = self.rows([{"id": "t", "status": "pass", "counters": {}}], [{"id": "t", "status": "fail", "counters": {}}])
        self.assertEqual(v["t"], "REGRESSION")
        self.assertEqual(regs, ["t"])

    def test_fewer_tests_is_regression(self):
        v, _ = self.rows([{"id": "t", "status": "pass", "counters": {"collected": 81}}], [{"id": "t", "status": "pass", "counters": {"collected": 80}}])
        self.assertEqual(v["t"], "REGRESSION")

    def test_more_skipped_is_regression_only_when_known_on_both_sides(self):
        v, _ = self.rows([{"id": "t", "status": "pass", "counters": {"skipped": 1}}], [{"id": "t", "status": "pass", "counters": {"skipped": 5}}])
        self.assertEqual(v["t"], "REGRESSION")
        v, _ = self.rows([{"id": "t", "status": "pass", "counters": {}}], [{"id": "t", "status": "pass", "counters": {"skipped": 5}}])
        self.assertEqual(v["t"], "same")

    def test_check_that_vanished_is_regression_and_new_check_is_not_comparable(self):
        v, regs = self.rows([{"id": "gone", "status": "pass", "counters": {}}], [{"id": "new", "status": "pass", "counters": {}}])
        self.assertEqual(v["gone"], "REGRESSION")
        self.assertEqual(v["new"], "NEW-not-comparable")

    def test_preexisting_failure_is_not_a_regression(self):
        v, regs = self.rows([{"id": "t", "status": "fail", "counters": {}}], [{"id": "t", "status": "fail", "counters": {}}])
        self.assertEqual(regs, [])


class Counters(unittest.TestCase):
    def test_pytest_and_extra(self):
        self.assertEqual(baseline.parse_counters("pytest", "40 passed, 41 skipped in 0.3s"), {"passed": 40, "skipped": 41})
        self.assertEqual(baseline.parse_counters("extra-1", "12 passed, 3 skipped in 1s"), {"passed": 12, "skipped": 3})
        self.assertEqual(baseline.parse_counters("pytest-collect", "81 tests collected in 0.1s"), {"collected": 81})


class Detect(unittest.TestCase):
    def test_pytest_detected_from_root_test_file_without_config(self):
        r = make_repo({"setup.py": "", "test_x.py": "def test_a(): pass\n", "pkg/__init__.py": ""})
        ids = [c["id"] for c in baseline.detect_checks(r)]
        self.assertIn("pytest", ids)

    def test_pytest_detected_from_tox_ini(self):
        r = make_repo({"tox.ini": "[testenv]\ncommands = pytest\n", "setup.py": ""})
        self.assertIn("pytest", [c["id"] for c in baseline.detect_checks(r)])


class EndToEnd(unittest.TestCase):
    def test_compare_exits_2_when_a_check_breaks(self):
        r = make_repo({"marker": "x\n", "a.txt": "x\n"})
        code, _ = run_script("baseline.py", r, "--only", "extra-1", "--extra", "test -f marker", "--out", r / "before.json")
        self.assertEqual(code, 0)
        (r / "marker").unlink()
        code, out = run_script("baseline.py", r, "--only", "extra-1", "--extra", "test -f marker", "--out", r / "after.json",
                               "--compare", r / "before.json")
        self.assertEqual(code, 2, out)
        self.assertIn("REGRESSION", out)

    def test_ref_runs_on_the_original_commit(self):
        r = make_repo({"marker": "x\n"})
        from helpers import git
        git(r, "checkout", "-qb", "work")
        git(r, "rm", "-q", "marker")
        code, out = run_script("baseline.py", r, "--ref", "HEAD~0", "--only", "extra-1", "--extra", "test -f marker", "--out", r / "b.json")
        # HEAD~0 is the working branch tip (marker removed in index only): the ref checkout still has the committed marker
        self.assertEqual(code, 0, out)
        data = json.loads((r / "b.json").read_text())
        self.assertEqual(data["checks"][0]["status"], "pass")


if __name__ == "__main__":
    unittest.main()
