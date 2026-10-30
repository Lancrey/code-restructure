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
`../y`) and Python (`from .x import`, `from ..y import`) files, every relative `url()`,
`@import`, `@use`, `@forward` in CSS/Sass/Less (partials `_x.scss`, `x/_index.scss`), and
every relative `src`/`href`/`srcset`/`poster` in HTML and template files, against the disk and
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
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    ".next", ".nuxt", ".turbo", ".cache", "coverage", ".idea", ".gradle", "Pods",
}
# build outputs: excluded only at the repository root (a Go package `internal/build/` is source)
TOP_LEVEL_EXCLUDE = {"dist", "build", "out", "target"}


def excluded(rel_parts):
    dirs = rel_parts[:-1]
    return any(p in EXCLUDE_DIRS for p in dirs) or (bool(dirs) and dirs[0] in TOP_LEVEL_EXCLUDE)
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


JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte", ".json",
           ".d.ts", ".d.mts", ".d.cts")
JS_SRC = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte"}
REL_JS = re.compile(r"""(?:\bfrom|\bimport|\brequire)\s*\(?\s*['"](\.{1,2}(?:/[^'"\n]*)?)['"]""")
REL_PY = re.compile(r"^\s*from\s+(\.+)([\w.]*)\s+import\s+(.*)$")
JS_SWAP = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}

STYLE_EXT = {".css", ".scss", ".sass", ".less"}
HTML_EXT = {".html", ".htm", ".xhtml", ".tpl", ".twig", ".jinja", ".jinja2", ".j2", ".ejs", ".erb", ".hbs",
            ".mustache", ".njk", ".liquid", ".svelte", ".astro"}
# url(x) / url('x') / url("x") ; @import 'x' / url(x) ; @use 'x' ; @forward 'x'  — one capture group each
CSS_URL = re.compile(r"""url\(\s*(?!['"]?(?:data:|https?:|//|#|%23|\$|@|\{|<))['"]?([^'")\s]+)['"]?\s*\)""")
CSS_IMPORT = re.compile(r"""@(?:import|use|forward)\s+(?:\(reference\)\s*|\(css\)\s*|\(less\)\s*)?(?!url\()['"](?!(?:https?:|//|data:|sass:|~|@|\$))([^'"]+)['"]""")
# src= href= srcset= poster= data-src= ; also <use href> ; ignores absolute, root-relative, anchors, schemes, template expressions
HTML_ATTR = re.compile(r"""\b(src|href|srcset|poster|data-src|xlink:href)\s*=\s*(["'])(?!(?:https?:|//|/|#|mailto:|tel:|data:|javascript:|\{|\$|<\?|%|\s*$))([^"']+)\2""", re.I)


def _style_candidates(base, exts):
    d, n = posixpath.dirname(base), posixpath.basename(base)
    for e in exts:
        yield from (posixpath.join(d, n + e), posixpath.join(d, "_" + n + e), posixpath.join(base, "_index" + e), posixpath.join(base, "index" + e))


def style_resolves(repo, importer, spec, kind, lang=None):
    """Relative CSS/Sass/Less reference -> True (on disk), False (missing), or "uncertain" (needs the compiler).

    `lang` overrides the language taken from the importer's extension (a `<style lang="scss">` block
    in a .vue file). Load paths are not known here; npm packages are resolved inside node_modules
    with the same partial/index conventions, and a bare package name (`@use "pkg"`) whose entry point
    would come from package.json is "uncertain"."""
    spec = spec.split("?")[0].split("#")[0]
    if not spec or spec.startswith("/"):
        return True  # root-relative: a deployment path, not checkable from the tree
    base = posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))
    if (repo / base).is_file():
        return True
    if kind != "import":
        return False
    ext_i = "." + lang if lang else os.path.splitext(importer)[1].lower()
    exts = (".scss", ".sass", ".css") if ext_i in (".scss", ".sass") else (".less", ".css") if ext_i == ".less" else (".css",)
    if any((repo / c).is_file() for c in _style_candidates(base, exts)):
        return True
    # a bare Sass/Less specifier may name an npm package (`@use "bootstrap/scss/bootstrap"`)
    if not spec.startswith(".") and ext_i in (".scss", ".sass", ".less"):
        segs = spec.lstrip("~").split("/")
        pkg = "/".join(segs[:2]) if segs[0].startswith("@") and len(segs) > 1 else segs[0]
        pkg_dir = repo / "node_modules" / pkg
        if pkg_dir.is_dir():
            inside = posixpath.join("node_modules", spec.lstrip("~"))
            if (repo / inside).is_file() or any((repo / c).is_file() for c in _style_candidates(inside, exts)):
                return True
            if spec.lstrip("~") == pkg:
                return "uncertain"  # entry point declared in package.json ("sass"/"style"/"main"): the compiler knows, we don't
            return False  # the package is there, the file inside it is not
    return False


FILE_LIKE = re.compile(r"\.[A-Za-z0-9]{1,6}$")


def html_unresolved(repo, page, attr, value, tag):
    """Return the candidate paths of one attribute that do not exist on disk.

    `src`, `srcset`, `poster`, `data-src` always name files. `href` names a file only on <link>
    (stylesheets, icons, preloads) or when it has a file extension (`about.html`, `doc.pdf`);
    `href="contact"` or `href="../about"` is a navigation URL that a router or the server may
    serve, so it is never reported from a template or page."""
    value = value.strip()
    if not value:
        return []
    if attr in ("href", "xlink:href") and tag not in ("link", "use", "image"):
        if not FILE_LIKE.search(value.split("?")[0].split("#")[0]):
            return []
    if attr == "srcset":
        cands = [p.strip().split()[0] for p in value.split(",") if p.strip()]
    else:
        cands = [value]
    bad = []
    for c in cands:
        c = c.split("?")[0].split("#")[0]
        if not c or c.startswith(("/", "http", "//", "{", "$", "<", "%", "#")):
            continue
        base = posixpath.normpath(posixpath.join(posixpath.dirname(page), c))
        if not (repo / base).is_file() and not (repo / base).is_dir():
            bad.append(c)
    return bad


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


def py_resolves(repo, importer, dots, mod, names):
    """Return the list of unresolved specs for one relative import (empty = fine)."""
    pkg = posixpath.dirname(importer)
    for _ in range(len(dots) - 1):
        pkg = posixpath.dirname(pkg)
    if mod:
        base = posixpath.join(pkg, mod.replace(".", "/"))
        ok = (repo / (base + ".py")).is_file() or (repo / (base + ".pyi")).is_file() or (repo / base).is_dir()
        return [] if ok else [dots + mod]
    # `from . import a, b as c`: each name is a sibling module/package, or a symbol defined
    # or imported in the package's __init__.py — anything else is unresolved
    init_txt = ""
    init = repo / pkg / "__init__.py"
    if init.is_file():
        init_txt = init.read_text(encoding="utf-8", errors="replace")
    bad = []
    for raw in names.strip("() ").split(","):
        name = raw.strip().split(" as ")[0].strip()
        if not name or name == "*" or name.startswith("#"):
            continue
        here = posixpath.join(pkg, name)
        if (repo / (here + ".py")).is_file() or (repo / (here + ".pyi")).is_file() or (repo / here).is_dir():
            continue
        if init_binds(name, init_txt):
            continue
        bad.append(dots + " import " + name)
    return bad


def init_binds(name, init_txt):
    """True if __init__.py binds `name`: def/class/assignment, `import x as name`, `from m import name`."""
    n = re.escape(name)
    if re.search(r"^\s*(?:def|class)\s+%s\b" % n, init_txt, re.M):
        return True
    if re.search(r"^\s*%s\s*(?::[^=\n]+)?=[^=]" % n, init_txt, re.M):
        return True
    for m in re.finditer(r"^\s*import\s+(.+)$", init_txt, re.M):
        for item in m.group(1).split(","):
            parts = item.strip().split()
            bound = parts[-1] if len(parts) == 3 and parts[1] == "as" else parts[0].split(".")[0]
            if bound == name:
                return True
    for m in re.finditer(r"^\s*from\s+\S+\s+import\s+\(?([^)#\n]+)", init_txt, re.M):
        for item in m.group(1).split(","):
            parts = item.strip().split()
            if parts and (parts[-1] if len(parts) == 3 and parts[1] == "as" else parts[0]) == name:
                return True
    return False


UNCERTAIN = []  # (file, line, spec): references this script cannot settle either way
UNCERTAIN_TPL = []  # relative asset paths in server-rendered templates (resolve against the route, not the file)


def hint_for(moved_hint, importer, spec):
    base = posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec.split("?")[0].split("#")[0]))
    stem = posixpath.splitext(base)[0]
    d, n = posixpath.dirname(stem), posixpath.basename(stem)
    for key in (base, stem, posixpath.join(d, "_" + n), posixpath.join(stem, "_index"), posixpath.join(stem, "index")):
        if moved_hint.get(key):
            return moved_hint[key]
    for frm, to in moved_hint.items():  # a moved directory: img/bg.png -> assets/img/bg.png
        if base.startswith(frm + "/"):
            return posixpath.join(to, base[len(frm) + 1:])
    return ""


def unresolved_relative_imports(repo, files, moved_hint, dests=()):
    """Yield (file, line, spec, hint) for relative references that do not resolve on disk.
    hint = where the target went (a moved path), or "(this file was moved)" when the referencing
    file is itself a destination of the plan — both mean the plan caused the break."""
    out = []
    for rel in files:
        self_moved = any(rel == d or rel.startswith(d.rstrip("/") + "/") for d in dests)
        ext = os.path.splitext(rel)[1].lower()
        if rel.endswith(".blade.php"):
            ext = ".html"
        sfc = ext in (".vue", ".svelte", ".astro")  # script + template + style in one file: check all three
        is_template = ext in HTML_EXT and ext not in (".html", ".htm", ".xhtml") and not sfc
        style_lang = None  # language of the current <style lang="…"> block in an SFC
        if ext not in JS_SRC and ext != ".py" and ext not in STYLE_EXT and ext not in HTML_EXT:
            continue
        try:
            text = (repo / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for ln, line in enumerate(text.splitlines(), 1):
            if sfc:
                ms = re.search(r"<style\b[^>]*\blang\s*=\s*[\"']?(scss|sass|less|postcss|css)", line, re.I)
                if ms:
                    style_lang = ms.group(1).lower()
                elif re.search(r"<style\b", line, re.I):
                    style_lang = "css"
                if re.search(r"</style>", line, re.I):
                    pass  # reset after processing this line (below)
            if ext in STYLE_EXT or sfc:
                for m in CSS_URL.finditer(line):
                    if not style_resolves(repo, rel, m.group(1), "url"):
                        if ext in (".scss", ".sass", ".less") or style_lang in ("scss", "sass", "less"):
                            # Sass copies url() verbatim: it resolves against the COMPILED css, whose location this
                            # script does not know. Not resolving from the source is a doubt, not a verdict.
                            UNCERTAIN.append((rel, ln, "url(%s)" % m.group(1)))
                        else:
                            out.append((rel, ln, "url(%s)" % m.group(1), hint_for(moved_hint, rel, m.group(1))))
                for m in CSS_IMPORT.finditer(line):
                    res = style_resolves(repo, rel, m.group(1), "import", lang=style_lang if sfc else None)
                    if res == "uncertain":
                        UNCERTAIN.append((rel, ln, "@import %s (package entry point from package.json)" % m.group(1)))
                    elif not res:
                        out.append((rel, ln, "@import %s" % m.group(1), hint_for(moved_hint, rel, m.group(1))))
                if sfc and re.search(r"</style>", line, re.I):
                    style_lang = None
                if not sfc:
                    continue
            if ext in HTML_EXT or sfc:
                for m in HTML_ATTR.finditer(line):
                    tags = re.findall(r"<([A-Za-z][\w:-]*)", line[:m.start()])
                    tag = tags[-1].lower() if tags else ""
                    for bad in html_unresolved(repo, rel, m.group(1).lower(), m.group(3), tag):
                        if is_template:
                            # a server-rendered template is addressed by its ROUTE, not its file location:
                            # a relative asset path resolves against the URL, which this script does not know
                            UNCERTAIN_TPL.append((rel, ln, f"{m.group(1)}={bad}"[:80]))
                        else:
                            out.append((rel, ln, f"{m.group(1)}={bad}"[:80], hint_for(moved_hint, rel, bad)))
                if not sfc:  # inline style="…url()" and <style> blocks: plain CSS, relative to the page
                    for m in CSS_URL.finditer(line):
                        if not style_resolves(repo, rel, m.group(1), "url"):
                            out.append((rel, ln, "url(%s)" % m.group(1), hint_for(moved_hint, rel, m.group(1))))
                    continue
            if ext == ".py":
                m = REL_PY.match(line)
                if m:
                    for spec in py_resolves(repo, rel, m.group(1), m.group(2), m.group(3)):
                        stem = spec.split(" import ")[-1].lstrip(".").split(".")[0]
                        pkg_dir = posixpath.dirname(rel)
                        for _ in range(len(m.group(1)) - 1):
                            pkg_dir = posixpath.dirname(pkg_dir)
                        hint = moved_hint.get(posixpath.join(pkg_dir, stem)) or moved_hint.get(posixpath.join(pkg_dir, stem) + ".py") or ""
                        out.append((rel, ln, spec, hint))
                continue
            for m in REL_JS.finditer(line):
                spec = m.group(1)
                base = posixpath.normpath(posixpath.join(posixpath.dirname(rel), spec))
                if not js_resolves(repo, base):
                    hint = moved_hint.get(base) or moved_hint.get(posixpath.splitext(base)[0]) or ""
                    out.append((rel, ln, spec, hint))
        if self_moved:
            out = [(f, l, sp, h or "(this file was moved)") if f == rel else (f, l, sp, h) for f, l, sp, h in out]
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
    stem, ext = os.path.splitext(p)
    if ext == "" and "/" not in p:
        toks.add(p + "/")                   # a bare directory name (img, lib): only its path use counts, not the word
        return toks
    toks.add(p)
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
            if rel and not excluded(Path(rel).parts):
                yield rel
        return
    except Exception:
        pass
    for root, dirs, names in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not (Path(root) == repo and d in TOP_LEVEL_EXCLUDE)]
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
    ap.add_argument("--all-unresolved", action="store_true", help="list every unresolved reference, including those not explained by the plan")
    ap.add_argument("--before", metavar="JSON", help="--json output of a run on the ORIGINAL commit: any unresolved reference that is not in it is new, and fails the run even when the plan does not explain it")
    ap.add_argument("--expand-globs", action="store_true", help="print the concrete files each glob From row matches on disk (run it BEFORE moving, to turn glob rows into explicit rows in the plan) and exit")
    ap.add_argument("--in-progress", action="store_true",
                    help="mid-migration run: rows whose From still exists and To does not are PENDING and skipped "
                         "(reported as a count); without this flag every row of the plan must be done")
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

    if args.expand_globs:
        for frm, to, note in moves:
            if any(ch in frm for ch in "*?[]"):
                for p_ in sorted(repo.glob(frm.strip().rstrip("/"))):
                    if p_.is_file():
                        relp = str(p_.relative_to(repo))
                        dest = to.replace("<name>", posixpath.splitext(posixpath.basename(relp))[0]) if "<name>" in to else posixpath.join(to.rstrip("/"), posixpath.basename(relp)) if to.endswith("/") else to
                        print(f"| {relp} | {dest} | {note or 'from ' + frm} |")
        return

    plan_rel = None
    if args.plan:
        try:
            plan_rel = str(plan_path.resolve().relative_to(repo))
        except ValueError:
            plan_rel = None
    allow = list(args.allow)
    # the plan itself and the other architecture reports legitimately mention old paths
    for name in ("architecture-audit*.md", "architecture-migration-plan*.md", "architecture-validation*.md"):
        allow += [name, "*/" + name]
    allow += ["restructure-evidence/*", "*/restructure-evidence/*"]
    if plan_rel:
        allow.append(plan_rel)

    # a From listed several times is a SPLIT (one module becomes several): references to the old
    # location are stale for all of them, and any destination's form counts as "already updated"
    dests = {}
    for frm, to, _ in moves:
        if frm != to:
            dests.setdefault(frm, []).append(to)
    token_map = {}  # token -> (from, to, new_forms)
    for frm, tos in dests.items():
        new_forms = set()
        for to in tos:
            new_forms |= tokens_for(to)
        for t in tokens_for(frm):
            # a token that is a prefix of the destination would match the new path too
            if any(to.startswith(t.rstrip("/")) for to in tos) and not t.endswith("/"):
                continue
            token_map.setdefault(t, (frm, " | ".join(tos), new_forms))
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
            # word boundaries: "app.db" inside "myapp.db" or "app/utils" inside "app/utils_v2" is another path
            before = line[m.start() - 1] if m.start() > 0 else ""
            after = line[m.end()] if m.end() < len(line) else ""
            if (tok[0].isalnum() or tok[0] == "_") and (before.isalnum() or before == "_"):
                continue
            if (tok[-1].isalnum() or tok[-1] == "_") and (after.isalnum() or after == "_"):
                continue
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
            if any(rel == t_ or rel.startswith(t_.rstrip("/") + "/") for t_ in to.split(" | ")):
                if tok == os.path.basename(frm) or tok == os.path.splitext(os.path.basename(frm))[0]:
                    continue
            hits.append({"file": rel, "line": ln, "token": tok, "from": frm, "to": to, "text": line.strip()[:200]})

    # relocation table vs disk: after the migration every From must be gone (unless it is a shim
    # listed with --allow) and every To must exist. With --in-progress, a row whose From is still
    # there and whose To is absent has simply not been done yet: it is skipped everywhere.
    table_problems, pending = [], set()
    for frm, to, _ in moves:
        f, t = frm.strip().rstrip("/"), to.strip().rstrip("/")
        if f == t:
            continue
        if any(ch in f for ch in "*?[]"):
            left = [str(p.relative_to(repo)) for p in repo.glob(f) if p.is_file()]
            if left:
                if args.in_progress:
                    pending.add(frm)
                else:
                    table_problems.append(f"From glob still matches {len(left)} file(s) on disk: {f}  (e.g. {left[0]})")
            continue
        from_exists = (repo / f).exists()
        to_checkable = not any(ch in t for ch in "*?[]<>")
        to_exists = (repo / t).exists() if to_checkable else True
        if args.in_progress and from_exists and not to_exists:
            pending.add(frm)
            continue
        if from_exists and not any(fnmatch.fnmatch(f, a) or f == a for a in allow):
            table_problems.append(f"From still exists on disk: {f}  (move not done, or an unlisted shim: add --allow {f})")
        if to_checkable and not to_exists:
            table_problems.append(f"To does not exist on disk: {t}  (from {f})")
    if pending:
        hits = [h for h in hits if h["from"] not in pending]
        print(f"{len(pending)} pending row(s) skipped (--in-progress): " + ", ".join(sorted(pending)[:8]) + (" …" if len(pending) > 8 else ""))

    broken = []
    if not args.no_imports:
        moved_hint = {}
        for frm, to, _ in moves:
            f = frm.strip().rstrip("/")
            moved_hint[f] = to
            moved_hint[os.path.splitext(f)[0]] = to
        files = [r for r in iter_files(repo) if not any(fnmatch.fnmatch(r, a) or r == a for a in allow)]
        dests = [to.strip().rstrip("/") for _, to, _ in moves if not any(ch in to for ch in "*?[]<>")]
        broken = unresolved_relative_imports(repo, files, moved_hint, dests)
    caused = [b for b in broken if b[3]]
    unrelated = [b for b in broken if not b[3]]
    new_since_before = []
    if args.before:
        try:
            prev = json.loads(Path(args.before).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"--before file unreadable: {e}", file=sys.stderr)
            sys.exit(2)
        seen_before = set()
        for u in prev.get("unresolved_relative_imports", []):
            seen_before.add((u["file"], u["spec"]))
            # the file may have moved since: the same broken reference now lives at the destination
            for frm, to, _ in moves:
                f, t = frm.strip().rstrip("/"), to.strip().rstrip("/")
                if any(ch in f + t for ch in "*?[]<>"):
                    continue
                if u["file"] == f:
                    seen_before.add((t, u["spec"]))
                elif u["file"].startswith(f + "/"):
                    seen_before.add((posixpath.join(t, u["file"][len(f) + 1:]), u["spec"]))
        new_since_before = [b for b in unrelated if (b[0], b[2]) not in seen_before]
        unrelated = [b for b in unrelated if (b[0], b[2]) in seen_before]
        # a reference already broken in the file BEFORE it moved is pre-existing, not caused by the move
        still_caused = []
        for b in caused:
            if b[3] == "(this file was moved)" and (b[0], b[2]) in seen_before:
                unrelated.append(b)
            else:
                still_caused.append(b)
        caused = still_caused

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps({"stale": hits, "relocation_table": table_problems, "unresolved_relative_imports": [
            {"file": f, "line": l, "spec": s_, "moved_to": h} for f, l, s_, h in broken],
            "uncertain": [{"file": f, "line": l, "spec": s_} for f, l, s_ in UNCERTAIN + UNCERTAIN_TPL]}, indent=2), encoding="utf-8")

    if table_problems:
        print(f"{len(table_problems)} relocation-table row(s) disagree with the disk:")
        for t in table_problems:
            print(f"  {t}")
        print()

    def report_broken():
        if new_since_before:
            print(f"\n{len(new_since_before)} relative reference(s) unresolved now and resolved on the original commit, not explained by the plan — a move the plan does not list, or a path edit gone wrong:")
            for f, l, s_, h in sorted(new_since_before)[:80]:
                print(f"  {f}:{l}  {s_}")
        if UNCERTAIN_TPL:
            print(f"\n{len(UNCERTAIN_TPL)} relative asset path(s) in server-rendered templates do not resolve from the template's location; they resolve against the page URL, which this script does not know — check them in a browser or with the framework's asset helper:")
            for f, l, s_ in sorted(UNCERTAIN_TPL)[:10]:
                print(f"  {f}:{l}  {s_}")
            if len(UNCERTAIN_TPL) > 10:
                print(f"  … {len(UNCERTAIN_TPL) - 10} more")
        if UNCERTAIN:
            print(f"\n{len(UNCERTAIN)} Sass/Less reference(s) this script cannot settle (url() resolves against the compiled CSS; a bare package entry point comes from package.json) — compare the compiled output instead:")
            for f, l, s_ in sorted(UNCERTAIN)[:10]:
                print(f"  {f}:{l}  {s_}")
            if len(UNCERTAIN) > 10:
                print(f"  … {len(UNCERTAIN) - 10} more")
        if caused:
            print(f"\n{len(caused)} relative reference(s) broken by the listed moves:")
            for f, l, s_, h in sorted(caused)[:80]:
                print(f"  {f}:{l}  {s_}   <- " + (h if h.startswith("(") else f"was moved to {h}"))
            if len(caused) > 80:
                print(f"  … {len(caused) - 80} more")
        if unrelated:
            shown = unrelated if args.all_unresolved else unrelated[:10]
            print(f"\n{len(unrelated)} relative reference(s) that do not resolve on disk and are NOT explained by the listed moves"
                  f" (pre-existing, vendored demo pages, or a move missing from the plan):")
            for f, l, s_, h in sorted(shown):
                print(f"  {f}:{l}  {s_}")
            if len(unrelated) > len(shown):
                by_dir = {}
                for f, *_ in unrelated:
                    by_dir[posixpath.dirname(f)] = by_dir.get(posixpath.dirname(f), 0) + 1
                print(f"  … {len(unrelated) - len(shown)} more (--all-unresolved to list), by folder:")
                for d, n in sorted(by_dir.items(), key=lambda kv: -kv[1])[:10]:
                    print(f"      {d or '.'}: {n}")
            print("  Run the same command on the original commit: anything unresolved there too is pre-existing and not yours.")

    if not hits:
        if tokens:
            print(f"No stale references found for {len(moves)} relocation rows ({len(tokens)} search tokens).")
        else:
            print("nothing to check in the relocation table")
        report_broken()
        if caused or table_problems or new_since_before:
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
