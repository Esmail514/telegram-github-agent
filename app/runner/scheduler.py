"""
Background scheduler loop — checks for due scheduled jobs every 60 seconds
and fires them via the JobExecutor.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class JobScheduler:
    """
    Background asyncio loop that polls the database every POLL_INTERVAL seconds
    and launches any PENDING scheduled jobs whose time has arrived.
    """

    POLL_INTERVAL = 60  # seconds

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._wake_event: asyncio.Event | None = None
        self._tick_lock = asyncio.Lock()
        self._csv_errors: dict[Path, str] = {}

    def start(self, app_context: dict) -> None:
        """Start the scheduler loop. Call this once after the bot is running."""
        if self._task is not None and not self._task.done():
            return
        self._wake_event = asyncio.Event()
        self._task = asyncio.get_event_loop().create_task(
            self._loop(app_context), name="job-scheduler"
        )
        logger.info("Job scheduler started (poll interval: %ds)", self.POLL_INTERVAL)

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("Job scheduler stopped")

    async def shutdown(self) -> None:
        task = self._task
        self.stop()
        if task is None or task is asyncio.current_task():
            return
        try:
            await task
        except asyncio.CancelledError:
            if not task.cancelled():
                raise
        finally:
            if self._task is task and task.done():
                self._task = None

    def wake(self) -> None:
        """Request an immediate queue check after an active job completes."""
        if self._wake_event is not None:
            self._wake_event.set()

    async def _loop(self, app_context: dict) -> None:
        while True:
            try:
                if self._wake_event is not None:
                    self._wake_event.clear()
                await self._tick(app_context)
                if self._wake_event is None:
                    await asyncio.sleep(self.POLL_INTERVAL)
                    continue
                try:
                    await asyncio.wait_for(self._wake_event.wait(), timeout=self.POLL_INTERVAL)
                except TimeoutError:
                    pass
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Scheduler tick error: %s", exc)
                await asyncio.sleep(self.POLL_INTERVAL)

    async def _tick(self, app_context: dict) -> None:
        async with self._tick_lock:
            await self._dispatch_due(app_context)

    async def _dispatch_due(self, app_context: dict) -> None:
        """Fire all due PENDING scheduled jobs."""
        from app.database.schedule_repository import ScheduleRepository
        from app.github.client import github_client
        from app.github.issues import issue_service
        from app.runner.local_prompt_schedules import LocalPromptSchedule, LocalPromptScheduleStore

        db = app_context.get("db")
        executor = app_context.get("executor")
        bot = app_context.get("bot")
        if db is None or executor is None:
            return

        # All agent executions share a single slot. Keep due tasks pending until
        # the current one finishes instead of marking them launched and losing them.
        if await executor.get_active_job() is not None:
            return

        # Read the host's clock once per tick. Stored schedules remain UTC so
        # they compare correctly regardless of the device timezone.
        current_device_time = datetime.now().astimezone()
        current_utc_time = current_device_time.astimezone(UTC)

        local_stores = await LocalPromptScheduleStore.discover(db)
        local_due: list[tuple[LocalPromptSchedule, LocalPromptScheduleStore]] = []
        for local_store in local_stores:
            try:
                schedules, errors = await local_store.list_due_with_errors(before=current_utc_time)
                local_due.extend(
                    (schedule, local_store)
                    for schedule in schedules
                )
                self._csv_errors.pop(local_store.path, None)
                if errors:
                    await self._notify_csv_error(bot, local_store.path, len(errors))
            except (ValueError, OSError) as exc:
                logger.error("Local prompt CSV is invalid: %s", exc)
                if self._csv_errors.get(local_store.path) != str(exc):
                    self._csv_errors[local_store.path] = str(exc)
                    await self._notify_csv_error(bot, local_store.path)
        local_due.sort(key=lambda item: item[0].scheduled_dt)
        sched_repo = ScheduleRepository(db)
        due_jobs = await sched_repo.list_pending(before=current_utc_time)
        if local_due and (not due_jobs or local_due[0][0].scheduled_dt <= due_jobs[0].scheduled_dt):
            schedule, local_store = local_due[0]
            await self._launch_local_prompt(schedule, local_store, app_context)
            return

        for sj in due_jobs:
            if await executor.get_active_job() is not None:
                return
            logger.info(
                "Scheduler firing: repo=%s issue=#%d agent=%s",
                sj.repo_full_name, sj.issue_number, sj.agent,
            )
            # Check if this job was overdue (e.g. while the bot was offline)
            time_diff = (current_utc_time - sj.scheduled_dt).total_seconds()
            is_recovered = time_diff > 120

            try:
                # Fetch repo & issue info
                repo_obj = await asyncio.to_thread(github_client.get_repo, sj.repo_full_name)
                from app.github.client import RepoInfo
                repo_info = RepoInfo.from_github(repo_obj)

                issue_info = await asyncio.to_thread(
                    issue_service.get_issue, sj.repo_full_name, sj.issue_number
                )

                # Build notify function targeting the right chat
                async def _notify(msg: str, chat_id: int = sj.chat_id) -> None:
                    if bot:
                        try:
                            await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
                        except Exception:
                            pass

                local_workspace_path = Path(sj.workspace_path) if sj.workspace_path else None
                if sj.project_id:
                    if sj.project_id.startswith("saved:"):
                        from app.database.repository import SavedProjectRepository

                        saved_project = await SavedProjectRepository(db).get_by_id(
                            int(sj.project_id.removeprefix("saved:"))
                        )
                        if saved_project is None or not Path(saved_project.path).is_dir():
                            raise RuntimeError("The saved local project could not be found")
                        local_workspace_path = Path(saved_project.path)
                    else:
                        from app.config.settings import settings
                        from app.runner.local_projects import LocalProjectRegistry

                        if not settings.PROJECTS_DIR:
                            raise RuntimeError("PROJECTS_DIR is not configured")
                        legacy_project = LocalProjectRegistry(settings.PROJECTS_DIR).resolve(sj.project_id)
                        if legacy_project is None:
                            raise RuntimeError("The saved local project could not be found")
                        local_workspace_path = legacy_project.path

                if not await sched_repo.mark_launched(sj.id):
                    continue

                async def on_complete(job) -> None:
                    self.wake()

                try:
                    await executor.start_job(
                        repo_info=repo_info,
                        issue_info=issue_info,
                        telegram_chat_id=sj.chat_id,
                        agent_name=sj.agent,
                        model=sj.model,
                        reasoning_effort=sj.reasoning_effort,
                        local_workspace_path=local_workspace_path,
                        notify_fn=_notify,
                        completion_fn=on_complete,
                    )
                except BaseException:
                    await sched_repo.release_launch(sj.id)
                    raise
                if bot:
                    header = "استئناف مهمة مجدولة" if is_recovered else "بدء تنفيذ مهمة مجدولة"
                    try:
                        await bot.send_message(
                            chat_id=sj.chat_id,
                            text=(
                                f"*{header}*\n"
                                f"المستودع: `{sj.repo_full_name}`\n"
                                f"Issue: `#{sj.issue_number}`\n"
                                f"المشروع: `{sj.project_name or '—'}`"
                            ),
                            parse_mode="Markdown",
                        )
                    except Exception as notify_err:
                        logger.warning("Could not notify user %d: %s", sj.chat_id, notify_err)
                return

            except RuntimeError as exc:
                # Another job is already active — leave as-is but notify
                logger.warning("Scheduler could not start job: %s", exc)
                if bot:
                    try:
                        await bot.send_message(
                            chat_id=sj.chat_id,
                            text=(
                                f" *Scheduled Job Skipped*\n\n"
                                f" `{sj.repo_full_name}` Issue #{sj.issue_number}\n\n"
                                f"Reason: {exc}"
                            ),
                            parse_mode="Markdown",
                        )
                    except Exception:
                        pass
            except Exception as exc:
                logger.exception(
                    "Failed to launch scheduled job id=%d: %s", sj.id, exc
                )

    async def _launch_local_prompt(self, schedule, store, app_context: dict) -> None:
        """Start one CSV-backed local Codex prompt and wake the queue on completion."""
        from app.config.settings import settings
        from app.database.repository import JobStatus
        from app.runner.executor import ExecutorBusyError

        bot = app_context.get("bot")
        db = app_context.get("db")
        if schedule.chat_id != settings.TELEGRAM_ALLOWED_USER_ID:
            await store.mark_failed(schedule.id, "Local schedule owner is not the configured operator")
            await self._notify_local_failure(bot, schedule, "تم إيقاف جدولة لا تتطابق مع المستخدم المصرح له.")
            return
        project_path: Path | None = None
        project_name = schedule.project_name
        repo_full_name: str | None = None
        if db is not None and schedule.project_id.startswith("saved:"):
            from app.database.repository import SavedProjectRepository

            saved = await SavedProjectRepository(db).get_by_id(int(schedule.project_id.removeprefix("saved:")))
            if saved is not None and saved.user_id != schedule.chat_id:
                await store.mark_failed(schedule.id, "Local schedule owner does not own the saved project")
                await self._notify_local_failure(bot, schedule, "تم إيقاف جدولة لا يتطابق مستخدمها مع مالك المشروع.")
                return
            if saved is not None and Path(saved.path).is_dir():
                project_path = Path(saved.path)
                project_name = saved.name
                repo_full_name = saved.repo_full_name
        elif not schedule.project_id.startswith("saved:"):
            from app.config.settings import settings
            from app.runner.local_projects import LocalProjectRegistry

            if settings.PROJECTS_DIR:
                legacy = LocalProjectRegistry(settings.PROJECTS_DIR).resolve(schedule.project_id)
                if legacy is not None:
                    project_path = legacy.path
                    project_name = legacy.name
                    repo_full_name = legacy.repo_full_name
        if project_path is None:
            await store.mark_failed(schedule.id, "The saved local project could not be found")
            await self._notify_local_failure(bot, schedule, "لم يتم العثور على المشروع المحلي.")
            return
        if not await store.mark_launched(schedule.id):
            return

        executor = app_context.get("executor")
        if executor is None:
            await store.mark_failed(schedule.id, "Job executor is unavailable")
            return

        async def notify(message: str, reply_markup=None) -> None:
            if bot is None:
                return
            try:
                await bot.send_message(
                    chat_id=schedule.chat_id,
                    text=message,
                    parse_mode="Markdown",
                    reply_markup=reply_markup,
                )
            except Exception as exc:
                logger.warning("Could not send local schedule notification: %s", exc)

        async def on_complete(job) -> None:
            from app.runner.recovery import is_resumable

            if job.status == JobStatus.COMPLETED:
                await store.mark_completed(schedule.id)
            elif job.status in JobStatus.TERMINAL_STATES and not is_resumable(job):
                await store.mark_failed(schedule.id, job.error or job.current_phase or "Job failed")
            self.wake()

        try:
            await executor.start_local_prompt_job(
                schedule_id=schedule.id,
                schedule_store_path=store.path,
                project_name=project_name,
                workspace_path=project_path,
                prompt=schedule.prompt,
                telegram_chat_id=schedule.chat_id,
                agent_name=schedule.agent,
                model=schedule.model,
                reasoning_effort=schedule.reasoning_effort,
                repo_full_name=repo_full_name,
                notify_fn=notify,
                completion_fn=on_complete,
            )
        except ExecutorBusyError:
            await store.release_launch(schedule.id)
            logger.info("Local scheduled prompt will retry after the active agent finishes cleanup")
        except Exception as exc:
            logger.exception("Could not launch local scheduled prompt: %s", exc)
            await store.mark_failed(schedule.id, str(exc))
            await self._notify_local_failure(bot, schedule, "تعذر بدء Prompt المحلي.")

    @staticmethod
    async def _notify_csv_error(bot, path: Path, invalid_rows: int | None = None) -> None:
        if bot is None:
            return
        from app.config.settings import settings

        if invalid_rows is None:
            text = f"تعذر قراءة ملف جدولة محلي. تحقق من ترميز UTF-8 والأعمدة المطلوبة.\n{path}"
        else:
            text = (
                f"تم إيقاف {invalid_rows} صف غير صالح في ملف الجدولة، دون تعطيل المهام السليمة. "
                f"صحح البيانات ثم غيّر الحالة إلى PENDING لإعادة الجدولة.\n{path}"
            )
        try:
            await bot.send_message(chat_id=settings.TELEGRAM_ALLOWED_USER_ID, text=text)
        except Exception as exc:
            logger.warning("Could not send CSV validation notification: %s", exc)

    @staticmethod
    async def _notify_local_failure(bot, schedule, message: str) -> None:
        if bot is None:
            return
        from app.config.settings import settings

        try:
            await bot.send_message(chat_id=settings.TELEGRAM_ALLOWED_USER_ID, text=message)
        except Exception as exc:
            logger.warning("Could not send local schedule failure notification: %s", exc)


# Module-level singleton
job_scheduler = JobScheduler()
