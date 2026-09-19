"""
Application configuration loaded from environment variables / .env file.
All secrets are typed as SecretStr so they are masked in logs and repr().
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    # Workspace
    # ------------------------------------------------------------------
    WORKSPACE_DIR: Path = Field(
        Path("./workspaces"),
        description="Root directory for cloned repositories",
    )

    # ------------------------------------------------------------------
    # Agent
    # ------------------------------------------------------------------
    DEFAULT_AGENT: Literal["opencode", "claude", "codex", "gemini"] = Field(
        "opencode", description="Default AI coding agent"
    )
    OPENCODE_COMMAND: str = Field(
        "opencode", description="opencode executable path or name"
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

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------
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
