"""Codex agent — stub for future implementation."""
from __future__ import annotations

from pathlib import Path

from app.agents.base import AgentContext, AgentResult, BaseAgent, ProgressCallback


class CodexAgent(BaseAgent):
    name = "codex"

    async def run(
        self,
        context: AgentContext,
        workspace_path: Path,
        on_progress: ProgressCallback,
    ) -> AgentResult:
        raise NotImplementedError(
            "Codex agent is not yet implemented. "
            "Set DEFAULT_AGENT=opencode in your .env file."
        )

    async def stop(self) -> None:
        pass
