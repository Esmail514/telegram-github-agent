---
name: implementation-planner
description: >-
  Workflow for turning an analyzed problem into a concrete implementation plan:
  file-level change list, safe ordering, test strategy, and rollback/risk checkpoints,
  matched to this repo's layout and conventions. Use after issue-analyzer and before
  writing code, when you need a plan of record for what will change and in what order.
---

# Implementation Planner Skill

Use this skill after analysis (see [issue-analyzer](../issue-analyzer/SKILL.md))
and before writing code. Produces a plan of record so changes are reviewable and
reversible, matching the patterns this repo already uses.

## 1. Produce a plan with these sections

- **Change list**: one bullet per file `app/.../file.py`, stating what changes
  and, if it is a Telegram handler/git/github-facing change, which entry point
  triggers it.
- **Order of work**: implement in dependency order, e.g. config → services →
  handlers → keyboards → tests. Never edit `settings.py` without noting .env
  side effects (see Section 4).
- **Test strategy**: which existing `tests/` file covers the area, which new
  assertions to add, and the exact command to run
  (`py -m pytest tests/test_<area>.py`; project uses asyncio_mode=auto).
- **Risk checkpoints**: what to check before moving to the next step (e.g. "all
  tests in the file pass before touching the handler").
- **Rollback**: how to revert (single commit / git revert on the branch).

## 2. Follow repo conventions

- Async everywhere: handlers, github services, and runner use `async def`
  + `asyncio` (aiosqlite, subprocess via `asyncio.create_subprocess_exec`).
  Blocking PyGithub/httpx calls are offloaded via `run_in_executor`.
- No comments unless asked; match existing style (PEP8, ruff config in
  `pyproject.toml`: line-length 100, ignore B008/E501).
- Keep changes minimal and additive; do not restructure modules unless the plan
  explicitly says so.

## 3. When NOT to plan

- Bug fix with an obvious one-file change: go straight to
  [test-driven-fixer](../test-driven-fixer/SKILL.md).
- Debugging an unclear failure: use
  [systematic-debugger](../systematic-debugger/SKILL.md) first, then return here.

## 4. Security-aware planning

- If the plan touches file access, subprocess execution, webhooks, or reading
  untrusted external content, review the [trust boundary](../../../AGENTS.md#trust-boundary)
  and route the change through
  [security-reviewer](../security-reviewer/SKILL.md) before finalizing.
- Flag every environment variable/secret touched so verification can confirm
  nothing is logged or committed.

See [references/planning_heuristics.md](./references/planning_heuristics.md)
for graded question/skip heuristics.