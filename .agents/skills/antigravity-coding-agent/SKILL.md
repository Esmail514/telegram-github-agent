---
name: antigravity-coding-agent
description: >-
  Workflows, conventions, and prompt engineering practices for using Antigravity
  as an autonomous coding agent and issue polisher. Use when configuring Antigravity
  CLI execution, task prompting, issue crafting, or agent progress streaming.
---

# Antigravity Coding Agent Skill

This skill explains how Antigravity (`agy` / `antigravity` / `antigravity-ide`) operates
within this repository as the primary autonomous agent and issue shaper.

## 1. Execution Architecture

Antigravity operates in two distinct modes in this bot:

### Mode A: Autonomous Issue Implementation Agent
When a user launches `/run` on an issue:
1. **Context Construction**: `context_builder.build(...)` inspects the cloned repo, generates the directory tree, and extracts key configuration files (`README.md`, `package.json`, `pyproject.toml`).
2. **Task Prompt**: Writes `.antigravity_task.md` into the workspace root.
3. **Execution**: Spawns Antigravity CLI non-interactively:
   ```bash
   agy --prompt "<task_prompt>"
   ```
4. **Progress Streaming**: Stderr and stdout streams are decoded line-by-line and sent to Telegram via `on_progress` callbacks.
5. **Validation**: The agent inspects `git status`, commits changes, pushes the branch, and opens a Pull Request.

### Mode B: Issue Crafting & Polishing Service
When a user drafts an issue or taps "✨ Polish with AI":
1. Antigravity analyzes the issue title and description.
2. Identifies the issue type (bug, feature, docs, refactor, performance, security, test).
3. Applies conventional commit prefixing (`fix:`, `feat:`, `docs:`, etc.).
4. Formats a comprehensive GitHub Flavored Markdown body with:
   - `## Summary`
   - `## Context & Motivation`
   - `## Current Behavior / Proposed Implementation`
   - `## Acceptance Criteria`
5. Returns a structured `PolishedIssue` object without requiring third-party API keys.

---

## 2. CLI Candidate Discovery

On Windows systems, Antigravity CLI executables are detected in this precedence:
1. `settings.ANTIGRAVITY_COMMAND` (from `.env`)
2. `shutil.which("antigravity")`
3. `shutil.which("agy")`
4. `shutil.which("antigravity-ide")`
5. `%LOCALAPPDATA%\Programs\Antigravity IDE\bin\antigravity-ide.cmd`

---

## 3. Graceful Termination & Safety

Autonomous agent processes run with safety bounds:
- **Timeout**: Enforced via `asyncio.wait_for(process.wait(), timeout=settings.MAX_AGENT_RUNTIME_MINUTES * 60)`.
- **Termination**: If user issues `/stop` in Telegram, `AntigravityAgent.stop()` sends `process.terminate()`.
- **Cleanup**: Prompt files (`.antigravity_task.md`) are deleted in the `finally:` block.
- **Workspace Preserved**: The cloned directory is preserved for user manual inspection if needed.

See [agent_prompting.md](./references/agent_prompting.md) for task prompt templates.
