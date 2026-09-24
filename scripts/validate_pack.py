#!/usr/bin/env python3
"""Validate portable Agent Skills structure without third-party dependencies."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
ABSOLUTE_PRODUCT_PATH = re.compile(r"/opt/[A-Za-z0-9._-]+")
CLIENT_SPECIFIC_CORE = re.compile(
    r"(?:Codex|Claude Code|OpenCode|\.codex(?:/|\b)|\.claude(?:/|\b)|opencode/skills)",
    re.IGNORECASE,
)


def frontmatter(text: str, path: Path, errors: list[str]) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        errors.append(f"{path}: missing opening YAML frontmatter")
        return {}
    try:
        end = lines.index("---", 1)
    except ValueError:
        errors.append(f"{path}: missing closing YAML frontmatter")
        return {}
    values: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" not in line or line.startswith((" ", "\t")):
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main() -> int:
    errors: list[str] = []
    skill_dirs = sorted(path for path in SKILLS.iterdir() if path.is_dir()) if SKILLS.exists() else []
    if not skill_dirs:
        errors.append("no skill directories found")

    for skill_dir in skill_dirs:
        manifest = skill_dir / "SKILL.md"
        if not manifest.is_file():
            errors.append(f"{skill_dir}: missing SKILL.md")
            continue
        text = manifest.read_text(encoding="utf-8")
        meta = frontmatter(text, manifest, errors)
        if meta.get("name") != skill_dir.name:
            errors.append(
                f"{manifest}: name {meta.get('name')!r} must match directory {skill_dir.name!r}"
            )
        if not meta.get("description"):
            errors.append(f"{manifest}: description is required")

        for path in skill_dir.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".md", ".json", ".yaml", ".yml", ".py"}:
                continue
            content = path.read_text(encoding="utf-8")
            match = ABSOLUTE_PRODUCT_PATH.search(content)
            if match:
                errors.append(f"{path}: contains product-specific absolute path {match.group(0)!r}")
            if "agents" not in path.relative_to(skill_dir).parts:
                match = CLIENT_SPECIFIC_CORE.search(content)
                if match:
                    errors.append(
                        f"{path}: portable core contains client-specific dependency {match.group(0)!r}"
                    )
            if path.suffix.lower() == ".md":
                for raw_target in LINK.findall(content):
                    target = raw_target.split("#", 1)[0]
                    if not target or target.startswith(("http://", "https://", "mailto:", "/")):
                        continue
                    resolved = (path.parent / target).resolve()
                    if not resolved.exists():
                        errors.append(f"{path}: broken relative link {raw_target!r}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"Validated {len(skill_dirs)} portable skill(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
