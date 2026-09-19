"""
Agent manager — registry and factory for AI coding agents.
"""
from __future__ import annotations

import logging

from app.agents.base import BaseAgent
from app.agents.claude import ClaudeAgent
from app.agents.codex import CodexAgent
from app.agents.gemini import GeminiAgent
from app.agents.opencode import OpenCodeAgent

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, type[BaseAgent]] = {
    "opencode": OpenCodeAgent,
    "claude": ClaudeAgent,
    "codex": CodexAgent,
    "gemini": GeminiAgent,
}


class AgentManager:
    """
    Creates and manages agent instances.

    The manager holds at most one active agent at a time.
    Use get_active() to retrieve the currently running agent (if any).
    """

    def __init__(self) -> None:
        self._active: BaseAgent | None = None

    def get_agent(self, name: str | None = None) -> BaseAgent:
        from app.config.settings import settings

        agent_name = (name or settings.DEFAULT_AGENT).lower()
        agent_cls = _REGISTRY.get(agent_name)
        if agent_cls is None:
            available = ", ".join(_REGISTRY.keys())
            raise ValueError(
                f"Unknown agent '{agent_name}'. Available: {available}"
            )
        agent = agent_cls()
        self._active = agent
        logger.info("Created agent: %s", agent_name)
        return agent

    def get_active(self) -> BaseAgent | None:
        return self._active

    def clear_active(self) -> None:
        self._active = None

    @staticmethod
    def list_available() -> list[str]:
        return list(_REGISTRY.keys())


# Module-level singleton
agent_manager = AgentManager()
