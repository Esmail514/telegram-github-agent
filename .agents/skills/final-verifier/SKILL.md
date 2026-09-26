---
name: final-verifier
description: >-
  Pre-completion verification gate for this project: run the real commands
  (pytest, ruff, mypy), check git status/diff for secrets and stray files, and
  confirm acceptance criteria are met before a commit or PR is made. Use as the
  last step before wrapping up any code task, after change-impact-analyzer and
  security-reviewer have approved.
---

# Final Verifier Skill

Run this immediately before committing/PR-ing any code change. If anything
fails, return to the appropriate skill (fix → test-driven-fixer, unsure cause →
systematic-debugger, security → security-reviewer) and repeat this gate.
Never claim completion without running the real commands.

## 1. Verify with the real commands

Use the exact commands CI runs (`.github/workflows/ci.yml`) plus the project's
config (`pyproject.toml`):

- `py -m pytest` (asyncio_mode=auto, testpaths=tests) — full green.
- `py -m ruff check .` — clean (line-length 100; ignores B008/E501).
- `py -m mypy app` — no new type errors (python_version 3.11, strict=false).
- If a tools/platform mismatch exists, report the exact error and mark the
  check as blocked; do not "soft-pass".

## 2. Check the working tree

- `git status --short` — only intended files staged/modified.
- `git diff --stat` + `git diff` review: no secrets, no `agent.db`, no `.env`,
  no temp/scratch files, no generated assets accidentally committed.
- No changes to `app/*` outside the task scope (production code changes are
  out of scope for skill work).

## 3. Confirm acceptance criteria

Re-read the analysis and plan from
[issue-analyzer](../issue-analyzer/SKILL.md) and
[implementation-planner](../implementation-planner/SKILL.md): every criterion
is demonstrably met (ideally by a test), unknowns resolved, rollback known.

## 4. Output

Produce a checklist:

- [ ] `py -m pytest` passed (note count)
- [ ] `py -m ruff check .` passed
- [ ] `py -m mypy app` passed
- [ ] `git status`/`git diff` clean of secrets/strays, scope respected
- [ ] Acceptance criteria met
- Verdict: **APPROVED** / **REVISION REQUIRED** (list the failing item).