#!/usr/bin/env python3
"""Build an OpenAI ChatGPT/Codex skill-only plugin from the canonical Claude skill.

Only the generated package receives the OpenAI adapter. The source skill, scripts,
and references are never modified.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "code-restructure"
PLUGIN_TEMPLATE = ROOT / "packaging" / "openai" / "plugin.json"
ADAPTER = ROOT / "packaging" / "openai" / "adapter.md"
SKILL_NAME = "code-restructure"
FRONTMATTER = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", re.S)
OPENAI_METADATA = (
    "---\n"
    "name: code-restructure\n"
    "description: Audit and plan safe structural refactoring of an existing Git repository. "
    "Use for architecture audits, file and module moves, and behaviour-preserving "
    "repository restructuring. Do not use for feature implementation or logic rewrites.\n"
    "---\n"
)


def skill_for_openai(source_text: str, adapter_text: str) -> str:
    """Preserve the source body exactly, adding a host-specific preamble."""
    found = FRONTMATTER.match(source_text)
    if not found:
        raise ValueError("Canonical SKILL.md is missing valid YAML frontmatter")
    if "name: code-restructure" not in found.group(0):
        raise ValueError("Unexpected canonical skill name")
    if "## OpenAI runtime adapter" not in adapter_text:
        raise ValueError("OpenAI adapter is missing its heading")
    return OPENAI_METADATA + "\n" + adapter_text.rstrip() + "\n\n" + source_text[found.end():]


def build(output: Path) -> Path:
    output = output.resolve()
    if output == ROOT or output == SOURCE or SOURCE in output.parents:
        raise ValueError("Refusing to write into the canonical Claude skill")
    if output.is_symlink():
        raise ValueError("Refusing to replace a symlink")
    if output.exists():
        if not output.is_dir() or not (output / "plugin.json").is_file():
            raise ValueError("Output directory exists but is not a generated plugin")
        try:
            old = json.loads((output / "plugin.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("Refusing to overwrite an invalid plugin directory") from exc
        if old.get("name") != SKILL_NAME or not (output / "skills" / SKILL_NAME / "SKILL.md").is_file():
            raise ValueError("Refusing to overwrite a plugin with unexpected contents")
    manifest = json.loads(PLUGIN_TEMPLATE.read_text(encoding="utf-8"))
    if manifest.get("name") != SKILL_NAME or not manifest.get("$schema"):
        raise ValueError("OpenAI plugin template has invalid metadata")
    source_skill = (SOURCE / "SKILL.md").read_text(encoding="utf-8")
    adapter = ADAPTER.read_text(encoding="utf-8")

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="openai-plugin-") as temp:
        staged = Path(temp) / "plugin"
        staged_skill = staged / "skills" / SKILL_NAME
        shutil.copytree(
            SOURCE,
            staged_skill,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
        )
        (staged_skill / "SKILL.md").write_text(
            skill_for_openai(source_skill, adapter),
            encoding="utf-8",
        )
        (staged / "plugin.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        if output.exists():
            shutil.rmtree(output)
        shutil.move(str(staged), str(output))
    return output


def archive(output: Path) -> Path:
    target = output.with_suffix(".zip")
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for file in sorted(output.rglob("*")):
            if file.is_file():
                z.write(file, arcname=(Path(output.name) / file.relative_to(output)).as_posix())
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "dist" / "openai" / SKILL_NAME,
        help="Destination plugin directory (default: dist/openai/code-restructure)",
    )
    parser.add_argument("--zip", action="store_true", help="Also create an installable plugin ZIP")
    args = parser.parse_args()
    try:
        output = build(args.output)
        print(f"OpenAI plugin: {output}")
        if args.zip:
            print(f"ZIP: {archive(output)}")
    except (OSError, ValueError) as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
