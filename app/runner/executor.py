"""
Job Executor — orchestrates the full agent workflow:

    clone  branch  inspect  agent  commit  push  PR

All Telegram progress notifications go through the `notify_fn` callback
so this module has no direct Telegram dependency.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.agents.manager import agent_manager
from app.config.settings import settings
from app.database.repository import (
    ApprovalRepository,
    Database,
    Job,
    JobRepository,
    JobStateRepository,
    JobStatus,
)
from app.github.client import RepoInfo
from app.github.issues import IssueInfo
from app.github.pull_requests import pr_service
from app.runner.approvals import (
    APPROVED,
    BEFORE_COMMIT,
    BEFORE_PR,
    BEFORE_PUSH,
    ApprovalCoordinator,
    ApprovalPolicy,
    RiskClassifier,
)
from app.runner.context_builder import context_builder
from app.runner.fallback import AgentRouter, FailureCode, FallbackPolicy
from app.runner.git import SecretDetectedError, git_service
from app.runner.jobs import (
    generate_job_id,
    make_branch_name,
    make_commit_message,
    make_pr_body,
    make_pr_title,
)
from app.runner.progress import ProgressReporter
from app.runner.recovery import (
    interrupted_error,
    is_resumable,
    token_limit_error,
)
from app.runner.verification import VerificationPipeline
from app.runner.workspace import WorkspaceManager
from app.telegram.keyboards import pr_action_keyboard

logger = logging.getLogger(__name__)

NotifyFn = Callable[..., Awaitable[None]]
CompletionFn = Callable[[Job], Awaitable[None]]


def _format_completion_message(
    *,
    status_header: str,
    repo_name: str,
    task_title: str,
    branch: str,
    summary: str | None,
    changed_files: list[str],
    pr_number: int | None = None,
    pr_url: str | None = None,
    merge_method: str | None = None,
    sha: str | None = None,
) -> str:
    lines = [status_header, ""]
    lines.append(f"📁 *المشروع / المستودع:* `{repo_name}`")
    lines.append(f"📌 *المهمة:* {task_title}")
    lines.append(f"🌿 *الفرع:* `{branch}`")

    if pr_number and pr_url:
        merge_label = f" (Merged via {merge_method})" if merge_method else ""
        sha_str = f" (`{sha[:7]}`)" if sha else ""
        lines.append(f"🔀 *Pull Request:* [#{pr_number}]({pr_url}){merge_label}{sha_str}")

    clean_summary = (summary or "").strip()
    if clean_summary:
        lines.append(f"\n📋 *الأشياء التي تمت (الملخص):*\n{clean_summary[:1200]}")
    else:
        lines.append("\n📋 *الأشياء التي تمت:*\nتم تنفيذ التعديلات المطلوبة وتجهيز التغييرات بنجاح.")

    if changed_files:
        lines.append(f"\n📝 *الملفات المعدلة ({len(changed_files)}):*")
        for f in changed_files[:15]:
            lines.append(f"• `{f}`")
        if len(changed_files) > 15:
            lines.append(f"_... و {len(changed_files) - 15} ملفات أخرى_")

    if pr_url and not merge_method:
        lines.append(f"\n[🔗 فتح الـ Pull Request]({pr_url})")

    return "\n".join(lines)


class ExecutorBusyError(RuntimeError):
    """The single execution slot is occupied, including final cleanup."""


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
        self._state_repo = JobStateRepository(db)
        self._workspace = workspace or WorkspaceManager()
        self._active_task: asyncio.Task | None = None
        self._approvals = ApprovalCoordinator(db)
        self._verification: VerificationPipeline | None = None
        self._router: AgentRouter | None = None
        self._start_lock = asyncio.Lock()
        self._closing = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_active_job(self) -> Job | None:
        return await self._repo.get_active_job()

    async def is_busy(self) -> bool:
        return (
            self._active_task is not None and not self._active_task.done()
        ) or await self.get_active_job() is not None

    async def shutdown(self) -> None:
        async with self._start_lock:
            self._closing = True
            task = self._active_task
            if task is None or task.done():
                return
            active = await self.get_active_job()
            if active is not None:
                await self._repo.update_job(
                    active.job_id, status=JobStatus.FAILED,
                    error=interrupted_error(active.status), current_phase="Interrupted by restart",
                )
                task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=10.0)
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            except asyncio.CancelledError:
                if not task.cancelled():
                    raise

    async def start_job(
        self,
        repo_info: RepoInfo,
        issue_info: IssueInfo,
        telegram_chat_id: int,
        agent_name: str | None = None,
        local_workspace_path: Path | None = None,
        notify_fn: NotifyFn | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        local_only: bool = False,
        branch_name: str | None = None,
        completion_fn: CompletionFn | None = None,
        schedule_source: dict[str, str] | None = None,
    ) -> Job:
        """Atomically reserve the executor's only job slot and start the job."""
        async with self._start_lock:
            return await self._start_job_locked(
                repo_info=repo_info,
                issue_info=issue_info,
                telegram_chat_id=telegram_chat_id,
                agent_name=agent_name,
                local_workspace_path=local_workspace_path,
                notify_fn=notify_fn,
                model=model,
                reasoning_effort=reasoning_effort,
                local_only=local_only,
                branch_name=branch_name,
                completion_fn=completion_fn,
                schedule_source=schedule_source,
            )

    async def _start_job_locked(
        self,
        repo_info: RepoInfo,
        issue_info: IssueInfo,
        telegram_chat_id: int,
        agent_name: str | None = None,
        local_workspace_path: Path | None = None,
        notify_fn: NotifyFn | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        local_only: bool = False,
        branch_name: str | None = None,
        completion_fn: CompletionFn | None = None,
        schedule_source: dict[str, str] | None = None,
    ) -> Job:
        """
        Queue and start a new job.
        Raises RuntimeError if another job is already active.

        Args:
            local_workspace_path: If provided, skip cloning and use this
                                  existing local directory as the workspace.
        """
        if self._closing:
            raise RuntimeError("The executor is shutting down.")
        active = await self._repo.get_active_job()
        if self._active_task is not None and not self._active_task.done() and active is None:
            raise ExecutorBusyError("The previous agent is still finishing cleanup.")
        if active:
            raise ExecutorBusyError(
                f"Another agent is already running.\n\n"
                f"Repository: {active.repo_full_name}\n"
                f"Issue: #{active.issue_number}"
            )

        agent_name = agent_name or settings.DEFAULT_AGENT
        branch = branch_name or make_branch_name(issue_info.number)
        job_id = generate_job_id(repo_info.full_name, issue_info.number)

        job = await self._repo.create_job(
            job_id=job_id,
            repo_full_name=repo_info.full_name,
            issue_number=issue_info.number,
            branch=branch,
            agent=agent_name,
            telegram_chat_id=telegram_chat_id,
            workspace_path=str(local_workspace_path) if local_workspace_path is not None else None,
            local_workspace=int(local_workspace_path is not None),
        )

        await self._persist_start_metadata(
            job_id,
            repo_info,
            issue_info,
            include_issue_body=not local_only,
            execution={
                "local_only": local_only,
                "model": model,
                "reasoning_effort": reasoning_effort,
                "branch": branch,
                "local_schedule": schedule_source,
            },
        )

        notify = notify_fn or self._noop_notify

        # Launch the execution loop as a background task
        self._active_task = asyncio.create_task(
            self._run_job(
                job_id, repo_info, issue_info, agent_name, notify,
                local_workspace_path=local_workspace_path,
                model=model,
                reasoning_effort=reasoning_effort,
                local_only=local_only,
                branch_name=branch,
                completion_fn=completion_fn,
            ),
            name=f"job-{job_id}",
        )
        self._active_task.add_done_callback(self._on_task_done)

        return job

    async def start_local_prompt_job(
        self,
        schedule_id: str,
        project_name: str,
        workspace_path: Path,
        prompt: str,
        telegram_chat_id: int,
        agent_name: str,
        model: str,
        reasoning_effort: str,
        repo_full_name: str | None = None,
        notify_fn: NotifyFn | None = None,
        completion_fn: CompletionFn | None = None,
        schedule_store_path: Path | None = None,
    ) -> Job:
        """Run a CSV prompt through the protected executor without any GitHub action."""
        full_name = repo_full_name or f"local/{project_name}"
        owner, _, repo_name = full_name.partition("/")
        repo_info = RepoInfo(
            full_name=full_name,
            name=repo_name or project_name,
            owner=owner or "local",
            description="Local scheduled prompt",
            clone_url="",
            ssh_url="",
            default_branch="local",
            private=True,
            html_url="",
        )
        issue_info = IssueInfo(
            number=0,
            title=f"Scheduled prompt: {prompt.splitlines()[0][:60]}",
            body=prompt,
            state="open",
            labels=["local-prompt"],
            html_url="",
            repo_full_name=full_name,
        )
        from app.runner.local_prompt_schedules import LocalPromptScheduleStore

        source_path = schedule_store_path or LocalPromptScheduleStore.for_project(workspace_path).path
        return await self.start_job(
            repo_info=repo_info,
            issue_info=issue_info,
            telegram_chat_id=telegram_chat_id,
            agent_name=agent_name,
            local_workspace_path=workspace_path,
            notify_fn=notify_fn,
            model=model,
            reasoning_effort=reasoning_effort,
            local_only=True,
            branch_name=f"{agent_name}/scheduled-{schedule_id.removeprefix('local-')}",
            completion_fn=completion_fn,
            schedule_source={
                "id": schedule_id,
                "path": str(source_path.resolve()),
                "prompt_sha256": hashlib.sha256(prompt.strip().encode("utf-8")).hexdigest(),
            },
        )

    async def stop_active_job(self, notify_fn: NotifyFn | None = None) -> Job | None:
        async with self._start_lock:
            return await self._stop_active_job_locked(notify_fn)

    async def _stop_active_job_locked(self, notify_fn: NotifyFn | None = None) -> Job | None:
        """Request graceful stop of the active job."""
        active = await self._repo.get_active_job()
        if not active:
            return None

        notify = notify_fn or self._noop_notify
        # Completion callbacks must observe the operator's terminal decision.
        job = await self._repo.set_status(active.job_id, JobStatus.STOPPED)

        if self._active_task and not self._active_task.done():
            self._active_task.cancel()
            try:
                await self._active_task
            except asyncio.CancelledError:
                pass

        agent = agent_manager.get_active()
        if agent:
            await agent.stop()

        await self._approvals.cancel_pending(active.job_id)
        agent_manager.clear_active()

        await notify(
            f" Agent stopped\n\n"
            f"Repository: {active.repo_full_name}\n"
            f"Issue: #{active.issue_number}\n\n"
            f"The workspace was preserved for inspection."
        )
        return job

    # ------------------------------------------------------------------
    # Resume after crash recovery
    # ------------------------------------------------------------------

    async def resume_job(
        self, job_id: str, notify_fn: NotifyFn | None = None
    ) -> Job:
        async with self._start_lock:
            return await self._resume_job_locked(job_id, notify_fn)

    async def _resume_job_locked(
        self, job_id: str, notify_fn: NotifyFn | None = None
    ) -> Job:
        """
        Resume an interrupted (restarted) job on its preserved workspace.

        The stored resume bundle (repo + issue metadata) and the workspace
        path must both exist; the job must be marked "Interrupted by restart".
        Raises RuntimeError/ValueError otherwise. The single-active-job rule
        still applies.
        """
        if self._closing:
            raise RuntimeError("The executor is shutting down.")
        job = await self._repo.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        if not is_resumable(job):
            raise ValueError(
                f"Job {job_id} is not resumable — only jobs interrupted by a "
                "restart or paused due to token limits can be resumed."
            )
        if not job.workspace_path or not Path(job.workspace_path).is_dir():
            raise ValueError(f"Workspace for job {job_id} is missing.")

        state = await self._state_repo.get_state(job_id)
        if state is None or not state.metadata:
            raise ValueError(f"Resume metadata for job {job_id} is missing.")

        active = await self._repo.get_active_job()
        if self._active_task is not None and not self._active_task.done() and active is None:
            raise ExecutorBusyError("The previous agent is still finishing cleanup.")
        if active:
            raise ExecutorBusyError(
                f"Another agent is already running.\n\n"
                f"Repository: {active.repo_full_name}\n"
                f"Issue: #{active.issue_number}"
            )

        repo_info, issue_info = self._restore_bundle(state.metadata)
        notify = notify_fn or self._noop_notify
        execution = state.metadata.get("execution") or {}
        local_only = bool(execution.get("local_only")) or "local-prompt" in issue_info.labels
        completion_fn = None
        if local_only:
            from app.runner.local_prompt_schedules import LocalPromptScheduleStore

            source = execution.get("local_schedule") or {}
            if not source.get("path") or not source.get("id") or not source.get("prompt_sha256"):
                raise ValueError("Local task resume source is missing; create a new schedule safely.")
            store = LocalPromptScheduleStore(Path(source["path"]))
            schedule = await store.get(source["id"])
            if schedule is None:
                raise ValueError("The original local schedule is missing.")
            fingerprint = hashlib.sha256(schedule.prompt.strip().encode("utf-8")).hexdigest()
            if fingerprint != source["prompt_sha256"]:
                raise ValueError("The local prompt has changed; create a new schedule instead of resuming.")
            if schedule.chat_id != job.telegram_chat_id:
                raise ValueError("The local schedule owner has changed.")
            issue_info.body = schedule.prompt
            if not await store.mark_resumed(schedule.id):
                raise ValueError("The local schedule cannot be resumed in its current state.")

            async def completed_local(resumed_job: Job) -> None:
                if is_resumable(resumed_job):
                    return
                if resumed_job.status == JobStatus.COMPLETED:
                    await store.mark_completed(schedule.id)
                elif resumed_job.status in JobStatus.TERMINAL_STATES:
                    await store.mark_failed(schedule.id, resumed_job.error or "Job failed")

            completion_fn = completed_local

        # Reset the row for a fresh run (keeps the same job_id & workspace).
        await self._repo.update_job(
            job_id,
            status=JobStatus.QUEUED,
            error=None,
            finished_at=None,
            current_phase=None,
        )

        self._active_task = asyncio.create_task(
            self._run_job(
                job_id,
                repo_info,
                issue_info,
                job.agent,
                notify,
                local_workspace_path=Path(job.workspace_path),
                local_only=local_only,
                model=execution.get("model"),
                reasoning_effort=execution.get("reasoning_effort"),
                branch_name=execution.get("branch") or job.branch,
                completion_fn=completion_fn,
            ),
            name=f"job-{job_id}",
        )
        self._active_task.add_done_callback(self._on_task_done)

        await notify(
            f" Resuming interrupted job {job.job_id}\n\n"
            f"Repository: {job.repo_full_name}\n"
            f"Issue: #{job.issue_number}\n"
            f"Branch: {job.branch}\n"
            f"Workspace: `{job.workspace_path}`"
        )
        return await self._repo.get_job(job_id)  # type: ignore[return-value]

    async def decide_approval(
        self, approval_id: int, decision: str, decision_by: int
    ) -> str:
        """Route an operator Approve/Reject callback into the coordinator."""
        record = await ApprovalRepository(self._db).get(approval_id)
        workspace_path = None
        if record is not None:
            job = await self._repo.get_job(record.job_id)
            workspace_path = job.workspace_path if job else None
        return await self._approvals.decide(
            approval_id, decision, decision_by, workspace_path
        )

    async def _persist_start_metadata(
        self,
        job_id: str,
        repo_info: RepoInfo,
        issue_info: IssueInfo,
        include_issue_body: bool = True,
        execution: dict[str, Any] | None = None,
    ) -> None:
        metadata = {
            "execution": execution or {},
            "repo": {
                "full_name": repo_info.full_name,
                "name": repo_info.name,
                "owner": repo_info.owner,
                "description": repo_info.description,
                "clone_url": repo_info.clone_url,
                "ssh_url": repo_info.ssh_url,
                "default_branch": repo_info.default_branch,
                "private": repo_info.private,
                "html_url": repo_info.html_url,
            },
            "issue": {
                "number": issue_info.number,
                "title": issue_info.title,
                "body": issue_info.body if include_issue_body else "",
                "state": issue_info.state,
                "labels": list(issue_info.labels),
                "html_url": issue_info.html_url,
                "repo_full_name": issue_info.repo_full_name,
            },
        }
        await self._state_repo.set_state(job_id, stage="queued", metadata=metadata)

    @staticmethod
    def _restore_bundle(metadata: dict[str, Any]) -> tuple[RepoInfo, IssueInfo]:
        repo = metadata.get("repo") or {}
        issue = metadata.get("issue") or {}
        repo_info = RepoInfo(
            full_name=str(repo.get("full_name")),
            name=str(repo.get("name")),
            owner=str(repo.get("owner")),
            description=str(repo.get("description") or ""),
            clone_url=str(repo.get("clone_url")),
            ssh_url=str(repo.get("ssh_url") or ""),
            default_branch=str(repo.get("default_branch")),
            private=bool(repo.get("private")),
            html_url=str(repo.get("html_url")),
        )
        issue_info = IssueInfo(
            number=int(issue.get("number") or 0),
            title=str(issue.get("title") or ""),
            body=str(issue.get("body") or ""),
            state=str(issue.get("state") or "open"),
            labels=list(issue.get("labels") or []),
            html_url=str(issue.get("html_url") or ""),
            repo_full_name=str(issue.get("repo_full_name")),
        )
        return repo_info, issue_info

    @staticmethod
    def _approval_keyboard(approval_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("موافقة (Approve)", callback_data=f"approve:{approval_id}"),
                InlineKeyboardButton("رفض (Reject)", callback_data=f"reject:{approval_id}"),
            ]
        ])

    # ------------------------------------------------------------------
    # Internal orchestration
    # ------------------------------------------------------------------

    async def _run_job(
        self,
        job_id: str,
        repo_info: RepoInfo,
        issue_info: IssueInfo,
        agent_name: str,
        notify_fn: NotifyFn,
        local_workspace_path: Path | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        local_only: bool = False,
        branch_name: str | None = None,
        completion_fn: CompletionFn | None = None,
    ) -> None:
        """Full end-to-end job execution pipeline."""
        repo = self._repo
        delivery_notify = notify_fn

        async def notify(message: str, reply_markup: Any = None) -> None:
            """Deliver agent-facing text only after redacting secret-like values."""
            await self._send_notification(delivery_notify, message, reply_markup=reply_markup)

        progress = ProgressReporter(self._db, job_id, notify_fn=notify)
        classifier = RiskClassifier(settings.protected_paths_list)
        approval_policy = ApprovalPolicy()
        router = AgentRouter(
            self._db,
            policy=FallbackPolicy(mode="disabled") if local_only else None,
        )
        self._router = router
        verification = VerificationPipeline(self._db)
        self._verification = verification

        async def update(status: str, phase: str | None = None, **kwargs) -> None:
            await repo.set_status(job_id, status, phase)
            if kwargs:
                await repo.update_job(job_id, **kwargs)
            await progress.report(f"Stage: {status}", level="INFO", phase=phase or status, notify=False)

        owner, repo_name = repo_info.full_name.split("/", 1)
        branch = branch_name or make_branch_name(issue_info.number)

        try:
            if local_workspace_path is not None:
                # -------------------------------------------------------
                # LOCAL PATH MODE: skip clone, use the provided directory
                # -------------------------------------------------------
                workspace_path = local_workspace_path
                await update(JobStatus.CLONING, "Using local path")
                await notify(
                    f" Agent started (local workspace)\n\n"
                    f"Repository: {repo_info.full_name}\n"
                    f"Issue: #{issue_info.number}\n"
                    f"Branch: {branch}\n"
                    f"Workspace: `{workspace_path}`"
                )
                # Configure git identity and create branch in the local repo
                await git_service.configure_identity(
                    workspace_path, "AI Agent", "ai-agent@noreply.local"
                )
                await git_service.create_branch(workspace_path, branch)
            else:
                # -------------------------------------------------------
                # CLONE MODE: clone or pull, then create branch
                # -------------------------------------------------------
                await update(JobStatus.CLONING, "Cloning repository")
                await notify(
                    f" Agent started\n\n"
                    f"Repository: {repo_info.full_name}\n"
                    f"Issue: #{issue_info.number}\n"
                    f"Branch: {branch}"
                )

                workspace_path = await self._workspace.clone_or_update(
                    clone_url=repo_info.clone_url,
                    owner=owner,
                    repo=repo_name,
                )
                await git_service.configure_identity(
                    workspace_path, "AI Agent", "ai-agent@noreply.local"
                )
                await git_service.create_branch(workspace_path, branch)

            # Persist the workspace path so crash-recovery / resume can find it.
            await repo.update_job(
                job_id,
                workspace_path=str(workspace_path),
                local_workspace=1 if local_workspace_path is not None else 0,
            )
            await self._state_repo.set_state(
                job_id, stage="inspecting", metadata={"workspace_path": str(workspace_path)}
            )

            # --- Phase: INSPECTING ----------------------------------------
            await update(JobStatus.INSPECTING, "Inspecting repository")
            await notify(" Inspecting repository...")

            context = context_builder.build(
                repo_info=repo_info,
                issue_info=issue_info,
                branch=branch,
                workspace_path=workspace_path,
                max_fix_iterations=settings.MAX_FIX_ITERATIONS,
                model=model,
                reasoning_effort=reasoning_effort,
            )

            # --- Phase: IMPLEMENTING ----------------------------------------
            await update(JobStatus.IMPLEMENTING, "Running agent")
            await notify(" Running AI agent...")

            async def on_agent_progress(msg: str) -> None:
                await progress.report(msg, level="INFO", phase="agent")

            timeout_secs = settings.MAX_AGENT_RUNTIME_MINUTES * 60
            try:
                outcome = await router.run(
                    job_id=job_id,
                    primary=agent_name,
                    context=context,
                    workspace_path=workspace_path,
                    agent_factory=agent_manager.get_agent,
                    timeout_secs=timeout_secs,
                    on_progress=on_agent_progress,
                    progress=progress,
                )
            except asyncio.CancelledError:
                logger.info("Job %s was cancelled", job_id)
                return

            result = outcome.result
            if not result.success:
                if getattr(result, "token_exhausted", False) or outcome.code == FailureCode.RATE_LIMIT:
                    err_msg = token_limit_error()
                    await update(JobStatus.FAILED, "Paused (Token Limit)", error=err_msg)
                    kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("التبديل إلى حساب آخر", callback_data="menu:accounts")],
                        [InlineKeyboardButton("استئناف العمل فوراً", callback_data=f"acc_resume:{job_id}")],
                    ])
                    await notify(
                        f" *توقف الـ Agent مؤقتاً: نفاد رصيد الـ Tokens!*\n\n"
                        f" *المستودع:* `{repo_info.full_name}`\n"
                        f" *المهمة:* #{issue_info.number}\n"
                        f" *الفرع:* `{branch}`\n\n"
                        f" تم حفظ مساحة العمل والتعديلات بأمان.\n"
                        f"يمكنك التبديل إلى حساب Antigravity آخر عبر قائمة الحسابات ثم استئناف العمل ليكمل الـ Agent من حيث توقف دون إعادة العمل.",
                        reply_markup=kb,
                    )
                    return
                if outcome.code == FailureCode.TIMEOUT:
                    await update(JobStatus.FAILED, "Timed out", error="Agent exceeded time limit")
                    await notify(
                        f" Agent timed out after {settings.MAX_AGENT_RUNTIME_MINUTES} minutes."
                    )
                    return
                error_msg = result.error or "Agent returned non-zero exit code"
                await update(JobStatus.FAILED, "Agent failed", error=error_msg[:500])
                await notify(
                    f" Agent failed\n\n{error_msg[:300]}"
                )
                return

            await notify(" Agent completed implementation!")

            # -----------------------------------------------------------
            # Change detection (before verification — nothing to verify if empty)
            # -----------------------------------------------------------
            git_status = await git_service.status(workspace_path)
            if not git_status.has_changes:
                await update(
                    JobStatus.FAILED,
                    "No changes",
                    error="Agent completed but made no file changes",
                )
                await notify(
                    " Agent finished but made no file changes. Nothing to commit."
                )
                return

            changed_files = [*git_status.changed_files, *git_status.untracked]
            await repo.update_job(job_id, files_changed=len(changed_files))
            await notify(
                f"🤖 *أنهى الـ Agent التعديلات البرمجية!*\n"
                f"📋 *ملخص ما تم:* {result.summary[:300] if result.summary else 'تم إنجاز التعديلات'}\n"
                f"📝 *الملفات المعدلة:* {len(changed_files)} ملف(اً)\n"
                "⏳ جاري الفحص والتحقق..."
            )

            # -----------------------------------------------------------
            # Phase: VERIFYING — controller-enforced checks
            # -----------------------------------------------------------
            await update(JobStatus.VERIFYING, "Running verification checks")
            await notify(" Running controller verification checks...")

            vresult = await verification.run(job_id, workspace_path, progress=progress)
            await progress.report(vresult.summary(), phase="verification", notify=False)
            if vresult.blocking:
                await update(
                    JobStatus.FAILED,
                    "Verification failed",
                    error=f"required check failed:\n{vresult.summary(300)}",
                )
                await notify(
                    f" Verification blocked this job.\n\n{vresult.summary(400)}"
                )
                return
            await notify(f" Verification passed.\n\n{vresult.summary(400)}")

            # -----------------------------------------------------------
            # Risk classification for the approval gates
            # -----------------------------------------------------------
            # `changed_files` includes untracked files, so a freshly-created
            # .env or agent.db is both classified and shown for review.
            risk = classifier.classify(changed_files)

            async def approval_gate(stage: str, gate: str) -> bool:
                """Request operator approval when the policy requires it."""
                if not approval_policy.gate_required(gate, risk):
                    return True
                diff_preview = await git_service.review_diff(workspace_path)
                diff_preview = diff_preview.replace("```", "'''")[:1600]
                changed_list = "\n".join(f"- `{name}`" for name in changed_files[:20])
                record = await self._approvals.request(
                    job_id, gate, risk, str(workspace_path)
                )
                await update(
                    JobStatus.AWAITING_APPROVAL,
                    f"Awaiting approval ({gate}, risk {risk})",
                )
                await notify(
                    f"*موافقة مطلوبة قبل {stage}*\n"
                    f"الفرع: `{branch}`\n"
                    f"المخاطر: `{risk}`\n"
                    f"الملفات المقترحة:\n{changed_list}\n\n"
                    f"معاينة مختصرة للفرق (staged/unstaged والملفات الجديدة):\n```\n{diff_preview or 'لا يوجد فرق قابل للعرض'}\n```\n\n"
                    "اختر الموافقة للمتابعة أو الرفض لإيقاف المهمة.",
                    reply_markup=self._approval_keyboard(record.id),
                )
                outcome = await self._approvals.wait(record.id)
                if outcome == APPROVED:
                    await progress.report(
                        f"Approval granted for {gate}", phase="approval", notify=False
                    )
                    return True
                error_desc = {
                    "REJECTED": "the approval was rejected",
                    "STALE": "the approval became stale because the code changed",
                    "EXPIRED": "the approval request expired",
                }.get(outcome, "the approval was not granted")
                await update(
                    JobStatus.FAILED,
                    "Approval required",
                    error=f"{gate}: {error_desc}",
                )
                await notify(
                    f" Job stopped — {error_desc}.\n\n"
                    f"The workspace was preserved for inspection."
                )
                return False

            if not await approval_gate("before commit", BEFORE_COMMIT):
                return

            # --- Phase: COMMITTING ----------------------------------------
            await update(JobStatus.COMMITTING, "Committing changes")
            await notify(" Creating commit...")

            commit_msg = (
                "chore: scheduled local task"
                if local_only
                else make_commit_message(issue_info.title, issue_info.number)
            )
            try:
                await git_service.commit(workspace_path, commit_msg)
            except SecretDetectedError as exc:
                await update(JobStatus.FAILED, "Secret detected", error=str(exc)[:300])
                await notify(
                    " Commit blocked: potential secret detected in changes.\n"
                    "Please review the workspace manually."
                )
                return

            if local_only:
                await update(JobStatus.COMPLETED, "Completed local prompt")
                msg = _format_completion_message(
                    status_header="✅ *اكتملت المهمة المحلية بنجاح!*",
                    repo_name=repo_info.name,
                    task_title=issue_info.title,
                    branch=branch,
                    summary=result.summary,
                    changed_files=changed_files,
                )
                await self._send_notification(notify, msg)
                return

            # --- Phase: PUSHING -------------------------------------------
            if not await approval_gate("before push", BEFORE_PUSH):
                return
            await update(JobStatus.PUSHING, "Pushing branch")
            await notify(f" Pushing branch {branch}...")

            await git_service.push(workspace_path, branch=branch)

            # --- Phase: CREATING_PR ----------------------------------------
            if not await approval_gate("before pull request", BEFORE_PR):
                return
            await update(JobStatus.CREATING_PR, "Creating Pull Request")
            await notify(" Creating Pull Request...")

            pr_title = make_pr_title(issue_info.title)
            validation_results = (
                " Agent reported success\n"
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

            # Check if auto-merge is enabled
            if getattr(settings, "AUTO_MERGE_PR", False):
                await update("MERGING_PR", "Merging Pull Request")
                await notify(" Auto-merging Pull Request...")
                merge_method: Literal["squash", "merge", "rebase"] = settings.DEFAULT_MERGE_METHOD
                merge_result = pr_service.merge_pr(
                    repo_full_name=repo_info.full_name,
                    number=pr.number,
                    commit_title=f"Merge PR #{pr.number}: {pr_title}",
                    merge_method=merge_method,
                )
                if merge_result.merged:
                    await update(JobStatus.COMPLETED, "Completed & Merged")
                    msg = _format_completion_message(
                        status_header="✅ *اكتملت المهمة وتم دمج طلب السحب بنجاح!*",
                        repo_name=repo_info.full_name,
                        task_title=f"#{issue_info.number} - {issue_info.title}",
                        branch=branch,
                        summary=result.summary,
                        changed_files=changed_files,
                        pr_number=pr.number,
                        pr_url=pr.html_url,
                        merge_method=merge_method,
                        sha=merge_result.sha,
                    )
                    await self._send_notification(notify, msg)
                    return
                else:
                    logger.warning(
                        "Auto-merge failed for PR #%d in %s: %s",
                        pr.number, repo_info.full_name, merge_result.message,
                    )

            await update(JobStatus.COMPLETED, "Completed")

            keyboard = pr_action_keyboard(
                pr_url=pr.html_url,
                repo_full_name=repo_info.full_name,
                pr_number=pr.number,
            )

            msg = _format_completion_message(
                status_header="✅ *اكتمل عمل الـ Agent بنجاح وتم إنشاء طلب السحب!*",
                repo_name=repo_info.full_name,
                task_title=f"#{issue_info.number} - {issue_info.title}",
                branch=branch,
                summary=result.summary,
                changed_files=changed_files,
                pr_number=pr.number,
                pr_url=pr.html_url,
            )
            await self._send_notification(
                notify,
                msg,
                reply_markup=keyboard,
            )

        except asyncio.CancelledError:
            logger.info("Job %s cancelled", job_id)
            raise

        except TimeoutError as exc:
            logger.error("Job %s timed out during operation: %s", job_id, exc)
            error_str = f"Operation timed out: {exc}"
            try:
                await repo.update_job(job_id, error=error_str[:500])
                await repo.set_status(job_id, JobStatus.FAILED, phase="Timed out")
                await notify(
                    f" Operation timed out to protect resources:\n\n"
                    f"`{error_str[:250]}`"
                )
            except Exception:
                pass

        except Exception as exc:
            logger.exception("Job %s failed with unexpected error: %s", job_id, exc)
            error_str = str(exc)[:500]
            try:
                await repo.update_job(job_id, error=error_str)
                await repo.set_status(job_id, JobStatus.FAILED)
                await notify(
                    f" Job failed with an unexpected error.\n\n"
                    f"{error_str[:200]}"
                )
            except Exception:
                pass

        finally:
            agent_manager.clear_active()
            # Any approval still awaiting a decision belongs to this job
            # which is now finished — cancel it so stale callbacks are refused.
            await self._approvals.cancel_pending(job_id)
            if completion_fn is not None:
                completed = await repo.get_job(job_id)
                if completed is not None:
                    try:
                        await completion_fn(completed)
                    except Exception as exc:
                        logger.warning("Job completion callback failed: %s", exc)

    def _on_task_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            logger.info("Job task was cancelled")
        elif task.exception():
            logger.error("Job task raised: %s", task.exception())
        from app.runner.scheduler import job_scheduler

        job_scheduler.wake()

    @staticmethod
    async def _noop_notify(msg: str, *args: Any, **kwargs: Any) -> None:
        from app.utils.security import sanitize_for_telegram

        logger.info("[notify] %s", sanitize_for_telegram(msg))

    @staticmethod
    async def _send_notification(notify: NotifyFn, msg: str, reply_markup: Any = None) -> None:
        import inspect

        from app.utils.security import sanitize_for_telegram

        safe_msg = sanitize_for_telegram(msg)
        try:
            sig = inspect.signature(notify)
            if len(sig.parameters) >= 2 or any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values()):
                await notify(safe_msg, reply_markup=reply_markup)  # type: ignore[call-arg]
            else:
                await notify(safe_msg)
        except Exception as exc:
            logger.warning("Failed to deliver notification with markup: %s, falling back", exc)
            try:
                await notify(safe_msg)
            except Exception:
                pass
