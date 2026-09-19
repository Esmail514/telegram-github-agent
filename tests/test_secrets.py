"""
Tests for the secret scanner.
"""
import pytest

from app.utils.secrets import SecretScanner


@pytest.fixture
def scanner():
    return SecretScanner()


def test_no_secrets_in_clean_text(scanner: SecretScanner):
    result = scanner.scan_diff("+ hello world\n+ print('OK')")
    assert not result.has_secrets


def test_detects_github_pat(scanner: SecretScanner):
    result = scanner.scan_diff(
        "+ TOKEN=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef1234"
    )
    assert result.has_secrets
    assert any(f.pattern_name == "github_pat" for f in result.findings)


def test_detects_openai_key(scanner: SecretScanner):
    result = scanner.scan_diff(
        "+ OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz12345678901234"
    )
    assert result.has_secrets
    assert any(f.pattern_name == "openai_key" for f in result.findings)


def test_detects_private_key_header(scanner: SecretScanner):
    result = scanner.scan_diff("+ -----BEGIN RSA PRIVATE KEY-----")
    assert result.has_secrets
    assert any(f.pattern_name == "private_key_header" for f in result.findings)


def test_detects_telegram_token(scanner: SecretScanner):
    result = scanner.scan_diff("+ BOT_TOKEN=1234567890:AABBCCDDEEFFaabbccddeeffgghhiijjkkll")
    assert result.has_secrets
    assert any(f.pattern_name == "telegram_bot_token" for f in result.findings)


def test_scan_diff_only_checks_added_lines(scanner: SecretScanner):
    # Removed line containing a secret — should NOT be flagged
    result = scanner.scan_diff(
        "- TOKEN=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef1234\n"
        "+ TOKEN=placeholder"
    )
    assert not result.has_secrets


def test_str_output_with_findings(scanner: SecretScanner):
    result = scanner.scan_diff(
        "+ OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz12345678901234"
    )
    output = str(result)
    assert "secret" in output.lower()
    assert "REDACTED" in output


def test_str_output_no_findings(scanner: SecretScanner):
    result = scanner.scan_diff("+ x = 42")
    assert "No secrets detected" in str(result)
