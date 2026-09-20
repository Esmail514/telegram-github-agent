from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings as settings_module
from app.github.issue_polisher import IssuePolisher, PolishedIssue


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

    with patch.object(polisher, "_call_gemini", new_callable=AsyncMock) as mock_gemini:
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
    with patch("app.config.settings.settings.ANTHROPIC_API_KEY", None), \
         patch("app.config.settings.settings.OPENAI_API_KEY", None), \
         patch("app.config.settings.settings.GOOGLE_GENERATIVEAI_API_KEY", None):
        with pytest.raises(RuntimeError, match="No AI API key configured"):
            await polisher.polish("owner/repo", "bug", "crash")
