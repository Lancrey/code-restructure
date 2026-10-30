#!/usr/bin/env python3
"""Consistency check between an Xcode project file and the files on disk.

For every *.xcodeproj under <repo>:
  - resolves each PBXFileReference (through the PBXGroup tree) to a path on disk
    and reports references whose file is missing;
  - reports source files (.swift/.m/.mm/.c/.cpp/.h/.plist/.strings/.xcassets/.entitlements)
    on disk, under directories the project references, that no reference points to.

Works on any OS (no Xcode needed). Heuristic, text-based parsing of the old-style
plist; projects with synchronized folders (objectVersion >= 77) are reported as
such and skipped, since they follow the disk.

Usage: python3 pbxproj_check.py <repo>      exit 0 = consistent, 1 = problems
"""
import re
import sys
from pathlib import Path

WATCH_EXT = {".swift", ".m", ".mm", ".c", ".cpp", ".h", ".plist", ".strings", ".entitlements", ".xcprivacy", ".storyboard", ".xib"}


def parse_objects(text):
    """Return dict id -> dict(isa, path, name, sourceTree, children[]).

    Tolerates Xcode's own formatting (bare keys, /* comments */, file references on a
    single line, groups spread over several lines) and the quoted-key style written
    by generators (every key and value in quotes).
    """
    objs = {}
    # the indentation of object entries is whatever the file uses (two tabs in Xcode's own
    # output, 4 or 8 spaces from some generators): take it from the first object line
    first = re.search(r'^([ \t]+)"?[0-9A-F]{24}"?(?: /\*.*?\*/)? = \{', text, re.M)
    if not first:
        return objs
    indent = re.escape(first.group(1))
    start = re.compile(r'^' + indent + r'"?([0-9A-F]{24})"?(?: /\*.*?\*/)? = \{(.*)$')
    close = re.compile(r"^" + indent + r"\};\s*$")
    body = None
    oid = None
    for line in text.splitlines():
        if body is None:
            m = start.match(line)
            if not m:
                continue
            oid, rest = m.group(1), m.group(2)
            if rest.rstrip().endswith("};"):  # whole object on one line
                add_object(objs, oid, rest.rstrip()[:-2])
            else:
                body = [rest]
        elif close.match(line):
            add_object(objs, oid, "\n".join(body))
            body = None
        else:
            body.append(line)
    return objs


def add_object(objs, oid, body):
    isa = re.search(r'"?isa"? = "?(\w+)"?;', body)
    if not isa:
        return
    d = {"isa": isa.group(1)}
    for key in ("path", "name", "sourceTree"):
        km = re.search(r'"?%s"? = ("(?:[^"\\]|\\.)*"|[^;]+);' % key, body)
        if km:
            d[key] = km.group(1).strip().strip('"')
    ch = re.search(r'"?children"? = \((.*?)\);', body, re.S)
    d["children"] = re.findall(r"([0-9A-F]{24})", ch.group(1)) if ch else []
    objs[oid] = d


def resolve(objs, root_id, base):
    """Walk group tree, yielding (file_path) for each PBXFileReference."""
    out = []

    def walk(oid, cur):
        o = objs.get(oid)
        if not o:
            return
        st = o.get("sourceTree", "<group>")
        p = o.get("path")
        if st == "<group>":
            here = cur / p if p else cur
        elif st == "SOURCE_ROOT":
            here = base / p if p else base
        else:
            here = None
        if o["isa"] == "PBXFileReference":
            if here is not None and st in ("<group>", "SOURCE_ROOT"):
                out.append(here)
        elif o["isa"] in ("PBXGroup", "PBXVariantGroup"):
            for c in o["children"]:
                walk(c, here if here is not None else cur)

    walk(root_id, base)
    return out


def main():
    repo = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    problems = 0
    projects = sorted(repo.glob("**/*.xcodeproj"))
    if not projects:
        print("no .xcodeproj found")
        return
    for proj in projects:
        pbx = proj / "project.pbxproj"
        if not pbx.exists():
            continue
        base = proj.parent
        text = pbx.read_text(encoding="utf-8", errors="replace")
        if "PBXFileSystemSynchronizedRootGroup" in text:
            print(f"{proj.name}: uses synchronized folders — follows the disk, nothing to compare")
            continue
        objs = parse_objects(text)
        root = re.search(r'"?mainGroup"? = "?([0-9A-F]{24})"?;', text)
        if not root:
            print(f"{proj.name}: cannot find mainGroup")
            problems += 1
            continue
        files = resolve(objs, root.group(1), base)
        if not files:
            print(f"{proj.name}: parsed 0 file references — unrecognised project format, nothing was checked")
            problems += 1
            continue
        refs = {f.resolve() for f in files}
        missing = sorted(f for f in files if not f.exists() and f.suffix)
        for f in missing:
            print(f"{proj.name}: MISSING on disk: {f.relative_to(base)}")
            problems += 1
        # directories the project references -> look for unreferenced files there
        dirs = {f.parent.resolve() for f in refs if f.exists()}
        seen = set()
        for d in dirs:
            if not d.is_dir():
                continue
            for p in d.iterdir():
                if p.is_file() and p.suffix in WATCH_EXT and p.resolve() not in refs and p not in seen:
                    seen.add(p)
                    # localized strings live inside *.lproj groups handled as variant groups; skip
                    if p.parent.name.endswith(".lproj"):
                        continue
                    print(f"{proj.name}: ON DISK but not referenced: {p.relative_to(base)}")
                    problems += 1
        print(f"{proj.name}: {len(refs)} file references checked")
    if problems:
        print(f"{problems} problem(s)")
        sys.exit(1)
    print("project file and disk are consistent")


if __name__ == "__main__":
    main()
