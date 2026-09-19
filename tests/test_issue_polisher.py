from unittest.mock import AsyncMock, patch

import pytest

from app.github.issue_polisher import IssuePolisher, PolishedIssue


@pytest.mark.asyncio
async def test_polish_gemini_success():
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
async def test_polish_fallback_on_anthropic_error():
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
