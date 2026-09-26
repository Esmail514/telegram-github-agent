"""
Workspace manager — handles local clone directories.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from app.config.settings import settings

logger = logging.getLogger(__name__)


class WorkspaceManager:
    """
    Manages the local workspace directory structure:

        {WORKSPACE_DIR}/
            {owner}/
                {repo}/
    """

    def __init__(self, workspace_root: Path | None = None) -> None:
        self.root = workspace_root or settings.WORKSPACE_DIR
        self.root.mkdir(parents=True, exist_ok=True)

    def get_repo_path(self, owner: str, repo: str) -> Path:
        return self.root / owner / repo

    def ensure_owner_dir(self, owner: str) -> Path:
        path = self.root / owner
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def clone_or_update(
        self,
        clone_url: str,
        owner: str,
        repo: str,
        branch: str | None = None,
    ) -> Path:
        """
        Clone the repository if it doesn't exist locally, or pull if it does.
        Returns the path to the local repository.
        """
        repo_path = self.get_repo_path(owner, repo)
        self.ensure_owner_dir(owner)

        if (repo_path / ".git").exists():
            logger.info("Repository %s/%s already cloned — pulling", owner, repo)
            await self._git_pull(repo_path)
        else:
            logger.info("Cloning %s/%s from %s", owner, repo, clone_url)
            await self._git_clone(clone_url, repo_path)

        if branch:
            pass  # branch checkout handled by GitService

        return repo_path

    async def _git_clone(self, url: str, dest: Path) -> None:
        """Run git clone with timeout."""
        timeout_sec = settings.GIT_NETWORK_TIMEOUT_SECONDS
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth=1", url, str(dest),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_sec
            )
        except TimeoutError as err:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
            logger.error("git clone timed out after %ss: %s", timeout_sec, url)
            raise TimeoutError(f"git clone timed out after {timeout_sec}s for {url}") from err

        if proc.returncode != 0:
            err = stderr.decode(errors="replace")
            raise RuntimeError(f"git clone failed (rc={proc.returncode}): {err[:500]}")
        logger.info("Cloned into %s", dest)

    async def _git_pull(self, repo_path: Path) -> None:
        """Run git pull --ff-only with timeout."""
        timeout_sec = settings.GIT_NETWORK_TIMEOUT_SECONDS
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(repo_path), "pull", "--ff-only",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_sec
            )
        except TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
            logger.warning("git pull timed out after %ss in %s", timeout_sec, repo_path)
            return

        if proc.returncode != 0:
            # Don't fail hard on pull errors — the workspace is usable
            err = stderr.decode(errors="replace")
            logger.warning("git pull --ff-only failed (may need manual reset): %s", err[:300])

    def delete_workspace(self, owner: str, repo: str) -> None:
        path = self.get_repo_path(owner, repo)
        if path.exists():
            shutil.rmtree(path)
            logger.info("Deleted workspace %s", path)
