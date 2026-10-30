# Code Restructure

A Claude skill (`/restructure`) for architecture audits and safe structural refactoring of an existing Git repository, with behaviour left unchanged.

## Modes

- `audit`: analyse the current structure (layout, dependencies, path-pinning files, findings).
- `plan`: audit, then design a target layout with an exhaustive relocation table and a migration sequence.
- `full`: audit, plan, then migrate on a dedicated branch (atomic commits) and verify before/after.

Outputs: an audit report, a migration plan and a validation report, written in English or French (the skill asks which). Nothing is pushed unless you ask.

## Supported ecosystems

Python, JavaScript/TypeScript, Go, Rust, Java/Kotlin, PHP, Swift/Xcode. Other languages use the same four questions (import resolution, path-pinning files, idiomatic layout, safe-rename tooling).

## Contents

- `code-restructure/SKILL.md`: the skill
- `code-restructure/references/`: one guide per ecosystem and the report templates
- `code-restructure/scripts/`: `inventory.py`, `baseline.py`, `check_moves.py`, `pbxproj_check.py` (Python 3, no dependencies)

## Installation

- **Claude.ai / Cowork:** zip the `code-restructure/` folder as `code-restructure.skill` and import it in Settings > Capabilities > Skills.
- **Claude Code:** copy `code-restructure/` into `~/.claude/skills/` (or `.claude/skills/` in a project).

## Usage

Type `/restructure` in a conversation with a repository attached or checked out, or ask for an architecture audit or a structural cleanup.

## License

MIT, © 2026 Pluristack. See `LICENSE`.
````

