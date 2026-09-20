"""
Pull Request service — create, inspect, list, and merge GitHub Pull Requests.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

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
    user_login: str = ""

    @classmethod
    def from_github(cls, pr: object) -> PRInfo:
        user_login = ""
        user_obj = getattr(pr, "user", None)
        if user_obj and hasattr(user_obj, "login"):
            user_login = user_obj.login

        return cls(
            number=pr.number,  # type: ignore[attr-defined]
            title=pr.title,  # type: ignore[attr-defined]
            html_url=pr.html_url,  # type: ignore[attr-defined]
            state=pr.state,  # type: ignore[attr-defined]
            head_branch=pr.head.ref,  # type: ignore[attr-defined]
            base_branch=pr.base.ref,  # type: ignore[attr-defined]
            user_login=user_login,
        )


@dataclass
class PRMergeResult:
    merged: bool
    message: str
    sha: str | None = None


class PullRequestService:
    """Create, inspect, list, and merge GitHub Pull Requests."""

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

    def list_prs(
        self,
        repo_full_name: str,
        state: str = "open",
        page: int = 0,
        per_page: int = 10,
    ) -> list[PRInfo]:
        """List pull requests for a repository."""
        try:
            repo = github_client.get_repo(repo_full_name)
            paginated = repo.get_pulls(state=state, sort="updated", direction="desc")
            prs: list[PRInfo] = []
            start_index = page * per_page
            skipped = 0
            for item in paginated:
                if skipped < start_index:
                    skipped += 1
                    continue
                prs.append(PRInfo.from_github(item))
                if len(prs) >= per_page:
                    break
            return prs
        except GithubException as exc:
            logger.error("GitHub error listing PRs for %s: %s", repo_full_name, exc)
            raise

    def merge_pr(
        self,
        repo_full_name: str,
        number: int,
        commit_title: str | None = None,
        commit_message: str | None = None,
        merge_method: Literal["merge", "squash", "rebase"] = "squash",
    ) -> PRMergeResult:
        """
        Merge an open Pull Request on GitHub.

        Args:
            repo_full_name: Target repo ("owner/repo").
            number: PR number.
            commit_title: Optional custom commit title.
            commit_message: Optional custom commit description.
            merge_method: "squash" (default), "merge", or "rebase".
        """
        try:
            repo = github_client.get_repo(repo_full_name)
            pr = repo.get_pull(number)
            kwargs: dict = {"merge_method": merge_method}
            if commit_title:
                kwargs["commit_title"] = commit_title
            if commit_message:
                kwargs["commit_message"] = commit_message

            status = pr.merge(**kwargs)
            logger.info(
                "Merged PR #%d in %s using %s (sha: %s)",
                number, repo_full_name, merge_method, getattr(status, "sha", None),
            )
            return PRMergeResult(
                merged=status.merged,
                message=status.message,
                sha=getattr(status, "sha", None),
            )
        except GithubException as exc:
            logger.error("GitHub error merging PR #%d in %s: %s", number, repo_full_name, exc)
            err_msg = exc.data.get("message", str(exc)) if isinstance(exc.data, dict) else str(exc)
            return PRMergeResult(
                merged=False,
                message=err_msg,
            )


# Module-level singleton
pr_service = PullRequestService()
