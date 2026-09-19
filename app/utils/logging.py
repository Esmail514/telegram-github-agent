"""
Structured logging setup.

Features:
- Masks secret values in log records.
- Writes to console (colour) and rotating file.
- Single call: configure_logging() at startup.
"""
from __future__ import annotations

import logging
import logging.handlers
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Secret masking filter
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(ghp_[A-Za-z0-9]{36})", re.IGNORECASE),           # GitHub PAT
    re.compile(r"(ghs_[A-Za-z0-9]{36})", re.IGNORECASE),           # GitHub App token
    re.compile(r"(AKIA[0-9A-Z]{16})", re.IGNORECASE),              # AWS access key
    re.compile(r"(sk-[A-Za-z0-9]{32,})", re.IGNORECASE),           # OpenAI / Anthropic
    re.compile(r"(xox[baprs]-[0-9A-Za-z-]{10,})", re.IGNORECASE), # Slack token
    re.compile(r"(Bearer\s+)[^\s\"']+", re.IGNORECASE),            # Bearer tokens
]


class SecretMaskingFilter(logging.Filter):
    """Replaces known secret patterns in log messages with '***REDACTED***'."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        if isinstance(record.msg, str):
            record.msg = _mask(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: _mask(v) if isinstance(v, str) else v for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    _mask(a) if isinstance(a, str) else a for a in record.args
                )
        return True


def _mask(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(r"***REDACTED***", text)
    return text


# ---------------------------------------------------------------------------
# Configure logging
# ---------------------------------------------------------------------------

def configure_logging(level: str = "INFO", log_dir: Path | None = None) -> None:
    """
    Call once at startup.  Sets up console + rotating-file handlers.
    All handlers receive the SecretMaskingFilter.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(numeric_level)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    masking_filter = SecretMaskingFilter()

    # --- Console handler ---
    console = logging.StreamHandler()
    console.setLevel(numeric_level)
    console.setFormatter(formatter)
    console.addFilter(masking_filter)
    root.addHandler(console)

    # --- File handler ---
    if log_dir is None:
        log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "agent.log"

    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(masking_filter)
    root.addHandler(file_handler)

    # Suppress noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("git").setLevel(logging.WARNING)

    logging.getLogger(__name__).info("Logging configured (level=%s)", level)
