#!/usr/bin/env python3
"""Repository inventory for a structural audit.

Walks a repository (respecting .gitignore when it is a Git repo), and reports:
  - language mix by source-file count and lines
  - manifests, build/CI/test/lint config files (the files that pin paths)
  - probable entry points
  - directory tree with file counts (depth-limited)
  - directories excluded from the audit (dependencies, build output, VCS)

Usage:
  python3 inventory.py <repo> [--out inventory.json] [--depth 3] [--exclude DIR ...]

Standard library only. Prints a human summary to stdout and writes the full
JSON to --out (default: inventory.json in the current directory).
"""
import argparse
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ALWAYS_EXCLUDE = {
    ".git", ".hg", ".svn", "node_modules", "vendor", ".venv", "venv", "env",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".nox",
    "dist", "build", "out", "target", ".next", ".nuxt", ".svelte-kit", ".turbo",
    ".cache", ".parcel-cache", "coverage", "htmlcov", ".idea", ".vscode", ".DS_Store",
    "bower_components", ".gradle", ".dart_tool", "Pods", "DerivedData", ".terraform",
}

LANG_BY_EXT = {
    ".py": "Python", ".pyi": "Python",
    ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".mts": "TypeScript", ".cts": "TypeScript", ".tsx": "TypeScript",
    ".vue": "Vue", ".svelte": "Svelte", ".astro": "Astro",
    ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin",
    ".scala": "Scala", ".php": "PHP", ".rb": "Ruby", ".cs": "C#", ".swift": "Swift",
    ".c": "C", ".h": "C/C++ header", ".cpp": "C++", ".cc": "C++", ".hpp": "C++",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell", ".ps1": "PowerShell",
    ".sql": "SQL", ".html": "HTML", ".css": "CSS", ".scss": "SCSS", ".less": "Less",
    ".md": "Markdown", ".rst": "reStructuredText", ".yml": "YAML", ".yaml": "YAML",
    ".json": "JSON", ".toml": "TOML", ".xml": "XML", ".ini": "INI", ".cfg": "INI",
    ".blade.php": "Blade", ".twig": "Twig", ".proto": "Protobuf", ".graphql": "GraphQL",
    ".tf": "Terraform", ".dockerfile": "Dockerfile", ".lua": "Lua", ".ex": "Elixir",
    ".exs": "Elixir", ".erl": "Erlang", ".hs": "Haskell", ".ml": "OCaml", ".dart": "Dart",
    ".r": "R", ".R": "R", ".jl": "Julia", ".ipynb": "Jupyter",
}
CODE_LANGS = {
    "Python", "JavaScript", "TypeScript", "Vue", "Svelte", "Astro", "Go", "Rust", "Java",
    "Kotlin", "Scala", "PHP", "Ruby", "C#", "Swift", "C", "C++", "C/C++ header", "Shell",
    "PowerShell", "Lua", "Elixir", "Erlang", "Haskell", "OCaml", "Dart", "R", "Julia",
}

# Files whose contents pin paths, module names or class names. Grouped by kind
# so the audit can list them and the agent knows which reference section applies.
PATH_PINNING_FILES = {
    "manifest": [
        "pyproject.toml", "setup.py", "setup.cfg", "MANIFEST.in", "requirements.txt",
        "package.json", "pnpm-workspace.yaml", "lerna.json", "nx.json", "turbo.json",
        "go.mod", "go.work", "Cargo.toml", "composer.json", "pom.xml", "build.gradle",
        "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "Gemfile", "*.csproj",
        "*.sln", "Package.swift", "pubspec.yaml", "mix.exs", "deno.json", "bun.lockb",
    ],
    "typescript": ["tsconfig.json", "tsconfig.*.json", "jsconfig.json"],
    "bundler": [
        "vite.config.*", "webpack.config.*", "rollup.config.*", "esbuild.*", "next.config.*",
        "nuxt.config.*", "astro.config.*", "svelte.config.*", "angular.json", "nest-cli.json",
        "babel.config.*", ".babelrc", "webpack.mix.js", "tailwind.config.*", "postcss.config.*",
        "manifest.json",
    ],
    "test": [
        "pytest.ini", "tox.ini", "noxfile.py", "conftest.py", "jest.config.*",
        "vitest.config.*", "vitest.workspace.*", "playwright.config.*", "cypress.config.*",
        "karma.conf.*", "phpunit.xml", "phpunit.xml.dist", ".rspec", "codeception.yml",
    ],
    "lint": [
        ".eslintrc", ".eslintrc.*", "eslint.config.*", ".prettierrc*", ".prettierignore",
        ".eslintignore", "ruff.toml", ".ruff.toml", ".flake8", "mypy.ini", ".pylintrc",
        "pyrightconfig.json", ".golangci.yml", ".golangci.yaml", "clippy.toml", "rustfmt.toml",
        "phpstan.neon", "phpstan.neon.dist", "psalm.xml", ".php-cs-fixer.php",
        ".php-cs-fixer.dist.php", "rector.php", "ecs.php", "deptrac.yaml", "checkstyle.xml",
        "detekt.yml", ".editorconfig", ".pre-commit-config.yaml",
    ],
    "ci": [
        ".github/workflows/*.yml", ".github/workflows/*.yaml", ".gitlab-ci.yml",
        ".circleci/config.yml", "Jenkinsfile", "azure-pipelines.yml", "bitbucket-pipelines.yml",
        ".travis.yml", ".drone.yml", "cloudbuild.yaml", ".buildkite/*",
    ],
    "container_deploy": [
        "Dockerfile", "Dockerfile.*", "*.dockerfile", "docker-compose.yml",
        "docker-compose.*.yml", "compose.yaml", "compose.yml", ".dockerignore", "Procfile",
        "fly.toml", "render.yaml", "vercel.json", "netlify.toml", "wrangler.toml",
        "wrangler.jsonc", "serverless.yml", "app.yaml", "deploy.php", "Envoy.blade.php",
        "Makefile", "justfile", "Taskfile.yml", "magefile.go", ".goreleaser.yml",
    ],
    "framework": [
        "manage.py", "artisan", "bin/console", "config/app.php", "config/services.yaml",
        "config/routes.yaml", "bootstrap/app.php", "bootstrap/providers.php", "alembic.ini",
        "prisma/schema.prisma", "drizzle.config.*", "knexfile.*", "ormconfig.*",
        "module-info.java", "AndroidManifest.xml", "application.yml", "application.properties",
        "application.yaml", "logback.xml", "log4j2.xml", "web.xml", "META-INF/services/*",
    ],
    "docs_guidance": [
        "README*", "CONTRIBUTING*", "CLAUDE.md", "AGENTS.md", "ARCHITECTURE*", "DESIGN*",
        "docs/", "doc/", "documentation/",
    ],
}

ENTRY_POINT_NAMES = {
    "main.py", "__main__.py", "app.py", "cli.py", "run.py", "manage.py", "wsgi.py", "asgi.py",
    "server.py", "main.go", "main.rs", "main.ts", "main.js", "index.ts", "index.js",
    "server.ts", "server.js", "app.ts", "app.js", "cli.ts", "cli.js", "artisan",
    "index.php", "Main.java", "Application.java", "Application.kt", "App.java",
}


def git_tracked_files(repo: Path):
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True, text=True, check=True, timeout=120,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return [p for p in out.split("\0") if p]


def walk_files(repo: Path, extra_exclude):
    exclude = ALWAYS_EXCLUDE | set(extra_exclude)
    tracked = git_tracked_files(repo)
    files = []
    excluded_dirs = set()
    if tracked is not None:
        for rel in tracked:
            parts = Path(rel).parts
            hit = next((p for p in parts[:-1] if p in exclude), None)
            if hit:
                excluded_dirs.add(str(Path(*parts[: parts.index(hit) + 1])))
                continue
            if (repo / rel).is_file():
                files.append(rel)
        return files, sorted(excluded_dirs), True
    for root, dirs, names in os.walk(repo):
        rel_root = Path(root).relative_to(repo)
        kept = []
        for d in dirs:
            if d in exclude:
                excluded_dirs.add(str(rel_root / d) if str(rel_root) != "." else d)
            else:
                kept.append(d)
        dirs[:] = kept
        for n in names:
            if n in exclude:
                continue
            files.append(str(rel_root / n) if str(rel_root) != "." else n)
    return files, sorted(excluded_dirs), False


def lang_of(rel: str):
    name = os.path.basename(rel)
    if name.lower() in ("dockerfile",) or name.lower().startswith("dockerfile."):
        return "Dockerfile"
    if name.endswith(".blade.php"):
        return "Blade"
    if name == "Makefile":
        return "Make"
    ext = os.path.splitext(name)[1]
    return LANG_BY_EXT.get(ext) or LANG_BY_EXT.get(ext.lower())


def count_lines(path: Path):
    try:
        with open(path, "rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def match_pattern(rel: str, pattern: str) -> bool:
    import fnmatch
    if pattern.endswith("/"):
        return rel.startswith(pattern) or rel == pattern.rstrip("/")
    if "/" in pattern:
        return fnmatch.fnmatch(rel, pattern)
    return fnmatch.fnmatch(os.path.basename(rel), pattern)


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def manifest_entry_points(repo: Path, files):
    eps = []
    if "package.json" in files:
        pj = read_json(repo / "package.json") or {}
        for key in ("main", "module", "bin", "types"):
            v = pj.get(key)
            if isinstance(v, str):
                eps.append({"kind": f"package.json:{key}", "ref": v})
            elif isinstance(v, dict):
                for k, vv in v.items():
                    eps.append({"kind": f"package.json:{key}[{k}]", "ref": vv})
        if isinstance(pj.get("exports"), (dict, str)):
            eps.append({"kind": "package.json:exports", "ref": json.dumps(pj["exports"])[:300]})
        for k, v in (pj.get("scripts") or {}).items():
            eps.append({"kind": f"npm script:{k}", "ref": v})
        if pj.get("workspaces"):
            eps.append({"kind": "package.json:workspaces", "ref": json.dumps(pj["workspaces"])})
    if "pyproject.toml" in files:
        txt = (repo / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
        section = None
        for line in txt.splitlines():
            s = line.strip()
            if s.startswith("["):
                section = s.strip("[]")
                continue
            if section in ("project.scripts", "project.gui-scripts", "project.entry-points") and "=" in s:
                eps.append({"kind": f"pyproject:{section}", "ref": s})
            if section and section.startswith("tool.setuptools") and any(
                k in s for k in ("packages", "package-dir", "where", "include")
            ):
                eps.append({"kind": f"pyproject:{section}", "ref": s})
            if section == "tool.pytest.ini_options" and s.startswith(("testpaths", "pythonpath")):
                eps.append({"kind": "pyproject:pytest", "ref": s})
    if "composer.json" in files:
        cj = read_json(repo / "composer.json") or {}
        for k in ("autoload", "autoload-dev"):
            if cj.get(k):
                eps.append({"kind": f"composer:{k}", "ref": json.dumps(cj[k])})
        if cj.get("bin"):
            eps.append({"kind": "composer:bin", "ref": json.dumps(cj["bin"])})
    if "Cargo.toml" in files:
        txt = (repo / "Cargo.toml").read_text(encoding="utf-8", errors="replace")
        section = None
        for line in txt.splitlines():
            s = line.strip()
            if s.startswith("["):
                section = s.strip("[]")
                continue
            if section in ("workspace", "lib") or section.startswith(("bin", "example", "test", "bench")):
                if s.startswith(("path", "members", "name", "exclude")):
                    eps.append({"kind": f"Cargo.toml:[{section}]", "ref": s})
    if "go.mod" in files:
        first = (repo / "go.mod").read_text(encoding="utf-8", errors="replace").splitlines()[:1]
        if first:
            eps.append({"kind": "go.mod:module", "ref": first[0]})
        cmd = [f for f in files if f.startswith("cmd/") and f.endswith("main.go")]
        for c in cmd:
            eps.append({"kind": "go:cmd", "ref": c})
    return eps


def build_tree(files, depth):
    counts = defaultdict(lambda: {"files": 0, "code_files": 0, "lines": 0})
    for rel, lang, lines in files:
        parts = Path(rel).parts
        for i in range(1, min(len(parts), depth + 1)):
            d = "/".join(parts[:i])
            if i == len(parts):
                break
            node = counts[d]
            node["files"] += 1
            if lang in CODE_LANGS:
                node["code_files"] += 1
                node["lines"] += lines
    root_files = [rel for rel, _, _ in files if "/" not in rel]
    return {"dirs": dict(sorted(counts.items())), "root_files": sorted(root_files)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo")
    ap.add_argument("--out", default="inventory.json")
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--exclude", nargs="*", default=[], help="extra directory names to exclude")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        sys.exit(f"not a directory: {repo}")

    rel_files, excluded_dirs, is_git = walk_files(repo, args.exclude)
    files = []
    lang_files = Counter()
    lang_lines = Counter()
    for rel in rel_files:
        lang = lang_of(rel)
        lines = count_lines(repo / rel) if lang else 0
        files.append((rel, lang, lines))
        if lang:
            lang_files[lang] += 1
            lang_lines[lang] += lines

    pinning = defaultdict(list)
    for kind, patterns in PATH_PINNING_FILES.items():
        for rel in rel_files:
            if any(match_pattern(rel, p) for p in patterns):
                pinning[kind].append(rel)
    for kind in pinning:
        pinning[kind] = sorted(set(pinning[kind]))

    entry_files = sorted(
        rel for rel in rel_files
        if os.path.basename(rel) in ENTRY_POINT_NAMES and "test" not in rel.lower()
    )
    test_files = sorted(
        rel for rel in rel_files
        if lang_of(rel) in CODE_LANGS and (
            "/test" in f"/{rel.lower()}" or "/spec" in f"/{rel.lower()}"
            or os.path.basename(rel).startswith("test_")
            or any(os.path.basename(rel).endswith(s) for s in ("_test.py", "_test.go", ".test.ts", ".test.js", ".spec.ts", ".spec.js", "Test.java", "Test.kt", "Test.php"))
        )
    )

    code_langs = [(l, n) for l, n in lang_files.most_common() if l in CODE_LANGS]
    tree = build_tree(files, args.depth)
    big_dirs = sorted(tree["dirs"].items(), key=lambda kv: -kv[1]["code_files"])[:15]

    inventory = {
        "repo": str(repo),
        "is_git_repo": is_git,
        "file_count": len(rel_files),
        "excluded_dirs": excluded_dirs,
        "languages": {
            "by_files": dict(lang_files.most_common()),
            "by_lines": dict(lang_lines.most_common()),
            "dominant_code_languages": code_langs[:4],
        },
        "path_pinning_files": dict(pinning),
        "manifest_entry_points": manifest_entry_points(repo, set(rel_files)),
        "probable_entry_files": entry_files,
        "test_files": test_files,
        "test_file_count": len(test_files),
        "tree": tree,
    }
    Path(args.out).write_text(json.dumps(inventory, indent=2), encoding="utf-8")

    print(f"Repository: {repo}  (git: {is_git})")
    print(f"Files inventoried: {len(rel_files)}; excluded dirs: {', '.join(excluded_dirs) or 'none'}")
    print("Code languages (files / lines):")
    for lang, n in code_langs[:6]:
        print(f"  {lang:<14} {n:>6} / {lang_lines[lang]:>8}")
    print(f"Test files: {len(test_files)}")
    print("Path-pinning files:")
    for kind, lst in pinning.items():
        print(f"  {kind:<17} {len(lst):>3}  " + ", ".join(lst[:6]) + (" …" if len(lst) > 6 else ""))
    print("Probable entry files: " + (", ".join(entry_files[:10]) or "none") + (" …" if len(entry_files) > 10 else ""))
    print("Largest directories by code files:")
    for d, c in big_dirs[:10]:
        print(f"  {d:<40} {c['code_files']:>5} files  {c['lines']:>8} lines")
    print(f"Full inventory written to {Path(args.out).resolve()}")


if __name__ == "__main__":
    main()
