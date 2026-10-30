# ChatGPT and Codex compatibility

This repository keeps `code-restructure/` **unchanged** as the canonical
Claude skill. The OpenAI distribution is generated from it, not maintained as a
second independent fork.

## Build the plugin

From the repository root (Python 3; no extra Python dependencies):

```bash
python3 tools/build_openai.py --zip
```

The command writes:

```text
dist/openai/code-restructure/
  plugin.json
  skills/code-restructure/
    SKILL.md
    references/
    scripts/
dist/openai/code-restructure.zip
```

`plugin.json` is a portable Agent Plugins manifest. No MCP server is required
for this skill-only package. The generated OpenAI `SKILL.md` contains a short
host-specific adapter, followed by the complete source skill body. References
and scripts are copied byte for byte.

## Installation and use

- **Claude Code / Claude.ai:** keep using `code-restructure/` and the existing
  installation instructions in the root README. Nothing changes.
- **ChatGPT:** install or import the generated plugin via a supported plugin
  installation surface, depending on your account/workspace capabilities.
  Select the bundled skill, or ask for a repository architecture audit.
- **Codex CLI / desktop:** install the plugin if supported by the selected
  surface. For local Skills usage without a plugin, copy the generated
  `skills/code-restructure/` directory to `~/.agents/skills/code-restructure/`
  (or `<your-repo>/.agents/skills/code-restructure/`). Invoke via the skills
  picker or mention `$code-restructure` where supported.

## Execution modes

| Mode | Needed | Result |
| --- | --- | --- |
| audit | Read access to repository files | Evidence-based architecture report |
| plan | Read access to relevant source/configuration | Audit plus migration plan |
| full | Writable checkout, Git, terminal, Python 3, build/test tools | Branch, guarded moves, before/after validation |

A connected GitHub repository viewed through ChatGPT Chat is **not** by itself
an executable checkout. If terminal or Git write capabilities are missing, the
generated Skill must not claim to have performed `full`. Use Codex or an
appropriately authorized ChatGPT Work environment for real migrations.

Approval for Phase 3 and approval for publishing/pushing are separate. The
original skill's safeguards and report language options remain in force.

## Maintenance and validation

Edit the canonical `code-restructure/` folder for shared workflow changes.
Edit `packaging/openai/adapter.md` for OpenAI-specific instructions and
`packaging/openai/plugin.json` for plugin metadata. Rebuild after either.

```bash
python3 -m unittest discover -s tests -v
python3 tools/build_openai.py --zip
```

Tests assert the generated Skill contains the entire canonical body; scripts
and references are identical byte-for-byte. The CI runs the package build
alongside the existing Claude tests. A passing CI verifies packaging and Python
checks; it **does not** replace separate acceptance tests inside real Claude,
ChatGPT, and Codex hosts.

Documentation:
- OpenAI Skills: https://learn.chatgpt.com/docs/build-skills
- Portable plugin packaging: https://developers.openai.com/plugins/build/plugins
