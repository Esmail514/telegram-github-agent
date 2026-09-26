---
name: skill-maintainer
description: >-
  Guidelines for creating, editing, renaming, or reviewing skills in this project's
  .agents/skills/ directory: frontmatter contract, unique non-overlapping descriptions
  that drive triggering, progressive disclosure, reference validity, and coordination
  with AGENTS.md routing and the other skills. Use when asked to add, modify, or
  critique a skill or its routing — do not use this skill for normal bot/issue work.
---

# Skill Maintainer Skill

Use when creating, updating, or reviewing the skills in `.agents/skills/` and
their routing in `AGENTS.md`. Keep the collection coherent.

## 1. Create or edit a skill

- **Location**: `.agents/skills/<kebab-case-name>/SKILL.md` with YAML frontmatter:
  - `name`: unique kebab-case matching the directory name.
  - `description`: unlike every other skill; short trigger-oriented text starting
    with what it's for and when to use it.
- **Progressive disclosure**: keep `SKILL.md` scannable (bounded, sections +
    numbered steps); put depth in `references/`, deterministic helpers in
    `scripts/`.
- **Structure conventions** used across this repo: execution architecture,
    safety/termination notes, "when NOT to use", verification commands
    (`py -m pytest` / `py -m ruff check .` / `py -m mypy app`).

## 2. Guardrails

- **Never delete, rename, or replace** the three existing skills
  (telegram-bot-expert, github-agent-orchestrator, antigravity-coding-agent)
  or their `references/`.
- **No overlapping triggers**: descriptions must route to exactly one skill.
  Check for near-duplicate `description` phrasing with the existing set before
  saving.
- **All links/locations must resolve**: every `../skills/.../SKILL.md` reference
  and `references/` path must exist (run `scripts/validate_skills.py`).
- **Routing not duplication**: `AGENTS.md` routes between skills; it must not
  restate a skill's body. Update the routing table and the relevant skill's
  "when NOT to use" sections together when responsibilities move.

## 3. Validate

Run the deterministic structural validator from the repo root:

```powershell
py scripts/validate_skills.py
# or, for this skill set only:
py .agents/skills/skill-maintainer/scripts/validate_skills.py
```

It checks: one `SKILL.md` per dir, valid frontmatter (`name` matches dir,
non-empty `description`), unique `name`s/`description`s across the set, and
internal `./references/...` links resolve. It exits non-zero on any violation.

## 4. When NOT to use

Not for debugging bots, GitHub ops, or Telegram work — hand those to the
domain skills. This skill exists to keep the skill library trustworthy.