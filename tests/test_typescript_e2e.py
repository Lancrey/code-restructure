"""A TypeScript project type-checked for real before and after a module moves. Skipped without tsc."""
import shutil
import unittest
from helpers import make_repo, git, run_script, write

TSC = shutil.which("tsc")
PROJECT = {
    "package.json": '{"name": "demo", "private": true}\n',
    "tsconfig.json": '{"compilerOptions": {"strict": true, "noEmit": true, "module": "esnext", "moduleResolution": "bundler", "target": "es2020"}, "include": ["src"]}\n',
    "src/utils/slug.ts": "export const slug = (s: string) => s.toLowerCase();\n",
    "src/types.d.ts": "export interface User { name: string }\n",
    "src/main.ts": "import { slug } from './utils/slug';\nimport type { User } from './types';\nexport const u: User = { name: slug('A') };\n",
    "src/components/widget.ts": "import { slug } from '../utils/slug';\nexport const w = slug('W');\n",
    "docs/architecture-migration-plan.md": "| From | To | Note |\n|---|---|---|\n| src/utils/slug.ts | src/core/text/slug.ts | |\n",
}
BUILD = f"{TSC} -p tsconfig.json" if TSC else ""


@unittest.skipUnless(TSC, "tsc not installed")
class TypeScriptEndToEnd(unittest.TestCase):
    def test_move_with_all_imports_updated_passes_and_forgotten_one_fails(self):
        r = make_repo(PROJECT)
        code, out = run_script("baseline.py", r, "--extra", BUILD, "--out", r / "before.json")
        self.assertEqual(code, 0, out)
        (r / "src/core/text").mkdir(parents=True)
        git(r, "mv", "src/utils/slug.ts", "src/core/text/slug.ts")
        write(r, {"src/main.ts": PROJECT["src/main.ts"].replace("./utils/slug", "./core/text/slug")})
        # widget.ts still imports '../utils/slug'
        code, out = run_script("check_moves.py", r, "--plan", "docs/architecture-migration-plan.md")
        self.assertEqual(code, 1, out)
        self.assertIn("src/components/widget.ts:1  ../utils/slug   <- was moved to src/core/text/slug.ts", out)
        code, out = run_script("baseline.py", r, "--extra", BUILD, "--out", r / "after.json", "--compare", r / "before.json")
        self.assertEqual(code, 2, out)
        write(r, {"src/components/widget.ts": PROJECT["src/components/widget.ts"].replace("../utils/slug", "../core/text/slug")})
        code, out = run_script("check_moves.py", r, "--plan", "docs/architecture-migration-plan.md")
        self.assertEqual(code, 0, out)
        code, out = run_script("baseline.py", r, "--extra", BUILD, "--out", r / "after2.json", "--compare", r / "before.json")
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
