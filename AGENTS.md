# Code Restructure

A skill (`/restructure`) that audits a repository's architecture and moves files without changing behaviour. Terms: [GLOSSARY.md](GLOSSARY.md).

## Where things live

- `code-restructure/`: the canonical skill. `SKILL.md` is the workflow, `references/` holds the ecosystem references and report templates, `scripts/` holds the tools the workflow runs.
- `packaging/openai/` and `tools/build_openai.py`: generate the OpenAI plugin into `dist/openai/`, which is git-ignored. See [docs/openai-compatibility.md](docs/openai-compatibility.md).
- `tests/`: unit and end-to-end tests.
- `sample-report/`: reports from a real run on another repository, kept as examples.

## Commands

Run from the repository root.

- Tests: `python3 -m unittest discover -s tests -v`. The Sass end-to-end tests skip without `sass` and the TypeScript ones without `tsc`; CI installs both.
- Lint: `ruff check --select E9,F code-restructure/scripts tests`
- Plugin build: `python3 tools/build_openai.py --zip`

## Rules

- `code-restructure/scripts/` uses the Python standard library only and must run on Python 3.9, because users run these scripts with no install step.
- Make shared workflow changes in `code-restructure/`. OpenAI-only changes go in `packaging/openai/adapter.md` (instructions), `packaging/openai/plugin.json` (metadata) or `OPENAI_METADATA` in `tools/build_openai.py` (the skill description). Never edit `dist/`.
- When a script's flags, outputs or exit codes change, update `SKILL.md` in the same change: the workflow calls them by name.
- Passing CI covers the scripts and the packaging, not how an agent follows `SKILL.md` or the references. When you change those files, say in the PR what you verified and how.
- Documentation uses British spelling ("behaviour").
