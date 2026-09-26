---
name: test-driven-fixer
description: >-
  Workflow for fixing bugs or behavior gaps using test-driven development in this
  repo: write the failing test that reproduces the problem first, implement the
  minimal fix, then confirm the full suite stays green. Use when fixing a bug that
  can be expressed as a test, or when asked to add regression coverage with the fix.
---

# Test-Driven Fixer Skill

Use this skill for bug fixes and behavior gaps. The test is the specification:
if the expected behavior cannot be written as a test, the requirement is not yet
understood — return to [issue-analyzer](../issue-analyzer/SKILL.md).

## 1. Write the failing test first

- Locate the matching test file in `tests/` (`test_<area>.py`). Test data setup
  is done in `tests/conftest.py` (autouse env mocks reset
  `settings_module._settings_instance`; `DEFAULT_AGENT=opencode`).
- Express the bug: `Given <state>, when <action>, then <expected>`.
- Run only that test and confirm it FAILS for the right reason
  (`py -m pytest tests/test_<area>.py -k <name>`).

## 2. Implement the minimal fix

- Change the smallest slice that makes the test pass, following repo conventions
  (async, no comments unless asked, ruff config in `pyproject.toml`).
- If the fix must spawn subprocesses or hit GitHub network, mock them like the
  other tests (`pytest-mock`, no real subprocess/network).
- Re-run the single test until green.

## 3. Confirm no regressions

- Run the full suite: `py -m pytest`
- Run `py -m ruff check .` and `py -m mypy app` (config in `pyproject.toml`).

## 4. When NOT to use this skill

- Feature work spanning multiple files: use
  [implementation-planner](../implementation-planner/SKILL.md) first.
- Failure is a timeout/hang/flake with unclear cause: use
  [systematic-debugger](../systematic-debugger/SKILL.md) to find the cause,
  then come back here to add the regression test.

See [references/tdd_workflow.md](./references/tdd_workflow.md) for a worked
example using this repo's mocks.