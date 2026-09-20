from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings as settings_module
from app.github.issue_polisher import IssuePolisher, PolishedIssue


@pytest.mark.asyncio
async def test_polish_antigravity_generator_success(monkeypatch):
    """Antigravity crafts issues cleanly without requiring any external AI API keys."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_GENERATIVEAI_API_KEY", raising=False)
    settings_module._settings_instance = None

    polisher = IssuePolisher()
    result = await polisher.polish(
        repo_full_name="owner/repo",
        title="crash during user login",
        body="login fails with 500 internal server error on submit",
    )

    assert isinstance(result, PolishedIssue)
    assert result.title.startswith("fix:")
    assert "crash" in result.title.lower() or "login" in result.title.lower()
    assert "bug" in result.labels
    assert "## Summary" in result.body
    assert "## Acceptance Criteria" in result.body
    assert "Antigravity" in result.explanation


@pytest.mark.asyncio
async def test_polish_antigravity_feature_request(monkeypatch):
    """Antigravity generates enhancement issues with conventional prefix and acceptance criteria."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_GENERATIVEAI_API_KEY", raising=False)
    settings_module._settings_instance = None

    polisher = IssuePolisher()
    result = await polisher.polish(
        repo_full_name="owner/repo",
        title="add export to csv button",
        body="users want to export their transaction history to a csv file",
    )

    assert isinstance(result, PolishedIssue)
    assert result.title.startswith("feat:")
    assert "enhancement" in result.labels
    assert "## Acceptance Criteria" in result.body
    assert "## Proposed Implementation" in result.body


@pytest.mark.asyncio
async def test_polish_antigravity_cli_success(monkeypatch):
    """When Antigravity CLI returns structured JSON, it is parsed and returned."""
    polisher = IssuePolisher()
    cli_json = """{
        "title": "fix(auth): prevent session timeout crash",
        "body": "## Summary\\nPrevents crash when session expires.\\n\\n## Acceptance Criteria\\n- [ ] Handled gracefully",
        "labels": ["bug", "security"],
        "explanation": "Formatted with Antigravity CLI"
    }"""

    with patch.object(polisher, "_resolve_antigravity_executable", return_value="agy"), \
         patch.object(polisher, "_call_antigravity_cli", new_callable=AsyncMock) as mock_cli:
        mock_cli.return_value = cli_json
        result = await polisher.polish("owner/repo", "session crash", "crash when timeout")

        assert isinstance(result, PolishedIssue)
        assert result.title == "fix(auth): prevent session timeout crash"
        assert result.labels == ["bug", "security"]
        assert result.explanation == "Formatted with Antigravity CLI"


@pytest.mark.asyncio
async def test_polish_gemini_success(monkeypatch):
    monkeypatch.setenv("GOOGLE_GENERATIVEAI_API_KEY", "mock-gemini-key")
    settings_module._settings_instance = None

    polisher = IssuePolisher()
    gemini_response = """{
        "title": "Fix crash on empty list",
        "body": "Detailed description here",
        "labels": ["bug"],
        "explanation": "Made it clearer"
    }"""

    with patch("app.config.settings.settings.USE_ANTIGRAVITY_FOR_POLISH", False), \
         patch.object(polisher, "_call_gemini", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = gemini_response
        result = await polisher.polish("owner/repo", "bug", "crash")

        assert isinstance(result, PolishedIssue)
        assert result.title == "Fix crash on empty list"
        assert result.labels == ["bug"]
        assert result.explanation == "Made it clearer"


@pytest.mark.asyncio
async def test_polish_fallback_on_anthropic_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "mock-anthropic-key")
    monkeypatch.setenv("OPENAI_API_KEY", "mock-openai-key")
    monkeypatch.setenv("GOOGLE_GENERATIVEAI_API_KEY", "mock-gemini-key")
    settings_module._settings_instance = None

    polisher = IssuePolisher()
    gemini_response = """{
        "title": "Fallback title",
        "body": "Fallback body",
        "labels": ["enhancement"],
        "explanation": "Fell back to Gemini"
    }"""

    with (
        patch("app.config.settings.settings.USE_ANTIGRAVITY_FOR_POLISH", False),
        patch.object(polisher, "_call_anthropic", side_effect=RuntimeError("Out of credits")),
        patch.object(polisher, "_call_openai", side_effect=RuntimeError("Quota exceeded")),
        patch.object(polisher, "_call_gemini", new_callable=AsyncMock) as mock_gemini,
    ):
        mock_gemini.return_value = gemini_response
        result = await polisher.polish("owner/repo", "bug", "crash")

        assert result.title == "Fallback title"
        assert result.labels == ["enhancement"]


@pytest.mark.asyncio
async def test_polish_no_keys_configured(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_GENERATIVEAI_API_KEY", raising=False)
    settings_module._settings_instance = None

    polisher = IssuePolisher()
    with patch("app.config.settings.settings.USE_ANTIGRAVITY_FOR_POLISH", False), \
         patch("app.config.settings.settings.ANTHROPIC_API_KEY", None), \
         patch("app.config.settings.settings.OPENAI_API_KEY", None), \
         patch("app.config.settings.settings.GOOGLE_GENERATIVEAI_API_KEY", None):
        with pytest.raises(RuntimeError, match="No AI API key configured"):
            await polisher.polish("owner/repo", "bug", "crash")
