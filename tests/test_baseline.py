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


class Regressions(unittest.TestCase):
    def test_more_lint_findings_is_regression(self):
        rows, regs = baseline.compare({"checks": [{"id": "ruff", "status": "fail", "counters": {"findings": 10}}]},
                                      {"checks": [{"id": "ruff", "status": "fail", "counters": {"findings": 12}}]})
        self.assertEqual(regs, ["ruff"])

    def test_flake8_lines_are_findings_not_successes(self):
        rows, regs = baseline.compare({"checks": [{"id": "flake8", "status": "fail", "counters": {"lines": 3}}]},
                                      {"checks": [{"id": "flake8", "status": "fail", "counters": {"lines": 5}}]})
        self.assertEqual(regs, ["flake8"])
        rows, regs = baseline.compare({"checks": [{"id": "go-list", "status": "pass", "counters": {"lines": 5}}]},
                                      {"checks": [{"id": "go-list", "status": "pass", "counters": {"lines": 3}}]})
        self.assertEqual(regs, ["go-list"])


class Counters(unittest.TestCase):
    def test_pytest_and_extra(self):
        self.assertEqual(baseline.parse_counters("pytest", "40 passed, 41 skipped in 0.3s"), {"passed": 40, "skipped": 41})
        self.assertEqual(baseline.parse_counters("extra-1", "12 passed, 3 skipped in 1s"), {"passed": 12, "skipped": 3})
        self.assertEqual(baseline.parse_counters("pytest-collect", "81 tests collected in 0.1s"), {"collected": 81})
        self.assertEqual(baseline.parse_counters("ruff", "Found 123 errors.\n"), {"findings": 123})
        self.assertEqual(baseline.parse_counters("npm:lint", "✖ 7 problems (2 errors, 5 warnings)"), {"findings": 7})


class Redaction(unittest.TestCase):
    def test_runtime_secrets_are_redacted_in_saved_tails(self):
        out = baseline.redact("postgres://admin:Sup3r@db/x password=hunter2 Authorization: Bearer abc.def AKIAABCDEFGHIJKLMNOP 40 passed")
        for v in ("Sup3r", "hunter2", "AKIAABCDEFGHIJKLMNOP"):
            self.assertNotIn(v, out)
        self.assertIn("40 passed", out)
        self.assertIn("admin:<redacted>@db", out)

    def test_pbxproj_exit_3_is_unverified(self):
        r = make_repo({"marker": "x\n"})
        rec = baseline.run_check(r, {"id": "pbxproj-files", "cmd": "exit 3", "kind": "build", "note": ""}, 10)
        self.assertEqual(rec["status"], "unverified")


class Detect(unittest.TestCase):
    def test_pytest_detected_from_root_test_file_without_config(self):
        r = make_repo({"setup.py": "", "test_x.py": "def test_a(): pass\n", "pkg/__init__.py": ""})
        ids = [c["id"] for c in baseline.detect_checks(r)]
        self.assertIn("pytest", ids)

    def test_pytest_detected_without_any_manifest(self):
        r = make_repo({"test_x.py": "def test_a(): pass\n"})
        self.assertIn("pytest", [c["id"] for c in baseline.detect_checks(r)])

    def test_pytest_command_has_no_fail_fast(self):
        r = make_repo({"test_x.py": "def test_a(): pass\n"})
        cmd = [c["cmd"] for c in baseline.detect_checks(r) if c["id"] == "pytest"][0]
        self.assertNotIn(" -x", cmd)

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

    def test_new_failure_in_moved_test_file_is_a_regression(self):
        # with -x this used to be masked: the run stopped at the pre-existing failure in test_beta
        r = make_repo({"setup.cfg": "", "test_alpha.py": "def test_a(): pass\ndef test_b(): pass\n",
                       "test_beta.py": "def test_c(): assert False\n", "test_gamma.py": "def test_d(): pass\ndef test_e(): pass\n"})
        code, _ = run_script("baseline.py", r, "--only", "pytest", "--out", r / "before.json")
        from helpers import git
        (r / "tests").mkdir()
        git(r, "mv", "test_gamma.py", "tests/test_gamma.py")
        write(r, {"tests/test_gamma.py": "def test_d(): assert False\ndef test_e(): pass\n"})
        code, out = run_script("baseline.py", r, "--only", "pytest", "--out", r / "after.json", "--compare", r / "before.json")
        self.assertEqual(code, 2, out)

    def test_missing_compare_file_is_an_error(self):
        r = make_repo({"marker": "x\n"})
        code, out = run_script("baseline.py", r, "--only", "extra-1", "--extra", "true", "--out", r / "a.json", "--compare", r / "nope.json")
        self.assertEqual(code, 2, out)
        self.assertNotIn("No regressions", out)

    def test_no_check_at_all_is_an_error(self):
        r = make_repo({"marker": "x\n"})
        code, out = run_script("baseline.py", r, "--only", "nothing", "--extra", "true", "--out", r / "a.json")
        self.assertEqual(code, 2, out)

    def test_only_subset_after_compares_that_subset(self):
        r = make_repo({"setup.cfg": "", "test_x.py": "def test_a(): pass\n"})
        code, out = run_script("baseline.py", r, "--out", r / "b.json")
        self.assertEqual(code, 0, out)
        code, out = run_script("baseline.py", r, "--only", "pytest", "--out", r / "a.json", "--compare", r / "b.json")
        self.assertEqual(code, 0, out)

    def test_out_in_a_new_directory(self):
        r = make_repo({"marker": "x\n"})
        code, out = run_script("baseline.py", r, "--extra", "true", "--out", r / "new/dir/b.json")
        self.assertEqual(code, 0, out)
        self.assertTrue((r / "new/dir/b.json").is_file())

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
