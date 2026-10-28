---
name: telegram-controller-project-guide
description: >-
  Navigate this Telegram Controller Bot's architecture and data ownership for
  repository-wide reviews, onboarding, or changes spanning UI, project selection,
  scheduling, and agent execution. Provides cross-layer source and test maps;
  delegates individual bug fixes, Telegram mechanics, security reviews, and
  release gates to the existing specialized skills.
---

# Telegram Controller Project Guide

Use this as the repository map, not as a replacement for the workflows in
[AGENTS.md](../../../AGENTS.md). The product is an async Python Telegram
controller for local projects, GitHub Issues, and CLI coding agents. Its user
interface is Telegram, not a web frontend or the agent's desktop application.

## 1. Establish the current implementation

Check the current branch and dirty files before making changes. Read
`pyproject.toml`, `.github/workflows/ci.yml`, and the source entry points relevant
to the request. Existing uncommitted work belongs to the user; a review does not
authorize fixing production code, starting the bot, launching an agent, or
changing live databases.

For component ownership and persistent state, read
[references/architecture.md](./references/architecture.md). It is a navigation
aid: verify named functions in the current source before relying on behavior.
The README, comments, and historical adapter files can describe older behavior.
In particular, inspect the agent registry instead of inferring supported agents
from filenames, and inspect startup call sites instead of assuming that an
existing recovery service is actually invoked.

## 2. Follow the complete contract

Trace a request from its Telegram entry point through state, persistence,
dispatch, agent context/arguments, and completion. Use the relevant contracts in
[references/cross-layer-checks.md](./references/cross-layer-checks.md); do not
load unrelated checks for a narrowly scoped request.

For every user-visible option, identify where the value is stored and where it
is consumed. An option in a keyboard or a confirmation message is not proof
that execution honors it. Keep these distinctions explicit:

- Local prompt CSV versus the SQLite Issue queue.
- Saved project ID versus a legacy scanned-project ID or a browsing root.
- Folder navigation versus confirmation and database mutation.
- Controller approval of a diff versus interactive input requested by an agent.
- Local-only execution versus the commit/push/PR workflow.

## 3. Route specialized work

- Use [issue-analyzer](../issue-analyzer/SKILL.md) to analyze an individual
  feature/bug and [implementation-planner](../implementation-planner/SKILL.md)
  for the implementation plan; this guide supplies component context only.
- Use [telegram-bot-expert](../telegram-bot-expert/SKILL.md) for handlers and
  keyboards, [github-agent-orchestrator](../github-agent-orchestrator/SKILL.md)
  for Git/GitHub changes, and
  [antigravity-coding-agent](../antigravity-coding-agent/SKILL.md) for that CLI.
- Follow the existing test-driven, impact, security, and final-verification
  workflow before shipping an authorized fix. Do not create another parallel
  review/release process here.

## 4. Review and verification boundaries

For a repository-wide review, cover entry/lifecycle, Telegram, data stores,
scheduler, executor/agents, GitHub, personal mode, tests, and packaging/CI. Report
confirmed defects by severity with file/line evidence, a safe reproduction or
call-path proof, user impact, and missing regression coverage. Separate
improvements and untested assumptions from findings. Review scope is not proof
of exhaustive live behavior or an external security audit.

Run isolated tests with fake clients/processes and temporary databases/CSVs.
Never reproduce a suspected failure on the user's projects, live bot, account
credentials, or actual queue. Do not import production settings merely to dump
their values, and never include secrets in an audit or skill resource.

The project's checks are:

```powershell
py -m pytest
py -m ruff check .
py -m mypy app
py -m app.main --check-config
py .agents/skills/skill-maintainer/scripts/validate_skills.py
```

Use [final-verifier](../final-verifier/SKILL.md) for the final gate, and identify
checks not run (especially live Telegram, installed CLI execution, and Linux CI)
instead of treating mock coverage as end-to-end verification.

## When NOT to use

Do not select this guide for an isolated keyboard edit, a single known bug,
routine GitHub operations, or authoring another skill when no repository map is
needed. Use the corresponding specialized skill. Do not use it for other bots
or generic Python projects.
