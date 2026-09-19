"""
Issue service — create, list, and fetch GitHub Issues.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from github import GithubException

from app.github.client import github_client

logger = logging.getLogger(__name__)


@dataclass
class IssueInfo:
    number: int
    title: str
    body: str
    state: str                      # "open" | "closed"
    labels: list[str]
    html_url: str
    repo_full_name: str
    assignees: list[str] = field(default_factory=list)

    @classmethod
    def from_github(cls, issue: object, repo_full_name: str) -> IssueInfo:
        return cls(
            number=issue.number,  # type: ignore[attr-defined]
            title=issue.title,  # type: ignore[attr-defined]
            body=issue.body or "",  # type: ignore[attr-defined]
            state=issue.state,  # type: ignore[attr-defined]
            labels=[lb.name for lb in issue.labels],  # type: ignore[attr-defined]
            html_url=issue.html_url,  # type: ignore[attr-defined]
            repo_full_name=repo_full_name,
            assignees=[a.login for a in issue.assignees],  # type: ignore[attr-defined]
        )


class IssueService:
    """CRUD operations for GitHub Issues."""

    def list_issues(
        self,
        repo_full_name: str,
        state: str = "open",
        page: int = 0,
        per_page: int = 20,
    ) -> list[IssueInfo]:
        try:
            repo = github_client.get_repo(repo_full_name)
            paginated = repo.get_issues(state=state, sort="updated")
            issues: list[IssueInfo] = []
            skipped = 0
            target_start = page * per_page
            for item in paginated:
                if item.pull_request is not None:
                    continue
                if skipped < target_start:
                    skipped += 1
                    continue
                issues.append(IssueInfo.from_github(item, repo_full_name))
                if len(issues) >= per_page:
                    break
            return issues
        except GithubException as exc:
            logger.error("GitHub error listing issues for %s: %s", repo_full_name, exc)
            raise

    def get_issue(self, repo_full_name: str, number: int) -> IssueInfo:
        try:
            repo = github_client.get_repo(repo_full_name)
            issue = repo.get_issue(number=number)
            return IssueInfo.from_github(issue, repo_full_name)
        except GithubException as exc:
            logger.error("GitHub error fetching issue #%d from %s: %s", number, repo_full_name, exc)
            raise

    def create_issue(
        self,
        repo_full_name: str,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> IssueInfo:
        try:
            repo = github_client.get_repo(repo_full_name)
            kwargs: dict = {"title": title, "body": body}
            if labels:
                kwargs["labels"] = labels
            issue = repo.create_issue(**kwargs)
            logger.info("Created issue #%d in %s", issue.number, repo_full_name)
            return IssueInfo.from_github(issue, repo_full_name)
        except GithubException as exc:
            logger.error("GitHub error creating issue in %s: %s", repo_full_name, exc)
            raise


    def update_issue(
        self,
        repo_full_name: str,
        number: int,
        title: str | None = None,
        body: str | None = None,
        labels: list[str] | None = None,
    ) -> IssueInfo:
        """
        Edit an existing GitHub issue.

        Only fields that are not None are updated. To clear labels pass
        an empty list; to leave them untouched pass None.
        """
        try:
            repo = github_client.get_repo(repo_full_name)
            issue = repo.get_issue(number=number)
            kwargs: dict = {}
            if title is not None:
                kwargs["title"] = title
            if body is not None:
                kwargs["body"] = body
            if labels is not None:
                kwargs["labels"] = labels
            issue.edit(**kwargs)
            logger.info("Updated issue #%d in %s", number, repo_full_name)
            return IssueInfo.from_github(issue, repo_full_name)
        except GithubException as exc:
            logger.error(
                "GitHub error updating issue #%d in %s: %s", number, repo_full_name, exc
            )
            raise


# Module-level singleton
issue_service = IssueService()
