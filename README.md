# Code Restructure

Skill Claude (`/restructure`) : audit d'architecture et refactoring structurel sûr d'un dépôt Git existant, comportement inchangé.

Modes : `audit` (analyse), `plan` (analyse + conception), `full` (analyse, conception, migration sur branche, vérification).

## Contenu

- `code-restructure/SKILL.md` — le skill (obligatoire)
- `code-restructure/references/` — une fiche par écosystème + modèles de rapports
- `code-restructure/scripts/` — `inventory.py`, `baseline.py`, `check_moves.py`, `pbxproj_check.py` (Python 3, sans dépendance)
- `code-restructure/evals/` — cas de test et critères de notation (facultatif, non nécessaire à l'exécution)

## Installation

Claude.ai / Cowork : zipper le dossier `code-restructure/` en `code-restructure.skill` et l'importer dans Réglages > Capacités > Skills.
Claude Code : copier `code-restructure/` dans `~/.claude/skills/` (ou `.claude/skills/` d'un projet).

## Licence

MIT — © 2026 Pluristack. Voir `LICENSE`.
