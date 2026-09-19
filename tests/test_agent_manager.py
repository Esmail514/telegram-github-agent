"""
Tests for AgentManager.
"""
import pytest

from app.agents.manager import AgentManager
from app.agents.opencode import OpenCodeAgent


def test_list_available():
    manager = AgentManager()
    available = manager.list_available()
    assert "opencode" in available
    assert "claude" in available
    assert "codex" in available
    assert "gemini" in available


def test_get_agent_creates_instance():
    manager = AgentManager()
    agent = manager.get_agent("opencode")
    assert isinstance(agent, OpenCodeAgent)
    assert manager.get_active() is agent

    manager.clear_active()
    assert manager.get_active() is None


def test_unknown_agent_raises():
    manager = AgentManager()
    with pytest.raises(ValueError, match="Unknown agent"):
        manager.get_agent("nonexistent-agent")
