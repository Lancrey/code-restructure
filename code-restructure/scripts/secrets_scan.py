#!/usr/bin/env python3
"""Find hard-coded secrets in a repository and prove that reports do not leak them.

Two modes, both standard library only.

SCAN (default): lists candidate secrets as `path:line NAME = <redacted>` — the
value is never printed or written. Covers `define('X_KEY', '...')`, `X = "..."`,
`x: "..."`, `X=...` in .env files, JSON/YAML keys, URL credentials
(`scheme://user:pass@host`) and well-known token formats (AWS, GitHub, Slack,
Stripe, Google, private-key blocks). Each hit has a confidence: `high` (value
looks random or matches a known token format) or `low` (name looks secret, value
looks like a word). Hits in files that the migration plan moves are tagged
[moved] (--plan).

VERIFY (--verify PATH...): re-scans the repo, keeps the values in memory only, and
greps the given files or folders (reports, evidence) for any of them — every high-confidence
value, plus low-confidence values that contain a digit or symbol (so a short password like
"@+ka6_9" is caught; a plain word such as "authority" is not, since it would match prose). Exit 1 and
print `path:line NAME` when a value is found. Run it on the reports and on the
evidence folder after every phase.

Usage:
  python3 secrets_scan.py <repo> [--plan docs/architecture-migration-plan.md]
                          [--out restructure-evidence/secrets-redacted.txt]
                          [--exclude GLOB ...] [--json hits.json]
  python3 secrets_scan.py <repo> --verify docs/architecture-audit.md restructure-evidence/

Git-ignored files (a local .env, config.local.php…) ARE scanned by default, tagged
[git-ignored, not committed] in the list, and their values are part of --verify: they are
not in the repository's history, but they are on disk and can be pasted into a report.
--no-ignored turns this off. --strict with --verify also checks plain-word low-confidence
values ("sunshine"), at the cost of false alarms on ordinary words.

Exit code: scan 0 (hits are information, not failure); verify 0 clean / 1 leak; 2 usage.
"""
import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path

EXCLUDE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "vendor", ".venv", "venv", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    ".next", ".nuxt", ".turbo", ".cache", "coverage", ".idea", ".gradle", "Pods",
}
BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".tar", ".jar",
    ".class", ".pyc", ".so", ".dylib", ".dll", ".exe", ".woff", ".woff2", ".ttf", ".otf",
    ".mp3", ".mp4", ".mov", ".sqlite", ".db", ".lock", ".lockb", ".map", ".min.js",
}
TOP_LEVEL_EXCLUDE = {"dist", "build", "out", "target"}  # build outputs, only at the repo root
MAX_BYTES = 2_000_000
IGNORED = set()  # git-ignored files seen by the last iter_files call (CLI use; reset at each call)

NAME_WORDS = (r"secret|passw(?:or)?d|pwd|passphrase|token|salt|credential|api[_-]?key|"
              r"private[_-]?key|access[_-]?key|crypto|key")
# (?-i:(?![a-z])) rejects a lowercase continuation ("keyboard", "tokenizer") but accepts camelCase ("passwordHash")
# the keyword must start a word part: preceded by nothing/_/-/./digit, or written in CamelCase (apiKey) —
# so "monkey", "donkey", "turkey" are not secrets, while API_KEY, apiKey, SESSKEY, passwordHash are
NAME_RX = r"""[A-Za-z0-9_.\-]*?(?:(?<![A-Za-z])|(?-i:(?=[A-Z])))(?:%s)(?-i:(?![a-z]))[A-Za-z0-9_.\-]*""" % NAME_WORDS
ASSIGN = re.compile(
    r"""(?P<name>["']?%s["']?)\s*(?:=>|=|:|,)\s*(?:[A-Za-z_]\w*\(\s*)?(?P<q>["'])(?P<val>[^"'\n]{6,})(?P=q)""" % NAME_RX, re.I)
# YAML / unquoted mapping value:  MYSQL_ROOT_PASSWORD: s3cr3t   (also "- KEY=value" list items in compose files)
YAML_KV = re.compile(r"""^\s*-?\s*["']?(?P<name>%s)["']?\s*[:=]\s*(?P<val>[^\s"'#\[\{][^\s#]{5,})\s*(?:#.*)?$""" % NAME_RX, re.I)
DOTENV = re.compile(r"^\s*(?:export\s+)?(?P<name>[A-Z][A-Z0-9_]*(?:%s)[A-Z0-9_]*)\s*=\s*(?P<val>[^\s'\"#][^\s#]{5,})\s*$" % NAME_WORDS.upper())
URL_CRED = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^/\s:@'\"]+:(?P<val>[^@\s/'\"]{3,})@")
TOKEN_FORMATS = [
    ("aws-access-key-id", re.compile(r"\b(AKIA[0-9A-Z]{16})\b")),
    ("github-token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{30,})\b")),
    ("slack-token", re.compile(r"\b(xox[abprs]-[A-Za-z0-9\-]{10,})\b")),
    ("stripe-key", re.compile(r"\b((?:sk|rk)_live_[A-Za-z0-9]{16,})\b")),
    ("google-api-key", re.compile(r"\b(AIza[0-9A-Za-z_\-]{35})\b")),
    ("anthropic-or-openai-key", re.compile(r"\b(sk-(?:ant-)?[A-Za-z0-9_\-]{30,})\b")),
    ("private-key-block", re.compile(r"(-----BEGIN [A-Z ]*PRIVATE KEY-----)")),
]
PLACEHOLDER = re.compile(
    r"^(?:x+|\*+|\.+|changeme|change[_-]?me|your[_-].*|my[_-]?(?:secret|key|password|token)|example.*|"
    r"dummy.*|test.*|todo|none|null|false|true|password|secret|token|<.*>|\$\{.*\}|\{\{.*\}\}|%\(.*\)s|%.*%)$", re.I)
GENERIC_SECRET_NAMES = {"key", "keys", "token", "tokens", "salt", "auth", "credential", "credentials", "crypto"}
CODE_LOOKUP = re.compile(r"(getenv|environ|process\.env|config\(|ENV\[|\$_(?:ENV|SERVER)|System\.getenv|os\.Getenv)", re.I)


def clean_name(n):
    return n.strip("\"' ")


def confidence(val):
    if re.search(r"\s", val):
        return None  # prose, not a secret
    if re.match(r"^[a-z][a-z0-9+.\-]*://", val, re.I) and "@" not in val:
        return "low"  # a URL without credentials (TOKEN_URL, AUTH_ENDPOINT…)
    if re.match(r"^[/~.]|^[A-Za-z]:\\", val) and not re.search(r"\d", val):
        return "low"  # a file path (KEY_PATH = "/etc/ssl/private/x.key")
    has_digit = bool(re.search(r"\d", val))
    has_alpha = bool(re.search(r"[A-Za-z]", val))
    has_sym = bool(re.search(r"[^A-Za-z0-9_\-./:]", val))
    if len(val) >= 12 and ((has_digit and has_alpha) or has_sym):
        return "high"
    if len(val) >= 8 and has_digit and has_alpha:
        return "high"
    if len(val) >= 6 and has_sym and (has_digit or has_alpha):
        return "high"  # short but clearly not a word: "@+ka6_9"
    return "low"


def iter_files(repo, excludes, include_ignored=True):
    """Tracked and untracked files; by default also git-IGNORED files (a local .env is exactly where
    credentials live, and it can be pasted into a report like anything else). Dependency and build
    directories stay excluded either way."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, text=True, check=True, timeout=120).stdout
        rels = [r for r in out.split("\0") if r]
        IGNORED.clear()
        if include_ignored:
            ign = subprocess.run(["git", "-C", str(repo), "ls-files", "-z", "--others", "--ignored", "--exclude-standard"],
                                 capture_output=True, text=True, check=True, timeout=120).stdout
            IGNORED.update(r for r in ign.split("\0") if r)
            rels += sorted(IGNORED)
    except Exception:
        rels = []
        for root, dirs, names in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not (Path(root) == repo and d in TOP_LEVEL_EXCLUDE)]
            rels += [os.path.relpath(os.path.join(root, n), repo) for n in names]
    for rel in rels:
        parts = Path(rel).parts
        if any(p in EXCLUDE_DIRS for p in parts[:-1]) or (len(parts) > 1 and parts[0] in TOP_LEVEL_EXCLUDE):
            continue
        if os.path.splitext(rel)[1].lower() in BINARY_EXT:
            continue
        if any(fnmatch.fnmatch(rel, g) for g in excludes):
            continue
        yield rel


def parse_plan_sources(plan_text):
    rows, in_table = [], False
    for line in plan_text.splitlines():
        s = line.strip()
        if not in_table:
            if re.match(r"^\|\s*From\s*\|\s*To\s*\|", s, re.I):
                in_table = True
            continue
        if not s.startswith("|"):
            if rows:
                break
            continue
        cells = [c.strip().strip("`") for c in s.strip("|").split("|")]
        if cells and cells[0] and not set(cells[0]) <= set("-: ") and cells[0].lower() not in ("from", "…", "..."):
            rows.append(cells[0])
    return rows


def is_moved(rel, sources):
    for s in sources:
        s = s.rstrip("/")
        if any(ch in s for ch in "*?["):
            if fnmatch.fnmatch(rel, s):
                return True
        elif rel == s or rel.startswith(s + "/"):
            return True
    return False


def scan_file(path, rel):
    hits = []
    try:
        if path.stat().st_size > MAX_BYTES:
            return hits
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return hits
    if "\0" in text:
        return hits
    is_env = os.path.basename(rel).startswith(".env") or rel.endswith((".env", ".ini", ".cfg", ".properties", ".conf"))
    is_yaml = rel.endswith((".yml", ".yaml", ".toml"))
    for ln, line in enumerate(text.splitlines(), 1):
        if len(line) > 2000:
            continue
        seen = set()
        for m in ASSIGN.finditer(line):
            val = m.group("val")
            if PLACEHOLDER.match(val) or CODE_LOOKUP.search(val) or val.startswith(("$", "%", "<", "{", "#{")):
                continue  # interpolation or template, not a literal
            c = confidence(val)
            if not c:
                continue
            name = clean_name(m.group("name"))
            # a bare `key`/`token`/`salt` is usually a map key or a loop variable (`key: 'playlist'`):
            # keep it only when the value itself looks like a secret
            if c == "low" and name.lower() in GENERIC_SECRET_NAMES:
                continue
            hits.append((rel, ln, name, val, c))
            seen.add(val)
        if is_env:
            m = DOTENV.match(line)
            if m and m.group("val") not in seen and not PLACEHOLDER.match(m.group("val")) and not CODE_LOOKUP.search(m.group("val")):
                c = confidence(m.group("val"))
                if c:
                    hits.append((rel, ln, m.group("name"), m.group("val"), c))
                    seen.add(m.group("val"))
        if is_yaml or is_env:
            m = YAML_KV.match(line)
            if m and m.group("val") not in seen and not PLACEHOLDER.match(m.group("val")) and not CODE_LOOKUP.search(m.group("val")) \
                    and not m.group("val").startswith(("$", "%", "<", "{")):
                c = confidence(m.group("val"))
                if c:
                    hits.append((rel, ln, m.group("name"), m.group("val"), c))
                    seen.add(m.group("val"))
        for m in URL_CRED.finditer(line):
            val = m.group("val")
            if val not in seen and not PLACEHOLDER.match(val) and not val.startswith(("$", "{", "<", "%")) \
                    and not re.search(r"[\\\[\]()*|?]", val):
                hits.append((rel, ln, "url-credential", val, "high"))
                seen.add(val)
        for name, rx in TOKEN_FORMATS:
            for m in rx.finditer(line):
                val = m.group(1)
                if val not in seen:
                    hits.append((rel, ln, name, val, "high"))
                    seen.add(val)
    return hits


def scan_repo(repo, excludes, include_ignored=True):
    hits = []
    for rel in iter_files(repo, excludes, include_ignored):
        hits += scan_file(repo / rel, rel)
    return hits


def verify(repo, hits, targets, strict=False):
    values = {}
    for rel, ln, name, val, c in hits:
        # every high-confidence value; low-confidence ones only when they are not a plain word
        # (a plain word such as "authority" would flag legitimate prose in the reports) —
        # unless --strict, which checks every candidate value of 6+ characters
        if strict or c == "high" or (len(val) >= 8 and re.search(r"[\d@#$%^&*!+=]", val)):
            values.setdefault(val, f"{name}")
    leaks = []
    files = []
    import glob as globmod
    expanded = []
    for t in targets:
        if any(ch in t for ch in "*?["):  # a glob the shell did not expand (no match yet): not an error
            found = globmod.glob(t) or globmod.glob(str(repo / t))
            if not found:
                print(f"note: no file matches {t} yet", file=sys.stderr)
            expanded += found
        else:
            expanded.append(t)
    for t in expanded:
        p = Path(t) if Path(t).is_absolute() else Path.cwd() / t
        if not p.exists():
            alt = repo / t
            p = alt if alt.exists() else p
        if p.is_dir():
            for root, dirs, names in os.walk(p):
                files += [Path(root) / n for n in names]
        elif p.exists():
            files.append(p)
        else:
            print(f"target not found: {t}", file=sys.stderr)
            sys.exit(2)
    for f in files:
        if f.suffix.lower() in BINARY_EXT:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for val, name in values.items():
            if val in text:
                idx = text.index(val)
                leaks.append((str(f), text.count("\n", 0, idx) + 1, name))
    return leaks, len(files), len(values)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo")
    ap.add_argument("--plan", help="migration plan; tags hits in files that will move")
    ap.add_argument("--out", help="write the redacted list to this file")
    ap.add_argument("--json", help="write redacted hits (no values) as JSON")
    ap.add_argument("--exclude", action="append", default=[], help="glob of files to skip (repeatable)")
    ap.add_argument("--all", action="store_true", help="also list low-confidence hits (name looks secret, value looks like a word)")
    ap.add_argument("--verify", nargs="+", metavar="PATH", help="check these files/folders for leaked values")
    ap.add_argument("--strict", action="store_true", help="with --verify: check every candidate value, including plain-word low-confidence ones (more noise, catches DB_PASSWORD=\"sunshine\")")
    ap.add_argument("--no-ignored", action="store_true", help="do not scan git-ignored files (.env etc. are scanned by default)")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        print("repo not found", file=sys.stderr)
        sys.exit(2)
    hits = scan_repo(repo, args.exclude, include_ignored=not args.no_ignored)

    if args.verify:
        leaks, nfiles, nvals = verify(repo, hits, args.verify, strict=args.strict)
        if leaks:
            print(f"LEAK: {len(leaks)} secret value(s) found in {nfiles} checked file(s):")
            for f, ln, name in leaks:
                print(f"  {f}:{ln}  {name} = <redacted>")
            sys.exit(1)
        print(f"clean: {nfiles} file(s) checked against {nvals} secret value(s) found in the repo")
        return

    moved_src = []
    if args.plan:
        pp = Path(args.plan) if Path(args.plan).is_absolute() else repo / args.plan
        if not pp.exists():
            print(f"plan not found: {pp}", file=sys.stderr)
            sys.exit(2)
        moved_src = parse_plan_sources(pp.read_text(encoding="utf-8", errors="replace"))

    lines, rows = [], []
    low = sum(1 for h in hits if h[4] == "low")
    for rel, ln, name, val, c in sorted(hits, key=lambda h: (h[0], h[1])):
        if c == "low" and not args.all:
            continue
        tag = " [moved]" if moved_src and is_moved(rel, moved_src) else ""
        if rel in IGNORED:
            tag += " [git-ignored, not committed]"
        lines.append(f"{rel}:{ln} {name} = <redacted> [{c}]{tag}")
        rows.append({"file": rel, "line": ln, "name": name, "confidence": c, "moved": bool(tag)})
    report = "\n".join(lines) if lines else "no hard-coded secret found"
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(report + "\n", encoding="utf-8")
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(report)
    files = {r["file"] for r in rows}
    high = sum(1 for r in rows if r["confidence"] == "high")
    print(f"\n{len(rows)} hit(s) in {len(files)} file(s), {high} high-confidence. {low if not args.all else 0} low-confidence hit(s) hidden (--all). Values are never printed; "
          f"after writing reports run: secrets_scan.py {args.repo} --verify <reports> <evidence-folder>", file=sys.stderr)


if __name__ == "__main__":
    main()
