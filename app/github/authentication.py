"""
GitHub authentication factory.

Supports two modes:
1. GitHub App — generates short-lived installation tokens automatically.
2. Personal Access Token (PAT) — fallback for simpler setups.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from github import Auth, Github, GithubIntegration

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_github_client() -> Github:
    """
    Return an authenticated PyGithub client.

    Called once at startup; result is cached.
    """
    from app.config.settings import settings

    # ---- GitHub App path -----------------------------------------------
    if (
        settings.GITHUB_APP_ID is not None
        and settings.GITHUB_INSTALLATION_ID is not None
        and settings.GITHUB_PRIVATE_KEY_PATH is not None
    ):
        private_key = settings.GITHUB_PRIVATE_KEY_PATH.read_text()
        app_auth = Auth.AppAuth(
            app_id=settings.GITHUB_APP_ID,
            private_key=private_key,
        )
        integration = GithubIntegration(auth=app_auth)
        installation = integration.get_installation(settings.GITHUB_INSTALLATION_ID)
        client = installation.get_github_for_installation()
        logger.info(
            "GitHub authenticated as App (id=%s, installation=%s)",
            settings.GITHUB_APP_ID,
            settings.GITHUB_INSTALLATION_ID,
        )
        return client

    # ---- PAT fallback --------------------------------------------------
    if settings.GITHUB_TOKEN is not None:
        client = Github(auth=Auth.Token(settings.GITHUB_TOKEN.get_secret_value()))
        logger.info("GitHub authenticated via Personal Access Token")
        return client

    # Should be caught by settings validator, but guard just in case
    raise RuntimeError("No GitHub authentication configured.")
