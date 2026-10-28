from unittest.mock import patch

from app.agents.antigravity import _resolve_antigravity_cli
from app.agents.transport import CliTransport, transports_for_agent
from app.github.issue_polisher import IssuePolisher


def test_transport_resolution_exposes_only_cli_for_supported_agents():
    antigravity = transports_for_agent("antigravity")
    codex = transports_for_agent("codex")

    assert len(antigravity) == 1 and isinstance(antigravity[0], CliTransport)
    assert len(codex) == 1 and isinstance(codex[0], CliTransport)
    assert transports_for_agent("antigravity-ide") == []


def test_antigravity_agent_never_falls_back_to_desktop_executables():
    with patch("app.agents.antigravity.shutil.which", side_effect=lambda name: name == "agy" and "agy") as which:
        assert _resolve_antigravity_cli("missing-command") == "agy"

    assert [call.args[0] for call in which.call_args_list] == ["missing-command", "agy"]


def test_issue_polisher_resolves_only_antigravity_cli_commands():
    polisher = IssuePolisher()
    with patch(
        "app.github.issue_polisher.shutil.which",
        side_effect=lambda name: name == "antigravity" and "antigravity",
    ) as which:
        assert polisher._resolve_antigravity_executable() == "antigravity"

    assert "antigravity-ide" not in [call.args[0] for call in which.call_args_list]
