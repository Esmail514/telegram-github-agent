"""
Job Executor — orchestrates the full agent workflow:

    clone → branch → inspect → agent → commit → push → PR

All Telegram progress notifications go through the `notify_fn` callback
so this module has no direct Telegram dependency.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from app.agents.manager import agent_manager
from app.config.settings import settings
from app.database.repository import Database, Job, JobRepository, JobStatus
from app.github.client import RepoInfo
from app.github.issues import IssueInfo
from app.github.pull_requests import pr_service
from app.runner.context_builder import context_builder
from app.runner.git import SecretDetectedError, git_service
from app.runner.jobs import (
    generate_job_id,
    make_branch_name,
    make_commit_message,
    make_pr_body,
    make_pr_title,
)
from app.runner.workspace import WorkspaceManager

logger = logging.getLogger(__name__)

NotifyFn = Callable[[str], Awaitable[None]]


class JobExecutor:
    """
    Orchestrates a single agent job end-to-end.
    Enforces single-active-job limit.
    """

    def __init__(
        self, db: Database, workspace: WorkspaceManager | None = None
    ) -> None:
        self._db = db
        self._repo = JobRepository(db)
        self._workspace = workspace or WorkspaceManager()
        self._active_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_active_job(self) -> Job | None:
        return await self._repo.get_active_job()

    async def start_job(
        self,
        repo_info: RepoInfo,
        issue_info: IssueInfo,
        telegram_chat_id: int,
        agent_name: str | None = None,
        notify_fn: NotifyFn | None = None,
    ) -> Job:
        """
        Queue and start a new job.
        Raises RuntimeError if another job is already active.
        """
        active = await self._repo.get_active_job()
        if active:
            raise RuntimeError(
                f"Another agent is already running.\n\n"
                f"Repository: {active.repo_full_name}\n"
                f"Issue: #{active.issue_number}"
            )

        agent_name = agent_name or settings.DEFAULT_AGENT
        branch = make_branch_name(issue_info.number)
        job_id = generate_job_id(repo_info.full_name, issue_info.number)

        job = await self._repo.create_job(
            job_id=job_id,
            repo_full_name=repo_info.full_name,
            issue_number=issue_info.number,
            branch=branch,
            agent=agent_name,
            telegram_chat_id=telegram_chat_id,
        )

        notify = notify_fn or self._noop_notify

        # Launch the execution loop as a background task
        self._active_task = asyncio.create_task(
            self._run_job(job_id, repo_info, issue_info, agent_name, notify),
            name=f"job-{job_id}",
        )
        self._active_task.add_done_callback(self._on_task_done)

        return job

    async def stop_active_job(self, notify_fn: NotifyFn | None = None) -> Job | None:
        """Request graceful stop of the active job."""
        active = await self._repo.get_active_job()
        if not active:
            return None

        notify = notify_fn or self._noop_notify

        agent = agent_manager.get_active()
        if agent:
            await agent.stop()

        if self._active_task and not self._active_task.done():
            self._active_task.cancel()

        job = await self._repo.set_status(active.job_id, JobStatus.STOPPED)
        agent_manager.clear_active()

        await notify(
            f"🛑 Agent stopped\n\n"
            f"Repository: {active.repo_full_name}\n"
            f"Issue: #{active.issue_number}\n\n"
            f"The workspace was preserved for inspection."
        )
        return job

    # ------------------------------------------------------------------
    # Internal orchestration
    # ------------------------------------------------------------------

    async def _run_job(
        self,
        job_id: str,
        repo_info: RepoInfo,
        issue_info: IssueInfo,
        agent_name: str,
        notify: NotifyFn,
    ) -> None:
        """Full end-to-end job execution pipeline."""
        repo = self._repo

        async def update(status: str, phase: str | None = None, **kwargs) -> None:
            await repo.set_status(job_id, status, phase)
            if kwargs:
                await repo.update_job(job_id, **kwargs)

        owner, repo_name = repo_info.full_name.split("/", 1)

        try:
            # --- Phase: CLONING ------------------------------------------
            await update(JobStatus.CLONING, "Cloning repository")
            await notify(
                f"🚀 Agent started\n\n"
                f"Repository: {repo_info.full_name}\n"
                f"Issue: #{issue_info.number}\n"
                f"Branch: {make_branch_name(issue_info.number)}"
            )

            workspace_path = await self._workspace.clone_or_update(
                clone_url=repo_info.clone_url,
                owner=owner,
                repo=repo_name,
            )

            # Configure git identity (needed for commits)
            await git_service.configure_identity(
                workspace_path, "AI Agent", "ai-agent@noreply.local"
            )

            branch = make_branch_name(issue_info.number)
            await git_service.create_branch(workspace_path, branch)

            # --- Phase: INSPECTING ----------------------------------------
            await update(JobStatus.INSPECTING, "Inspecting repository")
            await notify("🔍 Inspecting repository...")

            context = context_builder.build(
                repo_info=repo_info,
                issue_info=issue_info,
                branch=branch,
                workspace_path=workspace_path,
                max_fix_iterations=settings.MAX_FIX_ITERATIONS,
            )

            # --- Phase: IMPLEMENTING ----------------------------------------
            await update(JobStatus.IMPLEMENTING, "Running agent")
            await notify("🛠 Running AI agent...")

            agent = agent_manager.get_agent(agent_name)

            async def on_agent_progress(msg: str) -> None:
                await notify(msg)

            timeout_secs = settings.MAX_AGENT_RUNTIME_MINUTES * 60
            try:
                result = await asyncio.wait_for(
                    agent.run(context, workspace_path, on_agent_progress),
                    timeout=timeout_secs,
                )
            except TimeoutError:
                await update(JobStatus.FAILED, "Timed out", error="Agent exceeded time limit")
                await notify(
                    f"⏰ Agent timed out after {settings.MAX_AGENT_RUNTIME_MINUTES} minutes."
                )
                return
            except asyncio.CancelledError:
                logger.info("Job %s was cancelled", job_id)
                return

            if not result.success:
                error_msg = result.error or "Agent returned non-zero exit code"
                await update(JobStatus.FAILED, "Agent failed", error=error_msg[:500])
                await notify(
                    f"❌ Agent failed\n\n{error_msg[:300]}"
                )
                return

            await notify("✅ Agent completed implementation!")

            # --- Phase: COMMITTING ----------------------------------------
            await update(JobStatus.COMMITTING, "Committing changes")
            await notify("📦 Creating commit...")

            git_status = await git_service.status(workspace_path)
            if not git_status.has_changes:
                await update(
                    JobStatus.FAILED,
                    "No changes",
                    error="Agent completed but made no file changes",
                )
                await notify(
                    "⚠️ Agent finished but made no file changes. Nothing to commit."
                )
                return

            changed_files = git_status.changed_files
            await repo.update_job(job_id, files_changed=len(changed_files))

            commit_msg = make_commit_message(issue_info.title, issue_info.number)
            try:
                await git_service.commit(workspace_path, commit_msg)
            except SecretDetectedError as exc:
                await update(JobStatus.FAILED, "Secret detected", error=str(exc)[:300])
                await notify(
                    "🔒 Commit blocked: potential secret detected in changes.\n"
                    "Please review the workspace manually."
                )
                return

            # --- Phase: PUSHING -------------------------------------------
            await update(JobStatus.PUSHING, "Pushing branch")
            await notify(f"⬆️ Pushing branch {branch}...")

            await git_service.push(workspace_path, branch=branch)

            # --- Phase: CREATING_PR ----------------------------------------
            await update(JobStatus.CREATING_PR, "Creating Pull Request")
            await notify("🔀 Creating Pull Request...")

            pr_title = make_pr_title(issue_info.title)
            validation_results = (
                "✅ Agent reported success\n"
                + (result.summary[:500] if result.summary else "")
            )
            pr_body = make_pr_body(
                issue_number=issue_info.number,
                issue_title=issue_info.title,
                agent_name=agent_name,
                summary=result.summary[:800],
                files_changed=changed_files,
                validation_results=validation_results,
            )

            pr = pr_service.create_pr(
                repo_full_name=repo_info.full_name,
                title=pr_title,
                body=pr_body,
                head_branch=branch,
                base_branch=repo_info.default_branch,
            )

            await repo.update_job(
                job_id,
                pr_url=pr.html_url,
                pr_number=pr.number,
                files_changed=len(changed_files),
            )
            await update(JobStatus.COMPLETED, "Completed")

            await notify(
                f"🎉 Agent completed successfully!\n\n"
                f"Repository: {repo_info.full_name}\n"
                f"Issue: #{issue_info.number}\n"
                f"Branch: {branch}\n"
                f"Files changed: {len(changed_files)}\n"
                f"Pull Request: #{pr.number}\n\n"
                f"[Open Pull Request]({pr.html_url})"
            )

        except asyncio.CancelledError:
            logger.info("Job %s cancelled", job_id)
            raise

        except Exception as exc:
            logger.exception("Job %s failed with unexpected error: %s", job_id, exc)
            error_str = str(exc)[:500]
            try:
                await repo.update_job(job_id, error=error_str)
                await repo.set_status(job_id, JobStatus.FAILED)
                await notify(
                    f"❌ Job failed with an unexpected error.\n\n"
                    f"{error_str[:200]}"
                )
            except Exception:
                pass

        finally:
            agent_manager.clear_active()

    def _on_task_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            logger.info("Job task was cancelled")
        elif task.exception():
            logger.error("Job task raised: %s", task.exception())

    @staticmethod
    async def _noop_notify(msg: str) -> None:
        logger.info("[notify] %s", msg)
