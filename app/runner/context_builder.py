"""
Builds the AgentContext from a live repository workspace.

Collects:
- Directory tree
- Key config files (README, package.json, pyproject.toml, etc.)
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.agents.base import AgentContext
from app.github.client import RepoInfo
from app.github.issues import IssueInfo

logger = logging.getLogger(__name__)

# Files worth including as full content (truncated at 3000 chars)
_KEY_FILES = [
    "README.md",
    "README.rst",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "package.json",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "Cargo.toml",
    "pubspec.yaml",
    "go.mod",
    "Makefile",
    ".github/workflows/ci.yml",
    ".github/workflows/test.yml",
]

# Directories to skip when building the tree
_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", "target", ".dart_tool", ".pub-cache",
    "vendor", "coverage", ".pytest_cache", ".mypy_cache",
    ".gradle", ".idea", ".vscode",
}

# Max depth for tree display
_MAX_DEPTH = 5
# Max entries per directory level
_MAX_ENTRIES = 50


class AgentContextBuilder:
    """Builds a rich AgentContext from a workspace directory."""

    def build(
        self,
        repo_info: RepoInfo,
        issue_info: IssueInfo,
        branch: str,
        workspace_path: Path,
        max_fix_iterations: int = 5,
    ) -> AgentContext:
        structure = self._build_tree(workspace_path)
        key_files = self._read_key_files(workspace_path)

        return AgentContext(
            repo_full_name=repo_info.full_name,
            repo_url=repo_info.clone_url,
            repo_description=repo_info.description,
            default_branch=repo_info.default_branch,
            issue_number=issue_info.number,
            issue_title=issue_info.title,
            issue_body=issue_info.body,
            issue_labels=issue_info.labels,
            branch=branch,
            repo_structure=structure,
            key_files=key_files,
            max_fix_iterations=max_fix_iterations,
        )

    def _build_tree(self, root: Path, depth: int = 0, prefix: str = "") -> str:
        if depth > _MAX_DEPTH:
            return prefix + "...\n"

        lines: list[str] = []
        try:
            entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name))
        except PermissionError:
            return prefix + "[permission denied]\n"

        entries = entries[:_MAX_ENTRIES]
        for i, entry in enumerate(entries):
            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            child_prefix = prefix + ("    " if is_last else "│   ")

            if entry.name in _SKIP_DIRS or entry.name.startswith("."):
                if entry.name not in (".github",):
                    continue

            if entry.is_dir():
                lines.append(f"{prefix}{connector}{entry.name}/")
                lines.append(self._build_tree(entry, depth + 1, child_prefix))
            else:
                size = ""
                try:
                    size = f" ({entry.stat().st_size:,} bytes)"
                except OSError:
                    pass
                lines.append(f"{prefix}{connector}{entry.name}{size}")

        return "\n".join(lines)

    def _read_key_files(self, root: Path) -> dict[str, str]:
        result: dict[str, str] = {}
        for fname in _KEY_FILES:
            fpath = root / fname
            if fpath.exists() and fpath.is_file():
                try:
                    content = fpath.read_text(encoding="utf-8", errors="replace")
                    result[fname] = content[:3000]
                except OSError as exc:
                    logger.debug("Could not read %s: %s", fpath, exc)
        return result


# Module-level singleton
context_builder = AgentContextBuilder()
