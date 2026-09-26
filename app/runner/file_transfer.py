"""
Personal Mode File Transfer Module.

Handles:
1. Safe saving of incoming files (Telegram -> Machine).
2. Validation and security checks for outgoing files (Machine -> Telegram).
"""
from __future__ import annotations

import fnmatch
import logging
import re
from pathlib import Path

from app.config.settings import settings

logger = logging.getLogger(__name__)


def _is_dir_allowed(target_path: Path) -> bool:
    """Check if the given path is within PERSONAL_ALLOWED_DIRS (if set)."""
    allowed = settings.personal_allowed_dirs_list
    if not allowed:
        return True
    resolved = str(target_path.resolve())
    return any(resolved.startswith(str(Path(d).resolve())) for d in allowed)


def _is_sensitive_file(filename: str) -> bool:
    """Check if filename matches any sensitive pattern (.env, keys, db, etc.)."""
    patterns = settings.personal_sensitive_patterns_list
    lower_name = filename.lower()
    for pattern in patterns:
        if fnmatch.fnmatch(lower_name, pattern.lower()):
            return True
    return False


def save_incoming_file(
    content: bytes,
    original_filename: str,
    destination_dir: Path | None = None,
) -> tuple[bool, str, Path | None]:
    """
    Safely save an incoming file to disk.

    Returns:
        (True, "success message", file_path) on success.
        (False, "error message", None) on failure.
    """
    max_bytes = settings.PERSONAL_MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        return (
            False,
            f"❌ حجم الملف ({round(len(content) / (1024 * 1024), 2)} MB) يتجاوز الحد الأقصى المسموح ({settings.PERSONAL_MAX_UPLOAD_SIZE_MB} MB).",
            None,
        )

    # Sanitize filename - strip directory traversals
    safe_name = Path(original_filename).name
    # Keep alphanumeric, dot, underscore, dash, arabic
    safe_name = re.sub(r'[\\/*?:"<>|]', "", safe_name).strip()
    if not safe_name:
        safe_name = "uploaded_file.bin"

    target_dir = destination_dir or (settings.PERSONAL_WORKSPACE_DIR / "incoming")
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error("Failed to create target directory %s: %s", target_dir, exc)
        return False, f"❌ تعذر إنشاء مجلد الحفظ: {exc}", None

    target_file = target_dir / safe_name
    # Avoid collision
    counter = 1
    stem = target_file.stem
    suffix = target_file.suffix
    while target_file.exists():
        target_file = target_dir / f"{stem}_{counter}{suffix}"
        counter += 1

    try:
        target_file.write_bytes(content)
        logger.info("Saved incoming personal file: %s (%d bytes)", target_file, len(content))
        return True, "تم حفظ الملف بنجاح", target_file
    except OSError as exc:
        logger.error("Failed to write incoming file %s: %s", target_file, exc)
        return False, f"❌ خطأ أثناء حفظ الملف: {exc}", None


def validate_download_path(file_path_str: str | Path) -> tuple[bool, str, Path | None]:
    """
    Validate that a file path is safe and eligible to be downloaded to Telegram.

    Checks:
    - Path exists and is a file.
    - Path is within PERSONAL_ALLOWED_DIRS (if set).
    - File is NOT matched by sensitive patterns (.env, keys, db).
    - File size is within PERSONAL_MAX_DOWNLOAD_SIZE_MB and Telegram limits.

    Returns:
        (True, "OK", resolved_path) if valid.
        (False, "rejection reason", None) if rejected.
    """
    path = Path(file_path_str).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    else:
        path = path.resolve()

    if not path.exists():
        return False, f"❌ الملف غير موجود: `{path}`", None

    if not path.is_file():
        return False, f"⚠️ المسار المحدد ليس ملفاً: `{path}`", None

    # Check allowed dirs restriction
    if not _is_dir_allowed(path):
        return False, f"🚫 الوصول للمسار `{path}` غير مسموح به حسب إعدادات الأمان.", None

    # Check sensitive files blocklist
    if _is_sensitive_file(path.name):
        return False, f"🔒 محظور: الملف `{path.name}` مصنف كملف حساس ومحمي من التحميل.", None

    # Check file size
    try:
        size = path.stat().st_size
    except OSError as exc:
        return False, f"❌ تعذر قراءة بيانات الملف: {exc}", None

    max_bytes = settings.PERSONAL_MAX_DOWNLOAD_SIZE_MB * 1024 * 1024
    if size > max_bytes:
        size_mb = round(size / (1024 * 1024), 2)
        return (
            False,
            f"⚠️ حجم الملف ({size_mb} MB) أكبر من الحد الأقصى المسموح للتحميل ({settings.PERSONAL_MAX_DOWNLOAD_SIZE_MB} MB).",
            None,
        )

    return True, "OK", path
