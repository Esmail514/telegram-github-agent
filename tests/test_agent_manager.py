"""
Tests for AgentManager.
"""
import pytest

from app.agents.antigravity import AntigravityAgent
from app.agents.manager import AgentManager


def test_list_available():
    manager = AgentManager()
    available = manager.list_available()
    assert available == ["antigravity", "codex"]


def test_get_agent_creates_instance():
    manager = AgentManager()
    agent = manager.get_agent("antigravity")
    assert isinstance(agent, AntigravityAgent)
    assert manager.get_active() is agent

    manager.clear_active()
    assert manager.get_active() is None


def test_unknown_agent_raises():
    manager = AgentManager()
    with pytest.raises(ValueError, match="Unknown agent"):
        manager.get_agent("nonexistent-agent")
