"""
Application entry point.

Start with:
    python -m app.main

Or for config validation only:
    python -m app.main --check-config
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _setup() -> None:
    """Initialise logging before settings are loaded (bootstrap phase)."""
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if sys.stderr and hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    )


async def _async_main(*, reload_enabled: bool = False) -> bool:
    # ---- Load and validate settings ------------------------------------
    from app.config.settings import settings
    from app.utils.logging import configure_logging

    configure_logging(settings.LOG_LEVEL)
    logger.info("Starting AI Coding Agent bot")

    # ---- Ensure workspace directory exists -----------------------------
    settings.WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Workspace directory: %s", settings.WORKSPACE_DIR)

    # ---- Initialise database -------------------------------------------
    from app.database.repository import Database
    db = await Database.create()
    logger.info("Database initialised")

    # ---- Build job executor -------------------------------------------
    from app.runner.executor import JobExecutor
    executor = JobExecutor(db)

    # ---- Build Telegram application ------------------------------------
    from app.telegram.bot import build_application
    token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
    app = build_application(token)

    # Inject executor and db into bot_data (accessible in all handlers)
    app.bot_data["executor"] = executor
    app.bot_data["db"] = db

    logger.info("Bot is starting — allowed user ID: %d", settings.TELEGRAM_ALLOWED_USER_ID)

    # ---- Start polling --------------------------------------------------
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)  # type: ignore[union-attr]

    # ---- Start background job scheduler --------------------------------
    from app.runner.scheduler import job_scheduler
    scheduler_context = {
        "db": db,
        "executor": executor,
        "bot": app.bot,
    }
    job_scheduler.start(scheduler_context)
    logger.info("Background job scheduler started")

    # ---- Send startup notification -------------------------------------
    if settings.NOTIFY_ON_STARTUP:
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup

            from app.database.schedule_repository import ScheduleRepository

            sched_repo = ScheduleRepository(db)
            pending_jobs = await sched_repo.list_all_pending()
            pending_count = len(pending_jobs)

            startup_text = (
                "*البوت متصل*\n"
                f"Agent: `{settings.DEFAULT_AGENT}`\n"
                f"المهام المجدولة: *{pending_count}*"
            )

            startup_keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("القائمة", callback_data="menu:start"),
                    InlineKeyboardButton("المهام المجدولة", callback_data="menu:schedule"),
                ]
            ])

            await app.bot.send_message(
                chat_id=settings.TELEGRAM_ALLOWED_USER_ID,
                text=startup_text,
                reply_markup=startup_keyboard,
                parse_mode="Markdown",
            )
            logger.info("Startup notification sent to user %d", settings.TELEGRAM_ALLOWED_USER_ID)
        except Exception as exc:
            logger.warning("Failed to send startup notification: %s", exc)

    logger.info("Bot is running. Press Ctrl+C to stop.")

    shutdown_event = asyncio.Event()

    def _on_signal(*_args: object) -> None:
        logger.info("Shutdown signal received via OS signal")
        shutdown_event.set()

    import signal
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, AttributeError):
            pass
    if hasattr(signal, "SIGBREAK"):
        try:
            signal.signal(signal.SIGBREAK, _on_signal)
        except (ValueError, AttributeError):
            pass

    reload_requested = False
    reload_task: asyncio.Task[bool] | None = None
    shutdown_task = asyncio.create_task(shutdown_event.wait(), name="shutdown-waiter")
    if reload_enabled:
        from app.reloader import wait_for_source_change

        project_root = Path(__file__).resolve().parent.parent
        reload_task = asyncio.create_task(
            wait_for_source_change(project_root, shutdown_event),
            name="source-reloader",
        )
        logger.info("Auto-reload enabled for %s", project_root)

    try:
        waiters: set[asyncio.Task] = {shutdown_task}
        if reload_task is not None:
            waiters.add(reload_task)
        done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        reload_requested = reload_task is not None and reload_task in done and reload_task.result()

        if reload_requested:
            active_job = await executor.get_active_job()
            if active_job is not None:
                logger.info(
                    "Reload deferred until active job %s finishes",
                    active_job.job_id,
                )
            while active_job is not None and not shutdown_event.is_set():
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=1.0)
                except TimeoutError:
                    active_job = await executor.get_active_job()
            reload_requested = not shutdown_event.is_set()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Shutdown signal received")
    finally:
        for task in (reload_task, shutdown_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        logger.info("Shutting down...")

        # ---- Send shutdown notification before closing bot ---------------
        if settings.NOTIFY_ON_SHUTDOWN:
            try:
                from app.database.schedule_repository import ScheduleRepository
                sched_repo = ScheduleRepository(db)
                pending_count = await sched_repo.count_pending()

                shutdown_text = (
                    "*تم إيقاف البوت*\n"
                    f"المهام المجدولة المحفوظة: *{pending_count}*"
                )
                await app.bot.send_message(
                    chat_id=settings.TELEGRAM_ALLOWED_USER_ID,
                    text=shutdown_text,
                    parse_mode="Markdown",
                )
                logger.info("Shutdown notification sent to user %d", settings.TELEGRAM_ALLOWED_USER_ID)
            except Exception as exc:
                logger.warning("Failed to send shutdown notification: %s", exc)

        job_scheduler.stop()
        await app.updater.stop()  # type: ignore[union-attr]
        await app.stop()
        await app.shutdown()
        await db.close()
        logger.info("Shutdown complete")
    return reload_requested


def main() -> None:
    _setup()

    if "--enable-personal" in sys.argv:
        from app.runner.personal_guard import enable_personal_mode_locally
        success = enable_personal_mode_locally()
        if success:
            print(" Personal Mode has been successfully ENABLED locally.")
            print("   You can now use /personal, /task, /getfile, and file upload in Telegram.")
        else:
            print(" Failed to enable Personal Mode.")
            sys.exit(1)
        return

    if "--disable-personal" in sys.argv:
        from app.runner.personal_guard import disable_personal_mode_from_telegram
        disable_personal_mode_from_telegram(user_id=0)
        print(" Personal Mode has been DISABLED locally (lockfile created).")
        print("   Personal Mode cannot be enabled from Telegram.")
        return

    if "--check-config" in sys.argv:
        # Validate configuration and exit
        try:
            from app.config.settings import get_settings
            from app.runner.personal_guard import is_personal_mode_active
            s = get_settings()
            print(" Configuration is valid")
            print(f"  TELEGRAM_ALLOWED_USER_ID = {s.TELEGRAM_ALLOWED_USER_ID}")
            print(f"  DEFAULT_AGENT            = {s.DEFAULT_AGENT}")
            print(f"  WORKSPACE_DIR            = {s.WORKSPACE_DIR}")
            print(f"  MAX_AGENT_RUNTIME_MINUTES= {s.MAX_AGENT_RUNTIME_MINUTES}")
            github_mode = "GitHub App" if s.GITHUB_APP_ID else "Personal Access Token"
            print(f"  GitHub auth              = {github_mode}")
            active, reason = is_personal_mode_active()
            print(f"  Personal Mode status     = {'Active' if active else f'Disabled ({reason})'}")
        except Exception as exc:
            print(f" Configuration error: {exc}")
            sys.exit(1)
        return

    reload_enabled = "--reload" in sys.argv
    try:
        reload_requested = asyncio.run(_async_main(reload_enabled=reload_enabled))
    except KeyboardInterrupt:
        return

    if reload_requested:
        logger.info("Restarting process to load source changes")
        os.execv(sys.executable, [sys.executable, "-m", "app.main", "--reload"])


if __name__ == "__main__":
    main()
