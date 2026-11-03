# Code Restructure

The vocabulary of the skill, its reports and its scripts. A run audits a repository, plans a better structure, moves files and shows what it verified about behaviour staying the same.

## Language

### The run

**Mode**:
How much of the workflow a run covers: `audit` (Phase 1), `plan` (Phases 1–2) or `full` (Phases 1–4).

**Phase**:
One numbered stage of the workflow: 0 Setup, 1 Analysis, 2 Design, 3 Migration, 4 Verification.

**Step**:
One group of moves from the migration sequence. It is committed on its own and leaves the repository working.

**Host**:
The product running the skill, such as Claude Code, claude.ai, ChatGPT or Codex.

### The plan

**Relocation table**:
The exhaustive, machine-readable `From | To | Note` table in the migration plan, read by `check_moves.py`.
_Avoid_: From|To table

**Split**:
One module becoming several, shown by a `From` on several rows of the relocation table. It is the one structural change that touches file contents.

**Path-pinning integration point**:
A place that depends on a file's path, such as a manifest, a CI job, a dynamic import or a runtime file reference. A move can break it silently.

**Public contract**:
Anything callers outside the repository depend on: exports, import paths, CLI commands, resource locations, URLs. It must keep working after a move.
_Avoid_: public surface, public path

**Compatibility shim**:
A re-export, manifest alias or symlink that keeps an old public path working. Each one carries a comment saying where the real code lives, and removing it is the user's call.
_Avoid_: forwarding shim, forwarding module, compatibility layer

### The verification

**Baseline**:
The recorded results of the repository's own checks, taken before any move and compared after each step.

**Floor**:
The minimum set of checks built for a repository whose own checks cannot protect a migration: build, entry points, import resolution.

**Stale reference**:
A remaining mention of a `From` path that is not a listed compatibility shim.

**Unresolved reference**:
A relative import or asset path that does not resolve on disk. Only those that resolved before the migration count as regressions.
_Avoid_: broken reference

**Not verified**:
The label for anything a run could not check. A clean result is reported as "verified by X".
_Avoid_: safe, fully safe

### The gates

Plain "gate" is ambiguous. Always use one of these three terms.

**Verification gate**:
An automated check run by `gate.py` before any move, after each step and at the end. A failed step is fixed or reverted before the migration goes on.
_Avoid_: gate

**Approval gate**:
The user's decision after Phase 2, before any file moves. A request such as "just do it" or `--no-gate` skips it, but the plan is still written.
_Avoid_: gate

**Publishing gate**:
The user's decision before any push, PR or MR. Nothing skips it, and with no answer the branch stays local.
_Avoid_: gate

### The outputs

**Report**:
One of the three documents a run produces: `architecture-audit.md`, `architecture-migration-plan.md`, `architecture-validation.md`.

**Evidence folder**:
`<report-dir>/restructure-evidence/`, where script results and cited tool output are saved. `<workdir>` in `SKILL.md` is a placeholder for this folder.
_Avoid_: workdir

**Finding**:
A structural problem recorded in the audit, ranked by risk to maintainability.

**Additional recommendation**:
A confirmed issue beyond structure, kept apart from the findings and the migration plan. The skill never carries it out.

**Summary for stakeholders**:
The non-technical opening of a run's last report, with no paths, commands or tool names.

### The distribution

**Canonical skill**:
The `code-restructure/` folder, the single source for every host.

**OpenAI plugin**:
The ChatGPT and Codex package generated from the canonical skill plus `packaging/openai/`.
_Avoid_: OpenAI distribution, fork

**Ecosystem reference**:
The guide in `code-restructure/references/` for one language or for front-end assets, loaded according to what the repository contains.
