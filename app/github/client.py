"""
Thin wrapper around the PyGithub client with caching and error normalisation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from github import Github, GithubException

from app.github.authentication import get_github_client

logger = logging.getLogger(__name__)


@dataclass
class RepoInfo:
    full_name: str          # "owner/repo"
    name: str
    owner: str
    description: str
    clone_url: str          # https URL
    ssh_url: str
    default_branch: str
    private: bool
    html_url: str

    @classmethod
    def from_github(cls, repo: object) -> RepoInfo:  # repo: github.Repository.Repository
        return cls(
            full_name=repo.full_name,  # type: ignore[attr-defined]
            name=repo.name,  # type: ignore[attr-defined]
            owner=repo.owner.login,  # type: ignore[attr-defined]
            description=repo.description or "",  # type: ignore[attr-defined]
            clone_url=repo.clone_url,  # type: ignore[attr-defined]
            ssh_url=repo.ssh_url,  # type: ignore[attr-defined]
            default_branch=repo.default_branch,  # type: ignore[attr-defined]
            private=repo.private,  # type: ignore[attr-defined]
            html_url=repo.html_url,  # type: ignore[attr-defined]
        )


class GitHubClient:
    """Central GitHub client — thin async-friendly wrapper."""

    def __init__(self) -> None:
        self._client: Github | None = None

    def _get(self) -> Github:
        if self._client is None:
            self._client = get_github_client()
        return self._client

    def get_repo(self, full_name: str):  # type: ignore[return]
        """Return a raw PyGithub Repository object."""
        try:
            return self._get().get_repo(full_name)
        except GithubException as exc:
            logger.error("GitHub error fetching repo %s: %s", full_name, exc)
            raise

    def get_authenticated_user(self):  # type: ignore[return]
        try:
            return self._get().get_user()
        except GithubException as exc:
            logger.error("GitHub error fetching authenticated user: %s", exc)
            raise

    def list_repos(self, page: int = 0, per_page: int = 30) -> list[RepoInfo]:
        """List repositories accessible to the authenticated identity."""
        try:
            user = self._get().get_user()
            paginated = user.get_repos(type="all", sort="updated")
            # Manual slice — PyGithub PaginatedList is not async
            start = page * per_page
            end = start + per_page
            page_items = list(paginated[start:end])
            return [RepoInfo.from_github(r) for r in page_items]
        except GithubException as exc:
            logger.error("GitHub error listing repos: %s", exc)
            raise

    def get_repo_info(self, full_name: str) -> RepoInfo:
        return RepoInfo.from_github(self.get_repo(full_name))


# Module-level singleton
github_client = GitHubClient()
