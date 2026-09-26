"""
Background scheduler loop — checks for due scheduled jobs every 60 seconds
and fires them via the JobExecutor.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


class JobScheduler:
    """
    Background asyncio loop that polls the database every POLL_INTERVAL seconds
    and launches any PENDING scheduled jobs whose time has arrived.
    """

    POLL_INTERVAL = 60  # seconds

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    def start(self, app_context: dict) -> None:
        """Start the scheduler loop. Call this once after the bot is running."""
        self._task = asyncio.get_event_loop().create_task(
            self._loop(app_context), name="job-scheduler"
        )
        logger.info("Job scheduler started (poll interval: %ds)", self.POLL_INTERVAL)

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("Job scheduler stopped")

    async def _loop(self, app_context: dict) -> None:
        while True:
            try:
                await self._tick(app_context)
                await asyncio.sleep(self.POLL_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Scheduler tick error: %s", exc)
                await asyncio.sleep(self.POLL_INTERVAL)

    async def _tick(self, app_context: dict) -> None:
        """Fire all due PENDING scheduled jobs."""
        from app.database.schedule_repository import ScheduleRepository
        from app.github.client import github_client
        from app.github.issues import issue_service

        db = app_context.get("db")
        executor = app_context.get("executor")
        bot = app_context.get("bot")
        if db is None or executor is None:
            return

        sched_repo = ScheduleRepository(db)
        due_jobs = await sched_repo.list_pending(before=datetime.now(UTC))

        for sj in due_jobs:
            logger.info(
                "Scheduler firing: repo=%s issue=#%d agent=%s",
                sj.repo_full_name, sj.issue_number, sj.agent,
            )
            # Check if this job was overdue (e.g. while the bot was offline)
            time_diff = (datetime.now(UTC) - sj.scheduled_dt).total_seconds()
            is_recovered = time_diff > 120

            try:
                # Mark as launched immediately to prevent re-firing
                await sched_repo.mark_launched(sj.id)

                # Notify the user
                if bot:
                    if is_recovered:
                        header = "⏰ *استئناف مهمة مجدولة*\n⚠️ _فات موعدها أثناء إيقاف البوت وتم استئنافها الآن_"
                    else:
                        header = "⏰ *بدء تنفيذ مهمة مجدولة*"

                    msg_text = (
                        f"{header}\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"📦 *المستودع:* `{sj.repo_full_name}`\n"
                        f"📌 *الـ Issue:* `#{sj.issue_number}`\n"
                        f"🤖 *الـ Agent:* `{sj.agent}`\n"
                        f"🆔 *رقم الجدولة:* `#{sj.id}`\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "🚀 يبدأ الـ Agent الآن التحليل والحل تلقائياً..."
                    )
                    try:
                        await bot.send_message(
                            chat_id=sj.chat_id,
                            text=msg_text,
                            parse_mode="Markdown",
                        )
                    except Exception as notify_err:
                        logger.warning("Could not notify user %d: %s", sj.chat_id, notify_err)

                # Fetch repo & issue info
                repo_obj = await asyncio.get_event_loop().run_in_executor(
                    None, lambda name=sj.repo_full_name: github_client.get_repo(name)
                )
                from app.github.client import RepoInfo
                repo_info = RepoInfo.from_github(repo_obj)

                issue_info = await asyncio.get_event_loop().run_in_executor(
                    None, lambda name=sj.repo_full_name, num=sj.issue_number: issue_service.get_issue(name, num)
                )

                # Build notify function targeting the right chat
                async def _notify(msg: str, chat_id: int = sj.chat_id) -> None:
                    if bot:
                        try:
                            await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
                        except Exception:
                            pass

                await executor.start_job(
                    repo_info=repo_info,
                    issue_info=issue_info,
                    telegram_chat_id=sj.chat_id,
                    agent_name=sj.agent,
                    notify_fn=_notify,
                )

            except RuntimeError as exc:
                # Another job is already active — leave as-is but notify
                logger.warning("Scheduler could not start job: %s", exc)
                if bot:
                    try:
                        await bot.send_message(
                            chat_id=sj.chat_id,
                            text=(
                                f"⚠️ *Scheduled Job Skipped*\n\n"
                                f"📦 `{sj.repo_full_name}` Issue #{sj.issue_number}\n\n"
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


# Module-level singleton
job_scheduler = JobScheduler()
