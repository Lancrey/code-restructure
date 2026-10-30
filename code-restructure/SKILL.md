---
name: code-restructure
description: Architectural audit and safe structural refactoring of an existing codebase (any language, any Git host, local folder or attached repo). Use this skill whenever the user asks to restructure, reorganize, modularize, clean up or audit the architecture of a project — "clean up my project structure", "this repo grew organically", "separate features into modules", "move plugins into their own folder", "make this maintainable/scalable", "audit the architecture", "refactor the structure without breaking anything", and the same requests in French — "restructurer le projet", "réorganiser le code / l'arborescence", "nettoyer la structure du dépôt", "découper en modules", "audit d'architecture" — or types /restructure. Also use it for audit-only or plan-only requests about a repo's organization. Do not use it for feature work, bug fixes, or logic refactors inside a single file; the point of this skill is moving things without changing what they do.
---

# Code Restructure

Audit the architecture of a repository that grew without a plan, design a better structure for it, migrate the files, and prove nothing broke. The skill covers four phases; the user may ask for any prefix of them.

| Mode | Phases | Typical request |
|---|---|---|
| `audit` | 1 | "audit my architecture", "what's wrong with this structure" |
| `plan` | 1–2 | "propose a better layout", "how would you reorganize this" |
| `full` (default) | 1–4 with an approval gate after phase 2 | "restructure this", `/restructure` |

If the user says they don't want to be asked ("just do it", `--no-gate`), skip the gate but still write the plan document first — the plan is what the user and the next engineer will read to understand the diff.

## The one rule that outranks the others

Behaviour must be identical before and after. A cleaner tree that breaks one import, one CI job, one hard-coded path or one `pip install -e .` has negative value: the user came here to reduce risk, not to trade known debt for an unknown regression. Whenever a structural improvement and compatibility conflict, keep compatibility and write down what you did not do and why. Passing tests are evidence, not proof — say "verified by X" and "not verified", never "fully safe".

Everything below is in service of that rule.

## Phase 0 — Setup (always)

1. **Locate the repo.** Local folder, attached repo (`add_repo` in claude.ai/Cowork) or a path the user gives. If the folder is not a Git repo, say so and ask before continuing: without Git there is no rollback path, and you would be working without a safety net. Then run `git status --porcelain`: if it prints anything (modified, staged or untracked files), stop and ask the user what to do with them — commit them first, or confirm they may stay untouched in the working tree while you work. Never stash, discard or `git add -A` someone else's uncommitted changes: your commits must contain only the moves and edits of the plan, so stage files by name.
2. **Confirm scope** in one line: which mode, which programming language(s), and the output language (see step 6), any constraint the user already stated (e.g. "I want a `src/` layout", "don't touch `legacy/`"). Apart from the output-language question in step 6, which is always asked, only ask a question if a wrong guess would be expensive; otherwise state your assumption and proceed.
3. **Run the inventory script** — it gives you the tree, language mix, manifests, entry points, test runners, CI files and the directories to leave alone, in one call:
   ```bash
   python3 <skill-path>/scripts/inventory.py <repo> --out <workdir>/inventory.json
   ```
   `<workdir>` throughout this document is the evidence folder next to the reports, `<report-dir>/restructure-evidence/` (see step 6 and Phase 1): every JSON and raw output the scripts produce goes there, so the reader of the reports finds the evidence in one place. Read the JSON summary it prints. It is the map for everything that follows.
4. **Read the repo's own guidance**: `README`, `CONTRIBUTING`, `CLAUDE.md`/`AGENTS.md`, `ARCHITECTURE*`, `docs/`. If the project has stated conventions, your proposal must respect them or explicitly argue against them.
5. **Load the ecosystem reference** that matches the dominant language(s), from `references/` — see the table at the end. It tells you how imports resolve, which files pin paths (manifests, tsconfig, pyproject, Cargo.toml…), the idiomatic layouts, and the ecosystem's refactoring tools. Mixed repos: load every reference that applies. Load `references/frontend-assets.md` in addition whenever the inventory lists stylesheets, HTML or template files, or an asset folder (`img/`, `fonts/`, `static/`, `public/`, `assets/`) that the plan may touch: paths inside stylesheets and templates break without any test or build failing.
6. **Decide where reports go and in which language.** Default is `docs/architecture-audit.md`, `docs/architecture-migration-plan.md`, `docs/architecture-validation.md`. If the repo already has a documentation convention (`documentation/`, `doc/`, a docs site with a source dir), put them there instead. If a report with one of these names already exists (a previous run), do not overwrite it: read it, and write the new ones with a date suffix (`architecture-audit-2026-10-30.md`) or ask the user which they prefer. **Ask the user which language the reports and the conversation summary must be written in**, unless they already stated it in the request. Offer: English, French, Spanish, German, Italian, Portuguese, Estonian, Russian, Ukrainian, Chinese (Simplified), Arabic — the README's language first as the suggested default, then the user's conversation language if different, then the rest; the user may type any other language. Arabic and Chinese reports keep Markdown tables, code blocks, paths and identifiers in their original left-to-right form. Ask it in the same question as the scope check, using AskUserQuestion when available. If nobody can answer (scheduled or unattended run, or the question went unanswered), use the README's language, and say which one you chose at the top of the audit. Code identifiers, paths and commands stay verbatim. Use the chosen language for every report, the PR description and the final summary.

## Phase 1 — Analysis

Goal: understand the codebase well enough that the plan you write in phase 2 has no surprises in phase 3. The inventory gives you the skeleton; now read the code.

**Build the dependency picture.** For every top-level module/package/directory: what does it import, what imports it, which ones are entry points (CLI, web handlers, scheduled jobs, build targets). Use the ecosystem's own tooling when it exists (`pydeps`, `madge`, `go list -deps`, `cargo tree`, `jdeps`), otherwise grep import statements. You are looking for:

- cycles (A imports B imports A) — moving one side of a cycle usually breaks it in a way tests may not catch;
- god modules — one `utils.py` / `helpers.js` that everything imports and that mixes unrelated concerns;
- misplaced files — a database layer inside `views/`, tests next to scripts, a plugin inside `core/`;
- duplicated responsibility — two modules doing the same thing with different names;
- dead-looking code — flag it, never delete it in this skill (see safeguards).

**Find every integration point that pins a path.** This is where structural refactors fail silently. Check, and record in the audit:

- dynamic imports and string-based loading (`importlib.import_module("plugins." + name)`, `require(\`./${name}\`)`, reflection, plugin registries, Django `INSTALLED_APPS`, Spring component scan, service loaders);
- paths in configuration: manifests (`pyproject.toml` `packages=`, `package.json` `main`/`exports`/`bin`/`files`, `tsconfig` `paths`, `Cargo.toml` `[[bin]]`, `setup.cfg`), Dockerfiles, `docker-compose`, Makefiles, CI workflows, `.pre-commit-config`, linter configs with per-path rules, IDE run configs;
- runtime file references: `open("data/x.json")`, `__file__`-relative paths, `os.path.join(BASE_DIR, ...)`, templates/static directories, migration folders, fixtures, asset pipelines;
- public surface: anything published (package exports, `__init__.py` re-exports, a library's documented import paths), CLI commands, env vars that carry paths, documented URLs to files in the repo (raw GitHub links in a README count).

Grep for each of these; do not rely on memory of "what projects usually do". The inventory JSON lists the config files it found so you can open each one.

**Identify the functional domains.** Group what you found into the natural units of this project: features, shared/infrastructure, plugins/extensions, scripts/tooling, tests, config. Name them in the project's own vocabulary (if the README says "connectors", don't call them "adapters").

**Verify every factual claim before it goes in a report.** Reports are read as ground truth and the migration plan is built on them, so a wrong statement ("Core imports only Foundation", "84 tests", "file X is 1,600 lines") propagates into wrong moves. Reading a file and summarizing it is how these errors get in; confirm with a command instead — `grep -h '^import' dir/*`, `wc -l`, a test-function count, a dependency tool's output — and re-check any claim that appears in more than one place (summary and detail tables must agree). Where the repo's own documents disagree with the code (a README that says 63 tests while there are 84), report the discrepancy rather than choosing silently.

**Additional recommendations.** The user wants the audit to go beyond structure when the evidence supports it: logic-level refactors, duplicated code, dependency cycles, dead code, performance or correctness defects, stale documentation, build or tooling gaps. These belong in a dedicated section of the audit, "Additional recommendations", kept strictly apart from the structural findings and from the migration plan, because the migration must stay a pure move. The bar for an entry is high, since the user will act on it:

- **Confirmed.** State the evidence: the command and its output, or the file and line range. "Probably unused" is not an entry; "no reference found by `grep -rn X` over these paths, and no dynamic loading in the repo (checked: …)" is.
- **Solid.** Say what the change would be, what it would affect, what could go wrong, and how to check it afterwards. If you could not confirm a suspicion, put it under "Suspected, not confirmed" with what would settle it.
- **Rated** by value and risk, and tagged as *behaviour-preserving* or *behaviour-changing*; the latter must say exactly what changes.
- **Never executed** by this skill. Phase 3 only carries out the structural plan; additional recommendations wait for the user's explicit choice and become a separate task.

Quality matters more than volume: five verified entries are worth more than twenty impressions.

Save the raw output of every tool an entry cites (knip, `npm audit`, dependency graphs, diffs) next to the reports, in a `restructure-evidence/` folder in the same directory as the reports (`<report-dir>/restructure-evidence/`), and name the file in the entry. A reader who doubts a claim then has the output, not just your summary of it.

**Never copy secret values into any report or evidence file.** Credentials, keys, salts, tokens and passwords committed in the repo must be reported by location only: `file:line NAME = <redacted>`. Reports and evidence get committed, shared and pasted; a secret copied into them is a second leak that survives even if the original is rotated out of the code. Use the script rather than grep, because it prints no value by construction:

```
python3 <skill-path>/scripts/secrets_scan.py <repo> [--plan docs/architecture-migration-plan.md] --out <report-dir>/restructure-evidence/secrets-redacted.txt
```

Run it in Phase 1 and cite its output in the audit. Never run a grep whose output would contain a value, and never paste a secret line into a report. Before you finish each phase, prove that nothing leaked:

```
python3 <skill-path>/scripts/secrets_scan.py <repo> --verify <report-dir>/architecture-*.md <report-dir>/restructure-evidence
```

Exit code 1 means a value is in one of those files: remove it and re-run until it prints `clean`. Add `--strict` to the final Phase 4 run: it also checks plain-word values (`DB_PASSWORD="sunshine"`), at the price of some false alarms on ordinary words, which you then review by hand. Git-ignored files (`.env`, `config.local.*`) are scanned too and tagged `[git-ignored, not committed]`: they are not a repository leak, so report them as a separate line ("local credentials present on disk, not in Git"), but their values are still checked by `--verify`. Low-confidence hits (name looks secret, value looks like a word) are hidden from the list by default; use `--all` if the repo is small enough to review them. `baseline.py` redacts credential-looking strings in the output tails it saves (`password=…`, URL credentials, known token formats), because a test runner can print runtime secrets that are nowhere in the source; still skim the `stdout_tail` fields of the baseline JSON before committing them. The scanner is heuristic: a clean result means no known pattern was found, not that the repo has no secrets. In particular, values containing spaces (passphrases) are never detected; open the config files the audit lists rather than relying on the scan for those.

**Secrets already inside files you only move.** `secrets_scan.py --plan` tags hits in files that the relocation table moves with `[moved]`. Do not edit or scrub those files (moves only); list them in the audit as `file:line NAME = <redacted>` and add an Additional recommendation: rotate the credential and scrub the file, noting that moving it into a committed `docs/` folder neither worsens nor fixes the exposure and that it stays in Git history.

**Write `architecture-audit.md`** using the template in `references/report-templates.md`. Rank findings by risk-to-maintainability, not by how easy they are to fix. In `audit` mode this is the final report: open it with the non-technical "Summary for stakeholders" section described in the template, then stop and summarize in the conversation.

## Phase 2 — Design

Design the target structure for *this* repo. There is no house template; the right answer for a 40-file Flask app, a Laravel site, a Rust workspace and a monorepo of three Node services are four different shapes. Use the ecosystem reference's idiomatic layouts as the menu, then pick based on:

- what the code actually does (the domains from phase 1), not what it is named;
- the size: a repo with 30 source files does not need `domain/application/infrastructure` layers; it needs five well-named folders;
- where the project is going, if the user said anything about it ("we'll add more plugins", "we'll split the API out");
- what the existing conventions already got right — keep those, change only what the audit justifies.

Guard against the two classic failure modes: **over-engineering** (abstractions nobody asked for, a structure that would suit a 10-person team on a solo project) and **a rewrite in disguise** (if the plan touches most lines of most files, it is not a structural refactor any more — scale it back).

**Prefer incremental.** Order the moves so that each step leaves the repo working: usually leaf utilities first, then features, then entry points, then cleanup of compatibility shims. Each step should be one commit the user could stop at.

**Decide compatibility measures per public path.** For each path identified in phase 1 as public or externally consumed: keep it working. Options, in order of preference: re-export/forwarding module at the old location (Python `from new.location import *`, JS `export * from`), an alias in the manifest (`tsconfig` paths, `package.json` exports map), a symlink only when the ecosystem has nothing better. Mark each shim with a comment saying where the real code lives and that it is a compatibility layer; whether and when to remove it is the user's call, so put it in the plan as an explicit open question rather than deciding.

**Write `architecture-migration-plan.md`** (template in `references/report-templates.md`). In `plan` mode this is the final report: open it with the non-technical "Summary for stakeholders" section described in the template. The relocation table must be exhaustive and machine-readable — the check-moves script in phase 3 reads it:

```
| From | To | Note |
|---|---|---|
| src/utils.py | src/core/text.py | split: text helpers |
| src/utils.py | src/core/dates.py | split: date helpers |
| plugins/*.py | src/plugins/<name>/ | one dir per plugin |
```

Before writing glob rows, run `check_moves.py <repo> --plan <plan> --expand-globs` on the untouched tree: it prints one concrete row per matching file, ready to paste, so the plan lists exactly what moves and the final check can verify every destination. A `From` that appears on several rows is a **split**: one module becomes several. A split moves code between files, so it is the one structural change that touches file contents; keep it to cut-and-paste of whole functions/classes, leave a forwarding shim at the old path that re-exports from every destination (so `from src.utils import x` keeps working), and give it its own commit. `check_moves.py` treats any destination of a split as "already updated". Glob rows (`plugins/*.py`) are checked for files left behind, but a destination with a placeholder (`<name>`) cannot be checked: list the concrete files in the Note column or as extra rows when the set is small.

Then **stop and present the plan** (unless the user disabled the gate). In the conversation: the before/after tree, the three or four riskiest moves and how you will protect them, and any decision you want the user to make. Do not start moving files until they answer. In `plan` mode this is the end.

## Phase 3 — Migration

**Baseline first.** Before any change, record what "working" looks like, with the one command that runs every gate:

```bash
python3 <skill-path>/scripts/gate.py <repo> --plan <plan> --report-dir <report-dir> --record-before [--only <ids>] [--extra "<cmd>"]...
```

It writes `baseline-before.json`, `references-before.json` and `secrets-redacted.txt` into `<report-dir>/restructure-evidence/`. Use the same `--only`/`--extra` on every later call. The individual scripts remain available (`baseline.py --out …`, `check_moves.py --json …`) when you need one of them alone; `gate.py` only calls them. The references file records the references that are already unresolved on the untouched tree (vendored demo pages, dead links): every later run gets `--before <workdir>/references-before.json`, and any reference that resolved before and does not now fails the check even when the relocation table does not explain it — that is how a move missing from the plan, or a path edit gone wrong, surfaces.

Record this baseline on the untouched working tree, before creating the branch or at least before any move. If you must record it later, `baseline.py <repo> --ref <original-branch> --out <workdir>/baseline-before.json` runs the checks on that ref in a temporary worktree, with a caveat: a worktree has no `node_modules`, no virtualenv, and an editable install (`pip install -e .`) keeps pointing at the working tree, so the worktree's tests may import the working tree's code. Say in the validation report when `--ref` was used. Never record "before" from a tree that already contains moves.

It detects and runs the checks the repo has (tests, build, lint, type-check, `--help` of each CLI entry point), and stores exit codes and output. Compare `--list` with how the repo really runs its checks (CI files, `tox.ini`, `Makefile`, package scripts) and add anything missing with `--extra "<command>"`: a test suite the baseline does not run is invisible to the comparison. Counters (passed/failed/skipped/collected, and lint findings for ruff/flake8/eslint/mypy/phpstan) are parsed for known runners and for `--extra` commands that print the pytest-style summary; compare the rest by reading the output. Lint rules scoped by path (`per-file-ignores`, ESLint `overrides`, `# noqa` files lists) stop applying when a file moves: a rise in findings after a move is a real regression and the comparison flags it. The script refuses to compare (exit 2) when the "before" file is missing or when no check ran at all, so a typo cannot produce a green result. A check that appears only after the migration (because the migration created a config file or directory) is reported as `NEW-not-comparable`, not as an improvement: re-run it on the original with `--ref` using `--extra`. If a check already fails on the untouched repo, note it: it is not your regression, but you must not make it worse and you cannot use it as evidence. If the repo has **no tests at all**, the floor is: build passes, every entry point starts, the import graph resolves. Say so in the plan, and offer to write characterization tests for the areas the audit flagged as critical *before* migrating them — the user decides.

**Branch.** `git checkout -b restructure/<short-name>` from the current branch. Never commit to the default branch; the user reviews a diff, they don't inherit one. The audit, plan and evidence written in Phases 1–2 are committed on this branch as its first commit (`docs: architecture audit and migration plan`), never on the default branch; the validation report and its evidence are the last commit. If the reports go in a directory that a documentation build reads (Sphinx `docs/`, MkDocs, Docusaurus), run that build before and after and confirm it still succeeds and does not publish the reports by accident. `check_moves.py` skips the three `architecture-*.md` reports and the evidence folder on purpose, since they name old paths.

**Move with the ecosystem's tool when one exists, `git mv` otherwise.** Rename-aware tools (`rope`, IDE refactors, `ts-morph`/`jscodeshift`, `gofmt -r`, `cargo` module moves, IntelliJ) update references for you and are far less error-prone than regex. When you fall back to `git mv` + manual edits, do one logical group at a time.

**After every step:**

1. update imports, manifests, configs, CI, docs, and anything else the audit listed for the paths you just moved;
2. run the gates for this step — one command, three checks (relocation table and references, baseline compared with before, no secret in reports or evidence), exit 1 if any fails:
   ```bash
   python3 <skill-path>/scripts/gate.py <repo> --plan <plan> --report-dir <report-dir> --step N [--only …] [--extra …] [--allow <shim>]...
   ```
   Underneath, `check_moves.py <repo> --plan <plan> --in-progress --before <workdir>/references-before.json` reads the relocation table and greps the whole repo (including configs, YAML, Dockerfiles, Markdown) for every `From` path that should no longer be referenced. `--in-progress` skips the rows you have not reached yet (From still there, To absent) and tells you how many are pending; without it, every row of the plan must be done — that is the Phase 4 run. Every hit is either a compatibility shim you placed on purpose, or a reference you missed. There is no third category. The script also compares the relocation table with the disk: a `From` that still exists (move not done, or a shim you did not list with `--allow`), a glob `From` that still matches files, and a `To` that does not exist are reported. The script matches full paths, dotted module names, and file names with extension (which is how manifests such as `Package.swift` `exclude:` lists and `project.pbxproj` refer to files); it deliberately ignores bare identifiers in ordinary source code and prose. It also resolves relative references against the disk and lists those that no longer resolve (in the files that moved and in the files that reference them): imports in JS/TS and Python, `url()` / `@import` / `@use` / `@forward` in CSS, Sass and Less (partials and index files understood, load paths not), and `src` / `href` / `srcset` / `poster` in HTML and template files, so a leftover `import './widget'` or `url(../img/logo.png)` is caught. Three things it reports without deciding: `url()` inside Sass/Less sources and bare package entry points (they resolve against the compiled CSS or `package.json`, which it does not read — compare the compiled output, see `references/frontend-assets.md`); relative asset paths inside server-rendered templates (Twig, Blade, Smarty, Jinja…), which resolve against the page URL, not the template file — check them in a browser or through the framework's asset helper; and navigation `href`s without a file extension (`href="contact"`), which it treats as routes and never flags. Vue/Svelte/Astro single-file components get all three checks (script imports, template attributes, style urls); run it on the original commit too, since imports already broken before the migration are not yours. It does not resolve path aliases (`tsconfig` paths, webpack) or other languages' module systems, so it complements, not replaces, the ecosystem's own build check — for Xcode projects also run `scripts/pbxproj_check.py` — exit code 3 means the project uses synchronized folders and nothing could be compared: write it under "Not verified", never as a pass. The same applies to every language the import check does not resolve (Go, Rust, Java, PHP…): there the ecosystem's build or analysis tool is the verification, and if it cannot run here, the move is "not verified", with the exact command the user must run;
3. read the baseline gate's output (`baseline.py … --compare <workdir>/baseline-before.json`, run by `gate.py`): **a regression** (a check that passed before now fails, fewer tests, more skipped, more lint findings, a check that vanished) fails the gate. On a failed gate, stop, fix or `git revert` the step, and re-run before going on;
4. commit, with a message that names the step from the plan.

If a step breaks something you cannot fix cleanly within the scope of "moving things", revert that step (`git revert` or `git checkout -- .` before commit), leave the files where they were, and record it in the validation report as an exclusion. Preferring a half-finished migration that works over a complete one that doesn't is the rule from the top of this document.

**Scope discipline.** While you are in a file to fix an import, you will see things worth improving: a bad name, a long function, an obsolete comment. Leave them. Mixing behavioural edits into a structural diff makes the diff unreviewable and makes any later bug impossible to bisect. Record the ones that matter in the audit's "Additional recommendations" section (see Phase 1), where they are kept apart from the structural work.

## Phase 4 — Verification

Run the gates one last time in final mode — `gate.py <repo> --plan <plan> --report-dir <report-dir> --final [same --only/--extra/--allow]`: every row of the plan must be done, no stale reference or newly unresolved reference may remain outside the listed shims, the baseline must match (same checks, same pass/fail, ideally same output), and the secrets verify runs in `--strict` mode. Then go beyond the automated checks:

- resolve the import graph from a clean environment if you can (fresh `pip install -e .`, `npm ci`, `go build ./...`, `cargo check`) — a stale `__pycache__`/`node_modules` can mask a broken path;
- start every entry point the inventory found (CLI `--help`, `manage.py check`, server dry-run, `npm run build`);
- grep once more for every old path across the whole tree, including `.github/`, `Dockerfile*`, `*.md`;
- open the compatibility shims and confirm each one actually imports.

**Write `architecture-validation.md`** (template in `references/report-templates.md`). It opens with a **Summary for stakeholders**: 8–12 non-technical lines for a manager or product owner — what was done, why, what proves nothing broke and what could not be checked, what the team must do next, how to go back — with no paths, commands or tool names, in the chosen output language. The same section opens the last report of an `audit` or `plan` run (see the templates), so every mode ends with a page a non-developer can read. The honesty of this document is the deliverable: a "Not verified" section with real content is better than a green banner. List what you ran, what passed, what you could not test and why, and the rollback command (`git checkout <original-branch>` and the branch/PR to discard).

**Publishing is a separate decision, never a default.** Do not push and do not open a PR/MR on your own, even when the host and tooling would allow it. At the end of Phase 4, ask the user (AskUserQuestion when available): keep the branch local (default), push the branch, or push and open a PR/MR. If nobody can answer, keep it local and print the exact commands (`git push -u origin <branch>`, `gh pr create` / `glab mr create`). When the user does ask for a PR/MR, the body is the conversation summary below plus links to the three reports.

**Conversation summary**, under ten lines: what moved, why the new shape is easier to work in, which checks passed, what remains unverified or excluded, where the branch/PR is.

## Safeguards and why each one exists

- **Never modify the default branch directly.** The user must be able to look at the diff and say no.
- **Never delete code because it looks unused.** Grep finds static references; it does not find reflection, plugin discovery, external callers, or a cron job on a server. Flag dead-code candidates in the audit and leave removal to a separate, deliberate task.
- **Never change business logic in this skill.** A structural PR must be reviewable as "nothing changed but locations". If a move truly requires a logic change (e.g. a circular import can only be broken by a lazy import), make it minimal, isolate it in its own commit, and call it out in the validation report.
- **Never assume a move is harmless.** The audit's list of path-pinning integration points exists because every one of them is a place a `git mv` breaks silently. Check each before moving the file it points to.
- **Never bypass a failing check.** Skipping a test, loosening a linter rule, or adding `# noqa` to make the baseline green hides the exact signal you need.
- **Never claim full safety.** Tests cover what someone thought to test. Report "verified by …" and "not verified" and let the user decide how much risk they accept.
- **Always keep a rollback path.** One branch, atomic commits per step, original branch untouched. State the rollback command in the validation report.
- **Always preserve public contracts** (exports, import paths, CLI commands, resource locations, URLs): someone you can't see depends on them. Shim, alias or keep in place.
- **If a change cannot be validated, exclude it** and say so. The user can authorize it explicitly if they want it anyway.

## What not to touch

Directories the inventory script marks as excluded: VCS metadata, dependency directories (`node_modules`, `vendor`, `.venv`, `target`), build outputs (`dist`, `build`, `out`), generated code, caches, and anything matching `.gitignore`. Beyond that, exclusions are project-specific: ask about `migrations/`, `legacy/`, fixtures with hard-coded paths, and anything the README says is frozen.

## References

Load the ones that apply; each covers how imports and paths resolve, idiomatic layouts, path-pinning files to check, refactoring tools, and the ecosystem's usual traps.

| File | Covers |
|---|---|
| `references/python.md` | packages, `src/` layout, pyproject/setup, Django/Flask/FastAPI, pytest, rope |
| `references/javascript-typescript.md` | ESM/CJS resolution, `package.json` exports, tsconfig paths, monorepos, Next/Nest/Express, Vite/webpack |
| `references/go.md` | modules, `internal/`, `cmd/`, `pkg/`, gopls rename |
| `references/rust.md` | crates, workspaces, `mod` tree, `[[bin]]`, cargo tooling |
| `references/java-kotlin.md` | packages, Maven/Gradle modules, Spring component scan, IntelliJ refactors |
| `references/php.md` | PSR-4 autoload, Composer, Laravel/Symfony conventions |
| `references/swift.md` | SwiftPM targets, Xcode `project.pbxproj`, plists/entitlements, lproj, macOS-only verification |
| `references/frontend-assets.md` | CSS/Sass/Less `url()` and `@import`/`@use` resolution, HTML and template `src`/`href`, asset folders, fonts, Tailwind `content` globs, compiled template caches, static-site crawl check |
| `references/report-templates.md` | the three report templates |

For a language not listed, apply the same four questions (how do imports resolve, which files pin paths, what is the idiomatic layout, which tool renames safely) and say in the audit that no ecosystem reference was available.
