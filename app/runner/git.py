"""
Git service — async wrappers around git operations.

All heavy operations run via asyncio.create_subprocess_exec to stay non-blocking.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.agents.process_cleanup import terminate_process
from app.utils.secrets import SecretScanner

logger = logging.getLogger(__name__)
_scanner = SecretScanner()


@dataclass
class GitStatus:
    branch: str
    modified: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.modified or self.added or self.deleted or self.untracked)

    @property
    def changed_files(self) -> list[str]:
        return self.modified + self.added + self.deleted


class SecretDetectedError(Exception):
    """Raised when a secret is detected in staged changes."""


class GitService:
    """Async wrapper around git CLI operations."""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run(
        self,
        *args: str,
        cwd: Path,
        check: bool = True,
        timeout: float | None = None,
    ) -> tuple[int, str, str]:
        """Run a git command with an optional timeout and return (returncode, stdout, stderr)."""
        from app.config.settings import settings

        timeout_sec = timeout if timeout is not None else settings.GIT_OPERATION_TIMEOUT_SECONDS

        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_sec
            )
        except asyncio.CancelledError:
            await terminate_process(proc)
            raise
        except TimeoutError as err:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
            logger.error("Git command timed out after %ss: %s", timeout_sec, " ".join(args))
            raise TimeoutError(
                f"git command timed out after {timeout_sec}s: {' '.join(args)}"
            ) from err

        stdout = stdout_b.decode(errors="replace")
        stderr = stderr_b.decode(errors="replace")
        if check and proc.returncode != 0:
            raise RuntimeError(
                f"git command failed (rc={proc.returncode}): "
                f"{' '.join(args)}\n{stderr[:500]}"
            )
        assert proc.returncode is not None
        return proc.returncode, stdout, stderr

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def current_branch(self, repo_path: Path) -> str:
        _, stdout, _ = await self._run(
            "git", "rev-parse", "--abbrev-ref", "HEAD", cwd=repo_path
        )
        return stdout.strip()

    async def branch_exists(self, repo_path: Path, branch: str) -> bool:
        rc, _, _ = await self._run(
            "git", "rev-parse", "--verify", branch, cwd=repo_path, check=False
        )
        return rc == 0

    async def create_branch(self, repo_path: Path, branch: str) -> None:
        """Create and checkout a new branch from HEAD."""
        if await self.branch_exists(repo_path, branch):
            logger.info("Branch %s already exists — checking out", branch)
            await self._run("git", "checkout", branch, cwd=repo_path)
        else:
            await self._run("git", "checkout", "-b", branch, cwd=repo_path)
            logger.info("Created branch %s in %s", branch, repo_path)

    async def checkout(self, repo_path: Path, branch: str) -> None:
        await self._run("git", "checkout", branch, cwd=repo_path)

    async def fetch(self, repo_path: Path) -> None:
        from app.config.settings import settings
        await self._run(
            "git", "fetch", "--prune",
            cwd=repo_path,
            timeout=settings.GIT_NETWORK_TIMEOUT_SECONDS,
        )

    async def status(self, repo_path: Path) -> GitStatus:
        branch = await self.current_branch(repo_path)
        _, stdout, _ = await self._run(
            "git", "status", "--porcelain=v1", "-z", "--untracked-files=all", cwd=repo_path
        )
        modified, added, deleted, untracked = [], [], [], []
        entries = iter(stdout.split("\x00"))
        for line in entries:
            if len(line) < 3:
                continue
            xy, filepath = line[:2], line[3:]
            if "R" in xy or "C" in xy:
                next(entries, None)
            if filepath in {"local_prompt_schedules.csv", "local_prompt_schedules.csv.tmp"}:
                continue
            if xy.startswith("?"):
                untracked.append(filepath)
            elif xy.startswith("D") or xy.endswith("D"):
                deleted.append(filepath)
            elif xy.startswith("A"):
                added.append(filepath)
            else:
                modified.append(filepath)
        return GitStatus(
            branch=branch,
            modified=modified,
            added=added,
            deleted=deleted,
            untracked=untracked,
        )

    async def diff(self, repo_path: Path, staged: bool = False) -> str:
        """Return the git diff output."""
        args = ["git", "diff", "--no-ext-diff", "--no-textconv"]
        if staged:
            args.append("--staged")
        _, stdout, _ = await self._run(*args, cwd=repo_path, check=False)
        return stdout

    async def review_diff(self, repo_path: Path) -> str:
        unstaged = await self.diff(repo_path)
        staged = await self.diff(repo_path, staged=True)
        status = await self.status(repo_path)

        def preview_untracked() -> str:
            import fnmatch

            from app.config.settings import settings

            root = repo_path.resolve()
            pieces = []
            for name in status.untracked[:25]:
                path = root / name
                if not path.parent.resolve().is_relative_to(root):
                    continue
                if path.is_symlink():
                    content = "[symbolic link; contents not followed]"
                elif any(
                    fnmatch.fnmatch(path.name.lower(), pattern.lower())
                    for pattern in settings.personal_sensitive_patterns_list
                ):
                    content = "[sensitive file content withheld]"
                else:
                    try:
                        with path.open("rb") as file:
                            raw = file.read(8193)
                        content = (
                            "[binary file]" if b"\0" in raw
                            else raw[:8192].decode("utf-8", errors="replace")
                        )
                        if len(raw) > 8192:
                            content += "\n[preview truncated]"
                    except OSError:
                        content = "[file unavailable]"
                pieces.append(f"Untracked: {name}\n{content}")
            return "\n\n".join(pieces)

        untracked = await asyncio.to_thread(preview_untracked)
        return f"Unstaged:\n{unstaged}\nStaged:\n{staged}\n{untracked}"[:65536]

    async def add_all(self, repo_path: Path) -> None:
        await self._run(
            "git", "add", "-A", "--", ".",
            ":(exclude,top)local_prompt_schedules.csv",
            ":(exclude,top)local_prompt_schedules.csv.tmp",
            cwd=repo_path,
        )

    async def _check_runtime_files(self, repo_path: Path) -> None:
        _, tracked, _ = await self._run(
            "git", "ls-files", "-z", "--",
            ":(top)local_prompt_schedules.csv", ":(top)local_prompt_schedules.csv.tmp",
            cwd=repo_path,
        )
        if tracked.strip("\x00\r\n "):
            raise RuntimeError(
                "A runtime schedule CSV is already tracked by Git. Publication was blocked. "
                "Review and untrack the runtime file manually without deleting its contents."
            )

    async def commit(self, repo_path: Path, message: str) -> None:
        """
        Stage all changes and commit.
        Raises SecretDetectedError if secrets are found in the diff.
        """
        # Check for secrets in staged diff
        await self._check_runtime_files(repo_path)
        await self.add_all(repo_path)
        staged_diff = await self.diff(repo_path, staged=True)
        scan_result = _scanner.scan_diff(staged_diff)
        if scan_result.has_secrets:
            raise SecretDetectedError(
                f"Commit blocked — secrets detected in staged changes:\n{scan_result}"
            )

        # Check if there's actually anything to commit
        _, stdout, _ = await self._run(
            "git", "diff", "--staged", "--name-only", cwd=repo_path
        )
        if not stdout.strip():
            logger.info("No staged changes to commit in %s", repo_path)
            return

        await self._run("git", "commit", "-m", message, cwd=repo_path)
        logger.info("Committed in %s: %s", repo_path, message[:80])

    async def push(
        self,
        repo_path: Path,
        remote: str = "origin",
        branch: str | None = None,
    ) -> None:
        from app.config.settings import settings
        await self._check_runtime_files(repo_path)
        if branch is None:
            branch = await self.current_branch(repo_path)
        if branch in ("main", "master"):
            raise ValueError(
                f"Refusing to push directly to protected branch '{branch}'"
            )
        _, outgoing, _ = await self._run(
            "git", "log", "--format=%H", "--max-count=1", branch,
            "--not", f"--remotes={remote}", "--",
            ":(top)local_prompt_schedules.csv", ":(top)local_prompt_schedules.csv.tmp",
            cwd=repo_path,
        )
        if outgoing.strip():
            raise RuntimeError(
                "Outgoing history contains a private runtime schedule file. "
                "Publication was blocked; review the history locally before retrying."
            )
        await self._run(
            "git", "push", "--set-upstream", remote, branch,
            cwd=repo_path,
            timeout=settings.GIT_NETWORK_TIMEOUT_SECONDS,
        )
        logger.info("Pushed %s to %s/%s", branch, remote, branch)

    async def get_changed_files(self, repo_path: Path) -> list[str]:
        """Return list of files changed vs origin/HEAD."""
        _, stdout, _ = await self._run(
            "git", "diff", "--name-only", "HEAD~1..HEAD",
            cwd=repo_path, check=False
        )
        return [f for f in stdout.splitlines() if f]

    async def get_commit_count(self, repo_path: Path, branch: str) -> int:
        """Count commits ahead of origin/default."""
        _, stdout, _ = await self._run(
            "git", "rev-list", "--count", f"origin/HEAD..{branch}",
            cwd=repo_path, check=False
        )
        try:
            return int(stdout.strip())
        except ValueError:
            return 0

    async def configure_identity(
        self, repo_path: Path, name: str, email: str
    ) -> None:
        await self._run("git", "config", "user.name", name, cwd=repo_path)
        await self._run("git", "config", "user.email", email, cwd=repo_path)


# Module-level singleton
git_service = GitService()
