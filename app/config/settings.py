"""
Application configuration loaded from environment variables / .env file.
All secrets are typed as SecretStr so they are masked in logs and repr().
"""
from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Platform-aware workspace default
# ---------------------------------------------------------------------------

def _default_workspace_dir() -> Path:
    """
    Return a sensible per-OS default for the workspace directory.

    Priority:
        1. WORKSPACE_DIR env-var (handled by pydantic-settings before this runs)
        2. Per-OS convention:
           - Windows  : ~/Documents/telegram-agent-workspaces
           - macOS    : ~/Documents/telegram-agent-workspaces
           - Linux    : ~/telegram-agent-workspaces
           - Other    : ./workspaces  (safe relative fallback)
    """
    os_name = platform.system()  # 'Windows', 'Darwin', 'Linux', ''
    home = Path.home()

    if os_name == "Windows":
        return home / "Documents" / "telegram-agent-workspaces"
    elif os_name == "Darwin":  # macOS
        return home / "Documents" / "telegram-agent-workspaces"
    elif os_name == "Linux":
        return home / "telegram-agent-workspaces"
    else:
        # FreeBSD, Android-Termux, unknown — safe fallback
        return Path("./workspaces")


def get_platform_info() -> dict[str, str]:
    """Return a dict of human-readable platform details for status display."""
    return {
        "os": platform.system() or "Unknown",
        "os_version": platform.version(),
        "os_release": platform.release(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "platform": platform.platform(terse=True),
    }


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Telegram
    # ------------------------------------------------------------------
    TELEGRAM_BOT_TOKEN: SecretStr = Field(
        ..., description="Bot token from @BotFather"
    )
    TELEGRAM_ALLOWED_USER_ID: int = Field(
        ..., description="Numeric Telegram user ID allowed to control the bot"
    )
    TELEGRAM_PROXY_URL: str | None = Field(
        None, description="Optional HTTP/HTTPS/SOCKS5 proxy URL for Telegram (e.g. http://127.0.0.1:7890 or socks5://127.0.0.1:1080)"
    )
    TELEGRAM_REQUEST_TIMEOUT: float = Field(
        60.0, description="HTTP request timeout for Telegram Bot API (seconds)"
    )

    # ------------------------------------------------------------------
    # GitHub App authentication (preferred)
    # ------------------------------------------------------------------
    GITHUB_APP_ID: int | None = Field(None, description="GitHub App numeric ID")
    GITHUB_INSTALLATION_ID: int | None = Field(
        None, description="GitHub App installation ID"
    )
    GITHUB_PRIVATE_KEY_PATH: Path | None = Field(
        None, description="Path to GitHub App .pem private key"
    )

    # ------------------------------------------------------------------
    # GitHub PAT fallback
    # ------------------------------------------------------------------
    GITHUB_TOKEN: SecretStr | None = Field(
        None, description="GitHub Personal Access Token (fallback)"
    )

    # ------------------------------------------------------------------
    # Workspace & Projects
    # ------------------------------------------------------------------
    WORKSPACE_DIR: Path = Field(
        default_factory=_default_workspace_dir,
        description=(
            "Root directory for cloned repositories. "
            "Defaults are OS-specific: "
            "Windows/macOS → ~/Documents/telegram-agent-workspaces, "
            "Linux → ~/telegram-agent-workspaces."
        ),
    )
    PROJECTS_DIR: Path | None = Field(
        None,
        description=(
            "Root directory containing local projects on this machine "
            "(e.g. D:\\Work or /home/user/projects). When set, the bot "
            "scans this directory and lets you browse and run jobs on local projects."
        ),
    )

    # ------------------------------------------------------------------
    # Agent
    # ------------------------------------------------------------------
    DEFAULT_AGENT: Literal["antigravity", "codex", "claude", "gemini", "opencode"] = Field(
        "antigravity", description="Default AI coding agent"
    )
    ANTIGRAVITY_COMMAND: str = Field(
        "agy", description="Antigravity CLI executable path or name (e.g. agy or antigravity)"
    )
    USE_ANTIGRAVITY_FOR_POLISH: bool = Field(
        True, description="Whether to use Antigravity to craft and polish issues instead of external LLM APIs"
    )
    CODEX_COMMAND: str = Field(
        "codex", description="Codex executable path or command name"
    )
    OPENCODE_COMMAND: str = Field(
        "opencode", description="opencode executable path or name"
    )
    AUTO_MERGE_PR: bool = Field(
        False, description="Whether to automatically merge PRs after agent completes successfully"
    )
    DEFAULT_MERGE_METHOD: Literal["squash", "merge", "rebase"] = Field(
        "squash", description="Default PR merge strategy"
    )

    # AI provider keys forwarded to agent subprocess
    ANTHROPIC_API_KEY: SecretStr | None = None
    OPENAI_API_KEY: SecretStr | None = None
    GOOGLE_GENERATIVEAI_API_KEY: SecretStr | None = None

    # ------------------------------------------------------------------
    # Safety limits
    # ------------------------------------------------------------------
    MAX_AGENT_RUNTIME_MINUTES: int = Field(
        60, ge=1, le=480, description="Hard timeout for agent jobs"
    )
    MAX_FIX_ITERATIONS: int = Field(
        5, ge=1, le=20, description="Max fix attempts before giving up"
    )
    GIT_OPERATION_TIMEOUT_SECONDS: float = Field(
        120.0, ge=5.0, le=600.0, description="Timeout for local git operations (seconds)"
    )
    GIT_NETWORK_TIMEOUT_SECONDS: float = Field(
        600.0, ge=10.0, le=1800.0, description="Timeout for network git operations like clone, fetch, pull, push (seconds)"
    )

    # ------------------------------------------------------------------
    # Controller-enforced verification pipeline
    # ------------------------------------------------------------------
    VERIFICATION_CHECKS_JSON: str | None = Field(
        None,
        description=(
            "Trusted JSON policy for controller-run verification checks. "
            "List of {\"name\", \"command\" (argv list), \"timeout\", \"required\", "
            "\"skip_on_previous_failure\"}. When unset the built-in default "
            "(git diff --check) is used. Commands are never read from repository files."
        ),
    )

    # ------------------------------------------------------------------
    # Human-in-the-loop approval policy
    # ------------------------------------------------------------------
    APPROVAL_POLICY: Literal["autonomous", "before_commit", "before_push", "before_pr"] = Field(
        "autonomous",
        description=(
            "Approval gate policy. 'autonomous' = no approvals (current default). "
            "before_commit / before_push / before_pr require operator approval "
            "for MEDIUM/HIGH risk changes before that git action."
        ),
    )
    APPROVAL_TIMEOUT_MINUTES: int = Field(
        30, ge=1, le=1440, description="How long an approval request stays valid"
    )
    PROTECTED_PATHS: str = Field(
        ".github/workflows,migrations,.env*",
        description=(
            "Comma-separated glob paths that are ALWAYS classified HIGH risk "
            "for approval. Cannot be lowered by repository content."
        ),
    )

    @property
    def protected_paths_list(self) -> list[str]:
        return [p.strip() for p in self.PROTECTED_PATHS.split(",") if p.strip()]

    # ------------------------------------------------------------------
    # Agent fallback architecture
    # ------------------------------------------------------------------
    FALLBACK_MODE: Literal["automatic", "ask_before_fallback", "disabled"] = Field(
        "automatic",
        description=(
            "Fallback mode when the primary agent fails with a retryable "
            "infrastructure failure (missing binary, start-up failure, rate "
            "limit, transport failure). 'ask_before_fallback' pauses for the "
            "operator; 'disabled' never falls back."
        ),
    )
    FALLBACK_TIMEOUT_ALLOWED: bool = Field(
        False,
        description=(
            "Allow falling back when the failure was a TIMEOUT. Off by default "
            "because a timed-out agent may still be mid-edit."
        ),
    )
    AGENT_FALLBACK_ORDER: str = Field(
        "antigravity,codex,opencode,claude,gemini",
        description="Priority order used when choosing fallback agents.",
    )

    # ------------------------------------------------------------------
    # Lifecycle Notifications
    # ------------------------------------------------------------------
    NOTIFY_ON_STARTUP: bool = Field(
        True,
        description="Send a Telegram notification to TELEGRAM_ALLOWED_USER_ID on startup",
    )
    NOTIFY_ON_SHUTDOWN: bool = Field(
        True,
        description="Send a Telegram notification to TELEGRAM_ALLOWED_USER_ID on shutdown",
    )

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    LOGS_PAGE_SIZE: int = Field(
        15, ge=5, le=100, description="Number of event lines shown per /logs page"
    )

    # ------------------------------------------------------------------
    # Personal Use Mode
    # ------------------------------------------------------------------
    PERSONAL_MODE: bool = Field(
        False,
        description="Enable personal use commands: run shell commands, browse files, manage processes. DISABLED by default for safety.",
    )
    PERSONAL_COMMAND_TIMEOUT: int = Field(
        30,
        ge=5,
        le=300,
        description="Max seconds for a personal shell command before it is killed",
    )
    PERSONAL_ALLOWED_DIRS: str = Field(
        "",
        description=(
            "Comma-separated list of directories the bot is allowed to browse/run in. "
            "Empty string = no restriction (all dirs allowed). "
            "Example: C:\\Users\\me\\Projects,D:\\Work"
        ),
    )

    @property
    def personal_allowed_dirs_list(self) -> list[str]:
        """Return PERSONAL_ALLOWED_DIRS as a list of stripped strings (empty = unrestricted)."""
        if not self.PERSONAL_ALLOWED_DIRS.strip():
            return []
        return [d.strip() for d in self.PERSONAL_ALLOWED_DIRS.split(",") if d.strip()]

    PERSONAL_WORKSPACE_DIR: Path = Field(
        default=Path("workspaces/personal"),
        description="Directory used for incoming personal files and tasks",
    )
    PERSONAL_MAX_UPLOAD_SIZE_MB: int = Field(
        50,
        ge=1,
        le=100,
        description="Maximum file upload size in MB via Telegram",
    )
    PERSONAL_MAX_DOWNLOAD_SIZE_MB: int = Field(
        50,
        ge=1,
        le=50,
        description="Maximum file download size in MB via Telegram (Telegram Bot API limit is 50MB)",
    )
    PERSONAL_SENSITIVE_PATTERNS: str = Field(
        ".env*|*id_rsa*|*.pem|*.key|agent.db|*.p12|*.pfx",
        description="Pipe-separated glob patterns of sensitive files forbidden from download",
    )

    @property
    def personal_sensitive_patterns_list(self) -> list[str]:
        """Return sensitive file glob patterns as a list."""
        if not self.PERSONAL_SENSITIVE_PATTERNS.strip():
            return []
        return [p.strip() for p in self.PERSONAL_SENSITIVE_PATTERNS.split("|") if p.strip()]

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------
    @field_validator("WORKSPACE_DIR", mode="before")
    @classmethod
    def resolve_workspace_dir(cls, v: object) -> object:
        """If WORKSPACE_DIR is empty/blank in .env, fall back to the OS default."""
        if isinstance(v, str) and not v.strip():
            return _default_workspace_dir()
        return v

    @field_validator("PROJECTS_DIR", mode="before")
    @classmethod
    def resolve_projects_dir(cls, v: object) -> Path | None:
        """If PROJECTS_DIR is empty/blank, treat as None."""
        if isinstance(v, str):
            v_str = v.strip().strip('"').strip("'")
            if not v_str:
                return None
            return Path(v_str).resolve()
        if isinstance(v, Path):
            return v.resolve()
        return None

    @field_validator(
        "GITHUB_APP_ID",
        "GITHUB_INSTALLATION_ID",
        "GITHUB_PRIVATE_KEY_PATH",
        "GITHUB_TOKEN",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GOOGLE_GENERATIVEAI_API_KEY",
        mode="before",
    )
    @classmethod
    def empty_str_to_none(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @model_validator(mode="after")
    def validate_github_auth(self) -> Settings:
        has_app = (
            self.GITHUB_APP_ID is not None
            and self.GITHUB_INSTALLATION_ID is not None
            and self.GITHUB_PRIVATE_KEY_PATH is not None
        )
        has_pat = self.GITHUB_TOKEN is not None

        if not has_app and not has_pat:
            raise ValueError(
                "GitHub authentication not configured. "
                "Set either (GITHUB_APP_ID + GITHUB_INSTALLATION_ID + GITHUB_PRIVATE_KEY_PATH) "
                "or GITHUB_TOKEN in your .env file."
            )

        if has_app and self.GITHUB_PRIVATE_KEY_PATH is not None:
            if not self.GITHUB_PRIVATE_KEY_PATH.exists():
                raise ValueError(
                    f"GITHUB_PRIVATE_KEY_PATH does not exist: {self.GITHUB_PRIVATE_KEY_PATH}"
                )
        return self

    def get_agent_env(self) -> dict[str, str]:
        """Return environment variables to forward to agent subprocesses."""
        env: dict[str, str] = {}
        if self.ANTHROPIC_API_KEY:
            env["ANTHROPIC_API_KEY"] = self.ANTHROPIC_API_KEY.get_secret_value()
        if self.OPENAI_API_KEY:
            env["OPENAI_API_KEY"] = self.OPENAI_API_KEY.get_secret_value()
        if self.GOOGLE_GENERATIVEAI_API_KEY:
            env["GOOGLE_GENERATIVEAI_API_KEY"] = (
                self.GOOGLE_GENERATIVEAI_API_KEY.get_secret_value()
            )
        return env

    def set_projects_dir(self, new_path: Path | str | None) -> Path | None:
        """Update active PROJECTS_DIR at runtime."""
        if new_path is None:
            self.PROJECTS_DIR = None
            return None
        p = Path(str(new_path).strip().strip('"').strip("'")).resolve()
        self.PROJECTS_DIR = p
        return p



# Module-level lazy singleton — only instantiated on first use.
# This avoids ValidationError during test collection when .env is absent.
_settings_instance: Settings | None = None


def get_settings() -> Settings:
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()  # type: ignore[call-arg]
    return _settings_instance


# Convenience alias — behaves identically to the previous `settings` object
# for all modules that do `from app.config.settings import settings`.
# The proxy defers instantiation until first attribute access.
class _SettingsProxy:
    """Thin proxy that creates the Settings instance on first access."""
    def __getattr__(self, name: str):  # type: ignore[override]
        return getattr(get_settings(), name)

    def __repr__(self) -> str:
        return repr(get_settings())


settings: Settings = _SettingsProxy()  # type: ignore[assignment]
