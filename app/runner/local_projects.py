"""Stable identities for local Git projects selected through Telegram."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from app.runner.project_scanner import LocalProject, ProjectScanner

_MARKER_NAME = "telegram-controller-project-id"


@dataclass(frozen=True)
class RegisteredLocalProject:
    project_id: str
    name: str
    path: Path
    repo_full_name: str | None


class LocalProjectRegistry:
    """Stores a stable identifier in a repository's non-versioned Git metadata."""

    def __init__(self, projects_root: Path) -> None:
        self._projects_root = projects_root.resolve()
        self._scanner = ProjectScanner()

    @staticmethod
    def _git_dir(project_path: Path) -> Path | None:
        git_entry = project_path / ".git"
        if git_entry.is_dir():
            return git_entry
        if not git_entry.is_file():
            return None
        try:
            content = git_entry.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return None
        if not content.startswith("gitdir:"):
            return None
        return (project_path / content.removeprefix("gitdir:").strip()).resolve()

    def register(self, project: LocalProject) -> RegisteredLocalProject:
        project_path = project.path.resolve()
        if not project.is_git or not project_path.is_relative_to(self._projects_root):
            raise ValueError("The selected project must be a Git repository inside PROJECTS_DIR.")
        git_dir = self._git_dir(project_path)
        if git_dir is None:
            raise ValueError("The selected project does not have readable Git metadata.")

        marker = git_dir / "info" / _MARKER_NAME
        try:
            project_id = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
        except OSError as exc:
            raise ValueError("Could not read the local project identity.") from exc
        if not project_id:
            project_id = f"project-{uuid.uuid4().hex}"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(project_id + "\n", encoding="utf-8")

        return RegisteredLocalProject(
            project_id=project_id,
            name=project.name,
            path=project_path,
            repo_full_name=project.repo_full_name,
        )

    def resolve(self, project_id: str) -> RegisteredLocalProject | None:
        if not project_id:
            return None
        # A project may have been moved after it was selected. Do not reuse a
        # scanner result that still points at the old directory.
        self._scanner.invalidate_cache(self._projects_root)
        for project in self._scanner.scan(self._projects_root):
            if not project.is_git:
                continue
            git_dir = self._git_dir(project.path.resolve())
            if git_dir is None:
                continue
            marker = git_dir / "info" / _MARKER_NAME
            try:
                saved_id = marker.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if saved_id == project_id:
                return RegisteredLocalProject(
                    project_id=project_id,
                    name=project.name,
                    path=project.path.resolve(),
                    repo_full_name=project.repo_full_name,
                )
        return None
