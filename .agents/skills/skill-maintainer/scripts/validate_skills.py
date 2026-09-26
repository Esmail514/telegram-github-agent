#!/usr/bin/env python3
"""Validate the skill collection under .agents/skills/.

Checks (stdlib only, no third-party deps):
  1. Every subdirectory has a SKILL.md file.  (mandatory)
  2. SKILL.md starts with YAML frontmatter (--- delimited).  (mandatory)
  3. frontmatter has `name` equal to the directory name and a non-empty
     `description`.  (mandatory)
  4. `name` values are unique across the set.  (mandatory)
  5. Non-empty `description` values are unique across the set.  (mandatory)
  6. Internal links starting with `./` or `../` inside SKILL.md resolve to a
     real file relative to the SKILL.md location.  (warn-level)
  7. At least one SKILL.md file exists.  (mandatory)

Exit code 0 = all mandatory checks pass; non-zero otherwise.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve()
# locate the .agents/skills dir by walking up from this script
while not (REPO_ROOT / ".agents" / "skills").is_dir() and REPO_ROOT != REPO_ROOT.parent:
    REPO_ROOT = REPO_ROOT.parent
SKILLS_DIR = REPO_ROOT / ".agents" / "skills"
# allow SKILLS_DIR override via first CLI arg
if len(sys.argv) > 1:
    SKILLS_DIR = Path(sys.argv[1]).resolve()


def parse_frontmatter(path: Path) -> dict[str, str] | None:
    """Parse a minimal YAML-style frontmatter block (name: / description:)."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    lines = text.splitlines()[1:]
    meta: dict[str, str] = {}
    key: str | None = None
    accumulator: list[str] = []
    for line in lines:
        if line == "---":
            break
        if not line.startswith(" ") and ":" in line:
            if key and accumulator:
                meta[key] = " ".join(accumulator).strip()
            key, _, raw = line.partition(":")
            key = key.strip()
            accumulator = [raw.strip()]
        elif line.startswith("  ") or line.startswith("\t"):
            if key:
                accumulator.append(line.strip())
    if key and accumulator:
        meta[key] = " ".join(accumulator).strip()
    return meta


def main() -> int:
    if not SKILLS_DIR.is_dir():
        print(f"::error:: skills dir not found: {SKILLS_DIR}")
        return 1

    skill_dirs = sorted(d for d in SKILLS_DIR.iterdir() if d.is_dir())
    if not skill_dirs:
        print("::error:: no skill directories found")
        return 1

    errors: list[str] = []
    warnings: list[str] = []
    names: list[str] = []
    descriptions: list[str] = []

    for d in skill_dirs:
        skill_md = d / "SKILL.md"
        if not skill_md.is_file():
            errors.append(f"{d.name}: missing SKILL.md")
            continue

        meta = parse_frontmatter(skill_md)
        if meta is None:
            errors.append(f"{d.name}: missing or invalid YAML frontmatter")
            continue

        name = meta.get("name", "").strip()
        desc = meta.get("description", "").strip()
        if name != d.name:
            errors.append(f"{d.name}: frontmatter name {name!r} != directory name")
        if not desc:
            errors.append(f"{d.name}: empty description")
        names.append(name or d.name)
        descriptions.append(desc)

        # warn on internal links that do not resolve
        text = skill_md.read_text(encoding="utf-8")
        for line in text.splitlines():
            for marker in ("](./", "](../"):
                idx = line.find(marker)
                while idx != -1:
                    open_paren = line.find("(", idx)
                    if open_paren == -1:
                        break
                    close_paren = line.find(")", open_paren)
                    if close_paren == -1:
                        break
                    rel = line[open_paren + 1 : close_paren].strip()
                    rel = rel.split("#", 1)[0].strip()  # drop anchor fragment
                    target = (d / rel).resolve()
                    if not target.is_file():
                        warnings.append(
                            f"{d.name}: broken link {rel!r} -> {target}"
                        )
                    idx = line.find(marker, close_paren + 1)

    # uniqueness checks
    for label, values in (("name", names), ("description", descriptions)):
        seen: dict[str, int] = {}
        for v in values:
            seen[v.lower()] = seen.get(v.lower(), 0) + 1
        for value, count in seen.items():
            if count > 1:
                errors.append(f"duplicate {label}: {value!r} ({count} skills)")

    for w in warnings:
        print(f"::warning:: {w}")
    for e in errors:
        print(f"::error:: {e}")

    if errors:
        print(f"FAIL: {len(errors)} error(s) across {len(skill_dirs)} skill(s)")
        return 1
    print(f"OK: {len(skill_dirs)} skill(s) validated "
          f"({len(warnings)} warning(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
