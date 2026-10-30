"""Contract tests for the generated ChatGPT/Codex distribution."""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BUILDER = ROOT / "tools" / "build_openai.py"
SOURCE = ROOT / "code-restructure"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files_under(folder):
    return {
        p.relative_to(folder).as_posix(): digest(p)
        for p in folder.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }


class OpenAIPackage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="openai-package-tests-")
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "code-restructure"

    def run_builder(self, *extra):
        return subprocess.run(
            [sys.executable, str(BUILDER), "--output", str(self.output), *extra],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

    def test_manifest_and_skill_frontmatter(self):
        run = self.run_builder()
        self.assertEqual(run.returncode, 0, run.stderr)
        metadata = json.loads((self.output / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["name"], "code-restructure")
        self.assertEqual(metadata["version"], "0.1.0")
        self.assertEqual(metadata["license"], "MIT")
        self.assertEqual(
            metadata["$schema"],
            "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        )
        text = (self.output / "skills/code-restructure/SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\\nname: code-restructure\\n".replace("\\\\n", "\\n")))
        self.assertIn("## OpenAI runtime adapter", text)
        self.assertIn("### Non-negotiable safeguards", text)
        self.assertIn("# Code Restructure", text)

    def test_scripts_references_identical_and_claude_untouched(self):
        before = files_under(SOURCE)
        run = self.run_builder()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(files_under(SOURCE), before)
        out = self.output / "skills/code-restructure"
        for relative, checksum in before.items():
            if relative == "SKILL.md":
                continue
            self.assertEqual(digest(out / relative), checksum, relative)
        source_skill = (SOURCE / "SKILL.md").read_text(encoding="utf-8")
        generated = (out / "SKILL.md").read_text(encoding="utf-8")
        original_body = source_skill.split("---", 2)[2]
        self.assertIn(original_body, generated)
        self.assertNotEqual(generated, source_skill)

    def test_archive_and_repeatable_build(self):
        first = self.run_builder("--zip")
        self.assertEqual(first.returncode, 0, first.stderr)
        expected = self.output.with_suffix(".zip")
        self.assertTrue(expected.is_file())
        with zipfile.ZipFile(expected) as zipped:
            names = set(zipped.namelist())
            self.assertIn("code-restructure/plugin.json", names)
            self.assertIn("code-restructure/skills/code-restructure/SKILL.md", names)
            self.assertIn("code-restructure/skills/code-restructure/scripts/check_moves.py", names)
        before = files_under(self.output)
        second = self.run_builder("--zip")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(files_under(self.output), before)

    def test_will_not_overwrite_unrelated_folder(self):
        self.output.mkdir()
        sentinel = self.output / "important.txt"
        sentinel.write_text("keep", encoding="utf-8")
        run = self.run_builder()
        self.assertEqual(run.returncode, 2)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_missing_host_is_not_claimed_as_full(self):
        run = self.run_builder()
        self.assertEqual(run.returncode, 0, run.stderr)
        skill = (self.output / "skills/code-restructure/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("full", skill)
        self.assertIn("requires a writable Git working tree", skill)
        self.assertIn("never push", skill.lower())


if __name__ == "__main__":
    unittest.main()
