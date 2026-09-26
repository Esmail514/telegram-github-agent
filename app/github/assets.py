"""
GitHub asset upload service for issue images and attachments.

Supports two methods:
1. GitHub user-attachments endpoint (preferred, clean, does not affect git commits)
2. Repository file commit fallback (.github/assets/issue_images/ with [skip ci])
"""
from __future__ import annotations

import datetime
import logging
import uuid

import httpx
from github import GithubException

from app.github.authentication import get_raw_github_token
from app.github.client import github_client

logger = logging.getLogger(__name__)


class GitHubAssetUploader:
    """Uploads media attachments to GitHub for use in Issues and Pull Requests."""

    def upload_issue_image(
        self,
        repo_full_name: str,
        file_bytes: bytes,
        filename: str = "image.png",
        mime_type: str = "image/png",
    ) -> str:
        """
        Upload an image and return its URL for Markdown embedding.

        Tries the user-attachments endpoint first; falls back to creating a file
        in the repository under `.github/assets/issue_images/`.
        """
        repo = github_client.get_repo(repo_full_name)

        # Method 1: GitHub user-attachments endpoint
        token = get_raw_github_token()
        if token:
            try:
                url = self._upload_user_attachment(
                    repo_id=repo.id,
                    token=token,
                    file_bytes=file_bytes,
                    filename=filename,
                    mime_type=mime_type,
                )
                if url:
                    logger.info("Uploaded issue asset via user-attachments: %s", url)
                    return url
            except Exception as exc:
                logger.warning(
                    "User-attachments upload failed, falling back to repo commit: %s",
                    exc,
                )

        # Method 2: Fallback to repository file commit
        return self._upload_repo_commit(
            repo=repo,
            repo_full_name=repo_full_name,
            file_bytes=file_bytes,
            filename=filename,
        )

    def _upload_user_attachment(
        self,
        repo_id: int,
        token: str,
        file_bytes: bytes,
        filename: str,
        mime_type: str,
    ) -> str | None:
        """Upload via https://uploads.github.com/user-attachments/assets."""
        endpoint = (
            f"https://uploads.github.com/user-attachments/assets"
            f"?name={filename}&content_type={mime_type}&repository_id={repo_id}"
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": mime_type,
        }
        with httpx.Client(timeout=30.0) as client:
            res = client.post(endpoint, headers=headers, content=file_bytes)
            if res.status_code in (200, 201):
                data = res.json()
                return data.get("url")
            else:
                logger.warning(
                    "user-attachments returned status %d: %s",
                    res.status_code,
                    res.text[:200],
                )
                return None

    def _upload_repo_commit(
        self,
        repo: object,
        repo_full_name: str,
        file_bytes: bytes,
        filename: str,
    ) -> str:
        """Fallback: commit file directly to the repository default branch."""
        now_str = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S")
        safe_name = f"{now_str}_{uuid.uuid4().hex[:6]}_{filename}"
        path = f".github/assets/issue_images/{safe_name}"
        commit_msg = f"docs: upload issue attachment {safe_name} [skip ci]"

        try:
            default_branch = getattr(repo, "default_branch", "main")
            repo.create_file(  # type: ignore[attr-defined]
                path=path,
                message=commit_msg,
                content=file_bytes,
                branch=default_branch,
            )
            raw_url = f"https://github.com/{repo_full_name}/raw/{default_branch}/{path}"
            logger.info("Uploaded issue asset via repo commit: %s", raw_url)
            return raw_url
        except GithubException as exc:
            logger.error("Failed to commit asset to %s: %s", repo_full_name, exc)
            raise


asset_uploader = GitHubAssetUploader()
