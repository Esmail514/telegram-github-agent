"""
Pull Request service.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from github import GithubException

from app.github.client import github_client

logger = logging.getLogger(__name__)


@dataclass
class PRInfo:
    number: int
    title: str
    html_url: str
    state: str
    head_branch: str
    base_branch: str

    @classmethod
    def from_github(cls, pr: object) -> PRInfo:
        return cls(
            number=pr.number,  # type: ignore[attr-defined]
            title=pr.title,  # type: ignore[attr-defined]
            html_url=pr.html_url,  # type: ignore[attr-defined]
            state=pr.state,  # type: ignore[attr-defined]
            head_branch=pr.head.ref,  # type: ignore[attr-defined]
            base_branch=pr.base.ref,  # type: ignore[attr-defined]
        )


class PullRequestService:
    """Create and inspect GitHub Pull Requests."""

    def create_pr(
        self,
        repo_full_name: str,
        title: str,
        body: str,
        head_branch: str,
        base_branch: str,
    ) -> PRInfo:
        try:
            repo = github_client.get_repo(repo_full_name)
            pr = repo.create_pull(
                title=title,
                body=body,
                head=head_branch,
                base=base_branch,
                draft=False,
            )
            logger.info(
                "Created PR #%d in %s (%s → %s)",
                pr.number, repo_full_name, head_branch, base_branch,
            )
            return PRInfo.from_github(pr)
        except GithubException as exc:
            logger.error("GitHub error creating PR in %s: %s", repo_full_name, exc)
            raise

    def get_pr(self, repo_full_name: str, number: int) -> PRInfo:
        try:
            repo = github_client.get_repo(repo_full_name)
            pr = repo.get_pull(number)
            return PRInfo.from_github(pr)
        except GithubException as exc:
            logger.error("GitHub error fetching PR #%d from %s: %s", number, repo_full_name, exc)
            raise


# Module-level singleton
pr_service = PullRequestService()
