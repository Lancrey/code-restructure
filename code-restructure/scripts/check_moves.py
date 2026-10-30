#!/usr/bin/env python3
"""Find stale references to paths that a migration plan says were moved.

Reads the relocation table from the migration plan (the `| From | To | Note |`
table), derives the strings that would reference each old location (the path
itself, the path without extension, the dotted module form, the bare file
name when it is distinctive), and greps the whole repository for them —
source, configs, YAML, Dockerfiles, Markdown, everything except dependency
and build directories.

Every hit is either a compatibility shim you placed on purpose (list those
with --allow) or a reference you missed.

Usage:
  python3 check_moves.py <repo> --plan docs/architecture-migration-plan.md
  python3 check_moves.py <repo> --moves "src/utils.py=src/core/text.py" --moves "plugins=src/plugins"
  python3 check_moves.py <repo> --plan plan.md --allow src/utils.py --allow "docs/*"
  python3 check_moves.py <repo> --plan plan.md --json hits.json

Besides text references it also resolves every RELATIVE import in JS/TS (`./x`,
`../y`) and Python (`from .x import`, `from ..y import`) files against the disk and
lists those that no longer resolve — in files that moved (their relative imports now
point somewhere else) and in files that import something that moved. This runs without
a plan too (use --no-imports to skip). Not covered: tsconfig/webpack path aliases,
bare package specifiers, PHP/Rust/Go/Java module resolution (text matching only).
Run it on the original commit as well: imports already unresolved before the migration
are not your regression.

Exit code 0: no hits. 1: hits found. 2: usage / parse error.
"""
import argparse
import fnmatch
import json
import os
import posixpath
import re
import subprocess
import sys
from pathlib import Path

EXCLUDE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "vendor", ".venv", "venv", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", "dist", "build", "out", "target",
    ".next", ".nuxt", ".turbo", ".cache", "coverage", ".idea", ".gradle", "Pods",
}
BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".tar", ".jar",
    ".class", ".pyc", ".so", ".dylib", ".dll", ".exe", ".woff", ".woff2", ".ttf", ".otf",
    ".mp3", ".mp4", ".mov", ".sqlite", ".db", ".lock", ".lockb",
}
SOURCE_EXT_FOR_MODULE = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".php", ".java", ".kt"}
GENERIC_NAMES = {
    "index", "main", "app", "utils", "util", "helpers", "helper", "common", "core", "lib",
    "config", "settings", "types", "models", "views", "tests", "test", "src", "api", "base",
    "mod", "init", "__init__", "README", "readme", "setup", "cli", "server", "client",
}


JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte", ".json")
JS_SRC = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte"}
REL_JS = re.compile(r"""(?:\bfrom|\bimport|\brequire)\s*\(?\s*['"](\.{1,2}(?:/[^'"\n]*)?)['"]""")
REL_PY = re.compile(r"^\s*from\s+(\.+)([\w.]*)\s+import\b")
JS_SWAP = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}


def js_resolves(repo, base):
    """True if the relative JS/TS specifier resolved to `base` (repo-relative, posix) names a file or a folder with an index."""
    base = base.split("?")[0].split("#")[0]
    if base in ("", "."):
        return True
    cands = [base] + [base + e for e in JS_EXTS] + [posixpath.join(base, "index" + e) for e in JS_EXTS]
    stem, ext = posixpath.splitext(base)
    for alt in JS_SWAP.get(ext, ()):
        cands.append(stem + alt)
    return any((repo / c).is_file() for c in cands)


def py_resolves(repo, importer, dots, mod):
    pkg = posixpath.dirname(importer)
    for _ in range(len(dots) - 1):
        pkg = posixpath.dirname(pkg)
    if not mod:
        return True  # `from . import name`: name may be a module or a symbol
    base = posixpath.join(pkg, mod.replace(".", "/"))
    return (repo / (base + ".py")).is_file() or (repo / base).is_dir()


def unresolved_relative_imports(repo, files, moved_hint):
    """Yield (file, line, spec, hint) for relative imports that do not resolve on disk."""
    out = []
    for rel in files:
        ext = os.path.splitext(rel)[1].lower()
        if ext not in JS_SRC and ext != ".py":
            continue
        try:
            text = (repo / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for ln, line in enumerate(text.splitlines(), 1):
            if ext == ".py":
                m = REL_PY.match(line)
                if m and not py_resolves(repo, rel, m.group(1), m.group(2)):
                    out.append((rel, ln, m.group(1) + m.group(2), ""))
                continue
            for m in REL_JS.finditer(line):
                spec = m.group(1)
                base = posixpath.normpath(posixpath.join(posixpath.dirname(rel), spec))
                if not js_resolves(repo, base):
                    hint = moved_hint.get(base) or moved_hint.get(posixpath.splitext(base)[0]) or ""
                    out.append((rel, ln, spec, hint))
    return out


def parse_plan_table(text):
    """Return list of (from, to, note) from the first markdown table whose header starts with From | To."""
    rows = []
    lines = text.splitlines()
    in_table = False
    for line in lines:
        s = line.strip()
        if not in_table:
            if re.match(r"^\|\s*From\s*\|\s*To\s*\|", s, re.I):
                in_table = True
            continue
        if not s.startswith("|"):
            if rows:
                break
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 2 or set(cells[0]) <= set("-: "):
            continue
        frm, to = cells[0].strip("`"), cells[1].strip("`")
        note = cells[2].strip("`") if len(cells) > 2 else ""
        if frm and frm.lower() not in ("from", "…", "..."):
            rows.append((frm, to, note))
    return rows


CODE_EXT = SOURCE_EXT_FOR_MODULE | {".swift", ".rb", ".cs", ".c", ".cpp", ".h", ".m", ".mm", ".scala"}
# manifests/build files are source-extension files that list other files BY NAME, so bare names count there
MANIFEST_NAMES = {"Package.swift", "build.gradle.kts", "settings.gradle.kts", "Cargo.toml", "go.mod", "composer.json", "package.json"}


def bare_name(path):
    """Distinctive bare file name (no dir, no extension) or None."""
    stem, ext = os.path.splitext(path.strip().rstrip("/"))
    base = os.path.basename(stem)
    if any(ch in path for ch in "*?[]") or ext == "" or base in GENERIC_NAMES or len(base) < 5:
        return None
    return base


def tokens_for(path):
    """Strings that would reference the old location."""
    toks = set()
    p = path.strip().rstrip("/")
    # glob rows: keep only the literal prefix (e.g. plugins/*.py -> plugins/)
    if any(ch in p for ch in "*?[]"):
        prefix = re.split(r"[*?\[]", p)[0].rstrip("/")
        if prefix and "/" in prefix or len(prefix) > 3:
            toks.add(prefix + "/")
        return toks
    toks.add(p)
    stem, ext = os.path.splitext(p)
    if ext == "" and "/" in p:
        toks.add(p.replace("/", "."))       # package form of a directory (python, java, php-ish)
    if ext in SOURCE_EXT_FOR_MODULE:
        toks.add(stem)                       # import without extension (JS/TS)
        dotted = stem.replace("/", ".")      # python module form
        if "." in dotted and not dotted.endswith(".__init__"):
            toks.add(dotted)
        if dotted.endswith(".__init__"):
            toks.add(dotted[: -len(".__init__")])
    base = os.path.basename(stem)
    if base not in GENERIC_NAMES and len(base) >= 5 and not ext == "":
        toks.add(base)                       # distinctive bare filename
        toks.add(base + ext)                 # file name with extension (manifests, docs, scripts)
    return toks


def iter_files(repo: Path):
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True, text=True, check=True, timeout=120,
        ).stdout
        for rel in out.split("\0"):
            if rel and not any(part in EXCLUDE_DIRS for part in Path(rel).parts[:-1]):
                yield rel
        return
    except Exception:
        pass
    for root, dirs, names in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for n in names:
            yield os.path.relpath(os.path.join(root, n), repo)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo")
    ap.add_argument("--plan", help="migration plan markdown with the From|To table")
    ap.add_argument("--moves", action="append", default=[], help="FROM=TO pairs (repeatable), alternative to --plan")
    ap.add_argument("--allow", action="append", default=[], help="file path or glob whose hits are expected (shims); repeatable")
    ap.add_argument("--ignore-docs", action="store_true", help="skip Markdown/RST files (default: included, since READMEs link to paths)")
    ap.add_argument("--json", help="write hits to this JSON file")
    ap.add_argument("--no-imports", action="store_true", help="skip the relative-import resolution check")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        sys.exit(2)
    moves = []
    if args.plan:
        plan_path = Path(args.plan) if Path(args.plan).is_absolute() else repo / args.plan
        if not plan_path.exists():
            print(f"plan not found: {plan_path}", file=sys.stderr)
            sys.exit(2)
        moves = parse_plan_table(plan_path.read_text(encoding="utf-8", errors="replace"))
        if not moves:
            print("no `| From | To | Note |` table found in the plan", file=sys.stderr)
            sys.exit(2)
    for m in args.moves:
        if "=" not in m:
            print(f"bad --moves value (expected FROM=TO): {m}", file=sys.stderr)
            sys.exit(2)
        f, t = m.split("=", 1)
        moves.append((f.strip(), t.strip(), ""))

    plan_rel = None
    if args.plan:
        try:
            plan_rel = str(plan_path.resolve().relative_to(repo))
        except ValueError:
            plan_rel = None
    allow = list(args.allow)
    # the plan itself and the other architecture reports legitimately mention old paths
    allow += ["docs/architecture-*.md", "*/architecture-audit.md", "*/architecture-migration-plan.md", "*/architecture-validation.md", "*/restructure-evidence/*"]
    if plan_rel:
        allow.append(plan_rel)

    token_map = {}  # token -> (from, to, new_forms)
    for frm, to, _ in moves:
        if frm == to:
            continue
        new_forms = tokens_for(to)
        for t in tokens_for(frm):
            # a token that is a prefix of the destination would match the new path too
            if to.startswith(t.rstrip("/")) and not t.endswith("/"):
                continue
            token_map.setdefault(t, (frm, to, new_forms))
    # longest tokens first so "src/utils.py" is reported rather than "utils"
    tokens = sorted(token_map, key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(t) for t in tokens)) if tokens else None

    hits = []
    for rel in (iter_files(repo) if pattern else []):
        if os.path.splitext(rel)[1].lower() in BINARY_EXT:
            continue
        if args.ignore_docs and os.path.splitext(rel)[1].lower() in (".md", ".rst", ".txt"):
            continue
        if any(fnmatch.fnmatch(rel, a) or rel == a for a in allow):
            continue
        try:
            text = (repo / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not pattern.search(text):
            continue
        for ln, line, m in ((ln, line, m) for ln, line in enumerate(text.splitlines(), 1) for m in pattern.finditer(line)):
            tok = m.group(0)
            frm, to, new_forms = token_map[tok]
            if tok == bare_name(frm):
                ext_r = os.path.splitext(rel)[1].lower()
                if ext_r in (".md", ".mdx", ".rst", ".txt", ".html"):
                    continue  # prose naming a class; the file-name-with-extension token covers real file mentions
                if ext_r in CODE_EXT and os.path.basename(rel) not in MANIFEST_NAMES:
                    continue  # a class/identifier named like the file, not a path reference
            # the line references the NEW location and the token is just a substring of it
            # (e.g. token "plugins" inside "app.plugins.hello") — not stale
            if any(nf in line and tok in nf and len(nf) > len(tok) for nf in new_forms):
                continue
            # a hit on a bare filename inside its own new location is not stale
            if rel == to or rel.startswith(to.rstrip("/") + "/"):
                if tok == os.path.basename(frm) or tok == os.path.splitext(os.path.basename(frm))[0]:
                    continue
            hits.append({"file": rel, "line": ln, "token": tok, "from": frm, "to": to, "text": line.strip()[:200]})

    broken = []
    if not args.no_imports:
        moved_hint = {}
        for frm, to, _ in moves:
            f = frm.strip().rstrip("/")
            moved_hint[f] = to
            moved_hint[os.path.splitext(f)[0]] = to
        files = [r for r in iter_files(repo) if not any(fnmatch.fnmatch(r, a) or r == a for a in allow)]
        broken = unresolved_relative_imports(repo, files, moved_hint)

    if args.json:
        Path(args.json).write_text(json.dumps({"stale": hits, "unresolved_relative_imports": [
            {"file": f, "line": l, "spec": s_, "moved_to": h} for f, l, s_, h in broken]}, indent=2), encoding="utf-8")

    def report_broken():
        if not broken:
            return
        print(f"\n{len(broken)} relative import(s) that do not resolve on disk:")
        # imports that point at a moved file first: they are the ones the migration broke
        ordered = sorted(broken, key=lambda x: (not x[3], x[0], x[1]))
        for f, l, s_, h in ordered[:30]:
            print(f"  {f}:{l}  {s_}" + (f"   <- was moved to {h}" if h else ""))
        if len(broken) > 30:
            by_dir = {}
            for f, *_ in ordered[30:]:
                by_dir[posixpath.dirname(f)] = by_dir.get(posixpath.dirname(f), 0) + 1
            print(f"  … {len(broken) - 30} more, by folder:")
            for d, n in sorted(by_dir.items(), key=lambda kv: -kv[1])[:10]:
                print(f"      {d or '.'}: {n}")
        print("Compare with the original commit: imports unresolved there too are pre-existing.")

    if not hits:
        if tokens:
            print(f"No stale references found for {len(moves)} relocation rows ({len(tokens)} search tokens).")
        else:
            print("nothing to check in the relocation table")
        report_broken()
        if broken:
            sys.exit(1)
        return
    by_file = {}
    for h in hits:
        by_file.setdefault(h["file"], []).append(h)
    print(f"{len(hits)} possible stale reference(s) in {len(by_file)} file(s):\n")
    for f, hs in sorted(by_file.items()):
        print(f"  {f}")
        for h in hs[:20]:
            print(f"    L{h['line']:<5} [{h['token']}]  {h['text']}")
        if len(hs) > 20:
            print(f"    … {len(hs) - 20} more")
    print("\nEach hit is either an intentional compatibility shim (add it with --allow) or a missed reference.")
    report_broken()
    sys.exit(1)


if __name__ == "__main__":
    main()
