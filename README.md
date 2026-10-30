# Code Restructure

A Claude skill (`/restructure`) and/or ChatGPT/Codex plugin for architecture audits and safe structural refactoring of an existing Git repository, with behaviour left unchanged.

## Modes

- `audit`: analyse the current structure (layout, dependencies, path-pinning files, findings).
- `plan`: audit, then design a target layout with an exhaustive relocation table and a migration sequence.
- `full`: audit, plan, then migrate on a dedicated branch (atomic commits) and verify before/after.

Outputs: an audit report, a migration plan and a validation report, written in the language you choose (English, French, Spanish, German, Italian, Portuguese, Estonian, Russian, Ukrainian, Chinese or Arabic; the skill asks). Nothing is pushed unless you ask.

## Supported ecosystems

Python, JavaScript/TypeScript, Go, Rust, Java/Kotlin, PHP, Swift/Xcode. Other languages use the same four questions (import resolution, path-pinning files, idiomatic layout, safe-rename tooling).

## Contents

- `code-restructure/SKILL.md`: the skill
- `code-restructure/references/`: one guide per ecosystem (Python, JS/TS, Go, Rust, Java/Kotlin, PHP, Swift/Xcode), front-end assets (CSS/Sass/Less, HTML templates, asset folders) and the report templates
- `code-restructure/scripts/` (Python 3, no dependencies):
  - `gate.py`: runs every verification gate in one command (`--record-before`, `--step N`, `--final`); the entry point the skill uses during a migration
  - `inventory.py`: tree, languages, path-pinning files, entry points
  - `baseline.py`: detects and runs the repo's checks, compares before/after
  - `check_moves.py`: stale references, relocation table vs disk, unresolved relative imports and asset paths
  - `secrets_scan.py`: lists hard-coded secrets without their values; verifies reports and evidence leak none
  - `pbxproj_check.py`: Xcode project file vs disk
- `tests/`: unit and end-to-end tests (Python, TypeScript with `tsc`, Sass with `sass`), run by GitHub Actions

## Installation

- **Claude.ai / Cowork:** zip the `code-restructure/` folder as `code-restructure.skill` and import it in Settings > Capabilities > Skills.
- **Claude Code:** copy `code-restructure/` into `~/.claude/skills/` (or `.claude/skills/` in a project).


## ChatGPT and Codex

The existing Claude skill stays unchanged. Generate a skill-only OpenAI plugin
from the same scripts and references:

```bash
python3 tools/build_openai.py --zip
```

The plugin is created in `dist/openai/code-restructure/`, with a ZIP beside it.
You can also install its generated Skill in Codex under
`~/.agents/skills/code-restructure/`. In ChatGPT, use a supported plugin
installation surface. The `full` mode requires a writable repository checkout
and a terminal; reading files via a connector alone supports only `audit` and
`plan`. See [OpenAI compatibility](docs/openai-compatibility.md).

## Usage

Type `/restructure` in a conversation with a repository attached or checked out, or ask for an architecture audit or a structural cleanup.

## License

MIT, © 2026 Pluristack. See `LICENSE`.
````

