import unittest
from helpers import make_repo, run_script, write


class SecretsScan(unittest.TestCase):
    def repo(self):
        return make_repo({
            ".env": "DB_PASSWORD=Zx9!kLm2pQ7r\nAPI_URL=https://example.com\n",
            "app/settings.py": 'SECRET_KEY = "dj4ng0-ins3cure-7f3a9c1e2b"\nTOKEN = os.environ["TOKEN"]\nNAME = "monkey business"\n'
                               'DB = "postgres://admin:Sup3rS3cret!@db.internal:5432/app"\n',
            "config.php": "define('AM_CRYPTO_KEY', md5('4m3Ng1N3sS3cuR3K3y', true));\n",
            "docs/a.md": "nothing here\n",
        })

    def test_finds_secrets_and_never_prints_values(self):
        r = self.repo()
        code, out = run_script("secrets_scan.py", r)
        self.assertEqual(code, 0)
        for name in ("DB_PASSWORD", "SECRET_KEY", "url-credential", "AM_CRYPTO_KEY"):
            self.assertIn(name, out)
        for value in ("Zx9!kLm2pQ7r", "dj4ng0-ins3cure", "Sup3rS3cret", "4m3Ng1N3sS3cuR3K3y"):
            self.assertNotIn(value, out)
        self.assertNotIn("monkey", out)  # prose value
        self.assertNotIn("os.environ", out)  # lookups are not literals

    def test_verify_clean_then_leak_then_exit_code(self):
        r = self.repo()
        code, out = run_script("secrets_scan.py", r, "--verify", r / "docs")
        self.assertEqual(code, 0, out)
        write(r, {"docs/audit.md": "the key is dj4ng0-ins3cure-7f3a9c1e2b, oops\n"})
        code, out = run_script("secrets_scan.py", r, "--verify", r / "docs")
        self.assertEqual(code, 1, out)
        self.assertIn("docs/audit.md:1", out)
        self.assertNotIn("dj4ng0", out)

    def test_plan_tags_moved_files(self):
        r = self.repo()
        write(r, {"plan.md": "| From | To | Note |\n|---|---|---|\n| config.php | conf/config.php | |\n"})
        code, out = run_script("secrets_scan.py", r, "--plan", r / "plan.md")
        self.assertIn("[moved]", [l for l in out.splitlines() if l.startswith("config.php")][0])


if __name__ == "__main__":
    unittest.main()
