"""
Project Scanner — scans a local projects directory to discover repositories,
extract their active branch, and resolve their linked GitHub repository.
"""
from __future__ import annotations

import configparser
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from app.config.settings import settings

logger = logging.getLogger(__name__)

# Matches GitHub URLs:
# - git@github.com:owner/repo.git
# - ssh://git@github.com/owner/repo.git
# - https://github.com/owner/repo.git
# - https://token@github.com/owner/repo
_GITHUB_REMOTE_REGEX = re.compile(
    r"github\.com[:/](?P<owner>[a-zA-Z0-9_.-]+)/(?P<repo>[a-zA-Z0-9_.-]+?)(?:\.git)?/?$"
)


def parse_github_repo_from_url(url: str) -> str | None:
    """Extract 'owner/repo' from a GitHub git remote URL."""
    clean_url = url.strip()
    match = _GITHUB_REMOTE_REGEX.search(clean_url)
    if match:
        owner = match.group("owner")
        repo = match.group("repo")
        return f"{owner}/{repo}"
    return None


@dataclass
class LocalProject:
    """Represents a discovered local project on the machine."""
    name: str
    path: Path
    repo_full_name: str | None = None
    branch: str | None = None
    remote_url: str | None = None
    is_git: bool = False

    @property
    def has_github_remote(self) -> bool:
        return self.repo_full_name is not None

    @property
    def display_label(self) -> str:
        """Formatted label for keyboards and messages."""
        icon = "🐙" if self.has_github_remote else "📁"
        return f"{icon} {self.name}"


class ProjectScanner:
    """Discovers and inspects local projects within a root directory."""

    @staticmethod
    def _read_git_head(git_dir: Path) -> str | None:
        """Read the active branch name from .git/HEAD."""
        head_file = git_dir / "HEAD"
        if not head_file.is_file():
            return None
        try:
            content = head_file.read_text(encoding="utf-8", errors="replace").strip()
            if content.startswith("ref: refs/heads/"):
                return content.replace("ref: refs/heads/", "").strip()
            # Detached HEAD — return short hash
            return content[:7] if content else None
        except Exception as exc:
            logger.debug("Failed to read HEAD in %s: %s", git_dir, exc)
            return None

    @staticmethod
    def _read_git_remote_url(git_dir: Path) -> str | None:
        """Read the origin remote URL from .git/config."""
        config_file = git_dir / "config"
        if not config_file.is_file():
            return None
        try:
            parser = configparser.ConfigParser(interpolation=None)
            parser.read(config_file, encoding="utf-8")
            # Look for [remote "origin"]
            if parser.has_section('remote "origin"'):
                return parser.get('remote "origin"', "url", fallback=None)
            # Fallback: find any remote section
            for section in parser.sections():
                if section.lower().startswith("remote ") and parser.has_option(section, "url"):
                    return parser.get(section, "url")
            return None
        except Exception as exc:
            logger.debug("Failed to parse git config in %s: %s", git_dir, exc)
            # Fallback to simple regex on config file content
            try:
                text = config_file.read_text(encoding="utf-8", errors="replace")
                m = re.search(r'url\s*=\s*([^\r\n]+)', text)
                if m:
                    return m.group(1).strip()
            except Exception:
                pass
            return None

    def inspect_directory(self, project_path: Path) -> LocalProject:
        """Inspect a single folder to determine if it is a Git repo and its metadata."""
        project_path = project_path.resolve()
        name = project_path.name

        git_entry = project_path / ".git"
        if not git_entry.exists():
            return LocalProject(
                name=name,
                path=project_path,
                is_git=False,
            )

        # Handle normal .git directory or .git file (worktree/submodule)
        git_dir = git_entry
        if git_entry.is_file():
            try:
                content = git_entry.read_text(encoding="utf-8", errors="replace").strip()
                if content.startswith("gitdir:"):
                    gitdir_rel = content.replace("gitdir:", "").strip()
                    git_dir = (project_path / gitdir_rel).resolve()
            except Exception:
                pass

        branch = self._read_git_head(git_dir)
        remote_url = self._read_git_remote_url(git_dir)
        repo_full_name = parse_github_repo_from_url(remote_url) if remote_url else None

        return LocalProject(
            name=name,
            path=project_path,
            repo_full_name=repo_full_name,
            branch=branch,
            remote_url=remote_url,
            is_git=True,
        )

    def scan(self, directory: Path | str | None = None) -> list[LocalProject]:
        """
        Scan directory for project folders.
        If directory is None, uses settings.PROJECTS_DIR.
        Returns a sorted list of discovered projects.
        """
        target_dir = Path(directory) if directory else settings.PROJECTS_DIR
        if not target_dir:
            return []

        target_path = Path(target_dir).resolve()
        if not target_path.exists() or not target_path.is_dir():
            logger.warning("Projects directory does not exist or is not a dir: %s", target_path)
            return []

        projects: list[LocalProject] = []

        try:
            for item in target_path.iterdir():
                if not item.is_dir():
                    continue
                # Skip common system / hidden dirs
                if item.name.startswith(".") or item.name.startswith("$"):
                    continue
                if item.name in ("node_modules", "venv", ".venv", "__pycache__", "dist", "build"):
                    continue

                if (item / ".git").exists():
                    projects.append(self.inspect_directory(item))
                else:
                    # Check 1 level deep for nested git repos (e.g. Work/Web/MyProject)
                    sub_git_found = False
                    try:
                        for sub_item in item.iterdir():
                            if (
                                sub_item.is_dir()
                                and not sub_item.name.startswith(".")
                                and sub_item.name not in ("node_modules", "venv", ".venv", "__pycache__")
                                and (sub_item / ".git").exists()
                            ):
                                proj = self.inspect_directory(sub_item)
                                proj.name = f"{item.name}/{sub_item.name}"
                                projects.append(proj)
                                sub_git_found = True
                    except Exception:
                        pass
                    if not sub_git_found:
                        projects.append(self.inspect_directory(item))
        except Exception as exc:
            logger.error("Error scanning projects directory %s: %s", target_path, exc)

        # If the target directory itself is a git repo and has no project subfolders, include it
        if not projects and (target_path / ".git").exists():
            projects.append(self.inspect_directory(target_path))

        # Sort: git repos with remotes first, then git repos, then alphabetically
        projects.sort(key=lambda p: (not p.has_github_remote, not p.is_git, p.name.lower()))
        return projects

    def get_project_by_name(
        self, name: str, directory: Path | str | None = None
    ) -> LocalProject | None:
        """Find a project by folder name."""
        for proj in self.scan(directory):
            if proj.name.lower() == name.lower():
                return proj
        return None

    def get_project_by_repo(
        self, repo_full_name: str, directory: Path | str | None = None
    ) -> LocalProject | None:
        """Find a project by its GitHub repo_full_name (e.g. 'owner/repo')."""
        target = repo_full_name.lower().strip()
        for proj in self.scan(directory):
            if proj.repo_full_name and proj.repo_full_name.lower() == target:
                return proj
        return None


project_scanner = ProjectScanner()
