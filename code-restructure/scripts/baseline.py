#!/usr/bin/env python3
"""Record a baseline of the checks a repository offers, so before/after can be compared.

Detects the checks available (tests, build, lint, type-check, entry-point --help)
from the repository's own configuration, runs them, and writes a JSON record
with exit codes, durations, output tails and a few parsed counters.

Usage:
  python3 baseline.py <repo> --out baseline-before.json          # detect + run
  python3 baseline.py <repo> --list                                # detect only
  python3 baseline.py <repo> --out after.json --compare before.json
  python3 baseline.py <repo> --only pytest,build --out x.json      # subset by id
  python3 baseline.py <repo> --extra "make check" --extra "./scripts/smoke.sh"
  python3 baseline.py <repo> --ref main --out baseline-before.json  # run on a ref in a temporary worktree

Rules it follows:
  - never modifies the repository; everything runs from the repo root
  - a check that fails BEFORE the migration is recorded as pre-existing, not as a regression
  - counters (test counts) are parsed from output where the runner's format is known
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


def has(repo: Path, *names):
    return any((repo / n).exists() for n in names)


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def find_test_files(repo: Path, max_depth=3):
    """True if a test_*.py or *_test.py file exists within max_depth, ignoring virtualenvs and vendor dirs."""
    skip = {".git", "node_modules", "vendor", ".venv", "venv", "env", "__pycache__", "build", "dist", ".tox"}
    base = len(repo.parts)
    for root, dirs, names in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in skip]
        if len(Path(root).parts) - base >= max_depth:
            dirs[:] = []
        for n in names:
            if n.endswith(".py") and (n.startswith("test_") or n.endswith("_test.py")):
                return True
    return False


def which(cmd):
    return shutil.which(cmd) is not None


def detect_checks(repo: Path):
    checks = []

    def add(cid, cmd, kind, note=""):
        checks.append({"id": cid, "cmd": cmd, "kind": kind, "note": note})

    # ---------- Python ----------
    if has(repo, "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "pytest.ini", "tox.ini"):
        py = "python3" if which("python3") else "python"
        pyproject_txt = (repo / "pyproject.toml").read_text(errors="replace") if has(repo, "pyproject.toml") else ""
        cfg_txt = "".join((repo / n).read_text(errors="replace") for n in ("tox.ini", "setup.cfg", "requirements-dev.txt", "requirements.txt") if (repo / n).exists())
        if has(repo, "pytest.ini", "conftest.py", "tests", "test") or "pytest" in pyproject_txt or "pytest" in cfg_txt \
                or find_test_files(repo):
            add("pytest-collect", f"{py} -m pytest --collect-only -q", "test-discovery",
                "number of collected tests must not drop after the move")
            add("pytest", f"{py} -m pytest -q -x --no-header -p no:cacheprovider", "test")
        add("py-compile", f"{py} -m compileall -q .", "build")
        if which("ruff") or has(repo, "ruff.toml", ".ruff.toml"):
            add("ruff", "ruff check .", "lint")
        elif which("flake8") and has(repo, ".flake8", "setup.cfg", "tox.ini"):
            add("flake8", "flake8", "lint")
        if which("mypy") and (has(repo, "mypy.ini") or "[tool.mypy]" in pyproject_txt):
            add("mypy", "mypy .", "typecheck")
        if has(repo, "manage.py"):
            add("django-check", f"{py} manage.py check", "entrypoint")
        for ep in ("main.py", "cli.py", "app.py"):
            if has(repo, ep):
                add(f"help:{ep}", f"{py} {ep} --help", "entrypoint", "exit code only; some apps ignore --help")

    # ---------- JavaScript / TypeScript ----------
    if has(repo, "package.json"):
        pj = read_json(repo / "package.json")
        scripts = pj.get("scripts") or {}
        runner = "pnpm" if has(repo, "pnpm-lock.yaml") and which("pnpm") else \
                 "yarn" if has(repo, "yarn.lock") and which("yarn") else \
                 "bun" if has(repo, "bun.lockb", "bun.lock") and which("bun") else "npm"
        run = f"{runner} run" if runner != "yarn" else "yarn"
        for name in ("typecheck", "type-check", "tsc", "lint", "build", "test", "test:unit", "check"):
            if name in scripts:
                kind = "typecheck" if "type" in name or name == "tsc" else "lint" if name == "lint" else "build" if name == "build" else "test" if name.startswith("test") else "lint"
                extra = " -- --run" if name.startswith("test") and "vitest" in scripts[name] and "--run" not in scripts[name] and "--watch=false" not in scripts[name] else ""
                env = "CI=1 " if name.startswith("test") else ""
                add(f"npm:{name}", f"{env}{run} {name}{extra}", kind)
        if has(repo, "tsconfig.json") and "typecheck" not in scripts and "type-check" not in scripts:
            add("tsc", "npx tsc --noEmit -p tsconfig.json", "typecheck", "catches every broken import in TS")

    # ---------- Go ----------
    if has(repo, "go.mod"):
        add("go-build", "go build ./...", "build")
        add("go-vet", "go vet ./...", "lint")
        add("go-list", "go list ./...", "test-discovery", "package count must not drop")
        add("go-test", "go test -count=1 ./...", "test")
        add("go-mod-tidy", "go mod tidy -diff", "lint", "Go ≥1.23; a diff means dependencies changed")

    # ---------- Rust ----------
    if has(repo, "Cargo.toml"):
        add("cargo-check", "cargo check --workspace --all-targets", "build")
        add("cargo-test", "cargo test --workspace", "test")
        if has(repo, "clippy.toml") or has(repo, ".cargo/config.toml"):
            add("cargo-clippy", "cargo clippy --workspace --all-targets", "lint")
        add("cargo-doc", "cargo doc --workspace --no-deps", "lint", "broken intra-doc links after moves")

    # ---------- PHP ----------
    if has(repo, "composer.json"):
        add("php-lint", "find . -name '*.php' -not -path './vendor/*' -not -path './node_modules/*' -print0 | xargs -0 -n1 -P4 php -l > /dev/null", "build",
            "syntax only")
        add("composer-validate", "composer validate --no-check-publish", "lint")
        if has(repo, "phpunit.xml", "phpunit.xml.dist"):
            add("phpunit", "vendor/bin/phpunit", "test")
        if has(repo, "phpstan.neon", "phpstan.neon.dist"):
            add("phpstan", "vendor/bin/phpstan analyse --no-progress", "typecheck")
        if has(repo, "artisan"):
            add("artisan-about", "php artisan about", "entrypoint")
            add("artisan-routes", "php artisan route:list", "entrypoint", "route count must not drop")
        if has(repo, "bin/console"):
            add("symfony-container", "php bin/console debug:container --env=test", "entrypoint")

    # ---------- Swift / Xcode ----------
    if has(repo, "Package.swift") or any(repo.glob("*.xcodeproj")) or any(repo.glob("*.xcworkspace")):
        if which("swift"):
            if has(repo, "Package.swift"):
                add("swift-build", "swift build", "build")
                add("swift-test", "swift test", "test")
        else:
            checks.append({"id": "swift-toolchain", "cmd": "false", "kind": "unavailable",
                           "note": "no swift toolchain here: build/tests NOT run; verify on macOS or via CI"})
        if has(repo, "Scripts/check.sh") and which("xcodebuild"):
            add("check.sh", "bash Scripts/check.sh", "test")
        for proj in sorted(repo.glob("*.xcodeproj")):
            if which("xcodebuild"):
                add(f"xcodebuild-list:{proj.name}", f"xcodebuild -list -project {proj.name}", "build")
        # pbxproj consistency is checkable anywhere
        add("pbxproj-files", f"{sys.executable} {Path(__file__).parent / 'pbxproj_check.py'} .", "build",
            "every file referenced by project.pbxproj exists; every app .swift on disk is referenced")

    # ---------- JVM ----------
    if has(repo, "gradlew"):
        add("gradle-build", "./gradlew build --offline -q", "build", "includes tests unless excluded")
    elif has(repo, "build.gradle", "build.gradle.kts") and which("gradle"):
        add("gradle-build", "gradle build -q", "build")
    if has(repo, "mvnw"):
        add("maven-verify", "./mvnw -q -B verify", "build")
    elif has(repo, "pom.xml") and which("mvn"):
        add("maven-verify", "mvn -q -B verify", "build")

    # ---------- Generic ----------
    if has(repo, "Makefile"):
        mk = (repo / "Makefile").read_text(errors="replace")
        for target in ("test", "check", "lint", "build"):
            if re.search(rf"^{target}\s*:", mk, re.M):
                add(f"make:{target}", f"make {target}", target if target != "check" else "test")
    for sh in ("test.sh", "run_tests.sh", "scripts/test.sh", "scripts/check.sh"):
        if has(repo, sh):
            add(f"sh:{sh}", f"bash {sh}", "test")

    # de-duplicate by id, keep first
    seen, out = set(), []
    for c in checks:
        if c["id"] not in seen:
            seen.add(c["id"])
            out.append(c)
    return out


COUNTERS = [
    ("pytest", r"(\d+) passed", "passed"),
    ("pytest", r"(\d+) failed", "failed"),
    ("pytest", r"(\d+) errors?\b", "errors"),
    ("pytest", r"(\d+) skipped", "skipped"),
    ("pytest-collect", r"(\d+) tests? collected", "collected"),
    ("pytest-collect", r"^(\d+) tests? collected", "collected"),
    ("npm:test", r"Tests:\s+.*?(\d+) passed", "passed"),
    ("npm:test", r"Tests:\s+(\d+) failed", "failed"),
    ("npm:test", r"Tests\s+(\d+) passed", "passed"),
    ("npm:test", r"Test Files\s+(\d+) passed", "files_passed"),
    ("go-list", None, "lines"),
    ("go-test", r"^ok\s", "ok_packages"),
    ("go-test", r"^FAIL\s", "failed_packages"),
    ("cargo-test", r"test result: ok\. (\d+) passed", "passed"),
    ("cargo-test", r"test result: FAILED\. (\d+) passed; (\d+) failed", "failed"),
    ("phpunit", r"OK \((\d+) tests?", "passed"),
    ("phpunit", r"Tests: (\d+), Assertions: \d+, Failures: (\d+)", "failed"),
    ("artisan-routes", None, "lines"),
]


def parse_counters(cid, output):
    counters = {}
    for prefix, pattern, name in COUNTERS:
        # custom --extra commands: apply the pytest patterns, they match any runner printing "N passed"
        if not (cid.startswith(prefix) or (cid.startswith("extra") and prefix == "pytest")):
            continue
        if pattern is None:
            counters[name] = len([l for l in output.splitlines() if l.strip()])
            continue
        if name in ("ok_packages", "failed_packages"):
            counters[name] = len(re.findall(pattern, output, re.M))
            continue
        total = 0
        found = False
        for m in re.finditer(pattern, output, re.M):
            found = True
            total += int(m.group(m.lastindex or 1))
        if found:
            counters[name] = total
    return counters


def run_check(repo: Path, check, timeout):
    t0 = time.time()
    try:
        proc = subprocess.run(
            check["cmd"], shell=True, cwd=repo, capture_output=True, text=True,
            timeout=timeout, env={**os.environ, "CI": os.environ.get("CI", "1"), "NO_COLOR": "1", "FORCE_COLOR": "0"},
        )
        rc, out, err = proc.returncode, proc.stdout, proc.stderr
        status = "pass" if rc == 0 else "fail"
    except subprocess.TimeoutExpired as e:
        rc, out, err = None, (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""), "TIMEOUT"
        status = "timeout"
    dur = round(time.time() - t0, 1)
    combined = (out or "") + "\n" + (err or "")
    return {
        **check,
        "status": status,
        "exit_code": rc,
        "duration_s": dur,
        "counters": parse_counters(check["id"], combined),
        "stdout_tail": (out or "")[-3000:],
        "stderr_tail": (err or "")[-3000:],
    }


def compare(before, after):
    b = {c["id"]: c for c in before.get("checks", [])}
    a = {c["id"]: c for c in after.get("checks", [])}
    rows, regressions = [], []
    for cid in sorted(set(b) | set(a)):
        cb, ca = b.get(cid), a.get(cid)
        sb = cb["status"] if cb else "—"
        sa = ca["status"] if ca else "—"
        kb = cb.get("counters", {}) if cb else {}
        ka = ca.get("counters", {}) if ca else {}
        verdict = "same"
        if cb is None:
            verdict = "NEW-not-comparable"
        elif ca is None:
            verdict = "REGRESSION"  # a check that existed before is no longer run
        elif sb == "pass" and sa != "pass":
            verdict = "REGRESSION"
        elif sb != "pass" and sa == "pass":
            verdict = "fixed"
        elif sb != sa:
            verdict = "changed"
        for k in set(kb) | set(ka):
            if k in ("collected", "passed", "lines", "ok_packages", "files_passed") and kb.get(k, 0) > ka.get(k, 0):
                verdict = "REGRESSION"
            if k in ("failed", "errors", "failed_packages") and ka.get(k, 0) > kb.get(k, 0):
                verdict = "REGRESSION"
            if k == "skipped" and k in kb and k in ka and ka[k] > kb[k]:
                verdict = "REGRESSION"
        if verdict == "REGRESSION":
            regressions.append(cid)
        rows.append((cid, sb, json.dumps(kb), sa, json.dumps(ka), verdict))
    return rows, regressions


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo")
    ap.add_argument("--out", help="write JSON record here")
    ap.add_argument("--list", action="store_true", help="only list the detected checks")
    ap.add_argument("--only", help="comma-separated check ids to run")
    ap.add_argument("--skip", help="comma-separated check ids to skip")
    ap.add_argument("--extra", action="append", default=[], help="extra shell command to run as a check (repeatable)")
    ap.add_argument("--timeout", type=int, default=900, help="seconds per check")
    ap.add_argument("--compare", help="earlier baseline JSON to diff against")
    ap.add_argument("--ref", help="run the checks on this git ref (branch, tag, sha) in a temporary worktree instead of the working tree; "
                                  "use it to record the 'before' baseline after you have already created the migration branch")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        sys.exit(f"not a directory: {repo}")
    worktree = None
    if args.ref:
        import tempfile
        worktree = Path(tempfile.mkdtemp(prefix="baseline-ref-")) / "wt"
        r = subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", str(worktree), args.ref], capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"git worktree add failed: {r.stderr.strip()}")
        repo = worktree
    try:
        run(args, repo)
    finally:
        if worktree is not None:
            subprocess.run(["git", "-C", str(worktree), "worktree", "remove", "--force", str(worktree)], capture_output=True)
            shutil.rmtree(worktree.parent, ignore_errors=True)


def run(args, repo):
    checks = detect_checks(repo)
    for i, cmd in enumerate(args.extra, 1):
        checks.append({"id": f"extra-{i}", "cmd": cmd, "kind": "custom", "note": ""})
    if args.only:
        wanted = set(args.only.split(","))
        checks = [c for c in checks if c["id"] in wanted]
    if args.skip:
        skipped = set(args.skip.split(","))
        checks = [c for c in checks if c["id"] not in skipped]

    if args.list or not args.out:
        print(f"Detected checks for {repo}:")
        for c in checks:
            print(f"  [{c['id']}] ({c['kind']}) {c['cmd']}" + (f"   # {c['note']}" if c["note"] else ""))
        if not checks:
            print("  none — this repo has no detectable automated checks; the floor is build + entry points + import resolution")
        if args.list or not args.out:
            return

    git_head = ""
    try:
        git_head = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    except Exception:
        pass

    results = []
    for c in checks:
        print(f"→ [{c['id']}] {c['cmd']}", flush=True)
        r = run_check(repo, c, args.timeout)
        results.append(r)
        print(f"   {r['status']} (exit {r['exit_code']}, {r['duration_s']}s) {r['counters'] or ''}")

    record = {"repo": str(repo), "git_head": git_head, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "checks": results}
    Path(args.out).write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"Baseline written to {Path(args.out).resolve()}")

    if args.compare:
        before = read_json(Path(args.compare))
        rows, regressions = compare(before, record)
        print("\nComparison (before → after):")
        print(f"  {'check':<20} {'before':<8} {'counters':<28} {'after':<8} {'counters':<28} verdict")
        for cid, sb, kb, sa, ka, v in rows:
            print(f"  {cid:<20} {sb:<8} {kb:<28} {sa:<8} {ka:<28} {v}")
        if regressions:
            print(f"\nREGRESSIONS in: {', '.join(regressions)}")
            sys.exit(2)
        print("\nNo regressions detected by automated checks (this is evidence, not proof).")


if __name__ == "__main__":
    main()
