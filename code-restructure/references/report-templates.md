# Report templates

Write all three in the language the user chose in Phase 0 (asked explicitly; README language only if no one could answer). Keep paths, identifiers and commands verbatim. Replace `<…>` placeholders; drop a section only if it is genuinely empty, and then say "none" rather than omitting it.

---

## 1. `architecture-audit.md`

```markdown
# Architecture audit — <project name>

Date: <YYYY-MM-DD> · Commit: <short sha> · Branch: <branch>

## Summary for stakeholders
<Only when this audit is the last report of the run (audit mode). Same rules as the
validation report's stakeholder summary: non-technical, 8–12 lines, no paths or tool names.
What was examined, the two or three problems that matter in plain words, what they cost
the team today, what is recommended next and roughly how much work it is, and what was
not examined. Omit this section in plan and full modes.>

## Summary
<3–6 sentences: what the project is, how it is organized today, the two or three
structural problems that matter most, and whether a restructuring is worth doing.>

## Current layout
<Directory tree, depth 2–3, annotated. One line per entry: what lives there.
Mark entries with ⚠ when they contain something that does not belong.>

## Stack and conventions observed
- Languages / frameworks: …
- Build / packaging: …
- Tests: <runner, where tests live, rough coverage if known>
- CI: …
- Conventions already followed (keep these): …

## Dependency picture
<How the main modules relate. A short list or a Mermaid graph for ≤ 15 nodes.
Then:>
- Cycles: …
- Hub modules (imported by nearly everything): …
- Entry points: …

## Integration points that pin paths
<Every place a file path or module path is referenced outside a normal import.
These are the moves that can break silently. One row per item.>

| Where | What it references | Kind |
|---|---|---|
| pyproject.toml | packages = ["src/app"] | manifest |
| app/loader.py:42 | importlib.import_module(f"plugins.{name}") | dynamic import |
| .github/workflows/ci.yml | scripts/run_tests.sh | CI |
| README.md | raw link to docs/schema.json | docs |

## Findings
<Ordered by impact on maintainability. For each:>

### F1 — <title>
- **Where:** …
- **What:** …
- **Why it matters:** …
- **Suggested direction:** …

## Dead-code candidates (not removed)
<Files or symbols with no static reference found. Listed for the user; this skill
never deletes them.>

## Additional recommendations
<Beyond the structure: logic refactors, duplicated code, cycles, dead code,
defects, stale docs, tooling gaps. Kept apart from the migration plan and never
executed by this skill. Every entry needs evidence; unconfirmed suspicions go in
the last subsection.>

### R1 — <title>
- **Evidence:** <command + result, or file:lines>
- **Proposal:** <what would change>
- **Impact / what could go wrong:** …
- **How to verify afterwards:** …
- **Value / risk:** <high|med|low> / <high|med|low> · **Behaviour:** <preserving | changing: exactly what>

### Suspected, not confirmed
- <suspicion> — would be settled by: <check>

## Recommended changes, prioritized
1. <change> — impact <high/med/low>, risk <high/med/low>
2. …
```

---

## 2. `architecture-migration-plan.md`

```markdown
# Migration plan — <project name>

Based on: architecture-audit.md (<date>)

## Summary for stakeholders
<Only when this plan is the last report of the run (plan mode). Same rules as the
validation report's stakeholder summary: non-technical, 8–12 lines, no paths or tool names.
What would change and why, what would not change ("the program's behaviour"), the risks
and how they would be contained, what it would take (steps, rough effort), and the
decision asked of the reader. Omit this section in full mode, where the validation report
carries it.>

## Target layout
<Proposed tree, same depth as the audit's current tree, annotated.>

## Rationale
<Why this shape and not another; which audit findings each part addresses;
what was deliberately kept as is.>

## Module boundaries
<For each top-level unit: responsibility in one sentence, what it may depend on,
what must not depend on it.>

## Relocation table
<Exhaustive. One row per file or glob. The check_moves.py script parses this
table: keep the three columns and the header exactly as below.>

| From | To | Note |
|---|---|---|
| … | … | … |

## References to update
<Everything from the audit's "integration points" table that is affected by a
row above, with the planned edit.>

## Compatibility measures
<Per public path: shim / alias / kept in place, with the file that implements it.
Removal of shims is an open question for the user — list it under Decisions.>

## Migration sequence
<Numbered steps. Each step = one commit, repo working at the end of it.>
1. <step> — files: … — checks to run: …
2. …

## Validation strategy
- Baseline checks available: <from baseline.py>
- Checks that already fail before migration: …
- Gaps (areas with no coverage) and what will be done about them:
  <minimum floor / characterization tests proposed / accepted as unverified>

## Risks
| Risk | Likelihood | Mitigation |
|---|---|---|

## Decisions needed from you
- [ ] …
```

---

## 3. `architecture-validation.md`

```markdown
# Validation report — <project name>

Branch: <branch> · Commits: <first sha>..<last sha> · PR/MR: <link or "not opened">

## Summary for stakeholders
<Non-technical, 8–12 lines, written for a manager or product owner who will not read the
rest. No paths, no commands, no tool names. Cover, in this order:
- what was done, in plain words ("the project's files were reorganised into clearer
  folders; the program itself was not changed");
- why it was worth doing (what becomes easier or less risky);
- what proves nothing broke (e.g. "the same N automated tests pass before and after",
  "the application was started and its main functions checked") — and what could NOT be
  checked, said plainly ("the behaviour on the production server was not tested");
- what the team needs to do now (review, merge, deploy; any one-time action such as
  clearing a cache or updating a deployment script);
- the way back if a problem appears ("the previous version is intact and can be
  restored in minutes").
State the status in one word at the end: Verified within limits / Partial / Failed.>

## What changed
<Short prose, then the before/after trees side by side or stacked.>

## Relocations performed
<The relocation table from the plan, with a Status column: done / skipped / partial.>

## References updated
<Files edited for paths and imports, grouped by kind: imports, manifests, CI,
Docker, docs.>

## Compatibility shims in place
<File, what it forwards to, suggested removal condition.>

## Checks

| Check | Before | After |
|---|---|---|
| pytest | 212 passed | 212 passed |
| ruff | clean | clean |
| build | ok | ok |
| CLI --help (×3) | ok | ok |

## Not verified
<Explicit. E.g. "No tests cover the export pipeline; verified only that the
module imports and the CLI starts." Empty is suspicious.>

## Excluded or reverted
<Moves from the plan that were not done, and why.>

## Rollback
<Exact commands: switch back to the original branch; delete/close the branch.>

## Status
<One of: VERIFIED within the limits above / PARTIAL — see Excluded / FAILED — see …>
```
