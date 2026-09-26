"""
One-Way Safety Guard for Personal Mode.

Enforces the one-way kill switch:
1. Personal mode can be turned OFF from Telegram at any time.
2. Once turned OFF from Telegram, a local lockfile (.personal_mode_disabled) is created.
3. While the lockfile exists, personal mode CANNOT be turned on from Telegram under any circumstances.
4. It can ONLY be re-enabled locally from the host machine (e.g. via CLI: python -m app.main --enable-personal).
"""
from __future__ import annotations

import datetime
import logging
from pathlib import Path

from app.config.settings import settings

logger = logging.getLogger(__name__)

LOCKFILE_NAME = ".personal_mode_disabled"


def get_lockfile_path() -> Path:
    """Return the absolute path to the personal mode lockfile located in the project root."""
    return Path(__file__).resolve().parents[2] / LOCKFILE_NAME


def is_locked_by_switch() -> bool:
    """Check if personal mode has been disabled via the one-way kill switch lockfile."""
    return get_lockfile_path().exists()


def is_personal_mode_active() -> tuple[bool, str]:
    """
    Check if Personal Mode is active.

    Returns:
        (True, "active") if enabled in settings and not locked by the kill switch.
        (False, "disabled_by_lockfile") if locked by .personal_mode_disabled.
        (False, "disabled_by_config") if PERSONAL_MODE=False in settings.
    """
    if is_locked_by_switch():
        return False, "disabled_by_lockfile"

    if not settings.PERSONAL_MODE:
        return False, "disabled_by_config"

    return True, "active"


def disable_personal_mode_from_telegram(user_id: int) -> bool:
    """
    Permanently disable Personal Mode from Telegram.
    Creates .personal_mode_disabled in the project root.
    Updates the in-memory setting.
    """
    lockfile = get_lockfile_path()
    content = (
        f"# Personal Mode disabled from Telegram\n"
        f"Disabled-At: {datetime.datetime.now(datetime.UTC).isoformat()}\n"
        f"Disabled-By-User-ID: {user_id}\n"
        f"To re-enable, run locally on this machine:\n"
        f"    python -m app.main --enable-personal\n"
    )
    try:
        lockfile.write_text(content, encoding="utf-8")
        logger.warning(
            "SAFETY SWITCH ACTIVATED: Personal Mode disabled from Telegram by user %s. Lockfile created at %s",
            user_id,
            lockfile,
        )
    except OSError as exc:
        logger.error("Failed to write kill switch lockfile %s: %s", lockfile, exc)
        return False

    # Force in-memory flag to False
    settings.PERSONAL_MODE = False
    return True


def enable_personal_mode_locally() -> bool:
    """
    Re-enable Personal Mode locally.
    Must ONLY be called from local machine commands (CLI or local script).
    Removes .personal_mode_disabled.
    """
    lockfile = get_lockfile_path()
    if lockfile.exists():
        try:
            lockfile.unlink()
            logger.info("Local safety switch cleared: removed %s", lockfile)
        except OSError as exc:
            logger.error("Failed to remove lockfile %s: %s", lockfile, exc)
            return False

    # Also restore settings.PERSONAL_MODE to True
    settings.PERSONAL_MODE = True
    return True
