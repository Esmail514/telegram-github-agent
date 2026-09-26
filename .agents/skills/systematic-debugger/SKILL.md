---
name: systematic-debugger
description: >-
  Structured debugging workflow for failures in this project: failing tests,
  agent subprocess timeouts or hangs, Telegram callback errors, flaky behavior,
  and scheduler issues. Use ONLY when something is broken and the cause is
  unknown: reproduce, isolate, hypothesize, verify, fix, then confirm no
  regressions. Do not use for feature work, planning, or analysis.
---

# Systematic Debugger Skill

Invoked when tests fail, a job agent times out, a callback errors, a scheduler
job misfires, or behavior is flaky. Goal: a confirmed root cause + a regression
test, in that order — never "fix by feel".

## 1. Reproduce

- Get the exact failing signal: the pytest command and failure output, the
  Telegram callback error, the subprocess timeout message, or the scheduler log.
- If you cannot reproduce, state that and note the missing trigger — do not
  guess a cause.

## 2. Isolate

Narrow the blast radius by answering all that apply:

- Is it in a **handler** (`app/telegram/handlers/*`)? → single-handler test.
- Is it in **execution** (`app/runner/*` or an `app/agents/*` backend)? →
  exercise the service directly, mock the subprocess/git/network boundary.
- Is it in **scheduler** (`app/runner/scheduler.py`,
  `app/database/schedule_repository.py`)? → drive the repo layer directly.
- Is it in **settings/config** (`app/config/settings.py`)? → check env vars and
  `py -m app.main --check-config`.

## 3. Hypothesize, verify, fix

1. Form one hypothesis with a falsifiable check.
2. Verify it with a minimal experiment (a focused test, a direct service call).
3. Fix the smallest slice.
4. Add a regression test via [test-driven-fixer](../test-driven-fixer/SKILL.md).

## 4. Confirm no regressions

- `py -m pytest`
- `py -m ruff check .` and `py -m mypy app`

## 5. When NOT to use this skill

- Clear bug with a known cause → straight to
  [test-driven-fixer](../test-driven-fixer/SKILL.md).
- Investigating a PR/Git/GitHub process failure → cause first here, mechanics
  via [github-agent-orchestrator](../github-agent-orchestrator/SKILL.md).

## Known failure modes (this project)

- **Agent subprocess hang/timeout** → [runbooks/agent_timeouts.md](./references/runbooks/agent_timeouts.md)
- **Telegram callback_error / inline-keyboard failures** → [runbooks/callback_failures.md](./references/runbooks/callback_failures.md)
- **Scheduler job misfire / duplicate fire** → [runbooks/scheduler_issues.md](./references/runbooks/scheduler_issues.md)
- **Flaky or environment-dependent tests** → [runbooks/flaky_tests.md](./references/runbooks/flaky_tests.md)