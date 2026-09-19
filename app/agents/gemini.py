"""Gemini CLI agent — stub for future implementation."""
from __future__ import annotations

from pathlib import Path

from app.agents.base import AgentContext, AgentResult, BaseAgent, ProgressCallback


class GeminiAgent(BaseAgent):
    name = "gemini"

    async def run(
        self,
        context: AgentContext,
        workspace_path: Path,
        on_progress: ProgressCallback,
    ) -> AgentResult:
        raise NotImplementedError(
            "Gemini agent is not yet implemented. "
            "Set DEFAULT_AGENT=opencode in your .env file."
        )

    async def stop(self) -> None:
        pass
