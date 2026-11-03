#!/usr/bin/env python3
"""Software factory runner: deterministic brakes around coding agents.

The model never decides whether its own work passes. This script owns the
gates, the caps and the ledger; agents are child processes with a fresh context.
Standard library only (Python 3.9+). It is vendored into a repo as
.factory/bin/factory.py so CI can run the same code path as a local rehearsal.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

VERSION = "0.1.0"
CLAUDE_VERSION = "2.1.317"  # Pinned in workflows: flag behaviour changes between releases.
SKILL_ROOT = Path(__file__).resolve().parent.parent
FACTORY = ".factory"

DEFAULT_CONFIG = {
    "schema": 1,
    "language": "en",
    "base_branch": None,
    "setup": None,
    "check": {"fast": None, "full": None, "preflight": "full", "timeout_seconds": 1800},
    "implementer_commits": False,
    "tracker": {
        "type": "local",
        "labels": {
            "triage": "needs-triage",
            "info": "needs-info",
            "ready": "ready-for-agent",
            "human": "ready-for-human",
            "wontfix": "wontfix",
            "running": "factory:running",
            "failed": "factory:needs-human",
        },
    },
    "roles": {
        "implementer": {"agent": "claude", "model": None, "max_budget_usd": 3.0, "max_turns": 80, "timeout_seconds": 2400},
        "reviewer": {"agent": "codex", "model": None, "max_budget_usd": 1.0, "max_turns": 30, "timeout_seconds": 900},
        "advisor": {"agent": "claude", "model": None, "max_budget_usd": 1.0, "max_turns": 40, "timeout_seconds": 900},
    },
    "local_sandbox": {"enabled": True, "allowed_domains": [], "deny_read": ["~/.ssh", "~/.aws", "~/.gnupg", "~/.config/gh", "~/.config/gcloud", "~/.kube", "~/.docker", "~/.netrc", "~/.npmrc", "~/.pypirc"]},
    "caps": {
        "rework_attempts": 2,
        "max_changed_lines": 600,
        "max_diff_bytes": 200000,
        "max_run_usd": 8.0,
        "runs_per_day": 5,
    },
    "gates": {"accepted_rehearsals": 3},
    "protected_paths": [".factory/", ".github/workflows/", ".claude/", ".codex/", ".agents/", "AGENTS.md", "CLAUDE.md", "CODING_STANDARDS.md"],
    "gate_config_regex": r"(^|/)(package\.json|pyproject\.toml|setup\.cfg|tox\.ini|pytest\.ini|ruff\.toml|mypy\.ini|Makefile|Cargo\.toml|\.swiftlint\.yml|phpunit\.xml|phpstan\.neon|\.?eslint[^/]*|\.eslintrc[^/]*|tsconfig[^/]*\.json|(vitest|jest|playwright|stryker)\.config\.[a-z]+|\.pre-commit-config\.yaml|\.husky/.*|\.githooks/.*|\.gitlab-ci\.yml)$",
    "generated_paths": ["package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock", "poetry.lock", "uv.lock", "Cargo.lock", "go.sum", "composer.lock", "Gemfile.lock"],
    "test_path_regex": r"(^|/)(tests?|__tests__|spec|specs)/|\.(test|spec)\.[a-z]+$|_test\.(go|py)$|(^|/)test_[^/]+\.py$|Tests?\.(swift|java|kt|cs)$|_spec\.rb$",
    "suppression_regex": r"\.(only|skip)\(|\b(xit|xdescribe|xtest|fit|fdescribe)\(|eslint-disable|@ts-ignore|@ts-nocheck|@ts-expect-error|#\s*noqa|#\s*type:\s*ignore|pytest\.mark\.(skip|xfail)|t\.Skip\(|#\[ignore\]|@Disabled|@Ignore\b|swiftlint:disable|//\s*nolint|phpcs:ignore|@phpstan-ignore",
}

CODE_EXT = {".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".py", ".go", ".rs", ".php", ".rb", ".swift", ".java", ".kt", ".cs", ".c", ".cc", ".cpp", ".h", ".hpp", ".vue", ".svelte"}
SABOTAGE = {".py": "\ndef (:\n", ".rb": "\ndef (\n", ".go": "\nfunc ( {\n", ".rs": "\nfn ( {\n", ".php": "\n<?php function ( {\n", ".swift": "\nfunc ( {\n"}
SABOTAGE_DEFAULT = "\n}}} factory_brake_test ((( \n"
MAX_SABOTAGE_SIZE = 400_000

TRIGGERS = {
    "review-pr": {"file": "factory-review-pr.yml", "needs": ["checks", "review"], "writes": "PR comment only"},
    "triage": {"file": "factory-triage.yml", "needs": ["knowledge", "checks"], "writes": "issue comment and label"},
    "implement": {"file": "factory-implement.yml", "needs": ["knowledge", "checks", "review", "rehearsal"], "writes": "branch and draft PR"},
    "gardener": {"file": "factory-gardener.yml", "needs": ["knowledge", "checks"], "writes": "one issue"},
    "retro": {"file": "factory-retro.yml", "needs": ["knowledge", "checks", "review", "rehearsal"], "writes": "one issue"},
}


class FactoryError(Exception):
    pass


# ---------------------------------------------------------------- helpers


def sh(cmd, cwd=None, env=None, timeout=None, input_text=None):
    """Run a command, never raising on non-zero. A timeout returns exit code 124."""
    merged = dict(os.environ)
    if env:
        merged.update(env)
    shell = isinstance(cmd, str)
    if shell:
        cmd = ["bash", "-c", cmd]
    try:
        p = subprocess.run(cmd, cwd=cwd, env=merged, timeout=timeout, input=input_text, capture_output=True, text=True, errors="replace")
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        return 124, _text(e.stdout), _text(e.stderr) + f"\n[factory] timed out after {timeout}s"
    except FileNotFoundError as e:
        return 127, "", str(e)


def _text(v):
    if v is None:
        return ""
    return v.decode("utf-8", "replace") if isinstance(v, bytes) else v


def git(*args, cwd=None, check=True):
    code, out, err = sh(["git", *args], cwd=cwd)
    if check and code != 0:
        raise FactoryError(f"git {' '.join(args)} failed: {err.strip() or out.strip()}")
    return out.strip() if check else (code, out.strip())


def repo_root(path):
    code, out, _ = sh(["git", "rev-parse", "--show-toplevel"], cwd=path)
    if code != 0:
        raise FactoryError(f"{path} is not a Git repository")
    return Path(out.strip())


def deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def trusted_dir(repo):
    """CI jobs set FACTORY_TRUSTED_DIR to a copy of .factory taken from the base commit,
    so the change under review cannot redefine the rules it is judged by."""
    env = os.environ.get("FACTORY_TRUSTED_DIR")
    return Path(env) if env else Path(repo) / FACTORY


def load_config(repo):
    path = trusted_dir(repo) / "config.json"
    if not path.exists():
        return deep_merge(DEFAULT_CONFIG, {})
    try:
        return deep_merge(DEFAULT_CONFIG, json.loads(path.read_text()))
    except json.JSONDecodeError as e:
        raise FactoryError(f"{path} is not valid JSON: {e}")


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return default


def base_branch(repo, cfg):
    if cfg.get("base_branch"):
        return cfg["base_branch"]
    code, out = git("symbolic-ref", "--short", "refs/remotes/origin/HEAD", cwd=repo, check=False)
    if code == 0 and "/" in out:
        return out.split("/", 1)[1]
    for name in ("main", "master", "trunk"):
        if git("rev-parse", "--verify", "-q", name, cwd=repo, check=False)[0] == 0:
            return name
    code, out = git("symbolic-ref", "--short", "HEAD", cwd=repo, check=False)  # also true before the first commit
    return out if code == 0 else git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo)


def tracked_files(repo):
    return [f for f in git("ls-files", cwd=repo).splitlines() if f]


def fingerprint(*parts):
    return hashlib.sha256("\n".join(p or "" for p in parts).encode()).hexdigest()[:16]


def ledger_path(repo):
    return Path(repo) / FACTORY / "ledger.jsonl"


def ledger_read(repo):
    path = ledger_path(repo)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def ledger_append(repo, row):
    path = ledger_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def state_path(repo):
    return Path(repo) / FACTORY / "state.json"


def state_update(repo, **kw):
    path = state_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = read_json(path, {}) or {}
    state.update(kw)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def render(template, values):
    """Single-pass substitution, so a placeholder inside ticket text stays inert."""
    return re.sub(r"\{\{([A-Z_]+)\}\}", lambda m: str(values.get(m.group(1), m.group(0))), template)


def prompt_path(repo, name):
    local = trusted_dir(repo) / "prompts" / f"{name}.md"
    return local if local.exists() else SKILL_ROOT / "assets" / "prompts" / f"{name}.md"


def schema_path(repo):
    local = trusted_dir(repo) / "review-schema.json"
    return local if local.exists() else SKILL_ROOT / "assets" / "review-schema.json"


# ---------------------------------------------------------------- stack detection


def detect_stack(repo):
    repo = Path(repo)
    stacks, fast, full, setup, notes = [], [], [], None, []

    pkg = read_json(repo / "package.json")
    if isinstance(pkg, dict):
        stacks.append("node")
        scripts = pkg.get("scripts") or {}
        pm = "npm"
        for lock, name in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("bun.lock", "bun"), ("bun.lockb", "bun")):
            if (repo / lock).exists():
                pm = name
        setup = {"npm": "npm ci" if (repo / "package-lock.json").exists() else "npm install", "pnpm": "pnpm install --frozen-lockfile", "yarn": "yarn install --frozen-lockfile", "bun": "bun install --frozen-lockfile"}[pm]
        for key in scripts:
            if key in ("lint", "typecheck", "type-check", "tsc") or key.startswith("check"):
                fast.append(f"{pm} run {key}")
        full.extend(fast)
        if "test" in scripts and "no test specified" not in scripts["test"]:
            full.append(f"{pm} test")
        elif "test" in scripts:
            notes.append("package.json has only the placeholder test script")

    if (repo / "pyproject.toml").exists() or (repo / "requirements.txt").exists() or (repo / "setup.py").exists():
        stacks.append("python")
        pp = (repo / "pyproject.toml").read_text(errors="replace") if (repo / "pyproject.toml").exists() else ""
        if "ruff" in pp or (repo / "ruff.toml").exists():
            fast.append("ruff check .")
        if "mypy" in pp or (repo / "mypy.ini").exists():
            fast.append("mypy .")
        full.extend(c for c in fast if c.startswith(("ruff", "mypy")) and c not in full)
        full.append("pytest -q")
    if (repo / "go.mod").exists():
        stacks.append("go")
        fast.append("go vet ./...")
        full += ["go vet ./...", "go test ./..."]
    if (repo / "Cargo.toml").exists():
        stacks.append("rust")
        fast.append("cargo clippy --all-targets -- -D warnings")
        full += ["cargo clippy --all-targets -- -D warnings", "cargo test"]
    if (repo / "composer.json").exists():
        stacks.append("php")
        comp = read_json(repo / "composer.json", {}) or {}
        for key in (comp.get("scripts") or {}):
            if key in ("lint", "phpstan", "analyse", "test"):
                (full if key == "test" else fast).append(f"composer {key}")
        full = [c for c in fast if c not in full] + full
    if (repo / "Package.swift").exists():
        stacks.append("swift")
        full.append("swift test")
    elif any(p.suffix == ".xcodeproj" for p in repo.iterdir() if p.is_dir()):
        stacks.append("swift-xcode")
        notes.append("Xcode project: the check needs xcodebuild on a macOS runner; choose the scheme with the user")
    if (repo / "Gemfile").exists():
        stacks.append("ruby")
        full.append("bundle exec rspec" if (repo / "spec").exists() else "bundle exec rake test")
    if (repo / "Makefile").exists():
        mk = (repo / "Makefile").read_text(errors="replace")
        for tgt in ("check", "lint", "test"):
            if re.search(rf"^{tgt}:", mk, re.M):
                notes.append(f"Makefile has a '{tgt}' target; prefer it if it is what CI runs")
    join = lambda xs: " && ".join(dict.fromkeys(xs)) or None
    return {"stacks": stacks, "setup": setup, "check_fast": join(fast), "check_full": join(full), "notes": notes}


# ---------------------------------------------------------------- audit


def _find_doc(files, pattern):
    rx = re.compile(pattern, re.I)
    return [f for f in files if rx.search(f.rsplit("/", 1)[-1]) and f.lower().endswith((".md", ".mdx", ".txt", ".rst"))]


def _ci_files(repo):
    wf = Path(repo) / ".github" / "workflows"
    if wf.is_dir():
        return sorted(p for p in wf.iterdir() if p.suffix in (".yml", ".yaml"))
    return []


def audit(repo):
    repo = Path(repo)
    cfg = load_config(repo)
    # Includes files not committed yet: a standards file just written must count.
    files = [f for f in git("ls-files", "--cached", "--others", "--exclude-standard", cwd=repo).splitlines() if f]
    state = read_json(state_path(repo), {}) or {}
    stack = detect_stack(repo)
    test_rx = re.compile(cfg["test_path_regex"])
    code_files = [f for f in files if Path(f).suffix.lower() in CODE_EXT]
    test_files = [f for f in code_files if test_rx.search(f)]
    remote = git("remote", "get-url", "origin", cwd=repo, check=False)
    remote_url = remote[1] if remote[0] == 0 else None
    host = "github" if remote_url and "github.com" in remote_url else "gitlab" if remote_url and "gitlab" in remote_url else "other" if remote_url else "none"

    agents = repo / "AGENTS.md"
    claude = repo / "CLAUDE.md"
    steering = {p.name: len(p.read_text(errors="replace").splitlines()) for p in (agents, claude) if p.exists()}
    glossary = _find_doc(files, r"glossar|^context\.md$|terminolog|lexi[cq]ue|ubiquitous|vocabul")
    adrs = [f for f in files if re.search(r"(^|/)(adr|adrs|decisions)/", f)]
    arch = _find_doc(files, r"architecture|^map\.md$|codemap")
    standards = _find_doc(files, r"^coding[_-]standards|^contributing")
    ci_text = ""
    workflows = _ci_files(repo)
    for p in workflows:
        if not p.name.startswith("factory-"):
            ci_text += p.read_text(errors="replace")
    other_ci = [f for f in (".gitlab-ci.yml", ".circleci/config.yml", "azure-pipelines.yml") if (repo / f).exists()]
    for f in other_ci:
        ci_text += (repo / f).read_text(errors="replace")
    ci_runs_tests = bool(re.search(r"\b(test|pytest|vitest|jest|rspec|phpunit|cargo test|go test|swift test|xcodebuild)\b", ci_text))
    hooks_dirs = [d for d in (".husky", ".githooks", ".pre-commit-config.yaml", "lefthook.yml") if (repo / d).exists()]
    configured = (repo / FACTORY / "config.json").exists()
    fp = fingerprint(cfg["check"].get("full"), cfg["check"].get("fast"))
    brake = state.get("brake_test") or {}
    brake_ok = brake.get("passed") is True and brake.get("fingerprint") == fp
    review_prompt = (repo / FACTORY / "prompts" / "review.md").exists()
    rows = ledger_read(repo)
    accepted = sum(1 for r in rows if r.get("human_verdict") == "accepted")
    installed = [p.name for p in workflows if p.name.startswith("factory-")]

    def stage(name, required, warnings=None):
        missing = [m for m, ok in required if not ok]
        return {"name": name, "passed": not missing, "missing": missing, "warnings": [w for w, bad in (warnings or []) if bad]}

    longest = max(steering.values()) if steering else 0
    stages = [
        stage("knowledge", [("a steering file (AGENTS.md or CLAUDE.md)", bool(steering)), ("a glossary or terminology document", bool(glossary))],
              [(f"steering file is {longest} lines; it loads in every session", longest > 200),
               (f"glossary matched by file name only ({glossary[0] if glossary else ''}): read it, it may not be a real glossary", bool(glossary) and not any(re.search(r"(^|/)(GLOSSARY|CONTEXT)\.md$", g) for g in glossary)),
               ("no ADR folder", not adrs), ("no architecture map document", not arch),
               ("CLAUDE.md without AGENTS.md: Codex will not read it until bridged", claude.exists() and not agents.exists()),
               ("AGENTS.md and CLAUDE.md both exist: Claude reads only CLAUDE.md unless it imports @AGENTS.md", agents.exists() and claude.exists() and "@AGENTS.md" not in claude.read_text(errors="replace"))]),
        stage("checks", [(".factory/config.json", configured), ("check.full configured", bool(cfg["check"].get("full"))), ("tests exist", bool(test_files)),
                         ("CI runs the tests", ci_runs_tests), ("brake test passed for the current check commands", brake_ok)],
              [("check.fast not configured", configured and not cfg["check"].get("fast")),
               ("no local git hook manager", not hooks_dirs),
               ("no logic sabotage recorded: only a syntax error was proven to fail", brake_ok and not brake.get("logic"))]),
        stage("review", [(".factory/prompts/review.md", review_prompt), ("coding standards document (CODING_STANDARDS.md or CONTRIBUTING.md)", bool(standards))],
              [(f"reviewer agent '{cfg['roles']['reviewer']['agent']}' CLI not found on this machine", shutil.which(cfg["roles"]["reviewer"]["agent"]) is None),
               ("no pull request template", not (repo / ".github" / "pull_request_template.md").exists())]),
        stage("rehearsal", [(f"{cfg['gates']['accepted_rehearsals']} rehearsals accepted by a human (have {accepted})", accepted >= cfg["gates"]["accepted_rehearsals"])]),
    ]
    first_fail = next((s["name"] for s in stages if not s["passed"]), None)
    return {
        "repo": str(repo),
        "host": host,
        "base_branch": base_branch(repo, cfg),
        "stack": stack,
        "counts": {"tracked": len(files), "code": len(code_files), "tests": len(test_files)},
        "knowledge": {"steering": steering, "glossary": glossary[:5], "adr_files": len(adrs), "architecture": arch[:5], "standards": standards[:5]},
        "ci": {"workflows": [p.name for p in workflows], "other": other_ci, "runs_tests": ci_runs_tests, "git_hooks": hooks_dirs},
        "factory": {"configured": configured, "check": cfg["check"], "brake_test": brake or None, "rehearsals_accepted": accepted, "runs": len(rows), "triggers_installed": installed},
        "stages": stages,
        "next_stage": first_fail or "triggers",
    }


def print_audit(a):
    out = [f"Repo: {a['repo']}  host={a['host']}  base={a['base_branch']}",
           f"Stack: {', '.join(a['stack']['stacks']) or 'unknown'}  code={a['counts']['code']} tests={a['counts']['tests']}",
           ]
    if a["factory"]["configured"]:
        check = a["factory"]["check"]
        out += [f"Configured check.fast: {check.get('fast')}", f"Configured check.full: {check.get('full')}"]
        holes = (a["factory"]["brake_test"] or {}).get("logic_holes")
        if holes:
            out.append(f"Known test holes (check stayed green): {', '.join(holes)}")
    else:
        out += [f"Proposed setup: {a['stack']['setup']}", f"Proposed check.fast: {a['stack']['check_fast']}", f"Proposed check.full: {a['stack']['check_full']}"]
    out += [f"Note: {n}" for n in a["stack"]["notes"]]
    for s in a["stages"]:
        out.append(f"[{'PASS' if s['passed'] else 'FAIL'}] {s['name']}")
        out += [f"    missing: {m}" for m in s["missing"]]
        out += [f"    warning: {w}" for w in s["warnings"]]
    out.append(f"Triggers installed: {', '.join(a['factory']['triggers_installed']) or 'none'}")
    out.append(f"Next stage: {a['next_stage']}")
    return "\n".join(out)


# ---------------------------------------------------------------- tickets


class Ticket:
    def __init__(self, id, title, body, type="afk", status="ready-for-agent", blocked_by=None, priority=2, path=None, assigned=False, open=True):
        self.id, self.title, self.body, self.type, self.status = str(id), title, body, type, status
        self.blocked_by = [str(b) for b in (blocked_by or [])]
        self.priority, self.path, self.assigned, self.open = priority, path, assigned, open

    def as_dict(self):
        return {"id": self.id, "title": self.title, "type": self.type, "status": self.status, "blocked_by": self.blocked_by, "priority": self.priority}

    @property
    def slug(self):
        s = re.sub(r"[^a-z0-9]+", "-", self.title.lower()).strip("-")[:40].strip("-")
        return s or "ticket"


def parse_front_matter(text):
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, val = line.split(":", 1)
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            meta[key.strip()] = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
        else:
            meta[key.strip()] = val.strip("'\"")
    return meta, m.group(2)


def parse_blockers(body):
    m = re.search(r"^#+\s*Blocked by\s*$(.*?)(?=^#+\s|\Z)", body or "", re.M | re.S | re.I)
    if not m:
        return []
    section = m.group(1)
    # Only the first number on each line is an edge: "#12, fixed by #40" must not invent one.
    out = []
    for line in section.splitlines():
        if re.match(r"\s*(?:[-*]\s*)?(?:none|n/?a)\b", line, re.I):
            continue
        out += re.findall(r"(?<![\w/-])#(\d+)\b", line) or []
    return list(dict.fromkeys(out))


class LocalTracker:
    def __init__(self, repo, cfg):
        self.repo, self.cfg = Path(repo), cfg
        self.dir = self.repo / FACTORY / "tickets"

    def all(self):
        tickets = []
        if not self.dir.is_dir():
            return tickets
        for p in sorted(self.dir.glob("*.md")):
            meta, body = parse_front_matter(p.read_text())
            tid = meta.get("id") or p.stem.split("-")[0]
            try:
                prio = int(meta.get("priority", 2))
            except ValueError:
                prio = 2
            status = meta.get("status", "needs-triage")
            tickets.append(Ticket(tid, meta.get("title", p.stem), body, meta.get("type", "afk"), status, meta.get("blocked_by", []), prio, p, False, status not in ("done", "wontfix")))
        return tickets

    def blockers_open(self, ticket, index):
        return [b for b in ticket.blocked_by if b in index and index[b].open]

    def set_status(self, ticket, status, note=None):
        text = ticket.path.read_text()
        if re.search(r"^status:.*$", text, re.M):
            text = re.sub(r"^status:.*$", f"status: {status}", text, count=1, flags=re.M)
        if note:
            text = text.rstrip() + f"\n\n> [factory] {note}\n"
        ticket.path.write_text(text)
        ticket.status = status

    def claim(self, ticket):
        self.set_status(ticket, "factory:running")

    def finish(self, ticket, outcome, note):
        self.set_status(ticket, {"ready-for-human": "ready-for-human"}.get(outcome, "factory:needs-human"), note)


class GitHubTracker:
    def __init__(self, repo, cfg):
        self.repo, self.cfg, self.labels = Path(repo), cfg, cfg["tracker"]["labels"]

    def _gh(self, *args, check=True):
        code, out, err = sh(["gh", *args], cwd=self.repo)
        if check and code != 0:
            raise FactoryError(f"gh {' '.join(args)} failed: {err.strip() or out.strip()}")
        return out if code == 0 else None

    def _ticket(self, row):
        labels = [l["name"] for l in row.get("labels", [])]
        prio = next((int(m.group(1)) for l in labels for m in [re.match(r"factory:p(\d)$", l)] if m), 2)
        ttype = "hitl" if "factory:hitl" in labels else "afk"
        status = self.labels["ready"] if self.labels["ready"] in labels else self.labels["running"] if self.labels["running"] in labels else labels[0] if labels else "needs-triage"
        return Ticket(row["number"], row["title"], row.get("body") or "", ttype, status, parse_blockers(row.get("body")), prio, None, bool(row.get("assignees")), str(row.get("state", "OPEN")).upper() == "OPEN")

    def all(self):
        out = self._gh("issue", "list", "--state", "open", "--label", self.labels["ready"], "--limit", "200", "--json", "number,title,body,labels,assignees,state")
        return [self._ticket(r) for r in json.loads(out or "[]")]

    def get(self, number):
        out = self._gh("issue", "view", str(number), "--json", "number,title,body,labels,assignees,state")
        return self._ticket(json.loads(out))

    def native_blockers(self, number):
        """Open blockers from GitHub's dependency API; an empty list if unavailable."""
        out = self._gh("api", f"repos/{{owner}}/{{repo}}/issues/{number}/dependencies/blocked_by", check=False)
        if not out:
            return []
        try:
            return [str(i["number"]) for i in json.loads(out) if i.get("state") == "open"]
        except (ValueError, KeyError, TypeError):
            return []

    def blockers_open(self, ticket, index):
        still = list(self.native_blockers(ticket.id))
        for b in ticket.blocked_by:
            if b in still:
                continue
            state = self._gh("issue", "view", b, "--json", "state", check=False)
            # A reference that cannot be resolved counts as blocking: fail closed.
            if state is None or json.loads(state).get("state", "OPEN").upper() == "OPEN":
                still.append(b)
        return still

    def claim(self, ticket):
        self._gh("issue", "edit", ticket.id, "--add-label", self.labels["running"], "--remove-label", self.labels["ready"])

    def finish(self, ticket, outcome, note):
        label = self.labels["human"] if outcome == "ready-for-human" else self.labels["failed"]
        self._gh("issue", "edit", ticket.id, "--remove-label", self.labels["running"], "--remove-label", self.labels["ready"], "--add-label", label)
        self._gh("issue", "comment", ticket.id, "--body", f"> *This was generated by the software factory runner.*\n\n{note}")


def tracker_for(repo, cfg):
    kind = cfg["tracker"]["type"]
    if kind == "local":
        return LocalTracker(repo, cfg)
    if kind == "github":
        return GitHubTracker(repo, cfg)
    raise FactoryError(f"unsupported tracker type: {kind}")


def find_cycle(tickets):
    graph = {t.id: [b for b in t.blocked_by if any(x.id == b for x in tickets)] for t in tickets}
    color = {}

    def visit(node, trail):
        color[node] = 1
        for nxt in graph.get(node, []):
            if color.get(nxt) == 1:
                return trail + [node, nxt]
            if color.get(nxt) is None:
                found = visit(nxt, trail + [node])
                if found:
                    return found
        color[node] = 2
        return None

    for t in tickets:
        if color.get(t.id) is None:
            found = visit(t.id, [])
            if found:
                return found
    return None


def frontier(repo, cfg):
    """Tickets an agent may take now, plus why the others may not."""
    tracker = tracker_for(repo, cfg)
    tickets = tracker.all()
    index = {t.id: t for t in tickets}
    cycle = find_cycle(tickets)
    if cycle:
        raise FactoryError("blocking cycle: " + " -> ".join(cycle))
    if isinstance(tracker, LocalTracker):
        dangling = [(t.id, b) for t in tickets for b in t.blocked_by if b not in index]
        if dangling:
            raise FactoryError("blocked_by points at unknown tickets: " + ", ".join(f"{a}->{b}" for a, b in dangling))
    ready, waiting = [], []
    label = cfg["tracker"]["labels"]["ready"]
    for t in tickets:
        if not t.open or t.status in ("done", "wontfix"):
            continue
        blockers = tracker.blockers_open(t, index)
        why = None
        if t.type != "afk":
            why = "hitl: needs a human"
        elif t.status != label:
            why = f"status {t.status}"
        elif t.assigned:
            why = "assigned"
        elif blockers:
            why = "blocked by " + ", ".join(blockers)
        if why:
            waiting.append({**t.as_dict(), "why": why})
        else:
            ready.append(t)
    ready.sort(key=lambda t: (t.priority, int(t.id) if t.id.isdigit() else 10**9, t.id))
    return ready, waiting


# ---------------------------------------------------------------- agents


DENIED_DURING_RUN = ["Bash(git push *)", "Bash(git commit *)", "Bash(git reset *)", "Bash(git rebase *)", "Bash(gh *)"]
READ_ONLY_TOOLS = "Read,Grep,Glob"
EDIT_TOOLS = "Read,Edit,Write,Grep,Glob,Task,Bash"


def sandbox_settings(cfg):
    """Claude's Bash sandbox for a rehearsal on the user's own machine. It fails rather than run unsandboxed."""
    box = cfg.get("local_sandbox") or {}
    if not box.get("enabled"):
        return None
    return json.dumps({"sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True, "allowUnsandboxedCommands": False, "failIfUnavailable": True,
                                   "network": {"allowedDomains": box.get("allowed_domains", [])},
                                   "filesystem": {"denyRead": box.get("deny_read", [])}}})


def agent_command(role_cfg, *, read_only, schema=None, last_message=None, extra_read_tools=None, settings=None, allow_commits=False):
    """Build the argv for one fresh agent process. The prompt always goes on stdin.

    Permissions are an explicit allow list under dontAsk, so anything else is denied without
    a prompt. The same command runs locally and in CI: no permission-bypass flag anywhere.
    Claude does not get --bare: it would skip hooks and CLAUDE.md, and refuses OAuth tokens.
    """
    agent = role_cfg["agent"]
    model = role_cfg.get("model")
    if agent == "claude":
        cmd = ["claude", "-p", "--output-format", "json", "--no-session-persistence", "--permission-mode", "dontAsk",
               "--max-turns", str(role_cfg.get("max_turns", 40)), "--max-budget-usd", str(role_cfg.get("max_budget_usd", 2))]
        if model:
            cmd += ["--model", model]
        if read_only:
            tools = READ_ONLY_TOOLS + ("," + extra_read_tools if extra_read_tools else "")
            cmd += ["--tools", ",".join(t.split("(")[0] for t in tools.split(",")), "--allowedTools", tools]
        else:
            denied = [d for d in DENIED_DURING_RUN if not (allow_commits and d == "Bash(git commit *)")]
            cmd += ["--allowedTools", EDIT_TOOLS, "--disallowedTools", ",".join(denied)]
        if settings:
            cmd += ["--settings", settings]
        if schema:
            cmd += ["--json-schema", Path(schema).read_text()]
        return cmd
    if agent == "codex":
        cmd = ["codex", "exec", "--ephemeral", "--sandbox", "read-only" if read_only else "workspace-write"]
        if model:
            cmd += ["--model", model]
        if schema:
            cmd += ["--output-schema", str(schema)]
        if last_message:
            cmd += ["--output-last-message", str(last_message)]
        return cmd + ["-"]
    raise FactoryError(f"unknown agent: {agent}")


AGENT_GIT_ENV = {"GIT_AUTHOR_NAME": "software-factory", "GIT_AUTHOR_EMAIL": "software-factory@users.noreply.github.com", "GIT_COMMITTER_NAME": "software-factory", "GIT_COMMITTER_EMAIL": "software-factory@users.noreply.github.com"}


def invoke_agent(role, role_cfg, prompt, cwd, run_dir, step, *, read_only=False, schema=None, extra_read_tools=None, settings=None, allow_commits=False):
    """Run one agent. Returns exit code, final text, cost (None if not reported) and seconds."""
    agent = role_cfg["agent"]
    if not shutil.which(agent):
        raise FactoryError(f"{role}: the '{agent}' CLI is not installed or not on PATH")
    run_dir = Path(run_dir)
    last = run_dir / f"{step}.last.txt"
    (run_dir / f"{step}.prompt.md").write_text(prompt)
    start = time.time()
    cmd = agent_command(role_cfg, read_only=read_only, schema=schema, last_message=last, extra_read_tools=extra_read_tools, settings=settings, allow_commits=allow_commits)
    env = {"FACTORY_RUN": "1", **AGENT_GIT_ENV}
    if allow_commits:
        env["FACTORY_ALLOW_COMMIT"] = "1"
    code, out, err = sh(cmd, cwd=cwd, env=env, timeout=role_cfg.get("timeout_seconds", 1800), input_text=prompt)
    seconds = round(time.time() - start, 1)
    (run_dir / f"{step}.stdout.txt").write_text(out)
    (run_dir / f"{step}.stderr.txt").write_text(err)
    text, cost, structured = out, None, None
    if agent == "claude":
        data = None
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            text = data.get("result") or ""
            cost = data.get("total_cost_usd")
            structured = data.get("structured_output")
            if data.get("is_error"):
                code = code or 1
    elif last.exists():
        text = last.read_text()
    return {"code": code, "text": text, "cost": cost, "seconds": seconds, "structured": structured, "agent": agent}


# ---------------------------------------------------------------- gates


def run_check(cfg, tier, cwd, log_path):
    cmd = cfg["check"].get(tier)
    if not cmd:
        raise FactoryError(f"check.{tier} is not configured in .factory/config.json")
    start = time.time()
    code, out, err = sh(cmd, cwd=cwd, timeout=cfg["check"].get("timeout_seconds", 1800))
    Path(log_path).write_text(f"$ {cmd}\n{out}\n{err}")
    return {"tier": tier, "command": cmd, "code": code, "passed": code == 0, "seconds": round(time.time() - start, 1), "log": str(log_path)}


def tail(path_or_text, chars=6000):
    text = Path(path_or_text).read_text(errors="replace") if isinstance(path_or_text, Path) else path_or_text
    return text if len(text) <= chars else "[...truncated...]\n" + text[-chars:]


def scan_diff(cwd, base, cfg):
    """Deterministic gate-tampering scan of everything since the base."""
    prot = cfg["protected_paths"]
    gen = set(cfg["generated_paths"])
    test_rx = re.compile(cfg["test_path_regex"])
    numstat = git("diff", "--numstat", "-M", f"{base}...HEAD", cwd=cwd).splitlines()
    protected, lines, files = [], 0, []
    for row in numstat:
        parts = row.split("\t")
        if len(parts) < 3:
            continue
        add, rem, path = parts[0], parts[1], parts[2]
        if "=>" in path:
            m = re.match(r"(.*)\{(.*) => (.*)\}(.*)", path)
            if m:
                path = (m.group(1) + m.group(3) + m.group(4)).replace("//", "/")
            else:
                path = path.split(" => ")[-1]
        files.append(path)
        if any(path == p or path.startswith(p) for p in prot):
            protected.append(path)
        if path.rsplit("/", 1)[-1] not in gen and add != "-":
            lines += int(add) + int(rem)
    deleted = [f for f in git("diff", "--name-only", "--diff-filter=D", "-M", f"{base}...HEAD", cwd=cwd).splitlines() if test_rx.search(f)]
    sup_rx = re.compile(cfg["suppression_regex"])
    suppressions, current = [], None
    for line in git("diff", "-U0", "-M", f"{base}...HEAD", cwd=cwd).splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("+") and not line.startswith("+++") and sup_rx.search(line):
            suppressions.append({"file": current, "line": line[1:].strip()[:160]})
    hard = []
    if protected:
        hard.append("changes protected paths: " + ", ".join(protected[:10]))
    if deleted:
        hard.append("deletes test files: " + ", ".join(deleted[:10]))
    if lines > cfg["caps"]["max_changed_lines"]:
        hard.append(f"{lines} changed lines exceeds the cap of {cfg['caps']['max_changed_lines']}: slice the ticket thinner")
    gate_rx = re.compile(cfg["gate_config_regex"])
    gate_config = [f for f in files if gate_rx.search(f)]
    return {"files": files, "changed_lines": lines, "protected": protected, "deleted_tests": deleted, "suppressions": suppressions, "gate_config_changes": gate_config, "hard": hard}


def normalize_verdict(raw):
    """Fail closed: anything but a well-formed, blocker-free review is not a pass."""
    data = raw
    if isinstance(raw, str):
        text = raw.strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
        if fenced:
            text = fenced.group(1)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            try:
                data = json.loads(text[start:end + 1]) if start >= 0 and end > start else None
            except json.JSONDecodeError:
                data = None
    if not isinstance(data, dict) or data.get("verdict") not in ("pass", "changes_requested") or not isinstance(data.get("findings"), list):
        return {"verdict": "unusable", "summary": "The reviewer did not return valid JSON.", "findings": [], "blocking": []}
    findings = [f for f in data["findings"] if isinstance(f, dict)]
    blocking = [f for f in findings if f.get("severity") != "advisory"]
    verdict = "changes_requested" if blocking or data["verdict"] == "changes_requested" else "pass"
    return {"verdict": verdict, "summary": data.get("summary", ""), "findings": findings, "blocking": blocking}


# ---------------------------------------------------------------- run pipeline


COMMIT_RULES = {
    False: "Do not commit, push, open a pull request or change Git history. The runner handles Git.",
    True: "This repo's rules need separate commits, so you may commit, for example to keep a behaviour-preserving move apart from a behaviour change. Do not push, amend, rebase or open a pull request. The runner commits whatever you leave uncommitted.",
}


def prompt_values(cfg, ticket, feedback=""):
    fast = cfg["check"].get("fast") or cfg["check"]["full"]
    return {"TICKET": ticket_block(ticket), "FEEDBACK": feedback or "", "CHECK_FAST": fast, "CHECK_FULL": cfg["check"]["full"], "PROTECTED": ", ".join(cfg["protected_paths"]), "LANGUAGE": cfg.get("language", "en"), "COMMIT_RULE": COMMIT_RULES[bool(cfg.get("implementer_commits"))]}


def ticket_block(t):
    return f"Ticket {t.id}: {t.title}\n\n{t.body.strip()}"


def standards_block(repo, cfg):
    names = ["CODING_STANDARDS.md", "CONTRIBUTING.md", "docs/CODING_STANDARDS.md"]
    parts = []
    for n in names:
        p = Path(repo) / n
        if p.exists():
            parts.append(f"--- {n} ---\n{tail(p, 30000)}")
    return "\n\n".join(parts) or "(This repo documents no coding standards. Use the smell heuristics only.)"


def commit_all(cwd, message):
    git("add", "-A", cwd=cwd)
    if sh(["git", "diff", "--cached", "--quiet"], cwd=cwd)[0] == 0:
        return False
    code, out, err = sh(["git", "-c", "user.name=software-factory", "-c", "user.email=software-factory@users.noreply.github.com", "commit", "-q", "-m", message], cwd=cwd)
    if code != 0:
        raise FactoryError(f"commit refused (a repo hook may have rejected it): {tail(err or out, 1500)}")
    return True


def review_prompt(repo, wt, base, ticket, scan, cfg):
    diff = git("diff", "-M", f"{base}...HEAD", cwd=wt)
    if len(diff.encode()) > cfg["caps"]["max_diff_bytes"]:
        return None
    return render(prompt_path(repo, "review").read_text(), {
        "LANGUAGE": cfg.get("language", "en"),
        "TICKET": ticket_block(ticket),
        "STANDARDS": standards_block(repo, cfg),
        "SCAN": json.dumps({k: scan[k] for k in ("changed_lines", "suppressions", "gate_config_changes")}, ensure_ascii=False),
        "DIFF": diff,
    })


def pipeline(repo, cfg, ticket, wt, base, run_dir):
    """Implement, gate, review, rework: capped. Returns the run record."""
    rec = {"outcome": None, "attempts": 0, "cost_usd": 0.0, "cost_complete": True, "steps": [], "scan": None, "review": None, "handoff": ""}
    roles = cfg["roles"]

    def spend(res, label):
        if res["cost"] is None:
            rec["cost_complete"] = False
        else:
            rec["cost_usd"] = round(rec["cost_usd"] + res["cost"], 4)
        rec["steps"].append({"step": label, "agent": res["agent"], "code": res["code"], "seconds": res["seconds"], "cost": res["cost"]})

    def end(outcome):
        rec["outcome"] = outcome
        return rec

    if cfg.get("setup"):
        code, out, err = sh(cfg["setup"], cwd=wt, timeout=cfg["check"].get("timeout_seconds", 1800))
        (Path(run_dir) / "setup.log").write_text(out + err)
        if code != 0:
            return end("setup-failed")
    tier = cfg["check"].get("preflight") or "full"
    pre = run_check(cfg, tier if cfg["check"].get(tier) else "full", wt, Path(run_dir) / "preflight.log")
    rec["steps"].append({"step": "preflight", **{k: pre[k] for k in ("code", "seconds")}})
    if not pre["passed"]:
        # Nothing spent on a model: a red base must not be blamed on the agent.
        return end("base-red")

    feedback = None
    cap = cfg["caps"]["rework_attempts"]
    for attempt in range(cap + 1):
        rec["attempts"] = attempt + 1
        if rec["cost_usd"] > cfg["caps"]["max_run_usd"]:
            return end("budget-exceeded")
        if feedback is None:
            prompt = render(prompt_path(repo, "implement").read_text(), prompt_values(cfg, ticket))
        else:
            prompt = render(prompt_path(repo, "rework").read_text(), prompt_values(cfg, ticket, feedback))
        res = invoke_agent("implementer", roles["implementer"], prompt, wt, run_dir, f"implement-{attempt + 1}", settings=sandbox_settings(cfg), allow_commits=cfg.get("implementer_commits", False))
        spend(res, f"implement-{attempt + 1}")
        rec["handoff"] = res["text"]
        if res["code"] != 0:
            commit_all(wt, f"factory: partial work on {ticket.id} (attempt {attempt + 1})")
            return end("agent-failed")
        if re.search(r"^\s*STATUS:\s*BLOCKED", res["text"] or "", re.M):
            commit_all(wt, f"factory: partial work on {ticket.id} (attempt {attempt + 1})")
            return end("blocked")
        commit_all(wt, f"factory({ticket.id}): {ticket.title}\n\nAttempt {attempt + 1}.")
        if git("rev-parse", "HEAD", cwd=wt) == git("rev-parse", base, cwd=wt):
            return end("no-change")
        scan = scan_diff(wt, base, cfg)
        rec["scan"] = scan
        if scan["hard"]:
            return end("gate-tampering")
        check = run_check(cfg, "full", wt, Path(run_dir) / f"check-{attempt + 1}.log")
        rec["steps"].append({"step": f"check-{attempt + 1}", "code": check["code"], "seconds": check["seconds"]})
        rec["check"] = {k: check[k] for k in ("command", "code", "seconds", "log")}
        if not check["passed"]:
            feedback = "The full check failed. Output tail:\n\n" + tail(Path(check["log"]))
            if attempt == cap:
                return end("checks-failed")
            continue
        prompt = review_prompt(repo, wt, base, ticket, scan, cfg)
        if prompt is None:
            return end("diff-too-large")
        rev = invoke_agent("reviewer", roles["reviewer"], prompt, wt, run_dir, f"review-{attempt + 1}", read_only=True, schema=schema_path(repo))
        spend(rev, f"review-{attempt + 1}")
        verdict = normalize_verdict(rev["structured"] if rev.get("structured") else rev["text"])
        rec["review"] = verdict
        (Path(run_dir) / f"review-{attempt + 1}.json").write_text(json.dumps(verdict, indent=2, ensure_ascii=False))
        if rev["code"] != 0 or verdict["verdict"] == "unusable":
            return end("review-unusable")
        if verdict["verdict"] == "pass":
            return end("ready-for-human")
        feedback = "The reviewer requested changes. Fix these findings and nothing else:\n\n" + json.dumps(verdict["blocking"] or verdict["findings"], indent=2, ensure_ascii=False)
        if attempt == cap:
            return end("review-failed")
    return end("review-failed")


def report_markdown(ticket, rec, base, head, branch):
    review = rec.get("review") or {}
    scan = rec.get("scan") or {}
    cost = f"${rec['cost_usd']:.2f}" + ("" if rec.get("cost_complete", True) else " (partial: an agent reported no cost)")
    handoff = re.sub(r"^\s*STATUS:\s*DONE\s*\n?", "", rec.get("handoff") or "", count=1, flags=re.M).strip()
    lines = [
        handoff or "(The implementer left no hand-off.)", "",
        "## Factory report", "",
        f"- Ticket: {ticket.id} {ticket.title}",
        f"- Outcome: **{rec['outcome']}** after {rec['attempts']} attempt(s)",
        f"- Branch: `{branch}`  (`{base[:10]}..{head[:10]}`)",
        f"- Cost: {cost}",
    ]
    if rec.get("check"):
        lines.append(f"- Full check: exit {rec['check']['code']} in {rec['check']['seconds']}s: `{rec['check']['command']}`")
    if scan:
        lines.append(f"- Diff: {scan.get('changed_lines', 0)} changed lines in {len(scan.get('files', []))} files")
        for h in scan.get("hard", []):
            lines.append(f"- **Stopped by gate:** {h}")
        for s in scan.get("suppressions", [])[:10]:
            lines.append(f"- Suppression added in `{s['file']}`: `{s['line']}`")
        for f in scan.get("gate_config_changes", [])[:10]:
            lines.append(f"- Check configuration changed: `{f}`")
    if review:
        lines += ["", f"### Automated review: {review.get('verdict')}", "", review.get("summary", "") or ""]
        for f in review.get("findings", [])[:20]:
            lines.append(f"- [{f.get('axis', '?')}/{f.get('severity', '?')}] `{f.get('file', '?')}:{f.get('line', '?')}` {f.get('claim', '')}")
    lines += ["", "The factory never merges. A human reads this and decides."]
    return "\n".join(lines) + "\n"


def runs_today(repo):
    today = dt.date.today().isoformat()
    return sum(1 for r in ledger_read(repo) if str(r.get("at", "")).startswith(today) and r.get("kind") == "run")


def cmd_run(args):
    repo = repo_root(args.repo)
    cfg = load_config(repo)
    if not cfg["check"].get("full"):
        raise FactoryError("check.full is not configured. Run the checks stage first.")
    if (repo / FACTORY / "STOP").exists():
        raise FactoryError(".factory/STOP exists: the factory is halted. Remove it to resume.")
    if git("status", "--porcelain", "--", ".", ":(exclude).factory/tickets", cwd=repo):
        raise FactoryError("the working tree has uncommitted changes; commit them first so the run starts from a known base")
    if runs_today(repo) >= cfg["caps"]["runs_per_day"] and not args.force:
        raise FactoryError(f"daily cap reached ({cfg['caps']['runs_per_day']} runs). Raise caps.runs_per_day or pass --force.")
    if args.no_sandbox:
        cfg = deep_merge(cfg, {"local_sandbox": {"enabled": False}})
    for role in ("implementer", "reviewer"):
        agent = cfg["roles"][role]["agent"]
        if not shutil.which(agent):
            raise FactoryError(f"the {role} is set to '{agent}', but that CLI is not on PATH. Install and authenticate it, or change roles.{role}.agent. Nothing was spent.")
    tracker = tracker_for(repo, cfg)
    ready, waiting = frontier(repo, cfg)
    if args.ticket:
        match = [t for t in ready if t.id.lstrip("0") == args.ticket.lstrip("0")]
        if not match:
            why = next((w["why"] for w in waiting if w["id"].lstrip("0") == args.ticket.lstrip("0")), "not found")
            raise FactoryError(f"ticket {args.ticket} is not takeable: {why}")
        ticket = match[0]
    elif ready:
        ticket = ready[0]
    else:
        print(json.dumps({"outcome": "no-work", "waiting": waiting}, indent=2, ensure_ascii=False))
        return 0

    base_name = base_branch(repo, cfg)
    base = git("rev-parse", base_name, cwd=repo)
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{ticket.id}"
    run_dir = repo / FACTORY / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    branch = f"factory/{ticket.id}-{ticket.slug}"
    if git("rev-parse", "--verify", "-q", f"refs/heads/{branch}", cwd=repo, check=False)[0] == 0:
        branch = f"{branch}-{run_id[-10:].replace('-', '')}"
    wt = Path(tempfile.mkdtemp(prefix="factory-wt-"))
    git("worktree", "add", "-q", "-b", branch, str(wt), base, cwd=repo)
    tracker.claim(ticket)
    head = base
    rec = {"outcome": "runner-error", "attempts": 0, "cost_usd": 0.0, "steps": []}
    try:
        rec = pipeline(repo, cfg, ticket, wt, base, run_dir)
        head = git("rev-parse", "HEAD", cwd=wt)
    except FactoryError as e:
        rec["error"] = str(e)
    finally:
        report = report_markdown(ticket, rec, base, head, branch)
        (run_dir / "report.md").write_text(report)
        note = f"Run {run_id} ended **{rec['outcome']}** after {rec['attempts']} attempt(s). Branch `{branch}`."
        try:
            tracker.finish(ticket, rec["outcome"], note)
        except FactoryError as e:
            rec["tracker_error"] = str(e)
        row = {"kind": "run", "run": run_id, "at": dt.datetime.now().isoformat(timespec="seconds"), "ticket": ticket.id, "branch": branch, "outcome": rec["outcome"], "attempts": rec["attempts"], "cost_usd": rec["cost_usd"], "rehearsal": True, "sandboxed": bool((cfg.get("local_sandbox") or {}).get("enabled")), "human_verdict": None}
        ledger_append(repo, row)
        if not args.keep_worktree:
            sh(["git", "worktree", "remove", "--force", str(wt)], cwd=repo)
    publish = None
    if args.publish and rec["outcome"] == "ready-for-human":
        publish = publish_pr(repo, cfg, ticket, branch, base_name, run_dir / "report.md")
    print(json.dumps({"run": run_id, "ticket": ticket.as_dict(), "outcome": rec["outcome"], "attempts": rec["attempts"], "cost_usd": rec["cost_usd"], "branch": branch, "report": str(run_dir / "report.md"), "run_dir": str(run_dir), "pr": publish, "error": rec.get("error")}, indent=2, ensure_ascii=False))
    return 0 if rec["outcome"] == "ready-for-human" else 3


def publish_pr(repo, cfg, ticket, branch, base_name, report_path):
    code, out, err = sh(["git", "push", "-u", "origin", branch], cwd=repo)
    if code != 0:
        raise FactoryError(f"push failed: {err.strip()}")
    closes = f"\n\nCloses #{ticket.id}" if cfg["tracker"]["type"] == "github" else ""
    body = Path(report_path).read_text() + closes
    code, out, err = sh(["gh", "pr", "create", "--draft", "--base", base_name, "--head", branch, "--title", f"{ticket.title}", "--body", body], cwd=repo)
    if code != 0:
        raise FactoryError(f"gh pr create failed: {err.strip()}")
    return out.strip()


# ---------------------------------------------------------------- CI sub-steps
#
# A CI run is split so that no job holds both a model credential and a write token:
#   implement (Claude secret, read-only token removed before the agent starts)
#   verify    (no secret: clean checkout + patch + full check)
#   review    (OpenAI secret, read-only sandbox)
#   publish   (write token, runs no agent and no repo code)


def ticket_from_file(path, cfg):
    row = read_json(path)
    if not isinstance(row, dict) or "number" not in row:
        raise FactoryError(f"{path} is not an issue JSON document")
    labels = [l["name"] for l in row.get("labels", [])]
    return Ticket(row["number"], row.get("title", ""), row.get("body") or "", "hitl" if "factory:hitl" in labels else "afk", cfg["tracker"]["labels"]["ready"], parse_blockers(row.get("body")))


def cmd_ci_implement(args):
    repo = repo_root(args.repo)
    cfg = load_config(repo)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ticket = ticket_from_file(args.ticket_file, cfg)
    start = git("rev-parse", "HEAD", cwd=repo)
    base = args.base or start
    feedback = Path(args.feedback_file).read_text() if args.feedback_file and Path(args.feedback_file).exists() else None
    cfg = deep_merge(cfg, {"caps": {"rework_attempts": args.check_retries}})
    rec = ci_pipeline(repo, cfg, ticket, base, start, out_dir, feedback)
    head = git("rev-parse", "HEAD", cwd=repo)
    if head != start:
        with (out_dir / "change.patch").open("wb") as patch:
            # Written as bytes: a text round trip would corrupt files that are not UTF-8.
            if subprocess.run(["git", "format-patch", "--stdout", "--binary", f"{start}..HEAD"], cwd=repo, stdout=patch).returncode != 0:
                raise FactoryError("git format-patch failed")
    rec.update({"ticket": ticket.as_dict(), "base": base, "start": start, "head": head})
    (out_dir / "record.json").write_text(json.dumps(rec, indent=2, ensure_ascii=False))
    print(json.dumps({"outcome": rec["outcome"], "attempts": rec["attempts"], "cost_usd": rec["cost_usd"], "start": start, "base": base}))
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as f:
            f.write(f"outcome={rec['outcome']}\nstart={start}\nbase={base}\n")
    return 0


def ci_pipeline(repo, cfg, ticket, base, start, run_dir, feedback):
    """Implement and pass the deterministic gates. Review happens in a separate job."""
    rec = {"outcome": None, "attempts": 0, "cost_usd": 0.0, "cost_complete": True, "steps": [], "scan": None, "handoff": ""}
    run_dir = Path(run_dir)
    if cfg.get("setup"):
        code, out, err = sh(cfg["setup"], cwd=repo, timeout=cfg["check"].get("timeout_seconds", 1800))
        (run_dir / "setup.log").write_text(out + err)
        if code != 0:
            rec["outcome"] = "setup-failed"
            return rec
    if feedback is None:
        pre = run_check(cfg, "fast" if cfg["check"].get("fast") else "full", repo, run_dir / "preflight.log")
        if not pre["passed"]:
            rec["outcome"] = "base-red"
            return rec
    cap = cfg["caps"]["rework_attempts"]
    for attempt in range(cap + 1):
        rec["attempts"] = attempt + 1
        if rec["cost_usd"] > cfg["caps"]["max_run_usd"]:
            rec["outcome"] = "budget-exceeded"
            return rec
        name = "rework" if feedback else "implement"
        prompt = render(prompt_path(repo, name).read_text(), prompt_values(cfg, ticket, feedback))
        res = invoke_agent("implementer", cfg["roles"]["implementer"], prompt, repo, run_dir, f"implement-{attempt + 1}", allow_commits=cfg.get("implementer_commits", False))
        if res["cost"] is None:
            rec["cost_complete"] = False
        else:
            rec["cost_usd"] = round(rec["cost_usd"] + res["cost"], 4)
        rec["handoff"] = res["text"]
        if res["code"] != 0:
            rec["outcome"] = "agent-failed"
            return rec
        if re.search(r"^\s*STATUS:\s*BLOCKED", res["text"] or "", re.M):
            rec["outcome"] = "blocked"
            return rec
        commit_all(repo, f"factory({ticket.id}): {ticket.title}\n\nAttempt {attempt + 1}.")
        if git("rev-parse", "HEAD", cwd=repo) == start:
            rec["outcome"] = "no-change"
            return rec
        scan = scan_diff(repo, base, cfg)
        rec["scan"] = scan
        if scan["hard"]:
            rec["outcome"] = "gate-tampering"
            return rec
        check = run_check(cfg, "full", repo, run_dir / f"check-{attempt + 1}.log")
        rec["check"] = {k: check[k] for k in ("command", "code", "seconds")}
        if check["passed"]:
            rec["outcome"] = "gates-passed"
            return rec
        feedback = "The full check failed. Output tail:\n\n" + tail(Path(run_dir / f"check-{attempt + 1}.log"))
    rec["outcome"] = "checks-failed"
    return rec


def cmd_ci_review_prompt(args):
    repo = repo_root(args.repo)
    cfg = load_config(repo)
    if args.ticket_file:
        ticket = ticket_from_file(args.ticket_file, cfg)
    else:
        ticket = Ticket("PR", args.pr_title or "Pull request", Path(args.pr_body_file).read_text() if args.pr_body_file else "")
    scan = scan_diff(repo, args.base, cfg)
    status = "ok"
    prompt = ""
    if scan["hard"] and not args.advisory:
        status = "gate-tampering"
    else:
        prompt = review_prompt(repo, repo, args.base, ticket, scan, cfg)
        if prompt is None:
            prompt, status = "", "diff-too-large"
    Path(args.out).write_text(prompt)
    print(json.dumps({"status": status, "changed_lines": scan["changed_lines"], "hard": scan["hard"]}))
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as f:
            f.write(f"status={status}\n")
    return 0


def resolve_ci_outcome(rec_outcome, verify, verdict):
    """The runner decides from evidence. Anything missing means a human looks."""
    if rec_outcome != "gates-passed":
        return rec_outcome
    if verify != "success":
        return "verify-failed"
    if verdict is None or verdict["verdict"] == "unusable":
        return "review-unusable"
    return "ready-for-human" if verdict["verdict"] == "pass" else "review-failed"


FEEDBACK_MARK = "<!-- factory-feedback -->"


def cmd_ci_publish(args):
    repo = repo_root(args.repo)
    cfg = load_config(repo)
    if not args.branch.startswith("factory/"):
        raise FactoryError(f"refusing to publish to '{args.branch}': the factory only pushes factory/* branches")
    rec = read_json(args.record)
    if rec is None:
        raise FactoryError(f"cannot read run record {args.record}")
    ticket = ticket_from_file(args.ticket_file, cfg)
    verdict = None
    if args.verdict and Path(args.verdict).exists() and Path(args.verdict).read_text().strip():
        verdict = normalize_verdict(Path(args.verdict).read_text())
    outcome = resolve_ci_outcome(rec["outcome"], args.verify, verdict)
    has_patch = git("rev-parse", "HEAD", cwd=repo) != rec.get("start")
    if has_patch:
        # Rescan with the trusted config: the implement job's own scan ran next to the agent.
        scan = scan_diff(repo, rec["base"], cfg)
        rec["scan"] = scan
        if scan["hard"]:
            outcome = "gate-tampering"
    rec["outcome"], rec["review"] = outcome, verdict
    attempt = int(args.attempt)
    rework = outcome == "review-failed" and attempt <= cfg["caps"]["rework_attempts"]
    report = report_markdown(ticket, rec, rec.get("base", ""), git("rev-parse", "HEAD", cwd=repo), args.branch)
    decision = {"outcome": outcome, "attempt": attempt, "rework": rework, "pr": None}
    gh = GitHubTracker(repo, cfg)
    if has_patch and outcome not in ("gate-tampering", "no-change"):
        code, out, err = sh(["git", "push", "origin", f"HEAD:refs/heads/{args.branch}"], cwd=repo)
        if code != 0:
            raise FactoryError(f"push failed: {err.strip()}")
        found = json.loads(gh._gh("pr", "list", "--head", args.branch, "--state", "open", "--json", "number,url") or "[]")
        body = report + f"\n\nCloses #{ticket.id}\n"
        if found:
            decision["pr"] = found[0]["url"]
            gh._gh("pr", "edit", str(found[0]["number"]), "--body", body)
        else:
            decision["pr"] = gh._gh("pr", "create", "--draft", "--base", args.base_branch, "--head", args.branch, "--title", ticket.title, "--body", body).strip()
    if rework and decision["pr"]:
        findings = verdict["blocking"] or verdict["findings"]
        gh._gh("pr", "comment", decision["pr"], "--body", FEEDBACK_MARK + "\n\nAutomated review requested changes (attempt " + str(attempt) + "):\n\n```json\n" + json.dumps(findings, indent=2, ensure_ascii=False) + "\n```\n")
        gh._gh("workflow", "run", args.workflow, "-f", f"issue={ticket.id}", "-f", f"attempt={attempt + 1}", "-f", f"base={rec['base']}", "-f", f"branch={args.branch}")
    else:
        rework = False
        decision["rework"] = False
        note = f"Run ended **{outcome}** after attempt {attempt}." + (f" Draft PR: {decision['pr']}" if decision["pr"] else "")
        gh.finish(ticket, outcome, note)
    print(json.dumps(decision))
    return 0


def cmd_ci_review(args):
    """CI review with a Claude reviewer: read-only tools, no Bash, so no repo code runs."""
    repo = repo_root(args.repo)
    cfg = load_config(repo)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    res = invoke_agent("reviewer", cfg["roles"]["reviewer"], Path(args.prompt_file).read_text(), repo, out.parent, "review", read_only=True, schema=schema_path(repo))
    if res["code"] == 0:
        raw = res["structured"] if res["structured"] else res["text"]
        out.write_text(raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False))
    print(json.dumps({"code": res["code"], "cost_usd": res["cost"]}))
    return 0


def cmd_ci_feedback(args):
    """Write the newest review findings from a PR's comments to a file for a rework run."""
    repo = repo_root(args.repo)
    cfg = load_config(repo)
    gh = GitHubTracker(repo, cfg)
    out = gh._gh("pr", "view", args.branch, "--json", "comments")
    comments = (json.loads(out or "{}")).get("comments", [])
    marked = [c["body"] for c in comments if FEEDBACK_MARK in (c.get("body") or "") and (c.get("author") or {}).get("login", "") in ("github-actions", "github-actions[bot]", "app/github-actions")]
    if not marked:
        raise FactoryError(f"no review feedback found on the PR for {args.branch}")
    Path(args.out).write_text("The reviewer requested changes. Fix these findings and nothing else:\n\n" + marked[-1].replace(FEEDBACK_MARK, "").strip())
    print(json.dumps({"status": "ok"}))
    return 0


def cmd_ci_advise(args):
    """Run a read-only advisory task (triage, gardener, retro) and write its JSON result."""
    repo = repo_root(args.repo)
    cfg = load_config(repo)
    run_dir = Path(args.out).parent
    run_dir.mkdir(parents=True, exist_ok=True)
    material = Path(args.input_file).read_text() if args.input_file else ""
    prompt = render(prompt_path(repo, args.task).read_text(), {"ISSUE": material, "MATERIAL": material, "SINCE": args.since})
    role = cfg["roles"]["advisor"]
    res = invoke_agent("advisor", role, prompt, repo, run_dir, args.task, read_only=True, extra_read_tools="Bash(git log *),Bash(git show *),Bash(git diff *)")
    text = res["text"] or ""
    try:
        data = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
    except (AttributeError, json.JSONDecodeError):
        data = {"error": "advisor returned no JSON", "raw": text[:2000]}
    Path(args.out).write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(json.dumps({"code": res["code"], "cost_usd": res["cost"]}))
    return 0


def cmd_ci_advise_publish(args):
    """Holds the write token, runs no agent. Advisors can only ever comment or file an issue."""
    repo = repo_root(args.repo)
    cfg = load_config(repo)
    gh = GitHubTracker(repo, cfg)
    data = read_json(args.result)
    if not isinstance(data, dict) or data.get("error"):
        print(json.dumps({"published": False, "why": "no usable advisor output"}))
        return 3
    header = "> *This was generated by AI in the software factory.*\n\n"
    labels = cfg["tracker"]["labels"]
    if args.task == "triage":
        category = data.get("category")
        target = {"easy-call": labels["triage"], "needs-human": labels["human"], "needs-info": labels["info"], "not-a-bug": labels["triage"]}.get(category)
        if not target or not data.get("comment"):
            print(json.dumps({"published": False, "why": "unknown category"}))
            return 3
        gh._gh("issue", "comment", args.issue, "--body", header + f"**Triage: {category}**\n\n" + str(data["comment"]))
        if target != labels["triage"]:
            gh._gh("issue", "edit", args.issue, "--remove-label", labels["triage"], "--add-label", target)
        print(json.dumps({"published": True, "category": category}))
        return 0
    if not data.get("worth_filing") or not data.get("title"):
        print(json.dumps({"published": False, "why": "nothing worth filing"}))
        return 0
    gh._gh("issue", "create", "--title", str(data["title"])[:200], "--body", header + str(data.get("body", "")), "--label", labels["triage"])
    print(json.dumps({"published": True}))
    return 0


def cmd_ci_daily_cap(args):
    """Exit 3 once today's implement runs reach the cap. Counts runs that already started."""
    repo = repo_root(args.repo)
    cfg = load_config(repo)
    gh = GitHubTracker(repo, cfg)
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    out = gh._gh("run", "list", "--workflow", args.workflow, "--created", f">={today}", "--limit", "200", "--json", "databaseId")
    count = len(json.loads(out or "[]"))
    cap = cfg["caps"]["runs_per_day"]
    print(json.dumps({"runs_today": count, "cap": cap}))
    return 3 if count > cap else 0


# ---------------------------------------------------------------- brake test


def pick_sabotage_file(repo, cfg):
    test_rx = re.compile(cfg["test_path_regex"])
    candidates = []
    for f in tracked_files(repo):
        p = Path(repo) / f
        if Path(f).suffix.lower() in CODE_EXT and not test_rx.search(f) and not re.search(r"(^|/)(vendor|dist|build|node_modules|scripts|fixtures)/|\.min\.|bundle", f):
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if 0 < size < MAX_SABOTAGE_SIZE:
                candidates.append((f.count("/"), -size, f))
    candidates.sort()
    return candidates[0][2] if candidates else None


def cmd_brake_test(args):
    repo = repo_root(args.repo)
    cfg = load_config(repo)
    tier = args.tier
    if not cfg["check"].get(tier):
        raise FactoryError(f"check.{tier} is not configured")
    wt = Path(tempfile.mkdtemp(prefix="factory-brake-"))
    git("worktree", "add", "-q", "--detach", str(wt), "HEAD", cwd=repo)
    log_dir = repo / FACTORY / "runs"
    log_dir.mkdir(parents=True, exist_ok=True)
    result = {"tier": tier, "command": cfg["check"][tier]}
    try:
        if cfg.get("setup") and not args.skip_setup:
            code, out, err = sh(cfg["setup"], cwd=wt, timeout=cfg["check"].get("timeout_seconds", 1800))
            if code != 0:
                raise FactoryError(f"setup failed in the throwaway worktree: {tail(err or out, 1200)}")
        clean = run_check(cfg, tier, wt, log_dir / "brake-clean.log")
        result["clean_passes"] = clean["passed"]
        result["clean_seconds"] = clean["seconds"]
        if not clean["passed"]:
            result["passed"] = False
            result["reason"] = f"check.{tier} is already red on the untouched code: see {clean['log']}"
            print(json.dumps(result, indent=2))
            return 3
        if args.patch:
            code, out, err = sh(["git", "apply", str(Path(args.patch).resolve())], cwd=wt)
            if code != 0:
                raise FactoryError(f"sabotage patch does not apply: {err.strip()}")
            result["sabotage"] = f"patch {Path(args.patch).name}"
        else:
            target = args.file or pick_sabotage_file(repo, cfg)
            if not target:
                raise FactoryError("no source file to sabotage; pass --file")
            path = wt / target
            suffix = path.suffix.lower()
            path.write_text(path.read_text(errors="replace") + SABOTAGE.get(suffix, SABOTAGE_DEFAULT))
            result["sabotage"] = f"syntax error appended to {target}"
        broken = run_check(cfg, tier, wt, log_dir / "brake-broken.log")
        result["sabotaged_check_failed"] = not broken["passed"]
        result["sabotaged_seconds"] = broken["seconds"]
        result["passed"] = not broken["passed"]
        if broken["passed"]:
            result["reason"] = "the check stayed green with broken code: this brake does not hold there"
    finally:
        sh(["git", "worktree", "remove", "--force", str(wt)], cwd=repo)
    state = read_json(state_path(repo), {}) or {}
    prior = state.get("brake_test") or {}
    fp = fingerprint(cfg["check"].get("full"), cfg["check"].get("fast"))
    entry = {"passed": result["passed"], "fingerprint": fp, "at": dt.datetime.now().isoformat(timespec="seconds"), "sabotage": result.get("sabotage"), "tier": tier}
    same = prior.get("fingerprint") == fp
    entry["logic"] = bool(args.patch and result["passed"]) or bool(same and prior.get("logic"))
    holes = list(prior.get("logic_holes") or []) if same else []
    if args.patch and "sabotage" in result:
        holes = [h for h in holes if h != result["sabotage"]]
        if not result["passed"] and tier == "full":
            holes.append(result["sabotage"])
        if same and prior.get("passed") and not result["passed"]:
            # A logic hole is a finding about the tests. The check still goes red on broken code.
            entry["passed"] = True
    if holes:
        entry["logic_holes"] = holes
    state_update(repo, brake_test=entry)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["passed"] else 3


# ---------------------------------------------------------------- install


GITIGNORE_BLOCK = ["# software factory: local run evidence", ".factory/runs/", ".factory/state.json", ".factory/ledger.jsonl", ".factory/STOP"]


def copy_new(src, dst, report):
    dst = Path(dst)
    if dst.exists():
        report["skipped"].append(str(dst))
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    report["written"].append(str(dst))


def write_new(dst, text, report):
    dst = Path(dst)
    if dst.exists():
        report["skipped"].append(str(dst))
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text)
    report["written"].append(str(dst))


def merge_hook_settings(path, additions, report):
    """Add our hook entries to an agent's JSON settings, keeping everything already there."""
    path = Path(path)
    data = {}
    if path.exists():
        data = read_json(path)
        if data is None:
            raise FactoryError(f"{path} is not valid JSON; fix it and rerun")
    hooks = data.setdefault("hooks", {})
    changed = False
    for event, entries in additions.items():
        existing = hooks.setdefault(event, [])
        for entry in entries:
            if entry not in existing:
                existing.append(entry)
                changed = True
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        report["written"].append(str(path))
    else:
        report["skipped"].append(str(path))


def install_component(repo, component, cfg_overrides=None):
    repo = Path(repo)
    fdir = repo / FACTORY
    assets = SKILL_ROOT / "assets"
    report = {"component": component, "written": [], "skipped": []}
    if component != "labels" and not assets.is_dir():
        raise FactoryError("this is the copy of factory.py vendored in .factory/bin: it can run the factory but not install it. Run init from the skill's folder.")
    if component == "config":
        stack = detect_stack(repo)
        host = "github" if "github.com" in (git("remote", "get-url", "origin", cwd=repo, check=False)[1] or "") else None
        cfg = {"schema": 1, "language": (cfg_overrides or {}).get("language", "en"), "base_branch": base_branch(repo, DEFAULT_CONFIG),
               "setup": stack["setup"], "check": {"fast": stack["check_fast"], "full": stack["check_full"], "timeout_seconds": 1800},
               "tracker": {"type": "github" if host else "local"}, "roles": DEFAULT_CONFIG["roles"], "caps": DEFAULT_CONFIG["caps"], "gates": DEFAULT_CONFIG["gates"], "protected_paths": DEFAULT_CONFIG["protected_paths"]}
        write_new(fdir / "config.json", json.dumps(deep_merge(cfg, cfg_overrides), indent=2, ensure_ascii=False) + "\n", report)
        copy_new(Path(__file__).resolve(), fdir / "bin" / "factory.py", report)
        copy_new(assets / "review-schema.json", fdir / "review-schema.json", report)
        gi = repo / ".gitignore"
        current = gi.read_text() if gi.exists() else ""
        lines = [l for l in GITIGNORE_BLOCK if l not in current]
        if lines:
            gi.write_text(current.rstrip("\n") + ("\n\n" if current.strip() else "") + "\n".join(lines) + "\n")
            report["written"].append(str(gi))
    elif component == "prompts":
        for p in sorted((assets / "prompts").glob("*.md")):
            copy_new(p, fdir / "prompts" / p.name, report)
    elif component == "hooks":
        copy_new(assets / "hooks" / "guard.py", fdir / "hooks" / "guard.py", report)
        copy_new(assets / "hooks" / "stop_check.py", fdir / "hooks" / "stop_check.py", report)
        claude_add = read_json(assets / "hooks" / "claude-hooks.json")
        codex_add = read_json(assets / "hooks" / "codex-hooks.json")
        merge_hook_settings(repo / ".claude" / "settings.json", claude_add["hooks"], report)
        merge_hook_settings(repo / ".codex" / "hooks.json", codex_add["hooks"], report)
    elif component == "pr-template":
        copy_new(assets / "pull_request_template.md", repo / ".github" / "pull_request_template.md", report)
    elif component == "labels":
        cfg = load_config(repo)
        gh = GitHubTracker(repo, cfg)
        colors = {"triage": "fbca04", "info": "d876e3", "ready": "0e8a16", "human": "1d76db", "wontfix": "ffffff", "running": "c2e0c6", "failed": "b60205"}
        for key, name in cfg["tracker"]["labels"].items():
            gh._gh("label", "create", name, "--force", "--color", colors.get(key, "ededed"), "--description", "software factory")
            report["written"].append(f"label {name}")
        gh._gh("label", "create", "factory:hitl", "--force", "--color", "5319e7", "--description", "software factory: needs a human")
        report["written"].append("label factory:hitl")
    else:
        raise FactoryError(f"unknown component: {component}")
    return report


def cmd_init(args):
    repo = repo_root(args.repo)
    overrides = {"language": args.language} if args.language else None
    if args.vendor_only:
        report = {"component": "vendor", "written": [], "skipped": []}
        target = repo / FACTORY / "bin" / "factory.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(__file__).resolve(), target)
        report["written"].append(str(target))
    else:
        report = install_component(repo, args.component, overrides)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def review_steps(cfg, trusted="${{ runner.temp }}/trusted/.factory", allow_bots=False):
    """The workflow steps for the configured reviewer. Both run read-only and write verdict.json."""
    if cfg["roles"]["reviewer"]["agent"] == "codex":
        lines = ["# Codex runs last in the job, read-only, through the action's key proxy.",
                 "- uses: openai/codex-action@83eeca45b831921e73d1f6a7955efccc568fe4e8  # v1.13",
                 "  if: steps.prompt.outputs.status == 'ok'", "  with:",
                 "    openai-api-key: ${{ secrets.OPENAI_API_KEY }}",
                 "    prompt-file: ${{ runner.temp }}/review-prompt.md",
                 "    output-file: ${{ runner.temp }}/verdict.json",
                 f"    output-schema-file: {trusted}/review-schema.json",
                 "    sandbox: read-only", "    safety-strategy: drop-sudo"]
        if allow_bots:
            lines += ["    # Rework runs are dispatched by github-actions[bot].", '    allow-bots: "true"']
    else:
        lines = ["- name: Install the reviewer", "  if: steps.prompt.outputs.status == 'ok'",
                 f"  run: npm install -g @anthropic-ai/claude-code@{CLAUDE_VERSION}",
                 "- name: Review, read-only", "  if: steps.prompt.outputs.status == 'ok'", "  env:",
                 "    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}",
                 f"    FACTORY_TRUSTED_DIR: {trusted}", "  run: |",
                 '    python3 "$RUNNER_TEMP/trusted/.factory/bin/factory.py" ci-review --prompt-file "$RUNNER_TEMP/review-prompt.md" --out "$RUNNER_TEMP/verdict.json"']
    return ("\n" + " " * 6).join(lines)


def trigger_secrets(name, cfg):
    keys = {"claude": "ANTHROPIC_API_KEY", "codex": "OPENAI_API_KEY"}
    reviewer = keys[cfg["roles"]["reviewer"]["agent"]]
    implementer = keys[cfg["roles"]["implementer"]["agent"]]
    advisor = keys[cfg["roles"]["advisor"]["agent"]]
    return {"review-pr": [reviewer], "implement": sorted({implementer, reviewer})}.get(name, [advisor])


def toolchain_steps(repo):
    """Workflow steps that install what check.full needs. A starting point, for the user to confirm."""
    repo = Path(repo)
    pad = "      "
    steps = []
    if (repo / "package.json").exists():
        version = "node-version-file: .nvmrc" if (repo / ".nvmrc").exists() else "node-version-file: .node-version" if (repo / ".node-version").exists() else "node-version: lts/*"
        steps.append(f"- uses: actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1  # v7.1.0\n{pad}  with:\n{pad}    {version}")
    if (repo / "pyproject.toml").exists() or (repo / "requirements.txt").exists():
        steps.append(f"- uses: actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0  # v7.1.0\n{pad}  with:\n{pad}    python-version: '3.x'")
    if (repo / "go.mod").exists():
        steps.append(f"- uses: actions/setup-go@b7ad1dad31e06c5925ef5d2fc7ad053ef454303e  # v7.0.0\n{pad}  with:\n{pad}    go-version-file: go.mod")
    if not steps:
        return "# Add the steps that install your toolchain here."
    return ("\n" + pad).join(steps)


def cmd_trigger(args):
    repo = repo_root(args.repo)
    cfg = load_config(repo)
    name = args.name
    spec = TRIGGERS.get(name)
    if not spec:
        raise FactoryError(f"unknown trigger '{name}'. Choose from: {', '.join(TRIGGERS)}")
    a = audit(repo)
    status = {s["name"]: s for s in a["stages"]}
    missing = [f"{st}: {m}" for st in spec["needs"] for m in status[st]["missing"]]
    forced = False
    if missing:
        if not args.force:
            print(json.dumps({"installed": False, "trigger": name, "refused_because": missing}, indent=2, ensure_ascii=False))
            return 3
        forced = True
    code, out = git("status", "--porcelain", "--", ".github/workflows", cwd=repo, check=False)
    pending = [l[3:] for l in out.splitlines() if "factory-" in l]
    if pending and not args.force:
        print(json.dumps({"installed": False, "trigger": name, "refused_because": [f"previous trigger not committed yet: {', '.join(pending)}. Add one trigger at a time, commit it and watch it first."]}, indent=2, ensure_ascii=False))
        return 3
    needed = {"review-pr": "review", "triage": "triage", "implement": "implement", "gardener": "gardener", "retro": "retro"}[name]
    if not (repo / FACTORY / "prompts" / f"{needed}.md").exists() and not args.force:
        print(json.dumps({"installed": False, "trigger": name, "refused_because": [f".factory/prompts/{needed}.md is missing: run init --component prompts"]}, indent=2))
        return 3
    vendored = repo / FACTORY / "bin" / "factory.py"
    if not vendored.exists():
        vendored.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(__file__).resolve(), vendored)
    if not (repo / FACTORY / "review-schema.json").exists():
        shutil.copyfile(SKILL_ROOT / "assets" / "review-schema.json", repo / FACTORY / "review-schema.json")
    dst = repo / ".github" / "workflows" / spec["file"]
    if dst.exists():
        print(json.dumps({"installed": False, "trigger": name, "refused_because": [f"{dst} already exists"]}, indent=2))
        return 3
    dst.parent.mkdir(parents=True, exist_ok=True)
    text = (SKILL_ROOT / "assets" / "workflows" / spec["file"]).read_text()
    text = render(text, {"BASE_BRANCH": base_branch(repo, cfg), "READY_LABEL": cfg["tracker"]["labels"]["ready"], "TRIAGE_LABEL": cfg["tracker"]["labels"]["triage"], "FAILED_LABEL": cfg["tracker"]["labels"]["failed"], "TOOLCHAIN": toolchain_steps(repo), "CLAUDE_VERSION": CLAUDE_VERSION, "REVIEW_STEP": review_steps(cfg, allow_bots=name == "implement")})
    dst.write_text(text)
    if forced:
        ledger_append(repo, {"kind": "force", "at": dt.datetime.now().isoformat(timespec="seconds"), "trigger": name, "missing": missing})
    print(json.dumps({"installed": True, "trigger": name, "file": str(dst), "writes": spec["writes"], "secrets_required": trigger_secrets(name, cfg), "forced_past": missing}, indent=2, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- simple commands


def cmd_audit(args):
    a = audit(repo_root(args.repo))
    print(json.dumps(a, indent=2, ensure_ascii=False) if args.json else print_audit(a))
    return 0


def cmd_frontier(args):
    repo = repo_root(args.repo)
    ready, waiting = frontier(repo, load_config(repo))
    print(json.dumps({"ready": [t.as_dict() for t in ready], "waiting": waiting}, indent=2, ensure_ascii=False))
    return 0


def cmd_done(args):
    """Mark a local ticket done after its PR merges, so its dependents become takeable."""
    repo = repo_root(args.repo)
    cfg = load_config(repo)
    tracker = tracker_for(repo, cfg)
    if not isinstance(tracker, LocalTracker):
        raise FactoryError("on GitHub, closing the issue does this: the factory's PRs say 'Closes #N'")
    hit = [t for t in tracker.all() if t.id.lstrip("0") == args.ticket.lstrip("0")]
    if not hit:
        raise FactoryError(f"no ticket {args.ticket}")
    tracker.set_status(hit[0], "done")
    ready, _ = frontier(repo, cfg)
    print(json.dumps({"done": hit[0].id, "ready_now": [t.id for t in ready]}))
    return 0


def cmd_check(args):
    repo = repo_root(args.repo)
    cfg = load_config(repo)
    log = repo / FACTORY / "runs" / f"check-{args.tier}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    r = run_check(cfg, args.tier, repo, log)
    print(json.dumps(r, indent=2))
    return 0 if r["passed"] else 3


def cmd_scan(args):
    repo = repo_root(args.repo)
    result = scan_diff(repo, args.base, load_config(repo))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 3 if args.strict and result["hard"] else 0


def cmd_ledger(args):
    repo = repo_root(args.repo)
    rows = ledger_read(repo)
    if args.mark:
        if args.verdict not in ("accepted", "rejected"):
            raise FactoryError("--verdict must be accepted or rejected")
        hit = [r for r in rows if r.get("run") == args.mark]
        if not hit:
            raise FactoryError(f"no run {args.mark} in the ledger")
        for r in hit:
            r["human_verdict"] = args.verdict
            r["human_note"] = args.note
        ledger_path(repo).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    runs = [r for r in rows if r.get("kind") == "run"]
    summary = {"runs": len(runs), "accepted": sum(1 for r in runs if r.get("human_verdict") == "accepted"), "rejected": sum(1 for r in runs if r.get("human_verdict") == "rejected"), "unmarked": sum(1 for r in runs if not r.get("human_verdict")), "cost_usd": round(sum(r.get("cost_usd") or 0 for r in runs), 2)}
    print(json.dumps({"summary": summary, "runs": runs[-args.last:]}, indent=2, ensure_ascii=False))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="factory.py", description=__doc__.splitlines()[0])
    ap.add_argument("--version", action="version", version=VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_text):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--repo", default=".")
        p.set_defaults(fn=fn)
        return p

    p = add("audit", cmd_audit, "Inventory the repo and evaluate each stage")
    p.add_argument("--json", action="store_true")
    p = add("init", cmd_init, "Install one component without overwriting anything")
    p.add_argument("--component", choices=["config", "prompts", "hooks", "pr-template", "labels"], default="config")
    p.add_argument("--language")
    p.add_argument("--vendor-only", action="store_true", help="Refresh .factory/bin/factory.py from this copy")
    add("frontier", cmd_frontier, "List takeable tickets; fail on cycles")
    p = add("done", cmd_done, "Mark a local ticket done after its PR merges")
    p.add_argument("ticket")
    p = add("check", cmd_check, "Run a check tier")
    p.add_argument("--tier", choices=["fast", "full"], default="full")
    p = add("brake-test", cmd_brake_test, "Prove a check goes red on sabotaged code")
    p.add_argument("--tier", choices=["fast", "full"], default="full")
    p.add_argument("--patch", help="Diff with a hand-written logic sabotage")
    p.add_argument("--file", help="Repo-relative file to break instead of the auto-picked one")
    p.add_argument("--skip-setup", action="store_true")
    p = add("scan", cmd_scan, "Run the gate-tampering scan against a base ref")
    p.add_argument("--base", required=True)
    p.add_argument("--strict", action="store_true", help="Exit 3 when a hard gate is violated")
    p = add("run", cmd_run, "One attended run: one ticket, fresh agents, capped rework")
    p.add_argument("--ticket")
    p.add_argument("--publish", action="store_true", help="Push the branch and open a draft PR when the run ends ready-for-human")
    p.add_argument("--keep-worktree", action="store_true")
    p.add_argument("--force", action="store_true", help="Ignore the daily cap")
    p.add_argument("--no-sandbox", action="store_true", help="Run the implementer's Bash outside Claude's sandbox; recorded in the ledger")
    p = add("ledger", cmd_ledger, "Show runs; record a human verdict")
    p.add_argument("--mark")
    p.add_argument("--verdict")
    p.add_argument("--note", default="")
    p.add_argument("--last", type=int, default=20)
    p = add("trigger", cmd_trigger, "Install one trigger workflow, if its gates pass")
    p.add_argument("name")
    p.add_argument("--force", action="store_true", help="Install past failing gates; recorded in the ledger")
    p = add("ci-implement", cmd_ci_implement, "CI: implement and gate; write a patch and a record")
    p.add_argument("--ticket-file", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--base")
    p.add_argument("--feedback-file")
    p.add_argument("--check-retries", type=int, default=1)
    p = add("ci-review-prompt", cmd_ci_review_prompt, "CI: write the reviewer prompt")
    p.add_argument("--base", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--ticket-file")
    p.add_argument("--pr-title")
    p.add_argument("--pr-body-file")
    p.add_argument("--advisory", action="store_true", help="Review even when a hard gate is hit (human PRs)")
    p = add("ci-publish", cmd_ci_publish, "CI: decide from artifacts, push, open the draft PR, chain or stop")
    p.add_argument("--record", required=True)
    p.add_argument("--ticket-file", required=True)
    p.add_argument("--verdict")
    p.add_argument("--verify", default="skipped")
    p.add_argument("--attempt", default="1")
    p.add_argument("--branch", required=True)
    p.add_argument("--base-branch", required=True)
    p.add_argument("--workflow", default="factory-implement.yml")
    p = add("ci-review", cmd_ci_review, "CI: run a Claude reviewer, read-only")
    p.add_argument("--prompt-file", required=True)
    p.add_argument("--out", required=True)
    p = add("ci-feedback", cmd_ci_feedback, "CI: fetch the latest review findings from the PR")
    p.add_argument("--branch", required=True)
    p.add_argument("--out", required=True)
    p = add("ci-advise", cmd_ci_advise, "CI: run a read-only advisory task")
    p.add_argument("--task", choices=["triage", "gardener", "retro"], required=True)
    p.add_argument("--input-file")
    p.add_argument("--since", default="7 days ago")
    p.add_argument("--out", required=True)
    p = add("ci-advise-publish", cmd_ci_advise_publish, "CI: publish an advisory result; holds the write token")
    p.add_argument("--task", choices=["triage", "gardener", "retro"], required=True)
    p.add_argument("--result", required=True)
    p.add_argument("--issue")
    p = add("ci-daily-cap", cmd_ci_daily_cap, "CI: exit 3 when today's runs exceed the cap")
    p.add_argument("--workflow", default="factory-implement.yml")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except FactoryError as e:
        print(f"factory: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
