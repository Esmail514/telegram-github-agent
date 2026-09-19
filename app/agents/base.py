"""
Base agent interface and shared data classes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentContext:
    """Everything the agent needs to know to implement an issue."""

    # Repository
    repo_full_name: str         # "owner/repo"
    repo_url: str               # HTTPS clone URL
    repo_description: str
    default_branch: str

    # Issue
    issue_number: int
    issue_title: str
    issue_body: str
    issue_labels: list[str]

    # Branch for this job
    branch: str

    # Workspace snapshot
    repo_structure: str         # Directory tree as string
    key_files: dict[str, str]   # filename → content (README, package.json, etc.)

    # Constraints
    max_fix_iterations: int = 5


@dataclass
class AgentResult:
    """Result returned by an agent after execution."""

    success: bool
    exit_code: int
    summary: str                        # Human-readable summary
    files_changed: list[str] = field(default_factory=list)
    error: str | None = None
    raw_output: str = ""                # Last N lines of stdout for debugging


ProgressCallback = Callable[[str], Awaitable[None]]


class BaseAgent(ABC):
    """
    Abstract base class for AI coding agents.

    Concrete implementations:
        - OpenCodeAgent  (opencode CLI)
        - ClaudeAgent    (claude CLI — future)
        - CodexAgent     (codex CLI — future)
        - GeminiAgent    (gemini CLI — future)
    """

    name: str = "base"

    @abstractmethod
    async def run(
        self,
        context: AgentContext,
        workspace_path: Path,
        on_progress: ProgressCallback,
    ) -> AgentResult:
        """
        Execute the agent against the workspace.

        Args:
            context: Full task context including repo + issue details.
            workspace_path: Absolute path to the cloned repository.
            on_progress: Async callback to send progress messages to Telegram.

        Returns:
            AgentResult with success flag, exit code, and summary.
        """
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Request the agent to stop (graceful termination)."""
        ...

    def build_prompt(self, context: AgentContext) -> str:
        """
        Build the task prompt for the agent from AgentContext.
        Can be overridden by subclasses for agent-specific formatting.
        """
        labels_str = (
            ", ".join(context.issue_labels)
            if context.issue_labels
            else "none"
        )

        key_files_str = ""
        if context.key_files:
            parts = []
            for fname, content in context.key_files.items():
                parts.append(f"### {fname}\n```\n{content[:2000]}\n```")
            key_files_str = "\n\n".join(parts)

        return f"""You are an AI coding agent working on a GitHub repository.

## Repository

Name: {context.repo_full_name}
URL: {context.repo_url}
Description: {context.repo_description}
Default branch: {context.default_branch}
Working branch: {context.branch}

## Issue

Number: #{context.issue_number}
Title: {context.issue_title}
Labels: {labels_str}

### Description

{context.issue_body or "(No description provided)"}

## Repository Structure

```
{context.repo_structure}
```

## Key Configuration Files

{key_files_str or "(None found)"}

## Your Objective

Implement Issue #{context.issue_number}: "{context.issue_title}" completely.

## Instructions

1. Inspect the repository before making any changes.
2. Understand the existing architecture, patterns, and conventions.
3. Identify the files and modules relevant to the issue.
4. Do NOT make unnecessary changes to unrelated files.
5. Follow the existing coding style, naming conventions, and project structure.
6. Implement the requested functionality completely and correctly.
7. Preserve all existing functionality — do not break what already works.
8. Run appropriate tests, linters, analyzers, and builds.
9. If tests or validation fail, investigate the root cause carefully.
10. Fix all problems caused by your implementation.
11. Re-run validation after every fix.
12. Review the final git diff before finishing.
13. Do NOT modify files unrelated to this issue.
14. Do NOT commit secrets, credentials, API keys, or tokens.
15. Do NOT push to main or master — your branch is: {context.branch}
16. Stop when the implementation is complete and all validation passes.

## Before Finishing

- Review all changes with `git diff`.
- Run the project's test suite.
- Run any linter or static analysis tool present (eslint, ruff, flutter analyze, etc.).
- Verify the project builds (if applicable).
- Report what was changed and the results of all validation.

You have at most {context.max_fix_iterations} fix iterations. Use them wisely.
"""
