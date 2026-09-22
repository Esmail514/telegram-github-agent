"""
Application entry point.

Start with:
    python -m app.main

Or for config validation only:
    python -m app.main --check-config
"""
from __future__ import annotations

import asyncio
import logging
import sys

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


async def _async_main() -> None:
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

    logger.info("Bot is running. Press Ctrl+C to stop.")

    try:
        # Keep running until interrupted
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Shutdown signal received")
    finally:
        logger.info("Shutting down...")
        job_scheduler.stop()
        await app.updater.stop()  # type: ignore[union-attr]
        await app.stop()
        await app.shutdown()
        await db.close()
        logger.info("Shutdown complete")


def main() -> None:
    _setup()

    if "--check-config" in sys.argv:
        # Validate configuration and exit
        try:
            from app.config.settings import get_settings
            s = get_settings()
            print("✅ Configuration is valid")
            print(f"  TELEGRAM_ALLOWED_USER_ID = {s.TELEGRAM_ALLOWED_USER_ID}")
            print(f"  DEFAULT_AGENT            = {s.DEFAULT_AGENT}")
            print(f"  WORKSPACE_DIR            = {s.WORKSPACE_DIR}")
            print(f"  MAX_AGENT_RUNTIME_MINUTES= {s.MAX_AGENT_RUNTIME_MINUTES}")
            github_mode = "GitHub App" if s.GITHUB_APP_ID else "Personal Access Token"
            print(f"  GitHub auth              = {github_mode}")
        except Exception as exc:
            print(f"❌ Configuration error: {exc}")
            sys.exit(1)
        return

    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
